"""
Mock Tokenized Payment Gateway
Member 3 — Execution Engine & Security Lead

Simulates a sandbox payment flow (e.g. PayHere, a Sri Lankan payment
processor). No real card data ever enters this system: the frontend
would collect card details directly via the payment provider's own
hosted field/SDK (outside our system entirely, exactly like a real
PCI-DSS-compliant integration works), and only a one-time opaque
checkout token reaches our Booking Agent.

This is why the Booking Agent's threat surface for card data is zero:
it never sees a PAN, CVV, or expiry — only a hash-based reference.
"""

from __future__ import annotations

import hashlib
import uuid
from typing import Any


class MockPaymentGateway:
    """Mocks a tokenized card-payment sandbox. No real network calls."""

    @staticmethod
    def tokenize_card(card_last4: str) -> str:
        """
        Simulates what the payment provider's SDK would hand back after
        securely collecting the full card number on their side.
        Only the last 4 digits are ever passed to us, purely for the
        receipt display — never a full PAN.
        """
        if not (card_last4.isdigit() and len(card_last4) == 4):
            raise ValueError("card_last4 must be exactly 4 digits.")
        salt = uuid.uuid4().hex
        checkout_hash = hashlib.sha256(f"{card_last4}:{salt}".encode()).hexdigest()[:16]
        return f"CHK_{checkout_hash}"

    @staticmethod
    def charge(checkout_token: str, amount_lkr: float) -> dict[str, Any]:
        """Simulates a successful sandbox charge and returns a receipt."""
        if not checkout_token.startswith("CHK_"):
            raise ValueError("Invalid checkout token.")
        return {
            "status": "PAID",
            "checkout_token": checkout_token,
            "amount_lkr": round(amount_lkr, 2),
            "receipt_id": f"PAY-{uuid.uuid4().hex[:8].upper()}",
        }
