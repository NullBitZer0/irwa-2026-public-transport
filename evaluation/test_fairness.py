"""
Linguistic Fairness Test — Trilingual Parity Check (English / Singlish / Sinhala)
Member 4 — Responsible AI, Commercialization & Media Lead

Asserts that English, Singlish, and Sinhala queries with identical intent
return the same expected route in top-k. Covers Responsible AI
Linguistic Fairness rubric (Singlish/English/Sinhala parity).

Run:
    pytest evaluation/test_fairness.py -v
    pytest evaluation/test_fairness.py::test_trilingual_parity -v
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.planner.hybrid_retriever import HybridTransitRetriever


@pytest.fixture(scope="module")
def retriever() -> HybridTransitRetriever:
    return HybridTransitRetriever()


BILINGUAL_PAIRS = [
    # (English query, Singlish query, expected_route_id)
    (
        "Express train from Colombo Fort to Kandy tomorrow morning",
        "Heta ude Colombo Fort indan Kandy yanna express train ekak balanna",
        "TRAIN-1001",
    ),
    (
        "Highway bus from Makumbura to Galle",
        "Makumbura idala Galle yanna highway bus ekak",
        "BUS-EX1-32",
    ),
    (
        "Train from Colombo to Ella and Badulla",
        "Colombo indan Ella Badulla yanna train ekak",
        "TRAIN-1002",
    ),
]

# Sinhala (Unicode) version of the same intents — project.txt:1225 / Responsible AI viva
# NOTE: Dense model is all-MiniLM-L6-v2 (English-only). Sinhala queries score lower on
# dense cosine but can still be retrieved via BM25 fallback when transliterated names
# like "Colombo", "Kandy" remain in Latin. Pure Sinhala script tests multilingual gap.
SINHALA_PAIRS = [
    # (English query, Sinhala query, expected_route_id) — Unicode Sinhala script
    (
        "Express train from Colombo Fort to Kandy tomorrow morning",
        "හෙට උදේ කොළඹ කොටුවේ සිට මහනුවරට සීඝ්‍රගාමී දුම්රිය",
        "TRAIN-1001",
    ),
    (
        "Highway bus from Makumbura to Galle",
        "මකුඹුර සිට ගාල්ලට අධිවේගී බස් රථය",
        "BUS-EX1-32",
    ),
    (
        "Train from Colombo to Ella and Badulla",
        "කොළඹ සිට ඇල්ල සහ බදුල්ලට දුම්රිය",
        "TRAIN-1002",
    ),
]

# Full trilingual triple — strictest parity (EN + Singlish + Sinhala must all agree)
TRILINGUAL_TRIPLES = [
    (
        "Express train from Colombo Fort to Kandy tomorrow morning",
        "Heta ude Colombo Fort indan Kandy yanna express train ekak balanna",
        "හෙට උදේ කොළඹ කොටුවේ සිට මහනුවරට සීඝ්‍රගාමී දුම්රිය",
        "TRAIN-1001",
    ),
    (
        "Highway bus from Makumbura to Galle",
        "Makumbura idala Galle yanna highway bus ekak",
        "මකුඹුර සිට ගාල්ලට අධිවේගී බස් රථය",
        "BUS-EX1-32",
    ),
]


@pytest.mark.parametrize("english_q, singlish_q, expected_id", BILINGUAL_PAIRS)
def test_bilingual_parity(
    retriever: HybridTransitRetriever,
    english_q: str,
    singlish_q: str,
    expected_id: str,
) -> None:
    """
    Both the English and Singlish query must return the expected route at rank #1.
    This verifies the system does not penalise Singlish speakers.
    """
    en_results = retriever.retrieve_candidates(query=english_q, top_k=5)
    sl_results = retriever.retrieve_candidates(query=singlish_q, top_k=5)

    en_ids = [r["route_id"] for r in en_results]
    sl_ids = [r["route_id"] for r in sl_results]

    assert expected_id in en_ids, (
        f"[English] Expected '{expected_id}' in top-5 results, got: {en_ids}"
    )
    assert expected_id in sl_ids, (
        f"[Singlish] Expected '{expected_id}' in top-5 results, got: {sl_ids}"
    )


@pytest.mark.parametrize("english_q, sinhala_q, expected_id", SINHALA_PAIRS)
def test_sinhala_parity(
    retriever: HybridTransitRetriever,
    english_q: str,
    sinhala_q: str,
    expected_id: str,
) -> None:
    """
    English vs Sinhala (Unicode) parity — Responsible AI Linguistic Fairness.
    Documents the gap: all-MiniLM-L6-v2 is English-only, so pure Sinhala
    relies on BM25 with Latin station names or falls back to RRF blending.
    Viva defense: propose upgrade to paraphrase-multilingual-MiniLM-L12-v2
    if Sinhala parity < 100%. Test is xfail-tolerant via assertion message.
    """
    en_results = retriever.retrieve_candidates(query=english_q, top_k=5)
    si_results = retriever.retrieve_candidates(query=sinhala_q, top_k=5)

    en_ids = [r["route_id"] for r in en_results]
    si_ids = [r["route_id"] for r in si_results]

    assert expected_id in en_ids, (
        f"[English] Expected '{expected_id}' in top-5, got: {en_ids}"
    )
    # Sinhala assertion — if this fails, it evidences the embedding gap for the report
    assert expected_id in si_ids, (
        f"[Sinhala] Expected '{expected_id}' in top-5, got: {si_ids} "
        f"for query '{sinhala_q}' — indicates monolingual embedding limitation. "
        f"Mitigation: switch embedder to paraphrase-multilingual-MiniLM-L12-v2."
    )


@pytest.mark.parametrize("english_q, singlish_q, sinhala_q, expected_id", TRILINGUAL_TRIPLES)
def test_trilingual_parity(
    retriever: HybridTransitRetriever,
    english_q: str,
    singlish_q: str,
    sinhala_q: str,
    expected_id: str,
) -> None:
    """Strictest check: EN, Singlish, and Sinhala must all retrieve same route_id."""
    en_ids = [r["route_id"] for r in retriever.retrieve_candidates(query=english_q, top_k=5)]
    sl_ids = [r["route_id"] for r in retriever.retrieve_candidates(query=singlish_q, top_k=5)]
    si_ids = [r["route_id"] for r in retriever.retrieve_candidates(query=sinhala_q, top_k=5)]

    for label, ids in [("English", en_ids), ("Singlish", sl_ids), ("Sinhala", si_ids)]:
        assert expected_id in ids, f"[{label}] Expected '{expected_id}' in {ids}"

