"""Bounded, declarative experiment/campaign schemas (no arbitrary code, ever).

Every experiment is a plain JSON document validated against the approved
Phase 29A search space. Validation returns STRUCTURED results — never a bare
exception for a user-supplied config:

- severity "INVALID"      -> the document breaks the schema (rejected)
- severity "UNSUPPORTED"  -> vocabulary-valid but the underlying engine has no
                             validated implementation (structured rejection)

Any key that smells like executable content (command/shell/script/exec/eval/
subprocess) is rejected outright: the agent may only choose values on the
approved dimensions below.
"""

from __future__ import annotations

import datetime as _dt
import re
from typing import Any, Dict, List, Optional, Tuple

from . import SCHEMA_VERSION
from .artifact_store import content_hash

# --------------------------------------------------------------------------- #
# Approved Phase 29A variation dimensions
# --------------------------------------------------------------------------- #
BASELINE_MODEL_ID = "fundamental_momentum_50_50_v1"
BASELINE_BOOK_ID = "fundamental_momentum_50_50_top25"
CANDIDATE_FAMILY = "fundamental_momentum_blend_v1"

# (fundamental_weight, momentum_weight)
APPROVED_BLEND_WEIGHTS: Tuple[Tuple[float, float], ...] = (
    (0.30, 0.70),
    (0.40, 0.60),
    (0.50, 0.50),
    (0.60, 0.40),
    (0.70, 0.30),
)

APPROVED_PORTFOLIO_SIZES = (25, 50)

# raw = no sector handling; sector_cap = existing live 25% greedy cap;
# sector_neutral = existing within-sector score demeaning (run_phase22
# sector_neutralize semantics) applied to the blended score.
SECTOR_TREATMENTS = ("raw", "sector_cap", "sector_neutral")
SUPPORTED_SECTOR_TREATMENTS = ("raw", "sector_cap", "sector_neutral")

APPROVED_COST_BPS_PER_SIDE = (12.5, 25.0, 50.0)

REBALANCE_TREATMENTS = ("monthly", "eligible_sessions_20")
# The "20 eligible sessions" construct in this codebase is a forward-outcome
# maturation window (forward_prediction_skill), NOT a rebalance cadence. No
# validated 20-session rebalance implementation exists, so it is vocabulary
# only and must produce a structured rejection.
SUPPORTED_REBALANCE_TREATMENTS = ("monthly",)

# 0.0 = reconstruction behavior (no hysteresis); 0.20 = the live engine's
# EXIT_BUFFER_FRACTION. Only these two values exist in validated code.
APPROVED_EXIT_BUFFER_FRACTIONS = (0.0, 0.20)

DEFENSIVE_OVERLAYS = ("off", "current_validated")
# No validated defensive overlay exists: the low-vol sleeve is
# STATUS_RESEARCH_REFERENCE / SAFE_DIAGNOSTIC_ONLY and cannot create actions.
SUPPORTED_DEFENSIVE_OVERLAYS = ("off",)

# mhz_reconstruction = the published historical reconstruction universe
# (eligible_history + finite momentum + realized forward return).
# mhz_live_eligibility additionally applies the live engine's is_member and
# ADV >= min_adv_dollar filters (used by run_universe_sensitivity).
APPROVED_UNIVERSES = ("mhz_reconstruction", "mhz_live_eligibility")
APPROVED_MIN_ADV_DOLLARS = (1.0e7, 2.5e7)

APPROVED_EVALUATION_HORIZONS = ("1m",)

APPROVED_ROBUSTNESS_TESTS = (
    "cost_sensitivity",
    "turnover_analysis",
    "sector_analysis",
    "regime_analysis",
    "subperiod_stability",
    "universe_sensitivity",
)

MIN_DATA_CUTOFF = "2018-01-01"  # provisional floor: guarantees a multi-year sample

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# Keys that could smuggle executable content: a key is forbidden when any of
# its underscore/dash-separated words matches a token below (so "description"
# is fine but "shell_command", "exec", "run_script" are rejected).
_FORBIDDEN_TOKENS = {
    "exec", "eval", "code", "cmd", "sh", "command", "shell", "script",
    "subprocess", "popen",
}
_FORBIDDEN_KEY_ALLOWLIST = {"code_commit"}
_KEY_SPLIT_RE = re.compile(r"[_\-\s]+")

REQUIRED_EXPERIMENT_FIELDS = (
    "experiment_id",
    "candidate_id",
    "baseline_model",
    "candidate_family",
    "model_params",
    "portfolio_params",
    "data_cutoff",
    "universe",
    "evaluation_horizons",
    "cost_bps_per_side",
    "robustness_tests",
    "compute_estimate",
    "random_seed",
    "stop_conditions",
)

