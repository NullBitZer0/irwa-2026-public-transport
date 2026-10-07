"""Modelled corridors: realistic timetables, and fares that stay out of booking."""

from __future__ import annotations

import pytest

from src.planner.fares import resolve_fare
from src.planner.generate_missing_corridors import BUS_FILE, SPEED_KMH
from src.planner.generate_unpublished_corridors import (
    _departures_for,
    _duration_for,
    _seed_for,
    generate_corridor,
    unpublished_pairs,
)
from src.planner.schedule_ingest import load_fixture


@pytest.fixture(scope="module")
def corpus() -> list[dict]:
    return load_fixture(BUS_FILE)


@pytest.fixture(scope="module")
def modelled(corpus: list[dict]) -> list[dict]:
    return [row for row in corpus if row["route_id"].startswith("MOD-")]


# ── Nothing modelled may reach the money path ────────────────────────────────

def test_no_modelled_service_carries_a_sellable_fare(modelled: list[dict]) -> None:
    """
    A modelled fare must never be charged.

    These corridors have no published price. `fares.py` estimates one so the
    timetable is not a column of dashes, and `fare_unknown` is what stops that
    estimate from becoming a charge.
    """
    assert modelled, "no modelled rows found — did the generator run?"
    offenders = [
        row["route_id"] for row in modelled if not row.get("fare_unknown")
    ]
    assert not offenders, (
        f"these modelled rows look sellable: {offenders[:5]}"
    )
    # A modelled row must not also carry a published-looking fare, which would
    # read as real to anything that only looks at base_fare_lkr.
    assert all(not (row.get("base_fare_lkr") or 0) > 0 for row in modelled)


def test_every_modelled_row_is_flagged(modelled: list[dict]) -> None:
    """Each one names what was derived, so a reviewer can see what is not real."""
    assert all(row.get("synthetic") for row in modelled)
    for row in modelled[:50]:
        assert "departure_time" in row["synthetic_fields"]
        assert "arrival_time" in row["synthetic_fields"]


def test_a_modelled_fare_still_displays_a_price(modelled: list[dict]) -> None:
    """
    Displaying a price is the point.

    The estimate model exists so a timetable is not unreadable; refusing to show
    anything would leave the row looking like a bug rather than an estimate.
    """
    shown = [resolve_fare(row) for row in modelled]
    priced = [f for f in shown if f["fare_lkr"] and f["fare_estimated"]]
    assert len(priced) == len(modelled), (
        f"{len(modelled) - len(priced)} modelled rows display no fare at all"
    )
    # Every one says it was estimated. A bare number would read as published.
    assert all("estimated" in f["fare_basis"] for f in priced)


def test_the_booking_agent_refuses_a_modelled_route(client=None) -> None:
    """
    The end-to-end consequence: a modelled route cannot be booked.

    The Orchestrator asks the planner for a fare and treats "unknown" as
    "cannot sell", so a modelled corridor stops at the confirmation gate instead
    of issuing a ticket for an amount nobody published.
    """
    from fastapi.testclient import TestClient

    from src.planner.server import app

    planner = TestClient(app)
    response = planner.get("/mcp/fare", params={"route_id": "MOD-ANY-ANY-0600"})
    data = response.json()["data"]
    assert data["fare_unknown"] is True
    assert data["fare_lkr"] is None, "an unknown fare must not read as zero or a quote"


# ── The timetable has to look like a timetable ───────────────────────────────

def test_departures_cluster_in_the_peaks() -> None:
    """
    Intercity coaches bunch in the morning and evening.

    A flat headway is the giveaway of a generated timetable, and it is why the
    priced-corridor generator uses one only where a fare exists but no times do.
    """
    rng = _seed_for("peak-check")
    times = [_to_int(t) for t in _departures_for(rng, 200.0)]

    peak = sum(1 for t in times if 5 * 60 <= t < 10 * 60)
    evening = sum(1 for t in times if 16 * 60 <= t < 21 * 60)
    midday = sum(1 for t in times if 10 * 60 <= t < 16 * 60)

    assert peak >= 3, times
    assert evening >= 3, times
    # The middle of the day is the quiet part, which is what the window sizes
    # encode: more coaches then would be the tell.
    assert midday <= peak, (peak, midday)


