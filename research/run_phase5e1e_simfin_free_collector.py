"""Phase 5-E1E - Bounded SimFin Free fundamentals collector (dry-run by default).

Track A (quant brain). Phase 5-E1D's live smoke verified (via the OFFICIAL simfin
package / bulk-download workflow) that SimFin Free returns quarterly income / balance /
cash-flow statements for both standard companies and banks, returning
READY_FOR_PHASE5E1E_SIMFIN_COLLECTOR. This phase builds the bounded, cache-safe
collector that download/loads those quarterly fundamentals ONCE, stores the raw +
normalized data locally under git-ignored research/data/simfin/, and emits committed-safe
coverage / schema / quality reports plus a Phase 5-E2 enriched-model input plan.

This phase does NOT train a model, does NOT deploy anything, and does NOT build features.
It only prepares the SimFin fundamentals dataset for the Phase 5-E2 enriched rerun.

Access method
-------------
The ONLY access method is the official ``simfin`` Python package / bulk download:
each dataset is loaded ONCE (market='us', variant='quarterly'), then the target
universe is filtered LOCALLY (in-memory) - never one request per ticker. There is no
custom per-ticker web API in this module.

Universe
--------
The target universe is the Phase 5-C cross-sectional universe (~128 large-cap names),
read from the committed research/output/phase5c_feature_panel_sample.csv ticker column.
Banks (which use SimFin's bank statement templates) are discovered at load time, not
assumed: a ticker is routed to the bank or standard template by where its quarterly
statements actually appear.

Two modes
---------
DRY-RUN (default; no key, no package load, no network):
    python research/run_phase5e1e_simfin_free_collector.py
  - requires NO API key
  - loads NO dataset (no network, no package import)
  - emits a collection plan + expected dataset map + all committed-safe artifacts
  - recommends READY_FOR_SIMFIN_COLLECTOR_LIVE_RUN

LIVE collection (opt-in; requires SIMFIN_API_KEY AND the simfin package):
    python research/run_phase5e1e_simfin_free_collector.py --live --universe-source phase5c --max-tickers 128
  - requires SIMFIN_API_KEY (read from the environment ONLY; never printed/written)
  - requires the official ``simfin`` PyPI package (NEVER auto-installed)
  - loads each bulk dataset ONCE, filters the universe locally
  - writes raw + normalized local files ONLY under research/data/simfin/ (git-ignored)
  - writes ONLY summary artifacts under research/output/phase5e1e_simfin_free_collector/
  - never commits raw/normalized data; never prints/writes the key

Package policy
--------------
The live path REQUIRES the official ``simfin`` package. If it is missing this runner
does NOT install it and makes NO download: it records BLOCKED_NEEDS_SIMFIN_PACKAGE plus
the exact install command and exits cleanly (no Python stack trace).

Hard rules honored: SIMFIN_API_KEY read from the environment ONLY (never printed, never
written to any artifact, passed only to simfin.set_api_key()); D: is read-only input
only; no FMP; no Paper Trader / GCP / deploy changes; no orders / broker execution /
automation; no binary artifacts; no package auto-install; no commit; no push. The simfin
cache + raw + normalized data live OUTSIDE git under research/data/simfin/.
"""
from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import os
import sys
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Set, Tuple

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

PHASE = "5-E1E"
PROVIDER = "SimFin"
ENV_KEY = "SIMFIN_API_KEY"

# --------------------------------------------------------------------------- #
# Free-tier facts (confirmed by the user's SimFin account page in 5-E1C/5-E1D).
# --------------------------------------------------------------------------- #
FREE_DATA_DELAY_MONTHS = 12
FREE_HISTORY_YEARS = 5

# --------------------------------------------------------------------------- #
# Paths
#   * Committed, summarized text artifacts only -> research/output/...
#   * simfin cache + raw + normalized data -> research/data/simfin/ (git-ignored)
# --------------------------------------------------------------------------- #
_OUT_DIR = _REPO_ROOT / "research" / "output" / "phase5e1e_simfin_free_collector"
_REPORT_OUT = _OUT_DIR / "phase5e1e_simfin_free_collector.json"
_COLLECTION_PLAN_OUT = _OUT_DIR / "simfin_dataset_collection_plan.csv"
_UNIVERSE_COVERAGE_OUT = _OUT_DIR / "simfin_universe_coverage.csv"
_SCHEMA_CATALOG_OUT = _OUT_DIR / "simfin_schema_catalog.csv"
_ROW_COUNTS_OUT = _OUT_DIR / "simfin_statement_row_counts.csv"
_BANK_VS_STD_OUT = _OUT_DIR / "simfin_bank_vs_standard_coverage.csv"
_QUALITY_OUT = _OUT_DIR / "simfin_quality_report.csv"
_PIT_OUT = _OUT_DIR / "simfin_point_in_time_readiness.csv"
_SECRET_AUDIT_OUT = _OUT_DIR / "simfin_secret_safety_audit.csv"
_E2_PLAN_OUT = _OUT_DIR / "phase5e2_enriched_model_input_plan.json"

_DATA_DIR = _REPO_ROOT / "research" / "data" / "simfin"
_RAW_DIR = _DATA_DIR / "raw" / "phase5e1e"
_NORM_DIR = _DATA_DIR / "normalized" / "phase5e1e"

# Committed source of the Phase 5-C universe (distinct ticker column = the ~128 names).
_PHASE5C_UNIVERSE_CSV = _REPO_ROOT / "research" / "output" / "phase5c_feature_panel_sample.csv"

# Fallback universe (the 5-E1D smoke tickers) if the phase5c source is unavailable.
_DEFAULT_UNIVERSE: List[str] = [
    "AAPL", "MSFT", "AMZN", "NVDA", "APH", "ABT", "ACN", "JPM", "BAC", "C",
]

