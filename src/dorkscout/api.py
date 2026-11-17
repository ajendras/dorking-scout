"""FastAPI router providing JSON and export endpoints for Dork Scout.

Endpoints:
- GET /api/categories — returns curated category metadata
- POST /api/search — runs multi-engine dork searches, deduplicates domains,
  performs lightweight tech detection and returns DiscoveryResult objects
- GET /api/export — generates CSV or JSON downloads with metadata

All endpoints validate input, handle errors gracefully and respect rate limits.
Search is intentionally limited in scope to keep responses under 4 seconds.
"""

import csv
import httpx
from datetime import datetime, timezone
from io import StringIO
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, HTTPException, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse

from .config import get_search_engines
from .detector import detect_technologies
from .dorks import build_query, get_dorks, list_all_techs, load_dorks
from .models import Category, DiscoveryResult
from .search import SearchClient

router = APIRouter(tags=["api"], prefix="/api")


@router.get("/categories", response_model=List[Category])
def list_categories():
    """Return all available categories with metadata for the UI.

    Categories are taken from the merged dork database (embedded defaults
    + optional dorks.json).
    """
    db = load_dorks()
    cats = db.get("categories", {})
    return [Category.model_validate(data) for data in cats.values()]


@router.post("/search")
async def perform_search(
    request: Request, payload: Dict[str, Any] = Body(...)
) -> Dict[str, Any]:
    """Execute curated dork searches and return discovered domains with tech.

    Accepts either a broad category or a specific tech (not both). Uses
    the SearchClient with rate-limit aware calls. Performs HTML-based
    detection on the first handful of results only.

    Body example:
    {
        "category": "niches",
        "tech": null,
        "term": "vintage leather"
    }
    """
    category: Optional[str] = payload.get("category")
    tech: Optional[str] = payload.get("tech")
    term: str = (payload.get("term") or "").strip()

    if category is None and tech is None:
        raise HTTPException(
            status_code=400,
            detail="Either 'category' or 'tech' must be provided.",
        )

    if category and tech:
        raise HTTPException(
            status_code=400,
            detail="Provide either category or tech, not both.",
        )

    try:
        dork_patterns = get_dorks(category=category, tech=tech)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if not dork_patterns:
        return {
            "results": [],
            "total": 0,
            "message": "No dork patterns found for the selection.",
        }

    client = SearchClient(timeout=10.0)
    try:
        all_domains: List[str] = []
        queries_used: List[str] = []

        # Limit dorks to keep the request responsive
        for dork in dork_patterns[:3]:
            query = build_query(dork, term)
            queries_used.append(str(dork))

            domains = await client.search_with_rate_limit(
                query, dork.engine, delay_override=None
            )
            all_domains.extend(domains)

        # Deduplicate while preserving first-seen order
        seen: set[str] = set()
        unique_domains: List[str] = [
            d for d in all_domains if not (d in seen or seen.add(d))
        ]
        unique_domains = unique_domains[:25]

        # Fetch HTML for detection (limited to first 6 domains)
        html_map: Dict[str, str] = {}
        for domain in unique_domains[:6]:
            try:
                resp = await client.client.get(
                    f"https://{domain}",
                    timeout=httpx.Timeout(5.0),
                    follow_redirects=True,
                )
                html_map[domain] = resp.text
            except Exception:  # pylint: disable=broad-except
                html_map[domain] = ""

        # Build rich discovery objects
        results: List[DiscoveryResult] = []
        for domain in unique_domains:
            html = html_map.get(domain, "")
            detected = detect_technologies(html)

            # If user filtered by a specific tech, guarantee it appears
            if tech and tech not in [t.lower() for t in detected]:
                detected = [tech] + detected

            result = DiscoveryResult(
                domain=domain,
                tech=detected,
                url=f"https://{domain}",
                detected_at=datetime.now(timezone.utc).isoformat(),
            )
            results.append(result)

        return {
            "results": [r.model_dump(mode="json") for r in results],
            "total": len(results),
            "queries_used": queries_used,
            "term": term,
            "category": category,
            "tech": tech,
        }

    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502, detail="Search engine unavailable"
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=500, detail="Unexpected error during search"
        ) from exc
    finally:
        await client.close()


@router.get("/export")
async def export_results(
    category: Optional[str] = Query(None, description="Category to export"),
    tech: Optional[str] = Query(None, description="Specific technology"),
    term: str = Query("", description="Additional search term"),
    fmt: str = Query("csv", alias="format", pattern="^(csv|json)$"),
):
    """Export discovered domains with metadata as CSV or JSON.

    Runs a similar (but lighter) search process as /search. CSV includes
    a semicolon-separated tech column for easy spreadsheet use.
    """
    if not category and not tech:
        raise HTTPException(
            status_code=400,
            detail="Either category or tech parameter is required.",
        )

    client = SearchClient(timeout=8.0)
    try:
        dork_patterns = get_dorks(category=category, tech=tech)

        all_domains: List[str] = []
        for dork in dork_patterns[:4]:
            query = build_query(dork, term)
            domains = await client.search_with_rate_limit(query, dork.engine)
            all_domains.extend(domains)

        seen: set[str] = set()
        unique_domains = [
            d for d in all_domains if not (d in seen or seen.add(d))
        ][:30]

        # For export we use minimal detection (no extra HTTP round-trips)
        results: List[DiscoveryResult] = []
        for domain in unique_domains:
            tech_list = [tech] if tech else []
            result = DiscoveryResult(
                domain=domain,
                tech=tech_list,
                url=f"https://{domain}",
                detected_at=datetime.now(timezone.utc).isoformat(),
            )
            results.append(result)

        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M")

        if fmt == "json":
            payload = {
                "exported_at": datetime.now(timezone.utc).isoformat(),
                "category": category,
                "tech": tech,
                "term": term,
                "count": len(results),
                "results": [r.model_dump(mode="json") for r in results],
            }
            return JSONResponse(
                content=payload,
                headers={
                    "Content-Disposition": f'attachment; filename="dorkscout_{timestamp}.json"'
                },
            )

        # CSV export
        output = StringIO()
        fieldnames = ["domain", "tech", "url", "detected_at", "title"]
        writer = csv.DictWriter(output, fieldnames=fieldnames)
        writer.writeheader()

        for r in results:
            row = r.model_dump()
            row["tech"] = ";".join(row.get("tech", []))
            row["title"] = row.get("title", "")
            writer.writerow(row)

        csv_data = output.getvalue()

        return StreamingResponse(
            iter([csv_data]),
            media_type="text/csv",
            headers={
                "Content-Disposition": (
                    f'attachment; filename="dorkscout_{timestamp}.csv"'
                )
            },
        )

    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=500, detail="Export failed"
        ) from exc
    finally:
        await client.close()


@router.get("/techs")
def list_technologies():
    """Return every technology that has associated dorks.

    Useful for autocomplete dropdowns in the niche explorer.
    """
    return {"techs": list_all_techs()}


__all__ = ["router"]
