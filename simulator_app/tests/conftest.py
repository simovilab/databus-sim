"""Shared pytest fixtures for simulator_app tests."""

from __future__ import annotations

import pytest
from pathlib import Path

import yaml


@pytest.fixture
def tmp_schedule(tmp_path: Path) -> Path:
    """Write an empty schedule.yaml and return its path."""
    p = tmp_path / "schedule.yaml"
    p.write_text(
        yaml.safe_dump(
            {
                "defaults": {
                    "pre_run_idle_s": 0,
                    "post_run_idle_s": 30,
                    "auto_confirm_delay_s": 0,
                },
                "runs": [],
            }
        )
    )
    return p
