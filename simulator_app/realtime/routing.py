"""WebSocket URL routing for the SIMOVI simulator."""

from channels.routing import URLRouter
from django.urls import path

from simulator_app.realtime.consumers import FleetConsumer

websocket_urlpatterns = [
    path("ws/fleet/", FleetConsumer.as_asgi()),
]
