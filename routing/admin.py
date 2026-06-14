from django.contrib import admin

from routing.models import FuelStation, GeocodedPlace


@admin.register(FuelStation)
class FuelStationAdmin(admin.ModelAdmin):
    list_display = ("name", "city", "state", "retail_price", "latitude", "longitude")
    list_filter = ("state",)
    search_fields = ("name", "city", "address")


@admin.register(GeocodedPlace)
class GeocodedPlaceAdmin(admin.ModelAdmin):
    list_display = ("city", "state", "latitude", "longitude")
    search_fields = ("city",)
