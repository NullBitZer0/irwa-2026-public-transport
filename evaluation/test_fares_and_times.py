"""
Timetable quality: fares, and times that look like a timetable.

Both of these came from looking at the rendered timetable and finding it wrong:
62% of services had no price at all, and departures clustered on three clock
times so that 28% of the network left at 06:30. The tests below pin the fixes, so
regenerating the corpus cannot quietly reintroduce either.

Run:
    pytest evaluation/test_fares_and_times.py -v
"""

from __future__ import annotations

import os
import sys
from collections import Counter

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.planner import geo  # noqa: E402
from src.planner.fares import (  # noqa: E402
    FARE_MODEL,
    estimate_fare_lkr,
    great_circle_km,
    resolve_fare,
)
from src.planner.hybrid_retriever import HybridTransitRetriever  # noqa: E402
from src.planner.server import app as planner_app  # noqa: E402

SCHEDULES = HybridTransitRetriever().schedules
BUS = [r for r in SCHEDULES if "TRAIN" not in str(r.get("route_id", ""))]


@pytest.fixture(scope="module")
def client():
    return TestClient(planner_app)


def _to_minutes(hhmm: str) -> int:
    hours, minutes = str(hhmm).split(":")
    return int(hours) * 60 + int(minutes)


# ── Departure and arrival times ──────────────────────────────────────────────


def test_departures_are_not_concentrated_on_a_few_clock_times() -> None:
    """
    Regression: every route departed at 06:30, 13:15 or 19:45.

    That came from a single hardcoded triple applied to all routes. A timetable
    where a quarter of the network leaves at the same minute is not a timetable.
    """
    departures = Counter(r["departure_time"] for r in BUS)
    busiest_time, busiest_count = departures.most_common(1)[0]

    assert len(departures) >= 150, f"only {len(departures)} distinct departure times"
    assert busiest_count / len(BUS) < 0.05, (
        f"{busiest_count} of {len(BUS)} services ({100*busiest_count//len(BUS)}%) "
        f"depart at {busiest_time}"
    )


def test_arrival_times_are_not_derived_from_one_duration_per_class() -> None:
    """
    Arrivals were as clustered as departures, because the journey duration came
    from the route-code prefix rather than from how far the route goes.
    """
    arrivals = Counter(r["arrival_time"] for r in BUS)
    busiest_time, busiest_count = arrivals.most_common(1)[0]

    assert len(arrivals) >= 150, f"only {len(arrivals)} distinct arrival times"
    assert busiest_count / len(BUS) < 0.05, (
        f"{busiest_count} of {len(BUS)} services arrive at {busiest_time}"
    )


