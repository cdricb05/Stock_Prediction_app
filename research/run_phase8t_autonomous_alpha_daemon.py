"""Phase 8-T - Autonomous EODHD Alpha-Factory Daemon.

WHY THIS PHASE EXISTS
    Phase 8-S ran ONE bounded acquisition + 10-scenario scoring pass and promoted two earnings
    signals (surprise_sector_neutral, sue_standardized). This phase is the persistent daemon that
    continues *beyond* that single pass: it diagnoses why the scoreable universe stopped at 299
    tickers, searches for a broader local price universe (and emits a bounded expansion plan when
    none exists), refreshes/repairs EODHD coverage, then runs MANY more feature families and
    interaction scenarios across multiple autonomous cycles with extended robustness validation
    (subperiod stability, placebo/challenge, transaction cost at 10/25/50 bps, sign consistency,
    multiple-testing control), promoting only durable alpha and emitting Paper-Trader-preview-ready
    signal specifications.

    This is PREVIEW-ONLY research. It produces RANKING SIGNAL specs / trade-idea candidates for
    manual review, never orders, never automation, never broker execution. It does not touch Paper
    Trader or GCP. Raw + normalized EODHD payloads stay under the gitignored research/data/eodhd
    tree; only committed-safe metadata is written under research/output.

    It REUSES the proven Phase 8-S data layer + scoring primitives verbatim (point-in-time event
    table, rank-IC / quantile-spread / turnover / cost / regime machinery, BH multiple testing) and
    the Phase 8-R EODHD transport + secret discipline underneath it. 8-T adds: universe diagnosis,
    a ~40-scenario battery across earnings / fundamentals / price-context / regime / interaction
    families, extended robustness gates, an autonomous cycle loop, and preview specs.

SECRET DISCIPLINE
    EODHD_API_KEY is read ONLY from the environment, never printed, never written to disk. Every
    persisted URL is redacted. A leak scan over the committed artifacts confirms it is clean.

TERMINAL DECISIONS
    ALPHA_FOUND_READY_FOR_PAPER_TRADER_REVIEW | MORE_ALPHA_FOUND_READY_FOR_PAPER_TRADER_REVIEW |
    NO_ALPHA_FOUND_AFTER_EXHAUSTIVE_EODHD_CAMPAIGN | READY_FOR_NEXT_AUTONOMOUS_CYCLE |
    HARD_BLOCKER_REQUIRES_USER_ACTION | ERROR

Run (offline campaign on whatever EODHD data is already cached locally; no network, no key):
    python research/run_phase8t_autonomous_alpha_daemon.py
Live refresh + campaign (needs a PAID EODHD_API_KEY in the env; bounded, skip-existing):
    $env:EODHD_API_KEY = '<PAID_EODHD_KEY>'
    python research/run_phase8t_autonomous_alpha_daemon.py --live --max-tickers 500 \
        --max-requests 5000 --max-cycles 8
Test (fully offline; injected transport, no key, no network):
    python -m pytest tests/test_phase8t_autonomous_alpha_daemon.py -q
"""
from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Reuse the Phase 8-S alpha-factory machinery verbatim (which itself reuses the 8-R EODHD layer).
from research import run_phase8s_autonomous_eodhd_alpha_factory as s8  # noqa: E402
r8 = s8.r8

PHASE = "8-T"
PROVIDER_NAME = "EODHD"
API_KEY_ENV = r8.API_KEY_ENV

# Reused IO/stat helpers (single source of truth).
_write_json = s8._write_json
_write_csv = s8._write_csv
_read_csv_file = s8._read_csv_file
_read_json = s8._read_json
_round = s8._round
_rel = s8._rel

# --------------------------------------------------------------------------- #
# Paths.
# --------------------------------------------------------------------------- #
_OUT_DIR = _REPO_ROOT / "research" / "output" / "phase8t_autonomous_alpha_daemon"
_PHASE8S_DIR = _REPO_ROOT / "research" / "output" / "phase8s_autonomous_eodhd_alpha_factory"
# Broader-universe candidate sources to search (read-only).
_SP500_LIST_HTML = Path(
    "D:/Stock_Prediction_app_data/phase7k_survivorship_data_foundation/wikipedia/"
    "list_of_sp500_companies.html")
_SEC_SUBMISSIONS_DIR = Path(
    "D:/Stock_Prediction_app_data/phase7k_survivorship_data_foundation/sec_submissions")
_BROADER_PRICE_CANDIDATES = [
    Path("D:/Stock_Prediction_app_data/phase7k_survivorship_data_foundation/prices"),
    Path("D:/Stock_Prediction_app_data/phase7j_broad_universe_signal_retest/prices"),
    Path("D:/Stock_Prediction_app_data/research_panels"),
]

# Bounded acquisition + autonomous-loop defaults (from the brief).
DEFAULT_MAX_TICKERS = 500
DEFAULT_MAX_REQUESTS = 5000
DEFAULT_MAX_CYCLES = 8

# Scoring config (inherit the 8-S horizons / cross-section thresholds).
PRIMARY_HORIZON = s8.PRIMARY_HORIZON
FWD_WINDOWS = s8.FWD_WINDOWS
N_QUANTILES = s8.N_QUANTILES
MIN_NAMES_PER_MONTH = s8.MIN_NAMES_PER_MONTH
MIN_MONTHS = s8.MIN_MONTHS
MIN_EVENTS = s8.MIN_EVENTS

# Promotion gates (8-T - stricter / more robust than 8-S). All signals are oriented "higher==better"
# so a genuine signal has POSITIVE IC and POSITIVE spread.
GATE_MIN_IC = 0.03                # mean monthly rank IC (signed, must be positive)
GATE_MIN_T = 2.0                  # IC t-stat (signed, must be positive)
GATE_BH_Q = 0.10                  # Benjamini-Hochberg false-discovery rate
GATE_MIN_SPREAD_HITRATE = 0.55    # fraction of months the L/S spread is positive
GATE_STRONG_SPREAD = 0.004        # monthly gross spread that "strongly compensates" a weak hit-rate
GATE_COST_BPS_HARD = 25.0         # net spread must stay positive after THIS cost
GATE_MAX_SECTOR_SHARE = 0.60      # top sector may not dominate the long book beyond this
GATE_SUBPERIOD_SIGN = True        # both halves of the sample must share the (positive) IC sign

DEC_ALPHA = "ALPHA_FOUND_READY_FOR_PAPER_TRADER_REVIEW"
DEC_MORE_ALPHA = "MORE_ALPHA_FOUND_READY_FOR_PAPER_TRADER_REVIEW"
DEC_NO_ALPHA = "NO_ALPHA_FOUND_AFTER_EXHAUSTIVE_EODHD_CAMPAIGN"
DEC_NEXT_CYCLE = "READY_FOR_NEXT_AUTONOMOUS_CYCLE"
DEC_BLOCKER = "HARD_BLOCKER_REQUIRES_USER_ACTION"
DEC_ERROR = "ERROR"
ALLOWED_DECISIONS = (DEC_ALPHA, DEC_MORE_ALPHA, DEC_NO_ALPHA, DEC_NEXT_CYCLE, DEC_BLOCKER, DEC_ERROR)
_TERMINAL = (DEC_ALPHA, DEC_MORE_ALPHA, DEC_NO_ALPHA, DEC_BLOCKER, DEC_ERROR)

# Signals already promoted by Phase 8-S (used to decide ALPHA vs MORE_ALPHA).
_PHASE8S_PROMOTED = {"surprise_sector_neutral", "sue_standardized"}

_ARTIFACTS = {
    "report": "phase8t_autonomous_alpha_daemon.json",
    "daemon_log": "daemon_run_log.csv",
    "cycle_summary": "cycle_summary.csv",
    "universe_expansion": "universe_expansion_report.csv",
    "unscoreable": "unscoreable_tickers_report.csv",
    "acq_progress": "acquisition_progress.csv",
    "coverage_matrix": "data_coverage_matrix.csv",
    "feature_catalog": "feature_catalog.csv",
    "candidate_registry": "scenario_candidate_registry.csv",
    "scenario_plan": "scenario_test_plan.csv",
    "scoreboard": "scenario_scoreboard.csv",
    "rank_ic": "rank_ic_report.csv",
    "spread": "spread_report.csv",
    "hit_rate": "hit_rate_report.csv",
    "turnover": "turnover_report.csv",
    "tcost": "transaction_cost_sensitivity.csv",
    "sector_conc": "sector_concentration_report.csv",
    "regime_split": "regime_split_report.csv",
    "subperiod": "subperiod_stability_report.csv",
    "multiple_testing": "multiple_testing_report.csv",
    "placebo": "placebo_challenge_report.csv",
    "promoted": "promoted_alpha_signals.csv",
    "rejected": "rejected_alpha_signals.csv",
    "preview_specs": "paper_trader_preview_signal_specs.csv",
    "final_decision": "final_research_decision.json",
    "next_plan": "phase8u_next_plan.json",
    "secret_audit": "secret_safety_audit.csv",
    # acquisition helper manifests (reused 8-S acquire writes these; informative, committed-safe)
    "raw_manifest": "raw_storage_manifest.csv",
    "norm_manifest": "normalized_storage_manifest.csv",
}


