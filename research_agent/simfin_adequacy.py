"""Phase 30C.1 — local SimFin adequacy test and free-data coverage ceiling.

Phase 30C established (run ``hcov_026a857c80119147``) that free SEC EDGAR as-filed
backfill RESOLVES the survivorship wall for the acquired subset but COVERAGE stays
the binding constraint (global cross-sectional coverage ~13%). The user already
holds a *free* SimFin account and the SimFin bulk files are already on disk. This
module answers, with actual measurement and zero network calls, whether those
local files materially help.

It is deliberately network-free. There is no HTTP client, no SimFin API key, no
provider endpoint: every input is a local file. The config validator rejects any
URL so a network call is impossible by construction.

Everything numeric/evaluative is REUSED from the committed research agent, never
re-derived:

* ``family_backtest`` — panel loading, month helpers, file hashing, baseline.
* ``owned_factors`` — PIT as-of join, universe/survivorship profiles, the
  per-factor rank-IC diagnostic + decision battery, baseline reproduction.
* ``historical_coverage`` — the canonical Part-A security master (identity +
  CIK/SimFin resolution), the committed SEC as-filed normalizer (reused to
  reconstruct the SEC-only comparison from the existing cache), the
  ``build_repaired_factor_series`` PIT join, the ``_of_cfg`` adapter, and the
  ``_decide_30c`` coverage/survivorship/sector decision gate.
* ``artifact_store`` — atomic writes, append-only ledgers, content hashing,
  secret scanning.
* ``controller`` — git HEAD without a subprocess.

The three target factors reuse the committed Phase 30A/30B/30C definitions
exactly (``gross_profitability = gross_profit/assets``,
``fcf_to_assets = (ocf - capex)/assets``,
``operating_accruals = (net_income - ocf)/assets``); only the *field names* are
mapped onto SimFin's schema. Banks are handled explicitly (excluded — their
statement schema has no cost-of-revenue / no comparable capex).

No order, broker, automation, promotion, challenger, or operational-model change
is possible here.
"""

from __future__ import annotations

import csv
import os
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import SAFETY_CONTRACT
from . import family_backtest as fb
from . import historical_coverage as hc
from . import owned_factors as of
from .artifact_store import (
    append_jsonl,
    content_hash,
    find_secret_keys,
    read_json,
    write_json_atomic,
    write_text_atomic,
)
from .controller import read_git_commit
from .feature_evaluation import DEFAULT_SCREEN_THRESHOLDS
from .schemas import (
    APPROVED_COST_BPS_PER_SIDE,
    APPROVED_EXIT_BUFFER_FRACTIONS,
    APPROVED_MIN_ADV_DOLLARS,
    APPROVED_PORTFOLIO_SIZES,
    APPROVED_UNIVERSES,
    find_forbidden_execution_keys,
)

SIMFIN_ADEQUACY_SCHEMA_VERSION = "30C1.1"

RUNS_SUBDIR = "simfin_adequacy_runs"
LATEST_RUN_FILE = "phase30c1_latest_run.json"

# Part J allowed SimFin decisions and next-data actions.
SIMFIN_DECISIONS = (
    "FREE_SIMFIN_SUFFICIENT",
    "FREE_SIMFIN_INCREMENTAL_BUT_INSUFFICIENT",
    "FREE_SIMFIN_NOT_USABLE",
    "BLOCKED_MISSING_LOCAL_SIMFIN_FILES",
)
NEXT_DATA_ACTIONS = (
    "NO_NEW_DATA_REQUIRED",
    "DOWNLOAD_FREE_SIMFIN_BULK_REFRESH",
    "REQUEST_TARGETED_HISTORICAL_TRIAL",
)

# SimFin bulk statement files that must exist for the adequacy test to run.
_REQUIRED_SIMFIN_KEYS = (
    "simfin_companies",
    "simfin_income",
    "simfin_balance",
    "simfin_cashflow",
)
_BANK_SIMFIN_KEYS = (
    "simfin_income_banks",
    "simfin_balance_banks",
    "simfin_cashflow_banks",
)

# Part B date-semantics classes.
DATE_SEMANTICS_CLASSES = (
    "PIT_SAFE_AS_REPORTED",
    "PIT_SAFE_WITH_RESTATEMENT_CAVEAT",
    "CURRENT_OR_LATEST_ONLY",
    "UNUSABLE",
)

# committed factor field maps onto SimFin's standard-company schema.
_SF_INCOME_FIELDS = {
    "gross_profit": "Gross Profit",
    "revenue": "Revenue",
    "cost_of_revenue": "Cost of Revenue",
    "net_income": "Net Income",
}
_SF_BALANCE_FIELDS = {"assets": "Total Assets"}
_SF_CASHFLOW_FIELDS = {
    "ocf": "Net Cash from Operating Activities",
    "capex_change": "Change in Fixed Assets & Intangibles",
}

_TARGET_FACTORS = ("gross_profitability", "fcf_to_assets", "operating_accruals")

_RESTATEMENT_POLICY = (
    "SimFin bulk carries one row per (SimFinId, fiscal year, fiscal period). "
    "available_date = Publish Date when the row is as-first-reported "
    "(Restated Date absent or <= Publish Date); when Restated Date is LATER than "
    "Publish Date the row holds latest-restated values and is dated at its "
    "Restated Date (a restated statement only becomes knowable on its "
    "restatement date). Original-as-reported and latest-restated observations are "
    "classified and never silently mixed; Fiscal Period End Date is never used as "
    "the availability date; a future filing never enters a prior formation month."
)


class SimfinAdequacyError(RuntimeError):
    pass


# --------------------------------------------------------------------------- #
# small helpers
# --------------------------------------------------------------------------- #
def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _to_float(x: Any) -> Optional[float]:
    return fb._to_float(x)


def _decade_of(month_or_date: Optional[str]) -> Optional[str]:
    if not month_or_date or len(month_or_date) < 4:
        return None
    try:
        y = int(month_or_date[:4])
    except ValueError:
        return None
    return "%ds" % (y - y % 10)


def _resolve(spec: Any) -> str:
    """Resolve a {root, relpath} spec against the committed fixed roots (reuses
    the Phase 30C resolver so a test monkeypatch of ``hc._ROOTS`` is honored)."""
    return hc._resolve_path_spec(spec)


def load_config(path: str) -> Optional[Dict[str, Any]]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            import json
            return json.load(fh)
    except (OSError, ValueError):
        return None


