"""MQTT control subscriber: ``sim/control/+/+`` → fleet mutations.

Owned by Agent A (A2). Implements the topic surface defined in CONTRACTS.md §1.1.
All handlers are designed to be safe against malformed payloads — they log and
discard rather than raise.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

import orjson

from .fleet import FleetState

log = logging.getLogger(__name__)

# Handler dispatch table: knob name → handler method name
_PER_VEHICLE_KNOBS = {
    "transmit": "_handle_transmit",
    "moving": "_handle_moving",
    "speed": "_handle_speed",
    "occupancy": "_handle_occupancy",
    "dwell": "_handle_dwell",
    "jump_to_terminal": "_handle_jump_to_terminal",
    "set_progress": "_handle_set_progress",
    "inject_fault": "_handle_inject_fault",
}

_GLOBAL_KNOBS = {
    "start_run": "_handle_global_start_run",
    "reload_schedule": "_handle_global_reload_schedule",
}

_VALID_FAULT_KINDS = {"stale_ts", "out_of_bounds", "malformed"}


class Controller:
    """Subscribes to ``sim/control/+/+`` topics and mutates :class:`FleetState`.

    Wiring::

        controller = Controller(fleet_state, mqtt_client)
        controller.start()   # subscribes; routes on_message to handle()

    Cross-agent callbacks (set by Agent B at boot)::

        controller.on_reload_schedule = scheduler.reload
        controller.on_start_run = run_binder.start_run_for_vehicle
    """

    def __init__(self, fleet: FleetState, mqtt_client: Any) -> None:
        self.fleet = fleet
        self._client = mqtt_client
        # Agent B registers these at boot; no-op when None.
        self.on_reload_schedule: Callable[[], None] | None = None
        self.on_start_run: Callable[[str], None] | None = None

    # --- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        """Subscribe to control topics and wire the message callback."""
        self._client.subscribe("sim/control/+/+")
        self._client.on_message = self._on_message_raw
        log.info("Controller: subscribed to sim/control/+/+")

    def stop(self) -> None:
        self._client.unsubscribe("sim/control/+/+")
        log.info("Controller: unsubscribed")

    # --- entrypoint --------------------------------------------------------

    def _on_message_raw(self, client: Any, userdata: Any, message: Any) -> None:
        try:
            payload = orjson.loads(message.payload)
        except Exception as exc:
            log.warning("Controller: could not parse payload on %s: %s", message.topic, exc)
            return
        self.handle(message.topic, payload)

    def handle(self, topic: str, payload: dict[str, Any]) -> None:
        """Parse ``topic`` and dispatch to the matching ``_handle_*`` method.

        Topic shapes (locked in CONTRACTS.md §1.1)::

            sim/control/<vehicle_id>/<knob>
            sim/control/global/<knob>
        """
        parts = topic.split("/")
        # Expected: ["sim", "control", <vehicle_id|"global">, <knob>]
        if len(parts) != 4 or parts[0] != "sim" or parts[1] != "control":
            log.warning("Controller: unrecognised topic shape: %s", topic)
            return

        scope = parts[2]
        knob = parts[3]

        try:
            if scope == "global":
                handler_name = _GLOBAL_KNOBS.get(knob)
                if handler_name is None:
                    log.warning("Controller: unknown global knob %r", knob)
                    return
                getattr(self, handler_name)(payload)
            else:
                vehicle_id = scope
                if self.fleet.get(vehicle_id) is None:
                    log.warning("Controller: unknown vehicle_id %r", vehicle_id)
                    return
                handler_name = _PER_VEHICLE_KNOBS.get(knob)
                if handler_name is None:
                    log.warning("Controller: unknown per-vehicle knob %r", knob)
                    return
                getattr(self, handler_name)(vehicle_id, payload)
        except Exception as exc:
            log.warning("Controller: handler error on %s: %s", topic, exc)

    # --- per-vehicle knob handlers -----------------------------------------

    def _handle_transmit(self, vehicle_id: str, payload: dict[str, Any]) -> None:
        on = payload.get("on")
        if not isinstance(on, bool):
            log.warning("Controller: transmit payload missing bool 'on': %s", payload)
            return
        self.fleet.set_transmitting(vehicle_id, on)

    def _handle_moving(self, vehicle_id: str, payload: dict[str, Any]) -> None:
        on = payload.get("on")
        if not isinstance(on, bool):
            log.warning("Controller: moving payload missing bool 'on': %s", payload)
            return
        self.fleet.set_moving(vehicle_id, on)

    def _handle_speed(self, vehicle_id: str, payload: dict[str, Any]) -> None:
        value = payload.get("value")
        if value is not None and not isinstance(value, (int, float)):
            log.warning("Controller: speed value must be float or null: %s", payload)
            return
        self.fleet.set_speed_override(vehicle_id, float(value) if value is not None else None)

    def _handle_occupancy(self, vehicle_id: str, payload: dict[str, Any]) -> None:
        value = payload.get("value")
        if value is not None and not isinstance(value, int):
            log.warning("Controller: occupancy value must be int or null: %s", payload)
            return
        if value is not None and not (0 <= value <= 100):
            log.warning("Controller: occupancy value %d out of range [0,100]", value)
            return
        self.fleet.set_occupancy_override(vehicle_id, value)

    def _handle_dwell(self, vehicle_id: str, payload: dict[str, Any]) -> None:
        stop_id = payload.get("stop_id")
        ticks = payload.get("ticks", 0)
        if not isinstance(ticks, int) or ticks < 0:
            log.warning("Controller: dwell ticks must be non-negative int: %s", payload)
            return
        self.fleet.set_dwell_override(vehicle_id, stop_id, ticks)

    def _handle_jump_to_terminal(self, vehicle_id: str, payload: dict[str, Any]) -> None:
        v = self.fleet.get(vehicle_id)
        if v is None:
            return
        # Snap to just before terminal so next step advances it over the line.
        from .simulator import _get_shape, load_shapes
        from pathlib import Path
        try:
            shapes_path = Path(__file__).with_name("shapes.json")
            shapes, _, _ = load_shapes(shapes_path)
            shape = _get_shape(v, shapes)
            v.progress_m = max(0.0, shape.total_dist_m - 1.0)
            v.moving = True
            self.fleet._notify()
        except Exception as exc:
            log.warning("Controller: jump_to_terminal failed: %s", exc)

    def _handle_set_progress(self, vehicle_id: str, payload: dict[str, Any]) -> None:
        stop_id = payload.get("stop_id")
        if not isinstance(stop_id, str):
            log.warning("Controller: set_progress requires 'stop_id' string: %s", payload)
            return
        v = self.fleet.get(vehicle_id)
        if v is None:
            return
        from .simulator import _get_shape, _nearest_stop, haversine_m, interpolate, load_shapes
        from pathlib import Path
        try:
            shapes_path = Path(__file__).with_name("shapes.json")
            shapes, stops, _ = load_shapes(shapes_path)
            shape = _get_shape(v, shapes)
            # Find progress_m of the stop that matches stop_id.
            target_stop = next((s for s in stops if s["stop_id"] == stop_id), None)
            if target_stop is None:
                log.warning("Controller: unknown stop_id %r", stop_id)
                return
            # Walk shape points to find closest to target stop.
            best_prog_m = 0.0
            best_dist = float("inf")
            for lat, lon, dist_km in shape.points:
                d = haversine_m(lat, lon, target_stop["lat"], target_stop["lon"])
                if d < best_dist:
                    best_dist = d
                    best_prog_m = dist_km * 1000.0
            v.progress_m = best_prog_m
            self.fleet._notify()
        except Exception as exc:
            log.warning("Controller: set_progress failed: %s", exc)

    def _handle_inject_fault(self, vehicle_id: str, payload: dict[str, Any]) -> None:
        kind = payload.get("kind")
        duration_ticks = payload.get("duration_ticks", 0)
        if kind not in _VALID_FAULT_KINDS:
            log.warning("Controller: unknown fault kind %r; valid: %s", kind, _VALID_FAULT_KINDS)
            return
        if not isinstance(duration_ticks, int) or duration_ticks <= 0:
            log.warning("Controller: fault duration_ticks must be positive int: %s", payload)
            return
        v = self.fleet.get(vehicle_id)
        if v is None:
            return
        v._kin["fault"] = kind
        v._kin["fault_ticks"] = duration_ticks

    # --- global knob handlers ----------------------------------------------

    def _handle_global_start_run(self, payload: dict[str, Any]) -> None:
        vehicle_id = payload.get("vehicle_id")
        if not isinstance(vehicle_id, str):
            log.warning("Controller: start_run requires 'vehicle_id' string: %s", payload)
            return
        if self.on_start_run is not None:
            try:
                self.on_start_run(vehicle_id)
            except Exception as exc:
                log.warning("Controller: on_start_run callback failed: %s", exc)
        else:
            # Fallback: just flip moving=True (no run binding).
            if self.fleet.get(vehicle_id) is not None:
                self.fleet.set_moving(vehicle_id, True)

    def _handle_global_reload_schedule(self, payload: dict[str, Any]) -> None:
        if self.on_reload_schedule is not None:
            try:
                self.on_reload_schedule()
            except Exception as exc:
                log.warning("Controller: on_reload_schedule callback failed: %s", exc)
        else:
            log.info("Controller: reload_schedule received (scheduler not wired yet)")
