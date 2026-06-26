# Phase 8-N — Controlled FMP Critical-Data Backfill and Provider-Expanded Signal Confirmation

**Status:** built, tested (offline, 53/53). No commit. No push. Preview/research only.
**Files:** `research/run_phase8n_fmp_critical_data_backfill_signal_expansion.py`,
`tests/test_phase8n_fmp_critical_data_backfill_signal_expansion.py`, this doc.
**Output dir:** `research/output/phase8n_fmp_critical_data_backfill_signal_expansion/` (28 committed-safe artifacts).
**Paid-data dir:** `research/data/fmp/{raw,normalized}/` (gitignored — never committed).

## 0. Controller patch — early-stop bug fixed (the reason this doc is v1-patched)

The first live run stopped the whole batch after the `earnings_surprises` family hit three
consecutive `402 SUBSCRIPTION_BLOCKED` responses (ABT/ACN, then ADI/ADP/AMAT). That collapsed a
single-family entitlement gap into a full-batch halt: the other five critical families were
never tested across the 25-ticker universe (they showed only their 3 cached AAPL/MSFT/NVDA
cells). **Fixed:** a `402/403` subscription block is now recorded and the controller **continues**
to the next ticker and the next endpoint family. The batch stops globally **only** for: repeated
`401` invalid-auth (bad key), true `429` rate-limit exhaustion *after* bounded retry/backoff, the
`max_requests` budget being spent, or a manual safety stop. New CLI: `--continue-on-subscription-block`
(default **true**), `--no-continue-on-subscription-block` (legacy stop), `--endpoint-families`
(comma-separated subset of the six criticals), `--max-requests` (default 150). `401` and `402/403`
are now classified distinctly (`AUTH_ERROR` vs `SUBSCRIPTION_BLOCKED`).

---

## 1. Why this phase exists

Phase 8-M confirmed — live, with the user's `FMP_API_KEY` — that the **current FMP plan is
sufficient** for the six critical market-data families. `earnings_surprises`,
`earnings_calendar`, `analyst_estimates`, `analyst_recommendations`, `analyst_price_targets`
and `ratings_grades_consensus` all returned HTTP 200 / `ACCESS_VERIFIED` for AAPL/MSFT/NVDA.
`key_metrics_quarterly` and `ratios_quarterly` returned HTTP 402 (subscription block) — **not
a blocker**, because the statements are accessible and ratios can be computed internally.

The alpha blocker therefore moves from *"provider access unknown"* to *"controlled,
point-in-time-safe provider-backed data expansion and signal validation"*. Phase 8-N runs a
**bounded** FMP backfill for the verified critical families, normalizes the paid data
point-in-time, builds committed-safe coverage + signal-ready manifests, and tests — **without
fabricating any result** — whether the expanded earnings/analyst data lets the promising
signal families be scored.

## 2. What it does (two modes)

* **Offline normalize (default, no network).** Reuses whatever raw paid data already exists
  under the gitignored cache (the Phase 8-M live run already collected the six critical
  families for AAPL/MSFT/NVDA), normalizes it, and builds every coverage/manifest/scoreboard
  artifact + the decision. No key, no request.
* **Bounded live backfill (`--live`, requires `FMP_API_KEY`).** Runs the skip-existing,
  critical-first backfill across the resolved universe first, then the same offline pipeline.

The current offline run (no key in this shell) reports: universe resolved from the
already-collected cache, **0 requests**, all six critical families `CACHED`/`ENTITLED` with real
data — `earnings_surprises` for 5 tickers (the 8-M AAPL/MSFT/NVDA plus the live-collected ABBV/ADBE),
the other five for 3 tickers — coverage 3–5/`MIN_TICKERS_TO_SCORE`(20) → all six signals
`INSUFFICIENT_COVERAGE` → decision **`READY_FOR_NEXT_FMP_BATCH`**, no `missing/blocked` families,
`provider_upgrade_decision = NO_UPGRADE_NEEDED`. That is the honest agent-shell state: the cached
data is entitled but far below the breadth a cross-sectional cohort signal needs. The agent shell
has **no key**, so it observes no live `402`s — the real entitlement picture comes from the user's
`--live` run, which now tests all six families across the full 25-ticker batch.

## 3. Bounded backfill limits (hard ceilings — never exceeded)

| Limit | Default | Ceiling |
|---|---|---|
| tickers | 25 | 25 |
| endpoint families | 6 (the criticals) | 10 |
| requests (`--max-requests`) | 150 | 150 |
| skip-existing | on | — |
| stop after consecutive **401** invalid-auth | 3 | — |
| stop after consecutive **429** (post bounded retry) | 3 | — |
| per-cell **429** retry/backoff | 2 | — |
| stop on **402/403** | **never** (default) / 3 (legacy `--no-continue-on-subscription-block`) | — |

