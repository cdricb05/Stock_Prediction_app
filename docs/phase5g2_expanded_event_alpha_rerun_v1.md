# Phase 5-G2 — Expanded-Coverage Event Alpha Rerun (v1)

**Track A (quant brain) research rerun. Offline. Point-in-time. Preview-only.** Zero
network, no API key read or required, no ticker fetched, no raw-cache modification, no
strategy-filter optimization. No model trained, no prediction computed, no database
touched, no service restarted, no order placed, no automation enabled, no binary
artifact created. No Paper Trader / GCP / deploy work. No commit, no push.

## Why this phase exists

Phase 5-G found that on the original **50-ticker** earnings panel, `price_plus_event_alpha`
added a small **+0.0091** incremental out-of-sample rank IC over the Phase 5-C price-only
champion on the covered names (`EVENT_DATA_PARTIAL_BUT_USEFUL`, gated below the project's
own 75-ticker event minimum). Phase 5-G1 → 5-G1D then expanded PIT-safe Alpha Vantage
earnings coverage from 50 → **75 / 128** (raw cache = 75 payloads). With coverage now at
the gate, the strategic question is whether that incremental event edge **survives the
broader coverage** or was an artifact of the narrow 50-name cross-section.

    "Did price_plus_event_alpha or any event model improve over the correct Phase 5-C
     price-only reference on the SAME 75 covered tickers?"

## What this phase does

`research/run_phase5g2_expanded_event_alpha_rerun.py` is a thin, reuse-only wrapper:

1. **Preflight verification (read-only).** Confirms raw cache count, distinct-ticker
   event coverage, the Phase 5-C universe size (128), that no raw files are staged, that
   newly collected raw payloads are gitignored, and that no API key / network is needed.
2. **Expanded rerun.** Imports the corrected Phase 5-G runner
   (`research/run_phase5g_earnings_revision_alpha.py`) **unchanged** and calls its
   `run()` against the read-only D: price history. Every Phase 5-G artifact is routed
   into `phase5g_expanded_coverage_run/` under this phase's output dir, so the committed
   **original 50-ticker** Phase 5-G artifacts are preserved untouched.
3. **Original baseline.** Reads the committed 50-ticker Phase 5-G report (read-only) for
   the original-vs-expanded comparison.
4. **Apples-to-apples comparison** on the SAME covered (date, ticker) cross-section: the
   Phase 5-C price-only reference, `event_alpha_only`, `price_plus_event_alpha`,
   `event_gated_price_signal`, and the `best_event_candidate`. All models are scored by
   the reused Phase 5-G harness on the identical covered records (`n_scored_dates` equal),
   so IC differences are a clean incremental test.
5. Emits an honest gate matrix and one of the allowed recommendations. Never forces PASS.

## Result (this run)

| Field | Value |
|---|---|
| `raw_cache_count` | **75** |
| `coverage_count` (covered / universe) | **75 / 128** (`covered_date_count` 112) |
| `phase5c_reference_model_used` | `top_quintile_score_model` |
| `phase5c_reference_mean_rank_ic_covered` | **0.048001** |
| `event_alpha_only_mean_rank_ic` | 0.014419 (incremental −0.033582) |
| `price_plus_event_alpha_mean_rank_ic` | **0.04603** (incremental **−0.001971**) |
| `event_gated_price_signal_mean_rank_ic` | 0.031490 (incremental −0.016512) |
| `best_event_model` (strongest event arm) | `price_plus_event_alpha` |
| `best_event_model_is_eligible_incremental_edge` | **false** |
| `eligible_best_event_candidate` | **null** (no arm cleared the gate) |
| `incremental_ic_vs_reference` | **−0.001971** (threshold `> +0.005`) |
| `placebo_ic` (best arm) | −0.002285 (collapses toward zero — leakage-clean) |
| `recommendation` | **`NO_INCREMENTAL_EVENT_EDGE`** |

### Original (50) vs expanded (75)

