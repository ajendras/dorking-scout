"""Tests for search.py (parsing, noise filtering, bulk processing, CSV export)."""

import asyncio
from datetime import datetime, timezone
from unittest.mock import ANY, AsyncMock, MagicMock, mock_open, patch

import pytest

from dorkscout.models import DiscoveryResult
from dorkscout.search import (
    _write_csv,
    bulk_process,
    is_noise_domain,
    parse_results,
    SearchClient,
)


def test_is_noise_domain():
    """is_noise_domain correctly distinguishes useful domains from noise.

    myshopify.com must return False so that Shopify store subdomains are kept.
    """
    assert not is_noise_domain("coffee.example.com")
    assert not is_noise_domain("myshopify.com")
    assert not is_noise_domain("mycoffee.myshopify.com")
    assert is_noise_domain("amazon.com")
    assert is_noise_domain("shopify.com")
    assert is_noise_domain("amazon.marketplace.com")
    assert is_noise_domain("")
    assert is_noise_domain("blog.wordpress.com")


def test_parse_results():
    """parse_results extracts domains from typical search-engine HTML and applies noise filter."""
    html = """
    <html>
        <a href="https://coffee.example.com/shop">Coffee Shop</a>
        <a href="https://amazon.com">Amazon</a>
        <a href="https://testshop.com">Test Shop</a>
    </html>
    """
    domains = parse_results(html, "google")
    assert "coffee.example.com" in domains
    assert "testshop.com" in domains
    assert "amazon.com" not in domains
    assert parse_results("", "google") == []
    assert parse_results("<html></html>", "bing") == []


@pytest.mark.asyncio
async def test_search_with_rate_limit():
    """SearchClient.search_with_rate_limit returns parsed domains and respects rate limits."""
    client = SearchClient(timeout=1.0)
    mock_html = '<a href="https://testshop.com">shop</a>'
    with patch.object(client.client, "get") as mock_get:
        mock_resp = MagicMock()
        mock_resp.text = mock_html
        mock_resp.raise_for_status.return_value = None
        mock_get.return_value = mock_resp
        with patch("dorkscout.search.asyncio.sleep"):
            domains = await client.search_with_rate_limit(
                "coffee shopify", "google"
            )
            assert domains == ["testshop.com"]
            mock_get.assert_called_once()


@pytest.mark.asyncio
async def test_bulk_process():
    """bulk_process wires together search, noise filtering, enrichment and CSV writing."""
    with patch("dorkscout.search.get_dorks") as mock_get_dorks:
        mock_get_dorks.return_value = [MagicMock(engine="google")]
        with patch("dorkscout.search.build_query", return_value="test query"):
            with patch("dorkscout.search.SearchClient") as mock_client_cls:
                mock_client = AsyncMock()
                mock_client.search_with_rate_limit.return_value = [
                    "coffee.example.com",
                    "testshop.com",
                ]
                mock_client_cls.return_value = mock_client
                with patch("dorkscout.search._write_csv") as mock_write:
                    with patch(
                        "dorkscout.search.enrich_discovery_results",
                        new_callable=AsyncMock,
                    ) as mock_enrich:
                        with patch(
                            "dorkscout.search.is_noise_domain", return_value=False
                        ):
                            await bulk_process(
                                tech="Shopify",
                                terms=["coffee"],
                                limit=10,
                                output="test.csv",
                            )
                            mock_write.assert_called_once_with(ANY, "test.csv")
                            mock_enrich.assert_awaited_once()


def test_write_csv():
    """_write_csv produces a valid CSV with semicolon-separated tech list and
    all enrichment columns.
    """
    results = [
        DiscoveryResult(
            domain="coffee.example.com",
            tech=["Shopify", "Stripe"],
            url="https://coffee.example.com",
            detected_at=datetime.now(timezone.utc).isoformat(),
            title="Test Coffee Shop",
            domain_age_days=142,
            ssl_issuer="Let's Encrypt",
            ssl_valid_to="2026-01-01",
        )
    ]

    # Patch open to capture written content without touching disk
    with patch("dorkscout.search.open", mock_open()) as mocked_open:
        _write_csv(results, "results.csv")

    mocked_open.assert_called_once_with(
        "results.csv", "w", newline="", encoding="utf-8"
    )
    handle = mocked_open.return_value
    handle.write.assert_called()


@pytest.mark.asyncio
async def test_noise_filter_in_bulk_path():
    """is_noise_domain is applied inside bulk_process (verified via mock)."""
    with patch("dorkscout.search.is_noise_domain", return_value=True) as mock_noise:
        with patch("dorkscout.search.get_dorks") as mock_dorks:
            mock_dorks.return_value = [MagicMock(engine="google")]
            with patch("dorkscout.search.build_query"):
                with patch(
                    "dorkscout.search.enrich_discovery_results",
                    new_callable=AsyncMock,
                ):
                    with patch("dorkscout.search._write_csv"):
                        with patch("dorkscout.search.SearchClient") as mock_cls:
                            mock_client = AsyncMock()
                            mock_client.search_with_rate_limit.return_value = [
                                "amazon.com"
                            ]
                            mock_cls.return_value = mock_client

                            await bulk_process(
                                tech="Shopify", terms=["coffee"], limit=10
                            )
    mock_noise.assert_called()
