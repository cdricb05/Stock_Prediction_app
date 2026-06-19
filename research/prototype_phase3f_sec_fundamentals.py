"""Phase 3-F - SEC Fundamentals Source Selection and Free Prototype.

This is a tiny, **read-only research prototype**, not a training phase and not a production
ingestion. After Phase 3-E recommended PROCEED_TO_VENDOR_SELECTION_AND_FREE_SOURCE_PROTOTYPE,
this script proves the source-selection and normalization path for fundamentals using only
official SEC public JSON endpoints, for a very small ticker sample (<= 5 names). It answers a
single practical question:

    "Can the Phase 3-E schema + ingestion contract be filled, point-in-time, from a free public
     source for a tiny sample - and what is the safest next step?"

It reads the committed Phase 3-E feasibility result, schema catalog, ingestion contract, and
provider matrix, plus the current-as-of sector map. It selects SEC public companyfacts /
submissions as the first free prototype source, fetches a tiny sample (throttled, cached),
normalizes company identity + a small subset of fundamentals into the Phase 3-E schema, validates
point-in-time / availability-timestamp handling, writes a data-quality report, and emits a Phase
3-G recommendation.

Network policy. Network is used ONLY for official SEC public JSON endpoints
(www.sec.gov / data.sec.gov) needed for this tiny prototype, with a declared User-Agent, a
minimum 0.25s gap between requests, and a hard cap of 20 requests total. Raw responses are cached
(pruned to the mapped concepts so the sample stays Git-small) under
research/output/phase3f_sec_fundamentals_sample/raw/ and re-read from cache on subsequent runs.
No paid vendor is called, no data is purchased, and no third-party market-data vendor package
is used.

Scope and safety. This prototype trains no model, creates no production model candidate, writes
no deployable model artifact, computes no model features or labels, touches no database, runs no
migration, writes nothing to the D: drive, reads no D: price CSV, places no orders, and trades
nothing. It is research-only and writes only the small sample outputs listed below. If the
network and cache both fail it writes a clearly-marked blocked result instead of crashing.
"""
from __future__ import annotations

import csv
import gzip
import io
import json
import os
import time
import urllib.error
import urllib.request
from datetime import datetime

PHASE = "3-F"

# --------------------------------------------------------------------------- #
# Paths (repo-local only; nothing on the D: drive is read or written here)
# --------------------------------------------------------------------------- #
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_OUT_DIR = os.path.join(_REPO_ROOT, "research", "output")
_IN_DIR = os.path.join(_REPO_ROOT, "research", "input")
_SAMPLE_DIR = os.path.join(_OUT_DIR, "phase3f_sec_fundamentals_sample")
_RAW_DIR = os.path.join(_SAMPLE_DIR, "raw")

# Inputs (committed Phase 3-E artifacts + current-as-of sector map).
PHASE3E_JSON = os.path.join(_OUT_DIR, "phase3e_fundamentals_earnings_feasibility.json")
SCHEMA_CATALOG_CSV = os.path.join(_OUT_DIR, "phase3e_external_data_schema_catalog.csv")
INGESTION_CONTRACT_JSON = os.path.join(_OUT_DIR, "phase3e_ingestion_contract.json")
PROVIDER_MATRIX_CSV = os.path.join(_OUT_DIR, "phase3e_provider_requirements_matrix.csv")
SECTOR_MAP_CSV = os.path.join(_IN_DIR, "phase2k_p_sector_map_current.csv")

# Outputs.
PROTOTYPE_JSON = os.path.join(_OUT_DIR, "phase3f_sec_fundamentals_prototype.json")
COMPANY_IDENTITY_CSV = os.path.join(_SAMPLE_DIR, "company_identity_sample.csv")
FUNDAMENTALS_CSV = os.path.join(_SAMPLE_DIR, "fundamentals_sample.csv")
DATA_QUALITY_JSON = os.path.join(_SAMPLE_DIR, "data_quality_report.json")

# --------------------------------------------------------------------------- #
# Tiny sample + SEC access policy
# --------------------------------------------------------------------------- #
SAMPLE_TICKERS = ["AAPL", "MSFT", "JPM", "XOM", "UNH"]
MAX_SAMPLE_TICKERS = 5

# Declared fair-access User-Agent (research contact); required by SEC.
SEC_USER_AGENT = "PaperTraderResearch/Phase3F cedric.binisti.research@example.com"
SEC_ACCEPT_ENCODING = "gzip, deflate"

# Only these official SEC public hosts / patterns are allowed. No other domains.
ALLOWED_SEC_HOSTS = ("www.sec.gov", "data.sec.gov")
COMPANY_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL_TEMPLATE = "https://data.sec.gov/submissions/CIK{cik10}.json"
COMPANYFACTS_URL_TEMPLATE = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik10}.json"

MIN_REQUEST_INTERVAL_S = 0.25
MAX_TOTAL_REQUESTS = 20

# Filing forms we keep (periodic statements only).
_KEEP_FORMS = {"10-K", "10-Q", "10-K/A", "10-Q/A"}
# Caps to keep the cached sample small enough for Git.
_CACHE_FACT_CAP = 24       # most-recent facts kept per concept-unit in the cached payload
_CACHE_SUBM_CAP = 40       # most-recent periodic filings kept in the cached submissions payload
_OUTPUT_PERIOD_CAP = 8     # distinct fiscal periods emitted per (ticker, normalized_field)

