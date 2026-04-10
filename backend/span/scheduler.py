"""
APScheduler jobs for daily SPAN file refresh.

Schedule (IST, Mon–Fri):
  18:30  Primary download
  19:00  Retry if primary missed
  19:30  Final retry
"""

import logging
from datetime import date

import pytz
from apscheduler.triggers.cron import CronTrigger

from backend.extensions import scheduler

logger = logging.getLogger(__name__)
IST = pytz.timezone("Asia/Kolkata")


def _refresh_job(app, force: bool = False):
    """Download and parse SPAN data for today inside the Flask app context."""
    with app.app_context():
        from backend.utils.date_utils import most_recent_trading_day, today_ist
        from backend.span.downloader import already_downloaded, download_for_date
        from backend.span.orchestrator import parse_downloaded_file
        from backend.models.db import SpanFile

        trade_date = most_recent_trading_day(today_ist())

        if not force and already_downloaded(trade_date):
            logger.info("Scheduler: data for %s already present, skipping.", trade_date)
            return

        logger.info("Scheduler: starting SPAN refresh for %s", trade_date)
        zip_path, file_type = download_for_date(trade_date)
        if zip_path is None:
            logger.error("Scheduler: download failed for %s", trade_date)
            return

        span_file = SpanFile.query.filter_by(trade_date=trade_date).first()
        parse_downloaded_file(zip_path, file_type, trade_date, span_file)


def init_scheduler(app):
    """Register cron jobs and start the scheduler."""
    schedules = app.config.get("SPAN_REFRESH_SCHEDULE", [
        {"hour": 18, "minute": 30},
        {"hour": 19, "minute": 0},
        {"hour": 19, "minute": 30},
    ])

    for i, s in enumerate(schedules):
        job_id = f"span_refresh_{i}"
        force = (i == 0)   # first job forces; retries skip if already done
        scheduler.add_job(
            func=_refresh_job,
            args=[app, force],
            trigger=CronTrigger(
                hour=s["hour"],
                minute=s["minute"],
                day_of_week="mon-fri",
                timezone=IST,
            ),
            id=job_id,
            replace_existing=True,
            misfire_grace_time=600,   # 10 min grace
        )
        logger.info(
            "Scheduled SPAN refresh job '%s' at %02d:%02d IST (Mon-Fri)",
            job_id, s["hour"], s["minute"]
        )

    if not scheduler.running:
        scheduler.start()
    logger.info("APScheduler started.")
