"""Pydantic models for Dork Scout.

Defines the data structures for categories, dork patterns, discovery results
(with relevance_score), persistent storage entities and analytics.
"""

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class Category(BaseModel):
    id: str
    name: str
    icon: str
    color: str
    description: str


class DorkPattern(BaseModel):
    signature: str
    engine: str = "google"


class DiscoveryResult(BaseModel):
    """Rich result combining domain, detected technologies, metadata and
    the new relevance_score (higher = more likely independent/small site).
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
    relevance_score: float = Field(default=50.0, ge=0.0, le=100.0)


class SearchHistory(BaseModel):
    query: str
    timestamp: str
    result_count: int
    category: Optional[str] = None
    tech: Optional[str] = None
    term: Optional[str] = None
    engine: Optional[str] = None


class StoredDomain(BaseModel):
    domain: str
    tech: List[str] = Field(default_factory=list)
    first_seen: str
    last_seen: str
    seen_count: int = 1
    relevance_score: Optional[float] = Field(default=None)


class ScheduledJob(BaseModel):
    id: str
    name: str
    tech: Optional[str] = None
    category: Optional[str] = None
    term: Optional[str] = None
    interval_hours: int = 24
    alert: Dict[str, Any]
    last_run: Optional[str] = None
    last_alerted: Optional[str] = None


class AlertConfig(BaseModel):
    alert_type: str  # "email" or "webhook"
    target: str


class AnalyticsSummary(BaseModel):
    total_domains: int
    tech_distribution: Dict[str, int]
    top_techs: List[Dict[str, Any]]
    last_updated: str


__all__ = [
    "Category",
    "DorkPattern",
    "DiscoveryResult",
    "SearchHistory",
    "StoredDomain",
    "ScheduledJob",
    "AlertConfig",
    "AnalyticsSummary",
]
