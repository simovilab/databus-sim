"""Async HTTP client for the NavSat last-state endpoint.

Adapted from ``~/Desktop/SIMOVI/navsat-bridge/src/navsat_bridge/navsat_client.py``
and ``models.py``: same "untrusted response" validation, but async (httpx.AsyncClient)
because the simulator runs a single shared asyncio event loop (see runtime.py) —
a blocking sync client would stall every other background task.

Only the fields the map overlay needs are kept: plate, position, and the raw
``estado`` string ("movimiento" / "detenido") straight from the vendor API —
displayed verbatim, not reinterpreted.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)


class NavSatClientError(Exception):
    """Raised when the NavSat API is unreachable or returns garbage."""


@dataclass(frozen=True)
class NavSatRecord:
    plate_number: str
    latitude: float
    longitude: float
    estado: str

    @classmethod
    def from_api(cls, raw: dict) -> NavSatRecord:
        return cls(
            plate_number=str(raw["plateNumber"]),
            latitude=float(raw["latitude"]),
            longitude=float(raw["longitude"]),
            estado=str(raw.get("estado") or ""),
        )


class NavSatClient:
    """Async httpx client for the NavSat last-state endpoint.

    ``url`` embeds an API token — treat it as a secret (env var only, never
    hardcoded, never logged).
    """

    def __init__(self, url: str, timeout_s: float = 15.0) -> None:
        self._url = url
        self._http = httpx.AsyncClient(timeout=timeout_s)

    async def fetch(self) -> list[NavSatRecord]:
        try:
            response = await self._http.get(self._url)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise NavSatClientError(f"NavSat API error: {exc}") from exc

        try:
            payload = response.json()
        except ValueError as exc:
            body_preview = response.text[:300]
            raise NavSatClientError(
                f"NavSat returned non-JSON (status={response.status_code}, "
                f"content-type={response.headers.get('content-type', '?')}): "
                f"body={body_preview!r}"
            ) from exc

        if not isinstance(payload, list):
            raise NavSatClientError(f"NavSat returned {type(payload).__name__}, expected list")

        records: list[NavSatRecord] = []
        for i, raw in enumerate(payload):
            if not isinstance(raw, dict):
                logger.warning("NavSat record %d is not a dict — skipped", i)
                continue
            try:
                records.append(NavSatRecord.from_api(raw))
            except (KeyError, TypeError, ValueError) as exc:
                logger.warning("NavSat record %d malformed (%s) — skipped", i, exc)
        return records

    async def close(self) -> None:
        await self._http.aclose()

    async def __aenter__(self) -> NavSatClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()
