"""MQTT state publisher: emits ``sim/state/fleet`` and ``sim/state/schedule``.

Owned by Agent A (A2). Throttles fleet snapshots to ≤ 1 publish per 200 ms.

Usage::

    pub = StatePublisher(fleet_state, mqtt_client)
    pub.start()   # registers on fleet.on_change, publishes boot snapshot

Agent B calls ``pub.publish_schedule_snapshot(entries)`` whenever the
scheduler state changes.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone
from typing import Any

import orjson

from .fleet import FleetState

log = logging.getLogger(__name__)


class StatePublisher:
    """Coalesces fleet/schedule changes and publishes retained snapshots.

    Throttling target: ≤ 1 publish / 200 ms / topic (coalesce bursts).
    """

    FLEET_TOPIC = "sim/state/fleet"
    SCHEDULE_TOPIC = "sim/state/schedule"
    THROTTLE_S = 0.200  # 200 ms

    def __init__(self, fleet: FleetState, mqtt_client: Any) -> None:
        self.fleet = fleet
        self._client = mqtt_client
        self._lock = threading.Lock()
        self._fleet_dirty = False
        self._last_fleet_publish: float = 0.0
        self._timer: threading.Timer | None = None
        self._started = False

    # --- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        """Register on ``fleet.on_change`` and publish an initial boot snapshot."""
        self.fleet.on_change.append(self._on_fleet_change)
        self._started = True
        # Publish an immediate boot snapshot so UI has something to render.
        self.publish_fleet_snapshot()
        # Publish an empty schedule snapshot so the UI topic is retained at boot.
        self.publish_schedule_snapshot([])
        log.info("StatePublisher: started (throttle=%dms)", int(self.THROTTLE_S * 1000))

    def stop(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
        try:
            self.fleet.on_change.remove(self._on_fleet_change)
        except ValueError:
            pass
        self._started = False
        log.info("StatePublisher: stopped")

    # --- public API --------------------------------------------------------

    def publish_fleet_snapshot(self) -> None:
        """Immediately publish the current fleet state (retained, QoS 0)."""
        snapshot = self.fleet.snapshot()
        payload = orjson.dumps(snapshot)
        self._client.publish(self.FLEET_TOPIC, payload, qos=0, retain=True)
        self._last_fleet_publish = time.monotonic()
        log.debug("StatePublisher: published fleet snapshot")

    def publish_schedule_snapshot(self, entries: list[dict[str, Any]]) -> None:
        """Publish a schedule snapshot (retained, QoS 0).

        Agent B calls this whenever the scheduler state changes.
        For A2 (before B lands) we publish an empty entries list at boot.
        """
        snapshot = {
            "entries": entries,
            "published_at": datetime.now(timezone.utc).isoformat(),
        }
        payload = orjson.dumps(snapshot)
        self._client.publish(self.SCHEDULE_TOPIC, payload, qos=0, retain=True)
        log.debug("StatePublisher: published schedule snapshot (%d entries)", len(entries))

    # --- throttle helper ---------------------------------------------------

    def _on_fleet_change(self) -> None:
        """Called on every fleet mutation; coalesces to max 1 publish per throttle window."""
        with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_fleet_publish
            if elapsed >= self.THROTTLE_S:
                # Can publish immediately.
                if self._timer is not None:
                    self._timer.cancel()
                    self._timer = None
                self._fleet_dirty = False
            else:
                # Schedule deferred publish for the remainder of the window.
                self._fleet_dirty = True
                if self._timer is None:
                    delay = self.THROTTLE_S - elapsed
                    self._timer = threading.Timer(delay, self._deferred_publish)
                    self._timer.daemon = True
                    self._timer.start()
                return  # deferred; don't publish now

        # Outside the lock: do the actual publish.
        self.publish_fleet_snapshot()

    def _deferred_publish(self) -> None:
        with self._lock:
            self._timer = None
            if not self._fleet_dirty:
                return
            self._fleet_dirty = False
        self.publish_fleet_snapshot()
