# Phase 3-S — Earnings Coverage + Sentiment/Analyst Readiness & Prioritization Gate (v1)

Phase 3-S is a **research-only** readiness and prioritization gate.
It does not train a model, creates no production model candidate, computes no predictions /
scores / portfolio weights / order instructions, and writes no deployable model artifact.
Guardrails: it does not deploy. It does not run migrations. It does not write to production DB. It does not trade.
It restarts no prediction service, enables no model-v2 serving flag, reads nothing from the D:
drive, reads no provider API key value (only already-detected booleans), and calls no provider /
paid-vendor / Alpha Vantage / FRED API. No production edge is claimed; the universe is
current-as-of, so results remain survivorship-biased. Earnings, sentiment, and analyst-revision
data are never faked.

## Why this phase

Phase 3-R added a real, point-in-time local macro/inflation/rates regime feature family and
re-ran the walk-forward. The macro layer repaired the regime-fragile bad year (2021 and worst-year
IC improved) but lowered average IC too far to clear the robustness bar
(`MACRO_REGIME_WALKFORWARD_WEAK_NO_IMPROVEMENT`). The model needs an **orthogonal, event-driven**
signal family next: more **earnings coverage** and/or sentiment / analyst-revision data. Phase 3-S
measures what we have, plans the next safe step, and ranks the options — without spending provider
quota or fabricating anything.

## What it does

1. **Confirms Phase 3-R** — `phase == "3-R"`, recommendation is
   `MACRO_REGIME_WALKFORWARD_WEAK_NO_IMPROVEMENT` or `..._IMPROVES_ROBUSTNESS`,
   `recommended_next_phase.phase == "3-S"`, and all production / faking / provider flags are false.

2. **Earnings coverage status** — reads the Phase 3-M `collection_progress.json` and the local raw
   earnings cache directory listing (one provider JSON per ticker; no content parsed, no network).
   Reports universe / cached / missing ticker counts, the 75-ticker signal-gate minimum, whether
   the gate is allowed now, estimated runs to 75 and to 128 at ~20 tickers/run, the last provider
   limit timestamp (if any), and the next safe collection action.

3. **Next collection plan** — one row per action. The gate itself never calls the provider; it
   only plans. If the same-day provider limit guard is active the next action is **wait for
   reset**; if the guard is inactive and a provider api key is detected the next action is **run
   the resumable collector in a separate controlled step**.

4. **Sentiment / analyst inventory** — scans local repo CSVs only (`research/input`,
   `research/output`, `data/`, top-level CSVs). Detection is **filename-anchored** (like the
   Phase 3-R macro scan) so a derived earnings column such as `estimate_revision_proxy` is never
   misclassified as a real analyst-revision dataset. A candidate is only **usable** if it carries a
   ticker column, a real sentiment/revision value column, and a **point-in-time** publication /
   event date.

5. **Readiness** — when no usable local sentiment or analyst-revision data exists (the current
   state), the families `news_sentiment`, `analyst_revision`, `analyst_rating`, and
   `target_price_revision` are marked **BLOCKED** and the **exact, non-faked** data requirements are
   written (ticker; event/publication date; sentiment_score or analyst_revision_value; source;
   point-in-time availability date). Recommended local sources are **described only** — no API is
   called and nothing is browsed.

6. **Priority + roadmap** — ranks earnings-to-75, analyst-revision acquisition, news-sentiment
   acquisition, earnings-to-128, global cross-asset ETF proxies, risk simulation, and
   non-production candidate packaging; and writes the ordered roadmap 3-T → 3-Y.

## This run (current local state)

| Metric | Value |
| --- | --- |
| Universe tickers | 128 |
| Cached earnings tickers | 25 (local raw cache listing; progress JSON agrees) |
| Missing earnings tickers | 103 |
| Signal-gate minimum | 75 |
| Signal gate allowed now | No |
| Est. runs to 75 @ ~20/run | 3 |
| Est. runs to 128 @ ~20/run | 6 |
| Same-day provider limit guard | Inactive |
| Provider api key detected | Yes (boolean only; value never read) |
| Next safe collection action | `RUN_RESUMABLE_COLLECTOR_IN_SEPARATE_CONTROLLED_STEP` |
| Usable local sentiment files | 0 (`news_sentiment` blocked) |
| Usable local analyst-revision files | 0 (`analyst_revision`, `analyst_rating`, `target_price_revision` blocked) |
| Top priority | `continue_earnings_coverage_to_75_tickers` |

**Decision rule:** cached earnings tickers (25) < 75, so the recommendation is
`EVENT_SIGNAL_READINESS_CONTINUE_EARNINGS_COLLECTION`. (If coverage met the minimum but no
sentiment/analyst data existed → `..._ACQUIRE_SENTIMENT_OR_ANALYST_DATA`; if coverage met the
minimum and usable event data existed → `..._READY_FOR_GLOBAL_ASSET_EXPANSION`; missing required
Phase 3-R/3-M inputs → `..._BLOCKED_INPUTS`.)

**Recommendation:** `EVENT_SIGNAL_READINESS_CONTINUE_EARNINGS_COLLECTION`
**Recommended next phase:** `3-T — Continue Earnings Coverage to 75+ Tickers` (run the resumable,
cache-first, network-budgeted collector across days until cached ≥ 75, then re-run the
earnings/macro/technical model; no model trained yet).

## Outputs

- `research/output/phase3s_event_signal_readiness_gate.json` — full result + safety flags.
- `phase3s_event_signal_readiness_gate/earnings_coverage_status.csv`
- `phase3s_event_signal_readiness_gate/earnings_next_collection_plan.csv`
- `phase3s_event_signal_readiness_gate/sentiment_data_inventory.csv`
- `phase3s_event_signal_readiness_gate/analyst_revision_data_inventory.csv`
- `phase3s_event_signal_readiness_gate/event_signal_priority_table.csv`
- `phase3s_event_signal_readiness_gate/model_improvement_roadmap.csv`
- `phase3s_event_signal_readiness_gate/readiness_decision_table.csv`

## Model-improvement roadmap

| Phase | Title |
| --- | --- |
| 3-T | Continue Earnings Coverage to 75+ Tickers (re-run earnings/macro/technical model) |
| 3-U | Add Local Sentiment / Analyst Revision Data (if acquired) |
| 3-V | Global Cross-Asset ETF Proxy Universe + Cross-Asset Regime Features |
| 3-W | Turnover-Aware Risk Simulation and Portfolio Construction |
| 3-X | Non-Production Candidate Packaging |
| 3-Y | Paper Trader Preview Integration Only |

## Guarantees

All data is real and **never faked**: when sentiment / analyst-revision data is absent the gate
records blocked families and exact requirements instead of inventing values. Every output file is
Git-safe (well under 50 MB). This phase is **research-only** and claims no production edge.
