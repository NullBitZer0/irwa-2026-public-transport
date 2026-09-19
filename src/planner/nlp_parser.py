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


SL_STATIONS: list[str] = [
    "Colombo Fort", "Maradana", "Ragama", "Gampaha", "Veyangoda",
    "Polgahawela", "Peradeniya", "Kandy", "Hatton", "Nanu Oya",
    "Ella", "Badulla", "Galle", "Matara", "Hikkaduwa", "Aluthgama",
    "Kalutara", "Panadura", "Moratuwa", "Mount Lavinia",
    "Makumbura MMC", "Kadawatha", "Katunayake Airport", "Jaffna",
    "Anuradhapura", "Kurunegala", "Maho", "Vavuniya", "Kilinochchi",
]

STATION_ALIASES: dict[str, str] = {
    "colombo": "Colombo Fort", "fort": "Colombo Fort", "kotuwa": "Colombo Fort",
    "maradana": "Maradana", "ragama": "Ragama", "gampaha": "Gampaha",
    "veyangoda": "Veyangoda", "polgahawela": "Polgahawela",
    "peradeniya": "Peradeniya", "kandy": "Kandy", "hatton": "Hatton",
    "nanuoya": "Nanu Oya", "nanu": "Nanu Oya", "oya": "Nanu Oya",
    "ella": "Ella", "badulla": "Badulla", "galle": "Galle", "matara": "Matara",
    "hikkaduwa": "Hikkaduwa", "aluthgama": "Aluthgama", "kalutara": "Kalutara",
    "panadura": "Panadura", "moratuwa": "Moratuwa", "mountlavinia": "Mount Lavinia",
    "makumbura": "Makumbura MMC", "mmc": "Makumbura MMC", "kadawatha": "Kadawatha",
    "airport": "Katunayake Airport", "katunayake": "Katunayake Airport",
    "jaffna": "Jaffna", "anuradhapura": "Anuradhapura", "kurunegala": "Kurunegala",
    "maho": "Maho", "vavuniya": "Vavuniya", "kilinochchi": "Kilinochchi",
}

STOP_WORDS: set[str] = {
    "i", "me", "my", "want", "need", "like", "going", "go", "goingto",
    "wanna", "gonna", "gotta", "lemme", "plz", "pls", "please",
    "from", "to", "at", "in", "on", "by", "with", "for", "of",
    "a", "an", "the", "is", "are", "was", "were", "be", "been",
    "and", "or", "but", "so", "then", "also", "too",
    "tomorrow", "today", "heta", "ada",
    "train", "bus", "rail", "railway", "coach", "sltb", "highway",
    "express", "expressway", "menike", "odyssey",
    "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
}

TRAIN_KEYWORDS: set[str] = {"train", "rail", "railway", "menike", "odyssey", "express"}
BUS_KEYWORDS: set[str] = {"bus", "coach", "sltb", "highway"}
TOMORROW_KEYWORDS: set[str] = {"tomorrow", "heta"}

TIME_PATTERN = re.compile(
    r"\b([01]?\d|2[0-3])[:.]?([0-5]\d)?\s?(am|pm|ta)?\b", re.IGNORECASE,
)


