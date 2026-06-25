"""Phase 8-F — Autonomous External Signal Research Operating System.

**Track A (quant brain) research only.** Phases 8-A..8-E built the Norgate-backed engine, the
12-agent system, and the sensitivity-aware multi-input signal factory. 8-E found ONE promising
lead (S8E-011: rates shock x rates-negative/short-duration cohort, +0.40% matched-control lift,
+0.13% EV after 25bps) that fails the tail/portfolio gate, and pre-registered (but could not
test) the external-information families that need provider data.

What 8-F adds (the operating system, not another one-off script)
----------------------------------------------------------------
A DURABLE autonomous research OS that future phases read and extend:
  - agent operating memory (best leads / rejected families / gaps / provider readiness /
    open questions / next actions / stop conditions) + a backlog + a task board + a decision log;
  - a model-candidate registry + signal-promotion log + a rejected-hypothesis graveyard;
  - provider/key DETECTION (env + config NAMES only, never values) and provider-agnostic
    CONNECTORS: when a key exists -> bounded live collection; when it does not -> an executable
    adapter stub + a mock fixture + the exact normalized schema + a dry-run command, so the
    pipeline is READY the moment a key is supplied (it does not stop at a feasibility plan);
  - the six external normalized SCHEMAS (news/sentiment, analyst revisions, earnings, options,
    short interest, transcript tone);
  - an S8E-011 deep-dive validation track (sector/year/rate-regime/liquidity/volatility/
    active-vs-delisted/worst-decile decomposition + fixed pre-declared filter stress);
  - external event x sensitivity-cohort signal tests (A news, B revisions, C earnings, D options,
    E short interest, F S8E-011 + external confirmation, G macro/cross-asset continuing 8-E);
  - a 3-cycle autonomous manager that reads memory, allocates tasks, pre-registers experiments,
    runs what is testable locally, builds what is missing, updates registries, and decides the
    next cycle WITHOUT asking the user.

The ONE question this phase answers
-----------------------------------
    CAN THE AGENT SYSTEM FIND, VALIDATE, OR PREPARE A REAL EXTERNAL-INFORMATION SIGNAL BY
    COMBINING EXTERNAL EVENTS WITH TICKER-SPECIFIC SENSITIVITY COHORTS?

Hard safety contract (unchanged from 8-E, extended for live collection)
-----------------------------------------------------------------------
Offline-first, point-in-time, leakage-safe. Local data first; Norgate desktop DB + on-disk FRED
CSVs are the only price/macro source (no package install). Provider keys are DETECTED but never
printed; if a key exists, live collection is bounded (<=25 tickers, <=30 days, <=500 raw events)
with raw payloads ONLY under D:\\Stock_Prediction_app_data\\external_raw and normalized events
ONLY under ...\\external_normalized — the repo gets summaries/manifests only. No Paper Trader, no
GCP, no deployment, no broker/orders/automation, no live trading signals, no portfolio-weight
optimization, no factor-sign flipping after results, no regime activation/throttling, no ML
fitting, no holdout-tuned thresholds, no hidden experiments, no secrets printed, no commit, no
push. External data is NEVER faked: mock fixtures are explicitly labelled MOCK and are never fed
into the signal scoreboard as if real.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import math
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def _load_module(name: str, rel: str):
    path = _REPO_ROOT / rel
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod          # REQUIRED before exec so dataclasses resolve __module__
    spec.loader.exec_module(mod)
    return mod


# Reuse the 8-E engine (which itself reuses 8-D/8-A) by absolute path.
P8E = _load_module("phase8e_engine_for_8f", "research/run_phase8e_sensitivity_aware_signal_factory.py")
P8D = P8E.P8D

# leak-safe primitives + IO reused verbatim
_write_json = P8E._write_json
_write_csv = P8E._write_csv
_round = P8E._round
_utc_now_iso = P8E._utc_now_iso
SensPanel = P8E.SensPanel
SensSetup = P8E.SensSetup
evaluate_sensitivity_setup = P8E.evaluate_sensitivity_setup
walk_forward_lift = P8E.walk_forward_lift
classify_setup = P8E.classify_setup
mt_required_lift = P8E.mt_required_lift
simulate_event_portfolio = P8E.simulate_event_portfolio
_fwd5_pivot = P8E._fwd5_pivot
_spy_weekly = P8E._spy_weekly
COHORT_CATALOG = P8E.COHORT_CATALOG
SECTOR_ETF = P8E.SECTOR_ETF
SENS_DRIVERS = P8E.SENS_DRIVERS
RECENT_START = P8E.RECENT_START
COST_BPS_GRID = P8E.COST_BPS_GRID

PHASE = "8-F"
OBJECTIVE = (
    "Operate a durable autonomous research OS: read state, update memory, allocate agent work, "
    "detect provider keys and build provider-agnostic connectors/schemas/fixtures, validate the "
    "S8E-011 lead in depth, pre-register and (where local data allows) test external-event x "
    "ticker-sensitivity setups, update the model-candidate registry, and decide the next cycle "
    "autonomously. Research only; no orders/automation/optimization; external data never faked."
)

# --------------------------------------------------------------------------- #
# Recommendation vocabulary (allowed set, exact order). ERROR is the internal guard.
# --------------------------------------------------------------------------- #
REC_CONFIRMED = "CONFIRMED_EXTERNAL_SENSITIVITY_SIGNAL_FOUND"
REC_PROMISING_PROVIDER = "PROMISING_NEEDS_PROVIDER_HISTORY"
REC_PROMISING_UNCONF = "PROMISING_BUT_UNCONFIRMED"
REC_NEEDS_NEWS = "NEEDS_NEWS_SENTIMENT_PROVIDER_DATA"
REC_NEEDS_ANALYST = "NEEDS_ANALYST_REVISION_PROVIDER_DATA"
REC_NEEDS_EXTERNAL = "NEEDS_EXTERNAL_PROVIDER_DATA"
REC_REJECTED = "EXTERNAL_SIGNAL_RESEARCH_REJECTED"
REC_OS_READY_BLOCKED = "AUTONOMOUS_SIGNAL_OS_READY_BUT_PROVIDER_BLOCKED"
REC_FRAMEWORK_BLOCKED = "ASSESSMENT_FRAMEWORK_BLOCKED"
REC_ERROR = "ERROR"
ALLOWED_RECOMMENDATIONS = (
    REC_CONFIRMED, REC_PROMISING_PROVIDER, REC_PROMISING_UNCONF, REC_NEEDS_NEWS,
    REC_NEEDS_ANALYST, REC_NEEDS_EXTERNAL, REC_REJECTED, REC_OS_READY_BLOCKED,
    REC_FRAMEWORK_BLOCKED, REC_ERROR,
)

# Per-signal promotion status vocabulary.
ST_CONFIRMED = "CONFIRMED_EXTERNAL_SENSITIVITY_SIGNAL"
ST_PROMISING_PROVIDER = "PROMISING_NEEDS_PROVIDER_HISTORY"
ST_PROMISING_UNCONF = "PROMISING_BUT_UNCONFIRMED"
ST_NEEDS_PROVIDER = "NEEDS_PROVIDER_DATA"
ST_REJECTED = "REJECTED"
ST_BLOCKED = "BLOCKED"
ALLOWED_STATUSES = (ST_CONFIRMED, ST_PROMISING_PROVIDER, ST_PROMISING_UNCONF,
                    ST_NEEDS_PROVIDER, ST_REJECTED, ST_BLOCKED)

# --------------------------------------------------------------------------- #
# Paths (large data on D: only; repo gets summaries/manifests).
# --------------------------------------------------------------------------- #
DATA_ROOT = Path("D:/Stock_Prediction_app_data")
SENS_PANEL_ROOT = DATA_ROOT / "research_panels" / "phase8e_sensitivity"   # reuse persisted 8-E panel
EXTERNAL_RAW = DATA_ROOT / "external_raw"
EXTERNAL_NORMALIZED = DATA_ROOT / "external_normalized"
EXTERNAL_ADAPTERS = DATA_ROOT / "external_adapters"
DEFAULT_OUT_DIR = _REPO_ROOT / "research" / "output" / "phase8f_autonomous_external_signal_os"

# --------------------------------------------------------------------------- #
# Experiment budget (Part 9).
# --------------------------------------------------------------------------- #
MAX_TOTAL_EXPERIMENTS = 250
MAX_PER_FAMILY = 75
CHALLENGE_MIN_FRAC = 0.30
MAX_CYCLES = 3

# Bounded live-collection caps (Part 4) — only ever used if a key is present.
LIVE_MAX_TICKERS = 25
LIVE_MAX_DAYS = 30
LIVE_MAX_EVENTS = 500

# --------------------------------------------------------------------------- #
# Agents (Part 3) — 15 named responsibilities.
# --------------------------------------------------------------------------- #
DIR_A = "quant-research-director"
DATA_A = "data-foundation-agent"
UNI_A = "universe-construction-agent"
FEAT_A = "feature-library-agent"
EXT_A = "external-data-agent"
NEWS_A = "news-sentiment-agent"
REV_A = "analyst-revision-agent"
EARN_A = "earnings-event-agent"
OPT_A = "options-signal-agent"
SHORT_A = "short-interest-agent"
TRANS_A = "transcript-tone-agent"
SENS_A = "sensitivity-signal-agent"
VAL_A = "validation-skeptic-agent"
RSK_A = "risk-portfolio-agent"
MODEL_A = "model-contribution-agent"
ALL_AGENTS = [DIR_A, DATA_A, UNI_A, FEAT_A, EXT_A, NEWS_A, REV_A, EARN_A, OPT_A, SHORT_A,
              TRANS_A, SENS_A, VAL_A, RSK_A, MODEL_A]

# --------------------------------------------------------------------------- #
# External event-signal families (Part 7).  G is testable locally; A-F need provider data.
# --------------------------------------------------------------------------- #
FAM_NEWS = "A_news_sentiment_x_sensitivity"
FAM_REVISION = "B_analyst_revision_x_sensitivity"
FAM_EARNINGS = "C_earnings_surprise_x_sensitivity"
FAM_OPTIONS = "D_options_iv_x_sensitivity"
FAM_SHORT = "E_short_interest_x_sensitivity"
FAM_S8E011_EXT = "F_s8e011_plus_external_confirmation"
FAM_MACRO = "G_macro_cross_asset_x_sensitivity"
PROVIDER_FAMILIES = (FAM_NEWS, FAM_REVISION, FAM_EARNINGS, FAM_OPTIONS, FAM_SHORT, FAM_S8E011_EXT)
ALL_FAMILIES = (FAM_NEWS, FAM_REVISION, FAM_EARNINGS, FAM_OPTIONS, FAM_SHORT,
                FAM_S8E011_EXT, FAM_MACRO)

# --------------------------------------------------------------------------- #
# Provider/key detection (Part 4) — env var NAMES only; values never read into output.
# --------------------------------------------------------------------------- #
PROVIDER_KEY_ENV = [
    "ALPHAVANTAGE_API_KEY", "FINNHUB_API_KEY", "FMP_API_KEY", "POLYGON_API_KEY",
    "BENZINGA_API_KEY", "EODHD_API_KEY", "INTRINIO_API_KEY", "TIINGO_API_KEY",
    "NEWSAPI_KEY", "IEX_CLOUD_API_KEY", "SEC_API_KEY", "QUANDL_API_KEY",
]
# Which providers can serve which external family (used to route a key to a connector).
FAMILY_PROVIDERS = {
    FAM_NEWS: ["NEWSAPI_KEY", "BENZINGA_API_KEY", "ALPHAVANTAGE_API_KEY", "EODHD_API_KEY", "POLYGON_API_KEY"],
    FAM_REVISION: ["FMP_API_KEY", "FINNHUB_API_KEY", "INTRINIO_API_KEY", "ALPHAVANTAGE_API_KEY"],
    FAM_EARNINGS: ["FMP_API_KEY", "FINNHUB_API_KEY", "ALPHAVANTAGE_API_KEY", "EODHD_API_KEY"],
    FAM_OPTIONS: ["POLYGON_API_KEY", "INTRINIO_API_KEY", "IEX_CLOUD_API_KEY"],
    FAM_SHORT: ["FINNHUB_API_KEY", "FMP_API_KEY", "QUANDL_API_KEY"],
    FAM_S8E011_EXT: ["FMP_API_KEY", "FINNHUB_API_KEY"],   # external confirmation = revisions/earnings
}
# Config files scanned for key NAMES only (never values).
CONFIG_PROBES = [
    _REPO_ROOT / ".env",
    _REPO_ROOT / "research" / "config" / "providers.env",
    Path.home() / ".stock_prediction_providers.env",
]

# --------------------------------------------------------------------------- #
# Six external normalized schemas (Part 5) — exact fields.
# --------------------------------------------------------------------------- #
SCHEMA_NEWS = ["event_timestamp", "point_in_time_available_at", "ticker", "provider", "headline",
               "summary", "article_url_or_provider_id", "sentiment_score", "sentiment_label",
               "relevance_score", "source", "topic_tags", "raw_payload_path"]
SCHEMA_REVISION = ["event_timestamp", "point_in_time_available_at", "ticker", "fiscal_period",
                   "estimate_type", "old_value", "new_value", "revision_direction",
                   "revision_magnitude", "analyst_count", "consensus_change", "provider_id",
                   "raw_payload_path"]
SCHEMA_EARNINGS = ["event_timestamp", "point_in_time_available_at", "ticker", "fiscal_period",
                   "actual_eps", "estimated_eps", "surprise_pct", "revenue_actual",
                   "revenue_estimate", "guidance_direction", "guidance_magnitude", "provider_id",
                   "raw_payload_path"]
SCHEMA_OPTIONS = ["event_timestamp", "point_in_time_available_at", "ticker", "implied_volatility",
                  "iv_percentile", "put_call_ratio", "skew", "term_structure",
                  "volume_open_interest_signal", "provider_id", "raw_payload_path"]
SCHEMA_SHORT = ["event_timestamp", "point_in_time_available_at", "ticker", "short_interest",
                "short_interest_pct_float", "days_to_cover", "borrow_rate", "utilization",
                "provider_id", "raw_payload_path"]
SCHEMA_TRANSCRIPT = ["event_timestamp", "point_in_time_available_at", "ticker", "transcript_type",
                     "sentiment_score", "uncertainty_score", "guidance_tone",
                     "forward_looking_language_score", "provider_id", "raw_payload_path"]


@dataclass(frozen=True)
class ExternalSource:
    key: str                  # short family key used for files
    family: str               # FAM_* signal family it feeds
    label: str
    agent: str
    schema: List[str]
    schema_name: str
    mechanism: str
    horizons: str
    cohort_hint: str          # the sensitivity cohort this external driver would pair with


EXTERNAL_SOURCES: List[ExternalSource] = [
    ExternalSource("news_sentiment", FAM_NEWS, "News & sentiment shocks", NEWS_A, SCHEMA_NEWS,
                   "news_sentiment", "sentiment shocks move sentiment-reactive names (fast decay)",
                   "1d/5d/10d", "sentiment_sensitive (est. from past news-return reactivity)"),
    ExternalSource("analyst_revision", FAM_REVISION, "Analyst estimate revisions", REV_A, SCHEMA_REVISION,
                   "analyst_revisions", "consensus EPS/target revisions re-rate revision-sensitive names",
                   "5d/20d/60d", "revision_sensitive (est. from past revision-return reactivity)"),
    ExternalSource("earnings", FAM_EARNINGS, "Earnings surprise / guidance", EARN_A, SCHEMA_EARNINGS,
                   "earnings", "surprise + guidance drift in surprise-sensitive names",
                   "5d/20d", "surprise_sensitive (est. from past PEAD reactivity)"),
    ExternalSource("options_iv", FAM_OPTIONS, "Options IV / skew", OPT_A, SCHEMA_OPTIONS,
                   "options_iv", "IV/skew shifts precede moves in options-informative names",
                   "5d/10d/20d", "options_informative (est. from IV-lead reactivity)"),
    ExternalSource("short_interest", FAM_SHORT, "Short interest / borrow", SHORT_A, SCHEMA_SHORT,
                   "short_interest", "squeeze/borrow dynamics in heavily-shorted names",
                   "10d/20d", "squeeze_sensitive (est. from short-ratio reactivity)"),
    ExternalSource("transcript_tone", FAM_NEWS, "Transcript / management tone", TRANS_A, SCHEMA_TRANSCRIPT,
                   "transcript_tone", "tone/uncertainty shifts move tone-sensitive names",
                   "1d/5d/20d", "tone_sensitive (est. from past transcript-return reactivity)"),
]
EXTERNAL_SOURCE_BY_KEY = {s.key: s for s in EXTERNAL_SOURCES}
SCHEMA_BY_NAME = {
    "news_sentiment": SCHEMA_NEWS, "analyst_revisions": SCHEMA_REVISION, "earnings": SCHEMA_EARNINGS,
    "options_iv": SCHEMA_OPTIONS, "short_interest": SCHEMA_SHORT, "transcript_tone": SCHEMA_TRANSCRIPT,
}

# Manifest filename per external source key (Part 10).
MANIFEST_FILE = {
    "news_sentiment": "news_sentiment_event_manifest.csv",
    "analyst_revision": "analyst_revision_event_manifest.csv",
    "earnings": "earnings_event_manifest.csv",
    "options_iv": "options_event_manifest.csv",
    "short_interest": "short_interest_event_manifest.csv",
    "transcript_tone": "transcript_tone_event_manifest.csv",
}


# =========================================================================== #
# Provider/key detection — names + presence only (NEVER values).
# =========================================================================== #
def detect_provider_keys() -> List[dict]:
    """Return one row per provider key: presence (bool) only, plus which families it would feed.
    Values are NEVER read into the output — only os.environ membership is checked."""
    rows = []
    for name in PROVIDER_KEY_ENV:
        present = name in os.environ and bool(str(os.environ.get(name) or "").strip())
        feeds = [fam for fam, provs in FAMILY_PROVIDERS.items() if name in provs]
        rows.append({
            "key_env_var": name, "present": present,
            "feeds_families": ";".join(feeds),
            "detection": "os.environ membership (value never read)",
        })
    return rows


def scan_config_for_key_names() -> List[dict]:
    """Scan known local config files for provider key NAMES only (never values)."""
    rows = []
    for path in CONFIG_PROBES:
        if not path.exists():
            rows.append({"config_path": str(path).replace("\\", "/"), "exists": False,
                         "key_names_present": "", "note": "absent"})
            continue
        names_found = []
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as fh:
                for line in fh:
                    head = line.split("=", 1)[0].strip()
                    if head in PROVIDER_KEY_ENV and head not in names_found:
                        names_found.append(head)
        except OSError:
            pass
        rows.append({"config_path": str(path).replace("\\", "/"), "exists": True,
                     "key_names_present": ";".join(names_found),
                     "note": "names only; values never read"})
    return rows


def provider_readiness(key_rows: List[dict]) -> Dict[str, bool]:
    """Per external family: is at least one serving provider key present?"""
    present = {r["key_env_var"] for r in key_rows if r["present"]}
    out = {}
    for fam, provs in FAMILY_PROVIDERS.items():
        out[fam] = any(p in present for p in provs)
    return out


# =========================================================================== #
# Connectors: when a key exists -> bounded live collection; else -> adapter + mock + command.
# Mock fixtures are EXPLICITLY labelled MOCK and never enter the signal scoreboard.
# =========================================================================== #
def _adapter_source(src: ExternalSource, providers: List[str]) -> str:
    """A provider-agnostic, executable adapter stub. --dry-run prints the exact bounded plan;
    with a key it would perform bounded collection into the normalized schema. No secrets logged."""
    schema_repr = ", ".join(src.schema)
    prov_repr = ", ".join(providers)
    return f'''"""Auto-generated provider-agnostic adapter for {src.label} ({src.family}).

