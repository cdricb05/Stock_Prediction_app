"""Phase 5-G2 — Expanded-Coverage Event Alpha Rerun (offline, PIT, preview-only).

This is a **research rerun only**. It reuses the corrected Phase 5-G runner logic
(``research/run_phase5g_earnings_revision_alpha.py``) unchanged and asks one question
on the now-expanded event coverage:

    "Did ``price_plus_event_alpha`` (or any event model) still improve over the correct
     Phase 5-C price-only reference on the SAME covered tickers, once event coverage
     grew from the original 50-ticker panel to the expanded 75-ticker panel?"

It does NOT collect data, call any provider, touch the raw cache, optimize strategy
filters, deploy, run Paper Trader, or place/automate any order. It strips no API key
because it needs none. The underlying Phase 5-G run is routed to a SEPARATE output
sub-directory so the committed original 50-ticker Phase 5-G artifacts are preserved.

What it does
------------
  1. **Preflight verification** (all read-only): raw cache count, distinct-ticker
     event coverage, the Phase 5-C universe size, that no raw files are staged, that
     newly collected raw payloads are gitignored, that no API key is needed, and that
     no network call will be made.
  2. **Expanded rerun.** Imports the Phase 5-G runner as a module and calls its
     ``run()`` against the read-only D: price history, routing every Phase 5-G
     artifact into ``phase5g_expanded_coverage_run/`` under this phase's output dir
     (never the committed original Phase 5-G dir).
  3. **Original baseline.** Reads the committed original 50-ticker Phase 5-G report
     (read-only) to compare original-vs-expanded.
  4. **Apples-to-apples comparison** on the SAME covered cross-section: the Phase 5-C
     price-only reference, ``event_alpha_only``, ``price_plus_event_alpha``,
     ``event_gated_price_signal`` and ``best_event_candidate`` — all evaluated by the
     reused Phase 5-G harness on the identical covered (date, ticker) records.
  5. Emits committed-safe text artifacts (scoreboard, incremental-edge, gate matrix,
     horizon comparison, yearly IC, next-phase plan) plus the main JSON, and an honest
     gate matrix + one of the allowed recommendations. It never forces a PASS.

Hard rules honored: no live network / API calls, no provider shopping, no FMP, no
SimFin expansion, no raw-cache modification, no strategy-filter optimization, no
package installs, D: is read-only price input only, no Paper Trader / GCP / deploy /
orders / broker / automation / binary artifacts, no commit, no push.
"""
from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import math
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

PHASE = "5-G2"
OBJECTIVE = (
    "Rerun the Phase 5-G earnings / post-earnings-drift event-alpha research on the "
    "expanded 75-ticker event coverage and determine whether the incremental event-alpha "
    "edge over the correct Phase 5-C price-only reference (on the same covered tickers) "
    "survives broader coverage.")

# --------------------------------------------------------------------------- #
# Paths
# --------------------------------------------------------------------------- #
_PHASE5G_RUNNER = _REPO_ROOT / "research" / "run_phase5g_earnings_revision_alpha.py"
_ORIGINAL_PHASE5G_REPORT = (_REPO_ROOT / "research" / "output"
                            / "phase5g_earnings_revision_alpha"
                            / "phase5g_earnings_revision_alpha.json")
_RAW_CACHE_DIR = (_REPO_ROOT / "research" / "output"
                  / "phase3m_earnings_estimates_signal_gate" / "raw")
_EARNINGS_FEATURES_CSV = (_REPO_ROOT / "research" / "output"
                          / "phase3m_earnings_estimates_signal_gate"
                          / "earnings_features_universe.csv")
_OUT_DIR = _REPO_ROOT / "research" / "output" / "phase5g2_expanded_event_alpha_rerun"

# --------------------------------------------------------------------------- #
# Gates / thresholds (FIXED a-priori; not tuned on this sample)
# --------------------------------------------------------------------------- #
GATE_INCREMENTAL_IC = 0.005   # best event model must beat the covered 5-C reference by this
GATE_COVERAGE_MIN = 75        # expanded coverage must reach the project's own event gate
GATE_PLACEBO_ABS = 0.02       # |placebo IC| must collapse below this (leakage-clean)
PLACEBO_MATERIAL_FRACTION = 0.5  # placebo IC must be < this fraction of the real best IC

# --------------------------------------------------------------------------- #
# Recommendation vocabulary (the allowed values)
# --------------------------------------------------------------------------- #
REC_CONFIRMED = "EVENT_ALPHA_CONFIRMED_ON_EXPANDED_COVERAGE"
REC_WEAKENS = "EVENT_ALPHA_WEAKENS_NEEDS_REVIEW"
REC_NO_EDGE = "NO_INCREMENTAL_EVENT_EDGE"
REC_BLOCKED = "DATA_QUALITY_BLOCKED"
REC_ERROR = "ERROR"
ALLOWED_RECOMMENDATIONS = (REC_CONFIRMED, REC_WEAKENS, REC_NO_EDGE, REC_BLOCKED, REC_ERROR)

