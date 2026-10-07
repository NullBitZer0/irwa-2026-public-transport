"""
Missing Corridor Generator

Completes `data/processed/bus_routes.json` for major-city pairs that the fare
chart prices but the corpus has no service for.

The distinction this file is built on:

  * A pair in `ntc_bus_fares.json` is a corridor NTC actually operates and has
    published a fare for. Having no departure rows for it is a gap in our data,
    so filling it in is completing a record, not inventing a service.

  * A pair with no published fare is not a corridor we have any published
    evidence for. Nothing here creates one. See `unpriced_pairs` for what is
    left unreachable, which is reported rather than papered over.

Generated rows are flagged like every other generated row:

    "synthetic": true,
    "synthetic_fields": ["departure_time", "arrival_time", "duration_estimate"]

Both times are derived from the corridor's road distance, so they follow the same
rule as `generate_demo_data`: an arrival is always a departure plus a computed
duration, never a free value. The fare is NOT synthetic — it is the published
figure from the fare chart.

Usage:
    python -m src.planner.generate_missing_corridors --dry-run
    python -m src.planner.generate_missing_corridors
"""

from __future__ import annotations

import argparse
import itertools
import json
import re
from math import asin, cos, radians, sin, sqrt
from typing import Any

from src.planner.schedule_ingest import (
    BUS_FILE,
    ServiceRecord,
    load_fixture,
    merge_records,
    write_fixture,
)

FARES_FILE = "data/processed/ntc_bus_fares.json"

# Where the demo's major-city departures run, per corridor. Not published
# times: a plausible spread so the timetable shows several options rather than
# one. Every row is flagged as derived.
FIRST_DEPARTURE = "06:30"
LAST_DEPARTURE = "19:30"
HEADWAY_MINUTES = 120

# Road distance is longer than straight-line. The factor is fitted to the
# published corridors in the corpus; it is a model, not a measurement, which is
# why `duration_estimate` is listed as a synthetic field.
ROAD_FACTOR = 1.28

# Effective speed including stops and traffic, km/h. Inter-provincial express
# buses average well below their maximum for most of the run.
SPEED_KMH = 46.0

# Shortest plausible inter-city run. Below this, a "service" would be a town bus.
MIN_DURATION_MINUTES = 45

# Colombo's two terminals both key to "colombo" in the fare chart.
COLOMBO_TERMINALS = ("Colombo Bastian Mawatha", "Colombo Fort")

# The station names the corpus already uses for each major city. Reused rather
# than invented so a generated row matches the same places the real ones do.
CITY_STATION = {
    "ampara": "Ampara",
    "anuradhapura": "Anuradhapura",
    "badulla": "Badulla",
    "batticaloa": "Batticaloa",
    "colombo": "Colombo Bastian Mawatha",
    "dambulla": "Dambulla",
    "galle": "Galle",
    "hambantota": "Hambantota",
    "jaffna": "Jaffna",
    "kandy": "Kandy",
    "kegalle": "Kegalle",
    # Both spellings map to the one the corpus already uses. They are separate
    # keys in `MAJOR_CITIES`, and writing "Kunegala" onto rows meant every
    # generated service was filed under a city name nothing else could match —
    # so 440 rows existed and none were findable.
    "kunegala": "Kurunegala",
    "kurunegala": "Kurunegala",
    "mannar": "Mannar",
    "matale": "Matale",
    "matara": "Matara",
    "monaragala": "Monaragala",
    "negombo": "Negombo",
    "nuwaraeliya": "Nuwara Eliya",
    "ratnapura": "Ratnapura",
    "takunagaya": "Takunagaya",
    "trincomalee": "Trincomalee",
}

# Filled in by `_load_coords`, from the one coordinate table in the project.
CITY_COORDS: dict[str, tuple[float, float]] = {}


def _key(name: str) -> str:
    """The fare chart's key form: lowercase, spaces removed."""
    return name.lower().replace(" ", "")


