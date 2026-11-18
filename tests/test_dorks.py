"""Tests for dork loading, filtering, query building and validation."""

import pytest

from src.dorkscout.config import get_dork_db, validate_dork_database
from src.dorkscout.dorks import (
    build_query,
    get_category_info,
    get_dorks,
    get_tech_to_category,
    list_all_techs,
    load_dorks,
)
from src.dorkscout.models import Category, DorkPattern


def test_load_dorks_returns_merged_db():
    db = load_dorks()
    assert isinstance(db, dict)
    assert "categories" in db
    assert "dorks" in db
    assert "tech_to_category" in db
    assert len(db["dorks"]) >= 7
    assert "Stripe" in db["tech_to_category"]


def test_get_dorks_by_tech():
    patterns = get_dorks(tech="Shopify")
    assert len(patterns) >= 1
    assert all(isinstance(p, DorkPattern) for p in patterns)
    assert any("shopify" in str(p).lower() for p in patterns)


def test_get_dorks_by_category():
    patterns = get_dorks(category="niches")
    assert len(patterns) >= 3
    signatures = [p.signature.lower() for p in patterns]
    assert any("book" in s for s in signatures)


def test_get_dorks_raises_on_unknown():
    with pytest.raises(ValueError, match="Unknown or empty tech"):
        get_dorks(tech="UnknownTech123")
    with pytest.raises(ValueError, match="Unknown category"):
        get_dorks(category="unknown")


def test_get_dorks_mutually_exclusive():
    with pytest.raises(ValueError, match="either category or tech"):
        get_dorks(category="niches", tech="Stripe")


def test_build_query_appends_term():
    d = DorkPattern(signature='inurl:checkout', engine="google")
    q = build_query(d, "vintage books")
    assert 'inurl:checkout' in q
    assert '"vintage books"' in q


def test_build_query_no_extra_quotes():
    d = DorkPattern(signature="test", engine="google")
    assert build_query(d, "singleword") == "test singleword"
    assert build_query(d, '"already quoted"') == 'test "already quoted"'


def test_list_all_techs():
    techs = list_all_techs()
    assert isinstance(techs, list)
    assert "Stripe" in techs
    assert "Gift Cards" in techs


def test_get_tech_to_category():
    mapping = get_tech_to_category()
    assert mapping["Books"] == "niches"
    assert mapping["Stripe"] == "payment_gateways"


def test_get_category_info():
    info = get_category_info("platforms")
    assert info["name"] == "Platforms & CMS"
    assert isinstance(info, dict)


def test_validate_dork_database_on_defaults():
    db = get_dork_db()
    validate_dork_database(db)  # should pass silently


def test_validate_dork_database_invalid_structure():
    with pytest.raises(ValueError, match="must be a non-empty dict"):
        validate_dork_database({"categories": {}})


def test_validate_dork_database_bad_pattern():
    bad_db = {
        "categories": get_dork_db()["categories"],
        "tech_to_category": {"TestTech": "niches"},
        "dorks": {
            "TestTech": [
                {"signature": "a", "engine": "google"},  # too short for model
            ]
        },
    }
    with pytest.raises(ValueError):
        validate_dork_database(bad_db)


def test_validate_dork_database_bad_engine():
    bad_db = {
        "categories": get_dork_db()["categories"],
        "tech_to_category": {"TestTech": "niches"},
        "dorks": {
            "TestTech": [
                {
                    "signature": "inurl:test -site:example.com",
                    "engine": "invalidengine",
                }
            ]
        },
    }
    with pytest.raises(ValueError, match="Invalid engine"):
        validate_dork_database(bad_db)


def test_validate_dork_database_unknown_category_in_map():
    bad_db = {
        "categories": get_dork_db()["categories"],
        "tech_to_category": {"TestTech": "nonexistentcat"},
        "dorks": {
            "TestTech": [
                {"signature": "test signature here ok", "engine": "google"}
            ]
        },
    }
    with pytest.raises(ValueError, match="unknown category"):
        validate_dork_database(bad_db)
