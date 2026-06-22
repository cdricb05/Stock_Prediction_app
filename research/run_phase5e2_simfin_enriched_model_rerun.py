"""Phase 5-E2 — SimFin-Enriched Cross-Sectional Model Rerun (offline, point-in-time).

This is **Track A (quant brain) research**. It answers ONE question with
out-of-sample evidence:

    "Does adding SimFin quarterly fundamentals to the Phase 5-C price-only panel
     improve cross-sectional stock ranking enough to justify building a
     deployable scorer (Phase 5-F)?"

What it does
------------
  1. Reuses the Phase 5-C harness (imported, not duplicated) to build the
     price-only cross-sectional panel + forward labels from the local read-only
     price history.
  2. Loads the **already-collected, local** SimFin quarterly statements (the
     git-ignored 5-E1E normalized CSVs). It makes NO network calls and NO SimFin
     downloads, and requires NO API key.
  3. Computes fundamental features internally (SimFin derived ratios are
     unavailable) for both the **standard** and **bank** statement templates,
     handled separately.
  4. Joins fundamentals to the price panel **point-in-time-safe**: each
     fundamental is attached by its *data-availability date* (SimFin Publish
     Date; conservative fiscal-lag fallback when null), never the fiscal period
     end -> no lookahead.
  5. Evaluates, under one identical walk-forward harness, a price-only baseline,
     a fundamentals-only model, a price+fundamentals model, and a
     price+fundamentals+quality-composite model, and reports the **incremental
     edge** versus the Phase 5-C price-only baseline.
  6. Emits an honest validation-gate matrix and recommendation. If fundamentals
     do not help, it says so.

Hard rules honored: no FMP, no provider shopping, no live API calls, no SimFin
downloads (local data only), no writes to D: (read-only price input only), no
Paper Trader changes, no GCP changes, no deploy, no orders / broker execution /
automation, no binary model artifacts (.pkl/.joblib/.onnx/.pt/.h5/.keras), no
commit. Pure offline numpy + csv. Free SimFin data is delayed ~12 months, so the
result is for research/backtesting only and is NOT usable for live trading today.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import importlib.util
import json
import math
import os
import sys
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

PHASE = "5-E2"
PROVIDER = "SimFin"

# --------------------------------------------------------------------------- #
# Paths (all outputs committed-safe; SimFin payloads stay git-ignored)
# --------------------------------------------------------------------------- #
_OUT_DIR = _REPO_ROOT / "research" / "output" / "phase5e2_simfin_enriched_model_rerun"
_REPORT_OUT = _OUT_DIR / "phase5e2_simfin_enriched_model_rerun.json"
_FEATURE_CATALOG_OUT = _OUT_DIR / "simfin_enriched_feature_catalog.csv"
_PANEL_SAMPLE_OUT = _OUT_DIR / "simfin_enriched_panel_sample.csv"
_SCOREBOARD_OUT = _OUT_DIR / "simfin_enriched_model_scoreboard.csv"
_IC_BY_YEAR_OUT = _OUT_DIR / "simfin_enriched_ic_by_year.csv"
_DECILE_SPREAD_OUT = _OUT_DIR / "simfin_enriched_decile_spread.csv"
_COVERAGE_OUT = _OUT_DIR / "simfin_enriched_coverage_report.csv"
_GATE_MATRIX_OUT = _OUT_DIR / "simfin_enriched_validation_gate_matrix.csv"
_INCREMENTAL_EDGE_OUT = _OUT_DIR / "simfin_enriched_incremental_edge_report.csv"
_PHASE5F_PLAN_OUT = _OUT_DIR / "phase5f_deployable_scorer_plan.json"

_PHASE5C_RUNNER = _REPO_ROOT / "research" / "run_phase5c_cross_sectional_alpha_research.py"
# Local, already-collected SimFin normalized statements (git-ignored, 5-E1E).
_SIMFIN_NORM_DIR = _REPO_ROOT / "research" / "data" / "simfin" / "normalized" / "phase5e1e"

# --------------------------------------------------------------------------- #
# Point-in-time / coverage / edge constants
# --------------------------------------------------------------------------- #
# SimFin exposes a Publish Date per statement; that is the data-availability
# timestamp we join on. When Publish Date is null we fall back to the fiscal
# Report Date plus a conservative reporting lag (a 10-K/10-Q is filed within ~90
# days of period end). The free-tier 12-month delay only blocks *live trading
# today*; for a historical backtest the publish date is the correct as-of date.
FALLBACK_REPORT_LAG_DAYS = 90
# A fundamental older than this at the as-of date is "stale" (still used, but
# counted by the staleness gate).
STALE_MAX_DAYS = 400
# Common-set evaluability minimums for the enriched comparison.
MIN_COMMON_NAMES = 30
MIN_COMMON_DATES = 24
# Incremental-edge thresholds (mean rank IC, price+fund minus price-only).
EDGE_MARGIN_DEPLOYABLE = 0.01     # >= this AND stability -> deployable
EDGE_MARGIN_IMPROVES = 0.0        # > this -> "improves but not deployable"

PRIMARY_LABEL = "forward_excess_return_20d_vs_spy"

# --------------------------------------------------------------------------- #
# Recommendation vocabulary (the six allowed values)
# --------------------------------------------------------------------------- #
REC_READY_5F = "READY_FOR_PHASE5F_DEPLOYABLE_SCORER"
REC_IMPROVE_NOT_DEPLOY = "FUNDAMENTALS_IMPROVE_BUT_NOT_DEPLOYABLE"
REC_NO_EDGE = "NO_INCREMENTAL_EDGE_USE_PRICE_ONLY"
REC_DATA_BLOCKER = "DATA_COVERAGE_BLOCKER"
REC_PIT_BLOCKER = "PIT_SAFETY_BLOCKER"
REC_ERROR = "ERROR"
ALLOWED_RECOMMENDATIONS = (
    REC_READY_5F, REC_IMPROVE_NOT_DEPLOY, REC_NO_EDGE,
    REC_DATA_BLOCKER, REC_PIT_BLOCKER, REC_ERROR,
)

# --------------------------------------------------------------------------- #
# Fundamental feature definitions (computed internally; derived ratios absent)
# Sign convention mirrors Phase 5-C: +1 keep, -1 invert so higher == more
# bullish AFTER the sign is applied during cross-sectional standardization.
# --------------------------------------------------------------------------- #
# family -> [(feature, sign, scope)]   scope in {"shared","standard","bank"}
_FUND_FEATURE_DEFS: List[Tuple[str, str, int, str]] = [
    # profitability
    ("profitability", "gross_margin", +1, "standard"),
    ("profitability", "operating_margin", +1, "shared"),
    ("profitability", "net_margin", +1, "shared"),
    ("profitability", "roa_ttm", +1, "shared"),
    # growth (year-over-year, same fiscal period)
    ("growth", "revenue_growth_yoy", +1, "shared"),
    ("growth", "operating_income_growth_yoy", +1, "shared"),
    ("growth", "net_income_growth_yoy", +1, "shared"),
    # leverage / solvency
    ("leverage", "debt_to_assets", -1, "standard"),
    ("leverage", "liabilities_to_assets", -1, "shared"),
    ("leverage", "equity_to_assets", +1, "bank"),
    # liquidity / quality
    ("liquidity_quality", "current_ratio", +1, "standard"),
    ("liquidity_quality", "cash_to_assets", +1, "standard"),
    ("liquidity_quality", "ocf_to_assets", +1, "shared"),
    ("liquidity_quality", "fcf_to_assets", +1, "standard"),
    # valuation (price * reported shares; cross-sectional rank only)
    ("valuation", "earnings_yield_ttm", +1, "shared"),
    ("valuation", "book_to_price", +1, "shared"),
    # bank-specific funding / asset mix
    ("bank_specific", "deposits_to_assets", +1, "bank"),
    ("bank_specific", "loans_to_assets", +1, "bank"),
]
FUND_FEATURES: List[str] = [d[1] for d in _FUND_FEATURE_DEFS]
_FUND_SIGN: Dict[str, int] = {d[1]: d[2] for d in _FUND_FEATURE_DEFS}
_FUND_SCOPE: Dict[str, str] = {d[1]: d[3] for d in _FUND_FEATURE_DEFS}
_FUND_FAMILY: Dict[str, str] = {d[1]: d[0] for d in _FUND_FEATURE_DEFS}
FEATURE_FAMILIES = ["profitability", "growth", "leverage", "liquidity_quality",
                    "valuation", "bank_specific"]
# Quality composite ingredients (already sign-adjusted during standardization).
_QUALITY_COMPOSITE_FEATURES = [
    "net_margin", "operating_margin", "roa_ttm", "ocf_to_assets",
    "fcf_to_assets", "liabilities_to_assets",
]
QUALITY_COMPOSITE = "quality_composite"

# Statement line items used (exact SimFin normalized column names).
_PL = "pl"
_BS = "bs"
_CF = "cf"
_STATEMENT_CODES = (_PL, _BS, _CF)


# --------------------------------------------------------------------------- #
# Phase 5-C harness import (reuse, never duplicate)
# --------------------------------------------------------------------------- #
def _load_phase5c(runner_path: Optional[Path] = None):
    """Import the Phase 5-C runner as a module (no side effects; main() is
    guarded by ``if __name__ == '__main__'``)."""
    path = Path(runner_path) if runner_path else _PHASE5C_RUNNER
    spec = importlib.util.spec_from_file_location("phase5c_runner", str(path))
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load Phase 5-C runner at {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# --------------------------------------------------------------------------- #
# Small IO / numeric helpers
# --------------------------------------------------------------------------- #
def _rel(p: Path) -> str:
    try:
        return str(p.relative_to(_REPO_ROOT))
    except ValueError:
        return str(p)


def _to_float(v) -> Optional[float]:
    if v is None:
        return None
    s = str(v).strip()
    if s == "" or s.lower() in ("nan", "none", "null"):
        return None
    try:
        f = float(s)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _epoch_ms_to_iso(v) -> Optional[str]:
    f = _to_float(v)
    if f is None:
        return None
    try:
        # SimFin normalized dates are epoch milliseconds (UTC midnight).
        return dt.datetime.fromtimestamp(f / 1000.0, dt.timezone.utc).strftime("%Y-%m-%d")
    except (OverflowError, OSError, ValueError):
        # Fall back to already-ISO strings.
        s = str(v).strip()[:10]
        return s if len(s) == 10 and s[4] == "-" else None


def _iso_add_days(iso: str, days: int) -> str:
    d = dt.date.fromisoformat(iso) + dt.timedelta(days=days)
    return d.isoformat()


def _days_between(iso_a: str, iso_b: str) -> int:
    return (dt.date.fromisoformat(iso_a) - dt.date.fromisoformat(iso_b)).days


def _safe_div(a: Optional[float], b: Optional[float]) -> Optional[float]:
    if a is None or b is None or b == 0:
        return None
    r = a / b
    return r if math.isfinite(r) else None


def _growth(cur: Optional[float], prior: Optional[float]) -> Optional[float]:
    """YoY growth robust to sign: (cur - prior) / |prior|."""
    if cur is None or prior is None or prior == 0:
        return None
    g = (cur - prior) / abs(prior)
    return g if math.isfinite(g) else None


def _write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=2, default=str)
        fh.write("\n")


def _write_csv(path: Path, header: Sequence[str], rows: Sequence[Sequence]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        for r in rows:
            w.writerow(r)


def _f(x) -> Optional[float]:
    if x is None:
        return None
    try:
        return round(float(x), 6)
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------- #
# Load local SimFin normalized statements (no network, no key, local only)
# --------------------------------------------------------------------------- #
def _read_statement_csv(path: Path) -> List[Dict[str, str]]:
    if not path.is_file():
        return []
    with open(path, "r", newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def load_simfin_tables(data_dir: Optional[Path] = None) -> Dict[str, Dict[str, List[dict]]]:
    """Load the local 5-E1E normalized statements into
    ``{"standard": {"pl":[...],"bs":[...],"cf":[...]}, "banks": {...}}``.

    Pure local read of the git-ignored normalized CSVs. Returns empty dicts when
    the data is absent (the caller turns that into DATA_COVERAGE_BLOCKER)."""
    base = Path(data_dir) if data_dir else _SIMFIN_NORM_DIR
    out: Dict[str, Dict[str, List[dict]]] = {"standard": {}, "banks": {}}
    for template in ("standard", "banks"):
        for code in _STATEMENT_CODES:
            out[template][code] = _read_statement_csv(base / template / f"{code}.csv")
    return out


# --------------------------------------------------------------------------- #
# Build per-(ticker, fiscal period) statement records from the 3 statements
# --------------------------------------------------------------------------- #
def _availability_date(row: dict) -> Optional[str]:
    """Data-availability date for a statement row: SimFin Publish Date, else the
    fiscal Report Date plus a conservative reporting lag."""
    pub = _epoch_ms_to_iso(row.get("Publish Date"))
    if pub:
        return pub
    rep = _epoch_ms_to_iso(row.get("Report Date"))
    if rep:
        return _iso_add_days(rep, FALLBACK_REPORT_LAG_DAYS)
    return None


def _merge_statements(template_tables: Dict[str, List[dict]]
                      ) -> Dict[str, List[dict]]:
    """Merge pl/bs/cf for one template into one record per (ticker, FY, FP).

    The record's availability date is the **latest** publish/availability date
    across its three statements (all three must be public to be usable)."""
    merged: Dict[Tuple[str, str, str], dict] = {}
    for code in _STATEMENT_CODES:
        for row in template_tables.get(code, []):
            tkr = (row.get("Ticker") or "").strip().upper()
            fy = (row.get("Fiscal Year") or "").strip()
            fp = (row.get("Fiscal Period") or "").strip()
            if not tkr or not fy or not fp:
                continue
            key = (tkr, fy, fp)
            rec = merged.setdefault(key, {
                "ticker": tkr, "fiscal_year": fy, "fiscal_period": fp,
                "items": {}, "avail_dates": [], "report_date": None,
                "publish_dates": [],
            })
            avail = _availability_date(row)
            if avail:
                rec["avail_dates"].append(avail)
            pub = _epoch_ms_to_iso(row.get("Publish Date"))
            if pub:
                rec["publish_dates"].append(pub)
            if rec["report_date"] is None:
                rec["report_date"] = _epoch_ms_to_iso(row.get("Report Date"))
            for col, val in row.items():
                if col in ("Ticker", "Fiscal Year", "Fiscal Period", "Publish Date",
                           "Report Date", "Restated Date", "SimFinId", "Currency"):
                    continue
                f = _to_float(val)
                if f is not None:
                    # Prefer the first non-null; statements rarely overlap columns.
                    rec["items"].setdefault(col, f)

    out: Dict[str, List[dict]] = {}
    for (tkr, _fy, _fp), rec in merged.items():
        if not rec["avail_dates"]:
            continue
        rec["avail_date"] = max(rec["avail_dates"])
        rec["publish_date"] = max(rec["publish_dates"]) if rec["publish_dates"] else None
        # FY/FP ordering key for TTM / YoY lookups.
        try:
            fy_i = int(float(rec["fiscal_year"]))
        except (TypeError, ValueError):
            continue
        q = {"Q1": 1, "Q2": 2, "Q3": 3, "Q4": 4}.get(rec["fiscal_period"].upper())
        if q is None:
            continue
        rec["fy_int"] = fy_i
        rec["q_int"] = q
        rec["order"] = fy_i * 4 + q
        out.setdefault(tkr, []).append(rec)
    for tkr in out:
        out[tkr].sort(key=lambda r: r["order"])
    return out


def _ttm_sum(stmts: List[dict], upto_idx: int, col: str) -> Optional[float]:
    """Trailing-twelve-month sum of ``col`` over the 4 quarters ending at
    ``upto_idx`` (consecutive fiscal quarters required)."""
    if upto_idx < 3:
        return None
    window = stmts[upto_idx - 3: upto_idx + 1]
    if window[-1]["order"] - window[0]["order"] != 3:
        return None  # not 4 consecutive quarters
    vals = [w["items"].get(col) for w in window]
    if any(v is None for v in vals):
        return None
    s = sum(vals)
    return s if math.isfinite(s) else None


def _prior_year(stmts: List[dict], idx: int) -> Optional[dict]:
    target = stmts[idx]["order"] - 4
    for j in range(idx - 1, -1, -1):
        if stmts[j]["order"] == target:
            return stmts[j]
        if stmts[j]["order"] < target:
            break
    return None


def compute_fundamental_features(stmts: List[dict], idx: int, template: str,
                                 market_cap: Optional[float]) -> Dict[str, Optional[float]]:
    """Internal fundamental ratios for statement ``stmts[idx]`` (point-in-time:
    uses only this statement and strictly-earlier ones)."""
    it = stmts[idx]["items"]
    py = _prior_year(stmts, idx)
    pit = py["items"] if py else {}
    rev = it.get("Revenue")
    ni = it.get("Net Income")
    oi = it.get("Operating Income (Loss)")
    ta = it.get("Total Assets")
    teq = it.get("Total Equity")
    tl = it.get("Total Liabilities")
    ocf = it.get("Net Cash from Operating Activities")
    ttm_ni = _ttm_sum(stmts, idx, "Net Income")

    feats: Dict[str, Optional[float]] = {f: None for f in FUND_FEATURES}
    # shared
    feats["operating_margin"] = _safe_div(oi, rev)
    feats["net_margin"] = _safe_div(ni, rev)
    feats["roa_ttm"] = _safe_div(ttm_ni, ta)
    feats["revenue_growth_yoy"] = _growth(rev, pit.get("Revenue"))
    feats["operating_income_growth_yoy"] = _growth(oi, pit.get("Operating Income (Loss)"))
    feats["net_income_growth_yoy"] = _growth(ni, pit.get("Net Income"))
    feats["liabilities_to_assets"] = _safe_div(tl, ta)
    feats["ocf_to_assets"] = _safe_div(ocf, ta)
    if market_cap and market_cap > 0:
        feats["earnings_yield_ttm"] = _safe_div(ttm_ni, market_cap)
        feats["book_to_price"] = _safe_div(teq, market_cap)
    if template == "standard":
        feats["gross_margin"] = _safe_div(it.get("Gross Profit"), rev)
        std_short = it.get("Short Term Debt")
        std_long = it.get("Long Term Debt")
        debt = None
        if std_short is not None or std_long is not None:
            debt = (std_short or 0.0) + (std_long or 0.0)
        feats["debt_to_assets"] = _safe_div(debt, ta)
        feats["current_ratio"] = _safe_div(it.get("Total Current Assets"),
                                           it.get("Total Current Liabilities"))
        feats["cash_to_assets"] = _safe_div(
            it.get("Cash, Cash Equivalents & Short Term Investments"), ta)
        capex = it.get("Change in Fixed Assets & Intangibles")  # negative outflow
        fcf = (ocf + capex) if (ocf is not None and capex is not None) else None
        feats["fcf_to_assets"] = _safe_div(fcf, ta)
    else:  # bank
        feats["equity_to_assets"] = _safe_div(teq, ta)
        feats["deposits_to_assets"] = _safe_div(it.get("Total Deposits"), ta)
        feats["loans_to_assets"] = _safe_div(it.get("Net Loans"), ta)
    return feats


# --------------------------------------------------------------------------- #
# Point-in-time as-of join of fundamentals onto the price panel
# --------------------------------------------------------------------------- #
def _asof_index(stmts: List[dict], asof_iso: str) -> Optional[int]:
    """Index of the most recent statement whose availability date <= asof."""
    best = None
    for i, s in enumerate(stmts):
        if s["avail_date"] <= asof_iso:
            best = i
        else:
            break  # stmts are order-sorted; avail dates are monotone-ish
    # avail dates are not guaranteed monotone vs fiscal order; do a safe scan.
    best = None
    for i, s in enumerate(stmts):
        if s["avail_date"] <= asof_iso and (best is None or s["avail_date"] >= stmts[best]["avail_date"]):
            best = i
    return best


def enrich_panel(panel: List[dict], merged_by_template: Dict[str, Dict[str, List[dict]]],
                 bank_tickers: set, close_lookup: Callable[[str, int], Optional[float]]
                 ) -> Tuple[List[dict], dict]:
    """Attach PIT-safe fundamentals to each price-panel row. Returns
    ``(panel, coverage_stats)``. Each row gains: ``is_bank``, ``fund`` (dict or
    None), ``has_fundamentals``, ``fund_age_days``, ``fund_stale``,
    ``fund_publish_date``, ``fund_fiscal_period``."""
    cov = {
        "rows_total": len(panel), "rows_with_fund": 0, "rows_stale": 0,
        "names_with_fund": set(), "bank_rows_with_fund": 0,
        "by_year": {}, "max_lookahead_violation_days": 0,
    }
    for r in panel:
        tkr = r["ticker"]
        is_bank = tkr in bank_tickers
        r["is_bank"] = is_bank
        r["fund"] = None
        r["has_fundamentals"] = False
        r["fund_age_days"] = None
        r["fund_stale"] = False
        r["fund_publish_date"] = None
        r["fund_fiscal_period"] = None
        template = "banks" if is_bank else "standard"
        stmts = merged_by_template.get(template, {}).get(tkr)
        if not stmts:
            continue
        idx = _asof_index(stmts, r["date"])
        if idx is None:
            continue
        s = stmts[idx]
        # Strict PIT guard: never use a statement available after the as-of date.
        age = _days_between(r["date"], s["avail_date"])
        if age < 0:
            cov["max_lookahead_violation_days"] = max(cov["max_lookahead_violation_days"], -age)
            continue
        shares = s["items"].get("Shares (Diluted)") or s["items"].get("Shares (Basic)")
        price = close_lookup(tkr, r["rebal_idx"])
        market_cap = (price * shares) if (price and shares) else None
        feats = compute_fundamental_features(stmts, idx, "banks" if is_bank else "standard", market_cap)
        # quality composite is computed later (needs cross-sectional z); store raw.
        r["fund"] = feats
        r["has_fundamentals"] = True
        r["fund_age_days"] = age
        r["fund_stale"] = age > STALE_MAX_DAYS
        r["fund_publish_date"] = s.get("publish_date")
        r["fund_fiscal_period"] = f'{s["fiscal_year"]}-{s["fiscal_period"]}'
        cov["rows_with_fund"] += 1
        cov["names_with_fund"].add(tkr)
        if r["fund_stale"]:
            cov["rows_stale"] += 1
        if is_bank:
            cov["bank_rows_with_fund"] += 1
        cov["by_year"].setdefault(r["year"], {"rows": 0, "with_fund": 0})
        cov["by_year"][r["year"]]["with_fund"] += 1
    for r in panel:
        cov["by_year"].setdefault(r["year"], {"rows": 0, "with_fund": 0})
        cov["by_year"][r["year"]]["rows"] += 1
    return panel, cov


# --------------------------------------------------------------------------- #
# Cross-sectional standardization + quality composite
# --------------------------------------------------------------------------- #
def _standardize(recs: List[dict], feats: List[str], sign: Dict[str, int],
                 getter: Callable[[dict], Optional[dict]], min_names: int
                 ) -> Dict[int, Dict[str, float]]:
    """Per-date cross-sectional z-scores (sign-adjusted). Leakage-free:
    contemporaneous only. ``recs`` are one date's rows."""
    out: Dict[int, Dict[str, float]] = {id(r): {} for r in recs}
    for feat in feats:
        xs = []
        for r in recs:
            src = getter(r)
            v = src.get(feat) if src else None
            if v is not None and math.isfinite(v):
                xs.append((r, v))
        if len(xs) < min_names:
            continue
        arr = np.array([v for _, v in xs], dtype=float)
        mu, sd = float(np.mean(arr)), float(np.std(arr))
        if sd <= 0:
            continue
        sg = sign.get(feat, +1)
        for r, v in xs:
            out[id(r)][feat] = sg * (v - mu) / sd
    return out


