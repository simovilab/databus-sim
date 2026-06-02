"""DRF API views for the SIMOVI simulator.

Phase 1: only the healthz view is implemented. All other views are stubs
for Phase 5 (backend porting agent).

Phase 5 backend agent: implement the full route surface from PLAN §6.3:
  - FleetView          GET /sim/fleet → runtime.fleet.snapshot()
  - ScheduleView       GET/PUT /sim/schedule → scheduler.snapshot() / write()+reload()
  - ScheduleReloadView POST /sim/schedule/reload → scheduler.reload()
  - RunDetailView      GET /sim/run/<run_id> → databus.get_run_hash()
  - TrackRunView       POST /sim/runs/track → binder.track()
  - ControlView        POST /sim/control/<vehicle_id>/<knob> → apply_control()
  - GlobalControlView  POST /sim/control/global/<knob> → apply_control()

All views use AllowAny permission (see settings.REST_FRAMEWORK).
"""

from __future__ import annotations

from rest_framework.decorators import api_view
from rest_framework.request import Request
from rest_framework.response import Response


@api_view(["GET"])
def healthz_view(request: Request) -> Response:
    """GET /sim/healthz — liveness probe.

    Returns {"ok": true} unconditionally. Matches the FastAPI /healthz contract.
    """
    return Response({"ok": True})
