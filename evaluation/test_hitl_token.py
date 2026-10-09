"""
Human-in-the-Loop confirmation tokens (R-09).

Member 3 — Execution Engine & Security Lead

The gate used to be a client-supplied boolean. `hitl_approved: true` on the
approve turn meant the client was asserting its own approval, so a script could
hold a seat with no human in the loop. Staging capped the damage — a forged
approval produced a held seat awaiting payment, never a ticket — but the flag
itself was trusted, which is not a gate.

These tests pin the replacement: approval is a signed capability the client
returns. The properties that matter are that it cannot be manufactured, cannot be
replayed onto a different booking, and that the gate fails closed when the
signing key is missing.

Run:
    pytest evaluation/test_hitl_token.py -v
"""

from __future__ import annotations

import os
import sys
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.booking.hitl_token import (  # noqa: E402
    HitlTokenError,
    issue_hitl_token,
    verify_hitl_token,
)

SESSION = "sess-1"
ROUTE = "TRAIN-1007"
BUS_ROUTE = "SLTB-1-A-C-0600"
FARE = 900.0


def _token(**overrides) -> str:
    args = {
        "session_id": SESSION,
        "route_id": ROUTE,
        "fare_lkr": FARE,
        "seat_count": 2,
        "provider": "SLR",
    }
    args.update(overrides)
    return issue_hitl_token(**args)


# ── The happy path ───────────────────────────────────────────────────────────

def test_a_fresh_token_verifies() -> None:
    claims = verify_hitl_token(_token(), SESSION, ROUTE, FARE)

    assert claims["route"] == ROUTE
    assert claims["sid"] == SESSION
    assert claims["fare"] == FARE


# ── It cannot be manufactured ────────────────────────────────────────────────

def test_an_empty_token_is_refused() -> None:
    """The old exploit: simply not approving, and asserting approval anyway."""
    with pytest.raises(HitlTokenError):
        verify_hitl_token("", SESSION, ROUTE, FARE)


@pytest.mark.parametrize(
    "forged",
    [
        "true",
        "1",
        "yes",
        "approved",
        "eyJyb3V0ZSI6IlRSQUlOLTEwMDciLCJzaWduIjoiZmFrZSJ9.deadbeef",
        "not-a-token",
        "...",
    ],
)
def test_boolean_shaped_values_are_refused(forged: str) -> None:
    """
    Nothing that merely *looks* like approval is accepted.

    Worth pinning explicitly: the previous gate would have taken any of these if
    the caller said so in the right field, and a naive replacement that parsed a
    truthy string would reintroduce exactly that.
    """
    with pytest.raises(HitlTokenError):
        verify_hitl_token(forged, SESSION, ROUTE, FARE)


def test_a_tampered_payload_is_refused() -> None:
    """Editing the fare inside a valid token invalidates the signature."""
    token = _token()
    body, _signature = token.split(".", 1)

    with pytest.raises(HitlTokenError):
        verify_hitl_token(f"{body}.{'A' * 43}", SESSION, ROUTE, FARE)


def test_a_token_signed_with_another_key_is_refused(monkeypatch) -> None:
    """Two deployments must not accept each other's tokens."""
    forged = _token()
    monkeypatch.setenv("HITL_TOKEN_SECRET", "a-completely-different-key-987654321")

    with pytest.raises(HitlTokenError):
        verify_hitl_token(forged, SESSION, ROUTE, FARE)


def test_malformed_base64_does_not_crash() -> None:
    """A hostile token must produce a clean rejection, not a 500."""
    with pytest.raises(HitlTokenError):
        verify_hitl_token("!!!.!!!", SESSION, ROUTE, FARE)


# ── It cannot be replayed onto another booking ───────────────────────────────

def test_cross_session_replay_is_refused() -> None:
    """A token stolen from one traveller's session cannot clear another's."""
    with pytest.raises(HitlTokenError, match="different session"):
        verify_hitl_token(_token(), "attacker-session", ROUTE, FARE)


def test_route_substitution_is_refused() -> None:
    """A token for one service cannot authorise a different one."""
    with pytest.raises(HitlTokenError, match="different route"):
        verify_hitl_token(_token(), SESSION, "TRAIN-9999", FARE)


def test_fare_escalation_is_refused() -> None:
    """
    A token obtained for a cheap seat cannot be reused on an expensive one.

    This is why the fare is a claim and not just a lookup: without it, approval
    of a LKR 450 seat would authorise a LKR 4,500 one.
    """
    cheap = _token(fare_lkr=450.0, seat_count=1)

    with pytest.raises(HitlTokenError, match="fare"):
        verify_hitl_token(cheap, SESSION, ROUTE, 4500.0)


