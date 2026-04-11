"""
Integration tests for all REST API endpoints.

Uses the Flask test client (client fixture from conftest.py) and the
in-memory SQLite database.  DB fixtures create the reference contracts
needed for margin calculation tests.
"""

import json
from datetime import datetime

import pytest

from tests.conftest import TRADE_DATE


# ── Helpers ───────────────────────────────────────────────────────────────────

DATE_STR = TRADE_DATE.isoformat()  # "2026-01-15"


def _post(client, url, body):
    return client.post(url, data=json.dumps(body), content_type="application/json")


# ── GET /api/span-status ──────────────────────────────────────────────────────

class TestSpanStatus:
    def test_no_data_returns_no_data_status(self, client):
        """Empty DB → status=no_data, instrument_count=0."""
        r = client.get("/api/span-status")
        assert r.status_code == 200
        d = r.get_json()
        assert d["status"] == "no_data"
        assert d["instrument_count"] == 0
        assert d["is_stale"] is True

    def test_with_span_file_returns_success(self, client, app, span_file, nifty_future_contract):
        """After inserting a span file + contract, status=success."""
        r = client.get("/api/span-status")
        assert r.status_code == 200
        d = r.get_json()
        assert d["status"] == "success"
        assert d["trade_date"] == DATE_STR
        assert d["instrument_count"] >= 1

    def test_data_mode_span_file_when_risk_arrays_exist(
            self, client, app, span_file, nifty_future_contract):
        r = client.get("/api/span-status")
        d = r.get_json()
        assert d["data_mode"] == "span_file"
        assert d["risk_array_count"] >= 1


# ── GET /api/instruments/search ───────────────────────────────────────────────

class TestInstrumentSearch:
    def test_empty_query_returns_empty_results(self, client, nifty_future_contract):
        r = client.get(f"/api/instruments/search?date={DATE_STR}")
        assert r.status_code == 200
        assert r.get_json()["results"] == []

    def test_matching_prefix_returns_contracts(self, client, nifty_future_contract):
        r = client.get(f"/api/instruments/search?q=NIF&date={DATE_STR}")
        assert r.status_code == 200
        results = r.get_json()["results"]
        assert len(results) >= 1
        assert all(res["symbol"].startswith("NIF") for res in results)

    def test_result_has_required_fields(self, client, nifty_future_contract):
        r = client.get(f"/api/instruments/search?q=NIFTY&date={DATE_STR}")
        d = r.get_json()["results"][0]
        for field in ("contract_key", "symbol", "instrument_type",
                      "expiry_date", "lot_size", "underlying_price"):
            assert field in d, f"Missing field: {field}"

    def test_no_match_returns_empty(self, client, nifty_future_contract):
        r = client.get(f"/api/instruments/search?q=DOESNOTEXIST&date={DATE_STR}")
        assert r.get_json()["results"] == []


# ── GET /api/instruments/symbols ──────────────────────────────────────────────

class TestInstrumentSymbols:
    def test_returns_symbols_list(self, client, nifty_future_contract,
                                  banknifty_future_contract):
        r = client.get(f"/api/instruments/symbols?date={DATE_STR}")
        assert r.status_code == 200
        d = r.get_json()
        assert "NIFTY" in d["symbols"]
        assert "BANKNIFTY" in d["symbols"]

    def test_empty_db_returns_empty_list(self, client):
        r = client.get(f"/api/instruments/symbols?date={DATE_STR}")
        assert r.status_code == 200
        assert r.get_json()["symbols"] == []

    def test_no_duplicates(self, client, nifty_future_contract,
                           nifty_ce_contract, nifty_pe_contract):
        """Multiple NIFTY contracts should appear only once in symbols list."""
        r = client.get(f"/api/instruments/symbols?date={DATE_STR}")
        symbols = r.get_json()["symbols"]
        assert symbols.count("NIFTY") == 1


# ── GET /api/instruments/expiries ─────────────────────────────────────────────

