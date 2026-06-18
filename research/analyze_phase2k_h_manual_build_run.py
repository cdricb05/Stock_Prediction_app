"""Phase 2K-H manual build run tracking artifact (offline, read-only).

Phase 2K-G implemented the import-safe free expanded dataset builder. Phase 2K-H is the
MANUAL build run: the user assembles the full local ticker universe and runs the builder
separately to fetch the free daily adjusted OHLCV panel and write five large outputs onto the
D: data root (D:\\Stock_Prediction_app_data\\phase2k_g\\output). This analyzer does NOT run
that build. It only inspects the D: outputs AFTER the manual run, summarizes the data-quality
report, build summary, and survivorship caveat, lightly reads the two CSVs for header / row
count, and writes one small Git-tracked summary JSON in the C: repo.

It is read-only with respect to the D: data files: it reads the JSON summaries, streams the
CSV headers / row counts, and never rewrites, moves, or deletes anything on the D: drive. It
makes no network call, never runs the builder, trains no model, and claims no production edge.
The full guardrail rationale lives in docs/phase2k_h_manual_build_run_v1.md, not in this
source.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import time
from typing import Any, Dict, List, Optional

PHASE = "2K-H"

_OUTPUT_DIR = os.path.join("research", "output")

# Read-only upstream input: the Phase 2K-G builder-readiness validation (small, in the C: repo).
UPSTREAM_2KG_VALIDATION_JSON = os.path.join(_OUTPUT_DIR, "phase2k_g_builder_validation.json")

# External data root on the D: drive (mirrors the Phase 2K-G builder DEFAULT_DATA_ROOT). The
# manual build writes its large outputs here, never under the C: repo.
DATA_ROOT = r"D:\Stock_Prediction_app_data\phase2k_g"

# Basenames of the five large outputs the manual build writes (mirror the builder).
PRICE_HISTORY_FILENAME = "phase2k_g_expanded_price_history_free.csv"
SCORED_FILENAME = "phase2k_g_expanded_scored_free.csv"
DATA_QUALITY_FILENAME = "phase2k_g_data_quality_report.json"
BUILD_SUMMARY_FILENAME = "phase2k_g_data_build_summary.json"
SURVIVORSHIP_FILENAME = "phase2k_g_survivorship_caveat.json"

# Canonical expected D: paths (used when no data-root override is supplied).
_D_OUTPUT_DIR = os.path.join(DATA_ROOT, "output")
PRICE_HISTORY_CSV = os.path.join(_D_OUTPUT_DIR, PRICE_HISTORY_FILENAME)
SCORED_CSV = os.path.join(_D_OUTPUT_DIR, SCORED_FILENAME)
DATA_QUALITY_JSON = os.path.join(_D_OUTPUT_DIR, DATA_QUALITY_FILENAME)
BUILD_SUMMARY_JSON = os.path.join(_D_OUTPUT_DIR, BUILD_SUMMARY_FILENAME)
SURVIVORSHIP_JSON = os.path.join(_D_OUTPUT_DIR, SURVIVORSHIP_FILENAME)

# The single small output this analyzer writes (stays in the C: repo, Git-tracked).
RESULTS_JSON = os.path.join(_OUTPUT_DIR, "phase2k_h_manual_build_run_summary.json")

# Large outputs must NEVER appear under the C: repo output dir; this lets the analyzer confirm
# nothing large leaked into the repo.
_REAL_OUTPUT_FILENAMES = (PRICE_HISTORY_FILENAME, SCORED_FILENAME, DATA_QUALITY_FILENAME,
                          BUILD_SUMMARY_FILENAME, SURVIVORSHIP_FILENAME)
_LEGACY_REPO_OUTPUT_FILES = tuple(
    os.path.join(_OUTPUT_DIR, name) for name in _REAL_OUTPUT_FILENAMES)

# Stable ordering of the five expected outputs.
_OUTPUT_KEYS = ("expanded_price_history_csv", "expanded_scored_csv",
                "data_quality_report_json", "data_build_summary_json",
                "survivorship_caveat_json")


# --------------------------------------------------------------------------- #
# Read-only helpers
# --------------------------------------------------------------------------- #
def _norm(path: str) -> str:
    return path.replace("\\", "/")


def _isfile(path: str) -> bool:
    return os.path.isfile(path)


def _now() -> str:
    return dt.datetime.now().replace(microsecond=0).isoformat()


def _safe_read_json(path: str) -> Optional[Dict[str, Any]]:
    """Read a JSON file if it exists and parses; otherwise return None. Never raises."""
    if not _isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (ValueError, OSError):
        return None


def _resolve_paths(data_root: str) -> Dict[str, str]:
    """Resolve the five expected output paths under <data-root>/output."""
    out = os.path.join(data_root, "output")
    return {
        "output_dir": out,
        "expanded_price_history_csv": os.path.join(out, PRICE_HISTORY_FILENAME),
        "expanded_scored_csv": os.path.join(out, SCORED_FILENAME),
        "data_quality_report_json": os.path.join(out, DATA_QUALITY_FILENAME),
        "data_build_summary_json": os.path.join(out, BUILD_SUMMARY_FILENAME),
        "survivorship_caveat_json": os.path.join(out, SURVIVORSHIP_FILENAME),
    }


def _csv_metadata(path: str) -> Dict[str, Any]:
    """Stream a CSV for header / row count without loading it into memory.

    Reads the header line, captures a tiny first-row sample (date / ticker only), and counts
    the remaining data lines. The full expanded CSV is never loaded into a frame.
    """
    info: Dict[str, Any] = {
        "path": _norm(path),
        "exists": _isfile(path),
        "header": None,
        "column_count": 0,
        "data_row_count": 0,
        "sample_first_row": None,
    }
    if not info["exists"]:
        return info
    try:
        with open(path, "r", encoding="utf-8", newline="") as f:
            first = f.readline()
            if not first:
                return info
            header = [c.strip() for c in first.rstrip("\r\n").split(",")]
            info["header"] = header
            info["column_count"] = len(header)
            sample = f.readline()
            if sample:
                info["data_row_count"] = 1
                vals = sample.rstrip("\r\n").split(",")
                row = dict(zip(header, vals))
                info["sample_first_row"] = {
                    k: row.get(k) for k in ("date", "ticker") if k in row}
                for _ in f:
                    info["data_row_count"] += 1
    except OSError:
        return info
    return info


# --------------------------------------------------------------------------- #
# Output detection (read-only)
# --------------------------------------------------------------------------- #
def build_expected_outputs(paths: Dict[str, str]) -> Dict[str, str]:
    return {k: _norm(paths[k]) for k in _OUTPUT_KEYS}


def build_detected_outputs(paths: Dict[str, str]) -> Dict[str, Any]:
    return {k: {"path": _norm(paths[k]), "exists": _isfile(paths[k])} for k in _OUTPUT_KEYS}


def _repo_large_outputs_present() -> List[str]:
    return [_norm(p) for p in _LEGACY_REPO_OUTPUT_FILES if _isfile(p)]


# --------------------------------------------------------------------------- #
# Summaries of the manual-build outputs (read-only)
# --------------------------------------------------------------------------- #
def build_upstream_2kg_summary(upstream_path: str) -> Dict[str, Any]:
    data = _safe_read_json(upstream_path)
    if data is None:
        return {"present": False, "path": _norm(upstream_path)}
    dec = data.get("decision", {}) or {}
    nxt = data.get("recommended_next_phase", {}) or {}
    return {
        "present": True,
        "path": _norm(upstream_path),
        "phase": data.get("phase"),
        "builder_script_created": dec.get("builder_script_created"),
        "sample_universe_created": dec.get("sample_universe_created"),
        "ready_for_manual_live_build": dec.get("ready_for_manual_live_build"),
        "recommended_next_phase": nxt.get("phase"),
        "routed_to_2k_h": nxt.get("phase") == "2K-H",
    }


def summarize_data_quality(dq: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if dq is None:
        return {"present": False}
    stats = dq.get("stats", {}) or {}
    checks = dq.get("checks", []) or []
    status = dq.get("status")
    return {
        "present": True,
        "status": status,
        "passed": status in ("PASS", "PASS_WITH_CAVEAT"),
        "row_count": stats.get("row_count"),
        "ticker_count": stats.get("ticker_count"),
        "date_count": stats.get("date_count"),
        "years_span": stats.get("years_span"),
        "min_tickers_per_date": stats.get("min_tickers_per_date"),
        "benchmark_coverage_fraction": stats.get("benchmark_coverage_fraction"),
        "check_count": len(checks),
        "hard_checks_failed": [c.get("check_id") for c in checks
                               if c.get("severity") == "hard" and not c.get("passed")],
    }


def summarize_build_summary(bs: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if bs is None:
        return {"present": False}
    return {
        "present": True,
        "phase": bs.get("phase"),
        "mode": bs.get("mode"),
        "source": bs.get("source"),
        "row_count": bs.get("row_count"),
        "ticker_count": bs.get("ticker_count"),
        "universe_ticker_count": bs.get("universe_ticker_count"),
        "benchmark": bs.get("benchmark"),
        "date_start": bs.get("date_start"),
        "date_end": bs.get("date_end"),
        "years_span": bs.get("years_span"),
        "data_quality_status": bs.get("data_quality_status"),
        "model_trained": bs.get("model_trained"),
        "model_candidate_created": bs.get("model_candidate_created"),
    }


def summarize_survivorship(sc: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if sc is None:
        return {"present": False}
    return {
        "present": True,
        "membership_basis": sc.get("membership_basis"),
        "point_in_time_membership_claimed": sc.get("point_in_time_membership_claimed"),
        "reported_as_survivorship_biased": sc.get("reported_as_survivorship_biased"),
        "clean_point_in_time_build_deferred": sc.get("clean_point_in_time_build_deferred"),
        "universe_ticker_count": sc.get("universe_ticker_count"),
    }


# --------------------------------------------------------------------------- #
# Decision + routing
# --------------------------------------------------------------------------- #
def _decision_reason(*, manual_build_detected: bool, dq_ok: bool, dq_status: Any,
                     pit_claimed: Any, model_candidate_created: Any,
                     repo_large_present: bool, ready: bool) -> str:
    if ready:
        return ("A completed manual build was detected on the D: data root: all five outputs "
                "are present, the data-quality status is PASS or PASS_WITH_CAVEAT, the "
                "survivorship caveat records a current-as-of (not point-in-time) membership "
                "basis, and the build summary confirms no model candidate was created. The "
                "expanded, survivorship-caveated dataset is ready for the residual-signal "
                "retest (Phase 2K-I). No model was trained and no production edge is claimed.")
    if not manual_build_detected:
        return ("No completed manual build was detected on the D: data root "
                "(D:/Stock_Prediction_app_data/phase2k_g/output): one or more of the five "
                "expected outputs is absent. Assemble the full local universe and run the "
                "builder's manual network build mode separately, then re-run this analyzer. "
                "No live build, no model, and no production integration were performed here.")
    blockers: List[str] = []
    if not dq_ok:
        blockers.append(f"data-quality status is not PASS/PASS_WITH_CAVEAT (got {dq_status!r})")
    if pit_claimed is not False:
        blockers.append(
            "survivorship caveat does not confirm point_in_time_membership_claimed = false")
    if model_candidate_created is not False:
        blockers.append(
            "build summary does not confirm that no model candidate was created")
    if repo_large_present:
        blockers.append("large outputs unexpectedly present under the C: repo")
    if not blockers:
        blockers.append("one or more readiness conditions were not met")
    return ("A manual build was detected but it is not yet ready for retest: "
            + "; ".join(blockers) + ". Resolve these and re-run this analyzer. No model was "
            "trained and no production edge is claimed.")


def build_decision(*, detected: Dict[str, Any], dq_sum: Dict[str, Any],
                   bs_sum: Dict[str, Any], sc_sum: Dict[str, Any],
                   repo_large_present: List[str]) -> Dict[str, Any]:
    exists = {k: detected[k]["exists"] for k in _OUTPUT_KEYS}
    manual_build_detected = all(exists.values())
    any_present = any(exists.values())

    dq_ok = bool(dq_sum.get("present") and dq_sum.get("passed"))
    caveat_present = bool(sc_sum.get("present"))
    pit_claimed = (sc_sum.get("point_in_time_membership_claimed")
                   if caveat_present else None)
    model_candidate_created = (bs_sum.get("model_candidate_created")
                               if bs_sum.get("present") else False)
    repo_large = bool(repo_large_present)

    ready = bool(
        manual_build_detected
        and dq_ok
        and exists["expanded_price_history_csv"]
        and exists["expanded_scored_csv"]
        and exists["data_build_summary_json"]
        and exists["survivorship_caveat_json"]
        and caveat_present and (pit_claimed is False)
        and bs_sum.get("present") and (model_candidate_created is False)
        and not repo_large
    )

    return {
        "manual_build_detected": manual_build_detected,
        "ready_for_retest": ready,
        "data_quality_passed": dq_ok,
        "survivorship_caveat_present": caveat_present,
        "point_in_time_membership_claimed": pit_claimed,
        "large_data_on_d_drive": any_present,
        "repo_large_outputs_created": repo_large,
        "model_candidate_created": bool(model_candidate_created),
        "reason": _decision_reason(
            manual_build_detected=manual_build_detected, dq_ok=dq_ok,
            dq_status=dq_sum.get("status"), pit_claimed=pit_claimed,
            model_candidate_created=model_candidate_created,
            repo_large_present=repo_large, ready=ready),
    }


def build_recommended_next_phase(ready: bool) -> Dict[str, str]:
    if ready:
        return {
            "phase": "2K-I",
            "title": "Expanded Dataset Residual Signal Retest",
            "purpose": (
                "Retest the liquidity candidate avg_dollar_volume_21d on the expanded, "
                "survivorship-caveated panel produced by the manual 2K-H build, reporting all "
                "results as survivorship-biased. Still no model training, no model candidate, "
                "and no production integration."),
        }
    return {
        "phase": "2K-H-RUN",
        "title": "Run the Manual Free Expanded Dataset Build",
        "purpose": (
            "The manual free build has not been completed yet. Assemble the full local ticker "
            "universe on the D: data root and run the Phase 2K-G builder's manual network "
            "build mode to produce the five expanded outputs, then re-run this analyzer."),
    }


def build_interpretation() -> Dict[str, Any]:
    return {
        "analyzer_ran_the_build": False,
        "network_calls_made": False,
        "d_drive_files_modified": False,
        "model_trained": False,
        "model_candidate_created": False,
        "production_edge_claimed": False,
        "read_only_with_respect_to_d_drive": True,
        "narrative": (
            "Phase 2K-H is the manual free expanded dataset build run. This analyzer is a "
            "read-only tracking artifact: it inspects the D: outputs after the manual run, "
            "summarizes the data-quality report, build summary, and survivorship caveat, and "
            "writes one small Git-tracked summary JSON. It runs no build, fetches nothing, "
            "modifies no D: file, trains no model, and claims no production edge."
        ),
    }


# --------------------------------------------------------------------------- #
# Assembly
# --------------------------------------------------------------------------- #
def build_results(*, data_root: Optional[str] = None,
                  upstream_path: Optional[str] = None) -> Dict[str, Any]:
    data_root = data_root or DATA_ROOT
    upstream_path = upstream_path or UPSTREAM_2KG_VALIDATION_JSON
    paths = _resolve_paths(data_root)

    detected = build_detected_outputs(paths)
    repo_large_present = _repo_large_outputs_present()

    dq = _safe_read_json(paths["data_quality_report_json"])
    bs = _safe_read_json(paths["data_build_summary_json"])
    sc = _safe_read_json(paths["survivorship_caveat_json"])

    dq_sum = summarize_data_quality(dq)
    bs_sum = summarize_build_summary(bs)
    sc_sum = summarize_survivorship(sc)

    csv_metadata = {
        "expanded_price_history_csv": _csv_metadata(paths["expanded_price_history_csv"]),
        "expanded_scored_csv": _csv_metadata(paths["expanded_scored_csv"]),
    }

    decision = build_decision(detected=detected, dq_sum=dq_sum, bs_sum=bs_sum,
                              sc_sum=sc_sum, repo_large_present=repo_large_present)
    recommended = build_recommended_next_phase(decision["ready_for_retest"])

    return {
        "phase": PHASE,
        "generated_at": _now(),
        "inputs_read": {
            "upstream_phase2k_g_validation_json": _norm(upstream_path),
            "data_quality_report_json": _norm(paths["data_quality_report_json"]),
            "data_build_summary_json": _norm(paths["data_build_summary_json"]),
            "survivorship_caveat_json": _norm(paths["survivorship_caveat_json"]),
            "expanded_price_history_csv": _norm(paths["expanded_price_history_csv"]),
            "expanded_scored_csv": _norm(paths["expanded_scored_csv"]),
        },
        "data_root": _norm(data_root),
        # Safety flags (machine-readable).
        "database_touched": False,
        "database_write_executed": False,
        "migration_executed": False,
        "deployment_executed": False,
        "model_v2_enabled": False,
        "production_edge_claimed": False,
        "no_trading": True,
        "no_orders": True,
        "no_automation": True,
        "expected_d_drive_outputs": build_expected_outputs(paths),
        "detected_outputs": detected,
        "repo_large_outputs_present": repo_large_present,
        "upstream_phase2k_g_summary": build_upstream_2kg_summary(upstream_path),
        "data_quality_summary": dq_sum,
        "build_summary": bs_sum,
        "survivorship_caveat_summary": sc_sum,
        "csv_metadata": csv_metadata,
        "interpretation": build_interpretation(),
        "decision": decision,
        "recommended_next_phase": recommended,
    }


def write_results(results: Dict[str, Any], path: str = RESULTS_JSON) -> None:
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, allow_nan=False)


def run(output_path: str = RESULTS_JSON, *, data_root: Optional[str] = None,
        upstream_path: Optional[str] = None) -> Dict[str, Any]:
    """Inspect the manual-build outputs (read-only) and write the small summary JSON."""
    results = build_results(data_root=data_root, upstream_path=upstream_path)
    write_results(results, output_path)
    return results


def main() -> int:
    t0 = time.perf_counter()
    d = run()
    elapsed = time.perf_counter() - t0
    dec = d["decision"]
    nxt = d["recommended_next_phase"]
    dq = d["data_quality_summary"]
    print(f"[phase2k-h] data root                    : {d['data_root']}")
    print(f"[phase2k-h] manual build detected         : {dec['manual_build_detected']}")
    print(f"[phase2k-h] data-quality status           : {dq.get('status')}")
    print(f"[phase2k-h] survivorship caveat present   : {dec['survivorship_caveat_present']}")
    print(f"[phase2k-h] point-in-time membership claim : "
          f"{dec['point_in_time_membership_claimed']}")
    print(f"[phase2k-h] model candidate created       : {dec['model_candidate_created']}")
    print(f"[phase2k-h] large data on D: drive         : {dec['large_data_on_d_drive']}")
    print(f"[phase2k-h] repo large outputs created     : {dec['repo_large_outputs_created']}")
    print(f"[phase2k-h] ready for retest               : {dec['ready_for_retest']}")
    print(f"[phase2k-h] recommended next phase         : {nxt['phase']} ({nxt['title']})")
    print(f"[phase2k-h] results written                : {RESULTS_JSON}")
    print(f"[phase2k-h] elapsed seconds                : {elapsed:.2f}")
    print("[phase2k-h] read-only; no build, no network, no D: writes, no model.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
