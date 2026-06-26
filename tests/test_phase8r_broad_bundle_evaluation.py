"""Fully-offline tests for Phase 8-R broad earnings/fundamentals bundle evaluation (EODHD).

These tests never set a real key, never touch the network, and never write outside a tmp dir.
The bounded probe path is exercised with an injected ``transport`` returning canned EODHD-shaped
payloads (fundamentals with Earnings::History / Financials / AnalystRatings, an earnings calendar,
plan-block / rate-limit / invalid-key HTTP errors). They assert the brief's acceptance criteria:

  * the Phase 8-Q decision is read;
  * EODHD is tested first (request plan prioritizes FMP-blocked, then the current universe);
  * FMP Ultimate is rejected;
  * a missing EODHD key is handled clearly (BLOCKED_MISSING_EODHD_KEY + setup command);
  * raw/normalized paid data are gitignored and live only under research/data/eodhd;
  * API keys are never printed or written to any committed artifact;
  * bounded request limits (max_requests) are honored;
  * the EODHD-vs-FMP-vs-Alpha-Vantage comparison is produced;
  * point-in-time readiness is produced;
  * procurement questions are produced;
  * no Paper Trader / GCP / order / deployment logic is touched;
  * only committed-safe outputs are emitted.
"""
from __future__ import annotations

import csv
import importlib
from pathlib import Path

MOD = importlib.import_module("research.run_phase8r_broad_bundle_evaluation")

# ---- canned EODHD-shaped payloads (real structure; deep history so depth is sufficient) ----
def _earnings_history(n=28):
    """A point-in-time-safe Earnings::History dict spanning ~7 years (n quarters)."""
    hist = {}
    for i in range(n):
        year = 2018 + i // 4
        q = i % 4
        month = (q * 3) + 3
        fiscal = "%04d-%02d-30" % (year, month)
        report = "%04d-%02d-15" % (year + (1 if month == 12 else 0), (month % 12) + 1)
        hist[fiscal] = {"date": fiscal, "reportDate": report, "epsActual": 1.0 + i * 0.01,
                        "epsEstimate": 1.0 + i * 0.008, "epsDifference": 0.002 * i,
                        "surprisePercent": 1.5}
    return hist


