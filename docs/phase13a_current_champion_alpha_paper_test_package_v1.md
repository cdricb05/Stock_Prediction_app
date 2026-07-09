# Phase 13-A — Current Champion Alpha Paper-Test Package (v1)

## 1. Why this phase exists

Paid analyst-revision data is still pending with Nasdaq/Intrinio (Phase 12-A ended
`NASDAQ_ZACKS_NEEDS_CONTACT_SALES_OR_TRIAL`). Rather than wait idle, this phase **packages the alpha we
already have** into a disciplined, **preview only** paper-test candidate list, conservative rules, a
tracking framework, and a go/no-go scorecard.

The champion is the Phase 10-D sector-neutral quality composite **`composite_sn`**:

- long **sector-neutral free-cash-flow-to-assets**, short **sector-neutral operating accruals**
  (Sloan-negated), equal-weight, standardized within each month;
- a **quarterly / 63 trading**-day cross-sectional ranking signal;
- historical evidence (modest, not strong): IC t-stat ≈ **2.665**, quarterly net-25bps spread ≈
  **+0.00401**, net-50bps ≈ **+0.00095**, turnover ≈ **0.6115**.

This is **modest but good enough for a paper test, not live trading.** This phase does **not** search for
new alpha, does **not** retune the factor, does **not** optimize weights, and does **not** use
analyst-revision or any unavailable paid data. It does **not** modify the live Paper Trader app.

## 2. Decision — `CURRENT_ALPHA_READY_FOR_PAPER_TEST`

The frozen Phase 10-L scored panel loads, the latest fully-scored cross-section ranks cleanly, and
**owned local EOD prices initialize entry prices** for the covered names. The package is ready for a
paper test **with two loud caveats** (both in the scorecard): partial local-price coverage and signal
**staleness**.

Decision enum: **`CURRENT_ALPHA_READY_FOR_PAPER_TEST`** · `CURRENT_ALPHA_PACKAGE_READY_PANEL_ONLY` ·
`CURRENT_ALPHA_NEEDS_FRESH_PRICES` · `CURRENT_ALPHA_REJECTED_DUE_STALENESS` · `BLOCKED_DATA_MISSING` ·
`BLOCKED_RUNNER_ERROR`. Forbidden: `LIVE_TRADING_READY`, `ORDER_READY`, `AUTOMATION_READY`,
`PAPER_TRADER_READY`, `STRONG_ALPHA_FOUND_READY_FOR_REVIEW`.

## 3. Latest usable signal (cross-section identification)

In the frozen panel the per-observation `rebalance_date` is a per-ticker **daily event date**, but the
signal is standardized **within a calendar month** (`within_month_z`). The honest cross-section unit is
therefore the calendar **month**, and the latest usable signal is the most recent month with valid
`composite_sn` scores.

| field | value |
|---|---|
| latest signal date (max rebalance_date in month) | **2026-05-22** |
| cross-section month | **2026-05** |
| ranked tickers (valid composite_sn) | **234** |
| sector coverage | 11 sectors present; ~83% **Unknown** (owned-metadata gap carried from 10-F) |
| package/staleness anchor date | freshest owned price date (**2026-06-26**) |
| days since signal | **35** → **stale warning** (warn > 30d; reject > 120d) — not rejected |
| fresh prices available locally | **yes** (owned EODHD adjusted_close through 2026-06-26) |

**Staleness** is measured from the signal date to the package date (the freshest owned price date). 35
days is within the 63-trading-day (~92 calendar-day) holding horizon, so it is a **warning, not a
rejection**; re-run after refreshing local prices to re-measure against a newer date. A signal older
than 120 days flips the decision to `CURRENT_ALPHA_REJECTED_DUE_STALENESS`.

## 4. The paper-test candidate package

- **Top 25 / Top 50 candidates** — ranked by descending `composite_sn`.
- **Bottom 25 / avoid list** — ranked by ascending `composite_sn` (short-only **diagnostic**, not a
  live recommendation).
- **Full ranked universe** — all 234 names with rank, bucket, both z-legs, raw levels, liquidity proxy,
  and local-price availability.
- **Sector exposure** — per book (top25 / top50 / bottom25); any sector share above 30% is flagged
  `CONCENTRATED`. The top-25 book is 100% **Unknown**-sector because sector labels are largely unmapped
  in this reconstruction — a real caveat, surfaced not hidden.