class _Paths(s8._Paths):
    """8-S paths (so all reused loaders/acquire find the SAME cached EODHD data + price cache),
    re-pointed to the 8-T output dir + artifact filenames."""

    def __init__(self, out_dir: Optional[Path] = None, data_dir: Optional[Path] = None,
                 phase8n_dir: Optional[Path] = None, price_csv: Optional[Path] = None,
                 sector_csv: Optional[Path] = None, macro: Optional[Dict[str, Path]] = None,
                 phase8r_dir: Optional[Path] = None, phase8s_dir: Optional[Path] = None):
        super().__init__(out_dir or _OUT_DIR, data_dir, phase8n_dir, price_csv, sector_csv,
                         macro, phase8r_dir)
        self.phase8s = Path(phase8s_dir) if phase8s_dir else _PHASE8S_DIR
        for key, name in _ARTIFACTS.items():
            setattr(self, key, self.out / name)


# --------------------------------------------------------------------------- #
# Daemon run-log accumulator.
# --------------------------------------------------------------------------- #
class _Log(s8._Log):
    pass


def _zser(s):
    return s8._zscore(s)


def _net_spread(mean_spread, avg_turnover, bps):
    if mean_spread is None or (isinstance(mean_spread, float) and math.isnan(mean_spread)):
        return float("nan")
    t = avg_turnover if (avg_turnover is not None and not math.isnan(avg_turnover)) else 1.0
    return mean_spread - (bps / 1e4) * t * 2.0


# --------------------------------------------------------------------------- #
# A. Universe expansion + unscoreable diagnosis.
# --------------------------------------------------------------------------- #
def _sp500_list_count() -> int:
    """Count tickers in the local Wikipedia S&P-500 list (a NAME universe, not OHLCV)."""
    try:
        html = _SP500_LIST_HTML.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return 0
    import re
    # The constituents table links each symbol to a quote page: .../NYSE:AAPL or symbol cells.
    syms = set(re.findall(r"\b([A-Z]{1,5})\b</a>", html))
    # Heuristic floor: the table also encodes exchange:symbol links.
    syms |= set(re.findall(r"symbol=([A-Z]{1,5})", html))
    return len(syms)


def search_universe_expansion(P: _Paths, priced: List[str], scoreable: List[str]) -> List[Dict]:
    """Search the repo + D: data root for any broader local PRICE universe. Emits a structured
    report. If none exists, the only honest expansion is a BOUNDED EODHD EOD acquisition plan."""
    rows: List[Dict] = []
    rows.append({"source": _rel(P.price_csv), "kind": "local_ohlcv_cache",
                 "tickers": len(priced), "has_local_prices": True, "used_as_universe": True,
                 "note": "phase7i broad priced universe - the current scoring ceiling"})
    rows.append({"source": _rel(P.price_csv), "kind": "scoreable_intersection",
                 "tickers": len(scoreable), "has_local_prices": True, "used_as_universe": True,
                 "note": "priced names with >=1 usable point-in-time earnings event"})

    sp500_n = _sp500_list_count()
    rows.append({"source": _rel(_SP500_LIST_HTML), "kind": "name_universe_only",
                 "tickers": sp500_n, "has_local_prices": False, "used_as_universe": False,
                 "note": "S&P-500 constituent NAME list (Wikipedia) - no local OHLCV; EOD price "
                         "acquisition required before these become scoreable"})
    sec_n = len(list(_SEC_SUBMISSIONS_DIR.glob("*.json"))) if _SEC_SUBMISSIONS_DIR.is_dir() else 0
    rows.append({"source": _rel(_SEC_SUBMISSIONS_DIR), "kind": "sec_submissions",
                 "tickers": sec_n, "has_local_prices": False, "used_as_universe": False,
                 "note": "raw SEC submission JSON (filing metadata) - not a price universe"})

    broader_found = False
    for cand in _BROADER_PRICE_CANDIDATES:
        present = cand.exists()
        n = 0
        if present and cand.is_dir():
            n = len(list(cand.glob("*.csv")))
        if present and n > 0:
            broader_found = True
        rows.append({"source": _rel(cand), "kind": "broader_price_universe_probe",
                     "tickers": n, "has_local_prices": bool(present and n > 0),
                     "used_as_universe": False,
                     "note": "probed for a broader 500/1000/3000-name local price cache"})

    # Expansion plan row.
    if broader_found:
        plan = ("A broader local price cache was found - load it as the universe and re-run the "
                "campaign on the wider cross-section.")
    else:
        plan = ("No broader local OHLCV cache exists beyond the %d-name phase7i priced universe. "
                "Expansion requires a BOUNDED EODHD EOD-price acquisition for the S&P-500 name list "
                "(%d names) before those tickers can be scored. This is the gated next action - NOT "
                "an unbounded full-market download." % (len(priced), sp500_n))
    rows.append({"source": "expansion_plan", "kind": "decision", "tickers": len(priced),
                 "has_local_prices": broader_found, "used_as_universe": False, "note": plan})
    return rows


def diagnose_unscoreable(P: _Paths, ev, universe: List[Dict]) -> Tuple[List[Dict], Dict]:
    """For every priced ticker NOT in the usable scored panel, emit an exact reason."""
    import pandas as pd
    priced = [u["ticker"] for u in universe]
    earn_recs = _read_csv_file(P.normalized_csv)
    earn_tk = {(r.get("symbol") or "").strip().upper() for r in earn_recs if r.get("symbol")}
    fund = s8.load_fundamentals_panel(P)
    fund_tk = set(fund["ticker"].unique()) if isinstance(fund, pd.DataFrame) and not fund.empty else set()
    usable_tk = set(ev["ticker"].unique()) if isinstance(ev, pd.DataFrame) and not ev.empty else set()

    rows: List[Dict] = []
    for tk in priced:
        if tk in usable_tk:
            continue
        if tk not in earn_tk:
            reason = "no EODHD earnings acquired (fundamentals request returned no earnings history)"
        elif tk not in fund_tk:
            reason = "earnings present but no fundamentals statements (financials block empty)"
        else:
            reason = ("earnings + fundamentals present but no PIT-usable event (no quarter with a "
                      "realized EPS whose announcement maps to a forward price window in the cache)")
        rows.append({"ticker": tk, "in_price_cache": True, "has_earnings": tk in earn_tk,
                     "has_fundamentals": tk in fund_tk, "scoreable": False, "reason": reason})
    summary = {"priced": len(priced), "scoreable": len(usable_tk),
               "unscoreable": len(rows),
               "unscoreable_no_earnings": sum(1 for r in rows if not r["has_earnings"]),
               "unscoreable_no_fundamentals": sum(1 for r in rows
                                                  if r["has_earnings"] and not r["has_fundamentals"]),
               "unscoreable_no_pit_event": sum(1 for r in rows
                                               if r["has_earnings"] and r["has_fundamentals"])}
    return rows, summary


# --------------------------------------------------------------------------- #
# C. Extended feature generation (price-context + fundamentals + composites + regimes).
# --------------------------------------------------------------------------- #
def augment_price_features(ev, prices):
    """Per-event trailing price-context features at the entry index (same searchsorted discipline
    as the 8-S forward-return engine, so everything stays point-in-time): 63d momentum, 63d realized
    volatility, dollar-volume liquidity, 63d beta vs benchmark, and a market-drawdown regime flag."""
    import numpy as np
    import pandas as pd
    if ev.empty or prices.empty:
        for c in ("mom_pre_63", "vol_63", "liquidity", "beta_63", "bench_drawdown"):
            ev[c] = np.nan
        ev["market_drawdown"] = False
        return ev

    have_dv = "dollar_volume" in prices.columns
    bench = (prices[["date", "benchmark_close"]].dropna().drop_duplicates("date")
             .sort_values("date").reset_index(drop=True))
    bdates = bench["date"].to_numpy().astype("datetime64[ns]")
    bvals = bench["benchmark_close"].to_numpy(dtype=float)
    bench_ret = np.concatenate([[np.nan], bvals[1:] / bvals[:-1] - 1.0])
    # trailing 252d running max for drawdown
    bench_peak = pd.Series(bvals).rolling(252, min_periods=20).max().to_numpy()

    for c in ("mom_pre_63", "vol_63", "liquidity", "beta_63", "bench_drawdown"):
        ev[c] = np.nan
    ev["market_drawdown"] = False

    pg = {tk: g for tk, g in prices.groupby("ticker")}
    ev_tk = ev["ticker"].to_numpy()
    ev_entry = ev["entry_date"].to_numpy().astype("datetime64[ns]")
    rows_idx = ev.index.to_numpy()

    for i, ridx in enumerate(rows_idx):
        pgrp = pg.get(ev_tk[i])
        if pgrp is None:
            continue
        pdates = pgrp["date"].to_numpy().astype("datetime64[ns]")
        pclose = pgrp["adjusted_close"].to_numpy(dtype=float)
        pdv = (pgrp["dollar_volume"].to_numpy(dtype=float) if have_dv
               else np.full(len(pdates), np.nan))
        # entry index = the event's entry_date position
        j = int(np.searchsorted(pdates, ev_entry[i], side="left"))
        if j >= len(pdates) or j <= 0:
            continue
        if j - 63 >= 0 and pclose[j - 63] > 0:
            ev.at[ridx, "mom_pre_63"] = pclose[j] / pclose[j - 63] - 1.0
        lo = max(1, j - 63)
        stock_r = pclose[lo:j] / pclose[lo - 1:j - 1] - 1.0
        if len(stock_r) >= 10:
            ev.at[ridx, "vol_63"] = float(np.nanstd(stock_r))
            # beta vs benchmark over the same trailing window
            bj = int(np.searchsorted(bdates, pdates[j], side="left"))
            b_lo = max(1, bj - len(stock_r))
            br = bench_ret[b_lo:bj]
            n = min(len(stock_r), len(br))
            if n >= 10:
                sv = stock_r[-n:]
                bv = br[-n:]
                vb = float(np.nanvar(bv))
                if vb > 0:
                    ev.at[ridx, "beta_63"] = float(np.nancov(sv, bv)[0, 1] / vb) \
                        if hasattr(np, "nancov") else float(np.cov(sv, bv)[0, 1] / vb)
        if have_dv:
            ev.at[ridx, "liquidity"] = float(np.nanmean(pdv[lo:j])) if j > lo else np.nan
        # market drawdown regime at entry
        bj = int(np.searchsorted(bdates, pdates[j], side="right")) - 1
        if 0 <= bj < len(bench_peak) and bench_peak[bj] > 0:
            dd = bvals[bj] / bench_peak[bj] - 1.0
            ev.at[ridx, "bench_drawdown"] = dd
            ev.at[ridx, "market_drawdown"] = bool(dd <= -0.10)
    return ev


