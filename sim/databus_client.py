"""Thin HTTP client for the databus orchestrator.

Owned by Agent B (B1). P0 fixes the request/response shapes (see
``CONTRACTS.md`` §5). B1 implements the bodies with :mod:`httpx`.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Literal

import httpx
from pydantic import BaseModel


DEFAULT_BASE_URL = os.getenv("DATABUS_BASE_URL", "http://localhost:8000")

log = logging.getLogger(__name__)

# Valid operator-driven events (lower_snake_case per CONTRACTS.md §5.2)
OPERATOR_EVENTS = frozenset(
    {
        "run_confirmed_by_operator",
        "cancel_run",
        "interrupt_run",
        "short_turn_run",
    }
)


# ---------------------------------------------------------------------------
# Request / response models (CONTRACTS.md §5)
# ---------------------------------------------------------------------------


class CreateRunRequest(BaseModel):
    vehicle_id: str
    operator_id: str
    route_id: str
    trip_id: str
    direction_id: int
    shape_id: str
    schedule_relationship: str = "SCHEDULED"


class CreateRunResponse(BaseModel):
    status: Literal["success"]
    run_id: str
    run_lifecycle_state: str


class CreateRunError(BaseModel):
    status: Literal["error"]
    step: str
    errors: dict[str, Any]


class UpdateRunRequest(BaseModel):
    run_id: str
    event: str
    details: dict[str, Any] = {}


class UpdateRunResponse(BaseModel):
    status: Literal["success"]
    run_lifecycle_state: str


class DatabusError(RuntimeError):
    """Raised on non-2xx or schema-invalid responses from databus."""

    def __init__(self, message: str, status_code: int | None = None, body: Any = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.body = body


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class DatabusClient:
    """Async HTTP client for ``/api/create-run/`` and ``/api/update-run/``."""

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        timeout_s: float = 5.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s
        self._client = client

    async def __aenter__(self) -> "DatabusClient":
        if self._client is None:
            self._client = httpx.AsyncClient(base_url=self.base_url, timeout=self.timeout_s)
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # --- run lifecycle -----------------------------------------------------

    async def create_run(self, req: CreateRunRequest) -> CreateRunResponse:
        """POST /api/create-run/. See CONTRACTS.md §5.1."""
        assert self._client is not None, "DatabusClient must be used as a context manager"
        payload = req.model_dump()
        log.debug("create_run payload=%s", payload)
        resp = await self._client.post(f"{self.base_url}/api/create-run/", json=payload)
        if not resp.is_success:
            body: Any = None
            try:
                body = resp.json()
            except Exception:
                body = resp.text
            raise DatabusError(
                f"create_run failed: HTTP {resp.status_code}",
                status_code=resp.status_code,
                body=body,
            )
        return CreateRunResponse.model_validate(resp.json())

    async def update_run(self, req: UpdateRunRequest) -> UpdateRunResponse:
        """POST /api/update-run/. See CONTRACTS.md §5.2."""
        assert self._client is not None, "DatabusClient must be used as a context manager"
        if req.event not in OPERATOR_EVENTS:
            raise DatabusError(
                f"Invalid event '{req.event}'. Must be one of: {sorted(OPERATOR_EVENTS)}"
            )
        payload = req.model_dump()
        log.debug("update_run payload=%s", payload)
        resp = await self._client.post(f"{self.base_url}/api/update-run/", json=payload)
        if not resp.is_success:
            body: Any = None
            try:
                body = resp.json()
            except Exception:
                body = resp.text
            raise DatabusError(
                f"update_run failed: HTTP {resp.status_code}",
                status_code=resp.status_code,
                body=body,
            )
        return UpdateRunResponse.model_validate(resp.json())

    # --- run state (replaces the old Redis poller) -------------------------

    async def get_run_state(self, run_id: str) -> str | None:
        """GET /api/run/{run_id}/. Return ``run_lifecycle_state`` or None.

        Mirrors the old ``RedisClient.get_run_state`` semantics exactly:
        404 (run not found) → None. Any other error is logged and also
        returns None (resilience pattern shared with ``get_trip_terminal_stop``).
        """
        data = await self._get_run(run_id)
        if data is None:
            return None
        return data.get("run_lifecycle_state")

    async def get_run_hash(self, run_id: str) -> dict[str, str]:
        """GET /api/run/{run_id}/. Return the run's full field map, or {}.

        Equivalent to the old Redis ``run:{run_id}`` hash: the response's
        ``fields`` merged with ``run_lifecycle_state`` so the HTTP control
        ``/run/{run_id}`` endpoint keeps its response shape. 404/error → {}.
        """
        data = await self._get_run(run_id)
        if data is None:
            return {}
        fields: dict[str, str] = dict(data.get("fields") or {})
        state = data.get("run_lifecycle_state")
        if state is not None:
            fields["run_lifecycle_state"] = state
        return fields

    async def _get_run(self, run_id: str) -> dict[str, Any] | None:
        """Fetch GET /api/run/{run_id}/ JSON, or None on 404/error."""
        assert self._client is not None, "DatabusClient must be used as a context manager"
        try:
            resp = await self._client.get(f"{self.base_url}/api/run/{run_id}/")
        except httpx.HTTPError as exc:
            log.warning("get_run request failed run_id=%s: %s", run_id, exc)
            return None
        if resp.status_code == 404:
            return None
        if not resp.is_success:
            log.warning("get_run HTTP %s for run %s", resp.status_code, run_id)
            return None
        try:
            return resp.json()
        except Exception as exc:  # noqa: BLE001
            log.warning("get_run bad JSON run_id=%s: %s", run_id, exc)
            return None

    async def get_trip_terminal_stop(self, trip_id: str) -> str | None:
        """Return the terminal stop_id for ``trip_id``, or None if not found.

        Queries ``GET /api/stop-times/?trip=<id>`` and returns the stop_id with
        the highest stop_sequence.
        """
        assert self._client is not None, "DatabusClient must be used as a context manager"
        resp = await self._client.get(
            f"{self.base_url}/api/stop-times/", params={"trip": trip_id}
        )
        if not resp.is_success:
            log.warning("get_trip_terminal_stop HTTP %s for trip %s", resp.status_code, trip_id)
            return None
        data = resp.json()
        items: list[dict[str, Any]] = data.get("results", data) if isinstance(data, dict) else data
        if not items:
            return None
        terminal = max(items, key=lambda x: x.get("stop_sequence", 0))
        return terminal.get("stop_id")