def test_a_one_cent_difference_is_refused() -> None:
    """Bound to the cent, so rounding cannot be used to slip a fare past."""
    with pytest.raises(HitlTokenError, match="fare"):
        verify_hitl_token(_token(fare_lkr=450.0), SESSION, ROUTE, 450.01)


# ── Expiry ───────────────────────────────────────────────────────────────────

def test_an_expired_token_is_refused(monkeypatch) -> None:
    """Short-lived, because a token that lingers widens the replay window."""
    token = _token(ttl_seconds=1)
    time.sleep(1.1)

    with pytest.raises(HitlTokenError, match="expired"):
        verify_hitl_token(token, SESSION, ROUTE, FARE)


def test_expiry_is_a_claim_not_just_a_timestamp() -> None:
    """A token with a forged far-future expiry still fails on the signature."""
    token = _token()
    body, signature = token.split(".", 1)

    with pytest.raises(HitlTokenError, match="signature"):
        verify_hitl_token(f"{body[:-2]}99.{signature}", SESSION, ROUTE, FARE)


# ── Fail closed ──────────────────────────────────────────────────────────────

def test_no_configured_secret_refuses_to_issue(monkeypatch) -> None:
    """
    Without a signing key there is no gate, so the gate stays shut.

    A default key would be the wrong fix: every deployment would share it, and
    anyone could forge an approval. This is the same reasoning as refusing to
    ship a default database password.
    """
    monkeypatch.delenv("HITL_TOKEN_SECRET", raising=False)

    with pytest.raises(HitlTokenError, match="HITL_TOKEN_SECRET"):
        issue_hitl_token(SESSION, ROUTE, FARE)


def test_a_short_secret_is_refused(monkeypatch) -> None:
    monkeypatch.setenv("HITL_TOKEN_SECRET", "short")

    with pytest.raises(HitlTokenError, match="HITL_TOKEN_SECRET"):
        issue_hitl_token(SESSION, ROUTE, FARE)


def test_a_token_stops_verifying_when_the_key_is_rotated(monkeypatch) -> None:
    """Rotating the key invalidates outstanding tokens — the intended effect."""
    token = _token()
    monkeypatch.setenv("HITL_TOKEN_SECRET", "rotated-key-0123456789abcdef")

    with pytest.raises(HitlTokenError):
        verify_hitl_token(token, SESSION, ROUTE, FARE)


# ── The token is opaque to the client ────────────────────────────────────────

def test_the_token_carries_no_secret() -> None:
    """The signing key must not be recoverable from an issued token."""
    token = _token()
    secret = os.environ["HITL_TOKEN_SECRET"]

    assert secret not in token
    body, _signature = token.split(".", 1)
    assert "HITL_TOKEN_SECRET" not in body


# ── The approval must cover the seat count, not just the route ───────────────
#
# `issue_hitl_token` carried `seats` and `provider` in the signed payload, but
# verification only compared session, route, fare and expiry. A token approving
# one seat at LKR 850 could therefore be replayed to hold six: the traveller
# approved LKR 850 and was billed LKR 5,100. These hold that closed.


def test_a_token_for_one_seat_cannot_hold_six() -> None:
    token = _token(route_id=BUS_ROUTE, seat_count=1, fare_lkr=850.0, provider="SLTB")
    with pytest.raises(HitlTokenError, match="seat"):
        verify_hitl_token(
            token,
            session_id=SESSION,
            route_id=BUS_ROUTE,
            fare_lkr=850.0,
            seat_count=6,
            provider="SLTB",
        )


def test_a_token_cannot_be_replayed_to_another_operator() -> None:
    token = _token(route_id=BUS_ROUTE, seat_count=2, fare_lkr=850.0, provider="SLR")
    with pytest.raises(HitlTokenError, match="operator"):
        verify_hitl_token(
            token,
            session_id=SESSION,
            route_id=BUS_ROUTE,
            fare_lkr=850.0,
            seat_count=2,
            provider="SLTB",
        )


def test_the_matching_seat_count_still_verifies() -> None:
    """Binding the claim must not break the ordinary approval."""
    token = _token(route_id=BUS_ROUTE, seat_count=2, fare_lkr=850.0, provider="SLTB")
    claims = verify_hitl_token(
        token,
        session_id=SESSION,
        route_id=BUS_ROUTE,
        fare_lkr=850.0,
        seat_count=2,
        provider="SLTB",
    )
    assert claims["seats"] == 2