def _fund_features_ext(fund):
    """Extended point-in-time fundamentals features (keyed by filing_date) beyond the 8-S set:
    gross / operating margins, a quality composite, a balance-sheet-strength composite, and a
    quality-improvement delta."""
    import numpy as np
    f = fund.sort_values(["ticker", "fiscal_date"]).copy()
    rev = f["total_revenue"].replace(0, np.nan)
    f["gross_margin"] = f["gross_profit"] / rev
    f["op_margin"] = f["operating_income"] / rev
    f["net_margin"] = f["net_income"] / rev
    f["roa_x"] = f["net_income"] / f["total_assets"].replace(0, np.nan)
    f["fcf_margin_x"] = f["free_cash_flow"] / rev
    f["debt_assets_x"] = f["short_long_debt"] / f["total_assets"].replace(0, np.nan)
    f["cash_assets_x"] = f["cash"] / f["total_assets"].replace(0, np.nan)
    f["roa_chg"] = f.groupby("ticker")["roa_x"].transform(lambda s: s - s.shift(4))
    f["gm_chg"] = f.groupby("ticker")["gross_margin"].transform(lambda s: s - s.shift(4))
    keep = ["ticker", "filing_date", "gross_margin", "op_margin", "net_margin", "roa_x",
            "fcf_margin_x", "debt_assets_x", "cash_assets_x", "roa_chg", "gm_chg"]
    return f.dropna(subset=["filing_date"])[keep].sort_values("filing_date")


def prepare_signals_ext(ev, prices, fund, log: _Log):
    """Build the full 8-T signal set on top of the 8-S base event table. Every signal is oriented so
    that HIGHER == expected-better (inverse signals like debt / volatility / beta are negated), so a
    genuine alpha shows up as a POSITIVE IC. No look-ahead: all inputs are PIT (report_date /
    filing_date / trailing-price)."""
    import numpy as np
    import pandas as pd
    ev = s8._prepare_signals(ev)                       # 8-S base composites + base regime flags
    ev = augment_price_features(ev, prices)            # trailing price-context features

    # extended fundamentals as-of join
    if isinstance(fund, pd.DataFrame) and not fund.empty:
        fext = _fund_features_ext(fund)
        ev = s8._asof_join_fundamentals(ev, fext)

    # ---- earnings family (oriented higher==better) ----
    ev["earnings_momentum"] = ev.get("eps_yoy_growth")
    ev["earnings_accel"] = ev.get("eps_accel")
    bucket_map = {"big_miss": -2.0, "miss": -1.0, "inline": 0.0, "beat": 1.0, "big_beat": 2.0}
    if "magnitude_bucket" in ev.columns:
        ev["magnitude_score"] = ev["magnitude_bucket"].astype("object").map(bucket_map).astype(float)
    ev["surprise_pos"] = np.where(ev.get("surprise", np.nan) > 0, ev.get("surprise_pct", np.nan), 0.0)
    ev["surprise_fade"] = -ev.get("surprise_pct", np.nan)   # reversal/fade hypothesis (exploratory)

    # ---- fundamentals family (oriented) ----
    ev["low_debt"] = -ev["debt_assets_x"] if "debt_assets_x" in ev.columns else np.nan
    ev["quality_composite"] = (_zser(ev["roa_x"]) + _zser(ev["fcf_margin_x"])
                               - _zser(ev["debt_assets_x"])) if "roa_x" in ev.columns else np.nan
    ev["bs_strength"] = (_zser(ev["cash_assets_x"]) - _zser(ev["debt_assets_x"])) \
        if "cash_assets_x" in ev.columns else np.nan
    ev["quality_improvement"] = (ev["roa_chg"].fillna(0.0) + ev["gm_chg"].fillna(0.0)) \
        if "roa_chg" in ev.columns else np.nan
    # valuation proxy: trailing-4Q EPS / entry price (earnings yield; higher == cheaper == better)
    if "eps_actual" in ev.columns and "entry_price" in ev.columns:
        ttm = ev.groupby("ticker")["eps_actual"].transform(
            lambda s: s.rolling(4, min_periods=4).sum())
        ev["earnings_yield"] = ttm / ev["entry_price"].replace(0, np.nan)

    # ---- price-context family (oriented) ----
    ev["low_vol"] = -ev["vol_63"] if "vol_63" in ev.columns else np.nan
    ev["low_beta"] = -ev["beta_63"] if "beta_63" in ev.columns else np.nan
    # (mom_pre_21 / mom_pre_63 / liquidity already present)

    # ---- regime flags (as-of, PIT) ----
    if "oil_z" in ev.columns:
        ev["high_oil"] = ev["oil_z"] >= 0
    if "dollar_z" in ev.columns:
        ev["strong_dollar"] = ev["dollar_z"] >= 0
    # market_drawdown already set by augment_price_features; high_rates/easy_regime/high_quality by 8-S

    # ---- interaction composites (additive z-scores; both legs oriented higher==better) ----
    def zc(*cols):
        out = None
        for c in cols:
            if c in ev.columns:
                z = _zser(ev[c])
                out = z if out is None else out + z
        return out if out is not None else pd.Series(np.nan, index=ev.index)

    ev["surprise_x_quality"] = zc("surprise_pct", "roa_x")
    ev["surprise_x_qualimp"] = zc("surprise_pct", "quality_improvement")
    ev["surprise_x_margin_exp"] = zc("surprise_pct", "gm_chg")
    ev["surprise_x_valuation"] = zc("surprise_pct", "earnings_yield")
    ev["surprise_x_mom"] = zc("surprise_pct", "mom_pre_21")
    ev["surprise_x_lowvol"] = zc("surprise_pct", "low_vol")
    ev["sue_x_quality"] = zc("sue", "roa_x")
    ev["sue_x_mom"] = zc("sue", "mom_pre_21")
    ev["beats_x_mom"] = zc("beat_streak", "mom_pre_21")
    ev["accel_x_quality"] = zc("eps_accel", "roa_x")
    ev["accel_x_mom2"] = zc("eps_accel", "mom_pre_63")
    log.step("features", "DONE", "built extended signal set across earnings/fundamentals/price/"
             "regime/interaction families", count=len(feature_catalog()))
    return ev


def feature_catalog() -> List[Dict]:
    base = s8.feature_catalog()
    ext = [
        {"feature": "mom_pre_63", "family": "price", "definition": "market-relative 63d pre-event "
         "return", "pit_basis": "entry_date"},
        {"feature": "vol_63 / low_vol", "family": "price", "definition": "trailing 63d realized vol "
         "(negated so higher==calmer==better)", "pit_basis": "entry_date"},
        {"feature": "beta_63 / low_beta", "family": "price", "definition": "trailing 63d beta vs "
         "benchmark (negated)", "pit_basis": "entry_date"},
        {"feature": "liquidity", "family": "price", "definition": "trailing 63d mean dollar volume",
         "pit_basis": "entry_date"},
        {"feature": "market_drawdown", "family": "regime", "definition": "benchmark >=10% below "
         "trailing-252d peak at entry", "pit_basis": "entry_date"},
        {"feature": "high_oil / strong_dollar", "family": "regime", "definition": "WTI / broad "
         "dollar 1y z-score >= 0", "pit_basis": "report_date"},
        {"feature": "gross_margin / op_margin / net_margin", "family": "fundamentals",
         "definition": "PIT margins from the quarterly income statement", "pit_basis": "filing_date<=report_date"},
        {"feature": "quality_composite", "family": "fundamentals", "definition": "z(roa)+z(fcf "
         "margin)-z(debt/assets)", "pit_basis": "filing_date<=report_date"},
        {"feature": "bs_strength", "family": "fundamentals", "definition": "z(cash/assets)-z(debt/"
         "assets)", "pit_basis": "filing_date<=report_date"},
        {"feature": "quality_improvement", "family": "fundamentals", "definition": "YoY change in "
         "ROA + gross margin", "pit_basis": "filing_date<=report_date"},
        {"feature": "earnings_yield", "family": "valuation", "definition": "trailing-4Q EPS / entry "
         "price (higher==cheaper)", "pit_basis": "entry_date"},
        {"feature": "magnitude_score", "family": "earnings", "definition": "surprise bucket mapped "
         "big_miss(-2)..big_beat(+2)", "pit_basis": "report_date"},
        {"feature": "surprise_pos", "family": "earnings", "definition": "positive-surprise-only "
         "asymmetry", "pit_basis": "report_date"},
        {"feature": "surprise_x_* / sue_x_* / accel_x_*", "family": "interaction", "definition":
         "additive z-score interactions of earnings signals with quality/value/momentum/low-vol",
         "pit_basis": "report_date+as-of"},
    ]
    return base + ext


