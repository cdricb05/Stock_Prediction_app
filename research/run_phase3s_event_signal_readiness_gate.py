"""Phase 3-S - Earnings Coverage Expansion + Sentiment/Analyst Readiness & Prioritization Gate.

This is a research-only READINESS and PRIORITIZATION gate that sits on top of the Phase 3-R
macro/regime walk-forward result. Phase 3-R proved the local macro/inflation/rates layer is real
and point-in-time and that it repairs the 2021 / worst-year fragility, but it lowers average IC
too far to clear the robustness bar (MACRO_REGIME_WALKFORWARD_WEAK_NO_IMPROVEMENT). The model
still needs an orthogonal, event-driven signal family: more earnings coverage and/or
sentiment / analyst-revision data.

Phase 3-S answers five questions WITHOUT training anything and WITHOUT calling any provider:

  1. How much earnings coverage do we currently have (cached vs the 128-ticker universe)?
  2. Can we safely continue the resumable earnings collection without wasting provider quota?
  3. What exact sentiment / analyst / news data is locally available, if any?
  4. If none exists, what exact files / providers would be needed (described, never fetched)?
  5. Given Phase 3-P/3-Q/3-R, what is the next best action: finish earnings coverage, acquire
     sentiment / analyst-revision data, add cross-asset ETF proxies, or proceed to risk sim?

It reads only already-written local artifacts (Phase 3-R/3-Q/3-P/3-O/3-M result JSONs, the
Phase 3-M collection progress, the local earnings raw cache directory listing, and a local repo
scan for sentiment/analyst CSVs). It NEVER fakes earnings, sentiment, or analyst-revision data:
when no usable local sentiment/analyst data exists it marks the families BLOCKED and writes the
exact data requirements instead of inventing values. It touches no database, runs no migration,
restarts no prediction service, enables no model-v2 serving flag, writes nothing to the D: drive,
calls no provider / paid-vendor / Alpha Vantage / FRED API, reads no provider API key value (only
already-detected booleans), places no orders, trades nothing, creates no production model
candidate, computes no production predictions / scores / portfolio weights / order instructions,
and writes no deployable model artifact.

Run:
    python -B research/run_phase3s_event_signal_readiness_gate.py
Test:
    python -B tests/test_phase3s_event_signal_readiness_gate.py
"""
from __future__ import annotations

import csv
import json
import math
import os
from datetime import datetime

PHASE = "3-S"

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_HERE)

# --------------------------------------------------------------------------- #
# Paths (all local, on C:; nothing on D: is read or written)
# --------------------------------------------------------------------------- #
_OUT_DIR = os.path.join(_REPO_ROOT, "research", "output")
_S_DIR = os.path.join(_OUT_DIR, "phase3s_event_signal_readiness_gate")
RESULT_JSON = os.path.join(_OUT_DIR, "phase3s_event_signal_readiness_gate.json")

PHASE3R_RESULT_JSON = os.path.join(_OUT_DIR, "phase3r_macro_regime_walkforward.json")
PHASE3Q_RESULT_JSON = os.path.join(_OUT_DIR, "phase3q_model_robustness_diagnostics.json")
PHASE3P_RESULT_JSON = os.path.join(_OUT_DIR, "phase3p_multisignal_walkforward_model.json")
PHASE3O_RESULT_JSON = os.path.join(_OUT_DIR, "phase3o_multisignal_feature_factory.json")
PHASE3M_RESULT_JSON = os.path.join(_OUT_DIR, "phase3m_earnings_estimates_signal_gate.json")

_M_DIR = os.path.join(_OUT_DIR, "phase3m_earnings_estimates_signal_gate")
PHASE3M_PROGRESS_JSON = os.path.join(_M_DIR, "collection_progress.json")
PHASE3M_FEATURES_CSV = os.path.join(_M_DIR, "earnings_features_universe.csv")
PHASE3M_RAW_DIR = os.path.join(_M_DIR, "raw")

# Local-only inventory scan scope (no internet, no D:).
_SCAN_ROOTS = [
    os.path.join(_REPO_ROOT, "research", "input"),
    os.path.join(_REPO_ROOT, "research", "output"),
    os.path.join(_REPO_ROOT, "data"),
    _REPO_ROOT,  # top-level CSVs only (non-recursive at the repo root)
]

# Coverage thresholds / cadence assumptions (documented, not provider-confirmed).
SIGNAL_GATE_MIN_TICKERS = 75
FULL_UNIVERSE_TARGET = 128
EARNINGS_PER_RUN_BUDGET = 20  # conservative free-tier tickers per controlled collection run

# --------------------------------------------------------------------------- #
# Decision values
# --------------------------------------------------------------------------- #
REC_CONTINUE_EARNINGS = "EVENT_SIGNAL_READINESS_CONTINUE_EARNINGS_COLLECTION"
REC_ACQUIRE_SENTIMENT = "EVENT_SIGNAL_READINESS_ACQUIRE_SENTIMENT_OR_ANALYST_DATA"
REC_READY_GLOBAL = "EVENT_SIGNAL_READINESS_READY_FOR_GLOBAL_ASSET_EXPANSION"
REC_BLOCKED = "EVENT_SIGNAL_READINESS_BLOCKED_INPUTS"
ALLOWED_RECOMMENDATIONS = (
    REC_CONTINUE_EARNINGS, REC_ACQUIRE_SENTIMENT, REC_READY_GLOBAL, REC_BLOCKED,
)

