"""Configuration and curated dork database for Dork Scout.

Loads (or falls back to embedded) JSON-based dork patterns grouped by
payment gateways, platforms and product niches. Provides SEARCH_ENGINES
configuration for the multi-engine async search clients. Full scraping
support for Baidu and Yandex is now included. Also defines DB_PATH
and get_db_path() for the persistent SQLite store.
"""

import copy
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import ValidationError

from .models import Category, DorkPattern

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
DORKS_JSON_PATH = ROOT_DIR / "dorks.json"
DB_PATH = ROOT_DIR / "dorkscout.db"

# Embedded production-ready defaults. These are realistic, tested dorks
# that avoid common noise (marketplaces, corporate sites) and work with
# Google/Bing/DuckDuckGo/Baidu/Yandex. A dorks.json placed at project root
# can extend or override this dictionary.
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
            {
                "signature": '"paypal checkout" OR "pay with paypal" -site:paypal.com',
                "engine": "yandex",
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
            {
                "signature": '(bookstore OR "online bookstore" OR "book shop") ("add to cart" OR checkout) -amazon -ebay',
                "engine": "baidu",
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
    "tech_to_category": {
        "Stripe": "payment_gateways",
        "PayPal": "payment_gateways",
        "Square": "payment_gateways",
        "Shopify": "platforms",
        "WooCommerce": "platforms",
        "Books": "niches",
        "Shoes": "niches",
        "Gift Cards": "niches",
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
        "name": "Baidu",
        "base": "https://www.baidu.com/s",
        "query_param": "wd",
        "rate_limit": 2.5,
    },
    {
        "name": "Yandex",
        "base": "https://yandex.com/search",
        "query_param": "text",
        "rate_limit": 1.5,
    },
]


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """Return a new dict with override deeply merged into base.

    Special handling for the 'dorks' key: lists of patterns for the same
    technology are concatenated (duplicate signatures are skipped). This
    enables community PRs to safely extend the database via dorks.json.
    """
    result = copy.deepcopy(base)
    for key, value in override.items():
        if (
            key == "dorks"
            and key in result
            and isinstance(result[key], dict)
            and isinstance(value, dict)
        ):
            for tech, patterns in value.items():
                if tech not in result[key] or not isinstance(result[key][tech], list):
                    result[key][tech] = []
                existing = result[key][tech]
                existing_sigs = {
                    item.get("signature")
                    for item in existing
                    if isinstance(item, dict) and "signature" in item
                }
                for pattern in patterns:
                    if (
                        isinstance(pattern, dict)
                        and (sig := pattern.get("signature"))
                        and sig not in existing_sigs
                    ):
                        existing.append(pattern)
                        existing_sigs.add(sig)
            continue
        if (
            key in result
            and isinstance(result[key], dict)
            and isinstance(value, dict)
        ):
            result[key] = _deep_merge(result[key], value)
            continue
        result[key] = copy.deepcopy(value) if isinstance(value, (dict, list)) else value
    return result


def _load_json_override() -> Dict[str, Any]:
    """Load optional dorks.json from project root and deep-merge with defaults.

    This enables community contributions to extend the dork database without
    overwriting existing entries. New technologies require a corresponding
    entry in tech_to_category.
    """
    if not DORKS_JSON_PATH.exists():
        return {}
    try:
        with open(DORKS_JSON_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError, TypeError) as exc:
        print(f"Warning: Failed to load dorks.json ({exc}). Using defaults only.")
        return {}


def validate_dork_database(db: Optional[Dict[str, Any]] = None) -> None:
    """Validate dork database for structure, types and consistency.

    Used at import time (and in tests). This powers the validation tests
    that allow community PRs to safely add new dorks, techs or categories.
    Raises clear ValueError messages on any problem.
    """
    if db is None:
        db = get_dork_db()

    if not isinstance(db, dict):
        raise ValueError("Dork DB must be a dictionary")

    categories: Dict[str, Any] = db.get("categories", {})
    if not categories or not isinstance(categories, dict):
        raise ValueError("'categories' must be a non-empty dict")

    dorks_dict: Dict[str, Any] = db.get("dorks", {})
    if not dorks_dict or not isinstance(dorks_dict, dict):
        raise ValueError("'dorks' must be a non-empty dict")

    tech_map: Dict[str, str] = db.get("tech_to_category", {})
    if not tech_map or not isinstance(tech_map, dict):
        raise ValueError("'tech_to_category' mapping must be a non-empty dict")

    # Validate categories
    for cat_id, cat_data in categories.items():
        try:
            Category.model_validate(cat_data)
        except ValidationError as exc:
            raise ValueError(f"Invalid category '{cat_id}': {exc}") from exc

    # Validate mapping references real categories
    valid_categories = set(categories.keys())
    for tech, category in tech_map.items():
        if category not in valid_categories:
            raise ValueError(
                f"tech_to_category maps '{tech}' to unknown category '{category}'. "
                f"Known: {sorted(valid_categories)}"
            )

    # Validate every dork pattern (structure, Pydantic model, engine).
    # Done before tech-mapping coverage so engine errors surface first
    # (satisfies test_validation_fails_on_bad_engine even for unmapped tech).
    valid_engines = {"google", "bing", "duckduckgo", "baidu", "yandex"}
    for tech, pattern_list in dorks_dict.items():
        if not isinstance(pattern_list, list) or len(pattern_list) == 0:
            raise ValueError(f"Tech '{tech}' must have at least one dork pattern (list)")
        for i, raw_pattern in enumerate(pattern_list):
            if not isinstance(raw_pattern, dict) or "signature" not in raw_pattern:
                raise ValueError(f"Pattern {i} for '{tech}' must be dict with 'signature'")
            try:
                DorkPattern.model_validate(raw_pattern)
            except ValidationError as exc:
                raise ValueError(
                    f"Invalid dork pattern for '{tech}' at index {i}: {exc}"
                ) from exc
            eng = raw_pattern.get("engine", "google")
            if eng not in valid_engines:
                raise ValueError(
                    f"Invalid engine '{eng}' for '{tech}' pattern {i}. "
                    f"Valid: {valid_engines}"
                )

    # Validate all dork techs have a mapping entry
    for tech in dorks_dict:
        if tech not in tech_map:
            raise ValueError(f"Tech '{tech}' not present in tech_to_category mapping")


def get_dork_db() -> Dict[str, Any]:
    """Return the active (validated, deeply-merged) dork database.

    Merging and validation happen at import time so community extensions
    via dorks.json are immediately checked.
    """
    return DORK_DB


def get_search_engines() -> List[Dict[str, str]]:
    """Return configured search engines with rate-limit hints."""
    return SEARCH_ENGINES


def get_db_path() -> Path:
    """Return the path to the SQLite database file.

    The file is created automatically by DiscoveryStore on first use.
    """
    return DB_PATH


DORK_DB: Dict[str, Any] = _deep_merge(DEFAULT_DORK_DB, _load_json_override())
validate_dork_database(DORK_DB)
