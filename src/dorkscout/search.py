"""Search engine client and result parsing for Dork Scout.

Provides async SearchClient that can query multiple engines with rate limiting.
parse_results uses BeautifulSoup to extract clean second-level domains from
search result pages. Includes noise filtering to remove marketplaces and big tech.

New: bulk_process() for the CLI subcommand that aggregates results across
multiple terms, applies enrichment and writes a rich CSV.
"""

import asyncio
import csv
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx
from bs4 import BeautifulSoup
from urllib.parse import urlparse

from .config import get_search_engines
from .detector import detect_technologies, enrich_discovery_results
from .dorks import build_query, get_dorks
from .models import DiscoveryResult

# Noise domains to filter out (marketplaces, social, big tech)
NOISE_DOMAINS = {
    "amazon", "ebay", "aliexpress", "walmart", "etsy", "facebook", "instagram",
    "twitter", "t.co", "linkedin", "github", "youtube", "pinterest", "reddit",
    "tiktok", "google", "bing", "baidu", "yandex", "shopify.com", "stripe.com",
    "paypal.com", "squareup.com",
}


def is_noise_domain(domain: str) -> bool:
    """Return True if domain is in the noise list or contains noise keyword."""
    if not domain:
        return True
    d = domain.lower().strip()
    for noise in NOISE_DOMAINS:
        if noise in d:
            return True
    return False


def parse_results(html: str, engine: str = "google") -> List[str]:
    """Extract unique second-level domains from search engine result HTML.

    Uses engine-specific heuristics and falls back to general link parsing.
    Filters noise domains.
    """
    if not html or len(html) < 100:
        return []

    soup = BeautifulSoup(html, "html.parser")
    links = soup.find_all("a", href=True)
    extracted: List[str] = []

    for a in links:
        href = a.get("href", "")
        if not href or "javascript:" in href or "mailto:" in href:
            continue

        # Engine specific
        if engine == "google":
            if "/url?q=" in href:
                match = re.search(r"/url\?q=(https?://[^&]+)", href)
                if match:
                    url = match.group(1)
                    parsed = urlparse(url)
                    domain = parsed.netloc.lower()
                    if domain:
                        extracted.append(domain)
        elif engine in ("bing", "duckduckgo"):
            if href.startswith("http"):
                parsed = urlparse(href)
                if parsed.netloc and not parsed.netloc.endswith((".bing.com", ".duckduckgo.com")):
                    extracted.append(parsed.netloc.lower())
        elif engine == "baidu":
            if "baidu.com" not in href and href.startswith("http"):
                parsed = urlparse(href)
                extracted.append(parsed.netloc.lower())
        else:  # yandex etc
            if href.startswith(("http://", "https://")):
                parsed = urlparse(href)
                if parsed.netloc:
                    extracted.append(parsed.netloc.lower())

    # Normalize to second-level domain
    normalized: List[str] = []
    for dom in extracted:
        if not dom or is_noise_domain(dom):
            continue
        # remove www. and take last two parts
        dom = dom.replace("www.", "")
        parts = dom.split(".")
        if len(parts) >= 2:
            sld = ".".join(parts[-2:])
            if sld not in normalized:
                normalized.append(sld)

    return normalized


class SearchClient:
    """Async client for multi-engine searching with built-in rate limits."""

    def __init__(self, timeout: float = 10.0):
        self.client = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout),
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (compatible; DorkScout/0.2; "
                    "+https://github.com/ajendras/dorking-scout)"
                )
            },
            follow_redirects=True,
        )
        self.engines: Dict[str, Dict[str, Any]] = {
            e["name"].lower(): e for e in get_search_engines()
        }

    async def search_with_rate_limit(
        self,
        query: str,
        engine_name: str = "google",
        delay_override: Optional[float] = None,
    ) -> List[str]:
        """Perform a search on the specified engine with rate limiting."""
        engine_key = engine_name.lower()
        if engine_key not in self.engines:
            engine_key = "google"
        eng_conf = self.engines[engine_key]
        base = eng_conf["base"]
        qparam = eng_conf["query_param"]
        rate = delay_override if delay_override is not None else eng_conf.get("rate_limit", 1.5)

        await asyncio.sleep(rate)  # enforce delay between requests
        try:
            resp = await self.client.get(
                base,
                params={qparam: query},
            )
            resp.raise_for_status()
            return parse_results(resp.text, engine_key)
        except httpx.HTTPError as exc:
            print(f"HTTP error querying {engine_key} for '{query}': {exc}")
            return []
        except Exception as exc:  # pylint: disable=broad-except
            print(f"Unexpected error with {engine_key}: {exc}")
            return []

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self.client.aclose()


async def bulk_process(
    category: Optional[str] = None,
    tech: Optional[str] = None,
    terms: Optional[List[str]] = None,
    output: str = "dorkscout_bulk.csv",
    limit: int = 100,
) -> None:
    """CLI-friendly bulk processor.

    Processes multiple terms for a given category or tech.
    Outputs a CSV with enriched DiscoveryResult data.
    """
    if terms is None or len(terms) == 0:
        terms = [""]

    client = SearchClient(timeout=12.0)
    all_domains: List[str] = []
    try:
        for term_idx, term in enumerate(terms[:8]):  # safety limit on terms
            try:
                dork_patterns = get_dorks(category=category, tech=tech)
                for dork in dork_patterns[:3]:
                    query = build_query(dork, term)
                    domains = await client.search_with_rate_limit(
                        query, dork.engine, delay_override=1.0
                    )
                    all_domains.extend(domains)
            except ValueError:
                continue  # skip bad category

        # Deduplicate preserving order
        seen: set[str] = set()
        unique_domains: List[str] = [
            d for d in all_domains if not (d in seen or seen.add(d)) and not is_noise_domain(d)
        ][:limit]

        # Create results (minimal detection for speed in bulk)
        results: List[DiscoveryResult] = []
        for domain in unique_domains:
            tech_list = [tech] if tech and tech.strip() else detect_technologies("")
            result = DiscoveryResult(
                domain=domain,
                tech=tech_list,
                url=f"https://{domain}",
                detected_at=datetime.now(timezone.utc).isoformat(),
                title="",
                domain_age_days=None,
                creation_date=None,
                ssl_issuer=None,
                ssl_valid_from=None,
                ssl_valid_to=None,
            )
            results.append(result)

        # Enrich (limited)
        await enrich_discovery_results(results[:6])

        # Write CSV
        _write_csv(results, output)
        print(
            f"✅ Bulk processing completed: {len(results)} unique domains saved to {output}"
        )
    except Exception as exc:  # pylint: disable=broad-except
        print(f"❌ Bulk processing failed: {exc}")
        raise
    finally:
        await client.close()


def _write_csv(results: List[DiscoveryResult], filepath: str) -> None:
    """Internal helper to write enriched results to CSV."""
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
    with open(filepath, "w", newline="", encoding="utf-8") as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()
        for result in results:
            row = result.model_dump(mode="json")
            row["tech"] = ";".join(row.get("tech", []))
            # ensure all fields present
            for fn in fieldnames:
                if fn not in row or row[fn] is None:
                    row[fn] = ""
            writer.writerow(row)


__all__ = [
    "SearchClient",
    "parse_results",
    "bulk_process",
    "is_noise_domain",
    "NOISE_DOMAINS",
]
