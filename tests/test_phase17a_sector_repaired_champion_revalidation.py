"""Fully-offline targeted tests for Phase 17-A - formal sector-repaired champion revalidation.

Proves: (a) the sector-neutral recompute is faithful (hand-computed single-sector case + the
frozen-column reproduction guard); (b) the committed 16-A resolved sector map loads with provenance and
never fabricates a sector; (c) factor definitions / orientations / weights are unchanged and there is no
winsorization or post-hoc tuning; (d) the a-priori decision ladder across every terminal branch
(BLOCK on repro-fail and on low coverage, FAIL on each weakness, KEEP when indistinguishable, ELIGIBLE
when strong+distinct); (e) blocked-data handling; (f) the challenger package is created ONLY on the
eligible decision, every row states NO_ORDER, and it never touches the Phase 13-A / 16-A artifacts; and
(g) an integration run over the REAL frozen panel + committed resolved map (skipped if absent) that
reproduces the frozen composite, returns an ALLOWED decision, and never mutates the champion, promotes to
live, or writes orders. No key, no network, no writes outside a tmp dir.
"""
from __future__ import annotations

import csv
import hashlib
import importlib
import json
import math
from pathlib import Path

import pytest

MOD = importlib.import_module("research.run_phase17a_sector_repaired_champion_revalidation")


# --------------------------------------------------------------------------- #
# Statistics + transform helpers.
# --------------------------------------------------------------------------- #
def test_spearman_and_std_and_z():
    assert MOD._spearman([1, 2, 3, 4], [10, 20, 30, 40]) == pytest.approx(1.0)
    assert MOD._spearman([1, 2, 3, 4], [40, 30, 20, 10]) == pytest.approx(-1.0)
    assert MOD._std([2.0, 4.0], 1) == pytest.approx(math.sqrt(2.0))
    z = MOD._within_month_z({0: 1.0, 1: 2.0, 2: 3.0})
    assert sum(z.values()) == pytest.approx(0.0, abs=1e-12)
    assert MOD._std(list(z.values()), 1) == pytest.approx(1.0)


def test_positive_rate_and_drawdown():
    assert MOD._positive_rate([0.1, -0.2, 0.3, 0.0]) == pytest.approx(0.5)
    assert MOD._max_drawdown([1.0, 2.0, 1.5, 3.0]) == pytest.approx(-0.5)


def test_recompute_single_sector_matches_within_month_z():
    rows = [
        {"ticker": "A", "rebalance_date": "2020-01-15", "fcf_to_assets": "0.30", "operating_accruals": "0.10"},
        {"ticker": "B", "rebalance_date": "2020-01-15", "fcf_to_assets": "0.10", "operating_accruals": "0.20"},
        {"ticker": "C", "rebalance_date": "2020-01-15", "fcf_to_assets": "0.20", "operating_accruals": "0.05"},
        {"ticker": "D", "rebalance_date": "2020-01-15", "fcf_to_assets": "0.05", "operating_accruals": "0.15"},
    ]
    mi = {"2020-01": [0, 1, 2, 3]}
    comp = MOD.recompute_composite(rows, mi, lambda tk: "Information Technology")
    o_fcf = {i: float(rows[i]["fcf_to_assets"]) for i in range(4)}
    o_acc = {i: -float(rows[i]["operating_accruals"]) for i in range(4)}
    zf = MOD._within_month_z(o_fcf)
    za = MOD._within_month_z(o_acc)
    for i in range(4):
        assert comp[i] == pytest.approx(zf[i] + za[i], abs=1e-12)


# --------------------------------------------------------------------------- #
# Factor definitions / weights are unchanged; NO winsorization; NO post-hoc tuning.
# --------------------------------------------------------------------------- #
def test_factor_orientations_and_weights_unchanged():
    assert MOD.ORI_FCF == +1.0 and MOD.ORI_ACC == -1.0
    assert MOD.QUANTILE == 5 and MOD.MIN_NAMES_PER_MONTH == 20
    assert (MOD.COST25, MOD.COST50) == (0.0025, 0.0050)
    # No winsorization: an extreme outlier's within-month z is NOT clipped at +/-3.
    vals = {i: 0.0 for i in range(20)}
    vals[20] = 1000.0
    zz = MOD._within_month_z(vals)
    assert max(abs(v) for v in zz.values()) > 3.0


