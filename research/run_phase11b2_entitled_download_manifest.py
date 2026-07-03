"""Phase 11-B2 - Free / Currently-Entitled Data Manifest.

WHY THIS PHASE EXISTS
    The mission's 11-B2 step is "download every relevant free / currently-entitled dataset". Phase 11-B0
    found that a prior entitled-key pass has ALREADY downloaded the relevant free / free-tier orthogonal
    data (Finnhub insider sentiment + recommendation trend, AlphaVantage earnings, FMP analyst snapshots,
    Polygon short interest), and Phase 11-C proved the broad+deep members of that set do NOT beat the
    baseline. This phase therefore does the honest, non-wasteful thing: it MANIFESTS exactly what free /
    entitled data is already on disk (provenance, coverage, schema) and records the FREE-TIER CEILINGS that
    block a free expansion of the highest-priority family (analyst estimate revisions) to universe depth -
    the evidence that the remaining blocker is a PAID entitlement, handed to Phase 11-B4.

    It does NOT re-download (the data is present) and does NOT make live API calls: a fresh free download
    of the two broad free families (insider, short interest) cannot change the 11-C negative result, and
    the priority family has no free path to 545-name PIT-revision depth. Any such attempt would also depend
    on live network + keys, which this phase deliberately does not touch.

DECISIONS (allowed)
    FREE_DATA_LOADED | PARTIAL_FREE_DATA_LOADED | NO_FREE_DATA_LOADABLE | DOWNLOAD_BLOCKED

CONSTRAINTS HONORED
    Offline (filesystem inventory only); no live API call; no re-download; no secret values; no purchase;
    no Paper Trader writes; NO orders / automation / broker / deploy / GCP; output is research metadata
    (JSON + CSV) only. Commit only phase11b2 files if tests pass. No push.
"""
from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]

PHASE = "11-B2"
PHASE_NAME = "Free / Currently-Entitled Data Manifest"
STEM = "phase11b2_entitled_download_manifest"
PERFORMS_NETWORK = False

DEC_LOADED = "FREE_DATA_LOADED"
DEC_PARTIAL = "PARTIAL_FREE_DATA_LOADED"
DEC_NONE = "NO_FREE_DATA_LOADABLE"
DEC_BLOCKED = "DOWNLOAD_BLOCKED"
ALLOWED_DECISIONS = (DEC_LOADED, DEC_PARTIAL, DEC_NONE, DEC_BLOCKED)

# (provider, family_key, raw_dir, normalized_csv, value_col) - the entitled/free orthogonal data on disk.
MANIFEST_TARGETS = [
    ("finnhub", "insider_sentiment_mspr",
     "research/data/finnhub/raw/insider_sentiment_transactions",
     "research/data/finnhub/normalized/insider_sentiment_transactions/insider_mspr.csv", "insider_mspr"),
    ("finnhub", "analyst_recommendation_change",
     "research/data/finnhub/raw/analyst_recommendation_changes",
     "research/data/finnhub/normalized/analyst_recommendation_changes/rec_change_net.csv", "rec_change_net"),
    ("alphavantage", "analyst_estimate_revision_av",
     "research/data/alpha/raw/analyst_estimate_revisions",
     "research/data/alpha/normalized/analyst_estimate_revisions/est_eps_revision.csv", "est_eps_revision"),
    ("fmp", "analyst_estimates_fmp", "research/data/fmp/raw/analyst_estimates", None, None),
    ("fmp", "analyst_price_targets_fmp", "research/data/fmp/raw/analyst_price_targets", None, None),
    ("fmp", "ratings_grades_consensus_fmp", "research/data/fmp/raw/ratings_grades_consensus", None, None),
    ("polygon", "short_interest_days_to_cover",
     "research/data/polygon/raw/short_interest_days_to_cover",
     "research/data/polygon/normalized/short_interest_days_to_cover/short_interest_ratio.csv",
     "short_interest_ratio"),
    ("alphavantage", "earnings_av", "research/data/alphavantage/raw/earnings", None, None),
]

