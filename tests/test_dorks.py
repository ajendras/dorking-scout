"""Tests for dork loading, validation, query building and LLM generation."""

import asyncio
import json
import re

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from dorkscout.dorks import (
    build_query,
    generate_dorks_for_tech,
    get_dorks,
    list_all_techs,
    load_dorks,
)
from dorkscout.models import DorkPattern


def test_load_dorks_valid():
    db = load_dorks()
    assert "categories" in db
    assert "dorks" in db
    assert "tech_to_category" in db
    assert isinstance(db["dorks"], dict)


def test_get_dorks_raises_on_unknown():
    """Updated to match the helpful error message that lists available techs."""
    with pytest.raises(ValueError, match=r"Unknown or empty tech"):
        get_dorks(tech="NonExistentTech")


def test_get_dorks_by_tech():
    patterns = get_dorks(tech="Stripe")
    assert len(patterns) >= 1
    assert all(isinstance(p, DorkPattern) for p in patterns)


def test_get_dorks_by_category():
    patterns = get_dorks(category="niches")
    assert len(patterns) >= 2
    assert all(isinstance(p, DorkPattern) for p in patterns)


def test_build_query_quotes_term():
    dork = DorkPattern(signature='"powered by stripe"', engine="google")
    q = build_query(dork, "coffee shop")
    assert '"coffee shop"' in q
    assert "powered by stripe" in q


def test_list_all_techs():
    techs = list_all_techs()
    assert len(techs) >= 8
    assert "Stripe" in techs
    assert "Books" in techs


@pytest.mark.asyncio
@patch("httpx.AsyncClient")
async def test_generate_dorks_for_tech_with_llm(mock_client):
    """Fixed mock to use .json() return_value (was causing MagicMock in json.loads)."""
    mock_instance = AsyncMock()
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "response": ' [ {"signature": "inurl:newtech", "engine": "google"}, {"signature": "\\"powered by newtech\\"", "engine": "bing"} ] '
    }
    mock_instance.post.return_value = mock_response
    mock_client.return_value.__aenter__.return_value = mock_instance

    patterns = await generate_dorks_for_tech(
        tech_name="NewTech",
        num_dorks=2,
    )
    assert len(patterns) == 2
    assert all(isinstance(p, DorkPattern) for p in patterns)
    signatures = [p.signature for p in patterns]
    assert any("newtech" in s.lower() for s in signatures)


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
        assert all(isinstance(p, DorkPattern) for p in patterns)


def test_validation_fails_on_bad_engine():
    bad_db = {
        "categories": {"test": {"id": "test", "name": "Test", "icon": "T", "color": "#000", "description": "x"}},
        "dorks": {"BadTech": [{"signature": "test", "engine": "invalid"}]},
        "tech_to_category": {"BadTech": "test"},
    }
    with pytest.raises(ValueError, match="Invalid engine"):
        from dorkscout.config import validate_dork_database
        validate_dork_database(bad_db)


__all__ = ["test_load_dorks_valid"]
