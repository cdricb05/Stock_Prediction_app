# Phase 8-S - Autonomous EODHD Earnings/Fundamentals Alpha Factory

Status: implemented + tested (offline, 9/9 targeted tests). Runner:
`research/run_phase8s_autonomous_eodhd_alpha_factory.py`. A live acquisition + scoring run was
executed against the paid EODHD key. **Decision and run numbers are recorded in the Status block at
the bottom of this doc and in `research/output/phase8s_autonomous_eodhd_alpha_factory/`.** Nothing
committed, nothing pushed.

## Why this phase exists

Phase 8-R **accepted EODHD as the core earnings/fundamentals provider** (paid access works:
`earnings_history` + `fundamentals_statements` PIT-safe at 32/32 on the first batch). Phase 8-S is the
autonomous acquisition + scoring campaign that turns that provider into an honest alpha verdict. It
is **not** another provider-selection or one-batch phase: it acquires broadly, scores every relevant
earnings/fundamentals scenario with proper cross-sectional validation, and emits one terminal
research decision.

This is **preview-only research**. It produces ranking signals / trade-idea candidates for **manual
review**, never orders, never automation, never broker execution. It does not touch Paper Trader or
GCP. Raw + normalized EODHD payloads stay under the gitignored `research/data/eodhd/` tree.

## Autonomous workflow

1. **Universe** - the broadest set the local price cache can actually score. The scoreable ceiling
   is the intersection of the EODHD-coverable universe with the local OHLCV cache
   (`D:/Stock_Prediction_app_data/phase7i_broad_universe/prices/phase7i_broad_price_history_free.csv`,
   301 tickers, 2016-06-23 .. 2026-06-22, with a `benchmark_close` for index-relative returns).
   Ordering: **FMP-blocked first** (Phase 8-N), then the rest of the priced universe.
2. **Acquire** (bounded, live-gated) - one EODHD `fundamentals` request per not-yet-cached ticker
   (the `earnings_calendar` endpoint returns 0 rows on this plan; `reportDate` from the fundamentals
   `Earnings::History` is used as the point-in-time announcement date instead). `skip_existing`
   means already-cached tickers cost 0 requests. Checkpoints every 50 tickers. Stops on an invalid
   key, a free-tier entitlement block, or consecutive rate-limits. Reuses the proven Phase 8-R
   transport / redaction / persistence / secret discipline verbatim.
3. **Normalize** into point-in-time panels: an earnings-events panel (one row per ticker-quarter,
   keyed by `reportDate`) and a fundamentals quarterly panel (keyed by SEC `filing_date`).
4. **Join** to price / macro / sector context with strict no-look-ahead rules (below).
5. **Features** - earnings (surprise, surprise %, SUE, magnitude bucket, beat, beat streak, EPS YoY
   growth, EPS acceleration), fundamentals (revenue & earnings growth, margin & margin change,
   debt/assets, cash/assets, FCF margin, ROA - all as-of `filing_date <= reportDate`), price
   (market-relative pre-event momentum), regime (10y level, 2s10s slope, oil & dollar z-scores).
6. **Scenarios** (10) - standalone earnings surprise; standardized unexpected earnings (PEAD);
   surprise x rates regime; sector-neutral surprise; surprise x quality (ROA); acceleration x price
   momentum; beat streaks; quality-filtered surprise; revenue growth; regime-gated SUE.
7. **Validation** (honest, per scenario) - monthly cross-sectional **rank IC** (mean, std, t-stat,
   normal-approx p), **quintile long-short spread** (mean, t, monthly sign hit-rate), event hit
   rate, **turnover** (top-quintile name replacement), **transaction-cost sensitivity** (net spread
   at 5/10/20 bps round-trip x turnover), **regime split** (high vs low rates), **sector
   concentration** (top-sector share + HHI), and **multiple-testing control** (Bonferroni +
   Benjamini-Hochberg across scenarios).
8. **Promotion gates** - a signal is promoted only if it clears **all** of: `>= 150` usable events;
   `>= 10` monthly observations; `|mean IC| >= 0.03`; `|IC t| >= 2.0`; significant under
   Benjamini-Hochberg at `q = 0.10`; quintile-spread monthly sign hit-rate `>= 0.55`; spread
   survives `10 bps` round-trip cost; top sector `<= 60%` of the long book; right-sign IC in
   `>= 2` regimes.
9. **Decision** - `ALPHA_FOUND_READY_FOR_PAPER_TRADER_REVIEW` if anything is promoted;
   `READY_FOR_MORE_EODHD_ACQUISITION` if nothing is promoted but the cross-section is below the
   150-ticker target or a near-miss exists; `NO_ALPHA_FOUND_AFTER_FULL_EODHD_SWEEP` if the full
   scoreable cross-section yields nothing; `HARD_BLOCKER_REQUIRES_USER_ACTION` on a key/entitlement/
   rate-limit/missing-data blocker; `ERROR` on an unexpected failure.

## No-look-ahead rules (point-in-time discipline)

- An earnings event is **usable only if** `reportDate <= as_of`, `reportDate >= fiscal_period_end`,
  and `eps_actual` is realized (future/unreported quarters are dropped - e.g. an AAPL row with an
  estimate but no actual and a `reportDate` after today is excluded).
- **Entry price** = the first cache close **strictly after** `reportDate`, so the signal (known at
  the announcement) always precedes the entry. No announcement-day return is used.
- **Forward returns** are measured from that entry close over 1/5/21/63 trading days; the headline
  horizon is **21 trading days** of **benchmark-excess** return.
