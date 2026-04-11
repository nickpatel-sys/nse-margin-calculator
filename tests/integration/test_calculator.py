"""
Integration tests for backend/margin/calculator.py.

These tests create real DB records (in-memory SQLite) and call
calculate_portfolio_margin() end-to-end.

Risk array conventions (per lot, from LONG perspective):
  Positive value  = loss for long
  Negative value  = gain for long

Fixture PSR = 1000 per lot for NIFTY, 2000 for BANKNIFTY.
SOMC = 50 per contract.
"""

from datetime import date

import pytest

from backend.margin.calculator import PositionRequest, calculate_portfolio_margin
from tests.conftest import TRADE_DATE


# ── Helpers ───────────────────────────────────────────────────────────────────

def _calc(app, positions):
    with app.app_context():
        return calculate_portfolio_margin(positions, TRADE_DATE)


# ── Empty portfolio ───────────────────────────────────────────────────────────

def test_empty_portfolio_returns_zero_margin(app):
    result = _calc(app, [])
    assert result.span_margin == 0.0
    assert result.exposure_margin == 0.0
    assert result.total_margin == 0.0


# ── Missing contract ──────────────────────────────────────────────────────────

def test_unknown_contract_returns_error(app):
    req = [PositionRequest("DOES-NOT-EXIST", -1)]
    result = _calc(app, req)
    assert result.error is not None
    assert "not found" in result.error.lower()


# ── Single futures positions ──────────────────────────────────────────────────

def test_short_nifty_future_span_equals_psr(app, nifty_future_contract):
    """Worst-case loss for 1 short NIFTY lot = PSR = 1000."""
    req = [PositionRequest(nifty_future_contract, -1)]
    result = _calc(app, req)
    assert result.span_margin == pytest.approx(1000.0)


def test_long_nifty_future_span_equals_psr(app, nifty_future_contract):
    """Worst-case loss for 1 long NIFTY lot = PSR = 1000."""
    req = [PositionRequest(nifty_future_contract, 1)]
    result = _calc(app, req)
    assert result.span_margin == pytest.approx(1000.0)


def test_futures_exposure_margin(app, nifty_future_contract):
    """Exposure = 3% × lot_size × underlying × |lots|."""
    req = [PositionRequest(nifty_future_contract, -1)]
    result = _calc(app, req)
    expected_exposure = 0.03 * 25 * 22000.0 * 1
    assert result.exposure_margin == pytest.approx(expected_exposure)


def test_total_margin_is_span_plus_exposure(app, nifty_future_contract):
    req = [PositionRequest(nifty_future_contract, -1)]
    result = _calc(app, req)
    assert result.total_margin == pytest.approx(result.span_margin + result.exposure_margin)


def test_two_lots_doubles_span(app, nifty_future_contract):
    req = [PositionRequest(nifty_future_contract, -2)]
    result = _calc(app, req)
    assert result.span_margin == pytest.approx(2000.0)


def test_perfectly_hedged_position_has_zero_span(app, nifty_future_contract):
    """Long 1 + Short 1 same contract = net zero scenario losses."""
    req = [
        PositionRequest(nifty_future_contract, 1),
        PositionRequest(nifty_future_contract, -1),
    ]
    result = _calc(app, req)
    assert result.span_margin == pytest.approx(0.0)


# ── Options positions ─────────────────────────────────────────────────────────

def test_long_option_exposure_is_zero(app, nifty_ce_contract):
    """Options buyer pays no exposure margin."""
    req = [PositionRequest(nifty_ce_contract, 1)]
    result = _calc(app, req)
    assert result.exposure_margin == pytest.approx(0.0)


def test_short_option_exposure_is_nonzero(app, nifty_ce_contract):
    """Short option writer pays exposure margin."""
    req = [PositionRequest(nifty_ce_contract, -1)]
    result = _calc(app, req)
    expected_exposure = 0.03 * 25 * 22000.0
    assert result.exposure_margin == pytest.approx(expected_exposure)


