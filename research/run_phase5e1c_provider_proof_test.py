"""Phase 5-E1C - Low-Cost Fundamentals Provider Proof Test (dry-run, offline).

Track A (quant brain). Phase 5-E1B discovered that FMP Premium requires a ~$588
ANNUAL UPFRONT payment, which is not acceptable for this research experiment. FMP
is therefore dropped as the primary fundamentals provider and kept ONLY as a
negative benchmark.

This phase is a PROOF TEST, not a collector. It evaluates - offline, with NO
network calls and NO API key - whether a cheaper, self-serve provider can supply
the quarterly fundamentals the Phase 5-E2 enriched panel needs:

    * quarterly income statement
    * quarterly balance sheet
    * quarterly cash flow
    * at least 5 years of history (if available)
    * coverage for the 10 large-cap test tickers

Providers are evaluated in this priority order:
    1. SimFin
    2. EODHD
    3. Tiingo (only credited if usable WITHOUT sales / contact friction)
    4. Existing SEC / local fundamentals fallback (free, already prototyped in
       research/build_phase3h_sec_fundamental_features.py + the phase3g pipeline)

The provider facts encoded below come from vendor documentation knowledge and are
marked needs_user_confirmation - the proof test makes ZERO live calls, so pricing
and coverage are *documented expectations*, not live-verified results. An optional
--provider-notes JSON file lets the user inject confirmed pricing/coverage later
without changing code.

Hard rules honored: no live paid API calls (there is no network code in this
module at all), no package installs, no writes to D:, no Paper Trader changes, no
GCP changes, no deploy, no orders / broker execution / automation, no API key read
or written, no paid data, no binary artifacts, no commit. The committed artifacts
are small text summaries only.

Run (default = dry-run proof test, no network, no key):
    python research/run_phase5e1c_provider_proof_test.py
Test:
    python -m pytest tests/test_phase5e1c_provider_proof_test.py -q
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

PHASE = "5-E1C"

# --------------------------------------------------------------------------- #
# Paths - committed, summarized text artifacts only (no paid data, no key).
# --------------------------------------------------------------------------- #
_OUT_DIR = _REPO_ROOT / "research" / "output" / "phase5e1c_provider_proof_test"
_REPORT_OUT = _OUT_DIR / "phase5e1c_provider_proof_test.json"
_COMPARISON_OUT = _OUT_DIR / "provider_comparison.csv"
_TICKER_REQ_OUT = _OUT_DIR / "test_ticker_requirements.csv"

# --------------------------------------------------------------------------- #
# Requirements the chosen provider must satisfy for the Phase 5-E2 enriched panel.
# --------------------------------------------------------------------------- #
REQUIRED_DATA_FAMILIES = [
    "quarterly_income_statement",
    "quarterly_balance_sheet",
    "quarterly_cash_flow",
]
MIN_HISTORY_YEARS = 5

# The 10 large-cap test tickers (all current S&P 500 constituents). Coverage for
# mega/large caps is *expected* from every provider below, but this proof test does
# NOT make live calls, so coverage is an expectation, not a confirmation.
TEST_TICKERS = [
    ("AAPL", "Apple Inc."),
    ("MSFT", "Microsoft Corp."),
    ("AMZN", "Amazon.com Inc."),
    ("NVDA", "NVIDIA Corp."),
    ("JPM", "JPMorgan Chase & Co."),
    ("BAC", "Bank of America Corp."),
    ("C", "Citigroup Inc."),
    ("APH", "Amphenol Corp."),
    ("ABT", "Abbott Laboratories"),
    ("ACN", "Accenture plc"),
]

# --------------------------------------------------------------------------- #
# Recommendation vocabulary (exactly the four allowed values).
# --------------------------------------------------------------------------- #
REC_TRY_SIMFIN = "TRY_SIMFIN_FREE_OR_MONTHLY"
REC_TRY_EODHD = "TRY_EODHD_MONTHLY"
REC_USE_SEC = "USE_SEC_LOCAL_FALLBACK"
REC_STOP = "STOP_NO_PROVIDER_SELECTED"
ALLOWED_RECOMMENDATIONS = (REC_TRY_SIMFIN, REC_TRY_EODHD, REC_USE_SEC, REC_STOP)

# FMP is a negative benchmark only - never selectable.
FMP_STATUS = "rejected_due_annual_upfront"

# Pricing/coverage provenance marker - this proof test verified NOTHING live.
_PRICING_SOURCE = "vendor_documentation_knowledge_needs_user_confirmation"


# --------------------------------------------------------------------------- #
# Provider knowledge base (documented expectations; user-confirmable via notes).
#   tri-state fields use Python True / False / the string "unknown".
# --------------------------------------------------------------------------- #
def _provider_profiles() -> List[Dict]:
    return [
        {
            "provider_key": "simfin",
            "provider_name": "SimFin",
            "evaluation_order": 1,
            "is_negative_benchmark": False,
            "recommendation_token": REC_TRY_SIMFIN,
            "pricing_model": ("Free tier (API key, rate-limited, limited/lagged history) "
                              "plus SimFin+ paid MONTHLY subscription for full standardized "
                              "fundamentals; no annual lock-in required"),
            "annual_upfront_required": False,
            "monthly_option_available": True,
            "free_tier_available": True,
            "cost_model": "freemium_monthly",
            "data_families_supported": [
                "quarterly_income_statement", "quarterly_balance_sheet",
                "quarterly_cash_flow", "company_info",
            ],
            "history_years_available": "5+ (SimFin+ full history; free tier may lag/limit)",
            "api_available": True,
            "bulk_download_available": True,
            "requires_sales_contact": False,
            "implementation_friction": "low",
            "friction_notes": ("Self-serve signup with an instant API key; standardized "
                               "statement schema reduces normalization work; the free tier "
                               "lets us validate all 10 tickers BEFORE paying. An optional "
                               "'simfin' PyPI package exists but is NOT required (REST + bulk "
                               "CSV work without installing anything)."),
            "recommended_next_action": ("Create a FREE SimFin account, obtain the API key, and run "
                                        "a real 1-ticker smoke (AAPL quarterly IS/BS/CF) on the "
                                        "free tier to confirm schema + history before any payment."),
            "account_setup": ("Sign up at simfin.com -> Account -> generate API key. Store it in a "
                              "SIMFIN_API_KEY environment variable only (never commit). Free tier "
                              "needs no payment; SimFin+ is optional month-to-month."),
        },
        {
            "provider_key": "eodhd",
            "provider_name": "EODHD (EOD Historical Data)",
            "evaluation_order": 2,
            "is_negative_benchmark": False,
            "recommendation_token": REC_TRY_EODHD,
            "pricing_model": ("MONTHLY subscriptions; Fundamentals Data add-on or All-In-One "
                              "package; annual billing is an optional discount, not required"),
            "annual_upfront_required": False,
            "monthly_option_available": True,
            "free_tier_available": True,
            "cost_model": "monthly_subscription_with_demo",
            "data_families_supported": [
                "quarterly_income_statement", "quarterly_balance_sheet",
                "quarterly_cash_flow", "company_info",
            ],
            "history_years_available": "20-30y for US large caps",
            "api_available": True,
            "bulk_download_available": True,
            "requires_sales_contact": False,
            "implementation_friction": "low_to_moderate",
            "friction_notes": ("Self-serve, month-to-month billing. Fundamentals is a paid add-on; "
                               "a limited demo token covers a handful of tickers (e.g. AAPL.US) so "
                               "the schema can be smoke-tested before subscribing. Per-ticker REST "
                               "plus a bulk fundamentals endpoint."),
            "recommended_next_action": ("Smoke-test the schema with the demo token on AAPL.US, then "
                                        "subscribe MONTHLY to the Fundamentals feed only if SimFin "
                                        "does not clear the proof."),
            "account_setup": ("Register at eodhd.com -> get API token. Store as EODHD_API_KEY env "
                              "var only. Demo token is free; Fundamentals add-on is month-to-month."),
        },
        {
            "provider_key": "tiingo",
            "provider_name": "Tiingo",
            "evaluation_order": 3,
            "is_negative_benchmark": False,
            # Tiingo has NO dedicated recommendation token (per the allowed vocab); it
            # is profiled as a self-serve alternative only, credited just because it is
            # usable without sales/contact friction.
            "recommendation_token": None,
            "pricing_model": ("Free EOD-price tier plus a low MONTHLY 'Power' plan that unlocks the "
                              "fundamentals add-on; self-serve, no annual lock-in"),
            "annual_upfront_required": False,
            "monthly_option_available": True,
            "free_tier_available": True,
            "cost_model": "monthly_addon",
            "data_families_supported": [
                "quarterly_income_statement", "quarterly_balance_sheet",
                "quarterly_cash_flow", "daily_fundamentals",
            ],
            "history_years_available": "5+ for covered names (coverage skewed to larger caps)",
            "api_available": True,
            "bulk_download_available": False,
            "requires_sales_contact": False,
            "usable_without_sales_friction": True,
            "implementation_friction": "moderate",
            "friction_notes": ("Self-serve (no sales contact), but the fundamentals add-on requires "
                               "accepting a separate data-license click-through and coverage is "
                               "narrower/large-cap-skewed; per-ticker REST 'statements' endpoint, "
                               "no true bulk download. Ranked third behind SimFin/EODHD."),
            "recommended_next_action": ("Only if BOTH SimFin and EODHD fail the proof: enable the "
                                        "Tiingo Power plan, accept the fundamentals add-on terms, "
                                        "and smoke-test the statements endpoint for the 10 tickers."),
            "account_setup": ("Register at tiingo.com -> API token; enable Power plan + accept the "
                              "fundamentals add-on terms. Store token as TIINGO_API_KEY env var only."),
        },
        {
            "provider_key": "sec_local",
            "provider_name": "SEC EDGAR (existing local fallback)",
            "evaluation_order": 4,
            "is_negative_benchmark": False,
            "recommendation_token": REC_USE_SEC,
            "pricing_model": "Free public data (SEC EDGAR XBRL companyfacts) - no payment of any kind",
            "annual_upfront_required": False,
            # Free public data: there is no monthly charge because there is no charge.
            "monthly_option_available": False,
            "free_tier_available": True,
            "cost_model": "free_public_data",
            "data_families_supported": [
                "quarterly_income_statement", "quarterly_balance_sheet",
                "quarterly_cash_flow", "company_info",
            ],
            "history_years_available": "5+ (full EDGAR XBRL history)",
            "api_available": True,
            "bulk_download_available": True,
            "requires_sales_contact": False,
            "implementation_friction": "moderate",
            "friction_notes": ("Free, key-less companyfacts JSON API + bulk ZIP. Needs XBRL "
                               "tag->field mapping and point-in-time normalization, but that work "
                               "is ALREADY PROTOTYPED in this repo: "
                               "research/prototype_phase3g_sec_fundamentals_minipipeline.py and "
                               "research/build_phase3h_sec_fundamental_features.py. Zero cost, "
                               "zero vendor risk."),
            "recommended_next_action": ("If every paid provider is rejected, extend the existing "
                                        "phase3g/phase3h SEC fundamentals pipeline to the 10 test "
                                        "tickers - no key, no payment, no new dependency."),
            "account_setup": ("None. SEC EDGAR is public; only a descriptive User-Agent header is "
                              "requested by SEC. No key, no account, no payment."),
        },
        {
            "provider_key": "fmp",
            "provider_name": "FMP (Financial Modeling Prep)",
            "evaluation_order": 99,
            "is_negative_benchmark": True,
            "recommendation_token": None,
            "provider_status": FMP_STATUS,
            "pricing_model": ("Premium fundamentals require a ~$588 ANNUAL UPFRONT payment "
                              "(discovered in Phase 5-E1B) - unacceptable for this experiment"),
            "annual_upfront_required": True,
            "monthly_option_available": "unknown",
            "free_tier_available": False,
            "cost_model": "annual_upfront",
            "data_families_supported": [
                "quarterly_income_statement", "quarterly_balance_sheet",
                "quarterly_cash_flow", "company_info",
            ],
            "history_years_available": "5+ (but gated behind the annual-upfront premium tier)",
            "api_available": True,
            "bulk_download_available": False,
            "requires_sales_contact": False,
            "implementation_friction": "blocked_by_pricing",
            "friction_notes": ("Adapter already built in Phase 5-E0/5-E1, but the needed tier is "
                               "annual-upfront only. Retained ONLY as a negative cost benchmark."),
            "recommended_next_action": ("Do NOT use FMP as the primary fundamentals provider. "
                                        "Negative benchmark only."),
            "account_setup": "n/a - rejected.",
        },
    ]


def _annotate(profile: Dict) -> Dict:
    """Derive proof-gate fields from the documented facts (pure, deterministic)."""
    p = dict(profile)
    supported = set(p.get("data_families_supported", []))
    p["supports_all_quarterly_statements"] = all(
        fam in supported for fam in REQUIRED_DATA_FAMILIES)

    hist = str(p.get("history_years_available", "")).lower()
    p["meets_min_history_expectation"] = ("5+" in hist or "20" in hist or "30" in hist)

    api_or_bulk = bool(p.get("api_available")) or bool(p.get("bulk_download_available"))
    p["api_or_bulk_download_available"] = api_or_bulk

    has_low_cost_path = (p.get("free_tier_available") is True
                         or p.get("monthly_option_available") is True)

    # A provider PASSES the proof gate when it can plausibly supply the required
    # fundamentals through a self-serve, no-annual-upfront, no-sales-contact path.
    p["passes_proof_gate"] = (
        not p.get("is_negative_benchmark", False)
        and p["supports_all_quarterly_statements"]
        and p.get("annual_upfront_required") is False
        and has_low_cost_path
        and p.get("requires_sales_contact") is False
        and api_or_bulk
    )
    p["pricing_source"] = _PRICING_SOURCE
    return p


def _apply_notes(profiles: List[Dict], notes: Optional[Dict]) -> List[Dict]:
    """Optionally override documented facts with user-confirmed notes (by key).

    notes shape: {"providers": {"simfin": {<field>: <value>, ...}, ...}}.
    Network-free; only overrides plain fields. Absent / malformed -> no-op.
    """
    if not notes:
        return profiles
    overrides = (notes or {}).get("providers", {}) or {}
    out = []
    for p in profiles:
        ov = overrides.get(p.get("provider_key"))
        if isinstance(ov, dict):
            merged = dict(p)
            merged.update(ov)
            merged["pricing_source"] = "user_provided_notes"
            out.append(merged)
        else:
            out.append(p)
    return out


# --------------------------------------------------------------------------- #
# Small IO helpers (stdlib only; identical conventions to the 5-E1 runner).
# --------------------------------------------------------------------------- #
def _rel(path: Path) -> str:
    try:
        return str(Path(path).resolve().relative_to(_REPO_ROOT)).replace("\\", "/")
    except ValueError:
        return str(path).replace("\\", "/")


def _write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=2, allow_nan=False)
        fh.write("\n")


def _write_csv(path: Path, header: Sequence[str], rows: Sequence[Sequence]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        w.writerows(rows)


# --------------------------------------------------------------------------- #
# Recommendation
# --------------------------------------------------------------------------- #
def derive_recommendation(profiles: List[Dict]) -> Dict:
    by_key = {p["provider_key"]: p for p in profiles}
    order = [("simfin", REC_TRY_SIMFIN), ("eodhd", REC_TRY_EODHD), ("sec_local", REC_USE_SEC)]
    for key, token in order:
        prof = by_key.get(key)
        if prof and prof.get("passes_proof_gate"):
            return {
                "recommendation": token,
                "selected_provider_key": key,
                "selected_provider_name": prof["provider_name"],
                "reason": ("%s is the first provider (in priority order) that supports all three "
                           "quarterly statements through a self-serve, no-annual-upfront path "
                           "(free tier and/or monthly), with no sales contact required."
                           % prof["provider_name"]),
            }
    return {
        "recommendation": REC_STOP,
        "selected_provider_key": None,
        "selected_provider_name": None,
        "reason": ("No evaluated provider cleared the proof gate (all-three-statements + "
                   "no-annual-upfront + low-cost-path + no-sales-contact + API/bulk). "
                   "Confirm pricing via --provider-notes and re-run before selecting."),
    }


# --------------------------------------------------------------------------- #
# Ticker requirements artifact
# --------------------------------------------------------------------------- #
def _ticker_requirement_rows() -> List[List]:
    rows = []
    for ticker, name in TEST_TICKERS:
        rows.append([
            ticker, name, "yes",
            "required", "required", "required",
            MIN_HISTORY_YEARS,
            "expected_covered_large_cap_not_live_verified",
        ])
    return rows


# --------------------------------------------------------------------------- #
# Main run (offline, no network, no key)
# --------------------------------------------------------------------------- #
def run(out_dir: Optional[Path] = None, provider_notes: Optional[Dict] = None,
        verbose: bool = True) -> Dict:
    out_dir = Path(out_dir) if out_dir is not None else _OUT_DIR
    report_out = out_dir / _REPORT_OUT.name
    comparison_out = out_dir / _COMPARISON_OUT.name
    ticker_req_out = out_dir / _TICKER_REQ_OUT.name

    profiles = [_annotate(p) for p in _apply_notes(_provider_profiles(), provider_notes)]
    evaluated = [p for p in profiles if not p.get("is_negative_benchmark")]
    fmp = next(p for p in profiles if p.get("is_negative_benchmark"))

    rec = derive_recommendation(profiles)

    free_test_possible = any(
        p.get("free_tier_available") is True for p in evaluated)
    monthly_only_payment_possible = any(
        (p.get("annual_upfront_required") is False and p.get("monthly_option_available") is True)
        for p in evaluated)

    # ----- provider_comparison.csv -----
    comparison_cols = [
        "evaluation_order", "provider_name", "provider_status", "pricing_model",
        "annual_upfront_required", "monthly_option_available", "free_tier_available",
        "data_families_supported", "supports_all_quarterly_statements",
        "history_years_available", "meets_min_history_expectation",
        "api_or_bulk_download_available", "implementation_friction",
        "requires_sales_contact", "passes_proof_gate", "recommended",
        "recommended_next_action", "pricing_source",
    ]
    ordered = sorted(profiles, key=lambda p: p["evaluation_order"])
    comparison_rows = []
    for p in ordered:
        status = p.get("provider_status") or (
            "recommended" if p["provider_key"] == rec["selected_provider_key"]
            else ("viable" if p.get("passes_proof_gate") else "evaluated"))
        comparison_rows.append([
            p["evaluation_order"], p["provider_name"], status, p["pricing_model"],
            p["annual_upfront_required"], p["monthly_option_available"],
            p.get("free_tier_available"),
            "; ".join(p.get("data_families_supported", [])),
            p.get("supports_all_quarterly_statements"),
            p.get("history_years_available"), p.get("meets_min_history_expectation"),
            p.get("api_or_bulk_download_available"), p["implementation_friction"],
            p.get("requires_sales_contact"), p.get("passes_proof_gate", False),
            (p["provider_key"] == rec["selected_provider_key"]),
            p["recommended_next_action"], p.get("pricing_source"),
        ])
    _write_csv(comparison_out, comparison_cols, comparison_rows)

    # ----- test_ticker_requirements.csv -----
    ticker_cols = [
        "ticker", "company", "in_sp500",
        "required_quarterly_income_statement", "required_quarterly_balance_sheet",
        "required_quarterly_cash_flow", "required_min_years_history",
        "coverage_expectation",
    ]
    _write_csv(ticker_req_out, ticker_cols, _ticker_requirement_rows())

    # ----- phase5e1c_provider_proof_test.json -----
    report = {
        "phase": PHASE,
        "title": "Low-Cost Fundamentals Provider Proof Test",
        "mode": "dry_run_proof_test",
        "network_used": False,
        "api_key_required": False,
        "api_key_used": False,
        "no_paid_data_written": True,
        "no_binary_artifacts": True,
        "provider_priority_order": ["simfin", "eodhd", "tiingo", "sec_local"],
        "required_data_families": REQUIRED_DATA_FAMILIES,
        "min_history_years": MIN_HISTORY_YEARS,
        "test_tickers": [t for t, _ in TEST_TICKERS],
        "providers": ordered,
        "fmp_negative_benchmark": {
            "provider_name": fmp["provider_name"],
            "status": FMP_STATUS,
            "annual_upfront_required": fmp["annual_upfront_required"],
            "reason": fmp["friction_notes"],
        },
        "recommendation": rec["recommendation"],
        "selected_provider_key": rec["selected_provider_key"],
        "selected_provider_name": rec["selected_provider_name"],
        "recommendation_reason": rec["reason"],
        "allowed_recommendations": list(ALLOWED_RECOMMENDATIONS),
        "free_test_possible": free_test_possible,
        "monthly_only_payment_possible": monthly_only_payment_possible,
        "account_setup_by_provider": {
            p["provider_key"]: p.get("account_setup") for p in evaluated},
        "next_human_action": (
            "Create a FREE SimFin account, generate its API key (store only in a "
            "SIMFIN_API_KEY env var), and run a real 1-ticker free-tier smoke for AAPL "
            "(quarterly income/balance/cash-flow) to confirm schema + 5y history BEFORE "
            "building any collector or paying anything."),
        "next_command": "python research\\run_phase5e1c_provider_proof_test.py",
        "artifacts": {
            "report_json": _rel(report_out),
            "provider_comparison_csv": _rel(comparison_out),
            "test_ticker_requirements_csv": _rel(ticker_req_out),
        },
        # Safety contract (identical posture to the rest of Track A).
        "preview_only": True,
        "orders_enabled": False,
        "automation_enabled": False,
        "broker_execution_enabled": False,
        "production_replacement": False,
        "writes_to_d_drive": False,
        "modifies_paper_trader": False,
        "modifies_gcp": False,
        "installs_packages": False,
        "builds_collector": False,
    }
    assert report["recommendation"] in ALLOWED_RECOMMENDATIONS
    _write_json(report_out, report)

    if verbose:
        _print_summary(report, ordered)
    return report


def _print_summary(report: Dict, ordered: List[Dict]) -> None:
    print("phase:                         %s" % report["phase"])
    print("mode:                          %s (network_used=%s, api_key_required=%s)" % (
        report["mode"], report["network_used"], report["api_key_required"]))
    print("required families:             %s" % ", ".join(report["required_data_families"]))
    print("min history years:             %s" % report["min_history_years"])
    print("test tickers:                  %s" % ", ".join(report["test_tickers"]))
    print("")
    print("provider proof-gate results (priority order):")
    for p in ordered:
        if p.get("is_negative_benchmark"):
            print("  - %-26s NEGATIVE BENCHMARK (%s, annual_upfront=%s)" % (
                p["provider_name"], p.get("provider_status"), p["annual_upfront_required"]))
            continue
        print("  - %-26s passes=%s  annual_upfront=%s  monthly=%s  free=%s  friction=%s" % (
            p["provider_name"], p.get("passes_proof_gate"), p["annual_upfront_required"],
            p["monthly_option_available"], p.get("free_tier_available"),
            p["implementation_friction"]))
    print("")
    print("RECOMMENDATION:                %s" % report["recommendation"])
    print("selected provider:             %s" % report["selected_provider_name"])
    print("reason:                        %s" % report["recommendation_reason"])
    print("free test possible:            %s" % report["free_test_possible"])
    print("monthly-only payment possible: %s" % report["monthly_only_payment_possible"])
    print("FMP:                           %s (annual upfront ~$588, NOT used)" % FMP_STATUS)
    print("next human action:             %s" % report["next_human_action"])
    print("artifacts:")
    for name, rel in report["artifacts"].items():
        print("  - %-28s %s" % (name, rel))


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        description="Phase 5-E1C low-cost fundamentals provider proof test "
                    "(dry-run, offline, no key).")
    ap.add_argument("--provider-notes", type=str, default="",
                    help="Optional path to a JSON file of user-confirmed pricing/coverage "
                         "overrides ({\"providers\": {\"simfin\": {...}}}). Read-only; no network.")
    return ap.parse_args(argv)


def _load_notes(path_str: str) -> Optional[Dict]:
    if not path_str:
        return None
    path = Path(path_str)
    if not path.is_file():
        print("provider-notes file not found (ignored): %s" % path)
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (ValueError, OSError) as exc:
        print("provider-notes file unreadable (ignored): %s" % exc)
        return None


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parse_args(argv)
    notes = _load_notes(args.provider_notes)
    run(provider_notes=notes, verbose=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
