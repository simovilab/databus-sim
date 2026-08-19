"""Vehicle kinematics: shape loading, interpolation, stepping, payload building.

Ported from sim/simulator.py (kinematics half). The sink (MQTT publish +
Channels push) lives in runtime.tick_loop; this module is purely transport-agnostic.

Key public surface:
    load_shapes(path)               → (shapes_by_id, stops, routes)
    _init_kin(v)                    → populate v._kin scratch space (idempotent)
    _get_shape(v, shapes)           → resolve bound or default shape
    step_vehicle(v, dt, shape, stops) → advance kinematics by dt seconds
    build_vehicle_payloads(v, shape, stops) → pure payload builder; None if not transmitting
"""

from __future__ import annotations

import json
import logging
import math
import os
import random
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .fleet import Vehicle

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level constants (ported verbatim from sim/simulator.py)
# ---------------------------------------------------------------------------

# Speed bounds for random drift (m/s).
MIN_SPEED = 3.0   # ~11 km/h
MAX_SPEED = 12.0  # ~43 km/h

# Auto-dwell at stops: how many ticks a bus pauses when it enters a stop zone.
STOP_DWELL_TICKS = 3

# Radius within which a vehicle is considered "at" a stop (drives auto-dwell).
STOP_RADIUS_M = 20.0

# Seconds a vehicle remains transmitting after reaching the terminal stop.
# Read from env at import time (same as the original).
POST_RUN_IDLE_S: int = int(os.getenv("POST_RUN_IDLE_S", "30"))


# ---------------------------------------------------------------------------
# Shape data
# ---------------------------------------------------------------------------

@dataclass
class Shape:
    shape_id: str
    points: list[tuple[float, float, float]]  # (lat, lon, dist_km)

    @property
    def total_dist_m(self) -> float:
        return self.points[-1][2] * 1000.0


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    x = math.sin(dl) * math.cos(p2)
    y = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(x, y)) + 360) % 360


def interpolate(shape: Shape, progress_m: float) -> tuple[float, float, float]:
    """Return (lat, lon, bearing_deg) at ``progress_m`` along ``shape``."""
    pts = shape.points
    progress_km = max(0.0, min(progress_m / 1000.0, pts[-1][2]))

    lo, hi = 0, len(pts) - 1
    while lo < hi:
        mid = (lo + hi) // 2
        if pts[mid][2] < progress_km:
            lo = mid + 1
        else:
            hi = mid
    i = max(1, lo)

    lat1, lon1, d1 = pts[i - 1]
    lat2, lon2, d2 = pts[i]
    span = max(d2 - d1, 1e-9)
    t = (progress_km - d1) / span
    lat = lat1 + (lat2 - lat1) * t
    lon = lon1 + (lon2 - lon1) * t
    return lat, lon, bearing_deg(lat1, lon1, lat2, lon2)


def load_shapes(
    path: Path | str,
) -> tuple[dict[str, Shape], list[dict[str, Any]], list[dict[str, Any]]]:
    """Return (shapes_by_id, stops_list, routes_list)."""
    data = json.loads(Path(path).read_text())
    shapes = {
        sid: Shape(shape_id=sid, points=[(lat, lon, dist) for lat, lon, dist in pts])
        for sid, pts in data["shapes"].items()
    }
    stops: list[dict[str, Any]] = data["stops"]
    routes: list[dict[str, Any]] = data["routes"]
    return shapes, stops, routes


def _nearest_stop(
    stops: list[dict[str, Any]], lat: float, lon: float
) -> tuple[dict[str, Any] | None, float]:
    best: dict[str, Any] | None = None
    best_d = float("inf")
    for s in stops:
        d = haversine_m(lat, lon, s["lat"], s["lon"])
        if d < best_d:
            best, best_d = s, d
    return best, best_d


# ---------------------------------------------------------------------------
# Kinematics
# ---------------------------------------------------------------------------

def _init_kin(v: Vehicle) -> None:
    """Populate ``v._kin`` with initial kinematic scratch values (idempotent)."""
    v._kin.setdefault("speed", random.uniform(MIN_SPEED, MAX_SPEED))
    v._kin.setdefault("occupancy_pct", random.randint(20, 70))
    v._kin.setdefault("dwell_remaining", 0)
    v._kin.setdefault("stop_sequence", 1)
    v._kin.setdefault("last_stop_id", "")
    v._kin.setdefault("post_run_idle_remaining_s", 0.0)
    v._kin.setdefault("fault", None)
    v._kin.setdefault("fault_ticks", 0)


def _get_shape(v: Vehicle, shapes: dict[str, Shape]) -> Shape:
    shape_id = v.bound_shape_id or v.default_shape_id
    return shapes[shape_id]


