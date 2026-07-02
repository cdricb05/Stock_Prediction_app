"""Fully-offline targeted tests for Phase 10-D - Quarterly EODHD/Norgate quality composite validation.

No key, no network, no writes outside a tmp dir. A small synthetic Norgate-style event panel is injected
into `run(ev=..., norm_csvs=...)`. Both legs carry a PERSISTENT per-ticker quality trait (sticky
quarter-to-quarter, like real fundamentals), so the equal-weight composite is genuinely predictive at
63d with LOW quarterly turnover -> it must survive (CONFIRMED or WEAK_MONITOR). The composite-verdict
logic is also unit-tested directly.
"""
from __future__ import annotations

import csv
import importlib
from pathlib import Path

import pytest

MOD = importlib.import_module("research.run_phase10d_quarterly_quality_composite_validation")
FWD = MOD.FWD_WINDOWS
N_OLD, N_NEW = 10, 10
TICKERS = ["T%02d" % i for i in range(N_OLD + N_NEW)]
SECTORS = ["Information Technology", "Health Care", "Financials", "Industrials"]
N_MONTHS = 18


def _make_panel():
    import numpy as np
    import pandas as pd
    rng = np.random.default_rng(20260629)
    qf = {tk: float(rng.standard_normal()) for tk in TICKERS}   # persistent fcf quality
    qa = {tk: float(rng.standard_normal()) for tk in TICKERS}   # persistent accrual level
    rows, fcf_norm, acc_norm = [], [], []
    start = pd.Timestamp("2019-04-01")
    for m in range(N_MONTHS):
        entry = start + pd.DateOffset(months=m) + pd.Timedelta(days=14)
        avail = entry - pd.Timedelta(days=5)
        for i, tk in enumerate(TICKERS):
            f = qf[tk] + 0.15 * float(rng.standard_normal())     # sticky fcf signal
            a = qa[tk] + 0.15 * float(rng.standard_normal())     # sticky accrual signal
            # higher fcf -> higher returns; HIGHER accruals -> LOWER returns (real Sloan direction),
            # so the oriented accrual (negated) is also predictive.
            core = 0.05 * qf[tk] - 0.05 * qa[tk]
            ret = {h: core + 0.02 * float(rng.standard_normal()) for h in FWD}
            rows.append({"ticker": tk, "entry_date": entry, "sector": SECTORS[i % len(SECTORS)],
                         "cohort": "old" if i < N_OLD else "new",
                         "liquidity_proxy": float((i + 1) * 1000),
                         "fwd_exc_1": ret[1], "fwd_exc_5": ret[5], "fwd_exc_21": ret[21],
                         "fwd_exc_63": ret[63]})
            fcf_norm.append({"ticker": tk, "available_date": avail.date().isoformat(), "fcf_to_assets": f})
            acc_norm.append({"ticker": tk, "available_date": avail.date().isoformat(),
                             "operating_accruals": a})
    return pd.DataFrame(rows), fcf_norm, acc_norm


def _write_norm(path, rows, feature):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["ticker", "available_date", feature])
        for r in rows:
            w.writerow([r["ticker"], r["available_date"], r[feature]])


@pytest.fixture()
def synthetic(tmp_path):
    ev, fcf_norm, acc_norm = _make_panel()
    fcf_csv = tmp_path / "norm" / "eodhd_fcf_to_assets" / "fcf_to_assets.csv"
    acc_csv = tmp_path / "norm" / "eodhd_operating_accruals" / "operating_accruals.csv"
    _write_norm(fcf_csv, fcf_norm, "fcf_to_assets")
    _write_norm(acc_csv, acc_norm, "operating_accruals")
    return ev, {"eodhd_fcf_to_assets": fcf_csv, "eodhd_operating_accruals": acc_csv}


def _run(synthetic, tmp_path):
    ev, norm_csvs = synthetic
    return MOD.run(out_dir=tmp_path / "out", ev=ev, norm_csvs=norm_csvs,
                   train_months=4, test_months=2, step_months=2, verbose=False)


# --------------------------------------------------------------------------- #
# Scope / transparency.
# --------------------------------------------------------------------------- #
def test_only_two_quality_legs_used():
    assert {l["feature"] for l in MOD.LEGS} == {"fcf_to_assets", "operating_accruals"}
    assert MOD.ALLOWED_FAMILIES == frozenset({"eodhd_fcf_to_assets", "eodhd_operating_accruals"})


