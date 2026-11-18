import pytest

from dorkscout.search import parse_results, SearchClient


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


def test_extract_domain_various_cases():
    client = SearchClient()
    assert client._extract_domain("https://www.example.com/path") == "example.com"
    assert client._extract_domain("https://sub.domain.co.uk") == "sub.domain.co.uk"
    assert client._extract_domain("http://localhost:8000") is None
    assert client._extract_domain("192.168.0.1") is None
    assert client._extract_domain("not-a-domain") is None


def test_parse_google_redirect():
    html = """
    <a href="/url?q=https://examplebooks.com/checkout&amp;sa=U">Example Books</a>
    """
    domains = parse_results(html, "google")
    assert "examplebooks.com" in domains


def test_parse_bing_results():
    html = """
    <li class="b_algo"><a href="https://coffeestore.com">Coffee Store</a></li>
    """
    domains = parse_results(html, "bing")
    assert "coffeestore.com" in domains


def test_parse_duckduckgo_uddg():
    html = """
    <a class="result__a" href="https://duckduckgo.com/l/?uddg=https%3A%2F%2Fpayexample.com%2Fcart">Pay Example</a>
    """
    domains = parse_results(html, "duckduckgo")
    assert "payexample.com" in domains


def test_noise_domains_are_filtered():
    html = """
    <a href="https://amazon.com">Amazon</a>
    <a href="https://stripecoffee.com">Stripe Coffee</a>
    """
    domains = parse_results(html, "google")
    assert "amazon.com" not in domains
    assert "stripecoffee.com" in domains


def test_empty_or_short_html_returns_empty_list():
    assert parse_results("") == []
    assert parse_results("   ") == []
    assert parse_results("<html></html>") == []


def test_parse_results_with_malformed_url():
    html = '<a href="not-a-url">Bad</a><a href="https://goodsite.com">Good</a>'
    domains = parse_results(html, "google")
    assert "goodsite.com" in domains
    assert len(domains) == 1
