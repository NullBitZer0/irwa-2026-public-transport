"""
Turning raw weather and news into an advisory the planner can use.

Member 5 — Live Conditions & Advisory Agent

This is the security-critical part of the conditions agent, and it is
deliberately *not* an LLM call.

Why not? Because the input is a news headline written by a stranger, and the
output is a sentence shown to a traveller and consumed by the planner. An LLM in
this path would be reading attacker-influenced text as if it were instruction,
which is the latent indirect-injection finding this agent closes. Reducing the
text here with fixed rules means:

- no fetched text ever reaches a model prompt;
- no fetched text is ever echoed to the traveller verbatim;
- the traveller-facing sentence is assembled from a closed set of phrases.

What the planner receives is therefore a small structured verdict — a severity,
a short reason drawn from a fixed vocabulary, and whether the affected mode
should be avoided — not prose scraped from a website.
"""

from __future__ import annotations

from typing import Any, Optional

SEVERITY_ORDER = {"clear": 0, "advisory": 1, "severe": 2}


# ── Demo incidents ───────────────────────────────────────────────────────────
#
# Incidents we inject ourselves, for demonstrating the advisory path in a viva
# without waiting for a real accident.
#
# Two rules keep this honest:
#
# 1. They go through the *same* classifier and the *same* planner demotion as a
#    real headline. Nothing is special-cased downstream, so what the demo shows
#    is the code that runs in production.
# 2. They carry an explicit recommendation because we wrote it. A real headline
#    never produces one — there is no trusted source for "use the next entrance",
#    and inventing one from scraped text would be a guess presented as advice.
#
# Every simulated item is tagged `simulated: true`, and the UI labels it, so a
# demo incident can never be mistaken for a live report.
SIMULATED_INCIDENTS: dict[str, dict[str, Any]] = {
    "negombo_highway_accident": {
        "label": "Accident at the Negombo highway entrance",
        "headline": "Accident reported at the Negombo highway entrance blocking the approach",
        "modes": ["BUS"],
        "severity": "severe",
        "recommendation": "Use the next highway entrance instead — Katunayake (Exit 02).",
    },
    "kandy_rail_strike": {
        "label": "Railway strike affecting the Kandy line",
        "headline": "Railway strike reported on the Kandy line",
        "modes": ["TRAIN"],
        "severity": "severe",
        "recommendation": "Consider a bus from Makumbura, or travel after the strike is called off.",
    },
    "highway_heavy_rain": {
        "label": "Heavy rain on the Southern Expressway",
        "headline": "Heavy rain reported on the Southern Expressway near Galle",
        "modes": ["BUS"],
        "severity": "advisory",
        "recommendation": "Allow extra journey time, or take the coastal road.",
    },
}

# Headline vocabulary → the phrase the traveller is shown. A headline never
# becomes the message; it selects a message. This mapping is the whole reason a
# feed compromise cannot inject instructions into a reply.
SEVERE_PHRASES = {
    "strike": "reports of a strike",
    "protest": "reports of a protest",
    "sabotage": "reports of sabotage",
    "blocked": "reports of a blocked line",
    "suspended": "reports of a suspended service",
    "cancelled": "reports of cancellations",
    "canceled": "reports of cancellations",
    "derailment": "a derailment report",
    "landslide": "landslides reported",
    "flood": "flooding reported",
    "flooded": "flooding reported",
    "washout": "a washout reported",
}

ADVISORY_PHRASES = {
    "delay": "delay reports",
    "delayed": "delay reports",
    "disruption": "disruption reports",
    "accident": "an accident report",
    "closed": "a closure report",
    "demonstration": "a demonstration report",
}

# Which transport modes a term implicates. An empty list means "general".
RAIL_TERMS = {"railway", "train", "slr", "station"}
ROAD_TERMS = {"bus", "sltb", "expressway", "road"}