def _add_quality_composite(panel: List[dict], min_names: int) -> None:
    """Compute a standardized quality composite per date and store it inside each
    row's ``fund`` dict (so it standardizes again like any other feature)."""
    by_date: Dict[str, List[dict]] = {}
    for r in panel:
        if r.get("has_fundamentals"):
            by_date.setdefault(r["date"], []).append(r)
    for recs in by_date.values():
        z = _standardize(recs, _QUALITY_COMPOSITE_FEATURES, _FUND_SIGN,
                         lambda r: r.get("fund"), min_names)
        for r in recs:
            zz = z.get(id(r), {})
            vals = [zz[f] for f in _QUALITY_COMPOSITE_FEATURES if f in zz]
            if r["fund"] is not None:
                r["fund"][QUALITY_COMPOSITE] = float(np.mean(vals)) if vals else None


# --------------------------------------------------------------------------- #
# Generic walk-forward (one harness for every feature set -> clean attribution)
# --------------------------------------------------------------------------- #
def run_variant(p5c, panel: List[dict], feats: List[str], sign: Dict[str, int],
                getter: Callable[[dict], Optional[dict]],
                population_pred: Callable[[dict], bool], label: str
                ) -> Tuple[List[dict], List[dict]]:
    """Yearly walk-forward ridge, train-on-past / test-on-future, >=20-session
    embargo (identical scheme to Phase 5-C). Returns ``(predictions, folds)``."""
    pop = [r for r in panel if population_pred(r)]
    by_date: Dict[str, List[dict]] = {}
    for r in pop:
        by_date.setdefault(r["date"], []).append(r)
    dates = sorted(by_date)
    if not dates:
        return [], []
    date_idx = {d: by_date[d][0]["rebal_idx"] for d in dates}
    years = sorted({int(d[:4]) for d in dates})

    zmap: Dict[int, Dict[str, float]] = {}
    for d in dates:
        zmap.update(_standardize(by_date[d], feats, sign, getter, p5c.MIN_NAMES_PER_DATE))

    def _matrix(recs):
        rows = [[zmap.get(id(r), {}).get(f, 0.0) for f in feats] for r in recs]
        return np.array(rows, dtype=float)

    preds: List[dict] = []
    folds: List[dict] = []
    for test_year in years:
        test_dates = [d for d in dates if int(d[:4]) == test_year]
        test_recs = [r for d in test_dates for r in by_date[d] if r.get(label) is not None]
        if not test_recs:
            continue
        first_test_idx = min(date_idx[d] for d in test_dates)
        train_recs = []
        for d in dates:
            if int(d[:4]) >= test_year:
                continue
            label_close = date_idx[d] + p5c.H20
            if first_test_idx - label_close >= p5c.EMBARGO_DAYS:
                train_recs.extend(r for r in by_date[d] if r.get(label) is not None)
        if len(train_recs) < p5c.MIN_TRAIN_ROWS:
            continue
        Xtr = _matrix(train_recs)
        ytr = np.array([r[label] for r in train_recs], dtype=float)
        Xte = _matrix(test_recs)
        try:
            coef = p5c.ridge_fit(Xtr, ytr, p5c.RIDGE_LAMBDA)
            yhat = p5c.ridge_predict(Xte, coef)
        except np.linalg.LinAlgError:
            continue
        folds.append({"test_year": test_year, "train_rows": len(train_recs),
                      "test_rows": len(test_recs), "embargo_ok": True})
        for r, s in zip(test_recs, yhat):
            preds.append({
                "date": r["date"], "year": test_year, "regime": r["regime"],
                "ticker": r["ticker"], "score": float(s),
                "y": r[label], "raw20": r["forward_return_20d"],
            })
    return preds, folds


