"""Phase 5-D — External Data Alpha Enrichment Inventory and Point-in-Time Readiness.

Track A (quant brain) research, offline. After Phase 5-C showed the **price-only**
cross-sectional model is not strong enough to deploy (baseline rank IC ~0, fitted
models only modestly above noise, survivorship-inflated returns), the next edge
must come from **external alpha data**. This phase does NOT train a model and does
NOT fabricate data. It answers, from the data that already lives in this repo /
on the read-only D: input drive:

    1. What non-price local datasets exist?
    2. Which are point-in-time safe (usable in a walk-forward model w/o look-ahead)?
    3. Which can be joined to the Phase 5-C ticker/date panel without leakage?
    4. Which should become Phase 5-E features?
    5. Which datasets are missing and must be collected later?
    6. Can fundamentals / earnings / analyst revisions / macro / sector / ETF /
       sentiment features plausibly strengthen the model?

It produces an inventory, a point-in-time readiness table, a candidate
feature-family plan, a joinability matrix, a missing-source list, and a
machine-readable Phase 5-E implementation plan with go/no-go gates that compare an
enriched model against the Phase 5-C price-only baseline.

HONESTY CONTRACT: nothing here is faked. Where a dataset is absent (analyst
revisions, news sentiment, options/IV, point-in-time index constituents), it is
reported as MISSING / future_collection_needed, never invented. Where a dataset is
present but only partial (earnings, global ETF), that is stated. The prior
multisignal attempt (Phase 3-P) did NOT beat price-only at the headline model, so
the Phase 5-E plan is framed as a strict *incremental-edge* test, not a foregone
win.

Hard rules honored: no network, no paid APIs, no AlphaVantage, no orders, no broker
execution, no automation, no deploy, no Paper Trader changes, no GCP changes, no
writes to D: (D: is read-only INPUT only), no binary model artifacts
(.pkl/.joblib/.onnx/.pt/.h5/.keras), no commit. Pure offline stdlib.
"""
from __future__ import annotations

import csv
import json
import os
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

_OUT_DIR = _REPO_ROOT / "research" / "output"
_INPUT_DIR = _REPO_ROOT / "research" / "input"

# Local, read-only price-history candidates (D: is INPUT ONLY, never written).
_PRICE_HISTORY_CANDIDATES: List[str] = [
    r"D:\Stock_Prediction_app_data\phase2k_g\output\phase2k_g_expanded_price_history_free.csv",
    str(_REPO_ROOT / "research" / "output" / "phase2g_price_history_real.csv"),
]

# Output artifacts.
_REPORT_OUT = _OUT_DIR / "phase5d_external_data_alpha_enrichment.json"
_INVENTORY_OUT = _OUT_DIR / "phase5d_external_data_inventory.csv"
_PIT_READINESS_OUT = _OUT_DIR / "phase5d_point_in_time_readiness.csv"
_FEATURE_FAMILIES_OUT = _OUT_DIR / "phase5d_candidate_feature_families.csv"
_JOINABILITY_OUT = _OUT_DIR / "phase5d_joinability_matrix.csv"
_MISSING_OUT = _OUT_DIR / "phase5d_missing_alpha_sources.csv"
_PLAN_OUT = _OUT_DIR / "phase5d_phase5e_implementation_plan.json"

# The Phase 5-C baseline this phase is meant to (eventually) beat.
_BASELINE_SCOREBOARD = "research/output/phase5c_model_scoreboard.csv"
PRIMARY_LABEL = "forward_excess_return_20d_vs_spy"

# Classification vocabularies (kept explicit so artifacts are auditable).
FAMILIES = (
    "price_history", "macro", "sector", "fundamentals", "earnings",
    "analyst_revisions", "news_sentiment", "global_etf_cross_asset",
    "liquidity", "universe_mapping", "other",
)
PIT_STATUSES = (
    "point_in_time_safe", "potentially_point_in_time",
    "not_point_in_time_safe", "unknown",
)
JOINABILITIES = (
    "ticker_date_joinable", "date_only_joinable",
    "ticker_only_joinable", "not_joinable",
)
LEAKAGE_RISKS = ("low", "medium", "high", "unknown")
RECOMMENDED_USES = (
    "use_in_phase5e", "inspect_manually", "exclude_for_now",
    "future_collection_needed",
)

_DATE_TOKENS = ("date", "asof", "as_of", "observation", "filed",
                "availability", "reported", "timestamp", "datetime", "active_as_of")
_TICKER_TOKENS = ("ticker", "symbol", "cik")
_FRED_CODES = ("CPIAUCSL", "FEDFUNDS", "DGS10", "DGS2", "DCOILWTICO", "DTWEXBGS")

# Heuristic detectors based on real column signatures observed in the repo.
_FUNDAMENTAL_COL_HINTS = ("active_feature_asof_date", "fiscal_period", "operating_margin",
                          "net_margin", "filing_lag_days", "fiscal_year", "filed", "frame")
_EARNINGS_COL_HINTS = ("eps_surprise", "active_earnings_reported_date", "earnings_provider",
                       "trailing_4q_avg_surprise_pct", "days_since_last_earnings")


