"""Adapters exposing the existing deterministic research machinery as tools.

Every tool consumes the shared ToolContext (owned inputs loaded once,
memoized simulations) and returns plain structured data. Unsupported
parameter combinations return a structured rejection (the registry surfaces
it as REJECTED_UNSUPPORTED); nothing here can execute a command, reach a
network, or write to any Paper Trader store.
"""

from __future__ import annotations

import datetime as _dt
from typing import Any, Dict, List, Optional

from . import evaluator as ev
from . import family_backtest as fb
from . import schemas
from .artifact_store import content_hash
from .tool_registry import (
    COST_CHEAP,
    COST_EXPENSIVE,
    COST_MEDIUM,
    Tool,
    ToolRegistry,
)

TOOL_NAMES = (
    "audit_data_coverage",
    "inspect_feature_availability",
    "validate_point_in_time_integrity",
    "run_baseline_validation",
    "run_walk_forward_backtest",
    "run_parameter_experiment",
    "run_factor_ablation",
    "run_cost_sensitivity",
    "run_turnover_analysis",
    "run_sector_analysis",
    "run_regime_analysis",
    "run_subperiod_stability",
    "run_universe_sensitivity",
    "calculate_rank_ic",
    "calculate_portfolio_metrics",
    "compare_to_baseline",
    "score_candidate",
    "register_shadow_challenger",
    "generate_campaign_report",
)


class ToolContext:
    """Shared, lazily-loaded state for one campaign run."""

    def __init__(
        self,
        *,
        config: Dict[str, Any],
        today: Optional[_dt.date] = None,
        inputs: Optional[Dict[str, Any]] = None,
        reference_rows: Optional[List[dict]] = None,
        close_frame: Optional[Any] = None,
        challenger_registry: Optional[Any] = None,
        report_writer: Optional[Any] = None,
    ):
        self.config = config
        self.today = today or _dt.date.today()
        self._inputs = inputs
        self.reference_rows = reference_rows
        self.close_frame = close_frame
        self.challenger_registry = challenger_registry
        self.report_writer = report_writer
        self.baseline_metrics: Optional[Dict[str, Any]] = None
        self.baseline_validation: Optional[Dict[str, Any]] = None
        self._sim_cache: Dict[str, Dict[str, Any]] = {}

    # ---- data -------------------------------------------------------------
    @property
    def data_cutoff(self) -> str:
        return self.config["data"]["data_cutoff"]

    def inputs(self) -> Dict[str, Any]:
        if self._inputs is None:
            data = self.config.get("data", {})
            self._inputs = fb.load_family_inputs(
                data_cutoff=self.data_cutoff,
                momentum_panel_path=data.get("momentum_panel"),
                fundamental_panel_path=data.get("fundamental_panel"),
                sector_map_path=data.get("sector_map"),
                spy_monthly_path=data.get("spy_monthly"),
            )
        return self._inputs

    def thresholds(self) -> Dict[str, Any]:
        return self.config.get("thresholds", {}) or {}

    # ---- memoized simulation ---------------------------------------------
    def sim_for_params(self, params: Dict[str, Any]) -> Dict[str, Any]:
        key = content_hash(params)
        if key not in self._sim_cache:
            self._sim_cache[key] = fb.run_family_experiment(self.inputs(), params)
        return self._sim_cache[key]


def _params_from_spec(spec: Dict[str, Any]) -> Dict[str, Any]:
    mp = spec["model_params"]
    pp = spec["portfolio_params"]
    return {
        "fundamental_weight": mp["fundamental_weight"],
        "momentum_weight": mp["momentum_weight"],
        "top_n": pp["top_n"],
        "sector_treatment": pp["sector_treatment"],
        "exit_buffer_fraction": pp["exit_buffer_fraction"],
        "universe": spec["universe"],
        "min_adv_dollar": pp["min_adv_dollar"],
    }


