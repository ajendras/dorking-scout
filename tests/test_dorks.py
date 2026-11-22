"""Test suite for dork database loading, filtering, query building, validation and LLM generation."""

import json
from unittest.mock import patch, AsyncMock, MagicMock

import httpx
import pytest

from dorkscout.config import get_dork_db, validate_dork_database
from dorkscout.dorks import (
    load_dorks,
    get_dorks,
    build_query,
    list_all_techs,
    generate_dorks_for_tech,
)
from dorkscout.models import DorkPattern, Category


def test_load_dorks():
    db = load_dorks()
    assert isinstance(db, dict)
    assert "categories" in db
    assert "dorks" in db
    assert "tech_to_category" in db
    assert len(db["categories"]) == 3
    assert len(db["dorks"]) >= 5


def test_get_dork_db():
    db = get_dork_db()
    assert db is not None
    assert "payment_gateways" in db["categories"]


def test_validate_dork_database():
    validate_dork_database()  # no exception


def test_validate_dork_database_fails_on_bad_input():
    with pytest.raises(ValueError):
        validate_dork_database({})


def test_list_all_techs():
    techs = list_all_techs()
    assert isinstance(techs, list)
    assert len(techs) >= 5
    assert "Stripe" in techs
    assert "Shopify" in techs


def test_get_dorks_by_tech():
    patterns = get_dorks(tech="Stripe")
    assert len(patterns) > 0
    assert isinstance(patterns[0], DorkPattern)
    assert any("stripe" in p.signature.lower() for p in patterns)


def test_get_dorks_by_category():
    patterns = get_dorks(category="platforms")
    assert len(patterns) > 0
    for p in patterns:
        assert isinstance(p, DorkPattern)


def test_get_dorks_raises_on_unknown():
    with pytest.raises(ValueError, match="No dorks found"):
        get_dorks(tech="NonExistentTech")


def test_build_query():
    dork = DorkPattern(
        signature='"powered by shopify" OR ".myshopify.com"',
        engine="google",
    )
    query = build_query(dork, "coffee")
    assert query == '"powered by shopify" OR ".myshopify.com" "coffee"'
    query2 = build_query(dork, '"already quoted"')
    assert '"already quoted"' in query2


@pytest.mark.asyncio
async def test_generate_dorks_for_tech_fallback():
    # tests the fallback when Ollama not available
    patterns = await generate_dorks_for_tech(
        tech_name="NewAwesomeCMS",
        category="platforms",
        num_dorks=4,
    )
    assert len(patterns) == 4
    for pattern in patterns:
        assert isinstance(pattern, DorkPattern)
        assert "newawesomecms" in pattern.signature.lower()


@pytest.mark.asyncio
@patch("httpx.AsyncClient")
async def test_generate_dorks_for_tech_with_llm(mock_client):
    mock_instance = AsyncMock()
    mock_response = MagicMock()
    mock_response.text = json.dumps(
        {
            "response": ' [ {"signature": "inurl:newtech", "engine": "google"}, {"signature": "\\"powered by newtech\\"", "engine": "bing"} ] '
        }
    )
    mock_instance.post.return_value = mock_response
    mock_client.return_value.__aenter__.return_value = mock_instance

    patterns = await generate_dorks_for_tech(
        tech_name="NewTech",
        num_dorks=2,
    )
    assert len(patterns) == 2
    assert patterns[0].signature == "inurl:newtech"
    assert patterns[0].engine == "google"
