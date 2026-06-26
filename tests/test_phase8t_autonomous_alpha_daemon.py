"""Fully-offline tests for the Phase 8-T autonomous alpha-factory daemon.

No real key, no network, no writes outside a tmp dir. The whole refresh -> diagnose -> panel ->
extended-feature -> multi-cycle scenario campaign -> promotion -> preview-spec pipeline is driven by
an injected ``transport`` returning canned EODHD-shaped payloads plus tiny self-contained price /
macro / sector fixtures. The price fixture is engineered so that forward excess returns increase with
the earnings surprise, guaranteeing at least one genuine promotion (so both the promoted and rejected
registries are exercised) while leaving the fundamentals / regime / challenge families to be rejected.

They assert the brief's acceptance criteria:
  * the Phase 8-S promoted signals are read;
  * the 299-vs-301 scoreable gap is diagnosed and an unscoreable-tickers report is produced;
  * the acquisition universe target is broad (defaults 500 / 5000 / 8 cycles), never a 20-ticker toy;
  * the daemon runs multiple autonomous cycles;
  * the scenario candidate registry spans many feature families (not just 10 scenarios);
  * promoted AND rejected signals are both produced, with Paper-Trader preview specs for promoted;
  * raw/normalized paid data stay under the gitignored research/data/eodhd tree (never committed);
  * API keys are never printed or written;
  * no Paper Trader / GCP / order / automation logic is touched; no full regression is invoked.
"""
from __future__ import annotations

import csv
import datetime as dt
import importlib
import json

MOD = importlib.import_module("research.run_phase8t_autonomous_alpha_daemon")

N_TICKERS = 16
TICKERS = ["TK%s" % chr(65 + i) for i in range(N_TICKERS)]      # TKA..TKP
ETF = "ETF1"                                                     # priced but earnings-less (unscoreable)
START = dt.date(2019, 1, 1)
END = dt.date(2023, 6, 30)

_QENDS = [dt.date(y, m, d) for y in (2019, 2020, 2021, 2022)
          for (m, d) in ((3, 31), (6, 30), (9, 30), (12, 31))]


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


def _surprise_rank(i):
    """A per-ticker monotone surprise driver in [-2, 2]."""
    return (i % 5) - 2


def _write_price_cache(path):
    """16 scoreable tickers + 1 earnings-less ETF. Each ticker's drift increases with its surprise
    rank so that higher-surprise names earn higher forward excess returns (a real signal to find)."""
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
                drift = 0.05 + 0.05 * _surprise_rank(i)        # higher surprise -> steeper rise
                close = 100.0 + i * 1.0 + drift * d_idx
                w.writerow([ds, tk, close, close, close, close, 1000, 1000 * close, bench, 0.0])
            # the ETF tracks the benchmark, has price but never any earnings
            w.writerow([ds, ETF, bench / 10, bench / 10, bench / 10, bench / 10, 5000,
                        5000 * bench / 10, bench, 0.0])


def _write_sector_map(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    sectors = ["Information Technology", "Health Care", "Financials", "Industrials"]
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["ticker", "sector", "industry", "source", "as_of_date", "point_in_time", "notes"])
        for i, tk in enumerate(TICKERS):
            w.writerow([tk, sectors[i % len(sectors)], "Industry%d" % i, "test", "2026-01-01",
                        "False", ""])
        w.writerow([ETF, "Index", "ETF", "test", "2026-01-01", "False", ""])


def _write_macro(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["observation_date", "DGS10", "DGS2"])
        cur, k = dt.date(2018, 12, 1), 0
        while cur <= dt.date(2023, 7, 1):
            ten = 1.5 + 0.5 * ((k % 12) / 12.0)
            two = 0.5 + 0.7 * ((k % 12) / 12.0)
            w.writerow([cur.isoformat(), round(ten, 2), round(two, 2)])
            nm = cur.month + 1
            ny = cur.year + (1 if nm > 12 else 0)
            cur = dt.date(ny, ((nm - 1) % 12) + 1, 1)
            k += 1


def _write_phase8r(path):
    path.mkdir(parents=True, exist_ok=True)
    with open(path / "phase8r_broad_bundle_evaluation.json", "w", encoding="utf-8") as fh:
        json.dump({"decision": "EODHD_ACCEPT_AS_CORE_PROVIDER"}, fh)


def _write_phase8s(path):
    path.mkdir(parents=True, exist_ok=True)
    with open(path / "phase8s_autonomous_eodhd_alpha_factory.json", "w", encoding="utf-8") as fh:
        json.dump({"decision": "ALPHA_FOUND_READY_FOR_PAPER_TRADER_REVIEW",
                   "promoted_signals": ["surprise_sector_neutral", "sue_standardized"]}, fh)


