"""
The schedules table and the route map.

Both views are read-only projections of the same corpus, so the tests are mostly
about not lying: not dropping a mode, not dropping a place silently, and not
implying a service runs on a published timetable when its times were generated.

Run:
    pytest evaluation/test_schedules_and_map.py -v
"""

from __future__ import annotations

import json
import math
import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.planner import geo  # noqa: E402
from src.planner.hybrid_retriever import HybridTransitRetriever  # noqa: E402
from src.planner.server import app as planner_app  # noqa: E402

# The gazetteer used to check our city coordinates. Natural Earth only lists
# major settlements, so it covers some of our table and not all of it.
GAZETTEER = json.loads(
    (Path(__file__).resolve().parent.parent / "data" / "geo" / "lka_places.json").read_text()
)["places"]

SCHEDULES = HybridTransitRetriever().schedules


@pytest.fixture(scope="module")
def _fixtures_backend():
    """
    Pins these tests to the fixture retrieval backend, explicitly.

    The backend is chosen when a retriever is built, and the default is read at
    module import. That made the result depend on import order: the orchestrator
    calls load_dotenv() at import, so if anything imported it first, a retriever
    built later here would pick up RETRIEVER_BACKEND=opensearch from .env, load
    the dense encoder, and rank differently — a test that passes alone and fails
    in the full suite.

    Constructing with the backend named — and pointing the planner module's
    retriever at it, so the endpoint tests see the same thing — removes the
    dependency on both import order and the developer's .env.
    """
    from src.planner import server as planner_server

    original = planner_server._retriever
    retriever = HybridTransitRetriever(backend="fixtures")
    planner_server._retriever = retriever
    try:
        yield retriever
    finally:
        # Restore it. Leaving our retriever in place would silently change how
        # every later test module retrieves, which is exactly the kind of
        # cross-test coupling this fixture exists to remove.
        planner_server._retriever = original


@pytest.fixture(scope="module")
def client(_fixtures_backend):
    return TestClient(planner_app)


# ── Geodata ──────────────────────────────────────────────────────────────────


def test_every_place_in_the_corpus_can_be_placed_on_the_map() -> None:
    """
    A route that silently disappears because its town was unmapped is a bug the
    user reports as "the map is missing my bus". So the table is exhaustive.
    """
    assert geo.unmapped_places(SCHEDULES) == []


def test_unknown_places_resolve_to_nothing_rather_than_guessing() -> None:
    assert geo.resolve("Atlantis") is None
    assert geo.resolve("") is None
    assert geo.resolve(None) is None


def test_colombo_suburbs_cluster_into_one_city_node() -> None:
    """
    Drawing Pettah and Kotahena as separate pins a few kilometres apart would
    imply a precision this data does not have.
    """
    for stop in ("Pettah", "Kotahena", "Colombo Fort", "Bastian Mawatha – Fort"):
        resolved = geo.resolve(stop)
        assert resolved is not None and resolved[0] == "Colombo"

    # And they therefore produce no line between them.
    intra = [
        r for r in SCHEDULES
        if (geo.resolve(r.get("origin")) or ("",))[0] == (geo.resolve(r.get("destination")) or ("",))[0]
    ]
    assert intra, "the corpus should have some intra-city services"
    for record in intra:
        assert not any(
            c["origin_city"] == c["destination_city"] for c in geo.corridors([record])
        )


def _km(a: tuple[float, float], b: tuple[float, float]) -> float:
    (lat1, lon1), (lat2, lon2) = a, b
    return 6371 * math.hypot(
        math.radians(lat2 - lat1), math.radians(lon2 - lon1) * math.cos(math.radians(lat1))
    )


def _distance_to_coast_km(lat: float, lng: float) -> float:
    return min(_km((lat, lng), point) for ring in geo.ISLAND_RINGS for point in ring)


def test_coordinates_are_inside_the_declared_map_bounds() -> None:
    """Otherwise a route draws outside the viewport and vanishes."""
    bounds = geo.map_bounds()

    for city, (lat, lng) in geo.CITY_COORDS.items():
        assert bounds["min_lat"] <= lat <= bounds["max_lat"], city
        assert bounds["min_lng"] <= lng <= bounds["max_lng"], city


