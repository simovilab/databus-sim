"""AppConfig for the simulator_app Django application.

The runtime startup is triggered from ``ready()`` when running under daphne
(ASGI), using a lifespan event handler registered on the ASGI application.
Background tasks (tick_loop, binder, scheduler) are started when daphne
fires the ASGI lifespan startup event.

Note: ``ready()`` itself is synchronous and runs before the event loop starts,
so we cannot call ``asyncio.run()`` here. The actual startup happens in
``sim_project.asgi._LifespanHandler`` which daphne fires as an ASGI lifespan
message. For daphne, the lifespan handler fires automatically via channels'
ProtocolTypeRouter.
"""

from django.apps import AppConfig


class SimulatorAppConfig(AppConfig):
    name = "simulator_app"
    verbose_name = "SIMOVI Simulator"
    default_auto_field = "django.db.models.BigAutoField"