# --------------------------------------------------------------------------- #
# File scan helpers (read-only, single pass per file)
# --------------------------------------------------------------------------- #
def _scan_csv(path: Path) -> Dict:
    """Read header + count data rows + detect date/ticker columns in one pass.

    Never raises: an unreadable file returns a record with row_count=None and a
    blocker note. Performs no writes.
    """
    rec: Dict = {
        "columns": [], "row_count": None, "date_columns": [], "ticker_columns": [],
        "date_min": "", "date_max": "", "error": "",
    }
    try:
        with open(path, "r", newline="", encoding="utf-8-sig") as fh:
            reader = csv.reader(fh)
            try:
                header = next(reader)
            except StopIteration:
                rec["row_count"] = 0
                return rec
            header = [h.strip() for h in header]
            rec["columns"] = header
            lowered = [h.lower() for h in header]
            date_cols = [header[i] for i, h in enumerate(lowered)
                         if any(tok in h for tok in _DATE_TOKENS)]
            ticker_cols = [header[i] for i, h in enumerate(lowered)
                           if h in _TICKER_TOKENS]
            rec["date_columns"] = date_cols
            rec["ticker_columns"] = ticker_cols
            # Primary date column for min/max tracking (first detected).
            di = lowered.index(date_cols[0].lower()) if date_cols else -1
            n = 0
            dmin: Optional[str] = None
            dmax: Optional[str] = None
            for row in reader:
                if not row or all(c == "" for c in row):
                    continue
                n += 1
                if di >= 0 and di < len(row):
                    v = (row[di] or "")[:10]
                    if len(v) == 10 and v[4] == "-" and v[7] == "-":
                        if dmin is None or v < dmin:
                            dmin = v
                        if dmax is None or v > dmax:
                            dmax = v
            rec["row_count"] = n
            rec["date_min"] = dmin or ""
            rec["date_max"] = dmax or ""
    except Exception as exc:  # pragma: no cover - defensive, file system dependent
        rec["error"] = f"{type(exc).__name__}: {exc}"
    return rec


def _rel(path: Path) -> str:
    try:
        return str(path.relative_to(_REPO_ROOT)).replace("\\", "/")
    except ValueError:
        return str(path).replace("\\", "/")


def _price_history_path() -> Optional[Path]:
    for cand in _PRICE_HISTORY_CANDIDATES:
        p = Path(cand)
        if p.is_file():
            return p
    return None


# --------------------------------------------------------------------------- #
# Classification (deterministic, column- and path-driven)
# --------------------------------------------------------------------------- #
def classify_family(path_str: str, columns: Sequence[str]) -> str:
    p = path_str.lower().replace("\\", "/")
    cols = {c.lower() for c in columns}
    base = os.path.basename(p)

    if "phase2k_g_expanded_price_history_free" in p or "price_history" in p:
        return "price_history"
    if "global_etf" in p or "global_asset" in p:
        return "global_etf_cross_asset"
    if "analyst_revision" in p:
        return "analyst_revisions"
    if "sentiment" in p:
        return "news_sentiment"
    if "earnings" in p or any(h in cols for h in _EARNINGS_COL_HINTS):
        return "earnings"
    if "sector_map" in p or (("sector" in cols) and ("as_of_date" in cols) and ("ticker" in cols)):
        return "sector"
    if "universe_tickers" in p or ("active_as_of" in cols and "ticker" in cols and "name" in cols):
        return "universe_mapping"
    if ("fundamental" in p or "sec_universe" in p or "aligned_feature_price_panel" in p
            or any(h in cols for h in _FUNDAMENTAL_COL_HINTS)):
        return "fundamentals"
    if base.startswith("macro_") or "observation_date" in cols or any(
            base.upper().startswith(code) for code in _FRED_CODES) or base == "fredgraph.csv":
        return "macro"
    return "other"


def _has_ticker(rec: Dict) -> bool:
    return bool(rec["ticker_columns"])


def _has_date(rec: Dict) -> bool:
    return bool(rec["date_columns"])


def classify_joinability(family: str, rec: Dict) -> str:
    # Static reference maps join on ticker only even though they carry an as-of stamp.
    if family in ("sector", "universe_mapping"):
        return "ticker_only_joinable"
    if family == "macro":
        return "date_only_joinable"
    if _has_ticker(rec) and _has_date(rec):
        return "ticker_date_joinable"
    if _has_date(rec):
        return "date_only_joinable"
    if _has_ticker(rec):
        return "ticker_only_joinable"
    return "not_joinable"


def classify_pit(family: str, rec: Dict) -> Tuple[str, str]:
    """Return (pit_status, blocker_note)."""
    cols = {c.lower() for c in rec["columns"]}
    rows = rec["row_count"] or 0
    has_avail = any(k in cols for k in ("availability_datetime", "active_feature_asof_date",
                                        "feature_asof_date", "filed", "availability_date",
                                        "active_earnings_reported_date"))
    if family == "price_history":
        return "point_in_time_safe", ""
    if family == "fundamentals":
        if has_avail:
            return "point_in_time_safe", ""
        if "fiscal_period" in cols or "fiscal_year" in cols:
            return "potentially_point_in_time", "has fiscal period but no explicit filing/availability date column"
        return "unknown", "no filing/availability date column detected"
    if family == "earnings":
        if rows <= 0:
            return "unknown", "header-only / no data rows: earnings panel not populated for the universe"
        if has_avail:
            return "point_in_time_safe", ""
        return "potentially_point_in_time", "needs an explicit earnings announcement/availability date for as-of joins"
    if family == "macro":
        return ("potentially_point_in_time",
                "observation_date is the reference-period date, NOT the release date; "
                "needs a release/availability lag (Phase 3-R registry applies a release-date or "
                "default 21-calendar-day lag) before as-of joining")
    if family in ("sector", "universe_mapping"):
        return ("not_point_in_time_safe",
                "current-as-of snapshot (single as_of date in the future of most scoring dates); "
                "not point-in-time membership/classification")
    if family == "global_etf_cross_asset":
        if rows <= 0:
            return "unknown", "header-only / no data rows: global ETF panel not populated"
        return "point_in_time_safe", "daily ETF prices are same-day observable"
    if family in ("analyst_revisions", "news_sentiment"):
        return ("unknown",
                "inventory stub only: no real timestamped series present (no revision/article date + value)")
    return "unknown", "non-alpha research artifact (diagnostic output), not a predictive dataset"


