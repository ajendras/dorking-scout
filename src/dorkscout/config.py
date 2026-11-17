"""Configuration and curated dork database for Dork Scout.

Loads (or falls back to embedded) JSON-based dork patterns grouped by
payment gateways, platforms and product niches. Provides SEARCH_ENGINES
configuration for the multi-engine async search clients.
"""

import json
from pathlib import Path
from typing import Any, Dict, List

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
DORKS_JSON_PATH = ROOT_DIR / "dorks.json"

# Embedded production-ready defaults. These are realistic, tested dorks
# that avoid common noise (marketplaces, corporate sites) and work with
# Google/Bing/DuckDuckGo. A dorks.json placed at project root can extend
# or override this dictionary.
DEFAULT_DORK_DB: Dict[str, Any] = {
    "categories": {
        "payment_gateways": {
            "id": "payment_gateways",
            "name": "Payment Gateways",
            "icon": "💳",
            "color": "#c56a3b",
            "description": "Discover stores using specific payment processors.",
        },
        "platforms": {
            "id": "platforms",
            "name": "Platforms & CMS",
            "icon": "🛠",
            "color": "#6f8a5e",
            "description": "Find sites built on popular ecommerce platforms.",
        },
        "niches": {
            "id": "niches",
            "name": "Product Niches",
            "icon": "📦",
            "color": "#c56a3b",
            "description": "Explore vertical-specific online stores (books, shoes, gifts).",
        },
    },
    "dorks": {
        "Stripe": [
            {
                "signature": '"powered by stripe" OR "checkout.stripe.com" -site:stripe.com -site:github.com -site:dashboard',
                "engine": "google",
            },
            {
                "signature": 'inurl:stripe intitle:checkout -inurl:(admin OR login OR dashboard)',
                "engine": "bing",
            },
        ],
        "PayPal": [
            {
                "signature": '"paypal checkout" OR "pay with paypal" OR "paypal express" -site:paypal.com',
                "engine": "google",
            },
        ],
        "Square": [
            {
                "signature": '"powered by square" OR "square online" checkout',
                "engine": "google",
            },
        ],
        "Shopify": [
            {
                "signature": '"powered by shopify" OR ".myshopify.com" -inurl:admin',
                "engine": "google",
            },
            {
                "signature": 'inurl:.myshopify.com intitle:store',
                "engine": "duckduckgo",
            },
        ],
        "WooCommerce": [
            {
                "signature": '("woocommerce" OR "wc-") (checkout OR cart) ("powered by" OR "/wc-")',
                "engine": "google",
            },
        ],
        "Books": [
            {
                "signature": '(bookstore OR "online bookstore" OR "book shop") ("add to cart" OR checkout) -amazon -ebay',
                "engine": "google",
            },
        ],
        "Shoes": [
            {
                "signature": '("running shoes" OR sneakers OR footwear) ("buy now" OR "add to cart" OR checkout) -amazon -ebay',
                "engine": "google",
            },
        ],
        "Gift Cards": [
            {
                "signature": '("egift card" OR "electronic gift card" OR giftcard) (buy OR purchase OR store) -amazon',
                "engine": "google",
            },
        ],
    },
}

SEARCH_ENGINES: List[Dict[str, str]] = [
    {
        "name": "Google",
        "base": "https://www.google.com/search",
        "query_param": "q",
        "rate_limit": 1.0,
    },
    {
        "name": "Bing",
        "base": "https://www.bing.com/search",
        "query_param": "q",
        "rate_limit": 0.8,
    },
    {
        "name": "DuckDuckGo",
        "base": "https://html.duckduckgo.com/html",
        "query_param": "q",
        "rate_limit": 2.0,
    },
    {
        "name": "Yandex",
        "base": "https://yandex.com/search",
        "query_param": "text",
        "rate_limit": 1.5,
    },
]


def _load_json_override() -> Dict[str, Any]:
    """Load optional dorks.json from project root and merge with defaults."""
    if not DORKS_JSON_PATH.exists():
        return {}
    try:
        with open(DORKS_JSON_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"Warning: Failed to load dorks.json ({exc}). Using defaults only.")
        return {}


# Final module-level database (JSON overrides take precedence)
DORK_DB: Dict[str, Any] = {**DEFAULT_DORK_DB, **_load_json_override()}


def get_dork_db() -> Dict[str, Any]:
    """Return the active dork database (for use by dorks.py)."""
    return DORK_DB


def get_search_engines() -> List[Dict[str, str]]:
    """Return configured search engines with rate-limit hints."""
    return SEARCH_ENGINES
