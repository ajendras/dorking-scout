"""Tests for search parsing, detection, enrichment, and persistence."""

from unittest.mock import patch, AsyncMock, MagicMock
import asyncio

import httpx
import pytest

from dorkscout.search import SearchClient, parse_results
from dorkscout.detector import (
    detect_technologies,
    enrich_metadata,
    enrich_discovery_results,
    TECH_SIGNATURES,
)
from dorkscout.models import DiscoveryResult
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


def test_detect_technologies_matches_signatures():
    html = """
    <html>
    <head><title>Store</title></head>
    <body>
    <script src="https://js.stripe.com/v3/"></script>
    powered by shopify
    </body>
    </html>
    """
    techs = detect_technologies(html)
    assert "Stripe" in techs
    assert "Shopify" in techs
    assert len(techs) == 2


def test_detect_technologies_no_match():
    assert detect_technologies("<html><body>No tech here</body></html>") == []


def test_tech_signatures_are_valid():
    assert isinstance(TECH_SIGNATURES, dict)
    assert len(TECH_SIGNATURES) > 3
    for tech, rules in TECH_SIGNATURES.items():
        assert isinstance(rules, list)
        for rule in rules:
            assert "html" in rule or "header" in rule


@pytest.mark.asyncio
async def test_enrich_metadata():
    meta = await enrich_metadata("example.com")
    assert isinstance(meta, dict)
    assert "domain_age_days" in meta
    assert "ssl_issuer" in meta


@pytest.mark.asyncio
async def test_enrich_discovery_results():
    results = [
        DiscoveryResult(
            domain="example.com",
            tech=["Stripe"],
            url="https://example.com",
            detected_at="2024-12-18T10:00:00Z",
        ),
        DiscoveryResult(
            domain="test.com",
            tech=["Shopify"],
            url="https://test.com",
            detected_at="2024-12-18T10:00:00Z",
        ),
    ]
    with patch("dorkscout.detector.enrich_metadata", new_callable=AsyncMock) as mock_meta:
        mock_meta.return_value = {
            "domain_age_days": 365,
            "creation_date": "2023-12-18",
            "ssl_issuer": "Test CA",
            "ssl_valid_from": "2023-01-01",
            "ssl_valid_to": "2025-01-01",
        }
        await enrich_discovery_results(results)
        for r in results:
            assert r.domain_age_days == 365
            assert r.ssl_issuer == "Test CA"


def test_discovery_store_in_memory():
    store = DiscoveryStore(in_memory=True)
    results = [DiscoveryResult(
        domain="test.com",
        tech=["Stripe"],
        url="https://test.com",
        detected_at="2024-12-18T10:00:00Z"
    )]
    store.record_search(
        query="test query",
        results=results,
        category="payment_gateways",
        tech="Stripe",
        term="coffee",
        engine="google",
    )
    history = store.get_search_history(10)
    assert len(history) >= 1
    assert history[0]["query"] == "test query"
    domains = store.get_unique_domains(10)
    assert len(domains) >= 1
    assert any(d["domain"] == "test.com" for d in domains)
    total = store.get_total_unique_domains()
    assert total >= 1


def test_store_scheduled_jobs():
    store = DiscoveryStore(in_memory=True)
    job_id = "test-uuid-1234"
    store.add_scheduled_job(
        job_id=job_id,
        name="Test Job",
        tech="PayPal",
        term="gift",
        interval_hours=24,
        alert_type="webhook",
        target="https://example.com/webhook",
    )
    jobs = store.get_scheduled_jobs()
    assert any(j["id"] == job_id for j in jobs)
    store.delete_scheduled_job(job_id)
    jobs = store.get_scheduled_jobs()
    assert not any(j["id"] == job_id for j in jobs)
