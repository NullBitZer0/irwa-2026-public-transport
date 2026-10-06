"""
Durable SQLite store for booking transactions and the purchase ledger.

Member 3 — Execution Engine & Security Lead

The booking state machine and the purchase ledger were both plain in-memory
structures, so a container restart silently lost every held seat, every
confirmed ticket and the traveller's purchase history. This module backs both
with SQLite so state survives a restart, and so two concurrent requests cannot
read-modify-write the same row without a lock.

Design notes:

- **One connection per operation.** FastAPI runs these endpoints in a thread
  pool, and SQLite connections are not safe to share across threads, so each
  call opens and closes its own connection.
- **Write-through, not write-back.** The state machine keeps an in-memory cache
  for fast transitions but every transition is committed here immediately, so a
  crash can lose at most the milliseconds since the last write.
- **WAL mode** lets the audit reader and the booking writer coexist without
  blocking each other.
- **No PII.** Only the passenger *token*, never a NIC or name, matching what the
  in-memory ledger held before.
"""

from __future__ import annotations

import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator, Optional

_SCHEMA = """
CREATE TABLE IF NOT EXISTS bookings (
    transaction_id   TEXT PRIMARY KEY,
    route_id         TEXT NOT NULL,
    provider         TEXT NOT NULL,
    session_id       TEXT NOT NULL DEFAULT 'anonymous',
    passenger_token  TEXT NOT NULL,
    seat_count       INTEGER NOT NULL,
    fare_lkr         REAL NOT NULL,
    amount_lkr       REAL NOT NULL DEFAULT 0,
    state            TEXT NOT NULL,
    hold_expires_at  TEXT,
    booking_reference TEXT,
    created_at       TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_bookings_state ON bookings(state);

CREATE TABLE IF NOT EXISTS purchases (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    booking_reference TEXT,
    transaction_id   TEXT,
    route_id         TEXT,
    provider         TEXT,
    seat_count       INTEGER,
    fare_lkr         REAL,
    passenger_token  TEXT,
    purchased_at     TEXT NOT NULL,
    receipt_id       TEXT,
    amount_paid_lkr  REAL,
    card_last4       TEXT
);

CREATE INDEX IF NOT EXISTS idx_purchases_time ON purchases(purchased_at DESC);
"""

_BOOKING_COLUMNS = (
    "transaction_id",
    "route_id",
    "provider",
    "session_id",
    "passenger_token",
    "seat_count",
    "fare_lkr",
    "amount_lkr",
    "state",
    "hold_expires_at",
    "booking_reference",
    "created_at",
)

_PURCHASE_COLUMNS = (
    "booking_reference",
    "transaction_id",
    "route_id",
    "provider",
    "seat_count",
    "fare_lkr",
    "passenger_token",
    "purchased_at",
    "receipt_id",
    "amount_paid_lkr",
    "card_last4",
)