def test_the_map_bounds_contain_every_city_and_the_whole_coastline() -> None:
    """
    The extent is derived from the data, not hardcoded in the frontend.

    If it were hardcoded, a corrected coordinate would plot off-canvas and look
    like a missing route rather than an out-of-date constant.
    """
    bounds = geo.map_bounds()

    for ring in geo.ISLAND_RINGS:
        for lat, lng in ring:
            assert bounds["min_lat"] <= lat <= bounds["max_lat"]
            assert bounds["min_lng"] <= lng <= bounds["max_lng"]


def test_the_outline_is_real_boundary_data_not_a_hand_trace() -> None:
    """
    The coarse fallback exists for when the data file is missing, and it is fine
    for orientation — but shipping it by accident would be a silent downgrade.
    """
    assert "Natural Earth" in geo.boundary_source()

    main = geo.ISLAND_RINGS[0]
    assert len(main) > 500, "a traced outline is nowhere near this detailed"
    assert main[0] == main[-1], "an unclosed ring renders as an open shape"
    for lat, lng in main:
        assert 5.8 <= lat <= 9.9 and 79.6 <= lng <= 81.95


def test_the_offshore_islands_are_kept() -> None:
    """
    Mannar is a town on Mannar Island. Dropping the smaller rings — which an
    earlier version did, keeping only the largest — put it in the sea.
    """
    assert len(geo.ISLAND_RINGS) >= 3
    assert sum(len(r) for r in geo.ISLAND_RINGS) > len(geo.ISLAND_RINGS[0])

    mannar = geo.resolve("Mannar")
    assert mannar is not None
    assert geo.point_in_boundary(mannar[1], mannar[2]), "Mannar is not on any drawn landmass"


def test_every_city_is_on_land_or_within_the_coastline_resolution() -> None:
    """
    No city may be stranded in the sea.

    The tolerance is not a fudge: this boundary is generalised outward by a few
    kilometres, and Galle — genuinely on the shore — sits 2.4 km off it, so
    Kalutara and Aluthgama do too. A real data error looks nothing like this:
    Deniyaya was a coastal latitude for an inland town, and asserting "inside
    the outline" alone would have let a few-kilometre mistake through.
    """
    for city, (lat, lng) in geo.CITY_COORDS.items():
        if geo.point_in_boundary(lat, lng):
            continue
        assert _distance_to_coast_km(lat, lng) <= 6, (
            f"{city} plots {_distance_to_coast_km(lat, lng):.1f} km out to sea"
        )


def test_our_city_coordinates_agree_with_the_gazetteer() -> None:
    """
    The coordinate table is hand-entered, so it is checked against a source.

    This is what caught Moratuwa sitting 8.5 km from where Natural Earth puts
    it. Coverage is partial — only major settlements are listed — so the test
    asserts agreement where an entry exists rather than demanding one.
    """
    tolerance_km = 6.0
    checked = 0

    for name, (lat, lng) in geo.CITY_COORDS.items():
        reference = GAZETTEER.get(name)
        if reference is None:
            continue
        checked += 1
        distance = _km((lat, lng), (reference[0], reference[1]))
        assert distance <= tolerance_km, (
            f"{name} is {distance:.1f} km from the gazetteer's position"
        )

    assert checked >= 10, f"only {checked} cities cross-checked — gazetteer mismatch?"


def test_deniyaya_is_placed_inland() -> None:
    """Guards a correction: an inland town that had been given a coastal latitude."""
    deniyaya = geo.resolve("Deniyaya")
    assert deniyaya is not None
    assert deniyaya[1] > 5.93, "Deniyaya is inland; 5.92 put it on the south coast"
    assert geo.point_in_boundary(deniyaya[1], deniyaya[2])


def test_corridors_aggregate_and_report_fares() -> None:
    corridors = geo.corridors(SCHEDULES)

    colombo_kandy = next(
        (c for c in corridors if c["origin_city"] == "Colombo" and c["destination_city"] == "Kandy"),
        None,
    )
    assert colombo_kandy is not None, "the busiest corridor should exist"
    # Aggregated: many services collapse to one line, counted.
    assert colombo_kandy["service_count"] > 1
    assert {"BUS", "TRAIN"} <= set(colombo_kandy["modes"])
    assert colombo_kandy["min_fare_lkr"] <= colombo_kandy["max_fare_lkr"]
    assert colombo_kandy["providers"]

    # And the two directions are drawn separately, not merged into one line.
    reverse = next(
        (c for c in corridors if c["origin_city"] == "Kandy" and c["destination_city"] == "Colombo"),
        None,
    )
    assert reverse is not None
    assert reverse["service_count"] > 0


