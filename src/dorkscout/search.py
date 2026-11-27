"""Multi-engine search client for Dork Scout.

Supports Google, Bing, DuckDuckGo, Baidu and Yandex with rate limiting,
backoff retries, HTML parsing for result extraction and noise filtering.
Also provides bulk_process helper for the CLI.
"""

import asyncio
import csv
import random
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
from bs4 import BeautifulSoup

from .config import (
    get_backoff_config,
    get_big_brand_keywords,
    get_search_engines,
)
from .detector import detect_technologies, enrich_discovery_results
from .dorks import build_query, get_dorks
from .models import DiscoveryResult


NOISE_DOMAINS: set[str] = {
    "amazon.com",
    "amazon.co.uk",
    "ebay.com",
    "etsy.com",
    "walmart.com",
    "target.com",
    "aliexpress.com",
    "facebook.com",
    "instagram.com",
    "twitter.com",
    "linkedin.com",
    "pinterest.com",
    "youtube.com",
    "blogspot.com",
    "wordpress.com",
    "wixsite.com",
    "squarespace.com",
}


def is_noise_domain(domain: str) -> bool:
    """Return True if domain is a known marketplace, brand or noise."""
    if not domain:
        return True
    d = domain.lower().strip()
    if d in NOISE_DOMAINS:
        return True
    for brand in get_big_brand_keywords():
        if brand in d:
            return True
    return False


def parse_results(html: str, engine_name: str = "google") -> List[str]:
    """Extract unique second-level domains from search engine result HTML.

    Uses engine-specific selectors. Falls back to general link parsing.
    """
    if not html:
        return []

    soup = BeautifulSoup(html, "html.parser")
    links = soup.find_all("a", href=True)
    domains: List[str] = []
    seen = set()
    for link in links:
        href = link["href"]
        # engine specific parsing
        if "google" in engine_name.lower():
            if "/url?q=" in href:
                start = href.find("/url?q=") + 7
                end = href.find("&", start)
                url = href[start:end] if end > 0 else href[start:]
                if url.startswith("http"):
                    domain = url.split("/")[2].replace("www.", "")
                    if (
                        domain
                        and domain not in seen
                        and not is_noise_domain(domain)
                    ):
                        seen.add(domain)
                        domains.append(domain)
        elif "bing" in engine_name.lower():
            if "bing.com" not in href:
                try:
                    domain = href.split("/")[2].replace("www.", "")
                    if domain and "." in domain and domain not in seen:
                        seen.add(domain)
                        domains.append(domain)
                except IndexError:
                    continue
        else:
            # generic for DDG, Baidu, Yandex
            if href.startswith("http") and not any(
                n in href for n in ["google", "bing", "duckduckgo"]
            ):
                try:
                    domain = href.split("/")[2].replace("www.", "")
                    if (
                        domain.count(".") >= 1
                        and domain not in seen
                        and not is_noise_domain(domain)
                    ):
                        seen.add(domain)
                        domains.append(domain)
                except IndexError:
                    continue
    return domains[:15]  # limit per engine


class SearchClient:
    """Async client for rate-limited searches across multiple engines."""

    def __init__(self, timeout: float = 12.0):
        self.timeout = timeout
        self.client = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout, connect=5.0),
            headers={
                "User-Agent": "DorkScout/0.1.0 (+https://github.com/ajendras/dorking-scout)"
            },
            follow_redirects=True,
        )
        self.engines = {e["name"].lower(): e for e in get_search_engines()}

    async def search_with_rate_limit(
        self,
        query: str,
        engine: str = "google",
        delay_override: Optional[float] = None,
    ) -> List[str]:
        """Search one engine with backoff/jitter on rate limits or errors."""
        engine = engine.lower()
        eng_config = self.engines.get(engine, self.engines.get("google"))
        if not eng_config:
            return []

        base_url = eng_config["base"]
        param = eng_config["query_param"]
        rate = delay_override or eng_config.get("rate_limit", 1.0)
        backoff = get_backoff_config()

        for attempt in range(backoff["max_retries"] + 1):
            try:
                url = f"{base_url}?{param}={httpx.quote(query)}"
                resp = await self.client.get(url)
                resp.raise_for_status()
                domains = parse_results(resp.text, engine)
                await asyncio.sleep(rate * 0.5)
                return domains
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code in (429, 503) and attempt < backoff["max_retries"]:
                    sleep_time = backoff["base_backoff"] * (2 ** attempt)
                    if backoff.get("use_jitter", True):
                        sleep_time += random.uniform(0, 1.0)
                    sleep_time = min(sleep_time, backoff["max_backoff"])
                    await asyncio.sleep(sleep_time)
                    continue
                return []
            except Exception:  # pylint: disable=broad-except
                if attempt == backoff["max_retries"]:
                    return []
                await asyncio.sleep(1.0)
        return []

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self.client.aclose()


