"""Fully-offline tests for Phase 8-S autonomous EODHD alpha factory.

No real key, no network, no writes outside a tmp dir. The whole acquisition -> panel -> join ->
feature -> scenario-scoring -> decision pipeline is driven by an injected ``transport`` returning
canned EODHD-shaped fundamentals payloads, plus tiny self-contained price / macro / sector fixtures.
They assert the brief's acceptance criteria:

  * the Phase 8-R EODHD_ACCEPT decision is read;
  * the acquisition universe is broad (not a 20-ticker toy) and defaults are 500 / 5000;
  * skip-existing avoids re-requesting cached tickers;
  * raw/normalized paid data stay under the gitignored research/data/eodhd tree (never committed);
  * the point-in-time audit, price/macro/sector join report, scenario scoreboard, multiple-testing
    report, and promoted/rejected signal files are all produced;
  * the terminal decision is one of the allowed values;
  * API keys are never printed or written;
  * no Paper Trader / GCP / order / automation logic is touched.
"""
from __future__ import annotations

import csv
import datetime as dt
import importlib
import json

MOD = importlib.import_module("research.run_phase8s_autonomous_eodhd_alpha_factory")

N_TICKERS = 16
TICKERS = ["TK%s" % chr(65 + i) for i in range(N_TICKERS)]      # TKA..TKP
START = dt.date(2020, 1, 1)
END = dt.date(2023, 6, 30)

# Quarter ends 2020Q1..2022Q4 and their announcement dates (~45 days later = PIT availability).
_QENDS = [dt.date(y, m, d) for y in (2020, 2021, 2022)
          for (m, d) in ((3, 31), (6, 30), (9, 30), (12, 31))]


def _business_days(a, b):
    days = []
    cur = a
    while cur <= b:
        if cur.weekday() < 5:
            days.append(cur)
        cur += dt.timedelta(days=1)
    return days


def _report_date(qend):
    return qend + dt.timedelta(days=45)


def _filing_date(qend):
    return qend + dt.timedelta(days=40)


def _write_price_cache(path):
    """16 tickers, daily business-day closes + a common benchmark; deterministic, all positive."""
    days = _business_days(START, END)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["date", "ticker", "adjusted_open", "adjusted_high", "adjusted_low",
                    "adjusted_close", "volume", "dollar_volume", "benchmark_close", "daily_return"])
        for d_idx, day in enumerate(days):
            bench = 1000.0 + 0.4 * d_idx
            ds = day.isoformat()
            for i, tk in enumerate(TICKERS):
                close = 100.0 + i * 2.0 + 0.05 * d_idx + (d_idx % 7) * 0.01 * (1 + i % 3)
                w.writerow([ds, tk, close, close, close, close, 1000, 1000 * close, bench, 0.0])


def _write_sector_map(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    sectors = ["Information Technology", "Health Care", "Financials", "Industrials"]
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["ticker", "sector", "industry", "source", "as_of_date", "point_in_time", "notes"])
        for i, tk in enumerate(TICKERS):
            w.writerow([tk, sectors[i % len(sectors)], "Industry%d" % i, "test", "2026-01-01",
                        "False", ""])


def _write_macro(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["observation_date", "DGS10", "DGS2"])
        cur = dt.date(2019, 12, 1)
        k = 0
        while cur <= dt.date(2023, 7, 1):
            ten = 1.5 + 0.5 * ((k % 12) / 12.0)
            two = 0.5 + 0.7 * ((k % 12) / 12.0)        # sometimes inverts -> regime variety
            w.writerow([cur.isoformat(), round(ten, 2), round(two, 2)])
            # step ~1 month
            nm = cur.month + 1
            ny = cur.year + (1 if nm > 12 else 0)
            cur = dt.date(ny, ((nm - 1) % 12) + 1, 1)
            k += 1


def _write_phase8r(path):
    path.mkdir(parents=True, exist_ok=True)
    with open(path / "phase8r_broad_bundle_evaluation.json", "w", encoding="utf-8") as fh:
        json.dump({"decision": "EODHD_ACCEPT_AS_CORE_PROVIDER",
                   "earnings_point_in_time_safe": True}, fh)


def _write_phase8n(path):
    path.mkdir(parents=True, exist_ok=True)
    rows = [["ticker", "endpoint_family", "critical", "has_data", "rows", "first_date", "last_date"]]
    # mark a couple of our tickers FMP-blocked so the priority ordering is exercised
    rows.append(["TKA", "earnings_surprises", "True", "no", "0", "", ""])
    rows.append(["TKB", "earnings_surprises", "True", "yes", "40", "2016-01-01", "2026-01-01"])
    with open(path / MOD.r8._FMP_COVERAGE_CSV, "w", newline="", encoding="utf-8") as fh:
        csv.writer(fh).writerows(rows)


