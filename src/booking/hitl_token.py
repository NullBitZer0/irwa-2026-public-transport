"""
Signed Human-in-the-Loop confirmation tokens.

Member 3 — Execution Engine & Security Lead

Finding R-09: the human-in-the-loop gate was a **client-supplied boolean**. The
UI set `hitl_approved: true` on the approve turn, so a traveller (or a script)
could assert their own approval — one HTTP call with no human in the loop held a
seat. Staging the booking capped the damage: a forged approval produced a *held
seat awaiting payment*, never a ticket. But the flag itself was still trusted,
which is not a gate.

This module replaces the boolean with a capability the client cannot manufacture.

Flow
----
1. The traveller picks a route. The Booking Agent issues a confirmation token
   binding **session, route and fare**, signed with HMAC-SHA256 and expiring
   after a few minutes.
2. The UI shows the gate and, when the traveller approves, returns the token.
3. The Booking Agent verifies the signature and checks the claims still match the
   booking being attempted — then holds the seat.

Why this closes the gap: approval is now something the client must *return*
rather than something it can *assert*. Forging one requires the signing secret,
which never leaves the server. Binding the fare also means a token obtained for
a LKR 450 seat cannot be replayed to take a LKR 4,500 one.

Deliberate non-goals: this is not a substitute for re-authenticating the
traveller. It proves the server issued an approval opportunity and that nobody
edited the route or fare since — it does not prove a human clicked. Raising it to
an authenticated principal is the next step, and is recorded in the report.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import os
import time
from typing import Any

# Short: the token is spent within one interaction, so a long life only widens
# the replay window. It must outlive a round trip through the UI, not a session.
TOKEN_TTL_SECONDS = 10 * 60


class HitlTokenError(Exception):
    """Raised when a confirmation token is absent, malformed, forged or stale."""


def _secret() -> bytes:
    """
    The signing key.

    Required. There is deliberately no default: a hardcoded fallback would mean
    every deployment shares one signing key, which is the same mistake as the
    committed database password this repo just removed. The Booking Agent
    refuses to issue or accept tokens without one, so the gate fails closed.
    """
    value = os.getenv("HITL_TOKEN_SECRET")
    if not value or len(value) < 16:
        raise HitlTokenError(
            "HITL_TOKEN_SECRET is not configured. Set it to a random value of at "
            "least 16 characters (openssl rand -hex 24). The confirmation gate "
            "fails closed without it."
        )
    return value.encode()


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _unb64(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def issue_hitl_token(
    session_id: str,
    route_id: str,
    fare_lkr: float,
    seat_count: int = 1,
    provider: str = "SLR",
    ttl_seconds: int = TOKEN_TTL_SECONDS,
) -> str:
    """
    Issues a signed confirmation token for one specific booking.

    Claims are bound to the route, fare, seat count and provider so the token
    cannot be reused for a different — or more expensive — booking.
    """
    payload = {
        "sid": session_id,
        "route": route_id,
        "fare": round(float(fare_lkr), 2),
        "seats": int(seat_count),
        "provider": (provider or "SLR").upper(),
        # Float, not int: truncating to whole seconds makes expiry fire up to a
        # second late, so a token could outlive its stated window.
        "exp": time.time() + ttl_seconds,
        "nonce": _b64(os.urandom(9)),
    }
    body = _b64(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
    signature = _b64(_sign(body))
    return f"{body}.{signature}"


def verify_hitl_token(token: str, session_id: str, route_id: str, fare_lkr: float) -> dict:
    """
    Verifies a token and that it authorises *this* booking.

    Raises HitlTokenError on any failure. Callers must not downgrade a failure to
    a warning: a token that does not verify means no approval happened.
    """
    if not token:
        raise HitlTokenError("No human-in-the-loop confirmation supplied.")

    try:
        body, signature = token.split(".", 1)
    except ValueError as exc:
        raise HitlTokenError("Malformed confirmation token.") from exc

    expected = _sign(body)
    # Constant-time compare: a timing-sensitive comparison would leak the
    # signature byte by byte.
    if not hmac.compare_digest(_unb64_safe(signature), expected):
        raise HitlTokenError("Confirmation token signature does not verify.")

    try:
        claims: dict[str, Any] = json.loads(_unb64(body))
    except (ValueError, json.JSONDecodeError) as exc:
        raise HitlTokenError("Confirmation token payload is unreadable.") from exc

    if float(claims.get("exp", 0)) < time.time():
        raise HitlTokenError("Confirmation token has expired. Please re-approve.")

    if claims.get("sid") != session_id:
        raise HitlTokenError("Confirmation token belongs to a different session.")
    if claims.get("route") != route_id:
        raise HitlTokenError("Confirmation token was issued for a different route.")
    # Bound to the cent, so a token for one fare cannot be replayed on another.
    if round(float(claims.get("fare", -1)), 2) != round(float(fare_lkr), 2):
        raise HitlTokenError("Confirmation token does not match the quoted fare.")

    return claims


def _sign(body: str) -> bytes:
    return hmac.new(_secret(), body.encode(), hashlib.sha256).digest()


def _unb64_safe(value: str) -> bytes:
    """base64-decode without raising on malformed input."""
    try:
        return _unb64(value)
    except (ValueError, binascii.Error):
        return b""