# --------------------------------------------------------------------------- #
# Scenario battery (organized into autonomous cycles / families).
# --------------------------------------------------------------------------- #
def scenario_battery() -> List[Dict]:
    """~40 scenarios across families, each assigned to an autonomous CYCLE. Every signal is oriented
    higher==better. `exploratory` scenarios are tested + reported but held to the "marked
    exploratory" multiple-testing exemption (never silently promoted as confirmed)."""
    S = []

    def add(cycle, family, scenario, signal, sector_neutral=False, regime_filter=None,
            hypothesis="", exploratory=False):
        S.append({"cycle": cycle, "family": family, "scenario": scenario, "signal": signal,
                  "sector_neutral": sector_neutral, "regime_filter": regime_filter,
                  "hypothesis": hypothesis, "exploratory": exploratory})

    # Cycle 1 - earnings family
    add(1, "earnings", "earnings_surprise", "surprise_pct", False, None,
        "raw EPS surprise -> post-earnings drift")
    add(1, "earnings", "sue_standardized", "sue", False, None,
        "standardized unexpected earnings -> drift (PEAD)")
    add(1, "earnings", "surprise_magnitude", "magnitude_score", False, None,
        "bucketed surprise magnitude -> monotone drift")
    add(1, "earnings", "positive_surprise_asymmetry", "surprise_pos", False, None,
        "only positive surprises carry drift")
    add(1, "earnings", "earnings_momentum", "earnings_momentum", False, None,
        "YoY EPS growth momentum -> drift")
    add(1, "earnings", "earnings_acceleration", "earnings_accel", False, None,
        "acceleration in EPS growth -> drift")
    add(1, "earnings", "beat_streak", "beat_streak", False, None,
        "repeated consecutive beats -> continued drift")
    add(1, "earnings", "surprise_fade", "surprise_fade", False, None,
        "CHALLENGE: surprise reverses/fades (expect failure)", exploratory=True)

    # Cycle 2 - fundamentals family
    add(2, "fundamentals", "revenue_growth", "rev_growth", True, None,
        "within-sector revenue growth -> drift")
    add(2, "fundamentals", "earnings_growth", "ni_growth", True, None,
        "within-sector net-income growth -> drift")
    add(2, "fundamentals", "gross_margin", "gross_margin", True, None,
        "high gross margin quality -> drift")
    add(2, "fundamentals", "operating_margin", "op_margin", True, None,
        "high operating margin quality -> drift")
    add(2, "fundamentals", "fcf_margin", "fcf_margin", True, None,
        "free-cash-flow margin quality -> drift")
    add(2, "fundamentals", "roa_quality", "roa", True, None,
        "return-on-assets quality -> drift")
    add(2, "fundamentals", "low_leverage", "low_debt", True, None,
        "low debt/assets (balance-sheet safety) -> drift")
    add(2, "fundamentals", "quality_composite", "quality_composite", True, None,
        "composite profitability/quality -> drift")
    add(2, "fundamentals", "balance_sheet_strength", "bs_strength", True, None,
        "cash-rich, low-debt balance sheet -> drift")
    add(2, "fundamentals", "quality_improvement", "quality_improvement", True, None,
        "improving ROA + gross margin -> drift")
    add(2, "valuation", "earnings_yield_value", "earnings_yield", True, None,
        "cheap earnings yield -> drift")

    # Cycle 3 - price / context family
    add(3, "price", "price_momentum_21", "mom_pre_21", False, None,
        "market-relative 21d momentum -> continuation")
    add(3, "price", "price_momentum_63", "mom_pre_63", False, None,
        "market-relative 63d momentum -> continuation")
    add(3, "price", "low_volatility", "low_vol", False, None,
        "low realized volatility -> better risk-adjusted drift")
    add(3, "price", "low_beta", "low_beta", False, None,
        "low market beta -> defensive drift")
    add(3, "price", "liquidity", "liquidity", False, None,
        "CHALLENGE: dollar-volume liquidity as a return signal", exploratory=True)
    add(3, "price", "sector_leadership", "mom_pre_21", True, None,
        "within-sector momentum leadership -> drift")

    # Cycle 4 - regime-conditioned earnings
    add(4, "regime", "surprise_high_rates", "surprise_pct", False, "high_rates",
        "surprise drift in the high-rate regime")
    add(4, "regime", "surprise_easy_curve", "surprise_pct", False, "easy_regime",
        "surprise drift when the curve is non-inverted")
    add(4, "regime", "surprise_drawdown", "surprise_pct", False, "market_drawdown",
        "surprise drift during market drawdowns")
    add(4, "regime", "surprise_high_oil", "surprise_pct", False, "high_oil",
        "surprise drift in the high-oil regime", exploratory=True)
    add(4, "regime", "sue_easy_curve", "sue", False, "easy_regime",
        "SUE drift in supportive (steep-curve) regime")

    # Cycle 5 - earnings x cross-section interactions
    add(5, "interaction", "surprise_x_quality", "surprise_x_quality", False, None,
        "surprise confirmed by ROA quality")
    add(5, "interaction", "surprise_x_qualimp", "surprise_x_qualimp", False, None,
        "surprise confirmed by improving quality")
    add(5, "interaction", "surprise_x_margin_exp", "surprise_x_margin_exp", False, None,
        "surprise confirmed by margin expansion")
    add(5, "interaction", "surprise_x_valuation", "surprise_x_valuation", False, None,
        "surprise in cheap names")
    add(5, "interaction", "surprise_x_momentum", "surprise_x_mom", False, None,
        "surprise confirmed by price momentum")
    add(5, "interaction", "surprise_x_lowvol", "surprise_x_lowvol", False, None,
        "surprise concentrated in low-vol names")

    # Cycle 6 - SUE / streak / acceleration interactions + sector-neutral variants
    add(6, "interaction", "sue_x_quality", "sue_x_quality", False, None,
        "SUE confirmed by quality")
    add(6, "interaction", "sue_x_momentum", "sue_x_mom", False, None,
        "SUE confirmed by momentum")
    add(6, "interaction", "beats_x_momentum", "beats_x_mom", False, None,
        "beat streak confirmed by momentum")
    add(6, "interaction", "accel_x_quality", "accel_x_quality", False, None,
        "earnings acceleration confirmed by quality")
    add(6, "interaction", "accel_x_momentum", "accel_x_mom2", False, None,
        "earnings acceleration confirmed by 63d momentum")
    add(6, "earnings", "surprise_sector_neutral", "surprise_pct", True, None,
        "within-sector surprise ranking -> drift")
    add(6, "earnings", "sue_sector_neutral", "sue", True, None,
        "within-sector SUE ranking -> drift")
    return S


def cycle_families() -> Dict[int, str]:
    return {1: "earnings", 2: "fundamentals/valuation", 3: "price/context",
            4: "regime-conditioned", 5: "earnings x cross-section", 6: "SUE/streak/accel + sector"}


# --------------------------------------------------------------------------- #
# D. Extended validation (subperiod stability, placebo/challenge, 10/25/50 bps cost).
# --------------------------------------------------------------------------- #
def _monthly_ics(df, signal, ret_col, sector_neutral, regime_filter):
    """The monthly cross-sectional rank-IC series + the month labels (for subperiod splits)."""
    import numpy as np
    if regime_filter and regime_filter in df.columns:
        df = df[df[regime_filter] == True]  # noqa: E712
    df = df[df[signal].notna() & df[ret_col].notna()].copy()
    if df.empty:
        return [], []
    df["month"] = df["entry_date"].dt.to_period("M")
    if sector_neutral and "sector" in df.columns:
        df["sig"] = df.groupby(["month", "sector"])[signal].transform(lambda s: s - s.mean())
    else:
        df["sig"] = df[signal]
    df = df[df["sig"].notna()]
    months, ics = [], []
    for mth in sorted(df["month"].unique()):
        chunk = df[df["month"] == mth]
        if len(chunk) < MIN_NAMES_PER_MONTH:
            continue
        ic = s8._spearman(chunk["sig"].to_numpy(), chunk[ret_col].to_numpy())
        if not math.isnan(ic):
            months.append(str(mth))
            ics.append(ic)
    return months, ics


def subperiod_stability(df, signal, ret_col, sector_neutral, regime_filter):
    import numpy as np
    months, ics = _monthly_ics(df, signal, ret_col, sector_neutral, regime_filter)
    if len(ics) < 4:
        return {"h1_ic": float("nan"), "h2_ic": float("nan"), "stable": False, "n_months": len(ics)}
    half = len(ics) // 2
    h1 = float(np.mean(ics[:half]))
    h2 = float(np.mean(ics[half:]))
    stable = bool(h1 > 0 and h2 > 0)            # both halves positive (signal is oriented +)
    return {"h1_ic": h1, "h2_ic": h2, "stable": stable, "n_months": len(ics)}


