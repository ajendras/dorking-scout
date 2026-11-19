"""Tests for search functionality, parsing, deduplication and the persistent store.

Covers mocked engine HTML parsing, noise filtering, domain extraction,
SearchClient rate-limit wrapper and the new DiscoveryStore (in-memory mode,
record_search, history retrieval and unique domain accumulation).
"""

import asyncio
import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from dorkscout.models import DiscoveryResult
from dorkscout.search import SearchClient, parse_results
from dorkscout.store import DiscoveryStore


# Mock HTML snippets for different engines
GOOGLE_HTML = """<html><body>
<div class="g"><a href="https://coffee.example.com">Coffee Shop</a></div>
<div class="g"><a href="https://test.com">Test</a></div>
</body></html>"""

BING_HTML = """<html><body>
<li class="b_algo"><a href="https://books.example.com">Book Store</a></li>
</body></html>"""

DUCK_HTML = """<html><body>
<a class="result__a" href="https://https://shop.example.com">Shop</a>
</body></html>"""


def test_parse_results_google():
    domains = parse_results(GOOGLE_HTML, "google")
    assert "coffee.example.com" in domains
    assert "test.com" in domains


def test_parse_results_bing():
    domains = parse_results(BING_HTML, "bing")
    assert "books.example.com" in domains


def test_parse_results_duckduckgo():
    domains = parse_results(DUCK_HTML, "duckduckgo")
    assert "shop.example.com" in domains


def test_noise_domains_filtered():
    client = SearchClient()
    html = '<a href="https://amazon.com">Amazon</a><a href="https://example.com">Example</a>'
    domains = client.parse_results(html, "google")
    assert "amazon.com" not in domains
    assert "example.com" in domains


def test_extract_domain_various_cases():
    client = SearchClient()
    assert client._extract_domain("https://www.example.com/path") == "example.com"
    assert client._extract_domain("https://sub.test.co.uk") == "sub.test.co.uk"
    assert client._extract_domain("not-a-domain") is None
    assert client._extract_domain("https://localhost") is None
    assert client._extract_domain("https://192.168.1.1") is None


def test_is_valid_domain():
    client = SearchClient()
    assert client._is_valid_domain("example.com") is True
    assert client._is_valid_domain("amazon.com") is False
    assert client._is_valid_domain("google.com") is False
    assert client._is_valid_domain("short") is False


@pytest.mark.asyncio
async def test_search_with_rate_limit():
    client = SearchClient(timeout=1.0)
    with patch.object(client, "search_google", new_callable=AsyncMock) as mock_search:
        mock_search.return_value = ["test.com"]
        domains = await client.search_with_rate_limit(
            '"test query"', "google", delay_override=0.01
        )
        assert domains == ["test.com"]
        mock_search.assert_called_once_with('"test query"')


def test_discovery_store_in_memory():
    """Test the new persistent store in in-memory mode."""
    store = DiscoveryStore(":memory:")

    result = DiscoveryResult(
        domain="testbooks.com",
        tech=["Books", "Stripe"],
        url="https://testbooks.com",
        detected_at=datetime.now(timezone.utc).isoformat(),
        title="Test Books Store",
    )

    sid = store.record_search(
        query='"bookstore" "add to cart"',
        results=[result],
        category="niches",
        tech=None,
        term="books",
        engine="google",
    )
    assert sid > 0

    history = store.get_search_history(10)
    assert len(history) >= 1
    assert history[0]["term"] == "books"
    assert history[0]["results_count"] == 1
    assert history[0]["query"] == '"bookstore" "add to cart"'

    domains = store.get_unique_domains(10)
    assert len(domains) == 1
    assert domains[0]["domain"] == "testbooks.com"
    assert "Books" in domains[0]["techs"]
    assert domains[0]["seen_count"] == 1

    assert store.get_total_unique_domains() == 1

    # Second sighting updates count and last_seen but preserves first_seen
    result2 = DiscoveryResult(
        domain="testbooks.com",
        tech=["Books"],
        url="https://testbooks.com",
        detected_at=datetime.now(timezone.utc).isoformat(),
    )
    store.record_search(
        query="second query",
        results=[result2],
        category="niches",
        term="books",
    )

    domains2 = store.get_unique_domains(10)
    assert domains2[0]["seen_count"] == 2
    assert "Books" in domains2[0]["techs"]

    # Tech filter works on JSON content
    filtered = store.get_unique_domains(tech="Stripe")
    assert len(filtered) == 0  # second record overwrote with only Books


def test_store_with_real_path(tmp_path):
    """Test store with a real file path (uses tmp_path fixture)."""
    db_file = tmp_path / "test.db"
    store = DiscoveryStore(db_file)

    result = DiscoveryResult(domain="example.com", tech=["Shopify"])
    store.record_search("test query", [result], term="test")

    assert store.get_total_unique_domains() == 1
    history = store.get_search_history(5)
    assert len(history) == 1
