"""Unit tests for sim.run_binder using a fake databus HTTP client."""

from __future__ import annotations

import asyncio

import pytest

from sim.fleet import FLEET, FleetState
from sim.run_binder import BoundRun, RunBinder


class FakeDatabus:
    """Stand-in for DatabusClient.get_run_state backed by an in-memory map.

    ``states[run_id]`` → current ``run_lifecycle_state``; a missing key
    returns ``None`` (mirrors a 404 from ``GET /api/run/{run_id}/``).
    """

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
    b = binder.bindings()[0]
    assert b.run_id == "run-1"
    assert b.vehicle_id == "unit-01"


def test_untrack_removes_binding(binder: RunBinder) -> None:
    binder.track("run-1", "unit-01", "trip-001", "hacia_artes")
    binder.untrack("run-1")
    assert binder.bindings() == []


def test_untrack_missing_is_noop(binder: RunBinder) -> None:
    binder.untrack("does-not-exist")  # should not raise


def test_track_with_terminal_stop(binder: RunBinder) -> None:
    binder.track("run-2", "unit-01", "trip-001", "hacia_artes", terminal_stop_id="stop-99")
    b = binder.bindings()[0]
    assert b.terminal_stop_id == "stop-99"


# ---------------------------------------------------------------------------
# _apply_state
# ---------------------------------------------------------------------------


def test_apply_confirmed_binds_and_transmits(binder: RunBinder, fleet: FleetState) -> None:
    binder.track("run-3", "unit-01", "trip-001", "hacia_artes")
    binding = binder._bindings["run-3"]

    order: list[str] = []
    bound_calls: list = []
    transmit_calls: list = []

    def _bind(vid, rid, tid, sid, terminal_stop_id=None):  # type: ignore
        order.append("bind")
        bound_calls.append((vid, rid))

    def _transmit(vid, on):  # type: ignore
        order.append(f"transmit:{on}")
        transmit_calls.append((vid, on))

    fleet.bind_run = _bind  # type: ignore
    fleet.set_transmitting = _transmit  # type: ignore
    fleet.set_moving = lambda vid, on: order.append(f"moving:{on}")  # type: ignore
    fleet.set_lifecycle_state = lambda vid, s: None  # type: ignore

    binder._apply_state(binding, "Confirmed")

    assert len(bound_calls) == 1
    assert bound_calls[0] == ("unit-01", "run-3")
    assert ("unit-01", True) in transmit_calls
    # bind must come before transmit=True (CONTRACTS.md §7.4)
    assert order.index("bind") < order.index("transmit:True")


def test_apply_terminal_unbinds(binder: RunBinder, fleet: FleetState) -> None:
    binder.track("run-4", "unit-01", "trip-001", "hacia_artes")
    binding = binder._bindings["run-4"]

    unbind_calls: list = []
    transmit_calls: list = []
    fleet.unbind_run = lambda vid: unbind_calls.append(vid)  # type: ignore
    fleet.set_transmitting = lambda vid, on: transmit_calls.append((vid, on))  # type: ignore
    fleet.set_lifecycle_state = lambda vid, s: None  # type: ignore
    fleet.set_moving = lambda vid, on: None  # type: ignore

    for terminal in RunBinder.TERMINAL_STATES:
        unbind_calls.clear()
        transmit_calls.clear()
        binder._apply_state(binding, terminal)
        assert "unit-01" in unbind_calls
        assert ("unit-01", False) in transmit_calls


def test_apply_tracking_no_fleet_change(binder: RunBinder, fleet: FleetState) -> None:
    binder.track("run-5", "unit-01", "trip-001", "hacia_artes")
    binding = binder._bindings["run-5"]

    calls: list = []
    fleet.bind_run = lambda *a, **kw: calls.append("bind_run")  # type: ignore
    fleet.unbind_run = lambda *a: calls.append("unbind_run")  # type: ignore
    fleet.set_transmitting = lambda *a: calls.append("transmit")  # type: ignore
    fleet.set_moving = lambda *a: calls.append("moving")  # type: ignore
    fleet.set_lifecycle_state = lambda *a: None  # type: ignore

    for state in ("Tracking", "InProgress", "NoSignal"):
        calls.clear()
        binder._apply_state(binding, state)
        assert calls == [], f"Expected no fleet changes for {state}, got {calls}"


