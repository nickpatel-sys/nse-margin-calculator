"use strict";

/**
 * Main application controller.
 *
 * Wires all modules together, manages the Add-Position form state,
 * cascading dropdowns (symbol → expiry → strike), and the Calculate button.
 */

import * as API from "./api.js";
import * as Portfolio from "./portfolio.js";
import * as UI from "./ui.js";
import { formatDateShort } from "./formatters.js";

let _tradeDate = null;          // ISO date string, e.g. "2025-04-10"
let _spanStatus = null;

// ── Bootstrap ─────────────────────────────────────────────────────────────────

document.addEventListener("DOMContentLoaded", async () => {
  _tradeDate = new Date().toISOString().slice(0, 10);

  // Populate symbol list and check SPAN status in parallel
  const [symbols, status] = await Promise.allSettled([
    API.listSymbols(_tradeDate),
    API.getSpanStatus(),
  ]);

  _spanStatus = status.status === "fulfilled" ? status.value : null;
  UI.renderStatusBanner(_spanStatus);

  if (_spanStatus?.trade_date) {
    _tradeDate = _spanStatus.trade_date;
  }

  if (symbols.status === "fulfilled") {
    _populateSymbolDropdown(symbols.value.symbols || []);
  }

  // Portfolio updates
  Portfolio.subscribe((positions) => {
    UI.renderPortfolioTable(positions);
    UI.hideResult();
  });
  UI.renderPortfolioTable([]);

  // Event listeners
  document.getElementById("symbol-select").addEventListener("change", _onSymbolChange);
  document.getElementById("type-select").addEventListener("change", _onTypeChange);
  document.getElementById("expiry-select").addEventListener("change", _onExpiryChange);
  document.getElementById("side-buy").addEventListener("change", () => {});
  document.getElementById("side-sell").addEventListener("change", () => {});

  document.getElementById("lots-minus").addEventListener("click", () => _adjustLots(-1));
  document.getElementById("lots-plus").addEventListener("click", () => _adjustLots(+1));
  document.getElementById("lots-input").addEventListener("change", _clampLots);

  document.getElementById("add-btn").addEventListener("click", _onAddPosition);
  document.getElementById("calc-btn").addEventListener("click", _onCalculate);
  document.getElementById("clear-btn").addEventListener("click", () => Portfolio.clearPortfolio());
  document.getElementById("refresh-btn").addEventListener("click", _onRefresh);

  // Allow Enter key to add position
  document.getElementById("add-position-form").addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); _onAddPosition(); }
  });
});

// ── Symbol dropdown ───────────────────────────────────────────────────────────

function _populateSymbolDropdown(symbols) {
  const sel = document.getElementById("symbol-select");
  sel.innerHTML = '<option value="">— Select —</option>';
  // Common indices first
  const priority = ["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"];
  const sorted = [
    ...priority.filter((s) => symbols.includes(s)),
    ...symbols.filter((s) => !priority.includes(s)),
  ];
  sorted.forEach((sym) => {
    const opt = document.createElement("option");
    opt.value = sym;
    opt.textContent = sym;
    sel.appendChild(opt);
  });
}

async function _onSymbolChange() {
  const symbol = document.getElementById("symbol-select").value;
  _resetExpiry();
  _resetStrike();
  if (!symbol) return;

  const type = document.getElementById("type-select").value;
  if (type === "option") {
    await _loadExpiries(symbol);
  } else {
    await _loadFutures(symbol);
  }
}

async function _onTypeChange() {
  const symbol = document.getElementById("symbol-select").value;
  _resetExpiry();
  _resetStrike();

  const type = document.getElementById("type-select").value;
  const strikeRow = document.getElementById("strike-row");
  const optTypeRow = document.getElementById("opt-type-row");

  if (type === "option") {
    strikeRow.hidden = false;
    optTypeRow.hidden = false;
    if (symbol) await _loadExpiries(symbol);
  } else {
    strikeRow.hidden = true;
    optTypeRow.hidden = true;
    if (symbol) await _loadFutures(symbol);
  }
}

async function _onExpiryChange() {
  const symbol = document.getElementById("symbol-select").value;
  const expiry = document.getElementById("expiry-select").value;
  const type = document.getElementById("type-select").value;
  _resetStrike();
  if (!symbol || !expiry || type !== "option") return;
  await _loadStrikes(symbol, expiry);
}

async function _loadExpiries(symbol) {
  try {
    const data = await API.listExpiries(symbol, _tradeDate);
    const sel = document.getElementById("expiry-select");
    sel.innerHTML = '<option value="">— Expiry —</option>';
    (data.expiries || []).forEach((e) => {
      const opt = document.createElement("option");
      opt.value = e;
      opt.textContent = formatDateShort(e);
      sel.appendChild(opt);
    });
  } catch (err) {
    UI.showError("Could not load expiries: " + err.message);
  }
}

async function _loadFutures(symbol) {
  try {
    const data = await API.listFutures(symbol, _tradeDate);
    const sel = document.getElementById("expiry-select");
    sel.innerHTML = '<option value="">— Expiry —</option>';
    (data.futures || []).forEach((f) => {
      const opt = document.createElement("option");
      opt.value = f.expiry_date;
      opt.dataset.contractKey = f.contract_key;
      opt.textContent = formatDateShort(f.expiry_date);
      sel.appendChild(opt);
    });
  } catch (err) {
    UI.showError("Could not load futures: " + err.message);
  }
}

