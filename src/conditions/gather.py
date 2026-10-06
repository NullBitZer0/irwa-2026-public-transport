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

import os
import re
import threading
import time
from typing import Any, Iterable, Optional

import feedparser
import httpx

from src.security.guardrails import is_safe

# ── Weather ──────────────────────────────────────────────────────────────────

OPEN_METEO = "https://api.open-meteo.com/v1/forecast"

# OpenWeather, used as the primary weather source when a key is configured.
#
# The key is read from the environment and never logged, returned by an endpoint,
# or included in an error message: an OpenWeather key in a log file is a bill
# someone else pays. One Call 3.0 is deliberately *not* used — it is a paid plan
# and answers 401 on the free tier, so the free 2.5 current-conditions and
# forecast endpoints are the ones this integration depends on.
OPENWEATHER_CURRENT = "https://api.openweathermap.org/data/2.5/weather"
OPENWEATHER_FORECAST = "https://api.openweathermap.org/data/2.5/forecast"


def _openweather_key() -> Optional[str]:
    key = (os.getenv("OPENWEATHER_API_KEY") or "").strip()
    return key or None

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

# OpenWeather condition codes (https://openweathermap.org/weather-conditions),
# grouped into the two verdicts this project acts on. Mapping them by hand keeps
# the verdict independent of any provider's own severity field, which is a free
# text string in one API and a code in the other.
OPENWEATHER_THUNDERSTORM = range(200, 233)
OPENWEATHER_HEAVY_RAIN = range(500, 532)
OPENWEATHER_SNOW = range(600, 623)

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


def _classify_openweather(code: Optional[int], rain_mm: float, wind_kmh: float) -> tuple[str, list[str]]:
    """OpenWeather's codes and measurements -> the severity vocabulary used here."""
    severity = "clear"
    reasons: list[str] = []

    if isinstance(code, int) and code in OPENWEATHER_THUNDERSTORM:
        severity = "severe"
        reasons.append("thunderstorm")
    elif isinstance(code, int) and code in OPENWEATHER_HEAVY_RAIN:
        severity = "severe"
        reasons.append("heavy rain")
    elif isinstance(code, int) and code in OPENWEATHER_SNOW:
        severity = "advisory"
        reasons.append("snow")

    if rain_mm >= HEAVY_RAIN_MM:
        severity = "severe" if severity == "severe" else "advisory"
        reasons.append(f"heavy rain ({rain_mm:.0f}mm)")
    if wind_kmh >= STRONG_WIND_KMH:
        severity = "severe" if severity == "severe" else "advisory"
        reasons.append(f"strong winds ({wind_kmh:.0f} km/h)")

    return severity, reasons


def _fetch_openweather(coords: tuple[float, float], place: Optional[str]) -> Optional[dict[str, Any]]:
    """
    Current conditions from OpenWeather, or None if it cannot answer.

    Returns None rather than an error dict so the caller can fall back to the
    keyless source without treating a missing API key as bad weather.
    """
    key = _openweather_key()
    if not key:
        return None

    try:
        response = httpx.get(
            OPENWEATHER_CURRENT,
            params={"lat": coords[0], "lon": coords[1], "appid": key, "units": "metric"},
            timeout=TIMEOUT_SECONDS,
            follow_redirects=True,
        )
        if response.status_code != 200:
            # Deliberately does not include the body: OpenWeather error payloads
            # echo nothing useful, and the request URL carries the key.
            return None
        payload = response.json()
    except Exception:
        return None

    conditions = payload.get("weather") or [{}]
    code = conditions[0].get("id")
    main = payload.get("main") or {}
    wind = (payload.get("wind") or {}).get("speed") or 0.0
    rain = (payload.get("rain") or {}).get("1h") or (payload.get("rain") or {}).get("3h") or 0.0

    severity, reasons = _classify_openweather(code, float(rain), float(wind))
    return {
        "status": "ok",
        "place": place,
        "source": "OpenWeather",
        "temperature_c": main.get("temp"),
        "feels_like_c": main.get("feels_like"),
        "humidity_pct": main.get("humidity"),
        "weather_code": code,
        "description": conditions[0].get("description"),
        "rain_mm": float(rain),
        "wind_kmh": float(wind),
        "severity": severity,
        "reasons": reasons,
    }


