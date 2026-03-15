import os
import math
import time
import datetime as dt
import logging
import traceback
from typing import Dict, Any, List, Tuple
from urllib.parse import urlparse, urlunparse

import numpy as np
import pandas as pd
from fastapi import Body, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from prophet import Prophet
from sqlalchemy import create_engine, text
from sklearn.metrics import mean_absolute_error, mean_squared_error
from statsmodels.tsa.arima.model import ARIMA
from statsmodels.tsa.holtwinters import ExponentialSmoothing
from sklearn.linear_model import LinearRegression
from sklearn.preprocessing import MinMaxScaler
import xgboost as xgb
import yfinance as yf
from pydantic import BaseModel, Field
from multiprocessing import Process, Queue

# ===== Runtime flags (sane defaults) =====
ENABLE_LSTM = os.getenv("PREDICTOR_ENABLE_LSTM", "0").lower() in ("1", "true", "yes")
ALLOW_LIVE_PRICE = os.getenv("PREDICTOR_ALLOW_LIVE_PRICE", "1").lower() in ("1", "true", "yes")
FAST_ONLY = os.getenv("PREDICTOR_FAST_ONLY", "1").lower() in ("1", "true", "yes")

MODEL_TIMEOUT_SEC = int(os.getenv("PREDICTOR_MODEL_TIMEOUT_SEC", "15"))
TOTAL_BUDGET_SEC = int(os.getenv("PREDICTOR_TOTAL_BUDGET_SEC", "30"))
LOOKBACK_DAYS = int(os.getenv("PREDICTOR_LOOKBACK_DAYS", "180"))
XGB_ESTIMATORS = int(os.getenv("PREDICTOR_XGB_EST", "50"))

ALPHA_STRONG = float(os.getenv("ALPHA_STRONG", "0.02")) # 2% strong band
ALPHA_WEAK = float(os.getenv("ALPHA_WEAK", "0.005")) # 0.5% weak band
GOOD_ERR = float(os.getenv("GOOD_ERR", "1.0"))
WARN_ERR = float(os.getenv("WARN_ERR", "5.0"))

# Staleness control: if DB latest date < (last business day - STALE_DAYS), refresh from Yahoo
STALE_DAYS = int(os.getenv("PREDICTOR_STALE_DAYS", "3"))

# Recommendation calibration
REC_MIN_AGREEMENT = float(os.getenv("REC_MIN_AGREEMENT", "0.55"))
REC_MIN_CONF = float(os.getenv("REC_MIN_CONF", "30"))
REC_BUY_WEAK = float(os.getenv("REC_BUY_WEAK", "0.005")) # +0.5%
REC_BUY_STRONG = float(os.getenv("REC_BUY_STRONG", "0.02")) # +2%
REC_SELL_WEAK = float(os.getenv("REC_SELL_WEAK", "0.005")) # -0.5%
REC_SELL_STRONG = float(os.getenv("REC_SELL_STRONG", "0.02")) # -2%

# Agreement/confidence eligibility thresholds
EPS_DIRECTION = float(os.getenv("EPS_DIRECTION", "0.003")) # 0.30% band considered flat/neutral

if ENABLE_LSTM:
    os.environ["CUDA_VISIBLE_DEVICES"] = "-1"
    os.environ["TF_CPP_MIN_LOG_LEVEL"] = "2"
    try:
        import tensorflow as tf  # noqa: F401
        try:
            tf.config.set_visible_devices([], "GPU")
        except Exception:
            pass
        try:
            tf.config.threading.set_intra_op_parallelism_threads(2)
            tf.config.threading.set_inter_op_parallelism_threads(2)
        except Exception:
            pass
        tf.get_logger().setLevel("ERROR")
    except Exception:
        pass
    from keras.models import Sequential  # noqa: F401
    from keras.layers import LSTM, Dense  # noqa: F401
    from keras.callbacks import EarlyStopping  # noqa: F401

# ===== Logging =====
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("api_server")

print(
    f"✅ api_server.py — FAST_ONLY={FAST_ONLY} "
    f"(MODEL_TIMEOUT_SEC={MODEL_TIMEOUT_SEC}, TOTAL_BUDGET_SEC={TOTAL_BUDGET_SEC}, STALE_DAYS={STALE_DAYS})"
)

# ===== Helpers (DB URL handling) =====
def _mask_dsn(url: str) -> str:
    try:
        p = urlparse(url)
        netloc = p.netloc
        if "@" in netloc and ":" in netloc.split("@", 1)[0]:
            userpass, hostpart = netloc.split("@", 1)
            user = userpass.split(":", 1)[0]
            netloc = f"{user}:***@{hostpart}"
        return urlunparse((p.scheme, netloc, p.path, p.params, p.query, p.fragment))
    except Exception:
        return url

def _effective_db_url() -> str:
    raw = os.getenv("DB_URL")
    if not raw:
        pguser = os.getenv("PGUSER", "postgres")
        pgpass = os.getenv("PGPASSWORD")
        pghost = os.getenv("PGHOSTADDR") or os.getenv("PGHOST") or "127.0.0.1"
        pgport = os.getenv("PGPORT", "5432")
        pgdb = os.getenv("PGDATABASE") or "stock_data"
        if not pgpass:
            raise SystemExit("DB_URL not set and PGPASSWORD missing. Set DB_URL or PG* env vars.")
        raw = f"postgresql://{pguser}:{pgpass}@{pghost}:{pgport}/{pgdb}"
    raw = raw.replace("@localhost:", "@127.0.0.1:")
    return raw

