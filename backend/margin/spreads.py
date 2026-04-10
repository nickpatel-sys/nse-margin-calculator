"""
Inter- and intra-commodity spread credit / charge logic.

Inter-commodity spread credits reduce total SPAN margin when
correlated positions exist across different underlyings (e.g. long
Nifty futures hedged against short BankNifty futures).

Intra-commodity (calendar) spread charges replace the full scan-risk
charge with a lower calendar-spread charge when opposite-month legs exist.
"""

from __future__ import annotations
import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class CommodityGroup:
    code: str
    scan_risk: float       # worst-case loss after intra-spread
    composite_delta: float = 0.0   # net delta in lot-equivalents


@dataclass
class InterSpreadRule:
    priority: int
    leg1: str
    leg2: str
    credit_rate: float
    delta_ratio_1: float = 1.0
    delta_ratio_2: float = 1.0


def apply_intra_spread_charges(
    commodity_code: str,
    scan_risk: float,
    positions: list,   # list of position dicts from calculator
    intra_rules: list,
) -> float:
    """
    If calendar spread pairs exist within the same underlying, replace
    the scan risk for those paired lots with the spread charge.

    Simplified implementation: detect when both long and short legs exist
    for the same underlying across different expiries, then apply the
    spread charge rate to the paired lots.
    """
    if not intra_rules:
        return scan_risk

    # Separate near-month long and far-month short (or vice versa) by expiry
    expiry_lots: dict[str, int] = {}
    for p in positions:
        if p.get("commodity_code") != commodity_code:
            continue
        expiry = p.get("expiry_date")
        lots = p.get("signed_lots", 0)
        expiry_lots[expiry] = expiry_lots.get(expiry, 0) + lots

    # Count spread pairs (opposite-sign expiry pairs)
    expiries = sorted(expiry_lots.keys())
    spread_pairs = 0
    for i in range(len(expiries) - 1):
        near_lots = expiry_lots[expiries[i]]
        far_lots  = expiry_lots[expiries[i + 1]]
        # A calendar spread: opposite signs
        if near_lots * far_lots < 0:
            spread_pairs += min(abs(near_lots), abs(far_lots))

    if spread_pairs == 0:
        return scan_risk

    # Use the lowest-priority (most restrictive) spread charge rate
    spread_rate = min((r.spread_charge_rate for r in intra_rules), default=0.0)
    spread_charge = spread_pairs * spread_rate * scan_risk
    # The spread charge replaces that portion of scan risk (usually lower)
    return max(0.0, scan_risk - max(0.0, scan_risk - spread_charge))


def apply_inter_spread_credits(
    commodity_groups: dict[str, CommodityGroup],
    inter_rules: list[InterSpreadRule],
) -> tuple[dict[str, float], float]:
    """
    Apply inter-commodity spread credits.

    Works through rules sorted by priority. For each rule, checks if
    both leg1 and leg2 commodities have opposite-sign deltas (hedged).
    Credits reduce the combined scan risk.

    Returns (credits_per_commodity, total_credit_amount).
    """
    credits: dict[str, float] = {code: 0.0 for code in commodity_groups}
    total_credit = 0.0

    sorted_rules = sorted(inter_rules, key=lambda r: r.priority)

    for rule in sorted_rules:
        g1 = commodity_groups.get(rule.leg1)
        g2 = commodity_groups.get(rule.leg2)
        if g1 is None or g2 is None:
            continue

        delta1 = g1.composite_delta
        delta2 = g2.composite_delta

        # Spread requires opposite-sign deltas
        if delta1 * delta2 >= 0:
            continue

        # Number of spread units: min of available paired delta lots
        spread_units = min(
            abs(delta1) / rule.delta_ratio_1,
            abs(delta2) / rule.delta_ratio_2,
        )

        # Credit = spread_units × credit_rate × min(scan_risk_leg1, scan_risk_leg2)
        ref_scan_risk = min(
            g1.scan_risk - credits.get(rule.leg1, 0.0),
            g2.scan_risk - credits.get(rule.leg2, 0.0),
        )
        credit_amount = spread_units * rule.credit_rate * ref_scan_risk
        credit_amount = max(0.0, credit_amount)

        # Credits cannot exceed each commodity's remaining scan risk
        credit_per_leg = credit_amount / 2
        credit_per_leg = min(
            credit_per_leg,
            g1.scan_risk - credits.get(rule.leg1, 0.0),
        )
        credit_per_leg = min(
            credit_per_leg,
            g2.scan_risk - credits.get(rule.leg2, 0.0),
        )

        if credit_per_leg > 0:
            credits[rule.leg1] = credits.get(rule.leg1, 0.0) + credit_per_leg
            credits[rule.leg2] = credits.get(rule.leg2, 0.0) + credit_per_leg
            total_credit += credit_per_leg * 2
            logger.debug(
                "Inter-spread credit: %s↔%s = ₹%.0f",
                rule.leg1, rule.leg2, credit_per_leg * 2
            )

    return credits, total_credit