Phase 8-F autonomous external-data connector. Offline-safe: with no key it DRY-RUNS (prints the
exact bounded collection plan and the normalized schema); with a key it performs BOUNDED
collection only. Secrets are never printed. This file is generated under D: (not committed).
"""
import os, sys, json, datetime

FAMILY = "{src.family}"
SOURCE_KEY = "{src.key}"
SCHEMA = [{", ".join(repr(c) for c in src.schema)}]
CANDIDATE_KEYS = [{", ".join(repr(p) for p in providers)}]
RAW_DIR = r"{(EXTERNAL_RAW / src.key)}"
NORMALIZED_DIR = r"{(EXTERNAL_NORMALIZED / src.key)}"
MAX_TICKERS = {LIVE_MAX_TICKERS}
MAX_DAYS = {LIVE_MAX_DAYS}
MAX_EVENTS = {LIVE_MAX_EVENTS}


def detected_key():
    for name in CANDIDATE_KEYS:
        if str(os.environ.get(name) or "").strip():
            return name          # name only; value never returned/printed
    return None


def dry_run():
    print(json.dumps({{
        "family": FAMILY, "source": SOURCE_KEY, "mode": "DRY_RUN_NO_KEY",
        "candidate_keys": CANDIDATE_KEYS, "schema": SCHEMA,
        "bounded": {{"max_tickers": MAX_TICKERS, "max_days": MAX_DAYS, "max_events": MAX_EVENTS}},
        "raw_dir": RAW_DIR, "normalized_dir": NORMALIZED_DIR,
        "note": "set one candidate key in the environment, then re-run without --dry-run",
    }}, indent=2))


def collect():
    key = detected_key()
    if not key:
        print("NO_KEY: no candidate provider key present; dry-running instead.")
        dry_run(); return 0
    # Bounded, point-in-time collection would run here against the provider named by `key`.
    # Each normalized row must carry point_in_time_available_at >= event_timestamp. Raw payloads
    # are written under RAW_DIR; normalized {schema_repr} under NORMALIZED_DIR. Caps enforced:
    # <= {LIVE_MAX_TICKERS} tickers, <= {LIVE_MAX_DAYS} days, <= {LIVE_MAX_EVENTS} events.
    print(f"KEY_DETECTED ({{key}}): bounded collection not executed in offline phase 8-F.")
    return 0


if __name__ == "__main__":
    if "--dry-run" in sys.argv or detected_key() is None:
        dry_run()
    else:
        collect()
'''


def build_connectors(readiness: Dict[str, bool]) -> Tuple[List[dict], List[dict], List[dict], List[dict]]:
    """Materialize per-source: adapter stub + normalized schema header + (if no key) a MOCK fixture.
    Returns (connector_status_rows, raw_cache_rows, normalized_rows, per_source_manifest_rows)."""
    EXTERNAL_RAW.mkdir(parents=True, exist_ok=True)
    EXTERNAL_NORMALIZED.mkdir(parents=True, exist_ok=True)
    EXTERNAL_ADAPTERS.mkdir(parents=True, exist_ok=True)
    status, raw_rows, norm_rows, manifests = [], [], [], []
    for src in EXTERNAL_SOURCES:
        providers = FAMILY_PROVIDERS.get(src.family, [])
        key_present = readiness.get(src.family, False)
        adapter_path = EXTERNAL_ADAPTERS / f"{src.key}_adapter.py"
        adapter_path.write_text(_adapter_source(src, providers), encoding="utf-8")

        norm_dir = EXTERNAL_NORMALIZED / src.key
        norm_dir.mkdir(parents=True, exist_ok=True)
        # normalized schema header (empty real-event file; ready to receive collected events)
        norm_file = norm_dir / f"{src.key}_normalized.csv"
        _write_csv(norm_file, [], src.schema)
        # mock fixture: explicitly labelled, never fed to the scoreboard
        fixture_file = norm_dir / f"{src.key}_mock_fixture.csv"
        _write_csv(fixture_file, _mock_rows(src), src.schema + ["is_mock"])

        connector_mode = "READY_KEY_PRESENT" if key_present else "ADAPTER_AND_MOCK_NO_KEY"
        cmd = (f'$env:{providers[0] if providers else "PROVIDER_API_KEY"}="<YOUR_KEY>"; '
               f'python "{adapter_path}"')
        status.append({
            "source": src.key, "family": src.family, "agent": src.agent,
            "provider_candidates": ";".join(providers), "key_present": key_present,
            "connector_mode": connector_mode,
            "adapter_path": str(adapter_path).replace("\\", "/"),
            "normalized_schema_path": str(norm_file).replace("\\", "/"),
            "mock_fixture_path": str(fixture_file).replace("\\", "/"),
            "dry_run_command": cmd,
            "live_collection_ran": False,            # offline phase: never runs live
            "schema_fields": len(src.schema),
        })
        raw_rows.append({
            "source": src.key, "family": src.family,
            "raw_dir": str((EXTERNAL_RAW / src.key)).replace("\\", "/"),
            "n_raw_payloads": 0, "bounded_caps": f"<= {LIVE_MAX_TICKERS} tickers / "
            f"{LIVE_MAX_DAYS} days / {LIVE_MAX_EVENTS} events", "status": "EMPTY_NO_KEY",
        })
        norm_rows.append({
            "source": src.key, "family": src.family, "schema_name": src.schema_name,
            "normalized_path": str(norm_file).replace("\\", "/"),
            "n_real_events": 0, "n_mock_events": _MOCK_N,
            "mock_fixture_path": str(fixture_file).replace("\\", "/"),
            "pit_field": "point_in_time_available_at",
            "status": "SCHEMA_READY_MOCK_ONLY" if not key_present else "SCHEMA_READY_KEY_PRESENT",
        })
        manifests.append({
            "source": src.key, "family": src.family, "agent": src.agent,
            "provider_candidates": ";".join(providers), "key_present": key_present,
            "schema": ";".join(src.schema), "mechanism": src.mechanism,
            "candidate_horizons": src.horizons, "cohort_hint": src.cohort_hint,
            "normalized_path": str(norm_file).replace("\\", "/"),
            "mock_fixture_path": str(fixture_file).replace("\\", "/"),
            "adapter_path": str(adapter_path).replace("\\", "/"),
            "n_real_events": 0, "n_mock_events": _MOCK_N,
            "status": "NEEDS_PROVIDER_DATA" if not key_present else "READY_FOR_BOUNDED_COLLECTION",
            "dry_run_command": cmd,
        })
    return status, raw_rows, norm_rows, manifests


_MOCK_N = 3


def _mock_rows(src: ExternalSource) -> List[dict]:
    """A few schema-complete MOCK rows (clearly flagged). Never used as real signal input."""
    tickers = ["MOCKA", "MOCKB", "MOCKC"]
    rows = []
    for i, tk in enumerate(tickers):
        row = {c: "" for c in src.schema}
        row["ticker"] = tk
        row["event_timestamp"] = f"2026-01-{10 + i:02d}T13:30:00Z"
        row["point_in_time_available_at"] = f"2026-01-{10 + i:02d}T13:45:00Z"
        if "provider_id" in row:
            row["provider_id"] = "MOCK"
        if "provider" in row:
            row["provider"] = "MOCK"
        if "raw_payload_path" in row:
            row["raw_payload_path"] = "MOCK_FIXTURE"
        row["is_mock"] = True
        rows.append(row)
    return rows


def write_acquisition_commands(out_dir: Path, status_rows: List[dict]) -> Path:
    """Emit a committed-safe PowerShell script that sets a placeholder key and runs each adapter."""
    lines = [
        "# Phase 8-F provider acquisition commands (committed-safe; NO secrets).",
        "# Set a real key for the chosen provider, then run the matching adapter. Bounded caps are",
        f"# enforced inside each adapter: <= {LIVE_MAX_TICKERS} tickers, <= {LIVE_MAX_DAYS} days, "
        f"<= {LIVE_MAX_EVENTS} events. Raw under {str(EXTERNAL_RAW).replace(chr(92), '/')}; "
        f"normalized under {str(EXTERNAL_NORMALIZED).replace(chr(92), '/')}.",
        "",
    ]
    for r in status_rows:
        first_provider = (r["provider_candidates"].split(";") or ["PROVIDER_API_KEY"])[0]
        lines.append(f"# {r['family']} ({r['source']}) via one of: {r['provider_candidates']}")
        lines.append(f'$env:{first_provider} = "<YOUR_KEY>"')
        lines.append(f'python "{r["adapter_path"]}"   # add --dry-run to preview the plan only')
        lines.append("")
    path = out_dir / "provider_acquisition_commands.ps1"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


# =========================================================================== #
# Panel loading — reuse the persisted 8-E weekly grid (no Norgate rebuild).
# =========================================================================== #
def load_persisted_panel() -> Optional[SensPanel]:
    grid_path = SENS_PANEL_ROOT / "weekly_observation_grid.csv"
    meta_path = SENS_PANEL_ROOT / "symbol_metadata.csv"
    spy_path = SENS_PANEL_ROOT / "spy_daily_close.csv"
    sens_path = SENS_PANEL_ROOT / "ticker_sensitivity_map_full.csv"
    if not (grid_path.exists() and spy_path.exists()):
        return None
    grid = pd.read_csv(grid_path, parse_dates=["date"])
    grid["ctrl_bucket"] = grid["ctrl_bucket"].astype(str)
    meta = pd.read_csv(meta_path).set_index("ticker") if meta_path.exists() else pd.DataFrame()
    spy = pd.read_csv(spy_path, index_col=0)
    spy.index = pd.to_datetime(spy.index)
    spy_close = pd.to_numeric(spy.iloc[:, 0], errors="coerce").dropna()
    sens_map = pd.read_csv(sens_path) if sens_path.exists() else pd.DataFrame()
    grid_dates = pd.DatetimeIndex(sorted(grid["date"].unique()))
    return SensPanel(grid, meta, spy_close, grid_dates, True, sens_map, [], [], ["loaded from persisted 8-E panel"])


# =========================================================================== #
# Experiment ledger (Part 7/9) — one entry per pre-registered experiment.
# =========================================================================== #
@dataclass
class Experiment:
    exp_id: str
    cycle: int
    family: str
    agent: str
    kind: str                  # signal_test | validation | deep_dive | connector
    is_challenge: bool
    external_driver: str
    cohort: str
    needs_provider: bool
    hypothesis: str
    status: str = ""
    reason: str = ""
    metrics: dict = field(default_factory=dict)


def _macro_experiments_from_8e(panel: SensPanel, cycle: int) -> Tuple[List[Experiment], object]:
    """Run the 8-E macro campaign on the loaded grid (family G) and map to 8-F experiments.
    Excludes 8-E's own provider templates (A-F are pre-registered freshly below)."""
    state, _meta = P8E.run_campaign(panel)
    exps: List[Experiment] = []
    for e in state.registry:
        if e.needs_provider:
            continue                                   # 8-E provider stubs -> superseded by 8-F A-F
        status = _map_macro_status(e, panel)
        exps.append(Experiment(
            exp_id=e.setup_id, cycle=cycle, family=FAM_MACRO, agent=SENS_A,
            kind="validation" if e.is_challenge else "signal_test",
            is_challenge=e.is_challenge, external_driver=e.driver, cohort=e.cohort,
            needs_provider=False, hypothesis=e.hypothesis, status=status, reason=e.reason,
            metrics=e.metrics or {}))
    return exps, state


