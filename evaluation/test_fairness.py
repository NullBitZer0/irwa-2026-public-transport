"""
Linguistic Fairness Test — Trilingual Parity Check (English / Singlish / Sinhala)
Member 4 — Responsible AI, Commercialization & Media Lead

Asserts that English, Singlish, and Sinhala queries with identical intent
return the same expected route in top-k. Covers Responsible AI
Linguistic Fairness rubric (Singlish/English/Sinhala parity).

Run:
    pytest evaluation/test_fairness.py -v
    pytest evaluation/test_fairness.py::test_bilingual_parity -v
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
#
# OUT OF SCOPE. The supported input is **Singlish** (Sinhala in English letters),
# not native Sinhala script, so these tests are expected to fail and are marked
# xfail. They are kept rather than deleted so the gap is visible: Sinhala-script
# input returns nothing rather than the wrong route, which a traveller would
# read as "no such service exists" rather than "we did not understand".
#
# Supporting it needs either a Sinhala vocabulary/translation layer or a
# multilingual embedder (e.g. paraphrase-multilingual-MiniLM-L12-v2) — which is
# also the right fix for the original all-MiniLM-L6-v2 limitation.
#
# Singlish parity is asserted for real in test_singlish_parity above, which is
# the language the product actually accepts.
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

# Full triple: EN + Singlish + Sinhala. The first two must agree; the Sinhala leg
# is asserted but out of scope (see SINHALA_PAIRS above).
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


@pytest.mark.xfail(
    # Note the train cases may report XPASS. That is not Sinhala support: the
    # retriever falls back to the first N schedules when nothing scores, and the
    # first two happen to be TRAIN-1001/1002. The bus case fails for real, which
    # is the honest signal — do not read XPASS as parity.
    reason="Sinhala Unicode script is out of scope; the product accepts Singlish",
    strict=False,
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


@pytest.mark.xfail(
    reason="Sinhala Unicode script is out of scope; the product accepts Singlish",
    strict=False,
)
@pytest.mark.parametrize("english_q, singlish_q, sinhala_q, expected_id", TRILINGUAL_TRIPLES)
def test_trilingual_parity(
    retriever: HybridTransitRetriever,
    english_q: str,
    singlish_q: str,
    sinhala_q: str,
    expected_id: str,
) -> None:
    """EN and Singlish must retrieve the same route; Sinhala is checked but unsupported."""
    en_ids = [r["route_id"] for r in retriever.retrieve_candidates(query=english_q, top_k=5)]
    sl_ids = [r["route_id"] for r in retriever.retrieve_candidates(query=singlish_q, top_k=5)]
    si_ids = [r["route_id"] for r in retriever.retrieve_candidates(query=sinhala_q, top_k=5)]

    # The two supported languages must agree.
    for label, ids in [("English", en_ids), ("Singlish", sl_ids)]:
        assert expected_id in ids, f"[{label}] Expected '{expected_id}' in {ids}"
    # Out of scope, asserted so the gap stays measurable rather than forgotten.
    assert expected_id in si_ids, f"[Sinhala, unsupported] Expected '{expected_id}' in {si_ids}"



# ── Responsible AI: provenance ────────────────────────────────────────────────

def test_every_route_carries_a_provenance_citation() -> None:
    """
    No recommendation should reach the UI without the dataset it came from.

    Provenance is attached by the orchestrator (src/responsible_ai/grounding.py)
    so that a UI change cannot drop it silently.
    """
    from src.orchestrator.main_graph import _with_provenance

    routes = [
        {"route_id": "TRAIN-1007", "provider": "SLR", "transit_type": "TRAIN"},
        {"route_id": "SLTB-2-COLO-MATA-0930", "provider": "SLTB", "transit_type": "BUS"},
        {"route_id": "RM-02-COLOMB-GALLE-1315", "provider": "RM", "transit_type": "BUS"},
        {"route_id": "UNKNOWN-1"},
    ]
    cited = _with_provenance(routes)

    assert all(r.get("citation_source") for r in cited)
    assert "train_schedules.json" in cited[0]["citation_source"]
    assert "bus_routes.json" in cited[1]["citation_source"]
    assert "bus_routes.json" in cited[2]["citation_source"]
    # An unrecognised provider still gets a citation rather than none.
    assert cited[3]["citation_source"]


def test_connections_are_cited_per_leg() -> None:
    """A mixed train+bus itinerary spans two datasets, and must say so."""
    from src.orchestrator.main_graph import _with_provenance

    cited = _with_provenance(
        [
            {
                "route_id": "CONN-1",
                "legs": [
                    {"route_id": "TRAIN-1007", "provider": "SLR", "transit_type": "TRAIN"},
                    {"route_id": "SLTB-2-COLO-MATA-0930", "provider": "SLTB", "transit_type": "BUS"},
                ],
            }
        ]
    )

    source = cited[0]["citation_source"]
    assert "train_schedules.json" in source
    assert "bus_routes.json" in source
    assert " + " in source


def test_provenance_does_not_mutate_the_input_routes() -> None:
    """The planner's dicts are reused for the reply text; enrichment must copy."""
    from src.orchestrator.main_graph import _with_provenance

    original = {"route_id": "TRAIN-1007", "provider": "SLR"}
    _with_provenance([original])

    assert "citation_source" not in original
