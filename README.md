# Spotter — Route & Fuel Stop Planner API

![CI](https://github.com/abdullaharshadd/spotter/actions/workflows/ci.yml/badge.svg)

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

### With Docker (quickest)

```bash
cp .env.example .env    # optional; every value has a default
docker compose up --build
curl http://localhost:8000/api/health/
```

The container runs migrations and loads the fuel stations on start, then
serves the API on port 8000. The repo is mounted into the container, so code
edits reload the server.

### Local (Python 3.11+)

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements-dev.txt   # pinned runtime deps + coverage + ruff

python manage.py migrate
python manage.py load_fuel_prices   # one-time: load + geocode fuel stations
python manage.py runserver
```

`requirements.txt` lists the direct dependencies; `requirements-lock.txt`
pins every transitive version and is what Docker and CI install. Settings are
read from environment variables (see `.env.example`); Docker Compose loads
`.env` automatically, for a local run export them in your shell.

The `load_fuel_prices` command:
- Loads all rows from `routing/data/fuel_prices.csv`.
- Matches each station's city/state against a bundled US Census Gazetteer
  extract (`routing/data/us_places.csv`) — instant, offline, no API calls.
- For any city/state not found there (~5% of stations, mostly Canadian
  entries or unusual spellings), falls back to a one-time live Nominatim
  geocode, cached in the `GeocodedPlace` table so subsequent runs make no
  network calls.

## API

### `GET /api/health/`

Returns `{"status": "ok", "database": "ok"}` with 200, or 503 if the database
is unreachable. Suitable as a container liveness/readiness probe.

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

Errors: `400` for invalid input or a location that can't be geocoded, `502`
if the routing service fails. Both return `{"error": "..."}`.

## Configuration

Tunable assumptions live in `config/settings.py`:

| Setting | Default | Meaning |
|---|---|---|
| `VEHICLE_MAX_RANGE_MILES` | 500 | Max distance on a full tank |
| `VEHICLE_MPG` | 10 | Fuel economy |
| `VEHICLE_RANGE_BUFFER_MILES` | 50 | Safety margin before triggering a refuel search |
| `FUEL_STATION_CORRIDOR_MILES` | 5 | Max distance a station may be from the route to be considered |

Environment variables (all optional, see `.env.example`):

| Variable | Default | Meaning |
|---|---|---|
| `DJANGO_SECRET_KEY` | insecure dev key | Set this anywhere outside local dev |
| `DJANGO_DEBUG` | `True` | |
| `DJANGO_ALLOWED_HOSTS` | `*` | Comma-separated |
| `DJANGO_LOG_LEVEL` | `INFO` | Logs go to stdout as `key=value` lines |
| `OSRM_BASE_URL` | public OSRM server | |
| `NOMINATIM_BASE_URL` | public Nominatim server | |

## Tests and linting

```bash
python manage.py test                           # run the suite
coverage run manage.py test && coverage report  # with coverage (fails under 90%)
ruff check . && ruff format --check .           # lint + format check
```

Tests live in `routing/tests/` and run fully offline: geocoding, routing and
Nominatim calls are mocked. They cover the haversine helper, fuel-stop
planning (single and multi-stop routes, corridor filtering, the fallback
when no station is in the refuel window), cost calculation, the geocoding and
OSRM clients' error handling, the `load_fuel_prices` command, and the API
views.

CI (`.github/workflows/ci.yml`) runs lint, `manage.py check`, a missing-
migrations check and the tests with coverage on every push and pull request,
plus a `pip-audit` scan of the pinned dependencies. Dependabot opens weekly
update PRs for pip packages and GitHub Actions.