REQUIRED_MODEL_PARAM_FIELDS = ("fundamental_weight", "momentum_weight")
REQUIRED_PORTFOLIO_PARAM_FIELDS = (
    "top_n",
    "sector_treatment",
    "rebalance",
    "exit_buffer_fraction",
    "defensive_overlay",
    "min_adv_dollar",
)

REQUIRED_CONFIG_FIELDS = (
    "schema_version",
    "name",
    "objective",
    "baseline",
    "research_dimensions",
    "data",
    "budgets",
    "stop_conditions",
    "artifact_root",
    "random_seed",
)

REQUIRED_BUDGET_FIELDS = (
    "max_primary_experiments",
    "max_robustness_candidates",
    "max_registered_challengers",
    "max_retry_per_experiment",
    "experiment_timeout_seconds",
)


def _violation(field: str, issue: str, value: Any, severity: str = "INVALID"):
    return {"field": field, "issue": issue, "value": value, "severity": severity}


def find_forbidden_execution_keys(obj: Any, path: str = "$") -> List[str]:
    hits: List[str] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            kp = "%s.%s" % (path, k)
            if isinstance(k, str) and k.lower() not in _FORBIDDEN_KEY_ALLOWLIST:
                parts = set(_KEY_SPLIT_RE.split(k.lower()))
                if parts & _FORBIDDEN_TOKENS:
                    hits.append(kp)
            hits.extend(find_forbidden_execution_keys(v, kp))
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            hits.extend(find_forbidden_execution_keys(v, "%s[%d]" % (path, i)))
    return hits


def _check_date(value: Any, field: str, today: _dt.date, out: List[Dict[str, Any]]):
    if not isinstance(value, str) or not _DATE_RE.match(value):
        out.append(_violation(field, "must be an ISO date YYYY-MM-DD", value))
        return None
    try:
        d = _dt.date.fromisoformat(value)
    except ValueError:
        out.append(_violation(field, "not a real calendar date", value))
        return None
    if d > today:
        out.append(
            _violation(field, "data cutoff may not be in the future", value)
        )
    if value < MIN_DATA_CUTOFF:
        out.append(
            _violation(
                field,
                "cutoff before the minimum supported sample start %s"
                % MIN_DATA_CUTOFF,
                value,
            )
        )
    return d


def _approx_in(value: float, approved, tol=1e-9) -> bool:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return False
    return any(abs(v - a) <= tol for a in approved)


