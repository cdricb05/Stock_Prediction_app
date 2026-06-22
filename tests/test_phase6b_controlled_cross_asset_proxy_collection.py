"""Phase 6-B tests - Controlled Cross-Asset Proxy Data Collection.

Phase 6-B assembles the daily adjusted-close + volume history for the broad cross-asset proxy
universe needed to rerun the Phase 6-A macro context harness properly (Phase 6-C). It is DRY-RUN by
default (no network) and makes live Alpha Vantage requests only under --live with a key present.

These tests prove the discipline without ever touching the network:
  * the runner compiles, imports (network-free), and exposes the required API;
  * dry-run is the default and contacts no network; --live with NO key STOPS before any network
    access and reports the exact PowerShell set-key command (key read from env only, never printed);
  * the proxy universe is the Phase 6-A candidate universe (46 tickers across 8 asset-class layers);
  * the recommendation vocabulary is exactly the five allowed values; READY is reachable only when
    >= 12 usable proxies across >= 5 asset classes with >= 12 carrying >= 5 years of history;
  * an empty intake yields 0 usable proxies and NEEDS_ADDITIONAL_PROXY_COLLECTION; a synthetic
    full pack yields READY and a Phase 6-C plan with proceed_to_phase6c == True;
  * present-but-malformed files with nothing usable yield DATA_QUALITY_BLOCKED;
  * existing valid files are preserved (planned action skip_existing_valid);
  * raw payloads are routed to a gitignored research/output/*/raw/ cache; committed-safe artifacts
    carry counts / redacted URLs only - never a payload body or an API key;
  * the runner itself makes no direct network/library calls (network is delegated to the audited
    Phase 3-Y collector) and emits no DB / deployment / order / strategy-test / Phase-6A-rerun work;
  * every output file is Git-safe (< 50 MB) and no binary artifact is written;
  * results are deterministic.

Runs two ways:
  * under pytest:   pytest tests/test_phase6b_controlled_cross_asset_proxy_collection.py
  * without pytest: python tests/test_phase6b_controlled_cross_asset_proxy_collection.py
"""
from __future__ import annotations

import ast
import csv
import importlib.util
import json
import os
import sys
import tempfile

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

_RUNNER = os.path.join(_REPO_ROOT, "research",
                       "run_phase6b_controlled_cross_asset_proxy_collection.py")
_DOC = os.path.join(_REPO_ROOT, "docs",
                    "phase6b_controlled_cross_asset_proxy_collection_v1.md")


class _Skip(Exception):
    """Raised to mark a test skipped."""


# --------------------------------------------------------------------------- #
# Import the runner (must be network-free at import time)
# --------------------------------------------------------------------------- #
def _load_runner():
    spec = importlib.util.spec_from_file_location("phase6b_runner_under_test", _RUNNER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


MOD = _load_runner()


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _synth_csv(manual_dir, ticker, n_rows):
    """Write a synthetic but well-formed normalized <TICKER>.csv with n_rows ascending dates and a
    positive adjusted_close + volume. Fabricated for TESTS ONLY (temp dirs), never under research/."""
    os.makedirs(manual_dir, exist_ok=True)
    path = os.path.join(manual_dir, "%s.csv" % ticker)
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "open", "high", "low", "close", "adjusted_close", "volume"])
        y, m, d = 2008, 1, 1
        for i in range(n_rows):
            d += 1
            if d > 28:
                d = 1
                m += 1
                if m > 12:
                    m = 1
                    y += 1
            px = 100.0 + (i % 50) * 0.1
            w.writerow(["%04d-%02d-%02d" % (y, m, d), px, px + 1, px - 1, px, px, 1000000 + i])
    return path


def _run_in_temp(live=False, populate=None, max_requests=None):
    """Run the collector against fresh temp dirs. populate(manual_dir) may pre-create CSVs. Returns
    (result, paths)."""
    tmp = tempfile.mkdtemp(prefix="phase6b_test_")
    manual_dir = os.path.join(tmp, "manual")
    o_dir = os.path.join(tmp, "out")
    raw_dir = os.path.join(o_dir, "raw")
    result_json = os.path.join(o_dir, "phase6b_controlled_cross_asset_proxy_collection.json")
    os.makedirs(manual_dir, exist_ok=True)
    if populate:
        populate(manual_dir)
    result = MOD.run(result_json_path=result_json, o_dir=o_dir, manual_dir=manual_dir,
                     raw_dir=raw_dir, live=live, max_requests=max_requests, verbose=False)
    return result, {"tmp": tmp, "manual_dir": manual_dir, "o_dir": o_dir,
                    "raw_dir": raw_dir, "result_json": result_json}


