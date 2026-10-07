"""Orchestrator-level contract for booking a journey with a change."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from evaluation.conftest import sign_in
from src.orchestrator import server as orch


@pytest.fixture
def client() -> TestClient:
    """
    A signed-in Orchestrator client.

    The booking and planning agents run as separate services and are stubbed per
    test, so what this file tests is the Orchestrator's own contract: one token
    per leg, two holds, one payment, two tickets.
    """
    return sign_in(TestClient(orch.app))


def _connection(legs: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "route_id": "CONN-TEST-1-2",
        "service_name": "A → B (change at C)",
        "provider": "SLTB",
        "origin": "A",
        "destination": "B",
        "departure_time": "06:00",
        "arrival_time": "10:00",
        "base_fare_lkr": 600.0,
        "transit_type": "EXPRESS_BUS",
        "transfer_station": "C",
        "duration_minutes": 240,
        "is_connection": True,
        "legs": legs,
    }


LEG_ONE = {
    "route_id": "SLTB-1-A-C-0600",
    "service_name": "A → C",
    "provider": "SLTB",
    "mode": "BUS",
    "origin": "A",
    "destination": "C",
    "departure_time": "06:00",
    "arrival_time": "08:00",
    "base_fare_lkr": 300.0,
}

LEG_TWO = {
    "route_id": "SLTB-2-C-B-0830",
    "service_name": "C → B",
    "provider": "SLTB",
    "mode": "BUS",
    "origin": "C",
    "destination": "B",
    "departure_time": "08:30",
    "arrival_time": "10:00",
    "base_fare_lkr": 300.0,
}

FARES = {"SLTB-1-A-C-0600": 300.0, "SLTB-2-C-B-0830": 300.0}


@pytest.fixture
def stubbed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stands in for the three sub-agent calls a connection booking makes."""
    issued: list[str] = []

    async def fetch_fare(route_id: str) -> float | None:
        return FARES.get(route_id)

    async def request_hitl_token(**kwargs: Any) -> str:
        token = f"tok-{len(issued)}-{kwargs['route_id']}"
        issued.append(token)
        return token

    async def begin_booking(payload: Any) -> Any:
        class Response:
            data = {
                "transaction": {
                    "transaction_id": f"TXN-{payload.route_id}",
                    "amount_lkr": (payload.fare_lkr or 0) * payload.seat_count,
                }
            }

        return Response()

    monkeypatch.setattr(orch._bridge, "fetch_fare", fetch_fare)
    monkeypatch.setattr(orch._bridge, "request_hitl_token", request_hitl_token)
    monkeypatch.setattr(orch._bridge, "begin_booking", begin_booking)


def test_the_gate_issues_one_token_per_leg(client: TestClient, stubbed: None) -> None:
    """
    A change is two services, so it is approved twice.

    One token for the pair would let a traveller approve the cheap leg and spend
    that approval on the expensive one.
    """
    orch.SLOTS.propose("sess-conn", [_connection([LEG_ONE, LEG_TWO])])

    first = client.post(
        "/chat",
        json={
            "query": "Book route CONN-TEST-1-2",
            "session_id": "sess-conn",
            "selected_route_id": "CONN-TEST-1-2",
        },
    ).json()

    assert len(first["hitl_tokens"]) == 2, first["hitl_tokens"]
    assert "2 tickets" in first["response"], first["response"]
    # The change point is named: the traveller needs to know where to change.
    assert "**C**" in first["response"], first["response"]


def test_confirming_holds_both_legs(client: TestClient, stubbed: None) -> None:
    """Approving the itinerary holds every leg of it, and quotes the whole fare."""
    orch.SLOTS.propose("sess-conn", [_connection([LEG_ONE, LEG_TWO])])

    gate = client.post(
        "/chat",
        json={
            "query": "Book route CONN-TEST-1-2",
            "session_id": "sess-conn",
            "selected_route_id": "CONN-TEST-1-2",
        },
    ).json()

    held = client.post(
        "/chat",
        json={
            "query": "YES confirm CONN-TEST-1-2",
            "session_id": "sess-conn",
            "selected_route_id": "CONN-TEST-1-2",
            "hitl_tokens": gate["hitl_tokens"],
        },
    ).json()

    assert held["booking_status"] == "AWAITING_PAYMENT", held["response"]
    assert held["transaction_ids"] == ["TXN-SLTB-1-A-C-0600", "TXN-SLTB-2-C-B-0830"]
    # 300 + 300, one seat each. Under-quoting here is what a traveller finds out
    # at the payment step.
    assert held["amount_lkr"] == 600.0


