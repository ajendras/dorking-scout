"""API-less technology detection for Dork Scout.

Lightweight fingerprinting inspired by Wappalyzer. Uses regex signatures
against HTML content and HTTP headers to identify payment gateways, ecommerce
platforms and other technologies. Designed for speed and zero external API
dependencies. Signatures are curated to match the dork database and avoid
false positives on common noise domains.
"""

import re
from typing import Any, Dict, List, Optional

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


__all__ = ["detect_technologies", "detect_from_response", "TECH_SIGNATURES"]
