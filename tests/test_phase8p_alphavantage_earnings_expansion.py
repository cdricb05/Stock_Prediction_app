"""Fully-offline tests for Phase 8-P Alpha Vantage earnings coverage expansion.

These tests never set a key, never touch the network, and never write outside a tmp dir.
The bounded collection path is exercised with an injected ``transport`` that returns canned
Alpha-Vantage-shaped payloads (real EARNINGS shape, rate-limit envelopes, invalid-key
envelope). They assert the brief's acceptance criteria:

  * the API key is never printed or written to any committed artifact;
  * dry-run / offline works with no network and no key;
  * a missing key is handled clearly (BLOCKED_MISSING_ALPHAVANTAGE_KEY + setup command);
  * the request plan prioritizes FMP-blocked tickers first;
  * the bounded request limit (max_requests) is honored;
  * skip-existing behaviour works;
  * raw/normalized provider data are gitignored and live only under research/data/alphavantage;
  * the FMP-vs-AV comparison, combined-coverage, and signal-ready manifest are produced;
  * no Paper Trader / GCP / order / deployment logic is touched.
"""
from __future__ import annotations

import csv
import importlib
import json
import os
from pathlib import Path

import pytest

MOD = importlib.import_module("research.run_phase8p_alphavantage_earnings_expansion")

# A representative real-shape Alpha Vantage EARNINGS payload (two quarters).
_AV_OK = {
    "symbol": "ABT",
    "annualEarnings": [{"fiscalDateEnding": "2023-12-31", "reportedEPS": "4.44"}],
    "quarterlyEarnings": [
        {"fiscalDateEnding": "2024-03-31", "reportedDate": "2024-04-17",
         "reportedEPS": "0.98", "estimatedEPS": "0.95", "surprise": "0.03",
         "surprisePercentage": "3.1579", "reportTime": "pre-market"},
        {"fiscalDateEnding": "2023-12-31", "reportedDate": "2024-01-24",
         "reportedEPS": "1.19", "estimatedEPS": "1.18", "surprise": "0.01",
         "surprisePercentage": "0.8475", "reportTime": "pre-market"},
    ],
}
_AV_RATE_LIMIT = {"Note": ("Thank you for using Alpha Vantage! Our standard API rate limit is "
                           "25 requests per day.")}
_AV_INVALID_KEY = {"Information": ("the parameter apikey is invalid or missing. Please claim "
                                   "your free API key on the website.")}


def _fmp_coverage_dir(tmp_path: Path) -> Path:
    """Write a minimal Phase-8-N coverage CSV: 8 FMP-covered, 9 FMP-blocked earnings tickers."""
    d = tmp_path / "phase8n"
    d.mkdir(parents=True, exist_ok=True)
    covered = ["AAPL", "MSFT", "NVDA", "ABBV", "ADBE", "AMD", "AMZN", "JPM"]
    blocked = ["ABT", "ACN", "ADI", "ADP", "AMAT", "AMGN", "AON", "APD", "APH"]
    rows = [["ticker", "endpoint_family", "critical", "has_data", "rows", "first_date", "last_date"]]
    for tk in covered:
        rows.append([tk, "earnings_surprises", "True", "yes", "100", "1990-01-01", "2026-01-01"])
    for tk in blocked:
        rows.append([tk, "earnings_surprises", "True", "no", "0", "", ""])
    with open(d / MOD._FMP_COVERAGE_CSV, "w", newline="", encoding="utf-8") as fh:
        csv.writer(fh).writerows(rows)
    return d


def _run(tmp_path, transport=None, **kw):
    """Run Phase 8-P fully sandboxed under tmp_path (out/data/phase8n all redirected)."""
    out = tmp_path / "out"
    data = tmp_path / "data"
    phase8n = kw.pop("phase8n_dir", None) or _fmp_coverage_dir(tmp_path)
    return MOD.run(transport=transport, out_dir=out, data_dir=data, phase8n_dir=phase8n,
                   verbose=False, **kw), out, data


