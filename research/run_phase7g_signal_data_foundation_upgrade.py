"""Phase 7-G — Signal Data Foundation Upgrade.

RESEARCH / DATA-QUALITY EXERCISE ONLY. This module inventories and (where feasible)
upgrades the *local* research dataset that feeds the System 1 multi-factor ranking
engine (Phase 7-C / 7-F). Its goal is a cleaner, broader, more historically consistent
data foundation so the Phase 7-F signal can later be re-evaluated under more realistic
conditions.

It is explicitly NOT, and contains NO logic for:
  * a trading system / production model / order or execution automation
  * factor-weight optimization or factor-sign flipping
  * regime-throttle activation (Phase 7-E regimes stay diagnostic)
  * live or paid data-provider calls (every input is a local, already-collected file)
  * Paper Trader / GCP / broker integration / deployment

What it does:
  1. Inventories the current universe and every relevant local data source.
  2. Determines whether a *broader* universe already exists locally.
  3. Determines whether *point-in-time* sector data exists locally.
  4. Determines whether denser quarterly / TTM fundamentals can be built from the
     existing local SEC artifact (by de-cumulating YTD 10-Q flows, point-in-time safe).
  5. Where feasible from local files, builds a broader-universe candidate and a
     quarterly/TTM readiness panel.
  6. Emits explicit go/no-go gates for whether Phase 7-H may re-run the signal on the
     upgraded foundation, and a controlled future data-build plan where it may not.

Reproducible, offline, read-only with respect to the price panel on D:. Writes only
under research/output/phase7g_signal_data_foundation_upgrade/. Does not commit or push.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field as dc_field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd

from research import run_phase7c_multifactor_ranking_engine as C

PHASE = "7-G"
OBJECTIVE = ("Upgrade the local research data foundation (universe breadth, point-in-time "
             "sector correctness, quarterly/TTM fundamental density, survivorship bias) so "
             "the Phase 7-F signal can be re-evaluated under more realistic conditions.")

# --------------------------------------------------------------------------- #
# Recommendation vocabulary (exactly the allowed set).
# --------------------------------------------------------------------------- #
REC_READY = "READY_FOR_PHASE7H_RETEST_SIGNAL_ON_UPGRADED_DATA"
REC_NEEDS_COLLECTION = "NEEDS_CONTROLLED_DATA_COLLECTION"
REC_PARTIAL = "DATA_FOUNDATION_PARTIAL"
REC_BLOCKED = "DATA_QUALITY_BLOCKED"
REC_ERROR = "ERROR"
ALLOWED_RECOMMENDATIONS = (REC_READY, REC_NEEDS_COLLECTION, REC_PARTIAL, REC_BLOCKED, REC_ERROR)

# --------------------------------------------------------------------------- #
# Paths — every source is local and already collected. Price panel is READ ONLY on D:.
# --------------------------------------------------------------------------- #
PRICE_CSV = C.PRICE_CSV
FUNDAMENTALS_CSV = C.FUNDAMENTALS_CSV
SECTOR_MAP_CSV = C.SECTOR_MAP_CSV
BENCHMARK = C.BENCHMARK
_REPO_ROOT = C._REPO_ROOT

# Candidate broader-universe / auxiliary sources discovered in the repo inventory.
SEC_COMPANY_TICKERS_JSON = _REPO_ROOT / "research" / "output" / \
    "phase3l_sec_universe_signal_gate" / "raw" / "company_tickers.json"
FREE_UNIVERSE_SAMPLE_CSV = _REPO_ROOT / "research" / "input" / \
    "phase2k_g_free_universe_tickers_sample.csv"
SIMFIN_COVERAGE_CSV = _REPO_ROOT / "research" / "output" / \
    "phase5e1e_simfin_free_collector" / "simfin_universe_coverage.csv"
GLOBAL_ASSET_UNIVERSE_CSV = _REPO_ROOT / "research" / "output" / \
    "phase3u_global_asset_universe_readiness" / "target_global_asset_universe.csv"

_OUT_DIR_DEFAULT = _REPO_ROOT / "research" / "output" / "phase7g_signal_data_foundation_upgrade"

# --------------------------------------------------------------------------- #
# Config (fixed, documented; no tuning loop). Thresholds are breadth/continuity
# criteria for *data readiness*, not signal-performance gates.
# --------------------------------------------------------------------------- #
MIN_NAMES = C.MIN_NAMES                 # 20 — minimum cross-section for a usable rank IC
QUARTER_GAP_LO_D = 80                   # contiguous-quarter spacing window (calendar days)
QUARTER_GAP_HI_D = 100
MIN_TTM_WINDOWS_PER_TICKER = 8          # >= 2yr of monthly-usable TTM points to call a ticker "continuity-ready"
CLEAN_FRAME_RE = re.compile(r"^CY\d{4}Q\d$")

# Flow fields that benefit from 3-month / TTM construction.
FLOW_FIELDS = ("revenue", "net_income", "operating_cash_flow", "operating_income",
               "capital_expenditures", "free_cash_flow", "eps_diluted")
# Core fields the TTM-continuity gate is judged on.
CORE_TTM_FIELDS = ("revenue", "net_income", "operating_cash_flow")
BALANCE_FIELDS = ("total_assets", "shareholder_equity", "total_liabilities")

NAN = float("nan")


def _f(x) -> Optional[float]:
    return C._f(x)


def _round(x, n=6):
    return C._round(x, n)


# --------------------------------------------------------------------------- #
# Output paths.
# --------------------------------------------------------------------------- #
@dataclass
class _OutPaths:
    base: Path

    def __post_init__(self):
        self.report = self.base / "phase7g_signal_data_foundation_upgrade.json"
        self.universe_inventory = self.base / "universe_inventory.csv"
        self.source_inventory = self.base / "local_data_source_inventory.csv"
        self.sector_readiness = self.base / "sector_data_readiness.csv"
        self.quarterly_readiness = self.base / "quarterly_ttm_readiness.csv"
        self.broader_universe = self.base / "broader_universe_candidate.csv"
        self.gate_matrix = self.base / "data_foundation_gate_matrix.csv"
        self.next_plan = self.base / "phase7h_next_plan.json"


def _rel(p: Path) -> str:
    try:
        return str(Path(p).relative_to(_REPO_ROOT)).replace(os.sep, "/")
    except Exception:
        return str(p)


def _write_json(path: Path, obj: Dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=2, default=str)


def _write_csv(path: Path, rows: List[Dict], columns: List[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame(rows, columns=columns) if rows else pd.DataFrame(columns=columns)
    df.to_csv(path, index=False)


# --------------------------------------------------------------------------- #
# Current-universe construction (identical to Phase 7-C / 7-F).
# --------------------------------------------------------------------------- #
def build_current_universe() -> Dict[str, object]:
    """The exact universe the engine ranks today: sector ∩ annual-fundamentals ∩ price."""
    sector_map, sector_pit = C.load_sector_map()
    annual = C.load_annual_fundamentals()
    fund_tickers = set(annual["ticker"].unique())
    prices = C.load_prices(universe=set(sector_map) | fund_tickers)
    price_tickers = set(prices["ticker"].unique())
    universe = sorted((set(sector_map) & fund_tickers) & price_tickers)
    return {
        "universe": universe,
        "sector_map": sector_map,
        "sector_pit": sector_pit,
        "annual": annual,
        "prices": prices,
        "price_tickers": price_tickers,
        "fund_tickers": fund_tickers,
        "sector_tickers": set(sector_map),
    }


# --------------------------------------------------------------------------- #
# 1. Universe inventory.
# --------------------------------------------------------------------------- #
def inventory_universe(ctx: Dict) -> Tuple[List[Dict], Dict]:
    prices = ctx["prices"]
    universe = ctx["universe"]
    uset = set(universe)
    pdates = prices["date"]
    # Per-ticker price span over the engine universe.
    span = prices[prices["ticker"].isin(uset)].groupby("ticker")["date"].agg(["min", "max", "count"])
    rows = [{
        "metric": "engine_universe_ticker_count",
        "value": len(universe),
        "detail": "sector ∩ annual-fundamentals ∩ price (tickers actually ranked)",
    }, {
        "metric": "price_panel_ticker_count",
        "value": len(ctx["price_tickers"]),
        "detail": f"distinct non-{BENCHMARK} tickers in the local adjusted price panel",
    }, {
        "metric": "sector_mapped_ticker_count",
        "value": len(ctx["sector_tickers"]),
        "detail": "tickers with a sector label in the (static, non-PIT) sector map",
    }, {
        "metric": "annual_fundamental_ticker_count",
        "value": len(ctx["fund_tickers"]),
        "detail": "tickers with point-in-time 10-K fundamentals locally",
    }, {
        "metric": "price_date_min",
        "value": str(pdates.min().date()),
        "detail": "first date in the local price panel",
    }, {
        "metric": "price_date_max",
        "value": str(pdates.max().date()),
        "detail": "last date in the local price panel",
    }, {
        "metric": "median_ticker_price_history_days",
        "value": int(span["count"].median()) if not span.empty else 0,
        "detail": "median per-ticker daily price observations across the universe",
    }, {
        "metric": "universe_full_history_fraction",
        "value": _round(float((span["min"] <= pdates.min() + pd.Timedelta(days=5)).mean())
                         if not span.empty else 0.0, 4),
        "detail": "fraction of universe present from the panel start (back-fill / late-listing proxy)",
    }]
    summary = {
        "engine_universe_count": len(universe),
        "price_panel_count": len(ctx["price_tickers"]),
        "sector_mapped_count": len(ctx["sector_tickers"]),
        "annual_fundamental_count": len(ctx["fund_tickers"]),
        "price_date_min": str(pdates.min().date()),
        "price_date_max": str(pdates.max().date()),
    }
    return rows, summary


# --------------------------------------------------------------------------- #
# 2-3. Local data-source inventory (does a broader universe already exist?).
# --------------------------------------------------------------------------- #
def _count_csv(path: Path, ticker_col: str = "ticker") -> Tuple[int, int]:
    try:
        df = pd.read_csv(path)
    except Exception:
        return 0, 0
    n = len(df)
    t = df[ticker_col].nunique() if ticker_col in df.columns else 0
    return n, t


def inventory_local_sources(ctx: Dict) -> Tuple[List[Dict], Dict]:
    rows: List[Dict] = []

    def add(name, path, kind, pit, expands_universe, note, rows_n=None, tickers_n=None):
        p = Path(path)
        exists = p.exists()
        rows.append({
            "source": name,
            "path": _rel(p),
            "exists": str(exists).lower(),
            "kind": kind,
            "rows": rows_n if rows_n is not None else "",
            "tickers": tickers_n if tickers_n is not None else "",
            "point_in_time": pit,
            "usable_for_universe_expansion": str(expands_universe).lower(),
            "note": note,
        })

    # Price panel — the binding constraint for any ranked universe.
    price_n = len(ctx["prices"])
    price_t = len(ctx["price_tickers"])
    add("adjusted_price_panel", PRICE_CSV, "daily_price", "n/a", False,
        "ONLY local daily-price source; bounds the ranked universe. No broader local price exists.",
        price_n, price_t)

    # SEC fundamentals (long-format, point-in-time).
    fn, ft = _count_csv(FUNDAMENTALS_CSV)
    add("sec_fundamentals_universe", FUNDAMENTALS_CSV, "sec_fundamentals", "true", False,
        "Point-in-time SEC 10-K/10-Q rows; only for tickers already in the price panel.", fn, ft)

    # Sector map (static, not PIT).
    sn, st = _count_csv(SECTOR_MAP_CSV)
    add("sector_map_current", SECTOR_MAP_CSV, "sector_classification", "false", False,
        "Static current-as-of GICS-like map; point_in_time=false. No PIT sector history locally.",
        sn, st)

    # SEC company_tickers.json — broad ticker→CIK list, but NO local price/fundamentals.
    sec_known = 0
    if SEC_COMPANY_TICKERS_JSON.exists():
        try:
            d = json.load(open(SEC_COMPANY_TICKERS_JSON, encoding="utf-8"))
            sec_known = len(d) if isinstance(d, list) else len(d.keys())
        except Exception:
            sec_known = 0
    add("sec_company_tickers", SEC_COMPANY_TICKERS_JSON, "ticker_directory", "n/a", False,
        "Ticker->CIK directory only; NO local price and NO collected fundamentals for these names. "
        "Defines the addressable-but-uncollected pool, not a usable universe.",
        sec_known, sec_known)

    # Free-universe sample (tiny, current-member, survivorship-caveated).
    un, ut = _count_csv(FREE_UNIVERSE_SAMPLE_CSV)
    add("free_universe_sample", FREE_UNIVERSE_SAMPLE_CSV, "ticker_list", "false", False,
        "Tiny manual current-member sample; explicitly not point-in-time / survivorship-caveated.",
        un, ut)

    # SimFin coverage — planned, nothing collected (collection would be a provider call).
    qn, qt = _count_csv(SIMFIN_COVERAGE_CSV)
    add("simfin_universe_coverage", SIMFIN_COVERAGE_CSV, "coverage_plan", "n/a", False,
        "Coverage plan only (status 'planned', 0 statements present). Collecting requires a "
        "live SimFin call — out of scope this phase.", qn, qt)

    # Global asset/ETF universe — ETFs, not single-name stocks; irrelevant to breadth.
    gn, gt = _count_csv(GLOBAL_ASSET_UNIVERSE_CSV)
    add("global_asset_universe", GLOBAL_ASSET_UNIVERSE_CSV, "etf_list", "n/a", False,
        "Cross-asset ETFs, not S&P single names; does not expand the equity ranking universe.",
        gn, gt)

    broader_local_exists = any(
        r["usable_for_universe_expansion"] == "true" for r in rows
    )
    summary = {
        "broader_local_universe_exists": broader_local_exists,
        "sec_known_tickers": sec_known,
        "price_covered_tickers": price_t,
        "addressable_but_uncollected": max(0, sec_known - price_t),
    }
    return rows, summary


# --------------------------------------------------------------------------- #
# 4. Point-in-time sector readiness.
# --------------------------------------------------------------------------- #
def sector_readiness(ctx: Dict) -> Tuple[List[Dict], Dict]:
    smap = ctx["sector_map"]
    pit = ctx["sector_pit"]
    universe = set(ctx["universe"])
    covered = len(universe & set(smap))
    sectors = sorted(set(smap.values()))
    rows = [{
        "check": "sector_source_present",
        "result": "true",
        "detail": _rel(SECTOR_MAP_CSV),
    }, {
        "check": "point_in_time",
        "result": str(pit).lower(),
        "detail": "map declares point_in_time=false (current-as-of static mapping)",
    }, {
        "check": "universe_sector_coverage",
        "result": f"{covered}/{len(universe)}",
        "detail": "engine-universe tickers with a sector label",
    }, {
        "check": "distinct_sectors",
        "result": str(len(sectors)),
        "detail": ", ".join(sectors),
    }, {
        "check": "pit_sector_history_available_locally",
        "result": "false",
        "detail": "No local source of historical (as-of-date) sector membership exists; "
                  "only the static current map. Sector reclassifications are not captured.",
    }]
    summary = {
        "pit_sector_available_local": False,
        "sector_coverage": covered,
        "universe": len(universe),
        "distinct_sectors": len(sectors),
    }
    return rows, summary


# --------------------------------------------------------------------------- #
# 5/10. Quarterly / TTM densification readiness (de-cumulation of YTD 10-Q flows).
# --------------------------------------------------------------------------- #
@dataclass
class _Q3mo:
    """A point-in-time 3-month flow observation."""
    ticker: str
    quarter_end: pd.Timestamp
    avail: pd.Timestamp
    value: float
    provenance: str  # clean_frame | q1_is_3mo | decumulated_ytd | annual_minus_q3ytd


def _load_flow_raw(field: str, universe: set) -> pd.DataFrame:
    df = pd.read_csv(FUNDAMENTALS_CSV, dtype={"ticker": str})
    df = df[df["normalized_field"] == field].copy()
    df = df[df["point_in_time_usable"].astype(str).str.lower().eq("true")]
    df = df[df["ticker"].isin(universe)]
    df["fpe"] = pd.to_datetime(df["fiscal_period_end"], errors="coerce")
    df["avail"] = C._avail_date(df["availability_datetime"])
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    df = df.dropna(subset=["fpe", "avail", "value"])
    df["is_clean3mo"] = df["frame"].astype(str).str.fullmatch(CLEAN_FRAME_RE)
    return df


_PRED_OF = {"Q2": "Q1", "Q3": "Q2"}  # the YTD predecessor period for de-cumulation


def build_3mo_quarters(field: str, universe: set) -> List[_Q3mo]:
    """Construct point-in-time 3-month flow observations from the local SEC artifact.

    Per ticker, three categories of the flow field are assembled:
      * clean CYyyyyQn frames  -> already 3-month, used directly (best provenance);
      * YTD 10-Q line items    -> cumulative within a fiscal year, de-cumulated;
      * the annual 10-K (FY)    -> used to recover Q4 = FY - Q3_YTD.

    De-cumulation is keyed by fiscal-period-end *spacing* (a predecessor must sit one
    quarter, 80-100 days, earlier) and by the period ladder (Q2<-Q1, Q3<-Q2), NOT by the
    `fiscal_year` column, which is off-by-one for some issuers. A clean Q1 frame doubles
    as the Q1 YTD baseline (Q1 YTD == Q1 3-month). Every leg must be available before the
    observation's availability date (= max of its constituents), so nothing leaks."""
    df = _load_flow_raw(field, universe)
    out: List[_Q3mo] = []

    for tk, g in df.groupby("ticker"):
        g = g.sort_values("avail")
        clean: Dict[pd.Timestamp, Tuple[pd.Timestamp, float, str]] = {}   # fpe -> (avail, val, period)
        ytd: Dict[pd.Timestamp, Tuple[pd.Timestamp, float, str]] = {}     # fpe -> (avail, val, period)
        fy: Dict[pd.Timestamp, Tuple[pd.Timestamp, float]] = {}           # fpe -> (avail, val)
        for r in g.itertuples():
            p = str(r.fiscal_period)
            if p == "FY":
                fy.setdefault(r.fpe, (r.avail, float(r.value)))
            elif bool(r.is_clean3mo) and p in ("Q1", "Q2", "Q3", "Q4"):
                clean.setdefault(r.fpe, (r.avail, float(r.value), p))
            elif p in ("Q1", "Q2", "Q3"):
                ytd.setdefault(r.fpe, (r.avail, float(r.value), p))

        emitted = set()

        # 1) Clean 3-month frames — direct, highest priority.
        for fpe, (av, val, p) in clean.items():
            out.append(_Q3mo(tk, fpe, av, val, "clean_frame"))
            emitted.add(fpe)

        # YTD-equivalent values for predecessor lookup: true YTD rows, plus a clean Q1
        # (whose 3-month value equals the Q1 YTD baseline).
        ytd_pred: Dict[pd.Timestamp, Tuple[pd.Timestamp, float, str]] = dict(ytd)
        for fpe, (av, val, p) in clean.items():
            if p == "Q1":
                ytd_pred.setdefault(fpe, (av, val, "Q1"))
        pred_sorted = sorted(ytd_pred.items())

        def _predecessor(fpe, want_period):
            for pf, (pa, pv, pp) in reversed(pred_sorted):
                if pf >= fpe:
                    continue
                gap = (fpe - pf).days
                if gap > QUARTER_GAP_HI_D:
                    break
                if QUARTER_GAP_LO_D <= gap <= QUARTER_GAP_HI_D and pp == want_period:
                    return pa, pv
            return None

        # 2) YTD de-cumulation (skip fpes already covered by a clean frame).
        for fpe, (av, val, p) in sorted(ytd.items()):
            if fpe in emitted:
                continue
            if p == "Q1":
                out.append(_Q3mo(tk, fpe, av, val, "q1_is_3mo"))
                emitted.add(fpe)
            else:
                pred = _predecessor(fpe, _PRED_OF[p])
                if pred is not None:
                    pa, pv = pred
                    out.append(_Q3mo(tk, fpe, max(av, pa), val - pv, "decumulated_ytd"))
                    emitted.add(fpe)

        # 3) Q4 = annual 10-K - Q3 YTD (Q3 one quarter before the FY end).
        for fpe, (av, val) in fy.items():
            if fpe in emitted:
                continue
            q3 = _predecessor(fpe, "Q3")
            if q3 is not None:
                q3a, q3v = q3
                out.append(_Q3mo(tk, fpe, max(av, q3a), val - q3v, "annual_minus_q3ytd"))
                emitted.add(fpe)

    out.sort(key=lambda q: (q.ticker, q.quarter_end))
    return out


