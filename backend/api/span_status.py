from flask import Blueprint, jsonify
from backend.models.db import SpanFile, Contract
from backend.utils.date_utils import most_recent_trading_day, today_ist, span_data_is_stale

bp = Blueprint("span_status", __name__)


@bp.get("/api/span-status")
def get_span_status():
    latest = (
        SpanFile.query
        .filter_by(parse_status="success")
        .order_by(SpanFile.trade_date.desc())
        .first()
    )

    if latest is None:
        return jsonify({
            "status": "no_data",
            "trade_date": None,
            "file_type": None,
            "downloaded_at": None,
            "instrument_count": 0,
            "is_stale": True,
            "data_mode": "estimated",
        })

    count = Contract.query.filter_by(trade_date=latest.trade_date).count()
    stale = span_data_is_stale(latest.trade_date)

    # Determine if risk arrays exist
    from backend.models.db import RiskArray
    ra_count = (
        RiskArray.query
        .join(Contract)
        .filter(Contract.trade_date == latest.trade_date)
        .count()
    )

    return jsonify({
        "status": latest.parse_status,
        "trade_date": latest.trade_date.isoformat(),
        "file_type": latest.file_type,
        "downloaded_at": latest.downloaded_at.isoformat() if latest.downloaded_at else None,
        "instrument_count": count,
        "risk_array_count": ra_count,
        "is_stale": stale,
        "data_mode": "span_file" if ra_count > 0 else "estimated",
    })


@bp.post("/api/span/refresh")
def trigger_refresh():
    from backend.utils.date_utils import most_recent_trading_day, today_ist
    from backend.span.orchestrator import refresh_for_date

    trade_date = most_recent_trading_day(today_ist())
    result = refresh_for_date(trade_date, force=True)
    return jsonify(result)
