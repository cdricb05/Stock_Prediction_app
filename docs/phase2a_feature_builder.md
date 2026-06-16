# Phase 2A — Feature Builder + Labeled Dataset

_Offline / research model preparation. No API behavior change, no DB writes, no
schema migration, no deploy. Implements Phase 2A of
[`docs/phase2_model_rebuild_plan.md`](phase2_model_rebuild_plan.md)._

Phase 2A delivers the **point-in-time, cross-sectional feature builder** and the
**labeled dataset** the Phase 2B ranking model will train on. It is pure-Python
feature math (testable without a database) plus a thin, read-only DB loader.

- Code: [`model/features.py`](../model/features.py), [`model/__init__.py`](../model/__init__.py)
- Tests: [`tests/test_phase2_features.py`](../tests/test_phase2_features.py) (22, self-running)
- Sample: [`research/output/phase2a_feature_dataset_sample.csv`](../research/output/phase2a_feature_dataset_sample.csv)

---

## 1. Feature list (v1)

All features use **only data with `date <= as_of_date`** and are computed from the
de-duplicated `(date, adj_close[, volume])` series. Annualized vols use
`sqrt(252)`.

| Family | Feature | Definition (at session index `i` = as_of) |
|---|---|---|
| **Momentum** | `return_5d/10d/21d/63d/126d` | `adj[i]/adj[i-N] - 1` |
| | `momentum_12_1` | `adj[i-21]/adj[i-252] - 1` (12-month, skipping the last month). Needs ≥252 sessions, else `None`. |
| **Volatility** | `realized_vol_21d/63d` | annualized sample std (`ddof=1`) of the last N daily returns |
| | `downside_vol_21d` | annualized `sqrt(mean(r²))` over the **negative** daily returns in the last 21 (0.0 if none) |
| **SPY-relative** | `excess_return_vs_spy_21d/63d` | `return_Nd(ticker) − return_Nd(SPY)`, SPY aligned as-of each ticker date |
| | `rolling_beta_63d` | `cov(r_t, r_spy)/var(r_spy)` over the last 63 aligned daily-return pairs |
| | `rolling_corr_spy_63d` | Pearson corr of the same 63 pairs |
| **Market regime** (same for all names on a date) | `spy_return_21d/63d` | SPY trailing return on SPY's own calendar at as-of |
| | `spy_realized_vol_21d` | annualized std of last 21 SPY daily returns |
| | `spy_above_200d` | `1.0` if SPY close > its 200-session SMA, else `0.0`; `None` before 200 sessions |
| **Volume / liquidity** (OPTIONAL — see §3) | `avg_dollar_volume_21d` | mean of `adj_close × volume` over last 21 sessions |
| | `volume_zscore_21d` | `(volume[i] − mean₂₁) / std₂₁` |

Each feature degrades to `None`/`NaN` individually when its specific lookback is
unavailable (e.g. `return_126d` before 126 sessions) — it is never back-filled or
guessed. The builder also emits within-date standardized copies
(`<feature>_z`) via `cross_sectional_zscore` (z-scored across the universe within
each `as_of_date`), which is the form the cross-sectional ranking model consumes.

---

## 2. Source data used

- **Only** `stock_prices` (`date`, `adj_close`, and — when populated — `volume`),
  read through `research.walk_forward_dataset.load_series` /
  `model.features.load_series_with_volume`, which de-duplicate via
  `DISTINCT ON (date)` keeping the highest `id` (the table has ~1.6k duplicate
  `(ticker, date)` groups).
- **SPY** is the benchmark for the relative/regime families, aligned to each
  ticker's calendar with as-of-or-before lookups (handles calendar mismatches).
- **Labels** are reused verbatim from
  `research.walk_forward_dataset.build_rows_for_ticker` so features and targets
  share one point-in-time anchor.

No other source is read. Nothing is written to the database.

---

## 3. Disabled / optional feature families (never fabricated)

