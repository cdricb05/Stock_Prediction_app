# Phase 11-C — New-Data Orthogonal Alpha Investigation (v1)

## 1. Why this phase exists

Phase 11-B0 proved a genuinely **new** orthogonal family was already on disk and broad + deep enough for
a real walk-forward test: **Finnhub insider-sentiment MSPR** (292 tickers, ~76 monthly obs/ticker,
2016-2026), never tested in any prior phase. Phase 11-C is the honest alpha test of that data — plus a
new *derived* short-interest field — against the modest 10-D quality baseline `composite_sn`.

It reuses the **exact 10-D engine** (`c10._eval`, `d10.quarterly_backtest`, `d10.walk_forward_h`) and the
strict relative beat test from Phase 10-L-B (`l10b.load_panel` / `signal_battery` / `quarterly_book` /
`_assemble` / `classify`) — nothing is re-implemented — so a new signal is held to the **same** skeptical,
cost-aware, OOS-and-subperiod-stable bar the baseline itself passed. On top of 10-L-B's classifier this
phase adds the **10-N / 10-O subperiod-net25 improvement guard**: the incremental gain over the baseline
must be non-worsening in **both** the pre-2020 and post-2020 eras.

## 2. Method

- **Join:** PIT backward `merge_asof` by ticker (`available_date ≤ entry_date`) onto the frozen
  38,725-row `(rebalance_date, ticker)` panel — zero look-ahead.
- **Signals (sector-neutral z, within-month then (month,sector) demean, same as the quality legs):**
  `insider_mspr_last_sn`, `insider_mspr_3m_sn` (trailing-3-print mean; monthly insider data is noisy),
  `short_interest_change_sn` (bearish-oriented Δ short interest — a **new derived field** of the family
  that was already rejected in 10-A).
- **Tests at 63d (the decision horizon):** standalone battery (IC / quarterly L/S / walk-forward OOS /
  cohort / pre-post-2020), then incremental blends `composite_sn + w·signal` for `w ∈ {0.15, 0.30, 0.50}`,
  each run through the full strict relative beat test + the subperiod-net25 improvement guard.

**Horizon limitation (recorded, not hidden):** only **63d** was tested — the decision horizon and the
horizon the baseline is defined at. **5d / 21d are deferred** (the frozen offline panel carries only
`fwd_exc_63`; rebuilding shorter-horizon excess returns would break comparability with the baseline) and
are economically secondary for monthly / bimonthly signals.

## 3. Results

**Baseline `composite_sn` (reproduced on this panel — the integrity guard):** IC t **2.665**, quarterly
net-25bps **+0.00401**, net-50bps +0.00095; pre-2020 net25 +0.00522, post-2020 net25 +0.00325. This exact
reproduction confirms the comparison is trustworthy.

| signal | IC t (63d) | quarterly net-25bps | OOS frac + | outcome |
|---|---:|---:|---:|---|
| insider MSPR (latest) | **−0.38** | −0.0014 | 0.31 | wrong-signed / cost-killed |
| insider MSPR (3m mean) | **−0.76** | −0.0041 | 0.50 | wrong-signed / cost-killed |
| short-interest change | +0.97 | −0.0064 | 0.63 | weak / turnover cost-killed |

**Incremental blends vs `composite_sn`:**

| blend | net-25bps | subperiod-improvement survives? | outcome |
|---|---:|:--:|---|
| `+0.15·insider_mspr_3m` | −0.0016 | No | REJECT_COST_KILLED |
| `+0.30·insider_mspr_3m` | +0.0012 (< 0.00401) | No | NO_IMPROVEMENT |
| `+0.50·insider_mspr_3m` | −0.0012 | No | REJECT_COST_KILLED |
| `+0.30·insider_mspr_last` | −0.0010 | No | REJECT_COST_KILLED |

No blend beats the baseline net-25bps, and **every** blend fails the subperiod-net25 guard.

## 4. Decision — `NEW_DATA_NO_ALPHA`

The insider-sentiment MSPR and short-interest-change families are real, orthogonal, and adequately
covered, but they **do not add robust incremental alpha at 63d** over the modest quality composite: insider
MSPR is weak and *wrong-signed* to the buying-is-bullish hypothesis, and the short-interest-change edge is
too small to survive turnover costs. **The modest `composite_sn` baseline remains champion.** This is a
genuine negative result under the same bar every prior phase used, not a tuning failure.

Decision enum: `NEW_ALPHA_FOUND_READY_FOR_PAPER_RULES` · `NEW_DATA_NO_ALPHA` ·
`NEW_DATA_NEEDS_MORE_HISTORY` · `NEW_DATA_TEST_BLOCKED`.

## 5. Consequence

The genuinely free / currently-entitled orthogonal data on disk (insider sentiment, short interest, and
the too-shallow Finnhub recommendation trend) does **not** unlock a stronger alpha. The Phase 11-A #1
family — **analyst estimate revisions** — is only sparse locally (AlphaVantage 23 names / FMP 8 names,
free-tier rate/entitlement caps) and its full-universe PIT depth is **paid-gated**. The queue therefore
proceeds to **Phase 11-B4**, the concrete paid-data shopping cart for a bounded analyst-estimate-revisions
trial (explicit user opt-in required).

## 6. Artifacts (`research/output/phase11c_new_data_orthogonal_alpha_investigation/`)

`phase11c_new_data_orthogonal_alpha_investigation.json` · `signal_scorecard.csv` ·
`pit_join_coverage.csv` · `incremental_blend_results.csv` · `baseline_vs_champion.csv`.

## 7. Safety / constraints

Offline (reads only the owned/local frozen panel + already-downloaded normalized signals).
There are **no api calls**, no key, no provider probe, no new data purchased, no Paper Trader writes,
**no orders** and **no automation**, no broker, no deploy, no GCP, no payment. The modest 10-D baseline
is not oversold. Commit
only the phase11c files if targeted tests pass. No push.

## Run / test (Windows PowerShell only)

```powershell
Set-Location C:\Users\binis\Stock_Prediction_app_push
python -m py_compile research/run_phase11c_new_data_orthogonal_alpha_investigation.py
python research/run_phase11c_new_data_orthogonal_alpha_investigation.py
python -m pytest tests/test_phase11c_new_data_orthogonal_alpha_investigation.py -q
```
