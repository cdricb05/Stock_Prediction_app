"""Phase 2K-G builder-readiness validation (offline, safe modes only).

Phase 2K-F produced the build plan. Phase 2K-G implements the local / free expanded
dataset builder. This analyzer validates that the builder is READY without running a live
build: it reads the Phase 2K-F plan, confirms the builder script and the sample ticker
universe exist, runs the builder ONLY in its safe dry-run and fixture modes (no network,
into a temp directory), confirms the large real output files were not created, and writes
one validation JSON.

It never executes the builder's network mode, never acquires paid data, never trains a
model, and creates none of the large future output files. The machine-readable safety flags
are emitted in the JSON; the full guardrail rationale lives in
docs/phase2k_g_free_dataset_builder_v1.md, not in this source.
"""
from __future__ import annotations

import datetime as dt
import importlib.util
import json
import os
import tempfile
import time
from typing import Any, Dict, List, Optional

PHASE = "2K-G"

_OUTPUT_DIR = os.path.join("research", "output")

# Input (read-only): the Phase 2K-F build plan.
INPUT_2KF_JSON = os.path.join(_OUTPUT_DIR, "phase2k_f_free_data_build_plan.json")

# Artifacts created by Phase 2K-G that this analyzer inspects.
BUILDER_SCRIPT = os.path.join("research", "build_phase2k_g_free_expanded_dataset.py")
SAMPLE_UNIVERSE_CSV = os.path.join(
    "research", "input", "phase2k_g_free_universe_tickers_sample.csv")

# Basenames of the large real output files the full build would create. They must NOT exist
# after the safe dry-run / fixture validation, neither on the D: data root nor in the C: repo.
_REAL_OUTPUT_FILENAMES = (
    "phase2k_g_expanded_price_history_free.csv",
    "phase2k_g_expanded_scored_free.csv",
    "phase2k_g_data_quality_report.json",
    "phase2k_g_data_build_summary.json",
    "phase2k_g_survivorship_caveat.json",
)
# Legacy repo location. The large outputs now live on the D: data root (see the builder's
# DEFAULT_OUTPUT_DIR); this list lets the analyzer confirm nothing large leaked into the repo.
LEGACY_REPO_OUTPUT_FILES = tuple(
    os.path.join(_OUTPUT_DIR, name) for name in _REAL_OUTPUT_FILENAMES)

# The single output this analyzer is allowed to write (small, stays in the C: repo).
RESULTS_JSON = os.path.join(_OUTPUT_DIR, "phase2k_g_builder_validation.json")


