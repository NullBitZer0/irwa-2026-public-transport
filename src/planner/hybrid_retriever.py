"""
Hybrid retrieval over the timetable corpus.

Member 2 — NLP & Information Retrieval Lead

Sparse (BM25-style) and dense (semantic) retrieval, fused with Reciprocal Rank
Fusion. What differs between the two implementations here is *where the search
runs*, not what the caller sees:

- **fixtures** (default) — keyword scoring over the JSON timetables. No
  infrastructure, no network, no model download, fully deterministic. This is
  what the demo, the tests and CI run on, because a route answer that depends on
  whether a container is up is not something you can demonstrate or test.
- **opensearch** — the real thing: OpenSearch BM25 plus k-NN vector search,
  which is what the IR rubric asks for. Selected with
  `RETRIEVER_BACKEND=opensearch` after running `src/planner/opensearch_ingest.py`.

Everything downstream of the search — mode filtering, the direction check that
stops "Jaffna to Colombo" being answered with a Colombo-to-Jaffna service, and
the RRF merge — is shared, so the two backends cannot drift in behaviour.

If the OpenSearch backend is selected but unreachable, retrieval falls back to
the fixtures and says so, rather than failing the request. A planner that cannot
answer because an index is missing is worse than one that answers from the
timetables and admits where the answer came from.

Set `RETRIEVER_BACKEND=opensearch` and run the ingest first:

    RETRIEVER_BACKEND=opensearch python -m src.planner.opensearch_ingest
    RETRIEVER_BACKEND=opensearch docker compose up -d

`sentence-transformers` is imported lazily and only by the OpenSearch backend, so
the ~2GB dependency is never pulled in by the default path.
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

logger = logging.getLogger(__name__)

# ── Data paths ────────────────────────────────────────────────────────────────
_BASE = os.path.dirname(os.path.abspath(__file__))
_DATA_DIR = os.path.join(_BASE, "../../data/processed")

# OpenSearch connection (read from env). One URL, matching docker-compose.
OPENSEARCH_URL = os.getenv("OPENSEARCH_URL", "http://localhost:9200")
OPENSEARCH_USER = os.getenv("OPENSEARCH_USER", "admin")
OPENSEARCH_PASS = os.getenv("OPENSEARCH_PASSWORD", "")
OPENSEARCH_INDEX = os.getenv("OPENSEARCH_INDEX", "transit_routes")

# Which search backend to use. The default is a constant rather than an inline
# literal so tests can assert what the code defaults to without depending on the
# environment they happen to be running in.
DEFAULT_BACKEND = "fixtures"
RETRIEVER_BACKEND = os.getenv("RETRIEVER_BACKEND", DEFAULT_BACKEND).strip().lower()


def _load_all_schedules() -> list[dict[str, Any]]:
    """Loads train and bus schedule fixtures."""
    trains: list = []
    buses: list = []
    try:
        with open(os.path.join(_DATA_DIR, "train_schedules.json")) as f:
            trains = json.load(f)
    except FileNotFoundError:
        pass
    try:
        with open(os.path.join(_DATA_DIR, "bus_routes.json")) as f:
            buses = json.load(f)
    except FileNotFoundError:
        pass
    return trains + buses


def _rrf_merge(
    sparse_hits: list[dict],
    dense_hits: list[dict],
    k: float = 60.0,
    top_k: int = 3,
) -> list[dict]:
    """
    Reciprocal Rank Fusion — merges sparse BM25 and dense k-NN ranked lists.

    score(d) = Σ 1 / (k + rank_i(d))
    """
    scores: dict[str, float] = {}
    meta: dict[str, dict] = {}

    for rank, hit in enumerate(sparse_hits):
        rid = hit["route_id"]
        scores[rid] = scores.get(rid, 0.0) + 1.0 / (k + rank + 1)
        meta[rid] = hit

    for rank, hit in enumerate(dense_hits):
        rid = hit["route_id"]
        scores[rid] = scores.get(rid, 0.0) + 1.0 / (k + rank + 1)
        meta.setdefault(rid, hit)

    sorted_ids = sorted(scores, key=lambda x: scores[x], reverse=True)
    return [
        {**meta[rid], "rrf_score": round(scores[rid], 4)}
        for rid in sorted_ids[:top_k]
    ]


def _station_key(name: str) -> str:
    """
    Canonical comparison key for a station name.

    Only the leading token is significant, which lets adjacent-but-differently-named
    facilities match: "Colombo Fort" and "Colombo Bastian Mawatha" both key to
    "colombo", while "Kandy" and "Jaffna" stay distinct.

    The exceptions are multi-word place names where the leading token is not a
    city on its own. "Nuwara Eliya" keyed to "nuwara", which matches nothing —
    not the corpus, not `MAJOR_CITIES`, not the fare chart — so every service
    touching it looked like it served nowhere. `NUWARA_ELIYA` keeps the full
    name as the key instead.
    """
    lowered = name.lower()
    if _is_nuwara_eliya(lowered):
        return NUWARA_ELIYA
    tokens = [t for t in re.split(r"[^a-z0-9]+", lowered) if t]
    return tokens[0] if tokens else ""


# "Nuwara Eliya", and the misspellings that appear in the corpora.
NUWARA_ELIYA = "nuwaraeliya"
_NUWARA_ELIYA_SPELLINGS = ("nuwaraeliya", "nuwara eliya", "nuwaraeliya ")


def _is_nuwara_eliya(lowered: str) -> bool:
    return lowered.strip() in _NUWARA_ELIYA_SPELLINGS


def _serves_direction(route: dict[str, Any], origin: str, destination: str) -> bool:
    """True when the route runs origin → destination (not the reverse)."""
    return (
        _station_key(str(route.get("origin", ""))) == _station_key(origin)
        and _station_key(str(route.get("destination", ""))) == _station_key(destination)
    )


class HybridTransitRetriever:
    """
    Hybrid sparse + dense retrieval with RRF fusion.

    Public entry point for retrieval. `RETRIEVER_BACKEND` picks the search
    implementation; see the module docstring for why the fixture backend is the
    default and what the OpenSearch one adds.
    """

    def __init__(self, backend: str | None = None) -> None:
        self.schedules = _load_all_schedules()
        self._search, self._fallback_reason = _select_backend(
            backend or RETRIEVER_BACKEND
        )
        # Recorded, not just logged: see `index_freshness`.
        self._staleness: str | None = self._check_index_freshness()
        if self._staleness:
            # Not fatal — retrieval answers either way — but it must not be
            # something only the health endpoint reveals.
            logger.warning("Retrieval index is stale: %s", self._staleness)

    def _check_index_freshness(self) -> str | None:
        """
        Is the index still describing the corpus it was built from?

        The failure this catches is quiet. Nothing raises when the index drifts:
        BM25 just stops finding the newer services, both retrievers return an
        empty list, and `retrieve_candidates` falls through to the in-memory pool.
        The answer is still right — the direction filter already narrowed the pool
        to services that actually run that way — but it is right for the wrong
        reason, and a genuine indexing bug would look identical.

        So it is surfaced the same way a backend fallback is, on the retriever.
        """
        counter = getattr(self._search, "indexed_doc_count", None)
        if counter is None:
            return None  # fixture backend has no index to drift from
        try:
            indexed = counter()
        except Exception as exc:
            return f"could not read index size: {exc}"
        if indexed is None:
            return None

        corpus = len(self.schedules)
        if indexed != corpus:
            return (
                f"index '{OPENSEARCH_INDEX}' holds {indexed} documents but the "
                f"corpus has {corpus} — re-run "
                f"`python -m src.planner.opensearch_ingest`"
            )
        return None

    @property
    def index_freshness(self) -> str | None:
        """Why the index disagrees with the corpus, or None when it agrees."""
        return self._staleness

    # ── Search backends ──────────────────────────────────────────────────────
    #
    # The two backends below differ only in where the sparse and dense searches
    # run. Mode filtering, the direction check and the RRF merge are shared, so
    # switching backend cannot change what the system considers a valid answer —
    # only how the candidates were ranked.

    def _sparse_search(
        self, query: str, top_k: int, pool: list[dict] | None = None
    ) -> list[dict]:
        """Sparse retrieval: keyword/BM25-style matching."""
        raise NotImplementedError

    def _dense_search(
        self, query: str, top_k: int, pool: list[dict] | None = None
    ) -> list[dict]:
        """Dense retrieval: semantic matching."""
        raise NotImplementedError

    @property
    def backend_name(self) -> str:
        """Which backend actually served this instance (after any fallback)."""
        return self._search.name

    @property
    def fallback_reason(self) -> str | None:
        """Why the requested backend was not used, if it was not."""
        return self._fallback_reason

    def _pool_for_mode(self, mode: str) -> list[dict[str, Any]]:
        """
        Restricts candidates by travel mode.

        SLR is the rail operator, so provider identifies trains vs buses here.
        Filtering before ranking matters: a bus query must never be crowded out
        by trains that happen to score higher on station names.
        """
        if mode == "TRAIN":
            return [s for s in self.schedules if s.get("provider") == "SLR"]
        if mode == "BUS":
            return [s for s in self.schedules if s.get("provider") != "SLR"]
        return list(self.schedules)

    def reverse_direction_options(
        self,
        origin: str,
        destination: str,
        mode: str = "ANY",
        top_k: int = 3,
    ) -> list[dict[str, Any]]:
        """
        Services running destination → origin, used to explain that no direct
        service exists in the direction the user asked for.
        """
        if not (origin and destination):
            return []
        pool = self._pool_for_mode(mode)
        matches = [s for s in pool if _serves_direction(s, destination, origin)]
        return matches[:top_k]

    def retrieve_candidates(
        self,
        query: str,
        origin: str = "",
        destination: str = "",
        mode: str = "ANY",
        top_k: int = 3,
    ) -> list[dict[str, Any]]:
        """
        Hybrid retrieval: BM25 (sparse) + k-NN (dense) → RRF fusion.

        When both endpoints are known, only services running in the requested
        direction are eligible: a stub that scores on token overlap alone will
        happily return "Colombo Fort → Jaffna" for a "Jaffna → Colombo" query.
        """
        full_query = f"{query} {origin} {destination}".strip()

        # Apply the mode filter up front so ranking only ever sees candidates
        # of the requested mode.
        pool = self._pool_for_mode(mode)

        # …and the direction filter, so a reversed service is never presented as
        # an answer. An empty result here is honest: it is reported upstream.
        if origin and destination:
            exact = [s for s in pool if _serves_direction(s, origin, destination)]
            if not exact:
                return []
            pool = exact

        sparse_hits = self._search.sparse(full_query, top_k, pool)
        dense_hits = self._search.dense(full_query, top_k, pool)

        fused = _rrf_merge(sparse_hits, dense_hits, top_k=top_k)
        # Fall back to the filtered pool, never the raw schedule list.
        return fused if fused else pool[:top_k]


# ── Search backends ───────────────────────────────────────────────────────────
#
# Both implement `sparse()` and `dense()`. They receive the pre-filtered pool so
# that mode and direction rules stay in one place.


class SearchBackend:
    """Interface for a retrieval implementation."""

    name = "base"
    fallback_reason: str | None = None

    def sparse(self, query: str, top_k: int, pool: list[dict]) -> list[dict]:
        raise NotImplementedError

    def dense(self, query: str, top_k: int, pool: list[dict]) -> list[dict]:
        raise NotImplementedError


class FixtureSearchBackend(SearchBackend):
    """
    Keyword scoring over the JSON timetables. No infrastructure required.

    This is the default because it is deterministic and dependency-free: the
    tests assert on specific routes coming back, and a retrieval answer that
    depends on whether a container has finished ingesting is not something those
    assertions can be written against.
    """

    name = "fixtures"

    def sparse(self, query: str, top_k: int, pool: list[dict]) -> list[dict]:
        results = []
        for service in pool:
            text = self._haystack(service)
            score = sum(
                0.3 for token in _tokens(query) if len(token) > 3 and token in text
            )
            if score > 0:
                results.append({**service, "_score": score})
        results.sort(key=lambda r: r["_score"], reverse=True)
        return results[:top_k]

    def dense(self, query: str, top_k: int, pool: list[dict]) -> list[dict]:
        """
        Endpoint emphasis: matching a query token to an origin or destination is
        a stronger signal than matching it anywhere in the record.
        """
        results = []
        for service in pool:
            score = 0.0
            origin = str(service.get("origin", "")).lower()
            destination = str(service.get("destination", "")).lower()
            for token in _tokens(query):
                if token in origin:
                    score += 1.0
                if token in destination:
                    score += 1.0
            if score > 0:
                results.append({**service, "_score": score})
        results.sort(key=lambda r: r["_score"], reverse=True)
        return results[:top_k]

    @staticmethod
    def _haystack(service: dict) -> str:
        return (
            f"{service.get('origin','')} {service.get('destination','')} "
            f"{' '.join(service.get('stops', []))} {service.get('service_name','')}"
        ).lower()


class OpenSearchBackend(SearchBackend):
    """
    Real hybrid retrieval: OpenSearch BM25 + k-NN vector search, fused by RRF.

    Requires the index to exist — run `python -m src.planner.opensearch_ingest`
    first. `sentence-transformers` is imported here and nowhere else, so the
    ~2GB dependency is only paid when this backend is actually selected.

    If the cluster is unreachable or the index is missing, `available()` returns
    False and the retriever falls back to fixtures rather than failing the
    request.
    """

    name = "opensearch"

    def __init__(self) -> None:
        self._client = None
        self._embedder = None
        self._warned_dense = False

    # -- setup -----------------------------------------------------------------

    def _connect(self):
        if self._client is not None:
            return self._client
        try:
            from opensearchpy import OpenSearch
        except ImportError:
            raise RuntimeError("opensearch-py is not installed")

        auth = (OPENSEARCH_USER, OPENSEARCH_PASS) if OPENSEARCH_PASS else None
        self._client = OpenSearch(
            hosts=[OPENSEARCH_URL],
            http_auth=auth,
            ssl_show_warn=False,
            timeout=5,
            max_retries=1,
        )
        return self._client

    def _embed(self, text: str) -> list[float]:
        if self._embedder is None:
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError as exc:
                raise RuntimeError(
                    "sentence-transformers is required by the opensearch backend; "
                    "pip install -r requirements.txt"
                ) from exc
            self._embedder = SentenceTransformer("all-MiniLM-L6-v2")
        return self._embedder.encode(text, normalize_embeddings=True).tolist()

    def indexed_doc_count(self) -> int | None:
        """
        How many documents the index holds, or None if it cannot be asked.

        Used to notice the index drifting from the corpus — see
        `HybridTransitRetriever._check_index_freshness`.
        """
        try:
            client = self._connect()
            count = client.count(index=OPENSEARCH_INDEX)
        except Exception:
            return None
        return int(count.get("count", 0))

    def available(self) -> tuple[bool, str | None]:
        """Can this backend actually serve a query right now?"""
        try:
            client = self._connect()
            if not client.ping():
                return False, f"no response from {OPENSEARCH_URL}"
            if not client.indices.exists(index=OPENSEARCH_INDEX):
                return False, (
                    f"index '{OPENSEARCH_INDEX}' does not exist — run "
                    f"`python -m src.planner.opensearch_ingest`"
                )
        except Exception as exc:
            return False, f"{type(exc).__name__}: {exc}"
        return True, None

    # -- search ----------------------------------------------------------------

    def sparse(self, query: str, top_k: int, pool: list[dict]) -> list[dict]:
        """BM25 over the indexed text, filtered back to the caller's pool.

        The pool is re-applied after the search: the index holds every service,
        so a mode or direction filter that lived only in OpenSearch would be a
        rule this codebase could not see or test.
        """
        client = self._connect()
        response = client.search(
            index=OPENSEARCH_INDEX,
            body={
                "size": max(top_k * 3, 20),
                "query": {"match": {"text": query}},
            },
        )
        return _restrict(response, pool)[:top_k]

    def dense(self, query: str, top_k: int, pool: list[dict]) -> list[dict]:
        """
        k-NN vector search over the indexed embeddings.

        Degrades to sparse-only rather than failing. `sentence-transformers` is
        deliberately excluded from the container image (it drags in PyTorch, ~2GB)
        so the planner cannot assume it is there. Losing semantic recall is a
        real degradation, but losing retrieval entirely because an optional
        dependency is missing is worse, and RRF is perfectly happy fusing one
        ranked list.
        """
        try:
            vector = self._embed(query)
        except RuntimeError as exc:
            if not self._warned_dense:
                logger.warning(
                    "Dense retrieval unavailable (%s) — serving BM25 results only",
                    exc,
                )
                self._warned_dense = True
            return []

        client = self._connect()
        response = client.search(
            index=OPENSEARCH_INDEX,
            body={
                "size": max(top_k * 3, 20),
                "query": {
                    "knn": {"embedding": {"vector": vector, "k": max(top_k * 3, 20)}}
                },
            },
        )
        return _restrict(response, pool)[:top_k]


def _restrict(response: dict, pool: list[dict]) -> list[dict]:
    """
    Keeps only hits that are in the caller's pool, preserving rank order.

    Two jobs, and the second matters as much as the first: the index holds every
    service, so without this a mode or direction filter would be advisory only.
    The caller's own dict is returned rather than the indexed copy, so downstream
    code sees the same object shape whichever backend is running.
    """
    by_route_id = {service.get("route_id"): service for service in pool}

    kept = []
    for hit in response.get("hits", {}).get("hits", []):
        original = by_route_id.get((hit.get("_source") or {}).get("route_id"))
        if original is not None:
            kept.append(original)
    return kept


def _tokens(text: str) -> list[str]:
    """Query tokens for keyword matching: lowercased, punctuation stripped."""
    return [t for t in re.split(r"[^\w]+", text.lower()) if t]


def _select_backend(name: str) -> tuple[SearchBackend, str | None]:
    """
    Returns the backend to use, and why not the requested one.

    Falling back rather than raising is deliberate: retrieval is one input to an
    answer, and a traveller asking about a route should still get timetables when
    a search index is down. The reason is returned rather than logged alone so
    the degradation is visible on the retriever, not only in a log line nobody
    reads.
    """
    if name in ("fixtures", "fixture", "json", "local"):
        return FixtureSearchBackend(), None

    if name not in ("opensearch", "os"):
        return (
            FixtureSearchBackend(),
            f"unknown RETRIEVER_BACKEND '{name}' — using fixtures",
        )

    backend = OpenSearchBackend()
    ok, reason = backend.available()
    if ok:
        return backend, None

    return FixtureSearchBackend(), f"opensearch unavailable ({reason}) — using fixtures"