def ttm_windows(quarters: List[_Q3mo]) -> Dict[str, List[Tuple[pd.Timestamp, pd.Timestamp]]]:
    """Per ticker, the set of trailing-4-quarter windows whose ends are contiguous
    (each ~one quarter apart). Returns {ticker: [(window_end, avail), ...]}. A window
    is point-in-time available at the max availability of its four legs."""
    by_t: Dict[str, List[_Q3mo]] = {}
    for q in quarters:
        by_t.setdefault(q.ticker, []).append(q)
    res: Dict[str, List[Tuple[pd.Timestamp, pd.Timestamp]]] = {}
    for tk, qs in by_t.items():
        # de-dup quarter_end (prefer clean frame already first via sort by avail not needed here)
        seen = {}
        for q in qs:
            seen.setdefault(q.quarter_end, q)
        ordered = [seen[k] for k in sorted(seen)]
        wins = []
        for i in range(3, len(ordered)):
            legs = ordered[i - 3:i + 1]
            ok = True
            for a, b in zip(legs, legs[1:]):
                gap = (b.quarter_end - a.quarter_end).days
                if not (QUARTER_GAP_LO_D <= gap <= QUARTER_GAP_HI_D):
                    ok = False
                    break
            if ok:
                wins.append((legs[-1].quarter_end, max(l.avail for l in legs)))
        if wins:
            res[tk] = wins
    return res