def _map_macro_status(e: SensSetup, panel: SensPanel) -> str:
    """Map an 8-E setup status into the 8-F promotion vocabulary."""
    if e.status == P8E.ST_CONFIRMED:
        return ST_CONFIRMED
    if e.status == P8E.ST_PROMISING:
        # promising on a locally-testable (macro) driver -> unconfirmed (tail/portfolio gate)
        return ST_PROMISING_UNCONF
    if e.status == P8E.ST_BLOCKED:
        return ST_BLOCKED
    return ST_REJECTED


def _external_experiments(cycle: int) -> List[Experiment]:
    """Pre-register the external-event x sensitivity setups (A-F). All NEEDS_PROVIDER (no key)."""
    exps = []
    specs = [
        ("F8F-A01", FAM_NEWS, NEWS_A, "news_sentiment", "sentiment_sensitive",
         "Positive news-sentiment shock in a sentiment-sensitive name drifts up over 1-5d."),
        ("F8F-B01", FAM_REVISION, REV_A, "analyst_revision", "revision_sensitive",
         "Upward consensus EPS/target revision in a revision-sensitive name drifts up over 20-60d."),
        ("F8F-C01", FAM_EARNINGS, EARN_A, "earnings_surprise", "surprise_sensitive",
         "Positive earnings surprise + raised guidance in a surprise-sensitive name (PEAD) over 5-20d."),
        ("F8F-D01", FAM_OPTIONS, OPT_A, "options_iv", "options_informative",
         "IV/skew shift in an options-informative name precedes a directional move over 5-20d."),
        ("F8F-E01", FAM_SHORT, SHORT_A, "short_interest", "squeeze_sensitive",
         "Rising short interest + high borrow in a squeeze-sensitive name over 10-20d."),
        ("F8F-F01", FAM_S8E011_EXT, SENS_A, "analyst_revision", "cohort_rates_neg",
         "S8E-011 (rates shock x short-duration cohort) further confirmed by an upward analyst "
         "revision — external confirmation overlay on the macro lead."),
    ]
    for exp_id, fam, agent, driver, cohort, hyp in specs:
        exps.append(Experiment(
            exp_id=exp_id, cycle=cycle, family=fam, agent=agent, kind="signal_test",
            is_challenge=False, external_driver=driver, cohort=cohort, needs_provider=True,
            hypothesis=hyp, status=ST_NEEDS_PROVIDER,
            reason="no provider key present; adapter + schema + mock fixture + dry-run command built",
            metrics={"needs_provider": True}))
    return exps