def test_report_asserts_no_tuning(tmp_path):
    rep = _run_synthetic(tmp_path, repaired_differs=True, eligible=True)
    for flag in ("added_factors", "changed_weights", "flipped_signs", "tuned_thresholds",
                 "selected_favourable_period", "mutated_champion", "replaced_champion",
                 "promotes_to_live", "modified_phase13a_package", "modified_phase16a_artifacts"):
        assert rep[flag] is False, flag
    assert rep["recomputed_only_sector_neutral_transform"] is True


# --------------------------------------------------------------------------- #
# Resolved sector map: coverage + provenance; never fabricates.
# --------------------------------------------------------------------------- #
def test_resolved_map_loads_with_provenance(tmp_path):
    p = tmp_path / "resolved.csv"
    _write_resolved_map(p, {"AAA": "Energy", "BBB": "Financials"})
    m, issues = MOD.load_resolved_sector_map(p)
    assert set(m) == {"AAA", "BBB"} and issues == []
    assert m["AAA"]["resolved_sector"] == "Energy"
    assert m["AAA"]["source_family"] and m["AAA"]["source_field"] and m["AAA"]["confidence"]


def test_resolved_map_never_fabricates_blank_or_noncanonical(tmp_path):
    p = tmp_path / "resolved.csv"
    with open(p, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["ticker", "original_sector", "resolved_sector", "resolved_industry", "source_family",
                    "source_field", "source_file", "confidence", "point_in_time", "reason"])
        w.writerow(["AAA", "Unknown", "", "x", "s", "f", "p", "NONE", "n", "r"])          # blank -> skipped
        w.writerow(["BBB", "Unknown", "Made Up Sector", "x", "s", "f", "p", "MED", "n", "r"])  # non-canon
    m, issues = MOD.load_resolved_sector_map(p)
    assert "AAA" not in m                                   # blank sector never becomes a fabricated label
    assert any("blank/unknown" in i for i in issues)
    assert any("not in the canonical" in i for i in issues)


# --------------------------------------------------------------------------- #
# Decision ladder - DECLARED A-PRIORI; exercise every terminal branch.
# --------------------------------------------------------------------------- #
def _eval(ic_t, net25, net50, pre=(0.04, 0.008), post=(0.03, 0.016)):
    return {"ic_t_stat": ic_t, "net25_spread": net25, "net50_spread": net50,
            "subperiod": {"pre2020": {"mean_ic": pre[0], "mean_spread": pre[1]},
                          "post2020": {"mean_ic": post[0], "mean_spread": post[1]}}}


def test_decision_logic_declared_and_allowed_set():
    assert isinstance(MOD.DECISION_LOGIC, list) and len(MOD.DECISION_LOGIC) >= 4
    for d in (MOD.DEC_ELIGIBLE, MOD.DEC_KEEP, MOD.DEC_FAILED, MOD.DEC_BLOCKED_DATA, MOD.DEC_BLOCKED_ERROR):
        assert d in MOD.ALLOWED_DECISIONS
    for f in ("LIVE_TRADING_READY", "PRODUCTION_READY", "CHAMPION_REPLACED", "LIVE_CHAMPION_PROMOTED"):
        assert f in MOD.FORBIDDEN_DECISIONS


def test_decide_block_on_repro_failure():
    d, reasons = MOD.decide(repro_ok=False, resolved_frac=1.0, full_spearman=0.9, top25_overlap=0.9,
                            top50_overlap=0.9, champ=_eval(3.0, 0.01, 0.008), cand=_eval(2.9, 0.01, 0.008))
    assert d == MOD.DEC_BLOCKED_DATA and "Data-integrity" in reasons[0]


def test_decide_block_on_low_coverage():
    d, reasons = MOD.decide(repro_ok=True, resolved_frac=0.5, full_spearman=0.9, top25_overlap=0.9,
                            top50_overlap=0.9, champ=_eval(3.0, 0.01, 0.008), cand=_eval(2.9, 0.01, 0.008))
    assert d == MOD.DEC_BLOCKED_DATA and any("covers only" in r for r in reasons)