# ── /mcp/schedules ───────────────────────────────────────────────────────────


def test_schedules_lists_every_mode_by_default(client: TestClient) -> None:
    data = client.get("/mcp/schedules", params={"limit": 500}).json()["data"]

    modes = {s["mode"] for s in data["services"]}
    assert {"BUS", "TRAIN"} <= modes
    assert data["matched"] == len(SCHEDULES)


def test_schedules_can_be_filtered_to_one_mode(client: TestClient) -> None:
    data = client.get("/mcp/schedules", params={"mode": "TRAIN", "limit": 500}).json()["data"]

    assert data["services"], "the corpus has trains"
    assert {s["mode"] for s in data["services"]} == {"TRAIN"}
    assert all(s["route_id"].startswith("TRAIN") for s in data["services"])


def test_schedules_filter_by_town(client: TestClient) -> None:
    data = client.get("/mcp/schedules", params={"origin": "galle", "limit": 500}).json()["data"]

    assert data["matched"] > 0
    assert all("galle" in s["origin"].lower() for s in data["services"])


def test_schedules_are_ordered_by_departure_time(client: TestClient) -> None:
    data = client.get("/mcp/schedules", params={"limit": 100}).json()["data"]
    times = [s["departure_time"] for s in data["services"]]

    assert times == sorted(times)


def test_schedules_reject_a_nonsense_mode(client: TestClient) -> None:
    assert client.get("/mcp/schedules", params={"mode": "BANANA"}).status_code == 400


def test_schedules_cap_the_page_size(client: TestClient) -> None:
    """
    A corpus of 812 rows returned in full on every keystroke would be slow and
    would bury the point of a timetable.
    """
    data = client.get("/mcp/schedules", params={"limit": 100000}).json()["data"]

    assert len(data["services"]) <= 500
    assert data["truncated"] is True


def test_synthetic_services_are_flagged_in_the_payload(client: TestClient) -> None:
    data = client.get("/mcp/schedules", params={"mode": "TRAIN", "limit": 500}).json()["data"]
    flagged = [s for s in data["services"] if s["synthetic"]]

    assert flagged, "the Kandy-Jaffna demo service should be flagged"
    for service in flagged:
        assert service["synthetic_fields"], "flagged without saying which fields are generated"


# ── /mcp/map_routes ──────────────────────────────────────────────────────────


def test_map_returns_an_outline_nodes_and_corridors(client: TestClient) -> None:
    data = client.get("/mcp/map_routes").json()["data"]

    assert len(data["rings"]) >= 3, "offshore islands are part of the shape"
    assert data["bounds"] and data["boundary_source"]
    assert len(data["outline"]) > 10
    assert data["nodes"] and data["corridors"]
    node = data["nodes"][0]
    assert {"city", "lat", "lng", "service_count"} <= set(node)
    corridor = data["corridors"][0]
    assert {"origin_city", "destination_city", "from", "to", "service_count"} <= set(corridor)


def test_map_states_its_own_coverage(client: TestClient) -> None:
    """
    The map draws city-to-city corridors, so it does not draw everything. It has
    to say so: a map that quietly omits a fifth of the network reads as "that
    route does not exist".
    """
    data = client.get("/mcp/map_routes").json()["data"]
    coverage = data["coverage"]

    assert coverage["services_in_scope"] == len(SCHEDULES)
    assert coverage["unmapped_places"] == []
    assert coverage["drawn_as_corridors"] + coverage["intra_city_not_drawn"] == len(SCHEDULES)
    assert coverage["drawn_as_corridors"] < len(SCHEDULES), (
        "if every service were drawn, the clustering claim would be out of date"
    )
    assert "city-to-city" in coverage["note"]


def test_map_can_be_filtered_to_trains_only(client: TestClient) -> None:
    data = client.get("/mcp/map_routes", params={"mode": "TRAIN"}).json()["data"]

    assert data["mode"] == "TRAIN"
    assert data["corridors"]
    for corridor in data["corridors"]:
        assert corridor["modes"] == ["TRAIN"]


def test_map_rejects_a_nonsense_mode(client: TestClient) -> None:
    assert client.get("/mcp/map_routes", params={"mode": "CARRIER_PIGEON"}).status_code == 400
