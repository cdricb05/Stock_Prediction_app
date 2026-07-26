"""Baseline-relative candidate evaluation: gates, decisions, transparent score.

Everything here consumes ALREADY-COMPUTED metric dictionaries produced by the
deterministic tools; the evaluator never touches market data itself and never
computes returns. Thresholds come from the campaign config; any threshold
without a formally validated project source is labeled provisional.

Candidate outcomes: REJECTED / INCONCLUSIVE / RETAIN_FOR_ROBUSTNESS /
SHADOW_ELIGIBLE. A candidate can never qualify on total return alone: the
SHADOW_ELIGIBLE decision structurally requires rank-IC, cost-robustness,
subperiod-stability and concentration gates in addition to net excess return.

Phase 29A.2 stage separation:

- PRIMARY RETENTION (stage="primary") decides whether a candidate deserves
  expensive robustness testing. Its blocking gates are exactly the HARD gates
  (PIT, coverage, 25 bps cost survival, sector concentration, subperiod
  stability). The provisional absolute thresholds (rank-IC t, turnover cap,
  regime fraction) are DIAGNOSTICS at this stage — recorded as
  ``diagnostic_flags``, never silently blocking. Retention additionally
  requires a persisted baseline-relative delta table showing either a material
  balanced improvement or a useful robustness trade-off; total return alone
  cannot retain (a materially unbalanced or severely degraded candidate stays
  INCONCLUSIVE).
- SHADOW ELIGIBILITY (stage="robustness") keeps the strict full standard:
  every hard gate plus rank-IC, 50 bps cost survival, turnover, regime
  stability and beating the baseline. A candidate can never become
  SHADOW_ELIGIBLE from the primary stage, and human approval remains required
  downstream regardless.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

REJECTED = "REJECTED"
INCONCLUSIVE = "INCONCLUSIVE"
RETAIN_FOR_ROBUSTNESS = "RETAIN_FOR_ROBUSTNESS"
SHADOW_ELIGIBLE = "SHADOW_ELIGIBLE"

DECISIONS = (REJECTED, INCONCLUSIVE, RETAIN_FOR_ROBUSTNESS, SHADOW_ELIGIBLE)

STAGE_PRIMARY = "primary"
STAGE_ROBUSTNESS = "robustness"

# Threshold defaults. "provisional" marks values without a formally validated
# project source; they stay configurable through the campaign config.
DEFAULT_THRESHOLDS: Dict[str, Dict[str, Any]] = {
    "min_coverage_fraction": {"value": 0.60, "provisional": True},
    "min_rank_ic_t": {"value": 2.0, "provisional": True},
    "max_turnover_monthly_oneside": {"value": 0.35, "provisional": True},
    # Existing operational sector cap (Phase 10-H rules): 25% per sector.
    "max_sector_weight": {"value": 0.25, "provisional": False},
    # Phase 10-C standard: results must survive 25 bps per side.
    "min_net_excess_at_25bps": {"value": 0.0, "provisional": False},
    # SHADOW_ELIGIBLE additionally requires survival at 50 bps per side.
    "min_net_excess_at_50bps": {"value": 0.0, "provisional": False},
    # Phase 10-N lesson: lift must not depend on a single narrow subperiod.
    "min_positive_subperiods": {"value": 2, "provisional": False},
    "min_regime_positive_fraction": {"value": 0.5, "provisional": True},
}

HARD_GATES = (
    "point_in_time_integrity",
    "coverage",
    "cost_robustness_25bps",
    "sector_concentration",
    "subperiod_stability",
)

# Stage policy (Phase 29A.2). Primary retention blocks ONLY on the hard gates;
# the provisional absolute thresholds are diagnostics at that stage. The shadow
# standard additionally requires every strict evidence gate below.
PRIMARY_RETENTION_GATES = HARD_GATES
PRIMARY_DIAGNOSTIC_GATES = (
    "rank_ic",
    "turnover",
    "regime_stability",
    "cost_robustness_50bps",
    "beats_baseline_net_excess",
)
SHADOW_ELIGIBILITY_GATES = HARD_GATES + (
    "rank_ic",
    "cost_robustness_50bps",
    "turnover",
    "regime_stability",
    "beats_baseline_net_excess",
)


def stage_policy(stage: str) -> Dict[str, Any]:
    """The explicit, persisted primary-vs-shadow gate policy for one stage."""
    return {
        "stage": stage,
        "blocking_gates": list(
            PRIMARY_RETENTION_GATES if stage == STAGE_PRIMARY else SHADOW_ELIGIBILITY_GATES
        ),
        "diagnostic_gates": list(
            PRIMARY_DIAGNOSTIC_GATES if stage == STAGE_PRIMARY else ()
        ),
        "note": (
            "primary retention decides robustness-testing admission only; "
            "SHADOW_ELIGIBLE requires the full strict standard after robustness "
            "and can never be granted at the primary stage"
            if stage == STAGE_PRIMARY
            else "shadow eligibility: strict full standard; human approval still "
            "required and promotion beyond shadow is impossible here"
        ),
    }


# --------------------------------------------------------------------------- #
# Baseline-relative delta table (Phase 29A.2, Part F)
# --------------------------------------------------------------------------- #
# (metric name, source, direction: +1 higher-is-better / -1 lower-is-better,
#  default materiality tolerance). Tolerances are PROVISIONAL: they mark the
# band inside which a difference is treated as noise ("neutral"), and they stay
# configurable through config["evaluation"]["delta_tolerances"].
DELTA_METRICS = (
    ("net_spy_excess_ann", ("metric", "net_spy_excess_ann"), +1, 0.005),
    ("net_excess_ann_12p5bps", ("cost", 12.5), +1, 0.005),
    ("net_excess_ann_25bps", ("cost", 25.0), +1, 0.005),
    ("net_excess_ann_50bps", ("cost", 50.0), +1, 0.005),
    ("gross_return_ann", ("metric", "gross_return_ann"), +1, 0.005),
    ("max_drawdown", ("metric", "max_drawdown"), -1, 0.01),
    ("volatility_ann", ("metric", "volatility_ann"), -1, 0.01),
    ("turnover_monthly_oneside", ("metric", "turnover_monthly_oneside"), -1, 0.02),
    ("rank_ic_mean", ("metric", "rank_ic_mean"), +1, 0.002),
    ("rank_ic_t", ("metric", "rank_ic_t"), +1, 0.25),
    ("rank_ic_ir", ("metric", "rank_ic_ir"), +1, 0.02),
    ("subperiod_positive_fraction", ("metric", "subperiod_positive_fraction"), +1, 0.15),
    ("net_excess_ann_ex_best_subperiod", ("metric", "net_excess_ann_ex_best_subperiod"), +1, 0.005),
    ("regime_positive_fraction", ("metric", "regime_positive_fraction"), +1, 0.20),
    ("max_sector_weight", ("metric", "max_sector_weight"), -1, 0.02),
    ("membership_stability", ("metric", "membership_stability"), +1, 0.05),
    ("coverage_fraction", ("metric", "coverage_fraction"), +1, 0.05),
)

DEFAULT_DELTA_TOLERANCES = {name: tol for name, _src, _dirn, tol in DELTA_METRICS}

# A degradation this many times beyond its tolerance is "severe": it blocks
# primary retention outright regardless of how good the return improvement is.
SEVERE_DEGRADATION_MULTIPLIER = 5.0

# Risk/stability metrics whose material degradations count against "balance".
CORE_RISK_DELTA_METRICS = (
    "max_drawdown",
    "volatility_ann",
    "turnover_monthly_oneside",
    "rank_ic_mean",
    "rank_ic_t",
    "net_excess_ann_ex_best_subperiod",
    "regime_positive_fraction",
    "max_sector_weight",
    "coverage_fraction",
)

# Material improvements here (with return not materially worse) form a "useful
# robustness trade-off" — e.g. the 0.20 exit buffer trading a little return
# for materially lower turnover.
TRADE_OFF_IMPROVEMENT_METRICS = (
    "turnover_monthly_oneside",
    "max_drawdown",
    "volatility_ann",
    "max_sector_weight",
)

PRIMARY_RETURN_DELTA_METRIC = "net_spy_excess_ann"


def resolve_delta_tolerances(
    overrides: Optional[Dict[str, Any]] = None,
) -> Dict[str, float]:
    resolved = dict(DEFAULT_DELTA_TOLERANCES)
    for key, val in (overrides or {}).items():
        if key in resolved:
            resolved[key] = float(val)
    return resolved


def build_baseline_deltas(
    metrics: Dict[str, Any],
    baseline_metrics: Dict[str, Any],
    tolerances: Optional[Dict[str, Any]] = None,
    severe_multiplier: Optional[float] = None,
) -> Dict[str, Any]:
    """Explicit per-candidate baseline-relative delta table (persisted).

    Every metric row carries candidate value, baseline value, absolute delta,
    relative delta where meaningful, better/worse/neutral classification and
    whether the difference is material under the configured tolerance.
    """
    tol = resolve_delta_tolerances(tolerances)
    mult = float(severe_multiplier or SEVERE_DEGRADATION_MULTIPLIER)
    base = baseline_metrics or {}
    rows: Dict[str, Dict[str, Any]] = {}
    material_improvements: List[str] = []
    material_degradations: List[str] = []
    severe_degradations: List[str] = []

    for name, source, direction, _default in DELTA_METRICS:
        if source[0] == "cost":
            cand = _cost_lookup(metrics.get("net_excess_ann_by_cost_bps") or {}, source[1])
            bval = _cost_lookup(base.get("net_excess_ann_by_cost_bps") or {}, source[1])
        else:
            cand = metrics.get(source[1])
            bval = base.get(source[1])
        t = tol[name]
        row: Dict[str, Any] = {
            "candidate": cand,
            "baseline": bval,
            "direction": direction,
            "tolerance": t,
        }
        if cand is None or bval is None:
            row.update(
                delta_abs=None, delta_rel=None,
                classification="unavailable", material=False, severe=False,
            )
        else:
            delta_abs = float(cand) - float(bval)
            delta_rel = (delta_abs / abs(float(bval))) if abs(float(bval)) > 1e-12 else None
            signed = direction * delta_abs
            if abs(delta_abs) <= t:
                cls, material = "neutral", False
            elif signed > 0:
                cls, material = "better", True
            else:
                cls, material = "worse", True
            severe = bool(cls == "worse" and abs(delta_abs) > mult * t)
            row.update(
                delta_abs=delta_abs, delta_rel=delta_rel,
                classification=cls, material=material, severe=severe,
            )
            if material and cls == "better":
                material_improvements.append(name)
            if material and cls == "worse":
                material_degradations.append(name)
            if severe:
                severe_degradations.append(name)
        rows[name] = row

    return {
        "metrics": rows,
        "tolerances": tol,
        "severe_degradation_multiplier": mult,
        "material_improvements": material_improvements,
        "material_degradations": material_degradations,
        "severe_degradations": severe_degradations,
        "provisional": True,
        "note": (
            "tolerances are provisional materiality bands (configurable via "
            "config['evaluation']['delta_tolerances']); 'neutral' means the "
            "difference is inside the band and cannot justify retention or "
            "rejection on its own"
        ),
    }


def resolve_thresholds(
    overrides: Optional[Dict[str, Any]] = None,
) -> Dict[str, Dict[str, Any]]:
    resolved = {k: dict(v) for k, v in DEFAULT_THRESHOLDS.items()}
    for key, val in (overrides or {}).items():
        if key not in resolved:
            resolved[key] = {"value": val, "provisional": True}
        else:
            resolved[key] = {"value": val, "provisional": resolved[key]["provisional"]}
        resolved[key]["overridden"] = True
    return resolved


def _gate(name, passed, value, threshold, provisional, hard, note=""):
    return {
        "gate": name,
        "passed": bool(passed) if passed is not None else None,
        "value": value,
        "threshold": threshold,
        "provisional": bool(provisional),
        "hard": bool(hard),
        "note": note,
    }


def evaluate_gates(
    metrics: Dict[str, Any],
    baseline_metrics: Dict[str, Any],
    thresholds: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    th = resolve_thresholds(thresholds)
    gates: List[Dict[str, Any]] = []

    pit_ok = metrics.get("pit_integrity_ok")
    gates.append(
        _gate(
            "point_in_time_integrity",
            pit_ok,
            pit_ok,
            True,
            provisional=False,
            hard=True,
            note="fail-fast: a PIT/leakage failure blocks the whole campaign",
        )
    )

    cov = metrics.get("coverage_fraction")
    t = th["min_coverage_fraction"]
    gates.append(
        _gate(
            "coverage",
            None if cov is None else cov >= t["value"],
            cov,
            t["value"],
            t["provisional"],
            hard=True,
        )
    )

    ic_t = metrics.get("rank_ic_t")
    ic_mean = metrics.get("rank_ic_mean")
    t = th["min_rank_ic_t"]
    ic_pass = (
        None
        if ic_t is None or ic_mean is None
        else (ic_mean > 0 and ic_t >= t["value"])
    )
    gates.append(
        _gate(
            "rank_ic",
            ic_pass,
            {"rank_ic_mean": ic_mean, "rank_ic_t": ic_t},
            {"rank_ic_mean": "> 0", "rank_ic_t": t["value"]},
            t["provisional"],
            hard=False,
        )
    )

    by_cost = metrics.get("net_excess_ann_by_cost_bps") or {}
    t = th["min_net_excess_at_25bps"]
    v25 = _cost_lookup(by_cost, 25.0)
    gates.append(
        _gate(
            "cost_robustness_25bps",
            None if v25 is None else v25 > t["value"],
            v25,
            "> %s" % t["value"],
            t["provisional"],
            hard=True,
            note="Phase 10-C standard: must survive 25 bps per side",
        )
    )
    t = th["min_net_excess_at_50bps"]
    v50 = _cost_lookup(by_cost, 50.0)
    gates.append(
        _gate(
            "cost_robustness_50bps",
            None if v50 is None else v50 > t["value"],
            v50,
            "> %s" % t["value"],
            t["provisional"],
            hard=False,
            note="required for SHADOW_ELIGIBLE",
        )
    )

    to = metrics.get("turnover_monthly_oneside")
    t = th["max_turnover_monthly_oneside"]
    gates.append(
        _gate(
            "turnover",
            None if to is None else to <= t["value"],
            to,
            "<= %s" % t["value"],
            t["provisional"],
            hard=False,
        )
    )

    sec = metrics.get("max_sector_weight")
    t = th["max_sector_weight"]
    gates.append(
        _gate(
            "sector_concentration",
            None if sec is None else sec <= t["value"] + 1e-9,
            sec,
            "<= %s" % t["value"],
            t["provisional"],
            hard=True,
        )
    )

    n_pos = metrics.get("n_positive_subperiods")
    n_sub = metrics.get("n_subperiods")
    ex_best = metrics.get("net_excess_ann_ex_best_subperiod")
    t = th["min_positive_subperiods"]
    sub_pass = None
    if n_pos is not None and n_sub is not None:
        sub_pass = n_pos >= min(t["value"], n_sub)
        if ex_best is not None:
            sub_pass = bool(sub_pass and ex_best > 0)
    gates.append(
        _gate(
            "subperiod_stability",
            sub_pass,
            {
                "n_positive_subperiods": n_pos,
                "n_subperiods": n_sub,
                "net_excess_ann_ex_best_subperiod": ex_best,
            },
            {
                "n_positive_subperiods": ">= %s" % t["value"],
                "net_excess_ann_ex_best_subperiod": "> 0",
            },
            t["provisional"],
            hard=True,
            note="Phase 10-N lesson: no single-subperiod dependence",
        )
    )

    reg = metrics.get("regime_positive_fraction")
    t = th["min_regime_positive_fraction"]
    gates.append(
        _gate(
            "regime_stability",
            None if reg is None else reg >= t["value"],
            reg,
            ">= %s" % t["value"],
            t["provisional"],
            hard=False,
        )
    )

    base_excess = (baseline_metrics or {}).get("net_spy_excess_ann")
    cand_excess = metrics.get("net_spy_excess_ann")
    beat = None
    if base_excess is not None and cand_excess is not None:
        beat = cand_excess > base_excess
    gates.append(
        _gate(
            "beats_baseline_net_excess",
            beat,
            {"candidate": cand_excess, "baseline": base_excess},
            "candidate > baseline",
            provisional=False,
            hard=False,
        )
    )

    return {
        "gates": gates,
        "thresholds": th,
        "hard_gate_failures": [
            g["gate"] for g in gates if g["hard"] and g["passed"] is False
        ],
        "unevaluated_gates": [g["gate"] for g in gates if g["passed"] is None],
    }


def _cost_lookup(by_cost: Dict[Any, Any], bps: float) -> Optional[float]:
    for key in (bps, str(bps), int(bps) if float(bps).is_integer() else None):
        if key is not None and key in by_cost:
            return by_cost[key]
    # tolerate string keys like "25.0"
    for k, v in by_cost.items():
        try:
            if abs(float(k) - bps) < 1e-9:
                return v
        except (TypeError, ValueError):
            continue
    return None


def _diagnostic_flags(gates: Dict[str, Dict[str, Any]]) -> List[str]:
    flags = []
    for name in PRIMARY_DIAGNOSTIC_GATES:
        g = gates.get(name)
        if g is None:
            continue
        if g["passed"] is False:
            flags.append(
                "%s: provisional/diagnostic threshold not met "
                "(non-blocking at the primary stage)" % name
            )
        elif g["passed"] is None:
            flags.append("%s: not evaluated" % name)
    return flags


def decide_candidate(
    gate_results: Dict[str, Any],
    *,
    stage: str,
    deltas: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """stage: 'primary' (retention screening) or 'robustness' (shadow standard).

    ``deltas`` is the persisted baseline-relative delta table from
    ``build_baseline_deltas``. Primary retention REQUIRES it: without delta
    evidence the primary stage can reject but never retain (conservative).
    """
    gates = {g["gate"]: g for g in gate_results["gates"]}
    hard_failures = gate_results["hard_gate_failures"]
    policy = stage_policy(stage)
    reasons: List[str] = []

    if gates["point_in_time_integrity"]["passed"] is False:
        return {
            "decision": REJECTED,
            "reasons": ["point-in-time integrity failed (campaign fail-fast)"],
            "gate_overrides": ["point_in_time_integrity"],
            "stage_policy": policy,
            "diagnostic_flags": [],
        }

    if hard_failures:
        return {
            "decision": REJECTED,
            "reasons": ["hard gate failed: %s" % ", ".join(hard_failures)],
            "gate_overrides": list(hard_failures),
            "stage_policy": policy,
            "diagnostic_flags": [],
        }

    beat = gates["beats_baseline_net_excess"]["passed"]
    ic = gates["rank_ic"]["passed"]
    to = gates["turnover"]["passed"]

    if stage == STAGE_PRIMARY:
        flags = _diagnostic_flags(gates)
        if deltas is None:
            # No persisted delta table: retention evidence is missing, so the
            # primary stage may never RETAIN. Keep the pre-29A.2 rejection for
            # a candidate that does not beat the baseline at all.
            if beat is False:
                return {
                    "decision": REJECTED,
                    "reasons": ["does not beat the baseline on net SPY excess"],
                    "gate_overrides": [],
                    "stage_policy": policy,
                    "diagnostic_flags": flags,
                }
            return {
                "decision": INCONCLUSIVE,
                "reasons": [
                    "baseline-relative delta evidence unavailable; primary "
                    "retention requires the persisted delta table"
                ],
                "gate_overrides": [],
                "stage_policy": policy,
                "diagnostic_flags": flags,
            }

        rows = deltas.get("metrics") or {}
        ret = rows.get(PRIMARY_RETURN_DELTA_METRIC) or {}
        ret_cls = ret.get("classification")
        ret_material_better = bool(ret_cls == "better" and ret.get("material"))
        ret_material_worse = bool(ret_cls == "worse" and ret.get("material"))
        severe = list(deltas.get("severe_degradations") or [])
        worse_core = [
            name
            for name in CORE_RISK_DELTA_METRICS
            if (rows.get(name) or {}).get("classification") == "worse"
            and (rows.get(name) or {}).get("material")
        ]
        tradeoff_gains = [
            name
            for name in TRADE_OFF_IMPROVEMENT_METRICS
            if (rows.get(name) or {}).get("classification") == "better"
            and (rows.get(name) or {}).get("material")
        ]

        if severe:
            return {
                "decision": INCONCLUSIVE,
                "reasons": [
                    "severe baseline-relative degradation blocks retention: %s"
                    % ", ".join(severe)
                ],
                "gate_overrides": [],
                "stage_policy": policy,
                "diagnostic_flags": flags,
            }
        if ret_material_better and len(worse_core) <= 1:
            reasons = [
                "material balanced baseline-relative improvement on %s "
                "(delta %+0.4f, tolerance %.4f)"
                % (PRIMARY_RETURN_DELTA_METRIC, ret.get("delta_abs") or 0.0,
                   ret.get("tolerance") or 0.0)
            ]
            if worse_core:
                reasons.append(
                    "accepted trade-off: materially worse on %s only" % worse_core[0]
                )
            return {
                "decision": RETAIN_FOR_ROBUSTNESS,
                "reasons": reasons,
                "gate_overrides": [],
                "stage_policy": policy,
                "diagnostic_flags": flags,
            }
        if ret_material_better:
            return {
                "decision": INCONCLUSIVE,
                "reasons": [
                    "return improvement is not balanced: materially worse on %s"
                    % ", ".join(worse_core)
                ],
                "gate_overrides": [],
                "stage_policy": policy,
                "diagnostic_flags": flags,
            }
        if not ret_material_worse and tradeoff_gains and not worse_core:
            return {
                "decision": RETAIN_FOR_ROBUSTNESS,
                "reasons": [
                    "useful robustness trade-off: material improvement on %s "
                    "with net excess inside tolerance of the baseline"
                    % ", ".join(tradeoff_gains)
                ],
                "gate_overrides": [],
                "stage_policy": policy,
                "diagnostic_flags": flags,
            }
        if ret_material_worse:
            return {
                "decision": REJECTED,
                "reasons": [
                    "materially underperforms the baseline net of costs "
                    "(delta %+0.4f beyond tolerance %.4f) with no qualifying "
                    "robustness trade-off"
                    % (ret.get("delta_abs") or 0.0, ret.get("tolerance") or 0.0)
                ],
                "gate_overrides": [],
                "stage_policy": policy,
                "diagnostic_flags": flags,
            }
        return {
            "decision": INCONCLUSIVE,
            "reasons": [
                "inside configured tolerances of the baseline on every "
                "material dimension — indistinguishable, not retained"
            ],
            "gate_overrides": [],
            "stage_policy": policy,
            "diagnostic_flags": flags,
        }

    if stage == STAGE_ROBUSTNESS:
        if beat is False:
            return {
                "decision": REJECTED,
                "reasons": ["does not beat the baseline on net SPY excess"],
                "gate_overrides": [],
                "stage_policy": policy,
                "diagnostic_flags": [],
            }
        c50 = gates["cost_robustness_50bps"]["passed"]
        reg = gates["regime_stability"]["passed"]
        # Total return alone can never qualify: IC, 50 bps survival, regime
        # stability, turnover and the hard subperiod/sector/coverage gates are
        # ALL required — the strict shadow standard is never lowered.
        if beat and ic and c50 and reg is True and to is True:
            return {
                "decision": SHADOW_ELIGIBLE,
                "reasons": [
                    "beats baseline net of costs with IC, cost, subperiod, "
                    "regime, turnover and concentration evidence"
                ],
                "gate_overrides": [],
                "stage_policy": policy,
                "diagnostic_flags": [],
            }
        for name, g in gates.items():
            if g["passed"] is False:
                reasons.append("gate failed: %s" % name)
            elif g["passed"] is None and name != "point_in_time_integrity":
                reasons.append("gate not evaluated: %s" % name)
        return {
            "decision": REJECTED if any("failed" in r for r in reasons) else INCONCLUSIVE,
            "reasons": reasons or ["insufficient robustness evidence"],
            "gate_overrides": [],
            "stage_policy": policy,
            "diagnostic_flags": [],
        }

    raise ValueError("unknown evaluation stage: %s" % stage)


# ---------------------------------------------------------------------------
# Multi-objective, baseline-relative transparent score (Part K)
# ---------------------------------------------------------------------------

# (metric key, direction, normalization scale, weight)
SCORE_COMPONENTS = (
    ("net_spy_excess_ann", +1, 0.02, 0.25),
    ("rank_ic_mean", +1, 0.005, 0.15),
    ("rank_ic_ir", +1, 0.10, 0.10),
    ("subperiod_positive_fraction", +1, 0.25, 0.10),
    ("regime_positive_fraction", +1, 0.25, 0.05),
    ("max_drawdown", -1, 0.05, 0.10),
    ("turnover_monthly_oneside", -1, 0.10, 0.10),
    ("cost_slope_12p5_to_50", -1, 0.02, 0.05),
    ("max_sector_weight", -1, 0.10, 0.05),
    ("coverage_fraction", +1, 0.10, 0.05),
)


def score_candidate(
    metrics: Dict[str, Any],
    baseline_metrics: Dict[str, Any],
    gate_results: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    components = []
    total = 0.0
    missing: List[str] = []
    for key, direction, scale, weight in SCORE_COMPONENTS:
        cand = metrics.get(key)
        base = (baseline_metrics or {}).get(key)
        if cand is None or base is None:
            missing.append(key)
            components.append(
                {
                    "component": key,
                    "candidate": cand,
                    "baseline": base,
                    "delta": None,
                    "direction": direction,
                    "normalization_scale": scale,
                    "weight": weight,
                    "normalized": None,
                    "contribution": 0.0,
                    "note": "missing input; contributes zero",
                }
            )
            continue
        delta = float(cand) - float(base)
        normalized = direction * delta / scale
        contribution = weight * normalized
        total += contribution
        components.append(
            {
                "component": key,
                "candidate": cand,
                "baseline": base,
                "delta": delta,
                "direction": direction,
                "normalization_scale": scale,
                "weight": weight,
                "normalized": normalized,
                "contribution": contribution,
                "note": "",
            }
        )

    overrides = []
    if gate_results:
        overrides = list(gate_results.get("hard_gate_failures", []))

    ranked = sorted(
        (c for c in components if c["delta"] is not None),
        key=lambda c: c["contribution"],
        reverse=True,
    )
    tops = [c["component"] for c in ranked[:2]]
    bottoms = [c["component"] for c in ranked[-2:]] if len(ranked) >= 2 else []
    explanation = (
        "Baseline-relative multi-objective score %.4f. "
        "Strongest contributors: %s. Weakest: %s. "
        "%d component(s) missing. "
        "Hard-gate overrides in force: %s. "
        "A high score never implies operational approval."
        % (
            total,
            ", ".join(tops) or "none",
            ", ".join(bottoms) or "none",
            len(missing),
            ", ".join(overrides) or "none",
        )
    )

    return {
        "final_score": total,
        "components": components,
        "missing_components": missing,
        "gate_overrides": overrides,
        "score_capped_by_gates": bool(overrides),
        "explanation": explanation,
    }


__all__ = [
    "CORE_RISK_DELTA_METRICS",
    "DECISIONS",
    "DEFAULT_DELTA_TOLERANCES",
    "DEFAULT_THRESHOLDS",
    "DELTA_METRICS",
    "HARD_GATES",
    "INCONCLUSIVE",
    "PRIMARY_DIAGNOSTIC_GATES",
    "PRIMARY_RETENTION_GATES",
    "PRIMARY_RETURN_DELTA_METRIC",
    "REJECTED",
    "RETAIN_FOR_ROBUSTNESS",
    "SEVERE_DEGRADATION_MULTIPLIER",
    "SHADOW_ELIGIBILITY_GATES",
    "SHADOW_ELIGIBLE",
    "SCORE_COMPONENTS",
    "STAGE_PRIMARY",
    "STAGE_ROBUSTNESS",
    "TRADE_OFF_IMPROVEMENT_METRICS",
    "build_baseline_deltas",
    "decide_candidate",
    "evaluate_gates",
    "resolve_delta_tolerances",
    "resolve_thresholds",
    "score_candidate",
    "stage_policy",
]