# ===== App =====
app = FastAPI(
    title="Stock Prediction API",
    version="1.7.1",
    description="Ensemble: Prophet, ARIMA, XGBoost (returns), ETS, Linear, Naive, Drift (+ optional LSTM). "
                "DB/Yahoo fallback, per-model timeouts, residual alpha vs SPY, stale-data auto-refresh, "
                "and explicit rationale output."
)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# ===== DB =====
DB_URL = _effective_db_url()
logger.info(f"[DB] Effective DB_URL: {_mask_dsn(DB_URL)}")
engine = create_engine(DB_URL, pool_pre_ping=True)

_DB_OK = False
try:
    with engine.connect() as cx:
        cx.execute(text("select 1"))
    _DB_OK = True
    logger.info("[DB] Connection OK.")
except Exception as e:
    logger.error(f"[DB] Connection FAILED at startup: {e}")
    raise

# ===== Helpers =====
def last_business_day(date: dt.date | None = None) -> dt.date:
    if date is None:
        date = dt.date.today()
    d = date - dt.timedelta(days=1)
    while d.weekday() > 4:
        d -= dt.timedelta(days=1)
    return d

def insert_into_db(df: pd.DataFrame, ticker: str):
    if df.empty:
        return
    df = df.copy()
    df["ticker"] = ticker
    df.rename(columns={"ds": "date", "y": "adj_close"}, inplace=True)
    df["date"] = pd.to_datetime(df["date"])
    df["adj_close"] = pd.to_numeric(df["adj_close"], errors="coerce")
    df = df[["ticker", "date", "adj_close"]].dropna()
    with engine.begin() as conn:
        min_date = df["date"].min().strftime("%Y-%m-%d")
        max_date = df["date"].max().strftime("%Y-%m-%d")
        conn.execute(text("""
            DELETE FROM stock_prices
            WHERE ticker = :ticker AND date BETWEEN :start AND :end
        """), {"ticker": ticker, "start": min_date, "end": max_date})
        df.to_sql("stock_prices", conn, if_exists="append", index=False)

def _yf_download_resilient(ticker: str, start=None, end=None, period=None, interval="1d") -> pd.DataFrame:
    try:
        df = yf.download(ticker, start=start, end=end, period=period, interval=interval, progress=False, auto_adjust=False)
        if df is None or df.empty:
            return pd.DataFrame()
        df = df.reset_index()
        if "Adj Close" in df.columns:
            df = df[["Date", "Adj Close"]]; df.columns = ["ds", "y"]
        elif "Close" in df.columns:
            df = df[["Date", "Close"]]; df.columns = ["ds", "y"]
        else:
            return pd.DataFrame()
        df.dropna(inplace=True)
        return df
    except Exception as e:
        logger.warning(f"[YF] download failed for {ticker} ({period=}, {interval=}): {e}")
        return pd.DataFrame()

def fetch_from_yahoo(ticker, lookback_days=LOOKBACK_DAYS):
    logger.info(f"[YF] Fetching {ticker} from Yahoo Finance ...")
    end = dt.date.today() + dt.timedelta(days=1)
    start = end - dt.timedelta(days=lookback_days * 2)
    for args in [
        dict(start=start, end=end, interval="1d"),
        dict(period="2y", interval="1d"),
        dict(period="1y", interval="1d")
    ]:
        df = _yf_download_resilient(ticker, **args)
        if not df.empty:
            insert_into_db(df, ticker)
            logger.info(f"[YF] {ticker}: upserted {len(df)} rows into DB.")
            return df
    logger.error(f"[YF] Error fetching {ticker}: all attempts failed.")
    return pd.DataFrame()

def get_data_from_db(ticker, lookback_days=LOOKBACK_DAYS) -> pd.DataFrame:
    try:
        query = f"""
            SELECT date, adj_close FROM stock_prices
            WHERE ticker = :ticker
            ORDER BY date DESC
            LIMIT {lookback_days}
        """
        with engine.begin() as conn:
            df = pd.read_sql_query(text(query), con=conn, params={"ticker": ticker})
        if df.empty:
            raise ValueError("Ticker not in DB")
        df.columns = ["ds", "y"]
        df["ds"] = pd.to_datetime(df["ds"], errors="coerce")
        df["y"] = pd.to_numeric(df["y"], errors="coerce")
        df.dropna(inplace=True)
        return df.sort_values("ds")
    except Exception:
        logger.warning(f"[DB] {ticker} not in DB, falling back to Yahoo...")
        return fetch_from_yahoo(ticker, lookback_days)

_series_cache: dict = {}
_SERIES_CACHE_TTL = 1800  # 30 min

