# Phase 7-I — Controlled Broad Universe Data Foundation Build (v1)

**Status:** research / data-build exercise only.
**Recommendation:** `LIVE_COLLECTION_APPROVAL_REQUIRED`.
**Not** a trading system, production model, order/execution automation, factor/model/signal
retest, factor-weight optimization, or regime activation. No paid APIs. No packages installed.
Default run makes **no network call** and writes **nothing** to the D: data drive. Nothing
committed or pushed.

Governed by `docs/project_charter_sp500_multifactor_ranking_v1.md`.

---

## Why this phase exists

Phase 7-F is the best local signal checkpoint (upgraded composite IC **+0.017726**, t **1.245**),
and Phase 7-H proved that denser local TTM fundamentals do **not** improve it
(IC **+0.013954**, t **0.922**, incremental **−0.003772**). Both phases concluded that the
binding constraint is no longer the model or the fundamentals — it is the **data foundation**:
a ~128-name, current-constituent universe with survivorship bias and no point-in-time sectors.

The next honest step is therefore **not** more local feature polishing but a broader-universe
**data build**. This phase asks: *can we expand from ~128 names to a materially larger local
universe with daily prices and point-in-time fundamentals, using only free sources, in a
controlled and reproducible way?* It plans that build and (only under explicit approval) runs a
bounded pilot. It does **not** model or retest.

---

## What was built

`research/run_phase7i_controlled_broad_universe_data_build.py` — a preview-first, dry-run-by-
default data-build engine that:

1. **Detects dependencies** without importing them (`importlib.util.find_spec`): the free price
   collector `yfinance` and `pandas`; the SEC collector needs only stdlib `urllib`. Missing
   dependencies are **reported, never installed**.
2. **Builds a deterministic candidate universe** from the local SEC ticker directory
   (`company_tickers.json`, 10,415 names, preserved in market-cap order), excluding obvious
   non-common-stock symbols.
3. **Builds a controlled collection plan** for three tracks: daily adjusted prices (yfinance),
   SEC companyfacts fundamentals (stdlib EDGAR), and the CIK identity mapping (already local).
4. **Emits a storage manifest, a dependency check, and the exact future PowerShell live-run
   command**, and a `phase7j_next_plan.json`.
5. **Runs a controlled pilot live collection ONLY** when `PHASE7I_LIVE_APPROVED=YES`: daily
   prices for at most 300 top candidates, rate-limited, cache-backed, never overwriting an
   existing combined panel without backup, with large data written to the D: data root.

It reuses proven, already-tested code: the pure transform / data-quality helpers from
`build_phase2k_g_free_expanded_dataset` (yfinance retrieval + normalization + DQ) and the SEC
ticker directory path / repo inventory from Phase 7-G.

### The candidate universe (deterministic)

| Metric | Value |
|---|---:|
| SEC directory size | 10,415 |
| **Included candidates (common-stock-like)** | **8,702** |
| Excluded | 1,713 |
| Already collected locally (within candidates) | 128 |

Exclusion reasons (heuristic, conservative):

| Reason | Count | Basis |
|---|---:|---|
| `invalid_characters` | 540 | ticker not `^[A-Z]{1,5}$` (digits, dots, dashes, len > 5; e.g. `BRK-B`) |
| `suffix_warrant` | 430 | 5-letter symbol ending `W` |
| `title_fund_or_etf` | 375 | title matches ETF / ETN / FUND / closed-end / BDC keywords |
| `suffix_unit` | 251 | 5-letter symbol ending `U` |
| `suffix_rights` | 101 | 5-letter symbol ending `R` |
| `title_preferred` | 15 | title matches PREFERRED / DEPOSITARY / PFD |
| `title_right` | 1 | title matches RIGHTS |

> **Honest caveat.** Authoritative common-stock classification needs share-class / security-type
> metadata that is **not** in the local SEC directory. These rules are deliberately conservative
> (they drop suspected non-common names) and a few legitimate dual-class tickers carrying a dash
> (e.g. `BRK-B`) are excluded as `invalid_characters`. Every row carries its `exclusion_reason`
> in `candidate_universe.csv` so a later phase can audit and refine the filters. The pilot draws
> from the **largest** names (market-cap rank), where these edge cases are rare.

### The pilot subset

The controlled pilot is the **top 300 included candidates by market-cap rank** (rank 0 = NVDA,
1 = GOOGL, 2 = AAPL, …). Of these, 128 already have local prices; the rest would be genuinely
new coverage. The success bar is **≥ 250 usable price series** (each ≥ ~1 trading year of rows).

---

## Dependency availability (this environment)

| Dependency | Kind | Available | Version | Purpose |
|---|---|:--:|---|---|
| `pandas` | required | ✓ | 3.0.3 | panel assembly / DQ |
| `yfinance` | required | ✓ | 1.4.0 | free daily adjusted OHLCV collector |
| `urllib` / `json` / `csv` | stdlib | ✓ | stdlib | SEC EDGAR companyfacts collector |

