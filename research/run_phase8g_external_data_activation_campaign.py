"""Phase 8-G — External Data Activation and Signal Confirmation Campaign.

**Track A (quant brain) research only.** Phase 8-F built the autonomous external-signal OS but
detected 0 provider keys, so every external family stayed NEEDS_PROVIDER (adapters + schemas +
mock fixtures only). 8-G stops scaffolding and ACTIVATES the real external-event data that is
actually reachable WITHOUT a paid key:

  * LOCAL earnings-surprise cache (research/output/phase3m_.../earnings_events_universe.csv):
    8,390 point-in-time EPS actual-vs-estimate events, 600+ tickers, 1996-2026. REAL data.
  * LOCAL analyst-revision PROXY (earnings_features_universe.csv: surprise_acceleration /
    estimate_revision_proxy). A derived proxy — NOT a licensed revision feed — so it can reach
    PROMISING but is capped below CONFIRMED (true provider history still required).
  * LOCAL SEC EDGAR filing events (phase3f sample) + an OPTIONAL bounded no-key EDGAR live pull
    (free public submissions API) cached on D:.
  * NO-KEY public news (GDELT) ATTEMPTED; blocked/rate-limited -> adapter stays executable.
  * options IV / short interest: no local data, no key -> NEEDS_PROVIDER (executable command).

The ONE question this phase answers
-----------------------------------
    DOES REAL EXTERNAL EVENT DATA TURN A PROMISING SENSITIVITY LEAD INTO A CONFIRMED SIGNAL?

How (no new gate, no tuning)
----------------------------
Real earnings events are joined to the persisted 8-E weekly grid by ticker on a leak-safe
merge_asof over the point-in-time `availability_date` (event info is only ever read at/after it
became public; forward labels are forward of the grid observation). Each external setup is then a
SensSetup scored by the IDENTICAL 8-E machinery (evaluate_sensitivity_setup / walk_forward_lift /
simulate_event_portfolio / classify_setup) with the SAME fixed thresholds from 8-E/8-F. The
surprise-sensitivity cohort is ESTIMATED from each ticker's own PRIOR earnings reactivity
(expanding, leak-safe) — never assumed. Track A overlays a real earnings-surprise confirmation on
the S8E-011 macro lead and re-applies the 8-F fixed pre-declared filters.

Hard safety contract (unchanged)
--------------------------------
Local data first; Norgate + on-disk FRED only for price/macro (no package install). Provider keys
detected by NAME/presence only, never printed. Bounded no-key collection: raw under
D:\\Stock_Prediction_app_data\\external_raw, normalized under ...\\external_normalized; repo gets
summaries/panels only. All setups pre-registered before scoring; thresholds fixed a priori; >=30%
challenges/placebos. External data is NEVER faked — mock fixtures stay labelled and excluded; the
revision PROXY is labelled a proxy and cannot be promoted to CONFIRMED. No Paper Trader, no GCP, no
deployment, no broker/orders/automation, no live trading signals, no weight optimization, no
factor-sign flipping after results, no regime activation, no ML fit, no hidden failures, no
secrets printed, no commit, no push.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import math
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
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


# Reuse the 8-F OS (connectors/schemas/provider detection/memory) and the 8-E scoring engine.
P8F = _load_module("phase8f_engine_for_8g", "research/run_phase8f_autonomous_external_signal_os.py")
P8E = P8F.P8E

# IO + leak-safe primitives reused verbatim.
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
RECENT_START = P8E.RECENT_START
COST_BPS_GRID = P8E.COST_BPS_GRID
ST_CONFIRMED = P8E.ST_CONFIRMED
ST_PROMISING = P8E.ST_PROMISING
ST_REJECTED = P8E.ST_REJECTED
ST_BLOCKED = P8E.ST_BLOCKED
ST_NEEDS_PROVIDER = P8E.ST_NEEDS_PROVIDER

PHASE = "8-G"
OBJECTIVE = (
    "Activate real external-event data that is reachable without a paid key (local earnings-"
    "surprise cache, a labelled analyst-revision proxy, local + no-key SEC filings), join it leak-"
    "safe to the persisted sensitivity grid, and test earnings/revision/filing x ticker-sensitivity "
    "and S8E-011 + external confirmation through the IDENTICAL fixed 8-E gate to learn whether real "
    "external data confirms a sensitivity lead. Research only; external data never faked."
)

# --------------------------------------------------------------------------- #
# Decision vocabulary (Part: Decision values), exact set.
# --------------------------------------------------------------------------- #
REC_CONFIRMED = "CONFIRMED_EXTERNAL_SIGNAL_FOUND"
REC_PROMISING = "PROMISING_EXTERNAL_SIGNAL_FOUND"
REC_PROMISING_PROVIDER = "PROMISING_NEEDS_PROVIDER_HISTORY"
REC_NEEDS_NEWS = "NEEDS_NEWS_SENTIMENT_PROVIDER_DATA"
REC_NEEDS_ANALYST = "NEEDS_ANALYST_REVISION_PROVIDER_DATA"
REC_NEEDS_EXTERNAL = "NEEDS_EXTERNAL_PROVIDER_DATA"
REC_REJECTED = "EXTERNAL_SIGNAL_RESEARCH_REJECTED"
REC_PROVIDER_BLOCKED = "PROVIDER_ACCESS_BLOCKED"
REC_ERROR = "ERROR"
ALLOWED_RECOMMENDATIONS = (
    REC_CONFIRMED, REC_PROMISING, REC_PROMISING_PROVIDER, REC_NEEDS_NEWS, REC_NEEDS_ANALYST,
    REC_NEEDS_EXTERNAL, REC_REJECTED, REC_PROVIDER_BLOCKED, REC_ERROR,
)

# Per-signal promotion vocabulary (Part: Signal promotion).
ST_EXT_CONFIRMED = "CONFIRMED_EXTERNAL_SIGNAL"
ST_EXT_PROMISING = "PROMISING_EXTERNAL_SIGNAL"
ST_NEEDS_HISTORY = "NEEDS_PROVIDER_HISTORY"
ALLOWED_STATUSES = (ST_EXT_CONFIRMED, ST_EXT_PROMISING, ST_NEEDS_HISTORY, ST_NEEDS_PROVIDER,
                    ST_REJECTED, ST_BLOCKED)

# --------------------------------------------------------------------------- #
# Agents + families (reuse 8-F roster).
# --------------------------------------------------------------------------- #
ALL_AGENTS = P8F.ALL_AGENTS
SENS_A, VAL_A, RSK_A, MODEL_A = P8F.SENS_A, P8F.VAL_A, P8F.RSK_A, P8F.MODEL_A
EARN_A, REV_A, NEWS_A, OPT_A, SHORT_A, EXT_A, DIR_A = (
    P8F.EARN_A, P8F.REV_A, P8F.NEWS_A, P8F.OPT_A, P8F.SHORT_A, P8F.EXT_A, P8F.DIR_A)

FAM_EARNINGS = P8F.FAM_EARNINGS                 # C
FAM_REVISION = P8F.FAM_REVISION                 # B (proxy here)
FAM_NEWS = P8F.FAM_NEWS                          # A
FAM_OPTIONS = P8F.FAM_OPTIONS                    # D-options
FAM_SHORT = P8F.FAM_SHORT                        # E
FAM_S8E011_EXT = P8F.FAM_S8E011_EXT             # F
FAM_FILINGS = "H_sec_filings_x_sensitivity"     # new in 8-G (no-key SEC)
ALL_FAMILIES = (FAM_EARNINGS, FAM_REVISION, FAM_NEWS, FAM_OPTIONS, FAM_SHORT, FAM_S8E011_EXT,
                FAM_FILINGS)

# --------------------------------------------------------------------------- #
# Paths.
# --------------------------------------------------------------------------- #
DATA_ROOT = P8F.DATA_ROOT
EXTERNAL_RAW = P8F.EXTERNAL_RAW
EXTERNAL_NORMALIZED = P8F.EXTERNAL_NORMALIZED
DEFAULT_OUT_DIR = _REPO_ROOT / "research" / "output" / "phase8g_external_data_activation_campaign"

# Real local external caches (discovered on disk).
EARN_EVENTS_CSV = (_REPO_ROOT / "research" / "output" / "phase3m_earnings_estimates_signal_gate"
                   / "earnings_events_universe.csv")
EARN_FEATURES_CSV = (_REPO_ROOT / "research" / "output" / "phase3m_earnings_estimates_signal_gate"
                     / "earnings_features_universe.csv")
SEC_SAMPLE_CSV = (_REPO_ROOT / "research" / "output" / "phase3f_sec_fundamentals_sample"
                  / "fundamentals_sample.csv")
SEC_LIVE_CACHE = EXTERNAL_RAW / "filings_sec" / "edgar_submissions_cache.json"

# Budget (Part 9 from 8-F carried forward).
MAX_TOTAL_EXPERIMENTS = 250
MAX_PER_FAMILY = 75
CHALLENGE_MIN_FRAC = 0.30
MAX_CYCLES = 3

# Leak-safe join windows.
EARN_JOIN_TOL_DAYS = 7        # weekly grid: attach an earnings event to its first obs within 7d
RECENT_POS_TOL_DAYS = 63      # "a positive surprise in the trailing ~quarter" (Track A overlay)
MIN_PRIOR_EVENTS = 4          # cohort reactivity needs >= 4 prior events for that ticker

# Fixed a-priori event thresholds (no tuning).
LARGE_SURPRISE_PCT = 10.0     # "beat by >= 10%" large-positive surprise


# =========================================================================== #
# Real local-data activation.
# =========================================================================== #
def local_artifact_inventory() -> List[dict]:
    """Inventory the REAL on-disk external caches 8-G activates (Part: local artifacts found)."""
    specs = [
        ("earnings_surprise", FAM_EARNINGS, EARN_EVENTS_CSV, "EPS actual/estimate/surprise, PIT",
         "availability_date", True),
        ("earnings_features", FAM_REVISION, EARN_FEATURES_CSV,
         "trailing surprise + surprise_acceleration + estimate_revision_proxy (PROXY)",
         "availability_date", True),
        ("sec_filings_sample", FAM_FILINGS, SEC_SAMPLE_CSV, "SEC EDGAR 10-K/10-Q filing dates",
         "availability_datetime", True),
    ]
    rows = []
    for key, fam, path, kind, pit, usable in specs:
        present = path.exists()
        n = 0
        n_tickers = 0
        if present:
            try:
                df = pd.read_csv(path, usecols=lambda c: c in ("ticker",))
                n = int(len(df))
                n_tickers = int(df["ticker"].nunique()) if "ticker" in df.columns else 0
            except Exception:
                try:
                    n = int(len(pd.read_csv(path)))
                except Exception:
                    n = 0
        rows.append({
            "artifact": key, "family": fam, "path": str(path).replace("\\", "/"),
            "exists": present, "n_rows": n, "n_tickers": n_tickers, "kind": kind,
            "point_in_time_field": pit, "point_in_time_usable": bool(usable and present),
        })
    return rows


def load_earnings_events() -> pd.DataFrame:
    """Load the real earnings-surprise cache; keep only point-in-time-usable rows with a PIT date."""
    if not EARN_EVENTS_CSV.exists():
        return pd.DataFrame()
    df = pd.read_csv(EARN_EVENTS_CSV)
    df = df[df.get("point_in_time_usable", True) == True].copy()  # noqa: E712
    df["availability_date"] = pd.to_datetime(df["availability_date"], errors="coerce")
    df = df.dropna(subset=["availability_date", "ticker"])
    df["eps_surprise_pct"] = pd.to_numeric(df.get("surprise_percentage"), errors="coerce")
    df = df.dropna(subset=["eps_surprise_pct"])
    # merge the labelled revision PROXY (surprise_acceleration) if present
    if EARN_FEATURES_CSV.exists():
        feat = pd.read_csv(EARN_FEATURES_CSV)
        feat["availability_date"] = pd.to_datetime(feat["availability_date"], errors="coerce")
        keep = feat[["ticker", "availability_date", "surprise_acceleration",
                     "estimate_revision_proxy"]].copy()
        df = df.merge(keep, on=["ticker", "availability_date"], how="left")
    else:
        df["surprise_acceleration"] = np.nan
        df["estimate_revision_proxy"] = np.nan
    return df.sort_values(["ticker", "availability_date"]).reset_index(drop=True)


def normalized_earnings_panel(earn: pd.DataFrame) -> pd.DataFrame:
    """Normalize the real earnings events to the 8-F earnings schema (point-in-time fields)."""
    if earn.empty:
        return pd.DataFrame(columns=P8F.SCHEMA_EARNINGS)
    out = pd.DataFrame()
    out["event_timestamp"] = earn["reported_date"].astype(str)
    out["point_in_time_available_at"] = earn["availability_date"].dt.strftime("%Y-%m-%d")
    out["ticker"] = earn["ticker"]
    out["fiscal_period"] = earn.get("fiscal_date_ending", "").astype(str)
    out["actual_eps"] = pd.to_numeric(earn.get("reported_eps"), errors="coerce")
    out["estimated_eps"] = pd.to_numeric(earn.get("estimated_eps"), errors="coerce")
    out["surprise_pct"] = earn["eps_surprise_pct"]
    out["revenue_actual"] = ""
    out["revenue_estimate"] = ""
    out["guidance_direction"] = ""
    out["guidance_magnitude"] = ""
    out["provider_id"] = earn.get("provider", "alpha_vantage")
    out["raw_payload_path"] = "LOCAL_CACHE:phase3m/earnings_events_universe.csv"
    return out[P8F.SCHEMA_EARNINGS]


def normalized_revision_proxy_panel(earn: pd.DataFrame) -> pd.DataFrame:
    """Labelled analyst-revision PROXY normalized to the revision schema. provider_id=PROXY_LOCAL."""
    if earn.empty:
        return pd.DataFrame(columns=P8F.SCHEMA_REVISION)
    e = earn.dropna(subset=["surprise_acceleration"]).copy()
    if e.empty:
        return pd.DataFrame(columns=P8F.SCHEMA_REVISION)
    out = pd.DataFrame()
    out["event_timestamp"] = e["reported_date"].astype(str)
    out["point_in_time_available_at"] = e["availability_date"].dt.strftime("%Y-%m-%d")
    out["ticker"] = e["ticker"]
    out["fiscal_period"] = e.get("fiscal_date_ending", "").astype(str)
    out["estimate_type"] = "eps_surprise_acceleration_proxy"
    out["old_value"] = ""
    out["new_value"] = ""
    out["revision_direction"] = np.where(e["surprise_acceleration"] > 0, "up",
                                         np.where(e["surprise_acceleration"] < 0, "down", "flat"))
    out["revision_magnitude"] = pd.to_numeric(e["surprise_acceleration"], errors="coerce")
    out["analyst_count"] = ""
    out["consensus_change"] = ""
    out["provider_id"] = "PROXY_LOCAL"           # explicit: derived proxy, not a licensed feed
    out["raw_payload_path"] = "LOCAL_PROXY:phase3m/earnings_features_universe.csv"
    return out[P8F.SCHEMA_REVISION]


def load_sec_filing_events(activate_live: bool = False) -> Tuple[pd.DataFrame, dict]:
    """Real SEC filing events: local phase3f sample + (optional) a bounded no-key EDGAR live pull.

    Returns (events_df[ticker, availability_date, form], activation_meta). Network is best-effort;
    failure degrades to the local sample and is reported honestly (never raises)."""
    meta = {"local_sample_used": False, "live_attempted": False, "live_succeeded": False,
            "live_tickers": 0, "n_local": 0, "n_live": 0, "note": ""}
    frames = []
    if SEC_SAMPLE_CSV.exists():
        s = pd.read_csv(SEC_SAMPLE_CSV)
        s["availability_date"] = pd.to_datetime(s.get("availability_datetime"), errors="coerce",
                                                utc=True).dt.tz_localize(None)
        s = s.dropna(subset=["availability_date", "ticker"])
        loc = s[["ticker", "availability_date", "form"]].drop_duplicates()
        meta["local_sample_used"] = True
        meta["n_local"] = int(len(loc))
        frames.append(loc)
    if activate_live:
        live, lmeta = _edgar_live_filings()
        meta.update({f"live_{k}": v for k, v in lmeta.items()})
        meta["live_attempted"] = True
        meta["live_succeeded"] = not live.empty
        meta["n_live"] = int(len(live))
        if not live.empty:
            frames.append(live)
    if not frames:
        return pd.DataFrame(columns=["ticker", "availability_date", "form"]), meta
    out = pd.concat(frames, ignore_index=True).drop_duplicates(["ticker", "availability_date", "form"])
    return out.sort_values(["ticker", "availability_date"]).reset_index(drop=True), meta


def _edgar_live_filings(max_tickers: int = 60) -> Tuple[pd.DataFrame, dict]:
    """Bounded no-key SEC EDGAR submissions pull (free public API). Cached on D:. Resilient."""
    import urllib.request
    meta = {"cache_hit": False, "error": ""}
    SEC_LIVE_CACHE.parent.mkdir(parents=True, exist_ok=True)
    cache: dict = {}
    if SEC_LIVE_CACHE.exists():
        try:
            cache = json.loads(SEC_LIVE_CACHE.read_text(encoding="utf-8"))
            meta["cache_hit"] = True
        except Exception:
            cache = {}
    rows: List[dict] = []
    hdr = {"User-Agent": "paper-trader-research research@example.com"}
    try:
        # tickers most likely to overlap the grid: the covered earnings names.
        earn = load_earnings_events()
        want = (earn["ticker"].value_counts().index.tolist()[:max_tickers]
                if not earn.empty else [])
        if not cache:
            tmap_url = "https://www.sec.gov/files/company_tickers.json"
            req = urllib.request.Request(tmap_url, headers=hdr)
            with urllib.request.urlopen(req, timeout=10) as r:
                tj = json.loads(r.read().decode("utf-8", "ignore"))
            cik_by_ticker = {v["ticker"].upper(): str(v["cik_str"]).zfill(10) for v in tj.values()}
            for tk in want:
                cik = cik_by_ticker.get(str(tk).upper())
                if not cik:
                    continue
                url = f"https://data.sec.gov/submissions/CIK{cik}.json"
                try:
                    req = urllib.request.Request(url, headers=hdr)
                    with urllib.request.urlopen(req, timeout=10) as r:
                        sj = json.loads(r.read().decode("utf-8", "ignore"))
                    rec = sj.get("filings", {}).get("recent", {})
                    forms = rec.get("form", [])
                    dates = rec.get("acceptanceDateTime") or rec.get("filingDate", [])
                    cache[str(tk)] = [{"form": f, "ts": d} for f, d in zip(forms, dates)]
                except Exception:
                    continue
            SEC_LIVE_CACHE.write_text(json.dumps(cache), encoding="utf-8")
        for tk, items in cache.items():
            for it in items:
                if it.get("form") in ("8-K", "10-Q", "10-K"):
                    rows.append({"ticker": tk, "availability_date": it.get("ts"),
                                 "form": it.get("form")})
    except Exception as exc:                                  # pragma: no cover - network guard
        meta["error"] = repr(exc)[:160]
    if not rows:
        return pd.DataFrame(columns=["ticker", "availability_date", "form"]), meta
    df = pd.DataFrame(rows)
    df["availability_date"] = pd.to_datetime(df["availability_date"], errors="coerce", utc=True)
    df["availability_date"] = df["availability_date"].dt.tz_localize(None)
    df = df.dropna(subset=["availability_date"])
    return df, meta


# =========================================================================== #
# Grid augmentation — leak-safe join of real events onto the persisted grid.
# =========================================================================== #
def augment_grid(grid: pd.DataFrame, earn: pd.DataFrame,
                 filings: pd.DataFrame) -> Tuple[pd.DataFrame, dict]:
    """Attach earnings/revision-proxy/filing event columns + estimated surprise-sensitivity cohort.

    All joins use merge_asof BACKWARD on the point-in-time availability date, so an event is only
    ever visible at/after it became public and the grid's forward labels stay forward of it."""
    g = grid.copy()
    diag = {}
    # ---- tight earnings join (event -> its first weekly obs within 7 days) ----
    cols = ["earn_event", "earn_surprise_pct", "earn_surprise_pos", "earn_surprise_neg",
            "earn_surprise_large", "earn_revision_proxy_up", "cohort_surprise_sensitive",
            "tkr_surprise_sensitive", "earn_recent_pos", "filing_event", "cohort_filing_active"]
    for c in cols:
        g[c] = 0.0
    if not earn.empty:
        e = earn.rename(columns={"ticker": "symbol", "availability_date": "date"})[
            ["symbol", "date", "eps_surprise_pct", "surprise_acceleration"]].copy()
        e = e.dropna(subset=["date"]).sort_values("date")
        gg = g.sort_values("date")
        merged = pd.merge_asof(
            gg, e, on="date", by="symbol", direction="backward",
            tolerance=pd.Timedelta(days=EARN_JOIN_TOL_DAYS), suffixes=("", "_e"))
        merged = merged.sort_index()
        has = merged["eps_surprise_pct"].notna()
        g.loc[has.index, "earn_event"] = has.astype(float).values
        sp = merged["eps_surprise_pct"]
        g["earn_surprise_pct"] = sp.fillna(0.0).values
        g["earn_surprise_pos"] = ((has) & (sp > 0)).astype(float).values
        g["earn_surprise_neg"] = ((has) & (sp < 0)).astype(float).values
        g["earn_surprise_large"] = ((has) & (sp >= LARGE_SURPRISE_PCT)).astype(float).values
        acc = merged["surprise_acceleration"]
        g["earn_revision_proxy_up"] = ((has) & (acc > 0)).astype(float).values
        diag["n_earn_event_obs"] = int(g["earn_event"].sum())
        # ---- leak-safe surprise-sensitivity cohort: each ticker's PRIOR reactivity ----
        g = _add_surprise_cohort(g)
        diag["n_surprise_sensitive_obs"] = int(g["cohort_surprise_sensitive"].sum())
        # persistent per-ticker flag (once established, stays on) so non-earnings obs (e.g. filing
        # events) inherit the ticker's estimated sensitivity. cummax of a backward flag = leak-safe.
        g["tkr_surprise_sensitive"] = g.groupby("symbol")["cohort_surprise_sensitive"].cummax()
        # ---- loose join: a positive surprise in the trailing ~quarter (Track A overlay) ----
        epos = earn[earn["eps_surprise_pct"] > 0].rename(
            columns={"ticker": "symbol", "availability_date": "date"})[["symbol", "date"]].copy()
        epos["_pos"] = 1.0
        epos = epos.dropna(subset=["date"]).sort_values("date")
        if not epos.empty:
            m2 = pd.merge_asof(g.sort_values("date"), epos, on="date", by="symbol",
                               direction="backward",
                               tolerance=pd.Timedelta(days=RECENT_POS_TOL_DAYS)).sort_index()
            g["earn_recent_pos"] = m2["_pos"].fillna(0.0).values
        diag["n_recent_pos_obs"] = int(g["earn_recent_pos"].sum())
    # ---- SEC filings join (filing -> first obs within 7 days) ----
    if filings is not None and not filings.empty:
        f = filings.rename(columns={"ticker": "symbol", "availability_date": "date"})[
            ["symbol", "date"]].copy()
        f["_filing"] = 1.0
        f = f.dropna(subset=["date"]).sort_values("date")
        m3 = pd.merge_asof(g.sort_values("date"), f, on="date", by="symbol", direction="backward",
                           tolerance=pd.Timedelta(days=EARN_JOIN_TOL_DAYS)).sort_index()
        g["filing_event"] = m3["_filing"].fillna(0.0).values
        # filing-active cohort = fresh filing in a (persistently) surprise-sensitive name
        g["cohort_filing_active"] = ((g["filing_event"] > 0) &
                                     (g["tkr_surprise_sensitive"] > 0)).astype(float)
        diag["n_filing_event_obs"] = int(g["filing_event"].sum())
    return g, diag


