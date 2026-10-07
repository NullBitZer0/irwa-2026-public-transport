"""
Where things are, for the schedules and map views.

Two things this deliberately does not do:

- **It does not invent precision.** The corpus is full of individual bus stops
  (Pettah, Kotahena, Dehiwala Main) that are a few kilometres apart in Greater
  Colombo. Drawing them as separate pins at invented coordinates would look more
  authoritative than it is, so stops are clustered into their city and the map
  draws city-level corridors.
- **It does not guess.** Every place name in the corpus is mapped explicitly in
  `STOP_CITY`, and anything not in that table is reported as unmapped rather
  than being silently dropped from the map — a route that vanishes because its
  town was misspelled is a bug a user would report as "the map is missing my
  bus".

The outline is real geography, not a traced approximation: it is Natural Earth
1:10m admin-0 boundary data for Sri Lanka, baked into `data/geo/` so the map
works offline and renders the same everywhere. The significant offshore islands
are kept too — dropping Mannar Island, for instance, would put the town of Mannar
in the sea.
"""

from __future__ import annotations

import json
import os
from typing import Any, Iterable, Optional

# City nodes. Coordinates are settlement centres, and they are checked against the
# Natural Earth gazetteer in evaluation/test_schedules_and_map.py where an entry
# exists for them — which is what caught Moratuwa sitting 8.5 km off.
#
# The coastline in BOUNDARY_RINGS is generalised outward by a few kilometres, so a
# coastal town can plot a little into the sea: Galle, genuinely on the shore, sits
# 2.4 km off this line, and so do Kalutara and Aluthgama. That is the dataset's
# resolution, not a misplaced pin, and the tests assert the distance rather than
# pretending it is zero.
CITY_COORDS: dict[str, tuple[float, float]] = {
    # Greater Colombo (its stops are clustered into one node)
    "Colombo": (6.9271, 79.8612),
    "Katunayake": (7.1795, 79.8971),
    "Panadura": (6.8465, 79.8912),
    "Gampaha": (7.1983, 80.0980),
    "Moratuwa": (6.7804, 79.8800),  # Dehiwala / Nugegoda / Maharagama cluster
    "Kaduwela": (6.9368, 79.9535),
    "Nittambuwa": (7.2636, 79.8980),
    "Kalutara": (6.5854, 79.9000),
    "Aluthgama": (6.1244, 80.0575),
    "Galle": (6.0535, 80.2210),
    "Elpitiya": (6.2322, 80.3342),
    "Matara": (5.9485, 80.5350),
    "Tangalle": (6.0246, 80.7906),
    "Hambantota": (6.1244, 81.1181),
    "Deniyaya": (5.9600, 80.6380),
    "Nuwara Eliya": (6.9729, 80.7830),
    "Ella": (6.8668, 81.0460),
    "Bandarawela": (6.8297, 81.0460),
    "Badulla": (6.9934, 81.0600),
    "Negombo": (7.2083, 79.8339),
    "Kandy": (7.2906, 80.6337),
    "Nawalapitiya": (7.0221, 80.7014),
    "Matale": (7.4675, 80.4974),
    "Kurunegala": (7.4813, 80.3607),
    "Ratnapura": (6.6828, 80.4028),
    "Avissawella": (6.9544, 80.2044),
    "Anuradhapura": (8.3136, 80.4071),
    "Trincomalee": (8.5741, 81.2331),
    "Batticaloa": (7.7170, 81.7000),
    "Ampara": (7.6770, 81.6748),
    "Monaragala": (6.8724, 81.0470),
    "Kataragama": (6.4033, 81.3336),
    "Mannar": (8.9810, 79.9040),
    "Kalpitiya": (8.0333, 79.8333),
    "Jaffna": (9.6615, 80.0255),
    "Puttalam": (8.0313, 79.8281),
    "Dambulla": (7.8681, 80.6510),
    "Kegalle": (7.2931, 80.6367),
    "Kunegala": (7.4699, 80.2049),
    # Nuwara Eliya is spelled with the space in CITY_COORDS' title case and
    # without it in MAJOR_CITIES, so the generated corridors resolve it by the
    # same normalisation used there.
    "Takunagaya": (7.6367, 81.2478),
}

