# API Reference

All endpoints are under `/api/`. The server runs on `http://localhost:5000` by default.

Dates are always `YYYY-MM-DD` strings. Monetary values are floats in INR. Lot quantities are integers; positive = long, negative = short.

---

## Margin

### POST /api/margin/calculate

Calculate portfolio SPAN + exposure margin.

**Request body**

```json
{
  "trade_date": "2026-04-10",
  "positions": [
    {
      "contract_key": "NIFTY-OPTIDX-20260424-22500-CE",
      "quantity": -2,
      "prev_settlement": 0.0
    }
  ]
}
```

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `trade_date` | string | No | Defaults to most recent trading day |
| `positions` | array | Yes | At least one element |
| `positions[].contract_key` | string | Yes | See contract key format below |
| `positions[].quantity` | int | Yes | Signed: negative = short, positive = long. In lots. |
| `positions[].prev_settlement` | float | No | Previous day settlement price for variation margin; omit or pass `0` to skip |

**Contract key format**

```
{SYMBOL}-{INSTR_TYPE}-{YYYYMMDD}[-{STRIKE}][-{OPT_TYPE}]
```

Examples:
- `NIFTY-FUTIDX-20260424`
- `NIFTY-OPTIDX-20260424-22500-CE`
- `RELIANCE-FUTSTK-20260430`
- `RELIANCE-OPTSTK-20260430-1400-PE`

Strike is omitted for futures. Option type is `CE` or `PE`.

**Response 200**

```json
{
  "trade_date": "2026-04-10",
  "summary": {
    "span_margin": 125000.00,
    "exposure_margin": 18750.00,
    "total_margin": 143750.00,
    "premium_received": 25000.00,
    "variation_margin": 5000.00,
    "net_cash_required": 138750.00,
    "data_mode": "span_file"
  },
  "by_commodity": [
    {
      "commodity": "NIFTY",
      "scan_risk": 130000.00,
      "intra_spread_charge": 0.00,
      "inter_spread_credit": 5000.00,
      "short_option_min": 0.00,
      "commodity_span": 125000.00,
      "exposure_margin": 18750.00
    }
  ],
  "by_position": [
    {
      "contract_key": "NIFTY-OPTIDX-20260424-22500-CE",
      "symbol": "NIFTY",
      "instrument_type": "OPTIDX",
      "expiry_date": "2026-04-24",
      "strike_price": 22500.0,
      "option_type": "CE",
      "side": "sell",
      "lots": 2,
      "lot_size": 75,
      "underlying_price": 22600.00,
      "future_price": 150.50,
      "notional_value": 3390000.00,
      "worst_scenario": 6,
      "worst_scenario_loss": 65000.00,
      "exposure_margin": 0.00,
      "position_type": "short_option",
      "data_mode": "span_file",
      "underlying_isin": null,
      "variation_margin": null
    }
  ]
}
```

**`summary.data_mode`** values:

| Value | Meaning |
|-------|---------|
| `"span_file"` | All positions used official SPAN risk arrays |
| `"estimated"` | All positions used fallback PSR approximation |
| `"mixed"` | Some positions official, some estimated |

**`by_position[].variation_margin`**: `null` for options or when `prev_settlement` was not provided. For futures with `prev_settlement > 0`: `quantity × lot_size × (today_settle − prev_settlement)`.

**Error responses**

| Code | Cause |
|------|-------|
| 400 | Missing `positions`, invalid `trade_date`, or `quantity == 0` |
| 422 | Contract key not found in database for the given trade date |

---

## Instruments

### GET /api/instruments/symbols

List all underlying symbols available for a trade date.

**Query params**: `date` (optional, defaults to most recent trading day)

**Response**
```json
{
  "symbols": ["BANKNIFTY", "NIFTY", "RELIANCE", ...],
  "trade_date": "2026-04-10"
}
```

Symbols are sorted alphabetically.