def test_longer_journeys_take_longer() -> None:
    """A sanity check on distance-derived durations."""

    def km(record: dict) -> float | None:
        origin, destination = geo.resolve(record.get("origin")), geo.resolve(record.get("destination"))
        if not origin or not destination:
            return None
        return great_circle_km((origin[1], origin[2]), (destination[1], destination[2]))

    samples = []
    for record in BUS:
        distance = km(record)
        if distance and distance > 30:
            samples.append((distance, _to_minutes(record["arrival_time"]) - _to_minutes(record["departure_time"])))
    samples.sort()
    short = [d for _, d in samples[: max(1, len(samples) // 4)]]
    long = [d for _, d in samples[-max(1, len(samples) // 4):]]

    assert sum(long) / len(long) > sum(short) / len(short), (
        "estimated journey time does not grow with distance"
    )


def test_services_running_past_midnight_are_flagged() -> None:
    """
    An arrival clock earlier than the departure clock is only correct with the
    next-day flag set. Reading these as a negative duration is how an overnight
    service gets "fixed" into something impossible.
    """
    overnight = [
        r for r in SCHEDULES if _to_minutes(r["arrival_time"]) < _to_minutes(r["departure_time"])
    ]

    assert overnight, "the corpus should contain some overnight services"
    for record in overnight:
        assert record.get("arrival_next_day") is True, (
            f"{record['route_id']} arrives before it departs but is not flagged"
        )


# ── Fares ────────────────────────────────────────────────────────────────────


def test_a_published_fare_is_never_replaced_by_an_estimate() -> None:
    """
    The only thing the estimator must not do is overwrite a real price.
    """
    priced = [r for r in BUS if (r.get("base_fare_lkr") or 0) > 0]
    assert priced, "the corpus should contain published fares"

    for record in priced[:120]:
        resolved = resolve_fare(record)
        assert resolved["fare_estimated"] is False
        assert resolved["fare_lkr"] == float(record["base_fare_lkr"])


def test_unpriced_services_get_an_estimate_that_says_so() -> None:
    unpriced = [r for r in BUS if not (r.get("base_fare_lkr") or 0)]
    assert unpriced, "the corpus should contain unpriced services"

    estimated = [resolve_fare(r) for r in unpriced]
    with_fare = [x for x in estimated if x["fare_lkr"] is not None]
    assert with_fare, "no unpriced service could be estimated"

    for resolved in with_fare:
        assert resolved["fare_estimated"] is True
        assert resolved["fare_basis"], "an estimate must state what it is based on"


def test_same_city_services_are_never_estimated() -> None:
    """
    Fort to Pettah is a few hundred metres. Any fare for it would be invented,
    so the estimator declines instead of producing a number.
    """
    intra_city = [
        r for r in BUS
        if (geo.resolve(r.get("origin")) or ("",))[0] == (geo.resolve(r.get("destination")) or ("",))[0]
        and not (r.get("base_fare_lkr") or 0)
    ]
    assert intra_city, "the corpus should contain same-city services"

    for record in intra_city[:60]:
        assert estimate_fare_lkr(record) is None, (
            f"{record['origin']} -> {record['destination']} was given a made-up fare"
        )


def test_estimates_land_in_the_same_range_as_published_fares() -> None:
    """
    The model is fitted, and the fit is asserted rather than left to taste.

    A power law was chosen over a linear one deliberately: the linear fit had a
    better median but put a 41 km hop at LKR 410 when the published fare is
    LKR 170, because the long-distance intercept dominates at short distances.
    """
    errors = sorted(
        abs(estimate["fare_lkr"] - record["base_fare_lkr"]) / record["base_fare_lkr"]
        for record in BUS
        if (record.get("base_fare_lkr") or 0) > 0
        and (estimate := estimate_fare_lkr(record)) is not None
    )
    assert len(errors) > 100, "not enough published fares to judge the model against"

    median = errors[len(errors) // 2]
    p80 = errors[int(len(errors) * 0.8)]

    assert median < 0.35, f"median estimate error {median:.0%} is too high"
    assert p80 < 0.55, f"80% of estimates should be within 55%, currently {p80:.0%}"
    assert errors[-1] < 1.0, f"worst estimate is off by {errors[-1]:.0%}"


def test_the_fare_model_is_monotone_and_bounded() -> None:
    """
    Distance is the only input, so a longer journey can never cost less.
    """
    coefficient, exponent = FARE_MODEL["ordinary"]

    def model(km: float) -> float:
        return coefficient * km**exponent

    distances = [2, 5, 10, 25, 50, 100, 200, 400, 800]
    values = [model(d) for d in distances]

    assert values == sorted(values), "a longer journey quoted cheaper"
    assert model(1) > 0
    assert model(1000) < 10000, "an implausible fare for a very long journey"


def test_estimated_fares_are_quoted_in_whole_tens() -> None:
    """
    Rupees are sold in tens, so an estimate of 412.37 is not a fare.

    Published fares are left alone: the NTC chart contains amounts that are not
    round, and rounding someone else's published price would be worse than
    showing it.
    """
    for record in BUS[:400]:
        resolved = resolve_fare(record)
        if resolved["fare_lkr"] is None or not resolved["fare_estimated"]:
            continue
        assert resolved["fare_lkr"] % 10 == 0, resolved["fare_lkr"]


# ── The timetable endpoint ────────────────────────────────────────────────────


def test_timetable_mode_filtering_returns_only_that_mode(client: TestClient) -> None:
    """
    All / Buses / Trains must each return exactly their own mode.
    """
    for mode, expected in (("BUS", {"BUS"}), ("TRAIN", {"TRAIN"})):
        data = client.get("/mcp/schedules", params={"mode": mode, "limit": 500}).json()["data"]
        assert data["services"], f"no services returned for {mode}"
        assert {s["mode"] for s in data["services"]} == expected

    everything = client.get("/mcp/schedules", params={"mode": "ALL", "limit": 500}).json()["data"]
    assert {s["mode"] for s in everything["services"]} == {"BUS", "TRAIN"}


def test_timetable_reports_how_many_fares_are_estimated(client: TestClient) -> None:
    data = client.get("/mcp/schedules", params={"limit": 500}).json()["data"]

    assert data["published_fares"] + data["estimated_fares"] == len(data["services"])
    assert data["estimated_fares"] > 0, "the unpriced majority should be estimated"
    for service in data["services"]:
        assert service["fare_estimated"] in (True, False)
        if service["fare_estimated"]:
            assert service["fare_lkr"] is not None
            assert service["fare_basis"]


def test_timetable_does_not_send_an_estimate_into_the_booking_path(
    client: TestClient,
) -> None:
    """
    The timetable shows an estimate; booking must still refuse to sell from one.

    `base_fare_lkr` is what the booking agent charges against, so the endpoint
    must not write the estimate back into it.
    """
    data = client.get("/mcp/schedules", params={"mode": "BUS", "limit": 500}).json()["data"]

    for service in data["services"]:
        if not service["fare_estimated"]:
            continue
        original = next(r for r in BUS if r["route_id"] == service["route_id"])
        assert original.get("base_fare_lkr", 0) in (0, None), (
            f"{service['route_id']} gained a fare in the corpus — an estimate has "
            f"leaked into the price the booking agent would charge"
        )