class TestInstrumentExpiries:
    def test_missing_symbol_returns_400(self, client):
        r = client.get(f"/api/instruments/expiries?date={DATE_STR}")
        assert r.status_code == 400

    def test_returns_expiry_dates(self, client, nifty_future_contract, nifty_ce_contract):
        r = client.get(f"/api/instruments/expiries?symbol=NIFTY&date={DATE_STR}")
        assert r.status_code == 200
        d = r.get_json()
        assert "2026-01-29" in d["expiries"]

    def test_unknown_symbol_returns_empty(self, client):
        r = client.get(f"/api/instruments/expiries?symbol=ZZZZZ&date={DATE_STR}")
        assert r.status_code == 200
        assert r.get_json()["expiries"] == []


# ── GET /api/instruments/strikes ──────────────────────────────────────────────

class TestInstrumentStrikes:
    def test_missing_params_returns_400(self, client):
        r = client.get(f"/api/instruments/strikes?date={DATE_STR}")
        assert r.status_code == 400

    def test_returns_strike_map(self, client, nifty_ce_contract, nifty_pe_contract):
        r = client.get(
            f"/api/instruments/strikes"
            f"?symbol=NIFTY&expiry=2026-01-29&date={DATE_STR}"
        )
        assert r.status_code == 200
        d = r.get_json()
        assert len(d["strikes"]) >= 1
        strike = d["strikes"][0]
        assert "strike" in strike
        assert "ce_key" in strike
        assert "pe_key" in strike

    def test_strike_includes_both_legs(self, client, nifty_ce_contract, nifty_pe_contract):
        r = client.get(
            f"/api/instruments/strikes"
            f"?symbol=NIFTY&expiry=2026-01-29&date={DATE_STR}"
        )
        strike = r.get_json()["strikes"][0]
        assert strike["ce_key"] == "NIFTY-OPTIDX-20260129-22000-CE"
        assert strike["pe_key"] == "NIFTY-OPTIDX-20260129-22000-PE"

    def test_invalid_expiry_returns_400(self, client, nifty_ce_contract):
        r = client.get(
            f"/api/instruments/strikes"
            f"?symbol=NIFTY&expiry=NOT-A-DATE&date={DATE_STR}"
        )
        assert r.status_code == 400


# ── GET /api/instruments/futures ──────────────────────────────────────────────

class TestInstrumentFutures:
    def test_missing_symbol_returns_400(self, client):
        r = client.get(f"/api/instruments/futures?date={DATE_STR}")
        assert r.status_code == 400

    def test_returns_futures_list(self, client, nifty_future_contract):
        r = client.get(f"/api/instruments/futures?symbol=NIFTY&date={DATE_STR}")
        assert r.status_code == 200
        d = r.get_json()
        assert d["symbol"] == "NIFTY"
        assert len(d["futures"]) == 1
        assert d["futures"][0]["instrument_type"] == "FUTIDX"

    def test_options_not_included_in_futures(self, client, nifty_future_contract,
                                              nifty_ce_contract):
        r = client.get(f"/api/instruments/futures?symbol=NIFTY&date={DATE_STR}")
        futures = r.get_json()["futures"]
        assert all(f["instrument_type"] in ("FUTIDX", "FUTSTK") for f in futures)


# ── GET /api/instruments/contract/<key> ───────────────────────────────────────

class TestGetContract:
    def test_existing_contract_returned(self, client, nifty_future_contract):
        r = client.get(
            f"/api/instruments/contract/NIFTY-FUTIDX-20260129?date={DATE_STR}"
        )
        assert r.status_code == 200
        d = r.get_json()
        assert d["contract_key"] == "NIFTY-FUTIDX-20260129"
        assert d["instrument_type"] == "FUTIDX"

    def test_unknown_contract_returns_404(self, client):
        r = client.get(
            f"/api/instruments/contract/DOES-NOT-EXIST-99990101?date={DATE_STR}"
        )
        assert r.status_code == 404


# ── POST /api/margin/calculate ────────────────────────────────────────────────

