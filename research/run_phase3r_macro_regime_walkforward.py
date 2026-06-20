"""Phase 3-R - Macro/Inflation Regime Feature Layer + Walk-Forward Re-Test.

This is a research-only phase on top of the Phase 3-Q robustness diagnosis. Phase 3-Q found
that the Phase 3-P best model (ridge_technical_only @ 126d) is positive gross and survives
realistic transaction cost, but is regime-fragile: its 2021 worst-year IC (-0.108) is a
regime/style effect (the model earns IC in volatile/risk-off markets and is weakest in calm
risk-on / low-volatility years, and 2021 was almost entirely low-vol / risk-on). Phase 3-Q's
top improvement priority was to add a macro/inflation/rates regime feature family that encodes
the rate/rotation regime the technical model cannot see.

Phase 3-R attempts exactly that, using ONLY local/free, non-faked data:

  1. Confirm Phase 3-Q (phase == "3-Q", weak-fixable/pass recommendation, next phase 3-R,
     no production artifacts).
  2. Build a macro data inventory by scanning local repo paths only (no internet).
  3. Build a macro feature registry (CPI / inflation / fed funds / treasury / yield-curve /
     real-rate / oil / dollar / derived regime flags) with point-in-time availability rules.
  4. If usable local macro data exists: merge it point-in-time (as-of) into a Phase 3-P panel
     rebuilt in memory and re-run the research-only walk-forward model comparison to test
     whether macro/regime features improve bad-year (2021) robustness.
  5. If no usable local macro data exists: STOP with
     MACRO_REGIME_WALKFORWARD_BLOCKED_NEEDS_LOCAL_MACRO_DATA, write the exact macro data
     requirements into the docs and JSON, and still write every required output CSV (headers).

It NEVER fakes macro or sentiment data. It touches no database, runs no migration, restarts no
prediction service, enables no model-v2 serving flag, writes nothing to the D: drive, calls no
provider / paid-vendor / Alpha Vantage / FRED API, places no orders, trades nothing, creates no
production model candidate, computes no production predictions / scores / portfolio weights /
order instructions, and writes no deployable model artifact (no serialized-estimator file of any
kind: no pickle, no joblib dump, no ONNX / HDF5 / Keras / Torch export). Forward labels are used
for validation-only out-of-sample diagnostics; they are never turned into production predictions.

Run:
    python -B research/run_phase3r_macro_regime_walkforward.py
Test:
    python -B tests/test_phase3r_macro_regime_walkforward.py
"""
from __future__ import annotations

import csv
import json
import math
import os
import sys
from datetime import datetime

PHASE = "3-R"

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_HERE)

# --------------------------------------------------------------------------- #
# Paths
# --------------------------------------------------------------------------- #
_OUT_DIR = os.path.join(_REPO_ROOT, "research", "output")
_R_DIR = os.path.join(_OUT_DIR, "phase3r_macro_regime_walkforward")
RESULT_JSON = os.path.join(_OUT_DIR, "phase3r_macro_regime_walkforward.json")

PHASE3Q_RESULT_JSON = os.path.join(_OUT_DIR, "phase3q_model_robustness_diagnostics.json")
PHASE3P_RESULT_JSON = os.path.join(_OUT_DIR, "phase3p_multisignal_walkforward_model.json")
PHASE3O_RESULT_JSON = os.path.join(_OUT_DIR, "phase3o_multisignal_feature_factory.json")

# Allowed read-only inputs (the price spine lives on D:; only ever read, never written).
PRICE_CSV = os.path.join("D:\\", "Stock_Prediction_app_data", "phase2k_g", "output",
                         "phase2k_g_expanded_price_history_free.csv")
PHASE3L_PANEL_CSV = os.path.join(
    _OUT_DIR, "phase3l_sec_universe_signal_gate", "aligned_feature_price_panel_universe.csv")

# Local-only macro search scope.
_SEARCH_ROOTS = [
    os.path.join(_REPO_ROOT, "research", "input"),
    os.path.join(_REPO_ROOT, "research", "output"),
    os.path.join(_REPO_ROOT, "data"),
    _REPO_ROOT,  # top-level CSVs only (non-recursive for the repo root)
]
# Strong, specific macro tokens. Detection is FILENAME-anchored: a CSV is only a macro candidate
# if its file name contains one of these tokens (or the generic "macro_" prefix). This avoids
# false positives from common substrings inside unrelated feature panels (e.g. "ic_hit_rate"
# containing "rate", or "dollar_volume" containing "dollar").
_MACRO_FAMILIES = {
    "inflation": ["cpi", "inflation", "cpiaucsl", "cuur0000", "core_cpi", "corecpi", "ppi",
                  "pce_price"],
    "policy_rate": ["fed_funds", "fedfunds", "fedfundsrate", "effr", "policy_rate", "dff"],
    "rates": ["treasury", "yield_curve", "dgs10", "dgs2", "real_rate", "t10y", "t2y", "ust10",
              "ust2", "ten_year_yield", "two_year_yield"],
    "labor": ["unemploy", "unrate", "nonfarm", "payroll"],
    "commodity": ["wti", "brent", "crude_oil", "dcoilwti"],
    "fx": ["dxy", "dollar_index", "broad_dollar", "trade_weighted", "dtwexb"],
    "growth": ["real_gdp", "gdp_yoy", "ism_pmi", "ism_manufacturing"],
}
# Column-name fragments that CONFIRM a filename-matched file actually carries a macro series.
_MACRO_COLUMN_HINTS = [
    "cpi", "cpiaucsl", "inflation", "ppi", "pce", "fed_funds", "fedfunds", "effr", "dff",
    "treasury", "yield", "dgs10", "dgs2", "real_rate", "unrate", "unemploy", "payroll", "wti",
    "crude", "dcoilwtico", "dxy", "dollar_index", "broad_dollar", "dtwexb", "dtwexbgs",
    "fed_funds_rate", "policy_rate",
]

# Canonical FRED column mapping (Phase 3-S ingestion): map raw FRED series headers (and already
# canonical headers) onto the Phase 3-R macro feature namespace. Used only to read REAL local
# values - it never fabricates a series.
_FRED_COLUMN_MAP = {
    "cpiaucsl": "cpi_index_value",
    "cpi_index_value": "cpi_index_value",
    "fedfunds": "fed_funds_level",
    "dff": "fed_funds_level",
    "fed_funds_rate": "fed_funds_level",
    "fed_funds_level": "fed_funds_level",
    "dgs10": "treasury_10y",
    "treasury_10y": "treasury_10y",
    "dgs2": "treasury_2y",
    "treasury_2y": "treasury_2y",
    "dcoilwtico": "wti_price",
    "wti_price": "wti_price",
    "dtwexbgs": "broad_dollar_index",
    "broad_dollar_index": "broad_dollar_index",
}

BAD_YEAR = 2021
TRADING_DAYS_PER_MONTH = 21.0
DEFAULT_MONTHLY_RELEASE_LAG_DAYS = 21  # conservative availability lag for monthly releases

# Decision values.
REC_IMPROVES = "MACRO_REGIME_WALKFORWARD_IMPROVES_ROBUSTNESS"
REC_WEAK = "MACRO_REGIME_WALKFORWARD_WEAK_NO_IMPROVEMENT"
REC_BLOCKED_DATA = "MACRO_REGIME_WALKFORWARD_BLOCKED_NEEDS_LOCAL_MACRO_DATA"
REC_BLOCKED_INPUTS = "MACRO_REGIME_WALKFORWARD_BLOCKED_INPUTS"
ALLOWED_RECOMMENDATIONS = [REC_IMPROVES, REC_WEAK, REC_BLOCKED_DATA, REC_BLOCKED_INPUTS]