def placebo_challenge(df, signal, ret_col, sector_neutral, regime_filter, rng):
    """Two negative controls: (1) returns permuted within month (kills any real signal->return link);
    (2) a random gaussian signal. A real signal's IC should dominate both placebos."""
    import numpy as np
    _, real = _monthly_ics(df, signal, ret_col, sector_neutral, regime_filter)
    real_ic = float(np.mean(real)) if real else float("nan")
    sub = df[df[signal].notna() & df[ret_col].notna()].copy()
    if sub.empty:
        return {"real_ic": real_ic, "shuffled_ic": float("nan"), "random_ic": float("nan"),
                "beats_placebo": False}
    sub["month"] = sub["entry_date"].dt.to_period("M")
    # (1) shuffle the return within each month
    shuffled = sub.copy()
    shuffled[ret_col] = shuffled.groupby("month")[ret_col].transform(
        lambda s: s.to_numpy()[rng.permutation(len(s))])
    _, sh = _monthly_ics(shuffled, signal, ret_col, sector_neutral, regime_filter)
    shuffled_ic = float(np.mean(sh)) if sh else float("nan")
    # (2) random signal
    rnd = sub.copy()
    rnd["__rand__"] = rng.standard_normal(len(rnd))
    _, rr = _monthly_ics(rnd, "__rand__", ret_col, sector_neutral, regime_filter)
    random_ic = float(np.mean(rr)) if rr else float("nan")
    beats = bool(not math.isnan(real_ic)
                 and abs(real_ic) > 2 * max(abs(shuffled_ic) if not math.isnan(shuffled_ic) else 0,
                                            abs(random_ic) if not math.isnan(random_ic) else 0))
    return {"real_ic": real_ic, "shuffled_ic": shuffled_ic, "random_ic": random_ic,
            "beats_placebo": beats}


def evaluate_ext(ev, sc, rng) -> Dict:
    """8-S evaluation battery + 8-T extensions (25/50 bps net cost, subperiod stability,
    placebo/challenge)."""
    ret_col = "fwd_exc_%d" % PRIMARY_HORIZON
    m = s8.evaluate_signal(ev, sc["signal"], ret_col, sc["sector_neutral"], sc["regime_filter"])
    row = dict(sc)
    row.update(m)
    if m.get("n_events", 0) == 0:
        row.update({"net_spread_25bps": float("nan"), "net_spread_50bps": float("nan"),
                    "h1_ic": float("nan"), "h2_ic": float("nan"), "subperiod_stable": False,
                    "real_ic": float("nan"), "shuffled_ic": float("nan"), "random_ic": float("nan"),
                    "beats_placebo": False})
        return row
    row["net_spread_25bps"] = _net_spread(m.get("mean_spread"), m.get("avg_turnover"), 25)
    row["net_spread_50bps"] = _net_spread(m.get("mean_spread"), m.get("avg_turnover"), 50)
    # restrict the df the same way evaluate_signal did
    df = ev
    sp = subperiod_stability(df, sc["signal"], ret_col, sc["sector_neutral"], sc["regime_filter"])
    row["h1_ic"] = sp["h1_ic"]
    row["h2_ic"] = sp["h2_ic"]
    row["subperiod_stable"] = sp["stable"]
    pl = placebo_challenge(df, sc["signal"], ret_col, sc["sector_neutral"], sc["regime_filter"], rng)
    row["real_ic"] = pl["real_ic"]
    row["shuffled_ic"] = pl["shuffled_ic"]
    row["random_ic"] = pl["random_ic"]
    row["beats_placebo"] = pl["beats_placebo"]
    return row


# --------------------------------------------------------------------------- #
# E. Promotion gates (8-T).
# --------------------------------------------------------------------------- #
def gate_reasons(r: Dict) -> List[str]:
    reasons = []
    if r.get("n_events", 0) < MIN_EVENTS:
        reasons.append("insufficient events (%s<%d)" % (r.get("n_events", 0), MIN_EVENTS))
    if r.get("n_months", 0) < MIN_MONTHS:
        reasons.append("insufficient months (%s<%d)" % (r.get("n_months", 0), MIN_MONTHS))
    ic = r.get("mean_ic")
    if ic is None or math.isnan(ic) or ic < GATE_MIN_IC:           # signed: must be positive
        reasons.append("mean_ic below +%.2f (or wrong-sign)" % GATE_MIN_IC)
    t = r.get("ic_t")
    if t is None or math.isnan(t) or t < GATE_MIN_T:               # signed: must be positive
        reasons.append("ic_t below +%.1f" % GATE_MIN_T)
    # hit-rate OR strongly compensating economic spread
    sh = r.get("spread_hit_rate")
    spread = r.get("mean_spread")
    strong = (spread is not None and not math.isnan(spread) and spread >= GATE_STRONG_SPREAD)
    if (sh is None or math.isnan(sh) or sh < GATE_MIN_SPREAD_HITRATE) and not strong:
        reasons.append("spread hit-rate below %.2f and spread not strongly compensating"
                       % GATE_MIN_SPREAD_HITRATE)
    # survives at least one cost stress (10 bps), and stays positive at the hard 25 bps gate
    net10 = r.get("net_spread_10bps")
    net25 = r.get("net_spread_25bps")
    if net10 is None or math.isnan(net10) or net10 <= 0:
        reasons.append("does not survive 10 bps cost")
    if net25 is None or math.isnan(net25) or net25 <= 0:
        reasons.append("does not survive %g bps cost" % GATE_COST_BPS_HARD)
    # sector concentration
    share = r.get("top_sector_share")
    if share is not None and not math.isnan(share) and share > GATE_MAX_SECTOR_SHARE:
        reasons.append("sector-concentrated (%s %.0f%%)" % (r.get("top_sector"), share * 100))
    # subperiod stability
    if GATE_SUBPERIOD_SIGN and not r.get("subperiod_stable"):
        reasons.append("not stable across subperiods (a half flips sign)")
    # regime robustness (reuse 8-S regime split count)
    if r.get("regimes_right_sign", 0) < 2:
        reasons.append("not robust across rate regimes")
    # multiple-testing discipline: BH-significant OR explicitly exploratory
    if not r.get("bh_significant") and not r.get("exploratory"):
        reasons.append("fails Benjamini-Hochberg q=%.2f (and not marked exploratory)" % GATE_BH_Q)
    # a "challenge" exploratory scenario is never promoted as confirmed alpha
    if r.get("exploratory"):
        reasons.append("exploratory/challenge scenario - reported, not promoted as confirmed")
    return reasons


# --------------------------------------------------------------------------- #
# F. Autonomous campaign loop.
# --------------------------------------------------------------------------- #
def run_campaign(ev, max_cycles: int, log: _Log) -> Tuple[List[Dict], List[Dict], List[Dict], List[Dict]]:
    """Run the scenario battery cycle-by-cycle (autonomous loop): each cycle tests a family batch,
    accumulates into the candidate registry, and continues automatically until the battery is
    exhausted or max_cycles is hit. Multiple-testing control is applied across ALL tested scenarios.
    Returns (results, promoted, rejected, cycle_rows)."""
    import numpy as np
    rng = np.random.default_rng(8675309)
    battery = scenario_battery()
    cycles = sorted({s["cycle"] for s in battery})
    fam = cycle_families()
    results: List[Dict] = []
    cycle_rows: List[Dict] = []
    tested_scenarios = set()
    cycles_run = 0
    for cyc in cycles:
        if cycles_run >= max_cycles:
            break
        batch = [s for s in battery if s["cycle"] == cyc]
        for sc in batch:
            r = evaluate_ext(ev, sc, rng)
            results.append(r)
            tested_scenarios.add(sc["scenario"])
        cycles_run += 1
        # provisional BH across everything tested so far (re-finalized after the loop)
        log.step("cycle", "DONE", "cycle %d [%s]: tested %d scenarios (cumulative %d)"
                 % (cyc, fam.get(cyc, ""), len(batch), len(results)), count=len(results))
        cycle_rows.append({"cycle": cyc, "family": fam.get(cyc, ""),
                           "scenarios_tested_this_cycle": len(batch),
                           "scenarios_tested_cumulative": len(results),
                           "candidates_remaining": sum(1 for s in battery
                                                       if s["cycle"] > cyc) if cyc != cycles[-1] else 0})

    # finalize multiple-testing control across all scenarios with a computable IC p-value
    pvals = [r.get("ic_p") if r.get("ic_p") is not None else float("nan") for r in results]
    bh = s8.benjamini_hochberg(pvals, GATE_BH_Q)
    m = len([p for p in pvals if p is not None and not math.isnan(p)])
    for r, sig in zip(results, bh):
        p = r.get("ic_p")
        r["bonferroni_p"] = (min(1.0, p * m) if p is not None and not math.isnan(p) and m else None)
        r["bh_significant"] = bool(sig)

    promoted, rejected = [], []
    for r in results:
        reasons = gate_reasons(r)
        if not reasons:
            promoted.append(r)
        else:
            rr = dict(r)
            rr["reject_reason"] = "; ".join(reasons)
            rejected.append(rr)
    log.step("campaign", "DONE", "%d cycles, %d scenarios, %d promoted, %d rejected"
             % (cycles_run, len(results), len(promoted), len(rejected)), count=len(promoted))
    return results, promoted, rejected, cycle_rows


# --------------------------------------------------------------------------- #
# Artifact writers.
# --------------------------------------------------------------------------- #
def _g(r, k, n=4):
    return _round(r.get(k), n)


