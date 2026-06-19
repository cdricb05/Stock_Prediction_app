"""Phase 2K-Q Populate Sector Map for Reconfirmed Leads (offline, small C: files only).

Phase 2K-P was a sector-relative feasibility phase. It read the expanded D: price-history CSV
READ-ONLY for the 128-ticker equity universe (SPY excluded as benchmark), found no local sector
map, generated a one-row-per-equity template, and chose SECTOR_MAP_TEMPLATE_CREATED -> routed
here. Phase 2K-Q is a POPULATION phase, not a retest and not model training: it fills the sector
/ industry map for those 128 tickers using a curated current-as-of seed, validates coverage,
and decides whether a narrow, model-free sector-relative retest is now feasible.

It:

  1. Reads the Phase 2K-P result JSON and confirms it routed here (phase == "2K-P",
     recommendation.recommendation == SECTOR_MAP_TEMPLATE_CREATED, recommended_next_phase.phase
     == "2K-Q" with the matching title, sector_map_template.generated == true and row_count
     == 128, and the recommendation's create_model_candidate_now / train_model_now /
     run_sector_relative_retest_now all false).
  2. Reads the Phase 2K-P sector-map template CSV to recover the equity-ticker universe (one row
     per ticker; the same required columns). The large D: price-history CSV is NOT read here.
  3. Populates one row per template ticker from a curated current-as-of sector / industry seed
     (offline; never fetched from the network). The map is explicitly NOT point-in-time:
     point_in_time is "false" for every row, source records the curated current-as-of basis,
     and notes flag it as current-as-of research-only metadata.
  4. Writes the populated map to research/input/phase2k_p_sector_map_current.csv (the input the
     sector-relative retest would consume), and validates it: full ticker / sector / industry
     coverage, no SPY, no duplicates, uppercase tickers, source / as_of_date populated,
     point_in_time false for all rows, at least 5 sectors, and the per-sector size distribution
     (small sectors are reported, never silently dropped).
  5. Applies conservative rules and emits exactly one recommendation
     (SECTOR_MAP_POPULATED_READY_FOR_CAVEATED_RETEST / SECTOR_MAP_POPULATED_WITH_WARNINGS /
     SECTOR_MAP_POPULATION_INCOMPLETE / NO_ACTION_POPULATION_BLOCKED), then routes to Phase 2K-R
     with the title and purpose chosen by the decision.

It runs no broad alpha screen, runs no sector-relative retest, trains / fits / scores / creates
no model, fetches nothing from the network (no third-party data fetch), reads nothing from and
writes nothing to the D: drive, and performs no infrastructure action. It writes only two small
files in the C: repo: the populated sector-map CSV and the results JSON. The machine-readable
safety flags are emitted in the results JSON; the full guardrail rationale lives in
docs/phase2k_q_populate_sector_map_v1.md, not in this source.
"""
from __future__ import annotations

import csv
import datetime as dt
import json
import os
import time
from typing import Any, Dict, List, Optional, Tuple

PHASE = "2K-Q"

_OUTPUT_DIR = os.path.join("research", "output")
_INPUT_DIR = os.path.join("research", "input")

# Read-only upstream summaries (small, Git-tracked, in the C: repo). The large D: CSV is NOT
# read here; the equity universe is recovered from the Phase 2K-P template instead.
PHASE2K_P_JSON = os.path.join(_OUTPUT_DIR, "phase2k_p_sector_relative_feasibility.json")
PHASE2K_P_TEMPLATE = os.path.join(_OUTPUT_DIR, "phase2k_p_sector_map_template.csv")
PHASE2K_O_JSON = os.path.join(_OUTPUT_DIR, "phase2k_o_reconfirmed_lead_decision_gate.json")
PHASE2K_N_JSON = os.path.join(_OUTPUT_DIR, "phase2k_n_narrow_model_free_retest.json")

# The two small outputs this analyzer writes (both stay in the C: repo, never on the D: drive).
SECTOR_MAP_OUTPUT = os.path.join(_INPUT_DIR, "phase2k_p_sector_map_current.csv")
RESULTS_JSON = os.path.join(_OUTPUT_DIR, "phase2k_q_populate_sector_map.json")

# The benchmark ticker, never written into the equity sector map.
BENCHMARK = "SPY"

# Required schema for the populated sector map (same columns as the Phase 2K-P template).
SECTOR_MAP_REQUIRED_COLUMNS = [
    "ticker", "sector", "industry", "source", "as_of_date", "point_in_time", "notes",
]

# Provenance of the curated map: current-as-of, research-only, explicitly NOT point-in-time.
MAP_SOURCE = "curated_static_mapping_current_as_of_2026_06_18"
MAP_AS_OF_DATE = "2026-06-18"
MAP_POINT_IN_TIME = "false"
MAP_NOTES = "current-as-of sector map; not point-in-time; for caveated research only"

