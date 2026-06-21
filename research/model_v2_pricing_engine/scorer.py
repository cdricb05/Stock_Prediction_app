"""Phase 5-A — model_v2 preview pricing/scoring engine (deployable, offline).

This is the first operational version of the new "brain". It scores S&P 500
movement (SPY proxy) from **local price history only** and returns a stable
prediction payload that the GCP prediction service can serve unchanged in
Phase 5-B (``/predict_v2/{ticker}`` and ``/predict_market_v2``).

Honesty contract
----------------
The model is a *transparent, deterministic directional score* built from price
features (moving-average trend, damped momentum, a mean-reversion z-score, and a
volatility/drawdown regime gauge). It is **not** a calibrated probabilistic
forecaster: there is no walk-forward-fit coefficient set and no out-of-sample
probability calibration behind it yet. Accordingly every payload reports
``calibration_status == "limited"``, spells out the limitation in
``calibration_limitations``, and caps ``confidence`` conservatively. Missing or
degraded data never raises — it lowers confidence, marks features missing, and
(when there is no usable history at all) returns ``prediction ==
"INSUFFICIENT_DATA"``.

No network. No paid APIs. No AlphaVantage. No orders. No automation. No broker
execution. Preview only.
"""
from __future__ import annotations

import csv
import datetime as dt
import math
import os
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from . import features as F

# --------------------------------------------------------------------------- #
# Identity / contract constants
# --------------------------------------------------------------------------- #
MODEL_VERSION = "model_v2_preview-2026.06-v1"
MODEL_NAME = "model_v2_preview_market_direction"
DEFAULT_HORIZON = "21d"
_HORIZON_DAYS = 21

ALLOWED_PREDICTIONS = ("UP", "DOWN", "NEUTRAL", "INSUFFICIENT_DATA")
ALLOWED_REGIMES = ("risk_on", "risk_off", "neutral", "unknown")

# Confidence is intentionally capped because calibration is limited (see module
# docstring). A heuristic directional score must not advertise probabilistic
# precision it has not earned.
CONFIDENCE_CAP = 0.60
CONFIDENCE_FLOOR = 0.05

# Direction thresholds on the bounded composite signal score (in [-1, 1]).
UP_THRESHOLD = 0.15
DOWN_THRESHOLD = -0.15

# Transparent weights on bounded sub-signals (sum of abs weights = 1.0). These
# are *judgement-set*, not fit — documented as such in calibration_limitations.
SIGNAL_WEIGHTS = {
    "trend_200": 0.30,    # sign/size of price vs 200d SMA (primary trend)
    "trend_50_200": 0.20,  # 50d vs 200d SMA (golden/death-cross proxy)
    "mom_63": 0.20,       # damped 63d momentum
    "mom_21": 0.15,       # damped 21d momentum
    "meanrev": 0.15,      # contrarian fade of extreme 21d stretch
}

# Expected-return methodology: damped trailing-horizon momentum, shrunk toward
# zero. Documented, deployable, deliberately conservative — NOT a calibrated
# point forecast.
EXPECTED_RETURN_SHRINK = 0.40

# Regime thresholds (annualized realized vol).
REGIME_VOL_HIGH = 0.22
REGIME_DRAWDOWN_DEEP = -0.10

# Candidate local data files, in priority order. The first readable file that
# contains the ticker wins. Override with MODEL_V2_PRICE_HISTORY_CSV. D: is
# treated as READ-ONLY input only.
_REPO_ROOT = Path(__file__).resolve().parents[2]
_DATA_CANDIDATES: List[str] = [
    r"D:\Stock_Prediction_app_data\phase2k_g\output\phase2k_g_expanded_price_history_free.csv",
    str(_REPO_ROOT / "research" / "output" / "phase2g_price_history_real.csv"),
]

_CLOSE_COLS = ("adjusted_close", "adj_close", "close")
_DATE_COLS = ("date",)
_TICKER_COLS = ("ticker", "symbol")
_VOLUME_COLS = ("volume",)


