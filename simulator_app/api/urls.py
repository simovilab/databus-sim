"""URL patterns for the simulator API, mounted at /sim/ in sim_project/urls.py.

Phase 1: Only the /sim/healthz smoke-test endpoint is wired.

Phase 5 backend agent: add all routes from PLAN §6.3:
  GET  /sim/fleet
  GET  /sim/schedule
  PUT  /sim/schedule
  POST /sim/schedule/reload
  GET  /sim/run/<run_id>
  POST /sim/runs/track
  POST /sim/control/<vehicle_id>/<knob>
  POST /sim/control/global/<knob>
"""

from django.urls import path

from .views import healthz_view

app_name = "simulator_app"

urlpatterns = [
    # Health-check — used by the smoke test and compose health-checks.
    path("healthz", healthz_view, name="healthz"),
    # ---------------------------------------------------------------------------
    # Phase 5: uncomment / add routes as views are implemented.
    # ---------------------------------------------------------------------------
]
