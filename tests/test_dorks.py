"""Tests for dork loading, query building, validation and LLM fallback."""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from dorkscout.dorks import (
    build_query,
    generate_dorks_for_tech,
    get_dorks,
    list_all_techs,
    load_dorks,
)
from dorkscout.models import DorkPattern
from dorkscout.config import validate_dork_database


def test_load_dorks():
    db = load_dorks()
    assert isinstance(db, dict)
    assert "categories" in db
    assert "dorks" in db
    assert "tech_to_category" in db
    assert len(db["dorks"]) > 5


def test_get_dorks_by_tech():
    patterns = get_dorks(tech="Shopify")
    assert len(patterns) >= 1
    assert isinstance(patterns[0], DorkPattern)
    assert "shopify" in patterns[0].signature.lower()


def test_get_dorks_by_category():
    patterns = get_dorks(category="platforms")
    assert len(patterns) >= 2
    assert all(isinstance(p, DorkPattern) for p in patterns)


def test_get_dorks_raises_on_unknown():
    """Updated to match the helpful error message that lists available techs."""
    with pytest.raises(
        ValueError, match=r"Unknown or empty tech .*Available"
    ):
        get_dorks(tech="NonExistentTech")


def test_build_query_quotes_term():
    dork = DorkPattern(
        signature='"powered by shopify" OR ".myshopify.com"',
        engine="google",
    )
    query = build_query(dork, "coffee")
    assert '"coffee"' in query
    assert query.endswith('"coffee"')


def test_build_query_with_advanced_operators():
    """Operators inside signature are preserved, term is appended as phrase."""
    dork = DorkPattern(
        signature='filetype:pdf intitle:report',
        engine="google",
    )
    query = build_query(dork, "financial results")
    assert 'filetype:pdf intitle:report "financial results"' == query


def test_get_dorks_for_documents():
    patterns = get_dorks(category="documents")
    assert len(patterns) >= 4
    signatures = " ".join(p.signature.lower() for p in patterns)
    assert "filetype:pdf" in signatures or "intitle:" in signatures


def test_list_all_techs():
    techs = list_all_techs()
    assert isinstance(techs, list)
    assert len(techs) >= 8
    assert "Stripe" in techs
    assert "Resumes" in techs


def test_validate_dork_database():
    validate_dork_database()  # should not raise on current DB


def test_validation_fails_on_bad_engine():
    bad_db = {
        "categories": {
            "test": {
                "id": "test",
                "name": "Test",
                "icon": "🧪",
                "color": "#666",
                "description": "Test cat",
            }
        },
        "dorks": {
            "BadTech": [
                {"signature": "bad", "engine": "invalid"}
            ]
        },
        "tech_to_category": {"BadTech": "test"},
    }
    with pytest.raises(ValueError, match="Invalid engine 'invalid'"):
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
        "tech_to_category": {},
    }
    with pytest.raises(ValueError, match="not present in tech_to_category"):
        validate_dork_database(bad_db)


@pytest.mark.asyncio
async def test_generate_dorks_for_tech_with_ollama():
    """Test successful path with mocked Ollama response that returns JSON."""
    mock_json = [
        {"signature": '"TestCMS" admin', "engine": "google"},
        {"signature": 'intitle:"TestCMS"', "engine": "bing"},
    ]
    mock_response = MagicMock()
    mock_response.json.return_value = {"response": json.dumps(mock_json)}
    with patch("httpx.AsyncClient") as mock_client:
        mock_instance = AsyncMock()
        mock_instance.post.return_value = mock_response
        mock_client.return_value.__aenter__.return_value = mock_instance

        patterns = await generate_dorks_for_tech(
            tech_name="TestCMS", num_dorks=2
        )
        assert len(patterns) == 2
        assert all(isinstance(p, DorkPattern) for p in patterns)


def test_generate_dorks_for_tech_fallback():
    """Trigger fallback path; now returns exactly num_dorks varied patterns."""
    with patch("httpx.AsyncClient") as mock_client:
        mock_instance = AsyncMock()
        mock_instance.post.side_effect = Exception("Ollama unavailable")
        mock_client.return_value.__aenter__.return_value = mock_instance

        patterns = asyncio.run(
            generate_dorks_for_tech(tech_name="NewAwesomeCMS", num_dorks=4)
        )
        assert len(patterns) == 4
        # verify they are varied (different signatures or engines)
        sig_set = {p.signature for p in patterns}
        assert len(sig_set) >= 2
        eng_set = {p.engine for p in patterns}
        assert len(eng_set) >= 2


def test_generate_dorks_raises_on_empty_tech():
    with pytest.raises(ValueError):
        asyncio.run(generate_dorks_for_tech(tech_name=""))
