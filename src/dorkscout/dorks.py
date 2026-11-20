"""Core dork loading, filtering and query-building logic for Dork Scout.

Provides a clean interface over the JSON/embedded dork database. Supports
filtering by broad category or specific technology. All functions perform
input validation and return properly typed Pydantic models.

Also implements an async local-LLM helper (via Ollama) that can generate
fresh, high-quality dork patterns for technologies that are not yet in the
database.
"""

import json
import re
from typing import Any, Dict, List, Optional

import httpx

from .config import get_dork_db, get_llm_config, validate_dork_database
from .models import DorkPattern


def load_dorks() -> Dict:
    """Return the complete dork database (categories + dorks).

    The returned dict is a deep merge of the embedded defaults and any
    dorks.json file present at project root. The database is validated
    on import (see validate_dork_database() and tests/test_dorks.py).
    Used internally by get_dorks() and exposed for advanced use in
    example scripts.
    """
    return get_dork_db()


def get_tech_to_category() -> Dict[str, str]:
    """Return tech-to-category mapping loaded from the (validated) database.

    Community extensions can add new techs by supplying both dork patterns
    and a mapping entry in dorks.json.
    """
    return load_dorks().get("tech_to_category", {})


def get_dorks(
    category: Optional[str] = None, tech: Optional[str] = None
) -> List[DorkPattern]:
    """Retrieve dork patterns filtered by category or specific technology.

    Exactly one of category or tech may be supplied. When neither is given
    all patterns are returned. Patterns are converted into validated
    DorkPattern models. The underlying database is validated on load.

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
    tech_to_category = get_tech_to_category()

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
        valid = sorted(set(tech_to_category.values()))
        if category not in valid:
            raise ValueError(f"Unknown category '{category}'. Valid: {valid}")

        # Collect every tech that belongs to the requested category
        for t, cat in tech_to_category.items():
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
    When a term is supplied it is quoted (unless already quoted by the caller)
    and appended (implicit AND). This ensures the term is treated as a phrase
    for better precision on both single-word and multi-word inputs. The
    returned string is safe to pass directly to search engines.

    Args:
        dork: Validated DorkPattern instance.
        term: Additional keyword or phrase (e.g. "handmade", "vintage leather").
            Whitespace is normalised; empty strings are ignored.

    Returns:
        Complete query string.
    """
    if not isinstance(dork, DorkPattern):
        raise TypeError("dork must be a DorkPattern instance")

    query = dork.signature.strip()

    cleaned_term = (term or "").strip()
    if cleaned_term:
        if not (
            (cleaned_term.startswith('"') and cleaned_term.endswith('"'))
            or (cleaned_term.startswith("'") and cleaned_term.endswith("'"))
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


def _extract_json_from_llm_response(text: str) -> List[Dict[str, Any]]:
    """Robustly extract a JSON array from an LLM response.

    Handles cases where the model returns extra prose, markdown code fences
    or malformed JSON.
    """
    text = text.strip()
    if not text:
        return []

    # Strip markdown fences if present
    if text.startswith("```"):
        text = text.split("```", 2)[-2].strip()
        if text.startswith("json"):
            text = text[4:].strip()

    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            return parsed
    except json.JSONDecodeError:
        pass

    # Try to locate the first JSON array in the text
    match = re.search(r"(\[[\s\S]*\])", text, re.DOTALL)
    if match:
        try:
            parsed = json.loads(match.group(1))
            if isinstance(parsed, list):
                return parsed
        except json.JSONDecodeError:
            pass

    return []


async def generate_dorks_for_tech(
    tech_name: str,
    category: Optional[str] = None,
    num_dorks: int = 4,
    model: Optional[str] = None,
) -> List[DorkPattern]:
    """Generate new dork patterns for an unknown technology using a local LLM (Ollama).

    Requires Ollama running locally (`ollama serve`) with a capable model
    pulled (recommended: `ollama pull llama3.2`). The prompt is engineered
    to produce specific, low-noise signatures that work across multiple
    search engines. Returned patterns are always validated through the
    existing Pydantic model.

    If the LLM call fails (connection error, JSON parse error, etc.) a
    simple fallback dork based on the tech name is returned.

    Args:
        tech_name: Name of the technology (e.g. "AcmePayments", "NewShop").
        category: Optional category hint ("payment_gateways", "platforms", "niches").
        num_dorks: How many distinct patterns to request (capped at 8).
        model: Override the default model from config.

    Returns:
        List of validated DorkPattern objects (1 or more).
    """
    if not tech_name or not isinstance(tech_name, str):
        raise ValueError("tech_name must be a non-empty string")

    config = get_llm_config()
    llm_model = model or config["model"]
    cat = category or "platforms"
    n = min(max(int(num_dorks), 1), 8)

    prompt = f"""You are an expert OSINT and Google dorking specialist.
Generate exactly {n} high-signal search dorks that can be used to discover
websites using the unknown technology named '{tech_name}'.

Category hint: {cat}
Focus on:
- "powered by", script src, meta tags, URL patterns, footer text
- Specific exclusions for marketplaces, github, corporate sites
- Varied engines (google, bing, duckduckgo, baidu, yandex)

Return **ONLY** a valid JSON array of objects. No extra text.
Each object must contain "signature" and "engine".

Example:
[
  {{"signature": "\\"powered by {tech_name.lower()}\\" OR inurl:{tech_name.lower()}/checkout -site:github.com", "engine": "google"}},
  {{"signature": "intitle:{tech_name} dashboard -inurl:(login admin)", "engine": "bing"}}
]
"""

    try:
        async with httpx.AsyncClient(timeout=config["timeout"]) as client:
            resp = await client.post(
                config["url"],
                json={
                    "model": llm_model,
                    "prompt": prompt,
                    "stream": False,
                    "format": "json",
                    "options": {"temperature": config["temperature"]},
                },
            )
            resp.raise_for_status()
            data = resp.json()
            response_text = data.get("response", "")

            raw_list = _extract_json_from_llm_response(response_text)
            if not isinstance(raw_list, list):
                raw_list = []

            patterns: List[DorkPattern] = []
            valid_engines = {"google", "bing", "duckduckgo", "baidu", "yandex"}

            for item in raw_list[:n]:
                if not isinstance(item, dict) or "signature" not in item:
                    continue
                if "engine" not in item or item["engine"] not in valid_engines:
                    item["engine"] = "google"
                # Ensure some noise reduction is present
                sig = item["signature"]
                if not any(ex in sig.lower() for ex in ("-site:", "-inurl:", "-intitle:")):
                    sig += " -site:github.com -site:gitlab.com"
                    item["signature"] = sig
                patterns.append(DorkPattern.model_validate(item))

            if patterns:
                return patterns

    except Exception as exc:  # pylint: disable=broad-except
        print(f"Local LLM dork generation for '{tech_name}' failed: {exc}")
        print(
            "Make sure Ollama is running (`ollama serve`) "
            "and the model is pulled (`ollama pull llama3.2`)."
        )

    # Graceful fallback when LLM is unavailable
    fallback = f'"powered by {tech_name}" OR "{tech_name.lower()}" (checkout OR store OR dashboard) -site:github.com'
    return [DorkPattern(signature=fallback, engine="google")]


__all__ = [
    "load_dorks",
    "get_dorks",
    "build_query",
    "get_category_info",
    "list_all_techs",
    "get_tech_to_category",
    "generate_dorks_for_tech",
]
