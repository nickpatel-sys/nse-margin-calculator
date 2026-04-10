"""
SQLAlchemy ORM models for the NSE margin calculator.

Tables
------
span_files              — one row per downloaded SPAN / bhavcopy file
combined_commodities    — SPAN combined commodity record (PSR, VSR, charges)
contracts               — every tradeable F&O contract for a trade date
risk_arrays             — 16 scenario loss values per contract
inter_commodity_spreads — cross-underlying spread credit rules
intra_commodity_spreads — calendar-spread charges within one underlying
"""

from datetime import date, datetime
from backend.extensions import db


# ── span_files ────────────────────────────────────────────────────────────────

class SpanFile(db.Model):
    __tablename__ = "span_files"

    id             = db.Column(db.Integer, primary_key=True)
    trade_date     = db.Column(db.Date, unique=True, nullable=False, index=True)
    file_type      = db.Column(db.String(32), nullable=False)  # 'span_spn' | 'udiff_bhavcopy'
    download_url   = db.Column(db.Text)
    downloaded_at  = db.Column(db.DateTime)
    parse_status   = db.Column(db.String(16), default="pending")  # pending|success|error
    error_message  = db.Column(db.Text)

    commodities = db.relationship("CombinedCommodity", back_populates="span_file",
                                  cascade="all, delete-orphan")
    contracts   = db.relationship("Contract", back_populates="span_file",
                                  cascade="all, delete-orphan")
    inter_spreads = db.relationship("InterCommoditySpread", back_populates="span_file",
                                    cascade="all, delete-orphan")
    intra_spreads = db.relationship("IntraCommoditySpread", back_populates="span_file",
                                    cascade="all, delete-orphan")

    def __repr__(self):
        return f"<SpanFile {self.trade_date} {self.file_type} {self.parse_status}>"


# ── combined_commodities ──────────────────────────────────────────────────────

class CombinedCommodity(db.Model):
    __tablename__ = "combined_commodities"
    __table_args__ = (
        db.Index("ix_cc_date_code", "trade_date", "commodity_code"),
    )

    id                        = db.Column(db.Integer, primary_key=True)
    span_file_id              = db.Column(db.Integer, db.ForeignKey("span_files.id"),
                                          nullable=False)
    trade_date                = db.Column(db.Date, nullable=False)
    commodity_code            = db.Column(db.String(32), nullable=False)
    exchange_code             = db.Column(db.String(8), default="NSE")
    price_scan_range          = db.Column(db.Float)   # PSR in INR
    volatility_scan_range     = db.Column(db.Float)   # VSR as fraction (e.g. 0.04)
    inter_month_spread_charge = db.Column(db.Float, default=0.0)
    short_option_min_charge   = db.Column(db.Float, default=0.0)
    exposure_margin_rate      = db.Column(db.Float, default=0.03)  # 0.03 or 0.05
    instrument_type           = db.Column(db.String(8), default="INDEX")  # INDEX | STOCK
    # Whether this commodity's margin comes from the official file or is estimated
    is_estimated              = db.Column(db.Boolean, default=False)

    span_file = db.relationship("SpanFile", back_populates="commodities")

    def __repr__(self):
        return f"<CombinedCommodity {self.commodity_code} {self.trade_date}>"


# ── contracts ─────────────────────────────────────────────────────────────────

