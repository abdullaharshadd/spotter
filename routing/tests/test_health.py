from unittest.mock import patch

from django.db import OperationalError
from django.test import TestCase
from django.urls import reverse


class HealthViewTests(TestCase):
    def test_returns_ok_when_database_is_reachable(self):
        response = self.client.get(reverse("health"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok", "database": "ok"})

    @patch("routing.views.connection.ensure_connection")
    def test_returns_503_when_database_is_unreachable(self, mock_ensure):
        mock_ensure.side_effect = OperationalError("unable to open database file")

        response = self.client.get(reverse("health"))

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["status"], "error")