def _load_coords() -> None:
    """Index the project's one coordinate table by the fare chart's key form.

    Terminals inside Colombo ("Colombo Bastian Mawatha", "Colombo Fort") are
    keyed to the city, not the stop: a generated corridor is priced per city, so
    it must be measured per city too.
    """
    from src.planner.geo import CITY_COORDS as ALL
    from src.planner.geo import STOP_CITY

    for name, latlng in ALL.items():
        CITY_COORDS.setdefault(_key(name), latlng)
    CITY_COORDS["colombo"] = ALL["Colombo"]
    CITY_COORDS["nuwaraeliya"] = ALL["Nuwara Eliya"]
    for stop, city in STOP_CITY.items():
        if city in ALL:
            CITY_COORDS.setdefault(_key(stop), ALL[city])


def _coords_for(station: str) -> tuple[float, float]:
    """Coordinates of the city a station sits in.

    The corpus names terminals ("Colombo Bastian Mawatha") while the fare chart
    and coordinate table name cities ("Colombo"), so resolve to the city node.
    """
    from src.planner.geo import CITY_COORDS as ALL
    from src.planner.geo import STOP_CITY

    node = STOP_CITY.get(station)
    if node and node in ALL:
        return ALL[node]
    return CITY_COORDS[_key(station)]


