"""
Downloads NSE SPAN / UDiFF bhavcopy files for a given trade date.

Strategy
--------
1. Try the legacy SPAN SPN file (contains full risk arrays).
2. If 403/404, fall back to the UDiFF F&O bhavcopy (contract data only).
3. Cache: skip download if file already on disk and DB shows parse_status='success'.
"""

import logging
import zipfile
from datetime import date
from pathlib import Path

from flask import current_app

from backend.extensions import db
from backend.models.db import SpanFile
from backend.utils.date_utils import date_to_str
from backend.utils.http_client import build_session, download_file

logger = logging.getLogger(__name__)


def _data_dir() -> Path:
    d = Path(current_app.config["DATA_DIR"])
    d.mkdir(parents=True, exist_ok=True)
    return d


def _span_url(d: date) -> str:
    cfg = current_app.config
    return cfg["NSE_SPAN_URL_PATTERN"].format(
        base=cfg["NSE_SPAN_BASE_URL"], date=date_to_str(d)
    )


def _bhavcopy_url(d: date) -> str:
    cfg = current_app.config
    return cfg["NSE_BHAVCOPY_URL_PATTERN"].format(
        base=cfg["NSE_BHAVCOPY_BASE_URL"], date=date_to_str(d)
    )


def already_downloaded(trade_date: date) -> bool:
    """Return True if today's data was successfully parsed already."""
    record = SpanFile.query.filter_by(
        trade_date=trade_date, parse_status="success"
    ).first()
    return record is not None


def download_for_date(trade_date: date) -> tuple[Path | None, str]:
    """
    Download the SPAN or bhavcopy file for *trade_date*.

    Returns (local_zip_path, file_type) where file_type is
    'span_spn' or 'udiff_bhavcopy', or (None, 'none') on failure.
    """
    if already_downloaded(trade_date):
        logger.info("SPAN data for %s already present, skipping download.", trade_date)
        # Find the cached file on disk
        span_zip = _data_dir() / f"span_{date_to_str(trade_date)}.zip"
        bhav_zip = _data_dir() / f"bhavcopy_{date_to_str(trade_date)}.zip"
        if span_zip.exists():
            return span_zip, "span_spn"
        if bhav_zip.exists():
            return bhav_zip, "udiff_bhavcopy"

    session = build_session(
        max_retries=current_app.config["DOWNLOAD_MAX_RETRIES"],
        backoff_factor=current_app.config["DOWNLOAD_BACKOFF_FACTOR"],
        connect_timeout=current_app.config["DOWNLOAD_TIMEOUT_CONNECT"],
    )

    # ── Attempt 1: SPAN SPN file ──────────────────────────────────────────────
    span_url = _span_url(trade_date)
    span_zip = _data_dir() / f"span_{date_to_str(trade_date)}.zip"
    try:
        ok = download_file(
            session, span_url, span_zip,
            connect_timeout=current_app.config["DOWNLOAD_TIMEOUT_CONNECT"],
            read_timeout=current_app.config["DOWNLOAD_TIMEOUT_READ"],
        )
        if ok and _is_valid_zip(span_zip):
            _upsert_span_file_record(trade_date, "span_spn", span_url)
            return span_zip, "span_spn"
        elif span_zip.exists():
            span_zip.unlink(missing_ok=True)
    except Exception as exc:
        logger.warning("SPAN SPN download failed: %s", exc)
        span_zip.unlink(missing_ok=True)

    # ── Attempt 2: UDiFF bhavcopy ─────────────────────────────────────────────
    bhav_url = _bhavcopy_url(trade_date)
    bhav_zip = _data_dir() / f"bhavcopy_{date_to_str(trade_date)}.zip"
    try:
        ok = download_file(
            session, bhav_url, bhav_zip,
            connect_timeout=current_app.config["DOWNLOAD_TIMEOUT_CONNECT"],
            read_timeout=current_app.config["DOWNLOAD_TIMEOUT_READ"],
        )
        if ok and _is_valid_zip(bhav_zip):
            _upsert_span_file_record(trade_date, "udiff_bhavcopy", bhav_url)
            return bhav_zip, "udiff_bhavcopy"
        elif bhav_zip.exists():
            bhav_zip.unlink(missing_ok=True)
    except Exception as exc:
        logger.warning("Bhavcopy download failed: %s", exc)
        bhav_zip.unlink(missing_ok=True)

    logger.error("All download attempts failed for %s.", trade_date)
    return None, "none"


def _is_valid_zip(path: Path) -> bool:
    if not path.exists() or path.stat().st_size < 100:
        return False
    try:
        with zipfile.ZipFile(path):
            return True
    except zipfile.BadZipFile:
        return False


def _upsert_span_file_record(trade_date: date, file_type: str, url: str):
    from datetime import datetime
    record = SpanFile.query.filter_by(trade_date=trade_date).first()
    if record is None:
        record = SpanFile(trade_date=trade_date)
        db.session.add(record)
    record.file_type = file_type
    record.download_url = url
    record.downloaded_at = datetime.utcnow()
    record.parse_status = "pending"
    db.session.commit()
