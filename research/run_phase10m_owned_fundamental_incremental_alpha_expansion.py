"""Phase 10-M - Owned Fundamental Incremental Alpha Expansion.

WHY THIS PHASE EXISTS
    Phase 10-L-B exhausted the *two-factor reweighting* path: no reweight / z-cap / winsorize / liquidity
    / sector-cap of the frozen composite_sn (long fcf_to_assets, short operating_accruals, equal-weight,
    sector-neutral, 63d) beat the baseline on honest evidence (REJECT_REWEIGHTING_OVERFIT). The natural,
    NOT-yet-tested question is whether a THIRD owned fundamental factor - a profitability, investment, or
    leverage-risk leg - adds *incremental* alpha beyond the two-leg baseline. This is exactly what the
    10-C / 10-D "next plan" flagged: "widen the owned-EODHD quality family set into the composite
    (gross_profitability, asset_growth, net_share_issuance) at the quarterly horizon".

    Phase 10-M does NOT re-test two-factor reweighting (exhausted). It does NOT add providers, does NOT
    make live API calls, does NOT run a broad alpha search, does NOT optimise weights, and does NOT
    invent fields. Every candidate leg is an OWNED EODHD fundamental factor, built PIT-safe from the
    SAME filing-date line items the baseline legs use (b10._fund_quarters), and scored with the EXACT
    10-D engine (c10._eval, d10.quarterly_backtest, d10.walk_forward_h) so it is directly comparable.

CANDIDATE FACTORS (8; pre-declared; a-priori orientation from documented anomaly; NOT tuned)
    profitability / quality:
      gross_profitability   (+1)  gross profit / assets            [Novy-Marx; already normalized 10-B]
      return_on_assets      (+1)  net income / assets              [reconstructed from same line items]
      operating_margin      (+1)  operating income / revenue       [reconstructed]
      cash_return_on_assets (+1)  operating cash flow / assets     [reconstructed; cash quality]
    investment / capital discipline:
      asset_growth          (-1)  yoy total-asset growth           [Cooper-Gulen-Schill; normalized 10-B]
      net_share_issuance    (-1)  yoy shares-outstanding growth    [issuance anomaly; normalized 10-B]
    balance-sheet / leverage risk:
      leverage_change       (-1)  change in debt/assets            [normalized 10-B]
      debt_to_assets        (-1)  total debt / assets (LEVEL)      [reconstructed; low-leverage = safer]

    Orientations follow standard quality-factor convention (profitable +, investing/issuing/levering -)
    and are fixed BEFORE the run - never flipped to fit the data. A wrong-signed factor simply fails the
    standalone screen (which is the honest outcome).

METHOD
    1. Build the Norgate survivorship-free panel + attach the baseline legs (c10) and rebuild the frozen
       composite_sn (d10.build_composite). Integrity guard: composite_sn must reproduce the frozen 10-D
       baseline within tolerance, else NEEDS_FACTOR_INPUT_REPAIR (no scoring).
    2. Reconstruct the 4 non-normalized factors from b10._fund_quarters (filing-date PIT), attach all 8
       candidates via y8.attach_orthogonal_feature (as-of available_date <= entry_date, no lookahead).
    3. STANDALONE SCREEN (reject weak before composite testing): a factor is eligible only if, at 63d
       oriented, mean IC is positive AND both cohorts (old/new) are oriented-positive AND both subperiods
       (pre/post-2020) are oriented-positive. This rejects wrong-signed / sign-unstable factors; it does
       NOT require strength (a modest but directionally-robust leg can still diversify a composite - the
       RELATIVE beat test below is the real arbiter).
    4. COMPOSITE TEST: for each eligible factor, blend baseline + factor at the ALLOWED weights only
       (baseline 70/new 30, 60/40, 50/50) in within-month z space; plus two-factor blends
       (baseline 60 / A 20 / B 20 and baseline 50 / A 25 / B 25) for the top-2 eligible factors by
       standalone IC t. Score each with the 10-D engine on the factor's common support, against a
       MATCHED baseline (composite_sn on the identical rows) so coverage never confounds the comparison.
    5. STRICT RELATIVE CHAMPION TEST (same discipline as 10-L-B): a variant beats the matched baseline
       only if net-25bps strictly up, net-50bps not worse, turnover not materially worse (<=1.10x; hard
       reject >1.50x), IC t >= base - 0.10, OOS frac-positive >= base, top-sector share <= base, AND
       robust (both cohorts +, both subperiods +). Any in-sample net25 gain that fails a secondary
       criterion is REJECT_INCREMENTAL_OVERFIT, not a champion.

TERMINAL DECISIONS (allowed)
    INCREMENTAL_ALPHA_READY_FOR_PAPER_RULES | BASELINE_REMAINS_CHAMPION | REJECT_INCREMENTAL_OVERFIT |
    NEEDS_FACTOR_INPUT_REPAIR | NEEDS_MORE_OWNED_DATA

CONSTRAINTS HONORED
    Fully offline (no network / key / provider probe / build over live data); owned/local data only
    (Norgate panel + owned EODHD fundamentals cache); no new factor family beyond owned fundamentals; no
    broad alpha search; no weight optimisation; no Paper Trader writes; no orders; no automation; no
    broker; no deploy; no GCP; no package install; targeted tests only; keys never printed/written;
    output is research metadata only. No commit. No push.
"""
from __future__ import annotations

import argparse
import glob
import json
import math
import os
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from research import run_phase10d_quarterly_quality_composite_validation as d10   # noqa: E402
from research import run_phase10c_eodhd_quality_oos_validation as c10             # noqa: E402
from research import run_phase10b_eodhd_norgate_exhaustive_alpha_factory as b10   # noqa: E402
from research import run_phase8x_autonomous_strong_alpha_discovery as x8          # noqa: E402
from research import run_phase8y_orthogonal_data_family_acquisition as y8         # noqa: E402

s8 = x8.s8
t8 = x8.t8
_write_json = s8._write_json
_write_csv = s8._write_csv
_read_csv_file = s8._read_csv_file
_read_json = s8._read_json
_round = s8._round
_rel = s8._rel
_finite = c10._finite
_num = c10._num

PHASE = "10-M"
PHASE_NAME = "Owned Fundamental Incremental Alpha Expansion"
STEM = "phase10m_owned_fundamental_incremental_alpha_expansion"
PERFORMS_NETWORK = False