def classify_leakage(family: str, pit: str, rec: Dict) -> str:
    if family in ("sector", "universe_mapping"):
        return "high"   # current-as-of => survivorship + look-ahead classification
    if family == "macro":
        return "medium"  # safe ONLY once a release/availability lag is applied
    if family == "earnings":
        return "medium"  # restatement + partial coverage + announcement-timing risk
    if family in ("price_history", "fundamentals"):
        return "low" if pit == "point_in_time_safe" else "medium"
    if family == "global_etf_cross_asset":
        return "low" if pit == "point_in_time_safe" else "unknown"
    if family in ("analyst_revisions", "news_sentiment"):
        return "unknown"
    return "unknown"


def classify_recommended_use(family: str, pit: str, leakage: str, rec: Dict) -> str:
    rows = rec["row_count"] or 0
    if family == "price_history":
        return "use_in_phase5e"           # baseline source (already used in 5-C)
    if family == "fundamentals":
        return "use_in_phase5e" if (rows > 0 and pit in ("point_in_time_safe",
                                                         "potentially_point_in_time")) else "inspect_manually"
    if family == "macro":
        return "use_in_phase5e" if rows > 0 else "future_collection_needed"
    if family == "earnings":
        return "use_in_phase5e" if rows > 0 else "future_collection_needed"
    if family == "global_etf_cross_asset":
        return "use_in_phase5e" if rows > 0 else "future_collection_needed"
    if family in ("sector", "universe_mapping"):
        return "inspect_manually"         # usable only with explicit survivorship caveat
    if family in ("analyst_revisions", "news_sentiment"):
        return "future_collection_needed"
    return "exclude_for_now"


# --------------------------------------------------------------------------- #
# Scan + build inventory
# --------------------------------------------------------------------------- #
# Filename tokens that POSITIVELY mark a genuine joinable DATA panel / source.
# Inclusion from research/output is allow-list only, so the hundreds of prior
# diagnostic artifacts (IC tables, decision tables, manifests, requirements,
# coverage reports) are scanned but never listed as candidate datasets.
_DATA_PANEL_TOKENS = (
    "price_panel", "feature_price_panel", "earnings_features_universe",
    "earnings_events_universe", "combined_fundamental_earnings_panel",
    "fundamentals_universe", "fundamentals_sample", "macro_feature_panel",
    "global_etf_price_panel", "normalized_global_etf_price_panel",
    "data_inventory", "sector_map", "universe_tickers",
)


def _gather_csv_paths() -> Tuple[List[Path], int]:
    """Return (candidate_paths, total_csv_seen).

    Inventory = every CSV under research/input/ (curated input alpha data) PLUS
    every CSV under research/output/ that is a genuine joinable DATA panel in a
    real alpha family (diagnostic summary/decision/IC artifacts are scanned but
    excluded). The read-only D: price file is added by the caller.
    """
    seen = 0
    paths: List[Path] = []
    # research/input — keep all CSVs (these are curated inputs).
    for root, _dirs, files in os.walk(_INPUT_DIR):
        for fn in sorted(files):
            if fn.lower().endswith(".csv"):
                seen += 1
                paths.append(Path(root) / fn)
    # research/output — keep only non-diagnostic, alpha-family DATA panels.
    for root, _dirs, files in os.walk(_OUT_DIR):
        for fn in sorted(files):
            if not fn.lower().endswith(".csv"):
                continue
            seen += 1
            full = Path(root) / fn
            rel = _rel(full)
            base = os.path.basename(rel).lower()
            # Positive allow-list: include only genuine joinable DATA panels /
            # source files, never the operational manifests/requirements/diagnostics.
            if not any(tok in base for tok in _DATA_PANEL_TOKENS):
                continue
            # Cheap header-only classification to confirm it is an alpha family.
            try:
                with open(full, "r", newline="", encoding="utf-8-sig") as fh:
                    header = next(csv.reader(fh), [])
            except Exception:
                header = []
            if classify_family(rel, header) != "other":
                paths.append(full)
    # Stable, deterministic order.
    paths = sorted(set(paths), key=lambda p: _rel(p))
    return paths, seen


def build_inventory() -> Tuple[List[Dict], int]:
    records: List[Dict] = []
    candidates, total_seen = _gather_csv_paths()

    price = _price_history_path()
    if price is not None:
        candidates = [price] + candidates

    for path in candidates:
        rec = _scan_csv(path)
        rel = _rel(path)
        # The D: price file lives outside the repo; label it honestly.
        if str(path) in _PRICE_HISTORY_CANDIDATES and "D:" in str(path).upper()[:3]:
            rel = str(path).replace("\\", "/") + " (D: read-only input)"
        family = classify_family(rel, rec["columns"])
        pit, blocker = classify_pit(family, rec)
        join = classify_joinability(family, rec)
        leakage = classify_leakage(family, pit, rec)
        use = classify_recommended_use(family, pit, leakage, rec)
        # A template/sample shell is a collection target, not a usable dataset.
        if "template" in rel.lower() and use == "use_in_phase5e":
            use = "future_collection_needed"
        records.append({
            "path": rel,
            "family": family,
            "file_type": "csv",
            "row_count": rec["row_count"],
            "columns": rec["columns"],
            "date_columns": rec["date_columns"],
            "ticker_columns": rec["ticker_columns"],
            "date_min": rec["date_min"],
            "date_max": rec["date_max"],
            "point_in_time_status": pit,
            "joinability": join,
            "leakage_risk": leakage,
            "recommended_use": use,
            "blocker": blocker or rec["error"],
        })
    return records, total_seen


