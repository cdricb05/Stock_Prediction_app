"""Phase 2E-D — pre-deployment readiness tests for the model-v2 API wiring.

This suite proves the default-off model-v2 wiring in ``api_server.py`` is safe to
deploy with ``PREDICTOR_USE_MODEL_V2`` unset. It is a *readiness* check: it does
not deploy, does not connect to a database, does not run migrations, and does not
modify env files or secrets.

Like the 2E-C wiring tests, it does **not** import ``api_server`` as a module
(that would pull in fastapi/prophet/xgboost/sklearn and attempt an import-time DB
connection). It instead reads ``api_server.py`` as text, compiles it offline, and
extracts the *real* flag-gated block (between the ``MODELV2_GATE`` markers) to
exec in an isolated namespace.

Runs two ways:
  * under pytest:   pytest tests/test_phase2_predeploy.py
  * without pytest: python tests/test_phase2_predeploy.py
The __main__ block discovers every ``test_*`` function, runs it, prints
PASS/FAIL, and exits non-zero on any failure (the GCP venv has no pytest).
"""
from __future__ import annotations

import os
import py_compile
import sys
import tempfile

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

_API_SERVER = os.path.join(_REPO_ROOT, "api_server.py")

_FLAG = "PREDICTOR_USE_MODEL_V2"

# Live route names that must keep existing exactly as-is.
_ROUTES = (
    '@app.get("/predict/{ticker}")',
    '@app.post("/predict_all_models/"',
    '@app.get("/healthz")',
    '@app.get("/config")',
    '@app.get("/ping")',
)

# Legacy response keys existing consumers read; none may be removed.
_LEGACY_KEYS = ("recommendation", "confidence", "agreement",
                "ensemble_day5", "predictions", "zscore")

_BEGIN = "# >>> MODELV2_GATE_BEGIN"
_END = "# <<< MODELV2_GATE_END"


def _read_api_server() -> str:
    with open(_API_SERVER, "r", encoding="utf-8") as f:
        return f.read()


def _extract_gated_block() -> str:
    """Return the real model-v2 helper block from api_server.py (between markers)."""
    src = _read_api_server()
    assert _BEGIN in src and _END in src, "gated markers missing from api_server.py"
    after = src.split(_BEGIN, 1)[1].split(_END, 1)[0]
    # Drop the remainder of the marker line; keep the column-0 helper body.
    return after.split("\n", 1)[1]


class _DummyLogger:
    def info(self, *a, **k):
        pass

    def warning(self, *a, **k):
        pass

    def error(self, *a, **k):
        pass


def _load_gated_namespace(loader=None):
    """Exec the real gated block in isolation; optionally inject a row loader."""
    ns = {"os": os, "logger": _DummyLogger()}
    exec(compile(_extract_gated_block(), _API_SERVER, "exec"), ns)
    if loader is not None:
        ns["_load_model_v2_rows"] = loader
    return ns


def _clear_flag():
    os.environ.pop(_FLAG, None)


# --------------------------------------------------------------------------- #
# 1. api_server.py compiles
# --------------------------------------------------------------------------- #
def test_api_server_compiles():
    assert os.path.exists(_API_SERVER), "api_server.py not found"
    with tempfile.NamedTemporaryFile(suffix=".pyc", delete=False) as tmp:
        cfile = tmp.name
    try:
        # doraise=True turns a syntax error into a raised exception (test fails).
        py_compile.compile(_API_SERVER, cfile=cfile, doraise=True)
    finally:
        if os.path.exists(cfile):
            os.remove(cfile)


# --------------------------------------------------------------------------- #
# 2. Feature flag defaults off
# --------------------------------------------------------------------------- #
def test_flag_default_off():
    _clear_flag()
    ns = _load_gated_namespace()
    assert ns["model_v2_is_enabled"]() is False
    # The pure adapter agrees a missing value is off.
    from model import serve_v2 as SV
    assert SV.model_v2_enabled(os.getenv(_FLAG)) is False


# --------------------------------------------------------------------------- #
# 3. Flag-off path does not call the model-v2 lookup
# --------------------------------------------------------------------------- #
def test_flag_off_lookup_not_called():
    _clear_flag()
    calls = {"n": 0}

    def _tracking_loader():
        calls["n"] += 1
        return None

    ns = _load_gated_namespace(loader=_tracking_loader)
    assert ns["model_v2_predict_single"]("AAPL") is None
    assert ns["model_v2_predict_batch"](None) is None
    assert calls["n"] == 0, "row loader must not run when the flag is off"


# --------------------------------------------------------------------------- #
# 4. Legacy route names still exist
# --------------------------------------------------------------------------- #
def test_legacy_route_names_present():
    src = _read_api_server()
    for route in _ROUTES:
        assert route in src, f"missing live route: {route}"


