"""
NLP Entity Extraction Pipeline — STUB
Member 2 — NLP & Information Retrieval Lead

TODO (Member 2):
  - Implement extract_transit_intent() using instructor + litellm
  - Add RapidFuzz station name normalization
  - Expand SL_STATIONS with full station list
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


class ParsedTransitQuery(BaseModel):
    """Structured entity output from NLP extraction."""

    intent: Literal["PLAN_ROUTE", "CHECK_SEAT", "GENERAL_FAQ"] = Field(
        default="PLAN_ROUTE",
        description="Core intent of the commuter.",
    )
    origin: Optional[str] = Field(default=None, description="Starting station or town")
    destination: Optional[str] = Field(default=None, description="Target station or town")
    mode: Optional[Literal["TRAIN", "BUS", "ANY"]] = Field(default="ANY")
    travel_class: Optional[str] = Field(default=None)
    departure_date: str = Field(default="TODAY")
    departure_time: Optional[str] = Field(default=None)
    passengers: int = Field(default=1)


# Known Sri Lankan station vocabulary for fuzzy matching
SL_STATIONS: list[str] = [
    "Colombo Fort", "Maradana", "Ragama", "Gampaha", "Veyangoda",
    "Polgahawela", "Peradeniya", "Kandy", "Hatton", "Nanu Oya",
    "Ella", "Badulla", "Galle", "Matara", "Hikkaduwa", "Aluthgama",
    "Kalutara", "Panadura", "Moratuwa", "Mount Lavinia",
    "Makumbura MMC", "Kadawatha", "Katunayake Airport", "Jaffna",
    "Anuradhapura", "Kurunegala", "Maho", "Vavuniya", "Kilinochchi",
]


def normalize_station_name(extracted_name: Optional[str]) -> Optional[str]:
    """
    Resolves colloquial station names using RapidFuzz fuzzy matching.
    TODO (Member 2): Install rapidfuzz and implement full version.
    """
    if not extracted_name:
        return None
    synonyms = {
        "kotuwa": "Colombo Fort",
        "fort": "Colombo Fort",
        "kotte": "Rajagiriya",
        "airport": "Katunayake Airport",
        "makumbura": "Makumbura MMC",
    }
    cleaned = extracted_name.strip().lower()
    if cleaned in synonyms:
        return synonyms[cleaned]
    # Direct match check (case-insensitive)
    for station in SL_STATIONS:
        if station.lower() == cleaned:
            return station
    return extracted_name


def extract_transit_intent(user_query: str) -> ParsedTransitQuery:
    """
    Parses a user query into structured transit entities.
    TODO (Member 2): Replace stub with instructor + LiteLLM implementation.
    """
    # ── STUB: basic keyword extraction ────────────────────────────────────────
    query_lower = user_query.lower()
    mode: Literal["TRAIN", "BUS", "ANY"] = "ANY"
    if "train" in query_lower or "rail" in query_lower:
        mode = "TRAIN"
    elif "bus" in query_lower or "coach" in query_lower:
        mode = "BUS"

    date = "TOMORROW" if any(w in query_lower for w in ["heta", "tomorrow"]) else "TODAY"

    return ParsedTransitQuery(
        intent="PLAN_ROUTE",
        origin=None,
        destination=None,
        mode=mode,
        departure_date=date,
    )

