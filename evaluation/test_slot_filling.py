"""
Multi-turn slot filling for route planning.

Member 4 — Responsible AI, Commercialization & Media Lead

Route planning is a conversation, not a single query. The traveller says "I need
to go to Colombo", the assistant asks where they are starting from and what
time, and only then is a real answer possible. Each of these slots has to
survive the turn in which it was given — otherwise the second turn forgets the
destination and the traveller is asked the same question forever.

Run:
    pytest evaluation/test_slot_filling.py -v
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.orchestrator.session_store import SlotStore  # noqa: E402


@pytest.fixture()
def store() -> SlotStore:
    return SlotStore()


# ── Accumulation ─────────────────────────────────────────────────────────────

def test_destination_is_remembered_when_follow_up_omits_it(store: SlotStore) -> None:
    """The core bug: turn two said only "from Kandy at 8am"."""
    store.merge("s1", {"destination": "Colombo Fort", "mode": None, "departure_time": None})
    merged = store.merge("s1", {"origin": "Kandy", "departure_time": "08:00"})

    assert merged["destination"] == "Colombo Fort"
    assert merged["origin"] == "Kandy"
    assert merged["departure_time"] == "08:00"


def test_mode_given_in_one_turn_persists_to_the_next(store: SlotStore) -> None:
    store.merge("s1", {"origin": "Kandy", "destination": "Colombo Fort", "mode": "TRAIN"})
    merged = store.merge("s1", {"departure_time": "09:00"})

    assert merged["mode"] == "TRAIN"


def test_a_later_answer_overrides_an_earlier_one(store: SlotStore) -> None:
    """Self-correction must work: "actually make it Kandy"."""
    store.merge("s1", {"origin": "Colombo Fort", "destination": "Kandy"})
    merged = store.merge("s1", {"origin": "Matara"})

    assert merged["origin"] == "Matara"
    assert merged["destination"] == "Kandy"


def test_any_never_overwrites_a_chosen_mode(store: SlotStore) -> None:
    """
    "ANY" means the traveller did not say — so it must not erase a choice.

    Treating ANY as a value would silently drop a mode the user already gave,
    because every unstated turn reports ANY.
    """
    store.merge("s1", {"mode": "BUS"})
    merged = store.merge("s1", {"mode": "ANY"})

    assert merged["mode"] == "BUS"


def test_empty_values_do_not_clear_a_slot(store: SlotStore) -> None:
    store.merge("s1", {"destination": "Galle"})
    merged = store.merge("s1", {"destination": None, "origin": ""})

    assert merged["destination"] == "Galle"


def test_all_slots_combine_across_three_turns(store: SlotStore) -> None:
    """The full conversation from the brief, slot by slot."""
    store.merge("s2", {"destination": "Colombo Fort"})
    store.merge("s2", {"origin": "Negombo"})
    merged = store.merge("s2", {"mode": "BUS", "departure_time": "10:00"})

    # Only stated slots are stored, so the merge is sparse rather than a dict of
    # Nones: an absent key and an explicit None mean the same thing here.
    assert merged["origin"] == "Negombo"
    assert merged["destination"] == "Colombo Fort"
    assert merged["mode"] == "BUS"
    assert merged["departure_time"] == "10:00"
    assert "passenger_token" not in merged


# ── Isolation ────────────────────────────────────────────────────────────────

def test_sessions_do_not_leak_into_each_other(store: SlotStore) -> None:
    """One traveller's destination must not become another's origin."""
    store.merge("alice", {"destination": "Colombo Fort"})
    merged = store.merge("bob", {"origin": "Kandy"})

    assert merged.get("destination") is None
    assert store.get("alice")["destination"] == "Colombo Fort"


def test_clear_resets_a_session(store: SlotStore) -> None:
    """'New conversation' must not inherit the previous trip's slots."""
    store.merge("s1", {"origin": "Kandy", "destination": "Colombo Fort"})
    store.clear("s1")

    assert store.get("s1") == {}


def test_idle_sessions_expire(store: SlotStore) -> None:
    """An abandoned clarification must not hold memory forever."""
    short = SlotStore(ttl_seconds=0)
    short.merge("s1", {"destination": "Colombo Fort"})

    assert short.get("s1") == {}


def test_store_is_bounded(store: SlotStore) -> None:
    """A stream of abandoned sessions cannot grow memory without limit."""
    bounded = SlotStore(max_sessions=5)
    for i in range(20):
        bounded.merge(f"s{i}", {"destination": f"City{i}"})

    assert len(bounded._sessions) <= 5


def test_merged_slots_are_a_copy(store: SlotStore) -> None:
    """A caller mutating the result must not corrupt the session."""
    merged = store.merge("s1", {"destination": "Kandy"})
    merged["destination"] = "Tampered"

    assert store.get("s1")["destination"] == "Kandy"


def test_passenger_token_is_never_accumulated(store: SlotStore) -> None:
    """Identity is derived per session by the caller, not carried from user text."""
    merged = store.merge(
        "s1", {"passenger_token": "TOKEN_nic_injected", "destination": "Kandy"}
    )

    assert "passenger_token" not in merged


def test_concurrent_merges_are_consistent() -> None:
    """FastAPI runs these in a thread pool; a lost update loses a slot."""
    import threading

    store = SlotStore()
    barrier = threading.Barrier(4)

    def add(key: str, value: str) -> None:
        barrier.wait()
        for _ in range(50):
            store.merge("shared", {key: value})

    threads = [
        threading.Thread(target=add, args=("origin", "Kandy")),
        threading.Thread(target=add, args=("destination", "Galle")),
        threading.Thread(target=add, args=("mode", "TRAIN")),
        threading.Thread(target=add, args=("departure_time", "08:00")),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    slots = store.get("shared")
    assert slots["origin"] == "Kandy"
    assert slots["destination"] == "Galle"
    assert slots["mode"] == "TRAIN"
    assert slots["departure_time"] == "08:00"
