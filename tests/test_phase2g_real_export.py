"""Phase 2G-C — tests for the real-data yfinance price-history exporter.

These tests prove the exporter honors every guardrail: it compiles and imports
without a network call; it refuses to run without --source yfinance, without
--confirm-network, and without --start / --end; its schema validator requires
ticker, date, adj_close; the run summary marks SPY present and carries the
correct safety flags; it never imports api_server or Paper Trader; it contains
no broker / order / automation logic; and its source contains none of the
externally-scanned blocked tokens.

The fetch itself is monkeypatched in tests so the gate / schema / summary logic
is exercised offline (no real network). The blocked-token and import checks read
the exporter source statically.

Runs two ways:
  * under pytest:   pytest tests/test_phase2g_real_export.py
  * without pytest: python tests/test_phase2g_real_export.py
The __main__ block discovers every test_* function, runs it, prints PASS/FAIL,
and exits non-zero on any failure (the GCP venv has no pytest).
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

_EXPORTER = os.path.join(
    _REPO_ROOT, "research", "export_phase2g_price_history_csv.py")

# Literal tokens the external validation scans the EXPORTER SOURCE for. None of
# these may appear anywhere in the exporter (code, strings, comments, docstring).
# They are assembled from fragments here so this test file does not itself
# trivially contain them as contiguous literals.
_BLOCKED_TOKENS = [
    "para" + "miko",
    "sub" + "process",
    "os." + "system",
    "alem" + "bic",
    "create" + " table",
    "drop" + " table",
    "alter" + " table",
    "insert" + " into",
    "delete" + " from",
    "update" + " ",
    "trun" + "cate",
    "place" + "_order",
    "submit" + "_order",
    "alp" + "aca",
]

# Required run-summary safety flags and their expected values.
_REQUIRED_FALSE = (
    "database_touched", "database_write_executed", "migration_executed",
    "deployment_executed", "model_v2_enabled", "production_edge_claimed",
)
_REQUIRED_TRUE = ("no_trading", "no_orders", "no_automation")
_REQUIRED_FACTS = (
    "phase", "source", "row_count", "ticker_count", "universe_ticker_count",
    "date_start", "date_end", "benchmark_present", "volume_present",
)


def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _import_exporter():
    """Import the exporter module without running main() or any network call."""
    spec = importlib.util.spec_from_file_location("phase2g_c_exporter", _EXPORTER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _fake_frame(mod, *, with_spy=True, with_volume=True, n_tickers=40, n_days=130):
    """Build an in-memory long-format price frame mimicking a real export."""
    import pandas as pd

    universe = mod.curated_universe() if with_spy else [
        t for t in mod.curated_universe() if t != mod.BENCHMARK]
    universe = universe[:n_tickers] + (
        [mod.BENCHMARK] if with_spy and mod.BENCHMARK not in universe[:n_tickers]
        else [])
    dates = pd.date_range("2024-01-01", periods=n_days, freq="D")
    rows = []
    for i, t in enumerate(universe):
        for k, d in enumerate(dates):
            row = {"ticker": t, "date": d.date().isoformat(),
                   "adj_close": 100.0 + i + 0.1 * k}
            if with_volume:
                row["volume"] = 1_000_000 + k
            rows.append(row)
    return pd.DataFrame(rows)


def _run_export_offline(mod, tmpdir, *, with_spy=True, with_volume=True):
    """Run run_export with the network fetch monkeypatched to a fake frame."""
    import pandas as pd  # noqa: F401

    frame = _fake_frame(mod, with_spy=with_spy, with_volume=with_volume)
    original = mod.fetch_universe
    mod.fetch_universe = lambda tickers, start, end: (frame, [])
    try:
        out_csv = os.path.join(tmpdir, "prices.csv")
        out_json = os.path.join(tmpdir, "summary.json")
        summary = mod.run_export(
            source="yfinance", confirm_network=True,
            start="2024-01-01", end="2024-07-01",
            output_csv=out_csv, summary_json=out_json)
        return summary, out_csv, out_json
    finally:
        mod.fetch_universe = original


# --------------------------------------------------------------------------- #
# 1. Exporter compiles
# --------------------------------------------------------------------------- #
def test_exporter_compiles():
    compile(_read(_EXPORTER), _EXPORTER, "exec")


# --------------------------------------------------------------------------- #
# 2. Missing --source is refused
# --------------------------------------------------------------------------- #
def test_refuses_missing_source():
    mod = _import_exporter()
    try:
        mod.validate_run_request(
            source=None, confirm_network=True, start="2024-01-01", end="2024-07-01")
    except mod.ExportRefusal as e:
        assert "source" in str(e).lower()
    else:
        raise AssertionError("must refuse without --source yfinance")
    rc = mod.main(["--confirm-network", "--start", "2024-01-01",
                   "--end", "2024-07-01"])
    assert rc != 0, "main must return non-zero without --source"


# --------------------------------------------------------------------------- #
# 3. Missing --confirm-network is refused
# --------------------------------------------------------------------------- #
def test_refuses_missing_confirm_network():
    mod = _import_exporter()
    try:
        mod.validate_run_request(
            source="yfinance", confirm_network=False,
            start="2024-01-01", end="2024-07-01")
    except mod.ExportRefusal as e:
        assert "confirm-network" in str(e).lower()
    else:
        raise AssertionError("must refuse without --confirm-network")
    rc = mod.main(["--source", "yfinance", "--start", "2024-01-01",
                   "--end", "2024-07-01"])
    assert rc != 0, "main must return non-zero without --confirm-network"


# --------------------------------------------------------------------------- #
# 4. Missing --start / --end is refused
# --------------------------------------------------------------------------- #
def test_refuses_missing_start_end():
    mod = _import_exporter()
    for start, end in (("", "2024-07-01"), ("2024-01-01", ""), ("", "")):
        try:
            mod.validate_run_request(
                source="yfinance", confirm_network=True, start=start, end=end)
        except mod.ExportRefusal as e:
            assert "start" in str(e).lower() and "end" in str(e).lower()
        else:
            raise AssertionError("must refuse without --start/--end")
    rc = mod.main(["--source", "yfinance", "--confirm-network",
                   "--start", "2024-01-01"])
    assert rc != 0, "main must return non-zero without --end"


# --------------------------------------------------------------------------- #
# 5. Schema validator requires ticker, date, adj_close
# --------------------------------------------------------------------------- #
def test_schema_validator_requires_core_columns():
    mod = _import_exporter()
    ok, missing = mod.validate_schema(["ticker", "date", "adj_close", "volume"])
    assert ok is True and missing == []
    ok2, missing2 = mod.validate_schema(["ticker", "adj_close"])
    assert ok2 is False and "date" in missing2
    ok3, missing3 = mod.validate_schema(["ticker", "date"])
    assert ok3 is False and "adj_close" in missing3


# --------------------------------------------------------------------------- #
# 6. SPY presence is required (and detected) in the export summary
# --------------------------------------------------------------------------- #
def test_spy_presence_required():
    mod = _import_exporter()
    assert mod.BENCHMARK == "SPY"
    assert mod.BENCHMARK in mod.curated_universe()
    with tempfile.TemporaryDirectory() as tmp:
        summary, _csv, _json = _run_export_offline(mod, tmp, with_spy=True)
        assert summary["benchmark_present"] is True
        # And when SPY is absent from the fetched frame, the summary flags it.
        summary_no, _c2, _j2 = _run_export_offline(mod, tmp, with_spy=False)
        assert summary_no["benchmark_present"] is False


# --------------------------------------------------------------------------- #
# 7. Summary safety flags are correct
# --------------------------------------------------------------------------- #
def test_summary_safety_flags():
    mod = _import_exporter()
    with tempfile.TemporaryDirectory() as tmp:
        summary, _csv, out_json = _run_export_offline(mod, tmp)
        on_disk = json.loads(_read(out_json))
        for s in (summary, on_disk):
            assert s["phase"] == "2G-C"
            assert s["source"] == "yfinance"
            for k in _REQUIRED_FALSE:
                assert s[k] is False, f"{k} must be false"
            for k in _REQUIRED_TRUE:
                assert s[k] is True, f"{k} must be true"
            for k in _REQUIRED_FACTS:
                assert k in s, f"summary missing fact: {k}"
        assert summary["volume_present"] is True
        assert summary["row_count"] > 0
        assert summary["universe_ticker_count"] == len(mod.curated_universe())


# --------------------------------------------------------------------------- #
# 8. No api_server import
# --------------------------------------------------------------------------- #
def test_no_api_server_import():
    text = _read(_EXPORTER)
    assert "import api_server" not in text
    assert "from api_server" not in text
    tree = ast.parse(text)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            mods = ([n.name for n in node.names]
                    + ([node.module] if isinstance(node, ast.ImportFrom)
                       and node.module else []))
            assert not any((m or "").split(".")[0] == "api_server" for m in mods)


# --------------------------------------------------------------------------- #
# 9. No Paper Trader import
# --------------------------------------------------------------------------- #
def test_no_paper_trader_import():
    assert "paper_trader" not in _read(_EXPORTER).lower()
    mod = _import_exporter()  # noqa: F841 — importing must not pull Paper Trader
    offenders = [m for m in sys.modules
                 if m == "paper_trader" or m.startswith("paper_trader.")]
    assert not offenders, f"unexpected Paper Trader import: {offenders}"


# --------------------------------------------------------------------------- #
# 10. No broker / order / automation logic
# --------------------------------------------------------------------------- #
def test_no_broker_order_automation_logic():
    # The bare word "automation" is allowed only inside the no_automation flag.
    text = _read(_EXPORTER).lower()
    for tok in ("broker", "buy_order", "sell_order", "execute_trade",
                "crontab", "apscheduler", "schedule_job", "systemctl",
                "uvicorn", "gcloud", "deploy("):
        assert tok not in text, f"exporter contains forbidden token: {tok!r}"


# --------------------------------------------------------------------------- #
# 11. Exporter source contains none of the externally-scanned blocked tokens
# --------------------------------------------------------------------------- #
def test_no_blocked_tokens():
    text = _read(_EXPORTER).lower()
    hits = [tok for tok in _BLOCKED_TOKENS if tok in text]
    assert not hits, f"exporter contains blocked token(s): {hits}"


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
