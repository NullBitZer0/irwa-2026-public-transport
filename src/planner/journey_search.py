"""
Journey Search — clarification-first route discovery
Member 2 — Planning Agent

Answers the question the traveller is really asking: "how do I get from A to B,
leaving around T?" That needs three things the plain keyword retriever cannot do.

1. **When is the bus at my stop?**
   Fixtures carry a departure and arrival for the whole service but no per-stop
   times, so times along the route are estimated by distributing the journey
   evenly across the stop sequence (see `stop_times`). This is an approximation
   and is flagged as such — it is good enough to rank services, not to be a
   timetable.

2. **Is the service going my way?**
   A long-distance service such as Jaffna → Colombo passes through Negombo on
   its way, and is a perfectly valid answer for "Negombo to Colombo at 10am".
   The direction check therefore looks at stop *order*, not just whether the
   origin appears somewhere on the route.

3. **What do I still need to ask?**
   `missing_details` reports whether the traveller has not said which mode they
   want, or not given a time, so the caller can ask instead of guessing.

Run: see src/planner/server.py::plan_journey
"""

from __future__ import annotations

from typing import Any

from src.planner.hybrid_retriever import _station_key

# SLR is the rail operator, so provider identifies trains vs buses here.
RAIL_PROVIDERS = {"SLR"}

# Only these places are offered as origins/destinations. Keeps the demo focused
# on the corridors people actually ask about, instead of every village halt.
MAJOR_CITIES: frozenset[str] = frozenset(
    {
        "colombo",
        "negombo",
        "kandy",
        "matale",
        "galle",
        "matara",
        "jaffna",
        "anuradhapura",
        "trincomalee",
        "batticaloa",
        "badulla",
        "kunegala",
        "kurunegala",
        "ratnapura",
        "kegalle",
        "hambantota",
        "mannar",
        "ampara",
        "monaragala",
        "nuwaraeliya",
        "takunagaya",
        "dambulla",
    }
)

# How far from the requested time still counts as "close to that time". Long
# distance coaches run every couple of hours, so 60 minutes finds the nearby
# ones without being so wide that everything qualifies.
DEFAULT_WINDOW_MINUTES = 60


def _to_minutes(hhmm: str) -> int | None:
    try:
        hours, minutes = str(hhmm).split(":")
        return int(hours) * 60 + int(minutes)
    except (AttributeError, ValueError):
        return None


def _format(total: int) -> str:
    wrapped = total % (24 * 60)
    return f"{wrapped // 60:02d}:{wrapped % 60:02d}"


def is_major(name: str) -> bool:
    """True when a station is one of the major cities we serve."""
    return _station_key(name) in MAJOR_CITIES


def stop_times(service: dict[str, Any]) -> list[tuple[str, int]]:
    """
    Estimated time at each stop, as (stop, minutes-from-service-departure).

    The journey duration is divided evenly across the number of stop-to-stop
    segments. Real services do not travel uniformly (a trunk highway hop takes
    much longer than a town halt), so these are estimates for ranking only.
    """
    stops = service.get("stops") or [
        service.get("origin", ""),
        service.get("destination", ""),
    ]
    departure = _to_minutes(str(service.get("departure_time", "")))
    arrival = _to_minutes(str(service.get("arrival_time", "")))
    if departure is None or arrival is None or len(stops) < 2:
        return [(s, 0) for s in stops]

    duration = arrival - departure
    if duration < 0:  # overnight service
        duration += 24 * 60

    segments = len(stops) - 1
    return [(stop, round(duration * index / segments)) for index, stop in enumerate(stops)]


def service_at(service: dict[str, Any], station: str) -> int | None:
    """
    Minutes after departure at which the service is at `station`, or None if the
    service does not call there.
    """
    key = _station_key(station)
    for stop, offset in stop_times(service):
        if _station_key(str(stop)) == key:
            return offset
    return None


def stop_index(service: dict[str, Any], station: str) -> int | None:
    """Position of a station in the service's stop sequence."""
    key = _station_key(station)
    stops = service.get("stops") or []
    for index, stop in enumerate(stops):
        if _station_key(str(stop)) == key:
            return index
    return None


def _mode_of(service: dict[str, Any]) -> str:
    return "TRAIN" if service.get("provider") in RAIL_PROVIDERS else "BUS"