# Phase 2K-P routing values that send the template here to be populated.
PHASE2K_P_RECOMMENDATION = "SECTOR_MAP_TEMPLATE_CREATED"
PHASE2K_Q_TITLE_FROM_PHASE2K_P = "Populate Sector Map for Reconfirmed Leads"
EXPECTED_TEMPLATE_ROW_COUNT = 128

# Conservative validation thresholds for a caveated sector-relative retest.
MIN_DISTINCT_SECTORS = 5
MIN_TICKERS_PER_SECTOR = 3

# Recommendation vocabulary (the ONLY values this phase may emit).
REC_READY = "SECTOR_MAP_POPULATED_READY_FOR_CAVEATED_RETEST"
REC_WARNINGS = "SECTOR_MAP_POPULATED_WITH_WARNINGS"
REC_INCOMPLETE = "SECTOR_MAP_POPULATION_INCOMPLETE"
REC_NOACTION = "NO_ACTION_POPULATION_BLOCKED"
_ALLOWED_RECOMMENDATIONS = [REC_READY, REC_WARNINGS, REC_INCOMPLETE, REC_NOACTION]

# Next-phase (always Phase 2K-R) titles + purposes, chosen by the recommendation.
NEXT_PHASE = "2K-R"
NEXT_PHASE_BY_RECOMMENDATION = {
    REC_READY: {
        "title": "Sector-Relative Retest for Reconfirmed Leads",
        "purpose": (
            "Run a narrow, model-free sector-relative retest of the 3 reconfirmed leads using "
            "the populated current-as-of sector map, with explicit survivorship / current-map "
            "caveats, still with no model training and no model-candidate design. Results "
            "remain survivorship-biased and not a production edge."),
    },
    REC_WARNINGS: {
        "title": "Sector-Relative Retest for Reconfirmed Leads",
        "purpose": (
            "Run a narrow, model-free sector-relative retest of the 3 reconfirmed leads using "
            "the populated current-as-of sector map, carrying the recorded caveats "
            "(current-as-of only and any small sectors) into the retest, still with no model "
            "training and no model-candidate design. Results remain survivorship-biased and not "
            "a production edge."),
    },
    REC_INCOMPLETE: {
        "title": "Repair Sector Map Before Sector-Relative Retest",
        "purpose": (
            "Fix missing or duplicate sector-map rows (missing tickers, sectors, or industries) "
            "before retesting, still with no model training and no model candidate. Results "
            "remain survivorship-biased and not a production edge."),
    },
    REC_NOACTION: {
        "title": "Repair Sector Map Population Inputs",
        "purpose": (
            "Fix missing Phase 2K-P routing or the missing sector-map template before "
            "proceeding, still with no model training and no model candidate."),
    },
}