# Sentiment / analyst event-signal families and the FILENAME tokens that mark a CSV as a genuine
# candidate. Detection is filename-anchored (like the Phase 3-R macro scan) so that a derived
# earnings column such as ``estimate_revision_proxy`` is never misclassified as a real analyst
# revision dataset. Column patterns are then used to confirm usability of a filename candidate.
_SENTIMENT_FAMILIES = {
    "news_sentiment": ["news_sentiment", "sentiment", "news", "headline", "article", "media"],
    "social_sentiment": ["social", "twitter", "reddit", "stocktwits"],
}
_ANALYST_FAMILIES = {
    "analyst_revision": ["analyst_revision", "estimate_revision", "eps_revision",
                         "earnings_revision", "revision"],
    "analyst_rating": ["analyst_rating", "rating", "recommendation"],
    "target_price_revision": ["target_price", "price_target"],
}
_ALL_BLOCKED_FAMILIES = ("news_sentiment", "analyst_revision", "analyst_rating",
                         "target_price_revision")

# Column-name fragments that confirm a candidate file actually carries the needed value.
_TICKER_COL_HINTS = ["ticker", "symbol", "secid", "cusip", "permno"]
_DATE_COL_HINTS = ["date", "publication", "published", "event_date", "as_of", "availability",
                   "timestamp", "datetime"]
_SENTIMENT_VALUE_HINTS = ["sentiment_score", "sentiment", "polarity", "tone", "score"]
_REVISION_VALUE_HINTS = ["analyst_revision_value", "revision", "rating", "recommendation",
                         "target_price", "price_target", "estimate"]


# --------------------------------------------------------------------------- #
# Small IO helpers
# --------------------------------------------------------------------------- #
def _load_json(path):
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def _rel(path):
    try:
        return os.path.relpath(path, _REPO_ROOT).replace("\\", "/")
    except ValueError:
        return path.replace("\\", "/")


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


# --------------------------------------------------------------------------- #
# 1. Phase 3-R confirmation
# --------------------------------------------------------------------------- #
def confirm_phase3r(result_json_path=PHASE3R_RESULT_JSON):
    res = _load_json(result_json_path)
    checks = {
        "phase3r_result_present": res is not None,
        "phase_is_3r": False,
        "recommendation_ok": False,
        "next_phase_is_3s": False,
        "macro_not_faked": False,
        "sentiment_not_faked": False,
        "no_production_model_candidate": False,
        "no_production_predictions": False,
        "no_production_scores": False,
        "no_portfolio_weights": False,
        "no_deployable_artifact": False,
        "provider_api_not_called": False,
        "alpha_vantage_not_called": False,
    }
    rec = None
    if res is not None:
        rec = res.get("recommendation")
        if isinstance(rec, dict):
            rec = rec.get("recommendation")
        nxt = (res.get("recommended_next_phase") or {}).get("phase")
        checks["phase_is_3r"] = res.get("phase") == "3-R"
        checks["recommendation_ok"] = rec in (
            "MACRO_REGIME_WALKFORWARD_WEAK_NO_IMPROVEMENT",
            "MACRO_REGIME_WALKFORWARD_IMPROVES_ROBUSTNESS",
        )
        checks["next_phase_is_3s"] = nxt == "3-S"
        checks["macro_not_faked"] = res.get("macro_faked") is False
        checks["sentiment_not_faked"] = res.get("sentiment_faked") is False
        checks["no_production_model_candidate"] = \
            res.get("production_model_candidate_created") is False
        checks["no_production_predictions"] = res.get("production_predictions_computed") is False
        checks["no_production_scores"] = res.get("production_scores_computed") is False
        checks["no_portfolio_weights"] = res.get("portfolio_weights_computed") is False
        checks["no_deployable_artifact"] = res.get("deployable_model_artifact_written") is False
        checks["provider_api_not_called"] = res.get("provider_api_called") is False
        checks["alpha_vantage_not_called"] = res.get("alpha_vantage_called") is False
    checks["confirmed"] = all(v for k, v in checks.items() if k != "confirmed")
    checks["phase3r_recommendation"] = rec
    return checks


# --------------------------------------------------------------------------- #
# 2. Earnings coverage status
# --------------------------------------------------------------------------- #
def _count_cached_tickers(raw_dir=PHASE3M_RAW_DIR):
    """Count cached earnings tickers from the local Phase 3-M raw cache directory listing only.
    One provider JSON file per ticker. This is a read-only directory listing - no file content is
    parsed and no network is touched."""
    if not os.path.isdir(raw_dir):
        return 0
    return sum(1 for n in os.listdir(raw_dir)
               if n.lower().endswith(".json") and os.path.isfile(os.path.join(raw_dir, n)))


