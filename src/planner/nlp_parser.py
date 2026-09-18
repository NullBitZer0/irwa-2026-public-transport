from __future__ import annotations

import json
import os
import re
from typing import Literal, Optional

from dotenv import load_dotenv
from groq import Groq
from pydantic import BaseModel, Field
from rapidfuzz import fuzz, process

load_dotenv()


class ParsedTransitQuery(BaseModel):
    intent: Literal["PLAN_ROUTE", "CHECK_SEAT", "GENERAL_FAQ"] = Field(default="PLAN_ROUTE")
    origin: Optional[str] = Field(default=None)
    destination: Optional[str] = Field(default=None)
    mode: Optional[Literal["TRAIN", "BUS", "ANY"]] = Field(default="ANY")
    departure_date: str = Field(default="TODAY")
    departure_time: Optional[str] = Field(default=None)
    passengers: int = Field(default=1)


# Expand this to match every station in your real train_schedules.json / bus_routes.json
SL_STATIONS: list[str] = [
    "Colombo Fort", "Maradana", "Ragama", "Gampaha", "Veyangoda",
    "Polgahawela", "Peradeniya", "Kandy", "Hatton", "Nanu Oya",
    "Ella", "Badulla", "Galle", "Matara", "Hikkaduwa", "Aluthgama",
    "Kalutara", "Panadura", "Moratuwa", "Mount Lavinia",
    "Makumbura MMC", "Kadawatha", "Katunayake Airport", "Jaffna",
    "Anuradhapura", "Kurunegala", "Maho", "Vavuniya", "Kilinochchi",
]

STATION_SYNONYMS = {
    "kotuwa": "Colombo Fort",
    "fort": "Colombo Fort",
    "kotte": "Rajagiriya",
    "airport": "Katunayake Airport",
    "makumbura": "Makumbura MMC",
    "mmc": "Makumbura MMC",
}

TOMORROW_WORDS = {"heta", "tomorrow"}
TRAIN_WORDS = {"train", "rail", "railway", "menike", "odyssey", "express"}
BUS_WORDS = {"bus", "coach", "sltb", "highway"}
TIME_PATTERN = re.compile(r"\b([01]?\d|2[0-3])[:.]?([0-5]\d)?\s?(am|pm|ta)?\b", re.IGNORECASE)


def normalize_station_name(extracted_name: Optional[str]) -> Optional[str]:
    """Resolves colloquial/typo'd station names to a canonical SL_STATIONS entry."""
    if not extracted_name:
        return None
    cleaned = extracted_name.strip().lower()
    if cleaned in STATION_SYNONYMS:
        return STATION_SYNONYMS[cleaned]
    for station in SL_STATIONS:
        if station.lower() == cleaned:
            return station
    match, score, _ = process.extractOne(extracted_name, SL_STATIONS, scorer=fuzz.WRatio)
    return match if score >= 70 else extracted_name.title()


def _find_stations_in_text(text_lower: str) -> list[tuple[str, int]]:
    """Finds every known station mentioned in text, earliest position first."""
    found: dict[str, int] = {}
    for station in SL_STATIONS:
        idx = text_lower.find(station.lower())
        if idx != -1 and station not in found:
            found[station] = idx
    for alias, canonical in STATION_SYNONYMS.items():
        idx = text_lower.find(alias)
        if idx == -1 or canonical in found:
            continue
        overlaps_existing = any(
            existing_idx <= idx < existing_idx + len(existing.lower())
            for existing, existing_idx in found.items()
        )
        if not overlaps_existing:
            found[canonical] = idx
    return sorted(found.items(), key=lambda x: x[1])


