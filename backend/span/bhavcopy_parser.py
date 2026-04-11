"""
Parser for NSE UDiFF F&O Bhavcopy CSV files.

The bhavcopy does NOT contain SPAN risk arrays — it provides:
  • Contract details (symbol, expiry, strike, option type, lot size)
  • Settlement/last prices and underlying price

These are used to populate the `contracts` table. When risk arrays
are missing, the margin engine falls back to approximate PSR rates.

UDiFF bhavcopy CSV columns (as of 2024-25):
  TradDt, BizDt, Sgmt, Src, FinInstrmTp, FinInstrmId, ISIN,
  TckrSymb, SctySrs, XpryDt, FininstrmActlXpryDt, StrkPric, OptnTp,
  FinInstrmNm, OpnPric, HghPric, LwPric, ClsPric, LastPric, SttlmPric,
  TtlTradgVol, TtlTrfVal, TtlNbOfTxsExctd, SsnId, NewBrdLtPric,
  PrvsClsPric, UndrlygPric, OpnIntrst, ChngInOpnIntrst, MktLotSz
"""

import csv
import io
import logging
import zipfile
from datetime import date, datetime
from pathlib import Path

from backend.extensions import db
from backend.models.db import Contract, CombinedCommodity, SpanFile
from backend.margin.fallback_rates import get_fallback_commodity
from backend.span.isin_map import get_isin_map

logger = logging.getLogger(__name__)

# Map UDiFF FinInstrmTp → our instrument_type codes
# Actual codes observed in 2025-26 UDiFF files:
#   IDF = Index Future, IDO = Index Option
#   STF = Stock Future, STO = Stock Option
_TYPE_MAP = {
    "IDF": "FUTIDX",   # Index Future
    "IDO": "OPTIDX",   # Index Option
    "STF": "FUTSTK",   # Stock Future
    "STO": "OPTSTK",   # Stock Option
    # Legacy / alternate codes
    "IF": "FUTIDX",
    "IO": "OPTIDX",
    "SF": "FUTSTK",
    "SO": "OPTSTK",
    "FUTIDX": "FUTIDX",
    "OPTIDX": "OPTIDX",
    "FUTSTK": "FUTSTK",
    "OPTSTK": "OPTSTK",
}

_INDEX_SYMBOLS = {
    "NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY",
    "NIFTYNXT50", "SENSEX", "BANKEX",
}


def _make_contract_key(symbol: str, instr_type: str,
                       expiry: date, strike: float | None,
                       opt_type: str | None) -> str:
    parts = [symbol, instr_type, expiry.strftime("%Y%m%d")]
    if strike is not None:
        # Preserve half-integer strikes (e.g. 72.5 stays "72.5", 22500.0 → "22500")
        strike_str = f"{strike:.2f}".rstrip("0").rstrip(".")
        parts.append(strike_str)
    if opt_type:
        parts.append(opt_type)
    return "-".join(parts)


