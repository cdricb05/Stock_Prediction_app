"""Tests for Phase 2E-C — flag-gated model-v2 wiring in ``api_server.py``.

The model-v2 path in ``api_server.py`` is OFF by default and fail-closed: when
``PREDICTOR_USE_MODEL_V2`` is unset/false, or rows are missing, or anything
raises, the gated helper returns ``None`` so the existing legacy model path runs
unchanged.

These tests do **not** import ``api_server`` as a module (that would pull in
fastapi/prophet/xgboost/sklearn and attempt an import-time DB connection).
Instead they extract the *real* flag-gated block from ``api_server.py`` —
delimited by ``PHASE2E_C_MODELV2_BEGIN``/``END`` markers — and ``exec`` it in an
isolated namespace with a dummy logger and the real ``serve_v2`` adapter. So the
behavioral tests run the actual shipped source with **no** DB connection, no DB
writes, no migration, no Paper Trader import, and no sklearn/xgboost dependency.

Runs two ways:
  * under pytest:   pytest tests/test_phase2_wiring.py
  * without pytest: python tests/test_phase2_wiring.py
The __main__ block discovers every ``test_*`` function, runs it, prints
PASS/FAIL, and exits non-zero on any failure (the GCP venv has no pytest).
"""
from __future__ import annotations

import os
import sys
import types

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import pandas as pd  # noqa: E402

from model import serve_v2 as SV  # noqa: E402

_API_SERVER = os.path.join(_REPO_ROOT, "api_server.py")
_SAMPLE_CSV = os.path.join(_REPO_ROOT, "research", "output",
                           "phase2d_prediction_outputs_sample.csv")

_FLAG = "PREDICTOR_USE_MODEL_V2"

# Legacy keys existing /predict consumers expect on a single-ticker response.
_LEGACY_KEYS = ("ticker", "recommendation", "confidence", "agreement",
                "ensemble_day5", "predictions", "zscore")

_BEGIN = "# >>> MODELV2_GATE_BEGIN"
_END = "# <<< MODELV2_GATE_END"


def _extract_gated_block() -> str:
    """Return the real model-v2 helper block from api_server.py (between markers)."""
    with open(_API_SERVER, "r", encoding="utf-8") as f:
        src = f.read()
    assert _BEGIN in src and _END in src, "gated markers missing from api_server.py"
    after = src.split(_BEGIN, 1)[1].split(_END, 1)[0]
    # Drop the remainder of the marker line; keep the column-0 helper body.
    block = after.split("\n", 1)[1]
    return block


def _load_gated_namespace(loader=None):
    """Exec the real gated block in isolation; optionally inject a row loader.

    A dummy logger keeps it dependency-light. The real ``serve_v2`` is bound as
    ``_serve_v2`` after exec (the block's own guarded import also succeeds).
    """
    ns = {"os": os, "logger": _DummyLogger()}
    exec(compile(_extract_gated_block(), _API_SERVER, "exec"), ns)
    if loader is not None:
        ns["_load_model_v2_rows"] = loader
    return ns


class _DummyLogger:
    def info(self, *a, **k):
        pass

    def warning(self, *a, **k):
        pass

    def error(self, *a, **k):
        pass


def _clear_flag():
    os.environ.pop(_FLAG, None)


def _sample_rows() -> pd.DataFrame:
    return SV.load_prediction_outputs(_SAMPLE_CSV)


def _one_sample_ticker() -> str:
    return str(_sample_rows()["ticker"].iloc[0])


# --------------------------------------------------------------------------- #
# 1. Flag defaults off
# --------------------------------------------------------------------------- #
def test_flag_defaults_off():
    _clear_flag()
    ns = _load_gated_namespace()
    # No env var set => disabled.
    assert ns["model_v2_is_enabled"]() is False
    # The pure helper agrees that a missing value is off.
    assert SV.model_v2_enabled(os.getenv(_FLAG)) is False


# --------------------------------------------------------------------------- #
# 2. Flag off => model-v2 lookup is NOT called; helper returns None (-> legacy)
# --------------------------------------------------------------------------- #
def test_flag_off_lookup_not_called():
    _clear_flag()
    calls = {"n": 0}

    def _tracking_loader():
        calls["n"] += 1
        return _sample_rows()

    ns = _load_gated_namespace(loader=_tracking_loader)
    out = ns["model_v2_predict_single"]("AAPL")
    assert out is None              # -> caller falls through to legacy path
    assert calls["n"] == 0          # loader never invoked when flag is off


def test_flag_off_legacy_path_reachable():
    # With the flag off, the gated helper returns None, so predict_all proceeds
    # to its existing legacy code (get_fresh_series ... run_model_suite ...).
    # We assert (a) the helper yields None, and (b) the source returns the v2
    # response BEFORE any legacy work only when it is non-None.
    _clear_flag()
    ns = _load_gated_namespace()
    assert ns["model_v2_predict_single"]("AAPL") is None

    with open(_API_SERVER, "r", encoding="utf-8") as f:
        src = f.read()
    gate = src.index("v2_resp = model_v2_predict_single(ticker)")
    legacy = src.index("df = get_fresh_series(ticker", gate)
    guarded_return = src.index("if v2_resp is not None:", gate)
    # The flag-gated return is placed before the legacy data fetch.
    assert gate < guarded_return < legacy


