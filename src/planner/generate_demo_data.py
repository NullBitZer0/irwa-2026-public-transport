"""
Demo Schedule Generator
Member 2 — Planning Agent & Information Retrieval

Expands `data/processed/bus_routes.json` so the demo can answer real questions on
corridors that were previously missing. This is a DEMONSTRATION dataset: the
route numbers, endpoints, stop sequences, journey durations and fares are taken
from published NTC / SLTB sources, but the individual departure times and arrival
times are partly derived. Every generated row is therefore flagged:

    "synthetic": true,
    "synthetic_fields": ["arrival_time"]          <- departure time was published
    "synthetic_fields": ["departure_time", "arrival_time"]   <- both derived

`arrival_time` is always derived as `departure_time + published journey duration`,
never invented as a free value. Rows that carry no `synthetic` flag are the
hand-curated ones transcribed from a published timetable and must not be
regenerated.

Sources:
  * NTC inter-provincial full bus fare chart, effective 2026-03-24
    (https://www.ntc.gov.lk/bus_fare/2026/March/Inter%20Provincial%20Full%20Bus%20Fare.pdf)
  * NTC interim bus fare revision, effective 2026-07-06
    (https://www.ntc.gov.lk/Bus_info/bus_fares.php)
  * SLTB long-distance timetables via sltb.eseat.lk / published SLTB timetables

Usage:
    python -m src.planner.generate_demo_data --dry-run
    python -m src.planner.generate_demo_data
"""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass

from src.planner.schedule_ingest import (
    BUS_FILE,
    ServiceRecord,
    load_fixture,
    merge_records,
    write_fixture,
)

FARE_SOURCE = "https://www.ntc.gov.lk/bus_fare/2026/March/Inter%20Provincial%20Full%20Bus%20Fare.pdf"
SLTB_SOURCE = "https://sltb.eseat.lk/bus/schedules"

COLOMBO = "Colombo Bastian Mawatha"


@dataclass(frozen=True)
class Corridor:
    """
    One real corridor.

    route_number / endpoints / stops / duration / fares are published values.
    departures are published where available; otherwise a regular headway is
    used and both times are flagged as derived.
    """

    route_number: str
    name: str
    origin: str
    destination: str
    duration_minutes: int
    fare_normal: float
    fare_semi: float
    stops: tuple[str, ...]
    # Published departure times (HH:MM) from the origin, when known.
    departures: tuple[str, ...] = ()
    # Used to lay out departures when none are published.
    first_departure: str = "05:30"
    last_departure: str = "20:30"
    headway_minutes: int = 90
    source_url: str = SLTB_SOURCE


# Corridors, fares and durations taken from the published sources above.
CORRIDORS: tuple[Corridor, ...] = (
    Corridor(
        route_number="1",
        name="Colombo - Kandy",
        origin=COLOMBO,
        destination="Kandy",
        duration_minutes=165,  # ~2h45 on the A1
        fare_normal=830.0,
        fare_semi=1150.0,
        stops=(COLOMBO, "Kaduwela", "Nittambuwa", "Gampaha", "Veyangoda", "Polgahawela", "Peradeniya", "Kandy"),
        departures=(
            "05:10", "06:15", "07:00", "07:45", "08:30", "09:15",
            "10:00", "10:45", "11:30", "12:15", "13:00", "13:45",
            "14:30", "15:15", "16:00", "16:45", "17:30", "18:15",
        ),
        source_url=FARE_SOURCE,
    ),
    Corridor(
        route_number="2-1",
        name="Colombo - Galle",
        origin=COLOMBO,
        destination="Galle",
        duration_minutes=165,  # ~2h45
        fare_normal=800.0,
        fare_semi=1090.0,
        stops=(COLOMBO, "Moratuwa", "Panadura", "Kalutara", "Aluthgama", "Ambalangoda", "Hikkaduwa", "Galle"),
        first_departure="06:00",
        last_departure="18:00",
        headway_minutes=60,
        source_url=FARE_SOURCE,
    ),
    Corridor(
        route_number="2",
        name="Colombo - Matara",
        origin=COLOMBO,
        destination="Matara",
        duration_minutes=215,  # ~3h35
        fare_normal=797.0,
        fare_semi=1060.0,
        stops=(COLOMBO, "Moratuwa", "Kalutara", "Galle", "Weligama", "Matara"),
        first_departure="06:30",
        last_departure="17:30",
        headway_minutes=90,
        source_url=FARE_SOURCE,
    ),
    Corridor(
        route_number="8",
        name="Colombo - Matale",
        origin=COLOMBO,
        destination="Matale",
        duration_minutes=195,  # ~3h15
        fare_normal=990.0,
        fare_semi=1800.0,
        stops=(COLOMBO, "Gampaha", "Kandy", "Pallekele", "Matale"),
        first_departure="06:00",
        last_departure="17:00",
        headway_minutes=120,
        source_url=FARE_SOURCE,
    ),
    Corridor(
        route_number="4-3",
        name="Colombo - Anuradhapura",
        origin=COLOMBO,
        destination="Anuradhapura",
        duration_minutes=270,  # ~4h30 via the Central Expressway
        fare_normal=990.0,
        fare_semi=1320.0,
        stops=(COLOMBO, "Kaduwela", "Mirigama", "Kurunegala", "Dambulla", "Kekirawa", "Anuradhapura"),
        first_departure="06:15",
        last_departure="19:00",
        headway_minutes=120,
        source_url=FARE_SOURCE,
    ),
    Corridor(
        route_number="4",
        name="Colombo - Mannar",
        origin=COLOMBO,
        destination="Mannar",
        duration_minutes=360,  # ~6h
        fare_normal=1506.0,
        fare_semi=2010.0,
        stops=(COLOMBO, "Negombo", "Puttalam", "Anuradhapura", "Vavuniya", "Mannar"),
        first_departure="06:30",
        last_departure="18:30",
        headway_minutes=180,
        source_url=FARE_SOURCE,
    ),
    Corridor(
        route_number="87",
        name="Colombo - Jaffna (Northern Line)",
        origin=COLOMBO,
        destination="Jaffna",
        duration_minutes=405,  # ~6h45
        fare_normal=1701.0,
        fare_semi=2552.0,
        stops=(COLOMBO, "Negombo", "Chilaw", "Puttalam", "Nochchiyagama", "Anuradhapura", "Vavuniya", "Kilinochchi", "Jaffna"),
        departures=("05:45", "08:00", "14:10", "15:50", "17:30", "22:00"),
        source_url=FARE_SOURCE,
    ),
)