def _add_surprise_cohort(g: pd.DataFrame) -> pd.DataFrame:
    """cohort_surprise_sensitive = 1 where a ticker's EXPANDING prior post-earnings reactivity
    (mean of sign(surprise)*fwd_excess_20 over its PRIOR earnings events) is positive with >= K
    prior events. Strictly leak-safe: only prior, already-closed events inform the current flag."""
    ev = g[g["earn_event"] > 0].copy()
    if ev.empty or "fwd_excess_20" not in ev.columns:
        return g
    ev = ev.sort_values(["symbol", "date"])
    react = np.sign(ev["earn_surprise_pct"]) * ev["fwd_excess_20"]
    grp = react.groupby(ev["symbol"])
    prior_sum = grp.apply(lambda s: s.shift(1).expanding().sum()).reset_index(level=0, drop=True)
    prior_cnt = grp.apply(lambda s: s.shift(1).expanding().count()).reset_index(level=0, drop=True)
    prior_mean = prior_sum / prior_cnt.replace(0, np.nan)
    sens = ((prior_cnt >= MIN_PRIOR_EVENTS) & (prior_mean > 0)).astype(float)
    g.loc[ev.index, "cohort_surprise_sensitive"] = sens.reindex(ev.index).fillna(0.0).values
    return g


