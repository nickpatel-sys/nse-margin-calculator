"""
Orchestrates download → parse → DB update for a single trade date.

Called by:
  • The scheduler (daily automated refresh)
  • The /api/span/refresh endpoint (manual trigger)
  • App startup (catch-up if server restarted after 6 PM)
"""

import logging
from datetime import date
from pathlib import Path

from backend.extensions import db
from backend.models.db import SpanFile

logger = logging.getLogger(__name__)


def refresh_for_date(trade_date: date, force: bool = False) -> dict:
    """
    Full pipeline: download + parse for *trade_date*.

    Returns a status dict suitable for JSON response.
    """
    from backend.span.downloader import already_downloaded, download_for_date

    if not force and already_downloaded(trade_date):
        span_file = SpanFile.query.filter_by(trade_date=trade_date).first()
        return _status_dict(span_file, "already_current")

    zip_path, file_type = download_for_date(trade_date)
    if zip_path is None:
        return {"status": "error", "message": "Download failed for all URL patterns."}

    span_file = SpanFile.query.filter_by(trade_date=trade_date).first()
    return parse_downloaded_file(zip_path, file_type, trade_date, span_file)


def parse_downloaded_file(
    zip_path: Path, file_type: str, trade_date: date, span_file: SpanFile
) -> dict:
    """
    Parse an already-downloaded bhavcopy file, then overlay the SPAN XML if present.

    Step 1: Parse bhavcopy → contracts + estimated combined commodities.
    Step 2: If span_{date}.zip exists on disk, parse SPAN XML → populate risk arrays
            and update CombinedCommodity PSR/VSR with official values.
    """
    if span_file is None:
        logger.error("No SpanFile DB record found for %s", trade_date)
        return {"status": "error", "message": "No DB record for this date."}

    try:
        # Step 1 — bhavcopy (always)
        from backend.span.bhavcopy_parser import parse_bhavcopy
        count = parse_bhavcopy(zip_path, trade_date, span_file)

        # Step 2 — SPAN XML (opportunistic)
        has_risk_arrays = False
        span_count = 0
        from backend.span.downloader import span_xml_path
        sx_path = span_xml_path(trade_date)
        if sx_path is not None:
            try:
                from backend.span.span_xml_parser import parse_span_xml
                span_count = parse_span_xml(sx_path, trade_date, span_file)
                has_risk_arrays = span_count > 0
                if has_risk_arrays:
                    span_file.file_type = "span_xml+bhavcopy"
            except Exception as exc:
                logger.warning("SPAN XML parse failed for %s: %s", trade_date, exc)

        span_file.parse_status = "success"
        span_file.error_message = None
        db.session.commit()

        return _status_dict(span_file, "success", {
            "instrument_count": count,
            "risk_array_count": span_count,
            "has_risk_arrays": has_risk_arrays,
            "data_mode": "span_file" if has_risk_arrays else "estimated",
        })

    except Exception as exc:
        logger.exception("Parse failed for %s: %s", trade_date, exc)
        span_file.parse_status = "error"
        span_file.error_message = str(exc)
        db.session.commit()
        return {"status": "error", "message": str(exc)}


def _status_dict(span_file: SpanFile | None, status: str, extra: dict = None) -> dict:
    result = {"status": status}
    if span_file:
        result["trade_date"] = span_file.trade_date.isoformat() if span_file.trade_date else None
        result["file_type"] = span_file.file_type
        result["downloaded_at"] = (
            span_file.downloaded_at.isoformat() if span_file.downloaded_at else None
        )
        result["parse_status"] = span_file.parse_status
    if extra:
        result.update(extra)
    return result
