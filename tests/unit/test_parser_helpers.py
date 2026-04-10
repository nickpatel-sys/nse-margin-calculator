"""
Unit tests for the pure-function helpers in:
  - backend/span/parser.py   (_s, _i, _f, _parse_date_span)
  - backend/span/bhavcopy_parser.py  (_make_contract_key, _parse_date, _to_float)
"""

from datetime import date

import pytest

from backend.span.parser import _f, _i, _parse_date_span, _s
from backend.span.bhavcopy_parser import _make_contract_key, _parse_date, _to_float


# ── backend/span/parser.py helpers ──────────────────────────────────────────

class TestFieldExtract_s:
    """_s(line, start, end) — 1-indexed, strips whitespace."""

    def test_extracts_middle_of_line(self):
        line = "ABCDE"
        assert _s(line, 2, 5) == "BCD"

    def test_strips_whitespace(self):
        line = "A  FOO  Z"
        assert _s(line, 3, 8) == "FOO"

    def test_line_too_short_returns_empty(self):
        assert _s("AB", 3, 10) == ""

    def test_start_equals_end_returns_empty(self):
        assert _s("ABCDE", 3, 3) == ""


class TestFieldExtract_i:
    def test_parses_integer(self):
        line = "XXXX  1234  XX"
        assert _i(line, 5, 11) == 1234

    def test_returns_default_on_non_numeric(self):
        line = "XXXXXXXXXX"
        assert _i(line, 1, 5, default=99) == 99

    def test_returns_zero_default_by_default(self):
        assert _i("ABCDE", 2, 5) == 0

    def test_negative_integer(self):
        line = "  -500  "
        assert _i(line, 1, 8) == -500


class TestFieldExtract_f:
    def test_divides_by_divisor(self):
        # Field "  1234  " / 100 = 12.34
        line = "  1234  "
        assert _f(line, 1, 9, 100.0) == pytest.approx(12.34)

    def test_no_divisor_is_raw_int(self):
        line = "  500  "
        assert _f(line, 1, 8, 1.0) == pytest.approx(500.0)

    def test_non_numeric_returns_zero(self):
        assert _f("XXXXXX", 1, 7, 100.0) == 0.0

    def test_negative_value(self):
        line = " -2000 "
        assert _f(line, 1, 8, 100.0) == pytest.approx(-20.0)


class TestParseDateSpan:
    def test_yyyymmdd_format(self):
        assert _parse_date_span("20260115") == date(2026, 1, 15)

    def test_ddmmyyyy_format(self):
        assert _parse_date_span("15012026") == date(2026, 1, 15)

    def test_invalid_date_returns_none(self):
        assert _parse_date_span("BADDATE!") is None

    def test_empty_string_returns_none(self):
        assert _parse_date_span("") is None

    def test_strips_whitespace(self):
        assert _parse_date_span("  20260115  ") == date(2026, 1, 15)


# ── backend/span/bhavcopy_parser.py helpers ──────────────────────────────────

class TestMakeContractKey:
    def test_futures_has_no_strike_or_opttype(self):
        key = _make_contract_key("NIFTY", "FUTIDX", date(2026, 1, 29), None, None)
        assert key == "NIFTY-FUTIDX-20260129"

    def test_option_includes_strike_and_opttype(self):
        key = _make_contract_key("NIFTY", "OPTIDX", date(2026, 1, 29), 22000.0, "CE")
        assert key == "NIFTY-OPTIDX-20260129-22000-CE"

    def test_whole_number_strike_has_no_decimal(self):
        key = _make_contract_key("NIFTY", "OPTIDX", date(2026, 1, 29), 22500.0, "PE")
        assert "22500" in key
        assert "22500.0" not in key

    def test_half_integer_strike_preserves_decimal(self):
        """72.5 should NOT be truncated to 72."""
        key = _make_contract_key("INOXWIND", "OPTSTK", date(2026, 4, 28), 72.5, "PE")
        assert "72.5" in key

    def test_no_duplicate_keys_for_different_half_strikes(self):
        k1 = _make_contract_key("X", "OPTSTK", date(2026, 1, 29), 72.0, "CE")
        k2 = _make_contract_key("X", "OPTSTK", date(2026, 1, 29), 72.5, "CE")
        assert k1 != k2

    def test_stock_option(self):
        key = _make_contract_key("RELIANCE", "OPTSTK", date(2026, 1, 29), 1200.0, "PE")
        assert key == "RELIANCE-OPTSTK-20260129-1200-PE"


class TestBhavParseDate:
    def test_dd_mmm_yyyy(self):
        assert _parse_date("15-Jan-2026") == date(2026, 1, 15)

    def test_yyyy_mm_dd(self):
        assert _parse_date("2026-01-15") == date(2026, 1, 15)

    def test_dd_slash_mm_slash_yyyy(self):
        assert _parse_date("15/01/2026") == date(2026, 1, 15)

    def test_yyyymmdd(self):
        assert _parse_date("20260115") == date(2026, 1, 15)

    def test_invalid_returns_none(self):
        assert _parse_date("not-a-date") is None

    def test_empty_returns_none(self):
        assert _parse_date("") is None


class TestToFloat:
    def test_valid_float_string(self):
        assert _to_float("22000.50") == pytest.approx(22000.50)

    def test_integer_string(self):
        assert _to_float("22000") == pytest.approx(22000.0)

    def test_invalid_returns_zero(self):
        assert _to_float("N/A") == 0.0

    def test_none_returns_zero(self):
        assert _to_float(None) == 0.0

    def test_empty_string_returns_zero(self):
        assert _to_float("") == 0.0
