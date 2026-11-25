"""Search functionality for Dork Scout.

Provides SearchClient for rate-limited multi-engine searching, result parsing,
noise filtering, bulk processing and CSV export.
"""

import asyncio
import csv
from datetime import datetime, timezone
from typing import List, Optional

import httpx
from bs4 import BeautifulSoup

from .config import get_search_engines
from .detector import calculate_relevance_score, enrich_discovery_results
from .dorks import build_query, get_dorks
from .models import DiscoveryResult

# Precise base domains for noise filtering. This avoids false positives on
# useful patterns such as *.myshopify.com while still removing big brands.
NOISE_BASE_DOMAINS = {
    "amazon.com",
    "ebay.com",
    "etsy.com",
    "walmart.com",
    "target.com",
    "bestbuy.com",
    "aliexpress.com",
    "shopify.com",
    "squarespace.com",
    "wix.com",
    "weebly.com",
    "blogspot.com",
    "wordpress.com",
    "github.io",
    "paypal.com",
    "stripe.com",
    "braintreepayments.com",
    "adyen.com",
    "razorpay.com",
}


def is_noise_domain(domain: str) -> bool:
    """Return True if the domain is considered noise (big brand/marketplace).

    Uses base-domain matching so that coffee.myshopify.com is kept (useful
    for Shopify dorks) while shopify.com itself is filtered.
    """
    if not domain or not isinstance(domain, str):
        return True
    d = domain.lower().strip().rstrip(".")
    if "." not in d:
        return False
    parts = d.split(".")
    base = ".".join(parts[-2:])
    if base in NOISE_BASE_DOMAINS:
        return True
    extra_keywords = {"blog", "forum", "news", "wiki", "marketplace"}
    if any(k in d for k in extra_keywords):
        return True
    return False


def parse_results(html: str, engine: str = "google") -> List[str]:
    """Parse search-engine HTML and return clean, deduplicated, non-noise domains.

    Handles redirect patterns and HTML differences across Google, Bing,
    DuckDuckGo, Baidu and Yandex. Noise filtering is applied immediately.
    """
    if not html or not isinstance(html, str):
        return []
    soup = BeautifulSoup(html, "html.parser")
    candidate_links = soup.find_all("a", href=True)
    domains: List[str] = []
    seen = set()
    for link in candidate_links:
        href = link["href"]
        if "url?q=" in href:  # Google redirect
            try:
                href = href.split("url?q=")[1].split("&")[0]
            except IndexError:
                continue
        if not href.startswith(("http://", "https://")):
            continue
        try:
            host = href.split("://")[1].split("/")[0].lower()
            if host.startswith("www."):
                host = host[4:]
            if host and host not in seen and not is_noise_domain(host):
                seen.add(host)
                domains.append(host)
        except (IndexError, AttributeError):
            continue
    return domains


class SearchClient:
    """Async HTTP client with per-engine rate limiting."""

    def __init__(self, timeout: float = 12.0):
        self.timeout = httpx.Timeout(timeout)
        self.client = httpx.AsyncClient(timeout=self.timeout, follow_redirects=True)
        self.engines = {e["name"].lower(): e for e in get_search_engines()}

    async def search_with_rate_limit(
        self,
        query: str,
        engine: str = "google",
        delay_override: Optional[float] = None,
    ) -> List[str]:
        """Execute a search on the chosen engine and respect its rate limit."""
        engine_key = engine.lower()
        eng_config = self.engines.get(engine_key, self.engines.get("google", {}))
        base = eng_config.get("base", "https://www.google.com/search")
        param = eng_config.get("query_param", "q")
        rate = delay_override if delay_override is not None else eng_config.get("rate_limit", 1.0)

        try:
            resp = await self.client.get(base, params={param: query})
            resp.raise_for_status()
            domains = parse_results(resp.text, engine_key)
            await asyncio.sleep(rate)
            return domains
        except Exception:  # pylint: disable=broad-except
            await asyncio.sleep(rate)
            return []

    async def close(self):
        """Close the underlying HTTP client."""
        await self.client.aclose()


async def bulk_process(
    tech: str = "Shopify",
    terms: Optional[List[str]] = None,
    limit: int = 20,
    output: str = "results.csv",
    category: Optional[str] = None,
) -> None:
    """Run a full discovery pipeline for multiple terms.

    Searches, deduplicates, removes noise, enriches with metadata and
    relevance scoring, then writes a CSV. Used by the CLI bulk subcommand.
    """
    if terms is None:
        terms = ["coffee"]
    all_results: List[DiscoveryResult] = []
    client = SearchClient(timeout=10.0)
    try:
        dork_patterns = get_dorks(tech=tech, category=category)
        for term in terms:
            for dork in dork_patterns[:2]:  # keep responsive
                query = build_query(dork, term)
                domains = await client.search_with_rate_limit(query, dork.engine)
                for domain in domains[: limit // len(terms) + 1]:
                    if not is_noise_domain(domain):
                        result = DiscoveryResult(
                            domain=domain,
                            tech=[tech],
                            url=f"https://{domain}",
                            detected_at=datetime.now(timezone.utc).isoformat(),
                            title="Discovered via dork",
                            relevance_score=calculate_relevance_score(domain, tech),
                        )
                        all_results.append(result)

        # Deduplicate preserving highest-relevance first
        seen = {}
        for r in all_results:
            if r.domain not in seen or (r.relevance_score or 0) > (
                seen[r.domain].relevance_score or 0
            ):
                seen[r.domain] = r
        unique_results = sorted(
            seen.values(), key=lambda x: x.relevance_score or 0, reverse=True
        )[:limit]

        await enrich_discovery_results(unique_results)
        _write_csv(unique_results, output)
    finally:
        await client.close()


def _write_csv(results: List[DiscoveryResult], filepath: str = "results.csv") -> None:
    """Write DiscoveryResult list to CSV.

    Tech list is semicolon-separated; all enrichment columns (including the
    new relevance_score) are present. extrasaction=ignore prevents breakage
    when new model fields are added.
    """
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
        "relevance_score",
    ]
    with open(filepath, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for r in results:
            row = r.model_dump(mode="json")
            row["tech"] = ";".join(row.get("tech", []))
            for key in (
                "title",
                "domain_age_days",
                "creation_date",
                "ssl_issuer",
                "ssl_valid_from",
                "ssl_valid_to",
            ):
                if row.get(key) is None:
                    row[key] = ""
            if row.get("relevance_score") is None:
                row["relevance_score"] = 0
            writer.writerow(row)
