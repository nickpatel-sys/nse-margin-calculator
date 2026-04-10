"""
Integration tests for backend/span/bhavcopy_parser.py.

Uses the in-memory SQLite database (TestConfig) and the sample bhavcopy
rows defined in conftest.py to exercise parse_bhavcopy() end-to-end.
"""

import io
import zipfile
from datetime import date
from pathlib import Path

import pytest

from tests.conftest import TRADE_DATE, SAMPLE_BHAV_ROWS, _make_bhavcopy_zip


# ── Helpers ───────────────────────────────────────────────────────────────────

def _write_zip(tmp_path: Path, rows: list[str]) -> Path:
    """Write a bhavcopy zip to a temp file and return its path."""
    zip_bytes = _make_bhavcopy_zip(rows)
    zip_path = tmp_path / f"BhavCopy_NSE_FO_0_0_0_{TRADE_DATE.strftime('%Y%m%d')}_F_0000.csv.zip"
    zip_path.write_bytes(zip_bytes)
    return zip_path


def _parse(app, zip_path: Path) -> int:
    """Run parse_bhavcopy() inside an app context and return the count."""
    with app.app_context():
        from backend.extensions import db
        from backend.models.db import SpanFile
        from backend.span.bhavcopy_parser import parse_bhavcopy
        from datetime import datetime

        # Reuse an existing SpanFile for TRADE_DATE if one exists (unique constraint).
        sf = SpanFile.query.filter_by(trade_date=TRADE_DATE).first()
        if sf is None:
            sf = SpanFile(
                trade_date=TRADE_DATE,
                file_type="bhavcopy",
                download_url="https://example.com/test.zip",
                downloaded_at=datetime(2026, 1, 15, 18, 35, 0),
                parse_status="success",
            )
            db.session.add(sf)
            db.session.commit()
        return parse_bhavcopy(zip_path, TRADE_DATE, sf)


# ── Basic parsing ─────────────────────────────────────────────────────────────

def test_parse_returns_correct_count(app, tmp_path):
    """All 6 sample rows should be parsed successfully."""
    zip_path = _write_zip(tmp_path, SAMPLE_BHAV_ROWS)
    count = _parse(app, zip_path)
    assert count == 6


def test_parse_empty_zip_returns_zero(app, tmp_path):
    """A zip with no CSV rows yields 0."""
    zip_path = _write_zip(tmp_path, [])
    count = _parse(app, zip_path)
    assert count == 0


def test_parse_invalid_zip_returns_zero(app, tmp_path):
    """A corrupt zip file should not raise — returns 0."""
    from backend.span.bhavcopy_parser import parse_bhavcopy
    from datetime import datetime

    bad_path = tmp_path / "bad.zip"
    bad_path.write_bytes(b"not a zip file at all")

    with app.app_context():
        from backend.extensions import db
        from backend.models.db import SpanFile

        sf = SpanFile(
            trade_date=TRADE_DATE,
            file_type="bhavcopy",
            download_url="https://example.com/bad.zip",
            downloaded_at=datetime(2026, 1, 15, 19, 0, 0),
            parse_status="success",
        )
        db.session.add(sf)
        db.session.commit()
        result = parse_bhavcopy(bad_path, TRADE_DATE, sf)

    assert result == 0


# ── Contract fields ───────────────────────────────────────────────────────────

def test_nifty_future_contract_key(app, tmp_path):
    """NIFTY IDF row produces key 'NIFTY-FUTIDX-20260129'."""
    zip_path = _write_zip(tmp_path, SAMPLE_BHAV_ROWS)
    _parse(app, zip_path)

    with app.app_context():
        from backend.models.db import Contract
        c = Contract.query.filter_by(
            trade_date=TRADE_DATE,
            contract_key="NIFTY-FUTIDX-20260129",
        ).first()
        assert c is not None
        assert c.instrument_type == "FUTIDX"
        assert c.lot_size == 25
        assert c.underlying_price == pytest.approx(22000.0)


def test_nifty_ce_contract_key(app, tmp_path):
    zip_path = _write_zip(tmp_path, SAMPLE_BHAV_ROWS)
    _parse(app, zip_path)

    with app.app_context():
        from backend.models.db import Contract
        c = Contract.query.filter_by(
            trade_date=TRADE_DATE,
            contract_key="NIFTY-OPTIDX-20260129-22000-CE",
        ).first()
        assert c is not None
        assert c.instrument_type == "OPTIDX"
        assert c.strike_price == pytest.approx(22000.0)
        assert c.option_type == "CE"
        assert c.future_price == pytest.approx(150.0)


