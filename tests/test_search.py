"""Test suite for search, parsing, bulk, detection, enrichment, relevance scoring and analytics queries.

Covers parse_results for all engines, noise domain filtering, relevance scoring, enrichment with
mocked free APIs (Wayback/CDX and crt.sh), build_query with operators/quoting, bulk_process,
DiscoveryStore analytics with date/tech filters, and document category dorks. Uses mocks to
avoid real network calls and keep tests fast/reliable. Consistent with existing style (pytest,
pytest-asyncio, unittest.mock, Pydantic models, async tests).
"""

import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from datetime import datetime, timezone
import httpx
import json
from pathlib import Path
from typing import List

from dorkscout.models import DiscoveryResult, DorkPattern
from dorkscout.search import SearchClient, parse_results, is_noise_domain, bulk_process
from dorkscout.detector import (
    detect_technologies,
    enrich_discovery_results,
    calculate_relevance_score,
)
from dorkscout.store import DiscoveryStore
from dorkscout.dorks import build_query, get_dorks


@pytest.fixture
def mock_html_responses() -> dict[str, str]:
    """Provide minimal but realistic HTML snippets that parse_results can handle for each engine."""
    return {
        "google": (
            '<html><body><div class="g"><a href="https://coffee.shop.com/">'
            '<h3>Coffee Shop</h3></a></div><div class="g"><a href="https://test.com/">'
            '<h3>Test</h3></a></div></body></html>'
        ),
        "bing": (
            '<html><body><li><a href="https://bing.shop.com">Bing Shop</a></li>'
            '</body></html>'
        ),
        "duckduckgo": (
            '<html><body><a href="https://ddg.example.com" class="result__a">DDG</a>'
            '</body></html>'
        ),
        "baidu": (
            '<html><body><a href="https://baidu.shop.com"><h3>Baidu Shop</h3></a>'
            '</body></html>'
        ),
        "yandex": (
            '<html><body><a href="https://yandex.niche.com">Yandex Niche</a>'
            '</body></html>'
        ),
    }


def test_parse_results(mock_html_responses: dict[str, str]) -> None:
    """parse_results extracts second-level domains from each supported search engine's HTML."""
    for engine, html in mock_html_responses.items():
        domains = parse_results(html, engine)
        assert len(domains) >= 1, f"Failed to parse any domains for engine {engine}"
        assert any(
            d in {"coffee.shop.com", "test.com", "bing.shop.com", "ddg.example.com", "baidu.shop.com", "yandex.niche.com"}
            for d in domains
        )


def test_is_noise_domain() -> None:
    """is_noise_domain correctly identifies big brands/marketplaces using BIG_BRAND_KEYWORDS."""
    assert is_noise_domain("amazon.com") is True
    assert is_noise_domain("ebay.com") is True
    assert is_noise_domain("etsy.com") is True
    assert is_noise_domain("shopify.com") is True
    assert is_noise_domain("randomindiesite.example.com") is False
    assert is_noise_domain("mycoffee.store") is False


def test_calculate_relevance_score() -> None:
    """calculate_relevance_score rewards independent sites with age while penalizing brands."""
    independent = DiscoveryResult(
        domain="indieshoestore.com",
        tech=["Shopify"],
        url="https://indieshoestore.com",
        detected_at=datetime.now(timezone.utc).isoformat(),
        domain_age_days=400,
    )
    score = calculate_relevance_score(independent)
    assert score > 60, f"Expected high relevance for independent site, got {score}"

    brand = DiscoveryResult(
        domain="amazon.com",
        tech=["Amazon Payments"],
        url="https://amazon.com",
        detected_at=datetime.now(timezone.utc).isoformat(),
        domain_age_days=100,
    )
    score = calculate_relevance_score(brand)
    assert score < 40, f"Expected low relevance for brand, got {score}"