def _spec_gate(ctx: ToolContext, spec: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Validate a declarative spec; return a structured rejection or None."""
    verdict = schemas.validate_experiment_spec(spec, today=ctx.today)
    if verdict["accepted"]:
        return None
    unsupported = bool(verdict["unsupported"]) and all(
        v["severity"] == "UNSUPPORTED" for v in verdict["violations"]
    )
    return {
        "_rejected_unsupported": unsupported,
        "rejected": True,
        "reason": "UNSUPPORTED_COMBINATION" if unsupported else "INVALID_SPEC",
        "violations": verdict["violations"],
    }


# --------------------------------------------------------------------------- #
# tool implementations
# --------------------------------------------------------------------------- #
def _audit_data_coverage(ctx: ToolContext) -> Dict[str, Any]:
    inputs = ctx.inputs()
    months = inputs["months"]
    fund_era = [m for m in months if inputs["fund_cf"].get(m)]
    common_counts = []
    for m in fund_era:
        fbk = inputs["fund_cf"].get(m, {})
        mbk = inputs["mom_monthly"].get(m, {})
        common_counts.append(
            sum(
                1
                for tk in fbk
                if tk in mbk
                and mbk[tk].get("eligible")
                and mbk[tk].get("fwd_1m") is not None
            )
        )
    spy_ok = sum(1 for m in months if inputs["spy_fwd"].get(m) is not None)
    return {
        "provenance": inputs["provenance"],
        "n_formation_months": len(months),
        "n_fund_era_months": len(fund_era),
        "fund_era_range": [fund_era[0], fund_era[-1]] if fund_era else None,
        "common_universe": {
            "min": min(common_counts) if common_counts else None,
            "median": sorted(common_counts)[len(common_counts) // 2] if common_counts else None,
            "max": max(common_counts) if common_counts else None,
        },
        "months_below_25_common": sum(1 for c in common_counts if c < 25),
        "months_below_50_common": sum(1 for c in common_counts if c < 50),
        "spy_forward_coverage": spy_ok / len(months) if months else None,
        "sector_map_names": len(inputs["sector_map"]),
    }


def _inspect_feature_availability(ctx: ToolContext) -> Dict[str, Any]:
    inputs = ctx.inputs()
    sample_mom = next(iter(next(iter(inputs["mom_monthly"].values()), {}).values()), {})
    sample_fund = next(iter(next(iter(inputs["fund_monthly"].values()), {}).values()), {})
    return {
        "momentum_features": sorted(sample_mom.keys()),
        "fundamental_features": sorted(sample_fund.keys()),
        "required_momentum": ["mom_6_1", "fwd_1m", "eligible", "is_member", "adv_dollar", "sector"],
        "required_fundamental": ["composite_sn", "sector"],
        "momentum_ok": all(
            k in sample_mom for k in ("mom_6_1", "fwd_1m", "eligible")
        ),
        "fundamental_ok": all(k in sample_fund for k in ("composite_sn", "sector")),
        "spy_months": len(inputs["spy_close"]),
        "provenance": inputs["provenance"],
    }


def _validate_pit(ctx: ToolContext, seed: int = 29, sample_size: int = 40) -> Dict[str, Any]:
    return fb.validate_point_in_time_integrity(
        ctx.inputs(),
        seed=seed,
        sample_size=sample_size,
        close_frame=ctx.close_frame,
    )


def _run_baseline_validation(ctx: ToolContext) -> Dict[str, Any]:
    data = ctx.config.get("data", {})
    result = fb.run_baseline_validation(
        ctx.inputs(),
        reference_path=data.get("reference_book_returns"),
        reference_rows=ctx.reference_rows,
    )
    sim = result.pop("sim")
    baseline_cost = ctx.config.get("baseline", {}).get("cost_bps_per_side", 25.0)
    metrics = fb.compute_experiment_metrics(
        sim, ctx.inputs(), primary_cost_bps_per_side=baseline_cost
    )
    metrics["pit_integrity_ok"] = True  # campaign fails fast earlier when PIT fails
    ctx.baseline_metrics = metrics
    ctx.baseline_validation = result
    return {**result, "baseline_metrics": metrics}


def _run_spec_backtest(ctx: ToolContext, spec: Dict[str, Any]) -> Dict[str, Any]:
    rejected = _spec_gate(ctx, spec)
    if rejected:
        return rejected
    params = _params_from_spec(spec)
    sim = ctx.sim_for_params(params)
    metrics = fb.compute_experiment_metrics(
        sim, ctx.inputs(), primary_cost_bps_per_side=spec["cost_bps_per_side"]
    )
    return {
        "experiment_id": spec.get("experiment_id"),
        "params": params,
        "metrics": metrics,
        "n_periods": sim["n_periods"],
        "first_month": sim["first_month"],
        "last_month": sim["last_month"],
        "skipped_months": len(sim["skipped_months"]),
    }


def _run_factor_ablation(ctx: ToolContext, spec: Dict[str, Any]) -> Dict[str, Any]:
    rejected = _spec_gate(ctx, spec)
    if rejected:
        return rejected
    base_params = _params_from_spec(spec)
    out = {"blend": None, "fundamental_only": None, "momentum_only": None}
    for label, (wf, wm) in (
        ("blend", (base_params["fundamental_weight"], base_params["momentum_weight"])),
        ("fundamental_only", (1.0, 0.0)),
        ("momentum_only", (0.0, 1.0)),
    ):
        p = dict(base_params, fundamental_weight=wf, momentum_weight=wm)
        sim = ctx.sim_for_params(p)
        m = fb.compute_experiment_metrics(
            sim, ctx.inputs(), primary_cost_bps_per_side=spec["cost_bps_per_side"]
        )
        out[label] = {
            "net_spy_excess_ann": m.get("net_spy_excess_ann"),
            "rank_ic_mean": m.get("rank_ic_mean"),
            "rank_ic_t": m.get("rank_ic_t"),
            "turnover_monthly_oneside": m.get("turnover_monthly_oneside"),
            "months": m.get("months"),
        }
    out["note"] = (
        "single-leg views are DIAGNOSTIC ONLY - they are not approved candidate "
        "configurations and can never be registered as challengers"
    )
    return out


def _run_cost_sensitivity(ctx: ToolContext, spec: Dict[str, Any]) -> Dict[str, Any]:
    res = _run_spec_backtest(ctx, spec)
    if res.get("rejected"):
        return res
    m = res["metrics"]
    by_cost = m.get("net_excess_ann_by_cost_bps", {})
    return {
        "experiment_id": spec.get("experiment_id"),
        "net_excess_ann_by_cost_bps": by_cost,
        "cost_slope_12p5_to_50": m.get("cost_slope_12p5_to_50"),
        "survives_25bps": (by_cost.get("25.0") or 0) > 0 if by_cost.get("25.0") is not None else None,
        "survives_50bps": (by_cost.get("50.0") or 0) > 0 if by_cost.get("50.0") is not None else None,
        "collapses_under_costs": (
            (by_cost.get("12.5") or 0) > 0 and (by_cost.get("50.0") or 0) <= 0
            if by_cost.get("12.5") is not None and by_cost.get("50.0") is not None
            else None
        ),
    }


def _run_turnover_analysis(ctx: ToolContext, spec: Dict[str, Any]) -> Dict[str, Any]:
    res = _run_spec_backtest(ctx, spec)
    if res.get("rejected"):
        return res
    m = res["metrics"]
    return {
        "experiment_id": spec.get("experiment_id"),
        "turnover_monthly_oneside": m.get("turnover_monthly_oneside"),
        "turnover_including_establishment": m.get("turnover_including_establishment"),
        "membership_stability": m.get("membership_stability"),
        "months": m.get("months"),
    }


def _run_sector_analysis(ctx: ToolContext, spec: Dict[str, Any]) -> Dict[str, Any]:
    rejected = _spec_gate(ctx, spec)
    if rejected:
        return rejected
    params = _params_from_spec(spec)
    sim = ctx.sim_for_params(params)
    agg: Dict[str, List[float]] = {}
    for p in sim["periods"]:
        for sec, w in p["sector_weights"].items():
            agg.setdefault(sec, []).append(w)
    n = max(1, len(sim["periods"]))
    return {
        "experiment_id": spec.get("experiment_id"),
        "mean_sector_weights": {
            sec: round(sum(ws) / n, 6) for sec, ws in sorted(agg.items())
        },
        "peak_sector_weights": {sec: round(max(ws), 6) for sec, ws in sorted(agg.items())},
        "max_sector_weight": max((max(ws) for ws in agg.values()), default=None),
        "sector_treatment": params["sector_treatment"],
        "months": sim["n_periods"],
    }


def _run_regime_analysis(ctx: ToolContext, spec: Dict[str, Any]) -> Dict[str, Any]:
    res = _run_spec_backtest(ctx, spec)
    if res.get("rejected"):
        return res
    m = res["metrics"]
    return {
        "experiment_id": spec.get("experiment_id"),
        "regime_excess_ann": m.get("regime_excess_ann"),
        "regime_positive_fraction": m.get("regime_positive_fraction"),
        "months": m.get("months"),
    }


def _run_subperiod_stability(ctx: ToolContext, spec: Dict[str, Any]) -> Dict[str, Any]:
    res = _run_spec_backtest(ctx, spec)
    if res.get("rejected"):
        return res
    m = res["metrics"]
    return {
        "experiment_id": spec.get("experiment_id"),
        "subperiod_excess_ann": m.get("subperiod_excess_ann"),
        "n_positive_subperiods": m.get("n_positive_subperiods"),
        "n_subperiods": m.get("n_subperiods"),
        "net_excess_ann_ex_best_subperiod": m.get("net_excess_ann_ex_best_subperiod"),
        "pre2020_excess_ann": m.get("pre2020_excess_ann"),
        "post2020_excess_ann": m.get("post2020_excess_ann"),
        "months": m.get("months"),
    }


def _run_universe_sensitivity(ctx: ToolContext, spec: Dict[str, Any]) -> Dict[str, Any]:
    rejected = _spec_gate(ctx, spec)
    if rejected:
        return rejected
    base_params = _params_from_spec(spec)
    out: Dict[str, Any] = {"experiment_id": spec.get("experiment_id"), "views": {}}
    views = [("mhz_reconstruction", fb.MIN_ADV_DOLLAR_DEFAULT)]
    for adv in schemas.APPROVED_MIN_ADV_DOLLARS:
        views.append(("mhz_live_eligibility", adv))
    for universe, adv in views:
        p = dict(base_params, universe=universe, min_adv_dollar=adv)
        sim = ctx.sim_for_params(p)
        m = fb.compute_experiment_metrics(
            sim, ctx.inputs(), primary_cost_bps_per_side=spec["cost_bps_per_side"]
        )
        out["views"]["%s_adv%d" % (universe, int(adv))] = {
            "net_spy_excess_ann": m.get("net_spy_excess_ann"),
            "months": m.get("months"),
            "avg_common_universe": m.get("avg_common_universe"),
        }
    vals = [v["net_spy_excess_ann"] for v in out["views"].values() if v["net_spy_excess_ann"] is not None]
    out["all_views_positive"] = bool(vals) and all(v > 0 for v in vals)
    return out


def _calculate_rank_ic(ctx: ToolContext, spec: Dict[str, Any]) -> Dict[str, Any]:
    res = _run_spec_backtest(ctx, spec)
    if res.get("rejected"):
        return res
    m = res["metrics"]
    return {
        "experiment_id": spec.get("experiment_id"),
        "rank_ic_mean": m.get("rank_ic_mean"),
        "rank_ic_t": m.get("rank_ic_t"),
        "rank_ic_nw_t": m.get("rank_ic_nw_t"),
        "rank_ic_ir": m.get("rank_ic_ir"),
        "rank_ic_months": m.get("rank_ic_months"),
    }


def _calculate_portfolio_metrics(ctx: ToolContext, spec: Dict[str, Any]) -> Dict[str, Any]:
    return _run_spec_backtest(ctx, spec)


def _compare_to_baseline(ctx: ToolContext, candidate_metrics: Dict[str, Any]) -> Dict[str, Any]:
    if ctx.baseline_metrics is None:
        raise RuntimeError("baseline metrics unavailable - run_baseline_validation first")
    keys = (
        "net_spy_excess_ann",
        "net_return_ann",
        "rank_ic_mean",
        "rank_ic_t",
        "turnover_monthly_oneside",
        "max_drawdown",
        "volatility_ann",
        "hit_rate",
        "max_sector_weight",
        "coverage_fraction",
    )
    deltas = {}
    for k in keys:
        c, b = candidate_metrics.get(k), ctx.baseline_metrics.get(k)
        deltas[k] = {
            "candidate": c,
            "baseline": b,
            "delta": (c - b) if c is not None and b is not None else None,
        }
    return {"deltas": deltas, "baseline_months": ctx.baseline_metrics.get("months")}


def _score_candidate(
    ctx: ToolContext,
    candidate_metrics: Dict[str, Any],
    gate_results: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    if ctx.baseline_metrics is None:
        raise RuntimeError("baseline metrics unavailable - run_baseline_validation first")
    return ev.score_candidate(candidate_metrics, ctx.baseline_metrics, gate_results)


def _register_shadow_challenger(ctx: ToolContext, record: Dict[str, Any]) -> Dict[str, Any]:
    if ctx.challenger_registry is None:
        raise RuntimeError("challenger registry unavailable in this context")
    result = ctx.challenger_registry.register(record)
    return {
        "registered": result["registered"],
        "idempotent": result["idempotent"],
        "candidate_id": result["record"]["candidate_id"],
        "stage": result["record"]["stage"],
        "human_approval_required": True,
        "operational_model_changed": False,
    }


def _generate_campaign_report(ctx: ToolContext) -> Dict[str, Any]:
    if ctx.report_writer is None:
        raise RuntimeError("report writer unavailable in this context")
    return ctx.report_writer()


def build_registry() -> ToolRegistry:
    reg = ToolRegistry()
    spec_input = {"spec": {"type": "object", "required": True}}
    metrics_input = {"candidate_metrics": {"type": "object", "required": True}}

    reg.register(Tool(name="audit_data_coverage", description="Owned-input coverage audit",
                      fn=_audit_data_coverage, input_schema={}, cost_class=COST_CHEAP))
    reg.register(Tool(name="inspect_feature_availability", description="Feature/column availability",
                      fn=_inspect_feature_availability, input_schema={}, cost_class=COST_CHEAP))
    reg.register(Tool(name="validate_point_in_time_integrity",
                      description="Structural no-lookahead checks + sampled phase8c cross-check",
                      fn=_validate_pit,
                      input_schema={"seed": {"type": "int", "required": False},
                                    "sample_size": {"type": "int", "required": False}},
                      cost_class=COST_MEDIUM))
    reg.register(Tool(name="run_baseline_validation",
                      description="Deterministic rerun + exact owned-reference reproduction",
                      fn=_run_baseline_validation, input_schema={}, cost_class=COST_MEDIUM))
    reg.register(Tool(name="run_walk_forward_backtest",
                      description="Monthly walk-forward simulation of one bounded variant",
                      fn=_run_spec_backtest, input_schema=spec_input, cost_class=COST_MEDIUM))
    reg.register(Tool(name="run_parameter_experiment",
                      description="One bounded parameter experiment (full metric battery)",
                      fn=_run_spec_backtest, input_schema=spec_input, cost_class=COST_MEDIUM))
    reg.register(Tool(name="run_factor_ablation",
                      description="Diagnostic single-leg ablation around a spec",
                      fn=_run_factor_ablation, input_schema=spec_input, cost_class=COST_EXPENSIVE))
    reg.register(Tool(name="run_cost_sensitivity",
                      description="Net excess across the 12.5/25/50 bps-per-side ladder",
                      fn=_run_cost_sensitivity, input_schema=spec_input, cost_class=COST_CHEAP))
    reg.register(Tool(name="run_turnover_analysis", description="Turnover and membership stability",
                      fn=_run_turnover_analysis, input_schema=spec_input, cost_class=COST_CHEAP))
    reg.register(Tool(name="run_sector_analysis", description="Sector exposure/concentration",
                      fn=_run_sector_analysis, input_schema=spec_input, cost_class=COST_CHEAP))
    reg.register(Tool(name="run_regime_analysis", description="PIT SPY-regime decomposition",
                      fn=_run_regime_analysis, input_schema=spec_input, cost_class=COST_CHEAP))
    reg.register(Tool(name="run_subperiod_stability", description="Contiguous-thirds + pre/post-2020",
                      fn=_run_subperiod_stability, input_schema=spec_input, cost_class=COST_CHEAP))
    reg.register(Tool(name="run_universe_sensitivity",
                      description="Reconstruction vs live-eligibility universe views",
                      fn=_run_universe_sensitivity, input_schema=spec_input, cost_class=COST_EXPENSIVE))
    reg.register(Tool(name="calculate_rank_ic", description="Monthly Spearman rank IC battery",
                      fn=_calculate_rank_ic, input_schema=spec_input, cost_class=COST_CHEAP))
    reg.register(Tool(name="calculate_portfolio_metrics", description="Full portfolio metric battery",
                      fn=_calculate_portfolio_metrics, input_schema=spec_input, cost_class=COST_MEDIUM))
    reg.register(Tool(name="compare_to_baseline", description="Baseline-relative metric deltas",
                      fn=_compare_to_baseline, input_schema=metrics_input, cost_class=COST_CHEAP))
    reg.register(Tool(name="score_candidate", description="Transparent multi-objective score",
                      fn=_score_candidate,
                      input_schema={"candidate_metrics": {"type": "object", "required": True},
                                    "gate_results": {"type": "object", "required": False}},
                      cost_class=COST_CHEAP))
    reg.register(Tool(name="register_shadow_challenger",
                      description="Append-only challenger registration (shadow-only)",
                      fn=_register_shadow_challenger,
                      input_schema={"record": {"type": "object", "required": True}},
                      cost_class=COST_CHEAP))
    reg.register(Tool(name="generate_campaign_report", description="Campaign report generation",
                      fn=_generate_campaign_report, input_schema={}, cost_class=COST_CHEAP))
    return reg


__all__ = ["TOOL_NAMES", "ToolContext", "build_registry"]
