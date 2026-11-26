"""Multi-engine web search client with robust rate limiting for Dork Scout.

The SearchClient uses httpx.AsyncClient to query multiple search engines.
search_with_rate_limit enforces per-engine minimum delays and implements
exponential backoff with full jitter on transient errors (429, timeouts, 5xx).
parse_results uses BeautifulSoup to extract clean domains, removing noise.
Includes bulk_process helper for the CLI.
"""

import asyncio
import csv
import random
import time
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
from bs4 import BeautifulSoup
from urllib.parse import parse_qs, urlparse

from .config import (
    get_backoff_config,
    get_big_brand_keywords,
    get_search_engines,
)
from .detector import calculate_relevance_score, detect_technologies, enrich_discovery_results
from .dorks import build_query, get_dorks
from .models import DiscoveryResult


NOISE_DOMAINS: set[str] = {
    "google.com",
    "bing.com",
    "duckduckgo.com",
    "baidu.com",
    "yandex.com",
    "yandex.ru",
    "facebook.com",
    "twitter.com",
    "x.com",
    "instagram.com",
    "linkedin.com",
    "pinterest.com",
    "reddit.com",
    "youtube.com",
    "wikipedia.org",
    "github.com",
    "gitlab.com",
    "bitbucket.org",
}


def is_noise_domain(domain: str) -> bool:
    """Check domain against noise list and big brand keywords."""
    if not domain:
        return True
    d = domain.lower().strip()
    if d in NOISE_DOMAINS or any(nd in d for nd in NOISE_DOMAINS):
        return True
    if any(kw in d for kw in get_big_brand_keywords()):
        return True
    return False


def parse_results(html: str, engine: str = "google") -> List[str]:
    """Parse raw search engine HTML into a list of unique, clean domains.

    Selectors are engine-specific. Removes noise domains and subdomains like www.
    """
    if not html or len(html) < 100:
        return []

    soup = BeautifulSoup(html, "html.parser")
    links: List[str] = []
    engine = engine.lower()

    if engine == "google":
        for tag in soup.find_all("a", href=True):
            href = tag["href"]
            if "/url?q=" in href:
                try:
                    parsed = urlparse(href)
                    qs = parse_qs(parsed.query)
                    if "q" in qs:
                        real_href = qs["q"][0]
                        links.append(real_href)
                except Exception:
                    continue
            elif href.startswith(("http://", "https://")) and not any(
                x in href for x in ["google.", "webcache.", "policies.google"]
            ):
                links.append(href)
    elif engine == "bing":
        for tag in soup.select("li.b_algo a, a[href]"):
            href = tag.get("href", "")
            if href.startswith(("http", "//")):
                links.append(href)
    elif engine == "duckduckgo":
        for tag in soup.select("a.result__a, a[href^='http']"):
            href = tag.get("href", "")
            if href and "duckduckgo.com" not in href:
                if not href.startswith("http"):
                    href = "https://" + href
                links.append(href)
    elif engine == "baidu":
        for tag in soup.select("a[href^='http'], div.result a"):
            href = tag.get("href", "")
            if href and "baidu.com" not in href:
                links.append(href)
    elif engine == "yandex":
        for tag in soup.select("a.link, a[href^='http']"):
            href = tag.get("href", "")
            if href and "yandex." not in href:
                links.append(href)

    # Extract domains
    domains: List[str] = []
    seen: set[str] = set()
    for link in links:
        try:
            if not link.startswith(("http://", "https://")):
                link = "https://" + link
            parsed = urlparse(link)
            netloc = parsed.netloc
            if netloc.startswith("www."):
                netloc = netloc[4:]
            if netloc and netloc not in seen and not is_noise_domain(netloc):
                seen.add(netloc)
                domains.append(netloc)
        except Exception:
            continue

    return domains