---

### GET /api/instruments/expiries

List expiry dates for a symbol.

**Query params**: `symbol` (required), `date` (optional)

**Response**
```json
{
  "symbol": "NIFTY",
  "expiries": ["2026-04-24", "2026-05-28", "2026-06-25"],
  "trade_date": "2026-04-10"
}
```

Both futures and options expiries are included; sorted ascending.

---

### GET /api/instruments/strikes

List available strikes for a symbol + expiry combination (options only).

**Query params**: `symbol`, `expiry` (YYYY-MM-DD), `date` (optional)

**Response**
```json
{
  "symbol": "NIFTY",
  "expiry_date": "2026-04-24",
  "strikes": [
    {
      "strike": 22400.0,
      "ce_key": "NIFTY-OPTIDX-20260424-22400-CE",
      "pe_key": "NIFTY-OPTIDX-20260424-22400-PE"
    },
    {
      "strike": 22450.0,
      "ce_key": "NIFTY-OPTIDX-20260424-22450-CE",
      "pe_key": "NIFTY-OPTIDX-20260424-22450-PE"
    }
  ]
}
```

Strikes are sorted ascending. CE or PE key is `null` if that option doesn't exist in the database.

---

### GET /api/instruments/futures

List all futures contracts for a symbol.

**Query params**: `symbol`, `date` (optional)

**Response**
```json
{
  "symbol": "NIFTY",
  "futures": [
    {
      "contract_key": "NIFTY-FUTIDX-20260424",
      "symbol": "NIFTY",
      "instrument_type": "FUTIDX",
      "expiry_date": "2026-04-24",
      "strike_price": null,
      "option_type": null,
      "lot_size": 75,
      "underlying_price": 22600.00,
      "future_price": 22615.00,
      "prev_settlement": 22580.00,
      "underlying_isin": null,
      "commodity_code": "NIFTY"
    }
  ]
}
```

`prev_settlement` is the previous day's settlement price from the bhavcopy — useful as the default for Prev. Close.

---

### GET /api/instruments/contract/{contract_key}

Fetch a single contract by its key.

**Query params**: `date` (optional)

**Response**: Same structure as a single element from `futures` response above.

**Error**: `404` if not found.

---

### GET /api/instruments/search

Prefix search across all contracts.

**Query params**: `q` (search term), `limit` (default 50, max 200), `date` (optional)

**Response**
```json
{
  "results": [
    {
      "contract_key": "NIFTY-FUTIDX-20260424",
      "symbol": "NIFTY",
      "instrument_type": "FUTIDX",
      "expiry_date": "2026-04-24",
      "lot_size": 75
    }
  ]
}
```

Results are ordered by symbol, expiry, strike.

---

## Data Management

### GET /api/span-status

Returns the current state of loaded market data.

**Response**
```json
{
  "status": "success",
  "trade_date": "2026-04-10",
  "file_type": "span_xml+bhavcopy",
  "downloaded_at": "2026-04-10T18:35:22",
  "instrument_count": 45066,
  "risk_array_count": 45066,
  "is_stale": false,
  "data_mode": "span_file"
}
```

| Field | Values |
|-------|--------|
| `status` | `"success"` \| `"pending"` \| `"error"` \| `"no_data"` |
| `file_type` | `"span_xml+bhavcopy"` \| `"udiff_bhavcopy"` |
| `risk_array_count` | 0 in estimated mode; equals `instrument_count` in span_file mode |
| `is_stale` | `true` if `trade_date` is earlier than the most recent trading day |
| `data_mode` | `"span_file"` \| `"estimated"` |

---

### POST /api/span/refresh

Trigger a manual download and parse of today's SPAN data. Always re-fetches even if data already exists.

**No request body required.**

**Response**: Same structure as `GET /api/span-status`.

This endpoint is synchronous — it returns after the full download + parse completes. May take 15–60 seconds depending on network speed.
