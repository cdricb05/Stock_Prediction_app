# Phase 7-D — Risk Decomposition Foundation (System 1 risk lens, v1)

**Track A (quant brain) research. Offline, measurement-only, local data only.**
No network, no provider call, no paid API, no API key read or required, no model
trained or deployed, no factor-weight optimization, **no order / broker / automation /
hedging / sizing logic, no trade recommendation**, no Paper Trader / GCP work, no live
data, no binary artifact, no commit, no push. Reads only local files (the Phase 2K-G
price panel READ ONLY on D:, and committed sector / SEC fundamentals artifacts on C:).
Writes nothing to D:.

- **Phase:** 7-D
- **Status:** Implemented and gated (pending owner review)
- **Constitution:** [project_charter_sp500_multifactor_ranking_v1.md](project_charter_sp500_multifactor_ranking_v1.md)
- **Predecessors:** [phase7b_validation_harness_foundation_v1.md](phase7b_validation_harness_foundation_v1.md), [phase7c_multifactor_ranking_engine_v1.md](phase7c_multifactor_ranking_engine_v1.md)
- **Reuses:** the Phase 7-C factor construction + data loaders (`research/run_phase7c_multifactor_ranking_engine.py`)
- **Recommendation:** `READY_FOR_PHASE7E_REGIME_OVERLAY`

---

## Why this phase exists

The charter is a two-system design: System 1 = multi-factor ranking engine (Phase 7-C),
System 2 = regime / risk overlay. Charter **Phase 4.5** calls for a *risk decomposition*
layer that explains what exposures a book carries **before** any sizing, hedging, or
regime overlay is built. This layer is valuable even though Phase 7-C recommended
`MULTIFACTOR_RANKING_ENGINE_WEAK` (no confirmed alpha): understanding *what risks a book
carries* is independent of whether the ranking signal is profitable.

This is **not** a trading system, **not** a production model, **not** an order system,
**not** automation. It is a measurement layer. It explains exposure; it does not predict
returns and does not recommend trades.

## What was built

A measurement-only, book-level risk decomposition engine
([research/run_phase7d_risk_decomposition_foundation.py](../research/run_phase7d_risk_decomposition_foundation.py)):

1. Loads the local adjusted price panel (**including SPY**, which Phase 7-C drops — beta
   needs it) and the local sector map.
2. Builds a **deterministic sample portfolio** (explicitly **not** live or recommended
   holdings): 15 names evenly spaced across the sorted universe, linearly-decaying
   weights summing to 1. Fully reproducible from local data, no randomness.
3. Computes per-position trailing realized volatility, rolling beta to SPY, residual
   (idiosyncratic) volatility, and average dollar volume.
4. Aggregates to the book and reports the **five risk lenses** below.
5. Reuses the Phase 7-C point-in-time factor construction to report the book's **factor
   tilt** (weighted latest cross-sectional z of each factor bucket).
6. Emits a risk gate matrix and a consolidated risk view.

## The five risk lenses

| Lens | What it measures | Key outputs |
|---|---|---|
| **Market beta exposure** | systematic market risk vs SPY | net beta, portfolio vol, systematic vs idiosyncratic variance share |
| **Sector concentration** | sector tilt of the book | sector weights, sector HHI, effective # sectors, top sector |
| **Factor tilt exposure** | book lean on Phase 7-C factors | per-factor weighted z (momentum / low-vol / value / quality / growth), composite tilt |
| **Idiosyncratic / concentration risk** | single-name concentration & diversifiable risk | Herfindahl index, effective # names, top-1/3/5 weight, diversifiable idiosyncratic vol |
| **Liquidity risk** | how fast the book can be unwound | ADV per name, days-to-liquidate at an assumed book size / participation rate |

## Universe & sample book

- **Investable universe:** S&P names present in the price panel ∩ the sector map (SPY is
  the benchmark, excluded from holdings but used for beta).
- **Sample portfolio:** 15 deterministically-selected names, concentrated linear-decay
  weights. This is a *measurement target*, not a recommendation.
- **Risk window:** trailing 252 trading days (min 120) for vol / beta; trailing 63 days
  for average dollar volume.

## Portfolio risk metrics computed (this run)

| Lens | Metric | Value |
|---|---|---|
| Market beta | net beta to SPY | **0.553** |
| Market beta | portfolio realized vol (annual) | 0.120 |
| Market beta | systematic variance share | 0.324 |
| Market beta | idiosyncratic variance share | 0.676 |
| Concentration | Herfindahl index | 0.0861 |
| Concentration | effective # names (1/HHI) | 11.6 |
| Concentration | max single-name weight | 0.125 |
| Concentration | top-5 weight | 0.558 |
| Sector | top sector | Industrials (0.225) |
| Sector | effective # sectors | ~6.0 |
| Liquidity | worst days-to-liquidate (at $10M book, 10% ADV) | 0.021 |

