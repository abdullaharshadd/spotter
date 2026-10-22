"""Core route + fuel-stop planning logic.

Given a route's polyline (as returned by OSRM) and the vehicle's range/mpg,
walk the route and pick the cheapest fuel station within a corridor around
the route each time the vehicle is about to run low on fuel.
"""

import math
from dataclasses import dataclass

from django.conf import settings

from routing.models import FuelStation

EARTH_RADIUS_MILES = 3958.8


def haversine_miles(p1: tuple[float, float], p2: tuple[float, float]) -> float:
    """Great-circle distance in miles between two (lat, lon) points."""
    lat1, lon1 = map(math.radians, p1)
    lat2, lon2 = map(math.radians, p2)

    dlat = lat2 - lat1
    dlon = lon2 - lon1

    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    )
    c = 2 * math.asin(math.sqrt(a))
    return EARTH_RADIUS_MILES * c


@dataclass
class RoutePoint:
    lat: float
    lon: float
    cumulative_miles: float


@dataclass
class FuelStop:
    station: FuelStation
    mile_marker: float
    distance_from_route_miles: float


def build_route_points(coordinates: list[list[float]]) -> list[RoutePoint]:
    """Convert OSRM [lon, lat] coordinates into RoutePoints with cumulative
    distance along the route."""
    points = []
    cumulative = 0.0
    prev = None
    for lon, lat in coordinates:
        if prev is not None:
            cumulative += haversine_miles((prev[1], prev[0]), (lat, lon))
        points.append(RoutePoint(lat=lat, lon=lon, cumulative_miles=cumulative))
        prev = (lon, lat)
    return points


def _candidate_stations_near(point: RoutePoint, radius_miles: float):
    """Return FuelStations within a bounding box of `radius_miles` around a
    point, using a single indexed DB query (cheap lat/lon range filter)."""
    # 1 degree of latitude is ~69 miles; longitude varies with latitude, so
    # widen the longitude window accordingly.
    lat_delta = radius_miles / 69.0
    lon_delta = radius_miles / (69.0 * max(math.cos(math.radians(point.lat)), 0.1))

    return FuelStation.objects.filter(
        latitude__isnull=False,
        longitude__isnull=False,
        latitude__gte=point.lat - lat_delta,
        latitude__lte=point.lat + lat_delta,
        longitude__gte=point.lon - lon_delta,
        longitude__lte=point.lon + lon_delta,
    )


def _cheapest_station_in_window(
    points: list[RoutePoint],
    window_start_idx: int,
    window_end_idx: int,
    exclude_station_id: int | None = None,
):
    """Find the cheapest fuel station within the corridor around the route
    segment spanning points[window_start_idx:window_end_idx + 1].

    Returns a FuelStop, or None if no station is within the corridor.
    """
    corridor = settings.FUEL_STATION_CORRIDOR_MILES
    best: FuelStop | None = None

    # Sample along the window (every point is overkill for long segments;
    # OSRM geometry points are already fairly dense, so this stays cheap).
    for idx in range(window_start_idx, window_end_idx + 1):
        point = points[idx]
        for station in _candidate_stations_near(point, corridor):
            if station.pk == exclude_station_id:
                continue
            dist = haversine_miles(
                (point.lat, point.lon), (station.latitude, station.longitude)
            )
            if dist > corridor:
                continue
            if best is None or station.retail_price < best.station.retail_price:
                best = FuelStop(
                    station=station,
                    mile_marker=point.cumulative_miles,
                    distance_from_route_miles=dist,
                )

    return best


