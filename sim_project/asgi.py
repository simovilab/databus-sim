"""ASGI application for the SIMOVI simulator service.

Architecture (per PLAN §6.1):
  ProtocolTypeRouter dispatches:
    "http"      → Django ASGI app (DRF views + static files)
    "websocket" → AuthMiddlewareStack → URLRouter(simulator_app.realtime.routing)
    "lifespan"  → _LifespanHandler: calls runtime.start_runtime() / stop_runtime()

The lifespan handler is the key seam: Phase 2 porting agent fills start_runtime()
with real fleet/paho/tasks wiring; this scaffold runs stubs that log and return
safely even with no databus running.

Single-process invariant: do NOT add --workers to the daphne command. The
InMemoryChannelLayer and in-memory FleetState require exactly one process.
"""

from __future__ import annotations

import logging

from channels.auth import AuthMiddlewareStack
from channels.routing import ProtocolTypeRouter, URLRouter
from django.core.asgi import get_asgi_application

import simulator_app.realtime.routing as ws_routing

log = logging.getLogger(__name__)

# Build the plain Django ASGI app (handles HTTP).
_django_asgi_app = get_asgi_application()


class _LifespanHandler:
    """ASGI lifespan protocol handler.

    Receives ``lifespan.startup`` / ``lifespan.shutdown`` messages from daphne
    and delegates to simulator_app.runtime.start_runtime() / stop_runtime().

    Startup errors are caught and logged so that a misconfigured runtime does
    not prevent daphne from serving HTTP at all (graceful degradation for dev).
    Shutdown errors are similarly swallowed after logging.
    """

    async def __call__(
        self, scope: dict, receive: object, send: object  # type: ignore[type-arg]
    ) -> None:
        assert scope["type"] == "lifespan"

        # Import runtime lazily so settings are ready before the module-level
        # code runs (Django setup happens before ASGI is constructed).
        from simulator_app import runtime as _runtime_mod

        while True:
            message = await receive()  # type: ignore[misc]

            if message["type"] == "lifespan.startup":
                log.info("ASGI lifespan: startup received — starting runtime…")
                try:
                    await _runtime_mod.start_runtime()
                    log.info("ASGI lifespan: runtime started successfully.")
                except Exception:
                    log.exception(
                        "ASGI lifespan: runtime startup failed (service continues in degraded mode)."
                    )
                await send({"type": "lifespan.startup.complete"})  # type: ignore[misc]

            elif message["type"] == "lifespan.shutdown":
                log.info("ASGI lifespan: shutdown received — stopping runtime…")
                try:
                    await _runtime_mod.stop_runtime()
                    log.info("ASGI lifespan: runtime stopped.")
                except Exception:
                    log.exception("ASGI lifespan: runtime shutdown error (ignored).")
                await send({"type": "lifespan.shutdown.complete"})  # type: ignore[misc]
                return


# ---------------------------------------------------------------------------
# Main ASGI application — single entry point for daphne.
# ---------------------------------------------------------------------------

application = ProtocolTypeRouter(
    {
        "http": _django_asgi_app,
        "websocket": AuthMiddlewareStack(
            URLRouter(ws_routing.websocket_urlpatterns)
        ),
        "lifespan": _LifespanHandler(),
    }
)
