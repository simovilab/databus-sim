"""Unit tests for sim.databus_client using respx to mock HTTP calls."""

from __future__ import annotations

import pytest
import respx
import httpx

from sim.databus_client import (
    CreateRunRequest,
    CreateRunResponse,
    DatabusClient,
    DatabusError,
    UpdateRunRequest,
    UpdateRunResponse,
)

BASE = "http://test.local"


@pytest.fixture
def create_req() -> CreateRunRequest:
    return CreateRunRequest(
        vehicle_id="unit-01",
        operator_id="op-001",
        route_id="bUCR_L1",
        trip_id="trip-001",
        direction_id=0,
        shape_id="hacia_artes",
    )


@pytest.fixture
def update_req() -> UpdateRunRequest:
    return UpdateRunRequest(
        run_id="abc-123",
        event="run_confirmed_by_operator",
        details={},
    )


# ---------------------------------------------------------------------------
# create_run — happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_create_run_success(create_req: CreateRunRequest) -> None:
    respx.post(f"{BASE}/api/create-run/").mock(
        return_value=httpx.Response(
            200,
            json={"status": "success", "run_id": "run-999", "run_lifecycle_state": "Initialized"},
        )
    )
    async with DatabusClient(base_url=BASE) as db:
        resp = await db.create_run(create_req)
    assert isinstance(resp, CreateRunResponse)
    assert resp.run_id == "run-999"
    assert resp.run_lifecycle_state == "Initialized"
    assert resp.status == "success"


# ---------------------------------------------------------------------------
# create_run — error shapes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_create_run_400(create_req: CreateRunRequest) -> None:
    respx.post(f"{BASE}/api/create-run/").mock(
        return_value=httpx.Response(
            400,
            json={"status": "error", "step": "validate", "errors": {"vehicle_id": ["not found"]}},
        )
    )
    async with DatabusClient(base_url=BASE) as db:
        with pytest.raises(DatabusError) as exc_info:
            await db.create_run(create_req)
    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
@respx.mock
async def test_create_run_422(create_req: CreateRunRequest) -> None:
    respx.post(f"{BASE}/api/create-run/").mock(
        return_value=httpx.Response(
            422,
            json={"detail": [{"loc": ["body", "vehicle_id"], "msg": "field required"}]},
        )
    )
    async with DatabusClient(base_url=BASE) as db:
        with pytest.raises(DatabusError) as exc_info:
            await db.create_run(create_req)
    assert exc_info.value.status_code == 422


@pytest.mark.asyncio
@respx.mock
async def test_create_run_500(create_req: CreateRunRequest) -> None:
    respx.post(f"{BASE}/api/create-run/").mock(
        return_value=httpx.Response(500, text="Internal Server Error")
    )
    async with DatabusClient(base_url=BASE) as db:
        with pytest.raises(DatabusError) as exc_info:
            await db.create_run(create_req)
    assert exc_info.value.status_code == 500


# ---------------------------------------------------------------------------
# update_run — happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_update_run_success(update_req: UpdateRunRequest) -> None:
    respx.post(f"{BASE}/api/update-run/").mock(
        return_value=httpx.Response(
            200,
            json={"status": "success", "run_lifecycle_state": "Confirmed"},
        )
    )
    async with DatabusClient(base_url=BASE) as db:
        resp = await db.update_run(update_req)
    assert isinstance(resp, UpdateRunResponse)
    assert resp.run_lifecycle_state == "Confirmed"


# ---------------------------------------------------------------------------
# update_run — invalid event rejected client-side
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_run_rejects_invalid_event() -> None:
    async with DatabusClient(base_url=BASE) as db:
        with pytest.raises(DatabusError, match="Invalid event"):
            await db.update_run(
                UpdateRunRequest(run_id="x", event="CONFIRM_RUN", details={})
            )


@pytest.mark.asyncio
@respx.mock
async def test_update_run_400(update_req: UpdateRunRequest) -> None:
    respx.post(f"{BASE}/api/update-run/").mock(
        return_value=httpx.Response(400, json={"detail": "invalid transition"})
    )
    async with DatabusClient(base_url=BASE) as db:
        with pytest.raises(DatabusError) as exc_info:
            await db.update_run(update_req)
    assert exc_info.value.status_code == 400


# ---------------------------------------------------------------------------
# All valid operator events pass client-side validation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_all_valid_events_accepted() -> None:
    respx.post(f"{BASE}/api/update-run/").mock(
        return_value=httpx.Response(
            200,
            json={"status": "success", "run_lifecycle_state": "Cancelled"},
        )
    )
    valid_events = [
        "run_confirmed_by_operator",
        "cancel_run",
        "interrupt_run",
        "short_turn_run",
    ]
    for event in valid_events:
        async with DatabusClient(base_url=BASE) as db:
            resp = await db.update_run(UpdateRunRequest(run_id="r1", event=event))
        assert resp.status == "success"


# ---------------------------------------------------------------------------
# get_run_state / get_run_hash — GET /api/run/{run_id}/
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_get_run_state_200() -> None:
    respx.get(f"{BASE}/api/run/run-1/").mock(
        return_value=httpx.Response(
            200,
            json={"run_id": "run-1", "run_lifecycle_state": "Confirmed", "fields": {}},
        )
    )
    async with DatabusClient(base_url=BASE) as db:
        assert await db.get_run_state("run-1") == "Confirmed"


@pytest.mark.asyncio
@respx.mock
async def test_get_run_state_404_returns_none() -> None:
    respx.get(f"{BASE}/api/run/missing/").mock(
        return_value=httpx.Response(404, json={"detail": "not found"})
    )
    async with DatabusClient(base_url=BASE) as db:
        assert await db.get_run_state("missing") is None


@pytest.mark.asyncio
@respx.mock
async def test_get_run_state_500_returns_none() -> None:
    respx.get(f"{BASE}/api/run/boom/").mock(
        return_value=httpx.Response(500, text="Internal Server Error")
    )
    async with DatabusClient(base_url=BASE) as db:
        assert await db.get_run_state("boom") is None


@pytest.mark.asyncio
@respx.mock
async def test_get_run_hash_200_merges_state_and_fields() -> None:
    respx.get(f"{BASE}/api/run/run-2/").mock(
        return_value=httpx.Response(
            200,
            json={
                "run_id": "run-2",
                "run_lifecycle_state": "In Progress",
                "fields": {"vehicle_id": "unit-03", "trip_id": "trip-7"},
            },
        )
    )
    async with DatabusClient(base_url=BASE) as db:
        result = await db.get_run_hash("run-2")
    assert result == {
        "vehicle_id": "unit-03",
        "trip_id": "trip-7",
        "run_lifecycle_state": "In Progress",
    }


@pytest.mark.asyncio
@respx.mock
async def test_get_run_hash_404_returns_empty() -> None:
    respx.get(f"{BASE}/api/run/missing/").mock(
        return_value=httpx.Response(404, json={"detail": "not found"})
    )
    async with DatabusClient(base_url=BASE) as db:
        assert await db.get_run_hash("missing") == {}


# ---------------------------------------------------------------------------
# context manager behaviour
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_context_manager_closes_client() -> None:
    db = DatabusClient(base_url=BASE)
    async with db:
        assert db._client is not None
    assert db._client is None
