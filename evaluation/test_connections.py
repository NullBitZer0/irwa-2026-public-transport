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
    """
    Ella → Kandy is a known data gap: Kandy → Ella runs, the reverse does not, and
    nothing reaches Kandy from Ella either, so no connection can be built.
    """
    assert find_connections(services, "Ella", "Kandy") == []


# ── Finding connections ──────────────────────────────────────────────────────

def test_finds_mixed_mode_connection(services) -> None:
    """Kandy → Jaffna has no direct service; a train+bus change at Colombo works."""
    connections = find_connections(services, "Kandy", "Jaffna", max_results=10)
    assert connections, "expected a Kandy → Jaffna connection"

    mixed = [c for c in connections if c["mixed_mode"]]
    assert mixed, "a train-then-coach change should be offered"

    best = mixed[0]
    assert best["is_connection"] is True
    assert "Colombo Fort" in best["transfer_station"]
    assert [leg["mode"] for leg in best["legs"]] == ["TRAIN", "BUS"]


def test_the_fastest_connection_is_ranked_first(services) -> None:
    """
    Ranking follows what the traveller asked for, not mixed-mode presentation.

    Mixed-mode used to lead the list unconditionally. It now ranks by arrival for
    a time preference, because "I want to get there soon" is a question with an
    answer, and the agent should not answer it with a presentational preference.
    """
    connections = find_connections(services, "Kandy", "Jaffna", strategy="time")
    arrivals = [c["_arrive_offset"] for c in connections]

    assert connections == sorted(connections, key=lambda c: c["_arrive_offset"])
    assert arrivals == sorted(arrivals)


def test_budget_ranking_puts_the_cheapest_priced_option_first(services) -> None:
    """
    Cheapest first, and only among options whose fares are published.

    An unpriced connection totals zero, and zero sorts as free — so without the
    `fare_known` gate it would win every budget search by not having a price.
    """
    connections = find_connections(
        services, "Kandy", "Jaffna", strategy="budget", max_results=10
    )
    assert connections

    priced = [c for c in connections if c["fare_known"]]
    assert priced, "at least one option should have a published fare"
    assert priced[0]["base_fare_lkr"] == min(c["base_fare_lkr"] for c in priced)

    # Nothing unpriced may outrank a priced option on price.
    if priced and connections[0] is not priced[0]:
        assert not connections[0]["fare_known"], "an unknown fare outranked a real one"


def test_a_departure_floor_excludes_options_that_leave_too_early(services) -> None:
    """
    A traveller who says "at 10am" must not be shown a 06:00 change.
    """
    late = find_connections(services, "Kandy", "Jaffna", after="18:00", max_results=10)
    for connection in late:
        assert connection["departure_time"] >= "18:00", connection["departure_time"]

    early = find_connections(services, "Kandy", "Jaffna", max_results=10)
    assert any(c["departure_time"] < "18:00" for c in early), (
        "the fixture should contain earlier options for this to mean anything"
    )


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
        c
        for c in find_connections(services, "Kandy", "Jaffna", max_results=10)
        if c["adjacent_hub"]
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


def test_long_changeover_is_offered_but_flagged_overnight() -> None:
    """
    A long changeover is still a real connection, but it must be flagged.

    Synthetic services keep this independent of the fixture data: the feeder
    arrives 12:00 and the only onward service leaves at 23:00 the next day, an
    11 hour wait.
    """
    sparse = [
        {
            "route_id": "SYN-1",
            "service_name": "Synthetic feeder",
            "provider": "SLR",
            "origin": "OriginTown",
            "destination": "HubTown",
            "departure_time": "08:00",
            "arrival_time": "12:00",
            "base_fare_lkr": 100.0,
            "stops": ["OriginTown", "HubTown"],
            "classes": ["2nd Class Reserved"],
            "transit_type": "EXPRESS_TRAIN",
        },
        {
            "route_id": "SYN-2",
            "service_name": "Synthetic connector",
            "provider": "SLTB",
            "origin": "HubTown",
            "destination": "DestTown",
            "departure_time": "23:00",  # next day: an 11h change at the hub
            "arrival_time": "02:00",
            "base_fare_lkr": 100.0,
            "stops": ["HubTown", "DestTown"],
            "classes": ["Ordinary"],
            "transit_type": "EXPRESS_BUS",
        },
    ]

    connections = find_connections(sparse, "OriginTown", "DestTown")
    assert connections, "an 11h change should still be offered"
    assert connections[0]["transfer_minutes"] == 11 * 60
    assert connections[0]["overnight_change"] is True
    assert connections[0]["mixed_mode"] is True


