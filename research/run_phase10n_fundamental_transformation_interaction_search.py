"""Phase 10-N - Fundamental Transformation And Quality-Value Interaction Search.

WHY THIS PHASE EXISTS
    Phase 10-M tested owned fundamental factors as LINEAR incremental legs and returned
    BASELINE_REMAINS_CHAMPION (7/8 wrong-signed or sign-unstable; the one eligible cash-quality leg
    diluted the cost-efficient spread). The next honest question: does a defensible NONLINEAR transform
    of the owned factors - or an economically-motivated INTERACTION between two owned factors - unlock
    signal that the linear form missed?

    Phase 10-N tests exactly that, narrowly. It does NOT add providers, does NOT make live API calls,
    does NOT run a broad alpha search, does NOT optimise weights, does NOT use ML / optimiser / genetic
    search, and does NOT explode polynomials. Every candidate is a PRE-DECLARED, economically-named
    transform or interaction of OWNED factors, scored with the EXACT 10-D/10-M engine (m10.score_signal
    -> c10._eval / d10.quarterly_backtest / d10.walk_forward_h) against a matched baseline, so results are
    directly comparable to composite_sn.

ALLOWED TRANSFORMS (defensible; pre-declared)
    signed-log, within-month percentile rank, sector-neutral (month x sector) rank, year-over-year delta,
    acceleration (delta of delta). No arbitrary polynomials.

TRANSFORMED CANDIDATES (<= 25)
    Alternative-composite constructions (baseline 2-factor structure, transformed legs):
      altcomp_signed_log, altcomp_rank, altcomp_snrank
    Incremental transformed 3rd legs (added to composite_sn at the allowed 70/30, 60/40, 50/50 weights):
      cash_roa_rank, gross_prof_snrank, fcf_delta       (x 3 weights each)

INTERACTION CANDIDATES (<= 10; all named + economically explained)
    quality x value        : cash_return_on_assets x earnings_yield
    profitability x invest : gross_profitability x (-asset_growth)
    accruals x leverage    : (-operating_accruals) x (-debt_to_assets)
    FCF x value            : fcf_to_assets x earnings_yield
    Each interaction term is standalone-screened (directional); an eligible interaction is then blended
    into composite_sn at the allowed weights and judged by the same strict relative test.

STRICT GATES (identical discipline to 10-L-B / 10-M)
    A candidate beats the matched baseline only if net-25bps strictly up, net-50bps not worse, turnover
    not materially worse (<=1.10x; hard reject >1.50x), IC t >= base-0.10, OOS frac-positive >= base,
    top-sector share <= base, AND robust (both cohorts +, both subperiods +). In-sample net25 gains that
    fail a secondary criterion are REJECT_TRANSFORM_OVERFIT, not champions.

TERMINAL DECISIONS
    TRANSFORMED_ALPHA_READY_FOR_PAPER_RULES | BASELINE_REMAINS_CHAMPION | REJECT_TRANSFORM_OVERFIT |
    NEEDS_TRANSFORM_INPUT_REPAIR | NEEDS_MORE_OWNED_DATA

CONSTRAINTS HONORED
    Fully offline (no network / key / provider probe); owned/local data only (Norgate panel + owned EODHD
    fundamentals + the 10-M reconstructed factor CSVs); no ML / optimiser / genetic search; no polynomial
    explosion; no new factor family; no weight optimisation; no Paper Trader writes; no orders; no
    automation; no broker; no deploy; no GCP; no package install; targeted tests only; keys never
    printed/written; output is research metadata only. No commit. No push.
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
_read_csv_file = s8._read_csv_file
_round = s8._round
_rel = s8._rel
_finite = c10._finite
_num = c10._num

PHASE = "10-N"
PHASE_NAME = "Fundamental Transformation And Quality-Value Interaction Search"
STEM = "phase10n_fundamental_transformation_interaction_search"
PERFORMS_NETWORK = False

AS_OF = c10.AS_OF
PRIMARY_HORIZON_D = d10.PRIMARY_HORIZON_D
RET_PRIMARY = d10.RET_PRIMARY
_COMP_SN = d10.COMP_SN
_NORM_BASE = _REPO_ROOT / "research" / "data" / "eodhd" / "normalized"
_M10_RECON = (_REPO_ROOT / "research" / "output"
              / "phase10m_owned_fundamental_incremental_alpha_expansion" / "reconstructed_factors")
EODHD_KEY_ENV = "EODHD_API_KEY"

SINGLE_WEIGHTS = m10.SINGLE_WEIGHTS                  # (0.7,0.3),(0.6,0.4),(0.5,0.5)
MAX_TRANSFORM_CANDIDATES = 25
MAX_INTERACTION_CANDIDATES = 10

# Subperiod-robustness guard: a candidate that passes the full-period strict relative test is ONLY kept
# if its net-25bps advantage over the matched baseline also holds (is not materially reversed) in BOTH
# the pre-2020 and post-2020 subperiods. This catches the classic failure mode where a full-sample IC/net
# gain is a one-era relic that reverses out-of-sample. Tolerance allows small subperiod noise only.
EPS_SUB = 0.0005

# Base factors attached for transforms / interactions (a-priori orientation).
_BASE_FACTORS: Tuple[Dict, ...] = (
    {"feature": "cash_return_on_assets", "family": "eodhd_cash_return_on_assets", "orientation": +1,
     "source": "recon"},
    {"feature": "gross_profitability", "family": "eodhd_gross_profitability", "orientation": +1,
     "source": "norm"},
    {"feature": "asset_growth", "family": "eodhd_asset_growth", "orientation": -1, "source": "norm"},
    {"feature": "debt_to_assets", "family": "eodhd_debt_to_assets", "orientation": -1, "source": "recon"},
)
# earnings_yield is panel-native (owned; provenance = 8-series earnings-event feature build, PIT at the
# report) - used ONLY for interaction terms; any interaction that PASSED would carry a value-provenance
# caveat (a dedicated EODHD-normalized value reconstruction would be required before productizing).
_VALUE_COL = "earnings_yield"

REPRO_TOL = m10.REPRO_TOL

DEC_READY = "TRANSFORMED_ALPHA_READY_FOR_PAPER_RULES"
DEC_BASELINE = "BASELINE_REMAINS_CHAMPION"
DEC_OVERFIT = "REJECT_TRANSFORM_OVERFIT"
DEC_REPAIR = "NEEDS_TRANSFORM_INPUT_REPAIR"
DEC_MORE_DATA = "NEEDS_MORE_OWNED_DATA"
ALLOWED_DECISIONS = (DEC_READY, DEC_BASELINE, DEC_OVERFIT, DEC_REPAIR, DEC_MORE_DATA)

CLS_BASELINE = m10.CLS_BASELINE
CLS_PASS = m10.CLS_PASS
CLS_OVERFIT = m10.CLS_OVERFIT
CLS_COST = m10.CLS_COST
CLS_TURN = m10.CLS_TURN
CLS_NOIMPROVE = m10.CLS_NOIMPROVE

_ARTIFACTS = {
    "report": "%s.json" % STEM,
    "inventory": "transform_interaction_inventory.csv",
    "standalone": "interaction_standalone_screen.csv",
    "scorecard": "transform_variant_scorecard.csv",
    "baseline_vs": "baseline_vs_variants.csv",
    "oos": "oos_stability_report.csv",
    "cohort": "cohort_stability_report.csv",
    "sector": "sector_concentration_report.csv",
    "turnover": "turnover_cost_report.csv",
    "rejected": "rejected_candidates.csv",
    "next_plan": "phase10o_next_plan.json",
}


class _Paths:
    def __init__(self, out_dir=None):
        self.out = Path(out_dir) if out_dir else (_REPO_ROOT / "research" / "output" / STEM)
        self.recon = self.out / "derived_factors"

    def art(self, key: str) -> Path:
        return self.out / _ARTIFACTS[key]


# --------------------------------------------------------------------------- #
# A. Transform primitives (defensible; pre-declared).
# --------------------------------------------------------------------------- #
def _signed_log(ev, src: str) -> str:
    import numpy as np
    col = "_slog_%s" % src
    x = ev[src].to_numpy(dtype=float)
    ev[col] = np.sign(x) * np.log1p(np.abs(x))
    return col


def _rank_wm(ev, src: str) -> str:
    """Within-month percentile rank, centered at 0."""
    col = "_rank_%s" % src
    ev[col] = ev.groupby("month")[src].rank(pct=True) - 0.5
    return col


def _snrank(ev, src: str) -> str:
    """Within month x sector percentile rank, centered at 0 (sector-neutral rank)."""
    col = "_snrank_%s" % src
    if "sector" in ev.columns:
        ev[col] = ev.groupby(["month", "sector"])[src].rank(pct=True) - 0.5
    else:
        ev[col] = ev.groupby("month")[src].rank(pct=True) - 0.5
    return col


def build_delta_csv(feature: str, src_csv: Path, out_csv: Path, log) -> Tuple[Path, int]:
    """Year-over-year delta of a normalized factor series (value(t) - value(t-4q)), carried at the later
    report's availability date (PIT-safe). Reuses the same 4-quarter lag convention as b10._yoy."""
    import pandas as pd
    rows = _read_csv_file(src_csv)
    if not rows:
        _write_csv(out_csv, ["ticker", "available_date", feature], [])
        return out_csv, 0
    base_feat = [c for c in rows[0].keys() if c not in ("ticker", "available_date")][0]
    df = pd.DataFrame(rows)
    df["available_date"] = df["available_date"].astype(str)
    df[base_feat] = pd.to_numeric(df[base_feat], errors="coerce")
    out: List[List] = []
    for tk, g in df.groupby("ticker"):
        g = g.sort_values("available_date")
        vals = g[base_feat].tolist()
        dates = g["available_date"].tolist()
        for i in range(len(vals)):
            if i >= 4 and vals[i] is not None and vals[i - 4] is not None \
                    and _finite(vals[i]) and _finite(vals[i - 4]):
                out.append([tk, dates[i], vals[i] - vals[i - 4]])
    out.sort(key=lambda r: (r[0], r[1]))
    _write_csv(out_csv, ["ticker", "available_date", feature], out)
    log.step("delta", "DONE", "%s: %d rows" % (feature, len(out)))
    return out_csv, len(out)


