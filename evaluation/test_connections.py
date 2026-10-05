"""
Mixed-Mode Connection Tests
Member 2 — Planning Agent

Covers the fallback journey planner: when no direct train or bus runs between two
stations, the planner must offer a one-stop connection and prefer one that mixes
train with bus.

Connections are only ever built from service endpoints, because the fixtures hold
no per-stop times — so a connection is only proposed when the published timetable
genuinely supports it.

Run:
    pytest evaluation/test_connections.py -v
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.planner.connection_planner import (  # noqa: E402
    _format_minutes,
    _to_minutes,
    find_connections,
    public_connections,
    rank_connections,
)
from src.planner.hybrid_retriever import (  # noqa: E402
    HybridTransitRetriever,
    _station_key,
)


@pytest.fixture(scope="module")
def services() -> list[dict]:
    return HybridTransitRetriever().schedules


# ── Time helpers ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "value, expected",
    [("07:30", 450), ("00:00", 0), ("23:59", 1439), ("bad", None), (None, None)],
)
def test_to_minutes(value, expected) -> None:
    assert _to_minutes(value) == expected


def test_format_minutes_wraps_past_midnight() -> None:
    assert _format_minutes(450) == "07:30"
    assert _format_minutes(24 * 60 + 90) == "01:30"


# ── Guard rails ──────────────────────────────────────────────────────────────

def test_no_connections_without_both_endpoints(services) -> None:
    assert find_connections(services, "Kandy", "") == []
    assert find_connections(services, "", "Jaffna") == []


def test_no_connections_to_the_same_station(services) -> None:
    assert find_connections(services, "Kandy", "Kandy") == []


def test_no_connection_without_a_shared_hub(services) -> None:
    """Nothing runs out of Galle, so Galle → Badulla cannot be connected here."""
    assert find_connections(services, "Galle", "Badulla") == []


# ── Finding connections ──────────────────────────────────────────────────────

def test_finds_mixed_mode_connection(services) -> None:
    """Kandy → Jaffna has no direct service; a train+bus change at Colombo works."""
    connections = find_connections(services, "Kandy", "Jaffna")
    assert connections, "expected a Kandy → Jaffna connection"

    best = connections[0]
    assert best["is_connection"] is True
    assert best["mixed_mode"] is True, "a mix of train and bus should rank first"
    assert "Colombo Fort" in best["transfer_station"]
    assert [leg["mode"] for leg in best["legs"]] == ["TRAIN", "BUS"]


def test_mixed_mode_is_ranked_above_same_mode(services) -> None:
    """Mixed-mode options come first, then same-mode ones."""
    connections = find_connections(services, "Kandy", "Jaffna")
    mixed_flags = [c["mixed_mode"] for c in connections]
    assert mixed_flags == sorted(mixed_flags, reverse=True), mixed_flags


def test_legs_chain_through_the_transfer_station(services) -> None:
    for connection in find_connections(services, "Kandy", "Jaffna"):
        first, second = connection["legs"]
        # Same hub, though it may be two adjacent facilities with different names.
        assert _station_key(first["destination"]) == _station_key(second["origin"]), (
            "a connection must join up at one hub"
        )
        assert first["route_id"] != second["route_id"]


def test_adjacent_hubs_name_both_facilities(services) -> None:
    """
    Colombo Fort station and Bastian Mawatha bus stand are different places.

    They share a hub key so the change is walkable, but the traveller has to be
    told the coach leaves from the bus stand, not the platform.
    """
    connections = [
        c for c in find_connections(services, "Kandy", "Jaffna") if c["adjacent_hub"]
    ]
    assert connections, "expected a walkable change between Fort and Bastian Mawatha"
    for connection in connections:
        assert "Colombo Fort" in connection["transfer_station"]
        assert "Bastian Mawatha" in connection["transfer_station"]


def test_connection_totals_match_its_legs(services) -> None:
    for connection in find_connections(services, "Kandy", "Jaffna"):
        expected = sum(float(leg["base_fare_lkr"] or 0) for leg in connection["legs"])
        assert connection["base_fare_lkr"] == pytest.approx(expected)
        assert connection["origin"] == connection["legs"][0]["origin"]
        assert connection["destination"] == connection["legs"][-1]["destination"]


def test_connection_is_not_bookable_as_one_ticket(services) -> None:
    """Two legs are two tickets, so the UI must not offer a single seat hold."""
    for connection in find_connections(services, "Kandy", "Jaffna"):
        assert connection["bookable"] is False


# ── Time feasibility ─────────────────────────────────────────────────────────

def test_connection_respects_minimum_changeover(services) -> None:
    """The Kandy→Colombo train arrives 18:05 and the coach leaves 18:30."""
    best = find_connections(services, "Kandy", "Jaffna")[0]
    assert best["transfer_minutes"] >= 20, best["transfer_minutes"]


def test_connection_window_is_capped_at_24_hours(services) -> None:
    """
    A changeover longer than a day is not a connection, it is an unrelated service.

    The Kandy → Colombo train arrives 18:05; the earliest coach onwards leaves the
    next morning, so nothing may be offered once the window is exceeded.
    """
    assert find_connections(services, "Kandy", "Jaffna", min_transfer_minutes=25 * 60) == []


def test_overnight_change_is_flagged(services) -> None:
    """Arriving 19:03 then catching an early service is a change, not a same-day trip."""
    overnight = [
        c for c in find_connections(services, "Jaffna", "Kandy") if c["overnight_change"]
    ]
    assert overnight, "expected at least one overnight connection"
    assert all(c["transfer_minutes"] > 360 for c in overnight)


def test_total_duration_is_positive(services) -> None:
    """Overnight legs must roll over, not produce a negative journey time."""
    for origin, destination in [
        ("Kandy", "Jaffna"),
        ("Jaffna", "Kandy"),
        ("Kandy", "Galle"),
        ("Jaffna", "Galle"),
    ]:
        for connection in find_connections(services, origin, destination):
            assert connection["duration_minutes"] > 0, (origin, destination, connection)


# ── Mode handling ────────────────────────────────────────────────────────────

def test_train_only_query_keeps_both_legs_as_trains(services) -> None:
    for connection in find_connections(services, "Kandy", "Jaffna", mode="TRAIN"):
        assert [leg["mode"] for leg in connection["legs"]] == ["TRAIN", "TRAIN"]


def test_bus_only_query_keeps_both_legs_as_buses(services) -> None:
    found = find_connections(services, "Kandy", "Jaffna", mode="BUS")
    for connection in found:
        assert all(leg["mode"] == "BUS" for leg in connection["legs"])


def test_max_results_is_respected(services) -> None:
    assert len(find_connections(services, "Jaffna", "Kandy", max_results=1)) == 1


def test_ranking_is_idempotent(services) -> None:
    """
    The endpoint combines two searches and ranks the union, so ranking must be
    safe to apply to already-ranked connections.
    """
    once = find_connections(services, "Kandy", "Jaffna")
    twice = rank_connections(rank_connections(list(once)))
    assert [c["route_id"] for c in once] == [c["route_id"] for c in twice]


def test_public_connections_hides_internal_keys(services) -> None:
    """Internal ranking keys must not leak into the API response."""
    stripped = public_connections(find_connections(services, "Kandy", "Jaffna"))
    assert stripped
    for connection in stripped:
        assert not [k for k in connection if k.startswith("_")]


# ── Endpoint behaviour ───────────────────────────────────────────────────────

def _plan(query: str, travel_mode: str = "ANY") -> dict:
    from fastapi.testclient import TestClient

    from src.planner.server import app as planner_app

    res = TestClient(planner_app).post(
        "/mcp/plan_route",
        json={
            "origin": "",
            "destination": "",
            "travel_mode": travel_mode,
            "date_str": "TODAY",
            "raw_query": query,
        },
    )
    assert res.status_code == 200, res.text
    return res.json()["data"]


def test_endpoint_returns_a_connection_when_no_direct_service() -> None:
    data = _plan("Kandy indan Jaffna yanna")
    assert data["route_options"] == [], data["route_options"]
    assert data["connections"], "expected a connection"


def test_endpoint_keeps_the_mixed_option_for_a_single_mode_request() -> None:
    """
    Asking for a train must not hide the much quicker train-then-coach change.
    """
    data = _plan("Kandy indan Jaffna yanna train ekak thiyeda?")
    assert data["route_options"] == [], data["route_options"]
    assert data["connections"], "expected a connection"
    assert any(c["mixed_mode"] for c in data["connections"]), (
        "the mixed train+bus option should still be offered"
    )


def test_endpoint_connections_carry_no_internal_keys() -> None:
    data = _plan("Kandy indan Jaffna yanna")
    for connection in data["connections"]:
        assert not [k for k in connection if k.startswith("_")]
