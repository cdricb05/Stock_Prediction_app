"""Phase 10-Q - Owned Data Exhaustion And Research Decision.

WHY THIS PHASE EXISTS
    The autonomous owned-data alpha-improvement queue is complete and every avenue for a STRONGER alpha
    than the frozen 10-D quality baseline has failed out-of-sample:
      * 10-L-B  reweighting / z-cap / winsorize / liquidity / sector-cap  -> REJECT_REWEIGHTING_OVERFIT
      * 10-M    incremental owned fundamental factors (profitability/investment/leverage) -> BASELINE_REMAINS_CHAMPION
      * 10-N    nonlinear transforms + quality/value/leverage interactions -> REJECT_TRANSFORM_OVERFIT
      * 10-O    regime / conditional gating (macro / vol / dispersion / liquidity) -> REJECT_REGIME_OVERFIT
    No candidate beat the baseline on honest, cost-robust, era-stable evidence. Phase 10-P (paper-rule
    packaging of a NEW winner) is therefore skipped - there is no new winner. Phase 10-Q writes the final,
    honest research decision.

    This is a SYNTHESIS phase: it reads the frozen owned prior-phase decision JSONs (10-D baseline +
    10-L-B / 10-M / 10-N / 10-O) and aggregates them into one decision. It builds no panel, fits nothing,
    and makes no network call.

THE HONEST PICTURE
    The 10-D two-leg quality composite (long fcf_to_assets, short operating_accruals, equal-weight,
    sector-neutral, 63d) is a REAL but MODEST / BOUNDARY alpha (63d IC t ~2.665, below the 3.0 strong
    bar; small net-of-cost quarterly edge; the accruals short leg carries most of the robustness). It
    already passed the strict 10-D gate (CONFIRMED_READY_FOR_PAPER_REVIEW) and was already packaged into a
    paper-only review + position-tracker line (10-E -> 10-I). Nothing in 10-L-B / 10-M / 10-N / 10-O
    improved on it. The owned EODHD fundamentals + owned FRED/benchmark regimes are exhausted for a
    stronger edge. The one remaining OWNED avenue is a PIT-reconstructed VALUE leg (earnings_yield /
    book_to_market from owned prices + equity) - but the 10-N value INTERACTIONS were already wrong-signed
    / insignificant at 63d in this universe, so a value leg is unlikely to help and would need a careful
    PIT market-cap join. A genuinely stronger alpha most likely needs NEW ORTHOGONAL data (analyst
    estimate-revisions, short interest, options-implied, richer sentiment) - which requires paid feeds not
    present in this shell (per 10-A / 8-Y).

DECISION
    PACKAGE_MODEST_BASELINE_FOR_PAPER_REVIEW | PAUSE_ALPHA_RESEARCH_NEEDS_NEW_DATA |
    NEEDS_DATA_REFRESH_BEFORE_DECISION

CONSTRAINTS HONORED
    Fully offline (no network / key / provider probe); owned/local data only (reads prior-phase output
    JSONs); no new data; no Paper Trader writes; no orders; no automation; no broker; no deploy; no GCP;
    no package install; targeted tests only; output is research metadata only. No commit. No push.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from research import run_phase8x_autonomous_strong_alpha_discovery as x8   # noqa: E402

s8 = x8.s8
t8 = x8.t8
_write_json = s8._write_json
_write_csv = s8._write_csv
_read_json = s8._read_json
_round = s8._round

PHASE = "10-Q"
PHASE_NAME = "Owned Data Exhaustion And Research Decision"
STEM = "phase10q_owned_data_exhaustion_research_decision"
PERFORMS_NETWORK = False
_OUT = _REPO_ROOT / "research" / "output"

DEC_PACKAGE = "PACKAGE_MODEST_BASELINE_FOR_PAPER_REVIEW"
DEC_PAUSE = "PAUSE_ALPHA_RESEARCH_NEEDS_NEW_DATA"
DEC_REFRESH = "NEEDS_DATA_REFRESH_BEFORE_DECISION"
ALLOWED_DECISIONS = (DEC_PACKAGE, DEC_PAUSE, DEC_REFRESH)

# The improvement avenues (owned-data), in queue order, with the decision fields that mean "did not beat
# the baseline" vs "found a stronger winner".
_WINNER_DECISIONS = {
    "REWEIGHTED_ALPHA_READY_FOR_PAPER_RULES", "INCREMENTAL_ALPHA_READY_FOR_PAPER_RULES",
    "TRANSFORMED_ALPHA_READY_FOR_PAPER_RULES", "CONDITIONAL_ALPHA_READY_FOR_PAPER_RULES",
}

AVENUES: Tuple[Dict, ...] = (
    {"phase": "10-L-B", "avenue": "two-factor reweighting / z-cap / winsorize / liquidity / sector-cap",
     "dir": "phase10l_quality_composite_reweighting_robustness_backtest",
     "json": "phase10l_quality_composite_reweighting_robustness_backtest.json"},
    {"phase": "10-M", "avenue": "incremental owned fundamental factors (profitability/investment/leverage)",
     "dir": "phase10m_owned_fundamental_incremental_alpha_expansion",
     "json": "phase10m_owned_fundamental_incremental_alpha_expansion.json"},
    {"phase": "10-N", "avenue": "nonlinear transforms + quality/value/leverage interactions",
     "dir": "phase10n_fundamental_transformation_interaction_search",
     "json": "phase10n_fundamental_transformation_interaction_search.json"},
    {"phase": "10-O", "avenue": "regime / conditional gating (macro / vol / dispersion / liquidity)",
     "dir": "phase10o_regime_conditional_alpha_gating",
     "json": "phase10o_regime_conditional_alpha_gating.json"},
)

_BASELINE_JSON = (_OUT / "phase10d_quarterly_quality_composite_validation"
                  / "phase10d_quarterly_quality_composite_validation.json")

# Owned-data families and their exhaustion status for a STRONGER alpha.
OWNED_DATA_STATUS: Tuple[Tuple[str, str, str], ...] = (
    ("EODHD fundamentals - quality legs (fcf_to_assets, operating_accruals)", "EXHAUSTED (baseline)",
     "the confirmed 10-D two-leg composite; reweighting (10-L-B) does not improve it"),
    ("EODHD fundamentals - profitability (gross_prof, roa, op_margin, cash_roa)", "EXHAUSTED",
     "10-M: 7/8 wrong-signed or sign-unstable at 63d; the one eligible (cash_roa) dilutes the spread"),
    ("EODHD fundamentals - investment (asset_growth, net_share_issuance)", "EXHAUSTED",
     "10-M: wrong-signed / sign-unstable at 63d"),
    ("EODHD fundamentals - leverage (leverage_change, debt_to_assets)", "EXHAUSTED",
     "10-M: wrong-signed / sign-unstable at 63d"),
    ("Nonlinear transforms of owned factors (signed-log / rank / sn-rank / delta)", "EXHAUSTED",
     "10-N: the only full-sample winner (rank composite) is a pre-2020 relic that reverses post-2020"),
    ("Owned-factor interactions (quality x value, prof x invest, accruals x leverage, FCF x value)",
     "EXHAUSTED", "10-N: value interactions wrong-signed / insignificant at 63d; others do not beat net25"),
    ("Owned FRED macro + benchmark regimes (rates/oil/dollar/vol/dispersion/liquidity)", "EXHAUSTED",
     "10-O: apparent regime lifts are entirely post-2020; none beats the baseline in both eras"),
    ("PIT-reconstructed VALUE leg (earnings_yield / book_to_market from owned prices + equity)",
     "UNTESTED-AS-LEG (low-priority)",
     "the one remaining owned avenue; but 10-N value interactions were already wrong-signed at 63d, so a "
     "value leg is unlikely to help and needs a careful PIT market-cap join"),
    ("Owned EODHD news sentiment", "WEAK (8-series)",
     "explored in the 8-series; not a robust standalone quality-orthogonal alpha at 63d"),
)

# New data that would be required to go materially further (NOT owned; needs paid feeds - do not assume).
NEW_DATA_REQUIREMENTS: Tuple[Tuple[str, str, str], ...] = (
    ("Analyst estimate-revision history", "highest-priority orthogonal alpha family (post-earnings drift "
     "/ revisions momentum)", "paid feed (e.g. EODHD estimates entitlement or a revisions provider)"),
    ("Short interest / days-to-cover (full universe, historical)", "short-side crowding / squeeze signal",
     "paid or entitlement-limited (Polygon full-universe failed in 10-A)"),
    ("Options-implied vol / skew", "forward-looking risk + informed-flow signal", "paid feed"),
    ("Richer sentiment / alternative data", "quality-orthogonal soft information", "paid feed"),
)


def _load_decision(path: Path) -> Dict:
    try:
        j = _read_json(path)
        return {"decision": j.get("decision"), "rationale": j.get("decision_rationale"),
                "champion": (j.get("champion") or {}).get("champion") if isinstance(j.get("champion"),
                                                                                    dict) else None,
                "found": bool(j.get("decision") in _WINNER_DECISIONS)}
    except Exception as exc:
        return {"decision": None, "rationale": "could not read (%s)" % exc, "champion": None,
                "found": False, "missing": True}


def _baseline_metrics() -> Dict:
    try:
        j = _read_json(_BASELINE_JSON)
        for r in j.get("signal_results", []):
            if r.get("signal") == "composite_sn":
                return {"ic_t_63d": r.get("ic_t_63d"), "quarterly_net_25bps": r.get("quarterly_net_25bps"),
                        "quarterly_net_50bps": r.get("quarterly_net_50bps"),
                        "quarterly_turnover": r.get("quarterly_turnover"),
                        "oos_frac_windows_positive": r.get("oos_frac_windows_positive"),
                        "top_sector_share": r.get("top_sector_share")}
        return {"note": "composite_sn not found in 10-D report"}
    except Exception as exc:
        return {"note": "10-D baseline report unreadable (%s)" % exc}


def decide(avenue_rows: List[Dict], baseline: Dict) -> Tuple[str, str]:
    any_winner = any(a["found"] for a in avenue_rows)
    any_missing = any(a.get("missing") for a in avenue_rows)
    baseline_ok = "quarterly_net_25bps" in baseline and baseline.get("quarterly_net_25bps") is not None
    if any_winner:
        # Should not happen (10-P would have run); if it does, flag a refresh/repair.
        return DEC_REFRESH, ("an improvement avenue reported a NEW winner - Phase 10-P paper-rule "
                             "packaging should run before a final owned-data-exhaustion decision.")
    if any_missing or not baseline_ok:
        return DEC_REFRESH, ("one or more prior-phase decision reports (10-D baseline / 10-L-B / 10-M / "
                             "10-N / 10-O) are missing or unreadable - re-run the owned-data queue before "
                             "a final decision.")
    return DEC_PACKAGE, ("the modest / boundary 10-D quality composite is the ONLY usable owned-data alpha; "
                         "reweighting (10-L-B), incremental fundamentals (10-M), transforms/interactions "
                         "(10-N) and regimes (10-O) all failed to beat it out-of-sample. Carry the modest "
                         "baseline forward for PAPER review (it already passed the strict 10-D gate and is "
                         "packaged in 10-E -> 10-I); a genuinely STRONGER alpha needs new orthogonal data "
                         "(analyst revisions / short interest / options / richer sentiment - paid), so do "
                         "NOT keep mining the exhausted owned fundamentals + regimes.")


def _artifacts_dir() -> Path:
    return _OUT / STEM


def write_artifacts(P: Path, avenue_rows: List[Dict], baseline: Dict) -> None:
    _write_csv(P / "research_avenue_ledger.csv",
               ["phase", "avenue", "decision", "champion", "found_stronger_alpha", "rationale"],
               [[a["phase"], a["avenue"], a.get("decision"), a.get("champion"),
                 a["found"], (a.get("rationale") or "")[:300]] for a in avenue_rows])
    _write_csv(P / "baseline_status.csv",
               ["signal", "ic_t_63d", "quarterly_net_25bps", "quarterly_net_50bps", "quarterly_turnover",
                "oos_frac_windows_positive", "top_sector_share", "character", "paper_status"],
               [["composite_sn", baseline.get("ic_t_63d"), baseline.get("quarterly_net_25bps"),
                 baseline.get("quarterly_net_50bps"), baseline.get("quarterly_turnover"),
                 baseline.get("oos_frac_windows_positive"), baseline.get("top_sector_share"),
                 "modest / boundary (63d IC t < 3.0 strong bar)",
                 "confirmed 10-D; packaged for paper review 10-E -> 10-I"]])
    _write_csv(P / "owned_data_exhaustion.csv", ["owned_data_family", "status", "note"],
               [list(r) for r in OWNED_DATA_STATUS])
    _write_csv(P / "new_data_requirements.csv", ["new_data_family", "why", "access"],
               [list(r) for r in NEW_DATA_REQUIREMENTS])


def _build_report(decision: str, rationale: str, avenue_rows: List[Dict], baseline: Dict,
                  key_visible: bool) -> Dict:
    return {
        "phase": PHASE, "phase_name": PHASE_NAME, "decision": decision,
        "decision_rationale": rationale, "allowed_decisions": list(ALLOWED_DECISIONS),
        "objective": ("final honest owned-data-exhaustion research decision after the 10-L-B / 10-M / "
                      "10-N / 10-O improvement queue - synthesis only, no new fitting"),
        "offline": True, "performs_network": PERFORMS_NETWORK,
        "eodhd_key_visible": bool(key_visible), "eodhd_key_required": False,
        "input_inventory": [{"phase": a["phase"], "avenue": a["avenue"], "decision": a.get("decision"),
                             "report": "%s/%s" % (a["dir"], a["json"])} for a in AVENUES],
        "baseline": {"signal": "composite_sn",
                     "weighting": "equal (fcf+ / accruals-), sector-neutral, 63d quarterly",
                     "ic_t_63d": baseline.get("ic_t_63d"),
                     "quarterly_net_25bps": baseline.get("quarterly_net_25bps"),
                     "quarterly_net_50bps": baseline.get("quarterly_net_50bps"),
                     "quarterly_turnover": baseline.get("quarterly_turnover"),
                     "oos_frac_windows_positive": baseline.get("oos_frac_windows_positive"),
                     "top_sector_share": baseline.get("top_sector_share"),
                     "alpha_character": "REAL but MODEST / boundary (below the 3.0 strong bar; not "
                                        "oversold; accruals short leg carries most robustness)",
                     "paper_status": "confirmed at 10-D; already packaged for paper review (10-E -> 10-I "
                                     "sector-neutral 25L/25S rules + paper position tracker)"},
        "candidates_tested": [{"phase": a["phase"], "avenue": a["avenue"], "decision": a.get("decision"),
                               "champion": a.get("champion"), "found_stronger_alpha": a["found"]}
                              for a in avenue_rows],
        "variants_tested": [{"phase": a["phase"], "avenue": a["avenue"], "decision": a.get("decision")}
                            for a in avenue_rows],
        "rejected_candidates": [{"phase": a["phase"], "avenue": a["avenue"], "decision": a.get("decision"),
                                 "why": (a.get("rationale") or "")[:300]}
                                for a in avenue_rows if not a["found"]],
        "champion": {"champion": "composite_sn (the modest 10-D baseline)",
                     "baseline_remains_champion": True,
                     "no_stronger_owned_alpha_found": True},
        "baseline_vs_champion": {"note": "the champion IS the baseline; no improvement avenue beat it "
                                         "out-of-sample"},
        "owned_data_exhaustion": [{"family": r[0], "status": r[1], "note": r[2]}
                                  for r in OWNED_DATA_STATUS],
        "new_data_requirements": [{"family": r[0], "why": r[1], "access": r[2]}
                                  for r in NEW_DATA_REQUIREMENTS],
        "oos_stability_summary": {"note": "every improvement avenue's apparent gain failed an OOS / "
                                          "subperiod-generalisation guard (10-N rank composite and 10-O "
                                          "regimes were all post-2020 relics)"},
        "cohort_stability_summary": {"note": "the baseline is cohort- and subperiod-stable (10-D); the "
                                             "rejected candidates were not era-robust"},
        "sector_concentration_summary": {"baseline_top_sector_share": baseline.get("top_sector_share"),
                                         "note": "sector-neutral book runs ~0.63 (known Unknown-sector "
                                                 "mapping caveat, repaired in 10-F)"},
        "turnover_cost_summary": {"baseline_turnover": baseline.get("quarterly_turnover"),
                                  "note": "quarterly rebalance; the baseline survives 25bps at 63d"},
        "implementation_limits": [
            "synthesis phase: reads frozen prior-phase decision JSONs; builds no panel and fits nothing",
            "the modest baseline is not a prediction oracle and is not oversold - it is a small, "
            "cost-robust, sector-neutral quality tilt suitable ONLY for paper review",
            "a PIT-reconstructed value leg is the single remaining owned avenue but is low-priority given "
            "the 10-N wrong-signed value interactions; a stronger alpha realistically needs new paid data",
            "no new data was acquired and no provider was probed - paid-feed acquisition requires explicit "
            "user opt-in and is out of scope for this offline autonomous run",
        ],
        "recommended_next_actions": [
            "PAPER-review the modest 10-D/10-H sector-neutral 25L/25S book with the existing 10-I tracker "
            "(human gate; paper-only; no orders; no automation)",
            "if a stronger alpha is required, obtain ONE orthogonal paid family (analyst estimate-revisions "
            "first) with explicit user opt-in, then re-run the 8-X / 10-B strong-alpha gate",
            "optionally, a low-priority owned-data value-leg reconstruction (earnings_yield / "
            "book_to_market with a PIT market-cap join) before any purchase - expected weak per 10-N",
        ],
        "next_recommended_phase": "human paper review (10-I tracker) or a new-data acquisition phase on "
                                  "explicit user opt-in",
        "safety": {"paper_only": True, "owned_local_data_only": True, "no_live_api_calls": True,
                   "no_orders": True, "no_automation": True, "no_broker": True, "no_deploy": True},
        "constraints_honored": ["offline (no network/key/provider probe)", "owned/local data only",
                                "no new data", "no Paper Trader writes", "no orders", "no automation",
                                "no broker", "no deploy", "no GCP", "no package install",
                                "no full regression", "no commit", "no push"],
    }


def _print_summary(report: Dict) -> None:
    b = report.get("baseline", {})
    print("[10-Q] decision=%s | champion=%s | baseline 63d IC t=%s net25=%s | avenues_failed=%s/%s"
          % (report.get("decision"), report.get("champion", {}).get("champion"),
             b.get("ic_t_63d"), b.get("quarterly_net_25bps"),
             len(report.get("rejected_candidates", [])), len(report.get("candidates_tested", []))))


def run(out_dir: Optional[Path] = None, *, verbose: bool = True) -> Dict:
    P = Path(out_dir) if out_dir else _artifacts_dir()
    P.mkdir(parents=True, exist_ok=True)
    log = t8._Log(verbose)
    try:
        key_visible = bool(os.environ.get("EODHD_API_KEY"))
        log.step("preflight", "OFFLINE", "synthesis of owned prior-phase decisions; no network; "
                 "key_visible=%s" % key_visible)
        avenue_rows = []
        for a in AVENUES:
            d = _load_decision(_OUT / a["dir"] / a["json"])
            row = dict(a)
            row.update(d)
            avenue_rows.append(row)
            log.step("avenue", d.get("decision") or "MISSING",
                     "%s (%s): %s" % (a["phase"], a["avenue"][:40], d.get("decision")))
        baseline = _baseline_metrics()
        decision, rationale = decide(avenue_rows, baseline)
        write_artifacts(P, avenue_rows, baseline)
        report = _build_report(decision, rationale, avenue_rows, baseline, key_visible)
        _write_json(P / ("%s.json" % STEM), report)
        log.step("decision", decision, rationale[:120])
        _print_summary(report)
        return report
    except Exception as exc:  # pragma: no cover - defensive
        import traceback
        detail = "%s: %s" % (type(exc).__name__, exc)
        log.step("run", "ERROR", detail)
        report = {"phase": PHASE, "decision": DEC_REFRESH, "decision_rationale": detail,
                  "traceback": traceback.format_exc(),
                  "safety": {"paper_only": True, "owned_local_data_only": True, "no_live_api_calls": True,
                             "no_orders": True, "no_automation": True, "no_broker": True,
                             "no_deploy": True}}
        try:
            P.mkdir(parents=True, exist_ok=True)
            _write_json(P / ("%s.json" % STEM), report)
        except Exception:
            pass
        return report


def _parse_args(argv=None):
    p = argparse.ArgumentParser(description="Phase 10-Q - Owned Data Exhaustion And Research Decision")
    p.add_argument("--out-dir", default=None)
    p.add_argument("--quiet", action="store_true")
    return p.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    ns = _parse_args(argv)
    report = run(out_dir=ns.out_dir, verbose=not ns.quiet)
    return 0 if report.get("decision") in ALLOWED_DECISIONS else 1


if __name__ == "__main__":
    raise SystemExit(main())