- The book is **net long market** (beta 0.55) but well below 1: the deterministic
  sample over-weights several lower-beta / defensive names (e.g. CL, PG, T showed mildly
  negative trailing beta over the last year in this tech-led tape), pulling net beta
  down. ~68% of the book's variance is **idiosyncratic** — the concentration lens
  matters more than the market lens for this book.
- Concentration is moderate (effective ~11.6 of 15 names; no name above 12.5%).
- Liquidity is not a constraint at the assumed $10M book — every name unwinds in well
  under a day at 10% of ADV.

## Risk gate matrix

**27 PASS / 0 FAIL / 1 WARNING.** All six capability gates (weights normalized, position
vol, beta, idiosyncratic split, concentration, liquidity) and all fourteen safety gates
PASS. The single WARNING is the **sector-map point-in-time caveat** (a static
current-as-of map is used for sector exposure). The informational risk-flag gates
(name / sector concentration, breadth, market beta, liquidity) are all within their
illustrative thresholds for this sample book and do **not** affect the recommendation —
they describe the book, they are not order triggers.

## What is still caveated

- The portfolio is a **deterministic sample**, not live or recommended holdings.
- Beta / vol / idiosyncratic split use a **single trailing window** with no regime
  conditioning yet — regime conditioning is Phase 7-E.
- The idiosyncratic split treats residual = portfolio variance − net-beta systematic
  variance; the **diversifiable** proxy additionally assumes zero residual correlation
  across names.
- Liquidity uses adjusted-close × volume as a **dollar-volume proxy** at an *assumed*
  gross book size and participation rate.
- Sector exposure uses a **static current-as-of** sector map (not strictly point-in-time).
- Factor tilt reuses **annual-only** Phase 7-C factors (value/quality/growth stale up to
  ~1 year).

## Recommendation

**`READY_FOR_PHASE7E_REGIME_OVERLAY`** — the risk decomposition layer is built from local
data only, every capability and safety gate passes, and it explains the book's exposure
across all five lenses without predicting returns or recommending trades. The single
warning (sector-map PIT) is a documented, non-blocking caveat.

Allowed values: `READY_FOR_PHASE7E_REGIME_OVERLAY` / `NEEDS_RISK_REVIEW` /
`DATA_QUALITY_BLOCKED` / `ERROR`.

## Committed-safe artifacts

Written to `research/output/phase7d_risk_decomposition_foundation/`:

- `phase7d_risk_decomposition_foundation.json` — main report (universe, sample book, five lenses, gates, recommendation)
- `sample_portfolio.csv` — the deterministic sample book (ticker, sector, weight, notional at assumed book)
- `position_risk_decomposition.csv` — per-position weight, sector, realized vol, beta, residual vol, ADV
- `sector_exposure.csv` — sector weight, name count, HHI contribution
- `factor_tilt_exposure.csv` — per-factor book-weighted z (Phase 7-C factors) + composite tilt
- `liquidity_risk_report.csv` — per-position notional, ADV, days-to-liquidate, liquidity bucket
- `concentration_report.csv` — HHI, effective N, top-weights, sector HHI, systematic/idiosyncratic split
- `risk_gate_matrix.csv` — capability + informational risk-flag + safety gates
- `phase7e_next_plan.json` — hand-off to Phase 7-E (regime / risk overlay)

Code + tests:

- [research/run_phase7d_risk_decomposition_foundation.py](../research/run_phase7d_risk_decomposition_foundation.py)
- [tests/test_phase7d_risk_decomposition_foundation.py](../tests/test_phase7d_risk_decomposition_foundation.py) (17 tests, all passing)

## Recommended next phase

**Phase 7-E — Regime / Risk Overlay Foundation (System 2)** (charter Phase 5): classify
market regimes from local price/vol history and report how this 7-D risk lens shifts by
regime — descriptive only, still no sizing, hedging, orders, or automation. Position
sizing / weight optimization remain deferred until the owner endorses a sizing
philosophy.

## Safety contract

Research only · zero network / provider call · no Alpha Vantage / paid API · no model
trained or deployed · no factor weights optimized · **no orders / broker / automation /
hedging / order sizing · no trade recommendation** · no Paper Trader / GCP / deploy ·
D: read-only (nothing written) · committed-safe text artifacts only · no commit · no push.
