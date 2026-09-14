"""
PII Tokenization & Masking
Member 3 — Execution Engine & Security Lead

Complies with Sri Lanka Personal Data Protection Act No. 9 of 2022.
NIC numbers and phone numbers are replaced with ephemeral UUID tokens
before any LLM processing. Tokens are resolved only at the final
mock gateway call inside the Booking Agent.
"""

from __future__ import annotations

import re
import uuid
from typing import Dict


class PIITokenizer:
    """
    Tokenizes Sri Lankan NIC numbers and phone numbers into opaque UUID tokens.

    The vault is session-scoped and held in memory only.
    Tokens are never logged or persisted.
    """

    # Sri Lankan NIC: 9 digits + V/X (old format) or 12 digits (new format)
    NIC_PATTERN = re.compile(r"\b([0-9]{9}[vVxX]|[0-9]{12})\b")
    # Sri Lankan phone: +94XXXXXXXXX or 07XXXXXXXX  (full number captured in group 0)
    PHONE_PATTERN = re.compile(r"(?<!\d)(?:\+94|0)(7[0-9]{8})(?!\d)")

    def __init__(self) -> None:
        # Maps token → real PII value
        self.vault: Dict[str, str] = {}

    def redact(self, text: str) -> str:
        """
        Replaces NIC and phone numbers with secure opaque tokens.
        Must be called on every user message BEFORE LLM processing.
        """

        def replace_nic(match: re.Match) -> str:
            val = match.group(0)
            token = f"TOKEN_NIC_{uuid.uuid4().hex[:8]}"
            self.vault[token] = val
            return token

        def replace_phone(match: re.Match) -> str:
            val = match.group(0)
            token = f"TOKEN_TEL_{uuid.uuid4().hex[:8]}"
            self.vault[token] = val
            return token

        text = self.NIC_PATTERN.sub(replace_nic, text)
        text = self.PHONE_PATTERN.sub(replace_phone, text)
        return text

    def detokenize(self, text_or_token: str) -> str:
        """
        Resolves a token back to its original PII value.
        Only called at the final booking API step, never during LLM inference.
        """
        return self.vault.get(text_or_token, text_or_token)

    def clear(self) -> None:
        """Clears the in-memory vault at session end."""
        self.vault.clear()

