"""Main entrypoint for Dork Scout.

Provides FastAPI app factory, uvicorn runner for the web UI/API, and
new CLI subcommand `bulk` for headless batch processing of term lists with
direct CSV output (including metadata enrichment).
"""

import argparse
import asyncio
import sys
from typing import List

import uvicorn
from fastapi import FastAPI

from .api import router as api_router
from .config import get_db_path
from .scheduler import AlertScheduler
from .search import bulk_process
from .store import DiscoveryStore
from .web import router as web_router


def create_app() -> FastAPI:
    """Create and configure the FastAPI application.

    Registers routers, initialises the persistent store and starts
    the background AlertScheduler on startup.
    """
    app = FastAPI(
        title="Dork Scout",
        description="Discover unique websites by tech stack, payment gateway or product niche using curated search dorks",
        version="0.2.0",
    )

    app.include_router(api_router)
    app.include_router(web_router)

    # Initialise SQLite store
    db_path = get_db_path()
    store = DiscoveryStore(db_path=db_path)
    app.state.store = store

    # Start scheduler for background alerts
    scheduler = AlertScheduler(store=store)
    app.state.scheduler = scheduler

    @app.on_event("startup")
    async def on_startup():
        scheduler.start()
        print(f"✅ Dork Scout started. DB at {db_path}. Scheduler running.")

    @app.on_event("shutdown")
    async def on_shutdown():
        scheduler.shutdown()

    return app


def _run_bulk(args: argparse.Namespace) -> None:
    """Execute the bulk subcommand."""
    terms: List[str] = args.term or []
    if args.terms_file:
        try:
            with open(args.terms_file, "r", encoding="utf-8") as f:
                file_terms = [line.strip() for line in f.readlines() if line.strip() and not line.startswith("#")]
                terms.extend(file_terms)
        except Exception as e:  # pylint: disable=broad-except
            print(f"Error reading terms file: {e}")
            sys.exit(1)

    if not terms:
        terms = ["online store"]  # sensible default

    output_path = args.output
    print(
        f"🚀 Starting bulk processing for tech='{args.tech}' category='{args.category}' "
        f"with {len(terms)} term(s) -> {output_path}"
    )

    asyncio.run(
        bulk_process(
            category=args.category,
            tech=args.tech,
            terms=terms,
            output=output_path,
            limit=args.limit,
        )
    )


def run() -> None:
    """CLI entry point supporting both server and bulk subcommand."""
    parser = argparse.ArgumentParser(
        prog="dork-scout",
        description="Dork Scout - Curated dork search tool with web UI and CLI",
    )
    subparsers = parser.add_subparsers(
        dest="command",
        help="Command to run (default: start web server)",
    )

    # Serve / web server command (default)
    serve_parser = subparsers.add_parser(
        "serve", help="Start the web server and API (default when no subcommand given)"
    )
    serve_parser.add_argument(
        "--host", default="127.0.0.1", help="Host to bind to"
    )
    serve_parser.add_argument(
        "--port", type=int, default=8000, help="Port to listen on"
    )

    # Bulk command
    bulk_parser = subparsers.add_parser(
        "bulk",
        help="Bulk process multiple terms for a tech/category and output headless CSV",
    )
    bulk_parser.add_argument(
        "--tech",
        help="Technology to target (e.g. Stripe, Shopify)",
    )
    bulk_parser.add_argument(
        "--category",
        help="Category to use (payment_gateways, platforms, niches, documents)",
    )
    bulk_parser.add_argument(
        "--term",
        action="append",
        help="Additional search term. Can be specified multiple times.",
    )
    bulk_parser.add_argument(
        "--terms-file",
        type=str,
        help="Path to a text file with one term per line (comments with # supported)",
    )
    bulk_parser.add_argument(
        "-o",
        "--output",
        default="dorkscout_results.csv",
        help="Path for output CSV file",
    )
    bulk_parser.add_argument(
        "--limit",
        type=int,
        default=80,
        help="Maximum number of unique domains to return",
    )

    args = parser.parse_args()

    if args.command == "bulk":
        _run_bulk(args)
    else:
        # default to serve (even if command is serve or None)
        serve_args = args if args.command == "serve" else None
        host = serve_args.host if serve_args else "127.0.0.1"
        port = serve_args.port if serve_args else 8000
        print(f"🌐 Starting Dork Scout web server at http://{host}:{port}")
        uvicorn.run(
            "dorkscout.main:create_app",
            factory=True,
            host=host,
            port=port,
            log_level="info",
        )


if __name__ == "__main__":
    run()
