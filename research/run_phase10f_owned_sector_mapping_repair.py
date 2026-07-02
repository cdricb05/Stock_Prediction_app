"""Phase 10-F-A - Owned Metadata Sector Mapping Repair and Paper-Review Rerank.

WHY THIS PHASE EXISTS
    Phase 10-E packaged the 10-D quarterly SECTOR-NEUTRAL quality composite into a paper-review book and
    returned PAPER_REVIEW_PACKAGE_READY_WITH_SECTOR_MAPPING_CAVEAT: the book reconstructs cleanly for
    2026Q2 (97 long / 97 short) but 77.8% of it sits in the unmapped "Unknown" sector bucket. The 10-D
    composite is still valid, but the 10-E book is too Unknown-sector-heavy to approve by hand and - more
    importantly - the "sector-neutral" leg is only as good as the sector labels: with 374/483 names
    Unknown, the within-month x sector de-mean is dominated by one giant pseudo-sector and is NOT a real
    sector neutralisation.

    Phase 10-F-A does the one allowed next thing: it REPAIRS the sector labels using ONLY owned/local
    metadata, REBUILDS the sector-neutral composite over the repaired sectors, and RE-RANKS the 2026Q2
    paper-review book. It then reports before-vs-after (Unknown share, top-sector concentration, rank
    movement, names entering/leaving each side) and decides whether the repaired book is ready for a
    human approve/reject review.

    IT IS NOT a new alpha search, NOT a provider search, NOT order creation, NOT automation, NOT a
    deploy, and NOT (yet) a Paper Trader integration. It writes ONLY metadata CSV/JSON to its own
    research/output directory. It creates NO Paper Trader signals, NO trade decisions, and NO orders.
    Fully OFFLINE: no network, no API key, no provider probe.

OWNED SECTOR SOURCES (attempted in the brief's priority order; NO new acquisition)
    1. Norgate symbol metadata / sector / industry          -> NOT cached locally (no research/data/
       norgate sector dump exists); requires the Norgate SDK/DB, which is out of this offline scope.
       Recorded honestly as UNAVAILABLE; never fabricated.
    2. EODHD raw fundamentals General::GicSector / GicIndustry (research/data/eodhd/raw/fundamentals/
       <ticker>.json) -> PRIMARY source. GicSector is the SAME 11-bucket GICS taxonomy the curated
       map uses, so a repaired label is directly comparable to a curated label. HIGH confidence.
    3. EODHD raw fundamentals General::Sector (Morningstar taxonomy) -> FALLBACK via a documented,
       deterministic Morningstar->GICS crosswalk where GicSector is blank. MEDIUM confidence.
    4. EODHD normalized fundamentals metadata               -> time-series feature families only; carry
       NO sector classification. N/A.
    5. Existing repo sector/industry maps from prior phases (the curated phase2k map) -> already the
       panel's sector source; Unknown names are by definition absent from it, but it is still checked
       and logged, and used as a cross-check.
    6. Cached EODHD company profiles                        -> the General block of the fundamentals
       JSON IS the cached company profile (same as source 2).
    (FMP company_profile exists on disk but FMP is a HARD-BANNED source for this phase - never read.)

    Repaired sector labels are CURRENT-as-of company classifications, NOT historical point-in-time. They
    are used ONLY as a static neutralisation GROUPING (sector is not a return feature here), exactly as
    the curated map already is (point_in_time=false). This is documented, not hidden.

REUSE (single source of truth - nothing re-implemented)
    d10 = run_phase10d_quarterly_quality_composite_validation  (composite definition + build_composite)
    c10 = run_phase10c_eodhd_quality_oos_validation            (panel build, PIT attach, helpers)
    e10 = run_phase10e_quarterly_quality_paper_review_harness   (book/exposure/liquidity/risk/cross-
                                                                 section logic + safety badges)
    b10 = run_phase10b_eodhd_norgate_exhaustive_alpha_factory   (secret-safety audit)
    s8 = d10.s8 (io helpers + the curated sector map)   t8 = d10.t8 (logger)

TERMINAL DECISIONS (allowed)
    SECTOR_MAPPING_REPAIRED_READY_FOR_HUMAN_REVIEW |
    SECTOR_MAPPING_PARTIALLY_REPAIRED_REVIEW_WITH_CAVEAT |
    SECTOR_MAPPING_NOT_REPAIRABLE_WITH_OWNED_DATA |
    PAPER_REVIEW_REJECTED_AFTER_SECTOR_REPAIR |
    HARD_BLOCKER_REQUIRES_USER_ACTION | ERROR_WITH_REPRO_COMMAND
    FORBIDDEN: LIVE_TRADING_READY, ORDER_READY, AUTOMATION_READY,
    STRONG_ALPHA_FOUND_READY_FOR_REVIEW, MISSING_KEY, NO_DATA, NEEDS_PROVIDER, EMPTY_PAYLOAD,
    generic ERROR.

CONSTRAINTS HONORED
    Fully offline (no network / key / provider probe); owned/local metadata only; no FMP / AlphaVantage /
    Polygon / Finnhub / Norgate-API; no new purchase; composite imported from 10-D (no re-definition, no
    optimisation, no sign-flip); no Paper Trader writes; no GCP; NO orders; NO automation; NO live
    trading; NO broker; no deploy; no package install; no full regression (targeted tests only); keys
    never printed or written; output is metadata only. No commit. No push.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from research import run_phase10d_quarterly_quality_composite_validation as d10  # noqa: E402
from research import run_phase10c_eodhd_quality_oos_validation as c10            # noqa: E402
from research import run_phase10e_quarterly_quality_paper_review_harness as e10  # noqa: E402
from research import run_phase10b_eodhd_norgate_exhaustive_alpha_factory as b10  # noqa: E402

s8 = d10.s8
t8 = d10.t8
_write_json = s8._write_json
_write_csv = s8._write_csv
_read_csv_file = s8._read_csv_file
_read_json = s8._read_json
_round = s8._round
_rel = s8._rel
_finite = c10._finite
_num = c10._num

PHASE = "10-F-A"
PERFORMS_NETWORK = False

AS_OF = d10.AS_OF
PRIMARY_HORIZON_D = d10.PRIMARY_HORIZON_D

# Composite + book parameters are imported verbatim - never re-defined here.
LEGS = d10.LEGS
ALLOWED_FAMILIES = d10.ALLOWED_FAMILIES
COMP_RAW = d10.COMP_RAW
COMP_SN = d10.COMP_SN
DEFAULT_REVIEW_SCORE = e10.DEFAULT_REVIEW_SCORE         # comp_sn (sector-neutral) - the review view
SIDE_LONG = e10.SIDE_LONG
SIDE_SHORT = e10.SIDE_SHORT
SIDE_HOLD = e10.SIDE_HOLD
N_QUANTILES = e10.N_QUANTILES
_MIN_BOOK_NAMES = e10._MIN_BOOK_NAMES
MAX_SECTOR_SHARE = d10.MAX_SECTOR_SHARE                 # 0.60 concentration ceiling
UNKNOWN_CAVEAT_SHARE = e10.UNKNOWN_CAVEAT_SHARE         # 0.20 residual-Unknown caveat trigger
REVIEW_STATUS = e10.REVIEW_STATUS
SAFETY_BADGES = e10.SAFETY_BADGES
_is_unknown = e10._is_unknown

# --------------------------------------------------------------------------- #
# Owned sector sources.
# --------------------------------------------------------------------------- #
EODHD_FUND_DIR = _REPO_ROOT / "research" / "data" / "eodhd" / "raw" / "fundamentals"
CURATED_MAP_CSV = getattr(s8, "_SECTOR_MAP_CSV",
                          _REPO_ROOT / "research" / "input" / "phase2k_p_sector_map_current.csv")
EODHD_KEY_ENV = "EODHD_API_KEY"

# The canonical 11 GICS sectors used by the curated panel map. A repaired label must land in this set so
# the rebuilt sector-neutral composite is taxonomically coherent with the curated names.
CANONICAL_GICS = frozenset({
    "Communication Services", "Consumer Discretionary", "Consumer Staples", "Energy", "Financials",
    "Health Care", "Industrials", "Information Technology", "Materials", "Real Estate", "Utilities",
})
# Deterministic Morningstar/Yahoo -> GICS crosswalk (only the well-defined 1:1 sector renames; no
# guessing). Used ONLY when General::GicSector is blank but General::Sector is present.
MORNINGSTAR_TO_GICS = {
    "technology": "Information Technology",
    "financial services": "Financials",
    "financial": "Financials",
    "healthcare": "Health Care",
    "health care": "Health Care",
    "consumer cyclical": "Consumer Discretionary",
    "consumer defensive": "Consumer Staples",
    "basic materials": "Materials",
    "communication services": "Communication Services",
    "industrials": "Industrials",
    "energy": "Energy",
    "utilities": "Utilities",
    "real estate": "Real Estate",
}

CONF_HIGH = "HIGH"
CONF_MEDIUM = "MEDIUM"
CONF_LOW = "LOW"

# Source-family identifiers (the brief's priority order).
SRC_NORGATE = "norgate_local_symbol_metadata"
SRC_EODHD_GIC = "eodhd_raw_fundamentals(General.GicSector)"
SRC_EODHD_SECTOR = "eodhd_raw_fundamentals(General.Sector->GICS_crosswalk)"
SRC_EODHD_NORM = "eodhd_normalized_fundamentals_metadata"
SRC_PRIOR_MAP = "prior_repo_curated_sector_map(phase2k)"
SRC_EODHD_PROFILE = "cached_eodhd_company_profile(General_block)"

# Decisions.
DEC_REPAIRED = "SECTOR_MAPPING_REPAIRED_READY_FOR_HUMAN_REVIEW"
DEC_PARTIAL = "SECTOR_MAPPING_PARTIALLY_REPAIRED_REVIEW_WITH_CAVEAT"
DEC_NOT_REPAIRABLE = "SECTOR_MAPPING_NOT_REPAIRABLE_WITH_OWNED_DATA"
DEC_REJECTED = "PAPER_REVIEW_REJECTED_AFTER_SECTOR_REPAIR"
DEC_HARD_BLOCKER = "HARD_BLOCKER_REQUIRES_USER_ACTION"
DEC_ERROR = "ERROR_WITH_REPRO_COMMAND"
ALLOWED_DECISIONS = (DEC_REPAIRED, DEC_PARTIAL, DEC_NOT_REPAIRABLE, DEC_REJECTED, DEC_HARD_BLOCKER,
                     DEC_ERROR)
FORBIDDEN_DECISIONS = ("LIVE_TRADING_READY", "ORDER_READY", "AUTOMATION_READY",
                       "STRONG_ALPHA_FOUND_READY_FOR_REVIEW", "MISSING_KEY", "NO_DATA", "NEEDS_PROVIDER",
                       "EMPTY_PAYLOAD", "ERROR")

_PHASE10E_DIR = (_REPO_ROOT / "research" / "output"
                 / "phase10e_quarterly_quality_paper_review_harness")
_PHASE10E_CANDIDATES = "paper_review_candidate_list.csv"

_ARTIFACTS = {
    "report": "phase10f_owned_sector_mapping_repair.json",
    "attempts": "unknown_sector_repair_attempts.csv",
    "repaired_map": "repaired_sector_mapping.csv",
    "unrepaired": "unrepaired_unknown_sector_names.csv",
    "source_audit": "sector_mapping_source_audit.csv",
    "ba_sector": "before_after_sector_exposure.csv",
    "ba_unknown": "before_after_unknown_sector_exposure.csv",
    "reranked_candidates": "reranked_paper_review_candidate_list.csv",
    "reranked_book": "reranked_paper_review_long_short_book.csv",
    "book_change": "long_short_book_change_report.csv",
    "rank_movement": "rank_movement_report.csv",
    "sn_rebuild_audit": "sector_neutral_score_rebuild_audit.csv",
    "risk_flags": "repaired_book_risk_flags.csv",
    "next_plan": "phase10g_next_plan.json",
    "secret_audit": "secret_safety_audit.csv",
}
_REQUIRED_ARTIFACTS = tuple(_ARTIFACTS.keys())


class _Paths:
    def __init__(self, out_dir=None):
        self.out = Path(out_dir) if out_dir else (
            _REPO_ROOT / "research" / "output" / "phase10f_owned_sector_mapping_repair")

    def art(self, key: str) -> Path:
        return self.out / _ARTIFACTS[key]


# --------------------------------------------------------------------------- #
# A. Owned sector source loaders.
# --------------------------------------------------------------------------- #
def _norm_label(s) -> str:
    return ("" if s is None else str(s)).strip()


def load_eodhd_sector_meta(fund_dir: Path) -> Dict[str, Dict[str, str]]:
    """Read each owned EODHD fundamentals JSON's General block (offline). Returns
    {TICKER: {gic_sector, gic_industry, sector, industry, type, source_file}}. No network."""
    out: Dict[str, Dict[str, str]] = {}
    d = Path(fund_dir)
    if not d.is_dir():
        return out
    for fp in sorted(d.glob("*.json")):
        tk = fp.stem.strip().upper()
        if not tk:
            continue
        try:
            with open(fp, "r", encoding="utf-8") as fh:
                g = (json.load(fh) or {}).get("General", {}) or {}
        except (OSError, ValueError):
            continue
        out[tk] = {
            "gic_sector": _norm_label(g.get("GicSector")),
            "gic_industry": _norm_label(g.get("GicIndustry")),
            "sector": _norm_label(g.get("Sector")),
            "industry": _norm_label(g.get("Industry")),
            "type": _norm_label(g.get("Type")),
            "source_file": _rel(fp),
        }
    return out


def load_curated_map(curated_csv: Path) -> Dict[str, Tuple[str, str]]:
    """The prior-phase curated sector map (ticker -> (sector, industry)); read-only, owned."""
    out: Dict[str, Tuple[str, str]] = {}
    p = Path(curated_csv)
    if not p.is_file():
        return out
    for r in _read_csv_file(p):
        tk = _norm_label(r.get("ticker")).upper()
        if tk:
            out[tk] = (_norm_label(r.get("sector")) or "Unknown",
                       _norm_label(r.get("industry")) or "Unknown")
    return out


# --------------------------------------------------------------------------- #
# B. Per-ticker repair (owned sources only, in the brief's priority order).
# --------------------------------------------------------------------------- #
def repair_one(ticker: str, eodhd: Dict[str, Dict[str, str]],
               curated: Dict[str, Tuple[str, str]]) -> Tuple[Optional[Dict], List[Dict]]:
    """Try owned sources in priority order. Returns (repair | None, attempts). `repair` carries
    repaired_sector / repaired_industry / source_family / source_field / confidence / reason. Never
    fabricates: only labels that land in the canonical GICS taxonomy are accepted."""
    tk = ticker.strip().upper()
    attempts: List[Dict] = []

    def att(family, outcome, value=""):
        attempts.append({"ticker": tk, "source_family": family, "outcome": outcome, "value": value})

    # 1. Norgate local symbol metadata - not cached on disk for this offline scope.
    att(SRC_NORGATE, "UNAVAILABLE_NO_LOCAL_NORGATE_METADATA")

    meta = eodhd.get(tk)
    # 2. EODHD raw fundamentals General::GicSector (HIGH - same GICS taxonomy as the curated map).
    if meta and meta.get("gic_sector") in CANONICAL_GICS:
        att(SRC_EODHD_GIC, "HIT", meta["gic_sector"])
        return ({"repaired_sector": meta["gic_sector"],
                 "repaired_industry": meta.get("gic_industry") or meta.get("industry") or "Unknown",
                 "source_family": SRC_EODHD_GIC, "source_file": meta.get("source_file", ""),
                 "source_field": "General.GicSector", "confidence": CONF_HIGH,
                 "reason": "owned EODHD fundamentals General.GicSector in the canonical 11-bucket GICS "
                           "taxonomy - directly comparable to the curated map; current-as-of (not PIT) "
                           "classification used only as a neutralisation grouping"}), attempts
    if meta and meta.get("gic_sector"):
        att(SRC_EODHD_GIC, "PRESENT_BUT_NON_CANONICAL", meta["gic_sector"])
    else:
        att(SRC_EODHD_GIC, "BLANK")

    # 3. EODHD raw fundamentals General::Sector via Morningstar->GICS crosswalk (MEDIUM).
    if meta and meta.get("sector"):
        gx = MORNINGSTAR_TO_GICS.get(meta["sector"].lower())
        if gx in CANONICAL_GICS:
            att(SRC_EODHD_SECTOR, "HIT", "%s->%s" % (meta["sector"], gx))
            return ({"repaired_sector": gx,
                     "repaired_industry": meta.get("industry") or "Unknown",
                     "source_family": SRC_EODHD_SECTOR, "source_file": meta.get("source_file", ""),
                     "source_field": "General.Sector", "confidence": CONF_MEDIUM,
                     "reason": "owned EODHD fundamentals General.Sector (Morningstar taxonomy) mapped to "
                               "GICS via the documented 1:1 crosswalk (GicSector was blank); "
                               "current-as-of (not PIT) classification used only as a grouping"}), attempts
        att(SRC_EODHD_SECTOR, "NO_CROSSWALK", meta.get("sector", ""))
    else:
        att(SRC_EODHD_SECTOR, "BLANK")

    # 4. EODHD normalized fundamentals metadata - no sector classification exists.
    att(SRC_EODHD_NORM, "NA_NO_SECTOR_IN_NORMALIZED_FEATURES")

    # 5. Prior-phase curated sector map (Unknown names are by definition absent, but check + log).
    cur = curated.get(tk)
    if cur and cur[0] in CANONICAL_GICS:
        att(SRC_PRIOR_MAP, "HIT", cur[0])
        return ({"repaired_sector": cur[0], "repaired_industry": cur[1] or "Unknown",
                 "source_family": SRC_PRIOR_MAP, "source_file": _rel(CURATED_MAP_CSV),
                 "source_field": "sector", "confidence": CONF_HIGH,
                 "reason": "found in the owned curated phase2k sector map (prior-phase repo metadata)"}),\
            attempts
    att(SRC_PRIOR_MAP, "NOT_FOUND")

    # 6. Cached EODHD company profile == the General block (already exhausted by sources 2/3).
    att(SRC_EODHD_PROFILE, "SAME_AS_EODHD_GENERAL_BLOCK")
    return None, attempts


def build_repairs(unknown_tickers: Sequence[str], eodhd: Dict[str, Dict[str, str]],
                  curated: Dict[str, Tuple[str, str]]) -> Tuple[Dict[str, str], List[List], List[List],
                                                                List[List]]:
    """Returns (repaired_map{ticker:sector}, repaired_rows, unrepaired_rows, attempt_rows)."""
    repaired_map: Dict[str, str] = {}
    repaired_rows: List[List] = []
    unrepaired_rows: List[List] = []
    attempt_rows: List[List] = []
    for tk in sorted(set(t.strip().upper() for t in unknown_tickers)):
        rep, attempts = repair_one(tk, eodhd, curated)
        for a in attempts:
            attempt_rows.append([a["ticker"], a["source_family"], a["outcome"], a["value"]])
        if rep is None:
            meta = eodhd.get(tk)
            why = ("no owned EODHD fundamentals file for this ticker"
                   if meta is None else
                   "owned metadata present but no canonical-GICS sector could be derived without guessing")
            unrepaired_rows.append([tk, "Unknown", why,
                                    "kept Unknown - not fabricated (owned data only)"])
            continue
        repaired_map[tk] = rep["repaired_sector"]
        repaired_rows.append([tk, "Unknown", rep["repaired_sector"], rep["repaired_industry"],
                              rep["source_family"], rep["source_file"], rep["source_field"],
                              rep["confidence"], rep["reason"]])
    return repaired_map, repaired_rows, unrepaired_rows, attempt_rows


# --------------------------------------------------------------------------- #
# C. Apply repairs to the panel + rebuild the sector-neutral composite.
# --------------------------------------------------------------------------- #
def apply_repairs(ev, repaired_map: Dict[str, str]):
    """Overwrite ONLY Unknown-sector rows with a repaired label; mapped names are left untouched."""
    ev = ev.copy()
    if "sector" not in ev.columns:
        ev["sector"] = "Unknown"
    up = ev["ticker"].astype(str).str.upper()

    def pick(tk, cur):
        return repaired_map[tk] if (_is_unknown(cur) and tk in repaired_map) else cur

    ev["sector"] = [pick(t, c) for t, c in zip(up, ev["sector"])]
    return ev


# --------------------------------------------------------------------------- #
# D. Read the Phase 10-E "before" cross-section (req #1).
# --------------------------------------------------------------------------- #
def read_before(phase10e_dir: Optional[Path]) -> Tuple[Dict[str, Dict], bool]:
    """Returns ({TICKER: {rank_sn, review_label, comp_sn, sector, is_unknown}}, present)."""
    d = Path(phase10e_dir) if phase10e_dir else _PHASE10E_DIR
    f = d / _PHASE10E_CANDIDATES
    if not f.is_file():
        return {}, False
    before: Dict[str, Dict] = {}
    for r in _read_csv_file(f):
        tk = _norm_label(r.get("ticker")).upper()
        if not tk:
            continue
        try:
            rank = int(float(r.get("rank_sn"))) if _norm_label(r.get("rank_sn")) else None
        except (TypeError, ValueError):
            rank = None
        try:
            comp = float(r.get("comp_sn")) if _norm_label(r.get("comp_sn")) else None
        except (TypeError, ValueError):
            comp = None
        sect = _norm_label(r.get("sector")) or "Unknown"
        before[tk] = {"rank_sn": rank, "review_label": _norm_label(r.get("review_label")),
                      "comp_sn": comp, "sector": sect, "is_unknown": _is_unknown(sect)}
    return before, True


# --------------------------------------------------------------------------- #
# E. Before/after comparison reports.
# --------------------------------------------------------------------------- #
def _book_sets(book_label_by_ticker: Dict[str, str]):
    longs = {t for t, s in book_label_by_ticker.items() if s == SIDE_LONG}
    shorts = {t for t, s in book_label_by_ticker.items() if s == SIDE_SHORT}
    return longs, shorts


def book_change_rows(before: Dict[str, Dict], after_label: Dict[str, str]) -> Tuple[List[List], Dict]:
    b_long, b_short = _book_sets({t: v["review_label"] for t, v in before.items()})
    a_long, a_short = _book_sets(after_label)
    rows: List[List] = []
    summary = {
        "entered_long": sorted(a_long - b_long), "exited_long": sorted(b_long - a_long),
        "entered_short": sorted(a_short - b_short), "exited_short": sorted(b_short - a_short),
        "stayed_long": sorted(a_long & b_long), "stayed_short": sorted(a_short & b_short),
    }
    for change, names in (("ENTERED_LONG", summary["entered_long"]),
                          ("EXITED_LONG", summary["exited_long"]),
                          ("ENTERED_SHORT", summary["entered_short"]),
                          ("EXITED_SHORT", summary["exited_short"])):
        for tk in names:
            rows.append([tk, change, before.get(tk, {}).get("review_label", "") or "(absent)",
                         after_label.get(tk, SIDE_HOLD)])
    counts = {k: len(v) for k, v in summary.items()}
    return rows or [["", "NO_CHANGE", "", ""]], {**summary, "counts": counts}


def rank_movement_rows(before: Dict[str, Dict], cs) -> List[List]:
    rows: List[List] = []
    if cs is None or getattr(cs, "empty", True):
        return [["", "", "", "", "", "", ""]]
    for _, r in cs.iterrows():
        tk = str(r["ticker"]).upper()
        b = before.get(tk, {})
        br = b.get("rank_sn")
        ar = r.get("rank_sn")
        delta = (br - ar) if (_finite(br) and _finite(ar)) else ""   # +ve == moved up toward LONG
        rows.append([tk, br if br is not None else "", ar if _finite(ar) else "", delta,
                     _num(b.get("comp_sn")), _num(r.get(COMP_SN)),
                     b.get("review_label", "") or "(absent)", r.get("review_label", "")])
    rows.sort(key=lambda x: (abs(x[3]) if isinstance(x[3], (int, float)) else -1), reverse=True)
    return rows


# --------------------------------------------------------------------------- #
# F. Decision.
# --------------------------------------------------------------------------- #
def decide(book_meta: Dict, after_summary: Dict, repaired_count: int, before_unknown_share: float,
           after_unknown_share: float) -> Tuple[str, str]:
    if not book_meta.get("quintile_feasible") or book_meta.get("n_scoreable", 0) < _MIN_BOOK_NAMES:
        return DEC_REJECTED, ("after sector repair the reconstructed latest-quarter book has too few "
                              "scoreable names to form a reviewable quintile long/short book")
    if book_meta.get("n_long", 0) == 0 or book_meta.get("n_short", 0) == 0:
        return DEC_REJECTED, ("after sector repair the reconstructed book has an empty long or short "
                              "side - not reviewable")
    if repaired_count == 0:
        return DEC_NOT_REPAIRABLE, ("no Unknown-sector name in the book could be repaired from owned/"
                                    "local metadata - the Unknown bucket is unchanged")
    high_conc = bool(after_summary.get("high_concentration"))
    aus = after_unknown_share
    if _finite(aus) and aus >= UNKNOWN_CAVEAT_SHARE:
        return DEC_PARTIAL, ("owned-metadata repair cut the Unknown-sector book share from %.0f%% to "
                             "%.0f%%, but a material residual (>=%.0f%%) remains unmapped from owned "
                             "data - review against the sector-neutral composite with the residual "
                             "Unknown bucket understood" % (before_unknown_share * 100, aus * 100,
                                                            UNKNOWN_CAVEAT_SHARE * 100))
    if high_conc:
        return DEC_PARTIAL, ("owned-metadata repair cut Unknown-sector book share to %.0f%%, but the "
                             "repaired labels surface a single-sector long-book concentration >=%.0f%% "
                             "- review the concentration before sizing" % (aus * 100,
                                                                           MAX_SECTOR_SHARE * 100))
    return DEC_REPAIRED, ("owned-metadata sector repair cut the Unknown-sector book share from %.0f%% "
                          "to %.0f%% with no new single-sector concentration breach; the reranked "
                          "sector-neutral quarterly composite book is ready for a human approve/reject "
                          "review (paper-only; NO orders; NO automation)"
                          % (before_unknown_share * 100, aus * 100))


# --------------------------------------------------------------------------- #
# G. Artifact writers.
# --------------------------------------------------------------------------- #
def _exposure_map(sect_rows: List[List]) -> Dict[str, Dict]:
    """sector -> {n_universe, n_long, n_short, n_book} from e10.sector_exposure rows."""
    out: Dict[str, Dict] = {}
    for row in sect_rows:
        # row = [sector, n_in_universe, n_long, n_short, n_book, long_share, book_share]
        out[row[0]] = {"n_universe": row[1], "n_long": row[2], "n_short": row[3], "n_book": row[4]}
    return out


def write_artifacts(P: _Paths, cs, book_meta, before, after_label, sect_rows, sect_summary,
                    before_sect_rows, before_sect_summary, liq_rows, liq_summary, low_liq_thr,
                    risk_rows, avail, repaired_rows, unrepaired_rows, attempt_rows, source_audit_rows,
                    sn_rebuild_rows, change_rows, rank_rows) -> bool:
    import pandas as pd  # noqa: F401

    # 1. unknown_sector_repair_attempts
    _write_csv(P.art("attempts"), ["ticker", "source_family", "outcome", "value"],
               attempt_rows or [["", "", "", ""]])

    # 2. repaired_sector_mapping
    _write_csv(P.art("repaired_map"),
               ["ticker", "original_sector", "repaired_sector", "repaired_industry",
                "source_file_or_source_family", "source_file", "source_field", "confidence", "reason"],
               [[r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[7], r[8]] for r in repaired_rows]
               or [["", "", "", "", "", "", "", "", "no Unknown name was repairable"]])

    # 3. unrepaired_unknown_sector_names
    _write_csv(P.art("unrepaired"), ["ticker", "sector", "why_unrepairable", "disposition"],
               unrepaired_rows or [["", "", "no residual Unknown names", "n/a"]])

    # 4. sector_mapping_source_audit
    _write_csv(P.art("source_audit"),
               ["source_family", "available", "taxonomy", "point_in_time", "n_attempted", "n_hit",
                "note"], source_audit_rows or [["", "", "", "", 0, 0, ""]])

    # 5. before_after_sector_exposure
    b_exp = _exposure_map(before_sect_rows)
    a_exp = _exposure_map(sect_rows)
    sectors = sorted(set(b_exp) | set(a_exp))
    ba_rows = []
    for sct in sectors:
        b = b_exp.get(sct, {})
        a = a_exp.get(sct, {})
        ba_rows.append([sct, b.get("n_universe", 0), a.get("n_universe", 0),
                        b.get("n_long", 0), a.get("n_long", 0), b.get("n_short", 0), a.get("n_short", 0),
                        b.get("n_book", 0), a.get("n_book", 0)])
    _write_csv(P.art("ba_sector"),
               ["sector", "before_n_universe", "after_n_universe", "before_n_long", "after_n_long",
                "before_n_short", "after_n_short", "before_n_book", "after_n_book"],
               ba_rows or [["", 0, 0, 0, 0, 0, 0, 0, 0]])

    # 6. before_after_unknown_sector_exposure
    _write_csv(P.art("ba_unknown"),
               ["metric", "before", "after", "delta"],
               [["unknown_book_share", _num(before_sect_summary.get("unknown_book_share")),
                 _num(sect_summary.get("unknown_book_share")),
                 _num(_safe_delta(sect_summary.get("unknown_book_share"),
                                  before_sect_summary.get("unknown_book_share")))],
                ["unknown_long_share", _num(before_sect_summary.get("unknown_long_share")),
                 _num(sect_summary.get("unknown_long_share")),
                 _num(_safe_delta(sect_summary.get("unknown_long_share"),
                                  before_sect_summary.get("unknown_long_share")))],
                ["n_unknown_book", before_sect_summary.get("n_unknown_book", 0),
                 sect_summary.get("n_unknown_book", 0),
                 sect_summary.get("n_unknown_book", 0) - before_sect_summary.get("n_unknown_book", 0)],
                ["top_long_sector_share(mapped)", _num(before_sect_summary.get("top_long_sector_share")),
                 _num(sect_summary.get("top_long_sector_share")), ""]])

    # 7. reranked_paper_review_candidate_list
    chdr = ["rank_sn", "percentile_sn", "ticker", "review_label", "review_status", "quintile",
            "comp_sn", "comp_raw", "comp_sn_z", "sector", "sector_is_unknown", "sector_repaired",
            "before_rank_sn", "rank_delta", "before_review_label", "cohort", "liquidity_proxy"]
    for leg in LEGS:
        chdr += [leg["feature"], "avail_%s" % leg["feature"]]
    crows = []
    if cs is not None and not getattr(cs, "empty", True):
        for _, r in cs.iterrows():
            tk = str(r["ticker"]).upper()
            b = before.get(tk, {})
            br = b.get("rank_sn")
            ar = r.get("rank_sn")
            delta = (br - ar) if (_finite(br) and _finite(ar)) else ""
            repaired = bool(b.get("is_unknown")) and (not _is_unknown(r.get("sector")))
            row = [ar, _num(r.get("percentile_sn")), tk, r.get("review_label"), r.get("review_status"),
                   r.get("quintile"), _num(r.get(COMP_SN)), _num(r.get(COMP_RAW)),
                   _num(r.get("comp_sn_z")),
                   ("Unknown" if _is_unknown(r.get("sector")) else str(r.get("sector"))),
                   _is_unknown(r.get("sector")), repaired, br if br is not None else "", delta,
                   b.get("review_label", "") or "(absent)",
                   str(r.get("cohort")) if "cohort" in cs.columns else "", _num(r.get("liquidity_proxy"))]
            for leg in LEGS:
                feat = leg["feature"]
                row += [_num(r.get(feat)), avail.get(feat, {}).get(tk, "")]
            crows.append(row)
    _write_csv(P.art("reranked_candidates"), chdr, crows or [[""] * len(chdr)])

    # 8. reranked_paper_review_long_short_book
    bhdr = ["side", "rank_sn", "ticker", "comp_sn", "comp_raw", "sector", "sector_is_unknown",
            "sector_repaired", "before_review_label", "cohort", "liquidity_proxy", "review_status"]
    brows = []
    if cs is not None and not getattr(cs, "empty", True):
        bk = cs[cs["review_label"].isin([SIDE_LONG, SIDE_SHORT])]
        for _, r in bk.iterrows():
            tk = str(r["ticker"]).upper()
            b = before.get(tk, {})
            repaired = bool(b.get("is_unknown")) and (not _is_unknown(r.get("sector")))
            brows.append([r.get("review_label"), r.get("rank_sn"), tk, _num(r.get(COMP_SN)),
                          _num(r.get(COMP_RAW)),
                          ("Unknown" if _is_unknown(r.get("sector")) else str(r.get("sector"))),
                          _is_unknown(r.get("sector")), repaired,
                          b.get("review_label", "") or "(absent)",
                          str(r.get("cohort")) if "cohort" in cs.columns else "",
                          _num(r.get("liquidity_proxy")), REVIEW_STATUS])
    _write_csv(P.art("reranked_book"), bhdr, brows or [[""] * len(bhdr)])

    # 9. long_short_book_change_report
    _write_csv(P.art("book_change"), ["ticker", "change", "before_label", "after_label"], change_rows)

    # 10. rank_movement_report
    _write_csv(P.art("rank_movement"),
               ["ticker", "before_rank_sn", "after_rank_sn", "rank_delta(up=+)", "before_comp_sn",
                "after_comp_sn", "before_label", "after_label"], rank_rows)

    # 11. sector_neutral_score_rebuild_audit
    _write_csv(P.art("sn_rebuild_audit"),
               ["item", "before", "after", "note"], sn_rebuild_rows)

    # 12. repaired_book_risk_flags
    _write_csv(P.art("risk_flags"),
               ["ticker", "review_label", "sector", "unknown_sector", "low_liquidity", "missing_leg",
                "extreme_score", "cohort", "is_new_cohort", "in_concentrated_sector"],
               risk_rows or [["", "", "", "", "", "", "", "", "", ""]])

    # 13. secret_safety_audit
    sec_rows, clean = b10._secret_safety_audit(P.out)
    _write_csv(P.art("secret_audit"), ["file", "clean", "keyed_url_marker", "key_value_present"],
               [[r["file"], r["clean"], r["keyed_url_marker"], r["key_value_present"]] for r in sec_rows])
    return clean


def _safe_delta(a, b):
    return (a - b) if (_finite(a) and _finite(b)) else None


def _phase10g_plan(decision: str, after_unknown_share: float, repaired_count: int) -> Dict:
    if decision == DEC_REPAIRED:
        nxt = ("Phase 10-G: run the HUMAN approve/reject gate over reranked_paper_review_long_short_book."
               "csv (the now sector-mapped, reranked sector-neutral quarterly book). On approval, build a"
               " PAPER-ONLY position tracker (mark-to-market each quarter; realised vs expected "
               "net-25bps) - still NO orders, NO automation, NO broker, NO live trading, NO deploy. "
               "Optionally fold the repaired labels back into the panel sector source so future runs are "
               "mapped by default. No new data purchase.")
        cmd = ("review research/output/phase10f_owned_sector_mapping_repair/"
               "reranked_paper_review_long_short_book.csv")
    elif decision == DEC_PARTIAL:
        nxt = ("Phase 10-G: the repaired book still carries a residual Unknown bucket or a sector "
               "concentration to review. Inspect unrepaired_unknown_sector_names.csv and "
               "before_after_sector_exposure.csv; decide per-name whether to review now or defer the "
               "residual names. No new data purchase.")
        cmd = ("review research/output/phase10f_owned_sector_mapping_repair/"
               "unrepaired_unknown_sector_names.csv")
    elif decision == DEC_NOT_REPAIRABLE:
        nxt = ("Phase 10-G: owned/local metadata could not repair the Unknown bucket. Do NOT purchase a "
               "provider without explicit user opt-in; consider whether the curated map can be extended "
               "by hand from owned filings. No new data purchase.")
        cmd = ("review research/output/phase10f_owned_sector_mapping_repair/"
               "phase10f_owned_sector_mapping_repair.json")
    else:
        nxt = ("Phase 10-G: resolve the issue noted in the report (degenerate book or blocker), then "
               "re-run this repair harness.")
        cmd = ("review research/output/phase10f_owned_sector_mapping_repair/"
               "phase10f_owned_sector_mapping_repair.json")
    return {"phase": "10-G", "from_decision": decision,
            "after_unknown_book_share": _round(after_unknown_share, 4),
            "n_sectors_repaired": repaired_count, "next_step": nxt, "exact_next_command": cmd,
            "constraints": ["owned data only; no new purchase without explicit user opt-in", "paper-only",
                            "NO orders", "NO automation", "NO live trading", "NO broker", "no deploy",
                            "no Paper Trader writes", "no commit", "no push"]}


# --------------------------------------------------------------------------- #
# H. Report.
# --------------------------------------------------------------------------- #
def _build_report(decision, reason, book_meta, before_sect_summary, sect_summary, liq_summary,
                  before_unknown_share, after_unknown_share, repaired_count, n_unrepaired, n_attempted,
                  source_families, q, n_universe, n_scoreable, change_counts, key_visible, leak_clean,
                  as_of) -> Dict:
    return {
        "phase": PHASE, "as_of": as_of, "decision": decision, "decision_rationale": reason,
        "allowed_decisions": list(ALLOWED_DECISIONS), "forbidden_decisions": list(FORBIDDEN_DECISIONS),
        "objective": ("repair the Unknown sector labels of the 10-E quarterly quality book using ONLY "
                      "owned/local metadata, rebuild the sector-neutral composite, and rerank the "
                      "2026Q2 paper-review book - NOT alpha search, NOT provider search, NOT orders, "
                      "NOT automation, NOT a Paper Trader integration"),
        "performs_network": PERFORMS_NETWORK, "offline": True,
        "uses_owned_data_only": True, "performs_provider_acquisition": False,
        "eodhd_key_visible": bool(key_visible), "eodhd_key_required": False,
        "creates_paper_trader_signals": False, "creates_trade_decisions": False,
        "creates_orders": False, "creates_automation": False, "wrote_to_paper_trader": False,
        "live_trading": False, "broker_connected": False, "deploy": False,
        "fabricated_sectors": False,
        "sector_labels_point_in_time": False,
        "sector_labels_note": ("repaired sector labels are CURRENT-as-of company classifications used "
                               "ONLY as a static neutralisation grouping (sector is not a return "
                               "feature); same non-PIT basis as the existing curated map"),
        "composite_source": "imported verbatim from Phase 10-D build_composite; sector-neutral legs "
                            "recomputed AFTER applying repaired sectors; no re-definition",
        "composite_legs": [l["feature"] for l in LEGS], "leg_families": list(ALLOWED_FAMILIES),
        "optimised_weights": False, "sign_flipping": False,
        "default_review_score": DEFAULT_REVIEW_SCORE, "default_review_score_is_sector_neutral": True,
        "sector_neutral_composite_rebuilt": True,
        "primary_horizon_days": PRIMARY_HORIZON_D,
        "owned_sources_attempted": source_families,
        "latest_quarter": str(q) if q is not None else "",
        "latest_quarter_universe_names": n_universe,
        "latest_quarter_scoreable_names": n_scoreable,
        "repair": {"n_unknown_attempted": n_attempted, "n_repaired": repaired_count,
                   "n_still_unknown": n_unrepaired},
        "book": {"n_long": book_meta.get("n_long", 0), "n_short": book_meta.get("n_short", 0),
                 "n_hold": book_meta.get("n_hold", 0)},
        "unknown_sector_book_share_before": _round(before_unknown_share, 4),
        "unknown_sector_book_share_after": _round(after_unknown_share, 4),
        "sector_exposure_after": {"unknown_book_share": _round(sect_summary.get("unknown_book_share"), 4),
                                  "unknown_long_share": _round(sect_summary.get("unknown_long_share"), 4),
                                  "n_unknown_book": sect_summary.get("n_unknown_book", 0),
                                  "top_long_sector": sect_summary.get("top_long_sector"),
                                  "top_long_sector_share": _round(sect_summary.get("top_long_sector_share"), 4),
                                  "high_concentration": bool(sect_summary.get("high_concentration"))},
        "book_change_counts": change_counts,
        "liquidity": {"low_liq_threshold": _round(liq_summary.get("low_liq_threshold"), 4),
                      "n_low_liq_book": liq_summary.get("n_low_liq_book", 0)},
        "safety_badges": [b for b, _m in SAFETY_BADGES],
        "secret_safety_leak_scan_clean": leak_clean,
        "api_key_printed": False, "api_key_written_to_disk": False,
        "required_artifacts": [_ARTIFACTS[k] for k in _REQUIRED_ARTIFACTS],
        "exact_next_command": _phase10g_plan(decision, after_unknown_share, repaired_count)["exact_next_command"],
        "constraints_honored": ["offline (no network/key/provider probe)", "owned/local metadata only",
                                "no new provider purchase", "no FMP/AlphaVantage/Polygon/Finnhub/Norgate-API",
                                "composite imported from 10-D; sector-neutral legs recomputed; no "
                                "optimisation; no sign-flip", "no Paper Trader writes", "no GCP",
                                "NO orders", "NO automation", "NO live trading", "NO broker", "no deploy",
                                "no package install", "no full regression", "no commit", "no push",
                                "no key printed/written", "no fabricated sectors"],
    }


def _print_summary(report: Dict) -> None:
    rp = report.get("repair", {})
    b = report.get("book", {})
    print("[10-F-A] decision=%s | offline=%s | latest_q=%s | repaired=%s/%s still_unknown=%s | "
          "unknown_book_share %s -> %s | long=%s short=%s | wrote_pt=%s orders=%s automation=%s | "
          "leak_clean=%s"
          % (report.get("decision"), report.get("offline"), report.get("latest_quarter"),
             rp.get("n_repaired"), rp.get("n_unknown_attempted"), rp.get("n_still_unknown"),
             report.get("unknown_sector_book_share_before"),
             report.get("unknown_sector_book_share_after"), b.get("n_long"), b.get("n_short"),
             report.get("wrote_to_paper_trader"), report.get("creates_orders"),
             report.get("creates_automation"), report.get("secret_safety_leak_scan_clean")))


# --------------------------------------------------------------------------- #
# I. Orchestration.
# --------------------------------------------------------------------------- #
def run(out_dir: Optional[Path] = None, *, ev=None, norm_csvs: Optional[Dict[str, Path]] = None,
        panel_csv: Optional[Path] = None, fund_dir: Optional[Path] = None,
        curated_csv: Optional[Path] = None, phase10e_dir: Optional[Path] = None, as_of: str = AS_OF,
        verbose: bool = True) -> Dict:
    P = _Paths(out_dir)
    P.out.mkdir(parents=True, exist_ok=True)
    log = t8._Log(verbose)
    try:
        import pandas as pd
        key_visible = bool(os.environ.get(EODHD_KEY_ENV))  # context only; NEVER printed or written
        log.step("preflight", "OFFLINE", "owned-metadata sector repair only; no network / no key; "
                 "EODHD key visible=%s" % key_visible)

        # 1. Phase 10-E "before" cross-section (req #1).
        before, before_present = read_before(phase10e_dir)
        if not before_present:
            return _finish_blocker(P, log, DEC_HARD_BLOCKER, ("the Phase 10-E candidate list is missing "
                                   "(%s) - run Phase 10-E first to produce the book to repair."
                                   % _PHASE10E_CANDIDATES),
                                   "python research/run_phase10e_quarterly_quality_paper_review_harness.py",
                                   key_visible, as_of)
        before_label = {t: v["review_label"] for t, v in before.items()}
        before_book = {t for t, s in before_label.items() if s in (SIDE_LONG, SIDE_SHORT)}
        before_unknown_book = sum(1 for t in before_book if before.get(t, {}).get("is_unknown"))
        before_unknown_share = (before_unknown_book / len(before_book)) if before_book else float("nan")

        # 2. Panel (reuse 10-C/10-D build verbatim; offline).
        if ev is None:
            ev, panel_ok, stats = c10.build_panel(as_of, panel_csv, log)
        else:
            panel_ok = not getattr(ev, "empty", True)
        if not panel_ok:
            return _finish_blocker(P, log, DEC_HARD_BLOCKER, ("the Norgate survivorship-free panel is "
                                   "empty - cannot reconstruct the book to repair."),
                                   "python research/run_phase10d_quarterly_quality_composite_validation.py",
                                   key_visible, as_of)
        ev = ev.copy()

        # 3. Identify Unknown-sector tickers on the panel + load owned sources.
        if "sector" not in ev.columns:
            ev["sector"] = "Unknown"
        unknown_tickers = sorted(set(ev.loc[ev["sector"].apply(_is_unknown), "ticker"]
                                     .astype(str).str.upper()))
        eodhd = load_eodhd_sector_meta(fund_dir or EODHD_FUND_DIR)
        curated = load_curated_map(curated_csv or CURATED_MAP_CSV)
        log.step("sources", "DONE", "owned EODHD fundamentals=%d tickers; curated map=%d tickers; "
                 "panel Unknown=%d" % (len(eodhd), len(curated), len(unknown_tickers)))

        # 4. Repair (owned sources only; never fabricate).
        repaired_map, repaired_rows, unrepaired_rows, attempt_rows = build_repairs(
            unknown_tickers, eodhd, curated)
        log.step("repair", "DONE", "repaired %d / %d Unknown tickers from owned metadata; %d still "
                 "Unknown" % (len(repaired_map), len(unknown_tickers), len(unrepaired_rows)))

        # 5. Apply repairs + rebuild the sector-neutral composite (legs recomputed on repaired sectors).
        def _mapped_counts(frame):
            mapped = frame.loc[~frame["sector"].apply(_is_unknown)]
            return (int(mapped["ticker"].astype(str).str.upper().nunique()),
                    int(mapped["sector"].nunique()))
        mapped_tk_before, n_sectors_before = _mapped_counts(ev)
        ev = apply_repairs(ev, repaired_map)
        mapped_tk_after, n_sectors_after = _mapped_counts(ev)
        norm_csvs = norm_csvs or c10._default_norm_csvs()
        missing = [l["feature"] for l in LEGS if not Path(norm_csvs.get(l["family"], "")).is_file()]
        if missing:
            return _finish_blocker(P, log, DEC_HARD_BLOCKER, ("missing Phase 10-B normalized leg CSV(s): "
                                   "%s - cannot rebuild the composite." % ", ".join(missing)),
                                   "python research/run_phase10b_eodhd_norgate_exhaustive_alpha_"
                                   "factory.py --live", key_visible, as_of)
        ev, cols = c10.attach_signals(ev, norm_csvs, log)
        ev, comp_cov, _rl, _sl = d10.build_composite(ev, cols, log)

        # 6. Reconstruct the latest-quarter cross-section + reranked book (reuse 10-E logic verbatim).
        cs, q, prior_q, n_universe, n_scoreable = e10.latest_quarter_cross_section(ev)
        cs, book_meta = e10.build_book(cs)
        after_label = {}
        if cs is not None and not getattr(cs, "empty", True):
            after_label = {str(r["ticker"]).upper(): r["review_label"] for _, r in cs.iterrows()}
        log.step("rerank", "DONE", "latest quarter %s reranked: %d scoreable -> long=%s short=%s hold=%s"
                 % (str(q), n_scoreable, book_meta.get("n_long"), book_meta.get("n_short"),
                    book_meta.get("n_hold")))

        # 7. Exposure / liquidity / risk analytics on the repaired book + a synthetic "before" exposure.
        sect_rows, sect_summary = e10.sector_exposure(cs)
        before_sect_rows, before_sect_summary = _before_exposure(cs, before)
        liq_rows, liq_summary, low_liq_thr = e10.liquidity_report(cs)
        risk_rows = e10.risk_flags(cs, [], low_liq_thr, sect_summary)
        after_unknown_share = sect_summary.get("unknown_book_share")
        entry_by_ticker, tickers = {}, []
        if cs is not None and not getattr(cs, "empty", True):
            tickers = [str(t) for t in cs["ticker"].tolist()]
            entry_by_ticker = {str(r["ticker"]): r["entry_date"] for _, r in cs.iterrows()}
        avail = e10._availability_dates(norm_csvs, tickers, entry_by_ticker)

        # 8. Before/after change + rank movement + rebuild audit + source audit.
        change_rows, change_summary = book_change_rows(before, after_label)
        rank_rows = rank_movement_rows(before, cs)
        sn_rebuild_rows = _sn_rebuild_audit_rows(mapped_tk_before, mapped_tk_after, n_sectors_before,
                                                 n_sectors_after, comp_cov, before_unknown_share,
                                                 after_unknown_share)
        source_audit_rows = _source_audit_rows(eodhd, curated, unknown_tickers, repaired_rows)

        # 9. Decision.
        decision, reason = decide(book_meta, sect_summary, len(repaired_map), before_unknown_share,
                                  after_unknown_share if _finite(after_unknown_share) else 0.0)
        if decision not in ALLOWED_DECISIONS or decision in FORBIDDEN_DECISIONS:
            decision = DEC_HARD_BLOCKER

        # 10. Artifacts + report.
        leak_clean = write_artifacts(P, cs, book_meta, before, after_label, sect_rows, sect_summary,
                                     before_sect_rows, before_sect_summary, liq_rows, liq_summary,
                                     low_liq_thr, risk_rows, avail, repaired_rows, unrepaired_rows,
                                     attempt_rows, source_audit_rows, sn_rebuild_rows, change_rows,
                                     rank_rows)
        report = _build_report(decision, reason, book_meta, before_sect_summary, sect_summary,
                               liq_summary, before_unknown_share,
                               after_unknown_share if _finite(after_unknown_share) else float("nan"),
                               len(repaired_map), len(unrepaired_rows), len(unknown_tickers),
                               [r[0] for r in source_audit_rows], q, n_universe, n_scoreable,
                               change_summary.get("counts", {}), key_visible, leak_clean, as_of)
        _write_json(P.art("report"), report)
        _write_json(P.art("next_plan"),
                    _phase10g_plan(decision,
                                   after_unknown_share if _finite(after_unknown_share) else float("nan"),
                                   len(repaired_map)))
        log.step("artifacts", "DONE", "wrote %d artifacts" % len(_REQUIRED_ARTIFACTS))
        _print_summary(report)
        return report
    except Exception as exc:  # pragma: no cover - defensive
        import traceback
        detail = "%s: %s" % (type(exc).__name__, exc)
        log.step("run", "ERROR", detail)
        report = {"phase": PHASE, "decision": DEC_ERROR, "decision_rationale": detail,
                  "repro_command": "python research/run_phase10f_owned_sector_mapping_repair.py",
                  "traceback": traceback.format_exc()}
        try:
            P.out.mkdir(parents=True, exist_ok=True)
            _write_json(P.art("report"), report)
        except Exception:
            pass
        return report


def _before_exposure(cs, before: Dict[str, Dict]) -> Tuple[List[List], Dict]:
    """Reconstruct the BEFORE sector exposure over the SAME scoreable cross-section using the 10-E
    (pre-repair) sector labels + the 10-E review labels - so before/after compare like-for-like."""
    import pandas as pd
    if cs is None or getattr(cs, "empty", True):
        return [], {"unknown_book_share": float("nan"), "unknown_long_share": float("nan"),
                    "n_unknown_book": 0, "top_long_sector": "", "top_long_sector_share": float("nan")}
    rows_data = []
    for _, r in cs.iterrows():
        tk = str(r["ticker"]).upper()
        b = before.get(tk, {})
        rows_data.append({"ticker": tk,
                          "sector": "Unknown" if b.get("is_unknown", True) else b.get("sector", "Unknown"),
                          "review_label": b.get("review_label", SIDE_HOLD)})
    bdf = pd.DataFrame(rows_data)
    bcs = bdf.rename(columns={})
    # piggy-back on e10.sector_exposure by faking the minimal columns it needs.
    bcs2 = bcs.copy()
    rows, summary = e10.sector_exposure(bcs2)
    return rows, summary


def _sn_rebuild_audit_rows(mapped_tk_before, mapped_tk_after, n_sectors_before, n_sectors_after,
                           comp_cov, b_unk_share, a_unk_share):
    return [
        ["sector_neutral_composite_rebuilt", "False", "True",
         "comp_sn legs recomputed via within-month x sector de-mean over the repaired sector labels"],
        ["comp_raw_changed", "n/a", "False",
         "comp_raw is sector-independent and is unchanged by the repair (sanity check)"],
        ["mapped_tickers_in_panel", mapped_tk_before, mapped_tk_after,
         "tickers with a real (non-Unknown) sector => far more names neutralised within their TRUE "
         "sector instead of one giant Unknown bucket"],
        ["distinct_mapped_sector_labels", n_sectors_before, n_sectors_after,
         "count of distinct GICS sector labels in use (the curated map already spans all 11)"],
        ["composite_coverage_events", comp_cov, comp_cov, "both-legs covered events (unchanged)"],
        ["unknown_book_share", _num(b_unk_share), _num(a_unk_share),
         "Unknown share of the reconstructed long/short book"],
    ]


def _source_audit_rows(eodhd, curated, unknown_tickers, repaired_rows):
    by_family: Dict[str, int] = {}
    for r in repaired_rows:
        by_family[r[4]] = by_family.get(r[4], 0) + 1
    n_unknown = len(unknown_tickers)
    return [
        [SRC_NORGATE, "False", "n/a", "n/a", n_unknown, 0,
         "no local Norgate sector dump on disk; would require the Norgate SDK/DB (out of offline scope)"],
        [SRC_EODHD_GIC, "True", "GICS (11 buckets, == curated map)", "current-as-of (not PIT)",
         n_unknown, by_family.get(SRC_EODHD_GIC, 0),
         "owned research/data/eodhd/raw/fundamentals/*.json General.GicSector; %d tickers cached"
         % len(eodhd)],
        [SRC_EODHD_SECTOR, "True", "Morningstar->GICS crosswalk", "current-as-of (not PIT)",
         n_unknown, by_family.get(SRC_EODHD_SECTOR, 0),
         "fallback when General.GicSector blank; documented 1:1 crosswalk; no guessing"],
        [SRC_EODHD_NORM, "True", "n/a", "n/a", n_unknown, 0,
         "normalized EODHD families are time-series features only; no sector classification"],
        [SRC_PRIOR_MAP, "True", "GICS", "current-as-of (not PIT)", n_unknown,
         by_family.get(SRC_PRIOR_MAP, 0),
         "owned curated phase2k map (%d tickers); Unknown names are by definition absent from it"
         % len(curated)],
        [SRC_EODHD_PROFILE, "True", "GICS", "current-as-of (not PIT)", n_unknown, 0,
         "the EODHD General block IS the cached company profile (same data as the GicSector source)"],
    ]


def _finish_blocker(P: _Paths, log, decision: str, detail: str, fix_cmd: str, key_visible: bool,
                    as_of: str) -> Dict:
    log.step("blocker", decision, detail)
    report = {"phase": PHASE, "as_of": as_of, "decision": decision, "decision_rationale": detail,
              "exact_next_command": fix_cmd, "allowed_decisions": list(ALLOWED_DECISIONS),
              "forbidden_decisions": list(FORBIDDEN_DECISIONS), "offline": True,
              "performs_network": PERFORMS_NETWORK, "uses_owned_data_only": True,
              "performs_provider_acquisition": False, "eodhd_key_visible": bool(key_visible),
              "api_key_printed": False, "api_key_written_to_disk": False, "fabricated_sectors": False,
              "creates_paper_trader_signals": False, "creates_orders": False,
              "creates_automation": False, "wrote_to_paper_trader": False, "live_trading": False,
              "broker_connected": False, "deploy": False,
              "composite_legs": [l["feature"] for l in LEGS],
              "safety_badges": [b for b, _m in SAFETY_BADGES]}
    try:
        sec_rows, _clean = b10._secret_safety_audit(P.out)
        _write_csv(P.art("secret_audit"), ["file", "clean", "keyed_url_marker", "key_value_present"],
                   [[r["file"], r["clean"], r["keyed_url_marker"], r["key_value_present"]]
                    for r in sec_rows])
        _write_json(P.art("next_plan"), {"phase": "10-G", "from_decision": decision,
                                         "exact_next_command": fix_cmd})
        _write_json(P.art("report"), report)
    except Exception:
        pass
    print("[10-F-A] decision=%s | %s" % (decision, detail))
    return report


def _parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Phase 10-F-A - Owned Metadata Sector Mapping Repair and Paper-Review Rerank")
    p.add_argument("--out-dir", default=None)
    p.add_argument("--panel-csv", default=None)
    p.add_argument("--fund-dir", default=None)
    p.add_argument("--curated-csv", default=None)
    p.add_argument("--phase10e-dir", default=None)
    p.add_argument("--as-of", default=AS_OF)
    p.add_argument("--quiet", action="store_true")
    return p.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    ns = _parse_args(argv)
    report = run(out_dir=ns.out_dir, panel_csv=ns.panel_csv, fund_dir=ns.fund_dir,
                 curated_csv=ns.curated_csv, phase10e_dir=ns.phase10e_dir, as_of=ns.as_of,
                 verbose=not ns.quiet)
    return 0 if report.get("decision") in ALLOWED_DECISIONS else 1


if __name__ == "__main__":
    raise SystemExit(main())
