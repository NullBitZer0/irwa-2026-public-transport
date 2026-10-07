"""
Booking Agent FastAPI Server
Member 3 — Execution Engine & Security Lead

Full flow implemented:
  1. Security Gateway ingress  — sanitize_user_input() guardrail check
  2. Inventory check           — MockTransitGateway
  3. Seat hold                 — BookingStateMachine (SEAT_HELD, 10 min)
  4. HITL gate                 — R-09: enforced here, not by the caller.
                                  /mcp/hitl_challenge issues a signed token
                                  bound to session/route/fare; await_payment
                                  verifies it. A client-supplied boolean would
                                  be self-asserted approval.
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
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from src.booking.hitl_token import (
    TOKEN_TTL_SECONDS,
    HitlTokenError,
    issue_hitl_token,
    verify_hitl_token,
)
from src.booking.mock_gateway import MockTransitGateway
from src.booking.payment_gateway import MockPaymentGateway
from src.booking.providers import contact_for
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


class HitlChallengeRequest(BaseModel):
    """What the traveller is being asked to approve."""

    session_id: str
    route_id: str
    fare_lkr: float
    seat_count: int = 1
    provider: str = "SLR"


class HoldRequest(BaseModel):
    route_id: str
    provider: str = "SLR"
    session_id: str = "anonymous"
    # Set when this seat is one leg of a larger journey, so the legs are resumed
    # and paid for together.
    booking_group_id: str = ""
    passenger_token: str
    seat_count: int = 1
    fare_lkr: float = 0.0
    raw_note: str = ""  # any free-text passed through from the user, e.g. special requests


class AwaitPaymentRequest(BaseModel):
    transaction_id: str
    # R-09: approval is a signed capability the client returns, not a boolean it
    # asserts. `hitl_token` is verified against the transaction it is clearing.
    hitl_token: str = ""


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
        # What the gateway was actually charged, not the per-seat fare.
        "amount_paid_lkr": receipt.get("amount_lkr"),
        "card_last4": card_last4,
    }
    stored = _store.insert_purchase(entry)
    # Return the same shape the history endpoint serves, so a caller that uses
    # this response directly (the UI refreshes right after paying) does not have
    # to make a second request to render the ticket's contact details.
    return {**stored, "provider_contact": contact_for(stored.get("provider"), stored.get("route_id"))}


class BookTicketRequest(BaseModel):
    """Single-call convenience endpoint matching agent_connectors.py's expectation."""
    route_id: str
    provider: str = "SLR"
    passenger_token: str
    seat_count: int = 1
    fare_lkr: float = 0.0
    # R-09: the signed confirmation the traveller's approval produced.
    hitl_token: str = ""
    session_id: str = "anonymous"
    # Set when this seat is one leg of a larger journey, so the legs are resumed
    # and paid for together.
    booking_group_id: str = ""
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


@app.post("/mcp/hitl_challenge")
async def hitl_challenge(req: HitlChallengeRequest) -> dict:
    """
    Issues a signed human-in-the-loop confirmation token (R-09).

    Called when a route is chosen and the traveller is about to be asked to
    approve. It proves nothing yet — it is the server's way of saying "here is
    what you are approving". The traveller's approval is the token coming back.
    """
    try:
        token = issue_hitl_token(
            session_id=req.session_id,
            route_id=req.route_id,
            fare_lkr=req.fare_lkr,
            seat_count=req.seat_count,
            provider=req.provider,
        )
    except HitlTokenError as exc:
        # Fail closed: without a signing key there is no gate to clear.
        log_security_event("HITL_TOKEN_UNAVAILABLE", f"reason={exc}")
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return {
        "status": "SUCCESS",
        "data": {"hitl_token": token, "expires_in_seconds": TOKEN_TTL_SECONDS},
        "message": "Confirmation token issued; return it once the traveller approves.",
    }


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

    # A connection is not a route. The planner builds ids like
    # `CONN-SLTB-1-COLO-KAND-SLTB-2-KAND-COLO-0510` to represent *two* services
    # and one change between them, and this endpoint would cheerfully hold a seat
    # against that id and issue a ticket for a service that does not run.
    # Connections are expanded into their legs before they get here.
    if str(req.route_id).upper().startswith("CONN-"):
        raise HTTPException(
            status_code=400,
            detail=(
                "A connection is two services and one change, not a route that can "
                "be held. Book the legs individually."
            ),
        )

    txn_id = f"TXN-{uuid.uuid4().hex[:8].upper()}"
    txn = _state_machine.initiate_hold(
        txn_id=txn_id,
        route_id=req.route_id,
        provider=req.provider,
        passenger_token=req.passenger_token,
        seats=req.seat_count,
        fare=req.fare_lkr,
        session_id=req.session_id,
        booking_group_id=req.booking_group_id,
    )

    return {
        "status": "SUCCESS",
        "data": {
            "transaction": txn.model_dump(mode="json"),
        },
        "message": f"Seat held for 10 minutes. Transaction: {txn_id}",
    }


