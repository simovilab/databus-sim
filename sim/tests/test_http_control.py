"""Unit tests for sim.http_control using FastAPI TestClient."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, AsyncMock
import tempfile
import os

import pytest
from fastapi.testclient import TestClient

import fakeredis.aioredis as fake_aio

from sim.fleet import FLEET, FleetState
from sim.http_control import HttpControl, ScheduleDocument
from sim.redis_client import RedisClient
from sim.scheduler import Scheduler
from sim.databus_client import DatabusClient
from sim.run_binder import RunBinder


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_schedule_yaml(tmp_path: Path, runs: list[dict[str, Any]] | None = None) -> Path:
    import yaml

    doc = {
        "defaults": {"pre_run_idle_s": 30, "post_run_idle_s": 30, "auto_confirm_delay_s": 5},
        "runs": runs or [],
    }
    p = tmp_path / "schedule.yaml"
    p.write_text(yaml.safe_dump(doc))
    return p


@pytest.fixture
def tmp_schedule(tmp_path: Path) -> Path:
    return _make_schedule_yaml(tmp_path)


@pytest.fixture
def fleet() -> FleetState:
    f = FleetState(vehicles=list(FLEET))
    # Minimal snapshot so GET /fleet doesn't blow up
    f.snapshot = lambda: {"vehicles": [], "published_at": "2026-01-01T00:00:00+00:00"}  # type: ignore
    return f


@pytest.fixture
async def redis_client() -> RedisClient:
    server = fake_aio.FakeRedis(decode_responses=True)
    client = RedisClient(client=server)
    async with client:
        yield client


@pytest.fixture
def scheduler(tmp_schedule: Path, fleet: FleetState, redis_client: RedisClient) -> Scheduler:
    db = MagicMock(spec=DatabusClient)
    binder = MagicMock(spec=RunBinder)
    s = Scheduler(path=tmp_schedule, fleet=fleet, databus=db, binder=binder)
    s.load()
    return s


@pytest.fixture
def client(fleet: FleetState, scheduler: Scheduler, redis_client: RedisClient) -> TestClient:
    ctrl = HttpControl(fleet=fleet, scheduler=scheduler, redis_client=redis_client)
    return TestClient(ctrl.app, raise_server_exceptions=True)


# ---------------------------------------------------------------------------
# GET /healthz
# ---------------------------------------------------------------------------


def test_healthz(client: TestClient) -> None:
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


# ---------------------------------------------------------------------------
# GET /schedule
# ---------------------------------------------------------------------------


def test_get_schedule_empty(client: TestClient) -> None:
    resp = client.get("/schedule")
    assert resp.status_code == 200
    body = resp.json()
    assert "runs" in body
    assert body["runs"] == []


def test_get_schedule_with_entry(
    tmp_path: Path,
    fleet: FleetState,
    redis_client: RedisClient,
) -> None:
    from sim.databus_client import DatabusClient
    from sim.run_binder import RunBinder

    path = _make_schedule_yaml(
        tmp_path,
        runs=[
            {
                "id": "sched-001",
                "vehicle_id": "unit-01",
                "operator_id": "op-001",
                "route_id": "bUCR_L1",
                "trip_id": "trip-001",
                "direction_id": 0,
                "shape_id": "hacia_artes",
                "schedule_relationship": "SCHEDULED",
                "start_time": "2026-05-19T16:00:00-06:00",
                "auto_confirm": False,
                "auto_start_motion_after_s": None,
            }
        ],
    )
    db = MagicMock(spec=DatabusClient)
    binder = MagicMock(spec=RunBinder)
    s = Scheduler(path=path, fleet=fleet, databus=db, binder=binder)
    s.load()

    ctrl = HttpControl(fleet=fleet, scheduler=s, redis_client=redis_client)
    c = TestClient(ctrl.app)
    resp = c.get("/schedule")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["runs"]) == 1
    assert body["runs"][0]["id"] == "sched-001"
    assert body["runs"][0]["status"] == "pending"


# ---------------------------------------------------------------------------
# PUT /schedule
# ---------------------------------------------------------------------------


def test_put_schedule_valid(
    client: TestClient,
    scheduler: Scheduler,
) -> None:
    doc = {
        "defaults": {"pre_run_idle_s": 30, "post_run_idle_s": 30, "auto_confirm_delay_s": 5},
        "runs": [],
    }
    resp = client.put("/schedule", json=doc)
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


def test_put_schedule_with_entry(
    client: TestClient,
    scheduler: Scheduler,
) -> None:
    doc = {
        "defaults": {"pre_run_idle_s": 30, "post_run_idle_s": 30, "auto_confirm_delay_s": 5},
        "runs": [
            {
                "id": "new-001",
                "vehicle_id": "unit-02",
                "operator_id": "op-001",
                "route_id": "bUCR_L1",
                "trip_id": "trip-999",
                "direction_id": 1,
                "shape_id": "hacia_educacion",
                "schedule_relationship": "SCHEDULED",
                "start_time": "2026-06-01T08:00:00-06:00",
                "auto_confirm": True,
                "auto_start_motion_after_s": None,
            }
        ],
    }
    resp = client.put("/schedule", json=doc)
    assert resp.status_code == 200
    # Verify it reloaded — GET /schedule should show the new entry
    get_resp = client.get("/schedule")
    assert get_resp.status_code == 200
    runs = get_resp.json()["runs"]
    assert any(r["id"] == "new-001" for r in runs)


def test_put_schedule_invalid_body(client: TestClient) -> None:
    resp = client.put("/schedule", json={"runs": "not-a-list"})
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# POST /schedule/reload
# ---------------------------------------------------------------------------


def test_reload_schedule(client: TestClient) -> None:
    resp = client.post("/schedule/reload")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


# ---------------------------------------------------------------------------
# GET /fleet
# ---------------------------------------------------------------------------


def test_get_fleet(client: TestClient) -> None:
    resp = client.get("/fleet")
    assert resp.status_code == 200
    body = resp.json()
    assert "vehicles" in body


# ---------------------------------------------------------------------------
# GET /run/{run_id}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_run_found(
    fleet: FleetState,
    scheduler: Scheduler,
) -> None:
    server = fake_aio.FakeRedis(decode_responses=True)
    await server.hset(
        "run:test-run-1",
        mapping={"run_lifecycle_state": "Confirmed", "vehicle_id": "unit-01"},
    )
    redis_client = RedisClient(client=server)
    async with redis_client:
        ctrl = HttpControl(fleet=fleet, scheduler=scheduler, redis_client=redis_client)
        c = TestClient(ctrl.app)
        resp = c.get("/run/test-run-1")
    assert resp.status_code == 200
    body = resp.json()
    assert body["run_id"] == "test-run-1"
    assert body["run_lifecycle_state"] == "Confirmed"
    assert body["fields"]["vehicle_id"] == "unit-01"


@pytest.mark.asyncio
async def test_get_run_not_found(
    fleet: FleetState,
    scheduler: Scheduler,
) -> None:
    server = fake_aio.FakeRedis(decode_responses=True)
    redis_client = RedisClient(client=server)
    async with redis_client:
        ctrl = HttpControl(fleet=fleet, scheduler=scheduler, redis_client=redis_client)
        c = TestClient(ctrl.app)
        resp = c.get("/run/no-such-run")
    assert resp.status_code == 404
