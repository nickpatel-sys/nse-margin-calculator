"""
Integration tests for the fallback (estimated) margin calculation path.

These tests validate the margin engine when operating WITHOUT an official NSE
SPAN file — i.e., the bhavcopy-only mode used in production today.

Fixtures deliberately omit RiskArray rows so the engine falls back to
build_fallback_risk_array().  Market parameters mirror April 2026 actuals:

    NIFTY      underlying 24 000, lot_size 65, PSR_rate 0.08
    BANKNIFTY  underlying 56 000, lot_size 30, PSR_rate 0.09

Key invariants (revealed by Zerodha comparison April 2026):
  1. SPAN scales with lot_size — 1 NIFTY lot ≈ 65× a single contract
  2. Exposure margin = 2% of notional for index derivatives
  3. Long option buyers pay no SPAN margin (total = 0)
  4. Two lots doubles SPAN exactly
  5. Short strangle SPAN < sum of two naked options (netting benefit)
  6. Same-direction cross-commodity positions receive no inter-spread credit

The exact SPAN values will differ from Zerodha (~8–15% below) because the
fallback PSR rates are conservative approximations.  Tolerance tests document
the expected accuracy band relative to Zerodha's live figures.
"""

from datetime import date, datetime

import pytest

from backend.margin.calculator import PositionRequest, calculate_portfolio_margin


# ── Realistic reference values ────────────────────────────────────────────────

TRADE_DATE = date(2026, 4, 10)

_NIFTY_UNDERLYING  = 24_000.0
_NIFTY_LOT         = 65
_NIFTY_PSR_RATE    = 0.08               # from FALLBACK_PSR_RATES
_NIFTY_PSR         = _NIFTY_PSR_RATE * _NIFTY_UNDERLYING   # 1 920 per contract
_NIFTY_SPAN_1LOT   = _NIFTY_PSR * _NIFTY_LOT               # 124 800 per lot

_BNF_UNDERLYING    = 56_000.0
_BNF_LOT           = 30
_BNF_PSR_RATE      = 0.09
_BNF_PSR           = _BNF_PSR_RATE * _BNF_UNDERLYING       # 5 040 per contract
_BNF_SPAN_1LOT     = _BNF_PSR * _BNF_LOT                   # 151 200 per lot

_IDX_EXP_RATE      = 0.02               # NSE index exposure margin rate


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def est_span_file(app):
    with app.app_context():
        from backend.extensions import db
        from backend.models.db import SpanFile
        sf = SpanFile(
            trade_date=TRADE_DATE,
            file_type="udiff_bhavcopy",
            downloaded_at=datetime(2026, 4, 10, 19, 0, 0),
            parse_status="success",
        )
        db.session.add(sf)
        db.session.commit()
        yield sf.id


@pytest.fixture
def est_nifty_commodity(app, est_span_file):
    with app.app_context():
        from backend.extensions import db
        from backend.models.db import CombinedCommodity, SpanFile
        sf = db.session.get(SpanFile, est_span_file)
        cc = CombinedCommodity(
            span_file_id=sf.id,
            trade_date=TRADE_DATE,
            commodity_code="NIFTY",
            exchange_code="NSE",
            price_scan_range=_NIFTY_PSR,         # per contract
            volatility_scan_range=0.04,
            inter_month_spread_charge=0.0,
            short_option_min_charge=0.015 * _NIFTY_UNDERLYING,  # 360 per contract
            exposure_margin_rate=_IDX_EXP_RATE,
            instrument_type="INDEX",
            is_estimated=True,
        )
        db.session.add(cc)
        db.session.commit()
        yield cc.id


@pytest.fixture
def est_banknifty_commodity(app, est_span_file):
    with app.app_context():
        from backend.extensions import db
        from backend.models.db import CombinedCommodity, SpanFile
        sf = db.session.get(SpanFile, est_span_file)
        cc = CombinedCommodity(
            span_file_id=sf.id,
            trade_date=TRADE_DATE,
            commodity_code="BANKNIFTY",
            exchange_code="NSE",
            price_scan_range=_BNF_PSR,
            volatility_scan_range=0.04,
            inter_month_spread_charge=0.0,
            short_option_min_charge=0.015 * _BNF_UNDERLYING,
            exposure_margin_rate=_IDX_EXP_RATE,
            instrument_type="INDEX",
            is_estimated=True,
        )
        db.session.add(cc)
        db.session.commit()
        yield cc.id


