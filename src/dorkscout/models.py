"""Pydantic models used throughout Dork Scout.

Defines the shape of categories, dork patterns, discovery results,
search history, stored domains, scheduled jobs, alert configuration
and the new analytics summary used by the dashboard.
"""

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class Category(BaseModel):
    """Metadata for a dork category shown in the UI."""

    id: str
    name: str
    icon: str
    color: str
    description: str


class DorkPattern(BaseModel):
    """A single search pattern tied to an engine."""

    signature: str
    engine: str = "google"


class DiscoveryResult(BaseModel):
    """Rich result object returned to the UI and stored persistently.

    Includes optional enrichment fields populated by enrich_discovery_results().
    """

    domain: str
    tech: List[str] = Field(default_factory=list)
    url: str
    detected_at: str
    title: Optional[str] = None
    domain_age_days: Optional[int] = None
    creation_date: Optional[str] = None
    ssl_issuer: Optional[str] = None
    ssl_valid_from: Optional[str] = None
    ssl_valid_to: Optional[str] = None


class SearchHistory(BaseModel):
    """Record of a performed search (persisted in SQLite)."""

    id: Optional[int] = None
    query: str
    category: Optional[str] = None
    tech: Optional[str] = None
    term: str = ""
    engine: str = "multi"
    result_count: int
    searched_at: Optional[str] = None


class StoredDomain(BaseModel):
    """A unique domain accumulated over time."""

    domain: str
    techs: List[str]
    first_seen: datetime
    last_seen: datetime
    seen_count: int = 1


class ScheduledJob(BaseModel):
    """Persistent monitoring job for the AlertScheduler."""

    id: str = Field(default_factory=lambda: str(datetime.now().timestamp()))
    name: str
    tech: str
    term: Optional[str] = None
    interval_hours: int = 24
    alert_type: str  # "email" or "webhook"
    alert_target: str
    last_run: Optional[datetime] = None
    last_alerted: Optional[datetime] = None


class AlertConfig(BaseModel):
    """Configuration for alert delivery (email or webhook)."""

    alert_type: str
    target: str
    headers: Optional[Dict[str, str]] = None


class AnalyticsSummary(BaseModel):
    """Response shape for the /api/analytics endpoint.

    Used by the dashboard to drive Chart.js visualisations.
    """

    total_domains: int
    tech_distribution: Dict[str, int]
    top_techs: List[Dict[str, Any]]
    last_updated: str
