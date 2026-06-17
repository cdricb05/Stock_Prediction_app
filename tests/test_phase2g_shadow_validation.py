"""Phase 2G-A — tests for the model-v2 shadow validation harness.

These tests prove the harness is safe and honors every guardrail: it compiles,
runs end-to-end on a deterministic fixture CSV, emits every required field, and
the output marks fixture data as ``fixture_only`` with no production-edge claim
and ``safe_for_canary`` false. They also prove the script never deploys, never
connects to / writes a database, never runs a migration, never enables
``PREDICTOR_USE_MODEL_V2``, and contains no broker / order / automation logic and
no api_server.py modification.

The harness is offline and file-based; these tests run it on a temporary fixture
CSV in a temp directory and never touch a network, a database, or the live
service.

Runs two ways:
  * under pytest:   pytest tests/test_phase2g_shadow_validation.py
  * without pytest: python tests/test_phase2g_shadow_validation.py
The __main__ block discovers every ``test_*`` function, runs it, prints
PASS/FAIL, and exits non-zero on any failure (the GCP venv has no pytest).
"""
from __future__ import annotations

import ast
import importlib.util
import json
import os
import sys
import tempfile

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

_FLAG = "PREDICTOR_USE_MODEL_V2"
_HARNESS = os.path.join(_REPO_ROOT, "research", "run_phase2g_shadow_validation.py")
_DOC = os.path.join(_REPO_ROOT, "docs", "phase2g_shadow_validation_harness_v1.md")

# Every field the deliverable requires in the summary JSON.
_REQUIRED_FIELDS = (
    "phase", "input_mode", "database_touched", "migration_executed",
    "deployment_executed", "model_v2_enabled", "production_edge_claimed",
    "fixture_only", "no_trading", "no_orders", "no_automation",
    "row_count", "ticker_count", "date_start", "date_end", "horizon_days",
    "rank_ic", "top_decile_mean_excess_return", "universe_mean_excess_return",
    "top_decile_hit_rate", "bucket_monotonic", "legacy_comparison_available",
    "safe_for_canary",
)


def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _import_harness():
    """Import the harness module without running main()."""
    spec = importlib.util.spec_from_file_location("phase2g_harness", _HARNESS)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _run_on_fixture(tmpdir: str):
    """Generate a fixture CSV in tmpdir, run the harness, return (mod, summary)."""
    mod = _import_harness()
    csv_path = os.path.join(tmpdir, "fixture.csv")
    out_path = os.path.join(tmpdir, "out.json")
    mod.build_fixture_frame().to_csv(csv_path, index=False)
    summary = mod.run_validation(csv_path, out_path, fixture_only=True)
    return mod, summary, out_path


# --------------------------------------------------------------------------- #
# 1. Script compiles
# --------------------------------------------------------------------------- #
def test_harness_compiles():
    compile(_read(_HARNESS), _HARNESS, "exec")


# --------------------------------------------------------------------------- #
# 2. Script runs on a deterministic fixture CSV and writes JSON
# --------------------------------------------------------------------------- #
def test_runs_on_fixture_and_writes_json():
    with tempfile.TemporaryDirectory() as tmp:
        _mod, summary, out_path = _run_on_fixture(tmp)
        assert os.path.exists(out_path), "summary JSON not written"
        on_disk = json.loads(_read(out_path))
        assert on_disk["phase"] == "2G-A"
        assert summary["row_count"] > 0, "fixture produced no labeled rows"
        assert summary["ticker_count"] > 0


# --------------------------------------------------------------------------- #
# 3. Output JSON contains all required fields
# --------------------------------------------------------------------------- #
def test_output_has_all_required_fields():
    with tempfile.TemporaryDirectory() as tmp:
        _mod, summary, _out = _run_on_fixture(tmp)
        for field in _REQUIRED_FIELDS:
            assert field in summary, f"summary missing required field: {field}"
        assert summary["phase"] == "2G-A"
        assert summary["input_mode"] == "csv"
        assert summary["horizon_days"] == _mod.DEFAULT_HORIZON


# --------------------------------------------------------------------------- #
# 4. Fixture output is honest: fixture_only / no edge / not canary-safe
# --------------------------------------------------------------------------- #
def test_fixture_flags_are_safe():
    with tempfile.TemporaryDirectory() as tmp:
        _mod, summary, _out = _run_on_fixture(tmp)
        assert summary["fixture_only"] is True
        assert summary["production_edge_claimed"] is False
        assert summary["safe_for_canary"] is False
        assert summary["database_touched"] is False
        assert summary["migration_executed"] is False
        assert summary["deployment_executed"] is False
        assert summary["model_v2_enabled"] is False
        assert summary["no_trading"] is True
        assert summary["no_orders"] is True
        assert summary["no_automation"] is True


# --------------------------------------------------------------------------- #
# 5. Fixture output explicitly disclaims production evidence
# --------------------------------------------------------------------------- #
def test_fixture_output_disclaims_edge():
    with tempfile.TemporaryDirectory() as tmp:
        _mod, summary, _out = _run_on_fixture(tmp)
        blob = json.dumps(summary).lower()
        assert "not production evidence" in blob
        assert "not real market data" in blob or "not a real market-edge" in blob