def _haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1 = a
    lat2, lon2 = b
    h = sin(radians(lat2 - lat1) / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(
        radians(lon2 - lon1) / 2
    ) ** 2
    return 6371.0 * 2 * asin(sqrt(h))


def _to_minutes(hhmm: str) -> int:
    hours, minutes = hhmm.split(":")
    return int(hours) * 60 + int(minutes)


def _format(total: int) -> str:
    return f"{total // 60 % 24:02d}:{total % 60:02d}"


def _departures() -> list[tuple[str, bool]]:
    """Times to offer, and whether each is published.

    None are published for these corridors — the fare chart gives a fare but no
    timetable — so every one is flagged derived.
    """
    times: list[tuple[str, bool]] = []
    current = _to_minutes(FIRST_DEPARTURE)
    last = _to_minutes(LAST_DEPARTURE)
    while current <= last:
        times.append((_format(current), False))
        current += HEADWAY_MINUTES
    return times


def priced_major_pairs(existing: list[dict[str, Any]]) -> list[tuple[str, str, dict]]:
    """
    Major-city pairs that need a service and already have a published fare.

    Returns (origin_city, destination_city, fare_entry).
    """
    from src.planner.journey_search import MAJOR_CITIES, find_services_at

    with open(FARES_FILE) as handle:
        fares = json.load(handle)

    have_service: set[frozenset[str]] = set()
    for row in existing:
        have_service.add(frozenset({_key(row["origin"]), _key(row["destination"])}))

    needed: list[tuple[str, str, dict]] = []
    for origin, destination in itertools.combinations(sorted(MAJOR_CITIES), 2):
        origin_key, dest_key = _key(origin), _key(destination)
        if frozenset({origin_key, dest_key}) in have_service:
            continue
        # A pass-through service still covers the pair, so it needs no row.
        if find_services_at(existing, origin, destination, limit=1):
            continue
        fare = fares.get(f"{origin_key}|{dest_key}") or fares.get(f"{dest_key}|{origin_key}")
        if fare and fare.get("from_fare"):
            needed.append((origin, destination, fare))
    return needed


def unpriced_pairs(existing: list[dict[str, Any]]) -> list[tuple[str, str]]:
    """
    Major-city pairs with neither a service nor a published fare.

    Reported, not generated. We have no published evidence these corridors run,
    so creating them would be inventing services rather than completing records.
    """
    from src.planner.connection_planner import find_connections
    from src.planner.journey_search import MAJOR_CITIES, find_services_at

    with open(FARES_FILE) as handle:
        fares = json.load(handle)

    unreachable: list[tuple[str, str]] = []
    for origin, destination in itertools.combinations(sorted(MAJOR_CITIES), 2):
        if find_services_at(existing, origin, destination, limit=1):
            continue
        if find_connections(existing, origin, destination, max_results=1):
            continue
        key_a, key_b = _key(origin), _key(destination)
        if fares.get(f"{key_a}|{key_b}") or fares.get(f"{key_b}|{key_a}"):
            continue
        unreachable.append((origin, destination))
    return unreachable


def generate_corridor(
    origin_city: str, destination_city: str, fare_entry: dict
) -> list[ServiceRecord]:
    """One corridor, both directions, every generated departure."""
    origin = CITY_STATION.get(origin_city, origin_city.title())
    destination = CITY_STATION.get(destination_city, destination_city.title())

    route_number = str(fare_entry.get("route_no") or "0").split("/")[0]
    fare = float(fare_entry["from_fare"])

    records: list[ServiceRecord] = []
    for start, end in ((origin, destination), (destination, origin)):
        # Route ids must differ by direction: the two runs share a departure
        # time, so a slug built once from the origin would collide with its
        # own opposite and the merge would drop one of them.
        # Five characters per endpoint: four is not always enough, since Matale
        # and Matara both truncate to MATA and collided with their own opposite.
        slug = "-".join(
            re.sub(r"[^A-Za-z0-9]+", "", part)[:5].upper() for part in (start, end)
        )
        distance = _haversine_km(_coords_for(start), _coords_for(end))
        road_km = distance * ROAD_FACTOR
        duration = max(MIN_DURATION_MINUTES, int(road_km / SPEED_KMH * 60))

        for departure, _published in _departures():
            arrival_minutes = _to_minutes(departure) + duration
            overnight = arrival_minutes >= 24 * 60

            records.append(
                ServiceRecord(
                    route_id=f"GEN-{slug}-{departure.replace(':', '')}",
                    route_number=route_number,
                    service_name=f"{start} - {end} (inter-provincial)",
                    provider="SLTB",
                    origin=start,
                    destination=end,
                    departure_time=departure,
                    arrival_time=_format(arrival_minutes),
                    arrival_next_day=overnight,
                    # Published, not generated: this is the fare chart's figure.
                    base_fare_lkr=fare,
                    stops=[start, end],
                    classes=["Ordinary"],
                    transit_type="EXPRESS_BUS",
                    synthetic=True,
                    synthetic_fields=[
                        "departure_time",
                        "arrival_time",
                        "duration_estimate",
                    ],
                    source=str(fare_entry.get("source") or "NTC fare chart"),
                    source_url=(
                        "https://www.ntc.gov.lk/Bus_info/bus_fares.php"
                    ),
                    confidence="derived-from-published-fare",
                )
            )
    return records


def generate_all() -> list[ServiceRecord]:
    _load_coords()
    existing = load_fixture(BUS_FILE)
    records: list[ServiceRecord] = []
    for origin, destination, fare_entry in priced_major_pairs(existing):
        records.extend(generate_corridor(origin, destination, fare_entry))
    return records


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    existing = load_fixture(BUS_FILE)
    generated = generate_all()

    merged, notes = merge_records(existing, generated, force=False)
    print(
        f"buses: {len(existing)} -> {len(merged)} rows "
        f"({len(merged) - len(existing)} generated)"
    )
    for note in notes:
        print(f"  {note}")

    corridors = len({r.route_id.rsplit("-", 1)[0] for r in generated})
    print(f"  {corridors} corridors completed from the published fare chart")
    flagged = sum(1 for r in merged if r.get("synthetic"))
    print(f"  {flagged} of {len(merged)} rows are flagged synthetic")

    still_unreachable = unpriced_pairs(merged)
    if still_unreachable:
        print(
            f"\n  {len(still_unreachable)} major-city pairs remain unreachable and "
            f"are NOT generated: we have no published fare or timetable for them, "
            f"so there is nothing to complete. Agents will tell travellers to "
            f"check the operator directly."
        )
        for origin, destination in still_unreachable[:10]:
            print(f"    e.g. {origin} -> {destination}")
        if len(still_unreachable) > 10:
            print(f"    ... and {len(still_unreachable) - 10} more")

    if args.dry_run:
        print("  (dry run — nothing written)")
    else:
        write_fixture(BUS_FILE, merged)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