def quarterly_ttm_readiness(ctx: Dict) -> Tuple[List[Dict], Dict]:
    universe = set(ctx["universe"])
    rows: List[Dict] = []
    field_summary: Dict[str, Dict] = {}

    for fld in FLOW_FIELDS:
        quarters = build_3mo_quarters(fld, universe)
        by_t: Dict[str, List[_Q3mo]] = {}
        for q in quarters:
            by_t.setdefault(q.ticker, []).append(q)
        windows = ttm_windows(quarters)

        prov_counts = {"clean_frame": 0, "q1_is_3mo": 0, "decumulated_ytd": 0,
                       "annual_minus_q3ytd": 0}
        for q in quarters:
            prov_counts[q.provenance] = prov_counts.get(q.provenance, 0) + 1

        continuity_ready = 0
        for tk in sorted(by_t):
            qs = by_t[tk]
            ends = sorted({q.quarter_end for q in qs})
            n3 = len(ends)
            wins = windows.get(tk, [])
            n_ttm = len(wins)
            clean_n = sum(1 for q in qs if q.provenance == "clean_frame")
            ready = n_ttm >= MIN_TTM_WINDOWS_PER_TICKER
            if ready:
                continuity_ready += 1
            rows.append({
                "field": fld,
                "ticker": tk,
                "n_3mo_quarters": n3,
                "n_clean_frame": clean_n,
                "n_decumulated": n3 - clean_n,
                "n_ttm_windows": n_ttm,
                "first_quarter": str(ends[0].date()) if ends else "",
                "last_quarter": str(ends[-1].date()) if ends else "",
                "first_ttm_available": str(min(w[1] for w in wins).date()) if wins else "",
                "continuity_ready": str(ready).lower(),
            })
        field_summary[fld] = {
            "tickers_with_3mo": len(by_t),
            "total_3mo_quarters": len(quarters),
            "tickers_with_ttm": len(windows),
            "total_ttm_windows": int(sum(len(v) for v in windows.values())),
            "continuity_ready_tickers": continuity_ready,
            "provenance": prov_counts,
        }

    core_ready = {f: field_summary[f]["continuity_ready_tickers"] for f in CORE_TTM_FIELDS}
    densification_feasible = all(field_summary[f]["total_3mo_quarters"] > 0 for f in CORE_TTM_FIELDS)
    continuity_sufficient = all(core_ready[f] >= MIN_NAMES for f in CORE_TTM_FIELDS)

    summary = {
        "field_summary": field_summary,
        "core_continuity_ready": core_ready,
        "min_names": MIN_NAMES,
        "min_ttm_windows_per_ticker": MIN_TTM_WINDOWS_PER_TICKER,
        "densification_feasible": densification_feasible,
        "ttm_continuity_sufficient": continuity_sufficient,
    }
    return rows, summary


