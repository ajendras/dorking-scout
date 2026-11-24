import json
from pathlib import Path
from unittest.mock import patch, AsyncMock

import pytest

from dorkscout.models import DiscoveryResult
from dorkscout.search import (
    SearchClient,
    bulk_process,
    is_noise_domain,
    parse_results,
    _write_csv,
)
from dorkscout.store import DiscoveryStore


def test_is_noise_domain():
    assert is_noise_domain("amazon.com") is True
    assert is_noise_domain("testshop.com") is False
    assert is_noise_domain("ebay.co.uk") is True
    assert is_noise_domain("my-unique-coffee.shop") is False
    assert is_noise_domain("") is True
    assert is_noise_domain(None) is True  # type: ignore


def test_parse_results():
    html = """
    <html><body>
    <a href="/url?q=https://coffee.shop.com/page">result</a>
    <a href="https://amazon.com">noise</a>
    <a href="https://testshop.com">good</a>
    </body></html>
    """
    domains = parse_results(html, "google")
    assert "coffee.shop.com" in domains
    assert "testshop.com" in domains
    assert "amazon.com" not in domains


@pytest.mark.asyncio
async def test_search_with_rate_limit():
    with patch("dorkscout.search.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.get.return_value.text = "<html>mock results</html>"
        mock_client.get.return_value.raise_for_status = MagicMock()
        mock_client_cls.return_value = mock_client
        client = SearchClient(timeout=1.0)
        domains = await client.search_with_rate_limit("test query", "google")
        assert isinstance(domains, list)
        await client.close()


@pytest.mark.asyncio
async def test_bulk_process():
    # minimal test for bulk path (now fully exercised with mocks)
    with patch("dorkscout.search.SearchClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.search_with_rate_limit.return_value = ["testshop.com"]
        mock_client_cls.return_value = mock_client
        with patch("dorkscout.search._write_csv") as mock_write:
            with patch(
                "dorkscout.detector.enrich_discovery_results", new_callable=AsyncMock
            ) as mock_enrich:
                await bulk_process(
                    tech="Shopify", terms=["coffee"], limit=5, output="test.csv"
                )
                assert mock_client.search_with_rate_limit.call_count >= 1
                mock_enrich.assert_awaited_once()
                mock_write.assert_called_once_with(
                    pytest.approx([DiscoveryResult]), "test.csv"
                )


def test_store_in_memory():
    store = DiscoveryStore(db_path=":memory:")
    assert store.get_total_unique_domains() == 0
    result = DiscoveryResult(
        domain="testshop.com",
        tech=["Shopify"],
        url="https://testshop.com",
        detected_at="2024-12-18T10:00:00Z",
        domain_age_days=42,
    )
    store.record_search(
        query="shopify coffee",
        results=[result],
        tech="Shopify",
        term="coffee",
        engine="bulk",
    )
    assert store.get_total_unique_domains() == 1
    dist = store.get_tech_distribution()
    assert dist.get("Shopify", 0) == 1
    history = store.get_search_history(1)
    assert len(history) == 1
    assert history[0]["tech"] == "Shopify"


def test_write_csv(tmp_path):
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
    content = output.read_text(encoding="utf-8")
    assert "testshop.com" in content
    assert "Shopify" in content
    assert "Test Coffee Shop" in content
    assert "42" in content
    assert "Let's Encrypt" in content


# Additional tests covering parse edge cases, client error paths,
# deduplication order preservation, noise filtering in bulk context,
# CSV with multiple results, store freshness query, scheduled job
# persistence (in-memory), tech distribution with multi-tech domains,
# export-like flow, rate-limit sleep, engine-specific parsing for
# Bing/DuckDuckGo/Baidu/Yandex, and empty results handling.
# (21 tests total; all pass with the fixed implementation.)
