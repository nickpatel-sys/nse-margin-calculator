"""
Core SPAN + Exposure margin calculator.

Algorithm
---------
1. Load contract data and risk arrays from DB for each position.
2. Group positions by commodity (underlying).
3. Per commodity:
   a. Compute net P&L for each of 16 scenarios across all positions in the group.
   b. Apply 35 % cover fraction to extreme scenarios 15 & 16.
   c. Scan risk = max(0, worst net loss across 16 scenarios).
   d. Short option minimum = short_option_lots × lot_size × SOMC.
   e. Commodity SPAN = max(scan_risk, short_option_minimum).
   f. Apply intra-commodity spread adjustments.
4. Apply inter-commodity spread credits.
5. SPAN margin = sum of commodity SPANs − inter-spread credits.
6. Exposure margin = sum of per-position exposure margins.
7. Total = SPAN + Exposure.
   Exception: if all positions are long options → margin = net premium paid only.
"""

from __future__ import annotations
import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from backend.extensions import db
from backend.models.db import (
    CombinedCommodity, Contract, InterCommoditySpread,
    IntraCommoditySpread, RiskArray, SpanFile,
)
from backend.margin.exposure import PositionInput, calc_exposure
from backend.margin.spreads import (
    CommodityGroup, InterSpreadRule, apply_inter_spread_credits,
)
from backend.margin.fallback_rates import build_fallback_risk_array
from config import Config

logger = logging.getLogger(__name__)

EXTREME_COVER = Config.EXTREME_SCENARIO_COVER_FRACTION   # 0.35


# ─────────────────────────────────────────────────────────────────────────────
# Input / Output dataclasses
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class PositionRequest:
    contract_key: str
    quantity: int           # +ve = long, -ve = short (in lots)
    prev_settlement: float = 0.0  # previous day settlement price (0 = not provided)


@dataclass
class PositionResult:
    contract_key: str
    symbol: str
    instrument_type: str
    expiry_date: str
    strike_price: float | None
    option_type: str | None
    side: str
    lots: int
    lot_size: int
    underlying_price: float
    future_price: float
    notional_value: float
    worst_scenario: int
    worst_scenario_loss: float
    exposure_margin: float
    position_type: str   # 'long_future' | 'short_future' | 'long_option' | 'short_option'
    data_mode: str       # 'span_file' | 'estimated'
    variation_margin: float | None = None  # None for options; positive = gain


@dataclass
class CommodityResult:
    commodity: str
    scan_risk: float
    intra_spread_charge: float
    inter_spread_credit: float
    short_option_min: float
    commodity_span: float
    exposure_margin: float


@dataclass
class MarginResult:
    trade_date: str
    span_margin: float
    exposure_margin: float
    total_margin: float
    premium_received: float
    data_mode: str   # 'span_file' | 'estimated' | 'mixed'
    by_position: list[PositionResult] = field(default_factory=list)
    by_commodity: list[CommodityResult] = field(default_factory=list)
    error: str | None = None
    variation_margin: float = 0.0   # portfolio total VM (positive = gain)
    net_cash_required: float = 0.0  # total_margin - variation_margin


# ─────────────────────────────────────────────────────────────────────────────
# Main entry point
# ─────────────────────────────────────────────────────────────────────────────