Both required dependencies are present, so a live pilot **can** proceed once approved.

---

## Gate matrix

**18 PASS / 0 FAIL / 1 INFO / 1 N/A — 0 safety failures.**

* **INFO:** `live_collection_ran` (dry-run default — no live run this invocation).
* **N/A:** `usable_price_coverage_sufficient` (no live run yet, so coverage is not established).
* **PASS (capability):** candidate-universe-built, exclusion-filters-applied, collection-plan-
  built, price-collector-dependency-available, sec-collector-dependency-available,
  pandas-available, default-mode-is-dry-run.
* **PASS (safety):** no-network-calls-when-not-approved, no-paid-api, no-packages-installed,
  no-trading-order-automation, no-model-or-retest-in-this-phase, repo-outputs-are-summaries-only,
  large-data-on-data-drive, existing-files-not-overwritten-without-backup,
  no-paper-trader-gcp-broker, not-committed, not-pushed.

---

## Why `LIVE_COLLECTION_APPROVAL_REQUIRED`

The dry-run produced a concrete candidate universe (8,702) and a concrete collection plan; both
required dependencies are present; no safety gate failed. The only thing standing between this
phase and a usable broad panel is the **explicit human approval** to make the (free, bounded,
rate-limited) network calls. By design that approval is a single environment variable, so the
recommendation is `LIVE_COLLECTION_APPROVAL_REQUIRED` — not `BLOCKED` (nothing is broken) and not
`READY`/`PARTIAL` (no data has been collected yet).

Decision rule (strict):
* safety failure or missing price dependency → `DATA_COLLECTION_BLOCKED`;
* not approved (default) → `LIVE_COLLECTION_APPROVAL_REQUIRED`;
* approved + live ran + ≥ 250 usable price series → `READY_FOR_PHASE7J_BROAD_UNIVERSE_SIGNAL_RETEST`;
* approved + live ran + < 250 usable → `BROAD_UNIVERSE_DATA_PARTIAL`.

---

## Where data goes

* **Repo (committed-safe summaries / manifests only):**
  `research/output/phase7i_controlled_broad_universe_data_build/` — the eight artifacts below.
* **Large raw / normalized data (only on an approved live run):**
  `D:\Stock_Prediction_app_data\phase7i_broad_universe\` — a **new** directory; the existing
  `phase2k_g` price panel is never touched. A per-ticker cache and a `.prev` backup of the
  combined panel prevent any destructive overwrite.

---

## To run the approved pilot (Windows PowerShell)

`research/output/.../live_run_command.ps1` contains exactly:

```powershell
Set-Location 'C:\Users\binis\Stock_Prediction_app_push'
$env:PHASE7I_LIVE_APPROVED = 'YES'
python -m research.run_phase7i_controlled_broad_universe_data_build
Remove-Item Env:\PHASE7I_LIVE_APPROVED
```

It collects daily prices for up to 300 top candidates (rate-limited, free, cache-backed),
writes large data to D:, writes only summaries to the repo, and does **not** commit or push.

---

## Artifacts (`research/output/phase7i_controlled_broad_universe_data_build/`)

`phase7i_controlled_broad_universe_data_build.json`, `candidate_universe.csv`,
`collection_plan.csv`, `dependency_check.csv`, `data_storage_manifest.csv`,
`live_run_command.ps1`, `collection_status.csv` (all `PLANNED` in a dry-run),
`phase7j_next_plan.json`.

## Tests

`tests/test_phase7i_controlled_broad_universe_data_build.py` — 30 tests (symbol-classification
heuristics, candidate-universe build + de-dup + local overlap, pilot capping/ordering,
dependency detection present/missing, the gate matrix, all four recommendation branches, the
`PHASE7I_LIVE_APPROVED` env approval gate proving live collection never runs without it, and a
guarded end-to-end dry-run that asserts the eight artifacts exist, nothing is written to D:, and
the live-run command is ASCII-only). All pass.

## Recommended next phase

1. **Approve and run the pilot** (the command above). If ≥ 250 tickers gain usable daily price
   coverage → recommendation flips to `READY_FOR_PHASE7J_BROAD_UNIVERSE_SIGNAL_RETEST`.
2. **Phase 7-J — Broad Universe Signal Retest:** collect SEC companyfacts for the price-covered
   names via the existing stdlib EDGAR client, rebuild the de-cumulated TTM panel (reusing
   7-G/7-H), and re-grade the Phase 7-F/7-H composite through the **unmodified** Phase 7-B harness
   on the broad cross-section. Equal weight, no sign flipping, no optimization, regimes diagnostic
   only, and report the survivorship caveat (current-as-of membership, not point-in-time).

## Safety contract

Preview-first data build · default dry-run with no network · free sources only · no paid APIs ·
no package installs · large data on D: only · repo gets summaries only · existing files never
overwritten without backup · no model / factor / signal retest in this phase · no
trading/order/automation · no Paper Trader / GCP / broker / deployment · not committed · not pushed.
