"""Fully-offline targeted tests for Phase 16-A Part F - shadow sector-neutral revalidation.

Proves (a) the sector-neutral recompute is faithful (a hand-computed single-sector case + the
frozen-column reproduction guard), (b) the cross-sectional statistics helpers, (c) the a-priori
decision ladder across every branch (repro-fail, each REVALIDATE trigger, KEEP, KEEP_PENDING), and
(d) an integration run over the REAL frozen panel (skipped if absent) that must reproduce the frozen
composite, resolve the champion universe from owned data, and return an ALLOWED decision without ever
mutating the champion. No key, no network, no writes outside a tmp dir, no orders/automation/live.
"""
from __future__ import annotations

import csv
import importlib
import json
import math
from pathlib import Path

import pytest

MOD = importlib.import_module(
    "research.run_phase16a_sector_metadata_integrity_and_shadow_revalidation")


# --------------------------------------------------------------------------- #
# Cross-sectional statistics helpers.
# --------------------------------------------------------------------------- #
def test_spearman_perfect_and_reverse():
    assert MOD._spearman([1, 2, 3, 4], [10, 20, 30, 40]) == pytest.approx(1.0)
    assert MOD._spearman([1, 2, 3, 4], [40, 30, 20, 10]) == pytest.approx(-1.0)


def test_std_sample_ddof1():
    assert MOD._std([2.0, 4.0], 1) == pytest.approx(math.sqrt(2.0))   # sample std of {2,4}
    assert MOD._std([5.0], 1) is None                                 # undefined for n<=ddof


def test_within_month_z_is_standardised():
    z = MOD._within_month_z({0: 1.0, 1: 2.0, 2: 3.0})
    vals = [z[0], z[1], z[2]]
    assert sum(vals) == pytest.approx(0.0, abs=1e-12)                 # mean 0
    assert MOD._std(vals, 1) == pytest.approx(1.0)                    # unit sample std


def test_max_drawdown():
    assert MOD._max_drawdown([1.0, 2.0, 1.5, 3.0]) == pytest.approx(-0.5)
    assert MOD._max_drawdown([1.0, 2.0, 3.0]) == pytest.approx(0.0)


def test_t_stat_positive_series():
    t = MOD._t_stat([0.02, 0.03, 0.025, 0.028, 0.022])
    assert t is not None and t > 0


# --------------------------------------------------------------------------- #
# Sector-neutral recompute: hand-computed single-sector single-month case.
# --------------------------------------------------------------------------- #
def test_recompute_single_sector_matches_within_month_z():
    # One sector, one month: sector de-mean subtracts the common mean, then within-month z. The result
    # must equal the within-month z of the oriented legs (fcf +1, accruals -1) summed.
    rows = [
        {"ticker": "A", "rebalance_date": "2020-01-15", "fcf_to_assets": "0.30", "operating_accruals": "0.10"},
        {"ticker": "B", "rebalance_date": "2020-01-15", "fcf_to_assets": "0.10", "operating_accruals": "0.20"},
        {"ticker": "C", "rebalance_date": "2020-01-15", "fcf_to_assets": "0.20", "operating_accruals": "0.05"},
        {"ticker": "D", "rebalance_date": "2020-01-15", "fcf_to_assets": "0.05", "operating_accruals": "0.15"},
    ]
    mi = {"2020-01": [0, 1, 2, 3]}
    comp = MOD.recompute_composite(rows, mi, lambda tk: "Information Technology")
    # independent reference: o_fcf = +fcf, o_acc = -acc; z each (single sector -> de-mean == plain);
    o_fcf = {i: float(rows[i]["fcf_to_assets"]) for i in range(4)}
    o_acc = {i: -float(rows[i]["operating_accruals"]) for i in range(4)}
    zf = MOD._within_month_z(o_fcf)
    za = MOD._within_month_z(o_acc)
    for i in range(4):
        assert comp[i] == pytest.approx(zf[i] + za[i], abs=1e-12)


