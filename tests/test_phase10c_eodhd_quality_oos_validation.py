"""Fully-offline targeted tests for Phase 10-C - Strict OOS validation of the EODHD/Norgate quality
leads (fcf_to_assets, operating_accruals).

No key, no network, no writes outside a tmp dir. A small synthetic Norgate-style event panel is
injected directly into `run(ev=..., norm_csvs=...)` (the validation is offline and never touches a
provider). The panel plants:

  * fcf_to_assets  -> genuinely predictive of forward returns in its oriented (+1) form  -> must NOT be
    rejected (it should CONFIRM or land in WEAK_BUT_WORTH_MONITORING).
  * operating_accruals -> sample sign is INVERTED vs the Sloan prior, so its oriented (-1) form has a
    negative out-of-sample IC -> must be REJECTED (overfit / non-generalising).

The verdict/decision logic is also unit-tested directly (fast, deterministic).
"""
from __future__ import annotations

import csv
import importlib
from pathlib import Path

import pytest

MOD = importlib.import_module("research.run_phase10c_eodhd_quality_oos_validation")

PRIMARY = MOD.PRIMARY_HORIZON
HORIZONS = MOD.FWD_WINDOWS

N_OLD, N_NEW = 10, 10                                    # >=8 names per cohort half (engine floor)
TICKERS = ["T%02d" % i for i in range(N_OLD + N_NEW)]
SECTORS = ["Information Technology", "Health Care", "Financials", "Industrials"]
N_MONTHS = 16                                            # spans mid-2019..2020 (straddles 2020 split)


# --------------------------------------------------------------------------- #
# Synthetic panel + normalized CSVs.
# --------------------------------------------------------------------------- #
def _make_panel():
    import numpy as np
    import pandas as pd
    rng = np.random.default_rng(1234)
    rows = []
    fcf_norm, acc_norm = [], []
    start = pd.Timestamp("2019-06-01")                   # m=0..15 -> 2019-06 .. 2020-09 (crosses 2020)
    for m in range(N_MONTHS):
        entry = start + pd.DateOffset(months=m) + pd.Timedelta(days=14)
        avail = entry - pd.Timedelta(days=5)
        for i, tk in enumerate(TICKERS):
            f = float(rng.standard_normal())
            a = float(rng.standard_normal())
            signal = 0.06 * f + 0.06 * a               # BOTH raw features +corr with returns
            ret = {h: signal + 0.015 * float(rng.standard_normal()) for h in HORIZONS}
            rows.append({
                "ticker": tk, "entry_date": entry,
                "sector": SECTORS[i % len(SECTORS)],
                "cohort": "old" if i < N_OLD else "new",
                "liquidity_proxy": float((i + 1) * 1000),
                "fwd_exc_1": ret[1], "fwd_exc_5": ret[5], "fwd_exc_21": ret[21], "fwd_exc_63": ret[63],
            })
            fcf_norm.append({"ticker": tk, "available_date": avail.date().isoformat(),
                             "fcf_to_assets": f})
            # accruals raw is +corr with returns, so the Sloan-oriented (-1) signal is anti-predictive
            acc_norm.append({"ticker": tk, "available_date": avail.date().isoformat(),
                             "operating_accruals": a})
    return pd.DataFrame(rows), fcf_norm, acc_norm


def _write_norm(path: Path, rows, feature):
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
    norm_csvs = {"eodhd_fcf_to_assets": fcf_csv, "eodhd_operating_accruals": acc_csv}
    return ev, norm_csvs


def _run(synthetic, tmp_path):
    ev, norm_csvs = synthetic
    return MOD.run(out_dir=tmp_path / "out", ev=ev, norm_csvs=norm_csvs,
                   train_months=4, test_months=2, step_months=2, verbose=False)


