"""Fleet roster + per-vehicle state container.

Ported verbatim from sim/fleet.py. The ``on_change`` callback list is preserved
so that broadcast.py can subscribe to fleet mutations.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable


@dataclass
class Vehicle:
    """Per-vehicle simulator state."""

    vehicle_id: str
    route_id: str
    default_shape_id: str

    transmitting: bool = False
    moving: bool = False
    speed_override: float | None = None
    occupancy_override: int | None = None
    dwell_override: dict[str, Any] | None = None  # {"stop_id": str | None, "ticks": int}

    bound_run_id: str | None = None
    bound_trip_id: str | None = None
    bound_shape_id: str | None = None
    terminal_stop_id: str | None = None  # cached at bind_run for terminal stop detection
    lifecycle_state: str | None = None

    progress_m: float = 0.0
    last_state_change_at: datetime | None = None

    # Internal kinematics scratch space; populated by kinematics.py _init_kin.
    # Keys: speed, occupancy_pct, dwell_remaining, stop_sequence, last_stop_id,
    #       post_run_idle_remaining_s, fault, fault_ticks.
    _kin: dict[str, Any] = field(default_factory=dict, repr=False)


# ---------------------------------------------------------------------------
# Fixed fleet roster
# ---------------------------------------------------------------------------
FLEET: list[Vehicle] = [
    Vehicle(vehicle_id="unit-01", route_id="bUCR_L1", default_shape_id="hacia_artes"),
    Vehicle(vehicle_id="unit-02", route_id="bUCR_L1", default_shape_id="hacia_educacion"),
    Vehicle(vehicle_id="unit-03", route_id="bUCR_L1", default_shape_id="desde_artes_sin_milla"),
    Vehicle(vehicle_id="unit-04", route_id="bUCR_L2", default_shape_id="desde_artes_con_milla"),
    Vehicle(vehicle_id="unit-05", route_id="bUCR_L2", default_shape_id="desde_educacion_con_milla"),
    Vehicle(vehicle_id="unit-06", route_id="bUCR_L2", default_shape_id="desde_artes_con_milla"),
]


def _make_fleet() -> list[Vehicle]:
    """Return fresh Vehicle instances matching the roster."""
    return [
        Vehicle(vehicle_id="unit-01", route_id="bUCR_L1", default_shape_id="hacia_artes"),
        Vehicle(vehicle_id="unit-02", route_id="bUCR_L1", default_shape_id="hacia_educacion"),
        Vehicle(vehicle_id="unit-03", route_id="bUCR_L1", default_shape_id="desde_artes_sin_milla"),
        Vehicle(vehicle_id="unit-04", route_id="bUCR_L2", default_shape_id="desde_artes_con_milla"),
        Vehicle(
            vehicle_id="unit-05",
            route_id="bUCR_L2",
            default_shape_id="desde_educacion_con_milla",
        ),
        Vehicle(vehicle_id="unit-06", route_id="bUCR_L2", default_shape_id="desde_artes_con_milla"),
    ]


class FleetState:
    """Mutable container holding all :class:`Vehicle` instances by id.

    All mutation paths flow through these methods so that subscribers on
    ``on_change`` can react to every fleet state change.

    Usage::

        fleet = FleetState()
        fleet.on_change.append(my_callback)    # subscribe
        fleet.set_transmitting("unit-01", True)  # fires callbacks
    """

    def __init__(self, vehicles: list[Vehicle] | None = None) -> None:
        source = vehicles if vehicles is not None else _make_fleet()
        self._by_id: dict[str, Vehicle] = {v.vehicle_id: v for v in source}
        # Append callables here to subscribe to any fleet state change.
        self.on_change: list[Callable[[], None]] = []

    # --- lookups -----------------------------------------------------------

    def get(self, vehicle_id: str) -> Vehicle | None:
        return self._by_id.get(vehicle_id)

    def all(self) -> list[Vehicle]:
        return list(self._by_id.values())

    # --- mutators (controller-facing) --------------------------------------

    def set_transmitting(self, vehicle_id: str, on: bool) -> None:
        v = self._require(vehicle_id)
        v.transmitting = on
        v.last_state_change_at = datetime.now(timezone.utc)
        self._notify()

    def set_moving(self, vehicle_id: str, on: bool) -> None:
        v = self._require(vehicle_id)
        v.moving = on
        v.last_state_change_at = datetime.now(timezone.utc)
        self._notify()

    def set_speed_override(self, vehicle_id: str, value: float | None) -> None:
        v = self._require(vehicle_id)
        v.speed_override = value
        self._notify()

    def set_occupancy_override(self, vehicle_id: str, value: int | None) -> None:
        v = self._require(vehicle_id)
        v.occupancy_override = value
        self._notify()

    def set_dwell_override(
        self, vehicle_id: str, stop_id: str | None, ticks: int
    ) -> None:
        v = self._require(vehicle_id)
        v.dwell_override = {"stop_id": stop_id, "ticks": ticks}
        if v._kin:
            v._kin["dwell_remaining"] = ticks
            if stop_id:
                v._kin["last_stop_id"] = stop_id
        self._notify()

    # --- mutators (run_binder-facing) --------------------------------------

    def bind_run(
        self,
        vehicle_id: str,
        run_id: str,
        trip_id: str,
        shape_id: str,
        terminal_stop_id: str | None = None,
    ) -> None:
        v = self._require(vehicle_id)
        v.bound_run_id = run_id
        v.bound_trip_id = trip_id
        v.bound_shape_id = shape_id
        v.terminal_stop_id = terminal_stop_id
        v.last_state_change_at = datetime.now(timezone.utc)
        self._notify()

    def unbind_run(self, vehicle_id: str) -> None:
        v = self._require(vehicle_id)
        v.bound_run_id = None
        v.bound_trip_id = None
        v.bound_shape_id = None
        v.terminal_stop_id = None
        v.lifecycle_state = None
        v.progress_m = 0.0
        v.transmitting = False
        v.moving = False
        v.last_state_change_at = datetime.now(timezone.utc)
        self._notify()

    def set_lifecycle_state(self, vehicle_id: str, state: str | None) -> None:
        v = self._require(vehicle_id)
        v.lifecycle_state = state
        v.last_state_change_at = datetime.now(timezone.utc)
        self._notify()

    # --- snapshot ----------------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        """Return the JSON-serializable fleet snapshot."""
        now = datetime.now(timezone.utc).isoformat()
        return {
            "vehicles": [
                {
                    "vehicle_id": v.vehicle_id,
                    "route_id": v.route_id,
                    "transmitting": v.transmitting,
                    "moving": v.moving,
                    "speed_override": v.speed_override,
                    "occupancy_override": v.occupancy_override,
                    "bound_run_id": v.bound_run_id,
                    "bound_trip_id": v.bound_trip_id,
                    "bound_shape_id": v.bound_shape_id,
                    "lifecycle_state": v.lifecycle_state,
                    "progress_m": round(v.progress_m, 1),
                    "last_state_change_at": (
                        v.last_state_change_at.isoformat()
                        if v.last_state_change_at
                        else None
                    ),
                }
                for v in self._by_id.values()
            ],
            "published_at": now,
        }

    # --- private helpers ---------------------------------------------------

    def _require(self, vehicle_id: str) -> Vehicle:
        v = self._by_id.get(vehicle_id)
        if v is None:
            raise KeyError(f"Unknown vehicle: {vehicle_id!r}")
        return v

    def _notify(self) -> None:
        for cb in self.on_change:
            cb()