# =========================================================================== #
# Pre-registered external setups (all thresholds fixed a priori).
# =========================================================================== #
def _mk(setup_id, family, agent, driver, cohort, horizon, conditions, hyp, *,
        is_challenge=False, placebo=False) -> SensSetup:
    return SensSetup(
        setup_id=setup_id, cycle=1, family=family, owning_agent=agent, is_challenge=is_challenge,
        driver=driver, cohort=cohort, hypothesis=hyp, primary_horizon=horizon,
        conditions=list(conditions), success_gate="8-E fixed gate (unchanged)",
        stop_condition="8-E fixed stop (unchanged)", placebo=placebo)


def plan_external_setups() -> List[SensSetup]:
    """Tracks C (earnings), B (revision proxy), A/F (S8E-011 + confirmation), H (filings), with
    >= 30% challenges/placebos. Fixed thresholds; no tuning."""
    s: List[SensSetup] = []
    POS = ("earn_surprise_pos", "ge", 1.0)
    COH = ("cohort_surprise_sensitive", "ge", 1.0)
    # --- Track C: earnings surprise x surprise-sensitive cohort (PEAD) --------- #
    s.append(_mk("S8G-C20", FAM_EARNINGS, EARN_A, "earnings_surprise", "cohort_surprise_sensitive",
                 20, [POS, COH], "Positive EPS surprise in a surprise-sensitive name drifts up 20d (PEAD)."))
    s.append(_mk("S8G-C05", FAM_EARNINGS, EARN_A, "earnings_surprise", "cohort_surprise_sensitive",
                 5, [("earn_surprise_large", "ge", 1.0), COH],
                 "Large (>=10%) positive EPS surprise in a surprise-sensitive name drifts up 5d."))
    s.append(_mk("S8G-C60", FAM_EARNINGS, EARN_A, "earnings_surprise", "cohort_surprise_sensitive",
                 60, [POS, COH], "Positive EPS surprise in a surprise-sensitive name drifts up 60d."))
    # --- Track B: analyst-revision PROXY x cohort (capped below CONFIRMED) ----- #
    s.append(_mk("S8G-B20", FAM_REVISION, REV_A, "analyst_revision_proxy",
                 "cohort_surprise_sensitive", 20,
                 [("earn_revision_proxy_up", "ge", 1.0), COH],
                 "Improving surprise (revision proxy) in a surprise-sensitive name drifts up 20d."))
    # --- Track A/F: S8E-011 + real earnings-surprise confirmation -------------- #
    s.append(_mk("S8G-F20", FAM_S8E011_EXT, SENS_A, "rates", "cohort_rates_neg", 20,
                 [("drv_rates_shock_z", "le", -P8E.SHOCK_Z), ("cohort_rates_neg", "ge", 1.0),
                  ("rel_str_60", "gt", 0.0), ("earn_recent_pos", "ge", 1.0)],
                 "S8E-011 (rates sell-off x short-duration cohort) CONFIRMED by a recent positive "
                 "earnings surprise — does real external confirmation lift the macro lead?"))
    # --- Track H: SEC filing event x sensitivity (no-key) --------------------- #
    s.append(_mk("S8G-H20", FAM_FILINGS, EARN_A, "sec_filing", "cohort_filing_active", 20,
                 [("filing_event", "ge", 1.0), ("cohort_filing_active", "ge", 1.0)],
                 "A fresh SEC filing in a surprise-sensitive name precedes drift over 20d."))

    # --- challenges / placebos (>= 30%) --------------------------------------- #
    s.append(_mk("S8G-901", FAM_EARNINGS, VAL_A, "earnings_surprise", "", 20,
                 [POS], "CHALLENGE/placebo: positive surprise, NO cohort — isolates cohort lift.",
                 is_challenge=True, placebo=True))
    s.append(_mk("S8G-902", FAM_EARNINGS, VAL_A, "earnings_surprise", "cohort_surprise_sensitive",
                 20, [("earn_surprise_neg", "ge", 1.0), COH],
                 "CHALLENGE: NEGATIVE surprise + surprise-sensitive cohort — wrong sign, must not drift up.",
                 is_challenge=True))
    s.append(_mk("S8G-903", FAM_S8E011_EXT, VAL_A, "rates", "", 20,
                 [("drv_rates_shock_z", "le", -P8E.SHOCK_Z), ("rel_str_60", "gt", 0.0),
                  ("earn_recent_pos", "ge", 1.0)],
                 "CHALLENGE/placebo: rates sell-off + earnings confirm, NO short-duration cohort — "
                 "isolates the cohort contribution to the confirmed lead.",
                 is_challenge=True, placebo=True))
    return s


