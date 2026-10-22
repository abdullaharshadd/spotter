from django.test import TestCase

from routing.models import FuelStation
from routing.services.route_planner import (
    FuelStop,
    build_route_points,
    compute_fuel_cost,
    find_reference_price,
    haversine_miles,
    plan_fuel_stops,
)
from routing.tests.helpers import make_straight_line_coordinates


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


def make_station(opis_id, price, lat, lon):
    return FuelStation.objects.create(
        opis_id=opis_id,
        name=f"Station {opis_id}",
        address="addr",
        city="X",
        state="KS",
        rack_id=1,
        retail_price=price,
        latitude=lat,
        longitude=lon,
    )


class MultiStopPlannerTests(TestCase):
    def setUp(self):
        # ~1,300 mile route due east along latitude 39.0.
        self.coordinates = make_straight_line_coordinates(
            -110.0, 39.0, -85.0, 39.0, 600
        )
        self.points = build_route_points(self.coordinates)
        self.total_distance = self.points[-1].cumulative_miles

    def _point_at(self, mile):
        return next(p for p in self.points if p.cumulative_miles >= mile)

    def test_long_route_gets_a_stop_every_tank(self):
        for i, mile in enumerate((470, 940), start=1):
            point = self._point_at(mile)
            make_station(i, "3.500", point.lat, point.lon)

        stops = plan_fuel_stops(self.coordinates, self.total_distance)

        self.assertEqual(len(stops), 2)
        self.assertLess(stops[0].mile_marker, 500)
        self.assertLessEqual(stops[1].mile_marker - stops[0].mile_marker, 500)

    def test_station_outside_corridor_is_ignored(self):
        point = self._point_at(470)
        # ~35 miles north of the route, well outside the 5 mile corridor.
        make_station(1, "1.000", point.lat + 0.5, point.lon)

        self.assertEqual(plan_fuel_stops(self.coordinates, self.total_distance), [])

    def test_falls_back_to_earlier_station_when_trigger_window_is_empty(self):
        point = self._point_at(200)
        make_station(1, "3.000", point.lat, point.lon)

        stops = plan_fuel_stops(self.coordinates, self.total_distance)

        # Used to loop forever re-picking this station: with nothing further
        # along the route, planning must end after the single stop.
        self.assertEqual(len(stops), 1)
        self.assertEqual(stops[0].station.opis_id, 1)
        self.assertAlmostEqual(stops[0].mile_marker, 200, delta=5)

    def test_cost_prices_each_leg_at_the_fill_up_that_begins_it(self):
        first = make_station(1, "2.000", 39.0, -100.0)
        second = make_station(2, "4.000", 39.0, -95.0)
        stops = [
            FuelStop(station=first, mile_marker=400.0, distance_from_route_miles=0),
            FuelStop(station=second, mile_marker=800.0, distance_from_route_miles=0),
        ]

        result = compute_fuel_cost(1000.0, stops)

        # 0-400 and 400-800 at $2 (40 gal each), 800-1000 at $4 (20 gal).
        self.assertEqual(result["total_gallons"], 100.0)
        self.assertEqual(result["total_cost"], 240.0)


class FindReferencePriceTests(TestCase):
    def test_returns_cheapest_station_near_route_start(self):
        make_station(1, "4.000", 39.0, -98.0)
        cheaper = make_station(2, "3.000", 39.01, -98.0)
        make_station(3, "1.000", 39.0, -90.0)  # cheap, but far from the start
        coords = make_straight_line_coordinates(-98.0, 39.0, -97.0, 39.0, 10)

        self.assertEqual(find_reference_price(coords), cheaper)

    def test_returns_none_when_no_station_near_start(self):
        coords = make_straight_line_coordinates(-98.0, 39.0, -97.0, 39.0, 10)

        self.assertIsNone(find_reference_price(coords))