def _populate_ready(manual_dir):
    # 12 usable proxies (>= 5yr each) across 5 asset classes.
    for tk in ("QQQ", "IWM", "DIA",        # equity_structure
               "HYG", "LQD",               # credit
               "VIXY",                     # volatility
               "TLT", "IEF", "SHY", "TIP", # rates_duration
               "GLD", "SLV"):              # commodity
        _synth_csv(manual_dir, tk, 1300)


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #
def test_runner_compiles_and_imports():
    assert hasattr(MOD, "run") and callable(MOD.run)
    for fn in ("build_plan", "validate", "decide", "collect_live", "resolve_api_key",
               "set_key_powershell_command", "read_normalized_csv"):
        assert callable(getattr(MOD, fn)), "missing %s" % fn


def test_phase_id():
    assert MOD.PHASE == "6-B"
    result, _ = _run_in_temp()
    assert result["phase"] == "6-B"


def test_recommendation_vocabulary_exact():
    assert set(MOD.ALLOWED_RECOMMENDATIONS) == {
        "READY_FOR_PHASE6C_MACRO_CONTEXT_RERUN",
        "NEEDS_ADDITIONAL_PROXY_COLLECTION",
        "PROVIDER_LIMIT_BLOCKED",
        "DATA_QUALITY_BLOCKED",
        "ERROR",
    }
    result, _ = _run_in_temp()
    assert result["recommendation"]["recommendation"] in MOD.ALLOWED_RECOMMENDATIONS
    assert result["recommendation"]["allowed_values"] == list(MOD.ALLOWED_RECOMMENDATIONS)


def test_proxy_universe_is_phase6a_candidate_universe():
    tickers = MOD.TARGET_TICKERS
    assert len(tickers) == 46, "expected 46 proxies, got %d" % len(tickers)
    assert len(set(tickers)) == len(tickers), "duplicate tickers in universe"
    classes = {ac for _, ac in MOD.PROXY_UNIVERSE}
    assert len(classes) == 8, "expected 8 asset-class layers, got %d" % len(classes)
    # Spot-check the candidate universe from the task across every layer.
    for tk in ("QQQ", "IWM", "DIA", "EFA", "EEM", "ACWI", "VT", "EWJ", "EWU", "FXI", "EWZ", "INDA",
               "TLT", "IEF", "SHY", "HYG", "LQD", "TIP", "UUP", "FXE", "FXY", "FXB", "FXA", "FXC",
               "GLD", "SLV", "DBC", "GSG", "USO", "BNO", "UNG", "CPER", "VIXY",
               "XLE", "XLK", "XLF", "XLI", "XLV", "XLY", "XLP", "XLU", "XLRE", "XLB", "XLC",
               "SMH", "KRE"):
        assert tk in tickers, "missing candidate proxy %s" % tk


def test_ready_thresholds():
    assert MOD.READY_MIN_PROXIES == 12
    assert MOD.READY_MIN_ASSET_CLASSES == 5
    assert MOD.READY_MIN_FIVE_YEAR == 12
    assert MOD.MIN_USABLE_ROWS == 250
    assert MOD.FIVE_YEAR_ROWS >= 1259  # ~5 trading years


def test_dry_run_default_no_network():
    result, _ = _run_in_temp(live=False)
    assert result["mode"] == "dry_run"
    assert result["live_requested"] is False
    assert result["network_called"] is False
    assert result["alpha_vantage_called"] is False
    assert result["requests_attempted"] == 0
    # argparse default must be dry-run.
    ns = MOD._parse_args([])
    assert ns.live is False


