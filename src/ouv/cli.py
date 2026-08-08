from pathlib import Path
from typing import List, Optional
import typer
from rich.console import Console
from rich.table import Table
import ruamel.yaml

from ouv.discovery import ModuleDiscoverer
from ouv.graph import DependencyGraphBuilder
from ouv.orchestrator import Orchestrator
from ouv.history import HistoryStore

app = typer.Typer(help="Odoo 18 -> 19 Upgrade Validator (ouv) CLI")
console = Console()


@app.command()
def validate(
    paths: List[Path] = typer.Argument(..., help="Path(s) to Odoo custom module directories"),
    output_dir: Path = typer.Option(Path("./validator_reports"), "--output-dir", help="Directory for generated report folders"),
    history_dir: Path = typer.Option(Path("./history"), "--history-dir", help="Directory for index database & archived runs"),
    ruleset_path: Path = typer.Option(Path("./ruleset/odoo_18_19_rules.yaml"), "--ruleset-path", help="Path to ruleset YAML file"),
    agent_id: str = typer.Option("manual", "--agent-id", envvar="OUV_AGENT_ID", help="Caller / Fixer agent identity"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Run static analysis rules only, skip dynamic DB install"),
    keep_db_on_failure: bool = typer.Option(False, "--keep-db-on-failure", help="Do not drop scratch DB on failure"),
    max_fix_attempts: int = typer.Option(3, envvar="OUV_MAX_FIX_ATTEMPTS", help="Max consecutive fixer-agent attempts before triage block")
):
    """Discovers and validates upgrade readiness for Odoo 18 -> 19 modules."""
    console.print(f"[bold blue]Odoo 18 -> 19 Upgrade Validator (`ouv`)[/bold blue]")
    console.print(f"Caller Identity: [bold]{agent_id}[/bold] | Dry Run: {dry_run}")

    if not ruleset_path.exists():
        console.print(f"[bold red]Error:[/bold red] Ruleset file not found at `{ruleset_path}`")
        raise typer.Exit(code=1)

    # 1. Discover modules
    discovered = ModuleDiscoverer.discover_all(paths)
    if not discovered:
        console.print("[yellow]No Odoo modules discovered in provided paths.[/yellow]")
        return

    console.print(f"Discovered [bold green]{len(discovered)}[/bold green] modules.")

    # 2. Build Dependency DAG
    graph_builder = DependencyGraphBuilder(discovered)
    try:
        topo_order = graph_builder.get_topological_order()
    except Exception as e:
        console.print(f"[bold red]Graph Construction Error:[/bold red] {e}")
        raise typer.Exit(code=1)

    orchestrator = Orchestrator(
        output_dir=output_dir,
        history_dir=history_dir,
        ruleset_path=ruleset_path,
        max_fix_attempts=max_fix_attempts
    )

    table = Table(title="Validation Summary")
    table.add_column("Module", style="cyan")
    table.add_column("Verdict", style="bold")
    table.add_column("Details")

    for mod_name in topo_order:
        mod_info = graph_builder.modules[mod_name]
        verdict = orchestrator.validate_module(
            module_info=mod_info,
            agent_id=agent_id,
            dry_run=dry_run,
            keep_db_on_failure=keep_db_on_failure
        )
        style = "green" if verdict == "passed" else ("yellow" if verdict == "blocked" else "red")
        table.add_row(mod_name, f"[{style}]{verdict.value.upper()}[/{style}]", f"Report in `{output_dir}/{mod_name}_validator_report`")

    console.print(table)


@app.command(name="history")
def history_lineage(
    module_name: str = typer.Argument(..., help="Module name to query attempt history lineage"),
    history_dir: Path = typer.Option(Path("./history"), "--history-dir", help="Directory for history DB")
):
    """Queries complete attempt lineage history for a module (§4.12)."""
    store = HistoryStore(history_dir)
    lineage = store.get_lineage(module_name)

    if not lineage:
        console.print(f"[yellow]No attempt history recorded for module `{module_name}`.[/yellow]")
        return

    table = Table(title=f"Lineage History for `{module_name}`")
    table.add_column("Run ID", style="dim")
    table.add_column("Timestamp")
    table.add_column("Verdict", style="bold")
    table.add_column("Commit SHA")
    table.add_column("Fix Agent ID")
    table.add_column("Duration (ms)")

    for row in lineage:
        v = row["verdict"]
        style = "green" if v == "passed" else ("yellow" if v == "blocked" else "red")
        table.add_row(
            row["run_id"],
            row["timestamp"],
            f"[{style}]{v.upper()}[/{style}]",
            row["commit_sha"] or "N/A",
            row["fix_agent_id"] or "manual",
            str(row["duration_ms"] or 0)
        )

    console.print(table)


@app.command(name="ruleset-review")
def ruleset_review(
    pending_updates_path: Path = typer.Option(Path("./ruleset/pending_updates.yaml"), help="Path to pending updates queue file"),
    ruleset_path: Path = typer.Option(Path("./ruleset/odoo_18_19_rules.yaml"), help="Path to main ruleset file")
):
    """Interactive human review queue for ruleset proposals (Addendum v1.2, Point 1)."""
    if not pending_updates_path.exists():
        console.print("[yellow]No pending ruleset updates file found.[/yellow]")
        return

    yaml = ruamel.yaml.YAML()
    data = yaml.load(pending_updates_path.read_text(encoding="utf-8")) or {}
    proposals = data.get("pending_proposals", [])

    if not proposals:
        console.print("[green]No pending ruleset proposals to review.[/green]")
        return

    # Sort proposals by confirmation_count (repurposed threshold sorting hint)
    proposals.sort(key=lambda x: x.get("confirmation_count", 0), reverse=True)

    console.print(f"[bold cyan]Pending Ruleset Proposals ({len(proposals)} items):[/bold cyan]\n")
    for i, prop in enumerate(proposals, start=1):
        console.print(f"[{i}] Module: [bold]{prop['module_name']}[/bold] (Confirmed across [yellow]{prop['confirmation_count']}[/yellow] modules)")
        console.print(f"    Resolved Rules: {prop['rules_resolved']}")
        console.print(f"    Error Fingerprint: {prop['error_fingerprint']}")
        console.print("")

    console.print("[italic]Note: Human approval via this command is the ONLY write path into ruleset/*.yaml.[/italic]")


@app.command(name="triage-clear")
def triage_clear(
    module_name: str = typer.Argument(..., help="Module name to clear needs_human_triage status"),
    history_dir: Path = typer.Option(Path("./history"), "--history-dir", help="Directory for history DB")
):
    """Resets needs_human_triage circuit breaker status for a module (§4.10)."""
    store = HistoryStore(history_dir)
    with store._get_connection() as conn:
        conn.execute("UPDATE fix_attempts SET status = 'active', consecutive_failures = 0 WHERE module_name = ?", (module_name,))
        conn.commit()
    console.print(f"[green]Successfully cleared triage status for module `{module_name}`.[/green]")


if __name__ == "__main__":
    app()
