"""Async search clients and HTML result parsers for Dork Scout.

Provides a rate-limit-aware httpx.AsyncClient that queries Google, Bing and
DuckDuckGo. Results are parsed with BeautifulSoup4, deduplicated and filtered
of common noise (marketplaces, search engines, social platforms). The
SearchClient is used by background workers and the /api/search endpoint.
"""

import asyncio
import re
from typing import Dict, List, Optional
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

from .config import get_search_engines
from .models import DiscoveryResult


class SearchClient:
    """Async HTTP client for multi-engine searches with built-in parsing.

    All search methods return a list of clean, unique second-level domains.
    Rate-limit hints from config are respected via optional delays.
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
                "Accept": "text/html,application/xhtml+xml,application/xml",
                "Accept-Language": "en-US,en;q=0.9",
            },
            follow_redirects=True,
            limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
        )

        engines = get_search_engines()
        self.engine_configs: Dict[str, Dict] = {
            e["name"].lower(): e for e in engines
        }

        self.noise_domains = {
            "google.com",
            "bing.com",
            "duckduckgo.com",
            "yandex.com",
            "yahoo.com",
            "amazon.com",
            "ebay.com",
            "etsy.com",
            "aliexpress.com",
            "facebook.com",
            "instagram.com",
            "twitter.com",
            "linkedin.com",
            "pinterest.com",
            "youtube.com",
            "github.com",
            "wordpress.com",
            "blogspot.com",
            "wixsite.com",
            "weebly.com",
            "myshopify.com",
            "bigcartel.com",
            "square.site",
        }

    async def search_google(self, query: str) -> List[str]:
        """Execute a Google search and return parsed unique domains.

        Raises:
            ValueError: if query is empty.
            httpx errors are caught and logged; an empty list is returned.
        """
        if not query or not query.strip():
            raise ValueError("Search query cannot be empty")

        config = self.engine_configs.get("google", {})
        base = config.get("base", "https://www.google.com/search")
        param = config.get("query_param", "q")

        try:
            resp = await self.client.get(
                base,
                params={param: query},
                headers={"Referer": "https://www.google.com/"},
            )
            resp.raise_for_status()
            return self.parse_results(resp.text, "google")
        except httpx.HTTPError as exc:
            # In production a structured logger would be used
            print(f"Google request failed for query '{query[:60]}...': {exc}")
            return []

    async def search_bing(self, query: str) -> List[str]:
        """Execute a Bing search and return parsed unique domains."""
        if not query or not query.strip():
            raise ValueError("Search query cannot be empty")

        config = self.engine_configs.get("bing", {})
        base = config.get("base", "https://www.bing.com/search")
        param = config.get("query_param", "q")

        try:
            resp = await self.client.get(base, params={param: query})
            resp.raise_for_status()
            return self.parse_results(resp.text, "bing")
        except httpx.HTTPError as exc:
            print(f"Bing request failed for query '{query[:60]}...': {exc}")
            return []

    async def search_duckduckgo(self, query: str) -> List[str]:
        """Execute a DuckDuckGo HTML search (friendlier for scraping)."""
        if not query or not query.strip():
            raise ValueError("Search query cannot be empty")

        config = self.engine_configs.get("duckduckgo", {})
        base = config.get("base", "https://html.duckduckgo.com/html")
        param = config.get("query_param", "q")

        try:
            resp = await self.client.get(base, params={param: query})
            resp.raise_for_status()
            return self.parse_results(resp.text, "duckduckgo")
        except httpx.HTTPError as exc:
            print(f"DuckDuckGo request failed: {exc}")
            return []

    def parse_results(self, html: str, engine: str = "google") -> List[str]:
        """Parse search-engine HTML and extract a deduplicated list of domains.

        Engine-specific heuristics are used for link extraction. All domains
        are passed through _is_valid_domain() which removes marketplaces,
        search-engine domains and other low-value results. The extractor
        always returns second-level domains for consistency with DiscoveryResult.

        Returns:
            Sorted list of unique, clean second-level domains.
        """
        if not html or len(html.strip()) < 200:
            return []

        soup = BeautifulSoup(html, "html.parser")
        domains: set[str] = set()

        if engine == "google":
            # Google often wraps real links in a /url?q= redirector
            for a_tag in soup.find_all("a", href=True):
                href = a_tag["href"]
                if "/url?q=" in href:
                    match = re.search(r"/url\?q=(https?://[^&]+)", href)
                    if match:
                        domain = self._extract_domain(match.group(1))
                        if domain:
                            domains.add(domain)
                elif href.startswith(("http://", "https://")):
                    domain = self._extract_domain(href)
                    if domain:
                        domains.add(domain)

        elif engine == "bing":
            for a_tag in soup.select("a[href^='http'], li.b_algo a"):
                href = a_tag.get("href", "")
                if href and not any(x in href for x in ("bing.com", "microsoft.com")):
                    domain = self._extract_domain(href)
                    if domain:
                        domains.add(domain)

        elif engine == "duckduckgo":
            for a_tag in soup.select("a.result__a, a[href]"):
                href = a_tag.get("href", "")
                if "uddg=" in href:
                    match = re.search(r"uddg=(https?[^&]+)", href)
                    if match:
                        url = match.group(1)
                        # simplistic unescape
                        url = url.replace("%3A", ":").replace("%2F", "/")
                        domain = self._extract_domain(url)
                        if domain:
                            domains.add(domain)
                elif href.startswith(("http", "//")):
                    domain = self._extract_domain(href)
                    if domain:
                        domains.add(domain)
        else:
            # Generic fallback for any other engine (Yandex, etc.)
            for a_tag in soup.find_all("a", href=True):
                domain = self._extract_domain(a_tag["href"])
                if domain:
                    domains.add(domain)

        valid = [d for d in domains if self._is_valid_domain(d)]
        return sorted(valid)

    def _extract_domain(self, url_or_href: str) -> Optional[str]:
        """Return the second-level domain from a URL string or None.

        Uses stdlib urlparse for robustness (replaces fragile regex). Takes
        the last two hostname parts to guarantee a second-level domain that
        satisfies the DiscoveryResult model validator.
        """
        if not url_or_href or not isinstance(url_or_href, str):
            return None

        try:
            # Normalise to full URL for urlparse
            if "://" not in url_or_href:
                url_or_href = "https://" + url_or_href.lstrip("/")
            parsed = urlparse(url_or_href)
            if not parsed.hostname:
                return None
            hostname = parsed.hostname.lower()
            parts = hostname.split(".")
            if len(parts) >= 2:
                return ".".join(parts[-2:])
            return hostname
        except Exception:  # pylint: disable=broad-except
            # Fallback to original regex for any edge cases
            match = re.search(
                r"(?:https?://)?(?:www\.)?([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?"
                r"(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+)",
                url_or_href,
                re.IGNORECASE,
            )
            if match:
                domain = match.group(1).lower()
                parts = domain.split(".")
                if len(parts) >= 2:
                    return ".".join(parts[-2:])
                return domain
            return None

    def _is_valid_domain(self, domain: str) -> bool:
        """Return True only for plausible, non-noise domains.

        Filters out known marketplaces, search engines, social networks and
        single-label hostnames.
        """
        if not domain or "." not in domain or len(domain) < 5:
            return False

        for noise in self.noise_domains:
            if noise in domain or domain.endswith("." + noise):
                return False

        # Very generic TLD-only or numeric hosts are unlikely to be stores
        if re.search(r"^\d+\.", domain):
            return False

        return True

    async def search_with_rate_limit(
        self, query: str, engine: str = "google", delay_override: Optional[float] = None
    ) -> List[str]:
        """Convenience wrapper that respects the engine's configured rate_limit.

        Returns parsed domains after sleeping the required delay.
        """
        engine_key = engine.lower()
        config = self.engine_configs.get(engine_key, {})
        delay = delay_override or config.get("rate_limit", 1.0)

        if engine_key == "google":
            domains = await self.search_google(query)
        elif engine_key == "bing":
            domains = await self.search_bing(query)
        elif engine_key == "duckduckgo":
            domains = await self.search_duckduckgo(query)
        else:
            # For Yandex we only generate a link (no scraping)
            domains = []

        await asyncio.sleep(delay)
        return domains

    def enrich_with_detection(
        self, domains: List[str], html_map: Dict[str, str]
    ) -> List[DiscoveryResult]:
        """Turn a list of domains into DiscoveryResult objects.

        html_map is a dict of domain -> raw HTML (fetched separately by the
        detector module). This method is called after scraping to attach
        technology fingerprints.
        """
        results: List[DiscoveryResult] = []
        for domain in domains:
            html = html_map.get(domain, "")
            # Placeholder for detector.detect_technologies(html, {})
            detected_tech: List[str] = []
            if "shopify" in html.lower():
                detected_tech.append("Shopify")
            if "stripe" in html.lower():
                detected_tech.append("Stripe")

            result = DiscoveryResult(
                domain=domain,
                tech=detected_tech,
                url=f"https://{domain}",
            )
            results.append(result)
        return results

    async def close(self) -> None:
        """Close the underlying AsyncClient."""
        await self.client.aclose()


# Synchronous helper for example scripts and tests (no network)
def parse_results(html: str, engine: str = "google") -> List[str]:
    """Synchronous wrapper around the parser for tests and offline examples."""
    client = SearchClient()
    try:
        return client.parse_results(html, engine)
    finally:
        # No async close needed for pure parsing path
        pass


__all__ = [
    "SearchClient",
    "parse_results",
]
