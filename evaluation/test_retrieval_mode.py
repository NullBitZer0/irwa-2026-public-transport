"""
Travel-Mode Filtering Regression Tests
Member 2 — Planning Agent retrieval

The mode filter used to run *after* the candidate lists were truncated to top_k,
so trains filled the ranked lists and a bus query was left with nothing to show.
The unfiltered fallback then handed the user trains instead of buses.

These tests pin the behaviour down at both the retriever and the HTTP endpoint.

Run:
    pytest evaluation/test_retrieval_mode.py -v
"""

from __future__ import annotations

import os
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.planner.hybrid_retriever import (  # noqa: E402
    HybridTransitRetriever,
    _station_key,
)
from src.planner.server import app as planner_app  # noqa: E402

RAIL_PROVIDERS = {"SLR"}


def _is_bus(route: dict) -> bool:
    return route.get("provider") not in RAIL_PROVIDERS


def _is_train(route: dict) -> bool:
    return route.get("provider") in RAIL_PROVIDERS


@pytest.fixture(scope="module")
def retriever() -> HybridTransitRetriever:
    return HybridTransitRetriever()


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(planner_app)


def _plan(client: TestClient, query: str, travel_mode: str = "ANY") -> list[dict]:
    res = client.post(
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
    return res.json()["data"]["route_options"]


# ── Retriever level ──────────────────────────────────────────────────────────

def test_bus_mode_excludes_trains(retriever: HybridTransitRetriever) -> None:
    """Regression: trains previously crowded buses out of the top_k lists."""
    results = retriever.retrieve_candidates("Bus from Colombo to Galle", mode="BUS", top_k=3)
    assert results, "expected bus results for a bus query"
    assert all(_is_bus(r) for r in results), [r["route_id"] for r in results]


def test_train_mode_excludes_buses(retriever: HybridTransitRetriever) -> None:
    """A train query must not surface SLTB / private-highway coaches."""
    results = retriever.retrieve_candidates(
        "Express train from Colombo Fort to Kandy", mode="TRAIN", top_k=3
    )
    assert results
    assert all(_is_train(r) for r in results), [r["route_id"] for r in results]


def test_any_mode_may_return_both(retriever: HybridTransitRetriever) -> None:
    """Without a mode the pool is unrestricted, so both providers may appear."""
    results = retriever.retrieve_candidates("Colombo Kandy", mode="ANY", top_k=10)
    assert results
    providers = {r.get("provider") for r in results}
    assert providers & RAIL_PROVIDERS, "expected at least one train"


def test_fallback_respects_mode(retriever: HybridTransitRetriever) -> None:
    """No lexical overlap must still fall back inside the requested mode."""
    results = retriever.retrieve_candidates("zzz qqq xxx", mode="BUS", top_k=3)
    assert results, "fallback must still return something"
    assert all(_is_bus(r) for r in results), [r["route_id"] for r in results]


def test_singlish_bus_query_returns_bus_at_rank_one(
    retriever: HybridTransitRetriever,
) -> None:
    """'highway bus ekak' ranks the Makumbura → Galle coach first."""
    results = retriever.retrieve_candidates(
        "Makumbura idala Galle yanna highway bus ekak", mode="BUS", top_k=5
    )
    assert results[0]["route_id"] == "BUS-EX1-32", [r["route_id"] for r in results]


# ── Endpoint level ───────────────────────────────────────────────────────────

def test_endpoint_uses_parsed_mode_when_payload_is_any(client: TestClient) -> None:
    """
    The Orchestrator always sends travel_mode="ANY", so the planner must fall back
    to the mode its NLP parser detected from the query itself.
    """
    routes = _plan(client, "Highway bus from Makumbura to Galle", travel_mode="ANY")
    assert routes, "expected routes"
    assert all(_is_bus(r) for r in routes), [r["route_id"] for r in routes]


def test_endpoint_singlish_bus_query_end_to_end(client: TestClient) -> None:
    routes = _plan(client, "Galle yanna bus ekak thiyeda?", travel_mode="ANY")
    assert routes
    assert all(_is_bus(r) for r in routes), [r["route_id"] for r in routes]


def test_endpoint_singlish_train_query_end_to_end(client: TestClient) -> None:
    routes = _plan(
        client, "Heta ude Colombo indan Kandy yanna express train ekak", travel_mode="ANY"
    )
    assert routes
    assert all(_is_train(r) for r in routes), [r["route_id"] for r in routes]
    assert routes[0]["route_id"] == "TRAIN-1001", [r["route_id"] for r in routes]


def test_endpoint_explicit_mode_wins_over_parsed(client: TestClient) -> None:
    """An explicit caller-supplied mode must not be overridden by the parser."""
    routes = _plan(
        client, "Express train from Colombo Fort to Kandy", travel_mode="BUS"
    )
    assert all(_is_bus(r) for r in routes), [r["route_id"] for r in routes]


# ── Direction correctness ────────────────────────────────────────────────────
#
# The stub scores on token overlap, so without an explicit direction check it
# answers "Jaffna → Colombo" with "Colombo Fort → Jaffna". Schedules are
# directional, so a reversed service must never be presented as an answer.

def test_direction_filter_excludes_reversed_service(
    retriever: HybridTransitRetriever,
) -> None:
    """
    Nothing originates at Badulla, so Badulla → Colombo has no direct service.

    Trains do run the opposite way, and they must not be offered as the answer.
    """
    results = retriever.retrieve_candidates(
        "Badulla idala Colombo yanna train ekak",
        origin="Badulla",
        destination="Colombo Fort",
        top_k=5,
    )
    assert results == [], [r["route_id"] for r in results]


def test_direction_filter_keeps_matching_service(
    retriever: HybridTransitRetriever,
) -> None:
    """The outbound direction is a real service and must be returned."""
    results = retriever.retrieve_candidates(
        "Colombo indan Jaffna yanna train ekak",
        origin="Colombo Fort",
        destination="Jaffna",
        top_k=5,
    )
    assert results, "expected the Colombo Fort → Jaffna service"
    # Every result must run Colombo → Jaffna, never the reverse.
    assert all(
        _station_key(r["origin"]) == "colombo" and _station_key(r["destination"]) == "jaffna"
        for r in results
    ), [(r["origin"], r["destination"]) for r in results]


def test_no_service_in_requested_direction_returns_nothing(
    retriever: HybridTransitRetriever,
) -> None:
    """
    No coach runs Colombo → Galle, so nothing may be offered. Note that the
    fixtures have no Galle → Colombo coach either, so there is no reverse
    direction to point at and the caller falls back to a generic message.
    """
    results = retriever.retrieve_candidates(
        "Bus from Colombo to Galle",
        origin="Colombo Fort",
        destination="Galle",
        mode="BUS",
        top_k=5,
    )
    assert results == [], [r["route_id"] for r in results]
    assert (
        retriever.reverse_direction_options("Colombo Fort", "Galle", mode="BUS") == []
    )


def test_adjacent_facilities_still_match(retriever: HybridTransitRetriever) -> None:
    """"Colombo Fort" must still match buses from Colombo Bastian Mawatha."""
    results = retriever.retrieve_candidates(
        "Colombo indan Jaffna yanna bas ekak",
        origin="Colombo Fort",
        destination="Jaffna",
        mode="BUS",
        top_k=5,
    )
    assert results, "expected a Colombo → Jaffna coach"
    assert all(_is_bus(r) for r in results)


def test_reverse_direction_options_explains_the_gap(
    retriever: HybridTransitRetriever,
) -> None:
    """The reverse-direction lookup powers the 'no service that way' message."""
    reverse = retriever.reverse_direction_options("Jaffna", "Colombo Fort", mode="TRAIN")
    ids = [r["route_id"] for r in reverse]
    assert "TRAIN-1006" in ids, ids


def test_direction_filter_respects_mode(retriever: HybridTransitRetriever) -> None:
    """A bus request must not be satisfied by a reversed train."""
    results = retriever.retrieve_candidates(
        "Jaffna idala Colombo yanna",
        origin="Jaffna",
        destination="Colombo Fort",
        mode="BUS",
        top_k=5,
    )
    assert results == []


def test_endpoint_reports_direction_note(client: TestClient) -> None:
    """
    With no service in the requested direction — and nothing to connect with — the
    planner must say so and name the services that run the other way.
    """
    res = client.post(
        "/mcp/plan_route",
        json={
            "origin": "",
            "destination": "",
            "travel_mode": "ANY",
            "date_str": "TODAY",
            "raw_query": "Badulla idala Colombo yanna train ekak",
        },
    )
    assert res.status_code == 200, res.text
    data = res.json()["data"]
    assert data["route_options"] == [], [r["route_id"] for r in data["route_options"]]
    assert data["connections"] == [], data["connections"]
    note = data["direction_note"]
    assert note, "expected a direction note explaining there is no such service"
    assert "Colombo Fort" in note, note
