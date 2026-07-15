# Phase 13-G (Part A) — Current Alpha Universe Integrity Audit + S&P 500 Shadow

**Status:** complete · offline · owned/local data only · champion unchanged
**Runner:** `research/run_phase13g_current_alpha_universe_integrity_audit.py`
**Tests:** `tests/test_phase13g_current_alpha_universe_integrity_audit.py`
**Output:** `research/output/phase13g_current_alpha_universe_integrity_audit/`
(`phase13g_current_alpha_universe_integrity_audit.json`, `current_alpha_universe_membership.csv`)

## Why this phase exists

Before wiring a daily trading-desk cockpit on top of the champion `composite_sn` book,
we must state **honestly** what universe actually produced the Phase 13-A ranked panel —
and **not silently relabel it "S&P 500"** if it is not.

## What the universe actually is (traced, not assumed)

The Phase 13-A ranked cross-section reads the frozen Phase 10-L scored panel
(`historical_sector_neutral_scored_panel.csv`). Its tickers come from the **Phase 8-V
"combined EODHD price + fundamentals universe expansion"**:

- base broad-universe price cache `D:\Stock_Prediction_app_data\phase7i_broad_universe\...`
  (**301** priced tickers), plus
- **247** EODHD-acquired S&P-500-seeded names (seed = a *current* Wikipedia S&P 500 snapshot),
- materialized as `research/data/eodhd/normalized/eod_prices/expanded_price_panel.csv`
  = **548 tickers → 545 scoreable** (grown from an "old" cohort of 299).

So the validated universe is **S&P-500-SEEDED but BROADER**, not a strict S&P 500 index universe.
The panel-schema note "Norgate survivorship-free universe" is an *aspirational label*; the actual
membership is the 8-V EODHD acquisition, not a Norgate index-membership query.

### Headline answers

| field | value |
|---|---|
| `validated_alpha_universe_name` | `phase8v_combined_eodhd_price_fundamentals_universe` |
| `latest_ranked_count` | **234** (2026-05 cross-section, signal date 2026-05-22) |
| `is_strict_sp500_universe` | **false** |
| latest names confirmed PIT S&P 500 | **194 / 234 (82.9%)** |
| not confirmed S&P 500 | **40** (3 in the S&P superset but not members then; 37 never in the superset — e.g. foreign ADRs) |
| unknown membership | 0 |
| **decision** | **`CURRENT_UNIVERSE_BROADER_KEEP_CHAMPION`** |

The champion `composite_sn` is preserved on its **original validated universe**. Ranks and
historical evidence are unchanged; the audit **never auto-replaces** the champion.

## S&P 500 shadow (owned PIT membership IS available)

Owned point-in-time S&P 500 membership exists locally: the Norgate **"S&P 500 Current & Past"**
monthly membership panel
`D:\Stock_Prediction_app_data\research_panels\phase8a_norgate_sample\membership_panel.csv`
(month-end `1.0`/`0.0` matrix, 1990-01 → 2026-06, resolved strictly point-in-time as the latest
month-end `≤ rebalance_date`). Caveats: it is a 1,363-of-1,894 **sample** of the full superset,
**monthly** resolution, delist-suffixed identities are not matched to current tickers.

Because that membership is available, a separate **`S&P500_SHADOW`** is built with the **same
`composite_sn` formula** — **no reweight, no retune, no new factor, no threshold optimization**:
it filters the **same frozen scored cross-section** to PIT S&P 500 members and re-runs the **same
quarterly quintile L/S evaluation** (the exact 10-D engine: `lb10.quarterly_book`, `c10._eval`).

### Champion vs shadow (63-day quarterly quintile L/S, same cost assumptions)

| metric | CURRENT_CHAMPION (broader) | S&P500_SHADOW (PIT-filtered) |
|---|---|---|
| coverage (scoreable rows) | 37,917 | 27,713 |
| tickers | 545 | 452 |
| IC mean 63d | 0.0349 | 0.0351 |
| IC t 63d | **2.665** | 2.593 |
| avg quarterly return (gross spread) | 0.00707 | 0.00328 |
| spread t | 1.352 | 0.805 |
| hit rate | 0.575 | 0.525 |
| turnover | 0.6115 | 0.6301 |
| **net-25bps** | **+0.00401** | +0.00013 |
| **net-50bps** | +0.00095 | −0.00302 |
| cumulative (compounded) | +0.2986 | +0.1256 |
| max drawdown | −0.1182 | −0.0796 |
| n quarters | 40 | 40 |

The champion side **reproduces the frozen Phase 10-D `composite_sn` baseline exactly**
(IC t 2.665, net-25bps +0.00401, net-50bps +0.00095, turnover 0.6115) — a panel-integrity check
that the engine reuse is faithful.

**`sp500_shadow_decision = SP500_SHADOW_REJECTED_WEAKER`.** Restricting to PIT S&P 500 members
roughly halves the gross spread and removes the net-of-cost edge (net-25bps +0.00013, net-50bps
negative). Part of the champion's modest edge comes from the broader (non-S&P) breadth. Keep the
champion on its validated broader universe; treat the shadow as **research comparison only**.

The latest-month shadow books (`shadow_top25` / `shadow_top50`) are the highest-`composite_sn`
PIT members of the 2026-05 cross-section — 21 of the champion top-25 and 42 of the champion top-50
are confirmed S&P 500 members.

## Decision enums

- Universe identity: `CURRENT_UNIVERSE_CONFIRMED_SP500` | `CURRENT_UNIVERSE_BROADER_KEEP_CHAMPION`
- Shadow verdict: `SP500_SHADOW_READY_FOR_PAPER_TEST` | `SP500_SHADOW_REJECTED_WEAKER` | `SP500_MEMBERSHIP_DATA_INSUFFICIENT`

## Constraints honored

Fully offline (owned/local frozen panel + 13-A package + owned Norgate membership; **no network,
no key, no provider probe**); research/paper only; **does not change the champion ranks or
historical evidence**; no reweight / retune / new factor / threshold optimization; **no Paper
Trader writes, no orders, no automation, no broker, no deploy, no GCP**; output is metadata /
research CSV+JSON only. No commit inside the runner. No push.

## Run

```powershell
$py = "C:\Users\binis\paper_trader\.venv-win\Scripts\python.exe"
& $py research\run_phase13g_current_alpha_universe_integrity_audit.py
```