MEAN_IC_DROP_TOLERANCE = 0.25  # macro model mean IC must not fall by more than 25%

# Preferred macro feature catalogue (implemented only if the underlying data exists).
_PREFERRED_FEATURES = [
    ("cpi_yoy", "inflation", "monthly",
     "lag to release date if present, else default 21-calendar-day availability lag"),
    ("cpi_mom", "inflation", "monthly",
     "lag to release date if present, else default 21-calendar-day availability lag"),
    ("inflation_acceleration", "inflation", "monthly",
     "derived from cpi_yoy change; inherits the monthly availability lag"),
    ("inflation_regime_high_low", "inflation", "monthly",
     "derived high/low flag from cpi_yoy vs trailing median; inherits monthly lag"),
    ("fed_funds_level", "policy_rate", "daily",
     "as-of by date (daily series available same day)"),
    ("fed_funds_change_3m", "policy_rate", "daily",
     "derived 3-month change of fed_funds_level; as-of by date"),
    ("fed_policy_tightening_flag", "policy_rate", "daily",
     "derived flag from fed_funds_change_3m > 0; as-of by date"),
    ("treasury_10y", "rates", "daily", "as-of by date (daily series available same day)"),
    ("treasury_2y", "rates", "daily", "as-of by date (daily series available same day)"),
    ("yield_curve_10y_2y", "rates", "daily", "derived 10y minus 2y; as-of by date"),
    ("yield_curve_inversion_flag", "rates", "daily",
     "derived flag from yield_curve_10y_2y < 0; as-of by date"),
    ("real_rate_proxy", "rates", "daily",
     "derived treasury_10y minus cpi_yoy; inherits the slower (monthly cpi) availability lag"),
    ("oil_return_63d", "commodity", "daily",
     "as-of by date; implemented only if local oil price data exists"),
    ("dollar_return_63d", "fx", "daily",
     "as-of by date; implemented only if local dollar index data exists"),
    ("macro_risk_off_flag", "derived_regime", "daily",
     "derived from yield-curve inversion + tightening + inflation regime; as-of by date"),
    ("macro_inflation_shock_flag", "derived_regime", "monthly",
     "derived from inflation_acceleration spike; inherits monthly availability lag"),
    ("macro_rate_shock_flag", "derived_regime", "daily",
     "derived from fed_funds_change_3m / yield spike; as-of by date"),
]
# Optional families that require their own dedicated local source file.
_OPTIONAL_FAMILY_FEATURES = {"oil_return_63d": "commodity", "dollar_return_63d": "fx"}

# Macro-augmented model line-up that WOULD be compared if macro data existed.
_MACRO_MODEL_LINEUP = [
    "ridge_technical_only",          # Phase 3-P baseline carried forward
    "ridge_combined_no_macro",
    "ridge_combined_with_macro",
    "ridge_macro_interactions",
    "regime_gated_macro_model",
]


def _rel(path):
    try:
        return os.path.relpath(path, _REPO_ROOT).replace("\\", "/")
    except ValueError:
        return path.replace("\\", "/")


def _round(v, n=6):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return None
    try:
        return round(float(v), n)
    except (TypeError, ValueError):
        return v


def _load_json(path):
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


# --------------------------------------------------------------------------- #
# 1. Phase 3-Q confirmation
# --------------------------------------------------------------------------- #
def confirm_phase3q(result_json_path=PHASE3Q_RESULT_JSON):
    res = _load_json(result_json_path)
    checks = {
        "phase3q_result_present": res is not None,
        "phase_is_3q": False,
        "recommendation_ok": False,
        "next_phase_is_3r": False,
        "no_production_model_candidate": False,
        "no_deployable_artifact": False,
        "no_production_predictions": False,
        "no_production_scores": False,
        "no_portfolio_weights": False,
        "provider_api_not_called": False,
        "alpha_vantage_not_called": False,
    }
    if res is not None:
        rec = res.get("recommendation")
        if isinstance(rec, dict):
            rec = rec.get("recommendation")
        nxt = (res.get("recommended_next_phase") or {}).get("phase")
        checks["phase_is_3q"] = res.get("phase") == "3-Q"
        checks["recommendation_ok"] = rec in (
            "MODEL_ROBUSTNESS_WEAK_FIXABLE_WITH_REGIME_AND_DATA",
            "MODEL_ROBUSTNESS_PASS_READY_FOR_RISK_SIMULATION",
        )
        checks["next_phase_is_3r"] = nxt == "3-R"
        checks["no_production_model_candidate"] = \
            res.get("production_model_candidate_created") is False
        checks["no_deployable_artifact"] = res.get("deployable_model_artifact_written") is False
        checks["no_production_predictions"] = res.get("production_predictions_computed") is False
        checks["no_production_scores"] = res.get("production_scores_computed") is False
        checks["no_portfolio_weights"] = res.get("portfolio_weights_computed") is False
        checks["provider_api_not_called"] = res.get("provider_api_called") is False
        checks["alpha_vantage_not_called"] = res.get("alpha_vantage_called") is False
    checks["confirmed"] = all(v for k, v in checks.items() if k != "confirmed")
    checks["phase3q_recommendation"] = (
        rec if res is not None else None)
    return checks


def phase3q_baseline(result_json_path=PHASE3Q_RESULT_JSON):
    """Read the Phase 3-P best-model facts carried into Phase 3-Q (baseline for comparison)."""
    res = _load_json(result_json_path) or {}
    bmr = res.get("best_model_reviewed", {}) or {}
    diag = (res.get("bad_year_diagnosis", {}) or {}).get("diagnosis", {}) or {}
    return {
        "model": bmr.get("model", "ridge_technical_only"),
        "horizon": bmr.get("horizon", "126d"),
        "mean_daily_ic": bmr.get("mean_daily_rank_ic"),
        "decile_spread": bmr.get("top_decile_minus_bottom_decile_spread"),
        "ic_hit_rate": bmr.get("rank_ic_hit_rate"),
        "worst_year_ic": bmr.get("worst_year_ic"),
        "stability_score": bmr.get("stability_score"),
        "ic_2021": diag.get("bad_year_mean_daily_ic"),
        "ic_other_years": diag.get("other_years_mean_daily_ic"),
    }


# --------------------------------------------------------------------------- #
# 2. Macro data inventory (local repo scan only - no internet)
# --------------------------------------------------------------------------- #
def _detect_family_from_name(name):
    low = name.lower()
    for family, frags in _MACRO_FAMILIES.items():
        if any(frag in low for frag in frags):
            return family
    return None


def _is_macro_filename(name):
    """A file is a macro candidate only if its name carries the generic ``macro_`` prefix or a
    strong, specific macro token. Returns the detected family or None."""
    low = name.lower()
    if low.startswith("macro_") or "_macro_" in low or low.startswith("fred_"):
        return _detect_family_from_name(low) or "unspecified"
    return _detect_family_from_name(low)


def _csv_header_and_dates(path, max_rows=200000):
    """Read a CSV header and (best-effort) the min/max of the first date-like column.
    Read-only; reads at most a bounded number of rows."""
    cols, date_col = [], None
    dmin = dmax = None
    try:
        with open(path, "r", encoding="utf-8", newline="") as f:
            reader = csv.reader(f)
            header = next(reader, [])
            cols = [c.strip() for c in header]
            for i, c in enumerate(cols):
                if any(k in c.lower() for k in ("date", "observation", "period", "time")):
                    date_col = i
                    break
            if date_col is not None:
                for n, row in enumerate(reader):
                    if n >= max_rows or date_col >= len(row):
                        break
                    v = (row[date_col] or "").strip()
                    if not v:
                        continue
                    if dmin is None or v < dmin:
                        dmin = v
                    if dmax is None or v > dmax:
                        dmax = v
    except OSError:
        pass
    return cols, dmin, dmax


