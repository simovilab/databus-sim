"""Unit tests for simulator_app.domain.fleet."""

from __future__ import annotations

import pytest

from simulator_app.domain.fleet import FleetState, Vehicle, FLEET


def _make_fleet() -> FleetState:
    vehicles = [
        Vehicle(vehicle_id="unit-01", route_id="bUCR_L1", default_shape_id="hacia_artes"),
        Vehicle(vehicle_id="unit-02", route_id="bUCR_L1", default_shape_id="hacia_educacion"),
    ]
    return FleetState(vehicles)


def test_fleet_has_six_vehicles():
    fleet = FleetState()
    assert len(fleet.all()) == 6


def test_fleet_get_known_vehicle():
    fleet = _make_fleet()
    v = fleet.get("unit-01")
    assert v is not None
    assert v.vehicle_id == "unit-01"


def test_fleet_get_unknown_vehicle_returns_none():
    fleet = _make_fleet()
    assert fleet.get("unit-99") is None


def test_set_transmitting_true():
    fleet = _make_fleet()
    fleet.set_transmitting("unit-01", True)
    assert fleet.get("unit-01").transmitting is True


def test_set_transmitting_false():
    fleet = _make_fleet()
    fleet.get("unit-01").transmitting = True
    fleet.set_transmitting("unit-01", False)
    assert fleet.get("unit-01").transmitting is False


def test_set_moving():
    fleet = _make_fleet()
    fleet.set_moving("unit-01", True)
    assert fleet.get("unit-01").moving is True


def test_set_speed_override():
    fleet = _make_fleet()
    fleet.set_speed_override("unit-01", 10.0)
    assert fleet.get("unit-01").speed_override == 10.0


def test_set_speed_override_null():
    fleet = _make_fleet()
    fleet.get("unit-01").speed_override = 10.0
    fleet.set_speed_override("unit-01", None)
    assert fleet.get("unit-01").speed_override is None


def test_set_occupancy_override():
    fleet = _make_fleet()
    fleet.set_occupancy_override("unit-01", 80)
    assert fleet.get("unit-01").occupancy_override == 80


def test_bind_run():
    fleet = _make_fleet()
    fleet.bind_run("unit-01", "run-1", "trip-1", "hacia_artes", "stop-99")
    v = fleet.get("unit-01")
    assert v.bound_run_id == "run-1"
    assert v.terminal_stop_id == "stop-99"


def test_unbind_run():
    fleet = _make_fleet()
    fleet.bind_run("unit-01", "run-1", "trip-1", "hacia_artes")
    fleet.unbind_run("unit-01")
    v = fleet.get("unit-01")
    assert v.bound_run_id is None
    assert v.transmitting is False


def test_on_change_callback_fired():
    fleet = _make_fleet()
    fired = []
    fleet.on_change.append(lambda: fired.append(1))
    fleet.set_transmitting("unit-01", True)
    assert len(fired) >= 1


def test_snapshot_has_all_vehicles():
    fleet = _make_fleet()
    snap = fleet.snapshot()
    assert "vehicles" in snap
    assert len(snap["vehicles"]) == 2
    assert "published_at" in snap


def test_require_raises_for_unknown():
    fleet = _make_fleet()
    with pytest.raises(KeyError):
        fleet.set_transmitting("unit-99", True)
