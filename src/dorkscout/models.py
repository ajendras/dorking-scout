"""Pydantic models used throughout Dork Scout.

- Category, DorkPattern and DiscoveryResult describe the curated database,
  search queries and enriched results.
- New models AlertConfig and ScheduledJob power the background monitoring
  and alerting feature (email or webhook on fresh domains).
"""

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class Category(BaseModel):
    """Metadata for a dork category shown in the UI."""

    id: str
    name: str
    icon: str
    color: str
    description: str


class DorkPattern(BaseModel):
    """A single search-engine pattern belonging to a technology."""

    signature: str
    engine: str = "google"


class DiscoveryResult(BaseModel):
    """A discovered domain together with detected technologies and
    optional enrichment metadata from free public APIs.
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


class AlertConfig(BaseModel):
    """Configuration for a single alert delivery method."""

    alert_type: Literal["email", "webhook"] = "webhook"
    target: str


class ScheduledJob(BaseModel):
    """A recurring monitoring job persisted in the SQLite store.

    The scheduler executes the corresponding dork search on the given
    interval, identifies domains never seen before, enriches them and
    fires the configured alert.
    """

    id: Optional[str] = Field(default=None, alias="id")
    name: str
    category: Optional[str] = None
    tech: Optional[str] = None
    term: str = ""
    interval_hours: int = Field(24, ge=1, le=168)
    alert: AlertConfig
    enabled: bool = True
    last_run: Optional[datetime] = None
    last_alerted: Optional[datetime] = None
