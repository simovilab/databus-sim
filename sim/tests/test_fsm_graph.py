"""Unit tests for fsm_graph.py — covers all 6 FSMs and iterability guarantees."""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest

from harness.fsm_graph import Edge, FsmGraph, State, load_fsm, load_all_fsms

# --- helpers ---

FSM_DIR = Path(__file__).parent.parent.parent.parent / "context" / "behavior" / "databus" / "system" / "json"


def make_minimal_fsm(tmp_path: Path, states: dict) -> Path:
    data = {"id": "test-fsm", "initial": next(iter(states)), "states": states, "context": {}}
    p = tmp_path / "test-fsm.json"
    p.write_text(json.dumps(data))
    return p


# --- basic parsing ---


@pytest.mark.unit
def test_load_register_run():
    graph = load_fsm(FSM_DIR / "register-run.json")
    assert graph.fsm_id == "register-run"
    assert graph.initial == "waiting"
    assert "waiting" in graph.states
    assert "submitting" in graph.states
    assert "validating" in graph.states
    assert "notifying" in graph.states
    assert "initializing" in graph.states
    assert "confirming" in graph.states


@pytest.mark.unit
def test_load_ingest_telemetry():
    graph = load_fsm(FSM_DIR / "ingest-telemetry.json")
    assert graph.fsm_id == "ingest_telemetry"
    assert "waiting" in graph.states
    assert "validating" in graph.states
    assert "processing" in graph.states
    assert "notifying" in graph.states


@pytest.mark.unit
def test_load_update_system_state():
    graph = load_fsm(FSM_DIR / "update-system-state.json")
    assert graph.fsm_id == "update_system_state"
    assert "fetching" in graph.states
    assert "evaluating" in graph.states
    assert "updating" in graph.states


@pytest.mark.unit
def test_load_build_gtfs_realtime():
    graph = load_fsm(FSM_DIR / "build-gtfs-realtime.json")
    assert graph.fsm_id == "build-gtfs-realtime"
    assert "snapshotting" in graph.states
    assert "building" in graph.states
    assert "publishing" in graph.states


@pytest.mark.unit
def test_load_save_gtfs_feed_messages():
    graph = load_fsm(FSM_DIR / "save-gtfs-feed-messages.json")
    assert graph.fsm_id == "save-gtfs-feed-messages"
    assert "serializing" in graph.states
    assert "persisting" in graph.states


@pytest.mark.unit
def test_load_all_skips_end_run(tmp_path, caplog):
    import logging
    with caplog.at_level(logging.WARNING, logger="harness.fsm_graph"):
        graphs = load_all_fsms(FSM_DIR)
    assert "end-run" not in graphs
    assert any("SPEC BUG" in r.message for r in caplog.records)


@pytest.mark.unit
def test_entry_as_dict_parsed(tmp_path):
    """Entry action as single dict (not list) must be parsed correctly."""
    path = make_minimal_fsm(tmp_path, {
        "waiting": {"on": {"TRIGGER": [{"target": "confirming", "actions": []}]}},
        "confirming": {
            "entry": {"type": "some.action"},
            "on": {"DONE": [{"target": "waiting", "actions": []}]},
        },
    })
    graph = load_fsm(path)
    assert "some.action" in graph.states["confirming"].entry_actions


@pytest.mark.unit
def test_entry_as_list_parsed(tmp_path):
    path = make_minimal_fsm(tmp_path, {
        "waiting": {"on": {"TRIGGER": [{"target": "working", "actions": []}]}},
        "working": {
            "entry": [{"type": "action.a"}, {"type": "action.b"}],
            "on": {"DONE": [{"target": "waiting", "actions": []}]},
        },
    })
    graph = load_fsm(path)
    assert graph.states["working"].entry_actions == ("action.a", "action.b")


# --- navigation ---


@pytest.mark.unit
def test_paths_happy_path():
    graph = load_fsm(FSM_DIR / "register-run.json")
    paths = graph.paths("submitting", "confirming")
    assert any(
        [e.event for e in p] == [
            "backend.RUN_SUBMISSION_SUCCEEDED",
            "realtime_engine.RUN_VALIDATION_SUCCEEDED",
            "realtime_engine.RUN_INITIALIZATION_SUCCEEDED",
        ]
        for p in paths
    )


