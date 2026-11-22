"""FastAPI application factory and CLI entrypoint for Dork Scout.

Creates the app, mounts the web and API routers, initialises the
DiscoveryStore and starts the AlertScheduler on startup. The
console script 'dork-scout' points here.
"""

import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .api import router as api_router
from .store import DiscoveryStore
from .scheduler import AlertScheduler
from .web import router as web_router


def create_app() -> FastAPI:
    """Build the FastAPI application with all routes and state.

    The store and scheduler are attached to app.state so they are
    accessible from request handlers and background tasks.
    """
    app = FastAPI(
        title="Dork Scout",
        description="Discover unique websites by tech stack, payment gateway or product niche",
        version="0.2.0",
    )

    # Mount static assets (for any future CSS/JS extensions)
    app.mount("/static", StaticFiles(directory="static"), name="static")

    app.include_router(web_router)
    app.include_router(api_router)

    @app.on_event("startup")
    async def on_startup():
        """Initialise persistent store and background scheduler."""
        app.state.store = DiscoveryStore()
        app.state.scheduler = AlertScheduler(store=app.state.store)
        app.state.scheduler.start()

    @app.on_event("shutdown")
    async def on_shutdown():
        """Clean up resources on shutdown."""
        if hasattr(app.state, "store"):
            app.state.store.close()
        if hasattr(app.state, "scheduler"):
            app.state.scheduler.stop()

    return app


def run():
    """Entry point for the 'dork-scout' console script."""
    app = create_app()
    uvicorn.run(
        app,
        host="127.0.0.1",
        port=8000,
        log_level="info",
    )


if __name__ == "__main__":
    run()