# --------------------------------------------------------------------------- #
# 5. Legacy response keys still exist
# --------------------------------------------------------------------------- #
def test_legacy_response_keys_present():
    src = _read_api_server()
    for key in _LEGACY_KEYS:
        assert f'"{key}"' in src, f"missing legacy response key: {key}"


# --------------------------------------------------------------------------- #
# 6. Model-v2 imports are guarded
# --------------------------------------------------------------------------- #
def test_model_v2_import_is_guarded():
    block = _extract_gated_block()
    assert "from model import serve_v2" in block
    # The import sits inside a try/except that falls back to legacy-only.
    head = block.split("from model import serve_v2", 1)[0]
    assert head.rstrip().endswith("try:"), "serve_v2 import is not inside a try:"
    tail = block.split("from model import serve_v2", 1)[1]
    assert "except Exception" in tail
    assert "_serve_v2 = None" in tail
    # Imported exactly once across the whole file, only here.
    assert _read_api_server().count("from model import serve_v2") == 1


# --------------------------------------------------------------------------- #
# 7. Feature flag appears exactly where expected (once, via os.getenv, in block)
# --------------------------------------------------------------------------- #
def test_feature_flag_appears_where_expected():
    src = _read_api_server()
    needle = f'os.getenv("{_FLAG}")'
    assert src.count(needle) == 1, "flag must be read in exactly one place"
    assert needle in _extract_gated_block(), "flag read must live in the gated block"
    # The env value is passed into the pure adapter helper, not re-implemented.
    assert "model_v2_enabled(os.getenv(" in src


# --------------------------------------------------------------------------- #
# 8. No database write tokens in the gated model-v2 block
# --------------------------------------------------------------------------- #
def test_no_db_write_tokens_in_gated_block():
    block = _extract_gated_block().lower()
    for tok in ("create_engine", "sqlalchemy", "insert", "update ", "delete",
                "commit", "to_sql", "execute(", ".connect("):
        assert tok not in block, f"gated block contains DB token: {tok!r}"


# --------------------------------------------------------------------------- #
# 9. No migration is executed by this readiness package
# --------------------------------------------------------------------------- #
def test_no_migration_executed_by_wiring_or_package():
    block = _extract_gated_block().lower()
    assert "migrat" not in block, "gated block references a migration"
    # Prove no migration is *executed* anywhere in this readiness package by
    # scanning the gated block AND the predeploy script for actual migration
    # call patterns (not prose). The script names a `migration_executed` flag,
    # so we match call syntax, never the bare word.
    script = os.path.join(_REPO_ROOT, "scripts", "phase2e_d_predeploy_check.py")
    with open(script, "r", encoding="utf-8") as f:
        script_src = f.read().lower()
    for hay in (block, script_src):
        for tok in ("alembic.", "command.upgrade", "command.downgrade",
                    "op.create_table", "create table ", "alter table ",
                    "drop table "):
            assert tok not in hay, f"migration call pattern present: {tok!r}"


# --------------------------------------------------------------------------- #
# 10. No env file or secret is modified
# --------------------------------------------------------------------------- #
def test_no_env_or_secret_write_in_gated_block():
    block = _extract_gated_block()
    # Reading os.getenv is fine; writing env vars or .env files is not.
    assert "os.environ[" not in block, "gated block writes an environment variable"
    assert "os.putenv" not in block
    lower = block.lower()
    assert ".env" not in lower, "gated block references an .env file"
    assert "secret" not in lower, "gated block references a secret"


# --------------------------------------------------------------------------- #
# 11. No Paper Trader import
# --------------------------------------------------------------------------- #
def test_no_paper_trader_import():
    assert "paper_trader" not in _read_api_server().lower()
    offenders = [m for m in sys.modules
                 if m == "paper_trader" or m.startswith("paper_trader.")]
    assert not offenders, f"unexpected Paper Trader import: {offenders}"


# --------------------------------------------------------------------------- #
# 12. No sklearn/xgboost dependency added by the model-v2 wiring
# --------------------------------------------------------------------------- #
def test_no_sklearn_xgboost_added_by_model_v2():
    # api_server.py legitimately imports sklearn/xgboost for the LEGACY model;
    # the model-v2 wiring must add no new such dependency, so we scope the check
    # to the gated block and to this test module.
    block = _extract_gated_block().lower()
    for tok in ("sklearn", "xgboost"):
        assert tok not in block, f"model-v2 gated block pulls in {tok}"
    assert "sklearn" not in sys.modules
    assert "xgboost" not in sys.modules


# --------------------------------------------------------------------------- #
# 13. No broker / order / automation logic in the gated block
# --------------------------------------------------------------------------- #
def test_no_broker_order_automation_in_gated_block():
    block = _extract_gated_block().lower()
    for tok in ("broker", "place_order", "submit_order", "order(", "buy_order",
                "sell_order", "alpaca", "execute_trade", "schedule", "cron"):
        assert tok not in block, f"gated block contains automation token: {tok!r}"


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
