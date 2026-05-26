"""Console and JUnit XML reporter for scenario results."""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from pathlib import Path

from rich.console import Console
from rich.table import Table

from .runner import ScenarioResult

logger = logging.getLogger(__name__)

console = Console()


def print_results(results: list[ScenarioResult]) -> None:
    table = Table(title="Harness Results", show_lines=True)
    table.add_column("Scenario", style="cyan", no_wrap=True)
    table.add_column("FSM", style="blue")
    table.add_column("Status", justify="center")
    table.add_column("Duration", justify="right")
    table.add_column("Notes")

    for r in results:
        if r.skipped:
            status = "[yellow]SKIP[/yellow]"
            notes = r.skip_reason
        elif r.passed:
            status = "[green]PASS[/green]"
            notes = "; ".join(a.message for a in r.assertions if a.passed)
        else:
            status = "[red]FAIL[/red]"
            failed = [a.message for a in r.assertions if not a.passed]
            notes = r.error or "; ".join(failed)

        table.add_row(r.scenario_id, r.fsm, status, f"{r.duration_ms:.0f}ms", notes)

    console.print(table)

    total = len(results)
    passed = sum(1 for r in results if r.passed)
    skipped = sum(1 for r in results if r.skipped)
    failed = total - passed - skipped
    console.print(
        f"\n[bold]Total:[/bold] {total}  "
        f"[green]Pass: {passed}[/green]  "
        f"[red]Fail: {failed}[/red]  "
        f"[yellow]Skip: {skipped}[/yellow]"
    )


def write_junit(results: list[ScenarioResult], output_path: Path) -> None:
    suite = ET.Element("testsuite", name="databus-fsm-harness",
                       tests=str(len(results)),
                       failures=str(sum(1 for r in results if not r.passed and not r.skipped)),
                       skipped=str(sum(1 for r in results if r.skipped)))

    for r in results:
        case = ET.SubElement(suite, "testcase",
                             name=r.scenario_id,
                             classname=r.fsm,
                             time=str(r.duration_ms / 1000))
        if r.skipped:
            ET.SubElement(case, "skipped", message=r.skip_reason)
        elif not r.passed:
            failures = [a.message for a in r.assertions if not a.passed]
            msg = r.error or "; ".join(failures)
            ET.SubElement(case, "failure", message=msg)

    tree = ET.ElementTree(suite)
    ET.indent(tree)
    tree.write(output_path, encoding="unicode", xml_declaration=True)
    logger.info("JUnit XML written to %s", output_path)


def print_coverage(
    graphs: dict,  # fsm_id → FsmGraph
    covered_edges: set[tuple[str, str, str]],  # (fsm_id, source, event)
    stubbed_edges: set[tuple[str, str, str]] | None = None,  # (fsm_id, source, event)
) -> None:
    from rich.text import Text

    stubbed_edges = stubbed_edges or set()
    table = Table(title="FSM Edge Coverage", show_lines=True)
    table.add_column("FSM")
    table.add_column("Transition", overflow="ellipsis", min_width=40)  # rendered as Text to avoid Rich markup parsing of event names
    table.add_column("Type")
    table.add_column("Covered", justify="center")

    for fsm_id, graph in graphs.items():
        for edge in graph.edges:
            key = (fsm_id, edge.source, edge.event)
            is_failure = edge.event.endswith("_FAILED")
            if key in covered_edges:
                status = "[green]✓[/green]"
            elif key in stubbed_edges:
                status = "[yellow]stub[/yellow]"
            else:
                status = "[red]✗[/red]"
            transition = Text(f"{edge.source} → {edge.event} → {edge.target}")
            table.add_row(
                fsm_id,
                transition,
                "[red]failure[/red]" if is_failure else "happy",
                status,
            )

    console.print(table)