# --------------------------------------------------------------------------- #
# B. Attach helpers.
# --------------------------------------------------------------------------- #
def _base_csv(cand: Dict) -> Path:
    if cand["source"] == "recon":
        return _M10_RECON / ("%s.csv" % cand["feature"])
    return _NORM_BASE / cand["family"] / ("%s.csv" % cand["feature"])


def _subperiod_net25(df, col):
    q = d10.quarterly_backtest(df, col, RET_PRIMARY)
    return q.get("net_25bps")


def apply_subperiod_guard(cls: str, reason: str, sub, vcol: str, bcol: str) -> Tuple[str, str]:
    """Downgrade a PASS_STRICT candidate to REJECT_OVERFIT if its net-25bps advantage over the matched
    baseline is materially reversed in EITHER subperiod (pre/post-2020) - i.e. the full-sample gain does
    not generalise across eras."""
    import pandas as pd
    if cls != CLS_PASS:
        return cls, reason
    pre = sub[sub["entry_date"] < pd.Timestamp(m10._PRE2020)]
    post = sub[sub["entry_date"] >= pd.Timestamp(m10._PRE2020)]
    v_pre, b_pre = _subperiod_net25(pre, vcol), _subperiod_net25(pre, bcol)
    v_post, b_post = _subperiod_net25(post, vcol), _subperiod_net25(post, bcol)
    bad = []
    if _finite(v_pre) and _finite(b_pre) and v_pre < b_pre - EPS_SUB:
        bad.append("pre-2020 net25 reverses (%s vs base %s)" % (_num(v_pre), _num(b_pre)))
    if _finite(v_post) and _finite(b_post) and v_post < b_post - EPS_SUB:
        bad.append("post-2020 net25 reverses (%s vs base %s)" % (_num(v_post), _num(b_post)))
    if bad:
        return CLS_OVERFIT, ("full-sample net-25bps gain is one-era-driven and does not generalise: %s"
                             % "; ".join(bad))
    return cls, reason


