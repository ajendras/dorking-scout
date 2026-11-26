"""FastAPI router providing JSON and export endpoints for Dork Scout.

Endpoints:
- GET /api/categories — returns curated category metadata
- POST /api/search — runs multi-engine dork searches, deduplicates domains,
  performs lightweight tech detection, persists to DiscoveryStore and
  returns DiscoveryResult objects
- GET /api/export — generates CSV or JSON downloads with metadata
- GET /api/history — returns recent searches and total unique domains
  from the persistent SQLite store
- GET /api/domains — lists persisted unique domains (optionally filtered)
- POST /api/generate_dorks — uses the local LLM helper to suggest new
  dork patterns for unknown technologies (requires Ollama locally)
- GET /api/dorks — returns dork patterns for a technology (used by the
  companion browser extension)
- GET /api/analytics — returns aggregated tech distribution used by the
  new analytics dashboard (pie/bar charts + table). Now supports
  optional date_from, date_to and tech query params for live filtering.

All endpoints validate input, handle errors gracefully and respect rate limits.
Search is intentionally limited in scope to keep responses under 4 seconds.
"""

import csv
import httpx
import json
from datetime import datetime, timezone
from io import StringIO
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, HTTPException, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse

from .config import get_search_engines
from .detector import detect_technologies, enrich_discovery_results
from .dorks import build_query, generate_dorks_for_tech, get_dorks, list_all_techs, load_dorks
from .models import Category, DiscoveryResult, AnalyticsSummary
from .search import SearchClient
from .store import DiscoveryStore

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
    detection on the first handful of results only. Results are persisted
    to the DiscoveryStore before returning.

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

        # Enrich top results with domain age (Wayback CDX) and SSL cert metadata (crt.sh)
        # This is best-effort and limited to keep the endpoint fast
        await enrich_discovery_results(results)

        # Persist to SQLite store (non-fatal if it fails)
        try:
            store: DiscoveryStore = request.app.state.store
            primary_query = queries_used[0] if queries_used else ""
            store.record_search(
                query=primary_query,
                results=results,
                category=category,
                tech=tech,
                term=term,
                engine="multi",
            )
        except Exception as store_exc:  # pylint: disable=broad-except
            print(f"Warning: could not persist search to store: {store_exc}")

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

        # Enrich a few results for richer export (free sources)
        await enrich_discovery_results(results[:5])

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
        fieldnames = [
            "domain",
            "tech",
            "url",
            "detected_at",
            "title",
            "domain_age_days",
            "creation_date",
            "ssl_issuer",
            "ssl_valid_from",
            "ssl_valid_to",
            "relevance_score",
        ]
        writer = csv.DictWriter(output, fieldnames=fieldnames, extrasaction="ignore")
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


@router.get("/history")
def get_history(
    request: Request, limit: int = Query(20, ge=1, le=100)
) -> Dict[str, Any]:
    """Return recent search history from the persistent SQLite store.

    Includes query metadata, result counts and a total count of unique
    domains discovered so far.
    """
    store: Optional[DiscoveryStore] = getattr(request.app.state, "store", None)
    if not store:
        raise HTTPException(status_code=500, detail="Store not initialized")

    history = store.get_search_history(limit)
    total_domains = store.get_total_unique_domains()

    return {
        "history": history,
        "total_searches": len(history),
        "total_unique_domains": total_domains,
    }


@router.get("/domains")
def list_domains(
    request: Request,
    limit: int = Query(50, ge=1, le=500),
    tech: Optional[str] = Query(None, description="Filter by technology"),
) -> Dict[str, Any]:
    """Return persisted unique domains, optionally filtered by tech."""
    store: Optional[DiscoveryStore] = getattr(request.app.state, "store", None)
    if not store:
        raise HTTPException(status_code=500, detail="Store not initialized")

    domains = store.get_unique_domains(limit=limit, tech=tech)
    return {"domains": domains, "count": len(domains)}