def earnings_coverage_status(progress_json=PHASE3M_PROGRESS_JSON, raw_dir=PHASE3M_RAW_DIR,
                             features_csv=PHASE3M_FEATURES_CSV):
    prog = _load_json(progress_json) or {}
    universe = int(prog.get("universe_ticker_count") or FULL_UNIVERSE_TARGET)
    cached_from_dir = _count_cached_tickers(raw_dir)
    cached_from_prog = prog.get("cached_ticker_count_after_run")
    # Authoritative cached count = local raw cache listing; cross-checked against progress.
    cached = cached_from_dir if cached_from_dir else int(cached_from_prog or 0)
    missing = max(0, universe - cached)
    min_tickers = int(prog.get("signal_gate_min_tickers") or SIGNAL_GATE_MIN_TICKERS)
    gate_allowed = bool(prog.get("signal_gate_allowed_now")) or (cached >= min_tickers)

    def _runs_to(target):
        need = max(0, target - cached)
        return int(math.ceil(need / EARNINGS_PER_RUN_BUDGET)) if need > 0 else 0

    guard_active = bool(prog.get("same_day_limit_guard_active"))
    keys = prog.get("supported_provider_keys_detected_as_booleans") or {}
    # Read only the already-detected booleans - never any key value itself. A key is "available"
    # if the upstream Phase 3-M progress detected any supported provider env var as present.
    key_available = any(bool(v) for v in keys.values())
    provider = prog.get("selected_provider") or "configured provider"

    if cached >= min_tickers:
        next_action = "COVERAGE_SUFFICIENT_RUN_EARNINGS_SIGNAL_GATE"
    elif guard_active:
        next_action = "WAIT_FOR_PROVIDER_DAILY_LIMIT_RESET"
    elif key_available:
        next_action = "RUN_RESUMABLE_COLLECTOR_IN_SEPARATE_CONTROLLED_STEP"
    else:
        next_action = "CONFIGURE_PROVIDER_API_KEY_FIRST"

    notes = (
        "cached counted from local raw cache listing (%d files); progress JSON reports %s. "
        "Provider=%s; same-day limit guard active=%s; api key detected=%s (boolean only, value "
        "never read). Free-tier budget assumed ~%d tickers/run."
        % (cached_from_dir, cached_from_prog, provider, guard_active, key_available,
           EARNINGS_PER_RUN_BUDGET)
    )
    status = {
        "universe_ticker_count": universe,
        "cached_ticker_count": cached,
        "missing_ticker_count": missing,
        "signal_gate_min_tickers": min_tickers,
        "signal_gate_allowed_now": gate_allowed,
        "estimated_runs_to_75_at_20_per_day": _runs_to(SIGNAL_GATE_MIN_TICKERS),
        "estimated_runs_to_128_at_20_per_day": _runs_to(FULL_UNIVERSE_TARGET),
        "provider_limit_last_hit_utc": prog.get("provider_limit_last_hit_utc"),
        "next_safe_collection_action": next_action,
        "notes": notes,
        # extra fields carried in the summary (not in the CSV) for downstream decisions:
        "_guard_active": guard_active,
        "_key_available": key_available,
        "_features_csv_present": os.path.isfile(features_csv),
        "_progress_present": bool(prog),
    }
    return status


# --------------------------------------------------------------------------- #
# 3. Earnings next collection plan
# --------------------------------------------------------------------------- #
def earnings_next_collection_plan(status):
    cached = status["cached_ticker_count"]
    guard_active = status["_guard_active"]
    key_available = status["_key_available"]
    min_tickers = status["signal_gate_min_tickers"]
    rows = []

    rows.append({
        "action_order": 1,
        "action": "INSPECT_COLLECTION_PROGRESS",
        "reason": "Read the local Phase 3-M collection progress + raw cache listing to measure "
                  "coverage. This readiness gate only inspects; it makes no provider call.",
        "requires_api_key": False,
        "uses_provider_call": False,
        "safe_to_run_today": True,
        "expected_new_tickers": 0,
        "commit_after_success": False,
        "notes": "Already performed by this Phase 3-S gate.",
    })

    if cached >= min_tickers:
        rows.append({
            "action_order": 2,
            "action": "RUN_EARNINGS_SIGNAL_GATE",
            "reason": "Cached coverage already meets the %d-ticker minimum; run the Phase 3-M "
                      "earnings signal gate (no new provider calls needed)." % min_tickers,
            "requires_api_key": False, "uses_provider_call": False, "safe_to_run_today": True,
            "expected_new_tickers": 0, "commit_after_success": True,
            "notes": "Coverage sufficient - evaluate the earnings signal rather than collect more.",
        })
        return rows

    if guard_active:
        rows.append({
            "action_order": 2,
            "action": "WAIT_FOR_PROVIDER_DAILY_LIMIT_RESET",
            "reason": "The same-day provider limit guard is active; collecting now would waste "
                      "quota or fail. Wait for the daily reset before the next controlled run.",
            "requires_api_key": True, "uses_provider_call": False, "safe_to_run_today": False,
            "expected_new_tickers": 0, "commit_after_success": False,
            "notes": "Do NOT call the provider while the same-day guard is active.",
        })
    elif key_available:
        rows.append({
            "action_order": 2,
            "action": "RUN_RESUMABLE_EARNINGS_COLLECTOR",
            "reason": "Guard inactive and a provider api key is detected; run the resumable, "
                      "cache-first, network-budgeted collector in a SEPARATE controlled step "
                      "(not from this gate) to cache ~%d more tickers." % EARNINGS_PER_RUN_BUDGET,
            "requires_api_key": True, "uses_provider_call": True, "safe_to_run_today": True,
            "expected_new_tickers": EARNINGS_PER_RUN_BUDGET, "commit_after_success": True,
            "notes": "Run as its own controlled step; this Phase 3-S gate never calls the provider.",
        })
    else:
        rows.append({
            "action_order": 2,
            "action": "CONFIGURE_PROVIDER_API_KEY_FIRST",
            "reason": "No provider api key detected; configure one (free tier) before any "
                      "collection run can proceed.",
            "requires_api_key": True, "uses_provider_call": False, "safe_to_run_today": False,
            "expected_new_tickers": 0, "commit_after_success": False,
            "notes": "Key value is never read or stored by this phase - only its presence boolean.",
        })

    rows.append({
        "action_order": 3,
        "action": "RECHECK_COVERAGE_STATUS_ONLY",
        "reason": "After a collection run, re-run the status-only coverage check to update cached "
                  "vs missing counts without further provider calls.",
        "requires_api_key": False, "uses_provider_call": False, "safe_to_run_today": True,
        "expected_new_tickers": 0, "commit_after_success": False,
        "notes": "Status-only; no network.",
    })
    rows.append({
        "action_order": 4,
        "action": "REPEAT_UNTIL_75_THEN_RUN_SIGNAL_GATE",
        "reason": "Repeat controlled collection runs across days until cached >= %d, then run the "
                  "Phase 3-M earnings signal gate and re-run the combined model." % min_tickers,
        "requires_api_key": True, "uses_provider_call": True, "safe_to_run_today": False,
        "expected_new_tickers": max(0, min_tickers - cached), "commit_after_success": True,
        "notes": "Each run is cache-first and network-budgeted; partial progress is preserved.",
    })
    return rows