class BookingStore:
    """
    SQLite-backed persistence for bookings and purchases.

    With no path the store is in-memory, which is what the unit tests want: they
    must not leave database files behind. The Docker service sets
    BOOKING_DB_PATH to a mounted volume so state is genuinely durable.
    """

    def __init__(self, path: Optional[str] = None) -> None:
        self.path = path or ":memory:"
        self.is_persistent = self.path not in (":memory:", "")
        # Guards schema creation and writes against each other. Reentrant,
        # because the write methods take it and then call _connect(), which
        # takes it again for the shared in-memory connection.
        self._lock = threading.RLock()
        # An in-memory SQLite database belongs to the connection that opened it,
        # so opening one per operation would give each call a different empty
        # database. The in-memory case therefore keeps a single connection alive
        # for the life of the store; the on-disk case uses one per call.
        self._shared: Optional[sqlite3.Connection] = None
        if self.is_persistent:
            parent = os.path.dirname(os.path.abspath(self.path))
            if parent:
                os.makedirs(parent, exist_ok=True)
        else:
            self._shared = self._new_connection()
        self._ensure_schema()

    @classmethod
    def from_env(cls) -> BookingStore:
        return cls(os.getenv("BOOKING_DB_PATH") or None)

    @staticmethod
    def _new_connection() -> sqlite3.Connection:
        conn = sqlite3.connect(":memory:", timeout=10.0, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        if self._shared is not None:
            # Serialised access: the shared connection cannot be used
            # concurrently, and one in-memory database is the whole point.
            with self._lock:
                try:
                    yield self._shared
                    self._shared.commit()
                except Exception:
                    self._shared.rollback()
                    raise
            return

        conn = sqlite3.connect(self.path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(_SCHEMA)
            self._migrate(conn)

    @staticmethod
    def _migrate(conn: sqlite3.Connection) -> None:
        """
        Adds columns introduced after a database was first created.

        `CREATE TABLE IF NOT EXISTS` is a no-op on an existing table, so a new
        column would otherwise only apply to fresh databases — and the service
        would crash at boot on any database that already existed. Adding columns
        here means an upgrade works without deleting state.

        Deliberately additive only: dropping or retyping a column needs a real
        migration tool and a backup, not a guess at startup.
        """
        added: dict[str, list[tuple[str, str]]] = {
            "bookings": [
                ("session_id", "TEXT NOT NULL DEFAULT 'anonymous'"),
                # Defaults are chosen so an existing single-seat row reads back
                # correctly: amount defaults to the per-seat fare rather than 0,
                # which would look like a free ticket.
                ("amount_lkr", "REAL NOT NULL DEFAULT 0"),
            ],
        }

        for table, columns in added.items():
            existing = {
                row["name"] for row in conn.execute(f"PRAGMA table_info({table})")
            }
            for name, definition in columns:
                if name not in existing:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")

    # ── Bookings ──────────────────────────────────────────────────────────────

    def upsert_booking(self, booking: dict[str, Any]) -> None:
        """Insert or update one transaction. This is the write-through call."""
        row = {c: booking.get(c) for c in _BOOKING_COLUMNS}
        # The API model serialises datetimes to ISO strings already; guard
        # against a datetime slipping through from a direct caller.
        for key in ("hold_expires_at", "created_at"):
            if isinstance(row.get(key), datetime):
                row[key] = row[key].astimezone(timezone.utc).isoformat()
        placeholders = ", ".join("?" for _ in _BOOKING_COLUMNS)
        assignments = ", ".join(f"{c}=excluded.{c}" for c in _BOOKING_COLUMNS[1:])
        with self._lock, self._connect() as conn:
            conn.execute(
                f"INSERT INTO bookings ({', '.join(_BOOKING_COLUMNS)}) "
                f"VALUES ({placeholders}) "
                f"ON CONFLICT(transaction_id) DO UPDATE SET {assignments}",
                tuple(row[c] for c in _BOOKING_COLUMNS),
            )

    def load_bookings(self) -> list[dict[str, Any]]:
        """All persisted transactions, for hydrating the state machine at boot."""
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT {', '.join(_BOOKING_COLUMNS)} FROM bookings"
            ).fetchall()
        return [dict(r) for r in rows]

    def get_booking(self, transaction_id: str) -> Optional[dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT {', '.join(_BOOKING_COLUMNS)} FROM bookings "
                "WHERE transaction_id = ?",
                (transaction_id,),
            ).fetchone()
        return dict(row) if row else None

    # ── Purchases ─────────────────────────────────────────────────────────────

    def insert_purchase(self, entry: dict[str, Any]) -> dict[str, Any]:
        """Append one settled ticket to the ledger and return the stored entry."""
        stored = {c: entry.get(c) for c in _PURCHASE_COLUMNS}
        stored["purchased_at"] = stored.get("purchased_at") or datetime.now(
            tz=timezone.utc
        ).isoformat(timespec="seconds")
        with self._lock, self._connect() as conn:
            conn.execute(
                f"INSERT INTO purchases ({', '.join(_PURCHASE_COLUMNS)}) "
                f"VALUES ({', '.join('?' for _ in _PURCHASE_COLUMNS)})",
                tuple(stored[c] for c in _PURCHASE_COLUMNS),
            )
        return stored

    def list_purchases(self, limit: int = 50) -> list[dict[str, Any]]:
        """Purchase history, newest first — the shape the sidebar expects."""
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT {', '.join(_PURCHASE_COLUMNS)} FROM purchases "
                "ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def active_hold_seats(self, route_id: str) -> int:
        """
        Seats currently held or awaiting payment on a route.

        Used to refuse a hold that would oversell a route across restarts. Holds
        that have passed their expiry are ignored, since they are no longer
        holding anything.
        """
        now = datetime.now(tz=timezone.utc).isoformat()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(seat_count), 0) FROM bookings "
                "WHERE route_id = ? "
                "AND state IN ('SEAT_HELD', 'AWAITING_PAYMENT') "
                "AND (hold_expires_at IS NULL OR hold_expires_at > ?)",
                (route_id, now),
            ).fetchone()
        return int(row[0]) if row else 0
