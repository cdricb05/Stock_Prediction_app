"""Phase 11-C - New-Data Orthogonal Alpha Investigation.

WHY THIS PHASE EXISTS
    Phase 11-B0 proved that a genuinely NEW orthogonal data family is already on disk and broad + deep
    enough for a walk-forward test: Finnhub insider-sentiment MSPR (292 tickers, ~76 monthly obs/ticker,
    2016-2026), never tested in any prior phase. Phase 11-C is the honest alpha test of that data (plus a
    new derived short-interest field) against the modest 10-D quality baseline `composite_sn`.

    The test reuses the EXACT 10-D engine and the strict relative beat test from Phase 10-L-B - nothing is
    re-implemented - so a new signal is only allowed to unseat / augment the baseline if it clears the same
    skeptical, cost-aware, OOS-and-subperiod-stable bar the baseline itself was held to. On top of 10-L-B's
    classifier this phase adds the 10-N / 10-O SUBPERIOD-NET25 IMPROVEMENT guard: the incremental gain over
    the baseline must be present (non-worsening) in BOTH the pre-2020 and post-2020 eras, not just full
    sample - the guard that caught the 10-N altcomp_rank and the 10-O regime relics.

REUSE (single source of truth - nothing re-implemented)
    c10  = run_phase10c_eodhd_quality_oos_validation                     (_eval, _pos, AS_OF)
    d10  = run_phase10d_quarterly_quality_composite_validation           (quarterly_backtest, walk_forward_h)
    l10b = run_phase10l_quality_composite_reweighting_robustness_backtest (load_panel, signal_battery,
                                                                           quarterly_book, _assemble, classify)
    panel = research/output/phase10l_historical_sector_neutral_scored_panel_reconstruction/
            historical_sector_neutral_scored_panel.csv  (frozen; (rebalance_date, ticker) -> composite_sn +
            forward_63d_return; reproduces the 10-D baseline within tolerance).

NEW DATA JOINED (owned/local, already downloaded via entitled keys; PIT as-of joined, zero look-ahead)
    insider_sentiment_mspr  research/data/finnhub/normalized/insider_sentiment_transactions/insider_mspr.csv
    short_interest_ratio    research/data/polygon/normalized/short_interest_days_to_cover/short_interest_ratio.csv

HORIZON NOTE
    The frozen offline panel carries only the 63d forward EXCESS return (fwd_exc_63) - the decision horizon
    and the horizon at which the baseline is defined. 5d / 21d are DEFERRED (they would require rebuilding
    shorter-horizon excess returns with the upstream pipeline's excess convention, breaking comparability),
    and are economically secondary for monthly insider / bimonthly short-interest signals. This is recorded
    as an explicit limitation, not silently skipped.

DECISIONS (allowed)
    NEW_ALPHA_FOUND_READY_FOR_PAPER_RULES | NEW_DATA_NO_ALPHA | NEW_DATA_NEEDS_MORE_HISTORY |
    NEW_DATA_TEST_BLOCKED

CONSTRAINTS HONORED
    Offline (reads only owned/local frozen panel + already-downloaded normalized signals; NO network, NO
    key, NO live API call, NO provider probe); paper-only; owned-local-data only; no new purchase; no Paper
    Trader writes; NO orders / automation / broker / deploy / GCP; no package install; targeted tests only;
    output is research metadata (JSON + CSV) only. Commit only the phase11c files if targeted tests pass.
    No push.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from research import run_phase10c_eodhd_quality_oos_validation as c10           # noqa: E402
from research import run_phase10d_quarterly_quality_composite_validation as d10  # noqa: E402
from research import run_phase10l_quality_composite_reweighting_robustness_backtest as l10b  # noqa: E402

PHASE = "11-C"
PHASE_NAME = "New-Data Orthogonal Alpha Investigation"
STEM = "phase11c_new_data_orthogonal_alpha_investigation"
PERFORMS_NETWORK = False

_PANEL_REL = ("research/output/phase10l_historical_sector_neutral_scored_panel_reconstruction/"
              "historical_sector_neutral_scored_panel.csv")
_INSIDER_REL = "research/data/finnhub/normalized/insider_sentiment_transactions/insider_mspr.csv"
_SHORT_REL = "research/data/polygon/normalized/short_interest_days_to_cover/short_interest_ratio.csv"

_PRE2020 = pd.Timestamp("2020-01-01")
_EPS = 1e-6

# Decisions.
DEC_ALPHA = "NEW_ALPHA_FOUND_READY_FOR_PAPER_RULES"
DEC_NO_ALPHA = "NEW_DATA_NO_ALPHA"
DEC_MORE_HISTORY = "NEW_DATA_NEEDS_MORE_HISTORY"
DEC_BLOCKED = "NEW_DATA_TEST_BLOCKED"
ALLOWED_DECISIONS = (DEC_ALPHA, DEC_NO_ALPHA, DEC_MORE_HISTORY, DEC_BLOCKED)

CLS_PASS = "PASS_STRICT"  # must match l10b.CLS_PASS


# --------------------------------------------------------------------------- #
# A. New-signal loading + PIT as-of join + sector-neutralization.
# --------------------------------------------------------------------------- #
def _load_series(path: Path, valcol: str):
    if not path.exists():
        return None
    s = pd.read_csv(path)
    if "ticker" not in s.columns or "available_date" not in s.columns or valcol not in s.columns:
        return None
    s = s[["ticker", "available_date", valcol]].copy()
    s["available_date"] = pd.to_datetime(s["available_date"], errors="coerce")
    s[valcol] = pd.to_numeric(s[valcol], errors="coerce")
    s = s.dropna(subset=["available_date", valcol]).sort_values(["ticker", "available_date"])
    return s.reset_index(drop=True)


def _asof_join(panel: pd.DataFrame, series: pd.DataFrame, valcol: str, outcol: str):
    """PIT backward as-of join: for each (ticker, entry_date) take the latest available_date <= entry_date.
    Zero look-ahead."""
    left = panel.sort_values("entry_date").reset_index(drop=True)
    right = series.rename(columns={"available_date": "entry_date"}).sort_values("entry_date")
    right = right[["ticker", "entry_date", valcol]].reset_index(drop=True)
    merged = pd.merge_asof(left, right, on="entry_date", by="ticker", direction="backward")
    merged = merged.rename(columns={valcol: outcol})
    return merged


def _sector_neutralize(df: pd.DataFrame, incol: str, outcol: str):
    """Within-month z-score, then subtract the (month, sector) mean -> sector-neutral z (same construction
    as the frozen quality legs). Rows with a NaN input stay NaN and are filtered by the engine."""
    z = df.groupby("month")[incol].transform(lambda x: (x - x.mean()) / x.std(ddof=0))
    df["_z_tmp"] = z
    sec_mean = df.groupby(["month", "sector"])["_z_tmp"].transform("mean")
    df[outcol] = df["_z_tmp"] - sec_mean
    df.drop(columns=["_z_tmp"], inplace=True)
    return df


def _coverage(df: pd.DataFrame, sigcol: str):
    m = df[sigcol].notna() & df["fwd_exc_63"].notna()
    sub = df[m]
    return {
        "signal": sigcol,
        "scoreable_rows": int(m.sum()),
        "unique_tickers": int(sub["ticker"].nunique()),
        "unique_months": int(sub["month"].nunique()),
        "min_entry_date": (str(sub["entry_date"].min().date()) if not sub.empty else None),
        "max_entry_date": (str(sub["entry_date"].max().date()) if not sub.empty else None),
    }


# --------------------------------------------------------------------------- #
# B. Subperiod-net25 improvement guard (the 10-N / 10-O method contribution).
# --------------------------------------------------------------------------- #
def _subperiod_net25(df: pd.DataFrame, sigcol: str):
    pre = d10.quarterly_backtest(df[df["entry_date"] < _PRE2020], sigcol, ret_col="fwd_exc_63")
    post = d10.quarterly_backtest(df[df["entry_date"] >= _PRE2020], sigcol, ret_col="fwd_exc_63")
    return pre.get("net_25bps"), post.get("net_25bps")


def _fin(x):
    try:
        return x is not None and np.isfinite(float(x))
    except (TypeError, ValueError):
        return False


def _improvement_survives_subperiods(df, blend_col, base_pre, base_post):
    b_pre, b_post = _subperiod_net25(df, blend_col)
    survives = (_fin(b_pre) and _fin(base_pre) and b_pre >= base_pre - _EPS
                and _fin(b_post) and _fin(base_post) and b_post >= base_post - _EPS)
    return {"blend_pre2020_net25": b_pre, "blend_post2020_net25": b_post,
            "base_pre2020_net25": base_pre, "base_post2020_net25": base_post,
            "improvement_survives_subperiods": bool(survives)}


# --------------------------------------------------------------------------- #
# C. Score one signal column with the exact 10-D engine + strict relative test.
# --------------------------------------------------------------------------- #
def _score(df, sigcol, vid, group, desc, base_rec):
    batt = l10b.signal_battery(df, sigcol)
    book = l10b.quarterly_book(df, sigcol, cap_frac=None)
    rec = l10b._assemble(vid, group, desc, None, None, batt, book, {}, "backtested_sn")
    cls, reason = l10b.classify(rec, base_rec)
    rec["classification"], rec["reject_reason"] = cls, reason
    return rec


# --------------------------------------------------------------------------- #
# D. Main investigation.
# --------------------------------------------------------------------------- #
def investigate(repo: Path, log=print):
    panel_path = repo / _PANEL_REL
    df, meta, err = l10b.load_panel(panel_path)
    if err:
        return {"decision": DEC_BLOCKED, "decision_rationale": "panel load failed: %s" % err,
                "blocked": True}

    families_attempted, providers_attempted = [], []
    coverage, standalone, blends = [], [], []
    normalized_paths = []

    # Baseline (frozen 10-D composite_sn) metrics on this panel.
    base_batt = l10b.signal_battery(df, "comp_sn")
    base_book = l10b.quarterly_book(df, "comp_sn", cap_frac=None)
    base_rec = l10b._assemble("baseline_composite_sn", "baseline",
                              "10-D quality composite_sn (long fcf_to_assets, short operating_accruals, "
                              "EW, sector-neutral, 63d)", 0.5, 0.5, base_batt, base_book, {}, "backtested_sn")
    base_rec["is_baseline"] = True
    base_rec["classification"], base_rec["reject_reason"] = "BASELINE", "confirmed baseline configuration"
    base_pre, base_post = _subperiod_net25(df, "comp_sn")

    # ---- Family 1: Finnhub insider-sentiment MSPR (NEW, never tested) -----------------------------
    ins = _load_series(repo / _INSIDER_REL, "insider_mspr")
    if ins is not None and not ins.empty:
        families_attempted.append("insider_sentiment_mspr")
        providers_attempted.append("finnhub")
        normalized_paths.append(_INSIDER_REL)
        # trailing-3-observation smoothed MSPR (monthly insider prints are noisy).
        ins = ins.copy()
        ins["insider_mspr_3m"] = ins.groupby("ticker")["insider_mspr"].transform(
            lambda s: s.rolling(3, min_periods=1).mean())
        df = _asof_join(df, ins[["ticker", "available_date", "insider_mspr"]], "insider_mspr",
                        "insider_mspr_last_raw")
        # re-join the smoothed column (as-of) via a second merge on the smoothed frame
        sm = ins.rename(columns={"insider_mspr_3m": "val"})[["ticker", "available_date", "val"]]
        df = _asof_join(df, sm, "val", "insider_mspr_3m_raw")
        # orient +1 (higher MSPR == more insider buying == bullish hypothesis) then sector-neutralize.
        _sector_neutralize(df, "insider_mspr_last_raw", "insider_mspr_last_sn")
        _sector_neutralize(df, "insider_mspr_3m_raw", "insider_mspr_3m_sn")
        for col, label in (("insider_mspr_last_sn", "insider MSPR (latest, sector-neutral)"),
                           ("insider_mspr_3m_sn", "insider MSPR (trailing-3m mean, sector-neutral)")):
            coverage.append(_coverage(df, col))
            standalone.append(_score(df, col, col, "standalone_insider", label, base_rec))
        # incremental blends: composite_sn + w * insider_mspr_3m_sn (the smoother variant).
        for w in (0.15, 0.30, 0.50):
            bcol = "__blend_ins3m_%02d__" % int(w * 100)
            df[bcol] = df["comp_sn"] + w * df["insider_mspr_3m_sn"]
            rec = _score(df, bcol, "blend_insider_mspr3m_w%.2f" % w, "incremental_blend",
                         "composite_sn + %.2f * insider_mspr_3m_sn" % w, base_rec)
            rec["blend_weight"] = w
            rec.update(_improvement_survives_subperiods(df, bcol, base_pre, base_post))
            blends.append(rec)
        # one blend on the latest (unsmoothed) variant for contrast.
        df["__blend_inslast_030__"] = df["comp_sn"] + 0.30 * df["insider_mspr_last_sn"]
        rec = _score(df, "__blend_inslast_030__", "blend_insider_msprlast_w0.30", "incremental_blend",
                     "composite_sn + 0.30 * insider_mspr_last_sn", base_rec)
        rec["blend_weight"] = 0.30
        rec.update(_improvement_survives_subperiods(df, "__blend_inslast_030__", base_pre, base_post))
        blends.append(rec)

    # ---- Family 2: Polygon short-interest CHANGE (NEW derived field of a rejected family) ----------
    si = _load_series(repo / _SHORT_REL, "short_interest_ratio")
    if si is not None and not si.empty:
        families_attempted.append("short_interest_change")
        providers_attempted.append("polygon")
        normalized_paths.append(_SHORT_REL)
        si = si.copy()
        # rising short interest == more bearish; orient so higher == better -> signal = -change.
        si["si_change_neg"] = -si.groupby("ticker")["short_interest_ratio"].diff()
        si = si.dropna(subset=["si_change_neg"])
        df = _asof_join(df, si[["ticker", "available_date", "si_change_neg"]], "si_change_neg",
                        "si_change_raw")
        _sector_neutralize(df, "si_change_raw", "short_interest_change_sn")
        coverage.append(_coverage(df, "short_interest_change_sn"))
        standalone.append(_score(df, "short_interest_change_sn", "short_interest_change_sn",
                                 "standalone_short_interest",
                                 "short-interest change (bearish-oriented, sector-neutral); NEW derived "
                                 "field of the 10-A-rejected short-interest family", base_rec))

    # ---- Decision ---------------------------------------------------------------------------------
    def _passes(rec):
        return rec.get("classification") == CLS_PASS and bool(rec.get("improvement_survives_subperiods"))

    winners = [b for b in blends if _passes(b)]
    if winners:
        winners.sort(key=lambda r: (r.get("quarterly_net_25bps") or -9), reverse=True)
        champ = winners[0]
        decision = DEC_ALPHA
        rationale = ("Blend %s beats composite_sn on the full strict relative test AND the subperiod-net25 "
                     "improvement generalizes to both eras: quarterly net-25bps %s vs baseline %s."
                     % (champ["variant_id"], champ.get("quarterly_net_25bps"),
                        base_rec.get("quarterly_net_25bps")))
    else:
        champ = None
        decision = DEC_NO_ALPHA
        # honest attribution of why each blend failed
        reasons = "; ".join("%s -> %s" % (b["variant_id"], b.get("classification")) for b in blends)
        rationale = ("No new-data signal beats the modest composite_sn baseline under the strict, "
                     "cost-aware, OOS + subperiod-stable relative test. Insider MSPR and short-interest "
                     "change are real orthogonal families but do not add robust incremental alpha at 63d "
                     "(baseline stays champion). Blend outcomes: %s." % reasons)

    report = {
        "phase": PHASE, "phase_name": PHASE_NAME, "offline": True,
        "performs_network": PERFORMS_NETWORK, "eodhd_key_required": False,
        "decision": decision, "decision_rationale": rationale,
        "data_families_attempted": families_attempted,
        "providers_attempted": sorted(set(providers_attempted)),
        "files_downloaded": [],  # none this phase - data already on disk from prior entitled downloads
        "raw_data_paths": [],
        "normalized_data_paths": normalized_paths,
        "panel_meta": meta,
        "baseline": {
            "signal": "composite_sn",
            "ic_t": base_rec.get("ic_t"), "quarterly_net_25bps": base_rec.get("quarterly_net_25bps"),
            "quarterly_net_50bps": base_rec.get("quarterly_net_50bps"),
            "quarterly_turnover": base_rec.get("quarterly_turnover"),
            "oos_frac_windows_positive": base_rec.get("oos_frac_windows_positive"),
            "top_sector_share": base_rec.get("top_sector_share"), "n_quarters": base_rec.get("n_quarters"),
            "pre2020_net25": base_pre, "post2020_net25": base_post,
        },
        "coverage_summary": coverage,
        "standalone_results": standalone,
        "incremental_blend_results": blends,
        "champion": champ,
        "schema_summary": {"panel_key": "(rebalance_date, ticker)", "return_col": "fwd_exc_63",
                           "join": "PIT backward merge_asof by ticker (available_date <= entry_date)"},
        "quality_summary": {
            "pit_lookahead": "none (backward as-of join; available_date <= entry_date)",
            "sector_neutral": "within-month z then (month,sector) demean; evaluated sector_neutral=False",
            "subperiod_guard": "blend net-25bps must not worsen vs baseline in BOTH pre/post-2020 eras",
        },
        "horizon_limitation": ("Only 63d (fwd_exc_63) tested - the decision horizon and the horizon the "
                               "baseline is defined at. 5d/21d deferred (frozen panel is 63d-only; "
                               "rebuilding shorter horizons would break excess-return comparability) and "
                               "are economically secondary for monthly/bimonthly signals."),
        "blocked_sources": [],
        "paid_sources": [
            {"family": "analyst_estimate_revisions", "status": "PAID_GATED_AT_UNIVERSE_DEPTH",
             "note": "the Phase 11-A #1 family; only 23/8 names locally (free-tier caps). See Phase 11-B4 "
                     "if no local alpha is found."},
        ],
        "next_phase": (DEC_ALPHA + " -> paper-rules package" if decision == DEC_ALPHA
                       else "11-B4 paid-data shopping cart (analyst estimate revisions trial)"),
        "safety": {
            "paper_only": True, "owned_local_data_only": True, "no_live_api_calls": True,
            "no_orders": True, "no_automation": True, "no_broker": True, "no_deploy": True,
            "no_gcp": True, "no_paper_trader_writes": True, "no_payment_submitted": True,
        },
    }
    return report


# --------------------------------------------------------------------------- #
# E. Artifacts + CLI.
# --------------------------------------------------------------------------- #
def _write_json(path: Path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=2, sort_keys=False, default=str)


def _write_csv(path: Path, rows, headers):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=headers, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)


def write_artifacts(out_dir: Path, report):
    _write_json(out_dir / ("%s.json" % STEM), report)
    sc_headers = ["variant_id", "group", "classification", "ic_t", "quarterly_net_25bps",
                  "quarterly_net_50bps", "quarterly_turnover", "oos_frac_windows_positive",
                  "both_cohorts_positive", "both_subperiods_positive", "top_sector_share", "n_quarters",
                  "improvement_survives_subperiods", "reject_reason"]
    allrecs = report.get("standalone_results", []) + report.get("incremental_blend_results", [])
    _write_csv(out_dir / "signal_scorecard.csv", allrecs, sc_headers)
    _write_csv(out_dir / "pit_join_coverage.csv", report.get("coverage_summary", []),
               ["signal", "scoreable_rows", "unique_tickers", "unique_months",
                "min_entry_date", "max_entry_date"])
    _write_csv(out_dir / "incremental_blend_results.csv", report.get("incremental_blend_results", []),
               ["variant_id", "blend_weight", "classification", "quarterly_net_25bps",
                "base_pre2020_net25", "blend_pre2020_net25", "base_post2020_net25", "blend_post2020_net25",
                "improvement_survives_subperiods", "reject_reason"])
    b = report["baseline"]
    ch = report.get("champion")
    bvc = [{"which": "baseline_composite_sn", "ic_t": b["ic_t"], "net_25bps": b["quarterly_net_25bps"],
            "net_50bps": b["quarterly_net_50bps"], "turnover": b["quarterly_turnover"],
            "oos_frac_positive": b["oos_frac_windows_positive"]}]
    if ch:
        bvc.append({"which": ch["variant_id"], "ic_t": ch.get("ic_t"),
                    "net_25bps": ch.get("quarterly_net_25bps"), "net_50bps": ch.get("quarterly_net_50bps"),
                    "turnover": ch.get("quarterly_turnover"),
                    "oos_frac_positive": ch.get("oos_frac_windows_positive")})
    _write_csv(out_dir / "baseline_vs_champion.csv", bvc,
               ["which", "ic_t", "net_25bps", "net_50bps", "turnover", "oos_frac_positive"])


def _print_summary(report):
    print("[%s] decision=%s" % (PHASE, report["decision"]))
    b = report["baseline"]
    print("  baseline composite_sn: ic_t=%s net25=%s net50=%s (pre25=%s post25=%s)"
          % (b["ic_t"], b["quarterly_net_25bps"], b["quarterly_net_50bps"],
             b["pre2020_net25"], b["post2020_net25"]))
    print("  -- standalone new signals --")
    for r in report.get("standalone_results", []):
        print("    %-26s ic_t=%-7s net25=%-9s oos+=%-5s [%s]"
              % (r["variant_id"], r.get("ic_t"), r.get("quarterly_net_25bps"),
                 r.get("oos_frac_windows_positive"), r.get("classification")))
    print("  -- incremental blends vs composite_sn --")
    for r in report.get("incremental_blend_results", []):
        print("    %-30s net25=%-9s subperiod_ok=%-5s [%s]"
              % (r["variant_id"], r.get("quarterly_net_25bps"),
                 r.get("improvement_survives_subperiods"), r.get("classification")))


def run(out_dir: Path, verbose: bool = True):
    report = investigate(_REPO_ROOT)
    write_artifacts(out_dir, report)
    if verbose:
        _print_summary(report)
    return report


def _parse_args(argv):
    ap = argparse.ArgumentParser(description="Phase 11-C new-data orthogonal alpha investigation (offline).")
    ap.add_argument("--out-dir", default=str(_REPO_ROOT / "research" / "output" / STEM))
    ap.add_argument("--quiet", action="store_true")
    return ap.parse_args(argv)


def main(argv=None):
    args = _parse_args(argv or [])
    report = run(Path(args.out_dir), verbose=not args.quiet)
    return 0 if report.get("decision") in ALLOWED_DECISIONS else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
