"""
Route Code & Corridor Ingestion (routemaster.lk)
Member 2 — Planning Agent & Information Retrieval

routemaster.lk publishes every bus route it documents as schema.org microdata on
its homepage:

    <span itemprop=busNumber>EX 1-1</span>
    <span itemprop=departureBusStop>Makumbara Bus Stand</span>
    <span itemprop=arrivalBusStop>Matara</span>

That gives us 111 REAL route codes with their real endpoints and directions —
the island-wide route structure. What it does NOT publish is any timetable:
the site is a route-map project and its pages contain no departure or arrival
times. So route codes, endpoints and directions are taken from the source, while
departure and arrival times are generated for demonstration and flagged as such
per row.

Usage:
    python -m src.planner.ingest_routemaster --check
    python -m src.planner.ingest_routemaster --dry-run
    python -m src.planner.ingest_routemaster
"""

from __future__ import annotations

import argparse
import html as html_lib
import re

import httpx

from src.planner.fares import great_circle_km
from src.planner.geo import resolve
from src.planner.schedule_ingest import (
    BUS_FILE,
    ServiceRecord,
    load_fixture,
    merge_records,
    write_fixture,
)

SOURCE_URL = "https://routemaster.lk/"
SOURCE_NAME = "routemaster.lk route codes (schema.org BusTrip microdata)"
TIMEOUT = 30.0
USER_AGENT = "LankaJourneyAI/1.0 (academic transit project)"

# routemaster uses colloquial stand names; map them onto the canonical names the
# fixtures and the NLP parser already agree on.
STATION_ALIASES: dict[str, str] = {
    "makumbara bus stand": "Makumbura MMC",
    "makumbara": "Makumbura MMC",
    "colombo": "Colombo Bastian Mawatha",
    "colombo fort": "Colombo Fort",
    "kandy": "Kandy",
    "galle": "Galle",
    "matara": "Matara",
    "jaffna": "Jaffna",
    "anuradhapura": "Anuradhapura",
    "matale": "Matale",
    "mannar": "Mannar",
    "trincomalee": "Trincomalee",
    "batticaloa": "Batticaloa",
    "ampara": "Ampara",
    "kurunegala": "Kurunegala",
    "badulla": "Badulla",
    "ratnapura": "Ratnapura",
    "nuwara eliya": "Nuwara Eliya",
    "hambantota": "Hambantota",
    "kegalle": "Kegalle",
    "vavuniya": "Vavuniya",
    "monaragala": "Monaragala",
    "puttalam": "Puttalam",
    "chilaw": "Chilaw",
    "negombo": "Negombo",
}

# Route-code prefixes indicate the class of service, which sets a plausible
# journey time for a demonstration timetable. These are ESTIMATES, not published
# durations, and are recorded as derived per row.
_PREFIX_MINUTES: tuple[tuple[str, int], ...] = (
    ("EX", 300),  # expressway
    ("NC", 270),  # normal (north-central style)
    ("NW", 300),
    ("NE", 330),
    ("SE", 270),
    ("SW", 270),
    ("RT", 240),  # rural / local
    ("TR", 240),
)

# Fares are unknown per route from this source, so nothing is invented here; the
# importer allows a missing fare rather than showing a wrong price.

# Departure patterns for the demonstration.
#
# These were three fixed times — 06:30, 13:15 and 19:45 — applied to every route
# in both directions, which put 510 services in the timetable with the same three
# departures and three arrivals. A timetable like that reads as broken data
# rather than as a demo.
#
# Real inter-provincial services run more often in the morning and evening
# peaks and less often in the middle of the day, so the pattern below is
# built from headways rather than a flat grid: 40 minutes before 09:30, 90 in
# the middle of the day, 30 minutes from 16:00. Service offsets come from the
# route code so two routes on the same corridor do not depart in lockstep, which
# is what makes the spread look like a network instead of a copy.
# Headways by journey length, in minutes: (peak morning, off-peak, peak evening).
#
# A flat headway was the other half of the "everything departs at 06:30" problem,
# but a uniform *frequency* is no more realistic than a uniform *time*. A
# 40 km hop does not run every 30 minutes, and a full-day intercity run does.
# These are scaled off the route's journey duration, which is already estimated
# per service class.
HEADWAYS_BY_DURATION: tuple[tuple[int, tuple[int, int, int]], ...] = (
    (120, (120, 150, 120)),    # short local hop: a few services a day
    (240, (60, 90, 60)),        # regional
    (360, (45, 75, 40)),        # long-distance
    (10**9, (30, 60, 30)),      # full-day intercity
)

_FIRST_DEPARTURE = "05:45"
_PEAK_MORNING_UNTIL = 9 * 60 + 30
_PEAK_EVENING_FROM = 16 * 60
_LAST_DEPARTURE = "20:15"

# A ceiling on services per direction per day. Without it, doubling the headways
# pattern quadrupled the corpus, which is a lot of retrieval pool for a demo and
# a timetable nobody scrolls.
MAX_SERVICES_PER_DAY = 10