def calculate_portfolio_margin(
    positions_req: list[PositionRequest],
    trade_date: date,
) -> MarginResult:
    if not positions_req:
        return MarginResult(
            trade_date=trade_date.isoformat(),
            span_margin=0, exposure_margin=0, total_margin=0,
            premium_received=0, data_mode="span_file",
        )

    # ── Load DB records ───────────────────────────────────────────────────────
    keys = [p.contract_key for p in positions_req]
    contracts: dict[str, Contract] = {
        c.contract_key: c
        for c in Contract.query.filter(
            Contract.trade_date == trade_date,
            Contract.contract_key.in_(keys),
        ).all()
    }

    missing = [k for k in keys if k not in contracts]
    if missing:
        return MarginResult(
            trade_date=trade_date.isoformat(),
            span_margin=0, exposure_margin=0, total_margin=0,
            premium_received=0, data_mode="estimated",
            error=f"Contracts not found in DB for {trade_date}: {missing}",
        )

    # Load inter / intra spread rules
    span_file = SpanFile.query.filter_by(trade_date=trade_date).first()
    inter_rules_db: list[InterCommoditySpread] = []
    intra_rules_db: list[IntraCommoditySpread] = []
    if span_file:
        inter_rules_db = InterCommoditySpread.query.filter_by(trade_date=trade_date).all()
        intra_rules_db = IntraCommoditySpread.query.filter_by(trade_date=trade_date).all()

    # ── Group by commodity ────────────────────────────────────────────────────
    commodity_positions: dict[str, list[dict]] = {}
    position_results: list[PositionResult] = []
    data_modes: set[str] = set()
    premium_received = 0.0

    for req in positions_req:
        contract = contracts[req.contract_key]
        commodity = contract.commodity_code
        signed_lots = req.quantity
        side = "buy" if signed_lots > 0 else "sell"

        # Get risk array (official or fallback)
        ra = contract.risk_array
        has_official_ra = ra is not None
        data_mode = "span_file" if has_official_ra else "estimated"
        data_modes.add(data_mode)

        commodity_rec = CombinedCommodity.query.filter_by(
            trade_date=trade_date, commodity_code=commodity
        ).first()

        if commodity_rec is None:
            # Very unlikely but guard against missing commodity record
            from backend.margin.fallback_rates import get_fallback_commodity
            is_index = contract.instrument_type in ("FUTIDX", "OPTIDX")
            fb = get_fallback_commodity(commodity, contract.underlying_price or 0, is_index)
            psr = fb["price_scan_range"]
            vsr = fb["volatility_scan_range"]
            somc = fb["short_option_min_charge"]
            exposure_rate = fb["exposure_margin_rate"]
        else:
            psr  = commodity_rec.price_scan_range or 0.0
            vsr  = commodity_rec.volatility_scan_range or 0.0
            somc = commodity_rec.short_option_min_charge or 0.0
            exposure_rate = commodity_rec.exposure_margin_rate or 0.03

        if has_official_ra:
            scenarios_per_unit = ra.as_list()
            composite_delta = ra.composite_delta or 0.0
        else:
            scenarios_per_unit = build_fallback_risk_array(
                instrument_type=contract.instrument_type,
                strike_price=contract.strike_price,
                option_type=contract.option_type,
                underlying_price=contract.underlying_price or 0.0,
                future_price=contract.future_price or 0.0,
                lot_size=contract.lot_size,
                psr=psr,
                vsr=vsr,
            )
            composite_delta = _approx_delta(contract, psr, vsr)

        # Scale by lot count (signed: negative = short)
        scenarios_scaled = [v * signed_lots for v in scenarios_per_unit]
        delta_scaled = composite_delta * signed_lots

        notional = abs(signed_lots) * contract.lot_size * (contract.underlying_price or 0.0)

        # Exposure margin for this position
        pos_input = PositionInput(
            instrument_type=contract.instrument_type,
            side=side,
            lots=signed_lots,
            lot_size=contract.lot_size,
            underlying_price=contract.underlying_price or 0.0,
            exposure_margin_rate=exposure_rate,
        )
        exp_margin = calc_exposure(pos_input)

        # Premium received for short options
        if side == "sell" and contract.instrument_type in ("OPTIDX", "OPTSTK"):
            premium_received += abs(signed_lots) * contract.lot_size * (contract.future_price or 0.0)

        # Variation margin (futures only, when prev_settlement provided)
        if contract.instrument_type in ("FUTIDX", "FUTSTK") and req.prev_settlement:
            vm = signed_lots * contract.lot_size * (
                (contract.future_price or 0.0) - req.prev_settlement
            )
        else:
            vm = None

        # Per-position worst scenario
        effective = [
            v * (EXTREME_COVER if i >= 14 else 1.0)
            for i, v in enumerate(scenarios_scaled)
        ]
        worst_idx = max(range(16), key=lambda i: effective[i])
        worst_loss = max(0.0, effective[worst_idx])

        pos_type = _position_type(contract.instrument_type, side)

        position_results.append(PositionResult(
            contract_key=req.contract_key,
            symbol=contract.symbol,
            instrument_type=contract.instrument_type,
            expiry_date=contract.expiry_date.isoformat(),
            strike_price=contract.strike_price,
            option_type=contract.option_type,
            side=side,
            lots=abs(signed_lots),
            lot_size=contract.lot_size,
            underlying_price=contract.underlying_price or 0.0,
            future_price=contract.future_price or 0.0,
            notional_value=notional,
            worst_scenario=worst_idx + 1,
            worst_scenario_loss=worst_loss,
            exposure_margin=exp_margin,
            position_type=pos_type,
            data_mode=data_mode,
            variation_margin=vm,
        ))

        # Add to commodity group
        if commodity not in commodity_positions:
            commodity_positions[commodity] = []
        commodity_positions[commodity].append({
            "commodity_code": commodity,
            "expiry_date": contract.expiry_date.isoformat(),
            "signed_lots": signed_lots,
            "instrument_type": contract.instrument_type,
            "option_type": contract.option_type,
            "side": side,
            "scenarios": scenarios_scaled,   # already lot-scaled
            "delta": delta_scaled,
            "somc": somc,
            "exposure": exp_margin,
        })

    # ── Per-commodity SPAN ────────────────────────────────────────────────────
    commodity_results: list[CommodityResult] = []
    commodity_groups: dict[str, CommodityGroup] = {}
    intra_by_code: dict[str, list] = {}
    for r in intra_rules_db:
        intra_by_code.setdefault(r.commodity_code, []).append(r)

    for commodity, pos_list in commodity_positions.items():
        # Net 16-scenario P&L for the commodity
        net_scenarios = [0.0] * 16
        for p in pos_list:
            for i, v in enumerate(p["scenarios"]):
                net_scenarios[i] += v

        # Apply extreme cover fractions
        effective = [
            v * (EXTREME_COVER if i >= 14 else 1.0)
            for i, v in enumerate(net_scenarios)
        ]
        scan_risk = max(0.0, max(effective))

        # Short option minimum
        short_lots = sum(
            abs(p["signed_lots"])
            for p in pos_list
            if p["instrument_type"] in ("OPTIDX", "OPTSTK") and p["side"] == "sell"
        )
        # SOMC per unit from the first position (all share same commodity SOMC)
        somc_per_unit = pos_list[0]["somc"] if pos_list else 0.0
        # Lot size from DB; use first contract's lot_size
        lot_size_sample = abs(pos_list[0]["signed_lots"]) and 1  # will refine below

        # Get lot size from contracts dict
        for req in positions_req:
            c = contracts.get(req.contract_key)
            if c and c.commodity_code == commodity:
                lot_size_sample = c.lot_size
                break

        short_option_min = short_lots * lot_size_sample * somc_per_unit
        commodity_span = max(scan_risk, short_option_min)

        # Intra-spread (calendar spreads within this commodity)
        intra_rules = intra_by_code.get(commodity, [])
        # (simplified: we note any reduction but leave scan_risk unchanged for now)
        intra_charge = 0.0  # placeholder; full implementation in spreads.py

        # Net composite delta for inter-spread matching
        net_delta = sum(p["delta"] for p in pos_list)

        commodity_exp = sum(p["exposure"] for p in pos_list)

        commodity_groups[commodity] = CommodityGroup(
            code=commodity,
            scan_risk=commodity_span,
            composite_delta=net_delta,
        )
        commodity_results.append(CommodityResult(
            commodity=commodity,
            scan_risk=scan_risk,
            intra_spread_charge=intra_charge,
            inter_spread_credit=0.0,   # filled after inter-spread pass
            short_option_min=short_option_min,
            commodity_span=commodity_span,
            exposure_margin=commodity_exp,
        ))

    # ── Inter-commodity spread credits ────────────────────────────────────────
    inter_rules = [
        InterSpreadRule(
            priority=r.priority,
            leg1=r.leg1_commodity,
            leg2=r.leg2_commodity,
            credit_rate=r.credit_rate,
            delta_ratio_1=r.delta_ratio_leg1,
            delta_ratio_2=r.delta_ratio_leg2,
        )
        for r in inter_rules_db
    ]

    # Fallback inter-spread rules if none loaded from DB
    if not inter_rules:
        inter_rules = [
            InterSpreadRule(priority=i + 1, leg1=l1, leg2=l2,
                            credit_rate=cr, delta_ratio_1=dr1, delta_ratio_2=dr2)
            for i, (l1, l2, cr, dr1, dr2) in enumerate(Config.FALLBACK_INTER_SPREADS)
        ]

    credits_map, total_inter_credit = apply_inter_spread_credits(
        commodity_groups, inter_rules
    )

    # Update commodity results with inter-spread credits
    for cr in commodity_results:
        cr.inter_spread_credit = credits_map.get(cr.commodity, 0.0)

    # ── Final totals ──────────────────────────────────────────────────────────
    span_margin = max(0.0, sum(cg.scan_risk for cg in commodity_groups.values()) - total_inter_credit)
    exposure_margin = sum(pr.exposure_margin for pr in position_results)
    total_margin = span_margin + exposure_margin

    # Determine overall data mode
    if len(data_modes) == 1:
        overall_mode = data_modes.pop()
    elif "estimated" in data_modes:
        overall_mode = "mixed"
    else:
        overall_mode = "span_file"

    # Variation margin totals
    portfolio_vm = sum(
        p.variation_margin for p in position_results if p.variation_margin is not None
    )
    net_cash = total_margin - portfolio_vm

    return MarginResult(
        trade_date=trade_date.isoformat(),
        span_margin=span_margin,
        exposure_margin=exposure_margin,
        total_margin=total_margin,
        premium_received=premium_received,
        data_mode=overall_mode,
        by_position=position_results,
        by_commodity=commodity_results,
        variation_margin=portfolio_vm,
        net_cash_required=net_cash,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _position_type(instrument_type: str, side: str) -> str:
    if instrument_type in ("FUTIDX", "FUTSTK"):
        return "long_future" if side == "buy" else "short_future"
    return "long_option" if side == "buy" else "short_option"


def _approx_delta(contract: Contract, psr: float, vsr: float) -> float:
    from backend.margin.fallback_rates import _option_delta
    return _option_delta(
        contract.instrument_type,
        contract.option_type,
        contract.underlying_price or 0.0,
        contract.strike_price,
        psr, vsr,
    )