- **Fundamentals** are joined as-of (`merge_asof`, `filing_date <= reportDate`); **macro** is joined
  as-of `reportDate` (backward). No revised or current-snapshot field is used as a historical signal
  (analyst estimates/ratings are explicitly excluded from scoring - they are not PIT-safe).

## Committed-safe artifacts

`research/output/phase8s_autonomous_eodhd_alpha_factory/` (metadata only - never a payload, never a
key): `phase8s_autonomous_eodhd_alpha_factory.json` (main report), `autonomous_run_log.csv`,
`acquisition_universe.csv`, `eodhd_acquisition_progress.csv`, `eodhd_raw_storage_manifest.csv`,
`eodhd_normalized_storage_manifest.csv`, `eodhd_family_coverage_summary.csv`,
`eodhd_ticker_coverage_panel.csv`, `earnings_events_schema_report.csv`,
`fundamentals_schema_report.csv`, `point_in_time_audit.csv`, `price_macro_sector_join_report.csv`,
`feature_catalog.csv`, `scenario_test_plan.csv`, `scenario_scoreboard.csv`, `rank_ic_report.csv`,
`decile_spread_report.csv`, `hit_rate_report.csv`, `transaction_cost_sensitivity.csv`,
`turnover_report.csv`, `sector_concentration_report.csv`, `regime_split_report.csv`,
`multiple_testing_report.csv`, `promoted_alpha_signals.csv`, `rejected_alpha_signals.csv`,
`final_research_decision.json`, `phase8t_next_plan.json`, `secret_safety_audit.csv`.

Raw + normalized EODHD payloads live ONLY under the gitignored `research/data/eodhd/{raw,normalized}/`
trees.

## Secret discipline

`EODHD_API_KEY` is read ONLY from the environment, never printed, never written to disk. Every
persisted URL is redacted; a leak scan over the committed artifacts confirms it is clean. The
low-level EODHD transport, redaction, persistence and gitignore enforcement are reused verbatim from
Phase 8-R.

## Run

```powershell
# Offline scoring on whatever EODHD data is already cached locally (no network, no key):
python research/run_phase8s_autonomous_eodhd_alpha_factory.py

# Bounded live acquisition + scoring (requires a paid EODHD_API_KEY in the env):
$env:EODHD_API_KEY = '<PAID_EODHD_KEY>'
python research/run_phase8s_autonomous_eodhd_alpha_factory.py --live --max-tickers 500 --max-requests 5000

# Test (fully offline; injected transport, no key, no network):
python -m pytest tests/test_phase8s_autonomous_eodhd_alpha_factory.py -q
```

## Constraints honored

Existing installed packages only (numpy + pandas; no scipy - IC significance uses a normal-approx
p-value). No package install. Bounded acquisition (`max_tickers` / `max_requests`, skip-existing,
stop on invalid key / entitlement / rate-limit). No raw/normalized provider data committed. No API
key printed or written. No Paper Trader, no GCP, no deploy, no broker / order / automation logic. No
commit. No push.

## Status (live run)

Live acquisition + scoring completed against the paid EODHD key on `as_of = 2026-06-26`
(exit 0). Numbers below are from
`research/output/phase8s_autonomous_eodhd_alpha_factory/phase8s_autonomous_eodhd_alpha_factory.json`.

**Terminal decision: `ALPHA_FOUND_READY_FOR_PAPER_TRADER_REVIEW`.**

- **Universe:** 301 priced tickers targeted; 269 needed acquisition (32 already cached). 268
  acquired this run in 269 requests; no early stop. EODHD coverage: earnings 300, fundamentals
  298, scoreable 299.
- **Point-in-time panel:** 29,359 raw earnings events -> 29,068 with realized EPS -> **29,032
  usable PIT events** across 299 tickers, 12 sectors, `reportDate` 2016-06-23 .. 2026-05-20.
- **Scenarios tested:** 10. Cross-sectional monthly rank IC + quintile long-short, Bonferroni +
  Benjamini-Hochberg multiple-testing across all 10.
- **Promoted (clear every gate): 2** -
  - `surprise_sector_neutral` (signal `surprise_pct`): mean IC 0.0514, IC t 2.96, mean spread
    +1.09%/mo, monthly sign hit-rate 0.642, net +0.89%/mo after 10 bps, n=27,279, 120 months.
  - `sue_standardized` (signal `sue`): mean IC 0.0366, IC t 2.19, mean spread +0.31%/mo, hit-rate
    0.583, net +0.11%/mo after 10 bps, n=26,392, 120 months.
- **Rejected: 8** - `earnings_surprise` (BH-significant IC 0.0442 but quintile spread / cost /
  robustness gate fails), `surprise_x_rates_regime`, `surprise_x_quality` (significant but
  **wrong-sign** IC -0.047), `accel_x_momentum`, `beat_streak`, `quality_filtered_surprise`,
  `rev_growth`, `regime_gated_sue`.
- **Secret discipline:** leak scan clean over 26 committed-safe files; key never printed/written;
  `research/data/eodhd/{raw,normalized}/` force-gitignored (only the in-tree `.gitignore` is
  trackable - 300 raw JSON + 29,360 normalized rows stay local).
- **Safety:** preview-only; no orders, no automation, no broker, no Paper Trader / GCP touch; not
  committed, not pushed.

**Exact next step:** review `promoted_alpha_signals.csv`, then surface `surprise_sector_neutral`
and `sue_standardized` as PREVIEW-ONLY ranking ideas in the Paper Trader daily-review cockpit
(manual review; no orders).
