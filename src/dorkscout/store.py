"""Persistent SQLite store for discovered domains and search history.

Implements a lightweight, zero-dependency store using stdlib sqlite3.
Two tables track searches (with query metadata) and unique domains (with
first/last seen timestamps, detection count and tech list stored as JSON).
The store is attached to FastAPI app.state.store and used by /api/search,
/api/history and /api/domains. Supports ":memory:" for tests.
"""

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from .config import get_db_path
from .models import DiscoveryResult


class DiscoveryStore:
    """SQLite-backed persistent storage for search results and domain history.

    Automatically creates the database and tables on first use. Domains are
    deduplicated across runs; each new sighting increments a seen_count.
    Tech list is stored as a JSON array. Latest tech detection overwrites
    on subsequent sightings.
    """

    def __init__(self, db_path: Optional[Union[Path, str]] = None) -> None:
        """Initialise store with optional custom path (use ':memory:' for tests)."""
        if db_path is None:
            self.db_path: Union[Path, str] = get_db_path()
        elif isinstance(db_path, str) and db_path == ":memory:":
            self.db_path = ":memory:"
        else:
            self.db_path = Path(db_path) if isinstance(db_path, (str, Path)) else db_path

        self._init_db()

    def _init_db(self) -> None:
        """Create tables and indexes if they do not exist."""
        conn_path = ":memory:" if self.db_path == ":memory:" else str(self.db_path)
        with sqlite3.connect(conn_path) as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS searches (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    query TEXT NOT NULL,
                    category TEXT,
                    tech TEXT,
                    term TEXT,
                    timestamp TEXT NOT NULL,
                    results_count INTEGER DEFAULT 0,
                    engine TEXT DEFAULT 'multi'
                );
                CREATE TABLE IF NOT EXISTS discovered_domains (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    domain TEXT UNIQUE NOT NULL,
                    techs TEXT DEFAULT '[]',
                    first_seen TEXT NOT NULL,
                    last_seen TEXT NOT NULL,
                    seen_count INTEGER DEFAULT 1,
                    search_id INTEGER,
                    FOREIGN KEY(search_id) REFERENCES searches(id)
                );
                CREATE INDEX IF NOT EXISTS idx_searches_ts ON searches(timestamp DESC);
                CREATE INDEX IF NOT EXISTS idx_domains_domain ON discovered_domains(domain);
                CREATE INDEX IF NOT EXISTS idx_domains_lastseen ON discovered_domains(last_seen DESC);
            """)
            conn.commit()

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
        conn_path = ":memory:" if self.db_path == ":memory:" else str(self.db_path)

        with sqlite3.connect(conn_path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO searches
                (query, category, tech, term, timestamp, results_count, engine)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (query, category, tech, term, timestamp, len(results), engine),
            )
            search_id = cursor.lastrowid

            for result in results:
                techs_json = json.dumps(result.tech or [])
                cursor.execute(
                    """
                    INSERT INTO discovered_domains
                    (domain, techs, first_seen, last_seen, seen_count, search_id)
                    VALUES (?, ?, ?, ?, 1, ?)
                    ON CONFLICT(domain) DO UPDATE SET
                        techs = excluded.techs,
                        last_seen = excluded.last_seen,
                        seen_count = discovered_domains.seen_count + 1,
                        search_id = excluded.search_id
                    """,
                    (
                        result.domain,
                        techs_json,
                        timestamp,
                        timestamp,
                        search_id,
                    ),
                )

            conn.commit()
            return search_id or 0

    def get_search_history(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Return the most recent searches."""
        conn_path = ":memory:" if self.db_path == ":memory:" else str(self.db_path)
        with sqlite3.connect(conn_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                """
                SELECT id, query, category, tech, term, timestamp,
                       results_count, engine
                FROM searches
                ORDER BY timestamp DESC
                LIMIT ?
                """,
                (limit,),
            )
            return [dict(row) for row in cursor.fetchall()]

    def get_unique_domains(
        self, limit: int = 100, tech: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Return unique domains with their metadata.

        Tech filter performs a substring match on the stored JSON array.
        """
        conn_path = ":memory:" if self.db_path == ":memory:" else str(self.db_path)
        query = """
            SELECT domain, techs, first_seen, last_seen, seen_count
            FROM discovered_domains
        """
        params: List[Any] = []
        if tech:
            query += " WHERE techs LIKE ? "
            params.append(f'%"{tech}"%')
        query += " ORDER BY last_seen DESC LIMIT ?"
        params.append(limit)

        with sqlite3.connect(conn_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(query, params)
            rows = []
            for row in cursor.fetchall():
                row_dict = dict(row)
                try:
                    row_dict["techs"] = json.loads(row_dict.get("techs", "[]"))
                except (json.JSONDecodeError, TypeError):
                    row_dict["techs"] = []
                rows.append(row_dict)
            return rows

    def get_total_unique_domains(self) -> int:
        """Return total number of unique domains ever discovered."""
        conn_path = ":memory:" if self.db_path == ":memory:" else str(self.db_path)
        with sqlite3.connect(conn_path) as conn:
            cursor = conn.execute("SELECT COUNT(*) FROM discovered_domains")
            row = cursor.fetchone()
            return row[0] if row else 0

    def close(self) -> None:
        """No-op (connections are context-managed)."""


__all__ = ["DiscoveryStore"]
