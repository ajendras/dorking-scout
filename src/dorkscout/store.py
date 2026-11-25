"""Persistent SQLite store for Dork Scout.

Handles search history, unique domain accumulation (with multi-tech support
via JSON column), scheduled jobs and analytics queries. The
get_tech_distribution() and get_total_unique_domains() methods now accept
optional date_from, date_to and tech_filter parameters to power the
live-filtered analytics dashboard.
"""

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import get_db_path
from .models import DiscoveryResult, ScheduledJob


class DiscoveryStore:
    """SQLite-backed store for searches, domains and scheduled jobs.

    All timestamps are stored as ISO strings. Techs are stored as JSON
    arrays to support multiple technologies per domain. Filtering for
    analytics uses SQLite JSON functions (json_each).
    """

    def __init__(self, db_path: Optional[Path] = None):
        if db_path is None:
            db_path = get_db_path()
        self.db_path = db_path
        self.conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._ensure_tables()

    def _ensure_tables(self):
        """Create required tables if they do not exist."""
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS searches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                query TEXT NOT NULL,
                category TEXT,
                tech TEXT,
                term TEXT,
                engine TEXT,
                result_count INTEGER,
                searched_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS domains (
                domain TEXT PRIMARY KEY,
                first_seen TEXT DEFAULT (datetime('now')),
                last_seen TEXT DEFAULT (datetime('now')),
                seen_count INTEGER DEFAULT 1,
                techs TEXT,  -- JSON array
                title TEXT,
                domain_age_days INTEGER,
                ssl_issuer TEXT,
                ssl_valid_to TEXT,
                relevance_score REAL
            );

            CREATE TABLE IF NOT EXISTS scheduled_jobs (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                tech TEXT,
                term TEXT,
                interval_hours INTEGER,
                alert_type TEXT,
                target TEXT,
                last_run TEXT,
                last_alerted TEXT
            );
        """)
        self.conn.commit()

    def record_search(
        self,
        query: str,
        results: List[DiscoveryResult],
        category: Optional[str] = None,
        tech: Optional[str] = None,
        term: Optional[str] = None,
        engine: str = "multi",
    ):
        """Record a search and upsert discovered domains with techs."""
        now = datetime.now(timezone.utc).isoformat()
        self.conn.execute(
            """
            INSERT INTO searches
            (query, category, tech, term, engine, result_count, searched_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (query, category, tech, term, engine, len(results), now),
        )

        for result in results:
            techs_json = json.dumps(result.tech or [])
            # Simple upsert that merges tech lists
            self.conn.execute(
                """
                INSERT INTO domains (
                    domain, first_seen, last_seen, seen_count, techs,
                    title, domain_age_days, ssl_issuer, ssl_valid_to,
                    relevance_score
                ) VALUES (?, ?, ?, 1, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(domain) DO UPDATE SET
                    last_seen = excluded.last_seen,
                    seen_count = domains.seen_count + 1,
                    techs = excluded.techs,
                    title = COALESCE(excluded.title, domains.title),
                    domain_age_days = COALESCE(excluded.domain_age_days, domains.domain_age_days),
                    ssl_issuer = COALESCE(excluded.ssl_issuer, domains.ssl_issuer),
                    ssl_valid_to = COALESCE(excluded.ssl_valid_to, domains.ssl_valid_to),
                    relevance_score = COALESCE(excluded.relevance_score, domains.relevance_score)
                """,
                (
                    result.domain,
                    result.detected_at or now,
                    now,
                    techs_json,
                    getattr(result, "title", None),
                    getattr(result, "domain_age_days", None),
                    getattr(result, "ssl_issuer", None),
                    getattr(result, "ssl_valid_to", None),
                    getattr(result, "relevance_score", None),
                ),
            )
        self.conn.commit()

    def get_search_history(self, limit: int = 20) -> List[Dict[str, Any]]:
        """Return the most recent searches."""
        cursor = self.conn.execute(
            "SELECT * FROM searches ORDER BY searched_at DESC LIMIT ?",
            (limit,),
        )
        return [dict(row) for row in cursor.fetchall()]

    def get_total_unique_domains(
        self,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        tech_filter: Optional[List[str]] = None,
    ) -> int:
        """Return count of unique domains, optionally filtered by date and tech."""
        if tech_filter:
            placeholders = ",".join("?" for _ in tech_filter)
            query = f"""
                SELECT COUNT(DISTINCT d.domain) as total
                FROM domains d, json_each(d.techs) j
                WHERE j.value IN ({placeholders})
            """
            params: List[Any] = list(tech_filter)
        else:
            query = "SELECT COUNT(DISTINCT domain) as total FROM domains WHERE 1=1"
            params = []

        if date_from:
            query += " AND first_seen >= ?"
            params.append(f"{date_from}T00:00:00")
        if date_to:
            query += " AND first_seen <= ?"
            params.append(f"{date_to}T23:59:59")

        cursor = self.conn.execute(query, params)
        row = cursor.fetchone()
        return row["total"] if row else 0

    def get_tech_distribution(
        self,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        tech_filter: Optional[List[str]] = None,
    ) -> Dict[str, int]:
        """Return tech -> count map, optionally filtered by date range and tech list.

        Uses json_each to count domains per technology. When a tech_filter
        is supplied only those technologies are returned.
        """
        query = """
            SELECT j.value as tech, COUNT(DISTINCT d.domain) as count
            FROM domains d, json_each(d.techs) j
            WHERE 1 = 1
        """
        params: List[Any] = []

        if date_from:
            query += " AND d.first_seen >= ?"
            params.append(f"{date_from}T00:00:00")
        if date_to:
            query += " AND d.first_seen <= ?"
            params.append(f"{date_to}T23:59:59")
        if tech_filter:
            placeholders = ",".join("?" for _ in tech_filter)
            query += f" AND j.value IN ({placeholders})"
            params.extend(tech_filter)

        query += " GROUP BY j.value ORDER BY count DESC"
        cursor = self.conn.execute(query, params)
        return {row["tech"]: row["count"] for row in cursor.fetchall()}

    def get_unique_domains(
        self, limit: int = 50, tech: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Return list of stored domains, optionally filtered by a single tech."""
        if tech:
            query = """
                SELECT * FROM domains, json_each(techs) j
                WHERE j.value = ? ORDER BY first_seen DESC LIMIT ?
            """
            params = [tech, limit]
        else:
            query = "SELECT * FROM domains ORDER BY first_seen DESC LIMIT ?"
            params = [limit]
        cursor = self.conn.execute(query, params)
        return [dict(row) for row in cursor.fetchall()]

    def add_scheduled_job(self, job: ScheduledJob):
        """Persist a new or updated scheduled monitoring job."""
        self.conn.execute(
            """
            INSERT OR REPLACE INTO scheduled_jobs
            (id, name, tech, term, interval_hours, alert_type, target, last_run, last_alerted)
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
                job.last_run,
                job.last_alerted,
            ),
        )
        self.conn.commit()

    def get_scheduled_jobs(self) -> List[Dict[str, Any]]:
        """Return all configured scheduled jobs."""
        cursor = self.conn.execute("SELECT * FROM scheduled_jobs")
        return [dict(row) for row in cursor.fetchall()]

    def delete_scheduled_job(self, job_id: str):
        """Remove a scheduled job by UUID."""
        self.conn.execute("DELETE FROM scheduled_jobs WHERE id = ?", (job_id,))
        self.conn.commit()

    def close(self):
        """Close the database connection."""
        self.conn.close()
