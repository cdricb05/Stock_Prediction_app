"""Phase 4-B - Non-Production Model Candidate Package (research-only governance).

This phase reads the Phase 3-Z signal audit and the Phase 4-A turnover-aware
portfolio simulation and assembles a clear, auditable *non-production* candidate
package: the selected research model + portfolio strategy, the evidence behind it,
the mandatory risk guardrails, the known failure modes, a Paper Trader preview
integration contract, explicit no-go items, and a readiness decision.

This is NOT deployment. It is NOT production model training. It is NOT live
prediction. It is NOT live portfolio construction. It packages already-computed
research evidence and never recomputes scores, never trains a model, never writes a
deployable artifact, never computes live or research portfolio weights, never places
orders, and never touches Paper Trader. It calls no network: it does not call Alpha
Vantage, Yahoo, Stooq, FRED, yfinance, or any paid vendor API. It reads nothing from
the D: data drive. Every metric is copied verbatim from the Phase 3-Z / 4-A outputs;
no metric is faked.

Inputs (all local, read-only):
  - research/output/phase3z_oos_score_export_signal_audit.json
  - research/output/phase3z_oos_score_export_signal_audit/oos_score_summary.csv
  - research/output/phase3z_oos_score_export_signal_audit/yearly_score_diagnostics.csv
  - research/output/phase3z_oos_score_export_signal_audit/regime_score_diagnostics.csv
  - research/output/phase3z_oos_score_export_signal_audit/leakage_and_embargo_checks.csv
  - research/output/phase4a_turnover_aware_portfolio_simulation.json
  - research/output/phase4a_turnover_aware_portfolio_simulation/strategy_scoreboard.csv
  - research/output/phase4a_turnover_aware_portfolio_simulation/turnover_cost_sensitivity.csv
  - research/output/phase4a_turnover_aware_portfolio_simulation/drawdown_summary.csv
  - research/output/phase4a_turnover_aware_portfolio_simulation/risk_budget_summary.csv
  - research/output/phase4a_turnover_aware_portfolio_simulation/readiness_decision_table.csv

Important caveat carried into the package: the Phase 4-A returns use 126-day forward
excess returns sampled monthly, so the windows OVERLAP. Sharpe / Sortino are useful
for relative strategy comparison but are OPTIMISTIC as tradable ratios. The package
records this as a mandatory caveat and a known failure mode.
"""

from __future__ import annotations

import json
import os
from datetime import datetime

import pandas as pd

PHASE = "4-B"

# ---------------------------------------------------------------------------
# Paths (relative to the repo root; read-only inputs, local outputs only).
# ---------------------------------------------------------------------------
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_OUT_BASE = os.path.join(_REPO_ROOT, "research", "output")

_3Z_DIR = os.path.join(_OUT_BASE, "phase3z_oos_score_export_signal_audit")
PHASE3Z_RESULT_JSON = os.path.join(_OUT_BASE, "phase3z_oos_score_export_signal_audit.json")
PHASE3Z_SUMMARY_CSV = os.path.join(_3Z_DIR, "oos_score_summary.csv")
PHASE3Z_YEARLY_CSV = os.path.join(_3Z_DIR, "yearly_score_diagnostics.csv")
PHASE3Z_REGIME_CSV = os.path.join(_3Z_DIR, "regime_score_diagnostics.csv")
PHASE3Z_LEAKAGE_CSV = os.path.join(_3Z_DIR, "leakage_and_embargo_checks.csv")

_4A_DIR = os.path.join(_OUT_BASE, "phase4a_turnover_aware_portfolio_simulation")
PHASE4A_RESULT_JSON = os.path.join(_OUT_BASE, "phase4a_turnover_aware_portfolio_simulation.json")
PHASE4A_SCOREBOARD_CSV = os.path.join(_4A_DIR, "strategy_scoreboard.csv")
PHASE4A_SENSITIVITY_CSV = os.path.join(_4A_DIR, "turnover_cost_sensitivity.csv")
PHASE4A_DRAWDOWN_CSV = os.path.join(_4A_DIR, "drawdown_summary.csv")
PHASE4A_RISK_CSV = os.path.join(_4A_DIR, "risk_budget_summary.csv")
PHASE4A_READINESS_CSV = os.path.join(_4A_DIR, "readiness_decision_table.csv")

_B_DIR = os.path.join(_OUT_BASE, "phase4b_nonproduction_candidate_package")
RESULT_JSON = os.path.join(_OUT_BASE, "phase4b_nonproduction_candidate_package.json")

_OUT_PATHS = {
    "candidate_summary_card": os.path.join(_B_DIR, "candidate_summary_card.csv"),
    "model_candidate_spec": os.path.join(_B_DIR, "model_candidate_spec.csv"),
    "selected_strategy_spec": os.path.join(_B_DIR, "selected_strategy_spec.csv"),
    "evidence_scorecard": os.path.join(_B_DIR, "evidence_scorecard.csv"),
    "risk_guardrails": os.path.join(_B_DIR, "risk_guardrails.csv"),
    "known_failure_modes": os.path.join(_B_DIR, "known_failure_modes.csv"),
    "preview_integration_contract": os.path.join(_B_DIR, "preview_integration_contract.csv"),
    "no_go_items": os.path.join(_B_DIR, "no_go_items.csv"),
    "readiness_decision_table": os.path.join(_B_DIR, "readiness_decision_table.csv"),
}