def test_short_option_minimum_charge_applied(app, nifty_ce_contract):
    """
    With SOMC = 50/contract, lot_size = 25:
    short_option_min = 1 × 25 × 50 = 1250.
    If scan_risk < 1250, commodity_span = 1250.
    """
    req = [PositionRequest(nifty_ce_contract, -1)]
    result = _calc(app, req)
    comm = result.by_commodity[0]
    assert comm.commodity_span >= comm.short_option_min
    assert comm.short_option_min == pytest.approx(25 * 50.0)  # 1 lot × lot_size × SOMC


def test_short_strangle_span_less_than_two_naked_options(
        app, nifty_ce_contract, nifty_pe_contract):
    """
    A short strangle (short CE + short PE) benefits from netting:
    the CE and PE have partially opposite scenario P&Ls so scan_risk
    for the combined portfolio is less than the sum of each leg alone.
    (The final SPAN margin may be floored to SOMC, so we check scan_risk.)
    """
    req_strangle = [
        PositionRequest(nifty_ce_contract, -1),
        PositionRequest(nifty_pe_contract, -1),
    ]
    req_ce_only = [PositionRequest(nifty_ce_contract, -1)]
    req_pe_only = [PositionRequest(nifty_pe_contract, -1)]

    strangle_result = _calc(app, req_strangle)
    ce_result = _calc(app, req_ce_only)
    pe_result = _calc(app, req_pe_only)

    strangle_scan = strangle_result.by_commodity[0].scan_risk
    ce_scan = ce_result.by_commodity[0].scan_risk
    pe_scan = pe_result.by_commodity[0].scan_risk
    assert strangle_scan < ce_scan + pe_scan


def test_premium_received_for_short_options(app, nifty_ce_contract):
    """Premium received = lots × lot_size × future_price."""
    req = [PositionRequest(nifty_ce_contract, -1)]
    result = _calc(app, req)
    # future_price (premium) = 150 per contract
    expected_premium = 1 * 25 * 150.0
    assert result.premium_received == pytest.approx(expected_premium)


def test_no_premium_for_long_options(app, nifty_ce_contract):
    """Buyer does not receive premium; premium_received should be 0."""
    req = [PositionRequest(nifty_ce_contract, 1)]
    result = _calc(app, req)
    assert result.premium_received == pytest.approx(0.0)


# ── Per-position breakdown ───────────────────────────────────────────────────

def test_by_position_has_one_entry_per_leg(app, nifty_future_contract, nifty_ce_contract):
    req = [
        PositionRequest(nifty_future_contract, -1),
        PositionRequest(nifty_ce_contract, -1),
    ]
    result = _calc(app, req)
    assert len(result.by_position) == 2


def test_position_result_fields(app, nifty_future_contract):
    req = [PositionRequest(nifty_future_contract, -2)]
    result = _calc(app, req)
    pos = result.by_position[0]
    assert pos.symbol == "NIFTY"
    assert pos.instrument_type == "FUTIDX"
    assert pos.side == "sell"
    assert pos.lots == 2
    assert pos.lot_size == 25
    assert pos.underlying_price == pytest.approx(22000.0)
    assert pos.position_type == "short_future"
    assert pos.data_mode == "span_file"


def test_notional_value_calculation(app, nifty_future_contract):
    req = [PositionRequest(nifty_future_contract, -3)]
    result = _calc(app, req)
    pos = result.by_position[0]
    expected_notional = 3 * 25 * 22000.0
    assert pos.notional_value == pytest.approx(expected_notional)


# ── Per-commodity breakdown ───────────────────────────────────────────────────

def test_by_commodity_groups_same_underlying(app, nifty_ce_contract, nifty_pe_contract):
    req = [
        PositionRequest(nifty_ce_contract, -1),
        PositionRequest(nifty_pe_contract, -1),
    ]
    result = _calc(app, req)
    # Both are NIFTY → should be in a single commodity group
    assert len(result.by_commodity) == 1
    assert result.by_commodity[0].commodity == "NIFTY"


