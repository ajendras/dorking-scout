"""Background job scheduler for fresh-domain monitoring and alerting.

AlertScheduler uses APScheduler (AsyncIOScheduler) to run configured
ScheduledJob entries on a regular interval. Each job:

* Executes a limited set of curated dorks for the chosen tech/category/term
* Identifies domains that have never been seen before (fresh)
* Builds DiscoveryResult objects, enriches them with age/SSL metadata
* Persists them via DiscoveryStore
* Sends an email (smtplib) or webhook (async httpx) containing the new domains

Jobs are persisted in the scheduled_jobs SQLite table. The scheduler is
started automatically by the FastAPI lifespan in main.py.
"""

import asyncio
import logging
import smtplib
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any, Dict, List

import httpx
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from .config import get_alert_config
from .detector import enrich_discovery_results
from .dorks import build_query, get_dorks
from .models import AlertConfig, DiscoveryResult, ScheduledJob
from .search import SearchClient
from .store import DiscoveryStore

logger = logging.getLogger(__name__)


class AlertScheduler:
    def __init__(self, store: DiscoveryStore):
        self.store = store
        self.scheduler = AsyncIOScheduler(timezone="UTC")
        self.alert_config = get_alert_config()
        self._load_and_schedule_jobs()

    def _load_and_schedule_jobs(self):
        """Load enabled jobs from the DB and register them with APScheduler."""
        for job_dict in self.store.get_scheduled_jobs():
            if job_dict.get("enabled", 1):
                try:
                    job = ScheduledJob.model_validate(job_dict)
                    self.schedule_job(job)
                except Exception as exc:  # pylint: disable=broad-except
                    logger.warning("Failed to load scheduled job %s: %s", job_dict.get("id"), exc)

    def schedule_job(self, job: ScheduledJob):
        """Add (or replace) a job in the APScheduler instance."""
        trigger = IntervalTrigger(hours=job.interval_hours)
        self.scheduler.add_job(
            self.run_job,
            trigger=trigger,
            args=[job.id],
            id=f"job_{job.id}",
            replace_existing=True,
        )
        logger.info("Scheduled job '%s' (every %d h) with alert %s", job.name, job.interval_hours, job.alert.alert_type)

    async def run_job(self, job_id: str):
        """Execute one monitoring job, detect fresh domains and send alert."""
        jobs = self.store.get_scheduled_jobs()
        job_dict = next((j for j in jobs if j["id"] == job_id), None)
        if not job_dict or not job_dict.get("enabled"):
            return

        job = ScheduledJob.model_validate(job_dict)
        now = datetime.now(timezone.utc).isoformat()
        client = SearchClient(timeout=15.0)

        try:
            dork_patterns = get_dorks(category=job.category, tech=job.tech)
            if not dork_patterns:
                logger.warning("No dork patterns for job %s", job.name)
                self.store.update_job_last_run(job_id, now)
                return

            all_domains: List[str] = []
            for dork in dork_patterns[:2]:  # keep responsive
                query = build_query(dork, job.term)
                domains = await client.search_with_rate_limit(query, dork.engine, delay_override=None)
                all_domains.extend(domains)

            # Deduplicate preserving order
            seen: set[str] = set()
            candidates = [d for d in all_domains if not (d in seen or seen.add(d))][:20]

            # Fresh = never seen before in the store
            known = {d["domain"] for d in self.store.get_unique_domains()}
            fresh = [d for d in candidates if d not in known]
            if not fresh:
                self.store.update_job_last_run(job_id, now)
                return

            # Build rich results and enrich
            results: List[DiscoveryResult] = []
            for domain in fresh:
                result = DiscoveryResult(
                    domain=domain,
                    tech=[job.tech] if job.tech else [],
                    url=f"https://{domain}",
                    detected_at=now,
                )
                results.append(result)

            await enrich_discovery_results(results)

            # Persist (this also updates the domains table with first_seen = now)
            self.store.record_search(
                query=f"Scheduled: {job.name}",
                results=results,
                category=job.category,
                tech=job.tech,
                term=job.term,
                engine="scheduled",
            )

            # Deliver alert and mark as alerted
            await self._send_alert(job, results)
            self.store.mark_domains_alerted([r.domain for r in results], now)
            self.store.update_job_last_run(job_id, now)
            logger.info("Job '%s' found %d fresh domains and sent alert", job.name, len(fresh))

        except Exception as exc:  # pylint: disable=broad-except
            logger.error("Scheduled job %s failed: %s", job.name, exc)
        finally:
            await client.close()

    async def _send_alert(self, job: ScheduledJob, results: List[DiscoveryResult]):
        """Dispatch email or webhook. Email is run in a thread to avoid blocking."""
        domains = [r.domain for r in results]
        message = f"Dork Scout found {len(domains)} fresh domain(s) for '{job.name}':\n\n"
        message += "\n".join([f"https://{d}" for d in domains])
        payload = {
            "job": job.name,
            "tech": job.tech,
            "term": job.term,
            "count": len(domains),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "domains": domains,
            "results": [r.model_dump(mode="json") for r in results],
        }

        if job.alert.alert_type == "email":
            await asyncio.to_thread(self._send_email, job.alert.target, job.name, message)
        else:
            await self._send_webhook(job.alert.target, payload)

    def _send_email(self, to_email: str, subject: str, body: str):
        """Synchronous SMTP delivery (called via asyncio.to_thread)."""
        cfg = self.alert_config.get("smtp", {})
        if not cfg.get("host") or not to_email:
            logger.warning("SMTP not configured – skipping email alert to %s", to_email)
            return

        try:
            msg = MIMEMultipart("alternative")
            msg["From"] = cfg.get("from_email", "alerts@dorkscout.local")
            msg["To"] = to_email
            msg["Subject"] = f"Dork Scout Alert: {subject}"

            msg.attach(MIMEText(body, "plain"))

            with smtplib.SMTP(cfg["host"], cfg.get("port", 587)) as server:
                if cfg.get("use_tls"):
                    server.starttls()
                if cfg.get("user") and cfg.get("password"):
                    server.login(cfg["user"], cfg["password"])
                server.send_message(msg)

            logger.info("Email alert sent to %s", to_email)
        except Exception as exc:  # pylint: disable=broad-except
            logger.error("Failed to send email alert: %s", exc)

    async def _send_webhook(self, url: str, payload: Dict[str, Any]):
        """Async HTTP POST of alert payload."""
        try:
            headers = self.alert_config.get("webhook", {}).get("headers", {})
            async with httpx.AsyncClient(timeout=10.0) as client:
                await client.post(url, json=payload, headers=headers)
            logger.info("Webhook alert delivered to %s", url)
        except Exception as exc:  # pylint: disable=broad-except
            logger.error("Webhook delivery to %s failed: %s", url, exc)

    def start(self):
        """Start the APScheduler (must be called from within an asyncio loop)."""
        self.scheduler.start()
        logger.info("AlertScheduler started – %d active jobs", len(self.scheduler.get_jobs()))

    def shutdown(self):
        """Gracefully stop the scheduler."""
        self.scheduler.shutdown()
        logger.info("AlertScheduler shut down")
