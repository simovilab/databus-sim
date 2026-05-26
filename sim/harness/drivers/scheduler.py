"""Scheduler driver — fire synthetic trigger events via AMQP."""

from __future__ import annotations

import logging

from ..config import Config
from ..mappings import Mappings
from .amqp import AmqpDriver

logger = logging.getLogger(__name__)


class SchedulerDriver:
    def __init__(self, config: Config, mappings: Mappings) -> None:
        self._amqp = AmqpDriver(config)
        self._mappings = mappings

    def fire(self, event_name: str) -> None:
        mapping = self._mappings.resolve_event(event_name)
        if mapping is None:
            raise ValueError(f"No mapping for event '{event_name}'")
        if mapping.transport != "amqp":
            raise ValueError(f"Event '{event_name}' is not an AMQP event")
        assert mapping.exchange and mapping.routing_key
        self._amqp.publish_sync(
            mapping.exchange,
            mapping.routing_key,
            {"event": event_name, "source": "harness"},
        )
        logger.info("Fired trigger event '%s'", event_name)
