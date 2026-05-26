"""Unit tests for sim.redis_client using fakeredis."""

from __future__ import annotations

import pytest
import fakeredis.aioredis as fake_aio

from sim.redis_client import RedisClient


@pytest.fixture
async def redis_client() -> RedisClient:
    server = fake_aio.FakeRedis(decode_responses=True)
    client = RedisClient(client=server)
    async with client:
        yield client


@pytest.mark.asyncio
async def test_get_run_state_returns_value(redis_client: RedisClient) -> None:
    await redis_client._client.hset("run:abc-123", "run_lifecycle_state", "Initialized")
    state = await redis_client.get_run_state("abc-123")
    assert state == "Initialized"


@pytest.mark.asyncio
async def test_get_run_state_returns_none_when_missing(redis_client: RedisClient) -> None:
    state = await redis_client.get_run_state("nonexistent-run")
    assert state is None


@pytest.mark.asyncio
async def test_get_run_state_returns_none_when_field_missing(redis_client: RedisClient) -> None:
    await redis_client._client.hset("run:abc-456", "other_field", "value")
    state = await redis_client.get_run_state("abc-456")
    assert state is None


@pytest.mark.asyncio
async def test_get_run_hash_returns_full_hash(redis_client: RedisClient) -> None:
    await redis_client._client.hset(
        "run:full-001",
        mapping={
            "run_lifecycle_state": "InProgress",
            "vehicle_id": "unit-01",
            "route_id": "bUCR_L1",
        },
    )
    result = await redis_client.get_run_hash("full-001")
    assert result == {
        "run_lifecycle_state": "InProgress",
        "vehicle_id": "unit-01",
        "route_id": "bUCR_L1",
    }


@pytest.mark.asyncio
async def test_get_run_hash_returns_empty_when_missing(redis_client: RedisClient) -> None:
    result = await redis_client.get_run_hash("does-not-exist")
    assert result == {}


@pytest.mark.asyncio
async def test_context_manager_closes_client() -> None:
    server = fake_aio.FakeRedis(decode_responses=True)
    client = RedisClient(client=server)
    async with client:
        assert client._client is not None
    assert client._client is None
