"""
ISIN lookup map for NSE-listed underlying securities.

Downloads NSE's equity master file (EQUITY_L.csv) once and caches it locally.
Provides a {symbol: isin} mapping used when parsing bhavcopy to populate
Contract.underlying_isin for stock F&O instruments.

Index instruments (NIFTY, BANKNIFTY, FINNIFTY, etc.) are not listed in
EQUITY_L.csv — their underlying_isin is left NULL.
"""

import csv
import io
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

_EQUITY_LIST_URL = "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv"
_CACHE_TTL_DAYS  = 7
_CACHE_FILENAME  = "equity_isin.json"

# Module-level in-process cache so we parse the CSV once per worker lifetime
_cache: dict[str, str] | None = None


def get_isin_map() -> dict[str, str]:
    """
    Return {NSE_symbol: ISIN} for all equity-listed securities.

    Result is cached in memory after first call; JSON file on disk is refreshed
    when older than _CACHE_TTL_DAYS.  Returns an empty dict on any download failure
    so callers degrade gracefully.
    """
    global _cache
    if _cache is not None:
        return _cache
    _cache = _load_or_download()
    return _cache


# ── Internal helpers ──────────────────────────────────────────────────────────

def _cache_path() -> Path:
    from config import Config
    return Path(Config.DATA_DIR) / _CACHE_FILENAME


def _load_or_download() -> dict[str, str]:
    path = _cache_path()
    if path.exists():
        age = datetime.now() - datetime.fromtimestamp(path.stat().st_mtime)
        if age < timedelta(days=_CACHE_TTL_DAYS):
            try:
                data = json.loads(path.read_text())
                logger.info("ISIN map: loaded %d entries from cache %s", len(data), path)
                return data
            except Exception:
                pass  # corrupt — fall through to re-download
    return _download_and_cache()


def _download_and_cache() -> dict[str, str]:
    try:
        import requests
        resp = requests.get(
            _EQUITY_LIST_URL,
            headers={
                "User-Agent": "Mozilla/5.0",
                "Referer": "https://www.nseindia.com/",
            },
            timeout=30,
        )
        resp.raise_for_status()
        data = _parse_equity_csv(resp.text)

        path = _cache_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data))
        logger.info("ISIN map: downloaded %d entries → %s", len(data), path)
        return data
    except Exception as exc:
        logger.warning("ISIN map: download failed (%s) — underlying_isin will be NULL", exc)
        return {}


def _parse_equity_csv(text: str) -> dict[str, str]:
    reader = csv.DictReader(io.StringIO(text))
    # Column header is " ISIN NUMBER" (note leading space in NSE file)
    isin_col: str | None = None
    result: dict[str, str] = {}
    for row in reader:
        if isin_col is None:
            isin_col = next((k for k in row if "ISIN" in k.upper()), None)
            if isin_col is None:
                break
        symbol = row.get("SYMBOL", "").strip()
        isin   = row.get(isin_col, "").strip()
        if symbol and isin and isin.startswith("IN"):
            result[symbol] = isin
    return result
