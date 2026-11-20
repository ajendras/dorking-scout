"""Tests for search, parsing, detection and the new metadata enrichment."""

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from dorkscout.detector import (
    detect_technologies,
    enrich_discovery_results,
    enrich_metadata,
)
from dorkscout.models import DiscoveryResult
from dorkscout.search import NOISE_DOMAINS, SearchClient, parse_results


def test_parse_results_google():
    html = (
        '<html><body><a href="/url?q=https://coffeebooks.com">link</a>'
        '<a href="https://amazon.com">noise</a></body></html>'
    )
    domains = parse_results("google", html)
    assert "coffeebooks.com" in domains
    assert "amazon.com" not in domains


def test_parse_results_duckduckgo():
    html = '<a href="https://html.duckduckgo.com/lite/?uddg=https://examplebooks.com">link</a>'
    domains = parse_results("duckduckgo", html)
    assert "examplebooks.com" in domains


def test_parse_results_bing_baidu_yandex():
    html = '<a href="https://goodsite.com/store">good</a><a href="https://bing.com">noise</a>'
    for engine in ("bing", "baidu", "yandex"):
        domains = parse_results(engine, html)
        assert "goodsite.com" in domains


def test_noise_domains_filtered():
    html = '<a href="https://stripe.com">stripe</a><a href="https://indiebooks.com">indie</a>'
    domains = parse_results("google", html)
    assert "indiebooks.com" in domains
    for noise in NOISE_DOMAINS:
        assert noise not in domains


def test_detect_technologies():
    html = '<html><script src="https://js.stripe.com/v3/"></script>powered by shopify</html>'
    tech = detect_technologies(html)
    assert "Stripe" in tech
    assert "Shopify" in tech


def test_detect_technologies_empty():
    assert detect_technologies("") == []
    assert detect_technologies("short") == []


@pytest.mark.asyncio
async def test_enrich_metadata():
    mock_wayback = MagicMock()
    mock_wayback.is_success = True
    mock_wayback.json.return_value = [
        ["timestamp", "original"],
        ["20180315080000", "http://testbooks.com/"],
    ]

    mock_crt = MagicMock()
    mock_crt.is_success = True
    mock_crt.json.return_value = [
        {
            "not_before": "2022-01-01T00:00:00",
            "not_after": "2025-01-01T00:00:00",
            "issuer_name": 'C=US, O="Let\'s Encrypt", CN=Let\'s Encrypt Authority X3',
        },
        {
            "not_before": "2023-06-01T00:00:00",
            "not_after": "2024-06-01T00:00:00",
            "issuer_name": 'C=US, O="Let\'s Encrypt", CN=Let\'s Encrypt Authority X3',
        },
    ]

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.side_effect = [mock_wayback, mock_crt]

        meta = await enrich_metadata("testbooks.com")
        assert meta["domain_age_days"] is not None
        assert meta["domain_age_days"] > 1000
        assert meta["creation_date"] == "2018-03-15"
        assert meta["ssl_issuer"] is not None
        assert "Let's Encrypt" in meta["ssl_issuer"]
        assert meta["ssl_valid_from"] is not None
        assert meta["ssl_valid_to"] is not None


@pytest.mark.asyncio
async def test_enrich_discovery_results():
    results = [
        DiscoveryResult(
            domain="example.com",
            tech=["Stripe"],
            detected_at=datetime.now(timezone.utc).isoformat(),
        ),
        DiscoveryResult(
            domain="books.test",
            tech=["Books"],
            detected_at=datetime.now(timezone.utc).isoformat(),
        ),
    ]

    with patch("dorkscout.detector.enrich_metadata", new_callable=AsyncMock) as mock_meta:
        mock_meta.return_value = {
            "domain_age_days": 2345,
            "creation_date": "2017-11-03",
            "ssl_issuer": "Let's Encrypt",
            "ssl_valid_from": "2023-11-03",
            "ssl_valid_to": "2024-11-03",
        }
        await enrich_discovery_results(results)

        assert results[0].domain_age_days == 2345
        assert results[0].ssl_issuer == "Let's Encrypt"
        assert results[1].creation_date == "2017-11-03"


@pytest.mark.asyncio
async def test_search_client_rate_limit():
    client = SearchClient(timeout=2.0)
    with patch.object(client.client, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value.text = "<html><a href='https://test.com'>link</a></html>"
        mock_get.return_value.raise_for_status = MagicMock()

        domains = await client.search_with_rate_limit("test query", "google", delay_override=0.01)
        assert len(domains) > 0
    await client.close()


def test_search_client_noise_filter():
    client = SearchClient()
    html = '<a href="https://amazon.com">bad</a><a href="https://indieshoe.com">good</a>'
    domains = parse_results("google", html)
    assert "indieshoe.com" in domains
    assert "amazon.com" not in domains
    assert not any(n in d for d in domains for n in NOISE_DOMAINS if n != d)
