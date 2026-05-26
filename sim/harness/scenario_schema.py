"""Pydantic models for scenario YAML files."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ExpectedBranch(BaseModel):
    terminal_state: str
    via_states: list[str] = Field(default_factory=list)
    via_events: list[str] = Field(default_factory=list)


class InputStep(BaseModel):
    model_config = {"populate_by_name": True}

    driver: str  # http | mqtt | amqp | db | scheduler
    # http fields
    method: str | None = None
    url: str | None = None
    json_body: dict[str, Any] | None = Field(default=None, alias="json")
    body: str | None = None
    expect_status: list[int] | None = None
    capture: dict[str, str] | None = None  # name → jsonpath
    # mqtt fields
    topic: str | None = None
    payload: dict[str, Any] | str | None = None
    # amqp fields
    exchange: str | None = None
    routing_key: str | None = None
    # scheduler fields
    trigger: str | None = None
    # db fields
    sql: str | None = None


class ObservationExpect(BaseModel):
    action: str
    within_ms: int = 5000


class ExpectBlock(BaseModel):
    context: dict[str, Any] | None = None
    observations: list[ObservationExpect] = Field(default_factory=list)


class CleanupStep(BaseModel):
    driver: str
    sql: str | None = None
    url: str | None = None
    method: str | None = None


class Scenario(BaseModel):
    id: str
    fsm: str
    description: str = ""
    expected_branch: ExpectedBranch
    inputs: list[InputStep] = Field(default_factory=list)
    expect: ExpectBlock | None = None
    cleanup: list[CleanupStep] = Field(default_factory=list)
    timeout_ms: int = 10000
    tags: list[str] = Field(default_factory=list)
    # scaffold stub flag — harness skips execution for stubs
    stub: bool = False
