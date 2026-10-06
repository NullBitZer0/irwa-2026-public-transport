"""
Live weather and news conditions for a journey.

Member 5 — Live Conditions & Advisory Agent

Public transport advice is only useful if it accounts for what is happening today.
A route that is perfectly scheduled is the wrong answer when the line is flooded,
landslid, or on strike. This agent gathers two signals for a journey and reduces
them to a single advisory the planner can act on:

- **Weather** from Open-Meteo (no API key, free, no attribution burden).
- **Live news** from public RSS feeds, filtered to transit-relevant items.

⚠️ Both are third-party input and therefore untrusted.

The news text is never handed to a language model as instructions, and never
reaches an LLM prompt at all — see `analysis.py`. This is the fix for the latent
indirect-injection finding: the earlier disruption reader would have been spliced
into model context, where a headline is indistinguishable from an instruction.
Instead the text is reduced here, by rules, to a category and a severity; the
Orchestrator composes the traveller-facing sentence from *those*, never from the
headline. Anything that looks like an injected instruction is dropped outright.

Every failure mode degrades to "no information", never to a confident claim: if a
feed is down, the advisory says conditions are unknown rather than fine.
"""

from __future__ import annotations

import time
from typing import Any, Optional

import feedparser
import httpx

from src.security.guardrails import is_safe

# ── Weather ──────────────────────────────────────────────────────────────────

OPEN_METEO = "https://api.open-meteo.com/v1/forecast"

# Coordinates for the places this agent can advise on. Deliberately a closed
# table: a lat/long typo would silently report the weather for somewhere else,
# which is worse than reporting none.
COORDINATES: dict[str, tuple[float, float]] = {
    "colombo": (6.9271, 79.8612),
    "colombo fort": (6.9344, 79.8428),
    "kandy": (7.2906, 80.6337),
    "negombo": (7.2083, 79.8339),
    "makumbura": (7.2756, 79.8817),
    "galle": (6.0535, 80.2210),
    "matara": (5.9485, 80.5350),
    "matale": (7.4675, 80.4974),
    "jaffna": (9.6615, 80.0255),
    "badulla": (6.9934, 81.0600),
    "ella": (6.8668, 81.0460),
    "gampaha": (7.1983, 80.0980),
    "nugegoda": (6.9271, 79.8653),
    "ambalangoda": (6.0441, 80.0653),
    "kalutara": (6.5854, 79.9000),
    "ratnapura": (6.6828, 80.4028),
    "trincomalee": (8.5741, 81.2331),
    "anuradhapura": (8.3136, 80.4071),
    "polonnaruwa": (7.9433, 81.2490),
    "puttalam": (8.0313, 79.8281),
    "mannar": (8.9810, 79.9040),
    "batticaloa": (7.7170, 81.7000),
    "kurunegala": (7.4813, 80.3607),
    "avissawella": (6.9544, 80.2044),
}

# Thresholds from the Sri Lanka Department of Meteorology's public warnings,
# rounded to the values that actually change a traveller's decision.
HEAVY_RAIN_MM = 15.0        # mm in the current hour
STRONG_WIND_KMH = 40.0      # wind speed
STORM_CODE_RANGE = (95, 99)  # WMO thunderstorm codes
HEAVY_RAIN_CODE = 65        # heavy rain showers

TIMEOUT_SECONDS = 6.0


def _coords_for(place: Optional[str]) -> Optional[tuple[float, float]]:
    if not place:
        return None
    key = place.strip().lower()
    if key in COORDINATES:
        return COORDINATES[key]
    # "Colombo Fort" vs "colombo fort" handled above; allow a partial match for
    # "Kandy Fort", but only when it is unambiguous.
    matches = [name for name in COORDINATES if name in key or key in name]
    return COORDINATES[matches[0]] if len(matches) == 1 else None


def fetch_weather(place: Optional[str]) -> dict[str, Any]:
    """
    Current conditions for a place.

    Returns a dict that always has `status`. `ok` means real data; anything else
    means the caller must not claim the weather is good.
    """
    coords = _coords_for(place)
    if not coords:
        return {"status": "UNKNOWN", "place": place, "reason": "no coordinates for this place"}

    params = {
        "latitude": coords[0],
        "longitude": coords[1],
        "current": "temperature_2m,precipitation,rain,weather_code,wind_speed_10m",
        "timezone": "auto",
    }
    try:
        response = httpx.get(
            OPEN_METEO, params=params, timeout=TIMEOUT_SECONDS, follow_redirects=True
        )
        response.raise_for_status()
        current = response.json().get("current") or {}
    except Exception as exc:
        return {
            "status": "UNAVAILABLE",
            "place": place,
            "reason": f"weather service unreachable ({type(exc).__name__})",
        }

    if not current:
        return {"status": "UNAVAILABLE", "place": place, "reason": "weather service returned no data"}

    code = current.get("weather_code")
    rain = float(current.get("rain") or current.get("precipitation") or 0.0)
    wind = float(current.get("wind_speed_10m") or 0.0)

    severity = "clear"
    reasons: list[str] = []
    if isinstance(code, int) and code in STORM_CODE_RANGE:
        severity = "severe"
        reasons.append("thunderstorm")
    elif isinstance(code, int) and code == HEAVY_RAIN_CODE:
        severity = "severe"
        reasons.append("heavy rain showers")
    if rain >= HEAVY_RAIN_MM:
        severity = "severe" if severity == "severe" else "advisory"
        reasons.append(f"heavy rain ({rain:.0f}mm in the last hour)")
    if wind >= STRONG_WIND_KMH:
        severity = "severe" if severity == "severe" else "advisory"
        reasons.append(f"strong winds ({wind:.0f} km/h)")

    return {
        "status": "ok",
        "place": place,
        "temperature_c": current.get("temperature_2m"),
        "weather_code": code,
        "rain_mm": rain,
        "wind_kmh": wind,
        "severity": severity,
        "reasons": reasons,
    }


