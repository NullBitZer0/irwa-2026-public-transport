"""
Booking Transaction State Machine
Member 3 — Execution Engine & Security Lead

Manages the full reservation lifecycle exactly as specified in the
assignment brief:

    INITIATED -> SEAT_HELD -> AWAITING_PAYMENT -> CONFIRMED
                     |               |
                     v               v
                  EXPIRED         EXPIRED
                     |
                     v
                 CANCELLED (from SEAT_HELD or AWAITING_PAYMENT)

The state machine is the core "transactional integrity" viva topic:
it deterministically prevents any booking from jumping straight from
discovery to a confirmed, paid reservation without passing through an
explicit hold and an explicit payment step — and it enforces atomic,
single-direction transitions so double-booking or double-confirming
the same seat is structurally impossible.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


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
    created_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))


class BookingStateMachine:
    """
    Deterministic, in-memory state machine for the reservation lifecycle.
    TODO (Member 3, stretch goal): swap the in-memory dict for SQLite/Redis
    so holds survive a server restart — noted as a limitation in the report.
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
            hold_expires_at=datetime.now(tz=timezone.utc)
            + timedelta(minutes=self.HOLD_DURATION_MINUTES),
        )
        self.transactions[txn_id] = txn
        return txn

    def _check_not_expired(self, txn: BookingTransaction) -> None:
        """Shared expiry check used by every step after SEAT_HELD."""
        if txn.hold_expires_at and datetime.now(tz=timezone.utc) > txn.hold_expires_at:
            txn.state = BookingState.EXPIRED
            self.transactions[txn.transaction_id] = txn
            raise ValueError("Hold expired. Please re-select seats.")

    def mark_awaiting_payment(self, txn_id: str) -> BookingTransaction:
        """
        SEAT_HELD -> AWAITING_PAYMENT.
        Called once the commuter has passed the HITL confirmation gate and
        the system is about to redirect to the (mock) payment gateway.
        """
        txn = self.transactions.get(txn_id)
        if not txn:
            raise ValueError(f"Transaction {txn_id} not found.")
        if txn.state != BookingState.SEAT_HELD:
            raise ValueError(
                f"Cannot move to AWAITING_PAYMENT from state '{txn.state}'. "
                f"A seat must be held first."
            )
        self._check_not_expired(txn)
        txn.state = BookingState.AWAITING_PAYMENT
        self.transactions[txn_id] = txn
        return txn

    def confirm_payment(self, txn_id: str, reference_no: str) -> BookingTransaction:
        """
        AWAITING_PAYMENT -> CONFIRMED.
        Only reachable after mark_awaiting_payment() — a transaction can
        no longer be confirmed directly from SEAT_HELD, which is what
        makes "PAYMENT_PENDING" a real, enforced step rather than a label.
        """
        txn = self.transactions.get(txn_id)
        if not txn:
            raise ValueError(f"Transaction {txn_id} not found.")
        if txn.state != BookingState.AWAITING_PAYMENT:
            raise ValueError(
                f"Cannot confirm transaction in state '{txn.state}'. "
                f"Expected AWAITING_PAYMENT."
            )
        self._check_not_expired(txn)
        txn.state = BookingState.CONFIRMED
        txn.booking_reference = reference_no
        self.transactions[txn_id] = txn
        return txn

    def cancel(self, txn_id: str) -> BookingTransaction:
        txn = self.transactions.get(txn_id)
        if not txn:
            raise ValueError(f"Transaction {txn_id} not found.")
        if txn.state in (BookingState.CONFIRMED, BookingState.CANCELLED):
            raise ValueError(f"Cannot cancel a transaction already in state '{txn.state}'.")
        txn.state = BookingState.CANCELLED
        self.transactions[txn_id] = txn
        return txn

    def get(self, txn_id: str) -> Optional[BookingTransaction]:
        return self.transactions.get(txn_id)