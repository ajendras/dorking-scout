"""Data models for Dork Scout using Pydantic v2.

These models provide validation, serialization and type hints for all major
entities: categories, dork patterns, discovery results, history, stored domains,
scheduled jobs and alerts.
"""

from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator


class Category(BaseModel):
    """Category metadata used in UI and validation."""

    id: str
    name: str
    icon: str
    color: str
    description: str


class DorkPattern(BaseModel):
    """Validated search dork pattern.

    Enforces minimum signature length to prevent ineffective or noisy dorks
    (covers the pydantic min_length test case).
    """

    signature: str = Field(..., min_length=8)
    engine: str


class DiscoveryResult(BaseModel):
    """Enriched discovery result with tech, metadata from free sources."""

    domain: str
    tech: List[str] = Field(default_factory=list)
    url: str
    detected_at: str
    title: Optional[str] = Field(default=None)
    domain_age_days: Optional[int] = Field(default=None)
    creation_date: Optional[str] = Field(default=None)
    ssl_issuer: Optional[str] = Field(default=None)
    ssl_valid_from: Optional[str] = Field(default=None)
    ssl_valid_to: Optional[str] = Field(default=None)

    @field_validator("tech", mode="before")
    @classmethod
    def normalize_tech(cls, v: Any) -> List[str]:
        """Ensure tech is always a list (defensive)."""
        if isinstance(v, str):
            return [v]
        if isinstance(v, list):
            return v
        return []


class SearchHistory(BaseModel):
    """Search history entry persisted in SQLite."""

    id: Optional[int] = None
    query: str
    results: int
    category: Optional[str] = None
    tech: Optional[str] = None
    term: Optional[str] = None
    engine: str = "multi"
    timestamp: datetime = Field(default_factory=datetime.now)


class StoredDomain(BaseModel):
    """Unique domain record with sighting counts and enrichment."""

    domain: str
    tech: List[str] = Field(default_factory=list)
    first_seen: datetime
    last_seen: datetime = Field(default_factory=datetime.now)
    seen_count: int = 1
    category: Optional[str] = None
    title: Optional[str] = None
    domain_age_days: Optional[int] = None
    ssl_issuer: Optional[str] = None


class ScheduledJob(BaseModel):
    """Background monitoring job persisted in the store."""

    id: str = Field(default_factory=lambda: str(uuid4()))
    name: str
    tech: str
    term: Optional[str] = None
    interval_hours: int = Field(6, ge=1)
    alert: Dict[str, Any]
    last_run: Optional[datetime] = None
    last_alerted: Optional[datetime] = None


class AlertConfig(BaseModel):
    """Alert delivery configuration (used by scheduler)."""

    alert_type: str = Field(..., pattern="^(email|webhook)$")
    target: str


__all__ = [
    "Category",
    "DorkPattern",
    "DiscoveryResult",
    "SearchHistory",
    "StoredDomain",
    "ScheduledJob",
    "AlertConfig",
]