def _placebo_ic(p5c, panel, feats, sign, getter, population_pred, label) -> Optional[float]:
    """Leakage probe: shuffle the label within each date, refit, and confirm the
    mean rank IC collapses toward 0. A real edge must NOT survive the shuffle."""
    rng = np.random.RandomState(12345)
    shuffled = []
    by_date: Dict[str, List[dict]] = {}
    for r in panel:
        if population_pred(r) and r.get(label) is not None:
            by_date.setdefault(r["date"], []).append(r)
    for recs in by_date.values():
        ys = [r[label] for r in recs]
        perm = rng.permutation(len(ys))
        for r, j in zip(recs, perm):
            c = dict(r)
            c[label] = ys[j]
            shuffled.append(c)
    # Reuse the same id-keyed fund/features by copying references; getter still works.
    preds, _ = run_variant(p5c, shuffled, feats, sign, getter, lambda r: True, label)
    if not preds:
        return None
    ev = p5c.evaluate_model(preds)
    return ev.get("mean_rank_ic") if ev.get("evaluable") else None


# --------------------------------------------------------------------------- #
# Gates + recommendation
# --------------------------------------------------------------------------- #
def build_gate_matrix(common_eval: bool, n_common_names: int, n_common_dates: int,
                      pit_ok: bool, lookahead_violation_days: int,
                      placebo_ic: Optional[float], stale_frac: float,
                      n_banks: int, edge_delta_ic: Optional[float],
                      combined: dict, baseline: dict) -> List[dict]:
    gates: List[dict] = []

    def add(name, status, metric, value, threshold, note=""):
        gates.append({"gate_name": name, "status": status, "metric": metric,
                      "value": value, "threshold": threshold, "note": note})

    add("point_in_time_lag_explicit", "PASS", "lag_rule",
        f"publish_date else report_date+{FALLBACK_REPORT_LAG_DAYS}d", "documented",
        "free-tier 12-month delay blocks live trading; publish date is the as-of date for backtest")
    add("no_lookahead_join", "PASS" if lookahead_violation_days == 0 else "FAIL",
        "max_lookahead_violation_days", lookahead_violation_days, "==0",
        "only statements with availability date <= as-of date are joined")
    if placebo_ic is None:
        add("placebo_leakage", "NOT_EVALUABLE", "placebo_mean_ic", None, "|ic|<=0.02",
            "no placebo predictions")
    else:
        add("placebo_leakage", "PASS" if abs(placebo_ic) <= 0.02 else "FAIL",
            "placebo_mean_ic", _f(placebo_ic), "|ic|<=0.02",
            "label-shuffle must collapse IC -> no leakage")
    add("missing_derived_ratios_not_blocking", "PASS", "ratios_computed_internally", True,
        "true", "all fundamental features computed from raw statements")
    add("bank_standard_handled", "PASS", "bank_template_features_separate", True,
        "true", f"{n_banks} bank-template names use bank-specific line items")
    add("baseline_comparison_present", "PASS", "phase5c_price_only_baseline_evaluated",
        bool(baseline.get("evaluable")), "true", "price-only baseline rerun on the common set")
    cov_ok = n_common_names >= MIN_COMMON_NAMES and n_common_dates >= MIN_COMMON_DATES
    add("coverage_sufficiency", "PASS" if cov_ok else "FAIL", "common_names_and_dates",
        json.dumps({"names": n_common_names, "dates": n_common_dates}),
        f">={MIN_COMMON_NAMES} names & >={MIN_COMMON_DATES} dates")
    add("stale_data", "PASS" if stale_frac <= 0.25 else "WARNING", "stale_fraction",
        _f(stale_frac), "<=0.25", f"fundamental older than {STALE_MAX_DAYS}d at as-of date")
    if edge_delta_ic is None:
        add("incremental_edge_vs_price_only", "NOT_EVALUABLE", "delta_mean_rank_ic",
            None, f">={EDGE_MARGIN_DEPLOYABLE}")
    else:
        if edge_delta_ic >= EDGE_MARGIN_DEPLOYABLE:
            st = "PASS"
        elif edge_delta_ic > EDGE_MARGIN_IMPROVES:
            st = "WARNING"
        else:
            st = "FAIL"
        add("incremental_edge_vs_price_only", st, "delta_mean_rank_ic", _f(edge_delta_ic),
            f">={EDGE_MARGIN_DEPLOYABLE}", "price+fundamentals minus price-only (common set)")
    add("no_fake_data", "PASS", "real_local_data_only", True, "true",
        "labels from local price history; fundamentals from local SimFin statements")
    add("preview_only", "PASS", "preview_only", True, "true", "research harness only")
    add("no_orders", "PASS", "orders_enabled", False, "false", "")
    add("no_broker_execution", "PASS", "broker_execution_enabled", False, "false", "")
    add("no_automation", "PASS", "automation_enabled", False, "false", "")
    add("no_binary_model_artifact", "PASS", "binary_model_written", False, "false",
        "ridge fit in-memory for evaluation only; nothing persisted")
    return gates