# --------------------------------------------------------------------------- #
# 6. safe_for_canary stays false even if metrics look good, while fixture_only
# --------------------------------------------------------------------------- #
def test_canary_requires_real_data_and_legacy():
    mod = _import_harness()
    strong = {"rank_ic": 0.20, "top_decile_mean_excess_return": 0.05,
              "universe_mean_excess_return": 0.0, "bucket_monotonic": True}
    # Fixture data: never canary-safe, even with strong metrics.
    assert mod._canary_ready(strong, fixture_only=True, legacy_available=True) is False
    # Real data but no legacy comparison: still not canary-safe.
    assert mod._canary_ready(strong, fixture_only=False, legacy_available=False) is False
    # Real data + legacy + strong metrics: canary-*candidate* (gate passes).
    assert mod._canary_ready(strong, fixture_only=False, legacy_available=True) is True
    # Real data + legacy but weak IC: not canary-safe.
    weak = dict(strong, rank_ic=0.0)
    assert mod._canary_ready(weak, fixture_only=False, legacy_available=True) is False


# --------------------------------------------------------------------------- #
# 7. No api_server.py modification anywhere in the harness
# --------------------------------------------------------------------------- #
def test_no_api_server_modification():
    # api_server appears only as a negative statement in the docstring; what
    # matters is that the harness neither IMPORTS nor WRITES it. Prove both: no
    # import of api_server, and no file open of api_server.py.
    text = _read(_HARNESS)
    assert "import api_server" not in text, "harness imports api_server"
    assert "from api_server" not in text, "harness imports from api_server"
    tree = ast.parse(text)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            mods = ([n.name for n in node.names]
                    + ([node.module] if isinstance(node, ast.ImportFrom)
                       and node.module else []))
            assert not any((m or "").split(".")[0] == "api_server" for m in mods)
    assert "api_server.py" not in text.replace("``api_server.py``", ""), \
        "harness opens/writes api_server.py"


# --------------------------------------------------------------------------- #
# 8. No gcloud / SSH / subprocess deployment logic
# --------------------------------------------------------------------------- #
def test_no_deploy_or_shellout_logic():
    text = _read(_HARNESS).lower()
    for tok in ("subprocess", "os.system", "popen", "pexpect", "paramiko",
                "os.exec", "shell=true", '"gcloud', "'gcloud", '"ssh ',
                "'ssh ", "systemctl", "uvicorn"):
        assert tok not in text, f"harness contains deploy/shellout token: {tok!r}"


# --------------------------------------------------------------------------- #
# 9. No broker / order / automation tokens
# --------------------------------------------------------------------------- #
def test_no_broker_order_automation():
    # Scan for tokens that would indicate actual order/automation EXECUTION. The
    # bare word "automation" is excluded because the required safety flag
    # `no_automation` legitimately contains it.
    text = _read(_HARNESS).lower()
    for tok in ("broker", "place_order", "submit_order", "buy_order",
                "sell_order", "alpaca", "execute_trade", "crontab",
                "apscheduler", "schedule_job"):
        assert tok not in text, f"harness contains automation token: {tok!r}"


# --------------------------------------------------------------------------- #
# 10. No production DB write / migration tokens
# --------------------------------------------------------------------------- #
def test_no_db_write_or_migration_tokens():
    text = _read(_HARNESS).lower()
    for tok in ("create_engine", "psycopg", "to_sql", "insert into",
                "update ", "delete from", "alembic", "op.create_table",
                "create table ", "alter table ", "drop table ", ".commit(",
                "session.add"):
        assert tok not in text, f"harness contains DB-write/migration token: {tok!r}"


# --------------------------------------------------------------------------- #
# 11. Never enables PREDICTOR_USE_MODEL_V2 (or writes any env var)
# --------------------------------------------------------------------------- #
def test_does_not_enable_flag():
    text = _read(_HARNESS)
    assert "os.environ[" not in text, "harness writes an environment variable"
    assert "os.putenv" not in text
    assert "setenv" not in text.lower()
    tree = ast.parse(text)
    for node in ast.walk(tree):
        if isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Store):
            val = getattr(node.value, "attr", None)
            assert val != "environ", "harness assigns into os.environ"
    # The summary always reports the flag disabled.
    with tempfile.TemporaryDirectory() as tmp:
        _mod, summary, _out = _run_on_fixture(tmp)
        assert summary["model_v2_enabled"] is False
        assert summary["feature_flag"] == _FLAG


# --------------------------------------------------------------------------- #
# 12. No Paper Trader import
# --------------------------------------------------------------------------- #
def test_no_paper_trader_import():
    assert "paper_trader" not in _read(_HARNESS).lower()
    offenders = [m for m in sys.modules
                 if m == "paper_trader" or m.startswith("paper_trader.")]
    assert not offenders, f"unexpected Paper Trader import: {offenders}"


# --------------------------------------------------------------------------- #
# 13. Required-column validation on the input CSV
# --------------------------------------------------------------------------- #
def test_csv_schema_is_enforced():
    import pandas as pd
    mod = _import_harness()
    with tempfile.TemporaryDirectory() as tmp:
        bad = os.path.join(tmp, "bad.csv")
        pd.DataFrame({"ticker": ["X"], "adj_close": [10.0]}).to_csv(bad, index=False)
        try:
            mod.read_price_csv(bad)
        except ValueError as e:
            assert "date" in str(e)
            return
    raise AssertionError("read_price_csv must reject a CSV missing required columns")


# --------------------------------------------------------------------------- #
# 14. Documentation exists and makes the required explicit statements
# --------------------------------------------------------------------------- #
def test_doc_exists_and_states_guardrails():
    assert os.path.exists(_DOC), "harness doc missing"
    text = _read(_DOC).lower()
    for needle in (
        "does not deploy",
        "predictor_use_model_v2",
        "does not run migrations",
        "does not write to",
        "does not trade",
        "not production edge",
        "phase 2g-b",
    ):
        assert needle in text, f"doc missing required statement: {needle!r}"


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
