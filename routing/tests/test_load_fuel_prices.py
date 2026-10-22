import tempfile
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch

from django.core.management import call_command
from django.test import TestCase

from routing.models import FuelStation, GeocodedPlace

FUEL_CSV = """OPIS Truckstop ID,Truckstop Name,Address,City,State,Rack ID,Retail Price
7,WOODSHED OF BIG CABIN,"I-44, EXIT 283",Big Cabin,OK,307,3.00733333
9,KWIK TRIP #796,"I-94, EXIT 143",Tomah,WI,420,3.28733333
11,ROADSIDE GAS,"US-1",Smallville,KS,100,3.10000000
12,BORDER STOP,"HWY 1",Toronto,ON,100,3.20000000
"""

PLACES_CSV = """city,state,latitude,longitude
Big Cabin,OK,36.53,-95.22
Tomah,WI,43.98,-90.50
"""


class LoadFuelPricesCommandTests(TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.fuel_csv = Path(tmp.name) / "fuel.csv"
        self.fuel_csv.write_text(FUEL_CSV)
        places_csv = Path(tmp.name) / "places.csv"
        places_csv.write_text(PLACES_CSV)

        places_patch = patch(
            "routing.management.commands.load_fuel_prices.US_PLACES_CSV_PATH",
            places_csv,
        )
        places_patch.start()
        self.addCleanup(places_patch.stop)

        sleep_patch = patch("routing.management.commands.load_fuel_prices.time.sleep")
        sleep_patch.start()
        self.addCleanup(sleep_patch.stop)

    def run_command(self, *args):
        out = StringIO()
        call_command("load_fuel_prices", str(self.fuel_csv), *args, stdout=out)
        return out.getvalue()

    def test_loads_stations_and_applies_bundled_coordinates(self):
        output = self.run_command("--skip-geocode")

        self.assertIn("4 created, 0 updated", output)
        big_cabin = FuelStation.objects.get(opis_id=7)
        self.assertEqual((big_cabin.latitude, big_cabin.longitude), (36.53, -95.22))
        self.assertEqual(str(big_cabin.retail_price), "3.007")
        self.assertIsNone(FuelStation.objects.get(opis_id=11).latitude)

    def test_rerun_updates_instead_of_duplicating(self):
        self.run_command("--skip-geocode")
        output = self.run_command("--skip-geocode")

        self.assertIn("0 created, 4 updated", output)
        self.assertEqual(FuelStation.objects.count(), 4)

    @patch("routing.management.commands.load_fuel_prices.requests.get")
    def test_geocodes_unmatched_us_places_once_and_skips_non_us(self, mock_get):
        resp = MagicMock()
        resp.json.return_value = [{"lat": "39.1", "lon": "-96.6"}]
        mock_get.return_value = resp

        self.run_command()

        # Only Smallville, KS goes to Nominatim; Toronto, ON is skipped.
        self.assertEqual(mock_get.call_count, 1)
        self.assertEqual(mock_get.call_args.kwargs["params"]["city"], "Smallville")
        self.assertTrue(
            GeocodedPlace.objects.filter(city="Smallville", state="KS").exists()
        )
        smallville = FuelStation.objects.get(opis_id=11)
        self.assertEqual((smallville.latitude, smallville.longitude), (39.1, -96.6))

        # A second run uses the GeocodedPlace cache, not the network.
        FuelStation.objects.filter(opis_id=11).update(latitude=None, longitude=None)
        self.run_command()
        self.assertEqual(mock_get.call_count, 1)
        self.assertIsNotNone(FuelStation.objects.get(opis_id=11).latitude)

    @patch("routing.management.commands.load_fuel_prices.requests.get")
    def test_failed_geocode_is_reported_and_left_unset(self, mock_get):
        resp = MagicMock()
        resp.json.return_value = []
        mock_get.return_value = resp

        output = self.run_command()

        self.assertIn("No match for Smallville, KS", output)
        self.assertIn("0 newly geocoded, 1 failed", output)
        self.assertIsNone(FuelStation.objects.get(opis_id=11).latitude)

    def test_missing_csv_reports_error(self):
        err = StringIO()
        call_command("load_fuel_prices", "/nonexistent/fuel.csv", stderr=err)

        self.assertIn("File not found", err.getvalue())
        self.assertEqual(FuelStation.objects.count(), 0)