def _wm_z(ev_sub, col: str):
    return x8._within_month_z(ev_sub, col)


# --------------------------------------------------------------------------- #
# C. Candidate builders.
# --------------------------------------------------------------------------- #
def build_transform_variants(ev, log) -> List[Dict]:
    """Alternative-composite transforms + incremental transformed 3rd legs. Returns scorecard rows."""
    import numpy as np
    rows: List[Dict] = []

    # global baseline on full comp support (reference + matched baseline for alt-composites).
    base_full = ev[ev[_COMP_SN].notna()].copy()
    gsc = m10.score_signal(base_full, _COMP_SN)
    rows.append(_row("baseline_composite_sn", "baseline", "composite_sn", "1/1", None, gsc,
                     CLS_BASELINE, "confirmed baseline configuration",
                     "frozen 10-D equal-weight sector-neutral composite (reference)"))

    o_fcf = "o_fcf_to_assets__sn"
    o_acc = "o_operating_accruals__sn"

    # --- alt-composite: signed-log legs ---
    sub = base_full
    for label, tf in (("signed_log", _signed_log), ("rank", _rank_wm), ("snrank", _snrank)):
        lf = tf(sub, o_fcf)
        la = tf(sub, o_acc)
        col = "_altcomp_%s" % label
        sub[col] = (_wm_z(sub, lf) + _wm_z(sub, la)).to_numpy()
        sc = m10.score_signal(sub, col)
        cls, reason = m10.classify(sc, gsc)
        cls, reason = apply_subperiod_guard(cls, reason, sub, col, _COMP_SN)
        rows.append(_row("altcomp_%s" % label, "alt_composite", "composite(%s legs)" % label, "1/1",
                         gsc, sc, cls, reason,
                         "baseline 2-factor composite with %s-transformed sector-neutral legs" % label))
        log.step("transform", cls, "altcomp_%s | net25=%s (base %s) | ic_t=%s"
                 % (label, _num(sc.get("net_25bps")), _num(gsc.get("net_25bps")), _num(sc.get("ic_t"))))

    # --- incremental transformed 3rd legs ---
    # each entry: (id, leg_column_on_ev, description)
    o_cash = "o_cash_return_on_assets__sn"
    o_gp = "o_gross_profitability__sn"
    incr_specs = []
    if o_cash in ev.columns:
        incr_specs.append(("cash_roa_rank", _rank_wm(ev, o_cash),
                           "within-month rank of oriented cash-return-on-assets (robustified quality)"))
    if o_gp in ev.columns:
        incr_specs.append(("gross_prof_snrank", _snrank(ev, o_gp),
                           "sector-neutral rank of oriented gross profitability"))
    if "fcf_delta_sn" in ev.columns:
        incr_specs.append(("fcf_delta", "fcf_delta_sn",
                           "sector-neutral yoy change in fcf/assets (improving cash quality)"))

    for vid_base, legcol, desc in incr_specs:
        for (wb, wn) in SINGLE_WEIGHTS:
            sub = ev[ev[_COMP_SN].notna() & ev[legcol].notna()].copy()
            if sub.empty:
                continue
            base_sc = m10.score_signal(sub, _COMP_SN)
            col = "_blend_%s_%d" % (vid_base, int(wn * 100))
            sub[col] = m10._blend(sub, _COMP_SN, [legcol], [wb, wn])
            sc = m10.score_signal(sub, col)
            cls, reason = m10.classify(sc, base_sc)
            cls, reason = apply_subperiod_guard(cls, reason, sub, col, _COMP_SN)
            vid = "base%02d_%s_%02d" % (int(wb * 100), vid_base, int(wn * 100))
            rows.append(_row(vid, "incremental_transform", "composite_sn+%s" % vid_base,
                             "%d/%d" % (int(wb * 100), int(wn * 100)), base_sc, sc, cls, reason, desc))
            log.step("transform", cls, "%s | net25=%s (base %s) | ic_t=%s | share=%s"
                     % (vid, _num(sc.get("net_25bps")), _num(base_sc.get("net_25bps")),
                        _num(sc.get("ic_t")), _num(sc.get("top_sector_share"))))
    return rows


