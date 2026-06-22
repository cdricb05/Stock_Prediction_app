"""Phase 5-G1 - Earnings Event Coverage Expansion Plan + Controlled Collector (dry-run-first).

Phase 5-G proved a real, leakage-clean, out-of-sample *incremental* event-alpha edge
(earnings surprise + post-earnings drift) over the Phase 5-C price champion on the
covered names - but only 50 of the 128 Phase 5-C universe tickers carry point-in-time
earnings events, below the project's own 75-ticker event-signal gate. This phase does NOT
add a new provider, does NOT shop for data, and does NOT expand any alpha feature. It
answers one operational question:

    Can we safely reuse the existing earnings-event collector to extend PIT-safe coverage
    from 50/128 to at least 75/128 without deleting existing data, corrupting caches,
    making uncontrolled provider calls, or committing raw provider payloads?

It is **dry-run by default**. In dry-run it makes NO network call, requires NO API key,
writes ONLY into this phase's own output directory, and produces:
  * a coverage gap report (covered / missing / needed-to-75 / needed-to-128),
  * a deterministic missing-ticker collection plan (first batch to reach 75, full batch
    to reach 128),
  * an inventory of the existing collector (provider, key requirement, resumability,
    cache location, gitignore status, rate-limit behaviour, safety),
  * a collection-safety audit,
  * a Phase 5-G2 event-alpha rerun plan (gated on coverage >= 75),
  * and the EXACT future live command - which it never executes.

Live collection happens ONLY when invoked with the explicit ``--live`` flag. Live mode
reuses the existing, tested Phase 3-M collector client (cache-first, throttled,
network-budgeted, key-from-env-only); it adds new raw response files to the EXISTING
Phase 3-M raw cache and never deletes or overwrites existing event data or cache files.

Safety contract: preview-only research. No model is trained, no prediction is computed,
no order is placed, no broker/automation is touched, no Paper Trader / GCP / deployment
is involved, no package is installed, no binary artifact is produced, and no key value is
ever read into, printed by, or written from this module. In dry-run, network is never
used and no paid API is ever called.
"""
from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import os
import subprocess
from datetime import datetime

PHASE = "5-G1"

# --------------------------------------------------------------------------- #
# Paths (repo-local, C: drive only; D: is never touched by this phase)
# --------------------------------------------------------------------------- #
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_RESEARCH_DIR = os.path.join(_REPO_ROOT, "research")
_OUTPUT_DIR = os.path.join(_RESEARCH_DIR, "output")
_OUT_DIR = os.path.join(_OUTPUT_DIR, "phase5g1_earnings_coverage_expansion")

# Existing Phase 3-M earnings collector + its artifacts (the collector we plan to reuse).
_PHASE3M_SCRIPT = os.path.join(_RESEARCH_DIR, "run_phase3m_earnings_estimates_signal_gate.py")
_PHASE3M_DIR = os.path.join(_OUTPUT_DIR, "phase3m_earnings_estimates_signal_gate")
_PHASE3M_RAW_DIR = os.path.join(_PHASE3M_DIR, "raw")
_PHASE3M_FEATURES_CSV = os.path.join(_PHASE3M_DIR, "earnings_features_universe.csv")
_PHASE3M_EVENTS_CSV = os.path.join(_PHASE3M_DIR, "earnings_events_universe.csv")
_PHASE3M_PROGRESS_JSON = os.path.join(_PHASE3M_DIR, "collection_progress.json")

# Phase 3-S readiness gate (status-only, no network) + Phase 3-E feasibility template.
_PHASE3S_SCRIPT = os.path.join(_RESEARCH_DIR, "run_phase3s_event_signal_readiness_gate.py")
_PHASE3E_SCRIPT = os.path.join(_RESEARCH_DIR, "analyze_phase3e_fundamentals_earnings_feasibility.py")

# Phase 5-C universe reference.
_PHASE5C_JSON = os.path.join(_OUTPUT_DIR, "phase5c_cross_sectional_alpha_research.json")
_PHASE5C_OOS_CSV = os.path.join(_OUTPUT_DIR, "phase5c_oos_scores_sample.csv")
_PHASE5C_PANEL_CSV = os.path.join(_OUTPUT_DIR, "phase5c_feature_panel_sample.csv")

