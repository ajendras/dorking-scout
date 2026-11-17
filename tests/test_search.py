"""Tests for the search module including parsing, domain extraction, noise filtering
and async client behavior.
"""

import pytest
from unittest.mock import patch, AsyncMock
import httpx

from dorkscout.search import SearchClient, parse_results
from dorkscout.models import DiscoveryResult


# Test HTML fixtures for different search engines
GOOGLE_HTML = """
<!DOCTYPE html>
<html>
<body>
    <a href="/url?q=https://coffee.example.com&amp;sa=U">Coffee Shop</a>
    <a href="https://books.example.com">Book Store</a>
    <a href="https://yandex.ru/search">Yandex Link</a>
</body>
</html>
"""

BING_HTML = """
<html>
<body>
    <li class="b_algo">
        <a href="https://shoes.example.com">Shoes</a>
    </li>
    <a href="https://www.bing.com">Bing</a>
</body>
</html>
"""

DDG_HTML = """
<html>
<body>
    <a class="result__a" href="https://example.com">Result</a>
    <a href="//duckduckgo.com">DDG</a>
</body>
</html>
"""

BAIDU_HTML = """
<html>
<body>
    <a href="/link?url=https://gifts.example.com">Gift Cards</a>
</body>
</html>
"""

YANDEX_HTML = """
<html>
<body>
    <a href="https://payment.example.com">Payment</a>
    <a href="https://yandex.ru">Yandex</a>
</body>
</html>
"""


def test_parse_results_google():
    """Test Google result parsing with redirector links."""
    domains = parse_results(GOOGLE_HTML, "google")
    assert "coffee.example.com" in domains
    assert "books.example.com" in domains
    assert "yandex.ru" not in domains  # filtered by noise


def test_parse_results_bing():
    """Test Bing parser."""
    domains = parse_results(BING_HTML, "bing")
    assert "shoes.example.com" in domains
    assert "bing.com" not in domains


def test_parse_results_duckduckgo():
    """Test DuckDuckGo parser."""
    domains = parse_results(DDG_HTML, "duckduckgo")
    assert "example.com" in domains


def test_parse_results_baidu():
    """Test Baidu parser with redirect links."""
    domains = parse_results(BAIDU_HTML, "baidu")
    assert "gifts.example.com" in domains


def test_parse_results_yandex():
    """Test Yandex parser."""
    domains = parse_results(YANDEX_HTML, "yandex")
    assert "payment.example.com" in domains
    assert "yandex.ru" not in domains


def test_parse_results_empty_or_small_html():
    """Test guard against empty or too-small HTML."""
    assert parse_results("") == []
    assert parse_results("<html></html>") == []


def test_extract_domain_various_cases():
    """Test the internal domain extractor on various URL formats."""
    client = SearchClient()
    assert client._extract_domain("https://www.example.com/path?query=1") == "example.com"
    assert client._extract_domain("sub.domain.example.com") == "example.com"
    assert client._extract_domain("example.com") == "example.com"
    assert client._extract_domain("https://yandex.ru") == "yandex.ru"
    assert client._extract_domain("https://sub.yandex.ru/path") == "yandex.ru"
    assert client._extract_domain("not-a-url") is None
    assert client._extract_domain("https://localhost") is None
    assert client._extract_domain("192.168.1.1") is None


def test_is_valid_domain():
    """Test noise filtering and validity checks."""
    client = SearchClient()
    assert client._is_valid_domain("example.com") is True
    assert client._is_valid_domain("mycoffee.shop") is True
    assert client._is_valid_domain("google.com") is False
    assert client._is_valid_domain("yandex.ru") is False
    assert client._is_valid_domain("amazon.com") is False
    assert client._is_valid_domain("1.2.3.4") is False
    assert client._is_valid_domain("com") is False


def test_parse_results_deduplicates():
    """Test that duplicate domains are removed."""
    html = '<a href="https://example.com">1</a><a href="https://example.com/2">2</a>'
    domains = parse_results(html, "google")
    assert domains == ["example.com"]


@pytest.mark.asyncio
async def test_search_with_rate_limit():
    """Test the rate limited search dispatches to correct method."""
    client = SearchClient()
    with patch.object(client, "search_google", new_callable=AsyncMock) as mock_google:
        mock_google.return_value = ["test-domain.com"]
        domains = await client.search_with_rate_limit(
            "test query", "google", delay_override=0.01
        )
        assert domains == ["test-domain.com"]
        mock_google.assert_called_once_with("test query")


@pytest.mark.asyncio
async def test_search_yandex():
    """Test Yandex specific search method."""
    client = SearchClient()
    with patch.object(client, "search_yandex", new_callable=AsyncMock) as mock_yandex:
        mock_yandex.return_value = ["example.com"]
        domains = await client.search_with_rate_limit(
            "query", "yandex", delay_override=0.0
        )
        assert domains == ["example.com"]


def test_enrich_with_detection():
    """Test the (placeholder) enrichment with tech detection."""
    client = SearchClient()
    html_map = {
        "shop.example.com": "<html>shopify cdn</html>",
        "pay.example.com": "<html>stripe js</html>",
    }
    results = client.enrich_with_detection(
        ["shop.example.com", "pay.example.com"], html_map
    )
    assert len(results) == 2
    techs = [t for r in results for t in r.tech]
    assert "Shopify" in techs
    assert "Stripe" in techs


@pytest.mark.asyncio
async def test_client_error_handling():
    """Test that HTTP errors return empty list instead of crashing."""
    client = SearchClient()
    with patch.object(client.client, "get", side_effect=httpx.HTTPError("boom")):
        domains = await client.search_google("bad query")
        assert domains == []


def test_parse_results_fallback():
    """Test generic fallback parser."""
    html = '<a href="https://generic.example.com">Link</a>'
    domains = parse_results(html, "unknown")
    assert "generic.example.com" in domains


def test_extract_domain_edge_cases():
    """More edge cases for the domain extractor."""
    client = SearchClient()
    assert client._extract_domain("") is None
    assert client._extract_domain("https://localhost") is None
