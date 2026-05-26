"""Integration tests for A2: MQTT controller + state publisher.

Tests call controller.handle() directly (no live broker needed) and verify
fleet state mutations and state_publisher snapshot emissions.

Run: uv run pytest sim/tests/test_controller.py -v -p no:anyio
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock, call, patch

import orjson
import pytest

from sim.fleet import FleetState, Vehicle
from sim.simulator import _init_kin
from sim.controller import Controller
from sim.state_publisher import StatePublisher


def _v(fleet: FleetState, vid: str) -> Vehicle:
    """Get vehicle by ID; fail fast if not found (for test use only)."""
    v = fleet.get(vid)
    assert v is not None, f"vehicle {vid!r} not in fleet"
    return v


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


def _make_controller(fleet: FleetState | None = None) -> tuple[Controller, FleetState, MagicMock]:
    fleet = fleet or _make_fleet()
    client = MagicMock()
    ctrl = Controller(fleet, client)
    return ctrl, fleet, client


def _get_fleet_publishes(mock_client: MagicMock) -> list[dict]:
    """Return all payloads published to sim/state/fleet."""
    result = []
    for c in mock_client.publish.call_args_list:
        topic = c.args[0]
        if topic == "sim/state/fleet":
            result.append(orjson.loads(c.args[1]))
    return result


# ---------------------------------------------------------------------------
# Controller: transmit knob
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_transmit_false_silences_vehicle():
    """sim/control/unit-01/transmit {"on": false} → transmitting=False."""
    ctrl, fleet, _ = _make_controller()
    _v(fleet, "unit-01").transmitting = True

    ctrl.handle("sim/control/unit-01/transmit", {"on": False})

    assert _v(fleet, "unit-01").transmitting is False


@pytest.mark.unit
def test_transmit_true_enables_vehicle():
    ctrl, fleet, _ = _make_controller()
    _v(fleet, "unit-01").transmitting = False

    ctrl.handle("sim/control/unit-01/transmit", {"on": True})

    assert _v(fleet, "unit-01").transmitting is True


@pytest.mark.unit
def test_transmit_malformed_payload_does_not_crash():
    """Malformed transmit payload is discarded; no exception."""
    ctrl, fleet, _ = _make_controller()
    ctrl.handle("sim/control/unit-01/transmit", {"bad_key": "not_a_bool"})
    # No assertion — just must not raise.


# ---------------------------------------------------------------------------
# Controller: moving knob
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_moving_false_keeps_transmission_alive():
    """moving=False leaves transmitting unchanged."""
    ctrl, fleet, _ = _make_controller()
    v = _v(fleet, "unit-01")
    v.transmitting = True
    v.moving = True

    ctrl.handle("sim/control/unit-01/moving", {"on": False})

    assert v.moving is False
    assert v.transmitting is True   # transmission survives


# ---------------------------------------------------------------------------
# Controller: speed knob
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_speed_override_applied():
    """sim/control/unit-01/speed {"value": 15.0} sets speed_override=15.0."""
    ctrl, fleet, _ = _make_controller()

    ctrl.handle("sim/control/unit-01/speed", {"value": 15.0})

    assert _v(fleet, "unit-01").speed_override == 15.0


@pytest.mark.unit
def test_speed_override_released_by_null():
    ctrl, fleet, _ = _make_controller()
    _v(fleet, "unit-01").speed_override = 10.0

    ctrl.handle("sim/control/unit-01/speed", {"value": None})

    assert _v(fleet, "unit-01").speed_override is None


# ---------------------------------------------------------------------------
# Controller: global start_run
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_global_start_run_flips_moving():
    """sim/control/global/start_run {"vehicle_id":"unit-01"} → moving=True."""
    ctrl, fleet, _ = _make_controller()
    v = _v(fleet, "unit-01")
    v.moving = False

    ctrl.handle("sim/control/global/start_run", {"vehicle_id": "unit-01"})

    assert v.moving is True


@pytest.mark.unit
def test_global_start_run_calls_on_start_run_callback():
    """When on_start_run callback is registered, it gets called instead of direct mutation."""
    ctrl, fleet, _ = _make_controller()
    cb = MagicMock()
    ctrl.on_start_run = cb

    ctrl.handle("sim/control/global/start_run", {"vehicle_id": "unit-01"})

    cb.assert_called_once_with("unit-01")


# ---------------------------------------------------------------------------
# Controller: malformed payload general
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_malformed_payload_logged_not_raised():
    """Any handler that receives an unexpected payload structure must not raise."""
    ctrl, fleet, _ = _make_controller()
    bad_payloads = [
        ("sim/control/unit-01/transmit", {}),
        ("sim/control/unit-01/moving", {"on": "yes"}),
        ("sim/control/unit-01/speed", {"value": "fast"}),
        ("sim/control/unit-01/occupancy", {"value": 150}),
        ("sim/control/unit-01/inject_fault", {"kind": "unknown"}),
        ("sim/control/global/start_run", {}),
    ]
    for topic, payload in bad_payloads:
        ctrl.handle(topic, payload)  # must not raise


# ---------------------------------------------------------------------------
# Controller: unknown vehicle / topic
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_unknown_vehicle_id_does_not_crash():
    ctrl, fleet, _ = _make_controller()
    ctrl.handle("sim/control/unit-99/transmit", {"on": False})  # no raise


@pytest.mark.unit
def test_unknown_topic_shape_does_not_crash():
    ctrl, fleet, _ = _make_controller()
    ctrl.handle("totally/wrong/topic", {})  # no raise


# ---------------------------------------------------------------------------
# StatePublisher: snapshot on fleet change
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_state_publisher_emits_snapshot_on_change():
    """Any fleet state mutation triggers a sim/state/fleet publish."""
    fleet = _make_fleet()
    client = MagicMock()
    pub = StatePublisher(fleet, client)
    pub.start()

    # Clear calls from the boot snapshot.
    client.reset_mock()

    fleet.set_transmitting("unit-01", True)

    # Allow the throttle window to expire.
    time.sleep(StatePublisher.THROTTLE_S + 0.05)

    fleet_publishes = _get_fleet_publishes(client)
    assert len(fleet_publishes) >= 1, "Expected at least one sim/state/fleet publish"

    pub.stop()


@pytest.mark.unit
def test_state_publisher_boot_snapshot():
    """start() publishes an initial sim/state/fleet snapshot."""
    fleet = _make_fleet()
    client = MagicMock()
    pub = StatePublisher(fleet, client)
    pub.start()

    fleet_publishes = _get_fleet_publishes(client)
    assert len(fleet_publishes) >= 1

    pub.stop()


@pytest.mark.unit
def test_state_publisher_boot_schedule_snapshot():
    """start() publishes an empty sim/state/schedule snapshot."""
    fleet = _make_fleet()
    client = MagicMock()
    pub = StatePublisher(fleet, client)
    pub.start()

    sched_publishes = []
    for c in client.publish.call_args_list:
        if c.args[0] == "sim/state/schedule":
            sched_publishes.append(orjson.loads(c.args[1]))

    assert len(sched_publishes) >= 1
    assert sched_publishes[0]["entries"] == []

    pub.stop()


@pytest.mark.unit
def test_state_publisher_throttle():
    """Burst of changes coalesces into ≤ ceil(burst/throttle_window) publishes."""
    fleet = _make_fleet()
    client = MagicMock()
    pub = StatePublisher(fleet, client)
    pub.start()
    client.reset_mock()

    # Fire 10 rapid changes.
    for i in range(10):
        fleet.set_transmitting("unit-01", bool(i % 2))

    # Without sleeping, only the immediate publish (if any) fired.
    # Allow one full throttle window for deferred publish to fire.
    time.sleep(StatePublisher.THROTTLE_S + 0.05)

    fleet_publishes = _get_fleet_publishes(client)
    # Should be ≤ 2 (one immediate + one deferred), not 10.
    assert len(fleet_publishes) <= 2, (
        f"Expected ≤2 fleet publishes for 10 rapid changes, got {len(fleet_publishes)}"
    )

    pub.stop()
