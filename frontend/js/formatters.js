"use strict";

/**
 * Format a number as Indian Rupees with ₹ prefix and Indian comma grouping.
 * e.g. 108407.36 → "₹1,08,407"
 */
export function formatINR(amount, decimals = 0) {
  if (amount == null || isNaN(amount)) return "₹0";
  const fixed = Math.abs(amount).toFixed(decimals);
  const [intPart, decPart] = fixed.split(".");
  // Indian number grouping: last 3, then groups of 2
  const lastThree = intPart.slice(-3);
  const rest = intPart.slice(0, -3);
  const grouped =
    rest.length > 0
      ? rest.replace(/\B(?=(\d{2})+(?!\d))/g, ",") + "," + lastThree
      : lastThree;
  const sign = amount < 0 ? "-" : "";
  return sign + "₹" + grouped + (decPart ? "." + decPart : "");
}

/**
 * Format an ISO date string as "24 Apr 2025".
 */
export function formatDate(isoDate) {
  if (!isoDate) return "";
  const d = new Date(isoDate + "T00:00:00");
  return d.toLocaleDateString("en-IN", {
    day: "2-digit",
    month: "short",
    year: "numeric",
  });
}

/**
 * Short date: "24 Apr" (no year).
 */
export function formatDateShort(isoDate) {
  if (!isoDate) return "";
  const d = new Date(isoDate + "T00:00:00");
  return d.toLocaleDateString("en-IN", { day: "2-digit", month: "short" });
}

/**
 * Format a contract as a readable name.
 * e.g. { symbol:"NIFTY", instrument_type:"OPTIDX", expiry_date:"2025-04-24",
 *          strike_price:22500, option_type:"CE" }
 * → "NIFTY 24Apr 22500 CE"
 */
export function formatContractName(pos) {
  const parts = [pos.symbol, formatDateShort(pos.expiry_date)];
  if (pos.strike_price != null) parts.push(pos.strike_price);
  if (pos.option_type)          parts.push(pos.option_type);
  return parts.join(" ");
}

/**
 * Return a CSS class for a data-mode badge.
 */
export function dataModeClass(mode) {
  return mode === "span_file" ? "badge-live" : "badge-estimated";
}

/**
 * Human-readable data mode label.
 */
export function dataModeLabel(mode) {
  return mode === "span_file" ? "Live SPAN" : "Estimated";
}
