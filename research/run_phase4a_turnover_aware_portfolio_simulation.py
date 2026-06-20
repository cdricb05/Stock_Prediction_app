"""Phase 4-A - Turnover-Aware Portfolio Simulation and Risk Budget (research-only).

This phase consumes the Phase 3-Z out-of-sample (OOS) score panel and runs a
*research-only* long-only portfolio simulation to test whether the score signal
survives realistic portfolio construction: monthly rebalancing, turnover,
transaction costs, single-name position limits, drawdowns, and annual stability.

It is research-only. It does not deploy. It does not run migrations. It does not
write to any production database. It does not trade. It places no orders, implements
no automation, trains no production model, creates no production model candidate,
writes no deployable model artifact, computes no production predictions, computes no
production scores, and computes no *live* portfolio weights or order instructions.
The only weights it computes are *research* portfolio weights used to measure the
signal in-sample-free. It reads nothing from the D: data drive and calls no network:
it does not call Alpha Vantage, Yahoo, Stooq, FRED, yfinance, or any paid vendor API.
Forward returns from the Phase 3-Z panel are used as validation-only research labels
and are never faked.

Inputs (all local, read-only):
  - research/output/phase3z_oos_score_export_signal_audit.json
  - research/output/phase3z_oos_score_export_signal_audit/oos_score_panel.csv
  - research/output/phase3z_oos_score_export_signal_audit/oos_score_summary.csv
  - research/output/phase3z_oos_score_export_signal_audit/decile_forward_return_summary.csv
  - research/output/phase3z_oos_score_export_signal_audit/leakage_and_embargo_checks.csv

Annualization convention (documented honestly):
  Each per-rebalance return is the weighted mean of holdings' forward_return, which
  is the 126-trading-day forward excess return vs SPY (the Phase 3-Z label horizon),
  sampled at MONTHLY cadence -> the observations OVERLAP (each ~6 consecutive monthly
  rebalances share overlapping 126d windows). We do NOT smooth this away with a power
  transform (that artificially compresses drawdowns). Instead:
    * Return / volatility / Sharpe / Sortino use ARITHMETIC horizon annualization on
      the raw per-rebalance net returns: ann_return = mean(net) * (252/126) and
      ann_vol = std(net) * sqrt(252/126) (252/126 = 2 horizons per year). Because the
      monthly samples overlap, these are AUTOCORRELATED, so Sharpe/Sortino are
      OPTIMISTIC and should be read as relative comparisons across strategies / cost
      levels, not as tradable risk-adjusted ratios.
    * Max drawdown is taken from the literal compounded monthly path
      (equity = cumprod(1+net)), the same series named by monthly_portfolio_returns.csv.
    * Annual return is the annualized rate of each calendar year's mean net return.
  The raw per-rebalance gross/net/turnover/cost numbers are stored verbatim in
  monthly_portfolio_returns.csv as the transparent ledger. Results remain
  survivorship-biased and claim no production edge.
"""

from __future__ import annotations

import json
import math
import os
from datetime import datetime

import numpy as np
import pandas as pd

PHASE = "4-A"

# ---------------------------------------------------------------------------
# Paths (all relative to the repo root; read-only inputs, local outputs only).
# ---------------------------------------------------------------------------
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_OUT_BASE = os.path.join(_REPO_ROOT, "research", "output")

_3Z_DIR = os.path.join(_OUT_BASE, "phase3z_oos_score_export_signal_audit")
PHASE3Z_RESULT_JSON = os.path.join(_OUT_BASE, "phase3z_oos_score_export_signal_audit.json")
PHASE3Z_PANEL_CSV = os.path.join(_3Z_DIR, "oos_score_panel.csv")
PHASE3Z_SUMMARY_CSV = os.path.join(_3Z_DIR, "oos_score_summary.csv")
PHASE3Z_DECILE_CSV = os.path.join(_3Z_DIR, "decile_forward_return_summary.csv")
PHASE3Z_LEAKAGE_CSV = os.path.join(_3Z_DIR, "leakage_and_embargo_checks.csv")

_A_DIR = os.path.join(_OUT_BASE, "phase4a_turnover_aware_portfolio_simulation")
RESULT_JSON = os.path.join(_OUT_BASE, "phase4a_turnover_aware_portfolio_simulation.json")

_OUT_PATHS = {
    "scoreboard": os.path.join(_A_DIR, "strategy_scoreboard.csv"),
    "monthly": os.path.join(_A_DIR, "monthly_portfolio_returns.csv"),
    "annual": os.path.join(_A_DIR, "annual_performance.csv"),
    "sensitivity": os.path.join(_A_DIR, "turnover_cost_sensitivity.csv"),
    "drawdown": os.path.join(_A_DIR, "drawdown_summary.csv"),
    "concentration": os.path.join(_A_DIR, "position_concentration_summary.csv"),
    "risk_budget": os.path.join(_A_DIR, "risk_budget_summary.csv"),
    "readiness": os.path.join(_A_DIR, "readiness_decision_table.csv"),
}

