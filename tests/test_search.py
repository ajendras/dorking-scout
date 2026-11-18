"""Tests for search parsing, noise filtering and domain extraction.

Uses the synchronous parse_results helper so tests stay fast and offline.
Covers Google, Bing, DuckDuckGo, Baidu and Yandex result shapes, noise-domain
removal, and edge cases. The async SearchClient methods are exercised only
for initialisation and private helpers that are stable.
"""

from dorkscout.search import SearchClient, parse_results


def test_parse_results_empty_or_tiny_html_returns_empty_list():
    assert parse_results("") == []
    assert parse_results("   ") == []
    assert parse_results("<html><body></body></html>") == []


def test_parse_google_url_redirector():
    html = """
    <a href="/url?q=https://coffeestore.com&amp;sa=U&amp;ved=2ah">Coffee Store</a>
    <a href="https://www.examplebooks.com">Example Books</a>
    """
    domains = parse_results(html, "google")
    assert "coffeestore.com" in domains
    assert "examplebooks.com" in domains


def test_parse_bing_link_extraction():
    html = """
    <li class="b_algo">
        <a href="https://testshop.example.com/cart">Test Shop</a>
    </li>
    """
    domains = parse_results(html, "bing")
    assert "testshop.example.com" in domains


def test_parse_duckduckgo_uddg_redirect():
    html = """
    <a class="result__a" href="https://duckduckgo.com/l/?uddg=https%3A%2F%2Fshop.example.net%2F">
        Shop
    </a>
    """
    domains = parse_results(html, "duckduckgo")
    assert "shop.example.net" in domains


def test_parse_baidu_link_url_redirect():
    html = """
    <a href="http://www.baidu.com/link?url=abc123&amp;wd=test">https://baidushop.com</a>
    """
    domains = parse_results(html, "baidu")
    assert "baidushop.com" in domains


def test_parse_yandex_redirect_parameter():
    html = """
    <a href="https://yandex.com/search?url=https://yandexstore.com/checkout">Store</a>
    """
    domains = parse_results(html, "yandex")
    assert "yandexstore.com" in domains


def test_noise_domains_are_filtered():
    html = """
    <a href="https://amazon.com">Amazon</a>
    <a href="https://ebay.com">eBay</a>
    <a href="https://goodindependentbookstore.com">Good Bookstore</a>
    <a href="https://myshopify.com">Shopify marketplace</a>
    """
    domains = parse_results(html, "google")
    assert "amazon.com" not in domains
    assert "ebay.com" not in domains
    assert "myshopify.com" not in domains
    assert "goodindependentbookstore.com" in domains


def test_client_initialisation_and_noise_set():
    client = SearchClient(timeout=5.0)
    assert client.timeout == 5.0
    assert len(client.noise_domains) > 15
    assert "amazon.com" in client.noise_domains
    assert "google.com" in client.noise_domains
    assert "myshopify.com" in client.noise_domains


def test_extract_domain_various_cases():
    client = SearchClient()
    assert client._extract_domain("https://www.example.com/path") == "example.com"
    assert client._extract_domain("https://sub.domain.co.uk") == "sub.domain.co.uk"
    assert client._extract_domain("http://localhost:8000") is None
    assert client._extract_domain("192.168.0.1") is None
    assert client._extract_domain("not-a-domain") is None
    assert client._extract_domain("https://www.example.com:443") == "example.com"


def test_is_valid_domain_rejects_numeric_and_short_hosts():
    client = SearchClient()
    assert not client._is_valid_domain("123.com")
    assert not client._is_valid_domain("a.b")
    assert client._is_valid_domain("realshop.example.com")