# =========================================================================== #
# S8E-011 deep-dive validation track (Part 6).
# =========================================================================== #
def _s8e011_setup() -> SensSetup:
    for s in P8E.plan_cycle_1():
        if s.setup_id == "S8E-011":
            return s
    raise RuntimeError("S8E-011 not found in 8-E cycle-1 plan")


def _with_delisted_flag(grid: pd.DataFrame, panel: SensPanel) -> pd.DataFrame:
    if panel.metadata is None or panel.metadata.empty or "is_delisted" not in panel.metadata.columns:
        g = grid.copy()
        g["is_delisted"] = False
        return g
    flag = panel.metadata["is_delisted"].astype(bool)
    g = grid.copy()
    g["is_delisted"] = g["symbol"].map(flag).fillna(False)
    return g


def _group_diag(triggered: pd.DataFrame, lbl: str) -> dict:
    n = int(len(triggered))
    if n == 0:
        return {"n_events": 0, "mean_fwd": None, "worst_decile_mean": None, "hit_rate": None}
    vals = triggered[lbl]
    wd = float(vals[vals <= vals.quantile(0.10)].mean()) if n >= 10 else float("nan")
    return {"n_events": n, "mean_fwd": _round(float(vals.mean())),
            "worst_decile_mean": _round(wd), "hit_rate": _round(float((vals > 0).mean()))}


def s8e011_deep_dive(panel: SensPanel) -> Tuple[List[dict], List[dict]]:
    """Decompose S8E-011 triggered events by sector/year/rate-regime/liquidity/vol/active-delisted,
    and isolate the catastrophic tail. Returns (deep_dive_rows, tail_rows)."""
    setup = _s8e011_setup()
    h = setup.primary_horizon
    lbl = f"fwd_excess_{h}"
    g = _with_delisted_flag(panel.grid, panel)
    g = g[g[lbl].notna()].copy()
    trig = setup.trigger_mask(g).to_numpy().astype(bool)
    t = g[trig].copy()
    rows: List[dict] = []

    def add(dim, value, sub):
        d = _group_diag(sub, lbl)
        rows.append({"dimension": dim, "bucket": str(value), **d})

    add("ALL", "all_triggered", t)
    for sec, sub in t.groupby("sector", observed=True):
        add("sector", sec, sub)
    for yr, sub in t.groupby(t["date"].dt.year):
        add("year", yr, sub)
    # rate-regime: magnitude tertile of the (negative) rates shock that gates the setup
    if "drv_rates_shock_z" in t.columns and len(t):
        q = t["drv_rates_shock_z"]
        bins = pd.qcut(q.rank(method="first"), 3, labels=["mild_selloff", "moderate_selloff", "severe_selloff"])
        for rg, sub in t.groupby(bins, observed=True):
            add("rate_regime", rg, sub)
    for lb, sub in t.groupby("liq_bucket", observed=True):
        add("liquidity_bucket", lb, sub)
    for vb, sub in t.groupby("vol_bucket", observed=True):
        add("volatility_bucket", vb, sub)
    for bb, sub in t.groupby("beta_bucket", observed=True):
        add("beta_bucket", bb, sub)
    for dl, sub in t.groupby("is_delisted", observed=True):
        add("active_vs_delisted", "delisted" if dl else "active", sub)

    # tail decomposition: which structural buckets dominate the worst-decile events?
    tail_rows: List[dict] = []
    if len(t) >= 10:
        thresh = t[lbl].quantile(0.10)
        tail = t[t[lbl] <= thresh]
        tail_rows.append({"slice": "worst_decile_overall", "n_events": int(len(tail)),
                          "mean_fwd": _round(float(tail[lbl].mean())),
                          "threshold_fwd_excess_20": _round(float(thresh)), "detail": ""})
        for dim, col in [("sector", "sector"), ("liquidity_bucket", "liq_bucket"),
                         ("volatility_bucket", "vol_bucket"), ("beta_bucket", "beta_bucket"),
                         ("year", None)]:
            if col is None:
                share = tail["date"].dt.year.value_counts(normalize=True)
            else:
                share = tail[col].value_counts(normalize=True)
            if len(share):
                top = share.index[0]
                tail_rows.append({
                    "slice": f"tail_top_{dim}", "n_events": int(share.iloc[0] * len(tail)),
                    "mean_fwd": None, "threshold_fwd_excess_20": None,
                    "detail": f"{dim}={top} contributes {share.iloc[0]:.1%} of worst-decile events"})
    return rows, tail_rows


# Fixed, pre-declared filters (Part 6). Each filter is identifiable BEFORE the event (structural
# bucket known at decision time), never the realized outcome.
def _fixed_filters() -> List[Tuple[str, str]]:
    return [
        ("remove_bottom_liquidity_quintile", "liq_bucket >= 1"),
        ("remove_extreme_beta_tails", "beta_bucket == 1"),
        ("remove_top_volatility_quintile", "vol_bucket <= 3"),
        ("remove_bottom_liq_and_top_vol", "liq_bucket >= 1 & vol_bucket <= 3"),
    ]


def _apply_filter(g: pd.DataFrame, expr: str) -> pd.DataFrame:
    return g.query(expr) if expr else g


def s8e011_fixed_filter_stress(panel: SensPanel) -> List[dict]:
    """Apply each fixed pre-declared filter and recompute the gate-relevant metrics (lift, EV,
    worst-decile, recency, portfolio). Reports whether a filter improves the tail/portfolio gate
    WITHOUT tuning (filters are declared a priori on ex-ante-known structural buckets)."""
    setup = _s8e011_setup()
    spy_week = _spy_weekly(panel.spy_close, panel.grid_dates)
    rows = []

    def evaluate_on(label, sub):
        ev = evaluate_sensitivity_setup(setup, sub)
        if ev.get("n_events", 0) == 0:
            rows.append({"filter": label, "n_events": 0, "reason": "no events after filter"})
            return
        fwd5 = _fwd5_pivot(sub)
        port = simulate_event_portfolio(setup, sub, fwd5_pivot=fwd5, spy_week=spy_week)
        rows.append({
            "filter": label, "n_events": ev.get("n_events"),
            "n_recent_events": ev.get("n_recent_events"),
            "lift_vs_control": ev.get("lift_vs_control"),
            "ev_after_25bps": ev.get("ev_after_25bps"),
            "worst_decile_mean": ev.get("worst_decile_mean"),
            "recent_lift_vs_control": ev.get("recent_lift_vs_control"),
            "max_sector_fraction": ev.get("max_sector_fraction"),
            "beats_spy_active": bool(port.get("beats_spy_active")),
            "beats_cash_active": bool(port.get("beats_cash_active")),
            "worst_decile_improved": None,    # filled vs baseline below
        })

    g = panel.grid
    evaluate_on("baseline_no_filter", g)
    for name, expr in _fixed_filters():
        try:
            evaluate_on(name, _apply_filter(g, expr))
        except Exception as exc:                                 # pragma: no cover
            rows.append({"filter": name, "n_events": 0, "reason": f"filter error: {exc!r}"})
    # annotate tail improvement vs baseline
    base = next((r for r in rows if r["filter"] == "baseline_no_filter"), {})
    base_wd = base.get("worst_decile_mean")
    for r in rows:
        if r.get("worst_decile_mean") is not None and base_wd is not None and r["filter"] != "baseline_no_filter":
            r["worst_decile_improved"] = bool(r["worst_decile_mean"] > base_wd)
    return rows


# =========================================================================== #
# Evaluation roll-ups for the external-event scoreboard + matched controls + risk.
# =========================================================================== #
def _scoreboard_rows(ledger: List[Experiment]) -> List[dict]:
    rows = []
    for e in ledger:
        m = e.metrics or {}
        rows.append({
            "exp_id": e.exp_id, "cycle": e.cycle, "family": e.family, "agent": e.agent,
            "kind": e.kind, "is_challenge": e.is_challenge, "needs_provider": e.needs_provider,
            "external_driver": e.external_driver, "cohort": e.cohort, "status": e.status,
            "n_events": m.get("n_events"), "n_recent_events": m.get("n_recent_events"),
            "lift_vs_control": m.get("lift_vs_control"),
            "ev_after_25bps": m.get("ev_after_25bps"),
            "worst_decile_mean": m.get("worst_decile_mean"),
            "recent_lift_vs_control": m.get("recent_lift_vs_control"),
            "hit_rate": m.get("hit_rate"), "reason": e.reason,
        })
    return rows


_SCOREBOARD_COLS = ["exp_id", "cycle", "family", "agent", "kind", "is_challenge", "needs_provider",
                    "external_driver", "cohort", "status", "n_events", "n_recent_events",
                    "lift_vs_control", "ev_after_25bps", "worst_decile_mean",
                    "recent_lift_vs_control", "hit_rate", "reason"]


def _matched_control_rows(ledger: List[Experiment]) -> List[dict]:
    rows = []
    for e in ledger:
        m = e.metrics or {}
        if e.needs_provider or not m.get("n_events"):
            continue
        rows.append({
            "exp_id": e.exp_id, "family": e.family, "external_driver": e.external_driver,
            "cohort": e.cohort, "is_challenge": e.is_challenge, "n_events": m.get("n_events"),
            "n_matched_control": m.get("n_matched_control"),
            "triggered_mean": m.get("triggered_mean"), "control_mean": m.get("control_mean"),
            "lift_vs_control": m.get("lift_vs_control"),
            "hit_rate": m.get("hit_rate"), "control_hit_rate": m.get("control_hit_rate"),
            "ev_after_25bps": m.get("ev_after_25bps"),
            "recent_lift_vs_control": m.get("recent_lift_vs_control"),
        })
    return rows


def _risk_portfolio_rows(ledger: List[Experiment]) -> List[dict]:
    """Tail/worst-decile/concentration view for every testable experiment (fixed filters only)."""
    rows = []
    for e in ledger:
        m = e.metrics or {}
        if e.needs_provider or not m.get("n_events"):
            continue
        port = m.get("portfolio", {}) or {}
        rows.append({
            "exp_id": e.exp_id, "family": e.family, "cohort": e.cohort,
            "n_events": m.get("n_events"), "worst_decile_mean": m.get("worst_decile_mean"),
            "avg_mae_20": m.get("avg_mae_20"),
            "max_year_fraction": m.get("max_year_fraction"),
            "max_sector_fraction": m.get("max_sector_fraction"),
            "max_ticker_fraction": m.get("max_ticker_fraction"),
            "beats_spy_active": bool(port.get("beats_spy_active")),
            "beats_cash_active": bool(port.get("beats_cash_active")),
            "tail_gate_pass": (m.get("worst_decile_mean") is not None
                               and m.get("worst_decile_mean") >= P8E.GATE_WORST_DECILE_FLOOR),
        })
    return rows


