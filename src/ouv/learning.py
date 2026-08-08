import json
import sqlite3
from pathlib import Path
from typing import List, Dict, Any, Optional
import ruamel.yaml


class RulesetLearningEngine:
    """Manages learning queue & pending ruleset updates without ever writing to ruleset/*.yaml directly (§4.9 & Addendum v1.2)."""

    def __init__(self, db_path: Path, pending_updates_path: Path):
        self.db_path = db_path
        self.pending_updates_path = pending_updates_path

    def _get_connection(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def on_fix_confirmed(
        self,
        module_name: str,
        prev_run_id: str,
        curr_run_id: str,
        rules_resolved: List[str],
        error_fingerprint: Optional[str],
        ruleset_version_before: str
    ):
        """Enqueues passive learning event and updates pending_updates.yaml queue for human review."""
        now_ts = datetime.now(timezone.utc).isoformat() if 'datetime' in globals() else ""
        
        with self._get_connection() as conn:
            # Count how many total modules have confirmed fixes for these rules
            cur = conn.execute(
                "SELECT COUNT(DISTINCT module_name) as cnt FROM learning_events WHERE module_name != ?",
                (module_name,)
            )
            row = cur.fetchone()
            confirmation_count = (row["cnt"] if row else 0) + 1
            review_priority = confirmation_count  # Higher count = higher queue priority

            conn.execute("""
                INSERT INTO learning_events (
                    timestamp, module_name, prev_run_id, curr_run_id,
                    rules_resolved, error_fingerprint, confirmation_count,
                    review_priority, ruleset_version_before
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                now_ts, module_name, prev_run_id, curr_run_id,
                json.dumps(rules_resolved), error_fingerprint,
                confirmation_count, review_priority, ruleset_version_before
            ))

        # Update pending_updates.yaml safely for human review queue
        self._enqueue_pending_update(module_name, rules_resolved, error_fingerprint, confirmation_count)

    def _enqueue_pending_update(self, module_name: str, rules_resolved: List[str], error_fingerprint: Optional[str], count: int):
        yaml = ruamel.yaml.YAML()
        if self.pending_updates_path.exists():
            data = yaml.load(self.pending_updates_path.read_text(encoding="utf-8")) or {}
        else:
            data = {"pending_proposals": []}

        proposals = data.get("pending_proposals", [])
        proposals.append({
            "module_name": module_name,
            "rules_resolved": rules_resolved,
            "error_fingerprint": error_fingerprint,
            "confirmation_count": count,
            "priority_hint": count,
            "status": "pending_human_review"
        })
        data["pending_proposals"] = proposals

        with open(self.pending_updates_path, "w", encoding="utf-8") as f:
            yaml.dump(data, f)