AS_OF = c10.AS_OF
PRIMARY_HORIZON_D = d10.PRIMARY_HORIZON_D            # 63 (quarterly) - the decision horizon
RET_PRIMARY = d10.RET_PRIMARY                        # "fwd_exc_63"
WF_TRAIN = d10.WF_TRAIN_MONTHS                       # 24
WF_TEST = d10.WF_TEST_MONTHS                         # 6
WF_STEP = d10.WF_STEP_MONTHS                         # 6
_PRE2020 = "2020-01-01"
EODHD_KEY_ENV = "EODHD_API_KEY"

_NORM_BASE = _REPO_ROOT / "research" / "data" / "eodhd" / "normalized"
_FUND_RAW = _REPO_ROOT / "research" / "data" / "eodhd" / "raw" / "fundamentals"
_PHASE10D_JSON = (_REPO_ROOT / "research" / "output"
                  / "phase10d_quarterly_quality_composite_validation"
                  / "phase10d_quarterly_quality_composite_validation.json")

# Baseline composite_sn columns produced by d10.build_composite.
_COMP_SN = d10.COMP_SN                               # "comp_sn"

# --------------------------------------------------------------------------- #
# Candidate factor registry (pre-declared, owned EODHD, a-priori orientation).
#   norm    -> read the already-normalized 10-B CSV verbatim
#   recon   -> reconstruct from b10._fund_quarters filing-date line items (ratio of the SAME items the
#              baseline legs use); write a normalized (ticker, available_date, value) CSV to this phase's
#              own data dir, then attach with the identical PIT machinery.
# --------------------------------------------------------------------------- #
CANDIDATES: Tuple[Dict, ...] = (
    {"feature": "gross_profitability", "family": "eodhd_gross_profitability", "orientation": +1,
     "group": "profitability", "source": "norm",
     "anomaly": "Novy-Marx gross profitability: gross profit / assets, higher -> higher forward return"},
    {"feature": "return_on_assets", "family": "eodhd_return_on_assets", "orientation": +1,
     "group": "profitability", "source": "recon", "recon": "net_income/total_assets",
     "anomaly": "profitability: net income / assets, higher -> higher forward return"},
    {"feature": "operating_margin", "family": "eodhd_operating_margin", "orientation": +1,
     "group": "profitability", "source": "recon", "recon": "operating_income/total_revenue",
     "anomaly": "profitability: operating income / revenue, higher -> higher forward return"},
    {"feature": "cash_return_on_assets", "family": "eodhd_cash_return_on_assets", "orientation": +1,
     "group": "profitability", "source": "recon", "recon": "cfo/total_assets",
     "anomaly": "cash profitability: operating cash flow / assets, higher -> higher forward return"},
    {"feature": "asset_growth", "family": "eodhd_asset_growth", "orientation": -1,
     "group": "investment", "source": "norm",
     "anomaly": "asset-growth (Cooper-Gulen-Schill): higher investment -> lower forward return"},
    {"feature": "net_share_issuance", "family": "eodhd_net_share_issuance", "orientation": -1,
     "group": "investment", "source": "norm",
     "anomaly": "net issuance: higher dilution -> lower forward return (buybacks positive)"},
    {"feature": "leverage_change", "family": "eodhd_leverage_change", "orientation": -1,
     "group": "leverage", "source": "norm",
     "anomaly": "re-leveraging risk: rising debt/assets -> lower quality / lower forward return"},
    {"feature": "debt_to_assets", "family": "eodhd_debt_to_assets", "orientation": -1,
     "group": "leverage", "source": "recon", "recon": "total_debt/total_assets",
     "anomaly": "leverage level: higher debt/assets -> lower safety (QMJ) -> lower forward return"},
)
_BASELINE_FEATURES = ("fcf_to_assets", "operating_accruals")

# Allowed baseline / new-factor weight pairs (single new factor). No free optimisation.
SINGLE_WEIGHTS: Tuple[Tuple[float, float], ...] = ((0.7, 0.3), (0.6, 0.4), (0.5, 0.5))
# Allowed baseline / A / B triples (two new factors).
DOUBLE_WEIGHTS: Tuple[Tuple[float, float, float], ...] = ((0.6, 0.2, 0.2), (0.5, 0.25, 0.25))

# Champion margins (identical discipline to 10-L-B).
EPS_NET = 1e-6
TURN_OK_MULT = 1.10
TURN_HARD_MULT = 1.50
IC_T_MARGIN = 0.10
HIT_MARGIN = 0.05
SHARE_EPS = 1e-9

# Integrity tolerances vs frozen 10-D baseline.
REPRO_TOL = {"ic_t": 0.25, "net_25bps": 0.0015, "net_50bps": 0.0015, "turnover": 0.10}

# Decisions.
DEC_READY = "INCREMENTAL_ALPHA_READY_FOR_PAPER_RULES"
DEC_BASELINE = "BASELINE_REMAINS_CHAMPION"
DEC_OVERFIT = "REJECT_INCREMENTAL_OVERFIT"
DEC_REPAIR = "NEEDS_FACTOR_INPUT_REPAIR"
DEC_MORE_DATA = "NEEDS_MORE_OWNED_DATA"
ALLOWED_DECISIONS = (DEC_READY, DEC_BASELINE, DEC_OVERFIT, DEC_REPAIR, DEC_MORE_DATA)

# Variant classifications.
CLS_BASELINE = "BASELINE"
CLS_PASS = "PASS_STRICT"
CLS_OVERFIT = "REJECT_OVERFIT"
CLS_COST = "REJECT_COST_KILLED"
CLS_TURN = "REJECT_TURNOVER"
CLS_NOIMPROVE = "NO_IMPROVEMENT"

_ARTIFACTS = {
    "report": "%s.json" % STEM,
    "inventory": "factor_input_inventory.csv",
    "standalone": "standalone_factor_screen.csv",
    "scorecard": "composite_variant_scorecard.csv",
    "baseline_vs": "baseline_vs_variants.csv",
    "oos": "oos_stability_report.csv",
    "cohort": "cohort_stability_report.csv",
    "sector": "sector_concentration_report.csv",
    "turnover": "turnover_cost_report.csv",
    "rejected": "rejected_candidates.csv",
    "next_plan": "phase10n_next_plan.json",
}


class _Paths:
    def __init__(self, out_dir=None):
        self.out = Path(out_dir) if out_dir else (_REPO_ROOT / "research" / "output" / STEM)
        self.recon = self.out / "reconstructed_factors"

    def art(self, key: str) -> Path:
        return self.out / _ARTIFACTS[key]


