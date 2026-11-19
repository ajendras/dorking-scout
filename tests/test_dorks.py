"""Tests for dork loading, query building, validation and extensions."""

import pytest

from dorkscout.config import validate_dork_database
from dorkscout.dorks import (
    build_query,
    get_dorks,
    get_tech_to_category,
    list_all_techs,
    load_dorks,
)
from dorkscout.models import DorkPattern


def test_load_dorks_returns_valid_db():
    db = load_dorks()
    assert isinstance(db, dict)
    assert "categories" in db
    assert "dorks" in db
    assert "tech_to_category" in db
    assert len(db["dorks"]) > 5


def test_get_dorks_by_tech():
    dorks = get_dorks(tech="Shopify")
    assert len(dorks) >= 1
    assert isinstance(dorks[0], DorkPattern)
    assert dorks[0].engine in ["google", "duckduckgo"]


def test_get_dorks_by_category():
    dorks = get_dorks(category="niches")
    assert len(dorks) >= 3
    assert any("bookstore" in d.signature.lower() for d in dorks)


def test_build_query_adds_quoted_term():
    pattern = DorkPattern(
        signature='"powered by shopify" OR ".myshopify.com"',
        engine="google",
    )
    query = build_query(pattern, "coffee")
    assert query.endswith('"coffee"')
    assert '"powered by shopify"' in query


def test_build_query_respects_already_quoted():
    pattern = DorkPattern(signature="test signature", engine="google")
    query = build_query(pattern, '"already quoted"')
    assert '"already quoted"' in query


def test_list_all_techs():
    techs = list_all_techs()
    assert "Stripe" in techs
    assert "Books" in techs


def test_get_tech_to_category():
    mapping = get_tech_to_category()
    assert mapping["Shopify"] == "platforms"
    assert mapping["Books"] == "niches"


def test_validation_fails_on_bad_engine():
    bad_db = {
        "categories": {
            "payment_gateways": {
                "id": "payment_gateways",
                "name": "Payment Gateways",
                "icon": "💳",
                "color": "#c56a3b",
                "description": "Discover stores using specific payment processors.",
            }
        },
        "dorks": {
            "BadTech": [
                {
                    "signature": "this is a valid length signature for dork test",
                    "engine": "invalid-engine",
                }
            ]
        },
        "tech_to_category": {"Stripe": "payment_gateways"},
    }
    with pytest.raises(ValueError, match="Invalid engine"):
        validate_dork_database(bad_db)


def test_validation_fails_on_unknown_tech():
    bad_db = {
        "categories": {
            "payment_gateways": {
                "id": "payment_gateways",
                "name": "Payment Gateways",
                "icon": "💳",
                "color": "#c56a3b",
                "description": "Discover stores using specific payment processors.",
            }
        },
        "dorks": {
            "BadTech": [
                {
                    "signature": "valid signature that is long enough to pass min_length",
                    "engine": "google",
                }
            ]
        },
        "tech_to_category": {"Stripe": "payment_gateways"},
    }
    with pytest.raises(
        ValueError, match="not present in tech_to_category mapping"
    ):
        validate_dork_database(bad_db)


def test_validation_pydantic_signature_too_short():
    """Additional test for the pydantic min_length (covers original failing case)."""
    bad_db = {
        "categories": {
            "payment_gateways": {
                "id": "payment_gateways",
                "name": "Payment Gateways",
                "icon": "💳",
                "color": "#c56a3b",
                "description": "Test desc that is long enough.",
            }
        },
        "dorks": {
            "Stripe": [{"signature": "short", "engine": "google"}]
        },
        "tech_to_category": {"Stripe": "payment_gateways"},
    }
    with pytest.raises(ValueError, match="String should have at least 8 characters"):
        validate_dork_database(bad_db)
