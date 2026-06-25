# Phase 8-G — External Data Activation and Signal Confirmation Campaign

**Status:** `PROMISING_EXTERNAL_SIGNAL_FOUND`
**Repo:** `C:\Users\binis\Stock_Prediction_app_push` · **Engine:** `research/run_phase8g_external_data_activation_campaign.py`
**Tests:** 25/25 (8-G) · 247/247 (full phase-8) · **Not committed, not pushed.**

8-F built the autonomous external-signal OS but detected 0 provider keys, so every external family
stayed `NEEDS_PROVIDER` (adapters + mock fixtures only). 8-G **stops scaffolding** and activates the
real external-event data that is reachable **without a paid key**, then asks the one question:

> **Does real external event data turn a promising sensitivity lead into a confirmed signal?**

**Answer: It moves it materially toward confirmation, but does not yet confirm it.** Overlaying a
**real positive earnings surprise** on the S8E-011 macro lead roughly **triples EV-after-cost
(+0.128% → +0.378% per 20d)**, improves the worst-decile tail, and — combined with the 8-F
pre-declared beta filter — even flips *portfolio-beats-SPY* positive and clears the −12% tail floor.
But the confirmed subset collapses to a few hundred events, so the signal is **PROMISING, not
CONFIRMED**: the binding constraint is now **event coverage**, i.e. more ticker/year history.

---

## What was activated (real data, no paid key)

| Source | Mode | Real? | Events | Track |
|---|---|---|---|---|
| Earnings surprise (`phase3m/earnings_events_universe.csv`) | LOCAL_CACHE_ACTIVATED | **yes** | 8,320 PIT events (8,293 joined obs) | C, A/F |
| Analyst-revision **proxy** (`earnings_features_universe.csv`, `surprise_acceleration`) | LOCAL_PROXY_ACTIVATED | yes (labelled proxy) | capped < CONFIRMED | B |
| SEC filings (local `phase3f` + **no-key EDGAR live**) | SEC_LOCAL+LIVE | **yes** | 7,834 filings (5,926 joined obs) | H |
| News / sentiment (GDELT no-key) | ATTEMPTED → BLOCKED (HTTP 429) | no | 0 | A (blocked) |
| Options IV / short interest | NEEDS_PROVIDER | no | 0 | D, E |

- **Provider keys detected: 0 of 12** (names/presence only; values never read).
- **Live no-key collection ran: yes** — a bounded SEC EDGAR submissions pull (≤60 tickers, free
  public API, cached on `D:`) produced 7,834 real 8-K/10-Q/10-K filing events.
- **News/sentiment was NOT activated** — the GDELT public endpoint rate-limited us (429); the
  adapter stays executable and the schema is ready.
- Real normalized events live on `D:\Stock_Prediction_app_data\external_normalized\{earnings,
  analyst_revision,filings_sec}`; the repo gets compact event panels + manifests only.

All joins are leak-safe: each event attaches to the **first weekly grid observation on/after its
point-in-time `availability_date`** (`merge_asof` backward), and forward labels are forward of that
observation. The surprise-sensitivity cohort is **estimated from each ticker's own *prior* earnings
reactivity** (expanding `sign(surprise)·fwd_excess_20`, ≥4 prior events) — never assumed.

---

## The headline: S8E-011 + real earnings confirmation (Track A/F)

Fixed thresholds from 8-E/8-F; no tuning. `s8e011_external_confirmation_scoreboard.csv`:

| Variant | n | lift vs ctrl | EV@25bps | worst-decile | recent lift | beats SPY |
|---|--:|--:|--:|--:|--:|:--:|
| S8E-011 baseline | 11,881 | +0.40% | **+0.128%** | −15.8% | +0.34% | False |
| **+ real earnings confirm** | 692 | +0.53% | **+0.378%** | −15.5% | +0.49% | False |
| + confirm + remove_bottom_liquidity_q | 588 | +0.73% | +0.383% | −16.0% | +0.84% | False |
| + confirm + **remove_extreme_beta_tails** | 146 | −0.52% | **+0.504%** | **−11.8%** | −1.49% | **True** |
| + confirm + remove_top_volatility_q | 416 | −0.06% | +0.124% | −12.9% | −0.25% | **True** |

Reading it honestly: real earnings confirmation **improves EV-after-cost ~3×** and the tail; adding
the pre-declared beta/vol filter pushes *beats-SPY-active* to **True** and clears the −12% tail —
but only on ~150 events with negative recency. So the direction is real and consistent, the
**coverage is too thin to confirm**. This is `PROMISING_EXTERNAL_SIGNAL`, registered as **S8G-F20**.

---

## Every pre-registered setup (fixed 8-E gate)

`external_signal_scoreboard.csv` — 9 testable + 3 provider-blocked, 33% challenges:

| id | family | n | lift | EV@25bps | promotion | why |
|---|---|--:|--:|--:|---|---|
| **S8G-F20** | S8E-011 + earnings confirm | 692 | **+0.53%** | **+0.378%** | **PROMISING_EXTERNAL_SIGNAL** | +lift +EV +recency; fails only coverage/tail/SPY |
| S8G-C20 | earnings PEAD ×cohort 20d | 2,127 | −0.12% | −0.59% | REJECTED | PEAD dies net-of-cost |
| S8G-C05 | large surprise 5d | 723 | +0.37% | −0.42% | REJECTED | raw lift, cost-negative, thin |
| S8G-C60 | earnings PEAD 60d | 2,104 | −0.01% | −0.22% | REJECTED | no edge, −22.6% tail |
| S8G-B20 | analyst-revision **proxy** | 1,345 | +0.16% | −0.38% | REJECTED | proxy lift erased by cost |
| S8G-H20 | SEC filing ×cohort (no-key) | 2,202 | −0.30% | −0.58% | REJECTED | filings alone don't predict drift |
| S8G-901/902/903 | challenges/placebos | — | — | — | REJECTED (diagnostic) | cohort/sign/no-cohort controls |
| S8G-A01/D01/E01 | news / options / short | — | — | — | NEEDS_PROVIDER | no local data + no key |

The challenges behave: the no-cohort rates placebo (S8G-903) shows ≈0 lift, confirming the
short-duration cohort carries the S8E-011 confirmation; flagged challenges with residual lift are
recorded in `multiple_testing_report.csv` (`challenges_showing_lift`).

---

## End-of-task report (answers to the required list)

- **Exact files changed (all new, untracked):**
  `research/run_phase8g_external_data_activation_campaign.py`,
  `tests/test_phase8g_external_data_activation_campaign.py`, this doc, and
  `research/output/phase8g_external_data_activation_campaign/` (25 artifacts). Real normalized
  events + SEC cache written under `D:\Stock_Prediction_app_data\` (off-repo). No tracked file modified.
- **Local external artifacts found:** real earnings-surprise cache (8,390 rows), earnings features
  with a revision proxy (8,363), SEC EDGAR fundamentals sample (368), SimFin fundamentals, FMP
  profiles. (`local_external_artifact_inventory.csv`.)
- **No-key/free sources attempted:** SEC EDGAR submissions (**succeeded**, 7,834 filings); GDELT
  news (**rate-limited 429 → blocked**).
- **Provider keys detected:** **No** (0 of 12; names/presence only, never printed).
- **Live external collection ran:** **Yes** — bounded no-key SEC EDGAR (≤60 tickers, cached on D:).
- **News/sentiment actually activated:** **No** — GDELT rate-limited; adapter + schema remain ready.
- **External events normalized:** earnings (8,320), revision proxy, SEC filings (7,834) — all PIT.
- **S8E-011 external confirmation result:** real earnings confirmation lifts EV@25bps +0.128% →
  +0.378% (~3×) and improves the tail; with the fixed beta/vol filter it beats SPY-active and clears
  the −12% floor on ~150 events → **PROMISING (S8G-F20)**, not CONFIRMED.
- **Confirmed external signals:** **none.**
- **Promising external signals:** **1** — S8G-F20 (S8E-011 + earnings confirmation).
- **Provider history gaps:** true analyst-revision feed (replaces proxy), news/sentiment history,
  options IV, short interest; broader universe for more PEAD/confirmation events.
- **Tests pass:** 25/25 (8-G), 247/247 (full phase-8), no regression.
- **Commit appropriate:** **No** — per instruction (do not commit, do not push).
- **Next autonomous phase (8-H):** acquire a real analyst-revision feed and re-run family B on the
  identical gate; activate FINRA biweekly short interest (free, no key) → family E; broaden the
  daily universe (S&P 1500 / Russell 3000) and re-run the earnings-confirmation overlay for more
  events; scale the no-key SEC EDGAR pull. (`phase8h_next_plan.json`.)

## Safety contract honored

Local data first; real data activated before asking for providers; Norgate + FRED for price/macro;
no package install; large data on `D:` only; **no secrets printed** (keys by name/presence only);
point-in-time joins only; thresholds fixed a priori; ≥30% challenges; **external data never faked**
(the revision track is a labelled proxy, capped below CONFIRMED; mock fixtures excluded); no Paper
Trader / GCP / deployment / broker / orders / automation; no weight optimization; no factor-sign
flipping; no regime activation; no ML fit; failed experiments not hidden; **not committed, not pushed.**

## Artifacts (25)
`phase8g_external_data_activation_campaign.json`, `external_source_activation_log.csv`,
`provider_key_inventory.csv`, `local_external_artifact_inventory.csv`,
`external_raw_cache_manifest.csv`, `external_normalized_event_manifest.csv`,
`analyst_revision_event_panel.csv`, `earnings_surprise_event_panel.csv`,
`news_sentiment_event_panel.csv`, `filings_event_panel.csv`, `options_iv_event_panel.csv`,
`short_interest_event_panel.csv`, `s8e011_external_confirmation_scoreboard.csv`,
`external_signal_experiment_registry.csv`, `external_signal_scoreboard.csv`,
`matched_control_report.csv`, `confirmed_external_signals.csv`, `promising_external_signals.csv`,
`needs_provider_history.csv`, `failed_external_signals.csv`, `validation_skeptic_report.csv`,
`multiple_testing_report.csv`, `model_candidate_registry_update.csv`,
`research_director_decision.json`, `phase8h_next_plan.json`.
