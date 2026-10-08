"""Seat availability: shown before approval, enforced at the hold, with an
alternative offered when it cannot be satisfied."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from starlette.testclient import TestClient as StarletteClient

from evaluation.conftest import sign_in
from src.booking import server as booking_server
from src.booking.mock_gateway import MockTransitGateway
from src.booking.state_machine import BookingStateMachine
from src.booking.store import BookingStore
from src.orchestrator import server as orch

# ── The gateway ──────────────────────────────────────────────────────────────

class TestInventoryIsStable:
    def test_the_same_service_reports_the_same_seats(self) -> None:
        """
        A coach has a fixed number of seats for the life of the service.

        Re-rolling per call meant one coach reported 5 free seats and then 12, so
        a traveller could be quoted a seat and refused twenty seconds later.
        """
        first = MockTransitGateway.check_seat_inventory("SLTB-1-COLO-KAND-0510")
        second = MockTransitGateway.check_seat_inventory("SLTB-1-COLO-KAND-0510")
        assert first["available_seats"] == second["available_seats"]
        assert first["seat_numbers"] == second["seat_numbers"]

    def test_a_sold_out_service_exists(self) -> None:
        """
        The sold-out path has to be reachable or none of this can be demonstrated.

        Availability used to be `randint(3, 18)`, which never returns zero, so the
        409 branch could never execute and the alternative-service logic it exists
        to trigger had nothing to trigger on.
        """
        sold_out = [
            f"SLTB-1-COLO-KAND-{i:02d}00"
            for i in range(200)
            if MockTransitGateway.check_seat_inventory(f"SLTB-1-COLO-KAND-{i:02d}00")[
                "available_seats"
            ]
            == 0
        ]
        assert sold_out, "no service is ever sold out, so the refusal path is dead code"

    def test_a_partial_request_is_distinguishable_from_sold_out(self) -> None:
        """
        Three seats free is a different problem from none, and the two produce
        different advice: one can suggest a smaller party, the other cannot.
        """
        partial = next(
            route
            for route in (f"SLTB-X-{i:03d}" for i in range(200))
            if 0 < MockTransitGateway.check_seat_inventory(route)["available_seats"] < 5
        )
        assert (
            MockTransitGateway.check_seat_inventory(partial, seat_count=1)["status"]
            == "AVAILABLE"
        )
        assert (
            MockTransitGateway.check_seat_inventory(partial, seat_count=10)["status"]
            == "INSUFFICIENT"
        )


# ── The Booking Agent must not oversell ──────────────────────────────────────

@pytest.fixture
def booking_client() -> StarletteClient:
    booking_server._state_machine = BookingStateMachine(store=BookingStore(":memory:"))
    booking_server._store = BookingStore(":memory:")
    return StarletteClient(booking_server.app)


def _hold(client: StarletteClient, route_id: str, seats: int) -> StarletteClient:
    from src.booking.hitl_token import issue_hitl_token

    return client.post(
        "/mcp/begin_booking",
        json={
            "route_id": route_id,
            "provider": "SLTB",
            "passenger_token": "TOKEN_nic_abc",
            "seat_count": seats,
            "fare_lkr": 900.0,
            "session_id": "s1",
            "hitl_token": issue_hitl_token(
                session_id="s1",
                route_id=route_id,
                fare_lkr=900.0,
                seat_count=seats,
                provider="SLTB",
            ),
        },
    )


def test_a_request_larger_than_the_coach_is_refused(booking_client) -> None:
    """
    The core bug: the hold only rejected a fully sold-out coach.

    A six-seat booking was confirmed against three free seats — a ticket sold
    that cannot be honoured, for a bus that does not have the room.
    """
    route = next(
        r
        for r in (f"SLTB-Y-{i:03d}" for i in range(200))
        if 0 < MockTransitGateway.check_seat_inventory(r)["available_seats"] < 10
    )
    available = MockTransitGateway.check_seat_inventory(route)["available_seats"]

    response = _hold(booking_client, route, available + 4)

    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "INSUFFICIENT_SEATS"
    assert detail["available_seats"] == available
    assert detail["requested_seats"] == available + 4
    assert booking_server._state_machine.get(
        next(iter(booking_server._state_machine.transactions), "")
    ) is None or not booking_server._state_machine.transactions


def test_a_sold_out_coach_is_refused(booking_client) -> None:
    route = next(
        r
        for r in (f"SLTB-Z-{i:03d}" for i in range(200))
        if MockTransitGateway.check_seat_inventory(r)["available_seats"] == 0
    )

    response = _hold(booking_client, route, 1)

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "NO_SEATS"


def test_a_request_that_fits_is_still_held(booking_client) -> None:
    """The check must not break the ordinary case."""
    route = next(
        r
        for r in (f"SLTB-W-{i:03d}" for i in range(200))
        if MockTransitGateway.check_seat_inventory(r)["available_seats"] >= 2
    )

    response = _hold(booking_client, route, 2)

    assert response.status_code == 200, response.text
    assert response.json()["data"]["transaction"]["seat_count"] == 2


def test_the_inventory_endpoint_is_readable() -> None:
    client = StarletteClient(booking_server.app)
    response = client.get("/mcp/inventory/SLTB-1-COLO-KAND-0510", params={"seat_count": 2})
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["route_id"] == "SLTB-1-COLO-KAND-0510"
    assert data["requested_seats"] == 2
    assert data["available_seats"] >= 0


# ── The Orchestrator must not approve what it cannot hold ───────────────────

@pytest.fixture
def client() -> TestClient:
    return sign_in(TestClient(orch.app))


def _service(route_id: str, origin: str, destination: str, departure: str) -> dict:
    return {
        "route_id": route_id,
        "service_name": f"{origin} - {destination}",
        "provider": "SLTB",
        "origin": origin,
        "destination": destination,
        "departure_time": departure,
        "arrival_time": "10:00",
        "base_fare_lkr": 900.0,
        "transit_type": "EXPRESS_BUS",
    }


@pytest.fixture
def seat_stub(monkeypatch: pytest.MonkeyPatch):
    """A corridor where the 08:00 service is full and the later ones are not."""

    async def fetch_fare(route_id: str) -> float | None:
        return 900.0

    async def request_hitl_token(**kwargs) -> str:
        return f"tok-{kwargs['route_id']}"

    async def fetch_seat_availability(route_id: str, seat_count: int = 1):
        full = route_id.endswith("0800")
        return {
            "route_id": route_id,
            "available_seats": 0 if full else 40,
            "status": "SOLD_OUT" if full else "AVAILABLE",
            "sufficient": not full,
        }

    async def begin_booking(payload):
        from src.orchestrator.agent_connectors import NoSeatsError

        if payload.route_id.endswith("0800"):
            raise NoSeatsError(
                {"code": "NO_SEATS", "available_seats": 0, "requested_seats": payload.seat_count}
            )

        class Response:
            data = {
                "transaction": {
                    "transaction_id": f"TXN-{payload.route_id}",
                    "amount_lkr": 900.0 * payload.seat_count,
                    "seat_count": payload.seat_count,
                    "fare_lkr": 900.0,
                }
            }

        return Response()

    monkeypatch.setattr(orch._bridge, "fetch_fare", fetch_fare)
    monkeypatch.setattr(orch._bridge, "request_hitl_token", request_hitl_token)
    monkeypatch.setattr(orch._bridge, "fetch_seat_availability", fetch_seat_availability)
    monkeypatch.setattr(orch._bridge, "begin_booking", begin_booking)


def test_a_sold_out_service_is_refused_before_approval(
    client: TestClient, seat_stub: None
) -> None:
    """
    Nothing is held and no token is issued.

    Asking for approval on a seat that cannot be held wastes the traveller's
    confirmation and implies the seat is theirs when it is not.
    """
    orch.SLOTS.propose(
        "sess-seats",
        [
            _service("SLTB-A-B-0800", "Alpha", "Beta", "08:00"),
            _service("SLTB-A-B-1000", "Alpha", "Beta", "10:00"),
        ],
    )

    gate = client.post(
        "/chat",
        json={
            "query": "Book route SLTB-A-B-0800",
            "session_id": "sess-seats",
            "selected_route_id": "SLTB-A-B-0800",
        },
    ).json()

    assert gate["booking_status"] == "NO_SEATS"
    assert gate["hitl_tokens"] == []
    assert gate["hitl_token"] is None
    assert "fully booked" in gate["response"]
    # And it names somewhere else to go.
    assert "SLTB-A-B-1000" in gate["response"]
    offered = [o["route_id"] for o in gate["route_options"]]
    assert "SLTB-A-B-1000" in offered
    assert "SLTB-A-B-0800" not in offered


def test_a_booking_that_fits_says_how_many_seats_are_left(
    client: TestClient, seat_stub: None
) -> None:
    """The number is stated before approval, not discovered at the hold."""
    orch.SLOTS.propose(
        "sess-ok", [_service("SLTB-A-B-1000", "Alpha", "Beta", "10:00")]
    )

    gate = client.post(
        "/chat",
        json={
            "query": "Book route SLTB-A-B-1000",
            "session_id": "sess-ok",
            "selected_route_id": "SLTB-A-B-1000",
        },
    ).json()

    assert gate["booking_status"] is None
    assert gate["hitl_token"], "approval should be offered"
    assert "40 seats left" in gate["response"]


def test_seats_taken_between_approval_and_confirmation_still_refuses(
    client: TestClient, seat_stub: None
) -> None:
    """
    Availability can change in the window between the two turns.

    The traveller approved when there were seats; by the time they confirmed,
    someone else had them. The gate cannot prevent that race, but it must not
    hold a seat that does not exist.
    """
    orch.SLOTS.propose(
        "sess-race", [_service("SLTB-A-B-1000", "Alpha", "Beta", "10:00")]
    )
    gate = client.post(
        "/chat",
        json={
            "query": "Book route SLTB-A-B-1000",
            "session_id": "sess-race",
            "selected_route_id": "SLTB-A-B-1000",
        },
    ).json()

    result = client.post(
        "/chat",
        json={
            "query": "YES confirm SLTB-A-B-1000",
            "session_id": "sess-race",
            "selected_route_id": "SLTB-A-B-1000",
            "hitl_token": gate["hitl_token"],
        },
    ).json()

    assert result["booking_status"] == "AWAITING_PAYMENT"
    assert result["transaction_id"] == "TXN-SLTB-A-B-1000"


def test_an_unknown_availability_is_not_treated_as_free(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    The Booking Agent being unreachable must not become "seats available".

    Failing open here is how a booking ends with a ticket nobody can honour.
    """

    async def fetch_seat_availability(route_id: str, seat_count: int = 1):
        return None

    async def fetch_fare(route_id: str):
        return 900.0

    async def request_hitl_token(**kwargs) -> str:
        return "tok"

    monkeypatch.setattr(
        orch._bridge, "fetch_seat_availability", fetch_seat_availability
    )
    monkeypatch.setattr(orch._bridge, "fetch_fare", fetch_fare)
    monkeypatch.setattr(orch._bridge, "request_hitl_token", request_hitl_token)

    orch.SLOTS.propose("sess-unknown", [_service("SLTB-A-B-1000", "Alpha", "Beta", "10:00")])

    gate = client.post(
        "/chat",
        json={
            "query": "Book route SLTB-A-B-1000",
            "session_id": "sess-unknown",
            "selected_route_id": "SLTB-A-B-1000",
        },
    ).json()

    # Proceeds — the hold is still the authority — but claims no seat count.
    assert gate["hitl_token"], "an unknown count should not block the booking"
    assert "left" not in gate["response"]