def test_recompute_reproduces_frozen_when_original_sectors_used(tmp_path):
    # Build a panel whose composite_sn IS the original-sector recompute; the runner's reproduction guard
    # must then find max_abs_err == 0 and repro_spearman == 1.
    tickers = ["T%02d" % i for i in range(24)]
    orig = {tk: (["Financials", "Energy"][i % 2]) for i, tk in enumerate(tickers)}
    panel = tmp_path / "panel.csv"
    _build_repro_panel(panel, tickers, orig, ["2019-06", "2019-09", "2019-12", "2020-03"])
    fund = tmp_path / "fund"
    fund.mkdir()
    for i, tk in enumerate(tickers):                       # repair maps to DIFFERENT sectors -> shadow moves
        (fund / ("%s.json" % tk)).write_text(
            json.dumps({"General": {"GicSector": ["Information Technology", "Health Care"][i % 2]}}),
            encoding="utf-8")
    _empty_curated(tmp_path / "cur.csv")
    rep = MOD.run(out_dir=tmp_path / "out", panel_csv=panel, fund_dir=fund,
                  curated_csv=tmp_path / "cur.csv", pkg_json=tmp_path / "no.json", verbose=False)
    assert rep["reproduction"]["reproduces_frozen_composite"] is True
    assert rep["reproduction"]["max_abs_error"] == pytest.approx(0.0, abs=1e-9)
    assert rep["reproduction"]["rank_spearman"] == pytest.approx(1.0, abs=1e-6)


# --------------------------------------------------------------------------- #
# Decision ladder - DECLARED A-PRIORI; exercise every branch.
# --------------------------------------------------------------------------- #
def _eval(ic_t, net25, net50, pre=(0.04, 0.008), post=(0.03, 0.016)):
    return {"ic_t_stat": ic_t, "net25_spread": net25, "net50_spread": net50,
            "subperiod": {"pre2020": {"mean_ic": pre[0], "mean_spread": pre[1]},
                          "post2020": {"mean_ic": post[0], "mean_spread": post[1]}}}


def test_decision_logic_is_declared_before_result():
    assert isinstance(MOD.DECISION_LOGIC, list) and len(MOD.DECISION_LOGIC) >= 4
    assert MOD.DEC_KEEP in MOD.ALLOWED_DECISIONS
    assert MOD.DEC_KEEP_PENDING in MOD.ALLOWED_DECISIONS
    assert MOD.DEC_REVALIDATE in MOD.ALLOWED_DECISIONS
    assert "CHAMPION_REPLACED" in MOD.FORBIDDEN_DECISIONS


def test_decide_repro_failure_forces_revalidation():
    d, reasons = MOD.decide(repro_ok=False, full_spearman=0.999, top25_overlap=1.0, top50_overlap=1.0,
                            champ=_eval(3.0, 0.01, 0.008), shadow=_eval(3.0, 0.01, 0.008))
    assert d == MOD.DEC_REVALIDATE
    assert "Data-integrity" in reasons[0]


def test_decide_keep_when_consistent():
    d, _ = MOD.decide(repro_ok=True, full_spearman=0.99, top25_overlap=0.9, top50_overlap=0.9,
                      champ=_eval(3.0, 0.011, 0.009), shadow=_eval(2.9, 0.010, 0.008))
    assert d == MOD.DEC_KEEP


def test_decide_revalidate_on_low_full_spearman():
    d, reasons = MOD.decide(repro_ok=True, full_spearman=0.85, top25_overlap=0.9, top50_overlap=0.9,
                            champ=_eval(3.0, 0.011, 0.009), shadow=_eval(2.9, 0.010, 0.008))
    assert d == MOD.DEC_REVALIDATE
    assert any("materially change ranks" in r for r in reasons)


def test_decide_revalidate_when_shadow_ic_drops_below_monitor():
    d, reasons = MOD.decide(repro_ok=True, full_spearman=0.99, top25_overlap=0.9, top50_overlap=0.9,
                            champ=_eval(3.0, 0.011, 0.009), shadow=_eval(1.4, 0.010, 0.008))
    assert d == MOD.DEC_REVALIDATE
    assert any("monitor bar" in r for r in reasons)


def test_decide_revalidate_when_net25_turns_nonpositive():
    d, reasons = MOD.decide(repro_ok=True, full_spearman=0.99, top25_overlap=0.9, top50_overlap=0.9,
                            champ=_eval(3.0, 0.011, 0.009), shadow=_eval(2.9, -0.001, -0.004))
    assert d == MOD.DEC_REVALIDATE
    assert any("net-25bps" in r for r in reasons)


