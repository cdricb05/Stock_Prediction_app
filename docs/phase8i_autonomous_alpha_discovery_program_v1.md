# Phase 8-I — Autonomous Alpha Discovery Program

**Status:** `PROMISING_ALPHA_SIGNAL_FOUND`
**Repo:** `C:\Users\binis\Stock_Prediction_app_push` · **Engine:** `research/run_phase8i_autonomous_alpha_discovery_program.py`
**Tests:** 23/23 (8-I) · full phase-8 suite green · **Not committed, not pushed.**

8-G found the first real external lead (S8G-F20); 8-H proved its blocker is event **coverage**, not
Norgate or the daily panel. 8-I stops orbiting one setup and runs an **autonomous 5-cycle program**
across every reachable information family, builds a durable candidate registry, ranks the leads, and
answers the only question that matters now:

> **What is the best current path to a real signal, and which candidates / data sources should be
> pursued next without user micro-direction?**

**Answer:** there are **real, economically positive promising leads** (5), **none CONFIRMED** on the
current local/no-key data, and the **best current path is local and provider-free** — apply the fixed
structural (beta-tail / volatility) filter to the full-coverage macro lead **S8E-011** and re-validate
its stability — while a broad earnings+revision provider feed is the highest-ceiling parallel move.

---

## What ran (5 autonomous cycles, fixed 8-E gate, nothing tuned)

| Cycle | Work | Result |
|---|---|---|
| 1 | Rebuild state from 8-E..8-H (memory, backlog, registry, graveyard, scoreboards) | 9 prior leads ranked; **S8G-F20** classified coverage-blocked; 5 invalid-logic |
| 2 | Activate every local / no-key source | earnings cache (8,293 obs joined), no-key SEC EDGAR (75-ticker cache, 7,358 filing obs), **GDELT HTTP 200**, **FINRA reg-SHO HTTP 200**, Norgate macro/cross-asset |
| 3 | Generate **26 new combination candidates** + reuse C/B/F/H + macro G | **52 candidates total**, 49 testable, challenge fraction **0.347** |
| 4 | Validate: matched control, recent 2015-26, walk-forward, cost, tail, concentration, placebo, leakage, multiple-testing | full battery applied to all 49 testable |
| 5 | Decide + rank next options | **PROMISING_ALPHA_SIGNAL_FOUND**; 3+1 ranked next moves |

The program tested **combinations** (`external event × ticker sensitivity × sector/regime/vol/beta ×
confirmation`), not one universal factor — every new setup uses a **defined sensitivity cohort** and
**only columns that already exist** in the persisted grid (no invented features, no sign-flipping).

---

## The promising leads (5) — `promising_alpha_signals.csv`

| Signal | Family | n | recent n | lift vs ctrl | EV@25bps | recent lift | worst-decile | verdict |
|---|---|--:|--:|--:|--:|--:|--:|---|
| **S8E-011** | macro G (rates×short-dur) | **11,881** | 6,320 | +0.40% | +0.128% | +0.34% | −15.8% | full coverage; fails only tail/SPY-active → **local fixed-filter path** |
| **S8G-F20** | F (rates + earnings confirm) | 692 | 394 | +0.53% | +0.378% | +0.49% | −15.5% | coverage-limited (needs provider breadth) |
| **S8I-C-SECLEAD-20** *(new)* | C (earnings × sector leadership) | 933 | 411 | +0.32% | +0.269% | **+0.22%** | −17.3% | coverage-limited; positive recency — 67 events short of the gate |
| **S8I-C-HIGHBETA-20** *(new)* | C (earnings × high-beta) | **1,168** | 399 | +0.36% | +0.416% | −0.09% | −17.4% | clears the count gate but **fails 2015-26 recency** |
| **S8I-C-VOLSENS-20** *(new)* | C (earnings × vol-sensitive) | **1,152** | 380 | +0.53% | +0.503% | −0.03% | −17.2% | clears the count gate but **fails recency** |

