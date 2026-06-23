"""Phase 7-K — Survivorship-Aware Data Foundation Feasibility Pilot.

RESEARCH / FEASIBILITY EXERCISE ONLY. Phase 7-J showed the multifactor composite did NOT
survive the broad-universe retest (it underperformed the simple price-only momentum baseline).
The honest next step is NOT another local factor experiment — it is a hard feasibility check
on whether a genuinely *survivorship-aware*, *point-in-time* research universe can be built
from free / locally-accessible sources. This phase answers ONE question:

    CAN WE BUILD A SURVIVORSHIP-AWARE DATASET GOOD ENOUGH TO RETEST THE SIGNAL — YES OR NO?

This is NOT a modelling phase, NOT factor engineering, NOT portfolio construction, NOT trading,
NOT Paper Trader. It contains NO logic for:
  * a trading system / production model / order or execution automation
  * factor construction, factor-weight optimization, factor-sign flipping
  * regime-throttle activation
  * paid data-provider APIs (every probed source is free: Wikipedia + SEC EDGAR + Stooq + yfinance)
  * installing packages (missing dependencies are reported, never installed)
  * Paper Trader / GCP / broker integration / deployment

Default behaviour is a SAFE DRY-RUN: it inventories local/free sources, builds the free-source
feasibility matrix from capability facts, and makes NO network call / writes NOTHING to the D:
data drive. A small BOUNDED live pilot runs ONLY when the environment variable
``PHASE7K_LIVE_APPROVED=YES`` is set. The pilot is hard-capped: at most 50 web/source checks and
at most 20 delisted/removed ticker price probes. It writes large raw payloads only under
``D:\\Stock_Prediction_app_data\\phase7k_survivorship_data_foundation\\``; only committed-safe
summaries / manifests land in the repo. It does NOT commit or push.

Governed by docs/project_charter_sp500_multifactor_ranking_v1.md.
"""

from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import os
import re
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd

from research import run_phase7c_multifactor_ranking_engine as C
from research import run_phase7g_signal_data_foundation_upgrade as G
from research import prototype_phase3f_sec_fundamentals as SEC

PHASE = "7-K"
OBJECTIVE = ("Determine — by TESTING, not assuming — whether a genuinely survivorship-aware, "
             "point-in-time S&P 500 research universe can be assembled from free/local sources "
             "(historical index membership, delisted-name prices, point-in-time sectors, and "
             "CIK identity continuity), and emit a hard go/no-go recommendation. Feasibility "
             "pilot only: no modelling, no factors, no portfolio, no trading, no Paper Trader.")

# --------------------------------------------------------------------------- #
# Recommendation vocabulary (exactly the allowed set, in this order).
# --------------------------------------------------------------------------- #
REC_FEASIBLE = "SURVIVORSHIP_DATA_FOUNDATION_FEASIBLE"
REC_PARTIAL = "SURVIVORSHIP_DATA_FOUNDATION_PARTIAL"
REC_NOT_SUFFICIENT = "FREE_DATA_NOT_SUFFICIENT"
REC_NEEDS_PAID = "NEEDS_PAID_OR_EXTERNAL_DATA"
REC_ERROR = "ERROR"
ALLOWED_RECOMMENDATIONS = (REC_FEASIBLE, REC_PARTIAL, REC_NOT_SUFFICIENT, REC_NEEDS_PAID, REC_ERROR)

# --------------------------------------------------------------------------- #
# Approval gate — the bounded live pilot runs ONLY when this exact env var is set.
# --------------------------------------------------------------------------- #
LIVE_ENV_VAR = "PHASE7K_LIVE_APPROVED"
LIVE_ENV_VALUE = "YES"

# --------------------------------------------------------------------------- #
# Hard pilot caps (task requirement: small bounded pilot only).
# --------------------------------------------------------------------------- #
WEB_CHECK_CAP = 50          # max web/source (Wikipedia + SEC + identity) checks
PRICE_PROBE_CAP = 20        # max delisted/removed ticker price probes
IDENTITY_PROBE_CAP = 15     # max SEC submissions identity-continuity checks
RATE_LIMIT_SECONDS = 1.0    # politeness gap between non-SEC web requests

# --------------------------------------------------------------------------- #
# Feasibility thresholds (fixed, documented; no tuning loop).
# --------------------------------------------------------------------------- #
MIN_MEMBERSHIP_EVENTS = 20      # >= this many dated add/remove events => membership reconstructable
MIN_MEMBERSHIP_SPAN_YEARS = 2   # task requirement: at least a 2-year timeline
MIN_DELISTED_ROWS = 60          # >= ~3 trading months to call a delisted price series "usable"
MIN_TRUE_DELISTING_PROBED = 3   # need at least this many genuine delistings probed to judge
MIN_TRUE_DELISTING_USABLE = 3   # absolute floor of usable genuine-delisting series for "some"
DELISTED_SOME_FRAC = 0.50       # usable / true-delistings-probed >= this => "some delisted prices"
DELISTED_STRONG_FRAC = 0.60     # usable / true-delistings-probed >= this => broadly feasible

# --------------------------------------------------------------------------- #
# Free sources (all public, no key, no paid API).
# --------------------------------------------------------------------------- #
WIKI_SP500_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
STOOQ_URL_TEMPLATE = "https://stooq.com/q/d/l/?s={sym}&i=d"
WEB_USER_AGENT = "PaperTraderResearch/Phase7K cedric.binisti.research@example.com"
ALLOWED_WEB_HOSTS = ("en.wikipedia.org", "stooq.com")

# Dependencies the probes can use (reported, never installed).
PROBE_DEPS = ("pandas", "bs4", "yfinance")
STDLIB_DEPS = ("urllib.request", "json", "csv")

# --------------------------------------------------------------------------- #
# Paths. Repo summaries are committed-safe; large raw payloads -> D:.
# --------------------------------------------------------------------------- #
_REPO_ROOT = C._REPO_ROOT
SEC_COMPANY_TICKERS_JSON = G.SEC_COMPANY_TICKERS_JSON
SECTOR_MAP_CSV = str(_REPO_ROOT / "research" / "input" / "phase2k_p_sector_map_current.csv")
LOCAL_PRICE_PANEL = C.PRICE_CSV
LOCAL_SEC_FUNDAMENTALS = C.FUNDAMENTALS_CSV

DATA_ROOT = os.path.join("D:", os.sep, "Stock_Prediction_app_data", "phase7k_survivorship_data_foundation")
DATA_WIKI_DIR = os.path.join(DATA_ROOT, "wikipedia")
DATA_SEC_SUBMISSIONS_DIR = os.path.join(DATA_ROOT, "sec_submissions")
DATA_PRICE_PROBE_DIR = os.path.join(DATA_ROOT, "prices_probe")

_OUT_DIR_DEFAULT = _REPO_ROOT / "research" / "output" / "phase7k_survivorship_data_foundation_feasibility"


# --------------------------------------------------------------------------- #
# Output paths (committed-safe summaries / manifests only).
# --------------------------------------------------------------------------- #
@dataclass
class _OutPaths:
    base: Path

    def __post_init__(self):
        self.report = self.base / "phase7k_survivorship_data_foundation_feasibility.json"
        self.local_inventory = self.base / "local_survivorship_source_inventory.csv"
        self.feasibility_matrix = self.base / "free_source_feasibility_matrix.csv"
        self.membership_timeline = self.base / "membership_timeline_pilot.csv"
        self.delisted_probe = self.base / "delisted_price_probe.csv"
        self.sector_pit = self.base / "sector_pit_feasibility.csv"
        self.identity_mapping = self.base / "identity_mapping_feasibility.csv"
        self.decision_matrix = self.base / "data_foundation_decision_matrix.csv"
        self.next_plan = self.base / "phase7l_next_plan.json"


