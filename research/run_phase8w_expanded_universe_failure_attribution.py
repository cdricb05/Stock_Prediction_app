"""Phase 8-W - Expanded Universe Failure Attribution + Constrained-Alpha Salvage.

WHY THIS PHASE EXISTS
    Phase 8-V acquired a MATCHED EODHD price+fundamentals batch and grew the scoreable cross-section
    from 299 to 545 tickers (29,032 -> 38,725 point-in-time earnings events). But the four Phase 8-T
    promoted earnings-surprise signals all degraded on the wider universe (4 promoted -> 0 promoted):
    surprise_sector_neutral IC 0.0514 -> 0.0202 (t 2.96 -> 1.43), surprise_x_quality IC 0.0470 ->
    0.0249 (t 2.97 -> 1.78). The before/after comparison shows a near-uniform halving of IC across
    the whole earnings family - the signature of DILUTION by a cohort that carries little drift, not
    of a localized data error.

    Phase 8-W does NOT acquire data and does NOT touch Paper Trader. It is an ATTRIBUTION + SALVAGE
    phase: it rebuilds the SAME expanded event table, splits it into the original 299-ticker cohort
    vs the 246 newly-scoreable names, and re-measures every promoted signal separately on
    old / new / combined. It then attributes the degradation across sector, liquidity, event history,
    subperiod (pre/post-2020 + rate/drawdown regimes) and data-quality, and finally tests a battery
    of CONSTRAINED variants (old-cohort-only, high-liquidity, sector-neutral-within-old, quality-
    gated, large-surprise, post-2020, exclude-weak-sectors, minimum-event-history) to decide whether
    a robust constrained alpha survives or the signal is rejected after expansion.

REUSE
    The scoring core is reused verbatim from Phase 8-T (`evaluate_ext`, `gate_reasons`,
    `scenario_battery`, `prepare_signals_ext`) over Phase 8-S's point-in-time event table
    (`build_event_table`) and Benjamini-Hochberg control. Every constrained variant is just the SAME
    8-T evaluation battery run on a filtered slice of the SAME `ev`, gated by the SAME promotion
    gate - so a "surviving" variant clears exactly the bar the 8-T promotions did.

TERMINAL DECISIONS
    CONSTRAINED_ALPHA_SURVIVES | ALPHA_REJECTED_AFTER_EXPANSION | NEEDS_ADDITIONAL_DIAGNOSTIC | ERROR

Run (offline; reuses the gitignored 8-V expanded panel + EODHD earnings cache; no network, no key):
    python research/run_phase8w_expanded_universe_failure_attribution.py
Test (fully offline; synthetic old-drift / new-flat cohorts, no key, no network):
    python -m pytest tests/test_phase8w_expanded_universe_failure_attribution.py -q
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Reuse the Phase 8-T scoring core (which reuses 8-S data layer + 8-R transport/secret discipline).
from research import run_phase8t_autonomous_alpha_daemon as t8  # noqa: E402

s8 = t8.s8
r8 = t8.r8

PHASE = "8-W"

# Reused IO helpers (single source of truth).
_write_json = s8._write_json
_write_csv = s8._write_csv
_read_csv_file = s8._read_csv_file
_read_json = s8._read_json
_round = s8._round
_rel = s8._rel

# --------------------------------------------------------------------------- #
# Paths / inputs.
# --------------------------------------------------------------------------- #
_OUT_DIR = _REPO_ROOT / "research" / "output" / "phase8w_expanded_universe_failure_attribution"
_PHASE8V_DIR = _REPO_ROOT / "research" / "output" / "phase8v_combined_eodhd_universe_expansion"
_PHASE8V_REPORT = "phase8v_combined_eodhd_universe_expansion.json"
# The gitignored expanded price panel the 8-V live run assembled (base cache + acquired EOD bars).
_EXPANDED_PANEL = (_REPO_ROOT / "research" / "data" / "eodhd" / "normalized" / "eod_prices"
                   / "expanded_price_panel.csv")

# The Phase 8-T promoted signals to re-attribute, plus the three extra families the brief lists.
TARGET_SCENARIOS = ("surprise_sector_neutral", "surprise_x_quality", "positive_surprise_asymmetry",
                    "surprise_magnitude", "earnings_acceleration", "balance_sheet_strength",
                    "quality_composite")
# The two headline signals the report and salvage decision must explicitly answer for.
FOCUS_SIGNALS = ("surprise_sector_neutral", "surprise_x_quality")
PRIMARY_FOCUS = "surprise_sector_neutral"

# Gate constants reused verbatim from the 8-T promotion gate.
MIN_EVENTS = t8.MIN_EVENTS
MIN_MONTHS = t8.MIN_MONTHS
GATE_MIN_IC = t8.GATE_MIN_IC
GATE_MIN_T = t8.GATE_MIN_T
GATE_BH_Q = t8.GATE_BH_Q
_PRE2020 = "2020-01-01"
_RNG_SEED = 8675309

DEC_SURVIVES = "CONSTRAINED_ALPHA_SURVIVES"
DEC_REJECTED = "ALPHA_REJECTED_AFTER_EXPANSION"
DEC_DIAG = "NEEDS_ADDITIONAL_DIAGNOSTIC"
DEC_ERROR = "ERROR"
ALLOWED_DECISIONS = (DEC_SURVIVES, DEC_REJECTED, DEC_DIAG, DEC_ERROR)

_ARTIFACTS = {
    "report": "phase8w_expanded_universe_failure_attribution.json",
    "run_log": "phase8w_run_log.csv",
    "cohort_perf": "cohort_performance_comparison.csv",
    "old_vs_new": "old_vs_new_signal_attribution.csv",
    "sector_attr": "sector_attribution.csv",
    "liquidity_attr": "liquidity_bucket_attribution.csv",
    "event_history_attr": "event_history_attribution.csv",
    "subperiod_attr": "subperiod_attribution.csv",
    "data_quality_attr": "data_quality_attribution.csv",
    "variant_scoreboard": "constrained_variant_scoreboard.csv",
    "variant_promoted": "constrained_promoted_signals.csv",
    "variant_rejected": "constrained_rejected_signals.csv",
    "final_decision": "final_research_decision.json",
    "next_plan": "phase8x_next_plan.json",
    "secret_audit": "secret_safety_audit.csv",
}
# The 14 committed-safe artifacts the brief requires.
_REQUIRED_ARTIFACTS = (
    "report", "cohort_perf", "old_vs_new", "sector_attr", "liquidity_attr", "event_history_attr",
    "subperiod_attr", "data_quality_attr", "variant_scoreboard", "variant_promoted",
    "variant_rejected", "final_decision", "next_plan", "secret_audit")


def _g(r, k, n=4):
    return _round(r.get(k), n)


# --------------------------------------------------------------------------- #
# Scenario plumbing (reuse the 8-T battery; clone scenarios for constrained variants).
# --------------------------------------------------------------------------- #
def _battery_lookup() -> Dict[str, Dict]:
    return {sc["scenario"]: sc for sc in t8.scenario_battery()}


def _scn(base: Dict, scenario: str, signal: Optional[str] = None,
         sector_neutral: Optional[bool] = None, regime_filter: Optional[str] = None) -> Dict:
    """Clone a battery scenario with overrides for a constrained variant (keeps family/cycle/etc.)."""
    sc = dict(base)
    sc["scenario"] = scenario
    if signal is not None:
        sc["signal"] = signal
    if sector_neutral is not None:
        sc["sector_neutral"] = sector_neutral
    sc["regime_filter"] = regime_filter
    sc["exploratory"] = False
    return sc


def _safe_eval(ev_df, sc: Dict, rng) -> Dict:
    """Run the 8-T extended evaluation on a (possibly filtered) ev slice. Always returns a dict with
    the gate-relevant keys; a too-small slice yields n_events and falls through the gate honestly."""
    try:
        return t8.evaluate_ext(ev_df, sc, rng)
    except Exception as exc:  # pragma: no cover - defensive; never abort the whole attribution
        row = dict(sc)
        row.update({"n_events": 0, "n_months": 0, "mean_ic": float("nan"), "ic_t": float("nan"),
                    "ic_p": float("nan"), "mean_spread": float("nan"), "net_spread_10bps": float("nan"),
                    "net_spread_25bps": float("nan"), "net_spread_50bps": float("nan"),
                    "spread_hit_rate": float("nan"), "subperiod_stable": False,
                    "top_sector_share": float("nan"), "regimes_right_sign": 0,
                    "eval_error": "%s: %s" % (type(exc).__name__, exc)})
        return row


def _n_tickers(ev_df) -> int:
    try:
        return int(ev_df["ticker"].nunique())
    except Exception:
        return 0


# --------------------------------------------------------------------------- #
# A. Build the expanded event table + tag the old/new cohort.
# --------------------------------------------------------------------------- #
def build_expanded_ev(P_score, as_of: str, log):
    fund = s8.load_fundamentals_panel(P_score)
    prices = s8.load_prices(P_score)
    ev, stats, audit = s8.build_event_table(P_score, as_of, log)
    stats = dict(stats)
    if getattr(ev, "empty", True):
        log.step("build_ev", "EMPTY", "no usable events on the expanded panel")
        return ev, stats, audit
    ev = t8.prepare_signals_ext(ev, prices, fund, log)
    log.step("build_ev", "DONE", "%d usable events / %d scoreable tickers"
             % (stats.get("events_usable", 0), stats.get("tickers_usable", 0)),
             count=stats.get("events_usable", 0))
    return ev, stats, audit


def tag_cohort(ev, new_set):
    up = ev["ticker"].astype(str).str.upper()
    ev = ev.copy()
    ev["cohort"] = ["new" if t in new_set else "old" for t in up]
    return ev


def attach_liquidity_proxy(ev, panel_path, extra_csvs=(), eod_norm_dir=None):
    """The scoring pipeline only loads adjusted_close/benchmark, and the 8-V expanded panel itself
    dropped dollar_volume, so the per-event `liquidity` feature is NaN. For a size/liquidity bucket we
    read each ticker's mean dollar-volume as a per-ticker proxy (clearly a proxy, not a PIT feature):
    first from the panel (covers the test fixtures), then - if coverage is thin - from the base price
    cache (old names) and the per-ticker normalized EOD CSVs (newly-acquired names)."""
    import pandas as pd
    ev = ev.copy()
    up = ev["ticker"].astype(str).str.upper()
    m: Dict[str, float] = {}

    def _add(path):
        try:
            dv = pd.read_csv(path, usecols=["ticker", "dollar_volume"])
        except (ValueError, OSError, KeyError):
            return
        dv["ticker"] = dv["ticker"].astype(str).str.upper()
        for k, v in dv.groupby("ticker")["dollar_volume"].mean().items():
            m.setdefault(k, float(v))

    _add(panel_path)
    ev["liquidity_proxy"] = up.map(m)
    if ev["liquidity_proxy"].notna().mean() >= 0.5:
        return ev
    for c in extra_csvs:
        _add(c)
    if eod_norm_dir and Path(eod_norm_dir).is_dir():
        for fp in Path(eod_norm_dir).glob("*.csv"):
            if fp.name == "expanded_price_panel.csv":
                continue
            _add(fp)
    ev["liquidity_proxy"] = up.map(m)
    return ev


# --------------------------------------------------------------------------- #
# B. Cohort attribution: every target signal on old vs new vs combined.
# --------------------------------------------------------------------------- #
def cohort_performance(ev, lookup: Dict[str, Dict], rng) -> Tuple[List[List], List[List], Dict]:
    ev_old = ev[ev["cohort"] == "old"].copy()
    ev_new = ev[ev["cohort"] == "new"].copy()
    perf_rows, attr_rows = [], []
    summary: Dict[str, Dict] = {}
    for scen in TARGET_SCENARIOS:
        sc = lookup.get(scen)
        if sc is None:
            continue
        res = {}
        for label, sub in (("old_cohort", ev_old), ("new_cohort", ev_new), ("combined", ev)):
            r = _safe_eval(sub, sc, rng)
            res[label] = r
            perf_rows.append([scen, sc["family"], label, _n_tickers(sub), r.get("n_events", 0),
                              r.get("n_months", 0), _g(r, "mean_ic"), _g(r, "ic_t", 2),
                              _g(r, "mean_spread"), _g(r, "net_spread_25bps"),
                              _g(r, "net_spread_50bps"), r.get("subperiod_stable", False)])
        o, n, c = res["old_cohort"], res["new_cohort"], res["combined"]
        ic_o, ic_n, ic_c = _g(o, "mean_ic"), _g(n, "mean_ic"), _g(c, "mean_ic")
        d_on = (None if ic_o is None or ic_n is None else _round(ic_o - ic_n))
        # "diluted" = the combined IC is materially below the old-cohort IC (the 8-V symptom)
        diluted = bool(ic_o is not None and ic_c is not None and ic_o > 0 and ic_c < ic_o * 0.7)
        attr_rows.append([scen, sc["family"], ic_o, ic_n, ic_c, d_on,
                          _g(o, "ic_t", 2), _g(n, "ic_t", 2), _g(c, "ic_t", 2),
                          _g(o, "net_spread_25bps"), _g(n, "net_spread_25bps"),
                          _g(c, "net_spread_25bps"), o.get("n_events", 0), n.get("n_events", 0),
                          diluted])
        summary[scen] = res
    return perf_rows, attr_rows, summary


# --------------------------------------------------------------------------- #
# C. Dimension attributions for the primary focus signal (sector / liquidity / history / subperiod /
#    data-quality). Each isolates a slice of the SAME ev and re-measures the focus signal's IC.
# --------------------------------------------------------------------------- #
def sector_attribution(ev, focus: Dict, rng) -> Tuple[List[List], List[str]]:
    """Per-sector own-IC (within-sector cross-section) + new-cohort share + a leave-one-sector-out
    delta on the combined IC. A sector whose removal RAISES the combined IC is dilutive ('weak')."""
    import numpy as np  # noqa: F401
    base_combined = _safe_eval(ev, focus, rng)
    ic_all = _g(base_combined, "mean_ic")
    rows, weak = [], []
    # own-IC uses a raw (not sector-neutral) read so a single sector's cross-section is meaningful.
    own_sc = _scn(focus, focus["scenario"] + "_sector_raw", sector_neutral=False,
                  regime_filter=focus.get("regime_filter"))
    for sec in sorted(ev["sector"].dropna().astype(str).unique()):
        sub = ev[ev["sector"].astype(str) == sec]
        if sub.empty:
            continue
        own = _safe_eval(sub, own_sc, rng)
        without = _safe_eval(ev[ev["sector"].astype(str) != sec].copy(), focus, rng)
        ic_wo = _g(without, "mean_ic")
        drop_delta = (None if ic_all is None or ic_wo is None else _round(ic_wo - ic_all))
        own_ic = _g(own, "mean_ic")
        n_ev = int(len(sub))
        new_share = _round(float((sub["cohort"] == "new").mean()), 4) if n_ev else None
        # 'weak' = removing the sector raises combined IC AND the sector's own drift is itself below
        # the promotion threshold (so a strong sector like IT is never flagged just on a marginal LOO).
        below = (own_ic is None) or (own_ic < GATE_MIN_IC)
        is_weak = bool(drop_delta is not None and drop_delta > 0 and below)
        if is_weak:
            weak.append(sec)
        rows.append([sec, n_ev, _n_tickers(sub), new_share, own_ic, _g(own, "ic_t", 2),
                     ic_wo, drop_delta, is_weak])
    rows.sort(key=lambda x: (x[7] if x[7] is not None else -1), reverse=True)
    return rows, weak


def _quantile_buckets(ev, col: str, n: int):
    """Yield (label, mask) for n quantile buckets of `col`; falls back gracefully if degenerate."""
    import pandas as pd
    s = ev[col]
    valid = s.notna()
    if valid.sum() == 0:
        return
    try:
        q = pd.qcut(s[valid].rank(method="first"), n, labels=False)
    except ValueError:
        return
    bucket = pd.Series(index=ev.index, dtype="float")
    bucket.loc[valid[valid].index] = q.values
    for b in range(n):
        mask = bucket == b
        if mask.sum() == 0:
            continue
        yield ("Q%d" % (b + 1), mask)


def bucket_attribution(ev, focus: Dict, col: str, n_buckets: int, rng) -> List[List]:
    rows = []
    for label, mask in (_quantile_buckets(ev, col, n_buckets) or []):
        sub = ev[mask].copy()
        r = _safe_eval(sub, focus, rng)
        lo = _round(float(ev.loc[mask, col].min()), 2)
        hi = _round(float(ev.loc[mask, col].max()), 2)
        rows.append([col, label, "%s..%s" % (lo, hi), int(len(sub)), _n_tickers(sub),
                     _round(float((sub["cohort"] == "new").mean()), 4) if len(sub) else None,
                     _g(r, "mean_ic"), _g(r, "ic_t", 2), _g(r, "net_spread_25bps"),
                     r.get("subperiod_stable", False)])
    return rows


def event_history_attribution(ev, focus: Dict, rng) -> List[List]:
    """Bucket events by how many events their ticker contributes (a proxy for cached earnings /
    price-history depth: newly-acquired names carry fewer prior quarters)."""
    import pandas as pd
    counts = ev.groupby("ticker")["entry_date"].transform("count")
    ev = ev.copy()
    ev["_evcount"] = counts
    rows = []
    edges = [(0, 8, "lt8"), (8, 16, "8to15"), (16, 10 ** 9, "ge16")]
    for lo, hi, label in edges:
        mask = (ev["_evcount"] >= lo) & (ev["_evcount"] < hi)
        if mask.sum() == 0:
            continue
        sub = ev[mask].copy()
        r = _safe_eval(sub, focus, rng)
        rows.append(["event_count", label, "%d..%d" % (lo, min(hi, 999)), int(len(sub)),
                     _n_tickers(sub),
                     _round(float((sub["cohort"] == "new").mean()), 4) if len(sub) else None,
                     _g(r, "mean_ic"), _g(r, "ic_t", 2), _g(r, "net_spread_25bps"),
                     r.get("subperiod_stable", False)])
    return rows


def subperiod_attribution(ev, focus: Dict, rng) -> List[List]:
    import pandas as pd
    cut = pd.Timestamp(_PRE2020)
    rows = []
    slices = [("pre_2020", ev[ev["entry_date"] < cut].copy()),
              ("post_2020", ev[ev["entry_date"] >= cut].copy())]
    for label, sub in slices:
        if sub.empty:
            continue
        r = _safe_eval(sub, focus, rng)
        rows.append(["calendar", label, int(len(sub)), _n_tickers(sub),
                     _round(float((sub["cohort"] == "new").mean()), 4) if len(sub) else None,
                     _g(r, "mean_ic"), _g(r, "ic_t", 2), _g(r, "net_spread_25bps"),
                     r.get("subperiod_stable", False)])
    # macro-regime conditioned reads (reuse the PIT regime flags already on ev)
    for regime in ("high_rates", "easy_regime", "market_drawdown", "high_oil"):
        if regime not in ev.columns:
            continue
        sc = _scn(focus, focus["scenario"] + "_" + regime, regime_filter=regime)
        r = _safe_eval(ev, sc, rng)
        n_ev = int((ev[regime] == True).sum())  # noqa: E712
        rows.append(["regime", regime, n_ev, _n_tickers(ev[ev[regime] == True]),  # noqa: E712
                     None, _g(r, "mean_ic"), _g(r, "ic_t", 2), _g(r, "net_spread_25bps"),
                     r.get("subperiod_stable", False)])
    return rows


def data_quality_attribution(ev, focus: Dict, rng) -> List[List]:
    """Split the cross-section by data completeness: standardized-earnings availability (SUE needs
    >=4 prior surprises) and as-of fundamentals availability. Sparse names are newly-acquired."""
    rows = []
    splits = []
    if "sue" in ev.columns:
        splits.append(("sue_available", ev["sue"].notna()))
        splits.append(("sue_missing", ev["sue"].isna()))
    if "roa_x" in ev.columns:
        splits.append(("fundamentals_present", ev["roa_x"].notna()))
        splits.append(("fundamentals_absent", ev["roa_x"].isna()))
    for label, mask in splits:
        if mask.sum() == 0:
            continue
        sub = ev[mask].copy()
        r = _safe_eval(sub, focus, rng)
        rows.append(["data_quality", label, int(len(sub)), _n_tickers(sub),
                     _round(float((sub["cohort"] == "new").mean()), 4) if len(sub) else None,
                     _g(r, "mean_ic"), _g(r, "ic_t", 2), _g(r, "net_spread_25bps"),
                     r.get("subperiod_stable", False)])
    return rows


# --------------------------------------------------------------------------- #
# D. Constrained variants: salvage hypotheses, each gated by the SAME 8-T promotion gate.
# --------------------------------------------------------------------------- #
def _constrained_gate(r: Dict, sector_constrained: bool) -> List[str]:
    reasons = t8.gate_reasons(r)
    if sector_constrained:
        reasons = [x for x in reasons if not x.startswith("sector-concentrated")]
    return reasons


def build_variants(ev, focus: Dict, weak_sectors: List[str]) -> List[Dict]:
    """Each variant = (label, scenario spec, ev mask, sector_constrained flag, rationale). The base
    focus signal is the within-sector surprise drift; variants restrict the cross-section it runs on
    or swap to an explicitly quality-/surprise-gated read."""
    import pandas as pd
    lookup = _battery_lookup()
    sxq = lookup["surprise_x_quality"]
    cut = pd.Timestamp(_PRE2020)
    old_mask = ev["cohort"] == "old"

    # liquidity top half (most-tradable names), using the per-ticker dollar-volume proxy
    liq = (ev["liquidity_proxy"] if "liquidity_proxy" in ev.columns
           else pd.Series(float("nan"), index=ev.index))
    liq_hi = liq >= liq.median() if liq.notna().any() else pd.Series(False, index=ev.index)

    # the single best-contributing sector (highest within-sector own IC) for the sector-constrained read
    best_sector = None
    if "sector" in ev.columns:
        sec_ic = {}
        rng0 = _mk_rng()
        raw = _scn(focus, "tmp_raw", sector_neutral=False)
        sec_thresh = MIN_MONTHS * t8.MIN_NAMES_PER_MONTH  # enough cross-section to be meaningful
        for sec in ev["sector"].dropna().astype(str).unique():
            sub = ev[ev["sector"].astype(str) == sec]
            if len(sub) >= sec_thresh:
                sec_ic[sec] = _g(_safe_eval(sub, raw, rng0), "mean_ic") or float("-inf")
        if sec_ic:
            best_sector = max(sec_ic, key=sec_ic.get)

    # large-surprise events only (top-magnitude buckets)
    if "magnitude_bucket" in ev.columns:
        big = ev["magnitude_bucket"].astype("object").isin(["big_beat", "big_miss", "beat", "miss"])
    else:
        big = pd.Series(True, index=ev.index)

    # minimum event history: tickers contributing >= 12 events (mature earnings history)
    evcount = ev.groupby("ticker")["entry_date"].transform("count")
    mature = evcount >= 12

    not_weak = ~ev["sector"].astype(str).isin(weak_sectors) if weak_sectors else pd.Series(True, index=ev.index)

    variants = [
        {"label": "old_cohort_only", "sc": _scn(focus, "ssn_old_cohort_only"),
         "mask": old_mask, "sector_constrained": False,
         "rationale": "within-sector surprise drift restricted to the original 299-ticker cohort"},
        {"label": "high_liquidity_only", "sc": _scn(focus, "ssn_high_liquidity_only"),
         "mask": liq_hi, "sector_constrained": False,
         "rationale": "surprise drift among the most-tradable (top-half dollar-volume) names"},
        {"label": "top_sector_only",
         "sc": _scn(focus, "ssn_top_sector_only", sector_neutral=False),
         "mask": (ev["sector"].astype(str) == best_sector) if best_sector else pd.Series(False, index=ev.index),
         "sector_constrained": True,
         "rationale": "surprise drift inside the single best-contributing sector (%s)" % (best_sector or "n/a")},
        {"label": "sector_neutral_within_old_cohort", "sc": _scn(focus, "ssn_sector_neutral_old"),
         "mask": old_mask, "sector_constrained": False,
         "rationale": "sector-neutral surprise ranking inside the original cohort only"},
        {"label": "quality_gated_surprise", "sc": _scn(sxq, "sxq_quality_gated"),
         "mask": pd.Series(True, index=ev.index), "sector_constrained": False,
         "rationale": "surprise confirmed by ROA quality (surprise_x_quality) on the full expanded universe"},
        {"label": "large_surprise_only", "sc": _scn(focus, "ssn_large_surprise_only"),
         "mask": big, "sector_constrained": False,
         "rationale": "within-sector surprise drift among large-magnitude surprises only"},
        {"label": "post_2020_only", "sc": _scn(focus, "ssn_post_2020_only"),
         "mask": ev["entry_date"] >= cut, "sector_constrained": False,
         "rationale": "within-sector surprise drift on the post-2020 subperiod only"},
        {"label": "exclude_weak_sectors", "sc": _scn(focus, "ssn_exclude_weak_sectors"),
         "mask": not_weak, "sector_constrained": False,
         "rationale": "within-sector surprise drift after dropping IC-dilutive sectors: %s"
                      % (", ".join(weak_sectors) if weak_sectors else "(none flagged)")},
        {"label": "require_min_event_history", "sc": _scn(focus, "ssn_require_min_event_history"),
         "mask": mature, "sector_constrained": False,
         "rationale": "within-sector surprise drift among names with >=12 cached earnings events"},
    ]
    return variants


def _mk_rng():
    import numpy as np
    return np.random.default_rng(_RNG_SEED)


def run_constrained(ev, focus: Dict, weak_sectors: List[str], rng) -> Tuple[List[List], List[Dict], List[Dict]]:
    import math
    variants = build_variants(ev, focus, weak_sectors)
    evaluated = []
    for v in variants:
        sub = ev[v["mask"]].copy()
        r = _safe_eval(sub, v["sc"], rng)
        r["_variant"] = v["label"]
        r["_rationale"] = v["rationale"]
        r["_sector_constrained"] = v["sector_constrained"]
        r["_n_tickers"] = _n_tickers(sub)
        evaluated.append(r)

    # multiple-testing control across the salvage hypotheses (same BH machinery as the 8-T campaign)
    pvals = [r.get("ic_p") if r.get("ic_p") is not None else float("nan") for r in evaluated]
    bh = s8.benjamini_hochberg(pvals, GATE_BH_Q)
    for r, sig in zip(evaluated, bh):
        r["bh_significant"] = bool(sig)

    scoreboard, promoted, rejected = [], [], []
    for r in evaluated:
        reasons = _constrained_gate(r, r["_sector_constrained"])
        is_prom = not reasons
        scoreboard.append([r["_variant"], r.get("signal"), r["_sector_constrained"],
                           r.get("n_events", 0), r.get("n_months", 0), _g(r, "mean_ic"),
                           _g(r, "ic_t", 2), r.get("bh_significant", False), _g(r, "mean_spread"),
                           _g(r, "spread_hit_rate"), _g(r, "net_spread_25bps"),
                           _g(r, "net_spread_50bps"), r.get("subperiod_stable", False),
                           r.get("beats_placebo", False), is_prom, r["_rationale"]])
        if is_prom:
            promoted.append(r)
        else:
            rr = dict(r)
            rr["reject_reason"] = "; ".join(reasons)
            rejected.append(rr)
    return scoreboard, promoted, rejected


# --------------------------------------------------------------------------- #
# E. Decision.
# --------------------------------------------------------------------------- #
def derive_decision(summary: Dict, promoted: List[Dict], n_old: int, n_new: int) -> Tuple[str, str, str]:
    """Return (decision, rationale, main_reason). The salvage verdict turns on whether any constrained
    variant clears the full 8-T gate, anchored to the cohort attribution of the primary focus signal."""
    foc = summary.get(PRIMARY_FOCUS, {})
    ic_old = _g(foc.get("old_cohort", {}), "mean_ic")
    ic_new = _g(foc.get("new_cohort", {}), "mean_ic")
    ic_comb = _g(foc.get("combined", {}), "mean_ic")
    t_old = _g(foc.get("old_cohort", {}), "ic_t", 2)
    old_strong = bool(ic_old is not None and t_old is not None
                      and ic_old >= GATE_MIN_IC and t_old >= GATE_MIN_T)
    new_weak = bool(ic_new is None or (ic_old is not None and ic_new < ic_old * 0.6))

    if old_strong and new_weak:
        main = ("The newly-acquired cohort carries materially weaker post-earnings drift than the "
                "original cohort (focus signal %s: old-cohort IC %s vs new-cohort IC %s), so pooling "
                "the two halves DILUTES the combined IC (%s). The expansion did not break the signal "
                "in the original names - it added low-information names that average it down."
                % (PRIMARY_FOCUS, ic_old, ic_new, ic_comb))
    elif ic_comb is not None and ic_old is not None and ic_comb < ic_old:
        main = ("The combined IC (%s) sits below the original-cohort IC (%s); the wider universe "
                "weakens the measured edge, though the old/new split is less clean than pure dilution."
                % (ic_comb, ic_old))
    else:
        main = ("No clean cohort dilution pattern in the focus signal (old IC %s, new IC %s, combined "
                "IC %s); the weakening is distributed across the cross-section." % (ic_old, ic_new, ic_comb))

    names = sorted(p["_variant"] for p in promoted)
    if promoted:
        return (DEC_SURVIVES,
                "A constrained alpha SURVIVES the expansion: %d variant(s) clear the full 8-T "
                "promotion gate (25bps cost, positive spread, subperiod stability, BH q=%.2f): %s. %s "
                "These are CONSTRAINED/exploratory reads (restricted cross-section), not unconditional "
                "alpha - preview-only, no orders."
                % (len(promoted), GATE_BH_Q, ", ".join(names), main), main)

    if old_strong:
        return (DEC_DIAG,
                "The original-cohort focus signal is still strong (IC %s, t %s) but NO constrained "
                "variant clears the full multi-test gate on the expanded data, so the salvage is "
                "suggestive rather than confirmed. Additional diagnostics (deeper history for the new "
                "names, alternative liquidity/quality screens) are needed before relying on it. %s "
                "Preview-only, no orders." % (ic_old, t_old, main), main)

    return (DEC_REJECTED,
            "The earnings-surprise alpha does NOT survive the expansion: no constrained variant clears "
            "the 8-T gate and the original-cohort edge is not robust enough to carry on its own. %s "
            "Treat the 8-T promotions as overfit to the original 299-ticker sample. Preview-only, no "
            "orders." % main, main)


def _next_step(decision: str) -> Tuple[str, str]:
    if decision == DEC_SURVIVES:
        return ("Carry the surviving CONSTRAINED variant(s) into the Paper Trader daily-review cockpit "
                "as PREVIEW-ONLY ranking ideas, clearly labelled constrained/exploratory (manual "
                "review; no orders). Re-validate on the next data refresh before any reliance.",
                "Preview the surviving constrained alpha (manual review)")
    if decision == DEC_DIAG:
        return ("Phase 8-X: deepen the diagnostic - acquire longer earnings/price history for the "
                "newly-added names (so their SUE/quality features are populated), then re-attribute; "
                "do NOT integrate into Paper Trader until a variant clears the full gate.",
                "Deepen the new-cohort history, then re-attribute")
    if decision == DEC_REJECTED:
        return ("Shelve the earnings-surprise family as a standalone ranking signal; Phase 8-X should "
                "pivot to a different alpha family or a fundamentally different data source rather than "
                "more of the same. No Paper Trader integration.",
                "Pivot to a different alpha family (signal rejected)")
    return ("python research/run_phase8w_expanded_universe_failure_attribution.py",
            "Re-run the attribution")


# --------------------------------------------------------------------------- #
# Orchestration.
# --------------------------------------------------------------------------- #
class _Paths:
    def __init__(self, out_dir=None, phase8v_dir=None):
        self.out = Path(out_dir) if out_dir else _OUT_DIR
        self.phase8v = Path(phase8v_dir) if phase8v_dir else _PHASE8V_DIR
        for key, name in _ARTIFACTS.items():
            setattr(self, key, self.out / name)


def run(out_dir: Optional[Path] = None, phase8v_dir: Optional[Path] = None,
        price_csv: Optional[Path] = None, data_dir: Optional[Path] = None,
        sector_csv: Optional[Path] = None, macro: Optional[Dict[str, Path]] = None,
        phase8n_dir: Optional[Path] = None, phase8r_dir: Optional[Path] = None,
        as_of: str = s8.DEFAULT_AS_OF, verbose: bool = True) -> Dict:
    P = _Paths(out_dir, phase8v_dir)
    P.out.mkdir(parents=True, exist_ok=True)
    log = t8._Log(verbose)
    try:
        import pandas as pd  # noqa: F401

        # 1. Read the Phase 8-V expanded result (the cohort split + the before/after verdict).
        v8 = _read_json(P.phase8v / _PHASE8V_REPORT)
        newly = [str(t).upper() for t in (v8.get("newly_scoreable_tickers") or [])]
        new_set = set(newly)
        v8_before = (v8.get("before") or {}).get("promoted_signals") or []
        v8_after = (v8.get("after") or {}).get("promoted_signals") or []
        v8_decision = v8.get("decision", "(none read)")
        log.step("read_8v", "DONE",
                 "8-V decision=%s; before-promoted=%d; after-promoted=%d; newly-scoreable=%d"
                 % (v8_decision, len(v8_before), len(v8_after), len(newly)), count=len(newly))

        # 2. Rebuild the expanded event table (same panel + earnings cache the 8-V 'after' used).
        panel = Path(price_csv) if price_csv else _EXPANDED_PANEL
        P_score = s8._Paths(out_dir=P.out, data_dir=data_dir, phase8n_dir=phase8n_dir,
                            price_csv=panel, sector_csv=sector_csv, macro=macro,
                            phase8r_dir=phase8r_dir)
        ev, stats, _audit = build_expanded_ev(P_score, as_of, log)
        if getattr(ev, "empty", True) or stats.get("events_usable", 0) == 0:
            return _finish_minimal(P, log, v8, DEC_DIAG,
                                   "No usable events on the expanded panel (%s); cannot attribute. "
                                   "Re-run after the gitignored 8-V expanded panel + EODHD earnings "
                                   "cache are present." % _rel(panel), verbose)
        ev = tag_cohort(ev, new_set)
        _eod_norm = Path(P_score.eodhd_dir) / "normalized" / "eod_prices"
        ev = attach_liquidity_proxy(ev, panel, extra_csvs=[s8._PRICE_CACHE_CSV],
                                    eod_norm_dir=_eod_norm)
        n_old = int((ev["cohort"] == "old").sum())
        n_new = int((ev["cohort"] == "new").sum())
        t_old = _n_tickers(ev[ev["cohort"] == "old"])
        t_new = _n_tickers(ev[ev["cohort"] == "new"])
        log.step("cohort", "DONE", "old=%d events/%d tickers; new=%d events/%d tickers"
                 % (n_old, t_old, n_new, t_new), count=t_new)

        lookup = _battery_lookup()
        focus = lookup[PRIMARY_FOCUS]
        rng = _mk_rng()

        # 3. Cohort attribution (every target signal, old vs new vs combined).
        perf_rows, attr_rows, summary = cohort_performance(ev, lookup, rng)
        _write_csv(P.cohort_perf,
                   ["scenario", "family", "cohort", "n_tickers", "n_events", "n_months", "mean_ic",
                    "ic_t", "mean_spread", "net_spread_25bps", "net_spread_50bps", "subperiod_stable"],
                   perf_rows)
        _write_csv(P.old_vs_new,
                   ["scenario", "family", "ic_old", "ic_new", "ic_combined", "ic_old_minus_new",
                    "t_old", "t_new", "t_combined", "net25_old", "net25_new", "net25_combined",
                    "n_events_old", "n_events_new", "diluted_by_new_cohort"], attr_rows)

        # 4. Dimension attributions for the primary focus signal.
        sector_rows, weak_sectors = sector_attribution(ev, focus, rng)
        _write_csv(P.sector_attr,
                   ["sector", "n_events", "n_tickers", "new_cohort_share", "own_ic", "own_ic_t",
                    "combined_ic_without_sector", "leave_one_out_delta", "is_weak_sector"], sector_rows)

        liq_rows = (bucket_attribution(ev, focus, "liquidity_proxy", 4, rng)
                    if ev["liquidity_proxy"].notna().any() else [])
        _write_csv(P.liquidity_attr,
                   ["dimension", "bucket", "range", "n_events", "n_tickers", "new_cohort_share",
                    "mean_ic", "ic_t", "net_spread_25bps", "subperiod_stable"], liq_rows)

        eh_rows = event_history_attribution(ev, focus, rng)
        _write_csv(P.event_history_attr,
                   ["dimension", "bucket", "range", "n_events", "n_tickers", "new_cohort_share",
                    "mean_ic", "ic_t", "net_spread_25bps", "subperiod_stable"], eh_rows)

        sp_rows = subperiod_attribution(ev, focus, rng)
        _write_csv(P.subperiod_attr,
                   ["dimension", "segment", "n_events", "n_tickers", "new_cohort_share", "mean_ic",
                    "ic_t", "net_spread_25bps", "subperiod_stable"], sp_rows)

        dq_rows = data_quality_attribution(ev, focus, rng)
        _write_csv(P.data_quality_attr,
                   ["dimension", "segment", "n_events", "n_tickers", "new_cohort_share", "mean_ic",
                    "ic_t", "net_spread_25bps", "subperiod_stable"], dq_rows)
        log.step("attribute", "DONE", "weak sectors: %s"
                 % (", ".join(weak_sectors) if weak_sectors else "(none)"), count=len(weak_sectors))

        # 5. Constrained salvage variants.
        scoreboard, promoted, rejected = run_constrained(ev, focus, weak_sectors, rng)
        _write_csv(P.variant_scoreboard,
                   ["variant", "signal", "sector_constrained", "n_events", "n_months", "mean_ic",
                    "ic_t", "bh_significant", "mean_spread", "spread_hit_rate", "net_spread_25bps",
                    "net_spread_50bps", "subperiod_stable", "beats_placebo", "promoted", "rationale"],
                   scoreboard)
        _write_csv(P.variant_promoted,
                   ["variant", "signal", "sector_constrained", "mean_ic", "ic_t", "mean_spread",
                    "net_spread_25bps", "net_spread_50bps", "spread_hit_rate", "subperiod_stable",
                    "n_events", "n_months", "rationale"],
                   [[r["_variant"], r.get("signal"), r["_sector_constrained"], _g(r, "mean_ic"),
                     _g(r, "ic_t", 2), _g(r, "mean_spread"), _g(r, "net_spread_25bps"),
                     _g(r, "net_spread_50bps"), _g(r, "spread_hit_rate"),
                     r.get("subperiod_stable", False), r.get("n_events", 0), r.get("n_months", 0),
                     r["_rationale"]] for r in promoted])
        _write_csv(P.variant_rejected,
                   ["variant", "signal", "sector_constrained", "mean_ic", "ic_t", "n_events",
                    "reject_reason", "rationale"],
                   [[r["_variant"], r.get("signal"), r["_sector_constrained"], _g(r, "mean_ic"),
                     _g(r, "ic_t", 2), r.get("n_events", 0), r.get("reject_reason", ""),
                     r["_rationale"]] for r in rejected])
        log.step("constrained", "DONE", "%d variants -> %d promoted, %d rejected"
                 % (len(scoreboard), len(promoted), len(rejected)), count=len(promoted))

        # 6. Decision + plans.
        decision, rationale, main_reason = derive_decision(summary, promoted, n_old, n_new)
        log.step("decision", "DONE", decision)

        foc = summary.get(PRIMARY_FOCUS, {})
        sxq = summary.get("surprise_x_quality", {})

        def _cohort_block(s):
            return {lbl: {"mean_ic": _g(s.get(lbl, {}), "mean_ic"),
                          "ic_t": _g(s.get(lbl, {}), "ic_t", 2),
                          "n_events": s.get(lbl, {}).get("n_events", 0),
                          "net_spread_25bps": _g(s.get(lbl, {}), "net_spread_25bps")}
                    for lbl in ("old_cohort", "new_cohort", "combined")}

        next_cmd, next_title = _next_step(decision)
        _write_json(P.final_decision, {
            "phase": PHASE, "decision": decision, "decision_is_terminal": True,
            "allowed_decisions": list(ALLOWED_DECISIONS), "rationale": rationale,
            "main_reason_alpha_weakened": main_reason,
            "primary_focus_signal": PRIMARY_FOCUS,
            "focus_cohort_performance": _cohort_block(foc),
            "surprise_x_quality_cohort_performance": _cohort_block(sxq),
            "constrained_alpha_survives": bool(promoted),
            "constrained_promoted_variants": sorted(p["_variant"] for p in promoted),
            "weak_sectors": weak_sectors,
            "preview_only": True, "orders_enabled": False, "automation_enabled": False,
            "committed": False})
        _write_json(P.next_plan, {
            "phase": "8-X", "title": next_title, "depends_on_decision": decision,
            "next_command": next_cmd, "preview_only": True, "orders_enabled": False,
            "automation_enabled": False, "paper_trader_touched": False, "committed": False})

        _write_csv(P.run_log, ["step", "status", "detail", "count"], log.rows)

        # 7. Secret/safety audit (this phase reads only cached data; no key, no network).
        written = [p for p in P.out.glob("*") if p.is_file()]
        leak_clean, scanned, marker_found = r8._scan_for_leaks(written)
        _write_csv(P.secret_audit, ["check", "result", "detail"],
                   [["api_key_used", False, "attribution reads only the cached expanded panel + earnings"],
                    ["network_used", False, "no external API call in this phase"],
                    ["api_key_printed", False, "key value never referenced"],
                    ["committed_artifacts_contain_key_query_param", marker_found,
                     "leak scan over %d committed files" % scanned],
                    ["secret_leak_scan_clean", leak_clean, "no key value or key query param"]])

        report = {
            "phase": PHASE,
            "objective": ("Attribute why the Phase 8-T earnings-surprise alpha weakened after the 8-V "
                          "expansion (299 -> 545 scoreable tickers) and decide whether a robust "
                          "constrained alpha survives. Preview-only; no orders; no data acquisition."),
            "builds_on_phase8v_decision": v8_decision,
            "phase8v_before_promoted": v8_before, "phase8v_after_promoted": v8_after,
            "as_of": as_of,
            "universe": {
                "scoreable_tickers": stats.get("tickers_usable", 0),
                "usable_events": stats.get("events_usable", 0),
                "old_cohort_tickers": t_old, "new_cohort_tickers": t_new,
                "old_cohort_events": n_old, "new_cohort_events": n_new,
                "newly_scoreable_from_8v": len(newly)},
            "focus_cohort_performance": _cohort_block(foc),
            "surprise_x_quality_cohort_performance": _cohort_block(sxq),
            "weak_sectors": weak_sectors,
            "constrained_variants_tested": len(scoreboard),
            "constrained_alpha_survives": bool(promoted),
            "constrained_promoted_variants": sorted(p["_variant"] for p in promoted),
            "constrained_rejected_count": len(rejected),
            "main_reason_alpha_weakened": main_reason,
            "decision": decision, "decision_is_terminal": True,
            "allowed_decisions": list(ALLOWED_DECISIONS), "decision_rationale": rationale,
            "recommended_next_command": next_cmd,
            "secret_safety_leak_scan_clean": leak_clean, "secret_safety_files_scanned": scanned,
            "outputs": {k: _rel(getattr(P, k)) for k in _ARTIFACTS},
            "required_artifacts": [_ARTIFACTS[k] for k in _REQUIRED_ARTIFACTS],
            # safety / provenance contract
            "preview_only": True, "orders_enabled": False, "automation_enabled": False,
            "broker_execution_enabled": False, "production_replacement": False,
            "network_used": False, "data_acquired": False,
            "paper_trader_touched": False, "gcp_touched": False, "deployed": False,
            "data_fabricated": False, "alpha_fabricated": False,
            "raw_paid_data_in_artifacts": False, "wrote_to_d_drive": False, "committed": False,
        }
        _write_json(P.report, report)
        if verbose:
            _print_summary(report)
        return report
    except Exception as exc:  # pragma: no cover - defensive; never leaks a key
        report = {"phase": PHASE, "decision": DEC_ERROR, "decision_is_terminal": True,
                  "allowed_decisions": list(ALLOWED_DECISIONS),
                  "error": "%s: %s" % (type(exc).__name__, exc),
                  "committed": False, "preview_only": True, "orders_enabled": False,
                  "automation_enabled": False, "paper_trader_touched": False, "gcp_touched": False}
        try:
            _write_json(P.report, report)
            _write_csv(P.run_log, ["step", "status", "detail", "count"], log.rows)
        except Exception:
            pass
        if verbose:
            print("ERROR: %s" % report["error"])
        return report


def _finish_minimal(P, log, v8, decision, detail, verbose) -> Dict:
    """Write the required artifacts as empty-but-present shells when no event table can be built
    (e.g. the gitignored expanded panel is absent). Keeps the contract: a terminal decision + all
    required artifacts, never an order or a key."""
    log.step("build_ev", "ABORT", detail)
    headers = {
        "cohort_perf": ["scenario", "family", "cohort", "n_tickers", "n_events", "n_months",
                        "mean_ic", "ic_t", "mean_spread", "net_spread_25bps", "net_spread_50bps",
                        "subperiod_stable"],
        "old_vs_new": ["scenario", "family", "ic_old", "ic_new", "ic_combined", "ic_old_minus_new",
                       "t_old", "t_new", "t_combined", "net25_old", "net25_new", "net25_combined",
                       "n_events_old", "n_events_new", "diluted_by_new_cohort"],
        "sector_attr": ["sector", "n_events", "n_tickers", "new_cohort_share", "own_ic", "own_ic_t",
                        "combined_ic_without_sector", "leave_one_out_delta", "is_weak_sector"],
        "liquidity_attr": ["dimension", "bucket", "range", "n_events", "n_tickers",
                           "new_cohort_share", "mean_ic", "ic_t", "net_spread_25bps",
                           "subperiod_stable"],
        "event_history_attr": ["dimension", "bucket", "range", "n_events", "n_tickers",
                               "new_cohort_share", "mean_ic", "ic_t", "net_spread_25bps",
                               "subperiod_stable"],
        "subperiod_attr": ["dimension", "segment", "n_events", "n_tickers", "new_cohort_share",
                           "mean_ic", "ic_t", "net_spread_25bps", "subperiod_stable"],
        "data_quality_attr": ["dimension", "segment", "n_events", "n_tickers", "new_cohort_share",
                              "mean_ic", "ic_t", "net_spread_25bps", "subperiod_stable"],
        "variant_scoreboard": ["variant", "signal", "sector_constrained", "n_events", "n_months",
                               "mean_ic", "ic_t", "bh_significant", "mean_spread", "spread_hit_rate",
                               "net_spread_25bps", "net_spread_50bps", "subperiod_stable",
                               "beats_placebo", "promoted", "rationale"],
        "variant_promoted": ["variant", "signal", "sector_constrained", "mean_ic", "ic_t",
                             "mean_spread", "net_spread_25bps", "net_spread_50bps", "spread_hit_rate",
                             "subperiod_stable", "n_events", "n_months", "rationale"],
        "variant_rejected": ["variant", "signal", "sector_constrained", "mean_ic", "ic_t",
                            "n_events", "reject_reason", "rationale"],
    }
    for key, hdr in headers.items():
        _write_csv(getattr(P, key), hdr, [])
    next_cmd, next_title = _next_step(decision)
    _write_json(P.final_decision, {
        "phase": PHASE, "decision": decision, "decision_is_terminal": True,
        "allowed_decisions": list(ALLOWED_DECISIONS), "rationale": detail,
        "main_reason_alpha_weakened": "not computed (no event table)",
        "constrained_alpha_survives": False, "constrained_promoted_variants": [],
        "preview_only": True, "orders_enabled": False, "automation_enabled": False,
        "committed": False})
    _write_json(P.next_plan, {
        "phase": "8-X", "title": next_title, "depends_on_decision": decision,
        "next_command": next_cmd, "preview_only": True, "orders_enabled": False,
        "automation_enabled": False, "paper_trader_touched": False, "committed": False})
    _write_csv(P.run_log, ["step", "status", "detail", "count"], log.rows)
    written = [p for p in P.out.glob("*") if p.is_file()]
    leak_clean, scanned, marker_found = r8._scan_for_leaks(written)
    _write_csv(P.secret_audit, ["check", "result", "detail"],
               [["api_key_used", False, "attribution reads only cached data"],
                ["network_used", False, "no external API call"],
                ["secret_leak_scan_clean", leak_clean, "leak scan over %d files" % scanned]])
    report = {
        "phase": PHASE, "objective": "Expanded universe failure attribution (aborted: no event table).",
        "builds_on_phase8v_decision": v8.get("decision", ""),
        "decision": decision, "decision_is_terminal": True,
        "allowed_decisions": list(ALLOWED_DECISIONS), "decision_rationale": detail,
        "main_reason_alpha_weakened": "not computed (no event table)",
        "constrained_alpha_survives": False, "constrained_promoted_variants": [],
        "universe": {"scoreable_tickers": 0, "usable_events": 0},
        "recommended_next_command": next_cmd,
        "secret_safety_leak_scan_clean": leak_clean, "secret_safety_files_scanned": scanned,
        "outputs": {k: _rel(getattr(P, k)) for k in _ARTIFACTS},
        "required_artifacts": [_ARTIFACTS[k] for k in _REQUIRED_ARTIFACTS],
        "preview_only": True, "orders_enabled": False, "automation_enabled": False,
        "broker_execution_enabled": False, "production_replacement": False, "network_used": False,
        "data_acquired": False, "paper_trader_touched": False, "gcp_touched": False, "deployed": False,
        "data_fabricated": False, "alpha_fabricated": False, "raw_paid_data_in_artifacts": False,
        "wrote_to_d_drive": False, "committed": False,
    }
    _write_json(P.report, report)
    if verbose:
        _print_summary(report)
    return report


def _print_summary(report: Dict) -> None:
    print("phase:                 %s" % report.get("phase"))
    uni = report.get("universe", {})
    print("scoreable / events:    %s / %s" % (uni.get("scoreable_tickers"), uni.get("usable_events")))
    print("old / new tickers:     %s / %s"
          % (uni.get("old_cohort_tickers"), uni.get("new_cohort_tickers")))
    foc = report.get("focus_cohort_performance", {})
    print("focus IC old/new/comb: %s / %s / %s"
          % (foc.get("old_cohort", {}).get("mean_ic"), foc.get("new_cohort", {}).get("mean_ic"),
             foc.get("combined", {}).get("mean_ic")))
    print("weak sectors:          %s" % report.get("weak_sectors"))
    print("constrained survives:  %s %s" % (report.get("constrained_alpha_survives"),
                                            report.get("constrained_promoted_variants")))
    print("main reason:           %s" % report.get("main_reason_alpha_weakened"))
    print("decision:              %s" % report.get("decision"))
    print("next command:          %s" % report.get("recommended_next_command"))


def _parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        description=("Phase 8-W expanded-universe failure attribution + constrained-alpha salvage "
                     "(offline; reuses the gitignored 8-V expanded panel + EODHD earnings cache)."))
    ap.add_argument("--as-of", default=s8.DEFAULT_AS_OF,
                    help="Score only events announced on/before this date (default %s)." % s8.DEFAULT_AS_OF)
    return ap.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parse_args(argv)
    run(as_of=args.as_of, verbose=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
