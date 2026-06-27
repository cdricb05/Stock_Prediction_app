"""Fully-offline tests for the Phase 8-X autonomous strong-alpha discovery campaign.

No real key, no network, no writes outside a tmp dir. Two synthetic universes drive the two outcomes
the brief cares about:

  * STRONG universe - BOTH cohorts carry a genuine, monotone surprise->drift link across all sectors
    and both subperiods. With a lowered universe floor a broad signal clears the FULL strong gate
    (t>=3, BH-significant, net-of-25bps positive, positive IC in both cohorts and both subperiods,
    sector-diversified) -> STRONG_ALPHA_FOUND.
  * DILUTED universe - only the OLD cohort carries the drift; the NEW cohort is flat. No broad signal
    clears the gate; statistically-strong-but-cohort-constrained signals are logged as
    CONSTRAINED_NOT_GOOD_ENOUGH (never promoted), the hypothesis space is exhausted ->
    NEEDS_NEW_DATA_FAMILY.

The gate logic + classification are also unit-tested directly (fast, deterministic), and the resume /
bounded-search / missing-panel paths are exercised.
"""
from __future__ import annotations

import csv
import datetime as dt
import importlib
import json
from pathlib import Path

import pytest

MOD = importlib.import_module("research.run_phase8x_autonomous_strong_alpha_discovery")
r8 = MOD.r8

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

# lowered strong-alpha universe floor so a small synthetic universe can still be promoted on merit
LOW_TICKERS = 8
LOW_EVENTS = 200


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


def _adj_close(idx, d_idx, is_old, new_carries):
    # MULTIPLICATIVE growth so the percentage forward return depends ONLY on the surprise rank (not on
    # the base price level). Carrying cohorts grow monotone-in-surprise; a non-carrying NEW cohort is
    # truly flat (constant price -> identical forward excess return for every name -> IC undefined ~0),
    # which is what makes the dilution clean (no spurious cross-sectional signal leaks in via the base).
    if is_old or new_carries:
        g = 0.0003 * _surprise_rank(idx)
    else:
        g = 0.0
    return (100.0 + idx) * (1.0 + g) ** d_idx


def _write_expanded_panel(path, new_carries):
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
                c = _adj_close(idx, d_idx, is_old, new_carries)
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
    payload = {
        "phase": "8-V", "decision": "EXPANDED_UNIVERSE_WEAKENS_ALPHA",
        "before": {"scoreable_tickers": N_OLD, "promoted_signals": ["surprise_sector_neutral"]},
        "after": {"scoreable_tickers": N_OLD + N_NEW, "promoted_signals": []},
        "newly_scoreable_tickers": NEW_TICKERS,
    }
    with open(path / MOD._PHASE8V_REPORT, "w", encoding="utf-8") as fh:
        json.dump(payload, fh)


def _write_phase8w_reports(path):
    path.mkdir(parents=True, exist_ok=True)
    rep = {"phase": "8-W", "decision": "CONSTRAINED_ALPHA_SURVIVES",
           "constrained_promoted_variants": ["old_cohort_only", "high_liquidity_only"],
           "weak_sectors": ["Energy", "Unknown"]}
    with open(path / MOD._PHASE8W_REPORT, "w", encoding="utf-8") as fh:
        json.dump(rep, fh)
    dec = {"phase": "8-W", "decision": "CONSTRAINED_ALPHA_SURVIVES",
           "constrained_promoted_variants": ["old_cohort_only", "high_liquidity_only"],
           "weak_sectors": ["Energy", "Unknown"]}
    with open(path / MOD._PHASE8W_DECISION, "w", encoding="utf-8") as fh:
        json.dump(dec, fh)


def _build_env(tmp_path, new_carries):
    panel = tmp_path / "expanded_panel.csv"
    sector = tmp_path / "sector.csv"
    rates = tmp_path / "rates.csv"
    _write_expanded_panel(panel, new_carries)
    _write_sector_map(sector)
    _write_macro(rates)
    _write_phase8v_report(tmp_path / "phase8v")
    _write_phase8w_reports(tmp_path / "phase8w")
    data = tmp_path / "data"
    _seed_earnings_cache(data)
    return panel, sector, rates, data