# Git-ignore body shared with 5-E1D (research/data/simfin/.gitignore already exists and
# is committed; _ensure_gitignore only writes when the file is missing). Mirrors the FMP
# convention: ignore raw/ + normalized/ AND everything except the .gitignore itself.
_GITIGNORE_BODY = (
    "# Phase 5-E1 SimFin - LOCAL SimFin data. DO NOT COMMIT.\n"
    "#\n"
    "# The SimFin collectors store data here:\n"
    "#   <simfin bulk cache>  the official package's downloaded dataset cache\n"
    "#   raw/                 filtered raw records for the target universe\n"
    "#   normalized/          flattened statement CSVs derived from those records\n"
    "#\n"
    "# Only summarized coverage/schema/quality/safety reports (counts, paths - never\n"
    "# payloads or the API key) go to the committed research/output/ artifacts.\n"
    "raw/\n"
    "normalized/\n"
    "\n"
    "# Belt-and-braces: ignore everything in this directory EXCEPT this .gitignore.\n"
    "*\n"
    "!.gitignore\n"
)

# --------------------------------------------------------------------------- #
# Bulk datasets to download ONCE and filter locally. ``loader`` is the official
# simfin function suffix (sf.load_<loader>). ``code`` is the statement family;
# ``template`` is "shared" (companies), "standard", or "banks".
# --------------------------------------------------------------------------- #
BULK_DATASETS: List[Dict] = [
    {"name": "Companies", "loader": "companies", "code": "general",
     "template": "shared", "required": True, "family": "company_info"},
    # Standard-company quarterly statement templates.
    {"name": "Income Statement", "loader": "income", "code": "pl",
     "template": "standard", "required": True, "family": "quarterly_income_statement"},
    {"name": "Balance Sheet", "loader": "balance", "code": "bs",
     "template": "standard", "required": True, "family": "quarterly_balance_sheet"},
    {"name": "Cash Flow", "loader": "cashflow", "code": "cf",
     "template": "standard", "required": True, "family": "quarterly_cash_flow"},
    {"name": "Derived Figures & Ratios", "loader": "derived", "code": "derived",
     "template": "standard", "required": False, "family": "derived_figures_ratios"},
    # Bank quarterly statement templates (dedicated loaders if the package ships them).
    {"name": "Income Statement (Banks)", "loader": "income_banks", "code": "pl",
     "template": "banks", "required": True, "family": "quarterly_income_statement_banks"},
    {"name": "Balance Sheet (Banks)", "loader": "balance_banks", "code": "bs",
     "template": "banks", "required": True, "family": "quarterly_balance_sheet_banks"},
    {"name": "Cash Flow (Banks)", "loader": "cashflow_banks", "code": "cf",
     "template": "banks", "required": True, "family": "quarterly_cash_flow_banks"},
    {"name": "Derived Figures & Ratios (Banks)", "loader": "derived_banks", "code": "derived",
     "template": "banks", "required": False, "family": "derived_figures_ratios_banks"},
]

# Standard loaders used for the bank fallback when dedicated bank loaders are absent.
_STD_LOADER_FOR_CODE = {"pl": "income", "bs": "balance", "cf": "cashflow"}
# The three required quarterly statement families per template.
_REQUIRED_STATEMENT_CODES = ("pl", "bs", "cf")

# Coverage gate: fraction of the universe that must have full quarterly IS/BS/CF for the
# collected dataset to be judged ready for the Phase 5-E2 enriched rerun.
_COVERAGE_READY_THRESHOLD = 0.80

# Known deposit-taking banks among the Phase 5-C universe (used ONLY to annotate the
# dry-run plan's expected template; live coverage is discovered, never assumed).
_EXPECTED_BANK_TICKERS = {"JPM", "BAC", "C", "WFC", "USB", "PNC"}

# --------------------------------------------------------------------------- #
# Recommendation vocabulary (exactly the six allowed values).
# --------------------------------------------------------------------------- #
REC_READY_LIVE = "READY_FOR_SIMFIN_COLLECTOR_LIVE_RUN"
REC_READY_E2 = "READY_FOR_PHASE5E2_ENRICHED_MODEL_RERUN"
REC_NEEDS_MORE = "NEEDS_MORE_SIMFIN_COVERAGE"
REC_BLOCKED_NO_KEY = "BLOCKED_MISSING_SIMFIN_KEY"
REC_BLOCKED_PKG = "BLOCKED_NEEDS_SIMFIN_PACKAGE"
REC_USE_SEC = "USE_SEC_LOCAL_FALLBACK"
ALLOWED_RECOMMENDATIONS = (
    REC_READY_LIVE, REC_READY_E2, REC_NEEDS_MORE,
    REC_BLOCKED_NO_KEY, REC_BLOCKED_PKG, REC_USE_SEC,
)

ACCESS_BULK = "official_simfin_package_or_bulk"
_DEFAULT_MARKET = "us"
_DEFAULT_VARIANT = "quarterly"

_SIMFIN_PACKAGE = "simfin"
# Verbatim install command for the venv this project runs under (NEVER executed here).
_SIMFIN_INSTALL_CMD = (
    r"C:\Users\binis\paper_trader\.venv-win\Scripts\python.exe -m pip install simfin"
)


# --------------------------------------------------------------------------- #
# Small IO helpers (stdlib only; identical conventions to the 5-E1D runner).
# --------------------------------------------------------------------------- #
def _rel(path: Path) -> str:
    try:
        return str(Path(path).resolve().relative_to(_REPO_ROOT)).replace("\\", "/")
    except ValueError:
        return str(path).replace("\\", "/")


def _write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=2, allow_nan=False)
        fh.write("\n")