- **Liquidity diagnostics** — dollar-volume `liquidity_proxy`; names below the universe 25th percentile
  are flagged for size-down/exclusion.
- **Missing-data report** — Unknown-sector count, `NO_LOCAL_PRICE` names per book, low-liquidity names,
  and any names dropped for a missing composite leg.

## 5. Paper-test rules (conservative)

Written to `current_alpha_risk_limits.csv`:

- **PREVIEW ONLY · NO ORDERS · NO BROKER · MANUAL REVIEW · NO AUTOMATION · NO LIVE TRADING** (hard).
- **Quarterly** rebalance target; **63 trading**-day holding horizon.
- **Equal-weight, long-only** top-25 (4% each) and top-50 (2% each) paper portfolios.
- Optional **long-short diagnostic** (top-25 long / bottom-25 short) — diagnostic only, not for live use.
- **Max position size** suggestion = equal weight; hard single-name cap suggestion 5%.
- **Sector concentration warning** at 30% of a book.
- **Liquidity warning** below the universe 25th-percentile dollar-volume proxy.
- **No averaging down**; **no live-trading recommendation** — nothing here is advice to trade real
  capital.

## 6. Tracking framework

`current_alpha_tracking_template.csv` gives one row per top-25 holding plus a portfolio summary row:

- paper-test start date, signal date, entry price (initialized off owned local prices where available);
- **benchmark plan**: **SPY** if owned locally (it is **not**, so the plan falls back to an
  **equal-weight universe** reference computed from owned prices);
- checkpoint columns for **1-week, 1-month, 2-month, and 63-trading-day** horizons, each with a realized
  return and a benchmark-relative return column;
- drawdown, hit (win/loss), and sector-attribution placeholder columns.

Entry prices are taken at/just-before the signal date; the current mark is the latest owned local
`adjusted_close`. Realized/benchmark-relative cells are placeholders to be filled when owned local
prices are refreshed past the current mark — **no live market API is ever called**. The 10-D
`composite_sn` net-25bps spread is the documented *expected* reference, with the explicit caveat that it
is a full-rank quintile long/short backtest and only *approximates* a 25/50-name equal-weight long-only
book.

## 7. Go / no-go scorecard

`current_alpha_go_no_go_scorecard.csv` scores: panel loaded, composite coverage, **signal freshness**
(WARN at 35d), price coverage (top25 14/25, top50 24/50 → WARN), sector diversification (WARN under the
Unknown-sector caveat), local SPY benchmark (WARN — absent), historical IC t (PASS, modest), historical
net-25bps (PASS). Overall: **`GO_PAPER_ONLY_WITH_CAVEATS_NOT_LIVE`** — a paper-only recommendation,
never a live-trading signal.

## 8. Artifacts

`research/output/phase13a_current_champion_alpha_paper_test_package/`:
`phase13a_current_champion_alpha_paper_test_package.json` · `current_alpha_full_ranked_universe.csv` ·
`current_alpha_top25_candidates.csv` · `current_alpha_top50_candidates.csv` ·
`current_alpha_bottom25_avoid_list.csv` · `current_alpha_sector_exposure.csv` ·
`current_alpha_missing_data_report.csv` · `current_alpha_paper_portfolio_top25.csv` ·
`current_alpha_paper_portfolio_top50.csv` · `current_alpha_tracking_template.csv` ·
`current_alpha_risk_limits.csv` · `current_alpha_go_no_go_scorecard.csv` · `secret_safety_audit.csv`.

## 9. Safety / constraints

Fully **offline** (no network, no key, no provider probe); **owned/local data only**; no new alpha
search; no retune; no reweight; no analyst-revision/paid data; **no Paper Trader writes**, no Paper
Trader signals, no trade decisions; **NO orders**, **NO automation**, **NO broker**, **NO live
trading**; no deploy; no GCP; no package install; no key printed or written. Any move toward live
capital requires explicit, separate human authorization.

## Run / test (Windows PowerShell only)

```powershell
Set-Location C:\Users\binis\Stock_Prediction_app_push
python research/run_phase13a_current_champion_alpha_paper_test_package.py
python research/run_phase13a_current_champion_alpha_paper_test_package.py --as-of 2026-06-26   # deterministic
python -m pytest tests/test_phase13a_current_champion_alpha_paper_test_package.py -q
```