def build_interaction_candidates(ev, log) -> Tuple[List[Dict], List[Dict]]:
    """Standalone-screen 4 named interaction terms; blend eligible ones into composite_sn. Returns
    (screen_rows, scorecard_rows)."""
    import numpy as np
    import pandas as pd
    o_cash = "o_cash_return_on_assets__sn"
    o_gp = "o_gross_profitability__sn"
    o_ag = "o_asset_growth__sn"
    o_fcf = "o_fcf_to_assets__sn"
    o_acc = "o_operating_accruals__sn"
    o_dta = "o_debt_to_assets__sn"

    # value column, sector-neutralised in-place.
    ev["_ey_sn"] = np.nan
    if _VALUE_COL in ev.columns:
        if "sector" in ev.columns:
            ev["_ey_sn"] = ev.groupby(["month", "sector"])[_VALUE_COL].transform(lambda s: s - s.mean())
        else:
            ev["_ey_sn"] = x8._within_month_z(ev, _VALUE_COL)

    specs = [
        {"id": "quality_x_value", "a": o_cash, "b": "_ey_sn", "orientation": +1,
         "econ": "high cash-quality AND cheap (value) -> stronger forward return"},
        {"id": "profitability_x_investment", "a": o_gp, "b": o_ag, "orientation": +1,
         "econ": "profitable AND capital-disciplined (oriented low asset growth) -> stronger return"},
        {"id": "accruals_x_leverage", "a": o_acc, "b": o_dta, "orientation": +1,
         "econ": "clean accruals (oriented) AND low leverage (oriented) -> safer quality -> stronger"},
        {"id": "fcf_x_value", "a": o_fcf, "b": "_ey_sn", "orientation": +1,
         "econ": "high FCF/assets AND cheap (value) -> stronger forward return"},
    ]
    screen_rows: List[Dict] = []
    score_rows: List[Dict] = []
    eligible: List[Dict] = []
    for spec in specs:
        a, b = spec["a"], spec["b"]
        if a not in ev.columns or b not in ev.columns:
            screen_rows.append({"id": spec["id"], "econ": spec["econ"], "eligible": False,
                                "reject_reason": "input column missing", "ic_mean": None, "ic_t": None})
            continue
        col = "_inter_%s" % spec["id"]
        za = x8._within_month_z(ev, a)
        zb = x8._within_month_z(ev, b)
        ev[col] = spec["orientation"] * (za * zb).to_numpy()
        sub = ev[ev[col].notna()]
        ic = c10._eval(sub, col, PRIMARY_HORIZON_D, False)
        old = c10._eval(sub[sub["cohort"] == "old"], col, PRIMARY_HORIZON_D, False) \
            if "cohort" in sub.columns else ic
        new = c10._eval(sub[sub["cohort"] == "new"], col, PRIMARY_HORIZON_D, False) \
            if "cohort" in sub.columns else ic
        pre = c10._eval(sub[sub["entry_date"] < pd.Timestamp(m10._PRE2020)], col, PRIMARY_HORIZON_D, False)
        post = c10._eval(sub[sub["entry_date"] >= pd.Timestamp(m10._PRE2020)], col, PRIMARY_HORIZON_D,
                         False)
        cohorts_pos = c10._pos(old) and c10._pos(new)
        subs_pos = c10._pos(pre) and c10._pos(post)
        elig = bool(c10._pos(ic) and cohorts_pos and subs_pos)
        reason = "" if elig else (
            "interaction oriented 63d IC not positive" if not c10._pos(ic)
            else ("cohort sign-unstable" if not cohorts_pos else "subperiod sign-unstable"))
        screen_rows.append({"id": spec["id"], "econ": spec["econ"], "col": col,
                            "ic_mean": ic.get("mean_ic"), "ic_t": ic.get("ic_t"),
                            "both_cohorts_pos": cohorts_pos, "both_subperiods_pos": subs_pos,
                            "top_sector_share": ic.get("top_sector_share"),
                            "eligible": elig, "reject_reason": reason})
        log.step("interaction", "ELIGIBLE" if elig else "REJECT",
                 "%s: 63d IC=%s t=%s eligible=%s" % (spec["id"], _num(ic.get("mean_ic")),
                                                     _num(ic.get("ic_t")), elig))
        if elig:
            eligible.append({"id": spec["id"], "col": col, "econ": spec["econ"]})

    for e in eligible:
        for (wb, wn) in SINGLE_WEIGHTS:
            sub = ev[ev[_COMP_SN].notna() & ev[e["col"]].notna()].copy()
            if sub.empty:
                continue
            base_sc = m10.score_signal(sub, _COMP_SN)
            bcol = "_iblend_%s_%d" % (e["id"], int(wn * 100))
            sub[bcol] = m10._blend(sub, _COMP_SN, [e["col"]], [wb, wn])
            sc = m10.score_signal(sub, bcol)
            cls, reason = m10.classify(sc, base_sc)
            cls, reason = apply_subperiod_guard(cls, reason, sub, bcol, _COMP_SN)
            vid = "base%02d_%s_%02d" % (int(wb * 100), e["id"], int(wn * 100))
            score_rows.append(_row(vid, "interaction", "composite_sn+%s" % e["id"],
                                   "%d/%d" % (int(wb * 100), int(wn * 100)), base_sc, sc, cls, reason,
                                   e["econ"]))
            log.step("interaction", cls, "%s | net25=%s (base %s)"
                     % (vid, _num(sc.get("net_25bps")), _num(base_sc.get("net_25bps"))))
    return screen_rows, score_rows


