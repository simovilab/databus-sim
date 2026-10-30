"""Channels WebSocket consumer for the SIMOVI fleet stream.

Connects to the ``"fleet"`` group and:
1. On connect: sends current fleet snapshot and schedule snapshot immediately
   (replicating MQTT retained-message semantics for late joiners).
2. Relays all subsequent ``group_send`` messages to the socket client.
3. Receive is a no-op (control is via DRF POST endpoints).

Message shapes sent to the browser:
    {"type": "fleet",    "payload": {...}}  — fleet snapshot
    {"type": "schedule", "payload": {...}}  — schedule snapshot
    {"type": "telemetry", "vehicle_id": "...", "leaf": "...", "payload": {...}}
    {"type": "navsat",   "payload": [...]}   — NavSat overlay snapshot (optional)
"""

from __future__ import annotations

import logging
from typing import Any

from channels.generic.websocket import AsyncJsonWebsocketConsumer

from simulator_app.runtime import get_runtime

log = logging.getLogger(__name__)


class FleetConsumer(AsyncJsonWebsocketConsumer):
    """WebSocket consumer: streams fleet/schedule/telemetry to the browser."""

    GROUP = "fleet"

    async def connect(self) -> None:
        await self.channel_layer.group_add(self.GROUP, self.channel_name)
        await self.accept()
        log.info("FleetConsumer: client connected channel=%s", self.channel_name)

        # Replicate MQTT retained-message semantics: send current state immediately.
        rt = get_runtime()
        if rt.fleet is not None:
            await self.send_json(
                {"type": "fleet", "payload": rt.fleet.snapshot()}
            )
        if rt.scheduler is not None:
            await self.send_json(
                {"type": "schedule", "payload": rt.scheduler.snapshot()}
            )
        if rt._navsat_snapshot:
            await self.send_json(
                {"type": "navsat", "payload": rt._navsat_snapshot}
            )

    async def disconnect(self, code: int) -> None:
        await self.channel_layer.group_discard(self.GROUP, self.channel_name)
        log.info("FleetConsumer: client disconnected code=%s", code)

    async def receive(self, text_data: str | None = None, bytes_data: bytes | None = None) -> None:
        # Control is via DRF POST endpoints; receive is a no-op.
        pass

    # --- group message handlers -------------------------------------------

    async def fleet_update(self, event: dict[str, Any]) -> None:
        """Dispatch a group_send message to the WebSocket client."""
        kind = event.get("kind", "fleet")

        if kind == "fleet":
            await self.send_json({"type": "fleet", "payload": event["payload"]})
        elif kind == "schedule":
            await self.send_json({"type": "schedule", "payload": event["payload"]})
        elif kind == "telemetry":
            await self.send_json(
                {
                    "type": "telemetry",
                    "vehicle_id": event["vehicle_id"],
                    "leaf": event["leaf"],
                    "payload": event["payload"],
                }
            )
        elif kind == "navsat":
            await self.send_json({"type": "navsat", "payload": event["payload"]})
        else:
            log.debug("FleetConsumer: unknown event kind %r", kind)
