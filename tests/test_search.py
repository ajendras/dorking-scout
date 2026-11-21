"""Tests for search functionality and result parsing."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

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
        assert "bing.com" not in domains


def test_noise_domains_filtered():
    html = '<a href="https://stripe.com">stripe</a><a href="https://indiebooks.com">indie</a>'
    domains = parse_results("google", html)
    assert "indiebooks.com" in domains
    assert "stripe.com" not in domains


@pytest.mark.asyncio
async def test_search_client_rate_limit():
    client = SearchClient(timeout=2.0)
    with patch.object(client.client, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value.text = "<html><a href='https://test.com'>link</a></html>"
        mock_get.return_value.raise_for_status = MagicMock()

        domains = await client.search_with_rate_limit(
            "test query", "google", delay_override=0.01
        )
        assert len(domains) > 0
        assert "test.com" in domains
    await client.close()


def test_search_client_noise_filter():
    client = SearchClient()
    html = '<a href="https://amazon.com">bad</a><a href="https://indieshoe.com">good</a>'
    domains = parse_results("google", html)
    assert "indieshoe.com" in domains
    assert "amazon.com" not in domains


# Tests for updated parsing behaviour (noise filtering, subdomain handling, edge cases)
def test_parse_results_empty():
    assert parse_results("google", "") == []
    assert parse_results("google", "<html><body>no links</body></html>") == []


def test_parse_results_only_noise():
    html = '<a href="https://amazon.com">amz</a><a href="https://stripe.com">str</a>'
    assert parse_results("google", html) == []


def test_parse_results_subdomain():
    html = '<a href="https://blog.coffeebooks.com">link</a>'
    domains = parse_results("google", html)
    assert "coffeebooks.com" in domains


def test_noise_domains_set():
    assert len(NOISE_DOMAINS) >= 10
    assert "amazon.com" in NOISE_DOMAINS
    assert "stripe.com" in NOISE_DOMAINS


@pytest.mark.asyncio
async def test_search_client_close():
    client = SearchClient(timeout=1.0)
    await client.close()
