"""Schedule.yaml runner: posts ``/api/create-run`` at scheduled times.

Ported verbatim from sim/scheduler.py. The ``state_publisher`` dependency is
replaced: instead of calling ``state_publisher.publish_schedule_snapshot()``,
the scheduler calls ``broadcast_schedule()`` from realtime.broadcast so the
browser group receives the update over Channels. The scheduling/dispatch logic
is identical.
"""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from simulator_app.services.databus_client import (
    CreateRunRequest,
    DatabusClient,
    DatabusError,
    UpdateRunRequest,
)
from simulator_app.domain.fleet import FleetState
from simulator_app.services.run_binder import RunBinder


log = logging.getLogger(__name__)

SCHEDULER_TICK_S = float(os.getenv("SCHEDULER_TICK_S", "1.0"))

_PENDING = "pending"
_REQUESTED = "requested"
_INITIALIZED = "initialized"
_FAILED = "failed"

VALID_SCHEDULE_RELATIONSHIPS = {"SCHEDULED", "ADDED", "UNSCHEDULED"}
VALID_DIRECTION_IDS = {0, 1}


def _parse_dt(value: Any) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ValueError(f"start_time must include a timezone: {value!r}")
        return value
    dt = datetime.fromisoformat(str(value))
    if dt.tzinfo is None:
        raise ValueError(f"start_time must include a timezone: {value!r}")
    return dt


def _validate_entry(entry: dict[str, Any]) -> None:
    _parse_dt(entry["start_time"])
    if entry.get("direction_id") not in VALID_DIRECTION_IDS:
        raise ValueError(f"direction_id must be 0 or 1, got {entry.get('direction_id')!r}")
    rel = entry.get("schedule_relationship", "SCHEDULED")
    if rel not in VALID_SCHEDULE_RELATIONSHIPS:
        raise ValueError(
            f"schedule_relationship must be one of {VALID_SCHEDULE_RELATIONSHIPS}, got {rel!r}"
        )