# --------------------------------------------------------------------------- #
# 3. Flag on + rows exist => v2 response path is used (legacy keys preserved)
# --------------------------------------------------------------------------- #
def test_flag_on_with_rows_uses_v2():
    os.environ[_FLAG] = "1"
    try:
        ns = _load_gated_namespace(loader=_sample_rows)
        assert ns["model_v2_is_enabled"]() is True
        tk = _one_sample_ticker()
        out = ns["model_v2_predict_single"](tk)
        assert out is not None
        assert out["ticker"] == tk
        assert out["model_v2"] is True
        for k in _LEGACY_KEYS:
            assert k in out, f"v2 response missing legacy key: {k}"
    finally:
        _clear_flag()


def test_flag_on_batch_shape_preserved():
    os.environ[_FLAG] = "true"
    try:
        ns = _load_gated_namespace(loader=_sample_rows)
        batch = ns["model_v2_predict_batch"](None)
        assert batch is not None
        assert batch["model_v2"] is True
        assert batch["count"] == len(batch["results"]) >= 1
        # each result still carries the legacy keys
        for k in _LEGACY_KEYS:
            assert k in batch["results"][0]
    finally:
        _clear_flag()


# --------------------------------------------------------------------------- #
# 4. Flag on + rows missing => fallback (None)
# --------------------------------------------------------------------------- #
def test_flag_on_missing_rows_falls_back():
    os.environ[_FLAG] = "yes"
    try:
        ns = _load_gated_namespace(loader=lambda: pd.DataFrame())
        # empty frame => no latest row => None (legacy fallback)
        assert ns["model_v2_predict_single"]("AAPL") is None
        assert ns["model_v2_predict_batch"](None) is None
    finally:
        _clear_flag()


def test_flag_on_unknown_ticker_falls_back():
    os.environ[_FLAG] = "on"
    try:
        ns = _load_gated_namespace(loader=_sample_rows)
        # a ticker absent from the rows => None (legacy fallback)
        assert ns["model_v2_predict_single"]("NOT_A_REAL_TICKER_ZZZ") is None
    finally:
        _clear_flag()


# --------------------------------------------------------------------------- #
# 5. Flag on + lookup raises => fail closed (None)
# --------------------------------------------------------------------------- #
def test_flag_on_loader_raises_falls_back():
    os.environ[_FLAG] = "1"
    try:
        def _boom():
            raise RuntimeError("simulated read failure")

        ns = _load_gated_namespace(loader=_boom)
        assert ns["model_v2_predict_single"]("AAPL") is None
        assert ns["model_v2_predict_batch"](None) is None
    finally:
        _clear_flag()


# --------------------------------------------------------------------------- #
# 6. Guardrails: no DB / migration / Paper Trader / sklearn / xgboost; gated only
# --------------------------------------------------------------------------- #
def test_no_db_connection_or_writes_in_gated_path():
    # Sabotage the store engine builder: if the gated path tried to connect, fail.
    orig = SV.STORE._build_engine
    SV.STORE._build_engine = lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("model-v2 wiring must not connect to a DB"))
    os.environ[_FLAG] = "1"
    try:
        ns = _load_gated_namespace(loader=_sample_rows)
        ns["model_v2_predict_single"](_one_sample_ticker())
        ns["model_v2_predict_batch"](None)
    finally:
        SV.STORE._build_engine = orig
        _clear_flag()


def test_no_paper_trader_import():
    offenders = [m for m in sys.modules
                 if m == "paper_trader" or m.startswith("paper_trader.")]
    assert not offenders, f"unexpected Paper Trader import: {offenders}"


def test_no_sklearn_xgboost_pulled_in_by_tests():
    # This test module + serve_v2 must not require sklearn/xgboost.
    assert "sklearn" not in sys.modules
    assert "xgboost" not in sys.modules


def test_gated_block_has_no_db_or_migration_tokens():
    block = _extract_gated_block().lower()
    for tok in ("create_engine", "sqlalchemy", "insert", "update ", "delete",
                "migrat", "commit", "to_sql"):
        assert tok not in block, f"gated block contains forbidden token: {tok!r}"


def test_api_server_references_v2_only_through_gated_helpers():
    with open(_API_SERVER, "r", encoding="utf-8") as f:
        src = f.read()
    # The flag is read in exactly one place, via os.getenv, inside the helper.
    assert src.count(f'os.getenv("{_FLAG}")') == 1
    # serve_v2 is imported once, behind a guarded try/except.
    assert src.count("from model import serve_v2") == 1
    # The pure flag helper is used (env value passed in).
    assert "model_v2_enabled(os.getenv(" in src
    # Any /predict use of model-v2 goes through the gated single-ticker helper.
    assert "model_v2_predict_single(ticker)" in src
    # The serve_v2 conversion helpers are referenced only inside the gated block.
    block = _extract_gated_block()
    assert "row_to_predict_response" in block
    assert "rows_to_predict_all_response" in block


def test_routes_and_legacy_keys_unchanged():
    with open(_API_SERVER, "r", encoding="utf-8") as f:
        src = f.read()
    # Existing route names are intact.
    assert '@app.post("/predict_all_models/"' in src
    assert '@app.get("/predict/{ticker}")' in src
    # Legacy response dict still emits its keys (no key removed by this phase).
    for k in ("ensemble_day5", "agreement", "confidence", "zscore",
              "recommendation"):
        assert f'"{k}"' in src


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
