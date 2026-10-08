"""
Live Conditions & Advisory Agent.

Member 5 — Live Conditions & Advisory Agent

A microservice like the other three agents: it exposes a single MCP-style
endpoint, owns its own caching, and returns data rather than prose decisions.

    GET /mcp/conditions?origin=Kandy&destination=Colombo

The Orchestrator calls it during route planning and hands the verdict to the
Planning Agent, which uses it to decide whether to recommend the requested mode
or an alternative — and includes the advisory sentence in its reply.

Security posture: this agent fetches untrusted third-party text (RSS) and
deliberately never routes it through a language model. See `analysis.py`.
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from src.conditions.analysis import (
    SIMULATED_INCIDENTS,
    advisory_sentence,
    build_advisory,
    classify,
)
from src.conditions.gather import (
    HEADLINES,
    active_simulations,
    arm_incident_check,
    fetch_forecast_window,
    fetch_news_headlines,
    fetch_weather,
    incident_check_armed,
    set_simulated_incident,
    start_headline_worker,
    stop_headline_worker,
)
from src.security.gateway import IngressBlocked, enforce_ingress

app = FastAPI(
    title="Live Conditions Agent",
    description="Agent 5: current weather and live news, reduced to a route advisory.",
    version="0.1.0",
)


class ConditionsRequest(BaseModel):
    origin: str = ""
    destination: str = ""
    # The mode the traveller asked for, so the advisory can say whether *this*
    # mode is the one affected.
    travel_mode: str = "ANY"
    # When the traveller intends to leave, if they said. Used to report the
    # weather at that time rather than only right now.
    departure_time: str | None = None


@app.post("/mcp/conditions")
async def conditions(req: ConditionsRequest) -> dict:
    """
    Gathers live conditions for a journey and returns a structured verdict.

    Never fails the request: an unreachable feed yields `available: false` and
    the advisory says conditions are unknown. A planning flow that broke because
    a news site was down would be worse than one that plans without colour.
    """
    # This agent accepts free-text place names, so it enforces the same ingress
    # contract as every other agent rather than trusting its caller. Its output
    # never reaches a language model, but the input is still untrusted and this
    # keeps the S-01 invariant true as agents are added.
    try:
        origin = enforce_ingress(req.origin or "")
        destination = enforce_ingress(req.destination or "")
    except IngressBlocked as exc:
        raise HTTPException(
            status_code=400, detail=f"Blocked by security gateway: {exc}"
        ) from exc

    # Weather is only reported for a journey that exists. With half a route the
    # agent still checks for incidents — that is unconditional — but saying
    # "weather looks fine" before the traveller has said where they are going
    # answers a question they did not ask about a trip that does not exist.
    route_complete = bool(origin and destination)
    if route_complete:
        origin_weather = fetch_weather(origin or None)
        destination_weather = fetch_weather(destination or None)
    else:
        origin_weather = {"status": "UNKNOWN", "reason": "route not established yet"}
        destination_weather = {"status": "UNKNOWN", "reason": "route not established yet"}

    # The forecast at departure time, for the departure point. The agent owns
    # every weather decision: the caller is told what it found, including the
    # fact that there is no usable forecast, and decides how to phrase it.
    forecast = fetch_forecast_window(origin or None, req.departure_time)

    headlines: list[dict[str, Any]] = []
    news_available = False

    # Incidents are always checked. A real reported strike must never need a
    # toggle switched on to be mentioned — that would mean reporting a route is
    # clear simply because nobody asked. The demo toggle governs the *simulated*
    # incident alone.
    try:
        headlines = fetch_news_headlines()
        # An empty list is a legitimate answer ("nothing transit-relevant"), but
        # only if the scrape actually worked.
        news_available = True
    except Exception:
        news_available = False

    # Only reports about places on this journey are considered: a landslide on
    # the Badulla line should not mark a Colombo–Galle bus as disrupted.
    journey_places = {p for p in (origin, destination) if p}
    news = classify(headlines, journey_places=journey_places)
    advisory = build_advisory(
        origin_weather=origin_weather,
        destination_weather=destination_weather,
        news=news,
        news_available=news_available,
        requested_mode=req.travel_mode,
    )

    # Weather/news never override the traveller's explicit mode choice on their
    # own; the planner uses `avoid_modes` to *suggest* an alternative. Recorded
    # here so the reasoning is visible in the payload.
    advisory["requested_mode"] = req.travel_mode
    advisory["route_complete"] = route_complete

    # Whether a *simulated* incident is switched on. Real headlines are always
    # checked; only a fabricated one needs to be asked for.
    advisory["simulated_incident_active"] = bool(active_simulations())

    # No route means nothing can honestly be said about *this* journey.
    # Incidents are still fetched and counted — the work is done — but no claim
    # is made about "your route" while there isn't one, and no mode is suppressed
    # on a route the traveller has not described yet.
    sentence = advisory_sentence(advisory) if route_complete else None
    if not route_complete:
        advisory["avoid_modes"] = []
        advisory["travel_disrupted"] = False

    advisory["requested_mode_affected"] = bool(
        req.travel_mode in advisory.get("avoid_modes", [])
    )

    return {
        "status": "SUCCESS",
        "data": {
            "advisory": advisory,
            "forecast": forecast,
            "sentence": sentence,
            "news_count": len(headlines),
            "cache": HEADLINES.stats(),
        },
        "message": (
            f"Conditions: {advisory['severity']}"
            + (f" — {advisory['news']['reasons'][0]}" if advisory["news"]["reasons"] else "")
        ),
    }


@app.on_event("startup")
def _start_worker() -> None:
    """
    Scrape once now and every ten minutes after.

    Keeping the cache warm in the background is what makes "checked for
    incidents" mean something: the answer is a few minutes old at worst rather
    than however long ago someone last asked.
    """
    start_headline_worker()


@app.on_event("shutdown")
def _stop_worker() -> None:
    stop_headline_worker()


@app.get("/mcp/headline_cache")
def headline_cache() -> dict:
    """
    What the scraper is doing, and what it currently holds.

    Exposed because a background job nobody can inspect is a background job
    nobody can trust: if this says `overdue: true`, "no incidents reported" means
    "nobody has looked", and that is a very different answer.
    """
    return {
        "status": "SUCCESS",
        "data": {
            **HEADLINES.stats(),
            "headline_text": [e["headline"] for e in HEADLINES.entries()],
            "simulated_active": active_simulations(),
        },
        "message": "Headline cache status.",
    }


@app.get("/health")
def health() -> dict[str, Any]:
    return {"status": "ok", "agent": "conditions", "version": "0.1.0"}


@app.get("/mcp/sources")
def sources() -> dict[str, Any]:
    """
    What this agent reads, exposed for transparency.

    The weather provider needs no key and imposes no client attribution; the RSS
    feeds are public and are used as advisory colour only, never as a source of
    record for a timetable.
    """
    from src.conditions.gather import COORDINATES, RSS_FEEDS

    return {
        "status": "SUCCESS",
        "data": {
            "weather_provider": "Open-Meteo (https://open-meteo.com) — no API key",
            "news_feeds": RSS_FEEDS,
            "weather_places": sorted(COORDINATES),
            "note": (
                "Advisory only. Schedules come from the timetable fixtures; this "
                "agent never changes a timetable, it only advises on the journey."
            ),
        },
    }


# ── Demo controls ────────────────────────────────────────────────────────────
#
# Waiting for a real accident to demonstrate the advisory path is not practical,
# so a demo incident can be switched on. It is fed through the same fetch →
# classify → advise → planner pipeline as a live headline, so what is shown is
# the code that actually runs.
#
# It is always labelled as simulated, in the payload and in the traveller-facing
# sentence. Nothing here can invent a recommendation from scraped text: a
# recommendation only exists for incidents in our own registry.


class SimulateRequest(BaseModel):
    incident_id: Optional[str] = None
    active: bool = True


@app.get("/mcp/simulate_incident")
async def simulate_incident_state() -> dict:
    """
    Whether an incident is currently simulated.

    Read-only, so the UI can show the switch's real position after a reload
    instead of assuming the default.
    """
    return {
        "status": "SUCCESS",
        "data": {
            "active": active_simulations(),
            "simulated_incident_active": incident_check_armed(),
        },
        "message": (
            "Simulated incident active." if incident_check_armed() else "No simulated incident."
        ),
    }


@app.post("/mcp/simulate_incident")
async def simulate_incident(req: SimulateRequest) -> dict:
    """
    Switches a demo incident on or off.

    `incident_id: null` with `active: false` clears every simulation, which is
    what the UI's reset button does.
    """
    if not req.active:
        if req.incident_id:
            from src.conditions.gather import _ACTIVE_SIMULATIONS

            _ACTIVE_SIMULATIONS.pop(req.incident_id, None)
            armed = incident_check_armed()
        else:
            # Un-arming clears any simulation too, so nothing survives the switch
            # being turned off.
            arm_incident_check(False)
            armed = False
        return {
            "status": "SUCCESS",
            "data": {
                "active": active_simulations(),
                "simulated_incident_active": armed,
            },
            "message": (
                "Simulated incident off. Real headlines are still checked."
                if not armed
                else "Simulated incident cleared; real headlines still checked."
            ),
        }

    if req.incident_id not in SIMULATED_INCIDENTS:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown demo incident. Available: {sorted(SIMULATED_INCIDENTS)}",
        )

    # Arming the check and switching on an incident are one action from the UI's
    # point of view: the switch means "check for incidents on my route".
    arm_incident_check(True)
    set_simulated_incident(req.incident_id, SIMULATED_INCIDENTS[req.incident_id])
    spec = SIMULATED_INCIDENTS[req.incident_id]
    return {
        "status": "SUCCESS",
        "data": {
            "active": active_simulations(),
            "label": spec["label"],
            "simulated": True,
            "simulated_incident_active": True,
        },
        "message": f"Simulated incident switched on: {spec['label']}",
    }
