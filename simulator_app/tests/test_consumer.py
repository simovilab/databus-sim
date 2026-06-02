"""Channels WebSocket consumer tests.

Tests use channels.testing.WebsocketCommunicator.
"""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch

from channels.testing import WebsocketCommunicator

from sim_project.asgi import application
from simulator_app.domain.fleet import FleetState, Vehicle
from simulator_app.domain.kinematics import _init_kin


def _make_fleet() -> FleetState:
    vehicles = [
        Vehicle(vehicle_id="unit-01", route_id="bUCR_L1", default_shape_id="hacia_artes"),
    ]
    for v in vehicles:
        _init_kin(v)
    return FleetState(vehicles)


def _make_runtime(fleet: FleetState | None = None) -> MagicMock:
    rt = MagicMock()
    rt.fleet = fleet or _make_fleet()
    rt.scheduler = MagicMock()
    rt.scheduler.snapshot.return_value = {"defaults": {}, "runs": [], "published_at": "now"}
    return rt


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_consumer_connect_sends_initial_snapshots() -> None:
    """On connect, FleetConsumer immediately sends fleet + schedule snapshots."""
    fleet = _make_fleet()
    rt = _make_runtime(fleet=fleet)

    with patch("simulator_app.realtime.consumers.get_runtime", return_value=rt):
        communicator = WebsocketCommunicator(application, "/ws/fleet/")
        connected, _ = await communicator.connect()
        assert connected

        # Should receive fleet snapshot first
        fleet_msg = await communicator.receive_json_from(timeout=2)
        assert fleet_msg["type"] == "fleet"
        assert "vehicles" in fleet_msg["payload"]

        # Then schedule snapshot
        sched_msg = await communicator.receive_json_from(timeout=2)
        assert sched_msg["type"] == "schedule"
        assert "runs" in sched_msg["payload"]

        await communicator.disconnect()


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_consumer_receives_fleet_broadcast() -> None:
    """Fleet mutation pushed via broadcast_fleet reaches all connected consumers."""
    from simulator_app.realtime.broadcast import broadcast_fleet

    fleet = _make_fleet()
    rt = _make_runtime(fleet=fleet)

    with patch("simulator_app.realtime.consumers.get_runtime", return_value=rt):
        communicator = WebsocketCommunicator(application, "/ws/fleet/")
        connected, _ = await communicator.connect()
        assert connected

        # Drain initial messages
        await communicator.receive_json_from(timeout=2)
        await communicator.receive_json_from(timeout=2)

        # Push a fleet update
        snapshot = fleet.snapshot()
        snapshot["test_marker"] = "pushed"
        await broadcast_fleet(snapshot)

        msg = await communicator.receive_json_from(timeout=2)
        assert msg["type"] == "fleet"

        await communicator.disconnect()
