"""AMQP driver — aio-pika publish for direct event injection."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import aio_pika

from ..config import Config

logger = logging.getLogger(__name__)


class AmqpDriver:
    def __init__(self, config: Config) -> None:
        self._url = config.amqp_url
        self._connection: aio_pika.abc.AbstractConnection | None = None
        self._channel: aio_pika.abc.AbstractChannel | None = None

    async def connect(self) -> None:
        self._connection = await aio_pika.connect_robust(self._url)
        assert self._connection is not None
        self._channel = await self._connection.channel()
        logger.debug("AMQP connected to %s", self._url)

    async def close(self) -> None:
        if self._connection:
            await self._connection.close()

    async def publish(
        self,
        exchange_name: str,
        routing_key: str,
        payload: dict[str, Any] | str,
    ) -> None:
        if self._channel is None:
            await self.connect()
        assert self._channel is not None
        exchange = await self._channel.declare_exchange(
            exchange_name, aio_pika.ExchangeType.TOPIC, durable=True
        )
        body = json.dumps(payload).encode() if isinstance(payload, dict) else payload.encode()
        await exchange.publish(
            aio_pika.Message(body=body, content_type="application/json"),
            routing_key=routing_key,
        )
        logger.debug("AMQP published to %s / %s", exchange_name, routing_key)

    def publish_sync(
        self,
        exchange_name: str,
        routing_key: str,
        payload: dict[str, Any] | str,
    ) -> None:
        asyncio.run(self.publish(exchange_name, routing_key, payload))
