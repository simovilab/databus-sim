"""Unit tests for A1: telemetry shape, transmit gate, end-of-run idle.

All tests run against mock MQTT clients; no broker needed.
Run: uv run pytest sim/tests/test_telemetry_shape.py -v
"""

from __future__ import annotations

import orjson
import pytest
from unittest.mock import MagicMock

from sim.fleet import Vehicle
from sim.simulator import (
    POST_RUN_IDLE_S,
    STOP_RADIUS_M,
    Shape,
    _init_kin,
    _nearest_stop,
    publish_vehicle,
    step_vehicle,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_shape(shape_id: str = "test") -> Shape:
    """Minimal two-point shape: (9.9, -84.0) → (9.91, -84.0), ~1112 m."""
    return Shape(
        shape_id=shape_id,
        points=[
            (9.9, -84.0, 0.0),
            (9.91, -84.0, 1.112),
        ],
    )


def _make_stop(stop_id: str, lat: float, lon: float) -> dict:
    return {"stop_id": stop_id, "name": "Test", "lat": lat, "lon": lon}


def _published_payloads(mock_client: MagicMock) -> dict[str, dict]:
    """Return {leaf: decoded_payload} from all mock_client.publish calls."""
    result = {}
    for c in mock_client.publish.call_args_list:
        topic: str = c.args[0]
        leaf = topic.split("/")[-1]
        result[leaf] = orjson.loads(c.args[1])
    return result


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

@pytest.mark.unit
def test_no_publish_when_not_transmitting():
    """Vehicle with transmitting=False must produce zero MQTT publishes."""
    client = MagicMock()
    shape = _make_shape()
    stops: list = []
    v = _fresh_vehicle()
    v.transmitting = False

    publish_vehicle(client, v, shape, stops)

    client.publish.assert_not_called()


# ---------------------------------------------------------------------------
# Speed field
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_speed_zero_when_not_moving():
    """Stationary transmitting vehicle publishes speed=0.0."""
    client = MagicMock()
    shape = _make_shape()
    v = _fresh_vehicle()
    v.transmitting = True
    v.moving = False

    publish_vehicle(client, v, shape, [])

    payloads = _published_payloads(client)
    assert payloads["position"]["speed"] == 0.0


@pytest.mark.unit
def test_speed_positive_when_moving():
    """Moving transmitting vehicle publishes speed > 0."""
    client = MagicMock()
    shape = _make_shape()
    v = _fresh_vehicle()
    v.transmitting = True
    v.moving = True
    v._kin["speed"] = 8.0

    publish_vehicle(client, v, shape, [])

    payloads = _published_payloads(client)
    assert payloads["position"]["speed"] > 0


@pytest.mark.unit
def test_speed_zero_when_dwelling():
    """Vehicle with dwell_remaining > 0 publishes speed=0.0 even if moving=True."""
    client = MagicMock()
    shape = _make_shape()
    v = _fresh_vehicle()
    v.transmitting = True
    v.moving = True
    v._kin["speed"] = 8.0
    v._kin["dwell_remaining"] = 3

    publish_vehicle(client, v, shape, [])

    payloads = _published_payloads(client)
    assert payloads["position"]["speed"] == 0.0


# ---------------------------------------------------------------------------
# Progression status — STOPPED_AT with dwell
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_stopped_at_when_dwelling_near_stop():
    """Vehicle dwelling within STOP_RADIUS_M of a stop → STOPPED_AT + stop_id."""
    client = MagicMock()
    # Place vehicle at (9.9, -84.0); put stop right there.
    shape = _make_shape()
    stop = _make_stop("s01", lat=9.9, lon=-84.0)
    v = _fresh_vehicle()
    v.transmitting = True
    v.moving = True          # moving=True but dwell_remaining>0 → effectively stopped
    v.progress_m = 0.0       # at start of shape = (9.9, -84.0)
    v._kin["dwell_remaining"] = 3
    v._kin["speed"] = 8.0

    publish_vehicle(client, v, shape, [stop])

    payloads = _published_payloads(client)
    prog = payloads["progression"]
    assert prog["current_status"] == "STOPPED_AT", prog
    assert prog["stop_id"] == "s01"


@pytest.mark.unit
def test_stopped_at_when_not_moving_near_stop():
    """Vehicle with moving=False within STOP_RADIUS_M → STOPPED_AT."""
    client = MagicMock()
    shape = _make_shape()
    stop = _make_stop("s01", lat=9.9, lon=-84.0)
    v = _fresh_vehicle()
    v.transmitting = True
    v.moving = False
    v.progress_m = 0.0

    publish_vehicle(client, v, shape, [stop])

    payloads = _published_payloads(client)
    assert payloads["progression"]["current_status"] == "STOPPED_AT"
    assert payloads["progression"]["stop_id"] == "s01"


# ---------------------------------------------------------------------------
# data topic must never appear
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_data_topic_never_published():
    """No publish call may target the 'data' leaf."""
    client = MagicMock()
    shape = _make_shape()
    v = _fresh_vehicle()
    v.transmitting = True
    v.moving = True
    v._kin["speed"] = 5.0

    publish_vehicle(client, v, shape, [])

    topics = [c.args[0] for c in client.publish.call_args_list]
    assert not any(t.endswith("/data") for t in topics), f"data topic in {topics}"


# ---------------------------------------------------------------------------
# End-of-run idle
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_end_of_run_stops_moving_and_starts_idle():
    """Vehicle reaching total_dist_m sets moving=False and starts idle countdown."""
    shape = _make_shape()
    v = _fresh_vehicle()
    v.transmitting = True
    v.moving = True
    v._kin["speed"] = 8.0
    # Place just before terminal.
    v.progress_m = shape.total_dist_m - 0.01
    dt = 2.0

    step_vehicle(v, dt, shape, [])

    assert v.progress_m == shape.total_dist_m
    assert v.moving is False
    assert v._kin["post_run_idle_remaining_s"] == float(POST_RUN_IDLE_S)


@pytest.mark.unit
def test_idle_countdown_stops_transmitting():
    """After POST_RUN_IDLE_S seconds of idle, transmitting flips to False."""
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


@pytest.mark.unit
def test_idle_vehicle_still_publishes_until_expired():
    """During idle countdown vehicle keeps publishing (transmitting=True)."""
    client = MagicMock()
    shape = _make_shape()
    v = _fresh_vehicle()
    v.transmitting = True
    v.moving = False
    # More than one tick of idle left.
    v._kin["post_run_idle_remaining_s"] = float(POST_RUN_IDLE_S)

    publish_vehicle(client, v, shape, [])

    # Still transmitting → should have published
    assert client.publish.called


# ---------------------------------------------------------------------------
# Speed override
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_speed_override_applied():
    """step_vehicle uses speed_override instead of random drift."""
    shape = _make_shape()
    v = _fresh_vehicle()
    v.transmitting = True
    v.moving = True
    v.speed_override = 15.0
    v._kin["speed"] = 1.0  # would be below MIN_SPEED without override
    dt = 2.0

    step_vehicle(v, dt, shape, [])

    assert v._kin["speed"] == 15.0


@pytest.mark.unit
def test_speed_override_reflected_in_publish():
    """Publish uses speed_override value in position payload."""
    client = MagicMock()
    shape = _make_shape()
    v = _fresh_vehicle()
    v.transmitting = True
    v.moving = True
    v.speed_override = 15.0
    v._kin["speed"] = 15.0  # set after step would have applied it

    publish_vehicle(client, v, shape, [])

    payloads = _published_payloads(client)
    assert payloads["position"]["speed"] == 15.0
