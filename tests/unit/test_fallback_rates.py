"""Unit tests for backend/margin/fallback_rates.py."""

import pytest

from backend.margin.fallback_rates import (
    _option_delta,
    _psr_rate,
    build_fallback_risk_array,
    get_fallback_commodity,
)


class TestPsrRate:
    def test_nifty_rate(self):
        assert _psr_rate("NIFTY") == pytest.approx(0.08)

    def test_banknifty_rate(self):
        assert _psr_rate("BANKNIFTY") == pytest.approx(0.09)

    def test_unknown_symbol_uses_stock_default(self):
        assert _psr_rate("UNKNOWN_STOCK") == pytest.approx(0.15)

    def test_midcpnifty_rate(self):
        assert _psr_rate("MIDCPNIFTY") == pytest.approx(0.09)


class TestGetFallbackCommodity:
    def test_index_returns_3pct_exposure(self):
        result = get_fallback_commodity("NIFTY", 22000.0, is_index=True)
        assert result["exposure_margin_rate"] == pytest.approx(0.03)

    def test_stock_returns_5pct_exposure(self):
        result = get_fallback_commodity("RELIANCE", 1200.0, is_index=False)
        assert result["exposure_margin_rate"] == pytest.approx(0.05)

    def test_index_psr_is_psr_rate_times_price(self):
        result = get_fallback_commodity("NIFTY", 22000.0, is_index=True)
        expected_psr = 0.08 * 22000.0
        assert result["price_scan_range"] == pytest.approx(expected_psr)

    def test_stock_psr_uses_stock_rate(self):
        result = get_fallback_commodity("RELIANCE", 1200.0, is_index=False)
        expected_psr = 0.15 * 1200.0
        assert result["price_scan_range"] == pytest.approx(expected_psr)

    def test_zero_price_gives_zero_psr(self):
        result = get_fallback_commodity("NIFTY", 0.0, is_index=True)
        assert result["price_scan_range"] == 0.0
        assert result["short_option_min_charge"] == 0.0

    def test_index_vsr_is_4pct(self):
        result = get_fallback_commodity("NIFTY", 22000.0, is_index=True)
        assert result["volatility_scan_range"] == pytest.approx(0.04)

    def test_stock_vsr_is_6pct(self):
        result = get_fallback_commodity("RELIANCE", 1200.0, is_index=False)
        assert result["volatility_scan_range"] == pytest.approx(0.06)

    def test_somc_is_1_5pct_of_price(self):
        result = get_fallback_commodity("NIFTY", 22000.0, is_index=True)
        assert result["short_option_min_charge"] == pytest.approx(0.015 * 22000.0)


class TestOptionDelta:
    def test_future_delta_is_one(self):
        assert _option_delta("FUTIDX", None, 22000, None, 1000, 0.04) == pytest.approx(1.0)

    def test_atm_call_delta_is_half(self):
        # ATM: strike == ref → moneyness = 0 → delta = 0.5
        delta = _option_delta("OPTIDX", "CE", 22000, 22000, 1000, 0.04)
        assert delta == pytest.approx(0.5)

    def test_atm_put_delta_is_minus_half(self):
        delta = _option_delta("OPTIDX", "PE", 22000, 22000, 1000, 0.04)
        assert delta == pytest.approx(-0.5)

    def test_deep_itm_call_delta_clamped_to_one(self):
        # Very high ref vs strike → moneyness >> 0 → delta → 1.0
        delta = _option_delta("OPTIDX", "CE", 40000, 10000, 1000, 0.04)
        assert delta == pytest.approx(1.0)

    def test_deep_otm_call_delta_clamped_to_zero(self):
        delta = _option_delta("OPTIDX", "CE", 10000, 40000, 1000, 0.04)
        assert delta == pytest.approx(0.0)

    def test_deep_itm_put_delta_clamped_to_minus_one(self):
        delta = _option_delta("OPTIDX", "PE", 10000, 40000, 1000, 0.04)
        assert delta == pytest.approx(-1.0)

    def test_zero_ref_returns_atm_delta(self):
        # When ref=0, moneyness=0, delta=0.5 for CE
        delta = _option_delta("OPTIDX", "CE", 0, 22000, 1000, 0.04)
        assert delta == pytest.approx(0.5)


class TestBuildFallbackRiskArray:
    def test_returns_16_values(self):
        arr = build_fallback_risk_array("FUTIDX", None, None, 22000, 22050, 25, 1000, 0.04)
        assert len(arr) == 16

    def test_futures_no_price_move_is_zero(self):
        arr = build_fallback_risk_array("FUTIDX", None, None, 22000, 22050, 25, 1000, 0.04)
        # Scenarios 1 & 2 (indices 0, 1): price move = 0
        assert arr[0] == pytest.approx(0.0)
        assert arr[1] == pytest.approx(0.0)

    def test_futures_worst_scenario_is_full_psr(self):
        """For a long future, worst loss = PSR (price drops by full PSR)."""
        psr = 1000.0
        arr = build_fallback_risk_array("FUTIDX", None, None, 22000, 22050, 25, psr, 0.04)
        # Scenario 13 (index 12): price drop by PSR → loss = +PSR
        assert arr[12] == pytest.approx(psr)

    def test_futures_best_scenario_is_minus_full_psr(self):
        psr = 1000.0
        arr = build_fallback_risk_array("FUTIDX", None, None, 22000, 22050, 25, psr, 0.04)
        # Scenario 11 (index 10): price rise by PSR → gain = -PSR
        assert arr[10] == pytest.approx(-psr)

    def test_extreme_scenarios_are_double_psr(self):
        psr = 1000.0
        arr = build_fallback_risk_array("FUTIDX", None, None, 22000, 22050, 25, psr, 0.04)
        # Scenario 16 (index 15): price drops 2×PSR
        assert arr[15] == pytest.approx(2 * psr)
        assert arr[14] == pytest.approx(-2 * psr)

    def test_call_option_has_bounded_loss_on_downside(self):
        """Long call loses limited amount when price drops."""
        arr = build_fallback_risk_array("OPTIDX", 22000, "CE", 22000, 150, 25, 1000, 0.04)
        # Price drop scenarios should show positive loss (capped by delta)
        downside_loss = arr[12]  # scenario 13: -PSR
        assert downside_loss > 0   # long call loses when price drops

    def test_put_option_has_bounded_loss_on_upside(self):
        """Long put loses limited amount when price rises."""
        arr = build_fallback_risk_array("OPTIDX", 22000, "PE", 22000, 120, 25, 1000, 0.04)
        upside_loss = arr[10]   # scenario 11: +PSR
        assert upside_loss > 0  # long put loses when price rises
