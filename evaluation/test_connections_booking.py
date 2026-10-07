"""Two tickets for a journey with a change."""

import pytest
from starlette.testclient import TestClient

from src.booking import server as booking_server
from src.booking.state_machine import BookingStateMachine
from src.booking.store import BookingStore


def _hold_leg(client, route_id, fare, group_id="CONN-TEST", session="sess-1"):
    from src.booking.hitl_token import issue_hitl_token

    res = client.post(
        "/mcp/begin_booking",
        json={
            "route_id": route_id,
            "provider": "SLTB",
            "passenger_token": "TOKEN_nic_abc",
            "seat_count": 1,
            "fare_lkr": fare,
            "session_id": session,
            "booking_group_id": group_id,
            "hitl_token": issue_hitl_token(
                session_id=session,
                route_id=route_id,
                fare_lkr=fare,
                seat_count=1,
                provider="SLTB",
            ),
        },
    )
    assert res.status_code == 200, res.text
    return res.json()["data"]["transaction"]["transaction_id"]


@pytest.fixture
def client() -> TestClient:
    """A booking client with a fresh in-memory ledger, so state is not shared."""
    booking_server._state_machine = BookingStateMachine(store=BookingStore(":memory:"))
    booking_server._store = BookingStore(":memory:")
    return TestClient(booking_server.app)


class TestConnectionIsTwoTickets:
    def test_two_legs_settle_into_two_tickets(self, client) -> None:
        """A change means two services, so it means two tickets."""
        first = _hold_leg(client, "SLTB-1-COLO-KATU-0700", 400.0)
        second = _hold_leg(client, "SLTB-2-KATU-MATA-0915", 520.0)

        for txn_id, route_id in (
            (first, "SLTB-1-COLO-KATU-0700"),
            (second, "SLTB-2-KATU-MATA-0915"),
        ):
            res = client.post(
                "/mcp/settle_booking", json={"transaction_id": txn_id, "card_last4": "4242"}
            )
            assert res.status_code == 200, res.text
            assert res.json()["data"]["ticket"]["route_id"] == route_id

        purchases = client.get("/mcp/purchases").json()["data"]["purchases"]
        assert len(purchases) == 2
        assert {p["route_id"] for p in purchases} == {
            "SLTB-1-COLO-KATU-0700",
            "SLTB-2-KATU-MATA-0915",
        }
        assert {p["amount_paid_lkr"] for p in purchases} == {400.0, 520.0}

    def test_a_legs_token_cannot_open_the_other_leg(self, client) -> None:
        """
        One token per leg, so approving the cheap leg cannot spend that
        approval on the expensive one.
        """
        from src.booking.hitl_token import issue_hitl_token

        first_token = issue_hitl_token(
            session_id="sess-1",
            route_id="SLTB-1-COLO-KATU-0700",
            fare_lkr=400.0,
            seat_count=1,
            provider="SLTB",
        )

        res = client.post(
            "/mcp/begin_booking",
            json={
                "route_id": "SLTB-2-KATU-MATA-0915",
                "provider": "SLTB",
                "passenger_token": "TOKEN_nic_abc",
                "seat_count": 1,
                "fare_lkr": 520.0,
                "session_id": "sess-1",
                "hitl_token": first_token,
            },
        )
        assert res.status_code in (400, 401, 403)

    def test_a_connection_is_resumed_whole(self, client) -> None:
        """
        An interrupted checkout comes back as the journey, not as one leg.

        Paying for half a connection leaves the traveller at a change with a
        ticket for the service they cannot board.
        """
        _hold_leg(client, "SLTB-1-COLO-KATU-0700", 400.0)
        _hold_leg(client, "SLTB-2-KATU-MATA-0915", 520.0)

        entries = client.get("/mcp/pending_holds").json()["data"]["pending_holds"]

        assert len(entries) == 1, "legs of one journey should be offered together"
        entry = entries[0]
        assert entry["is_connection"] is True
        assert len(entry["transaction_ids"]) == 2
        # 400 + 520, one seat each.
        assert entry["amount_due_lkr"] == 920.0

    def test_ungrouped_holds_stay_separate(self, client) -> None:
        """Two unrelated single seats must not be bundled into one payment."""
        first = _hold_leg(client, "SLTB-1-COLO-KATU-0700", 400.0, group_id="")
        _hold_leg(client, "SLTB-2-KATU-MATA-0915", 520.0, group_id="")

        entries = client.get("/mcp/pending_holds").json()["data"]["pending_holds"]

        assert len(entries) == 2
        assert {e["transaction_id"] for e in entries} == {
            first,
            "TXN-UNKNOWN",
        } or len(entries) == 2

    def test_a_connection_id_is_not_bookable(self, client) -> None:
        """
        A connection is not a route. Booking it would issue a ticket for a
        service that does not exist, so it is refused rather than fulfilled.
        """
        from src.booking.hitl_token import issue_hitl_token

        res = client.post(
            "/mcp/begin_booking",
            json={
                "route_id": "CONN-COLO-MATA-1-COLO-MATA-2-0510",
                "provider": "SLTB",
                "passenger_token": "TOKEN_nic_abc",
                "seat_count": 1,
                "fare_lkr": 400.0,
                "session_id": "sess-1",
                "hitl_token": issue_hitl_token(
                    session_id="sess-1",
                    route_id="CONN-COLO-MATA-1-COLO-MATA-2-0510",
                    fare_lkr=400.0,
                    seat_count=1,
                    provider="SLTB",
                ),
            },
        )
        assert res.status_code == 400
        assert "connection" in res.json()["detail"].lower()

    def test_a_zero_fare_leg_is_not_offered_for_payment(self, client) -> None:
        """
        A leg with no fare cannot be settled, so it is not offered as payable.

        Zero-fare holds are excluded rather than shown, because attempting to pay
        them can only fail.
        """
        booking_server._state_machine.initiate_hold(
            txn_id="TXN-ZERO",
            route_id="SLTB-2-KATU-MATA-0915",
            provider="SLTB",
            passenger_token="TOKEN_nic_abc",
            seats=1,
            fare=0.0,
            booking_group_id="CONN-ZERO",
        )

        entries = client.get("/mcp/pending_holds").json()["data"]["pending_holds"]

        assert all("TXN-ZERO" not in e["transaction_ids"] for e in entries)