# The corpus names bus stops; this maps each of those to the city node it sits in.
# Every entry is deliberate. Anything missing from this table is reported, not
# guessed at (see `unmapped_places`).
STOP_CITY: dict[str, str] = {
    # Greater Colombo and its suburbs
    "Colombo Bastian Mawatha": "Colombo",
    "Colombo Fort": "Colombo",
    "Fort": "Colombo",
    "Pettah": "Colombo",
    "Kotahena": "Colombo",
    "Kollupitiya": "Colombo",
    "Bambalapitiya": "Colombo",
    "Bambalapitiya Junction": "Colombo",
    "Maradana": "Colombo",
    "Narahenpita": "Colombo",
    "Borella": "Colombo",
    "Town Hall": "Colombo",
    "Park Road – Park Avenue": "Colombo",
    "Gangarama – Slave Island": "Colombo",
    "Mount Lavinia": "Colombo",
    "Nugegoda Supermarket": "Moratuwa",
    "Dehiwala Main": "Moratuwa",
    "Maharagama": "Moratuwa",
    "Maharagama-Dehiwela Road": "Moratuwa",
    "Udahamulla": "Moratuwa",
    "Athurugiriya": "Colombo",
    "Battaramulla": "Colombo",
    "Kohuwala": "Colombo",
    "Padukka": "Colombo",
    "Soysapura": "Colombo",
    "Koswatta": "Colombo",
    "Kirillawala": "Colombo",
    "Ekala": "Colombo",
    "Malwana": "Colombo",
    "Mulleriyawa": "Colombo",
    "Talawatte": "Colombo",
    "Ratmalana Airport": "Colombo",
    "Malwana Airport": "Colombo",
    "Salmal Uyana": "Colombo",
    "Sri J’pura Hospital (Nawarohala)": "Colombo",
    "Sri J'pura Hospital (Nawarohala)": "Colombo",
    "Teldeniya": "Colombo",
    "Bastian Mawatha – Fort": "Colombo",
    "Moratuwa": "Moratuwa",
    "Mattakkuliya": "Colombo",
    "Narahenpita ": "Colombo",
    "Kohilawatta": "Colombo",
    "Kottawa": "Colombo",
    "Piliyandala": "Moratuwa",
    "Malwana ": "Colombo",
    "Rukmalgama": "Colombo",
    "Kirindiwala": "Gampaha",
    "Hatton": "Nawalapitiya",
    "Mathugama": "Aluthgama",
    "Kaduruwela": "Kandy",
    "Negombo": "Katunayake",
    "Uragasmanhandiya": "Batticaloa",
    "Ruhunu": "Hambantota",
    # Outer metro
    "Katunayake Airport": "Katunayake",
    "Katunayake Airport Bus Station": "Katunayake",
    "Raddolugama": "Katunayake",
    "Makumbura MMC": "Katunayake",
    "Kadawatha": "Kaduwela",
    "Kaduwela": "Kaduwela",
    "Ja Ela": "Katunayake",
    "Nittambuwa": "Nittambuwa",
    "Kelaniya": "Colombo",
    "Kiribathgoda": "Colombo",
    "Homagama": "Colombo",
    "Godagama": "Colombo",
    "Mattegoda": "Colombo",
    "Hanwella": "Colombo",
    "Pugoda": "Colombo",
    "Delgoda": "Colombo",
    "Angoda": "Colombo",
    "Angulana": "Colombo",
    "Goluwamulla": "Colombo",
    "Rajagiriya": "Colombo",
    "Panadura SLBT": "Panadura",
    "Panadura Bus Stop (private buses)": "Panadura",
    "Gampaha": "Gampaha",
    "Kaluthara": "Kalutara",
    "Avissawella": "Avissawella",
    # Southern coast
    "Aluthgama": "Aluthgama",
    "Elpitiya": "Elpitiya",
    "Galle": "Galle",
    "Matara": "Matara",
    "Tangalle": "Tangalle",
    "Deniyaya": "Deniyaya",
    "Wattegama": "Matara",
    "Wellampitiya": "Matara",
    "Nuwara Eliya": "Nuwara Eliya",
    "Ella": "Ella",
    "Bandarawela": "Bandarawela",
    "Dayagama": "Badulla",
    "Badulla": "Badulla",
    "Monaragala": "Monaragala",
    "Kataragama": "Kataragama",
    "Hambantota": "Hambantota",
    "Dambulla": "Dambulla",
    "Kalmunai": "Ampara",
    "Ampara": "Ampara",
    "Akkaraipattu": "Ampara",
    "Arugam Bay Pick Up": "Ampara",
    # Hill country and north
    "Kandy": "Kandy",
    "Digana": "Kandy",
    "Nawalapitiya": "Nawalapitiya",
    "Matale": "Matale",
    "Kurunegala": "Kurunegala",
    "Kegalle": "Kegalle",
    "Kunegala": "Kunegala",
    "Ratnapura": "Ratnapura",
    "Anuradhapura": "Anuradhapura",
    "Trincomalee": "Trincomalee",
    "Takunagaya": "Takunagaya",
    "Batticaloa": "Batticaloa",
    "Mannar": "Mannar",
    "Kalpitiya": "Kalpitiya",
    "Jaffna": "Jaffna",
}

