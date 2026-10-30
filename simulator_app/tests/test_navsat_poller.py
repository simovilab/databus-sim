"""Tests for simulator_app.services.navsat_poller.navsat_poll_loop.

Follows the same create-task/sleep/cancel pattern as test_tick_loop.py.
The NavSatClient is faked (no real HTTP) so these are pure unit tests of the
loop's broadcast/resilience behavior.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from simulator_app.services.navsat_client import NavSatClientError, NavSatRecord
from simulator_app.services.navsat_poller import _to_snapshot, navsat_poll_loop


def _record(plate: str = "SJB1234", estado: str = "movimiento") -> NavSatRecord:
    return NavSatRecord(plate_number=plate, latitude=9.9, longitude=-84.0, estado=estado)


def test_to_snapshot_keeps_estado_verbatim() -> None:
    snapshot = _to_snapshot([_record("A111", "movimiento"), _record("B222", "detenido")])
    assert snapshot == [
        {"plate_number": "A111", "latitude": 9.9, "longitude": -84.0, "estado": "movimiento"},
        {"plate_number": "B222", "latitude": 9.9, "longitude": -84.0, "estado": "detenido"},
    ]


@pytest.mark.asyncio
@pytest.mark.django_db
async def test_poll_loop_broadcasts_snapshot_and_caches_on_runtime() -> None:
    import simulator_app.realtime.broadcast as broadcast_mod
    from simulator_app import runtime as rt_mod

    fake_client = AsyncMock()
    fake_client.fetch.return_value = [_record("A111", "movimiento")]

    broadcast_calls: list[list[dict]] = []

    async def fake_broadcast_navsat(snapshot: list[dict]) -> None:
        broadcast_calls.append(snapshot)

    original = broadcast_mod.broadcast_navsat
    broadcast_mod.broadcast_navsat = fake_broadcast_navsat  # type: ignore[assignment]

    try:
        task = asyncio.create_task(navsat_poll_loop(fake_client, interval_s=0.05))
        await asyncio.sleep(0.12)  # ~2 polls
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    finally:
        broadcast_mod.broadcast_navsat = original  # type: ignore[assignment]

    assert fake_client.fetch.await_count >= 2
    assert broadcast_calls, "expected at least one broadcast"
    assert broadcast_calls[-1] == [
        {"plate_number": "A111", "latitude": 9.9, "longitude": -84.0, "estado": "movimiento"}
    ]
    assert rt_mod._runtime._navsat_snapshot == broadcast_calls[-1]


@pytest.mark.asyncio
@pytest.mark.django_db
async def test_poll_loop_survives_fetch_errors() -> None:
    """A failed poll logs and continues; it must not kill the background task."""
    import simulator_app.realtime.broadcast as broadcast_mod

    fake_client = AsyncMock()
    fake_client.fetch.side_effect = NavSatClientError("boom")

    broadcast_calls: list[list[dict]] = []

    async def fake_broadcast_navsat(snapshot: list[dict]) -> None:
        broadcast_calls.append(snapshot)

    original = broadcast_mod.broadcast_navsat
    broadcast_mod.broadcast_navsat = fake_broadcast_navsat  # type: ignore[assignment]

    try:
        task = asyncio.create_task(navsat_poll_loop(fake_client, interval_s=0.05))
        await asyncio.sleep(0.12)
        assert not task.done()  # still alive despite repeated fetch errors
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    finally:
        broadcast_mod.broadcast_navsat = original  # type: ignore[assignment]

    assert fake_client.fetch.await_count >= 2
    assert broadcast_calls == []
