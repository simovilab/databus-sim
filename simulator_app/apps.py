"""AppConfig for the simulator_app Django application."""

from django.apps import AppConfig


class SimulatorAppConfig(AppConfig):
    name = "simulator_app"
    verbose_name = "SIMOVI Simulator"
    default_auto_field = "django.db.models.BigAutoField"
