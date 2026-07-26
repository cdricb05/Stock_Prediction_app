"""Phase 29B strict declarative feature DSL.

A feature is a plain JSON document: a tree of approved transformation nodes
over owned, point-in-time source fields. The DSL is validated, never
executed here: ``compile_feature_set`` only maps a valid document onto the
approved deterministic transformation vocabulary (a future bounded feature
campaign is the only thing allowed to compute values).

Hard rejections (structured violations, never a bare exception for
LLM-supplied content):

- arbitrary Python / expressions / shell content (INVALID)
- file paths or unknown source fields supplied by the LLM (UNSUPPORTED)
- unsupported functions (UNSUPPORTED)
- forward shifts, negative lags, centered rolling windows, future joins,
  same-period target leakage (LEAKAGE)
- cyclic feature dependencies, unbounded recursion, excessive depth,
  excessive interaction counts, parameter grids (INVALID)
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

from .artifact_store import content_hash
from .schemas import find_forbidden_execution_keys

DSL_SCHEMA_VERSION = "29B.1"

# ---- approved transformation vocabulary ----------------------------------- #
# op -> (min_inputs, max_inputs, {param: kind}, is_interaction)
# kinds: "int_pos" >=1, "int_nonneg" >=0, "window" int >=2, "frac_low" [0,0.25],
# "frac_high" [0.75,1], "frac_unit" (0,1], "weights" bounded list
ALLOWED_TRANSFORMS: Dict[str, Tuple[int, int, Dict[str, str], bool]] = {
    "lag": (1, 1, {"periods": "int_nonneg"}, False),
    "difference": (1, 1, {"periods": "int_pos"}, False),
    "percentage_change": (1, 1, {"periods": "int_pos"}, False),
    "rolling_mean": (1, 1, {"window": "window", "min_periods": "int_pos"}, False),
    "rolling_median": (1, 1, {"window": "window", "min_periods": "int_pos"}, False),
    "rolling_std": (1, 1, {"window": "window", "min_periods": "int_pos"}, False),
    "rolling_slope": (1, 1, {"window": "window", "min_periods": "int_pos"}, False),
    "zscore": (1, 1, {"window": "window"}, False),
    "cross_sectional_rank": (1, 1, {}, False),
    "winsorize": (1, 1, {"lower_pct": "frac_low", "upper_pct": "frac_high"}, False),
    "volatility_scale": (1, 1, {"window": "window"}, False),
    "sector_neutralize": (1, 1, {}, False),
    "sector_cap": (1, 1, {"cap": "frac_unit"}, False),
    "interaction": (2, 2, {}, True),
    "ratio": (2, 2, {}, True),
    "bounded_sum": (2, 4, {}, True),
    "bounded_weighted_average": (2, 4, {"weights": "weights"}, True),
}

LEAF_OPS = ("source", "feature_ref")

# Ops whose very name encodes look-ahead: rejected as LEAKAGE, not merely
# as unknown vocabulary, so the rejection reason is scientifically explicit.
FORBIDDEN_LEAKAGE_OPS = (
    "forward_shift",
    "lead",
    "future_join",
    "shift_forward",
    "future_value",
    "next_period",
)

# Ops whose very name encodes executable content: rejected outright.
FORBIDDEN_EXECUTION_OPS = (
    "python", "exec", "eval", "shell", "cmd", "command", "script",
    "subprocess", "system", "os", "import", "lambda", "code",
)

# Realized-forward / target fields may never be a feature source: using them
# at formation time is same-period target leakage by construction.
TARGET_FIELD_PREFIXES = ("fwd_", "forward_", "target_")
TARGET_FIELDS = frozenset({"fwd_1m"})

# Bookkeeping identifiers are not numeric feature sources.
NON_SOURCE_FIELDS = frozenset({"ticker"})

SOURCE_FAMILIES = ("momentum", "fundamental", "mixed")

MAX_WINDOW = 60
MAX_PERIODS = 24
MAX_NODES = 25
MAX_TRAVERSAL_DEPTH = 50
MAX_FEATURES_PER_HYPOTHESIS = 3

DEFAULT_MAX_FEATURE_DEPTH = 3
DEFAULT_MAX_INTERACTIONS = 4

REQUIRED_FEATURE_FIELDS = ("feature_id", "description", "source_family", "expression")

_ID_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_"
)


def _is_safe_identifier(value: Any, max_len: int = 64) -> bool:
    return (
        isinstance(value, str)
        and 0 < len(value) <= max_len
        and not value[0].isdigit()
        and all(c in _ID_CHARS for c in value)
    )


def _violation(field: str, issue: str, value: Any, severity: str = "INVALID"):
    return {"field": field, "issue": issue, "value": value, "severity": severity}


def _is_int(x: Any) -> bool:
    return isinstance(x, int) and not isinstance(x, bool)


def _is_num(x: Any) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def _is_target_field(field: str) -> bool:
    return field in TARGET_FIELDS or any(
        field.startswith(p) for p in TARGET_FIELD_PREFIXES
    )


def _check_param(op: str, name: str, kind: str, value: Any, path: str, out: List[dict]):
    p = "%s.params.%s" % (path, name)
    if isinstance(value, (list, tuple, dict)):
        out.append(_violation(
            p, "parameter grids are not allowed; supply one concrete scalar "
            "parameterization per hypothesis", value))
        return
    if kind == "int_pos":
        if not _is_int(value) or value < 1:
            out.append(_violation(p, "must be a positive integer", value))
        elif value > MAX_PERIODS:
            out.append(_violation(p, "exceeds bounded maximum %d" % MAX_PERIODS, value))
    elif kind == "int_nonneg":
        if not _is_int(value):
            out.append(_violation(p, "must be a non-negative integer", value))
        elif value < 0:
            out.append(_violation(
                p, "negative lag is a forward shift into the future "
                "(look-ahead is forbidden)", value, severity="LEAKAGE"))
        elif value > MAX_PERIODS:
            out.append(_violation(p, "exceeds bounded maximum %d" % MAX_PERIODS, value))
    elif kind == "window":
        if not _is_int(value) or value < 2:
            out.append(_violation(p, "must be an integer window >= 2", value))
        elif value > MAX_WINDOW:
            out.append(_violation(p, "exceeds bounded maximum %d" % MAX_WINDOW, value))
    elif kind == "frac_low":
        if not _is_num(value) or not (0.0 <= float(value) <= 0.25):
            out.append(_violation(p, "must be a number in [0, 0.25]", value))
    elif kind == "frac_high":
        if not _is_num(value) or not (0.75 <= float(value) <= 1.0):
            out.append(_violation(p, "must be a number in [0.75, 1.0]", value))
    elif kind == "frac_unit":
        if not _is_num(value) or not (0.0 < float(value) <= 1.0):
            out.append(_violation(p, "must be a number in (0, 1]", value))
    elif kind == "weights":
        out.append(_violation(p, "internal: weights handled at node level", value))


def _walk(
    node: Any,
    path: str,
    *,
    available_fields: frozenset,
    known_feature_ids: frozenset,
    out: List[dict],
    stats: Dict[str, Any],
    depth: int,
) -> int:
    """Validate one expression node; returns the transform depth of the node."""
    stats["nodes"] += 1
    if stats["nodes"] > MAX_NODES:
        if not stats.get("nodes_flagged"):
            stats["nodes_flagged"] = True
            out.append(_violation(
                path, "expression exceeds the bounded maximum of %d nodes "
                "(unbounded recursion/complexity is forbidden)" % MAX_NODES, None))
        return 0
    if depth > MAX_TRAVERSAL_DEPTH:
        if not stats.get("depth_flagged"):
            stats["depth_flagged"] = True
            out.append(_violation(
                path, "expression nesting exceeds the traversal guard "
                "(unbounded recursion is forbidden)", None))
        return 0
    if isinstance(node, str):
        out.append(_violation(
            path, "expression must be a structured node object; free-text or "
            "arbitrary Python/pandas expressions are forbidden", node[:120]))
        return 0
    if not isinstance(node, dict):
        out.append(_violation(path, "expression node must be a JSON object",
                              type(node).__name__))
        return 0

    op = node.get("op")
    if not isinstance(op, str):
        out.append(_violation(path + ".op", "node op must be a string", op))
        return 0
    op_l = op.strip().lower()

    if op_l in FORBIDDEN_EXECUTION_OPS:
        out.append(_violation(
            path + ".op", "executable content is forbidden (no arbitrary "
            "Python, shell commands, or code execution)", op))
        return 0
    if op_l in FORBIDDEN_LEAKAGE_OPS:
        out.append(_violation(
            path + ".op", "forward-looking operation (forward shift / future "
            "join) is forbidden: features may only use information available "
            "at formation time", op, severity="LEAKAGE"))
        return 0

    if op_l == "source":
        extra = set(node) - {"op", "field"}
        for k in sorted(extra):
            out.append(_violation("%s.%s" % (path, k), "unknown field on source node",
                                  node.get(k)))
        field = node.get("field")
        if not _is_safe_identifier(field):
            out.append(_violation(
                path + ".field", "source field must be a plain identifier "
                "(file paths and expressions are forbidden)", field))
            return 0
        if _is_target_field(field):
            out.append(_violation(
                path + ".field", "realized-forward / target fields may never "
                "be feature sources (same-period target leakage)", field,
                severity="LEAKAGE"))
            return 0
        if field in NON_SOURCE_FIELDS:
            out.append(_violation(
                path + ".field", "bookkeeping identifier is not a numeric "
                "feature source", field, severity="UNSUPPORTED"))
            return 0
        if field not in available_fields:
            out.append(_violation(
                path + ".field", "unknown source field: not in the owned "
                "point-in-time feature inventory", field, severity="UNSUPPORTED"))
        return 0

    if op_l == "feature_ref":
        extra = set(node) - {"op", "feature_id"}
        for k in sorted(extra):
            out.append(_violation("%s.%s" % (path, k),
                                  "unknown field on feature_ref node", node.get(k)))
        fid = node.get("feature_id")
        if not _is_safe_identifier(fid, max_len=96):
            out.append(_violation(path + ".feature_id",
                                  "feature_id must be a plain identifier", fid))
            return 0
        if fid not in known_feature_ids:
            out.append(_violation(
                path + ".feature_id", "reference to an unknown feature "
                "(must be defined in the same hypothesis)", fid,
                severity="UNSUPPORTED"))
        stats["refs"].append(fid)
        return 0

    if op_l not in ALLOWED_TRANSFORMS:
        out.append(_violation(
            path + ".op", "unsupported function: not in the approved "
            "Phase 29B transformation vocabulary", op, severity="UNSUPPORTED"))
        return 0

    min_in, max_in, param_schema, is_interaction = ALLOWED_TRANSFORMS[op_l]
    if is_interaction:
        stats["interactions"] += 1

    extra = set(node) - {"op", "inputs", "params"}
    for k in sorted(extra):
        out.append(_violation("%s.%s" % (path, k),
                              "unknown field on transform node", node.get(k)))

    params = node.get("params", {})
    if not isinstance(params, dict):
        out.append(_violation(path + ".params", "params must be an object", params))
        params = {}
    if "centered" in params:
        cv = params.get("centered")
        if cv:
            out.append(_violation(
                path + ".params.centered", "centered rolling windows peek at "
                "the future; windows must be trailing-only", cv,
                severity="LEAKAGE"))
        else:
            out.append(_violation(
                path + ".params.centered", "the centered parameter is not "
                "supported (windows are trailing by construction; omit it)", cv))
    unknown_params = set(params) - set(param_schema) - {"centered"}
    for k in sorted(unknown_params):
        out.append(_violation("%s.params.%s" % (path, k),
                              "unknown parameter for %s" % op_l, params.get(k)))

    inputs = node.get("inputs")
    if not isinstance(inputs, list) or not (min_in <= len(inputs) <= max_in):
        out.append(_violation(
            path + ".inputs", "%s requires between %d and %d input node(s)"
            % (op_l, min_in, max_in), None if not isinstance(inputs, list)
            else len(inputs)))
        inputs = inputs if isinstance(inputs, list) else []

    for pname, kind in param_schema.items():
        if pname in params:
            if kind == "weights":
                w = params[pname]
                ok = (
                    isinstance(w, list)
                    and len(w) == len(inputs)
                    and all(_is_num(x) for x in w)
                    and all(abs(float(x)) <= 1.0 for x in w)
                    and sum(abs(float(x)) for x in w) > 0
                )
                if not ok:
                    out.append(_violation(
                        "%s.params.%s" % (path, pname),
                        "weights must be a list matching the inputs, each "
                        "|w| <= 1, not all zero (bounded by definition)", w))
            else:
                _check_param(op_l, pname, kind, params[pname], path, out)
        elif pname in ("window", "periods"):
            out.append(_violation("%s.params.%s" % (path, pname),
                                  "required parameter missing for %s" % op_l, None))

    child_depths = [
        _walk(child, "%s.inputs[%d]" % (path, i),
              available_fields=available_fields,
              known_feature_ids=known_feature_ids,
              out=out, stats=stats, depth=depth + 1)
        for i, child in enumerate(inputs)
    ]
    return 1 + (max(child_depths) if child_depths else 0)


def validate_feature_spec(
    spec: Any,
    *,
    available_fields: Sequence[str],
    known_feature_ids: Sequence[str] = (),
    max_depth: int = DEFAULT_MAX_FEATURE_DEPTH,
    max_interactions: int = DEFAULT_MAX_INTERACTIONS,
) -> Dict[str, Any]:
    """Validate one declarative feature. Returns a structured verdict."""
    violations: List[dict] = []
    if not isinstance(spec, dict):
        return {
            "accepted": False,
            "violations": [_violation("$", "feature spec must be a JSON object",
                                      type(spec).__name__)],
            "depth": None, "interactions": None, "refs": [],
            "signature": None, "spec_hash": None,
            "schema_version": DSL_SCHEMA_VERSION,
        }

    for kp in find_forbidden_execution_keys(spec):
        violations.append(_violation(
            kp, "executable-content key is forbidden (no arbitrary code)", None))

    for f in REQUIRED_FEATURE_FIELDS:
        if f not in spec:
            violations.append(_violation(f, "required field missing", None))
    for f in sorted(set(spec) - set(REQUIRED_FEATURE_FIELDS)):
        violations.append(_violation(f, "unknown field (strict schema)", spec.get(f)))

    if "feature_id" in spec and not _is_safe_identifier(spec.get("feature_id"), 96):
        violations.append(_violation("feature_id",
                                     "must be a plain identifier", spec.get("feature_id")))
    if "description" in spec and not isinstance(spec.get("description"), str):
        violations.append(_violation("description", "must be a string",
                                     spec.get("description")))
    if "source_family" in spec and spec.get("source_family") not in SOURCE_FAMILIES:
        violations.append(_violation("source_family", "must be one of %s"
                                     % (SOURCE_FAMILIES,), spec.get("source_family")))

    depth = None
    stats: Dict[str, Any] = {"nodes": 0, "interactions": 0, "refs": []}
    if "expression" in spec:
        depth = _walk(
            spec["expression"], "expression",
            available_fields=frozenset(available_fields),
            known_feature_ids=frozenset(known_feature_ids),
            out=violations, stats=stats, depth=0,
        )
        if depth > max_depth:
            violations.append(_violation(
                "expression", "excessive feature depth %d (max %d nested "
                "transformations)" % (depth, max_depth), depth))
        if stats["interactions"] > max_interactions:
            violations.append(_violation(
                "expression", "excessive interaction count %d (max %d)"
                % (stats["interactions"], max_interactions),
                stats["interactions"]))

    accepted = not violations
    return {
        "accepted": accepted,
        "violations": violations,
        "leakage_violations": [v for v in violations if v["severity"] == "LEAKAGE"],
        "unsupported_violations": [v for v in violations if v["severity"] == "UNSUPPORTED"],
        "depth": depth,
        "interactions": stats["interactions"],
        "refs": sorted(set(stats["refs"])),
        "signature": expression_signature(spec) if accepted else None,
        "spec_hash": content_hash(spec) if accepted else None,
        "schema_version": DSL_SCHEMA_VERSION,
    }


def validate_feature_set(
    features: Any,
    *,
    available_fields: Sequence[str],
    max_depth: int = DEFAULT_MAX_FEATURE_DEPTH,
    max_interactions: int = DEFAULT_MAX_INTERACTIONS,
) -> Dict[str, Any]:
    """Validate a hypothesis' full feature list, including cross-references.

    Detects duplicate feature ids, cyclic feature_ref dependencies, and
    reference-expanded depth beyond the configured bound.
    """
    violations: List[dict] = []
    if not isinstance(features, list) or not features:
        return {
            "accepted": False,
            "violations": [_violation("features",
                                      "must be a non-empty list of feature specs",
                                      None)],
            "per_feature": {}, "total_interactions": 0,
            "schema_version": DSL_SCHEMA_VERSION,
        }
    if len(features) > MAX_FEATURES_PER_HYPOTHESIS:
        violations.append(_violation(
            "features", "a hypothesis may define at most %d features"
            % MAX_FEATURES_PER_HYPOTHESIS, len(features)))

    ids: List[str] = []
    for i, f in enumerate(features):
        fid = f.get("feature_id") if isinstance(f, dict) else None
        if isinstance(fid, str):
            if fid in ids:
                violations.append(_violation(
                    "features[%d].feature_id" % i, "duplicate feature_id", fid))
            ids.append(fid)

    per_feature: Dict[str, Dict[str, Any]] = {}
    total_interactions = 0
    for i, f in enumerate(features):
        v = validate_feature_spec(
            f, available_fields=available_fields, known_feature_ids=ids,
            max_depth=max_depth, max_interactions=max_interactions,
        )
        key = (f.get("feature_id") if isinstance(f, dict) else None) or "features[%d]" % i
        per_feature[key] = v
        total_interactions += v.get("interactions") or 0
        for viol in v["violations"]:
            violations.append(dict(viol, field="features[%d].%s" % (i, viol["field"])))

    if total_interactions > max_interactions:
        violations.append(_violation(
            "features", "excessive interaction count %d across the feature "
            "set (max %d)" % (total_interactions, max_interactions),
            total_interactions))

    # cycle detection over feature_ref edges
    graph = {k: v.get("refs", []) for k, v in per_feature.items()}
    state: Dict[str, int] = {}

    def _dfs(node: str, stack: List[str]) -> Optional[List[str]]:
        state[node] = 1
        for nxt in graph.get(node, []):
            if nxt not in graph:
                continue
            if state.get(nxt) == 1:
                return stack + [node, nxt]
            if state.get(nxt, 0) == 0:
                found = _dfs(nxt, stack + [node])
                if found:
                    return found
        state[node] = 2
        return None

    for fid in graph:
        if state.get(fid, 0) == 0:
            cycle = _dfs(fid, [])
            if cycle:
                violations.append(_violation(
                    "features", "cyclic feature dependency is forbidden: %s"
                    % " -> ".join(cycle), cycle))
                break

    # reference-expanded effective depth
    if not any(v["value"] for v in violations if "cyclic" in v["issue"]):
        memo: Dict[str, int] = {}

        def _eff_depth(fid: str, guard: int = 0) -> int:
            if guard > MAX_TRAVERSAL_DEPTH:
                return max_depth + 1
            if fid in memo:
                return memo[fid]
            base = per_feature.get(fid, {}).get("depth") or 0
            ref_extra = max(
                (_eff_depth(r, guard + 1) for r in graph.get(fid, []) if r in graph),
                default=0,
            )
            memo[fid] = base + ref_extra
            return memo[fid]

        for fid in graph:
            if per_feature.get(fid, {}).get("accepted") and _eff_depth(fid) > max_depth:
                violations.append(_violation(
                    "features", "reference-expanded depth of %s exceeds max %d"
                    % (fid, max_depth), _eff_depth(fid)))

    accepted = not violations
    return {
        "accepted": accepted,
        "violations": violations,
        "leakage_violations": [v for v in violations if v["severity"] == "LEAKAGE"],
        "unsupported_violations": [v for v in violations if v["severity"] == "UNSUPPORTED"],
        "per_feature": per_feature,
        "total_interactions": total_interactions,
        "set_signature": feature_set_signature(features) if accepted else None,
        "schema_version": DSL_SCHEMA_VERSION,
    }


def _normalize_node(node: Any) -> Any:
    """Canonical form for signatures: lowercase ops, sorted params."""
    if not isinstance(node, dict):
        return node
    op = str(node.get("op", "")).strip().lower()
    if op == "source":
        return {"op": "source", "field": node.get("field")}
    if op == "feature_ref":
        return {"op": "feature_ref", "feature_id": node.get("feature_id")}
    return {
        "op": op,
        "params": {k: node.get("params", {})[k]
                   for k in sorted(node.get("params", {}) or {})},
        "inputs": [_normalize_node(c) for c in (node.get("inputs") or [])],
    }


def expression_signature(spec: Dict[str, Any]) -> str:
    """Deterministic duplicate-search signature of one feature construction."""
    core = {
        "source_family": spec.get("source_family"),
        "expression": _normalize_node(spec.get("expression")),
    }
    return "dsl:" + content_hash(core)[:20]


def feature_set_signature(features: Sequence[Dict[str, Any]]) -> str:
    sigs = sorted(expression_signature(f) for f in features if isinstance(f, dict))
    return "dslset:" + content_hash(sigs)[:20]


def compile_feature_set(
    features: Sequence[Dict[str, Any]],
    *,
    available_fields: Sequence[str],
    max_depth: int = DEFAULT_MAX_FEATURE_DEPTH,
    max_interactions: int = DEFAULT_MAX_INTERACTIONS,
) -> Dict[str, Any]:
    """Compile a valid feature set into an ordered, declarative step plan.

    Nothing is executed: the output is a data-only description that a future
    bounded deterministic feature campaign may consume. Invalid documents
    return the validation verdict with ``compiled: False``.
    """
    verdict = validate_feature_set(
        features, available_fields=available_fields,
        max_depth=max_depth, max_interactions=max_interactions,
    )
    if not verdict["accepted"]:
        return {"compiled": False, "steps": [], "verdict": verdict}

    steps: List[Dict[str, Any]] = []

    def _emit(node: Dict[str, Any], out: List[Dict[str, Any]]) -> str:
        op = str(node.get("op")).strip().lower()
        if op == "source":
            return "source:%s" % node["field"]
        if op == "feature_ref":
            return "feature:%s" % node["feature_id"]
        inputs = [_emit(c, out) for c in node.get("inputs", [])]
        ref = "step_%d" % (len(out) + 1)
        out.append({
            "step": ref,
            "transform": op,
            "params": {k: node.get("params", {})[k]
                       for k in sorted(node.get("params", {}) or {})},
            "inputs": inputs,
        })
        return ref

    for f in features:
        f_steps: List[Dict[str, Any]] = []
        top = _emit(f["expression"], f_steps)
        steps.append({
            "feature_id": f["feature_id"],
            "source_family": f["source_family"],
            "output": top,
            "steps": f_steps,
            "signature": expression_signature(f),
        })
    return {
        "compiled": True,
        "steps": steps,
        "verdict": verdict,
        "schema_version": DSL_SCHEMA_VERSION,
    }


__all__ = [
    "ALLOWED_TRANSFORMS",
    "DEFAULT_MAX_FEATURE_DEPTH",
    "DEFAULT_MAX_INTERACTIONS",
    "DSL_SCHEMA_VERSION",
    "FORBIDDEN_EXECUTION_OPS",
    "FORBIDDEN_LEAKAGE_OPS",
    "MAX_FEATURES_PER_HYPOTHESIS",
    "SOURCE_FAMILIES",
    "TARGET_FIELDS",
    "compile_feature_set",
    "expression_signature",
    "feature_set_signature",
    "validate_feature_set",
    "validate_feature_spec",
]
