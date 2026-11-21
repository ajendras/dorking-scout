"""Tests for dork loading, query building, validation and LLM helper."""

import json
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from src.dorkscout.config import (
    DEFAULT_DORK_DB,
    validate_dork_database,
)
from src.dorkscout.dorks import (
    build_query,
    generate_dorks_for_tech,
    get_dorks,
    list_all_techs,
    load_dorks,
)
from src.dorkscout.models import DorkPattern


def test_load_dorks_returns_merged_db():
    db = load_dorks()
    assert "categories" in db
    assert "dorks" in db
    assert "tech_to_category" in db
    assert "Stripe" in db["dorks"]
    assert "Shopify" in db["tech_to_category"]


def test_get_dorks_by_tech():
    patterns = get_dorks(tech="Stripe")
    assert len(patterns) >= 1
    assert all(isinstance(p, DorkPattern) for p in patterns)
    assert patterns[0].engine in ("google", "bing")


def test_get_dorks_by_category():
    patterns = get_dorks(category="payment_gateways")
    assert len(patterns) >= 2
    techs = {p.signature for p in patterns}  # rough check
    assert any("stripe" in s.lower() for s in techs)


def test_get_dorks_raises_for_unknown():
    with pytest.raises(ValueError, match="No dorks found"):
        get_dorks(tech="UnknownTech123")


def test_build_query_quotes_term():
    pattern = DorkPattern(signature="powered by shopify", engine="google")
    q = build_query(pattern, "coffee shop")
    assert '"coffee shop"' in q
    assert "powered by shopify" in q


def test_build_query_allows_prequoted_term():
    pattern = DorkPattern(signature="inurl:store", engine="google")
    q = build_query(pattern, '"already quoted"')
    assert '"already quoted"' in q


def test_list_all_techs():
    techs = list_all_techs()
    assert "Stripe" in techs
    assert "Shopify" in techs
    assert "Books" in techs


def test_validation_passes_default_db():
    validate_dork_database()  # no exception


def test_validation_fails_on_missing_keys():
    bad = {"dorks": {}}
    with pytest.raises(ValueError, match="must be a non-empty dict"):
        validate_dork_database(bad)


def test_validation_fails_on_bad_engine():
    bad_db = {
        "categories": DEFAULT_DORK_DB["categories"],
        "dorks": {"Stripe": [{"signature": "valid signature here", "engine": "invalid"}]},
        "tech_to_category": {"Stripe": "payment_gateways"},
    }
    with pytest.raises(ValueError, match="Invalid engine"):
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


def test_validation_fails_on_unmapped_tech():
    bad_db = {
        "categories": DEFAULT_DORK_DB["categories"],
        "dorks": {"UnknownTech": [{"signature": "some long enough signature here", "engine": "google"}]},
        "tech_to_category": {"Stripe": "payment_gateways"},
    }
    with pytest.raises(ValueError, match="not present in tech_to_category"):
        validate_dork_database(bad_db)


@pytest.mark.asyncio
async def test_generate_dorks_for_tech():
    mock_response = {
        "response": json.dumps(
            {
                "dorks": [
                    {"signature": "inurl:exampletech admin", "engine": "google"},
                    {"signature": '"powered by exampletech"', "engine": "bing"},
                ]
            }
        )
    }
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = httpx.Response(200, json=mock_response)
        patterns = await generate_dorks_for_tech("ExampleTech", "platforms", 2)
        assert len(patterns) == 2
        assert all(isinstance(p, DorkPattern) for p in patterns)


@pytest.mark.asyncio
async def test_generate_dorks_fallback_on_error():
    with patch("httpx.AsyncClient.post", side_effect=httpx.RequestError("offline")):
        patterns = await generate_dorks_for_tech("NewTech", num_dorks=1)
        assert len(patterns) == 1
        assert "newtech" in patterns[0].signature.lower()
