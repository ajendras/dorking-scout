"""Persistent SQLite store for search history and discovered domains.

DiscoveryStore provides methods to record searches, upsert domains with
first_seen / last_seen / seen_count tracking and tech accumulation. Supports
both file-based persistence (default dorkscout.db) and in-memory mode for
testing. Tables are created automatically on initialization.
"""

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from .config import get_db_path
from .models import DiscoveryResult


class DiscoveryStore:
    """SQLite backed store for searches and unique domains with history."""

    def __init__(self, db_path: Optional[Union[str, Path]] = None) -> None:
        """Initialize store, create tables. For ':memory:' keeps a live connection."""
        if db_path is None:
            self.db_path: Union[str, Path] = get_db_path()
        elif isinstance(db_path, str) and db_path == ":memory:":
            self.db_path = ":memory:"
        else:
            self.db_path = Path(db_path) if isinstance(db_path, str) else db_path

        self._conn: Optional[sqlite3.Connection] = None
        if self.db_path == ":memory:":
            self._conn = sqlite3.connect(":memory:", check_same_thread=False)
            self._init_tables()
        else:
            self._init_tables()

    def _init_tables(self) -> None:
        """Create schema if not exists. Uses live conn for memory, new conn for file."""
        if self.db_path == ":memory:" and self._conn is not None:
            conn = self._conn
            close = False
        else:
            conn = sqlite3.connect(str(self.db_path))
            close = True

        try:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS searches (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    query TEXT NOT NULL,
                    category TEXT,
                    tech TEXT,
                    term TEXT DEFAULT '',
                    timestamp TEXT NOT NULL,
                    results_count INTEGER DEFAULT 0,
                    engine TEXT DEFAULT 'multi'
                );

                CREATE TABLE IF NOT EXISTS domains (
                    domain TEXT PRIMARY KEY,
                    techs TEXT DEFAULT '[]',
                    first_seen TEXT NOT NULL,
                    last_seen TEXT NOT NULL,
                    seen_count INTEGER DEFAULT 1
                );
                """
            )
            conn.commit()
        finally:
            if close:
                conn.close()

    def record_search(
        self,
        query: str,
        results: List[DiscoveryResult],
        category: Optional[str] = None,
        tech: Optional[str] = None,
        term: str = "",
        engine: str = "multi",
    ) -> int:
        """Record a search execution and its resulting domains.

        Returns the generated search_id. Domains are upserted; first_seen
        is preserved on conflicts while last_seen, seen_count and techs
        are updated.
        """
        if not query or not results:
            return 0

        timestamp = datetime.now(timezone.utc).isoformat()

        if self.db_path == ":memory:" and self._conn is not None:
            conn = self._conn
            close_after = False
        else:
            conn = sqlite3.connect(str(self.db_path))
            close_after = True

        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO searches
                (query, category, tech, term, timestamp, results_count, engine)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (query, category, tech, term, timestamp, len(results), engine),
            )
            search_id = cursor.lastrowid or 0

            now = timestamp
            for result in results:
                domain = result.domain.lower()
                tech_list = sorted({t for t in (result.tech or [])})
                tech_str = json.dumps(tech_list)

                cursor.execute(
                    "SELECT techs, first_seen, seen_count FROM domains WHERE domain = ?",
                    (domain,),
                )
                row = cursor.fetchone()
                if row:
                    existing_techs: List[str] = json.loads(row[0]) if row[0] else []
                    merged = sorted(set(existing_techs + tech_list))
                    merged_str = json.dumps(merged)
                    new_count = int(row[2]) + 1
                    cursor.execute(
                        """
                        UPDATE domains
                        SET techs = ?, last_seen = ?, seen_count = ?
                        WHERE domain = ?
                        """,
                        (merged_str, now, new_count, domain),
                    )
                else:
                    cursor.execute(
                        """
                        INSERT INTO domains (domain, techs, first_seen, last_seen, seen_count)
                        VALUES (?, ?, ?, ?, 1)
                        """,
                        (domain, tech_str, now, now),
                    )

            conn.commit()
            return search_id
        finally:
            if close_after:
                conn.close()

    def get_search_history(self, limit: int = 20) -> List[Dict[str, Any]]:
        """Return recent searches as list of dicts."""
        if self.db_path == ":memory:" and self._conn is not None:
            conn = self._conn
            close_after = False
        else:
            conn = sqlite3.connect(str(self.db_path))
            close_after = True

        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT id, query, category, tech, term, timestamp,
                       results_count, engine
                FROM searches
                ORDER BY timestamp DESC
                LIMIT ?
                """,
                (limit,),
            )
            rows = cursor.fetchall()
            history = []
            for row in rows:
                history.append(
                    {
                        "id": row[0],
                        "query": row[1],
                        "category": row[2],
                        "tech": row[3],
                        "term": row[4],
                        "timestamp": row[5],
                        "results_count": row[6],
                        "engine": row[7],
                    }
                )
            return history
        finally:
            if close_after:
                conn.close()

    def get_total_unique_domains(self) -> int:
        """Return total number of unique domains stored."""
        if self.db_path == ":memory:" and self._conn is not None:
            conn = self._conn
            close_after = False
        else:
            conn = sqlite3.connect(str(self.db_path))
            close_after = True

        try:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM domains")
            row = cursor.fetchone()
            return row[0] if row else 0
        finally:
            if close_after:
                conn.close()

    def get_unique_domains(
        self, limit: int = 50, tech: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Return list of unique domains, optionally filtered by tech."""
        if self.db_path == ":memory:" and self._conn is not None:
            conn = self._conn
            close_after = False
        else:
            conn = sqlite3.connect(str(self.db_path))
            close_after = True

        try:
            cursor = conn.cursor()
            if tech:
                cursor.execute(
                    """
                    SELECT domain, techs, first_seen, last_seen, seen_count
                    FROM domains
                    """
                )
                rows = cursor.fetchall()
                result: List[Dict[str, Any]] = []
                t_lower = tech.lower()
                for row in rows[:limit]:
                    techs = json.loads(row[1]) if row[1] else []
                    if any(t_lower == t.lower() for t in techs):
                        result.append(
                            {
                                "domain": row[0],
                                "techs": techs,
                                "first_seen": row[2],
                                "last_seen": row[3],
                                "seen_count": row[4],
                            }
                        )
                return result
            else:
                cursor.execute(
                    """
                    SELECT domain, techs, first_seen, last_seen, seen_count
                    FROM domains
                    LIMIT ?
                    """,
                    (limit,),
                )
                rows = cursor.fetchall()
                result = []
                for row in rows:
                    result.append(
                        {
                            "domain": row[0],
                            "techs": json.loads(row[1]) if row[1] else [],
                            "first_seen": row[2],
                            "last_seen": row[3],
                            "seen_count": row[4],
                        }
                    )
                return result
        finally:
            if close_after:
                conn.close()

    def close(self) -> None:
        """Close the connection if it's the in-memory one."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None


__all__ = ["DiscoveryStore"]