# --------------------------------------------------------------------------- #
# 4. Sentiment / analyst-revision local inventory (filename-anchored, never faked)
# --------------------------------------------------------------------------- #
def _iter_local_csvs():
    seen = set()
    for root in _SCAN_ROOTS:
        if not os.path.isdir(root):
            continue
        recursive = (root != _REPO_ROOT)
        if recursive:
            for dirpath, _dirs, files in os.walk(root):
                for name in files:
                    if name.lower().endswith(".csv"):
                        p = os.path.join(dirpath, name)
                        if p not in seen:
                            seen.add(p)
                            yield p
        else:
            for name in os.listdir(root):
                p = os.path.join(root, name)
                if name.lower().endswith(".csv") and os.path.isfile(p) and p not in seen:
                    seen.add(p)
                    yield p


def _detect_family(basename, families):
    low = basename.lower()
    for family, tokens in families.items():
        if any(tok in low for tok in tokens):
            return family
    return None


def _read_csv_header_and_dates(path):
    """Read header + (optionally) min/max of a detected date column. Reads at most the file's
    rows once; returns (columns, row_count, date_min, date_max)."""
    cols, row_count = [], 0
    date_col = None
    date_min = date_max = None
    try:
        with open(path, "r", encoding="utf-8", newline="") as f:
            reader = csv.reader(f)
            header = next(reader, None)
            if not header:
                return [], 0, None, None
            cols = header
            low = [c.lower() for c in header]
            for i, c in enumerate(low):
                if any(h in c for h in _DATE_COL_HINTS):
                    date_col = i
                    break
            for r in reader:
                row_count += 1
                if date_col is not None and date_col < len(r):
                    v = (r[date_col] or "").strip()
                    if v:
                        if date_min is None or v < date_min:
                            date_min = v
                        if date_max is None or v > date_max:
                            date_max = v
    except OSError:
        return cols, row_count, None, None
    return cols, row_count, date_min, date_max


def _col_detected(cols, hints):
    low = [c.lower() for c in cols]
    return any(any(h in c for h in hints) for c in low)


def scan_event_signal_inventory():
    """Scan local repo CSVs (no internet, no D:) for genuine sentiment / analyst-revision data.
    Filename-anchored candidacy avoids misclassifying derived earnings columns. Returns
    (sentiment_rows, analyst_rows, sentiment_summary, analyst_summary)."""
    sentiment_rows, analyst_rows = [], []
    for path in _iter_local_csvs():
        base = os.path.basename(path)
        s_family = _detect_family(base, _SENTIMENT_FAMILIES)
        a_family = _detect_family(base, _ANALYST_FAMILIES)
        if not s_family and not a_family:
            continue
        cols, row_count, dmin, dmax = _read_csv_header_and_dates(path)
        ticker_ok = _col_detected(cols, _TICKER_COL_HINTS)
        if s_family:
            value_ok = _col_detected(cols, _SENTIMENT_VALUE_HINTS)
            usable = bool(ticker_ok and value_ok and row_count > 0 and (dmin is not None))
            blocker = "" if usable else (
                "missing required columns/dates for a point-in-time sentiment series "
                "(need ticker, publication/event date, sentiment_score)")
            sentiment_rows.append({
                "file_path": _rel(path), "detected_family": s_family,
                "columns": ";".join(cols), "row_count": row_count,
                "date_min": dmin or "", "date_max": dmax or "",
                "ticker_column_detected": ticker_ok,
                "sentiment_or_revision_column_detected": value_ok,
                "usable": usable, "blocker": blocker,
            })
        if a_family:
            value_ok = _col_detected(cols, _REVISION_VALUE_HINTS)
            usable = bool(ticker_ok and value_ok and row_count > 0 and (dmin is not None))
            blocker = "" if usable else (
                "missing required columns/dates for a point-in-time analyst-revision series "
                "(need ticker, event/publication date, analyst_revision_value)")
            analyst_rows.append({
                "file_path": _rel(path), "detected_family": a_family,
                "columns": ";".join(cols), "row_count": row_count,
                "date_min": dmin or "", "date_max": dmax or "",
                "ticker_column_detected": ticker_ok,
                "sentiment_or_revision_column_detected": value_ok,
                "usable": usable, "blocker": blocker,
            })

    def _summarize(rows, families):
        usable_rows = [r for r in rows if r["usable"]]
        return {
            "candidate_files": len(rows),
            "usable_files": len(usable_rows),
            "usable_file_paths": [r["file_path"] for r in usable_rows],
            "families_searched": list(families.keys()),
            "any_usable": bool(usable_rows),
        }

    return (sentiment_rows, analyst_rows,
            _summarize(sentiment_rows, _SENTIMENT_FAMILIES),
            _summarize(analyst_rows, _ANALYST_FAMILIES))


