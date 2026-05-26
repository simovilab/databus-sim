"""AMQP sniffer — subscribe to all broker exchanges and collect messages."""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable

import aio_pika

from ..config import Config

logger = logging.getLogger(__name__)

HARNESS_EXCHANGES = [
    "databus.commands",
    "databus.observations",
    "databus.assertions",
]


@dataclass
class AmqpMessage:
    exchange: str
    routing_key: str
    body: dict | str
    received_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class AmqpSniffer:
    """Background thread that listens to all AMQP exchanges and buffers messages."""

    def __init__(self, config: Config) -> None:
        self._url = config.amqp_url
        self._messages: list[AmqpMessage] = []
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._stop_event = asyncio.Event()
        self._listeners: list[Callable[[AmqpMessage], None]] = []

    def start(self) -> None:
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._loop:
            self._loop.call_soon_threadsafe(self._stop_event.set)
        if self._thread:
            self._thread.join(timeout=5.0)

    def messages_since(self, ts: datetime) -> list[AmqpMessage]:
        with self._lock:
            return [m for m in self._messages if m.received_at >= ts]

    def wait_for_routing_key(
        self, routing_key: str, *, since: datetime, timeout_ms: int
    ) -> AmqpMessage | None:
        deadline = since.timestamp() + timeout_ms / 1000
        import time
        while time.time() < deadline:
            with self._lock:
                for m in self._messages:
                    if m.routing_key == routing_key and m.received_at >= since:
                        return m
            time.sleep(0.05)
        return None

    def add_listener(self, fn: Callable[[AmqpMessage], None]) -> None:
        self._listeners.append(fn)

    def _run(self) -> None:
        assert self._loop is not None
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._listen())
        except Exception as exc:
            logger.error("AmqpSniffer loop error: %s", exc)

    async def _listen(self) -> None:
        try:
            conn = await aio_pika.connect_robust(self._url)
        except Exception as exc:
            logger.warning("AmqpSniffer could not connect: %s", exc)
            return

        async with conn:
            channel = await conn.channel()
            queue = await channel.declare_queue("", exclusive=True)
            for exchange_name in HARNESS_EXCHANGES:
                try:
                    exchange = await channel.declare_exchange(
                        exchange_name, aio_pika.ExchangeType.TOPIC, durable=True
                    )
                    await queue.bind(exchange, routing_key="#")
                    logger.debug("AMQP sniffer bound to exchange '%s'", exchange_name)
                except Exception as exc:
                    logger.warning("Could not bind to exchange '%s': %s", exchange_name, exc)

            async with queue.iterator() as it:
                async for raw in it:
                    if self._stop_event.is_set():
                        break
                    async with raw.process():
                        self._handle(raw)

    def _handle(self, raw: aio_pika.abc.AbstractIncomingMessage) -> None:
        try:
            body: dict | str
            try:
                body = json.loads(raw.body)
            except Exception:
                body = raw.body.decode(errors="replace")
            msg = AmqpMessage(
                exchange=raw.exchange or "",
                routing_key=raw.routing_key or "",
                body=body,
            )
            with self._lock:
                self._messages.append(msg)
            for fn in self._listeners:
                try:
                    fn(msg)
                except Exception as exc:
                    logger.warning("AmqpSniffer listener error: %s", exc)
        except Exception as exc:
            logger.error("AmqpSniffer message handling error: %s", exc)
