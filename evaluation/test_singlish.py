"""
Singlish Support Test — Bilingual NLP Parser Parity
Student 1 / Member 2 — NLP entity extraction

Asserts that English and Singlish queries with identical intent parse to the
same origin/destination/mode/date, so Singlish speakers are not penalised.

Run:
    pytest evaluation/test_singlish.py -v
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.planner.nlp_parser import extract_transit_intent  # noqa: E402

# ── Bilingual parity: same journey, two languages ────────────────────────────

BILINGUAL_PAIRS = [
    # (English query, Singlish query, expected origin, expected destination)
    (
        "Express train from Colombo Fort to Kandy tomorrow morning",
        "Heta ude Colombo Fort indan Kandy yanna express train ekak balanna",
        "Colombo Fort",
        "Kandy",
    ),
    (
        "Highway bus from Makumbura to Galle",
        "Makumbura idala Galle yanna highway bus ekak",
        "Makumbura MMC",
        "Galle",
    ),
    (
        "Train from Colombo to Badulla",
        "Colombo indan Ella Badulla yanna train ekak",
        "Colombo Fort",
        "Badulla",
    ),
    (
        "Bus from Galle to Matara today",
        "Galle sita Matara bas ekak thiyeda",
        "Galle",
        "Matara",
    ),
]


@pytest.mark.parametrize("english_q, singlish_q, origin, dest", BILINGUAL_PAIRS)
def test_bilingual_origin_destination_parity(
    english_q: str, singlish_q: str, origin: str, dest: str
) -> None:
    """Both languages must yield the same origin and destination."""
    en = extract_transit_intent(english_q)
    sl = extract_transit_intent(singlish_q)

    assert en.origin == origin, f"[English] {english_q!r} -> origin={en.origin!r}"
    assert en.destination == dest, f"[English] {english_q!r} -> dest={en.destination!r}"
    assert sl.origin == origin, f"[Singlish] {singlish_q!r} -> origin={sl.origin!r}"
    assert sl.destination == dest, f"[Singlish] {singlish_q!r} -> dest={sl.destination!r}"


# ── Singlish direction markers ───────────────────────────────────────────────

@pytest.mark.parametrize(
    "query, origin, dest",
    [
        ("Kolamba indan Kandy yanawa", "Colombo Fort", "Kandy"),
        ("Heta ude Colombo indan Kandy yanna train ekak thiye ne?",
         "Colombo Fort", "Kandy"),
        ("Makumbura indan Matara yanna bus ekak balanna", "Makumbura MMC", "Matara"),
        ("Galle indan Kandy enawa", "Galle", "Kandy"),
        ("Colombo idala Matara yanna train ekak", "Colombo Fort", "Matara"),
        ("Jaffna sita Colombo dumriya", "Jaffna", "Colombo Fort"),
    ],
)
def test_singlish_from_to_markers(query: str, origin: str, dest: str) -> None:
    """'indan/idala/sita' must mark origin; 'yanna/yanawa/enawa' must mark destination."""
    parsed = extract_transit_intent(query)
    assert parsed.origin == origin, f"{query!r} -> origin={parsed.origin!r}"
    assert parsed.destination == dest, f"{query!r} -> dest={parsed.destination!r}"


@pytest.mark.parametrize(
    "query, dest",
    [
        ("Kandy yanna train ekak thiyeda?", "Kandy"),
        ("Ella yanna monawada?", "Ella"),
        ("Heta ude 6ta Galle yanna train ekak", "Galle"),
    ],
)
def test_singlish_destination_only(query: str, dest: str) -> None:
    """Destination-only Singlish queries must not mislabel the destination as origin."""
    parsed = extract_transit_intent(query)
    assert parsed.origin is None, f"{query!r} -> unexpected origin={parsed.origin!r}"
    assert parsed.destination == dest, f"{query!r} -> dest={parsed.destination!r}"


def test_origin_fill_without_destination_marker() -> None:
    """'X indan Y' (no yanna) must still fill both endpoints from mention order."""
    parsed = extract_transit_intent("Colombo indan Kandy")
    assert parsed.origin == "Colombo Fort"
    assert parsed.destination == "Kandy"


# ── English direction markers (regression guard) ─────────────────────────────

@pytest.mark.parametrize(
    "query, origin, dest",
    [
        ("Find me a train from Colombo to Kandy", "Colombo Fort", "Kandy"),
        ("How do I get to Jaffna from Colombo Fort?", "Colombo Fort", "Jaffna"),
        ("Colombo to Kandy tomorrow", "Colombo Fort", "Kandy"),
        ("Kandy to Colombo tomorrow 8am", "Kandy", "Colombo Fort"),
        ("I want to go to Kandy from Colombo by train", "Colombo Fort", "Kandy"),
    ],
)
def test_english_direction_markers(query: str, origin: str, dest: str) -> None:
    """English from/to, to/from and bare 'X to Y' patterns must keep working."""
    parsed = extract_transit_intent(query)
    assert parsed.origin == origin, f"{query!r} -> origin={parsed.origin!r}"
    assert parsed.destination == dest, f"{query!r} -> dest={parsed.destination!r}"


def test_multistation_destination_takes_last_before_marker() -> None:
    """'X indan A B yanna' — the station next to 'yanna' is the destination."""
    parsed = extract_transit_intent("Colombo indan Ella Badulla yanna train ekak")
    assert parsed.origin == "Colombo Fort"
    assert parsed.destination == "Badulla"


# ── Mode, date, time, intent ─────────────────────────────────────────────────

@pytest.mark.parametrize(
    "query, mode",
    [
        ("Kandy yanna train ekak", "TRAIN"),
        ("Ella yanna dumriya ekak thiyeda", "TRAIN"),
        ("Colombo indan Kandy rela", "TRAIN"),
        ("Galle yanna bas ekak", "BUS"),
        ("Makumbura idala Galle highway bus ekak", "BUS"),
        ("Kandy yanna expressway ekak", "BUS"),
        ("Colombo Kandy", "ANY"),
    ],
)
def test_singlish_mode_detection(query: str, mode: str) -> None:
    """Singlish mode words ('bas', 'dumriya', 'relya') must map like English ones."""
    parsed = extract_transit_intent(query)
    assert parsed.mode == mode, f"{query!r} -> mode={parsed.mode!r}"


def test_expressway_is_bus_not_train() -> None:
    """'expressway' must not be caught by the substring 'express' as a train."""
    parsed = extract_transit_intent("expressway to Galle")
    assert parsed.mode == "BUS"


@pytest.mark.parametrize(
    "query, date",
    [
        ("Heta ude Kandy yanna train ekak", "TOMORROW"),
        ("Kandy yanna train ekak heta", "TOMORROW"),
        ("Train ekak ada Kandy yanna", "TODAY"),
        ("Find a train tomorrow", "TOMORROW"),
        ("Find a train today", "TODAY"),
        ("Kandy yanna train ekak", "TODAY"),
    ],
)
def test_date_keywords(query: str, date: str) -> None:
    """'heta' = tomorrow, 'ada' = today; default is today."""
    parsed = extract_transit_intent(query)
    assert parsed.departure_date == date, f"{query!r} -> {parsed.departure_date!r}"


@pytest.mark.parametrize(
    "query, time",
    [
        ("Heta ude 6ta Kandy yanna train ekak", "06:00"),
        ("9 wadiya Kandy yanna dumriya ekak", "09:00"),
        ("Colombo to Kandy 8am", "08:00"),
        ("Colombo to Kandy 14:30", "14:30"),
    ],
)
def test_time_keywords(query: str, time: str) -> None:
    """Singlish time suffixes ('6ta', '9 wadiya') must parse like '8am'/'14:30'."""
    parsed = extract_transit_intent(query)
    assert parsed.departure_time == time, f"{query!r} -> {parsed.departure_time!r}"


@pytest.mark.parametrize(
    "query, intent",
    [
        ("Kandy yanna train ekak thiyeda?", "PLAN_ROUTE"),
        ("Ella yanna monawada?", "PLAN_ROUTE"),
        ("What is the refund policy?", "GENERAL_FAQ"),
        ("Baggage rules on SLR?", "GENERAL_FAQ"),
        ("Seat available on the Intercity?", "CHECK_SEAT"),
        ("Kandy yanna seat ekak thiyeda", "CHECK_SEAT"),
    ],
)
def test_intent_keywords(query: str, intent: str) -> None:
    """Intent detection must work for English and Singlish phrasings."""
    parsed = extract_transit_intent(query)
    assert parsed.intent == intent, f"{query!r} -> {parsed.intent!r}"


# ── Station aliases ──────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "alias, canonical",
    [
        ("kolamba", "Colombo Fort"),
        ("kotuwa", "Colombo Fort"),
        ("fort", "Colombo Fort"),
        ("makumbura", "Makumbura MMC"),
        ("airport", "Katunayake Airport"),
    ],
)
def test_station_aliases(alias: str, canonical: str) -> None:
    """Colloquial/Singlish station names must resolve to the canonical station."""
    parsed = extract_transit_intent(f"{alias} indan Kandy yanna train ekak")
    assert parsed.origin == canonical, f"alias {alias!r} -> {parsed.origin!r}"