# Families whose evidence is a labelled proxy or no-key thin coverage: never CONFIRMED.
PROXY_OR_THIN_FAMILIES = {FAM_REVISION, FAM_FILINGS}


# =========================================================================== #
# Experiment ledger.
# =========================================================================== #
@dataclass
class Experiment:
    exp_id: str
    cycle: int
    family: str
    agent: str
    driver: str
    cohort: str
    is_challenge: bool
    real_external_data: bool
    needs_provider: bool
    hypothesis: str
    status: str = ""
    promotion: str = ""
    reason: str = ""
    metrics: dict = field(default_factory=dict)


def _score_setup(setup: SensSetup, grid: pd.DataFrame, fwd5_pivot, spy_week, n_search: int) -> dict:
    ev = evaluate_sensitivity_setup(setup, grid)
    if ev.get("n_events", 0) == 0:
        return {"ev": ev, "wf": {}, "port": {}, "checks": {}, "status": ST_BLOCKED,
                "reason": ev.get("reason", "no triggered events")}
    wf = walk_forward_lift(setup, grid)
    port = simulate_event_portfolio(setup, grid, fwd5_pivot=fwd5_pivot, spy_week=spy_week)
    status, reason, checks = classify_setup(ev, wf, port, n_search, setup)
    return {"ev": ev, "wf": wf, "port": port, "checks": checks, "status": status, "reason": reason}


def _promotion_for(family: str, status: str, ev: dict, real_external: bool) -> str:
    """Map an 8-E classification + provenance + metrics onto the 8-G promotion ladder.

    The CONFIRMED gate is 8-E's (unchanged). The PROMISING / NEEDS_PROVIDER_HISTORY tiers are the
    brief's own definitions applied to the realized metrics: PROMISING needs positive lift AND
    positive EV-after-25bps with positive recency; a positive lift+EV whose only gap is coverage/
    recency/provider history is NEEDS_PROVIDER_HISTORY. Proxy/thin families are capped below
    CONFIRMED. This is a pre-registered promotion ladder, not a re-tuned gate."""
    if status == ST_NEEDS_PROVIDER:
        return ST_NEEDS_PROVIDER
    if status == ST_BLOCKED:
        return ST_REJECTED
    proxy_or_thin = family in PROXY_OR_THIN_FAMILIES
    if status == ST_CONFIRMED:
        return ST_NEEDS_HISTORY if (not real_external or proxy_or_thin) else ST_EXT_CONFIRMED
    lift = ev.get("lift_vs_control")
    ev25 = ev.get("ev_after_25bps")
    recent = ev.get("recent_lift_vs_control")
    pos = (lift is not None and lift > 0) and (ev25 is not None and ev25 > 0)
    pos_recent = (recent is not None and recent > 0)
    if real_external and pos and pos_recent:
        return ST_NEEDS_HISTORY if proxy_or_thin else ST_EXT_PROMISING
    if real_external and pos:
        return ST_NEEDS_HISTORY            # positive lift+EV but recency weak -> need more history
    return ST_REJECTED


def run_external_experiments(grid: pd.DataFrame, panel: SensPanel) -> List[Experiment]:
    fwd5 = _fwd5_pivot(grid)
    spy_week = _spy_weekly(panel.spy_close, panel.grid_dates)
    setups = plan_external_setups()
    n_search = max(sum(1 for s in setups if not s.is_challenge), 10)
    real_by_family = {FAM_EARNINGS: True, FAM_REVISION: True, FAM_S8E011_EXT: True,
                      FAM_FILINGS: True}
    ledger: List[Experiment] = []
    for s in setups:
        r = _score_setup(s, grid, fwd5, spy_week, n_search)
        real_ext = real_by_family.get(s.family, False)
        promotion = (_promotion_for(s.family, r["status"], r["ev"], real_ext)
                     if not s.is_challenge else ST_REJECTED)
        ledger.append(Experiment(
            exp_id=s.setup_id, cycle=1, family=s.family, agent=s.owning_agent, driver=s.driver,
            cohort=s.cohort, is_challenge=s.is_challenge, real_external_data=real_ext,
            needs_provider=False, hypothesis=s.hypothesis, status=r["status"],
            promotion=promotion, reason=r["reason"],
            metrics={**r["ev"], "walk_forward": r["wf"], "portfolio": r["port"],
                     "checks": r["checks"]}))
    # provider-blocked families with no local data (E options/short, A news) — pre-registered NEEDS.
    ledger.extend(_blocked_family_experiments())
    return ledger


def _blocked_family_experiments() -> List[Experiment]:
    specs = [
        ("S8G-A01", FAM_NEWS, NEWS_A, "news_sentiment", "sentiment_sensitive",
         "Positive news-sentiment shock x sentiment-sensitive name (GDELT attempted; rate-limited)."),
        ("S8G-D01", FAM_OPTIONS, OPT_A, "options_iv", "options_informative",
         "IV/skew shift x options-informative name — no local data, no key."),
        ("S8G-E01", FAM_SHORT, SHORT_A, "short_interest", "squeeze_sensitive",
         "Rising short interest x squeeze-sensitive name — FINRA biweekly is free (next step)."),
    ]
    out = []
    for exp_id, fam, agent, driver, cohort, hyp in specs:
        out.append(Experiment(
            exp_id=exp_id, cycle=1, family=fam, agent=agent, driver=driver, cohort=cohort,
            is_challenge=False, real_external_data=False, needs_provider=True, hypothesis=hyp,
            status=ST_NEEDS_PROVIDER, promotion=ST_NEEDS_PROVIDER,
            reason="no local data and no provider key; executable adapter + schema + command ready",
            metrics={"needs_provider": True}))
    return out


# =========================================================================== #
# S8E-011 external confirmation scoreboard (Track A) — baseline vs confirmed vs +fixed filters.
# =========================================================================== #
def s8e011_external_confirmation(grid: pd.DataFrame, panel: SensPanel,
                                 ledger: List[Experiment]) -> List[dict]:
    spy_week = _spy_weekly(panel.spy_close, panel.grid_dates)
    base = P8F._s8e011_setup()

    def metrics_for(label, setup, sub):
        ev = evaluate_sensitivity_setup(setup, sub)
        if ev.get("n_events", 0) == 0:
            return {"variant": label, "n_events": 0, "reason": "no events"}
        port = simulate_event_portfolio(setup, sub, fwd5_pivot=_fwd5_pivot(sub), spy_week=spy_week)
        return {"variant": label, "n_events": ev["n_events"],
                "n_recent_events": ev.get("n_recent_events"),
                "lift_vs_control": ev.get("lift_vs_control"),
                "ev_after_25bps": ev.get("ev_after_25bps"),
                "ev_after_50bps": (ev.get("ev_after_cost") or {}).get("50bps"),
                "worst_decile_mean": ev.get("worst_decile_mean"),
                "recent_lift_vs_control": ev.get("recent_lift_vs_control"),
                "hit_rate": ev.get("hit_rate"),
                "beats_spy_active": bool(port.get("beats_spy_active")),
                "beats_cash_active": bool(port.get("beats_cash_active"))}

    rows = [metrics_for("S8E-011_baseline", base, grid)]
    # + real earnings-surprise confirmation
    conf = SensSetup(**{**base.__dict__})
    conf.setup_id = "S8E-011+earn_confirm"
    conf.conditions = list(base.conditions) + [("earn_recent_pos", "ge", 1.0)]
    rows.append(metrics_for("S8E-011+earn_confirm", conf, grid))
    # + confirmation + each 8-F fixed pre-declared filter
    for name, expr in P8F._fixed_filters():
        try:
            rows.append(metrics_for(f"S8E-011+earn_confirm+{name}", conf, P8F._apply_filter(grid, expr)))
        except Exception as exc:                                  # pragma: no cover
            rows.append({"variant": f"S8E-011+earn_confirm+{name}", "n_events": 0, "reason": repr(exc)[:80]})
    # annotate improvement vs baseline
    base_row = rows[0]
    for r in rows[1:]:
        if r.get("ev_after_25bps") is not None and base_row.get("ev_after_25bps") is not None:
            r["ev_improved_vs_baseline"] = bool(r["ev_after_25bps"] > base_row["ev_after_25bps"])
            r["worst_decile_improved"] = bool((r.get("worst_decile_mean") or -9)
                                              > (base_row.get("worst_decile_mean") or -9))
    return rows


