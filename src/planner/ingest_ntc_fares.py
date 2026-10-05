"""
NTC Bus Fare Ingestion
Member 2 — Planning Agent & Information Retrieval

Fills in the fares that were missing from `data/processed/bus_routes.json`.

Route codes and endpoints come from routemaster.lk, but it publishes no prices,
so most generated rows carried `fare_unknown` and could not be booked. This
module ingests the National Transport Commission's published inter-provincial and
expressway full-ticket fares and matches them to our services **by town pair**,
which is the one key both sources share.

Source: the NTC tables for the annual revision effective 2026-07-06, as
transcribed and published by induwara.lk (439 inter-provincial routes, 51
expressway services). A secondary transcription is used rather than NTC's own
Sinhala PDF because that PDF is a scan with no text layer; the figures are
cross-checkable against the operator's fare chart, which is what the conductor
uses as the legal authority.

A service whose town pair NTC does not publish keeps `fare_unknown`. Nothing is
interpolated: a missing fare stays missing rather than becoming a guess.

Usage:
    python -m src.planner.ingest_ntc_fares --check
    python -m src.planner.ingest_ntc_fares --dry-run
    python -m src.planner.ingest_ntc_fares
"""

from __future__ import annotations

import argparse
import json
import re

import httpx

from src.planner.hybrid_retriever import _station_key

SOURCE_URL = "https://induwara.lk/tools/sri-lanka-bus-fare-calculator"
SOURCE_NAME = "NTC inter-provincial + expressway full fares, effective 2026-07-06"
FARES_FILE = "data/processed/ntc_bus_fares.json"
BUS_FILE = "data/processed/bus_routes.json"

TIMEOUT = 45.0
USER_AGENT = "LankaJourneyAI/1.0 (academic transit project)"

_TABLE = re.compile(r"<table[^>]*>(.*?)</table>", re.S)
_ROW = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S)
_CELL = re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>", re.S)
_ARROW = re.compile(r"^(?P<origin>.+?)\s*→\s*(?P<dest>.+)$")


def _money(value: str) -> float | None:
    """'Rs 1,018' -> 1018.0. An em dash means NTC publishes no fare for that class."""
    cleaned = re.sub(r"[^\d.]", "", value or "")
    if not cleaned:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def parse_fares(html: str) -> dict[str, dict]:
    """
    Parses the published fare tables into {(origin, destination): fares}.

    Two tables are read: the inter-provincial full-ticket fares (normal,
    semi-luxury, AC luxury) and the expressway services (one route-flat fare).
    Both directions are stored — NTC publishes e.g. "Colombo → Kandy" but not
    necessarily the reverse, and travellers ask either way.
    """
    fares: dict[str, dict] = {}

    for table in _TABLE.findall(html):
        for row in _ROW.findall(table):
            cells = [
                re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", c)).strip()
                for c in _CELL.findall(row)
            ]
            if len(cells) < 3:
                continue

            route_label = cells[0]
            pair = _ARROW.match(route_label)
            if not pair:
                continue  # header row

            origin = pair.group("origin").strip()
            destination = pair.group("dest").strip()
            if not origin or not destination or origin == destination:
                continue

            route_no = cells[1]
            if route_no.upper().startswith("EX"):
                # Expressway: one route-flat fare, in either column 2 or 3.
                fare = _money(cells[-1])
                entry = {"route_no": route_no, "expressway": fare}
            else:
                normal = _money(cells[2]) if len(cells) > 2 else None
                semi = _money(cells[3]) if len(cells) > 3 else None
                luxury = _money(cells[4]) if len(cells) > 4 else None
                entry = {
                    "route_no": route_no,
                    "normal": normal,
                    "semi": semi,
                    "luxury": luxury,
                }

            published = [v for v in entry.values() if isinstance(v, float)]
            if not published:
                continue
            entry["from_fare"] = min(published)
            entry["source"] = SOURCE_NAME

            fares[f"{origin}|{destination}"] = entry
            fares[f"{destination}|{origin}"] = entry

    return fares


def fetch_fares(url: str = SOURCE_URL) -> dict[str, dict]:
    """Downloads and parses the fare tables. Raises rather than returning empty."""
    try:
        with httpx.Client(
            timeout=TIMEOUT, headers={"User-Agent": USER_AGENT}, follow_redirects=True
        ) as client:
            response = client.get(url)
            response.raise_for_status()
            html = response.text
    except Exception as exc:
        raise RuntimeError(f"fare source unreachable: {exc}") from exc

    fares = parse_fares(html)
    if len(fares) < 50:
        raise RuntimeError(
            f"Only {len(fares)} fare rows parsed — the page structure has probably "
            f"changed. Refusing to write a near-empty fare table."
        )
    return fares


