# Phase 10-F-A — Owned Metadata Sector Mapping Repair and Paper-Review Rerank (v1)

## Purpose

Phase 10-E packaged the 10-D quarterly **sector-neutral** quality composite into a paper-review book and
returned `PAPER_REVIEW_PACKAGE_READY_WITH_SECTOR_MAPPING_CAVEAT`: the 2026Q2 book reconstructs cleanly
(97 long / 97 short) but **77.8 % of it sat in the unmapped "Unknown" sector bucket**. The 10-D composite
is still valid, but the book was too Unknown-heavy to approve by hand — and, more importantly, the
"sector-neutral" leg is only as good as the sector labels. With 374/483 names Unknown, the within-month ×
sector de-mean collapses into **one giant pseudo-sector**, so `comp_sn` was not a genuine sector
neutralisation at all.

**Phase 10-F-A** does the one allowed next thing: it **repairs the sector labels using only owned/local
metadata**, **rebuilds the sector-neutral composite** over the repaired sectors, and **re-ranks** the
2026Q2 paper-review book. It then reports before-vs-after (Unknown share, top-sector concentration, rank
movement, names entering/leaving each side) and decides whether the repaired book is ready for a human
approve/reject review.

It is **not** a new alpha search, **not** a provider search, **not** order creation, **not** automation,
**not** a deploy, and **not** (yet) a Paper Trader integration. It writes **only metadata CSV/JSON** to
its own `research/output` directory. It creates **no Paper Trader signals, no trade decisions, and no
orders**. Fully **offline** (no network, no key, no provider probe).

## Owned sector sources (attempted in the brief's priority order; no new acquisition)

