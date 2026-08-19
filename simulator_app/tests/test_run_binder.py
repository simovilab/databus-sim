"""Unit tests for simulator_app.services.run_binder."""

from __future__ import annotations

import asyncio
import pytest

from simulator_app.domain.fleet import FLEET, FleetState
from simulator_app.services.run_binder import BoundRun, RunBinder


class FakeDatabus:
    def __init__(self) -> None:
        self.states: dict[str, str | None] = {}

    async def get_run_state(self, run_id: str) -> str | None:
        return self.states.get(run_id)


@pytest.fixture
def fleet() -> FleetState:
    return FleetState(vehicles=list(FLEET))


@pytest.fixture
def databus() -> FakeDatabus:
    return FakeDatabus()


@pytest.fixture
def binder(fleet: FleetState, databus: FakeDatabus) -> RunBinder:
    return RunBinder(fleet=fleet, databus=databus, poll_interval_s=0.05)


# ---------------------------------------------------------------------------
# track / untrack
# ---------------------------------------------------------------------------


def test_track_adds_binding(binder: RunBinder) -> None:
    binder.track("run-1", "unit-01", "trip-001", "hacia_artes")
    assert len(binder.bindings()) == 1


def test_untrack_removes_binding(binder: RunBinder) -> None:
    binder.track("run-1", "unit-01", "trip-001", "hacia_artes")
    binder.untrack("run-1")
    assert binder.bindings() == []


def test_untrack_missing_is_noop(binder: RunBinder) -> None:
    binder.untrack("does-not-exist")  # must not raise


def test_track_with_terminal_stop(binder: RunBinder) -> None:
    binder.track("run-2", "unit-01", "trip-001", "hacia_artes", terminal_stop_id="stop-99")
    assert binder.bindings()[0].terminal_stop_id == "stop-99"


# ---------------------------------------------------------------------------
# _apply_state
# ---------------------------------------------------------------------------


def test_apply_confirmed_binds_and_transmits(binder: RunBinder, fleet: FleetState) -> None:
    binder.track("run-3", "unit-01", "trip-001", "hacia_artes")
    binding = binder._bindings["run-3"]

    order: list[str] = []

    def _bind(vid, rid, tid, sid, terminal_stop_id=None):  # type: ignore
        order.append("bind")

    def _transmit(vid, on):  # type: ignore
        order.append(f"transmit:{on}")

    fleet.bind_run = _bind  # type: ignore
    fleet.set_transmitting = _transmit  # type: ignore
    fleet.set_moving = lambda vid, on: order.append(f"moving:{on}")  # type: ignore
    fleet.set_lifecycle_state = lambda vid, s: None  # type: ignore

    binder._apply_state(binding, "Confirmed")
    assert "bind" in order
    assert "transmit:True" in order
    assert order.index("bind") < order.index("transmit:True")


def test_apply_terminal_unbinds(binder: RunBinder, fleet: FleetState) -> None:
    binder.track("run-4", "unit-01", "trip-001", "hacia_artes")
    binding = binder._bindings["run-4"]

    unbind_calls: list = []
    fleet.unbind_run = lambda vid: unbind_calls.append(vid)  # type: ignore
    fleet.set_transmitting = lambda vid, on: None  # type: ignore
    fleet.set_lifecycle_state = lambda vid, s: None  # type: ignore
    fleet.set_moving = lambda vid, on: None  # type: ignore

    for terminal in RunBinder.TERMINAL_STATES:
        unbind_calls.clear()
        binder._apply_state(binding, terminal)
        assert "unit-01" in unbind_calls


# ---------------------------------------------------------------------------
# poll_loop
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_poll_loop_fires_state_change_callback(
    binder: RunBinder, databus: FakeDatabus, fleet: FleetState
) -> None:
    fleet.bind_run = lambda *a, **kw: None  # type: ignore
    fleet.unbind_run = lambda *a: None  # type: ignore
    fleet.set_transmitting = lambda *a: None  # type: ignore
    fleet.set_moving = lambda *a: None  # type: ignore
    fleet.set_lifecycle_state = lambda *a: None  # type: ignore

    databus.states["poll-1"] = "Initialized"
    binder.track("poll-1", "unit-01", "trip-001", "hacia_artes")

    changes: list = []
    binder.on_state_change = lambda rid, old, new: changes.append((rid, old, new))

    task = asyncio.create_task(binder.poll_loop())
    await asyncio.sleep(0.2)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert ("poll-1", None, "Initialized") in changes


@pytest.mark.asyncio
async def test_poll_loop_run_not_found_force_unbinds(
    binder: RunBinder, databus: FakeDatabus, fleet: FleetState
) -> None:
    fleet.bind_run = lambda *a, **kw: None  # type: ignore
    fleet.set_transmitting = lambda *a: None  # type: ignore
    fleet.set_moving = lambda *a: None  # type: ignore
    fleet.set_lifecycle_state = lambda *a: None  # type: ignore
    unbound: list[str] = []
    fleet.unbind_run = lambda vid: unbound.append(vid)  # type: ignore

    databus.states["gone-1"] = "Confirmed"
    binder.track("gone-1", "unit-01", "trip-001", "hacia_artes")

    task = asyncio.create_task(binder.poll_loop())
    await asyncio.sleep(0.15)
    databus.states["gone-1"] = None
    await asyncio.sleep(0.15)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert "unit-01" in unbound
    assert binder.bindings() == []
