"""
Hybrid BM25 + ChromaDB Retrieval Engine — STUB
Member 2 — NLP & Information Retrieval Lead

TODO (Member 2):
  - Implement full BM25Okapi sparse index
  - Implement ChromaDB dense index with sentence-transformers
  - Implement Reciprocal Rank Fusion (RRF)
  - Add cross-encoder re-ranking
"""

from __future__ import annotations

import json
import os
from typing import Any

# ── Data paths ────────────────────────────────────────────────────────────────
_BASE = os.path.dirname(os.path.abspath(__file__))
_DATA_DIR = os.path.join(_BASE, "../../data/processed")


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


class HybridTransitRetriever:
    """
    Hybrid BM25 + ChromaDB retrieval engine with Reciprocal Rank Fusion.

    STUB — returns filtered results from JSON fixtures by keyword matching.
    Member 2 will replace this with the full BM25 + ChromaDB + RRF pipeline.
    """

    def __init__(self, data_path: str | None = None) -> None:
        self.schedules = _load_all_schedules()

    def retrieve_candidates(
        self,
        query: str,
        origin: str = "",
        destination: str = "",
        mode: str = "ANY",
        top_k: int = 3,
    ) -> list[dict[str, Any]]:
        """
        STUB: Filters schedules by origin/destination keyword match.
        TODO (Member 2): Replace with BM25 + ChromaDB + RRF.
        """
        query_lower = query.lower()
        origin_lower = origin.lower()
        dest_lower = destination.lower()

        results = []
        for s in self.schedules:
            # Filter by mode
            if mode == "TRAIN" and s.get("provider") != "SLR":
                continue
            if mode == "BUS" and s.get("provider") == "SLR":
                continue

            # Score by origin/destination match
            score = 0.0
            if origin_lower and origin_lower in s.get("origin", "").lower():
                score += 1.0
            if dest_lower and dest_lower in s.get("destination", "").lower():
                score += 1.0
            if origin_lower and any(origin_lower in stop.lower() for stop in s.get("stops", [])):
                score += 0.5
            if dest_lower and any(dest_lower in stop.lower() for stop in s.get("stops", [])):
                score += 0.5
            # Fallback: query keyword match
            if score == 0.0:
                text = f"{s.get('origin', '')} {s.get('destination', '')} {' '.join(s.get('stops', []))}".lower()
                for token in query_lower.split():
                    if len(token) > 3 and token in text:
                        score += 0.2

            if score > 0:
                results.append({**s, "rrf_score": round(score / 3.0, 4)})

        # Sort by score desc, return top_k
        results.sort(key=lambda x: x["rrf_score"], reverse=True)
        return results[:top_k] if results else self.schedules[:top_k]

