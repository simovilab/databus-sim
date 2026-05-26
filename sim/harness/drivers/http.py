"""HTTP driver — thin httpx wrapper for backend REST calls."""

from __future__ import annotations

import logging
from typing import Any

import httpx
from jsonpath_ng import parse as jp_parse

from ..config import Config

logger = logging.getLogger(__name__)


class HttpDriver:
    def __init__(self, config: Config) -> None:
        self._base = config.backend
        self._client = httpx.Client(base_url=self._base, timeout=30.0)

    def close(self) -> None:
        self._client.close()

    def request(
        self,
        method: str,
        url: str,
        *,
        json: dict[str, Any] | None = None,
        expect_status: list[int] | None = None,
        capture: dict[str, str] | None = None,
        context: dict[str, Any] | None = None,
    ) -> tuple[httpx.Response, dict[str, Any]]:
        """Execute an HTTP request, optionally capturing jsonpath values from response."""
        ctx = context or {}
        resolved_url = _interpolate(url, ctx)
        resolved_json = _interpolate_json(json, ctx) if json else json
        resp = self._client.request(method.upper(), resolved_url, json=resolved_json)
        logger.debug("%s %s → %d", method.upper(), resolved_url, resp.status_code)

        if expect_status and resp.status_code not in expect_status:
            logger.warning(
                "Unexpected status %d (expected %s) for %s %s",
                resp.status_code, expect_status, method.upper(), resolved_url,
            )

        captured: dict[str, Any] = {}
        if capture:
            try:
                body = resp.json()
            except Exception:
                body = {}
            for name, path in capture.items():
                expr = jp_parse(path)
                matches = expr.find(body)
                captured[name] = matches[0].value if matches else None

        return resp, captured


def _interpolate(template: str, ctx: dict[str, Any]) -> str:
    for k, v in ctx.items():
        template = template.replace(f"{{{k}}}", str(v) if v is not None else "")
    return template


def _interpolate_json(obj: Any, ctx: dict[str, Any]) -> Any:
    if isinstance(obj, str):
        return _interpolate(obj, ctx)
    if isinstance(obj, dict):
        return {k: _interpolate_json(v, ctx) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_interpolate_json(item, ctx) for item in obj]
    return obj
