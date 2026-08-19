"""Vehicle ↔ run binder: polls databus for run-lifecycle state and drives fleet.

Ported verbatim from sim/run_binder.py. Imports updated to simulator_app paths.
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable

from simulator_app.domain.fleet import FleetState
from simulator_app.services.databus_client import DatabusClient


log = logging.getLogger(__name__)

_POLL_INTERVAL_S = float(os.getenv("RUN_POLL_INTERVAL_S", "2.0"))
_UNTRACK_DELAY_S = 5.0  # hold terminal state visible before dropping from tracking
# Grace window for brand-new runs databus hasn't registered yet. After this many
# seconds with the run still not found (404), treat it as lost.
_RUN_LOST_GRACE_S = 30.0


@dataclass
class BoundRun:
    run_id: str
    vehicle_id: str
    trip_id: str
    shape_id: str
    terminal_stop_id: str | None = None
    lifecycle_state: str | None = None
    last_polled_at: datetime | None = None
    tracked_since: datetime = field(default_factory=datetime.now)


# Callback: (run_id, old_state, new_state) -> None
StateChangeCallback = Callable[[str, str | None, str], None]


class RunBinder:
    """Maintain ``run_id → BoundRun`` and poll databus for state changes."""

    DEFAULT_POLL_INTERVAL_S = _POLL_INTERVAL_S
    # Must match databus's RunLifecycleStates.*.value verbatim (note the spaces).
    TERMINAL_STATES = frozenset(
        {"Completed", "Cancelled", "Interrupted", "Short Turned"}
    )

    def __init__(
        self,
        fleet: FleetState,
        databus: DatabusClient,
        poll_interval_s: float = _POLL_INTERVAL_S,
    ) -> None:
        self.fleet = fleet
        self.databus = databus
        self.poll_interval_s = poll_interval_s
        self._bindings: dict[str, BoundRun] = {}
        self.on_state_change: StateChangeCallback | None = None

    # --- mutators ----------------------------------------------------------

    def track(
        self,
        run_id: str,
        vehicle_id: str,
        trip_id: str,
        shape_id: str,
        terminal_stop_id: str | None = None,
    ) -> None:
        bound = BoundRun(
            run_id=run_id,
            vehicle_id=vehicle_id,
            trip_id=trip_id,
            shape_id=shape_id,
            terminal_stop_id=terminal_stop_id,
        )
        self._bindings[run_id] = bound
        log.info("run_binder.track run_id=%s vehicle_id=%s", run_id, vehicle_id)

    def untrack(self, run_id: str) -> None:
        self._bindings.pop(run_id, None)
        log.info("run_binder.untrack run_id=%s", run_id)

    # --- introspection -----------------------------------------------------

    def bindings(self) -> list[BoundRun]:
        return list(self._bindings.values())

    # --- poller ------------------------------------------------------------

    async def poll_loop(self) -> None:
        """Repeat: for each binding, fetch state from databus; on change fire callback."""
        log.info("run_binder.poll_loop started interval=%.1fs", self.poll_interval_s)
        while True:
            await asyncio.sleep(self.poll_interval_s)
            for run_id in list(self._bindings):
                binding = self._bindings.get(run_id)
                if binding is None:
                    continue
                if binding.lifecycle_state in self.TERMINAL_STATES:
                    continue  # already terminal — skip
                try:
                    new_state = await self.databus.get_run_state(run_id)
                except Exception as exc:
                    log.warning("run_binder.poll error run_id=%s: %s", run_id, exc)
                    continue
                if new_state is None:
                    grace_elapsed = (
                        (datetime.now() - binding.tracked_since).total_seconds()
                        > _RUN_LOST_GRACE_S
                    )
                    if binding.lifecycle_state is not None or grace_elapsed:
                        log.warning(
                            "run_binder.lost run_id=%s last_state=%s — run not found, force-unbinding",
                            run_id,
                            binding.lifecycle_state,
                        )
                        self._force_unbind(binding)
                    continue
                old_state = binding.lifecycle_state
                binding.last_polled_at = datetime.now()
                if new_state != old_state:
                    log.info(
                        "run_binder.state_change run_id=%s %s→%s",
                        run_id,
                        old_state,
                        new_state,
                    )
                    binding.lifecycle_state = new_state
                    self._apply_state(binding, new_state)
                    if self.on_state_change:
                        self.on_state_change(run_id, old_state, new_state)
                    if new_state in self.TERMINAL_STATES:
                        asyncio.get_event_loop().call_later(
                            _UNTRACK_DELAY_S, self.untrack, run_id
                        )

    def _apply_state(self, binding: BoundRun, new_state: str) -> None:
        """Map a run-lifecycle state change onto :class:`FleetState`."""
        vid = binding.vehicle_id
        self.fleet.set_lifecycle_state(vid, new_state)

        if new_state == "Confirmed":
            # (1) bind run fields first, before pings start (CONTRACTS.md §7.4)
            self.fleet.bind_run(
                vid,
                binding.run_id,
                binding.trip_id,
                binding.shape_id,
                terminal_stop_id=binding.terminal_stop_id,
            )
            # (2) reset kinematics: moving=False, progress_m=0
            self.fleet.set_moving(vid, False)
            v = self.fleet.get(vid)
            if v is not None:
                v.progress_m = 0.0
            # (3) now allow telemetry to flow
            self.fleet.set_transmitting(vid, True)

        elif new_state in ("Tracking", "In Progress", "No Signal"):
            pass  # no fleet change

        elif new_state in self.TERMINAL_STATES:
            self.fleet.set_transmitting(vid, False)
            self.fleet.unbind_run(vid)

    def _force_unbind(self, binding: BoundRun) -> None:
        """Unbind a vehicle when its run is no longer found."""
        self.fleet.unbind_run(binding.vehicle_id)
        self.untrack(binding.run_id)

    # --- private -----------------------------------------------------------

    def _bindings_snapshot(self) -> dict[str, Any]:
        return {
            run_id: {
                "run_id": b.run_id,
                "vehicle_id": b.vehicle_id,
                "trip_id": b.trip_id,
                "shape_id": b.shape_id,
                "lifecycle_state": b.lifecycle_state,
                "last_polled_at": b.last_polled_at.isoformat() if b.last_polled_at else None,
            }
            for run_id, b in self._bindings.items()
        }