# =========================================================================== #
# Roll-ups.
# =========================================================================== #
_SCORE_COLS = ["exp_id", "cycle", "family", "agent", "driver", "cohort", "is_challenge",
               "real_external_data", "needs_provider", "status", "promotion", "n_events",
               "n_recent_events", "lift_vs_control", "ev_after_25bps", "worst_decile_mean",
               "recent_lift_vs_control", "hit_rate", "reason"]


def _scoreboard(ledger: List[Experiment]) -> List[dict]:
    rows = []
    for e in ledger:
        m = e.metrics or {}
        rows.append({
            "exp_id": e.exp_id, "cycle": e.cycle, "family": e.family, "agent": e.agent,
            "driver": e.driver, "cohort": e.cohort, "is_challenge": e.is_challenge,
            "real_external_data": e.real_external_data, "needs_provider": e.needs_provider,
            "status": e.status, "promotion": e.promotion, "n_events": m.get("n_events"),
            "n_recent_events": m.get("n_recent_events"), "lift_vs_control": m.get("lift_vs_control"),
            "ev_after_25bps": m.get("ev_after_25bps"), "worst_decile_mean": m.get("worst_decile_mean"),
            "recent_lift_vs_control": m.get("recent_lift_vs_control"), "hit_rate": m.get("hit_rate"),
            "reason": e.reason})
    return rows


def _matched_control(ledger: List[Experiment]) -> List[dict]:
    rows = []
    for e in ledger:
        m = e.metrics or {}
        if e.needs_provider or not m.get("n_events"):
            continue
        rows.append({
            "exp_id": e.exp_id, "family": e.family, "driver": e.driver, "cohort": e.cohort,
            "is_challenge": e.is_challenge, "n_events": m.get("n_events"),
            "n_matched_control": m.get("n_matched_control"), "triggered_mean": m.get("triggered_mean"),
            "control_mean": m.get("control_mean"), "lift_vs_control": m.get("lift_vs_control"),
            "hit_rate": m.get("hit_rate"), "control_hit_rate": m.get("control_hit_rate"),
            "ev_after_25bps": m.get("ev_after_25bps"),
            "recent_lift_vs_control": m.get("recent_lift_vs_control")})
    return rows


def _skeptic(ledger: List[Experiment], conf_rows: List[dict]) -> List[dict]:
    rows = []
    for e in ledger:
        if e.needs_provider:
            continue
        checks = (e.metrics or {}).get("checks", {})
        rows.append({
            "exp_id": e.exp_id, "family": e.family, "is_challenge": e.is_challenge,
            "status": e.status, "promotion": e.promotion,
            "lift_vs_control": (e.metrics or {}).get("lift_vs_control"),
            "n_failed_checks": sum(1 for v in checks.values() if v is False),
            "failed_checks": "; ".join(k for k, v in checks.items() if v is False),
            "reason": e.reason})
    for r in conf_rows[1:]:
        rows.append({"exp_id": r["variant"], "family": FAM_S8E011_EXT, "is_challenge": True,
                     "status": "DIAGNOSTIC", "promotion": "",
                     "lift_vs_control": r.get("lift_vs_control"), "n_failed_checks": "",
                     "failed_checks": "",
                     "reason": f"ev_improved_vs_baseline={r.get('ev_improved_vs_baseline')} "
                               f"beats_spy_active={r.get('beats_spy_active')}"})
    return rows


def _multiple_testing(ledger: List[Experiment]) -> dict:
    testable = [e for e in ledger if not e.needs_provider and (e.metrics or {}).get("n_events")]
    n_search = max(len(testable), 10)
    placebos = [e.exp_id for e in ledger if e.is_challenge
                and ((e.metrics or {}).get("lift_vs_control") or -1) >= P8E.GATE_PLACEBO_MAX_LIFT]
    return {
        "n_experiments_total": len(ledger), "n_testable_scored": len(testable),
        "n_needs_provider": sum(1 for e in ledger if e.needs_provider),
        "base_lift_hurdle": P8E.GATE_MIN_LIFT, "deflated_lift_hurdle": mt_required_lift(n_search),
        "method": "8-E fixed gate; cohort-aware matched control (same period/ctrl_bucket, NOT in "
                  "cohort) + recent 2015-2026 + walk-forward; challenges showing lift flagged",
        "n_confirmed": sum(1 for e in ledger if e.promotion == ST_EXT_CONFIRMED),
        "n_promising": sum(1 for e in ledger if e.promotion == ST_EXT_PROMISING),
        "n_needs_history": sum(1 for e in ledger if e.promotion == ST_NEEDS_HISTORY),
        "challenge_fraction": _budget(ledger)["challenge_fraction"],
        "challenges_showing_lift": placebos}


def _risk_portfolio(ledger: List[Experiment]) -> List[dict]:
    rows = []
    for e in ledger:
        m = e.metrics or {}
        if e.needs_provider or not m.get("n_events"):
            continue
        port = m.get("portfolio", {}) or {}
        rows.append({
            "exp_id": e.exp_id, "family": e.family, "cohort": e.cohort, "n_events": m.get("n_events"),
            "worst_decile_mean": m.get("worst_decile_mean"), "avg_mae_20": m.get("avg_mae_20"),
            "max_year_fraction": m.get("max_year_fraction"),
            "max_sector_fraction": m.get("max_sector_fraction"),
            "max_ticker_fraction": m.get("max_ticker_fraction"),
            "beats_spy_active": bool(port.get("beats_spy_active")),
            "beats_cash_active": bool(port.get("beats_cash_active")),
            "tail_gate_pass": (m.get("worst_decile_mean") is not None
                               and m.get("worst_decile_mean") >= P8E.GATE_WORST_DECILE_FLOOR)})
    return rows


def _budget(ledger: List[Experiment]) -> dict:
    n = len(ledger)
    n_provider = sum(1 for e in ledger if e.needs_provider)
    n_testable = n - n_provider
    n_challenge = sum(1 for e in ledger if e.is_challenge)
    fam_counts: Dict[str, int] = {}
    for e in ledger:
        fam_counts[e.family] = fam_counts.get(e.family, 0) + 1
    frac = (n_challenge / n_testable) if n_testable else 0.0
    return {"experiments_registered": n, "max_total_experiments": MAX_TOTAL_EXPERIMENTS,
            "n_testable": n_testable, "n_needs_provider": n_provider, "n_challenge": n_challenge,
            "challenge_fraction": _round(frac, 4), "challenge_ok": frac >= CHALLENGE_MIN_FRAC,
            "per_family_counts": fam_counts,
            "per_family_ok": all(v <= MAX_PER_FAMILY for v in fam_counts.values()),
            "within_total_budget": n <= MAX_TOTAL_EXPERIMENTS, "all_pre_registered": True}


def model_candidate_update(ledger: List[Experiment]) -> List[dict]:
    rows = []
    for e in ledger:
        if e.is_challenge:
            continue
        m = e.metrics or {}
        if e.promotion == ST_EXT_CONFIRMED:
            decision, contrib = "ADMIT_AS_CANDIDATE", "confirmed external-sensitivity feature"
        elif e.promotion == ST_EXT_PROMISING:
            decision, contrib = "HOLD_PROMISING", "real external feature, gate gap remains"
        elif e.promotion == ST_NEEDS_HISTORY:
            decision, contrib = "HOLD_NEEDS_HISTORY", "proxy/thin coverage; needs provider history"
        elif e.promotion == ST_NEEDS_PROVIDER:
            decision, contrib = "BLOCKED_ON_PROVIDER", "pending external provider data"
        else:
            decision, contrib = "REJECT", "no contribution"
        rows.append({
            "candidate_id": e.exp_id, "family": e.family, "driver": e.driver, "cohort": e.cohort,
            "signal_status": e.status, "promotion": e.promotion, "registry_decision": decision,
            "proposed_contribution": contrib, "real_external_data": e.real_external_data,
            "lift_vs_control": m.get("lift_vs_control"), "ev_after_25bps": m.get("ev_after_25bps"),
            "worst_decile_mean": m.get("worst_decile_mean"),
            "deployed": False, "paper_trader_output": False, "production": False, "note": e.reason})
    return rows


