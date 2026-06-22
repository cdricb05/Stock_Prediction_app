# ADR — Phase 7A: Project Charter Integration and Multi-Factor Ranking Architecture Reset (v1)

**Track A (quant brain) architecture decision record. Planning only.**
No model built or trained, no model deployed, no database touched, no live provider
call, no Alpha Vantage, no paid API, no strategy / shadow test, no orders / broker /
automation, no Paper Trader / GCP / deploy work. No commit, no push.

- **Status:** Accepted (proposed by the quant engineer, pending owner endorsement of the roadmap)
- **Date:** 2026-06-22
- **Phase:** 7A
- **Supersedes (in intent):** the single-signal prediction-hunting thread (Phase 5-G earnings rescue, strategy-filter loops, Phase 6-A/6-B single-augmentation experiments)
- **Constitution:** [docs/project_charter_sp500_multifactor_ranking_v1.md](project_charter_sp500_multifactor_ranking_v1.md)

---

## Context

The project has spent many phases chasing a single, confident, single-name directional
signal: a 5-20 day price-only champion (Phase 5-C) wrapped in earnings-surprise / PEAD
event composites (Phase 5-G/5-G2) and cross-asset macro context (Phase 6-A). The honest
results were consistent and negative:

- Phase 5-G2: `NO_INCREMENTAL_EVENT_EDGE` — earnings wrappers add nothing once coverage broadens.
- Phase 6-A: the local partial macro pack *degraded* selection (`NEEDS_CONTROLLED_CROSS_ASSET_DATA_COLLECTION`).
- Phase 6-B: cross-asset proxy collection is built but empty (`NEEDS_ADDITIONAL_PROXY_COLLECTION`, 0/46 proxies, no key in shell).

In parallel, the project owner authored a governing charter
([project_charter_sp500_multifactor_ranking_v1.md](project_charter_sp500_multifactor_ranking_v1.md))
and pointed the root `CLAUDE.md` at it as the project constitution. The charter reframes
the entire effort:

> **This is a ranking and risk-monitoring system, not a prediction oracle.** Single-name
> directional prediction with confidence is explicitly out of scope and treated as a red flag.

That reframing is fundamentally incompatible with the direction the recent phases were
heading. Phase 7A exists to make the charter the actual architecture of record and to lay
out the implementation roadmap that follows from it.

## Decision

Adopt the charter as the governing architecture and reset the project to its two-system design:

1. **System 1 — Ranking engine (the engine).** A cross-sectional multi-factor scoring system
   that ranks all ~500 S&P names by documented factor premia (value, momentum, quality,
   low-vol, growth, sentiment/flow). Decides *which* names are relatively attractive. It does
   **not** predict direction or attach confidence to a single name.

2. **System 2 — Regime overlay (the throttle).** A macro-indicator regime classifier
   (risk-on / neutral / risk-off) that modulates *how much* risk to take and *which* factor
   buckets to favor. It is a posture throttle on top of System 1, **never** a standalone oracle.

The reset is bound by the charter's non-negotiable disciplines:

- **The validation harness is the measuring instrument and is built FIRST** (charter Section 6 /
  Phase 0). No factor or model is trusted until the instrument that measures it exists and is
  itself validated.
- **Equal weight first.** Factors are combined equal-weighted; weight optimization is deferred
  until the validation framework is trusted *and* the owner endorses the weighting philosophy
  (charter Section 5).
- **Cross-sectional normalization.** Z-score each factor, winsorize at +/-3, sector-neutralize
  where appropriate (charter Section 5).
- **Risk is a property of the whole book.** Exposures (market beta, rate/duration, sector/factor,
  idiosyncratic) are measured at book level before any hedging is contemplated (charter Sections 2, 7).
- **Judgment calls are surfaced, never buried.** Factor selection, weighting, and backtest
  interpretation are presented with options and visible reasoning for the owner to endorse or
  override (charter Section 1).

### Roadmap (System 1 first, then System 2)

