"""Web routes for the warm organic Dork Scout UI.

Serves the single-page index.html (which contains category blob cards,
HTMX-powered search controls and a live results table). Also provides
dedicated category views that pre-filter the displayed content. All
templates receive rich context for the sand/olive/terracotta theme.
"""

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query, Request

from .dorks import (
    get_category_info,
    get_dorks,
    get_tech_to_category,
    list_all_techs,
    load_dorks,
)

router = APIRouter()


def _category_context() -> Dict[str, Any]:
    """Shared helper that returns category metadata for templates."""
    db = load_dorks()
    return {
        "categories": list(db.get("categories", {}).values()),
        "all_techs": list_all_techs(),
    }


@router.get("/")
def index(request: Request):
    """Main landing page.

    Renders the full earthy UI with blob-shaped category cards, search
    bar (category/tech/term), and an empty results pane that HTMX will
    populate by calling /api/search.
    """
    templates = request.app.state.templates
    context = {
        "request": request,
        "page_title": "Dork Scout — Discover Unique Websites",
        "hero_title": "Find real stores by payment gateway or niche",
        "hero_subtitle": (
            "Curated search dorks • Multi-engine • "
            "Lightweight tech detection"
        ),
        **_category_context(),
        "active_category": None,
    }
    return templates.TemplateResponse("index.html", context)


@router.get("/category/{category_id}")
def category_page(request: Request, category_id: str):
    """Dedicated view for a single category (payment_gateways, platforms, niches).

    Pre-loads example dorks and filters the tech list to only those that
    belong to the selected category. The same index.html template is used
    with different context so HTMX behaviours remain consistent.
    """
    templates = request.app.state.templates

    try:
        category_meta = get_category_info(category_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    # Filter techs that belong to this category
    tech_to_cat = get_tech_to_category()
    relevant_techs = [
        tech
        for tech, cat in tech_to_cat.items()
        if cat == category_id
    ]

    example_dorks = get_dorks(category=category_id)

    context = {
        "request": request,
        "page_title": f"{category_meta['name']} • Dork Scout",
        "hero_title": category_meta["name"],
        "hero_subtitle": category_meta["description"],
        "active_category": category_id,
        "category": category_meta,
        "relevant_techs": relevant_techs,
        "example_dorks": [str(d) for d in example_dorks[:5]],
        **_category_context(),
    }
    return templates.TemplateResponse("index.html", context)


@router.get("/niche-explorer")
def niche_explorer(request: Request, q: Optional[str] = Query(None)):
    """Lightweight niche explorer page.

    Allows free-form product vertical searches (books, shoes, handmade gifts…).
    Same template, different heading and pre-filled search term when q is given.
    """
    templates = request.app.state.templates
    context = {
        "request": request,
        "page_title": "Niche Explorer • Dork Scout",
        "hero_title": "Product Niche Explorer",
        "hero_subtitle": (
            "Find stores selling specific products using "
            "curated vertical dorks"
        ),
        "active_category": "niches",
        "search_term": q or "",
        **_category_context(),
    }
    return templates.TemplateResponse("index.html", context)


__all__ = ["router"]