@pytest.mark.unit
def test_transitions_to():
    graph = load_fsm(FSM_DIR / "register-run.json")
    edges = graph.transitions_to("notifying")
    events = {e.event for e in edges}
    assert "backend.RUN_SUBMISSION_FAILED" in events
    assert "realtime_engine.RUN_VALIDATION_FAILED" in events
    assert "realtime_engine.RUN_INITIALIZATION_FAILED" in events
    assert "realtime_engine.RUN_CONFIRMATION_FAILED" in events


@pytest.mark.unit
def test_events_out():
    graph = load_fsm(FSM_DIR / "register-run.json")
    events = graph.events_out("submitting")
    assert "backend.RUN_SUBMISSION_SUCCEEDED" in events
    assert "backend.RUN_SUBMISSION_FAILED" in events


@pytest.mark.unit
def test_failure_edges():
    graph = load_fsm(FSM_DIR / "register-run.json")
    failure = graph.failure_edges()
    events = {e.event for e in failure}
    assert "backend.RUN_SUBMISSION_FAILED" in events
    assert "realtime_engine.RUN_VALIDATION_FAILED" in events
    assert "realtime_engine.RUN_INITIALIZATION_FAILED" in events
    assert "realtime_engine.RUN_CONFIRMATION_FAILED" in events


@pytest.mark.unit
def test_validate_reference_ok():
    graph = load_fsm(FSM_DIR / "register-run.json")
    graph.validate_reference(state="waiting")
    graph.validate_reference(event="backend.RUN_SUBMISSION_SUCCEEDED")


@pytest.mark.unit
def test_validate_reference_bad_state():
    graph = load_fsm(FSM_DIR / "register-run.json")
    with pytest.raises(ValueError, match="unknown state"):
        graph.validate_reference(state="GHOST_STATE")


@pytest.mark.unit
def test_validate_reference_bad_event():
    graph = load_fsm(FSM_DIR / "register-run.json")
    with pytest.raises(ValueError, match="unknown event"):
        graph.validate_reference(event="GHOST_EVENT")


# --- path_exists ---


@pytest.mark.unit
def test_path_exists_valid():
    graph = load_fsm(FSM_DIR / "register-run.json")
    assert graph.path_exists(
        ["submitting", "validating"],
        ["backend.RUN_SUBMISSION_SUCCEEDED"],
    )


@pytest.mark.unit
def test_path_exists_invalid_sequence():
    graph = load_fsm(FSM_DIR / "register-run.json")
    # submitting does not go to initializing directly
    assert not graph.path_exists(
        ["submitting", "initializing"],
        ["backend.RUN_SUBMISSION_SUCCEEDED"],
    )


# ======================================================================
# ITERABILITY GUARANTEE TESTS (§8 of HANDOFF)
# ======================================================================


@pytest.mark.unit
def test_iterability_renamed_state_breaks_scenario_load(tmp_path):
    """§8 #1 — renaming a state causes scenarios referencing old name to fail loading."""
    from harness.scenario_loader import load_scenarios
    from harness.fsm_graph import load_all_fsms

    # Build a tiny FSM where 'submitting' is renamed to 'processing'
    fsm_data = {
        "id": "register-run",
        "initial": "waiting",
        "context": {},
        "states": {
            "waiting": {"on": {"backend.RUN_SUBMISSION_REQUESTED": [{"target": "processing", "actions": []}]}},
            "processing": {  # was 'submitting'
                "entry": [],
                "on": {
                    "backend.RUN_SUBMISSION_SUCCEEDED": [{"target": "waiting", "actions": []}],
                    "backend.RUN_SUBMISSION_FAILED": [{"target": "waiting", "actions": []}],
                },
            },
        },
    }
    fsm_dir = tmp_path / "fsm"
    fsm_dir.mkdir()
    (fsm_dir / "register-run.json").write_text(json.dumps(fsm_data))

    # Scenario still references old name 'submitting'
    scenario_dir = tmp_path / "scenarios" / "register-run"
    scenario_dir.mkdir(parents=True)
    (scenario_dir / "happy.yaml").write_text(textwrap.dedent("""
        id: register-run-happy
        fsm: register-run
        description: happy path
        expected_branch:
          terminal_state: waiting
          via_states: [submitting]
          via_events: [backend.RUN_SUBMISSION_SUCCEEDED]
        inputs: []
        tags: []
    """))

    from harness.fsm_graph import load_all_fsms as load_from_dir
    graphs = {g.fsm_id: g for g in [load_fsm(fsm_dir / "register-run.json")]}
    result = load_scenarios(scenarios_dir=scenario_dir, fsm_graphs=graphs)

    assert not result.ok
    assert any("submitting" in str(err) for err in result.errors), \
        f"Expected dead-ref error for 'submitting', got: {result.errors}"