class TestMarginCalculate:
    def test_missing_body_returns_400(self, client):
        r = client.post("/api/margin/calculate")
        assert r.status_code == 400

    def test_empty_positions_returns_400(self, client):
        body = {"trade_date": DATE_STR, "positions": []}
        r = _post(client, "/api/margin/calculate", body)
        assert r.status_code == 400

    def test_missing_contract_key_returns_400(self, client):
        body = {
            "trade_date": DATE_STR,
            "positions": [{"quantity": -1}],
        }
        r = _post(client, "/api/margin/calculate", body)
        assert r.status_code == 400

    def test_zero_quantity_returns_400(self, client):
        body = {
            "trade_date": DATE_STR,
            "positions": [{"contract_key": "NIFTY-FUTIDX-20260129", "quantity": 0}],
        }
        r = _post(client, "/api/margin/calculate", body)
        assert r.status_code == 400

    def test_invalid_trade_date_returns_400(self, client):
        body = {
            "trade_date": "not-a-date",
            "positions": [{"contract_key": "NIFTY-FUTIDX-20260129", "quantity": -1}],
        }
        r = _post(client, "/api/margin/calculate", body)
        assert r.status_code == 400

    def test_unknown_contract_returns_422(self, client, span_file):
        """A contract key that doesn't exist in the DB → 422 with error message."""
        body = {
            "trade_date": DATE_STR,
            "positions": [{"contract_key": "FAKE-FUTIDX-20260129", "quantity": -1}],
        }
        r = _post(client, "/api/margin/calculate", body)
        assert r.status_code == 422
        assert "error" in r.get_json()

    def test_valid_single_future_returns_margin(self, client, nifty_future_contract):
        body = {
            "trade_date": DATE_STR,
            "positions": [{"contract_key": "NIFTY-FUTIDX-20260129", "quantity": -1}],
        }
        r = _post(client, "/api/margin/calculate", body)
        assert r.status_code == 200
        d = r.get_json()
        assert "summary" in d
        assert d["summary"]["span_margin"] == pytest.approx(1000.0)
        assert d["summary"]["total_margin"] > 0

    def test_response_has_required_top_level_keys(self, client, nifty_future_contract):
        body = {
            "trade_date": DATE_STR,
            "positions": [{"contract_key": "NIFTY-FUTIDX-20260129", "quantity": -1}],
        }
        r = _post(client, "/api/margin/calculate", body)
        d = r.get_json()
        for key in ("summary", "by_commodity", "by_position"):
            assert key in d, f"Missing key: {key}"

    def test_summary_has_all_fields(self, client, nifty_future_contract):
        body = {
            "trade_date": DATE_STR,
            "positions": [{"contract_key": "NIFTY-FUTIDX-20260129", "quantity": -1}],
        }
        r = _post(client, "/api/margin/calculate", body)
        summary = r.get_json()["summary"]
        for field in ("span_margin", "exposure_margin", "total_margin",
                      "premium_received", "data_mode"):
            assert field in summary

    def test_data_mode_span_file(self, client, nifty_future_contract):
        body = {
            "trade_date": DATE_STR,
            "positions": [{"contract_key": "NIFTY-FUTIDX-20260129", "quantity": -1}],
        }
        r = _post(client, "/api/margin/calculate", body)
        assert r.get_json()["summary"]["data_mode"] == "span_file"

    def test_by_position_has_correct_length(self, client, nifty_future_contract,
                                             nifty_ce_contract):
        body = {
            "trade_date": DATE_STR,
            "positions": [
                {"contract_key": "NIFTY-FUTIDX-20260129", "quantity": -1},
                {"contract_key": "NIFTY-OPTIDX-20260129-22000-CE", "quantity": -1},
            ],
        }
        r = _post(client, "/api/margin/calculate", body)
        assert r.status_code == 200
        assert len(r.get_json()["by_position"]) == 2

    def test_exposure_margin_for_short_future(self, client, nifty_future_contract):
        """Short NIFTY future: exposure = 3% × 25 × 22000."""
        body = {
            "trade_date": DATE_STR,
            "positions": [{"contract_key": "NIFTY-FUTIDX-20260129", "quantity": -1}],
        }
        r = _post(client, "/api/margin/calculate", body)
        expected = 0.03 * 25 * 22000.0
        assert r.get_json()["summary"]["exposure_margin"] == pytest.approx(expected)

    def test_total_is_span_plus_exposure(self, client, nifty_future_contract):
        body = {
            "trade_date": DATE_STR,
            "positions": [{"contract_key": "NIFTY-FUTIDX-20260129", "quantity": -1}],
        }
        r = _post(client, "/api/margin/calculate", body)
        d = r.get_json()["summary"]
        assert d["total_margin"] == pytest.approx(d["span_margin"] + d["exposure_margin"])

    def test_premium_received_for_short_option(self, client, nifty_ce_contract):
        body = {
            "trade_date": DATE_STR,
            "positions": [{"contract_key": "NIFTY-OPTIDX-20260129-22000-CE", "quantity": -1}],
        }
        r = _post(client, "/api/margin/calculate", body)
        # premium = 1 lot × 25 × 150.0
        expected_premium = 1 * 25 * 150.0
        assert r.get_json()["summary"]["premium_received"] == pytest.approx(expected_premium)

    def test_no_premium_for_long_option(self, client, nifty_ce_contract):
        body = {
            "trade_date": DATE_STR,
            "positions": [{"contract_key": "NIFTY-OPTIDX-20260129-22000-CE", "quantity": 1}],
        }
        r = _post(client, "/api/margin/calculate", body)
        assert r.get_json()["summary"]["premium_received"] == pytest.approx(0.0)

    def test_date_defaults_to_most_recent_when_omitted(self, client, nifty_future_contract):
        """Omitting trade_date should not cause a server error."""
        body = {
            "positions": [{"contract_key": "NIFTY-FUTIDX-20260129", "quantity": -1}],
        }
        r = _post(client, "/api/margin/calculate", body)
        # Will return 422 (contract not found for today's date) — not 400/500
        assert r.status_code in (200, 422)

    def test_api_summary_includes_vm_fields(self, client, nifty_future_contract):
        """Summary always includes variation_margin and net_cash_required."""
        body = {
            "trade_date": DATE_STR,
            "positions": [{"contract_key": "NIFTY-FUTIDX-20260129", "quantity": -1}],
        }
        r = _post(client, "/api/margin/calculate", body)
        summary = r.get_json()["summary"]
        assert "variation_margin" in summary
        assert "net_cash_required" in summary

    def test_api_vm_zero_when_prev_settlement_omitted(self, client, nifty_future_contract):
        """Omitting prev_settlement → VM=0, net_cash_required=total_margin."""
        body = {
            "trade_date": DATE_STR,
            "positions": [{"contract_key": "NIFTY-FUTIDX-20260129", "quantity": -1}],
        }
        r = _post(client, "/api/margin/calculate", body)
        summary = r.get_json()["summary"]
        assert summary["variation_margin"] == pytest.approx(0.0)
        assert summary["net_cash_required"] == pytest.approx(summary["total_margin"])

    def test_api_vm_computed_when_prev_settlement_provided(self, client, nifty_future_contract):
        """With prev_settlement provided, VM and net_cash differ from total_margin."""
        body = {
            "trade_date": DATE_STR,
            "positions": [{
                "contract_key": "NIFTY-FUTIDX-20260129",
                "quantity": -1,
                "prev_settlement": 23000.0,  # higher than today's 22050
            }],
        }
        r = _post(client, "/api/margin/calculate", body)
        summary = r.get_json()["summary"]
        # Short future, price fell (22050 < 23000) → gain → VM > 0 → net_cash < total
        assert summary["variation_margin"] > 0
        assert summary["net_cash_required"] < summary["total_margin"]

    def test_api_position_includes_vm_for_futures(self, client, nifty_future_contract):
        """by_position entries for futures include variation_margin."""
        body = {
            "trade_date": DATE_STR,
            "positions": [{
                "contract_key": "NIFTY-FUTIDX-20260129",
                "quantity": 1,
                "prev_settlement": 22000.0,
            }],
        }
        r = _post(client, "/api/margin/calculate", body)
        pos = r.get_json()["by_position"][0]
        assert "variation_margin" in pos
        assert pos["variation_margin"] == pytest.approx(1 * 25 * (22050.0 - 22000.0))

    def test_api_position_vm_null_for_options(self, client, nifty_ce_contract):
        """by_position entries for options have variation_margin = null."""
        body = {
            "trade_date": DATE_STR,
            "positions": [{"contract_key": "NIFTY-OPTIDX-20260129-22000-CE", "quantity": -1}],
        }
        r = _post(client, "/api/margin/calculate", body)
        pos = r.get_json()["by_position"][0]
        assert pos["variation_margin"] is None
