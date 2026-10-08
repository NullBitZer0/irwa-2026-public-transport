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

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from src.planner.connection_planner import (
    find_connections,
    public_connections,
    rank_connections,
)
from src.planner.fares import resolve_fare
from src.planner.geo import (
    ISLAND_RINGS,
    boundary_source,
    city_nodes,
    corridors,
    map_bounds,
    resolve,
    unmapped_places,
)
from src.planner.hybrid_retriever import HybridTransitRetriever, _serves_direction
from src.planner.journey_search import (
    apply_conditions,
    find_services_at,
    missing_details,
)
from src.planner.nlp_parser import extract_transit_intent
from src.security.gateway import IngressBlocked, enforce_ingress

app = FastAPI(
    title="Planning Agent — IR & NLP Worker",
    description="Agent 2: Hybrid retrieval, NLP entity extraction, and live alerts.",
    version="0.1.0-stub",
)

_retriever = HybridTransitRetriever()

# How far to look for a train-then-coach change when the traveller asked for one
# mode. Deliberately wider than the default result set: the mixed option is
# usually not the fastest thing on the corridor, so a narrow window tends to
# exclude the very option being looked for.
MIXED_FALLBACK_RESULTS = 20


class PlanRouteRequest(BaseModel):
    origin: str = ""
    # What the traveller chose when asked: "time" (soonest) or "budget"
    # (cheapest). Empty means they have not been asked yet, and a search with no
    # direct service then returns the question rather than guessing for them.
    preference: str = ""
    destination: str = ""
    travel_mode: str = "ANY"
    date_str: str = "TODAY"
    time_preference: str | None = None
    passengers: int = 1
    raw_query: str | None = None
    # Verdict from the Live Conditions Agent: which modes to avoid, if any.
    # Deliberately structured (a list of mode codes) rather than prose, so the
    # planner cannot be talked into a different route by a news headline.
    avoid_modes: list[str] = Field(default_factory=list)


@app.post("/mcp/plan_route")
async def plan_route(payload: PlanRouteRequest) -> dict:
    """
    Route planning MCP endpoint.
    Returns hybrid-retrieved route options for the given origin/destination.
    """
    raw_query = payload.raw_query or f"{payload.origin} to {payload.destination}"

    # Defence in depth: the Orchestrator already ran the gateway, but these
    # endpoints accept raw_query directly, so they enforce the same contract
    # rather than trusting their caller (the gap S-01 flags).
    try:
        raw_query = enforce_ingress(raw_query)
    except IngressBlocked as exc:
        raise HTTPException(status_code=400, detail=f"Blocked by security gateway: {exc}")

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

    # Retrieval is relevance-based, so it can return a service that is about the
    # right subject but runs the wrong way — Colombo → Jaffna for a request for
    # Kandy → Jaffna. Only a service that actually stops at both ends, in that
    # order, is a direct answer.
    #
    # Without this the endpoint reports a direct service where none exists, and
    # the connection search below never runs because `candidates` is not empty.
    # Only filterable when both ends are known. With one end, the traveller is
    # still specifying the journey ("services to Galle"), and dropping the
    # results would answer "nothing goes to Galle" when the opposite is true.
    if candidates and origin and destination:
        serving = [c for c in candidates if _serves_direction(c, origin, destination)]
        candidates = serving

    connections: list = []
    mixed_fallback = False
    if not candidates and origin and destination:
        connections = find_connections(
            _retriever.schedules,
            origin,
            destination,
            mode=travel_mode,
            strategy=(payload.preference or "time").strip().lower() or "time",
        )
        if travel_mode != "ANY":
            already = {c["route_id"] for c in connections}
            # A wide window on purpose. The default result set is small, so the
            # one mixed option could fall outside it and never be offered —
            # which is precisely the case this fallback exists to cover. Asking
            # for more is cheap; returning a slower list than the traveller
            # could have had is not.
            extra = [
                c
                for c in find_connections(
                    _retriever.schedules,
                    origin,
                    destination,
                    mode="ANY",
                    max_results=MIXED_FALLBACK_RESULTS,
                )
                if c["mixed_mode"] and c["route_id"] not in already
            ]
            mixed_fallback = bool(extra) or not connections
            connections = rank_connections(
                connections + extra,
                strategy=(payload.preference or "time").strip().lower() or "time",
            )

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