| | original 50 | expanded 75 |
|---|---|---|
| Phase 5-C reference IC (covered) | 0.036471 | **0.048001** |
| best event arm | `price_plus_event_alpha` | `price_plus_event_alpha` |
| best-arm IC | 0.045538 | 0.046030 |
| **incremental IC vs reference** | **+0.009067** | **−0.001971** |

## Did event alpha survive expanded coverage? — **No.**

The event signal did **not** improve on the broader cross-section. The mechanism is clear
from the numbers: expanding from 50 → 75 covered names **strengthened the Phase 5-C price
reference itself** (covered rank IC 0.0365 → 0.0480), while the best event arm
(`price_plus_event_alpha`) stayed essentially flat (0.0455 → 0.0460). The +0.0091 edge at
50 tickers therefore **inverted to −0.0020** at 75 tickers — the price champion now
slightly out-ranks every event model on the same names. No event arm has a positive,
placebo-clean, OOS-visible incremental edge, so the harness names **no** eligible best
candidate. The placebo IC of the best arm (−0.0023) confirms the comparison is
leakage-clean — this is a genuine "no incremental edge", not a leakage artifact.

## Gates (this run): 14 PASS / 1 FAIL

The single FAIL is `incremental_ic_over_reference_gate` (−0.001971 is not `> +0.005`) —
**expected and correct** for a no-edge result; the recommendation agrees with it. All
safety, PIT-safety, leakage/placebo-clean, same-covered-universe, no-network, and
raw-hygiene (no raw files modified/staged, new payloads gitignored) gates PASS.

## Is Phase 5-G3 needed, and what should it be?

**Yes — but not a strategy test.** `phase5g3_next_plan.json` records
`proceed_to_phase5h_strategy_test = false`. Because the earnings-surprise / PEAD event
composite carries **no incremental edge** over the price champion at the project's own
coverage gate, advancing to a Phase 5-H event-alpha strategy/shadow test is not justified.
Phase 5-G3 should instead **re-specify the event signal**:

- Add a **point-in-time analyst-estimate-revision series** (ticker, revision publication
  date, prior_estimate, new_estimate) — still the single most-cited missing input; the
  current composite is surprise/drift-only.
- **Attribute the IC change to the added tickers** (cohort / by-year IC breakdown) to
  confirm the edge dilution is structural, not a few outliers.
- Re-confirm survivorship handling on a survivorship-free universe before trusting any
  absolute portfolio return.

## Committed-safe artifacts

`research/output/phase5g2_expanded_event_alpha_rerun/`:

- `phase5g2_expanded_event_alpha_rerun.json` — main report (all required fields + flags).
- `expanded_coverage_event_alpha_scoreboard.csv` — per-model IC / t-stat / horizon / placebo.
- `expanded_coverage_incremental_edge_report.csv` — per-arm incremental edge (expanded 75)
  plus the original-50 best row.
- `expanded_coverage_gate_matrix.csv` — gate / status / metric / value / threshold / note.
- `expanded_coverage_horizon_comparison.csv` — 5d / 10d / 20d IC per model.
- `expanded_coverage_yearly_ic.csv` — yearly IC per model.
- `phase5g3_next_plan.json` — gated next-step plan.
- `phase5g_expanded_coverage_run/` — the full underlying Phase 5-G run at 75-ticker
  coverage (preserves provenance; the committed original 50-ticker dir is untouched).

## Run commands

```powershell
Set-Location C:\Users\binis\Stock_Prediction_app_push
python research\run_phase5g2_expanded_event_alpha_rerun.py          # writes committed-safe artifacts
$env:PAPER_TRADER_TEST_DATABASE_URL = "postgresql+psycopg2://postgres:Adam2015@localhost:5432/paper_trader_test"
python -m pytest tests\test_phase5g2_expanded_event_alpha_rerun.py -q
```

## Safety contract

Offline · zero network / API call · no API key read or required · no ticker fetched · no
raw file modified, deleted, or staged · newly collected raw payloads stay gitignored ·
no strategy-filter optimization · committed-safe text artifacts only · no model trained /
deployed · no Paper Trader / GCP / deploy · no orders / broker / automation · no binary
artifacts · survivorship-biased universe (no production edge claimed) · no commit · no push.
