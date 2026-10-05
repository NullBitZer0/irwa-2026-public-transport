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
_DEPARTURES = ("06:30", "13:15", "19:45")

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


def _minutes_for(route_code: str) -> int:
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
        duration = _minutes_for(code)
        origin, destination = route["origin"], route["destination"]

        for from_stop, to_stop in ((origin, destination), (destination, origin)):
            for departure in _DEPARTURES:
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
    args = parser.parse_args(argv)

    routes = fetch_route_codes()
    print(f"routemaster.lk: {len(routes)} real route codes with endpoints")
    for route in routes[:8]:
        print(f"  {route['route_code']:<10} {route['origin']} -> {route['destination']}")
    if len(routes) > 8:
        print(f"  … and {len(routes) - 8} more")

    records = build_records(routes)
    existing = load_fixture(BUS_FILE)
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
