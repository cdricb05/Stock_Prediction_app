"""Phase 11-B0 - Local Existing Data Entitlement Probe.

WHY THIS PHASE EXISTS
    Phase 11-A concluded (design only) that a genuinely STRONGER alpha than the modest 10-D quality
    baseline (composite_sn: long fcf_to_assets, short operating_accruals, EW, sector-neutral, 63d;
    IC t ~2.665, quarterly net-25bps ~+0.00401) needs NEW ORTHOGONAL data, and ranked analyst estimate
    revisions first. Before recommending any PAID acquisition, the honest first step is to inventory what
    is ALREADY on disk and which provider API keys are ALREADY entitled in this shell - because a prior
    collection pass (or the user) may already have downloaded usable orthogonal data.

    This runner is the entitlement PROBE. It walks research/data, measures the real coverage / history /
    per-ticker depth of every candidate orthogonal family, checks which provider key NAMES are present in
    the environment (NAMES ONLY - never values), and classifies each local family as BACKTESTABLE,
    SHALLOW_SNAPSHOT, TOO_SPARSE, or MISSING. That classification decides whether the queue proceeds to a
    local alpha test (Phase 11-C) or to a paid-acquisition shopping cart (Phase 11-B4).

WHAT IT IS NOT
    It makes NO network calls, downloads nothing, prints NO secret values, writes nothing outside
    research/output/phase11b0_*, and touches no orders / automation / broker / GCP / Paper Trader.

DECISIONS (allowed)
    LOCAL_DATA_READY_FOR_ALPHA_TEST | EXISTING_KEYS_READY_FOR_DOWNLOAD |
    LOCAL_SNAPSHOT_ONLY_NOT_BACKTESTABLE | NO_LOCAL_OR_ENTITLED_DATA_FOUND

CONSTRAINTS HONORED
    Offline (filesystem + os.environ name-presence only); reads only owned/local data; no key value is
    read or emitted; no new purchase; no Paper Trader writes; NO orders / automation / broker / deploy /
    GCP; no package install; targeted tests only; output is research metadata (JSON + CSV) only.
    Commit only the phase11b0 files if targeted tests pass. No push.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import statistics
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]

PHASE = "11-B0"
PHASE_NAME = "Local Existing Data Entitlement Probe"
STEM = "phase11b0_local_data_entitlement_probe"
PERFORMS_NETWORK = False

# --- decision enum ---------------------------------------------------------------------------------
DEC_READY = "LOCAL_DATA_READY_FOR_ALPHA_TEST"
DEC_KEYS = "EXISTING_KEYS_READY_FOR_DOWNLOAD"
DEC_SNAPSHOT = "LOCAL_SNAPSHOT_ONLY_NOT_BACKTESTABLE"
DEC_NONE = "NO_LOCAL_OR_ENTITLED_DATA_FOUND"
ALLOWED_DECISIONS = (DEC_READY, DEC_KEYS, DEC_SNAPSHOT, DEC_NONE)

# --- a-priori readiness thresholds (never tuned to a result) ---------------------------------------
MIN_TICKERS_BACKTEST = 100      # need a broad cross-section for sector-neutral ranking
MIN_TICKERS_NARROW = 50         # below this a sector-neutral cross-section is too thin
MIN_MEDIAN_OBS = 12             # per-ticker history must be deep enough for a walk-forward OOS test
MIN_DISTINCT_MONTHS = 24        # >= 2 years of distinct dates across the universe
MIN_MONTHS_SNAPSHOT = 6         # below this the family is effectively a current snapshot

CLS_BACKTEST = "BACKTESTABLE"
CLS_BACKTEST_NARROW = "BACKTESTABLE_NARROW"
CLS_SHALLOW = "SHALLOW_SNAPSHOT"
CLS_SPARSE = "TOO_SPARSE"
CLS_SNAPSHOT = "SNAPSHOT_ONLY"
CLS_MISSING = "MISSING"

# Provider API-key env var NAMES to probe (presence only - values are never read or emitted).
PROVIDER_KEY_ENVS = [
    "EODHD_API_KEY", "FMP_API_KEY", "FINNHUB_API_KEY", "POLYGON_API_KEY",
    "ALPHAVANTAGE_API_KEY", "NASDAQ_DATA_LINK_API_KEY", "TIINGO_API_KEY", "FRED_API_KEY",
    "INTRINIO_API_KEY", "SIMFIN_API_KEY", "QUANDL_API_KEY", "IEX_API_KEY",
    "ORATS_API_KEY", "BENZINGA_API_KEY", "RAVENPACK_API_KEY", "NEWSAPI_API_KEY",
]

# Curated registry of candidate orthogonal families already present on disk (paths discovered by probe).
# orthogonality = expected orthogonality to the fcf_to_assets / operating_accruals quality baseline.
# prior_status  = what earlier phases concluded about this data family (honesty guard).
LOCAL_FAMILIES = [
    {
        "family_key": "insider_sentiment_mspr", "provider": "finnhub",
        "raw_dir": "research/data/finnhub/raw/insider_sentiment_transactions",
        "normalized_csv": "research/data/finnhub/normalized/insider_sentiment_transactions/insider_mspr.csv",
        "value_col": "insider_mspr", "orthogonality": "HIGH",
        "prior_status": "NEW_NEVER_TESTED",
        "note": "Finnhub /stock/insider-sentiment monthly MSPR + net change; insider positioning family.",
    },
    {
        "family_key": "analyst_recommendation_change", "provider": "finnhub",
        "raw_dir": "research/data/finnhub/raw/analyst_recommendation_changes",
        "normalized_csv": "research/data/finnhub/normalized/analyst_recommendation_changes/rec_change_net.csv",
        "value_col": "rec_change_net", "orthogonality": "MEDIUM_HIGH",
        "prior_status": "NEW_NEVER_TESTED",
        "note": "Finnhub /stock/recommendation buy/hold/sell trend; free tier returns few months per name.",
    },
    {
        "family_key": "analyst_estimate_revision_av", "provider": "alphavantage",
        "raw_dir": "research/data/alpha/raw/analyst_estimate_revisions",
        "normalized_csv": "research/data/alpha/normalized/analyst_estimate_revisions/est_eps_revision.csv",
        "value_col": "est_eps_revision", "orthogonality": "HIGH",
        "prior_status": "PHASE_11A_TOP_RANKED_FAMILY",
        "note": "AlphaVantage EARNINGS EPS estimate vs reported; free tier ~25 req/day rate-caps coverage.",
    },
    {
        "family_key": "analyst_estimates_fmp", "provider": "fmp",
        "raw_dir": "research/data/fmp/raw/analyst_estimates",
        "normalized_csv": None, "value_col": None, "orthogonality": "HIGH",
        "prior_status": "PHASE_11A_TOP_RANKED_FAMILY",
        "note": "FMP analyst estimates; current key tier returns only a handful of names (entitlement cap).",
    },
    {
        "family_key": "short_interest_days_to_cover", "provider": "polygon",
        "raw_dir": "research/data/polygon/raw/short_interest_days_to_cover",
        "normalized_csv": "research/data/polygon/normalized/short_interest_days_to_cover/short_interest_ratio.csv",
        "value_col": "short_interest_ratio", "orthogonality": "MEDIUM",
        "prior_status": "FAMILY_REJECTED_10A",  # short-interest levels failed the 10-A gate (best t=1.56)
        "note": "Polygon short interest / days-to-cover; family already rejected in 10-A on levels; only a "
                "NEW derived field (change / momentum) would be a legitimate re-test.",
    },
]


def _read_norm_csv(path: Path):
    """Return (rows, headers) or ([], []) if missing/unreadable. Rows are dicts."""
    if not path.exists():
        return [], []
    with open(path, "r", encoding="utf-8", newline="") as fh:
        rdr = csv.DictReader(fh)
        rows = list(rdr)
        return rows, (rdr.fieldnames or [])


def _pick_col(headers, *cands):
    for c in cands:
        if c in headers:
            return c
    for h in headers:
        for c in cands:
            if c in h.lower():
                return h
    return None


def _coverage(rows, headers):
    """Compute coverage metrics from normalized rows."""
    tcol = _pick_col(headers, "ticker", "symbol", "code")
    dcol = _pick_col(headers, "available_date", "date", "period", "as_of")
    out = {"rows": len(rows), "ticker_col": tcol, "date_col": dcol,
           "unique_tickers": 0, "distinct_months": 0, "median_obs_per_ticker": 0,
           "min_date": None, "max_date": None}
    if not rows or not tcol:
        return out
    per = {}
    months = set()
    dates = []
    for r in rows:
        t = r.get(tcol)
        if t:
            per[t] = per.get(t, 0) + 1
        if dcol and r.get(dcol):
            d = str(r[dcol])
            dates.append(d)
            if len(d) >= 7:
                months.add(d[:7])
    out["unique_tickers"] = len(per)
    out["distinct_months"] = len(months)
    out["median_obs_per_ticker"] = int(statistics.median(sorted(per.values()))) if per else 0
    if dates:
        dates.sort()
        out["min_date"], out["max_date"] = dates[0], dates[-1]
    return out


def _classify(raw_files, cov, normalized_present):
    """A-priori classification of a family's alpha-test readiness."""
    if raw_files == 0 and not normalized_present:
        return CLS_MISSING
    t = cov["unique_tickers"]
    med = cov["median_obs_per_ticker"]
    mo = cov["distinct_months"]
    if t < MIN_TICKERS_NARROW:
        return CLS_SPARSE
    if mo < MIN_MONTHS_SNAPSHOT:
        return CLS_SNAPSHOT
    if med < MIN_MEDIAN_OBS:
        return CLS_SHALLOW
    if mo < MIN_DISTINCT_MONTHS:
        return CLS_SHALLOW
    if t >= MIN_TICKERS_BACKTEST:
        return CLS_BACKTEST
    return CLS_BACKTEST_NARROW


