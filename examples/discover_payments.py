import asyncio
import csv
from datetime import datetime, timezone
from typing import List

from dorkscout.dorks import build_query, get_dorks
from dorkscout.detector import detect_technologies, enrich_discovery_results
from dorkscout.models import DiscoveryResult
from dorkscout.search import SearchClient


async def main() -> None:
    """Discover stores using Stripe with a gift-card focus.

    Demonstrates full pipeline including the new domain-age and SSL-cert
    enrichment from free public sources (Wayback Machine + crt.sh).
    """
    patterns = get_dorks(tech="Stripe")
    client = SearchClient(timeout=10.0)

    try:
        query = build_query(patterns[0], "gift card")
        print("Query:", query)

        domains = await client.search_with_rate_limit(query, patterns[0].engine)
        unique_domains = list(dict.fromkeys(domains))[:15]

        results: List[DiscoveryResult] = []
        for domain in unique_domains:
            # In a full scanner we would fetch real HTML; here we simulate
            html = "<title>Checkout powered by Stripe</title>"
            detected = detect_technologies(html)
            if "Stripe" not in [t.lower() for t in detected]:
                detected = ["Stripe"] + detected

            result = DiscoveryResult(
                domain=domain,
                tech=detected,
                url=f"https://{domain}",
                detected_at=datetime.now(timezone.utc).isoformat(),
                title="Gift Card Store",
            )
            results.append(result)

        # Apply enrichment (domain age + SSL metadata)
        await enrich_discovery_results(results)

        # Write enriched CSV
        timestamp = datetime.now().strftime("%Y%m%d_%H%M")
        filename = f"stripe_gift_{timestamp}.csv"
        with open(filename, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(
                [
                    "domain",
                    "tech",
                    "url",
                    "detected_at",
                    "domain_age_days",
                    "creation_date",
                    "ssl_issuer",
                    "ssl_valid_from",
                    "ssl_valid_to",
                ]
            )
            for r in results:
                writer.writerow(
                    [
                        r.domain,
                        ";".join(r.tech),
                        r.url,
                        r.detected_at,
                        r.domain_age_days,
                        r.creation_date,
                        r.ssl_issuer,
                        r.ssl_valid_from,
                        r.ssl_valid_to,
                    ]
                )

        print(f"Exported {len(results)} enriched results to {filename}")
        print("\nSample enriched results:")
        for r in results[:3]:
            print(
                f"  {r.domain:25} age={r.domain_age_days}days "
                f"issuer={r.ssl_issuer} from={r.ssl_valid_from}"
            )

    except Exception as exc:  # pylint: disable=broad-except
        print("Error during discovery:", exc)
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
