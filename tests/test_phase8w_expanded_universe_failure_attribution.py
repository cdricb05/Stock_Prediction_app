"""Fully-offline tests for the Phase 8-W expanded-universe failure attribution + salvage.

No real key, no network, no writes outside a tmp dir. The synthetic expanded universe is built so the
OLD cohort carries a genuine surprise->drift link (its forward return is monotone in the earnings
surprise) while the NEW cohort is FLAT (its drift is independent of the surprise). This reproduces the
exact Phase 8-V symptom: every signal's IC roughly halves when the two halves are pooled, the
old-cohort signal stays strong, the new cohort dilutes it, and an old-cohort-constrained variant
salvages the alpha -> CONSTRAINED_ALPHA_SURVIVES.

They assert the brief's acceptance criteria:
  * the Phase 8-V expanded result (newly-scoreable cohort + before/after verdict) is read;
  * the old/new cohort split is produced and the new cohort is recognized as the dilutive one;
  * failure attribution (cohort / sector / liquidity / event-history / subperiod / data-quality) is
    produced;
  * constrained variants are tested and gated by the SAME 8-T promotion gate;
  * the final decision is one of the allowed values;
  * no API key is required, no external API is called, and no Paper Trader / GCP / order / deploy /
    full-regression logic is invoked (targeted module only).
"""
from __future__ import annotations

import csv
import datetime as dt
import importlib
import json
from pathlib import Path

import pytest

MOD = importlib.import_module("research.run_phase8w_expanded_universe_failure_attribution")
r8 = MOD.r8

N_OLD = 16
N_NEW = 16
OLD_TICKERS = ["OLD%s" % chr(65 + i) for i in range(N_OLD)]       # OLDA..OLDP
NEW_TICKERS = ["NEW%s" % chr(65 + i) for i in range(N_NEW)]       # NEWA..NEWP
ALL_TICKERS = OLD_TICKERS + NEW_TICKERS
ETF = "ETF1"
START = dt.date(2018, 1, 1)
END = dt.date(2024, 3, 31)

