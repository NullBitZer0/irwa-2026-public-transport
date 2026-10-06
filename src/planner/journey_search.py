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


def apply_conditions(
    services: list[dict],
    avoid_modes: list[str] | None,
    requested_mode: str = "ANY",
) -> tuple[list[dict], list[dict], str | None]:
    """
    Applies a live-conditions verdict to the candidate services.

    Returns (services, alternatives, note).

    Behaviour is deliberately conservative:

    - A mode named in `avoid_modes` is *demoted*, not deleted. A strike headline
      is a reason to suggest a bus, not a reason to declare the railway closed —
      the planner does not have authority over whether a service is running, and
      the traveller may know better.
    - The demoted services are still returned, after the alternatives and flagged,
      so the traveller is never quietly shown a list with something missing.
    - Only when the traveller asked for no particular mode (ANY) is the demoted
      group moved below the alternatives.

    `avoid_modes` is a list of mode codes from the Conditions Agent, not text, so
    no fetched headline can steer this decision.
    """
    if not avoid_modes:
        return services, [], None

    avoid = {m.upper() for m in avoid_modes if m}
    if not avoid:
        return services, [], None

    requested = (requested_mode or "ANY").upper()

    # The traveller asked for a mode that is not the affected one. Reordering
    # would put services they did not want first and describe them as
    # alternatives, which is noise — the advisory sentence already mentions the
    # other mode. Leave the list alone.
    if requested in ("TRAIN", "BUS") and requested not in avoid:
        return services, [], None

    preferred = [s for s in services if _mode_of(s) not in avoid]
    demoted = [s for s in services if _mode_of(s) in avoid]

    if not preferred:
        # Everything we found is the affected mode. Say exactly that, rather than
        # implying some other kind of service exists on this route.
        found_mode = _mode_of(services[0]) if services else "service"
        mode_word = "rail" if found_mode == "TRAIN" else "bus"
        other_word = "bus" if found_mode == "TRAIN" else "train"
        note = (
            f"Live reports flag {mode_word} services, which is all I have for this "
            f"route. Say the word and I'll look for {other_word} options instead."
        )
        return services, [], note

    for svc in demoted:
        svc["conditions_flag"] = "live reports suggest this service may be disrupted"

    alternative_word = "bus" if "TRAIN" in avoid else "train"
    note = (
        f"Live reports flag the "
        f"{'rail' if 'TRAIN' in avoid else 'bus'} services on this route, so the "
        f"{alternative_word} options below are listed first."
    )
    return preferred + demoted, demoted, note


def _mode_of(service: dict) -> str:
    """TRAIN or BUS for a service dict, from its provider or transit_type."""
    provider = str(service.get("provider") or "").upper()
    if provider == "SLR":
        return "TRAIN"
    transit = str(service.get("transit_type") or "").upper()
    if "TRAIN" in transit:
        return "TRAIN"
    if "BUS" in transit or provider in {"SLTB", "PRIVATE_HIGHWAY", "RM"}:
        return "BUS"
    return "BUS"