def fetch_forecast_window(place: Optional[str], at_hour: Optional[str]) -> Optional[dict[str, Any]]:
    """
    The 3-hourly forecast slot nearest a requested departure time.

    Used so a traveller asking about a 6pm bus is told about 6pm, not about now.
    Returns None when there is no departure time, no key, or no matching slot —
    and the caller then simply says nothing rather than inventing a forecast.
    """
    key = _openweather_key()
    coords = _coords_for(place)
    if not key or not coords or not at_hour:
        return None

    hour = _hour_from(at_hour)
    if hour is None:
        return None

    try:
        response = httpx.get(
            OPENWEATHER_FORECAST,
            params={"lat": coords[0], "lon": coords[1], "appid": key, "units": "metric"},
            timeout=TIMEOUT_SECONDS,
            follow_redirects=True,
        )
        if response.status_code != 200:
            return None
        entries = response.json().get("list") or []
    except Exception:
        return None

    if not entries:
        return None

    def slot_hour(entry: dict[str, Any]) -> Optional[int]:
        stamp = str(entry.get("dt_txt") or "")
        try:
            return int(stamp[11:13])
        except (ValueError, IndexError):
            return None

    target = min(entries, key=lambda e: abs((slot_hour(e) if slot_hour(e) is not None else 99) - hour))
    slot = slot_hour(target)
    if slot is None:
        return None

    conditions = target.get("weather") or [{}]
    code = conditions[0].get("id")
    rain = (target.get("rain") or {}).get("3h") or 0.0
    wind = (target.get("wind") or {}).get("speed") or 0.0
    severity, reasons = _classify_openweather(code, float(rain), float(wind))

    return {
        "status": "ok",
        "source": "OpenWeather",
        "hour_local": slot,
        "at_local": target.get("dt_txt"),
        "requested_hour": at_hour,
        "temperature_c": (target.get("main") or {}).get("temp"),
        "description": conditions[0].get("description"),
        "rain_mm": float(rain),
        "wind_kmh": float(wind),
        "severity": severity,
        "reasons": reasons,
    }


def _hour_from(value: Any) -> Optional[int]:
    """Pulls an hour out of '18:30', '6pm', '18' — and nothing else."""
    text = str(value or "").strip().lower()
    digits = "".join(ch for ch in text if ch.isdigit() or ch == ":")
    if ":" in digits:
        head = digits.split(":")[0]
    else:
        match = re.match(r"^(\d{1,2})", digits)
        head = match.group(1) if match else ""
    if not head.isdigit():
        return None
    hour = int(head)
    if "pm" in text and hour < 12:
        hour += 12
    if "am" in text and hour == 12:
        hour = 0
    return hour if 0 <= hour <= 23 else None


def _fetch_open_meteo(place: Optional[str], coords: tuple[float, float]) -> dict[str, Any]:
    """Current conditions from Open-Meteo, which needs no API key."""

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
        "source": "Open-Meteo",
        "temperature_c": current.get("temperature_2m"),
        "weather_code": code,
        "rain_mm": rain,
        "wind_kmh": wind,
        "severity": severity,
        "reasons": reasons,
    }


def fetch_weather(place: Optional[str]) -> dict[str, Any]:
    """
    Current conditions for a place, preferring OpenWeather when a key is set.

    Returns a dict that always has `status`. `ok` means real data; anything else
    means the caller must not claim the weather is good.

    Two sources on purpose. OpenWeather is the primary, configured by
    OPENWEATHER_API_KEY. Open-Meteo needs no key and covers the case where the
    key is absent, wrong, or the provider is down, so a key problem degrades to
    slightly different data rather than to "no weather" — and `source` records
    which one actually answered, because a traveller told to expect rain should
    know whether that came from a configured provider or a fallback.
    """
    coords = _coords_for(place)
    if not coords:
        return {"status": "UNKNOWN", "place": place, "reason": "no coordinates for this place"}

    primary = _fetch_openweather(coords, place)
    if primary is not None:
        return primary

    fallback = _fetch_open_meteo(place, coords)
    if fallback.get("status") == "ok":
        fallback["fallback_reason"] = (
            "no OpenWeather API key configured" if not _openweather_key()
            else "OpenWeather did not answer"
        )
    return fallback


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
# Scrape cadence and how long a headline is considered current.
#
# The gap between the two matters: a headline stays visible for 5 hours so a
# traveller planning an afternoon trip still sees a morning strike, while the
# 10-minute cadence means a new report appears within ten minutes of publication
# instead of waiting for someone to ask a question.
NEWS_SCRAPE_INTERVAL_SECONDS = 10 * 60
NEWS_TTL_SECONDS = 5 * 60 * 60

# Transit vocabulary, split by what it implies for the journey.
# Words that describe an *incident*. A headline must contain one of these to be
# captured at all.
#
# Previously this list also held bare context words — "disruption", "railway",
# "train", "bus" — so headlines like "software stocks scale as AI disruption
# worries" were collected and, once incident checking became unconditional,
# surfaced as a warning on a route. A generic noun is not evidence of a
# disruption, and over-warning is the failure this agent is most careful about.
INCIDENT_TERMS = {
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
}

