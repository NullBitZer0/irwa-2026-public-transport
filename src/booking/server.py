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

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from src.booking.mock_gateway import MockTransitGateway
from src.booking.payment_gateway import MockPaymentGateway
from src.booking.state_machine import BookingStateMachine
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


class BookTicketRequest(BaseModel):
    """Single-call convenience endpoint matching agent_connectors.py's expectation."""
    route_id: str
    provider: str = "SLR"
    passenger_token: str
    seat_count: int = 1
    fare_lkr: float = 0.0
    user_confirmed: bool = False  # HITL gate — orchestrator sets this after user approval
    card_last4: str = "0000"      # mock card digits for the demo payment step


# ── Endpoints ────────────────────────────────────────────────────────────────


@app.post("/mcp/hold_seat")
async def hold_seat(req: HoldRequest) -> dict:
    """Step 1-3: Security check, inventory check, then a 10-minute seat hold."""
    try:
        sanitize_user_input(req.raw_note or req.route_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

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
    txn_id = hold_result["transaction"]["transaction_id"]

    await await_payment(
        AwaitPaymentRequest(transaction_id=txn_id, user_confirmed=req.user_confirmed)
    )

    await charge(ChargeRequest(transaction_id=txn_id, card_last4=req.card_last4))

    confirm_result = await confirm_booking(
        ConfirmRequest(transaction_id=txn_id, checkout_token="", provider=req.provider)
    )

    return confirm_result


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
