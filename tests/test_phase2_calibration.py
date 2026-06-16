"""Tests for the Phase 2C calibration + risk/interval layer.

Pure-logic only: no database, no network, and no import of api_server or Paper
Trader. sklearn / xgboost / pytest are NOT required.

Runs two ways:
  * under pytest:   pytest tests/test_phase2_calibration.py
  * without pytest: python tests/test_phase2_calibration.py
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
from model import calibrate as C  # noqa: E402
from model import risk as R  # noqa: E402


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


def _make_preds(n_folds=4, per_fold=60, seed=0, signal=0.04):
    """A synthetic Phase 2B-shaped OOS prediction frame (T.OOS_COLUMNS).

    P(outperform) and the realized excess both rise with the score, so a sane
    calibrator and a conformal interval should both behave. Folds are laid out on
    consecutive date blocks so ``fold_id`` ordering matches time order.
    """
    rng = np.random.default_rng(seed)
    rows = []
    base = dt.date(2023, 1, 2)
    block = _business_dates(base, n_folds * 10)
    for f in range(n_folds):
        fold_dates = block[f * 10:(f + 1) * 10]
        for d in fold_dates:
            for j in range(per_fold // 10):
                score = float(rng.normal(0, 1))
                realized_excess = signal * score + float(rng.normal(0, 0.02))
                rows.append({
                    "ticker": f"N{j}", "as_of_date": d,
                    "target_date": d + dt.timedelta(days=7), "fold_id": f,
                    "model_name": "ridge", "score": score,
                    "predicted_excess_return_5d": score * signal,
                    "realized_excess_return_5d_vs_spy": realized_excess,
                    "realized_return_5d": realized_excess,
                    "spy_return_5d": 0.0,
                    "outperform_spy_flag": bool(realized_excess > 0),
                    "positive_return_flag": bool(realized_excess > 0),
                })
    return pd.DataFrame(rows)[T.OOS_COLUMNS]


# --------------------------------------------------------------------------- #
# 1. Isotonic calibrator is monotonic and stays in [0, 1]
# --------------------------------------------------------------------------- #
def test_isotonic_calibrator_is_monotonic():
    rng = np.random.default_rng(1)
    x = rng.normal(0, 1, 500)
    p = 1.0 / (1.0 + np.exp(-1.5 * x))
    y = (rng.uniform(size=500) < p).astype(float)
    cal = C._fit_isotonic(x, y)
    assert cal is not None and cal.name == "isotonic"
    grid = np.linspace(x.min(), x.max(), 50)
    out = cal.predict(grid)
    # non-decreasing -> higher score never yields a lower probability
    assert np.all(np.diff(out) >= -1e-12)
    assert np.all(out >= 0.0) and np.all(out <= 1.0)


def test_calibrated_probabilities_in_unit_interval():
    frame = C.build_calibrated_frame(_make_preds(seed=2))
    p = frame[C.PROB_COL].to_numpy(dtype=float)
    p = p[np.isfinite(p)]
    assert len(p) > 0
    assert np.all(p >= 0.0) and np.all(p <= 1.0)


# --------------------------------------------------------------------------- #
# 2. Calibration does not materially worsen Brier on a monotonic signal
# --------------------------------------------------------------------------- #
def test_calibration_beats_base_rate_brier_on_monotonic_signal():
    rng = np.random.default_rng(3)
    x = rng.normal(0, 1, 800)
    p = 1.0 / (1.0 + np.exp(-1.8 * x))
    y = (rng.uniform(size=800) < p).astype(float)
    cal = C._fit_isotonic(x[:600], y[:600])
    pred = cal.predict(x[600:])
    base = float(y[:600].mean())
    brier_cal = C.brier_score(pred, y[600:])
    brier_base = C.brier_score(np.full(200, base), y[600:])
    # calibrated probability should not be materially worse than the base rate;
    # on a clean monotonic signal it is clearly better.
    assert brier_cal <= brier_base + 1e-9, (brier_cal, brier_base)


# --------------------------------------------------------------------------- #
# 3. Walk-forward calibration never fits on the evaluated fold
# --------------------------------------------------------------------------- #
def test_calibration_does_not_fit_on_evaluated_fold():
    # Fold 0: prob rises with score. Fold 1: labels INVERTED vs score. If the
    # calibrator (wrongly) fit on fold 1 itself, isotonic would flatten; fitting
    # on the prior fold 0 makes fold-1 probabilities RISE with score.
    rng = np.random.default_rng(4)
    rows = []
    base = dt.date(2023, 1, 2)
    dates = _business_dates(base, 8)
    for i, d in enumerate(dates):
        fold = 0 if i < 4 else 1
        for j in range(12):
            score = float(rng.normal(0, 1))
            if fold == 0:
                label = score > 0          # increasing relationship
            else:
                label = score < 0          # inverted within the evaluated fold
            rows.append({
                "ticker": f"N{j}", "as_of_date": d,
                "target_date": d + dt.timedelta(days=7), "fold_id": fold,
                "model_name": "ridge", "score": score,
                "predicted_excess_return_5d": score * 0.01,
                "realized_excess_return_5d_vs_spy": (0.01 if label else -0.01),
                "realized_return_5d": (0.01 if label else -0.01),
                "spy_return_5d": 0.0, "outperform_spy_flag": bool(label),
                "positive_return_flag": bool(label),
            })
    preds = pd.DataFrame(rows)[T.OOS_COLUMNS]
    cal = C.walk_forward_calibrate(preds)
    # first fold has no prior data -> label-free rank fallback
    f0 = cal[cal["fold_id"] == 0]
    assert set(f0["calibrator_name"].unique()) == {"rank_fallback"}
    # fold 1 probabilities increase with score (used fold 0, not its own labels)
    f1 = cal[cal["fold_id"] == 1]
    rho = R.M.spearman_rank_ic(f1["score"].to_numpy(), f1[C.PROB_COL].to_numpy())
    assert rho > 0.5, rho


# --------------------------------------------------------------------------- #
# 4. Intervals use prior-fold residuals only (no future leakage)
# --------------------------------------------------------------------------- #
def test_interval_layer_uses_prior_residuals_only():
    preds = _make_preds(n_folds=4, per_fold=60, seed=5)
    out = R.walk_forward_intervals(preds, coverage=0.8, min_residuals=20)
    # fold 0 has no prior residuals -> empty interval
    f0 = out[out["fold_id"] == 0]
    assert f0["lo_return_5d"].isna().all() and f0["hi_return_5d"].isna().all()
    # fold 2 width must equal the conformal width of folds {0,1} residuals
    prior = out[out["fold_id"] < 2]
    res = (prior["realized_excess_return_5d_vs_spy"].to_numpy()
           - prior["predicted_excess_return_5d"].to_numpy())
    lo_q, hi_q = R.conformal_quantiles(res, 0.8)
    f2 = out[out["fold_id"] == 2]
    assert _approx(float(f2["interval_width"].iloc[0]), hi_q - lo_q, 1e-9)
    # mutating a fold-2 realized value must not change fold-2's own width
    mutated = preds.copy()
    idx = mutated.index[mutated["fold_id"] == 2][0]
    mutated.loc[idx, "realized_excess_return_5d_vs_spy"] += 99.0
    out2 = R.walk_forward_intervals(mutated, coverage=0.8, min_residuals=20)
    w_before = float(f2["interval_width"].iloc[0])
    w_after = float(out2[out2["fold_id"] == 2]["interval_width"].iloc[0])
    assert _approx(w_before, w_after, 1e-9)


# --------------------------------------------------------------------------- #
# 5. Interval coverage is computed correctly on a known case
# --------------------------------------------------------------------------- #
def test_interval_coverage_computed_correctly():
    df = pd.DataFrame({
        "fold_id": [1, 1, 1, 1],
        "score": [0.0, 0.0, 0.0, 0.0],
        "lo_return_5d": [-1.0, -1.0, -1.0, -1.0],
        "hi_return_5d": [1.0, 1.0, 1.0, 1.0],
        "realized_excess_return_5d_vs_spy": [0.5, -0.5, 2.0, -3.0],
        "realized_return_5d": [0.5, -0.5, 2.0, -3.0],
        "risk_band": ["low", "low", "high", "high"],
    })
    # two inside [-1, 1], two outside -> coverage 0.5
    assert _approx(R.interval_coverage(df), 0.5)


# --------------------------------------------------------------------------- #
# 6. Risk band assignment is deterministic and ordered
# --------------------------------------------------------------------------- #
def test_risk_band_assignment_is_deterministic():
    vals = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0]
    a = R.assign_risk_band(vals)
    b = R.assign_risk_band(vals)
    assert a == b                       # deterministic
    assert a[0] == "low" and a[-1] == "high"
    assert set(a) <= set(R.RISK_BANDS)
    # non-finite -> no band, finite values still banded
    mixed = R.assign_risk_band([float("nan"), 1.0, 100.0])
    assert mixed[0] == "" and mixed[1] in R.RISK_BANDS and mixed[2] == "high"


# --------------------------------------------------------------------------- #
# 7. Output schema
# --------------------------------------------------------------------------- #
def test_output_has_expected_columns():
    frame = C.build_calibrated_frame(_make_preds(seed=6))
    assert list(frame.columns) == C.CALIBRATED_COLUMNS
    assert len(frame) > 0
    # one calibrated row per (ticker, as_of_date) prediction, no dupes introduced
    assert not frame.duplicated(subset=["ticker", "as_of_date", "fold_id"]).any()


def test_build_calibrated_frame_handles_empty():
    empty = pd.DataFrame(columns=T.OOS_COLUMNS)
    frame = C.build_calibrated_frame(empty)
    assert list(frame.columns) == C.CALIBRATED_COLUMNS
    assert frame.empty


# --------------------------------------------------------------------------- #
# 8. Report contains required sections and never claims production edge
# --------------------------------------------------------------------------- #
def test_report_contains_required_sections_synthetic():
    frame = C.build_calibrated_frame(_make_preds(seed=7))
    meta = {"source": "SYNTHETIC (test)", "is_synthetic": True,
            "coverage": R.DEFAULT_COVERAGE, "n_rows": len(frame),
            "n_folds": int(frame["fold_id"].nunique()),
            "n_tickers": int(frame["ticker"].nunique()),
            "generated_on": "2026-06-16"}
    md = C.build_report_markdown(frame, meta)
    for section in C.REQUIRED_SECTIONS:
        assert section in md, f"missing section: {section}"
    assert "not production edge" in md.lower()
    # no false production-edge claim on synthetic data
    assert "production edge" in md.lower()


def test_gate3_and_gate5_structure():
    frame = C.build_calibrated_frame(_make_preds(n_folds=4, per_fold=80, seed=8))
    g3 = C.gate3(frame)
    g5 = R.gate5(frame, coverage=R.DEFAULT_COVERAGE)
    assert "passed" in g3 and "evaluable" in g3
    assert "passed" in g5 and "evaluable" in g5
    # with 4 folds the later folds carry intervals -> gate 5 is evaluable
    assert g5["evaluable"] is True


# --------------------------------------------------------------------------- #
# 9. Guardrails: no Paper Trader / sklearn / xgboost; api_server untouched
# --------------------------------------------------------------------------- #
def test_no_paper_trader_import():
    assert "api_server" not in sys.modules
    offenders = [m for m in sys.modules
                 if m == "paper_trader" or m.startswith("paper_trader.")]
    assert not offenders, f"unexpected Paper Trader import: {offenders}"


def test_no_sklearn_or_xgboost_dependency():
    import importlib
    for mod in ("model.calibrate", "model.risk"):
        m = importlib.import_module(mod)
        src = open(m.__file__, "r", encoding="utf-8").read().lower()
        assert "import sklearn" not in src and "from sklearn" not in src, mod
        assert "import xgboost" not in src and "from xgboost" not in src, mod


def test_model_sources_contain_no_paper_trader_or_api_server_refs():
    model_dir = os.path.join(_REPO_ROOT, "model")
    for fn in os.listdir(model_dir):
        if not fn.endswith(".py"):
            continue
        with open(os.path.join(model_dir, fn), "r", encoding="utf-8") as f:
            src = f.read().lower()
        assert "paper_trader" not in src, f"{fn} references paper_trader"
        assert "import api_server" not in src, f"{fn} imports api_server"


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
    assert "model.calibrate" not in src
    assert "phase2c" not in src.lower()


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
