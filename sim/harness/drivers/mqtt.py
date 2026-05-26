"""MQTT driver — paho-mqtt publish for telemetry injection."""

from __future__ import annotations

import json
import logging
from typing import Any

import paho.mqtt.client as mqtt

from ..config import Config

logger = logging.getLogger(__name__)


class MqttDriver:
    def __init__(self, config: Config) -> None:
        self._host = config.mqtt_host
        self._port = config.mqtt_port
        self._client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        self._connected = False

    def connect(self) -> None:
        self._client.connect(self._host, self._port, keepalive=60)
        self._client.loop_start()
        self._connected = True
        logger.debug("MQTT connected to %s:%d", self._host, self._port)

    def disconnect(self) -> None:
        if self._connected:
            self._client.loop_stop()
            self._client.disconnect()
            self._connected = False

    def publish(self, topic: str, payload: Any, *, qos: int = 0) -> None:
        if not self._connected:
            self.connect()
        body = json.dumps(payload) if not isinstance(payload, (str, bytes)) else payload
        result = self._client.publish(topic, body, qos=qos)
        result.wait_for_publish(timeout=5.0)
        logger.debug("MQTT published to %s", topic)