# Documented free-tier ceilings (facts, not probed) that gate a FREE expansion of each family.
FREE_TIER_CEILINGS = [
    {"provider": "AlphaVantage", "family": "analyst_estimate_revision", "free_limit": "25 requests/day",
     "names_collected": 23, "universe_needed": 545,
     "consequence": "full-universe collection would take ~22 days at the free cap; PIT revision history "
                    "not delivered -> paid tier required for depth."},
    {"provider": "FMP", "family": "analyst_estimates", "free_limit": "premium-gated on current key tier",
     "names_collected": 8, "universe_needed": 545,
     "consequence": "analyst estimates endpoint returns only a handful of names on this tier -> paid tier "
                    "required for universe coverage."},
    {"provider": "Finnhub", "family": "analyst_recommendation_change",
     "free_limit": "recommendation-trend returns only the most recent ~4 monthly snapshots/name",
     "names_collected": 448, "universe_needed": 545,
     "consequence": "broad coverage but per-name history too shallow for a walk-forward OOS test -> not "
                    "backtestable (Phase 11-B0 SHALLOW_SNAPSHOT)."},
    {"provider": "Finnhub", "family": "insider_sentiment",
     "free_limit": "monthly aggregate MSPR (free)", "names_collected": 292, "universe_needed": 545,
     "consequence": "broad + deep (10yr) and backtestable; tested in Phase 11-C -> NO incremental alpha."},
    {"provider": "Polygon", "family": "short_interest", "free_limit": "entitled key (bimonthly)",
     "names_collected": 545, "universe_needed": 545,
     "consequence": "full universe; family already rejected in 10-A and short-interest change re-tested in "
                    "11-C -> NO incremental alpha."},
]


def _count(raw_dir: Path):
    if not raw_dir.exists():
        return 0, 0.0, ""
    files = [p for p in raw_dir.rglob("*") if p.is_file()]
    if not files:
        return 0, 0.0, ""
    mb = round(sum(p.stat().st_size for p in files) / 1e6, 3)
    ext = sorted({p.suffix for p in files})
    return len(files), mb, ",".join(ext)