def _file_has_macro_columns(cols):
    joined = " ".join(c.lower() for c in cols)
    return [h for h in _MACRO_COLUMN_HINTS if h in joined]


def build_macro_data_inventory():
    """Scan local repo paths for usable macro data files. Returns (rows, usable_files)."""
    rows = []
    seen = set()
    scanned_any = False
    # Never scan our own Phase 3-R output dir (its panel-sample headers would self-match).
    exclude_dir = os.path.abspath(_R_DIR)
    for root in _SEARCH_ROOTS:
        if not os.path.isdir(root):
            continue
        scanned_any = True
        is_repo_root = os.path.abspath(root) == os.path.abspath(_REPO_ROOT)
        walker = ([(root, [], os.listdir(root))] if is_repo_root else os.walk(root))
        for cur, _dirs, files in walker:
            parts = cur.replace("\\", "/").split("/")
            if ".git" in parts or os.path.abspath(cur).startswith(exclude_dir):
                continue
            for fn in files:
                if not fn.lower().endswith((".csv", ".tsv", ".txt")):
                    continue
                full = os.path.join(cur, fn)
                if not os.path.isfile(full):
                    continue
                # FILENAME-anchored: only files whose name carries a strong macro token qualify.
                fam = _is_macro_filename(fn)
                if fam is None:
                    continue
                cols, dmin, dmax = _csv_header_and_dates(full)
                macro_cols = _file_has_macro_columns(cols)
                rel = _rel(full)
                if rel in seen:
                    continue
                seen.add(rel)
                has_date = dmin is not None
                usable = bool(fam and fam != "unspecified" and macro_cols and has_date)
                blocker = ""
                if not macro_cols:
                    blocker = "filename hints macro but no recognized macro series column"
                elif not has_date:
                    blocker = "no date/observation column to align point-in-time"
                elif fam == "unspecified":
                    blocker = "macro-prefixed file but family not recognized from name"
                rows.append({
                    "file_path": rel,
                    "detected_macro_family": fam or "",
                    "columns": ";".join(cols)[:500],
                    "row_count": "",
                    "date_min": dmin or "",
                    "date_max": dmax or "",
                    "usable": usable,
                    "blocker": blocker,
                })
    usable_files = [r for r in rows if r["usable"]]
    if not rows:
        rows.append({
            "file_path": "(none found)",
            "detected_macro_family": "",
            "columns": "",
            "row_count": 0,
            "date_min": "",
            "date_max": "",
            "usable": False,
            "blocker": ("No local macro/inflation/rates data file found under research/input, "
                        "research/output, data/, or repo top-level CSVs. "
                        "Scanned=%s. See macro_data_requirements." % bool(scanned_any)),
        })
    return rows, usable_files


# --------------------------------------------------------------------------- #
# 3. Macro feature registry + point-in-time assumptions
# --------------------------------------------------------------------------- #
def _family_available(usable_files, family):
    return any(f.get("detected_macro_family") == family for f in usable_files)


# Source-data families each registry feature actually derives from. Derived-regime flags depend on
# the underlying raw families (they have no data file of their own), so "implemented" reflects what
# build_macro_feature_panel can really compute, not a literal "derived_regime" file.
_FEATURE_FAMILY_DEPS = {
    "cpi_yoy": ["inflation"], "cpi_mom": ["inflation"],
    "inflation_acceleration": ["inflation"], "inflation_regime_high_low": ["inflation"],
    "fed_funds_level": ["policy_rate"], "fed_funds_change_3m": ["policy_rate"],
    "fed_policy_tightening_flag": ["policy_rate"],
    "treasury_10y": ["rates"], "treasury_2y": ["rates"],
    "yield_curve_10y_2y": ["rates"], "yield_curve_inversion_flag": ["rates"],
    "real_rate_proxy": ["rates", "inflation"],
    "oil_return_63d": ["commodity"], "dollar_return_63d": ["fx"],
    "macro_risk_off_flag": ["rates", "policy_rate", "inflation"],
    "macro_inflation_shock_flag": ["inflation"],
    "macro_rate_shock_flag": ["policy_rate", "rates"],
}
# Derived flags need ANY one of their input families; everything else needs ALL listed families.
_FEATURE_DEPS_ANY = {"macro_risk_off_flag", "macro_rate_shock_flag"}


def macro_feature_registry(usable_files):
    rows = []
    for feat, family, freq, lag_rule in _PREFERRED_FEATURES:
        deps = _FEATURE_FAMILY_DEPS.get(feat, [family])
        present = [d for d in deps if _family_available(usable_files, d)]
        implemented = bool(present) if feat in _FEATURE_DEPS_ANY else (len(present) == len(deps))
        optional = feat in _OPTIONAL_FAMILY_FEATURES
        if implemented:
            blocker = ""
        else:
            missing = [d for d in deps if d not in present]
            prefix = "optional: " if optional else ""
            blocker = "%sno local %s data file present" % (prefix, "/".join(missing) or family)
        src = next((f["file_path"] for f in usable_files
                    if f.get("detected_macro_family") in present), "")
        rows.append({
            "feature_name": feat,
            "source_file": src,
            "macro_family": family,
            "frequency": freq,
            "availability_lag_rule": lag_rule,
            "point_in_time_safe": True,
            "implemented": implemented,
            "blocker": blocker,
            "notes": ("derived feature - never uses future macro observations for past scoring "
                      "dates" if family == "derived_regime" or "derived" in lag_rule
                      else "raw macro series aligned as-of by availability date"),
        })
    implemented = [r["feature_name"] for r in rows if r["implemented"]]
    blocked = [r["feature_name"] for r in rows if not r["implemented"]]
    return rows, implemented, blocked


def point_in_time_assumptions():
    return {
        "monthly_release_default_lag_calendar_days": DEFAULT_MONTHLY_RELEASE_LAG_DAYS,
        "monthly_release_uses_release_date_column_when_present": True,
        "cpi_not_available_in_month_it_describes": True,
        "treasury_and_fed_rate_daily_series_as_of_by_date": True,
        "derived_features_inherit_slowest_input_lag": True,
        "never_use_future_macro_observations_for_past_scoring_dates": True,
        "as_of_merge_direction": "backward (merge_asof: last macro value available on/before "
                                 "the scoring date after applying the availability lag)",
        "macro_values_forward_filled_between_releases": True,
        "macro_faked": False,
    }