def _journey_message(
    services: list, connections: list, missing: list, needs_preference: bool
) -> str:
    """A one-line summary. Never empty, because the envelope requires a string."""
    if missing:
        return f"Need more detail: {', '.join(missing)}"
    if services:
        return f"{len(services)} service(s) found."
    if connections:
        return f"{len(connections)} connecting option(s)."
    if needs_preference:
        return "No direct service; changes are available — ask whether to optimise for time or cost."
    return "No service found."


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

    # Defence in depth: the Orchestrator already ran the gateway, but these
    # endpoints accept raw_query directly, so they enforce the same contract
    # rather than trusting their caller (the gap S-01 flags).
    try:
        raw_query = enforce_ingress(raw_query)
    except IngressBlocked as exc:
        raise HTTPException(status_code=400, detail=f"Blocked by security gateway: {exc}")
    parsed = extract_transit_intent(raw_query)

    origin = payload.origin or (parsed.origin or "")
    destination = payload.destination or (parsed.destination or "")
    # The Orchestrator sends "ANY" to mean "the traveller has not chosen yet".
    mode = payload.travel_mode if payload.travel_mode in ("TRAIN", "BUS") else parsed.mode
    at_time = payload.time_preference or parsed.departure_time

    missing = missing_details(origin, destination, mode, at_time)
    blocking = [m for m in missing if m in ("origin", "destination", "major_cities")]

    services: list[dict] = []
    connections: list[dict] = []
    needs_preference = False
    conditions_note: str | None = None
    if not blocking:
        services = find_services_at(
            _retriever.schedules,
            origin=origin,
            destination=destination,
            at_time=at_time,
            mode=mode,
            major_cities_only=True,
        )

        # Live conditions: a mode reported as disrupted is demoted below the
        # alternatives, not removed. The planner does not decide whether a
        # service is actually running — the traveller might know better.
        services, _demoted, conditions_note = apply_conditions(
            services, payload.avoid_modes, requested_mode=mode
        )

        # The traveller's own mode is the one reported as disrupted. Recommending
        # "try something else" is only useful if we actually go and look, so
        # search the other mode too and offer it as a genuine alternative.
        if mode in {m.upper() for m in payload.avoid_modes} and not _demoted:
            other_mode = "BUS" if mode == "TRAIN" else "TRAIN"
            alternatives = find_services_at(
                _retriever.schedules,
                origin=origin,
                destination=destination,
                at_time=at_time,
                mode=other_mode,
                major_cities_only=True,
            )
            if alternatives:
                for alt in alternatives:
                    alt["alternative_for_disruption"] = True
                services = services + alternatives
                conditions_note = (
                    f"Live reports flag {mode.lower()} services on this route, so "
                    f"the {other_mode.lower()} options at the end are the ones to look at."
                )

        # Nothing direct runs. Rather than report "no service" — which is
        # almost never true on these corridors — look for a change, and if there
        # are any, ask whether the traveller wants it soon or cheap. Guessing
        # would be the planner's opinion of which matters more.
        if not services and not blocking:
            preference = (payload.preference or "").strip().lower()
            if preference in {"time", "budget"}:
                connections = find_connections(
                    _retriever.schedules,
                    origin,
                    destination,
                    mode="ANY",
                    after=at_time if isinstance(at_time, str) else None,
                    strategy=preference,
                )
            else:
                candidates = find_connections(
                    _retriever.schedules,
                    origin,
                    destination,
                    mode="ANY",
                    after=at_time if isinstance(at_time, str) else None,
                    strategy="time",
                )
                # Only ask when there is genuinely something to choose between.
                needs_preference = bool(candidates)

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
            "connections": public_connections(connections),
            "needs_preference": needs_preference,
            "preference_options": (
                [
                    {"label": "⚡ Fastest", "value": "time", "hint": "get there soonest"},
                    {"label": "💰 Cheapest", "value": "budget", "hint": "spend as little as possible"},
                ]
                if needs_preference
                else []
            ),
            "conditions_note": conditions_note,
            "avoid_modes": payload.avoid_modes,
        },
        # Always a string: the MCP envelope's `message` is required, and a None
        # here fails validation in the caller — which looks like the planner being
        # unreachable and silently drops the traveller onto the fallback path.
        "message": _journey_message(services, connections, missing, needs_preference),
    }


