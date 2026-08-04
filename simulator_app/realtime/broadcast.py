"""Channels broadcast helpers.

Replaces sim/state_publisher.py. Sends fleet/schedule/telemetry to the
``"fleet"`` group via the InMemoryChannelLayer.

Throttle: ``broadcast_fleet`` coalesces bursts to ≤ 1 push / 200 ms,
porting the THROTTLE_S logic from StatePublisher.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from channels.layers import get_channel_layer

log = logging.getLogger(__name__)

GROUP = "fleet"
THROTTLE_S = 0.200  # 200 ms

# ---------------------------------------------------------------------------
# Throttle state (module-level, single-process invariant)
# ---------------------------------------------------------------------------

_last_fleet_broadcast: float = 0.0
_fleet_dirty: bool = False
_throttle_task: asyncio.Task[None] | None = None
_lock = asyncio.Lock()


async def broadcast_fleet(snapshot: dict[str, Any]) -> None:
    """Send the fleet snapshot to all WebSocket clients, throttled to ≤ 1/200 ms."""
    global _last_fleet_broadcast, _fleet_dirty, _throttle_task

    async with _lock:
        now = time.monotonic()
        elapsed = now - _last_fleet_broadcast
        if elapsed >= THROTTLE_S:
            # Can publish immediately.
            if _throttle_task is not None and not _throttle_task.done():
                _throttle_task.cancel()
                _throttle_task = None
            _fleet_dirty = False
            _last_fleet_broadcast = now
        else:
            # Schedule deferred publish for the remainder of the window.
            _fleet_dirty = True
            if _throttle_task is None or _throttle_task.done():
                delay = THROTTLE_S - elapsed

                async def _deferred(s: dict[str, Any] = snapshot) -> None:
                    global _last_fleet_broadcast, _fleet_dirty, _throttle_task
                    await asyncio.sleep(delay)
                    async with _lock:
                        _throttle_task = None
                        if not _fleet_dirty:
                            return
                        _fleet_dirty = False
                        _last_fleet_broadcast = time.monotonic()
                    await _group_send({"type": "fleet.update", "kind": "fleet", "payload": s})

                _throttle_task = asyncio.create_task(_deferred(snapshot))
            return  # deferred; don't send now

    await _group_send({"type": "fleet.update", "kind": "fleet", "payload": snapshot})


async def broadcast_schedule(snapshot: dict[str, Any]) -> None:
    """Send the schedule snapshot to all WebSocket clients (no throttle)."""
    await _group_send({"type": "fleet.update", "kind": "schedule", "payload": snapshot})


async def broadcast_navsat(snapshot: list[dict[str, Any]]) -> None:
    """Send the NavSat vehicle snapshot to all WebSocket clients (no throttle)."""
    await _group_send({"type": "fleet.update", "kind": "navsat", "payload": snapshot})


async def broadcast_telemetry(
    vehicle_id: str, leaf: str, data: dict[str, Any]
) -> None:
    """Send a single-vehicle telemetry payload to all WebSocket clients."""
    await _group_send(
        {
            "type": "fleet.update",
            "kind": "telemetry",
            "vehicle_id": vehicle_id,
            "leaf": leaf,
            "payload": data,
        }
    )


async def _group_send(message: dict[str, Any]) -> None:
    """Send a message to the fleet group; silently swallow if layer is unavailable."""
    layer = get_channel_layer()
    if layer is None:
        return
    try:
        await layer.group_send(GROUP, message)
    except Exception as exc:  # noqa: BLE001
        log.debug("broadcast._group_send error: %s", exc)