def extract_transit_intent(user_query: str) -> ParsedTransitQuery:
    """
    Rule-based structured extraction. No API key or internet needed.
    This is the always-available default and fallback for extract_transit_intent_llm().
    """
    text_lower = user_query.strip().lower()

    mode: Literal["TRAIN", "BUS", "ANY"] = "ANY"
    if any(w in text_lower for w in TRAIN_WORDS):
        mode = "TRAIN"
    elif any(w in text_lower for w in BUS_WORDS):
        mode = "BUS"

    date = "TOMORROW" if any(w in text_lower for w in TOMORROW_WORDS) else "TODAY"

    stations_found = _find_stations_in_text(text_lower)
    origin, destination = None, None
    if len(stations_found) >= 2:
        origin, destination = stations_found[0][0], stations_found[1][0]
    elif len(stations_found) == 1:
        origin = stations_found[0][0]

    time_match = TIME_PATTERN.search(text_lower)
    departure_time = None
    if time_match and (time_match.group(2) or time_match.group(3)):
        departure_time = f"{int(time_match.group(1)):02d}:{time_match.group(2) or '00'}"

    intent: Literal["PLAN_ROUTE", "CHECK_SEAT", "GENERAL_FAQ"] = "PLAN_ROUTE"
    if any(w in text_lower for w in ["refund", "baggage", "policy", "cancel"]):
        intent = "GENERAL_FAQ"
    elif any(w in text_lower for w in ["seat", "available"]):
        intent = "CHECK_SEAT"

    return ParsedTransitQuery(
        intent=intent,
        origin=normalize_station_name(origin) if origin else None,
        destination=normalize_station_name(destination) if destination else None,
        mode=mode,
        departure_date=date,
        departure_time=departure_time,
    )


GROQ_SYSTEM_PROMPT = """You are an NLP parser for Sri Lankan public transit queries.
The user may write in English, Sinhala, or Singlish (colloquial Sinhala typed in English letters).
Extract these fields and respond with ONLY a JSON object, nothing else:
{
  "origin": "<station name or null>",
  "destination": "<station name or null>",
  "mode": "TRAIN" | "BUS" | "ANY",
  "departure_date": "TODAY" | "TOMORROW",
  "departure_time": "<HH:MM or null>",
  "intent": "PLAN_ROUTE" | "CHECK_SEAT" | "GENERAL_FAQ"
}
Rules: 'heta' means TOMORROW. 'ada' means TODAY. If a field is not mentioned, use null
(or "ANY"/"TODAY" as shown above)."""


def extract_transit_intent_llm(user_query: str) -> ParsedTransitQuery:
    """
    Groq-backed extraction. Tries Groq first if GROQ_API_KEY is set; falls back
    to the deterministic rule-based extractor on ANY failure (no key, network
    issue, rate limit, malformed response) so a live demo never breaks.
    """
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        return extract_transit_intent(user_query)

    try:
        client = Groq(api_key=api_key)
        response = client.chat.completions.create(
            model="openai/gpt-oss-20b",
            messages=[
                {"role": "system", "content": GROQ_SYSTEM_PROMPT},
                {"role": "user", "content": user_query},
            ],
            temperature=0.0,
            response_format={"type": "json_object"},
        )
        data = json.loads(response.choices[0].message.content)
        return ParsedTransitQuery(
            intent=data.get("intent", "PLAN_ROUTE"),
            origin=normalize_station_name(data.get("origin")),
            destination=normalize_station_name(data.get("destination")),
            mode=data.get("mode", "ANY"),
            departure_date=data.get("departure_date", "TODAY"),
            departure_time=data.get("departure_time"),
        )
    except Exception as e:
        print(f"[nlp_parser] Groq extraction failed ({e}), using rule-based fallback")
        return extract_transit_intent(user_query)

if __name__ == "__main__":
    print("--- Testing rule-based extraction ---")
    test_queries = [
        "Heta ude 6ta Kandy indan Galle yanna train thiyeda?",
        "Early morning train from Colombo Fort to Ella",
        "What is the refund policy if I cancel my ticket?",
    ]
    for q in test_queries:
        result = extract_transit_intent(q)
        print(f"\nQuery: {q}")
        print(f"Extracted: {result.model_dump()}")

    print("\n--- Testing Groq-backed extraction ---")
    groq_result = extract_transit_intent_llm("Heta ude 6ta Kandy indan Galle yanna train thiyeda?")
    print(groq_result.model_dump())

    print("\n--- Available Groq models for your account ---")
    client = Groq(api_key=os.getenv("GROQ_API_KEY"))
    models = client.models.list()
    for m in models.data:
        print(m.id)