def parse_bhavcopy(zip_path: Path, trade_date: date, span_file: SpanFile) -> int:
    """
    Parse the UDiFF bhavcopy zip and populate `contracts` for *trade_date*.

    Returns the number of contracts inserted/updated.
    """
    count = 0
    commodity_cache: dict[str, CombinedCommodity] = {}
    isin_map = get_isin_map()  # {symbol: isin} for equity-listed stocks

    try:
        with zipfile.ZipFile(zip_path) as zf:
            # There should be exactly one CSV inside
            csv_names = [n for n in zf.namelist() if n.lower().endswith(".csv")]
            if not csv_names:
                logger.error("No CSV found in bhavcopy zip %s", zip_path)
                return 0
            csv_name = csv_names[0]
            raw = zf.read(csv_name).decode("utf-8", errors="replace")
    except Exception as exc:
        logger.error("Failed to open bhavcopy zip: %s", exc)
        return 0

    reader = csv.DictReader(io.StringIO(raw))

    # Delete existing contracts for this date to avoid duplicates on re-parse
    Contract.query.filter_by(trade_date=trade_date).delete()
    db.session.flush()

    for row in reader:
        try:
            instr_raw = (row.get("FinInstrmTp") or "").strip()
            instr_type = _TYPE_MAP.get(instr_raw)
            if instr_type is None:
                continue  # skip non-F&O rows

            symbol = (row.get("TckrSymb") or "").strip()
            if not symbol:
                continue

            expiry_str = (row.get("XpryDt") or "").strip()
            if not expiry_str:
                continue
            # Try multiple date formats
            expiry = _parse_date(expiry_str)
            if expiry is None:
                continue

            strike_raw = (row.get("StrkPric") or "").strip()
            strike = float(strike_raw) if strike_raw and strike_raw not in ("-", "0") else None

            opt_raw = (row.get("OptnTp") or "").strip().upper()
            opt_type = opt_raw if opt_raw in ("CE", "PE") else None

            # Column name changed in 2025-26 UDiFF format: MktLotSz → NewBrdLotQty
            lot_size_raw = (row.get("NewBrdLotQty") or row.get("MktLotSz") or "0").strip()
            lot_size = int(float(lot_size_raw)) if lot_size_raw else 0
            if lot_size <= 0:
                continue

            # Prefer SttlmPric; fall back to ClsPric then LastPric
            settle_raw  = (row.get("SttlmPric") or row.get("ClsPric") or row.get("LastPric") or "0").strip()
            underly_raw = (row.get("UndrlygPric") or "0").strip()
            # Previous day's settlement price (column name varies across file formats)
            prev_raw    = (row.get("PrvsClsgPric") or row.get("PrvsClsPric") or "0").strip()
            settle_price     = _to_float(settle_raw)
            underlying_price = _to_float(underly_raw)
            prev_settlement  = _to_float(prev_raw) or None  # store None when absent/zero

            # Underlying / commodity code (strip series suffix)
            commodity_code = _commodity_for(symbol, instr_type)

            contract_key = _make_contract_key(
                symbol, instr_type, expiry, strike, opt_type
            )

            # Ensure CombinedCommodity row exists for this underlying
            if commodity_code not in commodity_cache:
                cc = _get_or_create_commodity(
                    commodity_code, trade_date, span_file,
                    underlying_price, instr_type
                )
                commodity_cache[commodity_code] = cc

            # ISIN of the underlying (stocks only; indices have no ISIN)
            underlying_isin = isin_map.get(commodity_code) if instr_type in ("FUTSTK", "OPTSTK") else None

            contract = Contract(
                span_file_id=span_file.id,
                trade_date=trade_date,
                commodity_code=commodity_code,
                symbol=symbol,
                instrument_type=instr_type,
                expiry_date=expiry,
                strike_price=strike,
                option_type=opt_type,
                lot_size=lot_size,
                underlying_price=underlying_price,
                future_price=settle_price,
                prev_settlement=prev_settlement,
                underlying_isin=underlying_isin,
                contract_key=contract_key,
            )
            db.session.add(contract)
            count += 1

        except Exception as exc:
            logger.debug("Skipping row (error: %s): %s", exc, row)
            continue

    db.session.commit()
    logger.info("Bhavcopy: inserted %d contracts for %s", count, trade_date)
    return count


def _parse_date(s: str) -> date | None:
    for fmt in ("%d-%b-%Y", "%Y-%m-%d", "%d/%m/%Y", "%Y%m%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _to_float(s: str) -> float:
    try:
        return float(s)
    except (ValueError, TypeError):
        return 0.0


def _commodity_for(symbol: str, instr_type: str) -> str:
    """Derive the underlying commodity code from a contract symbol."""
    # For options/futures, the symbol IS the underlying for indices.
    # For stocks the symbol is the stock ticker itself.
    return symbol


def _get_or_create_commodity(
    code: str, trade_date: date, span_file: SpanFile,
    underlying_price: float, instr_type: str
) -> CombinedCommodity:
    cc = CombinedCommodity.query.filter_by(
        trade_date=trade_date, commodity_code=code
    ).first()
    if cc:
        return cc

    is_index = code in _INDEX_SYMBOLS or instr_type in ("FUTIDX", "OPTIDX")
    fb = get_fallback_commodity(code, underlying_price, is_index)

    cc = CombinedCommodity(
        span_file_id=span_file.id,
        trade_date=trade_date,
        commodity_code=code,
        exchange_code="NSE",
        price_scan_range=fb["price_scan_range"],
        volatility_scan_range=fb["volatility_scan_range"],
        inter_month_spread_charge=0.0,
        short_option_min_charge=fb["short_option_min_charge"],
        exposure_margin_rate=fb["exposure_margin_rate"],
        instrument_type="INDEX" if is_index else "STOCK",
        is_estimated=True,
    )
    db.session.add(cc)
    db.session.flush()   # get cc.id
    return cc