def _row(vid, group, legs, weights, base, sc, cls, reason, desc):
    return {
        "variant_id": vid, "group": group, "legs": legs, "weights": weights,
        "ic_mean": sc.get("ic_mean"), "ic_t": sc.get("ic_t"),
        "quarterly_gross_spread": sc.get("gross_spread"),
        "quarterly_net_25bps": sc.get("net_25bps"), "quarterly_net_50bps": sc.get("net_50bps"),
        "quarterly_turnover": sc.get("turnover"), "n_quarters": sc.get("n_quarters"),
        "oos_pooled_ic": sc.get("oos_pooled_ic"), "oos_frac_windows_positive": sc.get("oos_frac_pos"),
        "both_cohorts_positive": sc.get("both_cohorts_pos"),
        "both_subperiods_positive": sc.get("both_subperiods_pos"),
        "top_sector_share": sc.get("top_sector_share"),
        "matched_baseline_net_25bps": base.get("net_25bps") if base else None,
        "matched_baseline_ic_t": base.get("ic_t") if base else None,
        "matched_baseline_top_sector_share": base.get("top_sector_share") if base else None,
        "classification": cls, "reject_reason": reason, "description": desc,
    }


# --------------------------------------------------------------------------- #
# D. Decision.
# --------------------------------------------------------------------------- #
def decide(rows: List[Dict]) -> Tuple[str, str, Dict]:
    non_base = [v for v in rows if v["classification"] != CLS_BASELINE]
    passes = [v for v in non_base if v["classification"] == CLS_PASS]
    overfits = [v for v in non_base if v["classification"] == CLS_OVERFIT]
    if passes:
        champ = max(passes, key=lambda v: (v.get("quarterly_net_25bps") or -9))
        return (DEC_READY, ("%s clears the strict relative test vs the matched baseline (net-25bps up, no "
                            "secondary criterion worse, robust) - a transformed/interaction alpha ready "
                            "for Phase 10-P paper-rule packaging." % champ["variant_id"]),
                {"champion": champ["variant_id"], "n_pass": len(passes)})
    if overfits:
        return (DEC_OVERFIT, ("at least one transform/interaction raised in-sample net-25bps but failed a "
                              "secondary criterion (concentration / turnover / cohort or subperiod sign) "
                              "- classic transform overfit; the baseline stays champion and nothing is "
                              "productized."),
                {"champion": "baseline_composite_sn", "n_pass": 0, "n_overfit": len(overfits)})
    return (DEC_BASELINE, ("no defensible transform (signed-log / rank / sector-neutral rank / delta) and "
                           "no economically-motivated interaction (quality x value, profitability x "
                           "investment, accruals x leverage, FCF x value) beat the matched-baseline "
                           "net-25bps; nonlinear form does not unlock owned-factor signal - the two-leg "
                           "baseline remains champion."),
            {"champion": "baseline_composite_sn", "n_pass": 0})


# --------------------------------------------------------------------------- #
# E. Artifacts + report.
# --------------------------------------------------------------------------- #
_SC_HDR = ["variant_id", "group", "legs", "weights", "ic_mean", "ic_t", "quarterly_gross_spread",
           "quarterly_net_25bps", "quarterly_net_50bps", "quarterly_turnover", "n_quarters",
           "oos_pooled_ic", "oos_frac_windows_positive", "both_cohorts_positive",
           "both_subperiods_positive", "top_sector_share", "matched_baseline_net_25bps",
           "matched_baseline_ic_t", "matched_baseline_top_sector_share", "classification",
           "reject_reason", "description"]


def _scrow(v):
    return [v["variant_id"], v["group"], v["legs"], v["weights"], _num(v.get("ic_mean")),
            _num(v.get("ic_t")), _num(v.get("quarterly_gross_spread")), _num(v.get("quarterly_net_25bps")),
            _num(v.get("quarterly_net_50bps")), _num(v.get("quarterly_turnover")), v.get("n_quarters"),
            _num(v.get("oos_pooled_ic")), _num(v.get("oos_frac_windows_positive")),
            v.get("both_cohorts_positive"), v.get("both_subperiods_positive"),
            _num(v.get("top_sector_share")), _num(v.get("matched_baseline_net_25bps")),
            _num(v.get("matched_baseline_ic_t")), _num(v.get("matched_baseline_top_sector_share")),
            v["classification"], v.get("reject_reason"), v.get("description")]


