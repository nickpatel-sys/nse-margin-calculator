"""
Exposure margin rules for NSE F&O.

NSE Circular reference: NSCCL/CMPT/46514 and subsequent updates.

Rules
-----
• Index derivatives (FUTIDX / OPTIDX): 3 % of notional value
• Stock derivatives (FUTSTK / OPTSTK): 5 % of notional value
• Options buyers: exposure margin = 0 (only premium is at risk)
• Calendar spreads: exposure on 1/3 of the far-month leg only
"""

from dataclasses import dataclass


@dataclass
class PositionInput:
    instrument_type: str   # FUTIDX | OPTIDX | FUTSTK | OPTSTK
    side: str              # 'buy' | 'sell'
    lots: int              # signed: positive = long, negative = short
    lot_size: int
    underlying_price: float
    exposure_margin_rate: float   # from CombinedCommodity (0.03 or 0.05)
    # For calendar spread detection (optional)
    is_near_month_leg: bool = False
    is_calendar_spread: bool = False


def calc_exposure(pos: PositionInput) -> float:
    """
    Return the exposure margin (in INR) for one position.

    Options buyers pay zero exposure margin.
    Calendar spread far-month leg uses 1/3 notional.
    """
    # Long options buyers: no exposure margin
    if pos.instrument_type in ("OPTIDX", "OPTSTK") and pos.side == "buy":
        return 0.0

    notional = abs(pos.lots) * pos.lot_size * pos.underlying_price

    if pos.is_calendar_spread and not pos.is_near_month_leg:
        # Far-month leg of a calendar spread: 1/3 exposure
        notional /= 3.0

    return pos.exposure_margin_rate * notional


def calc_portfolio_exposure(positions: list[PositionInput]) -> float:
    """Sum exposure margin across all positions."""
    return sum(calc_exposure(p) for p in positions)