def plan_fuel_stops(
    coordinates: list[list[float]], total_distance_miles: float
) -> list[FuelStop]:
    """Plan fuel stops along the route.

    Strategy: the vehicle starts with a full tank (range = VEHICLE_MAX_RANGE_MILES).
    Once the remaining range drops to VEHICLE_RANGE_BUFFER_MILES, search the
    corridor between the current position and the max-range point ahead for
    the cheapest station, refuel there (tank becomes full again), and repeat.
    """
    if total_distance_miles <= settings.VEHICLE_MAX_RANGE_MILES:
        return []

    points = build_route_points(coordinates)
    max_range = settings.VEHICLE_MAX_RANGE_MILES
    trigger_at = max_range - settings.VEHICLE_RANGE_BUFFER_MILES

    stops: list[FuelStop] = []
    last_stop_mile = 0.0

    while True:
        remaining_total = total_distance_miles - last_stop_mile
        if remaining_total <= max_range:
            break

        search_start_mile = last_stop_mile + trigger_at
        search_end_mile = last_stop_mile + max_range

        start_idx = _index_for_mile(points, search_start_mile)
        end_idx = _index_for_mile(points, min(search_end_mile, total_distance_miles))

        stop = _cheapest_station_in_window(points, start_idx, end_idx)
        if stop is None:
            # No station found within the corridor in the safe window; widen
            # the search to the full reachable range as a fallback.
            # The window starts at the last fill-up, so skip that station or
            # it is picked again and the loop never advances.
            full_start_idx = _index_for_mile(points, last_stop_mile)
            stop = _cheapest_station_in_window(
                points,
                full_start_idx,
                end_idx,
                exclude_station_id=stops[-1].station.pk if stops else None,
            )

        if stop is None or stop.mile_marker <= last_stop_mile:
            # Nothing reachable further along the corridor; stop planning
            # further stops rather than producing an unreachable plan.
            break

        stops.append(stop)
        last_stop_mile = stop.mile_marker

    return stops


def _index_for_mile(points: list[RoutePoint], mile: float) -> int:
    """Return the index of the route point closest to `mile` along the route."""
    lo, hi = 0, len(points) - 1
    while lo < hi:
        mid = (lo + hi) // 2
        if points[mid].cumulative_miles < mile:
            lo = mid + 1
        else:
            hi = mid
    return lo


def find_reference_price(coordinates: list[list[float]]) -> FuelStation | None:
    """Find the cheapest station near the start of the route, used to price
    trips that are short enough to need no fuel stops."""
    points = build_route_points(coordinates)
    stop = _cheapest_station_in_window(points, 0, 0)
    return stop.station if stop else None


def compute_fuel_cost(
    total_distance_miles: float,
    stops: list[FuelStop],
    reference_station: FuelStation | None = None,
) -> dict:
    """Compute total fuel cost and gallons used.

    Each leg of the trip is priced using the fuel bought at the stop that
    begins it. The first leg (start -> first stop, or the whole trip if no
    stops are needed) is priced using the first stop's price, or
    `reference_station` if there are no stops at all.
    """
    mpg = settings.VEHICLE_MPG
    total_gallons = total_distance_miles / mpg

    if not stops:
        if reference_station is None:
            return {"total_gallons": total_gallons, "total_cost": None}
        return {
            "total_gallons": total_gallons,
            "total_cost": round(
                total_gallons * float(reference_station.retail_price), 2
            ),
        }

    total_cost = 0.0
    leg_start_mile = 0.0
    for i, stop in enumerate(stops):
        leg_miles = stop.mile_marker - leg_start_mile
        # The price for this leg is the price at the fill-up that begins it:
        # the first leg uses the first stop's price (fuel bought there before
        # departing this leg); subsequent legs use the previous stop's price.
        leg_price = float(stops[max(i - 1, 0)].station.retail_price)
        total_cost += (leg_miles / mpg) * leg_price
        leg_start_mile = stop.mile_marker

    # Final leg: from last stop to destination, priced at the last stop's price.
    final_leg_miles = total_distance_miles - leg_start_mile
    total_cost += (final_leg_miles / mpg) * float(stops[-1].station.retail_price)

    return {
        "total_gallons": total_gallons,
        "total_cost": round(total_cost, 2),
    }