def _read_csv(path):
    with open(path, "r", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


# --------------------------------------------------------------------------- #
# Secret discipline.
# --------------------------------------------------------------------------- #
def test_no_key_required_and_never_set(monkeypatch, tmp_path):
    monkeypatch.delenv(MOD.API_KEY_ENV, raising=False)
    report, out, _ = _run(tmp_path)
    assert report["api_key_present"] is False
    assert report["api_key_logged"] is False
    assert report["decision"] == MOD.DEC_BLOCKED_NO_KEY
    assert "ALPHAVANTAGE_API_KEY" in report["recommended_next_command"]
    assert report["network_used"] is False


def test_key_value_never_written_to_committed_artifacts(monkeypatch, tmp_path):
    secret = "SECRETKEY_DO_NOT_LEAK_8P"
    monkeypatch.setenv(MOD.API_KEY_ENV, secret)
    # transport path: the injected transport is used so no real network call happens.
    report, out, _ = _run(tmp_path, transport=lambda tk: _AV_OK)
    for fp in out.glob("*"):
        if fp.is_file():
            text = fp.read_text(encoding="utf-8", errors="replace")
            assert secret not in text, "API key value leaked into %s" % fp.name
            assert "apikey=" not in text.lower(), "literal apikey= leaked into %s" % fp.name
    assert report["secret_safety_leak_scan_clean"] is True
    assert report["api_key_logged"] is False


def test_key_presence_only_in_detection_report(monkeypatch, tmp_path):
    monkeypatch.setenv(MOD.API_KEY_ENV, "xyz")
    _, out, _ = _run(tmp_path, transport=lambda tk: _AV_RATE_LIMIT)
    rows = _read_csv(out / MOD._ARTIFACTS["key_detection"])
    assert rows[0]["key_present"] == "True"
    assert rows[0]["value_read"] == "False"


# --------------------------------------------------------------------------- #
# Offline / dry-run with no network and no key.
# --------------------------------------------------------------------------- #
def test_offline_plan_runs_without_network_or_key(monkeypatch, tmp_path):
    monkeypatch.delenv(MOD.API_KEY_ENV, raising=False)
    report, out, data = _run(tmp_path)
    assert report["collection_executed"] is False
    assert report["requests_made"] == 0
    # All 13 committed-safe artifacts exist.
    for key, name in MOD._ARTIFACTS.items():
        assert (out / name).is_file(), "missing artifact %s" % name
    assert report["network_used"] is False


def test_all_13_artifacts_listed_in_outputs(monkeypatch, tmp_path):
    monkeypatch.delenv(MOD.API_KEY_ENV, raising=False)
    report, _, _ = _run(tmp_path)
    assert set(report["outputs"].keys()) == set(MOD._ARTIFACTS.keys())
    assert len(MOD._ARTIFACTS) == 13


# --------------------------------------------------------------------------- #
# Request plan prioritization.
# --------------------------------------------------------------------------- #
def test_request_plan_prioritizes_fmp_blocked_first(monkeypatch, tmp_path):
    monkeypatch.delenv(MOD.API_KEY_ENV, raising=False)
    _, out, _ = _run(tmp_path)
    rows = _read_csv(out / MOD._ARTIFACTS["request_plan"])
    blocked = {"ABT", "ACN", "ADI", "ADP", "AMAT", "AMGN", "AON", "APD", "APH"}
    # The first 9 plan rows must be the FMP-blocked tickers (priority tier 1).
    first9 = rows[:9]
    assert all(r["priority"] == "1_fmp_blocked" for r in first9)
    assert {r["ticker"] for r in first9} == blocked


# --------------------------------------------------------------------------- #
# Bounded request limit + skip-existing.
# --------------------------------------------------------------------------- #
def test_max_requests_is_honored(monkeypatch, tmp_path):
    monkeypatch.setenv(MOD.API_KEY_ENV, "k")
    calls = {"n": 0}

    def transport(tk):
        calls["n"] += 1
        return _AV_OK

    report, out, _ = _run(tmp_path, transport=transport, max_requests=3, max_tickers=25)
    assert report["requests_made"] <= 3
    assert calls["n"] <= 3


def test_skip_existing_skips_cached_tickers(monkeypatch, tmp_path):
    monkeypatch.setenv(MOD.API_KEY_ENV, "k")
    # Pre-seed the normalized cache with ABT so skip_existing drops it from the request set.
    data = tmp_path / "data"
    norm = data / "alphavantage" / "normalized" / "earnings_quarterly.csv"
    norm.parent.mkdir(parents=True, exist_ok=True)
    with open(norm, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(MOD._NORMALIZED_HEADER)
        w.writerow(["ABT", "2023-12-31", "2024-01-24", "1.19", "1.18", "0.01", "0.85", "pre"])

    requested = []

    def transport(tk):
        requested.append(tk)
        return _AV_OK

    phase8n = _fmp_coverage_dir(tmp_path)
    report = MOD.run(transport=transport, out_dir=tmp_path / "out", data_dir=data,
                     phase8n_dir=phase8n, skip_existing=True, verbose=False)
    assert "ABT" not in requested, "skip_existing should have skipped the cached ABT"
    # ABT still appears in the plan, marked skip_existing.
    rows = _read_csv(tmp_path / "out" / MOD._ARTIFACTS["request_plan"])
    abt = [r for r in rows if r["ticker"] == "ABT"][0]
    assert abt["skip_existing"] == "True"


# --------------------------------------------------------------------------- #
# Raw/normalized gitignored under research/data/alphavantage only.
# --------------------------------------------------------------------------- #
def test_raw_and_normalized_are_gitignored(monkeypatch, tmp_path):
    monkeypatch.setenv(MOD.API_KEY_ENV, "k")
    _, out, data = _run(tmp_path, transport=lambda tk: _AV_OK, max_tickers=25)
    gi = data / "alphavantage" / ".gitignore"
    assert gi.is_file()
    text = gi.read_text(encoding="utf-8")
    assert "raw/" in text and "normalized/" in text
    assert "*" in text and "!.gitignore" in text
    # Raw + normalized payloads exist ONLY under the gitignored tree, never under out/.
    assert (data / "alphavantage" / "raw" / "earnings").is_dir()
    assert (data / "alphavantage" / "normalized" / "earnings_quarterly.csv").is_file()
    for fp in out.glob("**/*"):
        if fp.is_file():
            assert "quarterlyEarnings" not in fp.read_text(encoding="utf-8", errors="replace"), (
                "raw payload leaked into committed artifact %s" % fp.name)


# --------------------------------------------------------------------------- #
# Coverage comparison + combined + signal-ready manifest.
# --------------------------------------------------------------------------- #
def test_coverage_comparison_and_combined_produced(monkeypatch, tmp_path):
    monkeypatch.setenv(MOD.API_KEY_ENV, "k")
    # AV covers every requested ticker -> combined >> 20.
    report, out, _ = _run(tmp_path, transport=lambda tk: _AV_OK, max_tickers=25)
    comp = _read_csv(out / MOD._ARTIFACTS["comparison"])
    assert comp, "comparison report empty"
    # ABT (FMP-blocked) is newly covered by AV.
    abt = [r for r in comp if r["ticker"] == "ABT"][0]
    assert abt["fmp_covered"] == "False"
    assert abt["alphavantage_covered"] == "True"
    assert abt["newly_covered_by_alphavantage"] == "True"
    combined = _read_csv(out / MOD._ARTIFACTS["combined"])
    assert len(combined) >= 20
    assert report["scoring_threshold_reached"] is True


def test_signal_manifest_built_when_threshold_reached(monkeypatch, tmp_path):
    monkeypatch.setenv(MOD.API_KEY_ENV, "k")
    report, out, _ = _run(tmp_path, transport=lambda tk: _AV_OK, max_tickers=25)
    assert report["decision"] == MOD.DEC_SOLVES
    assert report["signal_readiness_decision"] == MOD.DEC_READY_SCORING
    manifest = _read_csv(out / MOD._ARTIFACTS["signal_manifest"])
    assert manifest, "signal manifest should be populated when threshold reached"
    families = {r["required_family"] for r in manifest}
    assert families == {"earnings_surprise"}
    signal_ids = {r["signal_id"] for r in manifest}
    assert signal_ids == {"S8P-EARNSURP-RATES", "S8P-EARNSURP-SECTOR", "S8P-EARNSURP-VOL"}


def test_coverage_fields_and_point_in_time_safe(monkeypatch, tmp_path):
    monkeypatch.setenv(MOD.API_KEY_ENV, "k")
    _, out, _ = _run(tmp_path, transport=lambda tk: _AV_OK, max_tickers=25)
    cov = _read_csv(out / MOD._ARTIFACTS["coverage"])
    abt = [r for r in cov if r["ticker"] == "ABT"][0]
    assert abt["has_data"] == "True"
    assert int(abt["row_count"]) == 2
    assert abt["has_actual_eps"] == "True"
    assert abt["has_estimated_eps"] == "True"
    assert abt["has_surprise"] == "True"
    assert abt["point_in_time_safe"] == "True"
    assert abt["first_reported_date"] == "2024-01-24"
    assert abt["last_reported_date"] == "2024-04-17"


# --------------------------------------------------------------------------- #
# Rate-limit + invalid-key handling.
# --------------------------------------------------------------------------- #
def test_rate_limit_stops_and_decides(monkeypatch, tmp_path):
    monkeypatch.setenv(MOD.API_KEY_ENV, "k")
    report, out, _ = _run(tmp_path, transport=lambda tk: _AV_RATE_LIMIT, max_tickers=25)
    assert report["rate_limited"] is True
    assert report["collection_stopped_early"] is True
    assert report["decision"] == MOD.DEC_RATE_LIMITED


def test_invalid_key_stops_immediately(monkeypatch, tmp_path):
    monkeypatch.setenv(MOD.API_KEY_ENV, "k")
    report, out, _ = _run(tmp_path, transport=lambda tk: _AV_INVALID_KEY, max_tickers=25)
    assert report["invalid_key"] is True
    assert report["decision"] == MOD.DEC_REJECTED
    assert report["requests_made"] == 1  # stopped after the first invalid-key response


def test_below_threshold_requests_next_batch(monkeypatch, tmp_path):
    monkeypatch.setenv(MOD.API_KEY_ENV, "k")
    # Only ABT returns data; everything else empty -> combined = 8 FMP + 1 AV = 9 < 20.
    def transport(tk):
        return _AV_OK if tk == "ABT" else {"symbol": tk, "quarterlyEarnings": []}

    report, out, _ = _run(tmp_path, transport=transport, max_tickers=25)
    assert report["scoring_threshold_reached"] is False
    assert "ABT" in report["alphavantage_newly_covered_tickers"]
    assert report["decision"] in (MOD.DEC_NEXT_BATCH, MOD.DEC_REJECTED)


# --------------------------------------------------------------------------- #
# Decision vocabulary + safety contract.
# --------------------------------------------------------------------------- #
def test_decision_in_allowed_vocabulary(monkeypatch, tmp_path):
    monkeypatch.delenv(MOD.API_KEY_ENV, raising=False)
    report, _, _ = _run(tmp_path)
    assert report["decision"] in MOD.ALLOWED_DECISIONS
    assert report["signal_readiness_decision"] in MOD.ALLOWED_DECISIONS


def test_safety_contract_flags(monkeypatch, tmp_path):
    monkeypatch.delenv(MOD.API_KEY_ENV, raising=False)
    report, _, _ = _run(tmp_path)
    assert report["preview_only"] is True
    assert report["orders_enabled"] is False
    assert report["automation_enabled"] is False
    assert report["broker_execution_enabled"] is False
    assert report["paper_trader_touched"] is False
    assert report["gcp_touched"] is False
    assert report["deployed"] is False
    assert report["data_fabricated"] is False
    assert report["committed"] is False


def test_no_paper_trader_or_gcp_or_order_logic_in_source():
    src = Path(MOD.__file__).read_text(encoding="utf-8")
    low = src.lower()
    # Tokens that would indicate real order/automation/deployment logic (not the negated
    # safety-contract flags such as gcp_touched / deployed / broker_execution_enabled).
    for forbidden in ("create_order", "place_order", "submit_order", "execute_order",
                      "import paper", "from api.app", "gcloud", "subprocess"):
        assert forbidden not in low, "forbidden token %r found in source" % forbidden


def test_redact_url_strips_apikey():
    url = "https://www.alphavantage.co/query?function=EARNINGS&symbol=ABT&apikey=SECRET123"
    red = MOD.redact_url(url)
    assert "SECRET123" not in red
    assert "apikey=" not in red.lower()
    assert "symbol=ABT" in red