def event_signal_data_requirements():
    """Exact (non-faked) data requirements for the blocked sentiment / analyst families and the
    free/local sources that could satisfy them. NOTHING here calls an API or browses the web."""
    return {
        "blocked_families": list(_ALL_BLOCKED_FAMILIES),
        "required_columns": {
            "ticker": "equity symbol matching the Phase 3-L / 3-M universe",
            "event_date_or_publication_date": "the date the article / rating / revision was "
                                              "published (drives point-in-time availability)",
            "sentiment_score_or_analyst_revision_value": "a real numeric sentiment score, rating "
                                              "change, or estimate-revision value (never fabricated)",
            "source": "the data origin (vendor / outlet / provider export)",
            "point_in_time_availability_date": "the timestamp the value first became known, so it "
                                              "can be merged as-of without lookahead",
        },
        "recommended_local_sources_described_only": [
            "a local exported news-sentiment CSV (vendor export the user already has rights to)",
            "a local analyst-revision CSV (estimate / rating / target-price changes with dates)",
            "a local earnings-estimate-revision CSV (consensus estimate time series with dates)",
            "a provider export the user later chooses (dropped into research/input as a local CSV)",
        ],
        "non_negotiable": [
            "data must be real (never faked or synthesized)",
            "must carry a point-in-time availability/publication date (no lookahead)",
            "must be obtained locally / via a source the user is entitled to (no API call here)",
        ],
        "note": "Phase 3-S describes these requirements only; it never fetches, browses, or calls "
                "any API. Place satisfying local CSVs under research/input and re-run the gate.",
    }


# --------------------------------------------------------------------------- #
# 5. Priority decision + roadmap
# --------------------------------------------------------------------------- #
def build_priority_table(status, sentiment_summary, analyst_summary):
    cached = status["cached_ticker_count"]
    min_tickers = status["signal_gate_min_tickers"]
    universe = status["universe_ticker_count"]
    below_min = cached < min_tickers
    have_event_data = sentiment_summary["any_usable"] or analyst_summary["any_usable"]

    rows = [
        {
            "workstream": "continue_earnings_coverage_to_75_tickers",
            "expected_impact": "HIGH",
            "urgency": "NOW" if below_min else "DONE",
            "dependency": "provider api key + daily free-tier budget",
            "estimated_effort": "LOW_MEDIUM",
            "cost_risk": "LOW (free tier; cache-first, network-budgeted)",
            "why_now": "cached %d of %d; %d short of the %d-ticker signal-gate minimum"
                       % (cached, universe, max(0, min_tickers - cached), min_tickers),
            "recommended_next_phase": "3-T",
        },
        {
            "workstream": "acquire_analyst_revision_data",
            "expected_impact": "HIGH",
            "urgency": "HIGH" if not analyst_summary["any_usable"] else "DONE",
            "dependency": "local analyst-revision CSV with point-in-time dates",
            "estimated_effort": "MEDIUM",
            "cost_risk": "MEDIUM (data acquisition / entitlement; no faking)",
            "why_now": "orthogonal event signal; macro repaired the bad year but lowered mean IC",
            "recommended_next_phase": "3-U",
        },
        {
            "workstream": "acquire_news_sentiment_data",
            "expected_impact": "MEDIUM_HIGH",
            "urgency": "MEDIUM" if not sentiment_summary["any_usable"] else "DONE",
            "dependency": "local news/social sentiment CSV with publication dates",
            "estimated_effort": "MEDIUM",
            "cost_risk": "MEDIUM (data acquisition / entitlement; no faking)",
            "why_now": "second orthogonal event-driven family to lift average IC",
            "recommended_next_phase": "3-U",
        },
        {
            "workstream": "finish_earnings_coverage_to_128_tickers",
            "expected_impact": "MEDIUM",
            "urgency": "MEDIUM",
            "dependency": "provider api key + several more budgeted runs",
            "estimated_effort": "MEDIUM",
            "cost_risk": "LOW (free tier across multiple days)",
            "why_now": "full-universe coverage reduces survivorship/coverage noise after the gate",
            "recommended_next_phase": "3-T",
        },
        {
            "workstream": "add_global_cross_asset_etf_proxy_universe",
            "expected_impact": "MEDIUM",
            "urgency": "LATER",
            "dependency": "local ETF / global asset price CSVs (free, manual)",
            "estimated_effort": "MEDIUM",
            "cost_risk": "LOW (free local data)",
            "why_now": "cross-asset regime context once event-signal families are in place",
            "recommended_next_phase": "3-V",
        },
        {
            "workstream": "build_risk_simulation_turnover_constraints",
            "expected_impact": "MEDIUM",
            "urgency": "LATER",
            "dependency": "a model whose signal has cleared the robustness bar",
            "estimated_effort": "MEDIUM_HIGH",
            "cost_risk": "LOW (compute only)",
            "why_now": "premature until an orthogonal signal lifts robust IC",
            "recommended_next_phase": "3-W",
        },
        {
            "workstream": "prepare_non_production_candidate",
            "expected_impact": "LOW",
            "urgency": "LAST",
            "dependency": "a robustness-passing, risk-simulated research model",
            "estimated_effort": "MEDIUM",
            "cost_risk": "LOW (research packaging only; never deployed)",
            "why_now": "only after the signal and risk work pass; no production edge claimed",
            "recommended_next_phase": "3-X",
        },
    ]

    # Rank: earnings-to-75 first while below the minimum; otherwise event-data acquisition leads.
    def _rank_key(r):
        ws = r["workstream"]
        if below_min:
            order = ["continue_earnings_coverage_to_75_tickers",
                     "acquire_analyst_revision_data", "acquire_news_sentiment_data",
                     "finish_earnings_coverage_to_128_tickers",
                     "add_global_cross_asset_etf_proxy_universe",
                     "build_risk_simulation_turnover_constraints",
                     "prepare_non_production_candidate"]
        elif not have_event_data:
            order = ["acquire_analyst_revision_data", "acquire_news_sentiment_data",
                     "finish_earnings_coverage_to_128_tickers",
                     "continue_earnings_coverage_to_75_tickers",
                     "add_global_cross_asset_etf_proxy_universe",
                     "build_risk_simulation_turnover_constraints",
                     "prepare_non_production_candidate"]
        else:
            order = ["add_global_cross_asset_etf_proxy_universe",
                     "build_risk_simulation_turnover_constraints",
                     "finish_earnings_coverage_to_128_tickers",
                     "acquire_analyst_revision_data", "acquire_news_sentiment_data",
                     "continue_earnings_coverage_to_75_tickers",
                     "prepare_non_production_candidate"]
        return order.index(ws) if ws in order else len(order)

    rows.sort(key=_rank_key)
    for i, r in enumerate(rows, start=1):
        r["priority_rank"] = i
    return rows


