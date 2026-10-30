"""Django settings for the SIMOVI simulator service.

All runtime knobs are read from environment variables with the same defaults
the original sim/ code used. See PLAN §9 for the full config contract.

ASGI server is uvicorn (not daphne): the simulator's background tasks start from
the ASGI lifespan protocol, which daphne 4.x does not emit. Run locally with
`uvicorn sim_project.asgi:application` (NOT `manage.py runserver`).

Single-process invariant: this service MUST run under exactly ONE uvicorn worker.
Scaling to >1 worker would split-brain the in-memory FleetState and deafen the
InMemoryChannelLayer. See PLAN §2 and docker-compose.yml.

Auth deliberate omission: AllowAny on all endpoints (see PLAN §2, §9).
OPERATIONAL RULE: do not expose WEB_PORT to an untrusted network.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent.parent

# Load a repo-root .env for local (non-Docker) runs so every os.environ.get()
# below picks up its values. override=False means real environment variables
# always win, so this is a no-op under Docker Compose (which injects env vars
# directly) and when no .env file exists. Copy .env.example → .env to use it.
load_dotenv(BASE_DIR / ".env", override=False)

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
    "channels",
    "rest_framework",
    "django.contrib.staticfiles",
    "simulator_app",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    # WhiteNoise serves static files under uvicorn/ASGI without collectstatic
    # or a separate nginx step. Must come immediately after SecurityMiddleware.
    "whitenoise.middleware.WhiteNoiseMiddleware",
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

# simulator_app/static/ is the app-level static directory; APP_DIRS=True makes
# Django's staticfiles finders pick it up automatically. STATICFILES_DIRS is not
# needed because the directory lives inside the installed app (APP_DIRS).
# WhiteNoise serves from STATIC_ROOT (after collectstatic) or from the app's
# static directories directly in development.
WHITENOISE_USE_FINDERS = True  # serve from app static dirs without collectstatic first

# ---------------------------------------------------------------------------
# Simulator runtime configuration
# Read from env; defaults match what sim/simulator.py, run_binder.py,
# scheduler.py, and databus_client.py originally used.
# ---------------------------------------------------------------------------

# MQTT (paho → databus telemetry-broker).
# The single source of truth in .env is DATABUS_MQTT_HOST/PORT (what compose
# uses); fall back to those so the SAME .env drives both Docker and local runs.
# An explicit MQTT_HOST/MQTT_PORT still wins if set.
MQTT_HOST: str = os.environ.get(
    "MQTT_HOST", os.environ.get("DATABUS_MQTT_HOST", "localhost")
)
MQTT_PORT: int = int(
    os.environ.get("MQTT_PORT", os.environ.get("DATABUS_MQTT_PORT", "1883"))
)
MQTT_TOPIC_ROOT: str = os.environ.get("MQTT_TOPIC_ROOT", "transit/vehicle")

# Databus HTTP REST. Prefer an explicit DATABUS_BASE_URL; otherwise build it
# from DATABUS_HOST/DATABUS_HTTP_PORT (the .env vars compose also uses).
DATABUS_BASE_URL: str = os.environ.get("DATABUS_BASE_URL") or "http://{}:{}".format(
    os.environ.get("DATABUS_HOST", "localhost"),
    os.environ.get("DATABUS_HTTP_PORT", "8000"),
)

# Data files (shapes, schedule, mappings) — defaults resolve to simulator_app/data/
SHAPES_PATH: str = os.environ.get(
    "SHAPES_PATH", str(BASE_DIR / "simulator_app" / "data" / "shapes.json")
)

# Schedule file (host-editable, bind-mounted in Docker)
SCHEDULE_PATH: str = os.environ.get(
    "SCHEDULE_PATH", str(BASE_DIR / "simulator_app" / "data" / "schedule.yaml")
)

# Tick / poll intervals (seconds)
SIM_TICK_INTERVAL: float = float(os.environ.get("SIM_TICK_INTERVAL", "2.0"))
POST_RUN_IDLE_S: int = int(os.environ.get("POST_RUN_IDLE_S", "30"))
RUN_POLL_INTERVAL_S: float = float(os.environ.get("RUN_POLL_INTERVAL_S", "2.0"))
SCHEDULER_TICK_S: float = float(os.environ.get("SCHEDULER_TICK_S", "1.0"))

# Web port (used by docker-compose CMD; also accepted by settings for reference)
WEB_PORT: int = int(os.environ.get("WEB_PORT", "8080"))

# Former CLI flags — now read from env at startup
SIM_ONLY_VEHICLES: set[str] = set(
    v.strip() for v in os.environ.get("SIM_ONLY_VEHICLES", "").split(",") if v.strip()
)
SIM_STOP_VEHICLES: set[str] = set(
    v.strip() for v in os.environ.get("SIM_STOP_VEHICLES", "").split(",") if v.strip()
)
SIM_RANDOM_DROP_RATE: int = int(os.environ.get("SIM_RANDOM_DROP_RATE", "0"))
SIM_STOP_ALL_AFTER: int = int(os.environ.get("SIM_STOP_ALL_AFTER", "0"))

# ---------------------------------------------------------------------------
# NavSat overlay (optional) — real-world vehicle positions shown on the map
# alongside the simulated fleet. NAVSAT_URL embeds a vendor API token: treat
# it as a secret (env var only, never hardcoded, never logged). Leaving it
# unset disables the feature entirely — no vendor account needed to run the sim.
# ---------------------------------------------------------------------------
NAVSAT_URL: str = os.environ.get("NAVSAT_URL", "")
NAVSAT_POLL_INTERVAL_S: float = float(os.environ.get("NAVSAT_POLL_INTERVAL_S", "10"))

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
    # httpx/httpcore log each outgoing request URL at INFO — NAVSAT_URL embeds
    # a vendor API token in its path, so that would otherwise leak the secret
    # to logs on every poll. Silenced regardless of LOG_LEVEL.
    "loggers": {
        "httpx": {"level": "WARNING", "handlers": ["console"], "propagate": False},
        "httpcore": {"level": "WARNING", "handlers": ["console"], "propagate": False},
    },
}

# ---------------------------------------------------------------------------
# Misc Django settings (no sessions, no i18n needed for a sim)
# ---------------------------------------------------------------------------

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

USE_TZ = True