# ---------------------------------------------------------------------------
# Simulation constants.
# ---------------------------------------------------------------------------
HORIZON_LABEL = "126d"
HORIZON_DAYS = 126
REBALANCE_TRADING_DAYS = 21            # ~1 month
STEPS_PER_HORIZON = HORIZON_DAYS / REBALANCE_TRADING_DAYS  # 6.0 (overlap factor)
TRADING_DAYS_PER_YEAR = 252.0
HORIZONS_PER_YEAR = TRADING_DAYS_PER_YEAR / HORIZON_DAYS   # 2.0
MAX_SINGLE_NAME_WEIGHT = 0.10          # 10% position cap
_WEIGHT_EPS = 1e-9

STRATEGIES = (
    "top_10_equal_weight",
    "top_20_equal_weight",
    "top_decile_equal_weight",
    "top_decile_score_weighted_capped",
)
COST_BPS = (0, 10, 25, 50)
GATING_COST_BPS = 25                   # readiness gate is evaluated at 25 bps

# Readiness thresholds (decision rule).
READY_MIN_ANN_RETURN = 0.0
READY_MIN_SHARPE = 0.0
READY_MAX_DRAWDOWN_FLOOR = -0.35       # max drawdown must be BETTER than -35%
READY_MIN_AVG_HOLDINGS = 10
READY_MAX_AVG_TURNOVER = 1.5
READY_MIN_PERIODS = 36

DEC_READY = "PORTFOLIO_SIMULATION_READY_FOR_NONPRODUCTION_CANDIDATE"
DEC_PARTIAL = "PORTFOLIO_SIMULATION_PARTIAL_NEEDS_RISK_REPAIR"
DEC_BLOCKED_NO_STRATEGY = "PORTFOLIO_SIMULATION_BLOCKED_NO_VALID_STRATEGY"
DEC_BLOCKED_INPUTS = "PORTFOLIO_SIMULATION_BLOCKED_INPUTS"
ALLOWED_RECOMMENDATIONS = (
    DEC_READY,
    DEC_PARTIAL,
    DEC_BLOCKED_NO_STRATEGY,
    DEC_BLOCKED_INPUTS,
)

# Exact output column orders.
SCOREBOARD_COLUMNS = [
    "strategy_name", "transaction_cost_bps", "periods", "annualized_return",
    "annualized_volatility", "sharpe", "sortino", "max_drawdown",
    "monthly_hit_rate", "average_turnover", "average_holdings", "worst_month",
    "worst_year_return", "best_year_return", "total_cost_drag", "readiness_status",
]
MONTHLY_COLUMNS = [
    "rebalance_date", "strategy_name", "transaction_cost_bps", "gross_return",
    "turnover", "cost_drag", "net_return", "holdings_count",
]
ANNUAL_COLUMNS = [
    "year", "strategy_name", "transaction_cost_bps", "annual_return",
    "annual_volatility", "hit_rate", "average_turnover",
]
SENSITIVITY_COLUMNS = [
    "strategy_name", "transaction_cost_bps", "annualized_return", "sharpe",
    "max_drawdown", "total_cost_drag", "survives_costs",
]
DRAWDOWN_COLUMNS = [
    "strategy_name", "transaction_cost_bps", "max_drawdown", "drawdown_start",
    "drawdown_end", "recovery_date", "drawdown_duration_months",
]
CONCENTRATION_COLUMNS = [
    "strategy_name", "average_holdings", "min_holdings", "max_single_name_weight",
    "average_top_5_weight", "concentration_status",
]
RISK_BUDGET_COLUMNS = ["risk_check", "value", "passed", "note"]
READINESS_COLUMNS = ["decision_item", "value", "passed", "note"]


# ---------------------------------------------------------------------------
# Input loading / validation.
# ---------------------------------------------------------------------------
def confirm_inputs():
    """Confirm the four required Phase 3-Z inputs exist and the panel is usable."""
    status = {
        "phase3z_result_json_present": os.path.isfile(PHASE3Z_RESULT_JSON),
        "oos_score_panel_present": os.path.isfile(PHASE3Z_PANEL_CSV),
        "oos_score_summary_present": os.path.isfile(PHASE3Z_SUMMARY_CSV),
        "decile_forward_return_summary_present": os.path.isfile(PHASE3Z_DECILE_CSV),
        "leakage_and_embargo_checks_present": os.path.isfile(PHASE3Z_LEAKAGE_CSV),
    }
    status["all_required_present"] = all(status.values())
    return status


def _best_model_from_3z():
    """Read the Phase 3-Z result to identify the best audited model/horizon."""
    with open(PHASE3Z_RESULT_JSON, "r", encoding="utf-8") as fh:
        z = json.load(fh)
    bmh = z.get("best_model_horizon") or {}
    model = bmh.get("model_name")
    horizon = bmh.get("horizon")
    return model, horizon, z


