"""Phase 30C.2 — export the historical-fundamentals vendor evaluation pack.

Phase 30C (run ``hcov_026a857c80119147``) and Phase 30C.1 (run
``sfadq_f5403565056936e8``) together established that free SEC EDGAR as-filed
backfill plus the free SimFin bulk cannot clear the unchanged 60% member-month
coverage gate: the binding constraint is *pre-2020 survivorship-safe deep
fundamentals for removed securities*. Both phases finished with the same next
action — ``REQUEST_TARGETED_HISTORICAL_TRIAL``.

This module performs that action's paperwork, and nothing else. It is a pure,
deterministic *exporter*: it reads the already-computed 30C / 30C.1 evidence and
emits a clean, vendor-neutral evaluation package (a fixed 80-security trial
sample, a required-field contract, acceptance gates, an empty response template,
scoring instructions, a neutral request email and a hashed manifest) that any
provider — Intrinio Gold Fundamentals, Nasdaq Sharadar SF1, or another — can be
evaluated against on identical terms.

It runs no alpha campaign, contacts no vendor, makes no network call, and never
touches Paper Trader. The 80-security sample is the *exact* deterministic Phase
30C selection (sample_hash ``997064...``); it is reused verbatim, never
reselected, and no inconvenient or unmapped name is dropped or replaced.

Everything is reused from the committed research agent:

* ``simfin_adequacy._read_phase30c_sample`` — the exact persisted 30C sample.
* ``historical_coverage`` — the fixed-root path resolver, the Paper Trader
  guard, and the config string-walker.
* ``family_backtest`` — file hashing and the fixed repo/data roots.
* ``artifact_store`` — atomic writes, content hashing, secret-key scanning.
* ``controller`` — git HEAD without a subprocess.

The pack is byte-deterministic: the only clock value (``generated_at``) is taken
from config, so re-running produces an identical package.
"""

from __future__ import annotations

import csv
import io
import os
import re
from typing import Any, Dict, List, Optional, Tuple

from . import SAFETY_CONTRACT
from . import family_backtest as fb
from . import historical_coverage as hc
from . import simfin_adequacy as sfa
from .artifact_store import (
    content_hash,
    find_secret_keys,
    read_json,
    write_json_atomic,
    write_text_atomic,
)
from .controller import read_git_commit
from .feature_evaluation import DEFAULT_SCREEN_THRESHOLDS
from .schemas import find_forbidden_execution_keys

VENDOR_PACK_SCHEMA_VERSION = "30C2.1"

DEFAULT_OUTPUT_SUBDIR = "vendor_evaluation_pack"

# Deliverable filenames (Part "REQUIRED OUTPUTS" 1..7).
TRIAL_SAMPLE_CSV = "historical_fundamentals_trial_sample.csv"
REQUIRED_FIELDS_CSV = "required_vendor_fields.csv"
ACCEPTANCE_GATES_JSON = "vendor_acceptance_gates.json"
RESPONSE_TEMPLATE_CSV = "vendor_response_template.csv"
SCORING_MD = "trial_scoring_instructions.md"
REQUEST_EMAIL_TXT = "provider_request_email.txt"
PACKAGE_MANIFEST_JSON = "package_manifest.json"

DELIVERABLES = (
    TRIAL_SAMPLE_CSV,
    REQUIRED_FIELDS_CSV,
    ACCEPTANCE_GATES_JSON,
    RESPONSE_TEMPLATE_CSV,
    SCORING_MD,
    REQUEST_EMAIL_TXT,
    PACKAGE_MANIFEST_JSON,
)

# Ordered columns of the trial sample CSV (exactly the requested fields).
TRIAL_SAMPLE_COLUMNS = (
    "sample_group",
    "canonical_security_id",
    "historical_ticker",
    "base_ticker",
    "company_name",
    "current_or_removed",
    "removal_decade",
    "first_membership_date",
    "last_membership_date",
    "delisting_date",
    "norgate_identifier",
    "cik",
    "simfin_id",
    "mapping_confidence",
    "mapping_ambiguity",
    "sec_statement_availability",
    "simfin_statement_availability",
    "current_failure_reason",
)

# Columns of the required-vendor-fields contract CSV.
REQUIRED_FIELDS_COLUMNS = (
    "field",
    "mandatory_or_optional",
    "accepted_aliases",
    "expected_type",
    "point_in_time_requirement",
    "why_required",
)

