"""Tests for search parsing, client, store and analytics."""

import pytest
from unittest.mock import patch

from dorkscout.search import parse_results, SearchClient
from dorkscout.store import DiscoveryStore


def test_parse_results_extracts_domains():
    sample_html = """
    <html>
    <div class="g"><a href="https://shop.example.com/page"><h3>Title</h3></a></div>
    <div class="g"><a href="https://www.example.com"><h3>Another</h3></a></div>
    </html>
    """
    domains = parse_results(sample_html, engine="google")
    assert "shop.example.com" in domains
    assert "example.com" in domains


def test_parse_results_handles_bing():
    bing_html = '<li><a href="https://test.com">Test</a></li>'
    domains = parse_results(bing_html, engine="bing")
    assert "test.com" in domains


def test_store_scheduled_jobs():
    store = DiscoveryStore(in_memory=True)
    job_id = "test-uuid-1234"
    returned_id = store.add_scheduled_job(
        job_id=job_id,
        name="Test Job",
        tech="PayPal",
        term="gift",
        interval_hours=24,
        alert_type="webhook",
        target="https://example.com/webhook",
    )
    assert returned_id == job_id
    jobs = store.get_scheduled_jobs()
    assert len(jobs) == 1
    assert jobs[0]["job_id"] == job_id
    assert jobs[0]["tech"] == "PayPal"
    store.close()


def test_tech_distribution():
    store = DiscoveryStore(in_memory=True)
    # simulate some data
    from dorkscout.models import DiscoveryResult
    from datetime import datetime, timezone
    results = [
        DiscoveryResult(
            domain="shop1.com",
            tech=["Stripe", "Shopify"],
            url="https://shop1.com",
            detected_at=datetime.now(timezone.utc).isoformat(),
        ),
        DiscoveryResult(
            domain="shop2.com",
            tech=["PayPal"],
            url="https://shop2.com",
            detected_at=datetime.now(timezone.utc).isoformat(),
        ),
    ]
    store.record_search("test query", results, tech="Stripe")
    dist = store.get_tech_distribution()
    assert dist["Stripe"] >= 1
    assert dist.get("Shopify", 0) >= 1
    store.close()


__all__ = ["test_parse_results_extracts_domains"]