# --------------------------------------------------------------------------- #
# 4. Macro requirements (printed into docs + JSON when blocked)
# --------------------------------------------------------------------------- #
def macro_data_requirements():
    return {
        "summary": ("No local macro/inflation/rates data exists in the repo. To run the "
                    "Phase 3-R walk-forward re-test, place the following free, manually "
                    "downloadable, non-paid local files under research/input/. No API is called "
                    "by this phase; download the CSVs separately and drop them in."),
        "required_files": [
            {"suggested_path": "research/input/macro_cpi_us.csv",
             "macro_family": "inflation",
             "needed_columns": ["date (observation month, YYYY-MM-DD)",
                                 "cpi_index_value or cpi_yoy",
                                 "release_date (optional but preferred for true PIT)"],
             "free_source_examples": ["FRED CPIAUCSL (manual CSV export)",
                                      "BLS CPI-U series CUUR0000SA0 (manual CSV export)"],
             "frequency": "monthly"},
            {"suggested_path": "research/input/macro_fed_funds.csv",
             "macro_family": "policy_rate",
             "needed_columns": ["date (YYYY-MM-DD)", "fed_funds_rate"],
             "free_source_examples": ["FRED DFF / FEDFUNDS (manual CSV export)"],
             "frequency": "daily or monthly"},
            {"suggested_path": "research/input/macro_treasury_yields.csv",
             "macro_family": "rates",
             "needed_columns": ["date (YYYY-MM-DD)", "treasury_10y", "treasury_2y"],
             "free_source_examples": ["FRED DGS10 + DGS2 (manual CSV export)",
                                      "US Treasury daily yield curve CSV"],
             "frequency": "daily"},
            {"suggested_path": "research/input/macro_oil_wti.csv (optional)",
             "macro_family": "commodity",
             "needed_columns": ["date (YYYY-MM-DD)", "wti_price"],
             "free_source_examples": ["FRED DCOILWTICO (manual CSV export)"],
             "frequency": "daily"},
            {"suggested_path": "research/input/macro_dollar_index.csv (optional)",
             "macro_family": "fx",
             "needed_columns": ["date (YYYY-MM-DD)", "broad_dollar_index"],
             "free_source_examples": ["FRED DTWEXBGS (manual CSV export)"],
             "frequency": "daily"},
        ],
        "coverage_requirement": ("Daily/monthly history must span at least the Phase 3-P "
                                 "walk-forward window (roughly 2019-2025) so the as-of merge "
                                 "covers every scoring date."),
        "non_negotiable": ["data must be real (never faked or synthesized)",
                           "must be free / non-paid", "must be obtained without an API key "
                           "(manual download) or with a key the user explicitly provides"],
    }


# --------------------------------------------------------------------------- #
# 5. Macro-augmented walk-forward comparison (ONLY runs if usable macro data exists)
# --------------------------------------------------------------------------- #
def run_macro_walkforward(usable_files, price_csv, phase3l_panel_csv, baseline, verbose=True):
    """Rebuild the Phase 3-P / 3-Q panel in memory, merge macro features point-in-time, and
    re-run the research-only walk-forward model comparison. Reuses the Phase 3-Q OOS helpers
    (which reuse Phase 3-P / 3-O). Returns (scoreboard_rows, yearly_rows, regime_rows,
    bad_year_rows, comparison_summary). This branch is only reached when usable macro data is
    present; with no local macro data the runner takes the BLOCKED path and never calls this."""
    if _HERE not in sys.path:
        sys.path.insert(0, _HERE)
    import pandas as pd  # noqa: E402  (lazy: only when macro data actually exists)
    import run_phase3q_model_robustness_diagnostics as p3q  # noqa: E402
    p3p = p3q.p3p

    panel, info = p3p.build_full_panel(price_csv, phase3l_panel_csv,
                                       p3p.PHASE3M_EARNINGS_CSV, verbose)
    macro_panel, macro_cols = build_macro_feature_panel(usable_files, panel, pd)
    panel = panel.merge(macro_panel, on="scoring_date", how="left")
    interaction_cols = build_macro_interactions(panel, pd)
    info = dict(info or {})
    info["macro_features_merged"] = macro_cols
    info["macro_interaction_features"] = interaction_cols
    info["macro_panel_scoring_date_min"] = str(macro_panel["scoring_date"].min())[:10]
    info["macro_panel_scoring_date_max"] = str(macro_panel["scoring_date"].max())[:10]

    feature_sets = {
        "ridge_technical_only": list(p3p.FEATURE_SETS["technical_only"]),
        "ridge_combined_no_macro": list(p3p.COMBINED_WITH_EARNINGS),
        "ridge_combined_with_macro": list(p3p.COMBINED_WITH_EARNINGS) + macro_cols,
        "ridge_macro_interactions": list(p3p.FEATURE_SETS["technical_only"]) + macro_cols
        + interaction_cols,
    }
    horizon = p3q.FOCUS_HORIZON
    breadth_med, disp_med = _panel_regime_medians(panel, pd)

    scoreboard_rows, yearly_rows, regime_rows, bad_year_rows = [], [], [], []
    preds_by_model = {}
    for name, feats in feature_sets.items():
        preds, _fw, _fu = p3q.oos_ridge_predictions(panel, feats, horizon)
        preds_by_model[name] = preds
        scoreboard_rows.append(_macro_scoreboard_row(name, preds, horizon, p3q))
    # Regime-gated macro model: route by macro_risk_off_flag if present, else market regime.
    if "regime_gated_macro_model" in _MACRO_MODEL_LINEUP:
        gated = p3q.oos_regime_ensemble_predictions(
            panel, feature_sets["ridge_combined_with_macro"], horizon)
        preds_by_model["regime_gated_macro_model"] = gated
        scoreboard_rows.append(_macro_scoreboard_row("regime_gated_macro_model", gated,
                                                     horizon, p3q))

    for name, preds in preds_by_model.items():
        for y in sorted(int(v) for v in preds["year"].unique()):
            ysub = preds[preds["year"] == y]
            yearly_rows.append({"model": name, "year": y,
                                "mean_daily_ic": _round(p3q._mean_ic(ysub), 6),
                                "test_rows": int(len(ysub))})
        rrows, _rs = p3q.regime_performance(preds, breadth_med, disp_med)
        for rr in rrows:
            rr2 = {"model": name}
            rr2.update(rr)
            regime_rows.append(rr2)

    comparison_summary = _macro_comparison_summary(scoreboard_rows, preds_by_model, baseline, p3q)
    bad_year_rows = _macro_bad_year_rows(scoreboard_rows, preds_by_model, baseline, p3q)
    sample_rows = _macro_panel_sample(macro_panel, macro_cols)
    return (scoreboard_rows, yearly_rows, regime_rows, bad_year_rows, comparison_summary,
            sample_rows, info)


def _load_canonical_macro_series(usable_files, pd, np):
    """Read each usable macro file, map its FRED column(s) onto the canonical macro namespace,
    and attach a conservative point-in-time availability date. Real values only - missing values
    (FRED '.' or blank) become NaN and are never fabricated. Returns
    canonical_name -> {"df": DataFrame[_obs,_avail,<canon>], "monthly": bool}."""
    series = {}
    for f in usable_files:
        path = os.path.join(_REPO_ROOT, f["file_path"])
        try:
            raw = pd.read_csv(path)
        except (OSError, ValueError):
            continue
        date_col = next((c for c in raw.columns
                         if any(k in c.lower() for k in ("date", "observation", "period"))), None)
        if date_col is None:
            continue
        obs = pd.to_datetime(raw[date_col], errors="coerce")
        rel_col = next((c for c in raw.columns if "release" in c.lower()), None)
        gaps = obs.dropna().sort_values().diff().dt.days.dropna()
        is_monthly = bool(len(gaps)) and float(gaps.median()) >= 20.0
        if rel_col is not None:                       # true release-date PIT when provided
            avail = pd.to_datetime(raw[rel_col], errors="coerce")
        elif is_monthly:                              # conservative default availability lag
            avail = obs + pd.Timedelta(days=DEFAULT_MONTHLY_RELEASE_LAG_DAYS)
        else:                                         # daily series: as-of by observation date
            avail = obs
        for c in raw.columns:
            canon = _FRED_COLUMN_MAP.get(str(c).strip().lower())
            if canon is None:
                continue
            vals = pd.to_numeric(
                raw[c].astype(str).str.strip().replace({".": None, "": None}), errors="coerce")
            sdf = (pd.DataFrame({"_obs": obs, "_avail": avail, canon: vals})
                   .dropna(subset=[canon, "_avail"]).sort_values("_obs").reset_index(drop=True))
            if len(sdf):
                series[canon] = {"df": sdf, "monthly": is_monthly}
    return series


