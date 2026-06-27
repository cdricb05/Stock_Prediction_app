"""Fully-offline tests for Phase 8-Y orthogonal data-family acquisition + strong-alpha re-run.

No real key, no network, no writes outside a tmp dir. Provider probing/entitlement/selection are
driven by INJECTED transports (a callable that returns a synthetic payload or raises a ProbeError);
the acquire -> normalize-PIT -> feature -> re-run discovery path is exercised end-to-end on the same
synthetic 545-style expanded universe the Phase 8-X tests use:

  * a PREDICTIVE new feature (value monotone in the surprise rank that drives the forward return)
    clears the full Phase-8-X strong gate -> STRONG_ALPHA_FOUND.
  * a NON-predictive (scrambled) new feature with sufficient coverage produces no strong alpha ->
    NEW_DATA_FAMILY_EXHAUSTED (weak/constrained is never promoted).
  * with no provider key and no transport every family is MISSING_KEY -> NEEDS_PROVIDER_PURCHASE.

Selection priority, one-blocked-provider-does-not-stop-the-phase, entitlement classification, PIT
enforcement, payload gitignoring, and the no-key/no-order/no-paper-trader contract are all asserted.
"""
from __future__ import annotations

import csv
import datetime as dt
import importlib
import json
import urllib.parse
from pathlib import Path

import pytest

MOD = importlib.import_module("research.run_phase8y_orthogonal_data_family_acquisition")
o8 = MOD.o8
r8 = MOD.r8

# --- synthetic universe (mirrors the Phase 8-X test harness) --- #
N_OLD = 10
N_NEW = 10
OLD_TICKERS = ["OLD%s" % chr(65 + i) for i in range(N_OLD)]
NEW_TICKERS = ["NEW%s" % chr(65 + i) for i in range(N_NEW)]
ALL_TICKERS = OLD_TICKERS + NEW_TICKERS
ETF = "ETF1"
START = dt.date(2018, 1, 1)
END = dt.date(2024, 3, 31)
_QENDS = [dt.date(y, m, d) for y in (2018, 2019, 2020, 2021, 2022, 2023)
          for (m, d) in ((3, 31), (6, 30), (9, 30), (12, 31))]
LOW_TICKERS = 8
LOW_EVENTS = 200
LOW_COVERAGE = 150


def _business_days(a, b):
    days, cur = [], a
    while cur <= b:
        if cur.weekday() < 5:
            days.append(cur)
        cur += dt.timedelta(days=1)
    return days


def _report_date(qend):
    return qend + dt.timedelta(days=45)


def _filing_date(qend):
    return qend + dt.timedelta(days=40)


def _surprise_rank(idx):
    return (idx % 5) - 2


def _adj_close(idx, d_idx, flat=False):
    # multiplicative growth monotone in the surprise rank (both cohorts carry it -> a broad signal can
    # be genuinely strong across cohorts/subperiods/sectors). `flat` -> constant price (no
    # cross-sectional return structure at all), so NOTHING can be strong (used for the exhausted test).
    g = 0.0 if flat else 0.0003 * _surprise_rank(idx)
    return (100.0 + idx) * (1.0 + g) ** d_idx


def _write_expanded_panel(path, flat=False):
    days = _business_days(START, END)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["date", "ticker", "adjusted_open", "adjusted_high", "adjusted_low",
                    "adjusted_close", "volume", "dollar_volume", "benchmark_close", "daily_return"])
        for d_idx, day in enumerate(days):
            bench = 1000.0 + 0.4 * d_idx
            ds = day.isoformat()
            for idx, tk in enumerate(ALL_TICKERS):
                c = _adj_close(idx, d_idx, flat)
                dv = 1000 * c * (1 + idx)
                w.writerow([ds, tk, c, c, c, c, 1000 * (1 + idx), dv, bench, 0.0])
            w.writerow([ds, ETF, bench / 10, bench / 10, bench / 10, bench / 10, 5000,
                        5000 * bench / 10, bench, 0.0])


