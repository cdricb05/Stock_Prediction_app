"""Phase 2G-B — tests for the real-data CSV validation runner.

These tests prove the strict wrapper around the Phase 2G-A shadow harness honors
every guardrail: it compiles; it refuses to run without ``--input-csv``; it
refuses to run without ``--confirm-real-data`` in normal mode; it accepts
``--test-fixture-mode`` and marks the output as a non-real, NO_GO fixture run;
it validates the required CSV columns, the benchmark presence, and the minimum
ticker / date thresholds; the output carries every required safety field; and
the runner never deploys, never connects to / writes a database, never runs a
migration, never enables ``PREDICTOR_USE_MODEL_V2``, never imports / modifies
``api_server.py``, and contains no broker / order / automation logic.

The wrapper is offline and file-based; these tests run it on a temporary fixture
CSV in a temp directory and never touch a network, a database, or the live
service.

Runs two ways:
  * under pytest:   pytest tests/test_phase2g_real_data_validation.py
  * without pytest: python tests/test_phase2g_real_data_validation.py
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
_RUNNER = os.path.join(_REPO_ROOT, "research", "run_phase2g_real_data_validation.py")
_DOC = os.path.join(_REPO_ROOT, "docs", "phase2g_b_real_data_validation_runner_v1.md")
_PLAN = os.path.join(_REPO_ROOT, "research", "output",
                     "phase2g_b_real_data_validation_plan.json")

# Every field the deliverable requires in the runner summary JSON.
_REQUIRED_FIELDS = (
    "phase", "phase2g_a_summary", "input_mode", "confirm_real_data",
    "test_fixture_mode", "real_data_validation_attempted", "database_touched",
    "migration_executed", "deployment_executed", "model_v2_enabled",
    "production_edge_claimed", "no_trading", "no_orders", "no_automation",
    "safe_for_canary", "go_no_go", "go_no_go_reason",
)


def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _import_runner():
    """Import the runner module without running main()."""
    spec = importlib.util.spec_from_file_location("phase2g_b_runner", _RUNNER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _write_fixture_csv(mod, tmpdir: str) -> str:
    """Write the 2G-A deterministic fixture CSV (small, synthetic) into tmpdir."""
    csv_path = os.path.join(tmpdir, "fixture.csv")
    mod.shadow.build_fixture_frame().to_csv(csv_path, index=False)
    return csv_path


def _run_fixture_mode(mod, tmpdir: str):
    """Run the wrapper in test-fixture-mode on the fixture CSV; return summary."""
    csv_path = _write_fixture_csv(mod, tmpdir)
    out_path = os.path.join(tmpdir, "out.json")
    scored = os.path.join(tmpdir, "scored.csv")
    summary = mod.run_real_data_validation(
        input_csv=csv_path, output_json=out_path, scored_csv=scored,
        confirm_real_data=False, test_fixture_mode=True)
    return summary, out_path


# --------------------------------------------------------------------------- #
# 1. Runner compiles
# --------------------------------------------------------------------------- #
def test_runner_compiles():
    compile(_read(_RUNNER), _RUNNER, "exec")


# --------------------------------------------------------------------------- #
# 2. Refuses missing --input-csv
# --------------------------------------------------------------------------- #
def test_refuses_missing_input_csv():
    mod = _import_runner()
    # Direct call: no input csv, even with confirmation, must refuse.
    try:
        mod.run_real_data_validation(
            input_csv="", output_json="", confirm_real_data=True)
    except mod.ValidationRefusal as e:
        assert "input-csv" in str(e).lower()
    else:
        raise AssertionError("runner must refuse without --input-csv")
    # Via main(): non-zero exit, no output file.
    rc = mod.main(["--confirm-real-data"])
    assert rc != 0, "main must return non-zero without --input-csv"


# --------------------------------------------------------------------------- #
# 3. Refuses missing --confirm-real-data in normal mode
# --------------------------------------------------------------------------- #
def test_refuses_missing_confirm_real_data():
    mod = _import_runner()
    with tempfile.TemporaryDirectory() as tmp:
        csv_path = _write_fixture_csv(mod, tmp)
        try:
            mod.run_real_data_validation(
                input_csv=csv_path, output_json="", confirm_real_data=False,
                test_fixture_mode=False)
        except mod.ValidationRefusal as e:
            assert "confirm-real-data" in str(e).lower()
        else:
            raise AssertionError("runner must refuse without --confirm-real-data")
        rc = mod.main(["--input-csv", csv_path])
        assert rc != 0, "main must return non-zero without --confirm-real-data"


# --------------------------------------------------------------------------- #
# 4. Accepts --test-fixture-mode and marks output test_fixture_mode true
# --------------------------------------------------------------------------- #
def test_test_fixture_mode_marks_output():
    mod = _import_runner()
    with tempfile.TemporaryDirectory() as tmp:
        summary, out_path = _run_fixture_mode(mod, tmp)
        assert os.path.exists(out_path), "summary JSON not written"
        on_disk = json.loads(_read(out_path))
        assert on_disk["phase"] == "2G-B"
        assert summary["test_fixture_mode"] is True
        assert summary["production_edge_claimed"] is False
        assert summary["safe_for_canary"] is False
        assert summary["go_no_go"] == "NO_GO"
        assert summary["real_data_validation_attempted"] is False
        # The nested 2G-A summary must exist and itself be honest.
        inner = summary["phase2g_a_summary"]
        assert inner is not None
        assert inner["fixture_only"] is True
        assert inner["safe_for_canary"] is False


# --------------------------------------------------------------------------- #
# 5. Validates required CSV columns
# --------------------------------------------------------------------------- #
def test_validates_required_columns():
    import pandas as pd
    mod = _import_runner()
    with tempfile.TemporaryDirectory() as tmp:
        bad = os.path.join(tmp, "bad.csv")
        pd.DataFrame({"ticker": ["X"], "adj_close": [10.0]}).to_csv(bad, index=False)
        ok, problems, _stats = mod.validate_input_csv(bad)
        assert ok is False
        assert any("date" in p for p in problems), problems
        # And the runner refuses on it in fixture mode too.
        try:
            mod.run_real_data_validation(
                input_csv=bad, output_json="", test_fixture_mode=True)
        except mod.ValidationRefusal as e:
            assert "validation" in str(e).lower()
        else:
            raise AssertionError("runner must refuse a CSV missing columns")


# --------------------------------------------------------------------------- #
# 6. Validates benchmark ticker presence
# --------------------------------------------------------------------------- #
def test_validates_benchmark_presence():
    import pandas as pd
    mod = _import_runner()
    with tempfile.TemporaryDirectory() as tmp:
        # A wide-enough universe but with NO benchmark (SPY) row.
        rows = []
        dates = pd.date_range("2024-01-01", periods=130, freq="D")
        for i in range(25):
            for d in dates:
                rows.append({"ticker": f"T{i}", "date": d.date().isoformat(),
                             "adj_close": 100.0 + i})
        no_spy = os.path.join(tmp, "no_spy.csv")
        pd.DataFrame(rows).to_csv(no_spy, index=False)
        ok, problems, stats = mod.validate_input_csv(no_spy)
        assert ok is False
        assert stats["benchmark_present"] is False
        assert any("benchmark" in p.lower() for p in problems), problems
        # Overriding the benchmark to one that IS present passes the bench check.
        ok2, problems2, _ = mod.validate_input_csv(no_spy, benchmark="T0")
        assert ok2 is True, problems2


# --------------------------------------------------------------------------- #
# 7. Validates minimum tickers / dates
# --------------------------------------------------------------------------- #
def test_validates_minimum_tickers_and_dates():
    mod = _import_runner()
    with tempfile.TemporaryDirectory() as tmp:
        csv_path = _write_fixture_csv(mod, tmp)  # 6 tickers + SPY, ~180 dates
        # Real-data thresholds (20 tickers): the small fixture must fail.
        ok, problems, stats = mod.validate_input_csv(
            csv_path, min_tickers=20, min_dates=120)
        assert ok is False
        assert any("universe tickers" in p for p in problems), problems
        # A confirmed real-data run over the too-small fixture must be refused
        # (so no production-edge claim can ever come from fixture-sized data).
        try:
            mod.run_real_data_validation(
                input_csv=csv_path, output_json="", confirm_real_data=True,
                test_fixture_mode=False)
        except mod.ValidationRefusal as e:
            assert "too few" in str(e).lower()
        else:
            raise AssertionError("runner must refuse a too-small real-data CSV")
        # Lowering the date floor still trips on the ticker floor; lowering both
        # passes validation.
        ok2, _p2, _s2 = mod.validate_input_csv(
            csv_path, min_tickers=1, min_dates=1)
        assert ok2 is True


# --------------------------------------------------------------------------- #
# 8. Output contains all required safety fields
# --------------------------------------------------------------------------- #
def test_output_has_all_required_fields():
    mod = _import_runner()
    with tempfile.TemporaryDirectory() as tmp:
        summary, _out = _run_fixture_mode(mod, tmp)
        for field in _REQUIRED_FIELDS:
            assert field in summary, f"summary missing required field: {field}"
        assert summary["phase"] == "2G-B"
        assert summary["input_mode"] == "csv"


# --------------------------------------------------------------------------- #
# 9. Safety flags are all safe in the output
# --------------------------------------------------------------------------- #
def test_safety_flags_are_safe():
    mod = _import_runner()
    with tempfile.TemporaryDirectory() as tmp:
        summary, _out = _run_fixture_mode(mod, tmp)
        assert summary["database_touched"] is False
        assert summary["migration_executed"] is False
        assert summary["deployment_executed"] is False
        assert summary["model_v2_enabled"] is False
        assert summary["production_edge_claimed"] is False
        assert summary["no_trading"] is True
        assert summary["no_orders"] is True
        assert summary["no_automation"] is True


# --------------------------------------------------------------------------- #
# 10. No api_server.py import or modification
# --------------------------------------------------------------------------- #
def test_no_api_server_modification():
    text = _read(_RUNNER)
    assert "import api_server" not in text, "runner imports api_server"
    assert "from api_server" not in text, "runner imports from api_server"
    tree = ast.parse(text)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            mods = ([n.name for n in node.names]
                    + ([node.module] if isinstance(node, ast.ImportFrom)
                       and node.module else []))
            assert not any((m or "").split(".")[0] == "api_server" for m in mods)
    assert "api_server.py" not in text.replace("``api_server.py``", ""), \
        "runner opens/writes api_server.py"


# --------------------------------------------------------------------------- #
# 11. No gcloud / SSH / subprocess / shellout logic
# --------------------------------------------------------------------------- #
def test_no_deploy_or_shellout_logic():
    text = _read(_RUNNER).lower()
    for tok in ("subprocess", "os.system", "popen", "pexpect", "paramiko",
                "os.exec", "shell=true", '"gcloud', "'gcloud", '"ssh ',
                "'ssh ", "systemctl", "uvicorn"):
        assert tok not in text, f"runner contains deploy/shellout token: {tok!r}"


# --------------------------------------------------------------------------- #
# 12. No production DB connection / write / migration tokens
# --------------------------------------------------------------------------- #
def test_no_db_write_or_migration_tokens():
    text = _read(_RUNNER).lower()
    for tok in ("create_engine", "psycopg", "to_sql", "insert into",
                "update ", "delete from", "alembic", "op.create_table",
                "create table ", "alter table ", "drop table ", ".commit(",
                "session.add", "sqlalchemy"):
        assert tok not in text, f"runner contains DB token: {tok!r}"


# --------------------------------------------------------------------------- #
# 13. No broker / order / automation implementation tokens
# --------------------------------------------------------------------------- #
def test_no_broker_order_automation():
    # The bare word "automation" is excluded because the required safety flag
    # `no_automation` legitimately contains it.
    text = _read(_RUNNER).lower()
    for tok in ("broker", "place_order", "submit_order", "buy_order",
                "sell_order", "alpaca", "execute_trade", "crontab",
                "apscheduler", "schedule_job"):
        assert tok not in text, f"runner contains automation token: {tok!r}"


# --------------------------------------------------------------------------- #
# 14. Never enables PREDICTOR_USE_MODEL_V2 (or writes any env var)
# --------------------------------------------------------------------------- #
def test_does_not_enable_flag():
    text = _read(_RUNNER)
    assert "os.environ[" not in text, "runner writes an environment variable"
    assert "os.putenv" not in text
    assert "setenv" not in text.lower()
    tree = ast.parse(text)
    for node in ast.walk(tree):
        if isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Store):
            val = getattr(node.value, "attr", None)
            assert val != "environ", "runner assigns into os.environ"
    with tempfile.TemporaryDirectory() as tmp:
        summary, _out = _run_fixture_mode(_import_runner(), tmp)
        assert summary["model_v2_enabled"] is False
        assert summary["feature_flag"] == _FLAG


# --------------------------------------------------------------------------- #
# 15. No Paper Trader import
# --------------------------------------------------------------------------- #
def test_no_paper_trader_import():
    assert "paper_trader" not in _read(_RUNNER).lower()
    offenders = [m for m in sys.modules
                 if m == "paper_trader" or m.startswith("paper_trader.")]
    assert not offenders, f"unexpected Paper Trader import: {offenders}"


# --------------------------------------------------------------------------- #
# 16. Plan JSON exists and is honest
# --------------------------------------------------------------------------- #
def test_plan_json_exists_and_is_safe():
    assert os.path.exists(_PLAN), "plan JSON missing"
    plan = json.loads(_read(_PLAN))
    assert plan["phase"] == "2G-B"
    for k in ("deployment_executed", "migration_executed", "database_touched",
              "model_v2_enabled", "production_edge_claimed"):
        assert plan[k] is False, f"plan {k} must be false"
    assert "next_step" in plan


# --------------------------------------------------------------------------- #
# 17. Documentation exists and makes the required explicit statements
# --------------------------------------------------------------------------- #
def test_doc_exists_and_states_guardrails():
    assert os.path.exists(_DOC), "runner doc missing"
    text = _read(_DOC).lower()
    for needle in (
        "does not deploy",
        "does not use gcloud",
        "stock-api.service",
        "predictor_use_model_v2",
        "does not run migrations",
        "does not connect to",
        "does not write to",
        "does not trade",
        "not production edge",
        "go_no_go",
        "phase 2g-c",
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