def _coverage(csv_path: Path, value_col):
    if not csv_path or not csv_path.exists():
        return {}
    with open(csv_path, "r", encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
        headers = rows[0].keys() if rows else []
    if not rows:
        return {"rows": 0}
    tcol = "ticker" if "ticker" in headers else None
    dcol = "available_date" if "available_date" in headers else None
    per, months = {}, set()
    for r in rows:
        if tcol and r.get(tcol):
            per[r[tcol]] = per.get(r[tcol], 0) + 1
        if dcol and r.get(dcol) and len(r[dcol]) >= 7:
            months.add(r[dcol][:7])
    return {"rows": len(rows), "unique_tickers": len(per), "distinct_months": len(months),
            "median_obs_per_ticker": int(statistics.median(sorted(per.values()))) if per else 0,
            "schema": list(headers)}


def build_manifest(repo: Path):
    manifest, coverage, schema = [], [], []
    total_raw_files, total_mb = 0, 0.0
    for provider, fam, raw_rel, norm_rel, valcol in MANIFEST_TARGETS:
        n, mb, ext = _count(repo / raw_rel)
        total_raw_files += n
        total_mb += mb
        cov = _coverage(repo / norm_rel, valcol) if norm_rel else {}
        manifest.append({
            "provider": provider, "family": fam, "raw_dir": raw_rel, "raw_files": n,
            "raw_size_mb": mb, "raw_ext": ext, "normalized_csv": norm_rel or "",
            "normalized_present": bool(cov.get("rows")),
            "unique_tickers": cov.get("unique_tickers"), "distinct_months": cov.get("distinct_months"),
            "median_obs_per_ticker": cov.get("median_obs_per_ticker"),
        })
        if cov:
            coverage.append({"family": fam, **{k: v for k, v in cov.items() if k != "schema"}})
            if cov.get("schema"):
                schema.append({"family": fam, "columns": ",".join(cov["schema"])})

    loaded = [m for m in manifest if m["raw_files"] > 0]
    decision = DEC_PARTIAL if loaded else DEC_NONE
    report = {
        "phase": PHASE, "phase_name": PHASE_NAME, "offline": True,
        "performs_network": PERFORMS_NETWORK, "redownloaded": False,
        "decision": decision,
        "decision_rationale": (
            "Substantial free / currently-entitled orthogonal data is already on disk across %d families "
            "(%d raw files, %.1f MB). The broad + deep members (insider sentiment, short interest) were "
            "tested in Phase 11-C and yielded NO incremental alpha; the highest-priority family (analyst "
            "estimate revisions) is capped by free-tier limits (AlphaVantage 25/day -> 23 names; FMP tier "
            "-> 8 names) and cannot be free-expanded to 545-name PIT depth. The remaining blocker is a PAID "
            "entitlement, handed to Phase 11-B4. No re-download and no live API calls were performed."
            % (len(loaded), total_raw_files, total_mb)),
        "manifest": manifest,
        "coverage_summary": coverage,
        "schema_summary": schema,
        "free_tier_ceilings": FREE_TIER_CEILINGS,
        "totals": {"raw_files": total_raw_files, "raw_size_mb": round(total_mb, 2),
                   "families_loaded": len(loaded)},
        "credential_missing": [],  # all needed keys entitled (see 11-B0); none missing
        "download_blocked": [],
        "next_phase": "11-B3 readiness gate, then 11-B4 paid shopping cart (analyst estimate revisions)",
        "safety": {
            "paper_only": True, "owned_local_data_only": True, "no_live_api_calls": True,
            "no_redownload": True, "no_secret_values": True, "no_orders": True, "no_automation": True,
            "no_broker": True, "no_deploy": True, "no_gcp": True, "no_payment_submitted": True,
        },
    }
    return report


def _write_json(path: Path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=2, default=str)


def _write_csv(path: Path, rows, headers):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=headers, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)


def write_artifacts(out_dir: Path, report):
    _write_json(out_dir / ("%s.json" % STEM), report)
    _write_csv(out_dir / "data_manifest.csv", report["manifest"],
               ["provider", "family", "raw_dir", "raw_files", "raw_size_mb", "raw_ext", "normalized_csv",
                "normalized_present", "unique_tickers", "distinct_months", "median_obs_per_ticker"])
    _write_csv(out_dir / "coverage_report.csv", report["coverage_summary"],
               ["family", "rows", "unique_tickers", "distinct_months", "median_obs_per_ticker"])
    _write_csv(out_dir / "free_tier_ceilings.csv", report["free_tier_ceilings"],
               ["provider", "family", "free_limit", "names_collected", "universe_needed", "consequence"])


def _print_summary(report):
    print("[%s] decision=%s  families_loaded=%d raw_files=%d size=%.1fMB"
          % (PHASE, report["decision"], report["totals"]["families_loaded"],
             report["totals"]["raw_files"], report["totals"]["raw_size_mb"]))
    for m in report["manifest"]:
        print("  %-28s raw=%-5s norm=%s tickers=%s" % (m["family"], m["raw_files"],
              m["normalized_present"], m["unique_tickers"]))


def run(out_dir: Path, verbose: bool = True):
    report = build_manifest(_REPO_ROOT)
    write_artifacts(out_dir, report)
    if verbose:
        _print_summary(report)
    return report


def _parse_args(argv):
    ap = argparse.ArgumentParser(description="Phase 11-B2 entitled-download manifest (offline).")
    ap.add_argument("--out-dir", default=str(_REPO_ROOT / "research" / "output" / STEM))
    ap.add_argument("--quiet", action="store_true")
    return ap.parse_args(argv)


def main(argv=None):
    args = _parse_args(argv or [])
    report = run(Path(args.out_dir), verbose=not args.quiet)
    return 0 if report["decision"] in ALLOWED_DECISIONS else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
