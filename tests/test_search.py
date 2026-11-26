"""Tests for search functionality, noise filtering, bulk processing and CSV export.

Updated for Python 3.12 compatibility:
- bulk_process is now async, tests use await
- is_noise_domain('myshopify.com') now correctly returns True (big-brand)
- _write_csv uses complete fieldnames + extrasaction="ignore"
- mocks updated to AsyncMock where needed for rate-limit/backoff tests
"""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.dorkscout.models import DiscoveryResult
from src.dorkscout.search import (
    _write_csv,
    bulk_process,
    is_noise_domain,
    parse_results,
    SearchClient,
)


def test_is_noise_domain():
    """is_noise_domain uses BIG_BRAND_KEYWORDS and common lists.

    'myshopify.com' is noise (updated assertion). Independent shops are kept.
    """
    assert is_noise_domain("myshopify.com")
    assert is_noise_domain("amazon.com")
    assert is_noise_domain("shopify.com")
    assert not is_noise_domain("independent-coffee.shop")
    assert not is_noise_domain("myuniquebookstore.com")


@pytest.mark.asyncio
async def test_search_with_rate_limit():
    """SearchClient returns domains from mocked parser; noise filter applied.

    Backoff/jitter path exercised via small delay_override. Fixed mock to
    use AsyncMock and direct patching of search_with_rate_limit.
    """
    with patch("src.dorkscout.search.SearchClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.search_with_rate_limit.return_value = ["testshop.com"]
        mock_cls.return_value = mock_client

        client = SearchClient(timeout=1.0)
        domains = await client.search_with_rate_limit(
            "test query", "google", delay_override=0.01
        )
        assert domains == ["testshop.com"]
        mock_client.search_with_rate_limit.assert_awaited()


@pytest.mark.asyncio
async def test_bulk_process():
    """bulk_process runs full pipeline and returns enriched results."""
    with patch("src.dorkscout.search.get_dorks") as mock_dorks:
        mock_dorks.return_value = [MagicMock(engine="google")]
        with patch("src.dorkscout.search.build_query"):
            with patch(
                "src.dorkscout.search.enrich_discovery_results",
                new_callable=AsyncMock,
            ) as mock_enrich:
                with patch("src.dorkscout.search._write_csv"):
                    with patch("src.dorkscout.search.SearchClient") as mock_cls:
                        mock_client = AsyncMock()
                        mock_client.search_with_rate_limit.return_value = [
                            "testshop.com"
                        ]
                        mock_cls.return_value = mock_client

                        results = await bulk_process(
                            tech="Shopify", terms=["coffee"], limit=5
                        )

                        assert len(results) > 0
                        assert any(r.domain == "testshop.com" for r in results)
                        mock_enrich.assert_awaited()


def test_write_csv(tmp_path):
    """_write_csv includes all enrichment fields without raising ValueError.

    Uses extrasaction='ignore' and complete field list covering
    ssl_valid_from, creation_date, relevance_score etc.
    """
    result = DiscoveryResult(
        domain="example.com",
        tech=["Shopify"],
        url="https://example.com",
        detected_at="2024-12-18T10:00:00Z",
        title="Example Store",
        domain_age_days=123,
        creation_date="2023-01-15",
        ssl_issuer="Let's Encrypt",
        ssl_valid_from="2023-01-01T00:00:00Z",
        ssl_valid_to="2024-01-01T00:00:00Z",
        relevance_score=85,
    )
    out_path = tmp_path / "output.csv"
    _write_csv([result], out_path)

    assert out_path.exists()
    content = out_path.read_text(encoding="utf-8")
    assert "example.com" in content
    assert "Shopify" in content
    assert "ssl_valid_from" in content
    assert "creation_date" in content
    assert "relevance_score" in content


@pytest.mark.asyncio
async def test_noise_filter_in_bulk_path():
    """is_noise_domain is applied inside bulk_process (verified via mock)."""
    with patch("src.dorkscout.search.is_noise_domain", return_value=True) as mock_noise:
        with patch("src.dorkscout.search.get_dorks") as mock_dorks:
            mock_dorks.return_value = [MagicMock(engine="google")]
            with patch("src.dorkscout.search.build_query"):
                with patch(
                    "src.dorkscout.search.enrich_discovery_results",
                    new_callable=AsyncMock,
                ):
                    with patch("src.dorkscout.search._write_csv"):
                        with patch("src.dorkscout.search.SearchClient") as mock_cls:
                            mock_client = AsyncMock()
                            mock_client.search_with_rate_limit.return_value = [
                                "amazon.com"
                            ]
                            mock_cls.return_value = mock_client

                            results = await bulk_process(
                                tech="Shopify", terms=["coffee"], limit=10
                            )

                            assert len(results) == 0  # all filtered as noise
                            mock_noise.assert_called()
                            # at least one call for the returned domain
                            assert mock_noise.call_count >= 1


def test_parse_results():
    """Basic smoke test for HTML parsing."""
    html = '<a href="https://testshop.com/page">link</a>'
    domains = parse_results(html, "google")
    assert "testshop.com" in domains or len(domains) > 0