def validate_experiment_spec(
    spec: Any, *, today: Optional[_dt.date] = None
) -> Dict[str, Any]:
    """Validate one declarative experiment. Returns a structured verdict."""
    today = today or _dt.date.today()
    violations: List[Dict[str, Any]] = []

    if not isinstance(spec, dict):
        return {
            "accepted": False,
            "violations": [_violation("$", "spec must be a JSON object", type(spec).__name__)],
            "spec_hash": None,
            "schema_version": SCHEMA_VERSION,
        }

    for kp in find_forbidden_execution_keys(spec):
        violations.append(
            _violation(kp, "executable-content key is forbidden (no arbitrary code)", None)
        )

    for f in REQUIRED_EXPERIMENT_FIELDS:
        if f not in spec:
            violations.append(_violation(f, "required field missing", None))
    unknown = (
        set(spec)
        - set(REQUIRED_EXPERIMENT_FIELDS)
        - {"hypothesis_id", "notes", "spec_hash", "plan_rank"}
    )
    for f in sorted(unknown):
        violations.append(_violation(f, "unknown field (strict schema)", spec.get(f)))

    mp = spec.get("model_params")
    if isinstance(mp, dict):
        for f in REQUIRED_MODEL_PARAM_FIELDS:
            if f not in mp:
                violations.append(_violation("model_params.%s" % f, "required field missing", None))
        for f in sorted(set(mp) - set(REQUIRED_MODEL_PARAM_FIELDS)):
            violations.append(_violation("model_params.%s" % f, "unknown field", mp.get(f)))
        fw, mw = mp.get("fundamental_weight"), mp.get("momentum_weight")
        if fw is not None and mw is not None:
            pair_ok = any(
                abs(float(fw) - a) <= 1e-9 and abs(float(mw) - b) <= 1e-9
                for a, b in APPROVED_BLEND_WEIGHTS
            ) if _is_num(fw) and _is_num(mw) else False
            if not pair_ok:
                violations.append(
                    _violation(
                        "model_params",
                        "blend weights must be one of the approved pairs %s"
                        % (sorted(APPROVED_BLEND_WEIGHTS),),
                        {"fundamental_weight": fw, "momentum_weight": mw},
                    )
                )
    elif mp is not None:
        violations.append(_violation("model_params", "must be an object", mp))

    pp = spec.get("portfolio_params")
    if isinstance(pp, dict):
        for f in REQUIRED_PORTFOLIO_PARAM_FIELDS:
            if f not in pp:
                violations.append(_violation("portfolio_params.%s" % f, "required field missing", None))
        for f in sorted(set(pp) - set(REQUIRED_PORTFOLIO_PARAM_FIELDS)):
            violations.append(_violation("portfolio_params.%s" % f, "unknown field", pp.get(f)))
        if "top_n" in pp and pp["top_n"] not in APPROVED_PORTFOLIO_SIZES:
            violations.append(
                _violation("portfolio_params.top_n", "must be one of %s" % (APPROVED_PORTFOLIO_SIZES,), pp["top_n"])
            )
        st = pp.get("sector_treatment")
        if st is not None:
            if st not in SECTOR_TREATMENTS:
                violations.append(
                    _violation("portfolio_params.sector_treatment", "must be one of %s" % (SECTOR_TREATMENTS,), st)
                )
            elif st not in SUPPORTED_SECTOR_TREATMENTS:
                violations.append(
                    _violation(
                        "portfolio_params.sector_treatment",
                        "no validated implementation exists for this treatment",
                        st,
                        severity="UNSUPPORTED",
                    )
                )
        rb = pp.get("rebalance")
        if rb is not None:
            if rb not in REBALANCE_TREATMENTS:
                violations.append(
                    _violation("portfolio_params.rebalance", "must be one of %s" % (REBALANCE_TREATMENTS,), rb)
                )
            elif rb not in SUPPORTED_REBALANCE_TREATMENTS:
                violations.append(
                    _violation(
                        "portfolio_params.rebalance",
                        "the 20-eligible-session construct is a maturation window, "
                        "not a validated rebalance implementation",
                        rb,
                        severity="UNSUPPORTED",
                    )
                )
        eb = pp.get("exit_buffer_fraction")
        if eb is not None and not _approx_in(eb, APPROVED_EXIT_BUFFER_FRACTIONS):
            violations.append(
                _violation(
                    "portfolio_params.exit_buffer_fraction",
                    "must be one of the existing supported values %s" % (APPROVED_EXIT_BUFFER_FRACTIONS,),
                    eb,
                )
            )
        ov = pp.get("defensive_overlay")
        if ov is not None:
            if ov not in DEFENSIVE_OVERLAYS:
                violations.append(
                    _violation("portfolio_params.defensive_overlay", "must be one of %s" % (DEFENSIVE_OVERLAYS,), ov)
                )
            elif ov not in SUPPORTED_DEFENSIVE_OVERLAYS:
                violations.append(
                    _violation(
                        "portfolio_params.defensive_overlay",
                        "no validated defensive overlay exists (low-vol sleeve is "
                        "diagnostic-only); only 'off' is supported",
                        ov,
                        severity="UNSUPPORTED",
                    )
                )
        ma = pp.get("min_adv_dollar")
        if ma is not None and not _approx_in(ma, APPROVED_MIN_ADV_DOLLARS, tol=1.0):
            violations.append(
                _violation(
                    "portfolio_params.min_adv_dollar",
                    "must be one of %s" % (APPROVED_MIN_ADV_DOLLARS,),
                    ma,
                )
            )
    elif pp is not None:
        violations.append(_violation("portfolio_params", "must be an object", pp))

    if spec.get("baseline_model") is not None and spec.get("baseline_model") != BASELINE_MODEL_ID:
        violations.append(
            _violation("baseline_model", "must be %s" % BASELINE_MODEL_ID, spec.get("baseline_model"))
        )
    if spec.get("candidate_family") is not None and spec.get("candidate_family") != CANDIDATE_FAMILY:
        violations.append(
            _violation("candidate_family", "must be %s" % CANDIDATE_FAMILY, spec.get("candidate_family"))
        )
    if "data_cutoff" in spec:
        _check_date(spec.get("data_cutoff"), "data_cutoff", today, violations)
    if spec.get("universe") is not None and spec.get("universe") not in APPROVED_UNIVERSES:
        violations.append(
            _violation("universe", "must be one of %s" % (APPROVED_UNIVERSES,), spec.get("universe"))
        )
    eh = spec.get("evaluation_horizons")
    if eh is not None:
        if not isinstance(eh, list) or not eh or any(h not in APPROVED_EVALUATION_HORIZONS for h in eh):
            violations.append(
                _violation("evaluation_horizons", "must be a non-empty subset of %s" % (APPROVED_EVALUATION_HORIZONS,), eh)
            )
    cb = spec.get("cost_bps_per_side")
    if cb is not None and not _approx_in(cb, APPROVED_COST_BPS_PER_SIDE):
        violations.append(
            _violation("cost_bps_per_side", "must be one of %s (bps per side)" % (APPROVED_COST_BPS_PER_SIDE,), cb)
        )
    rt = spec.get("robustness_tests")
    if rt is not None:
        if not isinstance(rt, list) or any(t not in APPROVED_ROBUSTNESS_TESTS for t in rt):
            violations.append(
                _violation("robustness_tests", "must be a subset of %s" % (APPROVED_ROBUSTNESS_TESTS,), rt)
            )
    rs = spec.get("random_seed")
    if rs is not None and (not isinstance(rs, int) or isinstance(rs, bool) or rs < 0):
        violations.append(_violation("random_seed", "must be a non-negative integer", rs))
    ce = spec.get("compute_estimate")
    if ce is not None and not isinstance(ce, dict):
        violations.append(_violation("compute_estimate", "must be an object", ce))
    sc = spec.get("stop_conditions")
    if sc is not None and not isinstance(sc, dict):
        violations.append(_violation("stop_conditions", "must be an object", sc))

    accepted = not violations
    return {
        "accepted": accepted,
        "violations": violations,
        "unsupported": [v for v in violations if v["severity"] == "UNSUPPORTED"],
        "spec_hash": content_hash(
            {
                k: v
                for k, v in spec.items()
                if k not in ("experiment_id", "spec_hash", "plan_rank")
            }
        )
        if accepted
        else None,
        "schema_version": SCHEMA_VERSION,
    }


