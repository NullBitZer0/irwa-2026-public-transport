"""
Conversation persistence.

Member 1 — Orchestration & State Graph

A conversation is one trip-planning session: it starts when the traveller opens a
new chat, accumulates turns while they work out where and when they want to go,
and ends when they complete a payment. After that it becomes read-only history.

Two things follow from that, and both are enforced here rather than in the UI:

1. **A completed conversation stops accepting turns.** The UI disables the
   composer, but a disabled button is a suggestion, not a control — so the
   server refuses too.
2. **Ending a conversation drops its slot memory.** The origin, destination and
   time the traveller had gathered belong to that trip. Carrying them into the
   next one would make the new conversation answer questions about a journey the
   traveller is no longer asking about.

Transcripts persist in SQLite so history survives a restart, matching how the
booking store works. With no CONVERSATIONS_DB_PATH the store is in-memory, which
is what the tests use.
"""

from __future__ import annotations

import os
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator, Optional

# An archived conversation is kept indefinitely — it is a receipt of what the
# traveller was told — but the list endpoint bounds how many are returned.
DEFAULT_HISTORY_LIMIT = 50

_SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id           TEXT PRIMARY KEY,
    session_id   TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'active',
    title        TEXT NOT NULL DEFAULT '',
    booking_reference TEXT,
    created_at   TEXT NOT NULL,
    ended_at     TEXT
);

CREATE INDEX IF NOT EXISTS idx_conversations_status ON conversations(status, created_at DESC);

CREATE TABLE IF NOT EXISTS messages (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL,
    role            TEXT NOT NULL,
    text            TEXT NOT NULL,
    intent          TEXT,
    created_at      TEXT NOT NULL,
    FOREIGN KEY (conversation_id) REFERENCES conversations(id)
);

CREATE INDEX IF NOT EXISTS idx_messages_conversation ON messages(conversation_id, id);

