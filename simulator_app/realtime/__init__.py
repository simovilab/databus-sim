"""Realtime layer — Django Channels consumers and broadcast helpers.

Submodules (created in Phase 4):
  consumers.py — FleetConsumer(AsyncJsonWebsocketConsumer)
  routing.py   — websocket_urlpatterns (imported by asgi.py)
  broadcast.py — broadcast_fleet() / broadcast_schedule() with 200 ms throttle
"""