def _route_offset(route_code: str) -> int:
    """A stable per-route offset in minutes, so services are not synchronised."""
    cleaned = re.sub(r"[^A-Za-z0-9]+", "", route_code).upper()
    if not cleaned:
        return 0
    # Spread of 30 minutes, matching the tightest peak headway used below.
    return sum(ord(char) for char in cleaned) % 30


def _headways(route_code: str, duration_minutes: int) -> tuple[int, int, int]:
    """Peak-morning, off-peak and peak-evening headways for this route."""
    duration = duration_minutes
    for limit, headways in HEADWAYS_BY_DURATION:
        if duration <= limit:
            return headways
    return HEADWAYS_BY_DURATION[-1][1]  # pragma: no cover - unreachable


def _departure_pattern(route_code: str, duration_minutes: int) -> list[str]:
    """
    A day's departures for one route, following a peak/off-peak pattern.

    Deterministic for a given route code, so re-running the importer produces the
    same timetable: a demo that reshuffles every time it is rebuilt cannot be
    demonstrated against.
    """
    peak_morning, offpeak, peak_evening = _headways(route_code, duration_minutes)
    offset = _route_offset(route_code)
    current = _to_minutes(_FIRST_DEPARTURE) + offset
    last = _to_minutes(_LAST_DEPARTURE)

    times: list[str] = []
    while current <= last:
        if current <= _PEAK_MORNING_UNTIL:
            step = peak_morning
        elif current >= _PEAK_EVENING_FROM:
            step = peak_evening
        else:
            step = offpeak
        times.append(_format(current))
        current += step
    return times[:MAX_SERVICES_PER_DAY]

_TRIPLE = re.compile(
    r"itemprop=busNumber>(?P<code>[^<]+)<.*?"
    r"itemprop=departureBusStop>(?P<origin>[^<]+)<.*?"
    r"itemprop=arrivalBusStop>(?P<destination>[^<]+)<",
    re.S,
)


def _canonical(raw: str) -> str:
    """Normalises a routemaster stop name onto a canonical station name."""
    # The page carries entities such as &#8211; (en dash) inside stop names.
    cleaned = html_lib.unescape(re.sub(r"\s+", " ", raw)).strip(" -–—\u2013\u2014")
    # Drop facility noise: "Maradana Police Bus Stop" -> "Maradana".
    cleaned = re.sub(
        r"\s+(police\s+)?(bus\s+(stop|stand)|bus\s+stop|stand)$", "", cleaned, flags=re.I
    ).strip()

    key = cleaned.lower()
    # Colombo's central bus stands are named inconsistently across routes.
    if "fort" in key and "colombo" in key:
        return "Colombo Fort"
    if "central bus stop" in key:
        return "Colombo Bastian Mawatha"
    if key in STATION_ALIASES:
        return STATION_ALIASES[key]
    head = key.split(" ")[0]
    return STATION_ALIASES.get(head, cleaned)


# Effective speed including stops, traffic and climb. Sri Lankan inter-provincial
# highways average well below the 60-70 km/h a free-road figure would suggest.
AVERAGE_SPEED_KMH = 48.0
# Time spent at stops, added once per service rather than per stop: a bus that
# calls at eight places does not lose an hour to them.
DWELL_MINUTES = 15
MIN_DURATION_MINUTES = 45
MAX_DURATION_MINUTES = 480


def _minutes_for(origin: str = "", destination: str = "", route_code: str = "") -> int:
    """
    Estimated journey time, preferring distance over the service-class table.

    The table maps a route-code prefix to a duration, so every route in a class
    got the same journey time regardless of how far it actually goes — which is
    why the arrivals all landed on the same few clock times. Distance is a better
    estimator and is available for these routes; the table stays as the fallback
    for endpoints we cannot place.
    """
    start = resolve(origin)
    end = resolve(destination)
    if start and end:
        distance = great_circle_km((start[1], start[2]), (end[1], end[2])) * 1.25
        if distance >= 1.0:
            minutes = int(round(distance / AVERAGE_SPEED_KMH * 60 + DWELL_MINUTES))
            return max(MIN_DURATION_MINUTES, min(MAX_DURATION_MINUTES, minutes))

    return _minutes_for_class(route_code)


def _minutes_for_class(route_code: str) -> int:
    """Estimated journey time for a route code, by service class."""
    upper = route_code.upper()
    for prefix, minutes in _PREFIX_MINUTES:
        if upper.startswith(prefix):
            return minutes
    return 270


def _to_minutes(hhmm: str) -> int:
    hours, minutes = hhmm.split(":")
    return int(hours) * 60 + int(minutes)


def _format(total: int) -> str:
    wrapped = total % (24 * 60)
    return f"{wrapped // 60:02d}:{wrapped % 60:02d}"