# Places this agent knows how to reason about, used to decide whether a headline
# is about *this* journey. Kept separate from the coordinate table because the
# question here is textual, not geographical.
PLACE_HINTS = {
    "colombo", "kandy", "negombo", "galle", "matara", "matale", "jaffna",
    "badulla", "ella", "gampaha", "nugegoda", "ambalangoda", "kalutara",
    "ratnapura", "trincomalee", "anuradhapura", "polonnaruwa", "puttalam",
    "mannar", "batticaloa", "kurunegala", "avissawella", "makumbura",
}

# Roads and corridors. A headline naming one of these is about that corridor, so
# it is checked against the journey too.
CORRIDOR_HINTS = {
    "southern expressway": {"colombo", "galle", "matara", "kalutara"},
    "western expressway": {"colombo", "gampaha", "avissawella"},
    "northern expressway": {"colombo", "anuradhapura", "puttalam"},
    "e04": {"colombo", "galle", "matara"},
    "e01": {"colombo", "matara"},
}


def _mentions_place(text: str) -> set[str]:
    """
    Known places mentioned in a headline, including via a named corridor.

    Case-folded here rather than relying on the caller: headlines are properly
    cased ("near Kandy"), and a helper that silently misses them would treat every
    report as national and warn about every journey.
    """
    text = text.lower()
    found = {place for place in PLACE_HINTS if place in text}
    for corridor, places in CORRIDOR_HINTS.items():
        if corridor in text:
            found |= places
    return found


def relevant_to_journey(headline: str, journey_places: set[str]) -> bool:
    """
    Is this headline about the traveller's journey?

    Without this, any national headline mentioning "landslide" and "railway"
    marks every journey as disrupted — including one on the far side of the
    island. That trains the traveller to ignore the warning, which is worse than
    not warning at all.

    The rule is deliberately asymmetric:

    - A headline naming somewhere we recognise is only relevant if that place is
      on this journey.
    - A headline naming no recognisable place is treated as national and kept.
      A strike called across the network really does affect everyone, and we
      cannot tell otherwise.
    """
    mentioned = _mentions_place(headline)
    if not mentioned:
        return True  # national / unplaceable — keep it
    if not journey_places:
        return True  # no journey to compare against
    return bool(mentioned & journey_places)


def classify(
    headlines: list[dict[str, str]],
    journey_places: set[str] | None = None,
) -> dict[str, Any]:
    """
    Reduces headlines to a severity, a reason and a set of affected modes.

    Headlines about other parts of the island are dropped: see
    `relevant_to_journey`.

    Returns `severity: "clear"` when nothing transit-relevant was found, which is
    a *positive* result only because the caller distinguishes it from
    "unavailable".
    """
    journey_places = {p.lower() for p in (journey_places or set())}
    ignored = 0
    severity = "clear"
    reasons: list[str] = []
    modes: set[str] = set()
    evidence: list[dict[str, str]] = []
    recommendation: str | None = None
    simulated = False

    for item in headlines:
        headline = str(item.get("headline", "")).lower()

        # A simulated incident is always about this journey by construction.
        if not item.get("simulated") and not relevant_to_journey(headline, journey_places):
            ignored += 1
            continue

        # A simulated incident already carries its own severity and modes; there
        # is no need to infer them from wording, and using our own wording would
        # only be testing the keyword list against itself.
        if item.get("simulated") and item.get("incident_id") in SIMULATED_INCIDENTS:
            spec = SIMULATED_INCIDENTS[item["incident_id"]]
            simulated = True
            if item.get("recommendation"):
                recommendation = item["recommendation"]
            if SEVERITY_ORDER.get(spec["severity"], 0) > SEVERITY_ORDER[severity]:
                severity = spec["severity"]
            # Front of the list: this is the report with an actionable
            # recommendation attached, so it is the one worth reading first.
            reasons.insert(0, f"a simulated incident: {spec['label']}")
            modes |= set(spec["modes"])
            evidence.append(
                {"term": "simulated", "headline": spec["headline"][:120],
                 "simulated": "true"}
            )
            continue

        for term in sorted(SEVERE_PHRASES, key=len, reverse=True):
            if term not in headline:
                continue
            severity = "severe" if SEVERITY_ORDER["severe"] > SEVERITY_ORDER[severity] else severity
            reasons.append(SEVERE_PHRASES[term])
            modes |= _modes_for(headline)
            evidence.append(
                {"term": term, "headline": str(item.get("headline", ""))[:120]}
            )
            break
        else:
            for term in sorted(ADVISORY_PHRASES, key=len, reverse=True):
                if term not in headline:
                    continue
                if SEVERITY_ORDER["advisory"] > SEVERITY_ORDER[severity]:
                    severity = "advisory"
                reasons.append(ADVISORY_PHRASES[term])
                modes |= _modes_for(headline)
                evidence.append(
                    {"term": term, "headline": str(item.get("headline", ""))[:120]}
                )
                break

    # Deduplicate while keeping order: several feeds carry the same story.
    unique_reasons = list(dict.fromkeys(reasons))

    return {
        "severity": severity,
        "reasons": unique_reasons[:3],
        "affected_modes": sorted(modes),
        "evidence": evidence[:3],
        "recommendation": recommendation,
        "simulated": simulated,
        # Surfaced so a demo or a viva can show that off-route reports were seen
        # and deliberately ignored, rather than silently dropped.
        "ignored_off_route": ignored,
    }


