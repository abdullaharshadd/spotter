from django.conf import settings
from django.db import DatabaseError, connection
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from routing.serializers import RouteRequestSerializer, RouteResponseSerializer
from routing.services.geocoding import GeocodingError, geocode_location
from routing.services.osrm import RoutingError, get_route
from routing.services.route_planner import (
    compute_fuel_cost,
    find_reference_price,
    plan_fuel_stops,
)


class RouteWithFuelStopsView(APIView):
    """Plan a route between two USA locations and return optimal fuel stops.

    POST body: {"start": "<location>", "finish": "<location>"}

    Makes at most 3 external API calls total:
      - up to 2 Nominatim geocoding calls (start + finish)
      - 1 OSRM routing call

    Fuel stop selection and pricing use the locally pre-geocoded
    FuelStation table, so no further external calls are made.
    """

    def post(self, request):
        req = RouteRequestSerializer(data=request.data)
        req.is_valid(raise_exception=True)

        start_query = req.validated_data["start"]
        finish_query = req.validated_data["finish"]

        try:
            start_coords = geocode_location(start_query)
            finish_coords = geocode_location(finish_query)
        except GeocodingError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        try:
            route = get_route(start_coords, finish_coords)
        except RoutingError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_502_BAD_GATEWAY)

        distance_miles = route["distance_miles"]
        coordinates = route["coordinates"]

        stops = plan_fuel_stops(coordinates, distance_miles)
        reference_station = None
        if not stops:
            reference_station = find_reference_price(coordinates)

        cost = compute_fuel_cost(distance_miles, stops, reference_station)

        payload = {
            "start": {
                "query": start_query,
                "latitude": start_coords[0],
                "longitude": start_coords[1],
            },
            "finish": {
                "query": finish_query,
                "latitude": finish_coords[0],
                "longitude": finish_coords[1],
            },
            "distance_miles": round(distance_miles, 2),
            "duration_hours": round(route["duration_seconds"] / 3600, 2),
            "route_geometry": route["geometry"],
            "fuel_stops": [
                {
                    "name": stop.station.name,
                    "address": stop.station.address,
                    "city": stop.station.city,
                    "state": stop.station.state,
                    "price_per_gallon": stop.station.retail_price,
                    "latitude": stop.station.latitude,
                    "longitude": stop.station.longitude,
                    "mile_marker": round(stop.mile_marker, 1),
                }
                for stop in stops
            ],
            "total_gallons": round(cost["total_gallons"], 2),
            "total_fuel_cost_usd": cost["total_cost"],
            "vehicle_assumptions": {
                "max_range_miles": settings.VEHICLE_MAX_RANGE_MILES,
                "mpg": settings.VEHICLE_MPG,
            },
        }

        serializer = RouteResponseSerializer(payload)
        return Response(serializer.data, status=status.HTTP_200_OK)


class HealthView(APIView):
    """Liveness/readiness probe: 200 when the app and its database respond."""

    def get(self, request):
        try:
            connection.ensure_connection()
        except DatabaseError:
            return Response(
                {"status": "error", "database": "unavailable"},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        return Response({"status": "ok", "database": "ok"})
