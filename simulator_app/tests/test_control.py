"""Unit tests for simulator_app.domain.control (apply_control / apply_global_control)."""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch

from simulator_app.domain.fleet import FleetState, Vehicle
from simulator_app.domain.kinematics import _init_kin
from simulator_app.domain.control import apply_control, apply_global_control


def _make_fleet() -> FleetState:
    vehicles = [
        Vehicle(vehicle_id="unit-01", route_id="bUCR_L1", default_shape_id="hacia_artes"),
        Vehicle(vehicle_id="unit-02", route_id="bUCR_L1", default_shape_id="hacia_educacion"),
    ]
    for v in vehicles:
        _init_kin(v)
    return FleetState(vehicles)


def _make_runtime(fleet: FleetState | None = None) -> MagicMock:
    rt = MagicMock()
    rt.fleet = fleet or _make_fleet()
    rt.scheduler = MagicMock()
    rt.binder = MagicMock()
    return rt


# ---------------------------------------------------------------------------
# transmit knob
# ---------------------------------------------------------------------------


def test_transmit_false_silences_vehicle():
    rt = _make_runtime()
    rt.fleet.get("unit-01").transmitting = True
    apply_control(rt, "unit-01", "transmit", {"on": False})
    assert rt.fleet.get("unit-01").transmitting is False


def test_transmit_true_enables_vehicle():
    rt = _make_runtime()
    rt.fleet.get("unit-01").transmitting = False
    apply_control(rt, "unit-01", "transmit", {"on": True})
    assert rt.fleet.get("unit-01").transmitting is True


def test_transmit_malformed_does_not_raise():
    rt = _make_runtime()
    apply_control(rt, "unit-01", "transmit", {"bad_key": "not_a_bool"})
    # Must not raise


# ---------------------------------------------------------------------------
# moving knob
# ---------------------------------------------------------------------------


def test_moving_false_leaves_transmitting():
    rt = _make_runtime()
    v = rt.fleet.get("unit-01")
    v.transmitting = True
    v.moving = True
    apply_control(rt, "unit-01", "moving", {"on": False})
    assert v.moving is False
    assert v.transmitting is True


# ---------------------------------------------------------------------------
# speed knob
# ---------------------------------------------------------------------------


def test_speed_override_applied():
    rt = _make_runtime()
    apply_control(rt, "unit-01", "speed", {"value": 15.0})
    assert rt.fleet.get("unit-01").speed_override == 15.0


def test_speed_override_released_by_null():
    rt = _make_runtime()
    rt.fleet.get("unit-01").speed_override = 10.0
    apply_control(rt, "unit-01", "speed", {"value": None})
    assert rt.fleet.get("unit-01").speed_override is None


# ---------------------------------------------------------------------------
# occupancy knob
# ---------------------------------------------------------------------------


def test_occupancy_override_applied():
    rt = _make_runtime()
    apply_control(rt, "unit-01", "occupancy", {"value": 80})
    assert rt.fleet.get("unit-01").occupancy_override == 80


def test_occupancy_out_of_range_discarded():
    rt = _make_runtime()
    apply_control(rt, "unit-01", "occupancy", {"value": 150})
    assert rt.fleet.get("unit-01").occupancy_override is None


# ---------------------------------------------------------------------------
# global start_run
# ---------------------------------------------------------------------------


def test_global_start_run_flips_moving():
    rt = _make_runtime()
    rt.fleet.get("unit-01").moving = False
    apply_global_control(rt, "start_run", {"vehicle_id": "unit-01"})
    assert rt.fleet.get("unit-01").moving is True


# ---------------------------------------------------------------------------
# global reload_schedule
# ---------------------------------------------------------------------------


def test_global_reload_schedule_calls_scheduler():
    rt = _make_runtime()
    apply_global_control(rt, "reload_schedule", {})
    rt.scheduler.reload.assert_called_once()


# ---------------------------------------------------------------------------
# malformed payloads — never raise
# ---------------------------------------------------------------------------


def test_malformed_payloads_never_raise():
    rt = _make_runtime()
    bad_payloads = [
        ("unit-01", "transmit", {}),
        ("unit-01", "moving", {"on": "yes"}),
        ("unit-01", "speed", {"value": "fast"}),
        ("unit-01", "occupancy", {"value": 150}),
        ("unit-01", "inject_fault", {"kind": "unknown_kind"}),
        ("unit-01", "dwell", {"ticks": -1}),
    ]
    for vid, knob, payload in bad_payloads:
        apply_control(rt, vid, knob, payload)


# ---------------------------------------------------------------------------
# unknown vehicle / knob
# ---------------------------------------------------------------------------


def test_unknown_vehicle_does_not_crash():
    rt = _make_runtime()
    apply_control(rt, "unit-99", "transmit", {"on": False})


def test_unknown_knob_does_not_crash():
    rt = _make_runtime()
    apply_control(rt, "unit-01", "nonexistent_knob", {})


def test_unknown_global_knob_does_not_crash():
    rt = _make_runtime()
    apply_global_control(rt, "nonexistent", {})


# ---------------------------------------------------------------------------
# inject_fault knob
# ---------------------------------------------------------------------------


def test_inject_fault_sets_kin():
    rt = _make_runtime()
    v = rt.fleet.get("unit-01")
    apply_control(rt, "unit-01", "inject_fault", {"kind": "stale_ts", "duration_ticks": 3})
    assert v._kin.get("fault") == "stale_ts"
    assert v._kin.get("fault_ticks") == 3