# --------------------------------------------------------------------------- #
# Candidate feature families (Phase 5-E plan inputs)
# --------------------------------------------------------------------------- #
def build_feature_families(inv: List[Dict]) -> List[Dict]:
    fams = {r["family"] for r in inv}
    have_fund = any(r["family"] == "fundamentals" and (r["row_count"] or 0) > 0
                    and r["point_in_time_status"] == "point_in_time_safe" for r in inv)
    have_macro = any(r["family"] == "macro" and (r["row_count"] or 0) > 0 for r in inv)
    have_sector = "sector" in fams
    earnings_rows = max((r["row_count"] or 0) for r in inv if r["family"] == "earnings") if \
        any(r["family"] == "earnings" for r in inv) else 0
    # A real populated ETF price panel (NOT the 2-row template) is required.
    have_etf = any(r["family"] == "global_etf_cross_asset" and (r["row_count"] or 0) > 50
                   and "template" not in r["path"] for r in inv)

    rows: List[Dict] = [
        {
            "feature_family": "sec_fundamental_quality_value_growth",
            "example_features": "operating_margin; net_margin; revenue_yoy_growth; eps_diluted_yoy_growth; "
                                "accrual_proxy; debt_to_assets; log_total_assets; cash_conversion",
            "data_source_files": "research/output/phase3l_sec_universe_signal_gate/aligned_feature_price_panel_universe.csv",
            "point_in_time_basis": "active_feature_asof_date (SEC filing acceptance lag already applied); never the fiscal period end",
            "join_method": "ticker + date as-of join (latest filing available on/before scoring date)",
            "leakage_controls": "as-of merge on filing availability; staleness cap; per-date cross-sectional z-score; embargo>=20",
            "readiness": "ready_now" if have_fund else "blocked_missing_data",
            "phase5e_priority": 1,
        },
        {
            "feature_family": "macro_regime",
            "example_features": "cpi_yoy; inflation_regime; fed_funds_level; fed_policy_tightening_flag; "
                                "treasury_10y; yield_curve_10y_2y; yield_curve_inversion_flag; real_rate_proxy; "
                                "oil_return_63d; dollar_return_63d; macro_risk_off_flag",
            "data_source_files": "research/input/CPIAUCSL.csv; research/input/FEDFUNDS.csv; "
                                 "research/input/macro_treasury_yields.csv; research/input/DCOILWTICO.csv; "
                                 "research/input/DTWEXBGS.csv (registry: phase3r_macro_regime_walkforward/macro_feature_registry.csv)",
            "point_in_time_basis": "observation_date + release/availability lag (release date if present, else default 21-calendar-day lag for monthly series)",
            "join_method": "date-only as-of join (macro value is identical across tickers on a date; regime-conditioning, not cross-sectional)",
            "leakage_controls": "availability lag applied before join; monthly series never back-dated; daily series same-day",
            "readiness": "ready_now" if have_macro else "blocked_missing_data",
            "phase5e_priority": 2,
        },
        {
            "feature_family": "sector_relative",
            "example_features": "ticker_return_minus_sector_return; ticker_rank_within_sector; "
                                "sector_return_minus_spy_return; sector_relative_strength_rank",
            "data_source_files": "research/input/phase2k_p_sector_map_current.csv + the D: price history",
            "point_in_time_basis": "CAVEATED: sector map is current-as-of (NOT point-in-time membership); returns are trailing/point-in-time",
            "join_method": "static sector-map join (ticker -> sector) + same-date sector aggregates of trailing returns",
            "leakage_controls": "trailing returns only; flag survivorship + look-ahead-classification risk; report sector results as caveated",
            "readiness": "caveated" if have_sector else "blocked_missing_data",
            "phase5e_priority": 3,
        },
        {
            "feature_family": "earnings_surprise_event",
            "example_features": "eps_surprise_pct; trailing_4q_avg_surprise_pct; surprise_acceleration; "
                                "days_since_last_earnings; positive_surprise_rate",
            "data_source_files": "research/output/phase3m_earnings_estimates_signal_gate/earnings_features_universe.csv",
            "point_in_time_basis": "active_earnings_reported_date / provider availability_date; as-of merge of latest event on/before scoring date",
            "join_method": "ticker + date as-of join",
            "leakage_controls": "as-of on announcement availability; restatement caveat; coverage flag per ticker",
            "readiness": "partial" if earnings_rows > 0 else "blocked_missing_data",
            "phase5e_priority": 4,
        },
        {
            "feature_family": "cross_asset_global_etf_regime",
            "example_features": "global_equity_breadth; credit_vs_treasury_spread_proxy; commodity_trend; ex_us_vs_us_trend",
            "data_source_files": "research/output/phase3w_manual_global_etf_data_import/normalized_global_etf_price_panel.csv",
            "point_in_time_basis": "daily ETF prices are same-day observable (point-in-time once populated)",
            "join_method": "date-only as-of join (cross-asset regime context)",
            "leakage_controls": "trailing windows only; same-day prices; no forward fill across gaps",
            "readiness": "ready_now" if have_etf else "blocked_missing_data",
            "phase5e_priority": 5,
        },
        {
            "feature_family": "analyst_estimate_revisions",
            "example_features": "estimate_revision_breadth; up_minus_down_revisions_30d; target_price_change",
            "data_source_files": "(none present)",
            "point_in_time_basis": "requires revision date + estimate value (timestamped); none available locally",
            "join_method": "ticker + date as-of join (once data exists)",
            "leakage_controls": "n/a until data collected",
            "readiness": "blocked_missing_data",
            "phase5e_priority": 6,
        },
        {
            "feature_family": "news_sentiment",
            "example_features": "news_sentiment_avg_7d; sentiment_momentum; negative_news_intensity; event_count_7d",
            "data_source_files": "(none present)",
            "point_in_time_basis": "requires article timestamp + sentiment score; none available locally",
            "join_method": "ticker + date as-of join (once data exists)",
            "leakage_controls": "n/a until data collected",
            "readiness": "blocked_missing_data",
            "phase5e_priority": 7,
        },
        {
            "feature_family": "options_implied_volatility",
            "example_features": "iv_rank; iv_skew; put_call_ratio; iv_minus_realized_vol",
            "data_source_files": "(none present)",
            "point_in_time_basis": "requires dated option-chain/IV snapshots; none available locally",
            "join_method": "ticker + date as-of join (once data exists)",
            "leakage_controls": "n/a until data collected",
            "readiness": "blocked_missing_data",
            "phase5e_priority": 8,
        },
    ]
    return rows


