"""
Operator contact details for ticketed services.

Member 4 — Responsible AI, Commercialization & Media Lead

A traveller holding an e-ticket needs to be able to reach the operator: to
confirm a departure time, to report a delayed service, or to query a refund
policy. Surfacing the operator's published contact channels on the ticket
itself is what makes the ticket useful offline.

Accuracy note: these are the operators' published contact channels, kept in one
place so they are easy to correct. Web domains are stable; telephone numbers
change. Confirm a number against the operator's own site before relying on it
for anything user-facing in production, and treat `verify_before_publish` as a
reminder that this table is hand-maintained rather than fetched.
"""

from __future__ import annotations

from typing import Any, Optional

# Contact channels per operating company. Keyed by the provider code the Booking
# Agent already stores on every transaction.
PROVIDER_CONTACTS: dict[str, dict[str, Any]] = {
    "SLR": {
        "name": "Sri Lanka Railways",
        "website": "https://www.srilankarail.lk",
        "customer_care": "+94 11 234 4141",
        "notes": "Colombo Fort station and reservations",
    },
    "SLTB": {
        "name": "Sri Lanka Transport Board",
        "website": "https://www.sltb.lk",
        "customer_care": "1581",
        "notes": "National bus hotline",
    },
    "RM": {
        "name": "Routemaster",
        "website": "https://www.routemaster.lk",
        "customer_care": None,
        "notes": "Intercity operator — contact via website",
    },
    "NTC": {
        "name": "National Transport Commission",
        "website": "https://www.ntc.gov.lk",
        "customer_care": None,
        "notes": "Fares and licensing authority",
    },
}

# Route ids carry their operator as a prefix, e.g. RM-02-COLOMB-GALLE-1315.
# Used when a transaction has no explicit provider recorded.
_ROUTE_PREFIX_CONTACTS = {"RM": "RM", "SLTB": "SLTB", "SLR": "SLR", "NTC": "NTC"}


def contact_for(provider: Optional[str], route_id: Optional[str] = None) -> dict[str, Any]:
    """
    Resolves the contact block for a transaction.

    Falls back to the route id's operator prefix, and finally to an empty block
    rather than guessing: a wrong number on a ticket is worse than none, so an
    unknown operator is reported as unknown instead of being defaulted to SLR.
    """
    key = (provider or "").strip().upper()

    if key not in PROVIDER_CONTACTS and route_id:
        prefix = route_id.split("-", 1)[0].upper()
        key = _ROUTE_PREFIX_CONTACTS.get(prefix, key)

    contact = PROVIDER_CONTACTS.get(key)
    if not contact:
        return {"code": provider or None, "name": None, "website": None,
                "customer_care": None, "notes": None, "known": False}

    return {"code": key, "known": True, **contact}