def test_decide_fail_on_weak_ic():
    d, reasons = MOD.decide(repro_ok=True, resolved_frac=1.0, full_spearman=0.9, top25_overlap=0.9,
                            top50_overlap=0.9, champ=_eval(3.0, 0.01, 0.008), cand=_eval(1.4, 0.01, 0.008))
    assert d == MOD.DEC_FAILED and any("monitor bar" in r for r in reasons)


def test_decide_fail_on_sign_flip():
    d, reasons = MOD.decide(repro_ok=True, resolved_frac=1.0, full_spearman=0.9, top25_overlap=0.9,
                            top50_overlap=0.9, champ=_eval(3.0, 0.01, 0.008), cand=_eval(-2.5, 0.01, 0.008))
    assert d == MOD.DEC_FAILED and any("sign flips" in r for r in reasons)


def test_decide_fail_on_nonpositive_net():
    d25, r25 = MOD.decide(repro_ok=True, resolved_frac=1.0, full_spearman=0.9, top25_overlap=0.9,
                          top50_overlap=0.9, champ=_eval(3.0, 0.01, 0.008), cand=_eval(2.9, -0.001, 0.008))
    assert d25 == MOD.DEC_FAILED and any("net-25bps" in r for r in r25)
    d50, r50 = MOD.decide(repro_ok=True, resolved_frac=1.0, full_spearman=0.9, top25_overlap=0.9,
                          top50_overlap=0.9, champ=_eval(3.0, 0.01, 0.008), cand=_eval(2.9, 0.01, -0.002))
    assert d50 == MOD.DEC_FAILED and any("net-50bps" in r for r in r50)


def test_decide_fail_on_subperiod_reversal():
    d, reasons = MOD.decide(repro_ok=True, resolved_frac=1.0, full_spearman=0.9, top25_overlap=0.9,
                            top50_overlap=0.9, champ=_eval(3.0, 0.01, 0.008, post=(0.03, 0.016)),
                            cand=_eval(2.9, 0.01, 0.008, post=(-0.03, -0.016)))
    assert d == MOD.DEC_FAILED and any("sign reversal" in r for r in reasons)


def test_decide_keep_when_indistinguishable():
    d, _ = MOD.decide(repro_ok=True, resolved_frac=1.0, full_spearman=0.99, top25_overlap=0.96,
                      top50_overlap=0.96, champ=_eval(3.0, 0.011, 0.009), cand=_eval(2.9, 0.010, 0.008))
    assert d == MOD.DEC_KEEP


def test_decide_eligible_when_strong_and_distinct():
    d, reasons = MOD.decide(repro_ok=True, resolved_frac=1.0, full_spearman=0.89, top25_overlap=0.88,
                            top50_overlap=0.84, champ=_eval(3.26, 0.0112, 0.0087), cand=_eval(2.93, 0.0102, 0.0077))
    assert d == MOD.DEC_ELIGIBLE and any("PARALLEL PAPER CHALLENGER" in r for r in reasons)


# --------------------------------------------------------------------------- #
# Blocked-data handling (missing panel / missing resolved map).
# --------------------------------------------------------------------------- #
def test_blocked_when_panel_missing(tmp_path):
    rep = MOD.run(out_dir=tmp_path / "o", panel_csv=tmp_path / "nope.csv",
                  resolved_csv=tmp_path / "r.csv", eod_dir=tmp_path / "eod",
                  pkg_json=tmp_path / "p.json", challenger_out=tmp_path / "ch", verbose=False)
    assert rep["decision"] == MOD.DEC_BLOCKED_DATA
    assert rep["challenger_package_created"] is False


def test_blocked_when_resolved_map_missing(tmp_path):
    tickers = ["T%02d" % i for i in range(24)]
    orig = {tk: ["Financials", "Energy"][i % 2] for i, tk in enumerate(tickers)}
    panel = tmp_path / "panel.csv"
    _build_panel(panel, tickers, orig, {}, ["2019-06", "2019-09", "2019-12"], eligible=False)
    rep = MOD.run(out_dir=tmp_path / "o", panel_csv=panel, resolved_csv=tmp_path / "missing.csv",
                  eod_dir=tmp_path / "eod", pkg_json=tmp_path / "p.json",
                  challenger_out=tmp_path / "ch", verbose=False)
    assert rep["decision"] == MOD.DEC_BLOCKED_DATA


