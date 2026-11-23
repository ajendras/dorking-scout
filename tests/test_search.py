"""Tests for search functionality, parsing, bulk CLI and enrichment."""

import csv
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from dorkscout.dorks import DorkPattern
from dorkscout.models import DiscoveryResult
from dorkscout.search import (
    NOISE_DOMAINS,
    bulk_process,
    is_noise_domain,
    parse_results,
)


def test_is_noise_domain():
    assert is_noise_domain("amazon.com") is True
    assert is_noise_domain("amazon.co.uk") is True
    assert is_noise_domain("stripe.shop.example.com") is False
    assert is_noise_domain("my-unique-coffee-store.com") is False
    assert is_noise_domain("") is True


def test_parse_results_basic():
    html = """
    <html><body>
    <a href="https://example.com/store">link</a>
    <a href="/url?q=https://testshop.com/page">google link</a>
    <a href="https://amazon.com/bad">noise</a>
    <a href="https://coffee-niche.example.org">niche</a>
    </body></html>
    """
    results = parse_results(html, "google")
    assert "example.com" in results
    assert "testshop.com" in results
    assert "coffee-niche.example.org" in results
    assert "amazon.com" not in results


def test_parse_results_baidu_style():
    html = '<a href="http://testbooks.com">baidu result</a>'
    results = parse_results(html, "baidu")
    assert "testbooks.com" in results


@pytest.mark.asyncio
async def test_search_client_rate_limit():
    with patch("dorkscout.search.httpx.AsyncClient") as mock_client:
        mock_resp = MagicMock()
        mock_resp.text = '<a href="https://testshop.com">result</a>'
        mock_resp.raise_for_status = MagicMock()
        mock_instance = mock_client.return_value
        mock_instance.get.return_value = mock_resp

        from dorkscout.search import SearchClient

        client = SearchClient(timeout=1.0)
        domains = await client.search_with_rate_limit('"powered by shopify" coffee', "google")
        assert len(domains) > 0
        await client.close()
        mock_instance.get.assert_called()


@pytest.mark.asyncio
async def test_bulk_process(tmp_path: Path):
    output_file = tmp_path / "bulk_output.csv"

    # Mock dependencies to avoid real network calls
    with patch("dorkscout.search.get_dorks") as mock_get_dorks, \
         patch("dorkscout.search.SearchClient") as mock_client_cls, \
         patch("dorkscout.search.enrich_discovery_results", new_callable=AsyncMock) as mock_enrich, \
         patch("dorkscout.search.build_query") as mock_build_query, \
         patch("dorkscout.search.detect_technologies") as mock_detect:

        # Setup mocks
        mock_get_dorks.return_value = [
            DorkPattern(signature="test sig", engine="google")
        ]
        mock_client = mock_client_cls.return_value
        mock_client.search_with_rate_limit = AsyncMock(
            return_value=["testshop1.com", "testshop2.com", "amazon.com"]
        )
        mock_client.close = AsyncMock()
        mock_build_query.return_value = '"test" coffee'
        mock_detect.return_value = ["Shopify"]
        mock_enrich.return_value = None

        # Run
        await bulk_process(
            tech="Shopify",
            terms=["coffee", "tea"],
            output=str(output_file),
            limit=10,
        )

    # Verify output file created with correct content
    assert output_file.exists()
    content = output_file.read_text(encoding="utf-8")
    assert "domain" in content.lower()
    assert "testshop1.com" in content
    assert "testshop2.com" in content
    assert "Shopify" in content
    assert "domain_age_days" in content

    mock_enrich.assert_awaited()
    assert mock_client.search_with_rate_limit.call_count >= 1


def test_bulk_process_csv_structure(tmp_path: Path):
    results = [
        DiscoveryResult(
            domain="example.com",
            tech=["Stripe"],
            url="https://example.com",
            detected_at="2024-12-18T10:00:00Z",
            title="Example Store",
            domain_age_days=1234,
            ssl_issuer="Let's Encrypt",
        )
    ]
    output_file = tmp_path / "test.csv"
    from dorkscout.search import _write_csv
    _write_csv(results, str(output_file))

    with open(output_file, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        assert len(rows) == 1
        assert rows[0]["domain"] == "example.com"
        assert rows[0]["tech"] == "Stripe"
        assert rows[0]["ssl_issuer"] == "Let's Encrypt"
        assert rows[0]["domain_age_days"] == "1234"
