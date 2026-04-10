"""
Fallback margin rates used when the official NSE SPAN file is unavailable.

These are well-known approximate values based on historical NSE circulars.
The UI clearly labels calculations performed with these as "Estimated".
"""

from config import Config

# PSR as fraction of underlying price
_PSR_RATES: dict[str, float] = Config.FALLBACK_PSR_RATES


def _psr_rate(commodity_code: str) -> float:
    return _PSR_RATES.get(commodity_code, _PSR_RATES["__STOCK__"])


def get_fallback_commodity(
    commodity_code: str,
    underlying_price: float,
    is_index: bool,
) -> dict:
    """
    Return approximate CombinedCommodity parameters for *commodity_code*.

    PSR = price_scan_range in INR (PSR_rate × underlying_price).
    VSR = volatility_scan_range as a fraction (typical: 0.04 for indices, 0.06 for stocks).
    SOMC = short_option_minimum_charge (rough estimate: 1.5% × underlying × lot as a per-unit figure).
    """
    psr_rate = _psr_rate(commodity_code)
    psr = psr_rate * underlying_price if underlying_price else 0.0

    vsr = 0.04 if is_index else 0.06
    exposure_rate = 0.03 if is_index else 0.05

    # Short option min charge: approx 1.5% of underlying per lot-unit
    somc = 0.015 * underlying_price if underlying_price else 0.0

    return {
        "price_scan_range": psr,
        "volatility_scan_range": vsr,
        "exposure_margin_rate": exposure_rate,
        "short_option_min_charge": somc,
    }


def build_fallback_risk_array(
    instrument_type: str,
    strike_price: float | None,
    option_type: str | None,
    underlying_price: float,
    future_price: float,
    lot_size: int,
    psr: float,
    vsr: float,
) -> list[float]:
    """
    Compute approximate 16-scenario loss values for a single lot.

    For futures: loss is linear (price_move × lot_size).
    For options: use intrinsic-value approximation with a delta proxy.
    Values are per lot (loss is positive).

    The 16 scenarios:
      Scenarios 1-14: price_moves × vol_moves (7 price levels × 2 vol levels)
      Scenario 15: +2×PSR price move (extreme, 35% cover applied externally)
      Scenario 16: -2×PSR price move (extreme, 35% cover applied externally)
    """
    # Price moves as fraction of underlying
    price_fracs = [0, 0, +1/3, +1/3, -1/3, -1/3,
                   +2/3, +2/3, -2/3, -2/3, +1, +1, -1, -1]
    # Vol moves (not used in intrinsic approximation, kept for structure)
    # vol_moves = [+1, -1, +1, -1, +1, -1, +1, -1, +1, -1, +1, -1, +1, -1]

    scenarios = []
    ref = future_price if future_price else underlying_price

    for frac in price_fracs:
        price_move = psr * frac        # INR move in underlying
        new_price  = ref + price_move

        if instrument_type in ("FUTIDX", "FUTSTK"):
            loss = -price_move         # long future gains when price rises
        else:
            # Option: approximate using delta × price_move
            delta = _option_delta(instrument_type, option_type, ref, strike_price, psr, vsr)
            loss = -delta * price_move

        scenarios.append(loss)

    # Extreme scenarios (15 and 16)
    extreme_up   = -_lot_pnl(instrument_type, option_type, ref, strike_price, +2 * psr, psr, vsr)
    extreme_down = -_lot_pnl(instrument_type, option_type, ref, strike_price, -2 * psr, psr, vsr)
    scenarios.append(extreme_up)
    scenarios.append(extreme_down)

    return scenarios  # 16 values, per-unit (not per-lot)


def _option_delta(instr_type: str, opt_type: str | None,
                  ref: float, strike: float | None, psr: float, vsr: float) -> float:
    """Simple delta approximation based on moneyness."""
    if instr_type in ("FUTIDX", "FUTSTK") or opt_type is None or strike is None:
        return 1.0   # futures: delta = 1

    moneyness = (ref - strike) / ref if ref else 0
    if opt_type == "CE":
        # ATM CE delta ≈ 0.5; ITM → 1.0, OTM → 0.0
        return max(0.0, min(1.0, 0.5 + moneyness * 5))
    else:  # PE
        return max(-1.0, min(0.0, -0.5 + moneyness * 5))


def _lot_pnl(instr_type: str, opt_type: str | None,
             ref: float, strike: float | None, price_move: float,
             psr: float, vsr: float) -> float:
    """P&L for one lot (positive = gain)."""
    delta = _option_delta(instr_type, opt_type, ref, strike, psr, vsr)
    return delta * price_move
