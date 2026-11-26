"""AlertScheduler for persistent monitoring jobs with alerts.

Uses APScheduler to run recurring dork searches in background. Detects
fresh (previously unseen) domains using the DiscoveryStore, enriches them,
persists, and delivers alerts via email or webhook. Jobs are persisted
in SQLite so they survive restarts. The build_alert_payload method is
public to enable comprehensive testing of the exact payload structure.
"""

import smtplib
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any, Dict, List, Optional

import httpx
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from .config import get_alert_config
from .detector import enrich_discovery_results
from .dorks import build_query, get_dorks
from .models import AlertConfig, AlertPayload, DiscoveryResult, ScheduledJob
from .search import SearchClient, is_noise_domain
from .store import DiscoveryStore


class AlertScheduler:
    """Manages scheduled dork monitoring jobs with alerts for new domains."""

    def __init__(self, store: Optional[DiscoveryStore] = None):
        self.store = store or DiscoveryStore()
        self.scheduler = AsyncIOScheduler()
        self.alert_config: Dict[str, Any] = get_alert_config()
        self._load_existing_jobs()

    def _load_existing_jobs(self) -> None:
        """Load persisted jobs from store and re-schedule them."""
        for job_dict in self.store.get_scheduled_jobs():
            try:
                job = ScheduledJob.model_validate(job_dict)
                self._schedule_job(job)
            except Exception:  # pylint: disable=broad-except
                continue  # skip invalid jobs

    def add_job(
        self,
        name: str,
        tech: str,
        term: str = "",
        interval_hours: int = 6,
        alert: Optional[Dict[str, Any]] = None,
    ) -> ScheduledJob:
        """Add a new monitoring job, persist it and schedule execution."""
        if alert is None:
            alert = {"alert_type": "webhook", "target": "https://example.com/alert"}
        alert_config = AlertConfig.model_validate(alert)
        job = ScheduledJob(
            name=name,
            tech=tech,
            term=term,
            interval_hours=interval_hours,
            alert=alert_config,
        )
        self.store.add_scheduled_job(job)
        self._schedule_job(job)
        return job

    def _schedule_job(self, job: ScheduledJob) -> None:
        """Schedule the job with APScheduler using interval trigger."""
        trigger = IntervalTrigger(hours=job.interval_hours, timezone="UTC")
        self.scheduler.add_job(
            self._run_job,
            trigger=trigger,
            args=[job.id],
            id=job.id,
            replace_existing=True,
            misfire_grace_time=60,
        )

    async def _run_job(self, job_id: str) -> None:
        """Execute a single monitoring job: search, detect fresh domains, alert."""
        job_dict = self.store.get_scheduled_job(job_id)
        if not job_dict:
            return
        job = ScheduledJob.model_validate(job_dict)
        now = datetime.now(timezone.utc)
        job.last_run = now
        self.store.update_scheduled_job(job)

        dork_patterns = get_dorks(tech=job.tech)
        if not dork_patterns:
            return

        client = SearchClient(timeout=12.0)
        try:
            all_domains: List[str] = []
            for dork in dork_patterns[:2]:
                query = build_query(dork, job.term)
                domains = await client.search_with_rate_limit(
                    query, dork.engine, delay_override=0.5
                )
                all_domains.extend(domains)

            # deduplicate and filter noise
            seen: set[str] = set()
            unique_domains = [
                d
                for d in all_domains
                if not (d in seen or seen.add(d)) and not is_noise_domain(d)
            ]

            # determine fresh domains using store
            existing_domains = {
                d["domain"] for d in self.store.get_unique_domains(limit=500)
            }
            fresh_domains = [d for d in unique_domains if d not in existing_domains]

            if not fresh_domains:
                return

            # create results
            results: List[DiscoveryResult] = []
            for domain in fresh_domains[:15]:
                result = DiscoveryResult(
                    domain=domain,
                    tech=[job.tech],
                    url=f"https://{domain}",
                    detected_at=now.isoformat(),
                )
                results.append(result)

            await enrich_discovery_results(results)

            # persist new domains
            for result in results:
                self.store.record_domain(
                    result.domain, result.tech, result.relevance_score
                )

            # check cooldown and send alert
            if self._should_send_alert(job):
                await self.send_alert(job, results)
                job.last_alerted = now
                self.store.update_scheduled_job(job)
        except Exception as exc:  # pylint: disable=broad-except
            print(f"Job {job_id} failed: {exc}")
        finally:
            await client.close()

    def _should_send_alert(self, job: ScheduledJob) -> bool:
        """Respect cooldown period before sending another alert."""
        if job.last_alerted is None:
            return True
        cooldown_minutes = self.alert_config.get("cooldown_minutes", 30)
        cooldown = timedelta(minutes=cooldown_minutes)
        return datetime.now(timezone.utc) - job.last_alerted > cooldown

    async def send_alert(
        self, job: ScheduledJob, results: List[DiscoveryResult]
    ) -> None:
        """Send alert using the configured method (email or webhook)."""
        alert = job.alert
        payload = self.build_alert_payload(job, results)
        if alert.alert_type == "webhook":
            await self._send_webhook(alert.target, payload)
        elif alert.alert_type == "email":
            self._send_email(alert.target, payload)

    def build_alert_payload(
        self, job: ScheduledJob, results: List[DiscoveryResult]
    ) -> Dict[str, Any]:
        """Construct and validate the alert payload.

        This is the core method tested by the integration suite to ensure
        rich context (including enriched metadata and relevance scores) is
        included in every alert.
        """
        payload = AlertPayload(
            job_id=job.id,
            job_name=job.name,
            tech=job.tech,
            term=job.term,
            timestamp=datetime.now(timezone.utc).isoformat(),
            new_domains_count=len(results),
            domains=[r.domain for r in results],
            results=[r.model_dump(mode="json") for r in results],
            message=(
                f"Found {len(results)} new domains for {job.tech} "
                f"matching term '{job.term}'"
            ),
        )
        return payload.model_dump()

    async def _send_webhook(self, url: str, payload: Dict[str, Any]) -> None:
        """POST the JSON payload to the configured webhook URL."""
        headers = self.alert_config.get("webhook", {}).get("headers", {})
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                await client.post(url, json=payload, headers=headers)
        except Exception as exc:  # pylint: disable=broad-except
            print(f"Webhook delivery to {url} failed: {exc}")

    def _send_email(self, to_email: str, payload: Dict[str, Any]) -> None:
        """Send formatted email alert using SMTP."""
        cfg = self.alert_config.get("smtp", {})
        if not cfg.get("user") or not cfg.get("password"):
            print("SMTP credentials not configured - skipping email.")
            return
        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"[Dork Scout] {payload['job_name']}"
        msg["From"] = cfg.get("from_email", "alerts@dorkscout.local")
        msg["To"] = to_email
        text_part = MIMEText(
            f"{payload['message']}\n\n"
            f"New domains:\n" + "\n".join(payload["domains"]) + "\n\n"
            f"Relevance scores and metadata are available in the dashboard.",
            "plain",
        )
        msg.attach(text_part)
        try:
            with smtplib.SMTP(cfg["host"], cfg["port"]) as server:
                if cfg.get("use_tls", True):
                    server.starttls()
                server.login(cfg["user"], cfg["password"] or "")
                server.send_message(msg)
        except Exception as exc:  # pylint: disable=broad-except
            print(f"Email delivery to {to_email} failed: {exc}")

    def start(self) -> None:
        """Start the APScheduler."""
        if not self.scheduler.running:
            self.scheduler.start()

    def shutdown(self) -> None:
        """Shutdown the scheduler gracefully."""
        if self.scheduler.running:
            self.scheduler.shutdown()

    def get_jobs(self) -> List[ScheduledJob]:
        """Return list of active scheduled jobs."""
        return [
            ScheduledJob.model_validate(d)
            for d in self.store.get_scheduled_jobs()
        ]