def _to_minutes(hhmm: str) -> int:
    hours, minutes = hhmm.split(":")
    return int(hours) * 60 + int(minutes)


def _format(total: int) -> str:
    wrapped = total % (24 * 60)
    return f"{wrapped // 60:02d}:{wrapped % 60:02d}"


def _departures_for(corridor: Corridor) -> list[tuple[str, bool]]:
    """Returns (departure_time, is_published) pairs for one direction."""
    if corridor.departures:
        return [(d, True) for d in corridor.departures]

    times: list[tuple[str, bool]] = []
    current = _to_minutes(corridor.first_departure)
    last = _to_minutes(corridor.last_departure)
    while current <= last:
        times.append((_format(current), False))
        current += corridor.headway_minutes
    return times


def generate_corridor(corridor: Corridor) -> list[ServiceRecord]:
    """
    Emits services for a corridor in both directions.

    Arrivals are always `departure + published duration`. Overnight arrivals are
    flagged so the UI can say so.
    """
    records: list[ServiceRecord] = []
    departures = _departures_for(corridor)

    for origin, destination, stops in (
        (corridor.origin, corridor.destination, corridor.stops),
        (corridor.destination, corridor.origin, tuple(reversed(corridor.stops))),
    ):
        for departure, published in departures:
            arrival_minutes = _to_minutes(departure) + corridor.duration_minutes
            arrival = _format(arrival_minutes)
            overnight = arrival_minutes >= 24 * 60

            derived = [] if published else ["departure_time"]
            derived.append("arrival_time")

            # Route ids stay stable and readable, e.g. SLTB-1-COL-KAN-0510. Both endpoints
            # are included: some corridors share a truncated 3-letter prefix.
            slug = f"{corridor.route_number}-" + "-".join(
                re.sub(r"[^A-Za-z0-9]+", "", part)[:4].upper()
                for part in (origin, destination)
            )
            records.append(
                ServiceRecord(
                    route_id=f"SLTB-{slug}-{departure.replace(':', '')}",
                    route_number=corridor.route_number,
                    service_name=f"{corridor.name} ({'Semi-Luxury' if published else 'Normal'})",
                    provider="SLTB",
                    origin=origin,
                    destination=destination,
                    departure_time=departure,
                    arrival_time=arrival,
                    arrival_next_day=overnight,
                    base_fare_lkr=corridor.fare_semi if published and corridor.departures else corridor.fare_normal,
                    stops=list(stops),
                    classes=["Semi-Luxury"] if published and corridor.departures else ["Ordinary"],
                    transit_type="EXPRESS_BUS",
                    synthetic=True,
                    synthetic_fields=derived,
                    source="NTC fare chart 2026-03-24 + published journey duration",
                    source_url=corridor.source_url,
                    confidence="derived-from-published-duration",
                )
            )
    return records


def generate_all() -> list[ServiceRecord]:
    records: list[ServiceRecord] = []
    for corridor in CORRIDORS:
        records.extend(generate_corridor(corridor))
    return records


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    generated = generate_all()
    existing = load_fixture(BUS_FILE)
    merged, notes = merge_records(existing, generated, force=False)

    added = len(merged) - len(existing)
    print(f"buses: {len(existing)} -> {len(merged)} rows ({added} generated)")
    for note in notes:
        print(f"  {note}")
    flagged = sum(1 for r in merged if r.get("synthetic"))
    print(f"  {flagged} of {len(merged)} rows are flagged synthetic")

    if args.dry_run:
        print("  (dry run — nothing written)")
    else:
        write_fixture(BUS_FILE, merged)
        print(f"  wrote {BUS_FILE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