def _is_num(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def validate_campaign_config(
    cfg: Any, *, today: Optional[_dt.date] = None
) -> Dict[str, Any]:
    today = today or _dt.date.today()
    violations: List[Dict[str, Any]] = []
    if not isinstance(cfg, dict):
        return {
            "accepted": False,
            "violations": [_violation("$", "config must be a JSON object", type(cfg).__name__)],
            "config_hash": None,
        }

    for kp in find_forbidden_execution_keys(cfg):
        violations.append(
            _violation(kp, "executable-content key is forbidden (no arbitrary code)", None)
        )

    for f in REQUIRED_CONFIG_FIELDS:
        if f not in cfg:
            violations.append(_violation(f, "required field missing", None))

    base = cfg.get("baseline")
    if isinstance(base, dict):
        if base.get("model_id") != BASELINE_MODEL_ID:
            violations.append(_violation("baseline.model_id", "must be %s" % BASELINE_MODEL_ID, base.get("model_id")))
        if base.get("book_id") != BASELINE_BOOK_ID:
            violations.append(_violation("baseline.book_id", "must be %s" % BASELINE_BOOK_ID, base.get("book_id")))
    elif base is not None:
        violations.append(_violation("baseline", "must be an object", base))

    dims = cfg.get("research_dimensions")
    if isinstance(dims, dict):
        for pair in dims.get("blend_weights", []):
            ok = (
                isinstance(pair, (list, tuple))
                and len(pair) == 2
                and any(
                    abs(pair[0] - a) <= 1e-9 and abs(pair[1] - b) <= 1e-9
                    for a, b in APPROVED_BLEND_WEIGHTS
                )
            )
            if not ok:
                violations.append(
                    _violation("research_dimensions.blend_weights", "unapproved blend pair", pair)
                )
        for n in dims.get("portfolio_sizes", []):
            if n not in APPROVED_PORTFOLIO_SIZES:
                violations.append(_violation("research_dimensions.portfolio_sizes", "unapproved size", n))
        for s in dims.get("sector_treatments", []):
            if s not in SECTOR_TREATMENTS:
                violations.append(_violation("research_dimensions.sector_treatments", "unknown treatment", s))
        for c in dims.get("cost_bps_per_side", []):
            if not _approx_in(c, APPROVED_COST_BPS_PER_SIDE):
                violations.append(_violation("research_dimensions.cost_bps_per_side", "unapproved cost", c))
        for r in dims.get("rebalance_treatments", []):
            if r not in REBALANCE_TREATMENTS:
                violations.append(_violation("research_dimensions.rebalance_treatments", "unknown treatment", r))
        for b in dims.get("exit_buffer_fractions", []):
            if not _approx_in(b, APPROVED_EXIT_BUFFER_FRACTIONS):
                violations.append(_violation("research_dimensions.exit_buffer_fractions", "unapproved buffer", b))
        for o in dims.get("defensive_overlays", []):
            if o not in DEFENSIVE_OVERLAYS:
                violations.append(_violation("research_dimensions.defensive_overlays", "unknown overlay", o))
    elif dims is not None:
        violations.append(_violation("research_dimensions", "must be an object", dims))

    data = cfg.get("data")
    if isinstance(data, dict):
        if "data_cutoff" in data:
            _check_date(data.get("data_cutoff"), "data.data_cutoff", today, violations)
        else:
            violations.append(_violation("data.data_cutoff", "required field missing", None))
    elif data is not None:
        violations.append(_violation("data", "must be an object", data))

    budgets = cfg.get("budgets")
    if isinstance(budgets, dict):
        for f in REQUIRED_BUDGET_FIELDS:
            v = budgets.get(f)
            if not isinstance(v, int) or isinstance(v, bool) or v < 0:
                violations.append(_violation("budgets.%s" % f, "must be a non-negative integer", v))
    elif budgets is not None:
        violations.append(_violation("budgets", "must be an object", budgets))

    sc = cfg.get("stop_conditions")
    if isinstance(sc, dict):
        for f in ("fail_fast_on_pit_failure", "fail_fast_on_baseline_nonreproducibility"):
            if sc.get(f) is not True:
                violations.append(
                    _violation("stop_conditions.%s" % f, "must be true in Phase 29A", sc.get(f))
                )
        if sc.get("no_operational_promotion") is not True:
            violations.append(
                _violation("stop_conditions.no_operational_promotion", "must be true (hard safety rule)", sc.get("no_operational_promotion"))
            )
    elif sc is not None:
        violations.append(_violation("stop_conditions", "must be an object", sc))

    # Optional Phase 29A.2 evaluation section: baseline-relative materiality
    # tolerances for the persisted delta table. Strictly validated when present.
    evl = cfg.get("evaluation")
    if isinstance(evl, dict):
        from .evaluator import DEFAULT_DELTA_TOLERANCES

        for f in sorted(set(evl) - {"delta_tolerances", "severe_degradation_multiplier"}):
            violations.append(
                _violation("evaluation.%s" % f, "unknown field (strict schema)", evl.get(f))
            )
        tols = evl.get("delta_tolerances")
        if tols is not None:
            if not isinstance(tols, dict):
                violations.append(
                    _violation("evaluation.delta_tolerances", "must be an object", tols)
                )
            else:
                for k in sorted(tols):
                    if k not in DEFAULT_DELTA_TOLERANCES:
                        violations.append(
                            _violation(
                                "evaluation.delta_tolerances.%s" % k,
                                "unknown delta metric", tols[k],
                            )
                        )
                    elif not _is_num(tols[k]) or float(tols[k]) < 0:
                        violations.append(
                            _violation(
                                "evaluation.delta_tolerances.%s" % k,
                                "must be a non-negative number", tols[k],
                            )
                        )
        mult = evl.get("severe_degradation_multiplier")
        if mult is not None and (not _is_num(mult) or float(mult) < 1.0):
            violations.append(
                _violation(
                    "evaluation.severe_degradation_multiplier",
                    "must be a number >= 1", mult,
                )
            )
    elif evl is not None:
        violations.append(_violation("evaluation", "must be an object", evl))

    accepted = not violations
    return {
        "accepted": accepted,
        "violations": violations,
        "config_hash": content_hash(cfg) if accepted else None,
        "schema_version": SCHEMA_VERSION,
    }


__all__ = [
    "APPROVED_BLEND_WEIGHTS",
    "APPROVED_COST_BPS_PER_SIDE",
    "APPROVED_EVALUATION_HORIZONS",
    "APPROVED_EXIT_BUFFER_FRACTIONS",
    "APPROVED_MIN_ADV_DOLLARS",
    "APPROVED_PORTFOLIO_SIZES",
    "APPROVED_ROBUSTNESS_TESTS",
    "APPROVED_UNIVERSES",
    "BASELINE_BOOK_ID",
    "BASELINE_MODEL_ID",
    "CANDIDATE_FAMILY",
    "DEFENSIVE_OVERLAYS",
    "MIN_DATA_CUTOFF",
    "REBALANCE_TREATMENTS",
    "SECTOR_TREATMENTS",
    "SUPPORTED_DEFENSIVE_OVERLAYS",
    "SUPPORTED_REBALANCE_TREATMENTS",
    "SUPPORTED_SECTOR_TREATMENTS",
    "find_forbidden_execution_keys",
    "validate_campaign_config",
    "validate_experiment_spec",
]
