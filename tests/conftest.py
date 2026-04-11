"""
Shared pytest fixtures for NSEMargins tests.

App-level fixture is session-scoped (one Flask app for the whole run).
DB cleanup runs before each test so every test starts with an empty database.

Fixed reference data used throughout:
  trade_date  = 2026-01-15  (Thursday)
  NIFTY       underlying = 22 000, lot_size = 25
  BANKNIFTY   underlying = 48 000, lot_size = 15
  RELIANCE    underlying =  1 200, lot_size = 250 (stock)
"""

import io
import zipfile
from datetime import date

import pytest

# ── Override the DB to an in-memory SQLite before importing the app ────────────

class TestConfig:
    TESTING = True
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    DATA_DIR = "/tmp/nse_test_data"
    SECRET_KEY = "test-secret"
    # Scheduler suppressed via TESTING=True (see app.py guard)
    NSE_HOME_URL = "https://www.nseindia.com"
    NSE_SPAN_BASE_URL = "https://nsearchives.nseindia.com/content/nsccl"
    NSE_BHAVCOPY_BASE_URL = "https://nsearchives.nseindia.com/content/fo"
    NSE_SPAN_URL_PATTERN = "{base}/nsccl.{date}.s.inn.spn.zip"
    NSE_BHAVCOPY_URL_PATTERN = "{base}/BhavCopy_NSE_FO_0_0_0_{date}_F_0000.csv.zip"
    DOWNLOAD_TIMEOUT_CONNECT = 30
    DOWNLOAD_TIMEOUT_READ = 120
    DOWNLOAD_MAX_RETRIES = 3
    DOWNLOAD_BACKOFF_FACTOR = 5
    INDEX_EXPOSURE_MARGIN_RATE = 0.02
    STOCK_EXPOSURE_MARGIN_RATE = 0.05
    EXTREME_SCENARIO_COVER_FRACTION = 0.35
    FALLBACK_PSR_RATES = {
        "NIFTY": 0.08, "NIFTY50": 0.08,
        "BANKNIFTY": 0.09, "FINNIFTY": 0.09, "MIDCPNIFTY": 0.09,
        "SENSEX": 0.08,
        "__STOCK__": 0.15,
    }
    FALLBACK_INTER_SPREADS = [
        ("BANKNIFTY", "NIFTY", 0.50, 1, 3),
        ("FINNIFTY",  "NIFTY", 0.50, 1, 2),
        ("MIDCPNIFTY","NIFTY", 0.50, 1, 2),
    ]
    SPAN_REFRESH_SCHEDULE = []


# ── Flask app ─────────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def app():
    from backend.app import create_app
    application = create_app(config_object=TestConfig)
    return application


@pytest.fixture
def client(app):
    return app.test_client()


# ── DB teardown between tests ──────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def clean_db(app):
    """Wipe all rows before every test (tables stay, data goes)."""
    with app.app_context():
        from backend.extensions import db
        from backend.models.db import (
            RiskArray, IntraCommoditySpread, InterCommoditySpread,
            Contract, CombinedCommodity, SpanFile,
        )
        for model in (RiskArray, IntraCommoditySpread, InterCommoditySpread,
                      Contract, CombinedCommodity, SpanFile):
            db.session.query(model).delete()
        db.session.commit()
    yield


# ── Reference dates ───────────────────────────────────────────────────────────

TRADE_DATE = date(2026, 1, 15)   # Thursday


# ── Core DB fixtures ──────────────────────────────────────────────────────────

@pytest.fixture
def span_file(app):
    with app.app_context():
        from backend.extensions import db
        from backend.models.db import SpanFile
        from datetime import datetime
        sf = SpanFile(
            trade_date=TRADE_DATE,
            file_type="span_spn",
            download_url="https://example.com/test.zip",
            downloaded_at=datetime(2026, 1, 15, 18, 35, 0),
            parse_status="success",
        )
        db.session.add(sf)
        db.session.commit()
        yield sf.id   # yield id so fixtures can be used across app_context boundaries


@pytest.fixture
def nifty_commodity(app, span_file):
    """NIFTY combined commodity with PSR=1000 (simplified), SOMC=50/contract."""
    with app.app_context():
        from backend.extensions import db
        from backend.models.db import CombinedCommodity, SpanFile
        sf = db.session.get(SpanFile, span_file)
        cc = CombinedCommodity(
            span_file_id=sf.id,
            trade_date=TRADE_DATE,
            commodity_code="NIFTY",
            exchange_code="NSE",
            price_scan_range=1000.0,       # simplified PSR per lot
            volatility_scan_range=0.04,
            inter_month_spread_charge=0.0,
            short_option_min_charge=50.0,  # per contract
            exposure_margin_rate=0.02,
            instrument_type="INDEX",
            is_estimated=False,
        )
        db.session.add(cc)
        db.session.commit()
        yield cc.id


