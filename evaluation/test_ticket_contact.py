"""
Ticket contact details and resumable payment holds.

Member 4 — Responsible AI, Commercialization & Media Lead

Two behaviours the purchase history depends on:

- A settled ticket expands to show how to contact the operator that issued it.
  A ticket you cannot get help with is not much use when a service is delayed.
- A booking whose payment was never completed stays reachable. The seat hold
  expires in 10 minutes, so closing the payment portal used to mean losing the
  seat silently; the card in the history panel is now the way back in.

Run:
    pytest evaluation/test_ticket_contact.py -v
"""

from __future__ import annotations

import os
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.booking import server as booking_server  # noqa: E402
from src.booking.hitl_token import issue_hitl_token  # noqa: E402
from src.booking.providers import contact_for  # noqa: E402
from src.booking.state_machine import BookingStateMachine  # noqa: E402
from src.booking.store import BookingStore  # noqa: E402


@pytest.fixture()
def client() -> TestClient:
    """A booking client with a fresh in-memory ledger, so tests do not share state."""
    booking_server._state_machine = BookingStateMachine(store=BookingStore(":memory:"))
    booking_server._store = BookingStore(":memory:")
    return TestClient(booking_server.app)


def _hold(client: TestClient, **overrides) -> str:
    payload = {
        "route_id": "TRAIN-1007",
        "provider": "SLR",
        "passenger_token": "TOKEN_nic_abc",
        "seat_count": 2,
        "fare_lkr": 900.0,
        "user_confirmed": True,
    }
    payload.update(overrides)
    payload["hitl_token"] = issue_hitl_token(
        session_id=payload.get("session_id", "anonymous"),
        route_id=payload["route_id"],
        fare_lkr=payload["fare_lkr"],
        seat_count=payload["seat_count"],
        provider=payload.get("provider", "SLR"),
    )
    res = client.post("/mcp/begin_booking", json=payload)
    assert res.status_code == 200, res.text
    return res.json()["data"]["transaction"]["transaction_id"]


def _holds(client: TestClient) -> list[dict]:
    res = client.get("/mcp/pending_holds")
    assert res.status_code == 200, res.text
    return res.json()["data"]["pending_holds"]


# ── Contact resolution ───────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "provider, route_id, expected_name",
    [
        ("SLR", "TRAIN-1007", "Sri Lanka Railways"),
        ("SLTB", "SLTB-2-COLO-MATA-0930", "Sri Lanka Transport Board"),
        # No provider recorded: fall back to the route id's operator prefix.
        (None, "RM-02-COLOMB-GALLE-1315", "Routemaster"),
        ("slr", "TRAIN-1007", "Sri Lanka Railways"),
    ],
)
def test_contact_resolves_the_operator(provider, route_id, expected_name) -> None:
    contact = contact_for(provider, route_id)

    assert contact["name"] == expected_name
    assert contact["known"] is True
    assert contact["website"].startswith("https://")


def test_unknown_operator_is_reported_not_guessed() -> None:
    """
    An unrecognised operator must not inherit SLR's details.

    A wrong phone number on a ticket is worse than no number: the traveller
    would call the railway about a bus journey.
    """
    contact = contact_for("ZZZ", "ZZZ-1-SOMEWHERE-0900")

    assert contact["known"] is False
    assert contact["name"] is None
    assert contact["customer_care"] is None


def test_every_contact_website_is_https() -> None:
    for code in ("SLR", "SLTB", "RM", "NTC"):
        assert contact_for(code)["website"].startswith("https://")


def test_contact_numbers_are_flagged_as_demo() -> None:
    """
    A placeholder number must be labelled, or it will be dialled.

    The numbers in this table are deliberately fake: a plausible-looking but
    wrong customer-care line on a ticket is worse than none, because the
    traveller trusts it.
    """
    for code in ("SLR", "SLTB", "RM", "NTC"):
        assert contact_for(code)["demo_contact"] is True


def test_placeholder_numbers_are_not_plausible_real_lines() -> None:
    """
    Guard against a "realistic" number creeping back in.

    Sri Lanka has no reserved fictional-number range, so a plausible number is
    simply a number that might belong to someone. The placeholder convention is
    a run of three or more zeros in the subscriber digits, which a real
    customer-care line will not contain.
    """
    for code in ("SLR", "SLTB", "RM", "NTC"):
        number = contact_for(code)["customer_care"]
        if number is None:
            continue
        digits = "".join(ch for ch in number if ch.isdigit())
        assert "000" in digits, (
            f"{code}: {number} has no zero run — a plausible-looking number could "
            f"belong to a real person or desk"
        )


