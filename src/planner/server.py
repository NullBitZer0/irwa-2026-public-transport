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

from typing import Optional

from fastapi import FastAPI
from pydantic import BaseModel

from src.planner.connection_planner import (
    find_connections,
    public_connections,
    rank_connections,
)
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

    # NLP extraction (rule-based English + Singlish)
    parsed = extract_transit_intent(raw_query)

    # Trust an explicitly requested mode, otherwise use the one the parser
    # detected (the Orchestrator sends "ANY" and lets the parser decide).
    travel_mode = (
        payload.travel_mode if payload.travel_mode in ("TRAIN", "BUS") else parsed.mode
    )

    origin = payload.origin or (parsed.origin or "")
    destination = payload.destination or (parsed.destination or "")

    # Hybrid retrieval (stub uses keyword scoring against JSON fixtures)
    candidates = _retriever.retrieve_candidates(
        query=raw_query,
        origin=origin,
        destination=destination,
        mode=travel_mode,
        top_k=3,
    )

    # No direct service in the requested direction? Offer a one-stop connection,
    # preferring a mix of train and bus. When the traveller insisted on a single
    # mode, keep any same-mode connection but still surface the mixed option,
    # since a 25-minute train-then-coach change usually beats a 23-hour wait for
    # the next train of the same type.
    connections: list = []
    mixed_fallback = False
    if not candidates and origin and destination:
        connections = find_connections(
            _retriever.schedules, origin, destination, mode=travel_mode
        )
        if travel_mode != "ANY":
            already = {c["route_id"] for c in connections}
            extra = [
                c
                for c in find_connections(
                    _retriever.schedules, origin, destination, mode="ANY"
                )
                if c["mixed_mode"] and c["route_id"] not in already
            ]
            mixed_fallback = bool(extra) or not connections
            connections = rank_connections(connections + extra)

    # If nothing at all runs that way, say so and point at the opposite direction.
    direction_note: Optional[str] = None
    if not candidates and not connections and origin and destination:
        reverse = _retriever.reverse_direction_options(
            origin=origin, destination=destination, mode=travel_mode
        )
        if reverse:
            options = ", ".join(
                f"{r['route_id']} ({r['origin']} → {r['destination']})" for r in reverse
            )
            direction_note = (
                f"No direct service runs {origin} → {destination}. "
                f"These services run the opposite way: {options}."
            )

    return {
        "status": "SUCCESS",
        "data": {
            "parsed_entities": parsed.model_dump(),
            "route_options": candidates,
            "connections": public_connections(connections),
            "mixed_mode_fallback": mixed_fallback,
            "direction_note": direction_note,
        },
        "message": (
            f"Retrieved {len(candidates)} route option(s) and "
            f"{len(connections)} connection(s). [STUB — Member 2 implementing full NLP+IR]"
        ),
    }


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "agent": "planner", "version": "0.1.0-stub"}