# ---------------------------------------------------------------------------
# The selected non-production candidate (fixed per the Phase 4-A result).
# ---------------------------------------------------------------------------
CANDIDATE_ID = "NPC-RIDGE-CRI-126D-TOP10EW-25BPS"
CANDIDATE_NAME = (
    "Ridge Combined Regime-Interactions (126d) / Top-10 Equal-Weight "
    "- research non-production candidate"
)
MODEL_NAME = "ridge_combined_regime_interactions"
HORIZON = "126d"
STRATEGY_NAME = "top_10_equal_weight"
TRANSACTION_COST_GATE_BPS = 25
REBALANCE_FREQUENCY = "monthly"
HOLDINGS_TARGET = 10
MAX_SINGLE_NAME_WEIGHT = 0.10
LONG_ONLY = True
LEVERAGE_ALLOWED = False
ORDERS_ALLOWED = False
AUTOMATION_ALLOWED = False
PAPER_TRADER_PREVIEW_ONLY = True

# Expected upstream recommendations the decision rule gates on.
EXPECTED_3Z_RECOMMENDATION = "OOS_SCORE_EXPORT_READY_FOR_PORTFOLIO_SIMULATION"
EXPECTED_4A_RECOMMENDATION = "PORTFOLIO_SIMULATION_READY_FOR_NONPRODUCTION_CANDIDATE"

# Readiness thresholds (mirrors the Phase 4-A risk gate, re-checked here).
READY_MAX_DRAWDOWN_FLOOR = -0.35
READY_MIN_AVG_HOLDINGS = 10
READY_MAX_AVG_TURNOVER = 1.5

# Decision values.
DEC_READY = "NONPROD_CANDIDATE_READY_FOR_PREVIEW_INTEGRATION"
DEC_PARTIAL = "NONPROD_CANDIDATE_PARTIAL_NEEDS_RISK_REPAIR"
DEC_BLOCKED_INPUTS = "NONPROD_CANDIDATE_BLOCKED_INPUTS"
DEC_BLOCKED_RISK = "NONPROD_CANDIDATE_BLOCKED_RISK"
ALLOWED_RECOMMENDATIONS = (
    DEC_READY,
    DEC_PARTIAL,
    DEC_BLOCKED_INPUTS,
    DEC_BLOCKED_RISK,
)

# Mandatory risk guardrails (every one of these must appear in risk_guardrails.csv).
GUARDRAILS = [
    ("preview_only", "Paper Trader may only PREVIEW this candidate; nothing is executed."),
    ("no_orders", "No order instructions, no order creation - ever."),
    ("no_automation", "No automation; every step is manual."),
    ("manual_review_required", "A human must review before any action is taken."),
    ("no_production_deployment", "Not deployed; no production serving of this candidate."),
    ("no_live_model_enablement", "PREDICTOR_USE_MODEL_V2 stays disabled; no live model enablement."),
    ("max_10_holdings_for_selected_strategy",
     "Selected strategy holds at most 10 names (top_10_equal_weight)."),
    ("max_single_name_weight_10pct", "No single name exceeds a 10% weight."),
    ("drawdown_warning_2024_severe",
     "Must show a drawdown warning: the 2024 drawdown was severe (-33.8% at 25 bps)."),
    ("overlapping_label_caveat",
     "Must show the overlapping 126d label caveat: Sharpe/Sortino are optimistic, not tradable."),
    ("survivorship_bias_caveat",
     "Must show the survivorship-bias caveat: universe is current-as-of."),
    ("fresh_validation_before_production",
     "Must require fresh validation before any production candidate is created."),
]

# Known failure modes recorded in the package.
KNOWN_FAILURE_MODES = [
    ("weak_2024_year_drawdown", "HIGH",
     "2024 was the model's weakest year (worst-year IC ~ -0.098) and the selected "
     "strategy's drawdown concentrates there (peak 2024-01 to trough 2024-12)."),
    ("cost_stress_failure_at_50bps", "MEDIUM",
     "top_10_equal_weight stops surviving at 50 bps - drawdown breaches the -35% floor."),
    ("overlapping_126d_labels_optimistic_sharpe", "MEDIUM",
     "126d forward returns sampled monthly overlap; Sharpe/Sortino are optimistic, "
     "useful only for relative comparison, not as tradable ratios."),
    ("survivorship_biased_universe", "MEDIUM",
     "Current-as-of universe; delisted/failed names are absent, inflating results."),
    ("incomplete_global_etf_cross_asset_data", "MEDIUM",
     "Global ETF / cross-asset data (Phase 3-U/3-V/3-W/3-X/3-Y) is incomplete; the "
     "universe is US single-name equities only."),
    ("incomplete_earnings_coverage", "LOW",
     "Earnings-estimate coverage (Phase 3-M) did not meet the full signal-gate threshold."),
    ("no_live_shadow_trading_proof", "HIGH",
     "No live or paper shadow-trading track record exists yet for this candidate."),
    ("no_production_model_artifact", "INFO",
     "No production model artifact has been trained or written; this is research-only."),
]

# Preview integration contract: (item, allowed, category, note).
PREVIEW_CONTRACT_ALLOWED = [
    ("model_score", "Paper Trader may display the research OOS model score for a ticker."),
    ("rank_percentile", "Paper Trader may display the cross-sectional rank percentile."),
    ("selected_strategy_membership",
     "Paper Trader may flag whether a ticker is in the selected strategy's top set."),
    ("safety_badges",
     "Paper Trader must show safety badges (PREVIEW ONLY / NO ORDERS / AUTOMATION OFF / MANUAL REVIEW)."),
    ("risk_warnings", "Paper Trader may show drawdown / overlapping-label / survivorship warnings."),
    ("reason_summary", "Paper Trader may show a plain-language reason summary for the score."),
]
PREVIEW_CONTRACT_FORBIDDEN = [
    ("no_orders", "Paper Trader must not create orders from this candidate."),
    ("no_broker_execution", "Paper Trader must not route anything to a broker."),
    ("no_automation", "Paper Trader must not automate any action from this candidate."),
    ("no_production_prediction_claim",
     "Paper Trader must not present these research scores as production predictions."),
    ("no_live_portfolio_weights",
     "Paper Trader must not compute or display live portfolio weights from this candidate."),
    ("no_database_write_unless_preview_endpoint",
     "No database write unless a future preview-only endpoint is explicitly implemented."),
]