def station_keys(name: str) -> set[str]:
    """
    Keys a published town name can be matched on.

    The two sources name places differently: NTC writes "Colombo" and
    "Colombo (Makumbura)", our fixtures write "Colombo Bastian Mawatha" and
    "Makumbura MMC". Matching on the leading token plus any parenthetical
    qualifier reconciles them without hand-mapping hundreds of spellings.
    """
    keys = {_station_key(name)}
    parenthetical = re.search(r"\(([^)]+)\)", name or "")
    if parenthetical:
        inner = _station_key(parenthetical.group(1))
        if inner:
            keys.add(inner)
    keys.discard("")
    return keys


def _index_fares(fares: dict[str, dict]) -> dict[str, dict]:
    """Re-keys the fare table on station keys so lookups are spelling-tolerant."""
    index: dict[str, dict] = {}
    for pair, entry in fares.items():
        origin, destination = pair.split("|", 1)
        for origin_key in station_keys(origin):
            for destination_key in station_keys(destination):
                index.setdefault(f"{origin_key}|{destination_key}", entry)
    return index


def _key(origin: str, destination: str) -> str:
    return f"{_station_key(origin)}|{_station_key(destination)}"


def apply_fares(rows: list[dict], fares: dict[str, dict]) -> tuple[list[dict], dict]:
    """
    Sets a published fare on every service whose town pair NTC covers.

    Returns (rows, stats). Services NTC does not publish keep `fare_unknown` —
    a missing fare is never turned into a zero, which would read as free.
    """
    stats = {"matched": 0, "still_unknown": 0, "updated": 0, "unchanged": 0}
    updated: list[dict] = []

    for row in rows:
        was_unknown = row.get("fare_unknown") or not (row.get("base_fare_lkr") or 0) > 0
        entry = fares.get(_key(str(row.get("origin", "")), str(row.get("destination", ""))))

        if entry is None:
            # Try the reverse direction: a symmetric service may be filed
            # under the opposite town pair.
            entry = fares.get(_key(str(row.get("destination", "")), str(row.get("origin", ""))))
        if entry is None:
            row["fare_unknown"] = True
            stats["still_unknown"] += 1
            updated.append(row)
            continue

        # Prefer the class the row advertises, then fall back to the cheapest
        # class NTC actually publishes for that pair.
        classes = row.get("classes") or []
        wanted = None
        if any("semi" in str(c).lower() for c in classes):
            wanted = entry.get("semi")
        elif any("luxury" in str(c).lower() or "ac" in str(c).lower() for c in classes):
            wanted = entry.get("luxury")
        if wanted is None:
            wanted = entry.get("from_fare")

        if wanted is None:
            row["fare_unknown"] = True
            stats["still_unknown"] += 1
            updated.append(row)
            continue

        row["base_fare_lkr"] = float(wanted)
        row.pop("fare_unknown", None)
        # The NTC route number is authoritative for pricing, so record it when
        # the row does not already carry one.
        row.setdefault("ntc_route_no", entry["route_no"])
        row["fare_source"] = SOURCE_NAME
        stats["matched"] += 1
        if was_unknown:
            stats["updated"] += 1
        else:
            stats["unchanged"] += 1
        updated.append(row)

    return updated, stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    parser.add_argument("--check", action="store_true", help="fetch and report only")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    fares = fetch_fares()
    fares = _index_fares(fares)
    print(f"parsed {len(fares)} town-pair fares ({SOURCE_NAME})")
    for pair in ("Colombo|Kandy", "Colombo|Jaffna", "Colombo|Galle", "Colombo|Matara"):
        entry = fares.get(pair)
        if entry:
            if entry.get("expressway"):
                detail = f"expressway flat {entry['expressway']}"
            else:
                detail = (
                    f"normal {entry.get('normal')} semi {entry.get('semi')} "
                    f"luxury {entry.get('luxury')}"
                )
            print(f"  {pair:<22} route {entry['route_no']:<10} {detail}")

    rows = json.load(open(BUS_FILE, encoding="utf-8"))
    updated, stats = apply_fares(rows, fares)

    unknown = sum(1 for r in updated if r.get("fare_unknown"))
    total = len(updated)
    print(
        f"\nbus services: {total - stats['still_unknown']}/{total} now have a "
        f"published fare ({unknown} still unpriced)"
    )
    print(f"  newly priced: {stats['updated']}, repriced: {stats['unchanged']}")

    if args.check or args.dry_run:
        print("  (nothing written)")
        return 0

    with open(FARES_FILE, "w", encoding="utf-8") as handle:
        json.dump(fares, handle, indent=2, ensure_ascii=False, sort_keys=True)
        handle.write("\n")
    print(f"  wrote {FARES_FILE}")

    with open(BUS_FILE, "w", encoding="utf-8") as handle:
        json.dump(updated, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    print(f"  wrote {BUS_FILE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