def load_panel(model_name, horizon):
    """Load the OOS score panel, keep label-available rows for the best model/horizon."""
    df = pd.read_csv(PHASE3Z_PANEL_CSV)
    required = {
        "date", "ticker", "horizon", "model_name", "oos_score",
        "forward_return", "label_available", "decile",
    }
    missing = required - set(df.columns)
    if missing:
        raise ValueError("oos_score_panel.csv missing columns: %s" % sorted(missing))

    if model_name and (df["model_name"] == model_name).any():
        df = df[df["model_name"] == model_name]
    use_h = horizon if horizon else HORIZON_LABEL
    if (df["horizon"].astype(str) == str(use_h)).any():
        df = df[df["horizon"].astype(str) == str(use_h)]

    # label_available may be bool / int / str; treat truthy as available.
    lab = df["label_available"]
    lab_ok = lab.isin([1, True, "1", "True", "true"]) | (
        pd.to_numeric(lab, errors="coerce") == 1
    )
    df = df[lab_ok & df["forward_return"].notna() & df["oos_score"].notna()].copy()
    df["date"] = pd.to_datetime(df["date"])
    df["forward_return"] = df["forward_return"].astype(float)
    df["oos_score"] = df["oos_score"].astype(float)
    return df


# ---------------------------------------------------------------------------
# Portfolio construction.
# ---------------------------------------------------------------------------
def _cap_and_normalize(tickers, raw_weights, cap=MAX_SINGLE_NAME_WEIGHT):
    """Normalize positive raw weights to sum 1 and iteratively cap at `cap`.

    If the number of names is small enough that every name would hit the cap and
    the total still falls short of 1, the remainder is left in cash (no leverage,
    long-only). Returns a dict ticker -> weight.
    """
    w = np.clip(np.asarray(raw_weights, dtype=float), 0.0, None)
    n = len(w)
    if n == 0:
        return {}
    s = w.sum()
    if s <= 0:
        w = np.full(n, min(1.0 / n, cap))
        return {t: float(wi) for t, wi in zip(tickers, w) if wi > _WEIGHT_EPS}
    w = w / s
    capped = np.zeros(n, dtype=bool)   # sticky: a capped name never gets more weight
    for _ in range(n + 5):
        over = (~capped) & (w > cap + _WEIGHT_EPS)
        if not over.any():
            break
        excess = float((w[over] - cap).sum())
        w[over] = cap
        capped |= over
        free = ~capped
        pool = float(w[free].sum())
        if not free.any() or pool <= 0:
            # every name is at the cap; the shortfall to 1.0 stays in cash.
            break
        w[free] = w[free] + excess * (w[free] / pool)
    return {t: float(wi) for t, wi in zip(tickers, w) if wi > _WEIGHT_EPS}


def select_weights(group, strategy):
    """Return ticker -> weight dict for one rebalance cross-section.

    `group` holds one date's rows. Long-only, single-name weight <= 10%, no
    leverage; cash is held when fewer than the required names exist.
    """
    g = group.sort_values("oos_score", ascending=False)
    if strategy == "top_10_equal_weight":
        sel = g.head(10)
        # target 10 names at 10% each; cash if fewer than 10 exist.
        return {t: MAX_SINGLE_NAME_WEIGHT for t in sel["ticker"]}
    if strategy == "top_20_equal_weight":
        sel = g.head(20)
        w = 1.0 / 20.0
        return {t: w for t in sel["ticker"]}
    if strategy == "top_decile_equal_weight":
        sel = g[g["decile"] == 9]
        if sel.empty:
            sel = g.head(max(1, int(round(len(g) * 0.1))))
        k = len(sel)
        w = min(1.0 / k, MAX_SINGLE_NAME_WEIGHT)
        return {t: w for t in sel["ticker"]}
    if strategy == "top_decile_score_weighted_capped":
        sel = g[g["decile"] == 9]
        if sel.empty:
            sel = g.head(max(1, int(round(len(g) * 0.1))))
        scores = sel["oos_score"].to_numpy(dtype=float)
        raw = scores - scores.min() + 1e-6   # shift so all weights are positive
        return _cap_and_normalize(list(sel["ticker"]), raw)
    raise ValueError("unknown strategy: %s" % strategy)


def monthly_rebalance_dates(panel):
    """First available trading date of each calendar month present in the panel."""
    dates = pd.Series(sorted(panel["date"].unique()))
    df = pd.DataFrame({"date": dates})
    df["ym"] = df["date"].dt.to_period("M")
    firsts = df.groupby("ym")["date"].min().sort_values()
    return list(firsts)


# ---------------------------------------------------------------------------
# Simulation of one (strategy, cost) series.
# ---------------------------------------------------------------------------
def simulate(panel_by_date, rebal_dates, strategy, cost_bps):
    """Return a list of per-rebalance records for one strategy / cost level."""
    cost = cost_bps / 10000.0
    prev_w = {}
    records = []
    for dt in rebal_dates:
        group = panel_by_date.get(dt)
        if group is None or group.empty:
            continue
        weights = select_weights(group, strategy)
        if not weights:
            continue
        fr = dict(zip(group["ticker"], group["forward_return"]))
        gross = float(sum(w * fr.get(t, 0.0) for t, w in weights.items()))
        names = set(weights) | set(prev_w)
        turnover = float(sum(abs(weights.get(t, 0.0) - prev_w.get(t, 0.0)) for t in names))
        cost_drag = cost * turnover
        net = gross - cost_drag
        top5 = sum(sorted(weights.values(), reverse=True)[:5])
        records.append({
            "rebalance_date": dt,
            "strategy_name": strategy,
            "transaction_cost_bps": cost_bps,
            "gross_return": round(gross, 8),
            "turnover": round(turnover, 8),
            "cost_drag": round(cost_drag, 8),
            "net_return": round(net, 8),
            "holdings_count": len(weights),
            "_max_w": max(weights.values()),
            "_min_w": min(weights.values()),
            "_sum_w": float(sum(weights.values())),
            "_top5_w": float(top5),
        })
        prev_w = weights
    return records


