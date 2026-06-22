"""Phase 5-G1D - Post-Live Cache Integration and Coverage Rebuild.

Phase 5-G1C ran a controlled live Alpha Vantage earnings collection batch (Batch 1) that grew the
Phase 3-M raw cache from 50 to 70 ticker payloads. All 20 new raw files exist, are gitignored, and
contain quarterlyEarnings. BUT the committed-safe Phase 3-M earnings events / features artifacts
were not rebuilt from the new 70-file raw cache, so coverage still reads 50 / 128. This phase closes
that gap: it integrates the newly cached raw payloads into the committed-safe
earnings_events_universe.csv / earnings_features_universe.csv using LOCAL CACHE ONLY, with zero
network calls and no API key.

How (reuse, do not reinvent). The Phase 3-M collector
(research/run_phase3m_earnings_estimates_signal_gate.py) ALREADY has a cache-only / offline rebuild
path: when run() is invoked with no supported provider API key in the environment, detect_providers
returns None and the run takes the "no provider key" branch (it never constructs a ProviderClient,
so no network code is reachable). That branch calls _detect_cached_provider + _load_cached_events to
read and normalize every raw payload already on disk, rebuilds the earnings events / features /
coverage from cache, and writes the committed-safe artifacts via _finish_collecting. This phase runs
exactly that path - it strips every supported provider key from the env handed to run(), which
GUARANTEES the cache-only branch regardless of the ambient shell - and then records a documented
audit of what was rebuilt.

Safety. Cache-only: zero network, no API key read or required, no ticker fetched. No raw file is
deleted, modified, or untracked; newly collected raw payloads stay gitignored and unstaged. Only
committed-safe text artifacts are written. This phase trains no model, computes no prediction, runs
the IC signal gate only if coverage already clears the Phase 3-M minimum, touches no database,
restarts no service, places no order, enables no automation, and creates no binary artifact. It does
NOT run Batch 2 and does NOT run Phase 5-G2. It does not commit and does not push.
"""
from __future__ import annotations

import csv
import hashlib
import importlib.util
import json
import os
import subprocess
from datetime import datetime

PHASE = "5-G1D"
OBJECTIVE = ("Integrate the newly collected raw Alpha Vantage earnings payloads into the "
             "committed-safe Phase 3-M earnings events/features artifacts using local cache only, "
             "with zero network calls and no API key, then audit coverage and git hygiene.")

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PHASE3M_PATH = os.path.join(_REPO_ROOT, "research",
                             "run_phase3m_earnings_estimates_signal_gate.py")
_OUT_DIR = os.path.join(_REPO_ROOT, "research", "output",
                        "phase5g1d_post_live_cache_integration")

REPORT_NAME = "phase5g1d_post_live_cache_integration.json"
AUDIT_CSV_NAME = "post_live_cache_integration_audit.csv"
PARSE_STATUS_CSV_NAME = "earnings_cache_parse_status.csv"
NEXT_PLAN_NAME = "phase5g1e_next_collection_or_g2_plan.json"

# Coverage gate for proceeding to the Phase 5-G2 event-alpha rerun (mirrors the Phase 3-M
# PASS_MIN_EARNINGS_FEATURE_TICKERS / DEFAULT_MIN_TICKERS_FOR_SIGNAL_GATE = 75).
TARGET_MIN_COVERAGE_FOR_G2 = 75

# Recommendation vocabulary.
REC_SUCCESS_NEEDS_BATCH2 = "CACHE_INTEGRATION_SUCCESS_NEEDS_BATCH2"
REC_SUCCESS_READY_FOR_G2 = "CACHE_INTEGRATION_SUCCESS_READY_FOR_G2"
REC_PARTIAL_REVIEW = "CACHE_INTEGRATION_PARTIAL_REVIEW_REQUIRED"
REC_BLOCKED = "CACHE_INTEGRATION_BLOCKED"
REC_ERROR = "ERROR"
ALLOWED_RECOMMENDATIONS = [
    REC_SUCCESS_NEEDS_BATCH2, REC_SUCCESS_READY_FOR_G2, REC_PARTIAL_REVIEW, REC_BLOCKED, REC_ERROR,
]

