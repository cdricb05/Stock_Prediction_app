"""Phase 7-J — Broad Universe Fundamentals Collection and Signal Retest.

**Track A (quant brain) research only.** Point-in-time, leakage-safe. The ONLY live
data is free SEC EDGAR public JSON (companyfacts + submissions) fetched with the stdlib
``urllib`` SEC client already in the repo, host-restricted to sec.gov, rate-limited, and
cache-first (re-runs hit local cache, no network). No paid API, no API key, no package
install, no model trained or deployed, **no factor-weight optimization, no factor-sign
flipping, no regime selection/weighting**, no Paper Trader / GCP / deployment / broker /
orders / automation, no commit, no push. Large raw SEC payloads and the normalized broad
fundamentals panel are written ONLY under
``D:\\Stock_Prediction_app_data\\phase7j_broad_universe_signal_retest\\``; the repo
receives only committed-safe summaries / scoreboards. The existing phase2k_g and
phase7i_broad_universe data on D: is never overwritten.

Why this phase exists
---------------------
Phase 7-F was the best local 128-name signal (composite IC +0.017726, t 1.245). Phase
7-H showed dense TTM fundamentals did NOT improve it on the 128-name universe (IC
+0.013954, t 0.922 — WEAK), and concluded the binding constraint was the narrow,
survivorship-biased universe, not the fundamentals. Phase 7-I then ran an approved free
price pilot and collected 296 usable daily-price tickers (>= 250 threshold), unblocking a
broad-universe retest. The remaining blocker was broad-universe SEC fundamentals for those
296 names. This phase collects them and runs the retest.

The ONE question this phase answers
-----------------------------------
    DID THE SIGNAL SURVIVE ON THE BROADER 296-NAME UNIVERSE, YES OR NO?

It is a RESULTS phase, not a plan-only phase. It reuses, unchanged:
  * the Phase 7-G de-cumulation that turns YTD 10-Q flows into point-in-time 3-month
    quarters and trailing-4-quarter TTM windows (availability = max of the four legs);
  * the Phase 7-H TTM value/quality/growth factor construction and the Phase 7-F
    price-momentum factors + low-volatility risk descriptor;
  * the Phase 7-B validation harness (mean rank IC, IC t-stat, yearly IC, quintile/decile
    spread, placebo, leakage check) via the Phase 7-C grading adapters;
by redirecting the data-source module globals to the broad price panel and the freshly
collected broad fundamentals. Factors are equal-weighted within and across approved alpha
buckets; signs are a priori and never flipped; low-volatility stays a risk descriptor only;
regimes are diagnostic only. Because no point-in-time sector history exists for the broad
universe, cross-sectional standardization is NOT sector-neutralized here (documented
caveat; this is a survivorship-aware *breadth* retest, not a sector-neutral retest).

Strict decision rule (borderline never rounded up)
--------------------------------------------------
  * broad final composite IC > 0 AND IC t-stat >= 1.5 AND it beats the broad price-only
    baseline by >= +0.005  -> BROAD_UNIVERSE_SIGNAL_SURVIVED
  * broad final composite IC > 0 but misses any one bar          -> BROAD_UNIVERSE_SIGNAL_WEAK
  * broad final composite IC <= 0                                -> BROAD_UNIVERSE_SIGNAL_FAILED
  * SEC/fundamental coverage too low to test                     -> FUNDAMENTAL_COVERAGE_BLOCKED
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Reuse, in order: the 7-B harness (Sharpe / drawdown / costs), the 7-C ranking engine
# (loaders / normalization / grading / composite / placebo / leakage), the 7-F upgrade
# (price-momentum factors + implied shares + equal-weight combiner), the 7-G data
# foundation (de-cumulation), the 7-H TTM retest (TTM panels / factors / catalogue), and
# the 3-F SEC client + companyfacts normalizer. No work at import.
from research import run_phase7b_validation_harness_foundation as H7B
from research import run_phase7c_multifactor_ranking_engine as C
from research import run_phase7f_signal_reliability_upgrade as F
from research import run_phase7g_signal_data_foundation_upgrade as G
from research import run_phase7h_ttm_fundamental_signal_retest as H
from research import prototype_phase3f_sec_fundamentals as SEC

PHASE = "7-J"
OBJECTIVE = (
    "Collect free SEC EDGAR companyfacts/submissions fundamentals for the 296 usable "
    "Phase 7-I price tickers, rebuild the de-cumulated point-in-time TTM/factor panel, and "
    "re-grade the Phase 7-F/7-H equal-weight composite through the UNMODIFIED Phase 7-B "
    "harness on the broad universe. One question: did the signal survive on the broader "
    "296-name universe? Equal weight, no sign flipping, no weight optimization, no regime "
    "selection/weighting, no paid API, no package install, no trading/order/automation."
)

# --------------------------------------------------------------------------- #
# Recommendation vocabulary (exactly the allowed set).
# --------------------------------------------------------------------------- #
REC_SURVIVED = "BROAD_UNIVERSE_SIGNAL_SURVIVED"
REC_WEAK = "BROAD_UNIVERSE_SIGNAL_WEAK"
REC_FAILED = "BROAD_UNIVERSE_SIGNAL_FAILED"
REC_COVERAGE_BLOCKED = "FUNDAMENTAL_COVERAGE_BLOCKED"
REC_ERROR = "ERROR"
ALLOWED_RECOMMENDATIONS = (REC_SURVIVED, REC_WEAK, REC_FAILED, REC_COVERAGE_BLOCKED, REC_ERROR)

# --------------------------------------------------------------------------- #
# Decision thresholds (strict; task-specified). Not tuned, never fit to outcome.
# --------------------------------------------------------------------------- #
IC_T_GATE = 1.5                 # composite IC t-stat must be >= this
INCREMENTAL_GATE = 0.005        # composite must beat the price-only baseline by this IC
MIN_USABLE_PRICE_TICKERS = 250  # Phase 7-I price-coverage precondition
MIN_FUND_COVERAGE_TICKERS = 200 # tickers needing >= 1 computable fundamental bucket
MIN_APPROVE_PERIODS = F.MIN_APPROVE_PERIODS   # 24 — breadth/history approval (NEVER IC-based)
MIN_NAMES = C.MIN_NAMES                       # 20 — minimum cross-section for usable rank IC
COST_BPS = 10.0
BENCHMARK = C.BENCHMARK                       # "SPY" — excluded from the ranked universe

# Phase 7-F 128-name result — for CONTEXT ONLY, not the primary statistical comparison.
PHASE7F_CONTEXT_IC = 0.017726
PHASE7F_CONTEXT_T = 1.245

# Factor buckets (identical to Phase 7-H; the task's bucket list maps 1:1).
MOMENTUM_SUBFACTORS = H.MOMENTUM_SUBFACTORS               # mom_12_1, mom_6_1, mom_riskadj
TTM_BUCKETS = H.TTM_BUCKETS                               # momentum, value_ttm, quality_ttm, growth_ttm
_VALUE_KEYS = [f.key for f in H.TTM_FACTORS if f.bucket == "value_ttm"]
_QUALITY_KEYS = [f.key for f in H.TTM_FACTORS if f.bucket == "quality_ttm"]
_GROWTH_KEYS = [f.key for f in H.TTM_FACTORS if f.bucket == "growth_ttm"]
_FUND_BUCKETS = ("value_ttm", "quality_ttm", "growth_ttm")

# --------------------------------------------------------------------------- #
# Paths.
# --------------------------------------------------------------------------- #
PHASE7I_OUT = _REPO_ROOT / "research" / "output" / "phase7i_controlled_broad_universe_data_build"
PHASE7I_STATUS_CSV = PHASE7I_OUT / "collection_status.csv"
PHASE7I_MANIFEST_CSV = PHASE7I_OUT / "data_storage_manifest.csv"

BROAD_PRICE_PANEL_CSV = os.path.join(
    "D:", os.sep, "Stock_Prediction_app_data", "phase7i_broad_universe", "prices",
    "phase7i_broad_price_history_free.csv")
SEC_COMPANY_TICKERS_JSON = G.SEC_COMPANY_TICKERS_JSON

# Large data root for Phase 7-J (NEW dir; existing D: data untouched).
DATA_ROOT = os.path.join("D:", os.sep, "Stock_Prediction_app_data", "phase7j_broad_universe_signal_retest")
DATA_COMPANYFACTS_DIR = os.path.join(DATA_ROOT, "sec_companyfacts")
DATA_SUBMISSIONS_DIR = os.path.join(DATA_ROOT, "sec_submissions")
BROAD_FUNDAMENTALS_CSV = os.path.join(DATA_ROOT, "broad_fundamentals.csv")
BROAD_FUND_SUMMARY_JSON = os.path.join(DATA_ROOT, "broad_fundamentals_build_summary.json")

_OUT_DIR_DEFAULT = _REPO_ROOT / "research" / "output" / "phase7j_broad_universe_signal_retest"

_f = C._f
_round = C._round


# --------------------------------------------------------------------------- #
# Output artifact paths (committed-safe summaries only).
# --------------------------------------------------------------------------- #
@dataclass
class _OutPaths:
    base: Path

    def __post_init__(self):
        self.report = self.base / "phase7j_broad_universe_signal_retest.json"
        self.data_coverage = self.base / "broad_universe_data_coverage.csv"
        self.collection_status = self.base / "broad_fundamental_collection_status.csv"
        self.factor_catalog = self.base / "broad_factor_catalog.csv"
        self.factor_scoreboard = self.base / "broad_factor_scoreboard.csv"
        self.bucket_scoreboard = self.base / "broad_bucket_scoreboard.csv"
        self.composite_scoreboard = self.base / "broad_composite_scoreboard.csv"
        self.regime_scoreboard = self.base / "broad_regime_diagnostic_scoreboard.csv"
        self.gate_matrix = self.base / "broad_signal_gate_matrix.csv"
        self.next_plan = self.base / "phase7k_next_plan.json"


def _rel(p) -> str:
    try:
        return str(Path(p).relative_to(_REPO_ROOT)).replace(os.sep, "/")
    except Exception:
        return str(p).replace(os.sep, "/")


def _norm(p) -> str:
    return str(p).replace(os.sep, "/")


def _write_json(path, obj: Dict) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=2, default=str)


def _write_csv(path, rows: List[Dict], columns: List[str]) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=columns)
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in columns})


# --------------------------------------------------------------------------- #
# 1-3. Phase 7-I usable price tickers.
# --------------------------------------------------------------------------- #
def read_phase7i_usable_tickers() -> Tuple[List[str], Dict]:
    """Read Phase 7-I live collection_status.csv; return the usable, non-benchmark tickers
    and a summary. Usable == price status COLLECTED/CACHED with usable flag true."""
    summary = {"status_csv": _rel(PHASE7I_STATUS_CSV), "present": False,
               "total_rows": 0, "usable_price_tickers": 0, "threshold": MIN_USABLE_PRICE_TICKERS,
               "threshold_met": False, "benchmark_excluded": BENCHMARK}
    if not PHASE7I_STATUS_CSV.is_file():
        return [], summary
    usable: List[str] = []
    total = 0
    with open(PHASE7I_STATUS_CSV, "r", encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            total += 1
            tk = (row.get("ticker") or "").strip().upper()
            status = (row.get("status") or "").strip().upper()
            is_usable = (row.get("usable") or "").strip().lower() == "true"
            if not tk or tk == BENCHMARK:
                continue
            if is_usable and status in ("COLLECTED", "CACHED"):
                usable.append(tk)
    usable = sorted(set(usable))
    summary.update({"present": True, "total_rows": total, "usable_price_tickers": len(usable),
                    "threshold_met": len(usable) >= MIN_USABLE_PRICE_TICKERS})
    return usable, summary


# --------------------------------------------------------------------------- #
# 4. Ticker -> CIK mapping from the local SEC directory.
# --------------------------------------------------------------------------- #
def load_ticker_cik_map() -> Dict[str, int]:
    if not Path(SEC_COMPANY_TICKERS_JSON).is_file():
        return {}
    with open(SEC_COMPANY_TICKERS_JSON, "r", encoding="utf-8") as fh:
        directory = json.load(fh)
    idx = SEC._company_ticker_index(directory)   # ticker(upper) -> (cik_int, title)
    out: Dict[str, int] = {}
    for tk, (cik, _title) in idx.items():
        try:
            out[tk] = int(cik)
        except (TypeError, ValueError):
            continue
    return out


# --------------------------------------------------------------------------- #
# 5. SEC companyfacts/submissions collection (reuse 3-F client + normalizer).
#    Caps raised vs the 3-F prototype so full fundamental history is kept; full payloads
#    cached to D: (no Git-size cap needed off-repo). Cache-first => re-runs make no calls.
# --------------------------------------------------------------------------- #
def _keep_companyfacts(full: Dict) -> Dict:
    """Prune to the wanted us-gaap concepts but keep ALL periodic-form facts (no cap)."""
    ug = (full.get("facts", {}) or {}).get("us-gaap", {}) or {}
    kept = {}
    for concept in SEC._WANTED_CONCEPTS:
        node = ug.get(concept)
        if not node:
            continue
        pruned_units = {}
        for unit, facts in (node.get("units", {}) or {}).items():
            periodic = [f for f in facts if f.get("form") in SEC._KEEP_FORMS]
            if periodic:
                pruned_units[unit] = periodic
        if pruned_units:
            kept[concept] = {"label": node.get("label"), "units": pruned_units}
    return {"cik": full.get("cik"), "entityName": full.get("entityName"),
            "facts": {"us-gaap": kept}}


def _keep_submissions(full: Dict) -> Dict:
    """Prune submissions to periodic filings (accession -> acceptance), no cap."""
    recent = (full.get("filings", {}) or {}).get("recent", {}) or {}
    keys = ["accessionNumber", "form", "filingDate", "acceptanceDateTime", "reportDate"]
    arrs = {k: recent.get(k, []) or [] for k in keys}
    n = len(arrs["accessionNumber"])
    forms = arrs["form"]
    idx = [i for i in range(n) if i < len(forms) and forms[i] in SEC._KEEP_FORMS]
    pruned = {k: [arrs[k][i] for i in idx if i < len(arrs[k])] for k in keys}
    return {"cik": full.get("cik"), "name": full.get("name"),
            "filings": {"recent": pruned}}


def collect_broad_fundamentals(universe: List[str], cik_map: Dict[str, int],
                               *, network: bool) -> Tuple[List[Dict], List[Dict], Dict]:
    """Collect or reuse SEC companyfacts/submissions for the universe; return
    (normalized_fundamentals_rows, per_ticker_status_rows, summary). Network is cache-first
    and gated by ``network`` (when False, only cached payloads are used)."""
    os.makedirs(DATA_COMPANYFACTS_DIR, exist_ok=True)
    os.makedirs(DATA_SUBMISSIONS_DIR, exist_ok=True)

    # Raise the 3-F prototype caps for a real multi-year retest (full history; many names).
    _saved_caps = (SEC.MAX_TOTAL_REQUESTS, SEC._OUTPUT_PERIOD_CAP)
    SEC.MAX_TOTAL_REQUESTS = 10_000_000
    SEC._OUTPUT_PERIOD_CAP = 10_000

    client = SEC.SecClient()
    fund_rows: List[Dict] = []
    status_rows: List[Dict] = []
    n_collected = n_cached = n_failed = n_no_cik = 0
    network_blocked = False

    try:
        for tk in universe:
            cik = cik_map.get(tk)
            if not cik:
                n_no_cik += 1
                status_rows.append({"ticker": tk, "cik": "", "status": "NO_CIK",
                                    "rows": 0, "fields": 0, "origin": "none",
                                    "note": "ticker not found in SEC directory"})
                continue
            cik10 = "%010d" % int(cik)
            cf_url = SEC.COMPANYFACTS_URL_TEMPLATE.format(cik10=cik10)
            sub_url = SEC.SUBMISSIONS_URL_TEMPLATE.format(cik10=cik10)
            cf_cache = os.path.join(DATA_COMPANYFACTS_DIR, "CIK%s.json" % cik10)
            sub_cache = os.path.join(DATA_SUBMISSIONS_DIR, "CIK%s.json" % cik10)

            # If network is off and nothing is cached, skip honestly.
            if not network and not (os.path.isfile(cf_cache) and os.path.isfile(sub_cache)):
                status_rows.append({"ticker": tk, "cik": cik10, "status": "SKIPPED_NO_NETWORK",
                                    "rows": 0, "fields": 0, "origin": "none",
                                    "note": "network disabled and no cache present"})
                continue
            try:
                companyfacts, cf_origin = client.get_cached_or_fetch(cf_url, cf_cache, _keep_companyfacts)
                submissions, sub_origin = client.get_cached_or_fetch(sub_url, sub_cache, _keep_submissions)
            except Exception as exc:  # network / parse failure for this ticker — record, continue
                n_failed += 1
                status_rows.append({"ticker": tk, "cik": cik10, "status": "FAILED",
                                    "rows": 0, "fields": 0, "origin": "network",
                                    "note": ("%s: %s" % (type(exc).__name__, exc))[:160]})
                continue

            acceptance = SEC._accession_to_acceptance(submissions)
            rows, fields = SEC._normalize_fundamentals_for_ticker(tk, int(cik), companyfacts, acceptance)
            fund_rows.extend(rows)
            origin = "cache" if cf_origin == "cache" and sub_origin == "cache" else "network"
            if origin == "cache":
                n_cached += 1
            else:
                n_collected += 1
            status_rows.append({"ticker": tk, "cik": cik10,
                                "status": "CACHED" if origin == "cache" else "COLLECTED",
                                "rows": len(rows), "fields": len(fields), "origin": origin,
                                "note": ""})
    finally:
        SEC.MAX_TOTAL_REQUESTS, SEC._OUTPUT_PERIOD_CAP = _saved_caps

    # Persist the normalized broad fundamentals panel to D: (large data, off-repo).
    if fund_rows:
        os.makedirs(DATA_ROOT, exist_ok=True)
        _write_csv(BROAD_FUNDAMENTALS_CSV, fund_rows, SEC.FUNDAMENTALS_COLUMNS)

    tickers_with_fund = len({r["ticker"] for r in fund_rows})
    summary = {
        "universe_size": len(universe),
        "tickers_with_cik": sum(1 for t in universe if cik_map.get(t)),
        "collected": n_collected, "cached": n_cached, "failed": n_failed, "no_cik": n_no_cik,
        "network_used": n_collected > 0,
        "network_requests": client.network_requests,
        "cache_hits": client.cache_hits,
        "fundamental_rows": len(fund_rows),
        "tickers_with_any_fundamental": tickers_with_fund,
        "broad_fundamentals_csv": _norm(BROAD_FUNDAMENTALS_CSV),
        "companyfacts_dir": _norm(DATA_COMPANYFACTS_DIR),
        "submissions_dir": _norm(DATA_SUBMISSIONS_DIR),
    }
    _write_json(BROAD_FUND_SUMMARY_JSON, summary)
    return fund_rows, status_rows, summary


# --------------------------------------------------------------------------- #
# 6-12. Broad-universe factor build + grading (reuse 7-C / 7-F / 7-G / 7-H).
#       Data sources are redirected to the broad price panel + broad fundamentals.
# --------------------------------------------------------------------------- #
def _grade(z: Optional[pd.DataFrame], realized: pd.DataFrame) -> Optional[Dict]:
    return C.grade_series(z, realized) if (z is not None and not z.empty) else None


def _ic(g: Optional[Dict]) -> Optional[float]:
    return (g or {}).get("mean_rank_ic")


def _t(g: Optional[Dict]) -> Optional[float]:
    return (g or {}).get("ic_t_stat")


def _znorm(raw: pd.DataFrame) -> pd.DataFrame:
    """Cross-sectional z-score WITHOUT sector neutralization (no PIT sectors for the broad
    universe). Per month: demean, z-score, clip +/-3 — via the unmodified 7-C normalizer."""
    return C.normalize_cross_sectional(raw, {}, sector_neutralize=False)


def build_broad_signal(universe: List[str]) -> Dict:
    """Construct broad-universe factors and grade them through the 7-B harness.
    Assumes the module data-source globals have been redirected to the broad data."""
    uset = set(universe)
    # NOTE: C.load_prices / C.load_annual_fundamentals bind their default path at import
    # (a Python default-arg, NOT the live module global), so we MUST pass the broad paths
    # explicitly here. G.build_3mo_quarters reads G.FUNDAMENTALS_CSV as a live module global,
    # which _redirect_data_sources() repoints, so the TTM panels pick up the broad data.
    prices = C.load_prices(path=BROAD_PRICE_PANEL_CSV, universe=uset)
    pf = C.build_price_factors(prices)
    realized = pf["forward"]
    monthly_close = pf["monthly_close"]
    month_end_date = pf["month_end_date"]

    annual = C.load_annual_fundamentals(path=BROAD_FUNDAMENTALS_CSV)
    annual_u = annual[annual["ticker"].isin(uset)].copy()

    # ---- TTM panels (reuse 7-G de-cumulation) + TTM factors (reuse 7-H). ----
    panels = H.build_ttm_panels(uset)
    ttm_raw, _ = H.build_ttm_factors(panels, annual_u, monthly_close, month_end_date, {})
    ttm_z = {k: _znorm(v) for k, v in ttm_raw.items()}

    # ---- Preserved price-momentum factors + low-vol risk descriptor (reuse 7-F). ----
    price_raw = F.build_upgraded_price_factors(pf)
    price_z = {k: _znorm(price_raw[k]) for k in ("mom_12_1", "mom_6_1", "mom_riskadj", "low_volatility")}

    all_z: Dict[str, pd.DataFrame] = {**ttm_z, **{k: price_z[k] for k in MOMENTUM_SUBFACTORS}}
    all_z["low_volatility"] = price_z["low_volatility"]   # graded standalone, NEVER in composite

    # ---- Grade every sub-factor; approval is breadth-and-history only (never IC). ----
    factor_results: Dict[str, Dict] = {}
    catalog_keys = [f.key for f in H.TTM_FACTORS] + list(MOMENTUM_SUBFACTORS) + ["low_volatility"]
    for key in catalog_keys:
        z = all_z.get(key, pd.DataFrame(columns=["month", "ticker", "z"]))
        graded = _grade(z, realized)
        cov = C.coverage_fraction(z, realized)
        n_per = (graded or {}).get("n_periods") or 0
        factor_results[key] = {"graded": graded, "coverage": cov,
                               "approved": bool(not z.empty and n_per >= MIN_APPROVE_PERIODS)}

    # ---- Equal-weight buckets (approved alpha sub-factors only; low-vol excluded). ----
    bucket_members = {
        "momentum": [k for k in MOMENTUM_SUBFACTORS if factor_results[k]["approved"]],
        "value_ttm": [k for k in _VALUE_KEYS if factor_results[k]["approved"]],
        "quality_ttm": [k for k in _QUALITY_KEYS if factor_results[k]["approved"]],
        "growth_ttm": [k for k in _GROWTH_KEYS if factor_results[k]["approved"]],
    }
    bucket_z = {b: F.combine_equal_weight(all_z, bucket_members[b]) for b in TTM_BUCKETS}
    bucket_graded = {b: _grade(bucket_z[b], realized) for b in TTM_BUCKETS}
    approved_buckets = [b for b in TTM_BUCKETS if not bucket_z[b].empty]

    # ---- Final composite = equal weight across approved alpha buckets only. ----
    composite = F.combine_equal_weight(bucket_z, approved_buckets)
    comp_graded = _grade(composite, realized)

    # ---- Baselines. price-only = canonical 12-1 momentum; momentum-only = momentum bucket. ----
    price_only_graded = factor_results["mom_12_1"]["graded"]
    momentum_only_graded = bucket_graded.get("momentum")

    composite_ic, composite_t = _ic(comp_graded), _t(comp_graded)
    price_only_ic = _ic(price_only_graded)
    momentum_ic = _ic(momentum_only_graded)
    incremental_ic = ((composite_ic - price_only_ic)
                      if (composite_ic is not None and price_only_ic is not None) else None)

    # ---- Leakage / placebo on the final composite. ----
    strictly_forward = C._forward_label_check(composite, realized) if not composite.empty \
        else {"strictly_forward": None}
    placebo = (C.placebo_test(comp_graded["_sbp"], comp_graded["_rbp"])
               if comp_graded else {"placebo_mean_ic": None})
    placebo_collapsed = C._placebo_collapsed(placebo)

    # ---- Portfolio diagnostics (quintile spread series, turnover, cost, Sharpe, MDD). ----
    port = (C.spread_series_from_frame(composite, realized, q=5)
            if not composite.empty else {"spreads": [], "turnovers": []})
    cost = (H7B.apply_transaction_costs(port["spreads"][1:], port["turnovers"], cost_bps=COST_BPS)
            if len(port["turnovers"]) else {"gross_mean": None, "net_mean": None, "mean_turnover": None})
    comp_sharpe = H7B.sharpe(port["spreads"], periods_per_year=12) if port["spreads"] else None
    comp_mdd = H7B.max_drawdown(port["spreads"]) if port["spreads"] else None

    # ---- Fundamental coverage: distinct tickers with >= 1 computable fundamental bucket. ----
    fund_tickers = set()
    for key, raw in ttm_raw.items():
        if not raw.empty:
            fund_tickers.update(raw["ticker"].unique().tolist())
    fund_coverage = len(fund_tickers)

    # ---- Regime diagnostic (diagnostic ONLY; never selection/weighting). ----
    regime_rows = _regime_diagnostic(comp_graded)

    return {
        "universe_size": len(universe),
        "n_price_names_in_panel": int(prices["ticker"].nunique()),
        "realized_periods": int(realized["month"].nunique()) if not realized.empty else 0,
        "panels": panels,
        "factor_results": factor_results,
        "bucket_members": bucket_members,
        "bucket_graded": bucket_graded,
        "approved_buckets": approved_buckets,
        "comp_graded": comp_graded,
        "price_only_graded": price_only_graded,
        "momentum_only_graded": momentum_only_graded,
        "composite_ic": composite_ic, "composite_t": composite_t,
        "price_only_ic": price_only_ic, "momentum_ic": momentum_ic,
        "incremental_ic": incremental_ic,
        "strictly_forward": strictly_forward, "placebo": placebo,
        "placebo_collapsed": placebo_collapsed,
        "cost": cost, "comp_sharpe": comp_sharpe, "comp_mdd": comp_mdd,
        "fund_coverage_tickers": fund_coverage,
        "regime_rows": regime_rows,
    }


def _regime_diagnostic(comp_graded: Optional[Dict]) -> List[Dict]:
    """Year-by-year composite IC as a regime diagnostic. Descriptive ONLY: regimes are
    never used to select or weight factors in this phase."""
    if not comp_graded:
        return [{"regime": "n/a", "metric": "mean_rank_ic", "value": "",
                 "note": "composite not gradable"}]
    yearly = comp_graded.get("yearly_ic") or {}
    rows = [{"regime": str(yr), "metric": "composite_mean_rank_ic", "value": _round(ic),
             "note": "diagnostic only; not used for factor selection or weighting"}
            for yr, ic in sorted(yearly.items())]
    if not rows:
        rows = [{"regime": "all", "metric": "composite_mean_rank_ic",
                 "value": _round(comp_graded.get("mean_rank_ic")),
                 "note": "diagnostic only; not used for factor selection or weighting"}]
    return rows


# --------------------------------------------------------------------------- #
# 15. Strict decision rule.
# --------------------------------------------------------------------------- #
def derive_recommendation(sig: Dict, price_threshold_met: bool) -> Tuple[str, str]:
    """Return (recommendation, survived_yes_no). Borderline is NEVER rounded up."""
    ic = sig.get("composite_ic")
    t = sig.get("composite_t")
    incr = sig.get("incremental_ic")
    fund_cov = sig.get("fund_coverage_tickers", 0)

    if (not price_threshold_met) or fund_cov < MIN_FUND_COVERAGE_TICKERS or ic is None:
        return REC_COVERAGE_BLOCKED, "NO"
    if ic <= 0:
        return REC_FAILED, "NO"
    survived = (ic > 0 and t is not None and t >= IC_T_GATE
                and incr is not None and incr >= INCREMENTAL_GATE)
    if survived:
        return REC_SURVIVED, "YES"
    return REC_WEAK, "NO"


# --------------------------------------------------------------------------- #
# Gate matrix.
# --------------------------------------------------------------------------- #
_SAFETY_GATES = {
    "free_sec_data_only", "no_paid_api", "no_packages_installed", "no_factor_weight_optimization",
    "no_factor_sign_flipping", "no_regime_selection_or_weighting", "low_volatility_excluded_from_composite",
    "no_future_fundamentals", "large_data_on_data_drive", "repo_outputs_are_summaries_only",
    "no_trading_order_automation", "no_paper_trader_gcp_broker", "not_committed", "not_pushed",
}


def build_gate_matrix(price_summary: Dict, fund_summary: Dict, sig: Dict,
                      recommendation: str) -> Tuple[List[Dict], Dict]:
    ic = sig.get("composite_ic")
    t = sig.get("composite_t")
    incr = sig.get("incremental_ic")
    fund_cov = sig.get("fund_coverage_tickers", 0)
    sf = (sig.get("strictly_forward") or {}).get("strictly_forward")

    def g(name, status, detail):
        return {"gate": name, "status": status, "detail": detail}

    rows = [
        # --- capability / data ---
        g("phase7i_usable_price_tickers", "PASS" if price_summary.get("threshold_met") else "FAIL",
          "%d usable price tickers (need >= %d)" % (price_summary.get("usable_price_tickers", 0),
                                                    MIN_USABLE_PRICE_TICKERS)),
        g("sec_fundamentals_collected", "PASS" if fund_summary.get("fundamental_rows", 0) > 0 else "FAIL",
          "%d fundamental rows for %d tickers" % (fund_summary.get("fundamental_rows", 0),
                                                  fund_summary.get("tickers_with_any_fundamental", 0))),
        g("fundamental_bucket_coverage", "PASS" if fund_cov >= MIN_FUND_COVERAGE_TICKERS else "FAIL",
          "%d tickers with >= 1 computable fundamental bucket (need >= %d)" % (fund_cov, MIN_FUND_COVERAGE_TICKERS)),
        g("min_alpha_buckets", "PASS" if len(sig.get("approved_buckets", [])) >= 2 else "FAIL",
          "approved alpha buckets: %s" % ",".join(sig.get("approved_buckets", []))),
        g("composite_gradable", "PASS" if ic is not None else "FAIL",
          "composite mean rank IC computed" if ic is not None else "composite not gradable"),
        # --- results (honest; may FAIL — that is the finding, not an error) ---
        g("composite_ic_positive", "PASS" if (ic is not None and ic > 0) else "FAIL",
          "composite IC = %s" % _round(ic)),
        g("composite_t_stat_gate", "PASS" if (t is not None and t >= IC_T_GATE) else "FAIL",
          "composite IC t-stat = %s (need >= %s)" % (_round(t), IC_T_GATE)),
        g("beats_price_only_baseline", "PASS" if (incr is not None and incr >= INCREMENTAL_GATE) else "FAIL",
          "incremental IC vs price-only = %s (need >= %s)" % (_round(incr), INCREMENTAL_GATE)),
        # --- validation ---
        g("strictly_forward_labels", "PASS" if sf else ("WARN" if sf is None else "FAIL"),
          "forward labels resolve strictly after the decision month"),
        g("placebo_collapses", "PASS" if sig.get("placebo_collapsed") else "WARN",
          "within-period label-permutation placebo collapses toward zero"),
        g("sector_neutralization_caveat", "WARN",
          "no point-in-time sector history for the broad universe; standardization is NOT sector-neutral"),
        g("survivorship_caveat", "WARN",
          "broad universe is current-as-of membership (price pilot of largest names), not point-in-time"),
        # --- safety ---
        g("free_sec_data_only", "PASS", "only SEC EDGAR public companyfacts/submissions JSON used"),
        g("no_paid_api", "PASS", "no paid API called"),
        g("no_packages_installed", "PASS", "stdlib urllib + pandas only; nothing installed"),
        g("no_factor_weight_optimization", "PASS", "equal weight within and across buckets"),
        g("no_factor_sign_flipping", "PASS", "factor signs a priori; never flipped to chase IC"),
        g("no_regime_selection_or_weighting", "PASS", "regimes diagnostic only"),
        g("low_volatility_excluded_from_composite", "PASS", "low-vol graded standalone as a risk descriptor only"),
        g("no_future_fundamentals", "PASS", "TTM availability = max of de-cumulation legs; activated only pre-month-end"),
        g("large_data_on_data_drive", "PASS", "raw SEC payloads + broad fundamentals under D: only"),
        g("repo_outputs_are_summaries_only", "PASS", "repo receives committed-safe scoreboards only"),
        g("no_trading_order_automation", "PASS", "no order/broker/automation logic"),
        g("no_paper_trader_gcp_broker", "PASS", "no Paper Trader / GCP / broker / deployment"),
        g("not_committed", "PASS", "not committed"),
        g("not_pushed", "PASS", "not pushed"),
    ]
    counts = {"pass": sum(1 for r in rows if r["status"] == "PASS"),
              "fail": sum(1 for r in rows if r["status"] == "FAIL"),
              "warn": sum(1 for r in rows if r["status"] == "WARN"),
              "safety_fail": any(r["status"] == "FAIL" and r["gate"] in _SAFETY_GATES for r in rows)}
    return rows, counts


# --------------------------------------------------------------------------- #
# Artifact writers.
# --------------------------------------------------------------------------- #
def _write_data_coverage(paths: _OutPaths, price_summary: Dict, fund_summary: Dict, sig: Dict) -> None:
    rows = [
        {"item": "usable_price_tickers", "scope": "phase7i", "count": price_summary.get("usable_price_tickers", 0),
         "detail": "threshold %d; met=%s" % (MIN_USABLE_PRICE_TICKERS, price_summary.get("threshold_met"))},
        {"item": "tickers_with_any_fundamental", "scope": "sec", "count": fund_summary.get("tickers_with_any_fundamental", 0),
         "detail": "SEC companyfacts normalized rows: %d" % fund_summary.get("fundamental_rows", 0)},
        {"item": "tickers_with_computable_fundamental_bucket", "scope": "factor", "count": sig.get("fund_coverage_tickers", 0),
         "detail": "threshold %d; met=%s" % (MIN_FUND_COVERAGE_TICKERS,
                                             sig.get("fund_coverage_tickers", 0) >= MIN_FUND_COVERAGE_TICKERS)},
        {"item": "price_names_in_panel", "scope": "factor", "count": sig.get("n_price_names_in_panel", 0),
         "detail": "non-benchmark tickers with usable price history in the broad panel"},
        {"item": "realized_label_periods", "scope": "factor", "count": sig.get("realized_periods", 0),
         "detail": "monthly forward-return periods"},
    ]
    for fld, p in (sig.get("panels") or {}).items():
        rows.append({"item": "ttm_field:%s" % fld, "scope": "ttm",
                     "count": p.get("tickers_with_ttm", 0),
                     "detail": "ttm_windows=%d continuity_ready=%d" % (p.get("total_ttm_windows", 0),
                                                                       p.get("continuity_ready_tickers", 0))})
    _write_csv(paths.data_coverage, rows, ["item", "scope", "count", "detail"])


def _write_collection_status(paths: _OutPaths, status_rows: List[Dict]) -> None:
    _write_csv(paths.collection_status, status_rows,
               ["ticker", "cik", "status", "rows", "fields", "origin", "note"])


def _write_factor_catalog(paths: _OutPaths) -> None:
    rows = []
    for f in H.TTM_FACTORS:
        rows.append({"factor": f.key, "bucket": f.bucket, "role": "alpha",
                     "definition": f.definition, "needs": ",".join(f.needs)})
    for k in MOMENTUM_SUBFACTORS:
        rows.append({"factor": k, "bucket": "momentum", "role": "alpha",
                     "definition": "price momentum (%s)" % k, "needs": "price"})
    rows.append({"factor": "low_volatility", "bucket": "risk", "role": "risk_descriptor",
                 "definition": "trailing return volatility (inverted)", "needs": "price"})
    _write_csv(paths.factor_catalog, rows, ["factor", "bucket", "role", "definition", "needs"])


def _write_factor_scoreboard(paths: _OutPaths, sig: Dict) -> None:
    rows = []
    for key, r in sig["factor_results"].items():
        g = r["graded"]
        rows.append({"factor": key,
                     "mean_rank_ic": _round(_ic(g)), "ic_t_stat": _round(_t(g)),
                     "n_periods": (g or {}).get("n_periods") or 0,
                     "coverage": _round(r["coverage"]), "approved": r["approved"]})
    rows.sort(key=lambda x: (x["mean_rank_ic"] if isinstance(x["mean_rank_ic"], (int, float)) else -9), reverse=True)
    _write_csv(paths.factor_scoreboard, rows,
               ["factor", "mean_rank_ic", "ic_t_stat", "n_periods", "coverage", "approved"])


def _write_bucket_scoreboard(paths: _OutPaths, sig: Dict) -> None:
    rows = []
    for b in TTM_BUCKETS:
        g = sig["bucket_graded"].get(b)
        rows.append({"bucket": b, "members": ",".join(sig["bucket_members"].get(b, [])),
                     "mean_rank_ic": _round(_ic(g)), "ic_t_stat": _round(_t(g)),
                     "n_periods": (g or {}).get("n_periods") or 0,
                     "approved": b in sig["approved_buckets"]})
    _write_csv(paths.bucket_scoreboard, rows,
               ["bucket", "members", "mean_rank_ic", "ic_t_stat", "n_periods", "approved"])


def _write_composite_scoreboard(paths: _OutPaths, sig: Dict) -> None:
    def row(series, graded, note):
        ic = _ic(graded)
        return {"series": series, "mean_rank_ic": _round(ic), "ic_t_stat": _round(_t(graded)),
                "n_periods": (graded or {}).get("n_periods") or 0,
                "incremental_vs_price_only": _round((ic - sig["price_only_ic"])
                                                    if (ic is not None and sig["price_only_ic"] is not None) else None),
                "quintile_spread": _round((graded or {}).get("quintile_spread")),
                "decile_spread": _round((graded or {}).get("decile_spread")),
                "note": note}
    rows = [
        row("broad_price_only_baseline", sig["price_only_graded"], "canonical 12-1 momentum (single price factor)"),
        row("broad_momentum_only", sig["momentum_only_graded"], "equal-weight momentum bucket"),
        row("broad_final_composite", sig["comp_graded"], "equal weight across approved alpha buckets"),
        {"series": "phase7f_128name_context", "mean_rank_ic": _round(PHASE7F_CONTEXT_IC),
         "ic_t_stat": _round(PHASE7F_CONTEXT_T), "n_periods": "", "incremental_vs_price_only": "",
         "quintile_spread": "", "decile_spread": "", "note": "CONTEXT ONLY — not the primary comparison"},
    ]
    _write_csv(paths.composite_scoreboard, rows,
               ["series", "mean_rank_ic", "ic_t_stat", "n_periods", "incremental_vs_price_only",
                "quintile_spread", "decile_spread", "note"])


def _write_regime_scoreboard(paths: _OutPaths, sig: Dict) -> None:
    _write_csv(paths.regime_scoreboard, sig["regime_rows"], ["regime", "metric", "value", "note"])


def _write_gate_matrix(paths: _OutPaths, gates: List[Dict]) -> None:
    _write_csv(paths.gate_matrix, gates, ["gate", "status", "detail"])


def _write_next_plan(paths: _OutPaths, recommendation: str, survived: str, sig: Dict) -> None:
    if recommendation == REC_SURVIVED:
        steps = [{"step": "phase7k_broad_universe_robustness", "scope": "LOCAL_RETEST",
                  "detail": ("Signal survived on the broad universe. Next: harden it — add "
                             "delisted-name prices + point-in-time index membership + PIT sectors, "
                             "re-grade sector-neutral, and stress walk-forward / cost / capacity. "
                             "Still equal weight, no sign flipping, no optimization.")}]
    elif recommendation in (REC_WEAK, REC_FAILED):
        steps = [{"step": "phase7k_data_foundation_not_polishing", "scope": "DATA",
                  "detail": ("Signal did not survive on the broad universe. Do NOT resume local "
                             "factor polishing. The next best path is a genuinely survivorship-free "
                             "data foundation: delisted-name daily prices + point-in-time index "
                             "membership + point-in-time sector history (free sources: SEC + "
                             "stooq/yfinance delisted where available), then a sector-neutral "
                             "re-grade. If that is infeasible free, the honest conclusion is that "
                             "the multifactor edge is not reliably demonstrable on free data.")}]
    else:  # coverage blocked
        steps = [{"step": "phase7k_expand_fundamental_coverage", "scope": "DATA",
                  "detail": ("Fundamental coverage too low to test. Re-run collection (cache-first) "
                             "for the remaining price-covered tickers, verify CIK mapping, and widen "
                             "the SEC concept set before retesting.")}]
    _write_json(paths.next_plan, {
        "from_phase": "7-J", "recommendation": recommendation,
        "broad_universe_signal_survived": survived,
        "broad_final_composite_ic": _round(sig.get("composite_ic")),
        "broad_final_composite_t": _round(sig.get("composite_t")),
        "broad_price_only_ic": _round(sig.get("price_only_ic")),
        "incremental_ic_vs_price_only": _round(sig.get("incremental_ic")),
        "fundamental_coverage_tickers": sig.get("fund_coverage_tickers"),
        "next_steps": steps,
        "hard_constraints": [
            "free sources only", "no paid APIs", "do not install packages",
            "large data on D: only", "repo outputs are summaries only",
            "no factor-weight optimization", "no factor-sign flipping",
            "no regime selection or weighting", "no trading/order/automation",
            "no Paper Trader/GCP/broker/deployment", "do not commit", "do not push"],
    })


# --------------------------------------------------------------------------- #
# Report assembly.
# --------------------------------------------------------------------------- #
def _assemble_report(paths, price_summary, fund_summary, sig, gates, gate_counts,
                     recommendation, survived) -> Dict:
    return {
        "phase": PHASE, "objective": OBJECTIVE,
        "question": "DID THE SIGNAL SURVIVE ON THE BROADER 296-NAME UNIVERSE?",
        "broad_universe_signal_survived": survived,
        "recommendation": recommendation,
        "allowed_recommendations": list(ALLOWED_RECOMMENDATIONS),
        "phase7i_price_coverage": price_summary,
        "sec_fundamental_collection": fund_summary,
        "results": {
            "usable_price_tickers": price_summary.get("usable_price_tickers"),
            "sec_fundamental_rows": fund_summary.get("fundamental_rows"),
            "tickers_with_any_fundamental": fund_summary.get("tickers_with_any_fundamental"),
            "tickers_with_usable_ttm_fundamentals": sig.get("fund_coverage_tickers"),
            "broad_price_only_baseline_ic": _round(sig.get("price_only_ic")),
            "broad_momentum_only_ic": _round(sig.get("momentum_ic")),
            "broad_final_composite_ic": _round(sig.get("composite_ic")),
            "broad_final_composite_t": _round(sig.get("composite_t")),
            "incremental_ic_vs_price_only": _round(sig.get("incremental_ic")),
            "approved_alpha_buckets": sig.get("approved_buckets"),
            "composite_quintile_spread": _round((sig.get("comp_graded") or {}).get("quintile_spread")),
            "composite_decile_spread": _round((sig.get("comp_graded") or {}).get("decile_spread")),
            "composite_sharpe": _round(sig.get("comp_sharpe")),
            "composite_max_drawdown": _round(sig.get("comp_mdd")),
            "phase7f_128name_context_ic": PHASE7F_CONTEXT_IC,
            "phase7f_128name_context_t": PHASE7F_CONTEXT_T,
        },
        "decision_rule": {
            "ic_t_gate": IC_T_GATE, "incremental_gate": INCREMENTAL_GATE,
            "min_usable_price_tickers": MIN_USABLE_PRICE_TICKERS,
            "min_fundamental_coverage_tickers": MIN_FUND_COVERAGE_TICKERS,
            "borderline_rounded_up": False,
        },
        "validation": {
            "strictly_forward": (sig.get("strictly_forward") or {}).get("strictly_forward"),
            "placebo_mean_ic": _round((sig.get("placebo") or {}).get("placebo_mean_ic")),
            "placebo_collapsed": sig.get("placebo_collapsed"),
            "cost_bps": COST_BPS,
            "net_spread_mean": _round((sig.get("cost") or {}).get("net_mean")),
            "mean_turnover": _round((sig.get("cost") or {}).get("mean_turnover")),
        },
        "gate_matrix_summary": gate_counts,
        "large_data_root": _norm(DATA_ROOT),
        "artifacts": {
            "report": _rel(paths.report), "data_coverage": _rel(paths.data_coverage),
            "collection_status": _rel(paths.collection_status), "factor_catalog": _rel(paths.factor_catalog),
            "factor_scoreboard": _rel(paths.factor_scoreboard), "bucket_scoreboard": _rel(paths.bucket_scoreboard),
            "composite_scoreboard": _rel(paths.composite_scoreboard), "regime_scoreboard": _rel(paths.regime_scoreboard),
            "gate_matrix": _rel(paths.gate_matrix), "next_plan": _rel(paths.next_plan),
        },
        "safety": {
            "live_data": "SEC EDGAR public companyfacts/submissions JSON only",
            "paid_api_used": False, "packages_installed": False,
            "factor_weight_optimization": False, "factor_sign_flipping": False,
            "regime_selection_or_weighting": False, "low_volatility_in_composite": False,
            "future_fundamentals_used": False, "repo_received_large_data": False,
            "wrote_to_data_drive": fund_summary.get("fundamental_rows", 0) > 0,
            "overwrote_existing_d_drive_data": False,
            "trading_order_automation": False, "paper_trader_gcp_broker_touched": False,
            "committed": False, "pushed": False,
        },
    }


def _error_report(msg: str) -> Dict:
    return {"phase": PHASE, "recommendation": REC_ERROR,
            "allowed_recommendations": list(ALLOWED_RECOMMENDATIONS),
            "broad_universe_signal_survived": "NO", "error": msg}


# --------------------------------------------------------------------------- #
# Orchestration.
# --------------------------------------------------------------------------- #
def _redirect_data_sources() -> None:
    """Repoint G.FUNDAMENTALS_CSV, which G.build_3mo_quarters reads as a LIVE module global
    (so H.build_ttm_panels de-cumulates the broad fundamentals). C's loaders are called with
    explicit broad paths in build_broad_signal because their defaults bind at import; the C
    global assignments below are belt-and-suspenders only. F's price factors are price-only."""
    G.FUNDAMENTALS_CSV = BROAD_FUNDAMENTALS_CSV
    C.PRICE_CSV = BROAD_PRICE_PANEL_CSV
    C.FUNDAMENTALS_CSV = BROAD_FUNDAMENTALS_CSV