SOURCE_NAME = "SEC EDGAR (public companyfacts + submissions JSON)"

# XBRL us-gaap concept mappings (namespace key is the parent "us-gaap"; concept names below).
CONCEPT_MAP = {
    "revenue": ["Revenues", "SalesRevenueNet"],
    "net_income": ["NetIncomeLoss"],
    "operating_income": ["OperatingIncomeLoss"],
    "eps_diluted": ["EarningsPerShareDiluted"],
    "total_assets": ["Assets"],
    "total_liabilities": ["Liabilities"],
    "shareholder_equity": ["StockholdersEquity"],
    "operating_cash_flow": ["NetCashProvidedByUsedInOperatingActivities"],
    "capital_expenditures": ["PaymentsToAcquirePropertyPlantAndEquipment"],
}
# free_cash_flow is derived (operating_cash_flow - capital_expenditures) only when both exist.
DERIVED_FIELDS = ["free_cash_flow"]
ALL_ATTEMPTED_FIELDS = list(CONCEPT_MAP.keys()) + DERIVED_FIELDS
_WANTED_CONCEPTS = sorted({c for cs in CONCEPT_MAP.values() for c in cs})

IDENTITY_COLUMNS = [
    "ticker", "company_name", "cik", "sector", "industry", "source",
    "effective_from", "effective_to",
]
FUNDAMENTALS_COLUMNS = [
    "ticker", "cik", "fiscal_period_end", "fiscal_year", "fiscal_period", "form", "filed",
    "frame", "source_concept", "normalized_field", "value", "unit", "source",
    "availability_datetime", "point_in_time_usable", "restatement_policy", "validation_note",
]

# Recommendation vocabulary.
REC_SUCCESS = "SEC_FUNDAMENTALS_PROTOTYPE_SUCCESS"
REC_PARTIAL = "SEC_FUNDAMENTALS_PROTOTYPE_PARTIAL_SUCCESS"
REC_BLOCKED = "SEC_FUNDAMENTALS_PROTOTYPE_BLOCKED"
REC_REJECTED = "SEC_FUNDAMENTALS_SOURCE_REJECTED"
ALLOWED_RECOMMENDATIONS = [REC_SUCCESS, REC_PARTIAL, REC_BLOCKED, REC_REJECTED]

PHASE3E_EXPECTED_RECOMMENDATION = "PROCEED_TO_VENDOR_SELECTION_AND_FREE_SOURCE_PROTOTYPE"

_RESTATEMENT_POLICY = (
    "first-reported preferred (earliest filed value kept per fiscal period); companyfacts may "
    "include later restated values - treated as caveated, never back-dated")


# --------------------------------------------------------------------------- #
# SEC client (throttled, capped, host-restricted, with pruned local caching)
# --------------------------------------------------------------------------- #
class SecClient:
    """Minimal SEC public-JSON client: host-restricted, throttled, capped, cache-first."""

    def __init__(self):
        self.network_requests = 0
        self.cache_hits = 0
        self.request_count = 0
        self.errors = []
        self._last_request_time = 0.0

    @staticmethod
    def _host_allowed(url):
        # Only official SEC public hosts are permitted.
        for host in ALLOWED_SEC_HOSTS:
            if url.startswith("https://" + host + "/"):
                return True
        return False

    def _throttle(self):
        elapsed = time.time() - self._last_request_time
        if elapsed < MIN_REQUEST_INTERVAL_S:
            time.sleep(MIN_REQUEST_INTERVAL_S - elapsed)

    def fetch_json(self, url):
        """Fetch + parse JSON from an allowed SEC URL. Raises on any failure."""
        if not self._host_allowed(url):
            raise ValueError("refusing non-SEC URL: " + url)
        if self.request_count >= MAX_TOTAL_REQUESTS:
            raise RuntimeError("SEC request cap reached (%d)" % MAX_TOTAL_REQUESTS)
        self._throttle()
        req = urllib.request.Request(url, headers={
            "User-Agent": SEC_USER_AGENT,
            "Accept-Encoding": SEC_ACCEPT_ENCODING,
            "Accept": "application/json",
        })
        self.request_count += 1
        self._last_request_time = time.time()
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
            if (resp.headers.get("Content-Encoding") or "").lower() == "gzip":
                raw = gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
        self.network_requests += 1
        return json.loads(raw.decode("utf-8"))

    def get_cached_or_fetch(self, url, cache_path, prune_fn):
        """Return (data, origin). Reads pruned cache if present; else fetches, prunes, caches."""
        if os.path.isfile(cache_path):
            with open(cache_path, "r", encoding="utf-8") as f:
                self.cache_hits += 1
                return json.load(f), "cache"
        full = self.fetch_json(url)
        pruned = prune_fn(full)
        os.makedirs(os.path.dirname(cache_path), exist_ok=True)
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(pruned, f, indent=1)
        return pruned, "network"


