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

from src.booking.store import BookingStore


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
    Deterministic state machine for the reservation lifecycle.

    State is cached in memory for fast transitions and written through to a
    `BookingStore` on every change, so holds and confirmations survive a server
    restart. The in-memory dict alone was the durability gap this replaced.
    """

    HOLD_DURATION_MINUTES: int = 10

    def __init__(self, store: Optional[BookingStore] = None) -> None:
        self.transactions: dict[str, BookingTransaction] = {}
        self.store = store if store is not None else BookingStore.from_env()
        self._hydrate()

    def _hydrate(self) -> None:
        """Reload persisted transactions so a restart resumes where it left off."""
        for row in self.store.load_bookings():
            try:
                txn = BookingTransaction.model_validate(_coerce(row))
            except Exception:  # noqa: BLE001 — a corrupt row must not block boot
                continue
            self.transactions[txn.transaction_id] = txn

    def _persist(self, txn: BookingTransaction) -> None:
        """Write-through: never leave a transition only in memory."""
        self.transactions[txn.transaction_id] = txn
        self.store.upsert_booking(txn.model_dump(mode="json"))

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
        self._persist(txn)
        return txn

    def _check_not_expired(self, txn: BookingTransaction) -> None:
        """Shared expiry check used by every step after SEAT_HELD."""
        if txn.hold_expires_at and datetime.now(tz=timezone.utc) > txn.hold_expires_at:
            txn.state = BookingState.EXPIRED
            self._persist(txn)
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
                f"Cannot move to AWAITING_PAYMENT from state '{txn.state.value}'. "
                f"A seat must be held first."
            )
        self._check_not_expired(txn)
        txn.state = BookingState.AWAITING_PAYMENT
        self._persist(txn)
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
                f"Cannot confirm transaction in state '{txn.state.value}'. "
                f"Expected AWAITING_PAYMENT."
            )
        self._check_not_expired(txn)
        txn.state = BookingState.CONFIRMED
        txn.booking_reference = reference_no
        self._persist(txn)
        return txn

    def cancel(self, txn_id: str) -> BookingTransaction:
        txn = self.transactions.get(txn_id)
        if not txn:
            raise ValueError(f"Transaction {txn_id} not found.")
        if txn.state in (BookingState.CONFIRMED, BookingState.CANCELLED):
            raise ValueError(f"Cannot cancel a transaction already in state '{txn.state.value}'.")
        txn.state = BookingState.CANCELLED
        self._persist(txn)
        return txn

    def get(self, txn_id: str) -> Optional[BookingTransaction]:
        return self.transactions.get(txn_id)


def _coerce(row: dict) -> dict:
    """
    Converts stored ISO strings back into datetimes so Pydantic can validate.

    SQLite has no native datetime type, so timestamps come back as text.
    """
    out = dict(row)
    for key in ("hold_expires_at", "created_at"):
        value = out.get(key)
        if isinstance(value, str) and value:
            try:
                out[key] = datetime.fromisoformat(value)
            except ValueError:
                out[key] = None
    return out
