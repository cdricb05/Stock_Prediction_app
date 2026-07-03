# Phase 11-A — Orthogonal Data Acquisition Decision Package (v1)

## 1. Why this phase exists

The autonomous **owned-data search was exhausted** through Phase 10-Q. Every avenue for an alpha
*stronger* than the frozen 10-D quality baseline failed out-of-sample:

| phase | avenue | decision |
|---|---|---|
| 10-L-B | two-factor reweighting / z-cap / winsorize / liquidity / sector-cap | `REJECT_REWEIGHTING_OVERFIT` |
| 10-M | incremental owned fundamentals (profitability / investment / leverage) | `BASELINE_REMAINS_CHAMPION` |
| 10-N | nonlinear transforms + quality/value/leverage interactions | `REJECT_TRANSFORM_OVERFIT` |
| 10-O | regime / conditional gating (macro / vol / dispersion / liquidity) | `REJECT_REGIME_OVERFIT` |

Phase 10-Q's honest conclusion (`PACKAGE_MODEST_BASELINE_FOR_PAPER_REVIEW`): the owned / local EODHD
fundamentals + prices, FRED macro, and benchmark regimes are **exhausted** for a stronger edge, and a
genuinely stronger alpha realistically needs **new orthogonal data**. Phase 11-A turns that conclusion
into a rigorous, evidence-based **decision package** for *which paid / trial feed to acquire first*, so
that the eventual (explicit user opt-in) spend is aimed at the highest-probability family.

**This is a research / design phase only.** It builds no panel, fits nothing, acquires no data, and
makes **no API calls / no provider probing** and requires **no key**. Vendor rows are design assumptions
from prior-phase notes, flagged `no_probe_performed=true` and `requires_user_opt_in=true`.

## 2. The incumbent that new data must beat

The 10-D two-leg quality composite (long `fcf_to_assets`, short `operating_accruals`, equal-weight,
sector-neutral, 63d) is a **real but modest / boundary** alpha: 63d IC t ≈ 2.665 (below the 3.0 strong
bar), quarterly net-25bps ≈ +0.00401, net-50bps ≈ +0.00095, turnover ≈ 0.61. A new orthogonal factor is
only worth productizing if a Phase 11-B trial shows it **beats this** out-of-sample. It is **not
oversold** — it is a small, cost-robust, sector-neutral quality tilt suitable **only** for paper review.

## 3. Candidate families and the scorecard

Six orthogonal data families are scored on an 11-axis, 1–5 weighted scorecard (5 = most favourable for
finding a strong, cost-robust, quality-orthogonal alpha at the primary **63d / quarterly** horizon). The
weights deliberately over-weight **orthogonality** (0.16), **63d horizon fit** (0.14), **economic
rationale** (0.12) and **prior-phase evidence** (0.12), then accessibility / integration / data-quality.

| family | primary | why it scores where it does |
|---|---|---|
| **Analyst estimate revisions** | ✅ | most orthogonal (forward-looking expectation *changes* vs realized *levels*); best-documented drift; near-universal S&P 500 coverage; affordable trial; named #1 new-data family in 8-Y / 10-A / 10-Q |
| Short interest / securities lending | | positioning signal, but bimonthly + lagged; price-derived short interest already **failed** the 10-A gate (best t=1.56); real-time SL fees expensive |
| Options-implied vol / skew | | very orthogonal + daily, but decays fast (best 5–21d, weak 63d); historical surfaces are among the most expensive; heavy integration |
| Insider transactions (Form 4) | | free (EDGAR), months-horizon signal, but **sparse** per name per quarter — better as an overlay than a standalone book |
| Institutional ownership / 13F | | quarterly with a **45-day lag** (worst PIT timeliness); messy reconciliation; long-only |
| News / event sentiment (PIT) | | continuous + orthogonal, but strongest at 5d, weak at 63d; owned EODHD news sentiment was already **weak** in the 8-series; look-ahead / label-leakage hazard |

The runner computes each family's weighted composite and ranks them; **analyst estimate revisions ranks
#1**, which maps to the decision `ANALYST_REVISIONS_FIRST`.

## 4. Analyst estimate revisions — required point-in-time fields

The full field list (see `analyst_revisions_required_fields.csv`), each with its as-of PIT requirement:
current fiscal year EPS estimate; next fiscal year EPS estimate; quarterly EPS estimate; revenue estimate
(if available); number of analysts; upward revisions count; downward revisions count; 7d / 30d / 60d
estimate change; consensus estimate level; estimate dispersion; recommendation changes (if available);
price target changes (if available); **point-in-time effective date** (the as-of join key,
`available_date <= entry_date`); and the announcement / revision **timestamp** (if available). The core
tradable construct is **net-revisions momentum**: `(up − down) / num_analysts` plus standardized 30d/60d
estimate change and a diffusion/breadth score.

