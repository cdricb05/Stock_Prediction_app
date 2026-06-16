# Phase 2 — Prediction Model Rebuild Plan

_Authored 2026-06-16. Architecture + implementation plan for rebuilding the GCP

> Validation summary: keep the serving spine; rebuild the signal / confidence / risk engine; no fake news/sentiment/macro features; no production trading until walk-forward evidence is positive.
prediction **model layer** while preserving the existing FastAPI service contract
consumed by Paper Trader._

> **Decision already taken (business):** We are **not** waiting for the full
> walk-forward run to choose an architecture. A code audit of `api_server.py` is
> sufficient to conclude the current model layer is not aligned with the target
> (cross-sectional, calibrated, point-in-time ranking). We **keep the serving
> spine** and **rebuild the signal / confidence / risk engine**.
>
> This document changes **no code**. It is a plan. No deploy, no service restart,
> no package install, no env/secret change, no Paper Trader change, no orders.

---

## 0. Why a rebuild, in one paragraph (audit conclusion)

`api_server.py` trains a handful of **price-only** models (`run_drift`,
`run_linear`, `run_xgboost`, `run_naive`, `run_sma`; optionally Prophet/ARIMA/
ETS/LSTM) **per request, on the adjusted-close series of one ticker at a time**
(`run_model_suite` → `run_with_timeout`). It produces a 5-business-day **point
price forecast**, a `recommendation` from fixed return bands
(`make_recommendation`), an `agreement` (vote share), and a `confidence` that is
literally `100 − coefficient_of_variation` of the member forecasts
(`compute_agreement_confidence`) — a **dispersion statistic, not a probability**.
`build_predictions_list` hard-codes `"lo": None, "hi": None` — there are **no
prediction intervals**. The reported per-model MAE%/RMSE% (`calculate_backtest_
metrics`) are **in-sample fit error**, not out-of-sample skill. There is no
feature store, no model registry, no persisted prediction table, and the model is
fit at request time inside the web worker. None of this is a quant-grade signal,
and none of it is salvageable as a *signal*. The **service around it is fine** and
worth keeping.

---

## 1. What we KEEP (the serving spine)

These stay essentially as-is. They are the contract Paper Trader depends on and
they are not the problem.

| Component | Where | Keep because |
|---|---|---|
| FastAPI app + CORS | `api_server.py:117-124` | Stable HTTP surface; Paper Trader already integrates against it. |
| `GET /predict/{ticker}` | `api_server.py:850-852` | Primary Paper Trader entry point; keep path + response shape. |
| `POST /predict_all_models/` | `api_server.py:720-848` | Primary detail endpoint; keep path + response shape, swap the *internals*. |
| Health/ops endpoints | `/ping` `:667`, `/healthz` `:683`, `/config` `:687` | Liveness/readiness + config introspection used in ops. |
| DB connection pattern | `_effective_db_url` `:102`, `create_engine(..., pool_pre_ping=True)` `:129` | Same Postgres, same `stock_prices` table, same masking/secret handling. Reuse verbatim. |
| Startup warmup | `warmup()` `:671` | Keeps DB pool warm; harmless and useful. |
| Series loading / staleness / yahoo fallback | `get_fresh_series` `:227`, `fetch_from_yahoo` `:186` | Reuse as the **price ingestion** layer feeding the feature pipeline. |
| Paper Trader integration contract | response dict `:819-841` | We add fields; we do **not** remove or rename existing ones (see §6). |

**The contract we must not break** — Paper Trader currently reads these response
fields (from `predict_all` at `api_server.py:819-841`):
`ticker, current_price, yesterday_close, price_source, price_as_of, hist_dates,
hist_prices, forecast_dates, results, metrics, ensemble_day1, ensemble_day5,
d1_change_pct, d5_change_pct, agreement, confidence, recommendation, rationale,
per_model_summary, n_models, predictions, table_rows, spy_current_price, ...`.
Phase 2 keeps every one of these keys populated (with sane values) and **adds**
new keys. Removal/rename is a separate, coordinated change (Phase 2F).

---

## 2. What we REBUILD (the model layer)