def get_fresh_series(ticker: str, lookback_days=LOOKBACK_DAYS) -> pd.DataFrame:
    cache_key = f"{ticker}_{lookback_days}"
    entry = _series_cache.get(cache_key)
    if entry and (time.time() - entry["ts"]) < _SERIES_CACHE_TTL:
        logger.info(f"[CACHE] Series HIT for {ticker}")
        return entry["df"].copy()
    df = get_data_from_db(ticker, lookback_days)
    if df is None or df.empty:
        return df
    last_db_date = pd.to_datetime(df["ds"].iloc[-1]).date()
    lb = last_business_day()
    days_stale = (lb - last_db_date).days
    if days_stale >= STALE_DAYS:
        logger.warning(f"[STALE] {ticker} last DB date {last_db_date} (stale by {days_stale}d). Refreshing via Yahoo...")
        fetch_from_yahoo(ticker, lookback_days)
        df = get_data_from_db(ticker, lookback_days)
        if not df.empty:
            logger.info(f"[STALE] {ticker} refreshed. New last DB date: {pd.to_datetime(df['ds'].iloc[-1]).date()}")
    else:
        logger.info(f"[OK] {ticker} last DB date {last_db_date} (<= {STALE_DAYS}d from last business day {lb}).")
    if df is not None and not df.empty:
        _series_cache[cache_key] = {"ts": time.time(), "df": df.copy()}
    return df

def get_current_price_with_meta(ticker, last_close: float, last_close_date: dt.date) -> Tuple[float, str, str]:
    if not ALLOW_LIVE_PRICE:
        return last_close, "db_last_close", last_close_date.strftime("%Y-%m-%d")
    # Skip yfinance on weekends or outside market hours (9:30-16:00 ET)
    import pytz
    now_et = dt.datetime.now(pytz.timezone("America/New_York"))
    is_weekend = now_et.weekday() >= 5
    is_market_hours = dt.time(9, 30) <= now_et.time() <= dt.time(16, 0)
    if is_weekend or not is_market_hours:
        logger.info(f"[LIVE] Skipping yfinance — {'weekend' if is_weekend else 'outside market hours'}")
        return last_close, "db_last_close", last_close_date.strftime("%Y-%m-%d")
    try:
        fast = yf.Ticker(ticker).fast_info
        if fast is not None:
            lp = fast.last_price or fast.last_close
            if lp:
                return float(lp), "intraday", dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    except Exception:
        pass
    try:
        info = yf.download(ticker, period="1d", interval="1m", progress=False)
        if info is not None and not info.empty and "Close" in info.columns:
            price = float(info["Close"].dropna().iloc[-1])
            idx = info.index[-1]
            as_of = getattr(idx, "strftime", lambda *_: str(idx))("%Y-%m-%d %H:%M")
            return price, "intraday", as_of
    except Exception as e:
        logger.warning(f"[LIVE] Live price error for {ticker}: {e}")
    return last_close, "db_last_close", last_close_date.strftime("%Y-%m-%d")

def calculate_backtest_metrics(y_true, y_pred):
    try:
        mae = mean_absolute_error(y_true, y_pred)
        rmse = math.sqrt(mean_squared_error(y_true, y_pred))
        mean_y = float(np.mean(y_true)) if len(y_true) else 1.0
        mae_pct = round(100 * mae / mean_y, 2) if mean_y else 0.0
        rmse_pct = round(100 * rmse / mean_y, 2) if mean_y else 0.0
        return [mae_pct, rmse_pct]
    except Exception as e:
        logger.warning(f"[METRIC] Backtest metric calculation failed: {e}")
        return [None, None]

def format_dates_as_strings(dates: List[pd.Timestamp]) -> List[str]:
    return [d.strftime("%m/%d/%y") for d in dates]

def prepare_forecast_dates(last_date: pd.Timestamp) -> pd.DatetimeIndex:
    return pd.date_range(last_date, periods=6, freq="B")[1:]

# ===== Models =====
def run_prophet(df, spy_price):
    t0 = time.time()
    results, ratios, metrics = {}, {}, {}
    try:
        model = Prophet()
        model.fit(df)
        future = model.make_future_dataframe(periods=5, freq="B")
        forecast = model.predict(future)
        pred = forecast.tail(5)["yhat"].round(2).tolist()
        results["Prophet"] = pred
        ratios["Prophet"] = [round(p / spy_price, 4) for p in pred] if spy_price else [None]*5
        y_pred = forecast.loc[:len(df)-1, "yhat"]
        metrics["Prophet"] = calculate_backtest_metrics(df["y"][:len(y_pred)], y_pred)
    except Exception as e:
        logger.warning(f"[MODEL] Prophet failed: {e}")
    finally:
        logger.info(f"[MODEL] Prophet in {time.time()-t0:.2f}s")
    return results, ratios, metrics

def run_arima(df, spy_price):
    t0 = time.time()
    results, ratios, metrics = {}, {}, {}
    try:
        series = df.set_index("ds")["y"]
        model = ARIMA(series, order=(1, 1, 0))
        fit = model.fit()
        pred = fit.forecast(steps=5).round(2).tolist()
        results["ARIMA"] = pred
        ratios["ARIMA"] = [round(p / spy_price, 4) for p in pred] if spy_price else [None]*5
        fitted = fit.fittedvalues
        y_true = series[-len(fitted):]
        metrics["ARIMA"] = calculate_backtest_metrics(y_true, fitted)
    except Exception as e:
        logger.warning(f"[MODEL] ARIMA failed: {e}")
    finally:
        logger.info(f"[MODEL] ARIMA in {time.time()-t0:.2f}s")
    return results, ratios, metrics