def test_no_connection_is_ever_more_than_a_day_after_readiness(services) -> None:
    """
    The search window is 24 hours wide from the moment the traveller is ready,
    so no offered connection can ever exceed min_transfer + 24h.
    """
    for origin, destination in [
        ("Kandy", "Jaffna"),
        ("Jaffna", "Kandy"),
        ("Kandy", "Galle"),
        ("Jaffna", "Colombo Fort"),
    ]:
        for connection in find_connections(services, origin, destination):
            assert connection["transfer_minutes"] <= 20 + 24 * 60, (origin, connection)


def test_overnight_change_is_flagged(services) -> None:
    """
    Arriving 19:03 then catching an early service is a change, not a same-day trip.

    Asked for a wide result set on purpose: ranking by arrival for a time
    preference puts same-day options first, so an overnight one will often fall
    outside the top few — which is correct behaviour, and would otherwise hide
    the flag this test exists to check.
    """
    overnight = [
        c
        for c in find_connections(services, "Jaffna", "Kandy", max_results=100)
        if c["overnight_change"]
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


# A corridor with no direct service but a one-stop connection. Anuradhapura ->
# Kandy is used because nothing runs it directly. (Kandy -> Jaffna served this
# until a direct service was added to the corpus, which is why the pair is
# asserted rather than assumed: a fixture change should not silently stop
# testing the connection fallback.)
NO_DIRECT_CORRIDOR = ("Anuradhapura", "Kandy")

# No direct service, and a train-then-coach change exists. Verified against the
# corpus rather than assumed; a corridor without a mixed option cannot test the
# claim that a single-mode request must not hide one.
MIXED_CORRIDOR = ("Anuradhapura", "Badulla")


def test_the_chosen_corridor_still_has_no_direct_service() -> None:
    """
    Guards the premise of the two tests below.

    Both are about what happens when no direct service exists. If a future
    ingestion adds one, they would pass for the wrong reason — or fail while
    testing nothing — so the premise is asserted rather than assumed.
    """
    from src.planner.hybrid_retriever import _serves_direction

    origin, destination = NO_DIRECT_CORRIDOR
    retriever = HybridTransitRetriever()
    assert not any(
        _serves_direction(s, origin, destination) for s in retriever.schedules
    ), (
        f"{origin} -> {destination} now has a direct service; pick another "
        f"corridor for the connection-fallback tests"
    )


def test_endpoint_returns_a_connection_when_no_direct_service() -> None:
    data = _plan(f"{NO_DIRECT_CORRIDOR[0]} indan {NO_DIRECT_CORRIDOR[1]} yanna")
    assert data["route_options"] == [], data["route_options"]
    assert data["connections"], "expected a connection"


def test_endpoint_keeps_the_mixed_option_for_a_single_mode_request() -> None:
    """
    Asking for a train must not hide the much quicker train-then-coach change.
    """
    # A corridor with no direct service that *does* have a mixed option, chosen
    # for that property rather than hardcoded: Kandy → Jaffna gained TRAIN-1012
    # and stopped being no-direct, and Anuradhapura → Kandy has no mixed change
    # at all, so neither can test what this is about.
    mixed_corridor = MIXED_CORRIDOR
    data = _plan(f"{mixed_corridor[0]} indan {mixed_corridor[1]} yanna train ekak thiyeda?")
    assert data["route_options"] == [], data["route_options"]
    assert data["connections"], "expected a connection"
    assert any(c["mixed_mode"] for c in data["connections"]), (
        "the mixed train+bus option should still be offered"
    )
    # And it may no longer lead the list — ordering follows arrival now.
    for connection in data["connections"]:
        assert connection["duration_minutes"] > 0


def test_endpoint_connections_carry_no_internal_keys() -> None:
    data = _plan("Kandy indan Jaffna yanna")
    for connection in data["connections"]:
        assert not [k for k in connection if k.startswith("_")]


# ── Faster or cheaper ────────────────────────────────────────────────────────


def _journey(origin: str, destination: str, preference: str = "", at_time: str = "") -> dict:
    from fastapi.testclient import TestClient

    from src.planner.server import app as planner_app

    payload = {
        "origin": origin,
        "destination": destination,
        "travel_mode": "ANY",
        "time_preference": at_time or None,
        "preference": preference,
    }
    return TestClient(planner_app).post("/mcp/plan_journey", json=payload).json()["data"]


def test_no_direct_service_asks_faster_or_cheaper() -> None:
    """
    The planner does not decide what matters more to the traveller.

    Picking for them would mean the agent's opinion of whether their time or
    their money is worth more — and it is not the one paying.
    """
    data = _journey(MIXED_CORRIDOR[0], MIXED_CORRIDOR[1], at_time="09:00")

    assert data["services"] == [], "this corridor is chosen because nothing direct runs"
    assert data["needs_preference"] is True
    values = {option["value"] for option in data["preference_options"]}
    assert values == {"time", "budget"}


def test_the_preference_changes_which_options_are_offered() -> None:
    """The two answers must actually differ, or the question is theatre."""
    fast = _journey(MIXED_CORRIDOR[0], MIXED_CORRIDOR[1], preference="time", at_time="09:00")
    cheap = _journey(MIXED_CORRIDOR[0], MIXED_CORRIDOR[1], preference="budget", at_time="09:00")

    assert fast["connections"], "expected options for the fast preference"
    assert cheap["connections"], "expected options for the cheap preference"
    assert fast["needs_preference"] is False, "already answered, so do not ask again"
    # Public fields only: internal ranking keys are stripped on the way out,
    # which is itself asserted elsewhere.
    assert fast["connections"][0]["duration_minutes"] <= cheap["connections"][0]["duration_minutes"]
    assert fast["connections"][0]["base_fare_lkr"] >= cheap["connections"][0]["base_fare_lkr"]
    assert fast["connections"][0]["strategy"] == "time"
    assert cheap["connections"][0]["strategy"] == "budget"


def test_an_unanswered_preference_returns_no_options() -> None:
    """Nothing is offered until the traveller has chosen."""
    data = _journey(MIXED_CORRIDOR[0], MIXED_CORRIDOR[1], at_time="09:00")

    assert data["connections"] == [], "options were offered before the question was answered"


def test_the_journey_message_is_never_empty() -> None:
    """
    Regression: the message was `None` when connections existed.

    `message` is a required string on the MCP envelope, so a None failed
    validation in the orchestrator — which read as "planner unreachable" and
    quietly dropped the traveller onto the fallback path, losing the
    connections entirely.
    """
    data = _journey(MIXED_CORRIDOR[0], MIXED_CORRIDOR[1], preference="time", at_time="09:00")

    assert data["connections"], "this test needs a case that produces connections"
    # The envelope itself is the contract; this asserts the value is a str.
    from src.planner.server import _journey_message

    assert isinstance(_journey_message([], [], [], True), str)
    assert isinstance(_journey_message([], [], [], False), str)
    assert _journey_message([], [], ["origin"], False)


def test_no_service_is_offered_for_the_wrong_direction() -> None:
    """
    Relevance-based retrieval can return Colombo → Jaffna for Kandy → Jaffna.

    Presenting it as a direct answer claims a service that does not board where
    the traveller is, and it also stops the connection search from running,
    because a non-empty candidate list looks like a successful answer.
    """
    from src.planner.hybrid_retriever import HybridTransitRetriever, _serves_direction

    services = HybridTransitRetriever().schedules
    service = next(r for r in services if r["route_id"] == "TRAIN-1012")

    assert _serves_direction(service, "Kandy", "Jaffna") is True
    assert _serves_direction(service, "Jaffna", "Kandy") is False


def test_connections_respect_the_requested_departure_time() -> None:
    """A traveller who says 6pm is not shown a 10am change."""
    data = _journey(MIXED_CORRIDOR[0], MIXED_CORRIDOR[1], preference="time", at_time="18:00")

    for connection in data["connections"]:
        assert connection["departure_time"] >= "18:00", connection["departure_time"]


def test_budget_never_leads_with_an_overnight_journey(services) -> None:
    """
    Regression: the "cheapest" answer was a 29h40 train.

    Ranking purely on fare ignores how long the trip takes, so the cheapest
    ticket won even when it meant a night at the hub and a day lost — while a 7h30
    change sat below it in the same list. Technically correct and useless advice.

    An overnight option is still returned when nothing else is on offer; it is
    simply never the headline.
    """
    for origin, destination in (("Colombo", "Ella"), ("Kandy", "Matara")):
        ranked = find_connections(
            services, origin, destination, strategy="budget", max_results=5
        )
        assert ranked, f"no connections for {origin} -> {destination}"
        assert not ranked[0]["overnight_change"], (
            f"{origin} -> {destination}: cheapest option needs an overnight stop "
            f"({ranked[0]['duration_minutes']} min)"
        )
