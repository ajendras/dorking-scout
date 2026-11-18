"""Pydantic models for Dork Scout data structures.

Defines strongly-typed models for categories, dork patterns and discovered
websites. All models include input validation with clear error messages.
"""

from typing import List

from pydantic import BaseModel, ConfigDict, Field


class Category(BaseModel):
    """A curated category of dorks (payment gateways, platforms, niches).

    Matches the structure defined in config.DEFAULT_DORK_DB["categories"].
    """

    id: str = Field(..., min_length=3, description="Machine-friendly category identifier")
    name: str = Field(..., min_length=3, description="Human-readable category name")
    icon: str = Field(..., min_length=1, description="Emoji or icon representing the category")
    color: str = Field(
        ...,
        pattern=r"^#[0-9a-fA-F]{6}$",
        description="Hex color used for UI accents (sand/olive/terracotta palette)",
    )
    description: str = Field(..., min_length=10, description="Short explanatory text for the category browser")


class DorkPattern(BaseModel):
    """A single tested search dork with its preferred engine.

    The signature contains the full Google-compatible dork string including
    exclusions for noise (marketplaces, corporate sites, etc.).
    """

    signature: str = Field(
        ...,
        min_length=8,
        description="Search engine dork string (operators, keywords, exclusions)",
    )
    engine: str = Field(
        default="google",
        pattern=r"^(google|bing|duckduckgo|yandex|baidu)$",
        description="Preferred search engine for this pattern",
    )

    def __str__(self) -> str:
        """Helpful string representation for logging and debugging.

        Uses conventional short codes (DDG for DuckDuckGo, BAI for Baidu,
        etc.) so that test_dorkpattern_model_and_string_repr continues to pass.
        """
        engine_map = {
            "google": "G",
            "bing": "B",
            "duckduckgo": "DDG",
            "baidu": "BAI",
            "yandex": "YAN",
        }
        engine_short = engine_map.get(self.engine, self.engine[:3].upper())
        sig = self.signature[:45]
        return f"{engine_short}:{sig}{'...' if len(self.signature) > 45 else ''}"


class DiscoveryResult(BaseModel):
    """A deduplicated domain discovered via dorks with lightweight fingerprinting.

    Used by the async runner, detector, export features and example scripts.
    """

    domain: str = Field(
        ...,
        min_length=4,
        pattern=r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)*\.([a-z]{2,})$",
        description="Domain (second-level or subdomain, lowercase, no protocol or path)",
    )
    tech: List[str] = Field(
        default_factory=list,
        description="Technologies/payment gateways detected via HTML signatures and headers",
    )
    title: str = Field(default="", description="Page title extracted during scan (if available)")
    url: str = Field(default="", description="Full URL where the domain was first observed")
    detected_at: str = Field(default="", description="ISO timestamp of discovery (optional)")

    def add_tech(self, tech_name: str) -> None:
        """Add a technology if not already present (case-insensitive)."""
        tech_lower = tech_name.lower()
        if not any(t.lower() == tech_lower for t in self.tech):
            self.tech.append(tech_name)

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "domain": "examplebooks.com",
                "tech": ["Shopify", "Stripe"],
                "title": "Example Books & Rare Editions",
                "url": "https://examplebooks.com",
            }
        }
    )


__all__ = ["Category", "DorkPattern", "DiscoveryResult"]
