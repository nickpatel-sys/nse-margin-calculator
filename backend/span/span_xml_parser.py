"""
Parser for NSE SPAN XML files (SPAN 4.00 format).

URL pattern: https://nsearchives.nseindia.com/archives/nsccl/span/nsccl.{YYYYMMDD}.s.zip
Contains the official SPAN risk arrays for all F&O contracts.

XML structure:
  spanFile/pointInTime/clearingOrg
    exchange
      phyPf (physical/underlying)  — pfCode = symbol, p = spot price
      futPf  (futures)             — fut: pe=expiry, p=price, ra=risk_array, scanRate=PSR/VSR
      oopPf  (options)             — series: pe=expiry; opt: o=C/P, k=strike, ra=risk_array
    ccDef (combined commodity def)  — cc=symbol, scanTiers, somTiers

Risk array conventions:
  - Each <ra> has 16 <a> values (per-unit, INR per single contract unit).
  - Positive value = loss for a LONG position; negative = gain.
  - Extreme scenarios 15-16 carry a 35% cover (applied in calculator).
  - We store per-LOT values in RiskArray (multiply by lot_size from bhavcopy).
"""

import logging
import zipfile
import io
from datetime import date, datetime
from pathlib import Path

from backend.extensions import db
from backend.models.db import Contract, CombinedCommodity, RiskArray, SpanFile

logger = logging.getLogger(__name__)

# Index symbols — used to set instrument_type filter for DB lookup
_INDEX_SYMBOLS = {
    "NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY",
    "NIFTYNXT50", "SENSEX", "BANKEX",
}


