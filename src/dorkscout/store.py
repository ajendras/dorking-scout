"""Persistent SQLite layer for Dork Scout.

Stores every search, accumulates unique second-level domains (with
tech lists, first_seen, last_seen and seen_count) and manages
scheduled_jobs. The new get_tech_distribution() method powers the
analytics dashboard by counting occurrences of each technology
across all persisted domains.
"""

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import get_db_path
from .models import (
    DiscoveryResult,
    ScheduledJob,
    SearchHistory,
    StoredDomain,
)


class DiscoveryStore:
    """SQLite-backed store for searches, domains and scheduled jobs.

    All methods are synchronous because they are called from FastAPI
    endpoints or the APScheduler background thread. Uses an in-memory
    mode for tests.
    """

    def __init__(self, db_path: Optional[Path] = None, in_memory: bool = False):
        if in_memory:
            self.conn = sqlite3.connect(":memory:", check_same_thread=False)
        else:
            if db_path is None:
                db_path = get_db_path()
            db_path.parent.mkdir(parents=True, exist_ok=True)
            self.conn = sqlite3.connect(str(db_path), check_same_thread=False)

        self.conn.row_factory = sqlite3.Row
        self._init_db()

    def _init_db(self) -> None:
        """Create the three tables if they do not exist."""
        with self.conn:
            self.conn.executescript("""
                CREATE TABLE IF NOT EXISTS searches (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    query TEXT NOT NULL,
                    category TEXT,
                    tech TEXT,
                    term TEXT,
                    engine TEXT,
                    result_count INTEGER,
                    searched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS domains (
                    domain TEXT PRIMARY KEY,
                    techs TEXT,  -- JSON array of strings
                    first_seen TIMESTAMP,
                    last_seen TIMESTAMP,
                    seen_count INTEGER DEFAULT 1
                );

                CREATE TABLE IF NOT EXISTS scheduled_jobs (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    tech TEXT NOT NULL,
                    term TEXT,
                    interval_hours INTEGER NOT NULL,
                    alert_type TEXT NOT NULL,
                    alert_target TEXT NOT NULL,
                    last_run TIMESTAMP,
                    last_alerted TIMESTAMP
                );
            """)

    def record_search(
        self,
        query: str,
        results: List[DiscoveryResult],
        category: Optional[str] = None,
        tech: Optional[str] = None,
        term: str = "",
        engine: str = "multi",
    ) -> None:
        """Record a search and update the unique domains table.

        Tech lists are stored as JSON. Existing domains have their
        last_seen timestamp and seen_count incremented.
        """
        now = datetime.now(timezone.utc).isoformat()

        with self.conn:
            self.conn.execute(
                """
                INSERT INTO searches
                (query, category, tech, term, engine, result_count)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (query, category, tech, term, engine, len(results)),
            )

            for result in results:
                techs_json = json.dumps(result.tech)
                self.conn.execute(
                    """
                    INSERT INTO domains (domain, techs, first_seen, last_seen, seen_count)
                    VALUES (?, ?, ?, ?, 1)
                    ON CONFLICT(domain) DO UPDATE SET
                        last_seen = ?,
                        seen_count = seen_count + 1,
                        techs = ?
                    """,
                    (result.domain, techs_json, now, now, now, techs_json),
                )

    def get_search_history(self, limit: int = 20) -> List[Dict[str, Any]]:
        """Return the most recent searches."""
        cursor = self.conn.execute(
            """
            SELECT * FROM searches
            ORDER BY searched_at DESC LIMIT ?
            """,
            (limit,),
        )
        return [dict(row) for row in cursor.fetchall()]

    def get_total_unique_domains(self) -> int:
        """Return total number of distinct domains ever seen."""
        cursor = self.conn.execute("SELECT COUNT(*) as cnt FROM domains")
        row = cursor.fetchone()
        return row["cnt"] if row else 0

    def get_unique_domains(
        self, limit: int = 50, tech: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Return stored domains, optionally filtered by a technology."""
        if tech:
            cursor = self.conn.execute(
                """
                SELECT domain, techs, first_seen, last_seen, seen_count
                FROM domains
                WHERE techs LIKE ?
                ORDER BY first_seen DESC LIMIT ?
                """,
                (f'%"{tech}"%', limit),
            )
        else:
            cursor = self.conn.execute(
                """
                SELECT domain, techs, first_seen, last_seen, seen_count
                FROM domains
                ORDER BY first_seen DESC LIMIT ?
                """,
                (limit,),
            )

        rows = []
        for row in cursor.fetchall():
            d = dict(row)
            try:
                d["techs"] = json.loads(d["techs"]) if d.get("techs") else []
            except (json.JSONDecodeError, TypeError):
                d["techs"] = []
            rows.append(d)
        return rows

    def get_tech_distribution(self) -> Dict[str, int]:
        """Return a count of how many domains mention each technology.

        Used by the analytics dashboard. A domain that matches multiple
        signatures contributes to each tech's count.
        """
        cursor = self.conn.execute("SELECT techs FROM domains")
        counts: Dict[str, int] = {}

        for row in cursor.fetchall():
            try:
                tech_list = json.loads(row["techs"]) if row["techs"] else []
                for tech in tech_list:
                    counts[tech] = counts.get(tech, 0) + 1
            except (json.JSONDecodeError, TypeError, KeyError):
                continue

        return counts

    def add_scheduled_job(self, job: ScheduledJob) -> str:
        """Persist a new monitoring job."""
        job_id = str(uuid.uuid4())
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO scheduled_jobs
                (id, name, tech, term, interval_hours, alert_type, alert_target)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    job.name,
                    job.tech,
                    job.term,
                    job.interval_hours,
                    job.alert_type,
                    job.alert_target,
                ),
            )
        return job_id

    def get_scheduled_jobs(self) -> List[Dict[str, Any]]:
        """Return all persisted monitoring jobs."""
        cursor = self.conn.execute("SELECT * FROM scheduled_jobs")
        return [dict(row) for row in cursor.fetchall()]

    def delete_scheduled_job(self, job_id: str) -> bool:
        """Remove a job by UUID."""
        with self.conn:
            cursor = self.conn.execute(
                "DELETE FROM scheduled_jobs WHERE id = ?", (job_id,)
            )
            return cursor.rowcount > 0

    def close(self) -> None:
        """Close the underlying SQLite connection."""
        self.conn.close()