**Three of the five promising leads are brand-new 8-I combinations** — earnings-catalyst × context
families that did not exist before this phase. None is CONFIRMED: S8E-011 fails the tail/portfolio
gate; F20 and SECLEAD are short on event count; HIGHBETA/VOLSENS clear the count but lose the
2015-2026 recency test. All have **positive EV after 25bps and positive matched-control lift** — the
brief's definition of a *promising* (not confirmed) alpha signal.

---

## Decision logic (why `PROMISING_ALPHA_SIGNAL_FOUND`, not provider-limited)

`research_director_decision.json`: the program separates **clean promising** (full coverage; the next
step is *local* — a fixed structural filter) from **provider/coverage-limited promising** (the only
path is more event data). **S8E-011 is clean promising** — 11,881 events, fails only the tail/active
gate that the fixed beta filter already addressed in 8-G/8-H (it flips *beats-SPY-active* True and
clears the −12% floor). So the honest call is *we have a promising signal with a local next step* —
`PROMISING_ALPHA_SIGNAL_FOUND` — with F20 / SECLEAD flagged provider-limited in the registry.

---

## Best current path + top next options (ranked by probability of success)

`top_next_options` (P = est. probability of making real progress):

1. **P=0.60 · LOCAL, no new data** — Apply the fixed beta-tail / volatility structural filter to the
   best promising macro × sensitivity and earnings-confirmed leads (S8E-011, F20) and **re-validate
   the filtered variant's stability** on the existing events. *Why:* the filter already flips
   beats-SPY-active True and clears the −12% tail; the open question is stability, answerable locally.
2. **P=0.50 · PROVIDER, highest ceiling** — Acquire a **broad multi-ticker earnings + analyst-revision
   feed** (FMP/Finnhub/Zacks/EODHD across S&P 500/1500), rebuild a chunked weekly sensitivity grid on
   `D:`, re-run F20 + the C/R families on the fixed gate. *Why:* the only lever that lifts F20's 692
   events past ≥1000 **and** raises every earnings/revision candidate's coverage at once.
3. **P=0.40 · NO-KEY / FREE** — Widen the no-key SEC EDGAR filings overlay + activate the **free FINRA
   biweekly short-interest bulk history**; test filings / short-interest × sensitivity on the gate.
4. *(P=0.30, provider)* — Acquire a timestamped news/sentiment history (GDELT bulk / NewsAPI) and test
   sentiment-shock × sensitivity + a news confirmation of the macro leads.

---

## Data-source activation (Cycle 2) — every lever exercised, honestly

| Source | Mode | Real PIT events? | Note |
|---|---|:--:|---|
| Local earnings-surprise cache | ACTIVATED | **yes** (8,293 obs) | 75 tickers — the binding breadth |
| Revision proxy | ACTIVATED (capped) | yes (labelled proxy) | never promoted to CONFIRMED |
| No-key SEC EDGAR | CACHE (75 tickers) | yes (7,358 obs) | warm cache from 8-H; widenable |
| GDELT news | **LIVE HTTP 200** | **no** | connector works; recent window only, no PIT history |
| FINRA reg-SHO | **LIVE HTTP 200** | **no** | daily file reachable no-key; single settlement window, not a deep history |
| Norgate macro / cross-asset | ACTIVATED | yes | rates/oil/usd/credit/vix/commodity × cohorts |
| Options IV | NEEDS_PROVIDER | no | no local data, no key |

Both no-key connectors (GDELT, FINRA) are now **proven reachable**, but neither yields a point-in-time
**history**, so neither was turned into events — reported as *connector-live / history-missing*,
**never faked**. 0 provider keys present (12 checked, names/presence only).

---

## End-of-task report (answers to the required list)

- **Exact files changed (all new, untracked):**
  `research/run_phase8i_autonomous_alpha_discovery_program.py`,
  `tests/test_phase8i_autonomous_alpha_discovery_program.py`, this doc, and
  `research/output/phase8i_autonomous_alpha_discovery_program/` (24 artifacts). No tracked file
  modified; large data stays on `D:`.
- **Cycles run:** 5 (rebuild → activate → generate → validate → decide).
- **Data sources activated:** local earnings cache, local revision proxy, no-key SEC EDGAR, Norgate
  macro/cross-asset + sector — all producing real PIT events/features.
