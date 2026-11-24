import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from dorkscout.search import (
    parse_results,
    is_noise_domain,
    SearchClient,
    bulk_process,
)


def test_parse_results_basic():
    html = """
    <html><body>
    <a href="https://example.com/store">link</a>
    <a href="/url?q=https://testshop.com/page">google link</a>
    <a href="https://amazon.com/bad">noise</a>
    <a href="https://coffee-niche.example.org">niche</a>
    </body></html>
    """
    results = parse_results(html, "google")
    assert "example.com" in results
    assert "testshop.com" in results
    assert "amazon.com" not in results


def test_parse_results_baidu_style():
    html = '<a href="http://testbooks.com">baidu result</a>'
    results = parse_results(html, "baidu")
    assert "testbooks.com" in results


def test_is_noise_domain():
    assert is_noise_domain("amazon.com")
    assert is_noise_domain("example.amazon.com")
    assert not is_noise_domain("indiecoffee.shop")
    assert not is_noise_domain("mynewtech.store")


@pytest.mark.asyncio
async def test_search_client_rate_limit():
    with patch("dorkscout.search.httpx.AsyncClient") as mock_client:
        mock_resp = MagicMock()
        mock_resp.text = '<a href="https://testshop.com">result</a>'
        mock_resp.raise_for_status = MagicMock()
        mock_instance = AsyncMock()
        mock_instance.get.return_value = mock_resp
        mock_client.return_value = mock_instance

        from dorkscout.search import SearchClient

        client = SearchClient(timeout=1.0)
        domains = await client.search_with_rate_limit('"powered by shopify" coffee', "google")
        assert len(domains) > 0


@pytest.mark.asyncio
async def test_bulk_process():
    # minimal test for bulk path
    with patch("dorkscout.search.SearchClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.search_with_rate_limit.return_value = ["testshop.com"]
        mock_client_cls.return_value = mock_client
        with patch("dorkscout.search._write_csv") as mock_write:
            await bulk_process(tech="Shopify", terms=["coffee"], limit=5, output="test.csv")
            mock_write.assert_called_once()


def test_parse_results_edge_cases():
    assert parse_results("") == []
    assert parse_results("<html>no links</html>") == []
    assert parse_results('<a href="https://github.com">noise</a>', "google") == []