# --------------------------------------------------------------------------- #
# 9. Broader-universe candidate (bounded by local price coverage).
# --------------------------------------------------------------------------- #
def broader_universe_candidate(ctx: Dict) -> Tuple[List[Dict], Dict]:
    price_tickers = sorted(ctx["price_tickers"])
    fund = ctx["fund_tickers"]
    sect = ctx["sector_tickers"]
    engine = set(ctx["universe"])
    rows = []
    for tk in price_tickers:
        in_engine = tk in engine
        rows.append({
            "ticker": tk,
            "has_local_price": "true",
            "has_annual_fundamentals": str(tk in fund).lower(),
            "has_sector": str(tk in sect).lower(),
            "in_current_engine_universe": str(in_engine).lower(),
            # No broader local price source exists, so no name is locally expandable
            # beyond the existing panel; flagged honestly rather than fabricated.
            "locally_expandable": "false",
        })
    candidate_count = len(price_tickers)
    summary = {
        "candidate_universe_count": candidate_count,
        "equals_current_engine_universe": candidate_count == len(engine) or
                                          set(price_tickers) >= engine,
        "engine_universe_count": len(engine),
        "broader_than_current": candidate_count > len(engine),
        "note": ("Candidate universe is bounded by the single local price panel. SEC lists "
                 "thousands of names but none have local price/fundamentals, so no broader "
                 "research universe can be assembled locally without controlled collection."),
    }
    return rows, summary


