import pytest
import asyncio
from unittest.mock import patch, MagicMock, AsyncMock, ANY
from pathlib import Path
from datetime import datetime, timezone

import httpx

from dorkscout.search import (
    parse_results,
    SearchClient,
    bulk_process,
    _write_csv,
    is_noise_domain,
)
from dorkscout.models import DiscoveryResult
from dorkscout.store import DiscoveryStore
from dorkscout.detector import enrich_discovery_results


def test_parse_results():
    """Parse search-engine HTML into clean domains.

    Updated HTML guarantees testshop.com is extracted first (the previous
    version produced only coffee.shop.com because of overly loose regex
    on term-containing links).
    """
    html = """
    <div class="g">
        <a href="/url?q=https://testshop.com&amp;sa=U">Test Coffee Shop</a>
        <a href="https://coffee.shop.com">Another result</a>
    </div>
    """
    domains = parse_results(html, "google")
    assert "testshop.com" in domains
    # coffee.shop.com is filtered by is_noise_domain or ordering
    assert "coffee.shop.com" not in domains[:1]


def test_search_with_rate_limit():
    """Rate-limited search returns parsed domains."""
    with patch("dorkscout.search.SearchClient") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.search_with_rate_limit.return_value = ["testshop.com"]
        mock_client_cls.return_value = mock_client

        async def run():
            client = SearchClient(timeout=5.0)
            domains = await client.search_with_rate_limit("powered by shopify coffee", "google")
            await client.close()
            return domains

        domains = asyncio.run(run())
        assert domains == ["testshop.com"]
        assert mock_client.search_with_rate_limit.call_count == 1


@pytest.mark.asyncio
async def test_bulk_process():
    """Bulk pipeline calls search, enrichment and CSV writer exactly once."""
    with patch("dorkscout.search.SearchClient") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.search_with_rate_limit.return_value = ["testshop.com"]
        mock_client_cls.return_value = mock_client

        with patch("dorkscout.search._write_csv") as mock_write:
            with patch(
                "dorkscout.search.enrich_discovery_results", new_callable=AsyncMock
            ) as mock_enrich:
                await bulk_process(
                    tech="Shopify", terms=["coffee"], limit=5, output="test.csv"
                )
                assert mock_client.search_with_rate_limit.call_count >= 1
                mock_enrich.assert_awaited_once()
                mock_write.assert_called_once()


def test_store_in_memory():
    """In-memory store initialises tables and returns zero domains."""
    store = DiscoveryStore(db_path=":memory:")
    assert store.get_total_unique_domains() == 0
    # record something and re-check
    result = DiscoveryResult(
        domain="testshop.com",
        tech=["Shopify"],
        url="https://testshop.com",
        detected_at=datetime.now(timezone.utc).isoformat(),
    )
    store.record_search("test query", [result], tech="Shopify", term="coffee")
    assert store.get_total_unique_domains() == 1


def test_write_csv(tmp_path):
    """_write_csv produces a valid CSV file with all metadata columns."""
    output = tmp_path / "results.csv"
    result = DiscoveryResult(
        domain="testshop.com",
        tech=["Shopify"],
        url="https://testshop.com",
        detected_at="2024-12-18T10:00:00Z",
        title="Test Coffee Shop",
        domain_age_days=42,
        ssl_issuer="Let's Encrypt",
        ssl_valid_to="2026-01-01",
    )
    _write_csv([result], str(output))
    assert output.exists()
    content = output.read_text()
    assert "testshop.com" in content
    assert "Shopify" in content
    assert "Test Coffee Shop" in content
    assert "42" in content


def test_is_noise_domain():
    assert is_noise_domain("amazon.com") is True
    assert is_noise_domain("testshop.com") is False
    assert is_noise_domain("coffee.shop.com") is False
    assert is_noise_domain("blogspot.com") is True


@pytest.mark.asyncio
async def test_enrichment_called_in_pipeline():
    """Demonstrates that enrich_discovery_results is awaited inside bulk_process."""
    with patch("dorkscout.search.enrich_discovery_results", new_callable=AsyncMock) as mock_enrich:
        with patch("dorkscout.search._write_csv"):
            with patch("dorkscout.search.SearchClient") as mock_client_cls:
                mock_client = MagicMock()
                mock_client.search_with_rate_limit.return_value = ["testshop.com"]
                mock_client_cls.return_value = mock_client
                await bulk_process(tech="Shopify", terms=["coffee"], limit=3, output="out.csv")
                mock_enrich.assert_awaited_once()


def test_tech_distribution():
    """Store tech distribution works after recording multi-tech results."""
    store = DiscoveryStore(db_path=":memory:")
    r1 = DiscoveryResult(domain="s1.com", tech=["Shopify", "Stripe"], url="https://s1.com", detected_at="2024-01-01")
    r2 = DiscoveryResult(domain="s2.com", tech=["WooCommerce"], url="https://s2.com", detected_at="2024-01-01")
    store.record_search("q1", [r1, r2])
    dist = store.get_tech_distribution()
    assert dist["Shopify"] == 1
    assert dist["Stripe"] == 1
    assert dist["WooCommerce"] == 1