def _mode_of(record: dict) -> str:
    """TRAIN or BUS, from the route id the corpus uses."""
    return "TRAIN" if "TRAIN" in str(record.get("route_id", "")).upper() else "BUS"


def _service_fare(record: dict) -> dict:
    """
    The fare to show in the timetable: published if there is one, else estimated.

    Display only. Booking reads `base_fare_lkr` from the record itself and still
    refuses an unpriced service, so an estimate is never charged to anyone — the
    difference between quoting a modelled price and taking a payment for it.
    """
    resolved = resolve_fare(record)
    return {
        "fare_lkr": resolved["fare_lkr"],
        "fare_estimated": resolved["fare_estimated"],
        "fare_basis": resolved["fare_basis"],
    }


@app.get("/mcp/schedules")
def list_schedules(
    mode: str = "ALL",
    origin: str = "",
    destination: str = "",
    limit: int = 200,
) -> dict:
    """
    The timetable as a table, for the schedules view.

    Deliberately not a journey search: this lists services so a traveller can
    browse what runs, including directions and times they did not think to ask
    for. `limit` is capped because the corpus is 812 rows and the UI is a table,
    not a report.
    """
    wanted = mode.upper()
    if wanted not in {"ALL", "BUS", "TRAIN"}:
        raise HTTPException(status_code=400, detail=f"mode must be BUS, TRAIN or ALL (got {mode!r})")

    limit = max(1, min(int(limit), 500))
    origin_key = origin.strip().lower()
    destination_key = destination.strip().lower()

    rows: list[dict] = []
    for record in _retriever.schedules:
        if wanted != "ALL" and _mode_of(record) != wanted:
            continue
        if origin_key and origin_key not in str(record.get("origin", "")).lower():
            continue
        if destination_key and destination_key not in str(record.get("destination", "")).lower():
            continue
        rows.append(record)

    # Sorted by departure so the table reads like a timetable, and stable on
    # route_id so equal times do not reshuffle between refreshes.
    rows.sort(key=lambda r: (str(r.get("departure_time") or ""), str(r.get("route_id") or "")))

    services = [
        {
            **_service_fare(r),
            "route_id": r.get("route_id"),
            "service_name": r.get("service_name"),
            "provider": r.get("provider"),
            "mode": _mode_of(r),
            "origin": r.get("origin"),
            "destination": r.get("destination"),
            "departure_time": r.get("departure_time"),
            "arrival_time": r.get("arrival_time"),
            "classes": r.get("classes") or [],
            "transit_type": r.get("transit_type"),
            "stop_count": len(r.get("stops") or []),
            "stops": r.get("stops") or [],
            # Carried through so a synthetic service is never presented as
            # published timetable data.
            "synthetic": bool(r.get("synthetic")),
            "synthetic_fields": r.get("synthetic_fields") or [],
        }
        for r in rows[:limit]
    ]

    return {
        "status": "SUCCESS",
        "data": {
            "services": services,
            "matched": len(rows),
            "returned": len(services),
            "truncated": len(rows) > len(services),
            "mode": wanted,
            # Stated once rather than per row: most estimates here are distance
            # modelled, and saying so only on the fare cell would leave the
            # column looking authoritative.
            "estimated_fares": sum(1 for s in services if s["fare_estimated"]),
            "published_fares": sum(1 for s in services if not s["fare_estimated"]),
        },
        "message": f"{len(services)} service(s) listed ({len(rows)} matched).",
    }