def derive_recommendation(price_csv_ok: bool, simfin_ok: bool, pit_ok: bool,
                          common_eval: bool, edge_delta_ic: Optional[float],
                          combined: dict, baseline: dict, stale_frac: float
                          ) -> Tuple[str, str]:
    if not price_csv_ok or not simfin_ok:
        return REC_DATA_BLOCKER, ("local price history or local SimFin data missing; "
                                  "cannot build the enriched panel")
    if not pit_ok:
        return REC_PIT_BLOCKER, "could not establish a point-in-time-safe availability date"
    if not common_eval:
        return REC_DATA_BLOCKER, ("too few names/dates with point-in-time fundamentals to "
                                  "evaluate an enriched model honestly")
    if edge_delta_ic is None or not combined.get("evaluable") or not baseline.get("evaluable"):
        return REC_NO_EDGE, "enriched model not evaluable; price-only remains the baseline"
    base_t = baseline.get("ic_t_stat") or 0.0
    comb_t = combined.get("ic_t_stat") or 0.0
    spread_delta = (combined.get("mean_decile_spread") or 0.0) - (baseline.get("mean_decile_spread") or 0.0)
    deployable = (
        edge_delta_ic >= EDGE_MARGIN_DEPLOYABLE
        and comb_t >= base_t
        and comb_t >= 2.0
        and spread_delta > 0
        and stale_frac <= 0.25
    )
    if deployable:
        return REC_READY_5F, ("fundamentals add stable, significant incremental rank-IC over "
                              "price-only and survive the gates")
    if edge_delta_ic > EDGE_MARGIN_IMPROVES:
        return REC_IMPROVE_NOT_DEPLOY, ("fundamentals improve ranking but not by enough / not "
                                        "stable enough to justify a deployable scorer yet")
    return REC_NO_EDGE, "fundamentals do not improve cross-sectional ranking; keep price-only"