async def bulk_process(
    tech: Optional[str] = None,
    category: Optional[str] = None,
    terms: Optional[List[str]] = None,
    output: Optional[Path] = None,
    limit: int = 50,
) -> List[DiscoveryResult]:
    """Run bulk search for multiple terms, deduplicate, detect tech, enrich and optionally export to CSV.

    Used by the CLI `dork-scout bulk` command.
    """
    if terms is None:
        terms = []
    if not tech and not category:
        raise ValueError("Must provide either tech or category")

    dork_patterns = get_dorks(tech=tech, category=category)
    if not dork_patterns:
        return []

    client = SearchClient(timeout=10.0)
    all_domains: List[str] = []
    queries_used = 0
    try:
        for term in terms:
            for pattern in dork_patterns[:3]:  # limit queries
                q = build_query(pattern, term)
                domains = await client.search_with_rate_limit(
                    q, pattern.engine
                )
                all_domains.extend(domains)
                queries_used += 1
                if queries_used > 10:  # safety
                    break
            if queries_used > 10:
                break

        # deduplicate preserving order
        seen: set[str] = set()
        unique_domains = [
            d
            for d in all_domains
            if not (d in seen or seen.add(d)) and not is_noise_domain(d)
        ][:limit]

        # create results
        results: List[DiscoveryResult] = []
        for domain in unique_domains:
            tech_list = detect_technologies("")
            if tech and tech not in [t.lower() for t in tech_list]:
                tech_list = [tech] + tech_list
            result = DiscoveryResult(
                domain=domain,
                tech=tech_list,
                url=f"https://{domain}",
                detected_at=datetime.now(timezone.utc).isoformat(),
                title="",
                domain_age_days=None,
                ssl_issuer=None,
                relevance_score=50,
            )
            results.append(result)

        await enrich_discovery_results(results, max_results=3)

        # sort by relevance
        results.sort(
            key=lambda r: getattr(r, "relevance_score", 50), reverse=True
        )

        if output:
            _write_csv(results, output)
        return results
    finally:
        await client.close()


def _write_csv(results: List[DiscoveryResult], filepath: Path) -> None:
    """Write enriched results to CSV file."""
    if not results:
        return

    fieldnames = [
        "domain",
        "tech",
        "url",
        "detected_at",
        "title",
        "domain_age_days",
        "ssl_issuer",
        "ssl_valid_from",
        "ssl_valid_to",
        "relevance_score",
    ]
    with open(filepath, "w", newline="", encoding="utf-8") as csvfile:
        writer = csv.DictWriter(
            csvfile, fieldnames=fieldnames, extrasaction="ignore"
        )
        writer.writeheader()
        for result in results:
            row = result.model_dump(mode="json")
            if isinstance(row.get("tech"), list):
                row["tech"] = ";".join(row["tech"])
            for f in ("domain_age_days", "relevance_score"):
                if row.get(f) is None:
                    row[f] = 0 if f == "domain_age_days" else 50
            writer.writerow(row)


__all__ = [
    "SearchClient",
    "parse_results",
    "is_noise_domain",
    "bulk_process",
    "detect_technologies",  # re-export for test patching compatibility
    "enrich_discovery_results",
]