_QENDS = [dt.date(y, m, d) for y in (2018, 2019, 2020, 2021, 2022, 2023)
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


def _surprise_rank(idx):
    return (idx % 5) - 2                                          # -2..2


def _adj_close(idx, d_idx, is_old):
    # OLD: drift is monotone in the surprise rank (real alpha). NEW: flat drift (no surprise link).
    drift = (0.05 + 0.05 * _surprise_rank(idx)) if is_old else 0.05
    return 100.0 + idx * 1.0 + drift * d_idx


def _write_expanded_panel(path):
    """One combined price panel for OLD + NEW (+ an earnings-less benchmark ETF), exactly the shape
    the Phase 8-V expanded panel has (date, ticker, OHLC, volume, dollar_volume, benchmark, return)."""
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
                is_old = tk in OLD_TICKERS
                c = _adj_close(idx, d_idx, is_old)
                # liquidity varies with index so the liquidity-bucket attribution has spread
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
    """Seed EODHD earnings + fundamentals for BOTH cohorts (they are all scoreable on the expanded
    panel); the OLD/NEW distinction lives only in the price drift + the Phase 8-V cohort list."""
    rp = r8._Paths(data_dir=data_dir)
    normalized = {}
    for tk in ALL_TICKERS:
        payload = _fundamentals_payload(tk)
        r8._persist_raw(rp, tk, "fundamentals", payload)
        normalized[tk] = r8._normalize_earnings(tk, payload)
    r8._append_normalized(rp, normalized)


def _write_phase8v_report(path):
    """The committed-safe Phase 8-V report the attribution reads: the newly-scoreable NEW cohort +
    the before/after verdict (4 promoted -> 0 promoted, EXPANDED_UNIVERSE_WEAKENS_ALPHA)."""
    path.mkdir(parents=True, exist_ok=True)
    payload = {
        "phase": "8-V", "decision": "EXPANDED_UNIVERSE_WEAKENS_ALPHA",
        "before": {"scoreable_tickers": N_OLD, "usable_events": 9999,
                   "promoted_signals": ["surprise_sector_neutral", "surprise_x_quality",
                                        "positive_surprise_asymmetry", "surprise_magnitude"]},
        "after": {"scoreable_tickers": N_OLD + N_NEW, "usable_events": 19999,
                  "promoted_signals": []},
        "newly_scoreable_tickers": NEW_TICKERS,
    }
    with open(path / MOD._PHASE8V_REPORT, "w", encoding="utf-8") as fh:
        json.dump(payload, fh)


def _fixtures(tmp_path):
    panel = tmp_path / "expanded_panel.csv"
    sector = tmp_path / "sector.csv"
    rates = tmp_path / "rates.csv"
    _write_expanded_panel(panel)
    _write_sector_map(sector)
    _write_macro(rates)
    _write_phase8v_report(tmp_path / "phase8v")
    data = tmp_path / "data"
    _seed_earnings_cache(data)
    return panel, sector, rates, data


def _run(tmp_path, **kw):
    panel, sector, rates, data = _fixtures(tmp_path)
    out = tmp_path / "out"
    report = MOD.run(out_dir=out, phase8v_dir=tmp_path / "phase8v", price_csv=panel, data_dir=data,
                     sector_csv=sector, macro={"rates": rates}, verbose=False, **kw)
    return report, out, data


def _read_csv(path):
    with open(path, "r", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


# A module-scoped run (building the event table + the full attribution is heavy; share one run).
@pytest.fixture(scope="module")
def attribution(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("phase8w")
    return _run(tmp)


# --------------------------------------------------------------------------- #
# Tests.
# --------------------------------------------------------------------------- #
def test_allowed_decisions_and_artifacts(attribution):
    report, out, _ = attribution
    assert len(MOD._REQUIRED_ARTIFACTS) == 14
    for key in MOD._REQUIRED_ARTIFACTS:
        name = MOD._ARTIFACTS[key]
        assert (out / name).is_file(), "missing required artifact %s" % name
    assert report["decision"] in MOD.ALLOWED_DECISIONS
    assert report["decision_is_terminal"] is True


def test_reads_phase8v_expanded_result(attribution):
    report, _, _ = attribution
    assert report["builds_on_phase8v_decision"] == "EXPANDED_UNIVERSE_WEAKENS_ALPHA"
    assert len(report["phase8v_before_promoted"]) == 4
    assert report["phase8v_after_promoted"] == []
    assert report["universe"]["newly_scoreable_from_8v"] == N_NEW


def test_old_new_cohort_split_produced(attribution):
    report, out, _ = attribution
    uni = report["universe"]
    assert uni["old_cohort_tickers"] == N_OLD
    assert uni["new_cohort_tickers"] == N_NEW
    assert uni["scoreable_tickers"] == N_OLD + N_NEW
    # every target signal has an old / new / combined row
    perf = _read_csv(out / MOD._ARTIFACTS["cohort_perf"])
    cohorts = {(r["scenario"], r["cohort"]) for r in perf}
    for scen in MOD.TARGET_SCENARIOS:
        for c in ("old_cohort", "new_cohort", "combined"):
            assert (scen, c) in cohorts


def test_new_cohort_is_the_dilutive_one(attribution):
    report, out, _ = attribution
    rows = {r["scenario"]: r for r in _read_csv(out / MOD._ARTIFACTS["old_vs_new"])}
    ssn = rows["surprise_sector_neutral"]
    ic_old = float(ssn["ic_old"])
    ic_new = float(ssn["ic_new"])
    ic_comb = float(ssn["ic_combined"])
    # the engineered structure: old cohort carries the drift, new cohort dilutes it
    assert ic_old > ic_new
    assert ic_comb < ic_old
    assert ssn["diluted_by_new_cohort"] == "True"
    # the report's focus-cohort block agrees
    foc = report["focus_cohort_performance"]
    assert foc["old_cohort"]["mean_ic"] > foc["new_cohort"]["mean_ic"]


def test_all_attribution_dimensions_produced(attribution):
    _, out, _ = attribution
    sector = _read_csv(out / MOD._ARTIFACTS["sector_attr"])
    assert sector and {"is_weak_sector", "leave_one_out_delta", "new_cohort_share"}.issubset(sector[0])
    liq = _read_csv(out / MOD._ARTIFACTS["liquidity_attr"])
    assert liq and {"mean_ic", "bucket"}.issubset(liq[0])
    eh = _read_csv(out / MOD._ARTIFACTS["event_history_attr"])
    assert eh and {"mean_ic", "new_cohort_share"}.issubset(eh[0])
    sub = _read_csv(out / MOD._ARTIFACTS["subperiod_attr"])
    segs = {r["segment"] for r in sub}
    assert "pre_2020" in segs and "post_2020" in segs
    dq = _read_csv(out / MOD._ARTIFACTS["data_quality_attr"])
    assert dq and {"segment", "mean_ic"}.issubset(dq[0])


def test_constrained_variants_tested(attribution):
    report, out, _ = attribution
    sb = _read_csv(out / MOD._ARTIFACTS["variant_scoreboard"])
    labels = {r["variant"] for r in sb}
    for expected in ("old_cohort_only", "high_liquidity_only", "top_sector_only",
                     "sector_neutral_within_old_cohort", "quality_gated_surprise",
                     "large_surprise_only", "post_2020_only", "exclude_weak_sectors",
                     "require_min_event_history"):
        assert expected in labels
    assert report["constrained_variants_tested"] == len(sb)
    # each variant carries an honest constrained/exploratory rationale
    assert all(r["rationale"] for r in sb)


def test_constrained_alpha_salvage_decision(attribution):
    report, out, _ = attribution
    # with a genuinely strong old cohort, the old-cohort-constrained read should salvage the alpha
    assert report["decision"] == MOD.DEC_SURVIVES
    assert report["constrained_alpha_survives"] is True
    promoted = _read_csv(out / MOD._ARTIFACTS["variant_promoted"])
    assert len(promoted) >= 1
    prom_labels = {r["variant"] for r in promoted}
    assert "old_cohort_only" in prom_labels
    # the final decision json mirrors the report
    dec = MOD._read_json(out / MOD._ARTIFACTS["final_decision"])
    assert dec["decision"] == MOD.DEC_SURVIVES
    assert dec["main_reason_alpha_weakened"]


def test_no_key_no_network_no_orders(attribution):
    report, out, _ = attribution
    audit = {r["check"]: r["result"] for r in _read_csv(out / MOD._ARTIFACTS["secret_audit"])}
    assert audit["network_used"] == "False"
    assert audit["api_key_used"] == "False"
    assert audit["secret_leak_scan_clean"] == "True"
    for flag in ("orders_enabled", "automation_enabled", "broker_execution_enabled",
                 "paper_trader_touched", "gcp_touched", "deployed", "committed", "data_acquired",
                 "network_used", "data_fabricated"):
        assert report[flag] is False
    assert report["preview_only"] is True


def test_next_plan_is_preview_only(attribution):
    _, out, _ = attribution
    plan = MOD._read_json(out / MOD._ARTIFACTS["next_plan"])
    assert plan["preview_only"] is True
    assert plan["orders_enabled"] is False
    assert plan["automation_enabled"] is False
    assert plan["phase"] == "8-X"


def test_no_full_regression_or_forbidden_logic():
    # the runner must not shell out, run pytest, acquire data, or carry order/deploy logic
    src = Path(MOD.__file__).read_text(encoding="utf-8").lower()
    for bad in ("import subprocess", "pytest.main", "place_order", "submit_order",
                "broker.execute", "subprocess.run", "os.system", "_live_get", "requests.get"):
        assert bad not in src


def test_missing_panel_is_terminal_diag(tmp_path):
    # no expanded panel on disk -> no event table -> graceful terminal NEEDS_ADDITIONAL_DIAGNOSTIC
    _write_phase8v_report(tmp_path / "phase8v")
    out = tmp_path / "out"
    report = MOD.run(out_dir=out, phase8v_dir=tmp_path / "phase8v",
                     price_csv=tmp_path / "does_not_exist.csv", data_dir=tmp_path / "data",
                     sector_csv=tmp_path / "missing_sector.csv", macro={}, verbose=False)
    assert report["decision"] == MOD.DEC_DIAG
    assert report["decision_is_terminal"] is True
    for key in MOD._REQUIRED_ARTIFACTS:
        assert (out / MOD._ARTIFACTS[key]).is_file()
