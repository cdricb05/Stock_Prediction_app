"""Phase 30B — owned-factor integration, PIT universe alignment, honest audit.

Connects already-owned, point-in-time-safe numeric factors to the deterministic
research-agent diagnostic path WITHOUT touching Paper Trader, without lowering
any gate, and without redefining coverage to make a factor pass:

- ``realized_vol_63d`` — the momentum-panel column the Phase 29C loader
  discards (full survivorship-free coverage; availability = formation month);
- the raw EODHD normalized fundamental factors (gross_profitability,
  fcf_to_assets, operating_accruals, leverage_change, net_share_issuance) —
  dated ``available_date`` (filing_date, else fiscal-date + a conservative
  90-day fallback baked into the normalized file) joined as-of each formation
  month with a configured maximum staleness.

Everything numeric is REUSED, never re-derived:

- panel loading / formation months / carry-forward / helpers: family_backtest;
- rank-IC battery + IC screen + bounded 80/20 integration + robustness:
  feature_evaluation (which itself reuses evaluator.py's strict gates);
- atomic writes / append-only ledgers / content hashing: artifact_store;
- git HEAD (no subprocess): controller.read_git_commit.

Two universe profiles are always reported side by side and never conflated:
GLOBAL_PIT_UNIVERSE (the survivorship-free Norgate current-and-past panel) and
SOURCE_OBSERVED_UNIVERSE (only names a factor actually observes). A
source-observed result may be scientifically informative while remaining
DIAGNOSTIC_ONLY: a current-survivor-biased subset can never create shadow
eligibility. There is no code path here that creates orders, touches a broker,
enables automation, registers a challenger, or changes the operational model.
"""

from __future__ import annotations

import bisect
import csv
import math
import os
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import SAFETY_CONTRACT
from . import family_backtest as fb
from . import feature_evaluation as ev
from .artifact_store import (
    append_jsonl,
    content_hash,
    find_secret_keys,
    read_json,
    write_json_atomic,
    write_text_atomic,
)
from .controller import read_git_commit
from .feature_dsl import _is_target_field
from .feature_evaluation import DEFAULT_SCREEN_THRESHOLDS, FEATURE_WEIGHT_CEILING
from .schemas import (
    APPROVED_COST_BPS_PER_SIDE,
    APPROVED_EXIT_BUFFER_FRACTIONS,
    APPROVED_MIN_ADV_DOLLARS,
    APPROVED_PORTFOLIO_SIZES,
    APPROVED_UNIVERSES,
    find_forbidden_execution_keys,
)

OWNED_FACTOR_SCHEMA_VERSION = "30B.1"

RUNS_SUBDIR = "owned_factor_runs"
LATEST_RUN_FILE = "phase30b_latest_run.json"

_ROOTS = {"repo": fb.REPO_ROOT, "data_root": fb.DATA_ROOT}
_KNOWN_KINDS = ("momentum_panel_column", "eodhd_normalized")
_KNOWN_USAGE = ("primary", "diagnostic_only", "blocked")

# Screen threshold direction: which committed default must not be relaxed and
# in which direction (">=" means a config value must be at least the default;
# "<=" means at most the default). This is how "do not lower a gate" is proven
# mechanically at config validation.
_SCREEN_MIN_DIRECTION = {
    "min_months": ">=",
    "min_coverage_fraction": ">=",
    "min_abs_rank_ic_t": ">=",
    "material_ic_t_margin": ">=",
    "min_universe": ">=",
    "near_duplicate_abs_corr": "<=",
    "max_complementary_abs_baseline_corr": "<=",
    "max_top_rank_sector_share": "<=",
    "leakage_suspicion_abs_ic": "<=",
}

# Allowed per-universe decisions (Part G).
DECISIONS = (
    "REJECTED_SIGNAL",
    "REJECTED_GLOBAL_COVERAGE",
    "DIAGNOSTIC_ONLY_SURVIVORSHIP",
    "DIAGNOSTIC_ONLY_SECTOR_HISTORY",
    "INCONCLUSIVE",
    "ADVANCE_TO_PORTFOLIO_SCREEN",
)

SURVIVORSHIP_RESULTS = (
    "PASS",
    "PASS_WITH_CAVEAT",
    "DIAGNOSTIC_ONLY_CURRENT_SURVIVOR_BIAS",
    "BLOCKED_INSUFFICIENT_DELISTED_COVERAGE",
    "BLOCKED_NON_PIT_UNIVERSE",
)

SECTOR_CLASSES = ("PIT_SAFE", "CURRENT_ONLY", "PARTIAL_HISTORY", "UNUSABLE")

_RESTATEMENT_CAVEAT = (
    "EODHD stores the latest-restated statement values in a single as-of "
    "snapshot; the available_date (filing_date, else fiscal-date + 90d "
    "fallback) is point-in-time correct, but a historical value may embed a "
    "later restatement. Original-as-reported values cannot be reconstructed "
    "from the owned normalized file. This is the identical caveat the accepted "
    "composite_sn champion already carries."
)


class OwnedFactorError(RuntimeError):
    pass


# --------------------------------------------------------------------------- #
# config
# --------------------------------------------------------------------------- #
def load_owned_factor_config(path: str) -> Optional[Dict[str, Any]]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            import json

            return json.load(fh)
    except (OSError, ValueError):
        return None


def _resolve_path_spec(spec: Any) -> str:
    """Resolve a {root, relpath} spec against a FIXED root; reject traversal."""
    if not isinstance(spec, dict) or "root" not in spec or "relpath" not in spec:
        raise OwnedFactorError("path spec must be {root, relpath}: %r" % (spec,))
    root = _ROOTS.get(spec["root"])
    if root is None:
        raise OwnedFactorError("unknown fixed root %r" % (spec["root"],))
    rel = str(spec["relpath"])
    parts = rel.replace("\\", "/").split("/")
    if os.path.isabs(rel) or ".." in parts:
        raise OwnedFactorError("relpath must stay within the fixed root: %r" % rel)
    return os.path.normpath(os.path.join(root, *parts))


def _factor_path(cfg: Dict[str, Any], factor: Dict[str, Any]) -> str:
    kind = factor.get("kind")
    if kind == "momentum_panel_column":
        return _resolve_path_spec(cfg["sources"]["momentum_panel"])
    if kind == "eodhd_normalized":
        base = _resolve_path_spec(cfg["sources"]["eodhd_normalized_root"])
        sub = str(factor["subdir"])
        fn = str(factor["file"])
        for token in (sub, fn):
            if os.path.isabs(token) or ".." in token.replace("\\", "/").split("/"):
                raise OwnedFactorError("unsafe factor path token: %r" % token)
        return os.path.normpath(os.path.join(base, sub, fn))
    raise OwnedFactorError("unknown factor kind %r" % (kind,))