def build_macro_feature_panel(usable_files, panel, pd):
    """Map the local FRED macro files into the Phase 3-R macro feature layer, derive every
    registry feature, and as-of (point-in-time) merge them onto the panel's daily scoring dates.
    Real data only - this function never fabricates a value; absent families are simply omitted
    and absent rows stay NaN (the ridge prep median-imputes per training fold)."""
    import numpy as np  # noqa: E402  (lazy: only when macro data actually exists)
    series = _load_canonical_macro_series(usable_files, pd, np)

    dates = pd.to_datetime(pd.Series(sorted(panel["scoring_date"].unique()), name="scoring_date"))
    out = pd.DataFrame({"scoring_date": dates}).sort_values("scoring_date").reset_index(drop=True)

    base_frames = []  # each: DataFrame[_avail, <cols...>] merged as-of (backward) onto scoring dates

    # --- Inflation (CPI, monthly, computed in observation order then lagged to availability) ---
    if "cpi_index_value" in series:
        d = series["cpi_index_value"]["df"].copy()
        cpi = d["cpi_index_value"]
        d["cpi_yoy"] = cpi / cpi.shift(12) - 1.0
        d["cpi_mom"] = cpi / cpi.shift(1) - 1.0
        d["inflation_acceleration"] = d["cpi_yoy"] - d["cpi_yoy"].shift(1)
        # High vs low inflation regime = current YoY above its trailing ~5y median (PIT-safe).
        d["inflation_regime_high_low"] = (
            d["cpi_yoy"] > d["cpi_yoy"].rolling(60, min_periods=24).median()).astype(float)
        d["macro_inflation_shock_flag"] = (
            (d["cpi_yoy"] - d["cpi_yoy"].shift(3)) > 0.01).astype(float)
        base_frames.append(d[["_avail", "cpi_yoy", "cpi_mom", "inflation_acceleration",
                              "inflation_regime_high_low", "macro_inflation_shock_flag"]])

    # --- Policy rate (fed funds, monthly) ---
    if "fed_funds_level" in series:
        d = series["fed_funds_level"]["df"].copy()
        lvl = d["fed_funds_level"]
        d["fed_funds_change_3m"] = lvl - lvl.shift(3)
        d["fed_policy_tightening_flag"] = (d["fed_funds_change_3m"] > 0).astype(float)
        d["_ff_rate_shock"] = (d["fed_funds_change_3m"] > 0.5).astype(float)
        base_frames.append(d[["_avail", "fed_funds_level", "fed_funds_change_3m",
                              "fed_policy_tightening_flag", "_ff_rate_shock"]])

    # --- Daily raw series (rates / commodity / fx) merged as-of by date ---
    for canon in ("treasury_10y", "treasury_2y", "wti_price", "broad_dollar_index"):
        if canon in series:
            base_frames.append(series[canon]["df"][["_avail", canon]].copy())

    out_sorted = out
    for fr in base_frames:
        fr2 = (fr.dropna(subset=["_avail"]).sort_values("_avail")
               .drop_duplicates(subset=["_avail"], keep="last")
               .rename(columns={"_avail": "scoring_date"}))
        out_sorted = pd.merge_asof(out_sorted, fr2, on="scoring_date", direction="backward")
    out = out_sorted.reset_index(drop=True)

    # --- Derived cross-series features (point-in-time safe: only merged-as-of inputs) ---
    if "treasury_10y" in out.columns and "treasury_2y" in out.columns:
        out["yield_curve_10y_2y"] = out["treasury_10y"] - out["treasury_2y"]
        out["yield_curve_inversion_flag"] = (out["yield_curve_10y_2y"] < 0).astype(float)
    if "treasury_10y" in out.columns and "cpi_yoy" in out.columns:
        # treasury is in percent; cpi_yoy is a fraction -> express both in percent.
        out["real_rate_proxy"] = out["treasury_10y"] - out["cpi_yoy"] * 100.0
    if "wti_price" in out.columns:
        out["oil_return_63d"] = out["wti_price"].pct_change(63)
    if "broad_dollar_index" in out.columns:
        out["dollar_return_63d"] = out["broad_dollar_index"].pct_change(63)

    # macro_rate_shock_flag: fed-funds 3m jump OR a >50bp 10y move over ~63 trading days.
    ff_shock = (out["_ff_rate_shock"].fillna(0.0) > 0
                if "_ff_rate_shock" in out.columns else pd.Series(False, index=out.index))
    if "treasury_10y" in out.columns:
        ts_shock = (out["treasury_10y"] - out["treasury_10y"].shift(63)) > 0.5
        ts_shock = ts_shock.fillna(False)
    else:
        ts_shock = pd.Series(False, index=out.index)
    out["macro_rate_shock_flag"] = (ff_shock | ts_shock).astype(float)

    # macro_risk_off_flag: curve inversion OR (tightening AND high-inflation regime).
    inv = (out["yield_curve_inversion_flag"].fillna(0.0) > 0
           if "yield_curve_inversion_flag" in out.columns else pd.Series(False, index=out.index))
    tight = (out["fed_policy_tightening_flag"].fillna(0.0) > 0
             if "fed_policy_tightening_flag" in out.columns else pd.Series(False, index=out.index))
    infl_hi = (out["inflation_regime_high_low"].fillna(0.0) > 0
               if "inflation_regime_high_low" in out.columns else pd.Series(False, index=out.index))
    out["macro_risk_off_flag"] = (inv | (tight & infl_hi)).astype(float)

    registry_feats = [f[0] for f in _PREFERRED_FEATURES]
    macro_cols = [c for c in registry_feats if c in out.columns]
    out = out[["scoring_date"] + macro_cols].copy()
    return out, macro_cols


# Interaction features (Phase 3-R spec): momentum/volatility/sector-relative x macro regime flags.
_MACRO_INTERACTION_SPEC = [
    ("momentum_x_inflation_shock", "return_63d", "macro_inflation_shock_flag"),
    ("momentum_x_rate_shock", "return_63d", "macro_rate_shock_flag"),
    ("volatility_x_yield_curve_inversion", "volatility_21d", "yield_curve_inversion_flag"),
    ("sector_relative_x_macro_risk_off",
     "ticker_return_21d_minus_sector_return_21d", "macro_risk_off_flag"),
]


def build_macro_interactions(panel, pd):
    """Build the macro x signal interaction columns ON the merged panel (per row). Returns the
    list of interaction column names actually created (only when both inputs are present)."""
    cols = []
    for name, base, flag in _MACRO_INTERACTION_SPEC:
        if base in panel.columns and flag in panel.columns:
            panel[name] = (pd.to_numeric(panel[base], errors="coerce")
                           * pd.to_numeric(panel[flag], errors="coerce"))
            cols.append(name)
    return cols


def _panel_regime_medians(panel, pd):
    reg = panel.drop_duplicates("scoring_date")
    b = float(pd.to_numeric(reg.get("cross_sectional_breadth_21d"), errors="coerce").median())
    d = float(pd.to_numeric(reg.get("cross_sectional_dispersion_21d"), errors="coerce").median())
    return b, d