def test_an_unpriced_leg_stops_the_booking(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """
    A leg with no published fare must not be asked for approval.

    The traveller is being asked to approve a charge, and there is nothing to
    approve — and nothing may be invented to fill the gap.
    """
    orch.SLOTS.propose("sess-conn", [_connection([LEG_ONE, LEG_TWO])])

    async def fetch_fare(route_id: str) -> float | None:
        return FARES.get(route_id) if route_id == LEG_ONE["route_id"] else None

    monkeypatch.setattr(orch._bridge, "fetch_fare", fetch_fare)

    gate = client.post(
        "/chat",
        json={
            "query": "Book route CONN-TEST-1-2",
            "session_id": "sess-conn",
            "selected_route_id": "CONN-TEST-1-2",
        },
    ).json()

    assert gate["hitl_tokens"] == []
    assert "no published fare" in gate["response"], gate["response"]
    assert gate["booking_status"] == "ERROR"


def test_partially_confirmed_is_refused(client: TestClient, stubbed: None) -> None:
    """
    Half an approval is not an approval.

    Booking one leg of a two-leg journey would leave the traveller with a ticket
    for the service they cannot board.
    """
    orch.SLOTS.propose("sess-conn", [_connection([LEG_ONE, LEG_TWO])])

    gate = client.post(
        "/chat",
        json={
            "query": "Book route CONN-TEST-1-2",
            "session_id": "sess-conn",
            "selected_route_id": "CONN-TEST-1-2",
        },
    ).json()

    result = client.post(
        "/chat",
        json={
            "query": "YES confirm CONN-TEST-1-2",
            "session_id": "sess-conn",
            "selected_route_id": "CONN-TEST-1-2",
            "hitl_tokens": gate["hitl_tokens"][:1],
        },
    ).json()

    assert result["booking_status"] == "ERROR", result["response"]
    assert result["transaction_ids"] == []
    assert "every leg" in result["response"], result["response"]


def test_payment_settles_every_leg(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """One payment, two tickets, one conversation archived."""
    settled: list[str] = []

    async def settle_booking(transaction_id: str, **_: Any) -> Any:
        settled.append(transaction_id)

        class Response:
            data = {
                "booking_reference": f"REF-{transaction_id}",
                "receipt": {"receipt_id": f"PAY-{transaction_id}", "amount_lkr": 300.0},
                "ticket": {"route_id": transaction_id, "seat_count": 1},
                "purchase": {"transaction_id": transaction_id},
            }

        return Response()

    monkeypatch.setattr(orch._bridge, "settle_booking", settle_booking)

    result = client.post(
        "/payment",
        json={
            "transaction_id": "TXN-A",
            "transaction_ids": ["TXN-A", "TXN-B"],
            "card_last4": "4242",
        },
    ).json()

    assert result["status"] == "PAID"
    assert settled == ["TXN-A", "TXN-B"]
    assert len(result["tickets"]) == 2
    assert len(result["receipts"]) == 2


def test_payment_rejects_a_card_number(client: TestClient) -> None:
    """Only the last four digits are ever accepted."""
    response = client.post(
        "/payment",
        json={"transaction_id": "TXN-A", "card_last4": "4111111111111111"},
    )
    assert response.status_code == 400
    assert "4 digits" in response.json()["detail"]


def test_payment_needs_something_to_pay_for(client: TestClient) -> None:
    """An empty payment is a client bug, not a free ticket."""
    response = client.post("/payment", json={"card_last4": "4242"})
    assert response.status_code == 400
    assert "No booking" in response.json()["detail"]


def test_the_legs_come_from_what_was_offered(client: TestClient, stubbed: None) -> None:
    """
    The gate prices the itinerary on screen, not a fresh search.

    A re-derivation could find a different service by the time the traveller
    answers, and they would be approving a price for something they were never
    shown.
    """
    orch.SLOTS.propose("sess-conn", [_connection([LEG_ONE, LEG_TWO])])
    # A later search in the same conversation replaces the proposals wholesale.
    # Its second leg is a service we have no published fare for.
    orch.SLOTS.propose(
        "sess-conn",
        [_connection([LEG_ONE, {**LEG_TWO, "route_id": "SLTB-9-C-B-0900"}])],
    )

    gate = client.post(
        "/chat",
        json={
            "query": "Book route CONN-TEST-1-2",
            "session_id": "sess-conn",
            "selected_route_id": "CONN-TEST-1-2",
        },
    ).json()

    assert gate["hitl_tokens"] == []
    assert "SLTB-9-C-B-0900" in gate["response"], gate["response"]
    assert "no published fare" in gate["response"], gate["response"]


def test_a_single_service_booking_is_unchanged(client: TestClient, stubbed: None) -> None:
    """One service, one token, one hold: the existing flow must not change."""
    single = {
        "route_id": "SLTB-1-A-C-0600",
        "service_name": "A → C",
        "provider": "SLTB",
        "origin": "A",
        "destination": "C",
        "departure_time": "06:00",
        "arrival_time": "08:00",
        "base_fare_lkr": 300.0,
        "transit_type": "EXPRESS_BUS",
    }
    orch.SLOTS.propose("sess-single", [single])

    gate = client.post(
        "/chat",
        json={
            "query": "Book route SLTB-1-A-C-0600",
            "session_id": "sess-single",
            "selected_route_id": "SLTB-1-A-C-0600",
        },
    ).json()

    assert gate["hitl_tokens"] == []
    assert gate["hitl_token"], "a single booking still returns one token"
    assert "2 tickets" not in gate["response"]

    held = client.post(
        "/chat",
        json={
            "query": "YES confirm SLTB-1-A-C-0600",
            "session_id": "sess-single",
            "selected_route_id": "SLTB-1-A-C-0600",
            "hitl_token": gate["hitl_token"],
        },
    ).json()

    assert held["booking_status"] == "AWAITING_PAYMENT"
    assert held["amount_lkr"] == 300.0


def test_a_connection_id_is_not_priced_as_a_route(
    client: TestClient, stubbed: None
) -> None:
    """
    Booking a connection by its own id must not silently fall through.

    The legs were proposed, so this asserts the refusal: there is no route named
    CONN-… that can be priced or held on its own.
    """
    response = client.post(
        "/chat",
        json={
            "query": "Book route CONN-UNKNOWN-1-2",
            "session_id": "sess-none",
            "selected_route_id": "CONN-UNKNOWN-1-2",
        },
    ).json()

    assert response["booking_status"] != "AWAITING_PAYMENT", response["response"]
    assert "no published fare" in response["response"], response["response"]
