"""Root URL configuration for the SIMOVI simulator service.

Path layout (per PLAN §7):
  /sim/          — DRF simulator API (fleet, schedule, control, run, healthz)
  /databus/      — PLACEHOLDER: httpx reverse proxy to databus REST (Phase 7)
  /              — index TemplateView (placeholder until Phase 6 frontend port)
  /static/       — staticfiles

WebSocket routes live in asgi.py (not here), mounted at /ws/.
"""

from django.conf import settings
from django.conf.urls.static import static
from django.urls import include, path
from django.views.generic import TemplateView

urlpatterns = [
    # Simulator control API — Phase 2/5 fills in the actual views.
    path("sim/", include("simulator_app.api.urls")),

    # ---------------------------------------------------------------------------
    # PLACEHOLDER — /databus/ reverse proxy (Phase 7).
    # Phase 7 backend agent: add a DRF passthrough view here that forwards all
    # requests to settings.DATABUS_BASE_URL via httpx, preserving method/headers/body.
    # This avoids CORS when the browser hits databus from the same origin.
    # ---------------------------------------------------------------------------
    # path("databus/", include("simulator_app.api.databus_proxy_urls")),

    # Index — minimal placeholder; Phase 6 frontend agent replaces this with the
    # real TemplateView pointing at the ported web/ assets.
    path("", TemplateView.as_view(template_name="index.html"), name="index"),
]

if settings.DEBUG:
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