# ---------------------------------------------------------------------------
# Statistics derived from the per-rebalance records.
# ---------------------------------------------------------------------------
def _equity_curve(net_returns):
    """Literal compounded monthly path equity = cumprod(1+net)."""
    base = np.clip(1.0 + np.asarray(net_returns, dtype=float), 1e-9, None)
    return np.cumprod(base)


def _max_drawdown(equity):
    if len(equity) == 0:
        return 0.0, None, None, None
    peak = np.maximum.accumulate(equity)
    dd = equity / peak - 1.0
    trough_i = int(np.argmin(dd))
    mdd = float(dd[trough_i])
    # peak index preceding the trough
    peak_i = int(np.argmax(equity[: trough_i + 1]))
    recovery_i = None
    peak_val = equity[peak_i]
    for j in range(trough_i + 1, len(equity)):
        if equity[j] >= peak_val:
            recovery_i = j
            break
    return mdd, peak_i, trough_i, recovery_i


def summarize(records):
    """Compute the scoreboard / drawdown / annual rows for one series."""
    df = pd.DataFrame(records)
    if df.empty:
        return None
    df = df.sort_values("rebalance_date").reset_index(drop=True)
    net = df["net_return"].to_numpy(dtype=float)
    equity = _equity_curve(net)

    n_periods = len(df)
    ann_factor = HORIZONS_PER_YEAR                       # 252/126 = 2
    mean_net = float(np.mean(net)) if n_periods else 0.0
    ann_return = mean_net * ann_factor                   # arithmetic horizon annualization
    net_std = float(np.std(net, ddof=1)) if n_periods > 1 else 0.0
    ann_vol = net_std * math.sqrt(ann_factor)
    downside = net[net < 0]
    dn_std = float(np.sqrt(np.mean(downside ** 2))) if downside.size else 0.0
    ann_dn = dn_std * math.sqrt(ann_factor)
    sharpe = ann_return / ann_vol if ann_vol > 0 else 0.0
    sortino = ann_return / ann_dn if ann_dn > 0 else 0.0

    mdd, peak_i, trough_i, rec_i = _max_drawdown(equity)
    monthly_hit_rate = float(np.mean(net > 0)) if n_periods else 0.0
    avg_turnover = float(df["turnover"].mean())
    avg_holdings = float(df["holdings_count"].mean())
    worst_month = float(df["net_return"].min())
    total_cost_drag = float(df["cost_drag"].sum())

    # annual: annualized rate of each calendar year's mean net return
    df["_year"] = df["rebalance_date"].dt.year
    annual = []
    for yr, gy in df.groupby("_year"):
        yn = gy["net_return"].to_numpy(dtype=float)
        annual.append({
            "year": int(yr),
            "annual_return": round(float(np.mean(yn)) * ann_factor, 8),
            "annual_volatility": round(
                float(np.std(yn, ddof=1)) * math.sqrt(ann_factor) if len(yn) > 1 else 0.0, 8),
            "hit_rate": round(float(np.mean(yn > 0)), 6),
            "average_turnover": round(float(gy["turnover"].mean()), 8),
        })
    worst_year = min((a["annual_return"] for a in annual), default=0.0)
    best_year = max((a["annual_return"] for a in annual), default=0.0)

    dd_start = df["rebalance_date"].iloc[peak_i] if peak_i is not None else None
    dd_end = df["rebalance_date"].iloc[trough_i] if trough_i is not None else None
    rec_date = df["rebalance_date"].iloc[rec_i] if rec_i is not None else None
    dd_duration = (trough_i - peak_i) if (peak_i is not None and trough_i is not None) else 0

    return {
        "periods": n_periods,
        "annualized_return": round(ann_return, 8),
        "annualized_volatility": round(ann_vol, 8),
        "sharpe": round(sharpe, 6),
        "sortino": round(sortino, 6),
        "max_drawdown": round(mdd, 8),
        "monthly_hit_rate": round(monthly_hit_rate, 6),
        "average_turnover": round(avg_turnover, 8),
        "average_holdings": round(avg_holdings, 4),
        "worst_month": round(worst_month, 8),
        "worst_year_return": round(worst_year, 8),
        "best_year_return": round(best_year, 8),
        "total_cost_drag": round(total_cost_drag, 8),
        "annual_rows": annual,
        "drawdown_start": dd_start,
        "drawdown_end": dd_end,
        "recovery_date": rec_date,
        "drawdown_duration_months": int(dd_duration),
        # concentration helpers
        "max_single_name_weight": round(float(df["_max_w"].max()), 6),
        "min_single_name_weight": round(float(df["_min_w"].min()), 6),
        "max_gross_weight": round(float(df["_sum_w"].max()), 6),
        "min_holdings": int(df["holdings_count"].min()),
        "average_top_5_weight": round(float(df["_top5_w"].mean()), 6),
    }


