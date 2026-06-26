"""Fully-offline tests for the Phase 8-V combined EODHD price+fundamentals universe expansion.

No real key, no network, no writes outside a tmp dir. The base cache deliberately covers only the
first N_BASE names with BOTH local prices AND seeded EODHD earnings/fundamentals; the remaining
names are 'missing combined' - they lack a price AND an earnings event (exactly the Phase 8-U
finding). An injected combined ``transport(symbol, endpoint)`` returns canned EODHD-shaped EOD bars
for the price leg and a fundamentals payload for the fundamentals leg, so a MATCHED acquisition of
both legs makes each missing name scoreable. The base prices and acquired bars share a
surprise-correlated drift, so the engineered surprise signal promotes both before and after,
exercising the EXPANDED_UNIVERSE_ALPHA_CONFIRMED path and the full before/after comparison.

They assert the brief's acceptance criteria:
  * Phase 8-T (and 8-U) outputs are read;
  * the S&P-500 list is parsed and the missing-combined tickers are recognized as also missing
    earnings/fundamentals;
  * a matched price+fundamentals acquisition list is produced; skip-existing works;
  * EODHD raw/normalized price AND fundamentals payloads stay under the gitignored data tree;
  * expanded scoreable coverage + before/after comparison are produced;
  * the Phase 8-T promoted signals are re-evaluated on the expanded universe;
  * API keys are never printed or written; no Paper Trader / GCP / order / deploy / full-regression
    logic is invoked (targeted module only).
"""
from __future__ import annotations

import csv
import datetime as dt
import importlib
import json
from pathlib import Path

import pytest

MOD = importlib.import_module("research.run_phase8v_combined_eodhd_universe_expansion")
r8 = MOD.r8

N_TICKERS = 20
TICKERS = ["TK%s" % chr(65 + i) for i in range(N_TICKERS)]       # TKA..TKT
N_BASE = 12                                                      # base = price + earnings (TKA..TKL)
BASE_TICKERS = TICKERS[:N_BASE]
MISSING_TICKERS = TICKERS[N_BASE:]                               # TKM..TKT (missing BOTH legs)
ETF = "ETF1"
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
    return (i % 5) - 2


def _adj_close(i, d_idx):
    drift = 0.05 + 0.05 * _surprise_rank(i)
    return 100.0 + i * 1.0 + drift * d_idx


def _write_base_price_cache(path):
    """Only the first N_BASE tickers (+ an earnings-less ETF) have local prices; the rest are
    'missing' and acquired via the injected combined transport."""
    days = _business_days(START, END)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["date", "ticker", "adjusted_open", "adjusted_high", "adjusted_low",
                    "adjusted_close", "volume", "dollar_volume", "benchmark_close", "daily_return"])
        for d_idx, day in enumerate(days):
            bench = 1000.0 + 0.4 * d_idx
            ds = day.isoformat()
            for i, tk in enumerate(BASE_TICKERS):
                c = _adj_close(i, d_idx)
                w.writerow([ds, tk, c, c, c, c, 1000, 1000 * c, bench, 0.0])
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


def _write_sp500_html(path):
    """Minimal Wikipedia-shaped constituents table listing ALL N_TICKERS (so the missing ones are
    discoverable as expansion targets); a separate changes table with ZZZZ that must be excluded."""
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = "".join(
        '<tr><td><a href="q">%s</a></td><td><a href="c">Co %s</a></td></tr>' % (tk, tk)
        for tk in TICKERS)
    html = ('<html><body><table id="constituents"><tbody>%s</tbody></table>'
            '<table id="changes"><tbody>'
            '<tr><td><a href="q">ZZZZ</a></td></tr></tbody></table></body></html>' % rows)
    path.write_text(html, encoding="utf-8")


def _write_phase8t(path):
    path.mkdir(parents=True, exist_ok=True)
    payload = {"decision": "MORE_ALPHA_FOUND_READY_FOR_PAPER_TRADER_REVIEW",
               "promoted_signals": ["surprise_sector_neutral", "surprise_x_quality",
                                    "positive_surprise_asymmetry", "surprise_magnitude"]}
    for name in ("phase8t_autonomous_alpha_daemon.json", "final_research_decision.json"):
        with open(path / name, "w", encoding="utf-8") as fh:
            json.dump(payload, fh)


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
                           "Cash_Flow": {"quarterly": cfs}}}


