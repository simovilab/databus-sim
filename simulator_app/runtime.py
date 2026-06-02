"""Runtime singleton and lifespan lifecycle for the SIMOVI simulator.

This module is the PRIMARY SEAM for Phase 2 backend porting.

PHASE 1 (scaffold): start_runtime() and stop_runtime() are stubs — they log and
return without starting any real tasks. The module-level `_runtime` is populated
with a bare Runtime instance (all fields None).

PHASE 2 (backend agent): fill start_runtime() to:
  1. Load shapes from settings.SHAPES_PATH (or simulator_app/data/shapes.json).
  2. Build FleetState from shapes/roster.
  3. Connect paho MQTT client (loop_start() — own thread, non-blocking).
  4. Open DatabusClient (httpx.AsyncClient).
  5. Wire RunBinder + Scheduler.
  6. asyncio.create_task() for:
       - tick_loop()          (kinematics + paho publish + Channels group_send)
       - binder.poll_loop()   (HTTP poll databus run state → FleetState)
       - scheduler.run_loop() (schedule.yaml → create/update run)
  7. Populate _runtime fields (fleet, scheduler, binder, databus, mqtt, channel_layer).

Fill stop_runtime() to:
  1. Cancel and await all background tasks.
  2. paho.loop_stop() / paho.disconnect().
  3. await databus.close() (httpx client).

See PLAN §6.1 and §5 for the full wiring contract.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Runtime holder
# ---------------------------------------------------------------------------


@dataclass
class Runtime:
    """Module-global container for all live simulator objects.

    All fields are Optional so the scaffold boots without any real services.
    Phase 2 populates these in start_runtime().

    Fields:
        fleet        — FleetState singleton (domain/fleet.py)
        scheduler    — Scheduler instance (services/scheduler.py)
        binder       — RunBinder instance (services/run_binder.py)
        databus      — DatabusClient (services/databus_client.py); async httpx
        mqtt         — paho.mqtt.client.Client (runs in its own thread via loop_start)
        channel_layer — channels.layers.InMemoryChannelLayer (from Django CHANNEL_LAYERS)
        _tasks       — internal list of asyncio.Task objects created at startup
    """

    fleet: Any | None = None
    scheduler: Any | None = None
    binder: Any | None = None
    databus: Any | None = None
    mqtt: Any | None = None
    channel_layer: Any | None = None
    _tasks: list[asyncio.Task[Any]] = field(default_factory=list)


# Module-level singleton — created once; never replaced.
_runtime: Runtime = Runtime()


def get_runtime() -> Runtime:
    """Return the module-global Runtime instance.

    Views and consumers import this to reach the live fleet/scheduler/binder.
    Safe to call before start_runtime() — all fields will be None until startup.
    """
    return _runtime


# ---------------------------------------------------------------------------
# Lifespan hooks — called by sim_project.asgi._LifespanHandler
# ---------------------------------------------------------------------------


async def start_runtime() -> None:
    """Start all simulator background services.

    Phase 1 stub: logs only. Phase 2 fills this with real startup logic.
    Must be safe to call even when databus is absent (log failures, don't raise).
    """
    log.info("runtime.start_runtime() called — stub (Phase 1 scaffold, no tasks started).")
    # Phase 2: populate _runtime fields and create_task() for the three loops.


async def stop_runtime() -> None:
    """Gracefully shut down all simulator background services.

    Phase 1 stub: cancels any tasks that may have been added to _runtime._tasks,
    then logs. Phase 2 adds paho.loop_stop() and httpx client close.
    """
    log.info("runtime.stop_runtime() called — cancelling background tasks.")

    tasks = _runtime._tasks[:]
    for task in tasks:
        if not task.done():
            task.cancel()

    if tasks:
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for result in results:
            if isinstance(result, Exception) and not isinstance(result, asyncio.CancelledError):
                log.error("runtime.stop_runtime(): task raised: %s", result)

    _runtime._tasks.clear()
    log.info("runtime.stop_runtime() complete.")