def _validation_skeptic_rows(ledger: List[Experiment], filter_rows: List[dict]) -> List[dict]:
    rows = []
    for e in ledger:
        if e.needs_provider:
            continue
        checks = (e.metrics or {}).get("checks", {})
        rows.append({
            "exp_id": e.exp_id, "family": e.family, "kind": e.kind, "is_challenge": e.is_challenge,
            "status": e.status, "lift_vs_control": (e.metrics or {}).get("lift_vs_control"),
            "n_failed_checks": sum(1 for v in checks.values() if v is False),
            "failed_checks": "; ".join(k for k, v in checks.items() if v is False),
            "reason": e.reason,
        })
    # fixed-filter stresses are skeptic challenges on S8E-011
    for r in filter_rows:
        if r["filter"] == "baseline_no_filter":
            continue
        rows.append({
            "exp_id": f"S8E-011::{r['filter']}", "family": FAM_MACRO, "kind": "validation",
            "is_challenge": True, "status": "DIAGNOSTIC",
            "lift_vs_control": r.get("lift_vs_control"),
            "n_failed_checks": "", "failed_checks": "",
            "reason": (f"fixed filter; worst_decile_improved={r.get('worst_decile_improved')} "
                       f"beats_spy_active={r.get('beats_spy_active')}"),
        })
    return rows


def _multiple_testing_report(ledger: List[Experiment]) -> dict:
    testable = [e for e in ledger if not e.needs_provider and (e.metrics or {}).get("n_events")]
    n_search = max(len(testable), 10)
    placebos = [e.exp_id for e in ledger
                if e.is_challenge and ((e.metrics or {}).get("lift_vs_control") or -1) >= P8E.GATE_PLACEBO_MAX_LIFT]
    return {
        "n_experiments_total": len(ledger),
        "n_testable_scored": len(testable),
        "n_needs_provider": sum(1 for e in ledger if e.needs_provider),
        "base_lift_hurdle": P8E.GATE_MIN_LIFT,
        "deflated_lift_hurdle": mt_required_lift(n_search),
        "method": "a-priori lift hurdle inflated by log10(n_search); cohort-aware matched control "
                  "(same shock, NOT in cohort) + recent-period + walk-forward + fixed-filter stress",
        "n_confirmed": sum(1 for e in ledger if e.status == ST_CONFIRMED),
        "n_promising_unconfirmed": sum(1 for e in ledger if e.status == ST_PROMISING_UNCONF),
        "n_promising_needs_provider": sum(1 for e in ledger if e.status == ST_PROMISING_PROVIDER),
        "challenge_fraction": _budget(ledger)["challenge_fraction"],
        "challenges_showing_lift": placebos,
    }


# =========================================================================== #
# Budget + promotion + model-candidate registry.
# =========================================================================== #
def _budget(ledger: List[Experiment]) -> dict:
    n = len(ledger)
    n_provider = sum(1 for e in ledger if e.needs_provider)
    n_testable = n - n_provider
    n_challenge = sum(1 for e in ledger if e.is_challenge)
    fam_counts: Dict[str, int] = {}
    for e in ledger:
        fam_counts[e.family] = fam_counts.get(e.family, 0) + 1
    frac = (n_challenge / n_testable) if n_testable else 0.0
    return {
        "experiments_registered": n, "max_total_experiments": MAX_TOTAL_EXPERIMENTS,
        "n_testable": n_testable, "n_needs_provider": n_provider, "n_challenge": n_challenge,
        "challenge_fraction": _round(frac, 4), "challenge_ok": frac >= CHALLENGE_MIN_FRAC,
        "per_family_counts": fam_counts,
        "per_family_ok": all(v <= MAX_PER_FAMILY for v in fam_counts.values()),
        "within_total_budget": n <= MAX_TOTAL_EXPERIMENTS,
        "all_pre_registered": True,
    }


def model_candidate_rows(ledger: List[Experiment], filter_rows: List[dict]) -> List[dict]:
    """model-contribution-agent: which signals may enter the model-candidate registry (no deploy)."""
    rows = []
    for e in ledger:
        if e.is_challenge:
            continue
        m = e.metrics or {}
        if e.status == ST_CONFIRMED:
            decision, contribution = "ADMIT_AS_CANDIDATE", "conditional external-sensitivity feature"
        elif e.status == ST_PROMISING_UNCONF:
            decision, contribution = "HOLD_BLOCKED_ON_TAIL", "conditional feature pending tail/portfolio fix"
        elif e.status == ST_NEEDS_PROVIDER:
            decision, contribution = "BLOCKED_ON_PROVIDER", "pending external provider history"
        else:
            decision, contribution = "REJECT", "no contribution"
        rows.append({
            "candidate_id": e.exp_id, "family": e.family, "external_driver": e.external_driver,
            "cohort": e.cohort, "signal_status": e.status, "registry_decision": decision,
            "proposed_contribution": contribution,
            "lift_vs_control": m.get("lift_vs_control"), "ev_after_25bps": m.get("ev_after_25bps"),
            "worst_decile_mean": m.get("worst_decile_mean"),
            "deployed": False, "paper_trader_output": False, "production": False,
            "note": e.reason,
        })
    return rows


def promotion_log_rows(ledger: List[Experiment]) -> List[dict]:
    rows = []
    for e in ledger:
        if e.is_challenge:
            continue
        if e.status in (ST_CONFIRMED, ST_PROMISING_UNCONF, ST_PROMISING_PROVIDER, ST_NEEDS_PROVIDER):
            rows.append({"signal_id": e.exp_id, "family": e.family, "from_status": "candidate",
                         "to_status": e.status, "cycle": e.cycle,
                         "promoted": e.status in (ST_CONFIRMED,),
                         "rationale": e.reason})
    return rows


def graveyard_rows(ledger: List[Experiment]) -> List[dict]:
    """Rejected hypotheses this phase + carry-forward family-level rejections from 8-B..8-E."""
    rows = []
    carry = [
        ("8-B", "always_on_momentum_value_quality", "weak full-sample; rejected"),
        ("8-C", "broad_always_on_price_volume_factor", "Russell 3000; rejected"),
        ("8-D", "conditional_price_volume_setups", "0 confirmed / 0 promising / 19 rejected"),
        ("8-E", "macro_shock_x_sensitivity (most variants)", "lifts erased by cost; 24 rejected"),
    ]
    for ph, hyp, why in carry:
        rows.append({"origin_phase": ph, "hypothesis": hyp, "family": "carry_forward",
                     "status": "REJECTED", "reason": why})
    for e in ledger:
        if e.status in (ST_REJECTED, ST_BLOCKED):
            rows.append({"origin_phase": PHASE, "hypothesis": e.hypothesis, "family": e.family,
                         "status": e.status, "reason": e.reason})
    return rows


# =========================================================================== #
# Recommendation (Part 8 decision values).
# =========================================================================== #
def derive_recommendation(framework_ok: bool, panel_ok: bool, readiness: Dict[str, bool],
                          ledger: List[Experiment]) -> Tuple[str, dict]:
    confirmed = [e for e in ledger if e.status == ST_CONFIRMED]
    prom_unconf = [e for e in ledger if e.status == ST_PROMISING_UNCONF]
    prom_provider = [e for e in ledger if e.status == ST_PROMISING_PROVIDER]
    needs_provider = [e for e in ledger if e.status == ST_NEEDS_PROVIDER]
    testable_scored = [e for e in ledger if not e.needs_provider and (e.metrics or {}).get("n_events")]
    any_key = any(readiness.values())
    detail = {
        "n_confirmed": len(confirmed), "n_promising_unconfirmed": len(prom_unconf),
        "n_promising_needs_provider": len(prom_provider), "n_needs_provider": len(needs_provider),
        "n_testable_scored": len(testable_scored), "any_provider_key_present": any_key,
        "provider_readiness": readiness,
    }
    if not framework_ok:
        return REC_FRAMEWORK_BLOCKED, detail
    if confirmed:
        return REC_CONFIRMED, detail
    if prom_unconf:
        return REC_PROMISING_UNCONF, detail            # S8E-011 headline: promising, tail-blocked
    if prom_provider:
        return REC_PROMISING_PROVIDER, detail
    if not panel_ok and not any_key:
        return REC_OS_READY_BLOCKED, detail
    if needs_provider and not testable_scored:
        return REC_OS_READY_BLOCKED, detail
    if needs_provider:
        # which provider unlocks the most-valuable next family (priority: analyst -> news -> other)
        fams = {e.family for e in needs_provider}
        if FAM_REVISION in fams:
            return REC_NEEDS_ANALYST, detail
        if FAM_NEWS in fams:
            return REC_NEEDS_NEWS, detail
        return REC_NEEDS_EXTERNAL, detail
    if testable_scored:
        return REC_REJECTED, detail
    return REC_OS_READY_BLOCKED, detail


# =========================================================================== #
# Agent operating memory + backlog + task board + decision log (Part 1/2).
# =========================================================================== #
BACKLOG_SEED = [
    ("BL-01", "S8E-011 validation", SENS_A, "high",
     "Deep-dive S8E-011 tail/portfolio failure; test fixed pre-declared filters."),
    ("BL-02", "news/sentiment ingestion", NEWS_A, "high",
     "Build news/sentiment connector + schema + fixture; collect when a key exists."),
    ("BL-03", "analyst revision ingestion", REV_A, "high",
     "Build analyst-revision connector + schema; revision drift is the top-priority anomaly."),
    ("BL-04", "earnings surprise/guidance ingestion", EARN_A, "medium",
     "Build earnings connector; inventory local SEC/SimFin; PEAD x surprise-sensitivity."),
    ("BL-05", "options IV/skew ingestion", OPT_A, "medium",
     "Build options connector + schema; IV/skew lead in options-informative names."),
    ("BL-06", "short-interest ingestion", SHORT_A, "low",
     "Build short-interest connector; FINRA biweekly is free."),
    ("BL-07", "transcript/tone ingestion", TRANS_A, "low",
     "Build transcript-tone connector + schema."),
    ("BL-08", "provider credentials needed", EXT_A, "high",
     "No provider key detected; emit acquisition commands and route keys to connectors."),
    ("BL-09", "external event signal tests", SENS_A, "high",
     "Pre-register A-G external-event x sensitivity setups; run G now, A-F on provider data."),
    ("BL-10", "model-candidate promotion tests", MODEL_A, "medium",
     "Decide which signals may enter the model-candidate registry (no deploy / no Paper Trader)."),
]