def build_missing_sources(inv: List[Dict]) -> List[Dict]:
    return [
        {
            "missing_source": "analyst_estimate_revisions",
            "why_it_matters": "estimate-revision breadth/momentum is one of the strongest documented cross-sectional alpha families",
            "required_fields": "ticker; revision_date (timestamped); consensus_estimate; prior_estimate; up/down counts",
            "point_in_time_requirement": "must carry the actual revision/publication date for as-of joins",
            "current_status": "MISSING (Phase 3-S inventory stub only; no real series)",
            "collection_note": "needs a licensed/open estimates feed; do NOT fabricate",
        },
        {
            "missing_source": "news_sentiment",
            "why_it_matters": "timestamped news/sentiment can add short-horizon signal orthogonal to price/fundamentals",
            "required_fields": "ticker; article/publication timestamp; sentiment_score; event_type",
            "point_in_time_requirement": "must carry article timestamp; aggregate only over windows strictly <= scoring date",
            "current_status": "MISSING (Phase 3-S inventory stub only; no real series)",
            "collection_note": "needs a timestamped news/sentiment feed; do NOT fabricate",
        },
        {
            "missing_source": "options_implied_volatility",
            "why_it_matters": "IV level/skew and IV-minus-realized capture risk repricing not visible in spot prices",
            "required_fields": "ticker; date; implied_vol; skew; put_call_ratio",
            "point_in_time_requirement": "dated end-of-day snapshots only",
            "current_status": "MISSING (no local options data)",
            "collection_note": "needs an options/IV history source; do NOT fabricate",
        },
        {
            "missing_source": "point_in_time_index_constituents",
            "why_it_matters": "the local universe is full-history survivors; absolute-return evidence stays survivorship-biased without true membership history",
            "required_fields": "ticker; index; add_date; drop_date",
            "point_in_time_requirement": "membership must be evaluated as-of each scoring date (no future members, includes delisted names)",
            "current_status": "MISSING (only a current-as-of universe + sector map exist)",
            "collection_note": "needed before any absolute-return claim is trustworthy; do NOT fabricate",
        },
        {
            "missing_source": "earnings_estimates_full_coverage",
            "why_it_matters": "Phase 3-M earnings surprise data is only partial; full coverage is needed for a fair cross-sectional test",
            "required_fields": "ticker; reported_date; estimate; actual; surprise",
            "point_in_time_requirement": "announcement/availability date per event",
            "current_status": "PARTIAL (cached subset only; combined panel header-only)",
            "collection_note": "extend the existing Phase 3-M cache to the full universe; do NOT fabricate",
        },
        {
            "missing_source": "macro_release_calendar",
            "why_it_matters": "converts macro observation_date to the true release/availability date, tightening the point-in-time lag",
            "required_fields": "series_id; reference_period; release_datetime",
            "point_in_time_requirement": "exact release timestamps; absent them a conservative default lag is used",
            "current_status": "PARTIAL (default 21-calendar-day lag used for monthly series in Phase 3-R)",
            "collection_note": "a real release calendar would replace the default lag; optional improvement",
        },
    ]


