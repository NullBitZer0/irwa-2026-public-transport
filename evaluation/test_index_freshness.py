"""Noticing when the search index stops matching the corpus.

The failure this covers is silent. When the index drifts behind the corpus, BM25
simply stops finding the newer services, both retrievers return nothing, and
`retrieve_candidates` falls through to the in-memory pool. The answer is still
correct — the direction filter has already narrowed the pool to services that run
the right way — so nothing raises and nothing logs. A real indexing bug would
look exactly the same.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.planner import server as planner_server
from src.planner.generate_missing_corridors import BUS_FILE
from src.planner.hybrid_retriever import (
    HybridTransitRetriever,
    OpenSearchBackend,
    _select_backend,
)
from src.planner.schedule_ingest import load_fixture


@pytest.fixture(scope="module")
def corpus() -> list[dict]:
    return load_fixture(BUS_FILE)


class _BackendWithCount:
    """Stands in for a backend whose index holds a chosen number of documents."""

    name = "opensearch"
    fallback_reason = None

    def __init__(self, count: int | None, schedules: list[dict]) -> None:
        self._count = count
        self.schedules = schedules

    def indexed_doc_count(self) -> int | None:
        return self._count

    def sparse(self, query, top_k, pool):  # pragma: no cover - never reached
        return []

    def dense(self, query, top_k, pool):  # pragma: no cover - never reached
        return []


def _retriever_with(count: int | None, corpus: list[dict]) -> HybridTransitRetriever:
    retriever = HybridTransitRetriever.__new__(HybridTransitRetriever)
    retriever.schedules = corpus
    retriever._search = _BackendWithCount(count, corpus)
    retriever._fallback_reason = None
    retriever._staleness = retriever._check_index_freshness()
    return retriever


class TestStalenessIsNoticed:
    def test_a_matching_index_is_not_flagged(self, corpus: list[dict]) -> None:
        retriever = _retriever_with(len(corpus), corpus)
        assert retriever.index_freshness is None

    def test_an_index_behind_the_corpus_is_flagged(self, corpus: list[dict]) -> None:
        """
        The bug as it actually happened: 812 documents indexed, corpus grown to
        4,311 rows since.
        """
        retriever = _retriever_with(812, corpus)
        assert retriever.index_freshness is not None
        message = retriever.index_freshness
        assert "812" in message
        assert str(len(corpus)) in message
        # It must say how to fix it, not just that something is wrong.
        assert "opensearch_ingest" in message

    def test_an_index_ahead_of_the_corpus_is_also_flagged(
        self, corpus: list[dict]
    ) -> None:
        """
        Drift in either direction means the index describes a corpus that is not
        this one — including a corpus row having been deleted.
        """
        retriever = _retriever_with(len(corpus) + 500, corpus)
        assert retriever.index_freshness is not None

    def test_the_fixture_backend_has_no_index_to_drift(self, corpus: list[dict]) -> None:
        """
        Nothing to compare against, so nothing is claimed.

        Reporting staleness for a backend with no index would be a false alarm on
        the default configuration.
        """
        retriever = HybridTransitRetriever("fixtures")
        assert retriever.index_freshness is None

    def test_an_unreadable_index_is_reported_not_assumed_fresh(
        self, corpus: list[dict]
    ) -> None:
        """A cluster that cannot be asked is not evidence of a healthy index."""
        retriever = _retriever_with(None, corpus)
        assert retriever.index_freshness is None, "None means unreadable, not stale"

        class Broken(_BackendWithCount):
            def indexed_doc_count(self):
                raise RuntimeError("connection refused")

        retriever._search = Broken(None, corpus)
        retriever._staleness = retriever._check_index_freshness()
        assert retriever.index_freshness is not None
        assert "connection refused" in retriever.index_freshness


class TestTheBackendCanBeAsked:
    def test_indexed_doc_count_is_read_from_the_cluster(self) -> None:
        backend = OpenSearchBackend()
        assert callable(backend.indexed_doc_count)

    def test_the_fixture_backend_offers_no_counter(self) -> None:
        backend, _ = _select_backend("fixtures")
        assert not hasattr(backend, "indexed_doc_count")


class TestHealthReportsDegradation:
    def test_health_names_the_backend_and_the_index_state(self) -> None:
        """
        Retrieval answers even when degraded, so a bare "ok" hides the
        difference between serving from the index and serving from memory.
        """
        client = TestClient(planner_server.app)
        body = client.get("/health").json()

        assert body["status"] == "ok"
        assert body["retrieval_backend"]
        assert "index_freshness" in body
        assert "retrieval_fallback" in body
        assert body["corpus_size"] > 0

    def test_health_surfaces_a_stale_index(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            type(planner_server._retriever),
            "index_freshness",
            property(lambda self: "index holds 10 documents but the corpus has 4311"),
        )
        body = TestClient(planner_server.app).get("/health").json()
        assert "10 documents" in body["index_freshness"]
        assert body["status"] == "ok", "a stale index is degraded, not down"