def _write_phase8n(path):
    path.mkdir(parents=True, exist_ok=True)
    rows = [["ticker", "endpoint_family", "critical", "has_data", "rows", "first_date", "last_date"],
            ["TKA", "earnings_surprises", "True", "no", "0", "", ""],
            ["TKB", "earnings_surprises", "True", "yes", "40", "2016-01-01", "2026-01-01"]]
    with open(path / MOD.r8._FMP_COVERAGE_CSV, "w", newline="", encoding="utf-8") as fh:
        csv.writer(fh).writerows(rows)


def _fundamentals_payload(ticker):
    i = TICKERS.index(ticker) if ticker in TICKERS else 0
    history, inc, bal, cfs = {}, {}, {}, {}
    for q, qend in enumerate(_QENDS):
        fiscal = qend.isoformat()
        actual = 1.0 + 0.1 * q + 0.01 * i
        surprise = 0.05 * _surprise_rank(i) + 0.002 * ((q % 3) - 1)
        estimate = actual - surprise
        history[fiscal] = {"date": fiscal, "reportDate": _report_date(qend).isoformat(),
                           "epsActual": round(actual, 4), "epsEstimate": round(estimate, 4),
                           "epsDifference": round(surprise, 4),
                           "surprisePercent": round(surprise / abs(estimate) * 100.0, 4) if estimate else 0.0}
        fd = _filing_date(qend).isoformat()
        inc[fiscal] = {"date": fiscal, "filing_date": fd, "totalRevenue": str(1000 + 10 * q + i),
                       "netIncome": str(100 + q + 0.1 * i), "grossProfit": str(400 + q + i),
                       "operatingIncome": str(200 + q)}
        bal[fiscal] = {"date": fiscal, "filing_date": fd, "totalAssets": str(5000 + i),
                       "totalLiab": str(2000 + i), "shortLongTermDebtTotal": str(800 + i),
                       "cashAndShortTermInvestments": str(300 + q)}
        cfs[fiscal] = {"date": fiscal, "filing_date": fd, "freeCashFlow": str(50 + q)}
    return {"General": {"Code": ticker, "Sector": "Technology"},
            "Earnings": {"History": history},
            "Financials": {"Income_Statement": {"quarterly": inc},
                           "Balance_Sheet": {"quarterly": bal},
                           "Cash_Flow": {"quarterly": cfs}},
            "AnalystRatings": {"Rating": 4.0}}


def _empty_payload(ticker):
    return {"General": {"Code": ticker}, "Earnings": {"History": {}}, "Financials": {}}


def _transport(ticker, endpoint):
    if endpoint == MOD.r8.EP_FUNDAMENTALS:
        return _empty_payload(ticker) if ticker == ETF else _fundamentals_payload(ticker)
    return {"earnings": []}


def _fixtures(tmp_path):
    price = tmp_path / "prices.csv"
    sector = tmp_path / "sector.csv"
    macro_rates = tmp_path / "rates.csv"
    _write_price_cache(price)
    _write_sector_map(sector)
    _write_macro(macro_rates)
    _write_phase8r(tmp_path / "phase8r")
    _write_phase8s(tmp_path / "phase8s")
    _write_phase8n(tmp_path / "phase8n")
    return price, sector, macro_rates


def _run(tmp_path, transport=_transport, **kw):
    price, sector, macro_rates = _fixtures(tmp_path)
    out = tmp_path / "out"
    data = tmp_path / "data"
    report = MOD.run(transport=transport, out_dir=out, data_dir=data,
                     phase8r_dir=tmp_path / "phase8r", phase8s_dir=tmp_path / "phase8s",
                     phase8n_dir=tmp_path / "phase8n", price_csv=price, sector_csv=sector,
                     macro={"rates": macro_rates}, verbose=False, **kw)
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
    assert MOD.DEFAULT_MAX_CYCLES == 8


def test_end_to_end_produces_all_artifacts(tmp_path):
    report, out, _ = _run(tmp_path)
    for key, name in MOD._ARTIFACTS.items():
        assert (out / name).is_file(), "missing artifact %s" % name
    assert report["decision"] in MOD.ALLOWED_DECISIONS
    assert report["decision_is_terminal"] is True
    assert report["point_in_time"]["usable_events"] > 0


def test_reads_phase8s_promoted(tmp_path):
    report, _, _ = _run(tmp_path)
    assert report["builds_on_phase8s_decision"] == "ALPHA_FOUND_READY_FOR_PAPER_TRADER_REVIEW"
    assert set(report["phase8s_promoted"]) == {"surprise_sector_neutral", "sue_standardized"}


def test_unscoreable_gap_is_diagnosed(tmp_path):
    report, out, _ = _run(tmp_path)
    rows = _read_csv(out / MOD._ARTIFACTS["unscoreable"])
    tickers = {r["ticker"]: r for r in rows}
    # the earnings-less ETF is priced but unscoreable, with an exact reason
    assert ETF in tickers
    assert tickers[ETF]["has_earnings"] == "False"
    assert "no EODHD earnings" in tickers[ETF]["reason"]
    assert report["universe"]["unscoreable"] >= 1
    assert report["universe"]["scoreable_tickers"] == N_TICKERS


