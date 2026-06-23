# Phase 7-K — Survivorship-Aware Data Foundation Feasibility Pilot (v1)

**Status:** research / feasibility pilot only.
**Recommendation:** `FREE_DATA_NOT_SUFFICIENT`.
**Can we build a survivorship-aware dataset good enough to retest the signal on free data? → NO.**

**Not** a modelling phase, factor-engineering phase, portfolio construction, trading system,
order/execution automation, factor-weight optimization, factor-sign flipping, regime-throttle
activation, or Paper Trader / GCP / deployment / broker work. The only live data is free public
sources — Wikipedia (S&P 500 list), SEC EDGAR submissions JSON (stdlib `urllib`), and an attempted
Stooq CSV probe — plus the already-present `yfinance`. No paid API, no API key, no package
installed. Large raw payloads live only under
`D:\Stock_Prediction_app_data\phase7k_survivorship_data_foundation\`; the repo received only
committed-safe summaries. Nothing committed or pushed.

Governed by `docs/project_charter_sp500_multifactor_ranking_v1.md`.

---

## Why this phase exists

Phase 7-J showed the multifactor composite did **not** survive the broad-universe retest — it
underperformed the simple price-only momentum baseline (composite IC +0.0113 vs price-only
+0.0169). The honest next step is **not** another local factor experiment. The binding question
is whether the *data foundation itself* can be made honest: a genuinely **survivorship-aware**,
**point-in-time** universe (delisted names included, membership effective-dated, sectors
as-of-date). This phase is a hard go/no-go feasibility test on whether that foundation can be
built from **free / locally-accessible** sources.

> **One question:** CAN WE BUILD A SURVIVORSHIP-AWARE DATASET GOOD ENOUGH TO RETEST THE SIGNAL?
> **Answer (tested, not assumed): NO** on free data. Index *membership* and *CIK identity* are
> free-feasible, but the two survivorship-critical pillars — **delisted-name prices** and
> **point-in-time sectors** — are not. By the strict rule this is `FREE_DATA_NOT_SUFFICIENT`.

The pilot was **bounded and env-gated**: it runs only when `PHASE7K_LIVE_APPROVED=YES`, capped at
≤ 50 web/source checks and ≤ 20 delisted price probes. This run used **7 web checks, 20 price
probes, 21 network requests** — comfortably inside the caps. Default mode is a fully offline
dry-run.

---

## The four pillars (`data_foundation_decision_matrix.csv`)

| Pillar | Free source | Feasible? | Evidence |
|---|---|:--:|---|
| **Historical membership** | Wikipedia constituents + selected-changes table | **YES** | 399 dated add/remove events over 50y (1976–2026); 367 removed names, 171 genuine delistings |
| **Delisted-name prices** | yfinance + Stooq | **NO** | only **1 / 20** genuine delistings had usable free prices (frac 0.05); Stooq blocked (below) |
| **Point-in-time sectors** | (none — current-only snapshots) | **NO** | no free source is effective-dated; sector-neutralization cannot be made point-in-time |
| **CIK identity continuity** | SEC EDGAR submissions (CIK + `formerNames`) | **YES** | 6/6 current survivors resolved with full rename history; but 0/9 *delisted* names are in the current SEC directory |

Two pillars pass, two fail — and the two that fail are exactly the ones that *cause and cure*
survivorship bias. Membership + identity tell you *which* names left the index and *who* they
were; without delisted prices and point-in-time sectors you still cannot actually **price** or
**neutralize** the dropped names in a backtest.

---

## Pillar 1 — Historical membership: FEASIBLE

The Wikipedia "Selected changes to the S&P 500" table parsed cleanly with `bs4` (stdlib
`html.parser`; no `lxml`/`html5lib` needed) into **399 dated events spanning 1976–2026**. Each row
yields the date, added ticker/security, removed ticker/security, and a reason string. The reason
column reliably distinguishes the two removal types:

- **Genuine delisting** (`acquired` / `merger` / `taken private` / `bankrupt`) — 171 of 367
  removed names. *These* are the survivorship-critical names.
- **Index removal** (`market capitalization change`) — the company keeps trading; its prices are
  trivially available and tell you **nothing** about survivorship coverage.

A 2-year (here 50-year) membership timeline is reconstructable for free. `membership_timeline_pilot.csv`
holds the full parsed event log.

## Pillar 2 — Delisted-name prices: NOT FEASIBLE (the decisive blocker)

This is the heart of the phase, and it required care to test honestly. A naive probe of the 20
most-recently-removed tickers returns ~16/20 "usable" — but that number is an **artifact**: most
recent removals are *index removals that still trade today* (POOL, CPB, MTCH, MOH … all return
full history to 2026-06-23). They are not delistings.

The corrected probe targets the **20 most recent genuine delistings** (acquisitions) and measures
free price coverage on those. Result: **1 of 20 usable** (`delisted_price_probe.csv`):

- Only **CTRA** (acquired most recently) still had a usable yfinance tail (ending 2026-05-07).
- Every other acquired name returned **nothing** from yfinance — including well-known tickers with
  years of history that ended at acquisition: **ATVI, TWTR, PXD, MRO, ABMD, CERN, CTXS, NLSN, K,
  IPG, HOLX, DAY, WBA, HES, ANSS, JNPR, DFS, CTLT, DRE**. Yahoo **purges** delisted symbols, and
  the gap widens with time since delisting.
- **Stooq was BLOCKED**, not empty: every one of the 20 requests returned a JavaScript anti-bot
  challenge page (`This site requires JavaScript to verify your browser`), confirmed by a direct
  probe of `AAPL`. Stooq's delisted coverage is therefore **UNVERIFIED** from this environment —
  recorded as `stooq_status=blocked_antibot`, **not** counted as a genuine negative. That a free
  stdlib route is gated behind bot-protection is itself evidence that reliable *programmatic* free
  delisted-price collection is not dependable.

So free delisted-price coverage of genuine delistings is **~5% (and unverified beyond yfinance)** —
far below any usable threshold (`some` requires ≥ 50% of ≥ 3 genuine delistings). The pillar fails.

## Pillar 3 — Point-in-time sectors: NOT FEASIBLE

`sector_pit_feasibility.csv`: every free sector source is a **current snapshot** — Wikipedia's GICS
sector column, SEC `sicDescription`, and the local `phase2k_p_sector_map_current.csv` (already
flagged `point_in_time=false` in Phase 7-G). None is effective-dated; none logs reclassifications.
A point-in-time, sector-neutral re-grade cannot be done on free data without fabricating history.

## Pillar 4 — CIK identity continuity: FEASIBLE (with a delisted-name gap)

`identity_mapping_feasibility.csv`: SEC submissions give a stable CIK anchor and full `formerNames`
history — **6/6** current survivors resolved, and renames trace cleanly (MTCH → 6 former names back
through the IAC lineage; AAPL → 3; WBD → 2 Discovery names; META → Facebook). Identity is solidly
free-feasible **for survivors**. The caveat: **0 of 9** probed delisted names appear in the current
`company_tickers.json` directory — acquired issuers stop filing and drop out — so identity recovery
for delisted names needs the full historical CIK lookup, not the current ticker directory.

---

## Free-source feasibility matrix (`free_source_feasibility_matrix.csv`)

| Pillar | Source | Dependency | Result |
|---|---|---|---|
| historical_membership | Wikipedia constituents + changes | bs4 (present) | **FEASIBLE** |
| delisted_prices | yfinance (Yahoo) | yfinance (present) | **PARTIAL** (1/20 genuine delistings — inadequate) |
| delisted_prices | Stooq CSV | urllib (stdlib) | **BLOCKED** (anti-bot JS challenge, 20/20; unverified) |
| pit_sectors | Wikipedia/SEC/local (current-only) | n/a | **INFEASIBLE** (no effective-dated source) |
| identity_continuity | SEC EDGAR submissions (CIK + formerNames) | urllib (stdlib) | **FEASIBLE** |

---

## Why `FREE_DATA_NOT_SUFFICIENT` (strict rule, borderline not rounded up)

Decision rule (`derive_recommendation`):

* not tested (dry-run) → `FREE_DATA_NOT_SUFFICIENT` (provisional; offline cannot *demonstrate*
  sufficiency — flagged `provisional_pending_live_pilot`)
* membership **and** delisted (strong) **and** PIT sectors all feasible → `FEASIBLE`
* membership **and** some delisted, PIT sectors not feasible → `PARTIAL`
* free providers return **zero** usable genuine-delisting series → `NEEDS_PAID_OR_EXTERNAL_DATA`
* membership infeasible, **or** delisted coverage sparse-but-not-zero → **`FREE_DATA_NOT_SUFFICIENT`** ← here

Membership is feasible, so this is not `NEEDS_PAID` on the membership axis; delisted coverage is
non-zero (CTRA), so it is not `NEEDS_PAID` on the strict zero test either — but at **5%** it is far
from "some" (≥ 50%), and point-in-time sectors are unavailable. The honest verdict is
`FREE_DATA_NOT_SUFFICIENT`: free sources cannot support a reliable survivorship-aware universe. This
sits **one step from `NEEDS_PAID_OR_EXTERNAL_DATA`** — for genuine delisted-name prices the only
reliable route is paid/external survivorship-free data (CRSP / Norgate / Sharadar). The result was
**not** rounded into a pass.

---

## Gate matrix (`phase7k...json` → `gate_matrix_summary`)

**19 PASS / 2 FAIL / 0 WARN — 0 safety failures.**

* **FAIL (results, honest):** `delisted_price_coverage` (genuine-delisting coverage inadequate),
  `pit_sectors_available` (no free effective-dated sector history).
* **PASS:** local inventory, feasibility matrix, bs4/yfinance available, membership
  reconstructable, identity continuity, and every safety gate — default dry-run, no network unless
  approved, bounded caps enforced (7/50 web, 20/20 price), no paid API, no packages installed, no
  model/factor/portfolio/trading logic, repo summaries only, large data on D: only, no Paper
  Trader/GCP/broker, not committed, not pushed.

---

## Recommended next phase (`phase7l_next_plan.json`)

The signal did not survive (7-J) **and** the survivorship-aware data foundation cannot be built on
free data (7-K). Do **not** resume local factor polishing. Two honest paths:

1. **Pivot off multifactor alpha-hunting** to a simpler **momentum / risk** strategy on the current
   survivor universe, sized conservatively, with the survivorship caveat documented explicitly —
   accepting that the cross-section is survivor-biased and that no free fix exists.
2. **Acquire external survivorship-free data** (CRSP / Norgate / Sharadar) *only if* a reliable
   multifactor edge is genuinely required — then build delisted prices + point-in-time membership +
   effective-dated sectors and a sector-neutral re-grade through the unmodified 7-B harness. This is
   out of scope for "free sources only."

Membership (Wikipedia) and identity (SEC `formerNames`) are reusable free building blocks for
either path; the blockers are delisted prices and point-in-time sectors.

---

## Where data lives

* **Repo (committed-safe summaries only):** `research/output/phase7k_survivorship_data_foundation_feasibility/`
  — the nine artifacts below.
* **Large raw payloads (D: only):** `D:\Stock_Prediction_app_data\phase7k_survivorship_data_foundation\`
  — cached Wikipedia HTML and per-CIK SEC submissions JSON. A **new** directory; existing
  `phase2k_g`, `phase7i_broad_universe`, and `phase7j_*` data are untouched.

## Artifacts

`phase7k_survivorship_data_foundation_feasibility.json`, `local_survivorship_source_inventory.csv`,
`free_source_feasibility_matrix.csv`, `membership_timeline_pilot.csv`, `delisted_price_probe.csv`,
`sector_pit_feasibility.csv`, `identity_mapping_feasibility.csv`,
`data_foundation_decision_matrix.csv`, `phase7l_next_plan.json`.

## Tests

`tests/test_phase7k_survivorship_data_foundation_feasibility.py` — 41 tests (recommendation
vocabulary, the five strict decision branches incl. the dry-run provisional and the exact "sparse
≠ pass" case, reason classification, Wikipedia table parsing + genuine-delistings-first ordering,
the monkeypatched delisted probe incl. the cap and the zero/some/strong cases, Stooq anti-bot
detection, host allow-list, decision/feasibility/gate matrices, the identity prune keeping
`formerNames`, next-plan scope, and a guarded offline end-to-end verifying all nine artifacts and
the safety contract). All pass.

## Safety contract

Free sources only (Wikipedia + SEC EDGAR + Stooq + yfinance) · no paid API · no packages installed
· no model/factor/portfolio/trading logic · default dry-run, network only under the
`PHASE7K_LIVE_APPROVED=YES` gate · bounded caps (≤ 50 web checks, ≤ 20 price probes) · large data on
D: only · repo gets summaries only · existing D: data never overwritten · no Paper Trader / GCP /
broker / deployment · not committed · not pushed.