| # | Current behavior (audited) | Problem | Replacement |
|---|---|---|---|
| 1 | Price-only per-ticker models, fit per request | No cross-sectional view; no real features; slow path | Cross-sectional feature pipeline + trained ranking model (§3, §4, §5) |
| 2 | `confidence = 100 − CV` of forecasts (`:631-635`) | Not a probability, not calibrated | Calibrated `prob_outperform_spy` via isotonic/Platt on walk-forward folds (§3 calibration) |
| 3 | Fixed return-band thresholds in `make_recommendation` (`:645-656`, `REC_*`) | Hand-set, not learned/validated | Decision rule driven by **calibrated probability × expected excess return**, thresholds chosen on walk-forward, not by hand |
| 4 | `lo`/`hi` hard-coded `None` (`:660`) | No downside/uncertainty | Prediction intervals (quantile or conformal) → real `lo`/`hi` + risk bands (§3 risk layer) |
| 5 | No feature store | Features recomputed ad hoc, no leakage guarantee | Versioned point-in-time feature table (§3 feature pipeline) |
| 6 | Request-time model fitting (`run_model_suite` `:699`) | Latency, instability, no reproducibility | **Scheduled** training + scheduled batch inference; API only *reads* (§6) |
| 7 | No model artifacts | Cannot reproduce or roll back a prediction | Model registry with versioned artifacts + metadata (§3 registry) |
| 8 | No walk-forward-calibrated probabilities | "Confidence" is meaningless to a trader | Probabilities calibrated and validated **only** by walk-forward (§5, §8) |

Everything in §2 is the "signal / confidence / risk engine." That is the rebuild.

---

## 3. Target architecture

Pipeline-first, batch-precompute, API-reads-cache. All new code lands under
`research/` and a new `model/` package; the live API gains a thin read path
behind a feature flag.

```
                price ingestion (KEEP: get_fresh_series / stock_prices)
                              │
                              ▼
 (A) FEATURE PIPELINE  ──►  feature_snapshots table        (point-in-time, ≤ as_of)
                              │
        ┌─────────────────────┴─────────────────────┐
        ▼ (offline, scheduled)                       ▼ (offline, scheduled)
 (B) TRAINING PIPELINE                        (D) INFERENCE PIPELINE
   walk-forward folds                           load latest artifact
   fit ranking model                            score today's universe
   fit calibrator + intervals                   write predictions table
   write artifact + metrics                            │
        │                                              ▼
        ▼                                    (E) prediction_outputs table
 (C) MODEL REGISTRY  ◄───────────────────────────────  (latest, versioned)
   artifacts/<model>/<version>/...                      │
   metadata.json (window, IC, calib, gate)              ▼
                                              (F) API READ PATH (KEEP service)
                                                /predict, /predict_all_models
                                                returns latest precomputed row
                                                + KEEP legacy fields
```

**(A) Feature pipeline** — `model/features.py`. Input: de-duplicated
`stock_prices` slices (`date <= as_of`), reusing `walk_forward_dataset.load_series`
(already de-dups via `DISTINCT ON (date)` and bounds by `end_date`). Output: a
tidy `feature_snapshots` table keyed `(ticker, as_of_date, feature_set_version)`.
Every feature must pass a leakage test (uses only data ≤ as_of). Cross-sectional:
features are computed per as_of_date across the **whole universe** so they can be
ranked/standardized within a date (z-score within date).

**(B) Training pipeline** — `model/train.py`. Walk-forward only (no random
split). For each fold: fit the ranking model on as_of dates in the train window,
fit the probability calibrator and the interval estimator on a held-out slice of
the train window, evaluate on the out-of-sample fold. Reuse
`research/walk_forward_dataset.py` for labels and `research/metrics.py` for IC /
precision@K / calibration / drawdown. Writes an artifact + a `metadata.json`.

**(C) Model registry / artifacts** — `model/registry.py` + `artifacts/`. Each
trained model is a directory `artifacts/<model_name>/<version>/` containing the
serialized estimator(s), the calibrator, the interval model, the feature-set
version, the training window, and the walk-forward metrics that gated it. A
`current` pointer (symlink or a row in a small `model_registry` table) names the
version the inference pipeline should use. Nothing is promoted to `current`
unless it passes §8 gates.

