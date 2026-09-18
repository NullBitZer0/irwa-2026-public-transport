"""
PII Tokenization & Field Encryption
Member 3 — Execution Engine & Security Lead

Complies with Sri Lanka Personal Data Protection Act No. 9 of 2022.
NIC numbers, phone numbers, and passport numbers are replaced with
ephemeral UUID tokens BEFORE any LLM processing. The real values are
encrypted with AES-256-GCM before being placed in the in-memory vault,
so even a vault dump does not expose plaintext PII.

Tokens are resolved back to real values only at the final mock gateway
call inside the Booking Agent (see booking/server.py::confirm_booking).
"""

from __future__ import annotations

import re
import uuid
from typing import Dict

from src.security.audit_log import log_security_event
from src.security.encryption import FieldEncryptor


class PIITokenizer:
    """
    Tokenizes Sri Lankan NIC numbers, phone numbers, and passport numbers
    into opaque UUID-style tokens.

    The vault is session-scoped, held in memory only, and stores AES-256-GCM
    encrypted values rather than plaintext. Tokens are never logged.
    """

    # Sri Lankan NIC: 9 digits + V/X (old format) or 12 digits (new format)
    NIC_PATTERN = re.compile(r"\b([0-9]{9}[vVxX]|[0-9]{12})\b")
    # Sri Lankan phone: +94XXXXXXXXX or 07XXXXXXXX (full number captured in group 0)
    PHONE_PATTERN = re.compile(r"(?<!\d)(?:\+94|0)(7[0-9]{8})(?!\d)")
    # Sri Lankan passport (ICAO format): one letter + 7 digits, e.g. N1234567
    PASSPORT_PATTERN = re.compile(r"\b[A-Z][0-9]{7}\b")

    def __init__(self) -> None:
        # Maps token -> AES-256-GCM encrypted value (base64 string)
        self.vault: Dict[str, str] = {}
        self._encryptor = FieldEncryptor()

    def redact(self, text: str) -> str:
        """
        Replaces NIC, phone, and passport numbers with secure opaque tokens.
        Must be called on every user message BEFORE it reaches any LLM.

        Order matters: NIC and passport patterns must run before generic
        digit-based patterns so they don't get partially matched twice.
        """

        def replace_nic(match: re.Match) -> str:
            val = match.group(0)
            token = f"TOKEN_NIC_{uuid.uuid4().hex[:8]}"
            self.vault[token] = self._encryptor.encrypt(val)
            return token

        def replace_phone(match: re.Match) -> str:
            val = match.group(0)
            token = f"TOKEN_TEL_{uuid.uuid4().hex[:8]}"
            self.vault[token] = self._encryptor.encrypt(val)
            return token

        def replace_passport(match: re.Match) -> str:
            val = match.group(0)
            token = f"TOKEN_PPT_{uuid.uuid4().hex[:8]}"
            self.vault[token] = self._encryptor.encrypt(val)
            return token

        before = text
        text = self.NIC_PATTERN.sub(replace_nic, text)
        text = self.PHONE_PATTERN.sub(replace_phone, text)
        text = self.PASSPORT_PATTERN.sub(replace_passport, text)
        if text != before:
            log_security_event("PII_REDACTED", f"fields_redacted={len(self.vault)}")
        return text

    def detokenize(self, text_or_token: str) -> str:
        """
        Resolves a token back to its original PII value by decrypting the
        vault entry. Only called at the final booking API step, never
        during LLM inference. Returns the input unchanged if it isn't a
        known token (safe no-op for already-plain values).
        """
        encrypted_val = self.vault.get(text_or_token)
        if encrypted_val is None:
            return text_or_token
        return self._encryptor.decrypt(encrypted_val)

    def clear(self) -> None:
        """Clears the in-memory vault at session end."""
        self.vault.clear()