def test_universe_target_broader_than_toy(tmp_path):
    report, out, _ = _run(tmp_path)
    # the universe is the whole priced cache (not a 20-ticker toy), and the limits are broad
    assert report["universe"]["priced_tickers"] == N_TICKERS + 1
    assert report["bounded_limits"]["max_tickers"] == 500
    exp = _read_csv(out / MOD._ARTIFACTS["universe_expansion"])
    assert any(r["kind"] == "decision" for r in exp)        # an explicit expansion plan row exists


def test_runs_multiple_autonomous_cycles(tmp_path):
    report, out, _ = _run(tmp_path)
    assert report["cycles_run"] >= 2
    cyc = _read_csv(out / MOD._ARTIFACTS["cycle_summary"])
    assert len(cyc) >= 2
    # cumulative scenario count is monotone increasing across cycles
    cum = [int(r["scenarios_tested_cumulative"]) for r in cyc]
    assert cum == sorted(cum) and cum[-1] == report["scenarios_tested"]


def test_candidate_registry_spans_many_families(tmp_path):
    report, out, _ = _run(tmp_path)
    reg = _read_csv(out / MOD._ARTIFACTS["candidate_registry"])
    assert len(reg) > 10                                    # far more than the 10 of Phase 8-S
    families = {r["family"] for r in reg}
    assert {"earnings", "fundamentals", "price", "interaction"}.issubset(families)
    assert report["scenario_candidates_generated"] == len(reg)


def test_promoted_and_rejected_both_produced_with_preview_specs(tmp_path):
    report, out, _ = _run(tmp_path)
    promoted = _read_csv(out / MOD._ARTIFACTS["promoted"])
    rejected = _read_csv(out / MOD._ARTIFACTS["rejected"])
    specs = _read_csv(out / MOD._ARTIFACTS["preview_specs"])
    assert len(promoted) >= 1                               # the engineered surprise signal promotes
    assert len(rejected) >= 1                               # challenge/uncorrelated families reject
    assert len(specs) == len(promoted)                      # one preview spec per promoted signal
    assert report["decision"] in (MOD.DEC_ALPHA, MOD.DEC_MORE_ALPHA)


def test_preview_specs_are_preview_only(tmp_path):
    _, out, _ = _run(tmp_path)
    specs = _read_csv(out / MOD._ARTIFACTS["preview_specs"])
    for s in specs:
        assert s["safety_preview"] == "PREVIEW_ONLY"
        assert s["safety_orders"] == "NO_ORDERS"
        assert s["safety_automation"] == "AUTOMATION_OFF"


def test_extended_validation_columns_present(tmp_path):
    _, out, _ = _run(tmp_path)
    sub = _read_csv(out / MOD._ARTIFACTS["subperiod"])
    assert sub and "first_half_ic" in sub[0] and "second_half_ic" in sub[0]
    pla = _read_csv(out / MOD._ARTIFACTS["placebo"])
    assert pla and {"real_ic", "shuffled_return_ic", "random_signal_ic"}.issubset(pla[0].keys())
    tc = _read_csv(out / MOD._ARTIFACTS["tcost"])
    assert tc and {"net_10bps", "net_25bps", "net_50bps"}.issubset(tc[0].keys())


def test_max_cycles_truncates_to_next_cycle_decision(tmp_path):
    report, out, _ = _run(tmp_path, max_cycles=2)
    assert report["cycles_run"] == 2
    assert report["decision"] == MOD.DEC_NEXT_CYCLE
    assert report["decision_is_terminal"] is False


def test_raw_and_normalized_data_gitignored_not_committed(tmp_path):
    _, out, data = _run(tmp_path)
    gi = data / "eodhd" / ".gitignore"
    assert gi.is_file()
    body = gi.read_text(encoding="utf-8")
    assert "raw/" in body and "normalized/" in body
    assert (data / "eodhd" / "raw" / "fundamentals").is_dir()
    assert (data / "eodhd" / "normalized" / "earnings.csv").is_file()
    # no committed artifact is a raw provider payload
    for p in out.glob("*.json"):
        assert "Income_Statement" not in p.read_text(encoding="utf-8")


def test_secret_safety_and_no_orders(tmp_path):
    report, out, _ = _run(tmp_path)
    audit = {r["check"]: r["result"] for r in _read_csv(out / MOD._ARTIFACTS["secret_audit"])}
    assert audit["secret_leak_scan_clean"] == "True"
    assert audit["api_key_written_to_disk"] == "False"
    for flag in ("orders_enabled", "automation_enabled", "broker_execution_enabled",
                 "paper_trader_touched", "gcp_touched", "deployed", "committed", "data_fabricated"):
        assert report[flag] is False
    assert report["preview_only"] is True
    assert report["network_used"] is False                 # injected transport, no real network


def test_offline_no_transport_no_cache_is_blocker(tmp_path):
    report, _, _ = _run(tmp_path, transport=None)
    assert report["acquisition"]["executed"] is False
    assert report["decision"] == MOD.DEC_BLOCKER            # empty cache, no acquisition -> blocker
