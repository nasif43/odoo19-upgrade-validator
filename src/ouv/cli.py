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
    ruleset_path: Path = typer.Option(Path("./ruleset/odoo_18_19_rules.yaml"), help="Path to main ruleset file"),
    apply: bool = typer.Option(False, "--apply", help="Interactively approve and apply proposals to the ruleset"),
):
    """Human review queue for ruleset proposals. Pass --apply to write approved changes.

    This is the ONLY write path into ruleset/*.yaml (Addendum v1.2, Point 1).
    No automated code path may call this with --apply; it requires a human at the terminal.
    """
    if not pending_updates_path.exists():
        console.print("[yellow]No pending ruleset updates file found.[/yellow]")
        return

    yaml = ruamel.yaml.YAML()
    yaml.preserve_quotes = True
    data = yaml.load(pending_updates_path.read_text(encoding="utf-8")) or {}
    proposals = data.get("pending_proposals", [])

    if not proposals:
        console.print("[green]No pending ruleset proposals to review.[/green]")
        return

    # Sort proposals by confirmation_count (highest priority first)
    proposals.sort(key=lambda x: x.get("confirmation_count", 0), reverse=True)

    console.print(f"[bold cyan]Pending Ruleset Proposals ({len(proposals)} items):[/bold cyan]\n")
    for i, prop in enumerate(proposals, start=1):
        console.print(f"[{i}] Module: [bold]{prop['module_name']}[/bold]"
                      f" (Confirmed across [yellow]{prop['confirmation_count']}[/yellow] modules)")
        console.print(f"    Resolved Rules  : {prop['rules_resolved']}")
        console.print(f"    Error Fingerprint: {prop['error_fingerprint']}")
        console.print("")

    console.print("[italic]Note: Human approval via this command is the ONLY write path into ruleset/*.yaml.[/italic]\n")

    if not apply:
        console.print("[dim]Pass --apply to enter interactive approval mode.[/dim]")
        return

    # ── Interactive approval ──────────────────────────────────────────────────
    raw = typer.prompt("Enter proposal numbers to approve (comma-separated), or 'none' to skip")
    if raw.strip().lower() == "none" or not raw.strip():
        console.print("[yellow]No proposals approved. Ruleset unchanged.[/yellow]")
        return

    try:
        indices = [int(x.strip()) for x in raw.split(",") if x.strip()]
    except ValueError:
        console.print("[bold red]Invalid input — expected comma-separated integers.[/bold red]")
        raise typer.Exit(code=1)

    valid_indices = [i for i in indices if 1 <= i <= len(proposals)]
    if not valid_indices:
        console.print("[yellow]No valid proposal indices provided. Ruleset unchanged.[/yellow]")
        return

    # Load the live ruleset YAML (ruamel.yaml preserves all comments and formatting)
    if not ruleset_path.exists():
        console.print(f"[bold red]Ruleset file not found: {ruleset_path}[/bold red]")
        raise typer.Exit(code=1)

    ruleset_data = yaml.load(ruleset_path.read_text(encoding="utf-8"))
    ruleset_rules: list = ruleset_data.get("rules", [])

    # Build a lookup: rule_id → index in the rules list
    rule_index_map = {r.get("id"): idx for idx, r in enumerate(ruleset_rules)}

    approved_proposals = []
    skipped = []

    for idx in valid_indices:
        prop = proposals[idx - 1]
        rules_resolved: list = prop.get("rules_resolved", [])
        error_fp: str = prop.get("error_fingerprint", "")
        module_name: str = prop.get("module_name", "")

        matched_any = False
        for rule_id in rules_resolved:
            if rule_id not in rule_index_map:
                console.print(f"  [yellow]SKIP[/yellow] Rule '{rule_id}' not found in ruleset — ignoring.")
                continue
            rule_entry = ruleset_rules[rule_index_map[rule_id]]

            # Update telemetry counters (passive — do not change rule logic)
            rule_entry["confirmed_in_module_count"] = rule_entry.get("confirmed_in_module_count", 0) + 1
            confirmed_fixes = rule_entry.get("confirmed_fixes", [])
            if module_name not in confirmed_fixes:
                confirmed_fixes.append(module_name)
            rule_entry["confirmed_fixes"] = confirmed_fixes

            if error_fp and error_fp not in rule_entry.get("install_error_signatures", []):
                sigs = rule_entry.get("install_error_signatures", [])
                sigs.append(error_fp)
                rule_entry["install_error_signatures"] = sigs

            matched_any = True
            console.print(f"  [green]APPLIED[/green] Updated confirmation telemetry on rule '{rule_id}' "
                          f"(module: {module_name}, count → {rule_entry['confirmed_in_module_count']})")

        if matched_any:
            approved_proposals.append(prop)
        else:
            skipped.append(prop)

    if not approved_proposals:
        console.print("[yellow]No proposals could be matched to existing rules. Ruleset unchanged.[/yellow]")
        return

    # Write the updated ruleset back (preserving all comments and formatting)
    import io
    buf = io.StringIO()
    yaml.dump(ruleset_data, buf)
    ruleset_path.write_text(buf.getvalue(), encoding="utf-8")
    console.print(f"\n[bold green]✓ Ruleset updated:[/bold green] {ruleset_path}")

    # Remove approved proposals from pending_updates.yaml
    remaining = [p for p in proposals if p not in approved_proposals]
    data["pending_proposals"] = remaining
    buf2 = io.StringIO()
    yaml.dump(data, buf2)
    pending_updates_path.write_text(buf2.getvalue(), encoding="utf-8")
    console.print(f"[bold green]✓ Cleared {len(approved_proposals)} proposal(s) from queue:[/bold green] {pending_updates_path}")

    if skipped:
        console.print(f"[yellow]{len(skipped)} proposal(s) skipped (rules not found in ruleset).[/yellow]")


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