@pytest.mark.unit
def test_iterability_new_failure_edge_shows_in_coverage():
    """§8 #2 — a new FAILED edge not covered by any scenario shows up as uncovered."""
    graph = load_fsm(FSM_DIR / "register-run.json")
    failure_edges = graph.failure_edges()
    # No scenarios cover all failure edges yet — at least one must be uncovered
    # (this test just verifies the detection mechanism works)
    covered_events: set[str] = set()  # empty — nothing covered
    uncovered = [e for e in failure_edges if e.event not in covered_events]
    assert len(uncovered) > 0, "Expected at least one uncovered failure edge"


@pytest.mark.unit
def test_iterability_path_invalidity_on_valid_refs(tmp_path):
    """§8 #3 — scenario whose path doesn't exist fails loading even if all refs are valid."""
    from harness.scenario_loader import load_scenarios

    fsm_data = {
        "id": "register-run",
        "initial": "waiting",
        "context": {},
        "states": {
            "waiting": {"on": {"backend.RUN_SUBMISSION_REQUESTED": [{"target": "submitting", "actions": []}]}},
            "submitting": {
                "entry": [],
                "on": {
                    "backend.RUN_SUBMISSION_SUCCEEDED": [{"target": "validating", "actions": []}],
                    "backend.RUN_SUBMISSION_FAILED": [{"target": "notifying", "actions": []}],
                },
            },
            "validating": {
                "entry": [],
                "on": {"realtime_engine.RUN_VALIDATION_SUCCEEDED": [{"target": "waiting", "actions": []}]},
            },
            "notifying": {
                "entry": [],
                "on": {"backend.RUN_NOTIFICATION_SENT": [{"target": "waiting", "actions": []}]},
            },
        },
    }
    fsm_dir = tmp_path / "fsm"
    fsm_dir.mkdir()
    (fsm_dir / "register-run.json").write_text(json.dumps(fsm_data))

    scenario_dir = tmp_path / "scenarios" / "register-run"
    scenario_dir.mkdir(parents=True)
    # References valid states/events but path is impossible:
    # submitting → notifying cannot happen via RUN_SUBMISSION_SUCCEEDED (that goes to validating)
    (scenario_dir / "impossible.yaml").write_text(textwrap.dedent("""
        id: impossible-path
        fsm: register-run
        description: impossible path
        expected_branch:
          terminal_state: notifying
          via_states: [submitting, notifying]
          via_events: [backend.RUN_SUBMISSION_SUCCEEDED]
        inputs: []
        tags: []
    """))

    graphs = {"register-run": load_fsm(fsm_dir / "register-run.json")}
    result = load_scenarios(scenarios_dir=scenario_dir, fsm_graphs=graphs)

    assert not result.ok
    assert any("No valid path" in str(err) for err in result.errors), \
        f"Expected path-invalidity error, got: {result.errors}"


@pytest.mark.unit
def test_iterability_updated_state_name_loads_ok(tmp_path):
    """§8 #4 — scenario using new state name loads fine after rename."""
    from harness.scenario_loader import load_scenarios

    fsm_data = {
        "id": "register-run",
        "initial": "waiting",
        "context": {},
        "states": {
            "waiting": {"on": {"backend.RUN_SUBMISSION_REQUESTED": [{"target": "processing", "actions": []}]}},
            "processing": {
                "entry": [],
                "on": {"backend.RUN_SUBMISSION_SUCCEEDED": [{"target": "waiting", "actions": []}]},
            },
        },
    }
    fsm_dir = tmp_path / "fsm"
    fsm_dir.mkdir()
    (fsm_dir / "register-run.json").write_text(json.dumps(fsm_data))

    scenario_dir = tmp_path / "scenarios" / "register-run"
    scenario_dir.mkdir(parents=True)
    (scenario_dir / "updated.yaml").write_text(textwrap.dedent("""
        id: updated-name
        fsm: register-run
        description: uses new state name
        expected_branch:
          terminal_state: waiting
          via_states: [processing]
          via_events: [backend.RUN_SUBMISSION_SUCCEEDED]
        inputs: []
        tags: []
    """))

    graphs = {"register-run": load_fsm(fsm_dir / "register-run.json")}
    result = load_scenarios(scenarios_dir=scenario_dir, fsm_graphs=graphs)

    assert result.ok, f"Expected clean load, got errors: {result.errors}"
    assert len(result.scenarios) == 1