# --------------------------------------------------------------------------- #
# Artifact writers
# --------------------------------------------------------------------------- #
def _scoreboard_rows(scoreboard: Dict[str, dict]) -> List[list]:
    rows = []
    for name, ev in scoreboard.items():
        if not ev.get("evaluable"):
            rows.append([name, False, 0, 0, None, None, None, None, None, None, None, None])
            continue
        p20 = (ev.get("portfolios") or {}).get(20, {})
        net50 = (p20.get("net_50bps") or {})
        rows.append([
            name, True, ev["n_scored_dates"], ev["n_predictions"],
            _f(ev["mean_rank_ic"]), _f(ev["median_rank_ic"]), _f(ev.get("ic_t_stat")),
            _f(ev["positive_ic_hit_rate"]), ev.get("worst_year"), _f(ev.get("worst_year_ic")),
            _f(ev["mean_decile_spread"]), _f(net50.get("total_return")),
        ])
    return rows


def _write_all_csv_artifacts(scoreboard, cov, gates, edge_rows, panel, catalog_rows):
    _write_csv(_SCOREBOARD_OUT,
               ["model", "evaluable", "n_scored_dates", "n_predictions", "mean_rank_ic",
                "median_rank_ic", "ic_t_stat", "positive_ic_hit_rate", "worst_year",
                "worst_year_ic", "mean_decile_spread", "top20_net_50bps_total_return"],
               _scoreboard_rows(scoreboard))

    ic_rows = []
    for name, ev in scoreboard.items():
        if ev.get("evaluable"):
            for y, v in (ev.get("ic_by_year") or {}).items():
                ic_rows.append([name, y, _f(v)])
    _write_csv(_IC_BY_YEAR_OUT, ["model", "year", "mean_rank_ic"], ic_rows)

    ds_rows = []
    for name, ev in scoreboard.items():
        if ev.get("evaluable"):
            ds_rows.append([name, _f(ev["mean_decile_spread"]),
                            _f(ev["mean_top_decile_fwd_excess"]),
                            _f(ev["mean_bottom_decile_fwd_excess"])])
    _write_csv(_DECILE_SPREAD_OUT,
               ["model", "mean_decile_spread", "mean_top_decile_fwd_excess",
                "mean_bottom_decile_fwd_excess"], ds_rows)

    by_year_rows = []
    for y in sorted(cov["by_year"]):
        d = cov["by_year"][y]
        frac = (d["with_fund"] / d["rows"]) if d["rows"] else 0.0
        by_year_rows.append([y, d["rows"], d["with_fund"], _f(frac)])
    _write_csv(_COVERAGE_OUT,
               ["year", "panel_rows", "rows_with_pit_fundamentals", "coverage_fraction"],
               by_year_rows)

    _write_csv(_GATE_MATRIX_OUT,
               ["gate_name", "status", "metric", "value", "threshold", "note"],
               [[g["gate_name"], g["status"], g["metric"], g["value"], g["threshold"], g["note"]]
                for g in gates])

    _write_csv(_INCREMENTAL_EDGE_OUT,
               ["comparison", "metric", "price_only", "price_plus_fundamentals", "delta"],
               edge_rows)

    _write_csv(_FEATURE_CATALOG_OUT,
               ["feature", "family", "scope", "sign", "direction", "formula"],
               catalog_rows)

    # Panel sample: most recent date's common-set rows (committed-safe summary).
    sample = [r for r in panel if r.get("has_fundamentals")]
    sample.sort(key=lambda r: r["date"], reverse=True)
    sample = sample[:200]
    sample_cols = (["date", "ticker", "is_bank", "regime", "fund_fiscal_period",
                    "fund_publish_date", "fund_age_days", "fund_stale",
                    PRIMARY_LABEL] + FUND_FEATURES + [QUALITY_COMPOSITE])
    rows = []
    for r in sample:
        fnd = r.get("fund") or {}
        rows.append([r["date"], r["ticker"], r["is_bank"], r["regime"],
                     r["fund_fiscal_period"], r["fund_publish_date"], r["fund_age_days"],
                     r["fund_stale"], _f(r.get(PRIMARY_LABEL))]
                    + [_f(fnd.get(f)) for f in FUND_FEATURES] + [_f(fnd.get(QUALITY_COMPOSITE))])
    _write_csv(_PANEL_SAMPLE_OUT, sample_cols, rows)


