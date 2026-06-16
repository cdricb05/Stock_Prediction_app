"""Phase 2E-D — offline pre-deployment readiness check.

Runs the important *local* safety checks for the default-off model-v2 API wiring
and writes a machine-readable summary to::

    research/output/phase2e_d_predeploy_summary.json

It is strictly offline and read-only: NO network, NO gcloud/SSH, NO database
connection, NO migration, NO deployment. ``latest_git_commit`` is read from the
local git metadata only. Nothing here enables ``PREDICTOR_USE_MODEL_V2`` or
mutates any file other than the summary JSON it writes.

Run:  python scripts/phase2e_d_predeploy_check.py
Exit code is 0 when ``safe_to_deploy_with_flag_off`` is true, else 1.
"""
from __future__ import annotations

import json
import os
import py_compile
import sys
import tempfile

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

_API_SERVER = os.path.join(_REPO_ROOT, "api_server.py")
_SUMMARY_PATH = os.path.join(_REPO_ROOT, "research", "output",
                             "phase2e_d_predeploy_summary.json")

_FLAG = "PREDICTOR_USE_MODEL_V2"
_BEGIN = "# >>> MODELV2_GATE_BEGIN"
_END = "# <<< MODELV2_GATE_END"

_ROUTES = (
    '@app.get("/predict/{ticker}")',
    '@app.post("/predict_all_models/"',
    '@app.get("/healthz")',
    '@app.get("/config")',
    '@app.get("/ping")',
)
_LEGACY_KEYS = ("recommendation", "confidence", "agreement",
                "ensemble_day5", "predictions", "zscore")


def _read_api_server() -> str:
    with open(_API_SERVER, "r", encoding="utf-8") as f:
        return f.read()


def _extract_gated_block(src: str) -> str:
    after = src.split(_BEGIN, 1)[1].split(_END, 1)[0]
    return after.split("\n", 1)[1]


def _latest_git_commit() -> str:
    """Read HEAD locally (no network). Falls back to parsing .git/ refs."""
    git_dir = os.path.join(_REPO_ROOT, ".git")
    try:
        head_path = os.path.join(git_dir, "HEAD")
        with open(head_path, "r", encoding="utf-8") as f:
            head = f.read().strip()
        if head.startswith("ref:"):
            ref = head.split(" ", 1)[1].strip()
            ref_path = os.path.join(git_dir, *ref.split("/"))
            if os.path.exists(ref_path):
                with open(ref_path, "r", encoding="utf-8") as f:
                    return f.read().strip()
            # packed refs fallback
            packed = os.path.join(git_dir, "packed-refs")
            if os.path.exists(packed):
                with open(packed, "r", encoding="utf-8") as f:
                    for line in f:
                        if line.strip().endswith(ref):
                            return line.split(" ", 1)[0].strip()
        else:
            return head  # detached HEAD: already a sha
    except Exception:
        pass
    return "unknown"


def _api_server_compiles() -> bool:
    with tempfile.NamedTemporaryFile(suffix=".pyc", delete=False) as tmp:
        cfile = tmp.name
    try:
        py_compile.compile(_API_SERVER, cfile=cfile, doraise=True)
        return True
    except py_compile.PyCompileError:
        return False
    finally:
        if os.path.exists(cfile):
            os.remove(cfile)


def _flag_default_off(src: str) -> bool:
    """Exec the real gated block in isolation with the flag unset; expect off."""
    os.environ.pop(_FLAG, None)
    ns = {"os": os, "logger": _SilentLogger()}
    try:
        exec(compile(_extract_gated_block(src), _API_SERVER, "exec"), ns)
        return ns["model_v2_is_enabled"]() is False
    except Exception:
        return False


class _SilentLogger:
    def info(self, *a, **k):
        pass

    def warning(self, *a, **k):
        pass

    def error(self, *a, **k):
        pass


def _routes_present(src: str) -> bool:
    return all(r in src for r in _ROUTES)


def _legacy_keys_present(src: str) -> bool:
    return all(f'"{k}"' in src for k in _LEGACY_KEYS)


def _model_v2_gated(src: str) -> bool:
    """True iff serve_v2 is imported once behind a guard and the flag is read once."""
    if src.count("from model import serve_v2") != 1:
        return False
    if src.count(f'os.getenv("{_FLAG}")') != 1:
        return False
    block = _extract_gated_block(src)
    guarded = ("try:" in block.split("from model import serve_v2", 1)[0]
               and "_serve_v2 = None" in block)
    no_db = not any(tok in block.lower() for tok in
                    ("create_engine", "sqlalchemy", "insert", "update ",
                     "delete", "commit", "to_sql"))
    return guarded and no_db


def main() -> int:
    src = _read_api_server()

    api_server_compiles = _api_server_compiles()
    flag_default_off = _flag_default_off(src)
    route_names_present = _routes_present(src)
    legacy_keys_present = _legacy_keys_present(src)
    model_v2_gated = _model_v2_gated(src)

    # This script performs no DB/migration/deploy actions, by construction.
    database_touched = False
    migration_executed = False
    deployment_executed = False

    safe_to_deploy_with_flag_off = all([
        api_server_compiles,
        flag_default_off,
        route_names_present,
        legacy_keys_present,
        model_v2_gated,
        not database_touched,
        not migration_executed,
        not deployment_executed,
    ])

    summary = {
        "phase": "2E-D",
        "latest_git_commit": _latest_git_commit(),
        "api_server_compiles": api_server_compiles,
        "flag_default_off": flag_default_off,
        "route_names_present": route_names_present,
        "legacy_keys_present": legacy_keys_present,
        "model_v2_gated": model_v2_gated,
        "database_touched": database_touched,
        "migration_executed": migration_executed,
        "deployment_executed": deployment_executed,
        "safe_to_deploy_with_flag_off": safe_to_deploy_with_flag_off,
        "feature_flag": _FLAG,
        "feature_flag_default": "off",
        "notes": (
            "Offline readiness check only. No network, gcloud, SSH, DB "
            "connection, migration, or deployment was performed. Do not enable "
            "PREDICTOR_USE_MODEL_V2 yet."
        ),
    }

    os.makedirs(os.path.dirname(_SUMMARY_PATH), exist_ok=True)
    with open(_SUMMARY_PATH, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)
        f.write("\n")

    print(json.dumps(summary, indent=2))
    print(f"\nwrote {os.path.relpath(_SUMMARY_PATH, _REPO_ROOT)}")
    return 0 if safe_to_deploy_with_flag_off else 1


if __name__ == "__main__":
    raise SystemExit(main())