def _count_raw(raw_dir: Path):
    if not raw_dir.exists():
        return 0, 0.0
    files = [p for p in raw_dir.rglob("*") if p.is_file()]
    mb = round(sum(p.stat().st_size for p in files) / 1e6, 3)
    return len(files), mb


def probe(repo: Path):
    # --- env key presence (NAMES only) ---
    key_presence = {name: (name in os.environ) for name in PROVIDER_KEY_ENVS}
    keys_present = sorted([n for n, ok in key_presence.items() if ok])

    # --- local family inventory ---
    families = []
    for fam in LOCAL_FAMILIES:
        raw_dir = repo / fam["raw_dir"]
        n_raw, mb = _count_raw(raw_dir)
        norm_path = repo / fam["normalized_csv"] if fam.get("normalized_csv") else None
        rows, headers = _read_norm_csv(norm_path) if norm_path else ([], [])
        cov = _coverage(rows, headers)
        cls = _classify(n_raw, cov, bool(rows))
        families.append({
            "family_key": fam["family_key"], "provider": fam["provider"],
            "orthogonality": fam["orthogonality"], "prior_status": fam["prior_status"],
            "raw_files": n_raw, "raw_size_mb": mb,
            "normalized_present": bool(rows), "value_col": fam.get("value_col"),
            "unique_tickers": cov["unique_tickers"], "distinct_months": cov["distinct_months"],
            "median_obs_per_ticker": cov["median_obs_per_ticker"],
            "min_date": cov["min_date"], "max_date": cov["max_date"],
            "classification": cls, "note": fam["note"],
        })

    backtestable = [f for f in families
                    if f["classification"] in (CLS_BACKTEST, CLS_BACKTEST_NARROW)
                    and f["prior_status"] != "FAMILY_REJECTED_10A"]
    any_data = any(f["classification"] != CLS_MISSING for f in families)

    # --- decision ---
    if backtestable:
        decision = DEC_READY
        rationale = (
            "At least one NEW orthogonal family already on disk is broad + deep enough for a walk-forward "
            "alpha test: " + ", ".join(
                "%s (%d tickers, med %d obs/ticker, %d months, %s)"
                % (f["family_key"], f["unique_tickers"], f["median_obs_per_ticker"],
                   f["distinct_months"], f["classification"])
                for f in backtestable)
            + ". Proceed to Phase 11-C on these; the Phase 11-A top family (analyst estimate revisions) "
              "is only sparse locally (free-tier rate caps) and its full-universe depth remains paid-gated."
        )
    elif keys_present:
        decision = DEC_KEYS
        rationale = ("No local family is backtestable yet, but provider keys are entitled ("
                     + ", ".join(keys_present) + "); proceed to keyed free-tier download.")
    elif any_data:
        decision = DEC_SNAPSHOT
        rationale = "Only snapshot / too-sparse local data present; not backtestable without more history."
    else:
        decision = DEC_NONE
        rationale = "No local orthogonal data and no entitled keys found."

    report = {
        "phase": PHASE, "phase_name": PHASE_NAME, "offline": True,
        "performs_network": PERFORMS_NETWORK, "no_secret_values_emitted": True,
        "decision": decision, "decision_rationale": rationale,
        "thresholds": {
            "min_tickers_backtest": MIN_TICKERS_BACKTEST, "min_tickers_narrow": MIN_TICKERS_NARROW,
            "min_median_obs_per_ticker": MIN_MEDIAN_OBS, "min_distinct_months": MIN_DISTINCT_MONTHS,
            "min_months_snapshot": MIN_MONTHS_SNAPSHOT,
        },
        "env_key_presence": key_presence,
        "entitled_keys_present": keys_present,
        "local_family_inventory": families,
        "backtestable_families": [f["family_key"] for f in backtestable],
        "phase_11a_champion_local_status": {
            "family": "analyst_estimate_revisions",
            "status": "SPARSE_LOCAL_ONLY_PAID_GATED_AT_UNIVERSE_DEPTH",
            "detail": "AlphaVantage EARNINGS collected for only ~23 names (25 req/day free cap); FMP "
                      "estimates for ~8 names (key entitlement cap). Neither is broad enough for a "
                      "545-universe sector-neutral revision-momentum test.",
        },
        "next_phase": "11-B1 provider/data-family discovery, then 11-C alpha test on backtestable families",
        "safety": {
            "paper_only": True, "owned_local_data_only": True, "no_live_api_calls": True,
            "no_secret_values_read_or_emitted": True, "no_orders": True, "no_automation": True,
            "no_broker": True, "no_deploy": True, "no_gcp": True, "no_paper_trader_writes": True,
        },
    }
    return report


