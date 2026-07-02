# Phase 10-E — Paper-Only Review Harness for the Quarterly Quality Composite (v1)

## Purpose

Phase 10-D returned `QUARTERLY_QUALITY_COMPOSITE_CONFIRMED_READY_FOR_PAPER_REVIEW` — the **first**
signal in the entire 8-T → 10-D arc to clear the strict 63d gate (IC t = 3.07; quarterly net-25bps
+0.0065 / net-50bps +0.0035 at a realistic 0.60 quarterly turnover; OOS-positive; both cohorts +; both
subperiods +; sector-robust; sector-neutral edge intact). It is a **legitimate but modest, boundary-level**
pass and was dispositioned to **human PAPER review — not live trading**.

**Phase 10-E** does the one allowed next thing: it builds a **paper-only human review package** for the
quarterly **sector-neutral** quality composite. It reconstructs the **latest quarterly cross-section**,
ranks a long/short candidate book by the sector-neutral composite (the default review view), explains
every score from its two transparent legs, surfaces sector / liquidity / cohort exposure, audits the
unmapped "Unknown" sector bucket (the documented 10-D caveat), flags per-name risks, and lays out a
quarterly rebalance calendar plus a human approve/reject checklist.

It is **not** a new alpha search, **not** a provider search, **not** order creation, **not** automation,
**not** a deploy, and **not** (yet) a Paper Trader integration. It writes **only metadata CSV/JSON** to
its own `research/output` directory. It creates **no Paper Trader signals, no trade decisions, and no
orders**. Fully **offline** (no network, no key, no provider probe).

## Composite (imported verbatim from Phase 10-D — single source of truth)

| leg | orientation | transform | weight |
|---|---|---|---|
| `fcf_to_assets` | **+1** | within-month z of oriented level | 1.0 |
| `operating_accruals` | **−1** (Sloan) | within-month z of oriented (negated) level | 1.0 |

`comp_sn` = z(sector-neutral leg1) + z(sector-neutral leg2) — **the default review view**;
`comp_raw` = z(leg1) + z(leg2) — reference only. The composite is built by
`run_phase10d_quarterly_quality_composite_validation.build_composite` — **never re-defined here**. No
optimisation, no sign-flipping, no post-hoc selection.

## Method (packaging, not re-validation)

1. **Panel** — rebuild the Norgate survivorship-free earnings-event panel via `c10.build_panel`
   (offline; 545 tickers / 38,725 events).
2. **Attach + composite** — `c10.attach_signals` then `d10.build_composite` (coverage 38,404 both-legs
   events).
3. **Latest quarterly cross-section** — group events by calendar quarter, take one (latest) observation
   per ticker for the most recent quarter; scoreable = both legs present → the composite is computable.
4. **Book** — rank scoreable names by the **sector-neutral** composite; quintile long/short/hold
   **review** labels (top quintile LONG, bottom quintile SHORT, middle HOLD). These are *review* labels,
   never orders.
5. **Analytics** — score explainability (per-leg decomposition), sector exposure + Unknown-sector share,
   liquidity report (bottom-quartile flag), Unknown-sector remediation audit, per-name risk flags,
   estimated turnover vs the prior quarter, and a quarterly rebalance calendar.
6. **Decision** — packaging readiness only (see below). The alpha was already validated in 10-D.

## Decision rule (a-priori)

- `PAPER_REVIEW_BLOCKED_BY_MISSING_COMPOSITE_INPUTS` — panel empty or a normalized leg CSV missing.
- `PAPER_REVIEW_REJECTED_AFTER_POSITION_RECONSTRUCTION` — the latest quarter has < 10 scoreable names
  (no reviewable quintile book) or an empty long/short side.
- `PAPER_REVIEW_PACKAGE_READY_WITH_SECTOR_MAPPING_CAVEAT` — reconstructs cleanly but the book's
  Unknown-sector share ≥ 20% (the documented 10-D mapping gap).
- `PAPER_REVIEW_PACKAGE_READY` — reconstructs cleanly with acceptable sector mapping.
- `HARD_BLOCKER_REQUIRES_USER_ACTION` / `ERROR_WITH_REPRO_COMMAND`.

**Forbidden:** `LIVE_TRADING_READY`, `ORDER_READY`, `AUTOMATION_READY`,
`STRONG_ALPHA_FOUND_READY_FOR_REVIEW`, `MISSING_KEY`, `NO_DATA`, `NEEDS_PROVIDER`, `EMPTY_PAYLOAD`,
generic `ERROR`.

## Artifacts (14, in `research/output/phase10e_quarterly_quality_paper_review_harness/`)

`phase10e_quarterly_quality_paper_review_harness.json` · `paper_review_candidate_list.csv` ·
`paper_review_long_short_book.csv` · `paper_review_score_explainability.csv` ·
`paper_review_sector_exposure.csv` · `paper_review_liquidity_report.csv` ·
`paper_review_unknown_sector_audit.csv` · `quarterly_rebalance_calendar.csv` ·
`paper_review_turnover_estimate.csv` · `paper_review_risk_flags.csv` ·
`paper_review_human_checklist.csv` · `paper_review_safety_badges.csv` · `phase10f_next_plan.json` ·
`secret_safety_audit.csv`.