# --------------------------------------------------------------------------- #
# 11. Go/no-go gate matrix.
# --------------------------------------------------------------------------- #
def build_gate_matrix(univ_s, src_s, sect_s, qttm_s, cand_s) -> Tuple[List[Dict], Dict]:
    gates: List[Dict] = []

    def g(name, status, detail, kind="capability"):
        gates.append({"gate": name, "status": status, "kind": kind, "detail": detail})

    # ---- Data-foundation capability gates ----
    g("broader_universe_available_locally",
      "PASS" if src_s["broader_local_universe_exists"] else "FAIL",
      f"price-covered={src_s['price_covered_tickers']}, SEC-known={src_s['sec_known_tickers']}, "
      f"addressable-but-uncollected={src_s['addressable_but_uncollected']}")

    g("survivorship_bias_addressable_locally", "FAIL",
      "No point-in-time historical S&P membership locally; only current-constituent names "
      "have price/fundamentals. Survivorship/current-constituent bias cannot be removed locally.")

    g("point_in_time_sector_available_locally",
      "PASS" if sect_s["pit_sector_available_local"] else "FAIL",
      "Only a static current-as-of sector map (point_in_time=false) exists locally.")

    g("quarterly_3mo_densification_feasible",
      "PASS" if qttm_s["densification_feasible"] else "FAIL",
      "YTD 10-Q flows de-cumulate into point-in-time 3-month quarters for the core fields: "
      + ", ".join(f"{f}={qttm_s['field_summary'][f]['total_3mo_quarters']}q" for f in CORE_TTM_FIELDS))

    g("ttm_continuity_sufficient",
      "PASS" if qttm_s["ttm_continuity_sufficient"] else "FAIL",
      f"continuity-ready tickers (>= {MIN_TTM_WINDOWS_PER_TICKER} TTM windows) vs MIN_NAMES="
      f"{MIN_NAMES}: " + ", ".join(f"{f}={qttm_s['core_continuity_ready'][f]}" for f in CORE_TTM_FIELDS))

    fund_density_upgrade = (qttm_s["densification_feasible"] and qttm_s["ttm_continuity_sufficient"])
    g("fundamental_density_materially_upgradable_locally",
      "PASS" if fund_density_upgrade else "FAIL",
      "A denser quarterly/TTM fundamental panel CAN be built locally (de-cumulation), a real "
      "upgrade over Phase 7-F's clean-frame-only TTM (which graded 0 months).")

    # ---- Safety gates (must all PASS) ----
    g("no_live_data_calls", "PASS", "All inputs are local, already-collected files.", "safety")
    g("no_paid_api_calls", "PASS", "No provider/paid API invoked.", "safety")
    g("reproducible_offline", "PASS", "Deterministic; no randomness, no network.", "safety")
    g("no_trading_order_automation", "PASS", "No order/execution/automation logic present.", "safety")
    g("no_factor_weight_optimization", "PASS", "No factor weighting/tuning in this phase.", "safety")
    g("no_regime_throttle_activation", "PASS", "No regime activation logic in this phase.", "safety")
    g("price_panel_read_only", "PASS", "D: price panel opened read-only; nothing written to D:.", "safety")

    n_pass = sum(1 for x in gates if x["status"] == "PASS")
    n_fail = sum(1 for x in gates if x["status"] == "FAIL")
    safety_fail = any(x["kind"] == "safety" and x["status"] != "PASS" for x in gates)
    summary = {
        "pass": n_pass, "fail": n_fail,
        "safety_fail": safety_fail,
        "fundamental_density_upgradable": fund_density_upgrade,
        "broader_universe_local": src_s["broader_local_universe_exists"],
        "pit_sector_local": sect_s["pit_sector_available_local"],
    }
    return gates, summary