def test_nifty_pe_contract_key(app, tmp_path):
    zip_path = _write_zip(tmp_path, SAMPLE_BHAV_ROWS)
    _parse(app, zip_path)

    with app.app_context():
        from backend.models.db import Contract
        c = Contract.query.filter_by(
            trade_date=TRADE_DATE,
            contract_key="NIFTY-OPTIDX-20260129-22000-PE",
        ).first()
        assert c is not None
        assert c.option_type == "PE"


def test_stock_future_instrument_type(app, tmp_path):
    """RELIANCE STF row → FUTSTK."""
    zip_path = _write_zip(tmp_path, SAMPLE_BHAV_ROWS)
    _parse(app, zip_path)

    with app.app_context():
        from backend.models.db import Contract
        c = Contract.query.filter_by(
            trade_date=TRADE_DATE,
            contract_key="RELIANCE-FUTSTK-20260129",
        ).first()
        assert c is not None
        assert c.instrument_type == "FUTSTK"
        assert c.lot_size == 250


# ── Half-integer strike ───────────────────────────────────────────────────────

def test_half_integer_strike_contract_key(app, tmp_path):
    """INOXWIND 72.5 PE → key contains '72.5', not '72' or '72.50'."""
    zip_path = _write_zip(tmp_path, SAMPLE_BHAV_ROWS)
    _parse(app, zip_path)

    with app.app_context():
        from backend.models.db import Contract
        c = Contract.query.filter_by(
            trade_date=TRADE_DATE,
            contract_key="INOXWIND-OPTSTK-20260428-72.5-PE",
        ).first()
        assert c is not None, "Half-integer strike key not found"
        assert c.strike_price == pytest.approx(72.5)


# ── Commodity creation ────────────────────────────────────────────────────────

def test_index_commodity_created_with_3pct_exposure(app, tmp_path):
    """Parsing creates a CombinedCommodity for NIFTY with 3% exposure rate."""
    zip_path = _write_zip(tmp_path, SAMPLE_BHAV_ROWS)
    _parse(app, zip_path)

    with app.app_context():
        from backend.models.db import CombinedCommodity
        cc = CombinedCommodity.query.filter_by(
            trade_date=TRADE_DATE, commodity_code="NIFTY"
        ).first()
        assert cc is not None
        assert cc.exposure_margin_rate == pytest.approx(0.03)
        assert cc.is_estimated is True


def test_stock_commodity_created_with_5pct_exposure(app, tmp_path):
    """RELIANCE gets 5% exposure rate (stock)."""
    zip_path = _write_zip(tmp_path, SAMPLE_BHAV_ROWS)
    _parse(app, zip_path)

    with app.app_context():
        from backend.models.db import CombinedCommodity
        cc = CombinedCommodity.query.filter_by(
            trade_date=TRADE_DATE, commodity_code="RELIANCE"
        ).first()
        assert cc is not None
        assert cc.exposure_margin_rate == pytest.approx(0.05)


def test_commodity_created_only_once_per_underlying(app, tmp_path):
    """Three NIFTY rows (1 future + 2 options) → 1 CombinedCommodity for NIFTY."""
    zip_path = _write_zip(tmp_path, SAMPLE_BHAV_ROWS)
    _parse(app, zip_path)

    with app.app_context():
        from backend.models.db import CombinedCommodity
        count = CombinedCommodity.query.filter_by(
            trade_date=TRADE_DATE, commodity_code="NIFTY"
        ).count()
        assert count == 1


# ── Re-parse idempotency ──────────────────────────────────────────────────────

def test_reparse_does_not_duplicate_contracts(app, tmp_path):
    """Parsing the same file twice keeps only the latest set of contracts."""
    zip_path = _write_zip(tmp_path, SAMPLE_BHAV_ROWS)
    _parse(app, zip_path)
    _parse(app, zip_path)   # second parse should replace, not duplicate

    with app.app_context():
        from backend.models.db import Contract
        total = Contract.query.filter_by(trade_date=TRADE_DATE).count()
        assert total == 6
