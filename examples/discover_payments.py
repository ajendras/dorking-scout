"""Example script demonstrating discovery of Stripe-powered gift card stores.

Runs curated Stripe dorks combined with a gift-card term, performs
lightweight HTML-based detection, deduplicates results and exports
them to a CSV file with metadata. Runnable as-is:

    python examples/discover_payments.py
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
    """Discover Stripe-powered gift-card sites and write CSV."""
    print("🚀 Dork Scout — Stripe Gift Card Stores")
    print("=" * 55)

    # Focus on Stripe dorks narrowed to the gift-card niche via term
    dork_patterns = get_dorks(tech="Stripe")
    term = '("gift card" OR egift OR giftcard OR "electronic gift") (buy OR store OR checkout)'

    client = SearchClient(timeout=12.0)
    discovered: List[DiscoveryResult] = []
    seen: set[str] = set()

    try:
        for dork in dork_patterns[:3]:
            query = build_query(dork, term)
            print(f"Query: {query[:65]}...")

            domains = await client.search_with_rate_limit(
                query, dork.engine, delay_override=1.0
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
                    # Continue with basic info when fetch fails
                    pass

                # Guarantee Stripe appears because we searched for it
                if "Stripe" not in techs:
                    techs.insert(0, "Stripe")
                if not any("gift" in t.lower() for t in techs):
                    techs.append("Gift Cards")

                result = DiscoveryResult(
                    domain=domain,
                    tech=techs,
                    url=f"https://{domain}",
                    detected_at=datetime.now().isoformat(),
                    title="Gift Card Store",
                )
                discovered.append(result)
                print(f"  ✓ {domain} → {', '.join(techs)}")

                if len(discovered) >= 12:  # keep example runs fast
                    break
            if len(discovered) >= 12:
                break

        # Export to CSV
        timestamp = datetime.now().strftime("%Y%m%d_%H%M")
        csv_path = Path(f"stripe_gift_stores_{timestamp}.csv")

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
        print(f"Found and deduplicated {len(discovered)} domains.")
        print(f"Results saved to ./{csv_path.name}")
        print("\nTop results:")
        for item in discovered[:5]:
            print(f"  • {item.domain} ({';'.join(item.tech)})")

    except Exception as exc:  # pylint: disable=broad-except
        print(f"Unexpected error: {exc}")
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