# --------------------------------------------------------------------------- #
# Recommendation + next-phase plan.
# --------------------------------------------------------------------------- #
def derive_recommendation(gate_s: Dict) -> str:
    if gate_s["safety_fail"]:
        return REC_BLOCKED
    broader = gate_s["broader_universe_local"]
    pit_sector = gate_s["pit_sector_local"]
    fund_upgrade = gate_s["fundamental_density_upgradable"]
    # Full readiness would require broader universe AND PIT sectors AND denser fundamentals.
    if broader and pit_sector and fund_upgrade:
        return REC_READY
    # A real local upgrade exists (denser fundamentals) but breadth/PIT-sector do not.
    if fund_upgrade:
        return REC_PARTIAL
    # Nothing locally upgradable -> must collect.
    return REC_NEEDS_COLLECTION


def build_next_plan(rec: str, univ_s, src_s, sect_s, qttm_s, cand_s) -> Dict:
    immediate = []
    if qttm_s["densification_feasible"] and qttm_s["ttm_continuity_sufficient"]:
        immediate.append({
            "step": "build_dense_ttm_fundamental_panel",
            "scope": "LOCAL_ONLY",
            "detail": ("De-cumulate YTD 10-Q flows into point-in-time 3-month quarters and roll "
                       "trailing-4-quarter TTM for revenue / net_income / operating_cash_flow / "
                       "operating_income / capex / fcf / eps, then rebuild Phase 7-F value/quality/"
                       "growth buckets on TTM levels (equal-weight, no sign flipping, no optimization)."),
        })
        immediate.append({
            "step": "rerun_phase7f_fundamental_signal_on_dense_ttm",
            "scope": "LOCAL_ONLY",
            "detail": ("Re-grade the upgraded composite through the UNMODIFIED Phase 7-B harness on "
                       "the same ~128-name universe. This is a fundamental-density retest only; it "
                       "does NOT resolve universe breadth or survivorship bias."),
            "caveat": "Same-universe; not a true robustness retest of the dominant 7-F weakness.",
        })
    collection_gated = [{
        "step": "collect_historical_pit_universe_membership",
        "scope": "CONTROLLED_COLLECTION_REQUIRED",
        "blocker": "No local point-in-time S&P membership / delisted-name price history.",
        "detail": ("Acquire survivorship-aware historical index membership plus daily prices for "
                   "names beyond the current 128 (incl. delisted), via a controlled, budgeted, "
                   "explicitly-approved collection run — NOT in this phase."),
    }, {
        "step": "collect_point_in_time_sector_history",
        "scope": "CONTROLLED_COLLECTION_REQUIRED",
        "blocker": "Only a static current-as-of sector map exists; no as-of-date sector history.",
        "detail": "Acquire historical GICS/sector assignments with effective dates for PIT neutralization.",
    }, {
        "step": "optional_quarterly_fundamental_provider_backfill",
        "scope": "CONTROLLED_COLLECTION_REQUIRED",
        "blocker": "SimFin coverage is 'planned' (0 collected); SEC clean 3-month frames are sparse.",
        "detail": ("Only if local de-cumulation proves insufficient: a controlled SimFin/SEC backfill "
                   "to densify quarterly fundamentals further."),
    }]
    return {
        "from_phase": PHASE,
        "recommendation": rec,
        "phase7h_retest_allowed": rec in (REC_READY, REC_PARTIAL),
        "phase7h_retest_scope": ("fundamental_density_only" if rec == REC_PARTIAL
                                 else ("full_upgraded_foundation" if rec == REC_READY else "none")),
        "immediate_local_steps": immediate,
        "collection_gated_steps": collection_gated,
        "hard_constraints": [
            "no live or paid data calls", "no factor-weight optimization", "no factor-sign flipping",
            "regimes diagnostic only", "no trading/order/automation", "do not commit", "do not push",
        ],
    }


