"""Phase 2E-E — tests for the flag-OFF deploy runbook + read-only smoke script.

These tests prove the *planning artifacts* are safe and honor every guardrail:
the runbook states no deployment happened and that ``PREDICTOR_USE_MODEL_V2``
must remain off; the smoke script is stdlib-only, requires ``--base-url``, is
read-only, never enables the flag, and validates the legacy keys and the
flag-off ``model_v2`` contract; the plan summary records the three "executed"
flags as false; and nothing pulls in Paper Trader or broker/order/automation
logic.

The smoke module is imported (it is stdlib-only and runs nothing at import
time), but **no HTTP request is ever made** — the tests stub the transport or
call pure helpers. They do not deploy, connect to a DB, run a migration, or set
any environment variable.

Runs two ways:
  * under pytest:   pytest tests/test_phase2_flagoff_deploy.py
  * without pytest: python tests/test_phase2_flagoff_deploy.py
The __main__ block discovers every ``test_*`` function, runs it, prints
PASS/FAIL, and exits non-zero on any failure (the GCP venv has no pytest).
"""
from __future__ import annotations

import ast
import importlib.util
import json
import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

_FLAG = "PREDICTOR_USE_MODEL_V2"

_RUNBOOK = os.path.join(_REPO_ROOT, "docs",
                        "phase2e_e_flagoff_deploy_runbook_v1.md")
_SMOKE = os.path.join(_REPO_ROOT, "scripts",
                      "phase2e_e_postdeploy_smoke.py")
_PLAN = os.path.join(_REPO_ROOT, "research", "output",
                     "phase2e_e_flagoff_deploy_plan_summary.json")

_LEGACY_KEYS = ("recommendation", "confidence", "agreement",
                "ensemble_day5", "predictions", "zscore")

# Module roots that count as Python standard library for this script.
_STDLIB_ROOTS = {
    "__future__", "argparse", "json", "sys", "os", "urllib",
}


