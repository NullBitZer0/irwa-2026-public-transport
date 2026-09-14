"""
Booking Agent FastAPI Server — Functional Stub
Member 3 — Execution Engine & Security Lead

Functional stub: state machine and mock gateway are implemented.
PII detokenization and payment flow are stubbed.

Start:
    uvicorn src.booking.server:app --port 8002 --reload
"""

from __future__ import annotations

import uuid

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from src.booking.mock_gateway import MockTransitGateway
from src.booking.state_machine import BookingStateMachine
from src.security.pii_masker import PIITokenizer

app = FastAPI(
    title="Booking Engine Agent",
    description="Agent 3: Reservation state machine, mock gateway, and PII detokenization.",
    version="0.1.0-stub",
)

_state_machine = BookingStateMachine()
_gateway = MockTransitGateway()
_tokenizer = PIITokenizer()


class HoldRequest(BaseModel):
    route_id: str
    provider: str = "SLR"
    passenger_token: str
    seat_count: int = 1
    fare_lkr: float = 0.0
    user_confirmed: bool = False


@app.post("/mcp/hold_seat")
async def hold_seat(req: HoldRequest) -> dict:
    """Initiates a 10-minute seat hold and returns transaction details."""
    txn_id = f"TXN-{uuid.uuid4().hex[:8].upper()}"

    # Check inventory
    inventory = _gateway.check_seat_inventory(req.route_id)
    if inventory["status"] == "SOLD_OUT":
        raise HTTPException(status_code=409, detail="No seats available for this route.")

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
        "transaction": txn.model_dump(mode="json"),
        "message": f"Seat held for 10 minutes. Transaction: {txn_id}",
    }


@app.post("/mcp/confirm_booking")
async def confirm_booking(payload: dict) -> dict:
    """Confirms payment and issues a booking reference."""
    txn_id = payload.get("transaction_id", "")
    passenger_token = payload.get("passenger_token", "")

    # Resolve PII token → real value only at the final mock API call
    real_nic = _tokenizer.detokenize(passenger_token)

    ref_no = _gateway.execute_partner_booking(
        provider=payload.get("provider", "SLR"),
        route_id=payload.get("route_id", ""),
        real_nic=real_nic,
        seats=payload.get("seat_count", 1),
    )

    try:
        confirmed = _state_machine.confirm_payment(txn_id, ref_no)
        return {
            "status": "SUCCESS",
            "booking_reference": ref_no,
            "details": confirmed.model_dump(mode="json"),
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/mcp/inventory/{route_id}")
def check_inventory(route_id: str) -> dict:
    """Returns seat availability for a route."""
    return _gateway.check_seat_inventory(route_id)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "agent": "booking", "version": "0.1.0-stub"}

