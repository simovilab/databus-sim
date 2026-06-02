"""WebSocket URL routing for the SIMOVI simulator.

Phase 4 adds the FleetConsumer here:

    from channels.routing import URLRouter
    from django.urls import path
    from simulator_app.realtime.consumers import FleetConsumer

    websocket_urlpatterns = [
        path("ws/fleet/", FleetConsumer.as_asgi()),
    ]

Phase 1 scaffold: empty list so asgi.py imports resolve cleanly.
"""

websocket_urlpatterns: list = []
