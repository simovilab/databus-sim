import time
import random
import paho.mqtt.client as mqtt
from typing import Optional

class MQTTClient:
    def __init__(
        self,
        broker: str,
        port: int = 1883,
        max_retries: int = 5,
        base_backoff: float = 1.0,
        publish_interval: float = 1.0,
        enable_vehicle_positions=True,
        enable_trip_updates=True,
        enable_alerts=False,
    ):
        self.broker = broker
        self.port = port
        self.max_retries = max_retries
        self.base_backoff = base_backoff
        self.publish_interval = publish_interval
        self.enable_vehicle_positions = enable_vehicle_positions
        self.enable_trip_updates = enable_trip_updates
        self.enable_alerts = enable_alerts
        self.last_publish_time = 0

        self.client = mqtt.Client()
        self._connect_with_backoff()

    def _connect_with_backoff(self):
        retries = 0
        while retries <= self.max_retries:
            try:
                print(f"Connecting to MQTT broker {self.broker}:{self.port}...")
                self.client.connect(self.broker, self.port, 60)
                print("✅ MQTT Connected successfully")
                return
            except Exception as e:
                retries += 1
                wait = self.base_backoff * (2 ** retries) + random.random()
                print(f"⚠ MQTT Connect failed: {e} — retry {retries}/{self.max_retries} in {wait:.2f}s")
                time.sleep(wait)

        raise RuntimeError("❌ Failed to connect to MQTT broker after retries")

    def publish(self, topic: str, payload: bytes):
        """Publishes data with throttling controls"""
        now = time.time()
        if now - self.last_publish_time < self.publish_interval:
            return  # ✅ throttle
        self.last_publish_time = now

        try:
            self.client.publish(topic, payload)
        except Exception as e:
            print("⚠ MQTT publish failed:", e)