def _run(paths: _OutPaths, *, network: bool) -> Dict:
    # 1-3. Phase 7-I usable price tickers.
    universe, price_summary = read_phase7i_usable_tickers()
    if not universe:
        report = _error_report("Phase 7-I collection_status.csv missing or no usable price tickers.")
        _write_json(paths.report, report)
        return report

    # 4. CIK mapping.
    cik_map = load_ticker_cik_map()

    # 5. Collect / reuse SEC fundamentals -> broad fundamentals CSV on D:.
    fund_rows, status_rows, fund_summary = collect_broad_fundamentals(universe, cik_map, network=network)
    _write_collection_status(paths, status_rows)

    if not fund_rows:
        # Nothing collected — cannot test. Honest BLOCKED (or ERROR if no network and no cache).
        sig = {"composite_ic": None, "composite_t": None, "price_only_ic": None, "momentum_ic": None,
               "incremental_ic": None, "fund_coverage_tickers": 0, "approved_buckets": [],
               "comp_graded": None, "price_only_graded": None, "momentum_only_graded": None,
               "bucket_graded": {}, "bucket_members": {}, "factor_results": {}, "panels": {},
               "strictly_forward": {"strictly_forward": None}, "placebo": {}, "placebo_collapsed": False,
               "cost": {}, "comp_sharpe": None, "comp_mdd": None, "regime_rows": _regime_diagnostic(None),
               "n_price_names_in_panel": 0, "realized_periods": 0}
        recommendation, survived = REC_COVERAGE_BLOCKED, "NO"
        gates, counts = build_gate_matrix(price_summary, fund_summary, sig, recommendation)
        _write_data_coverage(paths, price_summary, fund_summary, sig)
        _write_factor_catalog(paths)
        _write_factor_scoreboard(paths, sig)
        _write_bucket_scoreboard(paths, sig)
        _write_composite_scoreboard(paths, sig)
        _write_regime_scoreboard(paths, sig)
        _write_gate_matrix(paths, gates)
        _write_next_plan(paths, recommendation, survived, sig)
        report = _assemble_report(paths, price_summary, fund_summary, sig, gates, counts,
                                  recommendation, survived)
        _write_json(paths.report, report)
        return report

    # 6-12. Redirect data sources, build factors, grade.
    _redirect_data_sources()
    sig = build_broad_signal(universe)

    # 15. Decision.
    recommendation, survived = derive_recommendation(sig, price_summary.get("threshold_met", False))
    gates, counts = build_gate_matrix(price_summary, fund_summary, sig, recommendation)

    # Artifacts.
    _write_data_coverage(paths, price_summary, fund_summary, sig)
    _write_factor_catalog(paths)
    _write_factor_scoreboard(paths, sig)
    _write_bucket_scoreboard(paths, sig)
    _write_composite_scoreboard(paths, sig)
    _write_regime_scoreboard(paths, sig)
    _write_gate_matrix(paths, gates)
    _write_next_plan(paths, recommendation, survived, sig)

    report = _assemble_report(paths, price_summary, fund_summary, sig, gates, counts,
                              recommendation, survived)
    _write_json(paths.report, report)
    return report


