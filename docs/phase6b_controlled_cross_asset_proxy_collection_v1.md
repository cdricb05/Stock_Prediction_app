# Phase 6-B — Controlled Cross-Asset Proxy Data Collection (v1)

**Track A (quant brain) research. Controlled data collection only. Dry-run by default.**
No strategy / shadow test, no Phase 6-A rerun, no model trained or deployed, no database
touched, no orders / broker / automation, no paid API, no FMP, no Paper Trader / GCP /
deploy work. No commit, no push.

## Why this phase exists

Phase 6-A tested whether a cross-asset / macro context signal improves 5–20 day equity
selection over the Phase 5-C price-only champion. The partial macro pack that could be
built from purely local data (WTI, broad USD, 10Y/2Y, CPI, Fed Funds, SPY) **did not
help** and actively degraded selection, so Phase 6-A returned
`NEEDS_CONTROLLED_CROSS_ASSET_DATA_COLLECTION`. The data inventory showed the real
blocker: the broad cross-asset ETF / sector / credit / volatility / commodity proxy
universe is **not collected locally** (0 usable proxies). Phase 6-B is that controlled
collection step — it assembles the proxy pack needed to rerun the macro-context harness
**properly** in a later Phase 6-C. It is a collection phase only: it runs no strategy /
shadow test and does **not** rerun Phase 6-A.

## What this phase does

`research/run_phase6b_controlled_cross_asset_proxy_collection.py`:

1. **Plans** the per-ticker collection across the 46-proxy cross-asset universe (the Phase
   6-A candidate universe), grouped into 8 macro-context layers.
2. **Dry-run by default**: with no flags it contacts **no network**, emits the plan,
   validates whatever normalized files already exist, and reports what a `--live` run
   *would* do. A network request is made only under `--live`.
3. **Reuses the audited Phase 3-Y Alpha Vantage collector** for the only network layer
   (`TIME_SERIES_DAILY_ADJUSTED`, `outputsize=full`, `datatype=csv`), so the same rate
   limiting (≥ 13 s/request), provider-limit handling, REDACTED-endpoint persistence, and
   "never fabricate a row" discipline apply unchanged.
4. **Validates** the pack (rows, date range, adjusted-close + volume availability, asset
   classes, 5-year history) and emits one allowed recommendation + a gated Phase 6-C plan.

## Provider & safety contract

| Rule | How it is enforced |
|---|---|
| Dry-run by default | `--live` flag required for any network; argparse default `live=False` |
| Free Alpha Vantage only | delegates to Phase 3-Y; host allow-list = `www.alphavantage.co` only |
| No paid / FMP / SimFin / Yahoo / Stooq / FRED / yfinance | no such import or host anywhere; all present only as `False` safety flags |
| Key from env only, never printed | `ALPHAVANTAGE_API_KEY` read via `os.environ`; only a REDACTED endpoint template is persisted |
| Missing key STOPS before network | `--live` with no key stops with `stopped_no_key=true` and prints the exact set-key command |
| Rate-limited, quota-safe | ≥ 13 s between requests; stops immediately on a rate-limit / premium response, preserving downloads |
| Raw payloads gitignored | raw bodies go only to `research/output/.../raw/` (matched by `research/output/*/raw/` in `.gitignore`) |
| No blind overwrite | valid `<TICKER>.csv` files are skipped; an invalid one is backed up into `raw/` before any rewrite |
| No fabrication | invalid rows dropped; missing history stays missing |

## Cross-asset proxy universe (46 tickers, 8 layers)

| Layer (`asset_class`) | Proxies |
|---|---|
| `equity_structure` | QQQ, IWM, DIA |
| `global_regional` | EFA, EEM, ACWI, VT, EWJ, EWU, FXI, EWZ, INDA |
| `rates_duration` | TLT, IEF, SHY, TIP |
| `credit` | HYG, LQD |
| `dollar_fx` | UUP, FXE, FXY, FXB, FXA, FXC |
| `commodity` | GLD, SLV, DBC, GSG, USO, BNO, UNG, CPER |
| `volatility` | VIXY |
| `sector_rotation` | XLE, XLK, XLF, XLI, XLV, XLY, XLP, XLU, XLRE, XLB, XLC, SMH, KRE |

## Readiness gate (Phase 6-B success criteria)

`READY_FOR_PHASE6C_MACRO_CONTEXT_RERUN` requires **all** of:

- ≥ **12** usable proxies (each ≥ 250 usable daily rows with a positive adjusted close), and
- across ≥ **5** distinct asset-class layers, and
- ≥ **12** of those carrying ≥ **5 years** of history (≥ 1259 rows) where available.

## This run (dry-run, no key)

| Field | Value |
|---|---|
| mode | `dry_run` |
| api_key_present | **false** (absent in this shell) |
| live_collection_run | false |
| requests_attempted | 0 |
| network_called | false |
| usable proxies | **0 / 46** |
| asset classes covered | 0 |
| proxies with ≥ 5 yr | 0 |
| missing proxies | 46 |
| failed proxies | 0 |
| ready_threshold_met | **false** |
| recommendation | **`NEEDS_ADDITIONAL_PROXY_COLLECTION`** |

The Alpha Vantage key is **not set in this environment**, so per the task no live batch was
run. (For reference, the last live attempt — Phase 3-Y on 2026-06-20 from a shell that *did*
carry a key — hit the Alpha Vantage free-tier limit on the very first request and downloaded
0 tickers. The free daily quota is the practical constraint here.)

## Was live collection run? — **No.**

No key in the agent shell ⇒ dry-run only ⇒ zero network. To collect, the operator runs the
exact command below in a shell where the free key is set. Because each request waits ≥ 13 s
and the free tier caps daily calls, expect to run `--live` across **several days** (the
collector preserves already-downloaded files and resumes where it left off).

## Recommendation: `NEEDS_ADDITIONAL_PROXY_COLLECTION`

The pack is empty locally and no key was available to collect, so the readiness gate is not
met. **Phase 6-C is gated and NOT yet allowed** (`proceed_to_phase6c = false`,
`rerun_phase6a_now = false`).

## Exact future commands (recorded, **not run**)

```powershell
Set-Location C:\Users\binis\Stock_Prediction_app_push

# 1. Set the FREE Alpha Vantage key (this shell only; never committed) and collect LIVE.
#    Re-run across days as the free-tier daily quota resets; downloads are preserved.
$env:ALPHAVANTAGE_API_KEY = "<your-free-alpha-vantage-key>"
python -B research\run_phase6b_controlled_cross_asset_proxy_collection.py --live

# 2. Re-validate (dry-run) until the readiness gate reports READY (>= 12 / 5 classes / 5yr).
python -B research\run_phase6b_controlled_cross_asset_proxy_collection.py

# 3. ONLY AFTER READY: rerun the Phase 6-A macro-context harness on the full pack (Phase 6-C).
python -B research\run_phase6a_cross_asset_macro_context_alpha.py
```

## Committed-safe artifacts

`research/output/phase6b_controlled_cross_asset_proxy_collection/`:

- `phase6b_controlled_cross_asset_proxy_collection.json` — main report (mode, counts, gate
  table, recommendation, safety flags).
- `cross_asset_proxy_collection_plan.csv` — per-proxy plan (already-present, planned action,
  redacted endpoint).
- `cross_asset_proxy_collection_status.csv` — per-proxy action / rows / date range / adjusted-
  close + volume availability / usability.
- `cross_asset_proxy_quality_report.csv` — per-proxy quality (rows, span, ≥ 5 yr, blocker).
- `phase6c_macro_context_rerun_plan.json` — gated next-step (commands recorded, not run).
- `raw/` — gitignored raw provider payloads (written only under `--live`).

## Run commands

```powershell
Set-Location C:\Users\binis\Stock_Prediction_app_push
python -B research\run_phase6b_controlled_cross_asset_proxy_collection.py          # dry-run
$env:PAPER_TRADER_TEST_DATABASE_URL = "postgresql+psycopg2://postgres:Adam2015@localhost:5432/paper_trader_test"
python -m pytest tests\test_phase6b_controlled_cross_asset_proxy_collection.py -q  # 21 passed
```

## Safety contract

Dry-run by default · zero network unless `--live` with a key · key read from env only, never
printed/persisted · free Alpha Vantage only (no FMP / SimFin / Yahoo / Stooq / FRED /
yfinance / paid API) · raw payloads gitignored · existing valid files preserved · no row
fabrication · no strategy / shadow test · Phase 6-A not rerun · no model trained / deployed ·
no database / migration / serving flag · no orders / broker / automation · no D: write · no
Paper Trader / GCP / deploy · no binary artifacts · survivorship-biased universe (no
production edge claimed) · no commit · no push.
