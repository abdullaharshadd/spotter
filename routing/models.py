from django.db import models


class FuelStation(models.Model):
    """A truck-stop fuel station with a known retail diesel price.

    Coordinates are populated by the ``load_fuel_prices`` management command,
    which geocodes each unique city/state pair once and caches the result here
    so route requests never need to geocode.
    """

    opis_id = models.IntegerField(db_index=True)
    name = models.CharField(max_length=255)
    address = models.CharField(max_length=255)
    city = models.CharField(max_length=128)
    state = models.CharField(max_length=2)
    rack_id = models.IntegerField()
    retail_price = models.DecimalField(max_digits=6, decimal_places=3)

    latitude = models.FloatField(null=True, blank=True)
    longitude = models.FloatField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["latitude", "longitude"]),
            models.Index(fields=["city", "state"]),
        ]

    def __str__(self):
        return f"{self.name} ({self.city}, {self.state}) - ${self.retail_price}"


class GeocodedPlace(models.Model):
    """Cache of city/state -> lat/lon lookups so geocoding only ever runs once
    per unique place, even across repeated imports of the price list."""

    city = models.CharField(max_length=128)
    state = models.CharField(max_length=2)
    latitude = models.FloatField()
    longitude = models.FloatField()

    class Meta:
        unique_together = ("city", "state")

    def __str__(self):
        return f"{self.city}, {self.state} -> ({self.latitude}, {self.longitude})"
