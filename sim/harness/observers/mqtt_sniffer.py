"""MQTT sniffer — subscribe to telemetry topics for round-trip checks."""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import paho.mqtt.client as mqtt

from ..config import Config

logger = logging.getLogger(__name__)


@dataclass
class MqttMessage:
    topic: str
    payload: dict | str
    received_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class MqttSniffer:
    def __init__(self, config: Config, topics: list[str] | None = None) -> None:
        self._host = config.mqtt_host
        self._port = config.mqtt_port
        self._topics = topics or ["transit/vehicle/#"]
        self._messages: list[MqttMessage] = []
        self._lock = threading.Lock()
        self._client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        self._client.on_connect = self._on_connect
        self._client.on_message = self._on_message

    def start(self) -> None:
        try:
            self._client.connect(self._host, self._port, keepalive=60)
            self._client.loop_start()
        except Exception as exc:
            logger.warning("MqttSniffer could not connect: %s", exc)

    def stop(self) -> None:
        self._client.loop_stop()
        self._client.disconnect()

    def messages_since(self, ts: datetime) -> list[MqttMessage]:
        with self._lock:
            return [m for m in self._messages if m.received_at >= ts]

    def _on_connect(self, client: mqtt.Client, userdata: Any, flags: Any, rc: Any, props: Any = None) -> None:
        for topic in self._topics:
            client.subscribe(topic)
            logger.debug("MqttSniffer subscribed to %s", topic)

    def _on_message(self, client: mqtt.Client, userdata: Any, msg: mqtt.MQTTMessage) -> None:
        try:
            body: dict | str
            try:
                body = json.loads(msg.payload)
            except Exception:
                body = msg.payload.decode(errors="replace")
            with self._lock:
                self._messages.append(MqttMessage(topic=msg.topic, payload=body))
        except Exception as exc:
            logger.warning("MqttSniffer message error: %s", exc)
