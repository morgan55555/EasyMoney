"""APScheduler wiring for the hourly overdue-debt reminder scan (§2.7).

The business rules live in ``app.services.reminders``; this module only owns
the clock. ``python -m app.workers.scheduler`` runs one scan and exits — the
dev/CI door for the I2 mail verification.
"""

from __future__ import annotations

import asyncio
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from app.db.session import get_session_factory
from app.services import reminders as reminders_service

logger = logging.getLogger("easymoney.scheduler")

JOB_ID = "overdue-reminder-scan"


async def scan_once() -> None:
    """One full reminder pass (also the one-shot entry point)."""
    sent = await reminders_service.run_reminder_scan(get_session_factory())
    if sent:
        logger.info("Overdue reminder scan: %d email(s) sent", sent)


def start_scheduler() -> AsyncIOScheduler:
    """Start the hourly job in the current event loop (app lifespan)."""
    scheduler = AsyncIOScheduler()
    scheduler.add_job(scan_once, IntervalTrigger(hours=1), id=JOB_ID, replace_existing=True)
    scheduler.start()
    return scheduler


def main() -> None:
    """One-shot scan for dev / I2 mail verification."""
    asyncio.run(scan_once())


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