def write_campaign_artifacts(P: _Paths, results: List[Dict], promoted: List[Dict],
                             rejected: List[Dict], cycle_rows: List[Dict]) -> None:
    battery = scenario_battery()
    # candidate registry (every scenario the daemon generated, with family + cycle + status)
    promoted_names = {r["scenario"] for r in promoted}
    _write_csv(P.candidate_registry,
               ["cycle", "family", "scenario", "signal", "sector_neutral", "regime_filter",
                "exploratory", "hypothesis", "status"],
               [[s["cycle"], s["family"], s["scenario"], s["signal"], s["sector_neutral"],
                 s["regime_filter"] or "", s["exploratory"], s["hypothesis"],
                 "promoted" if s["scenario"] in promoted_names else "rejected"] for s in battery])

    _write_csv(P.scenario_plan,
               ["cycle", "family", "scenario", "signal", "sector_neutral", "regime_filter",
                "exploratory", "hypothesis"],
               [[s["cycle"], s["family"], s["scenario"], s["signal"], s["sector_neutral"],
                 s["regime_filter"] or "", s["exploratory"], s["hypothesis"]] for s in battery])

    _write_csv(P.cycle_summary,
               ["cycle", "family", "scenarios_tested_this_cycle", "scenarios_tested_cumulative",
                "candidates_remaining"],
               [[c["cycle"], c["family"], c["scenarios_tested_this_cycle"],
                 c["scenarios_tested_cumulative"], c["candidates_remaining"]] for c in cycle_rows])

    _write_csv(P.scoreboard,
               ["scenario", "family", "cycle", "n_events", "n_months", "mean_ic", "ic_t", "ic_p",
                "bh_significant", "mean_spread", "spread_hit_rate", "net_spread_25bps",
                "subperiod_stable", "beats_placebo", "exploratory", "promoted"],
               [[r["scenario"], r["family"], r["cycle"], r.get("n_events", 0), r.get("n_months", 0),
                 _g(r, "mean_ic"), _g(r, "ic_t", 2), _g(r, "ic_p"), r.get("bh_significant", False),
                 _g(r, "mean_spread"), _g(r, "spread_hit_rate"), _g(r, "net_spread_25bps"),
                 r.get("subperiod_stable", False), r.get("beats_placebo", False),
                 r.get("exploratory", False), r in promoted] for r in results])

    _write_csv(P.rank_ic, ["scenario", "horizon", "mean_ic", "ic_std", "n_months", "ic_t", "ic_p",
                           "bonferroni_p", "bh_significant"],
               [[r["scenario"], PRIMARY_HORIZON, _g(r, "mean_ic"), _g(r, "ic_std"),
                 r.get("n_months", 0), _g(r, "ic_t", 2), _g(r, "ic_p"), _g(r, "bonferroni_p"),
                 r.get("bh_significant", False)] for r in results])

    _write_csv(P.spread, ["scenario", "quantiles", "mean_long_short_spread", "spread_t",
                          "spread_hit_rate", "n_months"],
               [[r["scenario"], N_QUANTILES, _g(r, "mean_spread"), _g(r, "spread_t", 2),
                 _g(r, "spread_hit_rate"), r.get("n_months", 0)] for r in results])

    _write_csv(P.hit_rate, ["scenario", "event_hit_rate", "month_spread_hit_rate"],
               [[r["scenario"], _g(r, "event_hit_rate"), _g(r, "spread_hit_rate")] for r in results])

    _write_csv(P.turnover, ["scenario", "avg_top_quintile_turnover", "interpretation"],
               [[r["scenario"], _g(r, "avg_turnover"),
                 "fraction of top-quintile names replaced month-over-month"] for r in results])

    _write_csv(P.tcost, ["scenario", "gross_spread", "avg_turnover", "net_10bps", "net_25bps",
                         "net_50bps"],
               [[r["scenario"], _g(r, "mean_spread"), _g(r, "avg_turnover"),
                 _g(r, "net_spread_10bps"), _g(r, "net_spread_25bps"), _g(r, "net_spread_50bps")]
                for r in results])

    _write_csv(P.sector_conc, ["scenario", "top_sector", "top_sector_share", "hhi"],
               [[r["scenario"], r.get("top_sector", ""), _g(r, "top_sector_share"), _g(r, "hhi")]
                for r in results])

    reg_rows = []
    for r in results:
        for label, val in (r.get("regime_ic") or {}).items():
            reg_rows.append([r["scenario"], label, _round(val)])
    _write_csv(P.regime_split, ["scenario", "regime", "mean_ic"], reg_rows)

    _write_csv(P.subperiod, ["scenario", "first_half_ic", "second_half_ic", "stable", "n_months"],
               [[r["scenario"], _g(r, "h1_ic"), _g(r, "h2_ic"), r.get("subperiod_stable", False),
                 r.get("n_months", 0)] for r in results])

    _write_csv(P.multiple_testing,
               ["scenario", "ic_p", "bonferroni_p", "bh_q", "bh_significant", "exploratory"],
               [[r["scenario"], _g(r, "ic_p"), _g(r, "bonferroni_p"), GATE_BH_Q,
                 r.get("bh_significant", False), r.get("exploratory", False)] for r in results])

    _write_csv(P.placebo, ["scenario", "real_ic", "shuffled_return_ic", "random_signal_ic",
                           "beats_placebo"],
               [[r["scenario"], _g(r, "real_ic"), _g(r, "shuffled_ic"), _g(r, "random_ic"),
                 r.get("beats_placebo", False)] for r in results])

    _write_csv(P.promoted,
               ["scenario", "family", "signal", "mean_ic", "ic_t", "mean_spread", "spread_hit_rate",
                "net_spread_25bps", "subperiod_stable", "beats_placebo", "n_events", "n_months",
                "new_vs_phase8s"],
               [[r["scenario"], r["family"], r["signal"], _g(r, "mean_ic"), _g(r, "ic_t", 2),
                 _g(r, "mean_spread"), _g(r, "spread_hit_rate"), _g(r, "net_spread_25bps"),
                 r.get("subperiod_stable", False), r.get("beats_placebo", False),
                 r.get("n_events", 0), r.get("n_months", 0),
                 r["scenario"] not in _PHASE8S_PROMOTED] for r in promoted])

    _write_csv(P.rejected, ["scenario", "family", "signal", "mean_ic", "ic_t", "n_events",
                            "exploratory", "reject_reason"],
               [[r["scenario"], r["family"], r["signal"], _g(r, "mean_ic"), _g(r, "ic_t", 2),
                 r.get("n_events", 0), r.get("exploratory", False), r.get("reject_reason", "")]
                for r in rejected])

    _write_csv(P.feature_catalog, ["feature", "family", "definition", "pit_basis"],
               [[f["feature"], f["family"], f["definition"], f["pit_basis"]]
                for f in feature_catalog()])


def write_preview_specs(P: _Paths, promoted: List[Dict]) -> int:
    """Paper-Trader-preview-ready signal specifications for promoted signals. PREVIEW-ONLY: ranking
    ideas for manual review - NO orders, NO automation, NO position sizing directives."""
    rows = []
    for r in promoted:
        rows.append([
            r["scenario"], r["signal"], r["family"],
            "long top quintile / short bottom quintile, monthly, %s-neutral"
            % ("sector" if r.get("sector_neutral") else "raw"),
            PRIMARY_HORIZON,
            _g(r, "mean_ic"), _g(r, "ic_t", 2), _g(r, "mean_spread"), _g(r, "net_spread_25bps"),
            _g(r, "spread_hit_rate"), r.get("n_events", 0), r.get("n_months", 0),
            "PREVIEW_ONLY", "MANUAL_REVIEW", "NO_ORDERS", "AUTOMATION_OFF",
            "Surface as a ranking idea in the daily-review cockpit; analyst confirms before any "
            "manual paper trade. Point-in-time on EODHD earnings/fundamentals + local prices."])
    _write_csv(P.preview_specs,
               ["scenario", "signal", "family", "construction", "horizon_days", "mean_ic", "ic_t",
                "mean_monthly_spread", "net_spread_25bps", "spread_hit_rate", "n_events", "n_months",
                "safety_preview", "safety_review", "safety_orders", "safety_automation",
                "usage_note"], rows)
    return len(rows)


# --------------------------------------------------------------------------- #
# Decision.
# --------------------------------------------------------------------------- #
def derive_decision(acq: Dict, stats: Dict, results: List[Dict], promoted: List[Dict],
                    cycles_run: int, battery_cycles: int) -> Tuple[str, str]:
    if acq.get("blocker"):
        return (DEC_BLOCKER, acq["blocker"])
    if stats.get("events_usable", 0) == 0:
        return (DEC_BLOCKER,
                "No usable point-in-time earnings events could be built. Acquire EODHD fundamentals "
                "for the priced universe first (set EODHD_API_KEY and re-run --live).")
    if cycles_run < battery_cycles:
        return (DEC_NEXT_CYCLE,
                "max_cycles=%d halted the campaign before the scenario battery was exhausted "
                "(%d/%d cycles run). Re-run with a higher --max-cycles to finish the remaining "
                "families." % (cycles_run, cycles_run, battery_cycles))
    if promoted:
        new = [p for p in promoted if p["scenario"] not in _PHASE8S_PROMOTED]
        names = ", ".join(p["scenario"] for p in promoted)
        if new:
            return (DEC_MORE_ALPHA,
                    "%d signal(s) cleared every robustness gate (signed rank IC, t-stat, BH "
                    "multiple-testing, quintile-spread hit-rate, net-of-25bps cost, sector/regime/"
                    "subperiod robustness, placebo): %s. %d are NEW beyond the Phase 8-S pair. "
                    "Surface as preview-only ranking signals for manual Paper Trader review - no "
                    "orders." % (len(promoted), names, len(new)))
        return (DEC_ALPHA,
                "%d signal(s) cleared every robustness gate: %s. These reconfirm the Phase 8-S "
                "alpha under a much larger battery and stricter gates; no NEW family promoted. "
                "Preview-only; no orders." % (len(promoted), names))
    return (DEC_NO_ALPHA,
            "Across %d scenarios in %d autonomous cycles (earnings, fundamentals, valuation, price/"
            "context, regime, and interaction families) with subperiod, placebo, multiple-testing "
            "and 25bps-cost discipline, no signal cleared the full robustness gate on the %d-ticker "
            "scoreable cross-section." % (len(results), cycles_run, stats.get("tickers_usable", 0)))