# ---------------------------------------------------------------------------
# poll_loop state progression
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_poll_loop_fires_state_change_callback(
    binder: RunBinder,
    databus: FakeDatabus,
    fleet: FleetState,
) -> None:
    fleet.bind_run = lambda *a, **kw: None  # type: ignore
    fleet.unbind_run = lambda *a: None  # type: ignore
    fleet.set_transmitting = lambda *a: None  # type: ignore
    fleet.set_moving = lambda *a: None  # type: ignore
    fleet.set_lifecycle_state = lambda *a: None  # type: ignore

    # databus reports Initialized for this run
    databus.states["poll-1"] = "Initialized"
    binder.track("poll-1", "unit-01", "trip-001", "hacia_artes")

    changes: list = []
    binder.on_state_change = lambda rid, old, new: changes.append((rid, old, new))

    task = asyncio.create_task(binder.poll_loop())
    await asyncio.sleep(0.2)  # let one poll tick through
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert ("poll-1", None, "Initialized") in changes


@pytest.mark.asyncio
async def test_poll_loop_confirmed_to_completed(
    binder: RunBinder,
    databus: FakeDatabus,
    fleet: FleetState,
) -> None:
    fleet.bind_run = lambda *a, **kw: None  # type: ignore
    fleet.unbind_run = lambda *a: None  # type: ignore
    fleet.set_transmitting = lambda *a: None  # type: ignore
    fleet.set_moving = lambda *a: None  # type: ignore
    fleet.set_lifecycle_state = lambda *a: None  # type: ignore

    databus.states["seq-1"] = "Confirmed"
    binder.track("seq-1", "unit-01", "trip-001", "hacia_artes")

    transitions: list = []
    binder.on_state_change = lambda rid, old, new: transitions.append((old, new))

    task = asyncio.create_task(binder.poll_loop())
    # After first poll: Confirmed
    await asyncio.sleep(0.15)
    # Advance to Completed
    databus.states["seq-1"] = "Completed"
    await asyncio.sleep(0.15)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    states = [new for _, new in transitions]
    assert "Confirmed" in states
    assert "Completed" in states


@pytest.mark.asyncio
async def test_poll_loop_cancelled_path(
    binder: RunBinder,
    databus: FakeDatabus,
    fleet: FleetState,
) -> None:
    fleet.bind_run = lambda *a, **kw: None  # type: ignore
    fleet.unbind_run = lambda *a: None  # type: ignore
    fleet.set_transmitting = lambda *a: None  # type: ignore
    fleet.set_moving = lambda *a: None  # type: ignore
    fleet.set_lifecycle_state = lambda *a: None  # type: ignore

    databus.states["cancel-1"] = "Cancelled"
    binder.track("cancel-1", "unit-01", "trip-001", "hacia_artes")

    transitions: list = []
    binder.on_state_change = lambda rid, old, new: transitions.append((old, new))

    task = asyncio.create_task(binder.poll_loop())
    await asyncio.sleep(0.2)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    states = [new for _, new in transitions]
    assert "Cancelled" in states


@pytest.mark.asyncio
async def test_poll_loop_calls_get_run_state_each_tick(
    binder: RunBinder,
    databus: FakeDatabus,
    fleet: FleetState,
) -> None:
    fleet.bind_run = lambda *a, **kw: None  # type: ignore
    fleet.unbind_run = lambda *a: None  # type: ignore
    fleet.set_transmitting = lambda *a: None  # type: ignore
    fleet.set_moving = lambda *a: None  # type: ignore
    fleet.set_lifecycle_state = lambda *a: None  # type: ignore

    seen: list[str] = []
    databus.states["tick-1"] = "Confirmed"

    async def _spy(run_id: str) -> str | None:
        seen.append(run_id)
        return databus.states.get(run_id)

    databus.get_run_state = _spy  # type: ignore[method-assign]
    binder.track("tick-1", "unit-01", "trip-001", "hacia_artes")

    task = asyncio.create_task(binder.poll_loop())
    await asyncio.sleep(0.18)  # ~3 ticks at 0.05s
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert seen, "poll_loop never polled databus.get_run_state"
    assert all(rid == "tick-1" for rid in seen)


@pytest.mark.asyncio
async def test_poll_loop_run_not_found_force_unbinds(
    binder: RunBinder,
    databus: FakeDatabus,
    fleet: FleetState,
) -> None:
    """A 404 (None) after a state was seen → force-unbind, drop the binding."""
    fleet.bind_run = lambda *a, **kw: None  # type: ignore
    fleet.set_transmitting = lambda *a: None  # type: ignore
    fleet.set_moving = lambda *a: None  # type: ignore
    fleet.set_lifecycle_state = lambda *a: None  # type: ignore
    unbound: list[str] = []
    fleet.unbind_run = lambda vid: unbound.append(vid)  # type: ignore

    databus.states["gone-1"] = "Confirmed"
    binder.track("gone-1", "unit-01", "trip-001", "hacia_artes")

    task = asyncio.create_task(binder.poll_loop())
    await asyncio.sleep(0.15)  # observe Confirmed
    databus.states["gone-1"] = None  # databus now 404s the run
    await asyncio.sleep(0.15)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert "unit-01" in unbound
    assert binder.bindings() == []