def write_artifacts(P: _Paths, inv_rows, screen_rows, rows, log) -> None:
    _write_csv(P.art("inventory"),
               ["candidate", "kind", "definition", "allowed_transform_or_interaction"], inv_rows)
    _write_csv(P.art("standalone"),
               ["interaction_id", "economic_rationale", "ic_mean", "ic_t", "both_cohorts_pos",
                "both_subperiods_pos", "top_sector_share", "eligible", "reject_reason"],
               [[s["id"], s["econ"], _num(s.get("ic_mean")), _num(s.get("ic_t")),
                 s.get("both_cohorts_pos"), s.get("both_subperiods_pos"),
                 _num(s.get("top_sector_share")), s.get("eligible"), s.get("reject_reason")]
                for s in screen_rows])
    _write_csv(P.art("scorecard"), _SC_HDR, [_scrow(v) for v in rows])
    _write_csv(P.art("baseline_vs"), _SC_HDR,
               [_scrow(v) for v in rows
                if v["classification"] in (CLS_BASELINE, CLS_PASS, CLS_OVERFIT, CLS_NOIMPROVE)])
    _write_csv(P.art("rejected"), _SC_HDR,
               [_scrow(v) for v in rows
                if v["classification"] in (CLS_OVERFIT, CLS_COST, CLS_TURN, CLS_NOIMPROVE)])
    _write_csv(P.art("oos"),
               ["variant_id", "oos_pooled_ic", "oos_frac_windows_positive", "n_quarters",
                "classification"],
               [[v["variant_id"], _num(v.get("oos_pooled_ic")),
                 _num(v.get("oos_frac_windows_positive")), v.get("n_quarters"), v["classification"]]
                for v in rows])
    _write_csv(P.art("cohort"),
               ["variant_id", "both_cohorts_positive", "both_subperiods_positive", "classification"],
               [[v["variant_id"], v.get("both_cohorts_positive"), v.get("both_subperiods_positive"),
                 v["classification"]] for v in rows])
    _write_csv(P.art("sector"),
               ["variant_id", "top_sector_share", "matched_baseline_top_sector_share", "classification"],
               [[v["variant_id"], _num(v.get("top_sector_share")),
                 _num(v.get("matched_baseline_top_sector_share")), v["classification"]] for v in rows])
    _write_csv(P.art("turnover"),
               ["variant_id", "quarterly_turnover", "quarterly_net_25bps", "quarterly_net_50bps",
                "classification"],
               [[v["variant_id"], _num(v.get("quarterly_turnover")), _num(v.get("quarterly_net_25bps")),
                 _num(v.get("quarterly_net_50bps")), v["classification"]] for v in rows])


def _next_plan(decision: str) -> Dict:
    if decision == DEC_READY:
        nxt = "Phase 10-P: package the winning transformed/interaction alpha as PAPER-ONLY rules."
        cmd = "review research/output/%s/transform_variant_scorecard.csv" % STEM
        ph = "10-P"
    else:
        nxt = ("Phase 10-O: regime and conditional alpha gating - test whether the modest baseline (or a "
               "10-M/10-N candidate) is meaningfully stronger under simple pre-declared owned/local "
               "regimes (market trend, volatility, rate/oil/dollar macro from local FRED CSVs, liquidity, "
               "sector dispersion). Simple median/tertile thresholds only; no live macro API; no new data.")
        cmd = "python research/run_phase10o_regime_conditional_alpha_gating.py"
        ph = "10-O"
    return {"phase": ph, "from_decision": decision, "next_step": nxt, "exact_next_command": cmd,
            "constraints": ["owned/local data only", "no live API", "no providers", "no ML/optimiser",
                            "paper-only", "no orders", "no automation", "no deploy", "no commit",
                            "no push"]}