PARSE_STATUS_COLUMNS = [
    "ticker", "raw_file_exists", "quarterly_earnings_count", "has_quarterly_earnings",
    "provider_note_or_error_flag", "point_in_time_usable", "newly_collected", "integrated", "note",
]
AUDIT_COLUMNS = ["check", "value", "detail"]


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _load_phase3m():
    """Import the Phase 3-M collector module (its functions are reused; nothing is re-implemented)."""
    spec = importlib.util.spec_from_file_location("phase3m_under_phase5g1d", _PHASE3M_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _distinct_feature_tickers(csv_path):
    """Coverage = distinct non-SPY tickers present in an earnings features CSV."""
    out = set()
    if not os.path.isfile(csv_path):
        return out
    with open(csv_path, "r", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            t = (row.get("ticker") or "").strip().upper()
            if t and t != "SPY":
                out.add(t)
    return out


def _count_raw_json(raw_dir):
    if not os.path.isdir(raw_dir):
        return 0
    return sum(1 for fn in os.listdir(raw_dir)
               if fn.endswith(".json") and os.path.isfile(os.path.join(raw_dir, fn)))


def _git(args, cwd=_REPO_ROOT):
    """Run a git command read-only; return (exit_code, stdout). Never raises on non-zero."""
    try:
        p = subprocess.run(["git"] + args, cwd=cwd, capture_output=True, text=True, timeout=60)
        return p.returncode, (p.stdout or "")
    except (OSError, subprocess.SubprocessError):
        return 127, ""


def _path_is_gitignored(path):
    code, _ = _git(["check-ignore", path])
    return code == 0


def _staged_raw_files(raw_rel_prefix):
    """Return any staged paths under the raw payload dir (should always be empty)."""
    code, out = _git(["diff", "--cached", "--name-only"])
    if code != 0:
        return []
    return [ln.strip() for ln in out.splitlines()
            if ln.strip() and raw_rel_prefix in ln.replace("\\", "/")]


def _hash_dir_files(d):
    """Map of filename -> sha256 for the immediate files in a dir (not recursing into raw/)."""
    snap = {}
    if not os.path.isdir(d):
        return snap
    for fn in sorted(os.listdir(d)):
        fp = os.path.join(d, fn)
        if os.path.isfile(fp):
            with open(fp, "rb") as f:
                snap[fn] = hashlib.sha256(f.read()).hexdigest()
    return snap


def _provider_note_flag(payload):
    """Return the AV throttle/error marker key if the payload carries one, else ''."""
    if isinstance(payload, dict):
        for k in ("Note", "Information", "Error Message"):
            if payload.get(k):
                return k
    return ""


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def run(out_dir=None, env=None, apply=True, verbose=True):
    """Integrate the cached raw earnings payloads into the committed-safe Phase 3-M artifacts.

    apply=True  -> actually invoke the Phase 3-M cache-only rebuild (rewrites committed-safe
                   artifacts in research/output/phase3m_earnings_estimates_signal_gate/).
    apply=False -> audit / projection only (in-memory rebuild); production artifacts untouched.
                   Tests use apply=False so they never overwrite committed production state.
    """
    out_dir = _OUT_DIR if out_dir is None else out_dir
    os.makedirs(out_dir, exist_ok=True)
    try:
        return _run_inner(out_dir, env, apply, verbose)
    except Exception as exc:  # never crash: emit a documented ERROR report instead
        report = _base_report()
        report["recommendation"] = REC_ERROR
        report["recommended_next_phase"] = {
            "phase": "5-G1D", "title": "Diagnose Cache Integration Error",
            "detail": "An unexpected error occurred during cache integration; inspect and re-run."}
        report["error"] = "%s: %s" % (type(exc).__name__, exc)
        _dump_json(os.path.join(out_dir, REPORT_NAME), report)
        if verbose:
            print("Phase 5-G1D ERROR: %s" % report["error"])
        return report


def _base_report():
    return {
        "phase": PHASE,
        "objective": OBJECTIVE,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "raw_cache_count_before": None,
        "raw_cache_count_after": None,
        "coverage_count_before": None,
        "coverage_count_after": None,
        "new_cached_tickers_detected": [],
        "new_tickers_integrated": [],
        "failed_tickers": [],
        "network_used": False,
        "api_key_required": False,
        "api_key_used": False,
        "raw_files_deleted": False,
        "raw_files_untracked": False,
        "raw_files_staged": False,
        "committed_safe_artifacts_modified": [],
        "recommendation": REC_ERROR,
        "recommended_next_phase": {},
        "preview_only": True,
        "orders_enabled": False,
        "automation_enabled": False,
        "broker_execution_enabled": False,
        "production_replacement": False,
        "deployed": False,
        "binary_artifacts_created": False,
    }


def _run_inner(out_dir, env, apply, verbose):
    p3m = _load_phase3m()
    raw_dir = p3m._RAW_DIR
    m_dir = p3m._M_DIR
    features_csv = p3m.EARNINGS_FEATURES_CSV
    raw_rel_prefix = "research/output/phase3m_earnings_estimates_signal_gate/raw/"

    # ---- before snapshot (read-only) ----
    coverage_before_set = _distinct_feature_tickers(features_csv)
    coverage_before = len(coverage_before_set)
    raw_before = _count_raw_json(raw_dir)

    # ---- cache-only env: strip EVERY supported provider key so detect_providers() returns None,
    #      which forces the Phase 3-M no-key cache-only rebuild branch (no ProviderClient, no net) --
    src_env = dict(os.environ if env is None else env)
    cache_only_env = {k: v for k, v in src_env.items() if k not in p3m.SUPPORTED_ENV_VARS}
    api_key_was_present = any(bool((src_env.get(v) or "").strip()) for v in p3m.SUPPORTED_ENV_VARS)

    # ---- discover cached provider + parse every cached payload in-memory (read-only) ----
    sector_map = p3m.load_sector_map(p3m.SECTOR_MAP_CSV)
    sector_of = {t: info.get("sector", "") for t, info in sector_map.items()}
    universe = p3m.load_phase3l_universe(p3m.PHASE3L_IDENTITY_CSV, sector_map)
    provider, cached_set = p3m._detect_cached_provider(raw_dir, universe)
    events_by_ticker = p3m._load_cached_events(raw_dir, provider, universe)
    projected_feature_rows = p3m.build_earnings_features(events_by_ticker)
    projected_cov_set = {r["ticker"] for r in projected_feature_rows}

    new_cached_tickers_detected = sorted(t for t in cached_set if t not in coverage_before_set)

    # ---- per-ticker parse status (for the audit CSV) ----
    parse_rows = []
    for t in sorted(cached_set):
        cache_path = os.path.join(raw_dir, "%s_%s.json" % (provider, t.upper()))
        exists = os.path.isfile(cache_path)
        nq, has_q, note_flag, pit_usable = 0, False, "", False
        note = ""
        if exists:
            try:
                with open(cache_path, "r", encoding="utf-8") as f:
                    payload = json.load(f)
                quarters = (payload or {}).get("quarterlyEarnings", []) or []
                nq = len(quarters)
                has_q = nq > 0
                note_flag = _provider_note_flag(payload)
                evs = p3m.normalize_events(t, provider, payload)
                pit_usable = any(e.get("point_in_time_usable") for e in evs)
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                note = "parse_error: %s" % type(exc).__name__
        integrated = t in projected_cov_set
        if note_flag:
            note = (note + "; " if note else "") + "provider_marker=%s" % note_flag
        if not integrated and not note:
            note = "no usable point-in-time earnings events"
        parse_rows.append({
            "ticker": t, "raw_file_exists": exists, "quarterly_earnings_count": nq,
            "has_quarterly_earnings": has_q, "provider_note_or_error_flag": note_flag,
            "point_in_time_usable": pit_usable,
            "newly_collected": t in new_cached_tickers_detected, "integrated": integrated,
            "note": note,
        })

    # ---- snapshot committed-safe artifact hashes before applying ----
    before_hashes = _hash_dir_files(m_dir)

    network_used = False
    api_key_used = False
    p3m_recommendation = None
    if apply:
        # Invoke the existing Phase 3-M cache-only rebuild path (no key -> no network).
        result = p3m.run(env=cache_only_env, verbose=False)
        # Truthfully record the collector's own safety signals.
        network_used = bool(result.get("network_used") or result.get("vendor_called")
                            or result.get("provider_network_used"))
        api_key_used = result.get("selected_provider") is not None
        p3m_recommendation = (result.get("recommendation", {}) or {}).get("recommendation") \
            if isinstance(result.get("recommendation"), dict) else result.get("recommendation")
        coverage_after_set = _distinct_feature_tickers(features_csv)
    else:
        coverage_after_set = set(projected_cov_set)

    coverage_after = len(coverage_after_set)
    raw_after = _count_raw_json(raw_dir)

    after_hashes = _hash_dir_files(m_dir)
    committed_safe_artifacts_modified = sorted(
        fn for fn in set(before_hashes) | set(after_hashes)
        if before_hashes.get(fn) != after_hashes.get(fn))

    new_tickers_integrated = sorted(coverage_after_set - coverage_before_set)
    # Cached tickers that did not become covered (parsed to no usable point-in-time events).
    failed_tickers = sorted(t for t in cached_set if t not in (coverage_after_set | projected_cov_set))

    # ---- git hygiene (read-only) ----
    sample_new_raw = (os.path.join(raw_rel_prefix, "%s_%s.json" % (provider, new_cached_tickers_detected[0]))
                      if new_cached_tickers_detected else
                      os.path.join(raw_rel_prefix, "%s_ZZZZ_HYPOTHETICAL.json" % provider))
    new_raw_ignored = _path_is_gitignored(sample_new_raw)
    staged_raw = _staged_raw_files(raw_rel_prefix)
    raw_files_staged = len(staged_raw) > 0

    # ---- recommendation ----
    # Success = every cached ticker that parses is integrated (failed_tickers empty). This is
    # state-robust: re-running after the rebuild (coverage already reflects the cache) is still a
    # success, not "partial". The 75-ticker threshold then splits READY_FOR_G2 vs NEEDS_BATCH2.
    if network_used or api_key_used:
        recommendation = REC_BLOCKED
    elif failed_tickers:
        recommendation = REC_PARTIAL_REVIEW
    elif coverage_after >= TARGET_MIN_COVERAGE_FOR_G2:
        recommendation = REC_SUCCESS_READY_FOR_G2
    else:
        recommendation = REC_SUCCESS_NEEDS_BATCH2

    batch2_needed = coverage_after < TARGET_MIN_COVERAGE_FOR_G2
    g2_allowed = coverage_after >= TARGET_MIN_COVERAGE_FOR_G2
    still_needed = max(0, TARGET_MIN_COVERAGE_FOR_G2 - coverage_after)
    if recommendation == REC_SUCCESS_READY_FOR_G2:
        recommended_next_phase = {
            "phase": "5-G2",
            "title": "Run Event-Alpha Rerun on Expanded Earnings Coverage",
            "detail": "Coverage now clears the %d-ticker minimum; the event-alpha signal rerun is "
                      "allowed. (Not run here.)" % TARGET_MIN_COVERAGE_FOR_G2}
    elif recommendation == REC_SUCCESS_NEEDS_BATCH2:
        recommended_next_phase = {
            "phase": "5-G1C-BATCH2",
            "title": "Run Controlled Live Earnings Collection Batch 2",
            "detail": "Coverage is %d / 128; collect at least %d more tickers via the Phase 5-G1 "
                      "wrapper to reach the %d minimum, then re-run Phase 5-G1D, then Phase 5-G2. "
                      "(Not run here.)" % (coverage_after, still_needed, TARGET_MIN_COVERAGE_FOR_G2)}
    else:
        recommended_next_phase = {
            "phase": "5-G1D",
            "title": "Review Partial Cache Integration",
            "detail": "Some cached tickers did not yield usable point-in-time earnings events; "
                      "inspect earnings_cache_parse_status.csv before continuing."}

    # ---- assemble report ----
    report = _base_report()
    report.update({
        "apply": bool(apply),
        "cache_only_mode": True,
        "existing_phase3m_cache_only_rebuild_path": True,
        "existing_phase3m_cache_only_rebuild_path_note":
            "run_phase3m_earnings_estimates_signal_gate.run() with no provider key takes the "
            "no-key branch (lines ~2054-2090): _detect_cached_provider + _load_cached_events + "
            "_finish_collecting rebuild events/features/coverage from cache with no ProviderClient "
            "and no network; this phase forces that branch by stripping provider keys from the env.",
        "provider_of_cache": provider,
        "raw_cache_count_before": raw_before,
        "raw_cache_count_after": raw_after,
        "coverage_count_before": coverage_before,
        "coverage_count_after": coverage_after,
        "universe_ticker_count": len(universe),
        "new_cached_tickers_detected": new_cached_tickers_detected,
        "new_tickers_integrated": new_tickers_integrated,
        "failed_tickers": failed_tickers,
        "network_used": bool(network_used),
        "api_key_required": False,
        "api_key_present_in_env_was_ignored": bool(api_key_was_present),
        "api_key_used": bool(api_key_used),
        "raw_files_deleted": raw_after < raw_before,
        "raw_files_untracked": False,
        "raw_files_staged": raw_files_staged,
        "staged_raw_files": staged_raw,
        "new_raw_payload_gitignored": bool(new_raw_ignored),
        "committed_safe_artifacts_modified": committed_safe_artifacts_modified,
        "phase3m_collection_recommendation": p3m_recommendation,
        "target_min_coverage_for_g2": TARGET_MIN_COVERAGE_FOR_G2,
        "batch2_needed": bool(batch2_needed),
        "phase5g2_allowed": bool(g2_allowed),
        "recommendation": recommendation,
        "recommended_next_phase": recommended_next_phase,
    })

    # ---- audit CSV ----
    audit_rows = [
        {"check": "cache_only_mode", "value": True, "detail": "provider keys stripped from env"},
        {"check": "network_used", "value": bool(network_used), "detail": "no provider network reachable in no-key branch"},
        {"check": "api_key_required", "value": False, "detail": "rebuild reads only local raw cache"},
        {"check": "api_key_used", "value": bool(api_key_used), "detail": "selected_provider is None in cache-only rebuild"},
        {"check": "raw_cache_count_before", "value": raw_before, "detail": "*.json under raw/"},
        {"check": "raw_cache_count_after", "value": raw_after, "detail": "*.json under raw/"},
        {"check": "raw_files_deleted", "value": raw_after < raw_before, "detail": "no raw file deleted"},
        {"check": "raw_files_untracked", "value": False, "detail": "no git rm performed"},
        {"check": "raw_files_staged", "value": raw_files_staged, "detail": ",".join(staged_raw) or "none staged"},
        {"check": "new_raw_payload_gitignored", "value": bool(new_raw_ignored), "detail": sample_new_raw},
        {"check": "coverage_count_before", "value": coverage_before, "detail": "distinct tickers in features csv"},
        {"check": "coverage_count_after", "value": coverage_after, "detail": "distinct tickers in features csv"},
        {"check": "new_cached_tickers_detected", "value": len(new_cached_tickers_detected),
         "detail": ",".join(new_cached_tickers_detected)},
        {"check": "new_tickers_integrated", "value": len(new_tickers_integrated),
         "detail": ",".join(new_tickers_integrated)},
        {"check": "failed_tickers", "value": len(failed_tickers), "detail": ",".join(failed_tickers) or "none"},
        {"check": "committed_safe_artifacts_modified", "value": len(committed_safe_artifacts_modified),
         "detail": ",".join(committed_safe_artifacts_modified) or "none (audit/projection only)"},
        {"check": "no_paper_trader_change", "value": True, "detail": "research-only; no api/ touched"},
        {"check": "no_gcp_change", "value": True, "detail": "no deployment surface touched"},
        {"check": "no_deployment", "value": True, "detail": "no model/service deployed"},
        {"check": "no_orders", "value": True, "detail": "orders disabled"},
        {"check": "no_broker_execution", "value": True, "detail": "no broker execution"},
        {"check": "no_automation", "value": True, "detail": "automation off"},
        {"check": "no_binary_artifacts", "value": True, "detail": "text artifacts only"},
        {"check": "preview_only", "value": True, "detail": "manual review"},
        {"check": "recommendation", "value": recommendation, "detail": recommended_next_phase.get("title", "")},
    ]

    # ---- Phase 5-G1E next-step plan ----
    next_plan = {
        "phase": "5-G1E",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "coverage_count_after": coverage_after,
        "target_min_coverage_for_g2": TARGET_MIN_COVERAGE_FOR_G2,
        "batch2_needed": bool(batch2_needed),
        "additional_tickers_needed_for_g2": still_needed,
        "phase5g2_allowed": bool(g2_allowed),
        "recommendation": recommendation,
        "recommended_next_phase": recommended_next_phase,
        "next_live_batch_command_if_needed": (
            "$env:ALPHAVANTAGE_API_KEY = \"<your_key>\"; "
            "python research\\run_phase5g1_earnings_coverage_expansion.py --live "
            "--max-new-tickers 20") if batch2_needed else None,
        "do_not_commit": True,
        "do_not_push": True,
    }

    # ---- write committed-safe artifacts ----
    _dump_json(os.path.join(out_dir, REPORT_NAME), report)
    _write_csv(os.path.join(out_dir, AUDIT_CSV_NAME), AUDIT_COLUMNS, audit_rows)
    _write_csv(os.path.join(out_dir, PARSE_STATUS_CSV_NAME), PARSE_STATUS_COLUMNS, parse_rows)
    _dump_json(os.path.join(out_dir, NEXT_PLAN_NAME), next_plan)

    if verbose:
        print("Phase 5-G1D %s: coverage %d -> %d (raw %d -> %d), integrated %d, failed %d, apply=%s"
              % (recommendation, coverage_before, coverage_after, raw_before, raw_after,
                 len(new_tickers_integrated), len(failed_tickers), apply))
    return report


def _write_csv(path, columns, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        w.writeheader()
        for r in rows or []:
            w.writerow({c: ("" if r.get(c) is None else r.get(c)) for c in columns})


def _dump_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2)


def main():
    import argparse
    ap = argparse.ArgumentParser(description="Phase 5-G1D - post-live cache integration (cache-only)")
    ap.add_argument("--out-dir", default=None)
    ap.add_argument("--audit-only", action="store_true",
                    help="projection/audit only; do NOT rewrite committed-safe Phase 3-M artifacts")
    args = ap.parse_args()
    rep = run(out_dir=args.out_dir, apply=not args.audit_only, verbose=True)
    print(json.dumps({
        "recommendation": rep.get("recommendation"),
        "coverage_count_before": rep.get("coverage_count_before"),
        "coverage_count_after": rep.get("coverage_count_after"),
        "raw_cache_count_before": rep.get("raw_cache_count_before"),
        "raw_cache_count_after": rep.get("raw_cache_count_after"),
        "new_tickers_integrated": rep.get("new_tickers_integrated"),
        "failed_tickers": rep.get("failed_tickers"),
        "network_used": rep.get("network_used"),
        "phase5g2_allowed": rep.get("phase5g2_allowed"),
    }, indent=2))


if __name__ == "__main__":
    main()