# --------------------------------------------------------------------------- #
# Curated current-as-of sector / industry seed for the 128 equity tickers.
# This is a static, offline, research-only mapping (NOT point-in-time, never fetched from the
# network). Sector / industry follow the standard GICS-style taxonomy as of 2026-06-18.
# --------------------------------------------------------------------------- #
SECTOR_INDUSTRY_SEED: Dict[str, Tuple[str, str]] = {
    "AAPL": ("Information Technology", "Technology Hardware, Storage & Peripherals"),
    "ABBV": ("Health Care", "Pharmaceuticals"),
    "ABT": ("Health Care", "Health Care Equipment"),
    "ACN": ("Information Technology", "IT Consulting & Other Services"),
    "ADBE": ("Information Technology", "Application Software"),
    "ADI": ("Information Technology", "Semiconductors"),
    "ADP": ("Industrials", "Human Resource & Employment Services"),
    "AMAT": ("Information Technology", "Semiconductor Materials & Equipment"),
    "AMD": ("Information Technology", "Semiconductors"),
    "AMGN": ("Health Care", "Biotechnology"),
    "AMZN": ("Consumer Discretionary", "Broadline Retail"),
    "AON": ("Financials", "Insurance Brokers"),
    "APD": ("Materials", "Industrial Gases"),
    "APH": ("Information Technology", "Electronic Components"),
    "AVGO": ("Information Technology", "Semiconductors"),
    "AXP": ("Financials", "Consumer Finance"),
    "BA": ("Industrials", "Aerospace & Defense"),
    "BAC": ("Financials", "Diversified Banks"),
    "BKNG": ("Consumer Discretionary", "Hotels, Resorts & Cruise Lines"),
    "BLK": ("Financials", "Asset Management & Custody Banks"),
    "BMY": ("Health Care", "Pharmaceuticals"),
    "BSX": ("Health Care", "Health Care Equipment"),
    "C": ("Financials", "Diversified Banks"),
    "CAT": ("Industrials", "Construction Machinery & Heavy Transportation Equipment"),
    "CB": ("Financials", "Property & Casualty Insurance"),
    "CDNS": ("Information Technology", "Application Software"),
    "CI": ("Health Care", "Health Care Services"),
    "CL": ("Consumer Staples", "Household Products"),
    "CME": ("Financials", "Financial Exchanges & Data"),
    "CMG": ("Consumer Discretionary", "Restaurants"),
    "COP": ("Energy", "Oil & Gas Exploration & Production"),
    "COST": ("Consumer Staples", "Consumer Staples Merchandise Retail"),
    "CRM": ("Information Technology", "Application Software"),
    "CSCO": ("Information Technology", "Communications Equipment"),
    "CVS": ("Health Care", "Health Care Services"),
    "CVX": ("Energy", "Integrated Oil & Gas"),
    "DE": ("Industrials", "Agricultural & Farm Machinery"),
    "DIS": ("Communication Services", "Movies & Entertainment"),
    "DUK": ("Utilities", "Electric Utilities"),
    "ELV": ("Health Care", "Managed Health Care"),
    "EMR": ("Industrials", "Electrical Components & Equipment"),
    "EOG": ("Energy", "Oil & Gas Exploration & Production"),
    "EQIX": ("Real Estate", "Data Center REITs"),
    "ETN": ("Industrials", "Electrical Components & Equipment"),
    "FDX": ("Industrials", "Air Freight & Logistics"),
    "GD": ("Industrials", "Aerospace & Defense"),
    "GE": ("Industrials", "Aerospace & Defense"),
    "GILD": ("Health Care", "Biotechnology"),
    "GOOG": ("Communication Services", "Interactive Media & Services"),
    "GOOGL": ("Communication Services", "Interactive Media & Services"),
    "GS": ("Financials", "Investment Banking & Brokerage"),
    "HCA": ("Health Care", "Health Care Facilities"),
    "HD": ("Consumer Discretionary", "Home Improvement Retail"),
    "HON": ("Industrials", "Industrial Conglomerates"),
    "IBM": ("Information Technology", "IT Consulting & Other Services"),
    "ICE": ("Financials", "Financial Exchanges & Data"),
    "INTC": ("Information Technology", "Semiconductors"),
    "INTU": ("Information Technology", "Application Software"),
    "ISRG": ("Health Care", "Health Care Equipment"),
    "ITW": ("Industrials", "Industrial Machinery & Supplies & Components"),
    "JNJ": ("Health Care", "Pharmaceuticals"),
    "JPM": ("Financials", "Diversified Banks"),
    "KLAC": ("Information Technology", "Semiconductor Materials & Equipment"),
    "KO": ("Consumer Staples", "Soft Drinks & Non-alcoholic Beverages"),
    "LIN": ("Materials", "Industrial Gases"),
    "LLY": ("Health Care", "Pharmaceuticals"),
    "LMT": ("Industrials", "Aerospace & Defense"),
    "LOW": ("Consumer Discretionary", "Home Improvement Retail"),
    "MA": ("Financials", "Transaction & Payment Processing Services"),
    "MAR": ("Consumer Discretionary", "Hotels, Resorts & Cruise Lines"),
    "MCD": ("Consumer Discretionary", "Restaurants"),
    "MCO": ("Financials", "Financial Exchanges & Data"),
    "MDLZ": ("Consumer Staples", "Packaged Foods & Meats"),
    "MDT": ("Health Care", "Health Care Equipment"),
    "META": ("Communication Services", "Interactive Media & Services"),
    "MO": ("Consumer Staples", "Tobacco"),
    "MRK": ("Health Care", "Pharmaceuticals"),
    "MS": ("Financials", "Investment Banking & Brokerage"),
    "MSFT": ("Information Technology", "Systems Software"),
    "MU": ("Information Technology", "Semiconductors"),
    "NEE": ("Utilities", "Electric Utilities"),
    "NFLX": ("Communication Services", "Movies & Entertainment"),
    "NKE": ("Consumer Discretionary", "Apparel, Accessories & Luxury Goods"),
    "NOC": ("Industrials", "Aerospace & Defense"),
    "NOW": ("Information Technology", "Systems Software"),
    "NVDA": ("Information Technology", "Semiconductors"),
    "ORCL": ("Information Technology", "Systems Software"),
    "ORLY": ("Consumer Discretionary", "Automotive Retail"),
    "PANW": ("Information Technology", "Systems Software"),
    "PEP": ("Consumer Staples", "Soft Drinks & Non-alcoholic Beverages"),
    "PFE": ("Health Care", "Pharmaceuticals"),
    "PG": ("Consumer Staples", "Household Products"),
    "PH": ("Industrials", "Industrial Machinery & Supplies & Components"),
    "PLD": ("Real Estate", "Industrial REITs"),
    "PM": ("Consumer Staples", "Tobacco"),
    "PNC": ("Financials", "Regional Banks"),
    "PYPL": ("Financials", "Transaction & Payment Processing Services"),
    "QCOM": ("Information Technology", "Semiconductors"),
    "REGN": ("Health Care", "Biotechnology"),
    "ROP": ("Information Technology", "Application Software"),
    "RTX": ("Industrials", "Aerospace & Defense"),
    "SBUX": ("Consumer Discretionary", "Restaurants"),
    "SCHW": ("Financials", "Investment Banking & Brokerage"),
    "SHW": ("Materials", "Specialty Chemicals"),
    "SLB": ("Energy", "Oil & Gas Equipment & Services"),
    "SNPS": ("Information Technology", "Application Software"),
    "SO": ("Utilities", "Electric Utilities"),
    "SPGI": ("Financials", "Financial Exchanges & Data"),
    "SYK": ("Health Care", "Health Care Equipment"),
    "T": ("Communication Services", "Integrated Telecommunication Services"),
    "TDG": ("Industrials", "Aerospace & Defense"),
    "TJX": ("Consumer Discretionary", "Apparel Retail"),
    "TMO": ("Health Care", "Life Sciences Tools & Services"),
    "TSLA": ("Consumer Discretionary", "Automobile Manufacturers"),
    "TXN": ("Information Technology", "Semiconductors"),
    "UBER": ("Industrials", "Passenger Ground Transportation"),
    "UNH": ("Health Care", "Managed Health Care"),
    "UNP": ("Industrials", "Rail Transportation"),
    "UPS": ("Industrials", "Air Freight & Logistics"),
    "USB": ("Financials", "Regional Banks"),
    "V": ("Financials", "Transaction & Payment Processing Services"),
    "VRTX": ("Health Care", "Biotechnology"),
    "VZ": ("Communication Services", "Integrated Telecommunication Services"),
    "WFC": ("Financials", "Diversified Banks"),
    "WM": ("Industrials", "Environmental & Facilities Services"),
    "WMT": ("Consumer Staples", "Consumer Staples Merchandise Retail"),
    "XOM": ("Energy", "Integrated Oil & Gas"),
    "ZTS": ("Health Care", "Pharmaceuticals"),
}


