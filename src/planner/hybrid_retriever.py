from __future__ import annotations

import json
import os
from typing import Any

from rank_bm25 import BM25Okapi

_BASE = os.path.dirname(os.path.abspath(__file__))
_DATA_DIR = os.path.join(_BASE, "../../data/processed")


def _load_all_schedules() -> list[dict[str, Any]]:
    trains, buses = [], []
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


class HybridTransitRetriever:
    def __init__(self, data_path: str | None = None) -> None:
        self.schedules = _load_all_schedules()

        # ---- 1. Sparse index (BM25) ----
        self.corpus = [
            f"{s['route_id']} {s['service_name']} {s['provider']} {s['origin']} to "
            f"{s['destination']} stops {' '.join(s.get('stops', []))}"
            for s in self.schedules
        ]
        self.tokenized_corpus = [doc.lower().split() for doc in self.corpus]
        self.bm25 = BM25Okapi(self.tokenized_corpus) if self.tokenized_corpus else None

        # ---- 2. Dense index: local embedding model, no ChromaDB needed ----
        # We compute and store the vectors ourselves with plain NumPy instead
        # of routing through ChromaDB, to avoid ChromaDB's grpc/telemetry
        # dependency (a known source of DLL-blocking issues on Windows).
        # SentenceTransformer downloads all-MiniLM-L6-v2 once (~80MB) on first
        # run, then works fully offline from then on.
        self.dense_enabled = False
        self.doc_vectors = None
        self.embedder = None
        try:
            from sentence_transformers import SentenceTransformer

            self.embedder = SentenceTransformer("all-MiniLM-L6-v2")
            docs = [
                f"{s['service_name']} operated by {s['provider']} running from "
                f"{s['origin']} to {s['destination']}. Stops: {', '.join(s.get('stops', []))}."
                for s in self.schedules
            ]
            if docs:
                self.doc_vectors = self.embedder.encode(docs, normalize_embeddings=True)
            self.dense_enabled = True
        except Exception as e:
            print(f"[HybridTransitRetriever] Dense index unavailable, using BM25-only: {e}")
            self.dense_enabled = False

    def retrieve_candidates(
        self,
        query: str,
        origin: str = "",
        destination: str = "",
        mode: str = "ANY",
        top_k: int = 3,
    ) -> list[dict[str, Any]]:
        search_text = query or f"{origin} to {destination}"
        if not search_text.strip():
            return self.schedules[:top_k]

        # A. Sparse retrieval
        bm25_ranked_ids: list[str] = []
        if self.bm25:
            scores = self.bm25.get_scores(search_text.lower().split())
            ranked_idx = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
            bm25_ranked_ids = [
                self.schedules[i]["route_id"] for i in ranked_idx if scores[i] > 0
            ][: top_k * 3]

        # B. Dense retrieval (manual cosine similarity)
        dense_ids: list[str] = []
        if self.dense_enabled and self.doc_vectors is not None:
            import numpy as np

            query_vec = self.embedder.encode([search_text], normalize_embeddings=True)[0]
            similarities = self.doc_vectors @ query_vec  # vectors are normalized, so this is cosine similarity
            top_n = min(top_k * 3, len(self.schedules))
            top_idx = np.argsort(similarities)[::-1][:top_n]
            dense_ids = [self.schedules[i]["route_id"] for i in top_idx]

        # C. Reciprocal Rank Fusion
        k = 60.0
        rrf_scores: dict[str, float] = {}
        for rank, rid in enumerate(bm25_ranked_ids):
            rrf_scores[rid] = rrf_scores.get(rid, 0.0) + 1.0 / (k + rank + 1)
        for rank, rid in enumerate(dense_ids):
            rrf_scores[rid] = rrf_scores.get(rid, 0.0) + 1.0 / (k + rank + 1)

        # D. Soft mode filter (never returns an empty result set)
        def mode_ok(route_id: str) -> bool:
            item = next((s for s in self.schedules if s["route_id"] == route_id), None)
            if not item:
                return True
            if mode == "TRAIN":
                return item.get("provider") == "SLR"
            if mode == "BUS":
                return item.get("provider") != "SLR"
            return True

        filtered = {rid: sc for rid, sc in rrf_scores.items() if mode_ok(rid)}
        final_scores = filtered if filtered else rrf_scores

        sorted_routes = sorted(final_scores.items(), key=lambda x: x[1], reverse=True)

        results = []
        for rid, score in sorted_routes[:top_k]:
            item = next((s for s in self.schedules if s["route_id"] == rid), None)
            if item:
                results.append({**item, "rrf_score": round(score, 4)})

        return results if results else self.schedules[:top_k]


if __name__ == "__main__":
    retriever = HybridTransitRetriever()
    print(f"\nDense (embedding) search enabled: {retriever.dense_enabled}\n")

    results = retriever.retrieve_candidates(query="train from Kandy to Galle", top_k=3)
    for r in results:
        print(f"{r['route_id']} | {r['service_name']} | score={r['rrf_score']}")