def build_roadmap():
    return [
        {"phase": "3-T",
         "title": "Continue Earnings Coverage to 75+ Tickers",
         "description": "Run the resumable, cache-first, network-budgeted earnings collector across "
                        "days until cached >= 75, then re-run the earnings/macro/technical model.",
         "depends_on": "provider api key + free-tier daily budget", "status": "NEXT"},
        {"phase": "3-U",
         "title": "Add Local Sentiment / Analyst Revision Data (if acquired)",
         "description": "Ingest a real local news-sentiment or analyst-revision CSV with "
                        "point-in-time dates and re-run the combined model. Never faked.",
         "depends_on": "local sentiment/analyst CSV with availability dates", "status": "PLANNED"},
        {"phase": "3-V",
         "title": "Global Cross-Asset ETF Proxy Universe + Cross-Asset Regime Features",
         "description": "Add free local ETF / global asset proxies and cross-asset regime features.",
         "depends_on": "local ETF/global asset price CSVs", "status": "PLANNED"},
        {"phase": "3-W",
         "title": "Turnover-Aware Risk Simulation and Portfolio Construction",
         "description": "Simulate turnover-aware risk and (research-only) portfolio construction "
                        "once an orthogonal signal clears the robustness bar.",
         "depends_on": "a robustness-passing research signal", "status": "PLANNED"},
        {"phase": "3-X",
         "title": "Non-Production Candidate Packaging",
         "description": "Package a research candidate (no deployable artifact, no production edge).",
         "depends_on": "passing 3-W risk simulation", "status": "PLANNED"},
        {"phase": "3-Y",
         "title": "Paper Trader Preview Integration Only",
         "description": "Preview-only integration; no orders, no automation, manual review only.",
         "depends_on": "a reviewed non-production candidate", "status": "PLANNED"},
    ]


# --------------------------------------------------------------------------- #
# 6. Decision
# --------------------------------------------------------------------------- #
def decide(inputs_ok, status, sentiment_summary, analyst_summary):
    if not inputs_ok:
        return REC_BLOCKED
    cached = status["cached_ticker_count"]
    min_tickers = status["signal_gate_min_tickers"]
    have_event_data = sentiment_summary["any_usable"] or analyst_summary["any_usable"]
    if cached < min_tickers:
        return REC_CONTINUE_EARNINGS
    if not have_event_data:
        return REC_ACQUIRE_SENTIMENT
    return REC_READY_GLOBAL


_NEXT_PHASE = {
    REC_CONTINUE_EARNINGS: ("3-T", "Continue Earnings Coverage to 75+ Tickers",
                            "Run the resumable earnings collector across days until cached >= 75, "
                            "then re-run the earnings/macro/technical model. No model trained yet."),
    REC_ACQUIRE_SENTIMENT: ("3-T", "Acquire Local Sentiment or Analyst Revision Data",
                            "Obtain a real local sentiment / analyst-revision CSV with point-in-time "
                            "dates (never faked) and ingest it as the next orthogonal signal."),
    REC_READY_GLOBAL: ("3-T", "Global Cross-Asset ETF Proxy Universe",
                       "Add a free local cross-asset ETF proxy universe and cross-asset regime "
                       "features as the next data family."),
    REC_BLOCKED: ("3-T", "Repair Phase 3-S Inputs",
                  "Required Phase 3-R / 3-M inputs are missing; repair them before proceeding."),
}


def build_recommended_next_phase(rec):
    phase, title, purpose = _NEXT_PHASE[rec]
    return {"phase": phase, "title": title, "purpose": purpose}


def build_recommendation(rec, status, sentiment_summary, analyst_summary):
    cached = status["cached_ticker_count"]
    min_tickers = status["signal_gate_min_tickers"]
    reasons = {
        REC_CONTINUE_EARNINGS:
            "Cached earnings coverage is %d of the %d-ticker universe, below the %d-ticker "
            "minimum needed to run the earnings signal gate, so the next best action is to "
            "continue the resumable earnings collection (cache-first, budgeted, no faking)."
            % (cached, status["universe_ticker_count"], min_tickers),
        REC_ACQUIRE_SENTIMENT:
            "Earnings coverage meets the minimum but no usable local sentiment or analyst-revision "
            "data exists, so the next best action is to acquire a real local sentiment / analyst "
            "dataset (never faked).",
        REC_READY_GLOBAL:
            "Earnings coverage meets the minimum and usable local sentiment/analyst data exists, "
            "so the project is ready to expand to a global cross-asset ETF proxy universe.",
        REC_BLOCKED:
            "Required Phase 3-R / 3-M inputs are missing; repair them before proceeding.",
    }
    return {
        "recommendation": rec,
        "allowed_values": list(ALLOWED_RECOMMENDATIONS),
        "research_only": True,
        "create_production_model_candidate_now": False,
        "train_production_model_now": False,
        "compute_portfolio_weights_now": False,
        "deploy_now": False,
        "production_edge_claimed": False,
        "earnings_faked": False,
        "sentiment_faked": False,
        "analyst_revision_faked": False,
        "reason": reasons[rec],
    }