# The mandatory/optional vendor-field contract. Each row is emitted verbatim.
REQUIRED_VENDOR_FIELDS: Tuple[Dict[str, str], ...] = (
    {
        "field": "stable_company_identifier",
        "mandatory_or_optional": "mandatory",
        "accepted_aliases": "company_id|permco|entity_id|companyId|simfin_company_id",
        "expected_type": "string",
        "point_in_time_requirement": "immutable across the company's life; must not be reused after delisting",
        "why_required": "primary key to join fundamentals to a survivorship-safe entity across ticker changes and delisting",
    },
    {
        "field": "stable_security_identifier",
        "mandatory_or_optional": "optional",
        "accepted_aliases": "security_id|permno|figi|share_class_id",
        "expected_type": "string",
        "point_in_time_requirement": "immutable per share class; distinct from company id for multi-class issuers",
        "why_required": "disambiguates share classes (e.g. Class A vs Class B); optional when company id + ticker are unambiguous",
    },
    {
        "field": "cik",
        "mandatory_or_optional": "mandatory",
        "accepted_aliases": "sec_cik|central_index_key",
        "expected_type": "string(zero-padded 10)",
        "point_in_time_requirement": "as assigned by SEC; stable per registrant",
        "why_required": "bridges the vendor entity to our SEC-based identity and to the owned as-filed cache",
    },
    {
        "field": "historical_ticker",
        "mandatory_or_optional": "mandatory",
        "accepted_aliases": "ticker|symbol|trading_symbol",
        "expected_type": "string",
        "point_in_time_requirement": "the exchange symbol AS TRADED in each interval; not the current reuse of the symbol",
        "why_required": "removed names are keyed by their historical symbol; a current reuse of the symbol is a different entity",
    },
    {
        "field": "ticker_effective_from_date",
        "mandatory_or_optional": "mandatory",
        "accepted_aliases": "symbol_start_date|ticker_valid_from",
        "expected_type": "date(YYYY-MM-DD)",
        "point_in_time_requirement": "start of the interval the symbol was valid for this entity",
        "why_required": "prevents mapping a reused ticker to the wrong entity/period",
    },
    {
        "field": "ticker_effective_through_date",
        "mandatory_or_optional": "mandatory",
        "accepted_aliases": "symbol_end_date|ticker_valid_to",
        "expected_type": "date(YYYY-MM-DD) or empty-if-active",
        "point_in_time_requirement": "end of the interval the symbol was valid; empty only for still-active symbols",
        "why_required": "closes the symbol interval so delisted-name joins are unambiguous",
    },
    {
        "field": "active_inactive_delisted_status",
        "mandatory_or_optional": "mandatory",
        "accepted_aliases": "status|is_active|delisted_flag|listing_status",
        "expected_type": "enum(active|inactive|delisted)",
        "point_in_time_requirement": "reflects listing status as of the delisting/observation date, not just current status",
        "why_required": "removed-name coverage is the entire point; current-only status hides the survivorship gap",
    },
    {
        "field": "fiscal_period_end",
        "mandatory_or_optional": "mandatory",
        "accepted_aliases": "period_end|report_date|fiscal_period_end_date",
        "expected_type": "date(YYYY-MM-DD)",
        "point_in_time_requirement": "the fiscal period the statement covers; NEVER used as the availability date",
        "why_required": "period key for each statement observation",
    },
    {
        "field": "filing_publication_acceptance_date",
        "mandatory_or_optional": "mandatory",
        "accepted_aliases": "filed_date|publish_date|acceptance_datetime|available_date",
        "expected_type": "date(YYYY-MM-DD)",
        "point_in_time_requirement": "the date the value first became publicly knowable; this is the availability date used for PIT joins",
        "why_required": "point-in-time formation requires the true availability date, not the fiscal date",
    },
    {
        "field": "original_as_reported_vs_restated_indicator",
        "mandatory_or_optional": "mandatory",
        "accepted_aliases": "is_restated|as_reported_flag|restatement_flag|point_in_time_flag",
        "expected_type": "enum(as_reported|restated)",
        "point_in_time_requirement": "must distinguish the first-reported value from any later restatement of the same period",
        "why_required": "restated values embed look-ahead; original-as-reported is required for honest PIT research",
    },
    {
        "field": "revision_vintage_identifier",
        "mandatory_or_optional": "optional",
        "accepted_aliases": "vintage|revision_id|as_of_date|point_in_time_snapshot_date",
        "expected_type": "string or date",
        "point_in_time_requirement": "identifies which vintage a value belongs to when multiple exist for one period",
        "why_required": "explicit vintages let us reconstruct as-reported when a separate original flag is absent",
    },
    {
        "field": "revenue",
        "mandatory_or_optional": "mandatory",
        "accepted_aliases": "total_revenue|net_revenue|sales|revenues",
        "expected_type": "number(currency)",
        "point_in_time_requirement": "value as of the filing/publication date for the given vintage",
        "why_required": "input to gross_profitability and a core statement completeness check",
    },
    {
        "field": "cost_of_revenue",
        "mandatory_or_optional": "mandatory",
        "accepted_aliases": "cogs|cost_of_goods_sold|cost_of_sales",
        "expected_type": "number(currency)",
        "point_in_time_requirement": "value as of the filing/publication date for the given vintage",
        "why_required": "with revenue yields gross profit; sign convention must be documented",
    },
    {
        "field": "gross_profit",
        "mandatory_or_optional": "optional",
        "accepted_aliases": "gross_income",
        "expected_type": "number(currency)",
        "point_in_time_requirement": "value as of the filing/publication date for the given vintage",
        "why_required": "derivable from revenue and cost of revenue; supplying it directly removes sign-convention ambiguity",
    },
    {
        "field": "net_income",
        "mandatory_or_optional": "mandatory",
        "accepted_aliases": "net_income_common|net_earnings|profit_after_tax",
        "expected_type": "number(currency)",
        "point_in_time_requirement": "value as of the filing/publication date for the given vintage",
        "why_required": "input to operating_accruals",
    },
    {
        "field": "total_assets",
        "mandatory_or_optional": "mandatory",
        "accepted_aliases": "assets|total_assets_reported",
        "expected_type": "number(currency)",
        "point_in_time_requirement": "value as of the filing/publication date for the given vintage",
        "why_required": "denominator for all three target factors",
    },
    {
        "field": "operating_cash_flow",
        "mandatory_or_optional": "mandatory",
        "accepted_aliases": "ocf|cash_from_operations|net_cash_from_operating_activities",
        "expected_type": "number(currency)",
        "point_in_time_requirement": "value as of the filing/publication date for the given vintage",
        "why_required": "input to fcf_to_assets and operating_accruals",
    },
    {
        "field": "capital_expenditures",
        "mandatory_or_optional": "mandatory",
        "accepted_aliases": "capex|purchase_of_ppe|change_in_fixed_assets_and_intangibles",
        "expected_type": "number(currency)",
        "point_in_time_requirement": "value as of the filing/publication date for the given vintage",
        "why_required": "with OCF yields free cash flow for fcf_to_assets; sign convention must be documented",
    },
    {
        "field": "sector",
        "mandatory_or_optional": "mandatory",
        "accepted_aliases": "gics_sector|sector_name",
        "expected_type": "string",
        "point_in_time_requirement": "sector as classified during the observation interval, not only the current classification",
        "why_required": "sector-neutralisation of factors requires point-in-time sector, including for delisted names",
    },
    {
        "field": "industry",
        "mandatory_or_optional": "optional",
        "accepted_aliases": "gics_industry|industry_name|sub_industry",
        "expected_type": "string",
        "point_in_time_requirement": "industry as classified during the observation interval",
        "why_required": "finer neutralisation; optional because sector is the mandatory grain",
    },
    {
        "field": "sector_effective_from_date",
        "mandatory_or_optional": "mandatory",
        "accepted_aliases": "sector_start_date|classification_from",
        "expected_type": "date(YYYY-MM-DD)",
        "point_in_time_requirement": "start of the interval the sector classification applied",
        "why_required": "without sector history, sector neutralisation leaks look-ahead classification",
    },
    {
        "field": "sector_effective_through_date",
        "mandatory_or_optional": "mandatory",
        "accepted_aliases": "sector_end_date|classification_to",
        "expected_type": "date(YYYY-MM-DD) or empty-if-current",
        "point_in_time_requirement": "end of the interval the sector classification applied; empty only if still current",
        "why_required": "closes the sector interval so point-in-time sector joins are unambiguous",
    },
)

