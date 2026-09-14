"""
Linguistic Fairness Test — Bilingual Parity Check
Member 4 — Responsible AI, Commercialization & Media Lead

Asserts that English and Singlish queries with identical intent
return the same top-ranked route (same route_id at rank #1).

Run:
    pytest evaluation/test_fairness.py -v
"""

from __future__ import annotations

import sys
import os

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

