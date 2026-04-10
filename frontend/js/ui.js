"use strict";

import {
  formatINR, formatDate, formatDateShort,
  formatContractName, dataModeClass, dataModeLabel,
} from "./formatters.js";
import { removePosition, clearPortfolio } from "./portfolio.js";

// ── Status Banner ─────────────────────────────────────────────────────────────

export function renderStatusBanner(spanStatus) {
  const banner = document.getElementById("status-banner");
  const chip = document.getElementById("status-chip");
  const statusDate = document.getElementById("status-date");

  if (!spanStatus || spanStatus.status === "no_data") {
    banner.className = "banner banner-warn";
    banner.hidden = false;
    banner.querySelector(".banner-text").textContent =
      "No market data loaded. Click Refresh to download today's NSE SPAN file.";
    chip.textContent = "No Data";
    chip.className = "status-chip chip-warn";
    statusDate.textContent = "";
    return;
  }

  statusDate.textContent = spanStatus.trade_date
    ? "Data: " + formatDate(spanStatus.trade_date)
    : "";

  if (spanStatus.is_stale) {
    banner.className = "banner banner-warn";
    banner.hidden = false;
    banner.querySelector(".banner-text").textContent =
      `Data is from ${formatDate(spanStatus.trade_date)} — click Refresh to update.`;
    chip.textContent = "Stale";
    chip.className = "status-chip chip-warn";
  } else if (spanStatus.data_mode === "estimated") {
    banner.className = "banner banner-info";
    banner.hidden = false;
    banner.querySelector(".banner-text").textContent =
      "SPAN file unavailable from NSE. Margins are estimated using approximate rates.";
    chip.textContent = "Estimated";
    chip.className = "status-chip chip-info";
  } else {
    banner.hidden = true;
    chip.textContent = "Live";
    chip.className = "status-chip chip-live";
  }
}

// ── Portfolio Table ───────────────────────────────────────────────────────────

export function renderPortfolioTable(positions) {
  const tbody = document.getElementById("portfolio-tbody");
  const emptyRow = document.getElementById("portfolio-empty");
  const calcBtn = document.getElementById("calc-btn");
  const clearBtn = document.getElementById("clear-btn");

  tbody.innerHTML = "";

  if (positions.length === 0) {
    emptyRow.hidden = false;
    calcBtn.disabled = true;
    clearBtn.disabled = true;
    return;
  }

  emptyRow.hidden = true;
  calcBtn.disabled = false;
  clearBtn.disabled = false;

  positions.forEach((pos, idx) => {
    const notional = Math.abs(pos.quantity) * pos.lot_size * (pos.underlying_price || 0);
    const sideClass = pos.side === "sell" ? "side-sell" : "side-buy";
    const typeLabel = _instrLabel(pos.instrument_type);

    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td class="col-contract">
        <span class="contract-name">${formatContractName(pos)}</span>
        <span class="instr-badge badge-${pos.instrument_type.toLowerCase()}">${typeLabel}</span>
      </td>
      <td class="col-lots">${Math.abs(pos.quantity)}</td>
      <td class="col-side"><span class="${sideClass}">${pos.side.toUpperCase()}</span></td>
      <td class="col-expiry">${formatDateShort(pos.expiry_date)}</td>
      <td class="col-notional">${formatINR(notional)}</td>
      <td class="col-remove">
        <button class="btn-remove" data-idx="${idx}" title="Remove">×</button>
      </td>
    `;
    tbody.appendChild(tr);
  });

  // Wire remove buttons
  tbody.querySelectorAll(".btn-remove").forEach((btn) => {
    btn.addEventListener("click", () => {
      removePosition(parseInt(btn.dataset.idx, 10));
    });
  });
}

// ── Margin Result ─────────────────────────────────────────────────────────────

export function renderMarginResult(result) {
  const panel = document.getElementById("result-panel");
  panel.hidden = false;

  const s = result.summary;
  document.getElementById("res-span").textContent = formatINR(s.span_margin);
  document.getElementById("res-exposure").textContent = formatINR(s.exposure_margin);
  document.getElementById("res-total").textContent = formatINR(s.total_margin);
  document.getElementById("res-premium").textContent = formatINR(s.premium_received);

  const modeEl = document.getElementById("res-mode");
  modeEl.textContent = dataModeLabel(s.data_mode);
  modeEl.className = `badge ${dataModeClass(s.data_mode)}`;

  // By-commodity breakdown
  _renderCommodityBreakdown(result.by_commodity);

  // By-position breakdown
  _renderPositionBreakdown(result.by_position);
}

function _renderCommodityBreakdown(byCommodity) {
  const container = document.getElementById("commodity-breakdown");
  container.innerHTML = "";

  byCommodity.forEach((c) => {
    const div = document.createElement("div");
    div.className = "breakdown-card";
    div.innerHTML = `
      <div class="breakdown-header">
        <strong>${c.commodity}</strong>
        <span>SPAN: ${formatINR(c.commodity_span)}</span>
      </div>
      <div class="breakdown-rows">
        <div class="brow"><span>Scan Risk</span><span>${formatINR(c.scan_risk)}</span></div>
        ${c.short_option_min > 0 ? `<div class="brow"><span>Short Option Min</span><span>${formatINR(c.short_option_min)}</span></div>` : ""}
        ${c.inter_spread_credit > 0 ? `<div class="brow credit"><span>Inter-Spread Credit</span><span>−${formatINR(c.inter_spread_credit)}</span></div>` : ""}
        <div class="brow"><span>Exposure Margin</span><span>${formatINR(c.exposure_margin)}</span></div>
      </div>
    `;
    container.appendChild(div);
  });
}

function _renderPositionBreakdown(byPosition) {
  const tbody = document.getElementById("position-breakdown-tbody");
  tbody.innerHTML = "";

  byPosition.forEach((p) => {
    const tr = document.createElement("tr");
    const modeClass = dataModeClass(p.data_mode);
    tr.innerHTML = `
      <td>${formatContractName(p)}</td>
      <td>${p.lots}</td>
      <td class="${p.side === "sell" ? "side-sell" : "side-buy"}">${p.side.toUpperCase()}</td>
      <td>${formatINR(p.notional_value)}</td>
      <td>S${p.worst_scenario}</td>
      <td>${formatINR(p.worst_scenario_loss)}</td>
      <td>${formatINR(p.exposure_margin)}</td>
      <td><span class="badge ${modeClass}">${dataModeLabel(p.data_mode)}</span></td>
    `;
    tbody.appendChild(tr);
  });
}

// ── Error Toast ───────────────────────────────────────────────────────────────

export function showError(message) {
  const toast = document.getElementById("error-toast");
  toast.textContent = message;
  toast.hidden = false;
  setTimeout(() => { toast.hidden = true; }, 5000);
}

export function hideResult() {
  document.getElementById("result-panel").hidden = true;
}

// ── Loading state ─────────────────────────────────────────────────────────────

export function setLoading(loading) {
  const btn = document.getElementById("calc-btn");
  btn.disabled = loading;
  btn.textContent = loading ? "Calculating…" : "Calculate Margin →";
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function _instrLabel(type) {
  return { FUTIDX: "Idx Fut", OPTIDX: "Idx Opt", FUTSTK: "Stk Fut", OPTSTK: "Stk Opt" }[type] || type;
}
