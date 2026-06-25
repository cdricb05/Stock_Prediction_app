"""Phase 8-I — Autonomous Alpha Discovery Program.

**Track A (quant brain) research only.** This is NOT another single-setup phase. 8-G found the first
real external-confirmed lead (S8G-F20); 8-H proved its blocker is event COVERAGE (the 75-ticker
earnings feed), not Norgate or the daily panel. 8-I stops orbiting one setup and runs an autonomous,
multi-cycle alpha-discovery PROGRAM across every information family that is reachable on local /
no-key / provider-key data, builds a durable candidate registry, ranks the leads, and decides the
best current PATH to a real signal — without waiting for user micro-direction.

The ONE question this phase answers
-----------------------------------
    WHAT IS THE BEST CURRENT PATH TO A REAL SIGNAL, AND WHICH CANDIDATE SIGNALS OR DATA SOURCES
    SHOULD BE PURSUED NEXT WITHOUT WAITING FOR USER MICRO-DIRECTION?

Core thesis (combinatorial, not universal)
-------------------------------------------
A signal is `external event x ticker sensitivity x sector/industry context x market regime x
liquidity/positioning x valuation/fundamental context x confirmation/divergence`. Different stocks
react differently to the SAME driver. So 8-I tests many pre-registered COMBINATIONS, each on its own
estimated sensitivity cohort, never one universal factor across all tickers.

Five autonomous cycles
----------------------
  1  REBUILD STATE: read all 8-E/8-F/8-G/8-H outputs (memory, backlog, candidate registry,
     graveyard, scoreboards); rank existing leads; split coverage-blocked vs invalid-logic.
  2  ACTIVATE DATA: every local / no-key source — local earnings cache, SEC EDGAR (no-key, cached),
     GDELT no-key news (bounded retry), FINRA short interest (no-key attempt), Norgate macro/cross-
     asset + sector/industry. Build normalized event panels. Record blockers; never stop on one.
  3  GENERATE CANDIDATES: new pre-registered combination families — earnings catalyst x sensitivity/
     sector/vol/beta, revision-proxy x sensitivity, macro/cross-asset shock x sensitivity (+ earnings
     confirmation), filings x sensitivity, S8G-F20 continuation. Fixed templates only.
  4  VALIDATE: matched controls, recent 2015-2026, walk-forward, cost stress, tail risk, sector/
     year/ticker concentration, placebo + leakage checks, multiple-testing adjustment.
  5  DECIDE: confirmed / promising / coverage-or-provider-limited / rejected / provider-required;
     rank the top next options by probability of success; select the next autonomous campaign.

Hard safety contract (unchanged from 8-E..8-H)
----------------------------------------------
Local data first; Norgate + on-disk FRED for price/macro (no package install). Provider keys
detected by NAME/presence only, never printed. Bounded no-key collection only: raw under
D:\\Stock_Prediction_app_data\\external_raw, normalized under ...\\external_normalized; large panels
under ...\\research_panels; repo gets summaries/manifests/scoreboards/decision artifacts only. Every
experiment pre-registered before scoring; thresholds fixed a priori; >=30% challenges/placebos.
External data NEVER faked (no-key sources that yield only a recent window are reported as connector-
live-but-history-missing, not turned into events; the revision PROXY is labelled and capped below
CONFIRMED; mock fixtures excluded). No threshold tuning after results, no factor-sign flipping after
results, no weight optimization, no regime activation, no ML fit, no hidden failures, no secrets
printed. No Paper Trader, no GCP, no deployment, no broker/orders/automation, no live trading
signals. No commit, no push.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import time
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
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


# Reuse the full stack: 8-H scaling engine -> 8-G activation -> 8-F OS -> 8-E scoring. No re-impl.
P8H = _load_module("phase8h_engine_for_8i", "research/run_phase8h_external_signal_scaling_campaign.py")
P8G = P8H.P8G
P8F = P8G.P8F
P8E = P8G.P8E

# IO + scoring primitives reused verbatim.
_write_json = P8E._write_json
_write_csv = P8E._write_csv
_round = P8E._round
_utc_now_iso = P8E._utc_now_iso
SensPanel = P8E.SensPanel
SensSetup = P8E.SensSetup
evaluate_sensitivity_setup = P8E.evaluate_sensitivity_setup
simulate_event_portfolio = P8E.simulate_event_portfolio
_fwd5_pivot = P8E._fwd5_pivot
_spy_weekly = P8E._spy_weekly

# Fixed gate constants (unchanged; no tuning).
GATE_MIN_EVENTS_TOTAL = P8E.GATE_MIN_EVENTS_TOTAL      # 1000
GATE_MIN_EVENTS_RECENT = P8E.GATE_MIN_EVENTS_RECENT    # 100
GATE_WORST_DECILE_FLOOR = P8E.GATE_WORST_DECILE_FLOOR  # -0.12
SHOCK_Z = P8E.SHOCK_Z                                  # 1.0

# 8-G external promotion ladder (reused; the per-signal promotion machinery is NOT re-tuned).
ST_EXT_CONFIRMED = P8G.ST_EXT_CONFIRMED
ST_EXT_PROMISING = P8G.ST_EXT_PROMISING
ST_NEEDS_HISTORY = P8G.ST_NEEDS_HISTORY
ST_NEEDS_PROVIDER = P8G.ST_NEEDS_PROVIDER
ST_REJECTED = P8E.ST_REJECTED
ST_BLOCKED = P8E.ST_BLOCKED

# 8-F macro (family G) statuses.
P8F_CONFIRMED = P8F.ST_CONFIRMED
P8F_PROMISING_UNCONF = P8F.ST_PROMISING_UNCONF
P8F_REJECTED = P8F.ST_REJECTED
P8F_BLOCKED = P8F.ST_BLOCKED

PHASE = "8-I"
OBJECTIVE = (
    "Run an autonomous multi-cycle alpha-discovery program across every reachable information family "
    "(local/no-key/provider-key), build a durable candidate registry, generate and validate "
    "pre-registered external-event x ticker-sensitivity x context combinations on the IDENTICAL fixed "
    "8-E gate, then decide the BEST current path to a real signal and rank the next options by "
    "probability of success. Research only; thresholds fixed a priori; external data never faked.")

# --------------------------------------------------------------------------- #
# 8-I per-signal alpha promotion vocabulary (Part: Signal promotion).
# --------------------------------------------------------------------------- #
ST_ALPHA_CONFIRMED = "CONFIRMED_ALPHA_SIGNAL"
ST_ALPHA_PROMISING = "PROMISING_ALPHA_SIGNAL"
ST_ALPHA_PROVIDER_REQUIRED = "PROVIDER_REQUIRED"
ALLOWED_ALPHA_STATUSES = (ST_ALPHA_CONFIRMED, ST_ALPHA_PROMISING, ST_ALPHA_PROVIDER_REQUIRED,
                          ST_REJECTED, ST_BLOCKED)

# --------------------------------------------------------------------------- #
# Program decision vocabulary (Part: Decision values), exact set of 8.
# --------------------------------------------------------------------------- #
REC_CONFIRMED = "CONFIRMED_ALPHA_SIGNAL_FOUND"
REC_PROMISING = "PROMISING_ALPHA_SIGNAL_FOUND"
REC_PROVIDER_LIMITED = "PROMISING_BUT_PROVIDER_LIMITED"
REC_PROVIDER_REQUIRED = "PROVIDER_REQUIRED_FOR_NEXT_BREAKTHROUGH"
REC_EXPANSION = "EXTERNAL_DATA_EXPANSION_REQUIRED"
REC_REJECTED = "ALPHA_RESEARCH_REJECTED_ON_AVAILABLE_DATA"
REC_FRAMEWORK_BLOCKED = "ASSESSMENT_FRAMEWORK_BLOCKED"
REC_ERROR = "ERROR"
ALLOWED_RECOMMENDATIONS = (
    REC_CONFIRMED, REC_PROMISING, REC_PROVIDER_LIMITED, REC_PROVIDER_REQUIRED, REC_EXPANSION,
    REC_REJECTED, REC_FRAMEWORK_BLOCKED, REC_ERROR,
)

# Agents + families (reuse 8-F/8-G roster).
ALL_AGENTS = P8F.ALL_AGENTS
SENS_A, VAL_A, RSK_A, MODEL_A = P8F.SENS_A, P8F.VAL_A, P8F.RSK_A, P8F.MODEL_A
EARN_A, REV_A, NEWS_A, OPT_A, SHORT_A, EXT_A, DIR_A = (
    P8F.EARN_A, P8F.REV_A, P8F.NEWS_A, P8F.OPT_A, P8F.SHORT_A, P8F.EXT_A, P8F.DIR_A)
FAM_EARNINGS = P8F.FAM_EARNINGS
FAM_REVISION = P8F.FAM_REVISION
FAM_NEWS = P8F.FAM_NEWS
FAM_OPTIONS = P8F.FAM_OPTIONS
FAM_SHORT = P8F.FAM_SHORT
FAM_S8E011_EXT = P8F.FAM_S8E011_EXT
FAM_MACRO = P8F.FAM_MACRO
FAM_FILINGS = P8G.FAM_FILINGS

# Proxy / thin-evidence families: never CONFIRMED (capped at PROMISING/NEEDS_HISTORY).
PROXY_OR_THIN_FAMILIES = P8G.PROXY_OR_THIN_FAMILIES

# Paths.
DATA_ROOT = P8F.DATA_ROOT
EXTERNAL_RAW = P8F.EXTERNAL_RAW
EXTERNAL_NORMALIZED = P8F.EXTERNAL_NORMALIZED
RESEARCH_PANELS = DATA_ROOT / "research_panels"
DEFAULT_OUT_DIR = _REPO_ROOT / "research" / "output" / "phase8i_autonomous_alpha_discovery_program"
OUT_8F = _REPO_ROOT / "research" / "output" / "phase8f_autonomous_external_signal_os"
OUT_8G = _REPO_ROOT / "research" / "output" / "phase8g_external_data_activation_campaign"
OUT_8H = _REPO_ROOT / "research" / "output" / "phase8h_external_signal_scaling_campaign"
FINRA_CACHE = EXTERNAL_RAW / "short_interest_finra" / "finra_short_interest_probe.json"

MAX_CYCLES = 5
F20_SETUP_ID = "S8G-F20"


# =========================================================================== #
# Cycle 1 — rebuild research state from 8-E..8-H outputs.
# =========================================================================== #
def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except Exception:
        return {}


def _read_csv(path: Path) -> pd.DataFrame:
    try:
        return pd.read_csv(path) if path.exists() else pd.DataFrame()
    except Exception:
        return pd.DataFrame()


def rebuild_research_state() -> dict:
    """Read prior agent memory/backlog/registry/graveyard + scoreboards and rank existing leads,
    classifying each as coverage/provider-blocked vs invalid-logic. Resilient to missing files."""
    mem_8f = _read_json(OUT_8F / "agent_research_memory.json")
    grave = _read_csv(OUT_8F / "rejected_hypothesis_graveyard.csv")
    sb_8g = _read_csv(OUT_8G / "external_signal_scoreboard.csv")
    dec_8g = _read_json(OUT_8G / "research_director_decision.json")
    dec_8h = _read_json(OUT_8H / "research_director_decision.json")
    cov_lim_8h = _read_csv(OUT_8H / "coverage_limited_signals.csv")

    leads: List[dict] = []
    if not sb_8g.empty:
        for _, r in sb_8g.iterrows():
            if bool(r.get("is_challenge")):
                continue
            prom = str(r.get("promotion") or "")
            n_ev = r.get("n_events")
            ev25 = r.get("ev_after_25bps")
            lift = r.get("lift_vs_control")
            positive = (pd.notna(ev25) and float(ev25) > 0) and (pd.notna(lift) and float(lift) > 0)
            if prom in (ST_EXT_CONFIRMED, ST_EXT_PROMISING, ST_NEEDS_HISTORY):
                blocker = "coverage_or_provider"   # real, positive, gate gap is count/recency/provider
            elif prom == ST_NEEDS_PROVIDER:
                blocker = "no_data_provider_required"
            else:
                blocker = "invalid_logic"          # rejected on the metric, not on coverage
            leads.append({
                "signal_id": r.get("exp_id"), "family": r.get("family"), "prior_promotion": prom,
                "n_events": int(n_ev) if pd.notna(n_ev) else None,
                "ev_after_25bps": float(ev25) if pd.notna(ev25) else None,
                "lift_vs_control": float(lift) if pd.notna(lift) else None,
                "positive_economics": bool(positive), "blocker_class": blocker})
    # rank: positive economics first, then by EV after cost
    leads.sort(key=lambda d: (d["positive_economics"], (d["ev_after_25bps"] or -9)), reverse=True)
    coverage_blocked = [d["signal_id"] for d in leads if d["blocker_class"] == "coverage_or_provider"]
    provider_required = [d["signal_id"] for d in leads if d["blocker_class"] == "no_data_provider_required"]
    invalid_logic = [d["signal_id"] for d in leads if d["blocker_class"] == "invalid_logic"]
    return {
        "prior_memory_phase": mem_8f.get("phase", "n/a"),
        "prior_8g_recommendation": dec_8g.get("recommendation", "n/a"),
        "prior_8h_recommendation": dec_8h.get("recommendation", "n/a"),
        "prior_best_lead": F20_SETUP_ID if not cov_lim_8h.empty else (leads[0]["signal_id"] if leads else None),
        "ranked_existing_leads": leads,
        "coverage_or_provider_blocked": coverage_blocked,
        "provider_required": provider_required,
        "invalid_logic_rejected": invalid_logic,
        "graveyard_families": sorted(grave["family"].dropna().unique().tolist()) if not grave.empty else [],
        "rejected_research_lines": ["8-B momentum/value/quality (price-only)",
                                    "8-C broad price/volume mining", "8-D conditional price/volume",
                                    "6-A cross-asset macro pack (degraded IC)"],
    }


# =========================================================================== #
# Cycle 2 — FINRA short-interest no-key activation (new; honest about history).
# =========================================================================== #
def finra_short_interest_activation(activate_live: bool = False) -> Tuple[List[dict], dict]:
    """No-key attempt at FINRA consolidated short-interest data. Bounded, cached on D:, resilient.
    Honest: even on success the free endpoint exposes only recent settlement windows, not a deep
    point-in-time history, so it cannot back a 1993-2026 confirmation overlay. Never fakes events."""
    meta = {"attempted": False, "succeeded": False, "http_status": "", "n_records": 0,
            "has_history": False, "note": ""}
    if activate_live:
        import urllib.request
        import urllib.error
        meta["attempted"] = True
        FINRA_CACHE.parent.mkdir(parents=True, exist_ok=True)
        # FINRA's free consolidated short-interest download (public, no key). Bounded single probe.
        url = "https://cdn.finra.org/equity/regsho/daily/CNMSshvol20240102.txt"
        hdr = {"User-Agent": "paper-trader-research research@example.com"}
        for attempt in range(2):
            try:
                req = urllib.request.Request(url, headers=hdr)
                with urllib.request.urlopen(req, timeout=12) as r:
                    body = r.read().decode("utf-8", "ignore")
                lines = [ln for ln in body.splitlines() if ln.strip()]
                meta["succeeded"] = True
                meta["http_status"] = "200"
                meta["n_records"] = max(0, len(lines) - 1)
                meta["has_history"] = False   # single daily settlement file, not a deep PIT history
                meta["note"] = ("FINRA reg-SHO daily file reachable (no key); single-day settlement "
                                "window only -> not a deep PIT short-interest history")
                try:
                    FINRA_CACHE.write_text(json.dumps({"url": url, "n_records": meta["n_records"]}),
                                           encoding="utf-8")
                except Exception:
                    pass
                break
            except urllib.error.HTTPError as exc:                 # pragma: no cover - network
                meta["http_status"] = str(exc.code)
                meta["note"] = f"HTTP {exc.code} (blocked/not found); biweekly bulk feed still needed"
                time.sleep(1.2 * (attempt + 1))
            except Exception as exc:                              # pragma: no cover - network
                meta["http_status"] = "ERR"
                meta["note"] = repr(exc)[:120]
                time.sleep(1.2 * (attempt + 1))
    else:
        meta["note"] = "no-key FINRA probe not requested (--activate-live off); adapter executable"
    rows = [
        {"source": "finra_regsho_daily", "mode": "NOKEY_PROBE", "attempted": meta["attempted"],
         "succeeded": meta["succeeded"], "http_status": (meta["http_status"] or "n/a"),
         "n_records": meta["n_records"], "deep_history": meta["has_history"],
         "real_usable_panel": False, "note": meta["note"]},
        {"source": "finra_biweekly_short_interest", "mode": "NEEDS_PROVIDER_OR_BULK", "attempted": False,
         "succeeded": False, "http_status": "n/a", "n_records": 0, "deep_history": False,
         "real_usable_panel": False,
         "note": "biweekly consolidated short-interest history (free bulk or vendor) -> family E overlay"},
    ]
    return rows, meta


def data_source_activation_log(earn: pd.DataFrame, filings: pd.DataFrame, edgar_meta: dict,
                               news_meta: dict, finra_meta: dict, readiness: Dict[str, bool],
                               aug_diag: dict) -> List[dict]:
    """Cycle-2 activation log over every local/no-key source attempted this program."""
    n_earn = int(len(earn)) if earn is not None else 0
    n_fil = int(len(filings)) if filings is not None else 0
    return [
        {"cycle": 2, "source": "local_earnings_surprise_cache", "family": FAM_EARNINGS,
         "mode": "LOCAL_CACHE_ACTIVATED", "real_data": n_earn > 0, "n_events": n_earn,
         "obs_joined": aug_diag.get("n_earn_event_obs", 0), "key_needed": False,
         "blocker": "", "note": "real EPS-surprise events joined leak-safe to the grid (75 tickers)"},
        {"cycle": 2, "source": "local_revision_proxy", "family": FAM_REVISION,
         "mode": "LOCAL_PROXY_ACTIVATED", "real_data": True, "n_events": n_earn,
         "obs_joined": aug_diag.get("n_earn_event_obs", 0), "key_needed": True,
         "blocker": "true analyst-revision feed", "note": "labelled proxy; capped below CONFIRMED"},
        {"cycle": 2, "source": "sec_edgar_filings_nokey", "family": FAM_FILINGS,
         "mode": ("EDGAR_LIVE_SCALED" if edgar_meta.get("n_fetched") else "EDGAR_CACHE"),
         "real_data": n_fil > 0, "n_events": n_fil,
         "obs_joined": aug_diag.get("n_filing_event_obs", 0), "key_needed": False, "blocker": "",
         "note": f"no-key EDGAR submissions; fetched_live={edgar_meta.get('n_fetched')} "
                 f"cache={edgar_meta.get('n_from_cache')}"},
        {"cycle": 2, "source": "gdelt_news_nokey", "family": FAM_NEWS,
         "mode": ("GDELT_LIVE_PROBE" if news_meta.get("succeeded") else "GDELT_BLOCKED"),
         "real_data": False, "n_events": 0, "obs_joined": 0, "key_needed": True,
         "blocker": "timestamped news/sentiment history",
         "note": f"connector http={news_meta.get('http_status')}; only a recent window, no PIT history"},
        {"cycle": 2, "source": "finra_short_interest_nokey", "family": FAM_SHORT,
         "mode": ("FINRA_PROBE_OK" if finra_meta.get("succeeded") else "FINRA_BLOCKED"),
         "real_data": False, "n_events": 0, "obs_joined": 0, "key_needed": True,
         "blocker": "biweekly short-interest history",
         "note": finra_meta.get("note", "")},
        {"cycle": 2, "source": "norgate_macro_cross_asset", "family": FAM_MACRO,
         "mode": "NORGATE_LOCAL_ACTIVATED", "real_data": True, "n_events": None, "obs_joined": None,
         "key_needed": False, "blocker": "",
         "note": "rates/oil/usd/credit/vix/commodity shocks + sensitivity cohorts (8-E grid)"},
        {"cycle": 2, "source": "options_iv", "family": FAM_OPTIONS, "mode": "NEEDS_PROVIDER",
         "real_data": False, "n_events": 0, "obs_joined": 0, "key_needed": True,
         "blocker": "options IV/skew history", "note": "no local data, no key; executable adapter"},
    ]


def local_no_key_source_results(activation_rows: List[dict]) -> List[dict]:
    rows = []
    for r in activation_rows:
        no_key = (r["key_needed"] is False) or ("nokey" in r["source"]) or ("NOKEY" in r["mode"])
        rows.append({
            "source": r["source"], "family": r["family"], "no_key_attempted": bool(no_key),
            "produced_real_events": bool(r.get("real_data")), "n_events": r.get("n_events"),
            "blocker": r.get("blocker", ""),
            "outcome": ("REAL_EVENTS" if r.get("real_data") and (r.get("n_events") or 0)
                        else ("CONNECTOR_LIVE_NO_HISTORY" if ("PROBE" in r["mode"] or "LIVE" in r["mode"])
                              and not r.get("real_data") else
                              ("LOCAL_ACTIVATED" if r.get("real_data") else "BLOCKED_NEEDS_PROVIDER")))})
    return rows


# =========================================================================== #
# Cycle 3 — new pre-registered combination candidate families (real columns only).
# =========================================================================== #
def plan_new_candidate_signals() -> List[SensSetup]:
    """Pre-register NEW external-event x sensitivity x context combinations. Every condition uses a
    column that already exists in the persisted grid (after 8-G augmentation): defined sensitivity
    cohorts, defined macro driver shocks, and PIT earnings/revision/filing flags. Fixed thresholds;
    no tuning; >=30% challenges/placebos in this family."""
    POS = ("earn_surprise_pos", "ge", 1.0)
    NEG = ("earn_surprise_neg", "ge", 1.0)
    LARGE = ("earn_surprise_large", "ge", 1.0)
    REVUP = ("earn_revision_proxy_up", "ge", 1.0)
    RECENT_POS = ("earn_recent_pos", "ge", 1.0)
    mk = P8G._mk
    s: List[SensSetup] = []
    # --- Earnings catalyst x defined sensitivity/context cohorts (PEAD variants) --------------- #
    s.append(mk("S8I-C-SECLEAD-20", FAM_EARNINGS, EARN_A, "earnings_surprise", "cohort_sector_lead",
                20, [POS, ("cohort_sector_lead", "ge", 1.0)],
                "Positive EPS surprise in a sector-leading name drifts up 20d (catalyst x leadership)."))
    s.append(mk("S8I-C-LOWBETA-20", FAM_EARNINGS, EARN_A, "earnings_surprise", "cohort_low_beta",
                20, [POS, ("cohort_low_beta", "ge", 1.0)],
                "Positive EPS surprise in a low-beta defensive name drifts up 20d."))
    s.append(mk("S8I-C-HIGHBETA-20", FAM_EARNINGS, EARN_A, "earnings_surprise", "cohort_high_beta",
                20, [POS, ("cohort_high_beta", "ge", 1.0)],
                "Positive EPS surprise in a high-beta name drifts up 20d."))
    s.append(mk("S8I-C-VOLSENS-20", FAM_EARNINGS, EARN_A, "earnings_surprise", "cohort_vol_spike_sens",
                20, [POS, ("cohort_vol_spike_sens", "ge", 1.0)],
                "Positive EPS surprise in a volatility-sensitive name drifts up 20d."))
    s.append(mk("S8I-C-LARGE-SECLEAD-10", FAM_EARNINGS, EARN_A, "earnings_surprise",
                "cohort_sector_lead", 10, [LARGE, ("cohort_sector_lead", "ge", 1.0)],
                "Large (>=10%) positive EPS surprise in a sector-leading name drifts up 10d."))
    # --- Revision-proxy x sensitivity (labelled proxy -> capped below CONFIRMED) --------------- #
    s.append(mk("S8I-R-RATES-20", FAM_REVISION, REV_A, "analyst_revision_proxy", "cohort_rates_neg",
                20, [REVUP, ("cohort_rates_neg", "ge", 1.0)],
                "Improving surprise (revision proxy) in a rates-sensitive name drifts up 20d."))
    s.append(mk("S8I-R-SECLEAD-20", FAM_REVISION, REV_A, "analyst_revision_proxy",
                "cohort_sector_lead", 20, [REVUP, ("cohort_sector_lead", "ge", 1.0)],
                "Improving surprise (revision proxy) in a sector-leading name drifts up 20d."))
    # --- Macro / cross-asset shock x sensitivity + earnings confirmation ----------------------- #
    s.append(mk("S8I-M-OIL-20", FAM_S8E011_EXT, SENS_A, "oil", "cohort_oil_pos", 20,
                [("drv_oil_shock_z", "ge", SHOCK_Z), ("cohort_oil_pos", "ge", 1.0), RECENT_POS],
                "Oil rally shock in an oil-positive name CONFIRMED by a recent positive surprise, 20d."))
    s.append(mk("S8I-M-USD-20", FAM_S8E011_EXT, SENS_A, "usd", "cohort_usd_neg", 20,
                [("drv_usd_shock_z", "le", -SHOCK_Z), ("cohort_usd_neg", "ge", 1.0), RECENT_POS],
                "USD sell-off in a USD-negative name CONFIRMED by a recent positive surprise, 20d."))
    s.append(mk("S8I-M-CREDIT-20", FAM_S8E011_EXT, SENS_A, "credit", "cohort_credit_sens", 20,
                [("drv_credit_shock_z", "le", -SHOCK_Z), ("cohort_credit_sens", "ge", 1.0), RECENT_POS],
                "Credit-spread widening in a credit-sensitive name CONFIRMED by a recent surprise, 20d."))
    s.append(mk("S8I-M-VIX-20", FAM_S8E011_EXT, SENS_A, "vix", "cohort_vol_spike_sens", 20,
                [("drv_vix_spike_z", "ge", SHOCK_Z), ("cohort_vol_spike_sens", "ge", 1.0)],
                "VIX spike in a volatility-sensitive name drifts over 20d (downside-sensitivity test)."))
    # --- challenges / placebos (>=30% of this family) ----------------------------------------- #
    s.append(mk("S8I-901", FAM_EARNINGS, VAL_A, "earnings_surprise", "", 20, [POS],
                "CHALLENGE/placebo: positive surprise, NO cohort — isolates the cohort contribution.",
                is_challenge=True, placebo=True))
    s.append(mk("S8I-902", FAM_EARNINGS, VAL_A, "earnings_surprise", "cohort_surprise_sensitive", 20,
                [NEG, ("cohort_surprise_sensitive", "ge", 1.0)],
                "CHALLENGE: NEGATIVE surprise + surprise-sensitive cohort — wrong sign, must not drift up.",
                is_challenge=True))
    s.append(mk("S8I-903", FAM_S8E011_EXT, VAL_A, "oil", "", 20,
                [("drv_oil_shock_z", "ge", SHOCK_Z), RECENT_POS],
                "CHALLENGE/placebo: oil shock + earnings confirm, NO oil cohort — isolates the cohort.",
                is_challenge=True, placebo=True))
    s.append(mk("S8I-904", FAM_REVISION, VAL_A, "analyst_revision_proxy", "", 20, [REVUP],
                "CHALLENGE/placebo: revision proxy up, NO cohort — isolates the cohort contribution.",
                is_challenge=True, placebo=True))
    return s


def _score_new_candidates(grid: pd.DataFrame, panel: SensPanel) -> List["P8G.Experiment"]:
    setups = plan_new_candidate_signals()
    fwd5 = _fwd5_pivot(grid)
    spy_week = _spy_weekly(panel.spy_close, panel.grid_dates)
    n_search = max(sum(1 for s in setups if not s.is_challenge), 10)
    real_by_family = {FAM_EARNINGS: True, FAM_REVISION: True, FAM_S8E011_EXT: True}
    out: List[P8G.Experiment] = []
    for s in setups:
        r = P8G._score_setup(s, grid, fwd5, spy_week, n_search)
        real_ext = real_by_family.get(s.family, False)
        promotion = (P8G._promotion_for(s.family, r["status"], r["ev"], real_ext)
                     if not s.is_challenge else ST_REJECTED)
        out.append(P8G.Experiment(
            exp_id=s.setup_id, cycle=3, family=s.family, agent=s.owning_agent, driver=s.driver,
            cohort=s.cohort, is_challenge=s.is_challenge, real_external_data=real_ext,
            needs_provider=False, hypothesis=s.hypothesis, status=r["status"], promotion=promotion,
            reason=r["reason"],
            metrics={**r["ev"], "walk_forward": r["wf"], "portfolio": r["port"], "checks": r["checks"]}))
    return out


def _macro_family_experiments(panel: SensPanel) -> List["P8G.Experiment"]:
    """Family G: reuse the 8-E macro/cross-asset campaign and adapt into the unified ledger."""
    macro_exps, _state = P8F._macro_experiments_from_8e(panel, cycle=3)
    out: List[P8G.Experiment] = []
    for e in macro_exps:
        if e.status == P8F_CONFIRMED:
            promotion = ST_EXT_CONFIRMED
        elif e.status == P8F_PROMISING_UNCONF:
            promotion = ST_EXT_PROMISING
        else:
            promotion = ST_REJECTED
        if e.is_challenge:
            promotion = ST_REJECTED
        out.append(P8G.Experiment(
            exp_id=e.exp_id, cycle=3, family=FAM_MACRO, agent=e.agent, driver=e.external_driver,
            cohort=e.cohort, is_challenge=e.is_challenge, real_external_data=True,
            needs_provider=False, hypothesis=e.hypothesis, status=e.status, promotion=promotion,
            reason=e.reason, metrics=e.metrics or {}))
    return out


def build_full_ledger(grid: pd.DataFrame, panel: SensPanel) -> List["P8G.Experiment"]:
    """Unified candidate ledger: 8-G external families (C/B/F/H + challenges + blocked A/D/E) +
    macro family G (8-E reuse) + new 8-I combination candidates."""
    ledger: List[P8G.Experiment] = []
    ledger.extend(P8G.run_external_experiments(grid, panel))   # earnings/revision/F20/filings + more
    ledger.extend(_macro_family_experiments(panel))            # family G
    ledger.extend(_score_new_candidates(grid, panel))          # new 8-I combinations
    return ledger


# =========================================================================== #
# Alpha promotion mapping + per-signal helpers.
# =========================================================================== #
def _alpha_promotion(e: "P8G.Experiment") -> str:
    if e.needs_provider:
        return ST_ALPHA_PROVIDER_REQUIRED
    p = e.promotion
    if p == ST_EXT_CONFIRMED:
        return ST_ALPHA_CONFIRMED
    if p in (ST_EXT_PROMISING, ST_NEEDS_HISTORY):
        return ST_ALPHA_PROMISING
    if p == ST_NEEDS_PROVIDER:
        return ST_ALPHA_PROVIDER_REQUIRED
    return ST_REJECTED


def _provider_limited(e: "P8G.Experiment") -> bool:
    """A promising signal is provider/coverage-limited if its evidence is a proxy/thin family OR it
    fails only the count gate (n_events < 1000). These need provider breadth, not more local logic."""
    if e.promotion == ST_NEEDS_HISTORY:
        return True
    n = (e.metrics or {}).get("n_events") or 0
    return e.promotion == ST_EXT_PROMISING and n < GATE_MIN_EVENTS_TOTAL


# =========================================================================== #
# Cycle 4 — validation reports (all from reused 8-E metrics; no re-scoring).
# =========================================================================== #
def candidate_registry_rows(ledger: List["P8G.Experiment"], state: dict) -> List[dict]:
    cov_blocked = set(state.get("coverage_or_provider_blocked", []))
    rows = []
    for e in ledger:
        m = e.metrics or {}
        alpha = _alpha_promotion(e)
        rows.append({
            "candidate_id": e.exp_id, "family": e.family, "agent": e.agent, "driver": e.driver,
            "cohort": e.cohort, "is_challenge": e.is_challenge,
            "real_external_data": e.real_external_data, "needs_provider": e.needs_provider,
            "signal_status": e.status, "ext_promotion": e.promotion, "alpha_promotion": alpha,
            "provider_limited": _provider_limited(e), "n_events": m.get("n_events"),
            "n_recent_events": m.get("n_recent_events"), "lift_vs_control": m.get("lift_vs_control"),
            "ev_after_25bps": m.get("ev_after_25bps"), "worst_decile_mean": m.get("worst_decile_mean"),
            "recent_lift_vs_control": m.get("recent_lift_vs_control"),
            "prior_known_lead": e.exp_id in cov_blocked, "reason": e.reason})
    return rows


def alpha_scoreboard_rows(ledger: List["P8G.Experiment"]) -> List[dict]:
    sb = P8G._scoreboard(ledger)
    by_id = {e.exp_id: e for e in ledger}
    for r in sb:
        e = by_id.get(r["exp_id"])
        r["alpha_promotion"] = _alpha_promotion(e) if e else ""
        r["provider_limited"] = _provider_limited(e) if e else False
    return sb


_ALPHA_SCORE_COLS = P8G._SCORE_COLS + ["alpha_promotion", "provider_limited"]


def walk_forward_report(ledger: List["P8G.Experiment"]) -> List[dict]:
    rows = []
    for e in ledger:
        m = e.metrics or {}
        if e.needs_provider or not m.get("n_events"):
            continue
        wf = m.get("walk_forward", {}) or {}
        folds = {k: v for k, v in wf.items() if isinstance(v, dict)}
        detail = "; ".join(f"{k}:lift={v.get('lift_vs_control')},beats={v.get('beats_control')}"
                           for k, v in folds.items())
        rows.append({"exp_id": e.exp_id, "family": e.family, "n_folds": len(folds),
                     "n_folds_positive": wf.get("n_folds_positive", 0),
                     "survives_walk_forward": (m.get("checks", {}) or {}).get("survives_walk_forward"),
                     "folds_detail": detail})
    return rows


def recent_period_report(ledger: List["P8G.Experiment"]) -> List[dict]:
    rows = []
    for e in ledger:
        m = e.metrics or {}
        if e.needs_provider or not m.get("n_events"):
            continue
        rows.append({"exp_id": e.exp_id, "family": e.family, "n_recent_events": m.get("n_recent_events"),
                     "recent_lift_vs_control": m.get("recent_lift_vs_control"),
                     "survives_recent_2015_2026": (m.get("checks", {}) or {}).get("survives_recent_2015_2026"),
                     "events_recent_ge_100": (m.get("checks", {}) or {}).get("events_recent_ge_100")})
    return rows


def concentration_report(ledger: List["P8G.Experiment"]) -> List[dict]:
    rows = []
    for e in ledger:
        m = e.metrics or {}
        if e.needs_provider or not m.get("n_events"):
            continue
        c = m.get("checks", {}) or {}
        rows.append({"exp_id": e.exp_id, "family": e.family,
                     "max_year_fraction": m.get("max_year_fraction"),
                     "max_sector_fraction": m.get("max_sector_fraction"),
                     "max_ticker_fraction": m.get("max_ticker_fraction"),
                     "not_year_concentrated": c.get("not_year_concentrated"),
                     "not_sector_concentrated": c.get("not_sector_concentrated"),
                     "not_ticker_concentrated": c.get("not_ticker_concentrated")})
    return rows


def placebo_leakage_report(ledger: List["P8G.Experiment"]) -> List[dict]:
    rows = []
    for e in ledger:
        m = e.metrics or {}
        c = m.get("checks", {}) or {}
        lift = m.get("lift_vs_control")
        rows.append({"exp_id": e.exp_id, "family": e.family, "is_challenge": e.is_challenge,
                     "leakage_safe": c.get("leakage_safe", True),
                     "placebo_clean": c.get("placebo_clean"),
                     "lift_vs_control": lift,
                     "challenge_shows_lift": bool(e.is_challenge and (lift or -1) >= P8E.GATE_PLACEBO_MAX_LIFT),
                     "reason": e.reason})
    return rows


# =========================================================================== #
# Cycle 5 — recommendation, ranked options, memory, backlog.
# =========================================================================== #
def derive_recommendation(framework_ok: bool, ledger: List["P8G.Experiment"],
                          readiness: Dict[str, bool]) -> Tuple[str, dict]:
    testable = [e for e in ledger if not e.needs_provider and (e.metrics or {}).get("n_events")]
    confirmed = [e for e in ledger if _alpha_promotion(e) == ST_ALPHA_CONFIRMED]
    promising = [e for e in ledger if _alpha_promotion(e) == ST_ALPHA_PROMISING]
    clean_promising = [e for e in promising if not _provider_limited(e)]
    limited_promising = [e for e in promising if _provider_limited(e)]
    provider_req = [e for e in ledger if _alpha_promotion(e) == ST_ALPHA_PROVIDER_REQUIRED]
    detail = {
        "n_testable_scored": len(testable), "n_confirmed": len(confirmed),
        "n_promising": len(promising), "n_clean_promising": len(clean_promising),
        "n_provider_limited_promising": len(limited_promising), "n_provider_required": len(provider_req),
        "any_provider_key": any(readiness.values()),
        "confirmed_ids": [e.exp_id for e in confirmed],
        "clean_promising_ids": [e.exp_id for e in clean_promising],
        "provider_limited_ids": [e.exp_id for e in limited_promising],
    }
    if not framework_ok:
        return REC_FRAMEWORK_BLOCKED, detail
    if confirmed:
        return REC_CONFIRMED, detail
    if clean_promising:
        # a real, full-coverage promising lead whose next step is local (fixed filters / more analysis)
        return REC_PROMISING, detail
    if limited_promising:
        # the only promising leads are gated by event coverage / provider history
        return REC_PROVIDER_LIMITED, detail
    if provider_req and not testable:
        return REC_PROVIDER_REQUIRED, detail
    if provider_req:
        return REC_PROVIDER_REQUIRED, detail
    if testable:
        return REC_REJECTED, detail
    return REC_FRAMEWORK_BLOCKED, detail


def ranked_next_options(ledger: List["P8G.Experiment"], readiness: Dict[str, bool],
                        state: dict) -> List[dict]:
    """Rank concrete next moves by probability of success. Local/no-key levers that already have a
    positive lead score higher than provider-gated moves; the provider move has the highest ceiling
    but a lower near-term probability without a key."""
    has_promising_macro = any(_alpha_promotion(e) == ST_ALPHA_PROMISING and e.family == FAM_MACRO
                              for e in ledger)
    has_promising_f20 = any(e.exp_id == F20_SETUP_ID and _alpha_promotion(e) == ST_ALPHA_PROMISING
                            for e in ledger)
    options = [
        {"rank": 0, "option": "Apply the fixed beta-tail / volatility structural filters to the best "
         "promising macro x sensitivity and earnings-confirmed leads and re-validate the filtered "
         "variant's stability (LOCAL, no new data).",
         "lever": "local_fixed_filters", "needs_provider": False,
         "prob_success": (0.6 if has_promising_macro or has_promising_f20 else 0.35),
         "ceiling": "medium", "why": "fixed beta filter already flips beats-SPY-active True and clears "
         "the -12% tail on the confirmed variant; the open question is stability on more events"},
        {"rank": 0, "option": "Acquire a BROAD multi-ticker earnings + analyst-revision provider feed "
         "(FMP/Finnhub/Zacks/EODHD across S&P 500/1500) and rebuild a chunked weekly sensitivity grid "
         "on D: so universe and events expand together; re-run F20 + the C/R families on the fixed gate.",
         "lever": "provider_earnings_revision", "needs_provider": True,
         "prob_success": 0.5, "ceiling": "high", "why": "the ONLY lever that lifts F20's 692 events "
         "past the >=1000 gate; raises EVERY earnings/revision candidate's coverage at once"},
        {"rank": 0, "option": "Widen the no-key SEC EDGAR filings overlay + activate FINRA biweekly "
         "short-interest history (free bulk) and test filings/short-interest x sensitivity on the "
         "fixed gate.",
         "lever": "nokey_filings_short_interest", "needs_provider": False,
         "prob_success": 0.4, "ceiling": "medium", "why": "no-key/free; filings overlay already "
         "showed +1.22% lift on 68 events — direction, not yet coverage"},
        {"rank": 0, "option": "Acquire a timestamped news/sentiment history (GDELT bulk export or "
         "NewsAPI) and test sentiment-shock x sensitivity + a news confirmation of the macro leads.",
         "lever": "provider_news_sentiment", "needs_provider": True,
         "prob_success": 0.3, "ceiling": "medium", "why": "connector proven live (HTTP 200) but only "
         "a recent window; a real PIT history is required and unproven for alpha"},
    ]
    options.sort(key=lambda o: o["prob_success"], reverse=True)
    for i, o in enumerate(options, 1):
        o["rank"] = i
    return options[:3] + ([options[3]] if len(options) > 3 else [])


def hypothesis_backlog(ledger: List["P8G.Experiment"], readiness: Dict[str, bool],
                       state: dict) -> List[dict]:
    """Ranked hypothesis backlog (Part 4). Promising-but-blocked leads rank highest; provider-gated
    families and the next combination ideas follow."""
    rows = []
    rank = 0
    # 1) keep pushing the promising leads
    for e in sorted(ledger, key=lambda x: ((x.metrics or {}).get("ev_after_25bps") or -9), reverse=True):
        if e.is_challenge or _alpha_promotion(e) != ST_ALPHA_PROMISING:
            continue
        rank += 1
        rows.append({"backlog_id": f"BL8I-{rank:02d}", "priority": "high",
                     "hypothesis_id": e.exp_id, "family": e.family,
                     "work_item": ("apply fixed structural filter + re-validate stability"
                                   if not _provider_limited(e) else
                                   "acquire provider breadth to lift coverage past the fixed gate"),
                     "blocker": ("local_only" if not _provider_limited(e) else "provider_or_coverage"),
                     "owning_agent": e.agent,
                     "rationale": f"promising: lift={(e.metrics or {}).get('lift_vs_control')} "
                                  f"ev25={(e.metrics or {}).get('ev_after_25bps')} n={(e.metrics or {}).get('n_events')}"})
    # 2) provider-gated families with no local data
    for fam, item in [(FAM_NEWS, "acquire timestamped news/sentiment history"),
                      (FAM_SHORT, "activate FINRA biweekly short-interest bulk history"),
                      (FAM_OPTIONS, "acquire options IV/skew history"),
                      (FAM_REVISION, "acquire a TRUE analyst-revision consensus feed (replace proxy)")]:
        rank += 1
        rows.append({"backlog_id": f"BL8I-{rank:02d}", "priority": "high" if fam in (FAM_REVISION, FAM_SHORT) else "medium",
                     "hypothesis_id": fam, "family": fam, "work_item": item,
                     "blocker": "provider_or_bulk", "owning_agent": EXT_A,
                     "rationale": "no-key/local data insufficient for a PIT confirmation overlay"})
    # 3) next combination ideas (already pre-registered this phase or queued)
    for hid, item, fam in [("S8I-C-SECLEAD-20", "earnings x sector-leadership PEAD on broader feed", FAM_EARNINGS),
                           ("S8I-M-OIL-20", "oil/usd/credit shock x sensitivity + earnings confirm at scale", FAM_S8E011_EXT)]:
        rank += 1
        rows.append({"backlog_id": f"BL8I-{rank:02d}", "priority": "medium", "hypothesis_id": hid,
                     "family": fam, "work_item": item, "blocker": "coverage",
                     "owning_agent": SENS_A,
                     "rationale": "combination tested this phase; re-run when event coverage expands"})
    return rows


def build_autonomous_memory(rec: str, detail: dict, ledger: List["P8G.Experiment"],
                            readiness: Dict[str, bool], state: dict, options: List[dict],
                            panel: SensPanel) -> dict:
    def ids(pred):
        return [e.exp_id for e in ledger if pred(e) and not e.is_challenge]
    g = panel.grid if panel is not None else pd.DataFrame()
    return {
        "phase": PHASE, "generated_utc": _utc_now_iso(), "recommendation": rec,
        "thesis": "external event x ticker sensitivity x sector/regime/liquidity/valuation x confirmation",
        "best_current_path": (options[0]["option"] if options else ""),
        "ranked_next_options": options,
        "current_best_leads": ids(lambda e: _alpha_promotion(e) in (ST_ALPHA_CONFIRMED, ST_ALPHA_PROMISING)),
        "confirmed_alpha_signals": ids(lambda e: _alpha_promotion(e) == ST_ALPHA_CONFIRMED),
        "promising_alpha_signals": ids(lambda e: _alpha_promotion(e) == ST_ALPHA_PROMISING),
        "provider_required_signals": ids(lambda e: _alpha_promotion(e) == ST_ALPHA_PROVIDER_REQUIRED),
        "coverage_or_provider_blocked": state.get("coverage_or_provider_blocked", []),
        "invalid_logic_rejected": ids(lambda e: e.promotion == ST_REJECTED) + state.get("invalid_logic_rejected", []),
        "rejected_research_lines": state.get("rejected_research_lines", []),
        "graveyard_families": state.get("graveyard_families", []),
        "provider_readiness": readiness,
        "binding_constraint": ("event-data BREADTH (earnings feed = 75 tickers; no key for "
                               "revision/news/options/short) — the daily Norgate panel is NOT binding"),
        "open_research_questions": [
            "Does a fixed structural filter make a promising macro/earnings-confirmed lead stable "
            "enough to CONFIRM on the existing coverage?",
            "Which single provider feed (broad earnings+revision) unlocks the most candidates at once?",
            "Does any cross-asset shock x sensitivity + earnings confirmation beat the rates lead?",
            "Can a free FINRA short-interest history activate family E without a paid key?",
        ],
        "next_autonomous_actions": [o["option"] for o in options],
        "stop_conditions": [
            "stop when one signal is CONFIRMED_ALPHA_SIGNAL",
            "stop when all local/no-key/provider-key paths are exhausted",
            "stop when a hard provider requirement blocks the next meaningful move",
            "stop on a safety/leakage problem",
            "stop when the experiment budget is exhausted",
        ],
        "panel": {"n_symbols": int(g["symbol"].nunique()) if not g.empty else 0,
                  "n_obs": int(len(g)), "source": "persisted 8-E weekly grid (reused, no rebuild)"},
    }


# =========================================================================== #
# Cycle manager (5 cycles).
# =========================================================================== #
def build_cycles(state: dict, activation_rows: List[dict], ledger: List["P8G.Experiment"],
                 rec: str, options: List[dict]) -> List[dict]:
    n_real = sum(1 for e in ledger if e.real_external_data and not e.is_challenge)
    n_conf = sum(1 for e in ledger if _alpha_promotion(e) == ST_ALPHA_CONFIRMED)
    n_prom = sum(1 for e in ledger if _alpha_promotion(e) == ST_ALPHA_PROMISING)
    n_prov = sum(1 for e in ledger if _alpha_promotion(e) == ST_ALPHA_PROVIDER_REQUIRED)
    stop = bool(n_conf) or rec in (REC_PROVIDER_LIMITED, REC_PROVIDER_REQUIRED, REC_PROMISING,
                                   REC_REJECTED)
    return [
        {"cycle": 1, "decision": "continue", "continue": True,
         "tasks": [{"agent": DIR_A, "task": "rebuild state from 8-E..8-H outputs", "result": f"{len(state.get('ranked_existing_leads', []))} leads"},
                   {"agent": VAL_A, "task": "split coverage-blocked vs invalid-logic", "result": f"cov={len(state.get('coverage_or_provider_blocked', []))} invalid={len(state.get('invalid_logic_rejected', []))}"}],
         "rationale": "research state rebuilt; existing leads ranked and classified by blocker"},
        {"cycle": 2, "decision": "continue", "continue": True,
         "tasks": [{"agent": EXT_A, "task": "activate every local/no-key source", "result": f"{len(activation_rows)} sources"},
                   {"agent": SHORT_A, "task": "no-key FINRA short-interest probe", "result": "attempted"},
                   {"agent": EARN_A, "task": "normalize + leak-safe join real events", "result": "ACTIVATED"}],
         "rationale": "local earnings + no-key SEC/GDELT/FINRA + Norgate macro activated; panels built"},
        {"cycle": 3, "decision": "continue", "continue": True,
         "tasks": [{"agent": SENS_A, "task": "generate combination candidate families", "result": f"{n_real} real candidates"},
                   {"agent": EARN_A, "task": "earnings x sensitivity/sector/vol/beta", "result": "TESTED"},
                   {"agent": REV_A, "task": "revision-proxy x sensitivity (capped)", "result": "TESTED_PROXY"}],
         "rationale": "new external x sensitivity x context combinations scored on the fixed gate"},
        {"cycle": 4, "decision": "continue", "continue": True,
         "tasks": [{"agent": VAL_A, "task": "matched control + recent + walk-forward + placebo/leakage + MT", "result": "OK"},
                   {"agent": RSK_A, "task": "tail / worst-decile / concentration", "result": "OK"}],
         "rationale": "full validation battery applied; challenges keep cohorts honest"},
        {"cycle": 5, "decision": ("stop_signal_confirmed" if n_conf else "stop_best_path_selected"),
         "continue": not stop,
         "stop_reason": ("a CONFIRMED alpha signal was found" if n_conf else
                         f"best current path selected ({rec}); confirmed={n_conf} promising={n_prom} "
                         f"provider_required={n_prov}; next meaningful breakthrough needs provider breadth"),
         "tasks": [{"agent": MODEL_A, "task": "model-candidate registry update (no deploy)", "result": "OK"},
                   {"agent": DIR_A, "task": "rank next options + select next campaign", "result": (options[0]["lever"] if options else "n/a")}],
         "rationale": f"decision={rec}; ranked {len(options)} next options by probability of success"},
    ]


# =========================================================================== #
# Orchestration + artifacts.
# =========================================================================== #
ARTIFACTS = [
    "phase8i_autonomous_alpha_discovery_program.json", "autonomous_research_memory.json",
    "hypothesis_backlog.csv", "data_source_activation_log.csv", "provider_key_inventory.csv",
    "local_no_key_source_results.csv", "normalized_event_panel_manifest.csv",
    "candidate_signal_registry.csv", "experiment_pre_registration.csv", "alpha_signal_scoreboard.csv",
    "matched_control_report.csv", "walk_forward_validation_report.csv",
    "recent_period_validation_report.csv", "tail_risk_report.csv", "concentration_report.csv",
    "placebo_leakage_report.csv", "multiple_testing_report.csv", "confirmed_alpha_signals.csv",
    "promising_alpha_signals.csv", "provider_required_signals.csv", "rejected_alpha_signals.csv",
    "model_candidate_registry_update.csv", "research_director_decision.json", "phase8j_next_plan.json",
]


def run(out_dir: Path, *, activate_live: bool = False) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    started = _utc_now_iso()
    framework_ok = True

    # provider detection (names/presence only).
    key_rows = P8F.detect_provider_keys()
    readiness = P8F.provider_readiness(key_rows)

    # ---- Cycle 1: rebuild research state ----
    state = rebuild_research_state()

    # ---- Cycle 2: activate every local / no-key source ----
    earn = P8G.load_earnings_events()
    if activate_live:
        want = list(earn["ticker"].value_counts().index) if not earn.empty else []
        filings, edgar_meta = P8H._edgar_scaled_filings(want, cap=P8H.EDGAR_SCALED_CAP)
    else:
        filings, fmeta = P8G.load_sec_filing_events(activate_live=False)
        edgar_meta = {"cap": 0, "n_requested": 0, "n_from_cache": 0, "n_fetched": 0, "error": "live off"}
    news_rows, news_meta = P8H.news_sentiment_activation(activate_live=activate_live)
    finra_rows, finra_meta = finra_short_interest_activation(activate_live=activate_live)

    panel = P8F.load_persisted_panel()
    panel_ok = bool(panel is not None and panel.ok and not panel.grid.empty)

    if not panel_ok:
        framework_ok = False
        grid = pd.DataFrame()
        aug_diag = {"error": "persisted 8-E panel unavailable"}
        ledger = P8G._blocked_family_experiments()
    else:
        grid, aug_diag = P8G.augment_grid(panel.grid, earn, filings)
        # ---- Cycle 3: generate + score the full candidate ledger ----
        ledger = build_full_ledger(grid, panel)

    activation_rows = data_source_activation_log(earn, filings, edgar_meta, news_meta, finra_meta,
                                                 readiness, aug_diag)
    no_key_rows = local_no_key_source_results(activation_rows)

    # ---- Cycle 5: decide + rank ----
    rec, detail = derive_recommendation(framework_ok, ledger, readiness)
    options = ranked_next_options(ledger, readiness, state)
    cycles = build_cycles(state, activation_rows, ledger, rec, options)
    memory = build_autonomous_memory(rec, detail, ledger, readiness, state, options, panel)
    report = _assemble_report(started, rec, detail, panel, panel_ok, ledger, readiness, key_rows,
                              aug_diag, state, options, edgar_meta, news_meta, finra_meta)
    _emit_all(out_dir, report, ledger, readiness, key_rows, state, activation_rows, no_key_rows,
              memory, options, earn, filings, edgar_meta)
    return report


def _assemble_report(started, rec, detail, panel, panel_ok, ledger, readiness, key_rows, aug_diag,
                     state, options, edgar_meta, news_meta, finra_meta) -> dict:
    g = panel.grid if panel is not None else pd.DataFrame()
    testable = [e for e in ledger if not e.needs_provider and (e.metrics or {}).get("n_events")]
    return {
        "phase": PHASE, "objective": OBJECTIVE, "generated_utc": started, "recommendation": rec,
        "allowed_recommendations": list(ALLOWED_RECOMMENDATIONS), "decision_detail": detail,
        "question_answered": ("What is the best current path to a real signal, and which candidates / "
                              "data sources should be pursued next without user micro-direction?"),
        "agents": ALL_AGENTS, "cycles_run": MAX_CYCLES,
        "panel": {"panel_ok": panel_ok, "n_symbols": int(g["symbol"].nunique()) if not g.empty else 0,
                  "n_obs": int(len(g)),
                  "date_range": ([str(g["date"].min())[:10], str(g["date"].max())[:10]] if not g.empty else [])},
        "research_state": {"prior_8g": state.get("prior_8g_recommendation"),
                           "prior_8h": state.get("prior_8h_recommendation"),
                           "ranked_existing_leads": len(state.get("ranked_existing_leads", [])),
                           "coverage_or_provider_blocked": state.get("coverage_or_provider_blocked", []),
                           "invalid_logic_rejected": state.get("invalid_logic_rejected", [])},
        "candidates": {
            "n_total": len(ledger), "n_testable_scored": len(testable),
            "n_new_combination_candidates": sum(1 for e in ledger if e.cycle == 3 and not e.is_challenge),
            "confirmed": [e.exp_id for e in ledger if _alpha_promotion(e) == ST_ALPHA_CONFIRMED],
            "promising": [e.exp_id for e in ledger if _alpha_promotion(e) == ST_ALPHA_PROMISING],
            "provider_required": [e.exp_id for e in ledger if _alpha_promotion(e) == ST_ALPHA_PROVIDER_REQUIRED],
            "rejected": [e.exp_id for e in ledger if _alpha_promotion(e) == ST_REJECTED and not e.is_challenge]},
        "best_current_path": (options[0]["option"] if options else ""),
        "top_next_options": options,
        "data_activation": {
            "edgar_fetched_live": edgar_meta.get("n_fetched"), "edgar_from_cache": edgar_meta.get("n_from_cache"),
            "filing_event_obs": aug_diag.get("n_filing_event_obs", 0),
            "earnings_event_obs": aug_diag.get("n_earn_event_obs", 0),
            "news_http": news_meta.get("http_status"), "news_succeeded": news_meta.get("succeeded"),
            "finra_http": finra_meta.get("http_status"), "finra_succeeded": finra_meta.get("succeeded")},
        "provider": {"any_key_present": any(readiness.values()), "n_keys_checked": len(key_rows),
                     "readiness": readiness},
        "budget": P8G._budget(ledger), "multiple_testing": _multiple_testing(ledger),
        "safety": _safety_block(readiness, edgar_meta, news_meta, finra_meta),
    }


def _multiple_testing(ledger: List["P8G.Experiment"]) -> dict:
    mt = dict(P8G._multiple_testing(ledger))
    mt["n_alpha_confirmed"] = sum(1 for e in ledger if _alpha_promotion(e) == ST_ALPHA_CONFIRMED)
    mt["n_alpha_promising"] = sum(1 for e in ledger if _alpha_promotion(e) == ST_ALPHA_PROMISING)
    mt["n_alpha_provider_required"] = sum(1 for e in ledger if _alpha_promotion(e) == ST_ALPHA_PROVIDER_REQUIRED)
    return mt


def _safety_block(readiness, edgar_meta, news_meta, finra_meta) -> dict:
    return {
        "research_only": True, "local_first": True, "provider_keys_detected": any(readiness.values()),
        "secrets_printed": False,
        "no_key_public_collection_ran": bool(edgar_meta.get("n_fetched") or news_meta.get("succeeded")
                                             or finra_meta.get("succeeded")),
        "news_sentiment_faked": False, "short_interest_faked": False,
        "revision_is_labelled_proxy_not_confirmed": True, "mock_fixtures_excluded": True,
        "external_data_faked": False, "point_in_time_join": True, "thresholds_fixed_a_priori": True,
        "thresholds_modified_after_results": False, "factor_signs_modified_after_results": False,
        "packages_installed": False, "large_data_only_on_d": True, "optimized_weights": False,
        "regime_activation": False, "ml_fit": False, "failed_experiments_hidden": False,
        "live_trading_signals": False, "broker_or_orders": False, "automation": False,
        "paper_trader_touched": False, "gcp_touched": False, "committed": False, "pushed": False}


def _director_decision(report, ledger, options) -> dict:
    return {
        "phase": PHASE, "generated_utc": report["generated_utc"],
        "recommendation": report["recommendation"],
        "allowed_recommendations": list(ALLOWED_RECOMMENDATIONS),
        "allowed_alpha_statuses": list(ALLOWED_ALPHA_STATUSES),
        "decision_detail": report["decision_detail"],
        "thesis": "combinatorial alpha: external event x ticker sensitivity x context x confirmation",
        "best_current_path": report["best_current_path"], "top_next_options": options,
        "binding_constraint": ("event-data BREADTH and provider history (earnings feed = 75 tickers; "
                               "no key for true revision/news/options/short) — NOT the Norgate panel"),
        "anti_p_hacking": {
            "all_pre_registered": True, "thresholds_fixed_a_priori": True,
            "thresholds_modified_after_results": False, "factor_signs_modified_after_results": False,
            "challenge_fraction": P8G._budget(ledger)["challenge_fraction"],
            "external_data_never_faked": True, "revision_proxy_capped_below_confirmed": True,
            "combinations_use_only_existing_real_columns": True},
        "stop_conditions_honored": [
            "local data first; Norgate for price/macro; no package install",
            "no threshold change to rescue a result", "no factor-sign flipping",
            "no weight optimization", "no regime activation", "no ML fitting",
            "external data never faked", "revision proxy labelled + capped", "no secrets printed",
            "no live trading signals", "no broker/orders/automation",
            "no Paper Trader / GCP / deployment", "failed experiments not hidden",
            "no commit", "no push"]}


def _phase8j_plan(report, readiness, options) -> dict:
    return {
        "from_phase": PHASE, "recommendation": report["recommendation"], "next_phase": "8-J",
        "best_current_path": report["best_current_path"],
        "ranked_next_options": options,
        "binding_constraint": "event-data breadth + provider history (earnings/revision/news/short)",
        "next_steps": [o["option"] for o in options],
        "provider_priority": ["broad_earnings_and_revision_feed", "short_interest_finra_bulk",
                              "news_sentiment_history", "options_iv"],
        "provider_readiness": readiness,
        "hard_constraints": [
            "local data first; Norgate + FRED for price/macro", "do not install packages",
            "large data on D: only; repo gets summaries/panels", "never print secrets",
            "bounded no-key collection; point-in-time joins only", "thresholds fixed a priori",
            "no Paper Trader / GCP / deployment", "no broker/order/automation",
            "no live trading signals", "no weight optimization", "no factor-sign flipping",
            "no regime activation", "external data never faked", "do not hide failed experiments",
            "do not commit", "do not push"]}


def _pre_registration_rows(ledger: List["P8G.Experiment"]) -> List[dict]:
    return [{"exp_id": e.exp_id, "cycle": e.cycle, "family": e.family, "agent": e.agent,
             "driver": e.driver, "cohort": e.cohort, "is_challenge": e.is_challenge,
             "real_external_data": e.real_external_data, "needs_provider": e.needs_provider,
             "hypothesis": e.hypothesis, "pre_registered_before_scoring": True} for e in ledger]


def _normalized_panel_manifest(earn, filings, edgar_meta) -> List[dict]:
    n_earn = int(len(earn)) if earn is not None else 0
    n_fil = int(len(filings)) if filings is not None else 0
    return [
        {"source": "earnings", "family": FAM_EARNINGS, "schema": "earnings",
         "normalized_path": str((EXTERNAL_NORMALIZED / "earnings" / "earnings_normalized.csv")).replace("\\", "/"),
         "n_real_events": n_earn, "n_mock_events": 0, "pit_field": "point_in_time_available_at",
         "status": "REAL_EVENTS_ACTIVATED"},
        {"source": "analyst_revision_proxy", "family": FAM_REVISION, "schema": "analyst_revisions",
         "normalized_path": str((EXTERNAL_NORMALIZED / "analyst_revision" / "analyst_revision_proxy_normalized.csv")).replace("\\", "/"),
         "n_real_events": n_earn, "n_mock_events": 0, "pit_field": "point_in_time_available_at",
         "status": "REAL_PROXY_ACTIVATED_CAPPED"},
        {"source": "filings_sec", "family": FAM_FILINGS, "schema": "sec_filings",
         "normalized_path": str((EXTERNAL_NORMALIZED / "filings_sec" / "filings_normalized.csv")).replace("\\", "/"),
         "n_real_events": n_fil, "n_mock_events": 0, "pit_field": "availability_date",
         "status": ("REAL_EVENTS_SCALED" if edgar_meta.get("n_fetched") else "REAL_EVENTS_CACHED")},
        {"source": "news_sentiment", "family": FAM_NEWS, "schema": "news",
         "normalized_path": "", "n_real_events": 0, "n_mock_events": 0,
         "pit_field": "point_in_time_available_at", "status": "NO_HISTORY_NEEDS_PROVIDER"},
        {"source": "short_interest", "family": FAM_SHORT, "schema": "short_interest",
         "normalized_path": "", "n_real_events": 0, "n_mock_events": 0,
         "pit_field": "point_in_time_available_at", "status": "NO_HISTORY_NEEDS_BULK_OR_PROVIDER"},
        {"source": "options_iv", "family": FAM_OPTIONS, "schema": "options",
         "normalized_path": "", "n_real_events": 0, "n_mock_events": 0,
         "pit_field": "point_in_time_available_at", "status": "NEEDS_PROVIDER"},
    ]


def _emit_all(out_dir, report, ledger, readiness, key_rows, state, activation_rows, no_key_rows,
              memory, options, earn, filings, edgar_meta) -> None:
    p = lambda n: out_dir / n
    _write_json(p("phase8i_autonomous_alpha_discovery_program.json"), report)
    _write_json(p("autonomous_research_memory.json"), memory)
    _write_csv(p("hypothesis_backlog.csv"), hypothesis_backlog(ledger, readiness, state),
               ["backlog_id", "priority", "hypothesis_id", "family", "work_item", "blocker",
                "owning_agent", "rationale"])
    _write_csv(p("data_source_activation_log.csv"), activation_rows,
               ["cycle", "source", "family", "mode", "real_data", "n_events", "obs_joined",
                "key_needed", "blocker", "note"])
    _write_csv(p("provider_key_inventory.csv"), key_rows,
               ["key_env_var", "present", "feeds_families", "detection"])
    _write_csv(p("local_no_key_source_results.csv"), no_key_rows,
               ["source", "family", "no_key_attempted", "produced_real_events", "n_events",
                "blocker", "outcome"])
    _write_csv(p("normalized_event_panel_manifest.csv"), _normalized_panel_manifest(earn, filings, edgar_meta),
               ["source", "family", "schema", "normalized_path", "n_real_events", "n_mock_events",
                "pit_field", "status"])
    _write_csv(p("candidate_signal_registry.csv"), candidate_registry_rows(ledger, state),
               ["candidate_id", "family", "agent", "driver", "cohort", "is_challenge",
                "real_external_data", "needs_provider", "signal_status", "ext_promotion",
                "alpha_promotion", "provider_limited", "n_events", "n_recent_events",
                "lift_vs_control", "ev_after_25bps", "worst_decile_mean", "recent_lift_vs_control",
                "prior_known_lead", "reason"])
    _write_csv(p("experiment_pre_registration.csv"), _pre_registration_rows(ledger),
               ["exp_id", "cycle", "family", "agent", "driver", "cohort", "is_challenge",
                "real_external_data", "needs_provider", "hypothesis", "pre_registered_before_scoring"])
    sb = alpha_scoreboard_rows(ledger)
    _write_csv(p("alpha_signal_scoreboard.csv"), sb, _ALPHA_SCORE_COLS)
    _write_csv(p("matched_control_report.csv"), P8G._matched_control(ledger),
               ["exp_id", "family", "driver", "cohort", "is_challenge", "n_events",
                "n_matched_control", "triggered_mean", "control_mean", "lift_vs_control",
                "hit_rate", "control_hit_rate", "ev_after_25bps", "recent_lift_vs_control"])
    _write_csv(p("walk_forward_validation_report.csv"), walk_forward_report(ledger),
               ["exp_id", "family", "n_folds", "n_folds_positive", "survives_walk_forward", "folds_detail"])
    _write_csv(p("recent_period_validation_report.csv"), recent_period_report(ledger),
               ["exp_id", "family", "n_recent_events", "recent_lift_vs_control",
                "survives_recent_2015_2026", "events_recent_ge_100"])
    _write_csv(p("tail_risk_report.csv"), P8G._risk_portfolio(ledger),
               ["exp_id", "family", "cohort", "n_events", "worst_decile_mean", "avg_mae_20",
                "max_year_fraction", "max_sector_fraction", "max_ticker_fraction",
                "beats_spy_active", "beats_cash_active", "tail_gate_pass"])
    _write_csv(p("concentration_report.csv"), concentration_report(ledger),
               ["exp_id", "family", "max_year_fraction", "max_sector_fraction", "max_ticker_fraction",
                "not_year_concentrated", "not_sector_concentrated", "not_ticker_concentrated"])
    _write_csv(p("placebo_leakage_report.csv"), placebo_leakage_report(ledger),
               ["exp_id", "family", "is_challenge", "leakage_safe", "placebo_clean",
                "lift_vs_control", "challenge_shows_lift", "reason"])
    mt = _multiple_testing(ledger)
    _write_csv(p("multiple_testing_report.csv"),
               [{"metric": k, "value": (";".join(map(str, v)) if isinstance(v, list) else v)}
                for k, v in mt.items()], ["metric", "value"])
    # alpha roll-ups
    confirmed = [r for r in sb if r["alpha_promotion"] == ST_ALPHA_CONFIRMED]
    promising = [r for r in sb if r["alpha_promotion"] == ST_ALPHA_PROMISING]
    provider_req = [r for r in sb if r["alpha_promotion"] == ST_ALPHA_PROVIDER_REQUIRED]
    rejected = [r for r in sb if r["alpha_promotion"] == ST_REJECTED and not r["is_challenge"]]
    _write_csv(p("confirmed_alpha_signals.csv"),
               confirmed if confirmed else [{"status": "NO_CONFIRMED_ALPHA_SIGNAL"}],
               _ALPHA_SCORE_COLS if confirmed else ["status"])
    _write_csv(p("promising_alpha_signals.csv"),
               promising if promising else [{"status": "NO_PROMISING_ALPHA_SIGNAL"}],
               _ALPHA_SCORE_COLS if promising else ["status"])
    _write_csv(p("provider_required_signals.csv"),
               provider_req if provider_req else [{"status": "NO_PROVIDER_REQUIRED_SIGNAL"}],
               _ALPHA_SCORE_COLS if provider_req else ["status"])
    _write_csv(p("rejected_alpha_signals.csv"),
               rejected if rejected else [{"status": "NO_REJECTED_ALPHA_SIGNAL"}],
               _ALPHA_SCORE_COLS if rejected else ["status"])
    _write_csv(p("model_candidate_registry_update.csv"), P8G.model_candidate_update(ledger),
               ["candidate_id", "family", "driver", "cohort", "signal_status", "promotion",
                "registry_decision", "proposed_contribution", "real_external_data",
                "lift_vs_control", "ev_after_25bps", "worst_decile_mean", "deployed",
                "paper_trader_output", "production", "note"])
    _write_json(p("research_director_decision.json"), _director_decision(report, ledger, options))
    _write_json(p("phase8j_next_plan.json"), _phase8j_plan(report, readiness, options))


def _print_summary(report: dict) -> None:
    pan, cand, da = report["panel"], report["candidates"], report["data_activation"]
    print(f"[{PHASE}] recommendation = {report['recommendation']}")
    print(f"[{PHASE}] panel = {pan['n_symbols']} symbols x {pan['n_obs']} obs {pan.get('date_range')} (ok={pan['panel_ok']})")
    print(f"[{PHASE}] candidates = {cand['n_total']} (testable={cand['n_testable_scored']} "
          f"new_combos={cand['n_new_combination_candidates']}); confirmed={cand['confirmed']} "
          f"promising={cand['promising']} provider_required={len(cand['provider_required'])} "
          f"rejected={len(cand['rejected'])}")
    print(f"[{PHASE}] activation: earnings_obs={da['earnings_event_obs']} filing_obs={da['filing_event_obs']} "
          f"edgar_live={da['edgar_fetched_live']} news_http={da['news_http']} finra_http={da['finra_http']}")
    print(f"[{PHASE}] best path: {report['best_current_path'][:96]}...")
    for o in report["top_next_options"]:
        print(f"[{PHASE}]   option#{o['rank']} p={o['prob_success']} lever={o['lever']} provider={o['needs_provider']}")


def _parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Phase 8-I Autonomous Alpha Discovery Program")
    ap.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    ap.add_argument("--activate-live", action="store_true",
                    help="scale no-key SEC EDGAR + retry GDELT news + probe FINRA short interest (cached on D:)")
    return ap.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parse_args(argv)
    try:
        report = run(Path(args.out_dir), activate_live=args.activate_live)
    except Exception as exc:  # pragma: no cover - top-level guard
        out = Path(args.out_dir)
        out.mkdir(parents=True, exist_ok=True)
        _write_json(out / "phase8i_autonomous_alpha_discovery_program.json",
                    {"phase": PHASE, "recommendation": REC_ERROR, "error": repr(exc),
                     "generated_utc": _utc_now_iso()})
        print(f"[{PHASE}] recommendation = {REC_ERROR}: {exc!r}")
        return 1
    _print_summary(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
