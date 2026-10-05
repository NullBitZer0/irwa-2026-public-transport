"""
Schedule Ingestion Tests
Member 2 — Planning Agent

The importer widens the curated fixtures, so the tests focus on the two promises
that make that safe:

  * it never invents a field — invalid rows are rejected, not guessed at
  * it never silently overwrites curated data — conflicts are reported

Fully offline: nothing here touches the network.

Run:
    pytest evaluation/test_schedule_ingest.py -v
"""

from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.planner.schedule_ingest import (  # noqa: E402
    BUS_FILE,
    TRAIN_FILE,
    ServiceRecord,  # noqa: E402
    SourceUnavailable,
    _parse_schedule_table,
    fetch_slr_schedule,
    fetch_sltb_schedule,
    load_fixture,
    merge_records,
    read_csv_rows,
    validate,
    write_csv_template,
    write_fixture,
)


def _record(**overrides) -> ServiceRecord:
    defaults = {
        "route_id": "BUS-TEST-1",
        "service_name": "Test Service",
        "provider": "SLTB",
        "origin": "Kandy",
        "destination": "Galle",
        "departure_time": "06:00",
        "arrival_time": "09:00",
        "base_fare_lkr": 900.0,
        "source": "sltb timetable",
    }
    defaults.update(overrides)
    return ServiceRecord(**defaults)


# ── Validation ───────────────────────────────────────────────────────────────

def test_valid_record_has_no_problems() -> None:
    assert validate(_record()) == []


@pytest.mark.parametrize(
    "overrides, expected",
    [
        ({"route_id": ""}, "missing route_id"),
        ({"origin": ""}, "missing origin"),
        ({"destination": ""}, "missing destination"),
        ({"provider": ""}, "missing provider"),
        ({"departure_time": "25:99"}, "refusing to guess"),
        ({"departure_time": "6am"}, "refusing to guess"),
        ({"departure_time": ""}, "refusing to guess"),
        ({"arrival_time": "99:00"}, "arrival_time"),
        ({"base_fare_lkr": -5.0}, "negative"),
        ({"source": ""}, "every row must say where it came from"),
    ],
)
def test_invalid_records_are_rejected(overrides, expected) -> None:
    problems = validate(_record(**overrides))
    assert any(expected in problem for problem in problems), problems


def test_missing_fare_is_allowed() -> None:
    """Fares are often published only at the counter, so an absent fare is fine."""
    assert validate(_record(base_fare_lkr=None)) == []


# ── Fixture shaping ──────────────────────────────────────────────────────────

def test_to_fixture_fills_only_safe_defaults() -> None:
    """An absent arrival falls back to the departure, but nothing is invented."""
    fixture = _record(arrival_time=None, stops=[], classes=[]).to_fixture()
    assert fixture["arrival_time"] == "06:00"
    assert fixture["stops"] == ["Kandy", "Galle"]
    assert fixture["transit_type"] == "EXPRESS_BUS"


def test_train_and_bus_are_told_apart() -> None:
    assert _record(provider="SLR").is_train() is True
    assert _record(provider="SLTB").is_train() is False
    assert _record(provider="PRIVATE_HIGHWAY").is_train() is False


def test_train_default_transit_type() -> None:
    assert _record(provider="SLR").to_fixture()["transit_type"] == "INTERCITY_EXPRESS"


# ── Merging ──────────────────────────────────────────────────────────────────

def test_new_row_is_added() -> None:
    merged, notes = merge_records([], [_record()])
    assert len(merged) == 1
    assert not any(n.startswith(("CONFLICT", "SKIPPED")) for n in notes)


def test_identical_row_is_reported_unchanged() -> None:
    existing = [_record().to_fixture()]
    merged, notes = merge_records(existing, [_record()])
    assert len(merged) == 1
    assert any(n.startswith("UNCHANGED") for n in notes), notes


def test_conflicting_row_keeps_the_curated_data() -> None:
    existing = [_record(base_fare_lkr=100.0).to_fixture()]
    _merged, notes = merge_records(existing, [_record(base_fare_lkr=999.0)])
    assert any(n.startswith("CONFLICT") for n in notes), notes
    assert "use --force" in " ".join(notes)


def test_conflicting_row_is_replaced_with_force() -> None:
    existing = [_record(base_fare_lkr=100.0).to_fixture()]
    merged, notes = merge_records(existing, [_record(base_fare_lkr=999.0)], force=True)
    assert merged[0]["base_fare_lkr"] == 999.0
    assert any(n.startswith("REPLACED") for n in notes)


