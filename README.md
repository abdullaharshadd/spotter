# Spotter — Route & Fuel Stop Planner API

A Django + DRF API that plans a driving route between two USA locations and
returns the cheapest fuel stops needed along the way, plus the total
estimated fuel cost.

## How it works

1. **Geocode** the `start` and `finish` locations via the free
   [Nominatim](https://nominatim.org/) (OpenStreetMap) search API — up to 2 calls.
2. **Route** between the two points via the free
   [OSRM](https://project-osrm.org/) public routing API — **1 call**, returning
   full route geometry (GeoJSON) and total distance.
3. **Plan fuel stops** locally: the vehicle has a 500-mile range. Starting
   with a full tank, once the remaining range drops to 50 miles (the safety
   buffer), the cheapest fuel station within 5 miles of the route in the
   reachable window is selected as the next stop. This repeats until the
   destination is within range.
4. **Compute cost**: `total_gallons = distance / 10 mpg`. Each leg of the
   trip is priced using the price at the fill-up that begins it (the first
   leg uses the first stop's price).

No fuel-station geocoding happens at request time — the ~8,150 truck stops in
`routing/data/fuel_prices.csv` are pre-geocoded into the database by a
one-time management command (see Setup), so each API request makes at most
**3 external calls total** (2 geocoding + 1 routing) and is otherwise pure
local DB/CPU work.

## Setup

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

python manage.py migrate
python manage.py load_fuel_prices   # one-time: load + geocode fuel stations
python manage.py runserver
```

The `load_fuel_prices` command:
- Loads all rows from `routing/data/fuel_prices.csv`.
- Matches each station's city/state against a bundled US Census Gazetteer
  extract (`routing/data/us_places.csv`) — instant, offline, no API calls.
- For any city/state not found there (~5% of stations, mostly Canadian
  entries or unusual spellings), falls back to a one-time live Nominatim
  geocode, cached in the `GeocodedPlace` table so subsequent runs make no
  network calls.

## API

### `POST /api/route/`

**Request body:**
```json
{
  "start": "Los Angeles, CA",
  "finish": "Chicago, IL"
}
```

**Response:**
```json
{
  "start": {"query": "Los Angeles, CA", "latitude": 34.05, "longitude": -118.24},
  "finish": {"query": "Chicago, IL", "latitude": 41.88, "longitude": -87.62},
  "distance_miles": 2018.15,
  "duration_hours": 35.39,
  "route_geometry": { "type": "LineString", "coordinates": [[lon, lat], ...] },
  "fuel_stops": [
    {
      "name": "ERNIES TRUCK PLAZA",
      "address": "...",
      "city": "Beaver",
      "state": "UT",
      "price_per_gallon": "3.399",
      "latitude": 38.27,
      "longitude": -112.64,
      "mile_marker": 484.9
    }
  ],
  "total_gallons": 201.81,
  "total_fuel_cost_usd": 665.75,
  "vehicle_assumptions": {"max_range_miles": 500, "mpg": 10}
}
```

`route_geometry` is a standard GeoJSON `LineString` and can be plotted
directly on any map (Leaflet, Mapbox GL, Google Maps polyline, etc.).

## Configuration

Tunable assumptions live in `config/settings.py`:

| Setting | Default | Meaning |
|---|---|---|
| `VEHICLE_MAX_RANGE_MILES` | 500 | Max distance on a full tank |
| `VEHICLE_MPG` | 10 | Fuel economy |
| `VEHICLE_RANGE_BUFFER_MILES` | 50 | Safety margin before triggering a refuel search |
| `FUEL_STATION_CORRIDOR_MILES` | 5 | Max distance a station may be from the route to be considered |

## Tests

```bash
python manage.py test
```

Tests cover the haversine distance helper, the fuel-stop planning algorithm
(short routes need no stops, long routes pick the cheapest in-corridor
station), the cost calculation, and the API view (mocking the external
geocoding/routing calls so tests run fully offline).
