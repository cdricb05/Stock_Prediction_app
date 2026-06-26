"""Fully-offline tests for the Phase 8-U EODHD price-universe expansion + robustness re-test.

No real key, no network, no writes outside a tmp dir. An injected EOD ``transport`` returns canned
EODHD-shaped daily-bar payloads for the names missing from the (deliberately partial) base price
cache, while the EODHD earnings cache is pre-seeded for ALL names so that acquiring a missing name's
prices genuinely makes it scoreable. The base cache and the acquired bars share a surprise-correlated
drift, so the engineered surprise signal promotes both before and after - exercising the
EXPANDED_UNIVERSE_ALPHA_CONFIRMED path and the full before/after comparison machinery.

They assert the brief's acceptance criteria:
  * Phase 8-T outputs are read;
  * the S&P-500 list is parsed and the missing price tickers are identified;
  * EODHD EOD raw/normalized prices stay under the gitignored research/data/eodhd tree;
  * the expanded price panel manifest + before/after comparison are produced;
  * the Phase 8-T promoted signals are re-evaluated on the expanded universe;
  * API keys are never printed or written; no Paper Trader / GCP / order / deploy logic is touched;
  * no full regression is invoked (targeted module only).
"""
from __future__ import annotations

import csv
import datetime as dt
import importlib
import json

import pytest

MOD = importlib.import_module("research.run_phase8u_eodhd_price_universe_expansion")
r8 = MOD.r8

