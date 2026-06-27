# Phase 8-Y - Orthogonal Data Family Acquisition + Strong Alpha Re-run

Status: implemented + tested (12/12 targeted tests, fully offline) and **executed live** against the
real environment. Runner: `research/run_phase8y_orthogonal_data_family_acquisition.py`. The decision +
numbers are in the Status block at the bottom and in
`research/output/phase8y_orthogonal_data_family_acquisition/`. Nothing committed, nothing pushed.

## Why this phase exists

Phase 8-X proved the EODHD earnings / fundamentals / price families are EXHAUSTED for broad strong
alpha on the expanded 545-ticker universe (best broad t=2.24; 0 strong, 0 even constrained; decision
`NEEDS_NEW_DATA_FAMILY`). The binding constraint is the INFORMATION CONTENT of those families, not
scenario design or model class. The only honest next move is a genuinely NEW data family that is
ORTHOGONAL to realized earnings, accounting fundamentals, and historical prices.

Phase 8-Y is the autonomous data-acquisition agent that confirms the 8-X mandate, audits which
provider endpoints are reachable with the keys actually present, classifies entitlement per
(provider, family), selects the highest-value accessible orthogonal family in strict priority order,
acquires a BOUNDED batch, normalizes it POINT-IN-TIME, builds NEW features, and re-runs the Phase-8-X
strong-alpha discovery on the augmented dataset. It never promotes a weak/constrained signal.

## Priority order (highest value first)

1. analyst estimate revisions
2. analyst recommendation changes
3. price target revisions
4. short interest / days-to-cover
5. options implied volatility / skew
6. news / social sentiment

Each family carries a cheapest-first provider list with a representative endpoint, and the NEW
point-in-time feature its normalizer produces - none of which duplicate the EODHD
earnings/fundamentals/price features.

## Reuse

The provider-probe, entitlement-classification, URL-redaction, force-gitignore, and leak-scan
machinery is reused from **Phase 8-O** (`run_phase8o_cheapest_provider_selection`). The strong-alpha
gate, 3-way classify, scenario/model evaluation, and walk-forward factor-ensemble layer are reused
**verbatim** from **Phase 8-X** (`run_phase8x_autonomous_strong_alpha_discovery`), which in turn
reuses the 8-W expanded event table + the 8-T/8-S scoring core. The augmented re-run is the IDENTICAL
gate applied to the same expanded `ev` plus the new orthogonal feature column(s).

## Entitlement classification (per provider/family)

`ACCESS_VERIFIED` | `ENTITLEMENT_BLOCKED` (401/402/403) | `RATE_LIMITED` (429 / provider info
envelope) | `MISSING_KEY` (no key in env - explicitly classified, never misreported as "invalid") |
`ENDPOINT_NOT_FOUND` (404 / no provider mapped) | `PARSE_FAILED` (non-JSON). One blocked / missing /
rate-limited provider NEVER stops the phase - the audit continues and selection skips to the next
accessible family in priority order.

## Point-in-time normalization

Each normalized record carries an `available_date` = the date the datum was first publicly known. A
feature is as-of joined onto an event only if `available_date <= entry_date` (no lookahead); records
dated after the `as_of`, or with no availability date, are DROPPED and logged in the PIT audit. The
feature is then attached to the expanded event table by (ticker, entry_date), taking the most recent
eligible record.

## Secret discipline (hard rules)

Provider keys are read ONLY from their env vars, for PRESENCE and to build a transient request URL;
their value is NEVER printed and NEVER written to disk. Every persisted/committed URL strips the
secret query parameter entirely and appends `<API_KEY_REDACTED>`. Raw and normalized provider
payloads live ONLY under the force-gitignored `research/data/<provider>/{raw,normalized}/` trees
(a `.gitignore` that ignores everything except itself is written before any payload). Committed
artifacts carry only entitlement / coverage / decision METADATA. A leak scan over every committed
file confirms no key value and no key query parameter survive.

## Terminal decisions

`STRONG_ALPHA_FOUND` (a new-family candidate clears the full Phase-8-X strong gate) |
`NEW_DATA_FAMILY_EXHAUSTED` (acquired + normalized + re-run with sufficient coverage, no strong alpha)
| `NEEDS_PROVIDER_PURCHASE` (no accessible orthogonal family - names exactly which family/provider to
buy and why) | `READY_FOR_NEXT_ORTHOGONAL_BATCH` (accessible + partially acquired, coverage below the
floor needed to judge) | `HARD_BLOCKER_REQUIRES_USER_ACTION` (8-X mandate absent / not
`NEEDS_NEW_DATA_FAMILY`) | `ERROR`.

## Required artifacts (16, committed-safe metadata only)

`research/output/phase8y_orthogonal_data_family_acquisition/`:
`phase8y_orthogonal_data_family_acquisition.json`, `provider_entitlement_matrix.csv`,
`selected_data_family_decision.csv`, `acquisition_universe.csv`, `acquisition_progress.csv`,
`raw_storage_manifest.csv`, `normalized_storage_manifest.csv`,
`point_in_time_normalization_audit.csv`, `new_feature_catalog.csv`,
`augmented_scenario_scoreboard.csv`, `augmented_model_candidate_scoreboard.csv`,
`strong_alpha_candidates.csv`, `rejected_augmented_hypotheses.csv`,
`data_family_exhaustion_report.csv`, `phase8z_next_plan.json`, `secret_safety_audit.csv`. No raw or
normalized provider data is written to the committed tree.

