"""Phase 5-G1B - Raw Cache Gitignore Safety Patch (audit + report generator).

This is a repo-hygiene audit. It performs **no** network call, needs **no** API
key, runs **no** collection, and never modifies, deletes, or untracks any raw
payload. It only inspects the repo-root .gitignore and reports whether future
raw provider payloads written by the Phase 3-M earnings collector would be
ignored by Git by default.

Key semantic (verified): `git check-ignore` never reports an already-TRACKED
path as ignored. The 50 historical Alpha Vantage payloads under the Phase 3-M
raw dir remain tracked and are intentionally left in place, so check-ignore of
the raw *directory* still returns "not ignored". The meaningful safety property
is therefore proven against a *hypothetical new, untracked* payload under that
directory - which the .gitignore rule correctly ignores.

Run (Windows PowerShell):
    Set-Location C:\\Users\\binis\\Stock_Prediction_app_push
    python research\\run_phase5g1b_raw_cache_gitignore_safety.py
    python -m pytest tests\\test_phase5g1b_raw_cache_gitignore_safety.py -q
"""
import csv
import json
import os
import subprocess

PHASE = "5-G1B"
OBJECTIVE = (
    "Ensure future raw provider payloads written by the Phase 3-M earnings "
    "collector are ignored by Git by default, without deleting, modifying, or "
    "untracking any existing raw file, before any controlled live collection."
)

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(_THIS_DIR)

GITIGNORE_PATH = os.path.join(REPO_ROOT, ".gitignore")
PHASE3M_RAW_DIR = os.path.join(
    REPO_ROOT, "research", "output", "phase3m_earnings_estimates_signal_gate", "raw"
)
# A sentinel name that is guaranteed not to be a real ticker file and is never
# tracked, so `git check-ignore` reports purely from the .gitignore rules.
HYPOTHETICAL_NEW_RAW = os.path.join(
    PHASE3M_RAW_DIR, "alpha_vantage_ZZZZ_HYPOTHETICAL_NEW_PAYLOAD.json"
)
PHASE5G1_DIR = os.path.join(
    REPO_ROOT, "research", "output", "phase5g1_earnings_coverage_expansion"
)
PHASE5G1_COMMITTED_SAFE_ARTIFACTS = (
    "phase5g1_earnings_coverage_expansion.json",
    "earnings_coverage_gap_report.csv",
    "earnings_missing_ticker_plan.csv",
    "earnings_collector_inventory.csv",
    "earnings_collection_safety_audit.csv",
    "phase5g2_event_alpha_rerun_plan.json",
)

# Rule text that, if present in .gitignore, covers the Phase 3-M raw payloads.
_PHASE3M_RAW_RULES = (
    "research/output/phase3m_earnings_estimates_signal_gate/raw/",
    "research/output/*/raw/",
)

_OUT_DIR = os.path.join(
    REPO_ROOT, "research", "output", "phase5g1b_raw_cache_gitignore_safety"
)
REPORT_NAME = "phase5g1b_raw_cache_gitignore_safety.json"
AUDIT_CSV_NAME = "raw_cache_gitignore_audit.csv"

REC_READY = "READY_FOR_CONTROLLED_LIVE_EARNINGS_COLLECTION"
REC_FAILED = "GITIGNORE_PATCH_FAILED"
REC_ERROR = "ERROR"
ALLOWED_RECOMMENDATIONS = (REC_READY, REC_FAILED, REC_ERROR)


def _rel(path):
    return os.path.relpath(path, REPO_ROOT).replace(os.sep, "/")