# --------------------------------------------------------------------------- #
# Joinability matrix
# --------------------------------------------------------------------------- #
def build_joinability_matrix(inv: List[Dict]) -> List[Dict]:
    rows: List[Dict] = [
        {
            "source_family": "price_history",
            "source_files": "D: phase2k_g_expanded_price_history_free.csv (read-only)",
            "join_type": "ticker + date (native panel)",
            "join_keys": "ticker, date",
            "lookahead_risk": "low (trailing windows only)",
            "stale_data_risk": "low (daily)",
            "survivorship_risk": "high (full-history survivors only)",
            "missing_coverage_risk": "low",
            "notes": "the Phase 5-C base panel; external families join onto this ticker/date grid",
        },
        {
            "source_family": "fundamentals",
            "source_files": "phase3l aligned_feature_price_panel_universe.csv",
            "join_type": "ticker + date as-of join",
            "join_keys": "ticker, scoring_date (as-of active_feature_asof_date)",
            "lookahead_risk": "low (filing acceptance lag applied)",
            "stale_data_risk": "medium (quarterly cadence; staleness cap needed)",
            "survivorship_risk": "high (same survivor universe)",
            "missing_coverage_risk": "medium (pre-first-filing rows are blank)",
            "notes": "already carries forward labels; strongest ready-now external family",
        },
        {
            "source_family": "macro",
            "source_files": "research/input/{CPIAUCSL,FEDFUNDS,macro_treasury_yields,DCOILWTICO,DTWEXBGS}.csv",
            "join_type": "date-only as-of join",
            "join_keys": "scoring_date (as-of availability date = observation_date + release lag)",
            "lookahead_risk": "medium UNLESS a release/availability lag is applied",
            "stale_data_risk": "medium (monthly CPI); low (daily rates/fx/oil)",
            "survivorship_risk": "n/a (macro is not per-ticker)",
            "missing_coverage_risk": "low (long histories cover the panel)",
            "notes": "regime-conditioning; identical across tickers on a date (not cross-sectional alpha by itself)",
        },
        {
            "source_family": "sector",
            "source_files": "research/input/phase2k_p_sector_map_current.csv",
            "join_type": "static sector-map join (ticker -> sector)",
            "join_keys": "ticker",
            "lookahead_risk": "high (current-as-of classification applied to past dates)",
            "stale_data_risk": "high (single snapshot)",
            "survivorship_risk": "high",
            "missing_coverage_risk": "low",
            "notes": "use only for caveated sector-relative features; never as point-in-time membership",
        },
        {
            "source_family": "earnings",
            "source_files": "phase3m earnings_features_universe.csv",
            "join_type": "ticker + date as-of join",
            "join_keys": "ticker, scoring_date (as-of reported/availability date)",
            "lookahead_risk": "low-medium (as-of on announcement; restatement caveat)",
            "stale_data_risk": "medium (event-driven)",
            "survivorship_risk": "high",
            "missing_coverage_risk": "high (partial coverage only)",
            "notes": "include with an explicit coverage flag; do not impute missing tickers",
        },
        {
            "source_family": "global_etf_cross_asset",
            "source_files": "phase3w normalized_global_etf_price_panel.csv",
            "join_type": "date-only as-of join",
            "join_keys": "scoring_date",
            "lookahead_risk": "low (same-day prices) once populated",
            "stale_data_risk": "medium (depends on manual import freshness)",
            "survivorship_risk": "n/a (cross-asset)",
            "missing_coverage_risk": "high (panel currently header-only / unpopulated)",
            "notes": "blocked until the manual import is actually populated",
        },
    ]
    return rows


# --------------------------------------------------------------------------- #
# Phase 5-E implementation plan
# --------------------------------------------------------------------------- #
def build_phase5e_plan(inv: List[Dict], families: List[Dict]) -> Dict:
    ready = [f for f in families if f["readiness"] in ("ready_now", "partial", "caveated")]
    return {
        "phase": "5-E",
        "title": "Enriched Alpha Feature Panel and Model Rerun",
        "objective": ("Build a point-in-time ENRICHED feature panel (Phase 5-C price-only features "
                      "+ point-in-time external families) on the same survivor universe and rebalance "
                      "schedule, re-run the same walk-forward models, and measure whether the external "
                      "data produces a real, leakage-free INCREMENTAL edge over the Phase 5-C price-only "
                      "baseline. This is an incremental-edge test, not a foregone win: the prior Phase 3-P "
                      "multisignal attempt did NOT beat price-only at the headline model."),
        "primary_label": PRIMARY_LABEL,
        "feature_families_first": [
            {
                "feature_family": f["feature_family"],
                "priority": f["phase5e_priority"],
                "source_files": f["data_source_files"],
                "join_method": f["join_method"],
                "leakage_controls": f["leakage_controls"],
                "readiness": f["readiness"],
            }
            for f in sorted(ready, key=lambda x: x["phase5e_priority"])
        ],
        "exact_files_to_read": [
            "D: phase2k_g_expanded_price_history_free.csv (read-only) — base price panel + SPY benchmark/regime",
            "research/output/phase3l_sec_universe_signal_gate/aligned_feature_price_panel_universe.csv — fundamentals (PIT)",
            "research/input/CPIAUCSL.csv, research/input/FEDFUNDS.csv, research/input/macro_treasury_yields.csv, "
            "research/input/DCOILWTICO.csv, research/input/DTWEXBGS.csv — macro (PIT w/ release lag)",
            "research/output/phase3r_macro_regime_walkforward/macro_feature_registry.csv — macro PIT rules to reuse",
            "research/input/phase2k_p_sector_map_current.csv — sector map (CAVEATED, not PIT)",
            "research/output/phase3m_earnings_estimates_signal_gate/earnings_features_universe.csv — earnings (partial)",
            _BASELINE_SCOREBOARD + " — Phase 5-C price-only baseline metrics to beat",
        ],
        "join_methods": {
            "fundamentals": "ticker+date as-of merge on active_feature_asof_date (latest filing <= scoring date), staleness-capped",
            "earnings": "ticker+date as-of merge on reported/availability date, with per-ticker coverage flag",
            "macro": "date-only as-of merge on availability date (observation_date + release/default lag)",
            "sector": "static ticker->sector map join, used only for caveated sector-relative features",
            "global_etf_cross_asset": "date-only as-of merge (only if the manual panel is populated)",
        },
        "leakage_controls": [
            "all external joins are as-of (value must be available strictly on/before the scoring date)",
            "macro availability lag applied before joining (no back-dated macro observations)",
            "fundamentals use filing acceptance date, never fiscal-period end; staleness cap on feature age",
            "per-date cross-sectional standardization (contemporaneous features only; no labels)",
            "walk-forward yearly folds, train-on-past/test-on-future, embargo >= 20 sessions",
            "computed placebo: within-date label shuffle must collapse mean IC to ~0",
            "survivorship: absolute-return claims gated until point-in-time constituents exist; rank IC reported as primary",
        ],
        "expected_outputs": [
            "phase5e_enriched_feature_panel_sample.csv",
            "phase5e_feature_family_ic.csv (per-family incremental rank IC)",
            "phase5e_model_scoreboard.csv (price_only vs enriched, same folds)",
            "phase5e_incremental_edge.csv (enriched minus baseline IC, by horizon and year)",
            "phase5e_validation_gate_matrix.csv",
            "phase5e_enriched_alpha.json (report + recommendation)",
        ],
        "baseline_comparison": {
            "baseline_name": "Phase 5-C price-only (cross_sectional_composite_zscore + ridge_cross_sectional_return_model)",
            "baseline_metrics_source": _BASELINE_SCOREBOARD,
            "method": ("hold the universe, rebalance dates, walk-forward folds, embargo, and models IDENTICAL to "
                       "Phase 5-C; the ONLY change is appending point-in-time external features. Re-run and compare."),
            "comparison_metrics": ["mean_rank_ic", "ic_t_stat", "decile_spread", "worst_year_ic",
                                   "top20_net_return_after_cost", "turnover"],
            "improvement_metric": "delta_mean_rank_ic = enriched_mean_rank_ic - price_only_mean_rank_ic",
        },
        "go_no_go_gates": [
            {"gate": "enriched_beats_baseline_ic",
             "pass_condition": "delta_mean_rank_ic >= +0.01 on the primary horizon AND enriched worst_year_ic not worse than baseline"},
            {"gate": "incremental_edge_is_leakage_clean",
             "pass_condition": "placebo mean IC ~0, embargo >= 20, all external joins are as-of"},
            {"gate": "coverage_sufficient",
             "pass_condition": "fundamentals cover >= 80% of panel rows; earnings/etf families flagged if partial, not imputed"},
            {"gate": "survivorship_addressed_or_flagged",
             "pass_condition": "absolute-return claims withheld until PIT constituents exist; rank IC is the headline metric"},
            {"gate": "no_fabricated_data",
             "pass_condition": "no synthetic fundamentals/earnings/macro/sentiment values introduced"},
            {"gate": "safety_contract",
             "pass_condition": "preview_only true; orders/broker/automation false; no deploy; no D: writes; no binary artifacts"},
        ],
        "decision_rule": ("If enriched_beats_baseline_ic AND incremental_edge_is_leakage_clean PASS -> proceed toward a "
                          "deployable enriched scorer (Phase 5-F). If external data does NOT beat price-only, report that "
                          "honestly and prioritize collecting the MISSING families (analyst revisions, sentiment, "
                          "options/IV, point-in-time constituents) before any deployment."),
        "preview_only": True,
        "orders_enabled": False,
        "automation_enabled": False,
        "broker_execution_enabled": False,
        "production_replacement": False,
    }