def fetch_route_codes(url: str = SOURCE_URL) -> list[dict[str, str]]:
    """
    Fetches the real route codes and endpoints.

    Returns:
        One dict per route with route_code, origin and destination.

    Raises:
        RuntimeError: if the page cannot be read or the microdata has vanished,
            so a layout change can never be mistaken for "no routes exist".
    """
    try:
        with httpx.Client(
            timeout=TIMEOUT, headers={"User-Agent": USER_AGENT}, follow_redirects=True
        ) as client:
            response = client.get(url)
            response.raise_for_status()
            html = response.text
    except Exception as exc:
        raise RuntimeError(f"routemaster.lk unreachable: {exc}") from exc

    routes = [
        {
            "route_code": _canonical_code(m.group("code")),
            "origin": _canonical(m.group("origin")),
            "destination": _canonical(m.group("destination")),
        }
        for m in _TRIPLE.finditer(html)
    ]

    # De-duplicate on the code+direction pair.
    seen: set[tuple[str, str, str]] = set()
    unique: list[dict[str, str]] = []
    for route in routes:
        key = (route["route_code"], route["origin"], route["destination"])
        if key not in seen:
            seen.add(key)
            unique.append(route)

    if not unique:
        raise RuntimeError(
            "No busNumber/departureBusStop/arrivalBusStop microdata found. The page "
            "structure has probably changed; refusing to report zero routes."
        )
    return unique


def _canonical_code(raw: str) -> str:
    return re.sub(r"\s+", " ", raw).strip().upper()


def build_records(routes: list[dict[str, str]]) -> list[ServiceRecord]:
    """
    Turns real route codes into demo services.

    Route code, endpoints and direction come from the source. Departure and
    arrival times are generated for the demonstration, and the estimated journey
    duration is recorded so nothing is quietly presented as published.
    """
    records: list[ServiceRecord] = []

    for route in routes:
        code = route["route_code"]
        origin, destination = route["origin"], route["destination"]

        for from_stop, to_stop in ((origin, destination), (destination, origin)):
            base_duration = _minutes_for(from_stop, to_stop, code)
            departures = _departure_pattern(code, base_duration)

            for index, departure in enumerate(departures):
                # Peak journeys run slower, and the delay compounds through the
                # day. Without this, every service on a route arrives at exactly
                # the same minute regardless of when it left.
                variation = 0
                if _to_minutes(departure) <= _PEAK_MORNING_UNTIL:
                    variation = 5 * (index % 3)
                elif _to_minutes(departure) >= _PEAK_EVENING_FROM:
                    variation = 10 * (index % 3)
                duration = base_duration + variation
                arrival_minutes = _to_minutes(departure) + duration
                # The id must include BOTH endpoints: several routes share an
                # origin and a departure time, and omitting one loses rows.
                code_slug = re.sub(r"[^A-Z0-9]+", "", code)[:8]
                from_slug = re.sub(r"[^A-Z0-9]+", "", from_stop.upper())[:6]
                to_slug = re.sub(r"[^A-Z0-9]+", "", to_stop.upper())[:6]
                records.append(
                    ServiceRecord(
                        route_id=f"RM-{code_slug}-{from_slug}-{to_slug}-{departure.replace(':', '')}",
                        route_number=code,
                        service_name=f"Route {code} ({from_stop} – {to_stop})",
                        provider="SLTB",
                        origin=from_stop,
                        destination=to_stop,
                        departure_time=departure,
                        arrival_time=_format(arrival_minutes),
                        arrival_next_day=arrival_minutes >= 24 * 60,
                        base_fare_lkr=None,  # unknown from this source
                        fare_unknown=True,
                        stops=[from_stop, to_stop],
                        classes=["Ordinary"],
                        transit_type="EXPRESS_BUS",
                        synthetic=True,
                        synthetic_fields=["departure_time", "arrival_time", "duration_estimate"],
                        source=SOURCE_NAME,
                        source_url=SOURCE_URL,
                        confidence="real-route-code-generated-times",
                    )
                )
    return records


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    parser.add_argument("--check", action="store_true", help="fetch and report only")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--replace-generated",
        action="store_true",
        help=(
            "discard previously generated rows before rebuilding. Needed whenever "
            "the departure pattern changes: route ids embed the departure time, so "
            "new times mean new ids and the old rows are never superseded by the "
            "merge — they just accumulate, and the timetable keeps both."
        ),
    )
    args = parser.parse_args(argv)

    routes = fetch_route_codes()
    print(f"routemaster.lk: {len(routes)} real route codes with endpoints")
    for route in routes[:8]:
        print(f"  {route['route_code']:<10} {route['origin']} -> {route['destination']}")
    if len(routes) > 8:
        print(f"  … and {len(routes) - 8} more")

    records = build_records(routes)
    existing = load_fixture(BUS_FILE)

    if args.replace_generated:
        # Only rows this importer generated are dropped. Anything hand-curated
        # from a published timetable carries no `synthetic` flag and is never
        # touched, so regenerating cannot quietly delete real data.
        curated = [row for row in existing if not row.get("synthetic")]
        dropped = len(existing) - len(curated)
        print(f"  replacing {dropped} previously generated row(s); keeping {len(curated)} curated")
        existing = curated

    merged, notes = merge_records(existing, records, force=False)
    print(f"\nbuses: {len(existing)} -> {len(merged)} rows ({len(records)} generated)")

    if args.check or args.dry_run:
        print("  (nothing written)")
        return 0

    write_fixture(BUS_FILE, merged)
    print(f"  wrote {BUS_FILE}")
    for note in notes[:10]:
        print(f"  {note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
