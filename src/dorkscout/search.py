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
from urllib.parse import parse_qs, unquote, urlparse, quote

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
    "tumblr.com",
    "reddit.com",
    "quora.com",
    "stackoverflow.com",
    "google.com",
    "bing.com",
    "duckduckgo.com",
    "baidu.com",
    "yandex.com",
    "search.yahoo.com",
}


class SearchClient:
    """Async HTTP client with built-in rate limiting and result parsing for multiple search engines."""

    def __init__(self, timeout: float = 10.0) -> None:
        self.client = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout),
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (compatible; DorkScoutBot/0.1; "
                    "+https://github.com/ajendras/dorking-scout)"
                )
            },
            follow_redirects=True,
        )
        engines = get_search_engines()
        self.engines: Dict[str, Dict[str, Any]] = {
            e["name"].lower(): e for e in engines
        }

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

        url = f"{base_url}?{q_param}={quote(query)}"

        try:
            resp = await self.client.get(url)
            resp.raise_for_status()
            await asyncio.sleep(delay)
            return self.parse_results(resp.text, engine_key)
        except Exception:  # pylint: disable=broad-except
            return []

    def parse_results(self, html: str, engine: str = "google") -> List[str]:
        """Parse search engine results page and extract clean second-level domains.

        Uses engine-specific selectors where possible, falls back to general link
        extraction. Filters noise domains and deduplicates while preserving order.
        """
        if not html or len(html.strip()) < 100:
            return []

        soup = BeautifulSoup(html, "html.parser")
        links: List[str] = []

        # Engine-specific parsing for better coverage of real result pages
        if engine == "google":
            for a_tag in soup.select("a[href]"):
                href = a_tag.get("href", "")
                if href and href.startswith(("http://", "https://")):
                    links.append(href)
        elif engine == "bing":
            for a_tag in soup.select("a[href]"):
                href = a_tag.get("href", "")
                if href and "bing.com" not in href and href.startswith(("http://", "https://")):
                    links.append(href)
        elif engine == "duckduckgo":
            for a_tag in soup.select("a.result__a, a[href]"):
                href = a_tag.get("href", "")
                if href:
                    links.append(href)
        elif engine == "baidu":
            for a_tag in soup.select("a[href]"):
                href = a_tag.get("href", "")
                if href and href.startswith(("http://", "https://")):
                    links.append(href)
        elif engine == "yandex":
            for a_tag in soup.select("a[href]"):
                href = a_tag.get("href", "")
                if href and "yandex" not in href.lower() and href.startswith(("http://", "https://")):
                    links.append(href)
        else:
            for a_tag in soup.find_all("a", href=True):
                href = a_tag["href"]
                if href and href.startswith(("http://", "https://")):
                    links.append(href)

        # Extract clean domains from links
        domains: List[str] = []
        for link in links:
            try:
                parsed = urlparse(link)
                if parsed.netloc:
                    domain = parsed.netloc.lower()
                    if domain.startswith("www."):
                        domain = domain[4:]
                    if self._is_valid_domain(domain):
                        domains.append(domain)
            except Exception:  # pylint: disable=broad-except
                continue

        # Deduplicate preserving first-seen order
        seen: set[str] = set()
        unique: List[str] = [d for d in domains if not (d in seen or seen.add(d))]
        return unique

    def _is_valid_domain(self, domain: str) -> bool:
        """Return True if domain looks valid and is not a known noise domain."""
        if not domain or len(domain) < 4 or "." not in domain:
            return False
        if any(noise in domain for noise in NOISE_DOMAINS):
            return False
        # Simple regex sanity check (no obvious query params or fragments)
        if re.search(r"[\s&?=#%]", domain):
            return False
        return True

    async def close(self) -> None:
        """Close the underlying AsyncClient."""
        await self.client.aclose()