def _macro_scoreboard_row(name, preds, horizon, p3q):
    m = p3q._metrics(preds, name, horizon) or {}
    return {
        "model": name,
        "horizon": "%dd" % horizon,
        "mean_daily_ic": m.get("mean_daily_rank_ic"),
        "decile_spread": m.get("top_decile_minus_bottom_decile_spread"),
        "ic_hit_rate": m.get("rank_ic_hit_rate"),
        "worst_year_ic": m.get("worst_year_ic"),
        "stability_score": m.get("stability_score"),
        "fold_count": m.get("fold_count"),
        "note": "",
    }


def _ic_2021(preds, p3q):
    sub = preds[preds["year"] == BAD_YEAR]
    return p3q._mean_ic(sub) if len(sub) else None


def _macro_bad_year_rows(scoreboard_rows, preds_by_model, baseline, p3q):
    rows = []
    base_2021 = baseline.get("ic_2021")
    for sb in scoreboard_rows:
        preds = preds_by_model.get(sb["model"])
        ic21 = _ic_2021(preds, p3q) if preds is not None else None
        delta = (ic21 - base_2021) if (ic21 is not None and base_2021 is not None) else None
        rows.append({
            "model": sb["model"],
            "mean_daily_ic": sb["mean_daily_ic"],
            "ic_2021": _round(ic21, 6),
            "worst_year_ic": sb["worst_year_ic"],
            "stability_score": sb["stability_score"],
            "decile_spread": sb["decile_spread"],
            "delta_ic_2021_vs_baseline": _round(delta, 6),
            "improves_bad_year": bool(delta is not None and delta > 0),
            "note": "",
        })
    return rows


def _macro_comparison_summary(scoreboard_rows, preds_by_model, baseline, p3q):
    base_worst = baseline.get("worst_year_ic")
    base_mean = baseline.get("mean_daily_ic")
    base_2021 = baseline.get("ic_2021")
    macro_models = [r for r in scoreboard_rows if r["model"] != "ridge_technical_only"]
    best_worst = max((r for r in macro_models if r["worst_year_ic"] is not None),
                     key=lambda r: r["worst_year_ic"], default=None)
    best_2021 = None
    best_2021_val = None
    for r in macro_models:
        ic21 = _ic_2021(preds_by_model.get(r["model"]), p3q)
        if ic21 is not None and (best_2021_val is None or ic21 > best_2021_val):
            best_2021_val, best_2021 = ic21, r["model"]
    worst_improves = bool(best_worst and base_worst is not None
                          and best_worst["worst_year_ic"] > base_worst)
    y2021_improves = bool(best_2021_val is not None and base_2021 is not None
                          and best_2021_val > base_2021)
    mean_ok = True
    if best_worst and base_mean:
        mean_ok = (best_worst["mean_daily_ic"] is not None
                   and best_worst["mean_daily_ic"] >= base_mean * (1 - MEAN_IC_DROP_TOLERANCE))
    spread_pos = bool(best_worst and (best_worst["decile_spread"] or 0) > 0)
    return {
        "baseline_worst_year_ic": base_worst,
        "baseline_mean_daily_ic": base_mean,
        "baseline_ic_2021": base_2021,
        "best_macro_model_by_worst_year": None if best_worst is None else {
            "model": best_worst["model"], "worst_year_ic": best_worst["worst_year_ic"],
            "mean_daily_ic": best_worst["mean_daily_ic"]},
        "best_macro_model_by_2021": None if best_2021 is None else {
            "model": best_2021, "ic_2021": _round(best_2021_val, 6)},
        "macro_improves_worst_year": worst_improves,
        "macro_improves_2021": y2021_improves,
        "macro_mean_ic_within_tolerance": mean_ok,
        "macro_decile_spread_positive": spread_pos,
    }


def _macro_panel_sample(macro_panel, cols, max_rows=400):
    keep = ["scoring_date"] + [c for c in cols if c in macro_panel.columns]
    sub = macro_panel[keep].dropna(how="all", subset=[c for c in keep if c != "scoring_date"])
    sub = sub.tail(max_rows)
    rows = []
    for _i, r in sub.iterrows():
        d = {"scoring_date": str(r["scoring_date"])[:10]}
        for c in keep:
            if c == "scoring_date":
                continue
            d[c] = _round(r[c], 6)
        rows.append(d)
    return rows


# --------------------------------------------------------------------------- #
# 6. Decision
# --------------------------------------------------------------------------- #
def decide(inputs_ok, usable_files, comparison_summary):
    if not inputs_ok:
        return REC_BLOCKED_INPUTS
    if not usable_files:
        return REC_BLOCKED_DATA
    cs = comparison_summary or {}
    if (cs.get("macro_improves_worst_year") and cs.get("macro_improves_2021")
            and cs.get("macro_mean_ic_within_tolerance")
            and cs.get("macro_decile_spread_positive")):
        return REC_IMPROVES
    return REC_WEAK


def build_recommendation(rec):
    reasons = {
        REC_IMPROVES: (
            "Macro/regime features IMPROVE robustness: a macro-augmented model improves both the "
            "worst-year and the 2021 IC versus the Phase 3-P baseline, mean IC does not fall by "
            "more than 25%, and the decile spread stays positive. Still research-only: no "
            "production model candidate, predictions, scores, weights, or artifact; no edge "
            "claimed."),
        REC_WEAK: (
            "Macro data exists and the walk-forward re-test ran, but the macro/regime features do "
            "NOT improve bad-year robustness (worst-year / 2021 IC not improved, or mean IC fell "
            "too far, or the spread turned non-positive). Research-only; no production artifact or "
            "edge claim."),
        REC_BLOCKED_DATA: (
            "Blocked: no usable local macro/inflation/rates data file exists in the repo, so the "
            "macro feature layer cannot be built without fabricating data (which is forbidden). "
            "No model was trained and no macro data was faked. Acquire the local macro files "
            "listed in macro_data_requirements, then re-run. No provider / Alpha Vantage / FRED "
            "API was called."),
        REC_BLOCKED_INPUTS: (
            "Blocked: the Phase 3-Q result or a required local panel was missing or did not "
            "confirm, so the macro walk-forward re-test could not run. Repair the Phase 3-R "
            "inputs. No provider API was called."),
    }
    return {
        "recommendation": rec,
        "allowed_values": ALLOWED_RECOMMENDATIONS,
        "research_only": True,
        "create_production_model_candidate_now": False,
        "train_production_model_now": False,
        "compute_portfolio_weights_now": False,
        "deploy_now": False,
        "production_edge_claimed": False,
        "macro_faked": False,
        "reason": reasons[rec],
    }


def build_recommended_next_phase(rec):
    table = {
        REC_IMPROVES: ("Sentiment and Earnings Coverage Expansion",
                       "With the macro/regime layer improving robustness, add sentiment and "
                       "expand earnings coverage, then re-test. No production candidate."),
        REC_WEAK: ("Sentiment and Earnings Coverage Expansion",
                   "Macro features did not fix the bad year; pivot to adding sentiment and "
                   "finishing earnings coverage as the next orthogonal data family. No "
                   "production candidate."),
        REC_BLOCKED_DATA: ("Acquire Local Macro Data Files",
                           "Obtain the free, non-paid local macro/inflation/rates CSV files "
                           "listed in the Phase 3-R macro data requirements, then re-run the "
                           "macro walk-forward re-test. No production candidate."),
        REC_BLOCKED_INPUTS: ("Repair Phase 3-R Inputs",
                             "Restore the Phase 3-Q result and the local price / Phase 3-L "
                             "panels before re-running the macro walk-forward re-test."),
    }
    title, purpose = table[rec]
    return {"phase": "3-S", "title": title, "purpose": purpose}


