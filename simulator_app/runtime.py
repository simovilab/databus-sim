"""Runtime singleton and lifespan lifecycle for the SIMOVI simulator.

start_runtime() wires all services:
  1. Load shapes from settings.SHAPES_PATH
  2. Build FleetState; _init_kin each vehicle
  3. Connect paho MQTT client (loop_start in its own thread, non-blocking)
  4. Open DatabusClient (httpx)
  5. Build RunBinder + Scheduler; scheduler.load() (guard failures → empty schedule)
  6. Wire fleet.on_change → broadcast_fleet (throttled)
  7. asyncio.create_task() for tick_loop, binder.poll_loop, scheduler.run_loop
  8. Populate _runtime fields

stop_runtime() cancels all tasks, stops paho, closes DatabusClient.

Single-process invariant: exactly ONE daphne worker (see PLAN §2).
"""

from __future__ import annotations

import asyncio
import logging
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Runtime holder
# ---------------------------------------------------------------------------


@dataclass
class Runtime:
    """Module-global container for all live simulator objects.

    Fields:
        fleet        — FleetState singleton (domain/fleet.py)
        scheduler    — Scheduler instance (services/scheduler.py)
        binder       — RunBinder instance (services/run_binder.py)
        databus      — DatabusClient (services/databus_client.py); async httpx
        mqtt         — paho.mqtt.client.Client (runs in its own thread via loop_start)
        channel_layer — channels.layers.InMemoryChannelLayer (from Django CHANNEL_LAYERS)
        _tasks       — internal list of asyncio.Task objects created at startup
        _shapes      — shapes dict (keyed by shape_id)
        _stops       — stops list
        _mqtt_topic_root — MQTT topic prefix
    """

    fleet: Any | None = None
    scheduler: Any | None = None
    binder: Any | None = None
    databus: Any | None = None
    mqtt: Any | None = None
    channel_layer: Any | None = None
    _tasks: list[asyncio.Task[Any]] = field(default_factory=list)
    _shapes: Any | None = None
    _stops: Any | None = None
    _mqtt_topic_root: str = "transit/vehicle"
    _progression_geom: dict[str, list[dict]] = field(default_factory=dict)


# Module-level singleton — created once; never replaced.
_runtime: Runtime = Runtime()


def get_runtime() -> Runtime:
    """Return the module-global Runtime instance."""
    return _runtime


# ---------------------------------------------------------------------------
# Lifespan hooks
# ---------------------------------------------------------------------------