def run(out_dir: Optional[str] = None, *, network: bool = True) -> Dict:
    paths = _OutPaths(base=Path(out_dir) if out_dir else _OUT_DIR_DEFAULT)
    paths.base.mkdir(parents=True, exist_ok=True)
    try:
        return _run(paths, network=network)
    except Exception as exc:  # noqa: BLE001 — record, never raise out of the runner
        import traceback
        report = _error_report("%s: %s\n%s" % (type(exc).__name__, exc, traceback.format_exc()))
        _write_json(paths.report, report)
        return report


def _print_summary(report: Dict) -> None:
    print("[%s] recommendation = %s" % (PHASE, report.get("recommendation")))
    print("  signal survived on broad universe : %s" % report.get("broad_universe_signal_survived"))
    res = report.get("results", {})
    if res:
        print("  usable price tickers              : %s" % res.get("usable_price_tickers"))
        print("  sec fundamental rows              : %s" % res.get("sec_fundamental_rows"))
        print("  tickers w/ usable TTM fundamentals: %s" % res.get("tickers_with_usable_ttm_fundamentals"))
        print("  broad price-only baseline IC      : %s" % res.get("broad_price_only_baseline_ic"))
        print("  broad momentum-only IC            : %s" % res.get("broad_momentum_only_ic"))
        print("  broad final composite IC          : %s" % res.get("broad_final_composite_ic"))
        print("  broad final composite t-stat      : %s" % res.get("broad_final_composite_t"))
        print("  incremental IC vs price baseline  : %s" % res.get("incremental_ic_vs_price_only"))
    gc = report.get("gate_matrix_summary", {})
    if gc:
        print("  gates PASS=%s FAIL=%s WARN=%s safety_fail=%s" % (
            gc.get("pass"), gc.get("fail"), gc.get("warn"), gc.get("safety_fail")))


def _parse_args(argv):
    ap = argparse.ArgumentParser(description="Phase 7-J broad universe fundamentals collection + signal retest.")
    ap.add_argument("--out-dir", default=None, help="output directory for committed-safe artifacts")
    ap.add_argument("--no-network", action="store_true",
                    help="use only cached SEC payloads; make no network call")
    return ap.parse_args(argv)


def main(argv=None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    report = run(out_dir=args.out_dir, network=not args.no_network)
    _print_summary(report)
    return 0 if report.get("recommendation") in ALLOWED_RECOMMENDATIONS else 1


if __name__ == "__main__":
    raise SystemExit(main())
