# Phase 8-Q - Market Data Foundation Decision Gate and Broad Coverage Architecture

Status: tested (offline, 27/27). Decision artifacts generated. NOT committed.

## Why this phase exists

Phases 8-N / 8-O / 8-P were a sequence of small, provider-by-provider experiments. The user is
(rightly) tired of tiny 20-ticker trials and wants a serious answer: **what market data is actually
required for serious alpha research, and are free APIs a waste of time for the long run?**

This phase is a **decision gate, not a backfill**. It makes **no network calls**. It reads the
committed-safe 8-N / 8-O / 8-P artifacts, builds a tiered market-data requirement catalog, assesses
whether free APIs can serve a **broad (100-500 ticker) point-in-time** universe, and produces a
buy-vs-free decision plus a "test-first" procurement shortlist.

## The strategy correction this phase encodes

**20 tickers is a minimum SCORING GATE, not the target universe.** The real targets are, in order:

1. minimum scoring gate - 20 tickers (statistical floor only)
2. current working universe - Phase 5C / current available (~30-60)
3. broad research universe - **S&P 500-like (~500)** ← the real near-term target
4. future robust universe - survivorship-aware / point-in-time index membership

Free micro-batches are judged against **that** scale, not against 20 tickers.

## What it reads (read-only; never raw payloads)

- `research/output/phase8n_fmp_critical_data_backfill_signal_expansion/` - FMP plan **INSUFFICIENT**:
  every critical family entitlement-PARTIAL at **8/20**, SYSTEMATIC 402 blocks.
- `research/output/phase8o_cheapest_provider_selection/` - strategy **MIXED_PROVIDER_STRATEGY_REQUIRED**;
  FMP upgrade **not** justified; **FMP Ultimate rejected**; cost/value report for the provider matrix.
- `research/output/phase8p_alphavantage_earnings_expansion/` - Alpha Vantage **live**: added 1 ticker
  (ABT -> combined **9/20**), then **rate-limited after 3 requests** (free tier 25 req/day).

## The evidence that drives the decision

At the Alpha Vantage free tier (25 requests/day), a 500-ticker universe takes **~20 calendar days**
for a single refresh - free **throughput** is not a durable foundation for broad point-in-time
research. FMP's current plan is blocked. SimFin is broad but **delayed** (not clean real-time PIT).
FRED is genuinely sufficient **for macro only**. So the honest answer is **neither pure-free nor
pure-buy**: it is a **mixed free + paid core stack**.

## Final decision: `MIXED_FREE_PLUS_PAID_CORE_STACK`

- **Keep FREE** where it genuinely works: FRED (macro, full history + vintages), adjusted OHLCV
  (existing cache), ticker/sector identity, and EDGAR insider/13F when those are eventually needed.
- **BUY one cheap broad earnings/fundamentals provider**: test **EODHD (~$20-$80/mo)** first - the
  cheapest single bundle covering earnings + fundamentals + analyst with bulk download and PIT dates.
  **FMP Premium (~$69/mo)** is the fallback only if EODHD fails on coverage/PIT/history.
- **DEFER expensive alt-data**: news sentiment, earnings transcripts, options IV/skew, short
  interest/borrow, insider, 13F - until a validated core signal earns the right to add it.
- **FMP Ultimate is rejected** - no evidence requires it; cheaper sources cover every critical family.

## Data-family priority tiers

| Tier | Meaning | Families |
| --- | --- | --- |
| 0 | required now | adjusted OHLCV, sector/identity, FRED macro, earnings actual/estimate/surprise |
| 1 | required if earnings signal survives | analyst estimates/revisions, recommendations, price targets, fundamentals statements, ratios/quality/value/growth |
| 2 | valuable but not required yet | news sentiment, short interest/borrow, insider transactions, 13F ownership |
| 3 | expensive optional expansion | earnings transcripts, options implied volatility / skew |

