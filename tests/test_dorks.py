"""Tests for dork loading, validation, query building and LLM helper."""

import json
from unittest.mock import patch, MagicMock, AsyncMock

import httpx
import pytest

from dorkscout.config import validate_dork_database
from dorkscout.dorks import (
    build_query,
    generate_dorks_for_tech,
    get_dorks,
    list_all_techs,
    load_dorks,
)
from dorkscout.models import DorkPattern


def test_load_dorks():
    db = load_dorks()
    assert "categories" in db
    assert "dorks" in db
    assert "tech_to_category" in db
    assert isinstance(db["categories"], dict)
    assert isinstance(db["dorks"], dict)
    assert isinstance(db["tech_to_category"], dict)


def test_get_dorks_by_tech():
    patterns = get_dorks(tech="Stripe")
    assert len(patterns) > 0
    assert all(isinstance(p, DorkPattern) for p in patterns)


def test_get_dorks_by_category():
    patterns = get_dorks(category="niches")
    assert len(patterns) > 0
    assert all(isinstance(p, DorkPattern) for p in patterns)


def test_get_dorks_for_documents():
    patterns = get_dorks(category="documents")
    assert len(patterns) >= 4
    signatures = [p.signature.lower() for p in patterns]
    assert any("filetype:pdf" in s for s in signatures)
    assert any("intitle:" in s for s in signatures)


def test_list_all_techs():
    techs = list_all_techs()
    assert isinstance(techs, list)
    assert len(techs) >= 10
    assert "Stripe" in techs
    assert "Books" in techs


def test_build_query():
    dork = DorkPattern(signature='"powered by shopify" OR ".myshopify.com"', engine="google")
    query = build_query(dork, "coffee")
    assert query.startswith('"powered by shopify"')
    assert '"coffee"' in query or "coffee" in query


def test_build_query_with_advanced_operators():
    dork = DorkPattern(
        signature='filetype:pdf intitle:"annual report"',
        engine="google",
    )
    query = build_query(dork, "financial results")
    assert "filetype:pdf" in query
    assert 'intitle:"annual report"' in query
    assert '"financial results"' in query


def test_validation_passes_on_good_db():
    db = load_dorks()
    validate_dork_database(db)  # should not raise


def test_validation_fails_on_missing_categories():
    bad_db = {
        "dorks": {"Stripe": [{"signature": "foo", "engine": "google"}]},
        "tech_to_category": {"Stripe": "payment_gateways"},
    }
    with pytest.raises(ValueError, match="'categories' must be a non-empty dict"):
        validate_dork_database(bad_db)


def test_validation_fails_on_missing_mapping():
    bad_db = {
        "categories": {
            "platforms": {
                "id": "platforms",
                "name": "Platforms",
                "icon": "P",
                "color": "#6f8a5e",
                "description": "..",
            }
        },
        "dorks": {"NewTech": [{"signature": "foo", "engine": "google"}]},
        "tech_to_category": {"OldTech": "platforms"},
    }
    with pytest.raises(ValueError, match="not present in tech_to_category"):
        validate_dork_database(bad_db)


def test_validation_fails_on_empty_dorks():
    bad_db = {
        "categories": {
            "platforms": {
                "id": "platforms",
                "name": "Platforms",
                "icon": "P",
                "color": "#6f8a5e",
                "description": "..",
            }
        },
        "dorks": {},
        "tech_to_category": {"Stripe": "platforms"},
    }
    with pytest.raises(ValueError, match="'dorks' must be a non-empty dict"):
        validate_dork_database(bad_db)


def test_validation_fails_on_bad_engine():
    bad_db = {
        "categories": {
            "platforms": {
                "id": "platforms",
                "name": "Platforms",
                "icon": "P",
                "color": "#6f8a5e",
                "description": "..",
            }
        },
        "dorks": {"NewTech": [{"signature": "foo", "engine": "invalid"}]},
        "tech_to_category": {"NewTech": "platforms"},
    }
    with pytest.raises(ValueError, match="Invalid engine"):
        validate_dork_database(bad_db)


def test_validation_fails_on_unknown_category():
    bad_db = {
        "categories": {
            "platforms": {
                "id": "platforms",
                "name": "Platforms",
                "icon": "P",
                "color": "#6f8a5e",
                "description": "..",
            }
        },
        "dorks": {"NewTech": [{"signature": "foo", "engine": "google"}]},
        "tech_to_category": {"NewTech": "nonexistent"},
    }
    with pytest.raises(ValueError, match="unknown category"):
        validate_dork_database(bad_db)


@pytest.mark.asyncio
async def test_generate_dorks_for_tech():
    mock_json = {
        "response": '[{"signature": "inurl:newtech login", "engine": "google"}, '
                    '{"signature": "intitle:newtech", "engine": "bing"}]'
    }
    with patch("dorkscout.dorks.httpx.AsyncClient") as mock_async_client:
        mock_response = MagicMock()
        mock_response.json.return_value = mock_json
        mock_instance = mock_async_client.return_value.__aenter__.return_value
        mock_instance.post.return_value = mock_response

        patterns = await generate_dorks_for_tech(
            tech_name="NewTech", category="platforms", num_dorks=2
        )
        assert len(patterns) == 2
        assert all(isinstance(p, DorkPattern) for p in patterns)


@pytest.mark.asyncio
async def test_generate_dorks_for_tech_fallback_on_error():
    with patch("dorkscout.dorks.httpx.AsyncClient") as mock_async_client:
        mock_async_client.side_effect = httpx.ConnectError("Ollama not available")

        patterns = await generate_dorks_for_tech(
            tech_name="NewTech", category=None, num_dorks=3
        )
        assert len(patterns) == 3
        assert all(isinstance(p, DorkPattern) for p in patterns)
        assert all(p.engine == "google" for p in patterns)