async def start_runtime() -> None:
    """Start all simulator background services.

    Must be safe to call even when databus / MQTT broker is absent
    (log failures, don't raise).
    """
    from django.conf import settings
    from channels.layers import get_channel_layer

    from simulator_app.domain.fleet import FleetState
    from simulator_app.domain.kinematics import load_shapes, _init_kin
    from simulator_app.services.databus_client import DatabusClient
    from simulator_app.services.run_binder import RunBinder
    from simulator_app.services.scheduler import Scheduler
    import simulator_app.realtime.broadcast as _broadcast_mod_startup

    log.info("runtime.start_runtime(): loading shapes from %s", settings.SHAPES_PATH)

    # 1. Load shapes
    routes: list[Any] = []
    try:
        shapes, stops, routes = load_shapes(Path(settings.SHAPES_PATH))
    except Exception as exc:
        log.error("runtime: failed to load shapes: %s", exc)
        shapes, stops = {}, []

    # 2. Build FleetState and init kinematics
    fleet = FleetState()
    stop_vehicles: set[str] = getattr(settings, "SIM_STOP_VEHICLES", set())
    for v in fleet.all():
        _init_kin(v)
        if v.vehicle_id in stop_vehicles:
            v.transmitting = False

    _runtime.fleet = fleet
    _runtime._shapes = shapes
    _runtime._stops = stops
    _runtime._mqtt_topic_root = settings.MQTT_TOPIC_ROOT

    # 1b. Build progression geometry (shape_id → ordered stop list).
    #     Requires raw shapes.json point data, not the Shape objects.
    try:
        import json as _json
        from simulator_app.domain.progression.shapes import build_route_geometry as _brg

        _raw = _json.loads(Path(settings.SHAPES_PATH).read_text())
        _prog_geom: dict[str, list[dict]] = {}
        for _route in routes:
            _g = _brg(_route, _raw["shapes"], _raw["stops"])
            for _s in _g["shapes"]:
                _prog_geom[_s["shape_id"]] = _s["stops"]
        _runtime._progression_geom = _prog_geom
        log.info(
            "runtime: progression geometry built for %d shapes",
            len(_prog_geom),
        )
    except Exception as exc:
        log.warning("runtime: could not build progression geometry: %s — progression will be IN_TRANSIT_TO", exc)
        _runtime._progression_geom = {}

    # 3. Connect paho MQTT (best-effort; broker may be absent)
    mqtt_client = None
    try:
        import paho.mqtt.client as mqtt

        mqtt_client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2, client_id="ucr-simulator"
        )
        mqtt_client.connect(settings.MQTT_HOST, settings.MQTT_PORT, keepalive=30)
        mqtt_client.loop_start()
        log.info(
            "runtime: paho MQTT connected to %s:%d", settings.MQTT_HOST, settings.MQTT_PORT
        )
    except Exception as exc:
        log.warning(
            "runtime: paho MQTT connection failed (%s) — telemetry publishes will be skipped",
            exc,
        )
        mqtt_client = None

    _runtime.mqtt = mqtt_client

    # 4. Open DatabusClient
    databus = DatabusClient(base_url=settings.DATABUS_BASE_URL)
    try:
        await databus.open()
        log.info("runtime: DatabusClient opened at %s", settings.DATABUS_BASE_URL)
    except Exception as exc:
        log.warning("runtime: DatabusClient open failed: %s", exc)

    _runtime.databus = databus

    # 5. Build RunBinder + Scheduler
    binder = RunBinder(
        fleet=fleet,
        databus=databus,
        poll_interval_s=settings.RUN_POLL_INTERVAL_S,
    )
    _runtime.binder = binder

    schedule_path = Path(settings.SCHEDULE_PATH)
    scheduler = Scheduler(
        path=schedule_path,
        fleet=fleet,
        databus=databus,
        binder=binder,
        tick_s=settings.SCHEDULER_TICK_S,
    )
    try:
        scheduler.load()
        log.info("runtime: scheduler loaded %d entries", len(scheduler._entries))
    except Exception as exc:
        log.warning("runtime: scheduler.load failed (%s) — empty schedule", exc)

    _runtime.scheduler = scheduler

    # 6. Wire fleet.on_change → broadcast_fleet (throttled)
    def _on_fleet_change() -> None:
        asyncio.ensure_future(_broadcast_mod_startup.broadcast_fleet(fleet.snapshot()))

    fleet.on_change.append(_on_fleet_change)

    # 7. Wire channel layer
    _runtime.channel_layer = get_channel_layer()

    # 8. Create background tasks
    tick_task = asyncio.create_task(tick_loop(), name="tick_loop")
    binder_task = asyncio.create_task(binder.poll_loop(), name="run_binder")
    scheduler_task = asyncio.create_task(scheduler.run_loop(), name="scheduler")

    _runtime._tasks = [tick_task, binder_task, scheduler_task]

    log.info(
        "runtime.start_runtime(): %d vehicles, %.1fs tick — 3 background tasks started",
        len(fleet.all()),
        settings.SIM_TICK_INTERVAL,
    )


async def stop_runtime() -> None:
    """Gracefully shut down all simulator background services."""
    log.info("runtime.stop_runtime() called — cancelling background tasks.")

    tasks = _runtime._tasks[:]
    for task in tasks:
        if not task.done():
            task.cancel()

    if tasks:
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for result in results:
            if isinstance(result, Exception) and not isinstance(
                result, asyncio.CancelledError
            ):
                log.error("runtime.stop_runtime(): task raised: %s", result)

    _runtime._tasks.clear()

    # Stop paho MQTT
    if _runtime.mqtt is not None:
        try:
            _runtime.mqtt.loop_stop()
            _runtime.mqtt.disconnect()
            log.info("runtime: paho MQTT stopped and disconnected")
        except Exception as exc:
            log.warning("runtime: paho stop/disconnect error: %s", exc)
        _runtime.mqtt = None

    # Close DatabusClient
    if _runtime.databus is not None:
        try:
            await _runtime.databus.close()
            log.info("runtime: DatabusClient closed")
        except Exception as exc:
            log.warning("runtime: DatabusClient close error: %s", exc)
        _runtime.databus = None

    log.info("runtime.stop_runtime() complete.")


