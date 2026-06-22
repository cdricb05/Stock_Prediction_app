"""Phase 5-E1D - SimFin Free Live Smoke Test (dry-run by default; opt-in --live).

Track A (quant brain). Phase 5-E1C selected SimFin as the preferred low-cost
fundamentals provider (free tier + optional monthly SimFin+, no annual lock-in).
The user has created a FREE SimFin account and holds a free-tier API key. SimFin's
own page states the FREE datasets contain ~5 years of history and are delayed by
~12 months - acceptable for research / backtesting, NOT for live production signals.

This phase is a SMOKE TEST, not a collector and not a model. It answers one
question before we build anything: *can SimFin Free actually deliver the quarterly
fundamentals we need, for both standard companies and banks?*

WHY THIS WAS CORRECTED (5-E1D v2)
---------------------------------
The first live attempt used a CUSTOM low-level per-ticker web request (one HTTPS
request per ticker per statement against the SimFin v3 web endpoints). It
authenticated and the Companies/general lookup worked, but every financial-statement
lookup (income, balance, cash flow, derived, and bank variants) returned HTTP 500 /
429. That does NOT prove SimFin Free lacks quarterly fundamentals - it proves the
per-ticker web path is the wrong access path. SimFin documents the official Python
package / bulk dataset download as the preferred mechanism: download a whole dataset
ONCE, then filter the tickers of interest locally. This runner now uses that bulk
path. The deprecated per-ticker web path has been removed from this module.

Two modes
---------
DRY-RUN (default; no key, no package load, no network):
    python research/run_phase5e1d_simfin_free_smoke_test.py
  - requires NO API key
  - loads NO dataset (no network, no package import)
  - writes only committed-safe planning artifacts under research/output/...
  - recommends READY_FOR_SIMFIN_FREE_LIVE_SMOKE

LIVE smoke (opt-in; requires SIMFIN_API_KEY in the environment AND the simfin package):
    python research/run_phase5e1d_simfin_free_smoke_test.py --live --max-tickers 10
  - requires SIMFIN_API_KEY (read from the environment ONLY; never printed/written)
  - requires the official ``simfin`` PyPI package (NEVER auto-installed)
  - downloads each bulk dataset ONCE (market='us', variant='quarterly'), then filters
    the 10 test tickers LOCALLY - never one request per ticker
  - writes the simfin cache + filtered raw/normalized probes ONLY under
    research/data/simfin/ (git-ignored)
  - writes ONLY summary / coverage / schema reports under research/output/...
  - never commits raw or normalized data; never prints / writes the key

Package policy
--------------
The live path REQUIRES the official ``simfin`` package. If it is missing this runner
does NOT install it and does NOT attempt any download: it records
BLOCKED_NEEDS_SIMFIN_PACKAGE plus the exact install command and exits cleanly (no
Python stack trace).

Hard rules honored: SIMFIN_API_KEY read from the environment ONLY (never printed,
never written to any artifact, passed only to simfin.set_api_key()); no paid SimFin
subscription; no FMP; no writes to D:; no Paper Trader / GCP / deploy changes; no
orders / broker execution / automation; no binary model artifacts; no package auto
install; no commit; no push. The simfin cache + raw + normalized data live OUTSIDE
git under research/data/simfin/.
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

PHASE = "5-E1D"
PROVIDER = "SimFin"
ENV_KEY = "SIMFIN_API_KEY"

# --------------------------------------------------------------------------- #
# Free-tier facts (from the user's SimFin account page; confirmed by the user).
# --------------------------------------------------------------------------- #
FREE_DATA_DELAY_MONTHS = 12
FREE_HISTORY_YEARS = 5

# --------------------------------------------------------------------------- #
# Paths
#   * Committed, summarized text artifacts only -> research/output/...
#   * simfin cache + raw + normalized data -> research/data/simfin/ (git-ignored)
# --------------------------------------------------------------------------- #
_OUT_DIR = _REPO_ROOT / "research" / "output" / "phase5e1d_simfin_free_smoke_test"
_REPORT_OUT = _OUT_DIR / "phase5e1d_simfin_free_smoke_test.json"
_DATASET_PLAN_OUT = _OUT_DIR / "simfin_dataset_access_plan.csv"
_REQUEST_PLAN_OUT = _OUT_DIR / "simfin_smoke_request_plan.csv"
_SCHEMA_PROBE_OUT = _OUT_DIR / "simfin_schema_probe_report.csv"
_COVERAGE_OUT = _OUT_DIR / "simfin_coverage_report.csv"
_PIT_OUT = _OUT_DIR / "simfin_point_in_time_readiness.csv"
_SECRET_AUDIT_OUT = _OUT_DIR / "simfin_secret_safety_audit.csv"
_PACKAGE_PROBE_OUT = _OUT_DIR / "simfin_package_probe_report.csv"
_COLLECTOR_PLAN_OUT = _OUT_DIR / "phase5e1e_simfin_collector_plan.json"

_DATA_DIR = _REPO_ROOT / "research" / "data" / "simfin"
_RAW_DIR = _DATA_DIR / "raw"
_NORM_DIR = _DATA_DIR / "normalized"

# Git-ignore body that keeps the simfin cache + every raw / normalized payload OUT of
# git. Mirrors the Phase 5-E1 FMP convention (research/data/fmp/.gitignore): ignore
# raw/ + normalized/ AND everything in the directory except the .gitignore itself.
_GITIGNORE_BODY = (
    "# Phase 5-E1D - LOCAL SimFin data. DO NOT COMMIT.\n"
    "#\n"
    "# research/run_phase5e1d_simfin_free_smoke_test.py --live stores SimFin data\n"
    "# here:\n"
    "#   <simfin bulk cache>  the official package's downloaded dataset cache\n"
    "#   raw/                 filtered raw records for the test tickers\n"
    "#   normalized/          flattened probe CSVs derived from those records\n"
    "#\n"
    "# Even free-tier SimFin data is kept out of git for data hygiene and to match the\n"
    "# FMP convention. Only summarized coverage/schema/safety reports (counts, redacted\n"
    "# URLs, file paths - never payloads or the API key) go to the committed artifacts\n"
    "# under research/output/phase5e1d_simfin_free_smoke_test/.\n"
    "raw/\n"
    "normalized/\n"
    "\n"
    "# Belt-and-braces: ignore everything in this directory EXCEPT this .gitignore,\n"
    "# so no payload or bulk cache can ever be staged from here by accident.\n"
    "*\n"
    "!.gitignore\n"
)

# --------------------------------------------------------------------------- #
# Test tickers - standard companies and banks tracked SEPARATELY in the reports.
# NOTE: we do NOT assume the package exposes separate bank endpoints; we discover
# the actual schema at load time (banks may live in the standard datasets, or the
# package may ship dedicated *_banks loaders). See _run_bulk_smoke.
# --------------------------------------------------------------------------- #
STANDARD_TICKERS: List[Tuple[str, str]] = [
    ("AAPL", "Apple Inc."),
    ("MSFT", "Microsoft Corp."),
    ("AMZN", "Amazon.com Inc."),
    ("NVDA", "NVIDIA Corp."),
    ("APH", "Amphenol Corp."),
    ("ABT", "Abbott Laboratories"),
    ("ACN", "Accenture plc"),
]
BANK_TICKERS: List[Tuple[str, str]] = [
    ("JPM", "JPMorgan Chase & Co."),
    ("BAC", "Bank of America Corp."),
    ("C", "Citigroup Inc."),
]

# Bulk datasets to download once and filter locally. ``loader`` is the official
# simfin function suffix (sf.load_<loader>). ``code`` is the statement family;
# ``template`` is "shared" (companies), "standard", or "banks".
BULK_DATASETS: List[Dict] = [
    {"name": "Companies", "loader": "companies", "code": "general",
     "template": "shared", "required": True, "family": "company_info"},
    # Standard-company statement templates.
    {"name": "Income Statement", "loader": "income", "code": "pl",
     "template": "standard", "required": True, "family": "quarterly_income_statement"},
    {"name": "Balance Sheet", "loader": "balance", "code": "bs",
     "template": "standard", "required": True, "family": "quarterly_balance_sheet"},
    {"name": "Cash Flow", "loader": "cashflow", "code": "cf",
     "template": "standard", "required": True, "family": "quarterly_cash_flow"},
    {"name": "Derived Figures & Ratios", "loader": "derived", "code": "derived",
     "template": "standard", "required": False, "family": "derived_figures_ratios"},
    # Bank statement templates (dedicated loaders if the package ships them).
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

# The three required quarterly statement families (per template) that must be
# verified for the smoke to clear into the collector phase.
_REQUIRED_STATEMENT_CODES = ("pl", "bs", "cf")

# --------------------------------------------------------------------------- #
# Recommendation vocabulary (exactly the six allowed values).
# --------------------------------------------------------------------------- #
REC_READY_LIVE = "READY_FOR_SIMFIN_FREE_LIVE_SMOKE"
REC_READY_E1E = "READY_FOR_PHASE5E1E_SIMFIN_COLLECTOR"
REC_BLOCKED_PKG = "BLOCKED_NEEDS_SIMFIN_PACKAGE"
REC_BLOCKED_NO_QFUND = "BLOCKED_SIMFIN_FREE_NO_QUARTERLY_FUNDAMENTALS"
REC_BLOCKED_NO_KEY = "BLOCKED_MISSING_SIMFIN_KEY"
REC_USE_SEC = "USE_SEC_LOCAL_FALLBACK"
ALLOWED_RECOMMENDATIONS = (
    REC_READY_LIVE, REC_READY_E1E, REC_BLOCKED_PKG,
    REC_BLOCKED_NO_QFUND, REC_BLOCKED_NO_KEY, REC_USE_SEC,
)

# --------------------------------------------------------------------------- #
# Access method: the official SimFin Python package / bulk-download workflow.
# The deprecated per-ticker web API is recorded as historical context only.
# --------------------------------------------------------------------------- #
ACCESS_BULK = "official_simfin_package_or_bulk"
WEB_API_PER_TICKER_DEPRECATED = True
LIVE_WEB_API_RESULT = "rejected_due_500_429"

_DEFAULT_MARKET = "us"
_DEFAULT_VARIANT = "quarterly"

_SIMFIN_PACKAGE = "simfin"
# Verbatim install command for the venv this project runs under (NEVER executed here).
_SIMFIN_INSTALL_CMD = (
    r"C:\Users\binis\paper_trader\.venv-win\Scripts\python.exe -m pip install simfin"
)


# --------------------------------------------------------------------------- #
# Small IO helpers (stdlib only; identical conventions to the 5-E1 / 5-E1C runners).
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
    """Tolerant writer for filtered raw records (may contain non-JSON-native types
    from a DataFrame). Goes ONLY to the git-ignored data tree."""
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


def _ensure_gitignore(data_dir: Path) -> bool:
    """Create (idempotently) the data .gitignore that keeps the simfin cache + raw/ +
    normalized/ out of git. Returns True iff raw/ + normalized/ are ignored. No network."""
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
# Bulk loader. The default adapter uses the official simfin package; tests inject a
# fake loader so no network and no package are required. A loader returns either:
#   {"columns": [...], "records": [...], "full_rows": int}   (records already filtered
#                                                             to want_tickers locally)
# or None when the package has no such loader (e.g. no dedicated *_banks loader).
# --------------------------------------------------------------------------- #
SimfinLoader = Callable[[str, str, str, Optional[Sequence[str]]], Optional[Dict]]


def _default_simfin_loader(loader_name: str, market: str, variant: str,
                           want_tickers: Optional[Sequence[str]],
                           api_key: str, data_dir: Path) -> Optional[Dict]:
    """Download ONE bulk dataset via the official simfin package, then filter the
    requested tickers LOCALLY (in-memory) - never one request per ticker. Imported
    lazily so the module has no import-time package/network surface."""
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


def _probe_records(records: Sequence[Dict], want_set: Set[str]) -> Tuple[List[str], List[Tuple[str, str]]]:
    """Return (sorted test tickers found, sorted distinct quarterly (year, period))."""
    found = sorted({str(r.get("Ticker")) for r in records if r.get("Ticker") in want_set})
    quarters: Set[Tuple[str, str]] = set()
    for r in records:
        if r.get("Ticker") in want_set:
            period = str(r.get("Fiscal Period", "")).strip().upper()
            if period in ("Q1", "Q2", "Q3", "Q4"):
                quarters.add((str(r.get("Fiscal Year", "")), period))
    return found, sorted(quarters)


# --------------------------------------------------------------------------- #
# Planning rows (dry-run AND live both emit these planning artifacts)
# --------------------------------------------------------------------------- #
def _dataset_access_plan_rows(market: str, variant: str) -> List[List]:
    rows = []
    for ds in BULK_DATASETS:
        rows.append([
            ds["name"], ds["template"], ds["code"], ds["family"],
            "required" if ds["required"] else "optional",
            "sf.load_%s" % ds["loader"], market,
            variant if ds["code"] != "general" else "n/a",
            "free_tier_expected",
        ])
    return rows


def _selected_tickers(max_tickers: Optional[int]) -> List[Tuple[str, str, str]]:
    """Combined (ticker, name, template) list, standard first then banks, capped by
    max_tickers. Both groups are still represented for the full 10-ticker smoke."""
    combined = [(t, n, "standard") for t, n in STANDARD_TICKERS] + \
               [(t, n, "banks") for t, n in BANK_TICKERS]
    if max_tickers is not None and max_tickers >= 0:
        combined = combined[:max_tickers]
    return combined


def _split_tickers(max_tickers: Optional[int]) -> Tuple[List[str], List[str]]:
    sel = _selected_tickers(max_tickers)
    std = [t for t, _n, tmpl in sel if tmpl == "standard"]
    banks = [t for t, _n, tmpl in sel if tmpl == "banks"]
    return std, banks


def _want_for(ds: Dict, std: Sequence[str], banks: Sequence[str]) -> List[str]:
    if ds["code"] == "general":
        return sorted(set(std) | set(banks))
    return list(std) if ds["template"] == "standard" else list(banks)


def _bulk_load_plan_rows(market: str, variant: str, max_tickers: Optional[int]) -> List[List]:
    """One row PER DATASET (not per ticker): load once, then filter locally."""
    std, banks = _split_tickers(max_tickers)
    rows = []
    for ds in BULK_DATASETS:
        want = _want_for(ds, std, banks)
        rows.append([
            ds["name"], ds["template"], "sf.load_%s" % ds["loader"], ds["code"],
            ds["family"], market, variant if ds["code"] != "general" else "n/a",
            ";".join(want), "load_once_then_filter_locally",
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
         "Features must be aligned to when the data became available (publish/lag), "
         "not the fiscal period end, to avoid lookahead bias."],
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
         "The key is passed ONLY to the official package's simfin.set_api_key(); it "
         "is never placed in a URL, log line, or artifact."],
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
# Live smoke (ONLY when --live AND the package is present). Downloads each bulk
# dataset ONCE, filters the test tickers locally, writes git-ignored raw + normalized
# probes; summaries only go to research/output/.
# --------------------------------------------------------------------------- #
def _run_bulk_smoke(loader: SimfinLoader, market: str, variant: str,
                    max_tickers: Optional[int], raw_dir: Path, norm_dir: Path,
                    verbose: bool) -> Dict:
    std, banks = _split_tickers(max_tickers)
    std_set, bank_set = set(std), set(banks)
    all_set = std_set | bank_set

    raw_dir.mkdir(parents=True, exist_ok=True)
    norm_dir.mkdir(parents=True, exist_ok=True)

    schema_rows: List[List] = []
    raw_written = 0
    norm_written = 0
    datasets_attempted = 0
    datasets_loaded = 0
    total_full_rows = 0
    max_cols = 0
    all_found: Set[str] = set()
    max_quarters = 0
    datasets_verified_names: List[str] = []
    # found_by[(template, code)] = set of test tickers found WITH quarterly coverage
    found_by: Dict[Tuple[str, str], Set[str]] = {}

    def _persist(template: str, code: str, columns, frecs) -> None:
        nonlocal raw_written, norm_written
        _write_raw_json(raw_dir / template / ("%s.json" % code),
                        {"columns": columns, "records": frecs})
        raw_written += 1
        _write_csv(norm_dir / template / ("%s.csv" % code),
                   ["template", "statement_code", "ticker", "fiscal_year", "fiscal_period"],
                   [[template, code, r.get("Ticker"), r.get("Fiscal Year"),
                     r.get("Fiscal Period")] for r in frecs])
        norm_written += 1

    def _load_one(loader_name: str, template: str, code: str, ds_name: str,
                  required: bool, want: Sequence[str]) -> str:
        """Load one dataset, probe + persist, append a schema row. Returns status."""
        nonlocal datasets_attempted, datasets_loaded, total_full_rows, max_cols, max_quarters
        datasets_attempted += 1
        want_set = set(want) if code != "general" else all_set
        status, cols, full_rows, err = "error", 0, 0, ""
        found: List[str] = []
        quarters: List[Tuple[str, str]] = []
        try:
            result = loader(loader_name, market, variant, want)
            if result is None:
                status = "missing_loader"
            else:
                recs = result.get("records") or []
                cols = len(result.get("columns") or [])
                full_rows = int(result.get("full_rows") or len(recs))
                frecs = [r for r in recs
                         if code == "general" or r.get("Ticker") in want_set]
                found, quarters = _probe_records(frecs, want_set)
                if frecs:
                    status = "loaded"
                    datasets_loaded += 1
                    total_full_rows += full_rows
                    max_cols = max(max_cols, cols)
                    all_found.update(found)
                    if code in _REQUIRED_STATEMENT_CODES:
                        max_quarters = max(max_quarters, len(quarters))
                    _persist(template, code, result.get("columns"), frecs)
                    if code != "general" and quarters:
                        found_by[(template, code)] = set(found)
                        if ds_name not in datasets_verified_names:
                            datasets_verified_names.append(ds_name)
                    elif code == "general":
                        if ds_name not in datasets_verified_names:
                            datasets_verified_names.append(ds_name)
                else:
                    status = "empty"
        except Exception as exc:  # noqa: BLE001 - classify, never crash the smoke
            err = type(exc).__name__
            status = "error"
        schema_rows.append([
            ds_name, template, code, "required" if required else "optional",
            status, full_rows, cols, len(found), ";".join(found),
            len(quarters), variant if code != "general" else "n/a", market, err,
        ])
        if verbose:
            print("  %-7s %-3s sf.load_%-14s -> %-13s rows=%s cols=%s tickers=%s q=%s" % (
                template, code, loader_name, status, full_rows, cols,
                len(found), len(quarters)))
        return status

    # ---- 1) load every declared bulk dataset once ----
    for ds in BULK_DATASETS:
        want = _want_for(ds, std, banks)
        _load_one(ds["loader"], ds["template"], ds["code"], ds["name"],
                  ds["required"], want)

    # ---- 2) determine per-ticker verification (banks tracked separately) ----
    bank_separate_loaded = any(
        found_by.get(("banks", c)) for c in _REQUIRED_STATEMENT_CODES)

    bank_template_separate: Optional[bool] = None
    if bank_set:
        bank_template_separate = bool(bank_separate_loaded)

    # Fallback: if no dedicated bank statement data loaded, banks may live in the
    # STANDARD datasets. Re-load standard loaders filtered to the bank tickers and
    # record that the package does NOT expose separate bank endpoints.
    if bank_set and not bank_separate_loaded:
        any_bank_in_standard = False
        for code in _REQUIRED_STATEMENT_CODES:
            status = _load_one(_STD_LOADER_FOR_CODE[code], "banks_via_standard", code,
                               "%s (banks via standard template)" % code.upper(),
                               True, list(banks))
            if found_by.get(("banks_via_standard", code)):
                any_bank_in_standard = True
        if any_bank_in_standard:
            bank_template_separate = False

    def _found(template: str, code: str) -> Set[str]:
        return found_by.get((template, code), set())

    verified_required: Dict[str, Set[str]] = {"standard": set(), "banks": set()}
    for tk in std:
        if all(tk in _found("standard", c) for c in _REQUIRED_STATEMENT_CODES):
            verified_required["standard"].add(tk)
    for tk in banks:
        sep_ok = all(tk in _found("banks", c) for c in _REQUIRED_STATEMENT_CODES)
        std_ok = all(tk in _found("banks_via_standard", c) for c in _REQUIRED_STATEMENT_CODES)
        if sep_ok or std_ok:
            verified_required["banks"].add(tk)

    # ---- 3) per-ticker coverage rows ----
    coverage_rows: List[List] = []
    for ticker, _name, template in _selected_tickers(max_tickers):
        verified_codes = [c for c in _REQUIRED_STATEMENT_CODES
                          if ticker in _found(template, c)
                          or (template == "banks" and ticker in _found("banks_via_standard", c))]
        n_req = len(_REQUIRED_STATEMENT_CODES)
        if len(verified_codes) == n_req:
            cov = "quarterly_fundamentals_verified"
        elif verified_codes:
            cov = "partial"
        else:
            cov = "no_access"
        coverage_rows.append([
            ticker, template, n_req, len(verified_codes), ",".join(verified_codes), cov,
        ])

    package_probe = {
        "datasets_attempted": datasets_attempted,
        "datasets_loaded": datasets_loaded,
        "rows_loaded": total_full_rows,
        "columns_detected": max_cols,
        "test_tickers_found": len(all_found),
        "quarterly_dates_found": max_quarters,
    }

    return {
        "schema_rows": schema_rows,
        "coverage_rows": coverage_rows,
        "raw_written": raw_written,
        "norm_written": norm_written,
        "verified_required": verified_required,
        "datasets_verified_names": datasets_verified_names,
        "bank_template_separate": bank_template_separate,
        "package_probe": package_probe,
    }


# --------------------------------------------------------------------------- #
# Dry-run planning rows (no load)
# --------------------------------------------------------------------------- #
def _planned_schema_rows(market: str, variant: str, max_tickers: Optional[int]) -> List[List]:
    rows = []
    for ds in BULK_DATASETS:
        rows.append([
            ds["name"], ds["template"], ds["code"],
            "required" if ds["required"] else "optional",
            "planned", 0, 0, 0, "", 0,
            variant if ds["code"] != "general" else "n/a", market, "",
        ])
    return rows


def _planned_coverage_rows(max_tickers: Optional[int]) -> List[List]:
    rows = []
    for ticker, _name, template in _selected_tickers(max_tickers):
        rows.append([ticker, template, len(_REQUIRED_STATEMENT_CODES), 0, "", "planned"])
    return rows


# --------------------------------------------------------------------------- #
# Recommendation
# --------------------------------------------------------------------------- #
def _derive_recommendation(live: bool, api_key_present: bool, package_present: bool,
                           live_results: Optional[Dict]) -> Dict:
    if not live:
        return {"recommendation": REC_READY_LIVE,
                "recommended_next_phase": "Run the live smoke: --live --max-tickers 10 "
                                          "(needs SIMFIN_API_KEY and the simfin package).",
                "reason": "Dry-run planning complete; the bulk-download live smoke is ready "
                          "to run once SIMFIN_API_KEY is set and the simfin package is installed."}
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
    vr = (live_results or {}).get("verified_required", {"standard": set(), "banks": set()})
    std_ok = len(vr.get("standard", set())) > 0
    bank_ok = len(vr.get("banks", set())) > 0
    any_quarterly = std_ok or bank_ok
    if std_ok and bank_ok:
        return {"recommendation": REC_READY_E1E,
                "recommended_next_phase": "Phase 5-E1E - build the bounded SimFin Free collector "
                                          "(see phase5e1e_simfin_collector_plan.json).",
                "reason": "Quarterly income/balance/cash-flow verified on the free tier via the "
                          "official bulk download for both a standard company and a bank."}
    if not any_quarterly:
        return {"recommendation": REC_BLOCKED_NO_QFUND,
                "recommended_next_phase": "Fall back to USE_SEC_LOCAL_FALLBACK (phase3g/3h SEC "
                                          "pipeline) or reconsider a monthly tier.",
                "reason": "The bulk download returned no quarterly fundamentals for the required "
                          "statements; SimFin Free is insufficient as-is."}
    return {"recommendation": REC_BLOCKED_NO_QFUND,
            "recommended_next_phase": "Bulk download only partially covered quarterly fundamentals "
                                      "(standard_ok=%s, bank_ok=%s); confirm template coverage "
                                      "before building the collector." % (std_ok, bank_ok),
            "reason": "Quarterly fundamentals were not verified for BOTH the standard and bank "
                      "templates from the bulk download."}


def _collector_plan(market: str, variant: str) -> Dict:
    """The Phase 5-E1E collector design this smoke would unlock (committed-safe, no data)."""
    return {
        "phase": "5-E1E",
        "title": "SimFin Free fundamentals collector (planned, not yet built)",
        "provider": PROVIDER,
        "gated_on": "Phase 5-E1D live smoke returning %s." % REC_READY_E1E,
        "access_method": ACCESS_BULK,
        "package_required": True,
        "package_install_command": _SIMFIN_INSTALL_CMD,
        "market": market,
        "variant": variant,
        "auth": "simfin.set_api_key(); %s env var only" % ENV_KEY,
        "free_tier": True,
        "free_data_delay_months": FREE_DATA_DELAY_MONTHS,
        "free_history_years": FREE_HISTORY_YEARS,
        "datasets_standard": [d["name"] for d in BULK_DATASETS if d["template"] in ("shared", "standard")],
        "datasets_banks": [d["name"] for d in BULK_DATASETS if d["template"] == "banks"],
        "tickers_standard": [t for t, _ in STANDARD_TICKERS],
        "tickers_banks": [t for t, _ in BANK_TICKERS],
        "load_strategy": "Download each bulk dataset ONCE (market+variant), then filter "
                         "the universe locally - never one request per ticker.",
        "storage": {
            "data_dir": _rel(_DATA_DIR), "raw_dir": _rel(_RAW_DIR),
            "normalized_dir": _rel(_NORM_DIR), "gitignored": True,
            "committed_summaries_dir": _rel(_OUT_DIR),
        },
        "point_in_time_rule": "Align each fundamental to its data-availability date "
                              "(publish + free-tier lag), never to the fiscal period end.",
        "usable_for_live_trading_today": False,
        "open_questions": [
            "Confirm SimFin free-tier dataset coverage breadth and refresh cadence.",
            "Confirm whether banks ship as dedicated *_banks datasets or within the "
            "standard datasets (the smoke records the actual discovered schema).",
            "Decide refresh cadence (quarterly) given the 12-month free-tier delay.",
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
def run(live: bool = False, max_tickers: int = 10, access_method: str = ACCESS_BULK,
        market: str = _DEFAULT_MARKET, variant: str = _DEFAULT_VARIANT,
        out_dir: Optional[Path] = None, data_dir: Optional[Path] = None,
        api_key: Optional[str] = None,
        simfin_loader: Optional[SimfinLoader] = None,
        package_available: Optional[bool] = None,
        verbose: bool = True) -> Dict:
    """Run the SimFin Free smoke test via the official package / bulk-download workflow.

    dry-run (live=False): NO key, NO package load, NO network; writes only committed-safe
    planning artifacts. live=True: requires a SimFin key AND the simfin package, downloads
    each bulk dataset once, filters the test tickers locally, writes the cache/raw/normalized
    under the git-ignored data dir and summaries under research/output.

    Injectable for tests: out_dir / data_dir (temp folders), api_key, package_available
    (force package presence without installing), simfin_loader (fake bulk loader; no
    network, no package)."""
    out_dir = Path(out_dir) if out_dir is not None else _OUT_DIR
    data_dir = Path(data_dir) if data_dir is not None else _DATA_DIR
    raw_dir = data_dir / "raw"
    norm_dir = data_dir / "normalized"

    # Key: read from the environment ONLY (never logged / written). Only its PRESENCE
    # is ever recorded.
    resolved_key = api_key if api_key is not None else os.environ.get(ENV_KEY)
    api_key_present = bool(resolved_key)

    gitignore_ok = _ensure_gitignore(data_dir)

    # Package presence: detected via find_spec (no import, no install); injectable for tests.
    package_present = (package_available if package_available is not None
                       else importlib.util.find_spec(_SIMFIN_PACKAGE) is not None)
    pkg_version = _package_version() if package_present else ""

    mode = "live_smoke" if live else "dry_run"

    # ----- live smoke (only when fully unblocked: key + package both present) -----
    live_results: Optional[Dict] = None
    if live and api_key_present and package_present:
        loader = simfin_loader or _make_default_loader(resolved_key, data_dir)
        live_results = _run_bulk_smoke(loader, market, variant, max_tickers,
                                       raw_dir, norm_dir, verbose)

    package_blocked = bool(live and api_key_present and not package_present)
    rec = _derive_recommendation(live, api_key_present, package_present, live_results)

    # ----- committed-safe planning / summary artifacts -----
    _write_csv(out_dir / _DATASET_PLAN_OUT.name,
               ["dataset_name", "company_template", "statement_code", "data_family",
                "required", "loader_function", "market", "variant",
                "free_tier_expectation"],
               _dataset_access_plan_rows(market, variant))

    _write_csv(out_dir / _REQUEST_PLAN_OUT.name,
               ["dataset_name", "company_template", "loader_function", "statement_code",
                "data_family", "market", "variant", "tickers_filtered_locally",
                "load_strategy"],
               _bulk_load_plan_rows(market, variant, max_tickers))

    schema_rows = (live_results or {}).get("schema_rows") if live_results else None
    if schema_rows is None:
        schema_rows = _planned_schema_rows(market, variant, max_tickers)
    _write_csv(out_dir / _SCHEMA_PROBE_OUT.name,
               ["dataset_name", "company_template", "statement_code", "required",
                "load_status", "full_rows_loaded", "columns_detected",
                "test_tickers_found", "test_tickers", "quarterly_dates_found",
                "variant", "market", "error_type"],
               schema_rows)

    coverage_rows = (live_results or {}).get("coverage_rows") if live_results else None
    if coverage_rows is None:
        coverage_rows = _planned_coverage_rows(max_tickers)
    _write_csv(out_dir / _COVERAGE_OUT.name,
               ["ticker", "company_template", "required_statement_codes",
                "verified_statement_codes", "verified_codes", "coverage_status"],
               coverage_rows)

    _write_csv(out_dir / _PIT_OUT.name,
               ["aspect", "value", "safe_for_research_backtest",
                "safe_for_live_trading_today", "note"],
               _pit_readiness_rows())

    _write_csv(out_dir / _SECRET_AUDIT_OUT.name,
               ["check", "expected", "status", "detail"],
               _secret_safety_audit_rows(api_key_present, gitignore_ok, mode))

    # New artifact: package probe report (single summary row).
    pp = (live_results or {}).get("package_probe", {})
    _write_csv(out_dir / _PACKAGE_PROBE_OUT.name,
               ["package_present", "package_version", "access_method",
                "datasets_attempted", "datasets_loaded", "rows_loaded",
                "columns_detected", "test_tickers_found", "quarterly_dates_found",
                "recommendation"],
               [[package_present, pkg_version, access_method,
                 pp.get("datasets_attempted", 0), pp.get("datasets_loaded", 0),
                 pp.get("rows_loaded", 0), pp.get("columns_detected", 0),
                 pp.get("test_tickers_found", 0), pp.get("quarterly_dates_found", 0),
                 rec["recommendation"]]])

    _write_json(out_dir / _COLLECTOR_PLAN_OUT.name, _collector_plan(market, variant))

    # ----- main report -----
    datasets_planned = [d["name"] for d in BULK_DATASETS]
    datasets_verified = (live_results or {}).get("datasets_verified_names", []) if live_results else []
    vr = (live_results or {}).get("verified_required", {"standard": set(), "banks": set()})
    schema_verified = bool(vr.get("standard")) or bool(vr.get("banks"))

    report = {
        "phase": PHASE,
        "provider": PROVIDER,
        "mode": mode,
        "access_method": access_method,
        "package_present": package_present,
        "package_required": True,
        "package_version": pkg_version,
        "package_install_command": _SIMFIN_INSTALL_CMD,
        "package_blocked": package_blocked,
        "web_api_per_ticker_deprecated": WEB_API_PER_TICKER_DEPRECATED,
        "live_web_api_result": LIVE_WEB_API_RESULT,
        "market": market,
        "variant": variant,
        "api_key_present": api_key_present,
        "api_key_logged": False,
        "free_tier": True,
        "free_data_delay_months": FREE_DATA_DELAY_MONTHS,
        "free_history_years": FREE_HISTORY_YEARS,
        "datasets_planned": datasets_planned,
        "datasets_verified": datasets_verified,
        "standard_tickers_tested": [t for t, _ in STANDARD_TICKERS],
        "bank_tickers_tested": [t for t, _ in BANK_TICKERS],
        "bank_template_separate": (live_results or {}).get("bank_template_separate"),
        "raw_files_written_count": (live_results or {}).get("raw_written", 0),
        "normalized_files_written_count": (live_results or {}).get("norm_written", 0),
        "schema_verified": schema_verified,
        "point_in_time_safe_for_research": True,
        "usable_for_live_trading_today": False,
        "recommendation": rec["recommendation"],
        "recommended_next_phase": rec["recommended_next_phase"],
        "recommendation_reason": rec["reason"],
        "allowed_recommendations": list(ALLOWED_RECOMMENDATIONS),
        "network_used": bool(live_results),
        "raw_normalized_gitignored": gitignore_ok,
        "data_dir": _rel(data_dir),
        "next_command_dry_run": "python research\\run_phase5e1d_simfin_free_smoke_test.py",
        "next_command_live": "python research\\run_phase5e1d_simfin_free_smoke_test.py --live --max-tickers 10",
        "artifacts": {
            "report_json": _rel(out_dir / _REPORT_OUT.name),
            "dataset_access_plan_csv": _rel(out_dir / _DATASET_PLAN_OUT.name),
            "smoke_request_plan_csv": _rel(out_dir / _REQUEST_PLAN_OUT.name),
            "schema_probe_report_csv": _rel(out_dir / _SCHEMA_PROBE_OUT.name),
            "coverage_report_csv": _rel(out_dir / _COVERAGE_OUT.name),
            "point_in_time_readiness_csv": _rel(out_dir / _PIT_OUT.name),
            "secret_safety_audit_csv": _rel(out_dir / _SECRET_AUDIT_OUT.name),
            "package_probe_report_csv": _rel(out_dir / _PACKAGE_PROBE_OUT.name),
            "collector_plan_json": _rel(out_dir / _COLLECTOR_PLAN_OUT.name),
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
        "builds_collector": False,
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
    print("deprecated web api per-ticker: %s (live_web_api_result=%s)" % (
        report["web_api_per_ticker_deprecated"], report["live_web_api_result"]))
    print("market / variant:              %s / %s" % (report["market"], report["variant"]))
    print("free tier:                     delay=%sm, history=%sy" % (
        report["free_data_delay_months"], report["free_history_years"]))
    print("datasets planned:              %s" % ", ".join(report["datasets_planned"]))
    print("standard tickers:              %s" % ", ".join(report["standard_tickers_tested"]))
    print("bank tickers:                  %s" % ", ".join(report["bank_tickers_tested"]))
    print("bank template separate:        %s" % report["bank_template_separate"])
    print("datasets verified:             %s" % (", ".join(report["datasets_verified"]) or "(none - dry-run)"))
    print("raw / normalized files:        %s / %s" % (
        report["raw_files_written_count"], report["normalized_files_written_count"]))
    print("schema verified:               %s" % report["schema_verified"])
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
        print("  - %-28s %s" % (name, rel))


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        description="Phase 5-E1D SimFin Free live smoke test (dry-run by default; "
                    "official simfin package / bulk-download workflow).")
    ap.add_argument("--live", action="store_true",
                    help="Download bulk datasets and verify (requires %s and the "
                         "simfin package)." % ENV_KEY)
    ap.add_argument("--max-tickers", type=int, default=10,
                    help="Cap the number of tickers verified after the local filter "
                         "(default 10).")
    ap.add_argument("--access-method", choices=[ACCESS_BULK], default=ACCESS_BULK,
                    help="Only the official simfin package / bulk download is "
                         "supported (the per-ticker web API is deprecated).")
    ap.add_argument("--market", type=str, default=_DEFAULT_MARKET,
                    help="SimFin market (default %s)." % _DEFAULT_MARKET)
    ap.add_argument("--variant", type=str, default=_DEFAULT_VARIANT,
                    help="SimFin statement variant (default %s)." % _DEFAULT_VARIANT)
    return ap.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parse_args(argv)
    run(live=args.live, max_tickers=args.max_tickers, access_method=args.access_method,
        market=args.market, variant=args.variant, verbose=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