| # | Source | On disk? | Taxonomy | Used as | Confidence |
|---|---|---|---|---|---|
| 1 | Norgate symbol/sector metadata | **No** (no local Norgate sector dump; needs the Norgate SDK/DB) | — | recorded UNAVAILABLE, never fabricated | — |
| 2 | EODHD raw fundamentals `General.GicSector` / `GicIndustry` | **Yes** (`research/data/eodhd/raw/fundamentals/<ticker>.json`, 547 files) | **GICS — the same 11 buckets the curated map uses** | **primary repair** | **HIGH** |
| 3 | EODHD raw fundamentals `General.Sector` (Morningstar) | Yes (same files) | Morningstar → GICS via a documented 1:1 crosswalk | fallback when `GicSector` blank | MEDIUM |
| 4 | EODHD normalized fundamentals metadata | Yes | — | N/A — time-series feature families only, no sector | — |
| 5 | Prior-phase curated sector map (`phase2k_p_sector_map_current.csv`, 128 names) | Yes | GICS | already the panel's source; Unknown names are absent from it; checked + logged | HIGH if hit |
| 6 | Cached EODHD company profile | Yes | GICS | the General block **is** the cached profile (same data as #2) | — |

The on-disk **FMP** `company_profile` cache is a **hard-banned** source for this phase and is **never
read**. Repaired sector labels are **current-as-of** company classifications, **not historical
point-in-time** — used **only** as a static neutralisation *grouping* (sector is not a return feature
here), exactly as the curated map already is (`point_in_time=false`). This is documented, not hidden.

## Why `General.GicSector` is the right owned source

`General.GicSector` returns the **identical 11-bucket GICS taxonomy** the curated panel map uses
(`Information Technology`, `Consumer Discretionary`, `Health Care`, …). So a repaired label is **directly
comparable** to a curated label and the rebuilt sector-neutral composite stays taxonomically coherent —
**no crosswalk and no relabelling** of the already-mapped names. Coverage on disk: **540/547** files carry
`GicSector`; the **7** without it carry Morningstar `Sector`, mapped to GICS by the documented 1:1
crosswalk (MEDIUM).

## Method (repair → rebuild → rerank; the composite is imported verbatim from 10-D)

1. **Read the 10-E "before" book** (`paper_review_candidate_list.csv`) — ranks, review labels, sectors.
2. **Rebuild the Norgate panel** via `c10.build_panel` (offline; 545 tickers / 38,725 events).
3. **Identify every Unknown-sector ticker** on the panel.
4. **Repair** each from owned sources in priority order; record `ticker · original_sector ·
   repaired_sector · repaired_industry · source_family · source_file · source_field · confidence ·
   reason` for every repair, and **keep Unknown** (with a reason) anything not repairable. **Never
   fabricates** — only labels that land in the canonical 11-bucket GICS taxonomy are accepted.
5. **Apply** repairs to the panel (overwrite **only** Unknown rows; mapped names untouched), then
   **re-run** `c10.attach_signals` + `d10.build_composite` so the **sector-neutral legs are recomputed**
   over the repaired sectors. `comp_raw` is sector-independent and is unchanged (a sanity check).
6. **Rerank** the latest quarterly cross-section into the long/short/hold book (reusing the 10-E
   `build_book` / `sector_exposure` / `liquidity_report` / `risk_flags` logic verbatim).
7. **Compare** before vs after: Unknown share, top-sector concentration, long/short overlap, rank
   movement, names entering/exiting each side.
8. **Decide** packaging readiness (below). The alpha itself was validated in 10-D and is not re-tested.

## Decision rule (a-priori)

- `PAPER_REVIEW_REJECTED_AFTER_SECTOR_REPAIR` — the reranked book degenerates (too few scoreable names
  or an empty side).
- `SECTOR_MAPPING_NOT_REPAIRABLE_WITH_OWNED_DATA` — no Unknown name could be repaired from owned data.
- `SECTOR_MAPPING_PARTIALLY_REPAIRED_REVIEW_WITH_CAVEAT` — repaired, but a residual Unknown share ≥ 20 %
  remains **or** the repaired labels surface a single-sector long-book concentration ≥ 60 %.
- `SECTOR_MAPPING_REPAIRED_READY_FOR_HUMAN_REVIEW` — Unknown share cut below 20 % with no new sector
  concentration breach.
- `HARD_BLOCKER_REQUIRES_USER_ACTION` / `ERROR_WITH_REPRO_COMMAND`.

**Forbidden:** `LIVE_TRADING_READY`, `ORDER_READY`, `AUTOMATION_READY`,
`STRONG_ALPHA_FOUND_READY_FOR_REVIEW`, `MISSING_KEY`, `NO_DATA`, `NEEDS_PROVIDER`, `EMPTY_PAYLOAD`,
generic `ERROR`.

## Artifacts (15, in `research/output/phase10f_owned_sector_mapping_repair/`)

`phase10f_owned_sector_mapping_repair.json` · `unknown_sector_repair_attempts.csv` ·
`repaired_sector_mapping.csv` · `unrepaired_unknown_sector_names.csv` ·
`sector_mapping_source_audit.csv` · `before_after_sector_exposure.csv` ·
`before_after_unknown_sector_exposure.csv` · `reranked_paper_review_candidate_list.csv` ·
`reranked_paper_review_long_short_book.csv` · `long_short_book_change_report.csv` ·
`rank_movement_report.csv` · `sector_neutral_score_rebuild_audit.csv` · `repaired_book_risk_flags.csv` ·
`phase10g_next_plan.json` · `secret_safety_audit.csv`.

## Run / test (Windows PowerShell only)

```powershell
Set-Location C:\Users\binis\Stock_Prediction_app_push
python -m py_compile research/run_phase10f_owned_sector_mapping_repair.py
python -m pytest tests/test_phase10f_owned_sector_mapping_repair.py -q   # targeted; 16 passed
python research/run_phase10f_owned_sector_mapping_repair.py              # fully offline; no key
```

## Constraints honored

Offline (no network/key/provider probe); **owned/local metadata only**; no new purchase; no
FMP/AlphaVantage/Polygon/Finnhub/Norgate-API; composite imported from 10-D (sector-neutral legs
recomputed; no re-definition, no optimisation, no sign-flip); **no fabricated sectors**; **no Paper
Trader writes; no GCP; NO orders; NO automation; NO live trading; NO broker; no deploy**; no package
install; no full regression (targeted tests only); keys never printed or written; output is metadata
only. **No commit. No push.**

---

## Status — live run 2026-06-30 (offline; exit 0)

**Final decision: `SECTOR_MAPPING_REPAIRED_READY_FOR_HUMAN_REVIEW`.**

Owned-metadata repair eliminated the Unknown-sector problem on the 2026Q2 book and the reranked
sector-neutral quarterly composite book is ready for a human approve/reject review.

### Repair

| field | value |
|---|---|
| panel Unknown tickers | 418 |
| **repaired from owned metadata** | **418 (100 %)** — 413 HIGH (`General.GicSector`) + 5 MEDIUM (`General.Sector` crosswalk) |
| still Unknown after repair | **0** |
| mapped tickers in panel | **127 → 545** |
| Norgate local metadata | UNAVAILABLE (no local dump; logged honestly, not fabricated) |

### Before → after (2026Q2 book)

| metric | before (10-E) | after (10-F-A) |
|---|---|---|
| Unknown-sector **book** share | **0.778** | **0.000** |
| Unknown-sector **long** share | 0.794 | 0.000 |
| n Unknown names in book | 151 | 0 |
| top **mapped** long sector share | 0.072 | **0.196** (Industrials; `high_concentration` = False, < 0.60) |
| long / short candidates | 97 / 97 | 97 / 97 |

### Rerank churn (the sector-neutral score is now a genuine neutralisation)

`comp_sn` was recomputed over the repaired sectors (`comp_raw` unchanged, as it is sector-independent).
Book turnover vs the 10-E book: **14 names entered / 14 left the long side; 25 entered / 25 left the
short side** (83 longs and 72 shorts unchanged). The largest movers are interpretable — e.g. **NWS /
NWSA** (News Corp, both share classes) drop HOLD → SHORT once neutralised within Media/Communication
Services; **EXPE → Consumer Discretionary**, **EA → Communication Services**, **STX / APP → Information
Technology** are now correctly grouped.

### Honest caveats

1. **Current-as-of, not point-in-time.** Repaired sectors are today's company classifications, used only
   as a static neutralisation grouping — same non-PIT basis as the existing curated map. Sector is not a
   return feature here, so this introduces no return lookahead, but it is not a historical sector panel.
2. **The repair fixes the labels, not the alpha.** 10-D already validated the composite; this phase only
   makes the sector-neutral book reviewable. It is paper-**review**-ready, not a sized or cleared book.
3. **No order / automation / Paper Trader write of any kind.** A human must still approve/reject each
   name.

### Per-end-report fields

- **Final decision:** `SECTOR_MAPPING_REPAIRED_READY_FOR_HUMAN_REVIEW`.
- **Unknown-sector share before:** 77.8 % of the book. **After:** 0.0 %.
- **Sectors repaired:** 418 (413 HIGH / 5 MEDIUM). **Still Unknown:** 0.
- **Top-sector exposure after repair:** Industrials 19.6 % of the long book (no concentration breach).
- **Long/short candidates changed:** 14 in / 14 out (long), 25 in / 25 out (short); 97 long / 97 short.
- **Reranked book ready for human review?** **Yes** (paper-only).
- **Written to Paper Trader?** **No.** **Orders / automation created?** **No.** No broker, no live
  trading, no deploy.
- **Exact next command:** `review research/output/phase10f_owned_sector_mapping_repair/reranked_paper_review_long_short_book.csv`.
- **Targeted tests:** **16 passed**, 0 failed.
- **Commit recommendation:** **Do not commit** (standing rule). Runner, 16-test suite, doc, and 15
  metadata-only artifacts are on disk for review.

### Recommended Phase 10-G

Run the **human approve/reject gate** over `reranked_paper_review_long_short_book.csv` (now sector-mapped
and reranked). On approval, build a **paper-only position tracker** (mark-to-market each quarter; realised
vs expected net-25bps) — still **no orders, no automation, no broker, no live trading, no deploy**.
Optionally fold the repaired labels back into the panel's sector source so future runs are mapped by
default. No new data purchase.