| Family | Status | Why |
|---|---|---|
| **Volume / liquidity** | **Optional, presence-gated** | `stock_prices` *has* a `volume` column ([`load_sp500_to_db.py:106`](../load_sp500_to_db.py#L106) bulk-inserts full OHLCV), but the API's Yahoo-fallback path inserts only `ticker,date,adj_close` ([`api_server.py:158`](../api_server.py#L158)), so volume is **not guaranteed populated for every row**. Volume features are emitted **only** when a real, finite, non-negative volume window is present; otherwise `None` and the `volume_liquidity` family is reported unavailable. Never synthesized. |
| **Sector / industry** | Disabled | No timestamped membership map is wired in. |
| **Earnings / events** | Disabled | No real earnings calendar ingested; synthetic dates are forbidden. |
| **News / sentiment** | Disabled | No feed available; future optional module. |
| **Macro** | Disabled | No timestamped macro series ingested; future optional module. |

Disabled families produce **no columns** — the builder never invents a value or a
placeholder column for them. `unavailable_feature_families(volume_present)` reports
the active disabled set (and the CLI prints it).

---

## 4. Leakage controls

1. **Past-only math.** Every helper indexed by `i` uses only indices `≤ i`
   (`trailing_return`, `realized_vol`, `momentum_12_1`, rolling beta/corr windows,
   regime as-of anchor). The forward label is the only future-looking quantity and
   it comes from the already-tested `walk_forward_dataset`.
2. **As-of benchmark alignment.** SPY is aligned with last-close-on-or-before
   lookups, so a ticker session never sees a SPY close dated after it.
3. **No silent fill.** A `NaN`/missing value anywhere in a feature's window
   disqualifies that feature (returns `None`) rather than imputing.
4. **Truncation invariance (tested).** A feature row at `as_of` is byte-for-byte
   identical whether computed on the full series or on the series truncated exactly
   at `as_of`; and mutating any strictly-future price leaves the row unchanged.
   (`test_no_future_leakage_truncation_invariance`,
   `test_no_future_leakage_future_mutation_does_not_change_past_row`.)
5. **Unlabeled tail dropped.** The labeled join is an inner join on
   `(ticker, as_of_date)`; the last `horizon` sessions (no realized label) never
   enter the training set.

---

## 5. Sample output schema

`research/output/phase2a_feature_dataset_sample.csv` (200 rows; written from
**synthetic** prices when no `--db-url` is given — clearly `SYN_*` tickers so it is
never mistaken for real market data; the identical code path produces the real
dataset with `--db-url`).

Columns, in order:

```
keys/meta : ticker, as_of_date, feature_set_version, n_history, current_adj_close
features  : return_5d, return_10d, return_21d, return_63d, return_126d, momentum_12_1,
            realized_vol_21d, realized_vol_63d, downside_vol_21d,
            excess_return_vs_spy_21d, excess_return_vs_spy_63d,
            rolling_beta_63d, rolling_corr_spy_63d,
            spy_return_21d, spy_return_63d, spy_realized_vol_21d, spy_above_200d,
            avg_dollar_volume_21d, volume_zscore_21d
labels    : target_date, realized_return_5d, spy_return_5d,
            realized_excess_return_5d_vs_spy, positive_return_flag, outperform_spy_flag
z-scores  : <each feature above>_z   (within-date cross-sectional standardization)
```

Notes visible in the sample: `momentum_12_1` is empty for rows with `<252`
sessions, `spy_above_200d` empty before 200 SPY sessions, and the volume columns
are empty for the no-volume ticker (`SYN_A`) and populated for the others — the
honest disabled/optional behavior, not zeros.

### Running it

```bash
# Synthetic schema sample (no DB):
python -m model.features

# Real dataset from the database (read-only), small slice:
python -m model.features --db-url "$DB_URL" --max-tickers 10 \
    --start-date 2024-01-01 --end-date 2025-01-01 \
    --output research/output/phase2a_feature_dataset_sample.csv
```

(Requires an interpreter with `numpy`/`pandas`; on this workstation that is
`C:\Python313\python.exe`. No package install is performed.)

---

## 6. How Phase 2A feeds Phase 2B

- **Training matrix.** `build_feature_dataset(..., with_labels=True)` returns one
  row per `(ticker, as_of_date)` with the v1 features, their within-date z-scores,
  and the 5-day forward labels — directly consumable by 2B.
- **Targets.** Primary = `realized_excess_return_5d_vs_spy` (regression / rank);
  secondary = `outperform_spy_flag` (the probability-of-outperformance label that
  2C will calibrate).
- **Walk-forward ready.** Rows are keyed and ordered by `as_of_date`, so 2B can
  cut purged/embargoed walk-forward folds without touching the feature code, and
  reuse `research/metrics.py` (rank IC, precision@K, top-N, calibration, drawdown)
  as the scoring backbone.
- **Versioned.** `feature_set_version = "v1"`. Adding volume/sector/etc. later is a
  clean version bump, not a rewrite — the `feature_snapshots` materialization
  (the persisted form of this builder) is keyed `(ticker, as_of_date,
  feature_set_version)`.
