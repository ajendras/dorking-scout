"""Persistent SQLite storage for Dork Scout searches, domains and scheduled jobs.

Uses a single file (or :memory: for tests/bulk). Automatically creates schema on init.
Supports recording enriched results, querying history, unique domains, tech distribution
and job management for the AlertScheduler.
"""

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from .config import get_db_path
from .models import DiscoveryResult


class DiscoveryStore:
    """SQLite backed store with support for in-memory operation."""

    def __init__(self, db_path: Optional[Union[str, Path]] = None):
        if db_path is None:
            self.db_path = get_db_path()
        else:
            self.db_path = db_path
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        """Return a new connection with row_factory set for dict access."""
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        """Create required tables if they do not exist."""
        with self._get_connection() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS searches (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    query TEXT,
                    category TEXT,
                    tech TEXT,
                    term TEXT,
                    engine TEXT,
                    result_count INTEGER
                );
                CREATE TABLE IF NOT EXISTS domains (
                    domain TEXT PRIMARY KEY,
                    first_seen TEXT NOT NULL,
                    last_seen TEXT NOT NULL,
                    seen_count INTEGER DEFAULT 1,
                    tech TEXT,
                    title TEXT,
                    domain_age_days INTEGER,
                    ssl_issuer TEXT,
                    ssl_valid_from TEXT,
                    ssl_valid_to TEXT
                );
                CREATE TABLE IF NOT EXISTS scheduled_jobs (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    tech TEXT,
                    term TEXT,
                    category TEXT,
                    interval_hours INTEGER DEFAULT 24,
                    alert_type TEXT,
                    alert_target TEXT,
                    last_run TEXT,
                    last_alerted TEXT,
                    active INTEGER DEFAULT 1
                );
                CREATE INDEX IF NOT EXISTS idx_searches_ts ON searches(timestamp);
                CREATE INDEX IF NOT EXISTS idx_domains_seen ON domains(last_seen);
                """
            )
            conn.commit()

    def record_search(
        self,
        query: str,
        results: List[DiscoveryResult],
        category: Optional[str] = None,
        tech: Optional[str] = None,
        term: Optional[str] = None,
        engine: str = "multi",
    ) -> None:
        """Record search metadata and upsert the discovered domains with enrichment data."""
        ts = datetime.now(timezone.utc).isoformat()
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO searches 
                (timestamp, query, category, tech, term, engine, result_count)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (ts, query, category, tech, term, engine, len(results)),
            )

            for result in results:
                tech_str = json.dumps(getattr(result, "tech", []))
                conn.execute(
                    """
                    INSERT INTO domains (domain, first_seen, last_seen, seen_count, tech, 
                    title, domain_age_days, ssl_issuer, ssl_valid_from, ssl_valid_to)
                    VALUES (?, ?, ?, 1, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(domain) DO UPDATE SET
                        last_seen = ?,
                        seen_count = domains.seen_count + 1,
                        tech = COALESCE(?, domains.tech),
                        title = COALESCE(?, domains.title),
                        domain_age_days = COALESCE(?, domains.domain_age_days),
                        ssl_issuer = COALESCE(?, domains.ssl_issuer),
                        ssl_valid_from = COALESCE(?, domains.ssl_valid_from),
                        ssl_valid_to = COALESCE(?, domains.ssl_valid_to)
                    """,
                    (
                        result.domain,
                        ts,
                        ts,
                        tech_str,
                        getattr(result, "title", None),
                        getattr(result, "domain_age_days", None),
                        getattr(result, "ssl_issuer", None),
                        getattr(result, "ssl_valid_from", None),
                        getattr(result, "ssl_valid_to", None),
                        # for update
                        ts,
                        tech_str,
                        getattr(result, "title", None),
                        getattr(result, "domain_age_days", None),
                        getattr(result, "ssl_issuer", None),
                        getattr(result, "ssl_valid_from", None),
                        getattr(result, "ssl_valid_to", None),
                    ),
                )
            conn.commit()

    def get_search_history(self, limit: int = 20) -> List[Dict[str, Any]]:
        """Return latest searches."""
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM searches ORDER BY timestamp DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [dict(row) for row in rows]

    def get_total_unique_domains(self) -> int:
        """Count distinct persisted domains."""
        with self._get_connection() as conn:
            row = conn.execute("SELECT COUNT(*) as cnt FROM domains").fetchone()
            return int(row["cnt"]) if row else 0

    def get_unique_domains(
        self, limit: int = 50, tech: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Return unique domains, optionally filtered by tech (json match)."""
        with self._get_connection() as conn:
            if tech:
                sql = """
                    SELECT * FROM domains 
                    WHERE tech LIKE ? 
                    ORDER BY last_seen DESC LIMIT ?
                """
                rows = conn.execute(sql, (f'%"{tech}"%', limit)).fetchall()
            else:
                sql = "SELECT * FROM domains ORDER BY last_seen DESC LIMIT ?"
                rows = conn.execute(sql, (limit,)).fetchall()

            result = []
            for row in rows:
                d = dict(row)
                if isinstance(d.get("tech"), str):
                    try:
                        d["tech"] = json.loads(d["tech"])
                    except (json.JSONDecodeError, TypeError):
                        d["tech"] = []
                result.append(d)
            return result

    def get_tech_distribution(self) -> Dict[str, int]:
        """Return count of domains per technology (multi-tech domains counted for each)."""
        distribution: Dict[str, int] = {}
        with self._get_connection() as conn:
            rows = conn.execute("SELECT tech FROM domains WHERE tech IS NOT NULL").fetchall()
            for row in rows:
                try:
                    tech_list = json.loads(row["tech"])
                    for t in tech_list:
                        if isinstance(t, str):
                            distribution[t] = distribution.get(t, 0) + 1
                except (json.JSONDecodeError, TypeError, KeyError):
                    pass
        return distribution

    # Scheduled job support for AlertScheduler

    def get_scheduled_jobs(self) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM scheduled_jobs WHERE active = 1 ORDER BY name"
            ).fetchall()
            return [dict(row) for row in rows]

    def save_scheduled_job(self, job: Dict[str, Any]) -> str:
        """Persist job config. Returns the job id used."""
        job_id = job.get("id") or f"job_{int(datetime.now(timezone.utc).timestamp())}"
        alert = job.get("alert", {})
        alert_type = alert.get("alert_type") if isinstance(alert, dict) else job.get("alert_type")
        target = alert.get("target") if isinstance(alert, dict) else job.get("alert_target")
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO scheduled_jobs 
                (id, name, tech, term, category, interval_hours, alert_type, alert_target, last_run, last_alerted, active)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                """,
                (
                    job_id,
                    job.get("name", "Unnamed"),
                    job.get("tech"),
                    job.get("term"),
                    job.get("category"),
                    job.get("interval_hours", 24),
                    alert_type,
                    target,
                    job.get("last_run"),
                    job.get("last_alerted"),
                ),
            )
            conn.commit()
        return job_id

    def delete_scheduled_job(self, job_id: str) -> bool:
        with self._get_connection() as conn:
            cur = conn.execute(
                "UPDATE scheduled_jobs SET active = 0 WHERE id = ?", (job_id,)
            )
            conn.commit()
            return cur.rowcount > 0

    def update_job_last_run(self, job_id: str, alerted: bool = False) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._get_connection() as conn:
            if alerted:
                conn.execute(
                    """
                    UPDATE scheduled_jobs 
                    SET last_run = ?, last_alerted = ? 
                    WHERE id = ?
                    """,
                    (now, now, job_id),
                )
            else:
                conn.execute(
                    "UPDATE scheduled_jobs SET last_run = ? WHERE id = ?",
                    (now, job_id),
                )
            conn.commit()

    def get_fresh_domains(self, candidate_domains: List[str]) -> List[str]:
        """Filter to only domains never seen before in the store."""
        if not candidate_domains:
            return []
        with self._get_connection() as conn:
            placeholders = ",".join("?" for _ in candidate_domains)
            query = f"SELECT domain FROM domains WHERE domain IN ({placeholders})"
            existing = conn.execute(query, candidate_domains).fetchall()
            existing_set = {row["domain"] for row in existing}
        return [d for d in candidate_domains if d not in existing_set]


__all__ = ["DiscoveryStore"]
