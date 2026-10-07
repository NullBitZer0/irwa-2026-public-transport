"""Station-name matching, which is where a data gap hides as a logic bug."""

from __future__ import annotations

import pytest

from src.planner.hybrid_retriever import (
    NUWARA_ELIYA,
    HybridTransitRetriever,
    _serves_direction,
    _station_key,
)
from src.planner.journey_search import MAJOR_CITIES, find_services_at


class TestStationKeys:
    @pytest.mark.parametrize(
        "name, expected",
        [
            # Adjacent-but-differently-named facilities share a key, which is what
            # makes a change at Colombo walkable.
            ("Colombo Fort", "colombo"),
            ("Colombo Bastian Mawatha", "colombo"),
            ("Pettah", "pettah"),  # Colombo grouping is STOP_CITY's job
            # Distinct cities stay distinct.
            ("Kandy", "kandy"),
            ("Jaffna", "jaffna"),
            ("Kurunegala", "kurunegala"),
            # Both spellings of the multi-word name key identically.
            ("Nuwara Eliya", NUWARA_ELIYA),
            ("nuwaraeliya", NUWARA_ELIYA),
            ("NUWARA ELIYA", NUWARA_ELIYA),
        ],
    )
    def test_key_is_stable(self, name: str, expected: str) -> None:
        assert _station_key(name) == expected

    def test_every_major_city_keys_to_itself(self) -> None:
        """
        A major city whose own name does not key to its own key is unreachable.

        This is the bug the Nuwara Eliya fix addresses: the city keys to
        "nuwaraeliya" in `MAJOR_CITIES`, so if its station name keyed to anything
        else, every service touching it would serve no city at all — and the
        coverage report would quietly exclude it.
        """
        for city in MAJOR_CITIES:
            assert _station_key(city) == city, city


def test_nuwara_eliya_is_reachable_and_was_not_before() -> None:
    """
    Regression: Nuwara Eliya had services and could not be found.

    `_station_key` took the first token, so "Nuwara Eliya" keyed to "nuwara" —
    matching no city node, no fare-chart key and no corpus row. The 222 services
    that touch it were all still there and all invisible.
    """
    schedules = HybridTransitRetriever().schedules

    touching = [
        s
        for s in schedules
        if NUWARA_ELIYA in (_station_key(s["origin"]), _station_key(s["destination"]))
    ]
    assert touching, "no services reach Nuwara Eliya at all"

    # And they now resolve to the city in both directions.
    outbound = find_services_at(schedules, "nuwaraeliya", "colombo", limit=3)
    assert outbound, "Nuwara Eliya → Colombo should have services"
    assert all(
        _station_key(s["origin"]) == NUWARA_ELIYA and _station_key(s["destination"]) == "colombo"
        for s in outbound
    )


def test_a_multi_word_city_does_not_split_from_its_corridor() -> None:
    """
    Nuwara Eliya and Ella are neighbouring towns on the same road.

    If "Nuwara Eliya" keyed to "nuwara" they could still not be confused with
    Ella — but a plain first-token key is one place-name collision away from it.
    """
    assert _station_key("Nuwara Eliya") != _station_key("Ella")
    assert _station_key("Nuwara Eliya") != _station_key("Nuwara Eliya Depot")


def test_direction_filter_still_rejects_the_reverse() -> None:
    """The fix must not make a service match in both directions."""
    schedules = HybridTransitRetriever().schedules
    nuwara_to_colombo = [
        s
        for s in schedules
        if _serves_direction(s, "Nuwara Eliya", "Colombo Fort")
    ]
    assert nuwara_to_colombo
    assert not any(
        _serves_direction(s, "Colombo Fort", "Nuwara Eliya") for s in nuwara_to_colombo
    )
