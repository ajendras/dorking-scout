"""Main entrypoint for the Dork Scout FastAPI application.

Creates the app, configures static files, templates and registers the API and web routers.
Also initialises the persistent DiscoveryStore and the new AlertScheduler on app.state.
"""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .api import router as api_router
from .config import DORK_DB
from .scheduler import AlertScheduler
from .store import DiscoveryStore
from .web import router as web_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Handle startup/shutdown: initialise store + scheduler, start background jobs."""
    static_dir = Path("static")
    static_dir.mkdir(exist_ok=True)
    templates_dir = Path("templates")
    templates_dir.mkdir(exist_ok=True)

    store = DiscoveryStore()
    scheduler = AlertScheduler(store)

    app.state.store = store
    app.state.scheduler = scheduler
    app.state.templates = Jinja2Templates(directory="templates")

    scheduler.start()
    yield
    scheduler.shutdown()
    store.close()


def create_app() -> FastAPI:
    """Factory that creates and configures the FastAPI application.

    Mounts static assets (for the warm organic UI), registers routers for the
    category browser, live search, export, history, dork generation and the
    new /api/schedules endpoints. The lifespan context manager starts the
    AlertScheduler (APScheduler) that powers scheduled background jobs.
    """
    app = FastAPI(
        title="Dork Scout",
        description="Discover unique websites by tech stack, payment gateway or product niche using curated search dorks",
        version="0.1.0",
        docs_url="/api/docs",
        redoc_url=None,
        lifespan=lifespan,
    )

    app.mount("/static", StaticFiles(directory="static"), name="static")
    app.include_router(api_router)
    app.include_router(web_router)

    return app


def run():
    """Entry point used by the 'dork-scout' console script."""
    import uvicorn

    uvicorn.run(
        "dorkscout.main:create_app",
        factory=True,
        host="127.0.0.1",
        port=8000,
        log_level="info",
    )


if __name__ == "__main__":
    run()