# --------------------------------------------------------------------------- #
# Report assembly + orchestration.
# --------------------------------------------------------------------------- #
def _assemble_report(rec, paths, univ_s, src_s, sect_s, qttm_s, cand_s, gates, gate_s, next_plan) -> Dict:
    return {
        "phase": PHASE,
        "objective": OBJECTIVE,
        "recommendation": rec,
        "allowed_recommendations": list(ALLOWED_RECOMMENDATIONS),
        "determinations": {
            "broader_universe_exists_locally": src_s["broader_local_universe_exists"],
            "point_in_time_sectors_exist_locally": sect_s["pit_sector_available_local"],
            "denser_quarterly_ttm_feasible_locally": qttm_s["densification_feasible"] and
                                                      qttm_s["ttm_continuity_sufficient"],
            "phase7h_retest_allowed": next_plan["phase7h_retest_allowed"],
            "phase7h_retest_scope": next_plan["phase7h_retest_scope"],
        },
        "universe_inventory": univ_s,
        "local_source_inventory": src_s,
        "sector_readiness": sect_s,
        "quarterly_ttm_readiness": {
            "core_continuity_ready": qttm_s["core_continuity_ready"],
            "densification_feasible": qttm_s["densification_feasible"],
            "ttm_continuity_sufficient": qttm_s["ttm_continuity_sufficient"],
            "field_summary": qttm_s["field_summary"],
        },
        "broader_universe_candidate": cand_s,
        "gate_matrix_summary": gate_s,
        "remaining_blockers": [
            "constrained ~128-name current-constituent universe (no broader local price)",
            "survivorship / current-constituent bias (no delisted-name history locally)",
            "static non-point-in-time sector classification (no as-of-date sector history)",
        ] + ([] if qttm_s["ttm_continuity_sufficient"]
             else ["quarterly/TTM continuity below MIN_NAMES on a core field"]),
        "phase7h_next_plan": next_plan,
        "artifacts": {
            "report": _rel(paths.report),
            "universe_inventory": _rel(paths.universe_inventory),
            "local_data_source_inventory": _rel(paths.source_inventory),
            "sector_data_readiness": _rel(paths.sector_readiness),
            "quarterly_ttm_readiness": _rel(paths.quarterly_readiness),
            "broader_universe_candidate": _rel(paths.broader_universe),
            "data_foundation_gate_matrix": _rel(paths.gate_matrix),
            "phase7h_next_plan": _rel(paths.next_plan),
        },
        "safety": {
            "preview_only": True,
            "live_data_used": False,
            "paid_api_used": False,
            "trading_order_automation": False,
            "factor_weights_optimized": False,
            "factor_signs_flipped": False,
            "regimes_used_to_select_or_weight": False,
            "wrote_to_price_drive": False,
            "committed": False,
            "pushed": False,
        },
    }


