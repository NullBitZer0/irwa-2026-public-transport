"""
Mock API Gateway — Simulated SLR & SLTB Booking Portals
Member 3 — Execution Engine & Security Lead

Simulates responses from:
  - seatreservation.railway.gov.lk  (Sri Lanka Railways)
  - sltb.eseat.lk                   (SLTB eSeat)
"""

from __future__ import annotations

import hashlib
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

    # A coach holds a fixed number of seats, so availability has to stay put for
    # the life of the service. Re-rolling it per call meant one coach reported 5
    # free seats and then 12, which is both untrue and impossible to demo: a
    # traveller could be quoted 6 seats and refused 20 seconds later.
    #
    # Keyed on the route id so it is stable within and across runs — the corpus
    # is a committed fixture and a demo that reshuffles on every restart cannot
    # be reasoned about.
    COACH_CAPACITY = {
        "SLR": 120,
        "SLTB": 45,
        "PRIVATE_HIGHWAY": 50,
    }

    # Roughly one service in eight sells out. Without this the sold-out path was
    # unreachable: availability was `randint(3, 18)`, which never returns 0, so
    # the 409 branch could never run and the alternative-service logic it exists
    # to trigger had nothing to trigger on.
    SOLD_OUT_PERCENT = 12

    @staticmethod
    def _inventory_seed(route_id: str) -> int:
        digest = hashlib.sha256(route_id.strip().upper().encode()).hexdigest()
        return int(digest[:8], 16)

    @classmethod
    def check_seat_inventory(cls, route_id: str, seat_count: int = 1) -> dict[str, Any]:
        """
        Seat availability for one service, stable for that service.

        `seat_count` is what the traveller wants, so a partial answer is possible:
        a coach with 3 free seats is AVAILABLE for 1 and SHORT for 6. Reporting
        that distinction is what lets the agent suggest a different service rather
        than simply failing.
        """
        seed = cls._inventory_seed(route_id)
        provider = "SLR" if str(route_id).upper().startswith("TRAIN") else "SLTB"
        capacity = cls.COACH_CAPACITY.get(provider, 45)

        if seed % 100 < cls.SOLD_OUT_PERCENT:
            available = 0
        else:
            # Never zero here, and never below the number that sold it out —
            # a coach with 9 seats left is not the same problem as one with 1.
            available = (seed >> 8) % (capacity - 1) + 1

        # Deterministic pick, so the seat labels do not shuffle either.
        labels = [
            cls.SEAT_LABELS[(seed + index * 7) % len(cls.SEAT_LABELS)]
            for index in range(min(len(cls.SEAT_LABELS), max(1, available // 4)))
        ]
        seats = sorted(set(labels))

        if available == 0:
            status = "SOLD_OUT"
        elif seat_count > available:
            status = "INSUFFICIENT"
        else:
            status = "AVAILABLE"

        return {
            "route_id": route_id,
            "available_seats": available,
            "seat_numbers": seats,
            "status": status,
            "capacity": capacity,
            "requested_seats": seat_count,
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