async function _loadStrikes(symbol, expiry) {
  try {
    const data = await API.listStrikes(symbol, expiry, _tradeDate);
    const sel = document.getElementById("strike-select");
    sel.innerHTML = '<option value="">— Strike —</option>';
    (data.strikes || []).forEach((s) => {
      const opt = document.createElement("option");
      opt.value = JSON.stringify({ ce_key: s.ce_key, pe_key: s.pe_key, strike: s.strike });
      opt.textContent = s.strike;
      sel.appendChild(opt);
    });
  } catch (err) {
    UI.showError("Could not load strikes: " + err.message);
  }
}

function _resetExpiry() {
  const sel = document.getElementById("expiry-select");
  sel.innerHTML = '<option value="">— Expiry —</option>';
}
function _resetStrike() {
  const sel = document.getElementById("strike-select");
  sel.innerHTML = '<option value="">— Strike —</option>';
}

// ── Lots control ──────────────────────────────────────────────────────────────

function _adjustLots(delta) {
  const input = document.getElementById("lots-input");
  const val = Math.max(1, (parseInt(input.value, 10) || 1) + delta);
  input.value = val;
}
function _clampLots() {
  const input = document.getElementById("lots-input");
  input.value = Math.max(1, parseInt(input.value, 10) || 1);
}

// ── Add Position ──────────────────────────────────────────────────────────────

async function _onAddPosition() {
  const symbol = document.getElementById("symbol-select").value;
  const type = document.getElementById("type-select").value;
  const expiry = document.getElementById("expiry-select").value;
  const side = document.querySelector('input[name="side"]:checked')?.value || "sell";
  const lots = Math.max(1, parseInt(document.getElementById("lots-input").value, 10) || 1);

  if (!symbol) { UI.showError("Please select a symbol."); return; }
  if (!expiry) { UI.showError("Please select an expiry."); return; }

  let contractKey;

  if (type === "future") {
    // Get contract_key from expiry option's data attribute
    const expiryOpt = document.querySelector(`#expiry-select option[value="${expiry}"]`);
    contractKey = expiryOpt?.dataset.contractKey;
    if (!contractKey) {
      // Build it from symbol and expiry
      const instrType = _INDEX_SYMBOLS.has(symbol) ? "FUTIDX" : "FUTSTK";
      contractKey = `${symbol}-${instrType}-${expiry.replace(/-/g, "")}`;
    }
  } else {
    // Option
    const strikeVal = document.getElementById("strike-select").value;
    const optType = document.querySelector('input[name="opt-type"]:checked')?.value || "CE";
    if (!strikeVal) { UI.showError("Please select a strike price."); return; }
    const { ce_key, pe_key } = JSON.parse(strikeVal);
    contractKey = optType === "CE" ? ce_key : pe_key;
    if (!contractKey) { UI.showError("No contract found for this strike/option type."); return; }
  }

  // Fetch contract details for display
  try {
    const contract = await API.getContract(contractKey, _tradeDate);
    const signedQty = side === "sell" ? -lots : lots;

    Portfolio.addPosition({
      contract_key: contractKey,
      symbol: contract.symbol,
      instrument_type: contract.instrument_type,
      expiry_date: contract.expiry_date,
      strike_price: contract.strike_price,
      option_type: contract.option_type,
      lot_size: contract.lot_size,
      underlying_price: contract.underlying_price,
      future_price: contract.future_price,
      quantity: signedQty,
      side,
    });
  } catch (err) {
    UI.showError("Could not load contract details: " + err.message);
  }
}

const _INDEX_SYMBOLS = new Set([
  "NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTYNXT50", "SENSEX", "BANKEX"
]);

// ── Calculate ─────────────────────────────────────────────────────────────────

async function _onCalculate() {
  const positions = Portfolio.getPositions();
  if (positions.length === 0) { UI.showError("Add at least one position."); return; }

  UI.setLoading(true);
  try {
    const payload = Portfolio.toApiPayload(_tradeDate);
    const result = await API.calculateMargin(payload.positions, payload.trade_date);
    UI.renderMarginResult(result);
  } catch (err) {
    UI.showError("Calculation error: " + err.message);
  } finally {
    UI.setLoading(false);
  }
}

// ── Refresh ───────────────────────────────────────────────────────────────────

async function _onRefresh() {
  const btn = document.getElementById("refresh-btn");
  btn.disabled = true;
  btn.textContent = "Refreshing…";
  try {
    await API.triggerSpanRefresh();
    _spanStatus = await API.getSpanStatus();
    UI.renderStatusBanner(_spanStatus);

    // Reload symbols after refresh
    const data = await API.listSymbols(_tradeDate);
    _populateSymbolDropdown(data.symbols || []);
  } catch (err) {
    UI.showError("Refresh failed: " + err.message);
  } finally {
    btn.disabled = false;
    btn.textContent = "Refresh Data";
  }
}