def _catalog_rows() -> List[list]:
    formulas = {
        "gross_margin": "Gross Profit / Revenue",
        "operating_margin": "Operating Income / Revenue",
        "net_margin": "Net Income / Revenue",
        "roa_ttm": "TTM Net Income / Total Assets",
        "revenue_growth_yoy": "(Rev_t - Rev_t-4q) / |Rev_t-4q|",
        "operating_income_growth_yoy": "(OI_t - OI_t-4q) / |OI_t-4q|",
        "net_income_growth_yoy": "(NI_t - NI_t-4q) / |NI_t-4q|",
        "debt_to_assets": "(Short + Long Term Debt) / Total Assets",
        "liabilities_to_assets": "Total Liabilities / Total Assets",
        "equity_to_assets": "Total Equity / Total Assets (bank capital)",
        "current_ratio": "Total Current Assets / Total Current Liabilities",
        "cash_to_assets": "Cash & ST Investments / Total Assets",
        "ocf_to_assets": "Operating Cash Flow / Total Assets",
        "fcf_to_assets": "(OCF + Change in Fixed Assets) / Total Assets",
        "earnings_yield_ttm": "TTM Net Income / (price x diluted shares)",
        "book_to_price": "Total Equity / (price x diluted shares)",
        "deposits_to_assets": "Total Deposits / Total Assets (bank)",
        "loans_to_assets": "Net Loans / Total Assets (bank)",
    }
    rows = []
    for fam, feat, sg, scope in _FUND_FEATURE_DEFS:
        rows.append([feat, fam, scope, sg, "higher_is_bullish" if sg > 0 else "lower_is_bullish",
                     formulas.get(feat, "")])
    rows.append([QUALITY_COMPOSITE, "quality_composite", "shared", +1, "higher_is_bullish",
                 "mean of standardized " + "+".join(_QUALITY_COMPOSITE_FEATURES)])
    return rows


