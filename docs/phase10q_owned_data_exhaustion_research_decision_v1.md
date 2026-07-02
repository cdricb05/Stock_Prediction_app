# Phase 10-Q — Owned Data Exhaustion And Research Decision (v1)

## 1. Why this phase exists

The autonomous owned-data alpha-improvement queue is complete, and every avenue for an alpha **stronger**
than the frozen 10-D quality baseline has failed out-of-sample:

| phase | avenue | decision |
|---|---|---|
| 10-L-B | two-factor reweighting / z-cap / winsorize / liquidity / sector-cap | `REJECT_REWEIGHTING_OVERFIT` |
| 10-M | incremental owned fundamental factors (profitability / investment / leverage) | `BASELINE_REMAINS_CHAMPION` |
| 10-N | nonlinear transforms + quality/value/leverage interactions | `REJECT_TRANSFORM_OVERFIT` |
| 10-O | regime / conditional gating (macro / vol / dispersion / liquidity) | `REJECT_REGIME_OVERFIT` |

No candidate beat the baseline on honest, cost-robust, era-stable evidence. Phase 10-P (paper-rule
packaging of a **new** winner) is therefore skipped — there is no new winner. Phase 10-Q is a **synthesis**
phase: it reads the frozen owned prior-phase decision JSONs (10-D baseline + 10-L-B / 10-M / 10-N / 10-O)
and aggregates them into one honest final decision. It builds no panel, fits nothing, and makes no network
call.

## 2. The honest picture

The 10-D two-leg quality composite (long `fcf_to_assets`, short `operating_accruals`, equal-weight,
sector-neutral, 63d) is a **real but modest / boundary** alpha: 63d IC t ≈ 2.665 (below the 3.0 strong
bar), quarterly net-25bps ≈ +0.00401, net-50bps ≈ +0.00095, turnover ≈ 0.61, with the `operating_accruals`
short leg carrying most of the robustness. It already passed the strict 10-D gate
(`CONFIRMED_READY_FOR_PAPER_REVIEW`) and was already packaged into a paper-only review + position-tracker
line (10-E → 10-I). **Nothing in 10-L-B / 10-M / 10-N / 10-O improved on it.**

Two candidates *looked* strong in-sample and were correctly rejected by an out-of-sample /
subperiod-generalisation guard, both being **post-2020 relics**:
- 10-N `altcomp_rank` (rank-transformed legs): full-sample IC t 3.22 but the net25 gain is entirely
  pre-2020… no — entirely a **pre-2020** artifact that reverses post-2020;
- 10-O regime lifts (`market_liquidity`, `market_vol`, `curve_2s10s`): 2.3–2.7× full-sample net25 but
  entirely **post-2020**, worse than the always-on baseline pre-2020.

These are the value of the exercise: skepticism kept two plausible-but-fragile "improvements" from being
productized.

## 3. Owned-data exhaustion

Exhausted for a stronger edge: the EODHD fundamentals quality/profitability/investment/leverage families
(10-B → 10-O), their transforms and interactions (10-N), and the owned FRED-macro + benchmark regimes
(10-O). The single remaining **owned** avenue is a PIT-reconstructed **value leg** (earnings_yield /
book_to_market from owned prices + equity) — but it is **low-priority**: the 10-N value *interactions* were
already wrong-signed / insignificant at 63d in this universe, so a value leg is unlikely to help and would
need a careful PIT market-cap join.

## 4. What new data would be required to go further

A genuinely stronger, quality-orthogonal alpha most likely needs **new orthogonal data** that is **not
owned** and requires **paid feeds** (explicit user opt-in; out of scope for this offline run):

1. **Analyst estimate-revision history** — the highest-priority orthogonal family (revisions momentum /
   post-earnings drift);
2. **Short interest / days-to-cover** (full universe, historical) — short-side crowding;
3. **Options-implied vol / skew** — forward-looking risk + informed flow;
4. **Richer sentiment / alternative data**.

## 5. Decision — `PACKAGE_MODEST_BASELINE_FOR_PAPER_REVIEW`

The modest / boundary 10-D quality composite is the **only** usable owned-data alpha; reweighting,
incremental fundamentals, transforms/interactions, and regimes all failed to beat it out-of-sample. Carry
the modest baseline forward for **paper review** (it already passed the strict 10-D gate and is packaged in
10-E → 10-I as sector-neutral 25L/25S rules with a paper position tracker). Do **not** keep mining the
exhausted owned fundamentals + regimes. A genuinely stronger alpha needs new orthogonal data (paid; user
opt-in).

Decision enum: `PACKAGE_MODEST_BASELINE_FOR_PAPER_REVIEW` · `PAUSE_ALPHA_RESEARCH_NEEDS_NEW_DATA` ·
`NEEDS_DATA_REFRESH_BEFORE_DECISION`.

## 6. Recommended next actions

1. **Paper-review** the modest 10-D/10-H sector-neutral 25L/25S book with the existing 10-I tracker (human
   gate; paper-only; no orders; no automation).
2. If a stronger alpha is required, obtain **one** orthogonal paid family (analyst estimate-revisions
   first) with explicit user opt-in, then re-run the 8-X / 10-B strong-alpha gate.
3. Optionally, a low-priority owned-data **value-leg reconstruction** before any purchase — expected weak
   per 10-N.

## 7. Safety / constraints

This phase makes **no live API calls**, creates **no orders** and **no automation**, connects to no
broker, writes nothing to the Paper Trader, and does not deploy. It reads only owned prior-phase output
JSONs. The modest baseline is **not a prediction oracle** and is **not oversold** — it is a small,
cost-robust, sector-neutral quality tilt suitable **only** for paper review. **No commit. No push** beyond
the local research checkpoint.

## Artifacts (`research/output/phase10q_owned_data_exhaustion_research_decision/`)

`phase10q_owned_data_exhaustion_research_decision.json` · `research_avenue_ledger.csv` ·
`baseline_status.csv` · `owned_data_exhaustion.csv` · `new_data_requirements.csv`.

## Run / test (Windows PowerShell only)

```powershell
Set-Location C:\Users\binis\Stock_Prediction_app_push
python -m py_compile research/run_phase10q_owned_data_exhaustion_research_decision.py
python research/run_phase10q_owned_data_exhaustion_research_decision.py          # offline; no key
python -m pytest tests/test_phase10q_owned_data_exhaustion_research_decision.py -q
```

## Constraints honored

Offline (no network / key / provider probe); **owned/local data only** (reads prior-phase JSONs); **no new
data**; **no Paper Trader writes; no orders; no automation; no broker; no live trading; no deploy; no GCP**;
no package install; targeted tests only; output is research metadata only. **No commit. No push.**
