from django.urls import path

from routing.views import HealthView, RouteWithFuelStopsView

urlpatterns = [
    path("health/", HealthView.as_view(), name="health"),
    path("route/", RouteWithFuelStopsView.as_view(), name="route-with-fuel-stops"),
]
