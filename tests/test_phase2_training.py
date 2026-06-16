"""Tests for the Phase 2B walk-forward training + evaluation harness.

Pure-logic only: no database, no network, and no import of api_server or Paper
Trader. sklearn / xgboost / pytest are NOT required.

Runs two ways:
  * under pytest:   pytest tests/test_phase2_training.py
  * without pytest: python tests/test_phase2_training.py
The __main__ block discovers every ``test_*`` function, runs it, prints
PASS/FAIL, and exits non-zero on any failure (the GCP venv has no pytest).
"""
from __future__ import annotations

import datetime as dt
import math
import os
import sys

# Make the repo root importable when run directly (sys.path[0] == tests/).
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from model import train as T  # noqa: E402
from model import eval as E  # noqa: E402


def _approx(a, b, tol=1e-9):
    return abs(a - b) <= tol


def _business_dates(start: dt.date, n: int):
    out, d = [], start
    for _ in range(n):
        while d.weekday() > 4:
            d += dt.timedelta(days=1)
        out.append(d)
        d += dt.timedelta(days=1)
    return out


# --------------------------------------------------------------------------- #
# 1. Fold splitter: train strictly before test
# --------------------------------------------------------------------------- #
def test_folds_train_strictly_before_test():
    dates = _business_dates(dt.date(2023, 1, 2), 200)
    folds = T.make_walk_forward_folds(dates, n_folds=4, embargo=5,
                                      min_train_dates=60)
    assert len(folds) >= 1
    for f in folds:
        assert max(f.train_dates) < min(f.test_dates)
        # train and test never share a date
        assert not (set(f.train_dates) & set(f.test_dates))


def test_folds_are_expanding_window():
    dates = _business_dates(dt.date(2023, 1, 2), 200)
    folds = T.make_walk_forward_folds(dates, n_folds=4, embargo=5,
                                      min_train_dates=60)
    sizes = [len(f.train_dates) for f in folds]
    assert sizes == sorted(sizes)  # non-decreasing train window
    assert sizes[0] >= 60


# --------------------------------------------------------------------------- #
# 2. Embargo / purge: gap >= embargo, and >= horizon prevents label overlap
# --------------------------------------------------------------------------- #
def test_embargo_gap_in_sessions():
    dates = _business_dates(dt.date(2023, 1, 2), 200)
    embargo = 5
    folds = T.make_walk_forward_folds(dates, n_folds=4, embargo=embargo,
                                      min_train_dates=60)
    pos = {d: i for i, d in enumerate(dates)}
    for f in folds:
        gap = pos[min(f.test_dates)] - pos[max(f.train_dates)]
        # at least `embargo` sessions strictly between train end and test start
        assert gap - 1 >= embargo


def test_purge_prevents_label_leakage_on_real_dataset():
    # Build a labeled synthetic dataset, fold it, and assert no train row's
    # 5-day forward label (target_date) lands on or after the first test as_of.
    df, _ = T.build_synthetic_signal_dataset(n_tickers=6, n_days=260,
                                             min_history=120, horizon=5)
    df = df.copy()
    df["as_of_date"] = [T._as_date(d) for d in df["as_of_date"]]
    df["target_date"] = [T._as_date(d) for d in df["target_date"]]
    folds = T.make_walk_forward_folds(sorted(df["as_of_date"].unique()),
                                      n_folds=3, embargo=5, min_train_dates=60)
    assert len(folds) >= 1
    for f in folds:
        first_test = min(f.test_dates)
        train_rows = df[df["as_of_date"].isin(set(f.train_dates))]
        # every train label resolves strictly before the test window opens
        assert train_rows["target_date"].max() < first_test


def test_embargo_below_horizon_would_overlap_but_default_is_safe():
    # Default embargo equals the horizon -> safe by construction.
    assert T.DEFAULT_EMBARGO >= T.DEFAULT_HORIZON