**(D) Inference pipeline** — `model/infer.py`. Scheduled (daily, after market
close / data refresh). Loads `current` artifact, builds today's features for the
universe, scores, calibrates, attaches intervals/risk bands, and **writes** to
`prediction_outputs`. Pure batch; no web request involved.

**(E) Prediction table / output schema** — new table `prediction_outputs`:

```
prediction_outputs(
  ticker            text,
  as_of_date        date,        -- the data date the prediction was made on
  generated_at      timestamptz, -- when inference ran
  model_version     text,        -- FK to registry
  feature_set_version text,
  horizon_days      int,         -- 5
  pred_excess_return_5d  double,  -- expected 5d return vs SPY (the score)
  prob_outperform_spy    double,  -- CALIBRATED probability in [0,1]
  pred_return_5d         double,  -- expected absolute 5d return (for legacy fields)
  lo_return_5d           double,  -- lower interval (e.g. 10th pct)
  hi_return_5d           double,  -- upper interval (e.g. 90th pct)
  rank_in_universe       int,     -- cross-sectional rank on as_of_date
  recommendation         text,    -- derived from prob × expected excess + gate
  risk_band              text,    -- low/med/high from interval width / vol
  PRIMARY KEY (ticker, as_of_date, model_version)
)
```

**(F) Calibration layer** — `model/calibrate.py`. Maps raw model score →
calibrated `prob_outperform_spy`. Isotonic regression (preferred) or Platt
(logistic) fit on walk-forward out-of-fold predictions. Reliability/Brier
reported; gate in §8 requires monotone calibration buckets.

**(G) Risk layer** — intervals + a simple `risk_band`. Interval via quantile
regression (if XGBoost available, `reg:quantileerror`) or conformal prediction on
walk-forward residuals. Risk band from interval width relative to the ticker's
recent realized vol. Feeds `lo`/`hi` (currently `None`) and a downside-aware
decision rule.

**(H) Explainability / feature attribution** — `model/explain.py`. Per
prediction, store the top contributing features (model feature importances, or
SHAP if available) so `rationale` becomes "why this name ranks here" instead of
the current ensemble-dispersion prose. Stored alongside the prediction row or in
a sidecar table; surfaced in the API `rationale` field.

**Scheduled precompute vs request-time inference** — **scheduled precompute
wins.** Training and inference are batch jobs (cron / systemd timer on the VM, or
a worker process — *not* a deploy decision for this doc). The API only reads the
latest `prediction_outputs` row and formats it. This removes per-request model
fitting (current `run_model_suite`), makes responses fast and deterministic, and
makes every served number reproducible from a known artifact + feature snapshot.

---

## 4. Initial feature set (point-in-time only, no fabrication)

Only features we can **truly** source point-in-time from data already in
`stock_prices` (adjusted close per ticker/date) or a vetted source. Everything is
computed strictly from rows with `date <= as_of` and standardized **cross-
sectionally within each as_of_date**.

| Feature group | Concrete features | Source today | Notes |
|---|---|---|---|
| Price momentum | trailing returns 5/10/21/63/126d; 12-1 momentum | `stock_prices` (have it) | Core ranking signal. |
| Volatility | realized vol 21/63d; downside semivol; vol-of-vol | `stock_prices` | Used for sizing/risk band too. |
| Volume / liquidity | avg dollar-volume 21d; volume z-score; turnover | **needs volume column** | `stock_prices` currently stores only `adj_close`. If volume is not ingested, **mark as future feature**, do not fake it. |
| SPY-relative strength | excess return vs SPY 21/63d; rolling beta; correlation | `stock_prices` (SPY present) | Directly aligned with the target (excess vs SPY). |
| Sector / industry | sector one-hot or sector-relative momentum | **needs a sector map** | Only if a real, timestamped mapping exists. Otherwise future module. |
| Market regime | SPY trend (above/below 200d), SPY realized vol regime, breadth | `stock_prices` (SPY) | Same-for-all-names regime context; cheap and honest. |
| Earnings / events | days-to/since earnings, event flags | **placeholder only** | Add **only** if a real timestamped earnings calendar is wired in. No synthetic dates. |
| News / sentiment | sentiment score, news volume | **future optional module** | **Not assumed available.** Not in v1. No fabricated sentiment. |

