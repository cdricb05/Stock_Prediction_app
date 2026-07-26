"""Baseline-relative candidate evaluation: gates, decisions, transparent score.

Everything here consumes ALREADY-COMPUTED metric dictionaries produced by the
deterministic tools; the evaluator never touches market data itself and never
computes returns. Thresholds come from the campaign config; any threshold
without a formally validated project source is labeled provisional.

Candidate outcomes: REJECTED / INCONCLUSIVE / RETAIN_FOR_ROBUSTNESS /
SHADOW_ELIGIBLE. A candidate can never qualify on total return alone: the
SHADOW_ELIGIBLE decision structurally requires rank-IC, cost-robustness,
subperiod-stability and concentration gates in addition to net excess return.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

REJECTED = "REJECTED"
INCONCLUSIVE = "INCONCLUSIVE"
RETAIN_FOR_ROBUSTNESS = "RETAIN_FOR_ROBUSTNESS"
SHADOW_ELIGIBLE = "SHADOW_ELIGIBLE"

DECISIONS = (REJECTED, INCONCLUSIVE, RETAIN_FOR_ROBUSTNESS, SHADOW_ELIGIBLE)

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


def decide_candidate(
    gate_results: Dict[str, Any],
    *,
    stage: str,
) -> Dict[str, Any]:
    """stage: 'primary' (first backtest pass) or 'robustness' (full battery)."""
    gates = {g["gate"]: g for g in gate_results["gates"]}
    hard_failures = gate_results["hard_gate_failures"]
    reasons: List[str] = []

    if gates["point_in_time_integrity"]["passed"] is False:
        return {
            "decision": REJECTED,
            "reasons": ["point-in-time integrity failed (campaign fail-fast)"],
            "gate_overrides": ["point_in_time_integrity"],
        }

    if hard_failures:
        return {
            "decision": REJECTED,
            "reasons": ["hard gate failed: %s" % ", ".join(hard_failures)],
            "gate_overrides": list(hard_failures),
        }

    beat = gates["beats_baseline_net_excess"]["passed"]
    ic = gates["rank_ic"]["passed"]
    to = gates["turnover"]["passed"]

    if beat is False:
        return {
            "decision": REJECTED,
            "reasons": ["does not beat the baseline on net SPY excess"],
            "gate_overrides": [],
        }

    if stage == "primary":
        if beat and ic and (to is not False):
            return {
                "decision": RETAIN_FOR_ROBUSTNESS,
                "reasons": ["beats baseline with acceptable IC and turnover"],
                "gate_overrides": [],
            }
        reasons.append("improvement present but IC/turnover evidence incomplete")
        return {"decision": INCONCLUSIVE, "reasons": reasons, "gate_overrides": []}

    if stage == "robustness":
        c50 = gates["cost_robustness_50bps"]["passed"]
        reg = gates["regime_stability"]["passed"]
        # Total return alone can never qualify: IC, 50 bps survival, regime
        # stability and (hard) subperiod/sector/coverage gates are all required.
        if beat and ic and c50 and (reg is not False) and (to is not False):
            return {
                "decision": SHADOW_ELIGIBLE,
                "reasons": [
                    "beats baseline net of costs with IC, cost, subperiod, "
                    "regime and concentration evidence"
                ],
                "gate_overrides": [],
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
    "DECISIONS",
    "DEFAULT_THRESHOLDS",
    "HARD_GATES",
    "INCONCLUSIVE",
    "REJECTED",
    "RETAIN_FOR_ROBUSTNESS",
    "SHADOW_ELIGIBLE",
    "SCORE_COMPONENTS",
    "decide_candidate",
    "evaluate_gates",
    "resolve_thresholds",
    "score_candidate",
]
