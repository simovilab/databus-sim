"""Transport-agnostic control dispatch.

Ported from sim/controller.py. The MQTT subscribe/publish logic is removed;
the handler bodies are exposed as pure functions called by DRF views.

Public API:
    apply_control(runtime, vehicle_id, knob, payload) -> None
    apply_global_control(runtime, knob, payload)      -> None

Both functions log and silently discard malformed payloads (never raise).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# Valid per-vehicle knobs (matches CONTRACTS.md §1.1)
_PER_VEHICLE_KNOBS = frozenset(
    {
        "transmit",
        "moving",
        "speed",
        "occupancy",
        "dwell",
        "jump_to_terminal",
        "set_progress",
        "inject_fault",
    }
)

# Valid global knobs
_GLOBAL_KNOBS = frozenset({"start_run", "reload_schedule"})

_VALID_FAULT_KINDS = frozenset({"stale_ts", "out_of_bounds", "malformed"})


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


def apply_control(runtime: Any, vehicle_id: str, knob: str, payload: dict[str, Any]) -> None:
    """Dispatch a per-vehicle control action.

    ``runtime`` is the module-level ``Runtime`` instance (has .fleet, .scheduler,
    .binder attributes). Errors are logged and discarded; never raised.
    """
    fleet = runtime.fleet
    if fleet is None:
        log.warning("control.apply_control: runtime.fleet not ready")
        return

    if fleet.get(vehicle_id) is None:
        log.warning("control.apply_control: unknown vehicle_id %r", vehicle_id)
        return

    if knob not in _PER_VEHICLE_KNOBS:
        log.warning("control.apply_control: unknown knob %r", knob)
        return

    try:
        handler = _PER_VEHICLE_DISPATCH[knob]
        handler(runtime, vehicle_id, payload)
    except Exception as exc:
        log.warning("control.apply_control: handler error knob=%r vehicle=%r: %s", knob, vehicle_id, exc)


def apply_global_control(runtime: Any, knob: str, payload: dict[str, Any]) -> None:
    """Dispatch a global control action.

    Errors are logged and discarded; never raised.
    """
    if knob not in _GLOBAL_KNOBS:
        log.warning("control.apply_global_control: unknown global knob %r", knob)
        return

    try:
        handler = _GLOBAL_DISPATCH[knob]
        handler(runtime, payload)
    except Exception as exc:
        log.warning("control.apply_global_control: handler error knob=%r: %s", knob, exc)


# ---------------------------------------------------------------------------
# Per-vehicle handlers
# ---------------------------------------------------------------------------


def _handle_transmit(runtime: Any, vehicle_id: str, payload: dict[str, Any]) -> None:
    on = payload.get("on")
    if not isinstance(on, bool):
        log.warning("control: transmit payload missing bool 'on': %s", payload)
        return
    runtime.fleet.set_transmitting(vehicle_id, on)


def _handle_moving(runtime: Any, vehicle_id: str, payload: dict[str, Any]) -> None:
    on = payload.get("on")
    if not isinstance(on, bool):
        log.warning("control: moving payload missing bool 'on': %s", payload)
        return
    runtime.fleet.set_moving(vehicle_id, on)


def _handle_speed(runtime: Any, vehicle_id: str, payload: dict[str, Any]) -> None:
    value = payload.get("value")
    if value is not None and not isinstance(value, (int, float)):
        log.warning("control: speed value must be float or null: %s", payload)
        return
    runtime.fleet.set_speed_override(vehicle_id, float(value) if value is not None else None)


def _handle_occupancy(runtime: Any, vehicle_id: str, payload: dict[str, Any]) -> None:
    value = payload.get("value")
    if value is not None and not isinstance(value, int):
        log.warning("control: occupancy value must be int or null: %s", payload)
        return
    if value is not None and not (0 <= value <= 100):
        log.warning("control: occupancy value %d out of range [0,100]", value)
        return
    runtime.fleet.set_occupancy_override(vehicle_id, value)


def _handle_dwell(runtime: Any, vehicle_id: str, payload: dict[str, Any]) -> None:
    stop_id = payload.get("stop_id")
    ticks = payload.get("ticks", 0)
    if not isinstance(ticks, int) or ticks < 0:
        log.warning("control: dwell ticks must be non-negative int: %s", payload)
        return
    runtime.fleet.set_dwell_override(vehicle_id, stop_id, ticks)


def _handle_jump_to_terminal(
    runtime: Any, vehicle_id: str, payload: dict[str, Any]
) -> None:
    from . import kinematics as kin_mod

    v = runtime.fleet.get(vehicle_id)
    if v is None:
        return
    try:
        from django.conf import settings

        shapes, _, _ = kin_mod.load_shapes(settings.SHAPES_PATH)
        shape = kin_mod._get_shape(v, shapes)
        v.progress_m = max(0.0, shape.total_dist_m - 1.0)
        v.moving = True
        runtime.fleet._notify()
    except Exception as exc:
        log.warning("control: jump_to_terminal failed: %s", exc)


def _handle_set_progress(runtime: Any, vehicle_id: str, payload: dict[str, Any]) -> None:
    stop_id = payload.get("stop_id")
    if not isinstance(stop_id, str):
        log.warning("control: set_progress requires 'stop_id' string: %s", payload)
        return
    v = runtime.fleet.get(vehicle_id)
    if v is None:
        return

    from . import kinematics as kin_mod

    try:
        from django.conf import settings

        shapes, stops, _ = kin_mod.load_shapes(settings.SHAPES_PATH)
        shape = kin_mod._get_shape(v, shapes)
        target_stop = next((s for s in stops if s["stop_id"] == stop_id), None)
        if target_stop is None:
            log.warning("control: unknown stop_id %r", stop_id)
            return
        # Walk shape points to find closest to target stop.
        best_prog_m = 0.0
        best_dist = float("inf")
        for lat, lon, dist_km in shape.points:
            d = kin_mod.haversine_m(lat, lon, target_stop["lat"], target_stop["lon"])
            if d < best_dist:
                best_dist = d
                best_prog_m = dist_km * 1000.0
        v.progress_m = best_prog_m
        runtime.fleet._notify()
    except Exception as exc:
        log.warning("control: set_progress failed: %s", exc)


def _handle_inject_fault(runtime: Any, vehicle_id: str, payload: dict[str, Any]) -> None:
    kind = payload.get("kind")
    duration_ticks = payload.get("duration_ticks", 0)
    if kind not in _VALID_FAULT_KINDS:
        log.warning(
            "control: unknown fault kind %r; valid: %s", kind, _VALID_FAULT_KINDS
        )
        return
    if not isinstance(duration_ticks, int) or duration_ticks <= 0:
        log.warning("control: fault duration_ticks must be positive int: %s", payload)
        return
    v = runtime.fleet.get(vehicle_id)
    if v is None:
        return
    if not v._kin:
        from .kinematics import _init_kin

        _init_kin(v)
    v._kin["fault"] = kind
    v._kin["fault_ticks"] = duration_ticks


# ---------------------------------------------------------------------------
# Global handlers
# ---------------------------------------------------------------------------


def _handle_global_start_run(runtime: Any, payload: dict[str, Any]) -> None:
    vehicle_id = payload.get("vehicle_id")
    if not isinstance(vehicle_id, str):
        log.warning("control: start_run requires 'vehicle_id' string: %s", payload)
        return
    if runtime.fleet and runtime.fleet.get(vehicle_id) is not None:
        runtime.fleet.set_moving(vehicle_id, True)


def _handle_global_reload_schedule(runtime: Any, payload: dict[str, Any]) -> None:
    if runtime.scheduler is not None:
        try:
            runtime.scheduler.reload()
        except Exception as exc:
            log.warning("control: reload_schedule failed: %s", exc)
    else:
        log.info("control: reload_schedule received (scheduler not wired yet)")


# ---------------------------------------------------------------------------
# Dispatch tables
# ---------------------------------------------------------------------------

_PER_VEHICLE_DISPATCH: dict[str, Any] = {
    "transmit": _handle_transmit,
    "moving": _handle_moving,
    "speed": _handle_speed,
    "occupancy": _handle_occupancy,
    "dwell": _handle_dwell,
    "jump_to_terminal": _handle_jump_to_terminal,
    "set_progress": _handle_set_progress,
    "inject_fault": _handle_inject_fault,
}

_GLOBAL_DISPATCH: dict[str, Any] = {
    "start_run": _handle_global_start_run,
    "reload_schedule": _handle_global_reload_schedule,
}
