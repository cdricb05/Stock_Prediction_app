# Phase 13-I — Historical Daily Mark Backfill + Paper Performance Analytics (v1)

## Purpose

Reconstruct the historical **daily mark-to-market strip** for the frozen Phase 13-A
champion paper books (`composite_sn` Top-25 / Top-50, signal date **2026-05-22**) using
the user's **owned EODHD adjusted-close history**, so paper performance analytics do not
have to wait for future calendar days to accumulate.

This is **not** a backtest with changing holdings. It is a mark-to-market of the **same
frozen positions** on every completed EOD trading date after entry:

- Holdings and entry prices are **frozen** at the Phase 13-A book.
- There is **no reranking, no rebalancing**, and no name is added or removed on any date.
- Each date re-marks the frozen positions against that date's adjusted close.

It creates **no orders, no signals, no trade decisions**, connects to **no broker**, runs
**no automation / scheduling**, enables **no live trading**, and writes **nothing** to the
Paper Trader database or the champion definition. It only performs read-only market-data
reads and writes a dynamic reconstruction artifact **outside both git repositories**.

## Single reused provider client

All ticker normalization, EODHD transport, adjusted-close selection, completed-EOD
filtering, entry-price rule, and secret handling are **imported from Phase 13-G**
(`run_phase13g_daily_alpha_mark_refresh`): `_clean_symbol`, `_normalize_bars`,
`_completed_bars`, `_price_at_or_before`, `load_source_universe`, `live_transport`,
`_fetch_one`, `probe_entitlement`, and the atomic writers. No second, incompatible
provider client is introduced. `EODHD_API_KEY` is read only from the environment by the
reused client, is passed only as the `api_token` query param, and is **never printed and
never persisted**.

## Method

1. **Universe (frozen).** Top-50 book names from the Phase 13-A package (Top-25 = first 25
   rows) + SPY. No S&P 500 shadow, no rerank.
2. **Acquire once.** Fetch each ticker's full adjusted-close history a single time
   (`--start` default `2026-01-01`), after the reused bounded entitlement probe.
3. **Common trading calendar.** SPY completed sessions on/after the signal date (weekends,
   holidays, and the incomplete current-day bar are excluded by the reused completed-EOD
   rule). One financial observation per date; duplicates rejected.
4. **Frozen entry.** The Phase 13-A book entry price when present; otherwise the
   point-in-time adjusted close at/at-or-before the signal date (identical to the Phase
   13-G `mark_ticker` fallback). Computed once, held fixed across all dates.
5. **Per-date mark.** Each frozen position is marked at the last adjusted close on/before
   the date (carry-forward on idiosyncratic non-trading days; never look-ahead).
6. **SPY benchmark.** Reference price at/at-or-before the signal date (same point-in-time
   rule as the book entries); cumulative return per date.

## Reconciliation (integrity gate)

The reconstructed row **at the Phase 13-G latest valid mark date** must reproduce the live
Phase 13-G book/benchmark marks (same frozen entries + same price rule → near-exact).
Metrics compared: Top-25 average return, Top-50 average return, SPY return, latest mark
date presence, Top-25/Top-50 coverage counts. Tolerances (percentage points):
`RECON_TIGHT_PP = 0.05`, `RECON_LOOSE_PP = 1.00`.

Decision enum:

- `BACKFILL_RECONCILED` — every comparable metric within tight tolerance.
- `BACKFILL_RECONCILIATION_WARNING` — no reference on disk, or within loose (not tight)
  tolerance. Analytics still published.
- `BACKFILL_REJECTED_INTEGRITY_FAILURE` — a comparable metric diverges beyond the loose
  tolerance. **Analytics are not published** (only the manifest with the failed checks).
- `BLOCKED_EODHD_KEY` / `BLOCKED_EODHD_ENTITLEMENT` / `BLOCKED_EODHD_RATE_LIMIT` /
  `BLOCKED_PROVIDER_ERROR` / `BLOCKED_SCHEMA_ERROR` — provider states (reused taxonomy).

## Part B — paper performance analytics

Computed separately for Top-25 and Top-50 over the reconstructed strip: observation count,
start/end dates, current cumulative return, SPY cumulative return, current excess return,
maximum drawdown (depth, peak/trough dates, duration in observations, recovered flag),
best/worst daily change, daily-change volatility, % positive daily changes, % days
outperforming SPY, average daily excess change, tracking error, information ratio (only
reported at/above `MIN_IR_OBS = 20` daily excess observations), contributor concentration
(top-5 share of gross book PnL), signed top-5 PnL share, and coverage-warning /
insufficient-coverage date counts.

**Stability comparison** (Top-25 vs Top-50): `TOP25_MORE_STABLE` / `TOP50_MORE_STABLE` /
`NO_CLEAR_STABILITY_WINNER` / `INSUFFICIENT_DAILY_HISTORY` (fewer than
`MIN_STABILITY_OBS = 5` daily changes). This is an **operational** comparison only — it
changes no champion and **promotes no book to live trading**. The short forward window is
**not alpha validation**.

## Output (atomic; outside git; under `<daily-mark-dir>/backfill`)

Default root `D:\Stock_Prediction_app_data\phase13g_daily_alpha_marks\backfill`
(env `PAPER_TRADER_CURRENT_ALPHA_DAILY_MARK_DIR` overrides the root):

- `backfill_manifest.json` — decision, window, benchmark, latest marks, reconciliation,
  acquisition status, safety.
- `top25_daily_history.json` / `.csv`, `top50_daily_history.json` / `.csv` — per-date book
  summaries (never combined).
- `spy_daily_history.json` / `.csv` — per-date SPY cumulative return.
- `position_daily_marks.csv` — per-position per-date marks (`order_action = NO_ORDER`).
- `paper_performance_summary.json` — Part B analytics + stability comparison.

These dynamic artifacts are **not committed** into either repository.

## Safety badges

`HISTORICAL PAPER MARK RECONSTRUCTION`, `FROZEN HOLDINGS`, `NO DAILY REBALANCING`,
`PAPER TEST ONLY`, `NO ORDERS`, `NO BROKER`, `NO AUTOMATION`, `DOES NOT CREATE SIGNALS`,
`DOES NOT CREATE TRADE DECISIONS`, `DOES NOT EXECUTE TRADES`.

## Manual run (Windows PowerShell)

```powershell
$py = "C:\Users\binis\paper_trader\.venv-win\Scripts\python.exe"
Set-Location C:\Users\binis\Stock_Prediction_app_push
& $py -m pytest tests/test_phase13i_historical_daily_mark_backfill.py -q   # offline
& $py research\run_phase13i_historical_daily_mark_backfill.py               # live read-only EODHD
Get-Content D:\Stock_Prediction_app_data\phase13g_daily_alpha_marks\backfill\backfill_manifest.json
Get-Content D:\Stock_Prediction_app_data\phase13g_daily_alpha_marks\backfill\paper_performance_summary.json
```
