"""Client for the free OSRM public routing API (no API key required)."""

import requests
from django.conf import settings


class RoutingError(Exception):
    """Raised when OSRM cannot produce a route between two points."""


def get_route(start: tuple[float, float], end: tuple[float, float]) -> dict:
    """Fetch a driving route between two (lat, lon) points.

    Makes a single call to OSRM's /route endpoint with full geometry.
    Returns a dict with:
      - distance_miles: total route distance
      - duration_seconds: estimated driving time
      - geometry: GeoJSON LineString geometry of the route
      - coordinates: list of [lon, lat] points along the route
    """
    start_lat, start_lon = start
    end_lat, end_lon = end

    coords = f"{start_lon},{start_lat};{end_lon},{end_lat}"
    url = f"{settings.OSRM_BASE_URL}/route/v1/driving/{coords}"

    try:
        resp = requests.get(
            url,
            params={
                "overview": "full",
                "geometries": "geojson",
            },
            timeout=15,
        )
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise RoutingError(f"Routing service error: {exc}") from exc

    data = resp.json()
    if data.get("code") != "Ok" or not data.get("routes"):
        raise RoutingError(f"Routing service returned no route: {data.get('code')}")

    route = data["routes"][0]
    geometry = route["geometry"]
    meters = route["distance"]

    return {
        "distance_miles": meters / 1609.344,
        "duration_seconds": route["duration"],
        "geometry": geometry,
        "coordinates": geometry["coordinates"],
    }