def _make_contract(app, sf_id, commodity, symbol, instr_type,
                   lot_size, underlying, future_price,
                   strike=None, opt_type=None):
    """Create a Contract with NO RiskArray (triggers fallback path)."""
    from backend.extensions import db
    from backend.models.db import Contract, SpanFile
    expiry = date(2026, 4, 28)
    parts = [symbol, instr_type, expiry.strftime("%Y%m%d")]
    if strike is not None:
        parts.append(str(int(strike)))
    if opt_type:
        parts.append(opt_type)
    key = "-".join(parts)
    sf = db.session.get(SpanFile, sf_id)
    c = Contract(
        span_file_id=sf.id,
        trade_date=TRADE_DATE,
        commodity_code=commodity,
        symbol=symbol,
        instrument_type=instr_type,
        expiry_date=expiry,
        strike_price=strike,
        option_type=opt_type,
        lot_size=lot_size,
        underlying_price=underlying,
        future_price=future_price,
        contract_key=key,
    )
    db.session.add(c)
    db.session.commit()
    return key


@pytest.fixture
def est_nifty_future(app, est_span_file, est_nifty_commodity):
    with app.app_context():
        from backend.extensions import db
        yield _make_contract(app, est_span_file, "NIFTY", "NIFTY", "FUTIDX",
                             _NIFTY_LOT, _NIFTY_UNDERLYING, 24_101.0)


@pytest.fixture
def est_banknifty_future(app, est_span_file, est_banknifty_commodity):
    with app.app_context():
        from backend.extensions import db
        yield _make_contract(app, est_span_file, "BANKNIFTY", "BANKNIFTY", "FUTIDX",
                             _BNF_LOT, _BNF_UNDERLYING, 56_077.0)


@pytest.fixture
def est_nifty_ce(app, est_span_file, est_nifty_commodity):
    """ATM NIFTY CE: delta ≈ 0.5, underlying = strike = 24 000."""
    with app.app_context():
        yield _make_contract(app, est_span_file, "NIFTY", "NIFTY", "OPTIDX",
                             _NIFTY_LOT, _NIFTY_UNDERLYING, 432.6,
                             strike=24_000.0, opt_type="CE")


@pytest.fixture
def est_nifty_pe(app, est_span_file, est_nifty_commodity):
    """ATM NIFTY PE: delta ≈ -0.5, underlying = strike = 24 000."""
    with app.app_context():
        yield _make_contract(app, est_span_file, "NIFTY", "NIFTY", "OPTIDX",
                             _NIFTY_LOT, _NIFTY_UNDERLYING, 330.3,
                             strike=24_000.0, opt_type="PE")


# ── Helper ────────────────────────────────────────────────────────────────────

def _calc(app, positions):
    with app.app_context():
        return calculate_portfolio_margin(positions, TRADE_DATE)


# ── 1. SPAN scales with lot_size (fixes the missing × lot_size bug) ───────────

def test_estimated_future_span_includes_lot_size(app, est_nifty_future):
    """
    SPAN for 1 NIFTY lot must equal PSR × lot_size, not just PSR.

    Before the fix, build_fallback_risk_array returned per-contract values and
    the calculator multiplied only by signed_lots (1), giving SPAN = 1 920.
    After the fix it returns per-lot values: SPAN = 1 920 × 65 = 124 800.
    """
    req = [PositionRequest(est_nifty_future, -1)]
    result = _calc(app, req)
    assert result.span_margin == pytest.approx(_NIFTY_SPAN_1LOT)


def test_estimated_two_lots_doubles_span(app, est_nifty_future):
    req_1 = [PositionRequest(est_nifty_future, -1)]
    req_2 = [PositionRequest(est_nifty_future, -2)]
    span_1 = _calc(app, req_1).span_margin
    span_2 = _calc(app, req_2).span_margin
    assert span_2 == pytest.approx(2 * span_1)


def test_estimated_long_and_short_span_equal(app, est_nifty_future):
    """Futures SPAN is symmetric: long 1 lot = short 1 lot."""
    long_span  = _calc(app, [PositionRequest(est_nifty_future,  1)]).span_margin
    short_span = _calc(app, [PositionRequest(est_nifty_future, -1)]).span_margin
    assert long_span == pytest.approx(short_span)


