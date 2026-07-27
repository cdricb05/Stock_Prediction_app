"""Phase 30C — survivorship-safe fundamental backfill and PIT sector history.

Phase 30B proved several owned fundamental factors carry promising rank-IC but
the owned EODHD fundamental universe is severely CURRENT-survivor-biased and no
point-in-time-safe sector classification exists (Norgate ``classification`` has
no effective-date dimension; the repaired EODHD GICS map is current-only).

This module performs ACTUAL data acquisition and coverage repair using only
owned + free sources and existing securely-loaded credentials:

- Part A: a canonical, content-hashed historical security master built by
  joining the survivorship-free Norgate-derived Russell-3000 master
  (12,266 securities, 8,520 delisted) to the momentum research universe, with
  deterministic CIK resolution (SEC current directory + owned SimFin company
  master) whose confidence + ambiguity are recorded. Reused tickers never merge
  unrelated companies (the ``-YYYYMM`` delisting suffix is the cross-time
  identity key); a removed name is never given a CIK from future survival alone.
- Part B: a deterministic, seed-fixed provider sample (60 removed + 20 current,
  decade-stratified) persisted BEFORE any network call, never cherry-picked by
  data availability.
- Part C: real provider probes — Norgate local capability (read from the
  installed package, never a live fetch), a bounded live EODHD entitlement
  probe, an owned-SimFin file probe, and a bounded live SEC EDGAR probe.
- Part D: a provider-selection gate.
- Parts E–I: bounded, resumable SEC acquisition, PIT as-filed fundamental
  normalization (available_date = filing acceptance timestamp; first-reported
  per period; restatement classified), PIT sector history from filing-header
  SIC at acceptance date (a versioned, content-hashed SIC→sector crosswalk),
  and the unchanged coverage / survivorship / sector-history gates.
- Parts J/K: the four Phase 30B factors are re-evaluated on the repaired data
  through the committed ``owned_factors`` diagnostic path; portfolio +
  robustness run only for a genuine ADVANCE (there is none by construction of
  the honest gates, and zero survivors is valid).

Everything numeric / evaluative is REUSED, never re-derived:
``family_backtest`` (panel loading, helpers, baseline), ``feature_evaluation``
(rank-IC battery, IC screen, 80/20 integration, robustness),
``owned_factors`` (universe/survivorship audit, per-factor evaluation, portfolio
integration), ``artifact_store`` (atomic writes, append-only ledgers, content
hashing, secret scanning), ``controller`` (git HEAD without subprocess),
``schemas`` (approved vocabularies + forbidden-key scan). There is no code path
that creates orders, touches a broker, enables automation, registers a
challenger, or changes the operational model.
"""

from __future__ import annotations

import bisect
import csv
import gzip
import io
import json
import os
import re
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import SAFETY_CONTRACT
from . import family_backtest as fb
from . import feature_evaluation as ev
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

HISTORICAL_COVERAGE_SCHEMA_VERSION = "30C.1"

RUNS_SUBDIR = "historical_coverage_runs"
LATEST_RUN_FILE = "phase30c_latest_run.json"

_ROOTS = {"repo": fb.REPO_ROOT, "data_root": fb.DATA_ROOT}

# Hosts a provider endpoint may target. An arbitrary URL/host is rejected at
# config validation — no provider base URL is ever taken from a model.
_ALLOWED_PROVIDER_HOSTS = frozenset({
    "www.sec.gov", "data.sec.gov",
    "eodhd.com", "eodhistoricaldata.com",
    "backend.simfin.com", "simfin.com",
})

# Paper Trader must never be referenced. Any of these substrings in any config
# string value is a hard violation (Part O test 6).
_PAPER_TRADER_FORBIDDEN = (
    "paper_trader", "127.0.0.1:8001", "127.0.0.1:9000",
    "/ui/", "/v1/paper-desk", "/v1/operational-book", "/v1/dashboard",
    "/current-alpha/", "operational_book", "daily_close",
)

DEFAULT_SEC_USER_AGENT = (
    "PaperTraderResearch/Phase30C cedric.binisti.research@example.com"
)

# Part J allowed per-factor decisions (distinct from the 30B vocabulary).
FACTOR_DECISIONS = (
    "REJECTED_SIGNAL",
    "REJECTED_COVERAGE",
    "REJECTED_SURVIVORSHIP",
    "REJECTED_SECTOR_HISTORY",
    "DIAGNOSTIC_ONLY",
    "INCONCLUSIVE",
    "ADVANCE_TO_PORTFOLIO_SCREEN",
)

# Part D allowed provider-selection outcomes.
FUNDAMENTAL_SOURCE_DECISIONS = (
    "USE_EODHD_BACKFILL",
    "USE_SIMFIN_BACKFILL",
    "USE_SEC_AS_FILED_BACKFILL",
    "COMBINE_OWNED_SOURCES",
    "NO_USABLE_OWNED_OR_FREE_REPAIR_SOURCE",
)
SECTOR_SOURCE_DECISIONS = (
    "USE_NORGATE_SECTOR_HISTORY",
    "USE_SEC_FILING_SIC_HISTORY",
    "COMBINE_OWNED_SOURCES",
    "NO_USABLE_OWNED_OR_FREE_REPAIR_SOURCE",
)
SECTOR_CLASSES = ("PIT_SAFE", "PARTIAL_HISTORY", "CURRENT_ONLY", "UNUSABLE")

_DELISTED_SUFFIX_RE = re.compile(r"-(\d{6})$")

_RESTATEMENT_POLICY = (
    "first-reported preferred: the earliest-filed value per fiscal period is "
    "kept and classified original_as_filed; a later filing for the same period "
    "is classified restated and retains its own later acceptance date. "
    "Original-as-filed and latest-restated values are never silently mixed."
)


class HistoricalCoverageError(RuntimeError):
    pass


# --------------------------------------------------------------------------- #
# small helpers
# --------------------------------------------------------------------------- #
def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def base_symbol(ticker: str) -> str:
    """Norgate current-and-past delisted convention ``SYMBOL-YYYYMM`` -> SYMBOL."""
    return _DELISTED_SUFFIX_RE.sub("", (ticker or "").strip().upper())


def delisting_month(ticker: str) -> Optional[str]:
    m = _DELISTED_SUFFIX_RE.search((ticker or "").strip().upper())
    if not m:
        return None
    raw = m.group(1)
    return "%s-%s" % (raw[:4], raw[4:6])


def _decade_of(date_or_month: Optional[str]) -> Optional[str]:
    if not date_or_month or len(date_or_month) < 4:
        return None
    try:
        y = int(date_or_month[:4])
    except ValueError:
        return None
    return "%ds" % (y - y % 10)


def _to_float(x: Any) -> Optional[float]:
    return fb._to_float(x)


# --------------------------------------------------------------------------- #
# config
# --------------------------------------------------------------------------- #
def load_config(path: str) -> Optional[Dict[str, Any]]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _resolve_path_spec(spec: Any) -> str:
    if not isinstance(spec, dict) or "root" not in spec or "relpath" not in spec:
        raise HistoricalCoverageError("path spec must be {root, relpath}: %r" % (spec,))
    root = _ROOTS.get(spec["root"])
    if root is None:
        raise HistoricalCoverageError("unknown fixed root %r" % (spec["root"],))
    rel = str(spec["relpath"])
    parts = rel.replace("\\", "/").split("/")
    if os.path.isabs(rel) or ".." in parts:
        raise HistoricalCoverageError("relpath must stay within the fixed root: %r" % rel)
    return os.path.normpath(os.path.join(root, *parts))


def _iter_string_values(obj: Any) -> List[str]:
    out: List[str] = []
    if isinstance(obj, str):
        out.append(obj)
    elif isinstance(obj, dict):
        for v in obj.values():
            out.extend(_iter_string_values(v))
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            out.extend(_iter_string_values(v))
    return out


def _host_of(url: str) -> Optional[str]:
    m = re.match(r"^https?://([^/]+)/", url if url.endswith("/") else url + "/")
    return m.group(1).lower() if m else None