def parse_span_xml(zip_path: Path, trade_date: date, span_file: SpanFile) -> int:
    """
    Parse the SPAN XML zip and populate RiskArray rows + update CombinedCommodity data.

    This is called AFTER parse_bhavcopy() so that Contract rows already exist.
    Returns the number of RiskArray rows written.

    Steps:
    1. Parse XML → extract per-unit risk arrays keyed by (pfCode, expiry, opt_type, strike).
    2. For each contract found in the DB, multiply by lot_size → store RiskArray.
    3. Update CombinedCommodity.price_scan_range / volatility_scan_range with real values.
    """
    try:
        with zipfile.ZipFile(zip_path) as zf:
            spn_files = [n for n in zf.namelist() if n.lower().endswith(".spn")]
            if not spn_files:
                spn_files = zf.namelist()
            if not spn_files:
                logger.error("No .spn file found in SPAN zip %s", zip_path)
                return 0
            raw = zf.read(spn_files[0]).decode("utf-8", errors="replace")
    except Exception as exc:
        logger.error("Failed to open SPAN zip %s: %s", zip_path, exc)
        return 0

    try:
        import xml.etree.ElementTree as ET
        root = ET.fromstring(raw)
    except Exception as exc:
        logger.error("Failed to parse SPAN XML: %s", exc)
        return 0

    co = root.find("pointInTime/clearingOrg")
    if co is None:
        logger.error("SPAN XML missing pointInTime/clearingOrg")
        return 0

    exch = co.find("exchange")
    if exch is None:
        logger.error("SPAN XML missing exchange element")
        return 0

    # Build a lookup of commodity → PSR/VSR from the first future found per symbol
    commodity_scan: dict[str, dict] = {}

    # Collect all (pfCode, expiry, opt_type, strike) → {risk_array, psr, vsr, delta}
    contracts_data: list[dict] = []

    for pf in exch:
        pfc_el = pf.find("pfCode")
        if pfc_el is None or not pfc_el.text:
            continue
        symbol = pfc_el.text.strip()

        if pf.tag == "futPf":
            for fut in pf.findall("fut"):
                pe_el = fut.find("pe")
                if pe_el is None or not pe_el.text:
                    continue
                expiry = _parse_date(pe_el.text.strip())
                if expiry is None:
                    continue

                ra_vals = _extract_ra(fut)
                psr, vsr = _extract_scan_rate(fut)
                delta = _extract_delta(fut)

                contracts_data.append({
                    "symbol": symbol,
                    "expiry": expiry,
                    "option_type": None,
                    "strike": None,
                    "risk_array": ra_vals,
                    "psr": psr,
                    "vsr": vsr,
                    "composite_delta": delta,
                })
                if symbol not in commodity_scan and psr:
                    commodity_scan[symbol] = {"psr": psr, "vsr": vsr}

        elif pf.tag == "oopPf":
            for series in pf.findall("series"):
                pe_el = series.find("pe")
                if pe_el is None or not pe_el.text:
                    continue
                expiry = _parse_date(pe_el.text.strip())
                if expiry is None:
                    continue

                psr, vsr = _extract_scan_rate(series)
                if symbol not in commodity_scan and psr:
                    commodity_scan[symbol] = {"psr": psr, "vsr": vsr}

                for opt in series.findall("opt"):
                    o_el = opt.find("o")
                    k_el = opt.find("k")
                    if o_el is None or k_el is None:
                        continue
                    raw_opt = (o_el.text or "").strip()
                    option_type = "CE" if raw_opt == "C" else ("PE" if raw_opt == "P" else None)
                    if option_type is None:
                        continue
                    try:
                        strike = float(k_el.text.strip())
                    except (ValueError, AttributeError):
                        continue

                    ra_vals = _extract_ra(opt)
                    delta = _extract_delta(opt)
                    contracts_data.append({
                        "symbol": symbol,
                        "expiry": expiry,
                        "option_type": option_type,
                        "strike": strike,
                        "risk_array": ra_vals,
                        "psr": psr,
                        "vsr": vsr,
                        "composite_delta": delta,
                    })

    logger.info("SPAN XML: parsed %d contract entries", len(contracts_data))

    # Build in-memory contract lookup: (commodity_code, expiry, option_type, strike_key) → Contract
    # This avoids 156K individual DB queries — one bulk fetch instead.
    all_contracts = Contract.query.filter_by(trade_date=trade_date).all()
    contract_map: dict[tuple, "Contract"] = {}
    for c in all_contracts:
        strike_key = round(c.strike_price, 2) if c.strike_price is not None else None
        key = (c.commodity_code, c.expiry_date, c.option_type, strike_key)
        contract_map[key] = c

    # Delete existing risk arrays for this trade_date via subquery (avoids SQLite variable limit)
    db.session.execute(
        db.text(
            "DELETE FROM risk_arrays WHERE contract_id IN "
            "(SELECT id FROM contracts WHERE trade_date = :td)"
        ),
        {"td": trade_date},
    )
    db.session.flush()

    # Match to DB contracts and write RiskArrays using bulk insert
    rows: list[dict] = []
    for entry in contracts_data:
        ra_vals = entry["risk_array"]
        if len(ra_vals) != 16:
            continue

        strike_key = round(entry["strike"], 2) if entry["strike"] is not None else None
        key = (entry["symbol"], entry["expiry"], entry["option_type"], strike_key)
        contract = contract_map.get(key)
        if contract is None:
            continue

        lot = contract.lot_size or 1
        scaled = [v * lot for v in ra_vals]
        rows.append({
            "contract_id": contract.id,
            "s01": scaled[0],  "s02": scaled[1],  "s03": scaled[2],  "s04": scaled[3],
            "s05": scaled[4],  "s06": scaled[5],  "s07": scaled[6],  "s08": scaled[7],
            "s09": scaled[8],  "s10": scaled[9],  "s11": scaled[10], "s12": scaled[11],
            "s13": scaled[12], "s14": scaled[13], "s15": scaled[14], "s16": scaled[15],
            "composite_delta": entry["composite_delta"],
        })

    if rows:
        db.session.execute(db.text(
            "INSERT INTO risk_arrays "
            "(contract_id,s01,s02,s03,s04,s05,s06,s07,s08,s09,s10,s11,s12,s13,s14,s15,s16,composite_delta) "
            "VALUES (:contract_id,:s01,:s02,:s03,:s04,:s05,:s06,:s07,:s08,:s09,:s10,:s11,:s12,:s13,:s14,:s15,:s16,:composite_delta)"
        ), rows)
    count = len(rows)

    db.session.flush()

    # Update CombinedCommodity with real PSR/VSR
    for symbol, scan in commodity_scan.items():
        cc = CombinedCommodity.query.filter_by(
            trade_date=trade_date, commodity_code=symbol
        ).first()
        if cc is not None and scan["psr"]:
            cc.price_scan_range = scan["psr"]
            cc.volatility_scan_range = scan["vsr"]
            cc.is_estimated = False

    db.session.commit()
    logger.info("SPAN XML: wrote %d RiskArray rows for %s", count, trade_date)
    return count


# ── Helpers ────────────────────────────────────────────────────────────────────

def _extract_ra(elem) -> list[float]:
    """Extract 16 scenario values from an element's <ra> child."""
    ra_el = elem.find("ra")
    if ra_el is None:
        return []
    return [float(a.text) for a in ra_el.findall("a") if a.text]


def _extract_delta(elem) -> float:
    """Extract composite delta from the <ra><d> element."""
    ra_el = elem.find("ra")
    if ra_el is None:
        return 0.0
    d_el = ra_el.find("d")
    if d_el is None or not d_el.text:
        return 0.0
    try:
        return float(d_el.text)
    except ValueError:
        return 0.0


def _extract_scan_rate(elem) -> tuple[float, float]:
    """Return (priceScan, volScan) from elem's <scanRate> child, or (0, 0)."""
    sr = elem.find("scanRate")
    if sr is None:
        return 0.0, 0.0
    ps = sr.find("priceScan")
    vs = sr.find("volScan")
    try:
        psr = float(ps.text) if ps is not None and ps.text else 0.0
        vsr = float(vs.text) if vs is not None and vs.text else 0.0
        return psr, vsr
    except ValueError:
        return 0.0, 0.0


def _parse_date(s: str) -> date | None:
    if not s or s == "00000000":
        return None
    for fmt in ("%Y%m%d", "%d-%b-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