def _run(tmp_path, panel, sector, rates, data, out_name="out", **kw):
    out = tmp_path / out_name
    report = MOD.run(out_dir=out, phase8v_dir=tmp_path / "phase8v", phase8w_dir=tmp_path / "phase8w",
                     price_csv=panel, data_dir=data, sector_csv=sector, macro={"rates": rates},
                     verbose=False, **kw)
    return report, out


def _read_csv(path):
    with open(path, "r", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


# --------------------------------------------------------------------------- #
# Module-scoped heavy runs (build event table + full campaign once each).
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def strong_env(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("phase8x_strong")
    panel, sector, rates, data = _build_env(tmp, new_carries=True)
    return tmp, panel, sector, rates, data


@pytest.fixture(scope="module")
def strong_run(strong_env):
    tmp, panel, sector, rates, data = strong_env
    return _run(tmp, panel, sector, rates, data, out_name="out_low",
                min_tickers=LOW_TICKERS, min_events=LOW_EVENTS)


@pytest.fixture(scope="module")
def gate_size_run(strong_env):
    # SAME genuinely-strong data, but the real >=500-ticker / >=30000-event floor: must NOT promote.
    tmp, panel, sector, rates, data = strong_env
    return _run(tmp, panel, sector, rates, data, out_name="out_default")


@pytest.fixture(scope="module")
def diluted_env(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("phase8x_diluted")
    panel, sector, rates, data = _build_env(tmp, new_carries=False)
    return tmp, panel, sector, rates, data


@pytest.fixture(scope="module")
def diluted_run(diluted_env):
    tmp, panel, sector, rates, data = diluted_env
    return _run(tmp, panel, sector, rates, data, out_name="out_low",
                min_tickers=LOW_TICKERS, min_events=LOW_EVENTS)


# --------------------------------------------------------------------------- #
# Tests.
# --------------------------------------------------------------------------- #
def test_required_artifacts_and_terminal_decision(diluted_run):
    report, out = diluted_run
    assert len(MOD._REQUIRED_ARTIFACTS) == 19
    for key in MOD._REQUIRED_ARTIFACTS:
        name = MOD._ARTIFACTS[key]
        assert (out / name).is_file(), "missing required artifact %s" % name
    assert report["decision"] in MOD.ALLOWED_DECISIONS
    assert report["decision_is_terminal"] is True


def test_reads_phase8v_and_phase8w(diluted_run):
    report, _ = diluted_run
    assert report["builds_on_phase8v_decision"] == "EXPANDED_UNIVERSE_WEAKENS_ALPHA"
    assert report["builds_on_phase8w_decision"] == "CONSTRAINED_ALPHA_SURVIVES"
    assert report["universe"]["newly_scoreable_from_8v"] == N_NEW
    # 8-W constrained variants + weak sectors were read and carried into the report
    assert "old_cohort_only" in report["phase8w_constrained_variants"]
    assert "Energy" in report["phase8w_weak_sectors"]


def test_expanded_universe_loaded(diluted_run):
    report, _ = diluted_run
    assert report["universe"]["scoreable_tickers"] == N_OLD + N_NEW


def test_autonomous_loop_runs_more_than_one_cycle(diluted_run):
    report, out = diluted_run
    assert report["autonomous_loop"]["cycles_run"] > 1
    cyc = _read_csv(out / MOD._ARTIFACTS["cycle_log"])
    assert len({r["cycle"] for r in cyc}) > 1


def test_models_and_walk_forward_tested(diluted_run):
    report, out = diluted_run
    assert report["models_tested"] >= 1
    ms = _read_csv(out / MOD._ARTIFACTS["model_scoreboard"])
    assert ms, "model scoreboard should not be empty"
    wf = _read_csv(out / MOD._ARTIFACTS["walk_forward"])
    assert wf, "walk-forward results should not be empty"
    assert {"train_start", "test_start", "oos_mean_ic"}.issubset(wf[0])
    dec = _read_csv(out / MOD._ARTIFACTS["decile_spread"])
    assert dec and "mean_decile_spread" in dec[0]


def test_strong_alpha_found_on_strong_universe(strong_run):
    report, out = strong_run
    assert report["decision"] == MOD.DEC_STRONG
    assert report["strong_alpha_found"] is True
    strong = _read_csv(out / MOD._ARTIFACTS["strong_candidates"])
    assert len(strong) >= 1
    # every promoted candidate genuinely cleared the cohort + subperiod parts of the gate
    for r in strong:
        assert r["subperiod_stable"] == "True"
        assert float(r["ic_t"]) >= MOD.STRONG_MIN_IC_T


def test_weak_constrained_not_promoted_and_needs_new_data(diluted_run):
    report, out = diluted_run
    assert report["decision"] == MOD.DEC_NEW_DATA
    assert report["strong_alpha_found"] is False
    assert report["strong_alpha_candidates"] == []
    assert report["current_data_exhausted"] is True
    assert report["recommended_new_data_family"] == MOD._NEW_DATA_FAMILY
    # the surprise signals are NOT promoted as strong; they appear only as constrained or rejected
    reg = {r["name"]: r for r in _read_csv(out / MOD._ARTIFACTS["hypothesis_registry"])}
    ssn = reg.get("surprise_sector_neutral")
    assert ssn is not None
    assert ssn["status"] in ("constrained", "rejected")
    assert ssn["status"] != "strong"


def test_size_gate_enforced_on_real_universe_floor(gate_size_run):
    # genuinely-strong stats, but the synthetic universe is far below the >=500-ticker floor ->
    # the strong gate must REFUSE to promote it.
    report, out = gate_size_run
    assert report["decision"] != MOD.DEC_STRONG
    assert report["strong_alpha_found"] is False
    assert report["universe"]["meets_strong_universe_floor"] is False
    reg = {r["name"]: r for r in _read_csv(out / MOD._ARTIFACTS["hypothesis_registry"])}
    # at least one statistically strong scenario is rejected specifically for the universe size
    assert any("universe too small" in r["reject_reasons"] for r in reg.values())


def test_strong_gate_and_classification_unit():
    base = {"name": "x", "family": "earnings", "exploratory": False, "bh_significant": True,
            "ic_old": 0.05, "ic_new": 0.04,
            "metrics": {"n_events": 5000, "n_months": 24, "mean_ic": 0.05, "ic_t": 4.0, "ic_p": 1e-4,
                        "net_spread_25bps": 0.01, "spread_hit_rate": 0.9, "subperiod_stable": True,
                        "top_sector_share": 0.3}}
    big_n, big_e = MOD.STRONG_MIN_TICKERS, MOD.STRONG_MIN_EVENTS
    # (a) genuinely strong -> no reasons -> classify strong
    r = MOD.strong_gate_reasons(base, big_n, big_e, big_n, big_e)
    assert r == []
    assert MOD.classify(base, r) == "strong"
    # (b) old-cohort-only (new cohort negative) -> only the cohort reason -> constrained, NOT strong
    c = dict(base); c["ic_new"] = -0.01
    r = MOD.strong_gate_reasons(c, big_n, big_e, big_n, big_e)
    assert r and all(x == MOD._CONSTRAINING_REASONS[0] for x in r)
    assert MOD.classify(c, r) == "constrained"
    # (c) genuinely strong stats but universe below floor -> rejected (size is non-constraining)
    r = MOD.strong_gate_reasons(base, 50, 1000, big_n, big_e)
    assert any("universe too small" in x for x in r)
    assert MOD.classify(base, r) == "rejected"
    # (d) weak t-stat -> rejected
    w = dict(base); w["metrics"] = dict(base["metrics"], ic_t=1.5)
    r = MOD.strong_gate_reasons(w, big_n, big_e, big_n, big_e)
    assert MOD.classify(w, r) == "rejected"
    # (e) exploratory challenge is never strong
    e = dict(base); e["exploratory"] = True
    r = MOD.strong_gate_reasons(e, big_n, big_e, big_n, big_e)
    assert MOD.classify(e, r) != "strong"


def test_bounded_search_reports_no_strong(diluted_env):
    # stop after a single cycle (before exhausting the space) on a universe with no strong alpha ->
    # NO_STRONG_ALPHA_FOUND_CURRENT_DATA (distinct from data-exhaustion).
    tmp, panel, sector, rates, data = diluted_env
    report, _ = _run(tmp, panel, sector, rates, data, out_name="out_bounded",
                     min_tickers=LOW_TICKERS, min_events=LOW_EVENTS, max_cycles=1)
    assert report["decision"] == MOD.DEC_NO_STRONG
    assert report["autonomous_loop"]["hypothesis_space_exhausted"] is False


def test_resume_state_skips_already_tested(diluted_env):
    tmp, panel, sector, rates, data = diluted_env
    first, out = _run(tmp, panel, sector, rates, data, out_name="out_resume",
                      min_tickers=LOW_TICKERS, min_events=LOW_EVENTS, fresh=False)
    assert first["autonomous_loop"]["scenarios_tested"] > 0
    assert (out / MOD._ARTIFACTS["resume_state"]).is_file()
    # a second run with the SAME inputs must re-test nothing (no repeated scenario testing)
    second = MOD.run(out_dir=out, phase8v_dir=tmp / "phase8v", phase8w_dir=tmp / "phase8w",
                     price_csv=panel, data_dir=data, sector_csv=sector, macro={"rates": rates},
                     min_tickers=LOW_TICKERS, min_events=LOW_EVENTS, fresh=False, verbose=False)
    assert second["autonomous_loop"]["scenarios_tested"] == 0


def test_no_key_no_network_no_orders(diluted_run):
    report, out = diluted_run
    audit = {r["check"]: r["result"] for r in _read_csv(out / MOD._ARTIFACTS["secret_audit"])}
    assert audit["network_used"] == "False"
    assert audit["api_key_used"] == "False"
    assert audit["secret_leak_scan_clean"] == "True"
    for flag in ("orders_enabled", "automation_enabled", "broker_execution_enabled",
                 "paper_trader_touched", "gcp_touched", "deployed", "committed", "data_acquired",
                 "network_used", "full_regression_invoked", "constrained_signal_productized"):
        assert report[flag] is False
    assert report["preview_only"] is True


def test_next_plan_is_preview_only(diluted_run):
    _, out = diluted_run
    plan = MOD._read_json(out / MOD._ARTIFACTS["next_plan"])
    assert plan["preview_only"] is True
    assert plan["orders_enabled"] is False
    assert plan["automation_enabled"] is False
    assert plan["phase"] == "8-Y"
    assert plan["recommended_new_data_family"] == MOD._NEW_DATA_FAMILY


def test_no_forbidden_logic_in_source():
    src = Path(MOD.__file__).read_text(encoding="utf-8").lower()
    for bad in ("import subprocess", "pytest.main", "place_order", "submit_order", "broker.execute",
                "subprocess.run", "os.system", "_live_get", "requests.get", "acquire_eodhd"):
        assert bad not in src


def test_missing_panel_is_terminal_blocker(diluted_env):
    tmp, _panel, sector, rates, data = diluted_env
    out = tmp / "out_missing"
    report = MOD.run(out_dir=out, phase8v_dir=tmp / "phase8v", phase8w_dir=tmp / "phase8w",
                     price_csv=tmp / "does_not_exist.csv", data_dir=tmp / "empty_data",
                     sector_csv=sector, macro={"rates": rates}, verbose=False)
    assert report["decision"] == MOD.DEC_BLOCKER
    assert report["decision_is_terminal"] is True
    for key in MOD._REQUIRED_ARTIFACTS:
        assert (out / MOD._ARTIFACTS[key]).is_file()
