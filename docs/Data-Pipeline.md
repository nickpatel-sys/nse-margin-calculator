# Data Pipeline

NSEMargins fetches data from two NSE archive endpoints and stores it in a local SQLite database. The pipeline runs automatically via scheduler and on startup; it can also be triggered manually via the API.

---

## Sources

### 1. UDiFF Bhavcopy (Required)

**URL pattern:**
```
https://nsearchives.nseindia.com/content/fo/BhavCopy_NSE_FO_0_0_0_{YYYYMMDD}_F_0000.csv.zip
```

**Provides:**
- Complete list of all F&O contracts for the trade date
- Lot sizes (`NewBrdLotQty`)
- Settlement prices and previous day close prices
- Underlying (spot) prices
- Instrument types (futures / options)

**Cached to:** `data/fo_bhavcopy_{YYYYMMDD}.csv.zip`

This source **must** succeed. If the bhavcopy download fails, the pipeline aborts for that date.

### 2. SPAN XML (Supplementary, Opportunistic)

**URL pattern:**
```
https://nsearchives.nseindia.com/archives/nsccl/span/nsccl.{YYYYMMDD}.s.zip
```

**Provides:**
- Official 16-scenario SPAN risk arrays for every contract
- Official Price Scan Range (PSR) and Volatility Scan Range (VSR) per underlying
- Composite delta per contract (for spread matching)

**Cached to:** `data/span_{YYYYMMDD}.zip`

If this download fails (network error, 404, etc.), the pipeline logs the failure and continues in Estimated mode using the bhavcopy data. Margin calculations will use fallback PSR rates instead of official risk arrays.

### 3. ISIN Map (One-time, Cached)

**Source:** NSE EQUITY_L.csv (downloaded once, cached in `data/equity_isin.json`)

**Provides:** ISIN codes for stock underlyings (FUTSTK / OPTSTK). Index instruments receive a `null` ISIN.

---

## Processing Order

```
download_for_date()
  ├─ Fetch bhavcopy zip  ──────────► data/fo_bhavcopy_{date}.csv.zip
  └─ Fetch SPAN XML zip ───────────► data/span_{date}.zip  (if available)
       ↓
orchestrator.parse_downloaded_file()
  ├─ bhavcopy_parser.parse_bhavcopy()
  │    └─ Creates Contract rows + CombinedCommodity rows (estimated PSR)
  └─ span_xml_parser.parse_span_xml()  (only if span zip exists)
       └─ Creates RiskArray rows + updates CombinedCommodity with official PSR/VSR
            └─ Sets is_estimated = False on each CombinedCommodity
```

The bhavcopy step is always executed first because the SPAN XML parser relies on `Contract` rows already being present to match risk arrays.

---

## Bhavcopy Parser (`bhavcopy_parser.py`)

**Input:** CSV extracted from the bhavcopy zip.

**Instrument type mapping:**

| CSV code | Internal type | Description |
|----------|---------------|-------------|
| `IDF` | `FUTIDX` | Index Future |
| `IDO` | `OPTIDX` | Index Option |
| `STF` | `FUTSTK` | Stock Future |
| `STO` | `OPTSTK` | Stock Option |

**Contract key construction:**
```
{symbol}-{instrument_type}-{YYYYMMDD}[-{strike}][-{option_type}]
```

Strike formatting: trailing zeros removed (`22500.0` → `"22500"`), but half-integers preserved (`72.5` stays `"72.5"`).

**On re-parse (force refresh):** Existing `Contract` rows for the trade date are deleted and re-inserted. Existing `RiskArray` rows are deleted via the SPAN XML parser that runs next.

---

## SPAN XML Parser (`span_xml_parser.py`)

**Input:** `nsccl.{YYYYMMDD}.s.spn` (XML file inside the SPAN zip).

**XML structure:**
```xml
<spanFile>
  <pointInTime>
    <clearingOrg>
      <exchange>
        <futPf>            <!-- one per underlying -->
          <pfCode>NIFTY</pfCode>
          <fut>            <!-- one per expiry -->
            <pe>20260424</pe>
            <ra><a>-2200</a>...<a>3100</a><d>0.97</d></ra>
            <scanRate><priceScan>2236.71</priceScan><volScan>0.04</volScan></scanRate>
          </fut>
        </futPf>
        <oopPf>            <!-- options -->
          <pfCode>NIFTY</pfCode>
          <series>         <!-- one per expiry -->
            <pe>20260424</pe>
            <opt>          <!-- one per strike × option type -->
              <o>C</o>     <!-- C = CE, P = PE -->
              <k>22500</k>
              <ra><a>...</a>...<d>0.42</d></ra>
            </opt>
          </series>
        </oopPf>
      </exchange>
    </clearingOrg>
  </pointInTime>
</spanFile>
```

**Key behaviours:**
- Risk array values in XML are **per unit** (per single contract). The parser multiplies by `lot_size` before storing.
- Positive value = loss for a long position.
- `<d>` inside `<ra>` is the composite delta used for inter-commodity spread matching.
- Lookup uses an in-memory dict built from all 45K contracts in a single query — avoids per-entry DB queries.
- Bulk insert via a single `executemany`-style raw SQL call for performance.

**After parsing:** `CombinedCommodity.is_estimated` is set to `False` for every underlying that appeared in the SPAN file.

---

## Scheduler

Three APScheduler cron jobs run **Monday–Friday** in IST:

| Time (IST) | Behaviour |
|------------|-----------|
| 18:30 | Primary — forces refresh even if data already present |
| 19:00 | Retry — skips if already succeeded |
| 19:30 | Final retry — skips if already succeeded |

All jobs call `refresh_for_date(today_ist(), force=True/False)`.

NSE typically publishes bhavcopy by 18:00–18:15 and the SPAN XML by 18:30–18:45. The 19:00 and 19:30 retries ensure the SPAN XML is captured even if it is delayed.

---

## Startup Data Load

On server start, a daemon thread attempts to load data for the most recent trading day:

1. If `already_downloaded(trade_date)` returns `True` — skips silently.
2. Otherwise calls `download_for_date()` → `parse_downloaded_file()`.

The thread runs only in the Werkzeug reloader **child** process (guarded by `WERKZEUG_RUN_MAIN == "true"`) to prevent double-execution during debug-mode hot reloads.

---

## HTTP Client Behaviour (`http_client.py`)

- Visits `nseindia.com` home page first to seed the session cookie NSE requires for archive access.
- If the cookie seeding returns a 403, a warning is logged and the archive download still proceeds (often succeeds anyway).
- Retry policy: up to 3 attempts with backoff of 5s, 15s, 30s.
- Timeouts: 30s connect, 120s read.

---

## Caching & Skip Logic

| Condition | Behaviour |
|-----------|-----------|
| `SpanFile.parse_status == 'success'` | `already_downloaded()` returns `True`; startup + scheduler skip |
| Bhavcopy zip already on disk | Re-uses cached file; no re-download |
| SPAN XML zip already on disk | Re-uses cached file; no re-download |
| `force=True` (manual refresh) | Deletes existing data for the date and re-parses from cache (or re-downloads if zip missing) |

Cached zip files in `data/` are never automatically deleted. They serve as a replay buffer if re-parsing is needed.