class Contract(db.Model):
    __tablename__ = "contracts"
    __table_args__ = (
        db.Index(
            "ix_contract_lookup",
            "trade_date", "symbol", "instrument_type",
            "expiry_date", "strike_price", "option_type",
        ),
        db.UniqueConstraint("trade_date", "contract_key", name="uq_contract_key_date"),
    )

    id              = db.Column(db.Integer, primary_key=True)
    span_file_id    = db.Column(db.Integer, db.ForeignKey("span_files.id"), nullable=False)
    trade_date      = db.Column(db.Date, nullable=False)
    commodity_code  = db.Column(db.String(32), nullable=False)   # underlying symbol
    symbol          = db.Column(db.String(32), nullable=False)   # may differ from commodity_code
    # FUTIDX | OPTIDX | FUTSTK | OPTSTK
    instrument_type = db.Column(db.String(8),  nullable=False)
    expiry_date     = db.Column(db.Date,        nullable=False)
    strike_price    = db.Column(db.Float)        # NULL for futures
    option_type     = db.Column(db.String(4))    # CE | PE, NULL for futures
    lot_size        = db.Column(db.Integer,     nullable=False)
    underlying_price = db.Column(db.Float)       # settlement price of underlying
    future_price    = db.Column(db.Float)        # settlement/last price of this contract
    # Composite unique key:  NIFTY-OPTIDX-20250424-22500-CE
    contract_key    = db.Column(db.String(64),  nullable=False)

    span_file  = db.relationship("SpanFile", back_populates="contracts")
    risk_array = db.relationship("RiskArray", back_populates="contract",
                                 uselist=False, cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Contract {self.contract_key}>"


# ── risk_arrays ───────────────────────────────────────────────────────────────

class RiskArray(db.Model):
    __tablename__ = "risk_arrays"

    id          = db.Column(db.Integer, primary_key=True)
    contract_id = db.Column(db.Integer, db.ForeignKey("contracts.id"),
                            nullable=False, unique=True)
    # 16 scenario values (positive = loss, in INR per lot)
    s01 = db.Column(db.Float, default=0.0)
    s02 = db.Column(db.Float, default=0.0)
    s03 = db.Column(db.Float, default=0.0)
    s04 = db.Column(db.Float, default=0.0)
    s05 = db.Column(db.Float, default=0.0)
    s06 = db.Column(db.Float, default=0.0)
    s07 = db.Column(db.Float, default=0.0)
    s08 = db.Column(db.Float, default=0.0)
    s09 = db.Column(db.Float, default=0.0)
    s10 = db.Column(db.Float, default=0.0)
    s11 = db.Column(db.Float, default=0.0)
    s12 = db.Column(db.Float, default=0.0)
    s13 = db.Column(db.Float, default=0.0)
    s14 = db.Column(db.Float, default=0.0)
    s15 = db.Column(db.Float, default=0.0)   # extreme (+2×PSR, 35 % cover)
    s16 = db.Column(db.Float, default=0.0)   # extreme (−2×PSR, 35 % cover)
    composite_delta = db.Column(db.Float, default=0.0)

    contract = db.relationship("Contract", back_populates="risk_array")

    def as_list(self):
        """Return scenarios as a 16-element list (index 0 = scenario 1)."""
        return [
            self.s01, self.s02, self.s03, self.s04,
            self.s05, self.s06, self.s07, self.s08,
            self.s09, self.s10, self.s11, self.s12,
            self.s13, self.s14, self.s15, self.s16,
        ]


# ── inter_commodity_spreads ───────────────────────────────────────────────────

class InterCommoditySpread(db.Model):
    __tablename__ = "inter_commodity_spreads"

    id              = db.Column(db.Integer, primary_key=True)
    span_file_id    = db.Column(db.Integer, db.ForeignKey("span_files.id"), nullable=False)
    trade_date      = db.Column(db.Date, nullable=False, index=True)
    priority        = db.Column(db.Integer, default=1)   # lower = applied first
    leg1_commodity  = db.Column(db.String(32), nullable=False)
    leg2_commodity  = db.Column(db.String(32), nullable=False)
    credit_rate     = db.Column(db.Float, default=0.0)   # fraction of scan risk to credit
    delta_ratio_leg1 = db.Column(db.Float, default=1.0)
    delta_ratio_leg2 = db.Column(db.Float, default=1.0)

    span_file = db.relationship("SpanFile", back_populates="inter_spreads")


# ── intra_commodity_spreads ───────────────────────────────────────────────────

class IntraCommoditySpread(db.Model):
    __tablename__ = "intra_commodity_spreads"

    id                = db.Column(db.Integer, primary_key=True)
    span_file_id      = db.Column(db.Integer, db.ForeignKey("span_files.id"), nullable=False)
    trade_date        = db.Column(db.Date, nullable=False, index=True)
    commodity_code    = db.Column(db.String(32), nullable=False)
    priority          = db.Column(db.Integer, default=1)
    spread_charge_rate = db.Column(db.Float, default=0.0)  # fraction of scan risk

    span_file = db.relationship("SpanFile", back_populates="intra_spreads")
