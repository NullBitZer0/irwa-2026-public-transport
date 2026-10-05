"""
Journey Search Tests — clarification-first route discovery
Member 2 — Planning Agent

Covers the behaviour the traveller asked for: work out the journey, ask for the
mode and time rather than guessing, then show what boards at that place and
time — including long-distance coaches that only pass through.

Run:
    pytest evaluation/test_journey_search.py -v
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.planner.hybrid_retriever import HybridTransitRetriever  # noqa: E402
from src.planner.journey_search import (  # noqa: E402
    find_services_at,
    is_major,
    missing_details,
    service_at,
    stop_index,
    stop_times,
)

COLOMBO = "Colombo Bastian Mawatha"


@pytest.fixture(scope="module")
def services() -> list[dict]:
    return HybridTransitRetriever().schedules


# ── Stop timing ──────────────────────────────────────────────────────────────

def test_stop_times_span_the_journey(services) -> None:
    service = next(s for s in services if s["route_id"] == "SLTB-1-KAND-COLO-0615")
    times = stop_times(service)
    assert len(times) == len(service["stops"])
    offsets = [offset for _, offset in times]
    assert offsets == sorted(offsets), "offsets must increase along the route"
    assert offsets[0] == 0


def test_service_at_reports_the_offset(services) -> None:
    service = next(s for s in services if s["route_id"] == "SLTB-1-KAND-COLO-0615")
    offset = service_at(service, "Kandy")
    assert offset == 0, "the first stop is the service origin"
    assert service_at(service, "Nowhere Town") is None


# ── Major cities only ────────────────────────────────────────────────────────

@pytest.mark.parametrize("city", ["Colombo", "Negombo", "Kandy", "Jaffna", "Galle"])
def test_major_cities_are_served(city: str) -> None:
    assert is_major(city) is True


@pytest.mark.parametrize("town", ["Kahawatta", "Wekada", "Some Village"])
def test_minor_places_are_not_served(town: str) -> None:
    assert is_major(town) is False


def test_minor_places_are_not_searched(services) -> None:
    assert find_services_at(services, "Kahawatta", "Kandy") == []


# ── What we still need to ask ────────────────────────────────────────────────

def test_asks_for_mode_and_time() -> None:
    assert missing_details("Kandy", COLOMBO, "ANY", None) == ["mode", "time"]


def test_asks_only_for_the_time_once_the_mode_is_known() -> None:
    assert missing_details("Kandy", COLOMBO, "BUS", None) == ["time"]


def test_nothing_missing_when_mode_and_time_given() -> None:
    assert missing_details("Negombo", COLOMBO, "BUS", "10:00") == []


def test_asks_for_endpoints_when_unknown() -> None:
    missing = missing_details(None, None, "ANY", None)
    assert "origin" in missing and "destination" in missing


def test_non_major_destination_is_flagged() -> None:
    assert "major_cities" in missing_details("Kandy", "Wekada", "BUS", "10:00")


# ── Direction ────────────────────────────────────────────────────────────────

def test_only_services_heading_the_right_way(services) -> None:
    """A Colombo → Jaffna coach already left Colombo; it cannot serve Negombo → Colombo."""
    for match in find_services_at(services, "Negombo", COLOMBO, "11:39", mode="BUS"):
        assert stop_index(match, COLOMBO) > stop_index(match, "Negombo")


def test_direct_service_is_recognised(services) -> None:
    matches = find_services_at(services, "Kandy", COLOMBO, "10:00", mode="BUS", limit=5)
    assert matches
    assert any(m["board_type"] == "direct" for m in matches)


def test_passing_service_is_flagged_with_its_true_start(services) -> None:
    """
    The headline case: a long-distance coach for another origin that passes the
    traveller's stop on the way to their destination.
    """
    matches = find_services_at(services, "Kandy", COLOMBO, "09:38", mode="BUS", limit=5)
    passing = [m for m in matches if m["board_type"] == "passing"]
    assert passing, "expected a coach that passes Kandy on its way to Colombo"
    assert all(m["service_origin"] != "Kandy" for m in passing)
    assert all(m["boards_at"] != m["departure_time"] for m in passing)


def test_mode_is_respected(services) -> None:
    for match in find_services_at(services, "Kandy", COLOMBO, "10:00", mode="TRAIN"):
        assert match["provider"] == "SLR"


# ── Time ─────────────────────────────────────────────────────────────────────

def test_services_are_ordered_by_closeness_to_the_requested_time(services) -> None:
    matches = find_services_at(services, "Kandy", COLOMBO, "10:00", mode="BUS", limit=5)
    deltas = [abs(m["minutes_from_requested"]) for m in matches]
    assert deltas == sorted(deltas), deltas


def test_window_excludes_distant_services(services) -> None:
    near = find_services_at(services, "Kandy", COLOMBO, "10:00", mode="BUS", window_minutes=30)
    wide = find_services_at(services, "Kandy", COLOMBO, "10:00", mode="BUS", window_minutes=120)
    assert len(near) <= len(wide)


def test_next_services_offered_rather_than_nothing(services) -> None:
    """A narrow window must still return the next departures, marked as later."""
    matches = find_services_at(
        services, "Negombo", COLOMBO, "10:00", mode="BUS", window_minutes=1, limit=5
    )
    assert matches, "a dead-strip search must not look like 'no service exists'"
    assert all(m.get("after_requested") for m in matches)
    assert all(m["minutes_from_requested"] > 0 for m in matches)


def test_no_time_given_returns_earliest_first(services) -> None:
    matches = find_services_at(services, "Kandy", COLOMBO, None, mode="BUS", limit=5)
    assert matches
    assert all(m["minutes_from_requested"] is None for m in matches)


def test_no_results_for_unknown_corridor(services) -> None:
    assert find_services_at(services, "Kandy", "Kandy") == []
    assert find_services_at(services, "", COLOMBO) == []
