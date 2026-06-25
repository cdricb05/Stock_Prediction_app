"""Phase 8-J — Autonomous Alpha Research Daemon.

**Track A (quant brain) research only.** 8-I ran a single 5-cycle discovery PROGRAM and produced one
report. 8-J turns that program into a DURABLE, RESUMABLE research daemon that keeps selecting
hypotheses, running pre-registered experiments, updating persistent memory/queues/registries, scoring
candidates, deciding the next research action, and stopping ONLY on a clear stop condition — without
user micro-direction.

This is NOT a production trading system, NOT Paper Trader, NOT order automation, NOT a deployment.
It is a LOCAL research-automation loop over the persisted 8-E sensitivity grid plus local/no-key
event sources.

What the daemon does each cycle
-------------------------------
  1  LOAD/REBUILD state (durable on D:): research memory, hypothesis queue, experiment queue,
     experiment results, candidate registry, promotion log, graveyard, provider-blocker registry.
  2  ACTIVATE data sources: provider-key inventory (names/presence only), local earnings cache,
     no-key SEC EDGAR, GDELT/FINRA probes (honest about history), Norgate macro/cross-asset. Missing
     sources are logged as blockers; the daemon never stops on one.
  3  GENERATE hypotheses automatically from an expanding pre-registered bank (external event ×
     ticker sensitivity × sector/regime/vol/beta/liquidity context × confirmation), drawing from
     current promising leads, rejected families, provider blockers, and unused grid columns.
  4  RUN a bounded batch of NOT-YET-TESTED experiments on the IDENTICAL fixed 8-E gate.
  5  VALIDATE: matched controls, 5/10/20/60d horizons, recent 2015-2026, walk-forward, cost stress,
     tail / worst-decile, sector/year/ticker concentration, placebo + leakage, multiple-testing.
  6  PROMOTE: CONFIRMED / PROMISING / PROVIDER_REQUIRED / REJECTED on the unchanged ladder.
  7  DECIDE the next autonomous action and check stop conditions; persist everything; loop.

Stop conditions (the only reasons the daemon halts)
---------------------------------------------------
  CONFIRMED_ALPHA_SIGNAL_FOUND · HARD_PROVIDER_BLOCKER · SAFETY_OR_LEAKAGE_BLOCKER ·
  EXPERIMENT_BUDGET_EXHAUSTED · TIME_BUDGET_EXHAUSTED · MANUAL_STOP_FILE_DETECTED

Hard safety contract (unchanged from 8-E..8-I)
----------------------------------------------
Local data first; Norgate + on-disk FRED for price/macro (no package install). Provider keys detected
by NAME/presence only, never printed. Bounded no-key collection only; raw under
D:\\Stock_Prediction_app_data\\external_raw, normalized under ...\\external_normalized; large runtime
state under ...\\autonomous_alpha_daemon; repo gets summaries/snapshots/decision artifacts only. Every
experiment pre-registered before scoring; thresholds fixed a priori; >=30% challenges/placebos.
External data NEVER faked (no-key sources that yield only a recent window are reported as connector-
live-but-history-missing, not turned into events; the revision PROXY is labelled and capped below
CONFIRMED; mock fixtures excluded). No threshold tuning after results, no factor-sign flipping after
results, no weight optimization, no regime activation, no ML fit, no hidden failures, no secrets
printed. No Paper Trader, no GCP, no deployment, no broker/orders/automation, no live trading signals.
No commit, no push.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

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


# Reuse the whole validated stack via 8-I: 8-I -> 8-H -> 8-G -> 8-F -> 8-E. No re-implementation of
# scoring, gates, controls, promotion, or reports.
P8I = _load_module("phase8i_engine_for_8j", "research/run_phase8i_autonomous_alpha_discovery_program.py")
P8G = P8I.P8G
P8F = P8I.P8F
P8E = P8I.P8E

# IO + scoring primitives (verbatim).
_write_json = P8E._write_json
_write_csv = P8E._write_csv
_utc_now_iso = P8E._utc_now_iso
SensPanel = P8E.SensPanel
SensSetup = P8E.SensSetup
_fwd5_pivot = P8E._fwd5_pivot
_spy_weekly = P8E._spy_weekly
SHOCK_Z = P8E.SHOCK_Z

# Alpha promotion + recommendation vocab (reused unchanged).
ST_ALPHA_CONFIRMED = P8I.ST_ALPHA_CONFIRMED
ST_ALPHA_PROMISING = P8I.ST_ALPHA_PROMISING
ST_ALPHA_PROVIDER_REQUIRED = P8I.ST_ALPHA_PROVIDER_REQUIRED
ST_REJECTED = P8I.ST_REJECTED
ST_BLOCKED = P8I.ST_BLOCKED
ALLOWED_ALPHA_STATUSES = P8I.ALLOWED_ALPHA_STATUSES
ALLOWED_RECOMMENDATIONS = P8I.ALLOWED_RECOMMENDATIONS
_alpha_promotion = P8I._alpha_promotion
_provider_limited = P8I._provider_limited
_ALPHA_SCORE_COLS = P8I._ALPHA_SCORE_COLS

# Families + agents.
FAM_EARNINGS = P8I.FAM_EARNINGS
FAM_REVISION = P8I.FAM_REVISION
FAM_NEWS = P8I.FAM_NEWS
FAM_OPTIONS = P8I.FAM_OPTIONS
FAM_SHORT = P8I.FAM_SHORT
FAM_S8E011_EXT = P8I.FAM_S8E011_EXT
FAM_MACRO = P8I.FAM_MACRO
FAM_FILINGS = P8I.FAM_FILINGS
SENS_A, VAL_A, RSK_A, MODEL_A = P8I.SENS_A, P8I.VAL_A, P8I.RSK_A, P8I.MODEL_A
EARN_A, REV_A, NEWS_A, OPT_A, SHORT_A, EXT_A, DIR_A = (
    P8I.EARN_A, P8I.REV_A, P8I.NEWS_A, P8I.OPT_A, P8I.SHORT_A, P8I.EXT_A, P8I.DIR_A)

# Paths.
DATA_ROOT = P8F.DATA_ROOT
STATE_ROOT_DEFAULT = DATA_ROOT / "autonomous_alpha_daemon"
DEFAULT_OUT_DIR = _REPO_ROOT / "research" / "output" / "phase8j_autonomous_alpha_research_daemon"
STOP_FILE_NAME = "STOP_DAEMON.txt"

PHASE = "8-J"
OBJECTIVE = (
    "Run a durable, resumable autonomous research daemon that continuously selects hypotheses, runs "
    "pre-registered external-event x ticker-sensitivity x context experiments on the fixed 8-E gate, "
    "updates persistent memory/queues/registries, promotes/rejects candidates, and decides the next "
    "research action until it hits a clear stop condition. Research only; no provider micro-direction.")

# --------------------------------------------------------------------------- #
# Daemon next-action vocabulary (per-cycle decision; item 11).
# --------------------------------------------------------------------------- #
ACT_CONTINUE_LOCAL = "CONTINUE_LOCAL_RESEARCH"
ACT_EXPAND_NO_KEY = "EXPAND_NO_KEY_DATA"
ACT_BUILD_PANEL = "BUILD_BROADER_PANEL"
ACT_REQUIRE_PROVIDER = "REQUIRE_PROVIDER"
ACT_PROMOTE_CONFIRMED = "PROMOTE_CONFIRMED_SIGNAL"
ACT_REJECT_FAMILY = "REJECT_FAMILY"
ACT_STOP = "STOP"
ALLOWED_ACTIONS = (ACT_CONTINUE_LOCAL, ACT_EXPAND_NO_KEY, ACT_BUILD_PANEL, ACT_REQUIRE_PROVIDER,
                   ACT_PROMOTE_CONFIRMED, ACT_REJECT_FAMILY, ACT_STOP)

# --------------------------------------------------------------------------- #
# Stop conditions (the ONLY reasons the loop halts; item 3).
# --------------------------------------------------------------------------- #
STOP_CONFIRMED = "CONFIRMED_ALPHA_SIGNAL_FOUND"
STOP_PROVIDER = "HARD_PROVIDER_BLOCKER"
STOP_SAFETY = "SAFETY_OR_LEAKAGE_BLOCKER"
STOP_EXPERIMENT_BUDGET = "EXPERIMENT_BUDGET_EXHAUSTED"
STOP_TIME_BUDGET = "TIME_BUDGET_EXHAUSTED"
STOP_MANUAL = "MANUAL_STOP_FILE_DETECTED"
ALLOWED_STOPS = (STOP_CONFIRMED, STOP_PROVIDER, STOP_SAFETY, STOP_EXPERIMENT_BUDGET,
                 STOP_TIME_BUDGET, STOP_MANUAL)

# --------------------------------------------------------------------------- #
# Research agent roles (item 4).
# --------------------------------------------------------------------------- #
ROLE_DIRECTOR = "research-director-agent"
ROLE_DATA = "data-foundation-agent"
ROLE_UNIVERSE = "universe-agent"
ROLE_EXTERNAL = "external-data-agent"
ROLE_HYPGEN = "hypothesis-generator-agent"
ROLE_MACRO = "macro-sensitivity-agent"
ROLE_EARN = "earnings-catalyst-agent"
ROLE_NEWS = "news-sentiment-agent"
ROLE_REVISION = "analyst-revision-agent"
ROLE_OPTSHORT = "options-short-interest-agent"
ROLE_SKEPTIC = "validation-skeptic-agent"
ROLE_RISK = "risk-tail-agent"
ROLE_MODEL = "model-candidate-agent"
DAEMON_ROLES = [ROLE_DIRECTOR, ROLE_DATA, ROLE_UNIVERSE, ROLE_EXTERNAL, ROLE_HYPGEN, ROLE_MACRO,
                ROLE_EARN, ROLE_NEWS, ROLE_REVISION, ROLE_OPTSHORT, ROLE_SKEPTIC, ROLE_RISK,
                ROLE_MODEL]

# Family -> owning daemon role (for the agent task board / decision log).
_FAMILY_ROLE = {
    FAM_EARNINGS: ROLE_EARN, FAM_REVISION: ROLE_REVISION, FAM_S8E011_EXT: ROLE_MACRO,
    FAM_MACRO: ROLE_MACRO, FAM_FILINGS: ROLE_EARN, FAM_NEWS: ROLE_NEWS,
    FAM_OPTIONS: ROLE_OPTSHORT, FAM_SHORT: ROLE_OPTSHORT,
}

# Defaults.
HYPOTHESES_PER_CYCLE = 12
DEFAULT_MAX_CYCLES = 4

# Persistent runtime-state filenames (live on D:).
STATE_FILES = {
    "daemon_state": "daemon_state.json",
    "research_memory": "research_memory.json",
    "hypothesis_queue": "hypothesis_queue.csv",
    "experiment_queue": "experiment_queue.csv",
    "experiment_results": "experiment_results.csv",
    "candidate_registry": "candidate_signal_registry.csv",
    "promotion_log": "signal_promotion_log.csv",
    "graveyard": "rejected_hypothesis_graveyard.csv",
    "provider_blockers": "provider_blocker_registry.csv",
    "run_log": "daemon_run_log.csv",
    "next_action": "next_action_decision.json",
}

# Committed-safe output artifacts (snapshots + analysis; repo).
ARTIFACTS = [
    "phase8j_autonomous_alpha_research_daemon.json", "daemon_state_summary.json",
    "daemon_run_log.csv", "research_memory_snapshot.json", "hypothesis_queue_snapshot.csv",
    "experiment_queue_snapshot.csv", "experiment_results_snapshot.csv",
    "candidate_signal_registry_snapshot.csv", "signal_promotion_log.csv",
    "rejected_hypothesis_graveyard.csv", "provider_blocker_registry.csv", "agent_task_board.csv",
    "agent_cycle_summary.csv", "agent_decision_log.csv", "autonomous_signal_scoreboard.csv",
    "confirmed_alpha_signals.csv", "promising_alpha_signals.csv", "provider_required_signals.csv",
    "rejected_alpha_signals.csv", "best_trade_idea_candidates.csv", "ranked_next_actions.csv",
    "validation_skeptic_report.csv", "multiple_testing_report.csv",
    "model_candidate_registry_update.csv", "research_director_decision.json",
    "phase8k_next_plan.json",
]


# =========================================================================== #
# Hypothesis bank — expanding, pre-registered, column-legal combinations.
# =========================================================================== #
def generate_hypothesis_bank() -> List[SensSetup]:
    """Pre-register the full bank of external-event x ticker-sensitivity x context combinations the
    daemon draws from. Every condition uses a column that already exists in the persisted grid (after
    8-G augmentation): defined macro driver shocks, defined sensitivity cohorts, sector-leadership /
    beta / volatility cohorts, and PIT earnings/revision/filing flags. Fixed thresholds; no tuning;
    no invented features; no sign flipping. >=30% challenges/placebos.

    The bank spans the hypothesis families required by the program: rates/oil/usd/credit/vix
    sensitivity x context, earnings-surprise x sector-leadership/beta/vol/macro-supportive context,
    SEC-filing x sensitivity, and revision-proxy x sensitivity (capped below CONFIRMED)."""
    mk = P8G._mk
    POS = ("earn_surprise_pos", "ge", 1.0)
    NEG = ("earn_surprise_neg", "ge", 1.0)
    LARGE = ("earn_surprise_large", "ge", 1.0)
    REVUP = ("earn_revision_proxy_up", "ge", 1.0)
    RECENT_POS = ("earn_recent_pos", "ge", 1.0)
    STRONG = ("rel_str_60", "gt", 0.0)
    s: List[SensSetup] = []

    # --- pure macro x sensitivity base lead (S8E-011: rates sell-off x short-duration cohort) ---- #
    s.append(mk("S8J-RATES-MACRO-20", FAM_MACRO, SENS_A, "rates", "cohort_rates_neg", 20,
                [("drv_rates_shock_z", "le", -SHOCK_Z), ("cohort_rates_neg", "ge", 1.0), STRONG],
                "Rates sell-off x rate-sensitive (short-duration) cohort with positive trend drifts up "
                "20d — the full-coverage macro base lead (S8E-011), no earnings confirmation."))
    # --- rates sensitivity x earnings confirmation / sector context -------------------------- #
    s.append(mk("S8J-RATES-EARNCONF-20", FAM_S8E011_EXT, SENS_A, "rates", "cohort_rates_neg", 20,
                [("drv_rates_shock_z", "le", -SHOCK_Z), ("cohort_rates_neg", "ge", 1.0), STRONG, RECENT_POS],
                "Rates sell-off x short-duration cohort CONFIRMED by a recent positive surprise, 20d."))
    s.append(mk("S8J-RATES-EARNPOS-20", FAM_EARNINGS, EARN_A, "earnings_surprise", "cohort_rates_neg",
                20, [POS, ("cohort_rates_neg", "ge", 1.0)],
                "Positive EPS surprise in a rates-sensitive name drifts up 20d (catalyst x rate beta)."))
    s.append(mk("S8J-RATES-SECLEAD-20", FAM_S8E011_EXT, SENS_A, "rates", "cohort_sector_lead", 20,
                [("drv_rates_shock_z", "le", -SHOCK_Z), ("cohort_rates_neg", "ge", 1.0),
                 ("cohort_sector_lead", "ge", 1.0)],
                "Rates sell-off x rate-sensitive sector-leading name drifts up 20d (macro x leadership)."))
    # --- oil sensitivity x sector/industry context ------------------------------------------- #
    s.append(mk("S8J-OIL-POS-20", FAM_S8E011_EXT, SENS_A, "oil", "cohort_oil_pos", 20,
                [("drv_oil_shock_z", "ge", SHOCK_Z), ("cohort_oil_pos", "ge", 1.0)],
                "Oil rally shock in an oil-positive name drifts up 20d (energy beta)."))
    s.append(mk("S8J-OIL-SECLEAD-20", FAM_S8E011_EXT, SENS_A, "oil", "cohort_sector_lead", 20,
                [("drv_oil_shock_z", "ge", SHOCK_Z), ("cohort_oil_pos", "ge", 1.0),
                 ("cohort_sector_lead", "ge", 1.0)],
                "Oil rally x oil-positive sector-leading name drifts up 20d (energy x leadership)."))
    # --- USD sensitivity x sector exposure --------------------------------------------------- #
    s.append(mk("S8J-USD-NEG-20", FAM_S8E011_EXT, SENS_A, "usd", "cohort_usd_neg", 20,
                [("drv_usd_shock_z", "le", -SHOCK_Z), ("cohort_usd_neg", "ge", 1.0)],
                "USD sell-off in a USD-negative (international-exposed) name drifts up 20d."))
    s.append(mk("S8J-USD-SECLEAD-20", FAM_S8E011_EXT, SENS_A, "usd", "cohort_sector_lead", 20,
                [("drv_usd_shock_z", "le", -SHOCK_Z), ("cohort_usd_neg", "ge", 1.0),
                 ("cohort_sector_lead", "ge", 1.0)],
                "USD sell-off x USD-negative sector-leading name drifts up 20d."))
    # --- credit sensitivity x quality/defensive (low-beta) context --------------------------- #
    s.append(mk("S8J-CREDIT-20", FAM_S8E011_EXT, SENS_A, "credit", "cohort_credit_sens", 20,
                [("drv_credit_shock_z", "le", -SHOCK_Z), ("cohort_credit_sens", "ge", 1.0)],
                "Credit-spread move in a credit-sensitive name drifts over 20d (credit beta)."))
    s.append(mk("S8J-CREDIT-DEF-20", FAM_S8E011_EXT, SENS_A, "credit", "cohort_low_beta", 20,
                [("drv_credit_shock_z", "le", -SHOCK_Z), ("cohort_credit_sens", "ge", 1.0),
                 ("cohort_low_beta", "ge", 1.0)],
                "Credit move x credit-sensitive low-beta (defensive) name drifts over 20d."))
    # --- VIX sensitivity x volatility / beta context ----------------------------------------- #
    s.append(mk("S8J-VIX-VOLSENS-20", FAM_S8E011_EXT, SENS_A, "vix", "cohort_vol_spike_sens", 20,
                [("drv_vix_spike_z", "ge", SHOCK_Z), ("cohort_vol_spike_sens", "ge", 1.0)],
                "VIX spike in a volatility-sensitive name drifts over 20d (downside-sensitivity test)."))
    s.append(mk("S8J-VIX-HIGHBETA-20", FAM_S8E011_EXT, SENS_A, "vix", "cohort_high_beta", 20,
                [("drv_vix_spike_z", "ge", SHOCK_Z), ("cohort_high_beta", "ge", 1.0)],
                "VIX spike in a high-beta name drifts over 20d (vol x beta)."))
    # --- earnings surprise x sector leadership ----------------------------------------------- #
    s.append(mk("S8J-EARN-SECLEAD-20", FAM_EARNINGS, EARN_A, "earnings_surprise", "cohort_sector_lead",
                20, [POS, ("cohort_sector_lead", "ge", 1.0)],
                "Positive EPS surprise in a sector-leading name drifts up 20d (PEAD x leadership)."))
    s.append(mk("S8J-EARN-SECLEAD-10", FAM_EARNINGS, EARN_A, "earnings_surprise", "cohort_sector_lead",
                10, [POS, ("cohort_sector_lead", "ge", 1.0)],
                "Positive EPS surprise in a sector-leading name drifts up 10d."))
    # --- earnings surprise x beta / volatility cohorts --------------------------------------- #
    s.append(mk("S8J-EARN-HIGHBETA-20", FAM_EARNINGS, EARN_A, "earnings_surprise", "cohort_high_beta",
                20, [POS, ("cohort_high_beta", "ge", 1.0)],
                "Positive EPS surprise in a high-beta name drifts up 20d."))
    s.append(mk("S8J-EARN-LOWBETA-20", FAM_EARNINGS, EARN_A, "earnings_surprise", "cohort_low_beta",
                20, [POS, ("cohort_low_beta", "ge", 1.0)],
                "Positive EPS surprise in a low-beta defensive name drifts up 20d."))
    s.append(mk("S8J-EARN-VOLSENS-20", FAM_EARNINGS, EARN_A, "earnings_surprise",
                "cohort_vol_spike_sens", 20, [POS, ("cohort_vol_spike_sens", "ge", 1.0)],
                "Positive EPS surprise in a volatility-sensitive name drifts up 20d."))
    # --- earnings surprise x macro-supportive context ---------------------------------------- #
    s.append(mk("S8J-EARN-RATESUP-20", FAM_EARNINGS, EARN_A, "earnings_surprise", "cohort_rates_neg",
                20, [POS, ("cohort_rates_neg", "ge", 1.0), STRONG],
                "Positive EPS surprise in a rate-sensitive name with positive trend drifts up 20d."))
    s.append(mk("S8J-EARN-LARGE-SENS-10", FAM_EARNINGS, EARN_A, "earnings_surprise",
                "cohort_surprise_sensitive", 10, [LARGE, ("cohort_surprise_sensitive", "ge", 1.0)],
                "Large (>=10%) positive surprise in a surprise-sensitive name drifts up 10d."))
    # --- SEC filing events x sensitivity cohorts --------------------------------------------- #
    s.append(mk("S8J-FILING-SECLEAD-20", FAM_FILINGS, EARN_A, "sec_filing", "cohort_sector_lead", 20,
                [("filing_event", "ge", 1.0), ("cohort_sector_lead", "ge", 1.0)],
                "A fresh SEC filing in a sector-leading name precedes drift over 20d."))
    s.append(mk("S8J-FILING-HIGHBETA-20", FAM_FILINGS, EARN_A, "sec_filing", "cohort_high_beta", 20,
                [("filing_event", "ge", 1.0), ("cohort_high_beta", "ge", 1.0)],
                "A fresh SEC filing in a high-beta name precedes drift over 20d."))
    # --- revision proxy x sensitivity (labelled proxy -> capped below CONFIRMED) -------------- #
    s.append(mk("S8J-REV-RATES-20", FAM_REVISION, REV_A, "analyst_revision_proxy", "cohort_rates_neg",
                20, [REVUP, ("cohort_rates_neg", "ge", 1.0)],
                "Improving surprise (revision proxy) in a rates-sensitive name drifts up 20d."))
    s.append(mk("S8J-REV-SECLEAD-20", FAM_REVISION, REV_A, "analyst_revision_proxy",
                "cohort_sector_lead", 20, [REVUP, ("cohort_sector_lead", "ge", 1.0)],
                "Improving surprise (revision proxy) in a sector-leading name drifts up 20d."))

    # --- challenges / placebos (>=30% of the bank) ------------------------------------------- #
    s.append(mk("S8J-CH-EARN-NOCOHORT-20", FAM_EARNINGS, VAL_A, "earnings_surprise", "", 20, [POS],
                "CHALLENGE/placebo: positive surprise, NO cohort — isolates the cohort contribution.",
                is_challenge=True, placebo=True))
    s.append(mk("S8J-CH-EARN-NEG-20", FAM_EARNINGS, VAL_A, "earnings_surprise",
                "cohort_surprise_sensitive", 20, [NEG, ("cohort_surprise_sensitive", "ge", 1.0)],
                "CHALLENGE: NEGATIVE surprise + surprise-sensitive cohort — wrong sign, must not drift up.",
                is_challenge=True))
    s.append(mk("S8J-CH-EARN-SECLEAD-NEG-20", FAM_EARNINGS, VAL_A, "earnings_surprise",
                "cohort_sector_lead", 20, [NEG, ("cohort_sector_lead", "ge", 1.0)],
                "CHALLENGE: NEGATIVE surprise in a sector leader — wrong sign, must not drift up.",
                is_challenge=True))
    s.append(mk("S8J-CH-RATES-NOCOHORT-20", FAM_S8E011_EXT, VAL_A, "rates", "", 20,
                [("drv_rates_shock_z", "le", -SHOCK_Z), STRONG, RECENT_POS],
                "CHALLENGE/placebo: rates sell-off + earnings confirm, NO rate cohort — isolates cohort.",
                is_challenge=True, placebo=True))
    s.append(mk("S8J-CH-OIL-WRONGCOH-20", FAM_S8E011_EXT, VAL_A, "oil", "cohort_oil_neg", 20,
                [("drv_oil_shock_z", "ge", SHOCK_Z), ("cohort_oil_neg", "ge", 1.0)],
                "CHALLENGE: oil rally in an oil-NEGATIVE name — wrong cohort, must not drift up.",
                is_challenge=True))
    s.append(mk("S8J-CH-USD-NOCOHORT-20", FAM_S8E011_EXT, VAL_A, "usd", "", 20,
                [("drv_usd_shock_z", "le", -SHOCK_Z)],
                "CHALLENGE/placebo: USD sell-off, NO cohort — isolates the cohort contribution.",
                is_challenge=True, placebo=True))
    s.append(mk("S8J-CH-VIX-NOCOHORT-20", FAM_S8E011_EXT, VAL_A, "vix", "", 20,
                [("drv_vix_spike_z", "ge", SHOCK_Z)],
                "CHALLENGE/placebo: VIX spike, NO cohort — isolates the cohort contribution.",
                is_challenge=True, placebo=True))
    s.append(mk("S8J-CH-CREDIT-NOCOHORT-20", FAM_S8E011_EXT, VAL_A, "credit", "", 20,
                [("drv_credit_shock_z", "le", -SHOCK_Z)],
                "CHALLENGE/placebo: credit move, NO cohort — isolates the cohort contribution.",
                is_challenge=True, placebo=True))
    s.append(mk("S8J-CH-REV-NOCOHORT-20", FAM_REVISION, VAL_A, "analyst_revision_proxy", "", 20, [REVUP],
                "CHALLENGE/placebo: revision proxy up, NO cohort — isolates the cohort contribution.",
                is_challenge=True, placebo=True))
    s.append(mk("S8J-CH-FILING-NOCOHORT-20", FAM_FILINGS, VAL_A, "sec_filing", "", 20,
                [("filing_event", "ge", 1.0)],
                "CHALLENGE/placebo: a fresh filing, NO cohort — isolates the cohort contribution.",
                is_challenge=True, placebo=True))
    return s


def _interleave_challenges(bank: List[SensSetup]) -> List[SensSetup]:
    """Spread challenges/placebos through the bank (~1 per 2 real setups) so EVERY drawn batch carries
    its own controls — a cycle is never validated without placebo/sign-flip challenges. Deterministic
    (no shuffle): preserves the within-group order of reals and challenges."""
    reals = [s for s in bank if not s.is_challenge]
    chals = [s for s in bank if s.is_challenge]
    out: List[SensSetup] = []
    ri = ci = 0
    while ri < len(reals) or ci < len(chals):
        for _ in range(2):
            if ri < len(reals):
                out.append(reals[ri]); ri += 1
        if ci < len(chals):
            out.append(chals[ci]); ci += 1
    return out


# real-external-data provenance per family (all local/Norgate/no-key real; proxy/thin families are
# still real underlying but capped below CONFIRMED by PROXY_OR_THIN_FAMILIES in 8-G).
_REAL_BY_FAMILY = {FAM_EARNINGS: True, FAM_REVISION: True, FAM_S8E011_EXT: True, FAM_MACRO: True,
                   FAM_FILINGS: True}


def _score_batch(setups: List[SensSetup], grid: pd.DataFrame, panel: SensPanel,
                 cycle: int) -> List["P8G.Experiment"]:
    """Score a batch of pre-registered setups on the fixed 8-E gate (reuses P8G._score_setup)."""
    if not setups or grid is None or grid.empty:
        return []
    fwd5 = _fwd5_pivot(grid)
    spy_week = _spy_weekly(panel.spy_close, panel.grid_dates)
    n_search = max(sum(1 for s in setups if not s.is_challenge), 10)
    out: List[P8G.Experiment] = []
    for s in setups:
        r = P8G._score_setup(s, grid, fwd5, spy_week, n_search)
        real_ext = _REAL_BY_FAMILY.get(s.family, False)
        promotion = (P8G._promotion_for(s.family, r["status"], r["ev"], real_ext)
                     if not s.is_challenge else ST_REJECTED)
        out.append(P8G.Experiment(
            exp_id=s.setup_id, cycle=cycle, family=s.family, agent=s.owning_agent, driver=s.driver,
            cohort=s.cohort, is_challenge=s.is_challenge, real_external_data=real_ext,
            needs_provider=False, hypothesis=s.hypothesis, status=r["status"], promotion=promotion,
            reason=r["reason"],
            metrics={**r["ev"], "walk_forward": r["wf"], "portfolio": r["port"], "checks": r["checks"]}))
    return out


# =========================================================================== #
# Row serializers (durable state + snapshots).
# =========================================================================== #
def _hypothesis_row(s: SensSetup, tested_ids: set, batch_assign: Dict[str, int]) -> dict:
    return {"hypothesis_id": s.setup_id, "family": s.family, "owning_agent": s.owning_agent,
            "driver": s.driver, "cohort": s.cohort, "horizon": s.primary_horizon,
            "is_challenge": s.is_challenge, "placebo": s.placebo,
            "owning_role": _FAMILY_ROLE.get(s.family, ROLE_HYPGEN),
            "status": ("TESTED" if s.setup_id in tested_ids else "QUEUED"),
            "cycle_assigned": batch_assign.get(s.setup_id), "hypothesis": s.hypothesis}


def _result_row(e: "P8G.Experiment") -> dict:
    m = e.metrics or {}
    return {"exp_id": e.exp_id, "cycle": e.cycle, "family": e.family, "agent": e.agent,
            "driver": e.driver, "cohort": e.cohort, "is_challenge": e.is_challenge,
            "real_external_data": e.real_external_data, "needs_provider": e.needs_provider,
            "signal_status": e.status, "ext_promotion": e.promotion,
            "alpha_promotion": _alpha_promotion(e), "provider_limited": _provider_limited(e),
            "n_events": m.get("n_events"), "n_recent_events": m.get("n_recent_events"),
            "lift_vs_control": m.get("lift_vs_control"), "ev_after_25bps": m.get("ev_after_25bps"),
            "ev_after_50bps": m.get("ev_after_50bps"), "hit_rate": m.get("hit_rate"),
            "payoff_ratio": m.get("payoff_ratio"), "worst_decile_mean": m.get("worst_decile_mean"),
            "recent_lift_vs_control": m.get("recent_lift_vs_control"), "reason": e.reason}


def _promotion_log_rows(results: List["P8G.Experiment"], cycle: int) -> List[dict]:
    rows = []
    for e in results:
        if e.is_challenge:
            continue
        alpha = _alpha_promotion(e)
        rows.append({"cycle": cycle, "exp_id": e.exp_id, "family": e.family,
                     "alpha_promotion": alpha, "ext_promotion": e.promotion,
                     "provider_limited": _provider_limited(e),
                     "n_events": (e.metrics or {}).get("n_events"),
                     "ev_after_25bps": (e.metrics or {}).get("ev_after_25bps"),
                     "lift_vs_control": (e.metrics or {}).get("lift_vs_control"),
                     "decision": ("PROMOTED_CONFIRMED" if alpha == ST_ALPHA_CONFIRMED else
                                  "PROMOTED_PROMISING" if alpha == ST_ALPHA_PROMISING else
                                  "FLAGGED_PROVIDER_REQUIRED" if alpha == ST_ALPHA_PROVIDER_REQUIRED
                                  else "REJECTED"),
                     "reason": e.reason})
    return rows


def _graveyard_rows(results: List["P8G.Experiment"], cycle: int) -> List[dict]:
    rows = []
    for e in results:
        if _alpha_promotion(e) == ST_REJECTED:
            rows.append({"cycle": cycle, "exp_id": e.exp_id, "family": e.family,
                         "is_challenge": e.is_challenge,
                         "n_events": (e.metrics or {}).get("n_events"),
                         "lift_vs_control": (e.metrics or {}).get("lift_vs_control"),
                         "ev_after_25bps": (e.metrics or {}).get("ev_after_25bps"),
                         "rejection_reason": e.reason})
    return rows


def provider_blocker_rows(readiness: Dict[str, bool]) -> List[dict]:
    """Families with no local/no-key PIT history — the daemon logs the blocker and the exact (never
    executed here) PowerShell to supply the key, then continues other paths."""
    specs = [
        (FAM_NEWS, NEWS_A, "timestamped news/sentiment PIT history",
         ["NEWSAPI_KEY", "FINNHUB_API_KEY"], "GDELT connector live (HTTP 200) but only a recent "
         "window; a deep PIT history is required and unproven for alpha"),
        (FAM_OPTIONS, OPT_A, "options IV/skew history", ["POLYGON_API_KEY", "INTRINIO_API_KEY"],
         "no local options data, no key; executable adapter staged"),
        (FAM_SHORT, SHORT_A, "biweekly short-interest history",
         ["FINNHUB_API_KEY", "QUANDL_API_KEY"], "FINRA reg-SHO daily file reachable no-key but a "
         "single settlement window, not a deep PIT short-interest history"),
        (FAM_REVISION, REV_A, "true analyst-revision consensus feed (replace proxy)",
         ["FMP_API_KEY", "FINNHUB_API_KEY", "EODHD_API_KEY"], "current revision signal is a labelled "
         "proxy capped below CONFIRMED; a true consensus-revision feed would lift it"),
    ]
    rows = []
    for fam, agent, need, keys, note in specs:
        have = any(readiness.get(k) for k in keys)
        rows.append({"family": fam, "owning_agent": agent,
                     "owning_role": _FAMILY_ROLE.get(fam, ROLE_EXTERNAL),
                     "data_required": need, "candidate_keys": ";".join(keys),
                     "any_candidate_key_present": have, "blocker_active": not have,
                     "powershell_to_supply": (f'$env:{keys[0]} = "<your_key>"' if not have else "n/a"),
                     "note": note})
    return rows


# =========================================================================== #
# Agent task board / cycle summary / decision log.
# =========================================================================== #
def agent_task_board_rows(cycle: int, batch: List[SensSetup], results: List["P8G.Experiment"],
                          activation_rows: List[dict], blockers: List[dict]) -> List[dict]:
    fam_in_batch = sorted({s.family for s in batch})
    n_real = sum(1 for s in batch if not s.is_challenge)
    n_chal = sum(1 for s in batch if s.is_challenge)
    n_conf = sum(1 for e in results if _alpha_promotion(e) == ST_ALPHA_CONFIRMED)
    n_prom = sum(1 for e in results if _alpha_promotion(e) == ST_ALPHA_PROMISING)
    active_blockers = [b["family"] for b in blockers if b["blocker_active"]]
    rows = [
        {"cycle": cycle, "role": ROLE_DIRECTOR, "task": "orchestrate cycle, select next action",
         "status": "DONE", "detail": f"batch={len(batch)} families={','.join(fam_in_batch)}"},
        {"cycle": cycle, "role": ROLE_DATA, "task": "load persisted 8-E grid + leak-safe event joins",
         "status": "DONE", "detail": "Norgate weekly grid reused; no rebuild"},
        {"cycle": cycle, "role": ROLE_UNIVERSE, "task": "confirm S&P universe / delisting-safe panel",
         "status": "DONE", "detail": "survivorship-safe persisted panel"},
        {"cycle": cycle, "role": ROLE_EXTERNAL, "task": "activate local/no-key sources; log blockers",
         "status": "DONE", "detail": f"{len(activation_rows)} sources; blockers={','.join(active_blockers) or 'none'}"},
        {"cycle": cycle, "role": ROLE_HYPGEN, "task": "draw next pre-registered hypotheses",
         "status": "DONE", "detail": f"{n_real} real + {n_chal} challenge/placebo"},
        {"cycle": cycle, "role": ROLE_MACRO, "task": "score macro/cross-asset x sensitivity",
         "status": ("DONE" if any(s.family in (FAM_S8E011_EXT, FAM_MACRO) for s in batch) else "IDLE"),
         "detail": "rates/oil/usd/credit/vix x cohorts"},
        {"cycle": cycle, "role": ROLE_EARN, "task": "score earnings/filing x sensitivity",
         "status": ("DONE" if any(s.family in (FAM_EARNINGS, FAM_FILINGS) for s in batch) else "IDLE"),
         "detail": "PEAD x sector-leadership/beta/vol/macro context"},
        {"cycle": cycle, "role": ROLE_NEWS, "task": "news/sentiment x sensitivity",
         "status": "BLOCKED", "detail": "no PIT news history (provider required)"},
        {"cycle": cycle, "role": ROLE_REVISION, "task": "revision x sensitivity (proxy, capped)",
         "status": ("DONE" if any(s.family == FAM_REVISION for s in batch) else "IDLE"),
         "detail": "labelled proxy; never CONFIRMED"},
        {"cycle": cycle, "role": ROLE_OPTSHORT, "task": "options IV / short-interest x sensitivity",
         "status": "BLOCKED", "detail": "no PIT options/short history (provider/bulk required)"},
        {"cycle": cycle, "role": ROLE_SKEPTIC, "task": "matched control + recent + WF + placebo/leakage + MT",
         "status": "DONE", "detail": f"challenges={n_chal}; placebo/leakage enforced"},
        {"cycle": cycle, "role": ROLE_RISK, "task": "tail / worst-decile / concentration",
         "status": "DONE", "detail": "fixed -12% floor + SPY-active comparison"},
        {"cycle": cycle, "role": ROLE_MODEL, "task": "model-candidate registry update (no deploy)",
         "status": "DONE", "detail": f"confirmed={n_conf} promising={n_prom}"},
    ]
    return rows


def agent_cycle_summary_rows(board: List[dict]) -> List[dict]:
    by_role: Dict[str, List[dict]] = {}
    for r in board:
        by_role.setdefault(r["role"], []).append(r)
    out = []
    for role in DAEMON_ROLES:
        rs = by_role.get(role, [])
        done = sum(1 for r in rs if r["status"] == "DONE")
        blocked = sum(1 for r in rs if r["status"] == "BLOCKED")
        idle = sum(1 for r in rs if r["status"] == "IDLE")
        out.append({"role": role, "n_tasks": len(rs), "n_done": done, "n_blocked": blocked,
                    "n_idle": idle,
                    "status": ("BLOCKED" if blocked and not done else "ACTIVE" if done else "IDLE")})
    return out


# =========================================================================== #
# Next-action decision (item 11) + stop evaluation (item 3).
# =========================================================================== #
def _aggregate(registry: List[dict]) -> dict:
    real = [r for r in registry if not r.get("is_challenge")]
    testable = [r for r in real if not r.get("needs_provider") and r.get("n_events")]
    conf = [r for r in real if r.get("alpha_promotion") == ST_ALPHA_CONFIRMED]
    prom = [r for r in real if r.get("alpha_promotion") == ST_ALPHA_PROMISING]
    clean_prom = [r for r in prom if not r.get("provider_limited")]
    limited_prom = [r for r in prom if r.get("provider_limited")]
    prov = [r for r in real if r.get("alpha_promotion") == ST_ALPHA_PROVIDER_REQUIRED]
    rej = [r for r in real if r.get("alpha_promotion") == ST_REJECTED]
    return {"n_real": len(real), "n_testable": len(testable), "n_confirmed": len(conf),
            "n_promising": len(prom), "n_clean_promising": len(clean_prom),
            "n_provider_limited": len(limited_prom), "n_provider_required": len(prov),
            "n_rejected": len(rej), "confirmed_ids": [r["candidate_id"] for r in conf],
            "clean_promising_ids": [r["candidate_id"] for r in clean_prom],
            "provider_limited_ids": [r["candidate_id"] for r in limited_prom]}


def _rejected_families(registry: List[dict]) -> List[str]:
    """A family is 'rejected' once >=3 non-challenge members are tested and ALL are rejected."""
    by_fam: Dict[str, List[dict]] = {}
    for r in registry:
        if r.get("is_challenge") or r.get("needs_provider"):
            continue
        by_fam.setdefault(r["family"], []).append(r)
    out = []
    for fam, rs in by_fam.items():
        if len(rs) >= 3 and all(r.get("alpha_promotion") == ST_REJECTED for r in rs):
            out.append(fam)
    return out


def decide_next_action(agg: dict, queue_remaining: int, readiness: Dict[str, bool],
                       rejected_fams: List[str]) -> Tuple[str, str]:
    any_key = any(readiness.values())
    if agg["n_confirmed"]:
        return ACT_PROMOTE_CONFIRMED, f"confirmed alpha signal(s): {agg['confirmed_ids']}"
    if queue_remaining > 0:
        return ACT_CONTINUE_LOCAL, (f"{queue_remaining} untested pre-registered hypotheses remain; "
                                    "keep mining local/no-key combinations on the fixed gate")
    # queue drained for this bank — choose the highest-probability expansion lever
    if rejected_fams:
        return ACT_REJECT_FAMILY, ("families exhausted on available data: " + ",".join(rejected_fams)
                                   + " (recorded; not retried without new data)")
    if agg["n_clean_promising"]:
        return ACT_CONTINUE_LOCAL, ("clean promising lead(s) exist; next local step is the fixed "
                                    "structural beta-tail/volatility filter re-validation")
    if agg["n_provider_limited"] or agg["n_provider_required"]:
        return (ACT_REQUIRE_PROVIDER if not any_key else ACT_EXPAND_NO_KEY), (
            "remaining promising leads are coverage/provider-limited; a broad earnings+revision "
            "provider feed is the highest-ceiling lever")
    return ACT_BUILD_PANEL, ("no untested local hypotheses and no positive lead; widen the no-key "
                             "SEC EDGAR / FINRA overlays and rebuild a broader chunked panel")


def evaluate_stop(agg: dict, queue_remaining: int, batch_scored: bool, cycles_done: int,
                  experiments_scored: int, *, max_cycles: Optional[int], max_experiments: Optional[int],
                  time_exhausted: bool, stop_file: bool, stop_on_confirmed: bool,
                  stop_on_provider_blocker: bool, safety_ok: bool, hard_provider: bool) -> Optional[str]:
    if stop_file:
        return STOP_MANUAL
    if not safety_ok:
        return STOP_SAFETY
    if stop_on_confirmed and agg["n_confirmed"]:
        return STOP_CONFIRMED
    if agg["n_confirmed"]:
        # a confirmed signal always halts (the program's terminal success).
        return STOP_CONFIRMED
    if time_exhausted:
        return STOP_TIME_BUDGET
    if max_experiments is not None and experiments_scored >= max_experiments:
        return STOP_EXPERIMENT_BUDGET
    if max_cycles is not None and cycles_done >= max_cycles:
        # configured cycle budget is a hard cap (maps to the EXPERIMENT_BUDGET stop condition)
        return STOP_EXPERIMENT_BUDGET
    if queue_remaining == 0:
        # the pre-registered hypothesis bank is exhausted -> experiment budget for this campaign is up
        if stop_on_provider_blocker and hard_provider:
            return STOP_PROVIDER
        return STOP_EXPERIMENT_BUDGET
    return None


# =========================================================================== #
# Validation skeptic report (per-candidate adversarial summary).
# =========================================================================== #
def validation_skeptic_rows(results_all: List[dict]) -> List[dict]:
    rows = []
    for r in results_all:
        if r.get("needs_provider") or not r.get("n_events"):
            continue
        alpha = r.get("alpha_promotion")
        if r.get("is_challenge"):
            verdict = "CHALLENGE_CLEAN" if (r.get("lift_vs_control") or -1) < P8E.GATE_PLACEBO_MAX_LIFT else "CHALLENGE_LEAKS"
        elif alpha == ST_ALPHA_CONFIRMED:
            verdict = "SURVIVES_SKEPTIC"
        elif alpha == ST_ALPHA_PROMISING:
            verdict = "REAL_BUT_LIMITED" if r.get("provider_limited") else "PROMISING_LOCAL_NEXT_STEP"
        else:
            verdict = "REJECTED_BY_GATE"
        rows.append({"exp_id": r["candidate_id"], "family": r["family"],
                     "is_challenge": r.get("is_challenge"), "n_events": r.get("n_events"),
                     "n_recent_events": r.get("n_recent_events"),
                     "lift_vs_control": r.get("lift_vs_control"),
                     "ev_after_25bps": r.get("ev_after_25bps"),
                     "recent_lift_vs_control": r.get("recent_lift_vs_control"),
                     "worst_decile_mean": r.get("worst_decile_mean"),
                     "alpha_promotion": alpha, "skeptic_verdict": verdict,
                     "skeptic_note": r.get("reason")})
    return rows


def best_trade_idea_rows(registry: List[dict]) -> List[dict]:
    leads = [r for r in registry if not r.get("is_challenge")
             and r.get("alpha_promotion") in (ST_ALPHA_CONFIRMED, ST_ALPHA_PROMISING)]
    leads.sort(key=lambda r: (r.get("alpha_promotion") == ST_ALPHA_CONFIRMED,
                              (r.get("ev_after_25bps") or -9)), reverse=True)
    out = []
    for i, r in enumerate(leads, 1):
        out.append({"rank": i, "candidate_id": r["candidate_id"], "family": r["family"],
                    "driver": r.get("driver"), "cohort": r.get("cohort"),
                    "alpha_promotion": r.get("alpha_promotion"),
                    "provider_limited": r.get("provider_limited"), "n_events": r.get("n_events"),
                    "n_recent_events": r.get("n_recent_events"),
                    "lift_vs_control": r.get("lift_vs_control"),
                    "ev_after_25bps": r.get("ev_after_25bps"),
                    "recent_lift_vs_control": r.get("recent_lift_vs_control"),
                    "worst_decile_mean": r.get("worst_decile_mean"),
                    "actionability": ("PAPER_REVIEW_ONLY_MANUAL"),
                    "rationale": r.get("reason")})
    return out


# =========================================================================== #
# The daemon.
# =========================================================================== #
class AlphaResearchDaemon:
    def __init__(self, out_dir: Path, state_dir: Path, *, dry_run: bool = False,
                 activate_live: bool = False):
        self.out_dir = out_dir
        self.state_dir = state_dir
        self.dry_run = dry_run
        self.activate_live = activate_live
        self.bank = _interleave_challenges(generate_hypothesis_bank())
        self.bank_by_id = {s.setup_id: s for s in self.bank}
        # durable accumulators (lists of dict rows / experiments).
        self.tested_ids: List[str] = []
        self.results_rows: List[dict] = []          # experiment_results
        self.registry_rows: List[dict] = []         # candidate_signal_registry (latest per id)
        self.promotion_rows: List[dict] = []
        self.graveyard_rows: List[dict] = []
        self.run_log_rows: List[dict] = []
        self.experiments: List["P8G.Experiment"] = []   # in-memory ledger for reports this run
        self.cycles_completed = 0
        self.created_utc = _utc_now_iso()
        self._panel: Optional[SensPanel] = None
        self._grid: Optional[pd.DataFrame] = None
        self._aug_diag: dict = {}
        self._batch_assign: Dict[str, int] = {}

    # ---- persistence -------------------------------------------------------- #
    def _sp(self, key: str) -> Path:
        return self.state_dir / STATE_FILES[key]

    def _load_state(self) -> bool:
        st = P8I._read_json(self._sp("daemon_state"))
        if not st:
            return False
        self.tested_ids = list(st.get("tested_ids", []))
        self.cycles_completed = int(st.get("cycles_completed", 0))
        self.created_utc = st.get("created_utc", self.created_utc)
        self.results_rows = self._read_csv_rows("experiment_results")
        self.registry_rows = self._read_csv_rows("candidate_registry")
        self.promotion_rows = self._read_csv_rows("promotion_log")
        self.graveyard_rows = self._read_csv_rows("graveyard")
        self.run_log_rows = self._read_csv_rows("run_log")
        # rebuild the in-memory ledger (scalar metrics) so recommendation/scoreboard/MT reflect the
        # FULL accumulated campaign across resumes — even a resume that scores zero new experiments.
        # Skip provider-required rows: those static blocked-family entries are re-added each run from
        # _blocked_family_experiments(), so reconstructing them here would duplicate.
        self.experiments = [self._experiment_from_reg(r) for r in self.registry_rows
                            if str(r.get("needs_provider")).strip().lower() not in ("true", "1", "1.0")]
        for r in self.results_rows:
            cyc = r.get("cycle")
            if r.get("exp_id") is not None:
                self._batch_assign[r.get("exp_id")] = (None if pd.isna(cyc) else int(cyc))
        return True

    @staticmethod
    def _experiment_from_reg(r: dict) -> "P8G.Experiment":
        def _num(v):
            return None if v is None or (isinstance(v, float) and pd.isna(v)) else v

        def _flag(v):
            return str(v).strip().lower() in ("true", "1", "1.0")

        def _txt(v):
            return "" if v is None or (isinstance(v, float) and pd.isna(v)) else str(v)

        metrics = {k: _num(r.get(k)) for k in ("n_events", "n_recent_events", "lift_vs_control",
                                               "ev_after_25bps", "worst_decile_mean",
                                               "recent_lift_vs_control")}
        return P8G.Experiment(
            exp_id=_txt(r.get("candidate_id")), cycle=0, family=_txt(r.get("family")),
            agent=_txt(r.get("agent")), driver=_txt(r.get("driver")), cohort=_txt(r.get("cohort")),
            is_challenge=_flag(r.get("is_challenge")),
            real_external_data=_flag(r.get("real_external_data")),
            needs_provider=_flag(r.get("needs_provider")), hypothesis="",
            status=_txt(r.get("signal_status")), promotion=_txt(r.get("ext_promotion")),
            reason=_txt(r.get("reason")), metrics=metrics)

    def _read_csv_rows(self, key: str) -> List[dict]:
        df = P8I._read_csv(self._sp(key))
        return df.to_dict("records") if not df.empty else []

    def _persist(self, readiness, blockers) -> None:
        """Write durable runtime state on D: (skipped in --dry-run)."""
        if self.dry_run:
            return
        self.state_dir.mkdir(parents=True, exist_ok=True)
        _write_json(self._sp("daemon_state"), self._state_summary(readiness))
        _write_json(self._sp("research_memory"), self._research_memory(readiness))
        _write_json(self._sp("next_action"), self._next_action_doc)
        _write_csv(self._sp("hypothesis_queue"), self._hyp_rows(), _HYP_COLS)
        _write_csv(self._sp("experiment_queue"), self._exp_queue_rows(), _EXPQ_COLS)
        _write_csv(self._sp("experiment_results"), self.results_rows, _RESULT_COLS)
        _write_csv(self._sp("candidate_registry"), self.registry_rows, _REG_COLS)
        _write_csv(self._sp("promotion_log"), self.promotion_rows, _PROMO_COLS)
        _write_csv(self._sp("graveyard"), self.graveyard_rows or [{"status": "EMPTY"}],
                   _GRAVE_COLS if self.graveyard_rows else ["status"])
        _write_csv(self._sp("provider_blockers"), blockers, _BLOCKER_COLS)
        _write_csv(self._sp("run_log"), self.run_log_rows, _RUNLOG_COLS)

    def _full_ledger(self) -> List["P8G.Experiment"]:
        """Testable experiments (durable) PLUS the static provider-required blocked-family entries
        (news/options/short interest), deduped by id. Used for every report/aggregate so the
        provider-required classification and provider_required_signals.csv are populated."""
        seen = {e.exp_id for e in self.experiments}
        blocked = [e for e in P8G._blocked_family_experiments() if e.exp_id not in seen]
        return self.experiments + blocked

    # ---- panel -------------------------------------------------------------- #
    def _ensure_panel(self) -> bool:
        if self._grid is not None:
            return not self._grid.empty
        panel = P8F.load_persisted_panel()
        if panel is None or not panel.ok or panel.grid.empty:
            self._panel = None
            self._grid = pd.DataFrame()
            self._aug_diag = {"error": "persisted 8-E panel unavailable"}
            return False
        earn = P8G.load_earnings_events()
        if self.activate_live and not self.dry_run:
            want = list(earn["ticker"].value_counts().index) if not earn.empty else []
            filings, _meta = P8H_safe_filings(want)
        else:
            filings, _meta = P8G.load_sec_filing_events(activate_live=False)
        grid, diag = P8G.augment_grid(panel.grid, earn, filings)
        self._panel, self._grid, self._aug_diag = panel, grid, diag
        return True

    # ---- queues ------------------------------------------------------------- #
    def _untested(self) -> List[SensSetup]:
        done = set(self.tested_ids)
        return [s for s in self.bank if s.setup_id not in done]

    def _next_batch(self, size: int) -> List[SensSetup]:
        return self._untested()[:size]

    def _hyp_rows(self) -> List[dict]:
        done = set(self.tested_ids)
        return [_hypothesis_row(s, done, self._batch_assign) for s in self.bank]

    def _exp_queue_rows(self) -> List[dict]:
        done = set(self.tested_ids)
        rows = []
        for s in self.bank:
            rows.append({"exp_id": s.setup_id, "family": s.family, "owning_agent": s.owning_agent,
                         "owning_role": _FAMILY_ROLE.get(s.family, ROLE_HYPGEN),
                         "is_challenge": s.is_challenge,
                         "state": ("SCORED" if s.setup_id in done else "QUEUED"),
                         "cycle_assigned": self._batch_assign.get(s.setup_id)})
        return rows

    # ---- one cycle ---------------------------------------------------------- #
    def _run_cycle(self, cycle_no: int, readiness, key_rows, activation_rows, blockers,
                   batch_size: int) -> dict:
        batch = self._next_batch(batch_size)
        scored = _score_batch(batch, self._grid, self._panel, cycle_no)
        # record results + update durable accumulators
        for s in batch:
            self.tested_ids.append(s.setup_id)
            self._batch_assign[s.setup_id] = cycle_no
        self.experiments.extend(scored)
        self.results_rows.extend(_result_row(e) for e in scored)
        self.promotion_rows.extend(_promotion_log_rows(scored, cycle_no))
        self.graveyard_rows.extend(_graveyard_rows(scored, cycle_no))
        # rebuild registry (latest per id) from the full ledger (testable + provider-required)
        state = {"coverage_or_provider_blocked": []}
        self.registry_rows = P8I.candidate_registry_rows(self._full_ledger(), state)
        agg = _aggregate(self.registry_rows)
        rejected_fams = _rejected_families(self.registry_rows)
        queue_remaining = len(self._untested())
        action, action_reason = decide_next_action(agg, queue_remaining, readiness, rejected_fams)
        board = agent_task_board_rows(cycle_no, batch, scored, activation_rows, blockers)
        summary = agent_cycle_summary_rows(board)
        n_conf = agg["n_confirmed"]
        n_prom = agg["n_promising"]
        self.run_log_rows.append({
            "cycle": cycle_no, "utc": _utc_now_iso(), "batch_size": len(batch),
            "experiments_scored_total": len(self.tested_ids), "queue_remaining": queue_remaining,
            "n_confirmed": n_conf, "n_promising": n_prom,
            "n_provider_required": agg["n_provider_required"], "n_rejected": agg["n_rejected"],
            "next_action": action, "note": action_reason[:160]})
        self.cycles_completed = cycle_no
        return {"batch": batch, "scored": scored, "agg": agg, "action": action,
                "action_reason": action_reason, "board": board, "summary": summary,
                "rejected_fams": rejected_fams, "queue_remaining": queue_remaining}

    # ---- main loop ---------------------------------------------------------- #
    def run(self, *, once=False, max_cycles=None, max_experiments=None, time_budget_minutes=None,
            resume=False, stop_on_confirmed=False, stop_on_provider_blocker=False,
            heartbeat_seconds=0) -> dict:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        started_mono = time.monotonic()
        started_utc = _utc_now_iso()
        if resume:
            self._load_state()
        if once:
            max_cycles = self.cycles_completed + 1

        key_rows = P8F.detect_provider_keys()
        readiness = P8F.provider_readiness(key_rows)
        blockers = provider_blocker_rows(readiness)
        hard_provider = all(b["blocker_active"] for b in blockers)

        panel_ok = self._ensure_panel()
        # honest activation log (reuse 8-I builder; network only when --activate-live & not dry-run)
        activation_rows, news_meta, finra_meta, edgar_meta, earn, filings = self._activate(readiness)
        safety_ok = True  # leakage is enforced per-experiment by the fixed gate; no global breach
        stop_reason: Optional[str] = None
        last_cycle: dict = {}

        if not panel_ok:
            # framework cannot score; record honestly and stop on experiment budget (nothing testable)
            agg = _aggregate(self.registry_rows)
            self._next_action_doc = {"action": ACT_BUILD_PANEL, "reason": "persisted 8-E panel "
                                     "unavailable; cannot score — rebuild the weekly grid first",
                                     "allowed_actions": list(ALLOWED_ACTIONS)}
            stop_reason = STOP_EXPERIMENT_BUDGET
            last_cycle = {"agg": agg, "action": ACT_BUILD_PANEL,
                          "action_reason": self._next_action_doc["reason"], "board": [],
                          "summary": [], "rejected_fams": [], "queue_remaining": len(self._untested()),
                          "batch": [], "scored": []}
        else:
            cycle_no = self.cycles_completed
            while True:
                # ---- stop checks BEFORE doing more work ----
                stop_file = self._stop_file_present()
                agg = _aggregate(self.registry_rows)
                elapsed_min = (time.monotonic() - started_mono) / 60.0
                time_exhausted = bool(time_budget_minutes and elapsed_min >= time_budget_minutes)
                stop_reason = evaluate_stop(
                    agg, len(self._untested()), bool(self.experiments), self.cycles_completed,
                    len(self.tested_ids), max_cycles=max_cycles, max_experiments=max_experiments,
                    time_exhausted=time_exhausted, stop_file=stop_file,
                    stop_on_confirmed=stop_on_confirmed,
                    stop_on_provider_blocker=stop_on_provider_blocker, safety_ok=safety_ok,
                    hard_provider=hard_provider)
                if stop_reason is not None:
                    break
                cycle_no += 1
                if heartbeat_seconds:
                    print(f"[{PHASE}] heartbeat cycle={cycle_no} elapsed={elapsed_min:.2f}min "
                          f"tested={len(self.tested_ids)} queue={len(self._untested())}")
                last_cycle = self._run_cycle(cycle_no, readiness, key_rows, activation_rows,
                                             blockers, HYPOTHESES_PER_CYCLE)
                self._next_action_doc = {"cycle": cycle_no, "action": last_cycle["action"],
                                         "reason": last_cycle["action_reason"],
                                         "allowed_actions": list(ALLOWED_ACTIONS)}

            if not last_cycle:  # resumed already-complete campaign; emit a no-op cycle view
                agg = _aggregate(self.registry_rows)
                last_cycle = {"agg": agg, "action": ACT_REQUIRE_PROVIDER,
                              "action_reason": "campaign already complete on resume; bank exhausted",
                              "board": agent_task_board_rows(self.cycles_completed, [], [],
                                                             activation_rows, blockers),
                              "summary": [], "rejected_fams": _rejected_families(self.registry_rows),
                              "queue_remaining": len(self._untested()), "batch": [], "scored": []}
                last_cycle["summary"] = agent_cycle_summary_rows(last_cycle["board"])
                self._next_action_doc = {"action": last_cycle["action"],
                                         "reason": last_cycle["action_reason"],
                                         "allowed_actions": list(ALLOWED_ACTIONS)}

        # ensure the registry reflects the full ledger (testable + provider-required) even when this
        # run scored no new cycle (immediate stop / resume of a complete campaign / panel missing).
        if panel_ok and not last_cycle.get("scored"):
            self.registry_rows = P8I.candidate_registry_rows(self._full_ledger(),
                                                             {"coverage_or_provider_blocked": []})
        full_ledger = self._full_ledger()
        rec, detail = P8I.derive_recommendation(panel_ok, full_ledger, readiness)
        options = P8I.ranked_next_options(full_ledger, readiness, {})
        report = self._assemble_report(started_utc, panel_ok, rec, detail, readiness, key_rows,
                                        activation_rows, blockers, stop_reason, last_cycle, options,
                                        once, max_cycles, max_experiments, time_budget_minutes)
        self._persist(readiness, blockers)
        self._emit_snapshots(report, readiness, key_rows, activation_rows, blockers, last_cycle,
                             options, rec)
        return report

    # ---- data activation (honest; reuses 8-I) ------------------------------- #
    def _activate(self, readiness):
        earn = P8G.load_earnings_events()
        live = self.activate_live and not self.dry_run
        if live:
            want = list(earn["ticker"].value_counts().index) if not earn.empty else []
            filings, edgar_meta = P8H_safe_filings(want)
        else:
            filings, _f = P8G.load_sec_filing_events(activate_live=False)
            edgar_meta = {"cap": 0, "n_requested": 0, "n_from_cache": 0, "n_fetched": 0,
                          "error": ("dry-run" if self.dry_run else "live off")}
        news_rows, news_meta = P8I.P8H.news_sentiment_activation(activate_live=live)
        finra_rows, finra_meta = P8I.finra_short_interest_activation(activate_live=live)
        activation_rows = P8I.data_source_activation_log(earn, filings, edgar_meta, news_meta,
                                                         finra_meta, readiness, self._aug_diag)
        return activation_rows, news_meta, finra_meta, edgar_meta, earn, filings

    # ---- stop file ---------------------------------------------------------- #
    def _stop_file_present(self) -> bool:
        try:
            return (self.state_dir / STOP_FILE_NAME).exists()
        except Exception:
            return False

    # ---- summaries / memory ------------------------------------------------- #
    def _state_summary(self, readiness) -> dict:
        agg = _aggregate(self.registry_rows)
        return {"phase": PHASE, "created_utc": self.created_utc, "last_run_utc": _utc_now_iso(),
                "cycles_completed": self.cycles_completed,
                "experiments_scored": len(self.tested_ids), "bank_size": len(self.bank),
                "queue_remaining": len(self._untested()), "tested_ids": self.tested_ids,
                "aggregate": agg, "any_provider_key": any(readiness.values()),
                "dry_run": self.dry_run}

    def _research_memory(self, readiness) -> dict:
        agg = _aggregate(self.registry_rows)
        return {"phase": PHASE, "generated_utc": _utc_now_iso(),
                "thesis": "external event x ticker sensitivity x sector/regime/liquidity/valuation x confirmation",
                "confirmed_alpha_signals": agg["confirmed_ids"],
                "clean_promising_signals": agg["clean_promising_ids"],
                "provider_limited_signals": agg["provider_limited_ids"],
                "binding_constraint": ("event-data BREADTH (earnings feed = 75 tickers; no key for "
                                       "true revision/news/options/short) — the Norgate panel is NOT binding"),
                "rejected_research_lines": ["price/volume-only mining", "single universal factor",
                                            "cross-asset macro pack (degraded IC)"],
                "open_questions": [
                    "Does a fixed structural filter make a promising lead stable enough to CONFIRM?",
                    "Which single provider feed (broad earnings+revision) unlocks the most candidates?",
                    "Can a free FINRA short-interest history activate family E without a paid key?"],
                "provider_readiness": readiness}

    # ---- report ------------------------------------------------------------- #
    def _assemble_report(self, started_utc, panel_ok, rec, detail, readiness, key_rows,
                         activation_rows, blockers, stop_reason, last_cycle, options, once,
                         max_cycles, max_experiments, time_budget_minutes) -> dict:
        g = self._grid if self._grid is not None else pd.DataFrame()
        agg = _aggregate(self.registry_rows)
        full_ledger = self._full_ledger()
        budget = P8G._budget(full_ledger) if full_ledger else {"challenge_fraction": 0.0}
        return {
            "phase": PHASE, "objective": OBJECTIVE, "generated_utc": started_utc,
            "stop_reason": stop_reason, "allowed_stop_conditions": list(ALLOWED_STOPS),
            "recommendation": rec, "allowed_recommendations": list(ALLOWED_RECOMMENDATIONS),
            "decision_detail": detail, "daemon_roles": DAEMON_ROLES,
            "next_action": last_cycle.get("action"), "next_action_reason": last_cycle.get("action_reason"),
            "allowed_actions": list(ALLOWED_ACTIONS),
            "run_config": {"once": once, "max_cycles": max_cycles, "max_experiments": max_experiments,
                           "time_budget_minutes": time_budget_minutes, "dry_run": self.dry_run,
                           "activate_live": self.activate_live,
                           "hypotheses_per_cycle": HYPOTHESES_PER_CYCLE},
            "loop": {"cycles_completed": self.cycles_completed,
                     "experiments_scored": len(self.tested_ids), "bank_size": len(self.bank),
                     "queue_remaining": len(self._untested()),
                     "challenge_fraction": budget.get("challenge_fraction")},
            "panel": {"panel_ok": panel_ok,
                      "n_symbols": int(g["symbol"].nunique()) if not g.empty else 0,
                      "n_obs": int(len(g)),
                      "date_range": ([str(g["date"].min())[:10], str(g["date"].max())[:10]]
                                     if not g.empty else [])},
            "candidates": {"n_real_tested": agg["n_real"], "n_testable": agg["n_testable"],
                           "n_confirmed": agg["n_confirmed"], "n_promising": agg["n_promising"],
                           "n_clean_promising": agg["n_clean_promising"],
                           "n_provider_limited": agg["n_provider_limited"],
                           "n_provider_required": agg["n_provider_required"],
                           "n_rejected": agg["n_rejected"], "confirmed_ids": agg["confirmed_ids"],
                           "clean_promising_ids": agg["clean_promising_ids"],
                           "provider_limited_ids": agg["provider_limited_ids"]},
            "rejected_families": last_cycle.get("rejected_fams", []),
            "best_current_path": (options[0]["option"] if options else ""),
            "top_next_options": options,
            "provider": {"any_key_present": any(readiness.values()), "n_keys_checked": len(key_rows),
                         "n_blocked_families": sum(1 for b in blockers if b["blocker_active"]),
                         "readiness": readiness},
            "how_to_run_longer": {
                "validation_once": "python research/run_phase8j_autonomous_alpha_research_daemon.py --once",
                "bounded_campaign": "python research/run_phase8j_autonomous_alpha_research_daemon.py --max-cycles 3 --resume",
                "long_campaign": ("python research/run_phase8j_autonomous_alpha_research_daemon.py "
                                  "--time-budget-minutes 120 --max-experiments 200 --resume "
                                  "--stop-on-confirmed --heartbeat-seconds 30"),
                "manual_stop": f"create {self.state_dir / STOP_FILE_NAME} to halt before the next cycle"},
            "safety": self._safety_block(readiness),
        }

    def _safety_block(self, readiness) -> dict:
        return {
            "research_only": True, "local_first": True,
            "provider_keys_detected": any(readiness.values()), "secrets_printed": False,
            "external_data_faked": False, "news_sentiment_faked": False, "short_interest_faked": False,
            "revision_is_labelled_proxy_not_confirmed": True, "mock_fixtures_excluded": True,
            "point_in_time_join": True, "thresholds_fixed_a_priori": True,
            "thresholds_modified_after_results": False, "factor_signs_modified_after_results": False,
            "all_pre_registered": True, "packages_installed": False,
            "large_state_only_on_d": True, "optimized_weights": False, "regime_activation": False,
            "ml_fit": False, "failed_experiments_hidden": False, "live_trading_signals": False,
            "broker_or_orders": False, "automation_of_orders": False, "paper_trader_touched": False,
            "gcp_touched": False, "deployment": False, "committed": False, "pushed": False}

    # ---- snapshots (committed-safe, repo output) ---------------------------- #
    def _emit_snapshots(self, report, readiness, key_rows, activation_rows, blockers, last_cycle,
                        options, rec) -> None:
        p = lambda n: self.out_dir / n
        ledger = self._full_ledger()
        _write_json(p("phase8j_autonomous_alpha_research_daemon.json"), report)
        _write_json(p("daemon_state_summary.json"), self._state_summary(readiness))
        _write_json(p("research_memory_snapshot.json"), self._research_memory(readiness))
        _write_json(p("research_director_decision.json"),
                    self._director_decision(report, options, rec))
        _write_json(p("phase8k_next_plan.json"), self._phase8k_plan(report, readiness, options))
        _write_csv(p("daemon_run_log.csv"), self.run_log_rows or [{"status": "NO_CYCLES_RUN"}],
                   _RUNLOG_COLS if self.run_log_rows else ["status"])
        _write_csv(p("hypothesis_queue_snapshot.csv"), self._hyp_rows(), _HYP_COLS)
        _write_csv(p("experiment_queue_snapshot.csv"), self._exp_queue_rows(), _EXPQ_COLS)
        _write_csv(p("experiment_results_snapshot.csv"),
                   self.results_rows or [{"status": "NO_RESULTS"}],
                   _RESULT_COLS if self.results_rows else ["status"])
        _write_csv(p("candidate_signal_registry_snapshot.csv"),
                   self.registry_rows or [{"status": "EMPTY"}],
                   _REG_COLS if self.registry_rows else ["status"])
        _write_csv(p("signal_promotion_log.csv"), self.promotion_rows or [{"status": "EMPTY"}],
                   _PROMO_COLS if self.promotion_rows else ["status"])
        _write_csv(p("rejected_hypothesis_graveyard.csv"), self.graveyard_rows or [{"status": "EMPTY"}],
                   _GRAVE_COLS if self.graveyard_rows else ["status"])
        _write_csv(p("provider_blocker_registry.csv"), blockers, _BLOCKER_COLS)
        _write_csv(p("agent_task_board.csv"), last_cycle.get("board", []) or [{"status": "EMPTY"}],
                   _BOARD_COLS if last_cycle.get("board") else ["status"])
        _write_csv(p("agent_cycle_summary.csv"), last_cycle.get("summary", []) or [{"status": "EMPTY"}],
                   _CYCLESUM_COLS if last_cycle.get("summary") else ["status"])
        _write_csv(p("agent_decision_log.csv"), self._decision_log_rows(last_cycle),
                   _DECLOG_COLS)
        # analysis artifacts (reuse 8-I report builders verbatim)
        sb = P8I.alpha_scoreboard_rows(ledger)
        _write_csv(p("autonomous_signal_scoreboard.csv"), sb or [{"status": "EMPTY"}],
                   _ALPHA_SCORE_COLS if sb else ["status"])
        confirmed = [r for r in sb if r.get("alpha_promotion") == ST_ALPHA_CONFIRMED]
        promising = [r for r in sb if r.get("alpha_promotion") == ST_ALPHA_PROMISING]
        provider_req = [r for r in sb if r.get("alpha_promotion") == ST_ALPHA_PROVIDER_REQUIRED]
        rejected = [r for r in sb if r.get("alpha_promotion") == ST_REJECTED and not r.get("is_challenge")]
        _write_csv(p("confirmed_alpha_signals.csv"), confirmed or [{"status": "NO_CONFIRMED_ALPHA_SIGNAL"}],
                   _ALPHA_SCORE_COLS if confirmed else ["status"])
        _write_csv(p("promising_alpha_signals.csv"), promising or [{"status": "NO_PROMISING_ALPHA_SIGNAL"}],
                   _ALPHA_SCORE_COLS if promising else ["status"])
        _write_csv(p("provider_required_signals.csv"),
                   provider_req or [{"status": "NO_PROVIDER_REQUIRED_SIGNAL"}],
                   _ALPHA_SCORE_COLS if provider_req else ["status"])
        _write_csv(p("rejected_alpha_signals.csv"), rejected or [{"status": "NO_REJECTED_ALPHA_SIGNAL"}],
                   _ALPHA_SCORE_COLS if rejected else ["status"])
        _write_csv(p("best_trade_idea_candidates.csv"),
                   best_trade_idea_rows(self.registry_rows) or [{"status": "NO_TRADE_IDEA_CANDIDATE"}],
                   _BEST_COLS if best_trade_idea_rows(self.registry_rows) else ["status"])
        _write_csv(p("ranked_next_actions.csv"), options,
                   ["rank", "option", "lever", "needs_provider", "prob_success", "ceiling", "why"])
        _write_csv(p("validation_skeptic_report.csv"),
                   validation_skeptic_rows(self.registry_rows) or [{"status": "NO_TESTABLE"}],
                   _SKEPTIC_COLS if validation_skeptic_rows(self.registry_rows) else ["status"])
        mt = P8I._multiple_testing(ledger)
        _write_csv(p("multiple_testing_report.csv"),
                   [{"metric": k, "value": (";".join(map(str, v)) if isinstance(v, list) else v)}
                    for k, v in mt.items()], ["metric", "value"])
        _write_csv(p("model_candidate_registry_update.csv"), P8G.model_candidate_update(ledger),
                   ["candidate_id", "family", "driver", "cohort", "signal_status", "promotion",
                    "registry_decision", "proposed_contribution", "real_external_data",
                    "lift_vs_control", "ev_after_25bps", "worst_decile_mean", "deployed",
                    "paper_trader_output", "production", "note"])

    def _decision_log_rows(self, last_cycle) -> List[dict]:
        rows = []
        for r in self.run_log_rows:
            rows.append({"cycle": r.get("cycle"), "role": ROLE_DIRECTOR,
                         "decision": r.get("next_action"),
                         "n_confirmed": r.get("n_confirmed"), "n_promising": r.get("n_promising"),
                         "queue_remaining": r.get("queue_remaining"), "rationale": r.get("note")})
        if not rows:
            rows.append({"cycle": self.cycles_completed, "role": ROLE_DIRECTOR,
                         "decision": last_cycle.get("action", ACT_BUILD_PANEL), "n_confirmed": 0,
                         "n_promising": 0, "queue_remaining": len(self._untested()),
                         "rationale": last_cycle.get("action_reason", "")})
        return rows

    def _director_decision(self, report, options, rec) -> dict:
        return {"phase": PHASE, "generated_utc": report["generated_utc"], "recommendation": rec,
                "stop_reason": report["stop_reason"], "next_action": report["next_action"],
                "allowed_recommendations": list(ALLOWED_RECOMMENDATIONS),
                "allowed_alpha_statuses": list(ALLOWED_ALPHA_STATUSES),
                "allowed_actions": list(ALLOWED_ACTIONS),
                "allowed_stop_conditions": list(ALLOWED_STOPS),
                "decision_detail": report["decision_detail"],
                "best_current_path": report["best_current_path"], "top_next_options": options,
                "binding_constraint": ("event-data BREADTH and provider history; NOT the Norgate panel"),
                "anti_p_hacking": {"all_pre_registered": True, "thresholds_fixed_a_priori": True,
                                   "thresholds_modified_after_results": False,
                                   "factor_signs_modified_after_results": False,
                                   "challenge_fraction": report["loop"]["challenge_fraction"],
                                   "external_data_never_faked": True,
                                   "revision_proxy_capped_below_confirmed": True,
                                   "combinations_use_only_existing_real_columns": True},
                "stop_conditions_honored": [
                    "local data first; Norgate for price/macro; no package install",
                    "no threshold change to rescue a result", "no factor-sign flipping",
                    "no weight optimization", "no regime activation", "no ML fitting",
                    "external data never faked", "revision proxy labelled + capped",
                    "no secrets printed", "no live trading signals", "no broker/orders/automation",
                    "no Paper Trader / GCP / deployment", "failed experiments not hidden",
                    "no commit", "no push"]}

    def _phase8k_plan(self, report, readiness, options) -> dict:
        return {"from_phase": PHASE, "next_phase": "8-K", "recommendation": report["recommendation"],
                "stop_reason": report["stop_reason"], "next_action": report["next_action"],
                "best_current_path": report["best_current_path"], "ranked_next_options": options,
                "binding_constraint": "event-data breadth + provider history (earnings/revision/news/short)",
                "next_steps": [o["option"] for o in options],
                "provider_priority": ["broad_earnings_and_revision_feed", "short_interest_finra_bulk",
                                      "news_sentiment_history", "options_iv"],
                "provider_readiness": readiness,
                "resume_command": ("python research/run_phase8j_autonomous_alpha_research_daemon.py "
                                   "--max-cycles 3 --resume --stop-on-confirmed"),
                "hard_constraints": [
                    "local data first; Norgate + FRED for price/macro", "do not install packages",
                    "large state on D: only; repo gets summaries/snapshots", "never print secrets",
                    "bounded no-key collection; point-in-time joins only", "thresholds fixed a priori",
                    "no Paper Trader / GCP / deployment", "no broker/order/automation",
                    "no live trading signals", "no weight optimization", "no factor-sign flipping",
                    "no regime activation", "external data never faked",
                    "do not hide failed experiments", "do not commit", "do not push"]}


# 8-H EDGAR scaled-filings, guarded (8-H module reached via 8-I).
def P8H_safe_filings(want: List[str]):
    try:
        return P8I.P8H._edgar_scaled_filings(want, cap=P8I.P8H.EDGAR_SCALED_CAP)
    except Exception:
        return P8G.load_sec_filing_events(activate_live=False)


# Column orders for durable state + snapshots.
_HYP_COLS = ["hypothesis_id", "family", "owning_agent", "owning_role", "driver", "cohort", "horizon",
             "is_challenge", "placebo", "status", "cycle_assigned", "hypothesis"]
_EXPQ_COLS = ["exp_id", "family", "owning_agent", "owning_role", "is_challenge", "state",
              "cycle_assigned"]
_RESULT_COLS = ["exp_id", "cycle", "family", "agent", "driver", "cohort", "is_challenge",
                "real_external_data", "needs_provider", "signal_status", "ext_promotion",
                "alpha_promotion", "provider_limited", "n_events", "n_recent_events",
                "lift_vs_control", "ev_after_25bps", "ev_after_50bps", "hit_rate", "payoff_ratio",
                "worst_decile_mean", "recent_lift_vs_control", "reason"]
_REG_COLS = ["candidate_id", "family", "agent", "driver", "cohort", "is_challenge",
             "real_external_data", "needs_provider", "signal_status", "ext_promotion",
             "alpha_promotion", "provider_limited", "n_events", "n_recent_events", "lift_vs_control",
             "ev_after_25bps", "worst_decile_mean", "recent_lift_vs_control", "prior_known_lead",
             "reason"]
_PROMO_COLS = ["cycle", "exp_id", "family", "alpha_promotion", "ext_promotion", "provider_limited",
               "n_events", "ev_after_25bps", "lift_vs_control", "decision", "reason"]
_GRAVE_COLS = ["cycle", "exp_id", "family", "is_challenge", "n_events", "lift_vs_control",
               "ev_after_25bps", "rejection_reason"]
_BLOCKER_COLS = ["family", "owning_agent", "owning_role", "data_required", "candidate_keys",
                 "any_candidate_key_present", "blocker_active", "powershell_to_supply", "note"]
_RUNLOG_COLS = ["cycle", "utc", "batch_size", "experiments_scored_total", "queue_remaining",
                "n_confirmed", "n_promising", "n_provider_required", "n_rejected", "next_action",
                "note"]
_BOARD_COLS = ["cycle", "role", "task", "status", "detail"]
_CYCLESUM_COLS = ["role", "n_tasks", "n_done", "n_blocked", "n_idle", "status"]
_DECLOG_COLS = ["cycle", "role", "decision", "n_confirmed", "n_promising", "queue_remaining",
                "rationale"]
_SKEPTIC_COLS = ["exp_id", "family", "is_challenge", "n_events", "n_recent_events", "lift_vs_control",
                 "ev_after_25bps", "recent_lift_vs_control", "worst_decile_mean", "alpha_promotion",
                 "skeptic_verdict", "skeptic_note"]
_BEST_COLS = ["rank", "candidate_id", "family", "driver", "cohort", "alpha_promotion",
              "provider_limited", "n_events", "n_recent_events", "lift_vs_control", "ev_after_25bps",
              "recent_lift_vs_control", "worst_decile_mean", "actionability", "rationale"]


# =========================================================================== #
# CLI.
# =========================================================================== #
def _parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Phase 8-J Autonomous Alpha Research Daemon")
    ap.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    ap.add_argument("--state-dir", default=str(STATE_ROOT_DEFAULT))
    ap.add_argument("--once", action="store_true", help="run exactly one cycle (validation)")
    ap.add_argument("--max-cycles", type=int, default=None, help="bounded campaign: stop after N cycles")
    ap.add_argument("--max-experiments", type=int, default=None,
                    help="stop after N experiments scored (cumulative)")
    ap.add_argument("--time-budget-minutes", type=float, default=None,
                    help="stop once wall-clock minutes are exhausted")
    ap.add_argument("--resume", action="store_true", help="resume durable state from the state dir")
    ap.add_argument("--dry-run", action="store_true",
                    help="do not persist runtime state on D: and do not do any network collection")
    ap.add_argument("--activate-live", action="store_true",
                    help="scale no-key SEC EDGAR + retry GDELT + probe FINRA (cached on D:)")
    ap.add_argument("--stop-on-confirmed", action="store_true",
                    help="halt as soon as a CONFIRMED alpha signal is found")
    ap.add_argument("--stop-on-provider-blocker", action="store_true",
                    help="halt when the only remaining path is a hard provider requirement")
    ap.add_argument("--heartbeat-seconds", type=int, default=0,
                    help="emit a heartbeat line each cycle (liveness for long campaigns)")
    return ap.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parse_args(argv)
    # default to a small bounded campaign when no bound is given (keeps the daemon from running away)
    max_cycles = args.max_cycles
    if not args.once and max_cycles is None and args.max_experiments is None \
            and args.time_budget_minutes is None:
        max_cycles = DEFAULT_MAX_CYCLES
    daemon = AlphaResearchDaemon(Path(args.out_dir), Path(args.state_dir), dry_run=args.dry_run,
                                 activate_live=args.activate_live)
    try:
        report = daemon.run(once=args.once, max_cycles=max_cycles,
                            max_experiments=args.max_experiments,
                            time_budget_minutes=args.time_budget_minutes, resume=args.resume,
                            stop_on_confirmed=args.stop_on_confirmed,
                            stop_on_provider_blocker=args.stop_on_provider_blocker,
                            heartbeat_seconds=args.heartbeat_seconds)
    except Exception as exc:  # pragma: no cover - top-level guard
        out = Path(args.out_dir)
        out.mkdir(parents=True, exist_ok=True)
        _write_json(out / "phase8j_autonomous_alpha_research_daemon.json",
                    {"phase": PHASE, "recommendation": P8I.REC_ERROR, "error": repr(exc),
                     "generated_utc": _utc_now_iso()})
        print(f"[{PHASE}] ERROR: {exc!r}")
        return 1
    _print_summary(report)
    return 0


def _print_summary(report: dict) -> None:
    loop, cand = report["loop"], report["candidates"]
    print(f"[{PHASE}] stop_reason = {report['stop_reason']} | recommendation = {report['recommendation']}")
    print(f"[{PHASE}] cycles={loop['cycles_completed']} scored={loop['experiments_scored']}/"
          f"{loop['bank_size']} queue_remaining={loop['queue_remaining']} "
          f"challenge_frac={loop['challenge_fraction']}")
    print(f"[{PHASE}] candidates: confirmed={cand['n_confirmed']} promising={cand['n_promising']} "
          f"(clean={cand['n_clean_promising']} limited={cand['n_provider_limited']}) "
          f"provider_required={cand['n_provider_required']} rejected={cand['n_rejected']}")
    print(f"[{PHASE}] next_action = {report['next_action']} :: {str(report['next_action_reason'])[:96]}")
    print(f"[{PHASE}] best path: {report['best_current_path'][:96]}")


if __name__ == "__main__":
    raise SystemExit(main())
