import tempfile
import sqlite3
from pathlib import Path
import pytest

from ouv.discovery import ModuleInfo
from ouv.orchestrator import Orchestrator
from ouv.history import HistoryStore
from ouv.verdict import VerdictState


@pytest.fixture
def temp_workspace():
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        output_dir = tmp_path / "output"
        history_dir = tmp_path / "history"
        ruleset_path = tmp_path / "rules.yaml"

        ruleset_path.write_text("""
schema_version: 2
rules: []
""", encoding="utf-8")

        output_dir.mkdir()
        history_dir.mkdir()
        yield tmp_path, output_dir, history_dir, ruleset_path


def test_circuit_breaker(temp_workspace):
    tmp_path, output_dir, history_dir, ruleset_path = temp_workspace

    mod_dir = tmp_path / "my_module"
    mod_dir.mkdir()
    manifest = mod_dir / "__manifest__.py"
    manifest.write_text("{'name': 'my_module', 'depends': ['base']}", encoding="utf-8")

    # Initialize Git repo so fixer-agent mode requirement passes
    import subprocess
    subprocess.run(["git", "init"], cwd=mod_dir, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=mod_dir, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=mod_dir, check=True)
    subprocess.run(["git", "add", "."], cwd=mod_dir, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=mod_dir, check=True)

    orchestrator = Orchestrator(
        output_dir=output_dir,
        history_dir=history_dir,
        ruleset_path=ruleset_path,
        max_fix_attempts=3
    )

    mod_info = ModuleInfo(
        name="my_module",
        path=mod_dir,
        manifest={"name": "my_module"},
        depends=["base"],
        error="Simulated Failure"
    )

    # Agent runs 3 times and fails
    for i in range(3):
        v = orchestrator.validate_module(mod_info, agent_id="fixer_agent_1", dry_run=True)
        assert v == VerdictState.BLOCKED

    # 4th run should be skipped due to circuit breaker status 'needs_human_triage'
    v4 = orchestrator.validate_module(mod_info, agent_id="fixer_agent_1", dry_run=True)
    assert v4 == VerdictState.BLOCKED

    # Verify fix_attempts status in SQLite
    store = HistoryStore(history_dir)
    with store._get_connection() as conn:
        cur = conn.execute("SELECT status, consecutive_failures FROM fix_attempts WHERE module_name = 'my_module'")
        row = cur.fetchone()
        assert row["status"] == "needs_human_triage"
        assert row["consecutive_failures"] == 3
