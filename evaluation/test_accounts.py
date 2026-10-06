"""
Accounts, sessions, and the card rule.

The card tests are the point of this file. Everything else is ordinary
registration plumbing; what needed care was making it structurally impossible for
a full card number to reach disk, and proving it.

Run:
    pytest evaluation/test_accounts.py -v
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.orchestrator import server as orch  # noqa: E402
from src.orchestrator.accounts import (  # noqa: E402
    MIN_PASSWORD_LENGTH,
    AccountStore,
    detect_brand,
    hash_password,
    luhn_valid,
    normalise_contact,
    public_profile,
    summarise_card,
    verify_password,
)

VALID_VISA = "4111111111111111"


# ── Passwords ────────────────────────────────────────────────────────────────


def test_passwords_are_hashed_not_stored() -> None:
    store = AccountStore()
    user = store.create_user("a@example.com", "correct horse battery")

    assert "correct horse battery" not in user["password_hash"]
    assert user["password_hash"].startswith("scrypt$")
    # Salted: the same password must not produce the same digest twice.
    assert user["password_hash"] != hash_password("correct horse battery")


def test_the_right_password_verifies_and_the_wrong_one_does_not() -> None:
    store = AccountStore()
    stored = store.create_user("b@example.com", "correct horse battery")["password_hash"]

    assert verify_password("correct horse battery", stored) is True
    assert verify_password("Correct horse battery", stored) is False
    assert verify_password("", stored) is False


def test_a_malformed_hash_fails_closed_rather_than_raising() -> None:
    """A corrupted row must not 500 the login endpoint."""
    for broken in ("", "nonsense", "scrypt$bad$r$p$salt", "bcrypt$1$2$3$4$5"):
        assert verify_password("whatever", broken) is False


def test_short_passwords_are_refused() -> None:
    store = AccountStore()

    with pytest.raises(ValueError):
        store.create_user("c@example.com", "x" * (MIN_PASSWORD_LENGTH - 1))


def test_emails_are_normalised_and_unique() -> None:
    store = AccountStore()
    first = store.create_user("  Traveller@Example.COM ", "password1234")

    assert first["email"] == "traveller@example.com"
    with pytest.raises(ValueError, match="already exists"):
        store.create_user("TRAVELLER@example.com", "password1234")


def test_invalid_emails_are_refused() -> None:
    store = AccountStore()

    for bad in ("", "not-an-email", "a@b"):
        with pytest.raises(ValueError):
            store.create_user(bad, "password1234")


# ── Card numbers: validated, branded, then discarded ─────────────────────────


def test_a_valid_card_is_reduced_to_brand_and_last_four() -> None:
    assert summarise_card("4111 1111 1111 1111") == ("Visa", "1111")
    assert summarise_card("4111-1111-1111-1111") == ("Visa", "1111")


def test_a_bad_checksum_is_rejected() -> None:
    """
    Catching the typo here is the whole reason to accept a card number at all.
    """
    with pytest.raises(ValueError, match="checksum"):
        summarise_card("4111111111111112")

    for junk in ("", "abcd", "1234", "411111111111111111111111"):
        with pytest.raises(ValueError):
            summarise_card(junk)


@pytest.mark.parametrize(
    "number,brand",
    [
        ("4111111111111111", "Visa"),
        ("5555555555554444", "Mastercard"),
        ("2221000000000009", "Mastercard"),
        ("340000000000009", "American Express"),
        ("370000000000002", "American Express"),
        ("30000000000004", "Diners Club"),
        ("3530111333300000", "JCB"),
        ("6200000000000005", "UnionPay"),
        ("5018000000000009", "Maestro"),
        ("6011000000000004", "Discover"),
        ("6500000000000002", "Discover"),
        ("9999999999999999", "Card"),
    ],
)
def test_card_brands_are_detected_from_the_iin(number: str, brand: str) -> None:
    assert detect_brand(number) == brand


def test_luhn_rejects_a_transposed_digit() -> None:
    """The most common real mistake, and the one a checksum exists to catch."""
    assert luhn_valid(VALID_VISA) is True
    assert luhn_valid("4111111111111121") is False


# ── Contact numbers ──────────────────────────────────────────────────────────


def test_local_mobile_numbers_become_e164() -> None:
    assert normalise_contact("0771234567") == "+94771234567"
    assert normalise_contact("071 123 4567") == "+94711234567"
    assert normalise_contact("+94 77 123 4567") == "+94771234567"


def test_nonsense_contact_numbers_are_refused() -> None:
    for bad in ("123", "not a number", "077123456789012"):
        with pytest.raises(ValueError):
            normalise_contact(bad)


def test_an_empty_contact_number_clears_the_field() -> None:
    assert normalise_contact("") == ""


# ── Sessions ─────────────────────────────────────────────────────────────────


def test_a_session_resolves_and_a_forged_token_does_not() -> None:
    store = AccountStore()
    user = store.create_user("d@example.com", "password1234")
    token = store.create_session(user["id"], "pytest")

    assert store.resolve_session(token)["email"] == "d@example.com"
    assert store.resolve_session("forged") is None
    assert store.resolve_session(None) is None
    assert store.resolve_session("") is None


def test_the_raw_token_is_not_in_the_database() -> None:
    """A stolen database must not yield usable sessions."""
    path = "/tmp/opencode/session-leak-test.db"
    if os.path.exists(path):
        os.remove(path)
    store = AccountStore(path)
    user = store.create_user("e@example.com", "password1234")
    token = store.create_session(user["id"], "pytest")
    # Each operation opens and closes its own connection, so the row is already
    # committed to the file by the time create_session returns.
    with open(path, "rb") as handle:
        blob = handle.read()

    assert token.encode() not in blob
    with sqlite3.connect(path) as conn:
        assert token not in "".join(
            row[0] for row in conn.execute("SELECT token_hash FROM sessions")
        )
    os.remove(path)


def test_logout_invalidates_the_token_server_side() -> None:
    store = AccountStore()
    user = store.create_user("f@example.com", "password1234")
    token = store.create_session(user["id"], "pytest")

    store.delete_session(token)

    assert store.resolve_session(token) is None


def test_expired_sessions_are_refused_and_cleaned_up() -> None:
    store = AccountStore()
    user = store.create_user("g@example.com", "password1234")
    token = store.create_session(user["id"], "pytest")

    with store._lock, store._connect() as conn:
        conn.execute("UPDATE sessions SET expires_at = 1 WHERE token_hash = ?",
                     (store._token_hash(token),))

    assert store.resolve_session(token) is None
    with store._lock, store._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0


# ── Profile projection ───────────────────────────────────────────────────────


def test_the_public_profile_never_leaks_the_password_hash() -> None:
    """
    `dict(row)` would work right up until someone added a column.
    """
    store = AccountStore()
    user = store.create_user("h@example.com", "password1234")
    store.update_profile(user["id"], full_name="Nimal Perera")

    profile = public_profile(store.get_user(user["id"]))

    assert "password_hash" not in profile
    assert "password_hash" not in json.dumps(profile)
    assert profile["full_name"] == "Nimal Perera"


def test_the_store_cannot_be_persuaded_to_store_a_full_card_number() -> None:
    """update_profile only accepts the four columns it lists."""
    store = AccountStore()
    user = store.create_user("i@example.com", "password1234")

    with pytest.raises(ValueError):
        store.update_profile(user["id"], card_number=VALID_VISA)


# ── HTTP surface ─────────────────────────────────────────────────────────────


@pytest.fixture()
def client():
    orch.ACCOUNTS = AccountStore()
    orch._ensure_demo_account()
    return TestClient(orch.app)


def test_register_signs_you_in(client: TestClient) -> None:
    res = client.post(
        "/auth/register", json={"email": "new@example.com", "password": "password1234"}
    )

    assert res.status_code == 200
    assert res.json()["user"]["email"] == "new@example.com"
    # The session arrives as an HttpOnly cookie so no script can read it.
    assert "lankajourney_session" in res.cookies
    cookie_header = res.headers.get("set-cookie", "")
    assert "HttpOnly" in cookie_header
    assert client.get("/auth/me").status_code == 200


def test_login_rejects_a_wrong_password_and_an_unknown_email_alike(client: TestClient) -> None:
    client.post("/auth/register", json={"email": "u@example.com", "password": "password1234"})

    wrong = client.post("/auth/login", json={"email": "u@example.com", "password": "nope12345"})
    unknown = client.post("/auth/login", json={"email": "ghost@example.com", "password": "nope12345"})

    assert wrong.status_code == unknown.status_code == 401
    # The same message, so the response cannot be used to enumerate accounts.
    assert wrong.json()["detail"] == unknown.json()["detail"]


def test_the_demo_account_signs_in(client: TestClient) -> None:
    res = client.post(
        "/auth/login", json={"email": "demo@lankajourney.lk", "password": "demotravel123"}
    )

    assert res.status_code == 200
    assert res.json()["user"]["email"] == "demo@lankajourney.lk"


def test_the_user_api_requires_a_session(client: TestClient) -> None:
    """
    Authentication is the edge of the trust boundary, not a nicety on the login
    form: an open /chat would expose every traveller's conversations.
    """
    for method, path in [
        ("post", "/chat"),
        ("post", "/conversations"),
        ("get", "/conversations"),
        ("post", "/payment"),
        ("get", "/purchases"),
        ("get", "/pending_holds"),
        ("post", "/demo_incident"),
        ("get", "/schedules"),
        ("get", "/map-routes"),
        ("get", "/auth/me"),
        ("patch", "/profile"),
    ]:
        res = getattr(client, method)(
            path, **({"json": {"query": "hi"}} if method == "post" and path == "/chat" else {})
        )
        assert res.status_code == 401, f"{method.upper()} {path} was reachable without a session"


def test_health_stays_public(client: TestClient) -> None:
    """The orchestrator must be able to report liveness before anyone signs in."""
    assert client.get("/health").status_code == 200


def test_the_profile_can_be_updated_and_the_card_is_reduced(client: TestClient) -> None:
    client.post("/auth/register", json={"email": "p@example.com", "password": "password1234"})

    res = client.patch(
        "/profile",
        json={
            "full_name": "Nimal Perera",
            "contact_number": "0771234567",
            "card_number": VALID_VISA,
        },
    )
    user = res.json()["user"]

    assert res.status_code == 200
    assert user["full_name"] == "Nimal Perera"
    assert user["contact_number"] == "+94771234567"
    assert (user["card_brand"], user["card_last4"]) == ("Visa", "1111")
    # Nothing resembling a full PAN comes back.
    assert VALID_VISA not in json.dumps(user)
    assert "card_number" not in user


def test_the_profile_rejects_a_bad_card_and_a_bad_contact(client: TestClient) -> None:
    client.post("/auth/register", json={"email": "q@example.com", "password": "password1234"})

    bad_card = client.patch("/profile", json={"card_number": "4111111111111112"})
    bad_contact = client.patch("/profile", json={"contact_number": "123"})

    assert bad_card.status_code == 400
    assert bad_contact.status_code == 400


def test_logout_then_me_is_unauthorised(client: TestClient) -> None:
    client.post("/auth/register", json={"email": "r@example.com", "password": "password1234"})
    assert client.post("/auth/logout").status_code == 200
    assert client.get("/auth/me").status_code == 401
