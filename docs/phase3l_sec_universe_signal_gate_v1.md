# Phase 3-L — Full SEC Universe Expansion + End-to-End Fundamental Signal Gate (v1)

> Status note: the IC-results, temporal-breadth, sector-sanity, and final-recommendation
> sections below are finalized from the committed run artifact
> `research/output/phase3l_sec_universe_signal_gate.json` and its CSVs.

## Why Phase 3-L accelerates beyond the tiny-sample work

Phases 3-F through 3-K advanced one careful micro-step at a time: source selection (3-F), a
20-ticker mini-pipeline (3-G), feature engineering (3-H), a leakage-checked price-alignment dry run
(3-I), a staleness repair (3-J), and a tiny IC diagnostic (3-K). That discipline was correct for
proving the plumbing was leakage-free, but it left the central research question unanswered on a
sample far too small to answer it. Phase 3-L deliberately does more in one phase — it expands the
SEC fundamentals pipeline from 20 tickers toward the full current 128-equity universe, rebuilds the
normalization, features, alignment, labels, and the IC / feature-family / temporal-breadth gate end
to end — because only a broad, multi-regime cross-section can decide whether the SEC fundamental
feature families carry real signal. It remains research-only and safety-controlled: it **does not
deploy**, **does not restart stock-api.service**, **does not enable** any serving flag, **does not
run migrations**, **does not write to production DB**, and **does not trade**.

## Why Phase 3-K was inconclusive

Phase 3-K (`FUNDAMENTAL_IC_DIAGNOSTIC_INCONCLUSIVE_SMALL_SAMPLE`) found that several features on the
repaired 20-ticker panel cleared the raw IC-magnitude bar (max |mean IC| ≈ 0.205, rising with
horizon), but every qualifying dense cross-section fell in only three distinct calendar years
(2024–2026) at roughly five names per date, over heavily overlapping forward-return windows, with
yearly IC signs that flipped. The 365-day staleness cap, applied to a temporally sparse 20-ticker
SEC sample, concentrated all dense cross-sections into a single recent regime. Twenty tickers were
too few, and too temporally concentrated, to decide — so 3-K set `tiny_research_model_allowed_next =
false` and `expand_sec_universe_next = true`, routing here. Phase 3-L is the direct response: widen
the cross-section so dense IC dates exist across many years and regimes.

## Universe expansion