# --------------------------------------------------------------------------- #
# Local data loading (offline CSV; no DB, no network)
# --------------------------------------------------------------------------- #
def _candidate_paths() -> List[str]:
    paths: List[str] = []
    env = os.environ.get("MODEL_V2_PRICE_HISTORY_CSV")
    if env:
        paths.append(env)
    paths.extend(_DATA_CANDIDATES)
    return paths


def _pick(header: Sequence[str], options: Sequence[str]) -> Optional[str]:
    lower = {h.lower(): h for h in header}
    for opt in options:
        if opt in lower:
            return lower[opt]
    return None


def load_price_series(ticker: str
                      ) -> Tuple[List[dt.date], List[float], Optional[List[float]], Optional[str]]:
    """Load an ascending (date, close, volume) series for ``ticker`` from local CSV.

    Returns ``(dates, closes, volumes_or_None, source_path_or_None)``. Tolerates
    differing column names across the local data files. Returns empty lists and a
    ``None`` source when no local file yields the ticker — the caller degrades to
    INSUFFICIENT_DATA rather than failing.
    """
    target = ticker.strip().upper()
    for path in _candidate_paths():
        try:
            if not os.path.isfile(path):
                continue
            with open(path, "r", newline="", encoding="utf-8") as fh:
                reader = csv.reader(fh)
                header = next(reader, None)
                if not header:
                    continue
                tcol = _pick(header, _TICKER_COLS)
                dcol = _pick(header, _DATE_COLS)
                ccol = _pick(header, _CLOSE_COLS)
                vcol = _pick(header, _VOLUME_COLS)
                if dcol is None or ccol is None:
                    continue
                idx = {h: k for k, h in enumerate(header)}
                rows: List[Tuple[dt.date, float, Optional[float]]] = []
                for raw in reader:
                    if not raw or len(raw) < len(header):
                        continue
                    if tcol is not None and raw[idx[tcol]].strip().upper() != target:
                        continue
                    try:
                        d = dt.date.fromisoformat(raw[idx[dcol]].strip()[:10])
                        c = float(raw[idx[ccol]])
                    except (ValueError, KeyError):
                        continue
                    if not math.isfinite(c) or c <= 0:
                        continue
                    v: Optional[float] = None
                    if vcol is not None:
                        try:
                            vv = float(raw[idx[vcol]])
                            v = vv if math.isfinite(vv) and vv >= 0 else None
                        except (ValueError, KeyError):
                            v = None
                    rows.append((d, c, v))
            if not rows:
                continue
            rows.sort(key=lambda r: r[0])
            # De-dup on date keeping the last occurrence (latest revision).
            dedup: Dict[dt.date, Tuple[float, Optional[float]]] = {}
            for d, c, v in rows:
                dedup[d] = (c, v)
            dates = sorted(dedup)
            closes = [dedup[d][0] for d in dates]
            vols = [dedup[d][1] for d in dates]
            volumes = vols if any(v is not None for v in vols) else None
            return dates, closes, volumes, path
        except OSError:
            continue
    return [], [], None, None


# --------------------------------------------------------------------------- #
# Scoring math (transparent, bounded)
# --------------------------------------------------------------------------- #
def _tanh_damp(x: Optional[float], scale: float) -> Optional[float]:
    """Bound a raw signal into (-1, 1) via tanh(x/scale). None passes through."""
    if x is None:
        return None
    return math.tanh(x / scale)