def _write_sector_map(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    sectors = ["Information Technology", "Health Care", "Financials", "Industrials"]
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["ticker", "sector", "industry", "source", "as_of_date", "point_in_time", "notes"])
        for i, tk in enumerate(ALL_TICKERS):
            w.writerow([tk, sectors[i % len(sectors)], "Industry%d" % i, "test", "2026-01-01",
                        "False", ""])
        w.writerow([ETF, "Index", "ETF", "test", "2026-01-01", "False", ""])


def _write_macro(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["observation_date", "DGS10", "DGS2"])
        cur, k = dt.date(2017, 12, 1), 0
        while cur <= dt.date(2024, 5, 1):
            ten = 1.5 + 0.5 * ((k % 12) / 12.0)
            two = 0.5 + 0.7 * ((k % 12) / 12.0)
            w.writerow([cur.isoformat(), round(ten, 2), round(two, 2)])
            nm = cur.month + 1
            ny = cur.year + (1 if nm > 12 else 0)
            cur = dt.date(ny, ((nm - 1) % 12) + 1, 1)
            k += 1


def _fundamentals_payload(ticker):
    idx = ALL_TICKERS.index(ticker) if ticker in ALL_TICKERS else 0
    history, inc, bal, cfs = {}, {}, {}, {}
    for q, qend in enumerate(_QENDS):
        fiscal = qend.isoformat()
        actual = 1.0 + 0.1 * q + 0.01 * idx
        surprise = 0.05 * _surprise_rank(idx) + 0.002 * ((q % 3) - 1)
        estimate = actual - surprise
        history[fiscal] = {"date": fiscal, "reportDate": _report_date(qend).isoformat(),
                           "epsActual": round(actual, 4), "epsEstimate": round(estimate, 4),
                           "epsDifference": round(surprise, 4),
                           "surprisePercent": round(surprise / abs(estimate) * 100.0, 4) if estimate else 0.0}
        fd = _filing_date(qend).isoformat()
        inc[fiscal] = {"date": fiscal, "filing_date": fd, "totalRevenue": str(1000 + 10 * q + idx),
                       "netIncome": str(100 + q + 0.1 * idx), "grossProfit": str(400 + q + idx),
                       "operatingIncome": str(200 + q)}
        bal[fiscal] = {"date": fiscal, "filing_date": fd, "totalAssets": str(5000 + idx),
                       "totalLiab": str(2000 + idx), "shortLongTermDebtTotal": str(800 + idx),
                       "cashAndShortTermInvestments": str(300 + q)}
        cfs[fiscal] = {"date": fiscal, "filing_date": fd, "freeCashFlow": str(50 + q)}
    return {"General": {"Code": ticker, "Sector": "Technology"},
            "Earnings": {"History": history},
            "Financials": {"Income_Statement": {"quarterly": inc},
                           "Balance_Sheet": {"quarterly": bal},
                           "Cash_Flow": {"quarterly": cfs}}}


def _seed_earnings_cache(data_dir):
    rp = r8._Paths(data_dir=data_dir)
    normalized = {}
    for tk in ALL_TICKERS:
        payload = _fundamentals_payload(tk)
        r8._persist_raw(rp, tk, "fundamentals", payload)
        normalized[tk] = r8._normalize_earnings(tk, payload)
    r8._append_normalized(rp, normalized)


def _write_phase8v_report(path):
    path.mkdir(parents=True, exist_ok=True)
    payload = {"phase": "8-V", "decision": "EXPANDED_UNIVERSE_WEAKENS_ALPHA",
               "newly_scoreable_tickers": NEW_TICKERS}
    with open(path / MOD._PHASE8V_REPORT, "w", encoding="utf-8") as fh:
        json.dump(payload, fh)


def _write_phase8x_report(path, decision="NEEDS_NEW_DATA_FAMILY"):
    path.mkdir(parents=True, exist_ok=True)
    payload = {"phase": "8-X", "decision": decision, "decision_is_terminal": True,
               "recommended_new_data_family": "analyst_estimate_revisions + short_interest + ...",
               "universe": {"scoreable_tickers": N_OLD + N_NEW, "usable_events": 999}}
    with open(path / MOD._PHASE8X_REPORT, "w", encoding="utf-8") as fh:
        json.dump(payload, fh)


