"""FastAPI routes serving the HTMX-driven UI.

Renders the single index.html template for both the home/category
browser and the new analytics dashboard. The template inspects the
'view' context variable to decide which panel to show initially.
"""

from fastapi import APIRouter, Request
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse

templates = Jinja2Templates(directory="templates")

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
def index(request: Request):
    """Home page – category browser with blob cards."""
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "view": "home",
            "title": "Dork Scout — Discover by Tech",
        },
    )


@router.get("/analytics", response_class=HTMLResponse)
def analytics(request: Request):
    """Dedicated analytics page showing tech distribution graphs.

    The same template is used; the JS inside switches to the analytics
    panel and immediately fetches /api/analytics.
    """
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "view": "analytics",
            "title": "Dork Scout — Analytics",
        },
    )


@router.get("/history", response_class=HTMLResponse)
def history_page(request: Request):
    """Simple history view (HTMX can also load this via partials)."""
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "view": "history",
            "title": "Dork Scout — History",
        },
    )
