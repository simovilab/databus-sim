"""Passthrough proxy view to the databus orchestrator REST API.

Forwards GET/POST/PUT requests from the browser to ``settings.DATABUS_BASE_URL``,
preserving path, query string, body, and status code. This avoids CORS issues
because the browser hits /databus/* on the same Django origin.

Mount in sim_project/urls.py at path("databus/", ...).

SECURITY NOTE (from PLAN §9): do not expose WEB_PORT to an untrusted network.
The proxy would otherwise be an unauthenticated gateway to databus's write API.
"""

from __future__ import annotations

import logging

import httpx
from django.conf import settings
from django.http import HttpRequest, HttpResponse

log = logging.getLogger(__name__)

_ALLOWED_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE"})

# Headers that must NOT be forwarded (hop-by-hop or host-specific)
_HOP_BY_HOP = frozenset(
    {
        "host",
        "connection",
        "keep-alive",
        "transfer-encoding",
        "te",
        "trailer",
        "upgrade",
        "proxy-authorization",
        "proxy-authenticate",
        "content-length",  # httpx sets this correctly
    }
)


async def databus_proxy_view(request: HttpRequest, path: str = "") -> HttpResponse:
    """Async Django view: forward request to databus, return its response."""
    if request.method not in _ALLOWED_METHODS:
        return HttpResponse(status=405)

    base = settings.DATABUS_BASE_URL.rstrip("/")
    target_url = f"{base}/{path}"
    if request.META.get("QUERY_STRING"):
        target_url += f"?{request.META['QUERY_STRING']}"

    # Forward safe headers
    forward_headers = {
        k: v
        for k, v in request.headers.items()
        if k.lower() not in _HOP_BY_HOP
    }

    body = request.body  # bytes

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.request(
                method=request.method,
                url=target_url,
                headers=forward_headers,
                content=body,
            )
    except httpx.ConnectError as exc:
        log.warning("databus_proxy: connection error to %s: %s", target_url, exc)
        return HttpResponse(b"databus unavailable", status=502, content_type="text/plain")
    except httpx.TimeoutException as exc:
        log.warning("databus_proxy: timeout to %s: %s", target_url, exc)
        return HttpResponse(b"databus timeout", status=504, content_type="text/plain")
    except Exception as exc:  # noqa: BLE001
        log.exception("databus_proxy: unexpected error to %s", target_url)
        return HttpResponse(str(exc).encode(), status=502, content_type="text/plain")

    django_resp = HttpResponse(
        content=resp.content,
        status=resp.status_code,
        content_type=resp.headers.get("content-type", "application/json"),
    )
    return django_resp