def _write_json(path: Path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=2, sort_keys=False)


def _write_csv(path: Path, rows, headers):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=headers)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k) for k in headers})


def write_artifacts(out_dir: Path, report):
    _write_json(out_dir / ("%s.json" % STEM), report)
    # local_data_inventory.csv
    inv_headers = ["family_key", "provider", "orthogonality", "prior_status", "raw_files",
                   "raw_size_mb", "normalized_present", "unique_tickers", "distinct_months",
                   "median_obs_per_ticker", "min_date", "max_date", "classification"]
    _write_csv(out_dir / "local_data_inventory.csv", report["local_family_inventory"], inv_headers)
    # env_key_presence.csv (NAMES + bool only)
    key_rows = [{"env_var_name": k, "present": v} for k, v in report["env_key_presence"].items()]
    _write_csv(out_dir / "env_key_presence.csv", key_rows, ["env_var_name", "present"])
    # family_readiness.csv (the gate-facing summary)
    rr = [{"family_key": f["family_key"], "classification": f["classification"],
           "prior_status": f["prior_status"], "unique_tickers": f["unique_tickers"],
           "median_obs_per_ticker": f["median_obs_per_ticker"], "distinct_months": f["distinct_months"],
           "ready_for_alpha_test": (f["classification"] in (CLS_BACKTEST, CLS_BACKTEST_NARROW)
                                    and f["prior_status"] != "FAMILY_REJECTED_10A")}
          for f in report["local_family_inventory"]]
    _write_csv(out_dir / "family_readiness.csv", rr,
               ["family_key", "classification", "prior_status", "unique_tickers",
                "median_obs_per_ticker", "distinct_months", "ready_for_alpha_test"])


def _print_summary(report):
    print("[%s] decision=%s" % (PHASE, report["decision"]))
    print("  entitled keys present: %s" % ", ".join(report["entitled_keys_present"]))
    print("  backtestable families: %s" % ", ".join(report["backtestable_families"]))
    for f in report["local_family_inventory"]:
        print("  - %-32s %-20s tickers=%-4s medObs=%-4s months=%-4s [%s]"
              % (f["family_key"], f["classification"], f["unique_tickers"],
                 f["median_obs_per_ticker"], f["distinct_months"], f["prior_status"]))


def run(out_dir: Path, verbose: bool = True):
    report = probe(_REPO_ROOT)
    write_artifacts(out_dir, report)
    if verbose:
        _print_summary(report)
    return report


def _parse_args(argv):
    ap = argparse.ArgumentParser(description="Phase 11-B0 local data entitlement probe (offline).")
    ap.add_argument("--out-dir", default=str(_REPO_ROOT / "research" / "output" / STEM))
    ap.add_argument("--quiet", action="store_true")
    return ap.parse_args(argv)


def main(argv=None):
    args = _parse_args(argv or [])
    report = run(Path(args.out_dir), verbose=not args.quiet)
    return 0 if report["decision"] in ALLOWED_DECISIONS else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