# ── Settled tickets carry contact details ─────────────────────────────────────

def test_settled_ticket_carries_operator_contact(client: TestClient) -> None:
    txn_id = _hold(client)
    settled = client.post(
        "/mcp/settle_booking", json={"transaction_id": txn_id, "card_last4": "4242"}
    ).json()["data"]

    assert settled["purchase"]["provider_contact"]["name"] == "Sri Lanka Railways"

    history = client.get("/mcp/purchases").json()["data"]["purchases"]
    assert history[0]["provider_contact"]["customer_care"]


# ── Unfinished payments stay reachable ────────────────────────────────────────

def test_unpaid_booking_appears_as_awaiting_payment(client: TestClient) -> None:
    txn_id = _hold(client, route_id="SLTB-2-COLO-MATA-0930", provider="SLTB", fare_lkr=950.0)

    entries = _holds(client)

    assert len(entries) == 1
    entry = entries[0]
    assert entry["transaction_id"] == txn_id
    assert entry["state"] == "AWAITING_PAYMENT"
    # 950 per seat × 2 seats. This used to read back as 950, which under-quoted
    # the checkout the sidebar was inviting the traveller to pay.
    assert entry["fare_lkr"] == 950.0
    assert entry["amount_due_lkr"] == 1900.0
    assert entry["seat_count"] == 2
    assert entry["hold_expires_at"]
    assert entry["provider_contact"]["name"] == "Sri Lanka Transport Board"


def test_settled_booking_leaves_the_pending_list(client: TestClient) -> None:
    """A paid ticket is not outstanding, so it must not invite payment again."""
    txn_id = _hold(client)
    client.post("/mcp/settle_booking", json={"transaction_id": txn_id, "card_last4": "4242"})

    assert _holds(client) == []


def test_cancelled_booking_leaves_the_pending_list(client: TestClient) -> None:
    txn_id = _hold(client)
    client.post(f"/mcp/cancel/{txn_id}")

    assert _holds(client) == []


def test_zero_fare_is_not_offered_for_payment(client: TestClient) -> None:
    """
    A zero-fare booking cannot be settled, so offering it would be a dead end.

    begin_booking already refuses a zero fare, so this row is written straight
    to the state machine: the filter in pending_holds is defence in depth for a
    state the endpoint should never have allowed.
    """
    booking_server._state_machine.initiate_hold(
        "TXN-ZERO", "TRAIN-1007", "SLR", "TOKEN_nic_abc", 1, 0.0
    )

    assert _holds(client) == []


def test_expired_hold_is_not_offered_for_payment(client: TestClient) -> None:
    """An expired hold holds nothing; paying it would only fail at checkout."""
    from datetime import datetime, timedelta, timezone

    txn_id = _hold(client)
    txn = booking_server._state_machine.get(txn_id)
    txn.hold_expires_at = datetime.now(tz=timezone.utc) - timedelta(minutes=1)

    assert _holds(client) == []


def test_pending_holds_expose_no_pii(client: TestClient) -> None:
    """The panel is rendered on screen, so it must hold no raw identifiers."""
    _hold(client, passenger_token="TOKEN_nic_deadbeef")

    entry = _holds(client)[0]
    assert entry["passenger_token" if "passenger_token" in entry else "transaction_id"]
    assert "200012345678" not in str(entry)
    assert "TOKEN_" not in str(entry)


def test_pending_payment_can_be_completed_after_the_fact(client: TestClient) -> None:
    """The whole point: an interrupted checkout is resumable."""
    txn_id = _hold(client)
    assert _holds(client)[0]["transaction_id"] == txn_id

    settled = client.post(
        "/mcp/settle_booking", json={"transaction_id": txn_id, "card_last4": "4242"}
    )

    assert settled.status_code == 200, settled.text
    assert settled.json()["data"]["booking_reference"]


def test_pending_holds_are_newest_first(client: TestClient) -> None:
    first = _hold(client)
    second = _hold(client)

    entries = _holds(client)

    assert [e["transaction_id"] for e in entries][0] == second
    assert first in [e["transaction_id"] for e in entries]


def test_empty_history_is_an_empty_list_not_an_error(client: TestClient) -> None:
    assert _holds(client) == []
    assert client.get("/mcp/purchases").json()["data"]["purchases"] == []