def run_xgboost(df, spy_price):
    t0 = time.time()
    results, ratios, metrics = {}, {}, {}
    try:
        work = df.copy().set_index("ds")
        y = work["y"].astype(float)
        r = np.log(y / y.shift(1))
        feats = pd.DataFrame({
            "r1": r.shift(1), "r2": r.shift(2), "r3": r.shift(3),
            "ma5": r.rolling(5).mean().shift(1),
            "ma10": r.rolling(10).mean().shift(1),
            "vol20": r.rolling(20).std().shift(1)
        }).dropna()
        target = r.reindex(feats.index).shift(-1).dropna()
        X = feats.loc[target.index]
        model = xgb.XGBRegressor(
            n_estimators=XGB_ESTIMATORS, max_depth=3, learning_rate=0.1,
            subsample=0.9, colsample_bytree=0.9,
            objective="reg:squarederror", reg_alpha=0.0, reg_lambda=1.0, verbosity=0
        )
        model.fit(X.values, target.values)
        pred_r = pd.Series(model.predict(X.values), index=X.index)
        y_shift = y.shift(1).reindex(pred_r.index)
        yhat = (y_shift * np.exp(pred_r)).dropna()
        common = y.loc[yhat.index]
        metrics["XGBoost"] = calculate_backtest_metrics(common.values, yhat.values)
        last_price = float(y.iloc[-1])
        last_r1 = float(r.iloc[-1])
        last_r2 = float(r.iloc[-2]) if len(r) > 1 else last_r1
        last_r3 = float(r.iloc[-3]) if len(r) > 2 else last_r2
        ma5 = float(r.tail(5).mean())
        ma10 = float(r.tail(10).mean())
        vol20 = float(r.tail(20).std() or 0.0)
        preds = []
        r1, r2, r3 = last_r1, last_r2, last_r3
        p = last_price
        for _ in range(5):
            f = np.array([[r1, r2, r3, ma5, ma10, vol20]])
            rr = float(np.clip(model.predict(f)[0], -0.15, 0.15))
            p = float(p * math.exp(rr))
            preds.append(round(p, 2))
            r3, r2, r1 = r2, r1, rr
            ma5 = 0.8*ma5 + 0.2*rr
            ma10 = 0.9*ma10 + 0.1*rr
            vol20 = float(np.sqrt(max(1e-8, 0.95*vol20**2 + 0.05*rr**2)))
        results["XGBoost"] = preds
        ratios["XGBoost"] = [round(p / spy_price, 4) for p in preds] if spy_price else [None]*5
    except Exception as e:
        logger.warning(f"[MODEL] XGBoost failed: {e}")
    finally:
        logger.info(f"[MODEL] XGBoost in {time.time()-t0:.2f}s")
    return results, ratios, metrics

def run_ets(df, spy_price):
    t0 = time.time()
    results, ratios, metrics = {}, {}, {}
    try:
        series = df.set_index("ds")["y"]
        model = ExponentialSmoothing(series, trend="add", seasonal=None, initialization_method="estimated")
        fit = model.fit(optimized=True)
        fc = fit.forecast(5)
        pred = fc.round(2).tolist()
        results["ETS"] = pred
        ratios["ETS"] = [round(p / spy_price, 4) for p in pred] if spy_price else [None]*5
        metrics["ETS"] = calculate_backtest_metrics(series[-len(fit.fittedvalues):], fit.fittedvalues)
    except Exception as e:
        logger.warning(f"[MODEL] ETS failed: {e}")
    finally:
        logger.info(f"[MODEL] ETS in {time.time()-t0:.2f}s")
    return results, ratios, metrics

def run_linear(df, spy_price):
    t0 = time.time()
    results, ratios, metrics = {}, {}, {}
    try:
        y = df["y"].values.astype(float)
        X = np.arange(len(y)).reshape(-1, 1)
        reg = LinearRegression().fit(X, y)
        Xf = np.arange(len(y), len(y)+5).reshape(-1, 1)
        pred = reg.predict(Xf).round(2).tolist()
        results["LinearTrend"] = pred
        ratios["LinearTrend"] = [round(p / spy_price, 4) for p in pred] if spy_price else [None]*5
        metrics["LinearTrend"] = calculate_backtest_metrics(y, reg.predict(X))
    except Exception as e:
        logger.warning(f"[MODEL] LinearTrend failed: {e}")
    finally:
        logger.info(f"[MODEL] LinearTrend in {time.time()-t0:.2f}s")
    return results, ratios, metrics

def run_naive(df, spy_price):
    t0 = time.time()
    results, ratios, metrics = {}, {}, {}
    try:
        last = float(df["y"].iloc[-1])
        pred = [round(last, 2)] * 5
        results["Naive"] = pred
        ratios["Naive"] = [round(p / spy_price, 4) for p in pred] if spy_price else [None]*5
        y = df["y"].values.astype(float)
        yhat = np.roll(y, 1); yhat[0] = y[0]
        metrics["Naive"] = calculate_backtest_metrics(y, yhat)
    except Exception as e:
        logger.warning(f"[MODEL] Naive failed: {e}")
    finally:
        logger.info(f"[MODEL] Naive in {time.time()-t0:.2f}s")
    return results, ratios, metrics

