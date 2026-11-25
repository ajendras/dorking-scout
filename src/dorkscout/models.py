"""Pydantic models for Dork Scout.

Defines data structures used across the application for categories,
dork patterns, discovery results, analytics, scheduled jobs and alerts.
"""

from datetime import datetime
from typing import Any, Dict, List, Optional, ForwardRef

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
    relevance_score: Optional[float] = None


class SearchHistory(BaseModel):
    query: str
    category: Optional[str] = None
    tech: Optional[str] = None
    term: Optional[str] = None
    result_count: int
    searched_at: str


class StoredDomain(BaseModel):
    domain: str
    first_seen: str
    last_seen: str
    seen_count: int
    techs: List[str]


class AlertConfig(BaseModel):
    alert_type: str
    target: str


class ScheduledJob(BaseModel):
    id: str
    name: str
    tech: Optional[str] = None
    term: Optional[str] = None
    interval_hours: int
    alert: AlertConfig
    last_run: Optional[str] = None
    last_alerted: Optional[str] = None


class AnalyticsSummary(BaseModel):
    """Analytics payload returned by /api/analytics.

    Supports optional applied_filters when date-range or tech filters
    are used. Powers the live-updating Chart.js dashboard.
    """
    total_domains: int
    tech_distribution: Dict[str, int]
    top_techs: List[Dict[str, Any]]
    last_updated: str
    applied_filters: Optional[Dict[str, Any]] = None


# Resolve forward reference for ScheduledJob.alert
ScheduledJob.model_rebuild()
