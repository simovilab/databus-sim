"""Django settings for the SIMOVI simulator service.

All runtime knobs are read from environment variables with the same defaults
the original sim/ code used. See PLAN §9 for the full config contract.

Single-process invariant: this service MUST run under exactly ONE daphne worker.
Scaling to >1 worker would split-brain the in-memory FleetState and deafen the
InMemoryChannelLayer. See PLAN §2 and docker-compose.yml.

Auth deliberate omission: AllowAny on all endpoints (see PLAN §2, §9).
OPERATIONAL RULE: do not expose WEB_PORT to an untrusted network.
"""

from __future__ import annotations

import os
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------------------
# Core
# ---------------------------------------------------------------------------

SECRET_KEY = os.environ.get(
    "DJANGO_SECRET_KEY",
    "dev-insecure-change-me-before-any-real-deployment",
)

DEBUG = os.environ.get("DEBUG", "true").lower() in ("1", "true", "yes")

ALLOWED_HOSTS: list[str] = os.environ.get("ALLOWED_HOSTS", "*").split(",")

# ---------------------------------------------------------------------------
# Application definition
# ---------------------------------------------------------------------------

INSTALLED_APPS = [
    # Daphne must be first so it can handle the ASGI lifespan protocol
    "daphne",
    "channels",
    "rest_framework",
    "django.contrib.staticfiles",
    "simulator_app",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.common.CommonMiddleware",
]

ROOT_URLCONF = "sim_project.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
            ],
        },
    },
]

ASGI_APPLICATION = "sim_project.asgi.application"

# ---------------------------------------------------------------------------
# Database — sqlite throwaway so manage.py check / migrate work without a
# DB server. The simulator itself is fully DB-free at runtime.
# ---------------------------------------------------------------------------

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}

# ---------------------------------------------------------------------------
# Django Channels — InMemoryChannelLayer (single-process; never Redis here)
# ---------------------------------------------------------------------------

CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels.layers.InMemoryChannelLayer",
    }
}

# ---------------------------------------------------------------------------
# Django REST Framework — AllowAny everywhere (internal dev tool, no auth)
# ---------------------------------------------------------------------------

REST_FRAMEWORK = {
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.AllowAny",
    ],
    # No authentication; set UNAUTHENTICATED_USER to None so DRF does not try to
    # import django.contrib.auth.models.AnonymousUser (which pulls in contenttypes,
    # which is absent from our minimal INSTALLED_APPS). The simulator has no auth.
    "DEFAULT_AUTHENTICATION_CLASSES": [],
    "UNAUTHENTICATED_USER": None,
    "DEFAULT_RENDERER_CLASSES": [
        "rest_framework.renderers.JSONRenderer",
    ],
}

# ---------------------------------------------------------------------------
# Static files
# ---------------------------------------------------------------------------

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

# ---------------------------------------------------------------------------
# Simulator runtime configuration
# Read from env; defaults match what sim/simulator.py, run_binder.py,
# scheduler.py, and databus_client.py originally used.
# ---------------------------------------------------------------------------

# MQTT (paho → databus telemetry-broker)
MQTT_HOST: str = os.environ.get("MQTT_HOST", "localhost")
MQTT_PORT: int = int(os.environ.get("MQTT_PORT", "1883"))
MQTT_TOPIC_ROOT: str = os.environ.get("MQTT_TOPIC_ROOT", "transit/vehicle")

# Databus HTTP REST
DATABUS_BASE_URL: str = os.environ.get("DATABUS_BASE_URL", "http://localhost:8000")

# Schedule file (host-editable, bind-mounted in Docker)
SCHEDULE_PATH: str = os.environ.get("SCHEDULE_PATH", str(BASE_DIR / "sim" / "schedule.yaml"))

# Tick / poll intervals (seconds)
SIM_TICK_INTERVAL: float = float(os.environ.get("SIM_TICK_INTERVAL", "2.0"))
POST_RUN_IDLE_S: int = int(os.environ.get("POST_RUN_IDLE_S", "30"))
RUN_POLL_INTERVAL_S: float = float(os.environ.get("RUN_POLL_INTERVAL_S", "2.0"))
SCHEDULER_TICK_S: float = float(os.environ.get("SCHEDULER_TICK_S", "1.0"))

# Web port (used by docker-compose CMD; also accepted by settings for reference)
WEB_PORT: int = int(os.environ.get("WEB_PORT", "8080"))

# ---------------------------------------------------------------------------
# Logging — structured, sensible defaults for a dev tool
# ---------------------------------------------------------------------------

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "default": {
            "format": "[%(asctime)s] %(levelname)s %(name)s: %(message)s",
            "datefmt": "%H:%M:%S",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "default",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": os.environ.get("LOG_LEVEL", "INFO"),
    },
}

# ---------------------------------------------------------------------------
# Misc Django settings (no sessions, no i18n needed for a sim)
# ---------------------------------------------------------------------------

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

USE_TZ = True
