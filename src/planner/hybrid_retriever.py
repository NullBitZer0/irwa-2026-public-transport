from __future__ import annotations

from typing import Any

from opensearchpy import OpenSearch
from sentence_transformers import SentenceTransformer

INDEX_NAME = "transit_routes"


class HybridTransitRetriever:
    def __init__(self) -> None:
        self.client = OpenSearch(hosts=[{"host": "localhost", "port": 9200}])
        self.embedder = SentenceTransformer("all-MiniLM-L6-v2")
        self.dense_enabled = True  # kept so server.py's existing checks still work

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
            return []

        # A. Sparse retrieval — OpenSearch's built-in BM25
        bm25_resp = self.client.search(
            index=INDEX_NAME,
            body={"size": top_k * 3, "query": {"match": {"text": search_text}}},
        )
        bm25_ranked_ids = [hit["_id"] for hit in bm25_resp["hits"]["hits"]]

        # B. Dense retrieval — OpenSearch's built-in k-NN vector search
        query_vec = self.embedder.encode(search_text, normalize_embeddings=True).tolist()
        knn_resp = self.client.search(
            index=INDEX_NAME,
            body={"size": top_k * 3, "query": {"knn": {"embedding": {"vector": query_vec, "k": top_k * 3}}}},
        )
        dense_ranked_ids = [hit["_id"] for hit in knn_resp["hits"]["hits"]]

        # C. Reciprocal Rank Fusion — same formula as before
        k = 60.0
        rrf_scores: dict[str, float] = {}
        for rank, rid in enumerate(bm25_ranked_ids):
            rrf_scores[rid] = rrf_scores.get(rid, 0.0) + 1.0 / (k + rank + 1)
        for rank, rid in enumerate(dense_ranked_ids):
            rrf_scores[rid] = rrf_scores.get(rid, 0.0) + 1.0 / (k + rank + 1)

        # D. Fetch full documents, apply soft mode filter
        def mode_ok(doc: dict) -> bool:
            if mode == "TRAIN":
                return doc.get("provider") == "SLR"
            if mode == "BUS":
                return doc.get("provider") != "SLR"
            return True

        sorted_ids = sorted(rrf_scores.items(), key=lambda x: x[1], reverse=True)
        results = []
        for rid, score in sorted_ids:
            doc = self.client.get(index=INDEX_NAME, id=rid)["_source"]
            if mode_ok(doc):
                results.append({**doc, "rrf_score": round(score, 4)})
            if len(results) >= top_k:
                break

        return results