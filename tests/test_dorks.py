"""Tests for dork loading, validation, query building and database consistency.

These tests verify that the merged dork database (embedded + optional dorks.json)
is valid, that filtering by category or tech works, that models validate, and
that error cases are raised with clear messages. Imports use the installed
package name so the suite passes after `pip install -e .`.
"""

import pytest

from dorkscout.config import get_dork_db, validate_dork_database
from dorkscout.dorks import (
    build_query,
    get_dorks,
    get_tech_to_category,
    list_all_techs,
    load_dorks,
)
from dorkscout.models import Category, DorkPattern


def test_validate_dork_database_does_not_raise():
    db = get_dork_db()
    validate_dork_database(db)


def test_load_dorks_returns_expected_structure():
    db = load_dorks()
    assert isinstance(db, dict)
    assert "categories" in db
    assert "dorks" in db
    assert "tech_to_category" in db
    assert len(db["categories"]) == 3
    assert len(db["dorks"]) >= 7
    assert len(db["tech_to_category"]) >= 7


def test_get_dorks_by_tech():
    patterns = get_dorks(tech="Stripe")
    assert len(patterns) >= 1
    assert all(isinstance(p, DorkPattern) for p in patterns)
    assert any("stripe" in p.signature.lower() for p in patterns)
    assert all(p.engine in ("google", "bing") for p in patterns)


def test_get_dorks_by_category():
    patterns = get_dorks(category="payment_gateways")
    assert len(patterns) >= 4
    assert all(isinstance(p, DorkPattern) for p in patterns)
    techs = {p.signature for p in patterns}
    assert any("stripe" in t.lower() for t in techs)
    assert any("paypal" in t.lower() for t in techs)


def test_get_dorks_by_niches_category():
    patterns = get_dorks(category="niches")
    assert len(patterns) >= 4
    assert all(isinstance(p, DorkPattern) for p in patterns)


def test_get_dorks_returns_all_when_no_filter():
    patterns = get_dorks()
    assert len(patterns) >= 10


def test_build_query_with_term():
    dork = DorkPattern(
        signature='"powered by stripe" OR "checkout.stripe.com" -site:stripe.com',
        engine="google",
    )
    query = build_query(dork, "coffee")
    assert query == (
        '"powered by stripe" OR "checkout.stripe.com" -site:stripe.com "coffee"'
    )


def test_build_query_without_term():
    dork = DorkPattern(signature="inurl:shopify", engine="bing")
    query = build_query(dork, "")
    assert query == "inurl:shopify"
    query = build_query(dork, "   ")
    assert query == "inurl:shopify"


def test_list_all_techs():
    techs = list_all_techs()
    assert isinstance(techs, list)
    assert len(techs) >= 7
    assert "Stripe" in techs
    assert "Shopify" in techs
    assert "Books" in techs
    assert techs == sorted(techs)


def test_get_tech_to_category():
    mapping = get_tech_to_category()
    assert isinstance(mapping, dict)
    assert mapping["Stripe"] == "payment_gateways"
    assert mapping["Shopify"] == "platforms"
    assert mapping["Books"] == "niches"


def test_unknown_category_raises_value_error():
    with pytest.raises(ValueError, match="Unknown category"):
        get_dorks(category="nonexistent")


def test_unknown_tech_raises_value_error():
    with pytest.raises(ValueError, match="Unknown or empty tech"):
        get_dorks(tech="UnknownTech123")


def test_both_category_and_tech_raises_value_error():
    with pytest.raises(ValueError, match="Provide either category or tech"):
        get_dorks(category="niches", tech="Books")


def test_dorkpattern_model_and_string_repr():
    pattern = DorkPattern(
        signature="inurl:checkout intitle:store -amazon",
        engine="duckduckgo",
    )
    assert pattern.signature == "inurl:checkout intitle:store -amazon"
    assert pattern.engine == "duckduckgo"
    assert "DDG" in str(pattern)
    assert "inurl:checkout" in str(pattern)


def test_category_model_validation():
    cat = Category(
        id="test_cat",
        name="Test Category",
        icon="🧪",
        color="#6f8a5e",
        description="A category used only for unit tests.",
    )
    assert cat.id == "test_cat"
    assert cat.color == "#6f8a5e"


def test_validation_fails_on_bad_engine():
    bad_pattern = {"signature": "test", "engine": "invalid-engine"}
    db = get_dork_db().copy()
    db["dorks"]["BadTech"] = [bad_pattern]
    with pytest.raises(ValueError, match="Invalid engine"):
        validate_dork_database(db)