# ---------------------------------------------------------------------------
# Tick loop — async rewrite of sim/simulator.py run()
# ---------------------------------------------------------------------------


async def tick_loop() -> None:
    """Async tick loop: step vehicles, publish telemetry, push to browser."""
    from django.conf import settings
    import orjson

    from simulator_app.domain.kinematics import _get_shape, step_vehicle, build_vehicle_payloads
    from simulator_app.domain.progression.compute import compute_stop_status as _compute_stop_status
    import simulator_app.realtime.broadcast as _broadcast_mod

    interval = settings.SIM_TICK_INTERVAL
    only_vehicles: set[str] = getattr(settings, "SIM_ONLY_VEHICLES", set())
    stop_vehicles: set[str] = getattr(settings, "SIM_STOP_VEHICLES", set())
    drop_rate: int = getattr(settings, "SIM_RANDOM_DROP_RATE", 0)
    stop_all_after: int = getattr(settings, "SIM_STOP_ALL_AFTER", 0)
    topic_root: str = _runtime._mqtt_topic_root

    log.info("tick_loop: started (interval=%.1fs)", interval)
    cycle = 0

    while True:
        await asyncio.sleep(interval)
        cycle += 1

        fleet = _runtime.fleet
        shapes = _runtime._shapes
        stops = _runtime._stops
        mqtt = _runtime.mqtt

        if fleet is None or shapes is None:
            continue

        if stop_all_after and cycle > stop_all_after:
            continue

        for v in fleet.all():
            if only_vehicles and v.vehicle_id not in only_vehicles:
                continue
            if drop_rate and random.randint(1, 100) <= drop_rate:
                continue

            shape = _get_shape(v, shapes)
            step_vehicle(v, interval, shape, stops or [])

            payloads = build_vehicle_payloads(v, shape, stops or [])
            if payloads is None:
                continue

            # (a) paho-publish to databus broker (identical topics/payloads as before)
            if mqtt is not None:
                for leaf, payload_dict in payloads.items():
                    topic = f"{topic_root}/{v.vehicle_id}/{leaf}"
                    try:
                        mqtt.publish(topic, orjson.dumps(payload_dict), qos=0)
                    except Exception as exc:
                        log.debug("tick_loop: paho publish error: %s", exc)

            # (b) push telemetry to browser group via Channels
            for leaf, payload_dict in payloads.items():
                await _broadcast_mod.broadcast_telemetry(v.vehicle_id, leaf, payload_dict)

            # (c) progression oracle — Channels only, NEVER published to MQTT.
            _prog_geom = getattr(_runtime, "_progression_geom", None) or {}
            _shape_id = v.bound_shape_id or v.default_shape_id
            _shape_stops = _prog_geom.get(_shape_id, [])
            _pos = payloads["position"]
            _prev = v._kin.get("progression_prev")
            _prog = _compute_stop_status(
                v.progress_m,
                _pos["latitude"],
                _pos["longitude"],
                _shape_stops,
                speed=_pos.get("speed"),
                prev_state=_prev,
            )
            v._kin["progression_prev"] = _prog
            await _broadcast_mod.broadcast_telemetry(v.vehicle_id, "progression", _prog)

        # Push fleet snapshot to browser after every tick
        if fleet is not None:
            await _broadcast_mod.broadcast_fleet(fleet.snapshot())

        if cycle % 10 == 0:
            log.info(
                "[cycle %d] ticked %d vehicles", cycle, len(fleet.all()) if fleet else 0
            )
