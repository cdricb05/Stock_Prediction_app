# Current Model — Walk-Forward Results & Assessment (v1)

_Authored 2026-06-16 as a truthful quantitative assessment of the current GCP
prediction engine. Observational only: no model change, no live-API change, no
orders, no automation, no DB writes._

> **Status of the numbers in this document.** Two evidence tiers are kept strictly
> separate:
> - **Tier A — executed (real):** the `--fast-smoke` walk-forward that was actually
>   run on the GCP VM (3 tickers, 47 graded observations, ~1 month). Source:
>   [current_model_walk_forward_baseline_v1.md](current_model_walk_forward_baseline_v1.md).
> - **Tier B — structural (proven by code, not by sample):** facts read directly
>   from `api_server.py` — they hold regardless of sample size.
> - **Tier C — pending the full run:** full-universe, multi-year metrics. The harness
>   now computes all of them; the run has **not** been executed at scale by this
>   assessment (see *How to reproduce*). Tier-C cells say "pending full run".
>
> Nothing here should be read as a quant-grade claim. The current model is
> **price-only** (adjusted close); it does **not** use news, sentiment, macro,
> seasonality, fundamentals, sector, beta, liquidity, or calibrated risk.

---

## How to reproduce (exact GCP VM command)

Run on `stock-prediction-vm-new` (`/home/binisti/Stock_Prediction_app`) as the
repo owner. Read-only: it reads `stock_prices` and replays the model. It does not
restart the service, deploy, install packages, change env, or write to the DB.

```bash
cd /home/binisti/Stock_Prediction_app
set -a; . ./api.env; set +a          # DB_URL only; documented harness usage
# Sanity check (≈1 min):
PYTHONPATH=. /home/binisti/venv/bin/python -m research.run_walk_forward_baseline --fast-smoke
# Full walk-forward, nice'd so it cannot starve the live API:
nice -n 19 ionice -c3 env PYTHONPATH=. /home/binisti/venv/bin/python \
    -m research.run_walk_forward_baseline \
    --start-date 2024-01-01 --end-date 2025-12-31 --max-tickers 60
```

