"""
Unpublished Corridor Generator

Fills in services for major-city pairs that have **no published NTC fare** — the
corridors `generate_missing_corridors` deliberately leaves alone.

The distinction the two generators draw:

  * `generate_missing_corridors` completes corridors NTC has priced. The fare is
    real; only the times are derived.
  * This file has no published source for either the corridor or its fare. Every
    field it writes is modelled, so every row is flagged `synthetic: true` with
    `synthetic_fields` naming what was derived, and the fare is left
    `fare_unknown` so the existing estimator in `fares.py` supplies a display
    price and the money path refuses to sell it.

Modelled does not mean arbitrary. Departure times follow how intercity coaches
actually run — a peak-hour bunching, a long midday gap, a tapering evening —
and each service is seeded from its own route id, so the same corridor always
produces the same timetable and re-running this file changes nothing.

Usage:
    python -m src.planner.generate_unpublished_corridors --dry-run
    python -m src.planner.generate_unpublished_corridors
"""

from __future__ import annotations

import argparse
import itertools
import json
import random
import re
from typing import Any

from src.planner.generate_missing_corridors import (
    CITY_STATION,
    MIN_DURATION_MINUTES,
    ROAD_FACTOR,
    SPEED_KMH,
    _coords_for,
    _format,
    _haversine_km,
    _key,
    _load_coords,
    _to_minutes,
)
from src.planner.schedule_ingest import (
    BUS_FILE,
    ServiceRecord,
    load_fixture,
    merge_records,
    write_fixture,
)

# ── How intercity coaches actually run ───────────────────────────────────────
#
# Modelled from the departure patterns in the published SLTB/NTC timetables
# already in the corpus, which cluster rather than run on a fixed headway:
# buses bunch in the morning peak, thin out through the afternoon, and taper off
# in the evening. A flat 2-hourly headway — which is what the priced-corridor
# generator uses, because those have a fare but no timetable to fit — reads as
# synthetic immediately.
#
# Minutes from midnight.
PEAK_MORNING_START = _to_minutes("05:30")
PEAK_MORNING_END = _to_minutes("09:30")
MIDDAY_START = _to_minutes("09:30")
MIDDAY_END = _to_minutes("16:00")
EVENING_START = _to_minutes("16:00")
EVENING_END = _to_minutes("20:30")

# How many services to place in each window, before the distance-based scaling.
PEAK_MORNING_COUNT = 3
MIDDAY_COUNT = 2
EVENING_COUNT = 3

# Journeys under this get the base pattern; longer ones run more coaches, which
# is what a busy trunk corridor looks like next to a quiet branch.
SHORT_CORRIDOR_KM = 80.0
LONG_CORRIDOR_KM = 220.0

# A long run leaves in the evening and arrives after midnight, which is why the
# corpus has overnight services at all.
OVERNIGHT_AFTER_KM = 250.0

# Journey duration varies run to run — traffic, weather, a slow driver. A timetable
# where every service on a corridor takes exactly the same time is not a
# timetable.
DURATION_JITTER = (-0.06, 0.10)


def _seed_for(route_id: str) -> random.Random:
    """
    A generator seeded from the route id.

    Deterministic on purpose: the corpus is a committed fixture, so re-running
    this file must not reshuffle every departure time in it.
    """
    digest = sum(ord(ch) * (index + 1) for index, ch in enumerate(route_id))
    return random.Random(digest)


def _departures_for(rng: random.Random, road_km: float) -> list[str]:
    """
    Departure times for one corridor, in order.

    Weighted towards the peaks, scaled up for a longer corridor, and jittered
    within each window so no two runs leave at the same minute.
    """
    if road_km <= SHORT_CORRIDOR_KM:
        scale = 1.0
    elif road_km >= LONG_CORRIDOR_KM:
        scale = 1.8
    else:
        span = LONG_CORRIDOR_KM - SHORT_CORRIDOR_KM
        scale = 1.0 + 0.8 * (road_km - SHORT_CORRIDOR_KM) / span

    times: list[int] = []
    for start, end, count in (
        (PEAK_MORNING_START, PEAK_MORNING_END, PEAK_MORNING_COUNT),
        (MIDDAY_START, MIDDAY_END, MIDDAY_COUNT),
        (EVENING_START, EVENING_END, EVENING_COUNT),
    ):
        slots = max(1, round(count * scale))
        window = end - start
        for slot in range(slots):
            # Even spread across the window, then nudged, so the times are
            # plausibly spaced rather than bunched at the window's start.
            offset = window * (slot + 0.5) / slots
            times.append(start + round(offset) + rng.randint(-8, 8))

    return sorted({_format(minutes % (24 * 60)) for minutes in times})


def _duration_for(rng: random.Random, road_km: float) -> int:
    """Journey time for one run, in minutes."""
    nominal = road_km / SPEED_KMH * 60
    jitter = rng.uniform(*DURATION_JITTER)
    return max(MIN_DURATION_MINUTES, round(nominal * (1 + jitter)))


