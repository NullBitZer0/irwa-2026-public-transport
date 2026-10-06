"""
Accounts, sign-in and profile.

**On the card number.** The brief asked for a profile where the traveller can
change their credit card number. A card number is not a profile field: storing a
full PAN puts the project in PCI-DSS scope, makes the SQLite file a target worth
stealing, and — worst — means the number is one careless log line away from being
exfiltrated. The rest of this project refuses to send a PAN to any agent; writing
one to disk would undo that.

So the card is **validated, branded and reduced to its last four digits, and the
full number is then discarded**. It is never persisted, never logged, never
returned by an endpoint, and never sent to an agent or a model. What the profile
shows is "Visa •••• 4242", which is what the traveller needs to recognise their
own card, and what a booking receipt needs to record.

If a real card were ever to be charged, the PAN would go straight from the
browser to the payment gateway over TLS and never touch this server. That is the
correct architecture for this data, not a compromise made for a demo.

Passwords are hashed with scrypt (memory-hard, in the standard library) and
compared in constant time. Session tokens are random, stored only as SHA-256
digests, and handed to the client in an `HttpOnly` cookie, so a stolen database
does not yield usable sessions.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import sqlite3
import threading
import time
from contextlib import contextmanager
from typing import Any, Iterator, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id                  TEXT PRIMARY KEY,
    email               TEXT NOT NULL UNIQUE,
    password_hash       TEXT NOT NULL,
    created_at          TEXT NOT NULL,
    full_name           TEXT NOT NULL DEFAULT '',
    contact_number      TEXT NOT NULL DEFAULT '',
    card_brand          TEXT NOT NULL DEFAULT '',
    card_last4          TEXT NOT NULL DEFAULT '',
    card_added_at       TEXT
);

CREATE TABLE IF NOT EXISTS sessions (
    token_hash  TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at  REAL NOT NULL,
    expires_at  REAL NOT NULL,
    user_agent  TEXT NOT NULL DEFAULT ''
);
"""

# scrypt parameters. n=2**14 keeps hashing around 60-80ms on a laptop, which is
# the point: expensive enough to make offline cracking costly, fast enough that a
# login still feels instant.
SCRYPT_N = 2**14
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_DKLEN = 32

SESSION_TTL_SECONDS = 12 * 60 * 60
SESSION_COOKIE = "lankajourney_session"

MIN_PASSWORD_LENGTH = 8

# Card networks, by the digits that identify them (ISO/IEC 7812). Order matters
# where ranges overlap: Diners starts with 36, which Mastercard's 51-55 does not
# touch, but Amex 34 and Maestro 5018 must be tested before the ranges that
# swallow them.
CARD_BRANDS: list[tuple[str, str]] = [
    ("4", "Visa"),
    ("34", "American Express"),
    ("37", "American Express"),
    ("30", "Diners Club"),
    ("36", "Diners Club"),
    ("38", "Diners Club"),
    ("35", "JCB"),
    ("62", "UnionPay"),
    ("5018", "Maestro"),
    ("5020", "Maestro"),
    ("5038", "Maestro"),
    ("5893", "Maestro"),
    ("6011", "Discover"),
    ("65", "Discover"),
]

# Prefixes above are fixed-width except these, which are numeric ranges.
CARD_RANGES: list[tuple[int, int, str]] = [
    (51, 55, "Mastercard"),
    (2221, 2720, "Mastercard"),
    (3528, 3589, "JCB"),
    (644, 649, "Discover"),
    (6304, 6304, "Maestro"),
    (6759, 6759, "Maestro"),
    (6761, 6763, "Maestro"),
]


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# ── Passwords ────────────────────────────────────────────────────────────────


