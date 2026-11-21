"""Persistent SQLite storage for searches, unique domains and scheduled jobs.

DiscoveryStore manages three tables:
- searches: history of every query performed (manual or scheduled)
- domains: accumulated unique second-level domains with first/last seen,
  count, joined tech list and last_alerted timestamp
- scheduled_jobs: configuration for background monitoring jobs together
  with alert preferences, last_run and last_alerted.

All timestamps are ISO strings in UTC. The store is attached to
app.state.store on startup and used by API endpoints and the AlertScheduler.

Supports in_memory mode for tests.
"""

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import get_alert_config, get_db_path
from .models import DiscoveryResult, ScheduledJob


class DiscoveryStore:
    def __init__(self, db_path: Optional[Path] = None, in_memory: bool = False):
        if in_memory:
            self.conn = sqlite3.connect(":memory:", check_same_thread=False)
        else:
            path = db_path or get_db_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            self.conn = sqlite3.connect(str(path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._create_tables()

    def _create_tables(self):
        """Create all tables if they do not exist."""
        # searches table
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS searches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                query TEXT NOT NULL,
                category TEXT,
                tech TEXT,
                term TEXT,
                engine TEXT,
                result_count INTEGER,
                timestamp TEXT DEFAULT (datetime('now', 'utc'))
            )
            """
        )
        # domains table
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS domains (
                domain TEXT PRIMARY KEY,
                first_seen TEXT,
                last_seen TEXT,
                seen_count INTEGER DEFAULT 1,
                techs TEXT,
                last_alerted TEXT
            )
            """
        )
        # scheduled_jobs table
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS scheduled_jobs (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                category TEXT,
                tech TEXT,
                term TEXT DEFAULT "",
                interval_hours INTEGER DEFAULT 24,
                alert_type TEXT NOT NULL CHECK (alert_type IN ('email', 'webhook')),
                alert_target TEXT NOT NULL,
                enabled INTEGER DEFAULT 1,
                last_run TEXT,
                last_alerted TEXT
            )
            """
        )
        self.conn.commit()

    def record_search(
        self,
        query: str,
        results: List[DiscoveryResult],
        category: Optional[str] = None,
        tech: Optional[str] = None,
        term: str = "",
        engine: str = "multi",
    ) -> None:
        """Record a search and update (or insert) the unique domains ledger."""
        now = datetime.now(timezone.utc).isoformat()
        result_count = len(results)

        self.conn.execute(
            """
            INSERT INTO searches
            (query, category, tech, term, engine, result_count, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (query, category, tech, term, engine, result_count, now),
        )

        for result in results:
            techs_str = ";".join(sorted(set(result.tech))) if result.tech else ""
            self.conn.execute(
                """
                INSERT INTO domains (domain, first_seen, last_seen, seen_count, techs, last_alerted)
                VALUES (?, ?, ?, 1, ?, NULL)
                ON CONFLICT(domain) DO UPDATE SET
                    last_seen = ?,
                    seen_count = seen_count + 1,
                    techs = COALESCE(
                        CASE
                            WHEN techs IS NULL OR techs = '' THEN ?
                            WHEN instr(';' || techs || ';', ';' || ? || ';') = 0 THEN techs || ';' || ?
                            ELSE techs
                        END,
                        ?
                    )
                """,
                (result.domain, now, now, techs_str, now, techs_str, techs_str, techs_str, techs_str),
            )
        self.conn.commit()

    def get_search_history(self, limit: int = 20) -> List[Dict[str, Any]]:
        """Return recent searches ordered by timestamp desc."""
        cursor = self.conn.execute(
            """
            SELECT * FROM searches
            ORDER BY timestamp DESC LIMIT ?
            """,
            (limit,),
        )
        rows = cursor.fetchall()
        return [dict(row) for row in rows]

    def get_total_unique_domains(self) -> int:
        """Count distinct domains ever discovered."""
        cursor = self.conn.execute("SELECT COUNT(*) as cnt FROM domains")
        row = cursor.fetchone()
        return row["cnt"] if row else 0

    def get_unique_domains(
        self, limit: int = 50, tech: Optional[str] = None, since: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Return unique domains, optionally filtered by tech or first_seen since a timestamp."""
        query = "SELECT * FROM domains WHERE 1=1"
        params: List[Any] = []
        if tech:
            query += " AND techs LIKE ?"
            params.append(f"%{tech}%")
        if since:
            query += " AND first_seen > ?"
            params.append(since)
        query += " ORDER BY first_seen DESC LIMIT ?"
        params.append(limit)

        cursor = self.conn.execute(query, params)
        rows = cursor.fetchall()
        return [dict(row) for row in rows]

    def add_scheduled_job(self, job: ScheduledJob) -> str:
        """Persist a ScheduledJob (insert or update). Returns its id."""
        if not job.id:
            job.id = str(uuid.uuid4())

        data = {
            "id": job.id,
            "name": job.name,
            "category": job.category,
            "tech": job.tech,
            "term": job.term,
            "interval_hours": job.interval_hours,
            "alert_type": job.alert.alert_type,
            "alert_target": job.alert.target,
            "enabled": 1 if job.enabled else 0,
            "last_run": job.last_run.isoformat() if job.last_run else None,
            "last_alerted": job.last_alerted.isoformat() if job.last_alerted else None,
        }
        self.conn.execute(
            """
            INSERT INTO scheduled_jobs
            (id, name, category, tech, term, interval_hours,
             alert_type, alert_target, enabled, last_run, last_alerted)
            VALUES (:id, :name, :category, :tech, :term, :interval_hours,
                    :alert_type, :alert_target, :enabled, :last_run, :last_alerted)
            ON CONFLICT(id) DO UPDATE SET
                name = :name,
                category = :category,
                tech = :tech,
                term = :term,
                interval_hours = :interval_hours,
                alert_type = :alert_type,
                alert_target = :alert_target,
                enabled = :enabled,
                last_run = :last_run,
                last_alerted = :last_alerted
            """,
            data,
        )
        self.conn.commit()
        return job.id

    def get_scheduled_jobs(self) -> List[Dict[str, Any]]:
        """Return all scheduled jobs as dicts (ready for ScheduledJob.model_validate)."""
        cursor = self.conn.execute(
            "SELECT * FROM scheduled_jobs ORDER BY name"
        )
        rows = cursor.fetchall()
        return [dict(row) for row in rows]

    def update_job_last_run(self, job_id: str, timestamp: str) -> None:
        """Update last_run for a job."""
        self.conn.execute(
            "UPDATE scheduled_jobs SET last_run = ? WHERE id = ?",
            (timestamp, job_id),
        )
        self.conn.commit()

    def mark_domains_alerted(self, domains: List[str], timestamp: str) -> None:
        """Mark a list of domains as alerted (updates last_alerted)."""
        if not domains:
            return
        placeholders = ",".join(["?"] * len(domains))
        self.conn.execute(
            f"UPDATE domains SET last_alerted = ? WHERE domain IN ({placeholders})",
            [timestamp] + domains,
        )
        self.conn.commit()

    def delete_scheduled_job(self, job_id: str) -> bool:
        """Remove a scheduled job by id. Returns True if a row was deleted."""
        cursor = self.conn.execute(
            "DELETE FROM scheduled_jobs WHERE id = ?", (job_id,)
        )
        self.conn.commit()
        return cursor.rowcount > 0

    def close(self):
        self.conn.close()
