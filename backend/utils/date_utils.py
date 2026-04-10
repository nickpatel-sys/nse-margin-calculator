"""Date helpers for IST timezone and NSE trading calendar."""

from datetime import date, datetime, timedelta
import pytz

IST = pytz.timezone("Asia/Kolkata")


def now_ist() -> datetime:
    return datetime.now(IST)


def today_ist() -> date:
    return now_ist().date()


def date_to_str(d: date) -> str:
    """Return YYYYMMDD string for use in NSE URL patterns."""
    return d.strftime("%Y%m%d")


def is_weekday(d: date) -> bool:
    return d.weekday() < 5  # Mon–Fri


def most_recent_trading_day(reference: date | None = None) -> date:
    """
    Return the most recent weekday on or before *reference*.
    Does not account for NSE holidays (those would require a holiday calendar).
    """
    d = reference or today_ist()
    while not is_weekday(d):
        d -= timedelta(days=1)
    return d


def span_data_is_stale(last_trade_date: date | None) -> bool:
    """True if the stored SPAN data is older than the most recent trading day."""
    if last_trade_date is None:
        return True
    return last_trade_date < most_recent_trading_day()