Outputs: this file (`docs/current_model_walk_forward_results_v1.md`, regenerated
with full data) and a per-prediction table at
`research/output/walk_forward_predictions_v1.csv`. Drop `--max-tickers` for the
full universe (heavier; keep it nice'd).

**Leakage controls (enforced by the harness, covered by tests):** inputs are a
point-in-time DB slice `date <= as_of` (no yfinance refresh, no future rows);
the target uses sessions strictly in the future (`i+horizon`); "5 days" means 5
*available sessions*; duplicate `(ticker,date)` rows are de-duplicated. The replay
imports the exact production functions from `api_server` so it cannot drift from
the live model.

---

## Headline answers to the required questions

| Question | Answer | Tier |
|---|---|---|
| Tickers evaluated | **3** (AAPL, MSFT, NVDA) — smoke; up to full universe in full run | A / C |
| Date range evaluated | graded as-of **2026-05-04 → 2026-06-08** (~1 month) | A |
| Walk-forward observations | **47** graded (47 replayed, 47 with SPY excess) | A |
| Prediction horizon | **5 trading sessions** | A/B |
| Model avg forward 5d return | **+1.12%** (all graded rows) | A |
| Universe baseline forward 5d return | **+1.12%** (equal-weight, same rows) | A |
| Model excess vs universe baseline | **≈ 0.00%** (BUY picks did not beat the universe) | A |
| Excess vs SPY (BUY bucket) | **+0.30%** (n=29, std 3.6%) | A |
| Precision@K (top ranked) | top-10 **30%** positive / **50%** beat-SPY — *below* the 55%/62% base rates | A |
| Hit rate, positive 5d | **55.3%** overall (= base rate; no lift) | A |
| Rank IC / Spearman (pred vs realized) | **−0.165** (pred vs excess **−0.211**) — *wrong sign* | A |
| Performance by score bucket | pending full run (now computed) | C |
| Performance by confidence bucket | pending full run (now computed) | C |
| Is confidence a calibrated probability? | **No** — it is `100 − CV` of forecasts; calibration non-monotonic in smoke | A/B |
| Are BUY/HOLD/SELL meaningful out of sample? | **Not in smoke** — BUY ≈ HOLD ≈ universe; SELL n=1 | A |
| Is top-5 dispatch too narrow? | top-K precision below base rate ⇒ narrow top-list **not** justified by skill here; full top-N sim pending | A/C |
| top 5 / top 10 / top 25 / top 50 / all simulation | pending full run (now computed by harness) | C |
| Main reasons candidates fail the gate | gate = agreement≥0.55 **&** conf≥30 **&** \|move\|≥0.5%; per-reason tally pending full run | B/C |
| Proven by data vs assumed | see *What is proven vs assumed* | — |
| Build / refactor / rebuild | **Rebuild the signal & risk layer; keep the serving spine** | — |

---

## Tier A — Executed evidence (fast-smoke, n=47)

Real run on the VM, 3 mega-cap tickers, ~1 month, horizon 5, lookback =
`api_server.LOOKBACK_DAYS` (180), min history 60, benchmark SPY. All 5 fast models
(Drift, LinearTrend, XGBoost, Naive, SMA) contributed to 100% of predictions; 0
model errors.

**Recommendation distribution:** BUY 29 (61.7%), HOLD 17 (36.2%), SELL 1 (2.1%).
Raw labels: Buy=24, Hold=17, Sell=1, Strong Buy=5.

**Return by recommendation:**

| Bucket | n | Mean realized 5d | Mean excess vs SPY |
|---|---:|---:|---:|
| BUY | 29 | +1.11% | +0.30% |
| HOLD | 17 | +1.62% | +1.40% |
| SELL | 1 | −6.88% | −6.16% |

Universe (all 47) mean realized 5d ≈ **+1.12%**. BUY (+1.11%) ≈ universe and < HOLD
(+1.62%): the bucket that is supposed to be the "best" names slightly *trailed* the
ones the model declined to act on. No selection edge in this window.

**Ranking quality (the decisive metrics):**
- Spearman rank IC, predicted vs realized 5d return: **−0.165**
- Spearman rank IC, predicted vs realized excess vs SPY: **−0.211**
- Pearson IC: +0.027
- Precision@10: **30%** positive / **50%** beat-SPY; Precision@25: **44%** / **52%**.
  Random selection scores the base rates (**55.3%** positive, **61.7%** beat-SPY),
  so the top-ranked names did **worse than random** here.

A negative rank IC plus below-base-rate precision means, in this window, the score
was *inversely* related to the outcome. With n=47 over one month and three highly
correlated mega-caps this is **not statistically conclusive** — but it is the
opposite of edge, and it is real.

**Confidence calibration:** hit rate does **not** rise across confidence deciles
(non-monotonic); confidences are crammed in the 97–100 band. Confidence is not
behaving like a probability.

**BUY downside:** worst 5d −6.02%, 44.8% of BUYs negative, 20.7% below −2%, average
loss among losers −2.27%. There is no calibrated interval to size or filter these.

**Verdict from the executed run:** *INCONCLUSIVE on edge* (sample too small to
reject the null with confidence) — but every directional signal that exists points
*away* from skill.

---

## Tier B — Structural facts (proven by `api_server.py`, sample-independent)

- **Price-only inputs.** Models consume the adjusted-close series only
  (`get_data_from_db` / `get_fresh_series`). No news, sentiment, macro,
  seasonality, fundamentals, sector, beta, liquidity, or calibrated risk exist in
  the pipeline. Any such claim would be fabricated.
- **Confidence is dispersion, not probability.** `compute_agreement_confidence`
  sets `confidence = clamp(0,100, 100 − stdev/mean × 100)` of the member day-5
  forecasts. It measures how much the models *agree with each other*, not how
  often they are *right*. It is uncalibrated by construction.
- **Agreement is a vote share**, `max(bull,bear)/(bull+bear)` over models whose
  move exceeds a 0.3% flat band — again about model concord, not accuracy.
- **The actionability gate is a fixed rule, not a learned one.**
  `make_recommendation` emits BUY/SELL only when
  `agreement ≥ 0.55` **and** `confidence ≥ 30` **and** the ensemble move clears
  ±0.5% (weak) / ±2% (strong); otherwise HOLD. The harness now records *which* of
  these failed per row (`hold_reason`): `insufficient_eligible_models`,
  `low_agreement`, `low_confidence`, `below_direction_band`.
- **No prediction intervals.** `build_predictions_list` returns `lo=None, hi=None`.
  There is no calibrated downside, so sizing/filtering has no statistical basis.
- **The ensemble is a robust average** of price-only members; there is no
  cross-sectional model, so a "top-N across the universe" ranking is only as good
  as point forecasts that share one input.

---

## Tier C — Full-run sections (harness computes these; run pending)

The harness now emits these sections; they require the full-universe run above to
populate with real numbers. They are listed so the reader knows exactly what the
full run answers:

- **Performance by predicted-return (score) quintile** — does realized return rise
  Q1→Q5? (a real signal is monotone). Computed by `metrics.bucket_stats`.
- **Performance by confidence quintile** — realized return & hit rate per quintile.
- **Top-N selection simulation (dispatch width)** — for each as-of date, rank by
  predicted return and take top 5 / top 10 / top 25 / top 50 / all-eligible, equal weight,
  averaged across dates; reports mean return, hit rate, and excess vs all-eligible.
  This is the direct test of *"is the top-5 dispatch too narrow?"* — if top-5 does
  not beat wider lists, the narrow policy is not earning its keep. Computed by
  `metrics.topn_simulation`.
- **Actionability-gate failure breakdown** — count & share of each `hold_reason`,
  i.e. *why* candidates never become BUY/SELL. Computed by `metrics.tally`.
- **Rank-IC stability / regime splits / confidence intervals** — the full run gives
  enough observations to bootstrap these; the smoke run does not.

The Tier-A signal (negative IC, no BUY edge, sub-base-rate precision) is what we
expect to see confirmed or refined at scale. The full run is needed before any
*positive* claim of edge could ever be made.

---

## What is proven vs what is still assumed

**Proven (this evidence):**
- The engine is price-only with an uncalibrated, dispersion-based "confidence"
  and a fixed-threshold gate — *proven by code*.
- In the executed window, the model's ranking had **negative** rank IC, its top-K
  picks underperformed the base rate, and its BUY bucket did **not** beat the
  equal-weight universe — *proven by the n=47 run*.
- There are no prediction intervals and therefore no calibrated risk control —
  *proven by code*.

**Still assumed / not yet proven:**
- That the negative-IC result holds at full universe scale and across regimes (the
  smoke is one month, three correlated mega-caps — could be noise or a single
  adverse regime). **Run the full walk-forward to settle this.**
- That a wider dispatch list would help — plausible from the precision numbers but
  unmeasured until the top-N simulation runs at scale.
- Anything about news/sentiment/macro/fundamentals edge — **not present in the
  system at all**; cannot be claimed either way.
- Survivorship: the universe is whatever is in `stock_prices` today; results are
  **not** survivorship-controlled. Treat cross-sectional claims with that caveat.

---

## Conclusion: build on / refactor / rebuild

**Rebuild the signal and risk layer; keep and reuse the serving spine.**

Rationale:
- *Keep* the parts that are sound engineering and not the problem: the FastAPI
  service, the point-in-time DB access, the de-duplication, and **this
  walk-forward harness** (it is the asset that makes any future claim testable).
- *Rebuild* what actually determines quality, because the evidence and the code
  agree it is inadequate:
  1. **The signal.** A price-only ensemble showed negative rank IC and no
     selection edge in the one window we have. Even setting significance aside, a
     single-input forecast cannot be expected to rank a cross-section. This needs
     real point-in-time features (fundamentals, momentum/seasonality computed
     without leakage, sector/beta/liquidity) and a cross-sectional learner — not
     more price-only members.
  2. **Confidence → calibrated probability.** Replace `100 − CV` with a probability
     calibrated against realized outcomes (and report Brier/reliability).
  3. **Risk.** Add calibrated prediction intervals so downside can be sized and
     filtered; today there are none.
  4. **The decision rule.** Replace fixed return/agreement bands with a validated
     rule, and re-derive dispatch width from the top-N simulation rather than a
     hard-coded top-5.

This is "rebuild the model" not "rebuild the system": the brief's audit and this
run point at the *signal/confidence/risk* layer, which is exactly what should be
replaced. Do not call anything quant-grade until the full walk-forward shows a
stable, positive, out-of-sample rank IC with confidence intervals and a calibrated
probability — none of which is true today. See
[quant_model_upgrade_roadmap.md](quant_model_upgrade_roadmap.md).

_Observational report. It changes no model, API, threshold, order, or automation._