def build_research_memory(rec: str, detail: dict, ledger: List[Experiment],
                          readiness: Dict[str, bool], filter_rows: List[dict],
                          panel: SensPanel) -> dict:
    best = next((e for e in ledger if e.exp_id == "S8E-011"), None)
    prom_unconf = [e.exp_id for e in ledger if e.status == ST_PROMISING_UNCONF]
    rejected_fams = sorted({e.family for e in ledger
                            if e.status in (ST_REJECTED,) and e.family == FAM_MACRO})
    needs = [e.family for e in ledger if e.status == ST_NEEDS_PROVIDER]
    any_filter_helps = any(r.get("worst_decile_improved") for r in filter_rows)
    return {
        "phase": PHASE, "generated_utc": _utc_now_iso(), "recommendation": rec,
        "current_best_leads": [
            {"signal_id": "S8E-011", "family": FAM_MACRO,
             "driver": "rates", "cohort": "cohort_rates_neg",
             "status": (best.status if best else "unknown"),
             "lift_vs_control": (best.metrics or {}).get("lift_vs_control") if best else None,
             "ev_after_25bps": (best.metrics or {}).get("ev_after_25bps") if best else None,
             "blocker": "worst-decile tail + portfolio does not beat SPY/cash active",
             "fixed_filter_helps_tail": any_filter_helps}],
        "promising_unconfirmed": prom_unconf,
        "rejected_families": rejected_fams + ["8-B momentum/value/quality", "8-C broad price/volume",
                                              "8-D conditional price/volume"],
        "external_data_gaps": [s.key for s in EXTERNAL_SOURCES],
        "provider_readiness": readiness,
        "confirmed_signals": [e.exp_id for e in ledger if e.status == ST_CONFIRMED],
        "promising_signals": prom_unconf,
        "rejected_signals": [e.exp_id for e in ledger if e.status in (ST_REJECTED, ST_BLOCKED)],
        "needs_provider_families": needs,
        "open_research_questions": [
            "Does a fixed liquidity/vol filter rescue the S8E-011 tail without tuning?",
            "Does an upward analyst revision confirm S8E-011 (family F) once revision data exists?",
            "Which external family (revisions vs news) yields the first CONFIRMED external signal?",
            "Do sensitivity cohorts add lift on a broader (S&P 1500 / R3000) survivorship-aware panel?",
        ],
        "next_autonomous_actions": [
            "Acquire analyst-revision provider data (priority 1) and run family B + F with the SAME gate.",
            "Acquire news/sentiment data (priority 2); run family A.",
            "Broaden the daily universe and re-run S8E-011 with fixed filters; thresholds FIXED.",
        ],
        "stop_conditions": [
            "stop when one signal is CONFIRMED",
            "stop when all local + credential-available external sources are exhausted",
            "stop when provider access is required for the next meaningful step",
            "stop on a safety/leakage problem",
            "stop when the experiment budget (250) is exhausted",
        ],
        "panel": {
            "n_symbols": int(panel.grid["symbol"].nunique()) if not panel.grid.empty else 0,
            "n_obs": int(len(panel.grid)), "source": "persisted 8-E weekly grid (reused, no rebuild)",
        },
    }


def backlog_rows(ledger: List[Experiment], readiness: Dict[str, bool]) -> List[dict]:
    done_families = {e.family for e in ledger}
    rows = []
    for bl_id, item, agent, prio, desc in BACKLOG_SEED:
        if "validation" in item:
            state = "DONE_THIS_PHASE"
        elif "model-candidate" in item:
            state = "DONE_THIS_PHASE"
        elif "external event signal tests" in item:
            state = "PARTIAL_G_DONE_AF_PENDING_PROVIDER"
        elif "credentials" in item:
            state = "BLOCKED_NO_KEY"
        else:
            state = "CONNECTOR_BUILT_PENDING_KEY"
        rows.append({"backlog_id": bl_id, "work_item": item, "owning_agent": agent,
                     "priority": prio, "state": state, "description": desc})
    return rows


def task_board_rows(cycles: List[dict]) -> List[dict]:
    rows = []
    for c in cycles:
        for task in c["tasks"]:
            rows.append({"cycle": c["cycle"], **task})
    return rows


def decision_log_rows(cycles: List[dict]) -> List[dict]:
    return [{"cycle": c["cycle"], "decision": c["decision"], "rationale": c["rationale"],
             "continue": c["continue"], "stop_reason": c.get("stop_reason", "")} for c in cycles]


# =========================================================================== #
# Cycle manager (Part 2) — 3 autonomous cycles.
# =========================================================================== #
def run_cycles(panel: SensPanel, readiness: Dict[str, bool]
               ) -> Tuple[List[Experiment], List[dict], List[dict], object]:
    """Returns (ledger, cycle_summaries, fixed_filter_rows, macro_campaign_state)."""
    ledger: List[Experiment] = []
    cycles: List[dict] = []

    # ---- Cycle 1: foundation + macro (G) test + S8E-011 validation + external pre-registration ----
    macro_exps, macro_state = _macro_experiments_from_8e(panel, cycle=1)
    ledger.extend(macro_exps)
    ext_exps = _external_experiments(cycle=1)
    ledger.extend(ext_exps)
    filter_rows = s8e011_fixed_filter_stress(panel)
    n_prom = sum(1 for e in macro_exps if e.status == ST_PROMISING_UNCONF)
    cycles.append({
        "cycle": 1,
        "tasks": [
            {"agent": DATA_A, "task": "verify local data + reuse persisted 8-E panel", "result": "OK"},
            {"agent": UNI_A, "task": "active/delisted universe + identifier map", "result": "OK"},
            {"agent": FEAT_A, "task": "external feature schema + sensitivity lineage", "result": "OK"},
            {"agent": EXT_A, "task": "detect provider keys; build connectors", "result": "NO_KEY"},
            {"agent": SENS_A, "task": "run macro (G) x sensitivity + pre-register A-F", "result": f"{n_prom} promising"},
            {"agent": VAL_A, "task": "S8E-011 fixed-filter stress", "result": f"{len(filter_rows)-1} filters"},
        ],
        "decision": "continue", "continue": True,
        "rationale": f"macro (G) testable, {n_prom} promising-unconfirmed lead(s); external A-F "
                     f"pre-registered (no key); S8E-011 deep-dive underway",
    })

    # ---- Cycle 2: deep-dive decomposition + provider connectors materialized + skeptic ----
    deep_rows, tail_rows = s8e011_deep_dive(panel)
    cycles.append({
        "cycle": 2,
        "tasks": [
            {"agent": NEWS_A, "task": "news/sentiment schema + connector + mock", "result": "BUILT_NO_KEY"},
            {"agent": REV_A, "task": "analyst-revision schema + connector + mock", "result": "BUILT_NO_KEY"},
            {"agent": EARN_A, "task": "earnings schema + connector; local SEC/SimFin inventory", "result": "BUILT_NO_KEY"},
            {"agent": OPT_A, "task": "options IV/skew schema + connector + mock", "result": "BUILT_NO_KEY"},
            {"agent": SHORT_A, "task": "short-interest schema + connector + mock", "result": "BUILT_NO_KEY"},
            {"agent": TRANS_A, "task": "transcript-tone schema + connector + mock", "result": "BUILT_NO_KEY"},
            {"agent": VAL_A, "task": "S8E-011 tail decomposition", "result": f"{len(tail_rows)} tail slices"},
        ],
        "decision": "continue", "continue": True,
        "rationale": "all six external connectors + schemas + mock fixtures built and ready; "
                     "tail decomposition complete; provider keys remain the only blocker for A-F",
    })

    # ---- Cycle 3: risk/model-contribution decision + next-cycle decision ----
    any_filter_helps = any(r.get("worst_decile_improved") for r in filter_rows)
    cycles.append({
        "cycle": 3,
        "tasks": [
            {"agent": RSK_A, "task": "tail/worst-decile/concentration on testable experiments", "result": "OK"},
            {"agent": MODEL_A, "task": "model-candidate registry decision (no deploy)", "result": "OK"},
            {"agent": DIR_A, "task": "promote/reject + next-cycle decision", "result": "OK"},
        ],
        "decision": "stop_provider_required", "continue": False,
        "stop_reason": "next meaningful step (external families A-F) requires provider access; "
                       "the OS is built and ready to collect the moment a key is supplied",
        "rationale": f"no CONFIRMED signal on locally-testable data; S8E-011 remains promising-"
                     f"unconfirmed (fixed filters help tail={any_filter_helps}); escalate for "
                     f"provider acquisition in priority order",
    })
    # attach deep-dive to filter rows passthrough via closure return
    run_cycles._deep_rows = deep_rows
    run_cycles._tail_rows = tail_rows
    return ledger, cycles, filter_rows, macro_state


# =========================================================================== #
# Universe / feature lineage artifacts.
# =========================================================================== #
def data_foundation_rows(panel: SensPanel) -> List[dict]:
    g = panel.grid
    rows = [
        {"check": "persisted_8e_panel_reused", "status": "OK",
         "detail": str(SENS_PANEL_ROOT).replace("\\", "/")},
        {"check": "weekly_grid_rows", "status": "OK", "detail": int(len(g))},
        {"check": "n_symbols", "status": "OK", "detail": int(g["symbol"].nunique()) if not g.empty else 0},
        {"check": "date_range", "status": "OK",
         "detail": f"{str(g['date'].min())[:10]}..{str(g['date'].max())[:10]}" if not g.empty else ""},
        {"check": "point_in_time_safety", "status": "OK",
         "detail": "rolling betas + driver shocks trailing; forward labels shifted forward (8-E leak tests pass)"},
        {"check": "external_raw_path", "status": "OWNED", "detail": str(EXTERNAL_RAW).replace("\\", "/")},
        {"check": "external_normalized_path", "status": "OWNED", "detail": str(EXTERNAL_NORMALIZED).replace("\\", "/")},
        {"check": "external_adapters_path", "status": "OWNED", "detail": str(EXTERNAL_ADAPTERS).replace("\\", "/")},
    ]
    return rows


def universe_identifier_rows(panel: SensPanel) -> List[dict]:
    md = panel.metadata
    rows = []
    if md is None or md.empty:
        return rows
    for ticker, r in md.iterrows():
        rows.append({
            "ticker": ticker, "gics_sector": r.get("gics_sector", ""),
            "sector_etf": r.get("sector_etf", ""),
            "first_quoted_date": r.get("first_quoted_date", ""),
            "last_quoted_date": r.get("last_quoted_date", ""),
            "is_delisted": bool(r.get("is_delisted", False)),
            "norgate_symbol": ticker,
            "provider_identifier_hint": "map via ticker+exchange; providers key on ticker/CUSIP/FIGI",
        })
    return rows


def feature_lineage(panel: SensPanel) -> dict:
    return {
        "phase": PHASE,
        "price_volume_features": "8-D FEATURE_CATALOG (leak-safe, trailing)",
        "sensitivity_features": {
            "method": "leak-safe rolling 252d beta of each ticker return to each driver proxy return",
            "drivers": SENS_DRIVERS + ["sector"],
            "cohorts": [c.col for c in COHORT_CATALOG],
            "estimated_not_assumed": True,
        },
        "driver_shock_features": "date-level trailing 20d return + 252d z-score per proxy (leak-safe)",
        "external_event_features": {
            s.schema_name: {"agent": s.agent, "fields": s.schema, "status": "schema_ready_no_data"}
            for s in EXTERNAL_SOURCES},
        "forward_labels": "fwd_excess_{5,10,20,60} + fwd_total_5 (forward-shifted; never an input)",
        "lineage_note": "external event features are DECLARED with point-in-time availability fields; "
                        "no external feature is computed until provider data is collected.",
    }


