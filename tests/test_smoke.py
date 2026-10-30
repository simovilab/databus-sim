"""Smoke tests for the Phase 1 Django scaffold.

These tests verify the boot-level contract:
  - The healthz endpoint exists and returns 200 with {"ok": true}.
  - The ASGI application imports cleanly (lifespan wiring resolves).

Phase 2+ adds integration and unit tests in simulator_app/tests/.
"""

import pytest
from django.test import TestCase


class HealthzSmokeTest(TestCase):
    """GET /sim/healthz must return 200 JSON {"ok": true}."""

    def test_healthz_returns_200(self) -> None:
        response = self.client.get("/sim/healthz")
        self.assertEqual(response.status_code, 200)

    def test_healthz_body_ok_true(self) -> None:
        response = self.client.get("/sim/healthz")
        data = response.json()
        self.assertTrue(data.get("ok") is True)

    def test_asgi_imports_cleanly(self) -> None:
        """Importing sim_project.asgi must not raise (lifespan wiring resolves)."""
        import importlib

        import sim_project.asgi as asgi_mod
        importlib.reload(asgi_mod)
        self.assertIsNotNone(asgi_mod.application)
