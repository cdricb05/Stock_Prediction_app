# Phase 10-H — Rules-Based Paper Portfolio Constructor (v1)

## Purpose

Phase 10-G produced a 194-name review template (97 long / 97 short, all `NEEDS_REVIEW`). The user does
**not** want to hand-review 194 tickers. The correct operating model is: **the user approves the
construction rules (and a short exceptions list), not every ticker.**

`research/run_phase10h_rules_based_paper_portfolio.py` builds a first paper-only long/short portfolio
from the 10-F-A repaired book using transparent, fixed rules, and surfaces the handful of names the
rules excluded or flagged. The human reviews **rules + exceptions**, not 194 names.

It is **not** a new alpha search, **not** a provider search, **not** manual per-ticker review, **not** a
Paper Trader integration, **not** order creation, **not** automation, **not** a deploy. Fully offline
(no network, no API key, no provider probe). Output is metadata-only CSV/JSON.

## Inputs (owned 10-F-A artifacts only)

- `reranked_paper_review_long_short_book.csv` — the 97L/97S side universe (side, comp_sn, sector,
  liquidity_proxy, cohort).
- `reranked_paper_review_candidate_list.csv` — enrichment: `comp_sn_z` (for extreme detection) and the
  composite leg inputs `fcf_to_assets` / `operating_accruals`.
- `repaired_book_risk_flags.csv` — carried for cross-validation only (the phase applies its **own**
  rules).
- `phase10f_owned_sector_mapping_repair.json` — `as_of` / `latest_quarter` (rebalance schedule).

## Default construction rules (the thing the user approves)

| # | Rule | Setting |
|---|---|---|
| 1 | Rank within side by the 10-D **sector-neutral** composite | `comp_sn` |
| 2 | Book size | up to **25 long / 25 short** |
| 3 | Liquidity filter | exclude **bottom-quartile** (below p25 of `liquidity_proxy`) |
| 4 | Missing-sector filter | exclude Unknown/blank sector |
| 5 | Missing-input filter | require `fcf_to_assets` + `operating_accruals` |
| 6 | Sector cap | **≤ 25% of each side** (= 6 names per sector at a 25-name side) |
| 7–8 | Weighting | **equal-weight** each side |
| 9 | Gross exposure | **100% long / 100% short** (paper; net 0%, gross 200%) |
| 10 | Rebalance | **quarterly** |
| 11 | Weights | **no optimisation** |
| 12 | Approval model | **no per-ticker approval** — rules + exceptions |
| 13 | Extreme scores | **flag and HOLD OUT** `|comp_sn_z| ≥ 3.0` (not auto-included) |
| 14 | Underfill | if < 25 per side pass, produce the smaller valid book and explain why |

Method: **filter → rank → sector-capped greedy fill → equal weight.** The composite is the 10-D one,
unchanged; this phase only selects and weights.

## Decision rule

- `RULES_BASED_PAPER_PORTFOLIO_READY_FOR_RULE_APPROVAL` — full 25/25 book, no rule exceptions.
- `RULES_BASED_PAPER_PORTFOLIO_READY_WITH_EXCEPTIONS` — book built (possibly < 25/side) **and** rules
  had to exclude/flag names (liquidity / sector cap / extreme / missing). *(Expected normal outcome.)*
- `RULES_BASED_PAPER_PORTFOLIO_BLOCKED_TOO_FEW_CANDIDATES` — a side falls below the 3-name viability
  floor.
- `HARD_BLOCKER_REQUIRES_USER_ACTION` / `ERROR_WITH_REPRO_COMMAND`.

**Forbidden:** `LIVE_TRADING_READY`, `ORDER_READY`, `AUTOMATION_READY`, `PAPER_TRADER_READY`,
`STRONG_ALPHA_FOUND_READY_FOR_REVIEW`, `MISSING_KEY`, `NO_DATA`, `NEEDS_PROVIDER`, `EMPTY_PAYLOAD`,
generic `ERROR`.

## Artifacts (`research/output/phase10h_rules_based_paper_portfolio/`)

`phase10h_rules_based_paper_portfolio.json` · `selected_paper_portfolio.csv` · `selected_long_book.csv`
· `selected_short_book.csv` · `excluded_candidates.csv` · `portfolio_construction_rules.csv` ·
`portfolio_sector_exposure.csv` · `portfolio_liquidity_summary.csv` ·
`portfolio_long_short_balance.csv` · `portfolio_exceptions_report.csv` · `rule_approval_checklist.csv`
· `phase10i_next_plan.json` · `secret_safety_audit.csv`.

## Run / test (Windows PowerShell only)

```powershell
Set-Location C:\Users\binis\Stock_Prediction_app_push
python -m py_compile research/run_phase10h_rules_based_paper_portfolio.py
python -m pytest tests/test_phase10h_rules_based_paper_portfolio.py -q   # targeted; 17 passed
python research/run_phase10h_rules_based_paper_portfolio.py              # fully offline; no key
```

## Status — live run 2026-06-30 (offline; exit 0)

**Decision: `RULES_BASED_PAPER_PORTFOLIO_READY_WITH_EXCEPTIONS`.**

| metric | value |
|---|---|
| selected long / short | **25 / 25** (equal-weight 0.04 each; 100% / 100% paper) |
| excluded | 144 (every one explained in `excluded_candidates.csv`) |
| liquidity filter | p25 threshold ≈ **$135M**; **49 excluded** (22 long / 27 short) |
| largest sector share | **24.0%** (Industrials/IT long; under the 25% cap — `sector_cap_respected = True`) |
| exceptions | **56** = 49 bottom-quartile-liquidity + 7 extreme-score; 0 missing-sector/input; 0 sector-cap |
| extreme held out | EXPE, JPM, BKNG, EA, STX (long); ASML, CTVA (short) — `|comp_sn_z| ≥ 3` |
| rebalance | **QUARTERLY**, next ≈ **2026-09-30** (holding ≈ 96 days) |
| Paper Trader writes / signals / trade decisions / orders / automation | **None** |
| secret leak scan | clean |

The rules removed the extreme-score outliers (the very top raw composite names) and the illiquid tail
(RELX, IVZ, HDB, SMERY, …), then equal-weighted a sector-capped 25/25 book. The user reviews
`rule_approval_checklist.csv` (7 items) and `portfolio_exceptions_report.csv` (56 names) — **not 194
tickers**. Held-out extreme/liquidity names can be added back only via an explicit human exception.

## Constraints honored

Offline (no network/key/provider probe); reads only the owned 10-F-A artifacts; no
FMP/AlphaVantage/Polygon/Finnhub/Norgate-API; no new purchase; **rules-based (no per-ticker approval)**;
**no optimised weights**; **no Paper Trader writes; no signals; no trade decisions; NO orders; NO
automation; NO broker; NO live trading; no deploy; no GCP**; no package install; no full regression
(targeted tests only); keys never printed or written; output is metadata only. **No commit. No push.**

## Recommended Phase 10-I

Human signs off `rule_approval_checklist.csv` and resolves `portfolio_exceptions_report.csv`; on
approval, build a **paper-only position tracker** for the selected book (quarterly mark-to-market;
realised vs expected net-25bps) — still **no orders, no automation, no broker, no live trading, no
deploy**.