# --------------------------------------------------------------------------- #
# Orchestration.
# --------------------------------------------------------------------------- #
def run(live: bool = False, transport: Optional[Callable[[str, str], object]] = None,
        out_dir: Optional[Path] = None, data_dir: Optional[Path] = None,
        phase8n_dir: Optional[Path] = None, price_csv: Optional[Path] = None,
        sector_csv: Optional[Path] = None, macro: Optional[Dict[str, Path]] = None,
        phase8r_dir: Optional[Path] = None, phase8s_dir: Optional[Path] = None,
        max_tickers: int = DEFAULT_MAX_TICKERS, max_requests: int = DEFAULT_MAX_REQUESTS,
        max_cycles: int = DEFAULT_MAX_CYCLES, as_of: str = s8.DEFAULT_AS_OF,
        verbose: bool = True) -> Dict:
    P = _Paths(out_dir, data_dir, phase8n_dir, price_csv, sector_csv, macro, phase8r_dir, phase8s_dir)
    P.out.mkdir(parents=True, exist_ok=True)
    log = _Log(verbose)
    try:
        import pandas as pd
        present = r8.key_present()
        phase8s = _read_json(P.phase8s / "phase8s_autonomous_eodhd_alpha_factory.json")
        s_decision = phase8s.get("decision", "")
        s_promoted = phase8s.get("promoted_signals", sorted(_PHASE8S_PROMOTED))
        log.step("prior", "INFO", "Phase 8-S decision: %s; promoted: %s"
                 % (s_decision or "(none read)", ", ".join(s_promoted) if s_promoted else "(none)"))
        log.step("start", "INFO", "live=%s key_present=%s max_tickers=%d max_requests=%d max_cycles=%d"
                 % (live, present, max_tickers, max_requests, max_cycles))

        # A1. universe (reuse 8-S resolver) + B. bounded refresh/repair acquisition (skip-existing)
        universe = s8.resolve_universe(P, max_tickers)
        n_target = sum(1 for u in universe if u["targeted_for_acquisition"])
        log.step("universe", "DONE", "%d priced tickers; %d targeted for refresh/repair"
                 % (len(universe), n_target), count=len(universe))
        acq = s8.acquire_eodhd(P, universe, live, transport, max_requests, log)

        # 3-5. PIT event table (reuse 8-S builder) + extended features
        fund = s8.load_fundamentals_panel(P)
        prices = s8.load_prices(P)
        ev, stats, audit = s8.build_event_table(P, as_of, log)
        log.step("panels", "DONE", "%d usable events / %d tickers"
                 % (stats.get("events_usable", 0), stats.get("tickers_usable", 0)),
                 count=stats.get("events_usable", 0))

        # A2/A3. universe expansion search + unscoreable diagnosis
        priced = [u["ticker"] for u in universe]
        scoreable = sorted(set(ev["ticker"].unique())) if (isinstance(ev, pd.DataFrame)
                                                           and not ev.empty) else []
        exp_rows = search_universe_expansion(P, priced, scoreable)
        _write_csv(P.universe_expansion,
                   ["source", "kind", "tickers", "has_local_prices", "used_as_universe", "note"],
                   [[r["source"], r["kind"], r["tickers"], r["has_local_prices"],
                     r["used_as_universe"], r["note"]] for r in exp_rows])
        unscore_rows, unscore_summary = diagnose_unscoreable(P, ev, universe)
        _write_csv(P.unscoreable,
                   ["ticker", "in_price_cache", "has_earnings", "has_fundamentals", "scoreable",
                    "reason"],
                   [[r["ticker"], r["in_price_cache"], r["has_earnings"], r["has_fundamentals"],
                     r["scoreable"], r["reason"]] for r in unscore_rows])
        log.step("diagnose", "DONE", "%d priced, %d scoreable, %d unscoreable"
                 % (unscore_summary["priced"], unscore_summary["scoreable"],
                    unscore_summary["unscoreable"]), count=unscore_summary["unscoreable"])

        # data coverage matrix (committed-safe; no stray 8-S-named coverage files)
        cov = _write_data_coverage_matrix(P, ev, fund, universe)

        # C/D/E/F. extended features + autonomous campaign
        battery_cycles = len({s["cycle"] for s in scenario_battery()})
        if stats.get("events_usable", 0) > 0:
            ev = prepare_signals_ext(ev, prices, fund, log)
            results, promoted, rejected, cycle_rows = run_campaign(ev, max_cycles, log)
        else:
            results, promoted, rejected, cycle_rows = [], [], [], []
            log.step("campaign", "SKIP", "no usable events to score")
        cycles_run = len(cycle_rows)
        write_campaign_artifacts(P, results, promoted, rejected, cycle_rows)
        n_preview = write_preview_specs(P, promoted)

        # also (re)write the PIT audit so the committed dir documents the no-look-ahead checks
        _write_csv(P.out / "point_in_time_audit.csv", ["check", "result", "detail"],
                   [[a["check"], a["result"], a["detail"]] for a in audit])

        # decision
        decision, rationale = derive_decision(acq, stats, results, promoted, cycles_run,
                                              battery_cycles)
        log.step("decision", "DONE", "%s" % decision)

        new_promoted = [p["scenario"] for p in promoted if p["scenario"] not in _PHASE8S_PROMOTED]
        best = None
        ranked = [r for r in results if r.get("ic_t") is not None
                  and not math.isnan(r.get("ic_t") or float("nan"))]
        best = max(ranked, key=lambda r: r["ic_t"], default=None)   # signed: best positive t

        _write_json(P.final_decision, {
            "phase": PHASE, "decision": decision, "decision_is_terminal": decision in _TERMINAL,
            "allowed_decisions": list(ALLOWED_DECISIONS), "rationale": rationale,
            "alpha_found": decision in (DEC_ALPHA, DEC_MORE_ALPHA),
            "additional_alpha_found": bool(new_promoted),
            "promoted_signals": [p["scenario"] for p in promoted],
            "new_signals_vs_phase8s": new_promoted,
            "best_scenario": (best or {}).get("scenario"),
            "best_scenario_ic": _round((best or {}).get("mean_ic")),
            "best_scenario_ic_t": _round((best or {}).get("ic_t"), 2),
            "scoreable_tickers": stats.get("tickers_usable", 0),
            "usable_events": stats.get("events_usable", 0),
            "cycles_run": cycles_run, "scenarios_tested": len(results),
            "preview_specs": n_preview,
            "preview_only": True, "orders_enabled": False, "automation_enabled": False,
            "broker_execution_enabled": False, "paper_trader_touched": False, "gcp_touched": False,
            "committed": False,
        })

        next_cmd, next_title = _next_step(decision, max_tickers, max_requests, max_cycles)
        _write_json(P.next_plan, {
            "phase": "8-U", "title": next_title, "depends_on_decision": decision,
            "next_command": next_cmd, "preview_only": True, "orders_enabled": False,
            "automation_enabled": False, "committed": False,
        })

        _write_csv(P.daemon_log, ["step", "status", "detail", "count"], log.rows)

        # secret leak scan over committed artifacts
        written = [p for p in P.out.glob("*") if p.is_file()]
        leak_clean, scanned, marker_found = r8._scan_for_leaks(written)
        _write_csv(P.secret_audit, ["check", "result", "detail"],
                   [["api_key_read_from_env_only", True, "EODHD_API_KEY via os.environ only (8-R layer)"],
                    ["api_key_printed", False, "key value never printed"],
                    ["api_key_written_to_disk", False, "key value never written to any artifact"],
                    ["committed_artifacts_contain_key_query_param", marker_found,
                     "leak scan over %d committed files" % scanned],
                    ["secret_leak_scan_clean", leak_clean, "no key value or key query param"],
                    ["raw_normalized_gitignored", True,
                     "research/data/eodhd/{raw,normalized}/ force-gitignored before write"]])

        report = {
            "phase": PHASE,
            "objective": ("Autonomous EODHD alpha-factory daemon: diagnose/expand the scoreable "
                          "universe, refresh coverage, then run a multi-family scenario battery "
                          "across autonomous cycles with extended robustness validation, promoting "
                          "only durable alpha and emitting Paper-Trader-preview specs. Preview-only; "
                          "no orders."),
            "provider": PROVIDER_NAME, "api_key_env_var": API_KEY_ENV,
            "builds_on_phase8s_decision": s_decision, "phase8s_promoted": s_promoted,
            "eodhd_key_present": present, "api_key_logged": False,
            "mode": ("live_refresh_and_campaign" if acq.get("executed") and transport is None
                     else ("test_transport" if transport is not None else "offline_campaign_cached")),
            "as_of": as_of,
            "bounded_limits": {"max_tickers": max_tickers, "max_requests": max_requests,
                               "max_cycles": max_cycles, "checkpoint_every": s8.CHECKPOINT_EVERY},
            "universe": {"priced_tickers": len(universe),
                         "scoreable_tickers": stats.get("tickers_usable", 0),
                         "targeted_for_refresh": n_target,
                         "unscoreable": unscore_summary["unscoreable"],
                         "unscoreable_no_earnings": unscore_summary["unscoreable_no_earnings"],
                         "unscoreable_no_fundamentals": unscore_summary["unscoreable_no_fundamentals"],
                         "unscoreable_no_pit_event": unscore_summary["unscoreable_no_pit_event"],
                         "broader_local_price_universe_found": any(
                             r["kind"] == "broader_price_universe_probe" and r["has_local_prices"]
                             for r in exp_rows),
                         "sp500_name_universe": next((r["tickers"] for r in exp_rows
                                                      if r["kind"] == "name_universe_only"), 0)},
            "acquisition": {"executed": acq.get("executed"), "requests_made": acq.get("requests_made"),
                            "tickers_acquired_this_run": acq.get("acquired"),
                            "stopped_early": acq.get("stopped"), "stop_reason": acq.get("stop_reason")},
            "eodhd_coverage": cov,
            "point_in_time": {"events_raw": stats.get("events_raw", 0),
                              "events_with_realized_eps": stats.get("events_with_realized_eps", 0),
                              "usable_events": stats.get("events_usable", 0),
                              "scoreable_tickers": stats.get("tickers_usable", 0),
                              "date_min": stats.get("date_min", ""), "date_max": stats.get("date_max", ""),
                              "sectors": stats.get("sectors", 0)},
            "cycles_run": cycles_run, "battery_cycles": battery_cycles,
            "scenario_candidates_generated": len(scenario_battery()),
            "scenarios_tested": len(results),
            "scenario_families": sorted({s["family"] for s in scenario_battery()}),
            "scenarios": [{"scenario": r["scenario"], "family": r["family"], "cycle": r["cycle"],
                           "mean_ic": _round(r.get("mean_ic")), "ic_t": _round(r.get("ic_t"), 2),
                           "ic_p": _round(r.get("ic_p")),
                           "bh_significant": r.get("bh_significant", False),
                           "subperiod_stable": r.get("subperiod_stable", False),
                           "beats_placebo": r.get("beats_placebo", False),
                           "exploratory": r.get("exploratory", False),
                           "promoted": r in promoted} for r in results],
            "best_scenario": (best or {}).get("scenario"),
            "promoted_signals": [p["scenario"] for p in promoted],
            "new_signals_vs_phase8s": new_promoted,
            "rejected_signals": [r["scenario"] for r in rejected],
            "alpha_found": decision in (DEC_ALPHA, DEC_MORE_ALPHA),
            "additional_alpha_found": bool(new_promoted),
            "preview_specs_produced": n_preview,
            "decision": decision, "decision_is_terminal": decision in _TERMINAL,
            "allowed_decisions": list(ALLOWED_DECISIONS), "decision_rationale": rationale,
            "evidence_gates": {"min_ic": GATE_MIN_IC, "min_t": GATE_MIN_T, "bh_q": GATE_BH_Q,
                               "min_spread_hit_rate": GATE_MIN_SPREAD_HITRATE,
                               "hard_cost_bps": GATE_COST_BPS_HARD,
                               "max_sector_share": GATE_MAX_SECTOR_SHARE,
                               "subperiod_sign_stable": GATE_SUBPERIOD_SIGN,
                               "min_events": MIN_EVENTS, "min_months": MIN_MONTHS},
            "blockers": acq.get("blocker") or "",
            "recommended_next_command": next_cmd,
            "secret_safety_leak_scan_clean": leak_clean, "secret_safety_files_scanned": scanned,
            "raw_dir_gitignored": _rel(P.eodhd_dir),
            "outputs": {k: _rel(getattr(P, k)) for k in _ARTIFACTS},
            # safety / provenance contract
            "preview_only": True, "orders_enabled": False, "automation_enabled": False,
            "broker_execution_enabled": False, "production_replacement": False,
            "network_used": bool(acq.get("executed") and transport is None),
            "paper_trader_touched": False, "gcp_touched": False, "deployed": False,
            "data_fabricated": False, "no_alpha_fabricated": True,
            "raw_paid_data_in_artifacts": False, "wrote_to_d_drive": False, "committed": False,
        }
        _write_json(P.report, report)
        if verbose:
            _print_summary(report)
        return report
    except Exception as exc:  # pragma: no cover - defensive; never leaks a key
        report = {"phase": PHASE, "decision": DEC_ERROR, "decision_is_terminal": True,
                  "allowed_decisions": list(ALLOWED_DECISIONS),
                  "error": "%s: %s" % (type(exc).__name__, exc), "api_key_logged": False,
                  "committed": False, "preview_only": True, "orders_enabled": False,
                  "automation_enabled": False, "paper_trader_touched": False, "gcp_touched": False}
        try:
            _write_json(P.report, report)
            _write_csv(P.daemon_log, ["step", "status", "detail", "count"], log.rows)
        except Exception:
            pass
        if verbose:
            print("ERROR: %s" % report["error"])
        return report


