# Phase 10-G — Human Review Template for the Repaired Long/Short Book (v1)

## Purpose

Phase 10-F-A produced a reranked, sector-mapped long/short book
(`research/output/phase10f_owned_sector_mapping_repair/reranked_paper_review_long_short_book.csv`,
97 long / 97 short) and returned `SECTOR_MAPPING_REPAIRED_READY_FOR_HUMAN_REVIEW`. The one allowed
next step is to put that book in front of a **human** for an explicit approve/reject pass.

`research/run_phase10g_review_template.py` builds that template. It reads the repaired book and writes
**one row per long/short candidate (194 rows)** with **every candidate defaulted to
`review_decision = NEEDS_REVIEW`**. It performs **no auto-approval**, writes **nothing to Paper
Trader**, creates **no orders**, and creates **no automation**. The template is a static, human-fill-in
CSV — editing a decision in it changes nothing downstream until a human acts in a later, separately
gated step.

It is **not** a new alpha search, **not** a provider search, **not** order creation, **not**
automation, **not** a deploy, and **not** a Paper Trader integration. Fully offline (no network, no API
key, no provider probe). Output is metadata-only CSV/JSON in this phase's own `research/output`
directory.

## What every candidate ships as

| column | value at generation | who owns it |
|---|---|---|
| `review_decision` | **`NEEDS_REVIEW`** (every row) | **human-editable** |
| `reviewer` / `reviewed_at` / `conviction` / `reviewer_notes` | blank | human-editable |
| `approved` | `FALSE` (every row) | fixed — generator never pre-approves |
| `order_action` | `NO_ORDER` (every row) | fixed — never implies an order |
| `review_status` | `PAPER_REVIEW_ONLY` (every row) | fixed |

Context columns carried **read-only** from the 10-F-A book for provenance: `side`, `rank_sn`,
`ticker`, `sector`, `sector_repaired`, `sector_is_unknown`, `comp_sn`, `comp_raw`, `cohort`,
`liquidity_proxy`, `before_review_label`.

**Allowed human decisions** (the human may later set `review_decision` to one of):
`NEEDS_REVIEW` · `APPROVE` · `REJECT` · `HOLD_FOR_MORE_INFO`. The generator ships all rows as
`NEEDS_REVIEW` and **never writes `APPROVE`**.

## Decision rule

- `REVIEW_TEMPLATE_READY_FOR_HUMAN_REVIEW` — template written, every candidate `NEEDS_REVIEW`.
- `HARD_BLOCKER_REQUIRES_USER_ACTION` — the 10-F-A repaired book is missing or has no long/short rows.
- `ERROR_WITH_REPRO_COMMAND`.

**Forbidden:** `LIVE_TRADING_READY`, `ORDER_READY`, `AUTOMATION_READY`, `AUTO_APPROVED`,
`STRONG_ALPHA_FOUND_READY_FOR_REVIEW`, `MISSING_KEY`, `NO_DATA`, `NEEDS_PROVIDER`, `EMPTY_PAYLOAD`,
generic `ERROR`.

## Artifacts (`research/output/phase10g_review_template/`)

- `repaired_book_review_template.csv` — 194 rows, all `NEEDS_REVIEW`.
- `review_template_manifest.json` — counts, default-decision guarantees, allowed human decisions,
  safety flags, column legend, exact next command.

## Run / test (Windows PowerShell only)

```powershell
Set-Location C:\Users\binis\Stock_Prediction_app_push
python -m py_compile research/run_phase10g_review_template.py
python -m pytest tests/test_phase10g_review_template.py -q   # targeted; 12 passed
python research/run_phase10g_review_template.py              # fully offline; no key
```

## Status — live run 2026-06-30 (offline; exit 0)

**Decision: `REVIEW_TEMPLATE_READY_FOR_HUMAN_REVIEW`.** 194 candidates (97 long / 97 short); **all 194
default to `NEEDS_REVIEW`**; `approved = FALSE` and `order_action = NO_ORDER` on every row;
`auto_approval = False`, `creates_orders = False`, `creates_automation = False`,
`wrote_to_paper_trader = False`. Targeted tests: **12 passed**.

## Constraints honored

Offline (no network/key/provider probe); reads only the owned 10-F-A repaired book; no
FMP/AlphaVantage/Polygon/Finnhub/Norgate-API; **no auto-approval**; **no Paper Trader writes; NO
orders; NO automation; NO live trading; NO broker; no deploy; no GCP**; no package install; no full
regression (targeted tests only); keys never printed or written; output is metadata only.
**No commit. No push.**