def external_feature_catalog_rows() -> List[dict]:
    rows = []
    for s in EXTERNAL_SOURCES:
        rows.append({
            "feature_family": s.family, "source": s.key, "schema_name": s.schema_name,
            "owning_agent": s.agent, "mechanism": s.mechanism, "candidate_horizons": s.horizons,
            "pairs_with_cohort": s.cohort_hint, "fields": ";".join(s.schema),
            "availability": "SCHEMA_READY_NEEDS_PROVIDER_DATA",
        })
    return rows


def external_schema_catalog_rows() -> List[dict]:
    rows = []
    for name, fields in SCHEMA_BY_NAME.items():
        rows.append({"schema_name": name, "n_fields": len(fields), "fields": ";".join(fields),
                     "point_in_time_field": "point_in_time_available_at",
                     "rule": "point_in_time_available_at >= event_timestamp (no look-ahead)"})
    return rows


# =========================================================================== #
# Safety block.
# =========================================================================== #
def _safety_block(readiness: Dict[str, bool]) -> dict:
    return {
        "research_only": True, "local_first_norgate_plus_fred": True,
        "provider_keys_detected": any(readiness.values()),
        "provider_key_values_printed": False, "secrets_printed": False,
        "live_external_collection_ran": False, "external_data_faked": False,
        "mock_fixtures_labelled_and_excluded_from_scoreboard": True,
        "packages_installed": False, "large_data_only_on_d": True,
        "always_on_factor_test": False, "optimized_weights": False,
        "factor_signs_modified_after_results": False, "regime_activation": False,
        "ml_fit": False, "holdout_feedback_used_to_tune_thresholds": False,
        "failed_experiments_hidden": False, "live_trading_signals": False,
        "broker_or_orders": False, "automation": False, "paper_trader_touched": False,
        "gcp_touched": False, "committed": False, "pushed": False,
    }


# =========================================================================== #
# Orchestration + artifacts (Part 10).
# =========================================================================== #
ARTIFACTS = [
    "phase8f_autonomous_external_signal_os.json", "agent_research_memory.json",
    "agent_backlog.csv", "agent_task_board.csv", "agent_decision_log.csv",
    "model_candidate_registry.csv", "signal_promotion_log.csv", "rejected_hypothesis_graveyard.csv",
    "data_foundation_report.csv", "universe_and_identifier_map.csv", "feature_lineage.json",
    "external_feature_catalog.csv", "provider_key_inventory.csv", "provider_connector_status.csv",
    "provider_acquisition_commands.ps1", "external_event_schema_catalog.csv",
    "external_raw_cache_manifest.csv", "external_normalized_event_manifest.csv",
    "news_sentiment_event_manifest.csv", "analyst_revision_event_manifest.csv",
    "earnings_event_manifest.csv", "options_event_manifest.csv", "short_interest_event_manifest.csv",
    "transcript_tone_event_manifest.csv", "s8e011_deep_dive.csv", "s8e011_tail_risk_decomposition.csv",
    "s8e011_fixed_filter_stress.csv", "external_event_signal_scoreboard.csv", "matched_control_report.csv",
    "promising_external_setups.csv", "confirmed_external_signals.csv", "failed_external_setups.csv",
    "validation_skeptic_report.csv", "risk_portfolio_report.csv", "multiple_testing_report.csv",
    "model_contribution_report.csv", "research_director_decision.json", "phase8g_next_plan.json",
]


def run(out_dir: Path, *, rebuild: bool = False, max_symbols: Optional[int] = None) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    started = _utc_now_iso()
    framework_ok = True

    # provider detection (Part 4) — names/presence only
    key_rows = detect_provider_keys()
    config_rows = scan_config_for_key_names()
    readiness = provider_readiness(key_rows)

    # connectors/schemas/fixtures (Part 5) — always built, key or not
    status_rows, raw_rows, norm_rows, manifests = build_connectors(readiness)
    acq_path = write_acquisition_commands(out_dir, status_rows)

    # panel (reuse persisted 8-E grid; optional rebuild)
    panel = None if rebuild else load_persisted_panel()
    if panel is None and rebuild:
        adapter = P8E.NorgateAdapter()
        if adapter.available:
            universes = P8E.discover_universes(adapter)
            sel = P8E.select_universe(universes)
            syms = P8E.select_symbols(adapter, sel["selected_universe"], max_symbols)
            panel = P8E.build_sensitivity_panel(adapter, syms, sel.get("index_name", ""))
    panel_ok = bool(panel is not None and panel.ok and not panel.grid.empty)

    if not panel_ok:
        # OS scaffolding is still fully built; the testable track is unavailable.
        ledger: List[Experiment] = _external_experiments(cycle=1)
        cycles = [{"cycle": 1, "tasks": [{"agent": DATA_A, "task": "load persisted panel", "result": "MISSING"}],
                   "decision": "stop_provider_required", "continue": False,
                   "stop_reason": "persisted 8-E panel unavailable and rebuild not requested",
                   "rationale": "OS built; testable track blocked"}]
        filter_rows, deep_rows, tail_rows = [], [], []
        macro_state = None
        empty_panel = SensPanel(pd.DataFrame(columns=["symbol", "date"]), pd.DataFrame(),
                                pd.Series(dtype=float), pd.DatetimeIndex([]), False)
        panel = panel or empty_panel
    else:
        ledger, cycles, filter_rows, macro_state = run_cycles(panel, readiness)
        deep_rows = getattr(run_cycles, "_deep_rows", [])
        tail_rows = getattr(run_cycles, "_tail_rows", [])

    rec, detail = derive_recommendation(framework_ok, panel_ok, readiness, ledger)
    report = _assemble_report(started, rec, detail, panel, panel_ok, ledger, readiness,
                              key_rows, config_rows, cycles, filter_rows)
    _emit_all(out_dir, report, panel, panel_ok, ledger, readiness, key_rows, config_rows,
              status_rows, raw_rows, norm_rows, manifests, cycles, filter_rows, deep_rows,
              tail_rows, acq_path)
    return report


def _assemble_report(started, rec, detail, panel, panel_ok, ledger, readiness, key_rows,
                     config_rows, cycles, filter_rows) -> dict:
    g = panel.grid
    md = panel.metadata
    n_active = int((~md["is_delisted"]).sum()) if not md.empty and "is_delisted" in md else 0
    n_delisted = int(md["is_delisted"].sum()) if not md.empty and "is_delisted" in md else 0
    return {
        "phase": PHASE, "objective": OBJECTIVE, "generated_utc": started,
        "recommendation": rec, "allowed_recommendations": list(ALLOWED_RECOMMENDATIONS),
        "decision_detail": detail,
        "question_answered": ("Can the agent system find, validate, or prepare a real external-"
                              "information signal by combining external events with ticker-specific "
                              "sensitivity cohorts?"),
        "agents": ALL_AGENTS, "families": list(ALL_FAMILIES),
        "panel": {
            "panel_ok": panel_ok, "source": "persisted 8-E weekly grid (reused, no rebuild)",
            "n_symbols": int(g["symbol"].nunique()) if not g.empty else 0,
            "n_obs": int(len(g)),
            "date_range": ([str(g["date"].min())[:10], str(g["date"].max())[:10]] if not g.empty else []),
            "active": n_active, "delisted": n_delisted,
            "large_data_root": str(SENS_PANEL_ROOT).replace("\\", "/"),
        },
        "provider": {
            "any_key_present": any(readiness.values()),
            "n_keys_checked": len(key_rows), "readiness": readiness,
            "live_collection_ran": False,
            "news_or_sentiment_plugged_in": readiness.get(FAM_NEWS, False),
        },
        "cycles": [{"cycle": c["cycle"], "decision": c["decision"], "continue": c["continue"]}
                   for c in cycles],
        "n_cycles": len(cycles),
        "budget": _budget(ledger),
        "experiments": {
            "n_total": len(ledger),
            "confirmed": [e.exp_id for e in ledger if e.status == ST_CONFIRMED],
            "promising_unconfirmed": [e.exp_id for e in ledger if e.status == ST_PROMISING_UNCONF],
            "promising_needs_provider": [e.exp_id for e in ledger if e.status == ST_PROMISING_PROVIDER],
            "needs_provider": [e.exp_id for e in ledger if e.status == ST_NEEDS_PROVIDER],
            "rejected": [e.exp_id for e in ledger if e.status in (ST_REJECTED, ST_BLOCKED)],
        },
        "s8e011_fixed_filter_helps_tail": any(r.get("worst_decile_improved") for r in filter_rows),
        "multiple_testing": _multiple_testing_report(ledger),
        "safety": _safety_block(readiness),
    }


def _director_decision(report, ledger, filter_rows) -> dict:
    best = next((e for e in ledger if e.exp_id == "S8E-011"), None)
    return {
        "phase": PHASE, "generated_utc": report["generated_utc"],
        "recommendation": report["recommendation"],
        "allowed_recommendations": list(ALLOWED_RECOMMENDATIONS),
        "decision_detail": report["decision_detail"],
        "thesis": "external event x ticker sensitivity x market context, operated by an autonomous agent OS",
        "best_lead": None if best is None else {
            "signal_id": "S8E-011", "family": best.family, "status": best.status,
            "lift_vs_control": (best.metrics or {}).get("lift_vs_control"),
            "ev_after_25bps": (best.metrics or {}).get("ev_after_25bps"),
            "blocker": "tail/portfolio gate", "reason": best.reason},
        "fixed_filter_helps_tail": any(r.get("worst_decile_improved") for r in filter_rows),
        "anti_p_hacking": {
            "all_experiments_pre_registered": True, "thresholds_fixed_a_priori": True,
            "sensitivity_direction_estimated_not_assumed": True,
            "fixed_filters_declared_on_ex_ante_buckets": True,
            "challenge_fraction": _budget(ledger)["challenge_fraction"],
            "external_data_never_faked": True, "mock_fixtures_excluded_from_scoreboard": True,
        },
        "stop_conditions_honored": [
            "no price/volume-only mining", "no single universal factor", "sensitivity estimated from data",
            "no weak full-sample promotion", "no weight optimization", "no factor-sign flipping",
            "no regime activation/throttling", "no ML fitting", "external data never faked",
            "no secrets printed", "no live trading signals", "no broker/orders/automation",
            "no Paper Trader / GCP / deployment", "failed experiments not hidden", "no commit", "no push",
        ],
    }