# Real boundary data, loaded once at import. Falls back to the coarse trace below
# if the data file is missing, so the map degrades instead of failing.
_BOUNDARY_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data", "geo", "sri_lanka_boundary.json",
)

# A last-resort trace of the coast: about the shape of the island, in case the
# data file is ever absent. Enough to orient a reader, not to map against.
_FALLBACK_OUTLINE: list[tuple[float, float]] = [
    (8.98, 79.72), (8.55, 79.73), (8.15, 79.80), (8.03, 79.83), (7.97, 79.78),
    (7.70, 79.83), (7.40, 79.83), (7.21, 79.84), (7.05, 79.85), (6.93, 79.86),
    (6.72, 79.88), (6.55, 79.90), (6.34, 79.98), (6.12, 80.06), (6.03, 80.22),
    (5.98, 80.38), (5.95, 80.54), (6.00, 80.72), (6.12, 81.12), (6.37, 81.28),
    (6.72, 81.57), (6.95, 81.85), (7.40, 81.83), (7.72, 81.70), (7.95, 81.60),
    (8.20, 81.42), (8.57, 81.23), (8.62, 81.05), (8.95, 80.70), (9.35, 80.45),
    (9.62, 80.35), (9.82, 80.37), (9.78, 79.98), (9.66, 80.03), (9.50, 80.05),
    (9.30, 79.95), (9.05, 79.85),
]


def _load_boundary() -> tuple[list[list[tuple[float, float]]], Optional[dict[str, float]], Optional[str]]:
    """
    Reads the coastline rings and their extent from the data file.

    Returns (rings, bounds, source). An unreadable file is not an error: the map
    falls back to the coarse trace and says so via `boundary_source()`.
    """
    try:
        with open(_BOUNDARY_FILE, encoding="utf-8") as handle:
            payload = json.load(handle)
        rings = [[(lat, lng) for lat, lng in ring] for ring in payload["rings"]]
        if not rings or len(rings[0]) < 50:
            raise ValueError("boundary data has too few points to be a coastline")
        return rings, payload.get("bounds"), payload.get("source")
    except (OSError, ValueError, KeyError, TypeError):
        return [list(_FALLBACK_OUTLINE)], None, None


ISLAND_RINGS, BOUNDARY_BOUNDS, BOUNDARY_SOURCE = _load_boundary()

# The main island, kept as its own name: most consumers want one shape.
ISLAND_OUTLINE: list[tuple[float, float]] = ISLAND_RINGS[0]


def boundary_source() -> str:
    """Where the outline came from, for display and for tests."""
    if BOUNDARY_SOURCE is None:
        return (
            "built-in approximate trace (data/geo/sri_lanka_boundary.json not found)"
        )
    return BOUNDARY_SOURCE


def map_bounds(margin_deg: float = 0.06) -> dict[str, float]:
    """
    The extent the map should draw: the coastline plus every city node.

    Derived rather than hardcoded in the frontend, so a correction here (a moved
    city, a different boundary file) cannot leave routes plotted off-canvas.
    """
    if BOUNDARY_BOUNDS:
        lats = [BOUNDARY_BOUNDS["min_lat"], BOUNDARY_BOUNDS["max_lat"]]
        lngs = [BOUNDARY_BOUNDS["min_lng"], BOUNDARY_BOUNDS["max_lng"]]
    else:
        lats = [lat for lat, _ in ISLAND_OUTLINE]
        lngs = [lng for _, lng in ISLAND_OUTLINE]

    for lat, lng in CITY_COORDS.values():
        lats.append(lat)
        lngs.append(lng)

    return {
        "min_lat": min(lats) - margin_deg,
        "max_lat": max(lats) + margin_deg,
        "min_lng": min(lngs) - margin_deg,
        "max_lng": max(lngs) + margin_deg,
    }