# --------------------------------------------------------------------------- #
# Pruning (keep cached payloads small + Git-friendly, but lossless for our schema)
# --------------------------------------------------------------------------- #
def _prune_company_tickers(full):
    """Keep only the sample tickers' {cik_str, ticker, title} entries."""
    want = set(SAMPLE_TICKERS)
    out = {}
    i = 0
    for v in full.values():
        if (v.get("ticker") or "").upper() in want:
            out[str(i)] = {
                "cik_str": v.get("cik_str"),
                "ticker": v.get("ticker"),
                "title": v.get("title"),
            }
            i += 1
    return out


def _prune_submissions(full):
    recent = (full.get("filings", {}) or {}).get("recent", {}) or {}
    keys = ["accessionNumber", "form", "filingDate", "acceptanceDateTime", "reportDate"]
    arrs = {k: recent.get(k, []) or [] for k in keys}
    n = len(arrs["accessionNumber"])
    forms = arrs["form"]
    idx = [i for i in range(n) if i < len(forms) and forms[i] in _KEEP_FORMS][:_CACHE_SUBM_CAP]
    pruned_recent = {k: [arrs[k][i] for i in idx if i < len(arrs[k])] for k in keys}
    return {
        "cik": full.get("cik"),
        "name": full.get("name"),
        "tickers": full.get("tickers"),
        "sicDescription": full.get("sicDescription"),
        "filings": {"recent": pruned_recent},
    }


def _prune_companyfacts(full):
    ug = (full.get("facts", {}) or {}).get("us-gaap", {}) or {}
    kept = {}
    for concept in _WANTED_CONCEPTS:
        node = ug.get(concept)
        if not node:
            continue
        pruned_units = {}
        for unit, facts in (node.get("units", {}) or {}).items():
            periodic = [f for f in facts if f.get("form") in _KEEP_FORMS]
            periodic.sort(key=lambda f: f.get("filed") or "")
            pruned_units[unit] = periodic[-_CACHE_FACT_CAP:]
        if pruned_units:
            kept[concept] = {"label": node.get("label"), "units": pruned_units}
    return {
        "cik": full.get("cik"),
        "entityName": full.get("entityName"),
        "facts": {"us-gaap": kept},
    }


