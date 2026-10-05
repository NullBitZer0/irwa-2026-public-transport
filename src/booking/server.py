"""
Booking Agent FastAPI Server
Member 3 — Execution Engine & Security Lead

Full flow implemented:
  1. Security Gateway ingress  — sanitize_user_input() guardrail check
  2. Inventory check           — MockTransitGateway
  3. Seat hold                 — BookingStateMachine (SEAT_HELD, 10 min)
  4. HITL gate                 — enforced by the orchestrator before it
                                  calls /mcp/await_payment; the booking
                                  agent trusts user_confirmed=True only
                                  because the orchestrator is the sole
                                  caller on an internal network boundary
  5. Move to AWAITING_PAYMENT  — mark_awaiting_payment()
  6. Mock payment charge       — MockPaymentGateway
  7. Confirm booking           — PII detokenized only here, at the very
                                  last step, right before the mock
                                  partner API call
  8. /mcp/book_ticket          — single-call convenience wrapper that
                                  chains steps 3-7, matching what
                                  Member 1's agent_connectors.py expects

Start:
    uvicorn src.booking.server:app --port 8002 --reload
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from src.booking.mock_gateway import MockTransitGateway
from src.booking.payment_gateway import MockPaymentGateway
from src.booking.state_machine import BookingStateMachine
from src.booking.store import BookingStore
from src.security.audit_log import log_security_event
from src.security.guardrails import sanitize_user_input
from src.security.pii_masker import PIITokenizer

app = FastAPI(
    title="Booking Engine Agent",
    description="Agent 3: Reservation state machine, security gateway, mock gateway & payments.",
    version="0.2.0",
)

_state_machine = BookingStateMachine()
_transit_gateway = MockTransitGateway()
_payment_gateway = MockPaymentGateway()
_tokenizer = PIITokenizer()


# ── Request Schemas ─────────────────────────────────────────────────────────


class HoldRequest(BaseModel):
    route_id: str
    provider: str = "SLR"
    passenger_token: str
    seat_count: int = 1
    fare_lkr: float = 0.0
    raw_note: str = ""  # any free-text passed through from the user, e.g. special requests


class AwaitPaymentRequest(BaseModel):
    transaction_id: str
    user_confirmed: bool = False  # HITL gate — must be explicitly True


class ChargeRequest(BaseModel):
    transaction_id: str
    card_last4: str


class ConfirmRequest(BaseModel):
    transaction_id: str
    checkout_token: str
    provider: str = "SLR"


# ── Purchase ledger ──────────────────────────────────────────────────────────
#
# Backs the UI's purchase history. Persisted in SQLite so a container restart
# does not erase a traveller's tickets; the service sets BOOKING_DB_PATH to a
# mounted volume. Metadata only: no PII and no card number, just the last four
# digits a receipt would show anyway.
_store = BookingStore.from_env()


def record_purchase(ticket: dict, receipt: dict, card_last4: str) -> dict:
    """Appends one settled ticket to the durable ledger and returns the entry."""
    entry = {
        "booking_reference": ticket.get("booking_reference"),
        "transaction_id": ticket.get("transaction_id"),
        "route_id": ticket.get("route_id"),
        "provider": ticket.get("provider"),
        "seat_count": ticket.get("seat_count"),
        "fare_lkr": ticket.get("fare_lkr"),
        "passenger_token": ticket.get("passenger_token"),
        "purchased_at": datetime.now(tz=timezone.utc).isoformat(timespec="seconds"),
        "receipt_id": receipt.get("receipt_id"),
        "amount_paid_lkr": receipt.get("amount_lkr"),
        "card_last4": card_last4,
    }
    return _store.insert_purchase(entry)


class BookTicketRequest(BaseModel):
    """Single-call convenience endpoint matching agent_connectors.py's expectation."""
    route_id: str
    provider: str = "SLR"
    passenger_token: str
    seat_count: int = 1
    fare_lkr: float = 0.0
    user_confirmed: bool = False  # HITL gate — orchestrator sets this after user approval
    card_last4: str = "0000"      # mock card digits for the demo payment step