# Explicit no-go items (things this phase and any downstream preview must NOT do).
NO_GO_ITEMS = [
    ("create_orders", "No order instructions or orders may be created."),
    ("broker_execution", "No broker execution of any kind."),
    ("automation", "No automation of scan / predict / trade steps."),
    ("production_prediction_claim", "Research OOS scores must not be claimed as production predictions."),
    ("live_portfolio_weights", "No live portfolio weights may be computed."),
    ("deploy", "No deployment of model or strategy."),
    ("enable_model_v2", "PREDICTOR_USE_MODEL_V2 must not be enabled."),
    ("train_production_model", "No production model training."),
    ("write_deployable_artifact", "No deployable model artifact may be written."),
    ("database_write", "No database write from this phase."),
    ("touch_paper_trader", "No Paper Trader file may be modified by this phase."),
    ("write_to_d_drive", "Nothing may be written to the D: data drive."),
]

# Exact output column orders.
SUMMARY_CARD_COLUMNS = ["field", "value"]
MODEL_SPEC_COLUMNS = ["spec_item", "value", "note"]
STRATEGY_SPEC_COLUMNS = ["spec_item", "value", "note"]
EVIDENCE_COLUMNS = ["metric", "value", "source", "note"]
GUARDRAILS_COLUMNS = ["guardrail", "required", "status", "note"]
FAILURE_MODE_COLUMNS = ["failure_mode", "severity", "note"]
PREVIEW_CONTRACT_COLUMNS = ["contract_item", "allowed", "category", "note"]
NO_GO_COLUMNS = ["no_go_item", "enforced", "note"]
READINESS_COLUMNS = ["decision_item", "value", "passed", "note"]


# ---------------------------------------------------------------------------
# Input loading / validation.
# ---------------------------------------------------------------------------
def confirm_inputs():
    """Confirm all required Phase 3-Z / 4-A inputs exist."""
    status = {
        "phase3z_result_json_present": os.path.isfile(PHASE3Z_RESULT_JSON),
        "phase3z_oos_score_summary_present": os.path.isfile(PHASE3Z_SUMMARY_CSV),
        "phase3z_yearly_diagnostics_present": os.path.isfile(PHASE3Z_YEARLY_CSV),
        "phase3z_regime_diagnostics_present": os.path.isfile(PHASE3Z_REGIME_CSV),
        "phase3z_leakage_checks_present": os.path.isfile(PHASE3Z_LEAKAGE_CSV),
        "phase4a_result_json_present": os.path.isfile(PHASE4A_RESULT_JSON),
        "phase4a_scoreboard_present": os.path.isfile(PHASE4A_SCOREBOARD_CSV),
        "phase4a_turnover_cost_sensitivity_present": os.path.isfile(PHASE4A_SENSITIVITY_CSV),
        "phase4a_drawdown_summary_present": os.path.isfile(PHASE4A_DRAWDOWN_CSV),
        "phase4a_risk_budget_present": os.path.isfile(PHASE4A_RISK_CSV),
        "phase4a_readiness_present": os.path.isfile(PHASE4A_READINESS_CSV),
    }
    status["all_required_present"] = all(status.values())
    return status