class SearchClient:
    """Async HTTP client for multi-engine searches with intelligent rate limiting."""

    def __init__(self, timeout: float = 12.0):
        self.timeout = httpx.Timeout(timeout, connect=10.0)
        self.client = httpx.AsyncClient(
            timeout=self.timeout,
            follow_redirects=True,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0 Safari/537.36"
                )
            },
        )
        self.last_request_time: Dict[str, float] = {}
        self.consecutive_failures: Dict[str, int] = {}

    async def close(self):
        """Close the underlying HTTP client."""
        await self.client.aclose()

    async def search_with_rate_limit(
        self,
        query: str,
        engine: str = "google",
        delay_override: Optional[float] = None,
    ) -> List[str]:
        """Search with per-engine rate limiting, retries, exponential backoff and jitter.

        The delay_override can be used by scheduler to force a specific pause.
        On rate limit errors (429) or transient failures we retry with increasing
        backoff + full jitter (sleep = random(0 .. min(max, base*2**attempt)) ).
        Success resets the failure counter for that engine.
        """
        engines = {e["name"].lower(): e for e in get_search_engines()}
        eng_key = engine.lower()
        if eng_key not in engines:
            eng_key = "google"
        cfg = engines[eng_key]
        backoff = get_backoff_config()
        max_retries = backoff["max_retries"]
        base_boff = backoff["base_backoff"]
        max_boff = backoff["max_backoff"]
        rate = cfg.get("rate_limit", 1.5)

        for attempt in range(max_retries + 1):
            # rate limit delay
            now = time.time()
            last_time = self.last_request_time.get(eng_key, 0.0)
            delay = max(0.0, rate - (now - last_time))

            if attempt > 0:
                # exponential backoff with full jitter
                exp_backoff = base_boff * (2 ** attempt)
                curr_backoff = min(max_boff, exp_backoff)
                jitter = random.uniform(0.0, curr_backoff)
                delay = max(delay, jitter)

            if delay > 0.0:
                await asyncio.sleep(delay)

            try:
                params: Dict[str, str] = {cfg["query_param"]: query}
                response = await self.client.get(cfg["base"], params=params)
                response.raise_for_status()
                domains = parse_results(response.text, eng_key)
                # success path
                self.last_request_time[eng_key] = time.time()
                self.consecutive_failures[eng_key] = 0
                return list(dict.fromkeys(domains))  # preserve order, dedup
            except httpx.HTTPStatusError as e:
                status_code = e.response.status_code
                self.consecutive_failures[eng_key] = attempt + 1
                if status_code == 429 or 500 <= status_code < 600:
                    if attempt < max_retries:
                        continue
                return []
            except (httpx.TimeoutException, httpx.ConnectTimeout, httpx.ReadTimeout, httpx.ConnectError):
                self.consecutive_failures[eng_key] = attempt + 1
                if attempt < max_retries:
                    continue
                return []
            except Exception:  # pylint: disable=broad-except
                # unexpected error, retry a couple times
                if attempt < max_retries // 2:
                    continue
                return []

        return []


def bulk_process(
    tech: Optional[str] = None,
    category: Optional[str] = None,
    terms: Optional[List[str]] = None,
    output: Optional[str] = None,
    limit: int = 50,
) -> List[DiscoveryResult]:
    """Synchronous wrapper for bulk searches. Runs async pipeline then optionally writes CSV."""
    if terms is None:
        terms = ["demo"]

    async def _run() -> List[DiscoveryResult]:
        client = SearchClient(timeout=20.0)
        try:
            all_domains: List[str] = []
            for term in terms:
                patterns = get_dorks(tech=tech, category=category)
                for pattern in patterns[:2]:  # limit per term
                    q = build_query(pattern, term)
                    domains = await client.search_with_rate_limit(
                        q, pattern.engine, delay_override=None
                    )
                    all_domains.extend(domains)

            # unique
            seen = set()
            unique_domains = [d for d in all_domains if not (d in seen or seen.add(d))][:limit]

            # build results
            results: List[DiscoveryResult] = []
            for domain in unique_domains:
                res = DiscoveryResult(
                    domain=domain,
                    tech=[tech] if tech else ["unknown"],
                    url=f"https://{domain}",
                    detected_at=datetime.now(timezone.utc).isoformat(),
                    title="",
                    relevance_score=calculate_relevance_score(domain, tech or ""),
                )
                results.append(res)

            await enrich_discovery_results(results)

            # sort by relevance
            results.sort(key=lambda r: getattr(r, "relevance_score", 0) or 0, reverse=True)
            return results
        finally:
            await client.close()

    results = asyncio.run(_run())

    if output:
        _write_csv(results, output)
    return results


def _write_csv(results: List[DiscoveryResult], path: str) -> None:
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
        "ssl_valid_to",
        "relevance_score",
    ]
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in results:
            row = r.model_dump()
            row["tech"] = ";".join(row.get("tech", []))
            for k in ("domain_age_days", "relevance_score"):
                if row.get(k) is None:
                    row[k] = ""
            writer.writerow(row)


__all__ = [
    "SearchClient",
    "parse_results",
    "bulk_process",
    "is_noise_domain",
]