def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _import_smoke():
    """Import the smoke script as a module without running main()."""
    spec = importlib.util.spec_from_file_location("phase2e_e_smoke", _SMOKE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# --------------------------------------------------------------------------- #
# 1. Runbook exists and makes the required statements
# --------------------------------------------------------------------------- #
def test_runbook_exists():
    assert os.path.exists(_RUNBOOK), "runbook doc missing"
    assert _read(_RUNBOOK).strip(), "runbook doc is empty"


def test_runbook_says_no_deployment_happened():
    text = _read(_RUNBOOK).lower()
    assert "no deployment happened" in text, "runbook must state no deployment happened"
    assert "this phase does not deploy" in text


def test_runbook_says_flag_must_remain_off():
    text = _read(_RUNBOOK)
    assert "PREDICTOR_USE_MODEL_V2 must remain off" in text, \
        "runbook must state the flag must remain off"


# --------------------------------------------------------------------------- #
# 2. Smoke script: required --base-url, refuses without it
# --------------------------------------------------------------------------- #
def test_smoke_requires_base_url():
    mod = _import_smoke()
    # argparse exits (SystemExit) when a required arg is missing.
    try:
        mod._parse_args([])
    except SystemExit as e:
        assert e.code != 0
        return
    raise AssertionError("smoke script must require --base-url")


def test_smoke_accepts_base_url():
    mod = _import_smoke()
    args = mod._parse_args(["--base-url", "http://127.0.0.1:9000"])
    assert args.base_url == "http://127.0.0.1:9000"
    assert args.ticker == "AAPL"          # default ticker
    assert args.summary_output is None    # no write unless asked


# --------------------------------------------------------------------------- #
# 3. Smoke script uses only stdlib imports
# --------------------------------------------------------------------------- #
def test_smoke_uses_only_stdlib_imports():
    tree = ast.parse(_read(_SMOKE))
    roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for n in node.names:
                roots.add(n.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                roots.add(node.module.split(".")[0])
    extra = roots - _STDLIB_ROOTS
    assert not extra, f"smoke script imports non-stdlib modules: {sorted(extra)}"


# --------------------------------------------------------------------------- #
# 4. Smoke script contains no gcloud / SSH execution
# --------------------------------------------------------------------------- #
def test_smoke_no_gcloud_or_ssh():
    # Prove no gcloud/SSH *execution* is possible: the script contains none of
    # the primitives that could shell out (the words "gcloud"/"SSH" appear only
    # in the docstring/notes as negative statements, so a bare substring scan
    # would be a false positive). We scan for execution mechanisms instead.
    text = _read(_SMOKE).lower()
    for tok in ("paramiko", "subprocess", "os.system", "popen", "pexpect",
                "os.exec", "shell=true"):
        assert tok not in text, f"smoke script can shell out via {tok!r}"
    # And confirm no call to an SSH/gcloud command string is constructed.
    for tok in ('"gcloud', "'gcloud", '"ssh', "'ssh"):
        assert tok not in text, f"smoke script builds a command string: {tok!r}"


# --------------------------------------------------------------------------- #
# 5. Smoke script contains no DB-write / migration execution tokens
# --------------------------------------------------------------------------- #
def test_smoke_no_db_or_migration_tokens():
    text = _read(_SMOKE).lower()
    for tok in ("create_engine", "sqlalchemy", "psycopg", "insert ", "update ",
                "delete ", "to_sql", "alembic", "op.create_table",
                "create table ", "alter table ", "drop table ", ".commit("):
        assert tok not in text, f"smoke script contains DB/migration token: {tok!r}"


# --------------------------------------------------------------------------- #
# 6. Smoke script validates the legacy keys
# --------------------------------------------------------------------------- #
def test_smoke_validates_legacy_keys():
    mod = _import_smoke()
    # The constant lists exactly the legacy keys.
    assert tuple(mod.LEGACY_KEYS) == _LEGACY_KEYS
    full = {k: 1 for k in _LEGACY_KEYS}
    assert mod._legacy_keys_present(full) is True
    missing = dict(full)
    missing.pop("zscore")
    assert mod._legacy_keys_present(missing) is False
    assert mod._legacy_keys_present("not a dict") is False


# --------------------------------------------------------------------------- #
# 7. Smoke script validates model_v2 absent/false (flag-off contract)
# --------------------------------------------------------------------------- #
def test_smoke_validates_model_v2_inactive():
    mod = _import_smoke()
    assert mod._model_v2_inactive({}) is True               # absent => inactive
    assert mod._model_v2_inactive({"model_v2": False}) is True
    assert mod._model_v2_inactive({"model_v2": None}) is True
    assert mod._model_v2_inactive({"model_v2": True}) is False  # active => fail


# --------------------------------------------------------------------------- #
# 8. Smoke script never enables PREDICTOR_USE_MODEL_V2 (or any env var)
# --------------------------------------------------------------------------- #
def test_smoke_does_not_enable_flag():
    text = _read(_SMOKE)
    # No environment writes of any kind.
    assert "os.environ[" not in text, "smoke script writes an environment variable"
    assert "os.putenv" not in text
    assert "setenv" not in text.lower()
    # The flag name appears only as a documented constant / label, never assigned
    # a truthy value into the environment.
    tree = ast.parse(text)
    for node in ast.walk(tree):
        # Catch any `os.environ[...] = ...` or `os.environ.setdefault(...)`.
        if isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Store):
            val = getattr(node.value, "attr", None)
            assert val != "environ", "smoke script assigns into os.environ"


# --------------------------------------------------------------------------- #
# 9. Plan summary exists and records the executed flags as false
# --------------------------------------------------------------------------- #
def test_plan_summary_exists_and_safe():
    assert os.path.exists(_PLAN), "plan summary JSON missing"
    data = json.loads(_read(_PLAN))
    assert data["phase"] == "2E-E"
    assert data["deployment_executed"] is False
    assert data["migration_executed"] is False
    assert data["database_touched"] is False
    assert data["model_v2_enabled"] is False
    assert data["feature_flag"] == _FLAG
    assert data["smoke_script"] == "scripts/phase2e_e_postdeploy_smoke.py"
    assert data["runbook"] == "docs/phase2e_e_flagoff_deploy_runbook_v1.md"
    assert data["safe_to_prepare_flagoff_deploy"] is True


# --------------------------------------------------------------------------- #
# 10. No Paper Trader import in the smoke script
# --------------------------------------------------------------------------- #
def test_no_paper_trader_import():
    assert "paper_trader" not in _read(_SMOKE).lower()
    offenders = [m for m in sys.modules
                 if m == "paper_trader" or m.startswith("paper_trader.")]
    assert not offenders, f"unexpected Paper Trader import: {offenders}"


# --------------------------------------------------------------------------- #
# 11. No broker / order / automation logic in the smoke script
# --------------------------------------------------------------------------- #
def test_no_broker_order_automation():
    text = _read(_SMOKE).lower()
    for tok in ("broker", "place_order", "submit_order", "buy_order",
                "sell_order", "alpaca", "execute_trade", "schedule", "cron"):
        assert tok not in text, f"smoke script contains automation token: {tok!r}"


# --------------------------------------------------------------------------- #
# 12. Smoke transport is read-only (GET, plus a single read-POST) — no mutation
# --------------------------------------------------------------------------- #
def test_smoke_transport_is_read_only():
    mod = _import_smoke()
    seen = {"methods": [], "urls": []}

    def _fake_get(url):
        seen["methods"].append("GET")
        seen["urls"].append(url)
        if "/predict/" in url:
            body = {k: 1 for k in _LEGACY_KEYS}
            body["model_v2"] = False
            return 200, body
        return 200, {"ok": True}

    def _fake_post(url, payload):
        seen["methods"].append("POST")
        seen["urls"].append(url)
        assert set(payload.keys()) == {"ticker"}, "POST body must only name a ticker"
        body = {k: 1 for k in _LEGACY_KEYS}
        body["model_v2"] = False
        return 200, body

    orig_get, orig_post = mod._http_get, mod._http_post_json
    mod._http_get, mod._http_post_json = _fake_get, _fake_post
    try:
        summary = mod.run_smoke("http://127.0.0.1:9000", "AAPL")
    finally:
        mod._http_get, mod._http_post_json = orig_get, orig_post

    # Only GET + a single read-POST to the prediction route were used.
    assert "POST" in seen["methods"]
    assert seen["methods"].count("POST") == 1
    assert all(u.endswith("/predict_all_models/")
               for u, m in zip(seen["urls"], seen["methods"]) if m == "POST")
    # The flag-off contract is satisfied on the stubbed responses.
    assert summary["legacy_keys_present"] is True
    assert summary["model_v2_inactive"] is True
    assert summary["database_touched"] is False
    assert summary["migration_executed"] is False
    assert summary["deployment_executed"] is False
    assert summary["safe_flagoff_smoke"] is True


def test_smoke_flags_active_model_v2():
    # If the deployed service reports model_v2 active while the flag should be
    # off, the smoke must fail (model_v2_inactive False => not safe).
    mod = _import_smoke()

    def _fake_get(url):
        body = {k: 1 for k in _LEGACY_KEYS}
        body["model_v2"] = True
        return (200, body) if "/predict/" in url else (200, {"ok": True})

    def _fake_post(url, payload):
        body = {k: 1 for k in _LEGACY_KEYS}
        body["model_v2"] = True
        return 200, body

    orig_get, orig_post = mod._http_get, mod._http_post_json
    mod._http_get, mod._http_post_json = _fake_get, _fake_post
    try:
        summary = mod.run_smoke("http://127.0.0.1:9000", "AAPL")
    finally:
        mod._http_get, mod._http_post_json = orig_get, orig_post

    assert summary["model_v2_inactive"] is False
    assert summary["safe_flagoff_smoke"] is False


# --------------------------------------------------------------------------- #
# Self-running harness (no pytest required)
# --------------------------------------------------------------------------- #
def _run_all():
    tests = sorted((n, f) for n, f in globals().items()
                   if n.startswith("test_") and callable(f))
    passed = failed = 0
    failures = []
    for name, fn in tests:
        try:
            fn()
            print(f"PASS {name}")
            passed += 1
        except Exception as e:  # noqa: BLE001
            import traceback
            print(f"FAIL {name}: {e}")
            failures.append((name, traceback.format_exc()))
            failed += 1
    print(f"\n{passed} passed, {failed} failed, {len(tests)} total")
    if failures:
        print("\n--- failure details ---")
        for name, tb in failures:
            print(f"\n### {name}\n{tb}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(_run_all())