class Scheduler:
    """Reads ``schedule.yaml`` and fires create-run / update-run at start_time."""

    DEFAULT_PRE_RUN_IDLE_S = 30
    DEFAULT_POST_RUN_IDLE_S = 30
    DEFAULT_AUTO_CONFIRM_DELAY_S = 5

    def __init__(
        self,
        path: Path,
        fleet: FleetState,
        databus: DatabusClient,
        binder: RunBinder,
        state_publisher: Any = None,
        tick_s: float | None = None,
    ) -> None:
        self.path = path
        self.fleet = fleet
        self.databus = databus
        self.binder = binder
        # state_publisher kept for API compatibility; prefer broadcast_schedule
        self.state_publisher = state_publisher
        self._tick_s = tick_s if tick_s is not None else SCHEDULER_TICK_S
        self._entries: list[dict[str, Any]] = []
        self._statuses: dict[str, str] = {}
        self._run_ids: dict[str, str] = {}  # entry_id → run_id
        self._defaults: dict[str, Any] = {
            "pre_run_idle_s": self.DEFAULT_PRE_RUN_IDLE_S,
            "post_run_idle_s": self.DEFAULT_POST_RUN_IDLE_S,
            "auto_confirm_delay_s": self.DEFAULT_AUTO_CONFIRM_DELAY_S,
        }

    # --- file ops ----------------------------------------------------------

    def load(self, path: Path | None = None) -> None:
        """Read and validate ``schedule.yaml``; populate ``self._entries``."""
        p = path or self.path
        raw = yaml.safe_load(p.read_text()) or {}
        defaults = raw.get("defaults", {})
        self._defaults = {
            "pre_run_idle_s": int(
                defaults.get("pre_run_idle_s", self.DEFAULT_PRE_RUN_IDLE_S)
            ),
            "post_run_idle_s": int(
                defaults.get("post_run_idle_s", self.DEFAULT_POST_RUN_IDLE_S)
            ),
            "auto_confirm_delay_s": int(
                defaults.get("auto_confirm_delay_s", self.DEFAULT_AUTO_CONFIRM_DELAY_S)
            ),
        }
        runs = raw.get("runs") or []
        ids_seen: set[str] = set()
        for entry in runs:
            eid = entry.get("id")
            if not eid:
                raise ValueError("Each schedule entry must have an 'id' field")
            if eid in ids_seen:
                raise ValueError(f"Duplicate schedule entry id: {eid!r}")
            ids_seen.add(eid)
            _validate_entry(entry)
        self._entries = list(runs)
        for entry in self._entries:
            eid = entry["id"]
            if eid not in self._statuses:
                self._statuses[eid] = _PENDING

    def reload(self) -> None:
        """Re-read from disk and publish schedule snapshot."""
        self.load()
        self._publish_snapshot()

    def write(self, document: dict[str, Any]) -> None:
        """Validate and overwrite ``schedule.yaml`` on disk (atomic write)."""
        runs = document.get("runs") or []
        ids_seen: set[str] = set()
        for entry in runs:
            eid = entry.get("id")
            if not eid:
                raise ValueError("Each schedule entry must have an 'id' field")
            if eid in ids_seen:
                raise ValueError(f"Duplicate schedule entry id: {eid!r}")
            ids_seen.add(eid)
            _validate_entry(entry)
        content = yaml.safe_dump(document, default_flow_style=False, allow_unicode=True)
        dir_ = self.path.parent
        with tempfile.NamedTemporaryFile(
            mode="w", dir=dir_, suffix=".yaml.tmp", delete=False
        ) as fh:
            fh.write(content)
            fh.flush()
            os.fsync(fh.fileno())
            tmp_path = Path(fh.name)
        tmp_path.replace(self.path)

    # --- introspection -----------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        """Return the payload for ``GET /schedule`` and the Channels broadcast."""
        now = datetime.now(timezone.utc).isoformat()
        runs = []
        for entry in self._entries:
            eid = entry["id"]
            runs.append(
                {
                    **entry,
                    "status": self._statuses.get(eid, _PENDING),
                    "bound_run_id": self._run_ids.get(eid),
                }
            )
        return {
            "defaults": self._defaults,
            "runs": runs,
            "published_at": now,
        }

    # --- runner ------------------------------------------------------------

    async def run_loop(self) -> None:
        """Tick every _tick_s; dispatch entries whose start_time has come."""
        log.info("scheduler.run_loop started tick=%.3fs", self._tick_s)
        while True:
            await asyncio.sleep(self._tick_s)
            now = datetime.now(timezone.utc)
            pre_idle = self._defaults.get("pre_run_idle_s", self.DEFAULT_PRE_RUN_IDLE_S)
            for entry in list(self._entries):
                eid = entry["id"]
                if self._statuses.get(eid, _PENDING) != _PENDING:
                    continue
                try:
                    start = _parse_dt(entry["start_time"])
                except Exception:
                    continue
                trigger_at = start.timestamp() - pre_idle
                if now.timestamp() >= trigger_at:
                    await self._dispatch_entry(entry)

    async def _dispatch_entry(self, entry: dict[str, Any]) -> None:
        eid = entry["id"]
        self._statuses[eid] = _REQUESTED
        self._publish_snapshot()
        log.info(
            "scheduler.dispatch entry_id=%s vehicle_id=%s", eid, entry.get("vehicle_id")
        )

        try:
            req = CreateRunRequest(
                vehicle_id=entry["vehicle_id"],
                operator_id=entry["operator_id"],
                route_id=entry["route_id"],
                trip_id=entry["trip_id"],
                direction_id=entry["direction_id"],
                shape_id=entry["shape_id"],
                schedule_relationship=entry.get("schedule_relationship", "SCHEDULED"),
            )
            resp = await self.databus.create_run(req)
        except DatabusError as exc:
            log.error("scheduler.dispatch failed entry_id=%s: %s", eid, exc)
            self._statuses[eid] = _FAILED
            self._publish_snapshot()
            return

        run_id = resp.run_id
        self._run_ids[eid] = run_id
        self._statuses[eid] = _INITIALIZED
        self._publish_snapshot()

        terminal_stop_id: str | None = None
        try:
            terminal_stop_id = await self.databus.get_trip_terminal_stop(entry["trip_id"])
        except Exception as exc:
            log.warning(
                "scheduler.dispatch cannot fetch terminal stop for trip %s: %s",
                entry["trip_id"],
                exc,
            )

        self.binder.track(
            run_id=run_id,
            vehicle_id=entry["vehicle_id"],
            trip_id=entry["trip_id"],
            shape_id=entry["shape_id"],
            terminal_stop_id=terminal_stop_id,
        )

        if entry.get("auto_confirm"):
            delay_s = self._defaults.get(
                "auto_confirm_delay_s", self.DEFAULT_AUTO_CONFIRM_DELAY_S
            )

            async def _confirm(rid: str = run_id) -> None:
                await asyncio.sleep(delay_s)
                try:
                    await self.databus.update_run(
                        UpdateRunRequest(
                            run_id=rid,
                            event="run_confirmed_by_operator",
                            details={},
                        )
                    )
                    log.info("scheduler.auto_confirm run_id=%s", rid)
                except DatabusError as exc:
                    log.error("scheduler.auto_confirm failed run_id=%s: %s", rid, exc)

            asyncio.create_task(_confirm())

        auto_start_s = entry.get("auto_start_motion_after_s")
        if auto_start_s is not None:
            vid = entry["vehicle_id"]
            original_cb = self.binder.on_state_change

            def _on_confirmed(
                rid: str,
                old: str | None,
                new: str,
                _run_id: str = run_id,
                _vid: str = vid,
                _delay: int = auto_start_s,
                _orig: Any = original_cb,
            ) -> None:
                if new == "Confirmed" and rid == _run_id:

                    async def _start() -> None:
                        await asyncio.sleep(_delay)
                        self.fleet.set_moving(_vid, True)
                        log.info("scheduler.auto_start_motion vehicle_id=%s", _vid)

                    asyncio.create_task(_start())
                if _orig is not None:
                    _orig(rid, old, new)

            self.binder.on_state_change = _on_confirmed

    def _publish_snapshot(self) -> None:
        """Publish schedule snapshot — prefer Channels broadcast, fall back to legacy publisher."""
        snapshot = self.snapshot()
        # Channels broadcast (non-blocking fire-and-forget via asyncio task).
        # Only schedule if there is a running event loop (i.e. we're inside an async context).
        try:
            loop = asyncio.get_running_loop()
            from simulator_app.realtime.broadcast import broadcast_schedule

            loop.create_task(broadcast_schedule(snapshot))
        except RuntimeError:
            pass  # no running loop (sync context like tests) — skip Channels broadcast

        # Legacy state_publisher compatibility
        if self.state_publisher is not None:
            try:
                self.state_publisher.publish_schedule_snapshot(snapshot["runs"])
            except Exception:
                pass