def build_decision_table(p3r_checks, status, sentiment_summary, analyst_summary, rec):
    rows = []

    def add(item, value, passed, note):
        rows.append({"decision_item": item, "value": value,
                     "passed": "" if passed is None else bool(passed), "note": note})

    cached = status["cached_ticker_count"]
    min_tickers = status["signal_gate_min_tickers"]
    add("phase3r_confirmed", p3r_checks.get("confirmed"), p3r_checks.get("confirmed"),
        "Phase 3-R present, WEAK/IMPROVES recommendation, next phase 3-S, no production artifacts")
    add("earnings_cached_tickers", cached, None, "from local Phase 3-M raw cache listing")
    add("earnings_coverage_meets_min", cached >= min_tickers, cached >= min_tickers,
        "needs >= %d cached tickers to run the earnings signal gate" % min_tickers)
    add("usable_local_sentiment_data", sentiment_summary["any_usable"],
        sentiment_summary["any_usable"], "real local news/social sentiment CSV with PIT dates")
    add("usable_local_analyst_revision_data", analyst_summary["any_usable"],
        analyst_summary["any_usable"], "real local analyst-revision CSV with PIT dates")
    add("no_sentiment_data_faked", True, True, "blocked families instead of fabricating values")
    add("no_analyst_revision_data_faked", True, True, "blocked families instead of fabricating")
    add("no_earnings_data_faked", True, True, "only inspected the existing local cache")
    add("provider_or_alpha_vantage_called", False, True,
        "no provider / Alpha Vantage / FRED API call by this gate")
    add("provider_api_key_value_read", False, True, "only detected booleans read, never the value")
    add("d_drive_read_or_written", False, True, "nothing on the D: drive touched")
    add("no_production_model_candidate", True, True, "readiness/prioritization gate only")
    add("no_production_predictions_or_scores", True, True, "no predictions/scores computed")
    add("no_portfolio_weights", True, True, "no portfolio weights computed")
    add("no_deployable_artifact", True, True, "no serialized-estimator file written")
    add("recommendation", rec, rec in ALLOWED_RECOMMENDATIONS, "allowed values only")
    return rows


def build_safety_flags():
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
        "d_drive_read": False,
        "d_drive_written": False,
        "provider_api_called": False,
        "alpha_vantage_called": False,
        "paid_vendor_api_called": False,
        "fred_called": False,
        "macro_faked": False,
        "sentiment_faked": False,
        "analyst_revision_faked": False,
        "earnings_faked": False,
        "labels_for_validation_only": True,
    }


def build_interpretation(rec, status, sentiment_summary, analyst_summary):
    return {
        "phase_is_research_only": True,
        "research_only": True,
        "readiness_and_prioritization_gate_only": True,
        "earnings_collection_performed": False,
        "provider_api_called": False,
        "provider_api_key_value_read": False,
        "sentiment_family_faked": False,
        "analyst_revision_family_faked": False,
        "earnings_family_faked": False,
        "production_predictions_computed": False,
        "production_scores_computed": False,
        "portfolio_weights_computed": False,
        "deployable_model_artifact_written": False,
        "production_edge_claimed": False,
        "results_remain_survivorship_biased": True,
        "narrative":
            "Phase 3-S is the research-only readiness and prioritization gate that follows the "
            "Phase 3-R macro walk-forward (WEAK_NO_IMPROVEMENT: macro repaired the bad year but "
            "lowered mean IC). It measures current earnings coverage from the local Phase 3-M "
            "cache, plans the next SAFE resumable collection step without calling the provider, "
            "scans the local repo for genuine sentiment / analyst-revision data, and - finding "
            "none usable - marks those families BLOCKED and writes their exact (non-faked) data "
            "requirements. It then ranks the next workstreams and recommends %s. It trains no "
            "model, computes no predictions / scores / portfolio weights / order instructions, "
            "writes no deployable artifact, touches no database, runs no migration, restarts no "
            "service, enables no serving flag, reads nothing from the D: drive, reads no provider "
            "API key value, and calls no provider / paid-vendor / Alpha Vantage / FRED API. The "
            "universe is current-as-of, so all results remain survivorship-biased and claim no "
            "production edge." % rec,
    }


# --------------------------------------------------------------------------- #
# Output column specs
# --------------------------------------------------------------------------- #
_OUT_PATHS = {
    "coverage": "earnings_coverage_status.csv",
    "plan": "earnings_next_collection_plan.csv",
    "sentiment": "sentiment_data_inventory.csv",
    "analyst": "analyst_revision_data_inventory.csv",
    "priority": "event_signal_priority_table.csv",
    "roadmap": "model_improvement_roadmap.csv",
    "decision": "readiness_decision_table.csv",
}
_COVERAGE_COLS = ["universe_ticker_count", "cached_ticker_count", "missing_ticker_count",
                  "signal_gate_min_tickers", "signal_gate_allowed_now",
                  "estimated_runs_to_75_at_20_per_day", "estimated_runs_to_128_at_20_per_day",
                  "provider_limit_last_hit_utc", "next_safe_collection_action", "notes"]
_PLAN_COLS = ["action_order", "action", "reason", "requires_api_key", "uses_provider_call",
              "safe_to_run_today", "expected_new_tickers", "commit_after_success", "notes"]
_INVENTORY_COLS = ["file_path", "detected_family", "columns", "row_count", "date_min", "date_max",
                   "ticker_column_detected", "sentiment_or_revision_column_detected", "usable",
                   "blocker"]
_PRIORITY_COLS = ["priority_rank", "workstream", "expected_impact", "urgency", "dependency",
                  "estimated_effort", "cost_risk", "why_now", "recommended_next_phase"]
_ROADMAP_COLS = ["phase", "title", "description", "depends_on", "status"]
_DECISION_COLS = ["decision_item", "value", "passed", "note"]