# --------------------------------------------------------------------------- #
# Input loading + Phase 3-E confirmation
# --------------------------------------------------------------------------- #
def _load_json(path):
    if not os.path.isfile(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def confirm_phase3e(phase3e):
    """Confirm the Phase 3-E contract that gates this prototype."""
    if not phase3e:
        return {"all_confirmed": False, "phase3e_present": False}
    rec = phase3e.get("recommendation", {}) or {}
    nxt = phase3e.get("recommended_next_phase", {}) or {}
    checks = {
        "phase3e_present": True,
        "phase_is_3e": phase3e.get("phase") == "3-E",
        "recommendation_is_free_source_prototype":
            rec.get("recommendation") == PHASE3E_EXPECTED_RECOMMENDATION,
        "next_phase_is_3f": nxt.get("phase") == "3-F",
        "free_source_prototype_allowed": rec.get("free_source_prototype_allowed") is True,
        "provider_selection_required": rec.get("provider_selection_required") is True,
        "research_model_trained_false": phase3e.get("research_model_trained") is False,
        "production_model_trained_false": phase3e.get("production_model_trained") is False,
        "production_model_candidate_created_false":
            phase3e.get("production_model_candidate_created") is False,
        "deployable_model_artifact_written_false":
            phase3e.get("deployable_model_artifact_written") is False,
        "d_drive_read_false": phase3e.get("d_drive_read") is False,
        "network_used_false": phase3e.get("network_used") is False,
        "vendor_api_called_false": phase3e.get("vendor_api_called") is False,
        "data_purchase_made_false": phase3e.get("data_purchase_made") is False,
        "external_data_ingested_false": phase3e.get("external_data_ingested") is False,
    }
    checks["all_confirmed"] = all(checks.values())
    return checks


def confirm_ingestion_contract(contract):
    """Confirm the Phase 3-E ingestion contract gates this prototype must honor."""
    if not contract:
        return {"all_confirmed": False, "contract_present": False}
    tables = contract.get("normalized_tables", []) or []
    pit_cols = contract.get("point_in_time_columns", []) or []
    checks = {
        "contract_present": True,
        "has_company_identity_table": "company_identity" in tables,
        "has_fundamentals_table": "fundamentals" in tables,
        "no_database_write_by_default": contract.get("no_database_write_by_default") is True,
        "no_production_integration": contract.get("no_production_integration") is True,
        "pit_columns_include_accepted_datetime": "accepted_datetime" in pit_cols,
        "pit_columns_include_availability_datetime": "availability_datetime" in pit_cols,
    }
    checks["all_confirmed"] = all(checks.values())
    return checks


def _schema_catalog_present(path):
    if not os.path.isfile(path):
        return {"present": False}
    with open(path, "r", encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    families = {r.get("data_family") for r in rows}
    return {
        "present": True,
        "row_count": len(rows),
        "has_company_identity": "company_identity" in families,
        "has_fundamentals": "fundamentals" in families,
    }


def load_sector_map(path):
    """ticker -> {sector, industry, source, as_of_date}. Used only to attach sector/industry."""
    out = {}
    if not os.path.isfile(path):
        return out
    with open(path, "r", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            t = (row.get("ticker") or "").strip().upper()
            if t:
                out[t] = {
                    "sector": (row.get("sector") or "").strip(),
                    "industry": (row.get("industry") or "").strip(),
                    "source": (row.get("source") or "").strip(),
                    "as_of_date": (row.get("as_of_date") or "").strip(),
                }
    return out


# --------------------------------------------------------------------------- #
# Normalization
# --------------------------------------------------------------------------- #
def _company_ticker_index(company_tickers):
    """ticker(upper) -> (cik_int, title)."""
    idx = {}
    for v in (company_tickers or {}).values():
        t = (v.get("ticker") or "").upper()
        if t:
            idx[t] = (v.get("cik_str"), v.get("title"))
    return idx


def _accession_to_acceptance(submissions):
    recent = (submissions.get("filings", {}) or {}).get("recent", {}) or {}
    accns = recent.get("accessionNumber", []) or []
    accepted = recent.get("acceptanceDateTime", []) or []
    out = {}
    for i, accn in enumerate(accns):
        if i < len(accepted) and accepted[i]:
            out[accn] = accepted[i]
    return out


def _normalize_identity(ticker, cik_int, title, sector_info, as_of_date):
    return {
        "ticker": ticker,
        "company_name": title or "",
        "cik": ("%010d" % cik_int) if cik_int is not None else "",
        "sector": sector_info.get("sector", ""),
        "industry": sector_info.get("industry", ""),
        "source": SOURCE_NAME,
        # Identity is current-as-of (sector map is not point-in-time); record the as-of date.
        "effective_from": as_of_date or "",
        "effective_to": "",
    }


def _normalize_fundamentals_for_ticker(ticker, cik_int, companyfacts, acceptance_by_accn):
    """Emit normalized fundamentals rows for one ticker (first-reported, point-in-time)."""
    cik10 = ("%010d" % cik_int) if cik_int is not None else ""
    ug = (companyfacts.get("facts", {}) or {}).get("us-gaap", {}) or {}
    rows = []
    fields_found = set()

    # Index for free_cash_flow derivation: (period_end, fp, form) -> {field: (val, avail, pit)}.
    period_index = {}

    for normalized_field, concepts in CONCEPT_MAP.items():
        # First matching concept that is present wins (vendor-agnostic preference order).
        chosen_concept = None
        for concept in concepts:
            if concept in ug and (ug[concept].get("units") or {}):
                chosen_concept = concept
                break
        if not chosen_concept:
            continue

        units = ug[chosen_concept].get("units", {}) or {}
        # Prefer USD; for per-share metrics fall back to USD/shares or first available unit.
        unit = "USD" if "USD" in units else ("USD/shares" if "USD/shares" in units
                                             else next(iter(units), None))
        if unit is None:
            continue
        facts = units[unit]

        # Keep first-reported value per (period_end, fp, form): earliest filed wins.
        best = {}
        for f in facts:
            end = f.get("end")
            fp = f.get("fp")
            form = f.get("form")
            filed = f.get("filed")
            if not end or not fp or form not in _KEEP_FORMS:
                continue
            key = (end, fp, form)
            cur = best.get(key)
            if cur is None or (filed or "") < (cur.get("filed") or ""):
                best[key] = f

        # Cap to most-recent fiscal periods to keep the sample small.
        selected = sorted(best.values(), key=lambda f: f.get("end") or "")[-_OUTPUT_PERIOD_CAP:]
        for f in selected:
            end = f.get("end")
            fp = f.get("fp")
            form = f.get("form")
            filed = f.get("filed")
            accn = f.get("accn")
            accepted = acceptance_by_accn.get(accn)
            availability = accepted or filed or ""
            pit_usable = bool(availability)
            note = ("availability_datetime from filing acceptance" if accepted
                    else ("conservative: filed date used as availability_datetime"
                          if filed else "no availability timestamp - quarantined"))
            rows.append({
                "ticker": ticker,
                "cik": cik10,
                "fiscal_period_end": end,
                "fiscal_year": f.get("fy"),
                "fiscal_period": fp,
                "form": form,
                "filed": filed or "",
                "frame": f.get("frame") or "",
                "source_concept": "us-gaap:" + chosen_concept,
                "normalized_field": normalized_field,
                "value": f.get("val"),
                "unit": unit,
                "source": SOURCE_NAME,
                "availability_datetime": availability,
                "point_in_time_usable": pit_usable,
                "restatement_policy": _RESTATEMENT_POLICY,
                "validation_note": note,
            })
            fields_found.add(normalized_field)
            pkey = (end, fp, form)
            period_index.setdefault(pkey, {})[normalized_field] = (
                f.get("val"), availability, pit_usable)

    # Derive free_cash_flow = operating_cash_flow - capital_expenditures when both present.
    for (end, fp, form), fields in period_index.items():
        if "operating_cash_flow" in fields and "capital_expenditures" in fields:
            ocf_val, ocf_av, ocf_pit = fields["operating_cash_flow"]
            capex_val, capex_av, capex_pit = fields["capital_expenditures"]
            if ocf_val is None or capex_val is None:
                continue
            availability = max([a for a in (ocf_av, capex_av) if a] or [""])
            pit_usable = bool(availability) and ocf_pit and capex_pit
            rows.append({
                "ticker": ticker,
                "cik": cik10,
                "fiscal_period_end": end,
                "fiscal_year": None,
                "fiscal_period": fp,
                "form": form,
                "filed": "",
                "frame": "",
                "source_concept": ("derived:NetCashProvidedByUsedInOperatingActivities"
                                   "-PaymentsToAcquirePropertyPlantAndEquipment"),
                "normalized_field": "free_cash_flow",
                "value": ocf_val - capex_val,
                "unit": "USD",
                "source": SOURCE_NAME,
                "availability_datetime": availability,
                "point_in_time_usable": pit_usable,
                "restatement_policy": _RESTATEMENT_POLICY,
                "validation_note": "derived = operating_cash_flow - capital_expenditures; "
                                   "availability is the later of the two inputs",
            })
            fields_found.add("free_cash_flow")

    return rows, fields_found


# --------------------------------------------------------------------------- #
# Data quality
# --------------------------------------------------------------------------- #
def _round(x, n=4):
    try:
        return round(float(x), n)
    except (TypeError, ValueError):
        return 0.0


def build_data_quality_report(requested, processed, failed, raw_files, identity_rows,
                              fundamentals_rows, fields_found_by_ticker, client,
                              network_used, recommendation):
    total = len(fundamentals_rows)
    with_avail = sum(1 for r in fundamentals_rows if r.get("availability_datetime"))
    pit_usable = sum(1 for r in fundamentals_rows if r.get("point_in_time_usable"))

    # Duplicate-key check on the emitted output (point-in-time key).
    seen = {}
    for r in fundamentals_rows:
        k = (r["ticker"], r["normalized_field"], r["fiscal_period_end"],
             r["fiscal_period"], r["form"])
        seen[k] = seen.get(k, 0) + 1
    duplicate_key_count = sum(c - 1 for c in seen.values() if c > 1)

    missing_fields_by_ticker = {}
    for t in processed:
        found = fields_found_by_ticker.get(t, set())
        missing = [f for f in ALL_ATTEMPTED_FIELDS if f not in found]
        if missing:
            missing_fields_by_ticker[t] = missing

    return {
        "sample_tickers_requested": list(requested),
        "sample_tickers_processed": list(processed),
        "sample_tickers_failed": failed,
        "raw_files_written": sorted(raw_files),
        "identity_rows": len(identity_rows),
        "fundamentals_rows": total,
        "fields_attempted": list(ALL_ATTEMPTED_FIELDS),
        "fields_found": sorted({f for s in fields_found_by_ticker.values() for f in s}),
        "missing_fields_by_ticker": missing_fields_by_ticker,
        "availability_datetime_coverage": _round(with_avail / total) if total else 0.0,
        "point_in_time_usable_fraction": _round(pit_usable / total) if total else 0.0,
        "duplicate_key_count": duplicate_key_count,
        "source_limitations": [
            "SEC public data covers fundamentals only; it does NOT provide earnings consensus "
            "or analyst estimate revisions.",
            "companyfacts may include later restated values; this prototype keeps the "
            "first-reported (earliest filed) value per fiscal period and treats restatements as "
            "caveated.",
            "cached payloads are pruned to the mapped concepts and most-recent periods to stay "
            "Git-small; this is a tiny prototype sample, not full historical coverage.",
            "the sector / industry attached to identity is current-as-of (not point-in-time), so "
            "downstream results remain survivorship-caveated.",
        ],
        "sec_request_count": client.request_count,
        "used_cache_count": client.cache_hits,
        "network_request_count": client.network_requests,
        "network_used": network_used,
        "errors": client.errors,
        "recommendation": recommendation,
    }


# --------------------------------------------------------------------------- #
# Decision logic
# --------------------------------------------------------------------------- #
def decide(processed, fundamentals_rows, dq, identity_rows):
    """Apply the conservative Phase 3-F decision rules."""
    n_processed = len(processed)
    n_rows = len(fundamentals_rows)
    avail = dq["availability_datetime_coverage"]
    pit = dq["point_in_time_usable_fraction"]
    dup = dq["duplicate_key_count"]

    if n_rows == 0 or n_processed == 0:
        # Could not build a meaningful prototype (network/cache/source failure).
        if dq["network_request_count"] == 0 and dq["used_cache_count"] == 0:
            return REC_BLOCKED
        # Data was reachable but yielded no usable point-in-time fundamentals for the schema.
        if dq["sec_request_count"] > 0 and n_rows == 0:
            return REC_REJECTED
        return REC_BLOCKED

    if (n_processed >= 4 and identity_rows and n_rows > 0 and avail >= 0.95 and pit >= 0.95
            and dup == 0):
        return REC_SUCCESS
    if n_processed >= 2 and n_rows > 0:
        return REC_PARTIAL
    return REC_BLOCKED


def build_recommendation(recommendation):
    reason = {
        REC_SUCCESS: (
            "SEC public companyfacts + submissions filled the Phase 3-E company_identity and "
            "fundamentals schema point-in-time for at least 4 of 5 sample tickers, with full "
            "availability-timestamp coverage and no duplicate keys. The free source is validated "
            "for fundamentals; expand to a controlled 20-ticker sample next. No earnings consensus "
            "or analyst revisions are available from SEC, so that provider gap remains open."),
        REC_PARTIAL: (
            "SEC fundamentals normalized for part of the sample, but some tickers or fields are "
            "missing or availability coverage is incomplete. Repair mapping / availability / "
            "duplicate handling before expanding."),
        REC_BLOCKED: (
            "Network and cache both failed to deliver a meaningful SEC sample, so the prototype is "
            "blocked. Repair SEC access / caching / CIK mapping before continuing. No data was "
            "purchased and no vendor API was called."),
        REC_REJECTED: (
            "SEC data was reachable but could not supply usable point-in-time fundamentals for the "
            "required schema, so an alternative free or low-cost fundamentals source should be "
            "selected."),
    }[recommendation]
    return {
        "recommendation": recommendation,
        "allowed_values": ALLOWED_RECOMMENDATIONS,
        "create_production_model_candidate_now": False,
        "train_production_model_now": False,
        "deploy_now": False,
        "production_edge_claimed": False,
        "selected_source": SOURCE_NAME,
        "full_128_ticker_ingestion_now": False,
        "model_training_now": False,
        "reason": reason,
    }


def build_recommended_next_phase(recommendation):
    table = {
        REC_SUCCESS: (
            "SEC Fundamentals Mini-Pipeline Expansion",
            "Expand the SEC fundamentals prototype from 5 tickers to a controlled 20-ticker "
            "sample, add stronger data-quality checks, and prepare feature engineering; still no "
            "model training."),
        REC_PARTIAL: (
            "Repair SEC Fundamentals Prototype",
            "Fix missing fields, availability timestamps, duplicate records, or mapping issues "
            "before expanding."),
        REC_BLOCKED: (
            "Repair SEC Prototype Access",
            "Fix SEC access, caching, CIK mapping, or source compatibility before continuing."),
        REC_REJECTED: (
            "Alternative Fundamentals Source Selection",
            "Select a different free or low-cost fundamentals source before further prototyping."),
    }
    title, purpose = table[recommendation]
    return {"phase": "3-G", "title": title, "purpose": purpose}


def build_interpretation(recommendation, network_used):
    return {
        "free_source_prototype_only": True,
        "price_volume_only_modeling_stopped": True,
        "model_trained": False,
        "model_features_computed": False,
        "labels_computed": False,
        "research_model_trained": False,
        "production_model_trained": False,
        "production_model_candidate_created": False,
        "deployable_model_artifact_written": False,
        "external_data_ingested": True,
        "production_data_ingested": False,
        "full_128_ticker_ingestion": False,
        "sec_public_data_used": True,
        "paid_vendor_called": False,
        "data_purchased": False,
        "read_from_d_drive": False,
        "wrote_to_d_drive": False,
        "network_used": network_used,
        "results_context_remains_survivorship_biased": True,
        "production_edge_claimed": False,
        "narrative": (
            "Phase 3-F is a tiny, read-only free-source prototype. It read the committed Phase 3-E "
            "feasibility result, schema catalog, and ingestion contract, confirmed Phase 3-E routed "
            "here, selected SEC public companyfacts + submissions as the first free fundamentals "
            "source, fetched a tiny throttled/cached sample of at most 5 tickers from official SEC "
            "public endpoints only, and normalized company identity + a small subset of fundamentals "
            "into the Phase 3-E schema with point-in-time availability timestamps. It computed no "
            "model features or labels, trained no model, created no production model candidate, wrote "
            "no deployable model artifact, called no paid vendor, purchased no data, used no "
            "third-party market-data vendor package, touched no database, ran no migration, "
            "restarted no prediction service, "
            "enabled no serving flag, wrote nothing to the D: drive, read no D: price CSV, placed no "
            "orders, and traded nothing. SEC does not provide earnings consensus or analyst "
            "revisions, so that provider gap stays open for a later phase. This is a research "
            "prototype only and claims no production edge."),
    }


def build_safety_flags(network_used):
    return {
        "database_touched": False,
        "database_write_executed": False,
        "migration_executed": False,
        "deployment_executed": False,
        "model_v2_enabled": False,
        "production_edge_claimed": False,
        "no_trading": True,
        "no_orders": True,
        "no_automation": True,
        "research_model_trained": False,
        "production_model_trained": False,
        "production_model_candidate_created": False,
        "deployable_model_artifact_written": False,
        "d_drive_read": False,
        "d_drive_written": False,
        "network_used": bool(network_used),
        "sec_public_data_used": True,
        "vendor_api_called": False,
        "paid_vendor_api_called": False,
        "data_purchase_made": False,
        "external_data_ingested": True,
        "production_data_ingested": False,
    }


# --------------------------------------------------------------------------- #
# Writers
# --------------------------------------------------------------------------- #
def _write_csv(path, columns, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=columns)
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in columns})


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
def run(prototype_path=PROTOTYPE_JSON, identity_path=COMPANY_IDENTITY_CSV,
        fundamentals_path=FUNDAMENTALS_CSV, data_quality_path=DATA_QUALITY_JSON,
        raw_dir=_RAW_DIR):
    phase3e = _load_json(PHASE3E_JSON)
    contract = _load_json(INGESTION_CONTRACT_JSON)
    confirmed_3e = confirm_phase3e(phase3e)
    confirmed_contract = confirm_ingestion_contract(contract)
    schema_present = _schema_catalog_present(SCHEMA_CATALOG_CSV)
    sector_map = load_sector_map(SECTOR_MAP_CSV)

    client = SecClient()
    raw_files = []
    identity_rows = []
    fundamentals_rows = []
    fields_found_by_ticker = {}
    processed = []
    failed = []

    # Tickers in scope: in the requested sample AND present in the sector map (skip + report).
    in_scope = []
    for t in SAMPLE_TICKERS[:MAX_SAMPLE_TICKERS]:
        if t in sector_map:
            in_scope.append(t)
        else:
            failed.append({"ticker": t, "reason": "not present in sector map; skipped"})

    company_index = {}
    if in_scope:
        # 1) company_tickers.json (once) -> ticker -> CIK.
        ct_cache = os.path.join(raw_dir, "company_tickers.json")
        try:
            company_tickers, _origin = client.get_cached_or_fetch(
                COMPANY_TICKERS_URL, ct_cache, _prune_company_tickers)
            if os.path.isfile(ct_cache):
                raw_files.append("company_tickers.json")
            company_index = _company_ticker_index(company_tickers)
        except (urllib.error.URLError, OSError, ValueError, RuntimeError,
                json.JSONDecodeError) as e:
            client.errors.append("company_tickers fetch failed: %s" % e)

    for t in in_scope:
        cik_int, title = company_index.get(t, (None, None))
        if cik_int is None:
            failed.append({"ticker": t, "reason": "CIK not found in SEC company_tickers"})
            continue
        cik10 = "%010d" % int(cik_int)
        sub_cache = os.path.join(raw_dir, "submissions_%s.json" % cik10)
        cf_cache = os.path.join(raw_dir, "companyfacts_%s.json" % cik10)
        try:
            submissions, _o1 = client.get_cached_or_fetch(
                SUBMISSIONS_URL_TEMPLATE.format(cik10=cik10), sub_cache, _prune_submissions)
            companyfacts, _o2 = client.get_cached_or_fetch(
                COMPANYFACTS_URL_TEMPLATE.format(cik10=cik10), cf_cache, _prune_companyfacts)
        except (urllib.error.URLError, OSError, ValueError, RuntimeError,
                json.JSONDecodeError) as e:
            client.errors.append("%s (CIK%s) fetch failed: %s" % (t, cik10, e))
            failed.append({"ticker": t, "reason": "SEC fetch/cache failed: %s" % e})
            continue

        if os.path.isfile(sub_cache):
            raw_files.append("submissions_%s.json" % cik10)
        if os.path.isfile(cf_cache):
            raw_files.append("companyfacts_%s.json" % cik10)

        acceptance = _accession_to_acceptance(submissions)
        sector_info = sector_map.get(t, {})
        identity_rows.append(_normalize_identity(
            t, int(cik_int), title or submissions.get("name"), sector_info,
            sector_info.get("as_of_date")))
        rows, found = _normalize_fundamentals_for_ticker(
            t, int(cik_int), companyfacts, acceptance)
        fundamentals_rows.extend(rows)
        fields_found_by_ticker[t] = found
        if rows:
            processed.append(t)
        else:
            failed.append({"ticker": t, "reason": "no mappable fundamentals concepts found"})

    network_used = client.network_requests > 0

    # Decide (data-quality report carries a provisional recommendation echoed into the result).
    dq_provisional = build_data_quality_report(
        SAMPLE_TICKERS, processed, failed, raw_files, identity_rows, fundamentals_rows,
        fields_found_by_ticker, client, network_used, recommendation=None)
    recommendation = decide(processed, fundamentals_rows, dq_provisional, identity_rows)
    dq = build_data_quality_report(
        SAMPLE_TICKERS, processed, failed, raw_files, identity_rows, fundamentals_rows,
        fields_found_by_ticker, client, network_used, recommendation=recommendation)

    # Write sample CSVs + data-quality report.
    _write_csv(identity_path, IDENTITY_COLUMNS, identity_rows)
    _write_csv(fundamentals_path, FUNDAMENTALS_COLUMNS, fundamentals_rows)
    os.makedirs(os.path.dirname(data_quality_path), exist_ok=True)
    with open(data_quality_path, "w", encoding="utf-8") as f:
        json.dump(dq, f, indent=2)

    result = {
        "phase": PHASE,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "inputs_read": {
            "phase3e_feasibility_json":
                "research/output/phase3e_fundamentals_earnings_feasibility.json",
            "phase3e_schema_catalog_csv":
                "research/output/phase3e_external_data_schema_catalog.csv",
            "phase3e_ingestion_contract_json":
                "research/output/phase3e_ingestion_contract.json",
            "phase3e_provider_requirements_matrix_csv":
                "research/output/phase3e_provider_requirements_matrix.csv",
            "sector_map_csv": "research/input/phase2k_p_sector_map_current.csv",
        },
        "outputs_written": {
            "prototype_json": "research/output/phase3f_sec_fundamentals_prototype.json",
            "company_identity_sample_csv":
                "research/output/phase3f_sec_fundamentals_sample/company_identity_sample.csv",
            "fundamentals_sample_csv":
                "research/output/phase3f_sec_fundamentals_sample/fundamentals_sample.csv",
            "data_quality_report_json":
                "research/output/phase3f_sec_fundamentals_sample/data_quality_report.json",
            "raw_cache_dir": "research/output/phase3f_sec_fundamentals_sample/raw/",
        },
        "phase3e_summary": {
            "phase3e_confirmed": confirmed_3e,
            "ingestion_contract_confirmed": confirmed_contract,
            "schema_catalog": schema_present,
            "phase3e_recommendation": (phase3e or {}).get("recommendation", {}).get(
                "recommendation"),
        },
        "source_selection": {
            "selected_source": SOURCE_NAME,
            "rationale": "SEC public companyfacts + submissions are free, point-in-time via filing "
                         "acceptance timestamps, and cover the 128-name universe's fundamentals; "
                         "Phase 3-E flagged SEC as the strongest free first prototype source.",
            "official_sec_public_source": True,
            "covers_fundamentals": True,
            "covers_earnings_consensus": False,
            "covers_analyst_estimate_revisions": False,
            "endpoints_used": [
                COMPANY_TICKERS_URL,
                SUBMISSIONS_URL_TEMPLATE.format(cik10="##########"),
                COMPANYFACTS_URL_TEMPLATE.format(cik10="##########"),
            ],
        },
        "sec_access_summary": {
            "user_agent_declared": True,
            "allowed_hosts": list(ALLOWED_SEC_HOSTS),
            "min_request_interval_seconds": MIN_REQUEST_INTERVAL_S,
            "max_total_requests": MAX_TOTAL_REQUESTS,
            "sec_request_count": client.request_count,
            "network_request_count": client.network_requests,
            "used_cache_count": client.cache_hits,
            "cache_first": True,
            "errors": client.errors,
        },
        "sample_universe": {
            "sample_tickers_requested": list(SAMPLE_TICKERS),
            "max_sample_tickers": MAX_SAMPLE_TICKERS,
            "sample_tickers_in_scope": in_scope,
            "sample_tickers_processed": processed,
            "sample_tickers_failed": failed,
            "full_128_ticker_ingestion": False,
        },
        "company_identity_summary": {
            "rows": len(identity_rows),
            "columns": IDENTITY_COLUMNS,
            "tickers": [r["ticker"] for r in identity_rows],
        },
        "fundamentals_summary": {
            "rows": len(fundamentals_rows),
            "columns": FUNDAMENTALS_COLUMNS,
            "fields_attempted": list(ALL_ATTEMPTED_FIELDS),
            "fields_found": dq["fields_found"],
            "availability_datetime_coverage": dq["availability_datetime_coverage"],
            "point_in_time_usable_fraction": dq["point_in_time_usable_fraction"],
            "duplicate_key_count": dq["duplicate_key_count"],
        },
        "data_quality_summary": dq,
        "source_limitations": dq["source_limitations"],
        "earnings_revisions_gap": {
            "sec_provides_earnings_consensus": False,
            "sec_provides_analyst_estimate_revisions": False,
            "gap_description": "SEC public filings provide as-reported fundamentals and filing "
                               "timestamps but not forward analyst consensus or estimate "
                               "revisions; a separate provider must be researched and selected "
                               "(see the Phase 3-E provider requirements matrix) before earnings "
                               "surprise and revision-momentum features can be prototyped.",
            "provider_selection_required": True,
        },
        "recommendation": build_recommendation(recommendation),
        "interpretation": build_interpretation(recommendation, network_used),
        "recommended_next_phase": build_recommended_next_phase(recommendation),
    }
    result.update(build_safety_flags(network_used))

    os.makedirs(os.path.dirname(prototype_path), exist_ok=True)
    with open(prototype_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)
    return result


def main():
    result = run()
    rec = result["recommendation"]
    nxt = result["recommended_next_phase"]
    su = result["sample_universe"]
    fs = result["fundamentals_summary"]
    print("Phase %s - SEC Fundamentals Source Selection and Free Prototype" % result["phase"])
    print("  phase3e confirmed        : %s"
          % result["phase3e_summary"]["phase3e_confirmed"]["all_confirmed"])
    print("  ingestion contract conf  : %s"
          % result["phase3e_summary"]["ingestion_contract_confirmed"]["all_confirmed"])
    print("  tickers processed        : %s / %s"
          % (len(su["sample_tickers_processed"]), len(su["sample_tickers_requested"])))
    print("  fundamentals rows        : %s" % fs["rows"])
    print("  availability coverage    : %s" % fs["availability_datetime_coverage"])
    print("  point-in-time usable frac: %s" % fs["point_in_time_usable_fraction"])
    print("  duplicate key count      : %s" % fs["duplicate_key_count"])
    print("  network used             : %s" % result["network_used"])
    print("  sec requests / cache hits: %s / %s"
          % (result["sec_access_summary"]["network_request_count"],
             result["sec_access_summary"]["used_cache_count"]))
    print("  recommendation           : %s" % rec["recommendation"])
    print("  recommended next phase   : %s - %s" % (nxt["phase"], nxt["title"]))
    return result


if __name__ == "__main__":
    main()