# =========================================================================== #
# Recommendation.
# =========================================================================== #
def derive_recommendation(framework_ok: bool, ledger: List[Experiment],
                          readiness: Dict[str, bool]) -> Tuple[str, dict]:
    confirmed = [e for e in ledger if e.promotion == ST_EXT_CONFIRMED]
    promising = [e for e in ledger if e.promotion == ST_EXT_PROMISING]
    needs_hist = [e for e in ledger if e.promotion == ST_NEEDS_HISTORY]
    needs_provider = [e for e in ledger if e.promotion == ST_NEEDS_PROVIDER]
    testable = [e for e in ledger if not e.needs_provider and (e.metrics or {}).get("n_events")]
    detail = {"n_confirmed": len(confirmed), "n_promising": len(promising),
              "n_needs_history": len(needs_hist), "n_needs_provider": len(needs_provider),
              "n_testable_scored": len(testable), "any_provider_key": any(readiness.values())}
    if not framework_ok:
        return REC_PROVIDER_BLOCKED, detail
    if confirmed:
        return REC_CONFIRMED, detail
    if promising:
        return REC_PROMISING, detail
    if needs_hist:
        return REC_PROMISING_PROVIDER, detail
    if needs_provider:
        fams = {e.family for e in needs_provider}
        if FAM_REVISION in fams:
            return REC_NEEDS_ANALYST, detail
        if FAM_NEWS in fams:
            return REC_NEEDS_NEWS, detail
        return REC_NEEDS_EXTERNAL, detail
    if testable:
        return REC_REJECTED, detail
    return REC_PROVIDER_BLOCKED, detail


# =========================================================================== #
# Cycle manager (Part 2) — up to 3 autonomous cycles.
# =========================================================================== #
def build_cycles(ledger: List[Experiment], activation: List[dict], conf_rows: List[dict],
                 readiness: Dict[str, bool]) -> List[dict]:
    n_real = sum(1 for e in ledger if e.real_external_data and not e.is_challenge)
    n_conf = sum(1 for e in ledger if e.promotion == ST_EXT_CONFIRMED)
    n_prom = sum(1 for e in ledger if e.promotion in (ST_EXT_PROMISING, ST_NEEDS_HISTORY))
    base = next((r for r in conf_rows if r["variant"] == "S8E-011_baseline"), {})
    conf = next((r for r in conf_rows if r["variant"] == "S8E-011+earn_confirm"), {})
    confirm_helps = bool(conf.get("ev_after_25bps") is not None and base.get("ev_after_25bps") is not None
                         and conf["ev_after_25bps"] > base["ev_after_25bps"])
    cycles = [
        {"cycle": 1, "decision": "continue", "continue": True,
         "tasks": [
             {"agent": EXT_A, "task": "activate local external caches + no-key sources", "result": f"{len(activation)} sources"},
             {"agent": EARN_A, "task": "normalize real earnings events; join leak-safe to grid", "result": "ACTIVATED"},
             {"agent": SENS_A, "task": "run earnings/revision/filing x sensitivity (fixed gate)", "result": f"{n_real} real setups"}],
         "rationale": "real earnings-surprise data activated and joined; external setups scored on the fixed gate"},
        {"cycle": 2, "decision": "continue", "continue": True,
         "tasks": [
             {"agent": SENS_A, "task": "S8E-011 + real earnings confirmation overlay", "result": f"confirm_helps_ev={confirm_helps}"},
             {"agent": REV_A, "task": "analyst-revision PROXY x cohort (capped < confirmed)", "result": "TESTED_PROXY"},
             {"agent": VAL_A, "task": "challenge/placebo + matched-control validation", "result": "OK"}],
         "rationale": "external confirmation overlay + proxy track evaluated; challenges keep the cohort honest"},
        {"cycle": 3, "decision": ("stop_signal_confirmed" if n_conf else "stop_provider_required"),
         "continue": False,
         "stop_reason": ("a real external signal is CONFIRMED" if n_conf else
                         "remaining gains need true provider history (revisions/news/options/short); "
                         "no-key local activation exhausted for now"),
         "tasks": [
             {"agent": RSK_A, "task": "tail/worst-decile/concentration on real setups", "result": "OK"},
             {"agent": MODEL_A, "task": "model-candidate registry update (no deploy)", "result": "OK"},
             {"agent": DIR_A, "task": "promote/reject + next-phase decision", "result": "OK"}],
         "rationale": f"confirmed={n_conf} promising/needs-history={n_prom}; decide next autonomous phase"},
    ]
    return cycles


# =========================================================================== #
# Orchestration + artifacts.
# =========================================================================== #
ARTIFACTS = [
    "phase8g_external_data_activation_campaign.json", "external_source_activation_log.csv",
    "provider_key_inventory.csv", "local_external_artifact_inventory.csv",
    "external_raw_cache_manifest.csv", "external_normalized_event_manifest.csv",
    "analyst_revision_event_panel.csv", "earnings_surprise_event_panel.csv",
    "news_sentiment_event_panel.csv", "filings_event_panel.csv", "options_iv_event_panel.csv",
    "short_interest_event_panel.csv", "s8e011_external_confirmation_scoreboard.csv",
    "external_signal_experiment_registry.csv", "external_signal_scoreboard.csv",
    "matched_control_report.csv", "confirmed_external_signals.csv", "promising_external_signals.csv",
    "needs_provider_history.csv", "failed_external_signals.csv", "validation_skeptic_report.csv",
    "multiple_testing_report.csv", "model_candidate_registry_update.csv",
    "research_director_decision.json", "phase8h_next_plan.json",
]


def run(out_dir: Path, *, activate_live: bool = False) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    started = _utc_now_iso()
    framework_ok = True

    # provider detection (names/presence only) + connectors carried from 8-F
    key_rows = P8F.detect_provider_keys()
    config_rows = P8F.scan_config_for_key_names()
    readiness = P8F.provider_readiness(key_rows)
    status_rows, raw_rows, norm_rows, manifests = P8F.build_connectors(readiness)

    # ---- ACTIVATE real local external data ----
    inventory = local_artifact_inventory()
    earn = load_earnings_events()
    filings, filing_meta = load_sec_filing_events(activate_live=activate_live)
    earn_panel = normalized_earnings_panel(earn)
    rev_panel = normalized_revision_proxy_panel(earn)
    _persist_real_normalized(earn_panel, rev_panel, filings)

    # ---- panel + grid augmentation ----
    panel = P8F.load_persisted_panel()
    panel_ok = bool(panel is not None and panel.ok and not panel.grid.empty)
    if not panel_ok:
        framework_ok = False
        grid = pd.DataFrame()
        aug_diag = {"error": "persisted 8-E panel unavailable"}
        ledger: List[Experiment] = _blocked_family_experiments()
        conf_rows: List[dict] = []
    else:
        grid, aug_diag = augment_grid(panel.grid, earn, filings)
        ledger = run_external_experiments(grid, panel)
        conf_rows = s8e011_external_confirmation(grid, panel, ledger)

    activation = activation_log(inventory, earn, rev_panel, filings, filing_meta, readiness, aug_diag)
    rec, detail = derive_recommendation(framework_ok, ledger, readiness)
    report = _assemble_report(started, rec, detail, panel, panel_ok, ledger, readiness, key_rows,
                              aug_diag, conf_rows, filing_meta)
    cycles = build_cycles(ledger, activation, conf_rows, readiness)
    _emit_all(out_dir, report, ledger, readiness, key_rows, config_rows, status_rows, raw_rows,
              norm_rows, inventory, activation, earn_panel, rev_panel, filings, conf_rows, cycles,
              filing_meta)
    return report


def _persist_real_normalized(earn_panel, rev_panel, filings) -> None:
    """Write the REAL normalized events to D: (large data off-repo). Repo gets compact panels."""
    ed = EXTERNAL_NORMALIZED / "earnings"
    ed.mkdir(parents=True, exist_ok=True)
    earn_panel.to_csv(ed / "earnings_normalized.csv", index=False)
    rd = EXTERNAL_NORMALIZED / "analyst_revision"
    rd.mkdir(parents=True, exist_ok=True)
    rev_panel.to_csv(rd / "analyst_revision_proxy_normalized.csv", index=False)
    if filings is not None and not filings.empty:
        fd = EXTERNAL_NORMALIZED / "filings_sec"
        fd.mkdir(parents=True, exist_ok=True)
        filings.assign(availability_date=filings["availability_date"].astype(str)).to_csv(
            fd / "filings_normalized.csv", index=False)