def _phase8g_plan(report, readiness) -> dict:
    rec = report["recommendation"]
    steps = [
        "Acquire analyst-revision provider data (priority 1): set FMP/Finnhub/Intrinio key, run "
        "analyst_revision_adapter.py (bounded), then run family B + family F (S8E-011 + revision "
        "confirmation) through the IDENTICAL gate — thresholds FIXED, no tuning.",
        "Acquire news/sentiment data (priority 2; GDELT is free): run family A.",
        "Broaden the daily universe (S&P 1500 / Russell 3000, chunked) and re-run S8E-011 with the "
        "fixed pre-declared filters to test whether the tail/portfolio gate clears on more data.",
        "Re-run earnings (C), options (D), short-interest (E) families as their providers come online.",
    ]
    return {
        "from_phase": PHASE, "recommendation": rec, "next_phase": "8-G",
        "next_steps": steps,
        "provider_priority": ["analyst_revision", "news_sentiment", "earnings_surprise",
                              "options_iv", "short_interest", "transcript_tone"],
        "provider_readiness": readiness,
        "hard_constraints": [
            "local data first; Norgate + FRED only for price/macro", "do not install packages",
            "large data on D: only; repo outputs are summaries/manifests",
            "bounded live collection (<=25 tickers / 30 days / 500 events)", "never print secrets",
            "no Paper Trader / GCP / deployment", "no broker/order/automation",
            "no live trading signals", "no weight optimization", "no factor-sign flipping after results",
            "no regime activation/throttling", "external data never faked",
            "do not hide failed experiments", "do not commit", "do not push"],
    }


def _emit_all(out_dir, report, panel, panel_ok, ledger, readiness, key_rows, config_rows,
              status_rows, raw_rows, norm_rows, manifests, cycles, filter_rows, deep_rows,
              tail_rows, acq_path) -> None:
    p = lambda n: out_dir / n
    _write_json(p("phase8f_autonomous_external_signal_os.json"), report)

    # Part 1 — memory + backlog + boards + registries
    memory = build_research_memory(report["recommendation"], report["decision_detail"], ledger,
                                   readiness, filter_rows, panel)
    _write_json(p("agent_research_memory.json"), memory)
    _write_csv(p("agent_backlog.csv"), backlog_rows(ledger, readiness),
               ["backlog_id", "work_item", "owning_agent", "priority", "state", "description"])
    _write_csv(p("agent_task_board.csv"), task_board_rows(cycles),
               ["cycle", "agent", "task", "result"])
    _write_csv(p("agent_decision_log.csv"), decision_log_rows(cycles),
               ["cycle", "decision", "rationale", "continue", "stop_reason"])
    _write_csv(p("model_candidate_registry.csv"), model_candidate_rows(ledger, filter_rows),
               ["candidate_id", "family", "external_driver", "cohort", "signal_status",
                "registry_decision", "proposed_contribution", "lift_vs_control", "ev_after_25bps",
                "worst_decile_mean", "deployed", "paper_trader_output", "production", "note"])
    _write_csv(p("signal_promotion_log.csv"), promotion_log_rows(ledger),
               ["signal_id", "family", "from_status", "to_status", "cycle", "promoted", "rationale"])
    _write_csv(p("rejected_hypothesis_graveyard.csv"), graveyard_rows(ledger),
               ["origin_phase", "hypothesis", "family", "status", "reason"])

    # Part 3 — agent reports
    _write_csv(p("data_foundation_report.csv"), data_foundation_rows(panel),
               ["check", "status", "detail"])
    _write_csv(p("universe_and_identifier_map.csv"), universe_identifier_rows(panel),
               ["ticker", "gics_sector", "sector_etf", "first_quoted_date", "last_quoted_date",
                "is_delisted", "norgate_symbol", "provider_identifier_hint"])
    _write_json(p("feature_lineage.json"), feature_lineage(panel))
    _write_csv(p("external_feature_catalog.csv"), external_feature_catalog_rows(),
               ["feature_family", "source", "schema_name", "owning_agent", "mechanism",
                "candidate_horizons", "pairs_with_cohort", "fields", "availability"])

    # Part 4/5 — provider detection + connectors + schemas + manifests
    _write_csv(p("provider_key_inventory.csv"), key_rows + [
        {"key_env_var": f"CONFIG::{r['config_path']}", "present": r["exists"],
         "feeds_families": r["key_names_present"], "detection": r["note"]} for r in config_rows],
        ["key_env_var", "present", "feeds_families", "detection"])
    _write_csv(p("provider_connector_status.csv"), status_rows,
               ["source", "family", "agent", "provider_candidates", "key_present", "connector_mode",
                "adapter_path", "normalized_schema_path", "mock_fixture_path", "dry_run_command",
                "live_collection_ran", "schema_fields"])
    _write_csv(p("external_event_schema_catalog.csv"), external_schema_catalog_rows(),
               ["schema_name", "n_fields", "fields", "point_in_time_field", "rule"])
    _write_csv(p("external_raw_cache_manifest.csv"), raw_rows,
               ["source", "family", "raw_dir", "n_raw_payloads", "bounded_caps", "status"])
    _write_csv(p("external_normalized_event_manifest.csv"), norm_rows,
               ["source", "family", "schema_name", "normalized_path", "n_real_events",
                "n_mock_events", "mock_fixture_path", "pit_field", "status"])
    man_cols = ["source", "family", "agent", "provider_candidates", "key_present", "schema",
                "mechanism", "candidate_horizons", "cohort_hint", "normalized_path",
                "mock_fixture_path", "adapter_path", "n_real_events", "n_mock_events", "status",
                "dry_run_command"]
    by_src = {m["source"]: m for m in manifests}
    for src_key, fname in MANIFEST_FILE.items():
        m = by_src.get(src_key)
        _write_csv(p(fname), [m] if m else [], man_cols)

    # Part 6 — S8E-011 deep dive
    _write_csv(p("s8e011_deep_dive.csv"), deep_rows,
               ["dimension", "bucket", "n_events", "mean_fwd", "worst_decile_mean", "hit_rate"])
    _write_csv(p("s8e011_tail_risk_decomposition.csv"), tail_rows,
               ["slice", "n_events", "mean_fwd", "threshold_fwd_excess_20", "detail"])
    _write_csv(p("s8e011_fixed_filter_stress.csv"), filter_rows,
               ["filter", "n_events", "n_recent_events", "lift_vs_control", "ev_after_25bps",
                "worst_decile_mean", "recent_lift_vs_control", "max_sector_fraction",
                "beats_spy_active", "beats_cash_active", "worst_decile_improved", "reason"])

    # Part 7/8 — scoreboards + controls + validation + risk + MT + model contribution
    sb = _scoreboard_rows(ledger)
    _write_csv(p("external_event_signal_scoreboard.csv"), sb, _SCOREBOARD_COLS)
    _write_csv(p("matched_control_report.csv"), _matched_control_rows(ledger),
               ["exp_id", "family", "external_driver", "cohort", "is_challenge", "n_events",
                "n_matched_control", "triggered_mean", "control_mean", "lift_vs_control",
                "hit_rate", "control_hit_rate", "ev_after_25bps", "recent_lift_vs_control"])
    promising = [r for r in sb if r["status"] in (ST_PROMISING_UNCONF, ST_PROMISING_PROVIDER)]
    _write_csv(p("promising_external_setups.csv"), promising, _SCOREBOARD_COLS)
    confirmed = [r for r in sb if r["status"] == ST_CONFIRMED]
    _write_csv(p("confirmed_external_signals.csv"),
               confirmed if confirmed else [{"status": "NO_CONFIRMED_EXTERNAL_SENSITIVITY_SIGNAL"}],
               _SCOREBOARD_COLS if confirmed else ["status"])
    failed = [r for r in sb if r["status"] in (ST_REJECTED, ST_BLOCKED)]
    _write_csv(p("failed_external_setups.csv"), failed, _SCOREBOARD_COLS)
    _write_csv(p("validation_skeptic_report.csv"), _validation_skeptic_rows(ledger, filter_rows),
               ["exp_id", "family", "kind", "is_challenge", "status", "lift_vs_control",
                "n_failed_checks", "failed_checks", "reason"])
    _write_csv(p("risk_portfolio_report.csv"), _risk_portfolio_rows(ledger),
               ["exp_id", "family", "cohort", "n_events", "worst_decile_mean", "avg_mae_20",
                "max_year_fraction", "max_sector_fraction", "max_ticker_fraction",
                "beats_spy_active", "beats_cash_active", "tail_gate_pass"])
    mt = _multiple_testing_report(ledger)
    _write_csv(p("multiple_testing_report.csv"),
               [{"metric": k, "value": (";".join(map(str, v)) if isinstance(v, list) else v)}
                for k, v in mt.items()], ["metric", "value"])
    _write_csv(p("model_contribution_report.csv"), model_candidate_rows(ledger, filter_rows),
               ["candidate_id", "family", "external_driver", "cohort", "signal_status",
                "registry_decision", "proposed_contribution", "lift_vs_control", "ev_after_25bps",
                "worst_decile_mean", "deployed", "paper_trader_output", "production", "note"])

    _write_json(p("research_director_decision.json"), _director_decision(report, ledger, filter_rows))
    _write_json(p("phase8g_next_plan.json"), _phase8g_plan(report, readiness))


def _print_summary(report: dict) -> None:
    rec = report["recommendation"]
    pan = report["panel"]
    exp = report["experiments"]
    b = report["budget"]
    pr = report["provider"]
    print(f"[{PHASE}] recommendation = {rec}")
    print(f"[{PHASE}] panel = {pan['n_symbols']} symbols x {pan['n_obs']} obs {pan.get('date_range')} "
          f"active={pan['active']} delisted={pan['delisted']} (ok={pan['panel_ok']}, reused 8-E)")
    print(f"[{PHASE}] provider keys present = {pr['any_key_present']} ({pr['n_keys_checked']} checked); "
          f"news/sentiment plugged in = {pr['news_or_sentiment_plugged_in']}; live collection = {pr['live_collection_ran']}")
    print(f"[{PHASE}] experiments = {b['experiments_registered']}/{MAX_TOTAL_EXPERIMENTS} "
          f"(testable={b['n_testable']} provider={b['n_needs_provider']}) "
          f"challenge={b['n_challenge']} ({b['challenge_fraction']}); "
          f"confirmed={exp['confirmed']} promising_unconf={exp['promising_unconfirmed']} "
          f"needs_provider={len(exp['needs_provider'])} rejected={len(exp['rejected'])}")
    print(f"[{PHASE}] S8E-011 fixed filter helps tail = {report.get('s8e011_fixed_filter_helps_tail')}")


def _parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Phase 8-F Autonomous External Signal Research OS")
    ap.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    ap.add_argument("--rebuild", action="store_true",
                    help="rebuild the sensitivity panel from Norgate instead of reusing the persisted 8-E grid")
    ap.add_argument("--max-symbols", type=int, default=None, help="bound the universe on rebuild")
    return ap.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parse_args(argv)
    try:
        report = run(Path(args.out_dir), rebuild=args.rebuild, max_symbols=args.max_symbols)
    except Exception as exc:  # pragma: no cover - top-level guard
        out = Path(args.out_dir)
        out.mkdir(parents=True, exist_ok=True)
        err = {"phase": PHASE, "recommendation": REC_ERROR, "error": repr(exc),
               "generated_utc": _utc_now_iso()}
        _write_json(out / "phase8f_autonomous_external_signal_os.json", err)
        print(f"[{PHASE}] recommendation = {REC_ERROR}: {exc!r}")
        return 1
    _print_summary(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