def run_drift(df, spy_price):
    t0 = time.time()
    results, ratios, metrics = {}, {}, {}
    try:
        y = df["y"].astype(float)
        r = np.log(y / y.shift(1))
        mu = float(r.tail(20).mean())
        last = float(y.iloc[-1])
        preds = []
        p = last
        for _ in range(5):
            p = float(p * math.exp(mu))
            preds.append(round(p, 2))
        results["Drift"] = preds
        ratios["Drift"] = [round(p / spy_price, 4) for p in preds] if spy_price else [None]*5
        if not np.isnan(mu):
            yh = y.shift(1) * np.exp(mu)
            yh = yh.fillna(method="bfill")
            metrics["Drift"] = calculate_backtest_metrics(y.values, yh.values)
    except Exception as e:
        logger.warning(f"[MODEL] Drift failed: {e}")
    finally:
        logger.info(f"[MODEL] Drift in {time.time()-t0:.2f}s")
    return results, ratios, metrics

def run_sma(df, spy_price, window=5):
    t0 = time.time()
    results, ratios, metrics = {}, {}, {}
    try:
        y = df["y"].astype(float)
        sm = y.rolling(window=window, min_periods=1).mean()
        last_mean = float(sm.iloc[-1])
        r = np.log(y / y.shift(1))
        mu = float(r.tail(20).mean())
        preds = []
        p = last_mean
        for _ in range(5):
            p = float(p * math.exp(mu))
            preds.append(round(p, 2))
        results["SMA"] = preds
        ratios["SMA"] = [round(p / spy_price, 4) for p in preds] if spy_price else [None]*5
        metrics["SMA"] = calculate_backtest_metrics(y.values, sm.values)
    except Exception as e:
        logger.warning(f"[MODEL] SMA failed: {e}")
    finally:
        logger.info(f"[MODEL] SMA in {time.time()-t0:.2f}s")
    return results, ratios, metrics

def run_lstm(df, spy_price):
    results, ratios, metrics = {}, {}, {}
    if not ENABLE_LSTM or FAST_ONLY:
        return results, ratios, metrics
    t0 = time.time()
    try:
        df = df.tail(220).copy()
        scaler = MinMaxScaler()
        scaled = scaler.fit_transform(df[["y"]].astype(float))
        X, y = [], []
        for i in range(60, len(scaled)):
            X.append(scaled[i-60:i]); y.append(scaled[i])
        X, y = np.array(X), np.array(y)
        if len(X) == 0:
            raise ValueError("Insufficient data for LSTM")
        X = X.reshape((X.shape[0], X.shape[1], 1))
        from keras.models import Sequential
        from keras.layers import LSTM, Dense
        from keras.callbacks import EarlyStopping
        model = Sequential()
        model.add(LSTM(32, return_sequences=True, input_shape=(60, 1)))
        model.add(LSTM(32))
        model.add(Dense(1))
        model.compile(loss="mean_squared_error", optimizer="adam")
        es = EarlyStopping(monitor="loss", patience=0, restore_best_weights=True)
        model.fit(X, y, epochs=2, batch_size=32, verbose=0, callbacks=[es])
        last_input = scaled[-60:].reshape(1, 60, 1)
        pred_scaled = []
        for _ in range(5):
            pred = model.predict(last_input, verbose=0)[0][0]
            pred_scaled.append(pred)
            last_input = np.append(last_input[:, 1:, :], [[[pred]]], axis=1)
        pred = scaler.inverse_transform(np.array(pred_scaled).reshape(-1, 1)).flatten().round(2).tolist()
        results["LSTM"] = pred
        ratios["LSTM"] = [round(p / spy_price, 4) for p in pred] if spy_price else [None]*5
        y_pred_inv = scaler.inverse_transform(model.predict(X, verbose=0)).flatten()
        y_true_inv = scaler.inverse_transform(y).flatten()
        metrics["LSTM"] = calculate_backtest_metrics(y_true_inv, y_pred_inv)
    except Exception as e:
        logger.warning(f"[MODEL] LSTM failed: {e}")
    finally:
        logger.info(f"[MODEL] LSTM in {time.time()-t0:.2f}s")
    return results, ratios, metrics

# ===== Safe runner =====
def _model_worker(func, df, spy_price, q: Queue):
    try:
        r, rr, m = func(df, spy_price)
        q.put(("ok", r, rr, m, None))
    except Exception as e:
        q.put(("err", {}, {}, {}, str(e)))

def run_with_timeout(func, df, spy_price, timeout_sec: int):
    import os, signal
    q = Queue(maxsize=1)
    p = Process(target=_model_worker, args=(func, df, spy_price, q))
    p.start()
    p.join(timeout=timeout_sec)
    if p.is_alive():
        logger.warning(f"[TIMEOUT] Model {func.__name__} timed out after {timeout_sec}s; terminating.")
        try:
            import psutil
            parent = psutil.Process(p.pid)
            for child in parent.children(recursive=True):
                try: child.kill()
                except psutil.NoSuchProcess: pass
            parent.kill()
        except Exception:
            try: os.killpg(os.getpgid(p.pid), signal.SIGKILL)
            except Exception: p.kill()
        p.join(timeout=5)
        return {}, {}, {}, f"timeout@{timeout_sec}s"
    if not q.empty():
        status, r, rr, m, err = q.get()
        if status == "ok":
            return r, rr, m, None
        logger.warning(f"[MODEL] {func.__name__} failed: {err}")
    return {}, {}, {}, "unknown_error"

# ===== Schemas =====
class TickerRequest(BaseModel):
    ticker: str = Field(..., example="AAPL", description="Ticker symbol")

# ===== Utilities =====
def avg_forecast_series(results: Dict[str, List[float]]) -> List[float]:
    if not results:
        return []
    stacked = [preds for preds in results.values() if isinstance(preds, list) and len(preds) == 5]
    if not stacked:
        return []
    return np.round(np.nanmean(np.array(stacked, dtype=float), axis=0), 2).tolist()

