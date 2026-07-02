# Phase 10-O — Regime And Conditional Alpha Gating (v1)

## 1. Why this phase exists

Phases 10-M (incremental factors) and 10-N (transforms / interactions) both failed to beat the frozen 10-D
`composite_sn` baseline out-of-sample. The last owned-data avenue before declaring exhaustion: is the
modest baseline edge **conditional** — meaningfully stronger inside a simple, pre-declared, ex-ante
market/macro **regime** (and identifiable before the trade)? If so, a paper book that runs only in the
favourable regime could carry a stronger, tradeable edge.

Phase 10-O tests that narrowly and skeptically. It uses **only** owned/local regime data already on the
panel (FRED macro flags + benchmark trend, and month/quarter-level vol / dispersion / liquidity
reconstructed from owned prices). It makes **no live macro API call**, adds **no new data**, uses **only
simple median / majority thresholds** (no tuned regime boundaries), requires adequate sample per regime,
and — critically — requires any conditional edge to **beat the always-on baseline in both the pre- and
post-2020 subperiods**. Selecting the best of several regimes is a textbook overfit trap; the
subperiod-generalisation guard + sample-adequacy + a meaningful (≥1.25×) margin are the defenses.

## 2. Regimes (pre-declared; all converted to pure QUARTER-level time regimes; ex-ante / PIT)

- owned macro/market flags: `easy_regime`, `high_rates`, `market_drawdown`, `high_oil`, `strong_dollar`;
- owned macro levels (median split): `rates_10y`, `rates_2s10s`, `oil_z`;
- reconstructed (quarter aggregate → median split): `market_vol` (mean `vol_63`), `return_dispersion`
  (std `mom_pre_63`), `market_liquidity` (median dollar-volume).

Each regime is resolved at the **quarter** level (majority for flags, median split for continuous), so a
conditional strategy trades **whole favourable quarters** — never a partial within-quarter cross-section
(which would not be an implementable timing overlay). This quarter-level resolution was a deliberate fix:
a month-level state lets a single calendar quarter straddle two states and inflates the favourable-state
spread by cherry-picking events within quarters.

## 3. Method + gates

For each regime, split quarters into two states, evaluate `composite_sn`'s quarterly net-25bps + 63d IC t
+ sample in each, take the **favourable** state (higher net25), and judge the conditional
(favourable-only) strategy vs the always-on baseline. A regime is a champion **only if all** hold:

1. favourable-state net25 is **meaningfully** higher than baseline (≥1.25× and strictly up);
2. favourable state has adequate sample (≥10 quarters, ≥6000 events; not a tiny regime);
3. favourable-state 63d IC t is not materially worse (≥ base − 0.10);
4. favourable-state net25 **beats the always-on baseline in both the pre- and post-2020 subperiods**
   (each with ≥3 quarters) — the edge must generalise across eras, not be a one-era relic.

Decision enum: `CONDITIONAL_ALPHA_READY_FOR_PAPER_RULES` · `BASELINE_REMAINS_CHAMPION` ·
`REJECT_REGIME_OVERFIT` · `NEEDS_REGIME_INPUT_REPAIR` · `NEEDS_MORE_OWNED_DATA`.

## 4. Result (this run) — `REJECT_REGIME_OVERFIT`

The panel reproduced the frozen 10-D baseline exactly (63d IC t 2.665, net25 +0.00401 — `reproduces=True`).
Baseline subperiods: **pre-2020 net25 0.00522, post-2020 0.00325.**

Several regimes showed a much stronger *full-sample* favourable-state net25 — `market_liquidity` 0.0108
(2.7×), `market_vol` 0.0105 (2.6×), `curve_2s10s` 0.0092 (2.3×), `return_dispersion` 0.0066, `high_rates`
0.0065, `rates_10y` 0.0065 — but **every one failed the strict test**, and the failure is the same in each
case: the lift is entirely **post-2020**. In the pre-2020 era the "favourable" regime is at or **below**
the always-on baseline (curve 0.0007, rates_10y 0.0013, market_vol 0.0040, high_rates/liquidity 0.0047 —
all ≤ baseline's 0.0052), and `market_vol` / `rates_10y` / `return_dispersion` additionally carry a
materially weaker cross-sectional IC t. The remaining regimes (`market_drawdown`, `high_oil`,
`strong_dollar`, `oil_momentum`) were not even 1.25× above baseline; `easy_regime`'s favourable state had
only 9 quarters (tiny sample).

The economic reading is honest and coherent: the quality composite's edge has simply been **stronger in
recent years**, and several regime variables that trend with time (liquidity, low-vol, steep-curve) act as
proxies for "post-2020" rather than as genuine, era-stable conditioning signals. No simple owned/local
regime turns the modest baseline into a meaningfully stronger, era-robust, tradeable edge. Nothing is
productized; the always-on two-leg baseline remains champion.

## 5. Why the alpha remains modest / boundary

Regime conditioning did not strengthen the baseline in a way that generalises. The two-leg `composite_sn`
remains a **modest / boundary** alpha (63d IC t ≈ 2.665, below the 3.0 strong bar; small net-of-cost
quarterly edge; the short `operating_accruals` leg carrying most of the robustness). The apparent
regime "wins" are post-2020 artifacts. This is not oversold and is not a prediction oracle.

## 6. What this phase did **not** test

Any live macro feed or new data; tuned regime thresholds; a properly-reconstructed value leg (deferred).
It makes **no live API calls**, creates **no orders** and **no automation**, connects to no broker, writes
nothing to the Paper Trader, and does not deploy.

## 7. What comes next

`REJECT_REGIME_OVERFIT` and, together with 10-M (`BASELINE_REMAINS_CHAMPION`) and 10-N
(`REJECT_TRANSFORM_OVERFIT`), the owned-data avenues for a **stronger** alpha are exhausted →
**Phase 10-Q** (owned-data-exhaustion research decision): package the modest baseline for paper review, or
pause pending new owned data (a PIT-normalized value leg is the clearest gap).

## Artifacts (`research/output/phase10o_regime_conditional_alpha_gating/`)

`phase10o_regime_conditional_alpha_gating.json` · `regime_inventory.csv` ·
`regime_conditional_scorecard.csv` · `regime_state_detail.csv` · `regime_subperiod_report.csv` ·
`rejected_regimes.csv`.

## Run / test (Windows PowerShell only)

```powershell
Set-Location C:\Users\binis\Stock_Prediction_app_push
python -m py_compile research/run_phase10o_regime_conditional_alpha_gating.py
python research/run_phase10o_regime_conditional_alpha_gating.py          # offline; no key
python -m pytest tests/test_phase10o_regime_conditional_alpha_gating.py -q
```

## Constraints honored

Offline (no network / key / provider probe / **live macro API**); **owned/local data only**; **simple
median/majority thresholds only; no tuned regime boundaries; no new data**; **no Paper Trader writes; no
orders; no automation; no broker; no live trading; no deploy; no GCP**; no package install; targeted tests
only; output is research metadata only. **No commit. No push.**