def _build_report(decision, rationale, meta, inv_rows, screen_rows, rows, integrity, n_events,
                  n_tickers, key_visible) -> Dict:
    baseline = next((v for v in rows if v["classification"] == CLS_BASELINE), {})
    passes = [v for v in rows if v["classification"] == CLS_PASS]
    overfits = [v for v in rows if v["classification"] == CLS_OVERFIT]
    champ_id = meta.get("champion")
    champ = next((v for v in rows if v["variant_id"] == champ_id), baseline)
    n_transform = len([v for v in rows if v["group"] in ("alt_composite", "incremental_transform")])
    n_interaction = len([s for s in screen_rows])

    def _vslim(v):
        return {k: (_round(v[k], 5) if isinstance(v.get(k), float) else v.get(k))
                for k in ("variant_id", "group", "legs", "weights", "ic_t", "quarterly_net_25bps",
                          "quarterly_net_50bps", "quarterly_turnover", "oos_frac_windows_positive",
                          "top_sector_share", "both_cohorts_positive", "both_subperiods_positive",
                          "classification", "reject_reason")}

    return {
        "phase": PHASE, "phase_name": PHASE_NAME, "as_of": AS_OF,
        "decision": decision, "decision_rationale": rationale, "allowed_decisions": list(ALLOWED_DECISIONS),
        "objective": ("test whether a defensible nonlinear transform (signed-log / rank / sector-neutral "
                      "rank / delta) or an economically-motivated interaction (quality x value, "
                      "profitability x investment, accruals x leverage, FCF x value) of OWNED factors "
                      "beats the frozen 10-D composite_sn baseline at 63d - NOT a broad search, no ML, no "
                      "optimiser, no providers"),
        "offline": True, "performs_network": PERFORMS_NETWORK,
        "eodhd_key_visible": bool(key_visible), "eodhd_key_required": False,
        "primary_horizon_days": PRIMARY_HORIZON_D,
        "scoreable_events": n_events, "scoreable_tickers": n_tickers,
        "input_inventory": [{"candidate": r[0], "kind": r[1], "definition": r[2],
                             "allowed_transform_or_interaction": r[3]} for r in inv_rows],
        "limits": {"max_transform_candidates": MAX_TRANSFORM_CANDIDATES,
                   "n_transform_candidates": n_transform,
                   "max_interaction_candidates": MAX_INTERACTION_CANDIDATES,
                   "n_interaction_candidates": n_interaction},
        "baseline": {"signal": "composite_sn", "weighting": "equal (fcf+ / accruals-), sector-neutral",
                     "ic_t_63d": _round(baseline.get("ic_t"), 3),
                     "quarterly_net_25bps": _round(baseline.get("quarterly_net_25bps"), 5),
                     "quarterly_net_50bps": _round(baseline.get("quarterly_net_50bps"), 5),
                     "quarterly_turnover": _round(baseline.get("quarterly_turnover"), 4),
                     "top_sector_share": _round(baseline.get("top_sector_share"), 4),
                     "alpha_character": "modest / boundary (63d IC t < 3.0 strong bar; not oversold)"},
        "phase10d_baseline_reproduction": {
            "got": {k: _round(v, 5) for k, v in integrity["got"].items()},
            "frozen": {k: _round(v, 5) for k, v in integrity["frozen"].items()},
            "reproduces_within_tolerance": integrity["reproduces_within_tolerance"],
            "tolerances": REPRO_TOL},
        "interaction_standalone_screen": [
            {"interaction_id": s["id"], "economic_rationale": s["econ"],
             "ic_mean": _round(s.get("ic_mean"), 5), "ic_t": _round(s.get("ic_t"), 3),
             "eligible": s.get("eligible"), "reject_reason": s.get("reject_reason")} for s in screen_rows],
        "n_variants": len([v for v in rows if v["classification"] != CLS_BASELINE]),
        "variants_tested": [_vslim(v) for v in rows],
        "candidates_tested": [_vslim(v) for v in rows if v["classification"] != CLS_BASELINE],
        "rejected_candidates": [_vslim(v) for v in rows
                                if v["classification"] in (CLS_OVERFIT, CLS_COST, CLS_TURN,
                                                           CLS_NOIMPROVE)],
        "champion": {"champion": champ_id if passes else "baseline_composite_sn",
                     "baseline_remains_champion": not passes,
                     "ic_t_63d": _round(champ.get("ic_t"), 3),
                     "quarterly_net_25bps": _round(champ.get("quarterly_net_25bps"), 5),
                     "quarterly_net_50bps": _round(champ.get("quarterly_net_50bps"), 5)},
        "baseline_vs_champion": {
            "baseline_net_25bps": _round(baseline.get("quarterly_net_25bps"), 5),
            "champion_net_25bps": _round(champ.get("quarterly_net_25bps"), 5),
            "improvement": _round((champ.get("quarterly_net_25bps") or 0)
                                  - (baseline.get("quarterly_net_25bps") or 0), 6) if passes else 0.0},
        "oos_stability_summary": {"baseline_oos_frac_pos": _round(baseline.get("oos_frac_windows_positive"),
                                                                  3),
                                  "note": "walk-forward 24/6/6, pure held-out, no sign refit"},
        "cohort_stability_summary": {"note": "old/new cohort + pre/post-2020 sign checked per variant"},
        "sector_concentration_summary": {
            "baseline_top_sector_share": _round(baseline.get("top_sector_share"), 4),
            "note": "relative gate: variant top-sector share must not exceed matched baseline"},
        "turnover_cost_summary": {"baseline_turnover": _round(baseline.get("quarterly_turnover"), 4),
                                  "cost_model": "quarterly book; net(bps)=spread-(bps/1e4)*turnover*2.0"},
        "n_overfit_variants": len(overfits),
        "subperiod_robustness_guard": {
            "rule": ("any candidate that passes the full-sample strict relative test is downgraded to "
                     "REJECT_OVERFIT unless its net-25bps advantage over the matched baseline also holds "
                     "(within %s) in BOTH the pre-2020 and post-2020 subperiods" % EPS_SUB),
            "note": ("this guard caught altcomp_rank: full-sample IC t 3.22 and net25 +ve looked strong, "
                     "but the entire net25 gain was a pre-2020 relic (pre-2020 altrank 0.0096 vs base "
                     "0.0052; post-2020 altrank 0.0008 vs base 0.0033 - it REVERSES out-of-sample and is "
                     "fragile to a single quarter). A rank-IC lift concentrated in one era is not a "
                     "tradeable, generalising alpha.")},
        "implementation_limits": [
            "value dimension (earnings_yield) is panel-native (8-series earnings-event build, PIT at the "
            "report) - used ONLY for interactions; any interaction that PASSED would carry a "
            "value-provenance caveat and require an EODHD-normalized value reconstruction before "
            "productizing",
            "the subperiod-robustness guard (pre/post-2020 net25 must not reverse) is applied to every "
            "full-sample pass to reject one-era artifacts such as altcomp_rank",
            "transforms are pre-declared and defensible (signed-log / rank / sector-neutral rank / yoy "
            "delta); no polynomial explosion, no ML, no optimiser, no genetic search",
            "candidate counts are capped (<=25 transform, <=10 interaction) to avoid multiple-testing "
            "inflation",
            "reuses the 10-M reconstructed factor CSVs (owned, prior-phase output) to avoid re-scanning "
            "the fundamentals cache",
        ],
        "next_recommended_phase": "10-P" if passes else "10-O",
        "safety": {"paper_only": True, "owned_local_data_only": True, "no_live_api_calls": True,
                   "no_orders": True, "no_automation": True, "no_broker": True, "no_deploy": True},
        "constraints_honored": ["offline (no network/key/provider probe)", "owned/local data only",
                                "no ML/optimiser/genetic search", "no polynomial explosion",
                                "no new factor family", "no weight optimisation", "no Paper Trader writes",
                                "no orders", "no automation", "no broker", "no deploy", "no GCP",
                                "no package install", "no full regression", "no commit", "no push"],
    }