def test_live_without_key_stops_before_network():
    # No key set in this environment (the test never sets one).
    if MOD.resolve_api_key() is not None:
        raise _Skip("ALPHAVANTAGE_API_KEY is set in this environment; skip the no-key STOP test")
    result, _ = _run_in_temp(live=True)
    assert result["stopped_no_key"] is True
    assert result["network_called"] is False
    assert result["live_collection_run"] is False
    assert result["api_key_present"] is False
    assert result["requests_attempted"] == 0
    cmd = result["set_key_powershell_command"]
    assert "ALPHAVANTAGE_API_KEY" in cmd and "--live" in cmd


def test_empty_intake_needs_more_collection():
    result, _ = _run_in_temp()
    assert result["usable_proxy_count"] == 0
    assert result["missing_count"] == result["target_count"] == 46
    assert result["recommendation"]["recommendation"] == "NEEDS_ADDITIONAL_PROXY_COLLECTION"
    assert result["ready_threshold_met"] is False
    assert result["recommendation"]["proceed_to_phase6c_now"] is False


def test_synthetic_full_pack_is_ready():
    result, _ = _run_in_temp(populate=_populate_ready)
    assert result["usable_proxy_count"] == 12
    assert result["asset_classes_covered"] == 5
    assert result["five_year_history_count"] == 12
    assert result["ready_threshold_met"] is True
    assert result["recommendation"]["recommendation"] == "READY_FOR_PHASE6C_MACRO_CONTEXT_RERUN"
    plan = result["phase6c_macro_context_rerun_plan"]
    assert plan["proceed_to_phase6c"] is True
    assert plan["rerun_phase6a_now"] is False
    assert result["recommended_next_phase"]["phase"] == "6-C"


def test_below_threshold_not_ready():
    def populate(d):
        # 6 usable proxies (< 12) -> still needs more.
        for tk in ("QQQ", "IWM", "DIA", "HYG", "LQD", "VIXY"):
            _synth_csv(d, tk, 1300)
    result, _ = _run_in_temp(populate=populate)
    assert result["usable_proxy_count"] == 6
    assert result["ready_threshold_met"] is False
    assert result["recommendation"]["recommendation"] == "NEEDS_ADDITIONAL_PROXY_COLLECTION"


def test_short_history_not_usable():
    def populate(d):
        _synth_csv(d, "QQQ", 100)  # < MIN_USABLE_ROWS
    result, paths = _run_in_temp(populate=populate)
    assert result["usable_proxy_count"] == 0
    q = {r["ticker"]: r for r in _read_rows(os.path.join(
        paths["o_dir"], "cross_asset_proxy_quality_report.csv"))}
    assert q["QQQ"]["quality_status"] == "SHORT_HISTORY"


def test_present_but_malformed_is_data_quality_blocked():
    def populate(d):
        # Present file with a wrong header (no adjusted_close) -> BAD_FORMAT, nothing usable.
        with open(os.path.join(d, "QQQ.csv"), "w", encoding="utf-8", newline="") as f:
            f.write("foo,bar\n1,2\n3,4\n")
    result, _ = _run_in_temp(populate=populate)
    assert result["usable_proxy_count"] == 0
    assert result["quality_blocked_count"] >= 1
    assert result["recommendation"]["recommendation"] == "DATA_QUALITY_BLOCKED"


def test_existing_valid_file_preserved_in_plan():
    def populate(d):
        _synth_csv(d, "QQQ", 1300)
    result, _ = _run_in_temp(populate=populate)
    plan = {r["ticker"]: r for r in result["collection_plan"]}
    assert plan["QQQ"]["planned_action"] == "skip_existing_valid"
    assert plan["IWM"]["planned_action"] == "would_download"  # dry-run wording


def test_raw_cache_dir_is_gitignored_path():
    # The raw cache must live under research/output/*/raw/, which the repo .gitignore ignores.
    assert MOD._RAW_DIR.replace("\\", "/").endswith(
        "research/output/phase6b_controlled_cross_asset_proxy_collection/raw")
    gi = os.path.join(_REPO_ROOT, ".gitignore")
    with open(gi, "r", encoding="utf-8") as f:
        text = f.read()
    assert "research/output/*/raw/" in text


