"""
Native Sinhala script (Unicode) support.

Member 4 — Responsible AI, Commercialization & Media Lead

Singlish is Sinhala typed with English letters, so it needed no vocabulary and
parity came for free. Native Sinhala script did not: the parser and the retriever
both match on Latin text, so a Sinhala query scored nothing and fell through to
"no results" — which a traveller reads as "no such service exists" rather than
"we did not understand the language".

These tests pin the translation step. The parity requirement itself lives in
`evaluation/test_fairness.py`.

Run:
    pytest evaluation/test_sinhala_script.py -v
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.planner.nlp_parser import (  # noqa: E402
    extract_transit_intent,
    sinhala_to_latin,
)

# Sinhala script → the Singlish/English equivalent, and what must be extracted.
# Pairs are (sinhala, equivalent, origin, destination, mode, time)
PAIRS = [
    (
        "මහනුවර සිට කොළඹ ගිල",
        "kandy indan colombo train",
        "Kandy", "Colombo Fort", "TRAIN", None,
    ),
    (
        "මහනුවර සිට යාපන ගිල",
        "kandy indan jaffna train",
        "Kandy", "Jaffna", "TRAIN", None,
    ),
    (
        "යාපන යන බස්",
        "jaffna yanna bus",
        None, "Jaffna", "BUS", None,
    ),
]


def _ids() -> list[str]:
    """Readable test ids: the Sinhala query is the thing under test."""
    return [pair[0] for pair in PAIRS]


@pytest.mark.parametrize("pair", PAIRS, ids=_ids())
def test_translation_matches_the_singlish_equivalent(pair) -> None:
    sinhala, equivalent = pair[0], pair[1]

    assert sinhala_to_latin(sinhala) == equivalent


@pytest.mark.parametrize("pair", PAIRS, ids=_ids())
def test_parse_matches_the_singlish_equivalent(pair) -> None:
    sinhala, _equivalent, origin, destination, mode = pair[:5]
    parsed = extract_transit_intent(sinhala)

    assert parsed.origin == origin
    assert parsed.destination == destination
    assert parsed.mode == mode


@pytest.mark.parametrize("pair", PAIRS, ids=_ids())
def test_sinhala_and_singlish_parse_identically(pair) -> None:
    """
    The point of the translation: same route, whichever script it arrives in.

    Asserting against the Singlish parse rather than hand-written expectations
    means the two languages cannot drift apart.
    """
    sinhala, equivalent = pair[0], pair[1]

    assert extract_transit_intent(sinhala) == extract_transit_intent(equivalent)


def test_direction_is_not_reversed() -> None:
    """
    Sinhala සිට follows the place it departs from; English "from" precedes it.

    Translating සිට to the English "from" silently reversed the journey, so the
    marker maps to the Singlish spelling instead.
    """
    parsed = extract_transit_intent("මහනුවර සිට කොළඹ ගිල")

    assert (parsed.origin, parsed.destination) == ("Kandy", "Colombo Fort")


def test_time_is_parsed_from_sinhala_numerals() -> None:
    """හෙට උදේ 6ට must yield TOMORROW 06:00, not a missing time."""
    parsed = extract_transit_intent("මහනුවර සිට කොළඹ ගිල හෙට උදේ 6ට")

    assert parsed.departure_time == "06:00"
    assert parsed.departure_date == "TOMORROW"


@pytest.mark.parametrize("digit, expected", [("෦", "0"), ("෧", "1"), ("෨", "2"), ("෯", "9")])
def test_sinhala_numerals_become_digits(digit: str, expected: str) -> None:
    assert sinhala_to_latin(f"පස්සේ {digit}ට").endswith(f"{expected}ta")


def test_english_and_singlish_are_untouched() -> None:
    """The translation must be a no-op on text that is already Latin."""
    for query in ("Kandy to Colombo train", "Heta ude 6ta Kandy yanna train ekak"):
        assert sinhala_to_latin(query) == query


def test_unknown_sinhala_words_are_left_alone() -> None:
    """
    An unknown word is not transliterated.

    A guessed place name would be worse than an unrecognised one: it would
    produce confident results for the wrong destination.
    """
    out = sinhala_to_latin("අනාකූලන් ගම්")

    assert "අනාකූලන්" in out