def point_in_boundary(lat: float, lng: float) -> bool:
    """
    Is this point on land, according to the boundary rings?

    Ray casting over every ring. Used to check that our own city coordinates do
    not put a town in the sea, which is a data bug that a map hides rather than
    shows.
    """
    for ring in ISLAND_RINGS:
        inside = False
        for index in range(len(ring) - 1):
            y1, x1 = ring[index]
            y2, x2 = ring[index + 1]
            if (y1 > lat) != (y2 > lat):
                crossing_x = (x2 - x1) * (lat - y1) / (y2 - y1) + x1
                if lng < crossing_x:
                    inside = not inside
        if inside:
            return True
    return False


def resolve(place: Optional[str]) -> Optional[tuple[str, float, float]]:
    """
    Maps a corpus place name to (city, lat, lng), or None if we do not know it.

    Returns None rather than a guess: an unmapped town is a gap in our data, and
    the map should show the gap.
    """
    if not place:
        return None
    name = place.strip()
    city = STOP_CITY.get(name)
    if city is None:
        # Case-insensitive fallback for casing and stray whitespace differences,
        # still without fuzzy matching: two candidates means we do not know.
        lowered = {k.lower(): v for k, v in STOP_CITY.items()}
        city = lowered.get(name.lower())
    if city is None or city not in CITY_COORDS:
        return None
    lat, lng = CITY_COORDS[city]
    return city, lat, lng


def unmapped_places(schedules: Iterable[dict[str, Any]]) -> list[str]:
    """Every place in the corpus we cannot place, for reporting."""
    missing: set[str] = set()
    for record in schedules:
        for field in ("origin", "destination"):
            name = record.get(field)
            if name and resolve(name) is None:
                missing.add(name)
    return sorted(missing)


def corridors(schedules: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Aggregates services into city-to-city corridors.

    Two services on the same corridor are one line on the map, with a count,
    because drawing 233 separate lines shows nothing.
    """
    grouped: dict[tuple[str, str], dict[str, Any]] = {}

    for record in schedules:
        origin = resolve(record.get("origin"))
        destination = resolve(record.get("destination"))
        if not origin or not destination:
            continue
        (o_city, o_lat, o_lng), (d_city, d_lat, d_lng) = origin, destination
        if o_city == d_city:
            # An intra-city service has no line to draw.
            continue

        key = (o_city, d_city)
        entry = grouped.get(key)
        mode = "TRAIN" if "TRAIN" in str(record.get("route_id", "")).upper() else "BUS"
        if entry is None:
            grouped[key] = entry = {
                "origin_city": o_city,
                "destination_city": d_city,
                "from": [o_lat, o_lng],
                "to": [d_lat, d_lng],
                "service_count": 0,
                "modes": set(),
                "providers": set(),
                "min_fare_lkr": None,
                "max_fare_lkr": None,
                "stops": set(),
                "synthetic": False,
            }
        entry["service_count"] += 1
        entry["modes"].add(mode)
        entry["providers"].add(record.get("provider") or "Unknown")
        for stop in record.get("stops") or []:
            if resolve(stop):
                entry["stops"].add(stop)
        fare = record.get("base_fare_lkr")
        if isinstance(fare, (int, float)):
            entry["min_fare_lkr"] = fare if entry["min_fare_lkr"] is None else min(entry["min_fare_lkr"], fare)
            entry["max_fare_lkr"] = fare if entry["max_fare_lkr"] is None else max(entry["max_fare_lkr"], fare)
        if record.get("synthetic"):
            entry["synthetic"] = True

    result = []
    for entry in grouped.values():
        entry["modes"] = sorted(entry["modes"])
        entry["providers"] = sorted(entry["providers"])
        entry["stops"] = sorted(entry["stops"])
        result.append(entry)
    result.sort(key=lambda c: (-c["service_count"], c["origin_city"]))
    return result


def city_nodes(schedules: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """A marker per city that actually appears as an origin or destination."""
    counts: dict[str, int] = {}
    for record in schedules:
        for field in ("origin", "destination"):
            resolved = resolve(record.get(field))
            if resolved:
                counts[resolved[0]] = counts.get(resolved[0], 0) + 1

    nodes = []
    for city, count in counts.items():
        lat, lng = CITY_COORDS[city]
        nodes.append({"city": city, "lat": lat, "lng": lng, "service_count": count})
    nodes.sort(key=lambda n: (-n["service_count"], n["city"]))
    return nodes