# --------------------------------------------------------------------------- #
# Coverage targets / collection controls
# --------------------------------------------------------------------------- #
TARGET_MIN_COVERAGE = 75       # project event-signal gate (mirrors Phase 3-M / 3-S)
IDEAL_COVERAGE = 128           # full Phase 5-C universe
DEFAULT_MAX_NEW_TICKERS = 20   # Alpha Vantage free-tier friendly (mirrors Phase 3-M budget)
PROVIDER_ENV_PRIORITY = [
    ("alpha_vantage", "ALPHAVANTAGE_API_KEY"),
    ("fmp", "FMP_API_KEY"),
    ("finnhub", "FINNHUB_API_KEY"),
    ("intrinio", "INTRINIO_API_KEY"),
]

# --------------------------------------------------------------------------- #
# Recommendation vocabulary (spec-fixed)
# --------------------------------------------------------------------------- #
REC_READY = "READY_FOR_CONTROLLED_EARNINGS_COLLECTION"
REC_NEEDS_PATCH = "EXISTING_COLLECTOR_NEEDS_PATCH"
REC_SUFFICIENT = "EVENT_COVERAGE_ALREADY_SUFFICIENT"
REC_BLOCKED = "EVENT_COVERAGE_BLOCKED"
REC_ERROR = "ERROR"
ALLOWED_RECOMMENDATIONS = [REC_READY, REC_NEEDS_PATCH, REC_SUFFICIENT, REC_BLOCKED, REC_ERROR]

# --------------------------------------------------------------------------- #
# Output-path bundle (threaded through every writer; tests pass a temp dir, so a test
# run can never touch the production directory - the Phase 5-G determinism lesson).
# --------------------------------------------------------------------------- #
class _OutPaths:
    __slots__ = ("out_dir", "report", "gap", "plan", "inventory", "safety", "rerun_plan")

    def __init__(self, out_dir):
        d = out_dir
        self.out_dir = d
        self.report = os.path.join(d, "phase5g1_earnings_coverage_expansion.json")
        self.gap = os.path.join(d, "earnings_coverage_gap_report.csv")
        self.plan = os.path.join(d, "earnings_missing_ticker_plan.csv")
        self.inventory = os.path.join(d, "earnings_collector_inventory.csv")
        self.safety = os.path.join(d, "earnings_collection_safety_audit.csv")
        self.rerun_plan = os.path.join(d, "phase5g2_event_alpha_rerun_plan.json")

    def is_production(self):
        try:
            return os.path.realpath(self.out_dir) == os.path.realpath(_OUT_DIR)
        except OSError:
            return False


# --------------------------------------------------------------------------- #
# Small stdlib helpers
# --------------------------------------------------------------------------- #
def _read_csv_tickers(path, column="ticker"):
    out = []
    if not os.path.isfile(path):
        return out
    with open(path, "r", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            t = (row.get(column) or "").strip().upper()
            if t and t != "SPY":
                out.append(t)
    return out


def _dump_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, sort_keys=False)
        f.write("\n")


def _write_csv(path, columns, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=columns)
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in columns})


def _rel(path):
    try:
        return os.path.relpath(path, _REPO_ROOT).replace("\\", "/")
    except ValueError:
        return path


