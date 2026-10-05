"""
Mixed-Mode Connection Planner
Member 2 — Planning Agent

When no direct train or bus runs between two stations, this module finds
one-stop connections — for example Kandy → Colombo Fort by train, then a coach
on to Jaffna — and deliberately prefers journeys that MIX modes, since a change
at a hub is usually what the traveller was trying to avoid asking about.

Time feasibility is only ever evaluated at service endpoints. The fixtures
carry a departure and arrival time for a whole service but no per-stop times, so
restricting interchange to endpoints means a connection is only ever proposed
when the timetable genuinely supports it. Nothing here has to invent a time.

Services used as a leg must therefore run origin → hub and hub → destination as
published, which is why adding the missing reverse-direction services to the
fixtures directly increases the number of connections found.
"""

from __future__ import annotations

from typing import Any

from src.planner.hybrid_retriever import _station_key

RAIL_PROVIDERS = {"SLR"}

# Long enough to alight, cross the platform and board, short enough to stay useful.
# 20 minutes suits a change between an adjacent rail station and bus stand (for
# example Colombo Fort and Bastian Mawatha); same-station rail-to-rail changes
# usually need longer, so raise this per call site if stricter transfers are wanted.
DEFAULT_MIN_TRANSFER_MINUTES = 20

# A changeover longer than this is not a journey plan, it is a missed connection:
# the traveller would be stranded at the hub for a day.
DEFAULT_MAX_TRANSFER_MINUTES = 360


def _to_minutes(hhmm: str) -> int | None:
    """'07:30' → 450. Returns None for anything unparseable."""
    try:
        hours, minutes = str(hhmm).split(":")
        return int(hours) * 60 + int(minutes)
    except (AttributeError, ValueError):
        return None


def _format_minutes(total: int) -> str:
    """Formats minutes-from-midnight back to 'HH:MM', wrapping past 24h."""
    wrapped = total % (24 * 60)
    return f"{wrapped // 60:02d}:{wrapped % 60:02d}"


def _is_train(service: dict[str, Any]) -> bool:
    return service.get("provider") in RAIL_PROVIDERS


def _mode_of(service: dict[str, Any]) -> str:
    return "TRAIN" if _is_train(service) else "BUS"


def _matches_mode(service: dict[str, Any], mode: str) -> bool:
    if mode not in ("TRAIN", "BUS"):
        return True
    return _mode_of(service) == mode


def _earliest_feasible_departure(
    first_arrival: int,
    second_departure: int,
    min_transfer_minutes: int,
) -> int | None:
    """
    The second leg's departure, allowing it to run the next day.

    Overnight services (a 19:30 departure arriving 03:50) mean the connecting
    departure may be on either side of midnight, so both are tried. The window
    is capped at 24h so a "connection" is never really an unrelated later service.
    """
    ready_at = first_arrival + min_transfer_minutes
    for candidate in (second_departure, second_departure + 24 * 60):
        if ready_at <= candidate <= ready_at + 24 * 60:
            return candidate
    return None


def _build_leg(service: dict[str, Any]) -> dict[str, Any]:
    return {
        "route_id": service.get("route_id"),
        "service_name": service.get("service_name"),
        "provider": service.get("provider"),
        "mode": _mode_of(service),
        "origin": service.get("origin"),
        "destination": service.get("destination"),
        "departure_time": service.get("departure_time"),
        "arrival_time": service.get("arrival_time"),
        "base_fare_lkr": service.get("base_fare_lkr", 0.0),
        "transit_type": service.get("transit_type"),
        "stops": service.get("stops", []),
    }


