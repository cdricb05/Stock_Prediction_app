"""Tests for Phase 7-F — Signal Reliability Upgrade.

Offline, deterministic. The unit tests exercise the safe-ratio guards, the implied-
share construction, the point-in-time TTM roll-forward, the equal-weight bucket /
composite combination, the upgraded price-factor specs, the catalogue invariants
(low-volatility demoted to a risk descriptor; no sign flipping), the signal-weakness
inventory, the gate matrix, and the recommendation logic. A guarded end-to-end test
runs the full engine against the real local data only when that data is present, and
verifies same-universe Phase 7-C reproduction, the improvement attribution, leakage
discipline, and that regimes are not used to select / weight factors.
"""
from __future__ import annotations

import importlib.util
import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

_RUNNER = _REPO_ROOT / "research" / "run_phase7f_signal_reliability_upgrade.py"


def _load_runner():
    spec = importlib.util.spec_from_file_location("phase7f_runner_test", _RUNNER)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["phase7f_runner_test"] = mod
    spec.loader.exec_module(mod)
    return mod


F = _load_runner()

_TS = pd.Timestamp


# --------------------------------------------------------------------------- #
# Safe ratio guards.
# --------------------------------------------------------------------------- #
def test_safe_ratio_guards():
    assert F._safe_ratio(10.0, 2.0) == 5.0
    assert F._safe_ratio(10.0, 0.0) is None              # zero denominator
    assert F._safe_ratio(10.0, -5.0) is None             # den_positive default -> negative blocked
    assert F._safe_ratio(10.0, -5.0, den_positive=False) == -2.0
    assert F._safe_ratio(None, 2.0) is None
    assert F._safe_ratio(float("nan"), 2.0) is None


# --------------------------------------------------------------------------- #
# Implied diluted shares = NI / EPS (same split basis), point-in-time.
# --------------------------------------------------------------------------- #
def test_shares_obs_from_ni_and_eps():
    ni = {"AAA": [(_TS("2020-12-31"), _TS("2021-02-01"), 1000.0),
                  (_TS("2021-12-31"), _TS("2022-02-01"), 1200.0)]}
    eps = {"AAA": [(_TS("2020-12-31"), _TS("2021-02-01"), 2.0),
                   (_TS("2021-12-31"), _TS("2022-02-01"), 2.0)]}
    sh = F._shares_obs(ni, eps)
    vals = [v for _, _, v in sh["AAA"]]
    assert vals == [500.0, 600.0]              # 1000/2, 1200/2


def test_shares_obs_skips_zero_and_negative_eps():
    ni = {"AAA": [(_TS("2020-12-31"), _TS("2021-02-01"), 1000.0),
                  (_TS("2021-12-31"), _TS("2022-02-01"), -50.0)]}
    eps = {"AAA": [(_TS("2020-12-31"), _TS("2021-02-01"), 0.0),     # zero EPS -> skipped
                   (_TS("2021-12-31"), _TS("2022-02-01"), 1.0)]}     # NI<0 -> NI/EPS<0 -> skipped
    sh = F._shares_obs(ni, eps)
    assert "AAA" not in sh                      # both periods rejected


# --------------------------------------------------------------------------- #
# Point-in-time TTM roll-forward.
# --------------------------------------------------------------------------- #
def test_ttm_asof_rollforward():
    # Annual FY ends 2020-12-31 (value 400, available 2021-02). Interim Q1+Q2 2021
    # (110, 120) replace prior-year Q1+Q2 2020 (100, 100): TTM = 400 + 230 - 200 = 430.
    annual = [(_TS("2019-12-31"), _TS("2020-02-01"), 380.0),
              (_TS("2020-12-31"), _TS("2021-02-01"), 400.0)]
    qtr = [(_TS("2020-03-31"), _TS("2020-05-01"), 100.0),
           (_TS("2020-06-30"), _TS("2020-08-01"), 100.0),
           (_TS("2021-03-31"), _TS("2021-05-01"), 110.0),
           (_TS("2021-06-30"), _TS("2021-08-01"), 120.0)]
    ttm = F.ttm_asof(annual, qtr, _TS("2021-09-01"))
    assert ttm == pytest.approx(430.0)


