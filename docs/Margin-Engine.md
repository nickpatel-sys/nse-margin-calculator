# Margin Engine

The margin engine lives in `backend/margin/`. Entry point: `calculate_portfolio_margin()` in `calculator.py`.

---

## Overview

NSE uses the **SPAN** (Standard Portfolio Analysis of Risk) methodology. It evaluates a portfolio's loss across 16 hypothetical market scenarios and charges the worst-case outcome as the initial margin, plus a separate exposure margin on notional value.

The engine processes a list of `PositionRequest` objects and returns a `MarginResult` with per-commodity, per-position, and portfolio-level breakdowns.

---

## Step-by-Step Calculation

### 1. Load Contracts

Each `PositionRequest` carries a `contract_key`. The engine fetches the matching `Contract` and `RiskArray` rows from the database for the given trade date.

### 2. Group by Commodity

All positions on the same underlying (e.g., all NIFTY contracts regardless of expiry or strike) are grouped together into a **commodity group**. SPAN netting happens within each group.

### 3. Per-Commodity SPAN

For each commodity group:

**a. Scenario P&L**

For each of the 16 SPAN scenarios, the net portfolio P&L is computed by summing across all positions:

```
scenario_pnl[s] = Σ  (quantity_in_lots × risk_array[s])
```

`risk_array[s]` is already scaled per lot (stored as `lot_size × per-unit value`). Positive risk array values = loss for a long; the sign convention means **positive = loss for long**.

**b. Worst-case scan risk**

Scenarios 15–16 are extreme (±2 × PSR price move). They receive a **35% cover** — only 35% of the extreme-scenario loss is charged:

```python
adjusted = [
    pnl if s < 14 else pnl * 0.35
    for s, pnl in enumerate(scenario_pnl)
]
scan_risk = max(adjusted)   # highest loss across all 16
```

**c. Short option minimum (SOMC)**

For each short option position:
```
somc_contribution = |quantity| × lot_size × short_option_min_charge
```
`short_option_min_charge` comes from `CombinedCommodity` (official in SPAN mode, approximated at 1.5% of underlying in estimated mode).

**d. Commodity SPAN**
```
commodity_span = max(scan_risk, short_option_minimum)
```

**Special case — all-long-option commodity:** If every position in the commodity is a long option, `scan_risk` is zeroed out (premium already paid caps the maximum loss).

### 4. Intra-Commodity Spread Charges

Calendar spread charges apply when a commodity has positions in multiple expiries with opposite signs (e.g., long near-month + short far-month future). A spread charge rate is applied to the matched spread units.

*The calendar spread logic in `spreads.py` is a simplified implementation; intra-commodity charges from the official SPAN file are loaded but applied at a portfolio level only.*

### 5. Inter-Commodity Spread Credits

After computing per-commodity SPAN, the engine looks for delta-matched hedges **across** commodities. These reduce the total SPAN.

**Pairs used** (from `config.FALLBACK_INTER_SPREADS` or the official file):

| Leg 1 | Leg 2 | Credit | Delta Ratio |
|-------|-------|--------|-------------|
| BANKNIFTY | NIFTY | 50% | 1 : 3 |
| FINNIFTY | NIFTY | 50% | 1 : 2 |
| MIDCPNIFTY | NIFTY | 50% | 1 : 2 |

**Matching logic:**
1. Compute net delta for each commodity from all positions' composite deltas.
2. For each spread pair in priority order, check if the two commodities have **opposite-signed** deltas.
3. Calculate spread units = `min(|delta_leg1| / ratio1, |delta_leg2| / ratio2)`.
4. Credit = `spread_units × credit_rate × min(scan_risk_leg1, scan_risk_leg2)`.
5. Subtract credit from total SPAN; remaining delta carried forward to lower-priority pairs.

### 6. Exposure Margin

Charged per position on notional value:

| Instrument Type | Rate | Notes |
|----------------|------|-------|
| FUTIDX, OPTIDX | 2% | Per NSE circular NSCCL/CMPT/46514 |
| FUTSTK, OPTSTK | 5% | |
| Long options | 0% | Premium is the maximum loss |

```
notional = |quantity| × lot_size × underlying_price
exposure = notional × rate
```

### 7. Total Margin

```
total_margin = span_margin + exposure_margin
```

### 8. Variation Margin (Futures Only)

If `prev_settlement > 0` is provided for a futures position:

```
variation_margin = quantity × lot_size × (today_settlement − prev_settlement)
```

Sign convention:
- **Positive** = gain (today's price moved in your favour) — reduces cash you need to post.
- **Negative** = loss — increases cash requirement.

Portfolio-level total:
```
portfolio_vm = Σ variation_margin  (only positions where prev_settlement was provided)
net_cash_required = total_margin − portfolio_vm
```

---

## Risk Array Format

The `risk_arrays` table stores 16 values (`s01`–`s16`) **per lot** for each contract.

The 16 scenarios represent 7 underlying price moves × 2 volatility levels, plus 2 extreme price scenarios:

| Scenario | Price Move | Vol Move |
|----------|-----------|----------|
| 1 | 0 | Up |
| 2 | 0 | Down |
| 3 | +1/3 PSR | Up |
| 4 | +1/3 PSR | Down |
| 5 | −1/3 PSR | Up |
| 6 | −1/3 PSR | Down |
| 7 | +2/3 PSR | Up |
| 8 | +2/3 PSR | Down |
| 9 | −2/3 PSR | Up |
| 10 | −2/3 PSR | Down |
| 11 | +1 PSR | Up |
| 12 | +1 PSR | Down |
| 13 | −1 PSR | Up |
| 14 | −1 PSR | Down |
| 15 | +2 PSR | (extreme, 35% cover) |
| 16 | −2 PSR | (extreme, 35% cover) |

**Per-unit vs per-lot:** The SPAN XML contains per-unit values. The parser multiplies by `lot_size` before storing, so the calculator simply does `quantity_lots × risk_array[s]`.

---

## Estimated Mode (Fallback)

When the SPAN XML file is unavailable, risk arrays are synthesised in `fallback_rates.py`.

**PSR estimation:**
```
psr_inr = fallback_psr_rate × underlying_price
```

Rates from `config.FALLBACK_PSR_RATES`:

| Symbol | Rate |
|--------|------|
| NIFTY, SENSEX | 8% |
| BANKNIFTY, FINNIFTY, MIDCPNIFTY | 9% |
| All stocks | 15% |

**Scenario values for futures** (delta = 1.0):
```
scenario_value = price_move × underlying_price
```

**Scenario values for options** (using delta approximation):
- ATM delta ≈ 0.5
- ITM/OTM delta interpolated from moneyness: `delta ≈ 0.5 + moneyness × 5` (clamped 0–1)
- `scenario_value = delta × price_move × underlying_price`

This approximation ignores vega (volatility moves) and time decay. In Live SPAN mode the official risk arrays fully capture all Greeks.

---

## Dataclasses

**`PositionRequest`** (input per position):
- `contract_key: str`
- `quantity: int` (signed lots)
- `prev_settlement: float` (default 0.0)

**`PositionResult`** (output per position):
- All fields from the contract
- `worst_scenario: int`, `worst_scenario_loss: float`
- `exposure_margin: float`
- `position_type: str` (`"long_future"`, `"short_future"`, `"long_option"`, `"short_option"`)
- `data_mode: str`
- `underlying_isin: str | None`
- `variation_margin: float | None`

**`MarginResult`** (portfolio output):
- `span_margin: float`
- `exposure_margin: float`
- `total_margin: float`
- `premium_received: float`
- `variation_margin: float`
- `net_cash_required: float`
- `data_mode: str`
- `by_commodity: list[CommodityResult]`
- `by_position: list[PositionResult]`