def robust_avg_series(results: Dict[str, List[float]], metrics: Dict[str, List[float]]) -> List[float]:
    eligible_models = [m for m, v in metrics.items()
                       if isinstance(v, list) and len(v) == 2 and
                       all(isinstance(x, (int, float)) for x in v) and
                       v[0] is not None and v[1] is not None and v[0] <= WARN_ERR and v[1] <= WARN_ERR]
    day_matrix = [results[m] for m in eligible_models
                  if isinstance(results.get(m), list) and len(results[m]) == 5]
    if day_matrix:
        return np.round(np.nanmedian(np.array(day_matrix, dtype=float), axis=0), 2).tolist()
    return avg_forecast_series(results)

def compute_agreement_confidence(
    results: Dict[str, List[float]], current_price: float, eligible_models: List[str] | None = None
) -> Tuple[float | None, float | None, Dict[str, Any]]:
    details: Dict[str, Any] = {
        "n_total_models": 0, "n_eligible": 0, "n_bullish": 0, "n_bearish": 0, "n_flat": 0,
        "eligible_models": [], "ineligible_models": []
    }
    if current_price is None or current_price <= 0:
        return None, None, {**details, "reason": "invalid current_price"}
    day5_values = []
    models_iter = [(m, p) for m, p in results.items() if not eligible_models or m in eligible_models]
    for model, preds in models_iter:
        if not isinstance(preds, list) or len(preds) != 5:
            details["ineligible_models"].append({"model": model, "reason": "bad_preds"})
            continue
        details["n_total_models"] += 1
        day5 = float(preds[-1])
        rel = (day5 / current_price) - 1.0
        if abs(rel) < EPS_DIRECTION:
            details["n_flat"] += 1
            details["ineligible_models"].append({"model": model, "reason": "flat_within_band", "rel": round(rel*100, 2)})
            continue
        details["n_eligible"] += 1
        day5_values.append(day5)
        vote = "bullish" if rel > 0 else "bearish"
        if rel > 0:
            details["n_bullish"] += 1
        else:
            details["n_bearish"] += 1
        details["eligible_models"].append({"model": model, "day5": day5, "rel_pct": round(rel*100, 2), "vote": vote})
    agreement = None
    confidence = None
    if details["n_eligible"] >= 2:
        denom = details["n_bullish"] + details["n_bearish"]
        if denom > 0:
            agreement = round(max(details["n_bullish"], details["n_bearish"]) / denom, 2)
        if len(day5_values) >= 3:
            m = float(np.mean(day5_values))
            if m != 0.0:
                cv_pct = float(np.std(day5_values) / m * 100.0)
                confidence = max(0.0, min(100.0, round(100.0 - cv_pct, 2)))
    return agreement, confidence, details

def zscore_abs(avg_day5: float, current_price: float, hist: pd.Series) -> float:
    try:
        std = float(hist.tail(60).std(ddof=1))
        return round(abs((avg_day5 - current_price) / std), 2) if std > 0 else 0.0
    except Exception:
        return 0.0

def make_recommendation(ensemble_day5: float, current_price: float, agreement: float | None, confidence: float | None) -> str:
    if current_price <= 0:
        return "Hold"
    rel = (ensemble_day5 / current_price) - 1.0
    agr = agreement or 0.0
    conf = confidence or 0.0
    if agr >= REC_MIN_AGREEMENT and conf >= REC_MIN_CONF:
        if rel >= REC_BUY_STRONG: return "Strong Buy"
        if rel >= REC_BUY_WEAK: return "Buy"
        if rel <= -REC_SELL_STRONG: return "Strong Sell"
        if rel <= -REC_SELL_WEAK: return "Sell"
    return "Hold"

def build_predictions_list(results: Dict[str, List[float]]) -> List[Dict[str, Any]]:
    return [
        {"model": model, "horizon_day": i, "point": float(p), "lo": None, "hi": None}
        for model, preds in results.items()
        if isinstance(preds, list) and len(preds) == 5
        for i, p in enumerate(preds, start=1)
    ]

# ===== Routes =====
@app.get("/ping")
async def ping():
    return {"status": "ok"}

@app.on_event("startup")
async def warmup():
    import asyncio
    logger.info("[WARMUP] Pre-fetching AAPL and SPY...")
    try:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, lambda: get_fresh_series("AAPL", lookback_days=LOOKBACK_DAYS))
        await loop.run_in_executor(None, lambda: get_fresh_series("SPY", lookback_days=LOOKBACK_DAYS))
        logger.info("[WARMUP] Done — DB connections warm.")
    except Exception as e:
        logger.warning(f"[WARMUP] Failed: {e}")

@app.get("/healthz")
async def healthz():
    return {"status": "ok", "db_ok": _DB_OK, "fast_only": FAST_ONLY}

@app.get("/config")
async def config():
    return {
        "enable_lstm": ENABLE_LSTM, "allow_live_price": ALLOW_LIVE_PRICE,
        "fast_only": FAST_ONLY, "model_timeout_sec": MODEL_TIMEOUT_SEC,
        "total_budget_sec": TOTAL_BUDGET_SEC, "lookback_days": LOOKBACK_DAYS,
        "xgb_estimators": XGB_ESTIMATORS, "good_err": GOOD_ERR, "warn_err": WARN_ERR,
        "alpha_strong": ALPHA_STRONG, "alpha_weak": ALPHA_WEAK, "stale_days": STALE_DAYS,
        "rec_min_agreement": REC_MIN_AGREEMENT, "rec_min_conf": REC_MIN_CONF,
        "eps_direction": EPS_DIRECTION,
    }

