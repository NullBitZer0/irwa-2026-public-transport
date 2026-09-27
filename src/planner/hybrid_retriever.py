"""
Hybrid OpenSearch Retrieval Engine — STUB
Member 2 — NLP & Information Retrieval Lead

Uses OpenSearch for BOTH sparse (BM25) and dense (k-NN vector) retrieval,
then merges results via Reciprocal Rank Fusion (RRF).

OpenSearch replaces the previous ChromaDB + rank-bm25 dual-library approach:
  - Sparse  : OpenSearch native BM25 inverted index  (exact station/route matching)
  - Dense   : OpenSearch k-NN plugin                 (semantic policy & intent matching)
  - Fusion  : RRF merges both ranked lists

TODO (Member 2):
  1. Run OpenSearch locally:
       docker run -p 9200:9200 -e "discovery.type=single-node" \
         -e "OPENSEARCH_INITIAL_ADMIN_PASSWORD=<pwd>" \
         opensearchproject/opensearch:2.16.0

  2. Create index with both BM25 and knn_vector fields:
       PUT /transit_routes
       {
         "settings": { "index.knn": true },
         "mappings": {
           "properties": {
             "text":      { "type": "text" },          <- BM25
             "embedding": { "type": "knn_vector", "dimension": 384 }  <- Dense
           }
         }
       }

  3. Replace retrieve_candidates() below with real OpenSearch hybrid query.
"""

from __future__ import annotations

import json
import os
from typing import Any

# ── Data paths ────────────────────────────────────────────────────────────────
_BASE = os.path.dirname(os.path.abspath(__file__))
_DATA_DIR = os.path.join(_BASE, "../../data/processed")

# OpenSearch connection (read from env)
OPENSEARCH_HOST = os.getenv("OPENSEARCH_HOST", "localhost")
OPENSEARCH_PORT = int(os.getenv("OPENSEARCH_PORT", "9200"))
OPENSEARCH_USER = os.getenv("OPENSEARCH_USER", "admin")
OPENSEARCH_PASS = os.getenv("OPENSEARCH_PASSWORD", "")
OPENSEARCH_INDEX = os.getenv("OPENSEARCH_INDEX", "transit_routes")


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


class HybridTransitRetriever:
    """
    Hybrid BM25 + Dense vector retrieval via OpenSearch with RRF fusion.

    STUB — uses keyword scoring against JSON fixtures until Member 2 connects
    this to a running OpenSearch instance.
    """

    def __init__(self) -> None:
        self.schedules = _load_all_schedules()
        self._client = None  # TODO: init opensearch-py client here

    def _get_client(self):
        """
        TODO (Member 2): Initialise the OpenSearch client.

        from opensearchpy import OpenSearch
        return OpenSearch(
            hosts=[{"host": OPENSEARCH_HOST, "port": OPENSEARCH_PORT}],
            http_auth=(OPENSEARCH_USER, OPENSEARCH_PASS),
            use_ssl=True,
            verify_certs=False,
        )
        """
        return None

    def _sparse_search(self, query: str, top_k: int) -> list[dict]:
        """
        TODO (Member 2): BM25 query against OpenSearch.

        body = {
            "size": top_k,
            "query": { "match": { "text": query } }
        }
        res = self._client.search(index=OPENSEARCH_INDEX, body=body)
        return [hit["_source"] for hit in res["hits"]["hits"]]
        """
        # STUB: keyword filter over local JSON
        results = []
        for s in self.schedules:
            text = f"{s.get('origin','')} {s.get('destination','')} {' '.join(s.get('stops',[]))} {s.get('service_name','')}".lower()
            score = sum(0.3 for token in query.lower().split() if len(token) > 3 and token in text)
            if score > 0:
                results.append({**s, "_score": score})
        results.sort(key=lambda x: x["_score"], reverse=True)
        return results[:top_k]

    def _dense_search(self, query: str, top_k: int) -> list[dict]:
        """
        TODO (Member 2): k-NN vector search against OpenSearch.

        from sentence_transformers import SentenceTransformer
        model = SentenceTransformer("all-MiniLM-L6-v2")
        vector = model.encode(query).tolist()

        body = {
            "size": top_k,
            "query": {
                "knn": { "embedding": { "vector": vector, "k": top_k } }
            }
        }
        res = self._client.search(index=OPENSEARCH_INDEX, body=body)
        return [hit["_source"] for hit in res["hits"]["hits"]]
        """
        # STUB: origin/destination substring match
        results = []
        for s in self.schedules:
            score = 0.0
            for token in query.lower().split():
                if token in s.get("origin", "").lower():
                    score += 1.0
                if token in s.get("destination", "").lower():
                    score += 1.0
            if score > 0:
                results.append({**s, "_score": score})
        results.sort(key=lambda x: x["_score"], reverse=True)
        return results[:top_k]

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
        """
        full_query = f"{query} {origin} {destination}".strip()

        sparse_hits = self._sparse_search(full_query, top_k)
        dense_hits = self._dense_search(full_query, top_k)

        # Filter by mode
        if mode in ("TRAIN", "BUS"):
            if mode == "TRAIN":
                sparse_hits = [h for h in sparse_hits if h.get("provider") == "SLR"]
                dense_hits = [h for h in dense_hits if h.get("provider") == "SLR"]
            else:
                sparse_hits = [h for h in sparse_hits if h.get("provider") != "SLR"]
                dense_hits = [h for h in dense_hits if h.get("provider") != "SLR"]

        fused = _rrf_merge(sparse_hits, dense_hits, top_k=top_k)
        return fused if fused else self.schedules[:top_k]
