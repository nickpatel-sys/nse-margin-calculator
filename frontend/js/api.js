"use strict";

/**
 * Thin fetch wrappers for all backend API endpoints.
 * All functions return Promises that resolve to parsed JSON.
 * On HTTP error they throw an Error with the server's message.
 */

async function _get(url) {
  const res = await fetch(url);
  const json = await res.json();
  if (!res.ok) throw new Error(json.error || `HTTP ${res.status}`);
  return json;
}

async function _post(url, body) {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const json = await res.json();
  if (!res.ok) throw new Error(json.error || `HTTP ${res.status}`);
  return json;
}

export function getSpanStatus() {
  return _get("/api/span-status");
}

export function triggerSpanRefresh() {
  return _post("/api/span/refresh", {});
}

export function listSymbols(tradeDate) {
  const q = tradeDate ? `?date=${tradeDate}` : "";
  return _get(`/api/instruments/symbols${q}`);
}

export function listExpiries(symbol, tradeDate) {
  return _get(`/api/instruments/expiries?symbol=${symbol}&date=${tradeDate || ""}`);
}

export function listStrikes(symbol, expiry, tradeDate) {
  return _get(
    `/api/instruments/strikes?symbol=${symbol}&expiry=${expiry}&date=${tradeDate || ""}`
  );
}

export function listFutures(symbol, tradeDate) {
  return _get(`/api/instruments/futures?symbol=${symbol}&date=${tradeDate || ""}`);
}

export function getContract(contractKey, tradeDate) {
  return _get(`/api/instruments/contract/${encodeURIComponent(contractKey)}?date=${tradeDate || ""}`);
}

export function calculateMargin(positions, tradeDate) {
  return _post("/api/margin/calculate", {
    trade_date: tradeDate,
    positions,
  });
}
