"""Redis reader for ``run:{run_id}`` hash.

Interface declared at P0 (consumed by :mod:`sim.run_binder`,
:mod:`sim.http_control`). Agent A relies on the interface in A1; Agent B owns
the final implementation in B1. See ``CONTRACTS.md`` §6.
"""

from __future__ import annotations

import os
from typing import Any

import redis.asyncio as aioredis


DEFAULT_REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")


class RedisClient:
    """Thin async wrapper exposing only what the simulator needs."""

    def __init__(
        self,
        url: str = DEFAULT_REDIS_URL,
        client: aioredis.Redis | None = None,
    ) -> None:
        self.url = url
        self._client = client

    async def __aenter__(self) -> "RedisClient":
        if self._client is None:
            self._client = aioredis.from_url(self.url, decode_responses=True)
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        await self.close()

    # --- public API --------------------------------------------------------

    async def get_run_state(self, run_id: str) -> str | None:
        """Return ``run:{run_id}`` → ``run_lifecycle_state`` field, or None."""
        assert self._client is not None, "RedisClient must be used as a context manager"
        return await self._client.hget(f"run:{run_id}", "run_lifecycle_state")  # type: ignore[return-value]

    async def get_run_hash(self, run_id: str) -> dict[str, str]:
        """Return the full hash at ``run:{run_id}`` (empty dict if missing)."""
        assert self._client is not None, "RedisClient must be used as a context manager"
        result = await self._client.hgetall(f"run:{run_id}")
        return result or {}

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