def test_ttm_asof_requires_interim_quarter():
    # No interim quarters after the latest annual -> None (won't relabel an annual as TTM).
    annual = [(_TS("2020-12-31"), _TS("2021-02-01"), 400.0)]
    assert F.ttm_asof(annual, [], _TS("2021-09-01")) is None


def test_ttm_asof_respects_availability():
    # The interim quarter is not yet available at the cutoff -> falls back to None
    # (no matchable interim before cutoff).
    annual = [(_TS("2020-12-31"), _TS("2021-02-01"), 400.0)]
    qtr = [(_TS("2021-03-31"), _TS("2021-05-01"), 110.0)]   # available 2021-05
    assert F.ttm_asof(annual, qtr, _TS("2021-04-01")) is None  # cutoff before interim availability


def test_ttm_asof_unmatched_prioryear_returns_none():
    # Interim 2021-Q1 present but no matching 2020-Q1 to subtract -> None.
    annual = [(_TS("2020-12-31"), _TS("2021-02-01"), 400.0)]
    qtr = [(_TS("2021-03-31"), _TS("2021-05-01"), 110.0)]
    assert F.ttm_asof(annual, qtr, _TS("2021-09-01")) is None


# --------------------------------------------------------------------------- #
# Equal-weight combination (no learned weights; missing-tolerant).
# --------------------------------------------------------------------------- #
def test_combine_equal_weight_means_present_values():
    a = pd.DataFrame({"month": ["2020-01", "2020-01"], "ticker": ["X", "Y"], "z": [1.0, 2.0]})
    b = pd.DataFrame({"month": ["2020-01"], "ticker": ["X"], "z": [3.0]})
    out = F.combine_equal_weight({"a": a, "b": b}, ["a", "b"]).set_index("ticker")["z"]
    assert out["X"] == pytest.approx(2.0)      # mean(1, 3)
    assert out["Y"] == pytest.approx(2.0)      # only a present -> 2.0


def test_combine_equal_weight_empty_keys():
    out = F.combine_equal_weight({}, ["a"])
    assert list(out.columns) == ["month", "ticker", "z"] and out.empty


# --------------------------------------------------------------------------- #
# Upgraded price factors (momentum specs).
# --------------------------------------------------------------------------- #
def test_build_upgraded_price_factors_specs():
    months = pd.period_range("2019-01", periods=18, freq="M")
    # Strictly rising series -> positive 12-1 and 6-1 momentum.
    close = pd.DataFrame({"AAA": np.linspace(100, 200, len(months))}, index=months)
    pf = {"monthly_close": close,
          "low_volatility": pd.DataFrame(columns=["month", "ticker", "raw"])}
    raw = F.build_upgraded_price_factors(pf)
    for k in ("mom_12_1", "mom_6_1", "mom_riskadj"):
        assert not raw[k].empty
        assert (raw[k]["raw"] > 0).all()       # rising series -> positive momentum everywhere defined
    # 12-1 needs 13 months of history; 6-1 needs 7 -> 6-1 has more observations.
    assert len(raw["mom_6_1"]) > len(raw["mom_12_1"])


# --------------------------------------------------------------------------- #
# Catalogue invariants: low-vol is a risk descriptor, excluded from alpha buckets.
# --------------------------------------------------------------------------- #
def test_low_volatility_is_risk_descriptor_excluded_from_alpha():
    lv = next(sf for sf in F.SUBFACTORS if sf.key == "low_volatility")
    assert lv.role == "risk_descriptor"
    assert lv.bucket not in F.ALPHA_BUCKETS
    # No alpha bucket lists low_volatility as a member.
    alpha_members = [sf.key for sf in F.SUBFACTORS if sf.role == "alpha"]
    assert "low_volatility" not in alpha_members


def test_accruals_factor_is_inverted_and_split_invariant():
    acc = next(sf for sf in F.SUBFACTORS if sf.key == "qual_accruals_inv")
    assert acc.bucket == "quality" and acc.split_invariant is True
    assert "inverted" in acc.definition or "lower accruals" in acc.definition


def test_quality_and_growth_buckets_are_split_invariant():
    for sf in F.SUBFACTORS:
        if sf.bucket in ("quality", "growth"):
            assert sf.split_invariant is True, sf.key
    # Value (price-based) and momentum are not split-invariant.
    assert all(not sf.split_invariant for sf in F.SUBFACTORS if sf.bucket == "value")