def _write_raw_json(path: Path, obj) -> None:
    """Tolerant writer for filtered raw records (may contain non-JSON-native types).
    Goes ONLY to the git-ignored data tree."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=2, default=str)
        fh.write("\n")


def _write_csv(path: Path, header: Sequence[str], rows: Sequence[Sequence]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        w.writerows(rows)


def _write_full_csv(path: Path, columns: Sequence[str], records: Sequence[Dict]) -> None:
    """Write the full normalized statement table (all discovered columns). Git-ignored."""
    path.parent.mkdir(parents=True, exist_ok=True)
    cols = list(columns) if columns else (list(records[0].keys()) if records else [])
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in records:
            w.writerow({c: r.get(c) for c in cols})


def _ensure_gitignore(data_dir: Path) -> bool:
    """Create (idempotently) the data .gitignore that keeps the cache + raw/ + normalized/
    out of git. Returns True iff raw/ + normalized/ are ignored. No network."""
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    gi = data_dir / ".gitignore"
    if not gi.is_file():
        gi.write_text(_GITIGNORE_BODY, encoding="utf-8")
    text = gi.read_text(encoding="utf-8")
    has_raw = "raw/" in text or "*" in text
    has_norm = "normalized/" in text or "*" in text
    return has_raw and has_norm


def _package_version() -> str:
    """Best-effort simfin version WITHOUT importing the package. Empty if absent."""
    try:
        import importlib.metadata as md
        return md.version(_SIMFIN_PACKAGE)
    except Exception:  # noqa: BLE001 - version is informational only
        return ""


# --------------------------------------------------------------------------- #
# Universe loading (committed, local file read only - no network).
# --------------------------------------------------------------------------- #
def _load_phase5c_universe(csv_path: Path) -> List[str]:
    """Distinct, sorted ticker column from the committed Phase 5-C feature panel."""
    csv_path = Path(csv_path)
    if not csv_path.is_file():
        return []
    tickers: Set[str] = set()
    with open(csv_path, "r", newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            tk = (row.get("ticker") or row.get("Ticker") or "").strip().upper()
            if tk:
                tickers.add(tk)
    return sorted(tickers)


def _resolve_universe(universe_source: str, max_tickers: Optional[int],
                      universe: Optional[Sequence[str]],
                      phase5c_csv: Path) -> Tuple[List[str], str]:
    """Return (universe tickers, resolved source label). Local file read only."""
    if universe is not None:
        tickers = sorted({str(t).strip().upper() for t in universe if str(t).strip()})
        resolved = universe_source or "injected"
    elif universe_source == "phase5c":
        tickers = _load_phase5c_universe(phase5c_csv)
        if tickers:
            resolved = "phase5c"
        else:
            tickers = list(_DEFAULT_UNIVERSE)
            resolved = "default_fallback_phase5c_unavailable"
    else:
        tickers = list(_DEFAULT_UNIVERSE)
        resolved = "default"
    if max_tickers is not None and max_tickers >= 0:
        tickers = tickers[:max_tickers]
    return tickers, resolved


# --------------------------------------------------------------------------- #
# Bulk loader. Default adapter uses the official simfin package; tests inject a fake
# loader so no network and no package are required. A loader returns either
#   {"columns": [...], "records": [...], "full_rows": int}   (records filtered locally)
# or None when the package has no such loader (e.g. no dedicated *_banks loader).
# --------------------------------------------------------------------------- #
SimfinLoader = Callable[[str, str, str, Optional[Sequence[str]]], Optional[Dict]]


def _default_simfin_loader(loader_name: str, market: str, variant: str,
                           want_tickers: Optional[Sequence[str]],
                           api_key: str, data_dir: Path) -> Optional[Dict]:
    """Download ONE bulk dataset via the official simfin package, then filter the
    requested tickers LOCALLY - never one request per ticker. Imported lazily so the
    module has no import-time package / network surface."""
    import simfin as sf  # lazy: only reachable when the package is present and --live
    sf.set_api_key(api_key)
    sf.set_data_dir(str(data_dir))
    fn = getattr(sf, "load_" + loader_name, None)
    if fn is None:
        return None  # package ships no such loader (schema discovery, not an error)
    if loader_name == "companies":
        df = fn(market=market)
    else:
        df = fn(variant=variant, market=market)
    df = df.reset_index()
    full_rows = int(len(df))
    if want_tickers is not None and "Ticker" in df.columns:
        df = df[df["Ticker"].isin(list(want_tickers))]  # LOCAL filter, single load
    columns = [str(c) for c in df.columns]
    records = json.loads(df.to_json(orient="records"))  # JSON-safe records
    return {"columns": columns, "records": records, "full_rows": full_rows}


def _make_default_loader(api_key: str, data_dir: Path) -> SimfinLoader:
    def _loader(loader_name: str, market: str, variant: str,
                want_tickers: Optional[Sequence[str]]) -> Optional[Dict]:
        return _default_simfin_loader(loader_name, market, variant, want_tickers,
                                      api_key, data_dir)
    return _loader


def _probe_records(records: Sequence[Dict], want_set: Set[str]):
    """Return (sorted tickers found, sorted distinct quarterly (year, period),
    min fiscal year, max fiscal year) for the requested tickers."""
    found = sorted({str(r.get("Ticker")) for r in records if r.get("Ticker") in want_set})
    quarters: Set[Tuple[str, str]] = set()
    years: Set[int] = set()
    for r in records:
        if r.get("Ticker") in want_set:
            period = str(r.get("Fiscal Period", "")).strip().upper()
            fy = str(r.get("Fiscal Year", "")).strip()
            if period in ("Q1", "Q2", "Q3", "Q4"):
                quarters.add((fy, period))
            if fy.isdigit():
                years.add(int(fy))
    min_fy = min(years) if years else 0
    max_fy = max(years) if years else 0
    return found, sorted(quarters), min_fy, max_fy


# --------------------------------------------------------------------------- #
# Dry-run planning rows
# --------------------------------------------------------------------------- #
def _collection_plan_rows(universe: Sequence[str], market: str, variant: str) -> List[List]:
    """One row PER DATASET (load once, then filter locally). Bank tickers annotated by
    the expected-template heuristic; live discovery overrides this."""
    bank_expected = sorted(t for t in universe if t in _EXPECTED_BANK_TICKERS)
    std_expected = sorted(t for t in universe if t not in _EXPECTED_BANK_TICKERS)
    rows = []
    for ds in BULK_DATASETS:
        if ds["code"] == "general":
            want_n = len(universe)
        elif ds["template"] == "banks":
            want_n = len(bank_expected)
        else:
            want_n = len(std_expected)
        rows.append([
            ds["name"], ds["template"], "sf.load_%s" % ds["loader"], ds["code"],
            ds["family"], "required" if ds["required"] else "optional",
            market, variant if ds["code"] != "general" else "n/a",
            want_n, "load_once_then_filter_locally",
        ])
    return rows


def _pit_readiness_rows() -> List[List]:
    return [
        ["free_data_delay_months", str(FREE_DATA_DELAY_MONTHS), "yes", "no",
         "Free data is delayed ~12 months: fine for historical research/backtest, "
         "NOT usable for today's live trading signal."],
        ["free_history_years", str(FREE_HISTORY_YEARS), "yes", "n/a",
         "Free tier provides ~5 years of history - enough for a research panel."],
        ["point_in_time_alignment", "align to data-availability date, not fiscal end",
         "yes", "no",
         "Each fundamental must be aligned to when it became available (publish + free "
         "lag), not the fiscal period end, to avoid lookahead bias in 5-E2."],
        ["live_trading_today", "blocked_by_12m_delay", "no", "no",
         "The 12-month delay means free SimFin can never back a live signal for the "
         "current quarter; production would need a paid/lower-latency source."],
    ]


def _secret_safety_audit_rows(api_key_present: bool, gitignore_ok: bool,
                              mode: str) -> List[List]:
    return [
        ["key_source", "%s env var only" % ENV_KEY, "pass",
         "Key is read with os.environ.get(%s); its VALUE is never stored in any "
         "variable that is written out." % ENV_KEY],
        ["key_handling", "simfin.set_api_key() only", "pass",
         "The key is passed ONLY to the official package's simfin.set_api_key(); it is "
         "never placed in a URL, log line, or artifact."],
        ["key_printed_to_stdout", "no", "pass",
         "Only api_key_present (a bool) is ever printed - never the key value."],
        ["key_written_to_artifact", "no", "pass",
         "No artifact field contains the key; only the boolean api_key_present."],
        ["bulk_cache_gitignored", "yes", "pass" if gitignore_ok else "fail",
         "The simfin bulk cache is written under research/data/simfin/, ignored by * ."],
        ["raw_data_gitignored", "yes", "pass" if gitignore_ok else "fail",
         "research/data/simfin/.gitignore ignores raw/ (and * with a .gitignore allow)."],
        ["normalized_data_gitignored", "yes", "pass" if gitignore_ok else "fail",
         "research/data/simfin/.gitignore ignores normalized/."],
        ["raw_normalized_outside_output", "yes", "pass",
         "Cache/raw/normalized live under research/data/simfin/, never under the "
         "committed research/output/ artifacts directory."],
        ["api_key_present_this_run", "informational", "pass",
         "api_key_present=%s, mode=%s." % (api_key_present, mode)],
    ]


# --------------------------------------------------------------------------- #
# Live collection (ONLY when --live AND the package is present). Loads each bulk
# dataset ONCE, filters the universe locally, writes git-ignored raw + normalized
# files; summaries only go to research/output/.
# --------------------------------------------------------------------------- #
def _run_collection(loader: SimfinLoader, universe: Sequence[str], market: str,
                    variant: str, raw_dir: Path, norm_dir: Path,
                    verbose: bool) -> Dict:
    universe_set = set(universe)
    raw_dir.mkdir(parents=True, exist_ok=True)
    norm_dir.mkdir(parents=True, exist_ok=True)

    schema_rows: List[List] = []        # schema catalog (per dataset: columns)
    row_count_rows: List[List] = []     # statement row counts (per dataset)
    quality_rows: List[List] = []       # quality report (per dataset)
    raw_written = 0
    norm_written = 0
    dataset_row_counts: Dict[str, int] = {}
    datasets_loaded: List[str] = []
    # found_by[(template, code)] = set of universe tickers with >=1 quarterly row
    found_by: Dict[Tuple[str, str], Set[str]] = {}
    derived_available = False

    def _persist(template: str, code: str, columns, frecs) -> None:
        nonlocal raw_written, norm_written
        _write_raw_json(raw_dir / template / ("%s.json" % code),
                        {"columns": columns, "records": frecs})
        raw_written += 1
        _write_full_csv(norm_dir / template / ("%s.csv" % code), columns, frecs)
        norm_written += 1

    def _load_one(loader_name: str, template: str, code: str, ds_name: str,
                  required: bool) -> str:
        nonlocal derived_available
        want = sorted(universe_set)
        want_set = universe_set
        status, cols, full_rows, err = "error", 0, 0, ""
        columns: List[str] = []
        found: List[str] = []
        quarters: List[Tuple[str, str]] = []
        min_fy = max_fy = 0
        n_filtered = 0
        try:
            result = loader(loader_name, market, variant, want)
            if result is None:
                status = "missing_loader"
            else:
                recs = result.get("records") or []
                columns = list(result.get("columns") or [])
                cols = len(columns)
                full_rows = int(result.get("full_rows") or len(recs))
                frecs = [r for r in recs
                         if code == "general" or r.get("Ticker") in want_set]
                n_filtered = len(frecs)
                found, quarters, min_fy, max_fy = _probe_records(frecs, want_set)
                if frecs:
                    status = "loaded"
                    if ds_name not in datasets_loaded:
                        datasets_loaded.append(ds_name)
                    dataset_row_counts[ds_name] = n_filtered
                    _persist(template, code, columns, frecs)
                    if code == "derived":
                        derived_available = True
                    elif quarters:
                        found_by[(template, code)] = set(found)
                else:
                    status = "empty"
        except Exception as exc:  # noqa: BLE001 - classify, never crash the collection
            err = type(exc).__name__
            status = "error"

        schema_rows.append([ds_name, template, code, status, cols, "|".join(columns)])
        row_count_rows.append([
            ds_name, template, code, "required" if required else "optional",
            status, full_rows, n_filtered, cols, err,
        ])
        quality_rows.append([
            ds_name, template, code, status, n_filtered, len(found),
            len(quarters), min_fy or "", max_fy or "",
            "ok" if (status == "loaded" and (code in ("general", "derived") or quarters))
            else ("optional_unavailable" if (not required and status in ("missing_loader", "empty", "error"))
                  else status),
        ])
        if verbose:
            print("  %-9s %-3s sf.load_%-14s -> %-14s rows=%s cols=%s tickers=%s q=%s" % (
                template, code, loader_name, status, n_filtered, cols,
                len(found), len(quarters)))
        return status

    # ---- 1) load every declared bulk dataset once ----
    for ds in BULK_DATASETS:
        _load_one(ds["loader"], ds["template"], ds["code"], ds["name"], ds["required"])

    # ---- 2) bank discovery: dedicated *_banks loaders, else standard-template fallback ----
    bank_separate_loaded = any(
        found_by.get(("banks", c)) for c in _REQUIRED_STATEMENT_CODES)
    bank_template_separate: Optional[bool] = None
    if bank_separate_loaded:
        bank_template_separate = True
    elif not bank_separate_loaded:
        # banks may live within the standard datasets; record actual discovered schema.
        bank_in_standard = any(
            (universe_set & _found(found_by, "standard", c))
            and (universe_set & _EXPECTED_BANK_TICKERS & _found(found_by, "standard", c))
            for c in _REQUIRED_STATEMENT_CODES)
        bank_template_separate = False if bank_in_standard else None

    # ---- 3) per-ticker coverage (discovered, not assumed) ----
    def _full_quarterly(template: str, tk: str) -> bool:
        return all(tk in _found(found_by, template, c) for c in _REQUIRED_STATEMENT_CODES)

    standard_covered: Set[str] = set()
    bank_covered: Set[str] = set()
    coverage_rows: List[List] = []
    for tk in sorted(universe_set):
        std_ok = _full_quarterly("standard", tk)
        bank_ok = _full_quarterly("banks", tk)
        if bank_ok:
            resolved_template = "banks"
            bank_covered.add(tk)
        elif std_ok:
            resolved_template = "standard"
            standard_covered.add(tk)
        else:
            resolved_template = "uncovered"
        present_codes = sorted(
            {c for c in _REQUIRED_STATEMENT_CODES
             if tk in _found(found_by, "standard", c) or tk in _found(found_by, "banks", c)})
        if resolved_template == "uncovered" and present_codes:
            cov = "partial"
        elif resolved_template == "uncovered":
            cov = "no_access"
        else:
            cov = "quarterly_fundamentals_verified"
        coverage_rows.append([
            tk, resolved_template, len(_REQUIRED_STATEMENT_CODES),
            len(present_codes), ",".join(present_codes), cov,
        ])

    total_covered = standard_covered | bank_covered

    counts = {
        "datasets_loaded": datasets_loaded,
        "dataset_row_counts": dataset_row_counts,
        "standard_ticker_coverage_count": len(standard_covered),
        "bank_ticker_coverage_count": len(bank_covered),
        "total_ticker_coverage_count": len(total_covered),
        "derived_ratios_available": derived_available,
        "bank_template_separate": bank_template_separate,
    }

    return {
        "schema_rows": schema_rows,
        "row_count_rows": row_count_rows,
        "quality_rows": quality_rows,
        "coverage_rows": coverage_rows,
        "raw_written": raw_written,
        "norm_written": norm_written,
        "standard_covered": sorted(standard_covered),
        "bank_covered": sorted(bank_covered),
        "total_covered": sorted(total_covered),
        "counts": counts,
    }


def _found(found_by: Dict[Tuple[str, str], Set[str]], template: str, code: str) -> Set[str]:
    return found_by.get((template, code), set())


# --------------------------------------------------------------------------- #
# Dry-run planning rows for the artifacts that otherwise come from live results
# --------------------------------------------------------------------------- #
def _planned_schema_rows() -> List[List]:
    return [[ds["name"], ds["template"], ds["code"], "planned", 0, ""]
            for ds in BULK_DATASETS]


def _planned_row_count_rows() -> List[List]:
    return [[ds["name"], ds["template"], ds["code"],
             "required" if ds["required"] else "optional", "planned", 0, 0, 0, ""]
            for ds in BULK_DATASETS]


def _planned_quality_rows() -> List[List]:
    return [[ds["name"], ds["template"], ds["code"], "planned", 0, 0, 0, "", "", "planned"]
            for ds in BULK_DATASETS]


def _planned_coverage_rows(universe: Sequence[str]) -> List[List]:
    bank = lambda t: "banks" if t in _EXPECTED_BANK_TICKERS else "standard"
    return [[tk, "%s_expected" % bank(tk), len(_REQUIRED_STATEMENT_CODES), 0, "", "planned"]
            for tk in sorted(universe)]


def _bank_vs_standard_rows(live_results: Optional[Dict], universe: Sequence[str]) -> List[List]:
    if live_results is None:
        bank_expected = sorted(t for t in universe if t in _EXPECTED_BANK_TICKERS)
        std_expected = sorted(t for t in universe if t not in _EXPECTED_BANK_TICKERS)
        return [
            ["standard", "expected", len(std_expected), 0, ";".join(std_expected)],
            ["banks", "expected", len(bank_expected), 0, ";".join(bank_expected)],
        ]
    std = live_results["standard_covered"]
    bank = live_results["bank_covered"]
    return [
        ["standard", "discovered", len(universe) - len(bank), len(std), ";".join(std)],
        ["banks", "discovered", len(bank), len(bank), ";".join(bank)],
    ]


# --------------------------------------------------------------------------- #
# Recommendation
# --------------------------------------------------------------------------- #
def _derive_recommendation(live: bool, api_key_present: bool, package_present: bool,
                           live_results: Optional[Dict], universe_size: int) -> Dict:
    if not live:
        return {"recommendation": REC_READY_LIVE,
                "recommended_next_phase": "Run the live collection: --live "
                                          "--universe-source phase5c --max-tickers 128 "
                                          "(needs SIMFIN_API_KEY and the simfin package).",
                "reason": "Dry-run collection plan complete; the bulk live collection is "
                          "ready to run once SIMFIN_API_KEY is set and simfin is installed."}
    if not api_key_present:
        return {"recommendation": REC_BLOCKED_NO_KEY,
                "recommended_next_phase": "Set %s in the environment, then re-run --live." % ENV_KEY,
                "reason": "Live mode requires %s; none was found in the environment." % ENV_KEY}
    if not package_present:
        return {"recommendation": REC_BLOCKED_PKG,
                "recommended_next_phase": "Install the simfin package, then re-run --live:\n  %s"
                                          % _SIMFIN_INSTALL_CMD,
                "reason": "The official bulk-download access method requires the '%s' package, "
                          "which is not installed; this runner never installs packages and made "
                          "no download." % _SIMFIN_PACKAGE}
    counts = (live_results or {}).get("counts", {})
    total_cov = counts.get("total_ticker_coverage_count", 0)
    frac = (total_cov / universe_size) if universe_size else 0.0
    if total_cov == 0:
        return {"recommendation": REC_USE_SEC,
                "recommended_next_phase": "Fall back to the free phase3g/3h SEC local "
                                          "fundamentals pipeline (USE_SEC_LOCAL_FALLBACK).",
                "reason": "The bulk download returned no quarterly fundamentals for the "
                          "universe; SimFin Free is insufficient for the enriched rerun."}
    if frac >= _COVERAGE_READY_THRESHOLD:
        return {"recommendation": REC_READY_E2,
                "recommended_next_phase": "Phase 5-E2 - enriched model rerun using the "
                                          "collected SimFin fundamentals (see "
                                          "phase5e2_enriched_model_input_plan.json).",
                "reason": "Quarterly fundamentals collected for %d/%d universe names "
                          "(%.0f%% >= %.0f%% gate) across standard and bank templates."
                          % (total_cov, universe_size, frac * 100,
                             _COVERAGE_READY_THRESHOLD * 100)}
    return {"recommendation": REC_NEEDS_MORE,
            "recommended_next_phase": "Investigate uncovered names (coverage %d/%d, %.0f%% "
                                      "< %.0f%% gate) before the 5-E2 enriched rerun."
                                      % (total_cov, universe_size, frac * 100,
                                         _COVERAGE_READY_THRESHOLD * 100),
            "reason": "SimFin Free quarterly fundamentals covered only %d of %d universe "
                      "names - below the %.0f%% readiness gate." % (
                          total_cov, universe_size, _COVERAGE_READY_THRESHOLD * 100)}


def _enriched_model_input_plan(universe: Sequence[str], market: str, variant: str,
                               live_results: Optional[Dict]) -> Dict:
    """The Phase 5-E2 enriched-model input plan (committed-safe; no data, preview-only)."""
    counts = (live_results or {}).get("counts", {})
    return {
        "phase": "5-E2",
        "title": "Enriched model rerun input plan (planned, not yet built)",
        "provider": PROVIDER,
        "gated_on": "Phase 5-E1E live collection returning %s." % REC_READY_E2,
        "base_panel": "Phase 5-C price/momentum cross-sectional feature panel "
                      "(research/output/phase5c_feature_panel_sample.csv).",
        "fundamentals_source": "SimFin Free quarterly statements collected in 5-E1E "
                               "(git-ignored under research/data/simfin/).",
        "access_method": ACCESS_BULK,
        "market": market,
        "variant": variant,
        "join_keys": ["ticker", "fiscal_period -> data_availability_date"],
        "point_in_time_rule": "Join each fundamental to the price panel by its "
                              "data-availability date (publish + free-tier lag), never "
                              "the fiscal period end, to avoid lookahead bias.",
        "derived_ratios_available": counts.get("derived_ratios_available", False),
        "derived_ratios_required_for_next_phase": False,
        "ratios_can_be_computed_internally": True,
        "planned_feature_families": [
            "valuation (P/E, P/B, EV/EBITDA - computed from statements if derived absent)",
            "profitability (margins, ROE, ROA)",
            "growth (YoY revenue / earnings growth)",
            "leverage / solvency (debt-to-equity, interest coverage)",
            "quality (accruals, cash-conversion)",
        ],
        "bank_handling": "Bank-template names use bank statement line items; ratios for "
                         "banks are computed from the bank templates separately.",
        "universe_size": len(universe),
        "free_data_delay_months": FREE_DATA_DELAY_MONTHS,
        "free_history_years": FREE_HISTORY_YEARS,
        "usable_for_live_trading_today": False,
        "open_questions": [
            "Confirm the publish-lag model for free-tier point-in-time alignment.",
            "Decide internal ratio formulas where SimFin derived figures are unavailable.",
            "Confirm minimum coverage / history per name for inclusion in the rerun.",
        ],
        # Safety contract.
        "preview_only": True,
        "orders_enabled": False,
        "automation_enabled": False,
        "broker_execution_enabled": False,
        "production_replacement": False,
    }


# --------------------------------------------------------------------------- #
# Main run
# --------------------------------------------------------------------------- #
def run(live: bool = False, max_tickers: int = 128, universe_source: str = "phase5c",
        market: str = _DEFAULT_MARKET, variant: str = _DEFAULT_VARIANT,
        out_dir: Optional[Path] = None, data_dir: Optional[Path] = None,
        api_key: Optional[str] = None, universe: Optional[Sequence[str]] = None,
        simfin_loader: Optional[SimfinLoader] = None,
        package_available: Optional[bool] = None,
        phase5c_csv: Optional[Path] = None,
        verbose: bool = True) -> Dict:
    """Run the bounded SimFin Free fundamentals collector.

    dry-run (live=False): NO key, NO package load, NO network; emits the collection
    plan + expected dataset map + all committed-safe artifacts. live=True: requires a
    SimFin key AND the simfin package, loads each bulk dataset once, filters the universe
    locally, writes raw/normalized under the git-ignored data dir and summaries under
    research/output.

    Injectable for tests: out_dir / data_dir (temp folders), api_key, universe (explicit
    ticker list), package_available (force presence without installing), simfin_loader
    (fake bulk loader; no network, no package), phase5c_csv (universe source path)."""
    out_dir = Path(out_dir) if out_dir is not None else _OUT_DIR
    data_dir = Path(data_dir) if data_dir is not None else _DATA_DIR
    raw_dir = data_dir / "raw" / "phase5e1e"
    norm_dir = data_dir / "normalized" / "phase5e1e"
    phase5c_csv = Path(phase5c_csv) if phase5c_csv is not None else _PHASE5C_UNIVERSE_CSV

    universe_list, resolved_source = _resolve_universe(
        universe_source, max_tickers, universe, phase5c_csv)
    universe_size = len(universe_list)

    # Key: read from the environment ONLY (never logged / written). Only its PRESENCE
    # is ever recorded.
    resolved_key = api_key if api_key is not None else os.environ.get(ENV_KEY)
    api_key_present = bool(resolved_key)

    gitignore_ok = _ensure_gitignore(data_dir)

    package_present = (package_available if package_available is not None
                       else importlib.util.find_spec(_SIMFIN_PACKAGE) is not None)
    pkg_version = _package_version() if package_present else ""

    mode = "live_collection" if live else "dry_run"

    # ----- live collection (only when fully unblocked: key + package both present) -----
    live_results: Optional[Dict] = None
    if live and api_key_present and package_present:
        loader = simfin_loader or _make_default_loader(resolved_key, data_dir)
        live_results = _run_collection(loader, universe_list, market, variant,
                                       raw_dir, norm_dir, verbose)

    package_blocked = bool(live and api_key_present and not package_present)
    rec = _derive_recommendation(live, api_key_present, package_present,
                                 live_results, universe_size)

    counts = (live_results or {}).get("counts", {}) if live_results else {}

    # ----- committed-safe artifacts -----
    _write_csv(out_dir / _COLLECTION_PLAN_OUT.name,
               ["dataset_name", "company_template", "loader_function", "statement_code",
                "data_family", "required", "market", "variant",
                "tickers_filtered_locally", "load_strategy"],
               _collection_plan_rows(universe_list, market, variant))

    coverage_rows = (live_results or {}).get("coverage_rows") if live_results \
        else _planned_coverage_rows(universe_list)
    _write_csv(out_dir / _UNIVERSE_COVERAGE_OUT.name,
               ["ticker", "resolved_template", "required_statement_codes",
                "present_statement_codes", "present_codes", "coverage_status"],
               coverage_rows)

    schema_rows = (live_results or {}).get("schema_rows") if live_results \
        else _planned_schema_rows()
    _write_csv(out_dir / _SCHEMA_CATALOG_OUT.name,
               ["dataset_name", "company_template", "statement_code", "load_status",
                "columns_detected", "columns"],
               schema_rows)

    row_count_rows = (live_results or {}).get("row_count_rows") if live_results \
        else _planned_row_count_rows()
    _write_csv(out_dir / _ROW_COUNTS_OUT.name,
               ["dataset_name", "company_template", "statement_code", "required",
                "load_status", "full_rows_loaded", "rows_after_local_filter",
                "columns_detected", "error_type"],
               row_count_rows)

    _write_csv(out_dir / _BANK_VS_STD_OUT.name,
               ["template", "basis", "universe_names", "covered_names", "tickers"],
               _bank_vs_standard_rows(live_results, universe_list))

    quality_rows = (live_results or {}).get("quality_rows") if live_results \
        else _planned_quality_rows()
    _write_csv(out_dir / _QUALITY_OUT.name,
               ["dataset_name", "company_template", "statement_code", "load_status",
                "rows_after_local_filter", "tickers_found", "quarterly_dates_found",
                "min_fiscal_year", "max_fiscal_year", "quality"],
               quality_rows)

    _write_csv(out_dir / _PIT_OUT.name,
               ["aspect", "value", "safe_for_research_backtest",
                "safe_for_live_trading_today", "note"],
               _pit_readiness_rows())

    _write_csv(out_dir / _SECRET_AUDIT_OUT.name,
               ["check", "expected", "status", "detail"],
               _secret_safety_audit_rows(api_key_present, gitignore_ok, mode))

    _write_json(out_dir / _E2_PLAN_OUT.name,
                _enriched_model_input_plan(universe_list, market, variant, live_results))

    # ----- main report -----
    report = {
        "phase": PHASE,
        "provider": PROVIDER,
        "mode": mode,
        "access_method": ACCESS_BULK,
        "package_present": package_present,
        "package_required": True,
        "package_version": pkg_version,
        "package_install_command": _SIMFIN_INSTALL_CMD,
        "package_blocked": package_blocked,
        "market": market,
        "variant": variant,
        "api_key_present": api_key_present,
        "api_key_logged": False,
        "free_tier": True,
        "free_history_years": FREE_HISTORY_YEARS,
        "free_data_delay_months": FREE_DATA_DELAY_MONTHS,
        "usable_for_live_trading_today": False,
        "universe_source": resolved_source,
        "universe_size": universe_size,
        "datasets_planned": [d["name"] for d in BULK_DATASETS],
        "datasets_loaded": counts.get("datasets_loaded", []),
        "dataset_row_counts": counts.get("dataset_row_counts", {}),
        "standard_ticker_coverage_count": counts.get("standard_ticker_coverage_count", 0),
        "bank_ticker_coverage_count": counts.get("bank_ticker_coverage_count", 0),
        "total_ticker_coverage_count": counts.get("total_ticker_coverage_count", 0),
        "bank_template_separate": counts.get("bank_template_separate"),
        "derived_ratios_available": counts.get("derived_ratios_available", False),
        "derived_ratios_required_for_next_phase": False,
        "ratios_can_be_computed_internally": True,
        "point_in_time_safe_for_research": True,
        "raw_files_written_count": (live_results or {}).get("raw_written", 0),
        "normalized_files_written_count": (live_results or {}).get("norm_written", 0),
        "recommendation": rec["recommendation"],
        "recommended_next_phase": rec["recommended_next_phase"],
        "recommendation_reason": rec["reason"],
        "recommended_next_phase_id": "5-E2",
        "allowed_recommendations": list(ALLOWED_RECOMMENDATIONS),
        "network_used": bool(live_results),
        "raw_normalized_gitignored": gitignore_ok,
        "data_dir": _rel(data_dir),
        "next_command_dry_run": "python research\\run_phase5e1e_simfin_free_collector.py",
        "next_command_live": "python research\\run_phase5e1e_simfin_free_collector.py "
                             "--live --universe-source phase5c --max-tickers 128",
        "artifacts": {
            "report_json": _rel(out_dir / _REPORT_OUT.name),
            "dataset_collection_plan_csv": _rel(out_dir / _COLLECTION_PLAN_OUT.name),
            "universe_coverage_csv": _rel(out_dir / _UNIVERSE_COVERAGE_OUT.name),
            "schema_catalog_csv": _rel(out_dir / _SCHEMA_CATALOG_OUT.name),
            "statement_row_counts_csv": _rel(out_dir / _ROW_COUNTS_OUT.name),
            "bank_vs_standard_coverage_csv": _rel(out_dir / _BANK_VS_STD_OUT.name),
            "quality_report_csv": _rel(out_dir / _QUALITY_OUT.name),
            "point_in_time_readiness_csv": _rel(out_dir / _PIT_OUT.name),
            "secret_safety_audit_csv": _rel(out_dir / _SECRET_AUDIT_OUT.name),
            "enriched_model_input_plan_json": _rel(out_dir / _E2_PLAN_OUT.name),
        },
        # Safety contract (identical posture to the rest of Track A).
        "preview_only": True,
        "orders_enabled": False,
        "automation_enabled": False,
        "broker_execution_enabled": False,
        "production_replacement": False,
        "writes_to_d_drive": False,
        "modifies_paper_trader": False,
        "modifies_gcp": False,
        "installs_packages": False,
        "trains_model": False,
        "deploys": False,
    }
    assert report["recommendation"] in ALLOWED_RECOMMENDATIONS
    _write_json(out_dir / _REPORT_OUT.name, report)

    if verbose:
        _print_summary(report)
    return report


def _print_summary(report: Dict) -> None:
    print("phase:                         %s (%s)" % (report["phase"], report["provider"]))
    print("mode:                          %s (network_used=%s, api_key_present=%s)" % (
        report["mode"], report["network_used"], report["api_key_present"]))
    print("access method:                 %s" % report["access_method"])
    print("package present:               %s (version=%s, required=%s)" % (
        report["package_present"], report["package_version"] or "n/a",
        report["package_required"]))
    print("market / variant:              %s / %s" % (report["market"], report["variant"]))
    print("universe:                      %s (source=%s)" % (
        report["universe_size"], report["universe_source"]))
    print("free tier:                     delay=%sm, history=%sy" % (
        report["free_data_delay_months"], report["free_history_years"]))
    print("datasets loaded:               %s" % (", ".join(report["datasets_loaded"]) or "(none - dry-run)"))
    print("coverage std / bank / total:   %s / %s / %s" % (
        report["standard_ticker_coverage_count"], report["bank_ticker_coverage_count"],
        report["total_ticker_coverage_count"]))
    print("bank template separate:        %s" % report["bank_template_separate"])
    print("derived ratios available:      %s (required_next=%s, can_compute=%s)" % (
        report["derived_ratios_available"], report["derived_ratios_required_for_next_phase"],
        report["ratios_can_be_computed_internally"]))
    print("raw / normalized files:        %s / %s" % (
        report["raw_files_written_count"], report["normalized_files_written_count"]))
    print("point-in-time safe (research): %s" % report["point_in_time_safe_for_research"])
    print("usable for live trading today: %s" % report["usable_for_live_trading_today"])
    print("raw/normalized gitignored:     %s" % report["raw_normalized_gitignored"])
    print("")
    print("RECOMMENDATION:                %s" % report["recommendation"])
    print("recommended next phase:        %s" % report["recommended_next_phase"])
    print("reason:                        %s" % report["recommendation_reason"])
    if report["recommendation"] == REC_BLOCKED_PKG:
        print("install command:               %s" % report["package_install_command"])
    print("artifacts:")
    for name, rel in report["artifacts"].items():
        print("  - %-32s %s" % (name, rel))


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        description="Phase 5-E1E bounded SimFin Free fundamentals collector (dry-run by "
                    "default; official simfin package / bulk-download workflow).")
    ap.add_argument("--live", action="store_true",
                    help="Load bulk datasets and collect (requires %s and the simfin "
                         "package)." % ENV_KEY)
    ap.add_argument("--universe-source", choices=["phase5c", "default"], default="phase5c",
                    help="Target universe: phase5c (the ~128-name 5-C universe) or "
                         "default (the 10 smoke tickers).")
    ap.add_argument("--max-tickers", type=int, default=128,
                    help="Cap the universe size after resolution (default 128).")
    ap.add_argument("--market", type=str, default=_DEFAULT_MARKET,
                    help="SimFin market (default %s)." % _DEFAULT_MARKET)
    ap.add_argument("--variant", type=str, default=_DEFAULT_VARIANT,
                    help="SimFin statement variant (default %s)." % _DEFAULT_VARIANT)
    return ap.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parse_args(argv)
    run(live=args.live, max_tickers=args.max_tickers,
        universe_source=args.universe_source, market=args.market,
        variant=args.variant, verbose=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
