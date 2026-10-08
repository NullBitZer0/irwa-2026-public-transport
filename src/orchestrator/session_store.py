"""
Conversation slot store.

Member 4 — Responsible AI, Commercialization & Media Lead

Route planning is a slot-filling conversation, not a single-shot query. The
traveller says "I need to go to Colombo", the assistant asks where they are
starting from and what time, and the answer is only useful once those are known.

Previously every turn re-parsed the message in isolation, so the second turn
("from Kandy at 8am") forgot the destination from the first and asked for it
again — an infinite clarification loop. This store keeps the slots gathered so
far per session, so each answer can be a delta and the final reply is the real
result.

Notes:
- Values only ever move from empty to filled, or are replaced by a newly stated
  value, so correcting yourself ("actually make it 10am") works.
- A slot is never invented: if the traveller never said it, it stays empty and
  the assistant keeps asking.
- In-memory, like the rest of the demo's session handling. Multi-replica
  deployment would move this to Redis with the same interface.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Optional

# Slots carried across turns. `passenger_token` is deliberately absent: it is
# derived per session by the caller, not accumulated from user text.
SLOT_KEYS = (
    "origin", "destination", "mode", "departure_date", "departure_time",
    "passengers",
    # Set once the traveller has answered the faster-or-cheaper question.
    "preference",
    # And that the question has been asked at all, so a reply of "faster" is
    # only read as an answer to something and not as part of a timetable query.
    "preference_asked",
    # Kept alongside the count, or the agent forgets the traveller already said
    # "2 seats" and asks them again a turn later.
    "seat_count_stated",
    # Which slots the last reply asked for, so a bare answer fills the right one.
    # "Ella" on its own is not a request to travel — it is the answer to "where
    # are you heading?", and without this it is filed as unintelligible.
    "awaiting",
)

# Services the planner last proposed for this session, so a plain "yes" can mean
# "book that one". Kept separate from SLOT_KEYS because these are not things the
# traveller said — they are the agent's own proposals, and must be overwritten
# rather than merged when new results arrive.
PROPOSED_KEY = "proposed_routes"

# How many proposed services to remember. Only the top one is ever acted on
# automatically; the rest exist so a later turn can name the second choice.
MAX_PROPOSED_ROUTES = 5

# A conversation abandoned mid-clarification should not hold memory forever.
IDLE_TTL_SECONDS = 30 * 60
MAX_SESSIONS = 500


@dataclass
class SessionSlots:
    """What the traveller has told us so far, plus when they last spoke."""

    slots: dict[str, Any] = field(default_factory=dict)
    updated_at: float = field(default_factory=time.monotonic)

    def get(self, key: str) -> Optional[Any]:
        return self.slots.get(key) or None


class SlotStore:
    """
    Thread-safe per-session slot accumulator with idle expiry.

    A bounded LRU-ish store: when full, the least recently updated session is
    evicted, so a stream of abandoned sessions cannot grow memory without limit.
    """

    def __init__(
        self,
        ttl_seconds: float = IDLE_TTL_SECONDS,
        max_sessions: int = MAX_SESSIONS,
    ) -> None:
        self._sessions: dict[str, SessionSlots] = {}
        self._lock = threading.Lock()
        self._ttl = ttl_seconds
        self._max = max_sessions

    def merge(self, session_id: str, parsed: dict[str, Any]) -> dict[str, Any]:
        """
        Folds newly parsed entities into the session and returns the full set.

        A value stated in this turn always wins; a slot the traveller has not
        mentioned keeps whatever was gathered earlier.
        """
        now = time.monotonic()
        with self._lock:
            self._expire(now)

            entry = self._sessions.get(session_id)
            if entry is None:
                if len(self._sessions) >= self._max:
                    self._evict_oldest()
                entry = SessionSlots(slots={})
                self._sessions[session_id] = entry

            for key in SLOT_KEYS:
                value = parsed.get(key)
                # "ANY" is not an answer: it means "I did not say", so it must
                # not overwrite a mode the traveller already gave.
                if value in (None, "", "ANY"):
                    continue
                entry.slots[key] = value

            entry.updated_at = now
            return dict(entry.slots)

    def propose(self, session_id: str, routes: list[dict[str, Any]]) -> None:
        """
        Records the services just offered, so "yes" has something to refer to.

        Replaced wholesale on every call: proposals from an earlier search must
        not survive into a new one, or "ready" could book a service the traveller
        is no longer looking at.
        """
        trimmed = [
            {
                "route_id": r.get("route_id"),
                "service_name": r.get("service_name"),
                # A connection's legs travel with it. Without them a later "yes"
                # can only see the connection's id, and there is no route to
                # price or book under that name. The change point comes too:
                # the confirmation has to name where the traveller changes.
                "legs": r.get("legs") or [],
                "transfer_station": r.get("transfer_station"),
                # Kept so a proposal can still be *described* later. When a
                # service is refused for lack of seats the alternatives are read
                # back out of here, and a card with no departure time is not an
                # alternative — it is a route id.
                "departure_time": r.get("departure_time"),
                "arrival_time": r.get("arrival_time"),
                "origin": r.get("origin"),
                "destination": r.get("destination"),
                "base_fare_lkr": r.get("base_fare_lkr"),
            }
            for r in routes[:MAX_PROPOSED_ROUTES]
            if r.get("route_id")
        ]
        with self._lock:
            entry = self._sessions.get(session_id)
            if entry is None:
                entry = SessionSlots(slots={})
                self._sessions[session_id] = entry
            entry.slots[PROPOSED_KEY] = trimmed
            entry.updated_at = time.monotonic()

    def proposed(self, session_id: str) -> list[dict[str, Any]]:
        """The services last offered to this session, best first."""
        with self._lock:
            entry = self._sessions.get(session_id)
            if not entry:
                return []
            return list(entry.slots.get(PROPOSED_KEY) or [])

    def get(self, session_id: str) -> dict[str, Any]:
        with self._lock:
            self._expire(time.monotonic())
            entry = self._sessions.get(session_id)
            return dict(entry.slots) if entry else {}

    def clear(self, session_id: str) -> None:
        with self._lock:
            self._sessions.pop(session_id, None)

    def _expire(self, now: float) -> None:
        for sid in [
            sid
            for sid, entry in self._sessions.items()
            if now - entry.updated_at > self._ttl
        ]:
            del self._sessions[sid]

    def _evict_oldest(self) -> None:
        if not self._sessions:
            return
        oldest = min(self._sessions, key=lambda s: self._sessions[s].updated_at)
        del self._sessions[oldest]


# Process-wide store: one orchestrator process serves the UI.
SLOTS = SlotStore()
