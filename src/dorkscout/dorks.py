"""Core dork loading, filtering and query-building logic for Dork Scout.

Provides a clean interface over the JSON/embedded dork database. Supports
filtering by broad category or specific technology. All functions perform
input validation and return properly typed Pydantic models.
"""

from typing import Dict, List, Optional

from .config import get_dork_db
from .models import DorkPattern

# Hard-coded mapping from technology keys to their logical category.
# This is derived from the curated DEFAULT_DORK_DB and allows get_dorks()
# to return relevant patterns when a category filter is supplied.
# A dorks.json override can extend the dork database but must respect
# these category keys for the mapping to remain consistent.
TECH_TO_CATEGORY: Dict[str, str] = {
    "Stripe": "payment_gateways",
    "PayPal": "payment_gateways",
    "Square": "payment_gateways",
    "Shopify": "platforms",
    "WooCommerce": "platforms",
    "Books": "niches",
    "Shoes": "niches",
    "Gift Cards": "niches",
}


def load_dorks() -> Dict:
    """Return the complete dork database (categories + dorks).

    The returned dict is a merge of the embedded defaults and any
    dorks.json file present at project root. Used internally by
    get_dorks() and exposed for advanced use in example scripts.

    Returns:
        Dict containing 'categories' and 'dorks' keys.
    """
    return get_dork_db()


def get_dorks(
    category: Optional[str] = None, tech: Optional[str] = None
) -> List[DorkPattern]:
    """Retrieve dork patterns filtered by category or specific technology.

    Exactly one of category or tech may be supplied. When neither is given
    all patterns are returned. Patterns are converted into validated
    DorkPattern models.

    Args:
        category: One of 'payment_gateways', 'platforms', 'niches'.
        tech: Technology name exactly matching a key in the dork database
            (e.g. 'Stripe', 'Books').

    Returns:
        List of DorkPattern instances ready for query building.

    Raises:
        ValueError: For unknown category or tech, or when both filters are
            supplied (ambiguous).
    """
    if category is not None and tech is not None:
        raise ValueError("Provide either category or tech, not both.")

    db = load_dorks()
    dork_data: Dict[str, List[Dict]] = db.get("dorks", {})

    patterns: List[DorkPattern] = []

    if tech:
        if tech not in dork_data or not dork_data[tech]:
            raise ValueError(
                f"Unknown or empty tech '{tech}'. "
                f"Available: {sorted(dork_data.keys())}"
            )
        for raw in dork_data[tech]:
            patterns.append(DorkPattern.model_validate(raw))
        return patterns

    if category:
        if category not in TECH_TO_CATEGORY.values():
            valid = sorted(set(TECH_TO_CATEGORY.values()))
            raise ValueError(f"Unknown category '{category}'. Valid: {valid}")

        # Collect every tech that belongs to the requested category
        for t, cat in TECH_TO_CATEGORY.items():
            if cat == category and t in dork_data:
                for raw in dork_data[t]:
                    patterns.append(DorkPattern.model_validate(raw))
        return patterns

    # Return everything when no filter is supplied
    for tech_patterns in dork_data.values():
        for raw in tech_patterns:
            patterns.append(DorkPattern.model_validate(raw))

    return patterns


def build_query(dork: DorkPattern, term: str = "") -> str:
    """Construct a final search-engine query from a dork pattern and optional term.

    The dork.signature already contains strong operators and noise exclusions.
    When a term is supplied it is appended (implicit AND) so the caller can
    further narrow results (e.g. a product vertical that is not already
    encoded in the dork). The returned string is safe to pass directly to
    search engines.

    Args:
        dork: Validated DorkPattern instance.
        term: Additional keyword or phrase (e.g. "handmade", "vintage").
            Whitespace is normalised; empty strings are ignored.

    Returns:
        Complete query string.
    """
    if not isinstance(dork, DorkPattern):
        raise TypeError("dork must be a DorkPattern instance")

    query = dork.signature.strip()

    cleaned_term = (term or "").strip()
    if cleaned_term:
        # Surround multi-word terms with quotes for precision
        if " " in cleaned_term and not (
            cleaned_term.startswith('"') and cleaned_term.endswith('"')
        ):
            cleaned_term = f'"{cleaned_term}"'
        query = f"{query} {cleaned_term}"

    return query


def get_category_info(category_id: str) -> Dict:
    """Return metadata for a given category (used by web UI and API).

    Raises ValueError for unknown categories.
    """
    db = load_dorks()
    cats = db.get("categories", {})
    if category_id not in cats:
        raise ValueError(
            f"Unknown category_id '{category_id}'. "
            f"Available: {list(cats.keys())}"
        )
    return cats[category_id]


def list_all_techs() -> List[str]:
    """Return every technology name that has at least one dork pattern.

    Useful for autocomplete and the niche explorer.
    """
    db = load_dorks()
    return sorted(db.get("dorks", {}).keys())


__all__ = [
    "load_dorks",
    "get_dorks",
    "build_query",
    "get_category_info",
    "list_all_techs",
    "TECH_TO_CATEGORY",
]