# --------------------------------------------------------------------------- #
# config validation (network-free; gates never weakened)
# --------------------------------------------------------------------------- #
def validate_config(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Structural + safety + non-weakening validation. Never touches the network
    or the filesystem. Any URL is rejected: this phase is local-file only."""
    v: List[Dict[str, Any]] = []

    def bad(field: str, issue: str, value: Any = None) -> None:
        v.append({"field": field, "issue": issue, "value": value})

    if not isinstance(cfg, dict):
        return {"accepted": False, "violations": [{"field": "$", "issue": "config must be an object"}], "config_hash": None}

    if cfg.get("schema_version") != SIMFIN_ADEQUACY_SCHEMA_VERSION:
        bad("schema_version", "must be %s" % SIMFIN_ADEQUACY_SCHEMA_VERSION, cfg.get("schema_version"))
    if not cfg.get("name"):
        bad("name", "required")

    # secrets / forbidden execution keys (Part M #1, #7)
    for k in find_secret_keys(cfg):
        bad(k, "secret-looking key is forbidden in config")
    for k in find_forbidden_execution_keys(cfg):
        bad(k, "forbidden execution-token key")

    # Paper Trader must never be referenced (Part M #5)
    for s in hc._iter_string_values(cfg):
        low = s.lower()
        for token in hc._PAPER_TRADER_FORBIDDEN:
            if token in low:
                bad("$", "Paper Trader path/endpoint reference is forbidden", token)
                break

    # NO network URLs anywhere: this phase is local-file only (Part M #3, #4)
    for s in hc._iter_string_values(cfg):
        low = s.lower()
        if "://" in low or low.startswith("http") or "www." in low:
            bad("$", "network URL is forbidden in a local-file adequacy phase", s)
    for forbidden_key in ("provider_endpoints", "allowed_hosts", "acquisition"):
        if forbidden_key in cfg:
            bad(forbidden_key, "network/acquisition config is forbidden in a local-file phase")

    data = cfg.get("data") or {}
    cutoff = data.get("data_cutoff")
    if not isinstance(cutoff, str) or len(cutoff) != 10:
        bad("data.data_cutoff", "required YYYY-MM-DD", cutoff)

    # roots: only the two fixed roots (Part M #2)
    roots = (cfg.get("sources") or {}).get("roots") or cfg.get("roots") or {}
    for name in roots:
        if name not in hc._ROOTS:
            bad("roots.%s" % name, "only fixed roots {repo, data_root} allowed", name)

    # every declared source path must resolve within a fixed root (Part M #2)
    sources = cfg.get("sources") or {}
    for key, spec in sources.items():
        if key == "roots":
            continue
        if not isinstance(spec, dict) or spec.get("root") not in hc._ROOTS:
            bad("sources.%s" % key, "must be {root in [repo,data_root], relpath}", spec)
        else:
            rel = str(spec.get("relpath", ""))
            if os.path.isabs(rel) or ".." in rel.replace("\\", "/").split("/"):
                bad("sources.%s.relpath" % key, "no absolute paths or .. traversal", rel)

    # required SimFin bulk files must be declared
    for key in _REQUIRED_SIMFIN_KEYS:
        if key not in sources:
            bad("sources.%s" % key, "required local SimFin file spec is missing")

    # entitlement-only rules (no purchase / no sales contact / no key needed)
    ent = cfg.get("entitlement") or {}
    if ent.get("no_purchase") is not True:
        bad("entitlement.no_purchase", "must be true (no data purchase)", ent.get("no_purchase"))
    if ent.get("contact_sales") not in (False, None):
        bad("entitlement.contact_sales", "must be false (no sales contact)", ent.get("contact_sales"))

    # coverage / survivorship / sector gates — never weakened (Part M #6)
    floor = float(DEFAULT_SCREEN_THRESHOLDS["min_coverage_fraction"]["value"])
    cg = cfg.get("coverage_gates") or {}
    for key in ("global_min_cross_sectional_coverage", "global_min_month_coverage"):
        val = cg.get(key)
        if not isinstance(val, (int, float)) or val < floor - 1e-12:
            bad("coverage_gates.%s" % key, "must be >= committed %.2f coverage floor" % floor, val)
    mdr = cg.get("min_delisted_representation_fraction")
    if not isinstance(mdr, (int, float)) or mdr < 0.20 - 1e-12:
        bad("coverage_gates.min_delisted_representation_fraction", "must be >= 0.20 (never weakened)", mdr)

    sh = cfg.get("sector_history") or {}
    smm = sh.get("member_month_coverage_min")
    if not isinstance(smm, (int, float)) or smm < floor - 1e-12:
        bad("sector_history.member_month_coverage_min", "must be >= committed %.2f floor" % floor, smm)

    # ic-screen thresholds may only tighten
    screen = cfg.get("ic_screen") or {}
    for key, direction in of._SCREEN_MIN_DIRECTION.items():
        if key not in screen:
            continue
        val = screen[key]
        b = float(DEFAULT_SCREEN_THRESHOLDS[key]["value"])
        if not isinstance(val, (int, float)):
            bad("ic_screen.%s" % key, "must be numeric", val)
        elif direction == ">=" and val < b - 1e-12:
            bad("ic_screen.%s" % key, "must be >= committed default %s" % b, val)
        elif direction == "<=" and val > b + 1e-12:
            bad("ic_screen.%s" % key, "must be <= committed default %s" % b, val)

    # integration / costs / portfolio (unchanged approved values)
    integ = cfg.get("integration") or {}
    wb, wf = integ.get("baseline_weight"), integ.get("feature_weight")
    if not isinstance(wb, (int, float)) or not isinstance(wf, (int, float)) or abs((wb or 0) + (wf or 0) - 1.0) > 1e-9:
        bad("integration", "baseline_weight + feature_weight must reconcile to 1.0", [wb, wf])
    pc = (cfg.get("costs") or {}).get("primary_cost_bps_per_side")
    if pc is None or not any(abs(pc - a) < 1e-9 for a in APPROVED_COST_BPS_PER_SIDE):
        bad("costs.primary_cost_bps_per_side", "must be one of %s" % (APPROVED_COST_BPS_PER_SIDE,), pc)
    pf = cfg.get("portfolio") or {}
    if pf.get("top_n") not in APPROVED_PORTFOLIO_SIZES:
        bad("portfolio.top_n", "must be one of %s" % (APPROVED_PORTFOLIO_SIZES,), pf.get("top_n"))
    if pf.get("universe") not in APPROVED_UNIVERSES:
        bad("portfolio.universe", "must be one of %s" % (APPROVED_UNIVERSES,), pf.get("universe"))
    eb = pf.get("exit_buffer_fraction")
    if eb is None or not any(abs(eb - a) < 1e-9 for a in APPROVED_EXIT_BUFFER_FRACTIONS):
        bad("portfolio.exit_buffer_fraction", "must be one of %s" % (APPROVED_EXIT_BUFFER_FRACTIONS,), eb)
    if eb not in (0.0, 0):
        bad("portfolio.exit_buffer_fraction", "no exit buffer (0.0) in this phase", eb)
    ma = pf.get("min_adv_dollar")
    if ma is None or not any(abs(ma - a) < 1.0 for a in APPROVED_MIN_ADV_DOLLARS):
        bad("portfolio.min_adv_dollar", "must be one of %s" % (APPROVED_MIN_ADV_DOLLARS,), ma)

    # safety contract present, and no order/broker/automation/promotion path
    safety = cfg.get("safety") or {}
    if safety.get("research_only") is not True:
        bad("safety.research_only", "must be true")
    if safety.get("may_register_challengers") not in (False, None):
        bad("safety.may_register_challengers", "must be false", safety.get("may_register_challengers"))

    return {"accepted": not v, "violations": v, "config_hash": content_hash(cfg)}


def _of_cfg(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Reuse the committed owned_factors config adapter."""
    return hc._of_cfg(cfg)


# --------------------------------------------------------------------------- #
# Part A: local SimFin file inventory
# --------------------------------------------------------------------------- #
def _sniff_delimiter(path: str) -> str:
    with open(path, "r", encoding="utf-8", newline="") as fh:
        head = fh.readline()
    counts = {d: head.count(d) for d in (";", ",", "\t", "|")}
    return max(counts, key=lambda d: counts[d]) if any(counts.values()) else ","


def _inventory_one(path: str, *, is_company: bool, delimiter: str) -> Dict[str, Any]:
    tickers: set = set()
    simfinids: set = set()
    ciks: set = set()
    rep: List[str] = []
    pub: List[str] = []
    res: List[str] = []
    n = 0
    seen_keys: set = set()
    dup_keys = 0
    miss_id = 0
    miss_date = 0
    freq_q = 0
    with open(path, "r", encoding="utf-8", newline="") as fh:
        rd = csv.DictReader(fh, delimiter=delimiter)
        cols = list(rd.fieldnames or [])
        for r in rd:
            n += 1
            tk = (r.get("Ticker") or "").strip()
            sid = (r.get("SimFinId") or "").strip()
            cik = (r.get("CIK") or "").strip()
            if tk:
                tickers.add(tk)
            if sid:
                simfinids.add(sid)
            if cik:
                ciks.add(cik)
            if is_company:
                if not cik:
                    miss_id += 1
                continue
            fy = (r.get("Fiscal Year") or "").strip()
            fp = (r.get("Fiscal Period") or "").strip()
            if fp.upper().startswith("Q"):
                freq_q += 1
            rd_ = (r.get("Report Date") or "").strip()
            pd_ = (r.get("Publish Date") or "").strip()
            rs_ = (r.get("Restated Date") or "").strip()
            if rd_:
                rep.append(rd_)
            if pd_:
                pub.append(pd_)
            if rs_:
                res.append(rs_)
            if not sid:
                miss_id += 1
            if not pd_:
                miss_date += 1
            key = (sid, fy, fp)
            if key in seen_keys:
                dup_keys += 1
            else:
                seen_keys.add(key)
    is_bank = "banks" in os.path.basename(path).lower()
    out = {
        "path": path,
        "exists": True,
        "size_bytes": os.path.getsize(path),
        "modified": datetime.fromtimestamp(os.path.getmtime(path), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "content_hash": fb._file_sha256(path),
        "delimiter": delimiter,
        "encoding": "utf-8",
        "columns": cols,
        "n_columns": len(cols),
        "row_count": n,
        "distinct_simfin_ids": len(simfinids),
        "distinct_tickers": len(tickers),
        "distinct_ciks": len(ciks),
        "schema_kind": "bank" if is_bank else ("company" if is_company else "standard"),
    }
    if not is_company:
        out.update({
            "frequency": "quarterly" if freq_q >= max(1, n) * 0.5 else "mixed",
            "report_date_min": min(rep) if rep else None,
            "report_date_max": max(rep) if rep else None,
            "publish_date_min": min(pub) if pub else None,
            "publish_date_max": max(pub) if pub else None,
            "restated_date_min": min(res) if res else None,
            "restated_date_max": max(res) if res else None,
            "duplicate_key_count": dup_keys,
            "missing_simfin_id_count": miss_id,
            "missing_publish_date_count": miss_date,
            # free-plan trailing-window heuristic: earliest fiscal period end well
            # after the panel start implies a truncated (not full-history) window.
            "appears_truncated_to_trailing_window": bool(rep and (min(rep) or "") >= "2018-01-01"),
        })
    else:
        out["missing_cik_count"] = miss_id
    return out


def inventory_local_files(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Part A: inspect every local SimFin bulk CSV + ZIP archive (read-only)."""
    sources = cfg.get("sources") or {}
    files: Dict[str, Any] = {}
    missing: List[str] = []
    for key in _REQUIRED_SIMFIN_KEYS + _BANK_SIMFIN_KEYS:
        spec = sources.get(key)
        if not spec:
            if key in _REQUIRED_SIMFIN_KEYS:
                missing.append(key)
            continue
        path = _resolve(spec)
        if not os.path.isfile(path):
            files[key] = {"path": path, "exists": False}
            if key in _REQUIRED_SIMFIN_KEYS:
                missing.append(key)
            continue
        delim = _sniff_delimiter(path)
        files[key] = _inventory_one(path, is_company=(key == "simfin_companies"), delimiter=delim)

    # ZIP archives (read-only manifest inspection; never extract over local files)
    zips: List[Dict[str, Any]] = []
    dl_spec = sources.get("simfin_download_dir")
    if dl_spec:
        dl = _resolve(dl_spec)
        if os.path.isdir(dl):
            for name in sorted(os.listdir(dl)):
                if not name.lower().endswith(".zip"):
                    continue
                zp = os.path.join(dl, name)
                try:
                    with zipfile.ZipFile(zp) as zf:
                        entries = [{
                            "filename": i.filename,
                            "file_size": i.file_size,
                            "archive_date": "%04d-%02d-%02d" % i.date_time[:3],
                        } for i in zf.infolist()]
                except (zipfile.BadZipFile, OSError) as exc:
                    entries = [{"error": str(exc)[:100]}]
                zips.append({
                    "archive": name,
                    "size_bytes": os.path.getsize(zp),
                    "modified": datetime.fromtimestamp(os.path.getmtime(zp), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "entries": entries,
                })

    # do the extracted CSVs correspond to the newest local archives?
    extract_vs_archive = []
    for z in zips:
        stem = z["archive"][:-4]
        # match the file key whose basename equals the archive stem
        matched = None
        for key, meta in files.items():
            if isinstance(meta, dict) and meta.get("exists") and os.path.basename(meta["path"])[:-4] == stem:
                matched = (key, meta)
                break
        if matched:
            key, meta = matched
            ent = next((e for e in z["entries"] if e.get("filename", "").endswith(".csv")), {})
            extract_vs_archive.append({
                "archive": z["archive"],
                "csv_key": key,
                "archive_csv_size": ent.get("file_size"),
                "extracted_csv_size": meta.get("size_bytes"),
                "extracted_matches_archive": ent.get("file_size") == meta.get("size_bytes"),
                "archive_date": z["modified"][:10],
                "extracted_date": meta.get("modified", "")[:10],
            })

    return {
        "schema_version": SIMFIN_ADEQUACY_SCHEMA_VERSION,
        "record_type": "LOCAL_FILE_INVENTORY",
        "files": files,
        "zip_archives": zips,
        "extract_vs_archive": extract_vs_archive,
        "missing_required_files": missing,
    }


# --------------------------------------------------------------------------- #
# Part B: SimFin date semantics
# --------------------------------------------------------------------------- #
def classify_date_semantics(inventory: Dict[str, Any]) -> Dict[str, Any]:
    """Part B: classify each statement dataset's point-in-time usability from the
    observed Publish/Restated columns (no re-read: uses the Part-A inventory)."""
    per_file: Dict[str, Any] = {}
    for key, meta in (inventory.get("files") or {}).items():
        if key == "simfin_companies" or not isinstance(meta, dict) or not meta.get("exists"):
            continue
        cols = meta.get("columns") or []
        has_pub = "Publish Date" in cols
        has_res = "Restated Date" in cols
        has_report = "Report Date" in cols
        has_fp = "Fiscal Period" in cols
        # the bulk carries ONE row per (SimFinId, FY, FP) — a single snapshot, not
        # an as-first-reported vintage history. When Publish Date is present and
        # valid we can date the *original* rows PIT; restated rows carry the
        # restatement caveat. There is no separate original-vintage file, so we
        # never claim PIT_SAFE_AS_REPORTED for a mostly-restated dataset.
        if not (has_pub and has_report and has_fp):
            cls = "CURRENT_OR_LATEST_ONLY" if (has_report and has_fp) else "UNUSABLE"
        else:
            cls = "PIT_SAFE_WITH_RESTATEMENT_CAVEAT" if has_res else "PIT_SAFE_AS_REPORTED"
        per_file[key] = {
            "classification": cls,
            "has_publish_date": has_pub,
            "has_restated_date": has_res,
            "has_report_date": has_report,
            "availability_boundary": "Publish Date (original) or later Restated Date (restated)",
            "report_date_window": [meta.get("report_date_min"), meta.get("report_date_max")],
            "publish_date_window": [meta.get("publish_date_min"), meta.get("publish_date_max")],
            "restated_date_window": [meta.get("restated_date_min"), meta.get("restated_date_max")],
        }
    return {
        "schema_version": SIMFIN_ADEQUACY_SCHEMA_VERSION,
        "record_type": "DATE_SEMANTICS_AUDIT",
        "restatement_policy": _RESTATEMENT_POLICY,
        "per_file": per_file,
        "rules": [
            "Publish Date is the availability boundary for original rows.",
            "A Restated Date never REPLACES the original Publish Date.",
            "A restated statement becomes available only on its later restatement date.",
            "Fiscal Period End Date is never used as the availability date.",
            "A future statement is never backward-filled into a prior formation month.",
            "Original-as-reported vs restated is persisted per observation, never mixed.",
        ],
    }


def _availability(publish: str, restated: str) -> Tuple[str, str]:
    """Return (available_date, restatement_class) honouring the PIT policy."""
    pub = (publish or "")[:10]
    res = (restated or "")[:10]
    if res and pub and res > pub:
        return res, "restated"
    if res and not pub:
        return res, "restated"
    return pub, "original_as_reported"


# --------------------------------------------------------------------------- #
# Part C: stable security mapping (SimFinId <-> CIK <-> canonical ticker)
# --------------------------------------------------------------------------- #
def build_simfin_company_index(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Index the owned us-companies.csv: SimFinId -> {ticker, cik, name}, plus
    CIK->SimFinId and Ticker->SimFinId with reuse/ambiguity detection."""
    path = _resolve(cfg["sources"]["simfin_companies"])
    by_simfinid: Dict[str, Dict[str, str]] = {}
    cik_to_sids: Dict[str, List[str]] = {}
    ticker_to_sids: Dict[str, List[str]] = {}
    with open(path, "r", encoding="utf-8", newline="") as fh:
        for r in csv.DictReader(fh, delimiter=";"):
            sid = (r.get("SimFinId") or "").strip()
            if not sid:
                continue
            tk = (r.get("Ticker") or "").strip().upper()
            cik = (r.get("CIK") or "").strip()
            cik10 = ("%010d" % int(cik)) if cik.isdigit() else ""
            by_simfinid[sid] = {"ticker": tk, "cik": cik10, "cik_raw": cik,
                                "name": (r.get("Company Name") or "").strip()}
            if cik10:
                cik_to_sids.setdefault(cik10, []).append(sid)
            if tk:
                ticker_to_sids.setdefault(tk, []).append(sid)
    return {
        "path": path,
        "content_hash": fb._file_sha256(path),
        "by_simfinid": by_simfinid,
        "cik_to_sids": cik_to_sids,
        "ticker_to_sids": ticker_to_sids,
        "n_companies": len(by_simfinid),
        "n_with_cik": sum(1 for v in by_simfinid.values() if v["cik"]),
    }


def build_simfin_mapping(master: Dict[str, Any], index: Dict[str, Any]) -> Dict[str, Any]:
    """Deterministic canonical-security -> SimFinId mapping (Part C).

    Priority: (1) exact CIK match, (2) exact stable ticker match with a
    compatible/unique identity. A removed security never maps by base ticker
    alone; reused tickers (a base ticker pointing at multiple SimFinIds) stay
    unresolved unless CIK disambiguates. Future survival never influences the
    mapping. Every mapping records source, confidence and ambiguity.
    """
    cik_to_sids = index["cik_to_sids"]
    ticker_to_sids = index["ticker_to_sids"]
    by_sid = index["by_simfinid"]

    rows: List[Dict[str, Any]] = []
    sid_to_ticker: Dict[str, str] = {}
    n_current_mapped = n_removed_mapped = n_ambiguous = n_unmapped = 0
    n_current = n_removed = 0
    by_cik = by_sid_only = by_name = 0

    for r in master["rows"]:
        tk = r["ticker"]
        base = r["base_symbol"]
        is_removed = r["is_delisted"]
        cik10 = r.get("cik") or ""
        sid = None
        source = None
        confidence = "none"
        ambiguity = None

        if is_removed:
            n_removed += 1
        else:
            n_current += 1

        # 1. exact CIK match (survivorship-safe, reuse-proof)
        if cik10 and cik10 in cik_to_sids:
            sids = cik_to_sids[cik10]
            if len(sids) == 1:
                sid, source, confidence = sids[0], "cik", "high"
                by_cik += 1
            else:
                ambiguity = "one CIK maps to multiple SimFinIds"
        # 2. exact ticker match (only when unique AND, for removed names,
        #    corroborated by CIK — never base-ticker alone)
        if sid is None and ambiguity is None:
            sids = ticker_to_sids.get(base, [])
            if len(sids) == 1 and not is_removed:
                sid, source, confidence = sids[0], "ticker_unique_current", "medium"
                by_sid_only += 1
            elif len(sids) == 1 and is_removed:
                cand = sids[0]
                cand_name = by_sid.get(cand, {}).get("name", "")
                if r.get("security_name") and hc._name_tokens(r["security_name"]) & hc._name_tokens(cand_name):
                    sid, source, confidence = cand, "ticker_name_corroborated", "low"
                    by_name += 1
                else:
                    ambiguity = "removed name matches a SimFin ticker but is not CIK/name corroborated (reuse risk); unresolved"
            elif len(sids) > 1:
                ambiguity = "base ticker reused by multiple SimFin companies; unresolved"

        if sid is not None:
            sid_to_ticker[sid] = tk
            if is_removed:
                n_removed_mapped += 1
            else:
                n_current_mapped += 1
        elif ambiguity is not None:
            n_ambiguous += 1
        else:
            n_unmapped += 1

        rows.append({
            "ticker": tk, "base_symbol": base, "is_delisted": is_removed,
            "cik": cik10, "simfin_id": sid, "map_source": source,
            "confidence": confidence, "ambiguity": ambiguity,
        })

    return {
        "schema_version": SIMFIN_ADEQUACY_SCHEMA_VERSION,
        "record_type": "SECURITY_MAPPING",
        "simfin_companies_hash": index["content_hash"],
        "security_master_hash": master["content_hash"],
        "priority": ["exact CIK", "exact stable SimFin ID (via CIK)",
                     "exact ticker + compatible identity/interval",
                     "normalized company name (secondary check only)"],
        "hard_rules": [
            "a base-symbol match alone is insufficient for removed securities",
            "reused tickers never merge unrelated companies",
            "a current company sharing a historical ticker never inherits the removed company's observations",
            "ambiguous mappings remain unresolved",
            "future survival never influences the mapping",
        ],
        "current_members": n_current,
        "removed_members": n_removed,
        "current_mapping_rate": (n_current_mapped / n_current) if n_current else None,
        "removed_mapping_rate": (n_removed_mapped / n_removed) if n_removed else None,
        "ambiguous_rate": (n_ambiguous / max(1, len(master["rows"]))),
        "unmapped_rate": (n_unmapped / max(1, len(master["rows"]))),
        "by_source": {"cik": by_cik, "ticker_unique_current": by_sid_only, "ticker_name_corroborated": by_name},
        "sid_to_ticker": sid_to_ticker,
        "rows": rows,
    }


# --------------------------------------------------------------------------- #
# Part E: reconstruct the three factors from SimFin (committed definitions)
# --------------------------------------------------------------------------- #
def _read_statement(path: str, wanted: Dict[str, str]) -> Dict[Tuple[str, str, str], Dict[str, Any]]:
    """Parse one SimFin statement CSV into {(simfin_id, fy, fp): record}."""
    out: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
    if not os.path.isfile(path):
        return out
    with open(path, "r", encoding="utf-8", newline="") as fh:
        for r in csv.DictReader(fh, delimiter=";"):
            sid = (r.get("SimFinId") or "").strip()
            fy = (r.get("Fiscal Year") or "").strip()
            fp = (r.get("Fiscal Period") or "").strip()
            if not sid or not fy or not fp:
                continue
            rec = {
                "report_date": (r.get("Report Date") or "").strip(),
                "publish_date": (r.get("Publish Date") or "").strip(),
                "restated_date": (r.get("Restated Date") or "").strip(),
            }
            for k, col in wanted.items():
                rec[k] = _to_float(r.get(col))
            out[(sid, fy, fp)] = rec
    return out


def normalize_simfin_fundamentals(cfg: Dict[str, Any], mapping: Dict[str, Any]) -> Dict[str, Any]:
    """Part E: PIT as-reported/restated normalization of the three committed
    factors from the SimFin standard-company statements. Banks are excluded
    (their schema has no cost-of-revenue and no comparable capex). Returns the
    normalized rows (hc schema) plus a full audit."""
    sources = cfg.get("sources") or {}
    inc = _read_statement(_resolve(sources["simfin_income"]), _SF_INCOME_FIELDS)
    bal = _read_statement(_resolve(sources["simfin_balance"]), _SF_BALANCE_FIELDS)
    cf = _read_statement(_resolve(sources["simfin_cashflow"]), _SF_CASHFLOW_FIELDS)

    # bank universe (excluded, but counted explicitly)
    bank_sids: set = set()
    for key in _BANK_SIMFIN_KEYS:
        spec = sources.get(key)
        if not spec:
            continue
        p = _resolve(spec)
        if os.path.isfile(p):
            with open(p, "r", encoding="utf-8", newline="") as fh:
                for r in csv.DictReader(fh, delimiter=";"):
                    sid = (r.get("SimFinId") or "").strip()
                    if sid:
                        bank_sids.add(sid)

    sid_to_ticker = mapping["sid_to_ticker"]
    sid_meta = {r["simfin_id"]: r for r in mapping["rows"] if r.get("simfin_id")}

    rows: List[Dict[str, Any]] = []
    counts = {f: {"original_as_reported": 0, "restated": 0} for f in _TARGET_FACTORS}
    reason = {"unmapped_simfinid": 0, "bank_excluded": 0, "missing_component": 0}
    bank_periods_excluded = 0

    keys = set(inc) | set(cf)
    for key in keys:
        sid = key[0]
        if sid in bank_sids:
            bank_periods_excluded += 1
            continue
        tk = sid_to_ticker.get(sid)
        if not tk:
            reason["unmapped_simfinid"] += 1
            continue
        cik = (sid_meta.get(sid) or {}).get("cik", "")
        a = bal.get(key)
        assets = a.get("assets") if a else None
        i = inc.get(key)
        c = cf.get(key)
        report_end = (i or c or a or {}).get("report_date", key[1])

        def emit(factor: str, value: float, components: List[Dict[str, Any]]) -> None:
            avails = [_availability(x.get("publish_date"), x.get("restated_date")) for x in components]
            avail = max(d for d, _ in avails)
            klass = "restated" if any(k == "restated" for _, k in avails) else "original_as_reported"
            counts[factor][klass] += 1
            rows.append({
                "ticker": tk, "cik": cik, "simfin_id": sid, "factor": factor,
                "fiscal_period_end": report_end, "fiscal_period": key[2],
                "available_date": avail, "value": value,
                "form": "SimFin-bulk", "amended": klass == "restated",
                "restatement": klass, "source_concept": "SimFin",
                "provider": "SimFin bulk (as-reported/restated)",
            })

        # gross_profitability = gross_profit / assets  (SimFin provides Gross Profit
        # directly; fall back to Revenue + Cost of Revenue since SimFin stores
        # Cost of Revenue as a negative number).
        if i is not None and assets:
            gp = i.get("gross_profit")
            if gp is None and i.get("revenue") is not None and i.get("cost_of_revenue") is not None:
                gp = i["revenue"] + i["cost_of_revenue"]
            if gp is not None and assets:
                emit("gross_profitability", gp / assets, [i, a])
            else:
                reason["missing_component"] += 1
        # fcf_to_assets = (ocf - capex) / assets ; SimFin capex line "Change in
        # Fixed Assets & Intangibles" is stored negative -> capex_positive = -line.
        if c is not None and assets and c.get("ocf") is not None:
            capex_change = c.get("capex_change")
            capex_pos = (-capex_change) if capex_change is not None else 0.0
            emit("fcf_to_assets", (c["ocf"] - capex_pos) / assets, [c, a])
        # operating_accruals = (net_income - ocf) / assets
        if i is not None and c is not None and assets and i.get("net_income") is not None and c.get("ocf") is not None:
            emit("operating_accruals", (i["net_income"] - c["ocf"]) / assets, [i, c, a])

    audit = {
        "schema_version": SIMFIN_ADEQUACY_SCHEMA_VERSION,
        "record_type": "FACTOR_NORMALIZATION",
        "definitions_reused": {
            "gross_profitability": "gross_profit / assets",
            "fcf_to_assets": "(ocf - capex) / assets",
            "operating_accruals": "(net_income - ocf) / assets",
        },
        "simfin_field_map": {
            "gross_profit": _SF_INCOME_FIELDS["gross_profit"],
            "revenue": _SF_INCOME_FIELDS["revenue"],
            "cost_of_revenue": _SF_INCOME_FIELDS["cost_of_revenue"],
            "net_income": _SF_INCOME_FIELDS["net_income"],
            "assets": _SF_BALANCE_FIELDS["assets"],
            "ocf": _SF_CASHFLOW_FIELDS["ocf"],
            "capex": "-(%s)" % _SF_CASHFLOW_FIELDS["capex_change"],
        },
        "bank_handling": {
            "decision": "EXCLUDED",
            "reason": "bank statement schema has no Cost of Revenue / Gross Profit "
            "and no economically comparable capex; the standard-company factor "
            "definitions are not forced onto banks.",
            "bank_simfin_ids": len(bank_sids),
            "bank_periods_excluded": bank_periods_excluded,
        },
        "restatement_policy": _RESTATEMENT_POLICY,
        "n_observations": len(rows),
        "observations_by_factor": {f: sum(1 for r in rows if r["factor"] == f) for f in _TARGET_FACTORS},
        "restatement_class_counts": counts,
        "missing_reason_counts": reason,
        "distinct_tickers": len(sorted({r["ticker"] for r in rows})),
        "distinct_simfin_ids": len(sorted({r["simfin_id"] for r in rows})),
        "removed_tickers_with_factors": sorted({r["ticker"] for r in rows if "-" in r["ticker"]}),
    }
    return {"rows": rows, "audit": audit, "bank_sids": sorted(bank_sids)}


# --------------------------------------------------------------------------- #
# SEC reconstruction (cache-only; zero network) for the comparison
# --------------------------------------------------------------------------- #
def reconstruct_sec_rows(cfg: Dict[str, Any], master: Dict[str, Any]) -> Dict[str, Any]:
    """Reconstruct SEC as-filed normalized rows from the EXISTING Phase 30C cache
    + owned phase7j cache (no network). Reuses the committed
    ``hc.normalize_sec_fundamentals`` so the SEC side of the comparison is the
    exact committed as-filed definition."""
    sources = cfg.get("sources") or {}
    cutoff = (cfg.get("data") or {}).get("data_cutoff")

    def _dirs(*keys: str) -> List[str]:
        out = []
        for k in keys:
            spec = sources.get(k)
            if spec:
                p = _resolve(spec)
                if os.path.isdir(p):
                    out.append(p)
        return out

    cf_dirs = _dirs("sec_cache_companyfacts_dir", "owned_sec_companyfacts_dir")
    sub_dirs = _dirs("sec_cache_submissions_dir", "owned_sec_submissions_dir")

    def _load(dirs: List[str], cik10: str) -> Optional[Dict[str, Any]]:
        for d in dirs:
            p = os.path.join(d, "CIK%s.json" % cik10)
            if os.path.isfile(p):
                obj = read_json(p)
                if isinstance(obj, dict):
                    return obj
        return None

    rows: List[Dict[str, Any]] = []
    n_from_cache = 0
    seen: set = set()
    for r in master["rows"]:
        cik10 = r.get("cik") or ""
        cik_int = r.get("cik_int")
        if not cik10 or cik_int is None or cik10 in seen:
            continue
        seen.add(cik10)
        cf = _load(cf_dirs, cik10)
        sub = _load(sub_dirs, cik10)
        if cf is None or sub is None:
            continue
        try:
            rn = hc.normalize_sec_fundamentals(r["ticker"], int(cik_int), cf, sub, cutoff=cutoff)
        except Exception:
            continue
        if rn:
            n_from_cache += 1
            rows.extend(rn)
    return {
        "rows": rows,
        "n_ciks_reconstructed": n_from_cache,
        "companyfacts_dirs": cf_dirs,
        "submissions_dirs": sub_dirs,
        "distinct_tickers": len(sorted({r["ticker"] for r in rows})),
        "n_observations": len(rows),
    }


# --------------------------------------------------------------------------- #
# Parts F/G/H: coverage, union, diagnostics — reusing owned_factors
# --------------------------------------------------------------------------- #
def _series_from_rows(rows: List[Dict[str, Any]], months: List[str], max_stale: int
                      ) -> Dict[str, Dict[str, Dict[str, float]]]:
    series, _meta = hc.build_repaired_factor_series(rows, months, max_staleness_months=max_stale)
    return series


def _coverage_breakdowns(series_by_factor: Dict[str, Dict[str, Dict[str, float]]],
                         inputs: Dict[str, Any], master: Dict[str, Any]) -> Dict[str, Any]:
    """By-decade / by-year / current-vs-removed member-month coverage on the fixed
    global-PIT denominator (the survivorship-free momentum membership)."""
    mom = inputs["mom_monthly"]
    months = inputs["months"]
    removed_tickers = {r["ticker"] for r in master["rows"] if r["is_delisted"]}
    out: Dict[str, Any] = {}
    for fid, series in series_by_factor.items():
        by_decade: Dict[str, Dict[str, int]] = {}
        by_year: Dict[str, Dict[str, int]] = {}
        member_months = covered = cov_current = cov_removed = 0
        mem_current = mem_removed = 0
        for m in months:
            row = series.get(m, {})
            dk = _decade_of(m) or "unknown"
            yr = m[:4]
            dd = by_decade.setdefault(dk, {"member_months": 0, "covered": 0})
            yy = by_year.setdefault(yr, {"member_months": 0, "covered": 0})
            for tk, r in mom.get(m, {}).items():
                if not r.get("is_member"):
                    continue
                member_months += 1
                dd["member_months"] += 1
                yy["member_months"] += 1
                is_rem = tk in removed_tickers
                if is_rem:
                    mem_removed += 1
                else:
                    mem_current += 1
                if tk in row:
                    covered += 1
                    dd["covered"] += 1
                    yy["covered"] += 1
                    if is_rem:
                        cov_removed += 1
                    else:
                        cov_current += 1
        out[fid] = {
            "member_months_total": member_months,
            "member_month_coverage": (covered / member_months) if member_months else 0.0,
            "current_member_month_coverage": (cov_current / mem_current) if mem_current else 0.0,
            "removed_member_month_coverage": (cov_removed / mem_removed) if mem_removed else 0.0,
            "covered_removed_share_of_covered": (cov_removed / covered) if covered else 0.0,
            "by_decade": {k: {**vv, "coverage": (vv["covered"] / vv["member_months"]) if vv["member_months"] else None}
                          for k, vv in sorted(by_decade.items())},
            "by_year": {k: {**vv, "coverage": (vv["covered"] / vv["member_months"]) if vv["member_months"] else None}
                        for k, vv in sorted(by_year.items())},
            "decades_with_material_coverage": sorted(
                k for k, vv in by_decade.items()
                if vv["member_months"] and (vv["covered"] / vv["member_months"]) >= 0.05),
        }
    return out


def evaluate_source(inputs: Dict[str, Any], series_by_factor: Dict[str, Dict[str, Dict[str, float]]],
                    cfg: Dict[str, Any], baseline_t: float, of_cfg: Dict[str, Any],
                    *, source_tag: str, sector_member_month_cov: float = 0.0) -> Dict[str, Any]:
    """Reuse the committed owned_factors diagnostic + decision battery for one
    source's factor series. Fundamentals are never sector-sensitive, so the
    decision turns on coverage/survivorship/signal (via hc._decide_30c)."""
    universe = of.build_universe_profiles(inputs, series_by_factor, of_cfg, sector_pit_safe=True)
    diagnostics = []
    for fid in _TARGET_FACTORS:
        series = series_by_factor.get(fid, {})
        surv = universe["source_observed_universe"].get(fid, {
            "survivorship_classification": "BLOCKED_INSUFFICIENT_DELISTED_COVERAGE",
            "delisted_representation_fraction": 0.0, "shadow_eligible": False})
        res = of.evaluate_factor(inputs, series, factor_id=fid, baseline_t=baseline_t,
                                 survivorship=surv, sector_pit_safe=True, cfg=of_cfg)
        res.pop("_full_global_diagnostic", None)
        decision = hc._decide_30c(res, surv, cfg, sector_member_month_cov, factor_is_sector_sensitive=False)
        g = res["global_pit_universe"]["diagnostics"]
        diagnostics.append({
            "factor_id": fid, "source": source_tag, "decision": decision,
            "cross_sectional_coverage": g.get("cross_sectional_coverage"),
            "month_coverage": g.get("month_coverage"),
            "rank_ic_mean": g.get("rank_ic_mean"),
            "rank_ic_t": g.get("rank_ic_t"),
            "rank_ic_nw_t": g.get("rank_ic_nw_t"),
            "rank_ic_t_ex_best_month": g.get("rank_ic_t_ex_best_month"),
            "positive_month_fraction": g.get("positive_month_fraction"),
            "subperiod_ic_means": g.get("subperiod_ic_means"),
            "regime_ic_means": g.get("regime_ic_means"),
            "avg_top_rank_sector_share": g.get("avg_top_rank_sector_share"),
            "corr_with_mom_6_1": g.get("corr_with_mom_6_1"),
            "corr_with_composite_sn": g.get("corr_with_composite_sn"),
            "corr_with_baseline_score": g.get("corr_with_baseline_score"),
            "survivorship_classification": surv.get("survivorship_classification"),
            "delisted_representation_fraction": surv.get("delisted_representation_fraction"),
            "advance_to_portfolio_screen": decision == "ADVANCE_TO_PORTFOLIO_SCREEN",
        })
    return {"source": source_tag, "universe": universe, "diagnostics": diagnostics}


def build_union(sec_series: Dict[str, Dict[str, Dict[str, float]]],
                simfin_series: Dict[str, Dict[str, Dict[str, float]]]) -> Dict[str, Any]:
    """Deterministic SEC-first source-priority union. SEC as-filed values are
    never replaced by SimFin restated values; SimFin only fills cells SEC does
    not cover. Overlap and material conflicts are reported, never averaged."""
    union: Dict[str, Dict[str, Dict[str, float]]] = {}
    prov = {"sec_only": 0, "simfin_only": 0, "overlap": 0, "conflicts": 0}
    factors = set(sec_series) | set(simfin_series)
    for fid in factors:
        s_sec = sec_series.get(fid, {})
        s_sf = simfin_series.get(fid, {})
        months = set(s_sec) | set(s_sf)
        fu: Dict[str, Dict[str, float]] = {}
        for m in months:
            sec_row = s_sec.get(m, {})
            sf_row = s_sf.get(m, {})
            row: Dict[str, float] = {}
            for tk in set(sec_row) | set(sf_row):
                in_sec = tk in sec_row
                in_sf = tk in sf_row
                if in_sec and in_sf:
                    prov["overlap"] += 1
                    a, b = sec_row[tk], sf_row[tk]
                    denom = max(abs(a), abs(b), 1e-9)
                    if abs(a - b) / denom > 0.05:
                        prov["conflicts"] += 1
                    row[tk] = a  # SEC-first: never replace as-filed with restated
                elif in_sec:
                    prov["sec_only"] += 1
                    row[tk] = sec_row[tk]
                else:
                    prov["simfin_only"] += 1
                    row[tk] = sf_row[tk]
            if row:
                fu[m] = row
        union[fid] = fu
    return {"series": union, "provenance": prov}


# --------------------------------------------------------------------------- #
# Part D: deterministic 80-security sample adequacy
# --------------------------------------------------------------------------- #
def _read_phase30c_sample(cfg: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Reuse the exact persisted Phase 30C deterministic sample when available."""
    sources = cfg.get("sources") or {}
    ptr_spec = sources.get("phase30c_pointer")
    runs_spec = sources.get("phase30c_runs_root")
    if not ptr_spec or not runs_spec:
        return None
    ptr_path = _resolve(ptr_spec)
    if not os.path.isfile(ptr_path):
        return None
    ptr = read_json(ptr_path) or {}
    run_id = ptr.get("run_id")
    if not run_id:
        return None
    sm = os.path.join(_resolve(runs_spec), run_id, "sample_manifest.json")
    if not os.path.isfile(sm):
        return None
    manifest = read_json(sm)
    if isinstance(manifest, dict):
        manifest["_run_id"] = run_id
    return manifest


def evaluate_sample(cfg: Dict[str, Any], mapping: Dict[str, Any],
                    normalization: Dict[str, Any]) -> Dict[str, Any]:
    """Part D: apply the SimFin mapping + reconstruction to the reused Phase 30C
    80-security sample (60 removed + 20 current)."""
    sample = _read_phase30c_sample(cfg)
    map_by_ticker = {r["ticker"]: r for r in mapping["rows"]}
    factors_by_ticker: Dict[str, set] = {}
    for r in normalization["rows"]:
        factors_by_ticker.setdefault(r["ticker"], set()).add(r["factor"])
    bank_sids = set(normalization.get("bank_sids") or [])

    def assess(entry: Dict[str, Any], is_removed: bool) -> Dict[str, Any]:
        tk = entry["ticker"]
        mp = map_by_ticker.get(tk, {})
        sid = mp.get("simfin_id")
        facs = factors_by_ticker.get(tk, set())
        if sid is None:
            fail = "UNMAPPED"
        elif sid in bank_sids:
            fail = "BANK_EXCLUDED"
        elif not facs:
            fail = "MAPPED_NO_STATEMENTS_IN_WINDOW"
        else:
            fail = None
        return {
            "ticker": tk, "is_removed": is_removed, "cik": mp.get("cik"),
            "simfin_id": sid, "map_source": mp.get("map_source"),
            "map_confidence": mp.get("confidence"), "map_ambiguity": mp.get("ambiguity"),
            "usable_factors": sorted(facs), "failure_reason": fail,
        }

    if not sample:
        return {
            "schema_version": SIMFIN_ADEQUACY_SCHEMA_VERSION,
            "record_type": "SAMPLE_ADEQUACY",
            "reused_phase30c_sample": False,
            "note": "Phase 30C sample manifest not found; sample test skipped.",
        }

    removed = [assess(e, True) for e in sample.get("removed", [])]
    current = [assess(e, False) for e in sample.get("current", [])]

    def rate(rows: List[Dict[str, Any]], pred: Callable[[Dict[str, Any]], bool]) -> Optional[float]:
        return (sum(1 for r in rows if pred(r)) / len(rows)) if rows else None

    return {
        "schema_version": SIMFIN_ADEQUACY_SCHEMA_VERSION,
        "record_type": "SAMPLE_ADEQUACY",
        "reused_phase30c_sample": True,
        "phase30c_run_id": sample.get("_run_id"),
        "sample_hash": sample.get("sample_hash"),
        "counts": {"removed": len(removed), "current": len(current)},
        "removed_identity_match_rate": rate(removed, lambda r: r["simfin_id"] is not None),
        "current_identity_match_rate": rate(current, lambda r: r["simfin_id"] is not None),
        "removed_statement_rate": rate(removed, lambda r: bool(r["usable_factors"])),
        "current_statement_rate": rate(current, lambda r: bool(r["usable_factors"])),
        "removed_usable_gross_profitability_rate": rate(removed, lambda r: "gross_profitability" in r["usable_factors"]),
        "removed_usable_fcf_to_assets_rate": rate(removed, lambda r: "fcf_to_assets" in r["usable_factors"]),
        "removed_usable_operating_accruals_rate": rate(removed, lambda r: "operating_accruals" in r["usable_factors"]),
        "removed": removed,
        "current": current,
    }


# --------------------------------------------------------------------------- #
# Part I: free-data ceiling
# --------------------------------------------------------------------------- #
def compute_free_data_ceiling(inventory: Dict[str, Any], mapping: Dict[str, Any],
                              simfin_cov: Dict[str, Any], sec_cov: Dict[str, Any],
                              union_cov: Dict[str, Any], master: Dict[str, Any],
                              cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Part I: quantify the maximum scientifically usable coverage from the
    existing SEC cache + local SimFin files + their non-conflicting union."""
    gate = float((cfg.get("coverage_gates") or {}).get("global_min_cross_sectional_coverage", 0.60))

    def best_mm(cov: Dict[str, Any]) -> float:
        vals = [v.get("member_month_coverage", 0.0) for v in (cov or {}).values()]
        return max(vals) if vals else 0.0

    # SimFin statement window (statement-history + free-plan ceiling)
    inc = (inventory.get("files") or {}).get("simfin_income", {})
    window = [inc.get("report_date_min"), inc.get("report_date_max")]

    removed_total = master["removed_members"]
    removed_mapped = int(round((mapping.get("removed_mapping_rate") or 0.0) * removed_total))

    # per-decade union member-month coverage (max across factors)
    union_by_decade: Dict[str, float] = {}
    for fv in (union_cov or {}).values():
        for dk, dd in (fv.get("by_decade") or {}).items():
            c = dd.get("coverage")
            if c is not None:
                union_by_decade[dk] = max(union_by_decade.get(dk, 0.0), c)
    deficient_decades = sorted(dk for dk, c in union_by_decade.items() if c < gate)

    # missing member-months (union best factor)
    union_best = max((v for v in (union_cov or {}).values()),
                     key=lambda v: v.get("member_month_coverage", 0.0), default={})
    mm_total = union_best.get("member_months_total", 0)
    mm_cov = union_best.get("member_month_coverage", 0.0)
    mm_missing = int(round(mm_total * (1.0 - mm_cov)))

    return {
        "schema_version": SIMFIN_ADEQUACY_SCHEMA_VERSION,
        "record_type": "FREE_DATA_CEILING",
        "coverage_gate": gate,
        "simfin_only_max_member_month_coverage": best_mm(simfin_cov),
        "sec_only_max_member_month_coverage": best_mm(sec_cov),
        "union_max_member_month_coverage": best_mm(union_cov),
        "ceilings": {
            "mapping_ceiling": {
                "current_mapping_rate": mapping.get("current_mapping_rate"),
                "removed_mapping_rate": mapping.get("removed_mapping_rate"),
                "removed_members_total": removed_total,
                "removed_members_mappable": removed_mapped,
                "note": "removed-name identity is capped by free CIK/SimFinId mapping",
            },
            "statement_history_ceiling": {
                "simfin_report_date_window": window,
                "note": "SimFin free bulk fiscal-period window is trailing ~5y; "
                "no statements before it exist in the local files",
            },
            "publication_date_ceiling": {
                "note": "SimFin income/cashflow rows are predominantly latest-restated; "
                "honest PIT dates restated rows at the later restatement date",
            },
            "pre_2009_xbrl_ceiling": {
                "note": "SEC companyfacts XBRL starts ~2009; pre-2009 delisted names "
                "have no free structured fundamentals",
            },
            "free_plan_historical_window_ceiling": {
                "simfin_window": window,
                "note": "the free SimFin plan only exposes a trailing window; a bulk "
                "refresh shifts the window forward, it does not add older history",
            },
            "bank_schema_ceiling": {
                "note": "bank statements lack cost-of-revenue and comparable capex; "
                "excluded from the standard factor definitions",
            },
            "removed_security_ceiling": {
                "removed_members_total": removed_total,
                "removed_members_mappable_free": removed_mapped,
                "removed_members_unmappable_free": removed_total - removed_mapped,
            },
            "sector_history_ceiling": {
                "note": "SimFin carries no point-in-time sector; only SEC filing-header "
                "SIC (Phase 30C) is PIT-safe, and that is separately coverage-bound",
            },
        },
        "quantified_gap": {
            "deficient_decades_under_union": deficient_decades,
            "union_member_month_coverage": mm_cov,
            "union_member_months_missing": mm_missing,
            "removed_members_still_missing_free": removed_total - removed_mapped,
        },
    }


# --------------------------------------------------------------------------- #
# Part J: exact data decision
# --------------------------------------------------------------------------- #
def decide(cfg: Dict[str, Any], inventory: Dict[str, Any], simfin_cov: Dict[str, Any],
           sec_cov: Dict[str, Any], union_cov: Dict[str, Any], union_prov: Dict[str, Any],
           ceiling: Dict[str, Any]) -> Dict[str, Any]:
    """Part J: derive exactly one SimFin decision + one next-data action from the
    measured coverage (never hardcoded)."""
    gate_x = float((cfg.get("coverage_gates") or {}).get("global_min_cross_sectional_coverage", 0.60))
    gate_rep = float((cfg.get("coverage_gates") or {}).get("min_delisted_representation_fraction", 0.20))

    if inventory.get("missing_required_files"):
        return {
            "simfin_decision": "BLOCKED_MISSING_LOCAL_SIMFIN_FILES",
            "next_data_action": "DOWNLOAD_FREE_SIMFIN_BULK_REFRESH",
            "next_data_detail": "missing required local SimFin bulk files: %s" % inventory["missing_required_files"],
            "rationale": "a required local SimFin bulk file is absent.",
        }

    def clears_all(cov: Dict[str, Any]) -> bool:
        for v in (cov or {}).values():
            if v.get("member_month_coverage", 0.0) >= gate_x and \
               len(v.get("decades_with_material_coverage") or []) >= 3 and \
               v.get("removed_member_month_coverage", 0.0) >= gate_rep:
                return True
        return False

    simfin_adds = (union_prov.get("simfin_only", 0) > 0)
    union_best = max((v.get("member_month_coverage", 0.0) for v in (union_cov or {}).values()), default=0.0)
    sec_best = max((v.get("member_month_coverage", 0.0) for v in (sec_cov or {}).values()), default=0.0)
    simfin_best = max((v.get("member_month_coverage", 0.0) for v in (simfin_cov or {}).values()), default=0.0)

    if clears_all(union_cov):
        decision = "FREE_SIMFIN_SUFFICIENT"
        action = "NO_NEW_DATA_REQUIRED"
        detail = "the local free data clears every coverage/survivorship/decade gate."
    elif simfin_best <= 0.0 and not simfin_adds:
        decision = "FREE_SIMFIN_NOT_USABLE"
        action = "REQUEST_TARGETED_HISTORICAL_TRIAL"
        detail = _trial_scope(ceiling)
    elif simfin_adds and union_best > sec_best + 1e-9:
        decision = "FREE_SIMFIN_INCREMENTAL_BUT_INSUFFICIENT"
        action = "REQUEST_TARGETED_HISTORICAL_TRIAL"
        detail = _trial_scope(ceiling)
    else:
        decision = "FREE_SIMFIN_INCREMENTAL_BUT_INSUFFICIENT"
        action = "REQUEST_TARGETED_HISTORICAL_TRIAL"
        detail = _trial_scope(ceiling)

    return {
        "simfin_decision": decision,
        "next_data_action": action,
        "next_data_detail": detail,
        "measured": {
            "simfin_only_best_member_month_coverage": simfin_best,
            "sec_only_best_member_month_coverage": sec_best,
            "union_best_member_month_coverage": union_best,
            "union_adds_simfin_cells": union_prov.get("simfin_only", 0),
            "coverage_gate": gate_x,
        },
    }


def _trial_scope(ceiling: Dict[str, Any]) -> str:
    win = ((ceiling.get("ceilings") or {}).get("statement_history_ceiling") or {}).get("simfin_report_date_window")
    return (
        "targeted historical trial required for survivorship-free deep fundamentals "
        "(income: Revenue, Cost of Revenue/Gross Profit, Net Income; balance: Total "
        "Assets; cashflow: Operating Cash Flow, CapEx) for fiscal periods BEFORE the "
        "local SimFin window %s, INCLUDING delisted/removed securities, with "
        "original as-first-reported publish dates — the exact capability the local "
        "free files do not provide." % (win,)
    )


# --------------------------------------------------------------------------- #
# run store
# --------------------------------------------------------------------------- #
class SimfinAdequacyRunStore:
    _DIRS = ("diagnostics", "reports")

    def __init__(self, output_root: str):
        self.output_root = Path(output_root)
        self.runs_root = self.output_root / RUNS_SUBDIR

    def run_dir(self, run_id: str) -> Path:
        return self.runs_root / run_id

    def ensure_layout(self, run_id: str) -> Path:
        base = self.run_dir(run_id)
        for sub in self._DIRS:
            (base / sub).mkdir(parents=True, exist_ok=True)
        return base

    def write(self, run_id: str, rel: str, obj: Any) -> str:
        return write_json_atomic(self.run_dir(run_id) / rel, obj)

    def write_text(self, run_id: str, rel: str, text: str) -> None:
        write_text_atomic(self.run_dir(run_id) / rel, text)

    def read(self, run_id: str, rel: str) -> Any:
        path = self.run_dir(run_id) / rel
        return read_json(path) if path.exists() else None

    def append_event(self, run_id: str, kind: str, payload: Dict[str, Any]) -> None:
        append_jsonl(self.run_dir(run_id) / "events.jsonl",
                     {"kind": kind, "payload": payload, "ts": _now_iso()})

    def write_latest_pointer(self, obj: Dict[str, Any]) -> None:
        write_json_atomic(self.output_root / LATEST_RUN_FILE, obj)


def compute_run_id(config_hash: str, cutoff: str, code_commit: str) -> str:
    return "sfadq_" + content_hash(
        {"config_hash": config_hash, "cutoff": cutoff, "commit": code_commit})[:16]


# --------------------------------------------------------------------------- #
# orchestration
# --------------------------------------------------------------------------- #
def run_adequacy(cfg: Dict[str, Any], *, output_root: str) -> Dict[str, Any]:
    """Parts A–J: inventory → date semantics → mapping → sample → normalization →
    coverage (SimFin/SEC/union) → diagnostics → ceiling → decision. Fully local,
    deterministic, network-free."""
    verdict = validate_config(cfg)
    if not verdict["accepted"]:
        return {"status": "INVALID_CONFIG", "violations": verdict["violations"]}

    cutoff = (cfg.get("data") or {}).get("data_cutoff")
    code_commit = read_git_commit()
    run_id = compute_run_id(verdict["config_hash"], cutoff, code_commit)
    store = SimfinAdequacyRunStore(output_root)
    store.ensure_layout(run_id)
    store.write(run_id, "config.json", cfg)
    store.append_event(run_id, "RUN_START", {"config_hash": verdict["config_hash"]})

    # Part A / B
    inventory = inventory_local_files(cfg)
    store.write(run_id, "local_file_inventory.json", inventory)
    schema_audit = {
        "schema_version": SIMFIN_ADEQUACY_SCHEMA_VERSION, "record_type": "SIMFIN_SCHEMA_AUDIT",
        "files": {k: {kk: v.get(kk) for kk in ("columns", "row_count", "schema_kind",
                                               "report_date_min", "report_date_max",
                                               "duplicate_key_count", "appears_truncated_to_trailing_window")}
                  for k, v in (inventory.get("files") or {}).items() if isinstance(v, dict) and v.get("exists")},
    }
    store.write(run_id, "simfin_schema_audit.json", schema_audit)
    date_semantics = classify_date_semantics(inventory)
    store.write(run_id, "date_semantics_audit.json", date_semantics)

    if inventory.get("missing_required_files"):
        decision = decide(cfg, inventory, {}, {}, {}, {}, {"ceilings": {}})
        return _finalize(store, run_id, code_commit, verdict, cutoff, cfg, inventory,
                         None, None, None, None, None, {"ceilings": {}}, decision,
                         final_state="BLOCKED_MISSING_LOCAL_SIMFIN_FILES", output_root=output_root)

    # Part C: master (reuse committed Part-A master) + SimFin mapping
    master = hc.build_security_master(cfg)
    index = build_simfin_company_index(cfg)
    mapping = build_simfin_mapping(master, index)
    store.write(run_id, "security_mapping.json", {k: v for k, v in mapping.items() if k != "sid_to_ticker"})
    store.append_event(run_id, "MAPPING_DONE", {"current": mapping["current_mapping_rate"],
                                                "removed": mapping["removed_mapping_rate"]})

    # Part E: normalize SimFin fundamentals
    normalization = normalize_simfin_fundamentals(cfg, mapping)
    store.write(run_id, "factor_normalization.json", normalization["audit"])

    # Part D: 80-security sample adequacy
    sample_adequacy = evaluate_sample(cfg, mapping, normalization)
    store.write(run_id, "sample_adequacy.json", sample_adequacy)

    # inputs + baseline (reused committed path)
    inputs = fb.load_family_inputs(
        data_cutoff=cutoff,
        momentum_panel_path=_resolve(cfg["sources"]["momentum_panel"]),
        sector_map_path=_resolve(cfg["sources"]["sector_map"]),
    )
    of_cfg = _of_cfg(cfg)
    baseline = of.reproduce_baseline(inputs, of_cfg)
    baseline_t = baseline["baseline_rank_ic_t"]
    ref_t = (cfg.get("baseline") or {}).get("rank_ic_t")
    baseline_reproduced = (baseline_t is not None and ref_t is not None
                           and abs(baseline_t - float(ref_t)) < 1e-9)
    if baseline_t is None:   # degenerate inputs (hermetic fixture); keep numeric
        baseline_t = float(ref_t) if ref_t is not None else 0.0
    months = inputs["months"]
    max_stale = int(cfg.get("max_factor_staleness_months", 15))

    # Part F/G: build series (SimFin / SEC / union)
    simfin_series = _series_from_rows(normalization["rows"], months, max_stale)
    sec_recon = reconstruct_sec_rows(cfg, master)
    sec_series = _series_from_rows(sec_recon["rows"], months, max_stale)
    union = build_union(sec_series, simfin_series)
    union_series = union["series"]

    simfin_breakdown = _coverage_breakdowns(simfin_series, inputs, master)
    sec_breakdown = _coverage_breakdowns(sec_series, inputs, master)
    union_breakdown = _coverage_breakdowns(union_series, inputs, master)

    simfin_cov_doc = {
        "schema_version": SIMFIN_ADEQUACY_SCHEMA_VERSION, "record_type": "SIMFIN_COVERAGE",
        "by_factor": simfin_breakdown, "coverage_gate": of_cfg["global_min_cross_sectional_coverage"],
    }
    store.write(run_id, "simfin_coverage.json", simfin_cov_doc)

    # Part H: diagnostics for each source (PIT + identity audits pass -> earned)
    simfin_eval = evaluate_source(inputs, simfin_series, cfg, baseline_t, of_cfg, source_tag="SIMFIN_ONLY")
    sec_eval = evaluate_source(inputs, sec_series, cfg, baseline_t, of_cfg, source_tag="SEC_ONLY")
    union_eval = evaluate_source(inputs, union_series, cfg, baseline_t, of_cfg, source_tag="SEC_PLUS_SIMFIN")
    for tag, ev_res in (("simfin_only", simfin_eval), ("sec_only", sec_eval), ("sec_plus_simfin", union_eval)):
        for d in ev_res["diagnostics"]:
            store.write(run_id, "diagnostics/%s__%s.json" % (tag, d["factor_id"]), d)

    sec_comparison = {
        "schema_version": SIMFIN_ADEQUACY_SCHEMA_VERSION, "record_type": "SEC_COMPARISON",
        "sec_reconstruction": {k: sec_recon[k] for k in ("n_ciks_reconstructed", "distinct_tickers", "n_observations")},
        "sec_by_factor": sec_breakdown,
        "sec_diagnostics": sec_eval["diagnostics"],
        "note": "SEC side reconstructed from the existing Phase 30C + owned cache "
        "(zero network) using the committed hc.normalize_sec_fundamentals.",
    }
    store.write(run_id, "sec_comparison.json", sec_comparison)

    union_doc = {
        "schema_version": SIMFIN_ADEQUACY_SCHEMA_VERSION, "record_type": "COVERAGE_UNION",
        "source_priority": "SEC as-filed first; SimFin fills only SEC-missing cells; "
        "never averaged; SEC as-filed never replaced by SimFin restated",
        "provenance": union["provenance"],
        "union_by_factor": union_breakdown,
        "union_diagnostics": union_eval["diagnostics"],
        "semantics": "SEC_FIRST_PRIORITY_MIXED (SEC original vs SimFin mostly-restated) "
        "-> union diagnostics are DIAGNOSTIC-ONLY, never an advancement basis",
    }
    store.write(run_id, "coverage_union.json", union_doc)

    # Part I: ceiling
    ceiling = compute_free_data_ceiling(inventory, mapping, simfin_breakdown, sec_breakdown,
                                        union_breakdown, master, cfg)
    store.write(run_id, "free_data_ceiling.json", ceiling)

    # Part J: decision
    decision = decide(cfg, inventory, simfin_breakdown, sec_breakdown, union_breakdown,
                      union["provenance"], ceiling)

    extras = {
        "master": master, "mapping": mapping, "normalization": normalization,
        "sample_adequacy": sample_adequacy, "sec_recon": sec_recon,
        "simfin_eval": simfin_eval, "sec_eval": sec_eval, "union_eval": union_eval,
        "union_prov": union["provenance"], "date_semantics": date_semantics,
        "baseline": baseline, "baseline_reproduced": baseline_reproduced,
        "simfin_breakdown": simfin_breakdown, "sec_breakdown": sec_breakdown,
        "union_breakdown": union_breakdown,
    }
    return _finalize(store, run_id, code_commit, verdict, cutoff, cfg, inventory,
                     mapping, normalization, sample_adequacy, ceiling, extras, ceiling,
                     decision, final_state="COMPLETE", output_root=output_root)


def _best_cov(breakdown: Optional[Dict[str, Any]]) -> Tuple[Optional[str], float]:
    if not breakdown:
        return None, 0.0
    best = max(breakdown.items(), key=lambda kv: kv[1].get("member_month_coverage", 0.0), default=(None, {}))
    return best[0], (best[1].get("member_month_coverage", 0.0) if best[1] else 0.0)


def _finalize(store: SimfinAdequacyRunStore, run_id: str, code_commit: str, verdict: Dict[str, Any],
              cutoff: str, cfg: Dict[str, Any], inventory: Dict[str, Any],
              mapping: Optional[Dict[str, Any]], normalization: Optional[Dict[str, Any]],
              sample_adequacy: Optional[Dict[str, Any]], ceiling: Optional[Dict[str, Any]],
              extras: Any, ceiling2: Dict[str, Any], decision: Dict[str, Any], *,
              final_state: str, output_root: str) -> Dict[str, Any]:
    ex = extras if isinstance(extras, dict) else {}
    simfin_breakdown = ex.get("simfin_breakdown") or {}
    sec_breakdown = ex.get("sec_breakdown") or {}
    union_breakdown = ex.get("union_breakdown") or {}
    best_factor, best_cov = _best_cov(union_breakdown or simfin_breakdown)
    inc = (inventory.get("files") or {}).get("simfin_income", {})

    run_doc = {
        "schema_version": SIMFIN_ADEQUACY_SCHEMA_VERSION,
        "run_id": run_id, "code_commit": code_commit, "config_hash": verdict["config_hash"],
        "data_cutoff": cutoff, "final_state": final_state,
        "simfin_decision": decision["simfin_decision"],
        "next_data_action": decision["next_data_action"],
        "next_data_detail": decision.get("next_data_detail"),
        "decision_measured": decision.get("measured"),
        "simfin_report_window": [inc.get("report_date_min"), inc.get("report_date_max")],
        "mapping": None if not mapping else {
            "current_mapping_rate": mapping.get("current_mapping_rate"),
            "removed_mapping_rate": mapping.get("removed_mapping_rate"),
            "ambiguous_rate": mapping.get("ambiguous_rate"),
            "unmapped_rate": mapping.get("unmapped_rate"),
            "by_source": mapping.get("by_source"),
        },
        "normalization": None if not normalization else {
            "n_observations": normalization["audit"]["n_observations"],
            "observations_by_factor": normalization["audit"]["observations_by_factor"],
            "restatement_class_counts": normalization["audit"]["restatement_class_counts"],
            "bank_handling": normalization["audit"]["bank_handling"],
        },
        "sample_adequacy": None if not sample_adequacy else {
            "reused_phase30c_sample": sample_adequacy.get("reused_phase30c_sample"),
            "removed_identity_match_rate": sample_adequacy.get("removed_identity_match_rate"),
            "removed_statement_rate": sample_adequacy.get("removed_statement_rate"),
            "current_statement_rate": sample_adequacy.get("current_statement_rate"),
        },
        "simfin_only_global_coverage": _best_cov(simfin_breakdown)[1],
        "sec_only_global_coverage": _best_cov(sec_breakdown)[1],
        "union_global_coverage": best_cov,
        "best_feature": best_factor,
        "best_feature_decision": next((d["decision"] for d in (ex.get("union_eval") or {}).get("diagnostics", [])
                                       if d["factor_id"] == best_factor), None),
        "baseline": ex.get("baseline"),
        "baseline_reproduced": ex.get("baseline_reproduced"),
        "advanced_to_portfolio_screen": [],
        "n_portfolio_candidates": 0,
        "safety": dict(SAFETY_CONTRACT),
        "generated_at": _now_iso(),
    }
    store.write(run_id, "run.json", run_doc)
    store.write(run_id, "status.json", {
        "run_id": run_id, "final_state": final_state,
        "simfin_decision": decision["simfin_decision"],
        "next_data_action": decision["next_data_action"],
        "best_feature": best_factor,
        "best_feature_decision": run_doc["best_feature_decision"],
        "simfin_only_global_coverage": run_doc["simfin_only_global_coverage"],
        "sec_only_global_coverage": run_doc["sec_only_global_coverage"],
        "union_global_coverage": run_doc["union_global_coverage"],
        "advanced": [], "safety": dict(SAFETY_CONTRACT), "updated_at": _now_iso()})
    store.append_event(run_id, "RUN_COMPLETE", {"decision": decision["simfin_decision"],
                                                "action": decision["next_data_action"]})

    removed_mapped = None
    if normalization:
        removed_mapped = len(normalization["audit"].get("removed_tickers_with_factors") or [])
    store.write_latest_pointer({
        "run_id": run_id, "code_commit": code_commit, "config_hash": verdict["config_hash"],
        "data_cutoff": cutoff, "final_state": final_state,
        "simfin_decision": decision["simfin_decision"],
        "next_data_action": decision["next_data_action"],
        "simfin_only_global_coverage": run_doc["simfin_only_global_coverage"],
        "sec_only_global_coverage": run_doc["sec_only_global_coverage"],
        "union_global_coverage": run_doc["union_global_coverage"],
        "simfin_removed_names_mapped": (int(round((mapping.get("removed_mapping_rate") or 0.0)
                                                  * mapping.get("removed_members", 0))) if mapping else None),
        "simfin_removed_names_with_statements": removed_mapped,
        "earliest_usable_date": inc.get("report_date_min"),
        "latest_usable_date": inc.get("report_date_max"),
        "best_feature": best_factor,
        "best_feature_decision": run_doc["best_feature_decision"],
        "generated_at": _now_iso(), "output_root": str(output_root),
    })
    return {"status": final_state, "run_id": run_id, "output_root": str(output_root),
            "run_dir": str(store.run_dir(run_id)),
            "simfin_decision": decision["simfin_decision"],
            "next_data_action": decision["next_data_action"],
            "simfin_only_global_coverage": run_doc["simfin_only_global_coverage"],
            "sec_only_global_coverage": run_doc["sec_only_global_coverage"],
            "union_global_coverage": run_doc["union_global_coverage"],
            "best_feature": best_factor}


def generate_status(run_id: str, output_root: str) -> Dict[str, Any]:
    store = SimfinAdequacyRunStore(output_root)
    status = store.read(run_id, "status.json")
    if status is None:
        raise SimfinAdequacyError("unknown run_id: %s" % run_id)
    run_doc = store.read(run_id, "run.json") or {}
    return {
        "run_id": run_id, "final_state": status.get("final_state"),
        "simfin_decision": status.get("simfin_decision"),
        "next_data_action": status.get("next_data_action"),
        "simfin_only_global_coverage": status.get("simfin_only_global_coverage"),
        "sec_only_global_coverage": status.get("sec_only_global_coverage"),
        "union_global_coverage": status.get("union_global_coverage"),
        "best_feature": status.get("best_feature"),
        "best_feature_decision": run_doc.get("best_feature_decision"),
        "baseline_reproduced": run_doc.get("baseline_reproduced"),
        "safety": dict(SAFETY_CONTRACT),
    }


def generate_report(run_id: str, output_root: str) -> Dict[str, Any]:
    store = SimfinAdequacyRunStore(output_root)
    run_doc = store.read(run_id, "run.json")
    if run_doc is None:
        raise SimfinAdequacyError("unknown run_id: %s" % run_id)
    union = store.read(run_id, "coverage_union.json") or {}
    ceiling = store.read(run_id, "free_data_ceiling.json") or {}

    lines = ["# Phase 30C.1 — local SimFin adequacy + free-data coverage ceiling", ""]
    lines.append("- run_id: %s" % run_doc["run_id"])
    lines.append("- code_commit: %s" % run_doc["code_commit"])
    lines.append("- data_cutoff: %s" % run_doc["data_cutoff"])
    lines.append("- SimFin report window: %s" % (run_doc.get("simfin_report_window"),))
    lines.append("- SimFin-only global coverage: %s" % _fmt(run_doc.get("simfin_only_global_coverage")))
    lines.append("- SEC-only global coverage: %s" % _fmt(run_doc.get("sec_only_global_coverage")))
    lines.append("- Union global coverage: %s" % _fmt(run_doc.get("union_global_coverage")))
    lines.append("- baseline reproduced: %s" % run_doc.get("baseline_reproduced"))
    lines.append("- **SimFin decision: %s**" % run_doc.get("simfin_decision"))
    lines.append("- **Next-data action: %s**" % run_doc.get("next_data_action"))
    lines.append("")
    lines.append("## Union factor diagnostics (Part H, diagnostic-only)")
    lines.append("")
    lines.append("| factor | source | member-month cov | rank-IC t | decision |")
    lines.append("|---|---|---|---|---|")
    for d in (union.get("union_diagnostics") or []):
        lines.append("| %s | %s | %s | %s | %s |" % (
            d["factor_id"], d["source"], _fmt(d.get("month_coverage")),
            _fmt(d.get("rank_ic_t")), d["decision"]))
    lines.append("")
    lines.append("_Research-only. No order, broker, automation, promotion, challenger, "
                 "or operational-model change occurred._")
    report_md = "\n".join(lines) + "\n"

    report_json = {
        "schema_version": SIMFIN_ADEQUACY_SCHEMA_VERSION,
        "record_type": "SIMFIN_ADEQUACY_REPORT",
        "run_id": run_doc["run_id"], "code_commit": run_doc["code_commit"],
        "final_state": run_doc["final_state"],
        "simfin_decision": run_doc.get("simfin_decision"),
        "next_data_action": run_doc.get("next_data_action"),
        "next_data_detail": run_doc.get("next_data_detail"),
        "simfin_only_global_coverage": run_doc.get("simfin_only_global_coverage"),
        "sec_only_global_coverage": run_doc.get("sec_only_global_coverage"),
        "union_global_coverage": run_doc.get("union_global_coverage"),
        "quantified_gap": ceiling.get("quantified_gap"),
        "safety": dict(SAFETY_CONTRACT),
    }
    store.write(run_id, "reports/report.json", report_json)
    store.write_text(run_id, "reports/report.md", report_md)
    return {"run_id": run_id, "report": report_json,
            "artifact_paths": {"report_md": str(store.run_dir(run_id) / "reports" / "report.md"),
                               "report_json": str(store.run_dir(run_id) / "reports" / "report.json")}}


def _fmt(x: Any) -> str:
    if isinstance(x, bool):
        return str(x)
    if isinstance(x, (int, float)):
        return "%.4f" % x
    return str(x)


__all__ = [
    "DATE_SEMANTICS_CLASSES",
    "LATEST_RUN_FILE",
    "NEXT_DATA_ACTIONS",
    "SIMFIN_ADEQUACY_SCHEMA_VERSION",
    "SIMFIN_DECISIONS",
    "SimfinAdequacyError",
    "SimfinAdequacyRunStore",
    "build_simfin_company_index",
    "build_simfin_mapping",
    "build_union",
    "classify_date_semantics",
    "compute_free_data_ceiling",
    "compute_run_id",
    "decide",
    "evaluate_sample",
    "evaluate_source",
    "generate_report",
    "generate_status",
    "inventory_local_files",
    "load_config",
    "normalize_simfin_fundamentals",
    "reconstruct_sec_rows",
    "run_adequacy",
    "validate_config",
]
