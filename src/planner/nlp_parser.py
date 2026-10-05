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
    "Kalutara", "Panadura", "Moratuwa", "Mount Lavinia", "Ambalangoda",
    "Makumbura MMC", "Kadawatha", "Katunayake Airport", "Jaffna",
    "Anuradhapura", "Kurunegala", "Maho", "Vavuniya", "Kilinochchi",
]

STATION_ALIASES: dict[str, str] = {
    # Colombo Fort — English, short forms and common Singlish spellings
    "colombo": "Colombo Fort", "fort": "Colombo Fort", "kotuwa": "Colombo Fort",
    "kolamba": "Colombo Fort", "kolumba": "Colombo Fort", "kollamba": "Colombo Fort",
    "maradana": "Maradana", "ragama": "Ragama", "gampaha": "Gampaha",
    "veyangoda": "Veyangoda", "polgahawela": "Polgahawela",
    "peradeniya": "Peradeniya", "kandy": "Kandy", "hatton": "Hatton",
    "nanuoya": "Nanu Oya", "nanu": "Nanu Oya", "oya": "Nanu Oya",
    "ella": "Ella", "badulla": "Badulla", "galle": "Galle", "matara": "Matara",
    "hikkaduwa": "Hikkaduwa", "aluthgama": "Aluthgama", "kalutara": "Kalutara",
    "panadura": "Panadura", "moratuwa": "Moratuwa", "mountlavinia": "Mount Lavinia",
    "ambalangoda": "Ambalangoda",
    "makumbura": "Makumbura MMC", "mmc": "Makumbura MMC", "kadawatha": "Kadawatha",
    "airport": "Katunayake Airport", "katunayake": "Katunayake Airport",
    "jaffna": "Jaffna", "anuradhapura": "Anuradhapura", "kurunegala": "Kurunegala",
    "maho": "Maho", "vavuniya": "Vavuniya", "kilinochchi": "Kilinochchi",
}

# Singlish direction markers (Sinhala words typed in English letters).
# The marker sits BEFORE the origin/destination name it introduces for
# "from"-style words ("X indan" = from X) and AFTER for "to"-style words.
ORIGIN_MARKERS_BEFORE: set[str] = {
    "indan", "inda", "idala", "indala", "idda", "sita", "ida",  # = from
}
ORIGIN_MARKERS_AFTER: set[str] = {"from"}
DEST_MARKERS_BEFORE: set[str] = {
    # = to go / to come (Kandy yanna, Kandy yanawa, Kandy yanne)
    "yanna", "yan", "yanawa", "yanne", "enawa", "enna", "enne",
}
DEST_MARKERS_AFTER: set[str] = {"to"}

# Mode keywords — English plus common Singlish transliterations
# ("bas" = bus, "dumriya" = train, "relya" = rail). Matched as whole words so
# "express" does not collide with "expressway" (an expressway is a BUS route).
TRAIN_KEYWORDS: set[str] = {"train", "rail", "railway", "menike", "odyssey", "express",
                            "dumriya", "dumri", "relya", "rela"}
BUS_KEYWORDS: set[str] = {"bus", "coach", "sltb", "highway", "expressway", "bas"}
# Date keywords: "heta" = tomorrow, "ada" = today (Singlish)
TOMORROW_KEYWORDS: set[str] = {"tomorrow", "heta"}
TODAY_KEYWORDS: set[str] = {"today", "ada"}

# Matches "8", "08:30", "8.30", "6ta" (Singlish "at 6"), "7pm", "9 wadiya" (= 9 o'clock)
TIME_PATTERN = re.compile(
    r"\b([01]?\d|2[0-3])[:.]?([0-5]\d)?\s?(am|pm|ta|wadiya)?\b", re.IGNORECASE,
)


def _has_word(text: str, words: set[str]) -> bool:
    """True if any word in `words` appears in `text` as a whole word."""
    return any(re.search(rf"\b{re.escape(w)}\b", text) for w in words)


def _tokenize(text: str) -> list[str]:
    """Lowercase, strip punctuation, split into whole tokens (keeps direction markers)."""
    return [t for t in re.sub(r"[^\w\s]", " ", text.lower()).split() if t]


def _station_at_index(tokens: list[str]) -> dict[int, str]:
    """Map every token index covered by a station alias to the canonical station name."""
    mapping: dict[int, str] = {}
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token in STATION_ALIASES:
            mapping[i] = STATION_ALIASES[token]
            i += 1
            continue
        if i + 1 < len(tokens):
            combo = token + tokens[i + 1]
            if combo in STATION_ALIASES:
                mapping[i] = mapping[i + 1] = STATION_ALIASES[combo]
                i += 2
                continue
        i += 1
    return mapping


def _nearest_station(
    tokens: list[str], station_at: dict[int, str], idx: int, step: int
) -> Optional[str]:
    """Walk `step` direction from `idx` and return the first station encountered."""
    j = idx + step
    while 0 <= j < len(tokens):
        if j in station_at:
            return station_at[j]
        j += step
    return None


