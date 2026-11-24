import sqlite3
import contextlib
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import get_db_path
from .models import DiscoveryResult, StoredDomain, SearchHistory, ScheduledJob


class DiscoveryStore:
    """Persistent SQLite store for domains, search history and scheduled jobs.

    Supports :memory: for tests by keeping a single persistent connection.
    All tables are created with CREATE IF NOT EXISTS.
    """

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or str(get_db_path())
        self._memory_conn: Optional[sqlite3.Connection] = None
        if self.db_path == ":memory:":
            self._memory_conn = sqlite3.connect(":memory:", check_same_thread=False)
            self._create_tables(self._memory_conn)
        else:
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
            self._create_tables(None)

    @contextlib.contextmanager
    def _get_connection(self):
        """Context manager that reuses the same connection for :memory:."""
        if self._memory_conn is not None:
            yield self._memory_conn
            return
        conn = sqlite3.connect(self.db_path)
        try:
            self._create_tables(conn)
            yield conn
        finally:
            conn.close()

    def _create_tables(self, conn: Optional[sqlite3.Connection] = None) -> None:
        """Create all required tables (idempotent)."""
        if conn is None:
            with self._get_connection() as c:
                self._create_tables(c)
            return

        conn.executescript("""
            CREATE TABLE IF NOT EXISTS domains (
                domain TEXT PRIMARY KEY,
                first_seen TEXT,
                last_seen TEXT,
                seen_count INTEGER DEFAULT 1,
                tech TEXT,
                title TEXT,
                domain_age_days INTEGER,
                ssl_issuer TEXT,
                ssl_valid_to TEXT
            );
            CREATE TABLE IF NOT EXISTS searches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                query TEXT,
                category TEXT,
                tech TEXT,
                term TEXT,
                results_count INTEGER,
                engine TEXT,
                timestamp TEXT
            );
            CREATE TABLE IF NOT EXISTS scheduled_jobs (
                id TEXT PRIMARY KEY,
                name TEXT,
                tech TEXT,
                term TEXT,
                interval_hours INTEGER,
                alert_type TEXT,
                alert_target TEXT,
                last_run TEXT,
                last_alerted TEXT
            );
        """)
        conn.commit()

    def get_total_unique_domains(self) -> int:
        """Count distinct persisted domains."""
        with self._get_connection() as conn:
            row = conn.execute("SELECT COUNT(*) as cnt FROM domains").fetchone()
            return row[0] if row else 0

    def record_search(
        self,
        query: str,
        results: List[DiscoveryResult],
        category: Optional[str] = None,
        tech: Optional[str] = None,
        term: str = "",
        engine: str = "multi",
    ) -> None:
        """Record a search and persist fresh domains."""
        timestamp = datetime.now().isoformat()
        with self._get_connection() as conn:
            conn.execute(
                "INSERT INTO searches (query, category, tech, term, results_count, engine, timestamp) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (query, category, tech, term, len(results), engine, timestamp),
            )
            for r in results:
                conn.execute(
                    """
                    INSERT INTO domains (domain, first_seen, last_seen, seen_count, tech, title, domain_age_days, ssl_issuer, ssl_valid_to)
                    VALUES (?, ?, ?, 1, ?, ?, ?, ?, ?)
                    ON CONFLICT(domain) DO UPDATE SET
                        last_seen = excluded.last_seen,
                        seen_count = seen_count + 1,
                        tech = COALESCE(excluded.tech, tech)
                    """,
                    (
                        r.domain,
                        timestamp,
                        timestamp,
                        ",".join(r.tech),
                        r.title or "",
                        r.domain_age_days,
                        r.ssl_issuer or "",
                        r.ssl_valid_to or "",
                    ),
                )
            conn.commit()

    def get_search_history(self, limit: int = 20) -> List[Dict[str, Any]]:
        """Return recent searches."""
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM searches ORDER BY timestamp DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [dict(zip([c[0] for c in conn.description], row)) for row in rows]

    def get_unique_domains(
        self, limit: int = 50, tech: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Return persisted unique domains, optionally filtered by tech."""
        with self._get_connection() as conn:
            if tech:
                rows = conn.execute(
                    "SELECT * FROM domains WHERE tech LIKE ? ORDER BY first_seen DESC LIMIT ?",
                    (f"%{tech}%", limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM domains ORDER BY first_seen DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            return [dict(zip([c[0] for c in conn.description], row)) for row in rows]

    def get_tech_distribution(self) -> Dict[str, int]:
        """Aggregate tech counts across all domains."""
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT tech FROM domains"
            ).fetchall()
            distribution: Dict[str, int] = {}
            for (tech_str,) in rows:
                if tech_str:
                    for t in tech_str.split(","):
                        t = t.strip()
                        if t:
                            distribution[t] = distribution.get(t, 0) + 1
            return distribution

    def add_scheduled_job(self, job: ScheduledJob) -> None:
        """Persist a new scheduled monitoring job."""
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO scheduled_jobs
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
                    None,
                    None,
                ),
            )
            conn.commit()

    def get_scheduled_jobs(self) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            rows = conn.execute("SELECT * FROM scheduled_jobs").fetchall()
            return [dict(zip([c[0] for c in conn.description], row)) for row in rows]

    def delete_scheduled_job(self, job_id: str) -> bool:
        with self._get_connection() as conn:
            cur = conn.execute("DELETE FROM scheduled_jobs WHERE id = ?", (job_id,))
            conn.commit()
            return cur.rowcount > 0


def is_noise_domain(domain: str) -> bool:
    """Return True if domain is a known noisy marketplace, blog platform or brand."""
    noise = {
        "amazon.com", "ebay.com", "etsy.com", "alibaba.com", "shopify.com",
        "wordpress.com", "blogspot.com", "github.com", "youtube.com",
        "facebook.com", "twitter.com", "linkedin.com", "pinterest.com",
        "wix.com", "squarespace.com", "bigcartel.com",
    }
    d = domain.lower().strip()
    if any(n in d for n in noise):
        return True
    if d.endswith((".blogspot.com", ".wordpress.com", ".wixsite.com")):
        return True
    return False