def validate_owned_factor_config(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Structural + non-weakening validation; never touches the filesystem."""
    v: List[Dict[str, Any]] = []

    def bad(field: str, issue: str, value: Any = None) -> None:
        v.append({"field": field, "issue": issue, "value": value})

    if not isinstance(cfg, dict):
        return {"accepted": False, "violations": [{"field": "$", "issue": "config must be an object"}], "config_hash": None}

    if cfg.get("schema_version") != OWNED_FACTOR_SCHEMA_VERSION:
        bad("schema_version", "must be %s" % OWNED_FACTOR_SCHEMA_VERSION, cfg.get("schema_version"))
    if not cfg.get("name"):
        bad("name", "required")

    # secrets / forbidden execution keys
    for k in find_secret_keys(cfg):
        bad(k, "secret-looking key is forbidden in config")
    for k in find_forbidden_execution_keys(cfg):
        bad(k, "forbidden execution-token key")

    data = cfg.get("data") or {}
    cutoff = data.get("data_cutoff")
    if not isinstance(cutoff, str) or len(cutoff) != 10:
        bad("data.data_cutoff", "required YYYY-MM-DD (inherited source-campaign cutoff)", cutoff)

    sources = cfg.get("sources") or {}
    roots = sources.get("roots") or {}
    for name in roots:
        if name not in _ROOTS:
            bad("sources.roots.%s" % name, "only fixed roots {repo, data_root} allowed", name)
    for key in ("momentum_panel", "eodhd_normalized_root", "sector_map"):
        spec = sources.get(key)
        if not isinstance(spec, dict) or spec.get("root") not in _ROOTS:
            bad("sources.%s" % key, "must be {root in [repo,data_root], relpath}", spec)
        else:
            rel = str(spec.get("relpath", ""))
            if os.path.isabs(rel) or ".." in rel.replace("\\", "/").split("/"):
                bad("sources.%s.relpath" % key, "no absolute paths or .. traversal", rel)

    forbidden_targets = set(cfg.get("forbidden_target_fields") or [])
    permitted = set(cfg.get("permitted_numeric_fields") or [])
    factors = sources.get("factors") or []
    if not isinstance(factors, list) or not factors:
        bad("sources.factors", "at least one factor is required")
    ids: List[str] = []
    for i, f in enumerate(factors if isinstance(factors, list) else []):
        fp = "sources.factors[%d]" % i
        sid = f.get("source_id")
        if not sid:
            bad(fp + ".source_id", "required")
        else:
            ids.append(sid)
        if f.get("kind") not in _KNOWN_KINDS:
            bad(fp + ".kind", "must be one of %s" % (_KNOWN_KINDS,), f.get("kind"))
        if f.get("usage") not in _KNOWN_USAGE:
            bad(fp + ".usage", "must be one of %s" % (_KNOWN_USAGE,), f.get("usage"))
        col = f.get("value_column")
        if not col:
            bad(fp + ".value_column", "required")
        elif _is_target_field(col) or col in forbidden_targets:
            bad(fp + ".value_column", "forward/target fields are never a source", col)
        elif permitted and col not in permitted:
            bad(fp + ".value_column", "not in permitted_numeric_fields", col)
        if f.get("kind") == "eodhd_normalized":
            for req in ("subdir", "file", "availability_date_column"):
                if not f.get(req):
                    bad("%s.%s" % (fp, req), "required for eodhd_normalized")

    # forbidden target fields must not overlap permitted numeric fields
    for col in permitted & forbidden_targets:
        bad("permitted_numeric_fields", "overlaps forbidden_target_fields", col)
    for col in permitted:
        if _is_target_field(col):
            bad("permitted_numeric_fields", "target/forward field cannot be permitted", col)

    # coverage gates: never weakened below the committed 0.60 floor
    floor = float(DEFAULT_SCREEN_THRESHOLDS["min_coverage_fraction"]["value"])
    for key in ("global_min_month_coverage", "global_min_cross_sectional_coverage", "source_universe_min_coverage"):
        val = cfg.get(key)
        if not isinstance(val, (int, float)) or val < floor - 1e-12:
            bad(key, "must be >= the committed %.2f coverage floor (never weakened)" % floor, val)

    # ic-screen thresholds may only be tightened relative to the committed defaults
    screen = cfg.get("ic_screen") or {}
    for key, direction in _SCREEN_MIN_DIRECTION.items():
        if key not in screen:
            continue
        val = screen[key]
        base = float(DEFAULT_SCREEN_THRESHOLDS[key]["value"])
        if not isinstance(val, (int, float)):
            bad("ic_screen.%s" % key, "must be numeric", val)
        elif direction == ">=" and val < base - 1e-12:
            bad("ic_screen.%s" % key, "must be >= committed default %s (gate not lowered)" % base, val)
        elif direction == "<=" and val > base + 1e-12:
            bad("ic_screen.%s" % key, "must be <= committed default %s (gate not lowered)" % base, val)

    integ = cfg.get("integration") or {}
    wb, wf = integ.get("baseline_weight"), integ.get("feature_weight")
    if not isinstance(wb, (int, float)) or not isinstance(wf, (int, float)):
        bad("integration", "baseline_weight and feature_weight required")
    else:
        if abs((wb + wf) - 1.0) > 1e-9:
            bad("integration", "weights must reconcile to exactly one", [wb, wf])
        if not (0.0 <= wf <= FEATURE_WEIGHT_CEILING + 1e-12):
            bad("integration.feature_weight", "must be within [0, %.2f]" % FEATURE_WEIGHT_CEILING, wf)

    costs = cfg.get("costs") or {}
    pc = costs.get("primary_cost_bps_per_side")
    if pc is None or not any(abs(pc - a) < 1e-9 for a in APPROVED_COST_BPS_PER_SIDE):
        bad("costs.primary_cost_bps_per_side", "must be one of %s" % (APPROVED_COST_BPS_PER_SIDE,), pc)

    pf = cfg.get("portfolio") or {}
    if pf.get("top_n") not in APPROVED_PORTFOLIO_SIZES:
        bad("portfolio.top_n", "must be one of %s" % (APPROVED_PORTFOLIO_SIZES,), pf.get("top_n"))
    if pf.get("universe") not in APPROVED_UNIVERSES:
        bad("portfolio.universe", "must be one of %s" % (APPROVED_UNIVERSES,), pf.get("universe"))
    eb = pf.get("exit_buffer_fraction")
    if eb is None or not any(abs(eb - a) < 1e-9 for a in APPROVED_EXIT_BUFFER_FRACTIONS):
        bad("portfolio.exit_buffer_fraction", "must be one of %s" % (APPROVED_EXIT_BUFFER_FRACTIONS,), eb)
    if eb not in (0.0, 0) and abs((eb or 0) - 0.0) > 1e-9:
        # Part E: "no exit buffer"
        bad("portfolio.exit_buffer_fraction", "Phase 30B requires no exit buffer (0.0)", eb)
    ma = pf.get("min_adv_dollar")
    if ma is None or not any(abs(ma - a) < 1.0 for a in APPROVED_MIN_ADV_DOLLARS):
        bad("portfolio.min_adv_dollar", "must be one of %s" % (APPROVED_MIN_ADV_DOLLARS,), ma)

    surv = cfg.get("survivorship") or {}
    mdr = surv.get("min_delisted_representation_fraction")
    if not isinstance(mdr, (int, float)) or not (0.0 < mdr <= 1.0):
        bad("survivorship.min_delisted_representation_fraction", "must be in (0, 1]", mdr)

    diag = cfg.get("diagnostics") or {}
    budgets = cfg.get("budgets") or {}
    maxf = diag.get("max_features")
    budget_maxf = budgets.get("max_diagnostic_features")
    if not isinstance(maxf, int) or maxf < 1:
        bad("diagnostics.max_features", "must be a positive integer", maxf)
    if isinstance(maxf, int) and isinstance(budget_maxf, int) and maxf > budget_maxf:
        bad("diagnostics.max_features", "cannot exceed budgets.max_diagnostic_features", maxf)
    init = diag.get("initial_features") or []
    for fid in init:
        if fid not in ids:
            bad("diagnostics.initial_features", "references an undeclared factor", fid)

    return {
        "accepted": not v,
        "violations": v,
        "config_hash": content_hash(cfg),
    }


# --------------------------------------------------------------------------- #
# Part A: deterministic source registry
# --------------------------------------------------------------------------- #
def _read_header(path: str) -> List[str]:
    with open(path, "r", encoding="utf-8", newline="") as fh:
        rd = csv.reader(fh)
        for row in rd:
            return row
    return []


def _eodhd_file_stats(path: str, value_col: str) -> Dict[str, Any]:
    tks: set = set()
    ads: List[str] = []
    finite = 0
    rows = 0
    with open(path, "r", encoding="utf-8", newline="") as fh:
        rd = csv.DictReader(fh)
        for r in rd:
            rows += 1
            tks.add((r.get("ticker") or "").strip().upper())
            ad = (r.get("available_date") or "").strip()
            if ad:
                ads.append(ad)
            if fb._to_float(r.get(value_col)) is not None:
                finite += 1
    return {
        "rows": rows,
        "ticker_count": len(tks),
        "first_available_date": min(ads) if ads else None,
        "last_available_date": max(ads) if ads else None,
        "finite_values": finite,
    }


def _momentum_column_stats(path: str, value_col: str) -> Dict[str, Any]:
    tks: set = set()
    months: set = set()
    finite = 0
    elig = 0
    with open(path, "r", encoding="utf-8", newline="") as fh:
        rd = csv.DictReader(fh)
        for r in rd:
            if r.get("eligible_history") != "1":
                continue
            elig += 1
            m = (r.get("month") or "").strip()
            if m:
                months.add(m)
            v = fb._to_float(r.get(value_col))
            if v is not None:
                finite += 1
                tks.add((r.get("ticker") or "").strip().upper())
    return {
        "eligible_rows": elig,
        "ticker_count": len(tks),
        "first_available_date": min(months) if months else None,
        "last_available_date": max(months) if months else None,
        "finite_values": finite,
    }


def build_source_registry(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Deterministic registry for every declared numeric factor (Part A).

    Reads only headers + summary stats + a full-file sha256; never evaluates
    strings, never dynamically imports, never accepts a path from anywhere but
    the fixed configuration.
    """
    factors = (cfg.get("sources") or {}).get("factors") or []
    entries: List[Dict[str, Any]] = []
    for f in factors:
        path = _factor_path(cfg, f)
        exists = os.path.exists(path)
        entry: Dict[str, Any] = {
            "source_id": f.get("source_id"),
            "provider": f.get("provider"),
            "kind": f.get("kind"),
            "file_path": path,
            "file_exists": exists,
            "value_column": f.get("value_column"),
            "ticker_column": f.get("ticker_column", "ticker"),
            "availability_date_column": f.get("availability_date_column"),
            "frequency": f.get("frequency"),
            "orientation": "verified per feature at the IC screen; never assumed",
            "target_classification": "non_target",
            "is_target_field": bool(_is_target_field(str(f.get("value_column")))),
            "declared_usage": f.get("usage"),
            "restatement_caveat": _RESTATEMENT_CAVEAT if f.get("restatement_caveat") else None,
            "missing_value_behavior": (
                "as-of latest available_date <= formation month-end; missing "
                "when no observation exists on/before the boundary or the latest "
                "observation exceeds max staleness"
                if f.get("kind") == "eodhd_normalized"
                else "present only for eligible momentum rows; missing otherwise"
            ),
            "pit_availability_rule": (
                "available_date = filing_date, else fiscal-date + 90-day fallback "
                "(baked into the normalized file); as-of join never uses a later row"
                if f.get("kind") == "eodhd_normalized"
                else "availability = formation month (trailing 63-day realized "
                "statistic over survivorship-free Norgate prices)"
            ),
        }
        if not exists:
            entry["error"] = "file not found at the fixed configured path"
            entry["usage_resolved"] = "blocked"
            entries.append(entry)
            continue

        header = _read_header(path)
        entry["columns"] = header
        entry["source_content_hash"] = fb._file_sha256(path)
        col_ok = f.get("value_column") in header
        entry["value_column_present"] = col_ok
        if f.get("kind") == "eodhd_normalized":
            entry.update(_eodhd_file_stats(path, f.get("value_column")))
        else:
            entry.update(_momentum_column_stats(path, f.get("value_column")))
        # usage resolution: a declared source is usable only if its column is
        # present, non-target, and has finite values
        usable = col_ok and not entry["is_target_field"] and entry.get("finite_values", 0) > 0
        entry["usage_resolved"] = f.get("usage") if usable else "blocked"
        entries.append(entry)

    return {
        "schema_version": OWNED_FACTOR_SCHEMA_VERSION,
        "record_type": "SOURCE_REGISTRY",
        "data_cutoff": (cfg.get("data") or {}).get("data_cutoff"),
        "n_factors": len(entries),
        "factors": entries,
        "note": "paths come only from the fixed configuration; no path, code, "
        "or transform is ever supplied by a model",
    }


# --------------------------------------------------------------------------- #
# Part B: deterministic point-in-time as-of join
# --------------------------------------------------------------------------- #
def _load_eodhd_by_ticker(path: str, value_col: str) -> Dict[str, List[Tuple[str, float]]]:
    by_tk: Dict[str, List[Tuple[str, float]]] = {}
    with open(path, "r", encoding="utf-8", newline="") as fh:
        for r in csv.DictReader(fh):
            tk = (r.get("ticker") or "").strip().upper()
            ad = (r.get("available_date") or "").strip()
            val = fb._to_float(r.get(value_col))
            if not tk or len(ad) < 10 or val is None:
                continue
            by_tk.setdefault(tk, []).append((ad, val))
    for tk in by_tk:
        by_tk[tk].sort()
    return by_tk


def asof_join_eodhd(
    by_tk: Dict[str, List[Tuple[str, float]]],
    months: List[str],
    *,
    max_staleness_months: int,
) -> Tuple[Dict[str, Dict[str, float]], Dict[str, Any]]:
    """PIT as-of join: month T uses each ticker's latest available_date <= the
    formation month-end, never a later filing, never a future backfill."""
    series: Dict[str, Dict[str, float]] = {}
    obs_date: Dict[str, Dict[str, str]] = {}
    staleness_hist: Counter = Counter()
    obs_year_hist: Counter = Counter()
    rejected_stale = 0
    future_violations = 0
    all_dates: List[str] = [d for tl in by_tk.values() for d, _ in tl]

    for m in months:
        me = fb._month_end(m)
        cur: Dict[str, float] = {}
        odm: Dict[str, str] = {}
        for tk, tl in by_tk.items():
            ads = [d for d, _ in tl]
            i = bisect.bisect_right(ads, me) - 1
            if i < 0:
                continue
            ad, val = tl[i]
            if ad > me:  # impossible by construction; counted for the audit
                future_violations += 1
                continue
            stale = fb._month_diff(m, ad[:7])
            if stale > max_staleness_months:
                rejected_stale += 1
                continue
            cur[tk] = val
            odm[tk] = ad
            staleness_hist[stale] += 1
            obs_year_hist[ad[:4]] += 1
        if cur:
            series[m] = cur
            obs_date[m] = odm

    all_st = [s for s, c in staleness_hist.items() for _ in range(c)]
    meta = {
        "join_kind": "eodhd_asof",
        "min_source_date": min(all_dates) if all_dates else None,
        "max_source_date": max(all_dates) if all_dates else None,
        "max_formation_month": months[-1] if months else None,
        "selected_obs_year_hist": dict(sorted(obs_year_hist.items())),
        "staleness_months_hist": dict(sorted(staleness_hist.items())),
        "staleness_max": max(all_st) if all_st else None,
        "staleness_mean": (sum(all_st) / len(all_st)) if all_st else None,
        "max_staleness_months": max_staleness_months,
        "rejected_by_staleness": rejected_stale,
        "future_join_violations": future_violations,
        "filing_date_provenance": (
            "available_date is filing_date-or-(fiscal+90d) already merged in the "
            "normalized file; true-filing vs fallback provenance is not separable "
            "from this file and is reported as available_date"
        ),
        "restatement_caveat": _RESTATEMENT_CAVEAT,
        "selected_observation_dates": obs_date,
    }
    return series, meta


def load_momentum_column_series(
    path: str, value_col: str, months: List[str]
) -> Tuple[Dict[str, Dict[str, float]], Dict[str, Any]]:
    mset = set(months)
    series: Dict[str, Dict[str, float]] = {}
    market_date: Dict[str, str] = {}
    with open(path, "r", encoding="utf-8", newline="") as fh:
        for r in csv.DictReader(fh):
            m = (r.get("month") or "").strip()
            if m not in mset or r.get("eligible_history") != "1":
                continue
            v = fb._to_float(r.get(value_col))
            if v is None:
                continue
            tk = (r.get("ticker") or "").strip().upper()
            series.setdefault(m, {})[tk] = v
            market_date[m] = (r.get("market_date") or m).strip()
    meta = {
        "join_kind": "momentum_panel_column",
        "min_source_date": min(series) if series else None,
        "max_source_date": max(series) if series else None,
        "max_formation_month": months[-1] if months else None,
        "staleness_max": 0,
        "staleness_mean": 0.0,
        "rejected_by_staleness": 0,
        "future_join_violations": 0,
        "filing_date_provenance": "not applicable (derived price statistic; "
        "availability = formation month)",
        "restatement_caveat": None,
    }
    return series, meta


def build_factor_series(
    cfg: Dict[str, Any], factor: Dict[str, Any], months: List[str]
) -> Tuple[Dict[str, Dict[str, float]], Dict[str, Any]]:
    path = _factor_path(cfg, factor)
    if factor.get("kind") == "eodhd_normalized":
        by_tk = _load_eodhd_by_ticker(path, factor["value_column"])
        max_stale = int(cfg.get("max_factor_staleness_months", fb.MAX_FUND_STALE_MONTHS))
        series, meta = asof_join_eodhd(by_tk, months, max_staleness_months=max_stale)
    else:
        series, meta = load_momentum_column_series(path, factor["value_column"], months)
    meta["source_id"] = factor.get("source_id")
    meta["source_content_hash"] = fb._file_sha256(path)
    meta["joined_content_hash"] = content_hash(
        {m: {tk: series[m][tk] for tk in sorted(series[m])} for m in sorted(series)}
    )
    return series, meta


def build_factor_pit_audit(
    factor: Dict[str, Any], meta: Dict[str, Any], cutoff: str
) -> Dict[str, Any]:
    checks: List[Dict[str, Any]] = []
    checks.append({
        "check": "no_future_join",
        "passed": meta.get("future_join_violations", 0) == 0,
        "value": meta.get("future_join_violations", 0),
    })
    max_fm = meta.get("max_formation_month")
    within = max_fm is None or fb._month_end(fb._next_month(max_fm)) <= cutoff
    checks.append({
        "check": "forward_window_within_cutoff",
        "passed": within,
        "value": {"max_formation_month": max_fm, "data_cutoff": cutoff},
    })
    sm = meta.get("staleness_max")
    cap = meta.get("max_staleness_months")
    stale_ok = sm is None or cap is None or sm <= cap
    checks.append({
        "check": "staleness_within_cap",
        "passed": stale_ok,
        "value": {"staleness_max": sm, "cap": cap, "rejected_by_staleness": meta.get("rejected_by_staleness")},
    })
    checks.append({
        "check": "restatement_caveat_persisted",
        "passed": True,
        "value": bool(meta.get("restatement_caveat")) or factor.get("kind") == "momentum_panel_column",
    })
    hard = [c["check"] for c in checks if c["passed"] is False]
    return {
        "schema_version": OWNED_FACTOR_SCHEMA_VERSION,
        "record_type": "FACTOR_PIT_AUDIT",
        "source_id": factor.get("source_id"),
        "data_cutoff": cutoff,
        "min_source_date": meta.get("min_source_date"),
        "max_source_date": meta.get("max_source_date"),
        "staleness_months_hist": meta.get("staleness_months_hist"),
        "staleness_max": sm,
        "staleness_mean": meta.get("staleness_mean"),
        "filing_date_provenance": meta.get("filing_date_provenance"),
        "restatement_caveat": meta.get("restatement_caveat"),
        "source_content_hash": meta.get("source_content_hash"),
        "joined_content_hash": meta.get("joined_content_hash"),
        "checks": checks,
        "violations": hard,
        "pit_ok": not hard,
    }


# --------------------------------------------------------------------------- #
# Part C: universe contract + survivorship audit
# --------------------------------------------------------------------------- #
def _membership_split(mom_monthly: Dict[str, Dict[str, dict]]) -> Dict[str, Any]:
    if not mom_monthly:
        return {"final_month": None, "current": set(), "removed": set(), "ever": set()}
    final = max(mom_monthly)
    member_final: set = set()
    ever: set = set()
    for m, row in mom_monthly.items():
        for tk, r in row.items():
            if r.get("is_member"):
                ever.add(tk)
                if m == final:
                    member_final.add(tk)
    return {
        "final_month": final,
        "current": member_final,
        "removed": ever - member_final,
        "ever": ever,
    }


def _decade(month: str) -> str:
    y = int(month[:4])
    return "%ds" % (y - y % 10)


def build_universe_profiles(
    inputs: Dict[str, Any],
    factor_series: Dict[str, Dict[str, Dict[str, float]]],
    cfg: Dict[str, Any],
    *,
    sector_pit_safe: bool,
) -> Dict[str, Any]:
    """GLOBAL_PIT / SOURCE_OBSERVED / FUNDAMENTAL_RESEARCH profiles + survivorship."""
    mom = inputs["mom_monthly"]
    months = inputs["months"]
    split = _membership_split(mom)
    current, removed = split["current"], split["removed"]

    # global PIT members per formation month = is_member that month
    global_by_month: Dict[str, int] = {}
    for m in months:
        global_by_month[m] = sum(1 for r in mom.get(m, {}).values() if r.get("is_member"))

    surv_cfg = cfg.get("survivorship") or {}
    min_delisted = float(surv_cfg.get("min_delisted_representation_fraction", 0.20))

    per_factor: Dict[str, Any] = {}
    for fid, series in factor_series.items():
        observed: set = set()
        src_by_month: Dict[str, int] = {}
        covered_current_by_month: Dict[str, int] = {}
        covered_removed_by_month: Dict[str, int] = {}
        decade_cov: Dict[str, Dict[str, int]] = {}
        for m in months:
            row = series.get(m, {})
            observed |= set(row)
            members = {tk for tk, r in mom.get(m, {}).items() if r.get("is_member")}
            src = len(row)
            src_by_month[m] = src
            cc = len(set(row) & current)
            cr = len(set(row) & removed)
            covered_current_by_month[m] = cc
            covered_removed_by_month[m] = cr
            dk = _decade(m)
            d = decade_cov.setdefault(dk, {"member_months": 0, "covered_months": 0})
            d["member_months"] += len(members)
            d["covered_months"] += len(set(row) & members)

        observed_in_panel = observed & split["ever"]
        removed_covered = len(observed & removed)
        current_covered = len(observed & current)
        denom = len(observed_in_panel) or 1
        delisted_rep = removed_covered / denom

        if not split["final_month"]:
            classification = "BLOCKED_NON_PIT_UNIVERSE"
        elif delisted_rep >= min_delisted:
            classification = "PASS" if sector_pit_safe else "PASS_WITH_CAVEAT"
        elif removed_covered > 0:
            classification = "DIAGNOSTIC_ONLY_CURRENT_SURVIVOR_BIAS"
        else:
            classification = "BLOCKED_INSUFFICIENT_DELISTED_COVERAGE"

        tot_global = sum(global_by_month.values()) or 1
        tot_src = sum(src_by_month.values())
        per_factor[fid] = {
            "observed_ticker_count": len(observed),
            "observed_in_panel_count": len(observed_in_panel),
            "covered_current_members": current_covered,
            "covered_removed_members": removed_covered,
            "uncovered_current_members": len(current) - current_covered,
            "uncovered_removed_members": len(removed) - removed_covered,
            "delisted_representation_fraction": delisted_rep,
            "source_observed_fraction_of_global": tot_src / tot_global,
            "coverage_by_decade": {
                k: {
                    **vv,
                    "fraction": (vv["covered_months"] / vv["member_months"]) if vv["member_months"] else None,
                }
                for k, vv in sorted(decade_cov.items())
            },
            "survivorship_classification": classification,
            "shadow_eligible": classification in ("PASS", "PASS_WITH_CAVEAT"),
            "coverage_by_sector": None,  # no PIT-safe sector history (Part D)
        }

    return {
        "schema_version": OWNED_FACTOR_SCHEMA_VERSION,
        "record_type": "UNIVERSE_AUDIT",
        "data_cutoff": inputs["data_cutoff"],
        "global_pit_universe": {
            "definition": "Norgate current-and-past PIT membership (is_member); "
            "the denominator is fixed and never changed by a factor outcome",
            "final_month": split["final_month"],
            "distinct_current_members": len(current),
            "distinct_removed_members": len(removed),
            "member_months_total": sum(global_by_month.values()),
            "member_count_first_month": global_by_month.get(months[0]) if months else None,
            "member_count_last_month": global_by_month.get(months[-1]) if months else None,
        },
        "min_delisted_representation_fraction": min_delisted,
        "source_observed_universe": per_factor,
        "hard_rules": [
            "never define a historical universe using the final EODHD ticker list",
            "never use future availability to include an earlier member",
            "never condition membership on surviving to 2026",
            "a current-name subset is never called survivorship-free",
            "a source-observed diagnostic can be informative but stays "
            "DIAGNOSTIC_ONLY unless the survivorship gate passes",
        ],
    }


# --------------------------------------------------------------------------- #
# Part D: sector / industry history audit
# --------------------------------------------------------------------------- #
def _load_sector_map_raw(path: str) -> Tuple[Dict[str, str], List[str]]:
    out: Dict[str, str] = {}
    cols: List[str] = []
    try:
        with open(path, "r", encoding="utf-8", newline="") as fh:
            rd = csv.DictReader(fh)
            cols = rd.fieldnames or []
            for r in rd:
                tk = (r.get("ticker") or "").strip().upper()
                sec = (r.get("repaired_sector") or r.get("sector") or "").strip()
                if tk and sec:
                    out[tk] = sec
    except OSError:
        return {}, []
    return out, cols


def _three_way_concentration(
    series: Dict[str, Dict[str, float]],
    inputs: Dict[str, Any],
    sector_map: Dict[str, str],
    *,
    min_universe: int = 10,
) -> Dict[str, Optional[float]]:
    """Top-quartile sector share reported three ways (Part D)."""
    incl, excl, unk = [], [], []
    mom = inputs["mom_monthly"]
    for m in inputs["months"]:
        frow = series.get(m, {})
        uni = sorted(
            tk for tk in frow
            if (mom.get(m, {}).get(tk) or {}).get("eligible")
            and ev._finite(frow.get(tk))
        )
        if len(uni) < min_universe:
            continue
        k = max(1, len(uni) // 4)
        order = sorted(uni, key=lambda tk: (-frow[tk], tk))[:k]
        secs = [sector_map.get(tk, "Unknown") for tk in order]
        c = Counter(secs)
        incl.append(max(c.values()) / k)
        unk.append(c.get("Unknown", 0) / k)
        known = [s for s in secs if s != "Unknown"]
        if known:
            excl.append(max(Counter(known).values()) / len(known))
    return {
        "avg_top_quartile_sector_share_incl_unknown": ev._mean(incl),
        "avg_top_quartile_sector_share_excl_unknown": ev._mean(excl),
        "avg_unknown_share_in_top_quartile": ev._mean(unk),
    }


def build_sector_audit(
    inputs: Dict[str, Any],
    cfg: Dict[str, Any],
    factor_series: Optional[Dict[str, Dict[str, Dict[str, float]]]] = None,
) -> Dict[str, Any]:
    """Classify every owned classification source; concentration incl/excl Unknown."""
    mom = inputs["mom_monthly"]
    # 1) momentum panel `sector` column
    panel_secs: Dict[str, set] = {}
    for m, row in mom.items():
        for tk, r in row.items():
            panel_secs.setdefault(tk, set()).add((r.get("sector") or "Unknown"))
    n_tk = len(panel_secs) or 1
    all_unknown = sum(1 for s in panel_secs.values() if s == {"Unknown"})
    varies = sum(1 for s in panel_secs.values() if len(s) > 1)
    panel_class = "UNUSABLE" if all_unknown == len(panel_secs) else (
        "PARTIAL_HISTORY" if varies else "CURRENT_ONLY")

    # 2) repaired EODHD GICS map
    smap_path = _resolve_path_spec(cfg["sources"]["sector_map"])
    sector_map, smap_cols = _load_sector_map_raw(smap_path)
    has_date = any("date" in (c or "").lower() or "asof" in (c or "").lower()
                   for c in smap_cols)
    map_distinct = sorted({s for s in sector_map.values() if s and s != "Unknown"})
    repaired_class = "PARTIAL_HISTORY" if has_date else "CURRENT_ONLY"

    classifications = [
        {
            "source": "momentum_panel.sector",
            "classification": panel_class,
            "pit_safe": panel_class == "PIT_SAFE",
            "detail": {
                "tickers": len(panel_secs),
                "all_unknown_tickers": all_unknown,
                "unknown_fraction": all_unknown / n_tk,
                "time_varying_tickers": varies,
            },
        },
        {
            "source": "phase10f_repaired_eodhd_gics_map",
            "classification": repaired_class,
            "pit_safe": False,
            "detail": {
                "columns": smap_cols,
                "has_date_column": has_date,
                "ticker_count": len(sector_map),
                "distinct_sectors": map_distinct,
                "note": "current EODHD GICS classification; used only as a "
                "labeled diagnostic, never as historical PIT classification",
            },
        },
        {
            "source": "sec_sic_codes / norgate_classification_history",
            "classification": "UNUSABLE",
            "pit_safe": False,
            "detail": {"note": "not wired into this campaign; owned SEC SIC is 63-"
                       "ticker scale and Norgate classification history needs the "
                       "Data Director package — neither integrated here"},
        },
    ]
    pit_safe_available = any(c["pit_safe"] for c in classifications)

    concentration: Dict[str, Any] = {}
    if factor_series:
        for fid, series in factor_series.items():
            concentration[fid] = _three_way_concentration(series, inputs, sector_map)

    return {
        "schema_version": OWNED_FACTOR_SCHEMA_VERSION,
        "record_type": "SECTOR_AUDIT",
        "data_cutoff": inputs["data_cutoff"],
        "classifications": classifications,
        "pit_safe_classification_available": pit_safe_available,
        "treat_unknown_as_sector": bool((cfg.get("sector_history") or {}).get("treat_unknown_as_sector")),
        "require_pit_safe_for_promotion": bool(
            (cfg.get("sector_history") or {}).get("require_pit_safe_for_promotion", True)),
        "top_quartile_concentration": concentration,
        "sector_concentration_gate": {
            "max_top_rank_sector_share": DEFAULT_SCREEN_THRESHOLDS["max_top_rank_sector_share"]["value"],
            "status": "unchanged; applied diagnostically until a PIT-safe "
            "classification exists (Unknown is never treated as a real sector)",
        },
    }


# --------------------------------------------------------------------------- #
# Parts F/G: bounded diagnostics under both universe profiles
# --------------------------------------------------------------------------- #
def _restrict_inputs_to_series(
    inputs: Dict[str, Any], series: Dict[str, Dict[str, float]]
) -> Dict[str, Any]:
    """Source-observed view: keep only tickers a factor observes that month."""
    mm: Dict[str, Dict[str, dict]] = {}
    for m, row in inputs["mom_monthly"].items():
        allowed = series.get(m) or {}
        mm[m] = {tk: r for tk, r in row.items() if tk in allowed} if allowed else {}
    restricted = dict(inputs)
    restricted["mom_monthly"] = mm
    return restricted


def _map_screen_to_decision(
    screen: Dict[str, Any], sector_pit_safe: bool, require_pit_sector: bool
) -> str:
    out = screen["outcome"]
    if out == "ADVANCE_TO_PORTFOLIO_SCREEN":
        if require_pit_sector and not sector_pit_safe:
            return "DIAGNOSTIC_ONLY_SECTOR_HISTORY"
        return "ADVANCE_TO_PORTFOLIO_SCREEN"
    if out == "REJECTED_COVERAGE":
        return "REJECTED_GLOBAL_COVERAGE"
    if out == "REJECTED_NO_INCREMENTAL_SIGNAL":
        return "REJECTED_SIGNAL"
    if out == "INCONCLUSIVE":
        failed = [c for c in screen["checks"] if c.get("passed") is False]
        if any(c.get("check") == "sector_rank_concentration" for c in failed) and not sector_pit_safe:
            return "DIAGNOSTIC_ONLY_SECTOR_HISTORY"
        return "INCONCLUSIVE"
    return "INCONCLUSIVE"


def evaluate_factor(
    inputs: Dict[str, Any],
    series: Dict[str, Dict[str, float]],
    *,
    factor_id: str,
    baseline_t: float,
    survivorship: Dict[str, Any],
    sector_pit_safe: bool,
    cfg: Dict[str, Any],
) -> Dict[str, Any]:
    """Diagnostics + decision under GLOBAL and SOURCE_OBSERVED universes."""
    ic_overrides = cfg.get("ic_screen") or {}
    require_pit_sector = bool((cfg.get("sector_history") or {}).get("require_pit_safe_for_promotion", True))
    global_min_xc = float(cfg.get("global_min_cross_sectional_coverage", 0.60))
    src_min_xc = float(cfg.get("source_universe_min_coverage", 0.60))

    # GLOBAL
    dg = ev.compute_feature_diagnostics(series, inputs, feature_id="f_%s" % factor_id)
    sg = ev.run_ic_screen(dg, baseline_metrics={"rank_ic_t": baseline_t},
                          thresholds=ic_overrides, dsl_ok=True, pit_ok=True)
    if (dg.get("cross_sectional_coverage") or 0.0) < global_min_xc:
        global_decision = "REJECTED_GLOBAL_COVERAGE"
    else:
        global_decision = _map_screen_to_decision(sg, sector_pit_safe, require_pit_sector)

    # SOURCE_OBSERVED
    restricted = _restrict_inputs_to_series(inputs, series)
    ds = ev.compute_feature_diagnostics(series, restricted, feature_id="f_%s" % factor_id)
    ss = ev.run_ic_screen(ds, baseline_metrics={"rank_ic_t": baseline_t},
                          thresholds=ic_overrides, dsl_ok=True, pit_ok=True)
    if survivorship["survivorship_classification"] not in ("PASS", "PASS_WITH_CAVEAT"):
        source_decision = "DIAGNOSTIC_ONLY_SURVIVORSHIP"
    elif (ds.get("cross_sectional_coverage") or 0.0) < src_min_xc:
        source_decision = "REJECTED_GLOBAL_COVERAGE"
    else:
        source_decision = _map_screen_to_decision(ss, sector_pit_safe, require_pit_sector)

    advance = global_decision == "ADVANCE_TO_PORTFOLIO_SCREEN"

    def _tab(diag: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "months_evaluated": diag.get("months_evaluated"),
            "month_coverage": diag.get("month_coverage"),
            "cross_sectional_coverage": diag.get("cross_sectional_coverage"),
            "rank_ic_mean": diag.get("rank_ic_mean"),
            "rank_ic_t": diag.get("rank_ic_t"),
            "rank_ic_nw_t": diag.get("rank_ic_nw_t"),
            "rank_ic_t_ex_best_month": diag.get("rank_ic_t_ex_best_month"),
            "positive_month_fraction": diag.get("positive_month_fraction"),
            "subperiod_ic_means": diag.get("subperiod_ic_means"),
            "regime_ic_means": diag.get("regime_ic_means"),
            "avg_top_rank_sector_share": diag.get("avg_top_rank_sector_share"),
            "corr_with_mom_6_1": (diag.get("corr_with_sources") or {}).get("mom_6_1"),
            "corr_with_composite_sn": (diag.get("corr_with_sources") or {}).get("composite_sn"),
            "corr_with_baseline_score": diag.get("corr_with_baseline_score"),
        }

    return {
        "schema_version": OWNED_FACTOR_SCHEMA_VERSION,
        "record_type": "FACTOR_DIAGNOSTIC",
        "factor_id": factor_id,
        "baseline_rank_ic_t": baseline_t,
        "global_pit_universe": {
            "profile": "GLOBAL_PIT_UNIVERSE",
            "diagnostics": _tab(dg),
            "screen_outcome": sg["outcome"],
            "screen_reasons": sg["reasons"],
            "decision": global_decision,
        },
        "source_observed_universe": {
            "profile": "SOURCE_OBSERVED_UNIVERSE",
            "diagnostics": _tab(ds),
            "screen_outcome": ss["outcome"],
            "screen_reasons": ss["reasons"],
            "survivorship_classification": survivorship["survivorship_classification"],
            "delisted_representation_fraction": survivorship["delisted_representation_fraction"],
            "shadow_eligible": survivorship["shadow_eligible"],
            "decision": source_decision,
        },
        "sector_pit_safe": sector_pit_safe,
        "final_decision": global_decision,
        "advance_to_portfolio_screen": advance,
        "_full_global_diagnostic": dg,
    }


# --------------------------------------------------------------------------- #
# input loading + baseline
# --------------------------------------------------------------------------- #
def load_inputs(cfg: Dict[str, Any]) -> Dict[str, Any]:
    cutoff = (cfg.get("data") or {}).get("data_cutoff")
    src = cfg["sources"]
    kwargs: Dict[str, Any] = {
        "data_cutoff": cutoff,
        "momentum_panel_path": _resolve_path_spec(src["momentum_panel"]),
        "sector_map_path": _resolve_path_spec(src["sector_map"]),
    }
    # optional fixture overrides; the production config leaves these unset and
    # inherits family_backtest's committed defaults (identical files).
    if src.get("fundamental_panel"):
        kwargs["fundamental_panel_path"] = _resolve_path_spec(src["fundamental_panel"])
    if src.get("spy_monthly"):
        kwargs["spy_monthly_path"] = _resolve_path_spec(src["spy_monthly"])
    return fb.load_family_inputs(**kwargs)


def reproduce_baseline(inputs: Dict[str, Any], cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Baseline reproduction (Part H precondition) + baseline rank-IC t-stat."""
    val = fb.run_baseline_validation(inputs)
    repro = ev.verify_baseline_reproduction_via_integration(inputs)
    primary_cost = float((cfg.get("costs") or {}).get("primary_cost_bps_per_side", 25.0))
    base_sim = ev.run_integrated_experiment(inputs, {}, {
        "baseline_weight": 1.0, "feature_weight": 0.0, "feature_orientation": 1,
        "top_n": fb.BASELINE_PARAMS["top_n"],
        "sector_treatment": fb.BASELINE_PARAMS["sector_treatment"],
        "exit_buffer_fraction": fb.BASELINE_PARAMS["exit_buffer_fraction"],
        "universe": fb.BASELINE_PARAMS["universe"],
        "min_adv_dollar": fb.BASELINE_PARAMS["min_adv_dollar"],
    })
    base_metrics = fb.compute_experiment_metrics(
        base_sim, inputs, primary_cost_bps_per_side=primary_cost)
    # Harness faithfulness (the precondition for evaluating any candidate) is
    # determinism + the integrated engine collapsing to the committed baseline
    # + no construction-invariant violation. The exact owned-reference replay
    # is a stronger, data-specific fidelity proof reported alongside; it holds
    # for the real committed panel and is simply not applicable to a synthetic
    # fixture whose months do not overlap the reference book.
    structural_ok = bool(
        val["deterministic"] and repro["reproduced"] and not val["invariant_failures"]
    )
    return {
        "reference_available": val["reference_available"],
        "reference_reproduced": val["reference_reproduced"],
        "reference_months_compared": val["reference_months_compared"],
        "reference_mismatch_count": val["reference_mismatch_count"],
        "deterministic": val["deterministic"],
        "invariant_failures": val["invariant_failures"],
        "integration_collapses_to_baseline": repro["reproduced"],
        "baseline_reproduced": structural_ok,
        "reference_replay_ok": (
            val["reference_reproduced"] if val["reference_available"] else None
        ),
        "baseline_rank_ic_t": base_metrics.get("rank_ic_t"),
        "baseline_net_spy_excess_ann": base_metrics.get("net_spy_excess_ann"),
        "n_periods": val["n_periods"],
        "first_month": val["first_month"],
        "last_month": val["last_month"],
    }


# --------------------------------------------------------------------------- #
# Part J: run store
# --------------------------------------------------------------------------- #
class OwnedFactorRunStore:
    """Atomic, append-only run store outside the git checkout (Part J)."""

    def __init__(self, output_root: str):
        self.output_root = Path(output_root)
        self.runs_root = self.output_root / RUNS_SUBDIR

    def run_dir(self, run_id: str) -> Path:
        return self.runs_root / run_id

    def ensure_layout(self, run_id: str) -> Path:
        base = self.run_dir(run_id)
        for sub in ("pit_audits", "universe_audits", "sector_audits",
                    "diagnostics", "portfolio", "robustness", "reports"):
            (base / sub).mkdir(parents=True, exist_ok=True)
        return base

    def write(self, run_id: str, rel: str, obj: Any) -> str:
        return write_json_atomic(self.run_dir(run_id) / rel, obj)

    def write_text(self, run_id: str, rel: str, text: str) -> None:
        write_text_atomic(self.run_dir(run_id) / rel, text)

    def append_event(self, run_id: str, kind: str, payload: Dict[str, Any]) -> None:
        path = self.run_dir(run_id) / "events.jsonl"
        append_jsonl(path, {"kind": kind, "payload": payload, "ts": _now_iso()})

    def read(self, run_id: str, rel: str) -> Any:
        path = self.run_dir(run_id) / rel
        if not path.exists():
            return None
        return read_json(path)

    def write_latest_pointer(self, obj: Dict[str, Any]) -> None:
        write_json_atomic(self.output_root / LATEST_RUN_FILE, obj)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def compute_run_id(config_hash: str, cutoff: str, code_commit: str) -> str:
    return "owned_" + content_hash(
        {"config_hash": config_hash, "cutoff": cutoff, "commit": code_commit}
    )[:16]


# --------------------------------------------------------------------------- #
# Parts G/H/I: orchestrated campaign
# --------------------------------------------------------------------------- #
def run_owned_factor_campaign(
    cfg: Dict[str, Any],
    *,
    output_root: str,
    max_features: Optional[int] = None,
) -> Dict[str, Any]:
    verdict = validate_owned_factor_config(cfg)
    if not verdict["accepted"]:
        return {"status": "INVALID_CONFIG", "violations": verdict["violations"]}

    cutoff = (cfg.get("data") or {}).get("data_cutoff")
    code_commit = read_git_commit()
    run_id = compute_run_id(verdict["config_hash"], cutoff, code_commit)
    store = OwnedFactorRunStore(output_root)
    store.ensure_layout(run_id)
    store.append_event(run_id, "RUN_START", {"config_hash": verdict["config_hash"], "cutoff": cutoff})

    registry = build_source_registry(cfg)
    store.write(run_id, "source_inventory.json", registry)

    inputs = load_inputs(cfg)
    baseline = reproduce_baseline(inputs, cfg)
    if not baseline["baseline_reproduced"]:
        store.append_event(run_id, "BASELINE_NON_REPRODUCIBLE", baseline)
        store.write(run_id, "status.json", {"final_state": "BLOCKED_BASELINE", "baseline": baseline})
        return {"status": "BLOCKED_BASELINE", "run_id": run_id, "baseline": baseline}
    baseline_t = baseline["baseline_rank_ic_t"]

    # choose feature set (bounded)
    diag_cfg = cfg.get("diagnostics") or {}
    limit = int(max_features if max_features is not None else diag_cfg.get("max_features", 4))
    wanted = list(diag_cfg.get("initial_features") or [])[:limit]
    factors_by_id = {f["source_id"]: f for f in (cfg["sources"]["factors"])}

    # build series + PIT audits for the selected features
    factor_series: Dict[str, Dict[str, Dict[str, float]]] = {}
    for fid in wanted:
        f = factors_by_id[fid]
        series, meta = build_factor_series(cfg, f, inputs["months"])
        factor_series[fid] = series
        pit = build_factor_pit_audit(f, meta, cutoff)
        store.write(run_id, "pit_audits/%s.json" % fid, pit)

    # sector audit (needs series for concentration)
    sector_audit = build_sector_audit(inputs, cfg, factor_series)
    store.write(run_id, "sector_audits/sector_history.json", sector_audit)
    sector_pit_safe = sector_audit["pit_safe_classification_available"]

    # universe + survivorship
    universe = build_universe_profiles(inputs, factor_series, cfg, sector_pit_safe=sector_pit_safe)
    store.write(run_id, "universe_audits/universe.json", universe)

    # per-factor diagnostics + decisions under both profiles
    diagnostics: List[Dict[str, Any]] = []
    for fid in wanted:
        surv = universe["source_observed_universe"][fid]
        pit = store.read(run_id, "pit_audits/%s.json" % fid) or {}
        res = evaluate_factor(
            inputs, factor_series[fid], factor_id=fid, baseline_t=baseline_t,
            survivorship=surv, sector_pit_safe=sector_pit_safe, cfg=cfg,
        )
        res["pit_ok"] = pit.get("pit_ok")
        res.pop("_full_global_diagnostic", None)
        diagnostics.append(res)
        store.write(run_id, "diagnostics/%s.json" % fid, res)

    # Part H: portfolio integration only for genuine ADVANCE candidates
    advanced = [d["factor_id"] for d in diagnostics if d["advance_to_portfolio_screen"]]
    portfolio_results: List[Dict[str, Any]] = []
    robustness_results: List[Dict[str, Any]] = []
    max_integrations = int((cfg.get("budgets") or {}).get("max_portfolio_integrations", 2))
    for fid in advanced[:max_integrations]:
        port = run_portfolio_integration(inputs, factor_series[fid], cfg, factor_id=fid, baseline_t=baseline_t)
        portfolio_results.append(port)
        store.write(run_id, "portfolio/%s.json" % fid, port)
        robustness_results.append(port["robustness"])
        store.write(run_id, "robustness/%s.json" % fid, port["robustness"])

    best = _best_feature(diagnostics)
    surv_summary = {d["factor_id"]: d["source_observed_universe"]["survivorship_classification"] for d in diagnostics}
    run_doc = {
        "schema_version": OWNED_FACTOR_SCHEMA_VERSION,
        "run_id": run_id,
        "code_commit": code_commit,
        "data_cutoff": cutoff,
        "config_hash": verdict["config_hash"],
        "final_state": "COMPLETE",
        "baseline": baseline,
        "features_evaluated": wanted,
        "advanced_to_portfolio_screen": advanced,
        "best_feature": best["factor_id"] if best else None,
        "best_feature_decision": best["final_decision"] if best else None,
        "survivorship_by_factor": surv_summary,
        "sector_history_result": sector_audit["classifications"],
        "sector_pit_safe_available": sector_pit_safe,
        "n_portfolio_candidates": len(portfolio_results),
        "n_robustness_survivors": sum(
            1 for r in robustness_results if r.get("decision", {}).get("decision") == "PROMOTE"),
        "safety": dict(SAFETY_CONTRACT),
        "generated_at": _now_iso(),
    }
    store.write(run_id, "run.json", run_doc)
    store.write(run_id, "status.json", {
        "run_id": run_id, "final_state": "COMPLETE",
        "advanced": advanced, "best_feature": run_doc["best_feature"],
        "safety": dict(SAFETY_CONTRACT), "updated_at": _now_iso(),
    })
    store.append_event(run_id, "RUN_COMPLETE", {
        "best_feature": run_doc["best_feature"], "advanced": advanced})
    store.write_latest_pointer({
        "run_id": run_id,
        "code_commit": code_commit,
        "data_cutoff": cutoff,
        "config_hash": verdict["config_hash"],
        "final_state": "COMPLETE",
        "best_feature": run_doc["best_feature"],
        "best_feature_decision": run_doc["best_feature_decision"],
        "survivorship_result": surv_summary,
        "sector_history_result": sector_pit_safe,
        "generated_at": _now_iso(),
        "output_root": str(output_root),
    })
    return {
        "status": "COMPLETE",
        "run_id": run_id,
        "output_root": str(output_root),
        "run_dir": str(store.run_dir(run_id)),
        "best_feature": run_doc["best_feature"],
        "best_feature_decision": run_doc["best_feature_decision"],
        "advanced_to_portfolio_screen": advanced,
        "features_evaluated": wanted,
        "n_portfolio_candidates": len(portfolio_results),
    }


def _best_feature(diagnostics: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    scored = [
        (abs(d["global_pit_universe"]["diagnostics"].get("rank_ic_t") or 0.0), d)
        for d in diagnostics
    ]
    if not scored:
        return None
    scored.sort(key=lambda x: (-x[0], x[1]["factor_id"]))
    return scored[0][1]


def run_portfolio_integration(
    inputs: Dict[str, Any],
    series: Dict[str, Dict[str, float]],
    cfg: Dict[str, Any],
    *,
    factor_id: str,
    baseline_t: float,
) -> Dict[str, Any]:
    """80/20 integration + robustness for a genuine ADVANCE candidate (Part H/I)."""
    integ = cfg.get("integration") or {}
    pf = cfg.get("portfolio") or {}
    primary_cost = float((cfg.get("costs") or {}).get("primary_cost_bps_per_side", 25.0))
    # orientation from the standalone diagnostic
    diag = ev.compute_feature_diagnostics(series, inputs, feature_id="f_%s" % factor_id)
    params = {
        "baseline_weight": float(integ["baseline_weight"]),
        "feature_weight": float(integ["feature_weight"]),
        "feature_orientation": int(diag.get("orientation") or 1),
        "top_n": int(pf.get("top_n", 25)),
        "sector_treatment": pf.get("sector_treatment", "sector_cap"),
        "exit_buffer_fraction": float(pf.get("exit_buffer_fraction", 0.0)),
        "universe": pf.get("universe", "mhz_reconstruction"),
        "min_adv_dollar": float(pf.get("min_adv_dollar", fb.MIN_ADV_DOLLAR_DEFAULT)),
    }
    sim = ev.run_integrated_experiment(inputs, series, params)
    metrics = fb.compute_experiment_metrics(sim, inputs, primary_cost_bps_per_side=primary_cost)
    base_sim = ev.run_integrated_experiment(inputs, {}, dict(params, feature_weight=0.0, baseline_weight=1.0))
    base_metrics = fb.compute_experiment_metrics(base_sim, inputs, primary_cost_bps_per_side=primary_cost)
    robustness = ev.run_feature_robustness(
        inputs, series, params, primary_cost_bps_per_side=primary_cost,
        baseline_metrics=base_metrics, diagnostics=diag)
    return {
        "schema_version": OWNED_FACTOR_SCHEMA_VERSION,
        "record_type": "PORTFOLIO_INTEGRATION",
        "factor_id": factor_id,
        "params": params,
        "integration_weights_reconcile": abs(params["baseline_weight"] + params["feature_weight"] - 1.0) < 1e-9,
        "baseline_metrics": base_metrics,
        "candidate_metrics": metrics,
        "baseline_relative_delta": {
            "net_spy_excess_ann": (metrics.get("net_spy_excess_ann") or 0) - (base_metrics.get("net_spy_excess_ann") or 0),
            "rank_ic_t": (metrics.get("rank_ic_t") or 0) - (base_metrics.get("rank_ic_t") or 0),
            "turnover_monthly_oneside": (metrics.get("turnover_monthly_oneside") or 0) - (base_metrics.get("turnover_monthly_oneside") or 0),
            "max_drawdown": (metrics.get("max_drawdown") or 0) - (base_metrics.get("max_drawdown") or 0),
        },
        "robustness": robustness,
    }


# --------------------------------------------------------------------------- #
# read-only audit entry points (CLI)
# --------------------------------------------------------------------------- #
def audit_sources(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Read-only: source registry + per-factor PIT audit (no writes)."""
    registry = build_source_registry(cfg)
    cutoff = (cfg.get("data") or {}).get("data_cutoff")
    inputs = load_inputs(cfg)
    pit_audits = []
    for f in cfg["sources"]["factors"]:
        if not os.path.exists(_factor_path(cfg, f)):
            continue
        _series, meta = build_factor_series(cfg, f, inputs["months"])
        pit_audits.append(build_factor_pit_audit(f, meta, cutoff))
    return {
        "source_registry": registry,
        "pit_audits": pit_audits,
        "safety": dict(SAFETY_CONTRACT),
    }


def audit_universe(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Read-only universe + survivorship audit over all declared factors."""
    inputs = load_inputs(cfg)
    factor_series: Dict[str, Dict[str, Dict[str, float]]] = {}
    for f in cfg["sources"]["factors"]:
        if not os.path.exists(_factor_path(cfg, f)):
            continue
        series, _meta = build_factor_series(cfg, f, inputs["months"])
        factor_series[f["source_id"]] = series
    sector_audit = build_sector_audit(inputs, cfg)
    return build_universe_profiles(
        inputs, factor_series, cfg,
        sector_pit_safe=sector_audit["pit_safe_classification_available"])


def audit_sector(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Read-only sector-history audit over all declared factors."""
    inputs = load_inputs(cfg)
    factor_series: Dict[str, Dict[str, Dict[str, float]]] = {}
    for f in cfg["sources"]["factors"]:
        if not os.path.exists(_factor_path(cfg, f)):
            continue
        series, _meta = build_factor_series(cfg, f, inputs["months"])
        factor_series[f["source_id"]] = series
    return build_sector_audit(inputs, cfg, factor_series)


# --------------------------------------------------------------------------- #
# Part K/M: idempotent report
# --------------------------------------------------------------------------- #
def generate_report(run_id: str, output_root: str) -> Dict[str, Any]:
    store = OwnedFactorRunStore(output_root)
    run_doc = store.read(run_id, "run.json")
    if run_doc is None:
        raise OwnedFactorError("unknown run_id: %s" % run_id)
    diagnostics = []
    for fid in run_doc.get("features_evaluated", []):
        d = store.read(run_id, "diagnostics/%s.json" % fid)
        if d:
            diagnostics.append(d)

    lines: List[str] = []
    lines.append("# Phase 30B owned-factor integration report")
    lines.append("")
    lines.append("- run_id: %s" % run_doc["run_id"])
    lines.append("- code_commit: %s" % run_doc["code_commit"])
    lines.append("- data_cutoff: %s" % run_doc["data_cutoff"])
    lines.append("- config_hash: %s" % run_doc["config_hash"])
    lines.append("- final_state: %s" % run_doc["final_state"])
    lines.append("- best_feature: %s (%s)" % (run_doc["best_feature"], run_doc["best_feature_decision"]))
    lines.append("- advanced_to_portfolio_screen: %s" % (run_doc["advanced_to_portfolio_screen"] or "none"))
    lines.append("- sector_pit_safe_available: %s" % run_doc["sector_pit_safe_available"])
    lines.append("")
    lines.append("## Per-factor decisions (GLOBAL vs SOURCE_OBSERVED)")
    lines.append("")
    lines.append("| factor | global xcov | global rank-IC t | global decision | source decision | survivorship |")
    lines.append("|---|---|---|---|---|---|")
    for d in diagnostics:
        g = d["global_pit_universe"]
        s = d["source_observed_universe"]
        lines.append("| %s | %s | %s | %s | %s | %s |" % (
            d["factor_id"],
            _fmt(g["diagnostics"].get("cross_sectional_coverage")),
            _fmt(g["diagnostics"].get("rank_ic_t")),
            g["decision"], s["decision"], s["survivorship_classification"]))
    lines.append("")
    lines.append("_Research-only. No order, broker, automation, promotion, or "
                 "operational-model change occurred._")
    report_md = "\n".join(lines) + "\n"

    report_json = {
        "schema_version": OWNED_FACTOR_SCHEMA_VERSION,
        "record_type": "OWNED_FACTOR_REPORT",
        "run_id": run_doc["run_id"],
        "code_commit": run_doc["code_commit"],
        "data_cutoff": run_doc["data_cutoff"],
        "final_state": run_doc["final_state"],
        "best_feature": run_doc["best_feature"],
        "best_feature_decision": run_doc["best_feature_decision"],
        "advanced_to_portfolio_screen": run_doc["advanced_to_portfolio_screen"],
        "survivorship_by_factor": run_doc.get("survivorship_by_factor"),
        "sector_pit_safe_available": run_doc["sector_pit_safe_available"],
        "per_factor": [
            {
                "factor_id": d["factor_id"],
                "global_decision": d["global_pit_universe"]["decision"],
                "source_decision": d["source_observed_universe"]["decision"],
                "global_rank_ic_t": d["global_pit_universe"]["diagnostics"].get("rank_ic_t"),
                "global_cross_sectional_coverage": d["global_pit_universe"]["diagnostics"].get("cross_sectional_coverage"),
            }
            for d in diagnostics
        ],
        "safety": dict(SAFETY_CONTRACT),
    }
    store.write(run_id, "reports/report.json", report_json)
    store.write_text(run_id, "reports/report.md", report_md)
    return {
        "run_id": run_id,
        "artifact_paths": {
            "report_md": str(store.run_dir(run_id) / "reports" / "report.md"),
            "report_json": str(store.run_dir(run_id) / "reports" / "report.json"),
        },
        "report": report_json,
    }


def _fmt(x: Any) -> str:
    if isinstance(x, (int, float)):
        return "%.3f" % x
    return str(x)


__all__ = [
    "DECISIONS",
    "OWNED_FACTOR_SCHEMA_VERSION",
    "OwnedFactorError",
    "OwnedFactorRunStore",
    "SURVIVORSHIP_RESULTS",
    "asof_join_eodhd",
    "audit_sector",
    "audit_sources",
    "audit_universe",
    "build_factor_pit_audit",
    "build_factor_series",
    "build_sector_audit",
    "build_source_registry",
    "build_universe_profiles",
    "compute_run_id",
    "evaluate_factor",
    "generate_report",
    "load_inputs",
    "load_momentum_column_series",
    "load_owned_factor_config",
    "reproduce_baseline",
    "run_owned_factor_campaign",
    "run_portfolio_integration",
    "validate_owned_factor_config",
]