def _detect_origin_destination(raw_query: str) -> tuple[Optional[str], Optional[str]]:
    """
    Detect origin/destination from English AND Singlish direction markers.

    English:  "from X to Y" / "to Y from X" / "X to Y" / "go to Y"
    Singlish: "X indan Y yanna" (= to Y from X), "Kandy yanna" (= to Kandy),
              "X sita Y yanawa", "X idala Y yanna" (= via/from X to Y)

    Markers naming the origin may appear before it (Singlish "X indan") or
    after it (English "from X"); destination markers work the same way.
    """
    tokens = _tokenize(raw_query)
    if not tokens:
        return None, None
    station_at = _station_at_index(tokens)

    origin: Optional[str] = None
    destination: Optional[str] = None

    # Origin: walk markers left-to-right, first hit wins.
    for i, token in enumerate(tokens):
        if origin is not None:
            break
        if token in ORIGIN_MARKERS_BEFORE:
            origin = _nearest_station(tokens, station_at, i, -1)
        elif token in ORIGIN_MARKERS_AFTER:
            origin = _nearest_station(tokens, station_at, i, +1)

    # Destination: same, independent of origin resolution.
    for i, token in enumerate(tokens):
        if destination is not None:
            break
        if token in DEST_MARKERS_BEFORE:
            destination = _nearest_station(tokens, station_at, i, -1)
        elif token in DEST_MARKERS_AFTER:
            destination = _nearest_station(tokens, station_at, i, +1)

    # English "X to Y" without "from": the station before "to" is the origin.
    if origin is None and destination is not None:
        for i, token in enumerate(tokens):
            if token in DEST_MARKERS_AFTER:
                before = _nearest_station(tokens, station_at, i, -1)
                if before is not None and before != destination:
                    origin = before
                break

    # Fill a missing side from an unused station in mention order
    # (e.g. "Colombo indan Kandy" has no destination marker).
    if origin is None or destination is None:
        ordered = list(dict.fromkeys(station_at[k] for k in sorted(station_at)))
        used = {s for s in (origin, destination) if s}
        unused = [s for s in ordered if s not in used]
        if origin is None and len(unused) == 1:
            origin = unused[0]
        elif destination is None and len(unused) == 1:
            destination = unused[0]
        elif origin is None and destination is None and len(ordered) >= 2:
            origin, destination = ordered[0], ordered[1]
        elif origin is None and destination is None and ordered:
            origin = ordered[0]

    # A journey needs two distinct endpoints; drop degenerate pairs.
    if origin is not None and origin == destination:
        destination = None

    return origin, destination


def extract_transit_intent(user_query: str) -> ParsedTransitQuery:
    """
    Simplify-first rule-based extraction (English + Singlish).
    1. Detect origin/destination via direction markers ("from/to", "indan/yanna")
    2. Word-boundary keyword matching for mode, date, time and intent
    """
    text_lower = user_query.strip().lower()

    # Mode
    mode: Literal["TRAIN", "BUS", "ANY"] = "ANY"
    if _has_word(text_lower, TRAIN_KEYWORDS):
        mode = "TRAIN"
    elif _has_word(text_lower, BUS_KEYWORDS):
        mode = "BUS"

    # Date
    if _has_word(text_lower, TOMORROW_KEYWORDS):
        date = "TOMORROW"
    elif _has_word(text_lower, TODAY_KEYWORDS):
        date = "TODAY"
    else:
        date = "TODAY"

    # Time
    time_match = TIME_PATTERN.search(text_lower)
    departure_time = None
    if time_match and (time_match.group(2) or time_match.group(3)):
        departure_time = f"{int(time_match.group(1)):02d}:{time_match.group(2) or '00'}"

    # Intent
    intent: Literal["PLAN_ROUTE", "CHECK_SEAT", "GENERAL_FAQ"] = "PLAN_ROUTE"
    if _has_word(text_lower, {"refund", "baggage", "policy", "cancel"}):
        intent = "GENERAL_FAQ"
    elif _has_word(text_lower, {"seat", "available"}):
        intent = "CHECK_SEAT"

    # Stations (English + Singlish direction markers, with fallback ordering)
    origin, destination = _detect_origin_destination(user_query)

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
Singlish glossary:
- 'indan' / 'idala' / 'sita' = FROM; 'yanna' / 'yanawa' = TO (Kandy yanna = to Kandy)
- 'heta' = TOMORROW; 'ada' = TODAY; 'ude' = morning
- 'ekak' = a/an; 'thiyeda' = is there; 'balanna' = check/look
- 'bas' = bus; 'dumriya' / 'relya' = train; 'kotuwa' / 'kolamba' = Colombo Fort
Example: "Heta ude Colombo indan Kandy yanna train ekak thiye ne?"
  -> origin=Colombo Fort, destination=Kandy, mode=TRAIN, departure_date=TOMORROW
Rules: If a field is not mentioned, use null (or "ANY"/"TODAY" as shown above)."""


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
