"""Phase 29C deterministic feature evaluation.

Three layers, all pure functions over already-loaded owned data:

1. STANDALONE FEATURE DIAGNOSTIC — monthly cross-sectional rank-IC battery
   for one executed feature series (Part E, experiment 1). Statistical
   primitives are REUSED from research.run_phase22 (_spearman, _t_stat,
   _newey_west_t) exactly as family_backtest reuses them; regimes reuse
   family_backtest._regime_masks_pit. The target ``fwd_1m`` is joined HERE,
   only after the feature is fully formed, and only on formation months.

2. CHEAP IC SCREEN (Part F) — deterministic gate before any portfolio
   backtest. Positive in-sample IC alone never advances a feature: the
   screen also requires coverage, one-month robustness, subperiod breadth,
   non-duplication versus the existing sources/baseline, sane sector
   concentration of high ranks, and a material or complementary improvement
   versus the committed baseline signal.

3. BOUNDED BASELINE INTEGRATION + ROBUSTNESS (Parts E/G/I) — ONE configured
   deterministic integration (default 80% committed baseline score, 20%
   candidate feature rank; the LLM can never choose the weight) evaluated
   through the exact ported Phase 29A monthly simulation loop and the
   REUSED family_backtest.compute_experiment_metrics battery (cost ladder,
   turnover, drawdown, sector, regime, subperiod, rank-IC). With feature
   weight 0 the integrated engine reproduces the committed baseline
   simulation EXACTLY — proven, not assumed. Gates/deltas/decisions reuse
   evaluator.py unchanged: the strict shadow standard is never lowered here.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

import numpy as np

from research import run_phase22_autonomous_high_conviction_alpha_discovery as P

from . import evaluator as ev
from . import family_backtest as fb

FEATURE_EVALUATION_SCHEMA_VERSION = "29C.1"

_EPS = 1e-12

# IC-screen outcomes (Part F). REJECTED_DSL / REJECTED_PIT are produced when
# the upstream stage evidence says so; the screen itself covers the rest.
SCREEN_OUTCOMES = (
    "REJECTED_DSL",
    "REJECTED_PIT",
    "REJECTED_COVERAGE",
    "REJECTED_NO_INCREMENTAL_SIGNAL",
    "INCONCLUSIVE",
    "ADVANCE_TO_PORTFOLIO_SCREEN",
)

# Screen thresholds. Values with a validated project source are marked so;
# the rest are provisional and live in the campaign config (which may only
# tighten them — config validation refuses lowering).
DEFAULT_SCREEN_THRESHOLDS = {
    # controller floor: <36 fund-era months blocks a 29A campaign
    "min_months": {"value": 36, "provisional": False},
    # evaluator.DEFAULT_THRESHOLDS min_coverage_fraction
    "min_coverage_fraction": {"value": 0.60, "provisional": True},
    "min_abs_rank_ic_t": {"value": 1.0, "provisional": True},
    # evaluator delta tolerance for rank_ic_t (materiality band)
    "material_ic_t_margin": {"value": 0.25, "provisional": True},
    "near_duplicate_abs_corr": {"value": 0.95, "provisional": True},
    "max_complementary_abs_baseline_corr": {"value": 0.50, "provisional": True},
    "max_top_rank_sector_share": {"value": 0.50, "provisional": True},
    "leakage_suspicion_abs_ic": {"value": 0.50, "provisional": True},
    "min_universe": {"value": 10, "provisional": True},
}

FEATURE_WEIGHT_CEILING = 0.30  # hard bound on the candidate integration weight


class FeatureEvaluationError(RuntimeError):
    pass


def resolve_screen_thresholds(
    overrides: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    resolved = {k: dict(v) for k, v in DEFAULT_SCREEN_THRESHOLDS.items()}
    for k, v in (overrides or {}).items():
        if k in resolved:
            resolved[k] = dict(resolved[k], value=v, overridden=True)
    return {k: resolved[k] for k in sorted(resolved)}


def _finite(x: Any) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def _mean(xs: List[float]) -> Optional[float]:
    return sum(xs) / len(xs) if xs else None


def _std(xs: List[float]) -> Optional[float]:
    n = len(xs)
    if n < 2:
        return None
    mu = _mean(xs)
    return math.sqrt(sum((x - mu) ** 2 for x in xs) / (n - 1))


def _spearman(a: List[float], b: List[float]) -> Optional[float]:
    v = P._spearman(a, b)
    return float(v) if _finite(v) else None


# --------------------------------------------------------------------------- #
# 1) standalone feature diagnostic (Part E, experiment 1)
# --------------------------------------------------------------------------- #
def compute_feature_diagnostics(
    feature_series: Dict[str, Dict[str, float]],
    inputs: Dict[str, Any],
    *,
    feature_id: str,
    min_universe: int = 10,
) -> Dict[str, Any]:
    """Deterministic monthly rank-IC battery for one feature series.

    The universe each month is the eligible momentum-panel cross-section
    with a realized forward return; the feature must supply a finite value.
    Correlations with the existing baseline components and the committed
    50/50 aggregate score are computed on the fund-and-momentum common
    subset, so "new" information is measured against what the baseline
    already knows.
    """
    mom = inputs["mom_monthly"]
    fund_cf = inputs["fund_cf"]
    months = inputs["months"]

    ic_rows: List[Dict[str, Any]] = []
    eligible_months = 0
    xsec_cov: List[float] = []

    for m in months:
        mrow = mom.get(m, {})
        elig = sorted(
            tk for tk, r in mrow.items()
            if r.get("eligible") and _finite(r.get("mom_6_1"))
            and _finite(r.get("fwd_1m"))
        )
        if len(elig) < min_universe:
            continue
        eligible_months += 1
        frow = feature_series.get(m, {})
        uni = [tk for tk in elig if _finite(frow.get(tk))]
        xsec_cov.append(len(uni) / len(elig))
        if len(uni) < min_universe:
            continue

        fvals = [frow[tk] for tk in uni]
        fwd = [mrow[tk]["fwd_1m"] for tk in uni]
        momv = [mrow[tk]["mom_6_1"] for tk in uni]
        ic = _spearman(fvals, fwd)
        if ic is None:
            continue

        k = max(1, len(uni) // 4)
        order = sorted(uni, key=lambda tk: (-frow[tk], tk))
        top, bottom = order[:k], order[-k:]
        spread = (
            sum(mrow[tk]["fwd_1m"] for tk in top) / k
            - sum(mrow[tk]["fwd_1m"] for tk in bottom) / k
        )
        sec_counts: Dict[str, int] = {}
        for tk in top:
            fsec = ((fund_cf.get(m) or {}).get(tk) or {}).get("sector")
            sec = fsec if fsec and fsec != "Unknown" else mrow[tk].get("sector", "Unknown")
            sec_counts[sec] = sec_counts.get(sec, 0) + 1
        top_sector_share = max(sec_counts.values()) / k if sec_counts else None

        corr_mom = _spearman(fvals, momv)
        corr_fund = None
        corr_base = None
        common = [tk for tk in uni if (fund_cf.get(m) or {}).get(tk)]
        if len(common) >= min_universe:
            fnd = {tk: fund_cf[m][tk]["composite_sn"] for tk in common}
            mm = {tk: mrow[tk]["mom_6_1"] for tk in common}
            fp = fb._rank_desc_pct(fnd)
            mp = fb._rank_desc_pct(mm)
            base = [0.5 * fp[tk] + 0.5 * mp[tk] for tk in common]
            fsub = [frow[tk] for tk in common]
            corr_fund = _spearman(fsub, [fnd[tk] for tk in common])
            corr_base = _spearman(fsub, base)

        ic_rows.append({
            "month": m,
            "rank_ic": ic,
            "n_universe": len(uni),
            "top_minus_bottom_spread": spread,
            "top_rank_max_sector_share": top_sector_share,
            "corr_mom_6_1": corr_mom,
            "corr_composite_sn": corr_fund,
            "corr_baseline_score": corr_base,
        })

    ics = [r["rank_ic"] for r in ic_rows]
    ic_months = [r["month"] for r in ic_rows]
    ic_mean = _mean(ics)
    ic_std = _std(ics)
    ic_t = P._t_stat(ics) if len(ics) > 2 else None
    ic_t = float(ic_t) if ic_t is not None and _finite(ic_t) else None
    nw = P._newey_west_t(ics, 0) if len(ics) > 2 else None
    nw = float(nw) if nw is not None and _finite(nw) else None
    orientation = 1 if (ic_mean or 0.0) >= 0 else -1

    ex_best_mean = ex_best_t = None
    if len(ics) > 3:
        drop_idx = max(range(len(ics)), key=lambda i: orientation * ics[i])
        rest = [v for i, v in enumerate(ics) if i != drop_idx]
        ex_best_mean = _mean(rest)
        t = P._t_stat(rest)
        ex_best_t = float(t) if _finite(t) else None

    n = len(ics)
    third = max(1, n // 3)
    seg_means = [
        _mean(ics[a:b])
        for a, b in ((0, third), (third, 2 * third), (2 * third, n))
        if ics[a:b]
    ]

    masks = fb._regime_masks_pit(ic_months, inputs.get("spy_close", {}))
    regime_ic = {
        name: _mean([v for v, keep in zip(ics, mask) if keep])
        for name, mask in masks.items()
    }

    def _agg(key: str) -> Optional[float]:
        vals = [r[key] for r in ic_rows if r.get(key) is not None]
        return _mean(vals)

    return {
        "schema_version": FEATURE_EVALUATION_SCHEMA_VERSION,
        "record_type": "FEATURE_DIAGNOSTIC",
        "feature_id": feature_id,
        "months_evaluated": n,
        "first_month": ic_months[0] if ic_months else None,
        "last_month": ic_months[-1] if ic_months else None,
        "eligible_months": eligible_months,
        "month_coverage": (n / eligible_months) if eligible_months else None,
        "cross_sectional_coverage": _mean(xsec_cov),
        "rank_ic_mean": ic_mean,
        "rank_ic_std": ic_std,
        "rank_ic_t": ic_t,
        "rank_ic_nw_t": nw,
        "rank_ic_ir": (ic_mean / ic_std) if ic_mean is not None and ic_std else None,
        "positive_month_fraction": (
            sum(1 for v in ics if v > 0) / n if n else None
        ),
        "orientation": orientation,
        "rank_ic_mean_ex_best_month": ex_best_mean,
        "rank_ic_t_ex_best_month": ex_best_t,
        "subperiod_ic_means": seg_means,
        "regime_ic_means": regime_ic,
        "top_minus_bottom_spread_mean": _agg("top_minus_bottom_spread"),
        "avg_top_rank_sector_share": _agg("top_rank_max_sector_share"),
        "max_top_rank_sector_share": max(
            (r["top_rank_max_sector_share"] for r in ic_rows
             if r.get("top_rank_max_sector_share") is not None),
            default=None,
        ),
        "corr_with_sources": {
            "mom_6_1": _agg("corr_mom_6_1"),
            "composite_sn": _agg("corr_composite_sn"),
        },
        "corr_with_baseline_score": _agg("corr_baseline_score"),
        "monthly_rows": ic_rows,
    }


# --------------------------------------------------------------------------- #
# 2) cheap IC screen (Part F)
# --------------------------------------------------------------------------- #
def run_ic_screen(
    diagnostics: Dict[str, Any],
    *,
    baseline_metrics: Optional[Dict[str, Any]],
    thresholds: Optional[Dict[str, Any]] = None,
    dsl_ok: bool = True,
    pit_ok: bool = True,
) -> Dict[str, Any]:
    """Deterministic screen; a feature never advances on positive IC alone."""
    th = resolve_screen_thresholds(thresholds)

    def _t(name: str) -> float:
        return float(th[name]["value"])

    checks: List[Dict[str, Any]] = []
    reasons: List[str] = []

    def _done(outcome: str) -> Dict[str, Any]:
        return {
            "schema_version": FEATURE_EVALUATION_SCHEMA_VERSION,
            "record_type": "IC_SCREEN",
            "feature_id": diagnostics.get("feature_id"),
            "outcome": outcome,
            "reasons": reasons,
            "checks": checks,
            "thresholds": th,
        }

    if not dsl_ok:
        reasons.append("feature DSL validation failed upstream")
        return _done("REJECTED_DSL")
    if not pit_ok:
        reasons.append("point-in-time audit failed upstream")
        return _done("REJECTED_PIT")

    n = diagnostics.get("months_evaluated") or 0
    checks.append({"check": "min_months", "value": n,
                   "threshold": _t("min_months"), "passed": n >= _t("min_months")})
    mc = diagnostics.get("month_coverage")
    xc = diagnostics.get("cross_sectional_coverage")
    cov_ok = (
        mc is not None and xc is not None
        and mc >= _t("min_coverage_fraction")
        and xc >= _t("min_coverage_fraction")
    )
    checks.append({"check": "coverage", "value": {"month": mc, "cross_section": xc},
                   "threshold": _t("min_coverage_fraction"), "passed": cov_ok})
    if n < _t("min_months") or not cov_ok:
        reasons.append("insufficient evaluable months or coverage")
        return _done("REJECTED_COVERAGE")

    ic_mean = diagnostics.get("rank_ic_mean")
    ic_t = diagnostics.get("rank_ic_t")
    finite_ok = _finite(ic_mean) and _finite(ic_t)
    checks.append({"check": "rank_ic_finite", "value": {"mean": ic_mean, "t": ic_t},
                   "passed": finite_ok})
    if not finite_ok:
        reasons.append("rank-IC result is not finite")
        return _done("REJECTED_NO_INCREMENTAL_SIGNAL")

    if abs(ic_mean) > _t("leakage_suspicion_abs_ic"):
        checks.append({"check": "leakage_suspicion", "value": ic_mean,
                       "threshold": _t("leakage_suspicion_abs_ic"), "passed": False})
        reasons.append(
            "implausibly high target correlation (|IC mean| %.3f) — treated "
            "as leakage until proven otherwise" % abs(ic_mean))
        return _done("REJECTED_PIT")

    src_corrs = diagnostics.get("corr_with_sources") or {}
    base_corr = diagnostics.get("corr_with_baseline_score")
    dupes = sorted(
        name for name, c in src_corrs.items()
        if c is not None and abs(c) >= _t("near_duplicate_abs_corr")
    )
    if base_corr is not None and abs(base_corr) >= _t("near_duplicate_abs_corr"):
        dupes.append("baseline_score")
    checks.append({"check": "not_effectively_identical", "value": dupes,
                   "threshold": _t("near_duplicate_abs_corr"), "passed": not dupes})
    if dupes:
        reasons.append(
            "effectively identical to existing signal(s): %s" % ", ".join(dupes))
        return _done("REJECTED_NO_INCREMENTAL_SIGNAL")

    orientation = diagnostics.get("orientation") or 1
    ex_best = diagnostics.get("rank_ic_mean_ex_best_month")
    one_month_ok = ex_best is not None and orientation * ex_best > 0
    checks.append({"check": "not_one_month_driven", "value": ex_best,
                   "passed": one_month_ok})
    if not one_month_ok:
        reasons.append("IC evidence disappears when the best month is removed")
        return _done("REJECTED_NO_INCREMENTAL_SIGNAL")

    segs = [s for s in (diagnostics.get("subperiod_ic_means") or []) if s is not None]
    pos_segs = sum(1 for s in segs if orientation * s > 0)
    seg_ok = len(segs) >= 2 and pos_segs >= 2
    checks.append({"check": "subperiod_breadth",
                   "value": {"positive": pos_segs, "evaluable": len(segs)},
                   "threshold": ">= 2 positive", "passed": seg_ok})
    if not seg_ok:
        reasons.append("single-subperiod dependence (Phase 10-N lesson)")
        return _done("REJECTED_NO_INCREMENTAL_SIGNAL")

    sec = diagnostics.get("avg_top_rank_sector_share")
    sec_ok = sec is None or sec <= _t("max_top_rank_sector_share")
    checks.append({"check": "sector_rank_concentration", "value": sec,
                   "threshold": _t("max_top_rank_sector_share"), "passed": sec_ok})
    if not sec_ok:
        reasons.append("severe sector concentration of high feature ranks")
        return _done("INCONCLUSIVE")

    abs_t = abs(ic_t)
    baseline_t = (baseline_metrics or {}).get("rank_ic_t")
    strength_ok = abs_t >= _t("min_abs_rank_ic_t")
    material_vs_baseline = (
        baseline_t is None
        or abs_t >= abs(baseline_t) + _t("material_ic_t_margin")
        or (base_corr is not None
            and abs(base_corr) <= _t("max_complementary_abs_baseline_corr"))
    )
    checks.append({
        "check": "material_or_complementary_vs_baseline",
        "value": {"abs_rank_ic_t": abs_t, "baseline_rank_ic_t": baseline_t,
                  "corr_with_baseline": base_corr},
        "passed": bool(strength_ok and material_vs_baseline),
    })
    if strength_ok and material_vs_baseline:
        reasons.append(
            "material or complementary rank-IC evidence versus the committed "
            "baseline; advancing to ONE bounded portfolio integration")
        return _done("ADVANCE_TO_PORTFOLIO_SCREEN")
    reasons.append(
        "positive in-sample IC alone is not enough: no material or "
        "complementary improvement versus the committed baseline")
    return _done("INCONCLUSIVE")


# --------------------------------------------------------------------------- #
# 3) bounded baseline integration (Parts E/G)
# --------------------------------------------------------------------------- #
def run_integrated_experiment(
    inputs: Dict[str, Any],
    feature_series: Dict[str, Dict[str, float]],
    params: Dict[str, Any],
) -> Dict[str, Any]:
    """Deterministic monthly simulation of ONE configured integration.

    Score = baseline_weight * (the committed 50/50 fundamental/momentum
    rank-percentile blend) + feature_weight * (descending percentile of the
    oriented feature). Everything else — universe, greedy 25% sector cap,
    exit-buffer hysteresis, turnover, per-period bookkeeping — is ported
    VERBATIM from family_backtest.run_family_experiment; with
    feature_weight == 0 the output is exactly the committed baseline
    simulation. Tickers without a feature value keep the identical universe
    and receive the neutral 0.5 feature percentile (fill counts recorded).
    """
    w_base = float(params["baseline_weight"])
    w_feat = float(params["feature_weight"])
    if abs(w_base + w_feat - 1.0) > 1e-9:
        raise FeatureEvaluationError(
            "integration weights must reconcile to exactly one "
            "(baseline %.4f + feature %.4f)" % (w_base, w_feat))
    if not (0.0 <= w_feat <= FEATURE_WEIGHT_CEILING):
        raise FeatureEvaluationError(
            "feature weight %.4f outside the bounded range [0, %.2f]"
            % (w_feat, FEATURE_WEIGHT_CEILING))
    orientation = int(params.get("feature_orientation", 1))
    if orientation not in (-1, 1):
        raise FeatureEvaluationError("feature_orientation must be +1 or -1")

    wf = float(fb.BASELINE_PARAMS["fundamental_weight"])
    wm = float(fb.BASELINE_PARAMS["momentum_weight"])
    size = int(params.get("top_n", fb.BASELINE_PARAMS["top_n"]))
    sector_treatment = params.get(
        "sector_treatment", fb.BASELINE_PARAMS["sector_treatment"])
    buffer_f = float(params.get("exit_buffer_fraction", 0.0))
    universe = params.get("universe", "mhz_reconstruction")
    min_adv = float(params.get("min_adv_dollar", fb.MIN_ADV_DOLLAR_DEFAULT))

    fund_cf = inputs["fund_cf"]
    mom_monthly = inputs["mom_monthly"]

    periods: List[dict] = []
    skipped: List[dict] = []
    fill_counts: List[int] = []
    prev_set: Optional[set] = None
    for m in inputs["months"]:
        fbk = fund_cf.get(m, {})
        mb = mom_monthly.get(m, {})
        common = [
            tk for tk in fbk
            if tk in mb
            and mb[tk].get("eligible")
            and mb[tk].get("mom_6_1") is not None
            and mb[tk].get("fwd_1m") is not None
        ]
        if universe == "mhz_live_eligibility":
            common = [
                tk for tk in common
                if mb[tk].get("is_member")
                and (mb[tk].get("adv_dollar") is None
                     or mb[tk]["adv_dollar"] >= min_adv)
            ]
        if len(common) < size:
            if fbk:
                skipped.append({"month": m, "n_common": len(common)})
            prev_set = None
            continue
        fvals = {tk: fbk[tk]["composite_sn"] for tk in common}
        mvals = {tk: mb[tk]["mom_6_1"] for tk in common}
        fp = fb._rank_desc_pct(fvals)
        mp = fb._rank_desc_pct(mvals)
        if w_feat > 0:
            frow = feature_series.get(m, {})
            having = {
                tk: orientation * frow[tk]
                for tk in common
                if _finite(frow.get(tk))
            }
            featp_known = fb._rank_desc_pct(having) if having else {}
            featp = {tk: featp_known.get(tk, 0.5) for tk in common}
            fill_counts.append(len(common) - len(having))
        else:
            featp = None
        scored = []
        for tk in common:
            base_score = wf * fp[tk] + wm * mp[tk]
            score = (
                base_score if featp is None
                else w_base * base_score + w_feat * featp[tk]
            )
            scored.append({
                "ticker": tk,
                "score": score,
                "fwd": mb[tk]["fwd_1m"],
                "sector": fbk[tk]["sector"] if fbk[tk]["sector"] != "Unknown"
                else mb[tk]["sector"],
            })
        if sector_treatment == "sector_neutral":
            fb._demean_by_sector(scored)
        scored.sort(key=lambda r: (-r["score"], r["ticker"]))

        picked = fb._select_book(
            scored, size,
            sector_treatment=sector_treatment,
            exit_buffer_fraction=buffer_f,
            prev_set=prev_set,
        )
        if len(picked) < size:
            skipped.append({"month": m, "n_common": len(common),
                            "underfilled": len(picked)})
            prev_set = None
            continue

        gross = sum(r["fwd"] for r in picked) / len(picked)
        cur_set = {r["ticker"] for r in picked}
        established = prev_set is None
        turnover = 1.0 if established else len(cur_set - prev_set) / len(cur_set)

        w = 1.0 / len(picked)
        sec_w: Dict[str, float] = {}
        for r in picked:
            sec_w[r["sector"]] = sec_w.get(r["sector"], 0.0) + w

        scores_arr = np.array([r["score"] for r in scored], dtype=float)
        fwd_arr = np.array([r["fwd"] for r in scored], dtype=float)
        ic = P._spearman(scores_arr, fwd_arr) if len(scored) >= 10 else None

        bottom = scored[-size:]
        spread = gross - (sum(r["fwd"] for r in bottom) / len(bottom))

        periods.append({
            "month": m,
            "gross": gross,
            "turnover": turnover,
            "established": established,
            "n": len(picked),
            "n_common": len(common),
            "constituents": sorted(cur_set),
            "sector_weights": {k: round(v, 6) for k, v in sec_w.items()},
            "max_sector_weight": round(max(sec_w.values()), 6) if sec_w else None,
            "rank_ic": ic,
            "top_minus_bottom_spread": spread,
        })
        prev_set = cur_set

    sim = {
        "params": dict(params),
        "periods": periods,
        "skipped_months": skipped,
        "n_periods": len(periods),
        "first_month": periods[0]["month"] if periods else None,
        "last_month": periods[-1]["month"] if periods else None,
    }
    if w_feat > 0:
        total_common = sum(p["n_common"] for p in periods) or 1
        sim["feature_fill"] = {
            "months_with_fill": sum(1 for c in fill_counts if c > 0),
            "filled_values": sum(fill_counts),
            "fill_fraction": sum(fill_counts) / total_common,
            "policy": "missing feature values receive the neutral 0.5 "
            "percentile so the universe stays identical to the baseline",
        }
    return sim


def verify_baseline_reproduction_via_integration(
    inputs: Dict[str, Any],
) -> Dict[str, Any]:
    """Prove the integrated engine collapses to the committed baseline.

    Runs the integration with feature weight 0 and compares period-by-period
    against family_backtest.run_family_experiment(BASELINE_PARAMS). Exact
    equality is required — this is the Part G precondition for evaluating
    any candidate.
    """
    from .artifact_store import content_hash

    base_sim = fb.run_family_experiment(inputs, fb.BASELINE_PARAMS)
    integ_sim = run_integrated_experiment(
        inputs, {}, {
            "baseline_weight": 1.0,
            "feature_weight": 0.0,
            "feature_orientation": 1,
            "top_n": fb.BASELINE_PARAMS["top_n"],
            "sector_treatment": fb.BASELINE_PARAMS["sector_treatment"],
            "exit_buffer_fraction": fb.BASELINE_PARAMS["exit_buffer_fraction"],
            "universe": fb.BASELINE_PARAMS["universe"],
            "min_adv_dollar": fb.BASELINE_PARAMS["min_adv_dollar"],
        },
    )
    identical = base_sim["periods"] == integ_sim["periods"]
    return {
        "reproduced": identical,
        "n_periods": base_sim["n_periods"],
        "baseline_periods_hash": content_hash(base_sim["periods"]),
        "integrated_periods_hash": content_hash(integ_sim["periods"]),
    }


# --------------------------------------------------------------------------- #
# robustness battery for retained candidates (Part I)
# --------------------------------------------------------------------------- #
def run_feature_robustness(
    inputs: Dict[str, Any],
    feature_series: Dict[str, Dict[str, float]],
    params: Dict[str, Any],
    *,
    primary_cost_bps_per_side: float,
    baseline_metrics: Dict[str, Any],
    diagnostics: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Reused deterministic robustness battery over the integrated candidate.

    Metrics come from family_backtest.compute_experiment_metrics (cost
    ladder, turnover, drawdown, sector, regime, subperiod, rank-IC);
    universe sensitivity reruns the SAME integration under the live
    eligibility/ADV views; the final decision reuses
    evaluator.decide_candidate(stage='robustness') with the DEFAULT strict
    thresholds — nothing here can lower a gate, and zero survivors is a
    valid outcome.
    """
    sim = run_integrated_experiment(inputs, feature_series, params)
    metrics = fb.compute_experiment_metrics(
        sim, inputs, primary_cost_bps_per_side=primary_cost_bps_per_side)
    metrics["pit_integrity_ok"] = True  # robustness runs only after PIT passed

    by_cost = metrics.get("net_excess_ann_by_cost_bps") or {}
    cost_view = {
        "net_excess_ann_by_cost_bps": by_cost,
        "cost_slope_12p5_to_50": metrics.get("cost_slope_12p5_to_50"),
        "survives_25bps": (by_cost.get("25.0") or 0) > 0
        if by_cost.get("25.0") is not None else None,
        "survives_50bps": (by_cost.get("50.0") or 0) > 0
        if by_cost.get("50.0") is not None else None,
        "collapses_under_costs": (
            (by_cost.get("12.5") or 0) > 0 and (by_cost.get("50.0") or 0) <= 0
            if by_cost.get("12.5") is not None and by_cost.get("50.0") is not None
            else None
        ),
    }

    agg: Dict[str, List[float]] = {}
    for p in sim["periods"]:
        for sec, wv in p["sector_weights"].items():
            agg.setdefault(sec, []).append(wv)
    n_p = max(1, len(sim["periods"]))
    sector_view = {
        "mean_sector_weights": {
            sec: round(sum(ws) / n_p, 6) for sec, ws in sorted(agg.items())
        },
        "peak_sector_weights": {
            sec: round(max(ws), 6) for sec, ws in sorted(agg.items())
        },
        "max_sector_weight": max((max(ws) for ws in agg.values()), default=None),
    }

    uni_views: Dict[str, Any] = {}
    view_specs = [("mhz_reconstruction", fb.MIN_ADV_DOLLAR_DEFAULT)]
    from .schemas import APPROVED_MIN_ADV_DOLLARS

    for adv in APPROVED_MIN_ADV_DOLLARS:
        view_specs.append(("mhz_live_eligibility", adv))
    for uni, adv in view_specs:
        p2 = dict(params, universe=uni, min_adv_dollar=adv)
        s2 = run_integrated_experiment(inputs, feature_series, p2)
        m2 = fb.compute_experiment_metrics(
            s2, inputs, primary_cost_bps_per_side=primary_cost_bps_per_side)
        uni_views["%s_adv%d" % (uni, int(adv))] = {
            "net_spy_excess_ann": m2.get("net_spy_excess_ann"),
            "rank_ic_t": m2.get("rank_ic_t"),
            "months": m2.get("months"),
        }
    uni_vals = [v["net_spy_excess_ann"] for v in uni_views.values()
                if v["net_spy_excess_ann"] is not None]
    universe_view = {
        "views": uni_views,
        "all_views_positive": bool(uni_vals) and all(v > 0 for v in uni_vals),
    }

    gross = [p["gross"] for p in sim["periods"]]
    turn = [p["turnover"] for p in sim["periods"]]
    dd_by_cost = {}
    for c in fb.COST_LADDER_BPS_PER_SIDE:
        rt = 2.0 * (c / 1e4)
        nav, equity = 1.0, []
        for g, t in zip(gross, turn):
            nav *= 1.0 + (g - rt * t)
            equity.append(nav)
        dd_by_cost[str(c)] = fb._max_drawdown(equity)

    gates = ev.evaluate_gates(metrics, baseline_metrics, thresholds=None)
    decision = ev.decide_candidate(gates, stage=ev.STAGE_ROBUSTNESS)
    score = ev.score_candidate(metrics, baseline_metrics, gates)
    weaknesses = [
        "gate not passed: %s" % g["gate"]
        for g in gates["gates"] if g["passed"] is not True
    ]
    if universe_view["all_views_positive"] is False:
        weaknesses.append("excess not positive under all universe views")

    return {
        "schema_version": FEATURE_EVALUATION_SCHEMA_VERSION,
        "record_type": "FEATURE_ROBUSTNESS",
        "metrics": metrics,
        "rank_ic_stability": {
            "rank_ic_mean": metrics.get("rank_ic_mean"),
            "rank_ic_t": metrics.get("rank_ic_t"),
            "rank_ic_nw_t": metrics.get("rank_ic_nw_t"),
            "rank_ic_ir": metrics.get("rank_ic_ir"),
        },
        "cost_sensitivity": cost_view,
        "turnover_analysis": {
            "turnover_monthly_oneside": metrics.get("turnover_monthly_oneside"),
            "turnover_including_establishment": metrics.get(
                "turnover_including_establishment"),
            "membership_stability": metrics.get("membership_stability"),
        },
        "sector_analysis": sector_view,
        "regime_analysis": {
            "regime_excess_ann": metrics.get("regime_excess_ann"),
            "regime_positive_fraction": metrics.get("regime_positive_fraction"),
        },
        "subperiod_stability": {
            "subperiod_excess_ann": metrics.get("subperiod_excess_ann"),
            "n_positive_subperiods": metrics.get("n_positive_subperiods"),
            "net_excess_ann_ex_best_subperiod": metrics.get(
                "net_excess_ann_ex_best_subperiod"),
            "pre2020_excess_ann": metrics.get("pre2020_excess_ann"),
            "post2020_excess_ann": metrics.get("post2020_excess_ann"),
        },
        "universe_sensitivity": universe_view,
        "drawdown_sensitivity": {"max_drawdown_by_cost_bps": dd_by_cost},
        "baseline_correlation": {
            "corr_with_baseline_score": (diagnostics or {}).get(
                "corr_with_baseline_score"),
            "corr_with_sources": (diagnostics or {}).get("corr_with_sources"),
        },
        "gate_results": gates,
        "decision": decision,
        "score": score,
        "weaknesses": weaknesses,
    }


__all__ = [
    "DEFAULT_SCREEN_THRESHOLDS",
    "FEATURE_EVALUATION_SCHEMA_VERSION",
    "FEATURE_WEIGHT_CEILING",
    "FeatureEvaluationError",
    "SCREEN_OUTCOMES",
    "compute_feature_diagnostics",
    "resolve_screen_thresholds",
    "run_feature_robustness",
    "run_ic_screen",
    "run_integrated_experiment",
    "verify_baseline_reproduction_via_integration",
]
