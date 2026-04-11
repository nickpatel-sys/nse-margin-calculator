# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Activate virtualenv (Windows)
venv\Scripts\activate

# Run development server (debug + auto-reload)
py -3 run.py
# or after activating venv:
python run.py

# Install/update dependencies
venv\Scripts\pip install -r requirements.txt

# Force re-download and re-parse today's SPAN data (via API)
curl -X POST http://localhost:5000/api/span/refresh

# Check data status
curl http://localhost:5000/api/span-status
```

There are no automated tests at this time.

## Documentation

Full feature documentation lives in `docs/`:
- `docs/Home.md` — overview and quick start
- `docs/User-Guide.md` — UI walkthrough
- `docs/API-Reference.md` — all REST endpoints
- `docs/Margin-Engine.md` — SPAN calculation logic
- `docs/Data-Pipeline.md` — NSE data sources and parsers
- `docs/Architecture.md` — DB schema, config, directory layout

**Keep the wiki up to date** when making material changes: new API fields, new config keys, changes to margin calculation logic, new data sources, or schema migrations.

## Architecture

**Data flow:** NSE archive → `downloader.py` → zip on disk → parser → SQLite → calculator → REST API → frontend JS

### Backend (`backend/`)

**`app.py`** — Flask factory. Registers blueprints, calls `db.create_all()` (models must be imported first — done via `import backend.models.db`), starts APScheduler, and spawns a startup thread to load today's data. Uses `WERKZEUG_RUN_MAIN` env var to prevent double-execution in debug mode.

**`extensions.py`** — Shared `db` (SQLAlchemy) and `scheduler` (APScheduler) singletons. Must be imported before models to avoid circular imports.

**`config.py`** — Single source of truth for all settings: NSE URL patterns, fallback PSR rates, exposure margin rates, scheduler times, retry config.

**Data pipeline (`span/`):**
- `downloader.py` — Seeds an NSE session cookie (required), tries SPAN SPN URL first, falls back to UDiFF bhavcopy. Caches zip to `data/` dir; skips if DB already shows `parse_status='success'`.
- `parser.py` — Parses SP4 fixed-width SPAN records (types 0–5). Field offsets are approximate and may need live verification against a real SPN file.
- `bhavcopy_parser.py` — Parses UDiFF CSV. Instrument type codes: `IDF`=FUTIDX, `IDO`=OPTIDX, `STF`=FUTSTK, `STO`=OPTSTK. Lot size column is `NewBrdLotQty`. Strike keys are formatted as `f"{strike:.2f}".rstrip("0").rstrip(".")` to handle half-integer strikes (e.g. 72.5).
- `orchestrator.py` — Coordinates download → parse → DB update. Called by scheduler, API endpoint, and startup thread.
- `scheduler.py` — APScheduler cron jobs at 18:30, 19:00, 19:30 IST Mon–Fri.

**Margin engine (`margin/`):**
- `calculator.py` — Core: groups positions by commodity, computes net P&L across 16 SPAN scenarios per commodity, applies 35% cover fraction to extreme scenarios 15–16, calculates short-option minimum, then applies inter-spread credits via `spreads.py`. Returns `MarginResult` dataclass.
- `fallback_rates.py` — When no official risk arrays exist: approximates PSR from `config.FALLBACK_PSR_RATES`, constructs synthetic 16-scenario arrays using delta approximation. Used when bhavcopy mode is active (no SPN file).
- `exposure.py` — 3% of notional for index derivatives, 5% for stock derivatives. Long options pay zero exposure margin.
- `spreads.py` — Inter-commodity spread credits (delta-matched pairs, e.g. BankNifty↔Nifty) and intra-commodity calendar spread charges.

**API blueprints (`api/`):** All under `/api/`. Thin layer — validates request, calls into `calculator.py` or queries DB directly, serializes response. No business logic lives here.

### Database (SQLite, `data/nse_margin.db`)

Six tables via SQLAlchemy ORM (`models/db.py`):
- `span_files` — one row per trade date, tracks download/parse status
- `combined_commodities` — per-underlying SPAN parameters (PSR, VSR, SOMC, exposure rate)
- `contracts` — every F&O contract; unique key is `(trade_date, contract_key)` where `contract_key = "SYMBOL-INSTRTYPE-YYYYMMDD[-STRIKE][-OPTTYPE]"`
- `risk_arrays` — 16 scenario values (s01–s16) per contract; only populated from SPN files, not bhavcopy
- `inter_commodity_spreads` / `intra_commodity_spreads` — spread credit/charge rules from SPN file

### Frontend (`frontend/`)

ES modules (no build step). `index.html` loads `app.js` as `type="module"`. The instrument-type radio buttons are proxied through a hidden `<select id="type-select">` so `app.js` can use a single `.value` check.

Module responsibilities: `api.js` — fetch wrappers; `portfolio.js` — in-memory positions array with pub/sub; `ui.js` — all DOM rendering; `formatters.js` — Indian number formatting (`formatINR`) and date helpers; `app.js` — event wiring and cascading dropdowns (symbol → expiry → strike).

### Key operational notes

- **SPAN SPN file**: NSE's public archives return 404 for the SPN file; the app runs in **Estimated** mode using bhavcopy + approximate PSR rates. If the SPN file becomes accessible, the full 16-scenario calculation uses official risk arrays automatically.
- **NSE session cookie**: `http_client.py` visits `nseindia.com` first to seed cookies. NSE may return 403 on this seed step (logged as WARNING) — the archive download still proceeds and often succeeds anyway.
- **Reloader guard**: The startup data-load thread only runs in the Werkzeug reloader child process (`WERKZEUG_RUN_MAIN == "true"`) to prevent duplicate downloads during debug-mode restarts.