-- Which conversation a pending transaction belongs to.
--
-- Payment settles on a separate endpoint from the chat turn that created it, so
-- nothing else knows the pairing. Recording it here lets /payment archive the
-- right conversation on success without the client asserting which one it was —
-- the client could claim any conversation, and a wrong claim would archive the
-- wrong history.
CREATE TABLE IF NOT EXISTS pending_transactions (
    transaction_id  TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    session_id      TEXT NOT NULL
);
"""


class ConversationArchived(Exception):
    """
    Raised when a turn is submitted to a conversation that has ended.

    A dedicated exception rather than a return value, because the caller must not
    be able to forget to check it: silently accepting the turn would reintroduce
    the exact bug this guards.
    """


class ConversationStore:
    """SQLite-backed conversation and message store."""

    def __init__(self, path: Optional[str] = None) -> None:
        self.path = path or ":memory:"
        self._lock = threading.RLock()
        self._shared: Optional[sqlite3.Connection] = None

        if self.path in (":memory:", ""):
            # An in-memory database belongs to the connection that opened it, so
            # a connection per operation would give each call a different empty
            # database.
            self._shared = self._new_connection()
        else:
            parent = os.path.dirname(os.path.abspath(self.path))
            if parent:
                os.makedirs(parent, exist_ok=True)

        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    @classmethod
    def from_env(cls) -> ConversationStore:
        return cls(os.getenv("CONVERSATIONS_DB_PATH") or None)

    @staticmethod
    def _new_connection() -> sqlite3.Connection:
        conn = sqlite3.connect(":memory:", timeout=10.0, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        if self._shared is not None:
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

    # ── Conversations ─────────────────────────────────────────────────────────

    def create(self, session_id: str, title: str = "") -> str:
        """Opens a new active conversation and returns its id."""
        conversation_id = f"CNV-{uuid.uuid4().hex[:10].upper()}"
        now = _now()
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO conversations (id, session_id, status, title, created_at) "
                "VALUES (?, ?, 'active', ?, ?)",
                (conversation_id, session_id, title[:80], now),
            )
        return conversation_id

    def get(self, conversation_id: str) -> Optional[dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM conversations WHERE id = ?", (conversation_id,)
            ).fetchone()
        return dict(row) if row else None

    def status(self, conversation_id: str) -> Optional[str]:
        row = self.get(conversation_id)
        return row["status"] if row else None

    def require_active(self, conversation_id: str) -> None:
        """
        Raises unless the conversation exists and is still active.

        This is the server-side half of "history is read-only". The UI hides the
        composer, but the endpoint has to refuse as well.
        """
        row = self.get(conversation_id)
        if row is None:
            raise ConversationArchived("conversation not found")
        if row["status"] != "active":
            raise ConversationArchived(
                "This conversation is finished — start a new chat to plan "
                "another journey."
            )

    def archive(
        self,
        conversation_id: str,
        booking_reference: Optional[str] = None,
        title: Optional[str] = None,
    ) -> bool:
        """
        Closes a conversation. Returns False if it was already closed.

        Idempotent: payment confirmation can arrive more than once (a retry, or a
        refresh that re-posts the settlement), and re-archiving must not move the
        `ended_at` that marks when the trip actually finished.
        """
        now = _now()
        with self._lock, self._connect() as conn:
            cursor = conn.execute(
                "UPDATE conversations SET status = 'archived', ended_at = ?, "
                "booking_reference = COALESCE(?, booking_reference), "
                "title = COALESCE(NULLIF(?, ''), title) "
                "WHERE id = ? AND status = 'active'",
                (now, booking_reference, title, conversation_id),
            )
            return cursor.rowcount > 0

    def set_title(self, conversation_id: str, title: str) -> None:
        """Names the conversation after its first meaningful query."""
        if not title:
            return
        with self._lock, self._connect() as conn:
            conn.execute(
                "UPDATE conversations SET title = ? WHERE id = ? AND title = ''",
                (title[:80], conversation_id),
            )

    def list(
        self,
        status: Optional[str] = None,
        session_id: Optional[str] = None,
        limit: int = DEFAULT_HISTORY_LIMIT,
    ) -> list[dict[str, Any]]:
        """
        Conversations newest first, optionally filtered.

        Returns summaries with a message count and a preview rather than full
        transcripts: the sidebar renders a list, and loading every message of
        every past conversation to draw it would be wasteful.
        """
        clauses: list[str] = []
        params: list[Any] = []
        if status:
            clauses.append("c.status = ?")
            params.append(status)
        if session_id:
            clauses.append("c.session_id = ?")
            params.append(session_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""

        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT c.id, c.session_id, c.status, c.title, c.booking_reference,
                       c.created_at, c.ended_at,
                       (SELECT COUNT(*) FROM messages m WHERE m.conversation_id = c.id)
                           AS message_count,
                       (SELECT m.text FROM messages m
                         WHERE m.conversation_id = c.id AND m.role = 'user'
                         ORDER BY m.id LIMIT 1) AS preview
                FROM conversations c
                {where}
                ORDER BY c.created_at DESC, c.rowid DESC
                LIMIT ?
                """,
                (*params, limit),
            ).fetchall()

        summaries = []
        for row in rows:
            item = dict(row)
            item["preview"] = _preview(item.pop("preview") or "")
            summaries.append(item)
        return summaries

    def active_for_session(self, session_id: str) -> Optional[dict[str, Any]]:
        """The session's in-progress conversation, if it still has one."""
        rows = self.list(status="active", session_id=session_id, limit=1)
        return rows[0] if rows else None

    # ── Messages ──────────────────────────────────────────────────────────────

    def append(
        self,
        conversation_id: str,
        role: str,
        text: str,
        intent: Optional[str] = None,
    ) -> int:
        """
        Appends one message. Raises if the conversation has ended.

        The status check and the insert share a transaction so a turn cannot be
        written into a conversation that was archived concurrently.
        """
        now = _now()
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT status FROM conversations WHERE id = ?", (conversation_id,)
            ).fetchone()
            if row is None:
                raise ConversationArchived("conversation not found")
            if row["status"] != "active":
                raise ConversationArchived(
                    "This conversation is finished — start a new chat to plan "
                    "another journey."
                )
            cursor = conn.execute(
                "INSERT INTO messages (conversation_id, role, text, intent, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (conversation_id, role, text[:4000], intent, now),
            )
            return int(cursor.lastrowid or 0)

    def messages(self, conversation_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, role, text, intent, created_at FROM messages "
                "WHERE conversation_id = ? ORDER BY id",
                (conversation_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    # ── Pending transactions ──────────────────────────────────────────────────

    def bind_transaction(
        self, transaction_id: str, conversation_id: str, session_id: str
    ) -> None:
        """Records which conversation a held seat belongs to."""
        if not transaction_id:
            return
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO pending_transactions "
                "(transaction_id, conversation_id, session_id) VALUES (?, ?, ?)",
                (transaction_id, conversation_id, session_id),
            )

    def transaction_owner(self, transaction_id: str) -> Optional[dict[str, str]]:
        """The conversation and session a pending transaction belongs to."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM pending_transactions WHERE transaction_id = ?",
                (transaction_id,),
            ).fetchone()
        return dict(row) if row else None

    def count(self) -> int:
        with self._connect() as conn:
            return int(conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0])


def _now() -> str:
    """
    UTC timestamp with millisecond precision.

    Second precision was not enough: two conversations created in the same second
    tie on `created_at`, and the history list then ordered arbitrarily — so the
    sidebar could reshuffle between refreshes.
    """
    return datetime.now(tz=timezone.utc).isoformat(timespec="milliseconds")


def _preview(text: str, limit: int = 70) -> str:
    """One-line preview for the history list."""
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


def conversation_title_from(first_query: str) -> str:
    """
    Derives a readable conversation title from the traveller's first query.

    Used only for the history list, so a truncated query reads better than
    "New conversation".
    """
    cleaned = " ".join((first_query or "").split())
    if not cleaned:
        return ""
    return cleaned if len(cleaned) <= 60 else cleaned[:59] + "…"


# ── Process-wide store ────────────────────────────────────────────────────────
# One orchestrator process serves the UI.
CONVERSATIONS = ConversationStore.from_env()
