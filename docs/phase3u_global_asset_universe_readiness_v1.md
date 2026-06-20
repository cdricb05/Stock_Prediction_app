# Phase 3-U — Global Cross-Asset ETF Proxy Universe Readiness Gate (v1)

Phase 3-U is a **research-only** readiness and data-planning gate. It trains no model, creates no
production model candidate, computes no predictions / scores / portfolio weights / order
instructions, and writes no deployable model artifact.
Guardrails: it does not deploy. It does not run migrations. It does not write to production DB. It does not trade.
It restarts no prediction
service, enables no model-v2 serving flag, **writes nothing to the data drive** (it may only
**read** the single allowed price panel for availability inspection), calls no provider /
paid-vendor / Alpha Vantage / FRED API, uses no yfinance library, reads no provider API key value,
and places no orders. No production edge is claimed; the equity universe is current-as-of, so
results remain survivorship-biased. Price series are never invented: when a proxy has no local
price history the gate records the exact, **point-in-time** data requirement instead of fabricating
values. Price data, like earnings/sentiment data in earlier phases, **are not faked**.

## Why this phase

Phase 3-S recommended continuing earnings coverage (Phase 3-T) toward the 75-ticker signal gate,
but Phase 3-T is currently **provider-limited / waiting** on the Alpha Vantage daily quota (cached
earnings tickers are at 50 of 128; the signal gate minimum of 75 is not yet met). Rather than idle,
Phase 3-U advances the *orthogonal* expansion the roadmap already anticipated: moving the research
model from a US-equity-only universe toward a **global, multi-asset** view using liquid, free ETF
price proxies. This gate plans that move safely — it defines the universe, measures what local price
data already exists, classifies what is missing, and blueprints the cross-asset features — **without
calling any provider or downloading anything**.

## What it does

1. **Confirms the current state** — reads the Phase 3-T / 3-M `collection_progress.json`
   (read-only; Phase 3-M outputs are never modified), confirms cached earnings tickers are at least
   50, records whether the earnings signal gate is allowed now, and notes that earnings collection
   is provider-limited / waiting.

2. **Defines the target global cross-asset proxy universe** — 22 liquid ETF proxies across seven
   asset classes — US equity, international developed equity, emerging-market equity, rates, credit,
   commodities, currencies, and volatility — each with `ticker`, `asset_class`, `region`,
   `exposure`,
   `role_in_model`, `priority`, and `notes`.

3. **Inspects local price availability** — scans local repo CSV price panels and the one allowed
   **read-only** price panel for each target ticker, detecting whether a daily price history exists
   locally and, if so, its row count and date min/max. No network is used; nothing is downloaded.

4. **Classifies the missing data** — for every proxy that has no local price history it writes the
   exact, non-faked data requirement: required OHLCV fields, minimum history start, daily frequency,
   the point-in-time adjustment rule, priority, why it is needed, and the acceptable local file
   format (the same schema as the existing local price panel).

5. **Blueprints the cross-asset features** — describes (does not implement or compute) eight feature
   families: equity risk appetite, global equity breadth, rates/duration regime, credit risk,
   commodity inflation, dollar liquidity, volatility risk, and a combined cross-asset risk-on/off
   regime — each with required tickers, expected impact, point-in-time safety, and an implementation
   status driven by whether the required tickers are locally available.

6. **Writes the global model roadmap** — ordered phases 3-V → 4-A (assemble the local global ETF
   price panel → build the cross-asset feature factory → re-run the walk-forward with
   earnings + macro + cross-asset features → turnover-aware portfolio/risk simulation → non-
   production candidate packaging → Paper Trader preview integration only).

## Decision rule

Let `A` be the number of target proxies with a local daily price history and `C` the number of
distinct asset classes those cover:

| Condition | Recommendation |
| --- | --- |
| `A >= 12` and `C >= 5` | `GLOBAL_ASSET_UNIVERSE_READY_FOR_FEATURE_FACTORY` |
| `A >= 5` (but not READY) | `GLOBAL_ASSET_UNIVERSE_PARTIAL_LOCAL_COVERAGE` |
| `A < 5` | `GLOBAL_ASSET_UNIVERSE_BLOCKED_NEEDS_LOCAL_PRICE_DATA` |
| required inspection inputs unreadable | `GLOBAL_ASSET_UNIVERSE_BLOCKED_INPUTS` |

In every case the recommended next phase is **3-V** (READY → *Build Cross-Asset Feature Factory*;
PARTIAL → *Acquire Missing Global Asset Price Data*; BLOCKED_NEEDS_LOCAL_PRICE_DATA → *Acquire Local
Global ETF Price Data*; BLOCKED_INPUTS → *Repair Phase 3-U Inputs*).

## This run (current local state)

| Metric | Value |
| --- | --- |
| Target universe size | 22 proxies / 8 asset classes |
| Locally available proxies | 1 (`SPY` only, from the read-only price panel) |
| `SPY` local coverage | 2016-01-04 → 2026-06-16 (2628 daily rows) |
| Missing proxies | 21 |
| Asset classes covered locally | 1 of 8 (us_equity) |
| Earnings state (Phase 3-T) | provider-limited / waiting (cached 50 of 128; gate min 75) |

`A = 1 < 5`, so the recommendation is **`GLOBAL_ASSET_UNIVERSE_BLOCKED_NEEDS_LOCAL_PRICE_DATA`** and
the recommended next phase is **3-V — Acquire Local Global ETF Price Data**. The other 21 proxies'
exact (non-faked) price requirements are written to `global_asset_data_requirements.csv`, and the
cross-asset feature blueprint marks the dependent feature families `BLOCKED_NEEDS_PRICE_DATA` until
that local data is assembled.

## Outputs

- `research/output/phase3u_global_asset_universe_readiness.json` — full result + safety flags.
- `phase3u_global_asset_universe_readiness/target_global_asset_universe.csv`
- `phase3u_global_asset_universe_readiness/local_price_coverage.csv`
- `phase3u_global_asset_universe_readiness/global_asset_data_requirements.csv`
- `phase3u_global_asset_universe_readiness/cross_asset_feature_blueprint.csv`
- `phase3u_global_asset_universe_readiness/global_model_roadmap.csv`
- `phase3u_global_asset_universe_readiness/readiness_decision_table.csv`

## Global model roadmap

| Phase | Title |
| --- | --- |
| 3-V | Acquire or assemble local global ETF price panel |
| 3-W | Build cross-asset feature factory |
| 3-X | Re-run walk-forward model with earnings + macro + cross-asset features |
| 3-Y | Turnover-aware portfolio simulation and risk budget |
| 3-Z | Non-production candidate packaging |
| 4-A | Paper Trader preview integration only |

## Guarantees

All inspection is read-only and all data is real and **never faked**: missing proxies are recorded
as exact requirements, not invented price series. Every output file is Git-safe (well under 50 MB).
This phase is **research-only** and claims no production edge.
