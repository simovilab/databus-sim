"""Root URL configuration for the SIMOVI simulator service.

Path layout (per PLAN §7):
  /sim/          — DRF simulator API (fleet, schedule, control, run, healthz)
  /databus/<path> — httpx reverse proxy to databus REST (avoids CORS)
  /              — index TemplateView (placeholder until frontend port)
  /static/       — staticfiles

WebSocket routes live in asgi.py (not here), mounted at /ws/.
"""

from django.conf import settings
from django.conf.urls.static import static
from django.urls import include, path, re_path
from django.views.generic import TemplateView

from simulator_app.api.proxy import databus_proxy_view

urlpatterns = [
    # Simulator control API
    path("sim/", include("simulator_app.api.urls")),

    # /databus/<path> — passthrough proxy to databus orchestrator REST
    re_path(r"^databus/(?P<path>.*)$", databus_proxy_view, name="databus-proxy"),

    # Index
    path("", TemplateView.as_view(template_name="index.html"), name="index"),
]

if settings.DEBUG:
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
