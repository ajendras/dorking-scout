"""Main entrypoint for the Dork Scout FastAPI application.

Creates the app, configures static files, templates and registers the API and web routers.
Also initialises the persistent DiscoveryStore on app.state.
"""

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .config import DORK_DB


def create_app() -> FastAPI:
    """Factory that creates and configures the FastAPI application.

    Mounts static assets (for the warm organic UI), registers routers for the
    category browser, live search, export and history features, attaches
    shared state (templates, dork database and the persistent DiscoveryStore).

    Returns:
        FastAPI: Fully configured application instance.
    """
    app = FastAPI(
        title="Dork Scout",
        description="Discover unique websites by tech stack, payment gateway or product niche using curated search dorks",
        version="0.1.0",
        docs_url="/api/docs",
        redoc_url=None,
    )

    # Create required directories so the app starts cleanly even on first run
    static_dir = Path("static")
    static_dir.mkdir(exist_ok=True)
    templates_dir = Path("templates")
    templates_dir.mkdir(exist_ok=True)

    # Mount static directory for CSS/JS supporting the sand/olive/terracotta theme,
    # organic blob shapes, soft paper texture and accessible focus states.
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

    templates = Jinja2Templates(directory=str(templates_dir))
    app.state.templates = templates
    app.state.dork_db = DORK_DB

    from .store import DiscoveryStore
    app.state.store = DiscoveryStore()

    # Register UI and API routers (defined in web.py and api.py)
    from .web import router as web_router
    from .api import router as api_router

    app.include_router(web_router)
    app.include_router(api_router, prefix="/api")

    return app


def run() -> None:
    """Console script entry point (`dork-scout`).

    Launches the server with reload enabled for development.
    """
    import uvicorn

    uvicorn.run(
        "dorkscout.main:create_app",
        factory=True,
        host="127.0.0.1",
        port=8000,
        reload=True,
        log_level="info",
    )


if __name__ == "__main__":
    run()