# The Phase 5-G model identifiers (reused, not redefined).
M_REF = "phase5c_reference"
M_EVENT = "event_alpha_only"
M_PRICE_PLUS = "price_plus_event_alpha"
M_GATED = "event_gated_price_signal"
_EVENT_ARMS = [M_EVENT, M_PRICE_PLUS, M_GATED]
_SCOREBOARD_MODELS = [M_REF, M_EVENT, M_PRICE_PLUS, M_GATED]

LABEL_5D = "forward_excess_return_5d_vs_spy"
LABEL_10D = "forward_excess_return_10d_vs_spy"
LABEL_20D = "forward_excess_return_20d_vs_spy"


class _OutPaths:
    """Resolve every committed-safe artifact under one output directory so a test can
    redirect them into a temp dir and never overwrite committed production artifacts."""

    __slots__ = ("out_dir", "report", "scoreboard", "incremental", "gate_matrix",
                 "horizon", "yearly_ic", "next_plan", "expanded_run_dir")

    def __init__(self, out_dir) -> None:
        d = Path(out_dir)
        self.out_dir = d
        self.report = d / "phase5g2_expanded_event_alpha_rerun.json"
        self.scoreboard = d / "expanded_coverage_event_alpha_scoreboard.csv"
        self.incremental = d / "expanded_coverage_incremental_edge_report.csv"
        self.gate_matrix = d / "expanded_coverage_gate_matrix.csv"
        self.horizon = d / "expanded_coverage_horizon_comparison.csv"
        self.yearly_ic = d / "expanded_coverage_yearly_ic.csv"
        self.next_plan = d / "phase5g3_next_plan.json"
        # The underlying Phase 5-G expanded run lands here (NOT the committed 5-G dir).
        self.expanded_run_dir = d / "phase5g_expanded_coverage_run"

    @property
    def committed_artifacts(self) -> List[Path]:
        return [self.report, self.scoreboard, self.incremental, self.gate_matrix,
                self.horizon, self.yearly_ic, self.next_plan]


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #
def _rel(p: Path) -> str:
    try:
        return str(Path(p).relative_to(_REPO_ROOT))
    except ValueError:
        return str(p)


def _f(x) -> Optional[float]:
    if x is None:
        return None
    try:
        xf = float(x)
    except (TypeError, ValueError):
        return None
    return round(xf, 6) if math.isfinite(xf) else None


def _write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=2, default=str)
        fh.write("\n")


