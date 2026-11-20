import asyncio
import csv
from datetime import datetime, timezone
from typing import List

from dorkscout.dorks import build_query, get_dorks
from dorkscout.detector import detect_technologies, enrich_discovery_results
from dorkscout.models import DiscoveryResult
from dorkscout.search import SearchClient


async def main() -> None:
    """Niche explorer for online bookstores.

    Uses the 'Books' dork set, adds a custom term, deduplicates results,
    runs lightweight detection, enriches with domain age and SSL metadata
    from free sources, then exports a clean CSV.
    """
    patterns = get_dorks(category="niches", tech="Books")
    client = SearchClient(timeout=10.0)

    try:
        query = build_query(patterns[0], "rare first editions")
        print("Query:", query)

        domains = await client.search_with_rate_limit(query, patterns[0].engine)
        # Simple deduplication
        seen = set()
        unique_domains = [d for d in domains if not (d in seen or seen.add(d))][:20]

        results: List[DiscoveryResult] = []
        for domain in unique_domains:
            html = ""  # would fetch real page in production scanner
            detected = detect_technologies(html)
            if not detected:
                detected = ["Books"]

            result = DiscoveryResult(
                domain=domain,
                tech=detected,
                url=f"https://{domain}",
                detected_at=datetime.now(timezone.utc).isoformat(),
                title="Independent Bookstore",
            )
            results.append(result)

        # Enrich each result with free metadata sources
        await enrich_discovery_results(results)

        # Export
        timestamp = datetime.now().strftime("%Y%m%d_%H%M")
        filename = f"niche_books_{timestamp}.csv"
        with open(filename, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(
                [
                    "domain",
                    "tech",
                    "url",
                    "domain_age_days",
                    "creation_date",
                    "ssl_issuer",
                ]
            )
            for r in results:
                writer.writerow(
                    [
                        r.domain,
                        ";".join(r.tech),
                        r.url,
                        r.domain_age_days,
                        r.creation_date,
                        r.ssl_issuer,
                    ]
                )

        print(f"Exported {len(results)} enriched bookstore domains to {filename}")
        for r in results[:4]:
            print(
                f"{r.domain:30} age={r.domain_age_days}days  issuer={r.ssl_issuer}"
            )

    except Exception as exc:  # pylint: disable=broad-except
        print("Error:", exc)
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
