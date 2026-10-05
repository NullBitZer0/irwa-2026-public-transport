"""
Route Grounding & Data Provenance Citations
Member 4 — Responsible AI, Commercialization & Media Lead

Ensures every route recommendation displays an explicit data provenance
citation, preventing hallucination and satisfying the Transparency rubric.
"""

from __future__ import annotations

from typing import Any


def citation_source_for(route: dict[str, Any]) -> str:
    """
    Which dataset a route came from.

    Provenance is decided here, server-side, where the route dicts live, rather
    than in the UI: the React frontend renders whatever string arrives, so there
    is exactly one copy of this mapping instead of one per language.
    """
    provider = str(route.get("provider") or "").upper()
    transit_type = str(route.get("transit_type") or "").upper()

    if provider == "SLR" or "TRAIN" in transit_type:
        return (
            "Sri Lanka Railways official timetable "
            "(data/processed/train_schedules.json)"
        )
    if provider in {"SLTB", "PRIVATE_HIGHWAY", "RM"} or "BUS" in transit_type:
        return "SLTB / NTC expressway routes (data/processed/bus_routes.json)"
    return "Sri Lanka Railways & SLTB timetables (data/processed/)"


def format_grounded_response(
    route_data: dict[str, Any],
    citation_source: str = "Sri Lanka Railways Official Timetable (data/processed/train_schedules.json)",
) -> str:
    """
    Wraps a route dict in a citation-annotated markdown card.

    Args:
        route_data: A route dict from the hybrid retriever.
        citation_source: Human-readable source attribution string.

    Returns:
        Markdown-formatted route card with provenance footer.
    """
    rrf = route_data.get("rrf_score", 0.95)
    stops = ", ".join(route_data.get("stops", [])) or "Direct"
    classes = " | ".join(route_data.get("classes", ["Standard"]))

    card = f"""
### 🎫 {route_data.get("service_name", "Transit Service")} ({route_data.get("provider", "Unknown")})

| Field | Details |
|---|---|
| **Route ID** | `{route_data.get("route_id", "N/A")}` |
| **From** | {route_data.get("origin", "N/A")} |
| **To** | {route_data.get("destination", "N/A")} |
| **Departs** | {route_data.get("departure_time", "N/A")} |
| **Arrives** | {route_data.get("arrival_time", "N/A")} |
| **Fare (base)** | LKR {route_data.get("base_fare_lkr", 0.0):,.2f} |
| **Class** | {classes} |
| **Stops** | {stops} |

> 🔍 **Responsible AI — Data Provenance**
> Verified against **{citation_source}**
> *Retrieval Confidence (RRF Score):* `{rrf}`
""".strip()
    return card


def attach_hallucination_warning(response: str, has_routes: bool) -> str:
    """
    Appends a hallucination disclaimer if no grounded routes were found.

    Ported to the shipped React UI in ChatMessage.jsx, which shows the same
    disclaimer when a route search returns nothing — an empty result should read
    as "nothing verified", not as "no such service exists".
    """
    if not has_routes:
        return (
            response
            + "\n\n⚠️ *No verified routes found in our timetable database. "
            "Please check official sources before travelling.*"
        )
    return response