**Hard rule:** features whose source we do not actually have (volume, sector,
earnings, news) are **declared but disabled** in v1. We do not invent them. The
v1 model trains on the groups we can source honestly today (momentum, volatility,
SPY-relative, regime), and the feature set is versioned so adding volume/sector
later is a clean `feature_set_version` bump.

---

## 5. Model design

- **Cross-sectional ranking model**, not per-ticker time-series. Each as_of_date
  we rank the universe by expected forward edge. This matches how the output is
  used (pick the best names), and it is what the current per-ticker code cannot do.
- **Primary target:** 5-trading-day **forward excess return vs SPY**
  (`realized_excess_return_5d_vs_spy`, already produced by
  `walk_forward_dataset.build_rows_for_ticker`).
- **Secondary target:** **probability of outperforming SPY** over 5 sessions
  (`outperform_spy_flag`, already produced). Drives the calibrated probability.
- **Baseline models (in order of complexity):** ridge / logistic regression
  first (transparent, hard to overfit), then random forest, then XGBoost **if the
  walk-forward evidence justifies the added complexity**. XGBoost is already a
  dependency (`import xgboost as xgb`, `api_server.py:21`).
- **No LSTM / Prophet / ARIMA complexity** unless a simpler model is beaten on
  walk-forward out-of-sample by a margin that survives the §8 gates. The current
  code's heavy models are exactly the kind of unjustified complexity we are
  removing.
- **Calibration:** isotonic (preferred) or Platt on walk-forward out-of-fold
  scores → `prob_outperform_spy`. Report Brier + reliability.
- **Validation:** **walk-forward only** (purged/embargoed to respect the 5-day
  horizon so train labels never overlap the test window). Reuse the existing
  harness (`research/run_walk_forward_baseline.py`, `research/metrics.py`) as the
  scoring backbone — it already computes rank IC, precision@K, top-N simulation,
  calibration, confusion matrix, and drawdown.

---

## 6. Production constraints

- **Do not train on request.** Training is an offline scheduled job.
- **Precompute predictions on a schedule** (daily, after the data refresh) for the
  whole universe via the inference pipeline.
- **Cache the latest predictions** in `prediction_outputs`; optionally an
  in-process LRU/`_series_cache`-style cache for hot tickers.
- **API returns the latest precomputed prediction quickly** — a single indexed
  read of `prediction_outputs` for the requested ticker, formatted into the
  existing response shape. No model fitting in the request path.
- **Preserve existing response fields.** Map new outputs onto legacy keys so
  Paper Trader keeps working unchanged:
  - `ensemble_day5` / `d5_change_pct` ← derive from `pred_return_5d`.
  - `confidence` ← **now** `round(100 * prob_outperform_spy)` (same field, finally
    a real number). Document the semantics change for Paper Trader.
  - `recommendation` ← new decision rule output (same label set:
    Strong Buy/Buy/Hold/Sell/Strong Sell, so the UI does not break).
  - `predictions[].lo` / `.hi` ← **now populated** from interval model.
  - `rationale` ← top feature attributions.
- **Add new fields without breaking Paper Trader** (additive only):
  `prob_outperform_spy`, `pred_excess_return_5d`, `lo_return_5d`, `hi_return_5d`,
  `rank_in_universe`, `risk_band`, `model_version`, `feature_set_version`,
  `generated_at`.
- **Feature flag.** New read path lives behind e.g. `PREDICTOR_USE_MODEL_V2=0`
  (default off). When off, the service behaves exactly as today. Flip to `1` only
  after §8 gates pass. This makes the cutover reversible with an env change, no
  redeploy of logic.

---

## 7. Implementation phases

Each phase is additive, testable, and behind the flag until §8 gates pass. No live
behavior changes until **2E** flips the flag.

