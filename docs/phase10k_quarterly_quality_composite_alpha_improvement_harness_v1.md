# Phase 10-K — Quarterly Quality Composite Alpha Improvement Harness (v1)

## 1. What Phase 10-K does

Phase 10-K is a **deterministic, skeptical harness** that asks one narrow question:

> Can the Phase 10-D quarterly quality composite be **improved** using only narrow,
> defensible changes around the proven signal family — **without overfitting** — using
> only owned/local prior-phase outputs?

It freezes the Phase 10-D baseline, enumerates a small, pre-declared matrix of allowed
variants (leg re-weightings, robustness transforms, packaging filters), computes every
metric the frozen owned data actually supports, classifies each variant skeptically, and
selects a champion **only if it beats the baseline on honest evidence**. It writes a JSON
decision plus six diagnostic CSVs. It is fully offline (reads only frozen 10-D / 10-F /
10-H outputs; no network, no API key).

## 2. What Phase 10-K does NOT do

- It does **not** run a broad alpha search, add data families, or probe providers.
- It does **not** solve the Phase 10-I price-refresh / mark-to-market block (owned local
  EOD prices end at **2026-06-26** = inception, so realized P&L is still pending).
- It does **not** hand-review 194 tickers (that operating model was rejected in 10-G).
- It creates **no orders, no automation, no broker connection, no live trading, no deploy**,
  and writes **nothing** to the Paper Trader. No API key is printed or written.

## 3. What prior phases achieved

- **8-T → 8-X:** an autonomous EODHD alpha factory. Earnings/surprise-family signals looked
  promising at a smaller universe, but when the universe expanded from ~299 to ~545 names the
  edge **diluted** sharply, and the broad strong-gate search found no durable strong alpha.
- **8-Y / 9-x / 10-A:** attempts to add orthogonal data (revisions, short interest, options,
  sentiment). Most live provider shells had no usable key or were blocked, so accessible
  orthogonal alpha data was **exhausted / blocked** without additional paid entitlement.
- **10-B:** re-focused on owned data only (EODHD + Norgate). A high-t candidate,
  `f_accel_sn(eps_growth)`, was flagged **fragile / overfit** and set aside. The real, durable
  quality leads were `fcf_to_assets` and `operating_accruals`.
- **10-C:** strict offline OOS validation. Both leads were OOS-real and cohort-stable. The
  **monthly** horizon was **cost-killed** (turnover ~0.99 from event-panel name rotation); the
  **quarterly 63-day** horizon survived both 25bps and 50bps.
- **10-D:** built the first strict-gate-passing composite (see §5).
- **10-E / 10-F:** paper-review harness and owned-data sector-mapping repair (Unknown → 0%).
- **10-G / 10-H / 10-I:** rules-based 25L/25S paper portfolio and a paper-only position tracker
  (blocked on the price refresh noted above).

## 4. Where prior alpha attempts failed

The recurring failure mode was **breadth without durability**: signals that scored well in a
narrow universe or a single cost model **diluted** under a broader universe (8-V/8-W), failed a
strict multi-cohort/multi-subperiod OOS gate (8-X), needed **paid data that was blocked** (8-Y /
10-A), or looked strong only because of a fragile transform (`f_accel_sn` in 10-B) or a
**cost-killed** monthly rebalance (10-C). Phase 10-D is the first configuration that survived the
full strict bar simultaneously.

## 5. Why Phase 10-D is the current baseline

Phase 10-D confirmed a transparent quarterly quality composite:

| property | value |
|---|---|
| signal family | quality |
| horizon | **63 trading days / quarterly** |
| style | equal-weight long/short |
| ranking | **sector-neutral** |
| long signal | `fcf_to_assets` |
| short signal | `operating_accruals` |
| honest comparator | **`composite_sn`** |
| IC t-stat (63d) | 2.665 |
| quarterly net-25bps | **+0.00401** |
| quarterly net-50bps | +0.00095 |
| quarterly turnover | 0.6115 |
| OOS frac windows positive | 0.50 |
| top-sector share | 0.6262 |

It cleared the strict bar (survives 25bps and 50bps at quarterly turnover, both cohorts +, both
subperiods +, sector-robust, high-liquidity +), so it is the frozen baseline this phase tries to
beat.

## 6. Why the alpha is modest / boundary and must not be oversold

This is a **modest, boundary** alpha. Net-of-cost quarterly edge is small (net-25bps +0.00401,
net-50bps only +0.00095), IC t-stat sits right around the confirmation threshold, and pooled OOS
IC is positive but not overwhelming (frac-positive 0.50). It is a real, transparent, cost-robust
quality tilt — **not** a strong standalone signal, and **not** a prediction oracle. Any
presentation must keep that framing.

## 7. Why `composite_sn` is the honest comparator

The paper book (10-F/10-H) is ranked on the **sector-neutral** composite `comp_sn`. The apples-to-
apples benchmark for anything we would actually trade is therefore `composite_sn`
(net-25bps +0.00401), not a raw-basis variant.

## 8. Why `composite_raw` must not be treated as champion

`composite_raw` scores higher (net-25bps +0.00648, IC t 3.074) but it is **not** sector-neutral.
Using it as the champion for a sector-neutral book would be a basis mismatch — it earns part of
its spread from sector tilts the book deliberately removes. It is reported here as **diagnostic
context only**. The same caution applies to the single-leg standalone figures, which are on the
raw basis.

## 9. Which variants were tested

All variants are narrow changes around the two proven legs (no new data, no ML, no optimiser):

- **Weighting:** `w_50_50_equal` (== baseline), `w_60_40`, `w_40_60`, `w_70_30`, `w_30_70`,
  `w_fcf_only_100_0`, `w_accruals_only_0_100`.