@app.get("/mcp/map_routes")
def map_routes(mode: str = "ALL") -> dict:
    """
    Available routes as city-to-city corridors with coordinates.

    Coverage is reported alongside the routes. An earlier draft drew 608 of 812
    services and said nothing about the rest; a map that silently omits a fifth
    of the network reads as "that route does not exist", which is a worse answer
    than "204 services run inside one city and are listed in the schedules tab".
    """
    wanted = mode.upper()
    if wanted not in {"ALL", "BUS", "TRAIN"}:
        raise HTTPException(status_code=400, detail=f"mode must be BUS, TRAIN or ALL (got {mode!r})")

    schedules = _retriever.schedules
    if wanted != "ALL":
        schedules = [r for r in schedules if _mode_of(r) == wanted]

    all_routes = corridors(schedules)
    total = len(schedules)
    drawn = sum(c["service_count"] for c in all_routes)

    # Services that never leave their city have no line to draw, but they do
    # exist, so they are counted rather than dropped.
    intra_city = 0
    for record in schedules:
        origin = resolve(record.get("origin"))
        destination = resolve(record.get("destination"))
        if origin and destination and origin[0] == destination[0]:
            intra_city += 1

    return {
        "status": "SUCCESS",
        "data": {
            # Every ring, not just the main island: Mannar is a town on Mannar
            # Island, and dropping the smaller rings put it in the sea.
            "rings": [[[lat, lng] for lat, lng in ring] for ring in ISLAND_RINGS],
            "outline": [[lat, lng] for lat, lng in ISLAND_RINGS[0]],
            "bounds": map_bounds(),
            "boundary_source": boundary_source(),
            "nodes": city_nodes(schedules),
            "corridors": all_routes,
            "coverage": {
                "services_in_scope": total,
                "drawn_as_corridors": drawn,
                "intra_city_not_drawn": intra_city,
                "unmapped_places": unmapped_places(schedules),
                "note": (
                    "Corridors are city-to-city. Bus stops within Greater Colombo are "
                    "clustered into Colombo, and services that run entirely within one "
                    "city have no line to draw — they appear in the schedules tab."
                ),
            },
            "mode": wanted,
        },
        "message": f"{len(all_routes)} corridor(s) from {total} service(s).",
    }


@app.get("/mcp/fare")
def quote_fare(route_id: str) -> dict:
    """
    Authoritative fare for a route — the Planning Agent is the pricing authority.

    The Booking Agent must never take a price from the client: if it did, a
    tampered request could buy an LKR 850 ticket for LKR 1. So the Orchestrator
    quotes from here and books at this figure.

    Returns fare_unknown rather than 0 when no published fare exists, so a
    missing price can never be mistaken for a free ticket.
    """
    for service in _retriever.schedules:
        if service.get("route_id") == route_id:
            fare = service.get("base_fare_lkr") or 0.0
            unknown = bool(service.get("fare_unknown")) or fare <= 0
            return {
                "status": "SUCCESS",
                "data": {
                    "route_id": route_id,
                    "fare_lkr": None if unknown else float(fare),
                    "fare_unknown": unknown,
                    "source": "planner fare matrix",
                },
            }

    return {
        "status": "NOT_FOUND",
        "data": {
            "route_id": route_id,
            "fare_lkr": None,
            "fare_unknown": True,
            "source": "planner fare matrix",
        },
    }


@app.get("/health")
def health() -> dict:
    """
    Liveness, plus the two degradations a caller cannot otherwise see.

    `retrieval_fallback` and `index_freshness` are advisory. Retrieval still
    answers when either is set — that is the point of both mechanisms — so
    reporting a bare "ok" would hide the difference between "serving from the
    index" and "serving from the in-memory corpus because the index is stale".
    """
    return {
        "status": "ok",
        "agent": "planner",
        "version": "0.1.0-stub",
        "retrieval_backend": _retriever.backend_name,
        "retrieval_fallback": _retriever.fallback_reason,
        "index_freshness": _retriever.index_freshness,
        "corpus_size": len(_retriever.schedules),
    }