# --------------------------------------------------------------------------- #
# Safety flags / interpretation
# --------------------------------------------------------------------------- #
def build_safety_flags(d_drive_read):
    return {
        "database_touched": False,
        "database_write_executed": False,
        "migration_executed": False,
        "deployment_executed": False,
        "model_v2_enabled": False,
        "production_edge_claimed": False,
        "no_trading": True,
        "no_orders": True,
        "no_automation": True,
        "research_only": True,
        "production_model_trained": False,
        "production_model_candidate_created": False,
        "deployable_model_artifact_written": False,
        "production_predictions_computed": False,
        "production_scores_computed": False,
        "portfolio_weights_computed": False,
        "order_instructions_created": False,
        "d_drive_read": bool(d_drive_read),
        "d_drive_written": False,
        "provider_api_called": False,
        "alpha_vantage_called": False,
        "paid_vendor_api_called": False,
        "macro_faked": False,
        "sentiment_faked": False,
        "labels_for_validation_only": True,
    }


def build_interpretation(rec, usable_files, comparison_summary):
    return {
        "phase_is_research_only": True,
        "research_only": True,
        "rebuilt_oos_scores_in_memory": bool(usable_files),
        "full_prediction_panel_written_to_disk": False,
        "production_predictions_computed": False,
        "production_scores_computed": False,
        "portfolio_weights_computed": False,
        "deployable_model_artifact_written": False,
        "production_edge_claimed": False,
        "macro_family_faked": False,
        "sentiment_family_faked": False,
        "usable_local_macro_data_found": bool(usable_files),
        "results_remain_survivorship_biased": True,
        "narrative": (
            "Phase 3-R attempts to fix the Phase 3-Q bad-year fragility by adding a local, free, "
            "non-faked macro/inflation/rates regime feature family, merging it point-in-time "
            "(as-of, with conservative availability lags) into a Phase 3-P panel rebuilt in "
            "memory, and re-running the research-only walk-forward model comparison. It fakes no "
            "macro or sentiment data: when no usable local macro file exists it STOPS with a "
            "blocked recommendation and writes the exact data requirements instead of inventing "
            "values. It trains no production model, computes no production predictions / scores / "
            "portfolio weights / order instructions, writes no deployable model artifact, writes "
            "nothing to the D: drive, runs no migration, restarts no prediction service, enables "
            "no model-v2 serving flag, executes no trade, and calls no provider / paid-vendor / "
            "Alpha Vantage / FRED API. The universe is current-as-of, so all results remain "
            "survivorship-biased and claim no production edge."),
    }


# --------------------------------------------------------------------------- #
# Writers
# --------------------------------------------------------------------------- #
def _write_csv(path, columns, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            out = {}
            for c in columns:
                v = r.get(c, "")
                out[c] = "" if v is None or (isinstance(v, float) and math.isnan(v)) else v
            w.writerow(out)


def _json_default(o):
    """Serialize values that survive a pandas/numpy round trip (Timestamps, numpy scalars)."""
    if hasattr(o, "isoformat"):
        return o.isoformat()
    if hasattr(o, "item"):
        try:
            return o.item()
        except (ValueError, TypeError):
            pass
    return str(o)


def _dump_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, default=_json_default)


_OUT_PATHS = {
    "inventory": "macro_data_inventory.csv",
    "registry": "macro_feature_registry.csv",
    "panel_sample": "macro_feature_panel_sample.csv",
    "scoreboard": "macro_walkforward_scoreboard.csv",
    "yearly": "macro_yearly_stability.csv",
    "regime": "macro_regime_performance.csv",
    "bad_year": "macro_bad_year_comparison.csv",
    "decision": "macro_improvement_decision_table.csv",
}

_INVENTORY_COLS = ["file_path", "detected_macro_family", "columns", "row_count", "date_min",
                   "date_max", "usable", "blocker"]
_REGISTRY_COLS = ["feature_name", "source_file", "macro_family", "frequency",
                  "availability_lag_rule", "point_in_time_safe", "implemented", "blocker", "notes"]
_PANEL_SAMPLE_COLS = ["scoring_date"] + [f[0] for f in _PREFERRED_FEATURES]
_SCOREBOARD_COLS = ["model", "horizon", "mean_daily_ic", "decile_spread", "ic_hit_rate",
                    "worst_year_ic", "stability_score", "fold_count", "note"]
_YEARLY_COLS = ["model", "year", "mean_daily_ic", "test_rows"]
_REGIME_COLS = ["model", "regime", "test_rows", "ic_days", "mean_daily_ic", "median_daily_ic",
                "ic_hit_rate", "decile_spread", "quintile_spread"]
_BAD_YEAR_COLS = ["model", "mean_daily_ic", "ic_2021", "worst_year_ic", "stability_score",
                  "decile_spread", "delta_ic_2021_vs_baseline", "improves_bad_year", "note"]
_DECISION_COLS = ["decision_item", "value", "passed", "note"]


def _out(o_dir, key):
    return os.path.join(o_dir, _OUT_PATHS[key])


def build_decision_table(p3q_checks, usable_files, comparison_summary, rec, baseline):
    rows = []

    def add(item, value, passed, note):
        rows.append({"decision_item": item, "value": value,
                     "passed": "" if passed is None else bool(passed), "note": note})

    add("phase3q_confirmed", p3q_checks.get("confirmed"), p3q_checks.get("confirmed"),
        "Phase 3-Q present, weak-fixable/pass, next phase 3-R, no production artifacts")
    add("usable_local_macro_data_found", len(usable_files), len(usable_files) > 0,
        "scanned local repo paths only; macro data must be real and non-faked")
    cs = comparison_summary or {}
    add("macro_improves_worst_year", cs.get("macro_improves_worst_year"),
        cs.get("macro_improves_worst_year"),
        "IMPROVES needs a macro model worst-year IC above the Phase 3-P baseline")
    add("macro_improves_2021", cs.get("macro_improves_2021"), cs.get("macro_improves_2021"),
        "IMPROVES needs the 2021 IC to beat the baseline %s" % baseline.get("ic_2021"))
    add("macro_mean_ic_within_25pct", cs.get("macro_mean_ic_within_tolerance"),
        cs.get("macro_mean_ic_within_tolerance"), "mean IC must not fall by more than 25%")
    add("macro_decile_spread_positive", cs.get("macro_decile_spread_positive"),
        cs.get("macro_decile_spread_positive"), "decile spread must remain positive")
    add("no_macro_data_faked", True, True, "blocked instead of fabricating macro values")
    add("no_production_model_trained", True, True, "research-only walk-forward in memory")
    add("no_production_predictions", True, True, "no production predictions/scores computed")
    add("no_portfolio_weights", True, True, "no portfolio weights computed")
    add("no_deployable_artifact", True, True, "no serialized-estimator file written")
    add("d_drive_written", False, True, "wrote nothing to the D: drive")
    add("provider_or_alpha_vantage_called", False, True,
        "no provider / Alpha Vantage / FRED API call")
    add("recommendation", rec, rec in ALLOWED_RECOMMENDATIONS, "allowed values only")
    return rows


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
def _read_d_drive_header(price_csv):
    """Read-only touch of the allowed D: price spine (header line only) to confirm the baseline
    data exists. Returns True if the D: file was opened for reading."""
    if not os.path.isfile(price_csv):
        return False
    try:
        with open(price_csv, "r", encoding="utf-8", newline="") as f:
            f.readline()
        return True
    except OSError:
        return False


