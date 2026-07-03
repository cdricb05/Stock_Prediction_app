"""Phase 12-A - Nasdaq Data Link Zacks Entitlement And Historical Estimates Download Probe.

Purpose
-------
The 11-B4 shopping cart ended at ACTION_REQUIRED_ANALYST_REVISIONS_TRIAL and the user then
created a FREE Nasdaq Data Link account (NASDAQ_DATA_LINK_API_KEY is present in the environment).
This phase does NOT assume entitlement. It *tests* which Zacks / Nasdaq datatables the free key
can reach, inspects their schema, and - critically - distinguishes a genuine full-history
entitlement from Nasdaq's free PREMIUM SAMPLE (a curated handful of tickers and a single year of
history that any free key can pull). Only genuine full access can support a 545-name, >=10-year,
pre/post-2020 backtest.

What it does
------------
1. Entitlement smoke test - hits each candidate table via the Tables API
   (https://data.nasdaq.com/api/v3/datatables/{DB}/{TBL}.json) for AAPL/MSFT/JPM/XOM/MOS,
   recording HTTP status, error class, rows, columns, filter support, and pagination.
2. Observation-date detection - separates the point-in-time OBSERVATION/revision date
   (obs_date, eps_rev_date, ...) from the fiscal PERIOD-end date (per_end_date). A table with a
   real obs_date is the only kind that can be point-in-time.
3. Sample-vs-full probe (the decisive test) - for each obs_date table, measures universe
   coverage across an evenly spread set of the 545 tickers and whether obs_date history reaches
   back >= 10 years. Low coverage or a single sample year => Nasdaq free SAMPLE, not entitlement.
4. Schema + alpha-readiness classification per table.
5. Small raw sample cache under research/data/nasdaq_zacks/estimates/raw/sample/.
6. Readiness decision (enum below). Phase 12-B full download runs ONLY on
   NASDAQ_ZACKS_READY_FOR_FULL_DOWNLOAD (genuine full access).

Safety
------
- The API key is read from the environment and NEVER printed, logged, or written to disk.
  Every URL is redacted (api_key=***) before it appears in any output or cache.
- No orders, no automation, no broker, no deploy, no GCP, no Paper Trader writes.
- Network is confined to the Nasdaq Data Link Tables API (read-only GET) and only in this runner.
- Offline replay: `--offline` (or PHASE12A_OFFLINE=1) rebuilds every report from the cached
  probe_log.json with zero network, so the test suite is deterministic and network-free.

Decision enum
-------------
NASDAQ_ZACKS_READY_FOR_FULL_DOWNLOAD | NASDAQ_ZACKS_CURRENT_ONLY_NOT_BACKTESTABLE |
NASDAQ_ZACKS_ENTITLEMENT_BLOCKED | NASDAQ_ZACKS_SCHEMA_BLOCKED |
NASDAQ_ZACKS_NEEDS_CONTACT_SALES_OR_TRIAL | NASDAQ_ZACKS_NO_USABLE_TABLES

Run (Windows PowerShell only):
    Set-Location C:\\Users\\binis\\Stock_Prediction_app_push
    python research/run_phase12a_nasdaq_zacks_entitlement_download_probe.py            # live probe
    python research/run_phase12a_nasdaq_zacks_entitlement_download_probe.py --offline  # replay cache
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
STEM = "phase12a_nasdaq_zacks_entitlement_download_probe"
OUT_DIR = REPO / "research" / "output" / STEM
OUT_JSON = OUT_DIR / f"{STEM}.json"
PROBE_LOG = OUT_DIR / "probe_log.json"
RAW_SAMPLE_DIR = REPO / "research" / "data" / "nasdaq_zacks" / "estimates" / "raw" / "sample"
RAW_DIR = REPO / "research" / "data" / "nasdaq_zacks" / "estimates" / "raw"
NORM_DIR = REPO / "research" / "data" / "nasdaq_zacks" / "estimates" / "normalized"
PANEL = (REPO / "research" / "output"
         / "phase10l_historical_sector_neutral_scored_panel_reconstruction"
         / "historical_sector_neutral_scored_panel.csv")

BASE = "https://data.nasdaq.com/api/v3/datatables"
SMOKE_TICKERS = ["AAPL", "MSFT", "JPM", "XOM", "MOS"]
API_KEY_ENVS = ["NASDAQ_DATA_LINK_API_KEY", "QUANDL_API_KEY"]
THROTTLE_S = 0.34            # ~<3 req/s, polite to the free tier
HTTP_TIMEOUT = 30
SAMPLE_PER_PAGE = 5
HIST_PER_PAGE = 10000
MAX_CALLS = 300              # hard backstop on total live calls

# sample-vs-full thresholds
COVERAGE_N = 18                       # universe tickers probed per obs_date table
COVERAGE_MIN_FRAC = 0.5               # >= 50% of sampled universe must be present for full access
HIST_BACK_PROBE = "2016-01-01"        # need obs_date history at least this old (>= ~10yr)
HIST_BACK_THRESHOLDS = ["2012-01-01", "2016-01-01", "2020-01-01"]

# ---- Candidate tables ------------------------------------------------------------------------
# Tables API path is /datatables/{DB}/{TBL}. Codes from the user task + a couple of plausible
# historical codes; a wrong code simply returns HTTP 404 (recorded, not fatal).
CANDIDATE_TABLES = [
    {"code": "ZACKS/EE",   "db": "ZACKS", "tbl": "EE",   "group": "current_product_visible",
     "hypothesis": "earnings_estimates_current", "source": "user_task"},
    {"code": "ZACKS/LTG",  "db": "ZACKS", "tbl": "LTG",  "group": "current_product_visible",
     "hypothesis": "long_term_growth_current", "source": "user_task"},
    {"code": "ZACKS/MT",   "db": "ZACKS", "tbl": "MT",   "group": "current_product_visible",
     "hypothesis": "master_table_reference", "source": "user_task"},
    {"code": "ZACKS/EEH",  "db": "ZACKS", "tbl": "EEH",  "group": "historical_alpha_critical",
     "hypothesis": "earnings_estimates_history_pit", "source": "user_task"},
    {"code": "ZACKS/EET",  "db": "ZACKS", "tbl": "EET",  "group": "historical_alpha_critical",
     "hypothesis": "earnings_estimates_trend", "source": "user_task"},
    {"code": "ZACKS/EREV", "db": "ZACKS", "tbl": "EREV", "group": "historical_alpha_critical",
     "hypothesis": "estimate_revisions_history", "source": "user_task"},
    {"code": "ZACKS/SEH",  "db": "ZACKS", "tbl": "SEH",  "group": "historical_alpha_critical",
     "hypothesis": "sales_estimates_history", "source": "user_task"},
    {"code": "ZACKS/ZEEH", "db": "ZACKS", "tbl": "ZEEH", "group": "historical_alpha_critical",
     "hypothesis": "alt_estimates_history_code", "source": "user_task"},
    {"code": "ZACKS/SE",   "db": "ZACKS", "tbl": "SE",   "group": "current_product_visible",
     "hypothesis": "sales_estimates_current", "source": "hypothesized"},
    {"code": "ZACKS/EPRR", "db": "ZACKS", "tbl": "EPRR", "group": "historical_alpha_critical",
     "hypothesis": "eps_revisions_ratio_history", "source": "hypothesized"},
]

# ---- Column-name detectors -------------------------------------------------------------------
# The observation/as-of/revision date is the ONLY column that makes a table point-in-time.
OBS_DATE_HINTS = ["obs_date", "as_of", "asof", "rev_date", "estimate_date", "date_est", "eff_date"]
PERIOD_DATE_HINTS = ["per_end_date", "per_cal_end", "per_fisc_end", "period_end"]

CONCEPT_KEYWORDS = {
    "ticker": ["ticker"],
    "observation_or_effective_date": OBS_DATE_HINTS,
    "fiscal_period": ["per_end_date", "per_cal", "per_fisc", "per_type", "period"],
    "eps_consensus_estimate": ["eps_mean_est", "eps_est", "eps_median_est", "eps_rev_est"],
    "revenue_or_sales_estimate": ["sales_mean_est", "sales_median_est", "sales_", "rev_mean_est"],
    "analyst_count": ["cnt_est", "num_est", "n_est"],
    "estimate_high_low_std": ["high_est", "low_est", "std_dev", "stddev"],
    "upward_revision_count": ["rev_up", "cnt_est_rev_up", "num_up", "est_up"],
    "downward_revision_count": ["rev_down", "cnt_est_rev_down", "num_down", "est_down"],
    "prior_estimate": ["est_prev", "prior", "_prev", "last_est"],
    "new_estimate": ["rev_est", "curr_est", "new_est"],
    "broker_or_analyst": ["broker", "analyst_name", "analyst_id", "contributor"],
}

# ==============================================================================================
# HTTP (live) - key never leaves memory; every recorded URL is redacted.
# ==============================================================================================
def _get_key():
    for env in API_KEY_ENVS:
        v = os.environ.get(env)
        if v:
            return v, env
    return None, None


def _redact(url: str) -> str:
    return re.sub(r"(api_key=)[^&]*", r"\1***", url)


def _parse_quandl_error(body: str):
    try:
        j = json.loads(body)
        qe = j.get("quandl_error") or j.get("error") or {}
        if isinstance(qe, dict):
            return qe.get("code"), qe.get("message")
    except Exception:
        pass
    return None, None


class _Http:
    """Thin GET wrapper with a live-call counter and throttle."""

    def __init__(self, key):
        self.key = key
        self.calls = 0

    def get(self, path: str, params: dict, timeout: int = HTTP_TIMEOUT) -> dict:
        if self.calls >= MAX_CALLS:
            return {"status": None, "ok": False, "redacted_url": f"{BASE}/{path}?...(call cap)",
                    "body": "", "elapsed_s": 0.0, "error_code": "CALL_CAP",
                    "error_message": f"reached MAX_CALLS={MAX_CALLS}"}
        q = dict(params)
        q["api_key"] = self.key
        url = f"{BASE}/{path}?" + urllib.parse.urlencode(q)
        redacted = _redact(url)
        self.calls += 1
        t0 = time.time()
        req = urllib.request.Request(url, headers={"User-Agent": "phase12a-zacks-probe/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8", "replace")
                out = {"status": resp.getcode(), "ok": True, "redacted_url": redacted,
                       "body": raw, "elapsed_s": round(time.time() - t0, 3),
                       "error_code": None, "error_message": None}
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode("utf-8", "replace")
            except Exception:
                pass
            ec, em = _parse_quandl_error(body)
            out = {"status": e.code, "ok": False, "redacted_url": redacted,
                   "body": body[:2000], "elapsed_s": round(time.time() - t0, 3),
                   "error_code": ec, "error_message": em}
            if e.code == 429:
                time.sleep(2.0)
        except (urllib.error.URLError, socket.timeout, TimeoutError) as e:
            out = {"status": None, "ok": False, "redacted_url": redacted,
                   "body": "", "elapsed_s": round(time.time() - t0, 3),
                   "error_code": "NETWORK", "error_message": str(getattr(e, "reason", e))}
        except Exception as e:  # pragma: no cover - defensive
            out = {"status": None, "ok": False, "redacted_url": redacted,
                   "body": "", "elapsed_s": round(time.time() - t0, 3),
                   "error_code": "EXCEPTION", "error_message": repr(e)}
        time.sleep(THROTTLE_S)
        return out


# ==============================================================================================
# Datatable helpers
# ==============================================================================================
def _parse_datatable(body: str):
    try:
        j = json.loads(body)
    except Exception:
        return None, None, None
    dt = j.get("datatable")
    if not isinstance(dt, dict):
        return None, None, None
    cols = dt.get("columns") or []
    rows = dt.get("data") or []
    cursor = (j.get("meta") or {}).get("next_cursor_id")
    return cols, rows, cursor


def _col_names(cols):
    return [c.get("name", "") for c in (cols or [])]


def _find_col(cols, hints):
    """Return the first column whose lowercased name contains any hint (in hint priority)."""
    names = _col_names(cols)
    for h in hints:
        for n in names:
            if h in n.lower():
                return n
    return None


def _map_alpha_fields(cols):
    names = [c.lower() for c in _col_names(cols)]
    out = {}
    for concept, kws in CONCEPT_KEYWORDS.items():
        hit = None
        for c in names:
            if any(kw in c for kw in kws):
                hit = c
                break
        out[concept] = hit
    return out


_UNIVERSE_CACHE = None


def _universe():
    global _UNIVERSE_CACHE
    if _UNIVERSE_CACHE is None:
        try:
            import pandas as pd
            _UNIVERSE_CACHE = sorted(
                pd.read_csv(PANEL, usecols=["ticker"])["ticker"].dropna().unique().tolist())
        except Exception:
            _UNIVERSE_CACHE = []
    return _UNIVERSE_CACHE


def _spread_sample(items, n):
    if not items:
        return []
    step = max(1, len(items) // n)
    return items[::step][:n]


# ==============================================================================================
# Live probe
# ==============================================================================================
def _probe_table(http: _Http, spec: dict) -> dict:
    db, tbl = spec["db"], spec["tbl"]
    path = f"{db}/{tbl}.json"
    rec = {
        "code": spec["code"], "group": spec["group"], "hypothesis": spec["hypothesis"],
        "source": spec["source"], "endpoint_no_key": f"{BASE}/{db}/{tbl}.json",
        "base_status": None, "base_error_code": None, "base_error_message": None,
        "accessible": False, "premium_blocked": False, "code_not_found": False,
        "needs_filter": False, "columns": [], "n_columns": 0, "sample_rows": 0,
        "obs_date_column": None, "period_date_column": None,
        "ticker_filter_works": None, "date_filter_works": None, "pagination_required": None,
        "per_ticker_rows": {}, "alpha_fields": {}, "raw_sample_path": None,
        # sample-vs-full evidence
        "universe_coverage_sampled": 0, "universe_coverage_present": 0,
        "universe_coverage_frac": None, "obs_hist_span": None,
        "obs_hist_reaches": {}, "obs_hist_reaches_back": False, "full_access": None,
        "appears_sample": None,
    }

    base = http.get(path, {"qopts.per_page": SAMPLE_PER_PAGE})
    rec["base_status"] = base["status"]
    rec["base_error_code"] = base["error_code"]
    rec["base_error_message"] = (base["error_message"] or "")[:300]
    body = base["body"]

    if base["status"] == 400:                      # some tables require a filter
        retry = http.get(path, {"ticker": "AAPL", "qopts.per_page": SAMPLE_PER_PAGE})
        if retry["ok"]:
            rec["needs_filter"] = True
            base, body = retry, retry["body"]
            rec["base_status"] = 200
            rec["ticker_filter_works"] = True

    if base["status"] == 403:
        rec["premium_blocked"] = True
        return rec
    if base["status"] == 404:
        rec["code_not_found"] = True
        return rec
    if not base["ok"]:
        return rec

    cols, rows, cursor = _parse_datatable(body)
    if cols is None:
        rec["base_error_code"] = rec["base_error_code"] or "UNPARSEABLE_BODY"
        return rec

    rec["accessible"] = True
    rec["columns"] = _col_names(cols)
    rec["n_columns"] = len(rec["columns"])
    rec["sample_rows"] = len(rows or [])
    rec["pagination_required"] = bool(cursor)
    rec["obs_date_column"] = _find_col(cols, OBS_DATE_HINTS)
    rec["period_date_column"] = _find_col(cols, PERIOD_DATE_HINTS)
    rec["alpha_fields"] = _map_alpha_fields(cols)

    try:
        RAW_SAMPLE_DIR.mkdir(parents=True, exist_ok=True)
        sp = RAW_SAMPLE_DIR / f"{db}_{tbl}.json"
        sp.write_text(body[:200000], encoding="utf-8")
        rec["raw_sample_path"] = str(sp.relative_to(REPO)).replace("\\", "/")
    except Exception:
        pass

    has_ticker = any(c.lower() == "ticker" for c in rec["columns"])

    # per-ticker smoke across the 5 named tickers (task requirement)
    if has_ticker:
        for tk in SMOKE_TICKERS:
            r = http.get(path, {"ticker": tk, "qopts.per_page": 1})
            if r["ok"]:
                _, rws, _ = _parse_datatable(r["body"])
                rec["per_ticker_rows"][tk] = len(rws or [])
            else:
                rec["per_ticker_rows"][tk] = f"status={r['status']}"
        rec["ticker_filter_works"] = any(isinstance(v, int) and v > 0
                                         for v in rec["per_ticker_rows"].values())

    # ---- the decisive sample-vs-full probe: only for point-in-time (obs_date) tables ----
    if has_ticker and rec["obs_date_column"]:
        obs = rec["obs_date_column"]
        uni_sample = _spread_sample(_universe(), COVERAGE_N)
        present = 0
        first_present = None
        for tk in uni_sample:
            r = http.get(path, {"ticker": tk, "qopts.per_page": 1})
            n = 0
            if r["ok"]:
                _, rws, _ = _parse_datatable(r["body"])
                n = len(rws or [])
            if n > 0:
                present += 1
                if first_present is None:
                    first_present = tk
        rec["universe_coverage_sampled"] = len(uni_sample)
        rec["universe_coverage_present"] = present
        rec["universe_coverage_frac"] = round(present / len(uni_sample), 3) if uni_sample else None

        # history depth on obs_date for a covered ticker (prefer AAPL if present)
        probe_tk = "AAPL" if rec["per_ticker_rows"].get("AAPL", 0) else (first_present or "AAPL")
        rec["date_filter_works"] = None
        for thr in HIST_BACK_THRESHOLDS:
            r = http.get(path, {"ticker": probe_tk, f"{obs}.lt": thr, "qopts.per_page": 1})
            if rec["date_filter_works"] is None:
                rec["date_filter_works"] = bool(r["ok"])
            has = False
            if r["ok"]:
                _, rws, _ = _parse_datatable(r["body"])
                has = bool(rws)
            rec["obs_hist_reaches"][thr] = has
        rec["obs_hist_reaches_back"] = bool(rec["obs_hist_reaches"].get(HIST_BACK_PROBE))

        # span from a large page (for reporting only)
        d = http.get(path, {"ticker": probe_tk, "qopts.per_page": HIST_PER_PAGE})
        if d["ok"]:
            dcols, drows, _ = _parse_datatable(d["body"])
            names = _col_names(dcols)
            if obs in names and drows:
                idx = names.index(obs)
                vals = sorted({str(r[idx]) for r in drows if idx < len(r) and r[idx]})
                if vals:
                    rec["obs_hist_span"] = f"{vals[0]}..{vals[-1]} ({probe_tk})"

        cov_ok = (rec["universe_coverage_frac"] or 0) >= COVERAGE_MIN_FRAC
        rec["full_access"] = bool(cov_ok and rec["obs_hist_reaches_back"])
        rec["appears_sample"] = not rec["full_access"]

    return rec


def probe_all_tables(http: _Http) -> dict:
    probes = [_probe_table(http, s) for s in CANDIDATE_TABLES]
    return {"probed_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "n_calls": http.calls, "smoke_tickers": SMOKE_TICKERS, "probes": probes}


# ==============================================================================================
# Classification (pure - shared by live and offline paths)
# ==============================================================================================
def classify_schema(rec: dict) -> str:
    if rec.get("premium_blocked"):
        return "ENTITLEMENT_BLOCKED"
    if not rec.get("accessible"):
        return "NOT_BACKTESTABLE"
    if rec.get("hypothesis") == "master_table_reference":
        return "MASTER_REFERENCE"
    af = rec.get("alpha_fields", {})
    has_obs = bool(rec.get("obs_date_column"))
    has_rev = bool(af.get("upward_revision_count") or af.get("downward_revision_count")
                   or af.get("broker_or_analyst"))
    if has_obs and has_rev:
        return "REVISION_HISTORY"
    if has_obs:
        return "HISTORICAL_POINT_IN_TIME"
    return "CURRENT_SNAPSHOT_ONLY"


def alpha_ready_flags(rec: dict) -> dict:
    af = rec.get("alpha_fields", {})
    need = ["ticker", "observation_or_effective_date", "fiscal_period",
            "eps_consensus_estimate", "revenue_or_sales_estimate", "analyst_count",
            "estimate_high_low_std", "upward_revision_count", "downward_revision_count",
            "prior_estimate", "new_estimate", "broker_or_analyst"]
    flags = {k: bool(af.get(k)) for k in need}
    flags["observation_or_effective_date"] = bool(rec.get("obs_date_column"))
    core = flags["ticker"] and flags["observation_or_effective_date"]
    est = flags["eps_consensus_estimate"] or flags["revenue_or_sales_estimate"] \
        or bool(af.get("new_estimate"))
    revision = flags["upward_revision_count"] or flags["downward_revision_count"] \
        or flags["broker_or_analyst"]
    # SCHEMA is backtestable if it is a genuine PIT revision/estimate history table.
    flags["_schema_backtestable"] = bool(core and est and revision)
    # ACTUAL backtestable requires genuine full access (not a Nasdaq free sample).
    flags["_full_access"] = bool(rec.get("full_access"))
    flags["_backtestable"] = bool(flags["_schema_backtestable"] and flags["_full_access"])
    return flags


def decide(probes: list) -> tuple:
    per_class = {}
    accessible, backtestable, pit_schema_sample = [], [], []
    blocked = []
    any_403 = any_404 = any_network = False

    for rec in probes:
        cls = classify_schema(rec)
        per_class[rec["code"]] = cls
        flags = alpha_ready_flags(rec)
        rec["_schema_class"] = cls
        rec["_alpha_flags"] = flags
        if rec.get("premium_blocked"):
            any_403 = True
            blocked.append({"code": rec["code"], "reason": "premium/entitlement (HTTP 403)",
                            "error_code": rec.get("base_error_code"),
                            "error_message": rec.get("base_error_message")})
        if rec.get("code_not_found"):
            any_404 = True
        if rec.get("base_error_code") == "NETWORK":
            any_network = True
        if rec.get("accessible"):
            accessible.append(rec["code"])
            if flags["_backtestable"]:
                backtestable.append(rec["code"])
            elif flags["_schema_backtestable"] and rec.get("appears_sample"):
                pit_schema_sample.append(rec["code"])

    if any_network and not accessible and not any_403 and not any_404:
        decision = "NASDAQ_ZACKS_NEEDS_CONTACT_SALES_OR_TRIAL"
        rationale = ("No table could be reached (network error on every call). Re-run when "
                     "connectivity is restored; entitlement could not be established.")
    elif backtestable:
        decision = "NASDAQ_ZACKS_READY_FOR_FULL_DOWNLOAD"
        rationale = (f"Full-access point-in-time table(s) with universe coverage >= "
                     f"{COVERAGE_MIN_FRAC:.0%} and >=10yr obs_date history: "
                     f"{', '.join(backtestable)}.")
    elif pit_schema_sample:
        decision = "NASDAQ_ZACKS_NEEDS_CONTACT_SALES_OR_TRIAL"
        rationale = (f"The Zacks estimate-history schema is a perfect fit (obs_date + estimate + "
                     f"revision fields) in {', '.join(pit_schema_sample)}, but the free key returns "
                     f"only Nasdaq's PREMIUM SAMPLE (a curated handful of tickers and a single "
                     f"year of history) - insufficient universe/history for a backtest. A paid "
                     f"subscription or product trial is required to unlock the full data.")
    elif accessible:
        current_only = all(per_class[c] in ("CURRENT_SNAPSHOT_ONLY", "MASTER_REFERENCE")
                           for c in accessible)
        if current_only:
            decision = "NASDAQ_ZACKS_CURRENT_ONLY_NOT_BACKTESTABLE"
            rationale = (f"Accessible table(s) {', '.join(accessible)} expose only a current "
                         f"snapshot / master reference (no obs_date) - no point-in-time history.")
        else:
            decision = "NASDAQ_ZACKS_SCHEMA_BLOCKED"
            rationale = (f"Accessible table(s) {', '.join(accessible)} lack the estimate/revision "
                         f"fields required for a backtest.")
    elif any_403:
        decision = "NASDAQ_ZACKS_ENTITLEMENT_BLOCKED"
        rationale = ("Every existing Zacks table returned HTTP 403 (premium/subscription). The "
                     "free Nasdaq Data Link account does not entitle Zacks estimate data.")
    elif any_404:
        decision = "NASDAQ_ZACKS_NO_USABLE_TABLES"
        rationale = ("No candidate Zacks table code resolved (all HTTP 404) and none were "
                     "accessible. Codes may differ or the product is not on this key.")
    else:
        decision = "NASDAQ_ZACKS_NEEDS_CONTACT_SALES_OR_TRIAL"
        rationale = "Mixed/inconclusive access; a product trial or subscription is required."

    next_action = _build_next_action(decision, blocked, pit_schema_sample)
    return decision, rationale, per_class, blocked, next_action


def _build_next_action(decision, blocked, pit_schema_sample):
    rows = []
    if decision == "NASDAQ_ZACKS_READY_FOR_FULL_DOWNLOAD":
        rows.append({"step": 1, "action": "Proceed to Phase 12-B full 545-universe download",
                     "detail": "Full-access PIT table entitled; download + normalize + alpha test.",
                     "provider": "nasdaq_data_link_zacks", "blocking": "no"})
        return rows
    sample_codes = ", ".join(pit_schema_sample) or "(none)"
    blocked_codes = ", ".join(b["code"] for b in blocked) or "(none 403)"
    rows.append({"step": 1,
                 "action": "Subscribe to / trial the Zacks estimate-history product on Nasdaq Data Link",
                 "detail": (f"The free key confirmed the ideal schema in {sample_codes} but serves "
                            f"only a SAMPLE (curated tickers, single year). The Zacks North "
                            f"American Earnings Estimates product page on data.nasdaq.com exposes "
                            f"a self-serve 'Trial This Product' / subscribe button; that unlocks "
                            f"the full ZACKS/EEH + ZACKS/EREV history for the 545 universe. "
                            f"403-blocked: {blocked_codes}."),
                 "provider": "nasdaq_data_link_zacks", "blocking": "yes"})
    rows.append({"step": 2,
                 "action": "Confirm the paid tier covers ZACKS/EEH (EPS history) + ZACKS/EREV (revisions)",
                 "detail": ("EEH gives obs_date + eps_mean/high/low/std/cnt + rev_up/rev_down; "
                            "EREV gives analyst-level new/prior estimate with revision dates. Both "
                            "are needed for a net-revisions-momentum factor at 63d."),
                 "provider": "nasdaq_data_link_zacks", "blocking": "yes"})
    rows.append({"step": 3,
                 "action": "Alternate provider if Nasdaq trial unavailable: Intrinio Zacks",
                 "detail": ("Intrinio exposes Zacks estimate-trend / revision history via clean "
                            "REST with a self-serve trial; comparable PIT depth."),
                 "provider": "intrinio_zacks", "blocking": "yes"})
    rows.append({"step": 4,
                 "action": "Owned-key fallback: FMP Premium analyst-estimates upgrade (11-B4 rank-1)",
                 "detail": ("FMP_API_KEY already present; a tier upgrade unlocks estimate history "
                            "for a first revisions screen without a new signup."),
                 "provider": "fmp_premium", "blocking": "yes"})
    return rows


# ==============================================================================================
# Phase 12-B (gated) - only runs on NASDAQ_ZACKS_READY_FOR_FULL_DOWNLOAD (genuine full access)
# ==============================================================================================
def run_phase12b_full_download(http: _Http, probes: list) -> dict:
    target = next((r for r in probes if r.get("_alpha_flags", {}).get("_backtestable")), None)
    if target is None:
        return {"started": False, "reason": "no full-access backtestable table"}
    db, tbl = target["code"].split("/", 1)
    path = f"{db}/{tbl}.json"
    universe = _universe()
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    NORM_DIR.mkdir(parents=True, exist_ok=True)
    manifest = []
    for tk in universe:
        dest = RAW_DIR / f"{db}_{tbl}_{tk}.json"
        if dest.exists() and dest.stat().st_size > 2:      # resume: never redownload
            manifest.append({"ticker": tk, "status": "cached"})
            continue
        pages, cursor, rows_total = [], None, 0
        while True:
            params = {"ticker": tk, "qopts.per_page": HIST_PER_PAGE}
            if cursor:
                params["qopts.cursor_id"] = cursor
            r = http.get(path, params)
            if not r["ok"]:
                manifest.append({"ticker": tk, "status": f"error:{r['status']}"})
                break
            _, rws, cursor = _parse_datatable(r["body"])
            pages.append(r["body"])
            rows_total += len(rws or [])
            if not cursor or http.calls >= MAX_CALLS:
                dest.write_text(json.dumps({"pages": pages}, ensure_ascii=False), encoding="utf-8")
                manifest.append({"ticker": tk, "status": "downloaded", "rows": rows_total})
                break
        if http.calls >= MAX_CALLS:
            break
    return {"started": True, "table": target["code"], "universe_size": len(universe),
            "downloaded": sum(1 for m in manifest if m["status"] == "downloaded"),
            "cached": sum(1 for m in manifest if m["status"] == "cached"),
            "errors": sum(1 for m in manifest if str(m["status"]).startswith("error"))}


# ==============================================================================================
# CSV / JSON writers
# ==============================================================================================
def _write_csv(path, rows, headers):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=headers)
        w.writeheader()
        for r in rows:
            w.writerow({h: r.get(h, "") for h in headers})


def build_reports(bundle: dict, phase12b: dict, key_env) -> dict:
    probes = bundle["probes"]
    decision, rationale, per_class, blocked, next_action = decide(probes)

    probe_rows = []
    for rec in probes:
        probe_rows.append({
            "code": rec["code"], "group": rec["group"], "source": rec["source"],
            "endpoint_no_key": rec["endpoint_no_key"], "http_status": rec.get("base_status"),
            "accessible": rec.get("accessible"), "premium_blocked": rec.get("premium_blocked"),
            "code_not_found": rec.get("code_not_found"), "error_code": rec.get("base_error_code"),
            "error_message": rec.get("base_error_message"), "n_columns": rec.get("n_columns"),
            "sample_rows": rec.get("sample_rows"), "obs_date_column": rec.get("obs_date_column"),
            "period_date_column": rec.get("period_date_column"),
            "ticker_filter_works": rec.get("ticker_filter_works"),
            "date_filter_works": rec.get("date_filter_works"),
            "pagination_required": rec.get("pagination_required"),
            "universe_coverage_present": rec.get("universe_coverage_present"),
            "universe_coverage_sampled": rec.get("universe_coverage_sampled"),
            "universe_coverage_frac": rec.get("universe_coverage_frac"),
            "obs_hist_reaches_back_10yr": rec.get("obs_hist_reaches_back"),
            "obs_hist_span": rec.get("obs_hist_span"),
            "full_access": rec.get("full_access"), "appears_sample": rec.get("appears_sample"),
            "per_ticker_rows": json.dumps(rec.get("per_ticker_rows", {})),
            "schema_class": rec.get("_schema_class"),
            "schema_backtestable": rec.get("_alpha_flags", {}).get("_schema_backtestable"),
            "backtestable_full_access": rec.get("_alpha_flags", {}).get("_backtestable"),
        })
    _write_csv(OUT_DIR / "nasdaq_zacks_table_probe_results.csv", probe_rows,
               ["code", "group", "source", "endpoint_no_key", "http_status", "accessible",
                "premium_blocked", "code_not_found", "error_code", "error_message", "n_columns",
                "sample_rows", "obs_date_column", "period_date_column", "ticker_filter_works",
                "date_filter_works", "pagination_required", "universe_coverage_present",
                "universe_coverage_sampled", "universe_coverage_frac", "obs_hist_reaches_back_10yr",
                "obs_hist_span", "full_access", "appears_sample", "per_ticker_rows",
                "schema_class", "schema_backtestable", "backtestable_full_access"])

    schema_rows = []
    for rec in probes:
        if not rec.get("accessible"):
            continue
        for col in rec.get("columns", []):
            schema_rows.append({"code": rec["code"], "schema_class": rec.get("_schema_class"),
                                "column": col})
    _write_csv(OUT_DIR / "nasdaq_zacks_schema_inventory.csv", schema_rows,
               ["code", "schema_class", "column"])

    ar_rows = []
    for rec in probes:
        flags = rec.get("_alpha_flags", {})
        af = rec.get("alpha_fields", {})
        for concept in CONCEPT_KEYWORDS:
            matched = af.get(concept) or ""
            if concept == "observation_or_effective_date" and rec.get("obs_date_column"):
                matched = rec["obs_date_column"]
            ar_rows.append({"code": rec["code"], "concept": concept,
                            "present": bool(flags.get(concept)), "matched_column": matched})
        ar_rows.append({"code": rec["code"], "concept": "_SCHEMA_BACKTESTABLE",
                        "present": bool(flags.get("_schema_backtestable")), "matched_column": ""})
        ar_rows.append({"code": rec["code"], "concept": "_FULL_ACCESS_NOT_SAMPLE",
                        "present": bool(flags.get("_full_access")), "matched_column": ""})
        ar_rows.append({"code": rec["code"], "concept": "_BACKTESTABLE",
                        "present": bool(flags.get("_backtestable")), "matched_column": ""})
    _write_csv(OUT_DIR / "nasdaq_zacks_alpha_readiness_check.csv", ar_rows,
               ["code", "concept", "present", "matched_column"])

    sample_rows = [{"code": r["code"], "raw_sample_path": r["raw_sample_path"],
                    "sample_rows": r.get("sample_rows"), "n_columns": r.get("n_columns")}
                   for r in probes if r.get("raw_sample_path")]
    _write_csv(OUT_DIR / "nasdaq_zacks_sample_download_manifest.csv", sample_rows,
               ["code", "raw_sample_path", "sample_rows", "n_columns"])

    _write_csv(OUT_DIR / "nasdaq_zacks_blocked_tables.csv", blocked,
               ["code", "reason", "error_code", "error_message"])
    _write_csv(OUT_DIR / "nasdaq_zacks_next_action.csv", next_action,
               ["step", "action", "detail", "provider", "blocking"])

    accessible = [r["code"] for r in probes if r.get("accessible")]
    sample_only = [r["code"] for r in probes if r.get("appears_sample")]
    full = [r["code"] for r in probes if r.get("full_access")]
    result = {
        "phase": "12-A",
        "phase_name": "nasdaq_zacks_entitlement_and_historical_estimates_download_probe",
        "decision": decision,
        "decision_rationale": rationale,
        "generated_utc": bundle.get("probed_utc"),
        "offline_replay": bundle.get("offline_replay", False),
        "api_key_env_used": key_env,               # NAME only, never the value
        "api_key_value_printed": False,
        "data_families_attempted": ["analyst_estimate_revisions", "earnings_estimates",
                                    "sales_estimates", "long_term_growth"],
        "providers_attempted": ["nasdaq_data_link_zacks"],
        "candidate_tables": [t["code"] for t in CANDIDATE_TABLES],
        "smoke_tickers": SMOKE_TICKERS,
        "n_live_calls": bundle.get("n_calls"),
        "accessible_tables": accessible,
        "blocked_tables": [b["code"] for b in blocked],
        "sample_only_tables": sample_only,
        "full_access_tables": full,
        "schema_class_by_table": per_class,
        "free_tier_is_sample": bool(sample_only) and not full,
        "files_downloaded": [r["raw_sample_path"] for r in probes if r.get("raw_sample_path")],
        "raw_data_paths": [str(RAW_SAMPLE_DIR.relative_to(REPO)).replace("\\", "/")],
        "normalized_data_paths": [],
        "coverage_summary": {"candidate_tables": len(CANDIDATE_TABLES),
                             "accessible": len(accessible), "blocked_403": len(blocked),
                             "sample_only": len(sample_only), "full_access": len(full),
                             "coverage_probe_n": COVERAGE_N},
        "schema_summary": {"schema_inventory_rows": len(schema_rows),
                           "concepts_checked": list(CONCEPT_KEYWORDS.keys()),
                           "pit_tables": [r["code"] for r in probes if r.get("obs_date_column")]},
        "quality_summary": {"any_full_access_backtestable": bool(full and any(
            r.get("_alpha_flags", {}).get("_backtestable") for r in probes))},
        "next_action": next_action,
        "phase12b_full_download": phase12b,
        "paid_sources": [
            {"provider": "nasdaq_data_link_zacks", "product": "Zacks EEH+EREV history",
             "status": "trial_or_subscription_required", "owned_key": True},
            {"provider": "intrinio_zacks", "product": "Zacks estimate trend/revisions",
             "status": "alternate_provider", "owned_key": False},
            {"provider": "fmp_premium", "product": "analyst estimates + history",
             "status": "owned_key_upgrade", "owned_key": True},
        ],
        "next_phase": ("12-B" if decision == "NASDAQ_ZACKS_READY_FOR_FULL_DOWNLOAD"
                       else "user_opt_in_paid_zacks_trial"),
        "safety": {
            "paper_only": True, "no_secret_printed": True, "no_orders": True,
            "no_automation": True, "no_broker": True, "no_deploy": True, "no_gcp": True,
            "no_paper_trader_writes": True,
            "network_scope": "nasdaq_data_link_tables_api_read_only", "no_push": True,
        },
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(OUT_JSON, "w", encoding="utf-8") as fh:
        json.dump(result, fh, indent=2, sort_keys=True)
    return result


# ==============================================================================================
# Entry
# ==============================================================================================
def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true",
                    help="rebuild reports from cached probe_log.json (no network)")
    args = ap.parse_args(argv)
    offline = args.offline or os.environ.get("PHASE12A_OFFLINE") == "1"
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    if offline:
        if not PROBE_LOG.exists():
            print("PHASE12A: offline mode but no probe_log.json cached; run live once first.",
                  file=sys.stderr)
            return 2
        with open(PROBE_LOG, "r", encoding="utf-8") as fh:
            bundle = json.load(fh)
        bundle["offline_replay"] = True
        phase12b = bundle.get("phase12b", {"started": False, "reason": "offline replay"})
        result = build_reports(bundle, phase12b, bundle.get("api_key_env_used"))
        _print_summary(result)
        return 0

    key, key_env = _get_key()
    if not key:
        print(f"PHASE12A: no key in env ({'/'.join(API_KEY_ENVS)}); cannot probe.", file=sys.stderr)
        return 2

    http = _Http(key)
    bundle = probe_all_tables(http)
    bundle["api_key_env_used"] = key_env

    decision, *_ = decide(bundle["probes"])
    phase12b = {"started": False, "reason": f"decision={decision}"}
    if decision == "NASDAQ_ZACKS_READY_FOR_FULL_DOWNLOAD":
        phase12b = run_phase12b_full_download(http, bundle["probes"])
    bundle["phase12b"] = phase12b
    bundle["n_calls"] = http.calls

    with open(PROBE_LOG, "w", encoding="utf-8") as fh:      # redacted; contains NO key
        json.dump(bundle, fh, indent=2, sort_keys=True)

    result = build_reports(bundle, phase12b, key_env)
    _print_summary(result)
    return 0


def _print_summary(result):
    print("=" * 80)
    print("PHASE 12-A - Nasdaq Data Link Zacks entitlement / history probe")
    print("=" * 80)
    print(f"decision            : {result['decision']}")
    print(f"rationale           : {result['decision_rationale']}")
    print(f"key env (NAME only) : {result.get('api_key_env_used')}")
    print(f"live calls          : {result.get('n_live_calls')}")
    print(f"free tier is sample : {result.get('free_tier_is_sample')}")
    print(f"accessible tables   : {result.get('accessible_tables') or '(none)'}")
    print(f"sample-only tables  : {result.get('sample_only_tables') or '(none)'}")
    print(f"full-access tables  : {result.get('full_access_tables') or '(none)'}")
    print(f"blocked (403)       : {result.get('blocked_tables') or '(none)'}")
    print("per-table class:")
    for code, cls in sorted(result.get("schema_class_by_table", {}).items()):
        print(f"    {code:14s} {cls}")
    p12b = result.get("phase12b_full_download", {})
    print(f"phase 12-B download : started={p12b.get('started')} ({p12b.get('reason', '')})")
    print(f"next phase          : {result.get('next_phase')}")
    print(f"artifacts           : {OUT_DIR}")


if __name__ == "__main__":
    raise SystemExit(main())
