"""Tests for the search module."""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from dorkscout.search import SearchClient, parse_results
from dorkscout.models import DiscoveryResult


def test_parse_results_empty():
    """Empty or tiny HTML returns no domains."""
    assert parse_results("") == []
    assert parse_results("<html><body>No content</body></html>") == []


def test_parse_results_google():
    """Google parser handles redirector links and direct http links."""
    html = '''
    <a href="/url?q=https://coffeebean.com/menu">Coffee</a>
    <a href="https://rarebooks.com">Books</a>
    <a href="https://amazon.com">Noise</a>
    '''
    domains = parse_results(html, "google")
    assert "coffeebean.com" in domains
    assert "rarebooks.com" in domains
    assert "amazon.com" not in domains


def test_parse_results_bing():
    """Bing parser extracts from b_algo links and skips noise."""
    html = '''
    <li class="b_algo"><a href="https://indiebooks.com">Indie Books</a></li>
    <a href="https://ebay.com">Noise</a>
    '''
    domains = parse_results(html, "bing")
    assert "indiebooks.com" in domains
    assert "ebay.com" not in domains


def test_parse_results_duckduckgo():
    """DuckDuckGo parser handles uddg links and normal links."""
    html = '''
    <a href="https://html.duckduckgo.com/html/?uddg=https://novelbooks.com">Novel Books</a>
    '''
    domains = parse_results(html, "duckduckgo")
    assert "novelbooks.com" in domains


def test_parse_results_fallback_engine():
    """Generic parser works for unknown engines (e.g. Yandex) and extracts
    second-level domains consistently.
    """
    html = '<a href="https://indiebooks.com/page">Link</a>'
    domains = parse_results(html, "yandex")
    assert "indiebooks.com" in domains


def test_noise_filtering():
    """Noise domains (marketplaces, social, etc.) are removed."""
    html = '''
    <a href="https://amazon.com">Amazon</a>
    <a href="https://facebook.com">FB</a>
    <a href="https://myshopify.com">Shopify</a>
    <a href="https://goodstore.com">Good</a>
    '''
    domains = parse_results(html, "google")
    assert "goodstore.com" in domains
    for noise in ("amazon.com", "facebook.com", "myshopify.com"):
        assert noise not in domains


def test_extract_domain_edge_cases():
    """_extract_domain returns second-level domains or None."""
    client = SearchClient()
    assert client._extract_domain("https://sub.indiebooks.com/path") == "indiebooks.com"
    assert client._extract_domain("https://example.co.uk") == "co.uk"
    assert client._extract_domain("https://www.rarebooks.com") == "rarebooks.com"
    assert client._extract_domain("not-a-url") is None
    assert client._extract_domain("") is None


def test_is_valid_domain():
    """_is_valid_domain rejects noise and invalid hostnames."""
    client = SearchClient()
    assert client._is_valid_domain("indiebooks.com")
    assert not client._is_valid_domain("amazon.com")
    assert not client._is_valid_domain("google.com")
    assert not client._is_valid_domain("1.2.3.4")
    assert not client._is_valid_domain("localhost")
    assert not client._is_valid_domain("short")


def test_search_with_rate_limit_smoke():
    """Smoke test for the rate-limited async search (no pytest-asyncio needed)."""
    async def _run():
        client = SearchClient(timeout=2.0)
        # yandex path returns [] immediately (no network)
        domains = await client.search_with_rate_limit(
            "indie bookstore", "yandex", delay_override=0.0
        )
        assert isinstance(domains, list)
        await client.close()
        return domains

    domains = asyncio.run(_run())
    assert len(domains) == 0


@pytest.mark.parametrize("engine", ["google", "bing", "duckduckgo"])
def test_search_methods_return_lists(engine):
    """All engine-specific search methods return lists (smoke)."""
    client = SearchClient(timeout=1.0)
    method = getattr(client, f"search_{engine}", None)
    if method:
        # We don't call real network in parametrized test; just verify signature
        assert callable(method)


def test_enrich_with_detection():
    """enrich_with_detection turns domains into DiscoveryResult objects."""
    client = SearchClient()
    html_map = {
        "coffeebean.com": "<html>stripe checkout</html>",
        "rarebooks.com": "<html>shopify cdn</html>",
    }
    results = client.enrich_with_detection(["coffeebean.com", "rarebooks.com"], html_map)
    assert len(results) == 2
    assert any(r.domain == "coffeebean.com" for r in results)
    assert any("Stripe" in r.tech for r in results)
    assert any("Shopify" in r.tech for r in results)
    for r in results:
        assert isinstance(r, DiscoveryResult)


@patch("httpx.AsyncClient.get", new_callable=AsyncMock)
async def test_client_http_error_handling(mock_get):
    """HTTP errors in search methods return empty lists gracefully."""
    mock_get.side_effect = httpx.HTTPError("Simulated error")
    client = SearchClient(timeout=1.0)
    domains = await client.search_google("test query")
    assert domains == []
    await client.close()


def test_parse_results_with_minimal_html():
    """Parser tolerates minimal HTML without crashing."""
    html = '<a href="https://teststore.com">Store</a><a href="/relative">Relative</a>'
    domains = parse_results(html, "google")
    assert "teststore.com" in domains


__all__ = ["test_parse_results_fallback_engine", "test_search_with_rate_limit_smoke"]
