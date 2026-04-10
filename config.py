import os
from pathlib import Path

BASE_DIR = Path(__file__).parent


class Config:
    # ── Database ──────────────────────────────────────────────────────────────
    DATA_DIR = Path(os.environ.get("DATA_DIR", BASE_DIR / "data"))
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "DATABASE_URL", f"sqlite:///{DATA_DIR / 'nse_margin.db'}"
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # ── NSE download endpoints ────────────────────────────────────────────────
    NSE_HOME_URL = "https://www.nseindia.com"
    NSE_SPAN_BASE_URL = "https://nsearchives.nseindia.com/content/nsccl"
    NSE_BHAVCOPY_BASE_URL = "https://nsearchives.nseindia.com/content/fo"

    # SPAN SPN file pattern  (try YYYYMMDD substitution)
    NSE_SPAN_URL_PATTERN = (
        "{base}/nsccl.{date}.s.inn.spn.zip"
    )
    # UDiFF bhavcopy pattern (confirmed public)
    NSE_BHAVCOPY_URL_PATTERN = (
        "{base}/BhavCopy_NSE_FO_0_0_0_{date}_F_0000.csv.zip"
    )

    # ── HTTP client settings ──────────────────────────────────────────────────
    DOWNLOAD_TIMEOUT_CONNECT = 30   # seconds
    DOWNLOAD_TIMEOUT_READ = 120     # seconds
    DOWNLOAD_MAX_RETRIES = 3
    DOWNLOAD_BACKOFF_FACTOR = 5     # seconds (5 / 15 / 30)

    # ── Margin rates ──────────────────────────────────────────────────────────
    INDEX_EXPOSURE_MARGIN_RATE = 0.03       # 3 % for index derivatives
    STOCK_EXPOSURE_MARGIN_RATE = 0.05       # 5 % for stock derivatives
    EXTREME_SCENARIO_COVER_FRACTION = 0.35  # 35 % credit on scenarios 15 & 16

    # Fallback PSR estimates (used when official SPAN file is unavailable)
    FALLBACK_PSR_RATES = {
        "NIFTY": 0.08,
        "NIFTY50": 0.08,
        "BANKNIFTY": 0.09,
        "FINNIFTY": 0.09,
        "MIDCPNIFTY": 0.09,
        "SENSEX": 0.08,
        "__STOCK__": 0.15,   # default for all stock underlyings
    }

    # Known inter-commodity spread pairs (fallback when SPN unavailable)
    # (leg1, leg2, credit_rate, delta_ratio_leg1, delta_ratio_leg2)
    FALLBACK_INTER_SPREADS = [
        ("BANKNIFTY", "NIFTY", 0.50, 1, 3),
        ("FINNIFTY",  "NIFTY", 0.50, 1, 2),
        ("MIDCPNIFTY","NIFTY", 0.50, 1, 2),
    ]

    # ── Scheduler (IST) ───────────────────────────────────────────────────────
    SPAN_REFRESH_SCHEDULE = [
        {"hour": 18, "minute": 30},   # primary: 6:30 PM
        {"hour": 19, "minute": 0},    # retry 1: 7:00 PM
        {"hour": 19, "minute": 30},   # retry 2: 7:30 PM
    ]

    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-nse-margin")
