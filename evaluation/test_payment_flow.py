"""
Payment & Purchase-History Tests
Member 3 — Booking Agent

The Booking Agent owns seat availability, payment and confirmation; the
Orchestrator only routes. These tests cover the staged flow the payment portal
depends on: hold → HITL gate → pay → ticket issued → recorded in the ledger.

Run:
    pytest evaluation/test_payment_flow.py -v
"""

from __future__ import annotations

import os
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.booking.server import app as booking_app  # noqa: E402


@pytest.fixture
def client() -> TestClient:
    return TestClient(booking_app)


@pytest.fixture
def booking_client() -> TestClient:
    return TestClient(booking_app)


def _begin(client: TestClient, **overrides) -> dict:
    body = {
        "route_id": "TRAIN-1001",
        "provider": "SLR",
        "passenger_token": "GUEST-TEST",
        "seat_count": 2,
        "fare_lkr": 850.0,
        "user_confirmed": True,
    }
    body.update(overrides)
    res = client.post("/mcp/begin_booking", json=body)
    assert res.status_code == 200, res.text
    # Standard MCP envelope: the payload lives under "data".
    return res.json()["data"]


# ── Staging ──────────────────────────────────────────────────────────────────

def test_begin_booking_holds_without_charging(client: TestClient) -> None:
    """The portal needs a transaction id and an amount before any money moves."""
    data = _begin(client)
    txn = data["transaction"]
    assert txn["transaction_id"].startswith("TXN-")
    assert txn["state"] == "AWAITING_PAYMENT"
    assert txn["fare_lkr"] == 850.0
    # Nothing is issued until settlement.
    assert not txn["booking_reference"]


def test_begin_booking_respects_the_hitl_gate(client: TestClient) -> None:
    """Without explicit approval the hold must not progress to payment."""
    res = client.post(
        "/mcp/begin_booking",
        json={
            "route_id": "TRAIN-1001",
            "provider": "SLR",
            "passenger_token": "GUEST-TEST",
            "seat_count": 1,
            "fare_lkr": 850.0,
            "user_confirmed": False,
        },
    )
    assert res.status_code in (403, 400), res.status_code


# ── Settlement ───────────────────────────────────────────────────────────────

def test_settlement_issues_a_ticket_and_receipt(client: TestClient) -> None:
    txn_id = _begin(client)["transaction"]["transaction_id"]
    res = client.post(
        "/mcp/settle_booking",
        json={"transaction_id": txn_id, "card_last4": "4242", "provider": "SLR"},
    )
    assert res.status_code == 200, res.text
    body = res.json()["data"]  # standard MCP envelope
    assert body["booking_reference"], "a booking reference must be issued"
    assert body["receipt"]["status"] == "PAID"
    assert body["receipt"]["receipt_id"].startswith("PAY-")
    assert body["receipt"]["amount_lkr"] == 850.0
    assert body["ticket"]["state"] == "CONFIRMED"


@pytest.mark.parametrize("bad", ["4111111111111111", "424", "abcd", ""])
def test_full_card_numbers_are_rejected(client: TestClient, bad: str) -> None:
    """Only the last four digits may ever reach this system."""
    txn_id = _begin(client)["transaction"]["transaction_id"]
    res = client.post(
        "/mcp/settle_booking", json={"transaction_id": txn_id, "card_last4": bad}
    )
    assert res.status_code == 400, res.text
    assert "card_last4" in res.json()["detail"]


def test_settlement_requires_a_known_transaction(client: TestClient) -> None:
    res = client.post(
        "/mcp/settle_booking", json={"transaction_id": "TXN-NOPE", "card_last4": "4242"}
    )
    assert res.status_code == 404


# ── Purchase ledger ──────────────────────────────────────────────────────────

def _purchases(client: TestClient) -> list[dict]:
    res = client.get("/mcp/purchases")
    assert res.status_code == 200, res.text
    return res.json()["data"]["purchases"]  # standard MCP envelope


def test_settled_ticket_appears_in_purchase_history(client: TestClient) -> None:
    before = len(_purchases(client))

    txn_id = _begin(client, seat_count=3, fare_lkr=1200.0)["transaction"]["transaction_id"]
    settled = client.post(
        "/mcp/settle_booking",
        json={"transaction_id": txn_id, "card_last4": "4242"},
    ).json()["data"]

    purchases = _purchases(client)
    assert len(purchases) == before + 1

    # The endpoint returns newest first, so the ticket just bought is at index 0.
    entry = purchases[0]
    assert entry["booking_reference"] == settled["booking_reference"]
    assert entry["route_id"] == "TRAIN-1001"
    assert entry["seat_count"] == 3
    assert entry["amount_paid_lkr"] == 1200.0
    assert entry["card_last4"] == "4242"
    assert entry["purchased_at"]


def test_unpaid_holds_do_not_appear_in_history(client: TestClient) -> None:
    """A held seat is not a purchase."""
    before = len(_purchases(client))
    _begin(client)
    assert len(_purchases(client)) == before


def test_history_exposes_no_pii(client: TestClient) -> None:
    """The ledger is displayed on screen, so it must hold no raw identifiers."""
    txn_id = _begin(client, passenger_token="TOKEN_NIC_deadbeef")[
        "transaction"
    ]["transaction_id"]
    client.post(
        "/mcp/settle_booking",
        json={"transaction_id": txn_id, "card_last4": "4242"},
    )
    entry = _purchases(client)[0]  # newest first
    # The token is opaque, and no raw NIC/phone can appear.
    assert entry["passenger_token"].startswith("TOKEN_")
    assert "200012345678" not in str(entry)


# ── Fares are not client-supplied ────────────────────────────────────────────

def test_zero_fare_is_refused(booking_client: TestClient) -> None:
    """A missing fare must never become a free ticket."""
    res = booking_client.post(
        "/mcp/begin_booking",
        json={
            "route_id": "TRAIN-1001",
            "provider": "SLR",
            "passenger_token": "GUEST-TEST",
            "seat_count": 1,
            "fare_lkr": 0,
            "user_confirmed": True,
        },
    )
    assert res.status_code == 400, res.text
    assert "no fare" in res.json()["detail"].lower()


def test_chat_api_accepts_no_fare_field() -> None:
    """
    The tamper vector is removed at the API boundary, not merely ignored.

    A client that posts its own price must not be able to set one, so the field
    simply does not exist on the request model.
    """
    from src.orchestrator.server import ChatRequest

    assert "fare_lkr" not in ChatRequest.model_fields
    assert "fare" not in ChatRequest.model_fields


def test_planner_is_the_pricing_authority() -> None:
    """Fares come from the planner's matrix, not from the caller."""
    from src.planner.hybrid_retriever import HybridTransitRetriever
    from src.planner.server import app as planner_app

    tc = TestClient(planner_app)

    priced = tc.get("/mcp/fare", params={"route_id": "TRAIN-1001"}).json()["data"]
    assert priced["fare_lkr"] == 850.0
    assert priced["fare_unknown"] is False

    # A route with no published fare reports unknown, not zero.
    unpriced = next(
        s["route_id"] for s in HybridTransitRetriever().schedules if s.get("fare_unknown")
    )
    unknown = tc.get("/mcp/fare", params={"route_id": unpriced}).json()["data"]
    assert unknown["fare_unknown"] is True
    assert unknown["fare_lkr"] is None

    missing = tc.get("/mcp/fare", params={"route_id": "NOPE-999"}).json()
    assert missing["status"] == "NOT_FOUND"
    assert missing["data"]["fare_lkr"] is None