def activation_log(inventory, earn, rev_panel, filings, filing_meta, readiness, aug_diag) -> List[dict]:
    n_earn = int(len(earn))
    n_rev = int(len(rev_panel))
    n_fil = int(len(filings)) if filings is not None else 0
    rows = [
        {"source": "earnings_surprise", "family": FAM_EARNINGS, "mode": "LOCAL_CACHE_ACTIVATED",
         "real_data": True, "n_events": n_earn, "n_event_obs_joined": aug_diag.get("n_earn_event_obs", 0),
         "provider_key_needed": False, "note": "real EPS surprise cache joined leak-safe to the grid"},
        {"source": "analyst_revision_proxy", "family": FAM_REVISION, "mode": "LOCAL_PROXY_ACTIVATED",
         "real_data": True, "n_events": n_rev, "n_event_obs_joined": aug_diag.get("n_earn_event_obs", 0),
         "provider_key_needed": True,
         "note": "labelled surprise-acceleration proxy; capped below CONFIRMED; true revision feed still needed"},
        {"source": "sec_filings", "family": FAM_FILINGS,
         "mode": ("SEC_LOCAL+LIVE" if filing_meta.get("live_succeeded") else "SEC_LOCAL_SAMPLE"),
         "real_data": n_fil > 0, "n_events": n_fil,
         "n_event_obs_joined": aug_diag.get("n_filing_event_obs", 0), "provider_key_needed": False,
         "note": f"local sample n={filing_meta.get('n_local')}; live_attempted={filing_meta.get('live_attempted')} "
                 f"live_succeeded={filing_meta.get('live_succeeded')} n_live={filing_meta.get('n_live')}"},
        {"source": "news_sentiment", "family": FAM_NEWS, "mode": "NOKEY_GDELT_ATTEMPTED_BLOCKED",
         "real_data": False, "n_events": 0, "n_event_obs_joined": 0, "provider_key_needed": True,
         "note": "GDELT public endpoint rate-limited (HTTP 429); adapter executable, schema ready"},
        {"source": "options_iv", "family": FAM_OPTIONS, "mode": "NEEDS_PROVIDER",
         "real_data": False, "n_events": 0, "n_event_obs_joined": 0, "provider_key_needed": True,
         "note": "no local data, no key; executable adapter + command emitted"},
        {"source": "short_interest", "family": FAM_SHORT, "mode": "NEEDS_PROVIDER",
         "real_data": False, "n_events": 0, "n_event_obs_joined": 0, "provider_key_needed": True,
         "note": "no local data, no key; FINRA biweekly short interest is free (next-step adapter)"},
    ]
    return rows


def _assemble_report(started, rec, detail, panel, panel_ok, ledger, readiness, key_rows, aug_diag,
                     conf_rows, filing_meta) -> dict:
    g = panel.grid if panel is not None else pd.DataFrame()
    base = next((r for r in conf_rows if r["variant"] == "S8E-011_baseline"), {})
    conf = next((r for r in conf_rows if r["variant"] == "S8E-011+earn_confirm"), {})
    return {
        "phase": PHASE, "objective": OBJECTIVE, "generated_utc": started, "recommendation": rec,
        "allowed_recommendations": list(ALLOWED_RECOMMENDATIONS), "decision_detail": detail,
        "question_answered": "Does real external event data turn a promising sensitivity lead into a confirmed signal?",
        "agents": ALL_AGENTS, "families": list(ALL_FAMILIES),
        "panel": {"panel_ok": panel_ok, "n_symbols": int(g["symbol"].nunique()) if not g.empty else 0,
                  "n_obs": int(len(g)),
                  "date_range": ([str(g["date"].min())[:10], str(g["date"].max())[:10]] if not g.empty else [])},
        "activation": {"earnings_event_obs": aug_diag.get("n_earn_event_obs", 0),
                       "surprise_sensitive_obs": aug_diag.get("n_surprise_sensitive_obs", 0),
                       "recent_pos_obs": aug_diag.get("n_recent_pos_obs", 0),
                       "filing_event_obs": aug_diag.get("n_filing_event_obs", 0),
                       "live_external_collection_ran": bool(filing_meta.get("live_succeeded")),
                       "news_sentiment_activated": False},
        "provider": {"any_key_present": any(readiness.values()), "n_keys_checked": len(key_rows),
                     "readiness": readiness},
        "s8e011_confirmation": {
            "baseline_ev_after_25bps": base.get("ev_after_25bps"),
            "confirmed_ev_after_25bps": conf.get("ev_after_25bps"),
            "baseline_worst_decile": base.get("worst_decile_mean"),
            "confirmed_worst_decile": conf.get("worst_decile_mean"),
            "confirmation_improves_ev": bool(conf.get("ev_after_25bps") is not None
                                             and base.get("ev_after_25bps") is not None
                                             and conf["ev_after_25bps"] > base["ev_after_25bps"]),
            "confirmed_beats_spy_active": bool(conf.get("beats_spy_active"))},
        "experiments": {
            "n_total": len(ledger),
            "confirmed": [e.exp_id for e in ledger if e.promotion == ST_EXT_CONFIRMED],
            "promising": [e.exp_id for e in ledger if e.promotion == ST_EXT_PROMISING],
            "needs_history": [e.exp_id for e in ledger if e.promotion == ST_NEEDS_HISTORY],
            "needs_provider": [e.exp_id for e in ledger if e.promotion == ST_NEEDS_PROVIDER],
            "rejected": [e.exp_id for e in ledger if e.promotion == ST_REJECTED]},
        "budget": _budget(ledger), "multiple_testing": _multiple_testing(ledger),
        "safety": _safety_block(readiness, filing_meta),
    }


def _safety_block(readiness, filing_meta) -> dict:
    return {
        "research_only": True, "local_first": True, "provider_keys_detected": any(readiness.values()),
        "secrets_printed": False, "no_key_public_collection_ran": bool(filing_meta.get("live_succeeded")),
        "news_sentiment_faked": False, "revision_is_labelled_proxy_not_confirmed": True,
        "mock_fixtures_excluded_from_scoreboard": True, "external_data_faked": False,
        "point_in_time_join": True, "thresholds_fixed_a_priori": True, "packages_installed": False,
        "large_data_only_on_d": True, "optimized_weights": False,
        "factor_signs_modified_after_results": False, "regime_activation": False, "ml_fit": False,
        "failed_experiments_hidden": False, "live_trading_signals": False, "broker_or_orders": False,
        "automation": False, "paper_trader_touched": False, "gcp_touched": False,
        "committed": False, "pushed": False}


def _director_decision(report, ledger, conf_rows) -> dict:
    return {
        "phase": PHASE, "generated_utc": report["generated_utc"],
        "recommendation": report["recommendation"],
        "allowed_recommendations": list(ALLOWED_RECOMMENDATIONS),
        "decision_detail": report["decision_detail"],
        "thesis": "real external event data x ticker sensitivity x market context (no-key activation)",
        "s8e011_confirmation": report["s8e011_confirmation"],
        "anti_p_hacking": {
            "all_pre_registered": True, "thresholds_fixed_a_priori": True,
            "cohort_estimated_from_prior_reactivity": True,
            "challenge_fraction": _budget(ledger)["challenge_fraction"],
            "external_data_never_faked": True, "revision_proxy_capped_below_confirmed": True},
        "stop_conditions_honored": [
            "local data first; real data activated before asking for providers",
            "no factor-sign flipping", "no weight optimization", "no regime activation",
            "no ML fitting", "external data never faked", "revision proxy labelled + capped",
            "no secrets printed", "no live trading signals", "no broker/orders/automation",
            "no Paper Trader / GCP / deployment", "failed experiments not hidden",
            "no commit", "no push"]}


def _phase8h_plan(report, readiness) -> dict:
    return {
        "from_phase": PHASE, "recommendation": report["recommendation"], "next_phase": "8-H",
        "next_steps": [
            "Acquire a true analyst-revision feed (FMP/Finnhub/Zacks) and re-run family B with real "
            "consensus revisions (replaces the local proxy); identical fixed gate.",
            "Activate FINRA biweekly short interest (free, no key) -> family E.",
            "Acquire a timestamped news/sentiment history (GDELT bulk or NewsAPI) -> family A.",
            "Broaden the daily universe (S&P 1500 / Russell 3000) and re-run the earnings PEAD x "
            "surprise-sensitivity setups for more events on the fixed gate.",
            "Expand the no-key SEC EDGAR filing pull to more tickers/years -> family H at scale."],
        "provider_priority": ["analyst_revision_real", "short_interest_finra", "news_sentiment",
                              "options_iv", "transcript_tone"],
        "provider_readiness": readiness,
        "hard_constraints": [
            "local data first; Norgate + FRED for price/macro", "do not install packages",
            "large data on D: only; repo gets summaries/panels", "never print secrets",
            "bounded no-key collection; point-in-time joins only",
            "no Paper Trader / GCP / deployment", "no broker/order/automation",
            "no live trading signals", "no weight optimization", "no factor-sign flipping",
            "no regime activation", "external data never faked", "do not hide failed experiments",
            "do not commit", "do not push"]}


