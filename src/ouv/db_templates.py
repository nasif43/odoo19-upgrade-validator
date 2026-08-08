import uuid
from typing import Optional
import psycopg2
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT


class DBTemplateManager:
    """Manages creation and destruction of PostgreSQL scratch databases cloned from golden templates."""

    def __init__(self, db_host: str = "localhost", db_port: int = 5432, db_user: str = "postgres", db_password: str = "", golden_db: str = "golden_base"):
        self.db_host = db_host
        self.db_port = db_port
        self.db_user = db_user
        self.db_password = db_password
        self.golden_db = golden_db

    def _get_connection(self):
        conn = psycopg2.connect(
            dbname="postgres",
            user=self.db_user,
            password=self.db_password,
            host=self.db_host,
            port=self.db_port
        )
        conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        return conn

    def create_scratch_db(self, prefix: str = "scratch") -> str:
        """Clones a new database from the golden base template."""
        scratch_db_name = f"{prefix}_{uuid.uuid4().hex[:8]}"
        conn = self._get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(f'CREATE DATABASE "{scratch_db_name}" WITH TEMPLATE "{self.golden_db}";')
            return scratch_db_name
        finally:
            conn.close()

    def drop_scratch_db(self, db_name: str) -> None:
        """Drops a scratch database."""
        conn = self._get_connection()
        try:
            with conn.cursor() as cur:
                # Terminate any remaining connections to the scratch DB
                cur.execute(f"""
                    SELECT pg_terminate_backend(pg_stat_activity.pid)
                    FROM pg_stat_activity
                    WHERE pg_stat_activity.datname = %s
                      AND pid <> pg_backend_pid();
                """, (db_name,))
                cur.execute(f'DROP DATABASE IF EXISTS "{db_name}";')
        finally:
            conn.close()
