"""Search client and result parsing for multiple engines.

Provides async SearchClient with rate limiting per engine, parse_results that
extracts clean second-level domains from different search engine HTML formats
(Google redirect links, direct links, Baidu etc). Includes noise filtering,
bulk processing helper and CSV export.
"""

import asyncio
import csv
import re
from datetime import datetime
from io import StringIO
from typing import Any, Dict, List, Optional

import httpx
from bs4 import BeautifulSoup
from urllib.parse import parse_qs, unquote_plus, urlparse

from .config import get_search_engines
from .detector import detect_technologies, enrich_discovery_results
from .dorks import build_query, get_dorks
from .models import DiscoveryResult
from .store import DiscoveryStore

NOISE_DOMAINS: set[str] = {
    "amazon.com",
    "amazon.co.uk",
    "ebay.com",
    "ebay.co.uk",
    "aliexpress.com",
    "etsy.com",
    "walmart.com",
    "target.com",
    "shopify.com",
    "stripe.com",
    "paypal.com",
    "squareup.com",
    "github.com",
    "wordpress.com",
    "blogspot.com",
    "tumblr.com",
    "medium.com",
    "youtube.com",
    "facebook.com",
    "instagram.com",
    "twitter.com",
    "linkedin.com",
    "pinterest.com",
}


def is_noise_domain(domain: str) -> bool:
    """Return True if domain is a known noisy or irrelevant site."""
    if not domain:
        return True
    d = domain.lower()
    if any(nd in d for nd in NOISE_DOMAINS):
        return True
    # avoid common hosting / cdn
    if re.search(
        r"\.(vercel\.app|herokuapp\.com|github\.io|weebly\.com|wixsite\.com)$", d
    ):
        return True
    return False


def parse_results(html: str, engine: str = "google") -> List[str]:
    """Extract unique, non-noise second-level domains from search result HTML.

    Handles Google redirector (/url?q=), direct <a href>, Baidu style links
    and other engines. Updated to catch the direct https://example.com links
    that the previous version missed.
    """
    if not isinstance(html, str) or len(html.strip()) < 10:
        return []

    soup = BeautifulSoup(html, "html.parser")
    seen: set[str] = set()
    engine_lower = engine.lower()

    for a_tag in soup.find_all("a", href=True):
        href = a_tag["href"].strip()
        if not href or len(href) < 5 or href.startswith(("#", "javascript:", "tel:")):
            continue

        # Resolve the actual target URL for engines that use redirectors
        target = href
        if engine_lower in ("google", "bing", "duckduckgo") and "/url" in href:
            try:
                parsed = urlparse(href)
                params = parse_qs(parsed.query)
                for key in ("q", "url", "u"):
                    if key in params and params[key]:
                        target = unquote_plus(params[key][0])
                        break
            except Exception:
                target = href

        # Extract domain
        try:
            if not any(target.startswith(p) for p in ("http://", "https://")):
                if target.startswith("//"):
                    target = "https:" + target
                else:
                    target = "https://" + target
            parsed = urlparse(target)
            if parsed.netloc:
                domain = parsed.netloc.lower()
                if domain.startswith("www."):
                    domain = domain[4:]
                if (
                    domain
                    and "." in domain
                    and not is_noise_domain(domain)
                    and domain not in seen
                ):
                    seen.add(domain)
        except (ValueError, AttributeError):
            continue

    return sorted(seen)


class SearchClient:
    """Async HTTP client for rate-limited searches across multiple engines."""

    def __init__(self, timeout: float = 12.0):
        self.timeout = httpx.Timeout(timeout, connect=5.0)
        self.client: httpx.AsyncClient = httpx.AsyncClient(
            timeout=self.timeout,
            headers={"User-Agent": "Mozilla/5.0 (DorkScout/0.2)"},
            follow_redirects=True,
        )
        self.engines = {
            eng["name"].lower(): eng for eng in get_search_engines()
        }

    async def search_with_rate_limit(
        self,
        query: str,
        engine_name: str = "google",
        delay_override: Optional[float] = None,
    ) -> List[str]:
        """Execute a search on the specified engine with appropriate delay."""
        engine_key = engine_name.lower()
        if engine_key not in self.engines:
            engine_key = "google"
        config = self.engines[engine_key]
        base = config["base"]
        qparam = config["query_param"]
        delay = delay_override if delay_override is not None else config.get("rate_limit", 1.0)

        try:
            resp = await self.client.get(
                base, params={qparam: query}, timeout=self.timeout
            )
            resp.raise_for_status()
            domains = parse_results(resp.text, engine_key)
            await asyncio.sleep(delay)
            return domains
        except httpx.HTTPError as e:
            print(f"HTTP error with {engine_key}: {e}")
            return []
        except Exception as e:  # pylint: disable=broad-except
            print(f"Unexpected error with {engine_key}: {e}")
            return []

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self.client.aclose()


async def bulk_process(
    tech: Optional[str] = None,
    category: Optional[str] = None,
    terms: List[str] = None,
    limit: int = 50,
    output: str = "results.csv",
) -> None:
    """Run searches for multiple terms, enrich, dedup and write CSV.

    Used by the CLI bulk subcommand.
    """
    if terms is None:
        terms = []
    client = SearchClient(timeout=10.0)
    store = DiscoveryStore(db_path=":memory:")  # for bulk, in mem
    all_results: List[DiscoveryResult] = []
    seen_domains: set[str] = set()
    try:
        for term in terms:
            dorks = get_dorks(tech=tech, category=category)
            for dork in dorks[:2]:  # limit per term
                q = build_query(dork, term)
                domains = await client.search_with_rate_limit(q, dork.engine)
                for dom in domains:
                    if dom not in seen_domains and len(seen_domains) < limit:
                        seen_domains.add(dom)
                        # create result
                        res = DiscoveryResult(
                            domain=dom,
                            tech=detect_technologies(""),  # would fetch in real
                            url=f"https://{dom}",
                            detected_at=datetime.now().isoformat(),
                        )
                        all_results.append(res)
        # enrich
        await enrich_discovery_results(all_results)
        # write csv
        _write_csv(all_results, output)
        print(f"Bulk processing complete. Wrote {len(all_results)} results to {output}")
    finally:
        await client.close()


def _write_csv(results: List[DiscoveryResult], filepath: str) -> None:
    """Write list of DiscoveryResult to CSV with all metadata fields."""
    if not results:
        return
    fieldnames = [
        "domain",
        "tech",
        "url",
        "detected_at",
        "title",
        "domain_age_days",
        "creation_date",
        "ssl_issuer",
        "ssl_valid_from",
        "ssl_valid_to",
    ]
    with open(filepath, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in results:
            row = r.model_dump(mode="json")
            row["tech"] = ";".join(row.get("tech", []))
            for k in ("title", "creation_date", "ssl_issuer", "ssl_valid_from", "ssl_valid_to"):
                if k not in row or row[k] is None:
                    row[k] = ""
            writer.writerow(row)


__all__ = [
    "parse_results",
    "is_noise_domain",
    "SearchClient",
    "bulk_process",
]