def rank_connections(connections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Orders connections: mixed mode first, then same-day changes, then earliest
    arrival, then shortest total journey.

    Shared with the endpoint so that connections gathered from more than one
    search (a single-mode search plus mixed-mode alternatives) end up in one
    consistent order. Safe to call more than once on the same list: the internal
    sort key is left in place here and stripped by `public_connections`.
    """
    return sorted(
        connections,
        key=lambda c: (
            not c["mixed_mode"],
            c["overnight_change"],
            c["_arrive_offset"],
            c["duration_minutes"],
        ),
    )


def public_connections(connections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Strips internal ranking keys before connections leave the planner."""
    return [
        {k: v for k, v in connection.items() if not k.startswith("_")}
        for connection in connections
    ]


def find_connections(
    services: list[dict[str, Any]],
    origin: str,
    destination: str,
    mode: str = "ANY",
    min_transfer_minutes: int = DEFAULT_MIN_TRANSFER_MINUTES,
    max_transfer_minutes: int = DEFAULT_MAX_TRANSFER_MINUTES,
    max_results: int = 3,
) -> list[dict[str, Any]]:
    """
    Finds one-stop connections between two stations.

    Args:
        services: every train and bus service available.
        origin: requested starting station.
        destination: requested final station.
        mode: 'TRAIN', 'BUS' or 'ANY'. A specific mode restricts BOTH legs.
        min_transfer_minutes: minimum changeover time at the hub.
        max_transfer_minutes: reject changeovers longer than this.
        max_results: how many connections to return.

    Returns:
        Connection dicts shaped like route options so the UI can render them
        uniformly, each carrying its `legs`, `transfer_station` and `is_connection`.
        Mixed-mode journeys are ranked ahead of same-mode ones.
    """
    if not (origin and destination):
        return []

    origin_key = _station_key(origin)
    destination_key = _station_key(destination)
    if not origin_key or not destination_key or origin_key == destination_key:
        return []

    # Leg 1 must be boardable at the origin, leg 2 must terminate at the destination.
    outbound = [
        s
        for s in services
        if _station_key(str(s.get("origin", ""))) == origin_key
        and _station_key(str(s.get("destination", ""))) not in (origin_key, destination_key)
        and _matches_mode(s, mode)
    ]
    inbound = [
        s
        for s in services
        if _station_key(str(s.get("destination", ""))) == destination_key
        and _station_key(str(s.get("origin", ""))) not in (origin_key, destination_key)
        and _matches_mode(s, mode)
    ]

    connections: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    for first in outbound:
        hub_key = _station_key(str(first.get("destination", "")))
        first_dep = _to_minutes(str(first.get("departure_time", "")))
        first_arr = _to_minutes(str(first.get("arrival_time", "")))
        if first_dep is None or first_arr is None:
            continue
        if first_arr < first_dep:  # overnight service
            first_arr += 24 * 60

        for second in inbound:
            if _station_key(str(second.get("origin", ""))) != hub_key:
                continue

            second_dep_raw = _to_minutes(str(second.get("departure_time", "")))
            second_arr_raw = _to_minutes(str(second.get("arrival_time", "")))
            if second_dep_raw is None or second_arr_raw is None:
                continue

            second_dep = _earliest_feasible_departure(
                first_arr, second_dep_raw, min_transfer_minutes
            )
            if second_dep is None:
                continue

            transfer_minutes = second_dep - first_arr
            # A changeover longer than the same-day limit is still a real
            # connection, but it means spending a night at the hub — say so
            # rather than passing it off as a same-day journey.
            overnight_change = transfer_minutes > max_transfer_minutes

            second_arr = second_arr_raw + (second_dep - second_dep_raw)
            if second_arr < second_dep:  # overnight service on the second leg
                second_arr += 24 * 60
            runs_next_day = second_dep >= 24 * 60 or second_arr >= 24 * 60

            key = (str(first.get("route_id")), str(second.get("route_id")))
            if key in seen:
                continue
            seen.add(key)

            leg1, leg2 = _build_leg(first), _build_leg(second)
            leg2["departure_time"] = _format_minutes(second_dep)
            leg2["arrival_time"] = _format_minutes(second_arr)
            leg2["next_day"] = runs_next_day
            mixed = leg1["mode"] != leg2["mode"]
            modes = {leg1["mode"], leg2["mode"]}

            # The hub may be two adjacent facilities rather than one station —
            # Colombo Fort railway station and Colombo Bastian Mawatha bus
            # stand, for example. Name both so the traveller knows to walk.
            first_hub = str(first.get("destination", ""))
            second_hub = str(second.get("origin", ""))
            adjacent_hub = _station_key(first_hub) == _station_key(second_hub) and (
                first_hub != second_hub
            )
            transfer_label = f"{first_hub} → {second_hub}" if adjacent_hub else first_hub

            connections.append(
                {
                    "route_id": f"CONN-{leg1['route_id']}-{leg2['route_id']}",
                    "service_name": f"{leg1['service_name']} + {leg2['service_name']}",
                    "provider": "CONNECTION",
                    "origin": leg1["origin"],
                    "destination": leg2["destination"],
                    "departure_time": leg1["departure_time"],
                    "arrival_time": _format_minutes(second_arr),
                    "base_fare_lkr": round(
                        float(leg1["base_fare_lkr"] or 0) + float(leg2["base_fare_lkr"] or 0), 2
                    ),
                    "transit_type": (
                        "MIXED_MODE_CONNECTION" if mixed else f"{leg1['mode']}_CONNECTION"
                    ),
                    "legs": [leg1, leg2],
                    "transfer_station": transfer_label,
                    "transfer_minutes": transfer_minutes,
                    "overnight_change": overnight_change,
                    "adjacent_hub": adjacent_hub,
                    "is_connection": True,
                    # A connection is two separate tickets, so it is not bookable
                    # as a single seat hold.
                    "bookable": False,
                    "mixed_mode": mixed,
                    "modes": sorted(modes),
                    "duration_minutes": second_arr - first_dep,
                    # Internal sort key: arrival as minutes from the first
                    # departure, so 02:30 the next morning sorts after 14:30.
                    "_arrive_offset": second_arr - first_dep,
                }
            )

# Mixed-mode first (as requested), then same-day connections, then earliest
    # arrival, then shortest total journey.
    return rank_connections(connections)[:max_results]