def _seed_base_cache(data_dir):
    """Pre-seed the EODHD earnings + fundamentals cache for ONLY the base priced names (as Phase
    8-S/8-T would have). The missing names are intentionally absent from BOTH price and earnings -
    the precise Phase 8-U finding this phase acts on."""
    rp = r8._Paths(data_dir=data_dir)
    normalized = {}
    for tk in BASE_TICKERS:
        payload = _fundamentals_payload(tk)
        r8._persist_raw(rp, tk, "fundamentals", payload)
        normalized[tk] = r8._normalize_earnings(tk, payload)
    r8._append_normalized(rp, normalized)


def _eod_payload(ticker):
    i = TICKERS.index(ticker)
    bars = []
    for d_idx, day in enumerate(_business_days(START, END)):
        c = _adj_close(i, d_idx)
        bars.append({"date": day.isoformat(), "open": c, "high": c, "low": c, "close": c,
                     "adjusted_close": c, "volume": 1000})
    return bars


def _combined_transport(symbol, endpoint):
    """Matched price+fundamentals transport: serves both legs for the missing names only."""
    if symbol not in MISSING_TICKERS:
        return [] if endpoint == MOD.EP_EOD_PRICES else {}
    if endpoint == MOD.EP_EOD_PRICES:
        return _eod_payload(symbol)
    return _fundamentals_payload(symbol)


def _fixtures(tmp_path):
    price = tmp_path / "base_prices.csv"
    sector = tmp_path / "sector.csv"
    rates = tmp_path / "rates.csv"
    sp = tmp_path / "sp500.html"
    _write_base_price_cache(price)
    _write_sector_map(sector)
    _write_macro(rates)
    _write_sp500_html(sp)
    _write_phase8t(tmp_path / "phase8t")
    data = tmp_path / "data"
    _seed_base_cache(data)
    return price, sector, rates, sp, data


def _run(tmp_path, transport=_combined_transport, **kw):
    price, sector, rates, sp, data = _fixtures(tmp_path)
    out = tmp_path / "out"
    report = MOD.run(transport=transport, out_dir=out, data_dir=data,
                     phase8t_dir=tmp_path / "phase8t", price_csv=price, sector_csv=sector,
                     macro={"rates": rates}, sp500_html=sp, verbose=False, **kw)
    return report, out, data


