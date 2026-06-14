import csv
import time
from pathlib import Path

import requests
from django.conf import settings
from django.core.management.base import BaseCommand

from routing.models import FuelStation, GeocodedPlace

DEFAULT_CSV_PATH = Path(settings.BASE_DIR) / "routing" / "data" / "fuel_prices.csv"
US_PLACES_CSV_PATH = Path(settings.BASE_DIR) / "routing" / "data" / "us_places.csv"

# US state/territory codes. Stations in places outside this set (e.g. Canadian
# provinces that occasionally appear in truck-stop datasets) are skipped during
# the live-geocoding fallback rather than sent to Nominatim.
US_STATE_CODES = {
    "AL",
    "AK",
    "AZ",
    "AR",
    "CA",
    "CO",
    "CT",
    "DE",
    "FL",
    "GA",
    "HI",
    "ID",
    "IL",
    "IN",
    "IA",
    "KS",
    "KY",
    "LA",
    "ME",
    "MD",
    "MA",
    "MI",
    "MN",
    "MS",
    "MO",
    "MT",
    "NE",
    "NV",
    "NH",
    "NJ",
    "NM",
    "NY",
    "NC",
    "ND",
    "OH",
    "OK",
    "OR",
    "PA",
    "RI",
    "SC",
    "SD",
    "TN",
    "TX",
    "UT",
    "VT",
    "VA",
    "WA",
    "WV",
    "WI",
    "WY",
    "DC",
    "PR",
}

# Be a polite citizen of the free Nominatim service: max 1 request/second.
NOMINATIM_DELAY_SECONDS = 1.0


class Command(BaseCommand):
    help = (
        "Load the OPIS fuel-price CSV into the FuelStation table and attach "
        "lat/lon to each station. Coordinates come primarily from a bundled "
        "US Census Gazetteer extract (instant, offline); any city/state pair "
        "not found there is geocoded once via Nominatim and cached in "
        "GeocodedPlace so re-runs make no further network calls."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "csv_path",
            nargs="?",
            default=str(DEFAULT_CSV_PATH),
            help="Path to the fuel prices CSV file.",
        )
        parser.add_argument(
            "--skip-geocode",
            action="store_true",
            help="Load station rows and apply bundled coordinates only; skip "
            "the live Nominatim fallback for unmatched places.",
        )

    def handle(self, *args, **options):
        csv_path = Path(options["csv_path"])
        if not csv_path.exists():
            self.stderr.write(self.style.ERROR(f"File not found: {csv_path}"))
            return

        self.stdout.write(f"Loading fuel stations from {csv_path} ...")
        created, updated = self._load_stations(csv_path)
        self.stdout.write(
            self.style.SUCCESS(f"Stations: {created} created, {updated} updated.")
        )

        self.stdout.write("Applying bundled US Gazetteer coordinates ...")
        matched = self._apply_bundled_coordinates()
        self.stdout.write(
            self.style.SUCCESS(f"Matched {matched} stations from bundled dataset.")
        )

        if options["skip_geocode"]:
            return

        self._geocode_remaining()

    def _load_stations(self, csv_path: Path):
        created = 0
        updated = 0
        with open(csv_path, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                _, was_created = FuelStation.objects.update_or_create(
                    opis_id=int(row["OPIS Truckstop ID"]),
                    rack_id=int(row["Rack ID"]),
                    defaults={
                        "name": row["Truckstop Name"].strip(),
                        "address": row["Address"].strip(),
                        "city": row["City"].strip(),
                        "state": row["State"].strip(),
                        "retail_price": row["Retail Price"].strip(),
                    },
                )
                if was_created:
                    created += 1
                else:
                    updated += 1
        return created, updated

    def _load_bundled_places(self):
        places = {}
        with open(US_PLACES_CSV_PATH, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                key = (row["city"].lower(), row["state"])
                places[key] = (float(row["latitude"]), float(row["longitude"]))
        return places

    def _apply_bundled_coordinates(self):
        places = self._load_bundled_places()
        stations = list(FuelStation.objects.all())
        to_update = []
        for station in stations:
            coords = places.get((station.city.lower(), station.state))
            if coords is None:
                continue
            if (station.latitude, station.longitude) != coords:
                station.latitude, station.longitude = coords
                to_update.append(station)

        if to_update:
            FuelStation.objects.bulk_update(
                to_update, ["latitude", "longitude"], batch_size=500
            )
        return len(to_update)

    def _geocode_remaining(self):
        remaining = (
            FuelStation.objects.filter(latitude__isnull=True)
            .values_list("city", "state")
            .distinct()
        )
        remaining = sorted(set(remaining))
        remaining = [(c, s) for c, s in remaining if s in US_STATE_CODES]
        total = len(remaining)
        if total == 0:
            self.stdout.write(self.style.SUCCESS("No remaining US places to geocode."))
            return

        self.stdout.write(f"Geocoding {total} remaining US city/state combinations ...")

        cached = {
            (p.city, p.state): (p.latitude, p.longitude)
            for p in GeocodedPlace.objects.all()
        }

        geocoded = 0
        failed = 0
        for i, (city, state) in enumerate(remaining, start=1):
            if (city, state) not in cached:
                coords = self._geocode(city, state)
                if coords is None:
                    failed += 1
                    self.stdout.write(
                        self.style.WARNING(
                            f"  [{i}/{total}] No match for {city}, {state}"
                        )
                    )
                    continue
                GeocodedPlace.objects.create(
                    city=city, state=state, latitude=coords[0], longitude=coords[1]
                )
                cached[(city, state)] = coords
                geocoded += 1
                time.sleep(NOMINATIM_DELAY_SECONDS)

            if i % 25 == 0 or i == total:
                self.stdout.write(f"  ... {i}/{total} processed")

        self.stdout.write(
            self.style.SUCCESS(
                f"Geocoding complete: {geocoded} newly geocoded, {failed} failed."
            )
        )
        self._apply_geocoded_cache()

    def _geocode(self, city: str, state: str):
        try:
            resp = requests.get(
                f"{settings.NOMINATIM_BASE_URL}/search",
                params={
                    "city": city,
                    "state": state,
                    "country": "USA",
                    "format": "json",
                    "limit": 1,
                },
                headers={"User-Agent": "spotter-fuel-route-app/1.0"},
                timeout=10,
            )
            resp.raise_for_status()
            results = resp.json()
            if not results:
                return None
            return float(results[0]["lat"]), float(results[0]["lon"])
        except (requests.RequestException, KeyError, ValueError, IndexError):
            return None

    def _apply_geocoded_cache(self):
        place_map = {
            (p.city, p.state): (p.latitude, p.longitude)
            for p in GeocodedPlace.objects.all()
        }
        stations = list(FuelStation.objects.filter(latitude__isnull=True))
        to_update = []
        for station in stations:
            coords = place_map.get((station.city, station.state))
            if coords is None:
                continue
            station.latitude, station.longitude = coords
            to_update.append(station)

        if to_update:
            FuelStation.objects.bulk_update(
                to_update, ["latitude", "longitude"], batch_size=500
            )
        self.stdout.write(
            self.style.SUCCESS(
                f"Applied cached geocoding to {len(to_update)} stations."
            )
        )
