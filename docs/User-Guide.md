# User Guide

## Starting the App

```bash
venv\Scripts\activate
python run.py
```

Open **http://localhost:5000** in a browser. The server loads data for the most recent trading day on startup; a green "Live" or orange "Estimated" chip appears in the header once data is ready.

---

## Status Indicators

The chip in the top-right corner shows the current data state:

| Chip | Meaning |
|------|---------|
| **Live** (green) | Official SPAN XML loaded — margins match NSE exactly |
| **Estimated** (orange) | Only bhavcopy available — PSR approximated from config rates |
| **Stale** (yellow) | Data is from a prior trading day |
| **No Data** (red) | Nothing loaded yet; use Refresh Data |

Click **Refresh Data** to trigger a manual re-download of today's data.

---

## Adding Positions

### Options

1. Select a **Symbol** (e.g., NIFTY, BANKNIFTY, RELIANCE).
2. Select **Option** type (default).
3. Pick an **Expiry** date from the dropdown.
4. Pick a **Strike** — all available strikes for that expiry are listed.
5. Choose **CE** (call) or **PE** (put).
6. Set **Side**: Buy (long) or Sell (short). Default is Sell.
7. Set **Lots** (1–5000).
8. Click **Add to Portfolio** or press Enter.

### Futures

1. Select a **Symbol**.
2. Select **Future** type.
3. Pick an **Expiry**.
4. Set **Side** and **Lots**.
5. Click **Add to Portfolio**.

### Merging Duplicate Positions

Adding the same contract key + side combination again adds to the existing lot count rather than creating a duplicate row.

---

## The Portfolio Table

Each row shows the contract description, lot count, side, expiry, and estimated notional value.

**Prev. Close** (futures only): Enter yesterday's settlement price to calculate variation margin. Leave blank to omit variation margin from the result.

**Remove** (✕ button): Removes a single position.  
**Clear All**: Empties the entire portfolio.

---

## Calculating Margin

Click **Calculate Margin** once at least one position is in the portfolio.

### Summary Panel

| Field | Description |
|-------|-------------|
| **SPAN Margin** | Worst-case scenario loss across all commodities after spread credits |
| **Exposure Margin** | 2% (index) or 5% (stock) of notional; zero for long options |
| **Total Margin** | SPAN + Exposure |
| **Premium Received** | Cash received for short option positions (already in your account) |
| **Variation Margin** | MTM gain (+) or loss (−) on futures since previous close; shown only when Prev. Close is filled |
| **Net Cash Required** | Total Margin − Variation Margin gain (the actual cash you need to post today) |

The **data mode badge** shows whether the result used official SPAN data or estimated rates.

### By-Commodity Breakdown

One card per underlying showing:
- **Scan Risk** — gross SPAN before spread credits
- **Short Option Min** — minimum charge floor for short option positions
- **Inter-spread Credit** — credit for hedged positions across underlyings (e.g., long NIFTY future + short BANKNIFTY future)
- **SPAN** — final commodity-level SPAN = max(Scan Risk, Short Option Min) − inter-spread credit
- **Exposure** — exposure margin for this commodity

### By-Position Table

One row per position with:
- Contract name, expiry, lots, side
- Notional value
- Worst scenario number (1–16) and the loss for that scenario
- Exposure margin for the position
- Variation margin (futures with Prev. Close filled)
- ISIN (stock derivatives only)
- Data mode (Live / Estimated)

---

## Variation Margin (MTM)

Variation margin is the daily mark-to-market cash flow on futures positions that NSE collects or credits each evening.

- **Gain** (today's price > yesterday's close on a long, or < on a short): shown as a positive number. It reduces Net Cash Required because you receive cash from the exchange.
- **Loss** (price moved against you): shown as a negative number. It increases Net Cash Required.

**Example:** Short 1 lot NIFTY future. Yesterday's close ₹22,400, today's settlement ₹22,200.  
VM = 1 lot × 75 (lot size) × (22,200 − 22,400) = −₹15,000 (loss — you must pay this).  
Net Cash Required = Total Margin + 15,000.

---

## Tips

- **Multiple expiries** on the same underlying are grouped as one commodity for SPAN — calendar spreads and intra-commodity interactions are applied automatically.
- **Long options** pay no exposure margin (the premium is the maximum loss).
- **Mixed portfolios** (both long and short options on the same underlying) benefit from SPAN netting — the worst combined scenario is less than the sum of individual worsts.
- If the result shows **Estimated** mode, margins may differ slightly from broker/exchange values. Refresh Data after 18:30 IST to get official SPAN data.