def _modes_for(headline: str) -> set[str]:
    modes: set[str] = set()
    if any(term in headline for term in RAIL_TERMS):
        modes.add("TRAIN")
    if any(term in headline for term in ROAD_TERMS):
        modes.add("BUS")
    return modes


def weather_severity(weather: dict[str, Any]) -> tuple[str, list[str]]:
    """Pulls the severity and reasons out of a `fetch_weather` result."""
    if weather.get("status") != "ok":
        return "unknown", []
    return weather.get("severity", "clear"), list(weather.get("reasons") or [])


def build_advisory(
    origin_weather: dict[str, Any],
    destination_weather: dict[str, Any],
    news: dict[str, Any],
    news_available: bool,
    requested_mode: str = "ANY",
) -> dict[str, Any]:
    """
    Combines both signals into one verdict for a journey.

    `travel_disrupted` is what the planner acts on: it suppresses the affected
    mode from the recommendation and produces the alternative. Weather is judged
    on the worse of the two endpoints, since a storm at the destination ruins the
    trip just as thoroughly as one at the origin.
    """
    origin_sev, origin_reasons = weather_severity(origin_weather)
    dest_sev, dest_reasons = weather_severity(destination_weather)

    weather_sev = max(
        (origin_sev, dest_sev),
        key=lambda s: SEVERITY_ORDER.get(s, 0) if s != "unknown" else -1,
    )
    weather_reasons = list(dict.fromkeys(origin_reasons + dest_reasons))[:3]

    news_sev = news.get("severity", "clear") if news_available else "unknown"
    affected = set(news.get("affected_modes") or []) if news_available else set()

    severity = max(
        (weather_sev, news_sev),
        key=lambda s: SEVERITY_ORDER.get(s, 0) if s != "unknown" else -1,
    )

    # Weather does not cancel a service, so it informs rather than blocks. News
    # about a strike or a landslide does.
    travel_disrupted = news_sev == "severe"

    requested = (requested_mode or "ANY").upper()

    return {
        "severity": severity,
        "recommendation": news.get("recommendation"),
        "simulated": bool(news.get("simulated")),
        "requested_mode": requested,
        # Only true when the disruption hits the mode the traveller actually
        # asked for. A strike on the railway is not news to someone who asked for
        # a bus, and saying so would be noise.
        "requested_mode_affected": bool(travel_disrupted and requested in affected),
        "weather": {
            "severity": weather_sev,
            "reasons": weather_reasons,
            "origin": _weather_digest(origin_weather),
            "destination": _weather_digest(destination_weather),
        },
        "news": {
            "available": news_available,
            "severity": news_sev,
            "reasons": list(news.get("reasons") or [])[:3],
            "affected_modes": sorted(affected),
        },
        "avoid_modes": sorted(affected) if travel_disrupted else [],
        "travel_disrupted": travel_disrupted,
    }