# Words that make a headline *about transport at all*.
#
# A headline has to match an incident word AND one of these to be captured. Both
# halves are needed: "AI disruption worries" has an incident-adjacent word and no
# transport, and "SpaceX launches crew after delay" has "delay" and no transport.
# Neither is a disruption to anybody's journey, and with incident checking now
# unconditional, capturing them would warn about every route in the country.
# Matched on word boundaries, not substrings: "bus" occurs in "business" and
# "port" in "Spaceport", and both were putting ordinary business and space news
# into a transit incident cache.
TRANSPORT_TERMS = {
    "railway", "rail", "train", "trains", "slr", "platform", "commuter",
    "bus", "buses", "sltb", "coach", "expressway", "highway", "motorway",
    "transport", "transit", "flight", "airport", "petrol", "fuel",
    "fare", "ticket", "terminal", "public transport",
}


def mentions_any(text: str, terms: set[str] | Iterable[str]) -> bool:
    """True when any term appears as a whole word, not as a fragment of one."""
    for term in terms:
        if re.search(rf"\b{re.escape(term)}\b", text):
            return True
    return False

# Words that make a headline *about transport*, used by the classifier to judge
# severity and to decide whether the incident concerns the requested mode. They
# qualify nothing on their own — see INCIDENT_TERMS above.
CONTEXT_TERMS = {
    "disruption": "advisory",
    "expressway": "advisory",
    "railway": "advisory",
    "train": "advisory",
    "bus": "advisory",
    "sltb": "advisory",
    "slr": "advisory",
    "station": "advisory",
}

class HeadlineCache:
    """
    Time-limited store of transit headlines, refreshed on a timer.

    Three decisions worth stating:

    - **Age is measured from when we saw the headline, not when it was
      published.** Feed timestamps are missing or wrong often enough that
      trusting them would silently expire live reports. Being slightly generous
      is safer than dropping a real strike.
    - **Expired entries are evicted, not hidden.** The point of a TTL here is that
      a five-hour-old "services suspended" headline should stop being treated as
      current news, and a cache that keeps them forever fails at exactly that.
    - **It is a cache, so it is in memory.** Nothing here is a record of record:
      losing it on restart costs at most one scrape interval.
    """

    def __init__(self, ttl_seconds: int = NEWS_TTL_SECONDS) -> None:
        self.ttl_seconds = ttl_seconds
        self._entries: dict[str, dict[str, Any]] = {}
        self._lock = threading.RLock()
        self.last_scrape_at: float = 0.0
        self.last_scrape_ok: bool | None = None
        self.scrape_count: int = 0
        self.evicted_count: int = 0

    @staticmethod
    def _key(entry: dict[str, Any]) -> str:
        # Same headline from two feeds is one item, not two.
        return str(entry.get("headline", "")).strip().lower()

    def store(self, entries: list[dict[str, Any]], now: float | None = None) -> int:
        """Adds or refreshes entries and evicts anything past its TTL."""
        moment = now if now is not None else time.time()
        with self._lock:
            for entry in entries:
                key = self._key(entry)
                if not key:
                    continue
                existing = self._entries.get(key)
                self._entries[key] = {
                    **entry,
                    # A repeat sighting refreshes the clock, so a story still being
                    # reported stays current.
                    "first_seen_at": existing["first_seen_at"] if existing else moment,
                    "last_seen_at": moment,
                }
            self.last_scrape_at = moment
            self.scrape_count += 1
            return self.evict(now=moment)

    def evict(self, now: float | None = None) -> int:
        moment = now if now is not None else time.time()
        with self._lock:
            stale = [
                key
                for key, entry in self._entries.items()
                if moment - entry["last_seen_at"] > self.ttl_seconds
            ]
            for key in stale:
                del self._entries[key]
            self.evicted_count += len(stale)
            return len(stale)

    def entries(self, now: float | None = None) -> list[dict[str, Any]]:
        """Current entries, oldest first, after evicting anything expired."""
        self.evict(now=now)
        with self._lock:
            return sorted(self._entries.values(), key=lambda e: e["last_seen_at"])

    def stats(self, now: float | None = None) -> dict[str, Any]:
        moment = now if now is not None else time.time()
        with self._lock:
            live = [e for e in self._entries.values() if moment - e["last_seen_at"] <= self.ttl_seconds]
            # 0.0 means "never scraped", which is the most overdue state there
            # is — not a fresh cache at epoch zero.
            scraped_at = self.last_scrape_at or None
            age = moment - scraped_at if scraped_at else None
            return {
                "headlines": len(live),
                "ttl_seconds": self.ttl_seconds,
                "scrape_interval_seconds": NEWS_SCRAPE_INTERVAL_SECONDS,
                "scrape_count": self.scrape_count,
                "evicted_count": self.evicted_count,
                "last_scrape_at": (
                    time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(scraped_at))
                    if scraped_at
                    else None
                ),
                "age_seconds": round(age, 1) if age is not None else None,
                # Never scraped counts as overdue: it means nobody has looked.
                "overdue": age is None or age > NEWS_SCRAPE_INTERVAL_SECONDS * 2,
                "last_scrape_ok": self.last_scrape_ok,
            }