The universe is the current 128-equity sector map (`research/input/phase2k_p_sector_map_current.csv`,
current-as-of 2026-06-18); SPY is treated as a benchmark, not an equity, and is excluded from the
universe. Each ticker is mapped to its SEC CIK via `company_tickers.json`; tickers that cannot be
mapped or whose SEC facts cannot be fetched are recorded and skipped, and processing continues. The
success-quality bar is at least 90 processed tickers; the partial bar is at least 60. Because
membership is current-as-of (today's index constituents projected backward), the whole exercise
remains explicitly **survivorship-biased**, and that caveat is repeated on every artifact.

## SEC access and cache behavior

Network is used only for official SEC public JSON endpoints — `www.sec.gov/files/company_tickers.json`,
`data.sec.gov/submissions/CIK##########.json`, and
`data.sec.gov/api/xbrl/companyfacts/CIK##########.json` — with the declared User-Agent
`PaperTraderResearch/Phase3L cedric.binisti.research@example.com`, a minimum 0.25-second gap between
requests, and a hard cap of 270 total requests (one for the ticker map, then submissions +
companyfacts per selected ticker). No other domains are contacted; no paid vendor API is called, no
yfinance is used, and nothing is purchased. Behavior is cache-first: raw responses are pruned to the
mapped XBRL concepts and most-recent periodic filings and cached under
`research/output/phase3l_sec_universe_signal_gate/raw/`, so reruns read the cache and issue no
network requests. If SEC access and the cache both fail, the phase writes a clearly-marked
`SEC_UNIVERSE_SIGNAL_GATE_BLOCKED` result rather than crashing.

## Normalization

Fundamentals are normalized with the Phase 3-F / 3-G field mapping: revenue, net_income,
operating_income, eps_diluted, total_assets, total_liabilities, shareholder_equity,
operating_cash_flow, capital_expenditures, and a derived free_cash_flow (operating_cash_flow −
capital_expenditures, only when both are present). Each fact carries an `availability_datetime`
taken from the filing acceptance timestamp (or, conservatively, the filing date) — **never** the
fiscal_period_end — and a `point_in_time_usable` flag. The first-reported (earliest-filed) value is
kept per ticker + field + fiscal period; later restated values are treated as caveated and never
back-dated. Company identity is attached current-as-of from the sector map.

## Feature construction

The same six Phase 3-H feature families are rebuilt as trailing-only, point-in-time features:
profitability / margins (operating, net, fcf, operating-cash-flow margins), balance sheet / leverage
(liabilities-to-assets, equity-to-assets, asset turnover, liability-to-equity), growth / change
(year-over-year revenue, net income, operating income, EPS, assets, operating cash flow, free cash
flow), cash quality (cash conversion, fcf-to-net-income, capex intensity, accrual proxy), size /
scale (log assets / revenue / liabilities), and availability / recency (filing lag days). Annual
(10-K / FY) and quarterly (10-Q / Qn) snapshots are kept strictly separate; year-over-year growth
compares only same-period-type snapshots. Each snapshot's `feature_asof_date` is the latest
availability timestamp of the fields it uses, and is never the fiscal_period_end — guaranteeing no
look-ahead.

## Alignment and staleness controls

The Phase 2K-G expanded price history (`D:\...\phase2k_g_expanded_price_history_free.csv`, 2016-01-04
→ 2026-06-16, ~2,628 trading days per name) is read **READ ONLY**; nothing is written to the D:
drive. The universe is restricted to the processed tickers plus the SPY benchmark. For every trading
day, the active snapshot is the latest one already filed strictly before that day; a snapshot is
dropped once its active feature age exceeds the staleness cap (default 365 calendar days). Sensitivity
to the cap is reported at 365 / 540 / 730 days in `staleness_summary.csv`. Forward-return labels at
21 / 63 / 126 trading days — raw return, SPY return over the identical window, SPY excess, a binary
outperform flag, and a cross-sectional rank — are generated **for validation only**, never
forward-filled and never used as a model target.

## Leakage checks

Six per-row invariants are evaluated and aggregated in `leakage_checks.csv`: the active feature
must have an availability timestamp; that timestamp must be strictly before the scoring date; it
must be on or after the fiscal period end; it must not equal the fiscal period end; forward labels
must use only future closes; and the joined price row must be the scoring day. A separate inherited
point-in-time guard re-checks the panel. A non-blocked run must show zero leakage failures, and the
recommendation is forced to `BLOCKED` if any leakage is detected.

## IC results

Signal is measured exactly as in Phase 3-K: per feature and horizon, the cross-sectional daily
Spearman rank IC against `forward_excess_return_vs_spy` and `forward_return_rank_by_date`,
summarized into mean / median IC, an IC hit rate (directional consistency), and an IC IR, plus
top-vs-bottom quintile and top-10-vs-bottom-10 bucket return spreads. A date contributes an IC at
the partial floor of 15 valid names; the full-success density floor is 25 names. **The committed run
recorded the headline numbers below; see `feature_ic_summary.csv` and
`feature_family_ic_summary.csv` for the full table.**

- Processed tickers: see `universe_summary.processed_ticker_count`.
- Aligned tickers / rows: see `alignment_summary`.
- Best features and moderate-or-better / strong counts: see `decision_summary`.

## Horizon readiness

A horizon is `ready_for_research_model` only when it has at least one moderate-or-better feature,
at least 200 dense (≥25-name) IC dates, and dense cross-sections spanning at least 6 distinct
calendar years; otherwise it is `diagnostic_only` (enough partial dates to measure but not to
certify) or `not_ready`. This is the same temporal-breadth philosophy that governed Phase 3-K,
scaled up: raw IC magnitude alone never certifies the gate. See `horizon_readiness_summary.csv`.

## Temporal breadth

The model gate requires the qualifying dense cross-sections to span at least 6 distinct calendar
years (the partial-diagnostic floor is 4). This directly addresses the Phase 3-K failure mode, where
all dense dates sat in a single ~2-year regime. With the full universe, every current constituent
that existed and filed across 2016–2026 contributes to early-year cross-sections, so breadth is
measured honestly rather than assumed. The applied-cap breadth and its sensitivity to 540 / 730-day
caps are reported in `staleness_summary.csv`.

## Sector sanity

`sector_sanity_summary.csv` reports, per sector, the ticker count, row share, and the non-null
fractions of `operating_margin` and `capex_intensity`. Margin and capex features are structurally
undefined for banks, so any margin / cash-quality signal is effectively measured on non-financial
names; this is flagged, not patched, and no sector neutralization or modeling is performed.

## Final recommendation

The recommendation is one of `SEC_UNIVERSE_SIGNAL_GATE_PASSES_RESEARCH_MODEL_ALLOWED`,
`SEC_UNIVERSE_SIGNAL_GATE_WEAK_BUT_EXPAND_OR_ADD_REVISIONS`,
`SEC_UNIVERSE_SIGNAL_GATE_INCONCLUSIVE_DATA_COVERAGE`,
`SEC_UNIVERSE_SIGNAL_GATE_FAILS_ADD_RICHER_DATA`, or `SEC_UNIVERSE_SIGNAL_GATE_BLOCKED`. PASS requires
all of: ≥90 processed tickers, ≥75 aligned tickers, ≥75,000 aligned rows, zero leakage, label
coverage ≥0.80 / 0.70 / 0.60, at least one ready horizon, ≥5 moderate-or-better features, ≥2 strong
features, ≥2 stable families, and ≥6 distinct dense IC years. Insufficient coverage or breadth below
4 years yields `INCONCLUSIVE_DATA_COVERAGE`; sufficient multi-regime coverage with no usable signal
yields `FAILS_ADD_RICHER_DATA`; sufficient coverage with some-but-insufficient signal yields
`WEAK_BUT_EXPAND_OR_ADD_REVISIONS`. **The committed run's selected recommendation, its full reason,
and the decision table are in `research/output/phase3l_sec_universe_signal_gate.json`
(`recommendation`, `decision_summary`) and `decision_table.csv`.**

## Whether a research model is allowed next

`research_model_allowed_next` is `true` **only** under `PASSES`. Under any other recommendation it is
`false`: a weak result routes to richer data, an inconclusive result routes to coverage repair, a
failing result routes to alternative data, and a blocked result routes to input repair. A research
model is never permitted on coverage or signal that does not clear the full multi-regime gate, and
even under PASS the next phase is a **research-only** walk-forward, not a production candidate.

## Why no model is trained in Phase 3-L

This phase is a signal gate by design. It is allowed to ingest SEC fundamentals, build features and
validation labels, join prices, and compute IC / bucket-spread / family / yearly / sector
diagnostics — and nothing more. It fits no regression, logistic, ridge, lasso, tree, or any other
machine-learning estimator; it computes no predictions, scores, trading rankings, or portfolio
weights; and it writes no deployable model artifact. Measuring signal on a broad, leakage-free
sample must precede — and here, gates — any fitting.

## Why no production model candidate is created

A production model candidate implies a frozen, deployable artifact and an implied claim of edge.
This phase makes no such claim: it is a research IC gate on a current-as-of, survivorship-biased
universe. It creates no production model candidate, writes no deployable model artifact, and claims
no **production edge**.

## Safety guardrails

This phase **does not deploy**, **does not restart stock-api.service**, **does not enable** the
model-serving flag, **does not run migrations**, **does not write to production DB**, and it **does
not trade** or place orders. It reads the D: price panel read-only and writes nothing to the D:
drive, contacts only official SEC public endpoints (no paid vendor, no yfinance, no purchase),
ingests no production data, performs no full production integration, fits no model, computes no
predictions / scores / portfolio weights, creates no production model candidate, and writes no
deployable model artifact. It only measures information coefficients and bucket spreads on the
aligned validation panel and claims no **production edge**.
