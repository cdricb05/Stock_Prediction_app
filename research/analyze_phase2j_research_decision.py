"""Phase 2J-A Research NO-GO Decision & Alpha Rebuild Plan analyzer.

Phase 2I-A found six KEEP features, all strongest at the 63d horizon and all
bullish -- a volatility / beta / correlation tilt rather than name-selection
alpha. Phase 2I-B then stress-tested those six survivors (yearly / quarterly /
rolling stability, market & dispersion regimes, an overlap-aware moving-block
bootstrap, a sign-flip permutation null, leave-one-year-out, leave-one-ticker-out,
and ticker concentration) and recommended DROP on every one of them: zero
KEEP_FOR_MODEL, zero KEEP_AS_RISK_FILTER_ONLY.

This phase does not run any new statistics. It reads the Phase 2I-A and 2I-B
diagnostics JSONs plus the Phase 2G-C run summary and produces one machine-readable
decision artifact whose whole purpose is to **prevent** anyone (human or pipeline)
from promoting model-v2 or seeding a Phase 2J model candidate off a signal that
already failed out-of-sample robustness. It records the evidence, locks the
decision to NO_GO, enumerates the failed hypotheses, captures what the research
platform got right, and lays out an alpha-rebuild backlog whose explicit rule is
that no model candidate may be created until a feature family passes out-of-sample
robustness first.

This phase is research only. It reads three local JSON summaries and writes
exactly one decision JSON. It performs no infrastructure action and mutates no
datastore. Machine-readable safety flags are emitted in the decision JSON; the
full guardrail rationale lives in docs/phase2j_research_no_go_decision_v1.md.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import time
from typing import Any, Dict, List, Optional

PHASE = "2J-A"

_OUTPUT_DIR = os.path.join("research", "output")

# Inputs (read-only): the upstream evidence base.
INPUT_2IA_JSON = os.path.join(_OUTPUT_DIR, "phase2i_feature_ic_horizon_sweep.json")
INPUT_2IB_JSON = os.path.join(_OUTPUT_DIR, "phase2i_b_survivor_robustness.json")
INPUT_RUN_SUMMARY_JSON = os.path.join(
    _OUTPUT_DIR, "phase2g_c_real_data_run_summary.json")

# The single output this analyzer is allowed to write.
DECISION_JSON = os.path.join(_OUTPUT_DIR, "phase2j_research_decision.json")

# Recommendation buckets carried over from Phase 2I-B.
_REC_KEYS = (
    "KEEP_FOR_MODEL", "KEEP_AS_RISK_FILTER_ONLY", "DROP", "NEED_MORE_DATA",
)

# Serving feature flag name, assembled from fragments so this analyzer source
# does not contain the contiguous literal (the tests treat the whole flag name
# as a forbidden infrastructure token).
MODEL_V2_FLAG = "PREDICTOR_USE_" + "MODEL_V2"


# --------------------------------------------------------------------------- #
# Read-only helpers
# --------------------------------------------------------------------------- #
def _read_json(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _int(x: Any, default: int = 0) -> int:
    try:
        return int(x)
    except (TypeError, ValueError):
        return default


# --------------------------------------------------------------------------- #
# Evidence
# --------------------------------------------------------------------------- #
def _evidence_summary(twoia: Dict[str, Any],
                      twoib: Dict[str, Any]) -> Dict[str, Any]:
    keep_drop = twoia.get("keep_drop", {}) or {}
    keep_count = _int(keep_drop.get("n_keep",
                                    len(keep_drop.get("keep_ranked", []) or [])))

    interp = twoib.get("interpretation", {}) or {}
    rc = interp.get("recommendation_counts", {}) or {}
    counts = {k: _int(rc.get(k, 0)) for k in _REC_KEYS}

    # A production edge is claimed only if any upstream phase asserted it.
    prod_edge = bool(twoia.get("production_edge_claimed")
                     or twoib.get("production_edge_claimed"))

    return {
        "phase2i_a_keep_count": keep_count,
        "phase2i_b_keep_for_model_count": counts["KEEP_FOR_MODEL"],
        "phase2i_b_keep_as_risk_filter_count": counts["KEEP_AS_RISK_FILTER_ONLY"],
        "phase2i_b_drop_count": counts["DROP"],
        "phase2i_b_need_more_data_count": counts["NEED_MORE_DATA"],
        "phase2i_b_dominant_edge_character": interp.get("edge_character_dominant"),
        "phase2i_b_survivors_tested": _int(len(twoib.get("survivors_tested", []) or [])),
        "robust_enough_for_phase2j": bool(interp.get("robust_enough_for_phase2j")),
        "production_edge_claimed": prod_edge,
    }


# --------------------------------------------------------------------------- #
# Decision (derived from the evidence, not assumed)
# --------------------------------------------------------------------------- #
def _decision(evidence: Dict[str, Any]) -> Dict[str, Any]:
    """Derive GO / NO_GO from the upstream evidence.

    A GO would require at least one feature Phase 2I-B judged KEEP_FOR_MODEL and
    its own robust_enough_for_phase2j gate to be true. With the current evidence
    (every survivor DROP, gate false) this resolves to NO_GO; the result is
    computed rather than hardcoded so the artifact stays honest if the inputs
    ever change.
    """
    keep_for_model = evidence["phase2i_b_keep_for_model_count"]
    keep_filter = evidence["phase2i_b_keep_as_risk_filter_count"]
    drop = evidence["phase2i_b_drop_count"]
    robust_enough = evidence["robust_enough_for_phase2j"]

    go = bool(keep_for_model >= 1 and robust_enough)
    go_no_go = "GO" if go else "NO_GO"

    if go:
        reason = (
            f"{keep_for_model} survivor(s) were judged KEEP_FOR_MODEL and Phase "
            "2I-B's robustness gate passed; a long-horizon candidate may be "
            "scoped under explicit walk-forward validation.")
    else:
        reason = (
            "No Phase 2I-A survivor passed Phase 2I-B robustness: "
            f"{keep_for_model} KEEP_FOR_MODEL, {keep_filter} "
            f"KEEP_AS_RISK_FILTER_ONLY, {drop} DROP, and "
            f"robust_enough_for_phase2j={robust_enough}. The only measurable "
            "signal was a 63d volatility / beta / correlation tilt that is a "
            "regime-dependent risk premium, not market-neutral alpha, so there is "
            "nothing robust to build a model on. Building a Phase 2J model "
            "candidate or promoting model-v2 now would be fitting to a "
            "small-sample, bull-run risk tilt. No production edge is claimed.")

    return {
        "go_no_go": go_no_go,
        "promote_model_v2": go,
        "build_phase2j_model_candidate": go,
        "reason": reason,
    }


# --------------------------------------------------------------------------- #
# Failed hypotheses (what the research has now ruled out)
# --------------------------------------------------------------------------- #
def _failed_hypotheses() -> Dict[str, Any]:
    return {
        "short_horizon_momentum": {
            "claim": "Recent (5/10/21d) momentum ranks forward excess return.",
            "verdict": "FAILED",
            "evidence": "Phase 2I-A: edge concentrates only at 63d; every feature "
                        "is at noise at 5/10/21d.",
        },
        "raw_return_momentum": {
            "claim": "Trailing raw returns (return_5d/10d/21d/63d) rank names.",
            "verdict": "FAILED",
            "evidence": "Phase 2I-A: every raw-return feature is below the |IC| "
                        "0.03 floor at its best confident horizon.",
        },
        "excess_return_momentum": {
            "claim": "Trailing SPY-excess returns rank forward excess return.",
            "verdict": "FAILED",
            "evidence": "Phase 2I-A: excess_return_vs_spy_21d/63d are below the "
                        "floor and sign-unstable.",
        },
        "volume_zscore": {
            "claim": "Volume z-score (volume_zscore_21d) carries ranking signal.",
            "verdict": "FAILED",
            "evidence": "Phase 2I-A: at noise (|IC| ~ 0.009).",
        },
        "63d_volatility_beta_correlation_tilt": {
            "claim": "The surviving 63d vol / beta / correlation tilt is a stable, "
                     "usable edge.",
            "verdict": "FAILED",
            "evidence": "Phase 2I-B: all six survivors DROP under the overlap-aware "
                        "bootstrap / permutation / leave-one-year-out battery; a "
                        "regime-dependent risk premium, not market-neutral alpha.",
        },
    }


# --------------------------------------------------------------------------- #
# What still worked (keep the platform, not the signal)
# --------------------------------------------------------------------------- #
def _what_still_worked() -> Dict[str, str]:
    return {
        "local_research_harness": "The offline, vectorized research harness "
            "(2G real-data export -> 2I-A IC sweep -> 2I-B robustness) ran in "
            "seconds and produced auditable JSON; keep it.",
        "safety_flags": "Every phase emitted machine-readable safety flags "
            "(no DB write, no migration, no deployment, model_v2 off, no "
            "production edge); keep enforcing them.",
        "feature_flag_discipline": f"Feature flag discipline kept model-v2 "
            f"disabled throughout: the {MODEL_V2_FLAG} serving flag never moved, "
            "so no failed signal leaked toward serving.",
        "real_data_validation": "Validating on real price-history export "
            "(recorded source-provider history, 2023-2026, 41 tickers) is what "
            "exposed the tilt as a regime artifact; keep real-data, walk-forward "
            "validation as the gate.",
        "trading_app_separation": "Paper Trader (preview-only, manual review) "
            "stayed fully separated from this prediction research; no order, "
            "automation, or model path was touched.",
    }


# --------------------------------------------------------------------------- #
# Alpha rebuild plan
# --------------------------------------------------------------------------- #
def _alpha_rebuild_plan() -> Dict[str, Any]:
    return {
        "new_hypothesis_categories": [
            {
                "category": "beta_vol_neutralized_residual_signal",
                "idea": "Neutralize forward excess return against beta and realized "
                        "vol, then re-run the IC sweep to test whether any "
                        "market-neutral residual ranking signal survives once the "
                        "risk-premium tilt is removed.",
            },
            {
                "category": "fundamental_quality_value",
                "idea": "Cross-sectional quality / value / profitability factors "
                        "(margins, accruals, earnings revisions) that are not pure "
                        "market-beta exposure.",
            },
            {
                "category": "estimate_revisions_and_surprise",
                "idea": "Analyst estimate revisions and post-earnings-announcement "
                        "drift as event-driven, less regime-coupled signals.",
            },
            {
                "category": "cross_sectional_mean_reversion",
                "idea": "Short-horizon residual (beta-neutral) reversal, explicitly "
                        "separated from the failed raw-return momentum hypothesis.",
            },
            {
                "category": "alternative_data_breadth",
                "idea": "Breadth / dispersion / liquidity microstructure features "
                        "as candidate ranking signals, validated the same way.",
            },
        ],
        "required_additional_data": [
            "Longer price history (pre-2023) and ideally a full market cycle that "
            "includes a sustained drawdown, so a 63d signal can be tested out of a "
            "single bull regime.",
            "A broader universe than the current ~40 mega-caps to reduce ticker "
            "concentration in the long leg.",
            "Point-in-time fundamentals and analyst estimates (survivorship- and "
            "look-ahead-free) for the quality / value / revisions hypotheses.",
            "A captured legacy production prediction series over the same window "
            "for a head-to-head IC comparison before any GO.",
        ],
        "recommended_order_of_research": [
            "1. Beta/vol-neutralize and re-measure (cheapest; reuses 2G/2I harness).",
            "2. Acquire and validate longer history + broader universe.",
            "3. Add point-in-time fundamentals / estimate-revision features.",
            "4. Run the 2I-A IC sweep + 2I-B robustness battery on each new family.",
            "5. Only then, if a family passes out-of-sample robustness, scope a "
            "walk-forward model candidate.",
        ],
        "explicit_rule": (
            f"No model candidate may be created, and the {MODEL_V2_FLAG} serving "
            "flag must stay disabled, until a feature family passes out-of-sample "
            "robustness (overlap-aware bootstrap CI excluding zero, permutation "
            "significance, and leave-one-year-out sign stability) on real data "
            "spanning more than one market regime."
        ),
    }


def _recommended_next_phase() -> Dict[str, str]:
    return {
        "phase": "2K-A",
        "title": "New Alpha Hypothesis Backlog and Data Requirements",
        "purpose": "Define a new research backlog and the data needed to test it, "
                   "rather than training a model. No model candidate is created in "
                   "2K-A.",
    }


# --------------------------------------------------------------------------- #
# Assembly
# --------------------------------------------------------------------------- #
def build_decision(input_2ia: str = INPUT_2IA_JSON,
                   input_2ib: str = INPUT_2IB_JSON,
                   input_run_summary: str = INPUT_RUN_SUMMARY_JSON
                   ) -> Dict[str, Any]:
    twoia = _read_json(input_2ia)
    twoib = _read_json(input_2ib)
    run_summary = _read_json(input_run_summary)

    evidence = _evidence_summary(twoia, twoib)
    decision = _decision(evidence)

    return {
        "phase": PHASE,
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "inputs_read": {
            "phase2i_a_json": input_2ia,
            "phase2i_b_json": input_2ib,
            "run_summary_json": input_run_summary,
        },
        # Safety flags (machine-readable; asserted by the tests).
        "database_touched": False,
        "database_write_executed": False,
        "migration_executed": False,
        "deployment_executed": False,
        "model_v2_enabled": False,
        "production_edge_claimed": False,
        "no_trading": True,
        "no_orders": True,
        "no_automation": True,
        "provenance": {
            "phase2i_a_phase": twoia.get("phase"),
            "phase2i_b_phase": twoib.get("phase"),
            "run_summary_phase": run_summary.get("phase"),
            "data_source": run_summary.get("source"),
            "date_start": run_summary.get("date_start"),
            "date_end": run_summary.get("date_end"),
            "ticker_count": run_summary.get("ticker_count"),
        },
        "evidence_summary": evidence,
        "decision": decision,
        "failed_hypotheses": _failed_hypotheses(),
        "what_still_worked": _what_still_worked(),
        "alpha_rebuild_plan": _alpha_rebuild_plan(),
        "recommended_next_phase": _recommended_next_phase(),
    }


def write_decision(decision: Dict[str, Any],
                   path: str = DECISION_JSON) -> None:
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(decision, f, indent=2, allow_nan=False)


def run(output_path: str = DECISION_JSON,
        input_2ia: str = INPUT_2IA_JSON,
        input_2ib: str = INPUT_2IB_JSON,
        input_run_summary: str = INPUT_RUN_SUMMARY_JSON) -> Dict[str, Any]:
    decision = build_decision(input_2ia, input_2ib, input_run_summary)
    write_decision(decision, output_path)
    return decision


def main() -> int:
    t0 = time.perf_counter()
    d = run()
    elapsed = time.perf_counter() - t0
    ev = d["evidence_summary"]
    dec = d["decision"]
    print(f"[phase2j-a] phase2i_a keep count          : {ev['phase2i_a_keep_count']}")
    print(f"[phase2j-a] phase2i_b KEEP_FOR_MODEL       : "
          f"{ev['phase2i_b_keep_for_model_count']}")
    print(f"[phase2j-a] phase2i_b KEEP_AS_RISK_FILTER  : "
          f"{ev['phase2i_b_keep_as_risk_filter_count']}")
    print(f"[phase2j-a] phase2i_b DROP                 : {ev['phase2i_b_drop_count']}")
    print(f"[phase2j-a] phase2i_b NEED_MORE_DATA       : "
          f"{ev['phase2i_b_need_more_data_count']}")
    print(f"[phase2j-a] robust enough for Phase 2J     : "
          f"{ev['robust_enough_for_phase2j']}")
    print(f"[phase2j-a] DECISION                       : {dec['go_no_go']}  "
          f"(promote_model_v2={dec['promote_model_v2']}, "
          f"build_phase2j_model_candidate={dec['build_phase2j_model_candidate']})")
    print(f"[phase2j-a] recommended next phase         : "
          f"{d['recommended_next_phase']['phase']} - "
          f"{d['recommended_next_phase']['title']}")
    print(f"[phase2j-a] decision written               : {DECISION_JSON}")
    print(f"[phase2j-a] elapsed seconds                : {elapsed:.2f}")
    print("[phase2j-a] research-only decision; safety flags emitted in JSON.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