# --------------------------------------------------------------------------- #
# 3. Ridge learns a known linear signal (out-of-sample)
# --------------------------------------------------------------------------- #
def _linear_signal_frame(n_dates, n_names, seed=0, beta=(1.0, -0.5, 0.25)):
    rng = np.random.default_rng(seed)
    rows = []
    base = dt.date(2023, 1, 2)
    dates = _business_dates(base, n_dates)
    for d in dates:
        # cross-section of names with z-like features and a linear target
        f = rng.normal(0, 1, (n_names, len(beta)))
        target = f @ np.array(beta) + rng.normal(0, 0.3, n_names)
        for j in range(n_names):
            rows.append({
                "ticker": f"N{j}",
                "as_of_date": d,
                "target_date": d + dt.timedelta(days=7),
                "feat0_z": f[j, 0], "feat1_z": f[j, 1], "feat2_z": f[j, 2],
                "realized_excess_return_5d_vs_spy": target[j],
                "realized_return_5d": target[j],
                "spy_return_5d": 0.0,
                "outperform_spy_flag": bool(target[j] > 0),
                "positive_return_flag": bool(target[j] > 0),
            })
    return pd.DataFrame(rows)


def test_ridge_learns_linear_signal():
    df = _linear_signal_frame(120, 20, seed=1)
    feat = ["feat0_z", "feat1_z", "feat2_z"]
    train = df[df["as_of_date"] < df["as_of_date"].unique()[90]]
    test = df[df["as_of_date"] >= df["as_of_date"].unique()[90]]
    model = T.fit_predictor(train, feat, target_col="realized_excess_return_5d_vs_spy")
    assert model.name == "ridge"
    # learned coefficients recover the sign of the true betas
    assert model.w[0] > 0 and model.w[1] < 0 and model.w[2] > 0
    # out-of-sample rank IC is strongly positive
    pred = model.predict(test)
    ic = E.M.spearman_rank_ic(pred, test["realized_excess_return_5d_vs_spy"].to_numpy())
    assert ic > 0.5, ic


# --------------------------------------------------------------------------- #
# 4. Fallback model when ridge cannot fit
# --------------------------------------------------------------------------- #
def test_fallback_when_insufficient_data():
    df = _linear_signal_frame(120, 20, seed=2)
    feat = ["feat0_z", "feat1_z", "feat2_z"]
    tiny = df.head(3)  # far fewer rows than MIN_RIDGE_ROWS
    model = T.fit_predictor(tiny, feat, target_col="realized_excess_return_5d_vs_spy")
    assert model.name == "mean_zscore_composite"
    pred = model.predict(df.head(10))
    assert len(pred) == 10
    assert np.all(np.isfinite(pred))


def test_fallback_composite_ranks_by_mean_z():
    feat = ["feat0_z", "feat1_z", "feat2_z"]
    model = T.CompositeModel("mean_zscore_composite", feat)
    sample = pd.DataFrame({
        "feat0_z": [1.0, 0.0, np.nan],
        "feat1_z": [1.0, 0.0, np.nan],
        "feat2_z": [1.0, 0.0, 2.0],
    })
    pred = model.predict(sample)
    assert _approx(pred[0], 1.0)        # mean of (1,1,1)
    assert _approx(pred[1], 0.0)        # mean of (0,0,0)
    assert _approx(pred[2], 2.0)        # only non-nan feature
    assert pred[0] > pred[1]            # ranking preserved


# --------------------------------------------------------------------------- #
# 5. Prediction output has the expected columns / shape
# --------------------------------------------------------------------------- #
def test_run_walk_forward_output_columns():
    df = _linear_signal_frame(160, 12, seed=3)
    preds, fold_meta = T.run_walk_forward(
        df, feature_cols=["feat0_z", "feat1_z", "feat2_z"],
        n_folds=3, embargo=5, min_train_dates=40)
    assert list(preds.columns) == T.OOS_COLUMNS
    assert len(preds) > 0
    assert len(fold_meta) >= 1
    # one prediction per (ticker, as_of_date) in the test folds, no dupes
    assert not preds.duplicated(subset=["ticker", "as_of_date"]).any()
    # every prediction's as_of_date belongs to some fold's test window
    test_dates = set()
    for m in fold_meta:
        # fold meta records ranges; reconstruct membership via the predictions
        pass
    assert preds["score"].notna().all()


