import os
from celery import Celery
# Get the settings from the settings.conf file
from configparser import ConfigParser

config = ConfigParser()
config.read("settings.conf")

# Get broker URL from environment or use default
broker_url = os.getenv("CELERY_BROKER_URL", "redis://localhost:6379/0")
result_backend = os.getenv("CELERY_RESULT_BACKEND", "redis://localhost:6379/0")

# Create Celery app
app = Celery("databus-sim")

# Configure Celery
app.conf.update(
    broker_url=broker_url,
    result_backend=result_backend,
    task_serializer=config.get("task", "task_serializer", fallback="json"),
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    beat_schedule={
        # Example scheduled task - runs every 30 seconds
        "simulation-heartbeat": {
            "task": "tasks.simulation_heartbeat",
            "schedule": 30.0,
        },
    },
)

# Auto-discover tasks
app.autodiscover_tasks(["tasks"])