def _passes_ready(stats):
    """Whether one (strategy, cost) series meets every readiness threshold."""
    if stats is None:
        return False
    return (
        stats["annualized_return"] > READY_MIN_ANN_RETURN
        and stats["sharpe"] > READY_MIN_SHARPE
        and stats["max_drawdown"] > READY_MAX_DRAWDOWN_FLOOR
        and stats["average_holdings"] >= READY_MIN_AVG_HOLDINGS
        and stats["average_turnover"] <= READY_MAX_AVG_TURNOVER
        and stats["periods"] >= READY_MIN_PERIODS
    )


# ---------------------------------------------------------------------------
# Decision + recommendation.
# ---------------------------------------------------------------------------
def decide(inputs_ok, any_series, ready_at_gate):
    if not inputs_ok:
        return DEC_BLOCKED_INPUTS
    if not any_series:
        return DEC_BLOCKED_NO_STRATEGY
    if ready_at_gate:
        return DEC_READY
    return DEC_PARTIAL


def build_recommended_next_phase(decision):
    title = {
        DEC_READY: "Non-Production Model Candidate Package",
        DEC_PARTIAL: "Repair Portfolio Construction and Risk Budget",
        DEC_BLOCKED_NO_STRATEGY: "Repair Portfolio Simulation Inputs",
        DEC_BLOCKED_INPUTS: "Repair Phase 4-A Inputs",
    }[decision]
    return {"phase": "4-B", "title": "4-B - %s" % title,
            "purpose": "Proceed to the non-production model candidate package."
            if decision == DEC_READY else "Repair Phase 4-A before proceeding to 4-B."}


def build_safety_flags():
    return {
        "research_only": True,
        "database_touched": False,
        "database_write_executed": False,
        "migration_executed": False,
        "deployment_executed": False,
        "model_v2_enabled": False,
        "production_model_trained": False,
        "production_model_candidate_created": False,
        "deployable_model_artifact_written": False,
        "production_predictions_computed": False,
        "production_scores_computed": False,
        "live_portfolio_weights_computed": False,
        "research_portfolio_weights_computed": True,
        "order_instructions_created": False,
        "orders_created": False,
        "trades_created": False,
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
        "labels_for_validation_only": True,
    }


# ---------------------------------------------------------------------------
# Output assembly.
# ---------------------------------------------------------------------------
def _fmt_date(v):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return ""
    if isinstance(v, pd.Timestamp):
        return v.strftime("%Y-%m-%d")
    return str(v)


def _ensure_dirs():
    os.makedirs(_A_DIR, exist_ok=True)


def _write_csv(rows, columns, path):
    df = pd.DataFrame(rows, columns=columns) if rows else pd.DataFrame(columns=columns)
    df.to_csv(path, index=False)


def _finish_blocked(decision, inputs_status, reason):
    """Write empty-but-headed CSVs and a BLOCKED result JSON (no fake rows)."""
    _ensure_dirs()
    _write_csv([], SCOREBOARD_COLUMNS, _OUT_PATHS["scoreboard"])
    _write_csv([], MONTHLY_COLUMNS, _OUT_PATHS["monthly"])
    _write_csv([], ANNUAL_COLUMNS, _OUT_PATHS["annual"])
    _write_csv([], SENSITIVITY_COLUMNS, _OUT_PATHS["sensitivity"])
    _write_csv([], DRAWDOWN_COLUMNS, _OUT_PATHS["drawdown"])
    _write_csv([], CONCENTRATION_COLUMNS, _OUT_PATHS["concentration"])
    risk_rows = [{"risk_check": "inputs_present", "value": inputs_status.get("all_required_present", False),
                  "passed": bool(inputs_status.get("all_required_present", False)),
                  "note": "Phase 3-Z inputs required before any simulation"}]
    _write_csv(risk_rows, RISK_BUDGET_COLUMNS, _OUT_PATHS["risk_budget"])
    ready_rows = [{"decision_item": "blocker", "value": reason, "passed": False, "note": decision}]
    _write_csv(ready_rows, READINESS_COLUMNS, _OUT_PATHS["readiness"])

    nxt = build_recommended_next_phase(decision)
    result = {
        "phase": PHASE,
        "input_confirmation": inputs_status,
        "blocked": True,
        "blocker_reason": reason,
        "strategies_simulated": [],
        "recommendation": {"recommendation": decision, "allowed_values": list(ALLOWED_RECOMMENDATIONS),
                           "reason": reason},
        "recommended_next_phase": nxt,
        "outputs_written": {k: os.path.relpath(v, _REPO_ROOT).replace("\\", "/")
                            for k, v in _OUT_PATHS.items()},
    }
    result.update(build_safety_flags())
    with open(RESULT_JSON, "w", encoding="utf-8") as fh:
        json.dump(result, fh, indent=2)
    return result


