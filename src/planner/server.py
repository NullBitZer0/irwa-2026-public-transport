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
from src.planner.journey_search import find_services_at, missing_details
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


@app.post("/mcp/plan_journey")
async def plan_journey(payload: PlanRouteRequest) -> dict:
    """
    Clarification-first journey search.

    Rather than guessing, this reports what is still missing — a mode, a
    departure time — so the caller can ask the traveller instead of returning a
    confident but unhelpful list. When there is enough to answer, it returns the
    services that board at the requested origin heading the right way, including
    long-distance coaches that merely pass through, flagged `board_type`.

    Times at intermediate stops are estimated by distributing the journey evenly
    across the stop sequence, so they are good for ranking, not a timetable.
    """
    raw_query = payload.raw_query or f"{payload.origin} to {payload.destination}"
    parsed = extract_transit_intent(raw_query)

    origin = payload.origin or (parsed.origin or "")
    destination = payload.destination or (parsed.destination or "")
    # The Orchestrator sends "ANY" to mean "the traveller has not chosen yet".
    mode = payload.travel_mode if payload.travel_mode in ("TRAIN", "BUS") else parsed.mode
    at_time = payload.time_preference or parsed.departure_time

    missing = missing_details(origin, destination, mode, at_time)
    blocking = [m for m in missing if m in ("origin", "destination", "major_cities")]

    services: list[dict] = []
    if not blocking:
        services = find_services_at(
            _retriever.schedules,
            origin=origin,
            destination=destination,
            at_time=at_time,
            mode=mode,
            major_cities_only=True,
        )

    return {
        "status": "SUCCESS",
        "data": {
            "parsed_entities": parsed.model_dump(),
            "origin": origin,
            "destination": destination,
            "mode": mode if mode in ("TRAIN", "BUS") else None,
            "at_time": at_time,
            "missing": missing,
            "needs_clarification": bool(missing),
            "services": services,
        },
        "message": (
            f"{len(services)} service(s) found."
            if not missing
            else f"Need more detail: {', '.join(missing)}"
        ),
    }


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "agent": "planner", "version": "0.1.0-stub"}