The controller is the Phase 8-M fix carried forward: critical earnings/analyst families are
always at the FRONT and a generic cap can only trim the **non-critical tail** — it can never
drop a critical family (verified at caps 0/1/3/5). `key_metrics_quarterly` and
`ratios_quarterly` are **excluded by default** (the 402 do-not-spend list) and are never
requested unless `--include-blocked` is passed.

**Early-stop contract (the 8-N fix).** A `402/403` subscription block proves the key is valid
but the plan is not entitled to that endpoint, so it is recorded (error report + entitlement
matrix + block-pattern report) and the controller **continues** to the next ticker and the next
family. A `401` is a different class — the key itself is bad — and repeated `401`s stop the
batch. A `429` is retried/backed-off per cell; only genuine exhaustion stops the batch. Each
backfilled cell prints one progress line: `family · ticker · result · cumulative-requests ·
running-family-coverage · running-blocked-count`. `--endpoint-families a,b` restricts the run to
a subset of the six criticals (default: all six).

## 4. Skip-existing protects the paid cache

A raw file that already exists (and parses to non-empty JSON) is **reused read-only** and
**never overwritten**, and costs **no request**. A fetch (and a raw write) happens only for an
*absent* cell, and only when a transport is injected (tests) or `--live` is set **and** a key
is present. This is the guard that prevents re-corrupting already-collected licensed data:
across every offline + live-simulated run the md5 of the cached critical payloads is unchanged.

## 5. Universe priority (from the brief)

1. tickers in S8L/S8K promising candidates (if a ticker column exists);
2. the research candidate registry (Phase 8-I);
3. the current collected universe (tickers already profiled in the raw cache) — the Phase 5-C
   fallback in practice;
4. smoke fallback `AAPL, MSFT, NVDA, AMZN, JPM`.

AAPL/MSFT/NVDA (the 8-M verified tickers) are pinned to the front so their cached critical
data is always used first. The list is capped to `max_tickers` (≤ 25).

## 6. The six signal hypotheses and HONEST scoring

| signal_id | hypothesis | required FMP family | conditioning cohort (local) |
|---|---|---|---|
| S8N-EARNSURP-RATES | positive earnings surprise × rates sensitivity | earnings_surprises | rates_sensitivity |
| S8N-EARNSURP-SECTOR | positive earnings surprise × sector leadership | earnings_surprises | sector_leadership |
| S8N-ESTREV-RATES | analyst estimate revision × sensitivity | analyst_estimates | rates_sensitivity |
| S8N-RECCHG-SECTOR | analyst recommendation change × sector leadership | analyst_recommendations | sector_leadership |
| S8N-PTREV-SENS | price-target revision × sensitivity cohort | analyst_price_targets | sensitivity_cohort |
| S8N-GRADES-SENS | ratings/grades consensus × sensitivity cohort | ratings_grades_consensus | sensitivity_cohort |

**No fabricated alpha.** Coverage gates the verdict, and a real alpha verdict
(`CONFIRMED_ALPHA` / `PROMISING_ALPHA` / `REJECTED`) is produced **only** when a real scoring
result is supplied by a wired scoring harness (`injected_scores`). Absent that, a signal is at
most `COVERAGE_READY_SCORING_DEFERRED`, or `INSUFFICIENT_COVERAGE` (too few tickers) /
`PROVIDER_REQUIRED` (the required family is subscription-blocked). This run wires no scorer and
has 3-ticker coverage, so every signal is honestly `INSUFFICIENT_COVERAGE` and nothing is
promoted.

## 7. The research-director decisions (seven original + three patch outcomes)

Original seven: `PROVIDER_EXPANDED_CONFIRMED_ALPHA_FOUND` · `PROVIDER_EXPANDED_PROMISING_ALPHA_FOUND` ·
`READY_FOR_NEXT_FMP_BATCH` · `NEEDS_MORE_FMP_COVERAGE` · `BLOCKED_MISSING_FMP_KEY` ·
`PROVIDER_EXPANSION_REJECTED` · `ERROR`.

Three added by the 8-N controller patch, so a partial/blocked plan is reported honestly instead
of collapsing into a premature rejection:

* **`READY_FOR_PROVIDER_EXPANDED_SIGNAL_SCORING`** — all six critical families reached the
  20-ticker minimum; wire the scorer / run the 8-O walk-forward.
* **`FMP_PLAN_COVERAGE_INSUFFICIENT`** — most critical families are broadly `402/403`-blocked on
  the current plan; see the cheaper-provider + upgrade reports (do **not** blanket-upgrade FMP).
