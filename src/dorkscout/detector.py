"""API-less technology detection for Dork Scout.

Lightweight fingerprinting inspired by Wappalyzer. Uses regex signatures
against HTML content and HTTP headers to identify payment gateways, ecommerce
platforms and other technologies. Designed for speed and zero external API
dependencies. Signatures are curated to match the dork database and avoid
false positives on common noise domains.

Now also includes async enrichment of domain age (via Wayback Machine CDX API)
and SSL certificate metadata (via crt.sh Certificate Transparency endpoint).
These are free, public sources with no API keys required.
"""

import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import asyncio
import httpx

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
        {"html": r"data-stripe"},
        {"html": r"stripe\.js"},
        {"html": r'"stripe":\s*\{'},
    ],
    "PayPal": [
        {"html": r"paypalobjects\.com"},
        {"html": r"PayPal\.Buttons"},
        {"html": r"paypal\.com/sdk"},
        {"html": r"btn_paypal"},
        {"html": r"paypal-express"},
    ],
    "Square": [
        {"html": r"squareup\.com"},
        {"html": r"sqpaymentform"},
        {"html": r"square\.site"},
        {"html": r"commerce\.square"},
    ],
    "WooCommerce": [
        {"html": r"woocommerce"},
        {"html": r"wc-"},
        {"html": r"/wp-content/plugins/woocommerce"},
        {"html": r"var wc_add_to_cart_params"},
        {"html": r"woocommerce-checkout"},
        {"header": r"x-powered-by.*woocommerce"},
    ],
    "Magento": [
        {"html": r"mage-"},
        {"html": r"magento"},
        {"html": r"mage\.js"},
        {"html": r"static/version"},
    ],
    "BigCommerce": [
        {"html": r"bigcommerce"},
        {"html": r"stencil"},
        {"html": r"bcapp"},
    ],
}


def detect_technologies(
    html: str = "", headers: Optional[Dict[str, Any]] = None
) -> List[str]:
    """Detect technologies present on a webpage using regex signatures.

    Lightweight, synchronous scanner that works on raw HTML and response
    headers. Returns a deduplicated, sorted list of technology names that
    match the curated signatures (focused on the dork database).

    Args:
        html: Raw HTML content returned by the target site. Empty string
            is allowed and results in an empty list.
        headers: Optional dictionary of HTTP response headers. Keys are
            case-insensitive.

    Returns:
        Sorted list of detected technology names (e.g. ["Stripe", "Shopify"]).

    Raises:
        ValueError: If html is not a string.
    """
    if not isinstance(html, str):
        raise ValueError(
            "html must be a string. Got "
            f"{type(html).__name__} instead."
        )

    if headers is None:
        headers = {}

    if not html or len(html.strip()) < 50:
        return []

    # Truncate very large pages for performance (most fingerprinting
    # signals appear in the first 30-50 KB)
    html = html[:60000]
    html_lower = html.lower()

    headers_lower: Dict[str, str] = {
        k.lower(): str(v).lower() for k, v in headers.items()
    }

    detected: set[str] = set()

    for tech, rules in TECH_SIGNATURES.items():
        for rule in rules:
            matched = False

            if "html" in rule:
                pattern = rule["html"]
                if re.search(pattern, html_lower, re.IGNORECASE | re.DOTALL):
                    matched = True

            elif "header" in rule:
                pattern = rule["header"]
                for header_value in headers_lower.values():
                    if re.search(pattern, header_value, re.IGNORECASE):
                        matched = True
                        break

            if matched:
                detected.add(tech)
                break  # One match per tech is enough

    return sorted(detected)


def detect_from_response(
    html: str, status_code: int = 200, headers: Optional[Dict[str, Any]] = None
) -> List[str]:
    """Convenience wrapper for responses that also considers status.

    Useful for example scripts that have full response objects.
    Non-2xx responses return empty list.
    """
    if not 200 <= status_code < 300:
        return []
    return detect_technologies(html, headers)


