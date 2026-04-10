"use strict";

/**
 * In-memory portfolio state.
 * Each position is a plain object:
 * {
 *   contract_key, symbol, instrument_type, expiry_date,
 *   strike_price, option_type, lot_size, underlying_price,
 *   future_price, quantity,   // signed: + = long, - = short
 *   side                      // 'buy' | 'sell'
 * }
 */

let _positions = [];
const _listeners = [];

function _notify() {
  _listeners.forEach((fn) => fn([..._positions]));
}

export function subscribe(fn) {
  _listeners.push(fn);
}

export function getPositions() {
  return [..._positions];
}

export function addPosition(pos) {
  // Prevent exact duplicate (same contract_key + same side)
  const dup = _positions.find(
    (p) => p.contract_key === pos.contract_key && p.side === pos.side
  );
  if (dup) {
    // Merge: add lots
    dup.quantity += pos.quantity;
    if (dup.quantity === 0) {
      _positions = _positions.filter((p) => p !== dup);
    }
  } else {
    _positions.push({ ...pos });
  }
  _notify();
}

export function removePosition(index) {
  _positions.splice(index, 1);
  _notify();
}

export function clearPortfolio() {
  _positions = [];
  _notify();
}

export function toApiPayload(tradeDate) {
  return {
    trade_date: tradeDate,
    positions: _positions.map((p) => ({
      contract_key: p.contract_key,
      quantity: p.quantity,
    })),
  };
}
