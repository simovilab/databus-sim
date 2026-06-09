"""Tests for runtime.tick_loop: verify one tick produces correct payloads."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from simulator_app.domain.fleet import FleetState, Vehicle
from simulator_app.domain.kinematics import _init_kin, load_shapes
from pathlib import Path

import django
from django.conf import settings


def _make_transmitting_fleet() -> FleetState:
    vehicles = [
        Vehicle(vehicle_id="unit-01", route_id="bUCR_L1", default_shape_id="hacia_artes"),
    ]
    for v in vehicles:
        _init_kin(v)
        v.transmitting = True
        v.moving = True
    return FleetState(vehicles)


@pytest.mark.asyncio
@pytest.mark.django_db
async def test_tick_loop_publishes_to_paho_and_broadcasts() -> None:
    """One tick should paho-publish the position/occupancy leafs (no progression)."""
    import simulator_app.realtime.broadcast as broadcast_mod
    from simulator_app import runtime as rt_mod

    fleet = _make_transmitting_fleet()
    shapes_path = Path(settings.SHAPES_PATH)
    shapes, stops, _ = load_shapes(shapes_path)

    # Set up a fake runtime
    mock_mqtt = MagicMock()
    rt_mod._runtime.fleet = fleet
    rt_mod._runtime._shapes = shapes
    rt_mod._runtime._stops = stops
    rt_mod._runtime._mqtt_topic_root = "transit/vehicle"
    rt_mod._runtime.mqtt = mock_mqtt

    broadcast_calls: list[tuple[str, str, dict]] = []

    async def fake_broadcast_telemetry(vid: str, leaf: str, data: dict[str, Any]) -> None:
        broadcast_calls.append((vid, leaf, data))

    async def fake_broadcast_fleet(snap: dict[str, Any]) -> None:
        pass

    # Patch the broadcast module's functions directly so tick_loop sees the patch
    original_telemetry = broadcast_mod.broadcast_telemetry
    original_fleet = broadcast_mod.broadcast_fleet
    broadcast_mod.broadcast_telemetry = fake_broadcast_telemetry  # type: ignore[assignment]
    broadcast_mod.broadcast_fleet = fake_broadcast_fleet  # type: ignore[assignment]

    try:
        with (
            patch.object(django.conf.settings, "SIM_TICK_INTERVAL", 0.05),
            patch.object(django.conf.settings, "SIM_ONLY_VEHICLES", set()),
            patch.object(django.conf.settings, "SIM_STOP_VEHICLES", set()),
            patch.object(django.conf.settings, "SIM_RANDOM_DROP_RATE", 0),
            patch.object(django.conf.settings, "SIM_STOP_ALL_AFTER", 0),
        ):
            task = asyncio.create_task(rt_mod.tick_loop())
            await asyncio.sleep(0.15)  # ~3 ticks at 0.05s
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
    finally:
        broadcast_mod.broadcast_telemetry = original_telemetry  # type: ignore[assignment]
        broadcast_mod.broadcast_fleet = original_fleet  # type: ignore[assignment]

    # paho.publish should have been called for position/occupancy only.
    assert mock_mqtt.publish.called
    topics_published = [c.args[0] for c in mock_mqtt.publish.call_args_list]
    assert any("position" in t for t in topics_published)
    assert any("occupancy" in t for t in topics_published)
    # progression is server-owned and must not be published.
    assert not any("progression" in t for t in topics_published)


@pytest.mark.asyncio
@pytest.mark.django_db
async def test_tick_loop_non_transmitting_vehicle_produces_no_payloads() -> None:
    """Non-transmitting vehicle generates no MQTT publishes and no telemetry broadcasts."""
    import simulator_app.realtime.broadcast as broadcast_mod
    from simulator_app import runtime as rt_mod

    fleet = FleetState(
        [Vehicle(vehicle_id="unit-01", route_id="bUCR_L1", default_shape_id="hacia_artes")]
    )
    for v in fleet.all():
        _init_kin(v)
        v.transmitting = False

    shapes_path = Path(settings.SHAPES_PATH)
    shapes, stops, _ = load_shapes(shapes_path)

    mock_mqtt = MagicMock()
    rt_mod._runtime.fleet = fleet
    rt_mod._runtime._shapes = shapes
    rt_mod._runtime._stops = stops
    rt_mod._runtime._mqtt_topic_root = "transit/vehicle"
    rt_mod._runtime.mqtt = mock_mqtt

    broadcast_calls: list = []

    async def fake_broadcast_telemetry(vid: str, leaf: str, data: Any) -> None:
        broadcast_calls.append((vid, leaf))

    async def fake_broadcast_fleet(snap: Any) -> None:
        pass

    original_telemetry = broadcast_mod.broadcast_telemetry
    original_fleet = broadcast_mod.broadcast_fleet
    broadcast_mod.broadcast_telemetry = fake_broadcast_telemetry  # type: ignore[assignment]
    broadcast_mod.broadcast_fleet = fake_broadcast_fleet  # type: ignore[assignment]

    try:
        with (
            patch.object(django.conf.settings, "SIM_TICK_INTERVAL", 0.05),
            patch.object(django.conf.settings, "SIM_ONLY_VEHICLES", set()),
            patch.object(django.conf.settings, "SIM_STOP_VEHICLES", set()),
            patch.object(django.conf.settings, "SIM_RANDOM_DROP_RATE", 0),
            patch.object(django.conf.settings, "SIM_STOP_ALL_AFTER", 0),
        ):
            task = asyncio.create_task(rt_mod.tick_loop())
            await asyncio.sleep(0.12)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
    finally:
        broadcast_mod.broadcast_telemetry = original_telemetry  # type: ignore[assignment]
        broadcast_mod.broadcast_fleet = original_fleet  # type: ignore[assignment]

    assert broadcast_calls == []
    assert not mock_mqtt.publish.called