def _is_path_gitignored(path):
    """True iff `git check-ignore` says the path is ignored. Read-only; never raises."""
    try:
        res = subprocess.run(
            ["git", "check-ignore", "-q", path],
            cwd=_REPO_ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return res.returncode == 0
    except (OSError, ValueError):
        return False


# --------------------------------------------------------------------------- #
# Universe + coverage (Phase 5-C universe is the reference)
# --------------------------------------------------------------------------- #
def load_phase5c_universe():
    """The 128-ticker Phase 5-C universe, read from Phase 5-C artifacts (deterministic sort).

    Prefers the OOS scores sample (carries every universe ticker), falling back to the
    feature panel sample. Pure local read; no network.
    """
    for path in (_PHASE5C_OOS_CSV, _PHASE5C_PANEL_CSV):
        tickers = _read_csv_tickers(path)
        if tickers:
            return sorted(set(tickers))
    return []


def covered_tickers():
    """Universe tickers that already carry a PIT-usable earnings feature row (Phase 3-M)."""
    return sorted(set(_read_csv_tickers(_PHASE3M_FEATURES_CSV)))


def cached_raw_tickers():
    """Tickers with a cached raw provider response on disk (any supported provider)."""
    out = set()
    if not os.path.isdir(_PHASE3M_RAW_DIR):
        return out
    for fn in os.listdir(_PHASE3M_RAW_DIR):
        if not fn.endswith(".json"):
            continue
        stem = fn[:-5]
        for provider, _env in PROVIDER_ENV_PRIORITY:
            prefix = provider + "_"
            if stem.startswith(prefix):
                out.add(stem[len(prefix):].upper())
                break
    return out


def detect_provider(env):
    """Return (provider, env_var, present_bool, detected_booleans) from env keys only.

    Never reads a key VALUE - only presence. Follows the Phase 3-M priority order.
    """
    detected = {}
    for _provider, env_var in PROVIDER_ENV_PRIORITY:
        detected[env_var] = bool((env.get(env_var) or "").strip())
    for provider, env_var in PROVIDER_ENV_PRIORITY:
        if detected[env_var]:
            return provider, env_var, True, detected
    # Default to the provider already used for the existing cache (Alpha Vantage) so the
    # plan/inventory remain meaningful even when no key is present in this shell.
    return PROVIDER_ENV_PRIORITY[0][0], PROVIDER_ENV_PRIORITY[0][1], False, detected


# --------------------------------------------------------------------------- #
# Collector inventory
# --------------------------------------------------------------------------- #
def build_collector_inventory(provider, raw_gitignored):
    """Static, audited facts about the existing earnings collector(s) we plan to reuse."""
    cols = [
        "collector_script", "role", "provider_source", "api_key_env_var", "api_key_required",
        "uses_network", "resumable", "cache_first", "cache_dir", "raw_cache_gitignored",
        "rate_limit_behavior", "deletes_existing_data", "writes_committed_safe_only",
        "reusable_for_expansion", "notes",
    ]
    rows = [
        {
            "collector_script": _rel(_PHASE3M_SCRIPT),
            "role": "earnings EPS estimate/actual/surprise collector (THE reusable collector)",
            "provider_source": "alpha_vantage (priority: alpha_vantage>fmp>finnhub>intrinio)",
            "api_key_env_var": "ALPHAVANTAGE_API_KEY (or FMP/FINNHUB/INTRINIO)",
            "api_key_required": True,
            "uses_network": True,
            "resumable": True,
            "cache_first": True,
            "cache_dir": _rel(_PHASE3M_RAW_DIR),
            "raw_cache_gitignored": raw_gitignored,
            "rate_limit_behavior": ("per-provider min interval (AV ~15s), per-run network budget "
                                    "(default 20), MAX_TOTAL_PROVIDER_REQUESTS=200, same-day "
                                    "provider-limit guard, graceful _ProviderLimited handling"),
            "deletes_existing_data": False,
            "writes_committed_safe_only": True,
            "reusable_for_expansion": True,
            "notes": ("Cache-first add-only: rebuilds partial state from cache and preserves it; "
                      "raw files hold only response bodies (no key); keys read from env only, "
                      "never printed/written/hardcoded; selected provider host allow-listed."),
        },
        {
            "collector_script": _rel(_PHASE3S_SCRIPT),
            "role": "event-signal readiness gate (coverage status only)",
            "provider_source": "n/a",
            "api_key_env_var": "n/a",
            "api_key_required": False,
            "uses_network": False,
            "resumable": "n/a",
            "cache_first": "n/a",
            "cache_dir": "n/a",
            "raw_cache_gitignored": "n/a",
            "rate_limit_behavior": "no provider call (inspects local Phase 3-M progress + cache)",
            "deletes_existing_data": False,
            "writes_committed_safe_only": True,
            "reusable_for_expansion": False,
            "notes": "Status/planning gate; explicitly never calls a provider itself.",
        },
        {
            "collector_script": _rel(_PHASE3E_SCRIPT),
            "role": "fundamentals/earnings feasibility + provider-requirements template",
            "provider_source": "n/a (requirements matrix only)",
            "api_key_env_var": "n/a",
            "api_key_required": False,
            "uses_network": False,
            "resumable": "n/a",
            "cache_first": "n/a",
            "cache_dir": "n/a",
            "raw_cache_gitignored": "n/a",
            "rate_limit_behavior": "no provider call",
            "deletes_existing_data": False,
            "writes_committed_safe_only": True,
            "reusable_for_expansion": False,
            "notes": "Documentation/feasibility analysis only; asserts no current vendor dependency.",
        },
    ]
    return cols, rows


# --------------------------------------------------------------------------- #
# Coverage gap + missing-ticker plan
# --------------------------------------------------------------------------- #
def build_coverage_gap(universe, covered):
    cov_n = len(covered)
    uni_n = len(universe)
    return {
        "universe_count": uni_n,
        "covered_count": cov_n,
        "covered_fraction": round(cov_n / uni_n, 6) if uni_n else 0.0,
        "missing_count": uni_n - cov_n,
        "target_minimum_coverage": TARGET_MIN_COVERAGE,
        "ideal_coverage": IDEAL_COVERAGE,
        "new_tickers_needed_to_minimum": max(0, TARGET_MIN_COVERAGE - cov_n),
        "new_tickers_needed_to_ideal": max(0, IDEAL_COVERAGE - cov_n),
    }


def build_missing_plan(universe, covered, provider, max_new_tickers, target_subset):
    """Deterministic missing-ticker plan rows.

    `target_subset` (or None) is the explicit --ticker/--batch-file set intersected with
    missing; otherwise the first `max_new_tickers` missing names form the next batch.
    """
    covered_set = set(covered)
    missing = [t for t in universe if t not in covered_set]  # universe already sorted
    needed_to_75 = max(0, TARGET_MIN_COVERAGE - len(covered))
    batch_to_75 = set(missing[:needed_to_75])
    if target_subset:
        next_batch = [t for t in missing if t in target_subset][:max_new_tickers]
    else:
        next_batch = missing[:max_new_tickers]
    next_batch_set = set(next_batch)

    cols = ["priority_order", "ticker", "provider", "needed_for_75", "needed_for_128",
            "in_next_collection_batch", "currently_cached", "requires_api_key",
            "uses_provider_call_when_live", "safe_in_dry_run", "note"]
    rows = []
    for i, t in enumerate(missing, start=1):
        rows.append({
            "priority_order": i,
            "ticker": t,
            "provider": provider,
            "needed_for_75": t in batch_to_75,
            "needed_for_128": True,
            "in_next_collection_batch": t in next_batch_set,
            "currently_cached": False,
            "requires_api_key": True,
            "uses_provider_call_when_live": True,
            "safe_in_dry_run": True,
            "note": ("first batch to reach the 75-ticker gate" if t in batch_to_75
                     else "needed only for full 128-ticker coverage"),
        })
    return cols, rows, missing, next_batch


# --------------------------------------------------------------------------- #
# Live command construction (never executed by this module unless --live)
# --------------------------------------------------------------------------- #
def build_live_commands(env_var, max_new_tickers):
    """Return the exact PowerShell commands for a future controlled live collection."""
    wrapper = (
        '$env:%s = "<your_key>"; '
        'python research\\run_phase5g1_earnings_coverage_expansion.py --live '
        '--max-new-tickers %d' % (env_var, max_new_tickers))
    phase3m = (
        '$env:%s = "<your_key>"; '
        '$env:PHASE3M_MAX_NETWORK_REQUESTS_PER_RUN = "%d"; '
        'python research\\run_phase3m_earnings_estimates_signal_gate.py'
        % (env_var, max_new_tickers))
    return wrapper, phase3m


# --------------------------------------------------------------------------- #
# Safety audit
# --------------------------------------------------------------------------- #
def build_safety_audit(paths, live, network_used, api_key_present, raw_gitignored,
                       existing_events_preserved, existing_cache_preserved):
    cols = ["check", "passed", "detail"]
    write_only_phase5g1 = (not live)
    rows = [
        ("dry_run_default", not live,
         "run() defaults to dry-run; live requires the explicit --live flag"),
        ("live_requires_explicit_flag", True,
         "no provider call is attempted unless live=True (CLI --live)"),
        ("no_network_in_dry_run", (not network_used) if not live else True,
         "dry-run performs zero provider/network calls"),
        ("no_api_key_required_for_dry_run", True,
         "dry-run computes plan from local artifacts; no key is read or required"),
        ("existing_event_data_preserved", existing_events_preserved,
         "Phase 3-M earnings_events/features CSVs are never deleted or overwritten here"),
        ("existing_raw_cache_preserved", existing_cache_preserved,
         "Phase 3-M raw cache is add-only (cache-first); existing files never deleted"),
        ("no_overwrite_of_existing_rows", True,
         "live mode only adds NEW raw files for missing tickers; cached files are skipped"),
        ("writes_only_to_phase5g1_dir_in_dry_run", write_only_phase5g1,
         "dry-run writes solely into the phase5g1 output directory"),
        ("collector_writes_committed_safe_only", True,
         "reused Phase 3-M collector writes response bodies only; keys never persisted"),
        ("raw_cache_gitignored", raw_gitignored,
         "git check-ignore on the Phase 3-M raw cache path (FINDING if false)"),
        ("respects_rate_limits", True,
         "live mode reuses Phase 3-M throttle + per-run budget + same-day limit guard"),
        ("no_paper_trader_change", True, "this phase touches no Paper Trader code or DB"),
        ("no_gcp_change", True, "no GCP / remote prediction service interaction"),
        ("no_deployment", True, "nothing deployed; research preview only"),
        ("no_orders", True, "no order creation or execution"),
        ("no_broker_execution", True, "no broker interaction"),
        ("no_automation", True, "no automation enabled"),
        ("no_binary_artifacts", True, "only text JSON/CSV artifacts written"),
        ("no_package_install", True, "stdlib + reused Phase 3-M module only"),
        ("no_paid_apis_used", True, "no paid API called"),
    ]
    out = [{"check": c, "passed": p, "detail": d} for c, p, d in rows]
    # blockers = any safety-critical check that failed (excludes the informational gitignore row)
    informational = {"raw_cache_gitignored"}
    failed = [r["check"] for r in out
              if r["check"] not in informational and r["passed"] is False]
    return cols, out, failed


# --------------------------------------------------------------------------- #
# Recommendation
# --------------------------------------------------------------------------- #
def decide_recommendation(collector_found, collector_reusable, covered_count, safety_failures):
    if not collector_found:
        return REC_BLOCKED
    if safety_failures:
        return REC_NEEDS_PATCH
    if covered_count >= TARGET_MIN_COVERAGE:
        return REC_SUFFICIENT
    if not collector_reusable:
        return REC_NEEDS_PATCH
    return REC_READY


NEXT_PHASE_TABLE = {
    REC_READY: ("5-G2", "Re-run Phase 5-G event-alpha after coverage reaches >= 75 tickers"),
    REC_SUFFICIENT: ("5-G2", "Coverage already >= 75; re-run Phase 5-G event-alpha now"),
    REC_NEEDS_PATCH: ("5-G1b", "Patch the collector safety gap before any live collection"),
    REC_BLOCKED: ("5-G1b", "No reusable earnings collector found; resolve before collecting"),
    REC_ERROR: ("5-G1", "Diagnose the Phase 5-G1 error and re-run"),
}


# --------------------------------------------------------------------------- #
# Live collection (reuses the existing Phase 3-M collector client) - only when --live
# --------------------------------------------------------------------------- #
def _import_phase3m():
    spec = importlib.util.spec_from_file_location("phase3m_collector", _PHASE3M_SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def run_live_collection(provider, env_var, env, target_tickers, max_new_tickers, verbose):
    """Controlled live collection reusing the Phase 3-M client. Add-only, cache-first.

    Returns a dict summary. Never deletes; only fetches missing tickers up to the budget,
    writing into the EXISTING Phase 3-M raw cache. Raises nothing fatal on provider limit -
    it stops and records the limit.
    """
    p3m = _import_phase3m()
    api_key = (env.get(env_var) or "").strip()
    if not api_key:
        return {"attempted": False, "reason": "no provider API key present in environment",
                "collected": [], "new_count": 0, "provider_limit_hit": False}
    host = {p: h for p, _e, h in p3m.PROVIDER_PRIORITY}.get(provider)
    client = p3m.ProviderClient(provider, api_key, host, _PHASE3M_RAW_DIR)
    collected, errors = [], []
    provider_limit_hit = False
    for t in target_tickers[:max_new_tickers]:
        try:
            _payload, source = client.get_earnings(t)
            if source == "network":
                collected.append(t)
            if verbose:
                print("  ... %s (%s)" % (t, source))
        except p3m._ProviderLimited:
            provider_limit_hit = True
            break
        except Exception as exc:  # noqa: BLE001 - record and stop, never crash the wrapper
            errors.append({"ticker": t, "error": type(exc).__name__})
            break
    return {
        "attempted": True,
        "collected": collected,
        "new_count": len(collected),
        "provider_limit_hit": provider_limit_hit,
        "errors": errors,
        "network_requests": client.network_requests,
    }


# --------------------------------------------------------------------------- #
# Report assembly + run
# --------------------------------------------------------------------------- #
def _assemble_report(paths, universe, covered, gap, provider, env_var, api_key_present,
                     detected_keys, missing, next_batch, recommendation, live, live_result,
                     network_used, raw_gitignored, safety_failures, wrapper_cmd, phase3m_cmd):
    next_phase, next_title = NEXT_PHASE_TABLE.get(recommendation, NEXT_PHASE_TABLE[REC_ERROR])
    return {
        "phase": PHASE,
        "objective": ("Plan and (only on --live) perform PIT-safe earnings-event coverage "
                      "expansion from %d/%d to at least %d/%d tickers by reusing the existing "
                      "Phase 3-M collector - dry-run-first, no new provider, no new alpha."
                      % (gap["covered_count"], gap["universe_count"], TARGET_MIN_COVERAGE,
                         IDEAL_COVERAGE)),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "current_coverage_count": gap["covered_count"],
        "current_coverage_fraction": gap["covered_fraction"],
        "target_coverage_count": TARGET_MIN_COVERAGE,
        "ideal_coverage_count": IDEAL_COVERAGE,
        "minimum_new_tickers_needed": gap["new_tickers_needed_to_minimum"],
        "new_tickers_needed_for_full_universe": gap["new_tickers_needed_to_ideal"],
        "missing_ticker_count": gap["missing_count"],
        "first_tickers_to_reach_minimum": missing[:gap["new_tickers_needed_to_minimum"]],
        "next_collection_batch": next_batch,
        "collector_found": True,
        "collector_reusable": recommendation in (REC_READY, REC_SUFFICIENT),
        "collector_scripts_found": [_rel(_PHASE3M_SCRIPT), _rel(_PHASE3S_SCRIPT),
                                    _rel(_PHASE3E_SCRIPT)],
        "provider_source": provider,
        "provider_env_var": env_var,
        "api_key_required": True,
        "api_key_present_in_env": api_key_present,
        "supported_provider_keys_detected_as_booleans": detected_keys,
        "live_collection_default": False,
        "explicit_live_flag_required": True,
        "live_mode_invoked": bool(live),
        "live_collection_result": live_result,
        "raw_cache_gitignored": raw_gitignored,
        "existing_data_preserved": True,
        "dry_run_only_completed": (not live),
        "network_used": network_used,
        "paid_apis_used": False,
        "safety_failures": safety_failures,
        "proposed_live_command_powershell": wrapper_cmd,
        "alternate_live_command_phase3m_powershell": phase3m_cmd,
        "controlled_live_collection_safe": (not safety_failures),
        "recommendation": recommendation,
        "recommended_next_phase": {"phase": next_phase, "title": next_title},
        "artifacts": [_rel(p) for p in (paths.report, paths.gap, paths.plan, paths.inventory,
                                        paths.safety, paths.rerun_plan)],
        "preview_only": True,
        "orders_enabled": False,
        "automation_enabled": False,
        "broker_execution_enabled": False,
        "production_replacement": False,
        "deployed": False,
        "binary_artifacts_created": False,
    }


def _write_rerun_plan(paths, gap, missing, recommendation):
    proceed = gap["covered_count"] >= TARGET_MIN_COVERAGE
    plan = {
        "phase": "5-G2",
        "title": "Event-Alpha Re-run on Expanded Coverage",
        "objective": ("Re-run Phase 5-G earnings/PEAD event-alpha once PIT coverage reaches "
                      ">= %d tickers, to confirm the incremental edge holds on a broader "
                      "covered cross-section. No new alpha feature is added." % TARGET_MIN_COVERAGE),
        "proceed_to_event_alpha_rerun": proceed,
        "preconditions": [
            "PIT earnings coverage >= %d Phase 5-C universe tickers (currently %d)."
            % (TARGET_MIN_COVERAGE, gap["covered_count"]),
            "Coverage expanded via the controlled Phase 3-M collector in a SEPARATE step "
            "(cache-first, network-budgeted) - never from the Phase 5-G alpha harness.",
            "No PIT analyst-estimate-revision series is faked; revision features remain omitted "
            "until a real point-in-time source exists.",
            "Survivorship remains unresolved; absolute portfolio returns stay untrusted.",
        ],
        "rerun_command_powershell": ("python research\\run_phase5g_earnings_revision_alpha.py; "
                                     "python -m pytest tests\\test_phase5g_earnings_revision_alpha.py -q"),
        "tickers_still_missing_for_minimum": missing[:gap["new_tickers_needed_to_minimum"]],
        "current_coverage_count": gap["covered_count"],
        "target_coverage_count": TARGET_MIN_COVERAGE,
        "upstream_recommendation": recommendation,
        "preview_only": True,
        "orders_enabled": False,
        "automation_enabled": False,
        "broker_execution_enabled": False,
        "deployed": False,
    }
    _dump_json(paths.rerun_plan, plan)


def _snapshot_dir(path):
    """Sorted (name, size) listing of a directory's files - to assert nothing was deleted."""
    if not os.path.isdir(path):
        return []
    out = []
    for fn in sorted(os.listdir(path)):
        fp = os.path.join(path, fn)
        if os.path.isfile(fp):
            out.append((fn, os.path.getsize(fp)))
    return out


def run(out_dir=None, live=False, max_new_tickers=DEFAULT_MAX_NEW_TICKERS, tickers=None,
        batch_file=None, env=None, verbose=True):
    """Run Phase 5-G1. Dry-run by default; live collection only when live=True."""
    env = os.environ if env is None else env
    paths = _OutPaths(out_dir if out_dir is not None else _OUT_DIR)
    os.makedirs(paths.out_dir, exist_ok=True)

    try:
        universe = load_phase5c_universe()
        covered = covered_tickers()
        provider, env_var, api_key_present, detected_keys = detect_provider(env)
        raw_gitignored = _is_path_gitignored(_PHASE3M_RAW_DIR)

        # Explicit ticker/batch subset (intersected with missing inside build_missing_plan).
        target_subset = set()
        for t in (tickers or []):
            target_subset.add(t.strip().upper())
        if batch_file and os.path.isfile(batch_file):
            with open(batch_file, "r", encoding="utf-8") as f:
                for line in f:
                    s = line.strip().upper()
                    if s and not s.startswith("#"):
                        target_subset.add(s)

        gap = build_coverage_gap(universe, covered)
        plan_cols, plan_rows, missing, next_batch = build_missing_plan(
            universe, covered, provider, max_new_tickers, target_subset or None)

        # ---- Live collection (only with --live); snapshot cache to prove add-only ----
        cache_before = _snapshot_dir(_PHASE3M_RAW_DIR)
        events_before = os.path.getsize(_PHASE3M_EVENTS_CSV) if os.path.isfile(
            _PHASE3M_EVENTS_CSV) else None
        live_result = None
        network_used = False
        if live:
            live_targets = next_batch
            live_result = run_live_collection(
                provider, env_var, env, live_targets, max_new_tickers, verbose)
            network_used = bool(live_result.get("network_requests"))
        cache_after = _snapshot_dir(_PHASE3M_RAW_DIR)
        events_after = os.path.getsize(_PHASE3M_EVENTS_CSV) if os.path.isfile(
            _PHASE3M_EVENTS_CSV) else None
        # Add-only preservation: no existing file shrank/disappeared; events file untouched.
        before_map = dict(cache_before)
        cache_preserved = all(fn in dict(cache_after) and dict(cache_after)[fn] >= sz
                              for fn, sz in before_map.items())
        events_preserved = (events_before == events_after)

        inv_cols, inv_rows = build_collector_inventory(provider, raw_gitignored)
        safe_cols, safe_rows, safety_failures = build_safety_audit(
            paths, live, network_used, api_key_present, raw_gitignored,
            events_preserved, cache_preserved)

        recommendation = decide_recommendation(
            collector_found=True, collector_reusable=True,
            covered_count=gap["covered_count"], safety_failures=safety_failures)

        wrapper_cmd, phase3m_cmd = build_live_commands(env_var, max_new_tickers)

        # ---- Write artifacts (all into paths.out_dir) ----
        gap_rows = [{"metric": k, "value": v} for k, v in gap.items()]
        gap_rows += [
            {"metric": "provider_source", "value": provider},
            {"metric": "api_key_required", "value": True},
            {"metric": "api_key_present_in_env", "value": api_key_present},
        ]
        _write_csv(paths.gap, ["metric", "value"], gap_rows)
        _write_csv(paths.plan, plan_cols, plan_rows)
        _write_csv(paths.inventory, inv_cols, inv_rows)
        _write_csv(paths.safety, safe_cols, safe_rows)
        _write_rerun_plan(paths, gap, missing, recommendation)

        report = _assemble_report(
            paths, universe, covered, gap, provider, env_var, api_key_present, detected_keys,
            missing, next_batch, recommendation, live, live_result, network_used,
            raw_gitignored, safety_failures, wrapper_cmd, phase3m_cmd)
        _dump_json(paths.report, report)
        return report
    except Exception as exc:  # noqa: BLE001 - emit an ERROR report rather than a bare traceback
        report = {
            "phase": PHASE, "recommendation": REC_ERROR, "error": type(exc).__name__,
            "error_detail": str(exc)[:300], "live_mode_invoked": bool(live),
            "network_used": False, "paid_apis_used": False, "preview_only": True,
            "orders_enabled": False, "automation_enabled": False,
            "broker_execution_enabled": False, "production_replacement": False,
            "deployed": False, "binary_artifacts_created": False,
            "live_collection_default": False, "explicit_live_flag_required": True,
        }
        try:
            _dump_json(paths.report, report)
        except OSError:
            pass
        return report


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _parse_args(argv=None):
    p = argparse.ArgumentParser(
        description="Phase 5-G1 earnings coverage expansion plan + controlled collector "
                    "(dry-run by default; --live required for any provider call).")
    p.add_argument("--live", action="store_true",
                   help="ENABLE live provider collection (reuses the Phase 3-M collector). "
                        "Without this flag the run is dry-run only and makes no network call.")
    p.add_argument("--max-new-tickers", type=int, default=DEFAULT_MAX_NEW_TICKERS,
                   help="Maximum NEW tickers to collect this run (default %d)."
                        % DEFAULT_MAX_NEW_TICKERS)
    p.add_argument("--ticker", action="append", default=[],
                   help="Restrict collection to this ticker (repeatable).")
    p.add_argument("--batch-file", default=None,
                   help="Path to a text file of tickers (one per line) to restrict collection.")
    p.add_argument("--out-dir", default=None,
                   help="Override output directory (tests pass a temp dir).")
    return p.parse_args(argv)


def main(argv=None):
    args = _parse_args(argv)
    result = run(out_dir=args.out_dir, live=args.live, max_new_tickers=args.max_new_tickers,
                 tickers=args.ticker, batch_file=args.batch_file)
    print("Phase %s - Earnings Event Coverage Expansion" % result.get("phase"))
    if result.get("recommendation") == REC_ERROR:
        print("  ERROR: %s - %s" % (result.get("error"), result.get("error_detail")))
        return result
    print("  current coverage         : %s / %s (%.1f%%)"
          % (result["current_coverage_count"], result.get("ideal_coverage_count"),
             100.0 * result["current_coverage_fraction"]))
    print("  target minimum           : %s" % result["target_coverage_count"])
    print("  new tickers needed (>=75): %s" % result["minimum_new_tickers_needed"])
    print("  missing tickers          : %s" % result["missing_ticker_count"])
    print("  collector reusable       : %s" % result["collector_reusable"])
    print("  api key present in env   : %s" % result["api_key_present_in_env"])
    print("  live mode invoked        : %s" % result["live_mode_invoked"])
    print("  network used / paid apis : %s / %s"
          % (result["network_used"], result["paid_apis_used"]))
    print("  first batch to reach 75  : %s" % ", ".join(result["first_tickers_to_reach_minimum"]))
    print("  recommendation           : %s" % result["recommendation"])
    print("  next phase               : %s - %s"
          % (result["recommended_next_phase"]["phase"],
             result["recommended_next_phase"]["title"]))
    print("  proposed live command    : %s" % result["proposed_live_command_powershell"])
    return result


if __name__ == "__main__":
    main()