def _composite_signal(feats: Dict[str, Optional[float]]
                      ) -> Tuple[Optional[float], Dict[str, float], float]:
    """Combine features into a bounded composite signal in [-1, 1].

    Returns ``(signal_or_None, contributions, coverage)`` where ``coverage`` is
    the fraction of signal weight that had usable inputs. ``None`` when no
    sub-signal could be computed at all.
    """
    sub: Dict[str, Optional[float]] = {
        # price vs 200d SMA, scaled so ~8% above/below saturates toward +/-1.
        "trend_200": _tanh_damp(feats.get("price_vs_sma_200"), 0.08),
        # 50d vs 200d SMA, ~4% spread saturates.
        "trend_50_200": _tanh_damp(feats.get("sma_50_vs_200"), 0.04),
        # 63d momentum, ~10% move saturates.
        "mom_63": _tanh_damp(feats.get("return_63d"), 0.10),
        # 21d momentum, ~6% move saturates.
        "mom_21": _tanh_damp(feats.get("return_21d"), 0.06),
        # mean-reversion: fade a stretched 21d z-score (note the negative sign).
        "meanrev": (lambda z: -math.tanh(z / 2.0) if z is not None else None)(
            feats.get("return_zscore_21d")),
    }
    num = 0.0
    used_weight = 0.0
    contributions: Dict[str, float] = {}
    for key, weight in SIGNAL_WEIGHTS.items():
        val = sub.get(key)
        if val is None:
            continue
        contributions[key] = weight * val
        num += weight * val
        used_weight += weight
    if used_weight <= 0.0:
        return None, {}, 0.0
    signal = num / used_weight  # renormalize over available weight -> [-1, 1]
    coverage = used_weight  # weights sum to 1.0, so this is the covered fraction
    return max(-1.0, min(1.0, signal)), contributions, coverage


def _classify(signal: Optional[float]) -> str:
    if signal is None:
        return "INSUFFICIENT_DATA"
    if signal >= UP_THRESHOLD:
        return "UP"
    if signal <= DOWN_THRESHOLD:
        return "DOWN"
    return "NEUTRAL"


def _confidence(signal: Optional[float], coverage: float) -> float:
    """Conservative, capped confidence. Scales with signal strength and feature
    coverage; never exceeds CONFIDENCE_CAP because calibration is limited."""
    if signal is None:
        return CONFIDENCE_FLOOR
    raw = 0.15 + 0.45 * abs(signal)      # strength component
    raw *= (0.5 + 0.5 * max(0.0, min(1.0, coverage)))  # coverage haircut
    return round(max(CONFIDENCE_FLOOR, min(CONFIDENCE_CAP, raw)), 4)


def _expected_return(feats: Dict[str, Optional[float]], prediction: str) -> Optional[float]:
    """Damped trailing-horizon momentum, shrunk toward zero. None if unavailable
    or if the directional call is NEUTRAL/INSUFFICIENT (no honest point estimate)."""
    if prediction in ("NEUTRAL", "INSUFFICIENT_DATA"):
        return 0.0 if prediction == "NEUTRAL" else None
    mom = feats.get("return_21d")
    if mom is None:
        return None
    return round(EXPECTED_RETURN_SHRINK * float(mom), 6)


def _risk_regime(feats: Dict[str, Optional[float]]) -> str:
    above_200 = feats.get("price_vs_sma_200")
    vol = feats.get("realized_vol_63d")
    dd = feats.get("drawdown_from_peak_252d")
    if above_200 is None and vol is None:
        return "unknown"
    vol_high = vol is not None and vol > REGIME_VOL_HIGH
    deep_dd = dd is not None and dd < REGIME_DRAWDOWN_DEEP
    up_trend = above_200 is not None and above_200 > 0
    down_trend = above_200 is not None and above_200 <= 0
    if up_trend and not vol_high and not deep_dd:
        return "risk_on"
    if down_trend and (vol_high or deep_dd):
        return "risk_off"
    return "neutral"


# --------------------------------------------------------------------------- #
# Public scorer entrypoints
# --------------------------------------------------------------------------- #
def _safety_flags() -> Dict[str, bool]:
    return {
        "preview_only": True,
        "orders_enabled": False,
        "automation_enabled": False,
        "broker_execution_enabled": False,
        "production_replacement": False,
    }