# Columns a provider fills in the (empty) standardized response file, at the
# (security x fiscal period x vintage) grain.
RESPONSE_TEMPLATE_COLUMNS = (
    "canonical_security_id",
    "vendor_company_identifier",
    "vendor_security_identifier",
    "cik_returned",
    "matched",
    "match_method",
    "historical_ticker_returned",
    "active_inactive_delisted_status",
    "fiscal_period_end",
    "fiscal_period",
    "filing_publication_acceptance_date",
    "original_as_reported_vs_restated_indicator",
    "revision_vintage_identifier",
    "currency",
    "revenue",
    "cost_of_revenue",
    "gross_profit",
    "net_income",
    "total_assets",
    "operating_cash_flow",
    "capital_expenditures",
    "sector",
    "industry",
    "sector_effective_from_date",
    "sector_effective_through_date",
    "notes",
)


class VendorPackError(RuntimeError):
    pass


# --------------------------------------------------------------------------- #
# config
# --------------------------------------------------------------------------- #
def load_config(path: str) -> Optional[Dict[str, Any]]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            import json

            return json.load(fh)
    except (OSError, ValueError):
        return None


def validate_config(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Structural + safety validation. Never touches the network or a vendor.
    Any URL, secret, Paper Trader reference, acquisition/network key, or weakened
    coverage/representation gate is rejected."""
    v: List[Dict[str, Any]] = []

    def bad(field: str, issue: str, value: Any = None) -> None:
        v.append({"field": field, "issue": issue, "value": value})

    if not isinstance(cfg, dict):
        return {"accepted": False, "violations": [{"field": "$", "issue": "config must be an object"}], "config_hash": None}

    if cfg.get("schema_version") != VENDOR_PACK_SCHEMA_VERSION:
        bad("schema_version", "must be %s" % VENDOR_PACK_SCHEMA_VERSION, cfg.get("schema_version"))
    if not cfg.get("name"):
        bad("name", "required")

    # secrets / forbidden execution keys
    for k in find_secret_keys(cfg):
        bad(k, "secret-looking key is forbidden in config")
    for k in find_forbidden_execution_keys(cfg):
        bad(k, "forbidden execution-token key")

    # Paper Trader must never be referenced
    for s in hc._iter_string_values(cfg):
        low = s.lower()
        for token in hc._PAPER_TRADER_FORBIDDEN:
            if token in low:
                bad("$", "Paper Trader path/endpoint reference is forbidden", token)
                break

    # NO network URLs anywhere: this phase is a local export, never a vendor call
    for s in hc._iter_string_values(cfg):
        low = s.lower()
        if "://" in low or low.startswith("http") or "www." in low:
            bad("$", "network URL is forbidden in a local export phase", s)
    for forbidden_key in ("provider_endpoints", "allowed_hosts", "acquisition"):
        if forbidden_key in cfg:
            bad(forbidden_key, "network/acquisition config is forbidden in a local export phase")

    data = cfg.get("data") or {}
    cutoff = data.get("data_cutoff")
    if not isinstance(cutoff, str) or len(cutoff) != 10:
        bad("data.data_cutoff", "required YYYY-MM-DD", cutoff)

    ga = cfg.get("generated_at")
    if not isinstance(ga, str) or len(ga) < 10:
        bad("generated_at", "required ISO timestamp (pins determinism)", ga)

    # roots: only the two fixed roots
    roots = (cfg.get("sources") or {}).get("roots") or cfg.get("roots") or {}
    for name in roots:
        if name not in hc._ROOTS:
            bad("roots.%s" % name, "only fixed roots {repo, data_root} allowed", name)

    # every declared source path must resolve within a fixed root
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

    # required source pointers for the 30C / 30C.1 evidence must be declared
    for key in ("phase30c_pointer", "phase30c_runs_root", "phase30c1_pointer", "phase30c1_runs_root"):
        if key not in sources:
            bad("sources.%s" % key, "required source pointer is missing")

    # entitlement-only rules (no purchase / no sales contact)
    ent = cfg.get("entitlement") or {}
    if ent.get("no_purchase") is not True:
        bad("entitlement.no_purchase", "must be true (no data purchase)", ent.get("no_purchase"))
    if ent.get("contact_sales") not in (False, None):
        bad("entitlement.contact_sales", "must be false (no sales contact)", ent.get("contact_sales"))

    # coverage / representation gates — never weakened (must match the exported gates)
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

    # safety contract present, and no order/broker/automation/promotion path
    safety = cfg.get("safety") or {}
    if safety.get("research_only") is not True:
        bad("safety.research_only", "must be true")
    if safety.get("may_register_challengers") not in (False, None):
        bad("safety.may_register_challengers", "must be false", safety.get("may_register_challengers"))

    return {"accepted": not v, "violations": v, "config_hash": content_hash(cfg)}


def _resolve(spec: Any) -> str:
    return hc._resolve_path_spec(spec)


# --------------------------------------------------------------------------- #
# source evidence loaders (all local; zero network)
# --------------------------------------------------------------------------- #
def _pointer_run_id(cfg: Dict[str, Any], pointer_key: str) -> Optional[str]:
    spec = (cfg.get("sources") or {}).get(pointer_key)
    if not spec:
        return None
    path = _resolve(spec)
    if not os.path.isfile(path):
        return None
    ptr = read_json(path) or {}
    return ptr.get("run_id")


def load_phase30c_sample(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Reuse the EXACT committed Phase 30C deterministic sample (60 removed + 20
    current). Never reselects."""
    sample = sfa._read_phase30c_sample(cfg)
    if not sample:
        raise VendorPackError("Phase 30C sample_manifest.json not found; cannot export the pack")
    return sample


def load_sample_adequacy(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Reuse the Phase 30C.1 per-security SimFin adequacy (mapping, SimFin id,
    usable factors, failure reason)."""
    run_id = _pointer_run_id(cfg, "phase30c1_pointer")
    runs_spec = (cfg.get("sources") or {}).get("phase30c1_runs_root")
    if not run_id or not runs_spec:
        raise VendorPackError("Phase 30C.1 sample_adequacy.json not found; cannot export the pack")
    path = os.path.join(_resolve(runs_spec), run_id, "sample_adequacy.json")
    if not os.path.isfile(path):
        raise VendorPackError("Phase 30C.1 sample_adequacy.json missing at %s" % path)
    doc = read_json(path)
    doc["_run_id"] = run_id
    return doc


def load_security_master_index(cfg: Dict[str, Any], run_id: str) -> Dict[str, Dict[str, Any]]:
    """Per-ticker record from the 30C security master (last membership month,
    quote dates)."""
    runs_spec = (cfg.get("sources") or {}).get("phase30c_runs_root")
    path = os.path.join(_resolve(runs_spec), run_id, "security_master.json")
    master = read_json(path)
    rows = master.get("rows") if isinstance(master, dict) else master
    return {r.get("ticker"): r for r in (rows or [])}


def load_sec_asfiled_removed(cfg: Dict[str, Any], run_id: str) -> set:
    """Removed tickers for which Phase 30C reconstructed SEC as-filed statements."""
    runs_spec = (cfg.get("sources") or {}).get("phase30c_runs_root")
    path = os.path.join(_resolve(runs_spec), run_id, "normalized_manifests", "sec_asfiled.json")
    if not os.path.isfile(path):
        return set()
    doc = read_json(path)
    return set(doc.get("removed_tickers_acquired") or [])


def _companyfacts_dirs(cfg: Dict[str, Any]) -> List[str]:
    out: List[str] = []
    for key in ("owned_sec_companyfacts_dir", "sec_cache_companyfacts_dir"):
        spec = (cfg.get("sources") or {}).get(key)
        if spec:
            try:
                out.append(_resolve(spec))
            except Exception:
                pass
    return out


def sec_statement_availability(cik: Optional[str], dirs: List[str], *, min_bytes: int = 1024) -> str:
    """Network-free per-security SEC signal from the owned companyfacts caches:
    a present, non-trivial ``CIK<10>.json`` means SEC as-filed fundamentals exist."""
    if not cik:
        return "NO_SEC_IDENTITY"
    padded = str(cik).zfill(10)
    best = "NO_SEC_COMPANYFACTS_CACHED"
    for d in dirs:
        for name in ("CIK%s.json" % padded, "%s.json" % padded):
            p = os.path.join(d, name)
            if os.path.isfile(p):
                try:
                    size = os.path.getsize(p)
                except OSError:
                    size = 0
                if size > min_bytes:
                    return "SEC_COMPANYFACTS_CACHED"
                best = "SEC_COMPANYFACTS_EMPTY_STUB"
    return best


def _simfin_statement_availability(failure_reason: Optional[str], usable_factors: List[str]) -> str:
    if usable_factors:
        return "STATEMENTS_IN_WINDOW"
    if failure_reason == "MAPPED_NO_STATEMENTS_IN_WINDOW":
        return "MAPPED_NO_STATEMENTS_IN_WINDOW"
    if failure_reason == "BANK_EXCLUDED":
        return "BANK_SCHEMA_EXCLUDED"
    if failure_reason == "UNMAPPED":
        return "UNMAPPED"
    return "NONE"


def _base_root(base_symbol: str) -> str:
    """Root symbol without a share-class qualifier (AGR.B -> AGR)."""
    return (base_symbol or "").split(".")[0]


# --------------------------------------------------------------------------- #
# trial sample rows
# --------------------------------------------------------------------------- #
def build_trial_sample_rows(cfg: Dict[str, Any]) -> Dict[str, Any]:
    sample = load_phase30c_sample(cfg)
    c30_run_id = sample.get("_run_id")
    adequacy = load_sample_adequacy(cfg)
    master = load_security_master_index(cfg, c30_run_id)
    sec_removed = load_sec_asfiled_removed(cfg, c30_run_id)
    cf_dirs = _companyfacts_dirs(cfg)

    adq_by_ticker: Dict[str, Dict[str, Any]] = {}
    for grp in ("current", "removed"):
        for r in adequacy.get(grp, []):
            adq_by_ticker[r["ticker"]] = r

    def row_for(entry: Dict[str, Any], group: str) -> List[str]:
        tk = entry["ticker"]
        base = entry.get("base_symbol") or ""
        adq = adq_by_ticker.get(tk, {})
        mrow = master.get(tk, {})
        cik = entry.get("cik") or mrow.get("cik") or ""
        simfin_id = adq.get("simfin_id")
        usable = adq.get("usable_factors") or []
        failure = adq.get("failure_reason")
        # mapping ambiguity: prefer the descriptive identity note, then the SimFin note
        ambiguity = entry.get("mapping_ambiguity") or adq.get("map_ambiguity") or ""
        removal_decade = sfa._decade_of(entry.get("delisting_month")) if group == "removed" else ""
        last_membership = mrow.get("last_member_month") or ""
        delisting_date = entry.get("last_quoted_date") or entry.get("delisting_month") or ""
        sec_avail = sec_statement_availability(cik or None, cf_dirs)
        # corroborate removed SEC availability with the 30C as-filed set
        if group == "removed" and tk in sec_removed and sec_avail in (
            "NO_SEC_COMPANYFACTS_CACHED", "SEC_COMPANYFACTS_EMPTY_STUB"):
            sec_avail = "SEC_ASFILED_RECONSTRUCTED"
        simfin_avail = _simfin_statement_availability(failure, usable)
        return [
            group,
            tk,
            base,
            _base_root(base),
            entry.get("security_name") or "",
            "current" if group == "current" else "removed",
            removal_decade or "",
            entry.get("first_member_month") or "",
            last_membership,
            delisting_date,
            tk,  # norgate_identifier == panel symbol (Norgate delisted convention)
            cik,
            simfin_id or "",
            adq.get("map_confidence") or entry.get("cik_confidence") or "",
            ambiguity,
            sec_avail,
            simfin_avail,
            failure or "",
        ]

    rows: List[List[str]] = []
    for e in sample.get("current", []):
        rows.append(row_for(e, "current"))
    for e in sample.get("removed", []):
        rows.append(row_for(e, "removed"))

    n_current = len(sample.get("current", []))
    n_removed = len(sample.get("removed", []))
    return {
        "columns": list(TRIAL_SAMPLE_COLUMNS),
        "rows": rows,
        "counts": {"total": len(rows), "current": n_current, "removed": n_removed},
        "sample_hash": sample.get("sample_hash"),
        "security_master_hash": sample.get("security_master_hash"),
        "phase30c_run_id": c30_run_id,
        "phase30c1_run_id": adequacy.get("_run_id"),
        "removed_by_decade": sample.get("removed_by_decade"),
        "current_by_decade": sample.get("current_by_decade"),
    }


# --------------------------------------------------------------------------- #
# static deliverable content (gates / scoring / email)
# --------------------------------------------------------------------------- #
def acceptance_gates(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """The unchanged acceptance gates a returned trial sample is scored against."""
    return {
        "schema_version": VENDOR_PACK_SCHEMA_VERSION,
        "record_type": "VENDOR_ACCEPTANCE_GATES",
        "note": "Gates are unchanged from Phase 30C / 30C.1. A vendor sample must "
        "clear every gate to justify a paid historical acquisition.",
        "gates": {
            "removed_security_identity_mapping_min": {
                "threshold": 0.75,
                "type": "fraction",
                "pass_if": ">=",
                "description": "fraction of the 60 removed sample securities the vendor resolves to a stable identity",
            },
            "removed_security_usable_statements_min": {
                "threshold": 0.70,
                "type": "fraction",
                "pass_if": ">=",
                "description": "fraction of the 60 removed securities with all target-factor statement inputs present",
            },
            "availability_timestamp_completeness_min": {
                "threshold": 0.95,
                "type": "fraction",
                "pass_if": ">=",
                "description": "fraction of returned statement rows carrying a real filing/publication/acceptance date",
            },
            "stable_identifier_collision_rate_max": {
                "threshold": 0.0,
                "type": "fraction",
                "pass_if": "==",
                "description": "two distinct entities must never share one stable identifier (no reused-ticker collisions)",
            },
            "projected_global_member_month_coverage_min": {
                "threshold": 0.60,
                "type": "fraction",
                "pass_if": ">=",
                "description": "projected full-universe member-month coverage after integrating the vendor data (unchanged 60% gate)",
            },
            "removed_name_representation_min": {
                "threshold": 0.20,
                "type": "fraction",
                "pass_if": ">=",
                "description": "removed names must be at least 20% of covered member-months (unchanged; never weakened)",
            },
            "material_coverage_all_decades": {
                "threshold": ["2000s", "2010s", "2020s"],
                "type": "enum_set",
                "pass_if": "all_present",
                "description": "material coverage required in each of the 2000s, 2010s and 2020s",
            },
            "no_future_conditioned_universe": {
                "threshold": True,
                "type": "boolean",
                "pass_if": "==",
                "description": "the universe must not be conditioned on future survival",
            },
            "no_ticker_only_mapping_for_removed": {
                "threshold": True,
                "type": "boolean",
                "pass_if": "==",
                "description": "removed securities must be mapped by stable id/CIK, never by base ticker alone",
            },
            "original_as_reported_or_versioned_vintages": {
                "threshold": True,
                "type": "boolean",
                "pass_if": "==",
                "description": "values must be original-as-reported or carry explicit historical vintages",
            },
            "licensing_permits_internal_research_and_paper_trading": {
                "threshold": True,
                "type": "boolean",
                "pass_if": "==",
                "description": "licence must permit internal quantitative research and paper trading",
            },
        },
        "source_gates_reference": {
            "coverage_gates": cfg.get("coverage_gates"),
            "sector_history": cfg.get("sector_history"),
            "phase": "unchanged from Phase 30C / 30C.1",
        },
        "safety": SAFETY_CONTRACT,
    }


def scoring_instructions_md(sample_summary: Dict[str, Any]) -> str:
    counts = sample_summary["counts"]
    return """# Trial Scoring Instructions — Historical Fundamentals Evaluation

This document defines, in advance, exactly how a returned trial sample will be
scored. Scoring is mechanical and identical for every provider. A provider
either clears every gate or the trial fails; there is no partial acceptance.

## Inputs

* `historical_fundamentals_trial_sample.csv` — the fixed request universe:
  **{total} securities ({removed} removed + {current} current controls)**. This
  is the exact deterministic Phase 30C selection; it is never changed to favour
  a provider.
* `required_vendor_fields.csv` — the field contract.
* `vendor_response_template.csv` — the required response grain
  (security x fiscal period x vintage).
* `vendor_acceptance_gates.json` — the numeric gates below.

## Scoring steps

1. **Identity resolution.** For each of the 60 removed securities, check whether
   the provider resolved it to a stable company/security identifier using CIK or
   a stable id — never by base ticker alone. Reused base tickers that resolve to
   a different entity score as *unresolved*. Compute the removed-security
   identity-mapping rate. Gate: >= 0.75. The 20 current controls are scored the
   same way but do not set the gate.

2. **Point-in-time (PIT) audit.** For every returned statement row confirm a
   real filing/publication/acceptance date and an original-vs-restated (or
   explicit vintage) marker. Rows dated only by fiscal-period end, or with no
   vintage marker, fail the audit. Gate: availability-timestamp completeness
   >= 0.95. Any value that embeds a later restatement without a vintage marker
   is rejected.

3. **Statement completeness.** A security has usable statements when, for at
   least one in-universe fiscal period, all inputs to the three target factors
   are present: revenue and cost-of-revenue (or gross profit); total assets;
   operating cash flow; capital expenditures; net income. Compute the
   removed-security usable-statement rate. Gate: >= 0.70.

4. **Removed-company coverage.** Confirm delisted/removed names carry the same
   statement depth and history as survivors. Removed names must be at least 20%
   of covered member-months. Gate: removed-name representation >= 0.20.

5. **Coverage by decade.** Bucket the covered member-months into the 2000s,
   2010s and 2020s. Material coverage is required in **all three** decades; a
   single-decade sample (the free-SimFin failure mode) fails here.

6. **Factor reconstructability.** Recompute, with the committed definitions and
   no changes, on the returned data:
   * `gross_profitability = gross_profit / total_assets`
   * `fcf_to_assets = (operating_cash_flow - capex) / total_assets`
   * `operating_accruals = (net_income - operating_cash_flow) / total_assets`
   using each value's availability date for point-in-time formation. Document
   sign conventions for cost-of-revenue and capex.

7. **Estimated full-universe member-month coverage.** Extrapolate the trial
   mapping + statement rates onto the full survivorship-free universe and
   project global member-month coverage. Gate: projected coverage >= 0.60.

8. **Licensing.** Confirm the licence permits internal quantitative research and
   paper trading.

## Pass / fail decision

The trial **passes** only if every gate in `vendor_acceptance_gates.json` is
met: removed identity mapping >= 0.75, removed usable statements >= 0.70,
availability timestamps >= 0.95, stable-identifier collision rate == 0, projected
global member-month coverage >= 0.60, removed-name representation >= 0.20,
material coverage in all three decades, no future-conditioned universe, no
ticker-only mapping for removed names, original-as-reported or versioned
vintages, and a compatible licence. Otherwise the trial **fails** and no paid
acquisition proceeds.

This evaluation is research-only: no order, broker, automation, model promotion,
or operational change results from it.
""".format(**counts)


def request_email_txt(sample_summary: Dict[str, Any]) -> str:
    counts = sample_summary["counts"]
    return """Subject: Evaluation request — survivorship-free historical fundamentals (delisted + current)

Hello,

We run an internal, paper-only quantitative equity research effort and are
evaluating providers of historical company fundamentals. Our binding constraint
is point-in-time, survivorship-free depth: we need fundamentals for securities
that were delisted, acquired or otherwise removed from the investable universe,
back through the 2000s, not only for currently listed companies.

To evaluate any provider on identical terms we have prepared a small, fixed
evaluation pack (attached) and would appreciate a response populated against it:

  * historical_fundamentals_trial_sample.csv — a fixed {total}-security sample
    ({removed} removed/delisted + {current} current controls) with the identity
    fields we already hold (stable id, historical ticker, CIK where known,
    delisting date).
  * required_vendor_fields.csv — the fields we require per statement observation,
    with point-in-time expectations and accepted aliases.
  * vendor_response_template.csv — the exact response grain
    (security x fiscal period x vintage) to fill in.
  * vendor_acceptance_gates.json and trial_scoring_instructions.md — the
    criteria and mechanical scoring we will apply, shared up front.

The essentials we are checking:

  * Coverage of removed/delisted names with the same statement depth and history
    as survivors, materially across the 2000s, 2010s and 2020s.
  * Point-in-time values: original-as-reported, or explicit historical vintages,
    each with a real filing/publication/acceptance date (fiscal-period end alone
    is not sufficient).
  * Stable identifiers that are never reused across distinct entities, so a
    reused ticker cannot collide two companies.
  * Core statement lines: revenue, cost of revenue (or gross profit), net
    income, total assets, operating cash flow and capital expenditures, plus
    point-in-time sector.
  * A licence permitting internal quantitative research and paper trading.

We are not requesting a live-trading or redistribution licence and are not
committing to a purchase at this stage — this is a like-for-like evaluation. If
you can populate the response template for the attached sample, we can score it
against the shared gates and follow up.

Thank you,
Quantitative Research
""".format(**counts)


# --------------------------------------------------------------------------- #
# CSV / hashing helpers
# --------------------------------------------------------------------------- #
def _csv_text(header: List[str], rows: List[List[str]]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(header)
    for r in rows:
        w.writerow(r)
    return buf.getvalue()


_CONTENT_SECRET_RE = re.compile(
    r"(?i)(api[_-]?key|secret|password|passwd|access[_-]?token|auth[_-]?token|"
    r"private[_-]?key|bearer|credential)\s*[:=]\s*\S"
)
_PEM_RE = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")


def _scan_text_for_secrets(text: str) -> List[str]:
    hits = [m.group(0)[:40] for m in _CONTENT_SECRET_RE.finditer(text)]
    hits.extend(m.group(0) for m in _PEM_RE.finditer(text))
    return hits


def _scan_text_for_paper_trader(text: str) -> List[str]:
    low = text.lower()
    return [tok for tok in hc._PAPER_TRADER_FORBIDDEN if tok in low]


# --------------------------------------------------------------------------- #
# build + verify
# --------------------------------------------------------------------------- #
def _source_hashes(cfg: Dict[str, Any], summary: Dict[str, Any]) -> Dict[str, Optional[str]]:
    sources = cfg.get("sources") or {}
    c30 = summary["phase30c_run_id"]
    c31 = summary["phase30c1_run_id"]
    runs30 = _resolve(sources["phase30c_runs_root"])
    runs31 = _resolve(sources["phase30c1_runs_root"])
    out: Dict[str, Optional[str]] = {
        "phase30c_sample_manifest": fb._file_sha256(os.path.join(runs30, c30, "sample_manifest.json")),
        "phase30c_security_master": fb._file_sha256(os.path.join(runs30, c30, "security_master.json")),
        "phase30c_sec_asfiled_manifest": fb._file_sha256(
            os.path.join(runs30, c30, "normalized_manifests", "sec_asfiled.json")),
        "phase30c1_sample_adequacy": fb._file_sha256(os.path.join(runs31, c31, "sample_adequacy.json")),
    }
    for key in ("simfin_companies", "simfin_income", "simfin_balance", "simfin_cashflow"):
        spec = sources.get(key)
        if spec:
            out[key] = fb._file_sha256(_resolve(spec))
    return out


def build_pack(cfg: Dict[str, Any], output_root: str) -> Dict[str, Any]:
    verdict = validate_config(cfg)
    if not verdict["accepted"]:
        return {"status": "INVALID_CONFIG", "violations": verdict["violations"],
                "config_hash": verdict["config_hash"]}

    out_dir = os.path.join(output_root, cfg.get("output_subdir") or DEFAULT_OUTPUT_SUBDIR)
    os.makedirs(out_dir, exist_ok=True)

    summary = build_trial_sample_rows(cfg)
    if summary["counts"]["removed"] != 60 or summary["counts"]["current"] != 20:
        raise VendorPackError(
            "sample must be exactly 60 removed + 20 current, got %s" % summary["counts"])

    generated_at = cfg["generated_at"]

    # ---- generate the six content deliverables ----------------------------
    texts: Dict[str, str] = {}
    texts[TRIAL_SAMPLE_CSV] = _csv_text(summary["columns"], summary["rows"])
    texts[REQUIRED_FIELDS_CSV] = _csv_text(
        list(REQUIRED_FIELDS_COLUMNS),
        [[f[c] for c in REQUIRED_FIELDS_COLUMNS] for f in REQUIRED_VENDOR_FIELDS])
    texts[RESPONSE_TEMPLATE_CSV] = _csv_text(list(RESPONSE_TEMPLATE_COLUMNS), [])
    texts[SCORING_MD] = scoring_instructions_md(summary)
    texts[REQUEST_EMAIL_TXT] = request_email_txt(summary)
    gates_obj = acceptance_gates(cfg)

    # ---- secret / Paper Trader content scan (before anything is trusted) ---
    scan_targets = dict(texts)
    import json as _json

    scan_targets[ACCEPTANCE_GATES_JSON] = _json.dumps(gates_obj, sort_keys=True)
    secret_hits: Dict[str, List[str]] = {}
    pt_hits: Dict[str, List[str]] = {}
    for name, text in scan_targets.items():
        sh = _scan_text_for_secrets(text)
        ph = _scan_text_for_paper_trader(text)
        if sh:
            secret_hits[name] = sh
        if ph:
            pt_hits[name] = ph
    if secret_hits:
        raise VendorPackError("refusing to write secret-looking content: %s" % secret_hits)
    if pt_hits:
        raise VendorPackError("refusing to write Paper Trader references: %s" % pt_hits)

    # ---- write the six content deliverables -------------------------------
    from pathlib import Path

    for name in (TRIAL_SAMPLE_CSV, REQUIRED_FIELDS_CSV, RESPONSE_TEMPLATE_CSV,
                 SCORING_MD, REQUEST_EMAIL_TXT):
        write_text_atomic(Path(out_dir) / name, texts[name])
    write_json_atomic(Path(out_dir) / ACCEPTANCE_GATES_JSON, gates_obj)

    # ---- file hashes (bytes on disk) --------------------------------------
    file_hashes: Dict[str, Optional[str]] = {}
    file_bytes: Dict[str, int] = {}
    for name in (TRIAL_SAMPLE_CSV, REQUIRED_FIELDS_CSV, ACCEPTANCE_GATES_JSON,
                 RESPONSE_TEMPLATE_CSV, SCORING_MD, REQUEST_EMAIL_TXT):
        p = os.path.join(out_dir, name)
        file_hashes[name] = fb._file_sha256(p)
        file_bytes[name] = os.path.getsize(p)

    source_hashes = _source_hashes(cfg, summary)

    row_counts = {
        "trial_sample_total": summary["counts"]["total"],
        "trial_sample_current": summary["counts"]["current"],
        "trial_sample_removed": summary["counts"]["removed"],
        "required_vendor_fields": len(REQUIRED_VENDOR_FIELDS),
        "response_template_columns": len(RESPONSE_TEMPLATE_COLUMNS),
        "acceptance_gates": len(gates_obj["gates"]),
    }

    # content hash excludes generated_at + commit so the DATA is deterministic
    package_content_hash = content_hash({
        "file_hashes": file_hashes,
        "source_hashes": source_hashes,
        "row_counts": row_counts,
        "sample_hash": summary["sample_hash"],
        "source_run_ids": {"phase30c": summary["phase30c_run_id"],
                           "phase30c1": summary["phase30c1_run_id"]},
    })

    manifest = {
        "schema_version": VENDOR_PACK_SCHEMA_VERSION,
        "record_type": "VENDOR_EVALUATION_PACK_MANIFEST",
        "name": cfg.get("name"),
        "generated_at": generated_at,
        "code_commit": read_git_commit(),
        "config_hash": verdict["config_hash"],
        "data_cutoff": (cfg.get("data") or {}).get("data_cutoff"),
        "vendors_evaluated": cfg.get("vendors") or [],
        "source_run_ids": {"phase30c": summary["phase30c_run_id"],
                           "phase30c1": summary["phase30c1_run_id"]},
        "source_hashes": source_hashes,
        "sample_hash": summary["sample_hash"],
        "security_master_hash": summary["security_master_hash"],
        "row_counts": row_counts,
        "decades_represented": {"removed": summary["removed_by_decade"],
                                "current": summary["current_by_decade"]},
        "files": [{"name": n, "sha256": file_hashes[n], "bytes": file_bytes[n]}
                  for n in (TRIAL_SAMPLE_CSV, REQUIRED_FIELDS_CSV, ACCEPTANCE_GATES_JSON,
                            RESPONSE_TEMPLATE_CSV, SCORING_MD, REQUEST_EMAIL_TXT)],
        "file_hashes": file_hashes,
        "package_content_hash": package_content_hash,
        "sensitive_content_scan": {
            "clean": True,
            "sensitive_key_hits": [],
            "sensitive_value_hits": [],
            "forbidden_reference_hits": [],
            "files_scanned": sorted(scan_targets.keys()),
            "method": "artifact_store key scan on JSON + regex value scan on all file contents",
        },
        "safety": SAFETY_CONTRACT,
        "output_dir": out_dir,
    }
    write_json_atomic(Path(out_dir) / PACKAGE_MANIFEST_JSON, manifest)

    return {
        "status": "READY",
        "output_dir": out_dir,
        "files": list(DELIVERABLES),
        "row_counts": row_counts,
        "sample_hash": summary["sample_hash"],
        "sample_hash_matches_phase30c": summary["sample_hash"] is not None,
        "package_content_hash": package_content_hash,
        "code_commit": manifest["code_commit"],
        "config_hash": verdict["config_hash"],
    }


def verify_pack(output_root: str, cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Re-read the written pack and confirm: 60/20 counts, sample-hash match to
    the source Phase 30C manifest, recomputed file hashes match the manifest, and
    no secret or Paper Trader content."""
    out_dir = os.path.join(output_root, cfg.get("output_subdir") or DEFAULT_OUTPUT_SUBDIR)
    problems: List[str] = []

    manifest_path = os.path.join(out_dir, PACKAGE_MANIFEST_JSON)
    if not os.path.isfile(manifest_path):
        return {"ok": False, "problems": ["manifest missing at %s" % manifest_path]}
    manifest = read_json(manifest_path)

    # counts + sample hash from the actual CSV vs the source-of-truth manifest
    tsv_path = os.path.join(out_dir, TRIAL_SAMPLE_CSV)
    with open(tsv_path, "r", encoding="utf-8", newline="") as fh:
        reader = list(csv.DictReader(fh))
    n_removed = sum(1 for r in reader if r["sample_group"] == "removed")
    n_current = sum(1 for r in reader if r["sample_group"] == "current")
    if (n_removed, n_current) != (60, 20):
        problems.append("trial sample is %d removed + %d current (want 60 + 20)" % (n_removed, n_current))

    src_sample = load_phase30c_sample(cfg)
    if manifest.get("sample_hash") != src_sample.get("sample_hash"):
        problems.append("sample_hash %s != source Phase 30C %s" % (
            manifest.get("sample_hash"), src_sample.get("sample_hash")))

    # recompute the six file hashes
    for name, want in (manifest.get("file_hashes") or {}).items():
        got = fb._file_sha256(os.path.join(out_dir, name))
        if got != want:
            problems.append("file hash drift on %s" % name)

    # secret / Paper Trader scan across all files
    secret_files: List[str] = []
    pt_files: List[str] = []
    for name in DELIVERABLES:
        p = os.path.join(out_dir, name)
        if not os.path.isfile(p):
            problems.append("deliverable missing: %s" % name)
            continue
        text = open(p, "r", encoding="utf-8").read()
        if _scan_text_for_secrets(text):
            secret_files.append(name)
        if _scan_text_for_paper_trader(text):
            pt_files.append(name)
    if secret_files:
        problems.append("secret-looking content in %s" % secret_files)
    if pt_files:
        problems.append("Paper Trader reference in %s" % pt_files)

    return {
        "ok": not problems,
        "problems": problems,
        "output_dir": out_dir,
        "trial_sample_counts": {"removed": n_removed, "current": n_current},
        "sample_hash": manifest.get("sample_hash"),
        "sample_hash_matches_phase30c": manifest.get("sample_hash") == src_sample.get("sample_hash"),
        "package_content_hash": manifest.get("package_content_hash"),
        "no_secrets": not secret_files,
        "no_paper_trader": not pt_files,
    }
