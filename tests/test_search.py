"""Tests for search result parsing, domain extraction and noise filtering.

Uses mocked search engine HTML to verify parse_results() and the internal
_is_valid_domain logic without requiring live network calls.
"""

import pytest

from dorkscout.search import SearchClient, parse_results


# Realistic mock HTML snippets that mimic real search engine output
MOCK_GOOGLE_HTML = """
<!DOCTYPE html>
<html>
<head><title>Search</title></head>
<body>
<div class="g">
    <a href="/url?q=https://coffeestore.example.com/checkout&amp;sa=U">Coffee Store</a>
    <a href="/url?q=https://giftcards.test.com/buy">Gift Cards Shop</a>
    <a href="https://amazon.com">Amazon Marketplace</a>
    <a href="/url?q=https://stripebooks.example.com">Stripe Books</a>
    <a href="https://www.myshopify.com">Shopify demo</a>
    <a href="https://blogspot.com/example">Blogspot noise</a>
</div>
</body>
</html>
"""

MOCK_BING_HTML = """
<html>
<body>
<li class="b_algo">
    <a href="https://examplebooks.com/store">Example Books</a>
</li>
<a href="https://ebay.com">eBay</a>
<a href="https://test-gifts.com/egift">Test Gifts</a>
<a href="https://facebook.com">Facebook noise</a>
</body>
</html>
"""

MOCK_DDG_HTML = """
<html>
<body>
<a class="result__a" href="https://duckduckgo.com/l/?uddg=https%3A%2F%2Fshoesdirect.com%2Fbuy">Shoes Direct</a>
<a href="//duckduckgo.com">Internal DDG link</a>
<a href="https://square.site">Square noise</a>
</body>
</html>
"""


def test_parse_results_google():
    """Google parser extracts clean second-level domains and removes noise."""
    domains = parse_results(MOCK_GOOGLE_HTML, "google")
    assert "coffeestore.example.com" in domains
    assert "giftcards.test.com" in domains
    assert "stripebooks.example.com" in domains
    assert "amazon.com" not in domains
    assert "myshopify.com" not in domains
    assert "blogspot.com" not in domains
    assert domains == sorted(domains)


def test_parse_results_bing():
    """Bing parser uses its specific link selectors."""
    domains = parse_results(MOCK_BING_HTML, "bing")
    assert "examplebooks.com" in domains
    assert "test-gifts.com" in domains
    assert "ebay.com" not in domains
    assert "facebook.com" not in domains


def test_parse_results_duckduckgo():
    """DuckDuckGo parser handles uddg redirect parameter."""
    domains = parse_results(MOCK_DDG_HTML, "duckduckgo")
    assert "shoesdirect.com" in domains
    assert "square.site" not in domains


def test_parse_results_fallback_engine():
    """Generic parser works for unknown engines (e.g. Yandex)."""
    html = '<a href="https://generic.example.com/page">Link</a>'
    domains = parse_results(html, "yandex")
    assert "generic.example.com" in domains


def test_parse_results_empty_or_small_html_returns_empty_list():
    """Empty or very small HTML yields no domains."""
    assert parse_results("") == []
    assert parse_results("<html><body></body></html>") == []
    assert parse_results("too short") == []


def test_parse_results_deduplication():
    """Identical domains from multiple links appear only once."""
    html = """
    <a href="https://unique.example.com/1">1</a>
    <a href="https://unique.example.com/2">2</a>
    <a href="https://duplicate.example.com">Dup1</a>
    <a href="https://duplicate.example.com/other">Dup2</a>
    """
    domains = parse_results(html, "google")
    assert domains == ["duplicate.example.com", "unique.example.com"]
    assert len(domains) == 2


def test_is_valid_domain_logic():
    """Internal noise filter and validation rules behave as expected."""
    client = SearchClient()
    valid = [
        "indiebookstore.com",
        "sneakerboutique.co.uk",
        "paywithstripe.example.org",
        "handmadegifts.net",
    ]
    invalid = [
        "amazon.com",
        "ebay.com",
        "etsy.com",
        "myshopify.com",
        "square.site",
        "google.com",
        "facebook.com",
        "blogspot.com",
        "wixsite.com",
        "1.2.3.4.com",  # numeric start
        "short",
        "com",
        "example.blogspot.com",
    ]
    for domain in valid:
        assert client._is_valid_domain(domain), f"{domain} should be valid"
    for domain in invalid:
        assert not client._is_valid_domain(domain), f"{domain} should be invalid"


def test_searchclient_initialization():
    """SearchClient sets up engine configs and noise list correctly."""
    client = SearchClient(timeout=5.0)
    assert client.timeout == 5.0
    assert "google" in client.engine_configs
    assert "bing" in client.engine_configs
    assert len(client.noise_domains) > 15
    assert "amazon.com" in client.noise_domains
    assert "myshopify.com" in client.noise_domains


@pytest.mark.asyncio
async def test_search_with_rate_limit_smoke():
    """Smoke test for async search wrapper (minimal delay)."""
    client = SearchClient(timeout=3.0)
    try:
        # Use a safe delay override so test runs quickly
        domains = await client.search_with_rate_limit(
            "test query site:example.com", "bing", delay_override=0.01
        )
        assert isinstance(domains, list)
    finally:
        await client.close()
