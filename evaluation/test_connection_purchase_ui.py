"""A connection has to be purchasable from the UI, as two tickets."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from evaluation.conftest import sign_in
from src.orchestrator import server as orch
from src.planner.connection_planner import find_connections
from src.planner.hybrid_retriever import HybridTransitRetriever


@pytest.fixture
def client() -> TestClient:
    return sign_in(TestClient(orch.app))


def _schedules() -> list[dict]:
    return HybridTransitRetriever().schedules


class TestThePlannerSaysSo:
    def test_a_priced_connection_is_bookable_as_a_journey(self) -> None:
        """
        The planner is the authority on whether a journey can be sold.

        "Two tickets" is not a reason to refuse the sale — it is the shape of the
        sale. This is the flag the UI's "Book both tickets" button keys off.
        """
        bookable = [
            c
            for c in find_connections(_schedules(), "Jaffna", "Matara", max_results=10)
            if c["bookable_as_connection"]
        ]
        assert bookable, "no priced connection found to book"

    def test_it_is_still_not_bookable_as_one_ticket(self) -> None:
        """
        The distinction that stays: `bookable` False means no single seat hold.

        A UI that bought a connection as one ticket would issue a ticket for a
        service the traveller never boards.
        """
        for connection in find_connections(_schedules(), "Jaffna", "Matara", max_results=5):
            assert connection["bookable"] is False

    def test_an_unpriced_connection_cannot_be_bought(self) -> None:
        """
        A modelled leg has no published fare, so there is no charge to approve.

        The traveller is asked to approve a price, so there must be one.
        """
        connections = find_connections(
            _schedules(), "Kurunegala", "Matara", max_results=20
        )
        unpriced = [c for c in connections if not c["fare_known"]]
        assert unpriced, "expected a connection with an unpriced leg"
        for connection in unpriced:
            assert connection["bookable_as_connection"] is False


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

CONNECTION = {
    "route_id": "CONN-UI",
    "service_name": "A → B",
    "transfer_station": "C",
    "base_fare_lkr": 600.0,
    "fare_known": True,
    # As offered by the planner: not one seat hold, but purchasable as a journey.
    "bookable": False,
    "bookable_as_connection": True,
    "legs": [LEG_ONE, LEG_TWO],
}


@pytest.fixture
def stubbed(monkeypatch: pytest.MonkeyPatch) -> dict:
    """Stubs the three sub-agent calls a connection purchase makes."""
    settled: list[str] = []

    async def fetch_fare(route_id: str) -> float | None:
        return {LEG_ONE["route_id"]: 300.0, LEG_TWO["route_id"]: 300.0}.get(route_id)

    async def request_hitl_token(**kwargs):
        return f"tok-{kwargs['route_id']}"

    async def begin_booking(payload):
        class Response:
            data = {
                "transaction": {
                    "transaction_id": f"TXN-{payload.route_id}",
                    "amount_lkr": (payload.fare_lkr or 0) * payload.seat_count,
                }
            }

        return Response()

    async def settle_booking(transaction_id, **_):
        settled.append(transaction_id)

        # Mirrors the real Booking Agent: confirmation stamps the reference onto
        # the transaction, so the ticket carries it.
        class Response:
            data = {
                "booking_reference": f"REF-{transaction_id}",
                "receipt": {"receipt_id": f"PAY-{transaction_id}", "amount_lkr": 300.0},
                "ticket": {
                    "transaction_id": transaction_id,
                    "booking_reference": f"REF-{transaction_id}",
                    "route_id": transaction_id,
                    "seat_count": 1,
                },
                "purchase": {"transaction_id": transaction_id},
            }

        return Response()

    monkeypatch.setattr(orch._bridge, "fetch_fare", fetch_fare)
    monkeypatch.setattr(orch._bridge, "request_hitl_token", request_hitl_token)
    monkeypatch.setattr(orch._bridge, "begin_booking", begin_booking)
    monkeypatch.setattr(orch._bridge, "settle_booking", settle_booking)
    return {"settled": settled}


class TestOneClickBuysBothTickets:
    def test_the_journey_reaches_two_paid_tickets(
        self, client: TestClient, stubbed: dict
    ) -> None:
        """
        The whole point: one button, two tickets, one payment.

        Clicking "Book both tickets" sends the connection's id; from there the
        traveller approves each leg and pays once for the pair.
        """
        orch.SLOTS.propose("sess-ui", [CONNECTION])

        gate = client.post(
            "/chat",
            json={
                "query": "Book route CONN-UI",
                "session_id": "sess-ui",
                "selected_route_id": "CONN-UI",
            },
        ).json()

        # Approved once per leg, and told plainly what they are approving.
        assert len(gate["hitl_tokens"]) == 2, gate["response"]
        assert "2 tickets" in gate["response"]
        assert "Hold Both Seats" in gate["response"]

        held = client.post(
            "/chat",
            json={
                "query": "YES confirm CONN-UI",
                "session_id": "sess-ui",
                "selected_route_id": "CONN-UI",
                "hitl_tokens": gate["hitl_tokens"],
            },
        ).json()

        assert held["booking_status"] == "AWAITING_PAYMENT"
        assert len(held["transaction_ids"]) == 2
        assert held["amount_lkr"] == 600.0

        paid = client.post(
            "/payment",
            json={
                "transaction_id": held["transaction_ids"][0],
                "transaction_ids": held["transaction_ids"],
                "card_last4": "4242",
            },
        ).json()

        assert paid["status"] == "PAID"
        assert len(stubbed["settled"]) == 2, stubbed["settled"]
        assert len(paid["tickets"]) == 2
        assert len(paid["receipts"]) == 2
        assert paid["tickets"][0]["booking_reference"] != paid["tickets"][1]["booking_reference"]