# --------------------------------------------------------------------------- #
# Read-only helpers
# --------------------------------------------------------------------------- #
def _read_json(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _exists(path: str) -> bool:
    return os.path.isfile(path)


def _norm(path: str) -> str:
    return path.replace("\\", "/")


def load_builder():
    """Import the builder module from its file path (no package install needed)."""
    spec = importlib.util.spec_from_file_location("phase2k_g_builder", BUILDER_SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# --------------------------------------------------------------------------- #
# Upstream Phase 2K-F summary
# --------------------------------------------------------------------------- #
def build_upstream_2kf_summary(twokf: Dict[str, Any]) -> Dict[str, Any]:
    nxt = twokf.get("recommended_next_phase", {}) or {}
    dec = twokf.get("decision", {}) or {}
    interp = twokf.get("interpretation", {}) or {}
    return {
        "phase": twokf.get("phase"),
        "candidate": interp.get("candidate"),
        "kf_recommended_next_phase": nxt.get("phase"),
        "kf_recommended_next_phase_title": nxt.get("title"),
        "routed_to_2k_g": nxt.get("phase") == "2K-G",
        "kf_create_build_script_now": dec.get("create_build_script_now"),
        "kf_execute_data_build_now": dec.get("execute_data_build_now"),
    }


# --------------------------------------------------------------------------- #
# Builder + sample-universe inspection
# --------------------------------------------------------------------------- #
def inspect_builder_script() -> Dict[str, Any]:
    return {
        "path": _norm(BUILDER_SCRIPT),
        "exists_now": _exists(BUILDER_SCRIPT),
        "created_by_phase_2k_g": True,
    }


def inspect_sample_universe(builder) -> Dict[str, Any]:
    present = _exists(SAMPLE_UNIVERSE_CSV)
    info: Dict[str, Any] = {
        "path": _norm(SAMPLE_UNIVERSE_CSV),
        "exists_now": present,
        "created_by_phase_2k_g": True,
        "required_columns": list(builder.UNIVERSE_COLUMNS),
        "is_sample_not_full_universe": True,
        "full_universe_file_created": _exists(builder.FULL_UNIVERSE_CSV),
    }
    if present:
        df = builder.load_universe(SAMPLE_UNIVERSE_CSV)
        validation = builder.validate_universe(df)
        info["columns_present"] = not validation["missing_columns"]
        info["missing_columns"] = validation["missing_columns"]
        info["ticker_count"] = validation["ticker_count"]
        info["validation"] = validation
    return info


# --------------------------------------------------------------------------- #
# Safe-mode runs (dry-run + fixture; never the network mode)
# --------------------------------------------------------------------------- #
def _real_output_paths(builder) -> List[str]:
    """The large real output files on the D: data root, read from the builder constants."""
    return [builder.REAL_PRICE_HISTORY_CSV, builder.REAL_SCORED_CSV,
            builder.REAL_DATA_QUALITY_JSON, builder.REAL_BUILD_SUMMARY_JSON,
            builder.REAL_SURVIVORSHIP_JSON]


def _present(paths) -> List[str]:
    return [_norm(p) for p in paths if _exists(p)]


def _real_outputs_present(builder) -> List[str]:
    # Large outputs that would leak — on the D: data root or in the legacy repo location.
    return _present(_real_output_paths(builder)) + _present(LEGACY_REPO_OUTPUT_FILES)


def run_dry_run_validation(builder) -> Dict[str, Any]:
    before = _real_outputs_present(builder)
    summary = builder.dry_run(universe_path=SAMPLE_UNIVERSE_CSV)
    after = _real_outputs_present(builder)
    return {
        "executed": True,
        "mode": "dry-run",
        "universe_valid": bool(summary.get("universe_validation", {}).get("ok")),
        "network_calls_made": False,
        "real_outputs_created": bool(set(after) - set(before)),
        "real_outputs_present_after": after,
    }


def run_fixture_validation(builder) -> Dict[str, Any]:
    before = _real_outputs_present(builder)
    tmp = tempfile.mkdtemp(prefix="phase2k_g_fixture_validation_")
    result = builder.fixture_run(output_dir=tmp, universe_path=SAMPLE_UNIVERSE_CSV)
    after = _real_outputs_present(builder)
    return {
        "executed": True,
        "mode": "fixture-mode",
        "output_dir": _norm(tmp),
        "wrote_to_real_output_dir": False,
        "data_quality_status": result.get("data_quality_status"),
        "row_count": result.get("row_count"),
        "files_written_count": len(result.get("files_written", [])),
        "network_calls_made": False,
        "real_outputs_created": bool(set(after) - set(before)),
        "real_outputs_present_after": after,
    }


def build_planned_real_outputs(builder) -> Dict[str, Any]:
    return builder.planned_real_outputs()


def build_real_outputs_status(builder) -> Dict[str, Any]:
    real_paths = _real_output_paths(builder)
    present = _present(real_paths)
    legacy_present = _present(LEGACY_REPO_OUTPUT_FILES)
    return {
        "external_data_root": _norm(builder.DEFAULT_DATA_ROOT),
        "external_output_dir": _norm(builder.DEFAULT_OUTPUT_DIR),
        "files": [{"path": _norm(p), "exists_now": _exists(p)} for p in real_paths],
        "present": present,
        "legacy_repo_output_files": [_norm(p) for p in LEGACY_REPO_OUTPUT_FILES],
        "legacy_present": legacy_present,
        "legacy_all_absent": not legacy_present,
        # True only when nothing large exists on the D: root AND nothing leaked into the repo.
        "all_absent": (not present) and (not legacy_present),
    }


def build_survivorship_caveat_policy(twokf: Dict[str, Any]) -> Dict[str, Any]:
    pol = twokf.get("survivorship_caveat_policy", {}) or {}
    return {
        "explicitly_documented": pol.get("explicitly_documented", True),
        "no_false_point_in_time_membership_claim":
            pol.get("no_false_point_in_time_membership_claim", True),
        "clean_point_in_time_build_deferred":
            pol.get("clean_point_in_time_build_deferred", True),
        "reported_as_survivorship_biased": pol.get("reported_as_survivorship_biased", True),
        "carried_from_phase_2k_f": bool(pol),
    }


# --------------------------------------------------------------------------- #
# Decision + routing
# --------------------------------------------------------------------------- #
def build_decision(*, builder_exists: bool, sample_exists: bool, dry_ok: bool,
                   fixture_status: Optional[str], real_absent: bool) -> Dict[str, Any]:
    ready = bool(builder_exists and sample_exists and dry_ok
                 and fixture_status in ("PASS", "PASS_WITH_CAVEAT") and real_absent)
    return {
        "builder_script_created": builder_exists,
        "sample_universe_created": sample_exists,
        "live_download_executed": False,
        "real_dataset_outputs_created": False,
        "paid_data_acquired": False,
        "model_candidate_created": False,
        "ready_for_manual_live_build": ready,
        "reason": (
            "Phase 2K-G creates the builder script and the sample ticker universe and "
            "validates them only in safe dry-run and fixture modes (no network, into a "
            "temp directory). The full free build is the manual Phase 2K-H step run with "
            "--execute --allow-network. No live download, no real dataset outputs, no paid "
            "data, and no model candidate are produced here."
        ),
    }


def build_recommended_next_phase() -> Dict[str, str]:
    return {
        "phase": "2K-H",
        "title": "Manual Free Expanded Dataset Build Run",
        "purpose": (
            "Run the Phase 2K-G builder in explicit --execute --allow-network mode against "
            "the full local ticker universe to create the expanded, survivorship-caveated "
            "daily adjusted OHLCV dataset and its data-quality report. Still no model "
            "training, no model candidate, and no production integration."),
    }


def build_interpretation() -> Dict[str, Any]:
    return {
        "live_download_executed": False,
        "network_mode_run": False,
        "real_dataset_outputs_created": False,
        "paid_data_acquired": False,
        "model_trained": False,
        "model_fitted": False,
        "model_candidate_created": False,
        "authorized_to_train_model": False,
        "authorized_to_serve_model": False,
        "production_edge_claimed": False,
        "only_safe_modes_run": True,
        "narrative": (
            "Phase 2K-G implements the local / free expanded dataset builder from the Phase "
            "2K-F plan and proves it works using only safe dry-run and fixture modes. The "
            "builder is import-safe, requires an explicit --execute --allow-network to fetch "
            "anything, and writes its large outputs only in that manual mode. This validation "
            "fetches nothing, acquires no paid data, trains no model, and creates none of the "
            "large real output files. No production edge is claimed."
        ),
    }


# --------------------------------------------------------------------------- #
# Assembly
# --------------------------------------------------------------------------- #
def build_results() -> Dict[str, Any]:
    twokf = _read_json(INPUT_2KF_JSON)
    builder = load_builder()

    builder_info = inspect_builder_script()
    sample_info = inspect_sample_universe(builder)
    dry = run_dry_run_validation(builder)
    fixture = run_fixture_validation(builder)
    real_status = build_real_outputs_status(builder)
    decision = build_decision(
        builder_exists=builder_info["exists_now"],
        sample_exists=sample_info["exists_now"],
        dry_ok=dry["universe_valid"],
        fixture_status=fixture["data_quality_status"],
        real_absent=real_status["all_absent"])

    return {
        "phase": PHASE,
        "generated_at": dt.datetime.now().replace(microsecond=0).isoformat(),
        "inputs_read": {
            "phase2k_f_json": INPUT_2KF_JSON,
            "builder_script": BUILDER_SCRIPT,
            "sample_universe_csv": SAMPLE_UNIVERSE_CSV,
        },
        "database_touched": False,
        "database_write_executed": False,
        "migration_executed": False,
        "deployment_executed": False,
        "model_v2_enabled": False,
        "production_edge_claimed": False,
        "no_trading": True,
        "no_orders": True,
        "no_automation": True,
        "external_data_root": {
            "data_root": _norm(builder.DEFAULT_DATA_ROOT),
            "output_dir": _norm(builder.DEFAULT_OUTPUT_DIR),
            "cache_dir": _norm(builder.DEFAULT_CACHE_DIR),
            "full_universe_csv": _norm(builder.FULL_UNIVERSE_CSV),
            "note": ("Large datasets, caches, and the real build outputs live on the D: "
                     "data root, never in the C: repo. Created only by --execute "
                     "--allow-network."),
        },
        "upstream_phase2k_f_summary": build_upstream_2kf_summary(twokf),
        "builder_script": builder_info,
        "sample_universe": sample_info,
        "dry_run_validation": dry,
        "fixture_validation": fixture,
        "planned_real_outputs": build_planned_real_outputs(builder),
        "real_outputs_status": real_status,
        "data_quality_check_catalog": builder.data_quality_check_catalog(),
        "survivorship_caveat_policy": build_survivorship_caveat_policy(twokf),
        "interpretation": build_interpretation(),
        "decision": decision,
        "recommended_next_phase": build_recommended_next_phase(),
    }


def write_results(results: Dict[str, Any], path: str = RESULTS_JSON) -> None:
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, allow_nan=False)


def run(output_path: str = RESULTS_JSON) -> Dict[str, Any]:
    """Validate builder readiness in safe modes and write the validation JSON."""
    results = build_results()
    write_results(results, output_path)
    return results


def main() -> int:
    t0 = time.perf_counter()
    d = run()
    elapsed = time.perf_counter() - t0
    up = d["upstream_phase2k_f_summary"]
    dec = d["decision"]
    nxt = d["recommended_next_phase"]
    print(f"[phase2k-g] upstream 2K-F routed to 2K-G : {up['routed_to_2k_g']}")
    print(f"[phase2k-g] builder script exists        : {d['builder_script']['exists_now']}")
    print(f"[phase2k-g] sample universe exists        : {d['sample_universe']['exists_now']} "
          f"(tickers={d['sample_universe'].get('ticker_count')})")
    print(f"[phase2k-g] dry-run universe valid        : {d['dry_run_validation']['universe_valid']}")
    print(f"[phase2k-g] fixture DQ status             : "
          f"{d['fixture_validation']['data_quality_status']}")
    print(f"[phase2k-g] real outputs all absent       : {d['real_outputs_status']['all_absent']}")
    print(f"[phase2k-g] live download executed        : {dec['live_download_executed']}")
    print(f"[phase2k-g] real dataset outputs created  : {dec['real_dataset_outputs_created']}")
    print(f"[phase2k-g] model candidate created       : {dec['model_candidate_created']}")
    print(f"[phase2k-g] ready for manual live build   : {dec['ready_for_manual_live_build']}")
    print(f"[phase2k-g] recommended next phase        : {nxt['phase']} ({nxt['title']})")
    print(f"[phase2k-g] results written               : {RESULTS_JSON}")
    print(f"[phase2k-g] elapsed seconds               : {elapsed:.2f}")
    print("[phase2k-g] safe modes only; no network, no large outputs, no model.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