# --------------------------------------------------------------------------- #
# Recommendation
# --------------------------------------------------------------------------- #
def derive_recommendation(inv: List[Dict]) -> Tuple[str, str]:
    usable = [r for r in inv if r["recommended_use"] == "use_in_phase5e"]
    pit_safe = [r for r in inv if r["point_in_time_status"] == "point_in_time_safe"]
    has_fund = any(r["family"] == "fundamentals" and r["recommended_use"] == "use_in_phase5e" for r in inv)
    has_macro = any(r["family"] == "macro" and r["recommended_use"] == "use_in_phase5e" for r in inv)
    # Need at least one PIT-safe, ticker-joinable external family (beyond price) to enrich.
    has_external_ticker_alpha = any(
        r["family"] in ("fundamentals", "earnings") and r["recommended_use"] == "use_in_phase5e"
        and r["joinability"] == "ticker_date_joinable" for r in inv)

    if has_external_ticker_alpha and (has_fund or has_macro):
        return ("PROCEED_TO_ENRICHED_ALPHA_PANEL",
                "Point-in-time external data is available now (SEC fundamentals are PIT-safe and "
                "ticker/date-joinable with forward labels; macro regime series are PIT-safe with a release lag). "
                "Build the Phase 5-E enriched panel and test incremental edge vs the Phase 5-C price-only baseline. "
                "Caveat: the prior Phase 3-P multisignal model did not beat price-only at the headline, and analyst "
                "revisions / news sentiment / options-IV / point-in-time constituents are still MISSING.")
    if usable or pit_safe:
        return ("PROCEED_TO_ENRICHED_ALPHA_PANEL",
                "Some usable point-in-time data exists; proceed to a careful enriched-panel test.")
    return ("PROCEED_TO_EXTERNAL_DATA_COLLECTION",
            "No point-in-time, joinable external alpha data is usable yet; collect the missing families first.")