def _amount_due(txn: Any) -> float:
    """
    What this transaction is owed.

    Falls back to the per-seat fare for a row written before `amount_lkr` existed,
    rather than to zero: a fallback of zero would charge nothing and look like a
    free ticket.
    """
    amount = getattr(txn, "amount_lkr", 0) or 0
    if amount > 0:
        return float(amount)
    return float(getattr(txn, "fare_lkr", 0) or 0) * max(int(getattr(txn, "seat_count", 1) or 1), 1)


@app.post("/mcp/await_payment")
async def await_payment(req: AwaitPaymentRequest) -> dict:
    """
    Step 4-5: clear the HITL gate, then SEAT_HELD -> AWAITING_PAYMENT.

    R-09: the gate is cleared by a signed token, not a client boolean. The token
    is verified against *this* transaction's route and fare, so it cannot be
    minted by the client or replayed onto a different or pricier booking.
    """
    txn = _state_machine.get(req.transaction_id)
    if not txn:
        raise HTTPException(status_code=404, detail="Transaction not found.")

    try:
        verify_hitl_token(
            req.hitl_token,
            session_id=txn.session_id,
            route_id=txn.route_id,
            fare_lkr=txn.fare_lkr,
        )
    except HitlTokenError as exc:
        log_security_event(
            "BOOKING_BLOCKED_NO_HITL",
            f"transaction_id={req.transaction_id} reason={exc}",
        )
        raise HTTPException(
            status_code=403,
            detail=f"Human-in-the-loop confirmation required before payment: {exc}",
        ) from exc
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
    receipt = _payment_gateway.charge(checkout_token, _amount_due(txn))

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
            session_id=req.session_id,
        )
    )
    txn_id = hold_result["data"]["transaction"]["transaction_id"]

    await await_payment(
        AwaitPaymentRequest(transaction_id=txn_id, hitl_token=req.hitl_token)
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
            session_id=req.session_id,
            booking_group_id=req.booking_group_id,
        )
    )
    txn_id = hold_result["data"]["transaction"]["transaction_id"]

    await await_payment(
        AwaitPaymentRequest(transaction_id=txn_id, hitl_token=req.hitl_token)
    )

    fare = _amount_due(_state_machine.get(txn_id))
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
    receipt = _payment_gateway.charge(checkout_token, _amount_due(txn))

    confirm_result = await confirm_booking(
        ConfirmRequest(
            transaction_id=req.transaction_id,
            checkout_token=checkout_token,
            # The provider recorded when the seat was held, not the one this
            # request happens to carry: SettleRequest defaults to "SLR", so
            # trusting it stamped an SLR reference on an SLTB ticket. The
            # operator is a property of the booking, not of this call.
            provider=txn.provider,
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

    Each entry carries the operator's contact block so a traveller holding the
    ticket can reach the company that issued it.
    """
    purchases = [
        {**entry, "provider_contact": contact_for(entry.get("provider"), entry.get("route_id"))}
        for entry in _store.list_purchases()
    ]
    return {"status": "SUCCESS", "data": {"purchases": purchases}}


@app.get("/mcp/pending_holds")
def pending_holds() -> dict:
    """
    Bookings awaiting payment, so an interrupted checkout can be resumed.

    Without this a traveller who closes the payment portal, or reloads the page
    between holding a seat and paying, has no way back to it: the hold expires
    in 10 minutes and the seat is released. The UI lists these so the card
    itself is the way back into payment.

    Expired holds are excluded — they hold nothing, so offering to pay one would
    only produce a failure at checkout. Zero-fare holds are also excluded for
    the same reason: they can never be settled.
    """
    now = datetime.now(tz=timezone.utc)
    entries: list[dict] = []

    # Holds that are legs of one journey are offered as one item. A traveller
    # resuming an interrupted checkout wants their connection back, not one leg
    # of it, and paying for half of a journey strands them at the change.
    grouped: dict[str, list] = {}
    for txn in _state_machine.active_holds():
        if txn.hold_expires_at and now > txn.hold_expires_at:
            continue
        if txn.fare_lkr <= 0:
            continue
        grouped.setdefault(txn.booking_group_id or txn.transaction_id, []).append(txn)

    for legs in grouped.values():
        first = legs[0]
        entries.append(
            {
                "transaction_id": first.transaction_id,
                # Every leg, so payment settles the whole journey at once.
                "transaction_ids": [leg.transaction_id for leg in legs],
                "booking_group_id": first.booking_group_id or None,
                "is_connection": len(legs) > 1,
                "route_id": " → ".join(
                    f"{leg.route_id}" for leg in legs
                ),
                "provider": first.provider,
                "seat_count": first.seat_count,
                # The fare is per seat, so the amount due covers every seat on
                # every leg. Anything else would under-quote the connection.
                "fare_lkr": first.fare_lkr,
                "amount_due_lkr": sum(leg.amount_lkr for leg in legs),
                "state": legs[0].state.value,
                "hold_expires_at": min(
                    (leg.hold_expires_at for leg in legs if leg.hold_expires_at),
                    default=None,
                ).isoformat()
                if any(leg.hold_expires_at for leg in legs)
                else None,
                "provider_contact": contact_for(first.provider, first.route_id),
            }
        )

    entries.sort(key=lambda e: e["hold_expires_at"] or "", reverse=True)
    return {"status": "SUCCESS", "data": {"pending_holds": entries}}


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