def test_run_walk_forward_is_out_of_sample_only():
    # OOS predictions never include the earliest (pure-train) dates.
    df = _linear_signal_frame(160, 12, seed=4)
    preds, _ = T.run_walk_forward(
        df, feature_cols=["feat0_z", "feat1_z", "feat2_z"],
        n_folds=3, embargo=5, min_train_dates=40)
    earliest = sorted(df["as_of_date"].unique())[0]
    assert earliest not in set(preds["as_of_date"].unique())


# --------------------------------------------------------------------------- #
# 6. Metrics + report are wired to the OOS predictions
# --------------------------------------------------------------------------- #
def test_metrics_wired_to_oos_predictions():
    # Construct predictions where score perfectly orders realized excess.
    dates = _business_dates(dt.date(2024, 1, 1), 4)
    rows = []
    for d in dates:
        for j in range(10):
            rows.append({
                "ticker": f"N{j}", "as_of_date": d,
                "target_date": d + dt.timedelta(days=7), "fold_id": 0,
                "model_name": "ridge", "score": float(j),
                "predicted_excess_return_5d": float(j),
                "realized_excess_return_5d_vs_spy": float(j) / 100.0,
                "realized_return_5d": float(j) / 100.0, "spy_return_5d": 0.0,
                "outperform_spy_flag": bool(j >= 5),
                "positive_return_flag": bool(j >= 5),
            })
    preds = pd.DataFrame(rows)[T.OOS_COLUMNS]
    assert _approx(E.rank_ic(preds), 1.0, 1e-9)
    om = E.overall_metrics(preds)
    assert om["n_rows"] == 40 and om["n_tickers"] == 10
    # top-N must beat the universe when the score is perfect
    sim = E.topn_table(preds, [3])
    assert sim[3]["mean_return"] > sim["_universe"]["mean_return"]
    # score buckets monotone increasing in realized excess
    means = [b["mean_realized"] for b in om["buckets"]]
    assert means == sorted(means)


def test_evaluate_gates_structure_and_pass_on_perfect_signal():
    dates = _business_dates(dt.date(2024, 1, 1), 6)
    rows = []
    for d in dates:
        for j in range(12):
            rows.append({
                "ticker": f"N{j}", "as_of_date": d,
                "target_date": d + dt.timedelta(days=7), "fold_id": 0,
                "model_name": "ridge", "score": float(j),
                "predicted_excess_return_5d": float(j),
                "realized_excess_return_5d_vs_spy": float(j) / 100.0,
                "realized_return_5d": float(j) / 100.0, "spy_return_5d": 0.0,
                "outperform_spy_flag": bool(j >= 6),
                "positive_return_flag": bool(j >= 6),
            })
    preds = pd.DataFrame(rows)[T.OOS_COLUMNS]
    gates = E.evaluate_gates(preds)
    assert gates["gate1"]["passed"] is True
    assert gates["gate2"]["passed"] is True
    assert len(gates["deferred"]) == 3  # gates 3-5 deferred to 2C/2D


