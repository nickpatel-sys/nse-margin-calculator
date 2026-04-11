from dataclasses import asdict
from datetime import date

from flask import Blueprint, jsonify, request

from backend.margin.calculator import MarginResult, PositionRequest, calculate_portfolio_margin
from backend.utils.date_utils import most_recent_trading_day, today_ist

bp = Blueprint("margin", __name__)


@bp.post("/api/margin/calculate")
def calculate_margin():
    body = request.get_json(silent=True)
    if not body:
        return jsonify({"error": "JSON body required"}), 400

    # Parse trade date
    date_str = body.get("trade_date")
    if date_str:
        try:
            trade_date = date.fromisoformat(date_str)
        except ValueError:
            return jsonify({"error": f"invalid trade_date: {date_str}"}), 400
    else:
        trade_date = most_recent_trading_day(today_ist())

    # Parse positions
    raw_positions = body.get("positions", [])
    if not raw_positions:
        return jsonify({"error": "positions array is required and must not be empty"}), 400

    positions = []
    for i, p in enumerate(raw_positions):
        key = p.get("contract_key")
        qty = p.get("quantity")
        if not key:
            return jsonify({"error": f"positions[{i}].contract_key is required"}), 400
        if qty is None or not isinstance(qty, (int, float)):
            return jsonify({"error": f"positions[{i}].quantity must be a non-zero integer"}), 400
        if qty == 0:
            return jsonify({"error": f"positions[{i}].quantity must not be zero"}), 400
        prev_settle = p.get("prev_settlement")
        positions.append(PositionRequest(
            contract_key=str(key),
            quantity=int(qty),
            prev_settlement=float(prev_settle) if prev_settle else 0.0,
        ))

    result: MarginResult = calculate_portfolio_margin(positions, trade_date)

    if result.error:
        return jsonify({"error": result.error}), 422

    return jsonify(_serialize_result(result))


def _serialize_result(r: MarginResult) -> dict:
    return {
        "trade_date": r.trade_date,
        "summary": {
            "span_margin": round(r.span_margin, 2),
            "exposure_margin": round(r.exposure_margin, 2),
            "total_margin": round(r.total_margin, 2),
            "premium_received": round(r.premium_received, 2),
            "data_mode": r.data_mode,
            "variation_margin": round(r.variation_margin, 2),
            "net_cash_required": round(r.net_cash_required, 2),
        },
        "by_commodity": [
            {
                "commodity": c.commodity,
                "scan_risk": round(c.scan_risk, 2),
                "intra_spread_charge": round(c.intra_spread_charge, 2),
                "inter_spread_credit": round(c.inter_spread_credit, 2),
                "short_option_min": round(c.short_option_min, 2),
                "commodity_span": round(c.commodity_span, 2),
                "exposure_margin": round(c.exposure_margin, 2),
            }
            for c in r.by_commodity
        ],
        "by_position": [
            {
                "contract_key": p.contract_key,
                "symbol": p.symbol,
                "instrument_type": p.instrument_type,
                "expiry_date": p.expiry_date,
                "strike_price": p.strike_price,
                "option_type": p.option_type,
                "side": p.side,
                "lots": p.lots,
                "lot_size": p.lot_size,
                "underlying_price": round(p.underlying_price, 2),
                "future_price": round(p.future_price, 2),
                "notional_value": round(p.notional_value, 2),
                "worst_scenario": p.worst_scenario,
                "worst_scenario_loss": round(p.worst_scenario_loss, 2),
                "exposure_margin": round(p.exposure_margin, 2),
                "position_type": p.position_type,
                "data_mode": p.data_mode,
                "underlying_isin": p.underlying_isin,
                "variation_margin": round(p.variation_margin, 2) if p.variation_margin is not None else None,
            }
            for p in r.by_position
        ],
    }
