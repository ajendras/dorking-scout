"""Core dork loading, filtering and query-building logic for Dork Scout.

Provides a clean interface over the JSON/embedded dork database. Supports
filtering by broad category or specific technology. All functions perform
input validation and return properly typed Pydantic models.

Also implements an async local-LLM helper (via Ollama) that can generate
fresh, high-quality dork patterns for technologies that are not yet in the
database. The generator now explicitly encourages advanced operators
(filetype:, intitle:, inurl:, intext:, site:) especially for the new
'documents' category.
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
    """Retrieve DorkPattern instances for a category or specific tech.

    If both are provided, tech takes precedence. Raises ValueError for
    unknown values. For the new 'documents' category, patterns make heavy
    use of advanced operators (filetype:, intitle:, inurl: etc.).
    """
    db = load_dorks()
    dorks_dict: Dict[str, List[Dict]] = db.get("dorks", {})
    tech_map = get_tech_to_category()

    if tech:
        if tech not in dorks_dict or not dorks_dict[tech]:
            raise ValueError(f"No dorks available for technology '{tech}'")
        return [DorkPattern.model_validate(p) for p in dorks_dict[tech]]

    if category:
        if category not in db.get("categories", {}):
            raise ValueError(f"Unknown category '{category}'")
        matching_techs = [t for t, cat in tech_map.items() if cat == category]
        if not matching_techs:
            raise ValueError(f"No technologies mapped to category '{category}'")
        patterns: List[DorkPattern] = []
        for t in matching_techs:
            if t in dorks_dict:
                patterns.extend(
                    DorkPattern.model_validate(p) for p in dorks_dict[t]
                )
        return patterns

    raise ValueError("Either 'category' or 'tech' must be provided")


def build_query(dork: DorkPattern, term: str = "") -> str:
    """Combine a DorkPattern signature with an optional additional term.

    Advanced operators present in the signature (e.g. filetype:pdf,
    intitle:invoice, inurl:admin, intext:, -site:) are left untouched.
    The term is quoted as a phrase search unless it already looks quoted
    or itself starts with an operator. This enables precise document
    hunting queries such as:
        filetype:pdf intitle:confidential "quarterly report"
    """
    if not term or not str(term).strip():
        return dork.signature

    term = str(term).strip()
    # If the term already uses an operator or is quoted, append as-is
    if re.search(r"^(filetype|intitle|inurl|intext|site):", term, re.IGNORECASE) or (
        (term.startswith('"') and term.endswith('"'))
        or (term.startswith("'") and term.endswith("'"))
    ):
        return f"{dork.signature} {term}"

    # Default to a quoted phrase for maximum precision
    return f'{dork.signature} "{term}"'


def list_all_techs() -> List[str]:
    """Return sorted list of all technologies with dorks."""
    return sorted(load_dorks().get("dorks", {}).keys())


async def generate_dorks_for_tech(
    tech_name: str,
    category: Optional[str] = None,
    num_dorks: int = 4,
) -> List[DorkPattern]:
    """Use local Ollama to generate realistic dork patterns for a new tech.

    The prompt now explicitly encourages use of advanced operators such as
    filetype:, intitle:, inurl:, intext: when the requested category is
    'documents' or when it makes sense for document hunting.

    Falls back to a basic pattern if Ollama is unreachable.
    """
    config = get_llm_config()
    prompt = f"""You are an expert in Google dorks and OSINT.
Generate {num_dorks} high-quality, tested search dork patterns for '{tech_name}'.
Category: {category or 'general'}.

Each pattern must be a JSON object with "signature" (the search query - use advanced operators like filetype:pdf, intitle:, inurl:, intext:, -site:, OR where they improve precision, especially for document hunting) and "engine" (one of: google, bing, duckduckgo, baidu, yandex).

Return ONLY a valid JSON array of objects, no extra text or explanation.

Example for a document-related tech:
[
  {{"signature": "filetype:pdf intitle:\\"powered by {tech_name}\\" OR intitle:\\"{tech_name} report\\"", "engine": "google"}},
  {{"signature": "intitle:\\"{tech_name}\\" filetype:pdf -inurl:(login signup)", "engine": "bing"}}
]
"""

    try:
        async with httpx.AsyncClient(timeout=config["timeout"]) as client:
            resp = await client.post(
                config["url"],
                json={
                    "model": config["model"],
                    "prompt": prompt,
                    "temperature": config["temperature"],
                },
                headers={"Content-Type": "application/json"},
            )
            resp.raise_for_status()
            data = resp.json()
            generated_text = data.get("response", str(data))

            # Extract JSON array (LLM output can be verbose)
            match = re.search(r"\[\s*\{.*\}\s*\]", generated_text, re.DOTALL)
            if match:
                json_str = match.group(0)
                raw_patterns = json.loads(json_str)
            else:
                raw_patterns = json.loads(generated_text)

            if not isinstance(raw_patterns, list):
                raw_patterns = [raw_patterns]

            validated: List[DorkPattern] = []
            for p in raw_patterns[:num_dorks]:
                if isinstance(p, dict) and "engine" not in p:
                    p["engine"] = "google"
                validated.append(DorkPattern.model_validate(p))
            return validated
    except Exception:  # pylint: disable=broad-except
        # Graceful fallback
        fallback_sig = f'"{tech_name}" OR "powered by {tech_name}"'
        if category == "documents":
            fallback_sig = f'filetype:pdf intitle:"{tech_name}" OR intitle:"powered by {tech_name}"'
        return [
            DorkPattern(signature=fallback_sig, engine="google")
            for _ in range(min(2, num_dorks))
        ]


__all__ = [
    "load_dorks",
    "get_dorks",
    "build_query",
    "list_all_techs",
    "generate_dorks_for_tech",
    "get_tech_to_category",
]