def _classes_for(rng: random.Random, road_km: float) -> list[str]:
    """
    A coach class mix, so a corridor is not all-ordinary or all-semi.

    Longer runs carry proportionally more semi-luxury coaches, which is what the
    published fare chart shows: premium fares are concentrated on trunk routes.
    """
    premium_share = 0.25 if road_km < SHORT_CORRIDOR_KM else 0.45
    return ["Semi-Luxury"] if rng.random() < premium_share else ["Ordinary"]


def unpublished_pairs(existing: list[dict[str, Any]]) -> list[tuple[str, str]]:
    """
    Major-city pairs with neither a service nor a published fare.

    These are the ones a traveller can be told about in no other way: the
    corridor is not in the fare chart, and nothing reaches either end.
    """
    from src.planner.connection_planner import find_connections
    from src.planner.journey_search import MAJOR_CITIES, find_services_at

    with open("data/processed/ntc_bus_fares.json") as handle:
        fares = json.load(handle)

    pairs: list[tuple[str, str]] = []
    for origin, destination in itertools.combinations(sorted(MAJOR_CITIES), 2):
        if find_services_at(existing, origin, destination, limit=1):
            continue
        if find_connections(existing, origin, destination, max_results=1):
            continue
        key_a, key_b = _key(origin), _key(destination)
        if fares.get(f"{key_a}|{key_b}") or fares.get(f"{key_b}|{key_a}"):
            # Priced by NTC, so `generate_missing_corridors` owns this one.
            continue
        pairs.append((origin, destination))
    return pairs


def generate_corridor(origin_city: str, destination_city: str) -> list[ServiceRecord]:
    """One modelled corridor, both directions, its departures and durations."""
    origin = CITY_STATION.get(origin_city, origin_city.title())
    destination = CITY_STATION.get(destination_city, destination_city.title())

    # Five characters per endpoint: four is not enough, since Matale and Matara
    # both truncate to MATA.
    slug = "-".join(
        re.sub(r"[^A-Za-z0-9]+", "", part)[:5].upper() for part in (origin, destination)
    )
    distance = _haversine_km(_coords_for(origin), _coords_for(destination))
    road_km = distance * ROAD_FACTOR

    # A city whose name resolves to two spellings can produce a corridor from
    # itself to itself. A service that departs and arrives in the same place is
    # not a service, and the fare estimator refuses to price it, so it is not
    # written at all.
    if origin == destination:
        return []

    records: list[ServiceRecord] = []
    for start, end in ((origin, destination), (destination, origin)):
        # Seeded per direction, so the outbound and return timetables differ the
        # way real ones do.
        rng = _seed_for(f"{slug}-{start}")
        fare_class = _classes_for(_seed_for(f"class-{slug}-{start}"), road_km)
        route_number = f"GEN-{slug}"

        for departure in _departures_for(rng, road_km):
            duration = _duration_for(rng, road_km)
            arrival_minutes = _to_minutes(departure) + duration
            overnight = arrival_minutes >= 24 * 60

            records.append(
                ServiceRecord(
                    route_id=f"MOD-{slug}-{departure.replace(':', '')}",
                    route_number=route_number,
                    service_name=f"{start} - {end}",
                    provider="SLTB",
                    origin=start,
                    destination=end,
                    departure_time=departure,
                    arrival_time=_format(arrival_minutes),
                    arrival_next_day=overnight,
                    # No fare is written. `fares.py` estimates one from distance
                    # for display, and `fare_unknown` keeps the Booking Agent
                    # from selling a modelled price as though it were published.
                    base_fare_lkr=None,
                    fare_unknown=True,
                    stops=[start, end],
                    classes=fare_class,
                    transit_type="EXPRESS_BUS",
                    synthetic=True,
                    synthetic_fields=[
                        "departure_time",
                        "arrival_time",
                        "duration_estimate",
                    ],
                    source="modelled: no published NTC fare or timetable for this pair",
                    confidence="modelled",
                )
            )
    return records


def generate_all() -> list[ServiceRecord]:
    _load_coords()
    existing = load_fixture(BUS_FILE)
    records: list[ServiceRecord] = []
    for origin, destination in unpublished_pairs(existing):
        records.extend(generate_corridor(origin, destination))
    return records


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    existing = load_fixture(BUS_FILE)
    generated = generate_all()

    merged, notes = merge_records(existing, generated, force=False)
    added = len(merged) - len(existing)
    corridors = len({r.route_id.rsplit("-", 1)[0] for r in generated})
    print(f"buses: {len(existing)} -> {len(merged)} rows ({added} generated)")
    for note in notes:
        print(f"  {note}")
    print(f"  {corridors} corridors modelled")

    # The property that matters: none of this may reach the money path.
    bookable = sum(1 for r in generated if not r.fare_unknown)
    print(f"  {bookable} carry a sellable fare (must be 0)")
    print("  fares are estimated for display by fares.py; booking refuses them")

    if args.dry_run:
        print("  (dry run — nothing written)")
    else:
        write_fixture(BUS_FILE, merged)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
