"""URL patterns for the simulator API, mounted at /sim/ in sim_project/urls.py."""

from django.urls import path

from .views import (
    control_global_view,
    control_vehicle_view,
    fleet_view,
    geometry_view,
    healthz_view,
    run_detail_view,
    schedule_reload_view,
    schedule_view,
    track_run_view,
)

app_name = "simulator_app"

urlpatterns = [
    # Health-check
    path("healthz", healthz_view, name="healthz"),
    # Fleet
    path("fleet", fleet_view, name="fleet"),
    # Schedule
    path("schedule", schedule_view, name="schedule"),
    path("schedule/reload", schedule_reload_view, name="schedule-reload"),
    # Run state
    path("run/<str:run_id>", run_detail_view, name="run-detail"),
    # Track run
    path("runs/track", track_run_view, name="runs-track"),
    # Control — order matters: "global" before <vehicle_id>
    path("control/global/<str:knob>", control_global_view, name="control-global"),
    path("control/<str:vehicle_id>/<str:knob>", control_vehicle_view, name="control-vehicle"),
    # Geometry
    path("geometry", geometry_view, name="geometry"),
]