# --------------------------------------------------------------------------- #
# A. Factor reconstruction (owned raw fundamentals; same PIT filing-date line items as the baseline).
# --------------------------------------------------------------------------- #
def _recon_value(spec: str, r: Dict) -> Optional[float]:
    ta = r.get("total_assets")
    tr = r.get("total_revenue")
    if spec == "net_income/total_assets":
        ni = r.get("net_income")
        return (ni / ta) if (ni is not None and ta) else None
    if spec == "operating_income/total_revenue":
        oi = r.get("operating_income")
        return (oi / tr) if (oi is not None and tr and tr > 0) else None
    if spec == "cfo/total_assets":
        cfo = r.get("cfo")
        return (cfo / ta) if (cfo is not None and ta) else None
    if spec == "total_debt/total_assets":
        td = r.get("total_debt")
        return (td / ta) if (td is not None and ta) else None
    return None


def reconstruct_factor(cand: Dict, P: _Paths, as_of: str, log) -> Tuple[Path, int, int]:
    """Build a normalized (ticker, available_date, <feature>) CSV for one reconstructed candidate from
    the OWNED fundamentals cache, using b10._fund_quarters (filing-date PIT) and a ratio of the same
    line items the baseline legs use. Returns (csv_path, n_rows, n_tickers)."""
    import pandas as pd  # noqa: F401
    feat = cand["feature"]
    spec = cand["recon"]
    P.recon.mkdir(parents=True, exist_ok=True)
    out_csv = P.recon / ("%s.csv" % feat)
    rows: List[List] = []
    tickers = set()
    for jf in sorted(glob.glob(str(_FUND_RAW / "*.json"))):
        tk = Path(jf).stem.upper()
        try:
            with open(jf, "r", encoding="utf-8") as fh:
                payload = json.load(fh)
        except Exception:
            continue
        for r in b10._fund_quarters(payload):
            ad = r.get("available_date")
            if not ad or ad > as_of:                       # drop future-leak rows (as normalize does)
                continue
            val = _recon_value(spec, r)
            if val is None or not _finite(val):
                continue
            rows.append([tk, ad, val])
            tickers.add(tk)
    rows.sort(key=lambda x: (x[0], x[1]))
    _write_csv(out_csv, ["ticker", "available_date", feat], rows)
    log.step("reconstruct", "DONE", "%s (%s): %d rows / %d tickers" % (feat, spec, len(rows), len(tickers)))
    return out_csv, len(rows), len(tickers)


def _norm_csv_path(cand: Dict, P: _Paths) -> Path:
    if cand["source"] == "recon":
        return P.recon / ("%s.csv" % cand["feature"])
    return _NORM_BASE / cand["family"] / ("%s.csv" % cand["feature"])


# --------------------------------------------------------------------------- #
# B. Attach candidate legs PIT-safe + build oriented + within-month sector-neutral z leg.
# --------------------------------------------------------------------------- #
def attach_candidate(ev, cand: Dict, csv_path: Path, log) -> Tuple[object, Dict]:
    """As-of attach the base level, then build o_<feat> (orientation*level) and o_<feat>__sn
    (within month x sector de-mean of the oriented level), mirroring c10.attach_signals exactly."""
    feat = cand["feature"]
    have_sector = "sector" in ev.columns
    if not Path(csv_path).is_file():
        return ev, {"raw": None, "oriented": None, "sn": None, "coverage": 0, "tickers": 0}
    ev, cov, _added = y8.attach_orthogonal_feature(ev, {"feature": feat}, csv_path, log)
    ocol = "o_%s" % feat
    sncol = "o_%s__sn" % feat
    ev[ocol] = cand["orientation"] * ev[feat]
    if have_sector:
        ev[sncol] = ev.groupby(["month", "sector"])[ocol].transform(lambda s: s - s.mean())
    else:
        ev[sncol] = x8._within_month_z(ev, ocol)
    n_tk = int(ev.loc[ev[feat].notna(), "ticker"].nunique()) if feat in ev.columns else 0
    return ev, {"raw": feat, "oriented": ocol, "sn": sncol, "coverage": int(cov), "tickers": n_tk}


# --------------------------------------------------------------------------- #
# C. Scoring (single source of truth: the 10-D engine, 63d).
# --------------------------------------------------------------------------- #
def _slice_pos(ev, col: str) -> bool:
    m = c10._eval(ev, col, PRIMARY_HORIZON_D, False)
    return c10._pos(m)


def score_signal(ev, sigcol: str) -> Dict:
    """Full 63d battery for one signal column, using the exact 10-D engine functions."""
    import pandas as pd
    ic = c10._eval(ev, sigcol, PRIMARY_HORIZON_D, False)
    q = d10.quarterly_backtest(ev, sigcol, RET_PRIMARY)
    wf = d10.walk_forward_h(ev, sigcol, RET_PRIMARY, WF_TRAIN, WF_TEST, WF_STEP)
    old = c10._eval(ev[ev["cohort"] == "old"], sigcol, PRIMARY_HORIZON_D, False) \
        if "cohort" in ev.columns else ic
    new = c10._eval(ev[ev["cohort"] == "new"], sigcol, PRIMARY_HORIZON_D, False) \
        if "cohort" in ev.columns else ic
    pre = c10._eval(ev[ev["entry_date"] < pd.Timestamp(_PRE2020)], sigcol, PRIMARY_HORIZON_D, False)
    post = c10._eval(ev[ev["entry_date"] >= pd.Timestamp(_PRE2020)], sigcol, PRIMARY_HORIZON_D, False)
    return {
        "ic_mean": ic.get("mean_ic"), "ic_t": ic.get("ic_t"),
        "top_sector_share": ic.get("top_sector_share"),
        "gross_spread": q.get("mean_spread"), "spread_t": q.get("spread_t"),
        "net_25bps": q.get("net_25bps"), "net_50bps": q.get("net_50bps"),
        "turnover": q.get("avg_turnover"), "n_quarters": q.get("n_quarters"),
        "spread_hit_rate": q.get("spread_hit_rate"),
        "oos_pooled_ic": wf.get("pooled_oos_ic"), "oos_frac_pos": wf.get("frac_windows_positive"),
        "old_ic": old.get("mean_ic"), "new_ic": new.get("mean_ic"),
        "pre_ic": pre.get("mean_ic"), "post_ic": post.get("mean_ic"),
        "both_cohorts_pos": c10._pos(old) and c10._pos(new),
        "both_subperiods_pos": c10._pos(pre) and c10._pos(post),
    }


