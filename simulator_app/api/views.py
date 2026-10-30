"""DRF API views for the SIMOVI simulator.

Route surface (all under /sim/ prefix set in sim_project/urls.py):
  GET  healthz
  GET  fleet
  GET  schedule
  PUT  schedule
  POST schedule/reload
  GET  run/<run_id>
  POST runs/track
  POST control/<vehicle_id>/<knob>
  POST control/global/<knob>
  GET  geometry

All views use AllowAny permission (set as project default in settings.REST_FRAMEWORK).
Async httpx calls use asgiref.sync.sync_to_async or are invoked via async views
(Django 4.1+ supports async function-based views natively).
"""

from __future__ import annotations

import json
import logging
from typing import Any

from asgiref.sync import async_to_sync
from django.conf import settings
from rest_framework.decorators import api_view
from rest_framework.request import Request
from rest_framework.response import Response

from simulator_app.domain.control import apply_control, apply_global_control
from simulator_app.domain.progression.shapes import build_route_geometry
from simulator_app.runtime import get_runtime

from .serializers import (
    ScheduleDocumentSerializer,
    TrackRunRequestSerializer,
)

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------


@api_view(["GET"])
def healthz_view(request: Request) -> Response:
    """GET /sim/healthz — liveness probe."""
    return Response({"ok": True})


# ---------------------------------------------------------------------------
# Fleet
# ---------------------------------------------------------------------------


@api_view(["GET"])
def fleet_view(request: Request) -> Response:
    """GET /sim/fleet → current fleet snapshot."""
    rt = get_runtime()
    if rt.fleet is None:
        return Response({"vehicles": [], "published_at": None})
    return Response(rt.fleet.snapshot())


# ---------------------------------------------------------------------------
# Schedule
# ---------------------------------------------------------------------------


@api_view(["GET", "PUT"])
def schedule_view(request: Request) -> Response:
    """GET /sim/schedule → snapshot; PUT /sim/schedule → write+reload."""
    rt = get_runtime()
    if request.method == "GET":
        if rt.scheduler is None:
            return Response({"defaults": {}, "runs": [], "published_at": None})
        return Response(rt.scheduler.snapshot())

    # PUT
    serializer = ScheduleDocumentSerializer(data=request.data)
    if not serializer.is_valid():
        return Response(serializer.errors, status=422)

    if rt.scheduler is None:
        return Response({"error": "scheduler not ready"}, status=503)

    doc = serializer.validated_data
    try:
        rt.scheduler.write(doc)
        rt.scheduler.reload()
    except Exception as exc:
        log.exception("schedule_view PUT failed")
        return Response({"error": str(exc)}, status=400)

    return Response({"ok": True})


@api_view(["POST"])
def schedule_reload_view(request: Request) -> Response:
    """POST /sim/schedule/reload → reload schedule from disk."""
    rt = get_runtime()
    if rt.scheduler is None:
        return Response({"error": "scheduler not ready"}, status=503)
    try:
        rt.scheduler.reload()
    except Exception as exc:
        log.exception("schedule_reload_view failed")
        return Response({"error": str(exc)}, status=400)
    return Response({"ok": True})


# ---------------------------------------------------------------------------
# Run state
# ---------------------------------------------------------------------------


@api_view(["GET"])
def run_detail_view(request: Request, run_id: str) -> Response:
    """GET /sim/run/<run_id> → run state from databus; 404 if not found."""
    rt = get_runtime()
    if rt.databus is None:
        return Response({"error": "databus client not ready"}, status=503)

    fields: dict[str, Any] = async_to_sync(rt.databus.get_run_hash)(run_id)
    if not fields:
        return Response({"detail": f"run {run_id!r} not found"}, status=404)

    lifecycle_state = fields.get("run_lifecycle_state")
    return Response(
        {
            "run_id": run_id,
            "run_lifecycle_state": lifecycle_state,
            "fields": fields,
        }
    )


# ---------------------------------------------------------------------------
# Track run
# ---------------------------------------------------------------------------