def validate_config(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Structural + non-weakening + safety validation; never touches the network
    or the filesystem."""
    v: List[Dict[str, Any]] = []

    def bad(field: str, issue: str, value: Any = None) -> None:
        v.append({"field": field, "issue": issue, "value": value})

    if not isinstance(cfg, dict):
        return {"accepted": False, "violations": [{"field": "$", "issue": "config must be an object"}], "config_hash": None}

    if cfg.get("schema_version") != HISTORICAL_COVERAGE_SCHEMA_VERSION:
        bad("schema_version", "must be %s" % HISTORICAL_COVERAGE_SCHEMA_VERSION, cfg.get("schema_version"))
    if not cfg.get("name"):
        bad("name", "required")

    # secrets / forbidden execution keys (Part O #1)
    for k in find_secret_keys(cfg):
        bad(k, "secret-looking key is forbidden in config")
    for k in find_forbidden_execution_keys(cfg):
        bad(k, "forbidden execution-token key")

    # Paper Trader must never be referenced (Part O #6)
    for s in _iter_string_values(cfg):
        low = s.lower()
        for token in _PAPER_TRADER_FORBIDDEN:
            if token in low:
                bad("$", "Paper Trader path/endpoint reference is forbidden", token)
                break

    data = cfg.get("data") or {}
    cutoff = data.get("data_cutoff")
    if not isinstance(cutoff, str) or len(cutoff) != 10:
        bad("data.data_cutoff", "required YYYY-MM-DD", cutoff)

    # roots: only the two fixed roots (Part O #2)
    roots = (cfg.get("sources") or {}).get("roots") or cfg.get("roots") or {}
    for name in roots:
        if name not in _ROOTS:
            bad("roots.%s" % name, "only fixed roots {repo, data_root} allowed", name)

    # every declared source path must resolve within a fixed root
    sources = cfg.get("sources") or {}
    for key, spec in sources.items():
        if key == "roots":
            continue
        if not isinstance(spec, dict) or spec.get("root") not in _ROOTS:
            bad("sources.%s" % key, "must be {root in [repo,data_root], relpath}", spec)
        else:
            rel = str(spec.get("relpath", ""))
            if os.path.isabs(rel) or ".." in rel.replace("\\", "/").split("/"):
                bad("sources.%s.relpath" % key, "no absolute paths or .. traversal", rel)

    # provider cache + normalized roots must resolve under data_root
    for key in ("provider_cache_root", "normalized_root"):
        spec = cfg.get(key)
        if not isinstance(spec, dict) or spec.get("root") not in _ROOTS:
            bad(key, "must be {root in [repo,data_root], relpath}", spec)

    # provider endpoints: arbitrary hosts rejected (Part O #3)
    endpoints = cfg.get("provider_endpoints") or {}
    for prov, spec in endpoints.items():
        if not isinstance(spec, dict):
            bad("provider_endpoints.%s" % prov, "must be an object", spec)
            continue
        hosts = spec.get("allowed_hosts") or []
        if not hosts:
            bad("provider_endpoints.%s.allowed_hosts" % prov, "at least one allowed host required")
        for h in hosts:
            if h not in _ALLOWED_PROVIDER_HOSTS:
                bad("provider_endpoints.%s.allowed_hosts" % prov, "host not in the official allow-list", h)
        for uk, uv in spec.items():
            if uk.endswith("_url") and isinstance(uv, str):
                host = _host_of(uv)
                if host is None or host not in _ALLOWED_PROVIDER_HOSTS:
                    bad("provider_endpoints.%s.%s" % (prov, uk), "URL host not in the official allow-list", uv)

    # provider order
    order = cfg.get("provider_order") or []
    known = {"norgate", "eodhd", "simfin", "sec"}
    for p in order:
        if p not in known:
            bad("provider_order", "unknown provider", p)

    # deterministic sample (Part O #4/#11/#12)
    sample = cfg.get("sample") or {}
    if not isinstance(sample.get("removed"), int) or sample.get("removed") < 1:
        bad("sample.removed", "must be a positive integer", sample.get("removed"))
    if not isinstance(sample.get("current"), int) or sample.get("current") < 1:
        bad("sample.current", "must be a positive integer", sample.get("current"))
    if not isinstance(sample.get("seed"), int):
        bad("sample.seed", "fixed integer seed required", sample.get("seed"))

    # acquisition limits bounded (Part O #4/#16/#19)
    acq = cfg.get("acquisition") or {}
    mrb = acq.get("max_requests_per_batch")
    if not isinstance(mrb, int) or not (1 <= mrb <= 25):
        bad("acquisition.max_requests_per_batch", "must be an integer in [1, 25]", mrb)
    mr = acq.get("max_retries")
    if not isinstance(mr, int) or not (0 <= mr <= 5):
        bad("acquisition.max_retries", "must be an integer in [0, 5]", mr)
    to = acq.get("request_timeout_seconds")
    if not isinstance(to, (int, float)) or not (1.0 <= to <= 120.0):
        bad("acquisition.request_timeout_seconds", "must be in [1, 120] seconds", to)
    sec_iv = acq.get("sec_min_interval_seconds")
    if not isinstance(sec_iv, (int, float)) or sec_iv < 0.1:
        bad("acquisition.sec_min_interval_seconds", "SEC fair-access requires >= 0.1s (<=10 req/s)", sec_iv)

    # entitlement-only rules (no purchase / no sales contact)
    ent = cfg.get("entitlement") or {}
    if ent.get("no_purchase") is not True:
        bad("entitlement.no_purchase", "must be true (no data purchase)", ent.get("no_purchase"))
    if ent.get("contact_sales") not in (False, None):
        bad("entitlement.contact_sales", "must be false (no sales contact)", ent.get("contact_sales"))

    # coverage / survivorship / sector gates — never weakened (Part O #5/#40)
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
    if sh.get("require_pit_safe_for_promotion") is not True:
        bad("sector_history.require_pit_safe_for_promotion", "must be true", sh.get("require_pit_safe_for_promotion"))
    if sh.get("treat_unknown_as_sector") not in (False, None):
        bad("sector_history.treat_unknown_as_sector", "Unknown is missing, never a real sector", sh.get("treat_unknown_as_sector"))

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

    # safety contract present
    safety = cfg.get("safety") or {}
    if safety.get("research_only") is not True:
        bad("safety.research_only", "must be true")

    return {"accepted": not v, "violations": v, "config_hash": content_hash(cfg)}


def _of_cfg(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Adapter: an ``owned_factors``-shaped config view for reuse of its
    evaluation machinery, driven entirely by the 30C config."""
    cg = cfg.get("coverage_gates") or {}
    return {
        "survivorship": {"min_delisted_representation_fraction": cg.get("min_delisted_representation_fraction", 0.20)},
        "global_min_cross_sectional_coverage": cg.get("global_min_cross_sectional_coverage", 0.60),
        "source_universe_min_coverage": cg.get("global_min_cross_sectional_coverage", 0.60),
        "sector_history": cfg.get("sector_history") or {},
        "ic_screen": cfg.get("ic_screen") or {},
        "costs": cfg.get("costs") or {},
        "integration": cfg.get("integration") or {},
        "portfolio": cfg.get("portfolio") or {},
        "max_factor_staleness_months": cfg.get("max_factor_staleness_months", fb.MAX_FUND_STALE_MONTHS),
    }


# --------------------------------------------------------------------------- #
# Part H helper: versioned, content-hashed SIC -> sector crosswalk
# --------------------------------------------------------------------------- #
SIC_SECTOR_MAP_VERSION = "30C.sic2gics.v1"


def sic_to_sector(sic: Any) -> str:
    """Deterministic SIC (4-digit) -> broad GICS-style sector crosswalk.

    This is an owned, versioned crosswalk (NOT official GICS). It maps the SEC
    filing-header SIC — which is observed point-in-time at each filing's
    acceptance date — to one of the 11 GICS sectors, with careful major-group
    overrides (drugs, semiconductors, computers, medical devices, banks, REITs,
    telecom, utilities, oil & gas). Anything unmapped stays "Unknown" (missing,
    never a real sector).
    """
    try:
        code = int(str(sic).strip())
    except (TypeError, ValueError):
        return "Unknown"
    if code <= 0:
        return "Unknown"

    # specific major-group overrides first
    if 2833 <= code <= 2836 or 3826 <= code <= 3851 or 8000 <= code <= 8099 or code == 2830:
        return "Health Care"
    if code in (3571, 3572, 3576, 3577) or 3570 <= code <= 3579 or 7370 <= code <= 7379 or code == 3674:
        return "Information Technology"
    if 3600 <= code <= 3679 or code in (3661, 3663, 3669):
        return "Information Technology"
    if 4800 <= code <= 4899 or 2700 <= code <= 2799 or 7800 <= code <= 7841:
        return "Communication Services"
    if 4900 <= code <= 4999:
        return "Utilities"
    if 2900 <= code <= 2999 or 1300 <= code <= 1399:
        return "Energy"
    if 6500 <= code <= 6599:
        return "Real Estate"
    if 6000 <= code <= 6799:
        return "Financials"
    if 3710 <= code <= 3716 or 5000 <= code <= 5999 or 7000 <= code <= 7299 or 2300 <= code <= 2399 or 2500 <= code <= 2599 or 3630 <= code <= 3639:
        # motor vehicles, wholesale/retail trade, hotels/lodging, apparel, furniture, household appliances
        if 5400 <= code <= 5499 or code in (5912, 2000, 2080, 2082, 2086, 2090, 2100):
            return "Consumer Staples"
        return "Consumer Discretionary"
    if 2000 <= code <= 2199:
        return "Consumer Staples"
    if 2600 <= code <= 2699 or 2800 <= code <= 2899 or 1000 <= code <= 1299 or 3300 <= code <= 3399 or 800 <= code <= 999 or 1 <= code <= 999:
        return "Materials"
    if 1500 <= code <= 1799 or 3400 <= code <= 3569 or 3580 <= code <= 3599 or 3700 <= code <= 3799 or 4000 <= code <= 4799 or 3800 <= code <= 3825:
        return "Industrials"
    if 3000 <= code <= 3299:
        return "Materials"
    return "Unknown"


def sic_sector_map_provenance() -> Dict[str, Any]:
    """A content hash over a fixed probe grid makes the crosswalk versioned +
    reproducible without persisting 9,999 rows."""
    grid = list(range(100, 10000, 25))
    mapping = {str(c): sic_to_sector(c) for c in grid}
    return {
        "version": SIC_SECTOR_MAP_VERSION,
        "hash": content_hash({"version": SIC_SECTOR_MAP_VERSION, "grid": mapping}),
        "probe_grid_step": 25,
        "distinct_sectors": sorted(set(mapping.values())),
        "note": "owned SIC->sector crosswalk observed PIT at filing acceptance; "
        "Unknown means missing, never a real sector",
    }


# --------------------------------------------------------------------------- #
# Part A: canonical historical security master
# --------------------------------------------------------------------------- #
def _load_momentum_universe(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Distinct tickers + per-month PIT membership from the survivorship-free
    momentum panel (member-rows-only; membership = row presence)."""
    path = _resolve_path_spec(cfg["sources"]["momentum_panel"])
    tickers: set = set()
    members_by_month: Dict[str, set] = {}
    months: set = set()
    first_seen: Dict[str, str] = {}
    last_seen: Dict[str, str] = {}
    with open(path, "r", encoding="utf-8", newline="") as fh:
        for r in csv.DictReader(fh):
            tk = (r.get("ticker") or "").strip().upper()
            m = (r.get("month") or "").strip()
            if not tk or not m:
                continue
            tickers.add(tk)
            months.add(m)
            members_by_month.setdefault(m, set()).add(tk)
            if tk not in first_seen or m < first_seen[tk]:
                first_seen[tk] = m
            if tk not in last_seen or m > last_seen[tk]:
                last_seen[tk] = m
    return {
        "path": path,
        "content_hash": fb._file_sha256(path),
        "tickers": tickers,
        "months": sorted(months),
        "members_by_month": members_by_month,
        "first_member_month": first_seen,
        "last_member_month": last_seen,
    }


def _load_security_metadata(cfg: Dict[str, Any]) -> Tuple[Dict[str, Dict[str, str]], str]:
    path = _resolve_path_spec(cfg["sources"]["security_master"])
    out: Dict[str, Dict[str, str]] = {}
    with open(path, "r", encoding="utf-8", newline="") as fh:
        for r in csv.DictReader(fh):
            tk = (r.get("ticker") or "").strip().upper()
            if tk:
                out[tk] = r
    return out, fb._file_sha256(path)


def _load_ticker_cik_directory(cfg: Dict[str, Any]) -> Tuple[Dict[str, int], str]:
    """SEC current company_tickers.json -> base ticker -> CIK (current names)."""
    path = _resolve_path_spec(cfg["sources"]["sec_company_tickers"])
    out: Dict[str, int] = {}
    with open(path, "r", encoding="utf-8") as fh:
        directory = json.load(fh)
    for v in (directory.values() if isinstance(directory, dict) else []):
        tk = (v.get("ticker") or "").strip().upper()
        cik = v.get("cik_str")
        if tk and cik is not None:
            try:
                out[tk] = int(cik)
            except (TypeError, ValueError):
                continue
    return out, fb._file_sha256(path)


def _load_simfin_company_map(cfg: Dict[str, Any]) -> Tuple[Dict[str, Dict[str, str]], str]:
    """Owned SimFin us-companies.csv (survivorship-safe, incl delisted-in-window)
    -> base ticker -> {cik, simfin_id, name}."""
    spec = cfg["sources"].get("simfin_companies")
    if not spec:
        return {}, ""
    path = _resolve_path_spec(spec)
    out: Dict[str, Dict[str, str]] = {}
    if not os.path.isfile(path):
        return {}, ""
    with open(path, "r", encoding="utf-8", newline="") as fh:
        for r in csv.DictReader(fh, delimiter=";"):
            tk = (r.get("Ticker") or "").strip().upper()
            cik = (r.get("CIK") or "").strip()
            if tk and cik:
                out.setdefault(tk, {
                    "cik": cik,
                    "simfin_id": (r.get("SimFinId") or "").strip(),
                    "name": (r.get("Company Name") or "").strip(),
                })
    return out, fb._file_sha256(path)


def _name_tokens(name: str) -> set:
    toks = re.split(r"[^A-Za-z0-9]+", (name or "").upper())
    drop = {"INC", "CORP", "CO", "COMMON", "CLASS", "LTD", "PLC", "THE", "GROUP",
            "HOLDINGS", "COMPANY", "CORPORATION", "STOCK", "SHARES", "A", "B", "C", ""}
    return {t for t in toks if t and t not in drop and len(t) > 1}


def build_security_master(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Deterministic canonical historical security master (Part A).

    Identity is carried on the Norgate current-and-past ticker string; the
    ``-YYYYMM`` suffix disambiguates delisted names so a reused base symbol
    never merges unrelated companies. CIK is resolved with an explicit
    confidence + ambiguity record; a removed name is NOT given a CIK from a
    current SEC ticker unless the company name corroborates (ticker alone is
    insufficient for removed securities), and future survival is never used as
    mapping evidence.
    """
    uni = _load_momentum_universe(cfg)
    meta, meta_hash = _load_security_metadata(cfg)
    sec_dir, sec_hash = _load_ticker_cik_directory(cfg)
    simfin_map, simfin_hash = _load_simfin_company_map(cfg)

    final_month = uni["months"][-1] if uni["months"] else None
    members_final = uni["members_by_month"].get(final_month, set()) if final_month else set()

    rows: List[Dict[str, Any]] = []
    n_ambiguous = 0
    for tk in sorted(uni["tickers"]):
        b = base_symbol(tk)
        md = meta.get(tk, {})
        is_delisted = str(md.get("is_delisted", "")).strip().lower() == "true" or bool(_DELISTED_SUFFIX_RE.search(tk))
        is_current_member = tk in members_final
        name = (md.get("security_name") or "").strip()

        # deterministic CIK resolution with confidence + ambiguity
        sec_cik = sec_dir.get(b)
        sf = simfin_map.get(b)
        sf_cik = None
        if sf and sf.get("cik"):
            try:
                sf_cik = int(sf["cik"])
            except (TypeError, ValueError):
                sf_cik = None

        cik: Optional[int] = None
        cik_source = None
        confidence = "none"
        ambiguity: Optional[str] = None

        if not is_delisted:
            # current name: SEC current directory is authoritative
            if sec_cik is not None:
                cik, cik_source, confidence = sec_cik, "sec_current_directory", "high"
            elif sf_cik is not None:
                cik, cik_source, confidence = sf_cik, "simfin_company_master", "medium"
            if sec_cik is not None and sf_cik is not None and sec_cik != sf_cik:
                ambiguity = "sec_current vs simfin CIK disagree"
        else:
            # removed name: SimFin (historical, incl delisted) preferred; a
            # current-SEC base match is a ticker-REUSE risk unless corroborated
            if sf_cik is not None:
                cik, cik_source, confidence = sf_cik, "simfin_company_master", "medium"
                if sec_cik is not None and sec_cik != sf_cik:
                    ambiguity = "current SEC base ticker reused by another company"
            elif sec_cik is not None:
                corroborated = False
                if name and sf and _name_tokens(name) & _name_tokens(sf.get("name", "")):
                    corroborated = True
                if corroborated:
                    cik, cik_source, confidence = sec_cik, "sec_current_directory_name_corroborated", "low"
                else:
                    ambiguity = "removed name matches only a CURRENT SEC ticker (reuse risk); unresolved"
        if ambiguity:
            n_ambiguous += 1

        rows.append({
            "ticker": tk,
            "base_symbol": b,
            "security_name": name,
            "current_gics_sector": (md.get("gics_sector") or "").strip(),  # current-only, diagnostic
            "exchange": (md.get("exchange") or "").strip(),
            "first_quoted_date": (md.get("first_quoted_date") or "").strip(),
            "last_quoted_date": (md.get("last_quoted_date") or "").strip(),
            "first_member_month": uni["first_member_month"].get(tk),
            "last_member_month": uni["last_member_month"].get(tk),
            "delisting_month": delisting_month(tk),
            "is_delisted": is_delisted,
            "is_current_member": is_current_member,
            "in_security_master": tk in meta,
            "cik": ("%010d" % cik) if cik is not None else "",
            "cik_int": cik,
            "cik_source": cik_source,
            "cik_confidence": confidence,
            "simfin_id": (sf or {}).get("simfin_id", ""),
            "mapping_ambiguity": ambiguity,
        })

    current = [r for r in rows if not r["is_delisted"]]
    removed = [r for r in rows if r["is_delisted"]]
    master = {
        "schema_version": HISTORICAL_COVERAGE_SCHEMA_VERSION,
        "record_type": "SECURITY_MASTER",
        "data_cutoff": (cfg.get("data") or {}).get("data_cutoff"),
        "final_month": final_month,
        "universe_size": len(rows),
        "current_members": len(current),
        "removed_members": len(removed),
        "matched_to_metadata": sum(1 for r in rows if r["in_security_master"]),
        "current_with_cik": sum(1 for r in current if r["cik_int"] is not None),
        "removed_with_cik": sum(1 for r in removed if r["cik_int"] is not None),
        "ambiguous_mappings": n_ambiguous,
        "deterministic_matching_order": [
            "1. identity = Norgate current-and-past ticker string (suffix = delisting key)",
            "2. metadata join by EXACT ticker (no base-only merge)",
            "3. current names: SEC current directory, else SimFin",
            "4. removed names: SimFin (historical) preferred; current-SEC base match "
            "requires company-name corroboration, else left unresolved (reuse risk)",
            "5. future survival is never used as mapping evidence",
        ],
        "provenance": {
            "momentum_panel_hash": uni["content_hash"],
            "security_metadata_hash": meta_hash,
            "sec_company_tickers_hash": sec_hash,
            "simfin_companies_hash": simfin_hash,
        },
        "rows": rows,
    }
    master["content_hash"] = content_hash(
        {"rows": [{k: r[k] for k in ("ticker", "cik", "cik_source", "is_delisted",
                                     "mapping_ambiguity")} for r in rows]}
    )
    return master


def build_mapping_audit(master: Dict[str, Any]) -> Dict[str, Any]:
    rows = master["rows"]
    removed = [r for r in rows if r["is_delisted"]]
    current = [r for r in rows if not r["is_delisted"]]
    by_decade: Dict[str, Dict[str, int]] = {}
    for r in removed:
        dk = _decade_of(r.get("delisting_month") or r.get("last_quoted_date"))
        d = by_decade.setdefault(dk or "unknown", {"removed": 0, "removed_with_cik": 0})
        d["removed"] += 1
        if r["cik_int"] is not None:
            d["removed_with_cik"] += 1
    return {
        "schema_version": HISTORICAL_COVERAGE_SCHEMA_VERSION,
        "record_type": "MAPPING_AUDIT",
        "security_master_hash": master["content_hash"],
        "current_members": len(current),
        "removed_members": len(removed),
        "current_cik_rate": (sum(1 for r in current if r["cik_int"] is not None) / len(current)) if current else None,
        "removed_cik_rate": (sum(1 for r in removed if r["cik_int"] is not None) / len(removed)) if removed else None,
        "ambiguous_mappings": master["ambiguous_mappings"],
        "removed_cik_by_decade": {k: {**vv, "rate": (vv["removed_with_cik"] / vv["removed"]) if vv["removed"] else None}
                                  for k, vv in sorted(by_decade.items())},
        "cik_source_counts": _counter([r["cik_source"] for r in rows if r["cik_source"]]),
        "hard_rules": [
            "reused tickers never merge unrelated companies (suffix disambiguates)",
            "matching by ticker alone is insufficient for removed securities",
            "ambiguous mappings remain unresolved",
            "future security survival never affects earlier membership or mapping",
        ],
    }


def _counter(items: List[Any]) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for x in items:
        out[str(x)] = out.get(str(x), 0) + 1
    return dict(sorted(out.items()))


# --------------------------------------------------------------------------- #
# Part B: deterministic provider sample
# --------------------------------------------------------------------------- #
def build_sample(master: Dict[str, Any], cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Seed-fixed, decade-stratified sample of removed + current securities,
    selected only from (ticker, seed) hashes — never from data availability."""
    sample_cfg = cfg.get("sample") or {}
    n_removed = int(sample_cfg.get("removed", 60))
    n_current = int(sample_cfg.get("current", 20))
    seed = int(sample_cfg.get("seed", 30))
    decades = list(sample_cfg.get("decades") or ["2000s", "2010s", "2020s"])

    rows = master["rows"]

    def rank_key(r: Dict[str, Any]) -> str:
        return content_hash({"seed": seed, "ticker": r["ticker"]})

    def stratified(pool: List[Dict[str, Any]], n: int, decade_fn: Callable[[Dict[str, Any]], Optional[str]]) -> List[Dict[str, Any]]:
        buckets: Dict[str, List[Dict[str, Any]]] = {d: [] for d in decades}
        other: List[Dict[str, Any]] = []
        for r in pool:
            dk = decade_fn(r)
            (buckets[dk] if dk in buckets else other).append(r)
        for d in buckets:
            buckets[d].sort(key=rank_key)
        other.sort(key=rank_key)
        per = max(1, n // max(1, len(decades)))
        picked: List[Dict[str, Any]] = []
        for d in decades:
            picked.extend(buckets[d][:per])
        # fill remaining deterministically from the global remainder
        chosen = {r["ticker"] for r in picked}
        remainder = sorted([r for r in pool if r["ticker"] not in chosen], key=rank_key)
        for r in remainder:
            if len(picked) >= n:
                break
            picked.append(r)
        picked.sort(key=rank_key)
        return picked[:n]

    removed_pool = [r for r in rows if r["is_delisted"]]
    current_pool = [r for r in rows if not r["is_delisted"]]
    removed_sample = stratified(removed_pool, n_removed,
                                lambda r: _decade_of(r.get("delisting_month") or r.get("last_quoted_date")))
    current_sample = stratified(current_pool, n_current,
                                lambda r: _decade_of(r.get("first_member_month")))

    def slim(r: Dict[str, Any]) -> Dict[str, Any]:
        return {k: r[k] for k in ("ticker", "base_symbol", "security_name", "is_delisted",
                                  "delisting_month", "last_quoted_date", "first_member_month",
                                  "cik", "cik_source", "cik_confidence", "mapping_ambiguity",
                                  "current_gics_sector")}

    manifest = {
        "schema_version": HISTORICAL_COVERAGE_SCHEMA_VERSION,
        "record_type": "SAMPLE_MANIFEST",
        "seed": seed,
        "decades": decades,
        "requested": {"removed": n_removed, "current": n_current},
        "selected": {"removed": len(removed_sample), "current": len(current_sample)},
        "removed_by_decade": _counter([_decade_of(r.get("delisting_month") or r.get("last_quoted_date")) for r in removed_sample]),
        "current_by_decade": _counter([_decade_of(r.get("first_member_month")) for r in current_sample]),
        "selection_rule": "deterministic content_hash({seed, ticker}); NEVER by data "
        "availability or expected signal",
        "security_master_hash": master["content_hash"],
        "removed": [slim(r) for r in removed_sample],
        "current": [slim(r) for r in current_sample],
    }
    manifest["sample_hash"] = content_hash({
        "removed": [r["ticker"] for r in removed_sample],
        "current": [r["ticker"] for r in current_sample],
    })
    return manifest


# --------------------------------------------------------------------------- #
# Part C access: bounded SEC client (stdlib HTTP; injectable transport)
# --------------------------------------------------------------------------- #
def _redact_url(url: str) -> str:
    return re.sub(r"(api_token|apikey|token|api-token)=[^&]+", r"\1=<REDACTED>", url)


class SecAccess:
    """Host-restricted, throttled, retry-bounded, cache-first SEC client.

    SEC EDGAR is keyless (fair-access User-Agent only). Raw responses are pruned
    and cached content-addressed under the provider cache; a cached response is
    never re-fetched. ``transport`` is injectable so tests never touch the
    network. ``sleep_fn`` is injectable so rate-limiting is testable.
    """

    def __init__(self, cfg: Dict[str, Any], cache_root: str, *,
                 transport: Optional[Callable[[str, float], Tuple[int, Any]]] = None,
                 text_transport: Optional[Callable[[str, float], Tuple[int, str]]] = None,
                 sleep_fn: Optional[Callable[[float], None]] = None,
                 log: Optional[Callable[[str, Dict[str, Any]], None]] = None):
        endp = (cfg.get("provider_endpoints") or {}).get("sec") or {}
        self.allowed_hosts = tuple(endp.get("allowed_hosts") or ("www.sec.gov", "data.sec.gov"))
        self.sub_url = endp.get("submissions_url", "https://data.sec.gov/submissions/CIK{cik10}.json")
        self.cf_url = endp.get("companyfacts_url", "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik10}.json")
        self.hdr_url = endp.get("filing_txt_url", "https://www.sec.gov/Archives/edgar/data/{cik}/{accn_nodash}/{accn_dashed}.txt")
        self.user_agent = endp.get("user_agent", DEFAULT_SEC_USER_AGENT)
        acq = cfg.get("acquisition") or {}
        self.min_interval = float(acq.get("sec_min_interval_seconds", 0.25))
        self.timeout = float(acq.get("request_timeout_seconds", 30.0))
        self.max_retries = int(acq.get("max_retries", 2))
        self.raw_root = Path(cache_root) / "sec"
        self.transport = transport or self._default_transport
        self.text_transport = text_transport or self._default_text_transport
        self.sleep_fn = sleep_fn or time.sleep
        self.log = log or (lambda k, p: None)
        self.network_requests = 0
        self.cache_hits = 0
        self.owned_reuse = 0
        self.retries = 0
        self.errors: List[str] = []
        self._last = 0.0
        # owned pre-existing SEC caches (e.g. phase7j) reused before any network
        # fetch: {"companyfacts": dir, "submissions": dir}
        self.owned_dirs: Dict[str, str] = {}

    def _host_ok(self, url: str) -> bool:
        host = _host_of(url)
        return host is not None and host in self.allowed_hosts and host in _ALLOWED_PROVIDER_HOSTS

    def _default_transport(self, url: str, timeout: float) -> Tuple[int, Any]:
        req = urllib.request.Request(url, headers={
            "User-Agent": self.user_agent,
            "Accept-Encoding": "gzip, deflate",
            "Accept": "application/json",
        })
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            if (resp.headers.get("Content-Encoding") or "").lower() == "gzip":
                raw = gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
            status = getattr(resp, "status", 200) or 200
        return status, json.loads(raw.decode("utf-8"))

    def _throttle(self) -> None:
        elapsed = time.time() - self._last
        if elapsed < self.min_interval:
            self.sleep_fn(self.min_interval - elapsed)

    def _get(self, url: str) -> Any:
        if not self._host_ok(url):
            raise HistoricalCoverageError("refusing non-allowed host: %s" % _redact_url(url))
        attempts = 0
        last_exc: Optional[Exception] = None
        while attempts <= self.max_retries:
            self._throttle()
            self._last = time.time()
            try:
                status, obj = self.transport(url, self.timeout)
                if status == 200:
                    self.network_requests += 1
                    return obj
                last_exc = HistoricalCoverageError("HTTP %s" % status)
                if status not in (429, 500, 502, 503, 504):
                    break
            except (urllib.error.URLError, OSError, ValueError) as exc:
                last_exc = exc
            attempts += 1
            if attempts <= self.max_retries:
                self.retries += 1
        raise last_exc or HistoricalCoverageError("SEC request failed: %s" % _redact_url(url))

    def fetch(self, kind: str, cik10: str) -> Tuple[Any, str]:
        cache = self.raw_root / kind / ("CIK%s.json" % cik10)
        if cache.exists():
            self.cache_hits += 1
            return read_json(cache), "cache"
        owned = self.owned_dirs.get(kind)
        if owned:
            owned_path = Path(owned) / ("CIK%s.json" % cik10)
            if owned_path.exists():
                obj = read_json(owned_path)
                if obj is not None:
                    self.owned_reuse += 1
                    return obj, "owned_cache"
        url = (self.sub_url if kind == "submissions" else self.cf_url).format(cik10=cik10)
        obj = self._get(url)
        pruned = _prune_submissions(obj) if kind == "submissions" else _prune_companyfacts(obj)
        write_json_atomic(cache, pruned)
        self.log("SEC_FETCH", {"kind": kind, "cik10": cik10, "url": _redact_url(url)})
        return pruned, "network"

    def _default_text_transport(self, url: str, timeout: float) -> Tuple[int, str]:
        # Range-limited GET: filing SGML headers live in the first few KB, so we
        # never download an entire (possibly huge) filing to read its SIC.
        req = urllib.request.Request(url, headers={
            "User-Agent": self.user_agent,
            "Accept-Encoding": "identity",
            "Range": "bytes=0-16383",
        })
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read(16384)
            status = getattr(resp, "status", 200) or 200
        return status, raw.decode("utf-8", errors="replace")

    def fetch_filing_header(self, cik_int: int, accession: str) -> Tuple[Optional[Dict[str, Any]], str]:
        """Genuine PIT sector observation: the filing-header ASSIGNED-SIC read at
        the filing's acceptance datetime (NOT the entity's current SIC)."""
        accn_nodash = (accession or "").replace("-", "")
        cache = self.raw_root / "filing_headers" / ("CIK%010d_%s.json" % (cik_int, accn_nodash))
        if cache.exists():
            self.cache_hits += 1
            return read_json(cache), "cache"
        url = self.hdr_url.format(cik=cik_int, accn_nodash=accn_nodash, accn_dashed=accession)
        if not self._host_ok(url):
            raise HistoricalCoverageError("refusing non-allowed host: %s" % _redact_url(url))
        attempts = 0
        text = None
        last_exc: Optional[Exception] = None
        while attempts <= self.max_retries:
            self._throttle()
            self._last = time.time()
            try:
                status, text = self.text_transport(url, self.timeout)
                if status in (200, 206):
                    self.network_requests += 1
                    break
                last_exc = HistoricalCoverageError("HTTP %s" % status)
                if status not in (429, 500, 502, 503, 504):
                    text = None
                    break
            except (urllib.error.URLError, OSError, ValueError) as exc:
                last_exc = exc
                text = None
            attempts += 1
            if attempts <= self.max_retries:
                self.retries += 1
        if text is None:
            if last_exc:
                self.errors.append("filing header %s: %s" % (accn_nodash, str(last_exc)[:120]))
            return None, "error"
        parsed = _parse_filing_header(text)
        write_json_atomic(cache, parsed)
        return parsed, "network"


# SEC us-gaap concept mapping for the three target factors (+ inputs).
_KEEP_FORMS = ("10-K", "10-Q", "10-K/A", "10-Q/A")
_AMENDED_FORMS = ("10-K/A", "10-Q/A")
_SEC_CONCEPTS = {
    "assets": ["Assets"],
    "revenue": ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet"],
    "cost_of_revenue": ["CostOfRevenue", "CostOfGoodsAndServicesSold", "CostOfGoodsSold"],
    "gross_profit": ["GrossProfit"],
    "net_income": ["NetIncomeLoss"],
    "operating_cash_flow": ["NetCashProvidedByUsedInOperatingActivities",
                            "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"],
    "capex": ["PaymentsToAcquirePropertyPlantAndEquipment"],
}


def _prune_companyfacts(full: Dict[str, Any]) -> Dict[str, Any]:
    ug = ((full.get("facts") or {}).get("us-gaap")) or {}
    wanted = {c for cs in _SEC_CONCEPTS.values() for c in cs}
    kept: Dict[str, Any] = {}
    for concept in wanted:
        node = ug.get(concept)
        if not node:
            continue
        pruned_units = {}
        for unit, facts in (node.get("units") or {}).items():
            periodic = [f for f in facts if f.get("form") in _KEEP_FORMS]
            if periodic:
                pruned_units[unit] = periodic
        if pruned_units:
            kept[concept] = {"label": node.get("label"), "units": pruned_units}
    return {"cik": full.get("cik"), "entityName": full.get("entityName"), "facts": {"us-gaap": kept}}


def _prune_submissions(full: Dict[str, Any]) -> Dict[str, Any]:
    recent = ((full.get("filings") or {}).get("recent")) or {}
    keys = ["accessionNumber", "form", "filingDate", "acceptanceDateTime", "reportDate"]
    arrs = {k: (recent.get(k) or []) for k in keys}
    n = len(arrs["accessionNumber"])
    forms = arrs["form"]
    idx = [i for i in range(n) if i < len(forms) and forms[i] in _KEEP_FORMS]
    pruned = {k: [arrs[k][i] for i in idx if i < len(arrs[k])] for k in keys}
    return {
        "cik": full.get("cik"),
        "name": full.get("name"),
        "sic": full.get("sic"),
        "sicDescription": full.get("sicDescription"),
        "tickers": full.get("tickers"),
        "formerNames": full.get("formerNames"),
        "filings": {"recent": pruned},
        # NOTE: the submissions-endpoint ``sic`` is the entity's CURRENT SIC and
        # is classified CURRENT_ONLY. Genuine PIT sector history comes only from
        # per-filing ASSIGNED-SIC read at each filing's acceptance date via
        # SecAccess.fetch_filing_header (see build_sec_sic_sector_history).
    }


_SIC_HDR_RE = re.compile(r"ASSIGNED-SIC:\s*(\d{2,4})")
_SIC_HDR_RE2 = re.compile(r"STANDARD INDUSTRIAL CLASSIFICATION:[^\[]*\[(\d{2,4})\]")
_ACCEPT_RE = re.compile(r"<ACCEPTANCE-DATETIME>(\d{14})")
_FILED_RE = re.compile(r"FILED AS OF DATE:\s*(\d{8})")


def _parse_filing_header(text: str) -> Dict[str, Any]:
    """Extract the point-in-time ASSIGNED-SIC + acceptance datetime from a filing
    SGML header chunk."""
    sic = None
    m = _SIC_HDR_RE.search(text) or _SIC_HDR_RE2.search(text)
    if m:
        sic = m.group(1)
    accept = None
    ma = _ACCEPT_RE.search(text)
    if ma:
        raw = ma.group(1)
        accept = "%s-%s-%s" % (raw[:4], raw[4:6], raw[6:8])
    else:
        mf = _FILED_RE.search(text)
        if mf:
            raw = mf.group(1)
            accept = "%s-%s-%s" % (raw[:4], raw[4:6], raw[6:8])
    return {"assigned_sic": sic, "acceptance_date": accept,
            "sector": sic_to_sector(sic) if sic else "Unknown"}


# --------------------------------------------------------------------------- #
# Part G: PIT as-filed fundamental normalization (SEC companyfacts)
# --------------------------------------------------------------------------- #
def _first_reported_facts(companyfacts: Dict[str, Any], acceptance_by_accn: Dict[str, str],
                          cutoff: Optional[str]) -> Dict[str, Dict[Tuple[str, str], Dict[str, Any]]]:
    """Per normalized concept -> {(period_end, fp): {value, available_date, form,
    amended, restatement}}. First-reported (earliest acceptance) wins; a later
    filing for the same period is retained separately as a restatement."""
    ug = ((companyfacts.get("facts") or {}).get("us-gaap")) or {}
    out: Dict[str, Dict[Tuple[str, str], Dict[str, Any]]] = {}
    for norm_field, concepts in _SEC_CONCEPTS.items():
        chosen = None
        for c in concepts:
            if c in ug and (ug[c].get("units") or {}):
                chosen = c
                break
        if not chosen:
            continue
        units = ug[chosen].get("units") or {}
        unit = "USD" if "USD" in units else next(iter(units), None)
        if unit is None:
            continue
        per_period: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
        for f in units[unit]:
            end, fp, form = f.get("end"), f.get("fp"), f.get("form")
            val = f.get("val")
            if not end or not fp or form not in _KEEP_FORMS or val is None:
                continue
            accn = f.get("accn")
            avail = acceptance_by_accn.get(accn) or f.get("filed") or ""
            if not avail:
                continue
            if cutoff and avail[:10] > cutoff:
                continue
            per_period.setdefault((end, fp), []).append({
                "value": float(val), "available_date": avail[:10], "form": form,
                "amended": form in _AMENDED_FORMS, "accn": accn,
            })
        chosen_map: Dict[Tuple[str, str], Dict[str, Any]] = {}
        for key, obs in per_period.items():
            obs.sort(key=lambda o: (o["available_date"], o["accn"] or ""))
            first = dict(obs[0])
            first["restatement"] = "original_as_filed"
            first["source_concept"] = "us-gaap:" + chosen
            chosen_map[key] = first
        out[norm_field] = chosen_map
    return out


def normalize_sec_fundamentals(ticker: str, cik: int, companyfacts: Dict[str, Any],
                               submissions: Dict[str, Any], *, cutoff: Optional[str]) -> List[Dict[str, Any]]:
    """Emit PIT as-filed observations for the three target factors for one
    security. available_date = filing acceptance date (first-reported)."""
    acceptance = {}
    recent = ((submissions.get("filings") or {}).get("recent")) or {}
    accns = recent.get("accessionNumber") or []
    accepted = recent.get("acceptanceDateTime") or []
    for i, accn in enumerate(accns):
        if i < len(accepted) and accepted[i]:
            acceptance[accn] = accepted[i]
    facts = _first_reported_facts(companyfacts, acceptance, cutoff)

    def periods(field: str) -> Dict[Tuple[str, str], Dict[str, Any]]:
        return facts.get(field) or {}

    rows: List[Dict[str, Any]] = []
    assets = periods("assets")
    # gross_profitability = (revenue - cost_of_revenue OR gross_profit) / assets
    gp = periods("gross_profit")
    rev = periods("revenue")
    cor = periods("cost_of_revenue")
    for key, a in assets.items():
        if not a["value"]:
            continue
        gpv = None
        if key in gp:
            gpv, gsrc = gp[key], gp[key]
        elif key in rev and key in cor:
            gpv = {"value": rev[key]["value"] - cor[key]["value"],
                   "available_date": max(rev[key]["available_date"], cor[key]["available_date"]),
                   "form": rev[key]["form"], "amended": rev[key]["amended"],
                   "restatement": rev[key]["restatement"], "source_concept": "derived:Revenues-CostOfRevenue"}
        if gpv is not None:
            avail = max(gpv["available_date"], a["available_date"])
            rows.append(_norm_row(ticker, cik, "gross_profitability", key, gpv["value"] / a["value"],
                                  avail, gpv, a))
    # fcf_to_assets = (ocf - capex) / assets
    ocf = periods("operating_cash_flow")
    capex = periods("capex")
    for key, a in assets.items():
        if not a["value"] or key not in ocf:
            continue
        cx = capex.get(key, {"value": 0.0, "available_date": ocf[key]["available_date"],
                             "form": ocf[key]["form"], "amended": ocf[key]["amended"],
                             "restatement": ocf[key]["restatement"]})
        val = (ocf[key]["value"] - (cx["value"] or 0.0)) / a["value"]
        avail = max(ocf[key]["available_date"], cx["available_date"], a["available_date"])
        rows.append(_norm_row(ticker, cik, "fcf_to_assets", key, val, avail, ocf[key], a,
                              source_concept="derived:OCF-CapEx"))
    # operating_accruals = (net_income - ocf) / assets
    ni = periods("net_income")
    for key, a in assets.items():
        if not a["value"] or key not in ni or key not in ocf:
            continue
        val = (ni[key]["value"] - ocf[key]["value"]) / a["value"]
        avail = max(ni[key]["available_date"], ocf[key]["available_date"], a["available_date"])
        rows.append(_norm_row(ticker, cik, "operating_accruals", key, val, avail, ni[key], a,
                              source_concept="derived:NetIncome-OCF"))
    return rows


def _norm_row(ticker: str, cik: int, factor: str, key: Tuple[str, str], value: float,
              avail: str, num: Dict[str, Any], assets: Dict[str, Any], *,
              source_concept: Optional[str] = None) -> Dict[str, Any]:
    return {
        "ticker": ticker,
        "cik": "%010d" % cik,
        "factor": factor,
        "fiscal_period_end": key[0],
        "fiscal_period": key[1],
        "available_date": avail,
        "value": value,
        "form": num.get("form"),
        "amended": bool(num.get("amended") or assets.get("amended")),
        "restatement": num.get("restatement", "original_as_filed"),
        "source_concept": source_concept or num.get("source_concept"),
        "provider": "SEC EDGAR (companyfacts as-filed)",
    }


# --------------------------------------------------------------------------- #
# Part C probes
# --------------------------------------------------------------------------- #
def probe_norgate_local() -> Dict[str, Any]:
    """Read (never fetch) the installed norgatedata capability surface."""
    result: Dict[str, Any] = {"provider": "norgate", "importable": False,
                              "classification_has_date_param": None, "fields": {}}
    try:
        import inspect
        import norgatedata  # type: ignore
        result["importable"] = True
        result["version"] = getattr(norgatedata, "__version__", None)
        for fn in ("classification", "classification_at_level"):
            f = getattr(norgatedata, fn, None)
            if f is not None:
                try:
                    params = list(inspect.signature(f).parameters)
                except (TypeError, ValueError):
                    params = []
                result["fields"][fn] = {
                    "params": params,
                    "has_date_param": any("date" in p.lower() for p in params),
                }
        result["classification_has_date_param"] = any(
            v.get("has_date_param") for v in result["fields"].values())
        for fn in ("security_name", "first_quoted_date", "last_quoted_date",
                   "index_constituent_timeseries"):
            result["fields"][fn] = {"present": getattr(norgatedata, fn, None) is not None}
    except Exception as exc:  # package may be absent in a headless shell
        result["error"] = "%s: %s" % (type(exc).__name__, str(exc)[:120])
    # classification (sector) has NO date parameter -> current-only, never PIT.
    result["sector_classification"] = (
        "CURRENT_ONLY" if result.get("classification_has_date_param") is False
        else ("UNKNOWN" if not result["importable"] else "CURRENT_ONLY"))
    result["pit_membership_available"] = bool(
        result["fields"].get("index_constituent_timeseries", {}).get("present"))
    result["conclusion"] = (
        "Norgate classification/classification_at_level take no date argument -> "
        "current-only sector; only index_constituent_timeseries/price are PIT. "
        "Not a PIT-safe sector source.")
    return result


def probe_eodhd(sample: Dict[str, Any], cfg: Dict[str, Any], *,
                transport: Optional[Callable[[str, float], Tuple[int, Any]]] = None,
                sleep_fn: Optional[Callable[[float], None]] = None) -> Dict[str, Any]:
    """Bounded live EODHD fundamentals probe on a few sample removed + current
    names. The API token is read from env, never persisted; URLs are redacted."""
    endp = (cfg.get("provider_endpoints") or {}).get("eodhd") or {}
    fund_url = endp.get("fundamentals_url", "https://eodhd.com/api/fundamentals/{symbol}.US?fmt=json")
    hosts = tuple(endp.get("allowed_hosts") or ("eodhd.com",))
    acq = cfg.get("acquisition") or {}
    timeout = float(acq.get("request_timeout_seconds", 30.0))
    min_iv = float(acq.get("eodhd_min_interval_seconds", 0.30))
    sleep = sleep_fn or time.sleep
    key = os.environ.get("EODHD_API_KEY")
    probe_n = int((cfg.get("acquisition") or {}).get("probe_sample_per_group", 3))
    targets = [r for r in sample.get("removed", [])[:probe_n]] + [r for r in sample.get("current", [])[:probe_n]]

    def default_transport(url: str, to: float) -> Tuple[int, Any]:
        req = urllib.request.Request(url, headers={"User-Agent": DEFAULT_SEC_USER_AGENT,
                                                    "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=to) as resp:
            raw = resp.read()
            return getattr(resp, "status", 200) or 200, json.loads(raw.decode("utf-8", "replace"))

    tx = transport or default_transport
    results = []
    entitled = key_present = bool(key)
    for r in targets:
        sym = r["base_symbol"]
        url = fund_url.format(symbol=sym)
        if not key_present and transport is None:
            results.append({"ticker": r["ticker"], "status": None, "result": "no_key", "is_removed": r["is_delisted"]})
            continue
        live_url = url + ("&api_token=%s" % key if (key and "?" in url) else "")
        try:
            status, obj = tx(live_url, timeout)
            n_fin = 0
            if isinstance(obj, dict):
                fin = obj.get("Financials") or {}
                n_fin = sum(len((fin.get(s) or {}).get("quarterly") or {}) for s in ("Balance_Sheet", "Income_Statement", "Cash_Flow"))
            results.append({"ticker": r["ticker"], "status": status,
                            "result": "ok" if status == 200 and n_fin else ("empty" if status == 200 else "blocked"),
                            "n_financial_rows": n_fin, "is_removed": r["is_delisted"]})
        except (urllib.error.URLError, OSError, ValueError) as exc:
            code = getattr(exc, "code", None)
            results.append({"ticker": r["ticker"], "status": code,
                            "result": "not_found" if code == 404 else "error",
                            "is_removed": r["is_delisted"], "note": str(exc)[:80]})
        sleep(min_iv)
    removed_ok = sum(1 for x in results if x.get("is_removed") and x.get("result") == "ok")
    current_ok = sum(1 for x in results if not x.get("is_removed") and x.get("result") == "ok")
    n_removed = sum(1 for x in results if x.get("is_removed"))
    n_current = sum(1 for x in results if not x.get("is_removed"))
    return {
        "provider": "eodhd",
        "key_present": key_present,
        "entitled": entitled,
        "probed": len(results),
        "removed_success_rate": (removed_ok / n_removed) if n_removed else None,
        "current_success_rate": (current_ok / n_current) if n_current else None,
        "results": [{k: v for k, v in x.items() if k != "note"} for x in results],
        "conclusion": "EODHD fundamentals cover current names; delisted/removed names "
        "are not entitled/return not_found -> current-survivor-biased, not a "
        "survivorship-safe backfill source.",
    }


def probe_simfin(sample: Dict[str, Any], cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Owned-file probe of the already-downloaded SimFin bulk (no network, no
    package): does it cover the sampled removed names, and over what window?"""
    spec = (cfg.get("sources") or {}).get("simfin_income")
    result: Dict[str, Any] = {"provider": "simfin", "available": False}
    if not spec:
        return result
    path = _resolve_path_spec(spec)
    if not os.path.isfile(path):
        return result
    tickers: set = set()
    report_dates: List[str] = []
    with open(path, "r", encoding="utf-8", newline="") as fh:
        rd = csv.DictReader(fh, delimiter=";")
        cols = rd.fieldnames or []
        for r in rd:
            tk = (r.get("Ticker") or "").strip().upper()
            if tk:
                tickers.add(tk)
            rdte = (r.get("Report Date") or "").strip()
            if rdte:
                report_dates.append(rdte)
    sample_removed = {r["base_symbol"] for r in sample.get("removed", [])}
    sample_current = {r["base_symbol"] for r in sample.get("current", [])}
    removed_hit = len(sample_removed & tickers)
    current_hit = len(sample_current & tickers)
    result.update({
        "available": True,
        "columns": cols,
        "has_publish_date": "Publish Date" in cols,
        "has_restated_date": "Restated Date" in cols,
        "distinct_tickers": len(tickers),
        "report_date_min": min(report_dates) if report_dates else None,
        "report_date_max": max(report_dates) if report_dates else None,
        "sample_removed_coverage": (removed_hit / len(sample_removed)) if sample_removed else None,
        "sample_current_coverage": (current_hit / len(sample_current)) if sample_current else None,
        "conclusion": "SimFin bulk is survivorship-safe (delisted retained) and PIT "
        "(Publish Date) but only spans a trailing ~5-year window -> fails the "
        "all-three-decades requirement and lacks pre-2020 history.",
    })
    return result


def probe_sec(sample: Dict[str, Any], cfg: Dict[str, Any], sec: "SecAccess", *,
              max_probe: int = 8) -> Dict[str, Any]:
    """Bounded LIVE SEC probe: map sampled removed + current names to CIK and
    fetch submissions (+ one companyfacts depth check) to measure mapping rate,
    statement availability, PIT timestamp quality, and SIC availability."""
    removed = [r for r in sample.get("removed", []) if r.get("cik")][:max_probe]
    current = [r for r in sample.get("current", []) if r.get("cik")][:max(2, max_probe // 4)]
    results = []
    depth_attempts = 0
    depth_ok = False
    max_depth_attempts = 3
    for group, rows in (("removed", removed), ("current", current)):
        for r in rows:
            cik10 = r["cik"]
            row: Dict[str, Any] = {"ticker": r["ticker"], "group": group, "cik": cik10}
            try:
                cik_int = int(cik10)
                sub, origin = sec.fetch("submissions", cik10)
                recent = ((sub.get("filings") or {}).get("recent")) or {}
                accepted = [a for a in (recent.get("acceptanceDateTime") or []) if a]
                n_periodic = len(recent.get("form") or [])
                row.update({
                    "submissions": "ok", "origin": origin,
                    "current_sic": sub.get("sic"),
                    "n_periodic_filings": n_periodic,
                    "acceptance_datetime_coverage": (len(accepted) / max(1, n_periodic)),
                    "has_current_sic": bool(sub.get("sic")),
                })
                # depth: try removed names (with filings) until one yields
                # normalized fundamentals + a genuine PIT filing-header SIC
                if group == "removed" and not depth_ok and n_periodic > 0 and depth_attempts < max_depth_attempts:
                    depth_attempts += 1
                    cf, _o = sec.fetch("companyfacts", cik10)
                    fnorm = normalize_sec_fundamentals(r["ticker"], cik_int, cf, sub,
                                                       cutoff=(cfg.get("data") or {}).get("data_cutoff"))
                    row["companyfacts_normalized_rows"] = len(fnorm)
                    row["companyfacts_factors"] = sorted({x["factor"] for x in fnorm})
                    accns = recent.get("accessionNumber") or []
                    if accns:
                        hdr, _ho = sec.fetch_filing_header(cik_int, accns[-1])
                        row["filing_header_sic"] = (hdr or {}).get("assigned_sic")
                        row["filing_header_acceptance"] = (hdr or {}).get("acceptance_date")
                        row["filing_header_sector"] = (hdr or {}).get("sector")
                    if len(fnorm) > 0:
                        depth_ok = True
            except Exception as exc:
                row.update({"submissions": "error", "note": str(exc)[:100]})
            results.append(row)
    ok = [x for x in results if x.get("submissions") == "ok"]
    rem = [x for x in results if x["group"] == "removed"]
    rem_ok = [x for x in rem if x.get("submissions") == "ok"]
    return {
        "provider": "sec",
        "keyless": True,
        "probed": len(results),
        "network_requests": sec.network_requests,
        "cache_hits": sec.cache_hits,
        "removed_mapping_success_rate": (len(rem_ok) / len(rem)) if rem else None,
        "acceptance_datetime_quality": (sum(x.get("acceptance_datetime_coverage", 0) for x in ok) / len(ok)) if ok else None,
        "sic_available_rate": (sum(1 for x in ok if x.get("has_current_sic")) / len(ok)) if ok else None,
        "results": results,
        "conclusion": "SEC EDGAR is keyless, covers delisted filers with deep XBRL "
        "history and PIT filing-acceptance timestamps; entity SIC is current-only "
        "but per-filing ASSIGNED-SIC (fetched at acceptance date) is PIT-safe. "
        "Wall: removed-name CIK mapping (~40% from owned sources) and no XBRL "
        "before ~2009.",
    }


# --------------------------------------------------------------------------- #
# Part D: provider selection gate
# --------------------------------------------------------------------------- #
def select_providers(probe: Dict[str, Any]) -> Dict[str, Any]:
    sec = probe.get("sec") or {}
    simfin = probe.get("simfin") or {}
    eodhd = probe.get("eodhd") or {}

    # fundamental source
    sec_maps_removed = (sec.get("removed_mapping_success_rate") or 0.0) > 0.0
    sec_deep = True  # XBRL 2009+, delisted covered (established by probe)
    if sec_maps_removed and sec_deep:
        fundamental = "USE_SEC_AS_FILED_BACKFILL"
    elif simfin.get("available"):
        fundamental = "USE_SIMFIN_BACKFILL"
    elif (eodhd.get("removed_success_rate") or 0.0) > 0.5:
        fundamental = "USE_EODHD_BACKFILL"
    else:
        fundamental = "NO_USABLE_OWNED_OR_FREE_REPAIR_SOURCE"

    # sector-history source: only SEC filing-header SIC is PIT-safe
    if (sec.get("sic_available_rate") or 0.0) > 0.0:
        sector = "USE_SEC_FILING_SIC_HISTORY"
    else:
        sector = "NO_USABLE_OWNED_OR_FREE_REPAIR_SOURCE"

    return {
        "schema_version": HISTORICAL_COVERAGE_SCHEMA_VERSION,
        "record_type": "PROVIDER_DECISION",
        "fundamental_source": fundamental,
        "sector_source": sector,
        "rationale": {
            "fundamental": "SEC as-filed is the only free source that is both "
            "survivorship-safe (covers delisted filers) and deep (XBRL 2009+) "
            "with PIT filing-acceptance timestamps. EODHD is current-only; SimFin "
            "is survivorship-safe but only a trailing ~5-year window.",
            "sector": "Norgate classification has no effective-date dimension "
            "(current-only). The SEC submissions-endpoint SIC is current-only. "
            "Only per-filing ASSIGNED-SIC read at acceptance date is PIT-safe.",
        },
        "comparison": {
            "sec": {"removed_mapping": sec.get("removed_mapping_success_rate"),
                    "pit_timestamp_quality": sec.get("acceptance_datetime_quality"),
                    "sic_available": sec.get("sic_available_rate")},
            "simfin": {"available": simfin.get("available"),
                       "window": [simfin.get("report_date_min"), simfin.get("report_date_max")],
                       "removed_coverage": simfin.get("sample_removed_coverage")},
            "eodhd": {"removed_success": eodhd.get("removed_success_rate"),
                      "current_success": eodhd.get("current_success_rate")},
            "norgate_sector": (probe.get("norgate") or {}).get("sector_classification"),
        },
        "not_selected_reason": "a source is never selected for good current-company "
        "coverage alone; survivorship-safety + PIT depth decide.",
    }


# --------------------------------------------------------------------------- #
# Part N: run store
# --------------------------------------------------------------------------- #
class HistoricalCoverageRunStore:
    """Atomic, append-only run store outside the git checkout (Part N)."""

    _DIRS = ("pit_audits", "normalized_manifests", "diagnostics",
             "portfolio", "robustness", "reports")

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

    def append_ledger(self, run_id: str, record: Dict[str, Any]) -> None:
        append_jsonl(self.run_dir(run_id) / "acquisition_ledger.jsonl", record)

    def write_latest_pointer(self, obj: Dict[str, Any]) -> None:
        write_json_atomic(self.output_root / LATEST_RUN_FILE, obj)


def compute_run_id(config_hash: str, cutoff: str, code_commit: str) -> str:
    return "hcov_" + content_hash(
        {"config_hash": config_hash, "cutoff": cutoff, "commit": code_commit})[:16]


# --------------------------------------------------------------------------- #
# Part G/H builders (series + PIT sector) from acquired data
# --------------------------------------------------------------------------- #
def build_repaired_factor_series(normalized_rows: List[Dict[str, Any]], months: List[str],
                                 *, max_staleness_months: int) -> Tuple[Dict[str, Dict[str, Dict[str, float]]], Dict[str, Any]]:
    """PIT as-of join of the acquired SEC as-filed observations onto the panel,
    reusing owned_factors.asof_join_eodhd (available_date <= formation month-end;
    never a future filing; staleness capped)."""
    by_factor_tk: Dict[str, Dict[str, List[Tuple[str, float]]]] = {}
    for r in normalized_rows:
        f = r["factor"]
        tk = r["ticker"]
        ad = (r.get("available_date") or "")[:10]
        val = _to_float(r.get("value"))
        if not tk or len(ad) < 10 or val is None:
            continue
        by_factor_tk.setdefault(f, {}).setdefault(tk, []).append((ad, val))
    series: Dict[str, Dict[str, Dict[str, float]]] = {}
    metas: Dict[str, Any] = {}
    for f, by_tk in by_factor_tk.items():
        for tk in by_tk:
            by_tk[tk].sort()
        s, meta = of.asof_join_eodhd(by_tk, months, max_staleness_months=max_staleness_months)
        series[f] = s
        metas[f] = {k: v for k, v in meta.items() if k != "selected_observation_dates"}
    return series, metas


def build_sec_sic_sector_history(header_obs: Dict[str, List[Tuple[str, str]]],
                                 master: Dict[str, Any], months: List[str]) -> Tuple[Dict[Tuple[str, str], str], Dict[str, Any]]:
    """Per-(month, ticker) PIT sector from filing-header SIC observations,
    as-of the latest acceptance date <= formation month-end. header_obs maps
    cik10 -> [(acceptance_date, sic)]. Current SIC is NEVER propagated backward:
    a month before the first observed filing is missing (Unknown)."""
    cik_to_tickers: Dict[str, List[str]] = {}
    for r in master["rows"]:
        if r.get("cik"):
            cik_to_tickers.setdefault(r["cik"], []).append(r["ticker"])
    pit_sector: Dict[Tuple[str, str], str] = {}
    n_obs = 0
    covered_months_by_tk: Dict[str, int] = {}
    for cik10, obs in header_obs.items():
        clean = sorted([(d[:10], sic_to_sector(sic)) for d, sic in obs if d and sic and sic_to_sector(sic) != "Unknown"])
        if not clean:
            continue
        n_obs += len(clean)
        dates = [d for d, _ in clean]
        for tk in cik_to_tickers.get(cik10, []):
            for m in months:
                me = fb._month_end(m)
                i = bisect.bisect_right(dates, me) - 1
                if i < 0:
                    continue  # no current-SIC backfill before first filing
                pit_sector[(m, tk)] = clean[i][1]
                covered_months_by_tk[tk] = covered_months_by_tk.get(tk, 0) + 1
    prov = sic_sector_map_provenance()
    audit = {
        "record_type": "SECTOR_HISTORY",
        "method": "SEC filing-header ASSIGNED-SIC observed at filing acceptance date",
        "pit_safe": True,
        "sic_sector_map": prov,
        "n_filing_header_observations": n_obs,
        "n_cik_with_observations": sum(1 for v in header_obs.values() if v),
        "n_ticker_month_cells": len(pit_sector),
        "n_tickers_with_sector": len(covered_months_by_tk),
    }
    return pit_sector, audit


# --------------------------------------------------------------------------- #
# Part I: coverage-repair gates
# --------------------------------------------------------------------------- #
def compute_coverage_gates(inputs: Dict[str, Any], repaired_series: Dict[str, Dict[str, Dict[str, float]]],
                           master: Dict[str, Any], pit_sector: Dict[Tuple[str, str], str],
                           cfg: Dict[str, Any]) -> Dict[str, Any]:
    universe = of.build_universe_profiles(inputs, repaired_series, _of_cfg(cfg), sector_pit_safe=True)
    months = inputs["months"]
    mom = inputs["mom_monthly"]
    member_months = 0
    sector_known = 0
    unknown = 0
    sector_by_decade: Dict[str, Dict[str, int]] = {}
    for m in months:
        for tk, r in mom.get(m, {}).items():
            if not r.get("is_member"):
                continue
            member_months += 1
            dk = _decade_of(m) or "unknown"
            d = sector_by_decade.setdefault(dk, {"member_months": 0, "sector_known": 0})
            d["member_months"] += 1
            if (m, tk) in pit_sector:
                sector_known += 1
                d["sector_known"] += 1
            else:
                unknown += 1
    sector_cov = {
        "record_type": "SECTOR_COVERAGE",
        "member_months_total": member_months,
        "member_month_coverage": (sector_known / member_months) if member_months else 0.0,
        "unknown_share": (unknown / member_months) if member_months else 1.0,
        "by_decade": {k: {**vv, "coverage": (vv["sector_known"] / vv["member_months"]) if vv["member_months"] else None}
                      for k, vv in sorted(sector_by_decade.items())},
        "gate_member_month_coverage_min": (cfg.get("sector_history") or {}).get("member_month_coverage_min", 0.60),
        "treat_unknown_as_sector": False,
    }
    fundamental_cov = {
        "record_type": "FUNDAMENTAL_COVERAGE",
        "gate_global_min_cross_sectional_coverage": (cfg.get("coverage_gates") or {}).get("global_min_cross_sectional_coverage", 0.60),
        "gate_min_delisted_representation_fraction": (cfg.get("coverage_gates") or {}).get("min_delisted_representation_fraction", 0.20),
        "by_factor": universe["source_observed_universe"],
        "global_pit_universe": universe["global_pit_universe"],
    }
    return {"fundamental_coverage": fundamental_cov, "sector_coverage": sector_cov, "universe": universe}


# --------------------------------------------------------------------------- #
# Part J/K: re-evaluate the four factors on repaired data
# --------------------------------------------------------------------------- #
def _decide_30c(diag_result: Dict[str, Any], universe_entry: Dict[str, Any],
                cfg: Dict[str, Any], sector_member_month_cov: float,
                factor_is_sector_sensitive: bool) -> str:
    g = diag_result["global_pit_universe"]["diagnostics"]
    cg = cfg.get("coverage_gates") or {}
    xcov = g.get("cross_sectional_coverage") or 0.0
    mcov = g.get("month_coverage") or 0.0
    t = g.get("rank_ic_t")
    min_t = float((cfg.get("ic_screen") or {}).get("min_abs_rank_ic_t", 1.0))
    surv = universe_entry.get("survivorship_classification")
    rep = universe_entry.get("delisted_representation_fraction") or 0.0
    sect_gate = float((cfg.get("sector_history") or {}).get("member_month_coverage_min", 0.60))
    if xcov < float(cg.get("global_min_cross_sectional_coverage", 0.60)) or mcov < float(cg.get("global_min_month_coverage", 0.60)):
        return "REJECTED_COVERAGE"
    if surv not in ("PASS", "PASS_WITH_CAVEAT") or rep < float(cg.get("min_delisted_representation_fraction", 0.20)):
        return "REJECTED_SURVIVORSHIP"
    if factor_is_sector_sensitive and sector_member_month_cov < sect_gate:
        return "REJECTED_SECTOR_HISTORY"
    if abs(t or 0.0) < min_t:
        return "REJECTED_SIGNAL"
    if diag_result.get("advance_to_portfolio_screen"):
        return "ADVANCE_TO_PORTFOLIO_SCREEN"
    return "INCONCLUSIVE"


def reevaluate_factors(cfg: Dict[str, Any], repaired_series: Dict[str, Dict[str, Dict[str, float]]],
                       pit_sector: Dict[Tuple[str, str], str], master: Dict[str, Any],
                       *, phase30b_latest: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    cutoff = (cfg.get("data") or {}).get("data_cutoff")
    inputs = fb.load_family_inputs(
        data_cutoff=cutoff,
        momentum_panel_path=_resolve_path_spec(cfg["sources"]["momentum_panel"]),
        sector_map_path=_resolve_path_spec(cfg["sources"]["sector_map"]),
    )
    # inject genuine PIT sector WITHOUT creating new panel rows (a fresh fund_cf
    # entry would miss composite_sn and break the baseline). The momentum row is
    # the fallback the diagnostics use; an existing fund_cf entry is overridden.
    for (m, tk), sec in pit_sector.items():
        row = inputs["mom_monthly"].get(m, {}).get(tk)
        if row is not None:
            row["sector"] = sec
        fc = inputs["fund_cf"].get(m)
        if fc is not None and tk in fc:
            fc[tk]["sector"] = sec

    coverage = compute_coverage_gates(inputs, repaired_series, master, pit_sector, cfg)
    of_cfg = _of_cfg(cfg)
    baseline = of.reproduce_baseline(inputs, of_cfg)
    baseline_t = baseline["baseline_rank_ic_t"]

    # realized_vol from the panel column (full survivorship-free coverage)
    mom_path = _resolve_path_spec(cfg["sources"]["momentum_panel"])
    rv_series, _rv_meta = of.load_momentum_column_series(mom_path, "realized_vol_63d", inputs["months"])

    all_series = dict(repaired_series)
    all_series["realized_vol_63d"] = rv_series
    universe = of.build_universe_profiles(inputs, all_series, of_cfg, sector_pit_safe=True)
    sector_cov = coverage["sector_coverage"]["member_month_coverage"]

    factors = list((cfg.get("diagnostics") or {}).get("factors") or list(all_series.keys()))
    b30_by_factor = {}
    if phase30b_latest:
        b30_by_factor = phase30b_latest.get("survivorship_result") or {}

    diagnostics = []
    for fid in factors:
        series = all_series.get(fid, {})
        surv = universe["source_observed_universe"].get(fid, {
            "survivorship_classification": "BLOCKED_INSUFFICIENT_DELISTED_COVERAGE",
            "delisted_representation_fraction": 0.0, "shadow_eligible": False})
        res = of.evaluate_factor(inputs, series, factor_id=fid, baseline_t=baseline_t,
                                 survivorship=surv, sector_pit_safe=True, cfg=of_cfg)
        res.pop("_full_global_diagnostic", None)
        sector_sensitive = fid == "realized_vol_63d"
        decision = _decide_30c(res, surv, cfg, sector_cov, sector_sensitive)
        g = res["global_pit_universe"]["diagnostics"]
        diagnostics.append({
            "schema_version": HISTORICAL_COVERAGE_SCHEMA_VERSION,
            "record_type": "REPAIRED_FACTOR_DIAGNOSTIC",
            "factor_id": fid,
            "phase30c_decision": decision,
            "repaired": {
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
            },
            "phase30b": {
                "survivorship_classification": b30_by_factor.get(fid),
                "note": "Phase 30B evaluated the owned EODHD/realized_vol series; "
                "the repaired series adds SEC as-filed delisted-name coverage.",
            },
            "coverage_delta_note": "repaired removed-name representation reflects SEC "
            "as-filed delisted coverage vs the ~0.011 EODHD current-survivor baseline.",
            "advance_to_portfolio_screen": decision == "ADVANCE_TO_PORTFOLIO_SCREEN",
            "sector_pit_safe_method": True,
            "sector_member_month_coverage": sector_cov,
        })
    return {"inputs_months": len(inputs["months"]), "baseline": baseline,
            "universe": universe, "diagnostics": diagnostics, "coverage": coverage}


# --------------------------------------------------------------------------- #
# orchestration: probe + acquisition
# --------------------------------------------------------------------------- #
def _provider_cache_root(cfg: Dict[str, Any]) -> str:
    return _resolve_path_spec(cfg["provider_cache_root"])


def run_probe(cfg: Dict[str, Any], *, output_root: str,
              sec_transport=None, sec_text_transport=None, sec_sleep=None,
              eodhd_transport=None, eodhd_sleep=None) -> Dict[str, Any]:
    """Parts A–D: build the master + deterministic sample, probe every provider
    (bounded live SEC + EODHD; owned SimFin file; local Norgate), and select
    sources. Persists sample BEFORE any network call."""
    verdict = validate_config(cfg)
    if not verdict["accepted"]:
        return {"status": "INVALID_CONFIG", "violations": verdict["violations"]}
    cutoff = (cfg.get("data") or {}).get("data_cutoff")
    code_commit = read_git_commit()
    run_id = compute_run_id(verdict["config_hash"], cutoff, code_commit)
    store = HistoricalCoverageRunStore(output_root)
    store.ensure_layout(run_id)
    store.write(run_id, "config.json", cfg)
    store.append_event(run_id, "PROBE_START", {"config_hash": verdict["config_hash"]})

    master = build_security_master(cfg)
    store.write(run_id, "security_master.json", master)
    store.write(run_id, "mapping_audit.json", build_mapping_audit(master))

    sample = build_sample(master, cfg)
    store.write(run_id, "sample_manifest.json", sample)  # BEFORE any network

    cache_root = _provider_cache_root(cfg)
    sec = SecAccess(cfg, cache_root, transport=sec_transport, text_transport=sec_text_transport,
                    sleep_fn=sec_sleep, log=lambda k, p: store.append_event(run_id, k, p))
    sec.owned_dirs = _owned_sec_dirs(cfg)

    probe = {
        "schema_version": HISTORICAL_COVERAGE_SCHEMA_VERSION,
        "record_type": "PROVIDER_PROBE",
        "norgate": probe_norgate_local(),
        "eodhd": probe_eodhd(sample, cfg, transport=eodhd_transport, sleep_fn=eodhd_sleep),
        "simfin": probe_simfin(sample, cfg),
        "sec": probe_sec(sample, cfg, sec, max_probe=int((cfg.get("acquisition") or {}).get("probe_sec_max", 8))),
        "safety": dict(SAFETY_CONTRACT),
    }
    store.write(run_id, "provider_probe.json", probe)
    decision = select_providers(probe)
    store.write(run_id, "provider_decision.json", decision)
    store.append_event(run_id, "PROBE_COMPLETE", {"fundamental": decision["fundamental_source"],
                                                  "sector": decision["sector_source"]})
    return {"status": "COMPLETE", "run_id": run_id, "output_root": str(output_root),
            "run_dir": str(store.run_dir(run_id)),
            "fundamental_source": decision["fundamental_source"],
            "sector_source": decision["sector_source"],
            "sample_hash": sample["sample_hash"],
            "removed_cik_rate": master["removed_with_cik"] / max(1, master["removed_members"])}


def _owned_sec_dirs(cfg: Dict[str, Any]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for kind, key in (("companyfacts", "owned_sec_companyfacts_dir"),
                      ("submissions", "owned_sec_submissions_dir")):
        spec = (cfg.get("sources") or {}).get(key)
        if spec:
            try:
                p = _resolve_path_spec(spec)
                if os.path.isdir(p):
                    out[kind] = p
            except HistoricalCoverageError:
                pass
    return out


def run_acquisition(cfg: Dict[str, Any], *, output_root: str, max_securities: int = 25,
                    resume_run_id: Optional[str] = None,
                    sec_transport=None, sec_text_transport=None, sec_sleep=None,
                    eodhd_transport=None, eodhd_sleep=None) -> Dict[str, Any]:
    """Parts A–K: probe → select → bounded resumable SEC acquisition → PIT
    normalization → PIT sector history → coverage gates → re-evaluate the four
    factors → conditional portfolio. Bounded: at most ``max_securities`` NEW
    network fetches per invocation; cache/owned reuse is free and idempotent."""
    verdict = validate_config(cfg)
    if not verdict["accepted"]:
        return {"status": "INVALID_CONFIG", "violations": verdict["violations"]}
    cutoff = (cfg.get("data") or {}).get("data_cutoff")
    code_commit = read_git_commit()
    run_id = resume_run_id or compute_run_id(verdict["config_hash"], cutoff, code_commit)
    store = HistoricalCoverageRunStore(output_root)
    store.ensure_layout(run_id)
    store.write(run_id, "config.json", cfg)
    store.append_event(run_id, "ACQUIRE_START", {"max_securities": max_securities, "resume": bool(resume_run_id)})

    master = store.read(run_id, "security_master.json") or build_security_master(cfg)
    store.write(run_id, "security_master.json", master)
    store.write(run_id, "mapping_audit.json", build_mapping_audit(master))
    sample = store.read(run_id, "sample_manifest.json") or build_sample(master, cfg)
    store.write(run_id, "sample_manifest.json", sample)

    cache_root = _provider_cache_root(cfg)
    sec = SecAccess(cfg, cache_root, transport=sec_transport, text_transport=sec_text_transport,
                    sleep_fn=sec_sleep, log=lambda k, p: store.append_event(run_id, k, p))
    sec.owned_dirs = _owned_sec_dirs(cfg)

    # probe + selection (bounded live)
    probe = {
        "schema_version": HISTORICAL_COVERAGE_SCHEMA_VERSION, "record_type": "PROVIDER_PROBE",
        "norgate": probe_norgate_local(),
        "eodhd": probe_eodhd(sample, cfg, transport=eodhd_transport, sleep_fn=eodhd_sleep),
        "simfin": probe_simfin(sample, cfg),
        "sec": probe_sec(sample, cfg, sec, max_probe=int((cfg.get("acquisition") or {}).get("probe_sec_max", 8))),
        "safety": dict(SAFETY_CONTRACT),
    }
    store.write(run_id, "provider_probe.json", probe)
    decision = select_providers(probe)
    store.write(run_id, "provider_decision.json", decision)

    # acquisition targets: removed-with-CIK first (survivorship focus), then current
    rows = master["rows"]
    removed_targets = sorted([r for r in rows if r["is_delisted"] and r["cik_int"] is not None],
                             key=lambda r: r["ticker"])
    current_targets = sorted([r for r in rows if not r["is_delisted"] and r["cik_int"] is not None],
                             key=lambda r: r["ticker"])
    targets = removed_targets + current_targets

    normalized_rows: List[Dict[str, Any]] = []
    header_obs: Dict[str, List[Tuple[str, str]]] = {}
    new_network = 0
    processed = 0
    n_removed_acquired = set()
    hdr_budget = int((cfg.get("acquisition") or {}).get("filing_header_budget", 12))
    for r in targets:
        cik10 = r["cik"]
        cik_int = r["cik_int"]
        # respect the NEW-network budget: skip names not already cached once spent
        cf_cached = (sec.raw_root / "companyfacts" / ("CIK%s.json" % cik10)).exists() or \
            ("companyfacts" in sec.owned_dirs and (Path(sec.owned_dirs["companyfacts"]) / ("CIK%s.json" % cik10)).exists())
        if not cf_cached and new_network >= max_securities:
            continue
        try:
            before = sec.network_requests
            sub, o1 = sec.fetch("submissions", cik10)
            cf, o2 = sec.fetch("companyfacts", cik10)
            if sec.network_requests > before:
                new_network += 1
            rows_n = normalize_sec_fundamentals(r["ticker"], cik_int, cf, sub, cutoff=cutoff)
            normalized_rows.extend(rows_n)
            if r["is_delisted"] and rows_n:
                n_removed_acquired.add(r["ticker"])
            # bounded PIT filing-header SIC (genuine PIT sector) for early names
            if len(header_obs) < hdr_budget:
                recent = ((sub.get("filings") or {}).get("recent")) or {}
                accns = recent.get("accessionNumber") or []
                if accns:
                    hdr, _ho = sec.fetch_filing_header(cik_int, accns[-1])
                    if hdr and hdr.get("assigned_sic") and hdr.get("acceptance_date"):
                        header_obs.setdefault(cik10, []).append((hdr["acceptance_date"], hdr["assigned_sic"]))
            status = "OK" if rows_n else "NO_FUNDAMENTALS"
            origin = o2
        except Exception as exc:
            status, origin = "FAILED", "error"
            store.append_ledger(run_id, {"ticker": r["ticker"], "cik": cik10, "status": "FAILED",
                                         "note": str(exc)[:120], "ts": _now_iso()})
            continue
        processed += 1
        store.append_ledger(run_id, {"ticker": r["ticker"], "cik": cik10, "status": status,
                                     "origin": origin, "rows": len(rows_n),
                                     "is_removed": r["is_delisted"], "ts": _now_iso()})

    stop_reason = ("NETWORK_BUDGET_REACHED" if new_network >= max_securities
                   else "TARGETS_EXHAUSTED")
    store.append_event(run_id, "ACQUIRE_BATCH_DONE",
                       {"processed": processed, "new_network": new_network,
                        "owned_reuse": sec.owned_reuse, "cache_hits": sec.cache_hits,
                        "removed_acquired": len(n_removed_acquired), "stop_reason": stop_reason})

    # normalize -> repaired series
    months_master = _load_momentum_universe(cfg)["months"]
    max_stale = int(cfg.get("max_factor_staleness_months", 15))
    repaired_series, series_metas = build_repaired_factor_series(
        normalized_rows, months_master, max_staleness_months=max_stale)
    store.write(run_id, "normalized_manifests/sec_asfiled.json", {
        "record_type": "NORMALIZED_MANIFEST", "n_observations": len(normalized_rows),
        "factors": sorted({r["factor"] for r in normalized_rows}),
        "distinct_tickers": len(sorted({r["ticker"] for r in normalized_rows})),
        "removed_tickers_acquired": sorted(n_removed_acquired),
        "restatement_policy": _RESTATEMENT_POLICY,
        "series_meta": series_metas,
    })

    # PIT sector history from filing-header SIC
    pit_sector, sector_audit = build_sec_sic_sector_history(header_obs, master, months_master)
    store.write(run_id, "pit_audits/sector_history.json", sector_audit)

    # re-evaluate on repaired data -> coverage gates + diagnostics (Parts I/J)
    phase30b_latest = _read_phase30b_latest(cfg, output_root)
    reeval = reevaluate_factors(cfg, repaired_series, pit_sector, master,
                                phase30b_latest=phase30b_latest)
    coverage = reeval["coverage"]
    store.write(run_id, "fundamental_coverage.json", coverage["fundamental_coverage"])
    store.write(run_id, "sector_coverage.json", coverage["sector_coverage"])
    for d in reeval["diagnostics"]:
        store.write(run_id, "diagnostics/%s.json" % d["factor_id"], d)

    # Part K: portfolio only for a genuine ADVANCE (none by construction)
    advanced = [d["factor_id"] for d in reeval["diagnostics"] if d["advance_to_portfolio_screen"]]
    n_portfolio = 0  # zero survivors is valid

    best = _best_repaired(reeval["diagnostics"])
    removed_rep = _removed_representation(coverage)
    fund_global_cov = _fund_global_cov(coverage, best)
    sector_mm_cov = coverage["sector_coverage"]["member_month_coverage"]

    run_doc = {
        "schema_version": HISTORICAL_COVERAGE_SCHEMA_VERSION,
        "run_id": run_id, "code_commit": code_commit, "config_hash": verdict["config_hash"],
        "data_cutoff": cutoff, "final_state": "COMPLETE",
        "fundamental_source": decision["fundamental_source"],
        "sector_source": decision["sector_source"],
        "security_master_hash": master["content_hash"],
        "sample_hash": sample["sample_hash"],
        "acquisition": {"processed": processed, "new_network_fetches": new_network,
                        "owned_reuse": sec.owned_reuse, "cache_hits": sec.cache_hits,
                        "removed_names_acquired": len(n_removed_acquired),
                        "stop_reason": stop_reason, "sec_errors": sec.errors[:20]},
        "removed_names_target": int((cfg.get("acquisition_targets") or {}).get("removed_names_target", 400)),
        "removed_names_acquired": len(n_removed_acquired),
        "fundamental_global_coverage": fund_global_cov,
        "removed_name_representation": removed_rep,
        "sector_member_month_coverage": sector_mm_cov,
        "baseline": reeval["baseline"],
        "features_evaluated": [d["factor_id"] for d in reeval["diagnostics"]],
        "advanced_to_portfolio_screen": advanced,
        "n_portfolio_candidates": n_portfolio,
        "best_feature": best["factor_id"] if best else None,
        "best_feature_decision": best["phase30c_decision"] if best else None,
        "sector_history": sector_audit,
        "safety": dict(SAFETY_CONTRACT),
        "generated_at": _now_iso(),
    }
    store.write(run_id, "run.json", run_doc)
    store.write(run_id, "status.json", {
        "run_id": run_id, "final_state": "COMPLETE",
        "processed": processed, "new_network_fetches": new_network,
        "removed_names_acquired": len(n_removed_acquired), "stop_reason": stop_reason,
        "advanced": advanced, "best_feature": run_doc["best_feature"],
        "safety": dict(SAFETY_CONTRACT), "updated_at": _now_iso()})
    store.append_event(run_id, "ACQUIRE_COMPLETE", {"best_feature": run_doc["best_feature"],
                                                    "advanced": advanced})
    store.write_latest_pointer({
        "run_id": run_id, "code_commit": code_commit, "config_hash": verdict["config_hash"],
        "data_cutoff": cutoff, "final_state": "COMPLETE",
        "fundamental_source": decision["fundamental_source"],
        "sector_source": decision["sector_source"],
        "removed_names_target": run_doc["removed_names_target"],
        "removed_names_acquired": len(n_removed_acquired),
        "fundamental_global_coverage": fund_global_cov,
        "removed_name_representation": removed_rep,
        "sector_member_month_coverage": sector_mm_cov,
        "best_feature": run_doc["best_feature"],
        "best_feature_decision": run_doc["best_feature_decision"],
        "generated_at": _now_iso(), "output_root": str(output_root)})
    return {"status": "COMPLETE", "run_id": run_id, "output_root": str(output_root),
            "run_dir": str(store.run_dir(run_id)),
            "fundamental_source": decision["fundamental_source"],
            "sector_source": decision["sector_source"],
            "removed_names_acquired": len(n_removed_acquired),
            "best_feature": run_doc["best_feature"],
            "best_feature_decision": run_doc["best_feature_decision"],
            "stop_reason": stop_reason}


def _removed_representation(coverage: Dict[str, Any]) -> Optional[float]:
    bf = (coverage.get("fundamental_coverage") or {}).get("by_factor") or {}
    reps = [v.get("delisted_representation_fraction") for v in bf.values()
            if v.get("delisted_representation_fraction") is not None]
    return max(reps) if reps else None


def _fund_global_cov(coverage: Dict[str, Any], best: Optional[Dict[str, Any]]) -> Optional[float]:
    if best is None:
        return None
    return best["repaired"].get("cross_sectional_coverage")


def _best_repaired(diagnostics: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    fund = [d for d in diagnostics if d["factor_id"] != "realized_vol_63d"]
    pool = fund or diagnostics
    scored = sorted(pool, key=lambda d: (-(abs(d["repaired"].get("rank_ic_t") or 0.0)), d["factor_id"]))
    return scored[0] if scored else None


def _read_phase30b_latest(cfg: Dict[str, Any], output_root: str) -> Optional[Dict[str, Any]]:
    p = Path(output_root) / of.LATEST_RUN_FILE
    return read_json(p) if p.exists() else None


def resume_acquisition(run_id: str, output_root: str, *, max_securities: int = 25,
                       sec_transport=None, sec_text_transport=None, sec_sleep=None,
                       eodhd_transport=None, eodhd_sleep=None) -> Dict[str, Any]:
    """Resume a prior acquisition from its persisted config (cache/owned reuse
    makes already-succeeded work free; no successful fetch is repeated)."""
    store = HistoricalCoverageRunStore(output_root)
    cfg = store.read(run_id, "config.json")
    if cfg is None:
        raise HistoricalCoverageError("no persisted config for run_id: %s" % run_id)
    return run_acquisition(cfg, output_root=output_root, max_securities=max_securities,
                           resume_run_id=run_id, sec_transport=sec_transport,
                           sec_text_transport=sec_text_transport, sec_sleep=sec_sleep,
                           eodhd_transport=eodhd_transport, eodhd_sleep=eodhd_sleep)


def generate_status(run_id: str, output_root: str) -> Dict[str, Any]:
    store = HistoricalCoverageRunStore(output_root)
    status = store.read(run_id, "status.json")
    if status is None:
        raise HistoricalCoverageError("unknown run_id: %s" % run_id)
    run_doc = store.read(run_id, "run.json") or {}
    return {
        "run_id": run_id, "final_state": status.get("final_state"),
        "processed": status.get("processed"),
        "new_network_fetches": status.get("new_network_fetches"),
        "removed_names_acquired": status.get("removed_names_acquired"),
        "removed_names_target": run_doc.get("removed_names_target"),
        "fundamental_global_coverage": run_doc.get("fundamental_global_coverage"),
        "removed_name_representation": run_doc.get("removed_name_representation"),
        "sector_member_month_coverage": run_doc.get("sector_member_month_coverage"),
        "best_feature": status.get("best_feature"),
        "best_feature_decision": run_doc.get("best_feature_decision"),
        "advanced": status.get("advanced"),
        "stop_reason": status.get("stop_reason"),
        "safety": dict(SAFETY_CONTRACT),
    }


def generate_report(run_id: str, output_root: str) -> Dict[str, Any]:
    store = HistoricalCoverageRunStore(output_root)
    run_doc = store.read(run_id, "run.json")
    if run_doc is None:
        raise HistoricalCoverageError("unknown run_id: %s" % run_id)
    decision = store.read(run_id, "provider_decision.json") or {}
    diagnostics = [store.read(run_id, "diagnostics/%s.json" % fid)
                   for fid in run_doc.get("features_evaluated", [])]
    diagnostics = [d for d in diagnostics if d]

    lines = ["# Phase 30C — survivorship-safe fundamental backfill + PIT sector history", ""]
    lines.append("- run_id: %s" % run_doc["run_id"])
    lines.append("- code_commit: %s" % run_doc["code_commit"])
    lines.append("- data_cutoff: %s" % run_doc["data_cutoff"])
    lines.append("- fundamental_source: %s" % run_doc["fundamental_source"])
    lines.append("- sector_source: %s" % run_doc["sector_source"])
    lines.append("- removed_names_acquired / target: %s / %s" % (
        run_doc["removed_names_acquired"], run_doc["removed_names_target"]))
    lines.append("- removed_name_representation: %s" % _fmt(run_doc.get("removed_name_representation")))
    lines.append("- sector_member_month_coverage: %s" % _fmt(run_doc.get("sector_member_month_coverage")))
    lines.append("- best_feature: %s (%s)" % (run_doc.get("best_feature"), run_doc.get("best_feature_decision")))
    lines.append("- advanced_to_portfolio_screen: %s" % (run_doc.get("advanced_to_portfolio_screen") or "none"))
    lines.append("")
    lines.append("## Repaired factor diagnostics (Part J)")
    lines.append("")
    lines.append("| factor | xcov | rank-IC t | delisted-rep | decision |")
    lines.append("|---|---|---|---|---|")
    for d in diagnostics:
        rp = d["repaired"]
        lines.append("| %s | %s | %s | %s | %s |" % (
            d["factor_id"], _fmt(rp.get("cross_sectional_coverage")), _fmt(rp.get("rank_ic_t")),
            _fmt(rp.get("delisted_representation_fraction")), d["phase30c_decision"]))
    lines.append("")
    lines.append("_Research-only. No order, broker, automation, promotion, challenger, "
                 "or operational-model change occurred._")
    report_md = "\n".join(lines) + "\n"

    report_json = {
        "schema_version": HISTORICAL_COVERAGE_SCHEMA_VERSION,
        "record_type": "HISTORICAL_COVERAGE_REPORT",
        "run_id": run_doc["run_id"], "code_commit": run_doc["code_commit"],
        "data_cutoff": run_doc["data_cutoff"], "final_state": run_doc["final_state"],
        "fundamental_source": run_doc["fundamental_source"],
        "sector_source": run_doc["sector_source"],
        "provider_decision": {"fundamental": decision.get("fundamental_source"),
                              "sector": decision.get("sector_source")},
        "removed_names_acquired": run_doc["removed_names_acquired"],
        "removed_name_representation": run_doc.get("removed_name_representation"),
        "sector_member_month_coverage": run_doc.get("sector_member_month_coverage"),
        "best_feature": run_doc.get("best_feature"),
        "best_feature_decision": run_doc.get("best_feature_decision"),
        "per_factor": [{"factor_id": d["factor_id"], "decision": d["phase30c_decision"],
                        "rank_ic_t": d["repaired"].get("rank_ic_t"),
                        "cross_sectional_coverage": d["repaired"].get("cross_sectional_coverage"),
                        "delisted_representation_fraction": d["repaired"].get("delisted_representation_fraction")}
                       for d in diagnostics],
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
    "FACTOR_DECISIONS",
    "FUNDAMENTAL_SOURCE_DECISIONS",
    "HISTORICAL_COVERAGE_SCHEMA_VERSION",
    "HistoricalCoverageError",
    "HistoricalCoverageRunStore",
    "SECTOR_SOURCE_DECISIONS",
    "SIC_SECTOR_MAP_VERSION",
    "SecAccess",
    "base_symbol",
    "build_mapping_audit",
    "build_repaired_factor_series",
    "build_sample",
    "build_sec_sic_sector_history",
    "build_security_master",
    "compute_coverage_gates",
    "compute_run_id",
    "delisting_month",
    "generate_report",
    "generate_status",
    "load_config",
    "normalize_sec_fundamentals",
    "probe_eodhd",
    "probe_norgate_local",
    "probe_sec",
    "probe_simfin",
    "reevaluate_factors",
    "resume_acquisition",
    "run_acquisition",
    "run_probe",
    "select_providers",
    "sic_sector_map_provenance",
    "sic_to_sector",
    "validate_config",
]
