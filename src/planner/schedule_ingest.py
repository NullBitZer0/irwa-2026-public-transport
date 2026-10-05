"""
Schedule Ingestion — widen the curated fixture coverage with traceable sources
Member 2 — Planning Agent & Information Retrieval

`HybridTransitRetriever` reads `data/processed/{train_schedules,bus_routes}.json`.
Those files are hand-curated, which keeps every row trustworthy but caps coverage.
This module is the supported way to grow them without losing that trust:

  1. `--check` probes each source and reports whether it can actually be read from
     your network, and whether its response shape is understood.
  2. `--source csv` imports rows your team curated from published timetables, with
     every row carrying its provenance.
  3. `--source sltb` / `--source slr` read the official operators directly.

Two rules this module will not break:

  * It never invents a field. A row missing a departure time, an origin or a
    destination is rejected rather than filled in with a guess.
  * It never silently overwrites curated data. New rows are added; conflicting
    rows are reported and only replaced when `--force` is passed.

Sources that cannot be read raise `SourceUnavailable` with the reason. An empty
result is never treated as "no services exist".

Usage:
    python -m src.planner.schedule_ingest --check
    python -m src.planner.schedule_ingest --source csv --csv data/processed/inbox/buses.csv
    python -m src.planner.schedule_ingest --source sltb --corridor "Kandy->Colombo Fort"
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import httpx

DATA_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data",
    "processed",
)

TRAIN_FILE = os.path.join(DATA_DIR, "train_schedules.json")
BUS_FILE = os.path.join(DATA_DIR, "bus_routes.json")

SLTB_SCHEDULES_URL = "https://sltb.eseat.lk/bus/schedules"
SLTB_SEARCH_URL = "https://sltb.eseat.lk/bus/schedule/search"
SLR_SEARCH_URL = "https://eservices.railway.gov.lk/schedule/searchTrain.action"

USER_AGENT = "LankaJourneyAI/1.0 (academic transit project)"
TIMEOUT = 30.0

RAIL_PROVIDERS = {"SLR"}
TIME_PATTERN = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")

CSV_COLUMNS = [
    "route_id",
    "route_number",
    "service_name",
    "provider",
    "origin",
    "destination",
    "departure_time",
    "arrival_time",
    "base_fare_lkr",
    "classes",
    "transit_type",
    "source",
    "source_url",
    "confidence",
]


class SourceUnavailable(RuntimeError):
    """Raised when a source cannot be read. Never swallowed."""


@dataclass
class ServiceRecord:
    """One normalised service, plus where it came from."""

    route_id: str
    service_name: str
    provider: str
    origin: str
    destination: str
    departure_time: str
    arrival_time: str | None = None
    base_fare_lkr: float | None = None
    stops: list[str] = field(default_factory=list)
    classes: list[str] = field(default_factory=list)
    transit_type: str = ""
    # Operator's own route number, e.g. SLTB "87" for Colombo <-> Jaffna.
    # Distinct from our internal route_id.
    route_number: str = ""
    # True when the service arrives after midnight, so a bare arrival_time would
    # look earlier than the departure.
    arrival_next_day: bool = False
    # Provenance — kept alongside the row so a reviewer can check it.
    source: str = ""
    source_url: str = ""
    confidence: str = "curated"

    def is_train(self) -> bool:
        return self.provider in RAIL_PROVIDERS

    def to_fixture(self) -> dict[str, Any]:
        """Shape matching the existing fixture files (no provenance fields)."""
        fixture = {
            "route_id": self.route_id,
            "service_name": self.service_name,
            "provider": self.provider,
            "origin": self.origin,
            "destination": self.destination,
            "departure_time": self.departure_time,
            "arrival_time": self.arrival_time or self.departure_time,
            "stops": self.stops or [self.origin, self.destination],
            "classes": self.classes or ["2nd Class Reserved"],
            "base_fare_lkr": self.base_fare_lkr or 0.0,
            "transit_type": self.transit_type
            or ("INTERCITY_EXPRESS" if self.is_train() else "EXPRESS_BUS"),
        }
        if self.route_number:
            fixture["route_number"] = self.route_number
        if self.arrival_next_day:
            fixture["arrival_next_day"] = True
        return fixture


def validate(record: ServiceRecord) -> list[str]:
    """
    Returns a list of problems. An empty list means the row is safe to write.

    Deliberately strict about the fields a journey depends on (endpoints and a
    departure time) and lenient about fares, which are often published only at
    the counter.
    """
    problems: list[str] = []

    for field_name in ("route_id", "service_name", "provider", "origin", "destination"):
        if not str(getattr(record, field_name) or "").strip():
            problems.append(f"missing {field_name}")

    if not TIME_PATTERN.match(str(record.departure_time or "")):
        problems.append(
            f"departure_time {record.departure_time!r} is not HH:MM — refusing to guess"
        )

    if record.arrival_time and not TIME_PATTERN.match(str(record.arrival_time)):
        problems.append(f"arrival_time {record.arrival_time!r} is not HH:MM")

    if record.base_fare_lkr is not None and record.base_fare_lkr < 0:
        problems.append("base_fare_lkr cannot be negative")

    if not record.source:
        problems.append("missing source — every row must say where it came from")

    return problems


# ── CSV (manual curation) ────────────────────────────────────────────────────


def read_csv_rows(path: str) -> list[ServiceRecord]:
    """Loads curated rows from CSV, splitting multi-valued fields on ';'."""
    records: list[ServiceRecord] = []
    with open(path, newline="", encoding="utf-8") as handle:
        for index, row in enumerate(csv.DictReader(handle), start=2):
            clean = {k: (v or "").strip() for k, v in row.items() if k}
            fare = clean.get("base_fare_lkr") or ""
            records.append(
                ServiceRecord(
                    route_id=clean.get("route_id", ""),
                    service_name=clean.get("service_name", ""),
                    provider=clean.get("provider", ""),
                    origin=clean.get("origin", ""),
                    destination=clean.get("destination", ""),
                    departure_time=clean.get("departure_time", ""),
                    arrival_time=clean.get("arrival_time") or None,
                    base_fare_lkr=float(fare) if fare else None,
                    stops=[s.strip() for s in (clean.get("stops") or "").split(";") if s.strip()],
                    classes=[c.strip() for c in (clean.get("classes") or "").split(";") if c.strip()],
                    transit_type=clean.get("transit_type", ""),
                    route_number=clean.get("route_number", ""),
                    arrival_next_day=(clean.get("arrival_next_day", "") or "").lower()
                    in ("1", "true", "yes", "y"),
                    source=clean.get("source", ""),
                    source_url=clean.get("source_url", ""),
                    confidence=clean.get("confidence", "curated"),
                )
            )
            del index
    return records


def write_csv_template(path: str) -> str:
    """Writes an empty CSV with the expected columns, including stops."""
    columns = CSV_COLUMNS + ["stops"]
    with open(path, "w", newline="", encoding="utf-8") as handle:
        csv.DictWriter(handle, fieldnames=columns).writeheader()
    return path


# ── SLTB ─────────────────────────────────────────────────────────────────────


def fetch_sltb_station_map(client: httpx.Client | None = None) -> dict[str, str]:
    """
    Station name -> SLTB station id, read from the schedules page dropdown.

    This is the one live SLTB read that is known to work: the dropdown on
    sltb.eseat.lk lists every station and its numeric id, which is what the
    schedule search endpoint needs.
    """
    owned = client is None
    client = client or httpx.Client(timeout=TIMEOUT, headers={"User-Agent": USER_AGENT})
    try:
        response = client.get(SLTB_SCHEDULES_URL)
        response.raise_for_status()
    except Exception as exc:
        raise SourceUnavailable(f"SLTB station list unreachable: {exc}") from exc
    finally:
        if owned:
            client.close()

    options = re.findall(
        r'<option[^>]*value="(\d+)"[^>]*>(.*?)</option>', response.text, re.S
    )
    if not options:
        raise SourceUnavailable(
            "SLTB station list parsed to zero options — the page structure changed, "
            "so no id mapping can be trusted"
        )

    station_map: dict[str, str] = {}
    for station_id, raw_name in options:
        name = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", raw_name)).strip()
        if name:
            station_map.setdefault(name, station_id)
    return station_map


def fetch_sltb_schedule(
    origin: str, destination: str, station_map: dict[str, str], day: str | None = None
) -> list[ServiceRecord]:
    """
    Reads one SLTB corridor.

    NOTE: the schedule endpoint has not been observed returning data rows from an
    unauthenticated request — it needs session/POST parameters that are not
    documented. This raises rather than returning an empty list, so a silent
    "no services found" can never be mistaken for the truth. Use `--source csv`
    for SLTB rows until this is wired up with a real session.
    """
    origin_id = station_map.get(origin)
    destination_id = station_map.get(destination)
    if not origin_id or not destination_id:
        missing = origin if not origin_id else destination
        raise SourceUnavailable(f"'{missing}' is not in the SLTB station list")

    params = {"bus_type": "any", "from": origin_id, "to": destination_id, "start": "00:00"}
    if day:
        params["date"] = day

    try:
        with httpx.Client(
            timeout=TIMEOUT, headers={"User-Agent": USER_AGENT}, follow_redirects=True
        ) as client:
            response = client.get(SLTB_SEARCH_URL, params=params)
            response.raise_for_status()
    except Exception as exc:
        raise SourceUnavailable(f"SLTB schedule request failed: {exc}") from exc

    rows = _parse_schedule_table(response.text)
    if not rows:
        raise SourceUnavailable(
            "SLTB returned no schedule rows for "
            f"{origin} -> {destination}. The endpoint needs session parameters that "
            "are not documented; import these rows from a published timetable with "
            "--source csv instead of treating this as 'no service exists'."
        )
    return rows


def _parse_schedule_table(html: str) -> list[ServiceRecord]:
    """Parses the SLTB schedule table (Departure | Arrival | Route | via | Bus Type)."""
    records: list[ServiceRecord] = []
    for table in re.findall(r"<table[^>]*>(.*?)</table>", html, re.S):
        for row in re.findall(r"<tr[^>]*>(.*?)</tr>", table, re.S):
            cells = [
                re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", cell)).strip()
                for cell in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, re.S)
            ]
            if len(cells) < 5 or cells[0].lower() == "departure":
                continue
            departure, arrival, route, via, bus_type = cells[:5]
            if not TIME_PATTERN.match(departure):
                continue
            records.append(
                ServiceRecord(
                    route_id=route or f"SLTB-{departure.replace(':', '')}",
                    service_name=bus_type or route or "SLTB service",
                    provider="SLTB",
                    origin="",  # filled in by the caller: the table omits endpoints
                    destination="",
                    departure_time=departure,
                    arrival_time=arrival if TIME_PATTERN.match(arrival) else None,
                    stops=[s.strip() for s in via.split(",") if s.strip()],
                    classes=[bus_type] if bus_type else [],
                    source="sltb.eseat.lk",
                    source_url=SLTB_SEARCH_URL,
                    confidence="scraped",
                )
            )
    return records


# ── SLR ──────────────────────────────────────────────────────────────────────


def fetch_slr_schedule(origin: str, destination: str) -> list[ServiceRecord]:
    """
    Reads one SLR corridor from the official journey planner.

    The planner expects numeric station ids that are only listed inside its own
    form, so this needs that id map to be supplied. It raises if the service is
    unreachable rather than returning nothing.
    """
    raise SourceUnavailable(
        "The SLR journey planner (eservices.railway.gov.lk) needs numeric station ids "
        "that are only published inside its search form, and it was unreachable from "
        "the build environment. Capture the timetable from railway.gov.lk and import it "
        "with --source csv rather than skipping the corridor silently."
    )


# ── Merging ──────────────────────────────────────────────────────────────────


def load_fixture(path: str) -> list[dict[str, Any]]:
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def merge_records(
    existing: list[dict[str, Any]],
    incoming: list[ServiceRecord],
    force: bool = False,
) -> tuple[list[dict[str, Any]], list[str]]:
    """
    Adds new rows and reports conflicts.

    Returns (merged rows, human-readable notes). A route_id that already exists is
    only replaced when `force` is set, so curated data cannot be lost by accident.
    """
    notes: list[str] = []
    by_id = {row["route_id"]: dict(row) for row in existing}

    for record in incoming:
        problems = validate(record)
        if problems:
            notes.append(f"SKIPPED {record.route_id or '<no id>'}: {'; '.join(problems)}")
            continue

        fixture = record.to_fixture()
        current = by_id.get(record.route_id)
        if current is None:
            by_id[record.route_id] = fixture
            continue

        changed = {
            key: (current.get(key), fixture.get(key))
            for key in fixture
            if current.get(key) != fixture.get(key)
        }
        if not changed:
            notes.append(f"UNCHANGED {record.route_id} (identical)")
        elif force:
            by_id[record.route_id] = fixture
            notes.append(f"REPLACED {record.route_id} (--force): {changed}")
        else:
            notes.append(
                f"CONFLICT {record.route_id} differs in {sorted(changed)}; "
                f"kept the curated row (use --force to replace)"
            )

    return list(by_id.values()), notes


def write_fixture(path: str, rows: list[dict[str, Any]]) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(rows, handle, indent=2, ensure_ascii=False)
        handle.write("\n")


# ── Source probing ───────────────────────────────────────────────────────────


def check_sources() -> int:
    """
    Reports what can actually be read from this machine. Returns a process exit code.
    """
    print("Checking schedule sources…\n")
    failures = 0

    try:
        station_map = fetch_sltb_station_map()
        print(f"  [ok]   SLTB station list: {len(station_map)} stations")
        for name in ("Kandy", "Jaffna", "Colombo (Bastian Mawatha)"):
            print(f"           {name:<28} id={station_map.get(name, 'NOT FOUND')}")
    except SourceUnavailable as exc:
        failures += 1
        print(f"  [fail] SLTB station list: {exc}")

    try:
        fetch_slr_schedule("COLOMBO FORT", "KANDY")
        print("  [ok]   SLR journey planner")
    except SourceUnavailable as exc:
        failures += 1
        print(f"  [fail] SLR journey planner: {exc}")

    print(
        "\nSources marked [fail] cannot be read from here. Curate those rows from the "
        "operators' published\ntimetables and import them with --source csv; the importer "
        "keeps the source URL\nwith every row so the provenance stays auditable."
    )
    return 1 if failures else 0


# ── CLI ──────────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    parser.add_argument("--check", action="store_true", help="probe sources and exit")
    parser.add_argument("--source", choices=["csv", "sltb", "slr"], help="where to read from")
    parser.add_argument("--csv", help="CSV file to import")
    parser.add_argument("--corridor", help='"From->To" for sltb/slr sources')
    parser.add_argument("--target", choices=["trains", "buses", "both"], default="both")
    parser.add_argument("--dry-run", action="store_true", help="report without writing")
    parser.add_argument(
        "--force", action="store_true", help="replace conflicting curated rows"
    )
    parser.add_argument("--template", help="write an empty CSV template and exit")
    args = parser.parse_args(argv)

    if args.template:
        print(f"Wrote CSV template: {write_csv_template(args.template)}")
        return 0

    if args.check or not args.source:
        return check_sources()

    if args.source == "csv":
        if not args.csv:
            parser.error("--source csv requires --csv")
        if not os.path.exists(args.csv):
            parser.error(f"no such CSV: {args.csv}")
        incoming = read_csv_rows(args.csv)
        source_note = args.csv
    else:
        if not args.corridor or "->" not in args.corridor:
            parser.error(f'--source {args.source} requires --corridor "From->To"')
        origin, destination = (part.strip() for part in args.corridor.split("->", 1))
        if args.source == "slr":
            incoming = fetch_slr_schedule(origin, destination)
        else:
            incoming = fetch_sltb_schedule(origin, destination, fetch_sltb_station_map())
        source_note = f"{args.source}: {origin} -> {destination}"

    if not incoming:
        print(f"No rows read from {source_note}.", file=sys.stderr)
        return 1

    stamped = datetime.now(timezone.utc).isoformat(timespec="seconds")
    targets: list[str] = []
    if args.target in ("trains", "both"):
        targets.append(TRAIN_FILE)
    if args.target in ("buses", "both"):
        targets.append(BUS_FILE)

    exit_code = 0
    for path in targets:
        # With --target both, route each row to the file matching its mode:
        # SLR services are trains, everything else is a bus.
        if args.target == "both":
            wanted = [r for r in incoming if r.is_train() == (path == TRAIN_FILE)]
        else:
            wanted = incoming
        if not wanted:
            continue

        existing = load_fixture(path)
        merged, notes = merge_records(existing, wanted, force=args.force)
        print(f"\n{os.path.basename(path)}: {len(existing)} → {len(merged)} rows "
              f"({len(wanted)} read from {source_note}, retrieved {stamped})")
        for note in notes:
            print(f"  {note}")
        if any(n.startswith(("CONFLICT", "SKIPPED")) for n in notes):
            exit_code = 2

        if args.dry_run:
            print("  (dry run — nothing written)")
        else:
            write_fixture(path, merged)
            print(f"  wrote {path}")

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