# --------------------------------------------------------------------------- #
# Scope: ONLY the two quality leads are validated.
# --------------------------------------------------------------------------- #
def test_only_two_quality_leads_validated():
    feats = {c["feature"] for c in MOD.CANDIDATES}
    assert feats == {"fcf_to_assets", "operating_accruals"}
    assert MOD.ALLOWED_FAMILIES == frozenset({"eodhd_fcf_to_assets", "eodhd_operating_accruals"})
    assert len(MOD.CANDIDATES) == 2


def test_no_new_provider_acquisition():
    # the runner is offline by contract and never imports/uses a network client itself
    assert MOD.PERFORMS_NETWORK is False
    src = Path(MOD.__file__).read_text(encoding="utf-8")
    for banned in ("urllib.request", "urlopen", "import requests", "http.client", "socket."):
        assert banned not in src, "Phase 10-C must not perform network I/O (%s)" % banned
    # no acquisition/order/deploy entry points
    for n in ("acquire_eodhd", "create_order", "execute_order", "place_order", "deploy"):
        assert not hasattr(MOD, n)


def test_raw_and_sector_neutral_variants():
    assert [v for v, _ in MOD.VARIANTS] == ["raw", "sector_neutral"]


def test_horizons_include_full_sweep():
    assert tuple(MOD.FWD_WINDOWS) == (1, 5, 21, 63)
    assert MOD.PRIMARY_HORIZON == 21


# --------------------------------------------------------------------------- #
# End-to-end on the synthetic panel.
# --------------------------------------------------------------------------- #
def test_end_to_end_decision_and_artifacts(synthetic, tmp_path):
    report = _run(synthetic, tmp_path)
    assert report["decision"] in MOD.ALLOWED_DECISIONS
    assert report["decision"] not in MOD.FORBIDDEN_DECISIONS
    # every required artifact written
    out = tmp_path / "out"
    for name in report["required_artifacts"]:
        assert (out / name).is_file(), "missing artifact %s" % name


def test_predictive_signal_not_rejected_noise_signal_rejected(synthetic, tmp_path):
    report = _run(synthetic, tmp_path)
    by = {r["feature"]: r for r in report["candidate_results"]}
    # fcf is genuinely predictive (oriented +) -> survives (confirmed or monitor), never rejected
    assert by["fcf_to_assets"]["verdict"] in (MOD.V_CONFIRMED, MOD.V_WEAK)
    # accruals sample-sign is inverted vs the prior -> oriented (-1) is anti-predictive OOS -> rejected
    assert by["operating_accruals"]["verdict"] in (
        MOD.V_REJ_OVERFIT, MOD.V_REJ_COST, MOD.V_REJ_COHORT)
    assert report["fcf_to_assets_survived"] is True
    assert report["operating_accruals_survived"] is False


def test_walk_forward_split_and_stability_reports_present(synthetic, tmp_path):
    _run(synthetic, tmp_path)
    out = tmp_path / "out"
    # OOS split definition is non-trivial
    split = list(csv.DictReader(open(out / "oos_split_definition.csv", encoding="utf-8")))
    assert any(r.get("test_start") for r in split)
    # cohort, subperiod, sector-exclusion, tx-cost reports all carry both signals x both variants
    for fname, ncols in (("cohort_stability_report.csv", "both_cohorts_positive"),
                         ("subperiod_stability_report.csv", "both_subperiods_positive"),
                         ("transaction_cost_report.csv", "net_25bps"),
                         ("horizon_validation_report.csv", "oriented_positive")):
        rows = list(csv.DictReader(open(out / fname, encoding="utf-8")))
        feats = {r["feature"] for r in rows}
        variants = {r["variant"] for r in rows}
        assert {"fcf_to_assets", "operating_accruals"} <= feats
        assert {"raw", "sector_neutral"} <= variants
        assert ncols in rows[0]
    # sector exclusion report names excluded sectors
    se = list(csv.DictReader(open(out / "sector_exclusion_report.csv", encoding="utf-8")))
    assert any(r["scope"].startswith("exclude:") for r in se)
    # walk-forward IC report has per-signal pooled summary rows
    wf = list(csv.DictReader(open(out / "walk_forward_ic_report.csv", encoding="utf-8")))
    assert any(r["window"] == "ALL" for r in wf)