def test_fixed_transparent_weights_no_optimization_no_signflip():
    assert all(l["weight"] == 1.0 for l in MOD.LEGS)         # equal weight, fixed
    src = Path(MOD.__file__).read_text(encoding="utf-8")
    for banned in ("scipy", "sklearn", "minimize(", "optimize(", "LinearRegression", "argmax(weights"):
        assert banned not in src, "no optimised weights allowed (%s)" % banned
    # walk-forward uses fixed 'equal' weighting (no sign refit / no ic_sign)
    assert '"ic_sign"' not in src and "'ic_sign'" not in src


def test_no_provider_acquisition_offline():
    assert MOD.PERFORMS_NETWORK is False
    src = Path(MOD.__file__).read_text(encoding="utf-8")
    for banned in ("urllib.request", "urlopen", "import requests", "http.client", "socket.",
                   "alphavantage", "polygon.io", "finnhub", "financialmodelingprep"):
        assert banned not in src
    for n in ("acquire_eodhd", "create_order", "execute_order", "place_order", "deploy"):
        assert not hasattr(MOD, n)


def test_63d_is_primary_horizon():
    assert MOD.PRIMARY_HORIZON_D == 63
    assert MOD.RET_PRIMARY == "fwd_exc_63"
    assert tuple(MOD.FWD_WINDOWS) == (1, 5, 21, 63)


# --------------------------------------------------------------------------- #
# End-to-end.
# --------------------------------------------------------------------------- #
def test_end_to_end_decision_allowed_and_artifacts(synthetic, tmp_path):
    report = _run(synthetic, tmp_path)
    assert report["decision"] in MOD.ALLOWED_DECISIONS
    assert report["decision"] not in MOD.FORBIDDEN_DECISIONS
    assert report["optimised_weights"] is False and report["sign_flipping"] is False
    out = tmp_path / "out"
    for name in report["required_artifacts"]:
        assert (out / name).is_file(), "missing artifact %s" % name


def test_predictive_composite_survives(synthetic, tmp_path):
    report = _run(synthetic, tmp_path)
    # persistent, genuinely-predictive, low-turnover composite -> survives (confirmed or monitor)
    assert report["composite_verdict"] in (MOD.V_CONFIRMED, MOD.V_WEAK)
    assert report["composite_survived"] is True


def test_required_diagnostic_and_comparison_reports(synthetic, tmp_path):
    _run(synthetic, tmp_path)
    out = tmp_path / "out"
    # monthly vs quarterly cost diagnostic
    diag = list(csv.DictReader(open(out / "monthly_vs_quarterly_cost_diagnostic.csv", encoding="utf-8")))
    assert diag and "monthly_event_turnover" in diag[0] and "quarterly_turnover" in diag[0]
    assert "net25_quarterly_63d" in diag[0]
    # standalone vs composite comparison includes composite + both standalones
    cmp = list(csv.DictReader(open(out / "standalone_vs_composite_comparison.csv", encoding="utf-8")))
    sigs = {r["signal"] for r in cmp}
    assert {"composite_raw", "fcf_to_assets", "operating_accruals"} <= sigs
    # transaction cost report includes 10/25/50 bps
    tc = list(csv.DictReader(open(out / "transaction_cost_report.csv", encoding="utf-8")))
    assert {"net_10bps", "net_25bps", "net_50bps"} <= set(tc[0].keys())
    assert any(r["model"] == "quarterly_63d" for r in tc)
    # turnover report distinguishes monthly vs quarterly
    tr = list(csv.DictReader(open(out / "turnover_report.csv", encoding="utf-8")))
    assert "quarterly_turnover" in tr[0] and "monthly_event_turnover" in tr[0]
    # cohort / subperiod / sector / liquidity present
    for fname in ("cohort_stability_report.csv", "subperiod_stability_report.csv",
                  "sector_exclusion_report.csv", "liquidity_filter_report.csv"):
        rows = list(csv.DictReader(open(out / fname, encoding="utf-8")))
        assert any(r["signal"] == "composite_raw" for r in rows)


def test_no_secret_leak(synthetic, tmp_path):
    report = _run(synthetic, tmp_path)
    assert report["offline"] is True and report["performs_network"] is False
    assert report["api_key_printed"] is False and report["api_key_written_to_disk"] is False
    assert report["secret_safety_leak_scan_clean"] is True
    audit = list(csv.DictReader(open(tmp_path / "out" / "secret_safety_audit.csv", encoding="utf-8")))
    assert audit and all(r["clean"] == "True" for r in audit)


