"""
Planning Agent FastAPI Server — Functional Stub
Member 2 — NLP & Information Retrieval Lead

This stub returns real data from the JSON fixtures so Member 1 can test
the full orchestration flow before Member 2 implements NLP + Hybrid IR.

Start:
    uvicorn src.planner.server:app --port 8001 --reload

TODO (Member 2):
  - Replace stub retrieve with HybridTransitRetriever (BM25 + ChromaDB + RRF)
  - Replace stub parse with extract_transit_intent (instructor + LiteLLM)
  - Add ChromaDB knowledge base ingestion on startup
"""

from __future__ import annotations

from fastapi import FastAPI
from pydantic import BaseModel

from src.planner.hybrid_retriever import HybridTransitRetriever
from src.planner.nlp_parser import extract_transit_intent

app = FastAPI(
    title="Planning Agent — IR & NLP Worker",
    description="Agent 2: Hybrid retrieval, NLP entity extraction, and live alerts.",
    version="0.1.0-stub",
)

_retriever = HybridTransitRetriever()


class PlanRouteRequest(BaseModel):
    origin: str = ""
    destination: str = ""
    travel_mode: str = "ANY"
    date_str: str = "TODAY"
    time_preference: str | None = None
    passengers: int = 1
    raw_query: str | None = None


@app.post("/mcp/plan_route")
async def plan_route(payload: PlanRouteRequest) -> dict:
    """
    Route planning MCP endpoint.
    Returns hybrid-retrieved route options for the given origin/destination.
    """
    raw_query = payload.raw_query or f"{payload.origin} to {payload.destination}"

    # NLP extraction (stub uses basic keyword detection)
    parsed = extract_transit_intent(raw_query)

    # Hybrid retrieval (stub uses keyword scoring against JSON fixtures)
    candidates = _retriever.retrieve_candidates(
        query=raw_query,
        origin=payload.origin or (parsed.origin or ""),
        destination=payload.destination or (parsed.destination or ""),
        mode=payload.travel_mode,
        top_k=3,
    )

    return {
        "status": "SUCCESS",
        "parsed_entities": parsed.model_dump(),
        "route_options": candidates,
        "message": f"Retrieved {len(candidates)} route option(s). [STUB — Member 2 implementing full NLP+IR]",
    }


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "agent": "planner", "version": "0.1.0-stub"}