def _read_csv(path):
    with open(path, "r", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


# A module-scoped genuine-expansion run (before+after campaigns are heavy; share one run).
@pytest.fixture(scope="module")
def expansion(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("phase8v")
    return _run(tmp)


# --------------------------------------------------------------------------- #
# Tests.
# --------------------------------------------------------------------------- #
def test_defaults_are_bounded():
    assert MOD.DEFAULT_MAX_TICKERS == 250
    assert MOD.DEFAULT_MAX_REQUESTS == 1000               # two legs per ticker -> double 8-U's 500
    assert MOD.DEFAULT_START_DATE == "2016-01-01"


def test_end_to_end_produces_all_required_artifacts(expansion):
    report, out, _ = expansion
    for key in MOD._REQUIRED_ARTIFACTS:
        name = MOD._ARTIFACTS[key]
        assert (out / name).is_file(), "missing required artifact %s" % name
    assert len(MOD._REQUIRED_ARTIFACTS) == 18
    assert report["decision"] in MOD.ALLOWED_DECISIONS
    assert report["decision_is_terminal"] is True


def test_reads_phase8t_and_phase8u_outputs(expansion):
    report, _, _ = expansion
    assert report["builds_on_phase8t_decision"] == "MORE_ALPHA_FOUND_READY_FOR_PAPER_TRADER_REVIEW"
    assert "surprise_sector_neutral" in report["phase8t_promoted"]
    assert "surprise_x_quality" in report["phase8t_promoted"]
    # the 8-U decision is read (may be empty in an isolated tmp env, but the key must be present)
    assert "builds_on_phase8u_decision" in report


def test_sp500_list_is_parsed(expansion):
    report, out, _ = expansion
    rows = _read_csv(out / MOD._ARTIFACTS["sp500_extraction"])
    syms = {r["ticker"] for r in rows}
    assert set(TICKERS).issubset(syms)
    assert "ZZZZ" not in syms                              # changelog table excluded
    assert report["universe"]["sp500_name_list"] == N_TICKERS


def test_missing_combined_tickers_lack_both_legs(expansion):
    report, out, _ = expansion
    rows = _read_csv(out / MOD._ARTIFACTS["missing_tickers"])
    missing = {r["ticker"] for r in rows}
    assert missing == set(MISSING_TICKERS)
    assert report["universe"]["missing_combined_tickers"] == len(MISSING_TICKERS)
    # the precise 8-U finding: every missing name needs BOTH price and fundamentals
    assert all(r["need_price"] == "True" for r in rows)
    assert all(r["need_fundamentals"] == "True" for r in rows)
    assert all(r["has_price"] == "False" and r["has_earnings"] == "False" for r in rows)
    assert report["universe"]["missing_need_price"] == len(MISSING_TICKERS)
    assert report["universe"]["missing_need_fundamentals"] == len(MISSING_TICKERS)


def test_matched_acquisition_universe_produced(expansion):
    _, out, _ = expansion
    rows = _read_csv(out / MOD._ARTIFACTS["matched_universe"])
    assert {r["ticker"] for r in rows} == set(MISSING_TICKERS)
    assert all(r["need_price"] == "True" and r["need_fundamentals"] == "True" for r in rows)


def test_combined_acquired_and_gitignored(expansion):
    report, out, data = expansion
    assert report["acquisition"]["prices_acquired"] == len(MISSING_TICKERS)
    assert report["acquisition"]["fundamentals_acquired"] == len(MISSING_TICKERS)
    assert report["acquisition"]["matched_scoreable"] == len(MISSING_TICKERS)
    assert report["acquisition"]["requests_made"] == 2 * len(MISSING_TICKERS)
    gi = data / "eodhd" / ".gitignore"
    assert gi.is_file() and "*" in gi.read_text(encoding="utf-8")
    # both raw trees + both normalized trees exist under the gitignored data dir
    assert (data / "eodhd" / "raw" / "eod_prices" / ("%s.json" % MISSING_TICKERS[0])).is_file()
    assert (data / "eodhd" / "raw" / "fundamentals" / ("%s.json" % MISSING_TICKERS[0])).is_file()
    assert (data / "eodhd" / "normalized" / "eod_prices").is_dir()
    assert (data / "eodhd" / "normalized" / "earnings.csv").is_file()
    # no committed artifact leaks an api_token query param
    for p in out.glob("*"):
        if p.suffix == ".csv":
            assert "api_token" not in p.read_text(encoding="utf-8")[:400].lower()


def test_skip_existing_works(tmp_path):
    # acquisition-level skip-existing (fast; no scoring campaign)
    _, _, _, _, data = _fixtures(tmp_path)
    out = tmp_path / "out"
    P = MOD._Paths(out_dir=out, data_dir=data)
    log = MOD.t8._Log(False)
    missing = [{"ticker": tk} for tk in MISSING_TICKERS]
    a1 = MOD.acquire_combined(P, missing, live=False, transport=_combined_transport,
                              max_tickers=250, max_requests=1000, start_date="2016-01-01",
                              skip_existing=True, log=log)
    assert len(a1["price_acquired"]) == len(MISSING_TICKERS)
    assert len(a1["fund_acquired"]) == len(MISSING_TICKERS)
    assert a1["requests_made"] == 2 * len(MISSING_TICKERS)
    # second pass: everything is cached -> nothing requested
    a2 = MOD.acquire_combined(P, missing, live=False, transport=_combined_transport,
                              max_tickers=250, max_requests=1000, start_date="2016-01-01",
                              skip_existing=True, log=log)
    assert a2["price_acquired"] == [] and a2["fund_acquired"] == []
    assert a2["requests_made"] == 0


def test_expanded_scoreable_coverage_produced(expansion):
    _, out, _ = expansion
    rows = _read_csv(out / MOD._ARTIFACTS["expanded_scoreable"])
    newly = {r["ticker"] for r in rows if r["newly_scoreable"] == "True"}
    assert set(MISSING_TICKERS).issubset(newly)            # the acquired names became scoreable
    assert all(r["price_acquired_this_run"] == "True"
               for r in rows if r["ticker"] in MISSING_TICKERS)
    assert all(r["fundamentals_acquired_this_run"] == "True"
               for r in rows if r["ticker"] in MISSING_TICKERS)


def test_before_after_comparison_produced(expansion):
    report, out, _ = expansion
    cov = {r["metric"]: r for r in _read_csv(out / MOD._ARTIFACTS["ba_coverage"])}
    before = int(cov["scoreable_tickers"]["before"])
    after = int(cov["scoreable_tickers"]["after"])
    assert after > before                                  # the matched expansion genuinely widened it
    assert before == N_BASE and after == N_TICKERS
    ba = _read_csv(out / MOD._ARTIFACTS["ba_alpha"])
    # 25bps AND 50bps cost robustness both present before/after
    assert ba and {"net25_before", "net25_after", "net50_before", "net50_after",
                   "promoted_before", "promoted_after"}.issubset(ba[0])
    delta = _read_csv(out / MOD._ARTIFACTS["robustness_delta"])
    assert delta and {"promotion_status", "net50_after"}.issubset(delta[0])


def test_phase8t_signals_reevaluated_on_expanded_universe(expansion):
    report, out, _ = expansion
    ba = {r["scenario"]: r for r in _read_csv(out / MOD._ARTIFACTS["ba_alpha"])}
    assert "surprise_sector_neutral" in ba
    assert "surprise_x_quality" in ba
    assert report["surprise_sector_neutral_survives"] is True
    assert report["surprise_x_quality_survives"] is True
    assert report["decision"] == MOD.DEC_CONFIRMED


def test_expanded_promoted_and_rejected_produced(expansion):
    _, out, _ = expansion
    promoted = _read_csv(out / MOD._ARTIFACTS["promoted"])
    rejected = _read_csv(out / MOD._ARTIFACTS["rejected"])
    assert len(promoted) >= 1
    assert len(rejected) >= 1
    scoreboard = _read_csv(out / MOD._ARTIFACTS["scoreboard"])
    assert len(scoreboard) > 10                            # full 8-T battery, not a toy
    assert "net_spread_50bps" in scoreboard[0]


def test_secret_safety_and_no_orders(expansion):
    report, out, _ = expansion
    audit = {r["check"]: r["result"] for r in _read_csv(out / MOD._ARTIFACTS["secret_audit"])}
    assert audit["secret_leak_scan_clean"] == "True"
    assert audit["api_key_written_to_disk"] == "False"
    assert audit["raw_normalized_data_gitignored"] == "True"
    for flag in ("orders_enabled", "automation_enabled", "broker_execution_enabled",
                 "paper_trader_touched", "gcp_touched", "deployed", "committed", "data_fabricated"):
        assert report[flag] is False
    assert report["preview_only"] is True
    assert report["network_used"] is False                # injected transport, no real network


def test_next_plan_is_preview_only(expansion):
    _, out, _ = expansion
    plan = MOD._read_json(out / MOD._ARTIFACTS["next_plan"])
    assert plan["preview_only"] is True
    assert plan["orders_enabled"] is False
    assert plan["automation_enabled"] is False


def test_no_full_regression_or_forbidden_logic():
    # the runner must not shell out, run pytest, or carry order/deploy logic (preview-only research)
    src = Path(MOD.__file__).read_text(encoding="utf-8").lower()
    for bad in ("import subprocess", "pytest.main", "place_order", "submit_order",
                "broker.execute", "subprocess.run", "os.system"):
        assert bad not in src


def test_offline_no_transport_is_next_combined_batch(tmp_path):
    # no transport + no key + nothing cached to acquire -> universe cannot be expanded now
    report, out, _ = _run(tmp_path, transport=None)
    assert report["acquisition"]["executed"] is False
    assert report["acquisition"]["prices_acquired"] == 0
    assert report["acquisition"]["fundamentals_acquired"] == 0
    assert report["decision"] == MOD.DEC_NEXT_BATCH
    assert report["before"]["scoreable_tickers"] == report["after"]["scoreable_tickers"]
    man = {r["metric"]: r["value"] for r in _read_csv(out / MOD._ARTIFACTS["panel_manifest"])}
    assert man["is_expanded"] == "False"
