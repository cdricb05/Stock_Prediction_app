"""Tests for the walk-forward baseline harness.

Pure-logic only: no database, no network, and no import of api_server or Paper
Trader. The production model stack (xgboost/prophet/...) and pytest are not
required to run these.

Runs two ways:
  * under pytest:        pytest tests/test_walk_forward_baseline.py
  * without pytest:      python tests/test_walk_forward_baseline.py
The __main__ block discovers every ``test_*`` function and runs it, printing
PASS/FAIL and exiting non-zero on any failure (the GCP venv has no pytest).
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

from research import metrics as M  # noqa: E402
from research import walk_forward_dataset as W  # noqa: E402
from research.run_walk_forward_baseline import build_report_markdown, REQUIRED_SECTIONS  # noqa: E402


def _approx(a, b, tol=1e-9):
    return abs(a - b) <= tol


def _series(start: dt.date, prices):
    """Build a TickerSeries with consecutive business days from `start`."""
    dates, d = [], start
    for _ in prices:
        while d.weekday() > 4:
            d += dt.timedelta(days=1)
        dates.append(d)
        d += dt.timedelta(days=1)
    return W.TickerSeries(ticker="T", dates=dates, adj=[float(p) for p in prices])


# --------------------------------------------------------------------------- #
# 1. No future leakage in target construction
# --------------------------------------------------------------------------- #
def test_no_future_leakage_target_uses_only_future_index():
    prices = [10, 11, 12, 13, 14, 15, 16, 17, 18, 19]
    s = _series(dt.date(2024, 1, 1), prices)
    rows = W.build_rows_for_ticker(s, None, horizon=5, min_history=1)
    for r in rows:
        i = s.dates.index(r["as_of_date"])
        # target session is strictly in the future
        assert r["target_date"] > r["as_of_date"]
        assert r["target_date"] == s.dates[i + 5]
        # the as_of feature anchor never references a future close
        assert r["current_adj_close"] == prices[i]
        assert r["future_adj_close"] == prices[i + 5]


def test_forward_return_is_future_only_and_ignores_past():
    adj = [100.0, 50.0, 200.0, 1.0, 4.0, 8.0]  # wild past values must not matter
    # from i=0, horizon=2 -> adj[2]/adj[0]-1 = 200/100-1 = 1.0
    assert _approx(W.forward_return(adj, 0, 2), 1.0)
    # past (indices < i) cannot change a future-only target
    assert _approx(W.forward_return(adj, 3, 2), 8.0 / 1.0 - 1.0)


# --------------------------------------------------------------------------- #
# 2. Forward 5-session return computed correctly
# --------------------------------------------------------------------------- #
def test_forward_5_session_return_value():
    prices = [100, 101, 102, 103, 104, 110]  # i=0 -> i+5 = 110
    s = _series(dt.date(2024, 3, 1), prices)
    rows = W.build_rows_for_ticker(s, None, horizon=5, min_history=1)
    assert len(rows) == 1
    r = rows[0]
    assert _approx(r["realized_return_5d"], 110 / 100 - 1.0)
    assert r["positive_return_flag"] is True


def test_forward_return_none_when_no_future():
    adj = [1, 2, 3]
    assert W.forward_return(adj, 0, 5) is None
    assert W.forward_return(adj, 2, 1) is None


# --------------------------------------------------------------------------- #
# 3. Missing future target rows are excluded
# --------------------------------------------------------------------------- #
def test_last_horizon_rows_excluded():
    prices = list(range(100, 120))  # 20 sessions
    s = _series(dt.date(2024, 1, 1), prices)
    rows = W.build_rows_for_ticker(s, None, horizon=5, min_history=1)
    # 20 sessions, horizon 5 -> last 5 have no target -> 15 rows
    assert len(rows) == 15
    assert max(r["as_of_date"] for r in rows) == s.dates[14]


def test_min_history_excludes_early_rows():
    prices = list(range(100, 130))  # 30 sessions
    s = _series(dt.date(2024, 1, 1), prices)
    rows = W.build_rows_for_ticker(s, None, horizon=5, min_history=10)
    # need i+1 >= 10 -> i in [9, 24] -> 16 rows
    assert len(rows) == 16
    assert rows[0]["n_history"] == 10


# --------------------------------------------------------------------------- #
#    Benchmark alignment / excess return
# --------------------------------------------------------------------------- #
def test_benchmark_excess_and_outperform_flag():
    prices = [100, 101, 102, 103, 104, 106]  # +6% over 5 sessions
    s = _series(dt.date(2024, 2, 1), prices)
    # SPY rises only +2% over the same window
    spy = _series(dt.date(2024, 2, 1), [400, 401, 402, 403, 404, 408])
    rows = W.build_rows_for_ticker(s, spy, horizon=5, min_history=1)
    r = rows[0]
    assert _approx(r["realized_return_5d"], 0.06, 1e-6)
    assert _approx(r["spy_return_5d"], 0.02, 1e-6)
    assert _approx(r["realized_excess_return_5d_vs_spy"], 0.04, 1e-6)
    assert r["outperform_spy_flag"] is True


def test_asof_index_picks_last_on_or_before():
    dates = [dt.date(2024, 1, 1), dt.date(2024, 1, 3), dt.date(2024, 1, 5)]
    assert W._asof_index(dates, dt.date(2024, 1, 4)) == 1
    assert W._asof_index(dates, dt.date(2024, 1, 5)) == 2
    assert W._asof_index(dates, dt.date(2023, 12, 31)) is None


# --------------------------------------------------------------------------- #
# 4. Confidence decile bucketing works
# --------------------------------------------------------------------------- #
def test_calibration_decile_bucketing():
    conf = list(range(0, 100))  # 100 distinct confidences
    flags = [c >= 50 for c in conf]  # higher confidence -> positive
    table = M.calibration_table(conf, flags, n_bins=10)
    assert len(table) == 10
    assert sum(r["n"] for r in table) == 100
    # low decile all-negative, high decile all-positive
    assert table[0]["hit_rate"] == 0.0
    assert table[-1]["hit_rate"] == 1.0
    assert M.is_calibrated(table) is True


def test_calibration_handles_constant_confidence():
    table = M.calibration_table([50] * 20, [True, False] * 10, n_bins=10)
    assert len(table) == 1
    assert table[0]["n"] == 20


# --------------------------------------------------------------------------- #
# 5. Precision@K works
# --------------------------------------------------------------------------- #
def test_precision_at_k_known_example():
    scores = [0.9, 0.8, 0.7, 0.1, 0.05]
    labels = [1, 0, 1, 1, 0]
    assert _approx(M.precision_at_k(scores, labels, 2), 0.5)   # top2: 1,0
    assert _approx(M.precision_at_k(scores, labels, 3), 2 / 3) # top3: 1,0,1
    assert math.isnan(M.precision_at_k(scores, labels, 0))


def test_precision_at_k_clamps_and_drops_nan():
    scores = [0.5, float("nan"), 0.9]
    labels = [0, 1, 1]
    # nan score dropped -> two rows; top of {0.9->1, 0.5->0}
    assert _approx(M.precision_at_k(scores, labels, 10), 0.5)


# --------------------------------------------------------------------------- #
# 6. Rank IC handles missing values
# --------------------------------------------------------------------------- #
def test_rank_ic_perfect_and_missing():
    pred = [1, 2, 3, 4, 5]
    real = [10, 20, 30, 40, 50]
    assert _approx(M.spearman_rank_ic(pred, real), 1.0, 1e-9)
    anti = [50, 40, 30, 20, 10]
    assert _approx(M.spearman_rank_ic(pred, anti), -1.0, 1e-9)
    # NaNs dropped pairwise, remaining still perfectly ranked
    pred2 = [1, 2, float("nan"), 4, 5]
    real2 = [10, 20, 999, 40, 50]
    assert _approx(M.spearman_rank_ic(pred2, real2), 1.0, 1e-9)


def test_rank_ic_insufficient_or_constant_is_nan():
    assert math.isnan(M.spearman_rank_ic([1.0], [2.0]))
    assert math.isnan(M.spearman_rank_ic([5, 5, 5], [1, 2, 3]))


def test_drawdown_summary():
    rets = [0.05, -0.03, -0.10, 0.01, -0.01]
    dd = M.drawdown_summary(rets, loss_threshold=-0.02)
    assert dd["n"] == 5
    assert _approx(dd["worst"], -0.10)
    assert _approx(dd["pct_negative"], 0.6)
    assert _approx(dd["pct_below_threshold"], 0.4)  # -0.03 and -0.10


def test_bucket_and_confusion_matrix():
    recs = ["Strong Buy", "Buy", "Hold", "Sell", "Strong Sell"]
    assert [M.bucket_recommendation(r) for r in recs] == ["BUY", "BUY", "HOLD", "SELL", "SELL"]
    cm = M.confusion_matrix_rec(recs, [True, False, True, False, True])
    assert cm["BUY"]["positive"] == 1 and cm["BUY"]["non_positive"] == 1
    assert cm["SELL"]["n"] == 2


# --------------------------------------------------------------------------- #
# 7. Report contains required sections
# --------------------------------------------------------------------------- #
def _synthetic_preds(n=300, seed=1):
    rng = np.random.default_rng(seed)
    pred = rng.normal(0, 0.02, n)
    real = pred * 0.2 + rng.normal(0, 0.03, n)
    spy = rng.normal(0.001, 0.01, n)
    recs = rng.choice(["Buy", "Hold", "Sell", "Strong Buy"], n)
    return pd.DataFrame({
        "ticker": ["T%d" % (i % 20) for i in range(n)],
        "as_of_date": [dt.date(2025, 1, 1) + dt.timedelta(days=i) for i in range(n)],
        "target_date": [dt.date(2025, 1, 8) + dt.timedelta(days=i) for i in range(n)],
        "predicted_return_5d": pred,
        "recommendation": recs,
        "confidence": rng.uniform(0, 100, n),
        "agreement": rng.uniform(0.5, 1.0, n),
        "residual_alpha_vs_spy_pct": pred * 100,
        "zscore": rng.uniform(0, 3, n),
        "ran_models": ["Drift,LinearTrend,XGBoost,Naive,SMA"] * n,
        "model_errors": [""] * n,
        "realized_return_5d": real,
        "spy_return_5d": spy,
        "realized_excess_return_5d_vs_spy": real - spy,
        "positive_return_flag": real > 0,
        "outperform_spy_flag": (real - spy) > 0,
    })


def test_report_contains_required_sections():
    preds = _synthetic_preds()
    meta = {"start_date": dt.date(2025, 1, 1), "end_date": dt.date(2025, 6, 1),
            "horizon": 5, "min_history": 60, "lookback": 365, "benchmark": "SPY",
            "fast_smoke": False}
    md = build_report_markdown(preds, meta)
    for section in REQUIRED_SECTIONS:
        assert section in md, f"missing section: {section}"
    assert "Evidence of edge" in md


def test_report_handles_empty_predictions():
    empty = pd.DataFrame(columns=[
        "ticker", "as_of_date", "predicted_return_5d", "recommendation", "confidence",
        "agreement", "residual_alpha_vs_spy_pct", "zscore", "ran_models", "model_errors",
        "realized_return_5d", "spy_return_5d", "realized_excess_return_5d_vs_spy",
        "positive_return_flag", "outperform_spy_flag"])
    meta = {"start_date": None, "end_date": None, "horizon": 5, "min_history": 60,
            "lookback": 365, "benchmark": "SPY", "fast_smoke": True}
    md = build_report_markdown(empty, meta)
    assert "## Verdict" in md
    assert "INCONCLUSIVE" in md


# --------------------------------------------------------------------------- #
# 8. Research code does not import or mutate Paper Trader
# --------------------------------------------------------------------------- #
def test_no_paper_trader_or_api_server_at_import_time():
    # Importing the dataset builder + metrics must not pull in api_server,
    # the Paper Trader package, or any DB connection. (We check the actual
    # `paper_trader` package, not pip editable-install path-finder shims such as
    # `__editable___paper_trader_..._finder`, which the interpreter injects at
    # startup independent of what this code imports.)
    assert "api_server" not in sys.modules, "research modules must not import api_server"
    offenders = [m for m in sys.modules
                 if m == "paper_trader" or m.startswith("paper_trader.")]
    assert not offenders, f"unexpected Paper Trader import: {offenders}"


def test_research_sources_contain_no_paper_trader_references():
    research_dir = os.path.join(_REPO_ROOT, "research")
    for fn in os.listdir(research_dir):
        if not fn.endswith(".py"):
            continue
        with open(os.path.join(research_dir, fn), "r", encoding="utf-8") as f:
            src = f.read().lower()
        assert "paper_trader" not in src, f"{fn} references paper_trader"
        assert "8001" not in src, f"{fn} references the Paper Trader port"


# --------------------------------------------------------------------------- #
# 9. The live FastAPI route contract is not changed
# --------------------------------------------------------------------------- #
def test_api_server_predict_contract_unchanged():
    # Static check against the api_server source (no import needed): the route
    # and the key response fields the research harness depends on must exist.
    path = os.path.join(_REPO_ROOT, "api_server.py")
    if not os.path.exists(path):
        return  # api_server lives only on the VM; skip where absent
    with open(path, "r", encoding="utf-8") as f:
        src = f.read()
    assert '@app.post("/predict_all_models/"' in src
    for key in ('"recommendation"', '"confidence"', '"agreement"',
                '"ensemble_day5"', '"predictions"', '"zscore"'):
        assert key in src, f"response key {key} missing from api_server.py"
    # The research harness must not have edited api_server.
    assert "walk_forward" not in src.lower()


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
