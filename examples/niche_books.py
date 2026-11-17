"""Example script demonstrating product category dorks for bookstores.

Uses the curated "Books" niche dork combined with additional keywords to
surface independent and specialty bookstores. Performs lightweight tech
detection (payment gateways, platforms), deduplicates results, and exports
up to 20 unique domains to a timestamped CSV. Runnable as-is:

    python examples/niche_books.py
"""

import asyncio
import csv
from datetime import datetime
from pathlib import Path
from typing import List

from dorkscout.detector import detect_technologies
from dorkscout.dorks import build_query, get_dorks
from dorkscout.models import DiscoveryResult
from dorkscout.search import SearchClient


async def main() -> None:
    """Discover independent bookstores and export results."""
    print("📚 Dork Scout — Bookstore Niche Explorer")
    print("=" * 62)
    print("Using curated product-niche dorks to find real bookstores.\n")

    # Use the dedicated Books niche dork and further narrow with a term
    dork_patterns = get_dorks(tech="Books")
    term = (
        '("independent bookstore" OR "used books" OR "rare books" OR '
        'bookshop OR "local bookstore") (checkout OR "add to cart" OR cart)'
    )

    client = SearchClient(timeout=12.0)
    discovered: List[DiscoveryResult] = []
    seen: set[str] = set()

    try:
        for dork in dork_patterns:
            query = build_query(dork, term)
            print(f"Query → {query[:68]}...")

            domains = await client.search_with_rate_limit(
                query, dork.engine, delay_override=1.2
            )

            for domain in domains:
                if domain in seen:
                    continue
                seen.add(domain)

                html = ""
                techs: List[str] = []
                try:
                    resp = await client.client.get(
                        f"https://{domain}",
                        timeout=6.0,
                        follow_redirects=True,
                    )
                    html = resp.text
                    techs = detect_technologies(html, dict(resp.headers))
                except Exception:  # pylint: disable=broad-except
                    pass

                # Niches don't always have tech signatures; add context
                if not techs:
                    techs = ["Bookstore"]
                elif "Books" not in techs and "Bookstore" not in techs:
                    techs.insert(0, "Bookstore")

                result = DiscoveryResult(
                    domain=domain,
                    tech=techs,
                    url=f"https://{domain}",
                    detected_at=datetime.now().isoformat(),
                    title="Independent Bookstore",
                )
                discovered.append(result)
                print(f"  ✓ {domain} → {', '.join(techs)}")

                if len(discovered) >= 20:
                    break

            if len(discovered) >= 20:
                break

        # Export to CSV
        timestamp = datetime.now().strftime("%Y%m%d_%H%M")
        csv_path = Path(f"bookstores_niche_{timestamp}.csv")

        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=["domain", "tech", "url", "detected_at", "title"],
            )
            writer.writeheader()
            for result in discovered:
                row = result.model_dump()
                row["tech"] = ";".join(row.get("tech", []))
                writer.writerow(row)

        print("\n✅ Discovery complete!")
        print(f"Found and deduplicated {len(discovered)} unique domains.")
        print(f"Results saved to ./{csv_path.name}")
        print("\nTop results:")
        for item in discovered[:6]:
            print(f"  • {item.domain} ({';'.join(item.tech)})")

    except Exception as exc:  # pylint: disable=broad-except
        print(f"Unexpected error: {exc}")
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
