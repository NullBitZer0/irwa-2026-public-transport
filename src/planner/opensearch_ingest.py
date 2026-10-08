"""
Index the timetable corpus into OpenSearch for hybrid retrieval.

Member 2 — NLP & Information Retrieval Lead

Run once before using the OpenSearch retriever:

    RETRIEVER_BACKEND=opensearch python -m src.planner.opensearch_ingest

Creates the `transit_routes` index with both a text field (BM25) and a
knn_vector field (dense), then bulk-loads every schedule. Safe to re-run: the
index is recreated rather than appended to, so a re-ingest cannot leave stale
documents behind for routes that were removed from the fixtures.

`--no-embeddings` skips the dense pass. That is what you want in an environment
without `sentence-transformers` (the container image deliberately omits it, and
it pulls in PyTorch): the retriever then serves BM25 results only and says so.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any

from opensearchpy import OpenSearch, helpers

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.planner.hybrid_retriever import (  # noqa: E402
    OPENSEARCH_INDEX,
    OPENSEARCH_PASS,
    OPENSEARCH_URL,
    OPENSEARCH_USER,
    _load_all_schedules,
)

# MiniLM-L6-v2 emits 384 dimensions; the mapping has to agree exactly or the
# index rejects every document.
EMBEDDING_DIM = 384
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "all-MiniLM-L6-v2")
BATCH_SIZE = 200


def get_client() -> OpenSearch:
    auth = (OPENSEARCH_USER, OPENSEARCH_PASS) if OPENSEARCH_PASS else None
    return OpenSearch(
        hosts=[OPENSEARCH_URL],
        http_auth=auth,
        ssl_show_warn=False,
        timeout=30,
    )


def index_mapping() -> dict[str, Any]:
    return {
        "settings": {"index.knn": True},
        "mappings": {
            "properties": {
                "text": {"type": "text"},
                "embedding": {
                    "type": "knn_vector",
                    "dimension": EMBEDDING_DIM,
                },
                "route_id": {"type": "keyword"},
                "provider": {"type": "keyword"},
            }
        },
    }


def document_text(schedule: dict[str, Any]) -> str:
    """
    The BM25 field. Include the places and the mode a traveller might actually type.

    Naming the mode matters more than it looks. Without it the indexed text for a
    train and a coach on the same corridor is near-identical, so "train to Matara
    from Colombo" matched only on *Matara* and *Colombo* and the buses won on term
    frequency — the trains did not appear in the top ten. Saying "train" or "bus"
    is what lets a mode filter be a word rather than an accident.
    """
    stops = ", ".join(schedule.get("stops", []) or [])
    provider = str(schedule.get("provider") or "")
    mode = "train" if provider.upper() == "SLR" else "bus"
    classes = " ".join(schedule.get("classes") or [])
    return (
        f"{mode} {schedule.get('service_name', '')} operated by "
        f"{provider} running from "
        f"{schedule.get('origin', '')} to {schedule.get('destination', '')}. "
        f"Stops: {stops}. Class: {classes}. Route {schedule.get('route_id', '')}."
    )


def build_embedder():
    """Loads the sentence encoder, or returns None when it is unavailable."""
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError:
        return None
    return SentenceTransformer(EMBEDDING_MODEL)


def ingest(embed: bool = True, recreate: bool = True) -> int:
    client = get_client()
    if not client.ping():
        raise SystemExit(
            f"Cannot reach OpenSearch at {OPENSEARCH_URL}. Start it with "
            f"`docker compose up -d opensearch`, or check OPENSEARCH_URL."
        )

    if recreate and client.indices.exists(index=OPENSEARCH_INDEX):
        client.indices.delete(index=OPENSEARCH_INDEX)
    if not client.indices.exists(index=OPENSEARCH_INDEX):
        client.indices.create(index=OPENSEARCH_INDEX, body=index_mapping())
        print(f"created index '{OPENSEARCH_INDEX}'")

    schedules = _load_all_schedules()
    if not schedules:
        raise SystemExit(
            "No schedules found in data/processed/. Run the ingestion scripts "
            "before indexing."
        )

    encoder = build_embedder() if embed else None
    if embed and encoder is None:
        print(
            "sentence-transformers is not installed — indexing for BM25 only. "
            "Install it (pip install sentence-transformers) and re-run for "
            "semantic search."
        )

    indexed = 0
    for start in range(0, len(schedules), BATCH_SIZE):
        batch = schedules[start : start + BATCH_SIZE]
        texts = [document_text(s) for s in batch]

        actions = []
        for schedule, text in zip(batch, texts):
            source = {**schedule, "text": text}
            if encoder is not None:
                source["embedding"] = encoder.encode(
                    text, normalize_embeddings=True
                ).tolist()
            actions.append(
                {
                    "_index": OPENSEARCH_INDEX,
                    "_id": schedule["route_id"],
                    "_source": source,
                }
            )

        helpers.bulk(client, actions)
        indexed += len(actions)
        print(f"  indexed {indexed}/{len(schedules)}", end="\r", flush=True)

    print(f"\nIndexed {indexed} routes into '{OPENSEARCH_INDEX}'")
    if encoder is None:
        print("Dense retrieval will be unavailable for this index.")
    return indexed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--no-embeddings",
        action="store_true",
        help="index for BM25 only, skipping the dense pass",
    )
    parser.add_argument(
        "--keep-index",
        action="store_true",
        help="do not delete an existing index first (may leave stale documents)",
    )
    args = parser.parse_args()

    ingest(embed=not args.no_embeddings, recreate=not args.keep_index)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