# --------------------------------------------------------------------------- #
# D. Standalone screen (reject weak / wrong-signed / sign-unstable before composite testing).
# --------------------------------------------------------------------------- #
def standalone_screen(ev, cand: Dict, colinfo: Dict, log) -> Dict:
    import pandas as pd
    ocol = colinfo.get("oriented")
    feat = cand["feature"]
    res = {"feature": feat, "group": cand["group"], "orientation": cand["orientation"],
           "source": cand["source"], "coverage": colinfo.get("coverage", 0),
           "tickers": colinfo.get("tickers", 0), "eligible": False, "reject_reason": ""}
    if ocol is None or ocol not in ev.columns or colinfo.get("coverage", 0) == 0:
        res["reject_reason"] = "no panel coverage (factor could not be attached)"
        return res
    sub = ev[ev[ocol].notna()]
    ic = c10._eval(sub, ocol, PRIMARY_HORIZON_D, False)
    old = c10._eval(sub[sub["cohort"] == "old"], ocol, PRIMARY_HORIZON_D, False) \
        if "cohort" in sub.columns else ic
    new = c10._eval(sub[sub["cohort"] == "new"], ocol, PRIMARY_HORIZON_D, False) \
        if "cohort" in sub.columns else ic
    pre = c10._eval(sub[sub["entry_date"] < pd.Timestamp(_PRE2020)], ocol, PRIMARY_HORIZON_D, False)
    post = c10._eval(sub[sub["entry_date"] >= pd.Timestamp(_PRE2020)], ocol, PRIMARY_HORIZON_D, False)
    res.update({
        "ic_mean": ic.get("mean_ic"), "ic_t": ic.get("ic_t"),
        "top_sector_share": ic.get("top_sector_share"),
        "old_ic": old.get("mean_ic"), "new_ic": new.get("mean_ic"),
        "pre_ic": pre.get("mean_ic"), "post_ic": post.get("mean_ic"),
        "both_cohorts_pos": c10._pos(old) and c10._pos(new),
        "both_subperiods_pos": c10._pos(pre) and c10._pos(post),
    })
    if not c10._pos(ic):
        res["reject_reason"] = ("oriented 63d IC not positive (wrong-signed vs the a-priori anomaly): "
                                "mean_ic=%s" % _num(ic.get("mean_ic")))
        return res
    if not res["both_cohorts_pos"]:
        res["reject_reason"] = "oriented IC sign disagrees across old/new cohorts (sign-unstable)"
        return res
    if not res["both_subperiods_pos"]:
        res["reject_reason"] = "oriented IC sign disagrees across pre/post-2020 (sign-unstable)"
        return res
    res["eligible"] = True
    log.step("standalone", "ELIGIBLE", "%s: 63d IC=%s t=%s (cohorts+ subperiods+)"
             % (feat, _num(ic.get("mean_ic")), _num(ic.get("ic_t"))))
    return res


# --------------------------------------------------------------------------- #
# E. Composite construction + scoring on common support with a matched baseline.
# --------------------------------------------------------------------------- #
def _blend(ev_sub, base_col: str, leg_cols: Sequence[str], weights: Sequence[float]):
    """weight[0]*z(base) + sum(weight[i]*z(leg_i)) in within-month z space."""
    import numpy as np
    out = weights[0] * x8._within_month_z(ev_sub, base_col)
    for w, lc in zip(weights[1:], leg_cols):
        out = out + w * x8._within_month_z(ev_sub, lc)
    return out.to_numpy() if hasattr(out, "to_numpy") else np.asarray(out)


def classify(variant: Dict, base: Dict) -> Tuple[str, str]:
    """RELATIVE beat test vs the MATCHED baseline (same rows). Identical discipline to 10-L-B."""
    bnet25 = base.get("net_25bps")
    vnet25 = variant.get("net_25bps")
    if not (_finite(vnet25) and vnet25 > 0):
        return CLS_COST, "quarterly net-25bps <= 0 (cost-killed)"
    if not (_finite(vnet25) and _finite(bnet25) and vnet25 > bnet25 + EPS_NET):
        return CLS_NOIMPROVE, ("does not beat matched-baseline net-25bps %s (variant %s)"
                               % (_num(bnet25), _num(vnet25)))
    bturn, vturn = base.get("turnover"), variant.get("turnover")
    if _finite(bturn) and _finite(vturn) and vturn > bturn * TURN_HARD_MULT:
        return CLS_TURN, "turnover materially worse (>1.50x baseline)"
    reasons = []
    if _finite(bturn) and _finite(vturn) and vturn > bturn * TURN_OK_MULT:
        reasons.append("turnover worsens (>1.10x baseline)")
    vnet50, bnet50 = variant.get("net_50bps"), base.get("net_50bps")
    if _finite(vnet50) and _finite(bnet50) and vnet50 < bnet50 - EPS_NET:
        reasons.append("net-50bps worsens")
    vict, bict = variant.get("ic_t"), base.get("ic_t")
    if _finite(vict) and _finite(bict) and vict < bict - IC_T_MARGIN:
        reasons.append("IC t-stat materially worse")
    vfrac, bfrac = variant.get("oos_frac_pos"), base.get("oos_frac_pos")
    if _finite(vfrac) and _finite(bfrac) and vfrac < bfrac - HIT_MARGIN:
        reasons.append("OOS frac-windows-positive deteriorates")
    vshare, bshare = variant.get("top_sector_share"), base.get("top_sector_share")
    if _finite(vshare) and _finite(bshare) and vshare > bshare + SHARE_EPS:
        reasons.append("sector concentration worsens")
    if not variant.get("both_cohorts_pos"):
        reasons.append("a cohort turns negative")
    if not variant.get("both_subperiods_pos"):
        reasons.append("a subperiod turns negative")
    if reasons:
        return CLS_OVERFIT, ("net-25bps improves in-sample but fails the strict test: %s"
                             % "; ".join(reasons))
    return CLS_PASS, "beats matched baseline on net-25bps and worsens no secondary criterion"


