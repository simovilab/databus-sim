"""DRF APITestCase for every /sim/* endpoint + mocked /databus/ proxy.

Uses pytest-django's rf (RequestFactory) or DRF's APIClient. The global runtime
is patched for each test so there's no real paho/httpx/asyncio needed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml
from rest_framework.test import APIClient

from simulator_app.domain.fleet import FleetState, Vehicle
from simulator_app.domain.kinematics import _init_kin
from simulator_app.services.scheduler import Scheduler
from simulator_app.services.run_binder import RunBinder


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_fleet() -> FleetState:
    vehicles = [
        Vehicle(vehicle_id="unit-01", route_id="bUCR_L1", default_shape_id="hacia_artes"),
        Vehicle(vehicle_id="unit-02", route_id="bUCR_L1", default_shape_id="hacia_educacion"),
    ]
    for v in vehicles:
        _init_kin(v)
    return FleetState(vehicles)


def _make_schedule_yaml(path: Path, runs: list | None = None) -> None:
    doc = {
        "defaults": {"pre_run_idle_s": 30, "post_run_idle_s": 30, "auto_confirm_delay_s": 5},
        "runs": runs or [],
    }
    path.write_text(yaml.safe_dump(doc))


def _make_scheduler(path: Path, fleet: FleetState) -> Scheduler:
    db = MagicMock()
    binder = MagicMock(spec=RunBinder)
    binder.on_state_change = None
    s = Scheduler(path=path, fleet=fleet, databus=db, binder=binder)
    s.load()
    return s


def _make_runtime(
    fleet: FleetState | None = None,
    scheduler: Any = None,
    binder: Any = None,
    databus: Any = None,
) -> MagicMock:
    rt = MagicMock()
    rt.fleet = fleet or _make_fleet()
    rt.scheduler = scheduler or MagicMock()
    rt.binder = binder or MagicMock(spec=RunBinder)
    rt.databus = databus or AsyncMock()
    return rt


@pytest.fixture
def client() -> APIClient:
    return APIClient()


@pytest.fixture
def tmp_sched(tmp_path: Path) -> Path:
    p = tmp_path / "schedule.yaml"
    _make_schedule_yaml(p)
    return p


# ---------------------------------------------------------------------------
# GET /sim/healthz
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_healthz(client: APIClient) -> None:
    resp = client.get("/sim/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


# ---------------------------------------------------------------------------
# GET /sim/fleet
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_get_fleet(client: APIClient) -> None:
    fleet = _make_fleet()
    rt = _make_runtime(fleet=fleet)
    with patch("simulator_app.api.views.get_runtime", return_value=rt):
        resp = client.get("/sim/fleet")
    assert resp.status_code == 200
    body = resp.json()
    assert "vehicles" in body
    assert len(body["vehicles"]) == 2


@pytest.mark.django_db
def test_get_fleet_when_not_ready(client: APIClient) -> None:
    rt = MagicMock()
    rt.fleet = None
    with patch("simulator_app.api.views.get_runtime", return_value=rt):
        resp = client.get("/sim/fleet")
    assert resp.status_code == 200
    assert resp.json()["vehicles"] == []


# ---------------------------------------------------------------------------
# GET /sim/schedule
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_get_schedule_empty(client: APIClient, tmp_sched: Path) -> None:
    fleet = _make_fleet()
    scheduler = _make_scheduler(tmp_sched, fleet)
    rt = _make_runtime(fleet=fleet, scheduler=scheduler)
    with patch("simulator_app.api.views.get_runtime", return_value=rt):
        resp = client.get("/sim/schedule")
    assert resp.status_code == 200
    assert resp.json()["runs"] == []


# ---------------------------------------------------------------------------
# PUT /sim/schedule
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_put_schedule_valid(client: APIClient, tmp_sched: Path) -> None:
    fleet = _make_fleet()
    scheduler = _make_scheduler(tmp_sched, fleet)
    rt = _make_runtime(fleet=fleet, scheduler=scheduler)
    doc = {
        "defaults": {"pre_run_idle_s": 30, "post_run_idle_s": 30, "auto_confirm_delay_s": 5},
        "runs": [],
    }
    with patch("simulator_app.api.views.get_runtime", return_value=rt):
        resp = client.put("/sim/schedule", doc, format="json")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


@pytest.mark.django_db
def test_put_schedule_invalid_body(client: APIClient) -> None:
    rt = _make_runtime()
    with patch("simulator_app.api.views.get_runtime", return_value=rt):
        resp = client.put("/sim/schedule", {"runs": "not-a-list"}, format="json")
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# POST /sim/schedule/reload
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_reload_schedule(client: APIClient, tmp_sched: Path) -> None:
    fleet = _make_fleet()
    scheduler = _make_scheduler(tmp_sched, fleet)
    rt = _make_runtime(fleet=fleet, scheduler=scheduler)
    with patch("simulator_app.api.views.get_runtime", return_value=rt):
        resp = client.post("/sim/schedule/reload")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


# ---------------------------------------------------------------------------
# GET /sim/run/<run_id>
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_get_run_found(client: APIClient) -> None:
    db = MagicMock()
    db.get_run_hash = AsyncMock(
        return_value={"run_lifecycle_state": "Confirmed", "vehicle_id": "unit-01"}
    )
    rt = _make_runtime(databus=db)
    with patch("simulator_app.api.views.get_runtime", return_value=rt):
        resp = client.get("/sim/run/test-run-1")
    assert resp.status_code == 200
    body = resp.json()
    assert body["run_id"] == "test-run-1"
    assert body["run_lifecycle_state"] == "Confirmed"


@pytest.mark.django_db
def test_get_run_not_found(client: APIClient) -> None:
    db = MagicMock()
    db.get_run_hash = AsyncMock(return_value={})
    rt = _make_runtime(databus=db)
    with patch("simulator_app.api.views.get_runtime", return_value=rt):
        resp = client.get("/sim/run/no-such-run")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /sim/runs/track
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_track_run_ok(client: APIClient) -> None:
    db = MagicMock()
    db.get_trip_terminal_stop = AsyncMock(return_value="stop-99")
    binder = MagicMock(spec=RunBinder)
    rt = _make_runtime(databus=db, binder=binder)
    body = {
        "run_id": "run-1",
        "vehicle_id": "unit-01",
        "trip_id": "trip-1",
        "shape_id": "hacia_artes",
    }
    with patch("simulator_app.api.views.get_runtime", return_value=rt):
        resp = client.post("/sim/runs/track", body, format="json")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
    binder.track.assert_called_once()


@pytest.mark.django_db
def test_track_run_unknown_vehicle(client: APIClient) -> None:
    rt = _make_runtime()
    body = {
        "run_id": "run-1",
        "vehicle_id": "unit-99",
        "trip_id": "trip-1",
        "shape_id": "hacia_artes",
    }
    with patch("simulator_app.api.views.get_runtime", return_value=rt):
        resp = client.post("/sim/runs/track", body, format="json")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /sim/control/<vehicle_id>/<knob>
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_control_transmit_on(client: APIClient) -> None:
    fleet = _make_fleet()
    fleet.get("unit-01").transmitting = False
    rt = _make_runtime(fleet=fleet)
    with patch("simulator_app.api.views.get_runtime", return_value=rt):
        resp = client.post(
            "/sim/control/unit-01/transmit", {"on": True}, format="json"
        )
    assert resp.status_code == 200
    assert fleet.get("unit-01").transmitting is True


@pytest.mark.django_db
def test_control_transmit_off(client: APIClient) -> None:
    fleet = _make_fleet()
    fleet.get("unit-01").transmitting = True
    rt = _make_runtime(fleet=fleet)
    with patch("simulator_app.api.views.get_runtime", return_value=rt):
        resp = client.post(
            "/sim/control/unit-01/transmit", {"on": False}, format="json"
        )
    assert resp.status_code == 200
    assert fleet.get("unit-01").transmitting is False


@pytest.mark.django_db
def test_control_speed(client: APIClient) -> None:
    fleet = _make_fleet()
    rt = _make_runtime(fleet=fleet)
    with patch("simulator_app.api.views.get_runtime", return_value=rt):
        resp = client.post(
            "/sim/control/unit-01/speed", {"value": 12.0}, format="json"
        )
    assert resp.status_code == 200
    assert fleet.get("unit-01").speed_override == 12.0


@pytest.mark.django_db
def test_control_malformed_returns_200_silently(client: APIClient) -> None:
    """Malformed control payloads are discarded silently (logged, not raised)."""
    rt = _make_runtime()
    with patch("simulator_app.api.views.get_runtime", return_value=rt):
        resp = client.post(
            "/sim/control/unit-01/transmit", {"bad": "value"}, format="json"
        )
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# POST /sim/control/global/<knob>
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_global_control_start_run(client: APIClient) -> None:
    fleet = _make_fleet()
    fleet.get("unit-01").moving = False
    rt = _make_runtime(fleet=fleet)
    with patch("simulator_app.api.views.get_runtime", return_value=rt):
        resp = client.post(
            "/sim/control/global/start_run",
            {"vehicle_id": "unit-01"},
            format="json",
        )
    assert resp.status_code == 200
    assert fleet.get("unit-01").moving is True


@pytest.mark.django_db
def test_global_control_reload_schedule(client: APIClient, tmp_sched: Path) -> None:
    fleet = _make_fleet()
    scheduler = _make_scheduler(tmp_sched, fleet)
    rt = _make_runtime(fleet=fleet, scheduler=scheduler)
    with patch("simulator_app.api.views.get_runtime", return_value=rt):
        resp = client.post("/sim/control/global/reload_schedule", {}, format="json")
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# GET /sim/geometry
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=False)
def clear_geometry_cache() -> None:
    """Reset the module-level geometry cache between geometry tests."""
    import simulator_app.api.views as v

    v._geometry_cache.clear()
    yield
    v._geometry_cache.clear()


@pytest.mark.django_db
def test_geometry_returns_200_with_routes(client: APIClient, clear_geometry_cache: None) -> None:
    """GET /sim/geometry returns HTTP 200 with a non-empty routes list."""
    resp = client.get("/sim/geometry")
    assert resp.status_code == 200
    body = resp.json()
    assert "routes" in body
    assert len(body["routes"]) > 0


@pytest.mark.django_db
def test_geometry_route_ids(client: APIClient, clear_geometry_cache: None) -> None:
    """Response includes bUCR_L1 and bUCR_L2."""
    resp = client.get("/sim/geometry")
    assert resp.status_code == 200
    route_ids = {r["route_id"] for r in resp.json()["routes"]}
    assert "bUCR_L1" in route_ids
    assert "bUCR_L2" in route_ids


@pytest.mark.django_db
def test_geometry_route_structure(client: APIClient, clear_geometry_cache: None) -> None:
    """Each route has route_id, non-empty shapes, and non-empty stops."""
    resp = client.get("/sim/geometry")
    assert resp.status_code == 200
    for route in resp.json()["routes"]:
        assert "route_id" in route
        assert "shapes" in route and len(route["shapes"]) > 0
        assert "stops" in route and len(route["stops"]) > 0


@pytest.mark.django_db
def test_geometry_shape_latlngs_and_stops(client: APIClient, clear_geometry_cache: None) -> None:
    """Each shape has latlngs (list of [lat,lon]) and stops with stop_id + ordered progress_m."""
    resp = client.get("/sim/geometry")
    assert resp.status_code == 200
    for route in resp.json()["routes"]:
        for shape in route["shapes"]:
            # latlngs must be a non-empty list of two-element pairs
            assert "latlngs" in shape
            latlngs = shape["latlngs"]
            assert len(latlngs) > 0
            for pair in latlngs:
                assert len(pair) == 2, f"Expected [lat, lon] pair, got: {pair}"

            # stops must have stop_id and progress_m, ordered non-decreasing
            assert "stops" in shape
            stops = shape["stops"]
            for stop in stops:
                assert "stop_id" in stop
                assert "progress_m" in stop
            progress_values = [s["progress_m"] for s in stops]
            assert progress_values == sorted(progress_values), (
                f"stops not ordered by progress_m in shape {shape['shape_id']}"
            )


@pytest.mark.django_db
def test_geometry_missing_shapes_file_returns_empty(
    client: APIClient, clear_geometry_cache: None, settings: Any
) -> None:
    """When SHAPES_PATH is missing, the view returns {"routes": []} with HTTP 200."""
    settings.SHAPES_PATH = "/nonexistent/path/shapes.json"
    resp = client.get("/sim/geometry")
    assert resp.status_code == 200
    assert resp.json() == {"routes": []}
