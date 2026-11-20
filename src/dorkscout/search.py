"""Async search clients and HTML result parsers for Dork Scout.

Provides a rate-limit-aware httpx.AsyncClient that queries Google, Bing,
DuckDuckGo, Baidu and Yandex. Results are parsed with BeautifulSoup4,
deduplicated and filtered of common noise (marketplaces, search engines,
social platforms). The SearchClient is used by background workers and the
/api/search endpoint.
"""

import asyncio
import re
from typing import Dict, List, Optional
from urllib.parse import parse_qs, unquote, urlparse

import httpx
from bs4 import BeautifulSoup

from .config import get_search_engines

NOISE_DOMAINS: set[str] = {
    "amazon.com",
    "amazon.co.uk",
    "ebay.com",
    "ebay.co.uk",
    "etsy.com",
    "walmart.com",
    "target.com",
    "aliexpress.com",
    "facebook.com",
    "instagram.com",
    "twitter.com",
    "t.co",
    "linkedin.com",
    "pinterest.com",
    "youtube.com",
    "github.com",
    "gitlab.com",
    "wordpress.com",
    "blogspot.com",
    "medium.com",
    "quora.com",
    "reddit.com",
    "tumblr.com",
    "bing.com",
    "google.com",
    "duckduckgo.com",
    "baidu.com",
    "yandex.com",
    "yahoo.com",
    "search.yahoo.com",
}


def parse_results(html: str, engine: str = "google") -> List[str]:
    """Parse search engine result page HTML and extract unique clean domains.

    Uses BeautifulSoup with engine-specific heuristics for link extraction
    (accounting for redirect wrappers). Filters noise domains and returns
    sorted list of unique domains (www. prefix stripped).
    """
    if not isinstance(html, str) or not html.strip():
        return []

    soup = BeautifulSoup(html, "html.parser")
    raw_links: List[str] = []
    engine = engine.lower()

    if engine == "google":
        # Handle Google's /url?q= redirect links
        for link in soup.find_all("a", href=True):
            href = link["href"]
            if "url?q=" in href:
                try:
                    parsed_query = parse_qs(urlparse(href).query)
                    target = parsed_query.get("q", [None])[0]
                    if target and isinstance(target, str):
                        raw_links.append(target)
                except (ValueError, TypeError, KeyError):
                    pass
            elif href.startswith(("http://", "https://")) and not any(
                x in href for x in ["google.", "webcache."]
            ):
                raw_links.append(href)
    elif engine == "bing":
        for link in soup.select("a[href^='http'], h2 a[href]"):
            href = link.get("href", "")
            if href.startswith(("http://", "https://")) and "bing.com" not in href.lower():
                raw_links.append(href)
    elif engine == "duckduckgo":
        # DDG html version uses result__a and redirect urls with uddg param
        for link in soup.find_all("a", href=True):
            href = link.get("href", "")
            if not href:
                continue
            if "uddg=" in href:
                try:
                    u = urlparse(href)
                    params = parse_qs(u.query)
                    target = params.get("uddg", [""])[0]
                    if target:
                        raw_links.append(unquote(target))
                        continue
                except Exception:  # pylint: disable=broad-except
                    pass
            if href.startswith(("http://", "https://", "//")) and "duckduckgo.com" not in href.lower():
                if href.startswith("//"):
                    href = "https:" + href
                raw_links.append(href)
    elif engine == "baidu":
        for link in soup.find_all("a", href=True):
            href = link.get("href", "")
            if href.startswith(("http://", "https://")) and "baidu.com" not in href.lower():
                raw_links.append(href)
    elif engine == "yandex":
        for link in soup.find_all("a", href=True):
            href = link.get("href", "")
            if (
                href.startswith(("http://", "https://"))
                and "yandex." not in href.lower()
                and "yandex.ru" not in href.lower()
            ):
                raw_links.append(href)
    else:
        # fallback generic parser
        for link in soup.find_all("a", href=True):
            href = link["href"]
            if href.startswith(("http://", "https://")):
                raw_links.append(href)

    # Extract domains from urls, dedup, filter noise
    domains: List[str] = []
    seen: set[str] = set()
    for url in raw_links:
        try:
            parsed = urlparse(url)
            if not parsed.netloc:
                continue
            netloc = parsed.netloc.lower()
            if netloc.startswith("www."):
                netloc = netloc[4:]
            if netloc in seen:
                continue
            if any(
                noise in netloc or netloc.endswith("." + noise)
                for noise in NOISE_DOMAINS
            ):
                continue
            # basic validation it's a plausible domain
            if re.search(r"^[a-z0-9.-]+\.[a-z]{2,}$", netloc):
                seen.add(netloc)
                domains.append(netloc)
        except Exception:  # pylint: disable=broad-except
            continue

    return sorted(domains)


class SearchClient:
    """Rate-limited asynchronous search client supporting multiple engines."""

    def __init__(self, timeout: float = 12.0) -> None:
        self.timeout = httpx.Timeout(timeout, connect=10.0)
        self.client: httpx.AsyncClient = httpx.AsyncClient(
            timeout=self.timeout, follow_redirects=True
        )
        self.engines = {eng["name"].lower(): eng for eng in get_search_engines()}
        self.last_request_time: Dict[str, float] = {}

    async def search_with_rate_limit(
        self,
        query: str,
        engine: str = "google",
        delay_override: Optional[float] = None,
    ) -> List[str]:
        """Execute search on specified engine with rate limiting, parse results.

        Uses the engine's configured rate_limit (seconds between requests).
        Returns list of unique, noise-filtered domains.
        """
        engine_key = engine.lower()
        if engine_key not in self.engines:
            engine_key = "google"
        config = self.engines[engine_key]
        base = config["base"]
        qparam = config["query_param"]
        rate_limit = (
            delay_override
            if delay_override is not None
            else config.get("rate_limit", 1.0)
        )

        # Enforce rate limit
        now = asyncio.get_running_loop().time()
        if engine_key in self.last_request_time:
            time_since = now - self.last_request_time[engine_key]
            if time_since < rate_limit:
                await asyncio.sleep(rate_limit - time_since)
        self.last_request_time[engine_key] = asyncio.get_running_loop().time()

        params = {qparam: query}
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml",
        }

        try:
            response = await self.client.get(base, params=params, headers=headers)
            response.raise_for_status()
            return parse_results(response.text, engine=engine_key)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 429:
                # too many requests, wait longer next time
                self.last_request_time[engine_key] = (
                    asyncio.get_running_loop().time() + 5.0
                )
            return []
        except Exception:  # pylint: disable=broad-except
            return []

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self.client.aclose()


__all__ = ["NOISE_DOMAINS", "SearchClient", "parse_results"]
