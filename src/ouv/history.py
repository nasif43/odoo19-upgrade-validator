import sqlite3
import json
import shutil
from pathlib import Path
from typing import Optional, List, Dict, Any


class HistoryStore:
    """Manages SQLite telemetry database and runs history filesystem store."""

    def __init__(self, history_dir: Path):
        self.history_dir = history_dir
        self.history_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.history_dir / "index.sqlite"
        self._init_db()

    def _get_connection(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self._get_connection() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS runs (
                    run_id            TEXT PRIMARY KEY,
                    module_name       TEXT NOT NULL,
                    verdict           TEXT NOT NULL,
                    timestamp         TEXT NOT NULL,
                    duration_ms       INTEGER,
                    agent_id          TEXT,
                    ruleset_version   TEXT,
                    report_path       TEXT,
                    fired_rule_ids    TEXT,
                    install_error_sig TEXT,
                    prev_run_id       TEXT,
                    fix_agent_id      TEXT,
                    commit_sha        TEXT,
                    fix_diff          TEXT
                );

                CREATE TABLE IF NOT EXISTS fix_attempts (
                    module_name          TEXT PRIMARY KEY,
                    consecutive_failures INTEGER NOT NULL DEFAULT 0,
                    last_attempt_run_id  TEXT,
                    status               TEXT NOT NULL DEFAULT 'active'
                );

                CREATE TABLE IF NOT EXISTS dispatch_locks (
                    module_name    TEXT PRIMARY KEY,
                    status         TEXT NOT NULL DEFAULT 'idle',
                    fix_agent_id   TEXT,
                    claim_ts       TEXT,
                    lock_timeout_s INTEGER DEFAULT 1800
                );

                CREATE TABLE IF NOT EXISTS learning_events (
                    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp              TEXT,
                    module_name            TEXT,
                    prev_run_id            TEXT,
                    curr_run_id            TEXT,
                    rules_resolved         TEXT,
                    error_fingerprint      TEXT,
                    confirmation_count     INTEGER,
                    review_priority        INTEGER,
                    ruleset_version_before TEXT,
                    ruleset_version_after  TEXT
                );
            """)

    def get_latest_run_id(self, module_name: str) -> Optional[str]:
        with self._get_connection() as conn:
            cur = conn.execute(
                "SELECT run_id FROM runs WHERE module_name = ? ORDER BY timestamp DESC LIMIT 1",
                (module_name,)
            )
            row = cur.fetchone()
            return row["run_id"] if row else None

    def record_run(
        self,
        run_id: str,
        module_name: str,
        verdict: str,
        timestamp: str,
        duration_ms: Optional[int],
        agent_id: str,
        ruleset_version: str,
        report_path: str,
        fired_rule_ids: List[str],
        install_error_sig: Optional[str],
        prev_run_id: Optional[str],
        fix_agent_id: Optional[str],
        commit_sha: Optional[str],
        fix_diff: Optional[str],
        report_folder: Path
    ):
        # 1. Archive folder to history/valid or history/invalid
        status_dir = "valid" if verdict == "passed" else "invalid"
        target_dir = self.history_dir / status_dir / module_name / run_id
        if target_dir.exists():
            shutil.rmtree(target_dir)
        shutil.copytree(report_folder, target_dir)

        # 2. Write telemetry to index.sqlite
        with self._get_connection() as conn:
            conn.execute("""
                INSERT INTO runs (
                    run_id, module_name, verdict, timestamp, duration_ms,
                    agent_id, ruleset_version, report_path, fired_rule_ids,
                    install_error_sig, prev_run_id, fix_agent_id, commit_sha, fix_diff
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                run_id, module_name, verdict, timestamp, duration_ms,
                agent_id, ruleset_version, str(target_dir), json.dumps(fired_rule_ids),
                install_error_sig, prev_run_id, fix_agent_id, commit_sha, fix_diff
            ))

    def get_lineage(self, module_name: str) -> List[Dict[str, Any]]:
        query = """
        WITH RECURSIVE lineage(run_id, module_name, verdict, commit_sha,
                               fix_agent_id, duration_ms, timestamp, prev_run_id) AS (
          SELECT run_id, module_name, verdict, commit_sha,
                 fix_agent_id, duration_ms, timestamp, prev_run_id
          FROM   runs
          WHERE  module_name = ? AND prev_run_id IS NULL
          UNION ALL
          SELECT r.run_id, r.module_name, r.verdict, r.commit_sha,
                 r.fix_agent_id, r.duration_ms, r.timestamp, r.prev_run_id
          FROM   runs r
          JOIN   lineage l ON r.prev_run_id = l.run_id
        )
        SELECT * FROM lineage ORDER BY timestamp ASC;
        """
        with self._get_connection() as conn:
            cur = conn.execute(query, (module_name,))
            return [dict(row) for row in cur.fetchall()]