## Run / test (Windows PowerShell only)

```powershell
Set-Location C:\Users\binis\Stock_Prediction_app_push
python -m py_compile research/run_phase10e_quarterly_quality_paper_review_harness.py
python -m pytest tests/test_phase10e_quarterly_quality_paper_review_harness.py -q   # targeted; 14 passed
python research/run_phase10e_quarterly_quality_paper_review_harness.py              # fully offline; no key
```

## Constraints honored

Offline (no network/key/provider probe); only `fcf_to_assets` + `operating_accruals`; composite imported
from 10-D (no re-definition, no optimisation, no sign-flip); no FMP/AlphaVantage/Polygon/Finnhub; **no
Paper Trader writes; no GCP; NO orders; NO automation; NO live trading; NO broker; no deploy**; no package
install; no full regression (targeted tests only); keys never printed or written; output is metadata only.
**No commit. No push.**

---

## Status — live run 2026-06-29 (offline; exit 0)

**Final decision: `PAPER_REVIEW_PACKAGE_READY_WITH_SECTOR_MAPPING_CAVEAT`.**

The paper-review package reconstructs cleanly for the latest quarter (**2026Q2**), but **77.8% of the
reconstructed book sits in the unmapped "Unknown" sector bucket** — exactly the documented 10-D caveat,
so the package is dispositioned to review against the **sector-neutral** composite with sector mapping to
be improved before any sizing.

### Reconstructed book (2026Q2)

| field | value |
|---|---|
| latest quarter | 2026Q2 (prior 2026Q1) |
| universe names (reported this quarter) | 484 |
| scoreable names (both legs) | 483 |
| **long candidates** | **97** |
| **short candidates** | **97** |
| hold / not-in-book | 289 |
| Unknown-sector book share | **0.778** (long-side 0.794; 151 of 194 book names) |
| top *mapped* long sector | Industrials, 7.2% (no real concentration — `high_concentration` = False) |
| low-liquidity book names (bottom-quartile proxy) | 41 |
| estimated turnover vs prior quarter | long 0.67 / short 0.73 / **book 0.70** |

The top long names (EXPE, EA, JPM, …) are driven mostly by the **operating_accruals** leg (the dominant
leg from 10-D), confirmed in `paper_review_score_explainability.csv`; the top three carry an
`extreme_score` flag (within-quarter z > 2.5) so a reviewer can avoid over-sizing the tails.

### Phase 10-D provenance carried into the package

verdict `CONFIRMED`, ready_for_paper_review `True`, 63d IC t `3.074`, quarterly net-25bps `+0.00648`,
net-50bps `+0.00349`, quarterly turnover `0.599`.

### Honest caveats

1. **Unknown-sector heavy (77.8%).** The Norgate/EODHD sector mapping is sparse on this panel; the book
   is dominated (by *count*) by unmapped names. 10-D already showed the *edge* survives leave-Unknown-out
   and the sector-neutral composite holds — but a real paper book should trade the **sector-neutral**
   version and the mapping must be improved first. Every Unknown name is listed with a remediation step
   in `paper_review_unknown_sector_audit.csv`.
2. **Single-quarter turnover estimate (0.70) > 10-D's long-run 0.599.** The 0.70 here is the *one-period*
   Q1→Q2 name churn of the quintile extremes (and the reporting universe expanded this quarter); 10-D's
   0.599 is the *average* over the full validated history. The single-quarter figure is an estimate, not
   a re-measurement of the validated turnover.
3. **This is paper-*review*-ready, not a cleared strategy.** Nothing is sized, ordered, or automated. A
   human must approve/reject each name via `paper_review_human_checklist.csv`.

### Per-end-report fields

- **Final decision:** `PAPER_REVIEW_PACKAGE_READY_WITH_SECTOR_MAPPING_CAVEAT`.
- **Long candidates:** 97. **Short candidates:** 97.
- **Unknown-sector exposure:** 77.8% of the book (79.4% of the long side; 151 names).
- **Liquidity caveats:** 41 book names below the bottom-quartile liquidity proxy
  (threshold ≈ $1.26×10⁸ dollar-volume proxy); flagged per-name in `paper_review_liquidity_report.csv`.
- **Next quarterly rebalance date:** nominal **2026-10-01** (2026Q3 manual review; 2026Q2 is the current
  review). All dates are manual human-review dates — nothing executes automatically.
- **Written to Paper Trader?** **No.** **Orders / automation created?** **No.** No broker, no live
  trading, no deploy.
- **Exact next command:** `review research/output/phase10e_quarterly_quality_paper_review_harness/paper_review_long_short_book.csv`.
- **Targeted tests:** **14 passed**, 0 failed.
- **Commit recommendation:** **Do not commit** (standing rule). Runner, 14-test suite, doc, and 14
  metadata-only artifacts are on disk for review.

### Recommended Phase 10-F

Run the **human approve/reject gate** over `paper_review_long_short_book.csv`. On approval, build a
**paper-only position tracker** that marks the approved sector-neutral quarterly book to market each
quarter and compares realised vs expected net-of-25bps spread — still **no orders, no automation, no
broker, no live trading, no deploy**. In parallel, close the **"Unknown" sector-mapping gap** from
**owned** Norgate/EODHD metadata and re-rank. No new data purchase.