def _weather_digest(weather: dict[str, Any]) -> dict[str, Any]:
    """Only the fields worth showing, so nothing unbounded leaks through."""
    if weather.get("status") != "ok":
        return {"status": weather.get("status", "unknown")}
    return {
        "status": "ok",
        "temperature_c": weather.get("temperature_c"),
        "severity": weather.get("severity", "clear"),
    }


def advisory_sentence(advisory: dict[str, Any]) -> Optional[str]:
    """
    The traveller-facing sentence, assembled from fixed phrases.

    Returns None when conditions are unremarkable, so the caller can leave the
    message clean rather than padding it with "everything is fine".

    Note what cannot happen here: no fetched text is interpolated into this
    string. It is built from the severity and the fixed phrase vocabulary in
    `analysis.py`, so a hostile headline cannot put words in a traveller's mouth.
    """
    weather = advisory.get("weather") or {}
    news = advisory.get("news") or {}

    if advisory.get("travel_disrupted"):
        reasons = news.get("reasons") or ["a reported disruption"]
        modes = advisory.get("avoid_modes") or []
        requested = advisory.get("requested_mode") or "ANY"

        if not advisory.get("requested_mode_affected") and requested in ("TRAIN", "BUS"):
            # The traveller asked for a mode that is not the one affected. Mention
            # it once, briefly, and get out of the way.
            return (
                f"ℹ️ Quick note: {reasons[0]}, but that affects "
                f"{'rail' if requested == 'BUS' else 'bus'} services — your "
                f"{requested.lower()} options below should be unaffected."
            )

        mode_word = ""
        if "TRAIN" in modes and "BUS" not in modes:
            mode_word = "Rail services are the ones affected — "
        elif "BUS" in modes and "TRAIN" not in modes:
            mode_word = "Bus services are the ones affected — "

        # A recommendation only ever comes from an incident we wrote ourselves;
        # a real headline cannot produce one, because nothing in a news feed is
        # authoritative enough to tell a traveller which exit to use.
        recommendation = advisory.get("recommendation")
        if recommendation:
            # The recommendation is a full sentence; make sure the closing prompt
            # starts after it rather than butting up against the full stop.
            recommendation = recommendation.rstrip()
            if not recommendation.endswith((".", "!", "?")):
                recommendation += "."
            fix = f"{mode_word}{recommendation} "
        else:
            fix = f"{mode_word}I'd recommend an alternative service instead. "

        demo = " _(simulated for demonstration)_" if advisory.get("simulated") else ""
        return (
            f"⚠️ I've seen {reasons[0]} for your route. "
            f"{fix}Tell me if you'd like me to look at other options.{demo}"
        )

    if advisory.get("severity") == "severe" and weather.get("reasons"):
        # Severe weather with no reported disruption: the services still run, so
        # this must not read as "I couldn't check", and must not cancel a route.
        return (
            f"⚠️ Weather warning for this route: {weather['reasons'][0]}. "
            f"Scheduled services should still run, but expect delays and allow "
            f"extra time. Check with the operator before you set off."
        )

    if advisory.get("severity") == "advisory":
        parts = []
        if weather.get("reasons"):
            parts.append(f"the weather ({weather['reasons'][0]})")
        if news.get("reasons"):
            parts.append(f"the news ({news['reasons'][0]})")
        detail = " and ".join(parts) if parts else "minor reports"
        return (
            f"ℹ️ A quick check on current conditions: {detail}. "
            f"Routes look normal, but allow a little extra time."
        )

    if advisory.get("severity") == "clear":
        return (
            "✅ Weather and live news look fine for this journey — "
            "let's use the normal routes."
        )

    # Severity unknown: one or both sources could not be reached. Saying "fine"
    # here would be a claim we cannot support.
    return (
        "ℹ️ I couldn't check live weather or news just now, so I'm not able to "
        "confirm conditions. The routes below are the scheduled ones."
    )
