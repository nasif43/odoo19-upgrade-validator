import uuid
import os
import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Dict, Any

from ouv.discovery import ModuleDiscoverer, ModuleInfo
from ouv.graph import DependencyGraphBuilder
from ouv.static_checks import StaticAnalysisEngine, RuleMatch
from ouv.db_templates import DBTemplateManager
from ouv.installer import InstallExecutor, InstallResult
from ouv.verdict import VerdictAggregator, VerdictState
from ouv.reporting import ReportGenerator
from ouv.history import HistoryStore
from ouv.dispatch_lock import DispatchLock
from ouv.learning import RulesetLearningEngine


class Orchestrator:
    """Central orchestrator managing full validation pipeline, circuit breakers, and autonomy boundaries."""

    def __init__(
        self,
        output_dir: Path,
        history_dir: Path,
        ruleset_path: Path,
        odoo_bin_path: str = "odoo-bin",
        odoo_config_path: Optional[str] = None,
        db_template_mgr: Optional[DBTemplateManager] = None,
        max_fix_attempts: int = 3
    ):
        self.output_dir = output_dir
        self.history_store = HistoryStore(history_dir)
        self.ruleset_path = ruleset_path
        self.static_engine = StaticAnalysisEngine(ruleset_path)
        self.installer = InstallExecutor(odoo_bin_path, odoo_config_path)
        self.db_template_mgr = db_template_mgr
        self.dispatch_lock = DispatchLock(self.history_store.db_path)
        self.learning_engine = RulesetLearningEngine(
            self.history_store.db_path,
            ruleset_path.parent / "pending_updates.yaml"
        )
        self.max_fix_attempts = max_fix_attempts

    def validate_module(
        self,
        module_info: ModuleInfo,
        agent_id: str = "manual",
        dry_run: bool = False,
        keep_db_on_failure: bool = False
    ) -> VerdictState:
        run_id = f"run_{uuid.uuid4().hex[:8]}"
        now_ts = datetime.now(timezone.utc).isoformat()
        is_fixer_agent = agent_id != "manual" and agent_id != "ci"
        fix_agent_id = agent_id if is_fixer_agent else None

        # Git prerequisite check for fixer-agent mode
        commit_sha = self._get_git_commit_sha(module_info.path)
        if is_fixer_agent and not commit_sha:
            raise RuntimeError(f"Fixer-agent mode requires a git-managed repo. Repo not found at {module_info.path}")

        # Circuit breaker check (§4.10)
        if is_fixer_agent and self._is_triaged(module_info.name):
            print(f"[SKIP] Module {module_info.name} marked 'needs_human_triage'. Refusing fixer-agent dispatch.")
            return VerdictState.BLOCKED

        # Acquire dispatch lock if fixer agent (§4.11)
        if is_fixer_agent:
            acquired = self.dispatch_lock.acquire(module_info.name, agent_id)
            if not acquired:
                print(f"[LOCK REFUSED] Module {module_info.name} is currently locked by another fixer agent.")
                return VerdictState.BLOCKED

        try:
            prev_run_id = self.history_store.get_latest_run_id(module_info.name)
            fix_diff = self._get_git_diff(module_info.path) if commit_sha else None

            # 1. Manifest error / legacy check
            if module_info.error:
                verdict, reason = VerdictState.BLOCKED, module_info.error
                static_matches = []
                install_res = None
            else:
                # 2. Static Analysis Check
                static_matches = self.static_engine.analyze_module(module_info.path)
                verdict, reason = VerdictAggregator.aggregate(static_matches, None)

                # 3. Dynamic Install Check (if static clean and not dry run)
                install_res = None
                if verdict != VerdictState.FAILED and not dry_run and self.db_template_mgr:
                    scratch_db = None
                    try:
                        scratch_db = self.db_template_mgr.create_scratch_db()
                        install_res = self.installer.install_module(module_info.name, scratch_db)
                        verdict, reason = VerdictAggregator.aggregate(static_matches, install_res)
                    finally:
                        if scratch_db and not (keep_db_on_failure and verdict in (VerdictState.FAILED, VerdictState.ERRORED)):
                            self.db_template_mgr.drop_scratch_db(scratch_db)

            # Generate Report Folder Artifact
            report_folder = ReportGenerator.generate_report(
                output_dir=self.output_dir,
                module_name=module_info.name,
                verdict=verdict,
                reason=reason,
                timestamp=now_ts,
                ruleset_version="1.0.0",
                commit_sha=commit_sha,
                static_matches=static_matches,
                install_result=install_res
            )

            # Record Telemetry & Lineage
            fired_rule_ids = [m.rule_id for m in static_matches]
            install_error_sig = install_res.install_error_sig if install_res else None
            duration_ms = install_res.duration_ms if install_res else 0

            self.history_store.record_run(
                run_id=run_id,
                module_name=module_info.name,
                verdict=verdict.value,
                timestamp=now_ts,
                duration_ms=duration_ms,
                agent_id=agent_id,
                ruleset_version="1.0.0",
                report_path=str(report_folder),
                fired_rule_ids=fired_rule_ids,
                install_error_sig=install_error_sig,
                prev_run_id=prev_run_id,
                fix_agent_id=fix_agent_id,
                commit_sha=commit_sha,
                fix_diff=fix_diff,
                report_folder=report_folder
            )

            # Update Circuit Breaker table (§4.10)
            if is_fixer_agent:
                self._update_circuit_breaker(module_info.name, verdict, run_id)

            # Check Learning Engine trigger condition: prev == FAILED and curr == PASSED
            if prev_run_id and verdict == VerdictState.PASSED:
                prev_verdict = self._get_run_verdict(prev_run_id)
                if prev_verdict == VerdictState.FAILED.value:
                    self.learning_engine.on_fix_confirmed(
                        module_name=module_info.name,
                        prev_run_id=prev_run_id,
                        curr_run_id=run_id,
                        rules_resolved=fired_rule_ids,
                        error_fingerprint=install_error_sig,
                        ruleset_version_before="1.0.0"
                    )

            return verdict

        finally:
            if is_fixer_agent:
                self.dispatch_lock.release(module_info.name, agent_id)

    def _get_git_commit_sha(self, module_path: Path) -> Optional[str]:
        try:
            res = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=module_path,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True
            )
            return res.stdout.strip() if res.returncode == 0 else None
        except Exception:
            return None

    def _get_git_diff(self, module_path: Path) -> Optional[str]:
        try:
            res = subprocess.run(
                ["git", "diff", "HEAD~1", "--", "."],
                cwd=module_path,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True
            )
            return res.stdout if res.returncode == 0 else None
        except Exception:
            return None

    def _is_triaged(self, module_name: str) -> bool:
        with self.history_store._get_connection() as conn:
            cur = conn.execute("SELECT status FROM fix_attempts WHERE module_name = ?", (module_name,))
            row = cur.fetchone()
            return row["status"] == "needs_human_triage" if row else False

    def _update_circuit_breaker(self, module_name: str, verdict: VerdictState, run_id: str):
        with self.history_store._get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            cur = conn.execute("SELECT consecutive_failures FROM fix_attempts WHERE module_name = ?", (module_name,))
            row = cur.fetchone()
            failures = row["consecutive_failures"] if row else 0

            if verdict in (VerdictState.FAILED, VerdictState.ERRORED, VerdictState.BLOCKED):
                failures += 1
                status = "needs_human_triage" if (self.max_fix_attempts > 0 and failures >= self.max_fix_attempts) else "active"
            else:
                failures = 0
                status = "active"

            conn.execute("""
                INSERT INTO fix_attempts (module_name, consecutive_failures, last_attempt_run_id, status)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(module_name) DO UPDATE SET
                    consecutive_failures = excluded.consecutive_failures,
                    last_attempt_run_id = excluded.last_attempt_run_id,
                    status = excluded.status
            """, (module_name, failures, run_id, status))
            conn.commit()

    def _get_run_verdict(self, run_id: str) -> Optional[str]:
        with self.history_store._get_connection() as conn:
            cur = conn.execute("SELECT verdict FROM runs WHERE run_id = ?", (run_id,))
            row = cur.fetchone()
            return row["verdict"] if row else None