# ── Live news ────────────────────────────────────────────────────────────────

# Public RSS. Any feed that works without credentials is acceptable here: this is
# advisory colour, not a source of record, and the classifier below is
# deliberately conservative.
RSS_FEEDS: list[str] = [
    "http://www.adaderana.lk/rss.php",
    "https://www.newsfirst.lk/feed/",
]

MAX_HEADLINES_PER_FEED = 25
MAX_HEADLINE_CHARS = 160
NEWS_TTL_SECONDS = 20 * 60

# Transit vocabulary, split by what it implies for the journey.
DISRUPTION_TERMS = {
    "strike": "severe",
    "protest": "severe",
    "sabotage": "severe",
    "demonstration": "advisory",
    "blocked": "severe",
    "suspended": "severe",
    "cancelled": "severe",
    "canceled": "severe",
    "closed": "advisory",
    "derailment": "severe",
    "accident": "advisory",
    "landslide": "severe",
    "flood": "severe",
    "flooded": "severe",
    "washout": "severe",
    "delay": "advisory",
    "delayed": "advisory",
    "disruption": "advisory",
    "expressway": "advisory",
    "railway": "advisory",
    "train": "advisory",
    "bus": "advisory",
    "sltb": "advisory",
    "slr": "advisory",
    "station": "advisory",
}

_CACHE: dict[str, Any] = {"headlines": [], "fetched_at": 0.0}

# Demo incidents currently switched on, as {incident_id: {...}}. In-memory and
# per-process: a demo control should not outlive the process, and persisting it
# would mean a restart could show a traveller a stale simulated accident.
_ACTIVE_SIMULATIONS: dict[str, dict[str, str]] = {}


def set_simulated_incident(incident_id: str, spec: dict[str, Any]) -> None:
    """Switches on one demo incident. The spec comes from our own registry."""
    _ACTIVE_SIMULATIONS[incident_id] = {
        "headline": spec["headline"],
        "incident_id": incident_id,
        "recommendation": spec.get("recommendation") or "",
        "published": "just now",
        "source": "simulated",
        "simulated": "true",
    }


def clear_simulated_incidents() -> None:
    _ACTIVE_SIMULATIONS.clear()


def active_simulations() -> list[str]:
    return sorted(_ACTIVE_SIMULATIONS)


def fetch_news_headlines(force_refresh: bool = False) -> list[dict[str, str]]:
    """
    Transit-relevant headlines from public RSS.

    Entries are returned as *data only*. They are never used as instructions and
    never included in a model prompt; `analysis.py` reduces them to categories.
    """
    now = time.time()
    if not force_refresh and _CACHE["headlines"] and now - _CACHE["fetched_at"] < NEWS_TTL_SECONDS:
        return list(_CACHE["headlines"]) + list(_ACTIVE_SIMULATIONS.values())

    collected: list[dict[str, str]] = []
    for feed_url in RSS_FEEDS:
        try:
            parsed = feedparser.parse(feed_url)
        except Exception:
            # One dead feed must not lose the others.
            continue

        for entry in parsed.entries[:MAX_HEADLINES_PER_FEED]:
            title = _clean_text(entry.get("title", ""))
            if not title:
                continue
            lowered = title.lower()
            if not any(term in lowered for term in DISRUPTION_TERMS):
                continue
            # Drop anything carrying what looks like an injected instruction, so
            # a compromised feed cannot smuggle text past the classifier into a
            # reply. This is cheap and deliberately paranoid.
            if not is_safe(title):
                continue
            collected.append(
                {
                    "headline": title[:MAX_HEADLINE_CHARS],
                    "published": _clean_text(entry.get("published", "recent")),
                    "source": feed_url,
                }
            )

    _CACHE.update({"headlines": collected, "fetched_at": now})

    # Simulated incidents are appended to the live list rather than replacing it,
    # so a demo exercises the same classification path a real headline takes.
    # Fetched first from the cache: a demo control must respond instantly, even
    # with the cache warm.
    combined = list(_CACHE["headlines"]) + list(_ACTIVE_SIMULATIONS.values())
    return combined


def _clean_text(value: Any) -> str:
    """
    Strip markup and collapse whitespace from feed text.

    RSS summaries routinely contain HTML fragments and tracking markup. We only
    want the words, because this text ends up in a JSON payload and, after
    classification, in a traveller-facing sentence.
    """
    import html as html_mod
    import re

    text = str(value or "")
    text = re.sub(r"<[^>]+>", " ", text)
    text = html_mod.unescape(text)
    return re.sub(r"\s+", " ", text).strip()