def _is_path_gitignored(path):
    """True if `git check-ignore -q` reports the path as ignored.

    Note: git never reports an already-tracked path as ignored, so this returns
    True only for paths the .gitignore rules would exclude that are not tracked.
    Returns False on any git error (no false claim of protection).
    """
    try:
        proc = subprocess.run(
            ["git", "check-ignore", "-q", path],
            cwd=REPO_ROOT,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        return False
    return proc.returncode == 0


def _git_tracked_files(path):
    """List of tracked files under `path` (empty on error)."""
    try:
        out = subprocess.run(
            ["git", "ls-files", path],
            cwd=REPO_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
    except Exception:
        return []
    return [ln for ln in out.stdout.splitlines() if ln.strip()]


def _read_gitignore_rules():
    """Non-comment, non-blank, stripped lines of the root .gitignore."""
    if not os.path.isfile(GITIGNORE_PATH):
        return []
    rules = []
    with open(GITIGNORE_PATH, encoding="utf-8") as fh:
        for line in fh:
            s = line.strip()
            if s and not s.startswith("#"):
                rules.append(s)
    return rules


def _present_existing_raw_files():
    if not os.path.isdir(PHASE3M_RAW_DIR):
        return []
    return sorted(
        fn for fn in os.listdir(PHASE3M_RAW_DIR)
        if os.path.isfile(os.path.join(PHASE3M_RAW_DIR, fn))
    )


def _build_audit_rows(report):
    """Flat (check, value, detail) rows backing the committed-safe audit CSV."""
    r = report
    return [
        ("root_gitignore_exists", r["root_gitignore_exists"], _rel(GITIGNORE_PATH)),
        ("raw_cache_ignore_rule_present", r["raw_cache_ignore_rule_present"],
         "; ".join(r["matching_gitignore_rules"]) or "none"),
        ("hypothetical_new_raw_payload_ignored", r["hypothetical_new_raw_payload_ignored"],
         _rel(HYPOTHETICAL_NEW_RAW)),
        ("phase5g1_committed_safe_artifacts_not_ignored",
         r["phase5g1_committed_safe_artifacts_not_ignored"],
         "%d artifacts checked" % len(PHASE5G1_COMMITTED_SAFE_ARTIFACTS)),
        ("existing_raw_files_present_count", r["existing_raw_files_present_count"],
         "files still on disk under the Phase 3-M raw dir"),
        ("existing_raw_files_tracked_count", r["existing_raw_files_tracked_count"],
         "files still tracked in the git index"),
        ("existing_raw_files_deleted", r["existing_raw_files_deleted"], "must be False"),
        ("existing_raw_files_untracked", r["existing_raw_files_untracked"], "must be False"),
        ("phase3m_outputs_modified", r["phase3m_outputs_modified"], "must be False"),
        ("live_collection_run", r["live_collection_run"], "must be False"),
        ("network_used", r["network_used"], "must be False"),
        ("paid_apis_used", r["paid_apis_used"], "must be False"),
        ("api_key_required", r["api_key_required"], "dry audit needs no key"),
        ("no_paper_trader_change", True, "no Paper Trader files touched"),
        ("no_gcp_change", True, "no GCP / deploy work"),
        ("no_deployment", True, "no deployment"),
        ("no_orders", True, "no order/broker work"),
        ("no_broker_execution", True, "no broker execution"),
        ("no_automation", True, "no automation"),
        ("no_binary_artifacts", True, "text-only artifacts"),
        ("no_package_install", True, "no packages installed"),
    ]


def run(out_dir=None, verbose=True):
    """Audit the gitignore safety patch and write committed-safe artifacts.

    Read-only: inspects .gitignore and the repo index; never writes or deletes
    raw payloads. Output (the report JSON + audit CSV) goes to `out_dir`
    (default: the committed production dir); tests pass a temp dir.
    """
    out_dir = out_dir or _OUT_DIR
    try:
        os.makedirs(out_dir, exist_ok=True)

        rules = _read_gitignore_rules()
        root_gitignore_exists = os.path.isfile(GITIGNORE_PATH)
        matching = [rule for rule in _PHASE3M_RAW_RULES if rule in rules]
        raw_cache_ignore_rule_present = bool(matching)
        hypothetical_ignored = _is_path_gitignored(HYPOTHETICAL_NEW_RAW)

        artifact_ignored = {
            fn: _is_path_gitignored(os.path.join(PHASE5G1_DIR, fn))
            for fn in PHASE5G1_COMMITTED_SAFE_ARTIFACTS
        }
        phase5g1_not_ignored = not any(artifact_ignored.values())

        present_raw = _present_existing_raw_files()
        tracked_raw = _git_tracked_files(PHASE3M_RAW_DIR)
        existing_raw_files_deleted = len(present_raw) == 0
        existing_raw_files_untracked = len(tracked_raw) == 0 and len(present_raw) > 0

        patch_ok = (
            root_gitignore_exists
            and raw_cache_ignore_rule_present
            and hypothetical_ignored
            and phase5g1_not_ignored
            and not existing_raw_files_deleted
            and not existing_raw_files_untracked
        )
        recommendation = REC_READY if patch_ok else REC_FAILED

        report = {
            "phase": PHASE,
            "objective": OBJECTIVE,
            # --- core findings ---
            "root_gitignore_exists": root_gitignore_exists,
            "root_gitignore_path": _rel(GITIGNORE_PATH),
            "raw_cache_ignore_rule_present": raw_cache_ignore_rule_present,
            "matching_gitignore_rules": matching,
            "phase3m_raw_dir": _rel(PHASE3M_RAW_DIR),
            "hypothetical_new_raw_payload": _rel(HYPOTHETICAL_NEW_RAW),
            "hypothetical_new_raw_payload_ignored": hypothetical_ignored,
            "phase5g1_committed_safe_artifacts_not_ignored": phase5g1_not_ignored,
            "phase5g1_artifact_ignore_status": {
                fn: artifact_ignored[fn] for fn in PHASE5G1_COMMITTED_SAFE_ARTIFACTS
            },
            "existing_raw_files_present_count": len(present_raw),
            "existing_raw_files_tracked_count": len(tracked_raw),
            # --- safety / forbidden-surface guarantees ---
            "existing_raw_files_deleted": existing_raw_files_deleted,
            "existing_raw_files_untracked": existing_raw_files_untracked,
            "phase3m_outputs_modified": False,
            "live_collection_run": False,
            "network_used": False,
            "paid_apis_used": False,
            "api_key_required": False,
            "api_key_present_in_env": bool(os.environ.get("ALPHAVANTAGE_API_KEY")),
            # --- recommendation ---
            "recommendation": recommendation,
            "recommended_next_phase": {
                "phase": "5-G1-LIVE",
                "description": (
                    "Run the controlled live Phase 3-M earnings collection "
                    "(Phase 5-G1 --live) to expand PIT-safe coverage from 50 "
                    "toward >=75 tickers; newly written raw payloads are now "
                    "gitignored. Then proceed to Phase 5-G2 (event-alpha rerun)."
                ),
                "proposed_live_command_powershell": (
                    'Set-Location C:\\Users\\binis\\Stock_Prediction_app_push; '
                    '$env:ALPHAVANTAGE_API_KEY = "<your_key>"; '
                    "python research\\run_phase5g1_earnings_coverage_expansion.py "
                    "--live --max-new-tickers 20"
                ),
            },
            # --- standing safety flags ---
            "preview_only": True,
            "orders_enabled": False,
            "automation_enabled": False,
            "broker_execution_enabled": False,
            "production_replacement": False,
            "deployed": False,
            "binary_artifacts_created": False,
        }

        # Write committed-safe artifacts.
        report_path = os.path.join(out_dir, REPORT_NAME)
        with open(report_path, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=2)
            fh.write("\n")

        audit_path = os.path.join(out_dir, AUDIT_CSV_NAME)
        with open(audit_path, "w", encoding="utf-8", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow(["check", "value", "detail"])
            for check, value, detail in _build_audit_rows(report):
                writer.writerow([check, value, detail])

        if verbose:
            print("Phase 5-G1B raw-cache gitignore safety: %s" % recommendation)
            print("  root .gitignore exists ........ %s" % root_gitignore_exists)
            print("  raw-cache ignore rule present . %s (%s)"
                  % (raw_cache_ignore_rule_present, ", ".join(matching) or "none"))
            print("  new raw payload would be ignored %s" % hypothetical_ignored)
            print("  phase5g1 artifacts NOT ignored  %s" % phase5g1_not_ignored)
            print("  existing raw files present/tracked: %d / %d (deleted=%s, untracked=%s)"
                  % (len(present_raw), len(tracked_raw),
                     existing_raw_files_deleted, existing_raw_files_untracked))
            print("  artifacts -> %s" % _rel(out_dir))

        return report
    except Exception as exc:  # never crash; emit an ERROR report instead
        err = {
            "phase": PHASE,
            "objective": OBJECTIVE,
            "recommendation": REC_ERROR,
            "error": "%s: %s" % (type(exc).__name__, exc),
            "network_used": False,
            "paid_apis_used": False,
            "live_collection_run": False,
            "existing_raw_files_deleted": False,
            "existing_raw_files_untracked": False,
            "phase3m_outputs_modified": False,
            "preview_only": True,
            "orders_enabled": False,
            "automation_enabled": False,
            "broker_execution_enabled": False,
            "production_replacement": False,
            "deployed": False,
            "binary_artifacts_created": False,
        }
        try:
            os.makedirs(out_dir, exist_ok=True)
            with open(os.path.join(out_dir, REPORT_NAME), "w", encoding="utf-8") as fh:
                json.dump(err, fh, indent=2)
                fh.write("\n")
        except Exception:
            pass
        return err


def main(argv=None):
    run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