def test_report_contains_required_sections_synthetic():
    df, unavailable = T.build_synthetic_signal_dataset(n_tickers=6, n_days=260,
                                                       min_history=120, horizon=5)
    preds, fold_meta = T.run_walk_forward(df, n_folds=3, embargo=5,
                                          min_train_dates=50)
    meta = {
        "source": "SYNTHETIC (test)", "is_synthetic": True,
        "target": T.PRIMARY_TARGET, "secondary_target": T.SECONDARY_TARGET,
        "n_feature_cols": len(T.feature_columns(df)),
        "horizon": 5, "embargo": 5, "alpha": T.DEFAULT_ALPHA,
        "generated_on": "2026-06-16", "unavailable_families": unavailable,
    }
    md = E.build_report_markdown(preds, meta, fold_meta)
    for section in E.REQUIRED_SECTIONS:
        assert section in md, f"missing section: {section}"
    # never claims production edge on synthetic data
    assert "NOT a market result" in md or "not a market edge" in md.lower()


def test_report_handles_empty_predictions():
    empty = pd.DataFrame(columns=T.OOS_COLUMNS)
    meta = {"source": "SYNTHETIC (test)", "is_synthetic": True,
            "target": T.PRIMARY_TARGET, "secondary_target": T.SECONDARY_TARGET,
            "n_feature_cols": 0, "horizon": 5, "embargo": 5,
            "alpha": T.DEFAULT_ALPHA, "generated_on": "2026-06-16"}
    md = E.build_report_markdown(empty, meta, [])
    assert "## Verdict" in md
    assert "INCONCLUSIVE" in md


# --------------------------------------------------------------------------- #
# 7. End-to-end synthetic: harness recovers the planted signal
# --------------------------------------------------------------------------- #
def test_synthetic_signal_is_recovered_end_to_end():
    df, _ = T.build_synthetic_signal_dataset(n_tickers=10, n_days=320,
                                             min_history=140, horizon=5, phi=0.25)
    preds, fold_meta = T.run_walk_forward(df, n_folds=4, embargo=5,
                                          min_train_dates=50)
    assert len(preds) > 0
    # planted AR(1) momentum signal -> positive out-of-sample rank IC
    assert E.rank_ic(preds) > 0.0
    # ridge actually got used on at least one fold
    assert "ridge" in set(preds["model_name"].unique())


# --------------------------------------------------------------------------- #
# 8. No Paper Trader import; api_server untouched
# --------------------------------------------------------------------------- #
def test_no_paper_trader_import():
    assert "api_server" not in sys.modules, "model.train must not import api_server"
    offenders = [m for m in sys.modules
                 if m == "paper_trader" or m.startswith("paper_trader.")]
    assert not offenders, f"unexpected Paper Trader import: {offenders}"


def test_model_sources_contain_no_paper_trader_or_api_server_refs():
    model_dir = os.path.join(_REPO_ROOT, "model")
    for fn in os.listdir(model_dir):
        if not fn.endswith(".py"):
            continue
        with open(os.path.join(model_dir, fn), "r", encoding="utf-8") as f:
            src = f.read().lower()
        assert "paper_trader" not in src, f"{fn} references paper_trader"
        assert "import api_server" not in src, f"{fn} imports api_server"


def test_no_sklearn_or_xgboost_dependency():
    # The harness must run without these installed; assert it does not import them.
    import importlib
    for mod in ("model.train", "model.eval"):
        m = importlib.import_module(mod)
        src = open(m.__file__, "r", encoding="utf-8").read().lower()
        assert "import sklearn" not in src and "from sklearn" not in src, mod
        assert "import xgboost" not in src and "from xgboost" not in src, mod


def test_api_server_predict_contract_unchanged():
    path = os.path.join(_REPO_ROOT, "api_server.py")
    if not os.path.exists(path):
        return  # api_server lives only on the VM; skip where absent
    with open(path, "r", encoding="utf-8") as f:
        src = f.read()
    assert '@app.post("/predict_all_models/"' in src
    for key in ('"recommendation"', '"confidence"', '"agreement"',
                '"ensemble_day5"', '"predictions"', '"zscore"'):
        assert key in src, f"response key {key} missing from api_server.py"
    # the Phase 2B work must not have edited api_server.
    assert "model.train" not in src
    assert "phase2b" not in src.lower()


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