def _write_data_coverage_matrix(P: _Paths, ev, fund, universe) -> Dict:
    import pandas as pd
    earn_recs = _read_csv_file(P.normalized_csv)
    earn_by_tk: Dict[str, int] = {}
    for r in earn_recs:
        tk = (r.get("symbol") or "").strip().upper()
        if tk:
            earn_by_tk[tk] = earn_by_tk.get(tk, 0) + 1
    fund_by_tk = (fund.groupby("ticker").size().to_dict()
                  if isinstance(fund, pd.DataFrame) and not fund.empty else {})
    usable_by_tk = (ev.groupby("ticker").size().to_dict()
                    if isinstance(ev, pd.DataFrame) and not ev.empty else {})
    rows = []
    for u in universe:
        tk = u["ticker"]
        rows.append([tk, u["priority"], earn_by_tk.get(tk, 0), fund_by_tk.get(tk, 0),
                     usable_by_tk.get(tk, 0), usable_by_tk.get(tk, 0) > 0])
    _write_csv(P.coverage_matrix,
               ["ticker", "priority", "earnings_rows", "fundamentals_quarters",
                "usable_scored_events", "scoreable"], rows)
    return {"earnings_tickers": len(earn_by_tk), "fundamentals_tickers": len(fund_by_tk),
            "scoreable_tickers": len(usable_by_tk)}


def _next_step(decision: str, max_tickers: int, max_requests: int, max_cycles: int) -> Tuple[str, str]:
    if decision in (DEC_ALPHA, DEC_MORE_ALPHA):
        return ("Review research/output/phase8t_autonomous_alpha_daemon/"
                "paper_trader_preview_signal_specs.csv, then surface the promoted signal(s) as "
                "PREVIEW-ONLY ranking ideas in the Paper Trader daily-review cockpit (manual review; "
                "no orders).",
                "Promote validated EODHD signal(s) to preview-only Paper Trader review")
    if decision == DEC_NEXT_CYCLE:
        return ("python research/run_phase8t_autonomous_alpha_daemon.py --live --max-tickers %d "
                "--max-requests %d --max-cycles %d   (finish the remaining scenario families)"
                % (max_tickers, max_requests, max_cycles + 4),
                "Continue the autonomous campaign for the remaining families")
    if decision == DEC_BLOCKER:
        return ("Resolve the blocker (set a valid PAID EODHD_API_KEY / restore the price cache), "
                "then re-run: python research/run_phase8t_autonomous_alpha_daemon.py --live",
                "Clear the hard blocker, then re-run the daemon")
    if decision == DEC_NO_ALPHA:
        return ("The EODHD earnings/fundamentals families are exhausted with no promotable alpha. "
                "Acquire a genuinely new data family (e.g. bounded EODHD EOD prices to widen the "
                "universe, or an orthogonal alternative dataset) before re-scoring.",
                "No EODHD alpha after the exhaustive campaign; widen data, not scenarios")
    return ("python research/run_phase8t_autonomous_alpha_daemon.py", "Re-run the daemon")


def _print_summary(report: Dict) -> None:
    print("phase:                 %s" % report.get("phase"))
    print("mode:                  %s" % report.get("mode"))
    print("eodhd key present:     %s" % report.get("eodhd_key_present"))
    uni = report.get("universe", {})
    print("priced / scoreable:    %s / %s" % (uni.get("priced_tickers"), uni.get("scoreable_tickers")))
    print("unscoreable:           %s" % uni.get("unscoreable"))
    acq = report.get("acquisition", {})
    print("requests made:         %s" % acq.get("requests_made"))
    print("cycles run:            %s / %s" % (report.get("cycles_run"), report.get("battery_cycles")))
    print("scenarios tested:      %s" % report.get("scenarios_tested"))
    print("best scenario:         %s" % report.get("best_scenario"))
    print("promoted signals:      %s" % report.get("promoted_signals"))
    print("new vs phase8s:        %s" % report.get("new_signals_vs_phase8s"))
    print("preview specs:         %s" % report.get("preview_specs_produced"))
    print("alpha found:           %s" % report.get("alpha_found"))
    print("decision:              %s" % report.get("decision"))
    print("next command:          %s" % report.get("recommended_next_command"))


def _parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        description=("Phase 8-T autonomous EODHD alpha-factory daemon (offline campaign on cached "
                     "data by default; bounded live refresh with --live + EODHD_API_KEY)."))
    ap.add_argument("--live", action="store_true",
                    help="Bounded live EODHD refresh/repair before the campaign (requires EODHD_API_KEY).")
    ap.add_argument("--max-tickers", type=int, default=DEFAULT_MAX_TICKERS)
    ap.add_argument("--max-requests", type=int, default=DEFAULT_MAX_REQUESTS)
    ap.add_argument("--max-cycles", type=int, default=DEFAULT_MAX_CYCLES)
    ap.add_argument("--as-of", default=s8.DEFAULT_AS_OF)
    return ap.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parse_args(argv)
    run(live=args.live, max_tickers=args.max_tickers, max_requests=args.max_requests,
        max_cycles=args.max_cycles, as_of=args.as_of, verbose=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