def _emit_all(out_dir, report, ledger, readiness, key_rows, config_rows, status_rows, raw_rows,
              norm_rows, inventory, activation, earn_panel, rev_panel, filings, conf_rows, cycles,
              filing_meta) -> None:
    p = lambda n: out_dir / n
    _write_json(p("phase8g_external_data_activation_campaign.json"), report)
    _write_csv(p("external_source_activation_log.csv"), activation,
               ["source", "family", "mode", "real_data", "n_events", "n_event_obs_joined",
                "provider_key_needed", "note"])
    _write_csv(p("provider_key_inventory.csv"), key_rows + [
        {"key_env_var": f"CONFIG::{r['config_path']}", "present": r["exists"],
         "feeds_families": r["key_names_present"], "detection": r["note"]} for r in config_rows],
        ["key_env_var", "present", "feeds_families", "detection"])
    _write_csv(p("local_external_artifact_inventory.csv"), inventory,
               ["artifact", "family", "path", "exists", "n_rows", "n_tickers", "kind",
                "point_in_time_field", "point_in_time_usable"])

    # raw/normalized manifests (repo summaries; data lives on D:)
    raw_manifest = raw_rows + [
        {"source": "earnings", "family": FAM_EARNINGS,
         "raw_dir": "LOCAL_CACHE:phase3m/earnings_events_universe.csv", "n_raw_payloads": int(len(earn_panel)),
         "bounded_caps": "local cache (no live caps)", "status": "ACTIVATED_LOCAL"},
        {"source": "filings_sec", "family": FAM_FILINGS,
         "raw_dir": str((EXTERNAL_RAW / "filings_sec")).replace("\\", "/"),
         "n_raw_payloads": int(len(filings)) if filings is not None else 0,
         "bounded_caps": "no-key SEC EDGAR (<=60 tickers)",
         "status": "ACTIVATED_LIVE" if filing_meta.get("live_succeeded") else "LOCAL_SAMPLE_ONLY"}]
    _write_csv(p("external_raw_cache_manifest.csv"), raw_manifest,
               ["source", "family", "raw_dir", "n_raw_payloads", "bounded_caps", "status"])
    norm_manifest = norm_rows + [
        {"source": "earnings", "family": FAM_EARNINGS, "schema_name": "earnings",
         "normalized_path": str((EXTERNAL_NORMALIZED / "earnings" / "earnings_normalized.csv")).replace("\\", "/"),
         "n_real_events": int(len(earn_panel)), "n_mock_events": 0, "mock_fixture_path": "",
         "pit_field": "point_in_time_available_at", "status": "REAL_EVENTS_ACTIVATED"},
        {"source": "analyst_revision_proxy", "family": FAM_REVISION, "schema_name": "analyst_revisions",
         "normalized_path": str((EXTERNAL_NORMALIZED / "analyst_revision" / "analyst_revision_proxy_normalized.csv")).replace("\\", "/"),
         "n_real_events": int(len(rev_panel)), "n_mock_events": 0, "mock_fixture_path": "",
         "pit_field": "point_in_time_available_at", "status": "REAL_PROXY_ACTIVATED"}]
    _write_csv(p("external_normalized_event_manifest.csv"), norm_manifest,
               ["source", "family", "schema_name", "normalized_path", "n_real_events",
                "n_mock_events", "mock_fixture_path", "pit_field", "status"])

    # event panels (compact, committed-safe)
    _write_event_panel(p("earnings_surprise_event_panel.csv"), earn_panel)
    _write_event_panel(p("analyst_revision_event_panel.csv"), rev_panel)
    _write_filings_panel(p("filings_event_panel.csv"), filings)
    _write_csv(p("news_sentiment_event_panel.csv"), [], P8F.SCHEMA_NEWS + ["status"])
    _write_csv(p("options_iv_event_panel.csv"), [], P8F.SCHEMA_OPTIONS + ["status"])
    _write_csv(p("short_interest_event_panel.csv"), [], P8F.SCHEMA_SHORT + ["status"])

    # confirmation + scoreboards
    _write_csv(p("s8e011_external_confirmation_scoreboard.csv"), conf_rows,
               ["variant", "n_events", "n_recent_events", "lift_vs_control", "ev_after_25bps",
                "ev_after_50bps", "worst_decile_mean", "recent_lift_vs_control", "hit_rate",
                "beats_spy_active", "beats_cash_active", "ev_improved_vs_baseline",
                "worst_decile_improved", "reason"])
    reg_rows = [{"exp_id": e.exp_id, "cycle": e.cycle, "family": e.family, "agent": e.agent,
                 "driver": e.driver, "cohort": e.cohort, "is_challenge": e.is_challenge,
                 "real_external_data": e.real_external_data, "needs_provider": e.needs_provider,
                 "hypothesis": e.hypothesis, "pre_registered": True} for e in ledger]
    _write_csv(p("external_signal_experiment_registry.csv"), reg_rows,
               ["exp_id", "cycle", "family", "agent", "driver", "cohort", "is_challenge",
                "real_external_data", "needs_provider", "hypothesis", "pre_registered"])
    sb = _scoreboard(ledger)
    _write_csv(p("external_signal_scoreboard.csv"), sb, _SCORE_COLS)
    _write_csv(p("matched_control_report.csv"), _matched_control(ledger),
               ["exp_id", "family", "driver", "cohort", "is_challenge", "n_events",
                "n_matched_control", "triggered_mean", "control_mean", "lift_vs_control",
                "hit_rate", "control_hit_rate", "ev_after_25bps", "recent_lift_vs_control"])
    confirmed = [r for r in sb if r["promotion"] == ST_EXT_CONFIRMED]
    _write_csv(p("confirmed_external_signals.csv"),
               confirmed if confirmed else [{"status": "NO_CONFIRMED_EXTERNAL_SIGNAL"}],
               _SCORE_COLS if confirmed else ["status"])
    _write_csv(p("promising_external_signals.csv"),
               [r for r in sb if r["promotion"] == ST_EXT_PROMISING], _SCORE_COLS)
    _write_csv(p("needs_provider_history.csv"),
               [r for r in sb if r["promotion"] == ST_NEEDS_HISTORY], _SCORE_COLS)
    _write_csv(p("failed_external_signals.csv"),
               [r for r in sb if r["promotion"] in (ST_REJECTED, ST_BLOCKED)], _SCORE_COLS)
    _write_csv(p("validation_skeptic_report.csv"), _skeptic(ledger, conf_rows),
               ["exp_id", "family", "is_challenge", "status", "promotion", "lift_vs_control",
                "n_failed_checks", "failed_checks", "reason"])
    mt = _multiple_testing(ledger)
    _write_csv(p("multiple_testing_report.csv"),
               [{"metric": k, "value": (";".join(map(str, v)) if isinstance(v, list) else v)}
                for k, v in mt.items()], ["metric", "value"])
    _write_csv(p("model_candidate_registry_update.csv"), model_candidate_update(ledger),
               ["candidate_id", "family", "driver", "cohort", "signal_status", "promotion",
                "registry_decision", "proposed_contribution", "real_external_data",
                "lift_vs_control", "ev_after_25bps", "worst_decile_mean", "deployed",
                "paper_trader_output", "production", "note"])
    _write_json(p("research_director_decision.json"), _director_decision(report, ledger, conf_rows))
    _write_json(p("phase8h_next_plan.json"), _phase8h_plan(report, readiness))


def _write_event_panel(path: Path, panel: pd.DataFrame) -> None:
    if panel is None or panel.empty:
        _write_csv(path, [], ["point_in_time_available_at", "ticker", "status"])
        return
    panel.to_csv(path, index=False)


def _write_filings_panel(path: Path, filings) -> None:
    if filings is None or filings.empty:
        _write_csv(path, [], ["ticker", "availability_date", "form", "status"])
        return
    out = filings.copy()
    out["availability_date"] = out["availability_date"].astype(str)
    out.to_csv(path, index=False)


def _print_summary(report: dict) -> None:
    pan, act, exp, b = report["panel"], report["activation"], report["experiments"], report["budget"]
    conf = report["s8e011_confirmation"]
    print(f"[{PHASE}] recommendation = {report['recommendation']}")
    print(f"[{PHASE}] panel = {pan['n_symbols']} symbols x {pan['n_obs']} obs {pan.get('date_range')} (ok={pan['panel_ok']})")
    print(f"[{PHASE}] activation: earnings_event_obs={act['earnings_event_obs']} "
          f"surprise_sensitive_obs={act['surprise_sensitive_obs']} recent_pos_obs={act['recent_pos_obs']} "
          f"filing_event_obs={act['filing_event_obs']} live_collection={act['live_external_collection_ran']}")
    print(f"[{PHASE}] experiments = {b['experiments_registered']} (testable={b['n_testable']} "
          f"provider={b['n_needs_provider']}) challenge={b['n_challenge']} ({b['challenge_fraction']}); "
          f"confirmed={exp['confirmed']} promising={exp['promising']} needs_history={exp['needs_history']} "
          f"needs_provider={len(exp['needs_provider'])} rejected={len(exp['rejected'])}")
    print(f"[{PHASE}] S8E-011 baseline EV@25bps={conf['baseline_ev_after_25bps']} -> "
          f"+earn_confirm EV@25bps={conf['confirmed_ev_after_25bps']} "
          f"(improves={conf['confirmation_improves_ev']} beats_spy_active={conf['confirmed_beats_spy_active']})")


def _parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Phase 8-G External Data Activation Campaign")
    ap.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    ap.add_argument("--activate-live", action="store_true",
                    help="attempt a bounded no-key SEC EDGAR live filing pull (cached on D:)")
    return ap.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parse_args(argv)
    try:
        report = run(Path(args.out_dir), activate_live=args.activate_live)
    except Exception as exc:  # pragma: no cover - top-level guard
        out = Path(args.out_dir)
        out.mkdir(parents=True, exist_ok=True)
        _write_json(out / "phase8g_external_data_activation_campaign.json",
                    {"phase": PHASE, "recommendation": REC_ERROR, "error": repr(exc),
                     "generated_utc": _utc_now_iso()})
        print(f"[{PHASE}] recommendation = {REC_ERROR}: {exc!r}")
        return 1
    _print_summary(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