* **`MIXED_FMP_COVERAGE_NEEDS_ALTERNATIVE_EARNINGS_SOURCE`** — `earnings_*` is broadly blocked but
  the analyst families work; activate the recommended (cheapest) earnings alternative.

Decision logic: a family with ≥ 20 covered tickers is `READY_TO_SCORE`; < 20 with budget left →
next batch; most criticals `402`-blocked → plan insufficient; earnings blocked but analyst works
→ alternative earnings source; all six covered → ready to score. Confirmed/promising still
require a real score (no fabricated alpha).

## 8. The 28 committed-safe artifacts

The 24 from the original build **plus four** added by the patch:
`fmp_family_entitlement_matrix.csv` (per-family attempted/collected/cached/blocked + entitlement
+ ready/broadly-blocked), `fmp_subscription_block_pattern_report.csv` (block fraction + pattern:
NONE/SPORADIC/SYSTEMATIC/TOTAL + interpretation), `fmp_provider_upgrade_decision.csv` (per-family
action + overall: `NO_UPGRADE_NEEDED` / `UPGRADE_NOT_RECOMMENDED_USE_ALTERNATIVE`),
`fmp_cheaper_provider_alternative_report.csv` (cheapest non-FMP source per missing family — Alpha
Vantage for earnings, Finnhub for analyst — Ultimate never recommended). The original 24:

`phase8n_fmp_critical_data_backfill_signal_expansion.json`, `fmp_key_detection_report.csv`,
`fmp_backfill_request_plan.csv`, `fmp_backfill_progress_report.csv`,
`fmp_endpoint_status_report.csv`, `fmp_ticker_coverage_by_family.csv`,
`fmp_raw_storage_manifest.csv`, `fmp_normalized_storage_manifest.csv`,
`fmp_secret_safety_audit.csv`, `fmp_error_report.csv`, `fmp_signal_ready_coverage_report.csv`,
`expanded_earnings_event_manifest.csv`, `expanded_analyst_event_manifest.csv`,
`provider_expanded_signal_scoreboard.csv`, `confirmed_alpha_signals.csv`,
`promising_alpha_signals.csv`, `provider_required_signals.csv`, `rejected_alpha_signals.csv`,
`trade_idea_candidate_registry.csv`, `best_trade_idea_candidates.csv`,
`validation_skeptic_report.csv`, `multiple_testing_report.csv`,
`research_director_decision.json`, `phase8o_next_plan.json`.

Every one of these is **metadata only** — row counts, date ranges, field NAMES, redacted URLs,
file paths, statuses, decisions. The licensed values (raw payloads + normalized point-in-time
CSVs) live ONLY under the gitignored `research/data/fmp/{raw,normalized}/` tree. The earnings /
analyst manifests carry event counts and date ranges and a *has-field* boolean — never the
actual EPS/estimate/target numbers.

## 9. Secret discipline

`FMP_API_KEY` is read only from the environment, never printed, never written to disk.
Committed URLs strip the key parameter (placeholder token only); no committed artifact contains
the `apikey=` marker (a leak scan over every written artifact backs
`fmp_secret_safety_audit.csv`). Raw + normalized paid payloads are gitignored; skip-existing
guarantees the cache is reused read-only and never overwritten.

## 10. How to run

```powershell
# Default - OFFLINE: normalize existing cache + score coverage, no network:
python research/run_phase8n_fmp_critical_data_backfill_signal_expansion.py

# Bounded live backfill (FMP_API_KEY present in this session only) - a 402 no longer halts it:
python research/run_phase8n_fmp_critical_data_backfill_signal_expansion.py --live --max-tickers 25

# Restrict to a subset of the six critical families:
python research/run_phase8n_fmp_critical_data_backfill_signal_expansion.py --live \
    --endpoint-families earnings_surprises,analyst_estimates --max-requests 150

# Legacy behaviour (stop after repeated 402s) - opt-in only:
python research/run_phase8n_fmp_critical_data_backfill_signal_expansion.py --live \
    --no-continue-on-subscription-block

# Tests (offline; no key, no network):
python -m pytest tests/test_phase8n_fmp_critical_data_backfill_signal_expansion.py -q
```

## 11. Constraints honored

Existing installed packages only (stdlib). No package install. No full S&P 500 backfill —
bounded batches only. key_metrics/ratios skipped by default after the 402 block. No Paper
Trader. No GCP. No deploy. No broker / order / automation logic. No live trading signals. No
weights optimized, no signs flipped, no fabricated results. No failed experiments hidden. No
commit. No push.