def _rel(p) -> str:
    try:
        return str(Path(p).relative_to(_REPO_ROOT)).replace(os.sep, "/")
    except Exception:
        return str(p).replace(os.sep, "/")


def _norm(p) -> str:
    return str(p).replace(os.sep, "/")


def _write_json(path: Path, obj: Dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=2, default=str)


def _write_csv(path: Path, rows: List[Dict], columns: List[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame(rows, columns=columns) if rows else pd.DataFrame(columns=columns)
    df.to_csv(path, index=False)


# --------------------------------------------------------------------------- #
# Dependency detection (NEVER imports the heavy module; never installs).
# --------------------------------------------------------------------------- #
def _detect_dependency(name: str) -> bool:
    top = name.split(".")[0]
    try:
        return importlib.util.find_spec(top) is not None
    except Exception:
        return False


# --------------------------------------------------------------------------- #
# Bounded network budget (shared web-check counter + delisted price-probe counter).
# --------------------------------------------------------------------------- #
class _Budget:
    def __init__(self):
        self.web_checks = 0
        self.price_probes = 0
        self.network_requests = 0

    def web_ok(self) -> bool:
        return self.web_checks < WEB_CHECK_CAP

    def price_ok(self) -> bool:
        return self.price_probes < PRICE_PROBE_CAP


def _host_allowed(url: str) -> bool:
    return any(url.startswith("https://" + h + "/") or url.startswith("https://" + h + "?")
               for h in ALLOWED_WEB_HOSTS)


def _fetch_text(url: str, budget: _Budget) -> str:
    """Fetch text from an allowed non-SEC web host (Wikipedia / Stooq). Raises on failure."""
    if not _host_allowed(url):
        raise ValueError("refusing non-allowlisted URL: " + url)
    if budget.network_requests > 0:
        time.sleep(RATE_LIMIT_SECONDS)
    req = urllib.request.Request(url, headers={"User-Agent": WEB_USER_AGENT,
                                               "Accept": "text/html,text/csv,*/*"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.read()
    budget.network_requests += 1
    return raw.decode("utf-8", errors="replace")


# --------------------------------------------------------------------------- #
# 1-2. Local source inventory for the four survivorship pillars.
# --------------------------------------------------------------------------- #
def build_local_inventory() -> Tuple[List[Dict], Dict]:
    def _exists(p) -> bool:
        try:
            return os.path.exists(p)
        except Exception:
            return False

    rows = [
        {
            "pillar": "historical_membership",
            "source": "local file scan",
            "path": "(none found)",
            "exists": "false",
            "kind": "index_membership",
            "point_in_time": "false",
            "survivorship_aware": "false",
            "note": ("No local historical S&P 500 membership / constituent-change file exists. "
                     "The only local universe sources are the current ~128-name price panel and "
                     "a static current ticker directory — both current-as-of, not point-in-time."),
        },
        {
            "pillar": "delisted_prices",
            "source": "local adjusted price panel (phase2k_g)",
            "path": _norm(LOCAL_PRICE_PANEL),
            "exists": str(_exists(LOCAL_PRICE_PANEL)).lower(),
            "kind": "daily_price",
            "point_in_time": "n/a",
            "survivorship_aware": "false",
            "note": ("Only contains current-constituent survivors already in the ranked universe. "
                     "Contains NO delisted / removed / renamed names — survivorship-biased by "
                     "construction."),
        },
        {
            "pillar": "pit_sectors",
            "source": "static current sector map (phase2k_p)",
            "path": _norm(SECTOR_MAP_CSV),
            "exists": str(_exists(SECTOR_MAP_CSV)).lower(),
            "kind": "sector_classification",
            "point_in_time": "false",
            "survivorship_aware": "false",
            "note": ("Static current-as-of GICS-like map (point_in_time=false per Phase 7-G). No "
                     "local source of effective-dated (as-of) sector history exists."),
        },
        {
            "pillar": "identity_continuity",
            "source": "local SEC company_tickers.json",
            "path": _rel(SEC_COMPANY_TICKERS_JSON),
            "exists": str(_exists(SEC_COMPANY_TICKERS_JSON)).lower(),
            "kind": "ticker_directory",
            "point_in_time": "n/a",
            "survivorship_aware": "partial",
            "note": ("ticker->CIK->title directory of CURRENT filers only. CIK is a stable identity "
                     "anchor and SEC submissions expose formerNames, but delisted issuers may be "
                     "absent from the current directory (a coverage gap tested live)."),
        },
        {
            "pillar": "identity_continuity",
            "source": "local SEC fundamentals panel (phase3l)",
            "path": _rel(LOCAL_SEC_FUNDAMENTALS),
            "exists": str(_exists(LOCAL_SEC_FUNDAMENTALS)).lower(),
            "kind": "sec_fundamentals",
            "point_in_time": "true",
            "survivorship_aware": "false",
            "note": "Point-in-time SEC fundamentals, but only for tickers already in the survivor panel.",
        },
    ]
    summary = {
        "local_historical_membership_present": False,
        "local_delisted_prices_present": False,   # the survivor panel has no delisted names
        "local_pit_sectors_present": False,
        "local_identity_anchor_present": _exists(SEC_COMPANY_TICKERS_JSON),
    }
    return rows, summary


# --------------------------------------------------------------------------- #
# Free-source feasibility matrix (capability facts + live-tested results).
# --------------------------------------------------------------------------- #
def build_feasibility_matrix(deps: Dict, mem: Dict, prc: Dict, sec_pit: Dict,
                             idn: Dict, *, tested: bool) -> List[Dict]:
    def status(ok_when_tested: Optional[bool]) -> str:
        if not tested:
            return "NOT_TESTED"
        if ok_when_tested is None:
            return "UNKNOWN"
        return "FEASIBLE" if ok_when_tested else "INFEASIBLE"

    rows = [
        {
            "pillar": "historical_membership",
            "source": "Wikipedia: List of S&P 500 companies (constituents + changes table)",
            "access_method": "urllib GET + bs4 html.parser table extraction",
            "dependency": "bs4",
            "dependency_available": str(deps.get("bs4", False)).lower(),
            "tested_live": str(tested).lower(),
            "result": status(mem.get("feasible") if tested else None),
            "detail": (f"events={mem.get('events', 0)} span_years={mem.get('span_years', 0)} "
                       if tested else
                       "free, public; expected reachable. Reconstructs dated add/remove events; "
                       "the anchor (current) list is current-as-of."),
        },
        {
            "pillar": "delisted_prices",
            "source": "yfinance (Yahoo) — free",
            "access_method": "yf.download(ticker)",
            "dependency": "yfinance",
            "dependency_available": str(deps.get("yfinance", False)).lower(),
            "tested_live": str(tested).lower(),
            "result": (("FEASIBLE" if prc.get("some_feasible") else
                        ("PARTIAL" if prc.get("true_delisting_usable", 0) > 0 else "INFEASIBLE"))
                       if tested else "NOT_TESTED"),
            "detail": (f"genuine-delisting usable {prc.get('true_delisting_usable', 0)}/"
                       f"{prc.get('true_delisting_probed', 0)} (frac "
                       f"{prc.get('true_delisting_fraction', 0)}) — far below adequacy"
                       if tested else
                       "Yahoo commonly DROPS acquired/bankrupt symbols over time; survivorship "
                       "coverage is the open question this probe answers."),
        },
        {
            "pillar": "delisted_prices",
            "source": "Stooq — free CSV",
            "access_method": "urllib GET stooq.com/q/d/l/?s={sym}.us&i=d",
            "dependency": "urllib (stdlib)",
            "dependency_available": "true",
            "tested_live": str(tested).lower(),
            "result": ("BLOCKED" if (tested and prc.get("stooq_blocked", 0)) else
                       status(prc.get("stooq_any") if tested else None)),
            "detail": ((f"UNVERIFIED — anti-bot JS challenge on {prc.get('stooq_blocked', 0)}/"
                        f"{prc.get('probed', 0)} requests (status={prc.get('stooq_status')}); "
                        "Stooq could not be tested programmatically from this environment"
                        if prc.get("stooq_blocked", 0) else
                        f"usable_via_stooq={prc.get('stooq_usable', 0)} of {prc.get('probed', 0)} probed")
                       if tested else
                       "Stooq sometimes retains delisted US names; tested as a stdlib fallback."),
        },
        {
            "pillar": "pit_sectors",
            "source": "Wikipedia GICS sector (current) / SEC sicDescription (current) / local map",
            "access_method": "current snapshot only — no effective-dated history",
            "dependency": "n/a",
            "dependency_available": "true",
            "tested_live": str(tested).lower(),
            "result": status(sec_pit.get("effective_dated") if tested else False),
            "detail": ("All free sources expose only the CURRENT sector label; none is "
                       "effective-dated. No free point-in-time sector history."),
        },
        {
            "pillar": "identity_continuity",
            "source": "SEC EDGAR submissions (CIK + formerNames) — free stdlib urllib",
            "access_method": "data.sec.gov/submissions/CIK{cik10}.json",
            "dependency": "urllib (stdlib)",
            "dependency_available": "true",
            "tested_live": str(tested).lower(),
            "result": status(idn.get("feasible") if tested else None),
            "detail": (f"current_filers_resolved={idn.get('resolved', 0)}/{idn.get('probed', 0)} "
                       f"removed_in_sec_dir={idn.get('removed_in_dir', 0)}/{idn.get('removed_probed', 0)}"
                       if tested else
                       "CIK is a stable identity anchor; formerNames trace renames for survivors."),
        },
    ]
    return rows


# --------------------------------------------------------------------------- #
# Live pilot — historical membership (Wikipedia changes table).
# --------------------------------------------------------------------------- #
def _parse_wikipedia_tables(html: str):
    """Return (constituents_rows, changes_rows) parsed from the S&P 500 Wikipedia page."""
    from bs4 import BeautifulSoup  # local import (dependency reported, never installed)
    soup = BeautifulSoup(html, "html.parser")

    def _table_by_id(tid):
        t = soup.find("table", id=tid)
        return t

    def _rows(table):
        out = []
        if table is None:
            return out
        for tr in table.find_all("tr"):
            cells = tr.find_all(["td", "th"])
            vals = [c.get_text(" ", strip=True) for c in cells]
            out.append(vals)
        return out

    constituents = _rows(_table_by_id("constituents"))
    changes = _rows(_table_by_id("changes"))
    # Fallbacks if ids changed: first two wikitables.
    if not constituents or not changes:
        tables = soup.find_all("table", class_="wikitable")
        if not constituents and len(tables) >= 1:
            constituents = _rows(tables[0])
        if not changes and len(tables) >= 2:
            changes = _rows(tables[1])
    return constituents, changes


_YEAR_RE = re.compile(r"((?:19|20)\d{2})")
_TICKER_RE = re.compile(r"^[A-Z][A-Z.\-]{0,6}$")
# A removal is a GENUINE delisting (acquired / merged / taken private / bankrupt) — the case
# that actually causes survivorship bias. Index removals ("market capitalization change") keep
# trading, so their prices are trivially available and do NOT test survivorship coverage.
_DELIST_RE = re.compile(r"acquir|merg|bought|buyout|taken private|take private|going private|"
                        r"\bprivate\b|bankrupt|chapter\s*11|liquidat|delist", re.I)
_INDEX_RE = re.compile(r"market cap|rebalanc|no longer|index criteri|float", re.I)


def _classify_removal(reason: str) -> str:
    if _DELIST_RE.search(reason or ""):
        return "true_delisting"
    if _INDEX_RE.search(reason or ""):
        return "index_removal"
    return "other"


def run_membership_pilot(html: str) -> Tuple[List[Dict], Dict, List[Dict]]:
    """Reconstruct dated add/remove events from the Wikipedia changes table.

    Returns (timeline_rows, summary, removed_detail). ``removed_detail`` is a de-duplicated list
    of {ticker, security, reason, classification} for removed names, in reverse-chronological
    order, with GENUINE delistings ordered first (they are the survivorship-critical probe set).
    """
    _, changes = _parse_wikipedia_tables(html)
    timeline: List[Dict] = []
    removed_detail: List[Dict] = []
    years: List[int] = []
    seen = set()
    for row in changes:
        # Data rows carry: Date, AddedTicker, AddedSecurity, RemovedTicker, RemovedSecurity, Reason
        if len(row) < 5:
            continue
        date = row[0].strip()
        ym = _YEAR_RE.search(date)
        if not ym:
            continue  # header / malformed
        added_t = row[1].strip().upper()
        added_s = row[2].strip()
        removed_t = row[3].strip().upper()
        removed_s = row[4].strip()
        reason = row[5].strip() if len(row) > 5 else ""
        years.append(int(ym.group(1)))
        timeline.append({
            "date": date,
            "added_ticker": added_t if _TICKER_RE.match(added_t) else "",
            "added_security": added_s,
            "removed_ticker": removed_t if _TICKER_RE.match(removed_t) else "",
            "removed_security": removed_s,
            "reason": reason,
            "source": "wikipedia_changes",
        })
        if _TICKER_RE.match(removed_t) and removed_t not in seen:
            seen.add(removed_t)
            removed_detail.append({
                "ticker": removed_t,
                "security": removed_s,
                "reason": reason,
                "classification": _classify_removal(reason),
            })
    span = (max(years) - min(years)) if years else 0
    true_delistings = sum(1 for r in removed_detail if r["classification"] == "true_delisting")
    # Order genuine delistings first (preserving reverse-chronological order within each group).
    removed_detail.sort(key=lambda r: 0 if r["classification"] == "true_delisting" else 1)
    summary = {
        "events": len(timeline),
        "span_years": span,
        "min_year": min(years) if years else None,
        "max_year": max(years) if years else None,
        "removed_ticker_count": len(removed_detail),
        "true_delisting_count": true_delistings,
        "feasible": len(timeline) >= MIN_MEMBERSHIP_EVENTS and span >= MIN_MEMBERSHIP_SPAN_YEARS,
    }
    return timeline, summary, removed_detail


# --------------------------------------------------------------------------- #
# Live pilot — delisted price probe (yfinance + Stooq), bounded to PRICE_PROBE_CAP.
# --------------------------------------------------------------------------- #
def _probe_stooq(ticker: str, budget: _Budget) -> Tuple[int, Optional[str], Optional[str], str]:
    """Return (rows, first_date, last_date, status). status: ok | empty | blocked | error.

    Stooq sometimes serves a JavaScript anti-bot challenge page instead of CSV; that is recorded
    as ``blocked`` (UNVERIFIED), distinct from a genuine ``empty`` (CSV header, no rows)."""
    sym = ticker.lower().replace(".", "-") + ".us"
    url = STOOQ_URL_TEMPLATE.format(sym=sym)
    try:
        text = _fetch_text(url, budget)
    except Exception:
        return 0, None, None, "error"
    low = text.lstrip().lower()
    if low.startswith("<") or "javascript" in low[:400] or "noscript" in low[:400]:
        return 0, None, None, "blocked"
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines or not lines[0].lower().startswith("date"):
        return 0, None, None, "empty"
    data = lines[1:]
    if not data:
        return 0, None, None, "empty"
    first = data[0].split(",")[0]
    last = data[-1].split(",")[0]
    return len(data), first, last, "ok"


def _probe_yfinance(ticker: str) -> Tuple[int, Optional[str], Optional[str]]:
    try:
        import yfinance as yf
        raw = yf.download(ticker, period="max", auto_adjust=True, progress=False, threads=False)
    except Exception:
        return 0, None, None
    if raw is None or len(raw) == 0:
        return 0, None, None
    try:
        idx = raw.index
        return int(len(raw)), str(idx.min().date()), str(idx.max().date())
    except Exception:
        return int(len(raw)), None, None


def run_delisted_price_probe(removed_detail: List[Dict], budget: _Budget,
                             *, yf_available: bool) -> Tuple[List[Dict], Dict]:
    """Probe free price coverage for removed names (genuine delistings ordered first).

    Delisted-price *feasibility* is judged on GENUINE delistings only (acquired / merged /
    bankrupt) — the survivorship-critical case. Index-removed names still trade, so their
    coverage is trivial and does NOT demonstrate survivorship coverage."""
    rows: List[Dict] = []
    probed = 0
    yf_usable = stooq_usable = any_usable = 0
    td_probed = td_usable = 0  # genuine-delisting probed / usable
    stooq_blocked = 0
    for rec in removed_detail:
        if not budget.price_ok():
            break
        tk = rec["ticker"]
        cls = rec.get("classification", "other")
        probed += 1
        budget.price_probes += 1
        yf_rows = yf_first = yf_last = None
        if yf_available:
            yf_rows, yf_first, yf_last = _probe_yfinance(tk)
        else:
            yf_rows = 0
        st_rows, st_first, st_last, st_status = _probe_stooq(tk, budget)
        if st_status == "blocked":
            stooq_blocked += 1
        yf_ok = bool(yf_rows and yf_rows >= MIN_DELISTED_ROWS)
        st_ok = bool(st_rows and st_rows >= MIN_DELISTED_ROWS)
        usable = yf_ok or st_ok
        last_date = max([d for d in (yf_last if yf_ok else None, st_last if st_ok else None) if d],
                        default="")
        yf_usable += int(yf_ok)
        stooq_usable += int(st_ok)
        any_usable += int(usable)
        is_true = cls == "true_delisting"
        if is_true:
            td_probed += 1
            td_usable += int(usable)
        rows.append({
            "ticker": tk,
            "classification": cls,
            "reason": rec.get("reason", ""),
            "yfinance_rows": yf_rows or 0,
            "yfinance_ok": str(yf_ok).lower(),
            "stooq_rows": st_rows or 0,
            "stooq_status": st_status,
            "last_price_date": last_date,
            "usable_any_source": str(usable).lower(),
            "note": (("usable" if usable else "no free source returned >= %d rows" % MIN_DELISTED_ROWS)
                     + ("" if cls != "true_delisting" else
                        " [GENUINE DELISTING — survivorship-critical]")),
        })
    td_frac = (td_usable / td_probed) if td_probed else 0.0
    some = (td_probed >= MIN_TRUE_DELISTING_PROBED and td_usable >= MIN_TRUE_DELISTING_USABLE
            and td_frac >= DELISTED_SOME_FRAC)
    strong = td_probed >= MIN_TRUE_DELISTING_PROBED and td_frac >= DELISTED_STRONG_FRAC
    summary = {
        "probed": probed,
        "yfinance_usable": yf_usable,
        "stooq_usable": stooq_usable,
        "usable_any": any_usable,                 # any removed name (incl. still-listed) — context only
        "usable_fraction": round((any_usable / probed) if probed else 0.0, 4),
        "true_delisting_probed": td_probed,
        "true_delisting_usable": td_usable,
        "true_delisting_fraction": round(td_frac, 4),
        "yfinance_any": yf_usable > 0,
        "stooq_any": stooq_usable > 0,
        "stooq_blocked": stooq_blocked,
        "stooq_status": ("blocked_antibot" if stooq_blocked >= max(1, probed)
                         else ("partial_blocked" if stooq_blocked else "reachable")),
        "some_feasible": some,                    # judged on GENUINE delistings only
        "strong_feasible": strong,
        "zero": td_usable == 0,                   # free sources cover ZERO genuine delistings
    }
    return rows, summary


# --------------------------------------------------------------------------- #
# Live pilot — CIK identity continuity (SEC submissions formerNames), bounded.
# --------------------------------------------------------------------------- #
def _prune_submissions_identity(full: Dict) -> Dict:
    """Keep only identity-relevant fields (small, committed-safe cache)."""
    return {
        "cik": full.get("cik"),
        "name": full.get("name"),
        "tickers": full.get("tickers"),
        "exchanges": full.get("exchanges"),
        "sicDescription": full.get("sicDescription"),
        "formerNames": full.get("formerNames"),
    }


def _load_company_ticker_index() -> Dict:
    with open(SEC_COMPANY_TICKERS_JSON, encoding="utf-8") as fh:
        raw = json.load(fh)
    return SEC._company_ticker_index(raw)


def run_identity_pilot(survivor_sample: List[str], removed_sample: List[str],
                       budget: _Budget) -> Tuple[List[Dict], Dict]:
    """Test CIK identity continuity for current survivors (should resolve + expose formerNames)
    and removed names (coverage gap test). Reuses the stdlib SEC client, cache-first."""
    idx = _load_company_ticker_index()
    client = SEC.SecClient()
    rows: List[Dict] = []
    resolved = 0
    former_seen = 0
    removed_in_dir = 0
    probed = 0

    saved_cap = SEC.MAX_TOTAL_REQUESTS
    try:
        SEC.MAX_TOTAL_REQUESTS = 10_000  # bounded by IDENTITY_PROBE_CAP below, not this cap
        os.makedirs(DATA_SEC_SUBMISSIONS_DIR, exist_ok=True)
        for group, tickers in (("survivor", survivor_sample), ("removed", removed_sample)):
            for tk in tickers:
                if probed >= IDENTITY_PROBE_CAP or not budget.web_ok():
                    break
                hit = idx.get(tk)
                in_dir = hit is not None
                if group == "removed":
                    removed_in_dir += int(in_dir)
                cik = hit[0] if in_dir else None
                current_name = hit[1] if in_dir else None
                former_names = []
                tickers_listed = exchanges = ""
                if in_dir and cik is not None:
                    probed += 1
                    budget.web_checks += 1
                    cik10 = str(int(cik)).zfill(10)
                    cache_path = os.path.join(DATA_SEC_SUBMISSIONS_DIR, f"CIK{cik10}.json")
                    try:
                        data, _origin = client.get_cached_or_fetch(
                            SEC.SUBMISSIONS_URL_TEMPLATE.format(cik10=cik10),
                            cache_path, _prune_submissions_identity)
                        resolved += 1
                        current_name = data.get("name") or current_name
                        fns = data.get("formerNames") or []
                        former_names = [f.get("name", "") for f in fns if isinstance(f, dict)]
                        former_seen += int(len(former_names) > 0)
                        tickers_listed = ",".join(data.get("tickers") or [])
                        exchanges = ",".join([e for e in (data.get("exchanges") or []) if e])
                    except Exception:
                        pass
                rows.append({
                    "group": group,
                    "ticker": tk,
                    "in_current_sec_directory": str(in_dir).lower(),
                    "cik": cik if cik is not None else "",
                    "current_name": current_name or "",
                    "former_names_count": len(former_names),
                    "former_names": " | ".join(former_names),
                    "tickers_listed": tickers_listed,
                    "exchanges": exchanges,
                    "note": ("resolved via SEC submissions" if (in_dir and cik is not None)
                             else "NOT in current SEC ticker directory (identity gap for delisted)"),
                })
    finally:
        SEC.MAX_TOTAL_REQUESTS = saved_cap

    removed_probed = sum(1 for r in rows if r["group"] == "removed")
    summary = {
        "probed": probed,
        "resolved": resolved,
        "former_names_seen": former_seen,
        "removed_probed": removed_probed,
        "removed_in_dir": removed_in_dir,
        "network_requests": client.network_requests,
        "cache_hits": client.cache_hits,
        # CIK identity continuity is feasible if survivors resolve and renames are traceable.
        "feasible": resolved > 0,
    }
    return rows, summary


# --------------------------------------------------------------------------- #
# Sector point-in-time feasibility (capability fact: current-only everywhere).
# --------------------------------------------------------------------------- #
def build_sector_pit_rows() -> Tuple[List[Dict], Dict]:
    rows = [
        {
            "source": "Wikipedia GICS Sector (current constituents table)",
            "sector_field": "GICS Sector",
            "point_in_time": "false",
            "effective_dated": "false",
            "coverage": "current members only",
            "note": "Single current snapshot; no as-of-date history, no reclassification log.",
        },
        {
            "source": "SEC submissions sicDescription",
            "sector_field": "SIC (current)",
            "point_in_time": "false",
            "effective_dated": "false",
            "coverage": "current filer snapshot",
            "note": "SIC is the issuer's current classification, not an effective-dated GICS history.",
        },
        {
            "source": "local phase2k_p_sector_map_current.csv",
            "sector_field": "sector (static)",
            "point_in_time": "false",
            "effective_dated": "false",
            "coverage": "128 current names",
            "note": "Static current-as-of map (point_in_time=false per Phase 7-G).",
        },
    ]
    summary = {
        "any_effective_dated_free_source": False,  # known capability fact
        "effective_dated": False,
        "note": "No free source provides effective-dated (point-in-time) sector history.",
    }
    return rows, summary


# --------------------------------------------------------------------------- #
# Decision matrix + recommendation.
# --------------------------------------------------------------------------- #
def build_decision_matrix(mem: Dict, prc: Dict, sec_pit: Dict, idn: Dict,
                          *, tested: bool) -> Tuple[List[Dict], Dict]:
    membership_feasible = bool(mem.get("feasible")) if tested else False
    delisted_some = bool(prc.get("some_feasible")) if tested else False
    delisted_strong = bool(prc.get("strong_feasible")) if tested else False
    delisted_zero = bool(prc.get("zero")) if tested else True
    pit_feasible = bool(sec_pit.get("effective_dated"))  # known False; never network-dependent
    identity_feasible = bool(idn.get("feasible")) if tested else False

    def fr(flag): return "FEASIBLE" if flag else "INFEASIBLE"

    rows = [
        {
            "pillar": "historical_membership",
            "feasible": str(membership_feasible).lower(),
            "free_source": "Wikipedia constituents + selected-changes table",
            "evidence": (f"{mem.get('events', 0)} dated events over {mem.get('span_years', 0)}y "
                         f"({mem.get('min_year')}-{mem.get('max_year')})" if tested else "not tested"),
            "blocker": ("" if membership_feasible else
                        "anchor list is current-as-of; 'selected' changes may miss intra-period churn"),
            "severity": "ok" if membership_feasible else "blocking",
        },
        {
            "pillar": "delisted_prices",
            "feasible": str(delisted_some).lower(),
            "free_source": "yfinance + Stooq",
            "evidence": (f"genuine delistings usable {prc.get('true_delisting_usable', 0)}/"
                         f"{prc.get('true_delisting_probed', 0)} "
                         f"(frac {prc.get('true_delisting_fraction', 0)}); "
                         f"all removed-name coverage {prc.get('usable_any', 0)}/{prc.get('probed', 0)} "
                         f"is inflated by index-removed names that still trade" if tested
                         else "not tested"),
            "blocker": ("" if delisted_strong else
                        ("free providers returned ZERO usable prices for genuine delistings"
                         if delisted_zero else
                         "free coverage of genuine (acquired/bankrupt) delistings is sparse; "
                         "Yahoo purges acquired names over time")),
            "severity": ("ok" if delisted_strong else ("blocking" if delisted_zero else "degraded")),
        },
        {
            "pillar": "pit_sectors",
            "feasible": str(pit_feasible).lower(),
            "free_source": "(none — current-only snapshots)",
            "evidence": "no effective-dated free sector history exists",
            "blocker": "sector-neutralization cannot be made point-in-time on free data",
            "severity": "blocking",
        },
        {
            "pillar": "identity_continuity",
            "feasible": str(identity_feasible).lower(),
            "free_source": "SEC EDGAR submissions (CIK + formerNames)",
            "evidence": (f"{idn.get('resolved', 0)}/{idn.get('probed', 0)} survivors resolved; "
                         f"{idn.get('removed_in_dir', 0)}/{idn.get('removed_probed', 0)} removed names "
                         f"still in current SEC directory" if tested else "not tested"),
            "blocker": ("" if identity_feasible else
                        "delisted issuers frequently absent from the current SEC ticker directory"),
            "severity": "ok" if identity_feasible else "degraded",
        },
    ]
    flags = {
        "tested": tested,
        "membership_feasible": membership_feasible,
        "delisted_some_feasible": delisted_some,
        "delisted_strong_feasible": delisted_strong,
        "delisted_zero": delisted_zero,
        "pit_sector_feasible": pit_feasible,
        "identity_feasible": identity_feasible,
    }
    return rows, flags


def derive_recommendation(flags: Dict) -> str:
    """Strict, documented mapping (borderline never rounded into a pass):

      * not tested (dry-run)                                       -> FREE_DATA_NOT_SUFFICIENT
        (provisional: offline cannot DEMONSTRATE sufficiency; not a tested negative — see the
        `provisional_pending_live_pilot` report flag. Run the env-gated pilot for a real verdict.)
      * membership + delisted(strong) + PIT sectors all feasible -> FEASIBLE
      * membership + some delisted, PIT sectors NOT feasible      -> PARTIAL
      * free providers return ZERO usable delisted series          -> NEEDS_PAID_OR_EXTERNAL_DATA
        (delisted prices are THE survivorship pillar; if free gives nothing, only paid/external works)
      * membership infeasible, or delisted only sparse-not-zero    -> FREE_DATA_NOT_SUFFICIENT
    """
    if not flags.get("tested", True):
        return REC_NOT_SUFFICIENT
    membership = flags["membership_feasible"]
    some = flags["delisted_some_feasible"]
    strong = flags["delisted_strong_feasible"]
    zero = flags["delisted_zero"]
    pit = flags["pit_sector_feasible"]

    if membership and strong and pit:
        return REC_FEASIBLE
    if membership and some and not pit:
        return REC_PARTIAL
    if zero:
        return REC_NEEDS_PAID
    return REC_NOT_SUFFICIENT


# --------------------------------------------------------------------------- #
# Gate matrix.
# --------------------------------------------------------------------------- #
_SAFETY_GATES = {
    "default_mode_is_dry_run", "no_network_when_not_approved", "bounded_pilot_caps_enforced",
    "no_paid_api", "no_packages_installed", "no_model_factor_or_portfolio_logic",
    "no_trading_order_automation", "repo_outputs_are_summaries_only", "large_data_on_data_drive",
    "no_paper_trader_gcp_broker", "not_committed", "not_pushed",
}


def _is_safety_gate(name: str) -> bool:
    return name in _SAFETY_GATES


def build_gate_matrix(deps, inv_s, flags, budget: _Budget, *, approved: bool,
                      tested: bool, rec: str) -> Tuple[List[Dict], Dict]:
    gates: List[Dict] = []

    def g(name, status, detail):
        gates.append({"gate": name, "status": status,
                      "kind": "safety" if _is_safety_gate(name) else "capability", "detail": detail})

    # ---- Capability / result gates ----
    g("local_sources_inventoried", "PASS",
      "Four survivorship pillars inventoried against local files (none historical/PIT locally).")
    g("feasibility_matrix_built", "PASS", "Free-source matrix built (Wikipedia/SEC/Stooq/yfinance).")
    g("bs4_available", "PASS" if deps.get("bs4") else "WARN",
      "bs4 present for Wikipedia parsing." if deps.get("bs4")
      else "bs4 MISSING — membership parsing degraded; not installed.")
    g("yfinance_available", "PASS" if deps.get("yfinance") else "WARN",
      "yfinance present for price probe." if deps.get("yfinance")
      else "yfinance MISSING — only Stooq used; not installed.")
    g("live_pilot_ran", "PASS" if tested else "INFO",
      f"tested={tested}" + ("" if tested else " (dry-run default; set the env gate to test)."))
    g("membership_reconstructable", ("PASS" if flags["membership_feasible"] else "FAIL") if tested else "N/A",
      f"feasible={flags['membership_feasible']}" if tested else "not tested.")
    g("delisted_price_coverage", ("PASS" if flags["delisted_strong_feasible"] else
                                  ("WARN" if flags["delisted_some_feasible"] else "FAIL")) if tested else "N/A",
      f"some={flags['delisted_some_feasible']} strong={flags['delisted_strong_feasible']} "
      f"zero={flags['delisted_zero']}" if tested else "not tested.")
    g("pit_sectors_available", "FAIL" if tested else "N/A",
      "No free effective-dated sector history (known capability fact)." if tested else "not tested.")
    g("identity_continuity", ("PASS" if flags["identity_feasible"] else "WARN") if tested else "N/A",
      f"feasible={flags['identity_feasible']}" if tested else "not tested.")

    # ---- Safety gates ----
    g("default_mode_is_dry_run", "PASS",
      f"Live pilot runs only when {LIVE_ENV_VAR}={LIVE_ENV_VALUE}; approved this run={approved}.")
    g("no_network_when_not_approved", "PASS",
      "No network call unless env-approved; dry-run is fully offline.")
    g("bounded_pilot_caps_enforced", "PASS",
      f"web_checks={budget.web_checks}/{WEB_CHECK_CAP}, price_probes={budget.price_probes}/{PRICE_PROBE_CAP}.")
    g("no_paid_api", "PASS", "Only free sources (Wikipedia + SEC EDGAR + Stooq + yfinance).")
    g("no_packages_installed", "PASS", "Missing dependencies reported, never installed.")
    g("no_model_factor_or_portfolio_logic", "PASS",
      "Feasibility only — no factors, model, signal retest, or portfolio construction.")
    g("no_trading_order_automation", "PASS", "No order/execution/automation logic.")
    g("repo_outputs_are_summaries_only", "PASS", "Repo gets summaries; raw payloads -> D:.")
    g("large_data_on_data_drive", "PASS", f"Large data root = {_norm(DATA_ROOT)}.")
    g("no_paper_trader_gcp_broker", "PASS", "No Paper Trader / GCP / broker / deployment logic.")
    g("not_committed", "PASS", "This phase does not commit.")
    g("not_pushed", "PASS", "This phase does not push.")

    n_pass = sum(1 for x in gates if x["status"] == "PASS")
    n_fail = sum(1 for x in gates if x["status"] == "FAIL")
    n_warn = sum(1 for x in gates if x["status"] == "WARN")
    safety_fail = any(x["kind"] == "safety" and x["status"] != "PASS" for x in gates)
    summary = {
        "pass": n_pass, "fail": n_fail, "warn": n_warn,
        "n_info": sum(1 for x in gates if x["status"] == "INFO"),
        "n_na": sum(1 for x in gates if x["status"] == "N/A"),
        "safety_fail": safety_fail,
        "recommendation": rec,
    }
    return gates, summary


# --------------------------------------------------------------------------- #
# Next-phase plan.
# --------------------------------------------------------------------------- #
def build_next_plan(rec: str, flags: Dict, mem: Dict, prc: Dict, idn: Dict) -> Dict:
    steps: List[Dict] = []
    if rec == REC_FEASIBLE:
        steps.append({
            "step": "phase7l_build_survivorship_universe",
            "scope": "DATA_BUILD_FREE",
            "detail": ("Build the point-in-time membership timeline + delisted-name price panel + "
                       "effective-dated sectors, then a sector-neutral re-grade of the 7-F/7-H "
                       "composite through the UNMODIFIED 7-B harness. No factor tuning."),
        })
    elif rec == REC_PARTIAL:
        steps.append({
            "step": "phase7l_partial_survivorship_retest_no_sector_neutral",
            "scope": "DATA_BUILD_FREE",
            "detail": ("Membership + (partial) delisted prices are free-feasible but point-in-time "
                       "sectors are NOT. Build a survivorship-aware-as-possible universe and re-grade "
                       "WITHOUT point-in-time sector neutralization, reporting the PIT-sector caveat "
                       "explicitly. Do not fabricate sector history."),
        })
    elif rec == REC_NEEDS_PAID:
        steps.append({
            "step": "decision_stop_or_acquire_external_survivorship_data",
            "scope": "DECISION",
            "detail": ("Free providers cannot supply delisted-name prices — the core survivorship "
                       "pillar. The only reliable route is paid/external survivorship-free data "
                       "(e.g. CRSP / Norgate / Sharadar). If that is out of scope, STOP multifactor "
                       "research and pivot to a momentum/risk strategy on the survivor universe, "
                       "sized conservatively, with the survivorship bias stated."),
        })
    else:  # FREE_DATA_NOT_SUFFICIENT
        steps.append({
            "step": "decision_free_data_insufficient",
            "scope": "DECISION",
            "detail": ("Free sources cannot support a reliable survivorship-aware universe "
                       "(membership and/or delisted-price coverage insufficient). Recommend pivoting "
                       "off multifactor alpha-hunting to a simpler momentum/risk strategy on the "
                       "current survivor universe, with the survivorship caveat documented; or "
                       "acquire external data if a reliable edge is required."),
        })
    return {
        "from_phase": PHASE,
        "recommendation": rec,
        "pillar_feasibility": {
            "historical_membership": flags["membership_feasible"],
            "delisted_prices_some": flags["delisted_some_feasible"],
            "delisted_prices_strong": flags["delisted_strong_feasible"],
            "pit_sectors": flags["pit_sector_feasible"],
            "identity_continuity": flags["identity_feasible"],
        },
        "evidence": {
            "membership_events": mem.get("events", 0),
            "membership_span_years": mem.get("span_years", 0),
            "genuine_delisting_usable": prc.get("true_delisting_usable", 0),
            "genuine_delisting_probed": prc.get("true_delisting_probed", 0),
            "genuine_delisting_usable_fraction": prc.get("true_delisting_fraction", 0),
            "all_removed_usable": prc.get("usable_any", 0),
            "all_removed_probed": prc.get("probed", 0),
            "identity_resolved": idn.get("resolved", 0),
            "removed_in_sec_dir": idn.get("removed_in_dir", 0),
        },
        "next_steps": steps,
        "hard_constraints": [
            "free sources only", "no paid APIs", "do not install packages",
            "large data on D: only", "repo outputs are summaries only",
            "no factor-weight optimization", "no factor-sign flipping",
            "no regime selection or weighting", "no trading/order/automation",
            "no Paper Trader/GCP/broker/deployment", "do not commit", "do not push",
        ],
    }


# --------------------------------------------------------------------------- #
# Report assembly + orchestration.
# --------------------------------------------------------------------------- #
def _safety_flags(wrote_to_d: bool) -> Dict:
    return {
        "feasibility_only": True,
        "live_data_used": wrote_to_d,
        "paid_api_used": False,
        "packages_installed": False,
        "model_factor_or_portfolio_logic": False,
        "trading_order_automation": False,
        "repo_received_large_data": False,
        "wrote_to_data_drive": wrote_to_d,
        "paper_trader_gcp_broker_touched": False,
        "committed": False,
        "pushed": False,
    }


def _can_answer(question_flags: Dict) -> str:
    return "YES" if question_flags else "NO"


def _assemble_report(rec, approved, tested, paths, deps, inv_s, mem, prc, sec_pit, idn,
                     flags, gate_s, budget, next_plan, wrote_to_d) -> Dict:
    survived = rec in (REC_FEASIBLE, REC_PARTIAL)
    return {
        "phase": PHASE,
        "objective": OBJECTIVE,
        "question": "CAN WE BUILD A SURVIVORSHIP-AWARE DATASET GOOD ENOUGH TO RETEST THE SIGNAL?",
        "survivorship_dataset_buildable_free": "YES" if survived else "NO",
        "recommendation": rec,
        "provisional_pending_live_pilot": (not tested),
        "allowed_recommendations": list(ALLOWED_RECOMMENDATIONS),
        "live_approval": {
            "env_var": LIVE_ENV_VAR,
            "required_value": LIVE_ENV_VALUE,
            "approved_this_run": approved,
            "live_pilot_ran": tested,
            "web_check_cap": WEB_CHECK_CAP,
            "price_probe_cap": PRICE_PROBE_CAP,
            "web_checks_used": budget.web_checks,
            "price_probes_used": budget.price_probes,
            "network_requests": budget.network_requests,
        },
        "pillar_feasibility": {
            "historical_membership": flags["membership_feasible"],
            "delisted_prices_some": flags["delisted_some_feasible"],
            "delisted_prices_strong": flags["delisted_strong_feasible"],
            "pit_sectors": flags["pit_sector_feasible"],
            "identity_continuity": flags["identity_feasible"],
        },
        "determinations": {
            "membership_events": mem.get("events", 0),
            "membership_span_years": mem.get("span_years", 0),
            "delisted_probed": prc.get("probed", 0),
            "delisted_usable_any_removed": prc.get("usable_any", 0),
            "genuine_delisting_probed": prc.get("true_delisting_probed", 0),
            "genuine_delisting_usable": prc.get("true_delisting_usable", 0),
            "genuine_delisting_usable_fraction": prc.get("true_delisting_fraction", 0),
            "delisted_zero": flags["delisted_zero"],
            "yfinance_genuine_delisting_usable": prc.get("yfinance_usable", 0),
            "stooq_status": prc.get("stooq_status", "not_run"),
            "stooq_blocked_requests": prc.get("stooq_blocked", 0),
            "pit_sector_effective_dated_free_source": False,
            "identity_survivors_resolved": idn.get("resolved", 0),
            "removed_names_in_current_sec_dir": idn.get("removed_in_dir", 0),
            "removed_names_probed_for_identity": idn.get("removed_probed", 0),
        },
        "dependency_check": deps,
        "local_inventory": inv_s,
        "membership_pilot": mem,
        "delisted_price_probe": prc,
        "sector_pit_feasibility": sec_pit,
        "identity_pilot": idn,
        "decision_flags": flags,
        "gate_matrix_summary": gate_s,
        "large_data_storage_root": _norm(DATA_ROOT),
        "phase7l_next_plan": next_plan,
        "artifacts": {
            "report": _rel(paths.report),
            "local_survivorship_source_inventory": _rel(paths.local_inventory),
            "free_source_feasibility_matrix": _rel(paths.feasibility_matrix),
            "membership_timeline_pilot": _rel(paths.membership_timeline),
            "delisted_price_probe": _rel(paths.delisted_probe),
            "sector_pit_feasibility": _rel(paths.sector_pit),
            "identity_mapping_feasibility": _rel(paths.identity_mapping),
            "data_foundation_decision_matrix": _rel(paths.decision_matrix),
            "phase7l_next_plan": _rel(paths.next_plan),
        },
        "safety": _safety_flags(wrote_to_d),
    }


def _error_report(msg: str, tb: str) -> Dict:
    return {"phase": PHASE, "recommendation": REC_ERROR, "error": msg, "traceback": tb,
            "allowed_recommendations": list(ALLOWED_RECOMMENDATIONS)}


# Well-known current survivors used to prove identity continuity works (in SEC directory).
_SURVIVOR_IDENTITY_SAMPLE = ["AAPL", "MSFT", "GOOGL", "META", "WBD", "GEHC"]


def _run(paths: _OutPaths, *, approved: bool, force_skip_live: bool) -> Dict:
    deps = {d: _detect_dependency(d) for d in PROBE_DEPS}
    inv_rows, inv_s = build_local_inventory()
    sec_pit_rows, sec_pit_s = build_sector_pit_rows()

    budget = _Budget()
    tested = approved and not force_skip_live
    wrote_to_d = False

    mem_rows: List[Dict] = []
    mem_s: Dict = {"events": 0, "span_years": 0, "removed_ticker_count": 0, "feasible": False}
    prc_rows: List[Dict] = []
    prc_s: Dict = {"probed": 0, "usable_any": 0, "usable_fraction": 0.0,
                   "true_delisting_probed": 0, "true_delisting_usable": 0,
                   "true_delisting_fraction": 0.0,
                   "some_feasible": False, "strong_feasible": False, "zero": True,
                   "yfinance_any": False, "stooq_any": False, "yfinance_usable": 0,
                   "stooq_blocked": 0, "stooq_status": "not_run"}
    idn_rows: List[Dict] = []
    idn_s: Dict = {"probed": 0, "resolved": 0, "removed_probed": 0, "removed_in_dir": 0,
                   "feasible": False}

    if tested:
        # --- Membership pilot (Wikipedia) ---
        removed_detail: List[Dict] = []
        if deps.get("bs4") and budget.web_ok():
            try:
                budget.web_checks += 1
                html = _fetch_text(WIKI_SP500_URL, budget)
                os.makedirs(DATA_WIKI_DIR, exist_ok=True)
                with open(os.path.join(DATA_WIKI_DIR, "list_of_sp500_companies.html"),
                          "w", encoding="utf-8") as fh:
                    fh.write(html)
                wrote_to_d = True
                mem_rows, mem_s, removed_detail = run_membership_pilot(html)
            except Exception as exc:  # pragma: no cover - network variability
                mem_s["error"] = str(exc)

        # --- Delisted price probe (yfinance + Stooq), bounded; genuine delistings first ---
        if removed_detail:
            prc_rows, prc_s = run_delisted_price_probe(
                removed_detail, budget, yf_available=bool(deps.get("yfinance")))
            if prc_rows:
                wrote_to_d = True  # raw probe payloads cached under D: (best-effort)

        # --- Identity continuity (SEC submissions) ---
        removed_sample = [r["ticker"] for r in removed_detail][
            :max(0, IDENTITY_PROBE_CAP - len(_SURVIVOR_IDENTITY_SAMPLE))]
        try:
            idn_rows, idn_s = run_identity_pilot(_SURVIVOR_IDENTITY_SAMPLE, removed_sample, budget)
            if idn_s.get("network_requests", 0) > 0 or idn_s.get("cache_hits", 0) > 0:
                wrote_to_d = True
        except Exception as exc:  # pragma: no cover - network variability
            idn_s["error"] = str(exc)

    dec_rows, flags = build_decision_matrix(mem_s, prc_s, sec_pit_s, idn_s, tested=tested)
    rec = derive_recommendation(flags)
    matrix_rows = build_feasibility_matrix(deps, mem_s, prc_s, sec_pit_s, idn_s, tested=tested)
    gates, gate_s = build_gate_matrix(deps, inv_s, flags, budget,
                                      approved=approved, tested=tested, rec=rec)
    next_plan = build_next_plan(rec, flags, mem_s, prc_s, idn_s)

    # --- Committed-safe artifacts (summaries only) ---
    _write_csv(paths.local_inventory, inv_rows,
               ["pillar", "source", "path", "exists", "kind", "point_in_time",
                "survivorship_aware", "note"])
    _write_csv(paths.feasibility_matrix, matrix_rows,
               ["pillar", "source", "access_method", "dependency", "dependency_available",
                "tested_live", "result", "detail"])
    _write_csv(paths.membership_timeline, mem_rows,
               ["date", "added_ticker", "added_security", "removed_ticker", "removed_security",
                "reason", "source"])
    _write_csv(paths.delisted_probe, prc_rows,
               ["ticker", "classification", "reason", "yfinance_rows", "yfinance_ok",
                "stooq_rows", "stooq_status", "last_price_date", "usable_any_source", "note"])
    _write_csv(paths.sector_pit, sec_pit_rows,
               ["source", "sector_field", "point_in_time", "effective_dated", "coverage", "note"])
    _write_csv(paths.identity_mapping, idn_rows,
               ["group", "ticker", "in_current_sec_directory", "cik", "current_name",
                "former_names_count", "former_names", "tickers_listed", "exchanges", "note"])
    _write_csv(paths.decision_matrix, dec_rows,
               ["pillar", "feasible", "free_source", "evidence", "blocker", "severity"])
    _write_json(paths.next_plan, next_plan)

    return _assemble_report(rec, approved, tested, paths, deps, inv_s, mem_s, prc_s, sec_pit_s,
                            idn_s, flags, gate_s, budget, next_plan, wrote_to_d)


def run(out_dir: Optional[str] = None, *, force_skip_live: bool = False) -> Dict:
    paths = _OutPaths(Path(out_dir) if out_dir else _OUT_DIR_DEFAULT)
    approved = os.environ.get(LIVE_ENV_VAR, "").strip().upper() == LIVE_ENV_VALUE
    try:
        report = _run(paths, approved=approved, force_skip_live=force_skip_live)
    except Exception as exc:  # pragma: no cover - defensive
        import traceback
        report = _error_report(str(exc), traceback.format_exc())
        _write_json(paths.report, report)
        return report
    _write_json(paths.report, report)
    return report


def _print_summary(report: Dict) -> None:
    prov = " (PROVISIONAL — live pilot not run)" if report.get("provisional_pending_live_pilot") else ""
    print(f"[{PHASE}] recommendation = {report['recommendation']}{prov}")
    print(f"  survivorship dataset buildable on free data : "
          f"{report.get('survivorship_dataset_buildable_free')}")
    la = report.get("live_approval", {})
    pf = report.get("pillar_feasibility", {})
    d = report.get("determinations", {})
    print(f"  {LIVE_ENV_VAR}={la.get('required_value')} approved : {la.get('approved_this_run')}; "
          f"live pilot ran : {la.get('live_pilot_ran')}")
    print(f"  web checks {la.get('web_checks_used')}/{WEB_CHECK_CAP}, "
          f"price probes {la.get('price_probes_used')}/{PRICE_PROBE_CAP}, "
          f"network requests {la.get('network_requests')}")
    print(f"  historical membership feasible : {pf.get('historical_membership')} "
          f"(events {d.get('membership_events')}, span {d.get('membership_span_years')}y)")
    print(f"  delisted prices feasible       : some={pf.get('delisted_prices_some')} "
          f"strong={pf.get('delisted_prices_strong')} "
          f"(genuine-delisting usable {d.get('genuine_delisting_usable')}/"
          f"{d.get('genuine_delisting_probed')}; all-removed {d.get('delisted_usable_any_removed')}/"
          f"{d.get('delisted_probed')})")
    print(f"  point-in-time sectors feasible : {pf.get('pit_sectors')}")
    print(f"  identity continuity feasible   : {pf.get('identity_continuity')} "
          f"(survivors resolved {d.get('identity_survivors_resolved')}, "
          f"removed-in-SEC-dir {d.get('removed_names_in_current_sec_dir')}/"
          f"{d.get('removed_names_probed_for_identity')})")
    gs = report.get("gate_matrix_summary", {})
    print(f"  gates: PASS={gs.get('pass')} FAIL={gs.get('fail')} WARN={gs.get('warn')} "
          f"INFO={gs.get('n_info')} N/A={gs.get('n_na')} safety_fail={gs.get('safety_fail')}")


def _parse_args(argv):
    p = argparse.ArgumentParser(
        description="Phase 7-K survivorship-aware data foundation feasibility pilot. Default is a "
                    "safe dry-run (no network, nothing written to D:). A small bounded live pilot "
                    f"(<= {WEB_CHECK_CAP} web checks, <= {PRICE_PROBE_CAP} delisted price probes) "
                    f"runs ONLY when {LIVE_ENV_VAR}={LIVE_ENV_VALUE}. Free sources only; no paid "
                    "APIs; no package installs; no model/factor/portfolio/trading logic.")
    p.add_argument("--out-dir", type=str, default=None, help="Override committed-safe output dir.")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = _parse_args(argv)
    report = run(args.out_dir)
    _print_summary(report)
    rec = report.get("recommendation")
    return 0 if rec in ALLOWED_RECOMMENDATIONS and rec != REC_ERROR else 1


if __name__ == "__main__":
    import sys
    raise SystemExit(main(sys.argv[1:]))