@pytest.fixture
def banknifty_commodity(app, span_file):
    with app.app_context():
        from backend.extensions import db
        from backend.models.db import CombinedCommodity, SpanFile
        sf = db.session.get(SpanFile, span_file)
        cc = CombinedCommodity(
            span_file_id=sf.id,
            trade_date=TRADE_DATE,
            commodity_code="BANKNIFTY",
            exchange_code="NSE",
            price_scan_range=2000.0,
            volatility_scan_range=0.04,
            inter_month_spread_charge=0.0,
            short_option_min_charge=50.0,
            exposure_margin_rate=0.02,
            instrument_type="INDEX",
            is_estimated=False,
        )
        db.session.add(cc)
        db.session.commit()
        yield cc.id


@pytest.fixture
def reliance_commodity(app, span_file):
    with app.app_context():
        from backend.extensions import db
        from backend.models.db import CombinedCommodity, SpanFile
        sf = db.session.get(SpanFile, span_file)
        cc = CombinedCommodity(
            span_file_id=sf.id,
            trade_date=TRADE_DATE,
            commodity_code="RELIANCE",
            exchange_code="NSE",
            price_scan_range=45000.0,  # 15% × 1200 × 250
            volatility_scan_range=0.06,
            inter_month_spread_charge=0.0,
            short_option_min_charge=100.0,
            exposure_margin_rate=0.05,
            instrument_type="STOCK",
            is_estimated=False,
        )
        db.session.add(cc)
        db.session.commit()
        yield cc.id


def _futures_risk_array(psr: float) -> dict:
    """
    Build a simple futures risk array (per lot, long perspective).
    Worst loss for long = psr (price drops by full PSR).
    Extreme loss = 2×psr (with 35% cover applied by the engine).
    """
    t = psr / 3
    return dict(
        s01=0.0,    s02=0.0,    # no price move
        s03=-t,     s04=-t,     # +1/3 PSR: long gains
        s05=t,      s06=t,      # -1/3 PSR: long loses
        s07=-2*t,   s08=-2*t,   # +2/3 PSR
        s09=2*t,    s10=2*t,    # -2/3 PSR
        s11=-psr,   s12=-psr,   # +PSR
        s13=psr,    s14=psr,    # -PSR  ← worst for long
        s15=-2*psr, s16=2*psr,  # extreme (±2×PSR, 35% cover applied externally)
        composite_delta=1.0,
    )


def _option_risk_array(psr: float, is_call: bool) -> dict:
    """
    Simplified option risk array: half-delta approximation.
    Call delta ≈ 0.5, put delta ≈ -0.5.
    """
    sign = 1 if is_call else -1
    h = psr * 0.5   # half PSR (half-delta)
    t = psr / 3
    return dict(
        s01=100.0,   s02=-100.0,  # vol scenarios (flat price)
        s03=-sign*t, s04=-sign*t,
        s05=sign*t,  s06=sign*t,
        s07=-sign*2*t, s08=-sign*2*t,
        s09=sign*2*t,  s10=sign*2*t,
        s11=-sign*h,   s12=-sign*h,
        s13=sign*h,    s14=sign*h,  # worst for long call (price drops)
        s15=-sign*psr, s16=sign*psr,
        composite_delta=0.5 if is_call else -0.5,
    )


@pytest.fixture
def nifty_future_contract(app, span_file, nifty_commodity):
    """NIFTY Jan futures contract with PSR=1000 risk array."""
    with app.app_context():
        from backend.extensions import db
        from backend.models.db import Contract, RiskArray, SpanFile
        sf = db.session.get(SpanFile, span_file)
        c = Contract(
            span_file_id=sf.id,
            trade_date=TRADE_DATE,
            commodity_code="NIFTY",
            symbol="NIFTY",
            instrument_type="FUTIDX",
            expiry_date=date(2026, 1, 29),
            strike_price=None,
            option_type=None,
            lot_size=25,
            underlying_price=22000.0,
            future_price=22050.0,
            contract_key="NIFTY-FUTIDX-20260129",
        )
        db.session.add(c)
        db.session.flush()
        ra = RiskArray(contract_id=c.id, **_futures_risk_array(1000.0))
        db.session.add(ra)
        db.session.commit()
        yield c.contract_key


@pytest.fixture
def nifty_ce_contract(app, span_file, nifty_commodity):
    """NIFTY 22000 CE contract."""
    with app.app_context():
        from backend.extensions import db
        from backend.models.db import Contract, RiskArray, SpanFile
        sf = db.session.get(SpanFile, span_file)
        c = Contract(
            span_file_id=sf.id,
            trade_date=TRADE_DATE,
            commodity_code="NIFTY",
            symbol="NIFTY",
            instrument_type="OPTIDX",
            expiry_date=date(2026, 1, 29),
            strike_price=22000.0,
            option_type="CE",
            lot_size=25,
            underlying_price=22000.0,
            future_price=150.0,   # premium
            contract_key="NIFTY-OPTIDX-20260129-22000-CE",
        )
        db.session.add(c)
        db.session.flush()
        ra = RiskArray(contract_id=c.id, **_option_risk_array(1000.0, is_call=True))
        db.session.add(ra)
        db.session.commit()
        yield c.contract_key


