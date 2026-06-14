from rest_framework import serializers


class RouteRequestSerializer(serializers.Serializer):
    start = serializers.CharField(
        max_length=255,
        help_text="Start location, e.g. 'Chicago, IL'.",
    )
    finish = serializers.CharField(
        max_length=255,
        help_text="Finish location, e.g. 'Denver, CO'.",
    )


class FuelStopSerializer(serializers.Serializer):
    name = serializers.CharField()
    address = serializers.CharField()
    city = serializers.CharField()
    state = serializers.CharField()
    price_per_gallon = serializers.DecimalField(max_digits=6, decimal_places=3)
    latitude = serializers.FloatField()
    longitude = serializers.FloatField()
    mile_marker = serializers.FloatField(
        help_text="Distance in miles along the route where this stop occurs."
    )


class RouteResponseSerializer(serializers.Serializer):
    start = serializers.DictField()
    finish = serializers.DictField()
    distance_miles = serializers.FloatField()
    duration_hours = serializers.FloatField()
    route_geometry = serializers.DictField(
        help_text="GeoJSON LineString of the route, for map display."
    )
    fuel_stops = FuelStopSerializer(many=True)
    total_gallons = serializers.FloatField()
    total_fuel_cost_usd = serializers.FloatField(allow_null=True)
    vehicle_assumptions = serializers.DictField()
