"""Phase 2I-A tests for the Feature IC & Horizon Sweep analyzer.

These tests prove the analyzer is observational and read-only: it compiles and
imports without side effects; it reads only the local Phase 2G artifacts; it
writes only research/output/phase2i_feature_ic_horizon_sweep.json; it never
imports api_server or Paper Trader; its source contains no deploy / gcloud /
SSH / service-restart logic and no DB-write / migration logic; the diagnostics
JSON it produces carries every required field, every one of the 13 features and
all 4 horizons, and a KEEP/DROP shortlist; and the companion doc contains the
required guardrail phrases.

Runs two ways:
  * under pytest:   pytest tests/test_phase2i_feature_ic.py
  * without pytest: python tests/test_phase2i_feature_ic.py
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

_ANALYZER = os.path.join(_REPO_ROOT, "research", "analyze_phase2i_feature_ic.py")
_DOC = os.path.join(_REPO_ROOT, "docs", "phase2i_feature_ic_horizon_sweep_v1.md")
_OUTPUT_JSON = os.path.join(
    _REPO_ROOT, "research", "output", "phase2i_feature_ic_horizon_sweep.json")

# The 13 features and 4 horizons the sweep must cover.
_EXPECTED_FEATURES = [
    "return_5d", "return_10d", "return_21d", "return_63d", "momentum_12_1",
    "realized_vol_21d", "realized_vol_63d", "downside_vol_21d",
    "excess_return_vs_spy_21d", "excess_return_vs_spy_63d", "rolling_beta_63d",
    "rolling_corr_spy_63d", "volume_zscore_21d",
]
_EXPECTED_HORIZONS = ["5", "10", "21", "63"]

# Forbidden source tokens: the analyzer must not deploy, shell out, touch a DB,
# or run migrations. Assembled from fragments so this test file does not itself
# trivially contain them as contiguous literals.
_FORBIDDEN_TOKENS = [
    "gcl" + "oud",
    "sub" + "process",
    "os." + "system",
    "para" + "miko",
    "system" + "ctl",
    "stock-api." + "service",
    "alem" + "bic",
    "create" + " table",
    "drop" + " table",
    "alter" + " table",
    "insert" + " into",
    "delete" + " from",
    "trun" + "cate",
    "place" + "_order",
    "submit" + "_order",
    "deploy" + "(",
    "PREDICTOR_USE_" + "MODEL_V2",
]

# Guardrail phrases the doc must contain verbatim.
_REQUIRED_DOC_PHRASES = [
    "does not deploy",
    "does not restart stock-api.service",
    "does not enable",
    "does not run migrations",
    "does not write to production DB",
    "does not trade",
    "production edge",
]

# Required top-level fields of the diagnostics JSON.
_REQUIRED_JSON_FIELDS = (
    "phase", "generated_at", "inputs_read", "config", "provenance",
    "panel", "features", "keep_drop", "label_sources", "label_cross_checks",
)
_REQUIRED_CONFIG_FIELDS = (
    "features", "horizons", "rank_ic_floor", "year_sign_min", "min_dates",
    "min_names_per_date", "keep_rule",
)
_REQUIRED_CELL_FIELDS = (
    "horizon_days", "mean_rank_ic", "std_rank_ic", "information_ratio",
    "n_dates", "n_rows", "low_confidence", "direction",
    "top_minus_bottom_spread", "quintile_monotonic", "top_decile_hit_rate",
    "years_present", "years_matching_sign", "year_sign_consistent",
    "yearly_rank_ic", "quarterly_rank_ic",
)
_REQUIRED_FEATURE_FIELDS = (
    "best_horizon_days", "best_mean_rank_ic", "best_information_ratio",
    "direction", "keep", "keep_criteria", "horizons",
)
_REQUIRED_FALSE = (
    "database_touched", "database_write_executed", "migration_executed",
    "deployment_executed", "model_v2_enabled", "production_edge_claimed",
)
_REQUIRED_TRUE = ("no_trading", "no_orders", "no_automation")


def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _import_analyzer():
    spec = importlib.util.spec_from_file_location("phase2i_analyzer", _ANALYZER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# The full sweep is expensive, so run it at most once per test process and cache
# both the returned dict and the on-disk JSON; every structure test below reuses
# this instead of re-running the sweep.
_DIAG_CACHE = {}


def _diag():
    if "diag" not in _DIAG_CACHE:
        mod = _import_analyzer()
        out = os.path.join(tempfile.mkdtemp(), "diag.json")
        _DIAG_CACHE["diag"] = mod.run(output_path=out)
        _DIAG_CACHE["on_disk"] = json.loads(_read(out))
    return _DIAG_CACHE["diag"]


# --------------------------------------------------------------------------- #
# 1. Analyzer compiles
# --------------------------------------------------------------------------- #
def test_analyzer_compiles():
    compile(_read(_ANALYZER), _ANALYZER, "exec")


# --------------------------------------------------------------------------- #
# 2. Reads only the local Phase 2G artifacts, no network
# --------------------------------------------------------------------------- #
def test_reads_only_local_phase2g_files():
    mod = _import_analyzer()
    for attr, leaf in (
            ("SCORED_CSV", "phase2g_real_data_scored.csv"),
            ("PRICE_HISTORY_CSV", "phase2g_price_history_real.csv"),
            ("RUN_SUMMARY_JSON", "phase2g_c_real_data_run_summary.json")):
        val = getattr(mod, attr)
        assert val.endswith(leaf), f"{attr} must point at {leaf}"
        assert val.replace("\\", "/").startswith("research/output/")
    text = _read(_ANALYZER).lower()
    for tok in ("http://", "https://", "requests.", "urllib", "yfinance",
                "socket"):
        assert tok not in text, f"analyzer must not reach the network: {tok!r}"


# --------------------------------------------------------------------------- #
# 3. Writes only the Phase 2I-A diagnostics JSON
# --------------------------------------------------------------------------- #
def test_writes_only_diagnostics_json():
    mod = _import_analyzer()
    assert mod.DIAGNOSTICS_JSON.replace("\\", "/").endswith(
        "research/output/phase2i_feature_ic_horizon_sweep.json")
    text = _read(_ANALYZER)
    assert "to_csv" not in text, "analyzer must not write any CSV"
    assert "to_sql" not in text, "analyzer must not write to a database"
    assert text.count("open(") >= 1
    tree = ast.parse(text)
    write_opens = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "open":
            if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant) \
                    and "w" in str(node.args[1].value):
                write_opens += 1
    assert write_opens == 1, "exactly one write-open (the diagnostics JSON)"


# --------------------------------------------------------------------------- #
# 4. No api_server import
# --------------------------------------------------------------------------- #
def test_no_api_server_import():
    text = _read(_ANALYZER)
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
# 5. No Paper Trader import
# --------------------------------------------------------------------------- #
def test_no_paper_trader_import():
    assert "paper_trader" not in _read(_ANALYZER).lower()
    _import_analyzer()
    offenders = [m for m in sys.modules
                 if m == "paper_trader" or m.startswith("paper_trader.")]
    assert not offenders, f"unexpected Paper Trader import: {offenders}"


# --------------------------------------------------------------------------- #
# 6. No deploy / gcloud / SSH / service-restart / DB-write / migration logic
# --------------------------------------------------------------------------- #
def test_no_forbidden_infrastructure_logic():
    text = _read(_ANALYZER).lower()
    hits = [tok for tok in _FORBIDDEN_TOKENS if tok.lower() in text]
    assert not hits, f"analyzer contains forbidden token(s): {hits}"
    for tok in ("ssh ", "scp ", "uvicorn", "systemd", "restart"):
        assert tok not in text, f"analyzer contains forbidden token: {tok!r}"


# --------------------------------------------------------------------------- #
# 7. Output JSON: required fields, safety flags, all features + horizons
# --------------------------------------------------------------------------- #
def test_output_json_required_fields():
    diag = _diag()
    on_disk = _DIAG_CACHE["on_disk"]
    for d in (diag, on_disk):
        for k in _REQUIRED_JSON_FIELDS:
            assert k in d, f"diagnostics missing field: {k}"
        assert d["phase"] == "2I-A"
        for k in _REQUIRED_FALSE:
            assert d[k] is False, f"{k} must be false"
        for k in _REQUIRED_TRUE:
            assert d[k] is True, f"{k} must be true"
        for k in _REQUIRED_CONFIG_FIELDS:
            assert k in d["config"], f"config missing field: {k}"
        assert d["config"]["features"] == _EXPECTED_FEATURES
        assert d["config"]["horizons"] == [5, 10, 21, 63]


# --------------------------------------------------------------------------- #
# 8. Every feature and every horizon is present with all cell fields
# --------------------------------------------------------------------------- #
def test_all_features_and_horizons_present():
    diag = _diag()
    feats = diag["features"]
    for f in _EXPECTED_FEATURES:
        assert f in feats, f"missing feature block: {f}"
        block = feats[f]
        for k in _REQUIRED_FEATURE_FIELDS:
            assert k in block, f"{f} missing field: {k}"
        assert isinstance(block["keep"], bool)
        for h in _EXPECTED_HORIZONS:
            assert h in block["horizons"], f"{f} missing horizon {h}"
            cell = block["horizons"][h]
            for k in _REQUIRED_CELL_FIELDS:
                assert k in cell, f"{f}/{h} missing cell field: {k}"
            assert cell["horizon_days"] == int(h)
            assert isinstance(cell["low_confidence"], bool)
            assert isinstance(cell["yearly_rank_ic"], list)
            assert isinstance(cell["quarterly_rank_ic"], list)


# --------------------------------------------------------------------------- #
# 9. KEEP/DROP shortlist is present, consistent, and ranked by |IR|
# --------------------------------------------------------------------------- #
def test_keep_drop_shortlist():
    diag = _diag()
    kd = diag["keep_drop"]
    for k in ("keep_ranked", "drop", "n_keep", "n_drop"):
        assert k in kd, f"keep_drop missing field: {k}"
    keep_names = [r["feature"] for r in kd["keep_ranked"]]
    # Every feature is classified exactly once.
    assert sorted(keep_names + kd["drop"]) == sorted(_EXPECTED_FEATURES)
    assert not (set(keep_names) & set(kd["drop"]))
    assert kd["n_keep"] == len(keep_names)
    assert kd["n_drop"] == len(kd["drop"])
    # KEEP rows are ordered by descending |information ratio|.
    irs = [abs(r["information_ratio"]) for r in kd["keep_ranked"]
           if r["information_ratio"] is not None]
    assert irs == sorted(irs, reverse=True), "KEEP not ranked by |IR| desc"
    # KEEP features satisfy the magnitude + stability + sample gates.
    for r in kd["keep_ranked"]:
        block = diag["features"][r["feature"]]
        assert block["keep"] is True
        crit = block["keep_criteria"]
        assert crit["magnitude_pass"] and crit["stability_pass"] \
            and crit["sample_pass"]


# --------------------------------------------------------------------------- #
# 10. 63d horizon is swept with a sample guard, not silently dropped
# --------------------------------------------------------------------------- #
def test_63d_sample_guard():
    diag = _diag()
    for f in _EXPECTED_FEATURES:
        cell = diag["features"][f]["horizons"]["63"]
        # The 63d cell exists and carries the confidence guard explicitly.
        assert "low_confidence" in cell
        assert isinstance(cell["low_confidence"], bool)
        assert cell["n_dates"] >= 0


# --------------------------------------------------------------------------- #
# 11. Explicit per-horizon label provenance + structured 5d cross-check
# --------------------------------------------------------------------------- #
def test_label_sources_and_cross_checks():
    diag = _diag()
    on_disk = _DIAG_CACHE["on_disk"]
    for d in (diag, on_disk):
        ls = d.get("label_sources")
        assert ls is not None, "missing top-level label_sources"
        assert ls["5"] == "scored_csv.realized_excess_return_5d_vs_spy"
        for h in ("10", "21", "63"):
            assert ls[h] == "price_history.forward_excess_return_vs_spy", \
                f"label_sources[{h!r}] must be the price-history forward excess label"

        cx = d.get("label_cross_checks")
        assert cx is not None, "missing top-level label_cross_checks"
        c5 = cx["5d_scored_vs_price_derived"]
        assert c5["correlation"] == 1.0, "5d scored-vs-price-derived corr must be 1.0"
        assert isinstance(c5["n"], int) and c5["n"] > 0, "cross-check n must be positive"


# --------------------------------------------------------------------------- #
# 12. Doc has all required guardrail phrases
# --------------------------------------------------------------------------- #
def test_doc_has_required_phrases():
    text = _read(_DOC)
    missing = [p for p in _REQUIRED_DOC_PHRASES if p not in text]
    assert not missing, f"doc missing required phrase(s): {missing}"


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