def _insufficient_payload(ticker: str, source: Optional[str], reason: str) -> Dict:
    payload = {
        "model_version": MODEL_VERSION,
        "model_name": MODEL_NAME,
        "target_ticker": ticker.upper(),
        "as_of_date": None,
        "horizon": DEFAULT_HORIZON,
        "prediction": "INSUFFICIENT_DATA",
        "expected_return": None,
        "confidence": CONFIDENCE_FLOOR,
        "risk_regime": "unknown",
        "signal_score": None,
        "calibration_status": "limited",
        "calibration_limitations": [
            reason,
            "No local price history was available to compute features, so no "
            "directional view can be formed. This is a data-availability "
            "limitation, not a model failure; packaging is unaffected.",
        ],
        "features_used": [],
        "missing_features": list(F.ALL_FEATURES),
        "data_as_of": None,
        "input_data_source": source or "unavailable",
    }
    payload.update(_safety_flags())
    return payload


def score_ticker_v2(ticker: str) -> Dict:
    """Score one ticker's near-term direction from local price history.

    Returns the full model_v2 contract dict. Never raises on missing/short data
    — it degrades to ``INSUFFICIENT_DATA`` with ``calibration_status="limited"``.
    """
    if not ticker or not str(ticker).strip():
        return _insufficient_payload("", None, "Empty ticker supplied.")
    ticker = str(ticker).strip().upper()

    dates, closes, volumes, source = load_price_series(ticker)
    if not closes:
        return _insufficient_payload(
            ticker, source,
            f"No local price rows found for {ticker} in any configured data file.")

    feats, used, missing = F.compute_feature_vector(closes, volumes)
    signal, _contrib, coverage = _composite_signal(feats)
    prediction = _classify(signal)
    confidence = _confidence(signal, coverage)
    expected_return = _expected_return(feats, prediction)
    risk_regime = _risk_regime(feats)
    as_of = dates[-1].isoformat() if dates else None

    limitations = [
        "Directional score uses fixed, judgement-set weights on price-only "
        "signals; coefficients are NOT walk-forward fit to realized outcomes.",
        "Confidence is a heuristic strength/coverage gauge, NOT a calibrated "
        f"probability; it is capped at {CONFIDENCE_CAP} until out-of-sample "
        "probability calibration (Brier/log-loss) is added.",
        "Expected return is damped trailing-horizon momentum shrunk by "
        f"{EXPECTED_RETURN_SHRINK}; it is a conservative proxy, not a calibrated "
        "point forecast.",
        "Price-only feature set: no fundamentals, macro, earnings, or sentiment "
        "inputs are wired in yet.",
    ]
    if missing:
        limitations.append(
            "Some features degraded to unavailable for this run: "
            + ", ".join(missing) + ".")

    payload = {
        "model_version": MODEL_VERSION,
        "model_name": MODEL_NAME,
        "target_ticker": ticker,
        "as_of_date": as_of,
        "horizon": DEFAULT_HORIZON,
        "prediction": prediction,
        "expected_return": expected_return,
        "confidence": confidence,
        "risk_regime": risk_regime,
        "signal_score": round(signal, 6) if signal is not None else None,
        "calibration_status": "limited",
        "calibration_limitations": limitations,
        "features_used": used,
        "missing_features": missing,
        "data_as_of": as_of,
        "input_data_source": source or "unavailable",
    }
    payload.update(_safety_flags())
    return payload


def score_market_v2(ticker: str = "SPY") -> Dict:
    """Primary acceptance entrypoint: score broad-market (S&P 500 via SPY) direction.

    Thin wrapper over :func:`score_ticker_v2` that defaults to the SPY proxy and
    tags the payload as the market-level view.
    """
    payload = score_ticker_v2(ticker or "SPY")
    payload["market_proxy"] = payload["target_ticker"]
    payload["is_market_view"] = True
    return payload
