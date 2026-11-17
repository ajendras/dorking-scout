"""Tests for dork loading, filtering, and query construction.

Covers normal usage, category and tech based filtering, edge cases and
error handling as defined in dorks.py.
"""

import pytest

from dorkscout.dorks import (
    TECH_TO_CATEGORY,
    build_query,
    get_category_info,
    get_dorks,
    list_all_techs,
    load_dorks,
)
from dorkscout.models import DorkPattern


def test_load_dorks_returns_merged_database():
    """load_dorks() returns dict with categories and dorks sections."""
    db = load_dorks()
    assert isinstance(db, dict)
    assert "categories" in db
    assert "dorks" in db
    assert isinstance(db["categories"], dict)
    assert isinstance(db["dorks"], dict)
    assert "payment_gateways" in db["categories"]
    assert "Stripe" in db["dorks"]


def test_get_dorks_by_tech():
    """get_dorks(tech=...) returns only patterns for that tech."""
    patterns = get_dorks(tech="Stripe")
    assert len(patterns) == 2
    assert all(isinstance(p, DorkPattern) for p in patterns)
    assert all("stripe" in p.signature.lower() for p in patterns)
    assert patterns[0].engine in ("google", "bing")


def test_get_dorks_by_category():
    """get_dorks(category=...) aggregates patterns from all techs in category."""
    patterns = get_dorks(category="payment_gateways")
    assert len(patterns) >= 4
    signatures = " ".join(p.signature.lower() for p in patterns)
    assert "stripe" in signatures
    assert "paypal" in signatures


def test_get_dorks_niches_category():
    """Category filter for niches returns product vertical dorks."""
    patterns = get_dorks(category="niches")
    assert len(patterns) >= 3
    signatures = " ".join(p.signature.lower() for p in patterns)
    assert any(kw in signatures for kw in ("book", "shoes", "gift"))


def test_get_dorks_no_filter_returns_all():
    """Calling get_dorks() with no arguments returns every pattern."""
    patterns = get_dorks()
    assert len(patterns) >= 8


def test_build_query_with_term():
    """build_query appends term when provided (with quoting for phrases)."""
    dork = DorkPattern(
        signature='"powered by stripe" -site:stripe.com',
        engine="google",
    )
    query = build_query(dork, "vintage leather")
    assert query.startswith('"powered by stripe"')
    assert '"vintage leather"' in query
    assert "-site:stripe.com" in query


def test_build_query_no_term():
    """Term is ignored when empty or whitespace-only."""
    dork = DorkPattern(signature="test dork signature", engine="bing")
    query = build_query(dork, "   ")
    assert query == "test dork signature"
    query2 = build_query(dork)
    assert query2 == "test dork signature"


def test_build_query_respects_existing_quotes():
    """Multi-word terms are quoted only when necessary."""
    dork = DorkPattern(signature="base-dork", engine="google")
    query = build_query(dork, "already \"quoted phrase\"")
    assert 'already "quoted phrase"' in query


def test_list_all_techs():
    """list_all_techs returns sorted list of every tech with dorks."""
    techs = list_all_techs()
    expected = [
        "Books",
        "Gift Cards",
        "PayPal",
        "Shoes",
        "Shopify",
        "Square",
        "Stripe",
        "WooCommerce",
    ]
    for tech in expected:
        assert tech in techs
    assert techs == sorted(techs)


def test_get_category_info():
    """get_category_info returns metadata for a known category."""
    info = get_category_info("niches")
    assert info["name"] == "Product Niches"
    assert info["icon"] == "📦"
    assert info["color"] == "#c56a3b"


def test_get_dorks_validation_errors():
    """Appropriate errors are raised for bad inputs."""
    with pytest.raises(ValueError, match="either category or tech"):
        get_dorks(category="niches", tech="Stripe")

    with pytest.raises(ValueError, match="Unknown or empty tech"):
        get_dorks(tech="NonExistentTech123")

    with pytest.raises(ValueError, match="Unknown category"):
        get_dorks(category="invalid_cat")

    with pytest.raises(ValueError, match="Unknown category_id"):
        get_category_info("foo")


def test_tech_to_category_mapping_is_complete():
    """All techs defined in the dork database have a category mapping."""
    db = load_dorks()
    dork_techs = list(db.get("dorks", {}).keys())
    for tech in dork_techs:
        assert tech in TECH_TO_CATEGORY, f"Missing mapping for {tech}"
