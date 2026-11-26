"""Pydantic models for Dork Scout.

Defines the data structures for dork patterns, discovery results, stored
entities, scheduled jobs, alerts and analytics. Used across the API, store,
scheduler and tests.
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import uuid

from pydantic import BaseModel, Field, ConfigDict


class Category(BaseModel):
    id: str
    name: str
    icon: str
    color: str
    description: str

    model_config = ConfigDict(from_attributes=True)


class DorkPattern(BaseModel):
    signature: str
    engine: str = "google"


class DiscoveryResult(BaseModel):
    domain: str
    tech: List[str] = Field(default_factory=list)
    url: Optional[str] = None
    detected_at: str
    title: Optional[str] = None
    domain_age_days: Optional[int] = None
    creation_date: Optional[str] = None
    ssl_issuer: Optional[str] = None
    ssl_valid_from: Optional[str] = None
    ssl_valid_to: Optional[str] = None
    relevance_score: Optional[float] = Field(None, ge=0, le=100)

    model_config = ConfigDict(from_attributes=True)


class SearchHistory(BaseModel):
    id: Optional[int] = None
    query: str
    results_count: int
    timestamp: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    category: Optional[str] = None
    tech: Optional[str] = None
    term: Optional[str] = None
    engine: Optional[str] = "multi"


class StoredDomain(BaseModel):
    domain: str
    tech: List[str]
    first_seen: str
    last_seen: str
    seen_count: int = 1
    relevance_score: Optional[float] = None


class AlertConfig(BaseModel):
    alert_type: str = Field(..., pattern="^(email|webhook)$")
    target: str


class ScheduledJob(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    tech: str
    term: str
    interval_hours: int = Field(6, gt=0)
    alert: AlertConfig
    last_run: Optional[datetime] = None
    last_alerted: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class AlertPayload(BaseModel):
    """Standardized payload for scheduler alerts (email and webhook)."""

    job_id: str
    job_name: str
    tech: str
    term: str
    timestamp: str
    new_domains_count: int
    domains: List[str]
    results: List[Dict[str, Any]]
    message: str


class AnalyticsSummary(BaseModel):
    total_domains: int
    tech_distribution: Dict[str, int]
    top_techs: List[Dict[str, Any]]
    last_updated: str
    applied_filters: Dict[str, Any]

    model_config = ConfigDict(from_attributes=True)
