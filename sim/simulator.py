#!/usr/bin/env python
"""MQTT telemetry simulator — UCR bUCR_L1 / bUCR_L2 routes.

Publishes vehicle position, progression, and occupancy to an MQTT broker at
``transit/vehicle/<vehicle_id>/{position,progression,occupancy}``.

Key behaviors (A1):
- Vehicles only advance when ``moving=True``.
- Vehicles only publish when ``transmitting=True``.
- No endpoint reversal: direction is always forward along the shape.
- End-of-run idle: after reaching the terminal stop, vehicle holds position
  for ``POST_RUN_IDLE_S`` seconds (still transmitting), then stops transmitting.
- ``data`` topic is **not** published (databus consumer does not subscribe to it).

Kinematics scratch state lives in ``Vehicle._kin`` (see ``_init_kin``).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import math
import os
import random
import signal
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import orjson
import paho.mqtt.client as mqtt

from .fleet import FleetState, Vehicle
from .controller import Controller
from .state_publisher import StatePublisher
from .databus_client import DatabusClient
from .redis_client import RedisClient
from .run_binder import RunBinder
from .scheduler import Scheduler
from .http_control import HttpControl

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
MQTT_HOST = os.getenv("MQTT_HOST", "localhost")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
MQTT_TOPIC_ROOT = os.getenv("MQTT_TOPIC_ROOT", "transit/vehicle")

DEFAULT_INTERVAL = 2.0

# Speed bounds for random drift (m/s).
MIN_SPEED = 3.0   # ~11 km/h
MAX_SPEED = 12.0  # ~43 km/h

# Auto-dwell at stops: how many ticks a bus pauses when it enters a stop zone.
STOP_DWELL_TICKS = 3

# Radius within which a vehicle is considered "at" a stop (STOPPED_AT status).
STOP_RADIUS_M = 20.0

# Radius for INCOMING_AT — approaching but not yet at stop.
INCOMING_AT_RADIUS_M = 50.0

# Seconds a vehicle remains transmitting after reaching the terminal stop.
POST_RUN_IDLE_S = int(os.getenv("POST_RUN_IDLE_S", "30"))

RUNNING = True

log = logging.getLogger(__name__)


def on_sigterm(signum: int, frame: Any) -> None:
    global RUNNING
    log.info("Shutdown signal received.")
    RUNNING = False


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
    path: Path,
) -> tuple[dict[str, Shape], list[dict[str, Any]], list[dict[str, Any]]]:
    """Return (shapes_by_id, stops_list, routes_list)."""
    data = json.loads(path.read_text())
    shapes = {
        sid: Shape(shape_id=sid, points=[(lat, lon, dist) for lat, lon, dist in pts])
        for sid, pts in data["shapes"].items()
    }
    stops: list[dict[str, Any]] = data["stops"]
    routes: list[dict[str, Any]] = data["routes"]
    return shapes, stops, routes


def occupancy_status(pct: int) -> str:
    if pct < 20:
        return "MANY_SEATS_AVAILABLE"
    if pct < 50:
        return "FEW_SEATS_AVAILABLE"
    if pct < 80:
        return "STANDING_ROOM_ONLY"
    return "FULL"


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

    # 1. Dwell countdown (auto-stop or operator override via set_dwell_override).
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


def publish_vehicle(
    client: mqtt.Client,
    v: Vehicle,
    shape: Shape,
    stops: list[dict[str, Any]],
) -> None:
    """Publish position/progression/occupancy for vehicle ``v``.

    Skips entirely when ``v.transmitting`` is False.
    Does NOT publish the ``data`` topic (databus consumer does not subscribe to it).

    Progression status rules:
    - STOPPED_AT   : effectively stopped (not moving OR dwelling) AND within STOP_RADIUS_M.
    - INCOMING_AT  : moving AND within INCOMING_AT_RADIUS_M (but outside STOP_RADIUS_M).
    - IN_TRANSIT_TO: all other cases.
    """
    if not v.transmitting:
        return

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

    _publish(client, v.vehicle_id, "position", position)

    # Progression payload.
    ns, dist = _nearest_stop(stops, lat, lon)
    at_terminal = (
        v.bound_shape_id is not None
        and v.terminal_stop_id is not None
        and v.progress_m >= shape.total_dist_m - 1.0
    )
    if effectively_stopped and (at_terminal or (ns and dist < STOP_RADIUS_M)):
        status = "STOPPED_AT"
    elif (not effectively_stopped) and ns and dist < INCOMING_AT_RADIUS_M:
        status = "INCOMING_AT"
    else:
        status = "IN_TRANSIT_TO"

    shape_id = v.bound_shape_id or v.default_shape_id
    # When at the GTFS terminal of the bound trip, force-publish the cached
    # terminal_stop_id so databus's is_at_terminal_stop guard can match.
    # Otherwise the nearest stop in shapes.json may be a different route's stop.
    if at_terminal and status == "STOPPED_AT":
        stop_id_field = v.terminal_stop_id
    elif ns and status in ("STOPPED_AT", "INCOMING_AT"):
        stop_id_field = ns["stop_id"]
    else:
        stop_id_field = ""

    _publish(client, v.vehicle_id, "progression", {
        "timestamp": ts,
        "current_stop_sequence": kin.get("stop_sequence", 1),
        "stop_id": stop_id_field,
        "current_status": status,
        "congestion_level": "RUNNING_SMOOTHLY",
        "route_id": v.route_id,
        "shape_id": shape_id,
    })

    # Occupancy payload.
    occ_pct = (
        v.occupancy_override
        if v.occupancy_override is not None
        else kin.get("occupancy_pct", 40)
    )
    _publish(client, v.vehicle_id, "occupancy", {
        "timestamp": ts,
        "occupancy_status": occupancy_status(occ_pct),
        "occupancy_percentage": occ_pct,
    })


def _publish(
    client: mqtt.Client, vehicle_id: str, leaf: str, payload: dict[str, Any]
) -> None:
    topic = f"{MQTT_TOPIC_ROOT}/{vehicle_id}/{leaf}"
    client.publish(topic, orjson.dumps(payload), qos=0)


# ---------------------------------------------------------------------------
# Async services (B1/B2): HTTP control, run binder, scheduler
# ---------------------------------------------------------------------------

def _run_async_services(
    fleet: FleetState,
    state_pub: StatePublisher,
    controller: Controller,
    schedule_path: Path,
    http_port: int,
) -> None:
    """Boot databus/redis/binder/scheduler/http_control inside their own loop."""
    asyncio.run(
        _async_services_main(fleet, state_pub, controller, schedule_path, http_port)
    )


async def _async_services_main(
    fleet: FleetState,
    state_pub: StatePublisher,
    controller: Controller,
    schedule_path: Path,
    http_port: int,
) -> None:
    import uvicorn

    async with DatabusClient() as databus, RedisClient() as redis:
        binder = RunBinder(fleet, redis)
        scheduler = Scheduler(
            path=schedule_path,
            fleet=fleet,
            databus=databus,
            binder=binder,
            state_publisher=state_pub,
        )
        try:
            scheduler.load()
        except Exception as exc:
            log.warning("scheduler.load failed (%s) — empty schedule", exc)

        controller.on_reload_schedule = scheduler.reload
        controller.on_start_run = lambda vid: fleet.set_moving(vid, True)

        http = HttpControl(
            fleet=fleet,
            scheduler=scheduler,
            redis_client=redis,
            binder=binder,
            databus=databus,
        )
        config = uvicorn.Config(
            http.app, host="0.0.0.0", port=http_port, log_level="info", access_log=False
        )
        server = uvicorn.Server(config)

        async with asyncio.TaskGroup() as tg:
            tg.create_task(server.serve(), name="http_control")
            tg.create_task(binder.poll_loop(), name="run_binder")
            tg.create_task(scheduler.run_loop(), name="scheduler")


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run(
    interval: float,
    shapes_path: Path,
    per_route: int,
    stop_vehicles: set[str],
    only_vehicles: set[str],
    drop_rate: int,
    stop_all_after: int,
) -> None:
    signal.signal(signal.SIGINT, on_sigterm)
    signal.signal(signal.SIGTERM, on_sigterm)

    if per_route > 3:
        log.warning(
            "--per-route %d ignored: fleet is fixed at 6 vehicles (3 per route).",
            per_route,
        )

    shapes, stops, _ = load_shapes(shapes_path)
    fleet = FleetState()

    for v in fleet.all():
        _init_kin(v)
        if v.vehicle_id in stop_vehicles:
            # Initial-state override: controller takes over at runtime.
            v.transmitting = False

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="ucr-simulator")
    log.info("Connecting to MQTT %s:%d …", MQTT_HOST, MQTT_PORT)
    client.connect(MQTT_HOST, MQTT_PORT, keepalive=30)
    client.loop_start()

    # Wire A2: controller + state publisher.
    # Both take the raw paho client; StatePublisher serialises its own payloads.
    controller = Controller(fleet, client)
    state_pub = StatePublisher(fleet, client)
    state_pub.start()
    controller.start()

    # Wire B1/B2: databus client, redis client, run binder, scheduler, http control.
    # Run them in a background thread with its own asyncio loop so the sync
    # publish loop below can keep ticking.
    schedule_path = Path(os.getenv("SCHEDULE_PATH", "/app/schedule.yaml"))
    http_port = int(os.getenv("SIM_HTTP_PORT", "8081"))
    async_thread = threading.Thread(
        target=_run_async_services,
        args=(fleet, state_pub, controller, schedule_path, http_port),
        daemon=True,
        name="sim-async",
    )
    async_thread.start()

    log.info("Simulator running: %d vehicles, %.1fs tick", len(fleet.all()), interval)
    cycle = 0
    try:
        while RUNNING:
            cycle += 1
            if stop_all_after and cycle > stop_all_after:
                time.sleep(interval)
                continue
            for v in fleet.all():
                if only_vehicles and v.vehicle_id not in only_vehicles:
                    continue
                if drop_rate and random.randint(1, 100) <= drop_rate:
                    continue
                shape = _get_shape(v, shapes)
                step_vehicle(v, interval, shape, stops)
                publish_vehicle(client, v, shape, stops)
            if cycle % 10 == 0:
                log.info("[cycle %d] ticked %d vehicles", cycle, len(fleet.all()))
            time.sleep(interval)
    finally:
        controller.stop()
        state_pub.stop()
        client.loop_stop()
        client.disconnect()
        log.info("Simulator stopped.")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--interval", type=float, default=DEFAULT_INTERVAL)
    parser.add_argument("--shapes", default=str(Path(__file__).with_name("shapes.json")))
    parser.add_argument(
        "--per-route",
        type=int,
        default=3,
        help="Advisory: fleet is fixed at 3 vehicles per route. Values >3 are warned and ignored.",
    )
    parser.add_argument("--stop-vehicle", action="append", default=[],
                        dest="stop_vehicle")
    parser.add_argument("--only-vehicle", action="append", default=[],
                        dest="only_vehicle")
    parser.add_argument("--random-drop-rate", type=int, default=0)
    parser.add_argument("--stop-all-after", type=int, default=0)
    args = parser.parse_args()

    if not 0 <= args.random_drop_rate <= 100:
        print("--random-drop-rate must be in [0, 100]", file=sys.stderr)
        sys.exit(1)

    run(
        interval=args.interval,
        shapes_path=Path(args.shapes),
        per_route=args.per_route,
        stop_vehicles=set(args.stop_vehicle),
        only_vehicles=set(args.only_vehicle),
        drop_rate=args.random_drop_rate,
        stop_all_after=args.stop_all_after,
    )


if __name__ == "__main__":
    main()