# ── 2. Exposure margin = 2% of notional ──────────────────────────────────────

def test_estimated_future_exposure_is_two_percent(app, est_nifty_future):
    """Exposure = 2% × lot_size × underlying_price for index futures."""
    req = [PositionRequest(est_nifty_future, -1)]
    result = _calc(app, req)
    expected = _IDX_EXP_RATE * _NIFTY_LOT * _NIFTY_UNDERLYING
    assert result.exposure_margin == pytest.approx(expected)


def test_estimated_banknifty_exposure_is_two_percent(app, est_banknifty_future):
    req = [PositionRequest(est_banknifty_future, -1)]
    result = _calc(app, req)
    expected = _IDX_EXP_RATE * _BNF_LOT * _BNF_UNDERLYING
    assert result.exposure_margin == pytest.approx(expected)


def test_estimated_short_option_exposure_is_two_percent(app, est_nifty_ce):
    """Short index option writers also pay 2% exposure margin."""
    req = [PositionRequest(est_nifty_ce, -1)]
    result = _calc(app, req)
    expected = _IDX_EXP_RATE * _NIFTY_LOT * _NIFTY_UNDERLYING
    assert result.exposure_margin == pytest.approx(expected)


# ── 3. Long option SPAN = 0 ───────────────────────────────────────────────────

def test_estimated_long_option_span_is_zero(app, est_nifty_ce):
    """
    NSE rule: long option buyers post only the premium, no SPAN margin.
    The engine must zero scan_risk when all positions in a commodity are
    long options.
    """
    req = [PositionRequest(est_nifty_ce, 1)]
    result = _calc(app, req)
    assert result.span_margin == pytest.approx(0.0)
    assert result.exposure_margin == pytest.approx(0.0)
    assert result.total_margin == pytest.approx(0.0)


def test_estimated_long_put_span_is_zero(app, est_nifty_pe):
    req = [PositionRequest(est_nifty_pe, 1)]
    result = _calc(app, req)
    assert result.span_margin == pytest.approx(0.0)


def test_estimated_long_strangle_span_is_zero(app, est_nifty_ce, est_nifty_pe):
    """Long strangle (long CE + long PE) → SPAN = 0."""
    req = [PositionRequest(est_nifty_ce, 1), PositionRequest(est_nifty_pe, 1)]
    result = _calc(app, req)
    assert result.span_margin == pytest.approx(0.0)


def test_estimated_mixed_long_short_span_is_nonzero(app, est_nifty_ce, est_nifty_pe):
    """Short CE + long PE: short leg dominates, SPAN must not be zeroed out."""
    req = [PositionRequest(est_nifty_ce, -1), PositionRequest(est_nifty_pe, 1)]
    result = _calc(app, req)
    assert result.span_margin > 0.0


# ── 4. Short strangle netting ─────────────────────────────────────────────────

def test_estimated_short_strangle_scan_risk_less_than_naked_sum(
        app, est_nifty_ce, est_nifty_pe):
    """
    Short strangle (short CE + short PE): CE and PE lose value in opposite
    price directions, so combined scan_risk < sum of two naked positions.
    """
    combined = _calc(app, [PositionRequest(est_nifty_ce, -1),
                           PositionRequest(est_nifty_pe, -1)])
    ce_only  = _calc(app, [PositionRequest(est_nifty_ce, -1)])
    pe_only  = _calc(app, [PositionRequest(est_nifty_pe, -1)])

    combined_scan = combined.by_commodity[0].scan_risk
    ce_scan       = ce_only.by_commodity[0].scan_risk
    pe_scan       = pe_only.by_commodity[0].scan_risk
    assert combined_scan < ce_scan + pe_scan


# ── 5. Cross-commodity: same direction = no inter-spread credit ───────────────