def _build_env(tmp_path, phase8x_decision="NEEDS_NEW_DATA_FAMILY", flat=False):
    panel = tmp_path / "expanded_panel.csv"
    sector = tmp_path / "sector.csv"
    rates = tmp_path / "rates.csv"
    _write_expanded_panel(panel, flat)
    _write_sector_map(sector)
    _write_macro(rates)
    _write_phase8v_report(tmp_path / "phase8v")
    _write_phase8x_report(tmp_path / "phase8x", phase8x_decision)
    data = tmp_path / "data"
    _seed_earnings_cache(data)
    return panel, sector, rates, data


def _run(tmp_path, panel, sector, rates, data, out_name, transport=None, **kw):
    out = tmp_path / out_name
    report = MOD.run(out_dir=out, phase8x_dir=tmp_path / "phase8x", phase8v_dir=tmp_path / "phase8v",
                     price_csv=panel, data_dir=data, sector_csv=sector, macro={"rates": rates},
                     transport=transport, verbose=False, **kw)
    return report, out


def _read_csv(path):
    with open(path, "r", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


# --------------------------------------------------------------------------- #
# Transports (synthetic provider payloads; no network).
# --------------------------------------------------------------------------- #
def _ticker_from_url(url):
    parts = urllib.parse.urlsplit(url)
    qs = urllib.parse.parse_qs(parts.query)
    for k in ("symbol", "tickers", "s"):
        if k in qs and qs[k]:
            return qs[k][0].split(".")[0].upper()
    return parts.path.rstrip("/").split("/")[-1].upper()


def _feature_value(idx, predictive):
    if predictive:
        return float(_surprise_rank(idx))            # monotone in the forward-return driver
    return float(((idx * 7) % 13) - 6)               # permutation-scrambled -> ~0 cross-sectional IC


def _make_acquire_transport(predictive):
    def _t(url):
        tk = _ticker_from_url(url)
        if tk not in ALL_TICKERS:                    # probe symbol (AAPL): reachable, generic payload
            return {"records": [{"available_date": "2017-01-01", "value": 1.0}]}
        idx = ALL_TICKERS.index(tk)
        # one PIT record per ticker, available well before any entry date (no lookahead)
        return {"records": [{"available_date": "2017-06-01", "value": _feature_value(idx, predictive)}]}
    return _t


def _blocked(*_a, **_k):
    raise o8.ProbeError("subscription blocked", status_code=402, error_type="http_error")


# --------------------------------------------------------------------------- #
# Module-scoped heavy runs.
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def env(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("phase8y")
    panel, sector, rates, data = _build_env(tmp, "NEEDS_NEW_DATA_FAMILY")
    return tmp, panel, sector, rates, data


@pytest.fixture(scope="module")
def strong_run(env):
    tmp, panel, sector, rates, data = env
    return _run(tmp, panel, sector, rates, data, "out_strong",
                transport=_make_acquire_transport(True),
                min_tickers=LOW_TICKERS, min_events=LOW_EVENTS, min_coverage=LOW_COVERAGE)


@pytest.fixture(scope="module")
def flat_env(tmp_path_factory):
    # flat returns -> no cross-sectional structure for ANY factor (base or new) -> nothing strong.
    tmp = tmp_path_factory.mktemp("phase8y_flat")
    panel, sector, rates, data = _build_env(tmp, "NEEDS_NEW_DATA_FAMILY", flat=True)
    return tmp, panel, sector, rates, data


@pytest.fixture(scope="module")
def exhausted_run(flat_env):
    tmp, panel, sector, rates, data = flat_env
    return _run(tmp, panel, sector, rates, data, "out_exhausted",
                transport=_make_acquire_transport(False),
                min_tickers=LOW_TICKERS, min_events=LOW_EVENTS, min_coverage=LOW_COVERAGE)


@pytest.fixture(scope="module")
def purchase_run(env):
    # no transport + no provider keys -> every provider MISSING_KEY -> NEEDS_PROVIDER_PURCHASE.
    tmp, panel, sector, rates, data = env
    return _run(tmp, panel, sector, rates, data, "out_purchase",
                min_tickers=LOW_TICKERS, min_events=LOW_EVENTS, min_coverage=LOW_COVERAGE)


# --------------------------------------------------------------------------- #
# Fast unit tests (no event-table build).
# --------------------------------------------------------------------------- #
def test_entitlement_classification_states():
    # ProbeError HTTP statuses map to the right entitlement states.
    assert MOD.classify_entitlement(o8.ProbeError("x", status_code=402, error_type="http_error"),
                                    True)[0] == MOD.ENT_BLOCKED
    assert MOD.classify_entitlement(o8.ProbeError("x", status_code=429, error_type="http_error"),
                                    True)[0] == MOD.ENT_RATE
    assert MOD.classify_entitlement(o8.ProbeError("x", status_code=404, error_type="http_error"),
                                    True)[0] == MOD.ENT_NOTFOUND
    assert MOD.classify_entitlement(o8.ProbeError("x", error_type="bad_response"),
                                    True)[0] == MOD.ENT_PARSE
    # a real list/records payload -> ACCESS_VERIFIED; an AV rate-limit envelope -> RATE_LIMITED.
    assert MOD.classify_entitlement([{"a": 1}], True)[0] == MOD.ENT_VERIFIED
    assert MOD.classify_entitlement({"Note": "rate limit"}, True)[0] == MOD.ENT_RATE


def test_missing_key_is_classified_not_invalid():
    # no key + no transport -> MISSING_KEY (explicitly "not invalid"), never a key read.
    fam = MOD.ORTHOGONAL_FAMILIES[0]
    prov = fam["providers"][0]
    row = MOD.probe_provider(fam, prov, key_present=False, live=True, transport=None)
    assert row["entitlement"] == MOD.ENT_MISSING
    assert row["probed"] is False
    assert "not invalid" in row["note"]
    # the persisted endpoint is redacted: no key query parameter survives.
    assert "apikey=" not in row["endpoint_redacted"]
    assert "token=" not in row["endpoint_redacted"]


class _Log:
    def step(self, *a, **k):
        pass


def test_selection_follows_priority_and_one_blocked_does_not_stop():
    # estimate_revisions (priority 1) is blocked on every provider; recommendation_changes
    # (priority 2) AND price_target_revisions (priority 3) are accessible. The SELECTED family must
    # be the higher-priority accessible one (recommendation_changes), and the blocked priority-1
    # family must NOT abort the audit.
    def finnhub(url):
        if "eps-estimate" in url:                    # the estimate-revisions endpoint
            raise o8.ProbeError("blocked", status_code=402, error_type="http_error")
        return [{"buy": 5, "sell": 1}]               # recommendation / price-target -> verified
    tmap = {"Finnhub": finnhub, "FMP": _blocked}
    rows = MOD.audit_entitlement(live=True, transport=None, transport_map=tmap, log=_Log())
    fam, prov, sel = MOD.select_family(rows)
    assert fam is not None and fam["family"] == "analyst_recommendation_changes"
    # priority-1 family appears as ENTITLEMENT_BLOCKED but the phase continued past it.
    er = {r["family"] + "/" + r["provider"]: r["entitlement"] for r in rows}
    assert er["analyst_estimate_revisions/Finnhub"] == MOD.ENT_BLOCKED
    assert er["analyst_recommendation_changes/Finnhub"] == MOD.ENT_VERIFIED
    sel_status = {r["family"]: r["status"] for r in sel}
    assert sel_status["analyst_recommendation_changes"] == "SELECTED"
    assert sel_status["price_target_revisions"] == "ACCESSIBLE"      # accessible but lower priority


def test_pit_normalization_drops_future_and_undated_records(tmp_path):
    fam = MOD._FAMILY_BY_NAME["news_social_sentiment"]
    provider_dir = tmp_path / "prov"
    raw_dir = provider_dir / "raw" / fam["family"]
    raw_dir.mkdir(parents=True, exist_ok=True)
    # one valid PIT record, one dated AFTER as_of (future leak), one with no date (not PIT-safe).
    (raw_dir / "AAA.json").write_text(json.dumps({"records": [
        {"available_date": "2020-01-01", "value": 0.5},
        {"available_date": "2099-01-01", "value": 9.9},
        {"value": 1.0}]}), encoding="utf-8")
    out_csv, manifest, audit = MOD.normalize_pit(fam, raw_dir, provider_dir, as_of="2026-06-26",
                                                 log=_Log())
    rows = _read_csv(out_csv)
    assert len(rows) == 1 and rows[0]["available_date"] == "2020-01-01"
    statuses = sorted(a["status"] for a in audit)
    assert "OK" in statuses and "DROPPED_NO_PIT_DATE" in statuses
    assert manifest[0]["gitignored"] is True


# --------------------------------------------------------------------------- #
# Heavy end-to-end tests.
# --------------------------------------------------------------------------- #
def test_reads_phase8x_and_required_artifacts(strong_run):
    report, out = strong_run
    assert report["builds_on_phase8x_decision"] == "NEEDS_NEW_DATA_FAMILY"
    assert report["phase8x_precondition_met"] is True
    assert len(MOD._REQUIRED_ARTIFACTS) == 16
    for key in MOD._REQUIRED_ARTIFACTS:
        assert (out / MOD._ARTIFACTS[key]).is_file(), "missing %s" % MOD._ARTIFACTS[key]
    assert report["decision"] in MOD.ALLOWED_DECISIONS
    assert report["decision_is_terminal"] is True


def test_strong_alpha_found_on_predictive_feature(strong_run):
    report, out = strong_run
    assert report["selected_data_family"] == "analyst_estimate_revisions"  # priority-1, accessible
    assert report["access_status"] == MOD.ENT_VERIFIED
    assert report["acquisition"]["tickers_acquired"] >= N_OLD + N_NEW - 1
    assert report["feature_coverage_events"] > LOW_COVERAGE
    assert report["decision"] == MOD.DEC_STRONG
    assert report["strong_alpha_found"] is True
    strong = _read_csv(out / MOD._ARTIFACTS["strong_candidates"])
    assert len(strong) >= 1
    for r in strong:
        assert float(r["ic_t"]) >= MOD.STRONG_MIN_IC_T
        assert r["subperiod_stable"] == "True"
    # the new feature is catalogued as NOT a duplicate of the existing EODHD features.
    cat = _read_csv(out / MOD._ARTIFACTS["feature_catalog"])
    assert cat and any("est_rev_breadth" == r["feature"] for r in cat)
    assert all("NO" in r["duplicates_existing_eodhd"] for r in cat)


def test_weak_feature_not_promoted_exhausted(exhausted_run):
    report, out = exhausted_run
    assert report["selected_data_family"] == "analyst_estimate_revisions"
    assert report["feature_coverage_events"] > LOW_COVERAGE      # enough coverage to judge
    assert report["strong_alpha_found"] is False
    assert report["strong_alpha_candidates"] == []
    assert report["decision"] == MOD.DEC_EXHAUSTED
    # nothing weak/constrained leaked into the strong-candidates artifact.
    assert _read_csv(out / MOD._ARTIFACTS["strong_candidates"]) == []
    rej = _read_csv(out / MOD._ARTIFACTS["rejected"])
    assert rej and all(r["status"] != "strong" for r in rej)


def test_no_key_yields_needs_provider_purchase(purchase_run):
    report, out = purchase_run
    assert report["selected_data_family"] is None
    assert report["decision"] == MOD.DEC_PURCHASE
    assert report["provider_purchase_needed"] is True
    assert report["recommended_provider_purchase"]
    matrix = _read_csv(out / MOD._ARTIFACTS["entitlement_matrix"])
    keyed = [r for r in matrix if r["provider"] != "(none mapped)"]
    assert keyed and all(r["entitlement"] == MOD.ENT_MISSING for r in keyed)
    # next plan names the purchase + is preview-only.
    plan = MOD._read_json(out / MOD._ARTIFACTS["next_plan"])
    assert plan["recommended_provider_purchase"]
    assert plan["preview_only"] is True and plan["orders_enabled"] is False


def test_raw_and_normalized_payloads_gitignored(strong_run):
    _, out = strong_run
    # the provider data tree (under the test data_dir) is force-gitignored for raw + normalized.
    gis = list(Path(MOD._DATA_ROOT).glob("*/.gitignore"))  # repo-level providers (sanity)
    # the acquisition wrote under the test data_dir; assert its manifests flag gitignored=True.
    raw = _read_csv(out / MOD._ARTIFACTS["raw_manifest"])
    norm = _read_csv(out / MOD._ARTIFACTS["norm_manifest"])
    assert raw and all(r["gitignored"] == "True" for r in raw)
    assert norm and all(r["gitignored"] == "True" for r in norm)
    pit = _read_csv(out / MOD._ARTIFACTS["pit_audit"])
    assert pit and all(r["pit_ok"] == "True" for r in pit)  # synthetic records are all PIT-safe


def test_secret_safety_and_no_forbidden_contract(strong_run):
    report, out = strong_run
    audit = {r["check"]: r["result"] for r in _read_csv(out / MOD._ARTIFACTS["secret_audit"])}
    assert audit["secret_leak_scan_clean"] == "True"
    assert audit["api_key_printed"] == "False"
    assert audit["api_key_written_to_disk"] == "False"
    assert audit["network_used"] == "False"            # transport injected -> no real network
    for flag in ("orders_enabled", "automation_enabled", "broker_execution_enabled",
                 "paper_trader_touched", "gcp_touched", "deployed", "committed",
                 "full_regression_invoked", "constrained_signal_productized", "api_key_printed",
                 "api_key_written_to_disk", "packages_installed", "data_fabricated"):
        assert report[flag] is False
    assert report["preview_only"] is True
    assert report["raw_payloads_gitignored"] is True
    assert report["normalized_payloads_gitignored"] is True


def test_hard_blocker_when_phase8x_precondition_not_met(tmp_path):
    # 8-X decision is NOT NEEDS_NEW_DATA_FAMILY -> there is no mandate -> HARD_BLOCKER, all artifacts.
    panel, sector, rates, data = _build_env(tmp_path, phase8x_decision="STRONG_ALPHA_FOUND")
    report, out = _run(tmp_path, panel, sector, rates, data, "out_block",
                       min_tickers=LOW_TICKERS, min_events=LOW_EVENTS)
    assert report["decision"] == MOD.DEC_BLOCKER
    assert report["phase8x_precondition_met"] is False
    for key in MOD._REQUIRED_ARTIFACTS:
        assert (out / MOD._ARTIFACTS[key]).is_file()


def test_no_forbidden_logic_in_source():
    src = Path(MOD.__file__).read_text(encoding="utf-8").lower()
    for bad in ("import subprocess", "subprocess.run", "subprocess.call", "pytest.main",
                "os.system", "place_order", "submit_order", "create_order", "broker.execute",
                "enable_automation", "gcp_deploy", "deploy_to_"):
        assert bad not in src, "forbidden token in source: %s" % bad


# --------------------------------------------------------------------------- #
# EODHD news/social-sentiment normalizer (the dict-of-symbol payload shape).
# --------------------------------------------------------------------------- #
def _eodhd_sentiment_payload(provider_symbol, recs):
    # EODHD /sentiments returns a top-level dict keyed by provider symbol -> list of daily records.
    return {provider_symbol: recs}


def _seed_eodhd_sentiment(data_dir, symbols, as_of_future="2099-01-01"):
    raw_dir = data_dir / "eodhd" / "raw" / "news_social_sentiment"
    raw_dir.mkdir(parents=True, exist_ok=True)
    for tk in symbols:
        idx = ALL_TICKERS.index(tk) if tk in ALL_TICKERS else 0
        # scrambled (non-predictive) sentiment so the new family cannot accidentally clear the gate.
        val = round(0.10 * (((idx * 7) % 13) - 6), 4)
        recs = [
            {"date": "2018-06-01", "count": 5, "normalized": val},
            {"date": "2019-06-01", "count": 7, "normalized": val},
            {"date": as_of_future, "count": 1, "normalized": 0.99},   # future -> dropped (PIT)
            {"count": 3, "normalized": 0.50},                          # no date -> dropped (PIT)
        ]
        (raw_dir / ("%s.json" % tk)).write_text(json.dumps(_eodhd_sentiment_payload("%s.US" % tk, recs)),
                                                encoding="utf-8")
    return raw_dir


def test_eodhd_dict_payload_normalizes_to_rows(tmp_path):
    fam = MOD._FAMILY_BY_NAME["news_social_sentiment"]
    provider_dir = tmp_path / "eodhd"
    raw_dir = provider_dir / "raw" / fam["family"]
    raw_dir.mkdir(parents=True, exist_ok=True)
    # the exact shape from the diagnostic: dict keyed by "A.US", value is a list of daily records.
    (raw_dir / "A.json").write_text(json.dumps({"A.US": [
        {"date": "2026-06-26", "count": 1, "normalized": 0.999},
        {"date": "2026-06-25", "count": 5, "normalized": 0.9958},
        {"date": "2099-01-01", "count": 9, "normalized": 0.10},   # future -> dropped
        {"count": 4, "normalized": 0.20}]}), encoding="utf-8")    # missing date -> dropped
    (raw_dir / "AAPL.json").write_text(json.dumps({"AAPL.US": [
        {"date": "2026-06-20", "count": 8, "normalized": -0.4}]}), encoding="utf-8")
    out_csv, manifest, audit = MOD.normalize_pit(fam, raw_dir, provider_dir, as_of="2026-06-26",
                                                 log=_Log(), provider_name="EODHD")
    rows = _read_csv(out_csv)
    # two kept rows for A + one for AAPL = 3; future + undated dropped.
    assert manifest[0]["rows"] == 3 and len(rows) == 3
    assert manifest[0]["tickers"] == 2
    # the normalized PIT schema carries all required fields.
    assert set(rows[0].keys()) >= {"ticker", "provider_symbol", "available_date", "news_sentiment",
                                   "news_count", "source_family", "source_provider"}
    by = {(r["ticker"], r["available_date"]): r for r in rows}
    a = by[("A", "2026-06-26")]
    assert a["provider_symbol"] == "A.US"               # raw key preserved
    assert a["ticker"] == "A"                           # provider suffix stripped
    assert float(a["news_sentiment"]) == 0.999          # normalized -> news_sentiment
    assert float(a["news_count"]) == 1.0                # count -> news_count
    assert a["source_family"] == "news_social_sentiment"
    assert a["source_provider"] == "EODHD"
    aapl = by[("AAPL", "2026-06-20")]
    assert aapl["provider_symbol"] == "AAPL.US" and aapl["ticker"] == "AAPL"
    assert float(aapl["news_sentiment"]) == -0.4
    # PIT audit records both the kept and the dropped (future / undated) rows.
    statuses = sorted({a["status"] for a in audit})
    assert "OK" in statuses
    assert "DROPPED_FUTURE_DATE" in statuses
    assert "DROPPED_NO_PIT_DATE" in statuses


def test_derive_decision_raw_present_zero_norm_is_blocker():
    fam = MOD._FAMILY_BY_NAME["news_social_sentiment"]
    # raw payloads present, but the normalizer produced 0 rows -> PARSER_OR_NORMALIZATION_BLOCKER,
    # NEVER READY_FOR_NEXT_ORTHOGONAL_BATCH.
    dec, _ = MOD.derive_decision(True, fam, coverage=0, min_coverage=200, camp=None,
                                 n_tickers=600, n_events=40000, min_tickers=500, min_events=30000,
                                 raw_count=500, norm_rows=0)
    assert dec == MOD.DEC_PARSER_BLOCKER
    assert dec != MOD.DEC_NEXT_BATCH
    # after the fix (rows > 0) with full coverage and no strong alpha -> honest EXHAUSTED.
    dec2, _ = MOD.derive_decision(True, fam, coverage=40000, min_coverage=200, camp={"strong": [],
                                  "constrained": [], "rejected": [], "candidates": []},
                                  n_tickers=600, n_events=40000, min_tickers=500, min_events=30000,
                                  raw_count=500, norm_rows=500)
    assert dec2 == MOD.DEC_EXHAUSTED


def _raise_if_called(url):
    raise AssertionError("normalize-existing-only must not make any provider API call: %s" % url)


@pytest.fixture(scope="module")
def normalize_existing_run(tmp_path_factory):
    # Build a FLAT env (constant returns -> nothing strong, base factors included) so the decision is
    # an honest non-strong terminal state, then seed CACHED EODHD raw sentiment and re-normalize from
    # disk WITHOUT any API call (no provider key set).
    tmp = tmp_path_factory.mktemp("phase8y_norm_existing")
    panel, sector, rates, data = _build_env(tmp, "NEEDS_NEW_DATA_FAMILY", flat=True)
    _seed_eodhd_sentiment(data, ALL_TICKERS)
    # transport raises if touched -> proves no acquisition/probe call is made.
    return _run(tmp, panel, sector, rates, data, "out_norm_existing",
                transport=_raise_if_called, normalize_existing_only=True,
                family="news_social_sentiment", max_tickers=0, max_requests=0,
                min_tickers=LOW_TICKERS, min_events=LOW_EVENTS, min_coverage=LOW_COVERAGE)


def test_normalize_existing_only_normalizes_cached_without_api(normalize_existing_run):
    report, out = normalize_existing_run
    assert report["normalize_existing_only"] is True
    assert report["selected_data_family"] == "news_social_sentiment"
    assert report["normalized_rows"] > 0                      # parser fixed: cached raw -> rows
    assert report["normalized_tickers"] > 0
    assert report["parser_bug_fixed"] is True
    assert report["network_used"] is False                   # no provider call (transport raised)
    assert report["acquisition"]["requests_made"] == 0
    # raw present + rows > 0 -> NOT the parser-blocker, and an honest non-strong terminal decision.
    assert report["decision"] in (MOD.DEC_EXHAUSTED, MOD.DEC_NEXT_BATCH)
    assert report["decision"] != MOD.DEC_PARSER_BLOCKER
    assert report["strong_alpha_found"] is False
    # the normalized manifest reflects real rows for the news feature.
    norm = _read_csv(out / MOD._ARTIFACTS["norm_manifest"])
    assert norm and int(norm[0]["rows"]) > 0 and norm[0]["feature"] == "news_sentiment"
    # the normalized CSV carries the richer PIT-safe sentiment schema.
    npath = MOD._REPO_ROOT / norm[0]["normalized_path"]
    if npath.is_file():
        ncols = set(_read_csv(npath)[0].keys())
        assert {"ticker", "provider_symbol", "available_date", "news_sentiment",
                "news_count", "source_provider"} <= ncols


def test_normalize_existing_only_requires_no_api_key(normalize_existing_run):
    # no provider key is set in the test environment and the run still produced rows -> no key needed.
    report, out = normalize_existing_run
    assert report["api_key_printed"] is False
    assert report["api_key_written_to_disk"] is False
    audit = {r["check"]: r["result"] for r in _read_csv(out / MOD._ARTIFACTS["secret_audit"])}
    assert audit["network_used"] == "False"
    assert audit["secret_leak_scan_clean"] == "True"
    # entitlement matrix shows no provider was probed (every keyed provider MISSING_KEY, none VERIFIED).
    matrix = _read_csv(out / MOD._ARTIFACTS["entitlement_matrix"])
    assert all(r["entitlement"] != MOD.ENT_VERIFIED for r in matrix)


def test_raw_present_zero_normalized_run_is_blocker(env):
    # all cached raw records are future-dated or undated -> 0 normalized rows -> the run must classify
    # PARSER_OR_NORMALIZATION_BLOCKER, never READY_FOR_NEXT_ORTHOGONAL_BATCH.
    tmp, panel, sector, rates, data = env
    raw_dir = data / "eodhd" / "raw" / "news_social_sentiment"
    raw_dir.mkdir(parents=True, exist_ok=True)
    for tk in ALL_TICKERS:
        (raw_dir / ("%s.json" % tk)).write_text(json.dumps({"%s.US" % tk: [
            {"date": "2099-01-01", "count": 1, "normalized": 0.9},   # future -> dropped
            {"count": 2, "normalized": 0.5}]}), encoding="utf-8")    # undated -> dropped
    report, _ = _run(tmp, panel, sector, rates, data, "out_blocker",
                     transport=_raise_if_called, normalize_existing_only=True,
                     family="news_social_sentiment", max_tickers=0, max_requests=0,
                     min_tickers=LOW_TICKERS, min_events=LOW_EVENTS, min_coverage=LOW_COVERAGE)
    assert report["raw_payloads_present"] > 0
    assert report["normalized_rows"] == 0
    assert report["decision"] == MOD.DEC_PARSER_BLOCKER
    assert report["another_batch_needed"] is False
    assert report["parser_bug_fixed"] is False
