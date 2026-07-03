# Phase 11-B1 — Provider And Data-Family Discovery Map (v1)

## 1. Why this phase exists

Phase 11-C showed the free / entitled local data does **not** beat the modest `composite_sn` baseline.
To turn that into an actionable "what to acquire next", this phase catalogues the provider landscape
across the five orthogonal data families and overlays which providers are **already entitled** in this
shell (by env-var *name* presence only). It is built from prior-phase notes and generally-known public
product facts — it makes **no api calls**, does **no provider probing**, reads no secret values, and
creates **no orders** and **no automation**. Every vendor row is flagged `no_probe_performed=true`.

## 2. Families and providers catalogued

| family | providers (highlights) |
|---|---|
| **A. Analyst estimates / revisions** | Nasdaq Data Link (Zacks EE/ER) ★1, Intrinio (Zacks) ★1, FMP (owned key), AlphaVantage (owned key), LSEG I/B/E/S, FactSet |
| B. Short interest / securities lending | FINRA (free public), Polygon (owned key; family rejected in 10-A), ORTEX, S&P Global Sec. Finance |
| C. Options / implied vol | ORATS, OptionMetrics IvyDB, Polygon options (owned key), ThetaData |
| D. Insider / ownership | SEC EDGAR (free Form 4 + 13F), Finnhub (owned key; tested 11-C → no alpha), WhaleWisdom (13F) |
| E. News / sentiment | EODHD (owned; weak in 8-series), RavenPack, Benzinga |

★ = `alpha_priority` rank (1 = highest). The **analyst-estimate-revisions** family carries the top
priority (Phase 11-A #1); its free tiers (FMP, AlphaVantage) are too sparse / shallow, so a **paid trial**
of a point-in-time revision feed (Nasdaq Data Link Zacks or Intrinio Zacks) is the aimed-at acquisition.

## 3. Access overlay (from owned keys)

Owned keys entitle **FMP, Finnhub, Polygon, AlphaVantage, Nasdaq Data Link, Tiingo, EODHD**. Each provider
row is tagged `access_status ∈ {ENTITLED_NOW, FREE_PUBLIC, FREE_TIER_KEY_NEEDED, TRIAL_AVAILABLE,
PAID_ONLY, PAID_OR_QUOTE}` and `requires_user_opt_in` (yes when payment is required). This overlay is what
Phase 11-B4 ranks into a shopping cart.

## 4. Decision — `PROVIDER_MAP_READY`

All five orthogonal families are catalogued with ≥ 2 providers each, entitlement overlaid, and priorities
assigned. The map is ready to drive the paid shopping cart.

Decision enum: `PROVIDER_MAP_READY` · `PROVIDER_DISCOVERY_PARTIAL` · `PROVIDER_DISCOVERY_BLOCKED`.

## 5. Artifacts (`research/output/phase11b1_provider_data_family_discovery_map/`)

`phase11b1_provider_data_family_discovery_map.json` · `provider_data_family_map.csv` · `family_summary.csv`.

## 6. Safety / constraints

Offline (embedded registry + `os.environ` name-presence only). **No api calls**, **no provider probing**,
no secret values, no purchase, no Paper Trader writes, **no orders**, **no automation**, no broker, no
deploy, no GCP. Commit only the phase11b1 files if tests pass. No push.

## Run / test (Windows PowerShell only)

```powershell
Set-Location C:\Users\binis\Stock_Prediction_app_push
python research/run_phase11b1_provider_data_family_discovery_map.py
python -m pytest tests/test_phase11b1_provider_data_family_discovery_map.py -q
```
