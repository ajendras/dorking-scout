"""Persistent SQLite store for Dork Scout.

Handles search history, unique domains with tech and metadata, scheduled
jobs and analytics aggregations. Supports in-memory mode for tests.
All writes are committed immediately. Tech lists are stored as JSON.
"""

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import get_db_path
from .models import (
    DiscoveryResult,
    ScheduledJob,
)


class DiscoveryStore:
    """SQLite backed store with tables for searches, domains and scheduled_jobs."""

    def __init__(self, db_path: Optional[Path] = None):
        if db_path is None:
            self.db_path = get_db_path()
        else:
            self.db_path = db_path
        self.conn = sqlite3.connect(
            str(self.db_path), check_same_thread=False, isolation_level=None
        )
        self.conn.row_factory = sqlite3.Row
        self._create_tables()

    def _create_tables(self):
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS searches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                query TEXT NOT NULL,
                results_count INTEGER NOT NULL,
                timestamp TEXT NOT NULL,
                category TEXT,
                tech TEXT,
                term TEXT,
                engine TEXT
            );
            CREATE TABLE IF NOT EXISTS domains (
                domain TEXT PRIMARY KEY,
                tech TEXT NOT NULL, -- JSON array
                first_seen TEXT NOT NULL,
                last_seen TEXT NOT NULL,
                seen_count INTEGER DEFAULT 1,
                relevance_score REAL
            );
            CREATE TABLE IF NOT EXISTS scheduled_jobs (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                tech TEXT NOT NULL,
                term TEXT NOT NULL,
                interval_hours INTEGER NOT NULL,
                alert_type TEXT NOT NULL,
                alert_target TEXT NOT NULL,
                last_run TEXT,
                last_alerted TEXT
            );
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
        """Record a search and its results, updating the domains table."""
        count = len(results)
        ts = datetime.now().isoformat()
        self.conn.execute(
            """
            INSERT INTO searches 
            (query, results_count, timestamp, category, tech, term, engine)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (query, count, ts, category, tech, term, engine),
        )
        self.conn.commit()
        for result in results:
            score = getattr(result, "relevance_score", None)
            self.record_domain(result.domain, result.tech, score)

    def record_domain(
        self, domain: str, tech: List[str], relevance_score: Optional[float] = None
    ) -> None:
        """Insert or update a domain with tech list (as JSON) and score."""
        now = datetime.now().isoformat()
        tech_json = json.dumps(sorted(set(tech)))  # dedup techs
        cur = self.conn.execute(
            "SELECT seen_count FROM domains WHERE domain = ?", (domain,)
        )
        row = cur.fetchone()
        if row:
            new_count = int(row["seen_count"]) + 1
            self.conn.execute(
                """
                UPDATE domains 
                SET last_seen = ?, seen_count = ?, tech = ?, relevance_score = ?
                WHERE domain = ?
                """,
                (now, new_count, tech_json, relevance_score, domain),
            )
        else:
            self.conn.execute(
                """
                INSERT INTO domains 
                (domain, tech, first_seen, last_seen, seen_count, relevance_score)
                VALUES (?, ?, ?, ?, 1, ?)
                """,
                (domain, tech_json, now, now, relevance_score),
            )
        self.conn.commit()

    def get_search_history(self, limit: int = 20) -> List[Dict[str, Any]]:
        cur = self.conn.execute(
            "SELECT * FROM searches ORDER BY timestamp DESC LIMIT ?",
            (limit,),
        )
        return [dict(row) for row in cur.fetchall()]

    def get_unique_domains(
        self,
        limit: int = 50,
        tech: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Return unique domains, optionally filtered by tech or date."""
        query = "SELECT * FROM domains"
        params: List[Any] = []
        where = []
        if tech:
            where.append("tech LIKE ?")
            params.append(f"%{tech}%")
        if date_from:
            where.append("first_seen >= ?")
            params.append(date_from)
        if date_to:
            where.append("first_seen <= ?")
            params.append(date_to)
        if where:
            query += " WHERE " + " AND ".join(where)
        query += " ORDER BY first_seen DESC LIMIT ?"
        params.append(limit)
        cur = self.conn.execute(query, params)
        rows = []
        for row in cur.fetchall():
            d = dict(row)
            d["tech"] = json.loads(d["tech"])
            rows.append(d)
        return rows

    def get_total_unique_domains(
        self,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        tech_filter: Optional[List[str]] = None,
    ) -> int:
        query = "SELECT COUNT(DISTINCT domain) as cnt FROM domains"
        params: List[Any] = []
        where_clauses = []
        if date_from:
            where_clauses.append("first_seen >= ?")
            params.append(date_from)
        if date_to:
            where_clauses.append("first_seen <= ?")
            params.append(date_to)
        if tech_filter and len(tech_filter) > 0:
            tech_conds = ["tech LIKE ?" for _ in tech_filter]
            where_clauses.append("(" + " OR ".join(tech_conds) + ")")
            params.extend([f"%{t}%" for t in tech_filter])
        if where_clauses:
            query += " WHERE " + " AND ".join(where_clauses)
        cur = self.conn.execute(query, params)
        row = cur.fetchone()
        return row["cnt"] if row else 0

    def get_tech_distribution(
        self,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        tech_filter: Optional[List[str]] = None,
    ) -> Dict[str, int]:
        """Aggregate tech counts, respecting optional filters."""
        domains = self.get_unique_domains(
            limit=1000, date_from=date_from, date_to=date_to
        )
        dist: Dict[str, int] = {}
        for dom in domains:
            for t in dom["tech"]:
                if not tech_filter or t in tech_filter:
                    dist[t] = dist.get(t, 0) + 1
        return dist

    def add_scheduled_job(self, job: ScheduledJob) -> str:
        """Persist a new scheduled job."""
        self.conn.execute(
            """
            INSERT OR REPLACE INTO scheduled_jobs 
            (id, name, tech, term, interval_hours, alert_type, alert_target, last_run, last_alerted)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job.id,
                job.name,
                job.tech,
                job.term,
                job.interval_hours,
                job.alert.alert_type,
                job.alert.target,
                job.last_run.isoformat() if job.last_run else None,
                job.last_alerted.isoformat() if job.last_alerted else None,
            ),
        )
        self.conn.commit()
        return job.id

    def get_scheduled_jobs(self) -> List[Dict[str, Any]]:
        cur = self.conn.execute("SELECT * FROM scheduled_jobs ORDER BY name")
        rows = []
        for row in cur.fetchall():
            d = dict(row)
            d["alert"] = {
                "alert_type": d.pop("alert_type"),
                "target": d.pop("alert_target"),
            }
            rows.append(d)
        return rows

    def get_scheduled_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        cur = self.conn.execute(
            "SELECT * FROM scheduled_jobs WHERE id = ?", (job_id,)
        )
        row = cur.fetchone()
        if row is None:
            return None
        d = dict(row)
        d["alert"] = {
            "alert_type": d.pop("alert_type"),
            "target": d.pop("alert_target"),
        }
        return d

    def update_scheduled_job(self, job: ScheduledJob) -> None:
        """Update last_run and last_alerted for a job."""
        last_run = (
            job.last_run.isoformat() if isinstance(job.last_run, datetime) else job.last_run
        )
        last_alerted = (
            job.last_alerted.isoformat()
            if isinstance(job.last_alerted, datetime)
            else job.last_alerted
        )
        self.conn.execute(
            """
            UPDATE scheduled_jobs 
            SET last_run = ?, last_alerted = ?
            WHERE id = ?
            """,
            (last_run, last_alerted, job.id),
        )
        self.conn.commit()

    def delete_scheduled_job(self, job_id: str) -> bool:
        """Delete a scheduled job by ID. Returns True if deleted."""
        cur = self.conn.execute(
            "DELETE FROM scheduled_jobs WHERE id = ?", (job_id,)
        )
        self.conn.commit()
        return cur.rowcount > 0

    def close(self) -> None:
        self.conn.close()