- **No-key sources attempted:** SEC EDGAR (cache hit), **GDELT (HTTP 200)**, **FINRA reg-SHO
  (HTTP 200)** — connectors live; none gives a usable PIT history.
- **Provider keys detected:** **No** (0 of 12; detected by name/presence only, never printed).
- **Candidate signals generated:** **52** (26 new 8-I combinations + 6 earnings/revision/F20/filings
  from 8-G + macro family G + challenges/placebos).
- **Candidate signals tested:** **49** testable on the fixed gate (matched control + recent +
  walk-forward + cost + tail + concentration + placebo + leakage + multiple-testing).
- **Confirmed alpha signals:** **none.**
- **Promising alpha signals:** **5** — S8E-011, S8G-F20, **S8I-C-SECLEAD-20**, **S8I-C-HIGHBETA-20**,
  **S8I-C-VOLSENS-20** (3 are new this phase).
- **Provider-required signals:** **3** — S8G-A01 (news), S8G-D01 (options), S8G-E01 (short interest).
- **Best current solution:** apply the fixed beta-tail / volatility filter to **S8E-011** (and the
  earnings-confirmed F20 variant) and re-validate the filtered variant's stability — **local, no
  provider, no tuning**.
- **Top 3 next options (ranked):** (1) local fixed-filter re-validation **P=0.60**; (2) broad
  earnings+revision provider feed + chunked grid rebuild **P=0.50**; (3) no-key/free filings +
  FINRA short-interest overlay **P=0.40**.
- **What Claude will do next if allowed:** execute option 1 (build the filtered-variant stability
  campaign on the existing grid; thresholds fixed) and, in parallel, stage the option-2 provider
  acquisition + chunked S&P 1500 grid rebuild — phase **8-J** (`phase8j_next_plan.json`).
- **Tests pass:** 23/23 (8-I); full phase-8 suite green (see regression).
- **Commit appropriate:** **No** — per instruction (do not commit, do not push).
- **Next autonomous phase (8-J):** *Filtered-Variant Stability + Provider-Feed Expansion* — re-run the
  fixed structural filter on the promising leads and quantify stability; if a key is supplied, collect
  the broad earnings/revision feed and rebuild the chunked S&P 1500 grid so universe and events expand
  together; identical fixed gate.

## Safety contract honored
Local data first; Norgate for price/macro; on-disk caches reused; **no package install**; large data
on `D:` only (repo gets summaries/manifests/scoreboards/decision artifacts); **no secrets printed**
(keys by name/presence only); point-in-time joins only; **thresholds fixed a priori and not modified
after results**; **no factor-sign flipping** (combinations use only existing real columns); ≥30%
challenges/placebos (0.347); **external data never faked** (GDELT/FINRA left as connector-live /
history-missing; revision proxy labelled + capped below CONFIRMED; mock fixtures excluded); no weight
optimization; no regime activation; no ML fit; no Paper Trader / GCP / deployment / broker / orders /
automation; no live trading signals; failed experiments not hidden (27 rejected recorded);
**not committed, not pushed.**

## Artifacts (24)
`phase8i_autonomous_alpha_discovery_program.json`, `autonomous_research_memory.json`,
`hypothesis_backlog.csv`, `data_source_activation_log.csv`, `provider_key_inventory.csv`,
`local_no_key_source_results.csv`, `normalized_event_panel_manifest.csv`,
`candidate_signal_registry.csv`, `experiment_pre_registration.csv`, `alpha_signal_scoreboard.csv`,
`matched_control_report.csv`, `walk_forward_validation_report.csv`,
`recent_period_validation_report.csv`, `tail_risk_report.csv`, `concentration_report.csv`,
`placebo_leakage_report.csv`, `multiple_testing_report.csv`, `confirmed_alpha_signals.csv`,
`promising_alpha_signals.csv`, `provider_required_signals.csv`, `rejected_alpha_signals.csv`,
`model_candidate_registry_update.csv`, `research_director_decision.json`, `phase8j_next_plan.json`.
