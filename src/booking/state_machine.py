"""
Booking Transaction State Machine
Member 3 — Execution Engine & Security Lead

Manages the full lifecycle: INITIATED → SEAT_HELD → AWAITING_PAYMENT → CONFIRMED.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel


class BookingState(str, Enum):
    INITIATED = "INITIATED"
    SEAT_HELD = "SEAT_HELD"
    AWAITING_PAYMENT = "AWAITING_PAYMENT"
    CONFIRMED = "CONFIRMED"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"


class BookingTransaction(BaseModel):
    transaction_id: str
    route_id: str
    provider: str
    passenger_token: str
    seat_count: int
    fare_lkr: float
    state: BookingState = BookingState.INITIATED
    hold_expires_at: Optional[datetime] = None
    booking_reference: Optional[str] = None
    created_at: datetime = datetime.now(tz=timezone.utc)


class BookingStateMachine:
    """
    Deterministic state machine for seat reservation lifecycle.
    TODO (Member 3): Add persistence layer (SQLite / Redis) for production.
    """

    HOLD_DURATION_MINUTES: int = 10

    def __init__(self) -> None:
        self.transactions: dict[str, BookingTransaction] = {}

    def initiate_hold(
        self,
        txn_id: str,
        route_id: str,
        provider: str,
        passenger_token: str,
        seats: int,
        fare: float,
    ) -> BookingTransaction:
        txn = BookingTransaction(
            transaction_id=txn_id,
            route_id=route_id,
            provider=provider,
            passenger_token=passenger_token,
            seat_count=seats,
            fare_lkr=fare,
            state=BookingState.SEAT_HELD,
            hold_expires_at=datetime.now(tz=timezone.utc) + timedelta(minutes=self.HOLD_DURATION_MINUTES),
        )
        self.transactions[txn_id] = txn
        return txn

    def confirm_payment(self, txn_id: str, reference_no: str) -> BookingTransaction:
        txn = self.transactions.get(txn_id)
        if not txn:
            raise ValueError(f"Transaction {txn_id} not found.")
        if txn.state != BookingState.SEAT_HELD:
            raise ValueError(f"Cannot confirm transaction in state '{txn.state}'.")
        if datetime.now(tz=timezone.utc) > txn.hold_expires_at:  # type: ignore[operator]
            txn.state = BookingState.EXPIRED
            self.transactions[txn_id] = txn
            raise ValueError("Hold expired. Please re-select seats.")
        txn.state = BookingState.CONFIRMED
        txn.booking_reference = reference_no
        self.transactions[txn_id] = txn
        return txn

    def cancel(self, txn_id: str) -> BookingTransaction:
        txn = self.transactions.get(txn_id)
        if not txn:
            raise ValueError(f"Transaction {txn_id} not found.")
        txn.state = BookingState.CANCELLED
        self.transactions[txn_id] = txn
        return txn

    def get(self, txn_id: str) -> Optional[BookingTransaction]:
        return self.transactions.get(txn_id)