# --------------------------------------------------------------------------- #
# Challenger package created ONLY on the eligible decision; NO_ORDER everywhere.
# --------------------------------------------------------------------------- #
def test_challenger_created_only_when_eligible(tmp_path):
    rep = _run_synthetic(tmp_path, repaired_differs=True, eligible=True)
    assert rep["decision"] == MOD.DEC_ELIGIBLE
    assert rep["challenger_package_created"] is True
    ch = tmp_path / "ch"
    pkg = ch / "phase17b_sector_repaired_challenger_package.json"
    assert pkg.is_file()
    assert not (ch / "phase17b_challenger_not_created_manifest.json").is_file()
    body = json.loads(pkg.read_text(encoding="utf-8"))
    assert body["order_action_all"] == "NO_ORDER" and body["replaced_champion"] is False
    assert body["promotes_to_live"] is False and body["immutable"] is True
    # every position row states NO_ORDER
    for name in ("challenger_paper_portfolio_top25.csv", "challenger_paper_portfolio_top50.csv",
                 "challenger_top25_candidates.csv", "challenger_full_ranked_universe.csv"):
        rows = list(csv.DictReader(open(ch / name, encoding="utf-8")))
        assert rows and all(r.get("order_action") == "NO_ORDER" for r in rows), name


def test_no_challenger_and_manifest_when_not_eligible(tmp_path):
    # repaired == original sectors => candidate is indistinguishable => KEEP => no challenger package.
    rep = _run_synthetic(tmp_path, repaired_differs=False, eligible=True)
    assert rep["decision"] == MOD.DEC_KEEP
    assert rep["challenger_package_created"] is False
    ch = tmp_path / "ch"
    assert not (ch / "phase17b_sector_repaired_challenger_package.json").is_file()
    manifest = ch / "phase17b_challenger_not_created_manifest.json"
    assert manifest.is_file()
    body = json.loads(manifest.read_text(encoding="utf-8"))
    assert body["challenger_package_created"] is False and body["champion_replaced"] is False


# --------------------------------------------------------------------------- #
# Required artifacts + schema; no keys in output; read-only safety.
# --------------------------------------------------------------------------- #
def test_required_artifacts_and_no_key_leak(tmp_path):
    _run_synthetic(tmp_path, repaired_differs=True, eligible=True)
    out = tmp_path / "o"
    for key in MOD._ARTIFACTS.values():
        assert (out / key).is_file(), key
    audit = list(csv.DictReader(open(out / MOD._ARTIFACTS["secret_audit"], encoding="utf-8")))
    assert audit and all(r["clean"] == "True" for r in audit)


# --------------------------------------------------------------------------- #
# Integration over the REAL frozen panel + committed resolved map (skipped if absent).
# --------------------------------------------------------------------------- #
def test_real_panel_integration_and_immutable_upstream(tmp_path):
    if not (MOD._DEF_PANEL.exists() and MOD._DEF_RESOLVED.exists()):
        pytest.skip("owned frozen panel / committed 16-A resolved map not present in this checkout")
    # snapshot the upstream artifacts to prove they are never mutated
    before = {p: _sha(p) for p in (MOD._DEF_PANEL, MOD._DEF_RESOLVED, MOD._DEF_13A) if p.exists()}
    rep = MOD.run(out_dir=tmp_path / "o", challenger_out=tmp_path / "ch", verbose=False)
    after = {p: _sha(p) for p in before}
    assert before == after, "an upstream owned artifact was modified"
    assert rep["decision"] in MOD.ALLOWED_DECISIONS and rep["decision"] not in MOD.FORBIDDEN_DECISIONS
    assert rep["decision_logic_declared_before_result"] is True
    assert rep["reproduction"]["reproduces_frozen_composite"] is True
    assert rep["reproduction"]["max_abs_error"] == pytest.approx(0.0, abs=1e-6)
    cov = rep["coverage"]
    assert cov["all234"]["after_pct"] >= cov["all234"]["before_pct"]
    assert cov["resolved_fraction"] >= MOD.MIN_RESOLVED_FRAC
    # read-only research contract
    for flag in ("mutated_champion", "replaced_champion", "modified_phase13a_package",
                 "modified_phase16a_artifacts", "promotes_to_live", "creates_orders",
                 "creates_automation", "wrote_to_paper_trader", "live_trading"):
        assert rep[flag] is False, flag
    assert rep["secret_safety_leak_scan_clean"] is True


