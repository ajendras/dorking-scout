"""CLI entrypoint and FastAPI app factory for Dork Scout.

Supports `dork-scout serve` (default) and the new `dork-scout bulk` subcommand.
The bulk command now correctly calls the async bulk_process() using asyncio.run().
create_app() wires the DiscoveryStore, AlertScheduler and all routers.
"""

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Optional

import uvicorn
from fastapi import FastAPI

from .api import router as api_router
from .config import get_db_path
from .scheduler import AlertScheduler
from .search import bulk_process
from .store import DiscoveryStore
from .web import router as web_router


def create_app() -> FastAPI:
    """FastAPI application factory.

    Attaches routers, initialises persistent SQLite store and starts
    the background AlertScheduler.
    """
    app = FastAPI(
        title="Dork Scout",
        description="Discover unique websites by tech, gateway or niche",
        version="0.1.0",
    )

    app.include_router(api_router)
    app.include_router(web_router)

    # Persistent store
    store = DiscoveryStore(db_path=get_db_path())
    app.state.store = store

    # Background scheduler for alerts
    scheduler = AlertScheduler(store)
    app.state.scheduler = scheduler
    scheduler.start()

    @app.on_event("shutdown")
    def shutdown_scheduler():
        scheduler.shutdown()

    return app


def run() -> None:
    """Console entry point supporting both web server and bulk CLI.

    bulk subcommand now uses asyncio.run(bulk_process(...)) because
    bulk_process was made async to fix pytest-asyncio compatibility.
    """
    parser = argparse.ArgumentParser(prog="dork-scout")
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # serve / web server
    serve_p = subparsers.add_parser(
        "serve", help="Start the web UI and API server (default)"
    )
    serve_p.add_argument("--host", default="127.0.0.1", help="Bind address")
    serve_p.add_argument("--port", type=int, default=8000, help="Port")

    # bulk
    bulk_p = subparsers.add_parser(
        "bulk", help="Run headless bulk search and export to CSV"
    )
    bulk_p.add_argument("--tech", help="Technology name (e.g. Shopify)")
    bulk_p.add_argument("--category", help="Category (e.g. niches)")
    bulk_p.add_argument(
        "--term", action="append", dest="terms", help="Search term(s) - repeatable"
    )
    bulk_p.add_argument(
        "--terms-file", type=Path, help="File with one term per line"
    )
    bulk_p.add_argument(
        "--output",
        "-o",
        type=Path,
        default=Path("dorkscout_bulk.csv"),
        help="Output CSV path",
    )
    bulk_p.add_argument("--limit", type=int, default=20, help="Max results")

    args = parser.parse_args()
    if not args.command:
        args.command = "serve"

    if args.command == "serve":
        app = create_app()
        uvicorn.run(
            app,
            host=args.host,
            port=args.port,
            log_level="info",
        )
    elif args.command == "bulk":
        if not args.tech and not args.category:
            print("Error: --tech or --category is required for bulk", file=sys.stderr)
            sys.exit(1)

        results = asyncio.run(
            bulk_process(
                tech=args.tech,
                category=args.category,
                terms=args.terms or [],
                terms_file=args.terms_file,
                output=args.output,
                limit=args.limit,
            )
        )
        print(
            f"Bulk processing finished. {len(results)} unique domains written to {args.output}"
        )
    else:
        parser.print_help()


if __name__ == "__main__":
    run()