def test_decide_revalidate_on_subperiod_sign_flip():
    d, reasons = MOD.decide(repro_ok=True, full_spearman=0.99, top25_overlap=0.9, top50_overlap=0.9,
                            champ=_eval(3.0, 0.011, 0.009, post=(0.03, 0.016)),
                            shadow=_eval(2.9, 0.010, 0.008, post=(-0.03, -0.016)))
    assert d == MOD.DEC_REVALIDATE
    assert any("Subperiod sign instability" in r for r in reasons)


def test_decide_keep_pending_when_consistent_but_not_tight():
    # No materiality trigger (spearman 0.94 >= 0.90, ic ok, net25 ok, no flip) but not KEEP-tight
    # (spearman < 0.98 and overlaps < 0.80) -> hold the champion pending more data.
    d, _ = MOD.decide(repro_ok=True, full_spearman=0.94, top25_overlap=0.7, top50_overlap=0.7,
                      champ=_eval(3.0, 0.011, 0.009), shadow=_eval(2.9, 0.010, 0.008))
    assert d == MOD.DEC_KEEP_PENDING


# --------------------------------------------------------------------------- #
# Integration over the REAL frozen panel (skipped if the owned artifacts are absent).
# --------------------------------------------------------------------------- #
def test_real_panel_integration(tmp_path):
    if not (MOD._DEF_PANEL.exists() and MOD._DEF_FUND.is_dir()):
        pytest.skip("owned frozen panel / fundamentals not present in this checkout")
    rep = MOD.run(out_dir=tmp_path / "out", verbose=False)
    assert rep["decision"] in MOD.ALLOWED_DECISIONS
    assert rep["decision"] not in MOD.FORBIDDEN_DECISIONS
    assert rep["decision_logic_declared_before_result"] is True
    # The recompute reproduces the frozen champion composite with the original sectors.
    assert rep["reproduction"]["reproduces_frozen_composite"] is True
    # Owned data resolves the champion universe's sectors; coverage improves.
    cov = rep["sector_coverage_summary"]
    assert cov["all234_after_pct"] >= cov["all234_before_pct"]
    assert cov["top25_after_pct"] >= cov["top25_before_pct"]
    # Read-only research contract preserved.
    assert rep["mutated_champion"] is False and rep["modified_phase13a_package"] is False
    assert rep["replaced_champion"] is False and rep["promotes_to_live"] is False
    assert rep["creates_orders"] is False and rep["creates_automation"] is False


# --------------------------------------------------------------------------- #
# Helpers.
# --------------------------------------------------------------------------- #
def _det(s: str) -> float:
    x = 0
    for ch in s:
        x = (x * 131 + ord(ch)) & 0xFFFFFFFF
    return (x % 100000) / 100000.0


def _build_repro_panel(path: Path, tickers, orig_sectors, months):
    rows = []
    for m in months:
        for tk in tickers:
            rows.append({"rebalance_date": m + "-15", "ticker": tk,
                         "sector": orig_sectors.get(tk, "Unknown"),
                         "fcf_to_assets": repr(_det(tk + m + "f") * 2 - 1),
                         "operating_accruals": repr(_det(tk + m + "a") * 2 - 1),
                         "forward_63d_return": repr(_det(tk + m + "r") * 0.1 - 0.05)})
    mi = {}
    for i, r in enumerate(rows):
        mi.setdefault(r["rebalance_date"][:7], []).append(i)

    def orig_fn(tk):
        s = orig_sectors.get(tk.upper(), "Unknown")
        return s if s in MOD.CANONICAL_GICS else "Unknown"

    comp = MOD.recompute_composite(rows, mi, orig_fn)
    for i, r in enumerate(rows):
        c = comp.get(i)
        r["composite_sn"] = "" if c is None else repr(c)
    cols = ["rebalance_date", "ticker", "sector", "fcf_to_assets", "operating_accruals",
            "forward_63d_return", "composite_sn"]
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow(r)


def _empty_curated(path: Path):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        csv.writer(fh).writerow(["ticker", "sector", "industry"])
