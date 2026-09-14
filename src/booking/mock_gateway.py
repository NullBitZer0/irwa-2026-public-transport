"""
Mock API Gateway — Simulated SLR & SLTB Booking Portals
Member 3 — Execution Engine & Security Lead

Simulates responses from:
  - seatreservation.railway.gov.lk  (Sri Lanka Railways)
  - sltb.eseat.lk                   (SLTB eSeat)
"""

from __future__ import annotations

import random
import uuid
from typing import Any


class MockTransitGateway:
    """
    Mocks the request/response schema of official SLR and SLTB booking portals.
    No real network calls are made. No ToS are violated.
    """

    SEAT_LABELS = [
        "12A", "12B", "14A", "14B", "16A", "16B",
        "18A", "18B", "20A", "20B",
    ]

    @staticmethod
    def check_seat_inventory(route_id: str) -> dict[str, Any]:
        """Returns a mock seat availability response."""
        available = random.randint(3, 18)
        seats = random.sample(MockTransitGateway.SEAT_LABELS, min(4, available))
        return {
            "route_id": route_id,
            "available_seats": available,
            "seat_numbers": seats,
            "status": "AVAILABLE" if available > 0 else "SOLD_OUT",
        }

    @staticmethod
    def execute_partner_booking(
        provider: str,
        route_id: str,
        real_nic: str,  # noqa: ARG004 — real value used only here, not logged
        seats: int,
    ) -> str:
        """
        Simulates booking confirmation and returns a reference number.
        In production: would make an HTTPS POST to the partner API.
        """
        prefix = "SLR" if provider.upper() == "SLR" else "SLTB"
        ref = f"{prefix}-2026-{uuid.uuid4().hex[:6].upper()}"
        return ref

