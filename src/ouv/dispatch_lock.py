import sqlite3
from datetime import datetime, timezone
from typing import Optional


class DispatchLock:
    """SQLite-backed concurrency lock for parallel fixer agents (§4.11)."""

    def __init__(self, db_path):
        self.db_path = db_path

    def _get_connection(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def acquire(self, module_name: str, fix_agent_id: str, lock_timeout_s: int = 1800) -> bool:
        """Acquires lock for a module if idle or stale. Returns True if acquired."""
        now_ts = datetime.now(timezone.utc).isoformat()
        with self._get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            cur = conn.execute("SELECT * FROM dispatch_locks WHERE module_name = ?", (module_name,))
            row = cur.fetchone()

            if row:
                if row["status"] == "in_progress":
                    # Check for stale lock
                    try:
                        claim_dt = datetime.fromisoformat(row["claim_ts"])
                        elapsed = (datetime.now(timezone.utc) - claim_dt).total_seconds()
                        if elapsed < lock_timeout_s:
                            conn.commit()
                            return False  # Lock is active and not stale
                    except Exception:
                        pass  # Invalid timestamp, treat as stale

            # Acquire/Renew lock
            conn.execute("""
                INSERT INTO dispatch_locks (module_name, status, fix_agent_id, claim_ts, lock_timeout_s)
                VALUES (?, 'in_progress', ?, ?, ?)
                ON CONFLICT(module_name) DO UPDATE SET
                    status = 'in_progress',
                    fix_agent_id = excluded.fix_agent_id,
                    claim_ts = excluded.claim_ts,
                    lock_timeout_s = excluded.lock_timeout_s
            """, (module_name, fix_agent_id, now_ts, lock_timeout_s))
            conn.commit()
            return True

    def release(self, module_name: str, fix_agent_id: Optional[str] = None) -> None:
        """Releases the lock for a module."""
        with self._get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if fix_agent_id:
                conn.execute("""
                    UPDATE dispatch_locks SET status = 'idle'
                    WHERE module_name = ? AND fix_agent_id = ?
                """, (module_name, fix_agent_id))
            else:
                conn.execute("UPDATE dispatch_locks SET status = 'idle' WHERE module_name = ?", (module_name,))
            conn.commit()