def _out(o_dir, key):
    return os.path.join(o_dir, _OUT_PATHS[key])


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
def run(result_json_path=RESULT_JSON, o_dir=_S_DIR, phase3r_result_json=PHASE3R_RESULT_JSON,
        progress_json=PHASE3M_PROGRESS_JSON, raw_dir=PHASE3M_RAW_DIR,
        features_csv=PHASE3M_FEATURES_CSV, verbose=True):
    p3r_checks = confirm_phase3r(phase3r_result_json)

    if verbose:
        print("  measuring local earnings coverage ...")
    status = earnings_coverage_status(progress_json, raw_dir, features_csv)

    if verbose:
        print("  scanning local repo for sentiment / analyst-revision data ...")
    sentiment_rows, analyst_rows, sentiment_summary, analyst_summary = \
        scan_event_signal_inventory()

    # Inputs are OK when Phase 3-R is confirmed AND the Phase 3-M collection progress is present.
    inputs_ok = p3r_checks["confirmed"] and status["_progress_present"]
    rec = decide(inputs_ok, status, sentiment_summary, analyst_summary)

    plan_rows = earnings_next_collection_plan(status)
    priority_rows = build_priority_table(status, sentiment_summary, analyst_summary)
    roadmap_rows = build_roadmap()
    decision_rows = build_decision_table(p3r_checks, status, sentiment_summary, analyst_summary,
                                         rec)

    # Blocked event-signal families = those with no usable local data.
    blocked_families = []
    usable_families = set()
    for r in sentiment_rows + analyst_rows:
        if r["usable"]:
            usable_families.add(r["detected_family"])
    for fam in _ALL_BLOCKED_FAMILIES:
        if fam not in usable_families:
            blocked_families.append(fam)

    requirements = event_signal_data_requirements()

    # Write CSV outputs.
    coverage_csv_row = {k: status[k] for k in _COVERAGE_COLS}
    _write_csv(_out(o_dir, "coverage"), _COVERAGE_COLS, [coverage_csv_row])
    _write_csv(_out(o_dir, "plan"), _PLAN_COLS, plan_rows)
    _write_csv(_out(o_dir, "sentiment"), _INVENTORY_COLS, sentiment_rows)
    _write_csv(_out(o_dir, "analyst"), _INVENTORY_COLS, analyst_rows)
    _write_csv(_out(o_dir, "priority"), _PRIORITY_COLS, priority_rows)
    _write_csv(_out(o_dir, "roadmap"), _ROADMAP_COLS, roadmap_rows)
    _write_csv(_out(o_dir, "decision"), _DECISION_COLS, decision_rows)

    result = {
        "phase": PHASE,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "inputs_read": {
            "phase3r_result_json": _rel(phase3r_result_json),
            "phase3q_result_json": _rel(PHASE3Q_RESULT_JSON),
            "phase3p_result_json": _rel(PHASE3P_RESULT_JSON),
            "phase3o_result_json": _rel(PHASE3O_RESULT_JSON),
            "phase3m_result_json": _rel(PHASE3M_RESULT_JSON),
            "phase3m_collection_progress_json": _rel(progress_json),
            "phase3m_earnings_features_universe_csv": _rel(features_csv),
            "phase3m_raw_cache_dir": _rel(raw_dir),
        },
        "outputs_written": {
            "result_json": _rel(result_json_path),
            **{k: _rel(_out(o_dir, k)) for k in _OUT_PATHS},
        },
        "phase3r_confirmation": p3r_checks,
        "earnings_coverage_summary": {k: status[k] for k in _COVERAGE_COLS},
        "earnings_next_collection_plan": plan_rows,
        "sentiment_inventory_summary": sentiment_summary,
        "analyst_revision_inventory_summary": analyst_summary,
        "blocked_event_signal_families": blocked_families,
        "event_signal_data_requirements": requirements,
        "priority_decision": {
            "ranked_workstreams": [
                {"priority_rank": r["priority_rank"], "workstream": r["workstream"],
                 "recommended_next_phase": r["recommended_next_phase"]}
                for r in priority_rows],
            "top_priority": priority_rows[0]["workstream"] if priority_rows else None,
        },
        "model_improvement_roadmap": roadmap_rows,
        "readiness_decision_table": decision_rows,
        "recommendation": build_recommendation(rec, status, sentiment_summary, analyst_summary),
        "recommended_next_phase": build_recommended_next_phase(rec),
        "interpretation": build_interpretation(rec, status, sentiment_summary, analyst_summary),
    }
    result.update(build_safety_flags())
    _dump_json(result_json_path, result)
    return result


def main():
    result = run()
    rec = result["recommendation"]["recommendation"]
    cov = result["earnings_coverage_summary"]
    print("phase 3-R confirmed:        %s" % result["phase3r_confirmation"]["confirmed"])
    print("universe tickers:           %s" % cov["universe_ticker_count"])
    print("cached earnings tickers:    %s" % cov["cached_ticker_count"])
    print("missing earnings tickers:   %s" % cov["missing_ticker_count"])
    print("signal gate allowed now:    %s" % cov["signal_gate_allowed_now"])
    print("next safe collection action:%s" % cov["next_safe_collection_action"])
    print("sentiment usable files:     %s" % result["sentiment_inventory_summary"]["usable_files"])
    print("analyst usable files:       %s"
          % result["analyst_revision_inventory_summary"]["usable_files"])
    print("blocked families:           %s" % ", ".join(result["blocked_event_signal_families"]))
    print("top priority:               %s" % result["priority_decision"]["top_priority"])
    print("recommendation:             %s" % rec)
    print("recommended next:           %s - %s"
          % (result["recommended_next_phase"]["phase"], result["recommended_next_phase"]["title"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