# --------------------------------------------------------------------------- #
# Signal-weakness inventory invariants.
# --------------------------------------------------------------------------- #
def test_signal_weakness_inventory_wellformed():
    ids = set()
    for w in F.SIGNAL_WEAKNESSES:
        assert {"id", "source", "failure_mode", "root_cause", "status_7f", "addressed_by"} <= set(w)
        assert w["status_7f"] in ("addressed", "partial", "not_addressed")
        ids.add(w["id"])
    assert len(ids) == len(F.SIGNAL_WEAKNESSES)          # unique ids
    # Low-vol mis-specification (W5) and single-spec buckets (W2) are addressed.
    by_id = {w["id"]: w for w in F.SIGNAL_WEAKNESSES}
    assert by_id["W5"]["status_7f"] == "addressed"
    assert by_id["W2"]["status_7f"] == "addressed"


# --------------------------------------------------------------------------- #
# Gate matrix + recommendation logic.
# --------------------------------------------------------------------------- #
def _sub_results_stub(approved_alpha=6):
    """Minimal sub_results so the gate builder can count approved alpha sub-factors."""
    res = {}
    n = 0
    for sf in list(F.SUBFACTORS) + [F.TTM_SUBFACTOR]:
        approved = sf.role == "alpha" and n < approved_alpha
        if approved:
            n += 1
        res[sf.key] = {"sf": sf, "graded": {"n_periods": 30, "mean_rank_ic": 0.0, "ic_t_stat": 1.0},
                       "coverage": 0.5, "approved": approved}
    return res


def test_gate_matrix_upgraded_passes():
    sf_res = _sub_results_stub()
    gates = F._build_gate_matrix({"strictly_forward": True, "n_violations": 0}, True,
                                 incremental_ic=0.02, upgraded_ic=0.018,
                                 approved_buckets=["momentum", "value", "quality", "growth"],
                                 up_comp_graded={"ic_t_stat": 1.2}, sub_results=sf_res,
                                 sector_pit=False, ttm_months=0)
    by = {g["gate_name"]: g for g in gates}
    assert by["improvement_gate"]["status"] == "PASS"
    assert by["nonnegative_ic_gate"]["status"] == "PASS"
    assert by["low_vol_excluded_from_alpha_gate"]["status"] == "PASS"
    assert by["no_sign_flipping_gate"]["status"] == "PASS"
    assert all(g["status"] == "PASS" for g in gates if g["gate_name"].startswith("no_"))


def test_recommendation_upgraded_vs_weak_vs_needsdata_vs_review():
    sf_res = _sub_results_stub()
    base = dict(approved_buckets=["momentum", "value", "quality", "growth"],
                placebo_collapsed=True, strictly_forward={"strictly_forward": True}, sub_results=sf_res)

    def rec(inc, up):
        gates = F._build_gate_matrix({"strictly_forward": True, "n_violations": 0}, True, inc, up,
                                     base["approved_buckets"], {"ic_t_stat": 1.0}, sf_res, False, 0)
        return F._derive_recommendation(gates, base["approved_buckets"], up, inc,
                                        base["placebo_collapsed"], base["strictly_forward"], sf_res)

    assert rec(0.02, 0.018) == F.REC_UPGRADED          # clears gate + non-negative
    assert rec(0.002, 0.004) == F.REC_WEAK             # non-negative but below gate
    assert rec(0.001, -0.003) == F.REC_NEEDS_DATA      # still negative -> data foundation
    # Placebo failure forces review regardless of IC.
    gates = F._build_gate_matrix({"strictly_forward": True, "n_violations": 0}, False, 0.02, 0.018,
                                 base["approved_buckets"], {"ic_t_stat": 1.0}, sf_res, False, 0)
    assert F._derive_recommendation(gates, base["approved_buckets"], 0.018, 0.02,
                                    False, {"strictly_forward": True}, sf_res) == F.REC_NEEDS_REVIEW


def test_recommendation_needsdata_when_too_few_buckets():
    sf_res = _sub_results_stub()
    gates = F._build_gate_matrix({"strictly_forward": True, "n_violations": 0}, True, 0.02, 0.018,
                                 ["value"], {"ic_t_stat": 1.0}, sf_res, False, 0)
    assert F._derive_recommendation(gates, ["value"], 0.018, 0.02, True,
                                    {"strictly_forward": True}, sf_res) == F.REC_NEEDS_DATA


