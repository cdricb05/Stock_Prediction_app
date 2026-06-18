"""Phase 2K-I tests for the Expanded-Dataset Residual Signal Retest analyzer.

These tests prove the analyzer is a disciplined, read-only, model-free research artifact:
it compiles and imports cleanly; it references the expected D: drive input paths and the
upstream Phase 2K-H / 2K-C / 2K-B / 2L-B summaries; it tests exactly the required
candidate (avg_dollar_volume_21d); it reads the Phase 2K-H summary and clearly reports
NEED_MORE_DATA_OR_PIT_UNIVERSE when that build is not ready for retest; it emits every
required output field, safety flag, and a recommendation drawn only from the allowed
vocabulary; it always routes to Phase 2K-J; it carries the survivorship caveat forward;
it imports neither api_server nor Paper Trader; it contains no model-training / fitting,
infrastructure, network, or D:-mutation tokens and has exactly one write-open (the small
summary JSON); and the doc carries the guardrail phrases.

The full compute path is exercised deterministically on a small synthetic expanded
price-history CSV written to a temp data root (never the real D: drive, never the
network), together with a ready Phase 2K-H summary and the D: data-quality / build /
survivorship JSONs.

Runs two ways:
  * under pytest:   pytest tests/test_phase2k_i_expanded_residual_retest.py
  * without pytest: python tests/test_phase2k_i_expanded_residual_retest.py
The __main__ block discovers every test_* function, runs it, prints PASS/FAIL, and exits
non-zero on any failure (the GCP venv has no pytest).
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

_ANALYZER = os.path.join(
    _REPO_ROOT, "research", "analyze_phase2k_i_expanded_residual_retest.py")
_DOC = os.path.join(_REPO_ROOT, "docs", "phase2k_i_expanded_residual_retest_v1.md")

_D_DATA_ROOT = r"D:\Stock_Prediction_app_data\phase2k_g"
_D_DATA_ROOT_NORM = _D_DATA_ROOT.replace("\\", "/")

_REAL_OUTPUT_FILENAMES = (
    "phase2k_g_expanded_price_history_free.csv",
    "phase2k_g_expanded_scored_free.csv",
    "phase2k_g_data_quality_report.json",
    "phase2k_g_data_build_summary.json",
    "phase2k_g_survivorship_caveat.json",
)
# Large outputs must never appear under the C: repo output dir.
_C_OUTPUT_FILES = tuple(
    os.path.join("research", "output", name) for name in _REAL_OUTPUT_FILENAMES)

# Forbidden infrastructure tokens (assembled from fragments) — apply to the source.
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
_FORBIDDEN_EXTRA = ("ssh ", "scp ", "uvicorn", "systemd", "restart")

# Forbidden model-training / fitting tokens (no model work here).
_FORBIDDEN_MODEL_TOKENS = [
    ".fit(", "fit_transform", "sklearn", "tensorflow", "lightgbm", "xgboost",
    "torch", "keras", "LinearRegression", "RandomForest", "MLPRegressor",
    "GradientBoosting",
]

# Forbidden network / acquisition tokens — the analyzer must stay network-free.
_FORBIDDEN_NETWORK_TOKENS = [
    "http://", "https://", "requests.", "urllib", "yf" + "inance", "socket",
    "url" + "open", "down" + "load(", "pur" + "chase(",
]

# The analyzer must never mutate the D: data files or write large outputs.
_FORBIDDEN_MUTATION_TOKENS = [
    "os." + "remove", "os." + "rename", "os." + "unlink", "shutil", "rm" + "tree",
    "to_csv", "to_sql",
]

_REQUIRED_DOC_PHRASES = [
    "does not deploy",
    "does not restart stock-api.service",
    "does not enable",
    "does not run migrations",
    "does not write to production DB",
    "does not trade",
    "production edge",
]

_REQUIRED_JSON_FIELDS = (
    "phase", "generated_at", "inputs_read", "data_root",
    "upstream_phase2k_h_summary", "dataset_summary", "survivorship_caveat_summary",
    "feature_engineering_summary", "residualization_config", "candidate",
    "retest_metrics", "walk_forward_metrics", "regime_metrics", "stability_metrics",
    "pass_fail_gates", "recommendation", "interpretation", "recommended_next_phase",
)
_REQUIRED_FALSE = (
    "database_touched", "database_write_executed", "migration_executed",
    "deployment_executed", "model_v2_enabled", "production_edge_claimed",
)
_REQUIRED_TRUE = ("no_trading", "no_orders", "no_automation")

_ALLOWED_RECOMMENDATIONS = (
    "EXPANDED_RETEST_PASS", "EXPANDED_RETEST_FAIL", "NEED_MORE_DATA_OR_PIT_UNIVERSE")


def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _import_analyzer():
    spec = importlib.util.spec_from_file_location("phase2k_i_analyzer_test", _ANALYZER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _abs(rel: str) -> str:
    return os.path.join(_REPO_ROOT, rel)


def _assert_no_repo_large_outputs():
    for rel in _C_OUTPUT_FILES:
        assert not os.path.isfile(_abs(rel)), \
            f"large output must not exist under the C: repo: {rel}"


def _write_json(path: str, obj) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f)


def _make_2kh_summary(*, ready: bool) -> dict:
    if ready:
        return {
            "phase": "2K-H",
            "data_root": _D_DATA_ROOT_NORM,
            "decision": {"manual_build_detected": True, "ready_for_retest": True},
            "recommended_next_phase": {"phase": "2K-I"},
            "data_quality_summary": {"status": "PASS_WITH_CAVEAT", "passed": True,
                                     "row_count": 99999, "ticker_count": 30},
            "survivorship_caveat_summary": {
                "present": True, "membership_basis": "current_as_of",
                "point_in_time_membership_claimed": False,
                "reported_as_survivorship_biased": True},
        }
    return {
        "phase": "2K-H",
        "data_root": _D_DATA_ROOT_NORM,
        "decision": {"manual_build_detected": True, "ready_for_retest": False},
        "recommended_next_phase": {"phase": "2K-H-RUN"},
        "data_quality_summary": {"status": "FAIL", "passed": False},
        "survivorship_caveat_summary": {
            "present": True, "point_in_time_membership_claimed": False},
    }


def _make_ready_dataset(tmp: str, *, n_tickers: int = 25, n_days: int = 560) -> dict:
    """Write a small synthetic expanded build into <tmp>/output + a ready 2K-H summary.

    Uses numpy/pandas (available in the GCP venv) to fabricate a deterministic adjusted
    OHLCV panel for n_tickers names plus SPY. This is a local fixture only — no network,
    no real D: drive.
    """
    import numpy as np
    import pandas as pd

    rng = np.random.RandomState(7)
    dates = pd.bdate_range("2022-06-01", periods=n_days)
    tickers = [f"T{i:03d}" for i in range(n_tickers)] + ["SPY"]
    rows = []
    spy_ret = rng.normal(0.0004, 0.01, size=n_days)
    spy_close = 100.0 * np.cumprod(1.0 + spy_ret)
    for t in tickers:
        if t == "SPY":
            close = spy_close
        else:
            beta = rng.uniform(0.6, 1.4)
            idio = rng.normal(0.0, 0.012, size=n_days)
            ret = beta * spy_ret + idio
            close = float(rng.uniform(20, 200)) * np.cumprod(1.0 + ret)
        vol = rng.uniform(1e6, 5e7, size=n_days)
        for d, c, v in zip(dates, close, vol):
            rows.append({
                "ticker": t, "date": d.date().isoformat(),
                "adjusted_open": round(c * 0.999, 4),
                "adjusted_high": round(c * 1.01, 4),
                "adjusted_low": round(c * 0.99, 4),
                "adjusted_close": round(float(c), 4),
                "volume": int(v),
            })
    df = pd.DataFrame(rows)
    out_dir = os.path.join(tmp, "output")
    os.makedirs(out_dir, exist_ok=True)
    price_path = os.path.join(out_dir, "phase2k_g_expanded_price_history_free.csv")
    df.to_csv(price_path, index=False)

    _write_json(os.path.join(out_dir, "phase2k_g_data_quality_report.json"), {
        "status": "PASS_WITH_CAVEAT",
        "checks": [{"check_id": "survivorship_caveat_present", "severity": "caveat",
                    "passed": True}],
        "stats": {"row_count": len(df), "ticker_count": len(tickers),
                  "date_count": n_days, "years_span": round(n_days / 252.0, 3),
                  "min_tickers_per_date": len(tickers),
                  "benchmark_coverage_fraction": 1.0},
    })
    _write_json(os.path.join(out_dir, "phase2k_g_data_build_summary.json"), {
        "phase": "2K-G", "mode": "execute", "source": "yfinance",
        "row_count": len(df), "ticker_count": len(tickers), "benchmark": "SPY",
        "date_start": dates[0].date().isoformat(),
        "date_end": dates[-1].date().isoformat(),
        "years_span": round(n_days / 252.0, 3),
        "data_quality_status": "PASS_WITH_CAVEAT",
        "model_trained": False, "model_candidate_created": False,
    })
    _write_json(os.path.join(out_dir, "phase2k_g_survivorship_caveat.json"), {
        "membership_basis": "current_as_of",
        "point_in_time_membership_claimed": False,
        "reported_as_survivorship_biased": True,
        "clean_point_in_time_build_deferred": True,
        "universe_ticker_count": len(tickers),
    })
    upstream_path = os.path.join(tmp, "phase2k_h_summary.json")
    _write_json(upstream_path, _make_2kh_summary(ready=True))
    return {"data_root": tmp, "upstream_2kh_path": upstream_path,
            "price_path": price_path}


def _make_prices_df(*, n_tickers: int = 8, n_days: int = 90):
    """A small, deterministic adjusted-OHLCV panel (incl. SPY) as a DataFrame.

    Shaped like the output of ``_read_price_history`` so it can be fed straight into
    ``compute_features`` without touching disk or the network.
    """
    import numpy as np
    import pandas as pd

    rng = np.random.RandomState(11)
    dates = pd.bdate_range("2022-06-01", periods=n_days)
    tickers = [f"T{i:03d}" for i in range(n_tickers)] + ["SPY"]
    spy_ret = rng.normal(0.0004, 0.01, size=n_days)
    spy_close = 100.0 * np.cumprod(1.0 + spy_ret)
    rows = []
    for t in tickers:
        if t == "SPY":
            close = spy_close
        else:
            ret = rng.uniform(0.6, 1.4) * spy_ret + rng.normal(0.0, 0.012, size=n_days)
            close = float(rng.uniform(20, 200)) * np.cumprod(1.0 + ret)
        vol = rng.uniform(1e6, 5e7, size=n_days)
        for d, c, v in zip(dates, close, vol):
            rows.append({
                "ticker": t, "date": d,
                "adjusted_open": float(c) * 0.999, "adjusted_high": float(c) * 1.01,
                "adjusted_low": float(c) * 0.99, "adjusted_close": float(c),
                "volume": float(v),
            })
    return pd.DataFrame(rows)


def _run(tmp_out_dir, **kwargs):
    analyzer = _import_analyzer()
    out = os.path.join(tmp_out_dir, "phase2k_i.json")
    res = analyzer.run(output_path=out, **kwargs)
    return res, out


# --------------------------------------------------------------------------- #
# 1. Analyzer compiles and imports
# --------------------------------------------------------------------------- #
def test_analyzer_compiles():
    compile(_read(_ANALYZER), _ANALYZER, "exec")


def test_analyzer_imports():
    _import_analyzer()


# --------------------------------------------------------------------------- #
# 2. Expected D: input paths and upstream summaries are referenced
# --------------------------------------------------------------------------- #
def test_references_expected_inputs():
    a = _import_analyzer()
    assert a.DATA_ROOT.replace("\\", "/") == _D_DATA_ROOT_NORM
    for const in (a.PRICE_HISTORY_CSV, a.SCORED_CSV, a.DATA_QUALITY_JSON,
                  a.BUILD_SUMMARY_JSON, a.SURVIVORSHIP_JSON):
        assert const.replace("\\", "/").startswith(_D_DATA_ROOT_NORM + "/output/"), \
            f"expected D: path not on data root: {const}"
    assert a.UPSTREAM_2KH_JSON.replace("\\", "/").endswith(
        "research/output/phase2k_h_manual_build_run_summary.json")
    assert a.INPUT_2KC_JSON.replace("\\", "/").endswith(
        "research/output/phase2k_c_residual_robustness.json")
    assert a.INPUT_2KB_JSON.replace("\\", "/").endswith(
        "research/output/phase2k_b_residual_signal.json")
    assert a.INPUT_2LB_JSON.replace("\\", "/").endswith(
        "research/output/phase2l_b_walk_forward_validation.json")
    assert a.RESULTS_JSON.replace("\\", "/").endswith(
        "research/output/phase2k_i_expanded_residual_retest.json")


# --------------------------------------------------------------------------- #
# 3. Required candidate is avg_dollar_volume_21d
# --------------------------------------------------------------------------- #
def test_candidate_is_avg_dollar_volume_21d():
    a = _import_analyzer()
    assert a.CANDIDATE == "avg_dollar_volume_21d"
    assert a.PRIMARY_HORIZON == 63


# --------------------------------------------------------------------------- #
# 4. No api_server / Paper Trader import
# --------------------------------------------------------------------------- #
def test_no_api_server_import():
    text = _read(_ANALYZER)
    assert "import api_server" not in text
    assert "from api_server" not in text
    for node in ast.walk(ast.parse(text)):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            mods = ([n.name for n in node.names]
                    + ([node.module] if isinstance(node, ast.ImportFrom)
                       and node.module else []))
            assert not any((m or "").split(".")[0] == "api_server" for m in mods)


def test_no_paper_trader_import():
    assert "paper_trader" not in _read(_ANALYZER).lower()


# --------------------------------------------------------------------------- #
# 5. No forbidden infrastructure / model / network / mutation tokens
# --------------------------------------------------------------------------- #
def test_no_forbidden_tokens():
    text = _read(_ANALYZER)
    low = text.lower()
    hits = [tok for tok in _FORBIDDEN_TOKENS if tok.lower() in low]
    assert not hits, f"analyzer has forbidden token(s): {hits}"
    for tok in _FORBIDDEN_EXTRA:
        assert tok not in low, f"analyzer has forbidden token: {tok!r}"
    model_hits = [tok for tok in _FORBIDDEN_MODEL_TOKENS if tok in text]
    assert not model_hits, f"analyzer has model token(s): {model_hits}"
    net = [tok for tok in _FORBIDDEN_NETWORK_TOKENS if tok.lower() in low]
    assert not net, f"analyzer has network token(s): {net}"
    mut = [tok for tok in _FORBIDDEN_MUTATION_TOKENS if tok.lower() in low]
    assert not mut, f"analyzer has D:-mutation token(s): {mut}"


# --------------------------------------------------------------------------- #
# 6. Exactly one write-open (the small summary JSON); all others read-only
# --------------------------------------------------------------------------- #
def test_single_write_open():
    text = _read(_ANALYZER)
    write_opens = 0
    for node in ast.walk(ast.parse(text)):
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "open":
            if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant) \
                    and "w" in str(node.args[1].value):
                write_opens += 1
    assert write_opens == 1, "analyzer must have exactly one write-open (summary JSON)"


# --------------------------------------------------------------------------- #
# 7. Not-ready Phase 2K-H -> NEED_MORE_DATA_OR_PIT_UNIVERSE, routes to 2K-J
# --------------------------------------------------------------------------- #
def test_refuses_when_2kh_not_ready():
    tmp = tempfile.mkdtemp(prefix="phase2k_i_notready_")
    upstream = os.path.join(tmp, "phase2k_h_summary.json")
    _write_json(upstream, _make_2kh_summary(ready=False))
    res, on_disk = _run(tmp, data_root=os.path.join(tmp, "empty_root"),
                        upstream_2kh_path=upstream)
    rec = res["recommendation"]
    assert rec["recommendation"] == "NEED_MORE_DATA_OR_PIT_UNIVERSE"
    assert rec["enough_data"] is False
    assert res["upstream_phase2k_h_summary"]["ready_for_2k_i"] is False
    assert res["feature_engineering_summary"]["computed"] is False
    assert res["recommended_next_phase"]["phase"] == "2K-J"
    assert os.path.isfile(on_disk)
    _assert_no_repo_large_outputs()


def test_refuses_when_2kh_missing():
    tmp = tempfile.mkdtemp(prefix="phase2k_i_missing_")
    res, _ = _run(tmp, data_root=os.path.join(tmp, "empty_root"),
                  upstream_2kh_path=os.path.join(tmp, "does_not_exist.json"))
    assert res["recommendation"]["recommendation"] == "NEED_MORE_DATA_OR_PIT_UNIVERSE"
    assert res["upstream_phase2k_h_summary"]["present"] is False
    assert res["recommended_next_phase"]["phase"] == "2K-J"


# --------------------------------------------------------------------------- #
# 8. Output JSON: required fields and safety flags (not-ready run)
# --------------------------------------------------------------------------- #
def test_output_json_required_fields():
    tmp = tempfile.mkdtemp(prefix="phase2k_i_fields_")
    upstream = os.path.join(tmp, "phase2k_h_summary.json")
    _write_json(upstream, _make_2kh_summary(ready=False))
    res, on_disk = _run(tmp, data_root=os.path.join(tmp, "empty_root"),
                        upstream_2kh_path=upstream)
    on_disk_obj = json.loads(_read(on_disk))
    for d in (res, on_disk_obj):
        for k in _REQUIRED_JSON_FIELDS:
            assert k in d, f"summary JSON missing field: {k}"
        assert d["phase"] == "2K-I"
        for k in _REQUIRED_FALSE:
            assert d[k] is False, f"{k} must be false"
        for k in _REQUIRED_TRUE:
            assert d[k] is True, f"{k} must be true"


# --------------------------------------------------------------------------- #
# 9. Reads the Phase 2K-H summary and surfaces its readiness fields
# --------------------------------------------------------------------------- #
def test_reads_phase2k_h_summary():
    tmp = tempfile.mkdtemp(prefix="phase2k_i_reads2kh_")
    upstream = os.path.join(tmp, "phase2k_h_summary.json")
    _write_json(upstream, _make_2kh_summary(ready=True))
    res, _ = _run(tmp, data_root=os.path.join(tmp, "empty_root"),
                  upstream_2kh_path=upstream)
    up = res["upstream_phase2k_h_summary"]
    assert up["present"] is True
    assert up["manual_build_detected"] is True
    assert up["ready_for_retest"] is True
    assert up["recommended_next_phase"] == "2K-I"
    assert up["point_in_time_membership_claimed"] is False
    # Ready upstream but no expanded CSV present -> still NEED_MORE (no data to compute).
    assert res["recommendation"]["recommendation"] == "NEED_MORE_DATA_OR_PIT_UNIVERSE"


# --------------------------------------------------------------------------- #
# 10. Recommendation is always one of the allowed values, next phase is 2K-J
# --------------------------------------------------------------------------- #
def test_recommendation_allowed_and_routes_to_2k_j():
    tmp_a = tempfile.mkdtemp(prefix="phase2k_i_rec_a_")
    up_a = os.path.join(tmp_a, "s.json")
    _write_json(up_a, _make_2kh_summary(ready=False))
    res_a, _ = _run(tmp_a, data_root=os.path.join(tmp_a, "er"), upstream_2kh_path=up_a)

    tmp_b = tempfile.mkdtemp(prefix="phase2k_i_rec_b_")
    fx = _make_ready_dataset(tmp_b)
    res_b, _ = _run(tmp_b, data_root=fx["data_root"],
                    upstream_2kh_path=fx["upstream_2kh_path"])
    for res in (res_a, res_b):
        assert res["recommendation"]["recommendation"] in _ALLOWED_RECOMMENDATIONS
        assert res["recommendation"]["allowed_values"] == list(_ALLOWED_RECOMMENDATIONS)
        assert res["recommended_next_phase"]["phase"] == "2K-J"


# --------------------------------------------------------------------------- #
# 11. Output JSON carries the survivorship caveat and the decision safety block
# --------------------------------------------------------------------------- #
def test_output_carries_survivorship_caveat():
    tmp = tempfile.mkdtemp(prefix="phase2k_i_surv_")
    fx = _make_ready_dataset(tmp)
    res, _ = _run(tmp, data_root=fx["data_root"],
                  upstream_2kh_path=fx["upstream_2kh_path"])
    sc = res["survivorship_caveat_summary"]
    assert sc["present"] is True
    assert sc["point_in_time_membership_claimed"] is False
    assert sc["all_results_must_be_reported_survivorship_biased"] is True
    rec = res["recommendation"]
    assert rec["results_are_survivorship_biased"] is True
    assert rec["create_model_candidate_now"] is False
    assert rec["train_model_now"] is False
    assert rec["deploy_now"] is False
    assert rec["production_edge_claimed"] is False
    assert res["interpretation"]["results_are_survivorship_biased"] is True


# --------------------------------------------------------------------------- #
# 12. Full compute path: features computed, candidate carried, valid verdict
# --------------------------------------------------------------------------- #
def test_full_compute_path_runs():
    tmp = tempfile.mkdtemp(prefix="phase2k_i_compute_")
    fx = _make_ready_dataset(tmp)
    res, on_disk = _run(tmp, data_root=fx["data_root"],
                        upstream_2kh_path=fx["upstream_2kh_path"])
    fe = res["feature_engineering_summary"]
    assert fe["computed"] is True
    assert "avg_dollar_volume_21d" in fe["computed_features"]
    assert fe["all_features_trailing_point_in_time"] is True
    assert fe["labels_forward_filled"] is False
    assert res["candidate"]["feature"] == "avg_dollar_volume_21d"
    # The primary horizon residual metrics block exists and is finite-or-None.
    by_h = res["retest_metrics"]["by_horizon"]
    assert "63" in by_h and "21" in by_h
    assert by_h["63"]["horizon_days"] == 63
    assert res["recommendation"]["recommendation"] in _ALLOWED_RECOMMENDATIONS
    assert res["recommended_next_phase"]["phase"] == "2K-J"
    # The on-disk JSON is serializable with allow_nan=False (no NaN/inf leaked).
    json.loads(_read(on_disk))
    _assert_no_repo_large_outputs()


# --------------------------------------------------------------------------- #
# 13. Default run targets the D: data root and writes only the small summary
# --------------------------------------------------------------------------- #
def test_default_paths_target_d_drive():
    a = _import_analyzer()
    paths = a._resolve_paths(a.DATA_ROOT)
    for key in ("price_history_csv", "scored_csv", "data_quality_report_json",
                "data_build_summary_json", "survivorship_caveat_json"):
        assert paths[key].replace("\\", "/").startswith(_D_DATA_ROOT_NORM + "/output/")
    _assert_no_repo_large_outputs()


# --------------------------------------------------------------------------- #
# 14. Doc has all required guardrail phrases
# --------------------------------------------------------------------------- #
def test_doc_has_required_phrases():
    text = _read(_DOC)
    missing = [p for p in _REQUIRED_DOC_PHRASES if p not in text]
    assert not missing, f"doc missing required phrase(s): {missing}"


# --------------------------------------------------------------------------- #
# 15. compute_features keeps `ticker`/`date` as columns (groupby/apply regression)
# --------------------------------------------------------------------------- #
def test_compute_features_returns_ticker_and_date_columns():
    a = _import_analyzer()
    feats = a.compute_features(_make_prices_df())
    # Regression for ``KeyError: ['ticker'] not in index``: the grouping key must
    # survive groupby/apply as an ordinary column, never be pushed into the index.
    assert a.TICKER_COL in feats.columns
    assert a.DATE_COL in feats.columns
    for col in (a.CANDIDATE, "daily_return", "spy_return", "dollar_volume",
                "adjusted_close", "volume", *a.CONTROL_FEATURES):
        assert col in feats.columns, f"compute_features dropped column: {col}"
    assert not feats.columns.duplicated().any(), "compute_features produced dup columns"
    # The exact selection build_results performs must not raise.
    feat_cols = [a.TICKER_COL, a.DATE_COL, a.CANDIDATE] + list(a.CONTROL_FEATURES)
    _ = feats[feat_cols]


def test_ensure_date_ticker_columns_recovers_index():
    a = _import_analyzer()
    import pandas as pd
    df = pd.DataFrame(
        {"ticker": ["AAA", "BBB"], "date": ["2022-01-03", "2022-01-04"],
         "x": [1.0, 2.0]}).set_index("ticker")
    fixed = a._ensure_date_ticker_columns(df)
    assert a.TICKER_COL in fixed.columns and a.DATE_COL in fixed.columns
    assert "x" in fixed.columns
    assert not fixed.columns.duplicated().any()


# --------------------------------------------------------------------------- #
# 16. empty_walk_forward_metrics + build_interpretation never KeyError
# --------------------------------------------------------------------------- #
def test_empty_walk_forward_metrics_and_interpretation():
    a = _import_analyzer()
    wf = a.empty_walk_forward_metrics()
    for k in ("config", "n_folds", "pooled_n_validation_dates",
              "total_effective_validation_obs", "mean_validation_residual_rank_ic",
              "pooled_validation_mean_residual_rank_ic",
              "median_validation_residual_rank_ic", "n_validation_windows_same_sign",
              "n_validation_windows_positive", "frac_validation_windows_same_sign",
              "majority_validation_windows_same_sign", "bootstrap_pooled_ic_ci",
              "leave_one_fold_out", "fold_metrics"):
        assert k in wf, f"empty_walk_forward_metrics missing key: {k}"
    rec = {"recommendation": a.REC_NEED}
    interp = a.build_interpretation(rec, {"mean_residual_rank_ic": None}, wf)
    assert interp["candidate"] == a.CANDIDATE
    assert interp["pooled_validation_mean_residual_rank_ic"] is None
    # Defensive even against a truly minimal walk-forward dict (no pooled key at all).
    interp2 = a.build_interpretation(rec, {}, {})
    assert interp2["pooled_validation_mean_residual_rank_ic"] is None
    assert interp2["primary_mean_residual_rank_ic"] is None


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
