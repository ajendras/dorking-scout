"""Tests for search parsing, deduplication, detection and store."""

from datetime import datetime, timezone

import pytest

from dorkscout.detector import detect_technologies
from dorkscout.models import DiscoveryResult
from dorkscout.search import parse_results, SearchClient
from dorkscout.store import DiscoveryStore


def test_parse_results_google():
    html = '<html><div class="g"><a href="https://examplebooks.com"></a></div></html>'
    domains = parse_results("google", html)
    assert "examplebooks.com" in domains


def test_parse_results_duckduckgo():
    """Test the DDG parser with redirect link containing shop.example.com."""
    html = """
    <html><body>
    <a href="https://duckduckgo.com/l/?uddg=https%3A%2F%2Fshop.example.com%2F&amp;rut=...">Shop</a>
    <a href="https://noise.amazon.com">Noise</a>
    </body></html>
    """
    domains = parse_results("duckduckgo", html)
    assert "shop.example.com" in domains
    assert "amazon.com" not in domains  # noise filtered


def test_parse_results_baidu():
    html = '<html><a href="http://testshoes.com">shoes</a></html>'
    domains = parse_results("baidu", html)
    assert "testshoes.com" in domains


def test_detect_technologies():
    html = '<script src="https://js.stripe.com/v3/"></script><div>powered by shopify</div>'
    tech = detect_technologies(html)
    assert "Stripe" in tech
    assert "Shopify" in tech


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

    history = store.get_search_history(5)
    assert len(history) >= 1
    assert history[0]["query"] == '"bookstore" "add to cart"'

    unique = store.get_unique_domains(10)
    assert len(unique) >= 1
    assert unique[0]["domain"] == "testbooks.com"
    assert "Books" in unique[0]["techs"]

    total = store.get_total_unique_domains()
    assert total >= 1

    store.close()
