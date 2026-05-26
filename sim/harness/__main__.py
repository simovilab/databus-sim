"""CLI entry point: python -m harness <subcommand>."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from rich.console import Console

console = Console()


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


# --- helpers ---


def _fsm_dirs(args: argparse.Namespace) -> list[Path]:
    from .fsm_graph import DATABUS_DIR, INFOBUS_DIR
    dirs: list[Path] = []
    if getattr(args, "databus", False):
        dirs.append(DATABUS_DIR)
    if getattr(args, "infobus", False):
        dirs.append(INFOBUS_DIR)
    return dirs or [DATABUS_DIR]


def _scenarios_dirs(args: argparse.Namespace) -> list[Path]:
    from .scenario_loader import DATABUS_SCENARIOS_DIR, INFOBUS_SCENARIOS_DIR
    dirs: list[Path] = []
    if getattr(args, "databus", False):
        dirs.append(DATABUS_SCENARIOS_DIR)
    if getattr(args, "infobus", False):
        dirs.append(INFOBUS_SCENARIOS_DIR)
    return dirs or [DATABUS_SCENARIOS_DIR]


def _load_scenarios_multi(
    fsm_graphs: dict,
    scenarios_dirs: list[Path],
    tags: list[str] | None = None,
):
    from .scenario_loader import LoadResult, load_scenarios
    all_scenarios = []
    all_errors = []
    for sdir in scenarios_dirs:
        r = load_scenarios(scenarios_dir=sdir, fsm_graphs=fsm_graphs, tags=tags)
        all_scenarios.extend(r.scenarios)
        all_errors.extend(r.errors)
    return LoadResult(scenarios=all_scenarios, errors=all_errors)


# --- subcommand implementations ---


def cmd_list_fsms(args: argparse.Namespace) -> int:
    from .fsm_graph import load_all_fsms
    from .mappings import load_mappings, validate_mappings

    fsm_dirs = _fsm_dirs(args)
    graphs = load_all_fsms(fsm_dirs)
    mappings = load_mappings()
    warnings = validate_mappings(mappings, graphs)

    from rich.table import Table
    multi = len(fsm_dirs) > 1
    table = Table(title="FSMs", show_lines=True)
    if multi:
        table.add_column("System")
    table.add_column("ID")
    table.add_column("Initial")
    table.add_column("States", justify="right")
    table.add_column("Edges", justify="right")
    table.add_column("Failure edges", justify="right")
    for fsm_id, g in graphs.items():
        s = g.summary()
        row = [fsm_id, g.initial, str(s["states"]), str(s["edges"]), str(s["failure_edges"])]
        if multi:
            row = [g.system] + row
        table.add_row(*row)
    console.print(table)

    if warnings:
        console.print(f"\n[yellow]⚠  {len(warnings)} mapping warning(s):[/yellow]")
        for w in warnings:
            console.print(f"   {w}")
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    from .fsm_graph import load_all_fsms

    graphs = load_all_fsms(_fsm_dirs(args))
    result = _load_scenarios_multi(graphs, _scenarios_dirs(args))
    if result.ok:
        console.print(f"[green]✓ All {len(result.scenarios)} scenarios loaded cleanly.[/green]")
        return 0
    else:
        console.print(f"[red]✗ {len(result.errors)} load error(s):[/red]")
        for err in result.errors:
            console.print(f"  {err}")
        return 1


def cmd_run(args: argparse.Namespace) -> int:
    from .config import load_config
    from .fsm_graph import load_all_fsms
    from .mappings import load_mappings
    from .observers.amqp_sniffer import AmqpSniffer
    from .observers.db_poller import DbPoller
    from .observers.lake_watcher import LakeWatcher
    from .reporter import print_results, write_junit
    from .runner import ScenarioRunner

    graphs = load_all_fsms(_fsm_dirs(args))
    tags: list[str] | None = args.tags.split(",") if args.tags else None
    result = _load_scenarios_multi(graphs, _scenarios_dirs(args), tags=tags)

    if not result.ok:
        console.print(f"[red]✗ {len(result.errors)} scenario load error(s):[/red]")
        for err in result.errors:
            console.print(f"  {err}")
        return 1

    config = load_config()
    mappings = load_mappings()

    sniffer = AmqpSniffer(config)
    sniffer.start()
    db = DbPoller(config)
    db.connect()
    lake = LakeWatcher(config)
    lake.start()

    runner = ScenarioRunner(config, mappings, sniffer, db, lake)

    scenarios = result.scenarios
    if args.scenario_file:
        path = Path(args.scenario_file)
        from .scenario_loader import _read_yaml
        from .scenario_schema import Scenario
        raw = _read_yaml(path)
        if raw is None:
            console.print(f"[red]Cannot read {path}[/red]")
            return 1
        import pydantic
        try:
            scenarios = [Scenario.model_validate(raw)]
        except pydantic.ValidationError as exc:
            console.print(f"[red]{exc}[/red]")
            return 1

    results = [runner.run(s) for s in scenarios]

    print_results(results)

    if args.junit:
        write_junit(results, Path(args.junit))

    sniffer.stop()
    lake.stop()
    runner.close()

    return 0 if all(r.passed or r.skipped for r in results) else 1


def cmd_scaffold(args: argparse.Namespace) -> int:
    from .fsm_graph import load_all_fsms

    scenarios_dirs = _scenarios_dirs(args)
    # map system name → scenarios dir for per-system scaffold placement
    system_to_sdir = {d.name: d for d in scenarios_dirs}

    graphs = load_all_fsms(_fsm_dirs(args))
    existing = _load_scenarios_multi(graphs, scenarios_dirs)
    covered: set[tuple[str, str, str]] = set()
    for s in existing.scenarios:
        graph = graphs.get(s.fsm)
        if not graph:
            continue
        for evt in s.expected_branch.via_events:
            for edge in graph.edges:
                if edge.event == evt:
                    covered.add((s.fsm, edge.source, evt))

    created = 0
    for fsm_id, graph in graphs.items():
        sdir = system_to_sdir.get(graph.system, scenarios_dirs[0])
        scaffold_dir = sdir / "_scaffold"
        scaffold_dir.mkdir(parents=True, exist_ok=True)
        for edge in graph.failure_edges():
            key = (fsm_id, edge.source, edge.event)
            if key not in covered:
                fname = scaffold_dir / f"{fsm_id}__{edge.source}__{edge.event}.yaml"
                if not fname.exists():
                    fname.write_text(_scaffold_template(fsm_id, edge))
                    console.print(f"[yellow]Created stub:[/yellow] {fname}")
                    created += 1

    console.print(f"\n[bold]{created} scaffold stub(s) created.[/bold] Fill in 'inputs:' to enable.")
    return 0


def _scaffold_template(fsm_id: str, edge: object) -> str:
    return (
        f"id: {fsm_id}--{edge.source}--{edge.event}--stub\n"
        f"fsm: {fsm_id}\n"
        f"description: 'TODO: fill in description'\n"
        f"stub: true\n"
        f"\nexpected_branch:\n"
        f"  terminal_state: {edge.target}\n"
        f"  via_states: [{edge.source}]\n"
        f"  via_events: [{edge.event}]\n"
        f"\n# TODO: add inputs that drive the FSM into this failure branch\n"
        f"inputs: []\n"
        f"\ntimeout_ms: 10000\n"
        f"tags: [{fsm_id}, failure-path]\n"
    )


def cmd_prune(args: argparse.Namespace) -> int:
    """Delete scenario files (including scaffold stubs) that fail FSM validation."""
    import pydantic

    from .fsm_graph import load_all_fsms
    from .scenario_loader import _read_yaml
    from .scenario_schema import Scenario

    graphs = load_all_fsms(_fsm_dirs(args))
    dry_run: bool = args.dry_run

    # Collect all yaml files including scaffold across all selected systems
    yaml_files = sorted(
        p for sdir in _scenarios_dirs(args) for p in sdir.rglob("*.yaml")
    )

    deleted = 0
    kept = 0

    for path in yaml_files:
        raw = _read_yaml(path)
        if raw is None:
            label = f"[red]✗ unparseable YAML[/red] {path}"
            if dry_run:
                console.print(f"[dim]dry-run[/dim] would delete: {path} (unparseable YAML)")
            else:
                path.unlink()
                console.print(f"[red]deleted[/red] {path} (unparseable YAML)")
            deleted += 1
            continue

        try:
            scenario = Scenario.model_validate(raw)
        except pydantic.ValidationError as exc:
            if dry_run:
                console.print(f"[dim]dry-run[/dim] would delete: {path} (schema error)")
            else:
                path.unlink()
                console.print(f"[red]deleted[/red] {path} (schema error)")
            deleted += 1
            continue

        graph = graphs.get(scenario.fsm)
        if graph is None:
            if dry_run:
                console.print(f"[dim]dry-run[/dim] would delete: {path} (FSM '{scenario.fsm}' not found)")
            else:
                path.unlink()
                console.print(f"[red]deleted[/red] {path} (FSM '{scenario.fsm}' not found)")
            deleted += 1
            continue

        from .scenario_loader import _validate_fsm_refs
        ref_errors = _validate_fsm_refs(scenario, graph, path)
        if ref_errors:
            reasons = "; ".join(e.message for e in ref_errors)
            if dry_run:
                console.print(f"[dim]dry-run[/dim] would delete: {path} ({reasons})")
            else:
                path.unlink()
                console.print(f"[red]deleted[/red] {path} ({reasons})")
            deleted += 1
        else:
            kept += 1

    action = "Would delete" if dry_run else "Deleted"
    console.print(f"\n[bold]{action} {deleted} stale file(s). {kept} file(s) kept.[/bold]")
    return 0


def cmd_coverage(args: argparse.Namespace) -> int:
    from .fsm_graph import load_all_fsms
    from .reporter import print_coverage
    from .scenario_loader import load_scenarios

    scenarios_dirs = _scenarios_dirs(args)
    graphs = load_all_fsms(_fsm_dirs(args))

    covered: set[tuple[str, str, str]] = set()
    stubbed: set[tuple[str, str, str]] = set()

    for s in _load_scenarios_multi(graphs, scenarios_dirs).scenarios:
        graph = graphs.get(s.fsm)
        if not graph:
            continue
        for evt in s.expected_branch.via_events:
            for edge in graph.edges:
                if edge.event == evt:
                    covered.add((s.fsm, edge.source, evt))

    # Also count valid scaffold stubs so they appear as "stubbed" rather than uncovered
    for sdir in scenarios_dirs:
        scaffold_result = load_scenarios(scenarios_dir=sdir / "_scaffold", fsm_graphs=graphs)
        for s in scaffold_result.scenarios:
            graph = graphs.get(s.fsm)
            if not graph:
                continue
            for evt in s.expected_branch.via_events:
                for edge in graph.edges:
                    if edge.event == evt:
                        key = (s.fsm, edge.source, evt)
                        if key not in covered:
                            stubbed.add(key)

    print_coverage(graphs, covered, stubbed)
    return 0


# --- main ---


def main() -> None:
    parser = argparse.ArgumentParser(prog="harness")
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("-d", "--databus", action="store_true", help="Load databus FSMs")
    parser.add_argument("-i", "--infobus", action="store_true", help="Load infobus FSMs")
    sub = parser.add_subparsers(dest="cmd")

    sub.add_parser("list-fsms", help="Show all parsed FSMs")
    sub.add_parser("validate", help="Load and validate scenarios (no execution)")

    run_p = sub.add_parser("run", help="Run scenarios")
    run_p.add_argument("-t", "--tags", default="", help="Comma-separated tags to filter")
    run_p.add_argument("--junit", default="", help="Write JUnit XML to path")
    run_p.add_argument("scenario_file", nargs="?", default="", help="Run single scenario file")

    sub.add_parser("scaffold", help="Generate stubs for untested FSM failure edges")
    sub.add_parser("coverage", help="Show FSM edge coverage")

    prune_p = sub.add_parser("prune", help="Delete scenario files that are stale against current FSMs")
    prune_p.add_argument("--dry-run", action="store_true", help="Show what would be deleted without deleting")

    args = parser.parse_args()
    _setup_logging(args.verbose)

    handlers = {
        "list-fsms": cmd_list_fsms,
        "validate": cmd_validate,
        "run": cmd_run,
        "scaffold": cmd_scaffold,
        "coverage": cmd_coverage,
        "prune": cmd_prune,
    }

    if not args.cmd or args.cmd not in handlers:
        parser.print_help()
        sys.exit(0)

    sys.exit(handlers[args.cmd](args))


if __name__ == "__main__":
    main()