def step_vehicle(
    v: Vehicle, dt: float, shape: Shape, stops: list[dict[str, Any]]
) -> None:
    """Advance vehicle kinematics by ``dt`` seconds.

    Rules (in order):
    1. If dwelling, decrement dwell counter and freeze.
    2. If not moving, run the post-run idle countdown; stop transmitting when done.
    3. Advance speed (override or drift) and progress_m.
    4. Clamp at terminal: set moving=False, start idle countdown.
    5. Drift occupancy and trigger auto-dwell if entering a stop zone.
    """
    if not v._kin:
        _init_kin(v)
    kin = v._kin

    # 1. Dwell countdown.
    if kin["dwell_remaining"] > 0:
        kin["dwell_remaining"] -= 1
        return

    # 2. Not moving: run idle countdown.
    if not v.moving:
        idle = kin["post_run_idle_remaining_s"]
        if idle > 0:
            remaining = max(0.0, idle - dt)
            kin["post_run_idle_remaining_s"] = remaining
            if remaining == 0.0:
                v.transmitting = False
        return

    # 3. Advance speed.
    if v.speed_override is not None:
        kin["speed"] = float(v.speed_override)
    else:
        kin["speed"] = max(
            MIN_SPEED, min(MAX_SPEED, kin["speed"] + random.uniform(-1.0, 1.0))
        )

    v.progress_m += kin["speed"] * dt

    # 4. Terminal clamp.
    if v.progress_m >= shape.total_dist_m:
        v.progress_m = shape.total_dist_m
        v.moving = False
        kin["post_run_idle_remaining_s"] = float(POST_RUN_IDLE_S)
        return

    # 5. Occupancy drift + auto-dwell at stops.
    kin["occupancy_pct"] = max(0, min(100, kin["occupancy_pct"] + random.randint(-4, 4)))

    lat, lon, _ = interpolate(shape, v.progress_m)
    ns, dist = _nearest_stop(stops, lat, lon)
    if ns and dist < STOP_RADIUS_M and ns["stop_id"] != kin["last_stop_id"]:
        kin["last_stop_id"] = ns["stop_id"]
        kin["stop_sequence"] += 1
        kin["dwell_remaining"] = STOP_DWELL_TICKS


def build_vehicle_payloads(
    v: Vehicle,
    shape: Shape,
    stops: list[dict[str, Any]],
) -> dict[str, dict[str, Any]] | None:
    """Pure payload builder — returns ``{"position":{}, "occupancy":{}}``
    or ``None`` when ``v.transmitting`` is False.

    Per the databus MQTT contract the sim is a *dumb sensor emitter*: it
    publishes only what a real vehicle can sense by itself. The ``progression``
    leaf (map-matched stop status) and the ``occupancy_status`` enum are
    server-owned — databus recomputes them — so neither is put on the wire.
    """
    if not v.transmitting:
        return None

    if not v._kin:
        _init_kin(v)
    kin = v._kin

    ts = int(datetime.now(timezone.utc).timestamp())
    lat, lon, brg = interpolate(shape, v.progress_m)

    # Fault injection — mutate outgoing data for the next N ticks.
    fault: str | None = kin.get("fault")
    fault_ticks: int = kin.get("fault_ticks", 0)
    active_fault = bool(fault and fault_ticks > 0)
    if active_fault:
        if fault == "stale_ts":
            ts -= 120
        elif fault == "out_of_bounds":
            lat, lon = 0.0, 0.0
        kin["fault_ticks"] = fault_ticks - 1
        if kin["fault_ticks"] <= 0:
            kin["fault"] = None

    # Speed: zero while not moving or while dwelling.
    dwell_remaining: int = kin.get("dwell_remaining", 0)
    effectively_stopped = (not v.moving) or (dwell_remaining > 0)
    speed = 0.0 if effectively_stopped else round(kin.get("speed", 0.0), 2)

    # Position payload.
    position: dict[str, Any] = {
        "timestamp": ts,
        "latitude": round(lat, 6),
        "longitude": round(lon, 6),
        "bearing": round(brg, 1),
        "speed": speed,
        "odometer": round(v.progress_m, 1),
    }
    if active_fault and fault == "malformed":
        position.pop("speed", None)

    # Occupancy payload. Raw measurement only — databus recomputes the
    # ``occupancy_status`` enum server-side and discards any value the edge sends.
    occ_pct = (
        v.occupancy_override
        if v.occupancy_override is not None
        else kin.get("occupancy_pct", 40)
    )
    occupancy: dict[str, Any] = {
        "timestamp": ts,
        "occupancy_percentage": occ_pct,
    }

    return {
        "position": position,
        "occupancy": occupancy,
    }
