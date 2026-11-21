"""Search client and result parser for Dork Scout.

Provides async rate-limited search across multiple engines (Google, Bing,
DuckDuckGo, Baidu, Yandex). Uses BeautifulSoup to parse result pages and
extract clean second-level domains while filtering marketplace and noise
domains. The SearchClient manages per-engine rate limits using timestamps.
"""

import asyncio
import time
from typing import Dict, List, Optional

import httpx
from bs4 import BeautifulSoup
from urllib.parse import parse_qs, quote, unquote, urlparse

from .config import get_search_engines

NOISE_DOMAINS: set[str] = {
    "amazon.com",
    "amazon.co.uk",
    "ebay.com",
    "ebay.co.uk",
    "aliexpress.com",
    "stripe.com",
    "paypal.com",
    "square.com",
    "squareup.com",
    "shopify.com",
    "bigcommerce.com",
    "woocommerce.com",
    "magento.com",
    "bing.com",
    "google.com",
    "duckduckgo.com",
    "baidu.com",
    "yandex.com",
    "yandex.ru",
    "yahoo.com",
    "facebook.com",
    "twitter.com",
    "x.com",
    "instagram.com",
    "linkedin.com",
    "github.com",
    "gitlab.com",
    "bitbucket.org",
}


def _extract_domain_from_url(url: str) -> str:
    """Extract the second-level domain from a URL string.

    Handles Google redirectors, DuckDuckGo uddg params, and direct links.
    Returns empty string on failure.
    """
    if not url:
        return ""

    original_url = url
    # Handle Google /url?q= redirect
    if "/url?" in url:
        parsed = urlparse(url)
        qs = parse_qs(parsed.query)
        if "q" in qs and qs["q"]:
            url = qs["q"][0]

    # Handle DuckDuckGo uddg redirect
    if "uddg=" in original_url:
        parsed = urlparse(original_url)
        qs = parse_qs(parsed.query)
        if "uddg" in qs and qs["uddg"]:
            uddg_val = qs["uddg"][0]
            if "%" in uddg_val:
                uddg_val = unquote(uddg_val)
            if uddg_val.startswith(("http://", "https://", "//")):
                url = uddg_val
            else:
                url = f"https://{uddg_val}"

    # Ensure we have a full URL for parsing
    if not url.startswith(("http://", "https://", "//")):
        url = f"https://{url}"

    parsed = urlparse(url)
    hostname = parsed.hostname
    if not hostname:
        return ""

    hostname = hostname.lower()
    if hostname.startswith("www."):
        hostname = hostname[4:]

    # Return registrable domain (last two parts)
    parts = hostname.split(".")
    if len(parts) >= 2:
        return ".".join(parts[-2:])
    return hostname


def parse_results(engine: str, html: str) -> List[str]:
    """Parse HTML search results from a given engine and return list of unique
    clean domains.

    Filters out known noise domains (marketplaces, big tech, etc.). The
    function is deliberately tolerant of minimal test HTML snippets used
    in tests/test_search.py.
    """
    if not isinstance(html, str) or len(html.strip()) == 0:
        return []

    soup = BeautifulSoup(html, "html.parser")
    domains: List[str] = []
    seen: set[str] = set()
    for a_tag in soup.find_all("a", href=True):
        href = str(a_tag.get("href", "")).strip()
        if not href or any(
            href.startswith(p)
            for p in ("#", "mailto:", "tel:", "javascript:", "data:")
        ):
            continue

        domain = _extract_domain_from_url(href)
        if domain and domain not in seen and domain not in NOISE_DOMAINS:
            seen.add(domain)
            domains.append(domain)

    return domains


class SearchClient:
    """Async HTTP client with per-engine rate limiting and result parsing."""

    def __init__(self, timeout: float = 12.0) -> None:
        self.timeout = httpx.Timeout(timeout, connect=5.0)
        self.client = httpx.AsyncClient(
            timeout=self.timeout,
            headers={
                "User-Agent": "Mozilla/5.0 (compatible; DorkScout/0.1; +https://github.com/ajendras/dorking-scout)"
            },
            follow_redirects=True,
        )
        self.last_request: Dict[str, float] = {}

    async def search_with_rate_limit(
        self,
        query: str,
        engine: str = "google",
        delay_override: Optional[float] = None,
    ) -> List[str]:
        """Search with rate limiting per engine. Returns parsed domains.

        Uses the engine configuration from config.SEARCH_ENGINES and calls
        parse_results on the response HTML.
        """
        if not query or not isinstance(query, str):
            return []

        engine_key = engine.lower().strip()

        # Resolve engine configuration
        engines_config = get_search_engines()
        base_url = "https://www.google.com/search"
        q_param = "q"
        rate = 1.0
        for cfg in engines_config:
            name = cfg.get("name", "").lower()
            if name == engine_key or engine_key in name:
                base_url = cfg.get("base", base_url)
                q_param = cfg.get("query_param", q_param)
                rate = float(cfg.get("rate_limit", rate))
                break

        if delay_override is not None:
            rate = float(delay_override)

        # Rate-limit enforcement
        now = time.time()
        if engine_key in self.last_request:
            since_last = now - self.last_request[engine_key]
            if since_last < rate:
                await asyncio.sleep(rate - since_last)

        self.last_request[engine_key] = time.time()

        encoded = quote(query)
        search_url = f"{base_url}?{q_param}={encoded}"

        try:
            response = await self.client.get(search_url)
            response.raise_for_status()
            return parse_results(engine, response.text)
        except httpx.HTTPError:
            return []
        except Exception as exc:  # pylint: disable=broad-except
            print(f"Warning: search failed for {engine}: {exc}")
            return []

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self.client.aclose()


__all__ = ["SearchClient", "parse_results", "NOISE_DOMAINS"]