@pytest.fixture
def nifty_pe_contract(app, span_file, nifty_commodity):
    """NIFTY 22000 PE contract."""
    with app.app_context():
        from backend.extensions import db
        from backend.models.db import Contract, RiskArray, SpanFile
        sf = db.session.get(SpanFile, span_file)
        c = Contract(
            span_file_id=sf.id,
            trade_date=TRADE_DATE,
            commodity_code="NIFTY",
            symbol="NIFTY",
            instrument_type="OPTIDX",
            expiry_date=date(2026, 1, 29),
            strike_price=22000.0,
            option_type="PE",
            lot_size=25,
            underlying_price=22000.0,
            future_price=120.0,
            contract_key="NIFTY-OPTIDX-20260129-22000-PE",
        )
        db.session.add(c)
        db.session.flush()
        ra = RiskArray(contract_id=c.id, **_option_risk_array(1000.0, is_call=False))
        db.session.add(ra)
        db.session.commit()
        yield c.contract_key


@pytest.fixture
def banknifty_future_contract(app, span_file, banknifty_commodity):
    with app.app_context():
        from backend.extensions import db
        from backend.models.db import Contract, RiskArray, SpanFile
        sf = db.session.get(SpanFile, span_file)
        c = Contract(
            span_file_id=sf.id,
            trade_date=TRADE_DATE,
            commodity_code="BANKNIFTY",
            symbol="BANKNIFTY",
            instrument_type="FUTIDX",
            expiry_date=date(2026, 1, 29),
            strike_price=None,
            option_type=None,
            lot_size=15,
            underlying_price=48000.0,
            future_price=48100.0,
            contract_key="BANKNIFTY-FUTIDX-20260129",
        )
        db.session.add(c)
        db.session.flush()
        ra = RiskArray(contract_id=c.id, **_futures_risk_array(2000.0))
        db.session.add(ra)
        db.session.commit()
        yield c.contract_key


@pytest.fixture
def inter_spread_rule(app, span_file):
    """BANKNIFTY↔NIFTY spread credit at 50%."""
    with app.app_context():
        from backend.extensions import db
        from backend.models.db import InterCommoditySpread, SpanFile
        sf = db.session.get(SpanFile, span_file)
        rule = InterCommoditySpread(
            span_file_id=sf.id,
            trade_date=TRADE_DATE,
            priority=1,
            leg1_commodity="BANKNIFTY",
            leg2_commodity="NIFTY",
            credit_rate=0.50,
            delta_ratio_leg1=1.0,
            delta_ratio_leg2=3.0,
        )
        db.session.add(rule)
        db.session.commit()
        yield rule.id


# ── Sample bhavcopy CSV ───────────────────────────────────────────────────────

BHAVCOPY_HEADER = (
    "TradDt,BizDt,Sgmt,Src,FinInstrmTp,FinInstrmId,ISIN,TckrSymb,"
    "SctySrs,XpryDt,FininstrmActlXpryDt,StrkPric,OptnTp,FinInstrmNm,"
    "OpnPric,HghPric,LwPric,ClsPric,LastPric,PrvsClsgPric,UndrlygPric,"
    "SttlmPric,OpnIntrst,ChngInOpnIntrst,TtlTradgVol,TtlTrfVal,"
    "TtlNbOfTxsExctd,SsnId,NewBrdLotQty,Rmks,Rsvd1,Rsvd2,Rsvd3,Rsvd4"
)

def _bhav_row(tdate, tp, symbol, expiry, strike, opt, settle, underlying, lot):
    return (
        f"{tdate},{tdate},FO,NSE,{tp},,,"
        f"{symbol},,{expiry},{expiry},"
        f"{strike},{opt},NAME,"
        f"0.00,0.00,0.00,{settle},{settle},0.00,{underlying},"
        f"{settle},0,0,0,0.00,0,F1,{lot},,,,"
    )

def _make_bhavcopy_zip(rows: list[str]) -> bytes:
    csv_content = BHAVCOPY_HEADER + "\n" + "\n".join(rows)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("BhavCopy_NSE_FO_0_0_0_20260115_F_0000.csv", csv_content)
    return buf.getvalue()


SAMPLE_BHAV_ROWS = [
    # Index Future
    _bhav_row("2026-01-15", "IDF", "NIFTY",     "2026-01-29", "",       "",   22050.0, 22000.0, 25),
    # Index Options
    _bhav_row("2026-01-15", "IDO", "NIFTY",     "2026-01-29", 22000.00, "CE", 150.0,   22000.0, 25),
    _bhav_row("2026-01-15", "IDO", "NIFTY",     "2026-01-29", 22000.00, "PE", 120.0,   22000.0, 25),
    _bhav_row("2026-01-15", "IDO", "BANKNIFTY", "2026-01-29", 48000.00, "CE", 300.0,   48000.0, 15),
    # Stock Future
    _bhav_row("2026-01-15", "STF", "RELIANCE",  "2026-01-29", "",       "",   1210.0,  1200.0, 250),
    # Stock Option with half-integer strike
    _bhav_row("2026-01-15", "STO", "INOXWIND",  "2026-04-28", 72.50,    "PE", 39.35,   86.88,  3100),
]