def test_blocker_on_missing_inputs(synthetic, tmp_path):
    ev, _ = synthetic
    bad = {"eodhd_fcf_to_assets": tmp_path / "x1.csv", "eodhd_operating_accruals": tmp_path / "x2.csv"}
    report = MOD.run(out_dir=tmp_path / "blk", ev=ev, norm_csvs=bad, verbose=False)
    assert report["decision"] == MOD.DEC_BLOCKER and report["decision"] not in MOD.FORBIDDEN_DECISIONS


# --------------------------------------------------------------------------- #
# Composite-verdict unit tests.
# --------------------------------------------------------------------------- #
def _m(ic, t, share=0.30):
    return {"mean_ic": ic, "ic_t": t, "ic_p": 0.01, "n_events": 2000, "n_months": 40,
            "mean_spread": 0.012, "avg_turnover": 0.4, "spread_hit_rate": 0.62,
            "net_spread_10bps": 0.010, "net_spread_25bps": 0.006, "net_spread_50bps": 0.002,
            "top_sector": "Information Technology", "top_sector_share": share, "hhi": 0.2}


def _bat(ic_t63, oos_pooled, oos_frac, qnet25, qnet50, *, cohort_ok=True, sub_ok=True, sector_ok=True,
         highliq_ok=True, share=0.30):
    horizons = {h: _m(0.04, ic_t63 if h == 63 else 2.5, share) for h in FWD}
    return {
        "horizons": horizons,
        "wf": {"pooled_oos_ic": oos_pooled, "frac_windows_positive": oos_frac, "oos_ic_t": 3.0,
               "n_windows": 6, "windows": [], "decile": {}},
        "cohort": {"old": _m(0.03, 3.0), "new": _m(0.03 if cohort_ok else -0.03, 3.0 if cohort_ok else -3.0)},
        "subperiod": {"pre2020": _m(0.03 if sub_ok else -0.03, 3.0), "post2020": _m(0.03, 3.0)},
        "sector_excl": {"top_sector_share": share, "sign_holds_all": sector_ok, "rows": [],
                        "top_sector": "X", "hhi": 0.2},
        "liquidity": {"full": horizons[63], "high_liq": _m(0.03 if highliq_ok else -0.03, 3.0),
                      "low_liq": _m(0.01, 1.0)},
        "quarterly": {"net_25bps": qnet25, "net_50bps": qnet50, "avg_turnover": 0.4, "mean_spread": 0.012,
                      "spread_t": 3.0, "n_quarters": 20, "top_sector_share": share, "top_sector": "X"},
    }


def _sn(positive=True):
    return _bat(3.0, 0.03, 0.9, 0.006, 0.002)  # sn primary mean_ic = 0.04 (positive)


def test_verdict_confirmed():
    _f, v, _r = MOD.decide_composite(_bat(3.4, 0.03, 0.9, 0.006, 0.002), _sn())
    assert v == MOD.V_CONFIRMED


def test_verdict_cohort_rejected_first():
    _f, v, _r = MOD.decide_composite(_bat(3.4, 0.03, 0.9, 0.006, 0.002, cohort_ok=False), _sn())
    assert v == MOD.V_REJ_COHORT


def test_verdict_sector_rejected():
    _f, v, _r = MOD.decide_composite(_bat(3.4, 0.03, 0.9, 0.006, 0.002, sector_ok=False), _sn())
    assert v == MOD.V_REJ_SECTOR


def test_verdict_cost_rejected():
    _f, v, _r = MOD.decide_composite(_bat(3.4, 0.03, 0.9, -0.001, -0.004), _sn())
    assert v == MOD.V_REJ_COST


def test_verdict_weak_monitor():
    # OOS-strong, cost-robust at 25bps, but IC t<3.0 and net50<0 -> monitor, not paper-ready
    _f, v, _r = MOD.decide_composite(_bat(2.4, 0.03, 0.9, 0.006, -0.001), _sn())
    assert v == MOD.V_WEAK


def test_verdict_exhausted_when_no_edge():
    _f, v, _r = MOD.decide_composite(_bat(1.0, 0.03, 0.9, 0.006, 0.002), _sn())
    assert v == MOD.V_EXHAUSTED


def test_all_verdicts_map_to_allowed_decisions():
    for v in (MOD.V_CONFIRMED, MOD.V_WEAK, MOD.V_REJ_COST, MOD.V_REJ_COHORT, MOD.V_REJ_SECTOR,
              MOD.V_EXHAUSTED):
        dec = MOD._VERDICT_TO_DEC[v]
        assert dec in MOD.ALLOWED_DECISIONS and dec not in MOD.FORBIDDEN_DECISIONS