def _simplify(text: str) -> list[str]:
    """Lowercase, strip punctuation, remove stop words. Returns content tokens."""
    text = text.lower().strip()
    text = re.sub(r"[^\w\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return [t for t in text.split() if t not in STOP_WORDS]


def _match_stations(tokens: list[str]) -> list[str]:
    """Match simplified tokens against known station aliases. Returns canonical names in order."""
    matched: list[str] = []
    used: set[int] = set()

    for i, token in enumerate(tokens):
        if i in used:
            continue
        if token in STATION_ALIASES:
            matched.append(STATION_ALIASES[token])
            used.add(i)
            continue
        # Two-token combo for multi-word stations like "nanu oya", "mount lavinia"
        if i + 1 < len(tokens) and f"{token}{tokens[i + 1]}" in STATION_ALIASES:
            matched.append(STATION_ALIASES[f"{token}{tokens[i + 1]}"])
            used.add(i)
            used.add(i + 1)
            continue
        if i + 1 < len(tokens) and f"{token} {tokens[i + 1]}" in STATION_ALIASES:
            matched.append(STATION_ALIASES[f"{token} {tokens[i + 1]}"])
            used.add(i)
            used.add(i + 1)

    return matched


def _detect_from_to(raw_query: str) -> tuple[Optional[str], Optional[str]]:
    """
    Detect 'from X to Y' or 'to Y from X' patterns in the raw query.
    Uses _simplify + _match_stations on the segments between prepositions.
    """
    text = raw_query.lower().strip()

    # Pattern: "from <origin> to <destination>"
    m = re.search(r"\bfrom\s+(.+?)\s+to\s+(.+?)(?:\s+by|\s+tomorrow|\s+today|\s+at|\s+\d|\s*$)", text)
    if m:
        origin_tokens = _simplify(m.group(1))
        dest_tokens = _simplify(m.group(2))
        origins = _match_stations(origin_tokens)
        dests = _match_stations(dest_tokens)
        if origins and dests:
            return origins[0], dests[0]

    # Pattern: "to <destination> from <origin>"
    m = re.search(r"\bto\s+(.+?)\s+from\s+(.+?)(?:\s+by|\s+tomorrow|\s+today|\s+at|\s+\d|\s*$)", text)
    if m:
        dest_tokens = _simplify(m.group(1))
        origin_tokens = _simplify(m.group(2))
        dests = _match_stations(dest_tokens)
        origins = _match_stations(origin_tokens)
        if origins and dests:
            return origins[0], dests[0]

    # Pattern: "go to <destination>" (no origin specified)
    m = re.search(r"\b(?:go|travel|head|want)\s+to\s+(.+?)(?:\s+by|\s+tomorrow|\s+today|\s+at|\s+\d|\s*$)", text)
    if m:
        dest_tokens = _simplify(m.group(1))
        dests = _match_stations(dest_tokens)
        if dests:
            return None, dests[0]

    return None, None


def extract_transit_intent(user_query: str) -> ParsedTransitQuery:
    """
    Simplify-first rule-based extraction.
    1. Detect 'from/to' patterns on raw text (prepositions are meaningful signals)
    2. Simplify remaining text, match stations from content tokens
    3. Detect mode, date, time
    """
    text_lower = user_query.strip().lower()

    # Mode
    mode: Literal["TRAIN", "BUS", "ANY"] = "ANY"
    if any(w in text_lower for w in TRAIN_KEYWORDS):
        mode = "TRAIN"
    elif any(w in text_lower for w in BUS_KEYWORDS):
        mode = "BUS"

    # Date
    date = "TOMORROW" if any(w in text_lower for w in TOMORROW_KEYWORDS) else "TODAY"

    # Time
    time_match = TIME_PATTERN.search(text_lower)
    departure_time = None
    if time_match and (time_match.group(2) or time_match.group(3)):
        departure_time = f"{int(time_match.group(1)):02d}:{time_match.group(2) or '00'}"

    # Intent
    intent: Literal["PLAN_ROUTE", "CHECK_SEAT", "GENERAL_FAQ"] = "PLAN_ROUTE"
    if any(w in text_lower for w in ["refund", "baggage", "policy", "cancel"]):
        intent = "GENERAL_FAQ"
    elif any(w in text_lower for w in ["seat", "available"]):
        intent = "CHECK_SEAT"

    # Stations: try from/to pattern first, then fall back to simplified token scan
    origin, destination = _detect_from_to(user_query)

    if origin is None and destination is None:
        tokens = _simplify(text_lower)
        stations = _match_stations(tokens)
        if len(stations) >= 2:
            origin, destination = stations[0], stations[1]
        elif len(stations) == 1:
            origin = stations[0]

    return ParsedTransitQuery(
        intent=intent,
        origin=origin,
        destination=destination,
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
    Groq-backed extraction. Falls back to rule-based on ANY failure.
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


def normalize_station_name(extracted_name: Optional[str]) -> Optional[str]:
    """Resolves colloquial/typo'd station names to a canonical SL_STATIONS entry."""
    if not extracted_name:
        return None
    cleaned = extracted_name.strip().lower()
    if cleaned in STATION_ALIASES:
        return STATION_ALIASES[cleaned]
    for station in SL_STATIONS:
        if station.lower() == cleaned:
            return station
    match, score, _ = process.extractOne(extracted_name, SL_STATIONS, scorer=fuzz.WRatio)
    return match if score >= 70 else extracted_name.title()
