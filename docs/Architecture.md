# Architecture

## System Overview

```
NSE Archives (public HTTPS)
       │
       ▼
  downloader.py  ──────────────────────────────────────────────┐
       │  bhavcopy zip                SPAN XML zip             │
       ▼                                    ▼                  │
 bhavcopy_parser.py              span_xml_parser.py            │
       │ Contract rows                  RiskArray rows         │
       │ CombinedCommodity (estimated)  CombinedCommodity      │
       │                                (official PSR/VSR)     │
       └──────────────────────┬─────────────────────────────────┘
                              ▼
                      SQLite — nse_margin.db
                              │
            ┌─────────────────┼─────────────────┐
            ▼                 ▼                 ▼
   /api/instruments/*   /api/margin/     /api/span-status
                        calculate        /api/span/refresh
                              │
                              ▼
                    index.html + ES modules
```

---

## Directory Structure

```
/
├── backend/
│   ├── app.py               # Flask factory; WAL mode; startup thread
│   ├── extensions.py        # Shared db + scheduler singletons
│   ├── models/
│   │   └── db.py            # SQLAlchemy ORM models
│   ├── api/
│   │   ├── instruments.py   # /api/instruments/* blueprints
│   │   ├── margin.py        # /api/margin/calculate blueprint
│   │   └── span_status.py   # /api/span-status and /api/span/refresh
│   ├── margin/
│   │   ├── calculator.py    # Core SPAN engine
│   │   ├── exposure.py      # Exposure margin rules
│   │   ├── spreads.py       # Inter/intra-commodity spread credits
│   │   └── fallback_rates.py# Estimated mode risk array synthesis
│   ├── span/
│   │   ├── downloader.py    # HTTP fetch + caching
│   │   ├── orchestrator.py  # Coordinates download → parse → DB
│   │   ├── bhavcopy_parser.py
│   │   ├── span_xml_parser.py
│   │   ├── isin_map.py      # ISIN lookup for stock underlyings
│   │   └── scheduler.py     # APScheduler cron jobs
│   └── utils/
│       ├── date_utils.py    # Trading day helpers, IST timezone
│       └── http_client.py   # Requests session with retries + cookie seeding
├── frontend/
│   ├── index.html
│   ├── js/
│   │   ├── app.js           # Event wiring, cascading dropdowns
│   │   ├── api.js           # Fetch wrappers
│   │   ├── portfolio.js     # In-memory portfolio state + pub/sub
│   │   ├── ui.js            # All DOM rendering
│   │   └── formatters.js    # INR formatting, date helpers
│   └── css/
│       └── style.css
├── config.py                # All settings (URLs, rates, schedule)
├── run.py                   # Entry point: python run.py
├── data/                    # Runtime data — SQLite DB + cached zips
└── docs/                    # This wiki
```

---

## Database Schema

SQLite file: `data/nse_margin.db`. WAL mode is enabled on every connection via a SQLAlchemy event listener in `app.py`. This allows concurrent reads during writes and prevents "database is locked" errors.

### span_files

Tracks one row per trade date — acts as the root record for all data for that day.

| Column | Type | Notes |
|--------|------|-------|
| `id` | int PK | |
| `trade_date` | date | Unique + indexed |
| `file_type` | str | `"udiff_bhavcopy"` \| `"span_xml+bhavcopy"` |
| `download_url` | text | |
| `downloaded_at` | datetime | |
| `parse_status` | str | `"pending"` \| `"success"` \| `"error"` |
| `error_message` | text | Populated on error |

### combined_commodities

SPAN parameters per underlying per day.

| Column | Type | Notes |
|--------|------|-------|
| `id` | int PK | |
| `span_file_id` | int FK | → span_files |
| `trade_date` | date | Indexed with commodity_code |
| `commodity_code` | str | e.g., `"NIFTY"` |
| `price_scan_range` | float | PSR in INR |
| `volatility_scan_range` | float | VSR as fraction (e.g., 0.04) |
| `short_option_min_charge` | float | SOMC in INR |
| `exposure_margin_rate` | float | 0.02 for index, 0.05 for stock |
| `is_estimated` | bool | True = estimated mode |
| `exchange_code` | str | Default `"NSE"` |
| `instrument_type` | str | `"INDEX"` \| `"STOCK"` |
| `inter_month_spread_charge` | float | Calendar spread charge rate |

### contracts

One row per contract per trade date.

| Column | Type | Notes |
|--------|------|-------|
| `id` | int PK | |
| `span_file_id` | int FK | |
| `trade_date` | date | |
| `commodity_code` | str | Underlying symbol |
| `symbol` | str | Traded symbol (same as commodity_code for NSE) |
| `instrument_type` | str | `FUTIDX` \| `OPTIDX` \| `FUTSTK` \| `OPTSTK` |
| `expiry_date` | date | |
| `strike_price` | float | Null for futures |
| `option_type` | str | `"CE"` \| `"PE"` \| null |
| `lot_size` | int | Contracts per lot |
| `underlying_price` | float | Spot / settlement of underlying |
| `future_price` | float | Settlement price of this contract |
| `prev_settlement` | float | Previous day settlement (variation margin) |
| `underlying_isin` | str | ISIN of underlying stock; null for indices |
| `contract_key` | str | Unique within (trade_date, contract_key) |