def _fundamentals_payload(ticker):
    """An EODHD-shaped fundamentals payload with deep, point-in-time earnings + financials."""
    i = TICKERS.index(ticker) if ticker in TICKERS else 0
    history = {}
    inc, bal, cfs = {}, {}, {}
    for q, qend in enumerate(_QENDS):
        fiscal = qend.isoformat()
        actual = 1.0 + 0.1 * q + 0.01 * i
        surprise = 0.02 * ((i % 5) - 2) + 0.005 * ((q % 3) - 1)
        estimate = actual - surprise
        history[fiscal] = {
            "date": fiscal, "reportDate": _report_date(qend).isoformat(),
            "epsActual": round(actual, 4), "epsEstimate": round(estimate, 4),
            "epsDifference": round(surprise, 4),
            "surprisePercent": round(surprise / abs(estimate) * 100.0, 4) if estimate else 0.0,
        }
        fd = _filing_date(qend).isoformat()
        inc[fiscal] = {"date": fiscal, "filing_date": fd,
                       "totalRevenue": str(1000 + 10 * q + i), "netIncome": str(100 + q + 0.1 * i),
                       "grossProfit": str(400 + q), "operatingIncome": str(200 + q)}
        bal[fiscal] = {"date": fiscal, "filing_date": fd, "totalAssets": str(5000 + i),
                       "totalLiab": str(2000 + i), "shortLongTermDebtTotal": str(800 + i),
                       "cashAndShortTermInvestments": str(300 + q)}
        cfs[fiscal] = {"date": fiscal, "filing_date": fd, "freeCashFlow": str(50 + q)}
    return {
        "General": {"Code": ticker, "Sector": "Technology"},
        "Earnings": {"History": history,
                     "Trend": {"2026-03-31": {"epsEstimateAvg": "1.10"}}},
        "Financials": {"Income_Statement": {"quarterly": inc},
                       "Balance_Sheet": {"quarterly": bal},
                       "Cash_Flow": {"quarterly": cfs}},
        "AnalystRatings": {"Rating": 4.0, "TargetPrice": 150.0},
    }


def _transport(ticker, endpoint):
    if endpoint == MOD.r8.EP_FUNDAMENTALS:
        return _fundamentals_payload(ticker)
    return {"earnings": []}   # calendar empty on this plan


def _fixtures(tmp_path):
    price = tmp_path / "prices.csv"
    sector = tmp_path / "sector.csv"
    macro_rates = tmp_path / "rates.csv"
    _write_price_cache(price)
    _write_sector_map(sector)
    _write_macro(macro_rates)
    _write_phase8r(tmp_path / "phase8r")
    _write_phase8n(tmp_path / "phase8n")
    return price, sector, macro_rates


def _run(tmp_path, transport=_transport, seed_cached=None, **kw):
    price, sector, macro_rates = _fixtures(tmp_path)
    out = tmp_path / "out"
    data = tmp_path / "data"
    if seed_cached:
        # pre-seed the normalized cache so skip-existing has something to skip
        ncsv = data / "eodhd" / "normalized" / "earnings.csv"
        ncsv.parent.mkdir(parents=True, exist_ok=True)
        with open(ncsv, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["symbol", "fiscal_date", "report_date", "eps_actual", "eps_estimate",
                        "eps_difference", "surprise_percent"])
            for q, qend in enumerate(_QENDS):
                w.writerow([seed_cached, qend.isoformat(), _report_date(qend).isoformat(),
                            1.0 + 0.1 * q, 1.0 + 0.1 * q, 0.0, 0.0])
    report = MOD.run(transport=transport, out_dir=out, data_dir=data,
                     phase8r_dir=tmp_path / "phase8r", phase8n_dir=tmp_path / "phase8n",
                     price_csv=price, sector_csv=sector, macro={"rates": macro_rates},
                     verbose=False, **kw)
    return report, out, data