## 5. Phase 11-B trial test plan

1. **STEP 0 (zero-cost pre-check):** inspect the **owned** EODHD raw fundamentals JSONs for an
   `Earnings::Trend` block (`epsTrend` / `epsRevisions` up/down) to confirm field shape and identifier
   mapping **before any purchase** — a current snapshot only, *not* usable as history.
2. Ingest **one** provider trial / export as a local offline file (no live API, no key).
3. Normalize to `ticker / available_date / value` CSVs (one per field).
4. Align **point-in-time** to the existing 545-name panel via an as-of join (`available_date ≤
   entry_date`); zero look-ahead.
5. Build revision-momentum factors; sector-neutral rank (within-month z, then sector demean).
6. Test at **5d, 21d, 63d** (primary 63d/quarterly); standalone, then incremental to `composite_sn`;
   then cost-adjusted long/short.
7. **Reject if no OOS stability** — walk-forward pooled OOS IC positive, frac windows positive ≥ baseline,
   and the improvement must survive the **subperiod-net25 generalization guard** (favourable in *both*
   pre-2020 and post-2020). Do **not** spin a single-period or single-sector result.

Acceptance criteria (AC1–AC10) are enumerated in `phase11b_trial_acceptance_criteria.csv`; integration
risks (PIT look-ahead, restatement/survivorship, fiscal-period mapping, identifier crosswalk, coverage,
corporate actions, entitlement expiry, variant overfit) are in `integration_risk_register.csv`.

## 6. Decision — `ANALYST_REVISIONS_FIRST`

Analyst estimate revisions is the highest-probability first acquisition: most orthogonal to the quality
baseline, strongest rationale and prior-phase evidence, best 63d fit, near-universal coverage, and
accessible via an affordable trial. Acquire it **first** (explicit user opt-in), validate under the Phase
11-B plan, and **reject** it if it fails the OOS / subperiod stability gate.

Decision enum: `ANALYST_REVISIONS_FIRST` · `SHORT_INTEREST_FIRST` · `OPTIONS_DATA_FIRST` ·
`SENTIMENT_DATA_FIRST` · `PAUSE_NO_DATA_BUDGET`.

## 7. Recommended next actions

1. On explicit user opt-in, acquire a **bounded** analyst-estimate-revisions trial/export and run Phase 11-B.
2. Before purchase (zero cost): inspect the owned EODHD `Earnings::Trend` snapshot to confirm field shape.
3. In parallel, keep the modest 10-D/10-H book in **paper** review via the 10-I tracker (human gate).

## 8. Safety / constraints

This phase makes **no live API calls**, does **no provider probing**, acquires no data, and requires no
key. It creates **no orders** and **no automation**, connects to no broker, writes nothing to the Paper
Trader, and does not deploy or touch GCP. The **owned-data search was exhausted** (10-Q) before this
package was written. The modest baseline is **not a prediction oracle** and is **not oversold**. Any
paid-feed acquisition requires **explicit user opt-in**. **Commit only the phase11a files if targeted
tests pass. No push.**

## Artifacts (`research/output/phase11a_orthogonal_data_acquisition_decision_package/`)

`phase11a_orthogonal_data_acquisition_decision_package.json` · `data_family_scorecard.csv` ·
`vendor_candidate_scorecard.csv` · `analyst_revisions_required_fields.csv` ·
`phase11b_trial_acceptance_criteria.csv` · `integration_risk_register.csv`.

## Run / test (Windows PowerShell only)

```powershell
Set-Location C:\Users\binis\Stock_Prediction_app_push
python -m py_compile research/run_phase11a_orthogonal_data_acquisition_decision_package.py
python research/run_phase11a_orthogonal_data_acquisition_decision_package.py        # offline; no key
python -m pytest tests/test_phase11a_orthogonal_data_acquisition_decision_package.py -q
```

## Constraints honored

Offline (no network / key / provider probe); **no API calls**; **no provider probing**; owned-data search
already exhausted (10-Q); **no new data acquired**; **no Paper Trader writes; no orders; no automation; no
broker; no live trading; no deploy; no GCP**; no package install; targeted tests only; output is research
metadata only. **Commit only phase11a files if tests pass. No push.**
