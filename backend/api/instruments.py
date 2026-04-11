from flask import Blueprint, jsonify, request
from sqlalchemy import or_
from datetime import date

from backend.models.db import Contract
from backend.utils.date_utils import most_recent_trading_day, today_ist

bp = Blueprint("instruments", __name__)


def _resolve_date(date_str: str | None) -> date:
    if date_str:
        try:
            return date.fromisoformat(date_str)
        except ValueError:
            pass
    return most_recent_trading_day(today_ist())


def _contract_summary(c: Contract) -> dict:
    return {
        "contract_key": c.contract_key,
        "symbol": c.symbol,
        "instrument_type": c.instrument_type,
        "expiry_date": c.expiry_date.isoformat(),
        "strike_price": c.strike_price,
        "option_type": c.option_type,
        "lot_size": c.lot_size,
        "underlying_price": c.underlying_price,
        "future_price": c.future_price,
        "prev_settlement": c.prev_settlement,
        "underlying_isin": c.underlying_isin,
        "commodity_code": c.commodity_code,
    }


@bp.get("/api/instruments/search")
def search_instruments():
    q = (request.args.get("q") or "").strip().upper()
    trade_date = _resolve_date(request.args.get("date"))
    limit = min(int(request.args.get("limit", 50)), 200)

    if not q:
        return jsonify({"results": []})

    # Match by symbol prefix
    results = (
        Contract.query
        .filter(
            Contract.trade_date == trade_date,
            Contract.symbol.like(f"{q}%"),
        )
        .order_by(Contract.symbol, Contract.expiry_date, Contract.strike_price)
        .limit(limit)
        .all()
    )

    return jsonify({"results": [_contract_summary(c) for c in results]})


@bp.get("/api/instruments/symbols")
def list_symbols():
    """Return distinct underlying symbols available for the given date."""
    trade_date = _resolve_date(request.args.get("date"))

    from sqlalchemy import distinct
    rows = (
        Contract.query
        .with_entities(distinct(Contract.commodity_code))
        .filter(Contract.trade_date == trade_date)
        .order_by(Contract.commodity_code)
        .all()
    )
    symbols = [r[0] for r in rows]
    return jsonify({"symbols": symbols, "trade_date": trade_date.isoformat()})


@bp.get("/api/instruments/expiries")
def list_expiries():
    symbol = (request.args.get("symbol") or "").strip().upper()
    trade_date = _resolve_date(request.args.get("date"))

    if not symbol:
        return jsonify({"error": "symbol required"}), 400

    from sqlalchemy import distinct
    rows = (
        Contract.query
        .with_entities(distinct(Contract.expiry_date))
        .filter(
            Contract.trade_date == trade_date,
            Contract.commodity_code == symbol,
        )
        .order_by(Contract.expiry_date)
        .all()
    )
    expiries = [r[0].isoformat() for r in rows]
    return jsonify({"symbol": symbol, "expiries": expiries})


@bp.get("/api/instruments/strikes")
def list_strikes():
    symbol = (request.args.get("symbol") or "").strip().upper()
    expiry_str = request.args.get("expiry")
    trade_date = _resolve_date(request.args.get("date"))

    if not symbol or not expiry_str:
        return jsonify({"error": "symbol and expiry required"}), 400

    try:
        expiry = date.fromisoformat(expiry_str)
    except ValueError:
        return jsonify({"error": "invalid expiry date"}), 400

    contracts = (
        Contract.query
        .filter(
            Contract.trade_date == trade_date,
            Contract.commodity_code == symbol,
            Contract.expiry_date == expiry,
            Contract.instrument_type.in_(["OPTIDX", "OPTSTK"]),
        )
        .order_by(Contract.strike_price, Contract.option_type)
        .all()
    )

    # Group into strike → {CE, PE}
    strike_map: dict[float, dict] = {}
    for c in contracts:
        s = c.strike_price or 0.0
        if s not in strike_map:
            strike_map[s] = {"strike": s, "ce_key": None, "pe_key": None}
        if c.option_type == "CE":
            strike_map[s]["ce_key"] = c.contract_key
        elif c.option_type == "PE":
            strike_map[s]["pe_key"] = c.contract_key

    strikes = sorted(strike_map.values(), key=lambda x: x["strike"])
    return jsonify({
        "symbol": symbol,
        "expiry_date": expiry_str,
        "strikes": strikes,
    })


@bp.get("/api/instruments/futures")
def list_futures():
    symbol = (request.args.get("symbol") or "").strip().upper()
    trade_date = _resolve_date(request.args.get("date"))

    if not symbol:
        return jsonify({"error": "symbol required"}), 400

    contracts = (
        Contract.query
        .filter(
            Contract.trade_date == trade_date,
            Contract.commodity_code == symbol,
            Contract.instrument_type.in_(["FUTIDX", "FUTSTK"]),
        )
        .order_by(Contract.expiry_date)
        .all()
    )
    return jsonify({
        "symbol": symbol,
        "futures": [_contract_summary(c) for c in contracts],
    })


@bp.get("/api/instruments/contract/<contract_key>")
def get_contract(contract_key: str):
    trade_date = _resolve_date(request.args.get("date"))
    c = Contract.query.filter_by(
        trade_date=trade_date, contract_key=contract_key
    ).first()
    if c is None:
        return jsonify({"error": "contract not found"}), 404
    return jsonify(_contract_summary(c))
