"""
Parser for NSE NSCCL SPAN SPN files (SP4 / CME-SPAN format).

NSE SPAN files are fixed-width ASCII. Each line starts with a single
record-type character:

  '0'  File header
  '1'  Combined Commodity  (PSR, VSR, spread charges, short-option min)
  '2'  Contract/Series     (symbol, expiry, strike, option type, lot size)
  '3'  Risk Array          (16 scenario loss values + composite delta)
  '4'  Intra-commodity spread charge
  '5'  Inter-commodity spread credit
  '6'  Delivery margin      (ignored for F&O)
  '7'  Price/Volatility     (underlying price, settlement data)

Field positions below are derived from the NSCCL SP4 specification.
**IMPORTANT:** These offsets must be validated against a live SPN file.
The parser logs a WARNING if it encounters unexpected record lengths so
that offsets can be corrected without code re-deployment.

All monetary values in SPN files are stored as integers scaled by
10^(risk_exponent). Typical risk_exponent = 2 → divide by 100.
"""

import io
import logging
import zipfile
from datetime import date, datetime
from pathlib import Path

from backend.extensions import db
from backend.models.db import (
    CombinedCommodity, Contract, InterCommoditySpread,
    IntraCommoditySpread, RiskArray, SpanFile,
)
from backend.margin.fallback_rates import get_fallback_commodity

logger = logging.getLogger(__name__)

# Known index symbols for exposure margin classification
_INDEX_SYMBOLS = {
    "NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY",
    "NIFTYNXT50", "SENSEX", "BANKEX",
}


# ─────────────────────────────────────────────────────────────────────────────
# Field extraction helpers
# ─────────────────────────────────────────────────────────────────────────────

def _s(line: str, start: int, end: int) -> str:
    """Extract a substring slice (1-indexed, inclusive start, exclusive end)."""
    return line[start - 1: end - 1].strip()


def _i(line: str, start: int, end: int, default: int = 0) -> int:
    raw = _s(line, start, end)
    try:
        return int(raw)
    except ValueError:
        return default


def _f(line: str, start: int, end: int, divisor: float = 1.0) -> float:
    raw = _s(line, start, end)
    try:
        return int(raw) / divisor
    except ValueError:
        return 0.0