def _error_report(msg: str, tb: str) -> Dict:
    return {"phase": PHASE, "recommendation": REC_ERROR, "error": msg, "traceback": tb,
            "allowed_recommendations": list(ALLOWED_RECOMMENDATIONS)}


def _run(paths: _OutPaths) -> Dict:
    ctx = build_current_universe()

    univ_rows, univ_s = inventory_universe(ctx)
    src_rows, src_s = inventory_local_sources(ctx)
    sect_rows, sect_s = sector_readiness(ctx)
    qttm_rows, qttm_s = quarterly_ttm_readiness(ctx)
    cand_rows, cand_s = broader_universe_candidate(ctx)

    gates, gate_s = build_gate_matrix(univ_s, src_s, sect_s, qttm_s, cand_s)
    rec = derive_recommendation(gate_s)
    next_plan = build_next_plan(rec, univ_s, src_s, sect_s, qttm_s, cand_s)

    # Artifacts.
    _write_csv(paths.universe_inventory, univ_rows, ["metric", "value", "detail"])
    _write_csv(paths.source_inventory, src_rows,
               ["source", "path", "exists", "kind", "rows", "tickers", "point_in_time",
                "usable_for_universe_expansion", "note"])
    _write_csv(paths.sector_readiness, sect_rows, ["check", "result", "detail"])
    _write_csv(paths.quarterly_readiness, qttm_rows,
               ["field", "ticker", "n_3mo_quarters", "n_clean_frame", "n_decumulated",
                "n_ttm_windows", "first_quarter", "last_quarter", "first_ttm_available",
                "continuity_ready"])
    _write_csv(paths.broader_universe, cand_rows,
               ["ticker", "has_local_price", "has_annual_fundamentals", "has_sector",
                "in_current_engine_universe", "locally_expandable"])
    _write_csv(paths.gate_matrix, gates, ["gate", "status", "kind", "detail"])
    _write_json(paths.next_plan, next_plan)

    report = _assemble_report(rec, paths, univ_s, src_s, sect_s, qttm_s, cand_s,
                              gates, gate_s, next_plan)
    return report


def run(out_dir: Optional[str] = None) -> Dict:
    paths = _OutPaths(Path(out_dir) if out_dir else _OUT_DIR_DEFAULT)
    try:
        report = _run(paths)
    except Exception as exc:  # pragma: no cover - defensive
        import traceback
        report = _error_report(str(exc), traceback.format_exc())
        _write_json(paths.report, report)
        return report
    _write_json(paths.report, report)
    return report


def _print_summary(report: Dict) -> None:
    print(f"[{PHASE}] recommendation = {report['recommendation']}")
    d = report.get("determinations", {})
    print(f"  broader universe locally        : {d.get('broader_universe_exists_locally')}")
    print(f"  PIT sectors locally             : {d.get('point_in_time_sectors_exist_locally')}")
    print(f"  denser quarterly/TTM feasible   : {d.get('denser_quarterly_ttm_feasible_locally')}")
    print(f"  Phase 7-H retest allowed        : {d.get('phase7h_retest_allowed')} "
          f"({d.get('phase7h_retest_scope')})")
    qs = report.get("quarterly_ttm_readiness", {})
    print(f"  core TTM continuity-ready tickers: {qs.get('core_continuity_ready')}")
    gs = report.get("gate_matrix_summary", {})
    print(f"  gates: PASS={gs.get('pass')} FAIL={gs.get('fail')} safety_fail={gs.get('safety_fail')}")


def main(argv=None) -> int:
    out_dir = None
    if argv:
        out_dir = argv[0]
    report = run(out_dir)
    _print_summary(report)
    return 0 if report.get("recommendation") in ALLOWED_RECOMMENDATIONS and \
        report.get("recommendation") != REC_ERROR else 1


if __name__ == "__main__":
    import sys
    raise SystemExit(main(sys.argv[1:]))
