"""Environment-driven endpoint configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    backend_url: str
    mqtt_host: str
    mqtt_port: int
    amqp_url: str
    pg_dsn: str
    redis_url: str
    lake_dir: str
    fsm_dir: str

    @property
    def backend(self) -> str:
        return self.backend_url.rstrip("/")


def load_config() -> Config:
    return Config(
        backend_url=os.environ.get("DATABUS_BACKEND_URL", "http://localhost:8000"),
        mqtt_host=os.environ.get("MQTT_HOST", "localhost"),
        mqtt_port=int(os.environ.get("MQTT_PORT", "1883")),
        amqp_url=os.environ.get("AMQP_URL", "amqp://guest:guest@localhost:5672/"),
        pg_dsn=os.environ.get(
            "DATABASE_URL",
            "postgresql://postgres@localhost:5432/databus",
        ),
        redis_url=os.environ.get("REDIS_URL", "redis://localhost:6379/0"),
        lake_dir=os.environ.get("LAKE_DIR", "/lake"),
        fsm_dir=os.environ.get(
            "FSM_DIR",
            str(
                __import__("pathlib").Path(__file__).parent.parent.parent.parent
                / "context"
                / "behavior"
                / "databus"
                / "system"
                / "json"
            ),
        ),
    )