def test_no_secret_leak_and_offline(synthetic, tmp_path):
    report = _run(synthetic, tmp_path)
    assert report["offline"] is True
    assert report["performs_network"] is False
    assert report["api_key_printed"] is False
    assert report["api_key_written_to_disk"] is False
    assert report["secret_safety_leak_scan_clean"] is True
    out = tmp_path / "out"
    audit = list(csv.DictReader(open(out / "secret_safety_audit.csv", encoding="utf-8")))
    assert audit and all(r["clean"] == "True" for r in audit)


# --------------------------------------------------------------------------- #
# Blocker paths (missing inputs -> HARD_BLOCKER, never a forbidden decision).
# --------------------------------------------------------------------------- #
def test_missing_normalized_csv_is_hard_blocker(synthetic, tmp_path):
    ev, _ = synthetic
    bad = {"eodhd_fcf_to_assets": tmp_path / "nope1.csv",
           "eodhd_operating_accruals": tmp_path / "nope2.csv"}
    report = MOD.run(out_dir=tmp_path / "blk", ev=ev, norm_csvs=bad, verbose=False)
    assert report["decision"] == MOD.DEC_BLOCKER
    assert report["decision"] not in MOD.FORBIDDEN_DECISIONS
    assert "exact_next_command" in report


def test_empty_panel_is_hard_blocker(tmp_path):
    import pandas as pd
    report = MOD.run(out_dir=tmp_path / "blk2", ev=pd.DataFrame(), norm_csvs=MOD._default_norm_csvs(),
                     verbose=False)
    assert report["decision"] == MOD.DEC_BLOCKER


# --------------------------------------------------------------------------- #
# Verdict / decision unit tests (deterministic).
# --------------------------------------------------------------------------- #
def _metrics(ic, t, net25, net50, share=0.30):
    return {"mean_ic": ic, "ic_t": t, "ic_p": 0.01, "n_events": 2000, "n_months": 40,
            "mean_spread": 0.02, "avg_turnover": 0.5, "spread_hit_rate": 0.62,
            "net_spread_10bps": net25 + 0.001, "net_spread_25bps": net25, "net_spread_50bps": net50,
            "top_sector": "Information Technology", "top_sector_share": share, "hhi": 0.2}


def _variant(prim_ic, prim_t, net25, net50, oos_pooled, oos_frac, *, cohort_ok=True, sub_ok=True,
             sector_ok=True, sn_ok=True, n_pos_horizons=4, liq_ok=True, share=0.30):
    horizons = {}
    for k, h in enumerate(HORIZONS):
        if h == PRIMARY:
            horizons[h] = _metrics(prim_ic, prim_t, net25, net50, share)
        else:
            pos = k < n_pos_horizons
            horizons[h] = _metrics(0.02 if pos else -0.02, 2.0 if pos else -2.0, 0.01, 0.005, share)
    sgn = 1.0 if cohort_ok else -1.0
    return {
        "horizons": horizons,
        "cohort": {"old": _metrics(0.02, 3.0, net25, net50),
                   "new": _metrics(0.02 * sgn, 3.0 * sgn, net25, net50)},
        "subperiod": {"pre2020": _metrics(0.02 if sub_ok else -0.02, 3.0, net25, net50),
                      "post2020": _metrics(0.02, 3.0, net25, net50)},
        "sector_excl": {"top_sector_share": share, "top_sector": "Information Technology",
                        "hhi": 0.2, "sign_holds_all": sector_ok, "rows": []},
        "liquidity": {"full": horizons[PRIMARY],
                      "high_liq": _metrics(0.02 if liq_ok else -0.02, 3.0, net25, net50),
                      "low_liq": _metrics(0.01, 1.0, net25, net50)},
        "wf": {"pooled_oos_ic": oos_pooled, "oos_ic_t": 3.0, "frac_windows_positive": oos_frac,
               "n_windows": 6, "windows": [], "decile": {}},
    }


