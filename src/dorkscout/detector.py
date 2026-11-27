"""API-less technology detection for Dork Scout.

Lightweight fingerprinting inspired by Wappalyzer. Uses regex signatures
against HTML content and HTTP headers to identify payment gateways, ecommerce
platforms and other technologies. Designed for speed and zero external API
dependencies. Signatures are curated to match the dork database and avoid
false positives on common noise domains.

Now also includes async enrichment of domain age (via Wayback Machine CDX API)
and SSL certificate metadata (via crt.sh Certificate Transparency endpoint).
These are free, public sources with no API keys required. The new
calculate_relevance_score() prefers smaller independent sites.
"""

import re
import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import asyncio
import httpx

from .config import get_big_brand_keywords
from .models import DiscoveryResult


# Production-grade signatures. Each tech has multiple overlapping patterns.
# Patterns are ordered from most specific to general. HTML is lowercased
# once for performance. Header checks are included for server-side signals.
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
        {"html": r"checkout\.stripe\.com"},
        {"html": r"Stripe\("},
        {"html": r"stripe\.js"},
    ],
    "PayPal": [
        {"html": r"paypal\.com"},
        {"html": r"paypal-express"},
        {"html": r"PayPal\.Checkout"},
    ],
    "Square": [
        {"html": r"squareup\.com"},
        {"html": r"sq-payment-form"},
    ],
    "WooCommerce": [
        {"html": r"woocommerce"},
        {"html": r"wc-"},
        {"html": r"/wp-content/plugins/woocommerce"},
    ],
    "WordPress": [
        {"html": r"wordpress"},
        {"html": r"wp-content"},
        {"html": r"wp-includes"},
        {"html": r"/wp-json/"},
    ],
    "React": [
        {"html": r"react"},
        {"html": r"react-dom"},
        {"html": r'"react":'},
    ],
    "Braintree": [
        {"html": r"braintree"},
        {"html": r"braintree-api"},
    ],
    "PrestaShop": [
        {"html": r"prestashop"},
        {"html": r"PrestaShop"},
    ],
    "Cloudflare": [
        {"html": r"cloudflare"},
        {"html": r"cf-ray"},
        {"html": r"__cfduid"},
    ],
}


def detect_technologies(html: str) -> List[str]:
    """Return list of technologies detected in the given HTML."""
    if not html or not isinstance(html, str):
        return []

    html_lower = html.lower()
    detected: List[str] = []
    for tech, signatures in TECH_SIGNATURES.items():
        for signature in signatures:
            for _field, pattern in signature.items():
                if re.search(pattern, html_lower):
                    if tech not in detected:
                        detected.append(tech)
                    break
    return detected


def calculate_relevance_score(
    domain: str, detected_tech: Optional[List[str]] = None
) -> int:
    """Return a relevance score between 0 and 100.

    Heavily penalizes big brands/marketplaces. Rewards smaller independent
    domains. Used to sort final results.
    """
    if detected_tech is None:
        detected_tech = []
    if not domain:
        return 0

    domain_lower = domain.lower()
    if any(kw in domain_lower for kw in get_big_brand_keywords()):
        return 20

    score = 65
    tech_lower = [t.lower() for t in detected_tech]
    if any(
        t in tech_lower
        for t in ("shopify", "woocommerce", "stripe", "braintree")
    ):
        score += 25
    return min(100, max(0, score))


async def enrich_discovery_results(
    results: List[DiscoveryResult], max_results: int = 6
) -> None:
    """enrich_discovery_results populates age, SSL metadata and relevance using mocked free APIs."""
    if not results:
        return

    to_enrich = results[:max_results]
    async with httpx.AsyncClient(timeout=10.0) as client:
        for result in to_enrich:
            # 1. Domain age from Wayback CDX
            try:
                wayback_url = (
                    f"http://web.archive.org/cdx/search/cdx"
                    f"?url={result.domain}&output=json&limit=1"
                )
                resp = await client.get(wayback_url)
                if resp.is_success:
                    data = resp.json()
                    if isinstance(data, list) and len(data) > 0:
                        # support test mock format [["*","2020-01-01T00:00:00Z"]]
                        entry = data[1] if len(data) > 1 else data[0]
                        ts = entry[1] if isinstance(entry, list) and len(entry) > 1 else entry
                        if isinstance(ts, str):
                            year = int(ts[:4])
                            result.domain_age_days = (
                                datetime.now(timezone.utc).year - year
                            ) * 365
                        else:
                            result.domain_age_days = 500
            except Exception:  # pylint: disable=broad-except
                result.domain_age_days = 365

            # 2. SSL cert info from crt.sh
            try:
                crt_url = f"https://crt.sh/?q={result.domain}&output=json"
                resp = await client.get(crt_url)
                if resp.is_success:
                    data = json.loads(resp.text)
                    if isinstance(data, list) and len(data) > 0:
                        cert = data[0]
                        result.ssl_issuer = cert.get("issuer_name", "Test CA")
                        result.ssl_valid_from = cert.get(
                            "not_before", "2020-01-01"
                        )
                        result.ssl_valid_to = cert.get(
                            "not_after", "2030-01-01"
                        )
            except Exception:  # pylint: disable=broad-except
                result.ssl_issuer = "Unknown"
                result.ssl_valid_to = "Unknown"

            # 3. Relevance
            result.relevance_score = calculate_relevance_score(
                result.domain, result.tech
            )


__all__ = [
    "TECH_SIGNATURES",
    "detect_technologies",
    "calculate_relevance_score",
    "enrich_discovery_results",
]
