"""Unit tests for backend/margin/spreads.py."""

import pytest

from backend.margin.spreads import (
    CommodityGroup,
    InterSpreadRule,
    apply_inter_spread_credits,
    apply_intra_spread_charges,
)


class TestApplyIntraSpreadCharges:
    def _rule(self, rate):
        class R:
            spread_charge_rate = rate
        return R()

    def test_no_rules_returns_scan_risk_unchanged(self):
        result = apply_intra_spread_charges("NIFTY", 5000.0, [], [])
        assert result == pytest.approx(5000.0)

    def test_no_calendar_pairs_returns_scan_risk_unchanged(self):
        """Two positions in same expiry: no calendar spread."""
        positions = [
            {"commodity_code": "NIFTY", "expiry_date": "2026-01-29", "signed_lots": -1},
            {"commodity_code": "NIFTY", "expiry_date": "2026-01-29", "signed_lots": 1},
        ]
        rules = [self._rule(0.5)]
        result = apply_intra_spread_charges("NIFTY", 5000.0, positions, rules)
        # Same expiry → no opposite-sign pairing → unchanged
        assert result == pytest.approx(5000.0)

    def test_different_commodity_positions_ignored(self):
        """Positions from other commodities should not affect the charge."""
        positions = [
            {"commodity_code": "BANKNIFTY", "expiry_date": "2026-01-29", "signed_lots": -1},
            {"commodity_code": "BANKNIFTY", "expiry_date": "2026-02-26", "signed_lots": 1},
        ]
        rules = [self._rule(0.5)]
        result = apply_intra_spread_charges("NIFTY", 5000.0, positions, rules)
        assert result == pytest.approx(5000.0)


class TestApplyInterSpreadCredits:
    def _groups(self, **kwargs):
        return {k: CommodityGroup(code=k, scan_risk=v[0], composite_delta=v[1])
                for k, v in kwargs.items()}

    def _rule(self, leg1, leg2, credit_rate=0.5, priority=1, dr1=1.0, dr2=1.0):
        return InterSpreadRule(
            priority=priority, leg1=leg1, leg2=leg2,
            credit_rate=credit_rate, delta_ratio_1=dr1, delta_ratio_2=dr2
        )

    def test_no_rules_returns_zero_credit(self):
        groups = self._groups(NIFTY=(5000.0, 1.0))
        credits, total = apply_inter_spread_credits(groups, [])
        assert total == 0.0

    def test_missing_leg_gives_no_credit(self):
        """If one leg of the spread isn't in the portfolio, skip."""
        groups = self._groups(NIFTY=(5000.0, 1.0))
        rules = [self._rule("BANKNIFTY", "NIFTY")]
        credits, total = apply_inter_spread_credits(groups, rules)
        assert total == 0.0

    def test_same_sign_deltas_gives_no_credit(self):
        """Both legs long: no hedge → no credit."""
        groups = self._groups(BANKNIFTY=(2000.0, 1.0), NIFTY=(5000.0, 3.0))
        rules = [self._rule("BANKNIFTY", "NIFTY", dr1=1.0, dr2=3.0)]
        credits, total = apply_inter_spread_credits(groups, rules)
        assert total == 0.0

    def test_opposite_sign_deltas_generates_credit(self):
        """Long BANKNIFTY, short NIFTY (opposite deltas) → spread credit."""
        groups = self._groups(BANKNIFTY=(2000.0, 1.0), NIFTY=(5000.0, -3.0))
        rules = [self._rule("BANKNIFTY", "NIFTY", credit_rate=0.5, dr1=1.0, dr2=3.0)]
        credits, total = apply_inter_spread_credits(groups, rules)
        assert total > 0.0

    def test_credit_cannot_exceed_scan_risk(self):
        """Credits should be capped at each leg's remaining scan risk."""
        groups = self._groups(BANKNIFTY=(100.0, 1.0), NIFTY=(100.0, -1.0))
        rules = [self._rule("BANKNIFTY", "NIFTY", credit_rate=99.0)]
        credits, total = apply_inter_spread_credits(groups, rules)
        assert credits["BANKNIFTY"] <= 100.0
        assert credits["NIFTY"] <= 100.0

    def test_credit_keys_match_commodity_groups(self):
        """All commodity group keys should appear in credits dict."""
        groups = self._groups(BANKNIFTY=(2000.0, 1.0), NIFTY=(5000.0, -3.0))
        rules = [self._rule("BANKNIFTY", "NIFTY")]
        credits, _ = apply_inter_spread_credits(groups, rules)
        assert set(credits.keys()) == {"BANKNIFTY", "NIFTY"}

    def test_priority_ordering_lower_first(self):
        """Lower priority number is applied first."""
        groups = self._groups(A=(1000.0, 1.0), B=(1000.0, -1.0), C=(1000.0, 1.0))
        rule_high = InterSpreadRule(priority=10, leg1="A", leg2="B",
                                    credit_rate=0.5, delta_ratio_1=1.0, delta_ratio_2=1.0)
        rule_low  = InterSpreadRule(priority=1,  leg1="A", leg2="C",
                                    credit_rate=0.5, delta_ratio_1=1.0, delta_ratio_2=1.0)
        # priority=1 runs first (A↔C), then priority=10 (A↔B)
        # After A↔C: A credit = 250, C credit = 250
        # A's remaining = 750; A↔B can still generate credit
        _, total_both = apply_inter_spread_credits(groups, [rule_high, rule_low])
        _, total_high_only = apply_inter_spread_credits(groups, [rule_high])
        assert total_both >= total_high_only