class SettleRequest(BaseModel):
    """
    Payment settlement request.

    `card_last4` is the ONLY card data this system ever accepts. A real
    integration would have the payment provider's hosted field collect the full
    card number on their side and hand back an opaque checkout token, so no PAN
    or CVV ever reaches our agents.
    """

    transaction_id: str
    card_last4: str
    provider: str = "SLR"


# ── Endpoints ────────────────────────────────────────────────────────────────


@app.post("/mcp/hold_seat")
async def hold_seat(req: HoldRequest) -> dict:
    """Step 1-3: Security check, inventory check, then a 10-minute seat hold."""
    try:
        sanitize_user_input(req.raw_note or req.route_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # Defence in depth: the Orchestrator already ran the gateway, but a caller
    # hitting this endpoint directly must not get PII stored in the clear either.
    # Redacting also populates this process's vault, so confirm_booking can
    # detokenize at the final gateway step. The stub booking engine does not
    # persist the note itself, so the tokenised form is not stored anywhere.
    _tokenizer.redact(req.raw_note or "")

    inventory = _transit_gateway.check_seat_inventory(req.route_id)
    if inventory["status"] == "SOLD_OUT":
        raise HTTPException(status_code=409, detail="No seats available for this route.")

    txn_id = f"TXN-{uuid.uuid4().hex[:8].upper()}"
    txn = _state_machine.initiate_hold(
        txn_id=txn_id,
        route_id=req.route_id,
        provider=req.provider,
        passenger_token=req.passenger_token,
        seats=req.seat_count,
        fare=req.fare_lkr,
    )

    return {
        "status": "SUCCESS",
        "data": {
            "transaction": txn.model_dump(mode="json"),
        },
        "message": f"Seat held for 10 minutes. Transaction: {txn_id}",
    }


@app.post("/mcp/await_payment")
async def await_payment(req: AwaitPaymentRequest) -> dict:
    """Step 4-5: HITL gate, then SEAT_HELD -> AWAITING_PAYMENT."""
    if not req.user_confirmed:
        log_security_event(
            "BOOKING_BLOCKED_NO_HITL",
            f"transaction_id={req.transaction_id}",
        )
        raise HTTPException(
            status_code=403,
            detail="Human-in-the-loop confirmation required before payment.",
        )
    try:
        txn = _state_machine.mark_awaiting_payment(req.transaction_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {"status": "SUCCESS", "transaction": txn.model_dump(mode="json")}


@app.post("/mcp/charge")
async def charge(req: ChargeRequest) -> dict:
    """Step 6: Mock payment charge. Only ever sees the last 4 card digits."""
    txn = _state_machine.get(req.transaction_id)
    if not txn:
        raise HTTPException(status_code=404, detail="Transaction not found.")

    checkout_token = _payment_gateway.tokenize_card(req.card_last4)
    receipt = _payment_gateway.charge(checkout_token, txn.fare_lkr)

    return {"status": "SUCCESS", "receipt": receipt}


@app.post("/mcp/confirm_booking")
async def confirm_booking(req: ConfirmRequest) -> dict:
    """Step 7: AWAITING_PAYMENT -> CONFIRMED. PII is detokenized only here."""
    txn = _state_machine.get(req.transaction_id)
    if not txn:
        raise HTTPException(status_code=404, detail="Transaction not found.")

    real_nic = _tokenizer.detokenize(txn.passenger_token)

    ref_no = _transit_gateway.execute_partner_booking(
        provider=req.provider,
        route_id=txn.route_id,
        real_nic=real_nic,
        seats=txn.seat_count,
    )

    try:
        confirmed = _state_machine.confirm_payment(req.transaction_id, ref_no)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {
        "status": "SUCCESS",
        "booking_reference": ref_no,
        "details": confirmed.model_dump(mode="json"),
    }


@app.post("/mcp/book_ticket")
async def book_ticket(req: BookTicketRequest) -> dict:
    """
    Convenience wrapper for the Orchestrator: chains hold -> HITL gate ->
    charge -> confirm into one call, matching the AgentDispatchBridge
    contract in Member 1's agent_connectors.py.
    """
    hold_result = await hold_seat(
        HoldRequest(
            route_id=req.route_id,
            provider=req.provider,
            passenger_token=req.passenger_token,
            seat_count=req.seat_count,
            fare_lkr=req.fare_lkr,
        )
    )
    txn_id = hold_result["data"]["transaction"]["transaction_id"]

    await await_payment(
        AwaitPaymentRequest(transaction_id=txn_id, user_confirmed=req.user_confirmed)
    )

    await charge(ChargeRequest(transaction_id=txn_id, card_last4=req.card_last4))

    confirm_result = await confirm_booking(
        ConfirmRequest(transaction_id=txn_id, checkout_token="", provider=req.provider)
    )

    return confirm_result


@app.post("/mcp/begin_booking")
async def begin_booking(req: BookTicketRequest) -> dict:
    """
    Steps 2-4: hold the seat and clear the HITL gate, then STOP.

    Used by the UI payment flow: the seat is held and the traveller is asked to
    pay, so the transaction id and amount must be returned to the client. Nothing
    is charged here — settlement happens in /mcp/settle_booking once the
    traveller has actually paid.
    """
    hold_result = await hold_seat(
        HoldRequest(
            route_id=req.route_id,
            provider=req.provider,
            passenger_token=req.passenger_token,
            seat_count=req.seat_count,
            fare_lkr=req.fare_lkr,
        )
    )
    txn_id = hold_result["data"]["transaction"]["transaction_id"]

    await await_payment(
        AwaitPaymentRequest(transaction_id=txn_id, user_confirmed=req.user_confirmed)
    )

    fare = _state_machine.get(txn_id).fare_lkr
    if fare <= 0:
        # Refusing here is the last line of defence: a zero fare must never be
        # treated as a free ticket, whatever the caller sent.
        raise HTTPException(
            status_code=400,
            detail=(
                "Refusing to hold a seat with no fare. The price must come from the "
                "Planning Agent's fare matrix."
            ),
        )

    return {
        "status": "SUCCESS",
        "data": {"transaction": _state_machine.get(txn_id).model_dump(mode="json")},
        "message": f"Seat held for 10 minutes. Transaction: {txn_id}",
    }


@app.post("/mcp/settle_booking")
async def settle_booking(req: SettleRequest) -> dict:
    """
    Steps 6-7: charge the tokenized payment, then confirm and issue the ticket.

    Only the last four card digits are accepted. A full card number must never be
    sent to this agent — the payment provider's hosted field collects it and hands
    back an opaque token, which is the whole point of the tokenized design.
    """
    if not (req.card_last4.isdigit() and len(req.card_last4) == 4):
        raise HTTPException(
            status_code=400,
            detail="card_last4 must be exactly 4 digits; never send a full card number.",
        )

    txn = _state_machine.get(req.transaction_id)
    if not txn:
        raise HTTPException(status_code=404, detail="Transaction not found.")

    checkout_token = _payment_gateway.tokenize_card(req.card_last4)
    receipt = _payment_gateway.charge(checkout_token, txn.fare_lkr)

    confirm_result = await confirm_booking(
        ConfirmRequest(
            transaction_id=req.transaction_id,
            checkout_token=checkout_token,
            provider=req.provider,
        )
    )

    return {
        "status": "SUCCESS",
        "data": {
            "booking_reference": confirm_result["booking_reference"],
            "receipt": receipt,
            "ticket": confirm_result["details"],
            "purchase": record_purchase(confirm_result["details"], receipt, req.card_last4),
        },
        "message": f"Payment settled. Booking reference: {confirm_result['booking_reference']}",
    }


@app.get("/mcp/purchases")
def get_purchases() -> dict:
    """
    Completed ticket purchases, newest first, for the UI's purchase history.

    Metadata only: the passenger token is stored as the opaque TOKEN_ value, and
    only the card's last four digits are kept, so this ledger can be displayed
    without exposing PII or card data.
    """
    return {"status": "SUCCESS", "data": {"purchases": _store.list_purchases()}}


@app.get("/mcp/inventory/{route_id}")
def check_inventory(route_id: str) -> dict:
    """Returns seat availability for a route."""
    return _transit_gateway.check_seat_inventory(route_id)


@app.post("/mcp/cancel/{transaction_id}")
def cancel_booking(transaction_id: str) -> dict:
    try:
        txn = _state_machine.cancel(transaction_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "SUCCESS", "transaction": txn.model_dump(mode="json")}


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "agent": "booking", "version": "0.2.0"}
