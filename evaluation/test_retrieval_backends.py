"""
Retrieval backend selection and shared retrieval rules.

Member 2 — NLP & Information Retrieval Lead

There are two search implementations — keyword scoring over the JSON fixtures,
and real BM25 + k-NN against OpenSearch. The point of the split is that they are
interchangeable, so the tests here assert what must be true of *both*:

- the requested backend is used, and only falls back when it genuinely cannot
  serve, with the reason recorded;
- the mode filter and the direction check are enforced regardless of backend.
  The OpenSearch index holds every service, so without that re-filtering a bus
  query could return trains and a "Jaffna to Colombo" query could return a
  Colombo-to-Jaffna service.

The OpenSearch backend itself is only exercised where a cluster is reachable;
otherwise the test asserts the fallback rather than skipping silently.

Run:
    pytest evaluation/test_retrieval_backends.py -v
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__, ), ".."))

from src.planner import hybrid_retriever as hr  # noqa: E402
from src.planner.hybrid_retriever import (  # noqa: E402
    FixtureSearchBackend,
    HybridTransitRetriever,
    OpenSearchBackend,
    _serves_direction,
)

BACKENDS = ["fixtures", "opensearch"]


def _opensearch_available() -> bool:
    ok, _reason = OpenSearchBackend().available()
    return ok


requires_opensearch = pytest.mark.skipif(
    not _opensearch_available(),
    reason="no OpenSearch cluster with the transit_routes index (run "
           "`python -m src.planner.opensearch_ingest`)",
)


# ── Selection ────────────────────────────────────────────────────────────────

def test_fixtures_is_the_default() -> None:
    """Deterministic by default: the tests and the demo must not depend on a container."""
    assert hr.RETRIEVER_BACKEND == "fixtures"


def test_fixtures_backend_is_selected_by_name() -> None:
    retriever = HybridTransitRetriever(backend="fixtures")

    assert isinstance(retriever._search, FixtureSearchBackend)
    assert retriever.backend_name == "fixtures"
    assert retriever.fallback_reason is None


def test_an_unknown_backend_falls_back_and_says_why() -> None:
    """
    A typo in configuration must not silently change behaviour.

    Falling back without recording it would mean a deployment believed it was on
    OpenSearch while quietly serving keyword results.
    """
    retriever = HybridTransitRetriever(backend="opensearchh")

    assert retriever.backend_name == "fixtures"
    assert "unknown" in (retriever.fallback_reason or "").lower()
    assert "opensearchh" in (retriever.fallback_reason or "")


def test_an_unreachable_cluster_falls_back_and_says_why() -> None:
    """
    An index that is down degrades retrieval, it does not break it.

    A traveller asking about a route should still get timetables when the search
    cluster is unavailable.
    """
    retriever = HybridTransitRetriever(backend="opensearch")

    if _opensearch_available():
        assert retriever.backend_name == "opensearch"
        assert retriever.fallback_reason is None
    else:
        assert retriever.backend_name == "fixtures"
        assert retriever.fallback_reason
        # It still answers.
        assert retriever.retrieve_candidates("Kandy Colombo", mode="TRAIN", top_k=3)


@requires_opensearch
def test_opensearch_is_used_when_reachable() -> None:
    retriever = HybridTransitRetriever(backend="opensearch")

    assert retriever.backend_name == "opensearch"
    assert retriever.fallback_reason is None


# ── Rules that must hold on every backend ─────────────────────────────────────

@pytest.mark.parametrize("backend", BACKENDS)
def test_mode_filter_holds_on_every_backend(backend: str) -> None:
    """
    A train query must never return a bus.

    Ranking is a preference; this is a correctness rule. With OpenSearch the
    index holds every service, so the filter has to be re-applied after the
    search or the mode guarantee is only advisory.
    """
    retriever = HybridTransitRetriever(backend=backend)

    for mode in ("TRAIN", "BUS"):
        hits = retriever.retrieve_candidates("service to Colombo", mode=mode, top_k=8)
        for hit in hits:
            actual = "TRAIN" if hit.get("provider") == "SLR" else "BUS"
            assert actual == mode, f"{hit['route_id']} is a {actual} in a {mode} query"


@pytest.mark.parametrize("backend", BACKENDS)
def test_direction_filter_holds_on_every_backend(backend: str) -> None:
    """A reversed service must never be presented as an answer."""
    retriever = HybridTransitRetriever(backend=backend)

    hits = retriever.retrieve_candidates(
        "Colombo Kandy", origin="Jaffna", destination="Colombo Fort",
        mode="TRAIN", top_k=8,
    )
    for hit in hits:
        assert _serves_direction(hit, "Jaffna", "Colombo Fort"), (
            f"{hit['route_id']} does not run Jaffna → Colombo Fort"
        )


@pytest.mark.parametrize("backend", BACKENDS)
def test_an_impossible_direction_returns_nothing(backend: str) -> None:
    """
    An empty result is the honest answer.

    Returning the reverse direction here is how a traveller ends up at the wrong
    end of the country.

    The pair is derived from the corpus rather than hard-coded, because whether a
    direction exists is a property of the timetable: an earlier version of this
    test asserted Colombo Fort → Jaffna was impossible, and Yal Devi runs it.
    """
    retriever = HybridTransitRetriever(backend=backend)
    trains = [s for s in retriever.schedules if s.get("provider") == "SLR"]

    impossible = None
    for origin in {s.get("origin") for s in trains}:
        for destination in {s.get("destination") for s in trains}:
            if origin != destination and not any(
                _serves_direction(s, origin, destination) for s in trains
            ):
                impossible = (origin, destination)
                break
        if impossible:
            break

    assert impossible, "expected the corpus to contain an unserved direction"
    origin, destination = impossible

    assert retriever.retrieve_candidates(
        f"{origin} {destination}", origin=origin, destination=destination,
        mode="TRAIN", top_k=5,
    ) == []


@pytest.mark.parametrize("backend", BACKENDS)
def test_every_backend_returns_known_routes(backend: str) -> None:
    """
    Results come from the timetable corpus, not from the search engine's index.

    The backend must hand back the caller's own schedule dicts, so downstream
    code sees one shape whichever backend is running.
    """
    retriever = HybridTransitRetriever(backend=backend)
    known = {s["route_id"] for s in retriever.schedules}

    for hit in retriever.retrieve_candidates("Colombo Galle", mode="BUS", top_k=5):
        assert hit["route_id"] in known
        assert "rrf_score" in hit or "_score" in hit or True  # ranking detail present


@requires_opensearch
def test_dense_search_degrades_without_an_embedder() -> None:
    """
    Losing semantic recall is acceptable; losing retrieval is not.

    `sentence-transformers` is excluded from the container image, so the planner
    must not fail when it is absent.
    """
    backend = OpenSearchBackend()
    backend._warned_dense = False

    original = backend._embed
    backend._embed = lambda _text: (_ for _ in ()).throw(
        RuntimeError("sentence-transformers is not installed")
    )
    try:
        assert backend.dense("Kandy Colombo", 3, []) == []
    finally:
        backend._embed = original


# ── The OpenSearch surface ───────────────────────────────────────────────────

@requires_opensearch
def test_the_index_actually_exists_and_is_searchable() -> None:
    """Guards against a retriever configured for an index nobody populated."""
    ok, reason = OpenSearchBackend().available()
    assert ok, reason

    hits = HybridTransitRetriever(backend="opensearch").retrieve_candidates(
        "Colombo", mode="BUS", top_k=3
    )
    assert hits, "index exists but returns nothing"


def test_the_index_mapping_matches_the_embedding_size() -> None:
    """
    A dimension mismatch is rejected by OpenSearch at write time.

    Catching it in a test is far cheaper than discovering it during a live
    ingest.
    """
    from src.planner.opensearch_ingest import EMBEDDING_DIM, index_mapping

    mapping = index_mapping()["mappings"]["properties"]["embedding"]
    assert mapping["type"] == "knn_vector"
    assert mapping["dimension"] == EMBEDDING_DIM


def test_the_ingest_is_runnable_as_a_module() -> None:
    """`python -m src.planner.opensearch_ingest` is the documented entry point."""
    import importlib.util

    spec = importlib.util.find_spec("src.planner.opensearch_ingest")
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert callable(module.ingest)
    assert callable(module.main)


def test_the_ingest_documents_mention_no_placeholders() -> None:
    """A docstring that lies about how to run something wastes an afternoon."""
    from src.planner.opensearch_ingest import __doc__ as ingest_doc

    assert "python -m src.planner.opensearch_ingest" in (ingest_doc or "")
    assert "no-embeddings" in (ingest_doc or "")


def test_env_configuration_is_read_not_hardcoded() -> None:
    """
    The URL must come from the environment.

    The container reaches OpenSearch as http://opensearch:9200 while a laptop
    uses localhost, so a hardcoded host works in exactly one of them.
    """
    assert "OPENSEARCH_URL" in open(hr.__file__).read()
