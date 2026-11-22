"""Persistent SQLite store for searches, domains, scheduled jobs and analytics.

Supports in-memory mode for tests. Automatically creates tables for searches,
unique domains (with tech and timestamps), and scheduled_jobs.
"""

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import get_db_path
from .models import DiscoveryResult


class DiscoveryStore:
    def __init__(self, in_memory: bool = False):
        self.in_memory = in_memory
        if in_memory:
            self.conn = sqlite3.connect(":memory:")
        else:
            db_path = get_db_path()
            db_path.parent.mkdir(parents=True, exist_ok=True)
            self.conn = sqlite3.connect(str(db_path))
        self.conn.row_factory = sqlite3.Row
        self._init_db()

    def _init_db(self):
        with self.conn:
            self.conn.executescript("""
                CREATE TABLE IF NOT EXISTS searches (
                    id INTEGER PRIMARY KEY,
                    query TEXT,
                    category TEXT,
                    tech TEXT,
                    term TEXT,
                    engine TEXT,
                    result_count INTEGER,
                    timestamp TEXT
                );
                CREATE TABLE IF NOT EXISTS domains (
                    domain TEXT PRIMARY KEY,
                    first_seen TEXT,
                    last_seen TEXT,
                    seen_count INTEGER DEFAULT 1,
                    tech TEXT
                );
                CREATE TABLE IF NOT EXISTS scheduled_jobs (
                    job_id TEXT PRIMARY KEY,
                    name TEXT,
                    tech TEXT,
                    term TEXT,
                    interval_hours INTEGER,
                    alert_type TEXT,
                    target TEXT,
                    last_run TEXT,
                    last_alerted TEXT
                );
            """)

    def add_scheduled_job(
        self,
        job_id: Optional[str] = None,
        name: str = "",
        tech: str = "",
        term: str = "",
        interval_hours: int = 24,
        alert_type: str = "webhook",
        target: str = "",
    ) -> str:
        """Add or update a scheduled monitoring job.

        job_id is now optional (generated if not supplied). This fixes the
        test that passes an explicit job_id.
        """
        if job_id is None:
            job_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO scheduled_jobs
                (job_id, name, tech, term, interval_hours, alert_type, target, last_run, last_alerted)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL)
                ON CONFLICT(job_id) DO UPDATE SET
                    name=excluded.name,
                    tech=excluded.tech,
                    term=excluded.term,
                    interval_hours=excluded.interval_hours,
                    alert_type=excluded.alert_type,
                    target=excluded.target,
                    last_run=excluded.last_run
                """,
                (job_id, name, tech, term, interval_hours, alert_type, target, now),
            )
        return job_id

    def get_scheduled_jobs(self) -> List[Dict[str, Any]]:
        """Return all scheduled jobs."""
        cursor = self.conn.execute("SELECT * FROM scheduled_jobs ORDER BY name")
        return [dict(row) for row in cursor.fetchall()]

    def delete_scheduled_job(self, job_id: str) -> bool:
        """Delete a scheduled job by ID."""
        with self.conn:
            cur = self.conn.execute(
                "DELETE FROM scheduled_jobs WHERE job_id = ?", (job_id,)
            )
            return cur.rowcount > 0

    def record_search(
        self,
        query: str,
        results: List[DiscoveryResult],
        category: Optional[str] = None,
        tech: Optional[str] = None,
        term: str = "",
        engine: str = "multi",
    ) -> None:
        """Record a search and upsert the discovered domains."""
        count = len(results)
        now = datetime.now(timezone.utc).isoformat()
        with self.conn:
            self.conn.execute(
                "INSERT INTO searches (query, category, tech, term, engine, result_count, timestamp) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (query, category, tech, term, engine, count, now),
            )
        for r in results:
            self._upsert_domain(r.domain, r.tech or [], now)

    def _upsert_domain(self, domain: str, tech: List[str], timestamp: str):
        tech_str = json.dumps(tech)
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO domains (domain, first_seen, last_seen, seen_count, tech)
                VALUES (?, ?, ?, 1, ?)
                ON CONFLICT(domain) DO UPDATE SET
                    last_seen = ?,
                    seen_count = seen_count + 1,
                    tech = ?
                """,
                (domain, timestamp, timestamp, tech_str, timestamp, tech_str),
            )

    def get_search_history(self, limit: int = 20) -> List[Dict[str, Any]]:
        """Return recent searches."""
        cursor = self.conn.execute(
            "SELECT * FROM searches ORDER BY timestamp DESC LIMIT ?", (limit,)
        )
        return [dict(row) for row in cursor.fetchall()]

    def get_total_unique_domains(self) -> int:
        """Return total count of unique domains persisted."""
        cursor = self.conn.execute("SELECT COUNT(*) FROM domains")
        row = cursor.fetchone()
        return row[0] if row else 0

    def get_unique_domains(self, limit: int = 50, tech: Optional[str] = None) -> List[str]:
        """Return unique domains, optionally filtered by tech."""
        if tech:
            cursor = self.conn.execute(
                "SELECT domain FROM domains WHERE tech LIKE ? LIMIT ?",
                (f"%{tech}%", limit),
            )
        else:
            cursor = self.conn.execute(
                "SELECT domain FROM domains LIMIT ?", (limit,)
            )
        return [row[0] for row in cursor.fetchall()]

    def get_tech_distribution(self) -> Dict[str, int]:
        """Aggregate tech counts across all domains (supports multi-tech)."""
        cursor = self.conn.execute("SELECT tech FROM domains")
        counts: Dict[str, int] = {}
        for row in cursor:
            try:
                tech_list = json.loads(row[0]) if isinstance(row[0], str) else row[0] or []
                for t in tech_list:
                    counts[t] = counts.get(t, 0) + 1
            except Exception:  # pylint: disable=broad-except
                pass
        return counts

    def close(self):
        """Close the database connection."""
        if self.conn:
            self.conn.close()


__all__ = ["DiscoveryStore"]