def find_services_at(
    services: list[dict[str, Any]],
    origin: str,
    destination: str,
    at_time: str | None = None,
    mode: str = "ANY",
    window_minutes: int = DEFAULT_WINDOW_MINUTES,
    major_cities_only: bool = True,
    limit: int = 6,
) -> list[dict[str, Any]]:
    """
    Finds services the traveller could board at `origin` heading for `destination`.

    Includes long-distance services that merely pass through the origin, flagged
    with `board_type` so the UI can say where the bus actually starts.

    Args:
        services: all trains and buses.
        origin: where the traveller is.
        destination: where they want to get to.
        at_time: requested departure time 'HH:MM'. None means "any time".
        mode: 'TRAIN', 'BUS' or 'ANY'.
        window_minutes: how far from `at_time` still counts as close enough.
        major_cities_only: restrict to the major cities in MAJOR_CITIES.
        limit: maximum services returned.

    Returns:
        Service dicts augmented with `board_type` ('direct' or 'passing'),
        `boards_at`, `boards_at_minutes` (offset from service start) and
        `minutes_from_requested`.
    """
    if not (origin and destination):
        return []

    origin_key, destination_key = _station_key(origin), _station_key(destination)
    if not origin_key or not destination_key or origin_key == destination_key:
        return []

    if major_cities_only and not (is_major(origin) and is_major(destination)):
        return []

    wanted = _to_minutes(at_time) if at_time else None
    matches: list[dict[str, Any]] = []
    # Everything that could serve the journey, kept for the "later" fallback.
    candidates: list[dict[str, Any]] = []

    for service in services:
        if mode in ("TRAIN", "BUS") and _mode_of(service) != mode:
            continue

        board_at = stop_index(service, origin)
        if board_at is None:
            continue

        # Direction: the destination must come after the boarding stop.
        drop_at = stop_index(service, destination)
        if drop_at is None or drop_at <= board_at:
            continue

        departure = _to_minutes(str(service.get("departure_time", "")))
        if departure is None:
            continue
        offset = service_at(service, origin)
        if offset is None:
            continue

        boards_minutes = (departure + offset) % (24 * 60)
        delta = None
        if wanted is not None:
            delta = boards_minutes - wanted
            if delta > 12 * 60:
                delta -= 24 * 60
            elif delta < -12 * 60:
                delta += 24 * 60

        entry = {
                **service,
                "board_type": "direct" if board_at == 0 else "passing",
                "boards_at": _format(boards_minutes),
                "boards_at_minutes": boards_minutes,
                "minutes_from_requested": delta,
                "service_origin": service.get("origin"),
                "service_destination": service.get("destination"),
            }
        candidates.append(entry)

        if delta is not None and abs(delta) > window_minutes:
            continue
        matches.append(entry)

    # Closest to the requested time first; a service that starts here beats one
    # that merely passes through at the same moment.
    matches.sort(
        key=lambda m: (
            abs(m["minutes_from_requested"]) if m["minutes_from_requested"] is not None else 0,
            m["board_type"] != "direct",
        )
    )

    if matches or wanted is None:
        return matches[:limit]

    # Nothing boards within the window. Rather than claim there is no service —
    # which is almost never true on these corridors — offer the next ones after
    # the requested time, clearly marked as later.
    later = sorted(
        (m for m in candidates if (m["minutes_from_requested"] or 0) > 0),
        key=lambda m: m["minutes_from_requested"],
    )
    for match in later[:limit]:
        match["after_requested"] = True
    return later[:limit]


def missing_details(
    origin: str | None,
    destination: str | None,
    mode: str,
    at_time: str | None,
    major_cities_only: bool = True,
) -> list[str]:
    """
    What the traveller still has to tell us before we can answer well.

    Returns a list of 'origin', 'destination', 'major_cities', 'mode' and/or
    'time'. An empty list means we have enough to search.
    """
    missing: list[str] = []

    if not origin:
        missing.append("origin")
    if not destination:
        missing.append("destination")

    if (
        major_cities_only
        and origin
        and destination
        and not (is_major(origin) and is_major(destination))
    ):
        missing.append("major_cities")

    if mode not in ("TRAIN", "BUS"):
        missing.append("mode")
    if not at_time:
        missing.append("time")

    return missing