_EODHD_FUNDAMENTALS_OK = {
    "General": {"Code": "ABT", "Sector": "Healthcare"},
    "Earnings": {
        "History": _earnings_history(28),
        "Trend": {"2026-03-31": {"epsEstimateAvg": "1.10"}, "2026-06-30": {"epsEstimateAvg": "1.15"},
                  "2026-09-30": {"epsEstimateAvg": "1.20"}, "2026-12-31": {"epsEstimateAvg": "1.25"}},
    },
    "Financials": {"Income_Statement": {"quarterly": {
        ("%04d-%02d-30" % (2018 + i // 4, ((i % 4) * 3) + 3)): {
            "filing_date": "%04d-%02d-20" % (2018 + i // 4, ((i % 4) * 3) + 3),
            "totalRevenue": "1000"} for i in range(28)}}},
    "AnalystRatings": {"Rating": 4.2, "TargetPrice": 130.0, "StrongBuy": 8, "Buy": 12},
}
_EODHD_CALENDAR_OK = {"earnings": [
    {"code": "ABT.US", "report_date": "2024-04-17", "date": "2024-03-31",
     "estimate": 0.95, "actual": 0.98},
    {"code": "ABT.US", "report_date": "2024-07-18", "date": "2024-06-30",
     "estimate": 1.10, "actual": 1.14},
]}
_EODHD_EMPTY = {"General": {"Code": "ZZZZ"}, "Earnings": {"History": {}}}


def _fmp_coverage_dir(tmp_path):
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


def _phase8q_dir(tmp_path):
    """Write a minimal Phase-8-Q report + paid matrix so 8-R reads the prior decision + cost."""
    d = tmp_path / "phase8q"
    d.mkdir(parents=True, exist_ok=True)
    import json
    with open(d / "phase8q_market_data_foundation_decision.json", "w", encoding="utf-8") as fh:
        json.dump({"decision": "MIXED_FREE_PLUS_PAID_CORE_STACK", "fmp_ultimate_rejected": True,
                   "provider_category_to_evaluate_first": "earnings/fundamentals bundle (EODHD)"},
                  fh)
    with open(d / "paid_provider_decision_matrix.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["test_order", "provider", "monthly_cost"])
        w.writerow([2, "EODHD", "~$20-$80/mo"])
        w.writerow([3, "FMP (upgrade tier / Premium)", "~$69/mo"])
    return d


def _phase8p_dir(tmp_path):
    d = tmp_path / "phase8p"
    d.mkdir(parents=True, exist_ok=True)
    import json
    with open(d / "phase8p_alphavantage_earnings_expansion.json", "w", encoding="utf-8") as fh:
        json.dump({"combined_covered_count": 9, "alphavantage_covered_tickers": ["ABT"]}, fh)
    return d


def _run(tmp_path, transport=None, **kw):
    out = tmp_path / "out"
    data = tmp_path / "data"
    return MOD.run(transport=transport, out_dir=out, data_dir=data,
                   phase8n_dir=_fmp_coverage_dir(tmp_path), phase8q_dir=_phase8q_dir(tmp_path),
                   phase8p_dir=_phase8p_dir(tmp_path), verbose=False, **kw), out, data


def _read_csv(path):
    with open(path, "r", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _ok_transport(tk, endpoint):
    if endpoint == MOD.EP_EARNINGS_CALENDAR:
        return _EODHD_CALENDAR_OK
    return _EODHD_FUNDAMENTALS_OK


# --------------------------------------------------------------------------- #
# Secret discipline.
# --------------------------------------------------------------------------- #
def test_no_key_required_blocked_decision_and_setup_command(monkeypatch, tmp_path):
    monkeypatch.delenv(MOD.API_KEY_ENV, raising=False)
    report, out, _ = _run(tmp_path)
    assert report["eodhd_key_present"] is False
    assert report["api_key_logged"] is False
    assert report["decision"] == MOD.DEC_BLOCKED_NO_KEY
    assert "EODHD_API_KEY" in report["recommended_next_command"]
    assert report["network_used"] is False
    assert report["evaluation_executed"] is False


def test_key_value_never_written_to_committed_artifacts(monkeypatch, tmp_path):
    secret = "SECRETKEY_DO_NOT_LEAK_8R"
    monkeypatch.setenv(MOD.API_KEY_ENV, secret)
    report, out, _ = _run(tmp_path, transport=_ok_transport)
    for fp in out.glob("*"):
        if fp.is_file():
            text = fp.read_text(encoding="utf-8", errors="replace")
            assert secret not in text, "API key value leaked into %s" % fp.name
            assert "apikey=" not in text.lower(), "literal apikey= leaked into %s" % fp.name
            assert "api_token=" not in text.lower(), "literal api_token= leaked into %s" % fp.name
    assert report["secret_safety_leak_scan_clean"] is True
    assert report["api_key_logged"] is False


def test_key_presence_only_in_detection_report(monkeypatch, tmp_path):
    monkeypatch.setenv(MOD.API_KEY_ENV, "xyz")
    _, out, _ = _run(tmp_path, transport=_ok_transport)
    rows = _read_csv(out / MOD._ARTIFACTS["key_detection"])
    assert rows[0]["key_present"] == "True"
    assert rows[0]["value_read"] == "False"


# --------------------------------------------------------------------------- #
# Offline planning with no network and no key + artifact completeness.
# --------------------------------------------------------------------------- #
def test_offline_plan_runs_without_network_or_key(monkeypatch, tmp_path):
    monkeypatch.delenv(MOD.API_KEY_ENV, raising=False)
    report, out, _ = _run(tmp_path)
    assert report["evaluation_executed"] is False
    assert report["requests_made"] == 0
    for key, name in MOD._ARTIFACTS.items():
        assert (out / name).is_file(), "missing artifact %s" % name
    assert report["network_used"] is False


def test_all_13_artifacts_listed_in_outputs(monkeypatch, tmp_path):
    monkeypatch.delenv(MOD.API_KEY_ENV, raising=False)
    report, _, _ = _run(tmp_path)
    assert set(report["outputs"].keys()) == set(MOD._ARTIFACTS.keys())
    assert len(MOD._ARTIFACTS) == 13


def test_phase8q_decision_is_read(monkeypatch, tmp_path):
    monkeypatch.delenv(MOD.API_KEY_ENV, raising=False)
    report, _, _ = _run(tmp_path)
    assert report["phase8q_decision"] == "MIXED_FREE_PLUS_PAID_CORE_STACK"


# --------------------------------------------------------------------------- #
# EODHD tested first / request-plan prioritization.
# --------------------------------------------------------------------------- #
def test_request_plan_prioritizes_fmp_blocked_first(monkeypatch, tmp_path):
    monkeypatch.delenv(MOD.API_KEY_ENV, raising=False)
    _, out, _ = _run(tmp_path)
    rows = _read_csv(out / MOD._ARTIFACTS["request_plan"])
    # First distinct ticker must be a FMP-blocked one at priority tier 1.
    assert rows[0]["priority"] == "1_fmp_blocked"
    blocked = {"ABT", "ACN", "ADI", "ADP", "AMAT", "AMGN", "AON", "APD", "APH"}
    first_block = [r for r in rows if r["priority"] == "1_fmp_blocked"]
    assert {r["ticker"] for r in first_block} == blocked
    # Each ticker has two endpoint rows (fundamentals + earnings_calendar).
    abt = [r for r in rows if r["ticker"] == "ABT"]
    assert {r["endpoint"] for r in abt} == {MOD.EP_FUNDAMENTALS, MOD.EP_EARNINGS_CALENDAR}


def test_eodhd_is_the_test_first_provider(monkeypatch, tmp_path):
    monkeypatch.delenv(MOD.API_KEY_ENV, raising=False)
    report, out, _ = _run(tmp_path)
    assert report["provider"] == "EODHD"
    import json
    nxt = json.loads((out / MOD._ARTIFACTS["next_plan"]).read_text(encoding="utf-8"))
    assert nxt["test_first_provider"] == "EODHD"


# --------------------------------------------------------------------------- #
# Bounded request limit.
# --------------------------------------------------------------------------- #
def test_max_requests_is_honored(monkeypatch, tmp_path):
    monkeypatch.setenv(MOD.API_KEY_ENV, "k")
    calls = {"n": 0}

    def transport(tk, endpoint):
        calls["n"] += 1
        return _ok_transport(tk, endpoint)

    report, out, _ = _run(tmp_path, transport=transport, max_requests=5, max_tickers=50)
    assert report["requests_made"] <= 5
    assert calls["n"] <= 5


def test_skip_existing_skips_cached_tickers(monkeypatch, tmp_path):
    monkeypatch.setenv(MOD.API_KEY_ENV, "k")
    data = tmp_path / "data"
    norm = data / "eodhd" / "normalized" / "earnings.csv"
    norm.parent.mkdir(parents=True, exist_ok=True)
    with open(norm, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(MOD._NORMALIZED_HEADER)
        w.writerow(["ABT", "2023-12-31", "2024-01-24", "1.19", "1.18", "0.01", "0.85"])

    requested = []

    def transport(tk, endpoint):
        requested.append(tk)
        return _ok_transport(tk, endpoint)

    MOD.run(transport=transport, out_dir=tmp_path / "out", data_dir=data,
            phase8n_dir=_fmp_coverage_dir(tmp_path), phase8q_dir=_phase8q_dir(tmp_path),
            phase8p_dir=_phase8p_dir(tmp_path), skip_existing=True, verbose=False)
    assert "ABT" not in requested, "skip_existing should have skipped the cached ABT"
    rows = _read_csv(tmp_path / "out" / MOD._ARTIFACTS["request_plan"])
    abt = [r for r in rows if r["ticker"] == "ABT"][0]
    assert abt["skip_existing"] == "True"


# --------------------------------------------------------------------------- #
# Raw/normalized gitignored under research/data/eodhd only.
# --------------------------------------------------------------------------- #
def test_raw_and_normalized_are_gitignored(monkeypatch, tmp_path):
    monkeypatch.setenv(MOD.API_KEY_ENV, "k")
    _, out, data = _run(tmp_path, transport=_ok_transport, max_tickers=50)
    gi = data / "eodhd" / ".gitignore"
    assert gi.is_file()
    text = gi.read_text(encoding="utf-8")
    assert "raw/" in text and "normalized/" in text
    assert "*" in text and "!.gitignore" in text
    assert (data / "eodhd" / "normalized" / "earnings.csv").is_file()
    # No raw EODHD payload key leaks into a committed artifact.
    for fp in out.glob("**/*"):
        if fp.is_file():
            txt = fp.read_text(encoding="utf-8", errors="replace")
            for token in ("epsActual", "epsEstimate", "epsDifference", "surprisePercent"):
                assert token not in txt, "raw payload token %r leaked into %s" % (token, fp.name)


def test_no_raw_paid_data_in_committed_artifacts(monkeypatch, tmp_path):
    monkeypatch.setenv(MOD.API_KEY_ENV, "k")
    _, out, _ = _run(tmp_path, transport=_ok_transport, max_tickers=50)
    for fp in out.glob("*"):
        if fp.is_file():
            txt = fp.read_text(encoding="utf-8", errors="replace")
            for token in ("epsActual", "epsEstimate", "totalRevenue", "AnalystRatings"):
                assert token not in txt, "raw token %r in committed %s" % (token, fp.name)


# --------------------------------------------------------------------------- #
# Coverage, comparison, PIT readiness, scorecard.
# --------------------------------------------------------------------------- #
def test_coverage_by_family_and_pit_readiness_produced(monkeypatch, tmp_path):
    monkeypatch.setenv(MOD.API_KEY_ENV, "k")
    _, out, _ = _run(tmp_path, transport=_ok_transport, max_tickers=50)
    cov = _read_csv(out / MOD._ARTIFACTS["coverage_by_family"])
    fams = {r["endpoint_family"] for r in cov}
    assert fams == set(MOD._FAMILIES)
    earn = [r for r in cov if r["endpoint_family"] == MOD.FAM_EARNINGS][0]
    assert int(earn["tickers_with_data"]) > 0
    assert int(earn["tickers_backtest_usable"]) > 0  # deep PIT history -> usable
    pit = _read_csv(out / MOD._ARTIFACTS["pit_readiness"])
    pit_by_fam = {r["endpoint_family"]: r for r in pit}
    assert pit_by_fam[MOD.FAM_EARNINGS]["pit_safe_design"] == "True"
    assert pit_by_fam[MOD.FAM_ANALYST_RATINGS]["pit_safe_design"] == "False"


def test_comparison_against_fmp_and_alphavantage(monkeypatch, tmp_path):
    monkeypatch.setenv(MOD.API_KEY_ENV, "k")
    _, out, _ = _run(tmp_path, transport=_ok_transport, max_tickers=50)
    comp = _read_csv(out / MOD._ARTIFACTS["comparison"])
    assert comp
    header = comp[0].keys()
    for col in ("fmp_covered", "alphavantage_covered", "eodhd_covered", "eodhd_newly_covered"):
        assert col in header
    abt = [r for r in comp if r["ticker"] == "ABT"][0]
    assert abt["fmp_covered"] == "False"          # FMP-blocked
    assert abt["eodhd_covered"] == "True"          # EODHD covered it
    # ACN is FMP-blocked AND not Alpha-Vantage-covered -> EODHD newly covers it.
    acn = [r for r in comp if r["ticker"] == "ACN"][0]
    assert acn["fmp_covered"] == "False"
    assert acn["alphavantage_covered"] == "False"
    assert acn["eodhd_covered"] == "True"
    assert acn["eodhd_newly_covered"] == "True"


def test_vendor_scorecard_has_required_axes(monkeypatch, tmp_path):
    monkeypatch.setenv(MOD.API_KEY_ENV, "k")
    _, out, _ = _run(tmp_path, transport=_ok_transport, max_tickers=50)
    rows = _read_csv(out / MOD._ARTIFACTS["vendor_scorecard"])
    axes = {r["axis"] for r in rows}
    for required in ("coverage_100_500_tickers", "history_depth", "point_in_time_safety",
                     "update_frequency", "schema_stability", "bulk_batch_support",
                     "api_request_limits", "estimated_cost_tier", "data_family_breadth"):
        assert required in axes, "missing scorecard axis %s" % required


def test_procurement_questions_produced(monkeypatch, tmp_path):
    monkeypatch.delenv(MOD.API_KEY_ENV, raising=False)
    _, out, _ = _run(tmp_path)
    rows = _read_csv(out / MOD._ARTIFACTS["procurement_questions"])
    assert len(rows) >= 5
    assert all(r["question"] and r["why_it_matters"] for r in rows)


# --------------------------------------------------------------------------- #
# Decision outcomes (accept / promising / reject / blocked) and FMP fallback.
# --------------------------------------------------------------------------- #
def test_accept_when_broad_pit_coverage(monkeypatch, tmp_path):
    monkeypatch.setenv(MOD.API_KEY_ENV, "k")
    report, _, _ = _run(tmp_path, transport=_ok_transport, max_tickers=50)
    assert report["decision"] == MOD.DEC_ACCEPT
    assert report["earnings_point_in_time_safe"] is True
    assert report["fmp_premium_fallback_recommended"] is False


def test_reject_when_no_usable_coverage_recommends_fmp_fallback(monkeypatch, tmp_path):
    monkeypatch.setenv(MOD.API_KEY_ENV, "k")
    report, out, _ = _run(tmp_path, transport=lambda tk, ep: _EODHD_EMPTY, max_tickers=50)
    assert report["decision"] == MOD.DEC_REJECTED
    assert report["fmp_premium_fallback_recommended"] is True
    fb = _read_csv(out / MOD._ARTIFACTS["fmp_fallback_plan"])
    trigger = [r for r in fb if r["item"] == "trigger_condition"][0]
    assert trigger["value"] == "RECOMMENDED"


def test_rate_limit_stops_and_decides_promising(monkeypatch, tmp_path):
    monkeypatch.setenv(MOD.API_KEY_ENV, "k")

    def transport(tk, endpoint):
        raise MOD.EodhdError("HTTP 429", status_code=429, error_type="rate_limited")

    report, _, _ = _run(tmp_path, transport=transport, max_tickers=50)
    assert report["rate_limited"] is True
    assert report["evaluation_stopped_early"] is True
    assert report["decision"] == MOD.DEC_PROMISING


def test_invalid_key_stops_immediately_and_blocks(monkeypatch, tmp_path):
    monkeypatch.setenv(MOD.API_KEY_ENV, "k")

    def transport(tk, endpoint):
        raise MOD.EodhdError("HTTP 401", status_code=401, error_type="invalid_key")

    report, _, _ = _run(tmp_path, transport=transport, max_tickers=50)
    assert report["eodhd_key_invalid"] is True
    assert report["decision"] == MOD.DEC_BLOCKED_NO_KEY
    assert report["requests_made"] == 1


def test_free_tier_block_needs_vendor_quotes(monkeypatch, tmp_path):
    """A VALID key on EODHD's free tier (HTTP 403 'Only EOD data allowed for free users') is NOT an
    invalid key: it must decide NEEDS_VENDOR_QUOTES (subscribe to a paid plan), keep the key marked
    valid, stop after one request, and leave the FMP fallback on standby (EODHD untested, not failed)."""
    monkeypatch.setenv(MOD.API_KEY_ENV, "k")

    def transport(tk, endpoint):
        raise MOD.EodhdError("HTTP 403", status_code=403, error_type="free_tier_blocked")

    report, out, _ = _run(tmp_path, transport=transport, max_tickers=50)
    assert report["eodhd_free_tier_blocked"] is True
    assert report["eodhd_key_invalid"] is False
    assert report["eodhd_key_present"] is True
    assert report["decision"] == MOD.DEC_NEEDS_QUOTES
    assert report["fmp_premium_fallback_recommended"] is False
    assert report["requests_made"] == 1
    assert report["evaluation_stopped_early"] is True
    fb = _read_csv(out / MOD._ARTIFACTS["fmp_fallback_plan"])
    trigger = [r for r in fb if r["item"] == "trigger_condition"][0]
    assert trigger["value"] == "ON_STANDBY"


def test_systematic_plan_block_rejects_and_recommends_fallback(monkeypatch, tmp_path):
    monkeypatch.setenv(MOD.API_KEY_ENV, "k")

    def transport(tk, endpoint):
        raise MOD.EodhdError("HTTP 402", status_code=402, error_type="plan_blocked")

    report, _, _ = _run(tmp_path, transport=transport, max_tickers=50)
    assert report["plan_blocked_systematic"] is True
    assert report["decision"] == MOD.DEC_REJECTED
    assert report["fmp_premium_fallback_recommended"] is True


# --------------------------------------------------------------------------- #
# FMP Ultimate rejected + decision vocabulary + safety contract.
# --------------------------------------------------------------------------- #
def test_fmp_ultimate_is_rejected(monkeypatch, tmp_path):
    monkeypatch.delenv(MOD.API_KEY_ENV, raising=False)
    report, out, _ = _run(tmp_path)
    assert report["fmp_ultimate_rejected"] is True
    cv = _read_csv(out / MOD._ARTIFACTS["cost_value_decision"])
    ult = [r for r in cv if r["provider"] == "FMP Ultimate"][0]
    assert ult["decision"] == "REJECTED"
    fb = _read_csv(out / MOD._ARTIFACTS["fmp_fallback_plan"])
    ult_fb = [r for r in fb if r["item"] == "fmp_ultimate"][0]
    assert ult_fb["value"] == "REJECTED"


def test_decision_in_allowed_vocabulary(monkeypatch, tmp_path):
    monkeypatch.delenv(MOD.API_KEY_ENV, raising=False)
    report, _, _ = _run(tmp_path)
    assert report["decision"] in MOD.ALLOWED_DECISIONS


def test_safety_contract_flags(monkeypatch, tmp_path):
    monkeypatch.delenv(MOD.API_KEY_ENV, raising=False)
    report, _, _ = _run(tmp_path)
    for flag in ("preview_only",):
        assert report[flag] is True
    for flag in ("orders_enabled", "automation_enabled", "broker_execution_enabled",
                 "paper_trader_touched", "gcp_touched", "deployed", "data_fabricated",
                 "full_sp500_backfill_run", "raw_paid_data_in_artifacts", "committed"):
        assert report[flag] is False


def test_no_paper_trader_or_gcp_or_order_logic_in_source():
    src = Path(MOD.__file__).read_text(encoding="utf-8")
    low = src.lower()
    for forbidden in ("create_order", "place_order", "submit_order", "execute_order",
                      "import paper", "from api.app", "gcloud", "subprocess"):
        assert forbidden not in low, "forbidden token %r found in source" % forbidden


def test_no_full_sp500_backfill_default_bounds():
    assert MOD.DEFAULT_MAX_TICKERS == 50
    assert MOD.DEFAULT_MAX_REQUESTS == 100


def test_redact_url_strips_api_token():
    url = "https://eodhd.com/api/fundamentals/ABT.US?fmt=json&api_token=SECRET123"
    red = MOD.redact_url(url)
    assert "SECRET123" not in red
    assert "api_token=" not in red.lower()
    assert "fmt=json" in red
