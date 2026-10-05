"""
Operator contact details for ticketed services.

Member 4 — Responsible AI, Commercialization & Media Lead

A traveller holding an e-ticket needs to be able to reach the operator: to
confirm a departure time, to report a delayed service, or to query a refund
policy. Surfacing the operator's contact channels on the ticket itself is what
makes the ticket useful offline.

⚠️ Phone numbers in this table are **deliberately fake placeholders**.

They are not the operators' real numbers, and they are not numbers that happen
to be wrong. Printing a plausible-looking but incorrect customer-care number on
a ticket is worse than printing none: the traveller trusts it, dials it, and
either reaches the wrong desk or nobody at all. Sri Lanka has no reserved
fictional-number range either, so a "realistic" number would still be a number
that might belong to someone.

Every entry therefore carries `demo_contact: true`, which the UI surfaces as an
explicit "demo — not a real number" label. Websites are left real and correct,
because they are stable and verifiable and remain useful on a ticket.

Before any deployment outside a demo, replace each number with the operator's
published customer-care line and set `demo_contact` to False — at which point the
UI stops labelling it.
"""

from __future__ import annotations

from typing import Any, Optional

# Contact channels per operating company, keyed by the provider code the Booking
# Agent already stores on every transaction.
PROVIDER_CONTACTS: dict[str, dict[str, Any]] = {
    "SLR": {
        "name": "Sri Lanka Railways",
        "website": "https://www.srilankarail.lk",
        # Placeholder, in a deliberately un-dialable form.
        "customer_care": "+94 11 000 0000",
        "notes": "Reservations and timetable queries",
        "demo_contact": True,
    },
    "SLTB": {
        "name": "Sri Lanka Transport Board",
        "website": "https://www.sltb.lk",
        "customer_care": "1000",
        "notes": "National bus hotline",
        "demo_contact": True,
    },
    "RM": {
        "name": "Routemaster",
        "website": "https://www.routemaster.lk",
        "customer_care": None,
        "notes": "Intercity operator — contact via website",
        "demo_contact": True,
    },
    "NTC": {
        "name": "National Transport Commission",
        "website": "https://www.ntc.gov.lk",
        "customer_care": None,
        "notes": "Fares and licensing authority",
        "demo_contact": True,
    },
}

# Route ids carry their operator as a prefix, e.g. RM-02-COLOMB-GALLE-1315.
# Used when a transaction has no explicit provider recorded.
_ROUTE_PREFIX_CONTACTS = {"RM": "RM", "SLTB": "SLTB", "SLR": "SLR", "NTC": "NTC"}

_EMPTY = {
    "code": None,
    "name": None,
    "website": None,
    "customer_care": None,
    "notes": None,
    "known": False,
    "demo_contact": False,
}


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
        return {"code": provider or None, **_EMPTY}

    return {"code": key, "known": True, **contact}
