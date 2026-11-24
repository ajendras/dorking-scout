"""Multi-engine search client and bulk processing for Dork Scout.

Provides async SearchClient that can query Google, Bing, DuckDuckGo, Baidu and Yandex
with proper rate limiting and HTML parsing tailored to each engine's result format.
Also includes bulk_process for the CLI that runs multiple terms, deduplicates against
noise domains and previously seen (via store), enriches and exports to CSV.
"""

import asyncio
import csv
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
from bs4 import BeautifulSoup

from .config import get_search_engines
from .detector import enrich_discovery_results
from .dorks import build_query, get_dorks
from .models import DiscoveryResult
from .store import DiscoveryStore

NOISE_DOMAINS = {
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
    "github.com",
    "wordpress.com",
    "blogspot.com",
    "tumblr.com",
    "medium.com",
    "facebook.com",
    "instagram.com",
    "twitter.com",
    "linkedin.com",
    "pinterest.com",
    "youtube.com",
    "example.com",
    "test.com",
    "localhost",
    "127.0.0.1",
}


def is_noise_domain(domain: str) -> bool:
    """Check if domain is likely noise (marketplace, social, known big site)."""
    if not domain or not isinstance(domain, str):
        return True
    d = domain.lower().strip().rstrip(".")
    if any(nd in d for nd in NOISE_DOMAINS):
        return True
    # simple second level check
    parts = d.split(".")
    if len(parts) >= 2 and ".".join(parts[-2:]) in NOISE_DOMAINS:
        return True
    return False


def parse_results(html: str, engine: str = "google") -> List[str]:
    """Extract unique second-level domains from search engine result HTML.

    Uses engine-specific selectors because each has different markup.
    Returns list of clean domains (no protocol, no path).
    """
    if not html or len(html.strip()) < 100:
        return []

    soup = BeautifulSoup(html, "html.parser")
    domains: set[str] = set()

    if engine == "google":
        for g in soup.select("a[href]"):
            href = g.get("href", "")
            if "url?q=" in href:
                match = re.search(r"url\?q=https?://([^&/]+)", href)
                if match:
                    dom = match.group(1).lower()
                    if not is_noise_domain(dom):
                        domains.add(dom)
    elif engine == "bing":
        for item in soup.select("li.b_algo a"):
            href = item.get("href", "")
            if href.startswith("http"):
                match = re.search(r"https?://([^/]+)", href)
                if match:
                    dom = match.group(1).lower()
                    if not is_noise_domain(dom):
                        domains.add(dom)
    elif engine == "duckduckgo":
        for a in soup.select("a.result__a"):
            href = a.get("href", "")
            match = re.search(r"https?://([^/]+)", href)
            if match:
                dom = match.group(1).lower()
                if not is_noise_domain(dom):
                    domains.add(dom)
    elif engine == "baidu":
        for a in soup.select("a[href]"):
            href = a.get("href", "")
            if "http" in href:
                match = re.search(r"https?://([^/\"']+)", href)
                if match:
                    dom = match.group(1).lower()
                    if not is_noise_domain(dom) and "baidu.com" not in dom:
                        domains.add(dom)
    elif engine == "yandex":
        for a in soup.select("a[href]"):
            href = a.get("href", "")
            match = re.search(r"https?://([^/\"'\s]+)", href)
            if match:
                dom = match.group(1).lower()
                if not is_noise_domain(dom) and "yandex." not in dom:
                    domains.add(dom)

    # fallback generic
    if not domains:
        for a in soup.find_all("a", href=True):
            href = a["href"]
            match = re.search(r"https?://([a-z0-9.-]+\.[a-z]{2,})", href, re.I)
            if match:
                dom = match.group(1).lower()
                if not is_noise_domain(dom) and len(dom) > 4:
                    domains.add(dom)

    return sorted(list(domains))