## Run

```powershell
# Probes ONLY providers whose key is present; bounded acquire + re-run if a family is accessible:
python research/run_phase8y_orthogonal_data_family_acquisition.py
# Offline decision only (no network; classify key presence + emit the purchase/next-batch decision):
python research/run_phase8y_orthogonal_data_family_acquisition.py --offline
# Test (fully offline; injected transport + synthetic normalized data; no key, no network):
python -m pytest tests/test_phase8y_orthogonal_data_family_acquisition.py -q
```

## Constraints honored

Existing installed packages only (stdlib + numpy + pandas). No package install. No Paper Trader, no
GCP, no deploy, no broker / order / automation logic. No full Phase-8 regression - targeted tests
only. Keys never printed or written; raw + normalized payloads force-gitignored. The constrained 8-W
signal is NOT productized. No commit. No push.

## EODHD news/social-sentiment normalizer + `--normalize-existing-only`

The EODHD `/sentiments` payload is a top-level dict keyed by provider symbol whose value is a list of
daily records: `{"A.US": [{"date": "2026-06-26", "count": 1, "normalized": 0.999}, ...]}`. The
generic extractor only handled `records`/`feed`/`data` wrappers and bare lists, so it produced ZERO
PIT records from non-empty raw payloads - a parser defect, not data exhaustion. `normalize_pit` now
flattens this shape for the `news_social_sentiment` family into the richer PIT-safe schema
`ticker, provider_symbol, available_date, news_sentiment, news_count, source_family, source_provider`
(`available_date <- date`, `news_sentiment <- normalized`, `news_count <- count`; the raw `A.US` key
is preserved as `provider_symbol` and its exchange suffix stripped for `ticker`). PIT safety is
enforced per record: missing dates and dates after the as-of are dropped and logged in the audit.

`--normalize-existing-only` re-normalizes the CACHED raw payloads from disk (auto-locating the cached
provider for the chosen family), attaches the feature, and re-runs the augmented strong-alpha test -
making NO provider API call and requiring no key. The decision logic was hardened so that
**raw payloads present but zero normalized rows** classifies as `PARSER_OR_NORMALIZATION_BLOCKER`
(never `READY_FOR_NEXT_ORTHOGONAL_BATCH`); only after the normalizer yields rows is a non-strong
result reported honestly as `NEW_DATA_FAMILY_EXHAUSTED` (sufficient coverage) or
`READY_FOR_NEXT_ORTHOGONAL_BATCH` (coverage below the floor).

## Status (cached re-normalize run)

Executed on `as_of = 2026-06-26` with `--family news_social_sentiment --normalize-existing-only
--max-tickers 0 --max-requests 0` against the 500 cached EODHD sentiment payloads (no API call, no
key).

**Terminal decision: `READY_FOR_NEXT_ORTHOGONAL_BATCH`** (parser bug FIXED; sentiment history too
shallow to overlap the historical earnings events).

- **8-X precondition:** met (`NEEDS_NEW_DATA_FAMILY`).
- **Parser fix:** the 500 cached EODHD payloads now normalize to **9,703 PIT records / 498 tickers**
  (previously 0). `parser_bug_fixed = true`; `parser_or_normalization_blocker = false`.
- **Feature coverage: 0 events.** The cached `/sentiments` window spans only **2026-05-28 ->
  2026-06-26** (~30 days). The PIT as-of join requires `available_date <= entry_date`, but that
  recent sentiment lands AFTER essentially every scoreable earnings entry_date in the panel, so no
  event receives a sentiment value. Ticker keys match cleanly (`A`, `AAPL`, `ABBV`, ...) - this is a
  temporal-depth gap, not a join-key or parser bug.
- **Augmented re-run:** 4 scenarios+models scored; **0 strong, 0 constrained**; best candidate
  `orthogonal_blend__ic_weighted` ic_t -0.28 (rejected). No weak/constrained signal promoted.
- **Decision:** coverage (0) is below the 200-event floor, so the honest call is
  `READY_FOR_NEXT_ORTHOGONAL_BATCH` - acquire a DEEPER sentiment history (EODHD `from`/`to`) that
  overlaps the earnings-event panel before the family can actually be judged.
- **Safety:** preview-only; no orders, automation, broker, Paper Trader / GCP touch; no network call
  (transport disabled in normalize-existing mode); keys never printed/written; raw + normalized
  payload trees force-gitignored (`git check-ignore` confirms `eodhd/.gitignore` masks both the raw
  `news_social_sentiment/*.json` and the normalized `news_sentiment.csv`). Leak scan clean over the
  16 committed-safe files. Not committed, not pushed.

**Exact next step:**
Acquire a deeper sentiment history so it overlaps the historical earnings events, then re-judge:
`python research/run_phase8y_orthogonal_data_family_acquisition.py --family news_social_sentiment --max-tickers 500`
(with a deeper EODHD `from` date), then re-normalize from cache:
`python -B -m research.run_phase8y_orthogonal_data_family_acquisition --family news_social_sentiment --normalize-existing-only --max-tickers 0 --max-requests 0`.
