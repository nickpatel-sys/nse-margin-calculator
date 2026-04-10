"""Unit tests for backend/margin/exposure.py."""

import pytest

from backend.margin.exposure import PositionInput, calc_exposure, calc_portfolio_exposure


def _pos(instrument_type, side, lots, lot_size=25, underlying=22000.0,
         exposure_rate=0.03, is_calendar_spread=False, is_near_month=False):
    return PositionInput(
        instrument_type=instrument_type,
        side=side,
        lots=lots,
        lot_size=lot_size,
        underlying_price=underlying,
        exposure_margin_rate=exposure_rate,
        is_near_month_leg=is_near_month,
        is_calendar_spread=is_calendar_spread,
    )


class TestCalcExposure:
    def test_short_index_future(self):
        """Short index future: 3% × notional."""
        pos = _pos("FUTIDX", "sell", -1)
        notional = 1 * 25 * 22000
        assert calc_exposure(pos) == pytest.approx(0.03 * notional)

    def test_long_index_future(self):
        """Long index future also pays exposure margin."""
        pos = _pos("FUTIDX", "buy", 1)
        notional = 1 * 25 * 22000
        assert calc_exposure(pos) == pytest.approx(0.03 * notional)

    def test_short_stock_future_uses_5pct(self):
        pos = _pos("FUTSTK", "sell", -1, lot_size=250, underlying=1200.0, exposure_rate=0.05)
        notional = 1 * 250 * 1200
        assert calc_exposure(pos) == pytest.approx(0.05 * notional)

    def test_long_option_buyer_pays_zero(self):
        """Long option buyer: no exposure margin."""
        pos = _pos("OPTIDX", "buy", 1)
        assert calc_exposure(pos) == 0.0

    def test_long_stock_option_buyer_pays_zero(self):
        pos = _pos("OPTSTK", "buy", 1)
        assert calc_exposure(pos) == 0.0

    def test_short_option_pays_exposure(self):
        """Short option writer pays exposure margin."""
        pos = _pos("OPTIDX", "sell", -1)
        notional = 1 * 25 * 22000
        assert calc_exposure(pos) == pytest.approx(0.03 * notional)

    def test_multiple_lots_scale_linearly(self):
        pos = _pos("FUTIDX", "sell", -5)
        single = calc_exposure(_pos("FUTIDX", "sell", -1))
        assert calc_exposure(pos) == pytest.approx(5 * single)

    def test_zero_underlying_price_gives_zero(self):
        pos = _pos("FUTIDX", "sell", -1, underlying=0.0)
        assert calc_exposure(pos) == 0.0

    def test_lots_sign_irrelevant_uses_abs(self):
        """Both +1 and -1 lots give the same exposure (magnitude matters)."""
        long_pos  = _pos("FUTIDX", "buy",  1)
        short_pos = _pos("FUTIDX", "sell", -1)
        assert calc_exposure(long_pos) == pytest.approx(calc_exposure(short_pos))

    def test_calendar_spread_far_month_uses_third_notional(self):
        pos = _pos("FUTIDX", "sell", -1, is_calendar_spread=True, is_near_month=False)
        notional = 1 * 25 * 22000
        assert calc_exposure(pos) == pytest.approx(0.03 * notional / 3)

    def test_calendar_spread_near_month_uses_full_notional(self):
        pos = _pos("FUTIDX", "sell", -1, is_calendar_spread=True, is_near_month=True)
        notional = 1 * 25 * 22000
        assert calc_exposure(pos) == pytest.approx(0.03 * notional)


class TestCalcPortfolioExposure:
    def test_empty_portfolio_is_zero(self):
        assert calc_portfolio_exposure([]) == 0.0

    def test_single_position(self):
        pos = _pos("FUTIDX", "sell", -1)
        expected = calc_exposure(pos)
        assert calc_portfolio_exposure([pos]) == pytest.approx(expected)

    def test_sums_across_multiple_positions(self):
        pos1 = _pos("FUTIDX", "sell", -1)
        pos2 = _pos("FUTSTK", "sell", -2, lot_size=250, underlying=1200.0, exposure_rate=0.05)
        expected = calc_exposure(pos1) + calc_exposure(pos2)
        assert calc_portfolio_exposure([pos1, pos2]) == pytest.approx(expected)

    def test_long_options_do_not_add_exposure(self):
        fut = _pos("FUTIDX", "sell", -1)
        opt = _pos("OPTIDX", "buy", 1)   # long option: 0 exposure
        expected = calc_exposure(fut)
        assert calc_portfolio_exposure([fut, opt]) == pytest.approx(expected)
