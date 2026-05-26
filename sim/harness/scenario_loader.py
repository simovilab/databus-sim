"""Load scenario YAMLs, validate every FSM reference, expand templates."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from .fsm_graph import FsmGraph, load_all_fsms
from .scenario_schema import Scenario

logger = logging.getLogger(__name__)

SCENARIOS_DIR = Path(__file__).parent.parent / "scenarios"
DATABUS_SCENARIOS_DIR = SCENARIOS_DIR / "databus"
INFOBUS_SCENARIOS_DIR = SCENARIOS_DIR / "infobus"


@dataclass(frozen=True)
class LoadError:
    path: Path
    scenario_id: str | None
    field: str
    message: str

    def __str__(self) -> str:
        loc = f"{self.path}"
        if self.scenario_id:
            loc += f" (id={self.scenario_id})"
        return f"[LOAD ERROR] {loc} :: {self.field} → {self.message}"


@dataclass(frozen=True)
class LoadResult:
    scenarios: list[Scenario]
    errors: list[LoadError]

    @property
    def ok(self) -> bool:
        return len(self.errors) == 0


def _validate_fsm_refs(scenario: Scenario, graph: FsmGraph, path: Path) -> list[LoadError]:
    errors: list[LoadError] = []

    branch = scenario.expected_branch

    # Validate terminal_state
    if branch.terminal_state not in graph.states:
        errors.append(LoadError(path, scenario.id, "expected_branch.terminal_state",
                                f"'{branch.terminal_state}' not in FSM '{graph.fsm_id}'"))

    # Validate via_states
    for s in branch.via_states:
        if s not in graph.states:
            errors.append(LoadError(path, scenario.id, "expected_branch.via_states",
                                    f"'{s}' not in FSM '{graph.fsm_id}'"))

    # Validate via_events
    all_events = graph.all_events()
    for e in branch.via_events:
        if e not in all_events:
            errors.append(LoadError(path, scenario.id, "expected_branch.via_events",
                                    f"'{e}' not in FSM '{graph.fsm_id}'"))

    # Validate the path actually exists in the graph (path validity ≠ reference validity)
    if not errors:  # only check if refs are individually valid
        all_states = list(branch.via_states) + [branch.terminal_state]
        if not graph.path_exists(all_states, list(branch.via_events)):
            errors.append(LoadError(path, scenario.id, "expected_branch",
                                    f"No valid path through states={branch.via_states} "
                                    f"events={branch.via_events} → {branch.terminal_state} "
                                    f"in FSM '{graph.fsm_id}'"))

    # Validate observation action references via the graph's action set
    if scenario.expect:
        known_actions = graph.all_actions()
        for obs in scenario.expect.observations:
            if obs.action not in known_actions:
                errors.append(LoadError(path, scenario.id, "expect.observations",
                                        f"action '{obs.action}' not in FSM '{graph.fsm_id}'"))

    return errors


def load_scenarios(
    scenarios_dir: Path = SCENARIOS_DIR,
    fsm_graphs: dict[str, FsmGraph] | None = None,
    tags: list[str] | None = None,
) -> LoadResult:
    if fsm_graphs is None:
        fsm_graphs = load_all_fsms()

    scenarios: list[Scenario] = []
    errors: list[LoadError] = []

    yaml_files = sorted(scenarios_dir.rglob("*.yaml"))
    # Exclude _scaffold stubs unless the caller explicitly pointed at the scaffold dir
    if scenarios_dir.name != "_scaffold":
        yaml_files = [p for p in yaml_files if "_scaffold" not in p.parts]

    for path in yaml_files:
        raw = _read_yaml(path)
        if raw is None:
            errors.append(LoadError(path, None, "file", "Could not parse YAML"))
            continue

        try:
            scenario = Scenario.model_validate(raw)
        except ValidationError as exc:
            errors.append(LoadError(path, raw.get("id"), "schema", str(exc)))
            continue

        # FSM reference validation
        graph = fsm_graphs.get(scenario.fsm)
        if graph is None:
            errors.append(LoadError(path, scenario.id, "fsm",
                                    f"FSM '{scenario.fsm}' not found (known: {sorted(fsm_graphs)})"))
            continue

        ref_errors = _validate_fsm_refs(scenario, graph, path)
        if ref_errors:
            errors.extend(ref_errors)
            continue

        # Tag filter
        if tags and not any(t in scenario.tags for t in tags):
            continue

        scenarios.append(scenario)
        logger.debug("Loaded scenario '%s' from %s", scenario.id, path)

    return LoadResult(scenarios=scenarios, errors=errors)


def _read_yaml(path: Path) -> dict | None:
    try:
        data = yaml.safe_load(path.read_text())
        if not isinstance(data, dict):
            return None
        return data
    except Exception as exc:
        logger.error("YAML parse error in %s: %s", path, exc)
        return None


def load_scaffold_scenarios(scenarios_dir: Path = SCENARIOS_DIR) -> list[Scenario]:
    scaffold_dir = scenarios_dir / "_scaffold"
    if not scaffold_dir.exists():
        return []
    result = load_scenarios(scaffold_dir)
    return result.scenarios