| Phase | Deliverable | Output artifact | Done when |
|---|---|---|---|
| **2A** | Feature builder + labeled dataset | `model/features.py`, `feature_snapshots` table, leakage tests | Features computed point-in-time for the universe; every feature passes a leakage test; joins cleanly to existing labels. |
| **2B** | Walk-forward training + evaluation | `model/train.py`, fold metrics report | Ranking model trained per fold; rank IC / precision@K / top-N reported via existing `research/metrics.py`. |
| **2C** | Calibrated probability + risk bands | `model/calibrate.py`, `model/risk.py` | `prob_outperform_spy` calibrated (monotone buckets, Brier reported); intervals + `risk_band` produced. |
| **2D** | Prediction artifact persistence | `model/registry.py`, `model/infer.py`, `prediction_outputs` table | Inference writes versioned predictions; registry tracks `current`; reproducible from artifact + feature snapshot. |
| **2E** | API integration behind feature flag | read path in `api_server.py` guarded by `PREDICTOR_USE_MODEL_V2` | Flag off = identical to today; flag on (staging) = serves precomputed rows with legacy + new fields. |
| **2F** | Paper Trader display changes | (separate repo, later) | Surface `prob_outperform_spy`, intervals, risk band, attributions. Only after 2E is stable and gates pass. |

---

## 8. Decision gates (promotion criteria)

A model version is promoted to `current` (served behind the flag) **only if all**
of these pass on walk-forward, out-of-sample, across the full available window
**and** are stable across at least two non-overlapping sub-periods:

1. **Rank IC** (Spearman, predicted excess vs realized excess) ≥ **0.03** and
   positive in each sub-period. (`metrics.spearman_rank_ic`.)
2. **Top-N precision** beats the **universe baseline** materially: top-25 mean 5d
   return and hit rate exceed the equal-weight "hold everything" baseline
   (`metrics.universe_baseline`, `metrics.topn_simulation`).
3. **Calibrated probability** shows **monotonic** hit-rate buckets
   (`metrics.is_calibrated` / `metrics.calibration_table`) with acceptable Brier.
4. **BUY basket beats SPY and the universe baseline after simple costs** — apply a
   flat per-trade cost/slippage assumption to the top-N/BUY basket; the net excess
   vs SPY must remain positive.
5. **Downside is measurable** — interval coverage is honest (realized within
   `[lo,hi]` ≈ nominal) and BUY-basket drawdown is reported
   (`metrics.drawdown_summary`).

**If any gate fails:** the model **stays research-only**. The flag stays off, the
old path keeps serving, and we iterate features/model — we do **not** promote a
model that does not clear these bars, and we do **not** claim quant-grade.

---

## 9. Explicit statements (non-negotiable framing)

- **The current model is not quant-grade.** It is a price-only, per-request,
  point-forecast ensemble with a dispersion-based "confidence" and no intervals.
- **We are not throwing away the service.** The FastAPI app, endpoints, DB
  pattern, ingestion, and Paper Trader contract are kept.
- **We are rebuilding the signal / confidence / risk engine** — features, model,
  calibration, intervals, decision rule, persistence, and explainability.
- **No fake news / sentiment / macro features.** Features without a real,
  timestamped, point-in-time source are declared-but-disabled, never fabricated.
- **No production trading until walk-forward evidence is positive.** Output stays
  preview/manual-review; gates in §8 must pass before anything drives selection,
  and broker execution/automation remain out of scope unless separately approved.

---

## Appendix — Why the full walk-forward run is still useful but no longer blocking

The full walk-forward run on the *current* model is no longer a prerequisite for
the architecture decision: the rebuild is justified by **structural facts proven
from the code** (price-only inputs, CV-based "confidence", no intervals,
request-time fitting, in-sample metrics) that hold regardless of any sample. The
audit alone is decisive.

It remains valuable for three reasons, none of which block Phase 2:

1. **Baseline to beat.** It quantifies the current model's (likely near-zero)
   out-of-sample edge, giving Phase 2B a concrete number the new model must clear.
2. **Harness validation.** Running it end-to-end exercises
   `walk_forward_dataset` / `current_model_baseline` / `metrics` — the exact
   plumbing Phase 2B reuses — on real data, surfacing data issues (duplicates,
   coverage, SPY history start) before we trust the new model's evaluation.
3. **Communication.** A real before/after comparison (old IC ≈ noise → new IC ≥
   gate) is the cleanest way to justify the rebuild to stakeholders.

So: let any in-flight run finish and fold its numbers into the Phase 2B baseline,
but do not gate the rebuild on it.

