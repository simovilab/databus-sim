"""Scenario executor: drive inputs, collect events, run assertions."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .assertions import AssertionResult, assert_amqp_event, assert_http_status
from .config import Config
from .drivers.http import HttpDriver
from .drivers.mqtt import MqttDriver
from .drivers.amqp import AmqpDriver
from .drivers.scheduler import SchedulerDriver
from .mappings import Mappings
from .observers.amqp_sniffer import AmqpSniffer
from .observers.db_poller import DbPoller
from .observers.lake_watcher import LakeWatcher
from .scenario_schema import InputStep, Scenario

logger = logging.getLogger(__name__)


@dataclass
class ScenarioResult:
    scenario_id: str
    fsm: str
    passed: bool
    skipped: bool = False
    skip_reason: str = ""
    assertions: list[AssertionResult] = field(default_factory=list)
    error: str | None = None
    duration_ms: float = 0.0
    captured: dict[str, Any] = field(default_factory=dict)


class ScenarioRunner:
    def __init__(
        self,
        config: Config,
        mappings: Mappings,
        sniffer: AmqpSniffer,
        db: DbPoller,
        lake: LakeWatcher,
    ) -> None:
        self._config = config
        self._mappings = mappings
        self._sniffer = sniffer
        self._db = db
        self._lake = lake
        self._http = HttpDriver(config)
        self._mqtt = MqttDriver(config)
        self._amqp = AmqpDriver(config)
        self._scheduler = SchedulerDriver(config, mappings)

    def run(self, scenario: Scenario) -> ScenarioResult:
        if scenario.stub:
            return ScenarioResult(
                scenario_id=scenario.id,
                fsm=scenario.fsm,
                passed=False,
                skipped=True,
                skip_reason="scaffold stub — fill in inputs to enable",
            )

        start = time.monotonic()
        since = datetime.now(timezone.utc)
        captured: dict[str, Any] = {}
        assertions: list[AssertionResult] = []

        try:
            # Execute input steps
            for step in scenario.inputs:
                step_ctx = self._build_context(captured, scenario)
                cap = self._execute_step(step, step_ctx)
                captured.update(cap)
                if step.expect_status and cap.get("_status") not in step.expect_status:
                    assertions.append(assert_http_status(
                        cap.get("_status", 0), step.expect_status, step.url or ""
                    ))

            # Evaluate expected observations
            if scenario.expect:
                for obs in scenario.expect.observations:
                    mapping = self._mappings.resolve_action(obs.action)
                    if mapping and mapping.transport == "amqp" and mapping.routing_key:
                        result = assert_amqp_event(
                            self._sniffer,
                            mapping.routing_key,
                            since=since,
                            timeout_ms=obs.within_ms,
                        )
                        assertions.append(result)
                    else:
                        # No observable mapping — skip but note it
                        assertions.append(AssertionResult(
                            passed=True,
                            message=f"Action '{obs.action}' has no observable transport (log_or_noop)",
                        ))

        except Exception as exc:
            logger.exception("Scenario '%s' raised an exception", scenario.id)
            duration = (time.monotonic() - start) * 1000
            return ScenarioResult(
                scenario_id=scenario.id,
                fsm=scenario.fsm,
                passed=False,
                error=str(exc),
                duration_ms=duration,
                captured=captured,
            )
        finally:
            self._run_cleanup(scenario, captured)

        duration = (time.monotonic() - start) * 1000
        passed = all(a.passed for a in assertions)
        return ScenarioResult(
            scenario_id=scenario.id,
            fsm=scenario.fsm,
            passed=passed,
            assertions=assertions,
            duration_ms=duration,
            captured=captured,
        )

    def _execute_step(self, step: InputStep, ctx: dict[str, Any]) -> dict[str, Any]:
        if step.driver == "http":
            assert step.method and step.url
            resp, captured = self._http.request(
                step.method,
                step.url,
                json=step.json_body,
                expect_status=step.expect_status,
                capture=step.capture,
                context=ctx,
            )
            captured["_status"] = resp.status_code
            return captured

        if step.driver == "mqtt":
            assert step.topic
            self._mqtt.publish(step.topic, step.payload or {})
            return {}

        if step.driver == "amqp":
            assert step.exchange and step.routing_key
            self._amqp.publish_sync(step.exchange, step.routing_key, step.payload or {})
            return {}

        if step.driver == "scheduler":
            assert step.trigger
            self._scheduler.fire(step.trigger)
            return {}

        if step.driver == "db":
            if step.sql:
                self._db.execute(step.sql, ())
            return {}

        raise ValueError(f"Unknown driver: {step.driver!r}")

    def _run_cleanup(self, scenario: Scenario, captured: dict[str, Any]) -> None:
        ctx = self._build_context(captured, scenario)
        for step in scenario.cleanup:
            try:
                if step.driver == "db" and step.sql:
                    resolved = step.sql
                    for k, v in ctx.items():
                        resolved = resolved.replace(f"{{{k}}}", str(v) if v is not None else "NULL")
                    self._db.execute(resolved)
                elif step.driver == "http" and step.url and step.method:
                    self._http.request(step.method, step.url, context=ctx)
            except Exception as exc:
                logger.warning("Cleanup step failed for '%s': %s", scenario.id, exc)

    def _build_context(self, captured: dict[str, Any], scenario: Scenario) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        ctx: dict[str, Any] = {
            "backend": self._config.backend,
            "mqtt": f"{self._config.mqtt_host}:{self._config.mqtt_port}",
            "amqp": self._config.amqp_url,
            "now_iso": now.isoformat(),
            "now_epoch": int(now.timestamp()),
        }
        for k, v in captured.items():
            ctx[f"captured.{k}"] = v
        return ctx

    def close(self) -> None:
        self._http.close()
        self._mqtt.disconnect()
