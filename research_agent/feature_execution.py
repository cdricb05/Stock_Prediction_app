"""Phase 29C deterministic feature-execution engine.

Converts a VALIDATED, COMPILED Phase 29B feature-DSL document into real
per-(month, ticker) feature values over the owned point-in-time monthly
panel — through a FIXED function registry only. Nothing here evaluates
strings, imports code dynamically, or touches a shell: the only reachable
operations are the module-level ``TRANSFORM_EXECUTORS`` entries, and an
unknown compiled step is a structured rejection, never a dispatch.

Point-in-time rules enforced by construction (Part B):

- lag / difference / percentage_change / rolling_* / zscore /
  volatility_scale operate WITHIN ticker on calendar-month arithmetic and
  are trailing-only (the DSL already rejects negative lags and centered
  windows; this module simply has no code path that can look forward);
- cross_sectional_rank / winsorize operate within one formation month;
- sector_neutralize operates within one formation month and sector;
- realized-forward fields (``fwd_*``/``forward_*``/``target_*``) are never
  numeric sources: the source inventory excludes them and the executor
  re-refuses them independently of the DSL layer;
- the target is NOT joined here at all — evaluation joins ``fwd_1m`` only
  after features are fully formed (feature_evaluation);
- no backward filling from a future row: a missing value stays missing;
- no full-sample normalization: every normalizing transform (zscore,
  volatility_scale, winsorize, ranks) uses only trailing or same-month
  data, which the truncation-invariance PIT check proves dynamically.

Reused conventions (never re-derived):

- panel loading / formation-month semantics: family_backtest.load_family_inputs
- fundamental carry-forward (4-month staleness): family_backtest.build_fund_carryforward
- calendar-month helpers: family_backtest._month_diff/_next_month/_month_end
- descending percentile rank with ticker tie-break: family_backtest._rank_desc_pct
- content hashing: artifact_store.content_hash
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import family_backtest as fb
from .artifact_store import content_hash
from .feature_dsl import (
    ALLOWED_TRANSFORMS,
    NON_SOURCE_FIELDS,
    _is_target_field,
)

FEATURE_EXECUTION_SCHEMA_VERSION = "29C.1"

_EPS = 1e-12

# Vocabulary-valid transforms with NO validated feature-level implementation.
# ``sector_cap`` is the existing PORTFOLIO construction treatment (the greedy
# 25% known-sector cap in family_backtest._select_topn_capped); inventing a
# per-name numeric "capped feature" semantic would be new, unvalidated
# research machinery, so — exactly like the 29A rebalance treatment — it is
# vocabulary that produces a structured UNSUPPORTED rejection at execution.
EXECUTION_UNSUPPORTED_TRANSFORMS = ("sector_cap",)

# One documented missing-value policy line per transform (persisted in the
# per-step audit rows so behavior is recorded, never implicit).
MISSING_VALUE_POLICY = {
    "lag": "value at month m-p; missing when the lagged month/ticker is absent",
    "difference": "x[m]-x[m-p]; missing unless both months present",
    "percentage_change": "(x[m]-x[m-p])/|x[m-p]|; missing unless both present "
    "and |x[m-p]| exceeds the epsilon guard",
    "rolling_mean": "trailing calendar window; needs >= min_periods present "
    "values (default: the full window)",
    "rolling_median": "trailing calendar window; even counts average the two "
    "middle values; needs >= min_periods present values",
    "rolling_std": "trailing sample std (ddof=1); needs >= max(min_periods, 2)",
    "rolling_slope": "OLS slope of value on window position; needs >= "
    "max(min_periods, 2) and non-degenerate time variance",
    "zscore": "(x[m]-trailing mean)/trailing std over the FULL window; missing "
    "unless all window values present and std exceeds epsilon",
    "cross_sectional_rank": "descending percentile within month (best=1), "
    "deterministic ticker tie-break; absent tickers stay absent",
    "winsorize": "clamped to within-month interpolated quantiles; absent "
    "values stay absent",
    "volatility_scale": "x[m]/trailing std over the FULL window; missing "
    "unless all window values present and std exceeds epsilon",
    "sector_neutralize": "within-month within-sector demeaning (run_phase22 "
    "sector_neutralize semantics); absent values stay absent",
    "interaction": "elementwise product; missing unless both inputs present",
    "ratio": "a/b; missing unless both present and |b| exceeds epsilon",
    "bounded_sum": "elementwise sum; missing unless every input present",
    "bounded_weighted_average": "sum(w_i*x_i)/sum(|w_i|); equal weights when "
    "omitted; missing unless every input present",
}


class FeatureExecutionError(RuntimeError):
    pass


def _add_months(month: str, delta: int) -> str:
    y, m = int(month[:4]), int(month[5:7])
    total = y * 12 + (m - 1) + delta
    return "%04d-%02d" % (total // 12, total % 12 + 1)


def _is_finite_number(x: Any) -> bool:
    return (
        isinstance(x, (int, float))
        and not isinstance(x, bool)
        and math.isfinite(float(x))
    )


def _quantile(sorted_values: List[float], q: float) -> float:
    """Deterministic linear-interpolation quantile over a sorted list."""
    n = len(sorted_values)
    if n == 1:
        return sorted_values[0]
    pos = q * (n - 1)
    lo = int(math.floor(pos))
    hi = min(lo + 1, n - 1)
    frac = pos - lo
    return sorted_values[lo] + frac * (sorted_values[hi] - sorted_values[lo])


# --------------------------------------------------------------------------- #
# Part C: source inventory and field contract
# --------------------------------------------------------------------------- #
def build_source_inventory(
    inputs: Dict[str, Any], *, sample_rows: int = 400
) -> Dict[str, Any]:
    """Classify every panel field from the ACTUAL owned data, never a claim.

    numeric sources / categorical sources / filter flags / target fields /
    rejected bookkeeping, with dtype evidence, coverage over the formation
    months and the persisted provenance hashes.
    """
    mom = inputs["mom_monthly"]
    fund = inputs["fund_monthly"]
    months = inputs["months"]
    fund_cf = inputs["fund_cf"]

    def _classify(field: str, values: List[Any]) -> str:
        non_null = [v for v in values if v is not None]
        if not non_null:
            return "empty"
        if all(isinstance(v, bool) for v in non_null):
            return "filter"
        if all(_is_finite_number(v) for v in non_null):
            return "numeric"
        if all(isinstance(v, str) for v in non_null):
            return "categorical"
        return "mixed"

    def _sample(panel: Dict[str, Dict[str, dict]]) -> List[dict]:
        rows: List[dict] = []
        for m in sorted(panel):
            for tk in sorted(panel[m]):
                rows.append(panel[m][tk])
                if len(rows) >= sample_rows:
                    return rows
        return rows

    mom_rows = _sample(mom)
    fund_rows = _sample(fund)
    mom_fields = sorted({k for r in mom_rows for k in r})
    fund_fields = sorted({k for r in fund_rows for k in r})

    numeric: Dict[str, Dict[str, Any]] = {}
    categorical: List[str] = []
    filters: List[str] = []
    targets: List[str] = []
    rejected: Dict[str, str] = {}

    def _coverage_momentum(field: str) -> Optional[float]:
        total = hits = 0
        for m in months:
            for tk, row in mom.get(m, {}).items():
                if not row.get("eligible"):
                    continue
                total += 1
                if _is_finite_number(row.get(field)):
                    hits += 1
        return (hits / total) if total else None

    def _coverage_fund_cf() -> Optional[float]:
        total = hits = 0
        for m in months:
            elig = [tk for tk, r in mom.get(m, {}).items() if r.get("eligible")]
            total += len(elig)
            cf = fund_cf.get(m, {})
            hits += sum(
                1 for tk in elig if _is_finite_number(
                    (cf.get(tk) or {}).get("composite_sn"))
            )
        return (hits / total) if total else None

    for family, fields, rows in (
        ("momentum", mom_fields, mom_rows),
        ("fundamental", fund_fields, fund_rows),
    ):
        for field in fields:
            if field in numeric or field in categorical or field in filters \
                    or field in targets or field in rejected:
                continue
            if field in NON_SOURCE_FIELDS:
                rejected[field] = "bookkeeping identifier, never a feature source"
                continue
            if _is_target_field(field):
                targets.append(field)
                continue
            kind = _classify(field, [r.get(field) for r in rows])
            if kind == "numeric":
                cov = (
                    _coverage_fund_cf() if field == "composite_sn"
                    else _coverage_momentum(field) if family == "momentum"
                    else None
                )
                numeric[field] = {
                    "family": family,
                    "dtype": "float",
                    "coverage_fraction": cov,
                    "orientation": "verified per feature at the IC screen; "
                    "never assumed",
                }
            elif kind == "filter":
                filters.append(field)
            elif kind == "categorical":
                categorical.append(field)
            else:
                rejected[field] = "non-numeric or mixed-type field (%s)" % kind

    return {
        "schema_version": FEATURE_EXECUTION_SCHEMA_VERSION,
        "record_type": "SOURCE_INVENTORY",
        "data_cutoff": inputs["data_cutoff"],
        "numeric_sources": {k: numeric[k] for k in sorted(numeric)},
        "categorical_sources": sorted(categorical),
        "filter_fields": sorted(filters),
        "target_fields": sorted(targets),
        "rejected_fields": {k: rejected[k] for k in sorted(rejected)},
        "n_formation_months": len(months),
        "n_fund_era_months": sum(1 for m in months if fund_cf.get(m)),
        "provenance": {
            "sha256": (inputs.get("provenance") or {}).get("sha256"),
            "n_formation_months": (inputs.get("provenance") or {}).get(
                "n_formation_months"),
            "max_formation_month": (inputs.get("provenance") or {}).get(
                "max_formation_month"),
        },
        "note": "built from the actual in-memory owned panels; categorical "
        "and bookkeeping fields are never numeric feature inputs",
    }


# --------------------------------------------------------------------------- #
# panel context
# --------------------------------------------------------------------------- #
class FeaturePanel:
    """Deterministic month/ticker access over the loaded owned inputs.

    ``months`` covers every panel month whose month-end is on/before the
    data cutoff (features may use trailing history from before the first
    formation month); evaluation later joins targets on formation months
    only. ``max_month`` truncates the panel for the PIT probe reruns.
    """

    def __init__(self, inputs: Dict[str, Any], *, max_month: Optional[str] = None):
        self.data_cutoff = inputs["data_cutoff"]
        cutoff = self.data_cutoff
        mom = inputs["mom_monthly"]
        months = [m for m in sorted(mom) if fb._month_end(m) <= cutoff]
        if max_month is not None:
            months = [m for m in months if m <= max_month]
        self.months: List[str] = months
        self.formation_months: List[str] = [
            m for m in inputs["months"] if max_month is None or m <= max_month
        ]
        self.mom = mom
        fund = {
            m: d for m, d in inputs["fund_monthly"].items()
            if m <= (max_month or cutoff[:7])
        }
        self.fund_cf = fb.build_fund_carryforward(fund, self.months)
        self._series_cache: Dict[str, Dict[str, Dict[str, float]]] = {}

    def sector(self, month: str, ticker: str) -> str:
        cf = (self.fund_cf.get(month) or {}).get(ticker)
        if cf and cf.get("sector") and cf["sector"] != "Unknown":
            return cf["sector"]
        row = (self.mom.get(month) or {}).get(ticker)
        return (row or {}).get("sector") or "Unknown"

    def source_series(
        self, field: str, inventory: Dict[str, Any]
    ) -> Dict[str, Dict[str, float]]:
        meta = (inventory.get("numeric_sources") or {}).get(field)
        if meta is None:
            raise FeatureExecutionError(
                "field %r is not an approved numeric source" % (field,))
        if field in self._series_cache:
            return self._series_cache[field]
        out: Dict[str, Dict[str, float]] = {}
        if field == "composite_sn":
            for m in self.months:
                row = {
                    tk: float(p["composite_sn"])
                    for tk, p in (self.fund_cf.get(m) or {}).items()
                    if _is_finite_number(p.get("composite_sn"))
                }
                if row:
                    out[m] = row
        else:
            for m in self.months:
                row = {
                    tk: float(r[field])
                    for tk, r in (self.mom.get(m) or {}).items()
                    if _is_finite_number(r.get(field))
                }
                if row:
                    out[m] = row
        self._series_cache[field] = out
        return out


Series = Dict[str, Dict[str, float]]


def _series_size(series: Series) -> int:
    return sum(len(d) for d in series.values())


# --------------------------------------------------------------------------- #
# fixed transform registry (Part A)
# --------------------------------------------------------------------------- #
def _window_values(
    series: Series, month: str, ticker: str, window: int
) -> List[float]:
    vals: List[float] = []
    for j in range(window - 1, -1, -1):  # oldest -> newest, trailing only
        v = series.get(_add_months(month, -j), {}).get(ticker)
        if v is not None:
            vals.append(v)
    return vals


def _window_pairs(
    series: Series, month: str, ticker: str, window: int
) -> List[Tuple[int, float]]:
    pairs: List[Tuple[int, float]] = []
    for j in range(window - 1, -1, -1):
        v = series.get(_add_months(month, -j), {}).get(ticker)
        if v is not None:
            pairs.append((window - 1 - j, v))
    return pairs


def _tickers_in_window(series: Series, month: str, window: int) -> List[str]:
    out = set()
    for j in range(window):
        out |= set(series.get(_add_months(month, -j), {}))
    return sorted(out)


def _x_lag(panel: FeaturePanel, inputs: Sequence[Series], params: dict) -> Series:
    p = int(params["periods"])
    src = inputs[0]
    out: Series = {}
    for m in panel.months:
        prev = src.get(_add_months(m, -p))
        if prev:
            out[m] = dict(prev)
    return out


def _x_difference(panel, inputs, params) -> Series:
    p = int(params["periods"])
    src = inputs[0]
    out: Series = {}
    for m in panel.months:
        cur, prev = src.get(m, {}), src.get(_add_months(m, -p), {})
        row = {tk: cur[tk] - prev[tk] for tk in cur if tk in prev}
        if row:
            out[m] = row
    return out


def _x_percentage_change(panel, inputs, params) -> Series:
    p = int(params["periods"])
    src = inputs[0]
    out: Series = {}
    for m in panel.months:
        cur, prev = src.get(m, {}), src.get(_add_months(m, -p), {})
        row = {
            tk: (cur[tk] - prev[tk]) / abs(prev[tk])
            for tk in cur
            if tk in prev and abs(prev[tk]) > _EPS
        }
        if row:
            out[m] = row
    return out


def _rolling(panel, inputs, params, agg) -> Series:
    src = inputs[0]
    w = int(params["window"])
    q = int(params.get("min_periods", w))
    out: Series = {}
    for m in panel.months:
        row = {}
        for tk in _tickers_in_window(src, m, w):
            vals = _window_values(src, m, tk, w)
            if len(vals) >= q:
                v = agg(vals)
                if v is not None:
                    row[tk] = v
        if row:
            out[m] = row
    return out


def _agg_mean(vals: List[float]) -> float:
    return sum(vals) / len(vals)


def _agg_median(vals: List[float]) -> float:
    s = sorted(vals)
    n = len(s)
    mid = n // 2
    return s[mid] if n % 2 else 0.5 * (s[mid - 1] + s[mid])


def _agg_std(vals: List[float]) -> Optional[float]:
    n = len(vals)
    if n < 2:
        return None
    mu = sum(vals) / n
    return math.sqrt(sum((v - mu) ** 2 for v in vals) / (n - 1))


def _x_rolling_mean(panel, inputs, params) -> Series:
    return _rolling(panel, inputs, params, _agg_mean)


def _x_rolling_median(panel, inputs, params) -> Series:
    return _rolling(panel, inputs, params, _agg_median)


def _x_rolling_std(panel, inputs, params) -> Series:
    p = dict(params)
    p["min_periods"] = max(int(p.get("min_periods", p["window"])), 2)
    return _rolling(panel, inputs, p, _agg_std)


def _x_rolling_slope(panel, inputs, params) -> Series:
    src = inputs[0]
    w = int(params["window"])
    q = max(int(params.get("min_periods", w)), 2)
    out: Series = {}
    for m in panel.months:
        row = {}
        for tk in _tickers_in_window(src, m, w):
            pairs = _window_pairs(src, m, tk, w)
            if len(pairs) < q:
                continue
            ts = [t for t, _v in pairs]
            vs = [v for _t, v in pairs]
            tbar = sum(ts) / len(ts)
            vbar = sum(vs) / len(vs)
            den = sum((t - tbar) ** 2 for t in ts)
            if den <= _EPS:
                continue
            row[tk] = sum((t - tbar) * (v - vbar) for t, v in pairs) / den
        if row:
            out[m] = row
    return out


def _x_zscore(panel, inputs, params) -> Series:
    src = inputs[0]
    w = int(params["window"])
    out: Series = {}
    for m in panel.months:
        cur = src.get(m, {})
        row = {}
        for tk in cur:
            vals = _window_values(src, m, tk, w)
            if len(vals) < w:  # strict full trailing window
                continue
            sd = _agg_std(vals)
            if sd is None or sd <= _EPS:
                continue
            row[tk] = (cur[tk] - _agg_mean(vals)) / sd
        if row:
            out[m] = row
    return out


def _x_volatility_scale(panel, inputs, params) -> Series:
    src = inputs[0]
    w = int(params["window"])
    out: Series = {}
    for m in panel.months:
        cur = src.get(m, {})
        row = {}
        for tk in cur:
            vals = _window_values(src, m, tk, w)
            if len(vals) < w:
                continue
            sd = _agg_std(vals)
            if sd is None or sd <= _EPS:
                continue
            row[tk] = cur[tk] / sd
        if row:
            out[m] = row
    return out


def _x_cross_sectional_rank(panel, inputs, params) -> Series:
    src = inputs[0]
    out: Series = {}
    for m in panel.months:
        cur = src.get(m)
        if cur:
            out[m] = fb._rank_desc_pct(dict(cur))
    return out


def _x_winsorize(panel, inputs, params) -> Series:
    src = inputs[0]
    lo_q = float(params.get("lower_pct", 0.01))
    hi_q = float(params.get("upper_pct", 0.99))
    out: Series = {}
    for m in panel.months:
        cur = src.get(m)
        if not cur:
            continue
        s = sorted(cur.values())
        lo, hi = _quantile(s, lo_q), _quantile(s, hi_q)
        out[m] = {tk: min(max(v, lo), hi) for tk, v in cur.items()}
    return out


def _x_sector_neutralize(panel, inputs, params) -> Series:
    src = inputs[0]
    out: Series = {}
    for m in panel.months:
        cur = src.get(m)
        if not cur:
            continue
        groups: Dict[str, List[str]] = {}
        for tk in cur:
            groups.setdefault(panel.sector(m, tk), []).append(tk)
        row = {}
        for tks in groups.values():
            mu = sum(cur[tk] for tk in tks) / len(tks)
            for tk in tks:
                row[tk] = cur[tk] - mu
        out[m] = row
    return out


def _x_interaction(panel, inputs, params) -> Series:
    a, b = inputs[0], inputs[1]
    out: Series = {}
    for m in panel.months:
        ra, rb = a.get(m, {}), b.get(m, {})
        row = {tk: ra[tk] * rb[tk] for tk in ra if tk in rb}
        if row:
            out[m] = row
    return out


def _x_ratio(panel, inputs, params) -> Series:
    a, b = inputs[0], inputs[1]
    out: Series = {}
    for m in panel.months:
        ra, rb = a.get(m, {}), b.get(m, {})
        row = {
            tk: ra[tk] / rb[tk]
            for tk in ra
            if tk in rb and abs(rb[tk]) > _EPS
        }
        if row:
            out[m] = row
    return out


def _x_bounded_sum(panel, inputs, params) -> Series:
    out: Series = {}
    for m in panel.months:
        rows = [s.get(m, {}) for s in inputs]
        if not all(rows):
            continue
        common = set(rows[0])
        for r in rows[1:]:
            common &= set(r)
        row = {tk: sum(r[tk] for r in rows) for tk in common}
        if row:
            out[m] = row
    return out


def _x_bounded_weighted_average(panel, inputs, params) -> Series:
    weights = params.get("weights") or [1.0] * len(inputs)
    weights = [float(w) for w in weights]
    denom = sum(abs(w) for w in weights)
    out: Series = {}
    for m in panel.months:
        rows = [s.get(m, {}) for s in inputs]
        if not all(rows):
            continue
        common = set(rows[0])
        for r in rows[1:]:
            common &= set(r)
        row = {
            tk: sum(w * r[tk] for w, r in zip(weights, rows)) / denom
            for tk in common
        }
        if row:
            out[m] = row
    return out


TRANSFORM_EXECUTORS = {
    "lag": _x_lag,
    "difference": _x_difference,
    "percentage_change": _x_percentage_change,
    "rolling_mean": _x_rolling_mean,
    "rolling_median": _x_rolling_median,
    "rolling_std": _x_rolling_std,
    "rolling_slope": _x_rolling_slope,
    "zscore": _x_zscore,
    "cross_sectional_rank": _x_cross_sectional_rank,
    "winsorize": _x_winsorize,
    "volatility_scale": _x_volatility_scale,
    "sector_neutralize": _x_sector_neutralize,
    "interaction": _x_interaction,
    "ratio": _x_ratio,
    "bounded_sum": _x_bounded_sum,
    "bounded_weighted_average": _x_bounded_weighted_average,
}

# The executable registry must stay aligned with the committed DSL vocabulary:
# every allowed transform is either executable or explicitly declared
# execution-unsupported. Verified at import time so drift is impossible.
_missing = (
    set(ALLOWED_TRANSFORMS)
    - set(TRANSFORM_EXECUTORS)
    - set(EXECUTION_UNSUPPORTED_TRANSFORMS)
)
if _missing:  # pragma: no cover - import-time invariant
    raise FeatureExecutionError(
        "DSL transforms without an execution decision: %s" % sorted(_missing))


def _drop_nonfinite(series: Series) -> Tuple[Series, int]:
    dropped = 0
    out: Series = {}
    for m, row in series.items():
        clean = {}
        for tk, v in row.items():
            if _is_finite_number(v):
                clean[tk] = float(v)
            else:
                dropped += 1
        if clean:
            out[m] = clean
    return out, dropped


def _series_coverage(series: Series) -> Dict[str, Any]:
    months = sorted(series)
    n_values = _series_size(series)
    return {
        "n_months": len(months),
        "first_month": months[0] if months else None,
        "last_month": months[-1] if months else None,
        "n_values": n_values,
        "mean_cross_section": (n_values / len(months)) if months else None,
    }


# --------------------------------------------------------------------------- #
# feature-set execution (Part A)
# --------------------------------------------------------------------------- #
def _reject(status: str, violations: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "executed": False,
        "status": status,
        "violations": violations,
        "features": {},
        "terminal_feature_id": None,
        "feature_content_hash": None,
        "schema_version": FEATURE_EXECUTION_SCHEMA_VERSION,
    }


def execute_feature_set(
    compiled: Dict[str, Any],
    panel: FeaturePanel,
    inventory: Dict[str, Any],
) -> Dict[str, Any]:
    """Execute one COMPILED feature set deterministically.

    Accepts only the output of feature_dsl.compile_feature_set with
    ``compiled: True``. Returns structured data — per-feature value series,
    per-step audits (inputs/outputs/coverage/missing policy/dropped
    non-finite counts) and a deterministic feature content hash — or a
    structured rejection; it never raises for document-level problems.
    """
    if not isinstance(compiled, dict) or not compiled.get("compiled"):
        return _reject("REJECTED_INVALID", [{
            "field": "$", "issue": "input must be a compiled feature set "
            "(feature_dsl.compile_feature_set with compiled=true)",
            "severity": "INVALID"}])

    feature_plans = compiled.get("steps") or []
    by_id = {f.get("feature_id"): f for f in feature_plans}

    # dependency order over feature_ref edges (DSL already proved acyclic)
    ordered: List[str] = []
    seen: set = set()

    def _deps_of(plan: Dict[str, Any]) -> List[str]:
        deps = []
        for step in plan.get("steps") or []:
            for ref in step.get("inputs") or []:
                if isinstance(ref, str) and ref.startswith("feature:"):
                    deps.append(ref.split(":", 1)[1])
        out = plan.get("output")
        if isinstance(out, str) and out.startswith("feature:"):
            deps.append(out.split(":", 1)[1])
        return deps

    def _visit(fid: str) -> None:
        if fid in seen:
            return
        seen.add(fid)
        for dep in _deps_of(by_id.get(fid) or {}):
            if dep in by_id:
                _visit(dep)
        ordered.append(fid)

    for fid in sorted(by_id):
        _visit(fid)

    computed: Dict[str, Series] = {}
    features_out: Dict[str, Dict[str, Any]] = {}
    violations: List[Dict[str, Any]] = []

    for fid in ordered:
        plan = by_id[fid]
        step_audits: List[Dict[str, Any]] = []
        env: Dict[str, Series] = {}

        def _resolve(ref: str) -> Optional[Series]:
            if ref in env:
                return env[ref]
            if ref.startswith("source:"):
                field = ref.split(":", 1)[1]
                if field not in (inventory.get("numeric_sources") or {}):
                    violations.append({
                        "field": "%s.%s" % (fid, ref),
                        "issue": "not an approved numeric source in the owned "
                        "inventory (categorical/filter/bookkeeping/target "
                        "fields are never numeric feature inputs)",
                        "severity": "UNSUPPORTED"})
                    return None
                series = panel.source_series(field, inventory)
                env[ref] = series
                return series
            if ref.startswith("feature:"):
                dep = ref.split(":", 1)[1]
                series = computed.get(dep)
                if series is None:
                    violations.append({
                        "field": "%s.%s" % (fid, ref),
                        "issue": "referenced feature was not computed",
                        "severity": "INVALID"})
                return series
            violations.append({
                "field": "%s.%s" % (fid, ref),
                "issue": "unknown compiled input reference",
                "severity": "INVALID"})
            return None

        failed = False
        for step in plan.get("steps") or []:
            op = step.get("transform")
            if op in EXECUTION_UNSUPPORTED_TRANSFORMS:
                violations.append({
                    "field": "%s.%s" % (fid, step.get("step")),
                    "issue": "transform %r has no validated feature-level "
                    "implementation: the validated sector cap is the "
                    "portfolio-construction greedy cap applied at the "
                    "portfolio screen" % op,
                    "severity": "UNSUPPORTED"})
                failed = True
                break
            executor = TRANSFORM_EXECUTORS.get(op)
            if executor is None:
                violations.append({
                    "field": "%s.%s" % (fid, step.get("step")),
                    "issue": "unknown compiled operation %r (fixed registry "
                    "only; no dynamic dispatch)" % op,
                    "severity": "INVALID"})
                failed = True
                break
            resolved = [_resolve(r) for r in step.get("inputs") or []]
            if any(r is None for r in resolved):
                failed = True
                break
            raw = executor(panel, resolved, step.get("params") or {})
            clean, dropped = _drop_nonfinite(raw)
            env[step["step"]] = clean
            step_audits.append({
                "step": step["step"],
                "transform": op,
                "params": step.get("params") or {},
                "inputs": list(step.get("inputs") or []),
                "input_values": [_series_size(r) for r in resolved],
                "output_values": _series_size(clean),
                "output_months": len(clean),
                "dropped_nonfinite": dropped,
                "missing_policy": MISSING_VALUE_POLICY.get(op, ""),
            })
        if failed:
            break

        out_series = _resolve(plan.get("output")) if not failed else None
        if out_series is None:
            break
        computed[fid] = out_series
        features_out[fid] = {
            "signature": plan.get("signature"),
            "source_family": plan.get("source_family"),
            "coverage": _series_coverage(out_series),
            "steps": step_audits,
            "series": {
                m: {tk: out_series[m][tk] for tk in sorted(out_series[m])}
                for m in sorted(out_series)
            },
        }

    if violations:
        status = (
            "REJECTED_UNSUPPORTED"
            if all(v["severity"] == "UNSUPPORTED" for v in violations)
            else "REJECTED_INVALID"
        )
        return _reject(status, violations)

    # exactly one terminal (un-referenced) output feature per hypothesis
    referenced: set = set()
    for fid in by_id:
        referenced |= set(_deps_of(by_id[fid]))
    terminals = sorted(set(by_id) - referenced)
    if len(terminals) != 1:
        return _reject("REJECTED_INVALID", [{
            "field": "features",
            "issue": "a hypothesis must define exactly one terminal output "
            "feature (got %d: %s)" % (len(terminals), terminals),
            "severity": "INVALID"}])

    body = {fid: features_out[fid]["series"] for fid in sorted(features_out)}
    return {
        "executed": True,
        "status": "OK",
        "violations": [],
        "features": features_out,
        "terminal_feature_id": terminals[0],
        "feature_content_hash": content_hash(body),
        "schema_version": FEATURE_EXECUTION_SCHEMA_VERSION,
    }


# --------------------------------------------------------------------------- #
# Part B: feature-level point-in-time audit
# --------------------------------------------------------------------------- #
def _history_depth_months(plan: Dict[str, Any]) -> int:
    """Trailing history (months) a compiled feature needs — its effective lag."""
    depth_of: Dict[str, int] = {}

    def _ref_depth(ref: str) -> int:
        if ref.startswith("source:"):
            return 0
        if ref.startswith("feature:"):
            return 0  # cross-feature depth is audited on the referenced feature
        return depth_of.get(ref, 0)

    for step in plan.get("steps") or []:
        params = step.get("params") or {}
        own = 0
        op = step.get("transform")
        if op in ("lag", "difference", "percentage_change"):
            own = int(params.get("periods", 0))
        elif op in ("rolling_mean", "rolling_median", "rolling_std",
                    "rolling_slope", "zscore", "volatility_scale"):
            own = int(params.get("window", 1)) - 1
        child = max((_ref_depth(r) for r in step.get("inputs") or []), default=0)
        depth_of[step["step"]] = own + child
    return _ref_depth(plan.get("output") or "")


def build_pit_audit(
    compiled: Dict[str, Any],
    execution: Dict[str, Any],
    panel: FeaturePanel,
    inputs: Dict[str, Any],
    *,
    inventory: Dict[str, Any],
    truncation_probes: int = 2,
) -> Dict[str, Any]:
    """Persistable point-in-time audit for one executed feature set.

    Structural checks (sources/ops/months/cutoff) PLUS a dynamic
    truncation-invariance proof: recomputing every feature on a panel
    truncated at a probe month must reproduce the full-run values at all
    months up to that probe exactly — any dependence on later data
    (future joins, backfill, full-sample normalization) breaks equality.
    Any violation blocks the experiment.
    """
    checks: List[Dict[str, Any]] = []
    provenance = inputs.get("provenance") or {}
    formation = panel.formation_months
    max_formation = formation[-1] if formation else None
    max_realized = (
        fb._month_end(fb._next_month(max_formation)) if max_formation else None
    )

    # 1) every executed source is an approved numeric source, never a target
    used_sources: List[str] = []
    for plan in compiled.get("steps") or []:
        for step in plan.get("steps") or []:
            for ref in step.get("inputs") or []:
                if isinstance(ref, str) and ref.startswith("source:"):
                    used_sources.append(ref.split(":", 1)[1])
    used_sources = sorted(set(used_sources))
    bad_sources = [
        f for f in used_sources
        if _is_target_field(f) or f in NON_SOURCE_FIELDS
        or f not in (inventory.get("numeric_sources") or {})
    ]
    checks.append({
        "check": "sources_are_approved_numeric_never_target",
        "passed": not bad_sources,
        "violating_fields": bad_sources,
    })

    # 2) every feature month is a panel month on/before the cutoff
    beyond: List[str] = []
    for fid, f in (execution.get("features") or {}).items():
        for m in f.get("series") or {}:
            if m not in panel.months or fb._month_end(m) > panel.data_cutoff:
                beyond.append("%s@%s" % (fid, m))
    checks.append({
        "check": "feature_months_within_panel_and_cutoff",
        "passed": not beyond,
        "violations": beyond[:10],
    })

    # 3) compiled ops all belong to the fixed executable registry
    unknown_ops = sorted({
        step.get("transform")
        for plan in compiled.get("steps") or []
        for step in plan.get("steps") or []
        if step.get("transform") not in TRANSFORM_EXECUTORS
    })
    checks.append({
        "check": "compiled_ops_in_fixed_registry",
        "passed": not unknown_ops,
        "unknown_ops": unknown_ops,
    })

    # 4) dynamic truncation invariance (no future dependence of any kind)
    probe_months: List[str] = []
    if formation and truncation_probes > 0:
        idxs = sorted({len(formation) // 2, (3 * len(formation)) // 4,
                       len(formation) - 1})
        probe_months = [formation[i] for i in idxs][: int(truncation_probes)]
    truncation_failures: List[Dict[str, Any]] = []
    if execution.get("executed") and probe_months:
        for probe in probe_months:
            t_panel = FeaturePanel(inputs, max_month=probe)
            t_exec = execute_feature_set(compiled, t_panel, inventory)
            if not t_exec.get("executed"):
                truncation_failures.append({
                    "probe_month": probe,
                    "issue": "truncated rerun failed to execute",
                })
                continue
            for fid, f in (execution.get("features") or {}).items():
                t_series = ((t_exec.get("features") or {}).get(fid) or {}).get(
                    "series") or {}
                for m, row in (f.get("series") or {}).items():
                    if m > probe:
                        continue
                    if t_series.get(m) != row:
                        truncation_failures.append({
                            "probe_month": probe,
                            "feature_id": fid,
                            "month": m,
                            "issue": "values changed when the panel was "
                            "truncated: future data influenced this month",
                        })
                        break
    checks.append({
        "check": "truncation_invariance_no_future_dependence",
        "passed": (not truncation_failures) if probe_months else None,
        "probe_months": probe_months,
        "failures": truncation_failures[:10],
    })

    windows = {
        plan.get("feature_id"): {
            "transform_windows": [
                {"step": s.get("step"), "transform": s.get("transform"),
                 "params": s.get("params") or {}}
                for s in plan.get("steps") or []
            ],
            "effective_lag_months": _history_depth_months(plan),
        }
        for plan in compiled.get("steps") or []
    }

    hard_failures = [c["check"] for c in checks if c["passed"] is False]
    return {
        "schema_version": FEATURE_EXECUTION_SCHEMA_VERSION,
        "record_type": "FEATURE_PIT_AUDIT",
        "data_cutoff": panel.data_cutoff,
        "min_source_month": panel.months[0] if panel.months else None,
        "max_source_month": panel.months[-1] if panel.months else None,
        "max_formation_month": max_formation,
        "max_realized_return_date": max_realized,
        "transformation_windows": windows,
        "target_join_timing": "realized fwd_1m is joined ONLY at evaluation, "
        "after feature construction, on formation months whose forward window "
        "completed on/before the data cutoff",
        "source_fields_used": used_sources,
        "source_hashes": provenance.get("sha256"),
        "checks": checks,
        "violations": hard_failures,
        "pit_ok": not hard_failures,
    }


__all__ = [
    "EXECUTION_UNSUPPORTED_TRANSFORMS",
    "FEATURE_EXECUTION_SCHEMA_VERSION",
    "FeatureExecutionError",
    "FeaturePanel",
    "MISSING_VALUE_POLICY",
    "TRANSFORM_EXECUTORS",
    "build_pit_audit",
    "build_source_inventory",
    "execute_feature_set",
]
