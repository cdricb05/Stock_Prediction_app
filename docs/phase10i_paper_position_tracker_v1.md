# Phase 10-I — Paper-Only Position Tracker (v1)

## Purpose

Phase 10-H built a rules-based **25-long / 25-short** equal-weight paper portfolio. Phase 10-I **tracks**
that paper book: a holdings ledger, a mark-to-market plan/snapshot off **owned local prices only**,
expected-vs-realized against the 10-D net-25bps benchmark, and exposure/risk summaries.

It is a **read/observe harness only** — **not** a new alpha search, **not** a provider search, **not** a
Paper Trader integration, **not** order creation, **not** automation, **not** a deploy. Fully offline (no
network, no API key, no provider probe, **no live market API**). It writes **only** metadata CSV/JSON to
its own `research/output` directory, creates **no** Paper Trader signals / trade decisions / orders, and
keeps `order_action = NO_ORDER` on every ledger row.

## Inputs (owned only)

10-H artifacts: `selected_paper_portfolio.csv` (+ `selected_long_book.csv` / `selected_short_book.csv`),
`portfolio_construction_rules.csv`, `rule_approval_checklist.csv`,
`phase10h_rules_based_paper_portfolio.json` (inception `as_of`, `expected_rebalance_date`, declared
`n_long`/`n_short`, weights). Prices: `research/data/eodhd/raw/eod_prices/<ticker>.json` (owned EODHD
daily OHLCV + `adjusted_close`). Benchmark: Phase 10-D validation JSON.

## What it does

1. **Read + validate** the selected book: every row paper-only with `order_action = NO_ORDER`, equal
   weights within each side, and `n_long`/`n_short` matching the 10-H-declared sizes (else
   `BLOCKED_INVALID_PORTFOLIO`).
2. **Ledger** (`paper_holdings_ledger.csv`): ticker · side · target_weight · sector ·
   entry_reference_date · next_rebalance_date · paper_status (`PAPER_OPEN`) · `order_action = NO_ORDER`.
3. **Mark-to-market** off owned local prices only: `entry_price` = `adjusted_close` at/just-before
   inception; `current_price` = `adjusted_close` at the latest LOCAL date. A name is **MARKED** only
   when local data extends **past** inception; otherwise **PENDING_PRICE_REFRESH** (entry captured, no
   later local price) or **NO_LOCAL_PRICE** (no owned file). Short returns are side-signed (profit when
   price falls). No live market API.
4. **Expected vs realized**: expected quarterly net-25bps / net-50bps from the 10-D **sector-neutral
   composite** (`composite_sn` — the signal the book ranks on); realized left `PENDING` until a price
   refresh.
5. **Exposure / risk**: gross long, gross short, net, sector exposure, liquidity summary,
   concentration.
6. **Safety**: badges + status (`PAPER TRACKING ONLY`, `NO ORDERS`, `NO AUTOMATION`, `NO BROKER`,
   `HUMAN REVIEW REQUIRED`); secret-safety audit; `phase10j_next_plan.json`.

## Benchmark choice (honest)

The book ranks by the **sector-neutral** composite `comp_sn`, so the apt 10-D benchmark is
`composite_sn`: **expected quarterly net-25bps ≈ +0.00401**, net-50bps ≈ +0.00095, ic_t 2.67. The raw
composite (`composite_raw`) scored higher (net-25bps +0.00648) but is **not** the right comparator for a
sector-neutral book. The 10-D figure is a full-rank quintile L/S backtest, so this 25/25
liquidity-filtered equal-weight subset only **approximates** it — documented, not a promise.

## Decision rule

- `PAPER_POSITION_TRACKER_READY` — book valid and **at least one holding marked** off local prices.
- `PAPER_POSITION_TRACKER_READY_PENDING_PRICE_REFRESH` — book valid but **no** local price past
  inception yet (mark pending).
- `PAPER_POSITION_TRACKER_BLOCKED_INVALID_PORTFOLIO` — fails paper-only / equal-weight / count checks.
- `PAPER_POSITION_TRACKER_BLOCKED_MISSING_SELECTED_BOOK` — `selected_paper_portfolio.csv` not found.
- `HARD_BLOCKER_REQUIRES_USER_ACTION` / `ERROR_WITH_REPRO_COMMAND`.

**Forbidden:** `LIVE_TRADING_READY`, `ORDER_READY`, `AUTOMATION_READY`, `PAPER_TRADER_READY`,
`STRONG_ALPHA_FOUND_READY_FOR_REVIEW`, `MISSING_KEY`, `NO_DATA`, `NEEDS_PROVIDER`, `EMPTY_PAYLOAD`,
generic `ERROR`.

## Artifacts (`research/output/phase10i_paper_position_tracker/`)

`phase10i_paper_position_tracker.json` · `paper_holdings_ledger.csv` ·
`paper_mark_to_market_plan.csv` · `paper_mark_to_market_snapshot.csv` ·
`paper_expected_vs_realized_template.csv` · `paper_exposure_summary.csv` · `paper_sector_exposure.csv`
· `paper_liquidity_summary.csv` · `paper_tracker_safety_badges.csv` · `paper_tracker_status.csv` ·
`phase10j_next_plan.json` · `secret_safety_audit.csv`.

## Run / test (Windows PowerShell only)

```powershell
Set-Location C:\Users\binis\Stock_Prediction_app_push
python -m py_compile research/run_phase10i_paper_position_tracker.py
python -m pytest tests/test_phase10i_paper_position_tracker.py -q   # targeted; 15 passed
python research/run_phase10i_paper_position_tracker.py              # fully offline; no key
```

## Status — live run 2026-06-30 (offline; exit 0)

**Decision: `PAPER_POSITION_TRACKER_READY_PENDING_PRICE_REFRESH`.**

| metric | value |
|---|---|
| holdings | **50** (25 long / 25 short, equal-weight 4%) |
| inception date | **2026-06-26** (10-H `as_of`) |
| next rebalance | **2026-09-30** (QUARTERLY) |
| MTM status | **PENDING_PRICE_REFRESH** — owned local prices end at inception (no post-inception data) |
| price coverage | 0 marked · 21 pending (entry captured) · 29 no local file |
| long / short gross | 100% / 100% |
| net / gross | **0% / 200%** |
| expected benchmark | `composite_sn` net-25bps **+0.00401** / qtr (realized PENDING) |
| largest name weight | 4.0% |
| Paper Trader writes / signals / trade decisions / orders / automation | **None** |
| secret leak scan | clean |

Local EODHD EOD prices currently end at **2026-06-26** (= inception), so no holding can be marked yet;
every covered name has its **entry** price captured and is flagged `PENDING_PRICE_REFRESH`. The tracker
re-marks automatically once owned local prices extend past inception (the MTM logic is future-proof).

## Constraints honored

Offline (no network/key/provider probe); **owned/local data only** (EODHD local EOD prices); no
FMP/AlphaVantage/Polygon/Finnhub/Norgate-API; **no live market API**; no new purchase; **no Paper Trader
writes; no signals; no trade decisions; NO orders; NO automation; NO broker; NO live trading; no deploy;
no GCP**; no package install; no full regression (targeted tests only); keys never printed or written;
output is metadata only. **No commit. No push.**

## Recommended Phase 10-J

When owned local EOD prices extend past inception, re-run to mark the book and fill
`paper_expected_vs_realized_template.csv` (realized quarterly net spread vs the `composite_sn`
benchmark). At the 2026-09-30 rebalance, snapshot realized vs expected and re-run the 10-H rules for the
next quarter's paper book — still **no orders, no automation, no broker, no live trading, no deploy**.