def test_estimated_two_short_futures_no_inter_spread_credit(
        app, est_nifty_future, est_banknifty_future):
    """
    Short NIFTY + short BANKNIFTY are both directionally bearish.
    Zerodha confirms zero inter-spread credit for same-direction futures.
    Both commodities have negative delta, so the inter-spread check
    (delta1 × delta2 ≥ 0) correctly skips the credit.
    """
    combined  = _calc(app, [PositionRequest(est_nifty_future, -1),
                            PositionRequest(est_banknifty_future, -1)])
    nifty_only = _calc(app, [PositionRequest(est_nifty_future, -1)])
    bnf_only   = _calc(app, [PositionRequest(est_banknifty_future, -1)])

    assert combined.span_margin == pytest.approx(
        nifty_only.span_margin + bnf_only.span_margin
    )


# ── 6. Accuracy vs Zerodha ────────────────────────────────────────────────────

class TestZerodhaAccuracy:
    """
    Tolerance tests documenting the accuracy band of estimated-mode margins
    relative to Zerodha's live values (April 2026).

    Zerodha uses official NSE SPAN files; we use approximate PSR rates.
    Acceptable error: SPAN ±20%, Exposure ±5%.

    Reference (Zerodha, April 10 2026):
        Short 1 NIFTY Apr Fut:   SPAN 1,45,198   Exp 31,331  Total 1,76,529
        Short 1 BANKNIFTY Apr Fut: SPAN 1,59,758 Exp 33,646  Total 1,93,404
    """

    ZERODHA_NIFTY_SPAN    = 145_198
    ZERODHA_NIFTY_EXP     = 31_331
    ZERODHA_BNF_SPAN      = 159_758
    ZERODHA_BNF_EXP       = 33_646

    SPAN_TOLERANCE   = 0.20   # within 20% of Zerodha's SPAN
    EXP_TOLERANCE    = 0.05   # within 5% of Zerodha's exposure

    def test_nifty_future_span_within_tolerance(self, app, est_nifty_future):
        result = _calc(app, [PositionRequest(est_nifty_future, -1)])
        ratio = result.span_margin / self.ZERODHA_NIFTY_SPAN
        assert 1 - self.SPAN_TOLERANCE <= ratio <= 1 + self.SPAN_TOLERANCE, (
            f"NIFTY SPAN {result.span_margin:,.0f} is more than "
            f"{self.SPAN_TOLERANCE*100:.0f}% from Zerodha's {self.ZERODHA_NIFTY_SPAN:,}"
        )

    def test_nifty_future_exposure_within_tolerance(self, app, est_nifty_future):
        result = _calc(app, [PositionRequest(est_nifty_future, -1)])
        ratio = result.exposure_margin / self.ZERODHA_NIFTY_EXP
        assert 1 - self.EXP_TOLERANCE <= ratio <= 1 + self.EXP_TOLERANCE, (
            f"NIFTY Exp {result.exposure_margin:,.0f} is more than "
            f"{self.EXP_TOLERANCE*100:.0f}% from Zerodha's {self.ZERODHA_NIFTY_EXP:,}"
        )

    def test_banknifty_future_span_within_tolerance(self, app, est_banknifty_future):
        result = _calc(app, [PositionRequest(est_banknifty_future, -1)])
        ratio = result.span_margin / self.ZERODHA_BNF_SPAN
        assert 1 - self.SPAN_TOLERANCE <= ratio <= 1 + self.SPAN_TOLERANCE, (
            f"BANKNIFTY SPAN {result.span_margin:,.0f} is more than "
            f"{self.SPAN_TOLERANCE*100:.0f}% from Zerodha's {self.ZERODHA_BNF_SPAN:,}"
        )

    def test_banknifty_future_exposure_within_tolerance(self, app, est_banknifty_future):
        result = _calc(app, [PositionRequest(est_banknifty_future, -1)])
        ratio = result.exposure_margin / self.ZERODHA_BNF_EXP
        assert 1 - self.EXP_TOLERANCE <= ratio <= 1 + self.EXP_TOLERANCE, (
            f"BANKNIFTY Exp {result.exposure_margin:,.0f} is more than "
            f"{self.EXP_TOLERANCE*100:.0f}% from Zerodha's {self.ZERODHA_BNF_EXP:,}"
        )

    def test_long_option_total_is_zero_not_nonzero(self, app, est_nifty_ce):
        """Regression guard: long option must not charge any margin."""
        result = _calc(app, [PositionRequest(est_nifty_ce, 1)])
        assert result.total_margin == pytest.approx(0.0), (
            "Long option incorrectly charging margin — NSE rule violation"
        )
