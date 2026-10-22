"""Geocoding of free-text locations (e.g. "Chicago, IL") to coordinates.

Uses the free Nominatim (OpenStreetMap) search API. This is only called for
the user-supplied start/finish locations (at most two lookups per request),
not for the fuel-station dataset, which is pre-geocoded at load time.
"""

import logging

import requests
from django.conf import settings

logger = logging.getLogger(__name__)


class GeocodingError(Exception):
    """Raised when a location string cannot be resolved to coordinates."""


def geocode_location(query: str) -> tuple[float, float]:
    """Resolve a free-text location to (latitude, longitude).

    Raises GeocodingError if the location cannot be found.
    """
    try:
        resp = requests.get(
            f"{settings.NOMINATIM_BASE_URL}/search",
            params={
                "q": query,
                "countrycodes": "us",
                "format": "json",
                "limit": 1,
            },
            headers={"User-Agent": "spotter-fuel-route-app/1.0"},
            timeout=10,
        )
        resp.raise_for_status()
    except requests.RequestException as exc:
        status = getattr(exc.response, "status_code", None)
        logger.warning(
            "geocoding request failed query=%r status=%s error=%s", query, status, exc
        )
        raise GeocodingError(f"Geocoding service error for '{query}': {exc}") from exc

    results = resp.json()
    if not results:
        logger.info("geocoding returned no results query=%r", query)
        raise GeocodingError(f"Could not find a location matching '{query}'.")

    try:
        return float(results[0]["lat"]), float(results[0]["lon"])
    except (KeyError, ValueError, IndexError) as exc:
        logger.error("unexpected geocoding response query=%r body=%r", query, results)
        raise GeocodingError(f"Unexpected geocoding response for '{query}'.") from exc