# --------------------------------------------------------------------------- #
# Writers
# --------------------------------------------------------------------------- #
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
# Main
# --------------------------------------------------------------------------- #
def main() -> int:
    inv, total_seen = build_inventory()
    families = build_feature_families(inv)
    missing = build_missing_sources(inv)
    joinability = build_joinability_matrix(inv)
    plan = build_phase5e_plan(inv, families)
    recommendation, rec_reason = derive_recommendation(inv)

    # --- inventory CSV ---
    _write_csv(_INVENTORY_OUT,
               ["path", "family", "file_type", "row_count", "columns", "date_columns",
                "ticker_columns", "date_min", "date_max", "point_in_time_status",
                "joinability", "leakage_risk", "recommended_use", "blocker"],
               [[r["path"], r["family"], r["file_type"],
                 "" if r["row_count"] is None else r["row_count"],
                 ";".join(r["columns"]), ";".join(r["date_columns"]),
                 ";".join(r["ticker_columns"]), r["date_min"], r["date_max"],
                 r["point_in_time_status"], r["joinability"], r["leakage_risk"],
                 r["recommended_use"], r["blocker"]] for r in inv])

    # --- point-in-time readiness CSV ---
    _write_csv(_PIT_READINESS_OUT,
               ["path", "family", "point_in_time_status", "date_columns",
                "ticker_columns", "joinability", "leakage_risk", "row_count",
                "recommended_use", "blocker"],
               [[r["path"], r["family"], r["point_in_time_status"],
                 ";".join(r["date_columns"]), ";".join(r["ticker_columns"]),
                 r["joinability"], r["leakage_risk"],
                 "" if r["row_count"] is None else r["row_count"],
                 r["recommended_use"], r["blocker"]] for r in inv])

    # --- candidate feature families CSV ---
    _write_csv(_FEATURE_FAMILIES_OUT,
               ["feature_family", "example_features", "data_source_files",
                "point_in_time_basis", "join_method", "leakage_controls",
                "readiness", "phase5e_priority"],
               [[f["feature_family"], f["example_features"], f["data_source_files"],
                 f["point_in_time_basis"], f["join_method"], f["leakage_controls"],
                 f["readiness"], f["phase5e_priority"]] for f in families])

    # --- joinability matrix CSV ---
    _write_csv(_JOINABILITY_OUT,
               ["source_family", "source_files", "join_type", "join_keys",
                "lookahead_risk", "stale_data_risk", "survivorship_risk",
                "missing_coverage_risk", "notes"],
               [[j["source_family"], j["source_files"], j["join_type"], j["join_keys"],
                 j["lookahead_risk"], j["stale_data_risk"], j["survivorship_risk"],
                 j["missing_coverage_risk"], j["notes"]] for j in joinability])

    # --- missing alpha sources CSV ---
    _write_csv(_MISSING_OUT,
               ["missing_source", "why_it_matters", "required_fields",
                "point_in_time_requirement", "current_status", "collection_note"],
               [[m["missing_source"], m["why_it_matters"], m["required_fields"],
                 m["point_in_time_requirement"], m["current_status"],
                 m["collection_note"]] for m in missing])

    # --- Phase 5-E plan JSON ---
    _write_json(_PLAN_OUT, plan)

    # --- counts (data-driven) ---
    discovered = len(inv)
    usable = [r for r in inv if r["recommended_use"] == "use_in_phase5e"]
    pit_safe = [r for r in inv if r["point_in_time_status"] == "point_in_time_safe"]
    high_leak = [r for r in inv if r["leakage_risk"] == "high"]
    recommended_phase5e_sources = sorted(
        {f"{r['family']}: {r['path']}" for r in usable})
    best_families = [f["feature_family"] for f in families
                     if f["readiness"] in ("ready_now", "partial", "caveated")]
    missing_critical = [m["missing_source"] for m in missing
                        if m["current_status"].startswith("MISSING")]

    report = {
        "phase": "5-D",
        "objective": ("Inventory local non-price datasets, determine point-in-time readiness and "
                      "joinability to the Phase 5-C ticker/date panel, and plan the Phase 5-E enriched "
                      "alpha feature panel — without fabricating any data."),
        "context": ("Phase 5-C showed the price-only cross-sectional model is not strong enough to deploy "
                    "(baseline rank IC ~0; fitted models only modestly above noise; survivorship-inflated "
                    "returns). The next edge must come from external alpha data."),
        "files_scanned_total_csv": total_seen,
        "datasets_inventoried": discovered,
        "discovered_sources_count": discovered,
        "usable_sources_count": len(usable),
        "point_in_time_safe_sources_count": len(pit_safe),
        "high_leakage_risk_sources_count": len(high_leak),
        "recommended_phase5e_sources": recommended_phase5e_sources,
        "missing_critical_sources": missing_critical,
        "best_available_alpha_families": best_families,
        "family_breakdown": {fam: sum(1 for r in inv if r["family"] == fam)
                             for fam in FAMILIES if any(r["family"] == fam for r in inv)},
        "primary_label": PRIMARY_LABEL,
        "baseline_to_beat": _BASELINE_SCOREBOARD,
        "prior_multisignal_caveat": ("Phase 3-P multisignal best model was ridge_technical_only @126d "
                                     "(worst-year IC -0.108, FAIL) — adding fundamentals/earnings did not "
                                     "beat price-only at the headline; Phase 5-E must prove incremental edge."),
        "recommendation": recommendation,
        "recommendation_reason": rec_reason,
        "recommended_next_phase": ("Phase 5-E: Enriched Alpha Feature Panel and Model Rerun "
                                   "(fundamentals + macro first; sector caveated; earnings partial), "
                                   "with strict incremental-edge gates vs the Phase 5-C price-only baseline."),
        "outputs": {
            "report": _rel(_REPORT_OUT),
            "inventory": _rel(_INVENTORY_OUT),
            "point_in_time_readiness": _rel(_PIT_READINESS_OUT),
            "candidate_feature_families": _rel(_FEATURE_FAMILIES_OUT),
            "joinability_matrix": _rel(_JOINABILITY_OUT),
            "missing_alpha_sources": _rel(_MISSING_OUT),
            "phase5e_implementation_plan": _rel(_PLAN_OUT),
        },
        # Safety / provenance contract (explicit, like Phase 5-A/5-B/5-C).
        "preview_only": True,
        "orders_enabled": False,
        "automation_enabled": False,
        "broker_execution_enabled": False,
        "production_replacement": False,
        "network_used": False,
        "paid_apis_used": False,
        "deployed": False,
        "binary_artifacts_created": False,
        "data_fabricated": False,
        "wrote_to_d_drive": False,
        "committed": False,
    }
    _write_json(_REPORT_OUT, report)

    print(f"[phase5d] inventoried {discovered} dataset(s) from {total_seen} CSV(s) scanned; "
          f"usable={len(usable)}, pit_safe={len(pit_safe)}, high_leakage={len(high_leak)}; "
          f"recommendation={recommendation}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