def test_two_underlyings_give_two_commodity_rows(
        app, nifty_future_contract, banknifty_future_contract):
    req = [
        PositionRequest(nifty_future_contract, -1),
        PositionRequest(banknifty_future_contract, -1),
    ]
    result = _calc(app, req)
    commodities = {c.commodity for c in result.by_commodity}
    assert "NIFTY" in commodities
    assert "BANKNIFTY" in commodities


# ── Data mode ────────────────────────────────────────────────────────────────

def test_data_mode_span_file_when_risk_arrays_exist(app, nifty_future_contract):
    req = [PositionRequest(nifty_future_contract, -1)]
    result = _calc(app, req)
    assert result.data_mode == "span_file"


# ── Variation margin ──────────────────────────────────────────────────────────

def test_futures_vm_gain_when_price_rises_for_long(app, nifty_future_contract):
    """Long future, today settle (22050) > prev (21000) → VM positive (gain)."""
    req = [PositionRequest(nifty_future_contract, 1, prev_settlement=21000.0)]
    result = _calc(app, req)
    # VM = +1 lot × 25 × (22050 - 21000) = 26250
    assert result.variation_margin == pytest.approx(1 * 25 * (22050.0 - 21000.0))
    assert result.net_cash_required == pytest.approx(result.total_margin - result.variation_margin)
    assert result.net_cash_required < result.total_margin


def test_futures_vm_loss_when_price_falls_for_long(app, nifty_future_contract):
    """Long future, today settle (22050) < prev (23000) → VM negative (loss)."""
    req = [PositionRequest(nifty_future_contract, 1, prev_settlement=23000.0)]
    result = _calc(app, req)
    # VM = +1 × 25 × (22050 - 23000) = -23750 (loss)
    assert result.variation_margin == pytest.approx(1 * 25 * (22050.0 - 23000.0))
    assert result.net_cash_required > result.total_margin


def test_short_future_vm_gain_when_price_falls(app, nifty_future_contract):
    """Short future, today settle (22050) < prev (23000) → VM positive (gain for short)."""
    req = [PositionRequest(nifty_future_contract, -1, prev_settlement=23000.0)]
    result = _calc(app, req)
    # VM = -1 × 25 × (22050 - 23000) = +23750 (gain for short)
    assert result.variation_margin == pytest.approx((-1) * 25 * (22050.0 - 23000.0))
    assert result.variation_margin > 0
    assert result.net_cash_required < result.total_margin


def test_options_have_no_variation_margin(app, nifty_ce_contract):
    """Option positions have variation_margin = None (no daily VM)."""
    req = [PositionRequest(nifty_ce_contract, -1, prev_settlement=100.0)]
    result = _calc(app, req)
    assert result.by_position[0].variation_margin is None
    assert result.variation_margin == pytest.approx(0.0)
    assert result.net_cash_required == pytest.approx(result.total_margin)


def test_zero_prev_settlement_gives_zero_vm(app, nifty_future_contract):
    """When prev_settlement is 0 (default / not provided), VM is 0."""
    req = [PositionRequest(nifty_future_contract, -1)]  # no prev_settlement
    result = _calc(app, req)
    assert result.variation_margin == pytest.approx(0.0)
    assert result.net_cash_required == pytest.approx(result.total_margin)


def test_per_position_vm_is_in_by_position(app, nifty_future_contract):
    """PositionResult.variation_margin is computed and present for futures."""
    req = [PositionRequest(nifty_future_contract, 1, prev_settlement=22000.0)]
    result = _calc(app, req)
    pos = result.by_position[0]
    # VM = +1 × 25 × (22050 - 22000) = 1250
    assert pos.variation_margin == pytest.approx(1 * 25 * (22050.0 - 22000.0))
