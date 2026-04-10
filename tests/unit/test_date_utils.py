"""Unit tests for backend/utils/date_utils.py."""

from datetime import date

import pytest

from backend.utils.date_utils import (
    date_to_str,
    is_weekday,
    most_recent_trading_day,
    span_data_is_stale,
)


class TestDateToStr:
    def test_formats_as_yyyymmdd(self):
        assert date_to_str(date(2026, 1, 15)) == "20260115"

    def test_pads_month_and_day(self):
        assert date_to_str(date(2026, 3, 5)) == "20260305"

    def test_year_2000(self):
        assert date_to_str(date(2000, 12, 31)) == "20001231"


class TestIsWeekday:
    def test_monday_is_weekday(self):
        assert is_weekday(date(2026, 1, 12)) is True   # Monday

    def test_friday_is_weekday(self):
        assert is_weekday(date(2026, 1, 16)) is True   # Friday

    def test_saturday_is_not_weekday(self):
        assert is_weekday(date(2026, 1, 17)) is False  # Saturday

    def test_sunday_is_not_weekday(self):
        assert is_weekday(date(2026, 1, 18)) is False  # Sunday


class TestMostRecentTradingDay:
    def test_weekday_returns_same_day(self):
        thursday = date(2026, 1, 15)
        assert most_recent_trading_day(thursday) == thursday

    def test_saturday_returns_friday(self):
        saturday = date(2026, 1, 17)
        assert most_recent_trading_day(saturday) == date(2026, 1, 16)

    def test_sunday_returns_friday(self):
        sunday = date(2026, 1, 18)
        assert most_recent_trading_day(sunday) == date(2026, 1, 16)

    def test_monday_returns_monday(self):
        monday = date(2026, 1, 12)
        assert most_recent_trading_day(monday) == monday

    def test_month_boundary(self):
        """Sunday Jan 4 → Friday Jan 2."""
        assert most_recent_trading_day(date(2026, 1, 4)) == date(2026, 1, 2)


class TestSpanDataIsStale:
    def test_none_is_stale(self):
        assert span_data_is_stale(None) is True

    def test_old_date_is_stale(self):
        # A date far in the past is always stale.
        assert span_data_is_stale(date(2020, 1, 1)) is True

    def test_future_date_is_not_stale(self):
        # A date in the future is >= most_recent_trading_day so not stale.
        assert span_data_is_stale(date(2099, 12, 31)) is False