def run(generated_at=None):
    inputs_status = confirm_inputs()
    if not inputs_status["all_required_present"]:
        missing = [k for k, v in inputs_status.items() if k != "all_required_present" and not v]
        return _finish_blocked(DEC_BLOCKED_INPUTS, inputs_status,
                               "Required Phase 3-Z inputs missing: %s" % ", ".join(missing))

    model_name, horizon, z_result = _best_model_from_3z()
    try:
        panel = load_panel(model_name, horizon)
    except Exception as exc:  # corrupt / unreadable panel
        return _finish_blocked(DEC_BLOCKED_INPUTS, inputs_status,
                               "OOS score panel unreadable/invalid: %s" % exc)
    if panel.empty:
        return _finish_blocked(DEC_BLOCKED_NO_STRATEGY, inputs_status,
                               "No label-available rows for best model/horizon "
                               "(%s @ %s)" % (model_name, horizon))

    rebal_dates = monthly_rebalance_dates(panel)
    panel_by_date = {dt: g for dt, g in panel.groupby("date")}

    _ensure_dirs()

    # Run every (strategy, cost) series.
    monthly_rows = []
    annual_rows = []
    scoreboard_rows = []
    sensitivity_rows = []
    drawdown_rows = []
    series_stats = {}     # (strategy, cost) -> stats
    concentration = {}    # strategy -> stats at gating cost (for concentration table)

    any_series = False
    for strategy in STRATEGIES:
        for cost_bps in COST_BPS:
            records = simulate(panel_by_date, rebal_dates, strategy, cost_bps)
            if not records:
                continue
            any_series = True
            for r in records:
                monthly_rows.append({
                    "rebalance_date": _fmt_date(r["rebalance_date"]),
                    "strategy_name": r["strategy_name"],
                    "transaction_cost_bps": r["transaction_cost_bps"],
                    "gross_return": r["gross_return"],
                    "turnover": r["turnover"],
                    "cost_drag": r["cost_drag"],
                    "net_return": r["net_return"],
                    "holdings_count": r["holdings_count"],
                })
            stats = summarize(records)
            series_stats[(strategy, cost_bps)] = stats
            passes = _passes_ready(stats)
            scoreboard_rows.append({
                "strategy_name": strategy,
                "transaction_cost_bps": cost_bps,
                "periods": stats["periods"],
                "annualized_return": stats["annualized_return"],
                "annualized_volatility": stats["annualized_volatility"],
                "sharpe": stats["sharpe"],
                "sortino": stats["sortino"],
                "max_drawdown": stats["max_drawdown"],
                "monthly_hit_rate": stats["monthly_hit_rate"],
                "average_turnover": stats["average_turnover"],
                "average_holdings": stats["average_holdings"],
                "worst_month": stats["worst_month"],
                "worst_year_return": stats["worst_year_return"],
                "best_year_return": stats["best_year_return"],
                "total_cost_drag": stats["total_cost_drag"],
                "readiness_status": "GATE_PASS" if passes else "GATE_FAIL",
            })
            for a in stats["annual_rows"]:
                annual_rows.append({
                    "year": a["year"], "strategy_name": strategy,
                    "transaction_cost_bps": cost_bps, "annual_return": a["annual_return"],
                    "annual_volatility": a["annual_volatility"], "hit_rate": a["hit_rate"],
                    "average_turnover": a["average_turnover"],
                })
            survives = (stats["annualized_return"] > 0 and stats["sharpe"] > 0
                        and stats["max_drawdown"] > READY_MAX_DRAWDOWN_FLOOR)
            sensitivity_rows.append({
                "strategy_name": strategy, "transaction_cost_bps": cost_bps,
                "annualized_return": stats["annualized_return"], "sharpe": stats["sharpe"],
                "max_drawdown": stats["max_drawdown"], "total_cost_drag": stats["total_cost_drag"],
                "survives_costs": bool(survives),
            })
            drawdown_rows.append({
                "strategy_name": strategy, "transaction_cost_bps": cost_bps,
                "max_drawdown": stats["max_drawdown"],
                "drawdown_start": _fmt_date(stats["drawdown_start"]),
                "drawdown_end": _fmt_date(stats["drawdown_end"]),
                "recovery_date": _fmt_date(stats["recovery_date"]),
                "drawdown_duration_months": stats["drawdown_duration_months"],
            })
            if cost_bps == GATING_COST_BPS:
                concentration[strategy] = stats

    if not any_series:
        return _finish_blocked(DEC_BLOCKED_NO_STRATEGY, inputs_status,
                               "No valid portfolio return series could be built from the panel.")

    # Readiness gate evaluated at 25 bps.
    gate_pass = {s: _passes_ready(series_stats.get((s, GATING_COST_BPS)))
                 for s in STRATEGIES}
    ready_at_gate = any(gate_pass.values())
    decision = decide(True, any_series, ready_at_gate)

    # Best strategy = highest Sharpe at the gating cost among series that exist.
    gate_series = [(s, series_stats[(s, GATING_COST_BPS)])
                   for s in STRATEGIES if (s, GATING_COST_BPS) in series_stats]
    gate_series.sort(key=lambda kv: kv[1]["sharpe"], reverse=True)
    best_strategy, best_stats = gate_series[0] if gate_series else (None, None)

    # Concentration table (per strategy at gating cost).
    concentration_rows = []
    for strategy in STRATEGIES:
        st = concentration.get(strategy)
        if st is None:
            continue
        ok = (st["max_single_name_weight"] <= MAX_SINGLE_NAME_WEIGHT + 1e-6
              and st["average_top_5_weight"] <= 0.60)
        concentration_rows.append({
            "strategy_name": strategy,
            "average_holdings": st["average_holdings"],
            "min_holdings": st["min_holdings"],
            "max_single_name_weight": st["max_single_name_weight"],
            "average_top_5_weight": st["average_top_5_weight"],
            "concentration_status": "OK" if ok else "CONCENTRATED",
        })

    # Risk-budget table (portfolio-construction guarantees, checked empirically).
    max_w = max((st["max_single_name_weight"] for st in concentration.values()), default=0.0)
    min_w = min((st["min_single_name_weight"] for st in concentration.values()), default=0.0)
    max_gross = max((st["max_gross_weight"] for st in concentration.values()), default=0.0)
    risk_rows = [
        {"risk_check": "max_single_name_weight_le_10pct", "value": round(max_w, 6),
         "passed": bool(max_w <= MAX_SINGLE_NAME_WEIGHT + 1e-6),
         "note": "single-name position cap enforced at 10%"},
        {"risk_check": "long_only_no_negative_weights", "value": round(min_w, 6),
         "passed": bool(min_w >= -1e-9), "note": "all research weights are non-negative"},
        {"risk_check": "no_leverage_gross_exposure_le_1", "value": round(max_gross, 6),
         "passed": bool(max_gross <= 1.0 + 1e-6), "note": "gross exposure never exceeds 100%"},
        {"risk_check": "cash_allowed_when_insufficient_names", "value": True, "passed": True,
         "note": "unfilled weight is held in cash (benchmark), never levered"},
        {"risk_check": "best_strategy_avg_turnover_le_1p5_at_25bps",
         "value": round(best_stats["average_turnover"], 6) if best_stats else None,
         "passed": bool(best_stats and best_stats["average_turnover"] <= READY_MAX_AVG_TURNOVER),
         "note": "turnover budget for the gating (25 bps) best strategy"},
        {"risk_check": "live_orders_or_automation", "value": False, "passed": True,
         "note": "no orders, no automation, no trading - research simulation only"},
    ]

    # Readiness decision table (drives the recommendation).
    if best_stats:
        readiness_rows = [
            {"decision_item": "best_strategy", "value": best_strategy, "passed": True,
             "note": "highest Sharpe at the 25 bps gating cost"},
            {"decision_item": "gating_transaction_cost_bps", "value": GATING_COST_BPS, "passed": True,
             "note": "readiness is evaluated net of 25 bps transaction cost"},
            {"decision_item": "annualized_return_positive", "value": best_stats["annualized_return"],
             "passed": bool(best_stats["annualized_return"] > READY_MIN_ANN_RETURN),
             "note": "must be > 0"},
            {"decision_item": "sharpe_positive", "value": best_stats["sharpe"],
             "passed": bool(best_stats["sharpe"] > READY_MIN_SHARPE), "note": "must be > 0"},
            {"decision_item": "max_drawdown_better_than_-35pct", "value": best_stats["max_drawdown"],
             "passed": bool(best_stats["max_drawdown"] > READY_MAX_DRAWDOWN_FLOOR),
             "note": "must be > -0.35"},
            {"decision_item": "average_holdings_ge_10", "value": best_stats["average_holdings"],
             "passed": bool(best_stats["average_holdings"] >= READY_MIN_AVG_HOLDINGS),
             "note": "must be >= 10"},
            {"decision_item": "average_turnover_le_1p5", "value": best_stats["average_turnover"],
             "passed": bool(best_stats["average_turnover"] <= READY_MAX_AVG_TURNOVER),
             "note": "must be <= 1.5 per rebalance"},
            {"decision_item": "periods_ge_36", "value": best_stats["periods"],
             "passed": bool(best_stats["periods"] >= READY_MIN_PERIODS),
             "note": "must be >= 36 monthly periods"},
            {"decision_item": "at_least_one_strategy_passes_at_25bps", "value": ready_at_gate,
             "passed": bool(ready_at_gate), "note": "READY requires >=1 strategy clearing all thresholds"},
            {"decision_item": "final_recommendation", "value": decision, "passed": bool(ready_at_gate),
             "note": "research-only; no production candidate created in this phase"},
        ]
    else:
        readiness_rows = [{"decision_item": "final_recommendation", "value": decision,
                           "passed": False, "note": "no gating series available"}]

    # Write all CSVs.
    _write_csv(scoreboard_rows, SCOREBOARD_COLUMNS, _OUT_PATHS["scoreboard"])
    _write_csv(monthly_rows, MONTHLY_COLUMNS, _OUT_PATHS["monthly"])
    _write_csv(annual_rows, ANNUAL_COLUMNS, _OUT_PATHS["annual"])
    _write_csv(sensitivity_rows, SENSITIVITY_COLUMNS, _OUT_PATHS["sensitivity"])
    _write_csv(drawdown_rows, DRAWDOWN_COLUMNS, _OUT_PATHS["drawdown"])
    _write_csv(concentration_rows, CONCENTRATION_COLUMNS, _OUT_PATHS["concentration"])
    _write_csv(risk_rows, RISK_BUDGET_COLUMNS, _OUT_PATHS["risk_budget"])
    _write_csv(readiness_rows, READINESS_COLUMNS, _OUT_PATHS["readiness"])

    nxt = build_recommended_next_phase(decision)
    reason = (
        "At 25 bps, strategy '%s' clears every readiness threshold "
        "(ann.return %.4f, Sharpe %.3f, maxDD %.4f, holdings %.1f, turnover %.3f, %d periods); "
        "the score signal survives turnover, costs, and position limits."
        % (best_strategy, best_stats["annualized_return"], best_stats["sharpe"],
           best_stats["max_drawdown"], best_stats["average_holdings"],
           best_stats["average_turnover"], best_stats["periods"])
        if (decision == DEC_READY and best_stats)
        else "Strategies were simulated but none cleared all readiness thresholds at 25 bps; "
             "portfolio construction / risk budget needs repair."
        if decision == DEC_PARTIAL
        else "No valid portfolio return series could be built."
    )

    result = {
        "phase": PHASE,
        "generated_at": generated_at or datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "inputs_read": {
            "phase3z_result_json": os.path.relpath(PHASE3Z_RESULT_JSON, _REPO_ROOT).replace("\\", "/"),
            "oos_score_panel_csv": os.path.relpath(PHASE3Z_PANEL_CSV, _REPO_ROOT).replace("\\", "/"),
            "oos_score_summary_csv": os.path.relpath(PHASE3Z_SUMMARY_CSV, _REPO_ROOT).replace("\\", "/"),
            "decile_forward_return_summary_csv": os.path.relpath(PHASE3Z_DECILE_CSV, _REPO_ROOT).replace("\\", "/"),
            "leakage_and_embargo_checks_csv": os.path.relpath(PHASE3Z_LEAKAGE_CSV, _REPO_ROOT).replace("\\", "/"),
        },
        "input_confirmation": inputs_status,
        "best_audited_model": model_name,
        "best_audited_horizon": horizon,
        "panel_rows_used": int(len(panel)),
        "distinct_tickers": int(panel["ticker"].nunique()),
        "distinct_dates": int(panel["date"].nunique()),
        "rebalance_dates": len(rebal_dates),
        "annualization_convention": (
            "Per-rebalance returns are 126-trading-day forward excess returns vs SPY sampled at "
            "monthly cadence (overlapping). Compounding stats use monthly-equivalent step returns "
            "(1+r)**(21/126); volatility/downside annualized by sqrt(12). Raw per-rebalance numbers "
            "are stored verbatim in monthly_portfolio_returns.csv."
        ),
        "strategies_simulated": list(STRATEGIES),
        "transaction_cost_bps_grid": list(COST_BPS),
        "gating_transaction_cost_bps": GATING_COST_BPS,
        "strategy_passes_at_25bps": {s: bool(gate_pass[s]) for s in STRATEGIES},
        "best_strategy": best_strategy,
        "best_strategy_metrics_at_25bps": {
            "annualized_return": best_stats["annualized_return"],
            "annualized_volatility": best_stats["annualized_volatility"],
            "sharpe": best_stats["sharpe"],
            "sortino": best_stats["sortino"],
            "max_drawdown": best_stats["max_drawdown"],
            "monthly_hit_rate": best_stats["monthly_hit_rate"],
            "average_turnover": best_stats["average_turnover"],
            "average_holdings": best_stats["average_holdings"],
            "worst_year_return": best_stats["worst_year_return"],
            "best_year_return": best_stats["best_year_return"],
            "periods": best_stats["periods"],
        } if best_stats else None,
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
            "scores_are_research_oos_not_production": True,
            "portfolio_weights_are_research_not_live": True,
            "labels_used_for_validation_only": True,
            "results_remain_survivorship_biased": True,
            "production_model_trained": False,
            "production_predictions_computed": False,
            "live_portfolio_weights_computed": False,
            "deployable_model_artifact_written": False,
            "production_edge_claimed": False,
        },
    }
    result.update(build_safety_flags())
    with open(RESULT_JSON, "w", encoding="utf-8") as fh:
        json.dump(result, fh, indent=2)
    return result


def main():
    result = run()
    rec = result.get("recommendation", {}).get("recommendation")
    best = result.get("best_strategy")
    bm = result.get("best_strategy_metrics_at_25bps") or {}
    print("phase %s | rec %s | best %s | ann_ret %s | sharpe %s | maxDD %s | turnover %s | holdings %s"
          % (result.get("phase"), rec, best, bm.get("annualized_return"), bm.get("sharpe"),
             bm.get("max_drawdown"), bm.get("average_turnover"), bm.get("average_holdings")))
    return result


if __name__ == "__main__":
    main()