Key indices: `ix_contract_lookup` on (trade_date, symbol, instrument_type, expiry_date, strike_price, option_type); unique constraint on (trade_date, contract_key).

### risk_arrays

One row per contract (only populated when SPAN XML is available).

| Column | Type | Notes |
|--------|------|-------|
| `id` | int PK | |
| `contract_id` | int FK (unique) | One risk array per contract |
| `s01` – `s16` | float | Scenario values in INR per lot |
| `composite_delta` | float | Delta for spread matching |

Values are **per lot** (pre-multiplied by `lot_size` at parse time). Positive = loss for a long position.

### inter_commodity_spreads / intra_commodity_spreads

Spread credit/charge rules loaded from the official SPAN file (or initialised from `config.FALLBACK_INTER_SPREADS`).

| Column | Type | Notes |
|--------|------|-------|
| `leg1_commodity` / `leg2_commodity` | str | Underlyings forming the spread pair |
| `credit_rate` | float | Fraction of scan risk to credit (0–1) |
| `delta_ratio_leg1` / `delta_ratio_leg2` | float | Delta ratio for matching |
| `priority` | int | Lower = applied first |

---

## Configuration (`config.py`)

All settings are on the `Config` class. Override via environment variables where noted.

### NSE URLs

| Key | Value | Notes |
|-----|-------|-------|
| `NSE_HOME_URL` | `https://www.nseindia.com` | Cookie seeding |
| `NSE_SPAN_BASE_URL` | `https://nsearchives.nseindia.com/archives/nsccl/span` | |
| `NSE_BHAVCOPY_BASE_URL` | `https://nsearchives.nseindia.com/content/fo` | |
| `NSE_SPAN_URL_PATTERN` | `{base}/nsccl.{date}.s.zip` | |
| `NSE_BHAVCOPY_URL_PATTERN` | `{base}/BhavCopy_NSE_FO_0_0_0_{date}_F_0000.csv.zip` | |

### Margin Rates

| Key | Value | Notes |
|-----|-------|-------|
| `INDEX_EXPOSURE_MARGIN_RATE` | 0.02 | 2% for FUTIDX/OPTIDX |
| `STOCK_EXPOSURE_MARGIN_RATE` | 0.05 | 5% for FUTSTK/OPTSTK |
| `EXTREME_SCENARIO_COVER_FRACTION` | 0.35 | Cover on scenarios 15–16 |

### Fallback PSR Rates

Used when SPAN XML is unavailable:

| Symbol | Rate | Resulting PSR at Nifty 22,500 |
|--------|------|-------------------------------|
| NIFTY, SENSEX | 8% | ₹1,800 |
| BANKNIFTY, FINNIFTY, MIDCPNIFTY | 9% | — |
| All stocks | 15% | — |

### HTTP & Retry

| Key | Default |
|-----|---------|
| `DOWNLOAD_TIMEOUT_CONNECT` | 30s |
| `DOWNLOAD_TIMEOUT_READ` | 120s |
| `DOWNLOAD_MAX_RETRIES` | 3 |
| `DOWNLOAD_BACKOFF_FACTOR` | 5s (→ 5s, 15s, 30s) |

### Scheduler

```python
SPAN_REFRESH_SCHEDULE = [
    {"hour": 18, "minute": 30},
    {"hour": 19, "minute": 0},
    {"hour": 19, "minute": 30},
]
```

### Database

`SQLALCHEMY_DATABASE_URI` defaults to `sqlite:///./data/nse_margin.db`. Override with `DATABASE_URL` env var.

WAL mode and `busy_timeout=10000` are set via a SQLAlchemy event listener in `app.py` — not in config.

---

## Schema Migrations

`app.py:_apply_schema_migrations()` runs idempotent `ALTER TABLE ADD COLUMN` statements on every startup. This handles columns added after initial deployment without requiring a migration framework.

To add a new column:
1. Add the `db.Column` to the ORM model in `models/db.py`.
2. Add the `ALTER TABLE ... ADD COLUMN ...` statement to the `migrations` list in `_apply_schema_migrations`.

---

## Frontend Module Responsibilities

| Module | Responsibility |
|--------|---------------|
| `app.js` | DOM events, cascading dropdowns, orchestration |
| `api.js` | All fetch calls — returns parsed JSON or throws |
| `portfolio.js` | In-memory position list with pub/sub change notifications |
| `ui.js` | All DOM writes — table rendering, result panel, error toasts |
| `formatters.js` | INR formatting, date formatting, contract name formatting |

The frontend uses native ES modules (`type="module"`). No build step required. The instrument-type radio buttons are proxied through a hidden `<select id="type-select">` so `app.js` has a single `.value` check.

---

## Running in Development

```bash
venv\Scripts\activate
python run.py            # http://localhost:5000, debug mode, auto-reload on save
```

To force a data re-fetch after code changes to the parsers:
```bash
curl -X POST http://localhost:5000/api/span/refresh
```

To check what data is loaded:
```bash
curl http://localhost:5000/api/span-status
```