def _res(raw, sn_prim_ic):
    sn = _variant(sn_prim_ic, 3.0, 0.01, 0.005, 0.02, 0.9)
    return {"family": "eodhd_fcf_to_assets", "feature": "fcf_to_assets", "orientation": 1,
            "anomaly": "x", "coverage": 2000, "tickers": 500, "usable": True,
            "variants": {"raw": raw, "sector_neutral": sn}}


def test_verdict_confirmed_when_strong_and_robust():
    raw = _variant(0.05, 3.5, 0.01, 0.004, oos_pooled=0.03, oos_frac=0.9)
    flags, verdict, _reason = MOD._verdict(_res(raw, 0.03))
    assert verdict == MOD.V_CONFIRMED


def test_verdict_overfit_when_oos_fails():
    raw = _variant(0.05, 3.5, 0.01, 0.004, oos_pooled=-0.01, oos_frac=0.2)
    _flags, verdict, _r = MOD._verdict(_res(raw, 0.03))
    assert verdict == MOD.V_REJ_OVERFIT


def test_verdict_cohort_instability():
    raw = _variant(0.05, 3.5, 0.01, 0.004, oos_pooled=0.03, oos_frac=0.9, cohort_ok=False)
    _f, verdict, _r = MOD._verdict(_res(raw, 0.03))
    assert verdict == MOD.V_REJ_COHORT


def test_verdict_not_cost_robust():
    raw = _variant(0.05, 3.5, net25=-0.001, net50=-0.01, oos_pooled=0.03, oos_frac=0.9)
    _f, verdict, _r = MOD._verdict(_res(raw, 0.03))
    assert verdict == MOD.V_REJ_COST


def test_verdict_weak_monitor_when_directional_but_subthreshold():
    # OOS+, cohorts+, subperiods+, cost ok, but primary t below the strong 3.0 bar -> monitor
    raw = _variant(0.02, 2.0, 0.005, 0.002, oos_pooled=0.02, oos_frac=0.8)
    _f, verdict, _r = MOD._verdict(_res(raw, 0.02))
    assert verdict == MOD.V_WEAK


def test_overall_decision_mapping():
    def mk(verdict, feature="fcf_to_assets"):
        return {"usable": True, "verdict": verdict, "feature": feature}
    assert MOD.overall_decision([mk(MOD.V_CONFIRMED), mk(MOD.V_WEAK)])[0] == MOD.DEC_CONFIRMED
    assert MOD.overall_decision([mk(MOD.V_WEAK), mk(MOD.V_REJ_OVERFIT)])[0] == MOD.DEC_WEAK
    assert MOD.overall_decision([mk(MOD.V_REJ_OVERFIT), mk(MOD.V_REJ_OVERFIT)])[0] == MOD.DEC_EXHAUSTED
    assert MOD.overall_decision([mk(MOD.V_REJ_COST), mk(MOD.V_REJ_COST)])[0] == MOD.DEC_REJ_COST
    assert MOD.overall_decision([mk(MOD.V_REJ_COHORT), mk(MOD.V_REJ_COHORT)])[0] == MOD.DEC_REJ_COHORT
    # every mapped decision is allowed and never forbidden
    for v in (MOD.V_CONFIRMED, MOD.V_WEAK, MOD.V_REJ_OVERFIT, MOD.V_REJ_COST, MOD.V_REJ_COHORT):
        dec = MOD.overall_decision([mk(v), mk(v)])[0]
        assert dec in MOD.ALLOWED_DECISIONS and dec not in MOD.FORBIDDEN_DECISIONS
