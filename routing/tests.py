from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from routing.models import FuelStation
from routing.services.route_planner import (
    build_route_points,
    compute_fuel_cost,
    haversine_miles,
    plan_fuel_stops,
)


def make_straight_line_coordinates(start_lon, start_lat, end_lon, end_lat, num_points):
    """Generate evenly spaced [lon, lat] points along a straight line, used to
    build synthetic OSRM-style route geometry for tests."""
    coords = []
    for i in range(num_points):
        t = i / (num_points - 1)
        lon = start_lon + (end_lon - start_lon) * t
        lat = start_lat + (end_lat - start_lat) * t
        coords.append([lon, lat])
    return coords


class HaversineTests(TestCase):
    def test_known_distance_chicago_to_st_louis(self):
        chicago = (41.8781, -87.6298)
        st_louis = (38.6270, -90.1994)
        miles = haversine_miles(chicago, st_louis)
        # Straight-line distance is ~257 miles.
        self.assertAlmostEqual(miles, 257, delta=10)


class RoutePlannerTests(TestCase):
    def setUp(self):
        # A route running due east along latitude 39.0 from lon -98 to -88,
        # roughly 533 miles long (10 degrees longitude at this latitude).
        self.coordinates = make_straight_line_coordinates(-98.0, 39.0, -88.0, 39.0, 200)
        self.points = build_route_points(self.coordinates)
        self.total_distance = self.points[-1].cumulative_miles

    def test_short_route_needs_no_stops(self):
        short_coords = make_straight_line_coordinates(-98.0, 39.0, -97.0, 39.0, 10)
        points = build_route_points(short_coords)
        stops = plan_fuel_stops(short_coords, points[-1].cumulative_miles)
        self.assertEqual(stops, [])

    def test_long_route_picks_cheapest_station_in_corridor(self):
        # Two stations within range of the trigger window; the cheaper one
        # (on-route) should be selected over the more expensive one.
        mile_300_point = next(p for p in self.points if p.cumulative_miles >= 300)

        FuelStation.objects.create(
            opis_id=1,
            name="Expensive Stop",
            address="addr",
            city="A",
            state="KS",
            rack_id=1,
            retail_price="5.000",
            latitude=mile_300_point.lat,
            longitude=mile_300_point.lon,
        )
        FuelStation.objects.create(
            opis_id=2,
            name="Cheap Stop",
            address="addr",
            city="B",
            state="KS",
            rack_id=1,
            retail_price="3.000",
            latitude=mile_300_point.lat,
            longitude=mile_300_point.lon + 0.01,
        )

        stops = plan_fuel_stops(self.coordinates, self.total_distance)

        self.assertEqual(len(stops), 1)
        self.assertEqual(stops[0].station.name, "Cheap Stop")

    def test_compute_fuel_cost_prices_first_leg_at_first_stop(self):
        # Total distance 100 miles, mpg = 10 -> 10 gallons.
        from routing.services.route_planner import FuelStop

        station = FuelStation.objects.create(
            opis_id=3,
            name="Stop",
            address="addr",
            city="C",
            state="KS",
            rack_id=1,
            retail_price="2.500",
            latitude=39.0,
            longitude=-95.0,
        )
        stop = FuelStop(
            station=station, mile_marker=50.0, distance_from_route_miles=0.0
        )

        result = compute_fuel_cost(100.0, [stop])

        # First leg (0-50mi) and second leg (50-100mi) both priced at 2.50/gal.
        self.assertEqual(result["total_gallons"], 10.0)
        self.assertEqual(result["total_cost"], 25.0)

    def test_compute_fuel_cost_with_no_stops_and_no_reference(self):
        result = compute_fuel_cost(100.0, [], reference_station=None)
        self.assertEqual(result["total_gallons"], 10.0)
        self.assertIsNone(result["total_cost"])


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
        from routing.services.geocoding import GeocodingError

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
