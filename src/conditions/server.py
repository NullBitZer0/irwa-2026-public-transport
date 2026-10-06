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
    disarmed_sentence,
)
from src.conditions.gather import (
    active_simulations,
    arm_incident_check,
    fetch_news_headlines,
    fetch_weather,
    incident_check_armed,
    set_simulated_incident,
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

    origin_weather = fetch_weather(origin or None)
    destination_weather = fetch_weather(destination or None)

    headlines: list[dict[str, str]] = []
    news_available = False

    # Incidents are only looked for when the traveller has armed the check.
    # Weather is unconditional; a disruption warning on every journey is not.
    if incident_check_armed():
        try:
            headlines = fetch_news_headlines()
            # An empty list is a legitimate answer ("nothing transit-relevant"),
            # but only if the fetch actually worked. feedparser returns an empty
            # list on failure too, so "no entries parsed at all" is unavailable.
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
    advisory["incident_check_armed"] = incident_check_armed()

    # A disarmed check is not "all clear": nothing was looked for, so the wording
    # has to say that rather than imply there is nothing to report.
    # Armed -> the real advisory. Disarmed -> wording that says nothing was
    # checked. (An earlier version of this line had the branches the wrong way
    # round and silently returned no sentence at all while armed.)
    sentence = (
        advisory_sentence(advisory)
        if incident_check_armed()
        else disarmed_sentence(advisory)
    )
    advisory["requested_mode_affected"] = bool(
        req.travel_mode in advisory.get("avoid_modes", [])
    )

    return {
        "status": "SUCCESS",
        "data": {
            "advisory": advisory,
            "sentence": sentence,
            "news_count": len(headlines),
            "incident_check_armed": incident_check_armed(),
        },
        "message": (
            f"Conditions: {advisory['severity']}"
            + (f" — {advisory['news']['reasons'][0]}" if advisory["news"]["reasons"] else "")
        ),
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
                "incident_check_armed": armed,
            },
            "message": (
                "Incident check off."
                if not armed
                else "Simulated incident cleared; incident check still on."
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
            "incident_check_armed": True,
        },
        "message": f"Simulated incident switched on: {spec['label']}",
    }
