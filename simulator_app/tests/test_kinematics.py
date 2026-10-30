"""Unit tests for simulator_app.domain.kinematics (payload shape + kinematics)."""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock

from simulator_app.domain.fleet import Vehicle
from simulator_app.domain.kinematics import (
    POST_RUN_IDLE_S,
    STOP_RADIUS_M,
    Shape,
    _init_kin,
    _nearest_stop,
    build_vehicle_payloads,
    step_vehicle,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_shape(shape_id: str = "test") -> Shape:
    """Minimal two-point shape: ~1112 m."""
    return Shape(
        shape_id=shape_id,
        points=[
            (9.9, -84.0, 0.0),
            (9.91, -84.0, 1.112),
        ],
    )


def _make_stop(stop_id: str, lat: float, lon: float) -> dict:
    return {"stop_id": stop_id, "name": "Test", "lat": lat, "lon": lon}


def _fresh_vehicle(
    vehicle_id: str = "unit-01",
    route_id: str = "bUCR_L1",
    default_shape_id: str = "test",
) -> Vehicle:
    v = Vehicle(vehicle_id=vehicle_id, route_id=route_id, default_shape_id=default_shape_id)
    _init_kin(v)
    return v


# ---------------------------------------------------------------------------
# Transmit gate
# ---------------------------------------------------------------------------


def test_no_payload_when_not_transmitting():
    """Vehicle with transmitting=False must return None."""
    shape = _make_shape()
    v = _fresh_vehicle()
    v.transmitting = False
    assert build_vehicle_payloads(v, shape, []) is None


# ---------------------------------------------------------------------------
# Payload structure
# ---------------------------------------------------------------------------


def test_payload_has_two_leaves():
    """Transmitting vehicle returns only position and occupancy (no progression)."""
    shape = _make_shape()
    v = _fresh_vehicle()
    v.transmitting = True
    v.moving = True
    payloads = build_vehicle_payloads(v, shape, [])
    assert payloads is not None
    assert set(payloads.keys()) == {"position", "occupancy"}


def test_progression_leaf_not_emitted():
    """The server-owned 'progression' leaf must never be published."""
    shape = _make_shape()
    stop = _make_stop("s01", lat=9.9, lon=-84.0)
    v = _fresh_vehicle()
    v.transmitting = True
    v.moving = True
    payloads = build_vehicle_payloads(v, shape, [stop])
    assert "progression" not in payloads


def test_position_has_required_fields():
    shape = _make_shape()
    v = _fresh_vehicle()
    v.transmitting = True
    v.moving = True
    v._kin["speed"] = 8.0
    payloads = build_vehicle_payloads(v, shape, [])
    pos = payloads["position"]
    assert "timestamp" in pos
    assert "latitude" in pos
    assert "longitude" in pos
    assert "bearing" in pos
    assert "speed" in pos
    assert "odometer" in pos


def test_speed_zero_when_not_moving():
    shape = _make_shape()
    v = _fresh_vehicle()
    v.transmitting = True
    v.moving = False
    payloads = build_vehicle_payloads(v, shape, [])
    assert payloads["position"]["speed"] == 0.0


def test_speed_positive_when_moving():
    shape = _make_shape()
    v = _fresh_vehicle()
    v.transmitting = True
    v.moving = True
    v._kin["speed"] = 8.0
    payloads = build_vehicle_payloads(v, shape, [])
    assert payloads["position"]["speed"] > 0


def test_speed_zero_when_dwelling():
    shape = _make_shape()
    v = _fresh_vehicle()
    v.transmitting = True
    v.moving = True
    v._kin["speed"] = 8.0
    v._kin["dwell_remaining"] = 3
    payloads = build_vehicle_payloads(v, shape, [])
    assert payloads["position"]["speed"] == 0.0


# ---------------------------------------------------------------------------
# Occupancy contract (raw measurement only — no server-owned enum)
# ---------------------------------------------------------------------------


def test_occupancy_has_percentage():
    shape = _make_shape()
    v = _fresh_vehicle()
    v.transmitting = True
    v.moving = True
    v._kin["occupancy_pct"] = 42
    payloads = build_vehicle_payloads(v, shape, [])
    occ = payloads["occupancy"]
    assert occ["occupancy_percentage"] == 42


def test_occupancy_status_not_on_wire():
    """occupancy_status is server-owned; the edge must not send it."""
    shape = _make_shape()
    v = _fresh_vehicle()
    v.transmitting = True
    v.moving = True
    payloads = build_vehicle_payloads(v, shape, [])
    assert "occupancy_status" not in payloads["occupancy"]


def test_occupancy_override_reflected_in_payload():
    shape = _make_shape()
    v = _fresh_vehicle()
    v.transmitting = True
    v.moving = True
    v.occupancy_override = 90
    payloads = build_vehicle_payloads(v, shape, [])
    assert payloads["occupancy"]["occupancy_percentage"] == 90


def test_no_data_leaf_in_topics():
    """The 'data' leaf must never appear in payloads."""
    shape = _make_shape()
    v = _fresh_vehicle()
    v.transmitting = True
    v.moving = True
    v._kin["speed"] = 5.0
    payloads = build_vehicle_payloads(v, shape, [])
    assert "data" not in payloads


# ---------------------------------------------------------------------------
# End-of-run idle
# ---------------------------------------------------------------------------


def test_end_of_run_stops_moving_and_starts_idle():
    shape = _make_shape()
    v = _fresh_vehicle()
    v.transmitting = True
    v.moving = True
    v._kin["speed"] = 8.0
    v.progress_m = shape.total_dist_m - 0.01
    dt = 2.0
    step_vehicle(v, dt, shape, [])
    assert v.progress_m == shape.total_dist_m
    assert v.moving is False
    assert v._kin["post_run_idle_remaining_s"] == float(POST_RUN_IDLE_S)


def test_idle_countdown_stops_transmitting():
    shape = _make_shape()
    v = _fresh_vehicle()
    v.transmitting = True
    v.moving = False
    v._kin["post_run_idle_remaining_s"] = float(POST_RUN_IDLE_S)
    dt = 2.0
    ticks = int(POST_RUN_IDLE_S / dt) + 1
    for _ in range(ticks):
        step_vehicle(v, dt, shape, [])
    assert v.transmitting is False


def test_idle_vehicle_still_has_payloads():
    """During idle countdown vehicle still transmitting → payloads returned."""
    shape = _make_shape()
    v = _fresh_vehicle()
    v.transmitting = True
    v.moving = False
    v._kin["post_run_idle_remaining_s"] = float(POST_RUN_IDLE_S)
    payloads = build_vehicle_payloads(v, shape, [])
    assert payloads is not None


# ---------------------------------------------------------------------------
# Speed override
# ---------------------------------------------------------------------------


def test_speed_override_applied_in_step():
    shape = _make_shape()
    v = _fresh_vehicle()
    v.transmitting = True
    v.moving = True
    v.speed_override = 15.0
    v._kin["speed"] = 1.0
    step_vehicle(v, 2.0, shape, [])
    assert v._kin["speed"] == 15.0


def test_speed_override_reflected_in_payload():
    shape = _make_shape()
    v = _fresh_vehicle()
    v.transmitting = True
    v.moving = True
    v.speed_override = 15.0
    v._kin["speed"] = 15.0
    payloads = build_vehicle_payloads(v, shape, [])
    assert payloads["position"]["speed"] == 15.0
