from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from routing.models import FuelStation
from routing.services.geocoding import GeocodingError
from routing.services.osrm import RoutingError
from routing.services.route_planner import build_route_points
from routing.tests.helpers import make_straight_line_coordinates


class RouteWithFuelStopsViewTests(TestCase):
    def setUp(self):
        self.url = reverse("route-with-fuel-stops")
        self.coordinates = make_straight_line_coordinates(-98.0, 39.0, -88.0, 39.0, 200)
        self.points = build_route_points(self.coordinates)
        self.total_distance = self.points[-1].cumulative_miles

        mile_300_point = next(p for p in self.points if p.cumulative_miles >= 300)
        FuelStation.objects.create(
            opis_id=1,
            name="Midway Fuel",
            address="addr",
            city="A",
            state="KS",
            rack_id=1,
            retail_price="3.500",
            latitude=mile_300_point.lat,
            longitude=mile_300_point.lon,
        )

    def _mock_route_response(self):
        return {
            "code": "Ok",
            "routes": [
                {
                    "distance": self.total_distance * 1609.344,
                    "duration": 5 * 3600,
                    "geometry": {"type": "LineString", "coordinates": self.coordinates},
                }
            ],
        }

    @patch("routing.views.get_route")
    @patch("routing.views.geocode_location")
    def test_post_returns_route_and_fuel_stops(self, mock_geocode, mock_get_route):
        mock_geocode.side_effect = [(39.0, -98.0), (39.0, -88.0)]
        mock_get_route.return_value = {
            "distance_miles": self.total_distance,
            "duration_seconds": 5 * 3600,
            "geometry": {"type": "LineString", "coordinates": self.coordinates},
            "coordinates": self.coordinates,
        }

        response = self.client.post(
            self.url,
            data={"start": "Start, KS", "finish": "Finish, KS"},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("route_geometry", data)
        self.assertEqual(len(data["fuel_stops"]), 1)
        self.assertEqual(data["fuel_stops"][0]["name"], "Midway Fuel")
        self.assertIsNotNone(data["total_fuel_cost_usd"])
        self.assertGreater(data["total_gallons"], 0)

    @patch("routing.views.geocode_location")
    def test_post_returns_400_for_unresolvable_location(self, mock_geocode):
        mock_geocode.side_effect = GeocodingError("not found")

        response = self.client.post(
            self.url,
            data={"start": "Nowhereville", "finish": "Finish, KS"},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)

    def test_post_requires_start_and_finish(self):
        response = self.client.post(self.url, data={}, content_type="application/json")
        self.assertEqual(response.status_code, 400)

    @patch("routing.views.get_route")
    @patch("routing.views.geocode_location")
    def test_post_returns_502_when_routing_service_fails(
        self, mock_geocode, mock_get_route
    ):
        mock_geocode.side_effect = [(39.0, -98.0), (39.0, -88.0)]
        mock_get_route.side_effect = RoutingError("Routing service error: timeout")

        response = self.client.post(
            self.url,
            data={"start": "Start, KS", "finish": "Finish, KS"},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 502)
        self.assertIn("Routing service error", response.json()["error"])

    @patch("routing.views.get_route")
    @patch("routing.views.geocode_location")
    def test_short_route_is_priced_from_station_near_start(
        self, mock_geocode, mock_get_route
    ):
        FuelStation.objects.create(
            opis_id=9,
            name="Start Fuel",
            address="addr",
            city="S",
            state="KS",
            rack_id=1,
            retail_price="4.000",
            latitude=39.0,
            longitude=-98.0,
        )
        short_coords = make_straight_line_coordinates(-98.0, 39.0, -97.0, 39.0, 10)
        mock_geocode.side_effect = [(39.0, -98.0), (39.0, -97.0)]
        mock_get_route.return_value = {
            "distance_miles": 100.0,
            "duration_seconds": 3600,
            "geometry": {"type": "LineString", "coordinates": short_coords},
            "coordinates": short_coords,
        }

        response = self.client.post(
            self.url,
            data={"start": "Start, KS", "finish": "Near, KS"},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["fuel_stops"], [])
        # 100 mi / 10 mpg = 10 gal at $4.00.
        self.assertEqual(data["total_fuel_cost_usd"], 40.0)
