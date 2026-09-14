"""
IR Evaluation Script — MRR & NDCG@5
Member 2 — NLP & Information Retrieval Lead

Calculates Mean Reciprocal Rank (MRR) and NDCG@5 over the 30-query benchmark.

Run:
    python evaluation/evaluate_ir.py
"""

from __future__ import annotations

import json
import math
import sys
import os

# Allow imports from src/
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.planner.hybrid_retriever import HybridTransitRetriever


def calculate_mrr(retrieved_ids: list[str], relevant_id: str) -> float:
    """Mean Reciprocal Rank for a single query."""
    for rank, rid in enumerate(retrieved_ids, 1):
        if rid == relevant_id:
            return 1.0 / rank
    return 0.0


def calculate_ndcg(retrieved_ids: list[str], relevant_id: str, k: int = 5) -> float:
    """NDCG@k for a single query (binary relevance)."""
    dcg = 0.0
    for rank, rid in enumerate(retrieved_ids[:k], 1):
        if rid == relevant_id:
            dcg = 1.0 / math.log2(rank + 1)
            break
    # Ideal DCG for a single relevant document is always 1.0 (at rank 1)
    idcg = 1.0
    return dcg / idcg if idcg > 0 else 0.0


def evaluate(benchmark_path: str = "evaluation/benchmark_queries.json") -> None:
    with open(benchmark_path) as f:
        test_cases: list[dict] = json.load(f)

    retriever = HybridTransitRetriever()

    mrr_scores: list[float] = []
    ndcg_scores: list[float] = []
    failures: list[str] = []

    print("=" * 60)
    print("LankaJourney AI — IR Evaluation (30 Test Cases)")
    print("=" * 60)

    for item in test_cases:
        qid = item["id"]
        query = item["query"]
        expected = item["ground_truth_id"]

        results = retriever.retrieve_candidates(query=query, top_k=5)
        retrieved_ids = [r["route_id"] for r in results]

        mrr = calculate_mrr(retrieved_ids, expected)
        ndcg = calculate_ndcg(retrieved_ids, expected, k=5)
        mrr_scores.append(mrr)
        ndcg_scores.append(ndcg)

        status = "✓" if mrr > 0 else "✗"
        if mrr == 0:
            failures.append(qid)
        print(f"  [{status}] {qid}: MRR={mrr:.3f} | NDCG@5={ndcg:.3f} | {query[:50]}")

    mean_mrr = sum(mrr_scores) / len(mrr_scores)
    mean_ndcg = sum(ndcg_scores) / len(ndcg_scores)

    print("=" * 60)
    print(f"  Mean Reciprocal Rank (MRR):    {mean_mrr:.4f}")
    print(f"  NDCG@5:                        {mean_ndcg:.4f}")
    print(f"  Failed queries ({len(failures)}):         {', '.join(failures) or 'None'}")
    print("=" * 60)

    if mean_mrr < 0.85:
        print("⚠️  MRR below target threshold (0.85). Improve retrieval pipeline.")
    else:
        print("✅  MRR meets target threshold (≥ 0.85).")


if __name__ == "__main__":
    evaluate()