def _parse_date_span(s: str) -> date | None:
    """Parse YYYYMMDD or DDMMYYYY date strings found in SPN files."""
    s = s.strip()
    for fmt in ("%Y%m%d", "%d%m%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Record parsers
# ─────────────────────────────────────────────────────────────────────────────

def _parse_type1(line: str, span_file: SpanFile, trade_date: date,
                 commodity_map: dict) -> CombinedCommodity | None:
    """
    Type-1: Combined Commodity record.
    Approximate field layout (NSCCL SP4, 192-char records):
      1       record type ('1')
      2–7     commodity code
      8–15    price scan range (integer, divide by exponent)
      16–23   volatility scan range (integer, divide by 10000 → fraction)
      24–31   inter-month spread charge (integer)
      32–39   short option minimum charge (integer)
      40      risk exponent
    """
    if len(line) < 40:
        logger.warning("Type-1 record too short (%d chars): %r", len(line), line[:60])
        return None

    try:
        code       = _s(line, 2, 8)
        exponent   = _i(line, 40, 41, default=2)
        divisor    = 10 ** exponent

        psr        = _f(line, 8, 16, divisor)
        vsr        = _f(line, 16, 24, 10000)    # stored as integer basis points × 100
        spread_chg = _f(line, 24, 32, divisor)
        somc       = _f(line, 32, 40, divisor)

        if not code:
            return None

        is_index = code in _INDEX_SYMBOLS
        exposure_rate = 0.03 if is_index else 0.05

        cc = CombinedCommodity.query.filter_by(
            trade_date=trade_date, commodity_code=code
        ).first()
        if cc is None:
            cc = CombinedCommodity(
                span_file_id=span_file.id,
                trade_date=trade_date,
                commodity_code=code,
                exchange_code="NSE",
            )
            db.session.add(cc)

        cc.price_scan_range          = psr
        cc.volatility_scan_range     = vsr
        cc.inter_month_spread_charge = spread_chg
        cc.short_option_min_charge   = somc
        cc.exposure_margin_rate      = exposure_rate
        cc.instrument_type           = "INDEX" if is_index else "STOCK"
        cc.is_estimated              = False

        commodity_map[code] = cc
        return cc

    except Exception as exc:
        logger.debug("Error parsing type-1 record: %s | %r", exc, line[:80])
        return None


def _parse_type2(line: str, span_file: SpanFile, trade_date: date,
                 context: dict) -> Contract | None:
    """
    Type-2: Contract record.
    Approximate field layout:
      1       record type ('2')
      2–7     commodity code
      8       contract type ('F'=future, 'O'=option, 'C'=combined)
      9–16    expiry date (YYYYMMDD)
      17–24   strike price (integer, right-justified, divide by 100)
      25      option type ('C'=call, 'P'=put, ' '=future)
      26–31   lot size
      32–37   underlying price (integer, divide by 100)
      38–43   future/contract price (integer, divide by 100)
    """
    if len(line) < 43:
        logger.warning("Type-2 record too short (%d chars)", len(line))
        return None

    try:
        code          = _s(line, 2, 8)
        contract_type = _s(line, 8, 9)     # F / O / C
        expiry_str    = _s(line, 9, 17)
        strike_raw    = _i(line, 17, 25)
        opt_raw       = _s(line, 25, 26)
        lot_size      = _i(line, 26, 32)
        underly_raw   = _i(line, 32, 38)
        fut_raw       = _i(line, 38, 44)

        if not code or not expiry_str:
            return None

        expiry = _parse_date_span(expiry_str)
        if expiry is None:
            return None

        # Derive instrument type
        is_index = code in _INDEX_SYMBOLS
        if contract_type == "F":
            instr_type = "FUTIDX" if is_index else "FUTSTK"
            strike = None
            opt_type = None
        elif contract_type == "O":
            instr_type = "OPTIDX" if is_index else "OPTSTK"
            strike = strike_raw / 100.0 if strike_raw else None
            opt_type = "CE" if opt_raw == "C" else ("PE" if opt_raw == "P" else None)
        else:
            return None   # combined / unknown, skip

        underlying_price = underly_raw / 100.0
        future_price     = fut_raw / 100.0

        from backend.span.bhavcopy_parser import _make_contract_key
        contract_key = _make_contract_key(code, instr_type, expiry, strike, opt_type)

        # Upsert contract
        contract = Contract.query.filter_by(
            trade_date=trade_date, contract_key=contract_key
        ).first()
        if contract is None:
            contract = Contract(
                span_file_id=span_file.id,
                trade_date=trade_date,
                commodity_code=code,
                symbol=code,
                instrument_type=instr_type,
                expiry_date=expiry,
                strike_price=strike,
                option_type=opt_type,
                lot_size=lot_size if lot_size > 0 else 1,
                underlying_price=underlying_price,
                future_price=future_price,
                contract_key=contract_key,
            )
            db.session.add(contract)
        else:
            contract.lot_size = lot_size if lot_size > 0 else contract.lot_size
            contract.underlying_price = underlying_price or contract.underlying_price
            contract.future_price = future_price or contract.future_price

        # Store current contract in context so type-3 can link to it
        context["last_contract"] = contract
        context["last_exponent"] = 2   # default; type-3 may override
        return contract

    except Exception as exc:
        logger.debug("Error parsing type-2 record: %s | %r", exc, line[:80])
        return None


def _parse_type3(line: str, context: dict) -> RiskArray | None:
    """
    Type-3: Risk Array record.
    16 scenario values × 8 chars each, starting at position 10.
    Composite delta at position 138.
    Risk exponent at position 146 (optional).

    Values are signed integers; positive = loss to the position holder.
    """
    contract = context.get("last_contract")
    if contract is None:
        return None

    if len(line) < 138:
        logger.warning("Type-3 record too short (%d chars)", len(line))
        return None

    try:
        exponent = _i(line, 146, 147, default=context.get("last_exponent", 2))
        divisor  = 10 ** exponent

        scenarios = []
        for i in range(16):
            start = 10 + i * 8    # 1-indexed
            val   = _f(line, start, start + 8, divisor)
            scenarios.append(val)

        delta = _f(line, 138, 146, divisor)

        # Upsert risk array
        ra = contract.risk_array
        if ra is None:
            ra = RiskArray(contract_id=contract.id)
            db.session.add(ra)
            contract.risk_array = ra

        (ra.s01, ra.s02, ra.s03, ra.s04,
         ra.s05, ra.s06, ra.s07, ra.s08,
         ra.s09, ra.s10, ra.s11, ra.s12,
         ra.s13, ra.s14, ra.s15, ra.s16) = scenarios
        ra.composite_delta = delta
        return ra

    except Exception as exc:
        logger.debug("Error parsing type-3 record: %s | %r", exc, line[:80])
        return None


def _parse_type4(line: str, span_file: SpanFile, trade_date: date) -> None:
    """Type-4: Intra-commodity spread charge."""
    try:
        code     = _s(line, 2, 8)
        priority = _i(line, 8, 12)
        rate     = _f(line, 12, 20, 10000)    # stored as basis points × 100
        if not code:
            return
        rec = IntraCommoditySpread(
            span_file_id=span_file.id,
            trade_date=trade_date,
            commodity_code=code,
            priority=priority,
            spread_charge_rate=rate,
        )
        db.session.add(rec)
    except Exception as exc:
        logger.debug("Error parsing type-4 record: %s", exc)


def _parse_type5(line: str, span_file: SpanFile, trade_date: date) -> None:
    """Type-5: Inter-commodity spread credit."""
    try:
        priority   = _i(line, 2, 6)
        leg1       = _s(line, 6, 12)
        leg2       = _s(line, 12, 18)
        credit_raw = _i(line, 18, 26)
        d_ratio1   = _f(line, 26, 34, 100)
        d_ratio2   = _f(line, 34, 42, 100)
        credit     = credit_raw / 10000.0   # stored as basis points × 100

        if not leg1 or not leg2:
            return
        rec = InterCommoditySpread(
            span_file_id=span_file.id,
            trade_date=trade_date,
            priority=priority,
            leg1_commodity=leg1,
            leg2_commodity=leg2,
            credit_rate=credit,
            delta_ratio_leg1=d_ratio1,
            delta_ratio_leg2=d_ratio2,
        )
        db.session.add(rec)
    except Exception as exc:
        logger.debug("Error parsing type-5 record: %s", exc)


# ─────────────────────────────────────────────────────────────────────────────
# Main entry point
# ─────────────────────────────────────────────────────────────────────────────

def parse_span_file(zip_path: Path, trade_date: date, span_file: SpanFile) -> int:
    """
    Parse the SPAN SPN zip file and populate all DB tables.
    Returns the number of risk arrays written.
    """
    # Clear old data for this date
    for model in (RiskArray, IntraCommoditySpread, InterCommoditySpread, Contract, CombinedCommodity):
        model.query.join(SpanFile).filter(SpanFile.trade_date == trade_date).delete(
            synchronize_session="fetch"
        )
    db.session.flush()

    commodity_map: dict[str, CombinedCommodity] = {}
    context: dict = {}
    risk_array_count = 0

    try:
        with zipfile.ZipFile(zip_path) as zf:
            spn_names = [n for n in zf.namelist() if n.lower().endswith(".spn")]
            if not spn_names:
                # Some archives use .txt or no extension
                spn_names = zf.namelist()
            if not spn_names:
                logger.error("No SPN content in zip %s", zip_path)
                return 0
            raw = zf.read(spn_names[0]).decode("ascii", errors="replace")
    except Exception as exc:
        logger.error("Failed to open SPAN zip: %s", exc)
        return 0

    batch_size = 500
    batch_count = 0

    for line in io.StringIO(raw):
        line = line.rstrip("\r\n")
        if not line:
            continue

        rtype = line[0]

        if rtype == "1":
            _parse_type1(line, span_file, trade_date, commodity_map)
        elif rtype == "2":
            _parse_type2(line, span_file, trade_date, context)
        elif rtype == "3":
            if _parse_type3(line, context):
                risk_array_count += 1
        elif rtype == "4":
            _parse_type4(line, span_file, trade_date)
        elif rtype == "5":
            _parse_type5(line, span_file, trade_date)

        batch_count += 1
        if batch_count >= batch_size:
            db.session.flush()
            batch_count = 0

    db.session.commit()
    logger.info("SPAN parse complete: %d risk arrays for %s", risk_array_count, trade_date)
    return risk_array_count
