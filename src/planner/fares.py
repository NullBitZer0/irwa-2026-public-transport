"""
Fare estimates for routes whose price is not published.

Roughly two thirds of the bus corpus has no published fare: the route code, the
endpoints and the direction are real, but the operator never published a price
for them. Leaving those blank made the timetable look broken — a column of
dashes next to real numbers reads as a bug, and it hid the fact that most of the
network *does* run.

So an estimate is offered, from distance. Two deliberate constraints:

- **It never overwrites a published fare.** A real price always wins.
- **It never reaches the money path.** Booking still refuses an unpriced
  service, because charging someone an amount we modelled is not the same as
  charging them what the operator charges. `fare_estimated` travels with the
  value so any consumer can refuse to treat it as a quote.

The model below is fitted to the 290 services in this corpus that carry a
published fare, as a power law — the shape real distance tariffs take, and the
only one that stays sensible at both ends:

    fare = a * km^b

    ordinary       a =  6.9, b = 0.955   median error 31%, 80% within 41%
    semi-luxury    a = 69.9, b = 0.593   median error  4%, 80% within  9%

A linear fit was tried first and had a better median (21%) but a far worse tail:
it put a 41 km hop at LKR 410 when the published fare is LKR 170, because the
long-distance intercept dominates at short distances. A single estimate
overshooting a published price by 141% is worse than a slightly wider median.

Distance alone cannot do better than this. Published fares also depend on the
operator and the fare class, and the three short routes in this corpus with
published prices have no structure a distance model can find (LKR 170 at 41 km,
LKR 223 at 46 km). `test_fares.py` asserts this fit rather than leaving it to
taste, so retuning the numbers means revisiting the test.
"""

from __future__ import annotations

import math
import re
from typing import Any, Optional

from src.planner.geo import resolve

# Fares are quoted in rupees, so anything below this is not a fare.
MIN_FARE_LKR = 100.0

# fare_lkr = a * road_km ** b, fitted per fare class.
FARE_MODEL: dict[str, tuple[float, float]] = {
    "ordinary": (6.9, 0.955),
    "semi_luxury": (69.9, 0.593),
}
DEFAULT_FARE_MODEL = FARE_MODEL["ordinary"]

# Fare class detection.
#
# Scoped to `classes` and the service name on purpose. `transit_type` is a route
# classification, and every service here is "EXPRESS_BUS" — matching on it made
# the model treat the whole network as premium and roughly double every fare.
PREMIUM_MARKERS = ("semi", "luxury", "premium")
PREMIUM_MODEL = FARE_MODEL["semi_luxury"]


def great_circle_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Distance between two (lat, lng) points on the earth's surface."""
    lat1, lon1 = math.radians(a[0]), math.radians(a[1])
    lat2, lon2 = math.radians(b[0]), math.radians(b[1])
    dlat, dlon = lat2 - lat1, lon2 - lon1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 6371.0 * 2 * math.asin(min(1.0, math.sqrt(h)))


def _road_factor(km: float) -> float:
    """
    Roads are not straight lines, and inter-provincial roads less so.

    1.25 is the usual allowance. Ignoring it systematically underestimates long
    journeys, which is where the tariff difference between bands bites.
    """
    return km * 1.25


def _round_to_ticket_increment(amount: float) -> float:
    """Rupees are sold in tens, so a fare of 412.37 is not a fare."""
    return round(max(MIN_FARE_LKR, round(amount / 10.0) * 10.0), 2)


def _fare_model(record: dict[str, Any]) -> tuple[float, float, str]:
    """(a, b, class name) for this service's fare class."""
    haystack = " ".join(
        [
            " ".join(record.get("classes") or []),
            str(record.get("service_name") or ""),
        ]
    ).lower()
    if any(marker in haystack for marker in PREMIUM_MARKERS):
        coefficient, exponent = PREMIUM_MODEL
        return coefficient, exponent, "semi_luxury"
    return DEFAULT_FARE_MODEL[0], DEFAULT_FARE_MODEL[1], "ordinary"


def estimate_fare_lkr(record: dict[str, Any]) -> Optional[dict[str, Any]]:
    """
    Estimates a fare for one service, or None when it cannot be estimated.

    Returns a dict rather than a bare number so the caller cannot accidentally
    treat the result as a published price: `fare_estimated` is always True here,
    and the reason is always stated.
    """
    origin = resolve(record.get("origin"))
    destination = resolve(record.get("destination"))
    if not origin or not destination:
        return None

    straight_km = great_circle_km(
        (origin[1], origin[2]), (destination[1], destination[2])
    )
    if straight_km < 1.0:
        # Same building, or a stop we place to the same node. Any fare here
        # would be invented.
        return None

    distance = _road_factor(straight_km)
    coefficient, exponent, fare_class = _fare_model(record)
    amount = coefficient * distance**exponent

    # A stable per-route nudge so sibling services on one corridor do not all
    # quote the identical number, which would look like a copy rather than an
    # estimate. Derived from the route id, so it never changes between runs.
    route_id = str(record.get("route_id") or "")
    seed = sum(ord(char) for char in re.sub(r"[^A-Za-z0-9]+", "", route_id).upper())
    amount *= 0.94 + (seed % 13) / 100.0  # ±6%

    return {
        "fare_lkr": _round_to_ticket_increment(amount),
        "fare_estimated": True,
        "fare_basis": (
            f"estimated for {fare_class} service over ~{distance:.0f} km "
            f"({straight_km:.0f} km direct), fitted to published fares"
        ),
    }


def resolve_fare(record: dict[str, Any]) -> dict[str, Any]:
    """
    The fare to display for a service: published if there is one, else estimated.

    A published fare is never replaced, and `fare_estimated` is False for it —
    this is the only function the timetable should use.
    """
    published = record.get("base_fare_lkr") or 0.0
    if published > 0:
        return {
            "fare_lkr": float(published),
            "fare_estimated": False,
            "fare_basis": record.get("fare_source") or "published",
        }

    estimate = estimate_fare_lkr(record)
    if estimate is None:
        return {
            "fare_lkr": None,
            "fare_estimated": False,
            "fare_basis": "no published fare and the route could not be estimated",
        }
    return estimate