N_TICKERS = 20
TICKERS = ["TK%s" % chr(65 + i) for i in range(N_TICKERS)]       # TKA..TKT
N_BASE = 12                                                      # base price cache covers TKA..TKL
BASE_TICKERS = TICKERS[:N_BASE]
MISSING_TICKERS = TICKERS[N_BASE:]                               # TKM..TKT (priced via acquisition)
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
    """Only the first N_BASE tickers (+ an earnings-less ETF) have local prices. The rest are
    'missing' and acquired via the injected EOD transport."""
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
    discoverable as expansion targets)."""
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
    with open(path / "phase8t_autonomous_alpha_daemon.json", "w", encoding="utf-8") as fh:
        json.dump({"decision": "MORE_ALPHA_FOUND_READY_FOR_PAPER_TRADER_REVIEW",
                   "promoted_signals": ["surprise_sector_neutral", "surprise_x_quality",
                                        "positive_surprise_asymmetry", "surprise_magnitude"]}, fh)
    with open(path / "final_research_decision.json", "w", encoding="utf-8") as fh:
        json.dump({"decision": "MORE_ALPHA_FOUND_READY_FOR_PAPER_TRADER_REVIEW",
                   "promoted_signals": ["surprise_sector_neutral", "surprise_x_quality",
                                        "positive_surprise_asymmetry", "surprise_magnitude"]}, fh)


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


def _seed_earnings_cache(data_dir):
    """Pre-seed the EODHD earnings + fundamentals cache for ALL tickers (as Phase 8-S/8-T would),
    so a missing name becomes scoreable the moment its prices are acquired."""
    rp = r8._Paths(data_dir=data_dir)
    normalized = {}
    for tk in TICKERS:
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


def _eod_transport(symbol, start_date):
    if symbol in MISSING_TICKERS:
        return _eod_payload(symbol)
    return []                                                    # nothing to add for already-priced names


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
    _seed_earnings_cache(data)
    return price, sector, rates, sp, data


def _run(tmp_path, transport=_eod_transport, **kw):
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
    tmp = tmp_path_factory.mktemp("phase8u")
    return _run(tmp)


# --------------------------------------------------------------------------- #
# Tests.
# --------------------------------------------------------------------------- #
def test_defaults_are_bounded():
    assert MOD.DEFAULT_MAX_TICKERS == 250
    assert MOD.DEFAULT_MAX_REQUESTS == 500
    assert MOD.DEFAULT_START_DATE == "2016-01-01"


def test_end_to_end_produces_all_artifacts(expansion):
    report, out, _ = expansion
    for key, name in MOD._ARTIFACTS.items():
        assert (out / name).is_file(), "missing artifact %s" % name
    assert report["decision"] in MOD.ALLOWED_DECISIONS
    assert report["decision_is_terminal"] is True


def test_reads_phase8t_outputs(expansion):
    report, _, _ = expansion
    assert report["builds_on_phase8t_decision"] == "MORE_ALPHA_FOUND_READY_FOR_PAPER_TRADER_REVIEW"
    assert "surprise_sector_neutral" in report["phase8t_promoted"]
    assert "surprise_x_quality" in report["phase8t_promoted"]


def test_sp500_list_is_parsed(expansion):
    report, out, _ = expansion
    rows = _read_csv(out / MOD._ARTIFACTS["sp500_extraction"])
    syms = {r["ticker"] for r in rows}
    assert set(TICKERS).issubset(syms)
    assert "ZZZZ" not in syms                                    # changelog table excluded
    assert report["universe"]["sp500_name_list"] == N_TICKERS


def test_missing_price_tickers_identified(expansion):
    report, out, _ = expansion
    rows = _read_csv(out / MOD._ARTIFACTS["missing_tickers"])
    missing = {r["ticker"] for r in rows}
    assert missing == set(MISSING_TICKERS)
    assert report["universe"]["missing_price_tickers"] == len(MISSING_TICKERS)
    # the seeded earnings make these scoreable-after-price-only
    assert all(r["has_earnings_cache"] == "True" for r in rows)


def test_eod_prices_acquired_and_gitignored(expansion):
    report, out, data = expansion
    assert report["acquisition"]["prices_acquired"] == len(MISSING_TICKERS)
    # raw + normalized EOD payloads live under the gitignored eodhd tree, never in committed output
    gi = data / "eodhd" / ".gitignore"
    assert gi.is_file() and "*" in gi.read_text(encoding="utf-8")
    assert (data / "eodhd" / "raw" / "eod_prices").is_dir()
    assert (data / "eodhd" / "normalized" / "eod_prices").is_dir()
    assert (data / "eodhd" / "raw" / "eod_prices" / ("%s.json" % MISSING_TICKERS[0])).is_file()
    for p in out.glob("*"):
        if p.suffix == ".csv":
            head = p.read_text(encoding="utf-8")[:200].lower()
            assert "api_token" not in head


def test_expanded_panel_manifest_produced(expansion):
    report, out, _ = expansion
    man = {r["metric"]: r["value"] for r in _read_csv(out / MOD._ARTIFACTS["panel_manifest"])}
    assert man["is_expanded"] == "True"
    assert int(man["new_tickers"]) == len(MISSING_TICKERS)
    # base = N_BASE scoreable names + the priced ETF; plus the acquired missing names
    assert int(man["total_tickers"]) == N_BASE + 1 + len(MISSING_TICKERS)
    # expanded panel file itself is under the gitignored normalized tree
    assert report["expanded_panel"]["is_expanded"] is True


def test_before_after_comparison_produced(expansion):
    report, out, _ = expansion
    cov = {r["metric"]: r for r in _read_csv(out / MOD._ARTIFACTS["ba_coverage"])}
    assert "scoreable_tickers" in cov
    before = int(cov["scoreable_tickers"]["before"])
    after = int(cov["scoreable_tickers"]["after"])
    assert after > before                                        # the expansion genuinely widened it
    assert before == N_BASE and after == N_TICKERS
    ba = _read_csv(out / MOD._ARTIFACTS["ba_alpha"])
    assert ba and {"mean_ic_before", "mean_ic_after", "promoted_before", "promoted_after"}.issubset(ba[0])
    delta = _read_csv(out / MOD._ARTIFACTS["robustness_delta"])
    assert delta and "promotion_status" in delta[0]


def test_phase8t_signals_reevaluated_on_expanded_universe(expansion):
    report, out, _ = expansion
    # the comparison includes the 8-T focus signals with a before/after promotion verdict
    ba = {r["scenario"]: r for r in _read_csv(out / MOD._ARTIFACTS["ba_alpha"])}
    assert "surprise_sector_neutral" in ba
    assert "surprise_x_quality" in ba
    # the engineered surprise signal survives -> CONFIRMED
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
    assert len(scoreboard) > 10                                 # full 8-T battery, not a 10-scenario toy


def test_secret_safety_and_no_orders(expansion):
    report, out, _ = expansion
    audit = {r["check"]: r["result"] for r in _read_csv(out / MOD._ARTIFACTS["secret_audit"])}
    assert audit["secret_leak_scan_clean"] == "True"
    assert audit["api_key_written_to_disk"] == "False"
    assert audit["raw_normalized_prices_gitignored"] == "True"
    for flag in ("orders_enabled", "automation_enabled", "broker_execution_enabled",
                 "paper_trader_touched", "gcp_touched", "deployed", "committed", "data_fabricated"):
        assert report[flag] is False
    assert report["preview_only"] is True
    assert report["network_used"] is False                      # injected transport, no real network


def test_next_plan_is_preview_only(expansion):
    _, out, _ = expansion
    plan = MOD._read_json(out / MOD._ARTIFACTS["next_plan"])
    assert plan["preview_only"] is True
    assert plan["orders_enabled"] is False
    assert plan["automation_enabled"] is False


def test_offline_no_transport_is_next_price_batch(tmp_path):
    # no transport + no key + nothing cached to acquire -> price universe cannot be expanded now
    report, out, _ = _run(tmp_path, transport=None)
    assert report["acquisition"]["executed"] is False
    assert report["acquisition"]["prices_acquired"] == 0
    assert report["decision"] == MOD.DEC_NEXT_BATCH
    assert report["before"]["scoreable_tickers"] == report["after"]["scoreable_tickers"]
    man = {r["metric"]: r["value"] for r in _read_csv(out / MOD._ARTIFACTS["panel_manifest"])}
    assert man["is_expanded"] == "False"
