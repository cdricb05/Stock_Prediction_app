"""Phase 11-B4 - Paid-Data Acquisition Shopping Cart.

WHY THIS PHASE EXISTS
    Phases 11-B0 -> 11-C established that the free / currently-entitled orthogonal data on disk does NOT
    beat the modest composite_sn baseline, and Phase 11-B3 gated the result as NEEDS_PAID_DATA. This phase
    turns that into a concrete, ranked purchase / trial list for the highest-priority family - analyst
    estimate revisions (Phase 11-A #1) - with exact fields, minimum download scope, expected volume, cost
    range, delivery, signup steps, and post-trial rejection criteria. It is the terminal ACTION_REQUIRED
    deliverable of the autonomous acquisition queue: a bounded paid trial requires explicit user opt-in.

    It performs NO payment, NO signup, NO provider probing, and NO API call. Every provider row is a design
    recommendation flagged requires_user_opt_in.

DECISIONS (allowed)
    ACTION_REQUIRED_ANALYST_REVISIONS_TRIAL | ACTION_REQUIRED_SHORT_INTEREST_TRIAL |
    ACTION_REQUIRED_OPTIONS_TRIAL | ACTION_REQUIRED_MULTI_VENDOR_QUOTES | NO_PAID_DATA_RECOMMENDED

CONSTRAINTS HONORED
    Offline (embedded design + os.environ name-presence overlay); no payment; no signup; no provider
    probing; no api calls; no secret values; no Paper Trader writes; NO orders / automation / broker /
    deploy / GCP; output is research metadata (JSON + CSV) only. Commit only phase11b4 files if tests
    pass. No push.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]

PHASE = "11-B4"
PHASE_NAME = "Paid-Data Acquisition Shopping Cart"
STEM = "phase11b4_paid_data_shopping_cart"
PERFORMS_NETWORK = False

DEC_ANALYST = "ACTION_REQUIRED_ANALYST_REVISIONS_TRIAL"
DEC_SHORT = "ACTION_REQUIRED_SHORT_INTEREST_TRIAL"
DEC_OPTIONS = "ACTION_REQUIRED_OPTIONS_TRIAL"
DEC_MULTI = "ACTION_REQUIRED_MULTI_VENDOR_QUOTES"
DEC_NONE = "NO_PAID_DATA_RECOMMENDED"
ALLOWED_DECISIONS = (DEC_ANALYST, DEC_SHORT, DEC_OPTIONS, DEC_MULTI, DEC_NONE)

# The 16 point-in-time analyst-revision fields (carried from Phase 11-A).
REQUIRED_FIELDS = [
    "eps_estimate_cfy", "eps_estimate_nfy", "eps_estimate_quarter", "revenue_estimate",
    "num_analysts", "up_revisions_count", "down_revisions_count", "estimate_change_7d",
    "estimate_change_30d", "estimate_change_60d", "consensus_estimate_level", "estimate_dispersion",
    "recommendation_changes", "price_target_changes", "pit_effective_date", "revision_timestamp",
]

# Ranked shopping cart. tier: 1 must-try-first, 2 second choice, 3 enterprise/too-expensive, 4 not-now.
CART = [
    {
        "rank": 1, "tier": "must_try_first", "provider": "FMP (Financial Modeling Prep) Premium tier",
        "family": "analyst_estimate_revisions", "dataset": "Analyst Estimates + Historical + Ratings/Targets",
        "owned_key_env": "FMP_API_KEY", "expected_cost": "~$22-70/mo (~$264-840/yr)",
        "quote_required": False, "free_trial": True, "delivery": "REST API / CSV",
        "min_download_scope": "S&P 500 + expanded (~545 names), monthly snapshots 2010-2026",
        "expected_rows": "~650k-1M", "expected_storage": "<1 GB", "historical_depth_needed": ">=10yr",
        "reason": "Owned key already present -> lowest-friction paid path; upgrading the tier unlocks "
                  "universe-wide analyst estimates + history + revisions to run the first bounded screen.",
        "alpha_test_enabled": "net-revisions momentum (up-down)/n + 30/60d estimate change, sector-neutral, "
                              "63d incremental to composite_sn",
        "caveat": "PIT / revision-timestamp fidelity weaker than Zacks; acceptable for a first screen, "
                  "escalate to rank 2 if promising-but-PIT-fragile.",
        "signup_steps": "Upgrade the existing FMP account to a paid tier; reuse FMP_API_KEY; pull "
                        "/api/v3/analyst-estimates + /stable/analyst-estimates historical.",
    },
    {
        "rank": 2, "tier": "second_choice", "provider": "Nasdaq Data Link - Zacks (ZACKS/EE + ZACKS/ER)",
        "family": "analyst_estimate_revisions",
        "dataset": "Zacks Earnings Estimates + Estimate Revisions History",
        "owned_key_env": "NASDAQ_DATA_LINK_API_KEY", "expected_cost": "~$1-3k/yr (subscription; quote)",
        "quote_required": True, "free_trial": True, "delivery": "API / CSV / bulk export",
        "min_download_scope": "S&P 500 + expanded (~545 names), daily/monthly PIT revisions 2005-2026",
        "expected_rows": "~5-15M", "expected_storage": "~2-6 GB", "historical_depth_needed": ">=15yr",
        "reason": "Best point-in-time revision history at an affordable (non-enterprise) price; owned "
                  "Nasdaq Data Link key reduces signup. Gold-ish PIT for the revision-timestamp fields.",
        "alpha_test_enabled": "PIT-clean net-revisions momentum + diffusion/breadth, sector-neutral, "
                              "5d/21d/63d incremental to composite_sn with the AC8 subperiod-net25 guard",
        "caveat": "Redistribution-restricted license; subscription quote required.",
        "signup_steps": "Start a Nasdaq Data Link Zacks trial; reuse NASDAQ_DATA_LINK_API_KEY; pull "
                        "ZACKS/EE (estimates) + ZACKS/ER (revisions) for the universe.",
    },
    {
        "rank": 2, "tier": "second_choice", "provider": "Intrinio - Zacks Estimates/Trends",
        "family": "analyst_estimate_revisions", "dataset": "Zacks EPS/Sales Estimates + Estimate Trends",
        "owned_key_env": "INTRINIO_API_KEY", "expected_cost": "~$1-3k/yr tiered",
        "quote_required": False, "free_trial": True, "delivery": "REST API / CSV / bulk",
        "min_download_scope": "S&P 500 + expanded (~545 names), PIT estimate trends 2010-2026",
        "expected_rows": "~5-15M", "expected_storage": "~2-6 GB", "historical_depth_needed": ">=15yr",
        "reason": "Alternative affordable PIT Zacks source with clean REST delivery; use if Nasdaq Data "
                  "Link licensing/quote is a blocker.",
        "alpha_test_enabled": "same as Nasdaq Data Link Zacks",
        "caveat": "No Intrinio key present yet -> requires new signup.",
        "signup_steps": "Create an Intrinio trial; obtain INTRINIO_API_KEY; pull Zacks estimate-trends "
                        "endpoints for the universe.",
    },
    {
        "rank": 3, "tier": "enterprise_too_expensive", "provider": "LSEG I/B/E/S",
        "family": "analyst_estimate_revisions", "dataset": "I/B/E/S detail + summary estimates/revisions",
        "owned_key_env": None, "expected_cost": ">$10k/yr (enterprise; quote)", "quote_required": True,
        "free_trial": False, "delivery": "enterprise feed", "min_download_scope": "universe, 40yr PIT",
        "expected_rows": ">50M", "expected_storage": ">20 GB", "historical_depth_needed": ">=20yr",
        "reason": "Gold-standard PIT estimates/revisions but enterprise contract + integration cost far "
                  "exceed the value of a single 63d screen. Only if a paid pilot already shows strong alpha.",
        "alpha_test_enabled": "definitive PIT revision study", "caveat": "enterprise contract; high effort.",
        "signup_steps": "Contact LSEG sales for I/B/E/S; enterprise onboarding.",
    },
    {
        "rank": 4, "tier": "not_recommended_now", "provider": "Options IV (ORATS/OptionMetrics)",
        "family": "options_implied_vol", "dataset": "historical IV surfaces / skew",
        "owned_key_env": None, "expected_cost": "$100-600/mo (ORATS) / enterprise (OptionMetrics)",
        "quote_required": True, "free_trial": True, "delivery": "API / bulk",
        "min_download_scope": "universe daily surfaces", "expected_rows": ">50M", "expected_storage": ">10 GB",
        "historical_depth_needed": ">=10yr",
        "reason": "Deferred: options signals decay fast (best 5-21d, weak at the 63d decision horizon) and "
                  "are the most expensive + highest-integration family. Revisit only after analyst revisions.",
        "alpha_test_enabled": "IV-skew / term-structure at short horizons",
        "caveat": "wrong horizon for the quarterly book; heavy build.",
        "signup_steps": "n/a (deferred).",
    },
    {
        "rank": 4, "tier": "not_recommended_now", "provider": "Short interest / 13F / news sentiment",
        "family": "mixed", "dataset": "SI (ORTEX/S&P), 13F (WhaleWisdom), news (RavenPack/Benzinga)",
        "owned_key_env": None, "expected_cost": "varies", "quote_required": True, "free_trial": True,
        "delivery": "API", "min_download_scope": "universe", "expected_rows": "varies",
        "expected_storage": "varies", "historical_depth_needed": ">=10yr",
        "reason": "Deferred: short interest already rejected (10-A + 11-C), 13F has a 45-day PIT lag "
                  "(worst timeliness), owned EODHD news sentiment was weak in the 8-series.",
        "alpha_test_enabled": "n/a (low prior)", "caveat": "low expected value.",
        "signup_steps": "n/a (deferred).",
    },
]

# Post-trial rejection criteria (carried from Phase 11-A AC1-AC10 + the 11-C strict relative test).
REJECTION_CRITERIA = [
    {"criterion_id": "RC1_ingest_pit", "rule": "reject if the feed lacks a usable pit_effective_date / "
     "revision_timestamp for a zero-look-ahead as-of join"},
    {"criterion_id": "RC2_coverage", "rule": "reject if universe coverage < ~90% of the 545-name panel or "
     "median per-name history < 24 months"},
    {"criterion_id": "RC3_standalone", "rule": "reject if the sector-neutral revision-momentum factor has "
     "no positive 63d IC with |t| that clears the same bar the quality legs did"},
    {"criterion_id": "RC4_incremental", "rule": "reject if composite_sn + w*revision does not beat "
     "composite_sn net-25bps under the 11-C strict relative test"},
    {"criterion_id": "RC5_cost", "rule": "reject if the edge is turnover cost-killed at 25/50 bps"},
    {"criterion_id": "RC6_oos", "rule": "reject if walk-forward pooled OOS IC is not positive with "
     "frac-windows-positive >= baseline"},
    {"criterion_id": "RC7_subperiod_net25", "rule": "reject unless the net-25bps improvement over baseline "
     "survives in BOTH pre-2020 and post-2020 (the 10-N/10-O/11-C subperiod guard)"},
    {"criterion_id": "RC8_no_overfit", "rule": "reject post-hoc winners: no single-quarter / single-sector "
     "driver; both cohorts positive; concentration not worse than baseline"},
]


def build_cart():
    present = {}
    for item in CART:
        env = item.get("owned_key_env")
        if env:
            present[env] = env in os.environ
    rank1 = [c for c in CART if c["rank"] == 1]
    decision = DEC_ANALYST if any(c["family"] == "analyst_estimate_revisions" for c in rank1) else DEC_MULTI
    report = {
        "phase": PHASE, "phase_name": PHASE_NAME, "offline": True,
        "performs_network": PERFORMS_NETWORK, "no_payment_submitted": True, "no_signup_performed": True,
        "decision": decision,
        "decision_rationale": (
            "Free / currently-entitled orthogonal data does not beat composite_sn (11-C) and the priority "
            "family is paid-gated (11-B3). The first bounded paid trial should be ANALYST ESTIMATE "
            "REVISIONS: rank-1 = FMP Premium upgrade (owned key -> lowest friction, ~$22-70/mo); rank-2 = "
            "Nasdaq Data Link Zacks / Intrinio for PIT-grade revision history. Requires explicit user "
            "opt-in; no payment/signup performed here."),
        "recommended_family": "analyst_estimate_revisions",
        "required_fields": REQUIRED_FIELDS,
        "cart": CART,
        "owned_key_overlay": present,
        "rejection_criteria": REJECTION_CRITERIA,
        "trial_plan_ref": "Phase 11-A AC1-AC10 + Phase 11-C strict relative beat test + subperiod-net25 guard",
        "baseline_to_beat": {"signal": "composite_sn", "ic_t": 2.665, "quarterly_net_25bps": 0.00401,
                             "quarterly_net_50bps": 0.00095},
        "next_phase": "On user opt-in: acquire rank-1 trial, ingest offline, run Phase 11-C-style test; "
                      "else baseline composite_sn stays the paper-review candidate.",
        "safety": {
            "paper_only": True, "owned_local_data_only": True, "no_live_api_calls": True,
            "no_payment_submitted": True, "no_signup_performed": True, "no_orders": True,
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
    _write_csv(out_dir / "shopping_cart.csv", report["cart"],
               ["rank", "tier", "provider", "family", "dataset", "owned_key_env", "expected_cost",
                "quote_required", "free_trial", "delivery", "min_download_scope", "expected_rows",
                "expected_storage", "historical_depth_needed", "reason", "alpha_test_enabled", "caveat",
                "signup_steps"])
    _write_csv(out_dir / "required_fields.csv",
               [{"field_key": f, "pit_required": True} for f in report["required_fields"]],
               ["field_key", "pit_required"])
    _write_csv(out_dir / "rejection_criteria.csv", report["rejection_criteria"],
               ["criterion_id", "rule"])


def _print_summary(report):
    print("[%s] decision=%s" % (PHASE, report["decision"]))
    for c in report["cart"]:
        print("  rank%d %-16s %-42s %s" % (c["rank"], c["tier"], c["provider"], c["expected_cost"]))


def run(out_dir: Path, verbose: bool = True):
    report = build_cart()
    write_artifacts(out_dir, report)
    if verbose:
        _print_summary(report)
    return report


def _parse_args(argv):
    ap = argparse.ArgumentParser(description="Phase 11-B4 paid-data shopping cart (offline design).")
    ap.add_argument("--out-dir", default=str(_REPO_ROOT / "research" / "output" / STEM))
    ap.add_argument("--quiet", action="store_true")
    return ap.parse_args(argv)


def main(argv=None):
    args = _parse_args(argv or [])
    report = run(Path(args.out_dir), verbose=not args.quiet)
    return 0 if report["decision"] in ALLOWED_DECISIONS else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