def _variant_row(vid, group, legs, weights, base, sc, cls, reason, desc):
    return {
        "variant_id": vid, "group": group, "legs": "+".join(legs), "weights": "/".join(
            str(int(round(w * 100))) for w in weights),
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


def build_and_score_variants(ev, cols: Dict[str, Dict], eligible: List[Dict], log) -> List[Dict]:
    """For each eligible factor, score the matched baseline + the allowed blended variants on the
    factor's common support. Then two-factor blends for the top-2 eligible factors."""
    import numpy as np
    rows: List[Dict] = []
    # global baseline (full comp_sn support) - reference row.
    gsc = score_signal(ev[ev[_COMP_SN].notna()], _COMP_SN)
    rows.append(_variant_row("baseline_composite_sn", "baseline", ["composite_sn"], (1.0,), None, gsc,
                             CLS_BASELINE, "frozen 10-D equal-weight sector-neutral composite (fcf+/acc-)",
                             "baseline composite_sn on full support (reference)"))

    scored_by_feat: Dict[str, Dict] = {}
    for cand in eligible:
        feat = cand["feature"]
        sncol = cols[cand["family"]]["sn"]
        sub = ev[ev[_COMP_SN].notna() & ev[feat].notna()].copy()
        if sub.empty:
            continue
        base_sc = score_signal(sub, _COMP_SN)                       # matched baseline (same rows)
        scored_by_feat[feat] = {"cand": cand, "sncol": sncol, "sub_n": int(len(sub)),
                                "base": base_sc, "ic_t_standalone": cand.get("_screen_ic_t")}
        for (wb, wn) in SINGLE_WEIGHTS:
            colname = "_blend_%s_%d_%d" % (feat, int(wb * 100), int(wn * 100))
            sub[colname] = _blend(sub, _COMP_SN, [sncol], [wb, wn])
            sc = score_signal(sub, colname)
            cls, reason = classify(sc, base_sc)
            vid = "base%02d_%s_%02d" % (int(wb * 100), feat, int(wn * 100))
            rows.append(_variant_row(vid, "single:%s" % cand["group"], ["composite_sn", feat],
                                     (wb, wn), base_sc, sc, cls, reason,
                                     "baseline %d%% / %s %d%% (sector-neutral z blend)"
                                     % (int(wb * 100), feat, int(wn * 100))))
            log.step("variant", cls, "%s | net25=%s (base %s) | ic_t=%s | share=%s"
                     % (vid, _num(sc.get("net_25bps")), _num(base_sc.get("net_25bps")),
                        _num(sc.get("ic_t")), _num(sc.get("top_sector_share"))))

    # two-factor blends for the top-2 eligible factors by standalone IC t.
    ranked = sorted(eligible, key=lambda c: (c.get("_screen_ic_t") if _finite(c.get("_screen_ic_t"))
                                             else -1e9), reverse=True)
    if len(ranked) >= 2:
        a, b = ranked[0], ranked[1]
        fa, fb = a["feature"], b["feature"]
        sna, snb = cols[a["family"]]["sn"], cols[b["family"]]["sn"]
        sub = ev[ev[_COMP_SN].notna() & ev[fa].notna() & ev[fb].notna()].copy()
        if not sub.empty:
            base_sc = score_signal(sub, _COMP_SN)
            for (wb, wa, wbb) in DOUBLE_WEIGHTS:
                colname = "_blend2_%d" % int(wb * 100)
                sub[colname] = _blend(sub, _COMP_SN, [sna, snb], [wb, wa, wbb])
                sc = score_signal(sub, colname)
                cls, reason = classify(sc, base_sc)
                vid = "base%02d_%s%02d_%s%02d" % (int(wb * 100), fa, int(wa * 100), fb, int(wbb * 100))
                rows.append(_variant_row(vid, "double", ["composite_sn", fa, fb], (wb, wa, wbb),
                                         base_sc, sc, cls, reason,
                                         "baseline %d%% / %s %d%% / %s %d%% (top-2 eligible factors)"
                                         % (int(wb * 100), fa, int(wa * 100), fb, int(wbb * 100))))
                log.step("variant", cls, "%s | net25=%s (base %s)"
                         % (vid, _num(sc.get("net_25bps")), _num(base_sc.get("net_25bps"))))
    return rows


# --------------------------------------------------------------------------- #
# F. Integrity guard vs frozen 10-D baseline.
# --------------------------------------------------------------------------- #
def integrity_check(ev, log) -> Dict:
    """The freshly built composite_sn must reproduce the frozen 10-D baseline within tolerance."""
    sub = ev[ev[_COMP_SN].notna()]
    ic = c10._eval(sub, _COMP_SN, PRIMARY_HORIZON_D, False)
    q = d10.quarterly_backtest(sub, _COMP_SN, RET_PRIMARY)
    got = {"ic_t": ic.get("ic_t"), "net_25bps": q.get("net_25bps"),
           "net_50bps": q.get("net_50bps"), "turnover": q.get("avg_turnover")}
    frozen = {}
    try:
        j = _read_json(_PHASE10D_JSON)
        for r in j.get("signal_results", []):
            if r.get("signal") == "composite_sn":
                frozen = {"ic_t": r.get("ic_t_63d"), "net_25bps": r.get("quarterly_net_25bps"),
                          "net_50bps": r.get("quarterly_net_50bps"),
                          "turnover": r.get("quarterly_turnover")}
                break
    except Exception:
        frozen = {}
    within = True
    deltas = {}
    if frozen:
        for k, tol in REPRO_TOL.items():
            g, f = got.get(k), frozen.get(k)
            if _finite(g) and _finite(f):
                deltas[k] = abs(g - f)
                if abs(g - f) > tol:
                    within = False
            else:
                within = False
    else:
        within = False
    log.step("integrity", "DONE" if within else "CHECK",
             "composite_sn 63d IC t=%s net25=%s net50=%s turnover=%s | reproduces_10D=%s"
             % (_num(got.get("ic_t")), _num(got.get("net_25bps")), _num(got.get("net_50bps")),
                _num(got.get("turnover")), within))
    return {"got": got, "frozen": frozen, "deltas": deltas, "reproduces_within_tolerance": bool(within)}


# --------------------------------------------------------------------------- #
# G. Decision.
# --------------------------------------------------------------------------- #
def decide(variant_rows: List[Dict], eligible: List[Dict], n_candidates: int) -> Tuple[str, str, Dict]:
    non_baseline = [v for v in variant_rows if v["classification"] != CLS_BASELINE]
    passes = [v for v in non_baseline if v["classification"] == CLS_PASS]
    overfits = [v for v in non_baseline if v["classification"] == CLS_OVERFIT]
    baseline = next((v for v in variant_rows if v["classification"] == CLS_BASELINE), {})
    if passes:
        champ = max(passes, key=lambda v: (v.get("quarterly_net_25bps") or -9))
        return (DEC_READY, ("%s clears the strict RELATIVE incremental test vs the matched baseline "
                            "(net-25bps up, no secondary criterion worse, robust across cohorts and "
                            "subperiods) - ready for Phase 10-P paper-rule packaging."
                            % champ["variant_id"]),
                {"champion": champ["variant_id"], "n_pass": len(passes)})
    if not eligible:
        return (DEC_BASELINE, ("no owned fundamental candidate passed the standalone directional screen "
                               "(all %d were wrong-signed vs their a-priori anomaly or sign-unstable "
                               "across cohorts/subperiods), so none was eligible for composite testing; "
                               "the two-leg baseline remains champion." % n_candidates),
                {"champion": "baseline_composite_sn", "n_pass": 0})
    if overfits:
        return (DEC_OVERFIT, ("at least one blended variant raised in-sample net-25bps but failed the "
                              "strict test (worse concentration / turnover / cohort or subperiod sign) - "
                              "classic incremental overfit; the baseline stays champion and no widened "
                              "composite is productized."),
                {"champion": "baseline_composite_sn", "n_pass": 0, "n_overfit": len(overfits)})
    return (DEC_BASELINE, ("every eligible owned fundamental factor, blended at the allowed weights, "
                           "failed to beat the matched-baseline net-25bps (or was cost-killed); adding a "
                           "third owned quality/investment/leverage leg does not improve the two-leg "
                           "composite - the baseline remains champion."),
            {"champion": "baseline_composite_sn", "n_pass": 0})


# --------------------------------------------------------------------------- #
# H. Artifact writers.
# --------------------------------------------------------------------------- #
def write_artifacts(P: _Paths, inv_rows, screen_rows, variant_rows, integrity, log) -> None:
    _write_csv(P.art("inventory"),
               ["feature", "family", "group", "orientation", "source", "recon_formula",
                "normalized_path", "coverage_events", "coverage_tickers", "standalone_ic_t", "eligible"],
               inv_rows)

    _write_csv(P.art("standalone"),
               ["feature", "group", "orientation", "source", "coverage", "tickers", "ic_mean", "ic_t",
                "old_ic", "new_ic", "pre2020_ic", "post2020_ic", "both_cohorts_pos",
                "both_subperiods_pos", "top_sector_share", "eligible", "reject_reason"],
               [[s["feature"], s["group"], s["orientation"], s["source"], s.get("coverage"),
                 s.get("tickers"), _num(s.get("ic_mean")), _num(s.get("ic_t")), _num(s.get("old_ic")),
                 _num(s.get("new_ic")), _num(s.get("pre_ic")), _num(s.get("post_ic")),
                 s.get("both_cohorts_pos"), s.get("both_subperiods_pos"),
                 _num(s.get("top_sector_share")), s.get("eligible"), s.get("reject_reason")]
                for s in screen_rows])

    sc_hdr = ["variant_id", "group", "legs", "weights", "ic_mean", "ic_t", "quarterly_gross_spread",
              "quarterly_net_25bps", "quarterly_net_50bps", "quarterly_turnover", "n_quarters",
              "oos_pooled_ic", "oos_frac_windows_positive", "both_cohorts_positive",
              "both_subperiods_positive", "top_sector_share", "matched_baseline_net_25bps",
              "matched_baseline_ic_t", "matched_baseline_top_sector_share", "classification",
              "reject_reason", "description"]

    def _scrow(v):
        return [v["variant_id"], v["group"], v["legs"], v["weights"], _num(v.get("ic_mean")),
                _num(v.get("ic_t")), _num(v.get("quarterly_gross_spread")),
                _num(v.get("quarterly_net_25bps")), _num(v.get("quarterly_net_50bps")),
                _num(v.get("quarterly_turnover")), v.get("n_quarters"), _num(v.get("oos_pooled_ic")),
                _num(v.get("oos_frac_windows_positive")), v.get("both_cohorts_positive"),
                v.get("both_subperiods_positive"), _num(v.get("top_sector_share")),
                _num(v.get("matched_baseline_net_25bps")), _num(v.get("matched_baseline_ic_t")),
                _num(v.get("matched_baseline_top_sector_share")), v["classification"],
                v.get("reject_reason"), v.get("description")]

    _write_csv(P.art("scorecard"), sc_hdr, [_scrow(v) for v in variant_rows])
    _write_csv(P.art("baseline_vs"), sc_hdr,
               [_scrow(v) for v in variant_rows
                if v["classification"] in (CLS_BASELINE, CLS_PASS, CLS_OVERFIT, CLS_NOIMPROVE)])
    _write_csv(P.art("rejected"), sc_hdr,
               [_scrow(v) for v in variant_rows
                if v["classification"] in (CLS_OVERFIT, CLS_COST, CLS_TURN, CLS_NOIMPROVE)])

    _write_csv(P.art("oos"),
               ["variant_id", "oos_pooled_ic", "oos_frac_windows_positive", "n_quarters",
                "classification"],
               [[v["variant_id"], _num(v.get("oos_pooled_ic")),
                 _num(v.get("oos_frac_windows_positive")), v.get("n_quarters"), v["classification"]]
                for v in variant_rows])
    _write_csv(P.art("cohort"),
               ["variant_id", "both_cohorts_positive", "both_subperiods_positive", "classification"],
               [[v["variant_id"], v.get("both_cohorts_positive"), v.get("both_subperiods_positive"),
                 v["classification"]] for v in variant_rows])
    _write_csv(P.art("sector"),
               ["variant_id", "top_sector_share", "matched_baseline_top_sector_share", "classification"],
               [[v["variant_id"], _num(v.get("top_sector_share")),
                 _num(v.get("matched_baseline_top_sector_share")), v["classification"]]
                for v in variant_rows])
    _write_csv(P.art("turnover"),
               ["variant_id", "quarterly_turnover", "quarterly_net_25bps", "quarterly_net_50bps",
                "classification"],
               [[v["variant_id"], _num(v.get("quarterly_turnover")), _num(v.get("quarterly_net_25bps")),
                 _num(v.get("quarterly_net_50bps")), v["classification"]] for v in variant_rows])


def _next_plan(decision: str) -> Dict:
    if decision == DEC_READY:
        nxt = ("Phase 10-P: package the winning incremental composite as PAPER-ONLY rules (quarterly "
               "rebalance, sector-neutral book, cost budget, kill-switch, human gate). No orders, no "
               "automation, no deploy.")
        cmd = "review research/output/%s/composite_variant_scorecard.csv" % STEM
    else:
        nxt = ("Phase 10-N: fundamental transformation and quality-value interaction search over the "
               "owned factors (signed-log / rank / sector-neutral rank / deltas + quality x value, "
               "profitability x investment, accruals x leverage, FCF x value interactions), same strict "
               "63d relative gates. No new factors, no providers, no live API, no orders, no automation.")
        cmd = "python research/run_phase10n_fundamental_transformation_interaction_search.py"
    return {"phase": "10-N" if decision != DEC_READY else "10-P", "from_decision": decision,
            "next_step": nxt, "exact_next_command": cmd,
            "constraints": ["owned/local data only", "no live API", "no providers", "paper-only",
                            "no orders", "no automation", "no deploy", "no commit", "no push"]}


# --------------------------------------------------------------------------- #
# I. Report.
# --------------------------------------------------------------------------- #
def _build_report(decision, rationale, meta, inv_rows, screen_rows, variant_rows, integrity, eligible,
                  n_events, n_tickers, key_visible) -> Dict:
    baseline = next((v for v in variant_rows if v["classification"] == CLS_BASELINE), {})
    champ_id = meta.get("champion")
    champ = next((v for v in variant_rows if v["variant_id"] == champ_id), baseline)
    passes = [v for v in variant_rows if v["classification"] == CLS_PASS]
    overfits = [v for v in variant_rows if v["classification"] == CLS_OVERFIT]

    def _vslim(v):
        return {k: (_round(v[k], 5) if isinstance(v.get(k), float) else v.get(k))
                for k in ("variant_id", "group", "legs", "weights", "ic_t", "quarterly_net_25bps",
                          "quarterly_net_50bps", "quarterly_turnover", "oos_frac_windows_positive",
                          "top_sector_share", "both_cohorts_positive", "both_subperiods_positive",
                          "classification", "reject_reason")}

    return {
        "phase": PHASE, "phase_name": PHASE_NAME, "as_of": AS_OF,
        "decision": decision, "decision_rationale": rationale,
        "allowed_decisions": list(ALLOWED_DECISIONS),
        "objective": ("test whether a THIRD owned EODHD fundamental factor (profitability / investment / "
                      "leverage) adds incremental alpha to the frozen 10-D composite_sn baseline at 63d - "
                      "NOT reweighting (10-L-B exhausted), NOT a broad alpha search, NOT new providers"),
        "offline": True, "performs_network": PERFORMS_NETWORK,
        "eodhd_key_visible": bool(key_visible), "eodhd_key_required": False,
        "primary_horizon_days": PRIMARY_HORIZON_D,
        "scoreable_events": n_events, "scoreable_tickers": n_tickers,
        "input_inventory": [
            {"feature": r[0], "family": r[1], "group": r[2], "orientation": r[3], "source": r[4],
             "recon_formula": r[5], "coverage_events": r[7], "coverage_tickers": r[8],
             "standalone_ic_t": r[9], "eligible": r[10]} for r in inv_rows],
        "baseline": {"signal": "composite_sn", "weighting": "equal (fcf+ / accruals-), sector-neutral",
                     "ic_t_63d": _round(baseline.get("ic_t"), 3),
                     "quarterly_net_25bps": _round(baseline.get("quarterly_net_25bps"), 5),
                     "quarterly_net_50bps": _round(baseline.get("quarterly_net_50bps"), 5),
                     "quarterly_turnover": _round(baseline.get("quarterly_turnover"), 4),
                     "oos_frac_windows_positive": _round(baseline.get("oos_frac_windows_positive"), 3),
                     "top_sector_share": _round(baseline.get("top_sector_share"), 4),
                     "alpha_character": "modest / boundary (63d IC t < 3.0 strong bar; not oversold)"},
        "phase10d_baseline_reproduction": {
            "got": {k: _round(v, 5) for k, v in integrity["got"].items()},
            "frozen": {k: _round(v, 5) for k, v in integrity["frozen"].items()},
            "deltas": {k: _round(v, 6) for k, v in integrity["deltas"].items()},
            "reproduces_within_tolerance": integrity["reproduces_within_tolerance"],
            "tolerances": REPRO_TOL},
        "n_candidate_factors": len(CANDIDATES),
        "n_eligible_factors": len(eligible),
        "eligible_factors": [c["feature"] for c in eligible],
        "standalone_screen": [
            {"feature": s["feature"], "group": s["group"], "orientation": s["orientation"],
             "coverage": s.get("coverage"), "ic_mean": _round(s.get("ic_mean"), 5),
             "ic_t": _round(s.get("ic_t"), 3), "both_cohorts_pos": s.get("both_cohorts_pos"),
             "both_subperiods_pos": s.get("both_subperiods_pos"), "eligible": s.get("eligible"),
             "reject_reason": s.get("reject_reason")} for s in screen_rows],
        "n_variants": len([v for v in variant_rows if v["classification"] != CLS_BASELINE]),
        "variants_tested": [_vslim(v) for v in variant_rows],
        "candidates_tested": [_vslim(v) for v in variant_rows if v["classification"] != CLS_BASELINE],
        "rejected_candidates": [_vslim(v) for v in variant_rows
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
        "oos_stability_summary": {
            "baseline_oos_frac_pos": _round(baseline.get("oos_frac_windows_positive"), 3),
            "note": "walk-forward 24/6/6 months, pure held-out, equal weighting, no sign refit"},
        "cohort_stability_summary": {
            "note": "old vs new cohort + pre/post-2020 sign checked per variant; a variant that turns a "
                    "cohort or subperiod negative cannot be a champion"},
        "sector_concentration_summary": {
            "baseline_top_sector_share": _round(baseline.get("top_sector_share"), 4),
            "note": "relative gate (variant top-sector share must not exceed matched baseline); the "
                    "sector-neutral book runs ~0.63 due to the known Unknown-sector mapping caveat"},
        "turnover_cost_summary": {
            "baseline_turnover": _round(baseline.get("quarterly_turnover"), 4),
            "cost_model": "quarterly long-short book; net(bps)=mean_spread-(bps/1e4)*turnover*2.0"},
        "n_overfit_variants": len(overfits),
        "implementation_limits": [
            "value / yield factors (earnings_yield, book_to_market, fcf_yield) and multi-year stability "
            "factors are NOT reconstructed here: they require a PIT market-cap / equity price join or "
            "long history not in the current filing-date line-item extraction - deferred (a value leg "
            "is the clearest owned-data gap and a candidate for a dedicated reconstruction phase)",
            "reconstructed factors reuse b10._fund_quarters filing-date availability (90-day fallback "
            "lag when filing_date is absent); same PIT rule as the baseline legs",
            "candidate set is pre-declared (8 factors, 3 families) to avoid multiple-testing inflation; "
            "this is not a broad alpha search",
            "no weight optimisation: only the allowed 70/30, 60/40, 50/50 and 60/20/20, 50/25/25 blends",
        ],
        "next_recommended_phase": "10-P" if passes else "10-N",
        "safety": {"paper_only": True, "owned_local_data_only": True, "no_live_api_calls": True,
                   "no_orders": True, "no_automation": True, "no_broker": True, "no_deploy": True},
        "constraints_honored": ["offline (no network/key/provider probe)", "owned/local data only",
                                "no new factor family beyond owned fundamentals", "no broad alpha search",
                                "no weight optimisation", "no Paper Trader writes", "no orders",
                                "no automation", "no broker", "no deploy", "no GCP", "no package install",
                                "no full regression", "no commit", "no push"],
    }


def _print_summary(report: Dict) -> None:
    ch = report.get("champion", {})
    print("[10-M] decision=%s | candidates=%s eligible=%s variants=%s | champion=%s | "
          "baseline_reproduces=%s"
          % (report.get("decision"), report.get("n_candidate_factors"),
             report.get("n_eligible_factors"), report.get("n_variants"), ch.get("champion"),
             report.get("phase10d_baseline_reproduction", {}).get("reproduces_within_tolerance")))


# --------------------------------------------------------------------------- #
# J. Orchestration.
# --------------------------------------------------------------------------- #
def run(out_dir: Optional[Path] = None, *, as_of: str = AS_OF, verbose: bool = True) -> Dict:
    P = _Paths(out_dir)
    P.out.mkdir(parents=True, exist_ok=True)
    log = t8._Log(verbose)
    try:
        import pandas as pd  # noqa: F401
        key_visible = bool(os.environ.get(EODHD_KEY_ENV))            # context only; NEVER printed/written
        log.step("preflight", "OFFLINE", "owned-data only; no network / no key required; key_visible=%s"
                 % key_visible)

        # 1. Panel + baseline legs + composite_sn (exact 10-D build).
        ev, ok, stats = c10.build_panel(as_of, None, log)
        if not ok:
            return _blocker(P, log, "Norgate survivorship-free panel is empty - rebuild the expanded "
                            "price panel before Phase 10-M.", key_visible)
        n_events = int(stats.get("events_usable", len(ev)))
        n_tickers = int(stats.get("tickers_usable", ev["ticker"].nunique()))
        norm_csvs = c10._default_norm_csvs()
        ev, base_cols = c10.attach_signals(ev, norm_csvs, log)
        ev, comp_cov, _rl, _sl = d10.build_composite(ev, base_cols, log)
        if "month" not in ev.columns:
            ev["month"] = ev["entry_date"].dt.to_period("M")
        integrity = integrity_check(ev, log)
        if not integrity["reproduces_within_tolerance"]:
            return _finish(P, log, DEC_REPAIR,
                           ("the freshly built composite_sn does not reproduce the frozen 10-D baseline "
                            "within tolerance (%s vs %s) - the panel / baseline inputs need repair before "
                            "incremental factors can be tested."
                            % (integrity["got"], integrity["frozen"])),
                           {"champion": "baseline_composite_sn"}, [], [], [], integrity, [],
                           n_events, n_tickers, key_visible)

        # 2. Reconstruct + attach candidates.
        cols: Dict[str, Dict] = {}
        inv_rows: List[List] = []
        for cand in CANDIDATES:
            if cand["source"] == "recon":
                reconstruct_factor(cand, P, as_of, log)
            csv_path = _norm_csv_path(cand, P)
            ev, colinfo = attach_candidate(ev, cand, csv_path, log)
            cols[cand["family"]] = colinfo
            cand["_colinfo"] = colinfo

        # 3. Standalone screen.
        screen_rows: List[Dict] = []
        eligible: List[Dict] = []
        for cand in CANDIDATES:
            s = standalone_screen(ev, cand, cols[cand["family"]], log)
            screen_rows.append(s)
            cand["_screen_ic_t"] = s.get("ic_t")
            if s["eligible"]:
                eligible.append(cand)
        for cand in CANDIDATES:
            s = next(x for x in screen_rows if x["feature"] == cand["feature"])
            inv_rows.append([cand["feature"], cand["family"], cand["group"], cand["orientation"],
                             cand["source"], cand.get("recon", ""), _rel(_norm_csv_path(cand, P)),
                             cols[cand["family"]].get("coverage", 0),
                             cols[cand["family"]].get("tickers", 0), _num(s.get("ic_t")),
                             s.get("eligible")])

        # 4. Composite variants + score.
        variant_rows = build_and_score_variants(ev, cols, eligible, log)

        # 5. Decision.
        decision, rationale, meta = decide(variant_rows, eligible, len(CANDIDATES))

        write_artifacts(P, inv_rows, screen_rows, variant_rows, integrity, log)
        report = _build_report(decision, rationale, meta, inv_rows, screen_rows, variant_rows,
                               integrity, eligible, n_events, n_tickers, key_visible)
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
                  "safety": {"paper_only": True, "owned_local_data_only": True,
                             "no_live_api_calls": True, "no_orders": True, "no_automation": True,
                             "no_broker": True, "no_deploy": True}}
        try:
            P.out.mkdir(parents=True, exist_ok=True)
            _write_json(P.art("report"), report)
        except Exception:
            pass
        return report


def _finish(P, log, decision, rationale, meta, inv_rows, screen_rows, variant_rows, integrity,
            eligible, n_events, n_tickers, key_visible) -> Dict:
    write_artifacts(P, inv_rows, screen_rows, variant_rows, integrity, log)
    report = _build_report(decision, rationale, meta, inv_rows, screen_rows, variant_rows, integrity,
                           eligible, n_events, n_tickers, key_visible)
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
    print("[10-M] decision=%s | %s" % (DEC_REPAIR, detail))
    return report


def _parse_args(argv=None):
    p = argparse.ArgumentParser(description="Phase 10-M - Owned Fundamental Incremental Alpha Expansion")
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