def test_artifacts_written_and_git_safe():
    result, paths = _run_in_temp()
    expected = ("cross_asset_proxy_collection_plan.csv",
                "cross_asset_proxy_collection_status.csv",
                "cross_asset_proxy_quality_report.csv",
                "phase6c_macro_context_rerun_plan.json")
    for base in expected:
        p = os.path.join(paths["o_dir"], base)
        assert os.path.isfile(p), "missing artifact %s" % base
        assert os.path.getsize(p) < 50 * 1024 * 1024
    assert os.path.isfile(paths["result_json"])


def test_no_binary_artifacts():
    result, paths = _run_in_temp()
    for root, _dirs, files in os.walk(paths["o_dir"]):
        for name in files:
            assert name.lower().endswith((".csv", ".json")), "unexpected non-text artifact %s" % name


def test_status_and_quality_have_required_columns():
    result, paths = _run_in_temp(populate=_populate_ready)
    status = _read_rows(os.path.join(paths["o_dir"], "cross_asset_proxy_collection_status.csv"))
    quality = _read_rows(os.path.join(paths["o_dir"], "cross_asset_proxy_quality_report.csv"))
    assert len(status) == 46 and len(quality) == 46
    for col in ("ticker", "asset_class", "rows", "date_min", "date_max",
                "adjusted_close_available", "volume_available", "usable"):
        assert col in status[0]
        assert col in quality[0] or col == "action"


def test_set_key_command_is_redacted():
    cmd = MOD.set_key_powershell_command()
    assert "ALPHAVANTAGE_API_KEY" in cmd
    assert "--live" in cmd
    # The placeholder must NOT contain a real-looking key value.
    assert "<your-free-alpha-vantage-key>" in cmd


def test_runner_makes_no_direct_network_calls():
    with open(_RUNNER, "r", encoding="utf-8") as f:
        src = f.read()
    tree = ast.parse(src)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                imported.add(a.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    for forbidden in ("urllib", "requests", "http", "socket", "yfinance", "pandas_datareader"):
        assert forbidden not in imported, \
            "runner imports %s directly; network must be delegated to Phase 3-Y" % forbidden


def test_no_forbidden_provider_or_op_tokens():
    with open(_RUNNER, "r", encoding="utf-8") as f:
        low = f.read().lower()
    # Forbidden providers appear only as negative safety flags (…_called False), never as calls.
    for token in ("yahoo_called", "stooq_called", "fred_called", "fmp_called", "simfin_called"):
        assert token in low  # present as an explicit False safety flag
    # No strategy/shadow test, no Phase 6-A rerun, no orders/automation in this collector.
    assert "strategy_or_shadow_test_run" in low
    assert "phase6a_rerun" in low
    result, _ = _run_in_temp(populate=_populate_ready)
    assert result["strategy_or_shadow_test_run"] is False
    assert result["phase6a_rerun"] is False
    assert result["no_orders"] is True and result["no_automation"] is True
    assert result["recommendation"]["run_strategy_or_shadow_test_now"] is False
    assert result["recommendation"]["rerun_phase6a_now"] is False


def test_determinism():
    a, _ = _run_in_temp(populate=_populate_ready)
    b, _ = _run_in_temp(populate=_populate_ready)
    for k in ("usable_proxy_count", "asset_classes_covered", "five_year_history_count",
              "ready_threshold_met"):
        assert a[k] == b[k]
    assert (a["recommendation"]["recommendation"]
            == b["recommendation"]["recommendation"])


# --------------------------------------------------------------------------- #
# Small local helpers used by the tests
# --------------------------------------------------------------------------- #
def _read_rows(path):
    with open(path, "r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


# --------------------------------------------------------------------------- #
# __main__ runner (no pytest required)
# --------------------------------------------------------------------------- #
def _main():
    funcs = sorted((n, f) for n, f in globals().items()
                   if n.startswith("test_") and callable(f))
    passed = failed = skipped = 0
    for name, fn in funcs:
        try:
            fn()
        except _Skip as e:
            skipped += 1
            print("SKIP %s (%s)" % (name, e))
        except AssertionError as e:
            failed += 1
            print("FAIL %s: %s" % (name, e))
        except Exception as e:  # noqa: BLE001
            failed += 1
            print("ERROR %s: %s" % (name, e))
        else:
            passed += 1
            print("PASS %s" % name)
    print("\n%d passed, %d failed, %d skipped" % (passed, failed, skipped))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(_main())