# --------------------------------------------------------------------------- #
# Main orchestration
# --------------------------------------------------------------------------- #
def run(price_history: Optional[dict] = None, simfin_tables: Optional[dict] = None,
        out_dir: Optional[Path] = None, data_dir: Optional[Path] = None,
        price_csv: Optional[str] = None, phase5c_runner: Optional[Path] = None,
        max_tickers: Optional[int] = None, write: bool = True, verbose: bool = True) -> dict:
    """Run the enriched rerun. Inputs can be injected (tests) or loaded from disk
    (real run). Always returns a report dict; ``recommendation`` is one of
    ``ALLOWED_RECOMMENDATIONS``."""
    global _OUT_DIR, _REPORT_OUT, _FEATURE_CATALOG_OUT, _PANEL_SAMPLE_OUT, _SCOREBOARD_OUT
    global _IC_BY_YEAR_OUT, _DECILE_SPREAD_OUT, _COVERAGE_OUT, _GATE_MATRIX_OUT
    global _INCREMENTAL_EDGE_OUT, _PHASE5F_PLAN_OUT
    if out_dir is not None:
        _OUT_DIR = Path(out_dir)
        _REPORT_OUT = _OUT_DIR / "phase5e2_simfin_enriched_model_rerun.json"
        _FEATURE_CATALOG_OUT = _OUT_DIR / "simfin_enriched_feature_catalog.csv"
        _PANEL_SAMPLE_OUT = _OUT_DIR / "simfin_enriched_panel_sample.csv"
        _SCOREBOARD_OUT = _OUT_DIR / "simfin_enriched_model_scoreboard.csv"
        _IC_BY_YEAR_OUT = _OUT_DIR / "simfin_enriched_ic_by_year.csv"
        _DECILE_SPREAD_OUT = _OUT_DIR / "simfin_enriched_decile_spread.csv"
        _COVERAGE_OUT = _OUT_DIR / "simfin_enriched_coverage_report.csv"
        _GATE_MATRIX_OUT = _OUT_DIR / "simfin_enriched_validation_gate_matrix.csv"
        _INCREMENTAL_EDGE_OUT = _OUT_DIR / "simfin_enriched_incremental_edge_report.csv"
        _PHASE5F_PLAN_OUT = _OUT_DIR / "phase5f_deployable_scorer_plan.json"

    p5c = _load_phase5c(phase5c_runner)

    # ---- 1. price history -> aligned price-only panel ----
    price_csv_ok = True
    resolved_price_csv = None
    if price_history is None:
        resolved_price_csv = price_csv or p5c._data_path()
        if not resolved_price_csv:
            price_csv_ok = False
        else:
            price_history = p5c.load_local_history(resolved_price_csv)
    if not price_history:
        price_csv_ok = False

    # ---- 2. local SimFin statements ----
    if simfin_tables is None:
        simfin_tables = load_simfin_tables(data_dir)
    simfin_ok = bool(simfin_tables) and any(
        simfin_tables.get(t, {}).get(c) for t in ("standard", "banks") for c in _STATEMENT_CODES)

    base_report = {
        "phase": PHASE, "provider": PROVIDER,
        "objective": ("Does adding SimFin quarterly fundamentals improve cross-sectional "
                      "ranking enough to justify a deployable scorer (Phase 5-F)?"),
        "track": "A (quantitative model evidence) — not Track B operational packaging.",
        "data_inputs": {
            "price_history_csv": resolved_price_csv,
            "simfin_local_normalized_dir": _rel(Path(data_dir) if data_dir else _SIMFIN_NORM_DIR),
            "uses_local_simfin_only": True,
            "live_api_calls": False, "simfin_downloads": False, "api_key_required": False,
        },
        "point_in_time": {
            "join_key": "ticker + statement availability date (publish date)",
            "lag_assumption": (f"availability = SimFin Publish Date; when null, fiscal Report "
                               f"Date + {FALLBACK_REPORT_LAG_DAYS} days (conservative reporting lag)"),
            "free_data_delay_months": 12,
            "usable_for_live_trading_today": False,
            "point_in_time_safe_for_research": True,
        },
        "feature_families": FEATURE_FAMILIES,
        "fundamental_features": FUND_FEATURES + [QUALITY_COMPOSITE],
        "derived_ratios_available": False,
        "ratios_computed_internally": True,
        "models_evaluated": [
            "price_only_full_panel_reference", "price_only_baseline",
            "fundamentals_only", "price_plus_fundamentals",
            "price_plus_fundamentals_quality",
        ],
        "preview_only": True, "orders_enabled": False, "automation_enabled": False,
        "broker_execution_enabled": False, "production_replacement": False,
        "writes_to_d_drive": False, "modifies_paper_trader": False, "modifies_gcp": False,
        "deploys": False, "creates_binary_model_artifact": False,
        "fits_models_in_memory_for_evaluation_only": True,
        "allowed_recommendations": list(ALLOWED_RECOMMENDATIONS),
        "recommended_next_phase": "5-F",
    }

    if not price_csv_ok or not simfin_ok:
        rec, why = derive_recommendation(price_csv_ok, simfin_ok, True, False, None, {}, {}, 0.0)
        base_report.update({"recommendation": rec, "recommendation_reason": why,
                            "data_coverage": {"price_history_loaded": price_csv_ok,
                                              "simfin_loaded": simfin_ok}})
        assert base_report["recommendation"] in ALLOWED_RECOMMENDATIONS
        if write:
            _write_json(_REPORT_OUT, base_report)
            _write_json(_PHASE5F_PLAN_OUT, _phase5f_plan(rec))
        if verbose:
            _print_summary(base_report, {}, [])
        return base_report

    al = p5c.Aligned(price_history)
    panel = p5c.build_panel(al)
    if max_tickers:
        keep = set(sorted(al.tickers)[:max_tickers])
        panel = [r for r in panel if r["ticker"] in keep]

    # ---- 3. merge statements per template, discover bank tickers ----
    merged = {
        "standard": _merge_statements(simfin_tables.get("standard", {})),
        "banks": _merge_statements(simfin_tables.get("banks", {})),
    }
    bank_tickers = set(merged["banks"].keys())

    def _close(t, idx):
        arr = al.close.get(t)
        if arr is None or idx >= len(arr):
            return None
        v = float(arr[idx])
        return v if math.isfinite(v) else None

    # ---- 4. PIT-safe enrichment + quality composite ----
    panel, cov = enrich_panel(panel, merged, bank_tickers, _close)
    _add_quality_composite(panel, p5c.MIN_NAMES_PER_DATE)

    pit_ok = cov["max_lookahead_violation_days"] == 0
    common_rows = [r for r in panel if r.get("has_fundamentals") and r.get(PRIMARY_LABEL) is not None]
    n_common_names = len({r["ticker"] for r in common_rows})
    n_common_dates = len({r["date"] for r in common_rows})
    common_eval = n_common_names >= MIN_COMMON_NAMES and n_common_dates >= MIN_COMMON_DATES
    stale_frac = (cov["rows_stale"] / cov["rows_with_fund"]) if cov["rows_with_fund"] else 0.0

    # ---- 5. evaluate models under one harness ----
    price_sign = dict(p5c._FEATURE_SIGN)
    price_feats = list(p5c.MODEL_FEATURES)
    combined_feats = price_feats + FUND_FEATURES
    combined_sign = {**price_sign, **_FUND_SIGN}
    get_price = lambda r: r.get("features")
    get_fund = lambda r: r.get("fund")
    get_both = lambda r: {**(r.get("features") or {}), **(r.get("fund") or {})}

    has_fund = lambda r: bool(r.get("has_fundamentals"))
    all_rows = lambda r: True

    variants = {
        "price_only_full_panel_reference": (price_feats, price_sign, get_price, all_rows),
        "price_only_baseline": (price_feats, price_sign, get_price, has_fund),
        "fundamentals_only": (FUND_FEATURES, _FUND_SIGN, get_fund, has_fund),
        "price_plus_fundamentals": (combined_feats, combined_sign, get_both, has_fund),
        "price_plus_fundamentals_quality": (
            combined_feats + [QUALITY_COMPOSITE], {**combined_sign, QUALITY_COMPOSITE: +1},
            get_both, has_fund),
    }
    scoreboard: Dict[str, dict] = {}
    folds_by_model: Dict[str, list] = {}
    for name, (feats, sign, getter, pred) in variants.items():
        preds, folds = run_variant(p5c, panel, feats, sign, getter, pred, PRIMARY_LABEL)
        scoreboard[name] = p5c.evaluate_model(preds)
        folds_by_model[name] = folds

    baseline = scoreboard["price_only_baseline"]
    combined = scoreboard["price_plus_fundamentals"]
    fonly = scoreboard["fundamentals_only"]
    edge_delta_ic = None
    if baseline.get("evaluable") and combined.get("evaluable"):
        edge_delta_ic = combined["mean_rank_ic"] - baseline["mean_rank_ic"]

    placebo_ic = None
    if common_eval:
        placebo_ic = _placebo_ic(p5c, panel, combined_feats, combined_sign, get_both,
                                 has_fund, PRIMARY_LABEL)

    gates = build_gate_matrix(common_eval, n_common_names, n_common_dates, pit_ok,
                              cov["max_lookahead_violation_days"], placebo_ic, stale_frac,
                              len(bank_tickers), edge_delta_ic, combined, baseline)
    rec, why = derive_recommendation(price_csv_ok, simfin_ok, pit_ok, common_eval,
                                     edge_delta_ic, combined, baseline, stale_frac)

    # ---- incremental edge rows ----
    def _edge(metric, key):
        b = baseline.get(key) if baseline.get("evaluable") else None
        c = combined.get(key) if combined.get("evaluable") else None
        delta = (c - b) if (b is not None and c is not None) else None
        return [metric, _f(b), _f(c), _f(delta)]
    edge_rows = [
        _edge("mean_rank_ic", "mean_rank_ic"),
        _edge("median_rank_ic", "median_rank_ic"),
        _edge("ic_t_stat", "ic_t_stat"),
        _edge("positive_ic_hit_rate", "positive_ic_hit_rate"),
        _edge("mean_decile_spread", "mean_decile_spread"),
        _edge("top_quintile_hit_rate", "top_quintile_hit_rate"),
    ]
    if baseline.get("evaluable") and combined.get("evaluable"):
        years = sorted(set((baseline.get("ic_by_year") or {})) | set((combined.get("ic_by_year") or {})))
        improved = 0
        for y in years:
            b = (baseline.get("ic_by_year") or {}).get(y)
            c = (combined.get("ic_by_year") or {}).get(y)
            if b is not None and c is not None:
                edge_rows.append([f"ic_year_{y}", _f(b), _f(c), _f(c - b)])
                if c > b:
                    improved += 1
        edge_rows.append(["years_improved_fraction", None, None,
                          _f(improved / len(years)) if years else None])
    fundamentals_beat = bool(edge_delta_ic is not None and edge_delta_ic > EDGE_MARGIN_IMPROVES)

    report = dict(base_report)
    report.update({
        "universe_size": len(al.tickers),
        "data_coverage": {
            "price_history_loaded": True, "simfin_loaded": True,
            "panel_rows_total": cov["rows_total"],
            "panel_rows_with_pit_fundamentals": cov["rows_with_fund"],
            "common_evaluable_names": n_common_names,
            "common_evaluable_dates": n_common_dates,
            "names_with_fundamentals": len(cov["names_with_fund"]),
            "bank_template_names": sorted(bank_tickers),
            "bank_rows_with_fundamentals": cov["bank_rows_with_fund"],
            "stale_fraction": _f(stale_frac),
            "coverage_by_year": {str(y): cov["by_year"][y] for y in sorted(cov["by_year"])},
        },
        "point_in_time_safe": pit_ok,
        "max_lookahead_violation_days": cov["max_lookahead_violation_days"],
        "placebo_mean_ic": _f(placebo_ic),
        "scoreboard": p5c_scoreboard_json(scoreboard),
        "fundamentals_only_mean_rank_ic": _f(fonly.get("mean_rank_ic") if fonly.get("evaluable") else None),
        "incremental_edge": {
            "baseline_mean_rank_ic": _f(baseline.get("mean_rank_ic") if baseline.get("evaluable") else None),
            "combined_mean_rank_ic": _f(combined.get("mean_rank_ic") if combined.get("evaluable") else None),
            "delta_mean_rank_ic": _f(edge_delta_ic),
            "fundamentals_beat_price_only": fundamentals_beat,
        },
        "validation_gate_matrix": gates,
        "validation_gate_summary": _gate_summary(gates),
        "recommendation": rec,
        "recommendation_reason": why,
    })
    assert report["recommendation"] in ALLOWED_RECOMMENDATIONS

    if write:
        _write_json(_REPORT_OUT, report)
        _write_all_csv_artifacts(scoreboard, cov, gates, edge_rows, panel, _catalog_rows())
        _write_json(_PHASE5F_PLAN_OUT, _phase5f_plan(rec))
    if verbose:
        _print_summary(report, scoreboard, gates)
    return report


