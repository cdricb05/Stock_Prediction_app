# Phase 13-G (Part B) — Daily Alpha EOD Mark Refresh

**Status:** complete · manual, user-triggered · live read-only EODHD (authorized) · does not trade
**Runner:** `research/run_phase13g_daily_alpha_mark_refresh.py`
**Tests:** `tests/test_phase13g_daily_alpha_mark_refresh.py` (fully offline — fake transport, no key)
**Output (OUTSIDE git):** `D:\Stock_Prediction_app_data\phase13g_daily_alpha_marks\`
(env `PAPER_TRADER_CURRENT_ALPHA_DAILY_MARK_DIR` override)

## What it does

A **manual** daily mark refresh for the champion `composite_sn` paper books. Each run:

1. builds the source universe = **CURRENT_CHAMPION Top-50** (from the 13-A package) **+ SPY**
   (+ the **S&P500 shadow Top-50** if the Phase 13-G Part A audit packaged one),
2. runs a **bounded entitlement probe** (SPY + 3 candidates) — validates schema + a completed
   EOD date — then continues automatically to the full union,
3. fetches the latest **completed** EOD adjusted closes from the owned EODHD entitlement,
4. recomputes paper PnL vs the frozen 13-A book entry prices (entry preferred from the frozen
   book; derived from the fetched series only for names the package could not price),
5. marks Top-25 and Top-50, compares each to SPY, and
6. persists a dated price-mark artifact **outside both git repositories** (atomic writes).

**It does not trade.** No orders, no broker, no automation, no scheduling, no live trading — a
read-only market-data refresh + PnL computation only.

## EODHD client reuse + secret discipline

Reuses the existing Phase 8-U/8-R client `u8._eod_live_get` (imported lazily): allow-listed host
`eodhd.com`, `.US` ticker suffix (class shares mapped `.`→`-`), `EODHD_API_KEY` read only from the
environment and passed only as the `api_token` query param, URLs redacted, errors sanitized to a
status code. **The key is never printed and never persisted.** A test injects a fake transport, so
the offline suite makes zero network calls and needs no key.

## Latest-price rule (completed EOD only)

The latest **completed** session is used. A bar dated on the current calendar day is treated as
potentially incomplete and is **never** used as a completed EOD mark (the reference "today" is
injectable via `--today` for deterministic tests). The book `mark_date` is the SPY session date.

## Artifacts

```
latest/{daily_alpha_marks.json, daily_alpha_marks.csv, book_summaries.json,
        benchmark_summary.json, refresh_manifest.json}
history/YYYY-MM-DD/{... same five ...}
```

Per-ticker mark fields: `ticker, alpha_name, signal_date, source_rank, in_top25, in_top50,
entry_reference_date, entry_price, latest_completed_eod_date, latest_adjusted_close,
paper_return_pct, price_source, price_status, acquisition_status`.

Book summaries (Top-25 / Top-50): `book_id, mark_date, covered_count, missing_count, total_count,
coverage_pct, coverage_status, average_return_pct, median_return_pct, hit_rate_pct, best_5,
worst_5, previous_mark_date, previous_average_return_pct, change_since_previous_mark_pct_points,
benchmark_return_pct, excess_return_vs_spy_pct_points`.

`book_id = composite_sn__<signal_date>__top<size>` (matches the Phase 13-F paper-book store).

## Coverage integrity

`FULL_COVERAGE` (100%) · `PARTIAL_COVERAGE_WARNING` (≥90% and <100%) ·
`INSUFFICIENT_COVERAGE_REJECT` (<90%). Individual ticker marks are still recorded, but a book with
`INSUFFICIENT_COVERAGE_REJECT` carries `pnl_claim_valid=false` — full-book PnL is not claimed.

## Refresh-result enum

`REFRESH_OK_NEW_MARK_DATE` · `NO_NEW_MARK_DATE` · `PARTIAL_COVERAGE` · `INSUFFICIENT_COVERAGE` ·
`BLOCKED_EODHD_KEY` · `BLOCKED_EODHD_ENTITLEMENT` · `BLOCKED_EODHD_RATE_LIMIT` ·
`BLOCKED_PROVIDER_ERROR` · `BLOCKED_SCHEMA_ERROR`.

A repeated run on the **same** completed EOD mark date returns `NO_NEW_MARK_DATE` and does **not**
create another history directory or a duplicate financial observation. A blocked run does not
overwrite a good prior mark.

## Run (live read-only EODHD)

```powershell
$py = "C:\Users\binis\paper_trader\.venv-win\Scripts\python.exe"
& $py research\run_phase13g_daily_alpha_mark_refresh.py
```
The dynamic marks under `D:\` are **not** committed to either git repository.
