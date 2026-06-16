"""Tests for the Phase 2A feature builder (``model/features.py``).

Pure-logic only: no database, no network, no import of api_server or Paper
Trader. The production model stack and pytest are not required.

Runs two ways:
  * under pytest:   pytest tests/test_phase2_features.py
  * without pytest: python tests/test_phase2_features.py
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

from research import walk_forward_dataset as W  # noqa: E402
from model import features as F  # noqa: E402


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _approx(a, b, tol=1e-9):
    return abs(a - b) <= tol


def _is_missing(x):
    return x is None or (isinstance(x, float) and math.isnan(x))


def _eq_missing_aware(a, b, tol=1e-9):
    if _is_missing(a) or _is_missing(b):
        return _is_missing(a) and _is_missing(b)
    return abs(a - b) <= tol


def _mkseries(start: dt.date, prices, ticker="T"):
    """TickerSeries on consecutive business days from `start`."""
    dates, d = [], start
    for _ in prices:
        while d.weekday() > 4:
            d += dt.timedelta(days=1)
        dates.append(d)
        d += dt.timedelta(days=1)
    return W.TickerSeries(ticker=ticker, dates=dates, adj=[float(p) for p in prices])


# --------------------------------------------------------------------------- #
# 1. No feature uses future data
# --------------------------------------------------------------------------- #
def test_no_future_leakage_truncation_invariance():
    """A feature row at as_of must be identical whether the series is full or
    truncated exactly at as_of — i.e. it depends only on data <= as_of."""
    series, vol = F._synthetic_series("T", 300, seed=7)
    spy, _ = F._synthetic_series("SPY", 300, seed=8, with_volume=False)
    full = F.build_feature_rows_for_ticker(series, spy, volume=vol, min_history=1)

    i = 250
    target = next(r for r in full if r["as_of_date"] == series.dates[i])
    trunc = W.TickerSeries("T", series.dates[: i + 1], series.adj[: i + 1])
    spy_trunc = W.TickerSeries("SPY", spy.dates[: i + 1], spy.adj[: i + 1])
    trunc_rows = F.build_feature_rows_for_ticker(
        trunc, spy_trunc, volume=vol[: i + 1], min_history=1)
    last = trunc_rows[-1]

    assert last["as_of_date"] == target["as_of_date"]
    for name in F.feature_names():
        assert _eq_missing_aware(target[name], last[name]), (
            f"leakage: feature {name} changed under truncation "
            f"({target[name]!r} != {last[name]!r})")


def test_no_future_leakage_future_mutation_does_not_change_past_row():
    """Mutating prices strictly after as_of must not change the as_of row."""
    series, vol = F._synthetic_series("T", 280, seed=3)
    spy, _ = F._synthetic_series("SPY", 280, seed=4, with_volume=False)
    i = 240
    base_rows = F.build_feature_rows_for_ticker(series, spy, volume=vol, min_history=1)
    base = next(r for r in base_rows if r["as_of_date"] == series.dates[i])

    adj2 = list(series.adj)
    for k in range(i + 1, len(adj2)):
        adj2[k] = adj2[k] * 5.0 + 1.0  # wild future moves
    series2 = W.TickerSeries("T", series.dates, adj2)
    mut_rows = F.build_feature_rows_for_ticker(series2, spy, volume=vol, min_history=1)
    mut = next(r for r in mut_rows if r["as_of_date"] == series.dates[i])

    for name in F.feature_names():
        assert _eq_missing_aware(base[name], mut[name]), f"future mutation leaked into {name}"


# --------------------------------------------------------------------------- #
# 2. Momentum calculations are correct
# --------------------------------------------------------------------------- #
def test_trailing_return_known_values_and_bounds():
    adj = [10.0, 20.0, 40.0, 80.0]
    assert _approx(F.trailing_return(adj, 3, 1), 80 / 40 - 1)
    assert _approx(F.trailing_return(adj, 3, 3), 80 / 10 - 1)
    assert F.trailing_return(adj, 1, 2) is None          # window reaches before start
    assert F.trailing_return(adj, 0, 1) is None
    assert F.trailing_return([0.0, 5.0], 1, 1) is None    # non-positive base


def test_momentum_12_1_known_value_and_history_gate():
    adj = [float(k + 1) for k in range(260)]   # adj[k] = k + 1
    # at i: adj[i-21] / adj[i-252] - 1
    assert _approx(F.momentum_12_1(adj, 259), 239.0 / 8.0 - 1.0)
    assert F.momentum_12_1(adj, 251) is None             # < 252 history


def test_return_features_wired_into_rows():
    prices = list(range(100, 230))             # 130 strictly increasing sessions
    s = _mkseries(dt.date(2024, 1, 1), prices)
    rows = F.build_feature_rows_for_ticker(s, None, min_history=130)
    r = rows[-1]
    i = len(prices) - 1
    assert _approx(r["return_5d"], prices[i] / prices[i - 5] - 1)
    assert _approx(r["return_21d"], prices[i] / prices[i - 21] - 1)
    assert _approx(r["return_126d"], prices[i] / prices[i - 126] - 1)


# --------------------------------------------------------------------------- #
# 3. Volatility calculations are correct
# --------------------------------------------------------------------------- #
def test_realized_vol_matches_numpy():
    adj = [100.0, 110.0, 99.0, 108.9]          # daily rets: +0.1, -0.1, +0.1
    expected = float(np.std(np.array([0.1, -0.1, 0.1]), ddof=1)) * math.sqrt(252)
    assert _approx(F.realized_vol(adj, 3, 3), expected, 1e-9)
    assert F.realized_vol(adj, 3, 4) is None             # window exceeds history


def test_downside_vol_only_uses_negative_returns():
    adj = [100.0, 110.0, 99.0, 108.9]          # one negative return: -0.1
    assert _approx(F.downside_vol(adj, 3, 3), 0.1 * math.sqrt(252), 1e-9)
    rising = [100.0, 101.0, 102.0, 103.0]      # no negatives -> 0.0
    assert _approx(F.downside_vol(rising, 3, 3), 0.0)


# --------------------------------------------------------------------------- #
# 4. SPY-relative features are correct
# --------------------------------------------------------------------------- #
def test_rolling_beta_and_corr_on_known_returns():
    spy_rets = np.array([np.nan, 0.01, -0.02, 0.03, -0.01, 0.015, 0.02])
    tick_rets = np.array([np.nan] + list(2.0 * spy_rets[1:]))   # exactly 2x SPY
    assert _approx(F.rolling_beta(tick_rets, spy_rets, 6, 6), 2.0, 1e-9)
    assert _approx(F.rolling_corr(tick_rets, spy_rets, 6, 6), 1.0, 1e-9)
    # a NaN anywhere in the window -> None (no silent fill)
    bad = tick_rets.copy()
    bad[3] = np.nan
    assert F.rolling_beta(bad, spy_rets, 6, 6) is None


def test_excess_return_vs_spy_is_ticker_minus_benchmark():
    prices = [100.0] * 25
    prices[3] = 100.0
    prices[24] = 120.0                          # 21d return at i=24 = +0.20
    s = _mkseries(dt.date(2024, 1, 1), prices, ticker="T")
    spy = _mkseries(dt.date(2024, 1, 1), [400.0] * 25, ticker="SPY")   # flat SPY
    rows = F.build_feature_rows_for_ticker(s, spy, min_history=22)
    r = next(row for row in rows if row["as_of_date"] == s.dates[24])
    assert _approx(r["excess_return_vs_spy_21d"], 0.20, 1e-9)


# --------------------------------------------------------------------------- #
# 5. Market regime features are correct
# --------------------------------------------------------------------------- #
def test_spy_above_200d_and_returns():
    inc = _mkseries(dt.date(2023, 1, 2), list(range(100, 310)), ticker="SPY")  # 210 up
    reg = F.market_regime_features(inc, inc.dates[-1])
    assert reg["spy_above_200d"] == 1.0
    j = len(inc.adj) - 1
    assert _approx(reg["spy_return_21d"], inc.adj[j] / inc.adj[j - 21] - 1)

    dec = _mkseries(dt.date(2023, 1, 2), list(range(310, 100, -1)), ticker="SPY")  # 210 down
    assert F.market_regime_features(dec, dec.dates[-1])["spy_above_200d"] == 0.0


def test_spy_above_200d_none_without_enough_history():
    short = _mkseries(dt.date(2024, 1, 1), list(range(100, 150)), ticker="SPY")  # 50
    reg = F.market_regime_features(short, short.dates[-1])
    assert reg["spy_above_200d"] is None
    assert F.market_regime_features(None, dt.date(2024, 1, 1))["spy_return_21d"] is None


# --------------------------------------------------------------------------- #
# 6. Optional volume features work when volume exists
# --------------------------------------------------------------------------- #
def test_volume_features_when_present():
    adj = [float(p) for p in range(100, 125)]                 # 25 sessions
    vol = [float(v) for v in range(1000, 1025)]
    i = 24
    w = 21
    a = np.array(adj[i - w + 1: i + 1])
    v = np.array(vol[i - w + 1: i + 1])
    assert _approx(F.avg_dollar_volume(adj, vol, i, w), float(np.mean(a * v)), 1e-6)
    expected_z = (vol[i] - float(np.mean(v))) / float(np.std(v, ddof=1))
    assert _approx(F.volume_zscore(vol, i, w), expected_z, 1e-9)


# --------------------------------------------------------------------------- #
# 7. Optional volume features do not fake values when volume is missing
# --------------------------------------------------------------------------- #
def test_volume_features_none_when_missing_or_invalid():
    adj = [float(p) for p in range(100, 125)]
    assert F.avg_dollar_volume(adj, None, 24, 21) is None
    assert F.volume_zscore(None, 24, 21) is None
    # A NaN inside the window must disqualify the window (no silent fill).
    vol = [float(v) for v in range(1000, 1025)]
    vol[10] = float("nan")
    assert F.volume_window_available(vol, 24, 21) is False
    assert F.avg_dollar_volume(adj, vol, 24, 21) is None
    # Negative volume is invalid, not fabricated around.
    vol2 = [float(v) for v in range(1000, 1025)]
    vol2[5] = -1.0
    assert F.volume_window_available(vol2, 24, 21) is False


def test_volume_features_none_in_rows_when_no_volume():
    s = _mkseries(dt.date(2024, 1, 1), list(range(100, 230)))
    rows = F.build_feature_rows_for_ticker(s, None, volume=None, min_history=130)
    for name in F.VOLUME_FEATURES:
        assert rows[-1][name] is None


# --------------------------------------------------------------------------- #
# 8. Unavailable feature families are declared but not faked
# --------------------------------------------------------------------------- #
def test_unavailable_families_declared():
    fams_no_vol = F.unavailable_feature_families(volume_present=False)
    for fam in F.DISABLED_FEATURE_FAMILIES:
        assert fam in fams_no_vol
    assert "volume_liquidity" in fams_no_vol
    fams_vol = F.unavailable_feature_families(volume_present=True)
    assert "volume_liquidity" not in fams_vol


def test_no_fabricated_columns_for_disabled_families():
    s = _mkseries(dt.date(2024, 1, 1), list(range(100, 230)))
    row = F.build_feature_rows_for_ticker(s, None, min_history=130)[-1]
    forbidden = ("sector", "industry", "earnings", "event", "news",
                 "sentiment", "macro")
    for col in row:
        low = col.lower()
        assert not any(tok in low for tok in forbidden), f"fabricated column: {col}"


# --------------------------------------------------------------------------- #
# 9. Labeled dataset joins features to forward labels correctly
# --------------------------------------------------------------------------- #
def test_labeled_dataset_joins_and_labels_are_correct():
    series, vol = F._synthetic_series("T", 260, seed=11)
    spy, _ = F._synthetic_series("SPY", 260, seed=12, with_volume=False)
    min_history = 200
    horizon = 5
    df = F.build_labeled_dataset_for_ticker(
        series, spy, volume=vol, horizon=horizon, min_history=min_history)

    assert not df.empty
    for col in F.LABEL_COLUMNS:
        assert col in df.columns
    for col in ("return_21d", "realized_vol_21d", "feature_set_version"):
        assert col in df.columns

    n = len(series.adj)
    expected_rows = n - horizon - min_history + 1     # [min_history-1 .. n-1-horizon]
    assert len(df) == expected_rows

    # Labels match the realized forward return at each as_of (no off-by-one).
    by_date = {d: a for d, a in zip(series.dates, series.adj)}
    idx = {d: k for k, d in enumerate(series.dates)}
    row = df.iloc[len(df) // 2]
    i = idx[row["as_of_date"]]
    assert _approx(row["realized_return_5d"], series.adj[i + horizon] / series.adj[i] - 1, 1e-9)
    assert row["target_date"] == series.dates[i + horizon]
    assert by_date[row["as_of_date"]] == series.adj[i]


def test_join_drops_unlabeled_tail():
    # Feature rows in the last `horizon` sessions have no realized label and must
    # not appear in the labeled dataset.
    series, _ = F._synthetic_series("T", 230, seed=21)
    spy, _ = F._synthetic_series("SPY", 230, seed=22, with_volume=False)
    df = F.build_labeled_dataset_for_ticker(series, spy, min_history=200, horizon=5)
    assert df["as_of_date"].max() <= series.dates[len(series.dates) - 1 - 5]


def test_cross_sectional_zscore_standardizes_within_date():
    import pandas as pd
    df = pd.DataFrame({
        "ticker": ["A", "B", "A", "B"],
        "as_of_date": [dt.date(2024, 1, 1), dt.date(2024, 1, 1),
                       dt.date(2024, 1, 2), dt.date(2024, 1, 2)],
        "return_5d": [1.0, 3.0, 10.0, 10.0],   # day2 has zero variance
    })
    out = F.cross_sectional_zscore(df, cols=["return_5d"])
    day1 = out[out["as_of_date"] == dt.date(2024, 1, 1)]["return_5d_z"].tolist()
    sd = float(np.std([1.0, 3.0], ddof=1))
    assert _approx(sorted(day1)[0], (1.0 - 2.0) / sd, 1e-9)
    assert _approx(sorted(day1)[1], (3.0 - 2.0) / sd, 1e-9)
    day2 = out[out["as_of_date"] == dt.date(2024, 1, 2)]["return_5d_z"].tolist()
    assert all(_approx(v, 0.0) for v in day2)   # zero-variance date -> 0.0


# --------------------------------------------------------------------------- #
# 10. The model package imports neither Paper Trader nor api_server
# --------------------------------------------------------------------------- #
def test_no_paper_trader_or_api_server_at_import_time():
    assert "api_server" not in sys.modules, "model.features must not import api_server"
    offenders = [m for m in sys.modules
                 if m == "paper_trader" or m.startswith("paper_trader.")]
    assert not offenders, f"unexpected Paper Trader import: {offenders}"


def test_model_sources_contain_no_paper_trader_references():
    model_dir = os.path.join(_REPO_ROOT, "model")
    for fn in os.listdir(model_dir):
        if not fn.endswith(".py"):
            continue
        with open(os.path.join(model_dir, fn), "r", encoding="utf-8") as f:
            src = f.read().lower()
        assert "paper_trader" not in src, f"{fn} references paper_trader"
        assert "8001" not in src, f"{fn} references the Paper Trader port"


# --------------------------------------------------------------------------- #
# 11. api_server.py behavior/contract is unchanged by Phase 2A
# --------------------------------------------------------------------------- #
def test_api_server_contract_unchanged_and_untouched():
    path = os.path.join(_REPO_ROOT, "api_server.py")
    if not os.path.exists(path):
        return  # api_server lives only on the VM; skip where absent
    with open(path, "r", encoding="utf-8") as f:
        src = f.read()
    assert '@app.post("/predict_all_models/"' in src
    assert '@app.get("/predict/{ticker}")' in src
    for key in ('"recommendation"', '"confidence"', '"agreement"',
                '"ensemble_day5"', '"predictions"', '"zscore"'):
        assert key in src, f"response key {key} missing from api_server.py"
    # Phase 2A must not have wired itself into the live service.
    low = src.lower()
    for token in ("model.features", "feature_snapshots", "phase2", "build_feature_dataset"):
        assert token not in low, f"api_server.py unexpectedly references {token}"


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