- **Robustness transforms:** `zcap_abs_3_0`, `zcap_abs_2_5`, `winsorize_score`.
- **Packaging filters:** `liq_p25_cap25_extreme3` (== 10-H baseline), `liq_p50_stricter`,
  `sector_cap_20_stricter`.

**Central data limit.** The frozen 10-D/10-F/10-H outputs contain only (a) backtested summary
metrics for four **fixed** signals (equal-weight `composite_sn` / `composite_raw` and the two
standalone legs) and (b) the latest single **2026Q2** cross-section. They do **not** contain the
historical per-(month, ticker) sector-neutral scored panel with forward 63d returns. So the
interior weightings and the historical robustness transforms **cannot be honestly re-backtested**
here and are reported `INSUFFICIENT_INPUTS`, accompanied by cross-sectional book diagnostics only.
Only three points are backtestable: the equal-weight baseline itself, and the two single legs
(both on the raw basis).

## 10. Which variants were rejected and why

| variant | classification | reason |
|---|---|---|
| `w_fcf_only_100_0` | REJECT_UNSTABLE | OOS frac-positive 0.375 < 0.60 gate (weak held-out); single-leg; raw basis |
| `w_accruals_only_0_100` | REJECT_CONCENTRATION | top-sector share 0.6316 > 0.60 gate; single-leg; raw basis |
| `w_60_40` / `w_40_60` / `w_70_30` / `w_30_70` | INSUFFICIENT_INPUTS | no historical scored panel to re-backtest a re-weighting |
| `zcap_abs_3_0` / `zcap_abs_2_5` / `winsorize_score` | INSUFFICIENT_INPUTS | transform return effect needs the full history, not one cross-section |
| `liq_p50_stricter` / `sector_cap_20_stricter` | INSUFFICIENT_INPUTS | book reshape is faithful, but a single cross-section cannot show net-of-cost alpha |

Note that even ignoring the raw-basis issue, each backtestable single leg independently fails an
SN gate: `fcf_to_assets` on OOS stability, `operating_accruals` on sector concentration. So there
is no clean single-leg win even on its own terms.

## 11. Baseline remains champion

**Decision: `BASELINE_REMAINS_CHAMPION`.** No variant produced sufficient sector-neutral
backtested evidence to unseat the equal-weight sector-neutral composite. This is the skeptical
default, and the evidence upholds it: the only variants with backtested numbers are raw-basis
single legs that each breach an SN gate, and every re-weighting / transform lacks the historical
panel needed to judge it. Enhancement is **not** justified on honest evidence.

## 12. If any improvement exists, where does it come from?

The **leg-contribution** decomposition is the most useful finding:

- **Short leg (`operating_accruals`)** carries most of the robustness: highest IC t-stat (3.075)
  and best OOS (frac-positive 0.688) of the two legs — but standalone it is too sector-concentrated
  (top-sector 0.6316 > 0.60 gate).
- **Long leg (`fcf_to_assets`)** is a **weak standalone alpha** (OOS frac-positive only 0.375) but
  contributes **diversification**: it pulls the composite's sector concentration down
  (0.4995 standalone) and stabilises the blended OOS.
- The composite's value is therefore **not** extra headline return over the accruals leg; it is
  **lower sector concentration and steadier OOS** from combining the two. Any future gain is more
  likely to come from **lower turnover / better packaging** than from re-weighting the legs.

## 13. What the next phase should be

**Phase 10-L — persist the historical sector-neutral scored panel.** To test the 60/40 … 30/70
weightings and the z-cap / winsorize transforms *honestly*, persist the per-(month, ticker)
sector-neutral z-legs (`z_fcf_sn`, oriented `z_accruals_sn`) and forward 63d returns from the owned
10-B/10-C engine into a frozen artifact, then re-run this harness against that panel. Still
offline, owned-data-only, **no orders, no automation, no broker, no live trading, no deploy**.

## Artifacts (`research/output/phase10k_quarterly_quality_composite_alpha_improvement_harness/`)

`phase10k_quarterly_quality_composite_alpha_improvement_harness.json` · `variant_scorecard.csv` ·
`baseline_vs_enhancements.csv` · `leg_contribution_summary.csv` · `turnover_cost_summary.csv` ·
`sector_liquidity_diagnostics.csv` · `rejected_variants.csv` · `secret_safety_audit.csv`.

## Run / test (Windows PowerShell only)

```powershell
Set-Location C:\Users\binis\Stock_Prediction_app_push
python -m py_compile research/run_phase10k_quarterly_quality_composite_alpha_improvement_harness.py
python research/run_phase10k_quarterly_quality_composite_alpha_improvement_harness.py          # offline; no key
python -m pytest tests/test_phase10k_quarterly_quality_composite_alpha_improvement_harness.py -q
```

## Status — live run (offline; exit 0)

**Decision: `BASELINE_REMAINS_CHAMPION`.** 13 variants tested, 11 rejected / insufficient. The
liquidity-p25 threshold reproduced exactly against the frozen 10-H value (135,025,175.6), and the
equal-weight cross-section reconstruction tracks the frozen `comp_sn` ordering at rank-correlation
≈ 0.96 (a fidelity check on the diagnostic, which is still excluded from the decision).

## Constraints honored

Offline (no network / key / provider probe); **owned/local data only**; no new purchase; **no
Paper Trader writes; no signals; no trade decisions; NO orders; NO automation; NO broker; NO live
trading; no deploy; no GCP**; no package install; no full regression (targeted tests only); keys
never printed or written; output is metadata only. **No commit. No push.**