@api_view(["POST"])
def track_run_view(request: Request) -> Response:
    """POST /sim/runs/track → register a run with RunBinder."""
    rt = get_runtime()
    serializer = TrackRunRequestSerializer(data=request.data)
    if not serializer.is_valid():
        return Response(serializer.errors, status=400)

    data = serializer.validated_data
    vehicle_id = data["vehicle_id"]
    trip_id = data["trip_id"]
    run_id = data["run_id"]
    shape_id = data["shape_id"]

    if rt.fleet is None or rt.fleet.get(vehicle_id) is None:
        return Response({"detail": f"unknown vehicle {vehicle_id!r}"}, status=404)

    terminal_stop_id: str | None = None
    if rt.databus is not None:
        try:
            terminal_stop_id = async_to_sync(rt.databus.get_trip_terminal_stop)(trip_id)
        except Exception as exc:
            log.warning(
                "track_run_view: get_trip_terminal_stop failed for trip %s: %s",
                trip_id,
                exc,
            )

    if rt.binder is not None:
        rt.binder.track(
            run_id=run_id,
            vehicle_id=vehicle_id,
            trip_id=trip_id,
            shape_id=shape_id,
            terminal_stop_id=terminal_stop_id,
        )

    return Response(
        {
            "status": "ok",
            "run_id": run_id,
            "terminal_stop_id": terminal_stop_id,
        }
    )


# ---------------------------------------------------------------------------
# Control — per-vehicle and global
# ---------------------------------------------------------------------------


@api_view(["POST"])
def control_vehicle_view(request: Request, vehicle_id: str, knob: str) -> Response:
    """POST /sim/control/<vehicle_id>/<knob> — replaces MQTT sim/control/<id>/<knob>."""
    rt = get_runtime()
    payload = request.data if isinstance(request.data, dict) else {}
    apply_control(rt, vehicle_id, knob, payload)
    return Response({"ok": True})


@api_view(["POST"])
def control_global_view(request: Request, knob: str) -> Response:
    """POST /sim/control/global/<knob> — replaces MQTT sim/control/global/<knob>."""
    rt = get_runtime()
    payload = request.data if isinstance(request.data, dict) else {}
    apply_global_control(rt, knob, payload)
    return Response({"ok": True})


# ---------------------------------------------------------------------------
# Geometry
# ---------------------------------------------------------------------------

# Module-level cache so repeated requests don't recompute the (static) geometry.
_geometry_cache: dict[str, Any] = {}


@api_view(["GET"])
def geometry_view(request: Request) -> Response:
    """GET /sim/geometry — per-route map geometry (polylines + ordered stops).

    Returns a JSON object with a ``routes`` list.  Each entry is produced by
    ``build_route_geometry`` and contains:

    * ``route_id``, ``short_name``, ``color``, ``text_color``
    * ``shapes`` — list of shape dicts with ``shape_id``, ``latlngs``
      (list of [lat, lon] pairs), and ``stops`` (ordered by progress_m)
    * ``stops`` — merged unique stops across all shapes of the route

    The result is memoised in a module-level dict on the first successful
    request; subsequent calls return the cached value without re-reading or
    reprocessing the shapes file.

    On any read/parse failure returns ``{"routes": []}`` with HTTP 200 and
    logs a warning — the caller should treat an empty list as "data not yet
    available" rather than a hard error.
    """
    if "data" in _geometry_cache:
        return Response(_geometry_cache["data"])

    try:
        with open(settings.SHAPES_PATH, encoding="utf-8") as fh:
            raw: dict = json.load(fh)
    except (OSError, ValueError) as exc:
        log.warning("geometry_view: could not load shapes file %s: %s", settings.SHAPES_PATH, exc)
        return Response({"routes": []})

    routes_geo = [
        build_route_geometry(route, raw["shapes"], raw["stops"])
        for route in raw.get("routes", [])
    ]
    result: dict[str, Any] = {"routes": routes_geo}
    _geometry_cache["data"] = result
    return Response(result)
