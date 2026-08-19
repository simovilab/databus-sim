"""Unit tests for simulator_app.services.scheduler."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest
import respx
import yaml

from simulator_app.services.databus_client import DatabusClient
from simulator_app.domain.fleet import FLEET, FleetState
from simulator_app.services.run_binder import RunBinder
from simulator_app.services.scheduler import Scheduler


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


def _past_time(delta_s: float = 60.0) -> str:
    dt = datetime.now(timezone.utc) - timedelta(seconds=delta_s)
    return dt.isoformat()


def _future_time(delta_s: float = 3600.0) -> str:
    dt = datetime.now(timezone.utc) + timedelta(seconds=delta_s)
    return dt.isoformat()


def _write_schedule(
    path: Path, runs: list[dict[str, Any]], defaults: dict | None = None
) -> None:
    doc = {
        "defaults": defaults
        or {
            "pre_run_idle_s": 0,
            "post_run_idle_s": 30,
            "auto_confirm_delay_s": 0,
        },
        "runs": runs,
    }
    path.write_text(yaml.safe_dump(doc))


@pytest.fixture
def tmp_path_schedule(tmp_path: Path) -> Path:
    p = tmp_path / "schedule.yaml"
    _write_schedule(p, [])
    return p


@pytest.fixture
def fleet() -> FleetState:
    f = FleetState(vehicles=list(FLEET))
    f.set_moving = lambda vid, on: None  # type: ignore
    f.set_transmitting = lambda vid, on: None  # type: ignore
    f.bind_run = lambda *a, **kw: None  # type: ignore
    f.unbind_run = lambda vid: None  # type: ignore
    f.set_lifecycle_state = lambda vid, s: None  # type: ignore
    return f


class FakeStatePublisher:
    def __init__(self) -> None:
        self.published: list[Any] = []

    def publish_schedule_snapshot(self, entries: Any) -> None:
        self.published.append(entries)


BASE = "http://databus.test"


def _make_scheduler(
    path: Path,
    fleet: FleetState,
    binder: RunBinder | None = None,
    state_publisher: Any = None,
    databus: DatabusClient | None = None,
) -> Scheduler:
    if databus is None:
        databus = DatabusClient(base_url=BASE)
    if binder is None:
        binder = MagicMock(spec=RunBinder)
        binder.on_state_change = None
    pub = state_publisher or FakeStatePublisher()
    s = Scheduler(
        path=path, fleet=fleet, databus=databus, binder=binder, state_publisher=pub
    )
    s.load()
    return s


# ---------------------------------------------------------------------------
# load / reload / write
# ---------------------------------------------------------------------------


def test_load_empty_schedule(tmp_path_schedule: Path, fleet: FleetState) -> None:
    s = _make_scheduler(tmp_path_schedule, fleet)
    assert s.snapshot()["runs"] == []


def test_load_with_entries(tmp_path: Path, fleet: FleetState) -> None:
    p = tmp_path / "s.yaml"
    _write_schedule(
        p,
        runs=[
            {
                "id": "e1",
                "vehicle_id": "unit-01",
                "operator_id": "op-001",
                "route_id": "bUCR_L1",
                "trip_id": "trip-1",
                "direction_id": 0,
                "shape_id": "hacia_artes",
                "schedule_relationship": "SCHEDULED",
                "start_time": _future_time(),
                "auto_confirm": False,
                "auto_start_motion_after_s": None,
            }
        ],
    )
    s = _make_scheduler(p, fleet)
    snap = s.snapshot()
    assert len(snap["runs"]) == 1
    assert snap["runs"][0]["status"] == "pending"


def test_reload_picks_up_new_entries(tmp_path: Path, fleet: FleetState) -> None:
    p = tmp_path / "s.yaml"
    _write_schedule(p, [])
    pub = FakeStatePublisher()
    s = _make_scheduler(p, fleet, state_publisher=pub)
    assert s.snapshot()["runs"] == []

    _write_schedule(
        p,
        runs=[
            {
                "id": "late-entry",
                "vehicle_id": "unit-02",
                "operator_id": "op-001",
                "route_id": "bUCR_L1",
                "trip_id": "trip-2",
                "direction_id": 1,
                "shape_id": "hacia_educacion",
                "schedule_relationship": "SCHEDULED",
                "start_time": _future_time(),
                "auto_confirm": False,
                "auto_start_motion_after_s": None,
            }
        ],
    )
    s.reload()
    assert len(s.snapshot()["runs"]) == 1


def test_write_atomically(tmp_path: Path, fleet: FleetState) -> None:
    p = tmp_path / "s.yaml"
    _write_schedule(p, [])
    s = _make_scheduler(p, fleet)
    new_doc = {
        "defaults": {"pre_run_idle_s": 10, "post_run_idle_s": 10, "auto_confirm_delay_s": 2},
        "runs": [],
    }
    s.write(new_doc)
    raw = yaml.safe_load(p.read_text())
    assert raw["defaults"]["pre_run_idle_s"] == 10


# ---------------------------------------------------------------------------
# run_loop dispatch
# ---------------------------------------------------------------------------


def _entry(
    eid: str,
    start_time: str,
    auto_confirm: bool = False,
    vehicle_id: str = "unit-01",
) -> dict[str, Any]:
    return {
        "id": eid,
        "vehicle_id": vehicle_id,
        "operator_id": "op-001",
        "route_id": "bUCR_L1",
        "trip_id": "trip-1",
        "direction_id": 0,
        "shape_id": "hacia_artes",
        "schedule_relationship": "SCHEDULED",
        "start_time": start_time,
        "auto_confirm": auto_confirm,
        "auto_start_motion_after_s": None,
    }


@pytest.mark.asyncio
@respx.mock
async def test_run_loop_dispatches_past_entry(tmp_path: Path, fleet: FleetState) -> None:
    respx.post(f"{BASE}/api/create-run/").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "success",
                "run_id": "run-auto-1",
                "run_lifecycle_state": "Initialized",
            },
        )
    )
    respx.get(f"{BASE}/api/stop-times/").mock(
        return_value=httpx.Response(200, json=[])
    )

    p = tmp_path / "s.yaml"
    _write_schedule(p, runs=[_entry("past-entry", _past_time(300))])

    binder = MagicMock(spec=RunBinder)
    binder.on_state_change = None
    async with DatabusClient(base_url=BASE) as db:
        s = Scheduler(
            path=p, fleet=fleet, databus=db, binder=binder, tick_s=0.05
        )
        s.load()
        task = asyncio.create_task(s.run_loop())
        await asyncio.sleep(0.15)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    snap = s.snapshot()
    statuses = {r["id"]: r["status"] for r in snap["runs"]}
    assert statuses["past-entry"] == "initialized"
    binder.track.assert_called_once()


@pytest.mark.asyncio
async def test_future_entry_not_dispatched(tmp_path: Path, fleet: FleetState) -> None:
    p = tmp_path / "s.yaml"
    _write_schedule(p, runs=[_entry("future-entry", _future_time(3600))])

    binder = MagicMock(spec=RunBinder)
    binder.on_state_change = None
    db = DatabusClient(base_url=BASE)
    s = Scheduler(path=p, fleet=fleet, databus=db, binder=binder)
    s.load()

    task = asyncio.create_task(s.run_loop())
    await asyncio.sleep(0.15)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert s.snapshot()["runs"][0]["status"] == "pending"
    binder.track.assert_not_called()


@pytest.mark.asyncio
@respx.mock
async def test_run_loop_failed_create_marks_failed(tmp_path: Path, fleet: FleetState) -> None:
    respx.post(f"{BASE}/api/create-run/").mock(
        return_value=httpx.Response(
            400,
            json={"status": "error", "step": "validate", "errors": {}},
        )
    )

    p = tmp_path / "s.yaml"
    _write_schedule(p, runs=[_entry("fail-entry", _past_time(60), vehicle_id="unit-99")])

    binder = MagicMock(spec=RunBinder)
    binder.on_state_change = None
    async with DatabusClient(base_url=BASE) as db:
        s = Scheduler(path=p, fleet=fleet, databus=db, binder=binder, tick_s=0.05)
        s.load()
        task = asyncio.create_task(s.run_loop())
        await asyncio.sleep(0.15)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    assert s.snapshot()["runs"][0]["status"] == "failed"
    binder.track.assert_not_called()