# --------------------------------------------------------------------------- #
# Helpers.
# --------------------------------------------------------------------------- #
def _det(s: str) -> float:
    x = 0
    for ch in s:
        x = (x * 131 + ord(ch)) & 0xFFFFFFFF
    return (x % 100000) / 100000.0


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _write_resolved_map(path: Path, mapping):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["ticker", "original_sector", "resolved_sector", "resolved_industry", "source_family",
                    "source_field", "source_file", "confidence", "point_in_time", "reason"])
        for tk, sec in mapping.items():
            w.writerow([tk, "Unknown", sec, "Ind", "eodhd_raw_fundamentals(General.GicSector)",
                        "General.GicSector", "path/%s.json" % tk, "HIGH", "current-as-of (not PIT)",
                        "owned EODHD General.GicSector"])


def _build_panel(path: Path, tickers, orig_sectors, repaired_map, months, eligible):
    """Build a panel whose composite_sn IS the original-sector recompute (so the reproduction guard is
    exact). If `eligible`, forward_63d_return tracks the REPAIRED composite so the repaired candidate has
    a strong, cost-robust IC; otherwise it tracks the original composite."""
    rows = []
    for m in months:
        for tk in tickers:
            rows.append({"rebalance_date": m + "-15", "ticker": tk,
                         "sector": orig_sectors.get(tk, "Unknown"), "cohort": "C1", "is_new_cohort": "False",
                         "liquidity_proxy": repr(1e8 + _det(tk) * 1e8),
                         # distinct leading token so the two legs are independent (a shared prefix would
                         # make the hashes near-identical and cancel the composite to ~0).
                         "fcf_to_assets": repr(_det("fcf|" + tk + "|" + m) * 2 - 1),
                         "operating_accruals": repr(_det("acc|" + tk + "|" + m) * 2 - 1)})
    mi = {}
    for i, r in enumerate(rows):
        mi.setdefault(r["rebalance_date"][:7], []).append(i)

    def orig_fn(tk):
        s = orig_sectors.get(tk.upper(), "Unknown")
        return s if s in MOD.CANONICAL_GICS else "Unknown"

    def rep_fn(tk):
        s = repaired_map.get(tk.upper())
        if s in MOD.CANONICAL_GICS:
            return s
        return orig_fn(tk)

    comp_o = MOD.recompute_composite(rows, mi, orig_fn)
    comp_r = MOD.recompute_composite(rows, mi, rep_fn)
    for i, r in enumerate(rows):
        c = comp_o.get(i)
        r["composite_sn"] = "" if c is None else repr(c)
        driver = comp_r.get(i) if eligible else comp_o.get(i)
        r["forward_63d_return"] = "" if driver is None else repr(driver * 0.05)
    cols = ["rebalance_date", "ticker", "sector", "cohort", "is_new_cohort", "liquidity_proxy",
            "fcf_to_assets", "operating_accruals", "composite_sn", "forward_63d_return"]
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow(r)


def _run_synthetic(tmp_path, *, repaired_differs, eligible):
    tickers = ["T%02d" % i for i in range(30)]
    orig = {tk: ["Financials", "Energy"][i % 2] for i, tk in enumerate(tickers)}
    if repaired_differs:
        repaired = {tk: ["Information Technology", "Health Care", "Industrials"][i % 3]
                    for i, tk in enumerate(tickers)}
    else:
        repaired = dict(orig)                     # identical sectors -> candidate == champion -> KEEP
    months = ["2019-03", "2019-06", "2019-09", "2019-12", "2020-03", "2020-06", "2020-09", "2020-12"]
    panel = tmp_path / "panel.csv"
    _build_panel(panel, tickers, orig, repaired, months, eligible=eligible)
    resolved = tmp_path / "resolved.csv"
    _write_resolved_map(resolved, repaired)
    eod = tmp_path / "eod"
    eod.mkdir()
    for tk in tickers[:20]:
        (eod / ("%s.json" % tk)).write_text("{}", encoding="utf-8")
    return MOD.run(out_dir=tmp_path / "o", panel_csv=panel, resolved_csv=resolved, eod_dir=eod,
                   pkg_json=tmp_path / "no.json", challenger_out=tmp_path / "ch", verbose=False)