@router.get("/techs")
def list_technologies():
    """Return every technology that has associated dorks.

    Useful for autocomplete dropdowns in the niche explorer.
    """
    return {"techs": list_all_techs()}


@router.get("/dorks")
def get_tech_dorks(
    tech: Optional[str] = Query(None, description="Technology name")
) -> Dict[str, Any]:
    """Return dork patterns for a given technology.

    Used by the companion browser extension to retrieve the exact
    curated signatures when the local server is running.
    """
    if not tech:
        return {"techs": list_all_techs()}

    try:
        patterns = get_dorks(tech=tech)
        return {
            "tech": tech,
            "patterns": [p.model_dump(mode="json") for p in patterns],
            "count": len(patterns),
        }
    except ValueError:
        return {"tech": tech, "patterns": [], "count": 0}


@router.post("/generate_dorks")
async def generate_dorks(
    payload: Dict[str, Any] = Body(...)
) -> Dict[str, Any]:
    """Generate fresh dork patterns for an unknown technology using the
    local LLM helper (Ollama).

    The returned patterns are validated Pydantic models and can be copied
    directly into a dorks.json file. Requires Ollama to be running locally.
    """
    tech: str = (payload.get("tech") or "").strip()
    if not tech:
        raise HTTPException(
            status_code=400, detail="tech name is required"
        )

    category: Optional[str] = payload.get("category")
    num_dorks: int = int(payload.get("num_dorks", 4))

    try:
        patterns = await generate_dorks_for_tech(
            tech_name=tech,
            category=category,
            num_dorks=num_dorks,
        )
        return {
            "tech": tech,
            "category": category,
            "dorks": [p.model_dump(mode="json") for p in patterns],
            "count": len(patterns),
            "message": (
                "Copy these patterns into dorks.json under the tech key "
                "and add a tech_to_category mapping."
            ),
        }
    except Exception as exc:
        raise HTTPException(
            status_code=500, detail=f"LLM generation failed: {str(exc)}"
        ) from exc


@router.get("/analytics", response_model=AnalyticsSummary)
def get_analytics(
    request: Request,
    date_from: Optional[str] = Query(
        None,
        description="Start date (YYYY-MM-DD)",
        pattern=r"^\d{4}-\d{2}-\d{2}$",
    ),
    date_to: Optional[str] = Query(
        None,
        description="End date (YYYY-MM-DD)",
        pattern=r"^\d{4}-\d{2}-\d{2}$",
    ),
    tech: Optional[str] = Query(
        None, description="Comma-separated techs to filter by"
    ),
) -> Dict[str, Any]:
    """Return aggregated analytics for the dashboard, optionally filtered.

    date_from/date_to limit results by first_seen timestamp.
    tech limits both total and distribution to matching technologies.
    Used by the analytics UI for live Chart.js updates on filter changes.
    """
    store: Optional[DiscoveryStore] = getattr(request.app.state, "store", None)
    if not store:
        raise HTTPException(status_code=500, detail="Store not initialized")

    tech_filter = [t.strip() for t in tech.split(",")] if tech else None

    distribution = store.get_tech_distribution(
        date_from=date_from, date_to=date_to, tech_filter=tech_filter
    )
    total = store.get_total_unique_domains(
        date_from=date_from, date_to=date_to, tech_filter=tech_filter
    )

    # Top 10 for the bar chart
    top_techs = sorted(
        [{"tech": k, "count": v} for k, v in distribution.items()],
        key=lambda x: x["count"],
        reverse=True,
    )[:10]

    return {
        "total_domains": total,
        "tech_distribution": distribution,
        "top_techs": top_techs,
        "last_updated": datetime.now(timezone.utc).isoformat(),
        "applied_filters": {
            "date_from": date_from,
            "date_to": date_to,
            "techs": tech_filter,
        },
    }


__all__ = ["router"]