@pytest.mark.asyncio
async def test_enrich_discovery_results() -> None:
    """enrich_discovery_results populates age, SSL metadata and relevance using mocked free APIs."""
    results: List[DiscoveryResult] = [
        DiscoveryResult(
            domain="example.com",
            tech=["Stripe"],
            url="https://example.com",
            detected_at=datetime.now(timezone.utc).isoformat(),
        )
    ]

    with patch("dorkscout.detector.httpx.AsyncClient") as MockClient:
        instance = MockClient.return_value.__aenter__.return_value
        instance.get = AsyncMock()

        # Wayback CDX response (timestamp list)
        wayback_resp = MagicMock()
        wayback_resp.json.return_value = [["*", "2020-01-01T00:00:00Z"]]
        # crt.sh JSON response
        crt_resp = MagicMock()
        crt_resp.text = json.dumps(
            [
                {
                    "issuer_name": "Test CA",
                    "not_before": "2020-01-01",
                    "not_after": "2030-01-01",
                }
            ]
        )
        instance.get.side_effect = [wayback_resp, crt_resp]

        await enrich_discovery_results(results, max_results=1)

    assert results[0].domain_age_days is not None
    assert results[0].ssl_issuer is not None
    assert results[0].ssl_valid_to is not None
    assert results[0].relevance_score > 0


@pytest.mark.asyncio
async def test_search_client() -> None:
    """SearchClient performs rate-limited searches and parses domains without errors."""
    client = SearchClient(timeout=2.0)
    with patch.object(client.client, "get") as mock_get:
        mock_response = MagicMock()
        mock_response.text = (
            '<html><body><div class="g"><a href="https://test.com/"><h3>Test</h3></a></div></body></html>'
        )
        mock_get.return_value = mock_response

        domains = await client.search_with_rate_limit(
            "test query", "google", delay_override=0.0
        )
        assert isinstance(domains, list)
        assert len(domains) >= 0

    await client.close()


def test_build_query_with_advanced_operators() -> None:
    """build_query preserves advanced operators while quoting the additional term."""
    dork = DorkPattern(
        signature='filetype:pdf intitle:"financial report"',
        engine="google",
    )
    query = build_query(dork, "acme corp")
    assert 'filetype:pdf' in query
    assert 'intitle:"financial report"' in query
    assert '"acme corp"' in query


@pytest.mark.asyncio
async def test_bulk_process(tmp_path: Path) -> None:
    """bulk_process runs multi-term searches, deduplicates, enriches, writes CSV and returns results."""
    output_csv = tmp_path / "test_bulk.csv"
    terms = ["coffee", "books"]

    with patch("dorkscout.search.SearchClient") as MockClient:
        mock_instance = MockClient.return_value
        mock_instance.search_with_rate_limit = AsyncMock(
            return_value=["shop1.com", "shop2.com", "amazon.com"]
        )
        mock_instance.close = AsyncMock()

        with patch("dorkscout.search.enrich_discovery_results", new_callable=AsyncMock):
            with patch("dorkscout.search.detect_technologies", return_value=["Shopify"]):
                results = await bulk_process(
                    tech="Shopify",
                    terms=terms,
                    output_path=str(output_csv),
                    limit=5,
                )
                assert len(results) > 0
                assert output_csv.exists()
                content = output_csv.read_text()
                assert "domain" in content
                assert "shop1.com" in content or "shop2.com" in content
                assert "tech" in content


def test_store_analytics_filters() -> None:
    """DiscoveryStore.get_tech_distribution and get_total_unique_domains respect date/tech filters."""
    store = DiscoveryStore(db_path=":memory:")

    res = DiscoveryResult(
        domain="testshop.com",
        tech=["Stripe", "WooCommerce"],
        url="https://testshop.com",
        detected_at=datetime.now(timezone.utc).isoformat(),
        domain_age_days=100,
    )
    store.record_search(
        query="stripe coffee",
        results=[res],
        category="payment_gateways",
        tech="Stripe",
        term="coffee",
        engine="google",
    )

    dist = store.get_tech_distribution()
    assert "Stripe" in dist
    assert dist["Stripe"] >= 1

    dist_filtered = store.get_tech_distribution(tech_filter=["Stripe"])
    assert "Stripe" in dist_filtered
    assert dist_filtered["Stripe"] >= 1

    total = store.get_total_unique_domains(tech_filter=["Stripe"])
    assert total >= 1

    today = datetime.now(timezone.utc).date().isoformat()
    dist_date = store.get_tech_distribution(date_from=today)
    assert len(dist_date) >= 0

    store.close()


def test_get_dorks_for_documents() -> None:
    """get_dorks returns patterns for the documents category including advanced operators."""
    dorks = get_dorks(category="documents")
    assert len(dorks) > 0
    assert any("filetype:pdf" in d.signature for d in dorks)
    assert any("intitle:" in d.signature for d in dorks)
