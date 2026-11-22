"""Search client for multi-engine dork queries with parsing and rate limiting.

Uses httpx.AsyncClient for async requests. Supports Google, Bing, DuckDuckGo, Baidu, Yandex with engine-specific parsers.

parse_results is the key function that extracts domains from raw HTML depending on the engine's result structure.
"""

import asyncio
import re
import time
from typing import Dict, List, Optional

import httpx
from bs4 import BeautifulSoup

from .config import get_search_engines

NOISE_DOMAINS = {
    "github.com",
    "amazon.com",
    "ebay.com",
    "youtube.com",
    "twitter.com",
    "facebook.com",
    "linkedin.com",
    "reddit.com",
    "example.com",
    "test.com",
}


def parse_results(html: str, engine: str = "google") -> List[str]:
    """Parse search engine results HTML and extract unique second-level domains.

    Engine-specific selectors for reliability. First parameter is HTML (fixes
    test calls that supply html positionally + engine=keyword).
    """
    if not html or not isinstance(html, str):
        return []

    soup = BeautifulSoup(html, "html.parser")
    links = []

    if engine == "google":
        for g in soup.select("div.g, .g"):
            a = g.find("a", href=True)
            if a and a["href"].startswith("http"):
                links.append(a["href"])
        for a_tag in soup.select("a[href] h3, h3 a[href]"):
            parent = a_tag.find_parent("a") or a_tag
            if parent and parent.get("href"):
                links.append(parent["href"])
    elif engine == "bing":
        for li in soup.select("li.b_algo, li"):
            a = li.find("a", href=True)
            if a and a["href"].startswith("http"):
                links.append(a["href"])
    elif engine == "duckduckgo":
        for a in soup.select("a.result__a, a[href]"):
            if a.get("href"):
                links.append(a["href"])
    elif engine == "baidu":
        for a in soup.select("a[href], h3 a"):
            if a.get("href") and "http" in a["href"]:
                links.append(a["href"])
    elif engine == "yandex":
        for a in soup.select("a.link, a[href]"):
            if a.get("href") and "http" in a["href"]:
                links.append(a["href"])
    else:
        for a in soup.find_all("a", href=True):
            if "http" in a["href"]:
                links.append(a["href"])

    domains: List[str] = []
    for link in links:
        match = re.search(r"https?://(?:www\.)?([^/]+)", link)
        if match:
            domain = match.group(1).lower()
            if (
                domain not in NOISE_DOMAINS
                and not any(d in domain for d in ["google", "bing", "duckduckgo", "baidu", "yandex"])
            ):
                parts = domain.split(".")
                if len(parts) > 2 and parts[0] not in ("www", "shop", "store"):
                    domain = ".".join(parts[-2:])
                if domain not in domains:
                    domains.append(domain)
    return domains


class SearchClient:
    def __init__(self, timeout: float = 12.0):
        self.timeout = timeout
        self.client = httpx.AsyncClient(timeout=httpx.Timeout(timeout))
        self.engines = {e["name"].lower(): e for e in get_search_engines()}
        self.last_request_time: Dict[str, float] = {}

    async def search_with_rate_limit(
        self, query: str, engine: str = "google", delay_override: Optional[float] = None
    ) -> List[str]:
        """Perform search on given engine with rate limit respect, parse results, return domains."""
        engine_key = engine.lower()
        if engine_key not in self.engines:
            engine_key = "google"
        eng_config = self.engines[engine_key]
        base_url = eng_config["base"]
        param = eng_config["query_param"]
        rate = delay_override if delay_override is not None else eng_config.get("rate_limit", 1.0)

        now = time.time()
        if engine_key in self.last_request_time:
            elapsed = now - self.last_request_time[engine_key]
            if elapsed < rate:
                await asyncio.sleep(rate - elapsed)
        self.last_request_time[engine_key] = time.time()

        try:
            resp = await self.client.get(
                base_url,
                params={param: query},
                headers={"User-Agent": "DorkScout/0.2 (+https://github.com/ajendras/dorking-scout)"},
            )
            resp.raise_for_status()
            html = resp.text
            return parse_results(html, engine=engine_key)
        except Exception:  # pylint: disable=broad-except
            return []

    async def close(self):
        """Close the underlying HTTP client."""
        await self.client.aclose()


__all__ = ["SearchClient", "parse_results"]
