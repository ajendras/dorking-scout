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
    category: Optional[str] = None,
    tech: Optional[str] = None,
) -> List[DorkPattern]:
    """Return dork patterns for a category or specific tech.

    If tech is given, returns its patterns (must exist in mapping).
    If category, returns all dorks for techs in that category.
    Raises ValueError for unknown tech or empty.
    """
    db = load_dorks()
    dorks_dict = db["dorks"]
    tech_map = db["tech_to_category"]

    if tech:
        tech = tech.strip()
        if not tech or tech not in dorks_dict:
            available = sorted(dorks_dict.keys())
            raise ValueError(
                f"Unknown or empty tech '{tech}'. Available: {available}"
            )
        raw_patterns = dorks_dict[tech]
        return [DorkPattern.model_validate(p) for p in raw_patterns]

    if category:
        category = category.strip().lower()
        techs_in_cat = [t for t, c in tech_map.items() if c == category]
        if not techs_in_cat:
            raise ValueError(f"No techs found for category '{category}'")
        patterns = []
        for t in techs_in_cat:
            patterns.extend(get_dorks(tech=t))
        return patterns

    # return all if neither provided
    all_patterns = []
    for t in dorks_dict:
        all_patterns.extend(get_dorks(tech=t))
    return all_patterns


def build_query(dork: DorkPattern, term: str = "") -> str:
    """Build the final search query by appending an optional term (always quoted for precision)."""
    query = dork.signature
    term = (term or "").strip()
    if term:
        if not (term.startswith('"') and term.endswith('"')) and not any(
            op in term for op in ["OR", "AND", "-"]
        ):
            term = f'"{term}"'
        query = f"{query} {term}"
    return query


def list_all_techs() -> List[str]:
    """Return sorted list of all technologies with dorks."""
    return sorted(load_dorks()["dorks"].keys())


async def generate_dorks_for_tech(
    tech_name: str,
    category: Optional[str] = None,
    num_dorks: int = 4,
) -> List[DorkPattern]:
    """Generate dork patterns for a new technology using local Ollama LLM.

    Falls back to generic patterns if LLM call fails or parsing fails.
    """
    if not tech_name or not tech_name.strip():
        raise ValueError("tech_name is required")

    tech_name = tech_name.strip()
    config = get_llm_config()
    prompt = f"""You are an expert in Google dorks for OSINT.

Generate exactly {num_dorks} high-quality, unique search dork patterns for the technology '{tech_name}'.
Focus on finding real websites using this tech, avoiding official sites, github, etc.
Return ONLY a valid JSON array of objects, each with "signature" (the dork string) and "engine" (google, bing, duckduckgo, baidu or yandex).

Example:
[ {{"signature": "inurl:{tech_name.lower()}", "engine": "google"}}, {{"signature": "\\"powered by {tech_name}\\"", "engine": "bing"}} ]

Make them effective and varied. Do not include explanations."""

    try:
        async with httpx.AsyncClient(timeout=config.get("timeout", 30.0)) as client:
            payload = {
                "model": config["model"],
                "prompt": prompt,
                "stream": False,
                "temperature": config.get("temperature", 0.7),
            }
            response = await client.post(config["url"], json=payload)
            response.raise_for_status()
            data = response.json()
            response_text = data.get("response", "").strip()
            if not response_text:
                raise ValueError("Empty response from Ollama")

            # Extract JSON array (handles extra markdown/text from LLM)
            json_match = re.search(r"\[.*\]", response_text, re.DOTALL)
            json_str = json_match.group(0) if json_match else response_text

            parsed = json.loads(json_str)
            if not isinstance(parsed, list):
                parsed = [parsed]

            patterns = []
            for item in parsed[:num_dorks]:
                if isinstance(item, dict) and "signature" in item:
                    item["engine"] = item.get("engine", "google")
                    if item["engine"] not in {
                        "google",
                        "bing",
                        "duckduckgo",
                        "baidu",
                        "yandex",
                    }:
                        item["engine"] = "google"
                    patterns.append(DorkPattern.model_validate(item))
            if patterns:
                return patterns
            raise ValueError("No valid patterns parsed")
    except Exception as exc:  # pylint: disable=broad-except
        print(
            f"Local LLM dork generation for '{tech_name}' failed: {exc}\n"
            f"Make sure Ollama is running (`ollama serve`) and the model is pulled (`ollama pull llama3.2`)."
        )
        return _generate_fallback_dorks(tech_name, num_dorks)


def _generate_fallback_dorks(tech_name: str, num_dorks: int = 4) -> List[DorkPattern]:
    """Generate generic fallback dorks when LLM is unavailable.

    Returns up to num_dorks varied patterns (fixes previous tests that expected 2 or 4).
    """
    base = tech_name.lower().replace(" ", "")
    templates = [
        f'"powered by {base}" OR "{base}" (checkout OR store OR dashboard) -site:github.com -site:*.com/{base}',
        f'inurl:{base} intitle:(admin OR dashboard OR store) -inurl:(login signup)',
        f'"{base} checkout" OR "{base} payment" OR "powered by {base}"',
        f'(site:{base}.com OR inurl:{base}) (shop OR store) -site:github.com',
    ]
    engines = ["google", "bing", "duckduckgo", "yandex"]
    patterns = []
    for i in range(min(num_dorks, len(templates))):
        patterns.append(
            DorkPattern(
                signature=templates[i],
                engine=engines[i % len(engines)],
            )
        )
    if not patterns:
        patterns.append(
            DorkPattern(
                signature=f'"{base}" (checkout OR store)',
                engine="google",
            )
        )
    return patterns


__all__ = [
    "load_dorks",
    "get_dorks",
    "build_query",
    "list_all_techs",
    "generate_dorks_for_tech",
    "get_tech_to_category",
]
