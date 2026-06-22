# Phase 5-G — Earnings / Revision / Post-Earnings Drift Alpha Module (v1)

**Track A (quant brain) research. Preview-only. No deployment, no shadow trading, no
orders, no automation, no network, no paid data.** The local universe is full-history
survivors, so absolute returns remain survivorship-biased and **no production edge is
claimed.**

## Why this phase exists

The Phase 5-C → 5-F0 / 5-F0B / 5-F0C / 5-F0D lineage repeatedly tuned **portfolio
filters** (top-N, entry/exit bands, industry caps, regime scaling, confidence gating)
around the **same price/volume/regime signal**. 5-F0D found the first risk-controlled
arm that beat the raw 5-C baseline on net portfolio metrics, but the underlying
**ranking signal** never gained genuinely new information — every arm re-used price
momentum + relative strength + regime.

The strategic correction: **the next real improvement must add new predictive
information.** For a 5–20 trading-day horizon, the hypothesis is that **earnings
surprises, analyst estimate revisions, and post-earnings drift (PEAD)** carry signal
that delayed fundamentals do not. Phase 5-G tests that hypothesis with point-in-time,
out-of-sample, leakage-controlled evidence — and, critically, **inventories the local
event data first and refuses to assume it is usable.**

## Step 1 — Event/catalyst data inventory (the honest gate)

| Source (in-repo research artifact) | What it is | Verdict |
|---|---|---|
| `phase3m_earnings_estimates_signal_gate/earnings_events_universe.csv` | 5,625 EPS report events, 50 tickers, 1996–2026; `reported_eps`, `estimated_eps`, `surprise`, `surprise_percentage` | **PIT-safe, usable** |
| `phase3m_earnings_estimates_signal_gate/earnings_features_universe.csv` | 5,609 per-event rows, 12 trailing-only surprise features keyed by `availability_date` | **PIT-safe, usable** |
| `phase3s_event_signal_readiness_gate/analyst_revision_data_inventory.csv` | analyst-revision inventory | **NOT usable** — missing PIT columns (ticker, publication date, revision value) |
| `phase3s_event_signal_readiness_gate/earnings_coverage_status.csv` | coverage status (25–50 cached vs 128 universe; gate min 75) | coverage context |

**Point-in-time safety:** every earnings row carries `availability_date = reported_date`
(the filing/report timestamp), explicitly **not** the fiscal-quarter end. The validation
note in the source confirms it. The join key is therefore PIT-safe.

**Coverage:** 50 of the 128 Phase 5-C universe tickers (**39.1%**), the alphabetically
first large caps (AAPL→GOOGL) — collection was rate-limited at the provider and never
reached the project's own **75-ticker** event-signal gate.

**Revisions:** there is **no usable point-in-time analyst-estimate-revision series**.
`estimate_revision_proxy` in the feature panel is documented-empty. Per the hard rule
*"do not fake missing fields,"* Phase 5-G builds **no** revision features and records the
blocker instead of patching it.

**Conclusion: PARTIAL event data.** Build only the safe (earnings-surprise + PEAD)
features; omit revisions; measure incremental edge on the covered subset.

## What the harness does

1. Reuses the Phase 5-C harness (D: read-only price load, SPY-calendar alignment, the
   monthly PIT panel, the yearly walk-forward champion predictions, rank-IC math, the
   SPY regime proxy, the placebo probe). The champion price model is selected
   **dynamically as the highest-mean-IC 5-C model** (never a hardcoded id) and
   restricted to event-covered names. On the real D: price history that champion is
   **`top_quintile_score_model`**, recorded in `phase5c_reference_meta.champion_model`.
2. Builds a **PIT earnings-event panel**: for each monthly rebalance date and covered
   ticker, joins the most recent event with `availability_date <= rebalance_date`
   (strict as-of, no look-ahead) and derives the features below.
3. Evaluates **five models on the identical covered cross-section** (apples-to-apples):
   `phase5c_reference`, `event_alpha_only`, `price_plus_event_alpha`,
   `event_gated_price_signal`, and `best_event_candidate` (the best event arm by
   incremental OOS IC, guarded by a placebo floor + cross-fold visibility).
4. Validates walk-forward with rank IC, IC t-stat, IC by year, decile / top-N spread,
   net-of-25/50bps annualized-mean return, turnover, drawdown, regime breakdown,
   comparisons vs Phase 5-C and Phase 5-F0C, and a within-date label-shuffle placebo.

### Features built (safe subset)

`surprise_direction`, `eps_surprise_pct` (winsorized), `surprise_magnitude_abs`,
`trailing_4q_avg_surprise_pct`, `trailing_8q_avg_surprise_pct`,
`trailing_4q_positive_surprise_rate`, `surprise_acceleration`, `days_since_earnings`,
`post_earnings_drift_window` (within ~63 sessions), `fresh_positive_surprise`
(direction gated to the drift window — the PEAD core bet), and `announcement_reaction`
(the realized 2-day earnings-window price move, used only when the whole window closes
**before** the as-of date, so it is always past data).

### Features NOT built (no usable PIT source — not faked)

`estimate_revision_direction`, `estimate_revision_magnitude`, `revision_acceleration`.

## Leakage controls

- **Strict as-of join** — never reads an event whose `availability_date` is after the
  rebalance date. Unit-tested (`test_asof_join_never_uses_future_event`).
