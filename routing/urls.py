from django.urls import path

from routing.views import RouteWithFuelStopsView

urlpatterns = [
    path("route/", RouteWithFuelStopsView.as_view(), name="route-with-fuel-stops"),
]