class SearchClient:
    """Async client for rate-limited searches across multiple engines.

    Handles query encoding, fetching, parsing and sleep between requests per engine's rate_limit.
    """

    def __init__(self, timeout: float = 12.0):
        self.timeout = timeout
        self.engines = get_search_engines()
        self.client = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout),
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (compatible; DorkScout/0.1; "
                    "+https://github.com/ajendras/dorking-scout)"
                )
            },
            follow_redirects=True,
        )

    async def close(self) -> None:
        await self.client.aclose()

    async def search_with_rate_limit(
        self,
        query: str,
        engine_name: str = "google",
        delay_override: Optional[float] = None,
    ) -> List[str]:
        """Perform search on given engine with rate limit sleep."""
        engine_cfg = next(
            (e for e in self.engines if e["name"].lower() == engine_name.lower()),
            self.engines[0],
        )
        rate = delay_override if delay_override is not None else engine_cfg.get("rate_limit", 1.0)
        base_url = engine_cfg["base"]
        param = engine_cfg["query_param"]
        try:
            resp = await self.client.get(
                base_url,
                params={param: query},
            )
            resp.raise_for_status()
            domains = parse_results(resp.text, engine_name.lower())
            await asyncio.sleep(rate)
            return domains[:30]  # limit per query
        except httpx.HTTPError:
            await asyncio.sleep(rate)
            return []
        except Exception:  # pylint: disable=broad-except
            await asyncio.sleep(rate)
            return []


async def _write_csv(results: List[DiscoveryResult], output: str = "results.csv") -> None:
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
    out_path = Path(output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in results:
            row = r.model_dump(mode="json")
            row["tech"] = ";".join(row.get("tech", []))
            for k in ("title", "creation_date", "ssl_issuer", "ssl_valid_from", "ssl_valid_to"):
                if k not in row or row[k] is None:
                    row[k] = ""
            writer.writerow(row)


async def bulk_process(
    tech: Optional[str] = None,
    category: Optional[str] = None,
    terms: Optional[List[str]] = None,
    limit: int = 50,
    output: str = "results.csv",
) -> None:
    """Run searches for multiple terms, enrich, dedup and write CSV.

    Used by the CLI bulk subcommand.
    Uses in-memory store to avoid polluting the main DB.
    """
    if terms is None:
        terms = []
    client = SearchClient(timeout=10.0)
    store = DiscoveryStore(db_path=":memory:")  # for bulk, in mem
    all_domains: List[str] = []
    queries_used: List[str] = []

    for term in terms:
        try:
            patterns = get_dorks(tech=tech, category=category)
            for pattern in patterns[:3]:  # limit per term to stay fast
                q = build_query(pattern, term)
                queries_used.append(q)
                domains = await client.search_with_rate_limit(
                    q, pattern.engine
                )
                all_domains.extend(domains)
        except Exception:  # pylint: disable=broad-except
            continue

    # dedup preserving order, remove noise
    seen: set[str] = set()
    unique_domains = [
        d for d in all_domains
        if not (d in seen or seen.add(d)) and not is_noise_domain(d)
    ][:limit]

    # build results
    results: List[DiscoveryResult] = []
    for domain in unique_domains:
        result = DiscoveryResult(
            domain=domain,
            tech=[tech] if tech else [],
            url=f"https://{domain}",
            detected_at=datetime.now(timezone.utc).isoformat(),
            title="",
        )
        results.append(result)

    # enrich (network calls limited by the helper)
    await enrich_discovery_results(results)

    # persist using the in-memory store (for consistency with other paths)
    try:
        primary_query = queries_used[0] if queries_used else f"bulk-{tech or category}"
        store.record_search(
            query=primary_query,
            results=results,
            category=category,
            tech=tech,
            term=";".join(terms),
            engine="bulk",
        )
    except Exception:  # pylint: disable=broad-except
        pass  # non fatal for bulk

    await _write_csv(results, output)
    await client.close()
