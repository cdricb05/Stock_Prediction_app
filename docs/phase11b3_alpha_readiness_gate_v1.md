# Phase 11-B3 — Alpha-Readiness Gate For Newly Loaded Data (v1)

## 1. Why this phase exists

The queue's readiness gate: for every candidate orthogonal family it decides whether the data is good
enough to support (or has already supported) an incremental-alpha test versus `composite_sn`. It is an
offline **synthesis** of the frozen prior-phase JSONs — the Phase 11-B0 local inventory and the Phase 11-C
alpha-test outcome — not a new backtest. It makes **no api calls**, builds no panel, and creates **no
orders** and **no automation**.

## 2. The gate

| family | classification (11-B0) | tested in 11-C? | gate status |
|---|---|:--:|---|
| insider_sentiment_mspr | BACKTESTABLE | ✓ | **READY_TESTED_NO_ALPHA** |
| short_interest_days_to_cover | (rejected 10-A) | ✓ (change field) | tested → no alpha |
| analyst_recommendation_change | SHALLOW_SNAPSHOT | — | NOT_BACKTESTABLE |
| analyst_estimate_revision_av | TOO_SPARSE (23) | — | **PAID_GATED** |
| analyst_estimates_fmp | TOO_SPARSE (8) | — | **PAID_GATED** |

## 3. Decision — `NEEDS_PAID_DATA`

Every locally-backtestable family has been tested and yields **no incremental alpha** over `composite_sn`
(insider sentiment was weak / wrong-signed; short-interest change was cost-killed — see Phase 11-C), and
the highest-priority family (**analyst estimate revisions**) is only sparse locally and **paid-gated** at
universe depth. No locally-ready, untested family remains. A stronger alpha therefore needs **new paid
data** → Phase 11-B4. The modest `composite_sn` baseline **remains champion**.

Decision enum: `NEW_DATA_READY_FOR_ALPHA_TEST` · `NEW_DATA_PARTIAL_NEEDS_REPAIR` ·
`NEW_DATA_NOT_BACKTESTABLE` · `NEEDS_PAID_DATA`.

## 4. Artifacts (`research/output/phase11b3_alpha_readiness_gate/`)

`phase11b3_alpha_readiness_gate.json` · `readiness_gate.csv`.

## 5. Safety / constraints

Offline (reads only frozen local prior-phase JSONs). **No api calls**, no key, no purchase, no Paper
Trader writes, **no orders**, **no automation**, no broker, no deploy, no GCP. Commit only the phase11b3
files if tests pass. No push.

## Run / test (Windows PowerShell only)

```powershell
Set-Location C:\Users\binis\Stock_Prediction_app_push
python research/run_phase11b3_alpha_readiness_gate.py
python -m pytest tests/test_phase11b3_alpha_readiness_gate.py -q
```