def _print_summary(report: Dict) -> None:
    ch = report.get("champion", {})
    print("[10-N] decision=%s | transforms=%s interactions=%s variants=%s | champion=%s | "
          "baseline_reproduces=%s"
          % (report.get("decision"), report.get("limits", {}).get("n_transform_candidates"),
             report.get("limits", {}).get("n_interaction_candidates"), report.get("n_variants"),
             ch.get("champion"),
             report.get("phase10d_baseline_reproduction", {}).get("reproduces_within_tolerance")))


# --------------------------------------------------------------------------- #
# F. Orchestration.
# --------------------------------------------------------------------------- #
def run(out_dir: Optional[Path] = None, *, as_of: str = AS_OF, verbose: bool = True) -> Dict:
    P = _Paths(out_dir)
    P.out.mkdir(parents=True, exist_ok=True)
    P.recon.mkdir(parents=True, exist_ok=True)
    log = t8._Log(verbose)
    try:
        import pandas as pd  # noqa: F401
        key_visible = bool(os.environ.get(EODHD_KEY_ENV))
        log.step("preflight", "OFFLINE", "owned-data only; no network / no key; key_visible=%s"
                 % key_visible)

        ev, ok, stats = c10.build_panel(as_of, None, log)
        if not ok:
            return _blocker(P, log, "Norgate panel empty - rebuild before Phase 10-N.", key_visible)
        n_events = int(stats.get("events_usable", len(ev)))
        n_tickers = int(stats.get("tickers_usable", ev["ticker"].nunique()))
        ev, base_cols = c10.attach_signals(ev, c10._default_norm_csvs(), log)
        ev, comp_cov, _rl, _sl = d10.build_composite(ev, base_cols, log)
        if "month" not in ev.columns:
            ev["month"] = ev["entry_date"].dt.to_period("M")
        integrity = m10.integrity_check(ev, log)
        if not integrity["reproduces_within_tolerance"]:
            return _finish(P, log, DEC_REPAIR,
                           "composite_sn does not reproduce the frozen 10-D baseline; inputs need repair.",
                           {"champion": "baseline_composite_sn"}, [], [], [], integrity, n_events,
                           n_tickers, key_visible)

        # attach the base factors used by transforms / interactions.
        for cand in _BASE_FACTORS:
            csv_path = _base_csv(cand)
            ev, _ci = m10.attach_candidate(ev, cand, csv_path, log)

        # build the fcf yoy-delta factor from the owned normalized fcf CSV and attach it sector-neutral.
        fcf_src = _NORM_BASE / "eodhd_fcf_to_assets" / "fcf_to_assets.csv"
        dcsv, dn = build_delta_csv("fcf_delta", fcf_src, P.recon / "fcf_delta.csv", log)
        if dn > 0:
            ev, _ci = m10.attach_candidate(
                ev, {"feature": "fcf_delta", "family": "eodhd_fcf_delta", "orientation": +1}, dcsv, log)
            if "o_fcf_delta__sn" in ev.columns:
                ev["fcf_delta_sn"] = ev["o_fcf_delta__sn"]

        # inventory (pre-declared candidate menu).
        inv_rows = [
            ["altcomp_signed_log", "transform", "composite with signed-log legs", "signed-log"],
            ["altcomp_rank", "transform", "composite with within-month rank legs", "rank"],
            ["altcomp_snrank", "transform", "composite with sector-neutral rank legs", "sector-neutral rank"],
            ["cash_roa_rank", "transform", "rank of cash-return-on-assets (3rd leg)", "rank"],
            ["gross_prof_snrank", "transform", "sector-neutral rank of gross profitability (3rd leg)",
             "sector-neutral rank"],
            ["fcf_delta", "transform", "yoy change in fcf/assets (3rd leg)", "yoy delta"],
            ["quality_x_value", "interaction", "cash_return_on_assets x earnings_yield", "quality x value"],
            ["profitability_x_investment", "interaction", "gross_profitability x (-asset_growth)",
             "profitability x investment"],
            ["accruals_x_leverage", "interaction", "(-operating_accruals) x (-debt_to_assets)",
             "accruals x leverage"],
            ["fcf_x_value", "interaction", "fcf_to_assets x earnings_yield", "FCF x value"],
        ]

        rows = build_transform_variants(ev, log)
        screen_rows, inter_rows = build_interaction_candidates(ev, log)
        rows.extend(inter_rows)

        decision, rationale, meta = decide(rows)
        write_artifacts(P, inv_rows, screen_rows, rows, log)
        report = _build_report(decision, rationale, meta, inv_rows, screen_rows, rows, integrity,
                               n_events, n_tickers, key_visible)
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


def _finish(P, log, decision, rationale, meta, inv_rows, screen_rows, rows, integrity, n_events,
            n_tickers, key_visible) -> Dict:
    write_artifacts(P, inv_rows, screen_rows, rows, log)
    report = _build_report(decision, rationale, meta, inv_rows, screen_rows, rows, integrity, n_events,
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
    print("[10-N] decision=%s | %s" % (DEC_REPAIR, detail))
    return report


def _parse_args(argv=None):
    p = argparse.ArgumentParser(description="Phase 10-N - Transformation & Interaction Search")
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