# --------------------------------------------------------------------------- #
# Small pure helpers
# --------------------------------------------------------------------------- #
def _norm(path: str) -> str:
    return path.replace("\\", "/")


def _now() -> str:
    return dt.datetime.now().replace(microsecond=0).isoformat()


def _safe_read_json(path: str) -> Optional[Dict[str, Any]]:
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (ValueError, OSError):
        return None


def _frac(num: int, den: int) -> float:
    return round(num / den, 6) if den else 0.0


# --------------------------------------------------------------------------- #
# Upstream Phase 2K-P routing confirmation
# --------------------------------------------------------------------------- #
def summarize_phase2k_p(kp: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Read-only digest of the Phase 2K-P result plus the routing confirmation."""
    if kp is None:
        return {"present": False, "routing_confirmed": False}
    rec = kp.get("recommendation", {}) or {}
    nxt = kp.get("recommended_next_phase", {}) or {}
    tmpl = kp.get("sector_map_template", {}) or {}

    phase_ok = kp.get("phase") == "2K-P"
    rec_ok = rec.get("recommendation") == PHASE2K_P_RECOMMENDATION
    routes_ok = nxt.get("phase") == "2K-Q"
    title_ok = nxt.get("title") == PHASE2K_Q_TITLE_FROM_PHASE2K_P
    template_generated = tmpl.get("generated") is True
    template_rows_ok = tmpl.get("row_count") == EXPECTED_TEMPLATE_ROW_COUNT
    create_ok = rec.get("create_model_candidate_now") is False
    train_ok = rec.get("train_model_now") is False
    retest_ok = rec.get("run_sector_relative_retest_now") is False

    return {
        "present": True,
        "phase": kp.get("phase"),
        "recommendation": rec.get("recommendation"),
        "recommended_next_phase": nxt,
        "template_generated": template_generated,
        "template_row_count": tmpl.get("row_count"),
        "phase_is_2k_p": phase_ok,
        "recommendation_is_template_created": rec_ok,
        "recommended_next_phase_is_2k_q": routes_ok,
        "recommended_next_phase_title_matches": title_ok,
        "template_generated_true": template_generated,
        "template_row_count_is_128": template_rows_ok,
        "create_model_candidate_now_false": create_ok,
        "train_model_now_false": train_ok,
        "run_sector_relative_retest_now_false": retest_ok,
        "routing_confirmed": bool(
            phase_ok and rec_ok and routes_ok and title_ok and template_generated
            and template_rows_ok and create_ok and train_ok and retest_ok),
    }


def read_template_tickers(path: str = PHASE2K_P_TEMPLATE) -> Optional[List[str]]:
    """Recover the equity-ticker universe from the Phase 2K-P template CSV (read-only)."""
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8", newline="") as f:
            rows = list(csv.DictReader(f))
    except (OSError, ValueError):
        return None
    tickers: List[str] = []
    for r in rows:
        t = str(r.get("ticker", "")).strip().upper()
        if t and t != BENCHMARK:
            tickers.append(t)
    return tickers


# --------------------------------------------------------------------------- #
# Populate + write the current-as-of sector map
# --------------------------------------------------------------------------- #
def build_populated_rows(tickers: List[str]) -> List[Dict[str, str]]:
    """One row per template ticker, populated from the curated current-as-of seed.

    Tickers absent from the seed are emitted with blank sector / industry so validation flags
    them as incomplete rather than silently dropping them.
    """
    rows: List[Dict[str, str]] = []
    for t in tickers:
        sector, industry = SECTOR_INDUSTRY_SEED.get(t, ("", ""))
        rows.append({
            "ticker": t,
            "sector": sector,
            "industry": industry,
            "source": MAP_SOURCE,
            "as_of_date": MAP_AS_OF_DATE,
            "point_in_time": MAP_POINT_IN_TIME,
            "notes": MAP_NOTES,
        })
    return rows


def write_sector_map(rows: List[Dict[str, str]], path: str = SECTOR_MAP_OUTPUT) -> None:
    """Write the populated sector map CSV (C: repo only; never the D: drive)."""
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(SECTOR_MAP_REQUIRED_COLUMNS)
        for r in rows:
            writer.writerow([r[c] for c in SECTOR_MAP_REQUIRED_COLUMNS])


# --------------------------------------------------------------------------- #
# Validation + distributions
# --------------------------------------------------------------------------- #
def validate_populated_map(rows: List[Dict[str, str]],
                           template_tickers: List[str]) -> Dict[str, Any]:
    """Validate the populated sector map against the Phase 2K-P template universe."""
    cols = list(rows[0].keys()) if rows else []
    has_required = all(c in cols for c in SECTOR_MAP_REQUIRED_COLUMNS)

    tickers = [str(r.get("ticker", "")).strip().upper() for r in rows]
    non_empty = [t for t in tickers if t]
    all_upper = bool(non_empty) and all(t == t.upper() for t in non_empty)
    duplicate_tickers = sorted({t for t in non_empty if non_empty.count(t) > 1})
    unique = not duplicate_tickers
    spy_present = BENCHMARK in non_empty

    by_ticker = {t: r for t, r in zip(tickers, rows)}
    template_set = set(template_tickers)
    covered = [t for t in template_tickers if t in by_ticker]
    missing = [t for t in template_tickers if t not in by_ticker]
    extra = sorted(t for t in by_ticker if t and t not in template_set and t != BENCHMARK)

    def _non_null(field: str) -> int:
        return sum(1 for t in covered if str(by_ticker[t].get(field, "")).strip())

    sector_non_null = _non_null("sector")
    industry_non_null = _non_null("industry")
    source_non_null = sum(1 for r in rows if str(r.get("source", "")).strip())
    as_of_non_null = sum(1 for r in rows if str(r.get("as_of_date", "")).strip())

    sectors = sorted({str(by_ticker[t].get("sector", "")).strip()
                      for t in covered if str(by_ticker[t].get("sector", "")).strip()})
    per_sector: Dict[str, int] = {}
    for t in covered:
        s = str(by_ticker[t].get("sector", "")).strip()
        if s:
            per_sector[s] = per_sector.get(s, 0) + 1
    min_per_sector = min(per_sector.values()) if per_sector else 0
    small_sectors = sorted(s for s, n in per_sector.items() if n < MIN_TICKERS_PER_SECTOR)

    pit_vals = [str(r.get("point_in_time", "")).strip().lower() for r in rows]
    pit_all_false = bool(pit_vals) and all(v == "false" for v in pit_vals)

    sector_coverage = _frac(sector_non_null, len(template_tickers))
    industry_coverage = _frac(industry_non_null, len(template_tickers))
    ticker_coverage = _frac(len(covered), len(template_tickers))

    full_coverage = (
        ticker_coverage == 1.0 and sector_coverage == 1.0 and industry_coverage == 1.0)

    return {
        "template_ticker_count": len(template_tickers),
        "row_count": len(rows),
        "columns_present": cols,
        "has_required_columns": has_required,
        "all_tickers_uppercase": all_upper,
        "tickers_unique": unique,
        "duplicate_tickers": duplicate_tickers,
        "spy_present": spy_present,
        "spy_absent": not spy_present,
        "covered_ticker_count": len(covered),
        "ticker_coverage_fraction": ticker_coverage,
        "missing_ticker_count": len(missing),
        "missing_tickers": missing[:25],
        "extra_ticker_count": len(extra),
        "extra_tickers": extra[:25],
        "sector_coverage_fraction": sector_coverage,
        "industry_coverage_fraction": industry_coverage,
        "source_populated_all_rows": source_non_null == len(rows) and len(rows) > 0,
        "as_of_date_populated_all_rows": as_of_non_null == len(rows) and len(rows) > 0,
        "point_in_time_all_false": pit_all_false,
        "distinct_sector_count": len(sectors),
        "sectors": sectors,
        "min_tickers_per_sector": min_per_sector,
        "small_sectors_below_min": small_sectors,
        "at_least_min_sectors": len(sectors) >= MIN_DISTINCT_SECTORS,
        "min_tickers_per_sector_met": min_per_sector >= MIN_TICKERS_PER_SECTOR,
        "full_coverage": full_coverage,
    }


def build_sector_distribution(rows: List[Dict[str, str]]) -> Dict[str, int]:
    dist: Dict[str, int] = {}
    for r in rows:
        s = str(r.get("sector", "")).strip()
        if s:
            dist[s] = dist.get(s, 0) + 1
    return dict(sorted(dist.items(), key=lambda kv: (-kv[1], kv[0])))


def build_industry_distribution(rows: List[Dict[str, str]]) -> Dict[str, int]:
    dist: Dict[str, int] = {}
    for r in rows:
        i = str(r.get("industry", "")).strip()
        if i:
            dist[i] = dist.get(i, 0) + 1
    return dict(sorted(dist.items(), key=lambda kv: (-kv[1], kv[0])))


# --------------------------------------------------------------------------- #
# Decision
# --------------------------------------------------------------------------- #
def select_recommendation(routing_ok: bool, template_present: bool,
                          validation: Dict[str, Any]) -> str:
    """Apply the conservative rules and pick exactly one recommendation."""
    if not routing_ok or not template_present:
        return REC_NOACTION

    complete = bool(
        validation.get("full_coverage")
        and validation.get("has_required_columns")
        and validation.get("tickers_unique")
        and validation.get("spy_absent")
        and validation.get("at_least_min_sectors"))
    if not complete:
        return REC_INCOMPLETE

    # Coverage is complete. A current-as-of (non-point-in-time) map and/or any small sector is a
    # caveat, not a blocker: report it as WITH_WARNINGS rather than claiming a clean READY.
    point_in_time_all = not validation.get("point_in_time_all_false")
    no_small_sectors = validation.get("min_tickers_per_sector_met")
    if point_in_time_all and no_small_sectors:
        return REC_READY
    return REC_WARNINGS


def build_caveats(validation: Dict[str, Any]) -> List[str]:
    caveats = [
        "The sector / industry map is current-as-of 2026-06-18 and is NOT point-in-time; "
        "historical sector membership at each test date is not reconstructed.",
        "The underlying D: equity universe is survivorship-biased / current-membership, so any "
        "sector-relative result remains survivorship-biased and is not a production edge.",
        "Sector / industry labels are a curated static mapping, not a licensed point-in-time "
        "classification feed; a clean point-in-time map would tighten, never rescue, a "
        "sub-floor edge.",
    ]
    small = validation.get("small_sectors_below_min") or []
    if small:
        caveats.append(
            "Small sectors with fewer than "
            f"{MIN_TICKERS_PER_SECTOR} tickers are present ({', '.join(small)}); within-sector "
            "ranking there is thin and the retest must treat those sectors cautiously.")
    return caveats


def build_recommendation(selected: str, validation: Dict[str, Any]) -> Dict[str, Any]:
    if selected == REC_READY:
        reason = (
            "The populated sector map covers 100% of the equity universe (sector and industry) "
            "with no duplicate tickers and no SPY row, across at least 5 sectors with at least "
            f"{MIN_TICKERS_PER_SECTOR} tickers each. A narrow, model-free sector-relative retest "
            "is feasible (Phase 2K-R) under the explicit current-as-of caveat. No retest is run "
            "here and no model candidate is created.")
    elif selected == REC_WARNINGS:
        small = validation.get("small_sectors_below_min") or []
        small_note = (f" One or more sectors have fewer than {MIN_TICKERS_PER_SECTOR} tickers "
                      f"({', '.join(small)})." if small else "")
        reason = (
            "The populated sector map covers 100% of the equity universe (sector and industry) "
            "with no duplicate tickers and no SPY row, across at least 5 sectors, but it carries "
            "caveats: it is current-as-of only (not point-in-time)." + small_note + " A narrow, "
            "model-free sector-relative retest is feasible (Phase 2K-R) provided these caveats "
            "are carried explicitly. No retest is run here and no model candidate is created.")
    elif selected == REC_INCOMPLETE:
        reason = (
            "The populated sector map has missing tickers / sectors / industries or duplicate "
            "tickers; repair it before any sector-relative retest. No retest is run here and no "
            "model candidate is created.")
    else:
        reason = (
            "Phase 2K-P did not route here or the sector-map template is missing; the "
            "population step is blocked until the inputs are repaired. No retest is run here and "
            "no model candidate is created.")
    return {
        "recommendation": selected,
        "allowed_values": list(_ALLOWED_RECOMMENDATIONS),
        "create_model_candidate_now": False,
        "train_model_now": False,
        "deploy_now": False,
        "run_sector_relative_retest_now": False,
        "production_edge_claimed": False,
        "results_are_survivorship_biased": True,
        "reason": reason,
    }


def build_recommended_next_phase(selected: str) -> Dict[str, str]:
    meta = NEXT_PHASE_BY_RECOMMENDATION.get(selected, NEXT_PHASE_BY_RECOMMENDATION[REC_NOACTION])
    return {"phase": NEXT_PHASE, "title": meta["title"], "purpose": meta["purpose"]}


def build_interpretation(selected: str, validation: Dict[str, Any]) -> Dict[str, Any]:
    if selected == REC_READY:
        verdict = ("populates a complete current-as-of sector map and clears a narrow, "
                   "model-free sector-relative retest to proceed in Phase 2K-R")
    elif selected == REC_WARNINGS:
        verdict = ("populates a complete current-as-of sector map (with explicit "
                   "current-as-of / small-sector caveats) and clears a caveated, model-free "
                   "sector-relative retest to proceed in Phase 2K-R")
    elif selected == REC_INCOMPLETE:
        verdict = "requires the sector map to be repaired before a sector-relative retest"
    else:
        verdict = "blocks the population step until the inputs are repaired"
    return {
        "model_trained": False,
        "model_fitted": False,
        "model_candidate_created": False,
        "authorized_to_train_model": False,
        "authorized_to_serve_model": False,
        "ran_broad_alpha_screen": False,
        "ran_sector_relative_retest": False,
        "candidate_set_expanded": False,
        "fetched_sector_data_from_network": False,
        "read_from_d_drive": False,
        "wrote_to_d_drive": False,
        "sector_map_is_point_in_time": False,
        "production_edge_claimed": False,
        "results_are_survivorship_biased": True,
        "narrative": (
            "Phase 2K-Q is a sector-map population phase after the Phase 2K-P feasibility gate. "
            "It read the Phase 2K-P template (not the D: CSV) to recover the "
            f"{validation.get('template_ticker_count')}-ticker equity universe (SPY excluded), "
            "populated one row per ticker from a curated current-as-of sector / industry seed, "
            "wrote the populated map to research/input, validated coverage, and " + verdict + ". "
            "It ran no broad alpha screen, ran no sector-relative retest, computed no alpha "
            "signal or label, trained / fitted / scored no model, created no model candidate, "
            "fetched nothing from the network, and read from / wrote nothing to the D: drive; "
            "the map is current-as-of (not point-in-time) and results remain survivorship-biased "
            "and are not a production edge."),
    }


def build_implementation_constraints() -> List[str]:
    return [
        "This is a sector-map population phase only: no sector-relative retest is run, no broad "
        "alpha screen is run, and no model is trained, fitted, scored, or created.",
        "The equity universe is recovered from the Phase 2K-P template (small C: file); the "
        "large D: price-history CSV is NOT read and nothing is read from the D: drive.",
        "No sector data is fetched from the network; the sector / industry values come from a "
        "curated static current-as-of seed embedded offline.",
        "The populated map is explicitly current-as-of and NOT point-in-time; results remain "
        "survivorship-biased and a clean point-in-time map would tighten, never rescue, a "
        "sub-floor edge.",
        "Nothing is written to the D: drive; only two small C: outputs are written (the "
        "populated sector-map CSV in research/input and the results JSON in research/output).",
        "No deployment, no model-v2 enablement, no migration, no database write, no order, and "
        "no automation; nothing here is a production edge.",
    ]


# --------------------------------------------------------------------------- #
# Assembly
# --------------------------------------------------------------------------- #
def build_results(*, phase2k_p_path: Optional[str] = None,
                  template_path: Optional[str] = None,
                  phase2k_o_path: Optional[str] = None,
                  phase2k_n_path: Optional[str] = None,
                  sector_map_output: Optional[str] = None
                  ) -> Tuple[Dict[str, Any], List[Dict[str, str]]]:
    phase2k_p_path = phase2k_p_path or PHASE2K_P_JSON
    template_path = template_path or PHASE2K_P_TEMPLATE
    phase2k_o_path = phase2k_o_path or PHASE2K_O_JSON
    phase2k_n_path = phase2k_n_path or PHASE2K_N_JSON
    sector_map_output = sector_map_output or SECTOR_MAP_OUTPUT

    kp = _safe_read_json(phase2k_p_path)
    kp_summary = summarize_phase2k_p(kp)

    template_tickers = read_template_tickers(template_path)
    template_present = template_tickers is not None
    tickers = template_tickers or []

    rows = build_populated_rows(tickers)
    validation = validate_populated_map(rows, tickers)

    routing_ok = bool(kp_summary.get("routing_confirmed"))
    selected = select_recommendation(routing_ok, template_present, validation)

    sector_distribution = build_sector_distribution(rows)
    industry_distribution = build_industry_distribution(rows)

    sector_map_population = {
        "output_path": _norm(sector_map_output),
        "base_template_path": _norm(template_path),
        "template_present": template_present,
        "template_ticker_count": len(tickers),
        "row_count": len(rows),
        "columns": list(SECTOR_MAP_REQUIRED_COLUMNS),
        "source": MAP_SOURCE,
        "as_of_date": MAP_AS_OF_DATE,
        "point_in_time": False,
        "notes": MAP_NOTES,
        "network_fetch_used": False,
        "fill_basis": "curated static current-as-of sector / industry seed (offline)",
    }

    results = {
        "phase": PHASE,
        "generated_at": _now(),
        "inputs_read": {
            "phase2k_p_json": _norm(phase2k_p_path),
            "phase2k_p_template_csv": _norm(template_path),
            "phase2k_o_json": _norm(phase2k_o_path),
            "phase2k_n_json": _norm(phase2k_n_path),
        },
        "sector_map_output": _norm(sector_map_output),
        # Safety flags (machine-readable; asserted by the tests).
        "database_touched": False,
        "database_write_executed": False,
        "migration_executed": False,
        "deployment_executed": False,
        "model_v2_enabled": False,
        "production_edge_claimed": False,
        "no_trading": True,
        "no_orders": True,
        "no_automation": True,
        "model_trained": False,
        "model_candidate_created": False,
        "d_drive_read": False,
        "d_drive_written": False,
        "broad_alpha_screen_run": False,
        "sector_relative_retest_run": False,
        "network_used": False,
        "upstream_phase2k_p_summary": kp_summary,
        "sector_map_population": sector_map_population,
        "sector_map_validation": validation,
        "sector_distribution": sector_distribution,
        "industry_distribution": industry_distribution,
        "caveats": build_caveats(validation),
        "implementation_constraints": build_implementation_constraints(),
        "recommendation": build_recommendation(selected, validation),
        "interpretation": build_interpretation(selected, validation),
        "recommended_next_phase": build_recommended_next_phase(selected),
        "selected_path": {
            "recommendation": selected,
            "decision_inputs": {
                "routing_confirmed": routing_ok,
                "template_present": template_present,
                "full_coverage": validation.get("full_coverage"),
                "tickers_unique": validation.get("tickers_unique"),
                "spy_absent": validation.get("spy_absent"),
                "at_least_min_sectors": validation.get("at_least_min_sectors"),
                "min_tickers_per_sector_met": validation.get("min_tickers_per_sector_met"),
                "point_in_time_all_false": validation.get("point_in_time_all_false"),
            },
        },
    }
    return results, rows


def write_results(results: Dict[str, Any], path: str = RESULTS_JSON) -> None:
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, allow_nan=False)


def run(output_path: str = RESULTS_JSON,
        sector_map_output: str = SECTOR_MAP_OUTPUT,
        **kwargs) -> Dict[str, Any]:
    results, rows = build_results(sector_map_output=sector_map_output, **kwargs)
    # Only write the populated map when the template universe was recovered (rows present).
    if rows:
        write_sector_map(rows, sector_map_output)
    write_results(results, output_path)
    return results


def main() -> int:
    t0 = time.perf_counter()
    d = run()
    elapsed = time.perf_counter() - t0
    rec = d["recommendation"]
    nxt = d["recommended_next_phase"]
    kp = d["upstream_phase2k_p_summary"]
    val = d["sector_map_validation"]
    print(f"[phase2k-q] upstream 2K-P routing confirmed : {kp.get('routing_confirmed')}")
    print(f"[phase2k-q] populated rows                  : {val.get('row_count')} "
          f"(template {val.get('template_ticker_count')})")
    print(f"[phase2k-q] ticker / sector / industry cov  : "
          f"{val.get('ticker_coverage_fraction')} / {val.get('sector_coverage_fraction')} / "
          f"{val.get('industry_coverage_fraction')}")
    print(f"[phase2k-q] distinct sectors                : {val.get('distinct_sector_count')} "
          f"(min size {val.get('min_tickers_per_sector')})")
    print(f"[phase2k-q] SPY absent / unique / pit=false  : {val.get('spy_absent')} / "
          f"{val.get('tickers_unique')} / {val.get('point_in_time_all_false')}")
    print(f"[phase2k-q] sector map written               : {SECTOR_MAP_OUTPUT}")
    print(f"[phase2k-q] recommendation                  : {rec['recommendation']}")
    print(f"[phase2k-q] recommended next phase           : {nxt['phase']} ({nxt['title']})")
    print(f"[phase2k-q] results written                 : {RESULTS_JSON}")
    print(f"[phase2k-q] elapsed seconds                  : {elapsed:.2f}")
    print("[phase2k-q] population only; no D: read; no retest; no model trained; no network.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