def test_invalid_row_is_skipped_not_written() -> None:
    merged, notes = merge_records([], [_record(route_id="", departure_time="25:99")])
    assert merged == []
    assert any(n.startswith("SKIPPED") for n in notes), notes


def test_existing_rows_are_never_dropped() -> None:
    existing = [_record(route_id="KEEP-1").to_fixture()]
    merged, _ = merge_records(existing, [_record(route_id="NEW-1")])
    assert {row["route_id"] for row in merged} == {"KEEP-1", "NEW-1"}


# ── CSV ──────────────────────────────────────────────────────────────────────

def test_csv_round_trip(tmp_path) -> None:
    template = write_csv_template(str(tmp_path / "t.csv"))
    assert os.path.exists(template)
    rows = read_csv_rows(template)
    assert rows == []


def test_csv_parses_multi_value_fields(tmp_path) -> None:
    path = tmp_path / "in.csv"
    path.write_text(
        "route_id,service_name,provider,origin,destination,departure_time,"
        "arrival_time,base_fare_lkr,classes,transit_type,source,source_url,"
        "confidence,stops\n"
        "BUS-X1,Express,SLTB,Kandy,Galle,06:00,09:00,900,Semi-Luxury;Ordinary,"
        "EXPRESS_BUS,sltb timetable,https://example.org,published,"
        "Kandy;Galle\n",
        encoding="utf-8",
    )
    record = read_csv_rows(str(path))[0]
    assert record.classes == ["Semi-Luxury", "Ordinary"]
    assert record.stops == ["Kandy", "Galle"]
    assert record.base_fare_lkr == 900.0
    assert validate(record) == []


def test_csv_tolerates_blank_fare(tmp_path) -> None:
    path = tmp_path / "in.csv"
    path.write_text(
        "route_id,service_name,provider,origin,destination,departure_time,"
        "arrival_time,base_fare_lkr,classes,transit_type,source,source_url,"
        "confidence,stops\n"
        "BUS-X2,Express,SLTB,Kandy,Galle,06:00,09:00,,Semi-Luxury,EXPRESS_BUS,"
        "sltb timetable,https://example.org,published,Kandy;Galle\n",
        encoding="utf-8",
    )
    assert read_csv_rows(str(path))[0].base_fare_lkr is None


# ── Sources fail loudly ──────────────────────────────────────────────────────

def test_unknown_station_raises_before_any_request() -> None:
    """An unknown station must be reported, not turned into zero services."""
    with pytest.raises(SourceUnavailable, match="not in the SLTB station list"):
        fetch_sltb_schedule("Atlantis", "Galle", {"Kandy": "15"})


def test_slr_source_raises_rather_than_returning_nothing() -> None:
    """Never let an unreadable source look like 'no services exist'."""
    with pytest.raises(SourceUnavailable):
        fetch_slr_schedule("Colombo Fort", "Kandy")


def test_schedule_table_parsing() -> None:
    html = """
    <table class="table">
      <tr><th>Departure</th><th>Arrival</th><th>Route</th><th>via</th><th>Bus Type</th></tr>
      <tr><td>06:00</td><td>09:30</td><td>1</td><td>Kandy, Galle</td><td>Semi-Luxury</td></tr>
      <tr><td>not-a-time</td><td>10:00</td><td>1</td><td>Kandy</td><td>Ordinary</td></tr>
    </table>
    """
    rows = _parse_schedule_table(html)
    assert len(rows) == 1, "the header row and the bad time row must both be skipped"
    assert rows[0].departure_time == "06:00"
    assert rows[0].provider == "SLTB"
    assert rows[0].stops == ["Kandy", "Galle"]


# ── The real fixtures stay valid ──────────────────────────────────────────────

@pytest.mark.parametrize("path", [TRAIN_FILE, BUS_FILE])
def test_existing_fixture_rows_are_acceptable_to_the_importer(path: str) -> None:
    """Every curated row already on disk must pass the importer's own validation."""
    rows = load_fixture(path)
    assert rows, f"{path} should not be empty"
    for row in rows:
        record = ServiceRecord(
            route_id=row["route_id"],
            service_name=row["service_name"],
            provider=row["provider"],
            origin=row["origin"],
            destination=row["destination"],
            departure_time=row["departure_time"],
            arrival_time=row.get("arrival_time"),
            base_fare_lkr=row.get("base_fare_lkr"),
            source="existing fixture",
        )
        assert validate(record) == [], (row["route_id"], validate(record))


def test_fixture_write_is_valid_json(tmp_path) -> None:
    path = tmp_path / "out.json"
    write_fixture(str(path), [_record().to_fixture()])
    assert json.loads(path.read_text(encoding="utf-8"))[0]["route_id"] == "BUS-TEST-1"