Free is **sufficient** for the Tier-0 non-equity-fundamental layer (FRED + OHLCV + identity) and
**insufficient** for broad earnings (Tier 0) and the Tier-1 analyst/fundamentals families.

## Explicit buy-vs-free answers (the brief's item 6)

- Stop trying free sources for **broad** earnings? **Yes** (keep AV free only as cheap evidence).
- Is an FMP **Premium** upgrade justified? **Only as a fallback** if EODHD fails.
- Is **FMP Ultimate** justified? **No - rejected.**
- Buy a cheaper earnings/fundamentals provider instead? **Yes - test EODHD first.**
- Wait to buy sentiment/transcripts/options until a core signal works? **Yes - defer.**

## Committed-safe artifacts (14)

| Artifact | Contents |
| --- | --- |
| `phase8q_market_data_foundation_decision.json` | main report: evidence, decision, headline, reasons, mandatory/deferrable, next command |
| `current_provider_coverage_summary.csv` | per provider: role, families, broad-coverage-proven, evidence phase, verdict |
| `market_data_requirement_catalog.csv` | 15 families: tier, required-now, when, free coverable/enough-broad, PIT, buy-evidence, source |
| `data_family_priority_tiers.csv` | family -> Tier 0/1/2/3 + label + rationale |
| `universe_scale_targets.csv` | min gate (20, not final) / current / broad S&P 500-like / future PIT membership |
| `free_stack_viability_report.csv` | per family: free provider, coverable, enough-for-100-500, PIT, limiting factor |
| `paid_provider_decision_matrix.csv` | providers x cost/coverage/PIT/history/bulk + **test-first order** |
| `vendor_evaluation_criteria.csv` | coverage / history / PIT / API limits / cost / license / freshness / bulk / schema |
| `recommended_market_data_stack.csv` | family -> recommended source + buy/free/defer + status |
| `deferred_expensive_data_families.csv` | Tier 2/3 families + defer-until condition |
| `buy_vs_continue_free_decision.csv` | the five explicit questions + answers + rationale |
| `provider_key_inventory.csv` | env var presence booleans (FRED/AV/SimFin/FMP/Finnhub/EODHD); `value_read=False` |
| `procurement_questions_for_vendors.csv` | questions to ask vendors before buying |
| `phase8r_next_plan.json` | next phase: bounded EODHD broad-bundle evaluation (FMP Premium fallback) |

All artifacts carry **metadata only** - never a payload, never a key.

## Decision values

`FREE_STACK_STILL_VIABLE`, `FREE_STACK_NOT_VIABLE_FOR_CORE_RESEARCH`,
`BUY_EARNINGS_FUNDAMENTALS_PROVIDER`, `MIXED_FREE_PLUS_PAID_CORE_STACK`, `DEFER_EXPENSIVE_ALT_DATA`,
`NEEDS_VENDOR_QUOTES`, `ERROR`. Current decision: **`MIXED_FREE_PLUS_PAID_CORE_STACK`**.

## Secret discipline (hard rules)

- API keys are checked as **presence booleans only** via `os.environ`; values are never read,
  printed, or written. The key inventory records `value_read=False`.
- A leak scan over the written artifacts confirms `secret_safety_leak_scan_clean` (no held key value
  and no `apikey=` query param in any committed file).
- The runner makes **no network calls** and writes **no provider payloads** - it only reads prior
  committed-safe metadata artifacts.

## Run

```powershell
# Always offline (a decision gate; reads 8-N/8-O/8-P, no network):
python research/run_phase8q_market_data_foundation_decision.py

# Test:
python -m pytest tests/test_phase8q_market_data_foundation_decision.py -q
```

## Constraints honored

Existing installed packages only (stdlib). No package install. No live network calls. No API key
printed or written. No raw/normalized provider data read into or emitted from committed artifacts.
No Paper Trader, no GCP, no deploy, no broker / order / automation logic. No commit. No push.
