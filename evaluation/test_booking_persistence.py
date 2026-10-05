"""
Booking durability tests (SQLite)

Member 3 — Execution Engine & Security Lead

The booking state machine and the purchase ledger used to be plain in-memory
structures, so restarting the container silently discarded every held seat,
every confirmed ticket and the traveller's purchase history. These tests pin the
behaviour that replaced that: state written through SQLite is still there after
the process is gone.

Run:
    pytest evaluation/test_booking_persistence.py -v
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.booking.state_machine import (  # noqa: E402
    BookingState,
    BookingStateMachine,
)
from src.booking.store import BookingStore  # noqa: E402


@pytest.fixture()
def db_path(tmp_path) -> str:
    return str(tmp_path / "booking.db")


def _confirmed_machine(store: BookingStore) -> tuple[BookingStateMachine, str]:
    sm = BookingStateMachine(store=store)
    sm.initiate_hold("TXN-1", "TRAIN-1007", "SLR", "TOKEN_nic_abc", 2, 900.0)
    sm.mark_awaiting_payment("TXN-1")
    sm.confirm_payment("TXN-1", "SLR-2026-ABC123")
    return sm, "TXN-1"


# ── Restart survival ─────────────────────────────────────────────────────────

def test_confirmed_booking_survives_restart(db_path: str) -> None:
    """The headline gap: a restart used to lose confirmed tickets."""
    _confirmed_machine(BookingStore(db_path))

    # A brand-new state machine over the same file == process restart.
    reloaded = BookingStateMachine(store=BookingStore(db_path))
    txn = reloaded.get("TXN-1")

    assert txn is not None, "confirmed booking vanished across restart"
    assert txn.state == BookingState.CONFIRMED
    assert txn.booking_reference == "SLR-2026-ABC123"
    assert txn.seat_count == 2
    assert txn.fare_lkr == 900.0
    assert txn.route_id == "TRAIN-1007"


def test_reloaded_timestamps_are_datetimes(db_path: str) -> None:
    """SQLite returns text; the API contract expects real datetimes."""
    _confirmed_machine(BookingStore(db_path))
    txn = BookingStateMachine(store=BookingStore(db_path)).get("TXN-1")

    assert isinstance(txn.created_at, datetime)
    assert isinstance(txn.hold_expires_at, datetime)
    assert txn.created_at.tzinfo is not None


def test_unpaid_hold_survives_restart_and_stays_unpaid(db_path: str) -> None:
    """
    A held seat must come back as a hold, never silently as confirmed.

    Rehydrating a transaction in a more advanced state would let a restart
    upgrade an unpaid hold into a paid ticket.
    """
    sm = BookingStateMachine(store=BookingStore(db_path))
    sm.initiate_hold("TXN-2", "TRAIN-1002", "SLR", "TOKEN_x", 1, 500.0)

    txn = BookingStateMachine(store=BookingStore(db_path)).get("TXN-2")

    assert txn.state == BookingState.SEAT_HELD
    assert txn.booking_reference is None


def test_purchase_ledger_survives_restart(db_path: str) -> None:
    store = BookingStore(db_path)
    store.insert_purchase(
        {
            "route_id": "TRAIN-1007",
            "fare_lkr": 900.0,
            "amount_paid_lkr": 900.0,
            "card_last4": "4242",
            "passenger_token": "TOKEN_nic_abc",
            "booking_reference": "SLR-2026-ABC123",
        }
    )

    reopened = BookingStore(db_path).list_purchases()

    assert len(reopened) == 1
    assert reopened[0]["booking_reference"] == "SLR-2026-ABC123"
    assert reopened[0]["card_last4"] == "4242"


def test_purchase_history_is_newest_first(db_path: str) -> None:
    """The sidebar renders in array order, so the newest ticket must be first."""
    store = BookingStore(db_path)
    for i in range(3):
        store.insert_purchase({"route_id": f"R{i}", "booking_reference": f"REF-{i}"})

    refs = [p["booking_reference"] for p in BookingStore(db_path).list_purchases()]

    assert refs == ["REF-2", "REF-1", "REF-0"]


# ── State-machine integrity after rehydration ────────────────────────────────

def test_rehydrated_transaction_cannot_skip_payment(db_path: str) -> None:
    """Rehydration must not weaken the enforced transition rules."""
    sm = BookingStateMachine(store=BookingStore(db_path))
    sm.initiate_hold("TXN-3", "TRAIN-1003", "SLR", "TOKEN_x", 1, 500.0)

    restarted = BookingStateMachine(store=BookingStore(db_path))
    with pytest.raises(ValueError, match="AWAITING_PAYMENT"):
        restarted.confirm_payment("TXN-3", "SLR-2026-SHORT")  # bypasses the payment gate


def test_cancelled_state_survives_restart(db_path: str) -> None:
    sm = BookingStateMachine(store=BookingStore(db_path))
    sm.initiate_hold("TXN-4", "TRAIN-1004", "SLR", "TOKEN_x", 1, 500.0)
    sm.cancel("TXN-4")

    assert BookingStateMachine(store=BookingStore(db_path)).get("TXN-4").state == (
        BookingState.CANCELLED
    )


def test_expired_hold_is_persisted_as_expired(db_path: str) -> None:
    """Expiry must survive a restart, or a stale hold could be paid for later."""
    store = BookingStore(db_path)
    sm = BookingStateMachine(store=store)
    sm.initiate_hold("TXN-5", "TRAIN-1005", "SLR", "TOKEN_x", 1, 500.0)

    # Backdate the hold past its 10-minute window.
    store.upsert_booking(
        {
            **sm.get("TXN-5").model_dump(mode="json"),
            "hold_expires_at": (
                datetime.now(tz=timezone.utc) - timedelta(minutes=1)
            ).isoformat(),
        }
    )

    restarted = BookingStateMachine(store=BookingStore(db_path))
    with pytest.raises(ValueError, match="expired"):
        restarted.mark_awaiting_payment("TXN-5")

    assert BookingStore(db_path).get_booking("TXN-5")["state"] == "EXPIRED"


# ── Oversell protection ──────────────────────────────────────────────────────

def test_active_holds_counted_across_restart(db_path: str) -> None:
    sm = BookingStateMachine(store=BookingStore(db_path))
    sm.initiate_hold("TXN-6", "TRAIN-1007", "SLR", "TOKEN_x", 3, 900.0)

    assert BookingStore(db_path).active_hold_seats("TRAIN-1007") == 3


def test_expired_holds_do_not_count_against_inventory(db_path: str) -> None:
    """An expired hold holds nothing, so it must not block a later booking."""
    store = BookingStore(db_path)
    sm = BookingStateMachine(store=store)
    sm.initiate_hold("TXN-7", "TRAIN-1007", "SLR", "TOKEN_x", 3, 900.0)
    store.upsert_booking(
        {
            **sm.get("TXN-7").model_dump(mode="json"),
            "hold_expires_at": (
                datetime.now(tz=timezone.utc) - timedelta(minutes=1)
            ).isoformat(),
        }
    )

    assert BookingStore(db_path).active_hold_seats("TRAIN-1007") == 0


def test_confirmed_seats_are_released_from_holds(db_path: str) -> None:
    """Once confirmed the transaction is no longer a *hold* on the seat."""
    _confirmed_machine(BookingStore(db_path))

    assert BookingStore(db_path).active_hold_seats("TRAIN-1007") == 0


# ── Store behaviour ──────────────────────────────────────────────────────────

def test_default_store_is_in_memory_and_writes_no_files(tmp_path, monkeypatch) -> None:
    """
    Tests must not leave database files in the repo.

    With no BOOKING_DB_PATH the store is in-memory; durability is opted into by
    the service setting the variable.
    """
    monkeypatch.delenv("BOOKING_DB_PATH", raising=False)
    store = BookingStore.from_env()

    assert store.is_persistent is False
    store.insert_purchase({"route_id": "TRAIN-1001"})
    assert len(store.list_purchases()) == 1
    assert not list(tmp_path.iterdir())


def test_store_creates_parent_directory(tmp_path) -> None:
    """The Docker volume path may not exist yet on first boot."""
    nested = tmp_path / "a" / "b" / "booking.db"
    BookingStore(str(nested)).insert_purchase({"route_id": "TRAIN-1001"})

    assert nested.exists()


def test_upsert_updates_rather_than_duplicating(db_path: str) -> None:
    store = BookingStore(db_path)
    sm = BookingStateMachine(store=store)
    sm.initiate_hold("TXN-8", "TRAIN-1008", "SLR", "TOKEN_x", 1, 500.0)
    sm.mark_awaiting_payment("TXN-8")
    sm.confirm_payment("TXN-8", "SLR-2026-REF8")

    rows = BookingStore(db_path).load_bookings()

    assert len(rows) == 1
    assert rows[0]["state"] == "CONFIRMED"


def test_corrupt_row_does_not_block_boot(db_path: str) -> None:
    """
    A bad row must not take the whole service down at startup.

    The booking service should start and serve new bookings even if one stored
    transaction is unreadable.
    """
    store = BookingStore(db_path)
    store.insert_purchase({"route_id": "TRAIN-1001"})
    sm = BookingStateMachine(store=store)
    sm.initiate_hold("TXN-9", "TRAIN-1009", "SLR", "TOKEN_x", 1, 500.0)

    # Write a row that cannot validate as a BookingTransaction: the column
    # constraints all pass, but the state is not one this version knows. This is
    # the realistic corruption case — a row written by another build.
    import sqlite3

    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO bookings (transaction_id, route_id, provider, passenger_token,"
        " seat_count, fare_lkr, state, hold_expires_at, booking_reference, created_at)"
        " VALUES ('TXN-BAD', 'TRAIN-0000', 'SLR', 'TOKEN_x', 1, 100.0,"
        " 'STATE_FROM_A_NEWER_BUILD', NULL, NULL, ?)",
        (datetime.now(tz=timezone.utc).isoformat(),),
    )
    conn.commit()
    conn.close()

    recovered = BookingStateMachine(store=BookingStore(db_path))

    assert recovered.get("TXN-BAD") is None
    assert recovered.get("TXN-9") is not None  # good rows still loaded