- The event composite is a **fixed PIT transform (no fit)**; the price score is already
  OOS from the 5-C yearly walk-forward (≥20-session embargo).
- **Placebo label-shuffle** per model — a real signal must collapse to ~0 after a
  within-date label permutation.

## v1 live result (synthetic-price tests are hermetic; this is the real-data run)

| Model | mean rank IC (covered) | 5d / 10d / 20d IC | placebo IC |
|---|--:|--|--:|
| phase5c_reference (`top_quintile_score_model`) | 0.0365 | — | 0.0116 |
| event_alpha_only | 0.0288 | — | — |
| **price_plus_event_alpha (best)** | **0.0455** | 0.0520 / 0.0521 / 0.0455 | 0.0080 |
| event_gated_price_signal | 0.0235 | — | — |

(Exact real-run values: reference 0.036471, best 0.045538, incremental +0.009067 —
reproduced deterministically across consecutive offline runs.)

- **Incremental edge over Phase 5-C:** **+0.0091** mean rank IC (0.0455 vs 0.0365,
  ~+25% relative), clearing the +0.005 threshold, placebo-clean, and positive across
  the majority of yearly folds.
- The event signal is **complementary, not standalone** — `event_alpha_only` (0.0288)
  is weaker than price alone, but **combining** price + event beats price-only. The
  edge is strongest at the **5–10 day** horizon (0.052), consistent with the PEAD
  hypothesis that earnings information decays over a few weeks.
- **Gate summary: PASS 13 / WARNING 2 / FAIL 0.** The two WARNINGs are
  `coverage_sufficient` (50 < 75) and the standing `survivorship_bias`.

## Recommendation

**`EVENT_DATA_PARTIAL_BUT_USEFUL`.**

A real, leakage-clean, out-of-sample **incremental** event edge over the Phase 5-C
champion exists on the covered names — the hypothesis is supported. But coverage is
**below the project's own 75-ticker event gate** and there is **no PIT revision
series**, so the result is **not yet a shadow-ready strategy**.

### What this does and does NOT mean

- **Does** mean: adding earnings-surprise / PEAD information improves the ranking
  signal, which portfolio-filter tuning never did. This is the first new-information
  win in the lineage.
- **Does NOT** mean: deploy, shadow-trade, connect to Paper Trader, or trust the
  absolute portfolio returns (still survivorship-inflated and computed on 50 names).

## Recommended next phase — 5-H (gated)

`phase5h_next_alpha_plan.json` records `proceed_to_event_alpha_strategy_test: false`
until:
1. PIT earnings coverage is expanded to **≥75** universe tickers (resumable,
   cache-first collection in a **separate controlled step** — never from this harness);
2. a **point-in-time analyst-estimate-revision series** is added (ticker, publication
   date, prior estimate, new estimate);
3. survivorship is resolved on a survivorship-free universe before absolute returns are
   trusted.

## Artifact-consistency audit (determinism / baseline reconciliation)

An earlier delivery had the JSON artifact say `NO_INCREMENTAL_EVENT_EDGE` while the
written summary said `EVENT_DATA_PARTIAL_BUT_USEFUL`. **Root cause:** `run()` wrote
every artifact to hardcoded module-global production paths, and the test suite called
`run(price_csv=<synthetic>)` with no output redirect — so the tests **overwrote the
committed production JSON with synthetic-data metrics**. The synthetic run found no
edge; the real run did. The committed JSON's `price_history_source` pointed at a
`pytest-of-binis\…\test_determinism0\a.csv` temp path, which is the fingerprint of
the clobber.

**Fix:**
- `run(out_dir=…)` now threads a single output directory through every writer.
  Tests pass a temporary `out_dir`; they can no longer touch the production
  directory. The default remains the committed production directory.
- A committed-safe audit artifact `phase5g_artifact_consistency_audit.csv`
  reconciles the JSON recommendation, the console-summary recommendation (rendered
  from the same report dict, so they cannot diverge), the Phase 5-C reference model
  actually used, the reference / best-event ICs, whether the recommendation is
  supported by those metrics, whether tests write to temp only, and whether a
  production overwrite was detected. The real run reports `final_status = CONSISTENT`.
- New tests assert: the run never modifies the production directory; the test module
  redirects every run to temp; the JSON recommendation is supported by the
  scoreboard; the Phase 5-C reference is dynamically selected (no hardcoded id) and
  documented; and two consecutive offline runs produce identical per-model ICs.

## Run command (Windows PowerShell)

```powershell
Set-Location C:\Users\binis\Stock_Prediction_app_push
python research\run_phase5g_earnings_revision_alpha.py
python -m pytest tests\test_phase5g_earnings_revision_alpha.py -q
```

## Artifacts (all committed-safe text)

`phase5g_earnings_revision_alpha.json`, `event_data_inventory.csv`,
`event_data_pit_safety_report.csv`, `event_feature_catalog.csv`,
`event_alpha_panel_sample.csv`, `event_alpha_model_scoreboard.csv`,
`event_alpha_incremental_edge_report.csv`, `event_alpha_validation_gate_matrix.csv`,
`phase5h_next_alpha_plan.json`, `phase5g_artifact_consistency_audit.csv`.

## Safety contract

No live network / API calls · no provider shopping · no FMP · no SimFin fundamentals as
alpha · no new paid data · no package installs · D: read-only price input only · no
writes to D: · no Paper Trader / GCP / deploy · no orders / broker / automation · no
binary model artifacts · no commit · no push.