| Phase | Title | Charter mapping | Status |
|---|---|---|---|
| **7B** | Validation Harness Foundation | Phase 0 (harness, build FIRST) + Phase 1 (point-in-time data) | **NEXT** |
| **7C** | Multi-Factor Ranking Engine | Phase 2 (single-factor) + Phase 3 (composite, equal weight) | Planned |
| **7D** | Risk Decomposition | Phase 4.5 (consolidated risk view) | Planned |
| **7E** | Regime Overlay | Phase 5 (System 2) | Planned |

- **7B — Validation Harness Foundation.** Point-in-time data layer (yfinance, FRED, SEC EDGAR)
  plus a purged walk-forward harness reporting the full metrics suite and the three
  anti-self-deception safeguards. Reuse-first: extend the audited Phase 5-C walk-forward
  machinery rather than reinvent leakage-prone paths.
- **7C — Multi-Factor Ranking Engine.** Compute the six factor buckets, normalize
  cross-sectionally, combine equal-weight into a composite rank of ~500 names. System 1 only.
- **7D — Risk Decomposition.** Factor exposure matrix → net beta / net duration / sector
  concentration / factor tilts across the whole book. A CFO-readable consolidated risk view,
  valuable on its own with zero hedging. Measurement only.
- **7E — Regime Overlay.** Macro-indicator regime classifier that tilts factor-bucket weights
  and risk posture. Gated on System 1 being trusted over months *and* on the cross-asset macro
  data being collected (ties back to Phase 6-B/6-C — currently data-BLOCKED).

The detailed roadmap, factor catalogue, and validation requirements are recorded as:

- `research/output/phase7a_ranking_engine_reset/phase7a_ranking_engine_reset.json`
- `research/output/phase7a_ranking_engine_reset/implementation_roadmap.csv`
- `research/output/phase7a_ranking_engine_reset/factor_inventory.csv`
- `research/output/phase7a_ranking_engine_reset/validation_framework_requirements.csv`

## Consequences

**What we stop doing (immediately):**

- Single-signal prediction hunting and prediction-oracle framing.
- Earnings-surprise-only research and any Phase 5-G rescue.
- Strategy-filter loops.
- Early factor-weight optimization.
- Paper Trader / GCP / deploy / broker / order / automation work.
- Live provider calls (in this phase).

**What we build next:**

Phase 7B — the validation harness foundation. The measuring instrument before any factor or
model, so that every later claim resolves to out-of-sample, cost-adjusted, regime-decomposed
evidence against an equal-weight benchmark.

**Trade-offs accepted:**

- This is a slower, less glamorous path than a single headline signal — by design. The charter's
  expected outcome is *modest, durable outperformance through consistency*, not spectacular returns.
- System 2 (regime overlay, 7E) is deliberately deferred behind a trusted System 1 and behind
  cross-asset data collection. You cannot hedge or throttle a book whose net exposures you cannot
  yet measure.
- Sentiment/flow and most macro/regime inputs are not yet collected under the free-data-first
  constraint; the roadmap marks them as later-phase data dependencies rather than blockers to 7B.

**Open judgment calls reserved for the owner (not decided here):**

- The factor weighting philosophy (after equal-weight is validated).
- Where on the robustness-vs-return frontier the book should sit (charter Section 10 — the one
  irreducible human judgment).
- Risk-appetite levers (net exposure, position count, vol target — charter Section 8).

## Charter & CLAUDE.md compliance

- The charter is present in-repo and is treated as the constitution. No charter content was
  modified or moved.
- Root `CLAUDE.md` already references the charter; it was not changed this phase.
- No charter contradiction was found in this reset, so the recommendation is
  `READY_FOR_PHASE7B_VALIDATION_HARNESS` (not `NEEDS_CHARTER_REVIEW`).

## Recommendation

**`READY_FOR_PHASE7B_VALIDATION_HARNESS`** — proceed to build the validation harness foundation.

## Safety contract

Planning only · zero network / provider call · no Alpha Vantage / paid API · no model trained
or deployed · no database / migration touched · no strategy / shadow test · no orders / broker /
automation · no Paper Trader / GCP / deploy · committed-safe text artifacts only · no commit ·
no push.
