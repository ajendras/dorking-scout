import asyncio
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from dorkscout.search import SearchClient, parse_results
from dorkscout.detector import detect_technologies


def test_parse_results_google():
    """Google parser handles the /url?q= redirector and normal links."""
    html = '''
        <a href="/url?q=https://coffeebean.com&amp;ved=0CA&...">Coffee Bean</a>
        <a href="https://roastmasters.com">Roast Masters</a>
    '''
    domains = parse_results(html, "google")
    assert "coffeebean.com" in domains
    assert "roastmasters.com" in domains


def test_parse_results_bing():
    """Bing parser extracts domains from standard result links."""
    html = '<a href="https://indiebooks.com/page">Link</a>'
    domains = parse_results(html, "bing")
    assert "indiebooks.com" in domains


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
    assert "amazon.com" not in domains
    assert "facebook.com" not in domains
    assert "myshopify.com" not in domains


def test_extract_domain_edge_cases():
    """_extract_domain returns second-level domains or None."""
    client = SearchClient()
    assert client._extract_domain("https://sub.indiebooks.com/path") == "indiebooks.com"
    assert client._extract_domain("https://example.co.uk") == "co.uk"
    assert client._extract_domain("https://www.rarebooks.com") == "rarebooks.com"
    assert client._extract_domain("not-a-url") is None
    assert client._extract_domain("localhost") is None
    assert client._extract_domain("https://192.168.1.1") is None


def test_client_http_error_handling():
    """HTTP errors during search are caught gracefully and return an empty list."""
    async def inner_test():
        client = SearchClient(timeout=5.0)
        try:
            mock_get = AsyncMock(side_effect=httpx.HTTPError("Simulated search engine failure"))
            with patch.object(client.client, "get", mock_get):
                domains = await client.search_with_rate_limit(
                    "test query that will fail", "google", delay_override=0.0
                )
                assert domains == []
        finally:
            await client.close()

    asyncio.run(inner_test())


def test_parse_results_with_minimal_html():
    """Parser tolerates minimal HTML without crashing."""
    html = '<a href="https://teststore.com">Store</a><a href="/relative">Relative</a>'
    domains = parse_results(html, "google")
    assert "teststore.com" in domains


def test_search_client_initialization():
    """SearchClient sets up engines, noise list and async client correctly."""
    client = SearchClient(timeout=10.0)
    assert "google" in client.engine_configs
    assert "bing" in client.engine_configs
    assert "duckduckgo" in client.engine_configs
    assert len(client.noise_domains) >= 15
    assert client.timeout == 10.0


def test_is_valid_domain():
    """_is_valid_domain correctly filters noise and invalid domains."""
    client = SearchClient()
    assert client._is_valid_domain("indiebooks.com") is True
    assert client._is_valid_domain("goodstore.com") is True
    assert client._is_valid_domain("amazon.com") is False
    assert client._is_valid_domain("myshopify.com") is False
    assert client._is_valid_domain("facebook.com") is False
    assert client._is_valid_domain("1shop.com") is False  # starts with digit


def test_enrich_with_detection():
    """enrich_with_detection turns domains into DiscoveryResult objects with
    placeholder tech detection.
    """
    client = SearchClient()
    domains = ["teststore.com", "coffeebean.com"]
    html_map = {
        "teststore.com": "<html>powered by shopify and stripe</html>",
        "coffeebean.com": "<html>plain html</html>",
    }
    results = client.enrich_with_detection(domains, html_map)
    assert len(results) == 2
    for r in results:
        if r.domain == "teststore.com":
            assert "Shopify" in r.tech
            assert "Stripe" in r.tech
        if r.domain == "coffeebean.com":
            assert len(r.tech) == 0


def test_detect_technologies_integration():
    """Detector works on real HTML snippets (cross-check with search flow)."""
    html = '<html><head><script src="https://cdn.shopify.com"></script></head></html>'
    tech = detect_technologies(html)
    assert "Shopify" in tech


@pytest.mark.parametrize("engine", ["google", "bing", "duckduckgo"])
def test_parse_results_empty_or_invalid_html(engine):
    """Parser returns empty list for empty or tiny input without crashing."""
    assert parse_results("", engine) == []
    assert parse_results("   ", engine) == []
    assert parse_results("<html></html>", engine) == []