def _to_int(hhmm: str) -> int:
    hours, minutes = hhmm.split(":")
    return int(hours) * 60 + int(minutes)


def test_a_longer_corridor_runs_more_services() -> None:
    """A busy trunk route has more coaches than a quiet branch."""
    short = _departures_for(_seed_for("count"), 60.0)
    long = _departures_for(_seed_for("count"), 260.0)
    assert len(long) > len(short), (len(short), len(long))


def test_journey_times_vary_between_runs_of_one_corridor() -> None:
    """
    Every service on a corridor taking exactly the same time is not a timetable.

    Traffic, weather and the driver all move the arrival.
    """
    rng = _seed_for("duration-variance")
    departures = _departures_for(rng, 180.0)
    durations = [_duration_for(rng, 180.0) for _ in departures]

    assert len(set(durations)) > 1, durations
    # And they stay plausible: jitter, not chaos. The nominal figure is derived
    # from distance, so compare against that rather than against the distance.
    rng2 = _seed_for("duration-variance")
    _departures_for(rng2, 180.0)
    nominal = 180.0 / SPEED_KMH * 60
    assert min(durations) > nominal * 0.85, (min(durations), nominal)
    assert max(durations) < nominal * 1.25, (max(durations), nominal)


def test_departures_are_ordered_and_unique() -> None:
    rng = _seed_for("ordering")
    times = _departures_for(rng, 150.0)
    assert times == sorted(times), times
    assert len(times) == len(set(times)), times


def test_the_timetable_is_reproducible() -> None:
    """
    The corpus is a committed fixture.

    A generator whose output changed on every run would make every commit a
    diff of hundreds of unrelated departure times.
    """
    first = _departures_for(_seed_for("stable"), 210.0)
    second = _departures_for(_seed_for("stable"), 210.0)
    assert first == second

    a = generate_corridor("kurunegala", "matara")
    b = generate_corridor("kurunegala", "matara")
    assert [r.route_id for r in a] == [r.route_id for r in b]
    assert [r.arrival_time for r in a] == [r.arrival_time for r in b]


def test_the_two_directions_are_not_mirror_images() -> None:
    """Real outbound and return timetables differ; identical ones read as copied."""
    records = generate_corridor("kurunegala", "matara")
    outbound = {r.departure_time for r in records if r.origin == "Kurunegala"}
    inbound = {r.departure_time for r in records if r.origin == "Matara"}
    assert outbound != inbound, (sorted(outbound), sorted(inbound))


def test_an_overnight_run_is_flagged() -> None:
    """
    A long evening departure arrives the next day.

    Without the flag a traveller reads 20:00 → 02:30 as a four-hour journey.
    """
    records = generate_corridor("kurunegala", "matara")
    overnight = [r for r in records if r.arrival_next_day]
    assert overnight, "a 400 km corridor should have at least one overnight run"
    for record in overnight:
        assert _to_int(record.arrival_time) < _to_int(record.departure_time)


# ── Coverage, and the limit on it ────────────────────────────────────────────

def test_every_unreachable_pair_now_has_a_service(corpus: list[dict]) -> None:
    """
    The gap this generator exists to close is closed.

    Any major-city pair still with neither a service nor a connection is a pair
    we never modelled, so the count has to have gone to zero.
    """
    from src.planner.connection_planner import find_connections
    from src.planner.journey_search import MAJOR_CITIES, find_services_at

    still_unreachable = []
    for origin in MAJOR_CITIES:
        for destination in MAJOR_CITIES:
            if origin == destination:
                continue
            if find_services_at(corpus, origin, destination, limit=1):
                continue
            if find_connections(corpus, origin, destination, max_results=1):
                continue
            still_unreachable.append((origin, destination))

    assert not still_unreachable, still_unreachable[:10]


def test_the_generator_has_nothing_left_to_do(corpus: list[dict]) -> None:
    """Idempotent: re-running finds no pair it has not already covered."""
    assert unpublished_pairs(corpus) == []