def _blocked_outputs(o_dir, registry_rows, baseline, rec, note_for_macro):
    """Write all required CSVs for a blocked run (headers + the honest baseline carry-forward)."""
    _write_csv(_out(o_dir, "registry"), _REGISTRY_COLS, registry_rows)
    _write_csv(_out(o_dir, "panel_sample"), _PANEL_SAMPLE_COLS, [])
    _write_csv(_out(o_dir, "scoreboard"), _SCOREBOARD_COLS, [])
    _write_csv(_out(o_dir, "yearly"), _YEARLY_COLS, [])
    _write_csv(_out(o_dir, "regime"), _REGIME_COLS, [])
    # Bad-year comparison: honest Phase 3-P baseline carry-forward + a blocked macro placeholder
    # (no fabricated macro numbers).
    bad_year_rows = [
        {"model": "%s_%s_baseline_phase3p" % (baseline.get("model"), baseline.get("horizon")),
         "mean_daily_ic": baseline.get("mean_daily_ic"), "ic_2021": baseline.get("ic_2021"),
         "worst_year_ic": baseline.get("worst_year_ic"),
         "stability_score": baseline.get("stability_score"),
         "decile_spread": baseline.get("decile_spread"),
         "delta_ic_2021_vs_baseline": "", "improves_bad_year": "",
         "note": "Phase 3-P baseline carried forward from Phase 3-Q (no macro re-test run)."},
        {"model": "ridge_combined_with_macro", "mean_daily_ic": "", "ic_2021": "",
         "worst_year_ic": "", "stability_score": "", "decile_spread": "",
         "delta_ic_2021_vs_baseline": "", "improves_bad_year": "", "note": note_for_macro},
    ]
    _write_csv(_out(o_dir, "bad_year"), _BAD_YEAR_COLS, bad_year_rows)
    return bad_year_rows


def run(result_json_path=RESULT_JSON, o_dir=_R_DIR, price_csv=PRICE_CSV,
        phase3l_panel_csv=PHASE3L_PANEL_CSV, phase3q_result_json=PHASE3Q_RESULT_JSON,
        verbose=True):
    p3q_checks = confirm_phase3q(phase3q_result_json)
    baseline = phase3q_baseline(phase3q_result_json)
    d_drive_read = _read_d_drive_header(price_csv)

    if verbose:
        print("  scanning local repo for macro/inflation/rates data ...")
    inventory_rows, usable_files = build_macro_data_inventory()
    _write_csv(_out(o_dir, "inventory"), _INVENTORY_COLS, inventory_rows)

    registry_rows, implemented, blocked_feats = macro_feature_registry(usable_files)
    pit = point_in_time_assumptions()
    requirements = macro_data_requirements()

    inputs_ok = p3q_checks["confirmed"]
    panel_ok = os.path.isfile(price_csv) and os.path.isfile(phase3l_panel_csv)

    comparison_summary = {}
    scoreboard_rows = yearly_rows = regime_rows = bad_year_rows = sample_rows = []
    oos_info = {}

    if not inputs_ok:
        rec = REC_BLOCKED_INPUTS
        note = "Phase 3-Q not confirmed - repair Phase 3-R inputs."
        bad_year_rows = _blocked_outputs(o_dir, registry_rows, baseline, rec, note)
    elif not usable_files or not panel_ok:
        rec = REC_BLOCKED_DATA if not usable_files else REC_BLOCKED_INPUTS
        note = (requirements["summary"] if not usable_files
                else "Required local price / Phase 3-L panel missing.")
        bad_year_rows = _blocked_outputs(o_dir, registry_rows, baseline, rec, note)
    else:
        if verbose:
            print("  usable local macro data found - running macro walk-forward re-test ...")
        (scoreboard_rows, yearly_rows, regime_rows, bad_year_rows, comparison_summary,
         sample_rows, oos_info) = run_macro_walkforward(
            usable_files, price_csv, phase3l_panel_csv, baseline, verbose)
        rec = decide(True, usable_files, comparison_summary)
        _write_csv(_out(o_dir, "registry"), _REGISTRY_COLS, registry_rows)
        _write_csv(_out(o_dir, "panel_sample"), _PANEL_SAMPLE_COLS, sample_rows)
        _write_csv(_out(o_dir, "scoreboard"), _SCOREBOARD_COLS, scoreboard_rows)
        _write_csv(_out(o_dir, "yearly"), _YEARLY_COLS, yearly_rows)
        _write_csv(_out(o_dir, "regime"), _REGIME_COLS, regime_rows)
        _write_csv(_out(o_dir, "bad_year"), _BAD_YEAR_COLS, bad_year_rows)

    decision_rows = build_decision_table(p3q_checks, usable_files, comparison_summary, rec,
                                         baseline)
    _write_csv(_out(o_dir, "decision"), _DECISION_COLS, decision_rows)

    result = {
        "phase": PHASE,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "inputs_read": {
            "phase3q_result_json": _rel(phase3q_result_json),
            "phase3p_result_json": _rel(PHASE3P_RESULT_JSON),
            "phase3o_result_json": _rel(PHASE3O_RESULT_JSON),
            "price_history_csv": price_csv.replace("\\", "/"),
            "phase3l_aligned_panel_csv": _rel(phase3l_panel_csv),
        },
        "outputs_written": {
            "result_json": _rel(result_json_path),
            **{k: _rel(_out(o_dir, k)) for k in _OUT_PATHS},
        },
        "phase3q_confirmation": p3q_checks,
        "baseline_phase3p_best_model": baseline,
        "macro_data_inventory_summary": {
            "search_roots": [_rel(r) for r in _SEARCH_ROOTS],
            "candidate_files": len([r for r in inventory_rows
                                    if r.get("file_path") not in ("", "(none found)")]),
            "usable_macro_files": len(usable_files),
            "usable_files": [f["file_path"] for f in usable_files],
            "no_local_macro_data": len(usable_files) == 0,
        },
        "macro_features_implemented": implemented,
        "macro_features_blocked": blocked_feats,
        "macro_feature_registry": registry_rows,
        "point_in_time_assumptions": pit,
        "macro_data_requirements": requirements,
        "model_comparison_summary": comparison_summary,
        "bad_year_comparison": bad_year_rows,
        "oos_rebuild_summary": oos_info,
        "recommendation": build_recommendation(rec),
        "recommended_next_phase": build_recommended_next_phase(rec),
        "interpretation": build_interpretation(rec, usable_files, comparison_summary),
    }
    result.update(build_safety_flags(d_drive_read))
    _dump_json(result_json_path, result)
    return result


def main():
    result = run()
    rec = result["recommendation"]
    nxt = result["recommended_next_phase"]
    inv = result["macro_data_inventory_summary"]
    print("Phase %s - Macro/Inflation Regime Feature Layer + Walk-Forward Re-Test"
          % result["phase"])
    print("  phase 3-Q confirmed   : %s" % result["phase3q_confirmation"]["confirmed"])
    print("  candidate macro files : %s" % inv["candidate_files"])
    print("  usable macro files    : %s" % inv["usable_macro_files"])
    print("  macro features impl.  : %s" % len(result["macro_features_implemented"]))
    print("  macro features blocked: %s" % len(result["macro_features_blocked"]))
    cs = result["model_comparison_summary"]
    if cs:
        print("  macro improves worst/2021: %s / %s"
              % (cs.get("macro_improves_worst_year"), cs.get("macro_improves_2021")))
    print("  recommendation        : %s" % rec["recommendation"])
    print("  recommended next      : %s - %s" % (nxt["phase"], nxt["title"]))
    return result


if __name__ == "__main__":
    main()