# --------------------------------------------------------------------------- #
# Safety flags.
# --------------------------------------------------------------------------- #
def test_safety_flags_all_false_except_preview():
    flags = F._safety_flags()
    assert flags["preview_only"] is True
    for k, v in flags.items():
        if k == "preview_only":
            continue
        assert v is False, k
    # The forbidden-action flags exist and are off.
    for k in ("orders_enabled", "automation_enabled", "factor_weights_optimized",
              "factor_signs_flipped", "regimes_used_to_select_or_weight", "live_data_used",
              "paper_trader_touched", "gcp_touched", "d_drive_written"):
        assert flags[k] is False


# --------------------------------------------------------------------------- #
# Guarded end-to-end test against the real local data.
# --------------------------------------------------------------------------- #
_HAVE_DATA = Path(F.PRICE_CSV).exists() and Path(F.FUNDAMENTALS_CSV).exists()


@pytest.mark.skipif(not _HAVE_DATA, reason="local price / fundamentals panel not present")
def test_end_to_end_real_data(tmp_path):
    report = F.run(out_dir=str(tmp_path))
    assert report["recommendation"] in F.ALLOWED_RECOMMENDATIONS

    # All nine committed-safe artifacts exist.
    for name in ("phase7f_signal_reliability_upgrade.json", "signal_weakness_inventory.csv",
                 "data_quality_inventory.csv", "upgraded_factor_catalog.csv",
                 "upgraded_factor_scoreboard.csv", "upgraded_composite_scoreboard.csv",
                 "regime_diagnostic_scoreboard.csv", "signal_reliability_gate_matrix.csv",
                 "phase7g_next_plan.json"):
        assert (tmp_path / name).exists(), name

    # Main JSON parses and is internally consistent.
    d = json.loads((tmp_path / "phase7f_signal_reliability_upgrade.json").read_text())
    assert d["universe"]["same_universe_as_phase7c"] is True

    # Improvement arithmetic is consistent.
    old = d["old_baseline_ic"]; up = d["upgraded_composite_ic"]; inc = d["incremental_ic_vs_old_baseline"]
    assert up - old == pytest.approx(inc, abs=1e-6)
    att = d["improvement_attribution"]
    assert (att["lowvol_exclusion_effect"] + att["fundamental_spec_upgrade_effect"]
            == pytest.approx(inc, abs=1e-6))

    # Leakage discipline.
    assert d["leakage_checks"]["strictly_forward_labels"]["strictly_forward"] is True
    assert d["leakage_checks"]["strictly_forward_labels"]["n_violations"] == 0
    assert d["leakage_checks"]["placebo_collapsed"] is True

    # Low-volatility is excluded from the alpha composite; no bucket lists it.
    assert d["low_volatility_treatment"]["role"] == "risk_descriptor"
    for members in d["bucket_members"].values():
        assert "low_volatility" not in members

    # Regimes are reported but never used to select / weight factors.
    assert d["regime_diagnostics"]["available"] is True
    assert d["regimes_used_to_select_or_weight"] is False
    assert d["factor_weights_optimized"] is False
    assert d["factor_signs_flipped"] is False

    # Every safety flag is off (except preview_only).
    for k in ("orders_enabled", "broker_execution_enabled", "automation_enabled",
              "live_data_used", "paid_apis_used", "deployed", "production_model_built",
              "paper_trader_touched", "gcp_touched", "d_drive_written"):
        assert d[k] is False
    assert d["preview_only"] is True

    # No gate FAILs.
    assert d["gate_summary"]["counts"]["FAIL"] == 0


@pytest.mark.skipif(not _HAVE_DATA, reason="local price / fundamentals panel not present")
def test_same_universe_baseline_matches_phase7c(tmp_path):
    """The reconstructed Phase 7-C composite (old baseline) must reproduce the Phase 7-C
    engine's published composite IC on the same universe (within rounding)."""
    from research import run_phase7c_multifactor_ranking_engine as C
    c_report = C.run(out_dir=str(tmp_path / "c"))
    f_report = F.run(out_dir=str(tmp_path / "f"))
    c_ic = c_report["equal_weight_composite"]["mean_rank_ic"]
    f_old = f_report["old_baseline_ic"]
    assert f_old == pytest.approx(c_ic, abs=1e-6)
