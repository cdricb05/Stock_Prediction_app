# Phase 11-B4 — Paid-Data Acquisition Shopping Cart (v1)

## 1. Why this phase exists

Phases 11-B0 → 11-C established that the free / currently-entitled orthogonal data on disk does **not**
beat the modest `composite_sn` baseline, and Phase 11-B3 gated the result as `NEEDS_PAID_DATA`. This phase
is the **terminal** deliverable of the acquisition queue: a concrete, ranked purchase / trial list for the
highest-priority family — **analyst estimate revisions** (Phase 11-A #1) — with exact fields, scope, cost,
delivery, signup steps, and post-trial rejection criteria. It performs **no payment**, **no signup**, **no
provider probing**, and **no api calls**, and creates **no orders** and **no automation**. Every row
requires **explicit user opt-in**.

## 2. The cart (ranked)

| rank | tier | provider | cost | owned key? | why |
|---|---|---|---|:--:|---|
| **1** | must-try-first | **FMP Premium** (analyst estimates + history) | ~$22-70/mo | ✓ FMP_API_KEY | owned key → lowest-friction first screen |
| 2 | second choice | **Nasdaq Data Link — Zacks** (ZACKS/EE + ZACKS/ER) | ~$1-3k/yr (quote) | ✓ NASDAQ key | best affordable **PIT** revision history |
| 2 | second choice | **Intrinio — Zacks** estimate trends | ~$1-3k/yr | — | alt PIT source (clean REST) |
| 3 | enterprise / too expensive | LSEG **I/B/E/S** | >$10k/yr | — | gold-standard but contract + build ≫ value of one screen |
| 4 | not now | Options IV (ORATS/OptionMetrics) | $100-600/mo+ | — | wrong horizon (decays <21d), most expensive |
| 4 | not now | Short interest / 13F / news | varies | — | SI rejected (10-A/11-C); 13F 45-day lag; news weak (8-series) |

**Recommended first action:** run a **bounded FMP Premium** trial (rank 1) because the FMP key is already
present, so only a tier upgrade is needed to pull universe-wide analyst estimates + history and run the
first sector-neutral revision-momentum screen. If the FMP screen is promising but its point-in-time /
revision-timestamp fidelity proves too weak, escalate to **Nasdaq Data Link Zacks** (rank 2) for
PIT-grade revision history.

## 3. Required point-in-time fields (16)

`eps_estimate_cfy`, `eps_estimate_nfy`, `eps_estimate_quarter`, `revenue_estimate`, `num_analysts`,
`up_revisions_count`, `down_revisions_count`, `estimate_change_7d`, `estimate_change_30d`,
`estimate_change_60d`, `consensus_estimate_level`, `estimate_dispersion`, `recommendation_changes`,
`price_target_changes`, **`pit_effective_date`** (the as-of join key), `revision_timestamp`. The tradable
construct is **net-revisions momentum** `(up − down)/num_analysts` + standardized 30/60d estimate change +
breadth.

**Minimum scope:** ~545 names (S&P 500 + expanded), monthly (or on-revision) snapshots, **≥ 10 yr**
(2010-2026) so the pre/post-2020 subperiod guard can run. Expected volume ~0.65-15M rows, < 6 GB.

## 4. Post-trial rejection criteria

Carried from Phase 11-A (AC1–AC10) + the Phase 11-C strict relative beat test. Reject the paid factor if:
no usable PIT date (RC1); < 90% coverage / < 24-month depth (RC2); no positive 63d IC (RC3); does not beat
`composite_sn` net-25bps incrementally (RC4); turnover cost-killed (RC5); OOS not positive (RC6); the
net-25bps improvement does **not** survive in **both** pre- and post-2020 (RC7 — the subperiod guard); or
it is a post-hoc / single-quarter / single-sector / concentration-worsening winner (RC8).

## 5. Decision — `ACTION_REQUIRED_ANALYST_REVISIONS_TRIAL`

The first bounded paid trial should be **analyst estimate revisions**, starting with the FMP Premium
upgrade (owned key) and escalating to Nasdaq Data Link / Intrinio Zacks for PIT depth. This requires
**explicit user opt-in** — no payment or signup was performed. Until then, the modest `composite_sn`
baseline (IC t 2.665, net-25bps +0.00401) remains the paper-review candidate.

Decision enum: `ACTION_REQUIRED_ANALYST_REVISIONS_TRIAL` · `ACTION_REQUIRED_SHORT_INTEREST_TRIAL` ·
`ACTION_REQUIRED_OPTIONS_TRIAL` · `ACTION_REQUIRED_MULTI_VENDOR_QUOTES` · `NO_PAID_DATA_RECOMMENDED`.

## 6. Artifacts (`research/output/phase11b4_paid_data_shopping_cart/`)

`phase11b4_paid_data_shopping_cart.json` · `shopping_cart.csv` · `required_fields.csv` ·
`rejection_criteria.csv`.

## 7. Safety / constraints

Offline (embedded design + `os.environ` name overlay). **No payment**, **no signup**, **no provider
probing**, **no api calls**, no secret values, no Paper Trader writes, **no orders**, **no automation**,
no broker, no deploy, no GCP. Any acquisition requires explicit **user opt-in**. Commit only the
phase11b4 files if tests pass. No push.

## Run / test (Windows PowerShell only)

```powershell
Set-Location C:\Users\binis\Stock_Prediction_app_push
python research/run_phase11b4_paid_data_shopping_cart.py
python -m pytest tests/test_phase11b4_paid_data_shopping_cart.py -q
```
