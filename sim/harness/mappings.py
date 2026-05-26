"""Load mappings.yaml and resolve FSM action/event names to concrete observables."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import yaml

from .fsm_graph import FsmGraph

logger = logging.getLogger(__name__)

MAPPINGS_FILE = Path(__file__).parent.parent / "mappings.yaml"


@dataclass(frozen=True)
class EventMapping:
    transport: str  # amqp | mqtt | http
    exchange: str | None = None
    routing_key: str | None = None
    topic_pattern: str | None = None
    url_pattern: str | None = None


@dataclass(frozen=True)
class ActionMapping:
    kind: str | None = None  # log_or_noop when no observable side-effect
    transport: str | None = None  # amqp | redis | db | file | http
    exchange: str | None = None
    routing_key: str | None = None
    key_pattern: str | None = None
    table: str | None = None
    path_pattern: str | None = None
    url_pattern: str | None = None


@dataclass(frozen=True)
class Mappings:
    events: dict[str, EventMapping]
    actions: dict[str, ActionMapping]

    def resolve_event(self, name: str) -> EventMapping | None:
        return self.events.get(name)

    def resolve_action(self, name: str) -> ActionMapping | None:
        return self.actions.get(name)


def load_mappings(path: Path = MAPPINGS_FILE) -> Mappings:
    raw = yaml.safe_load(path.read_text())
    events: dict[str, EventMapping] = {}
    for name, spec in (raw.get("events") or {}).items():
        events[name] = EventMapping(
            transport=spec.get("transport", "unknown"),
            exchange=spec.get("exchange"),
            routing_key=spec.get("routing_key"),
            topic_pattern=spec.get("topic_pattern"),
            url_pattern=spec.get("url_pattern"),
        )
    actions: dict[str, ActionMapping] = {}
    for name, spec in (raw.get("actions") or {}).items():
        actions[name] = ActionMapping(
            kind=spec.get("kind"),
            transport=spec.get("transport"),
            exchange=spec.get("exchange"),
            routing_key=spec.get("routing_key"),
            key_pattern=spec.get("key_pattern"),
            table=spec.get("table"),
            path_pattern=spec.get("path_pattern"),
            url_pattern=spec.get("url_pattern"),
        )
    return Mappings(events=events, actions=actions)


def validate_mappings(mappings: Mappings, graphs: dict[str, FsmGraph]) -> list[str]:
    """Cross-check every FSM event/action against mappings. Returns warning strings."""
    warnings: list[str] = []
    for fsm_id, graph in graphs.items():
        for event in graph.all_events():
            if event not in mappings.events:
                warnings.append(f"[{fsm_id}] Event '{event}' has no mapping in mappings.yaml")
        for action in graph.all_actions():
            if action not in mappings.actions:
                warnings.append(f"[{fsm_id}] Action '{action}' has no mapping in mappings.yaml")
    return warnings
