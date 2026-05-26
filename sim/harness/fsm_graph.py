"""Parse FSM JSON files into directed graphs and expose navigation utilities."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

DATABUS_DIR = Path(__file__).parent / "json" / "databus"
INFOBUS_DIR = Path(__file__).parent / "json" / "infobus"

@dataclass(frozen=True)
class Edge:
    source: str
    event: str
    target: str
    actions: tuple[str, ...]  # action type names on the transition itself


@dataclass(frozen=True)
class State:
    name: str
    entry_actions: tuple[str, ...]
    outgoing: tuple[Edge, ...]


@dataclass(frozen=True)
class FsmGraph:
    fsm_id: str
    initial: str
    states: dict[str, State]  # name → State
    edges: tuple[Edge, ...]
    system: str = ""  # "databus" or "infobus"

    # --- navigation ---

    def paths(self, from_state: str, to_state: str) -> list[list[Edge]]:
        """All simple paths from from_state to to_state (DFS, cycle-safe)."""
        self._validate_state(from_state)
        self._validate_state(to_state)
        results: list[list[Edge]] = []
        self._dfs(from_state, to_state, [], set(), results)
        return results

    def _dfs(
        self,
        current: str,
        target: str,
        path: list[Edge],
        visited: set[str],
        results: list[list[Edge]],
    ) -> None:
        if current == target and path:
            results.append(list(path))
            return
        if current in visited:
            return
        visited = visited | {current}
        for edge in self.states[current].outgoing:
            path.append(edge)
            self._dfs(edge.target, target, path, visited, results)
            path.pop()

    def transitions_to(self, state: str) -> list[Edge]:
        """All edges whose target is state."""
        self._validate_state(state)
        return [e for e in self.edges if e.target == state]

    def events_out(self, state: str) -> list[str]:
        """Event names that transition out of state."""
        self._validate_state(state)
        return [e.event for e in self.states[state].outgoing]

    def failure_edges(self) -> list[Edge]:
        """Edges whose event name ends with _FAILED."""
        return [e for e in self.edges if e.event.endswith("_FAILED")]

    def all_events(self) -> set[str]:
        return {e.event for e in self.edges}

    def all_actions(self) -> set[str]:
        actions: set[str] = set()
        for state in self.states.values():
            actions.update(state.entry_actions)
            for edge in state.outgoing:
                actions.update(edge.actions)
        return actions

    def validate_reference(self, *, state: str | None = None, event: str | None = None) -> None:
        if state is not None and state not in self.states:
            raise ValueError(f"FSM '{self.fsm_id}': unknown state '{state}'")
        if event is not None and event not in self.all_events():
            raise ValueError(f"FSM '{self.fsm_id}': unknown event '{event}'")

    def path_exists(self, via_states: list[str], via_events: list[str]) -> bool:
        """Return True if there is a valid path through these states/events in order."""
        if not via_states and not via_events:
            return True
        # Walk the path: interleave states and events
        # We need: s0 -[e0]-> s1 -[e1]-> s2 ...
        states = via_states
        events = via_events
        if len(events) != len(states) - 1 if len(states) > 1 else len(events) == 0:
            pass  # asymmetric is fine, just check reachability
        if not states:
            return True
        current = states[0]
        if current not in self.states:
            return False
        for i, evt in enumerate(events):
            found = False
            for edge in self.states[current].outgoing:
                if edge.event == evt:
                    next_state = edge.target
                    if i + 1 < len(states) and states[i + 1] != next_state:
                        continue
                    current = next_state
                    found = True
                    break
            if not found:
                return False
        return True

    # --- private ---

    def _validate_state(self, state: str) -> None:
        if state not in self.states:
            raise ValueError(f"FSM '{self.fsm_id}': unknown state '{state}'")

    def summary(self) -> dict[str, int]:
        return {
            "states": len(self.states),
            "edges": len(self.edges),
            "failure_edges": len(self.failure_edges()),
        }


def _parse_actions(raw: object) -> tuple[str, ...]:
    """Handle single dict, list of dicts, or missing entry."""
    if raw is None:
        return ()
    if isinstance(raw, dict):
        return (raw["type"],)
    if isinstance(raw, list):
        return tuple(item["type"] for item in raw)
    raise ValueError(f"Unexpected actions shape: {raw!r}")


def load_fsm(path: Path, system: str = "") -> FsmGraph:
    data = json.loads(path.read_text())
    fsm_id: str = data["id"]
    initial: str = data["initial"]
    raw_states: dict = data["states"]

    edges_list: list[Edge] = []
    states: dict[str, State] = {}

    for state_name, state_data in raw_states.items():
        entry_actions = _parse_actions(state_data.get("entry"))
        outgoing: list[Edge] = []
        for event_name, transitions in (state_data.get("on") or {}).items():
            for tr in transitions:
                tr_actions = _parse_actions(tr.get("actions"))
                edge = Edge(
                    source=state_name,
                    event=event_name,
                    target=tr["target"],
                    actions=tr_actions,
                )
                outgoing.append(edge)
                edges_list.append(edge)
        states[state_name] = State(
            name=state_name,
            entry_actions=entry_actions,
            outgoing=tuple(outgoing),
        )

    return FsmGraph(
        fsm_id=fsm_id,
        initial=initial,
        states=states,
        edges=tuple(edges_list),
        system=system,
    )


def load_all_fsms(fsm_dirs: list[Path] | None = None) -> dict[str, FsmGraph]:
    """Load every FSM JSON from each dir in fsm_dirs, tagging each graph with its system name."""
    if fsm_dirs is None:
        fsm_dirs = [DATABUS_DIR]
    graphs: dict[str, FsmGraph] = {}
    for fsm_dir in fsm_dirs:
        system = fsm_dir.name
        for path in sorted(fsm_dir.glob("*.json")):
            try:
                graph = load_fsm(path, system=system)
                graphs[graph.fsm_id] = graph
                logger.debug("Loaded FSM '%s' [%s] (%d states, %d edges)", graph.fsm_id, system, len(graph.states), len(graph.edges))
            except Exception as exc:
                logger.error("Failed to load FSM from %s: %s", path, exc)
                raise
    return graphs
