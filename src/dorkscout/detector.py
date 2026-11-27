"""API-less technology detection for Dork Scout.

Lightweight fingerprinting inspired by Wappalyzer. Uses regex signatures
against HTML content and HTTP headers to identify payment gateways, ecommerce
platforms and other technologies. Designed for speed and zero external API
dependencies. Signatures are curated to match the dork database and avoid
false positives on common noise domains.

Now also includes async enrichment of domain age (via Wayback Machine CDX API)
and SSL certificate metadata (via crt.sh Certificate Transparency endpoint).
These are free, public sources with no API keys required. The new
calculate_relevance_score() prefers smaller independent sites over big brands.
"""

import re
import asyncio
import httpx
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Union

from .config import get_big_brand_keywords
from .models import DiscoveryResult


# Production-grade signatures (subset of the full 180+). Each tech has
# multiple overlapping patterns. HTML is lowercased once for performance.
TECH_SIGNATURES: Dict[str, List[Dict[str, str]]] = {
    "Shopify": [
        {"html": r"powered by shopify"},
        {"html": r"cdn\.shopify\.com"},
        {"html": r"Shopify\.Checkout"},
        {"html": r'"shopify":'},
        {"html": r"shopify\.theme"},
        {"html": r"/cart/add.*shopify"},
    ],
    "Stripe": [
        {"html": r"js\.stripe\.com"},
        {"html": r"stripe\.com"},
        {"html": r'"stripe":'},
    ],
    "PayPal": [
        {"html": r"paypal\.com"},
        {"html": r"paypalobjects"},
        {"html": r"PayPal\.Checkout"},
    ],
    "WooCommerce": [
        {"html": r"woocommerce"},
        {"html": r"/wc-"},
        {"html": r"woocommerce-"},
    ],
    "WordPress": [
        {"html": r"wp-content"},
        {"html": r"wp-includes"},
        {"html": r"powered by wordpress"},
    ],
    "React": [
        {"html": r"react"},
        {"html": r"reactjs"},
    ],
    "PrestaShop": [
        {"html": r"prestashop"},
        {"html": r"powered by prestashop"},
    ],
    "Cloudflare": [
        {"html": r"cloudflare"},
        {"html": r"cf-ray"},
    ],
    "Braintree": [
        {"html": r"braintree"},
    ],
}


def detect_technologies(html: str, headers: Optional[Dict[str, str]] = None) -> List[str]:
    """Detect technologies from HTML and headers using regex signatures."""
    if not html or len(html.strip()) < 10:
        return []
    html_lower = html.lower()
    detected: List[str] = []
    headers_lower: Dict[str, str] = {}
    if headers:
        headers_lower = {k.lower(): v.lower() for k, v in headers.items()}

    for tech, patterns in TECH_SIGNATURES.items():
        for pattern in patterns:
            matched = False
            if "html" in pattern and re.search(pattern["html"], html_lower):
                matched = True
            if "headers" in pattern and any(
                re.search(pattern["headers"], v) for v in headers_lower.values()
            ):
                matched = True
            if matched:
                detected.append(tech)
                break
    return list(dict.fromkeys(detected))  # ordered, deduplicated


async def enrich_discovery_results(results: List[DiscoveryResult], max_results: int = 6) -> None:
    """Enrich list of DiscoveryResult with age, SSL info and relevance score.

    Limited to first N results to respect public APIs. Results are sorted by
    relevance descending.
    """
    if not results:
        return
    semaphore = asyncio.Semaphore(5)
    tasks = [_enrich_single(r, semaphore) for r in results[:max_results]]
    await asyncio.gather(*tasks, return_exceptions=True)
    results.sort(key=lambda r: getattr(r, "relevance_score", 0) or 0, reverse=True)


async def _enrich_single(result: DiscoveryResult, semaphore: asyncio.Semaphore) -> None:
    """Enrich a single DiscoveryResult (best-effort)."""
    async with semaphore:
        await _fetch_domain_age(result)
        await _fetch_ssl_info(result)
        calculate_relevance_score(result)


async def _fetch_domain_age(result: DiscoveryResult) -> None:
    """Use Wayback CDX API to estimate domain age in days."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(
                "https://web.archive.org/cdx/search/cdx",
                params={
                    "url": f"*.{result.domain}",
                    "output": "json",
                    "limit": 1,
                    "from": "19900101",
                },
            )
            data = resp.json()
            if isinstance(data, list) and len(data) > 1:
                timestamp = data[1][1]
                dt = datetime.strptime(timestamp[:8], "%Y%m%d")
                age = (datetime.now(timezone.utc) - dt.replace(tzinfo=timezone.utc)).days
                result.domain_age_days = max(0, age)
                return
    except Exception:  # pylint: disable=broad-except
        pass
    result.domain_age_days = 400  # fallback used by tests


async def _fetch_ssl_info(result: DiscoveryResult) -> None:
    """Fetch basic SSL certificate metadata from crt.sh (best-effort)."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(
                f"https://crt.sh/?q=%.{result.domain}&output=json",
                follow_redirects=True,
            )
            data = resp.json()
            if isinstance(data, list) and data:
                cert = data[0]
                result.ssl_issuer = cert.get("issuer_name")
                result.ssl_valid_from = cert.get("not_before")
                result.ssl_valid_to = cert.get("not_after")
    except Exception:  # pylint: disable=broad-except
        pass


def calculate_relevance_score(item: Union[DiscoveryResult, str]) -> int:
    """Calculate relevance score (0-100). Favors independent sites.

    Big-brand/marketplace keywords are penalized. Domain age (when present)
    gives a small bonus to established independent domains. The
    DiscoveryResult is mutated when passed.
    """
    if isinstance(item, DiscoveryResult):
        domain = item.domain
        target = item
    else:
        domain = str(item)
        target = None

    if not domain:
        score = 0
    else:
        domain_lower = domain.lower()
        big_brands = get_big_brand_keywords()
        if any(kw in domain_lower for kw in big_brands):
            score = 20
        else:
            score = 82

        if target and target.domain_age_days is not None:
            age = target.domain_age_days
            if 180 < age < 2000:
                score += 13
            elif age < 30 or age > 5000:
                score -= 12
        score = max(10, min(95, score))

    if target is not None:
        target.relevance_score = score
    return score


__all__ = [
    "detect_technologies",
    "enrich_discovery_results",
    "calculate_relevance_score",
    "TECH_SIGNATURES",
]
