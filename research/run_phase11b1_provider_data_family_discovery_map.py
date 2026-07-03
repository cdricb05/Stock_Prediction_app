"""Phase 11-B1 - Provider And Data-Family Discovery Map.

WHY THIS PHASE EXISTS
    Phase 11-B0 found entitled keys and one backtestable local family; Phase 11-C then showed the free /
    entitled local data does NOT beat the modest composite_sn baseline. To turn that into an actionable
    "what to acquire next", this phase catalogues the provider landscape across the five orthogonal data
    families (analyst estimates/revisions, short interest / securities lending, options / implied vol,
    insider / ownership, news / sentiment), records each provider's access terms, and overlays which
    providers are ALREADY entitled in this shell (by env-var NAME presence only). It feeds the Phase 11-B4
    paid shopping cart.

    The map is built from prior-phase notes and generally-known public product facts. It performs NO
    network probing of any provider endpoint, calls NO API, and reads NO secret values - every vendor row
    is flagged no_probe_performed=true.

DECISIONS (allowed)
    PROVIDER_MAP_READY | PROVIDER_DISCOVERY_PARTIAL | PROVIDER_DISCOVERY_BLOCKED

CONSTRAINTS HONORED
    Offline (embedded registry + os.environ name-presence only); no provider probing; no api calls; no
    secret values; no purchase; no Paper Trader writes; NO orders / automation / broker / deploy / GCP;
    output is research metadata (JSON + CSV) only. Commit only phase11b1 files if tests pass. No push.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]

PHASE = "11-B1"
PHASE_NAME = "Provider And Data-Family Discovery Map"
STEM = "phase11b1_provider_data_family_discovery_map"
PERFORMS_NETWORK = False

DEC_READY = "PROVIDER_MAP_READY"
DEC_PARTIAL = "PROVIDER_DISCOVERY_PARTIAL"
DEC_BLOCKED = "PROVIDER_DISCOVERY_BLOCKED"
ALLOWED_DECISIONS = (DEC_READY, DEC_PARTIAL, DEC_BLOCKED)

# Map a provider to the env-var NAME that would entitle it (presence-only overlay; never reads the value).
PROVIDER_KEY_ENV = {
    "EODHD": "EODHD_API_KEY", "FMP": "FMP_API_KEY", "Finnhub": "FINNHUB_API_KEY",
    "Polygon": "POLYGON_API_KEY", "AlphaVantage": "ALPHAVANTAGE_API_KEY",
    "Nasdaq Data Link": "NASDAQ_DATA_LINK_API_KEY", "Tiingo": "TIINGO_API_KEY",
    "Intrinio": "INTRINIO_API_KEY", "ORATS": "ORATS_API_KEY",
}

FAMILIES = {
    "A_analyst_estimates_revisions": "Analyst estimates / revisions",
    "B_short_interest_securities_lending": "Short interest / securities lending",
    "C_options_implied_vol": "Options / implied volatility / skew",
    "D_insider_ownership": "Insider transactions / institutional ownership",
    "E_news_sentiment": "News / event sentiment (PIT)",
}

# Each row: provider, family, dataset/endpoint, public_url, pricing_if_public, free_tier, free_trial,
# api_key_required, payment_required, pit_support, historical_depth, sp500_coverage, update_frequency,
# expected_rows, storage, integration_difficulty, licensing_risk, alpha_priority (1=highest).
PROVIDERS = [
    # ---- A. Analyst estimates / revisions (Phase 11-A #1 family) --------------------------------
    {"provider": "Nasdaq Data Link", "family": "A_analyst_estimates_revisions",
     "dataset": "Zacks Earnings Estimates (ZACKS/EE) + Estimate Revisions (ZACKS/ER)",
     "public_url": "https://data.nasdaq.com/databases/ZEE",
     "pricing_if_public": "subscription (institutional; quote-based)", "free_tier": False,
     "free_trial": True, "api_key_required": True, "payment_required": True, "pit_support": "yes",
     "historical_depth": "~20yr", "sp500_coverage": "full", "update_frequency": "daily",
     "expected_rows": "~5-15M (universe x history x fields)", "storage": "~2-6 GB",
     "integration_difficulty": "medium", "licensing_risk": "medium (redistribution restricted)",
     "alpha_priority": 1},
    {"provider": "Intrinio", "family": "A_analyst_estimates_revisions",
     "dataset": "Zacks EPS/Sales Estimates + Estimate Trends / Revisions",
     "public_url": "https://intrinio.com/marketplace/data/zacks",
     "pricing_if_public": "from ~$1-3k/yr tiered", "free_tier": False, "free_trial": True,
     "api_key_required": True, "payment_required": True, "pit_support": "yes", "historical_depth": "~15-20yr",
     "sp500_coverage": "full", "update_frequency": "daily", "expected_rows": "~5-15M",
     "storage": "~2-6 GB", "integration_difficulty": "medium", "licensing_risk": "medium",
     "alpha_priority": 1},
    {"provider": "FMP", "family": "A_analyst_estimates_revisions",
     "dataset": "Analyst Estimates / Price Target / Ratings (v3/v4)",
     "public_url": "https://site.financialmodelingprep.com/developer/docs",
     "pricing_if_public": "$0 free / ~$22-70/mo paid", "free_tier": True, "free_trial": True,
     "api_key_required": True, "payment_required": False, "pit_support": "weak (snapshot; limited history)",
     "historical_depth": "shallow on lower tiers", "sp500_coverage": "partial on free tier",
     "update_frequency": "daily", "expected_rows": "~1-3M", "storage": "~0.5-1 GB",
     "integration_difficulty": "low", "licensing_risk": "low",
     "alpha_priority": 2},
    {"provider": "LSEG / I/B/E/S", "family": "A_analyst_estimates_revisions",
     "dataset": "I/B/E/S Estimates (detail + summary, revisions)",
     "public_url": "https://www.lseg.com/en/data-analytics/financial-data/company-data/ibes-estimates",
     "pricing_if_public": "enterprise (quote only)", "free_tier": False, "free_trial": False,
     "api_key_required": True, "payment_required": True, "pit_support": "yes (gold standard)",
     "historical_depth": "~40yr", "sp500_coverage": "full", "update_frequency": "intraday",
     "expected_rows": ">50M", "storage": ">20 GB", "integration_difficulty": "high",
     "licensing_risk": "high (enterprise contract)", "alpha_priority": 3},
    {"provider": "FactSet Estimates", "family": "A_analyst_estimates_revisions",
     "dataset": "Estimates / Consensus / Revisions",
     "public_url": "https://www.factset.com/", "pricing_if_public": "enterprise (quote only)",
     "free_tier": False, "free_trial": False, "api_key_required": True, "payment_required": True,
     "pit_support": "yes", "historical_depth": "~20yr", "sp500_coverage": "full",
     "update_frequency": "intraday", "expected_rows": ">50M", "storage": ">20 GB",
     "integration_difficulty": "high", "licensing_risk": "high", "alpha_priority": 4},
    {"provider": "AlphaVantage", "family": "A_analyst_estimates_revisions",
     "dataset": "EARNINGS (reported vs estimated EPS) / EARNINGS_ESTIMATES",
     "public_url": "https://www.alphavantage.co/documentation/",
     "pricing_if_public": "free (25 req/day) / ~$50-250/mo", "free_tier": True, "free_trial": True,
     "api_key_required": True, "payment_required": False, "pit_support": "weak",
     "historical_depth": "quarterly EPS ~20yr", "sp500_coverage": "full but RATE-CAPPED on free",
     "update_frequency": "on earnings", "expected_rows": "~0.5M", "storage": "~0.2 GB",
     "integration_difficulty": "low", "licensing_risk": "low", "alpha_priority": 2},

    # ---- B. Short interest / securities lending --------------------------------------------------
    {"provider": "FINRA", "family": "B_short_interest_securities_lending",
     "dataset": "Daily Short Sale Volume + bi-monthly Short Interest",
     "public_url": "https://www.finra.org/finra-data/browse-catalog/short-sale-volume-data",
     "pricing_if_public": "free (public files)", "free_tier": True, "free_trial": True,
     "api_key_required": False, "payment_required": False, "pit_support": "yes (dated files)",
     "historical_depth": "~10yr+", "sp500_coverage": "full", "update_frequency": "daily / bimonthly",
     "expected_rows": "~5-20M", "storage": "~1-3 GB", "integration_difficulty": "low-medium",
     "licensing_risk": "low (public)", "alpha_priority": 3},
    {"provider": "Polygon", "family": "B_short_interest_securities_lending",
     "dataset": "Short Interest / Short Volume (owned key)",
     "public_url": "https://polygon.io/docs", "pricing_if_public": "tiered ($29-199/mo)",
     "free_tier": False, "free_trial": True, "api_key_required": True, "payment_required": False,
     "pit_support": "yes", "historical_depth": "~8yr", "sp500_coverage": "full",
     "update_frequency": "bimonthly", "expected_rows": "~0.5M", "storage": "~0.3 GB",
     "integration_difficulty": "low", "licensing_risk": "low",
     "alpha_priority": 4},  # family already rejected in 10-A
    {"provider": "ORTEX", "family": "B_short_interest_securities_lending",
     "dataset": "Estimated short interest, utilization, cost-to-borrow",
     "public_url": "https://www.ortex.com/", "pricing_if_public": "retail sub (~$50-100/mo)",
     "free_tier": False, "free_trial": True, "api_key_required": True, "payment_required": True,
     "pit_support": "partial", "historical_depth": "~5yr", "sp500_coverage": "full",
     "update_frequency": "daily", "expected_rows": "~2M", "storage": "~0.5 GB",
     "integration_difficulty": "medium", "licensing_risk": "medium", "alpha_priority": 4},
    {"provider": "S&P Global Securities Finance (ex-IHS Markit)", "family": "B_short_interest_securities_lending",
     "dataset": "Securities lending / borrow-fee analytics",
     "public_url": "https://www.spglobal.com/", "pricing_if_public": "enterprise (quote only)",
     "free_tier": False, "free_trial": False, "api_key_required": True, "payment_required": True,
     "pit_support": "yes", "historical_depth": "~15yr", "sp500_coverage": "full",
     "update_frequency": "daily", "expected_rows": ">20M", "storage": ">5 GB",
     "integration_difficulty": "high", "licensing_risk": "high", "alpha_priority": 5},

    # ---- C. Options / implied vol ----------------------------------------------------------------
    {"provider": "ORATS", "family": "C_options_implied_vol",
     "dataset": "Historical IV surfaces / smv summaries / skew",
     "public_url": "https://orats.com/data-api", "pricing_if_public": "~$100-600/mo",
     "free_tier": False, "free_trial": True, "api_key_required": True, "payment_required": True,
     "pit_support": "yes", "historical_depth": "~15yr", "sp500_coverage": "full",
     "update_frequency": "daily", "expected_rows": ">50M", "storage": ">10 GB",
     "integration_difficulty": "high", "licensing_risk": "medium", "alpha_priority": 3},
    {"provider": "OptionMetrics IvyDB", "family": "C_options_implied_vol",
     "dataset": "IvyDB US (vol surface, greeks)", "public_url": "https://optionmetrics.com/",
     "pricing_if_public": "enterprise / academic (quote only)", "free_tier": False, "free_trial": False,
     "api_key_required": False, "payment_required": True, "pit_support": "yes", "historical_depth": "~25yr",
     "sp500_coverage": "full", "update_frequency": "daily", "expected_rows": ">100M", "storage": ">50 GB",
     "integration_difficulty": "high", "licensing_risk": "high", "alpha_priority": 4},
    {"provider": "Polygon", "family": "C_options_implied_vol",
     "dataset": "Options aggregates / snapshots (owned key)", "public_url": "https://polygon.io/docs/options",
     "pricing_if_public": "options add-on ($29-199/mo)", "free_tier": False, "free_trial": True,
     "api_key_required": True, "payment_required": False, "pit_support": "partial (needs surface build)",
     "historical_depth": "~5yr", "sp500_coverage": "full", "update_frequency": "daily",
     "expected_rows": ">50M", "storage": ">10 GB", "integration_difficulty": "high",
     "licensing_risk": "low", "alpha_priority": 4},
    {"provider": "ThetaData", "family": "C_options_implied_vol",
     "dataset": "Historical options EOD / greeks", "public_url": "https://www.thetadata.net/",
     "pricing_if_public": "~$50-160/mo", "free_tier": False, "free_trial": True, "api_key_required": True,
     "payment_required": True, "pit_support": "partial", "historical_depth": "~10yr", "sp500_coverage": "full",
     "update_frequency": "daily", "expected_rows": ">50M", "storage": ">10 GB",
     "integration_difficulty": "high", "licensing_risk": "medium", "alpha_priority": 4},

    # ---- D. Insider / ownership ------------------------------------------------------------------
    {"provider": "SEC EDGAR", "family": "D_insider_ownership",
     "dataset": "Form 4 insider transactions + 13F holdings (public filings)",
     "public_url": "https://www.sec.gov/cgi-bin/browse-edgar",
     "pricing_if_public": "free (public)", "free_tier": True, "free_trial": True,
     "api_key_required": False, "payment_required": False, "pit_support": "yes (filing date)",
     "historical_depth": "~20yr", "sp500_coverage": "full", "update_frequency": "daily",
     "expected_rows": ">20M (Form 4) ; ~1-3M (13F)", "storage": "~3-8 GB",
     "integration_difficulty": "medium-high (XML parse + entity map)", "licensing_risk": "low",
     "alpha_priority": 3},
    {"provider": "Finnhub", "family": "D_insider_ownership",
     "dataset": "Insider transactions + insider sentiment (owned key)",
     "public_url": "https://finnhub.io/docs/api", "pricing_if_public": "free / ~$50-250/mo",
     "free_tier": True, "free_trial": True, "api_key_required": True, "payment_required": False,
     "pit_support": "partial (monthly aggregate)", "historical_depth": "~10yr (sentiment)",
     "sp500_coverage": "broad (292 names local)", "update_frequency": "monthly",
     "expected_rows": "~0.5M", "storage": "~0.2 GB", "integration_difficulty": "low",
     "licensing_risk": "low", "alpha_priority": 4},  # tested in 11-C -> no alpha
    {"provider": "WhaleWisdom", "family": "D_insider_ownership",
     "dataset": "13F institutional ownership changes", "public_url": "https://whalewisdom.com/",
     "pricing_if_public": "~$50-300/mo API", "free_tier": False, "free_trial": True,
     "api_key_required": True, "payment_required": True, "pit_support": "yes (45-day filing lag)",
     "historical_depth": "~15yr", "sp500_coverage": "full", "update_frequency": "quarterly",
     "expected_rows": "~5M", "storage": "~1-2 GB", "integration_difficulty": "medium",
     "licensing_risk": "medium", "alpha_priority": 5},

    # ---- E. News / sentiment ---------------------------------------------------------------------
    {"provider": "EODHD", "family": "E_news_sentiment",
     "dataset": "News + financial news sentiment (owned key; already collected)",
     "public_url": "https://eodhd.com/financial-apis/", "pricing_if_public": "~$20-80/mo",
     "free_tier": False, "free_trial": True, "api_key_required": True, "payment_required": False,
     "pit_support": "partial", "historical_depth": "~5-8yr", "sp500_coverage": "broad",
     "update_frequency": "daily", "expected_rows": "~5M", "storage": "~1-2 GB",
     "integration_difficulty": "medium", "licensing_risk": "low",
     "alpha_priority": 5},  # owned EODHD sentiment already weak in 8-series
    {"provider": "RavenPack", "family": "E_news_sentiment",
     "dataset": "News analytics / event sentiment (PIT)", "public_url": "https://www.ravenpack.com/",
     "pricing_if_public": "enterprise (quote only)", "free_tier": False, "free_trial": False,
     "api_key_required": True, "payment_required": True, "pit_support": "yes (gold standard)",
     "historical_depth": "~20yr", "sp500_coverage": "full", "update_frequency": "intraday",
     "expected_rows": ">100M", "storage": ">20 GB", "integration_difficulty": "high",
     "licensing_risk": "high", "alpha_priority": 5},
    {"provider": "Benzinga", "family": "E_news_sentiment",
     "dataset": "News feed + analyst rating actions", "public_url": "https://www.benzinga.com/apis/",
     "pricing_if_public": "~$100-500/mo", "free_tier": False, "free_trial": True, "api_key_required": True,
     "payment_required": True, "pit_support": "partial", "historical_depth": "~8yr", "sp500_coverage": "full",
     "update_frequency": "intraday", "expected_rows": ">10M", "storage": ">3 GB",
     "integration_difficulty": "medium", "licensing_risk": "medium", "alpha_priority": 5},
]


def build_map():
    present = {name: (name in os.environ) for name in sorted(set(PROVIDER_KEY_ENV.values()))}
    rows = []
    for p in PROVIDERS:
        env = PROVIDER_KEY_ENV.get(p["provider"])
        entitled = bool(present.get(env)) if env else False
        if p["payment_required"] and not (p["free_tier"] or p["free_trial"]):
            access = "PAID_ONLY"
        elif entitled and (p["free_tier"] or not p["payment_required"]):
            access = "ENTITLED_NOW"
        elif p["free_tier"] or (env is None and not p["api_key_required"]):
            access = "FREE_PUBLIC" if not p["api_key_required"] else "FREE_TIER_KEY_NEEDED"
        elif p["free_trial"]:
            access = "TRIAL_AVAILABLE"
        else:
            access = "PAID_OR_QUOTE"
        row = dict(p)
        row["entitled_key_present"] = entitled
        row["access_status"] = access
        row["no_probe_performed"] = True
        row["requires_user_opt_in"] = "yes" if p["payment_required"] else "no"
        rows.append(row)

    fam_counts = {}
    for r in rows:
        fam_counts.setdefault(r["family"], 0)
        fam_counts[r["family"]] += 1

    decision = DEC_READY if all(f in fam_counts for f in FAMILIES) else DEC_PARTIAL
    report = {
        "phase": PHASE, "phase_name": PHASE_NAME, "offline": True,
        "performs_network": PERFORMS_NETWORK, "no_provider_probing": True,
        "decision": decision,
        "decision_rationale": ("Provider landscape catalogued across all five orthogonal data families "
                               "(%d providers), with entitlement overlaid from local key names (no values, "
                               "no probing). Feeds the Phase 11-B4 paid shopping cart." % len(rows)),
        "families": FAMILIES,
        "family_provider_counts": fam_counts,
        "entitled_keys_overlay": present,
        "providers": rows,
        "highest_priority_family": "A_analyst_estimates_revisions (Phase 11-A #1; free tiers too "
                                   "sparse/shallow -> paid trial needed)",
        "next_phase": "11-B2 entitled-download manifest, 11-B3 readiness gate, 11-B4 paid shopping cart",
        "safety": {
            "paper_only": True, "owned_local_data_only": True, "no_live_api_calls": True,
            "no_provider_probing": True, "no_secret_values": True, "no_orders": True,
            "no_automation": True, "no_broker": True, "no_deploy": True, "no_gcp": True,
        },
    }
    return report


def _write_json(path: Path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=2)


def _write_csv(path: Path, rows, headers):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=headers, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)


def write_artifacts(out_dir: Path, report):
    _write_json(out_dir / ("%s.json" % STEM), report)
    headers = ["provider", "family", "dataset", "access_status", "entitled_key_present", "free_tier",
               "free_trial", "payment_required", "pit_support", "historical_depth", "sp500_coverage",
               "update_frequency", "integration_difficulty", "licensing_risk", "alpha_priority",
               "requires_user_opt_in", "no_probe_performed", "public_url", "pricing_if_public"]
    _write_csv(out_dir / "provider_data_family_map.csv", report["providers"], headers)
    fam_rows = [{"family_key": k, "family_name": v, "provider_count": report["family_provider_counts"].get(k, 0)}
                for k, v in FAMILIES.items()]
    _write_csv(out_dir / "family_summary.csv", fam_rows, ["family_key", "family_name", "provider_count"])


def _print_summary(report):
    print("[%s] decision=%s  providers=%d" % (PHASE, report["decision"], len(report["providers"])))
    for k, v in report["family_provider_counts"].items():
        print("  %-42s providers=%d" % (k, v))


def run(out_dir: Path, verbose: bool = True):
    report = build_map()
    write_artifacts(out_dir, report)
    if verbose:
        _print_summary(report)
    return report


def _parse_args(argv):
    ap = argparse.ArgumentParser(description="Phase 11-B1 provider/data-family discovery map (offline).")
    ap.add_argument("--out-dir", default=str(_REPO_ROOT / "research" / "output" / STEM))
    ap.add_argument("--quiet", action="store_true")
    return ap.parse_args(argv)


def main(argv=None):
    args = _parse_args(argv or [])
    report = run(Path(args.out_dir), verbose=not args.quiet)
    return 0 if report["decision"] in ALLOWED_DECISIONS else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