def hash_password(password: str) -> str:
    """
    Returns `scrypt$n$r$p$salt$digest`, all base64/hex encoded.

    The parameters travel with the hash so they can be raised later without
    invalidating existing passwords.
    """
    if not isinstance(password, str) or len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"password must be at least {MIN_PASSWORD_LENGTH} characters")
    salt = os.urandom(16)
    derived = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
        dklen=SCRYPT_DKLEN,
        maxmem=SCRYPT_N * SCRYPT_R * 256 * 2,
    )
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${salt.hex()}${derived.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """Constant-time check. Returns False for malformed hashes, never raises."""
    try:
        scheme, n, r, p, salt_hex, digest_hex = stored.split("$")
        if scheme != "scrypt":
            return False
        derived = hashlib.scrypt(
            password.encode("utf-8"),
            salt=bytes.fromhex(salt_hex),
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(bytes.fromhex(digest_hex)),
            maxmem=int(n) * int(r) * 256 * 2,
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(derived, bytes.fromhex(digest_hex))


# ── Card numbers ─────────────────────────────────────────────────────────────


def luhn_valid(digits: str) -> bool:
    """The Luhn checksum, so a typo is caught here rather than at a gateway."""
    if not digits.isdigit() or not 12 <= len(digits) <= 19:
        return False
    total = 0
    for index, char in enumerate(reversed(digits)):
        value = int(char)
        if index % 2 == 1:
            value *= 2
            if value > 9:
                value -= 9
        total += value
    return total % 10 == 0


def detect_brand(digits: str) -> str:
    """Network from the leading digits, or 'Card' when it matches nothing known."""
    for prefix, brand in CARD_BRANDS:
        if digits.startswith(prefix):
            return brand

    # Numeric ranges, longest prefix first so 2221 is not read as 222.
    for length in (4, 3, 2):
        if len(digits) >= length and digits[:length].isdigit():
            value = int(digits[:length])
            for low, high, brand in CARD_RANGES:
                if low <= value <= high and digits.startswith(str(low)):
                    return brand
            for low, high, brand in CARD_RANGES:
                if low <= value <= high:
                    return brand
    return "Card"


def summarise_card(number: str) -> tuple[str, str]:
    """
    Validates a card number and returns (brand, last4).

    This is the only place a full card number is ever handled, and the full
    number does not leave this function.
    """
    digits = "".join(ch for ch in (number or "") if ch.isdigit())
    if not luhn_valid(digits):
        raise ValueError("that card number failed its checksum — please check it")
    return detect_brand(digits), digits[-4:]


# ── Contact numbers ──────────────────────────────────────────────────────────


def normalise_contact(raw: str) -> str:
    """
    Keeps a Sri Lankan mobile in +94XXXXXXXXX form.

    Digits only, because this string goes into an SMS-shaped field and a stray
    space or bracket would make the operator's system reject it later.

    Separators are tolerated; anything else is an error rather than a silent
    empty. "not a number" contains no digits, so a strip-the-digits approach would
    clear the field and the traveller would believe they had saved it.
    """
    raw = (raw or "").strip()
    if not raw:
        return ""

    cleaned = "".join(ch for ch in raw if ch not in " +-()")
    if not cleaned.isdigit():
        # Anything beyond digits and the tolerated separators survived above.
        raise ValueError("a contact number can only contain digits, spaces and +")

    digits = "".join(ch for ch in raw if ch.isdigit())
    if not digits:
        return ""
    if digits.startswith("0") and len(digits) == 10:
        digits = "94" + digits[1:]
    if len(digits) not in (9, 11):
        raise ValueError("enter a contact number as 07XXXXXXXX or +94XXXXXXXXX")
    return f"+{digits}"


# ── Store ────────────────────────────────────────────────────────────────────


class AccountStore:
    """SQLite-backed users and sessions. In-memory when no path is given."""

    def __init__(self, path: Optional[str] = None) -> None:
        self.path = path
        self._lock = threading.RLock()

        if path:
            parent = os.path.dirname(os.path.abspath(path))
            if parent:
                os.makedirs(parent, exist_ok=True)
        self._shared: Optional[sqlite3.Connection] = None
        if not path:
            self._shared = self._new_connection()
            self._shared.executescript(SCHEMA)
            self._shared.commit()

    def _new_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path or ":memory:", check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        if self._shared is not None:
            yield self._shared
            return
        conn = self._new_connection()
        try:
            conn.executescript(SCHEMA)
            yield conn
            conn.commit()
        finally:
            conn.close()

    # ── users ──

    def create_user(self, email: str, password: str) -> dict[str, Any]:
        email = (email or "").strip().lower()
        if "@" not in email or len(email) < 5:
            raise ValueError("enter a valid email address")

        # Hashed before the uniqueness check, so a duplicate email costs the same
        # work as a new one and cannot be used to time an enumeration attack.
        password_hash = hash_password(password)
        user_id = f"USR-{secrets.token_hex(8).upper()}"

        with self._lock, self._connect() as conn:
            existing = conn.execute(
                "SELECT id FROM users WHERE email = ?", (email,)
            ).fetchone()
            if existing:
                raise ValueError("an account with that email already exists")
            conn.execute(
                "INSERT INTO users (id, email, password_hash, created_at) VALUES (?, ?, ?, ?)",
                (user_id, email, password_hash, now_iso()),
            )
            row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
            return dict(row)

    def get_user_by_email(self, email: str) -> Optional[dict[str, Any]]:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE email = ?", ((email or "").strip().lower(),)
            ).fetchone()
            return dict(row) if row else None

    def get_user(self, user_id: str) -> Optional[dict[str, Any]]:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
            return dict(row) if row else None

    def update_profile(self, user_id: str, **fields: Any) -> dict[str, Any]:
        """
        Updates the editable profile fields.

        There is no code path here that accepts a full card number: the only
        card fields writable are `card_brand` and `card_last4`, which are
        derived by `summarise_card` before they get here.
        """
        allowed = {"full_name", "contact_number", "card_brand", "card_last4", "card_added_at"}
        updates = {k: v for k, v in fields.items() if k in allowed}
        if not updates:
            raise ValueError("nothing to update")

        with self._lock, self._connect() as conn:
            if not conn.execute("SELECT 1 FROM users WHERE id = ?", (user_id,)).fetchone():
                raise KeyError("no such user")
            assignments = ", ".join(f"{column} = ?" for column in updates)
            conn.execute(
                f"UPDATE users SET {assignments} WHERE id = ?",  # noqa: S608 - fixed columns
                (*updates.values(), user_id),
            )
            row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
            return dict(row)

    def set_password(self, user_id: str, password: str) -> None:
        password_hash = hash_password(password)
        with self._lock, self._connect() as conn:
            conn.execute(
                "UPDATE users SET password_hash = ? WHERE id = ?", (password_hash, user_id)
            )

    # ── sessions ──

    @staticmethod
    def _token_hash(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def create_session(self, user_id: str, user_agent: str = "") -> str:
        """Returns the raw token, once. Only its digest is stored."""
        token = secrets.token_urlsafe(32)
        issued = time.time()
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO sessions (token_hash, user_id, created_at, expires_at, user_agent) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    self._token_hash(token),
                    user_id,
                    issued,
                    issued + SESSION_TTL_SECONDS,
                    (user_agent or "")[:200],
                ),
            )
        return token

    def resolve_session(self, token: Optional[str]) -> Optional[dict[str, Any]]:
        """The signed-in user for a session token, or None. Expired tokens die."""
        if not token:
            return None
        digest = self._token_hash(token)
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM sessions WHERE token_hash = ?", (digest,)
            ).fetchone()
            if row is None:
                return None
            if float(row["expires_at"]) <= time.time():
                conn.execute("DELETE FROM sessions WHERE token_hash = ?", (digest,))
                return None
            user = conn.execute("SELECT * FROM users WHERE id = ?", (row["user_id"],)).fetchone()
            return dict(user) if user else None

    def delete_session(self, token: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM sessions WHERE token_hash = ?", (self._token_hash(token),))

    def purge_expired(self) -> int:
        with self._lock, self._connect() as conn:
            cursor = conn.execute("DELETE FROM sessions WHERE expires_at <= ?", (time.time(),))
            return cursor.rowcount


def public_profile(user: dict[str, Any]) -> dict[str, Any]:
    """
    The user as the client is allowed to see it.

    Deliberately explicit rather than `dict(user)`: the row also holds
    `password_hash`, and a projection that forgets to drop it would hand the
    password digest to the browser the first time someone added a field.
    """
    return {
        "id": user["id"],
        "email": user["email"],
        "full_name": user.get("full_name") or "",
        "contact_number": user.get("contact_number") or "",
        "has_card": bool(user.get("card_last4")),
        "card_brand": user.get("card_brand") or "",
        "card_last4": user.get("card_last4") or "",
        "card_added_at": user.get("card_added_at"),
        "created_at": user.get("created_at"),
    }