HEADLINES = HeadlineCache()

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


# Whether incident checking is armed at all.
#
# Off by default, and toggled by the demo control. Weather is always reported —
# it is unconditional and needs no consent — but incidents and disruptions are
# only looked for once the traveller has armed the check. An unrequested
# disruption warning on every journey is noise, and noise trains people to ignore
# warnings.
_INCIDENT_CHECK_ARMED = False


_WORKER_THREAD: Optional[threading.Thread] = None
_WORKER_STOP = threading.Event()
_WORKER_LOCK = threading.Lock()


def arm_incident_check(armed: bool) -> bool:
    """Turns incident checking on or off. Returns the previous state."""
    global _INCIDENT_CHECK_ARMED
    previous = _INCIDENT_CHECK_ARMED
    _INCIDENT_CHECK_ARMED = bool(armed)
    if not armed:
        # Un-arming also drops any simulated incident, so nothing survives the
        # switch being turned off.
        clear_simulated_incidents()
    return previous


def incident_check_armed() -> bool:
    return _INCIDENT_CHECK_ARMED


def scrape_headlines() -> list[dict[str, Any]]:
    """
    Fetches every feed and returns the transit-relevant headlines.

    Kept separate from the cache so the background worker and a
    request-triggered refresh do exactly the same thing.
    """
    collected: list[dict[str, Any]] = []
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
            if not mentions_any(lowered, INCIDENT_TERMS):
                continue
            if not mentions_any(lowered, TRANSPORT_TERMS):
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
    return collected


def refresh_headlines() -> int:
    """One scrape into the cache. Returns how many entries are now live."""
    try:
        entries = scrape_headlines()
    except Exception:
        # A total failure must not clear a cache that still holds headlines
        # inside their five-hour window.
        HEADLINES.last_scrape_ok = False
        return len(HEADLINES.entries())

    HEADLINES.last_scrape_ok = True
    HEADLINES.store(entries)
    return len(HEADLINES.entries())


def fetch_news_headlines(force_refresh: bool = False) -> list[dict[str, Any]]:
    """
    Transit-relevant headlines from public RSS, plus any simulated incident.

    Served from the cache, which a background worker keeps warm every ten
    minutes. A request that finds the cache overdue refreshes it inline rather
    than reporting no news: the traveller asked a question now, and the
    alternative is telling them a route is clear because nobody has looked
    recently.

    Real headlines are always checked. Only the *simulated* incident is behind
    the demo toggle — a fabricated accident must be requested, but a real
    reported strike must never need permission to be mentioned.
    """
    if force_refresh or time.time() - HEADLINES.last_scrape_at > NEWS_SCRAPE_INTERVAL_SECONDS * 2:
        refresh_headlines()

    return HEADLINES.entries() + list(_ACTIVE_SIMULATIONS.values())


_WORKER_THREAD: Optional[threading.Thread] = None
_WORKER_STOP = threading.Event()
_WORKER_LOCK = threading.Lock()


def start_headline_worker(interval_seconds: int = NEWS_SCRAPE_INTERVAL_SECONDS) -> Optional[threading.Thread]:
    """
    Starts the background scraper. Safe to call more than once.

    A daemon thread rather than an event-loop task: scraping is blocking I/O to
    third-party feeds, and holding a worker for several seconds every ten minutes
    would stall request handling for no benefit.
    """
    global _WORKER_THREAD

    with _WORKER_LOCK:
        if _WORKER_THREAD is not None and _WORKER_THREAD.is_alive():
            return _WORKER_THREAD

        _WORKER_STOP.clear()
        refresh_headlines()  # populate immediately, so the first question is not blind

        def loop() -> None:
            while not _WORKER_STOP.wait(interval_seconds):
                try:
                    refresh_headlines()
                except Exception:
                    # The worker is a convenience. If it fails, the cache ages
                    # out and requests refresh it inline.
                    HEADLINES.last_scrape_ok = False

        thread = threading.Thread(target=loop, name="headline-scraper", daemon=True)
        thread.start()
        _WORKER_THREAD = thread
        return thread


def stop_headline_worker() -> None:
    """Stops the worker and forgets the thread. Used by tests."""
    global _WORKER_THREAD

    _WORKER_STOP.set()
    with _WORKER_LOCK:
        if _WORKER_THREAD is not None:
            _WORKER_THREAD = None


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