def _read_csv(path):
    with open(path, "r", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


# --------------------------------------------------------------------------- #
# Tests.
# --------------------------------------------------------------------------- #
def test_defaults_are_broad():
    assert MOD.DEFAULT_MAX_TICKERS == 500
    assert MOD.DEFAULT_MAX_REQUESTS == 5000


def test_end_to_end_pipeline_produces_all_artifacts(tmp_path):
    report, out, data = _run(tmp_path)
    # every declared artifact exists
    for key, name in MOD._ARTIFACTS.items():
        assert (out / name).is_file(), "missing artifact %s" % name
    # phase 8-R accepted decision was read
    assert report["phase8r_decision"] == "EODHD_ACCEPT_AS_CORE_PROVIDER"
    assert report["eodhd_accepted_as_core_provider"] is True
    # terminal decision is allowed
    assert report["decision"] in MOD.ALLOWED_DECISIONS
    assert report["decision_is_terminal"] is True
    # the pipeline actually scored events
    assert report["point_in_time"]["usable_events"] > 0
    assert report["scenarios_tested"] == len(MOD.scenario_plan())
    # scoreboard + multiple-testing have a row per scenario
    sb = _read_csv(out / MOD._ARTIFACTS["scoreboard"])
    assert len(sb) == len(MOD.scenario_plan())
    mt = _read_csv(out / MOD._ARTIFACTS["multiple_testing"])
    assert len(mt) == len(MOD.scenario_plan())


def test_acquisition_universe_is_broad_not_toy(tmp_path):
    report, out, _ = _run(tmp_path)
    uni = _read_csv(out / MOD._ARTIFACTS["universe"])
    assert len(uni) == N_TICKERS         # the whole priced universe, not a 20-cap toy
    assert report["universe_targeted_for_acquisition"] >= N_TICKERS - 1
    # FMP-blocked ticker is prioritized first
    assert uni[0]["priority"] == "1_fmp_blocked"
    assert uni[0]["ticker"] == "TKA"


def test_skip_existing_does_not_reacquire(tmp_path):
    report, out, data = _run(tmp_path, seed_cached="TKA")
    prog = _read_csv(out / MOD._ARTIFACTS["acq_progress"])
    acquired_tickers = {r["ticker"] for r in prog}
    assert "TKA" not in acquired_tickers          # already cached -> skipped
    uni = {r["ticker"]: r for r in _read_csv(out / MOD._ARTIFACTS["universe"])}
    assert uni["TKA"]["cached_before"] == "True"
    assert uni["TKA"]["targeted_for_acquisition"] == "False"
    # requests only for the non-cached tickers
    assert report["acquisition"]["requests_made"] == N_TICKERS - 1


def test_point_in_time_audit_has_no_lookahead_checks(tmp_path):
    _, out, _ = _run(tmp_path)
    audit = {r["check"]: r["result"] for r in _read_csv(out / MOD._ARTIFACTS["pit_audit"])}
    assert audit.get("entry_after_announcement") == "True"
    assert audit.get("require_forward_prices") == "True"
    assert audit.get("require_realized_eps_actual") == "True"
    assert "drop_future_announcements" in audit


def test_raw_and_normalized_data_gitignored_not_committed(tmp_path):
    _, out, data = _run(tmp_path)
    gi = data / "eodhd" / ".gitignore"
    assert gi.is_file()
    body = gi.read_text(encoding="utf-8")
    assert "raw/" in body and "normalized/" in body
    # raw payloads + normalized csv live under the data tree, NOT under the committed out dir
    assert (data / "eodhd" / "raw" / "fundamentals").is_dir()
    assert (data / "eodhd" / "normalized" / "earnings.csv").is_file()
    assert not (out / "raw").exists()
    assert not list(out.glob("**/*.json")) == []  # out has json *artifacts* only
    # no committed artifact is a raw provider payload
    for p in out.glob("*.json"):
        assert "Income_Statement" not in p.read_text(encoding="utf-8")


def test_secret_safety_and_no_orders(tmp_path):
    report, out, _ = _run(tmp_path)
    audit = {r["check"]: r["result"] for r in _read_csv(out / MOD._ARTIFACTS["secret_audit"])}
    assert audit["secret_leak_scan_clean"] == "True"
    assert audit["api_key_written_to_disk"] == "False"
    # safety / provenance contract
    for flag in ("orders_enabled", "automation_enabled", "broker_execution_enabled",
                 "paper_trader_touched", "gcp_touched", "deployed", "committed",
                 "data_fabricated"):
        assert report[flag] is False
    assert report["preview_only"] is True
    assert report["network_used"] is False        # transport injected, no real network


def test_offline_no_transport_still_scores_cached(tmp_path):
    # No transport and no key: acquisition is skipped but cached data (if any) is still scored.
    report, out, _ = _run(tmp_path, transport=None)
    assert report["acquisition"]["executed"] is False
    assert report["decision"] in MOD.ALLOWED_DECISIONS
    # with an empty cache and no acquisition there are no usable events -> hard blocker
    assert report["decision"] == MOD.DEC_BLOCKER


def test_join_and_feature_reports_present(tmp_path):
    _, out, _ = _run(tmp_path)
    jr = {r["metric"]: r["value"] for r in _read_csv(out / MOD._ARTIFACTS["join_report"])}
    assert int(jr["events_usable"]) > 0
    fc = _read_csv(out / MOD._ARTIFACTS["feature_catalog"])
    fams = {r["family"] for r in fc}
    assert {"earnings", "fundamentals", "regime"}.issubset(fams)
    promoted = out / MOD._ARTIFACTS["promoted"]
    rejected = out / MOD._ARTIFACTS["rejected"]
    assert promoted.is_file() and rejected.is_file()