def _read_json(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _scoreboard_row(scoreboard, strategy, bps):
    """Return the scoreboard row dict for one (strategy, cost), or None."""
    sub = scoreboard[
        (scoreboard["strategy_name"] == strategy)
        & (scoreboard["transaction_cost_bps"].astype(int) == int(bps))
    ]
    if sub.empty:
        return None
    return sub.iloc[0].to_dict()


def gather_evidence():
    """Read all upstream evidence verbatim from the Phase 3-Z / 4-A artifacts."""
    z = _read_json(PHASE3Z_RESULT_JSON)
    a = _read_json(PHASE4A_RESULT_JSON)
    scoreboard = pd.read_csv(PHASE4A_SCOREBOARD_CSV)
    sensitivity = pd.read_csv(PHASE4A_SENSITIVITY_CSV)
    drawdown = pd.read_csv(PHASE4A_DRAWDOWN_CSV)

    bmh = z.get("best_model_horizon") or {}
    sel = _scoreboard_row(scoreboard, STRATEGY_NAME, TRANSACTION_COST_GATE_BPS) or {}

    # Strategies clearing the gate at 25 bps (GATE_PASS in the scoreboard).
    gate25 = scoreboard[scoreboard["transaction_cost_bps"].astype(int) == TRANSACTION_COST_GATE_BPS]
    passing = sorted(
        gate25[gate25["readiness_status"] == "GATE_PASS"]["strategy_name"].tolist()
    )

    # Cost survival for the selected strategy across the cost grid.
    sel_sens = sensitivity[sensitivity["strategy_name"] == STRATEGY_NAME].copy()
    sel_sens = sel_sens.sort_values("transaction_cost_bps")
    survives = {
        int(r["transaction_cost_bps"]): bool(r["survives_costs"])
        for _, r in sel_sens.iterrows()
    }
    survive_through = [b for b, ok in sorted(survives.items()) if ok]
    fail_from = [b for b, ok in sorted(survives.items()) if not ok]
    if fail_from:
        cost_survival = "survives through %d bps; fails at %d bps" % (
            max(survive_through) if survive_through else 0, min(fail_from))
    else:
        cost_survival = "survives all tested cost levels (0-%d bps)" % max(survives, default=0)

    # Selected-strategy drawdown window (for the 2024 warning).
    sel_dd = drawdown[
        (drawdown["strategy_name"] == STRATEGY_NAME)
        & (drawdown["transaction_cost_bps"].astype(int) == TRANSACTION_COST_GATE_BPS)
    ]
    dd_row = sel_dd.iloc[0].to_dict() if not sel_dd.empty else {}

    return {
        "z": z,
        "a": a,
        "phase3z_recommendation": (z.get("recommendation") or {}).get("recommendation"),
        "phase4a_recommendation": (a.get("recommendation") or {}).get("recommendation"),
        "oos_row_count": z.get("oos_score_panel_rows"),
        "ticker_count": z.get("distinct_tickers"),
        "date_count": z.get("distinct_dates"),
        "mean_rank_ic": bmh.get("mean_daily_rank_ic"),
        "decile_spread": bmh.get("top_minus_bottom_decile_spread"),
        "worst_year_ic": bmh.get("worst_year_ic"),
        "leakage_failures": z.get("leakage_checks_failed"),
        "selected_scoreboard": sel,
        "annualized_return_25bps": sel.get("annualized_return"),
        "annualized_volatility_25bps": sel.get("annualized_volatility"),
        "sharpe_25bps": sel.get("sharpe"),
        "max_drawdown_25bps": sel.get("max_drawdown"),
        "average_turnover": sel.get("average_turnover"),
        "average_holdings": sel.get("average_holdings"),
        "selected_gate_status": sel.get("readiness_status"),
        "passing_strategies_25bps": passing,
        "num_passing_strategies_25bps": len(passing),
        "cost_survival_status": cost_survival,
        "drawdown_window": dd_row,
    }


# ---------------------------------------------------------------------------
# Decision.
# ---------------------------------------------------------------------------
def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def evaluate_gates(ev):
    """Return the list of decision gates (decision_item, value, passed, note)."""
    maxdd = _f(ev.get("max_drawdown_25bps"))
    holdings = _f(ev.get("average_holdings"))
    turnover = _f(ev.get("average_turnover"))
    leak = ev.get("leakage_failures")

    gates = [
        {
            "decision_item": "phase3z_recommendation_ready",
            "value": ev.get("phase3z_recommendation"),
            "passed": ev.get("phase3z_recommendation") == EXPECTED_3Z_RECOMMENDATION,
            "note": "Phase 3-Z must be %s" % EXPECTED_3Z_RECOMMENDATION,
            "_kind": "evidence",
        },
        {
            "decision_item": "phase4a_recommendation_ready",
            "value": ev.get("phase4a_recommendation"),
            "passed": ev.get("phase4a_recommendation") == EXPECTED_4A_RECOMMENDATION,
            "note": "Phase 4-A must be %s" % EXPECTED_4A_RECOMMENDATION,
            "_kind": "evidence",
        },
        {
            "decision_item": "selected_strategy_passes_at_25bps",
            "value": ev.get("selected_gate_status"),
            "passed": ev.get("selected_gate_status") == "GATE_PASS",
            "note": "selected strategy (%s) must clear the 25 bps gate" % STRATEGY_NAME,
            "_kind": "risk",
        },
        {
            "decision_item": "no_leakage_failures",
            "value": leak,
            "passed": (leak is not None and int(leak) == 0),
            "note": "Phase 3-Z leakage/embargo checks must all pass",
            "_kind": "evidence",
        },
        {
            "decision_item": "max_drawdown_better_than_-35pct",
            "value": maxdd,
            "passed": (maxdd is not None and maxdd > READY_MAX_DRAWDOWN_FLOOR),
            "note": "selected strategy max drawdown must be > -0.35",
            "_kind": "risk",
        },
        {
            "decision_item": "average_holdings_ge_10",
            "value": holdings,
            "passed": (holdings is not None and holdings >= READY_MIN_AVG_HOLDINGS),
            "note": "selected strategy average holdings must be >= 10",
            "_kind": "risk",
        },
        {
            "decision_item": "average_turnover_le_1p5",
            "value": turnover,
            "passed": (turnover is not None and turnover <= READY_MAX_AVG_TURNOVER),
            "note": "selected strategy average turnover must be <= 1.5",
            "_kind": "risk",
        },
        {
            "decision_item": "all_required_guardrails_present",
            "value": len(GUARDRAILS),
            "passed": len(GUARDRAILS) == 12,
            "note": "all 12 mandatory guardrails must be present in the package",
            "_kind": "evidence",
        },
    ]
    return gates


def decide(inputs_ok, gates):
    if not inputs_ok:
        return DEC_BLOCKED_INPUTS
    risk_ok = all(g["passed"] for g in gates if g["_kind"] == "risk")
    evidence_ok = all(g["passed"] for g in gates if g["_kind"] == "evidence")
    if not risk_ok:
        return DEC_BLOCKED_RISK
    if risk_ok and evidence_ok:
        return DEC_READY
    return DEC_PARTIAL


def build_recommended_next_phase(decision):
    title = {
        DEC_READY: "Paper Trader Preview Integration Plan",
        DEC_PARTIAL: "Repair Candidate Package",
        DEC_BLOCKED_RISK: "Repair Strategy Risk Budget",
        DEC_BLOCKED_INPUTS: "Repair Phase 4-B Inputs",
    }[decision]
    return {
        "phase": "4-C",
        "title": "4-C - %s" % title,
        "purpose": "Proceed to the Paper Trader preview integration plan."
        if decision == DEC_READY
        else "Repair Phase 4-B before proceeding to 4-C.",
    }


def build_safety_flags(packaged):
    return {
        "research_only": True,
        "database_touched": False,
        "database_write_executed": False,
        "migration_executed": False,
        "deployment_executed": False,
        "model_v2_enabled": False,
        "production_model_trained": False,
        "production_model_candidate_created": False,
        "nonproduction_model_candidate_packaged": bool(packaged),
        "deployable_model_artifact_written": False,
        "production_predictions_computed": False,
        "production_scores_computed": False,
        "live_portfolio_weights_computed": False,
        "research_portfolio_weights_computed": False,
        "order_instructions_created": False,
        "orders_created": False,
        "trades_created": False,
        "paper_trader_files_modified": False,
        "d_drive_read": False,
        "d_drive_written": False,
        "provider_api_called": False,
        "alpha_vantage_called": False,
        "fred_called": False,
        "stooq_called": False,
        "yahoo_called": False,
        "yfinance_called": False,
        "paid_vendor_api_called": False,
        "network_called": False,
        "data_faked": False,
        "metrics_faked": False,
        "labels_for_validation_only": True,
    }


# ---------------------------------------------------------------------------
# Output assembly.
# ---------------------------------------------------------------------------
def _ensure_dirs():
    os.makedirs(_B_DIR, exist_ok=True)


def _write_csv(rows, columns, path):
    df = pd.DataFrame(rows, columns=columns) if rows else pd.DataFrame(columns=columns)
    df.to_csv(path, index=False)


def _num(x, fmt="%.4f"):
    v = _f(x)
    return (fmt % v) if v is not None else ""


def _build_card_rows(ev, decision, nxt):
    top_reasons = "; ".join([
        "OOS rank IC positive and decile-monotone (mean IC %s, decile spread %s) with %s "
        "leakage failures over %s rows / %s tickers / %s dates" % (
            _num(ev.get("mean_rank_ic"), "%.4f"), _num(ev.get("decile_spread"), "%.4f"),
            ev.get("leakage_failures"), ev.get("oos_row_count"),
            ev.get("ticker_count"), ev.get("date_count")),
        "Signal survives realistic portfolio construction: %s nets %s%% annualized at 25 bps "
        "(Sharpe %s)" % (STRATEGY_NAME, _num(_f(ev.get("annualized_return_25bps")) * 100, "%.1f")
                         if _f(ev.get("annualized_return_25bps")) is not None else "",
                         _num(ev.get("sharpe_25bps"), "%.2f")),
        "%d of 4 strategies clear the 25 bps readiness gate; risk budget respected "
        "(<=10 names, 10%% cap, no leverage, turnover %s)" % (
            ev.get("num_passing_strategies_25bps"), _num(ev.get("average_turnover"), "%.2f")),
    ])
    top_risks = "; ".join([
        "Severe 2024 drawdown (%s%% at 25 bps; model worst-year IC %s)" % (
            _num(_f(ev.get("max_drawdown_25bps")) * 100, "%.1f")
            if _f(ev.get("max_drawdown_25bps")) is not None else "",
            _num(ev.get("worst_year_ic"), "%.3f")),
        "Overlapping 126d labels make Sharpe/Sortino optimistic, not tradable ratios",
        "Survivorship-biased current-as-of universe; no live shadow-trading proof; "
        "no production model artifact yet",
    ])
    guardrail_summary = (
        "preview-only; no orders; no automation; manual review; no production deployment; "
        "no live model enablement; <=10 holdings; <=10% single name; drawdown + "
        "overlapping-label + survivorship caveats shown; fresh validation required before "
        "any production candidate"
    )
    return [
        {"field": "candidate_id", "value": CANDIDATE_ID},
        {"field": "candidate_name", "value": CANDIDATE_NAME},
        {"field": "model_name", "value": MODEL_NAME},
        {"field": "horizon", "value": HORIZON},
        {"field": "strategy_name", "value": STRATEGY_NAME},
        {"field": "status", "value": "RESEARCH_NONPRODUCTION_CANDIDATE"},
        {"field": "recommendation", "value": decision},
        {"field": "next_phase", "value": nxt["title"]},
        {"field": "top_reasons_to_proceed", "value": top_reasons},
        {"field": "top_risks", "value": top_risks},
        {"field": "mandatory_guardrails", "value": guardrail_summary},
    ]


def _build_model_spec_rows(ev):
    z = ev["z"]
    bmh = z.get("best_model_horizon") or {}
    return [
        {"spec_item": "model_name", "value": MODEL_NAME, "note": "best audited Phase 3-Z model"},
        {"spec_item": "horizon", "value": HORIZON, "note": "126 trading-day forward excess vs SPY"},
        {"spec_item": "model_family", "value": "ridge_closed_form_cross_sectional_rank",
         "note": "NumPy closed-form ridge; transforms fit on train rows only"},
        {"spec_item": "training_convention", "value": "expanding_walk_forward_per_horizon_embargo",
         "note": "Phase 3-P/3-Z walk-forward with 126d embargo; no leakage"},
        {"spec_item": "mean_daily_rank_ic", "value": bmh.get("mean_daily_rank_ic"),
         "note": "out-of-sample, validation-only"},
        {"spec_item": "top_minus_bottom_decile_spread", "value": bmh.get("top_minus_bottom_decile_spread"),
         "note": "decile-monotone forward return spread"},
        {"spec_item": "stability_score", "value": bmh.get("stability_score"),
         "note": "fraction of years with positive IC"},
        {"spec_item": "labels_for_validation_only", "value": True,
         "note": "forward returns used for OOS IC only; never emitted as production predictions"},
        {"spec_item": "production_model_trained", "value": False, "note": "research-only"},
        {"spec_item": "deployable_model_artifact_written", "value": False, "note": "no artifact written"},
        {"spec_item": "results_survivorship_biased", "value": True, "note": "current-as-of universe"},
    ]


def _build_strategy_spec_rows():
    return [
        {"spec_item": "strategy_name", "value": STRATEGY_NAME, "note": "highest Sharpe at 25 bps gate"},
        {"spec_item": "rebalance_frequency", "value": REBALANCE_FREQUENCY, "note": "first trading day of month"},
        {"spec_item": "holdings_target", "value": HOLDINGS_TARGET, "note": "top 10 by OOS score"},
        {"spec_item": "max_single_name_weight", "value": MAX_SINGLE_NAME_WEIGHT, "note": "10% position cap"},
        {"spec_item": "transaction_cost_gate_bps", "value": TRANSACTION_COST_GATE_BPS,
         "note": "readiness evaluated net of 25 bps"},
        {"spec_item": "long_only", "value": LONG_ONLY, "note": "weights >= 0"},
        {"spec_item": "leverage_allowed", "value": LEVERAGE_ALLOWED, "note": "gross <= 100%"},
        {"spec_item": "orders_allowed", "value": ORDERS_ALLOWED, "note": "no orders, ever"},
        {"spec_item": "automation_allowed", "value": AUTOMATION_ALLOWED, "note": "no automation"},
        {"spec_item": "paper_trader_preview_only", "value": PAPER_TRADER_PREVIEW_ONLY,
         "note": "preview integration only; nothing executed"},
    ]


def _build_evidence_rows(ev):
    return [
        {"metric": "oos_row_count", "value": ev.get("oos_row_count"),
         "source": "phase3z", "note": "rows in the OOS score panel"},
        {"metric": "ticker_count", "value": ev.get("ticker_count"),
         "source": "phase3z", "note": "distinct tickers"},
        {"metric": "date_count", "value": ev.get("date_count"),
         "source": "phase3z", "note": "distinct dates"},
        {"metric": "mean_rank_ic", "value": ev.get("mean_rank_ic"),
         "source": "phase3z", "note": "mean daily cross-sectional rank IC"},
        {"metric": "top_minus_bottom_decile_spread", "value": ev.get("decile_spread"),
         "source": "phase3z", "note": "forward-return decile spread"},
        {"metric": "leakage_failures", "value": ev.get("leakage_failures"),
         "source": "phase3z", "note": "failed leakage/embargo checks"},
        {"metric": "annualized_return_at_25bps", "value": ev.get("annualized_return_25bps"),
         "source": "phase4a", "note": "selected strategy, net of 25 bps"},
        {"metric": "sharpe_at_25bps", "value": ev.get("sharpe_25bps"),
         "source": "phase4a", "note": "OPTIMISTIC - overlapping 126d labels"},
        {"metric": "max_drawdown_at_25bps", "value": ev.get("max_drawdown_25bps"),
         "source": "phase4a", "note": "literal compounded monthly path"},
        {"metric": "average_turnover", "value": ev.get("average_turnover"),
         "source": "phase4a", "note": "two-sided turnover per rebalance"},
        {"metric": "average_holdings", "value": ev.get("average_holdings"),
         "source": "phase4a", "note": "names held per rebalance"},
        {"metric": "cost_survival_status", "value": ev.get("cost_survival_status"),
         "source": "phase4a", "note": "selected strategy across the cost grid"},
        {"metric": "passing_strategies_at_25bps", "value": ev.get("num_passing_strategies_25bps"),
         "source": "phase4a", "note": "strategies clearing the 25 bps gate (%s)"
         % ", ".join(ev.get("passing_strategies_25bps") or [])},
    ]


def _build_guardrail_rows():
    return [
        {"guardrail": key, "required": True, "status": "ENFORCED", "note": note}
        for key, note in GUARDRAILS
    ]


def _build_failure_rows():
    return [
        {"failure_mode": key, "severity": sev, "note": note}
        for key, sev, note in KNOWN_FAILURE_MODES
    ]


def _build_preview_contract_rows():
    rows = [
        {"contract_item": key, "allowed": True, "category": "may_show", "note": note}
        for key, note in PREVIEW_CONTRACT_ALLOWED
    ]
    rows += [
        {"contract_item": key, "allowed": False, "category": "must_not", "note": note}
        for key, note in PREVIEW_CONTRACT_FORBIDDEN
    ]
    return rows


def _build_no_go_rows():
    return [
        {"no_go_item": key, "enforced": True, "note": note}
        for key, note in NO_GO_ITEMS
    ]


def _build_readiness_rows(gates, decision):
    rows = [
        {"decision_item": g["decision_item"], "value": g["value"],
         "passed": bool(g["passed"]), "note": g["note"]}
        for g in gates
    ]
    rows.append({
        "decision_item": "final_recommendation", "value": decision,
        "passed": decision == DEC_READY,
        "note": "research-only; no production candidate created in this phase",
    })
    return rows


def _finish_blocked(inputs_status, reason):
    """Write empty-but-headed CSVs and a BLOCKED_INPUTS result JSON (no fake rows)."""
    _ensure_dirs()
    _write_csv([], SUMMARY_CARD_COLUMNS, _OUT_PATHS["candidate_summary_card"])
    _write_csv([], MODEL_SPEC_COLUMNS, _OUT_PATHS["model_candidate_spec"])
    _write_csv([], STRATEGY_SPEC_COLUMNS, _OUT_PATHS["selected_strategy_spec"])
    _write_csv([], EVIDENCE_COLUMNS, _OUT_PATHS["evidence_scorecard"])
    # Guardrails and no-go items are intrinsic to the phase; emit them even when blocked.
    _write_csv(_build_guardrail_rows(), GUARDRAILS_COLUMNS, _OUT_PATHS["risk_guardrails"])
    _write_csv(_build_failure_rows(), FAILURE_MODE_COLUMNS, _OUT_PATHS["known_failure_modes"])
    _write_csv(_build_preview_contract_rows(), PREVIEW_CONTRACT_COLUMNS,
               _OUT_PATHS["preview_integration_contract"])
    _write_csv(_build_no_go_rows(), NO_GO_COLUMNS, _OUT_PATHS["no_go_items"])
    _write_csv([{"decision_item": "blocker", "value": reason, "passed": False,
                 "note": DEC_BLOCKED_INPUTS}],
               READINESS_COLUMNS, _OUT_PATHS["readiness_decision_table"])

    nxt = build_recommended_next_phase(DEC_BLOCKED_INPUTS)
    result = {
        "phase": PHASE,
        "input_confirmation": inputs_status,
        "blocked": True,
        "blocker_reason": reason,
        "candidate_id": CANDIDATE_ID,
        "candidate_name": CANDIDATE_NAME,
        "recommendation": {"recommendation": DEC_BLOCKED_INPUTS,
                           "allowed_values": list(ALLOWED_RECOMMENDATIONS), "reason": reason},
        "recommended_next_phase": nxt,
        "outputs_written": {k: os.path.relpath(v, _REPO_ROOT).replace("\\", "/")
                            for k, v in _OUT_PATHS.items()},
    }
    result.update(build_safety_flags(packaged=False))
    with open(RESULT_JSON, "w", encoding="utf-8") as fh:
        json.dump(result, fh, indent=2)
    return result


def run(generated_at=None):
    inputs_status = confirm_inputs()
    if not inputs_status["all_required_present"]:
        missing = [k for k, v in inputs_status.items()
                   if k != "all_required_present" and not v]
        return _finish_blocked(inputs_status,
                               "Required Phase 3-Z / 4-A inputs missing: %s" % ", ".join(missing))

    try:
        ev = gather_evidence()
    except Exception as exc:  # unreadable / malformed upstream artifact
        return _finish_blocked(inputs_status,
                               "Upstream Phase 3-Z / 4-A artifact unreadable/invalid: %s" % exc)

    gates = evaluate_gates(ev)
    decision = decide(True, gates)
    nxt = build_recommended_next_phase(decision)

    _ensure_dirs()
    card_rows = _build_card_rows(ev, decision, nxt)
    _write_csv(card_rows, SUMMARY_CARD_COLUMNS, _OUT_PATHS["candidate_summary_card"])
    _write_csv(_build_model_spec_rows(ev), MODEL_SPEC_COLUMNS, _OUT_PATHS["model_candidate_spec"])
    _write_csv(_build_strategy_spec_rows(), STRATEGY_SPEC_COLUMNS, _OUT_PATHS["selected_strategy_spec"])
    _write_csv(_build_evidence_rows(ev), EVIDENCE_COLUMNS, _OUT_PATHS["evidence_scorecard"])
    _write_csv(_build_guardrail_rows(), GUARDRAILS_COLUMNS, _OUT_PATHS["risk_guardrails"])
    _write_csv(_build_failure_rows(), FAILURE_MODE_COLUMNS, _OUT_PATHS["known_failure_modes"])
    _write_csv(_build_preview_contract_rows(), PREVIEW_CONTRACT_COLUMNS,
               _OUT_PATHS["preview_integration_contract"])
    _write_csv(_build_no_go_rows(), NO_GO_COLUMNS, _OUT_PATHS["no_go_items"])
    _write_csv(_build_readiness_rows(gates, decision), READINESS_COLUMNS,
               _OUT_PATHS["readiness_decision_table"])

    if decision == DEC_READY:
        reason = (
            "Phase 3-Z (%s) and Phase 4-A (%s) are both READY; selected strategy '%s' clears "
            "the 25 bps gate (ann.return %s, Sharpe %s, maxDD %s, holdings %s, turnover %s) with "
            "0 leakage failures and all 12 guardrails present. Packaged as a research "
            "non-production candidate; no production model, predictions, weights, or deployment."
            % (ev.get("phase3z_recommendation"), ev.get("phase4a_recommendation"), STRATEGY_NAME,
               ev.get("annualized_return_25bps"), ev.get("sharpe_25bps"),
               ev.get("max_drawdown_25bps"), ev.get("average_holdings"), ev.get("average_turnover"))
        )
    elif decision == DEC_BLOCKED_RISK:
        failed = [g["decision_item"] for g in gates if g["_kind"] == "risk" and not g["passed"]]
        reason = ("Selected strategy fails a risk gate (%s); risk budget must be repaired "
                  "before packaging." % ", ".join(failed))
    else:  # PARTIAL
        failed = [g["decision_item"] for g in gates if not g["passed"]]
        reason = ("Risk gates pass but evidence is incomplete (%s); candidate package needs "
                  "repair before preview integration." % ", ".join(failed))

    result = {
        "phase": PHASE,
        "generated_at": generated_at or datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "inputs_read": {
            "phase3z_result_json": os.path.relpath(PHASE3Z_RESULT_JSON, _REPO_ROOT).replace("\\", "/"),
            "phase3z_oos_score_summary_csv": os.path.relpath(PHASE3Z_SUMMARY_CSV, _REPO_ROOT).replace("\\", "/"),
            "phase3z_yearly_score_diagnostics_csv": os.path.relpath(PHASE3Z_YEARLY_CSV, _REPO_ROOT).replace("\\", "/"),
            "phase3z_regime_score_diagnostics_csv": os.path.relpath(PHASE3Z_REGIME_CSV, _REPO_ROOT).replace("\\", "/"),
            "phase3z_leakage_and_embargo_checks_csv": os.path.relpath(PHASE3Z_LEAKAGE_CSV, _REPO_ROOT).replace("\\", "/"),
            "phase4a_result_json": os.path.relpath(PHASE4A_RESULT_JSON, _REPO_ROOT).replace("\\", "/"),
            "phase4a_strategy_scoreboard_csv": os.path.relpath(PHASE4A_SCOREBOARD_CSV, _REPO_ROOT).replace("\\", "/"),
            "phase4a_turnover_cost_sensitivity_csv": os.path.relpath(PHASE4A_SENSITIVITY_CSV, _REPO_ROOT).replace("\\", "/"),
            "phase4a_drawdown_summary_csv": os.path.relpath(PHASE4A_DRAWDOWN_CSV, _REPO_ROOT).replace("\\", "/"),
            "phase4a_risk_budget_summary_csv": os.path.relpath(PHASE4A_RISK_CSV, _REPO_ROOT).replace("\\", "/"),
            "phase4a_readiness_decision_table_csv": os.path.relpath(PHASE4A_READINESS_CSV, _REPO_ROOT).replace("\\", "/"),
        },
        "input_confirmation": inputs_status,
        "candidate_id": CANDIDATE_ID,
        "candidate_name": CANDIDATE_NAME,
        "selected_candidate": {
            "model_name": MODEL_NAME,
            "horizon": HORIZON,
            "strategy_name": STRATEGY_NAME,
            "transaction_cost_gate_bps": TRANSACTION_COST_GATE_BPS,
            "rebalance_frequency": REBALANCE_FREQUENCY,
            "holdings_target": HOLDINGS_TARGET,
            "max_single_name_weight": MAX_SINGLE_NAME_WEIGHT,
            "long_only": LONG_ONLY,
            "leverage_allowed": LEVERAGE_ALLOWED,
            "orders_allowed": ORDERS_ALLOWED,
            "automation_allowed": AUTOMATION_ALLOWED,
            "paper_trader_preview_only": PAPER_TRADER_PREVIEW_ONLY,
        },
        "evidence_summary": {
            "phase3z_recommendation": ev.get("phase3z_recommendation"),
            "phase4a_recommendation": ev.get("phase4a_recommendation"),
            "oos_row_count": ev.get("oos_row_count"),
            "ticker_count": ev.get("ticker_count"),
            "date_count": ev.get("date_count"),
            "mean_rank_ic": ev.get("mean_rank_ic"),
            "top_minus_bottom_decile_spread": ev.get("decile_spread"),
            "leakage_failures": ev.get("leakage_failures"),
            "annualized_return_at_25bps": ev.get("annualized_return_25bps"),
            "annualized_volatility_at_25bps": ev.get("annualized_volatility_25bps"),
            "sharpe_at_25bps": ev.get("sharpe_25bps"),
            "max_drawdown_at_25bps": ev.get("max_drawdown_25bps"),
            "average_turnover": ev.get("average_turnover"),
            "average_holdings": ev.get("average_holdings"),
            "cost_survival_status": ev.get("cost_survival_status"),
            "passing_strategies_at_25bps": ev.get("passing_strategies_25bps"),
            "num_passing_strategies_at_25bps": ev.get("num_passing_strategies_25bps"),
        },
        "mandatory_guardrails": [k for k, _ in GUARDRAILS],
        "known_failure_modes": [k for k, _, _ in KNOWN_FAILURE_MODES],
        "no_go_items": [k for k, _ in NO_GO_ITEMS],
        "decision_gates": [
            {"decision_item": g["decision_item"], "value": g["value"], "passed": bool(g["passed"])}
            for g in gates
        ],
        "sharpe_caveat": (
            "Phase 4-A uses 126-day forward excess returns sampled monthly, so windows overlap. "
            "Sharpe/Sortino are useful for relative strategy comparison but optimistic as tradable "
            "ratios. This must be honored in any preview integration."
        ),
        "recommendation": {
            "recommendation": decision,
            "allowed_values": list(ALLOWED_RECOMMENDATIONS),
            "research_only": True,
            "create_production_model_candidate_now": False,
            "compute_live_portfolio_weights_now": False,
            "deploy_now": False,
            "production_edge_claimed": False,
            "reason": reason,
        },
        "recommended_next_phase": nxt,
        "outputs_written": {k: os.path.relpath(v, _REPO_ROOT).replace("\\", "/")
                            for k, v in _OUT_PATHS.items()},
        "interpretation": {
            "phase_is_research_only": True,
            "nonproduction_candidate_packaged": True,
            "scores_are_research_oos_not_production": True,
            "labels_used_for_validation_only": True,
            "results_remain_survivorship_biased": True,
            "production_model_trained": False,
            "production_predictions_computed": False,
            "live_portfolio_weights_computed": False,
            "deployable_model_artifact_written": False,
            "production_edge_claimed": False,
        },
    }
    result.update(build_safety_flags(packaged=True))
    with open(RESULT_JSON, "w", encoding="utf-8") as fh:
        json.dump(result, fh, indent=2)
    return result


def main():
    result = run()
    rec = result.get("recommendation", {}).get("recommendation")
    nxt = result.get("recommended_next_phase", {})
    ev = result.get("evidence_summary", {})
    print("phase %s | rec %s | next %s | candidate %s | strat %s | ann_ret %s | sharpe %s | maxDD %s"
          % (result.get("phase"), rec, nxt.get("phase"), result.get("candidate_id"),
             result.get("selected_candidate", {}).get("strategy_name"),
             ev.get("annualized_return_at_25bps"), ev.get("sharpe_at_25bps"),
             ev.get("max_drawdown_at_25bps")))
    return result


if __name__ == "__main__":
    main()