async def enrich_metadata(domain: str) -> Dict[str, Any]:
    """Enrich a domain with age estimate and SSL certificate metadata using
    free public sources. No API keys or accounts required.

    Sources:
      * Wayback Machine CDX API for first-capture timestamp (domain age)
      * crt.sh JSON endpoint for Certificate Transparency data (issuer, validity)

    Failures are silent; partial results are still useful.
    """
    enrichment: Dict[str, Any] = {
        "domain_age_days": None,
        "creation_date": None,
        "ssl_issuer": None,
        "ssl_valid_from": None,
        "ssl_valid_to": None,
    }

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(6.0),
        headers={"User-Agent": "DorkScout-Enrich/0.2"},
    ) as client:
        # Domain age from Wayback Machine
        try:
            cdx_url = "https://web.archive.org/cdx/search/cdx"
            params = {
                "url": f"*.{domain}",
                "matchType": "domain",
                "limit": 1,
                "output": "json",
                "filter": "statuscode:200",
                "from": "1996",
            }
            resp = await client.get(cdx_url, params=params)
            if resp.is_success:
                data = resp.json()
                if isinstance(data, list) and len(data) > 1:
                    ts_str = data[1][1]  # timestamp is second column
                    if isinstance(ts_str, str) and len(ts_str) >= 8:
                        dt = datetime.strptime(ts_str[:8], "%Y%m%d")
                        enrichment["creation_date"] = dt.strftime("%Y-%m-%d")
                        age = (datetime.now(timezone.utc) - dt.replace(tzinfo=timezone.utc)).days
                        enrichment["domain_age_days"] = max(age, 0)
        except Exception:  # pylint: disable=broad-except
            pass

        # SSL metadata from crt.sh
        try:
            ct_url = f"https://crt.sh/?q=%25.{domain}&output=json"
            resp = await client.get(ct_url)
            if resp.is_success:
                certs = resp.json()
                if isinstance(certs, list) and len(certs) > 0:
                    from_dates = [c.get("not_before") for c in certs if isinstance(c, dict) and c.get("not_before")]
                    to_dates = [c.get("not_after") for c in certs if isinstance(c, dict) and c.get("not_after")]
                    issuer_list = []
                    for c in certs:
                        if isinstance(c, dict) and isinstance(c.get("issuer_name"), str):
                            issuer = c["issuer_name"]
                            # Clean up to just the CN part
                            if "CN=" in issuer:
                                issuer = issuer.split("CN=")[-1].split(",")[0].strip('" ')
                            issuer_list.append(issuer)
                    if from_dates:
                        enrichment["ssl_valid_from"] = min(d for d in from_dates if d)
                    if to_dates:
                        enrichment["ssl_valid_to"] = max(d for d in to_dates if d)
                    if issuer_list:
                        # Use most common issuer
                        counter: Dict[str, int] = {}
                        for iss in issuer_list:
                            counter[iss] = counter.get(iss, 0) + 1
                        enrichment["ssl_issuer"] = max(counter, key=counter.get)  # type: ignore[arg-type]
        except Exception:  # pylint: disable=broad-except
            pass

    return enrichment


async def enrich_discovery_results(results: List[DiscoveryResult]) -> None:
    """Apply metadata enrichment to a list of DiscoveryResult objects (in-place).

    Uses bounded concurrency. Only enriches the first 6 results to stay responsive
    and respect public service load.
    """
    if not results:
        return

    sem = asyncio.Semaphore(3)

    async def _do_enrich(result: DiscoveryResult):
        async with sem:
            meta = await enrich_metadata(result.domain)
            for field, value in meta.items():
                if value is not None:
                    setattr(result, field, value)

    tasks = [_do_enrich(r) for r in results[:6]]
    await asyncio.gather(*tasks, return_exceptions=True)


__all__ = [
    "detect_technologies",
    "detect_from_response",
    "TECH_SIGNATURES",
    "enrich_metadata",
    "enrich_discovery_results",
]