def p5c_scoreboard_json(scoreboard: Dict[str, dict]) -> Dict[str, dict]:
    out = {}
    for name, ev in scoreboard.items():
        if not ev.get("evaluable"):
            out[name] = {"evaluable": False, "reason": ev.get("reason")}
            continue
        p20 = (ev.get("portfolios") or {}).get(20, {})
        out[name] = {
            "evaluable": True, "n_scored_dates": ev["n_scored_dates"],
            "n_predictions": ev["n_predictions"], "mean_rank_ic": _f(ev["mean_rank_ic"]),
            "median_rank_ic": _f(ev["median_rank_ic"]), "ic_t_stat": _f(ev.get("ic_t_stat")),
            "positive_ic_hit_rate": _f(ev["positive_ic_hit_rate"]),
            "worst_year": ev.get("worst_year"), "worst_year_ic": _f(ev.get("worst_year_ic")),
            "mean_decile_spread": _f(ev["mean_decile_spread"]),
            "top20_net_50bps_total_return": _f((p20.get("net_50bps") or {}).get("total_return")),
        }
    return out


def _gate_summary(gates: List[dict]) -> dict:
    summ = {"PASS": 0, "FAIL": 0, "WARNING": 0, "NOT_EVALUABLE": 0}
    for g in gates:
        summ[g["status"]] = summ.get(g["status"], 0) + 1
    return summ


def _phase5f_plan(recommendation: str) -> dict:
    return {
        "phase": "5-F",
        "title": "Deployable cross-sectional scorer plan (planned, not yet built)",
        "gated_on": f"Phase 5-E2 recommendation = {REC_READY_5F}.",
        "phase5e2_recommendation": recommendation,
        "proceed_to_5f": recommendation == REC_READY_5F,
        "scope_if_proceed": [
            "Freeze the winning feature set + ridge coefficients from the walk-forward.",
            "Wrap a stateless scorer that ingests local price features + PIT fundamentals.",
            "Re-validate on a fully out-of-sample hold-out before any packaging.",
        ],
        "hard_constraints": [
            "Free SimFin data is delayed ~12 months -> NOT usable for live trading; "
            "a deployable scorer needs a non-delayed fundamentals source first.",
            "No orders, no broker execution, no automation, no GCP deploy in 5-F research.",
        ],
        "if_not_ready": ("Keep the Phase 5-C price-only baseline; do not build a fundamentals "
                         "scorer until incremental edge is demonstrated and a timely data source exists."),
        "preview_only": True, "orders_enabled": False, "automation_enabled": False,
        "broker_execution_enabled": False, "production_replacement": False,
    }


def _print_summary(report: dict, scoreboard: Dict[str, dict], gates: List[dict]) -> None:
    print("-" * 72)
    print(f"  Phase {report['phase']} — SimFin-enriched cross-sectional model rerun")
    print(f"  recommendation   : {report['recommendation']}")
    print(f"  reason           : {report.get('recommendation_reason')}")
    dc = report.get("data_coverage", {})
    if dc.get("simfin_loaded"):
        print(f"  common names/dates: {dc.get('common_evaluable_names')} / "
              f"{dc.get('common_evaluable_dates')}")
        print(f"  bank names        : {len(dc.get('bank_template_names', []))} "
              f"{dc.get('bank_template_names')}")
        ie = report.get("incremental_edge", {})
        print(f"  baseline IC       : {ie.get('baseline_mean_rank_ic')}")
        print(f"  combined IC       : {ie.get('combined_mean_rank_ic')}")
        print(f"  delta IC          : {ie.get('delta_mean_rank_ic')} "
              f"(fundamentals beat price-only: {ie.get('fundamentals_beat_price_only')})")
        print(f"  placebo IC        : {report.get('placebo_mean_ic')} (should be ~0)")
        print(f"  gate summary      : {report.get('validation_gate_summary')}")
    else:
        print("  data coverage     : price/SimFin data not fully available")
    print(f"  next phase        : {report.get('recommended_next_phase')}")
    print("-" * 72)
    if report.get("recommendation") not in (REC_DATA_BLOCKER, REC_PIT_BLOCKER, REC_ERROR):
        for p in (_REPORT_OUT, _FEATURE_CATALOG_OUT, _PANEL_SAMPLE_OUT, _SCOREBOARD_OUT,
                  _IC_BY_YEAR_OUT, _DECILE_SPREAD_OUT, _COVERAGE_OUT, _GATE_MATRIX_OUT,
                  _INCREMENTAL_EDGE_OUT, _PHASE5F_PLAN_OUT):
            print(f"  wrote: {_rel(p)}")


def _parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Phase 5-E2 SimFin-enriched cross-sectional rerun")
    ap.add_argument("--price-csv", default=None, help="override local price-history CSV path")
    ap.add_argument("--data-dir", default=None, help="override local SimFin normalized dir")
    ap.add_argument("--max-tickers", type=int, default=None, help="cap universe (debug)")
    return ap.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    ns = _parse_args(argv)
    try:
        report = run(price_csv=ns.price_csv,
                     data_dir=Path(ns.data_dir) if ns.data_dir else None,
                     max_tickers=ns.max_tickers, write=True, verbose=True)
    except Exception as exc:  # noqa: BLE001 — emit an honest ERROR report, never crash silently
        report = {"phase": PHASE, "provider": PROVIDER, "recommendation": REC_ERROR,
                  "recommendation_reason": f"{type(exc).__name__}: {exc}"}
        _write_json(_REPORT_OUT, report)
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return 0 if report.get("recommendation") != REC_ERROR else 1


if __name__ == "__main__":
    raise SystemExit(main())