def run_model_suite(df: pd.DataFrame, spy_price: float, budget_left: float):
    funcs = [run_drift, run_linear, run_xgboost, run_naive, run_sma]
    if not FAST_ONLY:
        funcs = [run_drift, run_linear, run_ets, run_arima, run_xgboost, run_naive, run_sma]
    if ENABLE_LSTM:
        funcs.append(run_lstm)
    results, ratios, metrics = {}, {}, {}
    ran, skipped, errors = [], [], {}
    t0 = time.time()
    for f in funcs:
        if time.time() - t0 >= budget_left:
            skipped.append(f.__name__)
            logger.warning(f"[BUDGET] Skipping {f.__name__}: budget exceeded.")
            continue
        r, rr, m, err = run_with_timeout(f, df.copy(), spy_price if spy_price else 1.0, MODEL_TIMEOUT_SEC)
        if r:
            results.update(r); ratios.update(rr); metrics.update(m); ran.extend(list(r.keys()))
        else:
            errors[f.__name__] = err or "failed"
    return results, ratios, metrics, ran, skipped, errors

@app.post("/predict_all_models/", response_model=Dict[str, Any])
async def predict_all(data: TickerRequest = Body(...)):
    req_start = time.time()
    try:
        ticker = data.ticker.upper()
        logger.info(f"[REQ] {ticker}")
        df = get_fresh_series(ticker, lookback_days=LOOKBACK_DAYS).tail(LOOKBACK_DAYS).copy()
        df.dropna(inplace=True)
        if df.empty:
            return {"error": "No data available for forecast."}
        spy_df = get_fresh_series("SPY", lookback_days=LOOKBACK_DAYS).tail(LOOKBACK_DAYS).copy()
        spy_price_for_ratios = float(spy_df["y"].iloc[-1]) if not spy_df.empty else None
        last_close = float(df["y"].iloc[-1])
        last_date = pd.to_datetime(df["ds"].iloc[-1]).date()
        current_price, price_source, price_as_of = get_current_price_with_meta(ticker, last_close, last_date)
        spy_last_close = float(spy_df["y"].iloc[-1]) if not spy_df.empty else None
        spy_last_date = pd.to_datetime(spy_df["ds"].iloc[-1]).date() if not spy_df.empty else dt.date.today()
        spy_current_price, spy_price_source, spy_price_as_of = (
            (spy_last_close, "db_last_close", spy_last_date.strftime("%Y-%m-%d"))
            if spy_last_close is not None else (None, "", "")
        )
        forecast_dates = prepare_forecast_dates(df["ds"].max())
        hist_dates = df["ds"].tolist()[-14:] + [pd.to_datetime(price_as_of)]
        hist_prices = df["y"].tolist()[-14:] + [current_price]
        total_budget = TOTAL_BUDGET_SEC
        ticker_budget = max(6.0, total_budget * 0.7)
        spy_budget = max(4.0, total_budget - ticker_budget)
        results, ratios, metrics, ran_models, skipped_models, model_errors = \
            run_model_suite(df, spy_price_for_ratios or 1.0, ticker_budget)
        if not results:
            return {"error": "Model suite failed or timed out.", "ran_models": ran_models,
                    "skipped_models": skipped_models, "model_errors": model_errors}
        robust_avg = robust_avg_series(results, metrics)
        avg_forecast = avg_forecast_series(results)
        ensemble_day1 = float(robust_avg[0] if robust_avg else (avg_forecast[0] if avg_forecast else current_price))
        ensemble_day5 = float(robust_avg[-1] if robust_avg else (avg_forecast[-1] if avg_forecast else current_price))
        eligible_for_consensus = [m for m, v in metrics.items()
                                   if isinstance(v, list) and v[0] is not None and v[1] is not None
                                   and v[0] <= WARN_ERR and v[1] <= WARN_ERR]
        agreement, confidence_score, elig_details = compute_agreement_confidence(results, current_price, eligible_for_consensus)
        n_models = int(elig_details.get("n_total_models", 0))
        spy_avg_forecast = []; spy_ret_pct = []
        if spy_df is not None and not spy_df.empty and spy_current_price is not None:
            # SPY only needs fast models for alpha — never run Prophet/ARIMA on it
            spy_fast_results = {}
            for f in [run_drift, run_linear, run_naive]:
                r, _, _, _ = run_with_timeout(f, spy_df.copy(), spy_current_price, 8)
                spy_fast_results.update(r)
            spy_results = spy_fast_results
            spy_avg_forecast = avg_forecast_series(spy_results)
            spy_ret_pct = [round(100.0 * (p / spy_current_price - 1.0), 2) for p in spy_avg_forecast] if spy_avg_forecast else []
        alpha_by_day_pct = []; spy_ratio = []
        if robust_avg and spy_avg_forecast and spy_current_price:
            for p_t, p_s in zip(robust_avg, spy_avg_forecast):
                r_t = p_t / current_price - 1.0 if current_price else 0.0
                r_s = p_s / spy_current_price - 1.0 if spy_current_price else 0.0
                alpha_by_day_pct.append(round(100.0 * (r_t - r_s), 2))
                spy_ratio.append(round(p_t / p_s, 4) if p_s else None)
        elif robust_avg:
            for p_t in robust_avg:
                alpha_by_day_pct.append(round(100.0 * (p_t / current_price - 1.0), 2) if current_price else 0.0)
                spy_ratio.append(None)
        alpha_day5_bp = int(round((alpha_by_day_pct[-1] / 100.0 if alpha_by_day_pct else 0.0) * 10000))
        z = zscore_abs(ensemble_day5, current_price, df["y"])
        recommendation = make_recommendation(ensemble_day5, current_price, agreement, confidence_score)
        rel1 = (ensemble_day1 / current_price - 1.0) if current_price else 0.0
        rel5 = (ensemble_day5 / current_price - 1.0) if current_price else 0.0
        reasons = []
        if eligible_for_consensus:
            reasons.append("Models eligible for consensus: " + ", ".join(sorted(eligible_for_consensus)))
        else:
            reasons.append("No models eligible for consensus — using robust average across all models.")
        if agreement is None:
            reasons.append(f"Agreement could not be computed (need ≥2 directional models).")
        else:
            reasons.append(f"Agreement: {int(round(agreement*100))}% "
                           f"(bullish={elig_details.get('n_bullish',0)}, bearish={elig_details.get('n_bearish',0)}).")
        if confidence_score is not None:
            reasons.append(f"Confidence: {confidence_score:.0f}%.")
        reasons.append(f"D+1={ensemble_day1:.2f} ({rel1*100:.2f}%), D+5={ensemble_day5:.2f} ({rel5*100:.2f}%).")
        if alpha_by_day_pct:
            reasons.append(f"Residual alpha vs SPY D+5: {alpha_by_day_pct[-1]:.2f}%.")
        reasons.append(f"Z-score 5D move: {z:.2f}.")
        per_model_summary = [
            {"model": m, "d1": round(float(preds[0]), 2), "d5": round(float(preds[-1]), 2),
             "d5_change_pct": round((float(preds[-1])/current_price - 1.0)*100, 2) if current_price else 0.0,
             "direction": "Up" if (float(preds[-1])/current_price - 1.0) > EPS_DIRECTION
             else ("Down" if (float(preds[-1])/current_price - 1.0) < -EPS_DIRECTION else "Flat")}
            for m, preds in results.items() if isinstance(preds, list) and len(preds) == 5
        ]
        table_rows = [
            {**{"Date": d.strftime("%m/%d/%y")},
             **{m: float(preds[i]) for m, preds in results.items() if isinstance(preds, list) and len(preds) == 5},
             "Robust Avg": float(robust_avg[i]) if robust_avg else None,
             "SPY Ret %": float(spy_ret_pct[i]) if i < len(spy_ret_pct) else None,
             "Alpha %": float(alpha_by_day_pct[i]) if i < len(alpha_by_day_pct) else None,
             "SPY Ratio": float(spy_ratio[i]) if i < len(spy_ratio) and spy_ratio[i] is not None else None}
            for i, d in enumerate(forecast_dates)
        ]
        resp = {
            "ticker": ticker, "current_price": round(float(current_price), 2),
            "yesterday_close": round(float(last_close), 2), "price_source": price_source,
            "price_as_of": price_as_of,
            "hist_dates": format_dates_as_strings(hist_dates),
            "hist_prices": [round(float(p), 2) for p in hist_prices],
            "forecast_dates": format_dates_as_strings(list(forecast_dates)),
            "results": results, "metrics": metrics, "ran_models": ran_models,
            "skipped_models": skipped_models, "model_errors": model_errors,
            "ensemble_day1": round(float(ensemble_day1), 2),
            "ensemble_day5": round(float(ensemble_day5), 2),
            "d1_change_pct": round(rel1*100, 2), "d5_change_pct": round(rel5*100, 2),
            "agreement": float(agreement) if agreement is not None else None,
            "confidence": float(confidence_score) if confidence_score is not None else None,
            "eligibility": elig_details, "alpha_by_day_pct": alpha_by_day_pct,
            "alpha_day5_bp": alpha_day5_bp, "residual_alpha_bp": alpha_day5_bp,
            "zscore": float(z), "avg_forecast": avg_forecast, "robust_forecast": robust_avg,
            "spy_avg_forecast": spy_avg_forecast, "recommendation": recommendation,
            "rationale": reasons, "per_model_summary": per_model_summary, "n_models": n_models,
            "predictions": build_predictions_list(results), "table_rows": table_rows,
            "spy_current_price": round(float(spy_current_price), 2) if spy_current_price else None,
            "spy_price_source": spy_price_source, "spy_price_as_of": spy_price_as_of,
        }
        logger.info(f"[DONE] {ticker}: ran={ran_models} ensemble5={resp['ensemble_day5']} "
                    f"d5={resp['d5_change_pct']}% agree={resp['agreement']} conf={resp['confidence']} "
                    f"in {time.time()-req_start:.2f}s")
        return resp
    except Exception as e:
        logger.error(f"[ERROR] Prediction failed: {e}\n{traceback.format_exc()}")
        return {"error": str(e)}

@app.get("/predict/{ticker}")
async def predict_get(ticker: str):
    return await predict_all(TickerRequest(ticker=ticker))
