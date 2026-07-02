"""Phase 10-O - Regime And Conditional Alpha Gating.

WHY THIS PHASE EXISTS
    Phases 10-M (incremental factors) and 10-N (transforms / interactions) both failed to beat the frozen
    10-D composite_sn baseline out-of-sample. The last owned-data avenue before declaring exhaustion: is
    the modest baseline edge CONDITIONAL - meaningfully stronger inside a simple, pre-declared, ex-ante
    market/macro REGIME (and identifiable before the trade)? If so, a paper book that only runs in the
    favourable regime could carry a stronger, tradeable edge.

    Phase 10-O tests exactly that, narrowly and skeptically. It uses ONLY owned/local regime data already
    on the panel (FRED macro flags + benchmark trend, and month-level vol / dispersion / liquidity
    reconstructed from owned prices). It makes NO live macro API call, adds NO new data, uses ONLY simple
    median / majority thresholds (no tuned regime boundaries), requires adequate sample per regime, and -
    critically - requires any conditional edge to hold in BOTH pre- and post-2020 subperiods (the same
    subperiod-robustness guard that caught the 10-N altcomp_rank one-era artifact). Selecting the best of
    several regimes is a textbook overfit trap; the subperiod guard + sample-adequacy + a meaningful
    margin are the defenses.

REGIMES (pre-declared; all converted to pure MONTH-LEVEL time regimes; ex-ante / PIT)
    owned macro/market flags   : easy_regime, high_rates, market_drawdown, high_oil, strong_dollar
    owned macro levels (median): rates_10y, rates_2s10s, oil_z
    reconstructed (month agg)  : vol (mean vol_63), dispersion (std mom_pre_63), liquidity (median $vol)

METHOD
    For each regime, split months into two states, evaluate composite_sn's quarterly net-25bps + 63d IC t
    + sample in each, take the FAVOURABLE state (higher net25), and judge the conditional (favourable-only)
    strategy vs the always-on baseline. A regime is a champion ONLY if the favourable-state net25 is
    MEANINGFULLY higher than baseline (>= 1.25x AND strictly up), the favourable state has adequate sample
    (>= 10 quarters, >= 6000 events), the favourable-state 63d IC t is not materially worse, AND the
    favourable-state net25 is positive in BOTH pre- and post-2020 subperiods (each with enough quarters).

TERMINAL DECISIONS
    CONDITIONAL_ALPHA_READY_FOR_PAPER_RULES | BASELINE_REMAINS_CHAMPION | REJECT_REGIME_OVERFIT |
    NEEDS_REGIME_INPUT_REPAIR | NEEDS_MORE_OWNED_DATA

CONSTRAINTS HONORED
    Fully offline (no network / key / provider probe / live macro API); owned/local data only (Norgate
    panel + owned FRED/benchmark regime columns already on it); simple median/majority thresholds only; no
    tuned regime boundaries; no new data; no Paper Trader writes; no orders; no automation; no broker; no
    deploy; no GCP; no package install; targeted tests only; output is research metadata only. No commit.
    No push.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from research import run_phase10m_owned_fundamental_incremental_alpha_expansion as m10   # noqa: E402
from research import run_phase10d_quarterly_quality_composite_validation as d10           # noqa: E402
from research import run_phase10c_eodhd_quality_oos_validation as c10                     # noqa: E402
from research import run_phase8x_autonomous_strong_alpha_discovery as x8                  # noqa: E402

s8 = x8.s8
t8 = x8.t8
_write_json = s8._write_json
_write_csv = s8._write_csv
_round = s8._round
_finite = c10._finite
_num = c10._num

PHASE = "10-O"
PHASE_NAME = "Regime And Conditional Alpha Gating"
STEM = "phase10o_regime_conditional_alpha_gating"
PERFORMS_NETWORK = False

AS_OF = c10.AS_OF
PRIMARY_HORIZON_D = d10.PRIMARY_HORIZON_D
RET_PRIMARY = d10.RET_PRIMARY
_COMP_SN = d10.COMP_SN
_PRE2020 = m10._PRE2020
EODHD_KEY_ENV = "EODHD_API_KEY"

REPRO_TOL = m10.REPRO_TOL

# Strict conditional-alpha bars (a-priori).
MIN_REGIME_QUARTERS = 10
MIN_REGIME_EVENTS = 6000
MIN_SUBPERIOD_QUARTERS = 3
REGIME_MEANINGFUL_MULT = 1.25          # favourable net25 must be >= 1.25x baseline to be "meaningful"
IC_T_MARGIN = 0.10
# The favourable-regime edge must GENERALISE across eras: its net25 must not be materially worse than the
# baseline's SAME-subperiod net25 in EITHER the pre- or post-2020 era. This rejects one-era regimes whose
# full-sample lift is entirely post-2020 (the market_liquidity / curve trap) - "both subperiods positive"
# alone is too weak because the baseline is already positive in both.
EPS_SUB = 0.0005

DEC_READY = "CONDITIONAL_ALPHA_READY_FOR_PAPER_RULES"
DEC_BASELINE = "BASELINE_REMAINS_CHAMPION"
DEC_OVERFIT = "REJECT_REGIME_OVERFIT"
DEC_REPAIR = "NEEDS_REGIME_INPUT_REPAIR"
DEC_MORE_DATA = "NEEDS_MORE_OWNED_DATA"
ALLOWED_DECISIONS = (DEC_READY, DEC_BASELINE, DEC_OVERFIT, DEC_REPAIR, DEC_MORE_DATA)

CLS_PASS = "CONDITIONAL_PASS"
CLS_OVERFIT = "REJECT_REGIME_OVERFIT"
CLS_TINY = "REJECT_TINY_SAMPLE"
CLS_NOIMPROVE = "NO_IMPROVEMENT"

_ARTIFACTS = {
    "report": "%s.json" % STEM,
    "inventory": "regime_inventory.csv",
    "scorecard": "regime_conditional_scorecard.csv",
    "state_detail": "regime_state_detail.csv",
    "subperiod": "regime_subperiod_report.csv",
    "rejected": "rejected_regimes.csv",
    "next_plan": "phase10p_or_10q_next_plan.json",
}


class _Paths:
    def __init__(self, out_dir=None):
        self.out = Path(out_dir) if out_dir else (_REPO_ROOT / "research" / "output" / STEM)

    def art(self, key: str) -> Path:
        return self.out / _ARTIFACTS[key]


# --------------------------------------------------------------------------- #
# A. Regime builders (all -> a month-level 0/1 state; ex-ante).
# --------------------------------------------------------------------------- #
def _flag_quarter_state(ev, col):
    """Binary macro flag -> QUARTER state = 1 if the flag is mostly on that quarter. Quarter-level (not
    month-level) so a conditional strategy trades WHOLE favourable quarters - never a partial
    within-quarter cross-section (which would not be an implementable timing overlay)."""
    g = ev.groupby("q")[col].mean()
    return (g > 0.5).astype(int)


def _median_quarter_state(ev, col, agg):
    """Continuous column -> QUARTER aggregate -> median split across quarters (state 1 = high)."""
    if agg == "mean":
        g = ev.groupby("q")[col].mean()
    elif agg == "std":
        g = ev.groupby("q")[col].std()
    elif agg == "median":
        g = ev.groupby("q")[col].median()
    else:
        g = ev.groupby("q")[col].mean()
    med = g.median()
    return (g > med).astype(int)


REGIMES: Tuple[Dict, ...] = (
    {"id": "easy_regime", "kind": "flag", "col": "easy_regime",
     "state1": "risk-on / easy market", "state0": "risk-off / hard market"},
    {"id": "high_rates", "kind": "flag", "col": "high_rates",
     "state1": "high rates", "state0": "low rates"},
    {"id": "market_drawdown", "kind": "flag", "col": "market_drawdown",
     "state1": "market drawdown", "state0": "no drawdown"},
    {"id": "high_oil", "kind": "flag", "col": "high_oil",
     "state1": "high oil", "state0": "low oil"},
    {"id": "strong_dollar", "kind": "flag", "col": "strong_dollar",
     "state1": "strong dollar", "state0": "weak dollar"},
    {"id": "rates_10y_level", "kind": "median", "col": "rates_10y", "agg": "mean",
     "state1": "above-median 10y", "state0": "below-median 10y"},
    {"id": "curve_2s10s", "kind": "median", "col": "rates_2s10s", "agg": "mean",
     "state1": "steep curve", "state0": "flat/inverted curve"},
    {"id": "oil_momentum", "kind": "median", "col": "oil_z", "agg": "mean",
     "state1": "high oil momentum", "state0": "low oil momentum"},
    {"id": "market_vol", "kind": "median", "col": "vol_63", "agg": "mean",
     "state1": "high market vol", "state0": "low market vol"},
    {"id": "return_dispersion", "kind": "median", "col": "mom_pre_63", "agg": "std",
     "state1": "high dispersion", "state0": "low dispersion"},
    {"id": "market_liquidity", "kind": "median", "col": "liquidity_proxy", "agg": "median",
     "state1": "high liquidity", "state0": "low liquidity"},
)


def build_state(ev, regime: Dict):
    col = regime["col"]
    if col not in ev.columns:
        return None
    if regime["kind"] == "flag":
        qs = _flag_quarter_state(ev, col)
    else:
        qs = _median_quarter_state(ev, col, regime.get("agg", "mean"))
    return ev["q"].map(qs)


# --------------------------------------------------------------------------- #
# B. Evaluation.
# --------------------------------------------------------------------------- #
def _state_metrics(df):
    q = d10.quarterly_backtest(df, _COMP_SN, RET_PRIMARY)
    ic = c10._eval(df, _COMP_SN, PRIMARY_HORIZON_D, False)
    return {"net_25bps": q.get("net_25bps"), "net_50bps": q.get("net_50bps"),
            "turnover": q.get("avg_turnover"), "n_quarters": q.get("n_quarters"),
            "ic_t": ic.get("ic_t"), "ic_mean": ic.get("mean_ic"),
            "top_sector_share": ic.get("top_sector_share"),
            "n_events": int(df[_COMP_SN].notna().sum())}


def _subperiod_positive(df):
    import pandas as pd
    pre = df[df["entry_date"] < pd.Timestamp(_PRE2020)]
    post = df[df["entry_date"] >= pd.Timestamp(_PRE2020)]
    qp = d10.quarterly_backtest(pre, _COMP_SN, RET_PRIMARY)
    qo = d10.quarterly_backtest(post, _COMP_SN, RET_PRIMARY)
    return {"pre_net25": qp.get("net_25bps"), "pre_nq": qp.get("n_quarters"),
            "post_net25": qo.get("net_25bps"), "post_nq": qo.get("n_quarters")}


def evaluate_regime(ev, regime: Dict, base: Dict, base_sp: Dict) -> Tuple[Dict, Dict, Dict]:
    state = build_state(ev, regime)
    if state is None:
        return ({"regime": regime["id"], "classification": CLS_TINY,
                 "reject_reason": "regime column missing"}, {}, {})
    ev = ev.assign(_state=state.values)
    sub = ev[ev[_COMP_SN].notna()]
    s1 = sub[sub["_state"] == 1]
    s0 = sub[sub["_state"] == 0]
    m1 = _state_metrics(s1)
    m0 = _state_metrics(s0)
    # favourable = higher net25 (guard against NaN).
    n1 = m1["net_25bps"] if _finite(m1["net_25bps"]) else -9
    n0 = m0["net_25bps"] if _finite(m0["net_25bps"]) else -9
    fav_state, fav, other = (1, m1, m0) if n1 >= n0 else (0, m0, m1)
    fav_df = s1 if fav_state == 1 else s0
    sp = _subperiod_positive(fav_df)

    base_net25 = base["net_25bps"]
    fav_net25 = fav["net_25bps"]
    cls, reason = _classify(fav, sp, base, base_sp)
    row = {
        "regime": regime["id"], "favourable_state": regime["state1"] if fav_state == 1 else regime["state0"],
        "favourable_net_25bps": fav_net25, "unfavourable_net_25bps": other["net_25bps"],
        "baseline_net_25bps": base_net25,
        "net25_ratio_vs_baseline": (fav_net25 / base_net25) if (_finite(fav_net25) and _finite(base_net25)
                                                                and base_net25) else None,
        "favourable_ic_t": fav["ic_t"], "favourable_turnover": fav["turnover"],
        "favourable_n_quarters": fav["n_quarters"], "favourable_n_events": fav["n_events"],
        "favourable_top_sector_share": fav["top_sector_share"],
        "pre2020_net25": sp["pre_net25"], "pre2020_nq": sp["pre_nq"],
        "post2020_net25": sp["post_net25"], "post2020_nq": sp["post_nq"],
        "classification": cls, "reject_reason": reason,
    }
    detail = {"regime": regime["id"],
              "state1_desc": regime["state1"], "state1_net25": m1["net_25bps"], "state1_ic_t": m1["ic_t"],
              "state1_nq": m1["n_quarters"], "state1_nevents": m1["n_events"],
              "state0_desc": regime["state0"], "state0_net25": m0["net_25bps"], "state0_ic_t": m0["ic_t"],
              "state0_nq": m0["n_quarters"], "state0_nevents": m0["n_events"]}
    return row, detail, sp


def _classify(fav: Dict, sp: Dict, base: Dict, base_sp: Dict) -> Tuple[str, str]:
    fav_net25 = fav["net_25bps"]
    base_net25 = base["net_25bps"]
    if not (_finite(fav_net25) and fav_net25 > 0):
        return CLS_NOIMPROVE, "favourable-state net-25bps not positive"
    if not (_finite(base_net25) and fav_net25 > base_net25):
        return CLS_NOIMPROVE, ("favourable-state net25 %s does not exceed baseline %s"
                               % (_num(fav_net25), _num(base_net25)))
    # sample adequacy
    if not (_finite(fav["n_quarters"]) and fav["n_quarters"] >= MIN_REGIME_QUARTERS
            and fav["n_events"] >= MIN_REGIME_EVENTS):
        return CLS_TINY, ("favourable regime sample too small (%s quarters / %s events; need >=%d / >=%d)"
                          % (fav["n_quarters"], fav["n_events"], MIN_REGIME_QUARTERS, MIN_REGIME_EVENTS))
    # meaningful margin
    if fav_net25 < REGIME_MEANINGFUL_MULT * base_net25:
        return CLS_NOIMPROVE, ("favourable-state net25 %s is not MEANINGFULLY above baseline %s "
                               "(< %.2fx)" % (_num(fav_net25), _num(base_net25), REGIME_MEANINGFUL_MULT))
    reasons = []
    if _finite(fav["ic_t"]) and _finite(base["ic_t"]) and fav["ic_t"] < base["ic_t"] - IC_T_MARGIN:
        reasons.append("favourable-state IC t materially worse")
    # subperiod GENERALISATION: the favourable regime must not be materially worse than the BASELINE's
    # same-subperiod net25 in EITHER era (rejects one-era regimes whose lift is entirely post-2020).
    for label, n, nq, bn in (("pre-2020", sp["pre_net25"], sp["pre_nq"], base_sp["pre_net25"]),
                             ("post-2020", sp["post_net25"], sp["post_nq"], base_sp["post_net25"])):
        if not (_finite(nq) and nq >= MIN_SUBPERIOD_QUARTERS):
            reasons.append("%s subperiod has too few quarters (%s) - favourable regime is one-era"
                           % (label, nq))
        elif not (_finite(n) and n > 0):
            reasons.append("%s subperiod net25 not positive (%s)" % (label, _num(n)))
        elif _finite(bn) and n < bn - EPS_SUB:
            reasons.append("%s the regime is WORSE than the always-on baseline (%s vs %s) - the lift "
                           "does not generalise to this era" % (label, _num(n), _num(bn)))
    if reasons:
        return CLS_OVERFIT, ("favourable-regime net25 improves in-sample but fails the strict test: %s"
                             % "; ".join(reasons))
    return CLS_PASS, ("favourable-regime net25 is meaningfully above baseline, adequately sampled, and "
                      "beats the always-on baseline in BOTH subperiods")


def decide(rows: List[Dict]) -> Tuple[str, str, Dict]:
    passes = [r for r in rows if r["classification"] == CLS_PASS]
    overfits = [r for r in rows if r["classification"] == CLS_OVERFIT]
    if passes:
        champ = max(passes, key=lambda r: (r.get("favourable_net_25bps") or -9))
        return (DEC_READY, ("the %s regime conditions the baseline into a meaningfully stronger, "
                            "subperiod-robust, adequately-sampled edge - ready for Phase 10-P paper-rule "
                            "packaging (as a regime-gated overlay)." % champ["regime"]),
                {"champion": champ["regime"], "n_pass": len(passes)})
    if overfits:
        return (DEC_OVERFIT, ("one or more regimes showed a stronger favourable-state edge in-sample but "
                              "it failed the strict test (one-era / subperiod sign reversal / IC-t worse) "
                              "- classic regime selection overfit; the always-on baseline stays champion "
                              "and no regime overlay is productized."),
                {"champion": "baseline_composite_sn", "n_pass": 0, "n_overfit": len(overfits)})
    return (DEC_BASELINE, ("no simple owned/local regime (market trend, rates, oil, dollar, vol, "
                           "dispersion, liquidity) conditions the baseline into a meaningfully stronger "
                           "edge; the always-on two-leg baseline remains champion."),
            {"champion": "baseline_composite_sn", "n_pass": 0})


# --------------------------------------------------------------------------- #
# C. Artifacts + report.
# --------------------------------------------------------------------------- #
def write_artifacts(P: _Paths, rows, details, base) -> None:
    _write_csv(P.art("inventory"),
               ["regime_id", "kind", "column", "state1", "state0"],
               [[r["id"], r["kind"], r["col"], r["state1"], r["state0"]] for r in REGIMES])
    hdr = ["regime", "favourable_state", "favourable_net_25bps", "unfavourable_net_25bps",
           "baseline_net_25bps", "net25_ratio_vs_baseline", "favourable_ic_t", "favourable_turnover",
           "favourable_n_quarters", "favourable_n_events", "favourable_top_sector_share",
           "pre2020_net25", "pre2020_nq", "post2020_net25", "post2020_nq", "classification",
           "reject_reason"]

    def _r(v):
        return [v.get("regime"), v.get("favourable_state"), _num(v.get("favourable_net_25bps")),
                _num(v.get("unfavourable_net_25bps")), _num(v.get("baseline_net_25bps")),
                _num(v.get("net25_ratio_vs_baseline")), _num(v.get("favourable_ic_t")),
                _num(v.get("favourable_turnover")), v.get("favourable_n_quarters"),
                v.get("favourable_n_events"), _num(v.get("favourable_top_sector_share")),
                _num(v.get("pre2020_net25")), v.get("pre2020_nq"), _num(v.get("post2020_net25")),
                v.get("post2020_nq"), v.get("classification"), v.get("reject_reason")]

    _write_csv(P.art("scorecard"), hdr, [_r(v) for v in rows])
    _write_csv(P.art("rejected"), hdr,
               [_r(v) for v in rows if v["classification"] in (CLS_OVERFIT, CLS_TINY, CLS_NOIMPROVE)])
    _write_csv(P.art("subperiod"),
               ["regime", "favourable_state", "pre2020_net25", "pre2020_nq", "post2020_net25",
                "post2020_nq", "classification"],
               [[v.get("regime"), v.get("favourable_state"), _num(v.get("pre2020_net25")),
                 v.get("pre2020_nq"), _num(v.get("post2020_net25")), v.get("post2020_nq"),
                 v.get("classification")] for v in rows])
    _write_csv(P.art("state_detail"),
               ["regime", "state1_desc", "state1_net25", "state1_ic_t", "state1_nq", "state1_nevents",
                "state0_desc", "state0_net25", "state0_ic_t", "state0_nq", "state0_nevents"],
               [[d["regime"], d["state1_desc"], _num(d["state1_net25"]), _num(d["state1_ic_t"]),
                 d["state1_nq"], d["state1_nevents"], d["state0_desc"], _num(d["state0_net25"]),
                 _num(d["state0_ic_t"]), d["state0_nq"], d["state0_nevents"]] for d in details])


def _next_plan(decision: str) -> Dict:
    if decision == DEC_READY:
        return {"phase": "10-P", "from_decision": decision,
                "next_step": "Phase 10-P: package the regime-gated overlay as PAPER-ONLY rules.",
                "exact_next_command": "review research/output/%s/regime_conditional_scorecard.csv" % STEM,
                "constraints": ["owned/local only", "no live API", "paper-only", "no orders",
                                "no automation", "no deploy", "no commit", "no push"]}
    return {"phase": "10-Q", "from_decision": decision,
            "next_step": ("Phase 10-Q: owned data exhausted for stronger alpha (reweighting 10-L-B, "
                          "incremental factors 10-M, transforms/interactions 10-N, and regimes 10-O all "
                          "failed to beat the baseline out-of-sample). Write the final honest research "
                          "decision: package the modest baseline for paper review, or pause pending new "
                          "owned data (a PIT-normalized value leg is the clearest gap)."),
            "exact_next_command": "python research/run_phase10q_owned_data_exhaustion_research_decision.py",
            "constraints": ["owned/local only", "no live API", "paper-only", "no orders", "no automation",
                            "no deploy", "no commit", "no push"]}


def _build_report(decision, rationale, meta, rows, details, base, integrity, n_events, n_tickers,
                  key_visible) -> Dict:
    passes = [r for r in rows if r["classification"] == CLS_PASS]
    overfits = [r for r in rows if r["classification"] == CLS_OVERFIT]
    champ_id = meta.get("champion")

    def _slim(v):
        return {k: (_round(v[k], 5) if isinstance(v.get(k), float) else v.get(k))
                for k in ("regime", "favourable_state", "favourable_net_25bps", "baseline_net_25bps",
                          "net25_ratio_vs_baseline", "favourable_ic_t", "favourable_n_quarters",
                          "favourable_n_events", "pre2020_net25", "post2020_net25", "classification",
                          "reject_reason")}

    return {
        "phase": PHASE, "phase_name": PHASE_NAME, "as_of": AS_OF,
        "decision": decision, "decision_rationale": rationale, "allowed_decisions": list(ALLOWED_DECISIONS),
        "objective": ("test whether the modest 10-D composite_sn baseline is CONDITIONALLY stronger inside "
                      "a simple pre-declared ex-ante owned/local regime (market trend / rates / oil / "
                      "dollar / vol / dispersion / liquidity) - NOT a broad search, no live macro API, no "
                      "new data, no tuned thresholds"),
        "offline": True, "performs_network": PERFORMS_NETWORK,
        "eodhd_key_visible": bool(key_visible), "eodhd_key_required": False,
        "primary_horizon_days": PRIMARY_HORIZON_D,
        "scoreable_events": n_events, "scoreable_tickers": n_tickers,
        "input_inventory": [{"regime_id": r["id"], "kind": r["kind"], "column": r["col"],
                             "state1": r["state1"], "state0": r["state0"]} for r in REGIMES],
        "n_regimes": len(REGIMES),
        "regime_thresholds": "simple median (continuous) / majority (binary flag) month-level splits; "
                             "no tuned regime boundaries",
        "sample_bars": {"min_regime_quarters": MIN_REGIME_QUARTERS, "min_regime_events": MIN_REGIME_EVENTS,
                        "min_subperiod_quarters": MIN_SUBPERIOD_QUARTERS,
                        "meaningful_multiple": REGIME_MEANINGFUL_MULT},
        "baseline": {"signal": "composite_sn", "weighting": "equal (fcf+ / accruals-), sector-neutral",
                     "ic_t_63d": _round(base.get("ic_t"), 3),
                     "quarterly_net_25bps": _round(base.get("net_25bps"), 5),
                     "quarterly_net_50bps": _round(base.get("net_50bps"), 5),
                     "quarterly_turnover": _round(base.get("turnover"), 4),
                     "n_quarters": base.get("n_quarters"),
                     "alpha_character": "modest / boundary (63d IC t < 3.0 strong bar; not oversold)"},
        "phase10d_baseline_reproduction": {
            "got": {k: _round(v, 5) for k, v in integrity["got"].items()},
            "frozen": {k: _round(v, 5) for k, v in integrity["frozen"].items()},
            "reproduces_within_tolerance": integrity["reproduces_within_tolerance"],
            "tolerances": REPRO_TOL},
        "n_variants": len(rows),
        "variants_tested": [_slim(v) for v in rows],
        "candidates_tested": [_slim(v) for v in rows],
        "rejected_candidates": [_slim(v) for v in rows
                                if v["classification"] in (CLS_OVERFIT, CLS_TINY, CLS_NOIMPROVE)],
        "champion": {"champion": champ_id if passes else "baseline_composite_sn",
                     "baseline_remains_champion": not passes},
        "baseline_vs_champion": {
            "baseline_net_25bps": _round(base.get("net_25bps"), 5),
            "champion_favourable_net_25bps": _round(
                next((r["favourable_net_25bps"] for r in passes), base.get("net_25bps")), 5) if passes
                else _round(base.get("net_25bps"), 5),
            "improvement": _round(next((r["favourable_net_25bps"] for r in passes), base.get("net_25bps"))
                                  - base.get("net_25bps"), 6) if passes else 0.0},
        "oos_stability_summary": {"note": "each favourable regime's net25 checked in pre/post-2020 "
                                          "subperiods; one-era regimes rejected"},
        "cohort_stability_summary": {"note": "subperiod (pre/post-2020) robustness enforced on the "
                                             "favourable state; regimes are ex-ante and PIT"},
        "sector_concentration_summary": {"note": "favourable-state top-sector share reported per regime"},
        "turnover_cost_summary": {"baseline_turnover": _round(base.get("turnover"), 4),
                                  "cost_model": "quarterly book; net(bps)=spread-(bps/1e4)*turnover*2.0"},
        "n_overfit_variants": len(overfits),
        "implementation_limits": [
            "regime states use a full-sample median/majority threshold (permitted by the brief) - a mild "
            "in-sample element; the subperiod-robustness guard and sample-adequacy bar are the defenses "
            "against regime-selection overfit",
            "11 regimes are screened; selecting the best of several is a multiple-testing risk explicitly "
            "controlled by requiring a meaningful (>=1.25x) margin AND both-subperiod positivity AND "
            "adequate sample",
            "conditional strategy = run the sector-neutral book only in favourable-regime quarters; it "
            "trades less often and is a simple timing overlay, not a new signal",
            "no 10-M/10-N candidate survived to be conditioned, so only the baseline composite_sn is "
            "regime-gated here",
        ],
        "next_recommended_phase": "10-P" if passes else "10-Q",
        "safety": {"paper_only": True, "owned_local_data_only": True, "no_live_api_calls": True,
                   "no_orders": True, "no_automation": True, "no_broker": True, "no_deploy": True},
        "constraints_honored": ["offline (no network/key/provider probe/live macro API)",
                                "owned/local data only", "simple median/majority thresholds only",
                                "no tuned regime boundaries", "no new data", "no Paper Trader writes",
                                "no orders", "no automation", "no broker", "no deploy", "no GCP",
                                "no package install", "no full regression", "no commit", "no push"],
    }


def _print_summary(report: Dict) -> None:
    ch = report.get("champion", {})
    print("[10-O] decision=%s | regimes=%s | champion=%s | baseline_reproduces=%s"
          % (report.get("decision"), report.get("n_regimes"), ch.get("champion"),
             report.get("phase10d_baseline_reproduction", {}).get("reproduces_within_tolerance")))


# --------------------------------------------------------------------------- #
# D. Orchestration.
# --------------------------------------------------------------------------- #
def run(out_dir: Optional[Path] = None, *, as_of: str = AS_OF, verbose: bool = True) -> Dict:
    P = _Paths(out_dir)
    P.out.mkdir(parents=True, exist_ok=True)
    log = t8._Log(verbose)
    try:
        import pandas as pd  # noqa: F401
        key_visible = bool(os.environ.get(EODHD_KEY_ENV))
        log.step("preflight", "OFFLINE", "owned-data only; no network / no macro API; key_visible=%s"
                 % key_visible)

        ev, ok, stats = c10.build_panel(as_of, None, log)
        if not ok:
            return _blocker(P, log, "Norgate panel empty - rebuild before Phase 10-O.", key_visible)
        n_events = int(stats.get("events_usable", len(ev)))
        n_tickers = int(stats.get("tickers_usable", ev["ticker"].nunique()))
        ev, base_cols = c10.attach_signals(ev, c10._default_norm_csvs(), log)
        ev, comp_cov, _rl, _sl = d10.build_composite(ev, base_cols, log)
        if "month" not in ev.columns:
            ev["month"] = ev["entry_date"].dt.to_period("M")
        ev["q"] = ev["entry_date"].dt.to_period("Q")
        integrity = m10.integrity_check(ev, log)
        if not integrity["reproduces_within_tolerance"]:
            return _finish(P, log, DEC_REPAIR,
                           "composite_sn does not reproduce the frozen 10-D baseline; inputs need repair.",
                           {"champion": "baseline_composite_sn"}, [], [], {}, integrity, n_events,
                           n_tickers, key_visible)

        base = _state_metrics(ev[ev[_COMP_SN].notna()])
        base_sp = _subperiod_positive(ev[ev[_COMP_SN].notna()])
        log.step("baseline", "DONE", "baseline net25=%s ic_t=%s nq=%s | pre=%s post=%s"
                 % (_num(base["net_25bps"]), _num(base["ic_t"]), base["n_quarters"],
                    _num(base_sp["pre_net25"]), _num(base_sp["post_net25"])))

        rows, details = [], []
        for regime in REGIMES:
            row, detail, _sp = evaluate_regime(ev, regime, base, base_sp)
            rows.append(row)
            if detail:
                details.append(detail)
            log.step("regime", row["classification"],
                     "%s: fav_net25=%s (base %s, ratio %s) | pre=%s post=%s | nq=%s"
                     % (regime["id"], _num(row.get("favourable_net_25bps")), _num(base["net_25bps"]),
                        _num(row.get("net25_ratio_vs_baseline")), _num(row.get("pre2020_net25")),
                        _num(row.get("post2020_net25")), row.get("favourable_n_quarters")))

        decision, rationale, meta = decide(rows)
        write_artifacts(P, rows, details, base)
        report = _build_report(decision, rationale, meta, rows, details, base, integrity, n_events,
                               n_tickers, key_visible)
        _write_json(P.art("report"), report)
        _write_json(P.art("next_plan"), _next_plan(decision))
        log.step("artifacts", "DONE", "wrote %d artifacts" % len(_ARTIFACTS))
        _print_summary(report)
        return report
    except Exception as exc:  # pragma: no cover - defensive
        import traceback
        detail = "%s: %s" % (type(exc).__name__, exc)
        log.step("run", "ERROR", detail)
        report = {"phase": PHASE, "decision": DEC_REPAIR, "decision_rationale": detail,
                  "repro_command": "python research/run_%s.py" % STEM,
                  "traceback": traceback.format_exc(),
                  "safety": {"paper_only": True, "owned_local_data_only": True, "no_live_api_calls": True,
                             "no_orders": True, "no_automation": True, "no_broker": True,
                             "no_deploy": True}}
        try:
            P.out.mkdir(parents=True, exist_ok=True)
            _write_json(P.art("report"), report)
        except Exception:
            pass
        return report


def _finish(P, log, decision, rationale, meta, rows, details, base, integrity, n_events, n_tickers,
            key_visible) -> Dict:
    write_artifacts(P, rows, details, base or {})
    report = _build_report(decision, rationale, meta, rows, details, base or {}, integrity, n_events,
                           n_tickers, key_visible)
    _write_json(P.art("report"), report)
    _write_json(P.art("next_plan"), _next_plan(decision))
    _print_summary(report)
    return report


def _blocker(P, log, detail, key_visible) -> Dict:
    log.step("blocker", "REPAIR", detail)
    report = {"phase": PHASE, "phase_name": PHASE_NAME, "decision": DEC_REPAIR,
              "decision_rationale": detail, "offline": True,
              "safety": {"paper_only": True, "owned_local_data_only": True, "no_live_api_calls": True,
                         "no_orders": True, "no_automation": True, "no_broker": True, "no_deploy": True}}
    _write_json(P.art("report"), report)
    print("[10-O] decision=%s | %s" % (DEC_REPAIR, detail))
    return report


def _parse_args(argv=None):
    p = argparse.ArgumentParser(description="Phase 10-O - Regime And Conditional Alpha Gating")
    p.add_argument("--out-dir", default=None)
    p.add_argument("--as-of", default=AS_OF)
    p.add_argument("--quiet", action="store_true")
    return p.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    ns = _parse_args(argv)
    report = run(out_dir=ns.out_dir, as_of=ns.as_of, verbose=not ns.quiet)
    return 0 if report.get("decision") in ALLOWED_DECISIONS else 1


if __name__ == "__main__":
    raise SystemExit(main())
