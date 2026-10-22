from unittest.mock import MagicMock, patch

import requests
from django.test import SimpleTestCase, override_settings

from routing.services.geocoding import GeocodingError, geocode_location
from routing.services.osrm import RoutingError, get_route


def fake_response(json_data=None, status_error=None):
    resp = MagicMock()
    resp.json.return_value = json_data
    if status_error is not None:
        resp.raise_for_status.side_effect = status_error
    return resp


@override_settings(NOMINATIM_BASE_URL="https://geo.test")
class GeocodeLocationTests(SimpleTestCase):
    @patch("routing.services.geocoding.requests.get")
    def test_returns_lat_lon_of_first_result(self, mock_get):
        mock_get.return_value = fake_response([{"lat": "41.88", "lon": "-87.62"}])

        self.assertEqual(geocode_location("Chicago, IL"), (41.88, -87.62))
        url = mock_get.call_args.args[0]
        self.assertEqual(url, "https://geo.test/search")
        self.assertEqual(mock_get.call_args.kwargs["params"]["countrycodes"], "us")

    @patch("routing.services.geocoding.requests.get")
    def test_no_results_raises(self, mock_get):
        mock_get.return_value = fake_response([])

        with self.assertRaisesMessage(GeocodingError, "Could not find a location"):
            geocode_location("Nowhereville")

    @patch("routing.services.geocoding.requests.get")
    def test_http_error_raises(self, mock_get):
        mock_get.return_value = fake_response(
            status_error=requests.HTTPError("503 Service Unavailable")
        )

        with self.assertRaisesMessage(GeocodingError, "Geocoding service error"):
            geocode_location("Chicago, IL")

    @patch("routing.services.geocoding.requests.get")
    def test_network_error_raises(self, mock_get):
        mock_get.side_effect = requests.ConnectionError("connection refused")

        with self.assertRaises(GeocodingError):
            geocode_location("Chicago, IL")

    @patch("routing.services.geocoding.requests.get")
    def test_malformed_result_raises(self, mock_get):
        mock_get.return_value = fake_response([{"lat": "not-a-number", "lon": "1"}])

        with self.assertRaisesMessage(GeocodingError, "Unexpected geocoding response"):
            geocode_location("Chicago, IL")


@override_settings(OSRM_BASE_URL="https://osrm.test")
class GetRouteTests(SimpleTestCase):
    @patch("routing.services.osrm.requests.get")
    def test_converts_meters_to_miles_and_returns_geometry(self, mock_get):
        geometry = {"type": "LineString", "coordinates": [[-87.6, 41.9], [-90.2, 38.6]]}
        mock_get.return_value = fake_response(
            {
                "code": "Ok",
                "routes": [  # 482803.2 m == 300 mi
                    {"distance": 482803.2, "duration": 18000, "geometry": geometry}
                ],
            }
        )

        route = get_route((41.9, -87.6), (38.6, -90.2))

        self.assertAlmostEqual(route["distance_miles"], 300.0)
        self.assertEqual(route["duration_seconds"], 18000)
        self.assertEqual(route["geometry"], geometry)
        self.assertEqual(route["coordinates"], geometry["coordinates"])
        # OSRM expects lon,lat order in the URL.
        self.assertEqual(
            mock_get.call_args.args[0],
            "https://osrm.test/route/v1/driving/-87.6,41.9;-90.2,38.6",
        )

    @patch("routing.services.osrm.requests.get")
    def test_no_route_code_raises(self, mock_get):
        mock_get.return_value = fake_response({"code": "NoRoute", "routes": []})

        with self.assertRaisesMessage(RoutingError, "NoRoute"):
            get_route((41.9, -87.6), (21.3, -157.8))

    @patch("routing.services.osrm.requests.get")
    def test_http_error_raises(self, mock_get):
        mock_get.return_value = fake_response(
            status_error=requests.HTTPError("429 Too Many Requests")
        )

        with self.assertRaisesMessage(RoutingError, "Routing service error"):
            get_route((41.9, -87.6), (38.6, -90.2))

    @patch("routing.services.osrm.requests.get")
    def test_timeout_raises(self, mock_get):
        mock_get.side_effect = requests.Timeout("timed out")

        with self.assertRaises(RoutingError):
            get_route((41.9, -87.6), (38.6, -90.2))