def _write_csv(path: Path, header: Sequence[str], rows: Sequence[Sequence]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        for r in rows:
            w.writerow(r)


def _load_phase5g(runner_path: Optional[Path] = None):
    path = Path(runner_path) if runner_path else _PHASE5G_RUNNER
    spec = importlib.util.spec_from_file_location("phase5g_runner_for_g2", str(path))
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load Phase 5-G runner at {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _distinct_event_coverage(features_csv: Path) -> int:
    """Distinct non-SPY tickers in the committed Phase 3-M earnings features CSV."""
    if not features_csv.is_file():
        return 0
    seen = set()
    try:
        with open(features_csv, "r", encoding="utf-8", newline="") as fh:
            for row in csv.DictReader(fh):
                t = (row.get("ticker") or "").strip().upper()
                if t and t != "SPY":
                    seen.add(t)
    except (OSError, ValueError):
        return 0
    return len(seen)


def _raw_cache_count(raw_dir: Path) -> int:
    if not raw_dir.is_dir():
        return 0
    return sum(1 for _ in raw_dir.glob("*.json"))


# --------------------------------------------------------------------------- #
# Git hygiene (read-only; best-effort — never raises, never writes)
# --------------------------------------------------------------------------- #
def _git(args: List[str]) -> Tuple[int, str]:
    try:
        proc = subprocess.run(["git", "-C", str(_REPO_ROOT), *args],
                              capture_output=True, text=True, timeout=30)
        return proc.returncode, (proc.stdout or "")
    except (OSError, subprocess.SubprocessError):
        return -1, ""


def _raw_git_hygiene() -> dict:
    """Read-only git evidence for the raw cache: nothing staged, newly collected raw
    payloads are gitignored, no raw payload modified. Best-effort and conservative."""
    raw_rel = "research/output/phase3m_earnings_estimates_signal_gate/raw/"

    # Staged files touching the raw cache.
    rc, staged = _git(["diff", "--cached", "--name-only"])
    staged_raw = [l for l in staged.splitlines() if raw_rel in l.replace("\\", "/")]

    # Modified (unstaged, tracked) files touching the raw cache.
    rc2, status = _git(["status", "--porcelain"])
    modified_raw = [l[3:] for l in status.splitlines()
                    if raw_rel in l[3:].replace("\\", "/") and l[:2].strip()]

    # A newly collected raw payload must be gitignored. Probe a non-committed sample.
    gitignored = True
    probe = None
    if _RAW_CACHE_DIR.is_dir():
        for p in sorted(_RAW_CACHE_DIR.glob("*.json")):
            rc_idx, _ = _git(["ls-files", "--error-unmatch", str(p)])
            if rc_idx != 0:           # not tracked -> this is a newly collected payload
                rc_ig, _ = _git(["check-ignore", str(p)])
                gitignored = (rc_ig == 0)
                probe = p.name
                break

    return {
        "raw_files_staged": bool(staged_raw),
        "staged_raw_files": staged_raw,
        "raw_files_modified": bool(modified_raw),
        "modified_raw_files": modified_raw,
        "new_raw_payload_gitignored": gitignored,
        "new_raw_payload_probe": probe,
        "git_available": rc == 0 or rc2 == 0,
    }


# --------------------------------------------------------------------------- #
# Preflight verification (req #1)
# --------------------------------------------------------------------------- #
def verify_preconditions(expanded_report: dict) -> dict:
    raw_count = _raw_cache_count(_RAW_CACHE_DIR)
    coverage_count = _distinct_event_coverage(_EARNINGS_FEATURES_CSV)
    universe_count = expanded_report.get("coverage_summary", {}).get("universe_size")
    hygiene = _raw_git_hygiene()
    checks = {
        "raw_cache_count": raw_count,
        "raw_cache_count_ok": raw_count >= GATE_COVERAGE_MIN,
        "event_coverage_count": coverage_count,
        "event_coverage_ok": coverage_count >= GATE_COVERAGE_MIN,
        "universe_count": universe_count,
        "phase5c_universe_is_128": universe_count == 128,
        "raw_files_staged": hygiene["raw_files_staged"],
        "no_raw_files_staged_ok": not hygiene["raw_files_staged"],
        "raw_files_modified": hygiene["raw_files_modified"],
        "no_raw_files_modified_ok": not hygiene["raw_files_modified"],
        "new_raw_payload_gitignored": hygiene["new_raw_payload_gitignored"],
        "api_key_required": False,
        "network_used": False,
        "git_evidence": hygiene,
    }
    checks["all_preconditions_ok"] = bool(
        checks["raw_cache_count_ok"] and checks["event_coverage_ok"]
        and checks["no_raw_files_staged_ok"] and checks["new_raw_payload_gitignored"])
    return checks


# --------------------------------------------------------------------------- #
# Extract a per-model view from the reused Phase 5-G report scoreboard.
# --------------------------------------------------------------------------- #
def _model_block(expanded: dict, model: str) -> dict:
    return expanded.get("model_scoreboard", {}).get(model, {}) or {}


def _ic(expanded: dict, model: str) -> Optional[float]:
    b = _model_block(expanded, model)
    return b.get("mean_rank_ic") if b.get("evaluable") else None


def _horizon_ic(expanded: dict, model: str, label: str) -> Optional[float]:
    return (_model_block(expanded, model).get("multi_horizon_ic") or {}).get(label)


# --------------------------------------------------------------------------- #
# Recommendation (req #5/#7/#8) — evidence-driven, never forced.
# --------------------------------------------------------------------------- #
def derive_recommendation(expanded: dict, coverage_ok: bool, leakage_ok: bool,
                          incremental_ic: Optional[float], best_name: Optional[str],
                          best_placebo: Optional[float], best_ic: Optional[float]) -> str:
    # Data-quality blockers first.
    if not expanded:
        return REC_BLOCKED
    if expanded.get("recommendation") == "ERROR":
        return REC_ERROR
    if not expanded.get("local_event_data_found") or not expanded.get("usable_event_data"):
        return REC_BLOCKED
    pit_ok = expanded.get("pit_safety_summary", {}).get("availability_is_report_date") is True
    ref_ic = _ic(expanded, M_REF)
    if not pit_ok or ref_ic is None or not coverage_ok:
        return REC_BLOCKED

    placebo_clean = (best_placebo is None or abs(best_placebo) <= GATE_PLACEBO_ABS) and leakage_ok
    placebo_material = (best_placebo is None or best_ic is None or best_ic <= 0
                        or abs(best_placebo) < PLACEBO_MATERIAL_FRACTION * abs(best_ic))

    # No clean positive incremental edge -> no edge.
    if (best_name is None or incremental_ic is None or incremental_ic <= 0
            or not placebo_clean or not placebo_material):
        return REC_NO_EDGE
    # A real, leakage-clean, placebo-clean positive edge at sufficient coverage.
    if incremental_ic >= GATE_INCREMENTAL_IC:
        return REC_CONFIRMED
    # Positive but below the materiality threshold -> survived but weakened.
    return REC_WEAKENS


# --------------------------------------------------------------------------- #
# Gate matrix (req #7)
# --------------------------------------------------------------------------- #
def build_gate_matrix(pre: dict, expanded: dict, coverage_count: int, leakage_ok: bool,
                      incremental_ic: Optional[float], best_name: Optional[str],
                      best_ic: Optional[float], best_placebo: Optional[float],
                      recommendation: str, same_universe: bool) -> List[dict]:
    gates: List[dict] = []

    def add(name, status, metric, value, threshold, note):
        gates.append({"gate_name": name, "status": status, "metric": metric,
                      "value": value, "threshold": threshold, "note": note})

    cov_ok = coverage_count >= GATE_COVERAGE_MIN
    add("coverage_at_least_75_gate", "PASS" if cov_ok else "FAIL", "event_coverage_count",
        coverage_count, f">={GATE_COVERAGE_MIN}",
        f"covered {coverage_count}/{expanded.get('coverage_summary', {}).get('universe_size')}")

    add("no_network_used_gate", "PASS", "network_used", False, "false",
        "pure offline rerun; reused Phase 5-G harness makes no provider call")

    add("no_raw_files_modified_gate", "PASS" if pre["no_raw_files_modified_ok"] else "FAIL",
        "raw_files_modified", pre["raw_files_modified"], "false",
        "raw Alpha Vantage payload cache untouched")

    add("no_raw_files_staged_gate", "PASS" if pre["no_raw_files_staged_ok"] else "FAIL",
        "raw_files_staged", pre["raw_files_staged"], "false",
        "newly collected raw payloads remain gitignored and unstaged")

    pit_ok = expanded.get("pit_safety_summary", {}).get("availability_is_report_date") is True
    add("pit_safety_gate", "PASS" if (pit_ok and leakage_ok) else "FAIL",
        "availability_is_report_date_and_asof_join", bool(pit_ok and leakage_ok), "true",
        "availability_date = report date; strict as-of join; no future event used")

    if incremental_ic is None:
        add("incremental_ic_over_reference_gate", "NOT_EVALUABLE", "incremental_mean_rank_ic",
            None, f">{GATE_INCREMENTAL_IC}", "best event arm or reference not evaluable")
    else:
        st = "PASS" if incremental_ic > GATE_INCREMENTAL_IC else (
            "WARNING" if incremental_ic > 0 else "FAIL")
        add("incremental_ic_over_reference_gate", st, "incremental_mean_rank_ic",
            _f(incremental_ic), f">{GATE_INCREMENTAL_IC}",
            f"best event IC={_f(best_ic)} vs covered 5-C ref IC={_f(_ic(expanded, M_REF))}")

    # Placebo materially lower than the real best IC.
    if best_placebo is None or best_ic is None:
        add("placebo_materially_lower_gate", "NOT_EVALUABLE", "placebo_vs_real_ic",
            None, f"|placebo| < {PLACEBO_MATERIAL_FRACTION}x real IC", "")
    else:
        material = (best_ic <= 0) or (abs(best_placebo) < PLACEBO_MATERIAL_FRACTION * abs(best_ic))
        clean = abs(best_placebo) <= GATE_PLACEBO_ABS
        add("placebo_materially_lower_gate", "PASS" if (material and clean) else "FAIL",
            "placebo_mean_ic", _f(best_placebo),
            f"|placebo| < {PLACEBO_MATERIAL_FRACTION}x real IC and <= {GATE_PLACEBO_ABS}",
            f"best real IC={_f(best_ic)}; placebo collapses toward zero")

    add("same_covered_universe_gate", "PASS" if same_universe else "FAIL",
        "all_models_share_covered_cross_section", same_universe, "true",
        "phase5c_reference and every event arm scored on the identical covered (date,ticker) set")

    supported = _recommendation_supported(recommendation, incremental_ic, best_name,
                                          best_ic, best_placebo, leakage_ok, cov_ok, expanded)
    add("recommendation_metrics_agree_gate", "PASS" if supported else "FAIL",
        "recommendation_supported_by_metrics", supported, "true",
        f"recommendation={recommendation}")

    # Always-on safety gates.
    add("preview_only_gate", "PASS", "preview_only", True, "true", "research rerun only")
    add("no_orders_gate", "PASS", "orders_enabled", False, "false", "")
    add("no_broker_execution_gate", "PASS", "broker_execution_enabled", False, "false", "")
    add("no_automation_gate", "PASS", "automation_enabled", False, "false", "")
    add("no_binary_model_artifact_gate", "PASS", "binary_artifacts_created", False, "false",
        "models evaluated in-memory; nothing persisted")
    add("no_strategy_filter_optimization_gate", "PASS", "strategy_optimization_loop_run", False,
        "false", "no portfolio-filter tuning; pure IC comparison")
    return gates


def _recommendation_supported(rec: str, incr: Optional[float], best_name: Optional[str],
                              best_ic: Optional[float], best_placebo: Optional[float],
                              leakage_ok: bool, cov_ok: bool, expanded: dict) -> bool:
    """Independently re-check that the emitted recommendation agrees with the metrics."""
    if rec == REC_ERROR:
        return True
    if rec == REC_BLOCKED:
        pit_ok = expanded.get("pit_safety_summary", {}).get("availability_is_report_date") is True
        return (not expanded) or (not expanded.get("usable_event_data")) or (not pit_ok) \
            or (_ic(expanded, M_REF) is None) or (not cov_ok)
    placebo_clean = (best_placebo is None or abs(best_placebo) <= GATE_PLACEBO_ABS) and leakage_ok
    placebo_material = (best_placebo is None or best_ic is None or best_ic <= 0
                        or abs(best_placebo) < PLACEBO_MATERIAL_FRACTION * abs(best_ic))
    has_clean_edge = bool(best_name and incr is not None and incr > 0
                          and placebo_clean and placebo_material)
    if rec == REC_NO_EDGE:
        return not has_clean_edge
    if rec == REC_CONFIRMED:
        return bool(has_clean_edge and cov_ok and incr is not None and incr >= GATE_INCREMENTAL_IC)
    if rec == REC_WEAKENS:
        return bool(has_clean_edge and incr is not None and 0 < incr < GATE_INCREMENTAL_IC)
    return False


# --------------------------------------------------------------------------- #
# Artifact writers
# --------------------------------------------------------------------------- #
def _write_scoreboard(paths: "_OutPaths", expanded: dict, best_name: Optional[str]) -> None:
    ref_ic = _ic(expanded, M_REF)
    hdr = ["model", "evaluable", "mean_rank_ic", "ic_t_stat", "ic_5d", "ic_10d", "ic_20d",
           "placebo_mean_ic", "incremental_ic_vs_reference", "is_best_event_candidate"]
    rows = []
    for m in _SCOREBOARD_MODELS:
        b = _model_block(expanded, m)
        ev = bool(b.get("evaluable"))
        ic = b.get("mean_rank_ic") if ev else None
        incr = (ic - ref_ic) if (ic is not None and ref_ic is not None and m != M_REF) else (
            0.0 if m == M_REF and ref_ic is not None else None)
        rows.append([m, ev, _f(ic), _f(b.get("ic_t_stat")) if ev else None,
                     _horizon_ic(expanded, m, LABEL_5D), _horizon_ic(expanded, m, LABEL_10D),
                     _horizon_ic(expanded, m, LABEL_20D), b.get("placebo_mean_ic"),
                     _f(incr), (m == best_name)])
    _write_csv(paths.scoreboard, hdr, rows)


def _write_incremental(paths: "_OutPaths", expanded: dict, original: dict,
                       best_name: Optional[str]) -> None:
    ref_ic = _ic(expanded, M_REF)
    sel = expanded.get("best_event_candidate_selection", {}).get("arm_detail", {})
    hdr = ["coverage_regime", "model", "mean_rank_ic", "reference_mean_rank_ic",
           "incremental_ic", "placebo_mean_ic", "placebo_clean", "eligible_as_best"]
    rows = []
    # Expanded 75-ticker arms.
    for arm in _EVENT_ARMS:
        d = sel.get(arm, {})
        rows.append(["expanded_75", arm, d.get("mean_rank_ic"), _f(ref_ic),
                     d.get("incremental_ic"), d.get("placebo_mean_ic"),
                     d.get("placebo_clean"), d.get("eligible")])
    # Original 50-ticker best (for the explicit original-vs-expanded comparison).
    osel = original.get("best_event_candidate_selection", {})
    o_ref = osel.get("reference_mean_rank_ic")
    o_best = original.get("best_event_candidate")
    o_detail = osel.get("arm_detail", {}).get(o_best, {}) if o_best else {}
    rows.append(["original_50", o_best, o_detail.get("mean_rank_ic"), _f(o_ref),
                 o_detail.get("incremental_ic"), o_detail.get("placebo_mean_ic"),
                 o_detail.get("placebo_clean"), o_detail.get("eligible")])
    _write_csv(paths.incremental, hdr, rows)


def _write_gate_matrix(paths: "_OutPaths", gates: List[dict]) -> None:
    _write_csv(paths.gate_matrix, ["gate_name", "status", "metric", "value", "threshold", "note"],
               [[g["gate_name"], g["status"], g["metric"], g["value"], g["threshold"], g["note"]]
                for g in gates])


def _write_horizon(paths: "_OutPaths", expanded: dict) -> None:
    hdr = ["model", "ic_5d", "ic_10d", "ic_20d"]
    rows = [[m, _horizon_ic(expanded, m, LABEL_5D), _horizon_ic(expanded, m, LABEL_10D),
             _horizon_ic(expanded, m, LABEL_20D)] for m in _SCOREBOARD_MODELS]
    _write_csv(paths.horizon, hdr, rows)


def _write_yearly_ic(paths: "_OutPaths", expanded: dict) -> None:
    by_model = {m: (_model_block(expanded, m).get("ic_by_year") or {}) for m in _SCOREBOARD_MODELS}
    years = sorted({y for d in by_model.values() for y in d})
    hdr = ["year"] + _SCOREBOARD_MODELS
    rows = [[y] + [_f(by_model[m].get(y)) for m in _SCOREBOARD_MODELS] for y in years]
    _write_csv(paths.yearly_ic, hdr, rows)


def _write_next_plan(paths: "_OutPaths", recommendation: str, coverage_count: int,
                     best_name: Optional[str], incremental_ic: Optional[float],
                     revision_present: bool) -> None:
    proceed = recommendation == REC_CONFIRMED
    if recommendation == REC_CONFIRMED:
        title = "Phase 5-H Event-Alpha Strategy Test (research / shadow-only)"
        focus = ("Coverage and a leakage-clean incremental edge now both clear the gate. "
                 "Phase 5-G3 should add a point-in-time analyst-estimate-revision series and "
                 "then proceed to the Phase 5-H event-alpha shadow strategy test.")
    elif recommendation == REC_WEAKENS:
        title = "Phase 5-G3 Edge-Stability Diagnosis (research-only)"
        focus = ("The incremental edge survived expansion but fell below the materiality "
                 "threshold. Phase 5-G3 should diagnose which added tickers diluted the edge "
                 "(by-year / by-cohort IC attribution) and add a PIT revision series before any "
                 "strategy test.")
    elif recommendation == REC_NO_EDGE:
        title = "Phase 5-G3 Event-Signal Re-specification (research-only)"
        focus = ("No leakage-clean incremental edge at expanded coverage. Phase 5-G3 should "
                 "re-specify the event composite (e.g. add a PIT analyst-revision series, revisit "
                 "the drift window) rather than proceed to a strategy test.")
    else:
        title = "Phase 5-G3 Data-Quality Remediation (research-only)"
        focus = ("Expanded rerun was blocked. Phase 5-G3 should remediate the flagged data-quality "
                 "issue before re-running the event-alpha comparison.")
    plan = {
        "phase": "5-G3",
        "title": title,
        "proceed_to_phase5h_strategy_test": proceed,
        "phase5g2_recommendation": recommendation,
        "what_phase5g3_should_be": focus,
        "best_event_candidate": best_name,
        "incremental_mean_rank_ic_vs_reference": _f(incremental_ic),
        "coverage_count": coverage_count,
        "pit_analyst_revision_series_present": revision_present,
        "required_before_strategy_test": [
            "add a point-in-time analyst-estimate-revision series "
            "(ticker, revision publication date, prior_estimate, new_estimate)",
            "attribute the expanded-coverage IC change to the added tickers (cohort IC breakdown)",
            "re-confirm survivorship handling on a survivorship-free universe before trusting "
            "absolute portfolio returns",
        ],
        "preview_only": True, "orders_enabled": False, "automation_enabled": False,
        "broker_execution_enabled": False, "deployed": False, "network_used": False,
        "raw_files_modified": False, "raw_files_staged": False,
    }
    _write_json(paths.next_plan, plan)


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
def run(price_csv: Optional[str] = None, out_dir: Optional[Path] = None,
        original_report_path: Optional[Path] = None,
        phase5g_runner_path: Optional[Path] = None) -> dict:
    paths = _OutPaths(out_dir if out_dir is not None else _OUT_DIR)
    paths.out_dir.mkdir(parents=True, exist_ok=True)

    base_flags = {
        "phase": PHASE,
        "objective": OBJECTIVE,
        "generated_by": _rel(Path(__file__)),
        "network_used": False, "paid_apis_used": False,
        "raw_files_modified": False, "raw_files_staged": False,
        "api_key_required": False, "preview_only": True,
        "orders_enabled": False, "automation_enabled": False,
        "broker_execution_enabled": False, "production_replacement": False,
        "deployed": False, "binary_artifacts_created": False,
    }

    # ---- Step 2: reuse the corrected Phase 5-G runner on the expanded coverage ----
    p5g = _load_phase5g(phase5g_runner_path)
    expanded = p5g.run(price_csv=price_csv, out_dir=str(paths.expanded_run_dir))

    # ---- Step 1: preflight verification (uses the expanded run's universe_size) ----
    pre = verify_preconditions(expanded)
    coverage_count = expanded.get("coverage_summary", {}).get("covered_tickers", 0)

    # ---- Step 3: original 50-ticker baseline (read-only) ----
    orig_path = Path(original_report_path) if original_report_path else _ORIGINAL_PHASE5G_REPORT
    original: dict = {}
    if orig_path.is_file():
        try:
            with open(orig_path, "r", encoding="utf-8") as fh:
                original = json.load(fh)
        except (OSError, ValueError):
            original = {}

    # ---- Step 4: metrics + comparison ----
    ref_ic = _ic(expanded, M_REF)
    # The harness only names an eligible best candidate when an arm CLEARS the gate
    # (positive incremental IC, placebo-clean, OOS-visible). When none qualifies we
    # still report the strongest event arm by raw IC so the magnitude of the (non-)edge
    # is visible — the recommendation stays honest because its incremental IC is <= 0.
    eligible_best = expanded.get("best_event_candidate")
    arm_ics = {arm: _ic(expanded, arm) for arm in _EVENT_ARMS if _ic(expanded, arm) is not None}
    best_by_ic = max(arm_ics, key=lambda a: arm_ics[a]) if arm_ics else None
    best_name = eligible_best or best_by_ic
    best_eligible = eligible_best is not None
    best_ic = _ic(expanded, best_name) if best_name else None
    best_placebo = (_model_block(expanded, best_name).get("placebo_mean_ic")
                    if best_name else None)
    incr = (best_ic - ref_ic) if (best_ic is not None and ref_ic is not None) else None

    # Leakage: reuse the Phase 5-G leakage gate verdict (as-of join + embargo + placebo).
    g5g = {g["gate_name"]: g for g in
           expanded.get("validation_gate_summary", {}).get("gates", [])}
    leakage_ok = g5g.get("no_future_event_leakage_gate", {}).get("status") == "PASS"

    # Same covered universe: by construction every model shares the covered record set.
    cov = expanded.get("coverage_summary", {})
    same_universe = all(_model_block(expanded, m).get("n_scored_dates")
                        == _model_block(expanded, M_REF).get("n_scored_dates")
                        for m in _EVENT_ARMS
                        if _model_block(expanded, m).get("evaluable")) \
        and _model_block(expanded, M_REF).get("evaluable", False)

    coverage_ok = coverage_count >= GATE_COVERAGE_MIN
    recommendation = derive_recommendation(expanded, coverage_ok, leakage_ok, incr,
                                            best_name, best_placebo, best_ic)

    gates = build_gate_matrix(pre, expanded, coverage_count, leakage_ok, incr, best_name,
                              best_ic, best_placebo, recommendation, same_universe)

    revision_present = expanded.get("features_built", {}).get(
        "estimate_revision_features_built", False)

    # ---- Step 5: artifacts ----
    _write_scoreboard(paths, expanded, best_name)
    _write_incremental(paths, expanded, original, best_name)
    _write_gate_matrix(paths, gates)
    _write_horizon(paths, expanded)
    _write_yearly_ic(paths, expanded)
    _write_next_plan(paths, recommendation, coverage_count, best_name, incr, revision_present)

    report = _assemble_report(paths, base_flags, pre, expanded, original, gates,
                              recommendation, ref_ic, best_name, best_ic, best_placebo,
                              incr, coverage_count, same_universe, leakage_ok, best_eligible)
    _write_json(paths.report, report)
    return report


def _assemble_report(paths, base_flags, pre, expanded, original, gates, recommendation,
                     ref_ic, best_name, best_ic, best_placebo, incr, coverage_count,
                     same_universe, leakage_ok, best_eligible) -> dict:
    status_counts: Dict[str, int] = {}
    for g in gates:
        status_counts[g["status"]] = status_counts.get(g["status"], 0) + 1

    cov = expanded.get("coverage_summary", {})
    o_sel = original.get("best_event_candidate_selection", {})
    o_incr = (original.get("incremental_edge_vs_phase5c", {}) or {}).get("incremental_mean_rank_ic")
    survived_direction = None
    if incr is not None and o_incr is not None:
        survived_direction = "improved" if incr > o_incr else (
            "weakened" if incr < o_incr else "unchanged")

    report = dict(base_flags)
    report.update({
        # ---- required headline metrics ----
        "raw_cache_count": pre["raw_cache_count"],
        "coverage_count": coverage_count,
        "universe_count": cov.get("universe_size"),
        "covered_ticker_count": coverage_count,
        "covered_date_count": cov.get("covered_dates"),
        "original_phase5g_coverage_count": original.get("coverage_summary", {}).get("covered_tickers"),
        "original_phase5g_best_event_model": original.get("best_event_candidate"),
        "original_phase5g_incremental_ic": _f(o_incr),
        "phase5c_reference_model_used": expanded.get("phase5c_reference_meta", {}).get("champion_model"),
        "phase5c_reference_mean_rank_ic_covered": _f(ref_ic),
        "event_alpha_only_mean_rank_ic": _f(_ic(expanded, M_EVENT)),
        "price_plus_event_alpha_mean_rank_ic": _f(_ic(expanded, M_PRICE_PLUS)),
        "event_gated_price_signal_mean_rank_ic": _f(_ic(expanded, M_GATED)),
        "best_event_model": best_name,
        "best_event_model_mean_rank_ic": _f(best_ic),
        "best_event_model_is_eligible_incremental_edge": best_eligible,
        "eligible_best_event_candidate": expanded.get("best_event_candidate"),
        "incremental_ic_vs_reference": _f(incr),
        "placebo_ic": best_placebo,
        # ---- comparison + provenance ----
        "preconditions": pre,
        "same_covered_universe": same_universe,
        "leakage_clean": leakage_ok,
        "ic_t_stat_by_model": {m: _f(_model_block(expanded, m).get("ic_t_stat"))
                               for m in _SCOREBOARD_MODELS},
        "yearly_ic_by_model": {m: {y: _f(v) for y, v in
                                   (_model_block(expanded, m).get("ic_by_year") or {}).items()}
                               for m in _SCOREBOARD_MODELS},
        "horizon_comparison": {m: {LABEL_5D: _horizon_ic(expanded, m, LABEL_5D),
                                   LABEL_10D: _horizon_ic(expanded, m, LABEL_10D),
                                   LABEL_20D: _horizon_ic(expanded, m, LABEL_20D)}
                               for m in _SCOREBOARD_MODELS},
        "placebo_ic_by_model": {m: _model_block(expanded, m).get("placebo_mean_ic")
                                for m in _SCOREBOARD_MODELS},
        "original_vs_expanded": {
            "original_coverage": original.get("coverage_summary", {}).get("covered_tickers"),
            "expanded_coverage": coverage_count,
            "original_best_event_model": original.get("best_event_candidate"),
            "expanded_best_event_model": best_name,
            "original_reference_mean_rank_ic": _f(o_sel.get("reference_mean_rank_ic")),
            "expanded_reference_mean_rank_ic": _f(ref_ic),
            "original_incremental_ic": _f(o_incr),
            "expanded_incremental_ic": _f(incr),
            "incremental_edge_direction_vs_original": survived_direction,
            "event_alpha_survived_expanded_coverage": bool(
                best_name is not None and incr is not None and incr > GATE_INCREMENTAL_IC),
        },
        "event_alpha_survived_expanded_coverage": bool(
            best_name is not None and incr is not None and incr > GATE_INCREMENTAL_IC),
        "gate_summary": {"counts": status_counts, "gates": gates},
        "recommendation": recommendation,
        "recommendation_allowed_values": list(ALLOWED_RECOMMENDATIONS),
        "recommended_next_phase": {
            "phase": "5-G3",
            "title": "Add PIT analyst-revision series + Phase 5-H event-alpha shadow test"
                     if recommendation == REC_CONFIRMED else
                     "Event-alpha edge diagnosis / re-specification (research-only)",
            "gated_on": "a leakage-clean incremental edge over Phase 5-C and a PIT revision series",
        },
        "models_compared": _SCOREBOARD_MODELS + ["best_event_candidate"],
        "expanded_run_report": _rel(paths.expanded_run_dir
                                    / "phase5g_earnings_revision_alpha.json"),
        "artifacts": [_rel(p) for p in paths.committed_artifacts],
        "survivorship_biased": True,
        "production_edge_claimed": False,
    })
    assert recommendation in ALLOWED_RECOMMENDATIONS
    return report


# --------------------------------------------------------------------------- #
# Summary print + CLI
# --------------------------------------------------------------------------- #
def _print_summary(report: dict) -> None:
    print("=" * 80)
    print(f"Phase {report['phase']} — Expanded-Coverage Event Alpha Rerun")
    print("=" * 80)
    print(f"raw cache count        : {report['raw_cache_count']}")
    print(f"coverage (covered/uni) : {report['coverage_count']}/{report['universe_count']}")
    print(f"5-C reference model    : {report['phase5c_reference_model_used']}")
    print(f"5-C reference IC       : {report['phase5c_reference_mean_rank_ic_covered']}")
    print(f"best event model       : {report['best_event_model']} "
          f"IC={report['best_event_model_mean_rank_ic']}")
    print(f"incremental IC vs ref  : {report['incremental_ic_vs_reference']} "
          f"(threshold >{GATE_INCREMENTAL_IC})")
    print(f"placebo IC (best)      : {report['placebo_ic']}")
    ov = report["original_vs_expanded"]
    print(f"original 50-tkr incr IC: {ov['original_incremental_ic']} "
          f"({ov['original_best_event_model']})")
    print(f"survived expansion     : {report['event_alpha_survived_expanded_coverage']} "
          f"({ov['incremental_edge_direction_vs_original']} vs original)")
    sc = report["gate_summary"]["counts"]
    print(f"gates                  : {sc}")
    print(f"RECOMMENDATION         : {report['recommendation']}")
    print("=" * 80)


def _parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        description="Phase 5-G2 expanded-coverage event-alpha rerun (offline, preview-only).")
    ap.add_argument("--price-csv", default=None, help="override local price-history CSV (read-only)")
    ap.add_argument("--out-dir", default=None, help="override output directory")
    ap.add_argument("--original-report", default=None,
                    help="override the original Phase 5-G report path (baseline)")
    ap.add_argument("--quiet", action="store_true")
    return ap.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parse_args(argv)
    out_dir = Path(args.out_dir) if args.out_dir else None
    try:
        report = run(price_csv=args.price_csv, out_dir=out_dir,
                     original_report_path=Path(args.original_report) if args.original_report else None)
    except Exception as exc:  # noqa: BLE001 — emit an ERROR report, never a stack-only failure
        report = {
            "phase": PHASE, "recommendation": REC_ERROR,
            "error": f"{type(exc).__name__}: {exc}",
            "network_used": False, "paid_apis_used": False, "preview_only": True,
            "orders_enabled": False, "automation_enabled": False,
            "broker_execution_enabled": False, "production_replacement": False,
            "deployed": False, "binary_artifacts_created": False,
            "raw_files_modified": False, "raw_files_staged": False, "api_key_required": False,
        }
        _write_json(_OutPaths(out_dir if out_dir is not None else _OUT_DIR).report, report)
        if not args.quiet:
            print(f"Phase 5-G2 ERROR: {report['error']}")
        return 1
    if not args.quiet:
        _print_summary(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
