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
from .models import DiscoveryResult

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
    "tumblr.com",
    "stripe.com",
    "paypal.com",
    "square.com",
    "shopify.com",
    "duckduckgo.com",
    "google.com",
    "bing.com",
    "baidu.com",
    "yandex.com",
    "wikipedia.org",
}


def _normalize_domain(url: str) -> Optional[str]:
    """Return cleaned lowercase domain from URL (supports uddg redirects)."""
    if not url:
        return None
    try:
        if "uddg=" in url:
            qs = parse_qs(urlparse(url).query)
            if "uddg" in qs:
                url = unquote(qs["uddg"][0])
        if not url.startswith(("http://", "https://")):
            url = "https://" + url.lstrip("/")
        parsed = urlparse(url)
        domain = parsed.netloc
        if not domain and parsed.path:
            domain = parsed.path.split("/")[0]
        if domain.startswith("www."):
            domain = domain[4:]
        domain = domain.lower()
        if domain and "." in domain and len(domain) > 3:
            return domain
        return None
    except Exception:
        return None


def parse_results(engine: str, html: str) -> List[str]:
    """Parse search engine results page HTML and return clean domains.

    Uses engine-specific logic (including DuckDuckGo uddg redirect handling).
    Noise domains are filtered. Returns unique second-level/sub domains.
    """
    if not html or not isinstance(html, str):
        return []

    soup = BeautifulSoup(html, "html.parser")
    links: List[str] = []
    engine_lower = engine.lower()

    if engine_lower == "google":
        for a in soup.select("a[href]"):
            href = a.get("href", "")
            if "/url?q=" in href:
                try:
                    real = href.split("/url?q=")[1].split("&")[0]
                    links.append(real)
                except Exception:
                    links.append(href)
            elif href.startswith("http"):
                links.append(href)
    elif engine_lower == "duckduckgo":
        for a in soup.select("a[href]"):
            href = a.get("href", "")
            if not href:
                continue
            if "uddg=" in href:
                try:
                    qs = parse_qs(urlparse(href).query)
                    if "uddg" in qs:
                        real = unquote(qs["uddg"][0])
                        links.append(real)
                        continue
                except Exception:
                    pass
            if href.startswith(("http", "//")):
                if href.startswith("//"):
                    href = "https:" + href
                links.append(href)
    else:  # bing, baidu, yandex
        for a in soup.select("a[href]"):
            href = a.get("href", "")
            if href.startswith("http") and not any(
                bad in href.lower()
                for bad in ("bing.com", "baidu.com", "yandex.", "duckduckgo.com")
            ):
                links.append(href)

    domains: List[str] = []
    seen: set[str] = set()
    for link in links:
        domain = _normalize_domain(link)
        if (
            domain
            and domain not in seen
            and domain not in NOISE_DOMAINS
            and not any(n in domain for n in NOISE_DOMAINS if n != domain)
        ):
            seen.add(domain)
            domains.append(domain)

    return domains


class SearchClient:
    """Async HTTP client for multi-engine searches with built-in parsing.

    All search methods return a list of clean, unique second-level domains.
    Rate-limit hints from config are respected via optional delays.
    Supports Google, Bing, DuckDuckGo, Baidu and Yandex.
    """

    def __init__(self, timeout: float = 12.0) -> None:
        """Initialise shared async client and load engine configuration.

        A realistic browser User-Agent is used to improve result quality.
        """
        self.timeout = timeout
        self.client = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout),
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0 Safari/537.36"
                ),
            },
            follow_redirects=True,
        )
        self.engines = {e["name"].lower(): e for e in get_search_engines()}

    async def search_with_rate_limit(
        self,
        query: str,
        engine_name: str = "google",
        delay_override: Optional[float] = None,
    ) -> List[str]:
        """Execute a search against the selected engine and parse results.

        Applies rate-limit sleep after the request. Returns parsed domains.
        """
        engine_key = engine_name.lower()
        eng_conf = self.engines.get(engine_key) or self.engines.get("google")
        if not eng_conf:
            return []

        base_url = eng_conf["base"]
        q_param = eng_conf["query_param"]
        delay = delay_override if delay_override is not None else eng_conf.get("rate_limit", 1.0)

        url = f"{base_url}?{q_param}={httpx.quote(query)}"
        try:
            resp = await self.client.get(url)
            resp.raise_for_status()
            domains = parse_results(engine_name, resp.text)
            await asyncio.sleep(delay)
            return domains
        except httpx.HTTPError:
            await asyncio.sleep(delay)
            return []
        except Exception:
            await asyncio.sleep(delay)
            return []

    async def close(self) -> None:
        """Close the underlying async HTTP client."""
        await self.client.aclose()


__all__ = ["SearchClient", "parse_results"]
