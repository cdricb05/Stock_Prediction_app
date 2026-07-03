"""Phase 11-B3 - Alpha-Readiness Gate For Newly Loaded Data.

WHY THIS PHASE EXISTS
    The queue's readiness gate: for every candidate orthogonal family it decides whether the data is good
    enough to support (or has already supported) an incremental-alpha test versus composite_sn. It is a
    synthesis of the frozen prior-phase JSONs - the Phase 11-B0 local inventory (coverage / history / PIT)
    and the Phase 11-C alpha-test outcome - not a new backtest. It builds no panel, fits nothing, and
    touches no network.

    Because Phase 11-C has already been run (the one broad + deep local family, insider sentiment, was
    ready and got tested), this gate resolves forward-looking: no locally-ready UNTESTED family remains
    that could yield a stronger alpha, the tested families gave none, and the highest-priority family
    (analyst estimate revisions) is not locally backtestable -> the next data must be PAID.

DECISIONS (allowed)
    NEW_DATA_READY_FOR_ALPHA_TEST | NEW_DATA_PARTIAL_NEEDS_REPAIR | NEW_DATA_NOT_BACKTESTABLE |
    NEEDS_PAID_DATA

CONSTRAINTS HONORED
    Offline (reads only frozen local prior-phase JSONs); no network; no key; no purchase; no Paper Trader
    writes; NO orders / automation / broker / deploy / GCP; output is research metadata (JSON + CSV) only.
    Commit only phase11b3 files if tests pass. No push.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]

PHASE = "11-B3"
PHASE_NAME = "Alpha-Readiness Gate For Newly Loaded Data"
STEM = "phase11b3_alpha_readiness_gate"
PERFORMS_NETWORK = False

DEC_READY = "NEW_DATA_READY_FOR_ALPHA_TEST"
DEC_REPAIR = "NEW_DATA_PARTIAL_NEEDS_REPAIR"
DEC_NOT_BT = "NEW_DATA_NOT_BACKTESTABLE"
DEC_PAID = "NEEDS_PAID_DATA"
ALLOWED_DECISIONS = (DEC_READY, DEC_REPAIR, DEC_NOT_BT, DEC_PAID)

_B0_JSON = ("research/output/phase11b0_local_data_entitlement_probe/"
            "phase11b0_local_data_entitlement_probe.json")
_C_JSON = ("research/output/phase11c_new_data_orthogonal_alpha_investigation/"
           "phase11c_new_data_orthogonal_alpha_investigation.json")

# Gate statuses.
G_TESTED_NO_ALPHA = "READY_TESTED_NO_ALPHA"
G_READY_UNTESTED = "READY_UNTESTED"
G_NOT_BT = "NOT_BACKTESTABLE"
G_PAID = "PAID_GATED"


def _load_json(path: Path):
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def gate(repo: Path):
    b0 = _load_json(repo / _B0_JSON)
    c = _load_json(repo / _C_JSON)

    inv = (b0 or {}).get("local_family_inventory", [])
    c_families_tested = set((c or {}).get("data_families_attempted", []))
    c_decision = (c or {}).get("decision")

    rows = []
    for f in inv:
        key = f["family_key"]
        cls = f["classification"]
        prior = f.get("prior_status")
        backtestable = cls in ("BACKTESTABLE", "BACKTESTABLE_NARROW")
        # map B0 family_key -> the 11-C family label
        tested = ("insider_sentiment_mspr" in c_families_tested and key == "insider_sentiment_mspr") or \
                 ("short_interest_change" in c_families_tested and key == "short_interest_days_to_cover")
        if prior in ("PHASE_11A_TOP_RANKED_FAMILY",) and not backtestable:
            status = G_PAID          # analyst revisions: too sparse locally -> paid
        elif tested:
            status = G_TESTED_NO_ALPHA if c_decision == "NEW_DATA_NO_ALPHA" else G_READY_UNTESTED
        elif backtestable:
            status = G_READY_UNTESTED
        else:
            status = G_NOT_BT
        rows.append({
            "family_key": key, "classification": cls, "prior_status": prior,
            "backtestable": backtestable, "tested_in_11c": bool(tested),
            "gate_status": status,
            "unique_tickers": f.get("unique_tickers"), "distinct_months": f.get("distinct_months"),
            "median_obs_per_ticker": f.get("median_obs_per_ticker"),
        })

    ready_untested = [r for r in rows if r["gate_status"] == G_READY_UNTESTED]
    tested_no_alpha = [r for r in rows if r["gate_status"] == G_TESTED_NO_ALPHA]
    paid_gated = [r for r in rows if r["gate_status"] == G_PAID]

    # Decision logic (forward-looking, given 11-C already ran).
    if ready_untested:
        decision = DEC_READY
        rationale = ("A locally-ready, untested orthogonal family remains: %s. Run Phase 11-C on it."
                     % ", ".join(r["family_key"] for r in ready_untested))
    elif tested_no_alpha and paid_gated:
        decision = DEC_PAID
        rationale = ("Every locally-backtestable family has been tested and yields NO incremental alpha "
                     "over composite_sn (%s), and the highest-priority family(s) (%s) are only sparse "
                     "locally and paid-gated at universe depth. A stronger alpha needs NEW PAID data -> "
                     "Phase 11-B4." % (", ".join(r["family_key"] for r in tested_no_alpha),
                                       ", ".join(r["family_key"] for r in paid_gated)))
    elif not any(r["backtestable"] for r in rows):
        decision = DEC_NOT_BT
        rationale = "No local family is backtestable; the ready data is snapshot/too-sparse."
    else:
        decision = DEC_PAID
        rationale = "No untested locally-ready family remains; the priority family is paid-gated."

    report = {
        "phase": PHASE, "phase_name": PHASE_NAME, "offline": True,
        "performs_network": PERFORMS_NETWORK,
        "decision": decision, "decision_rationale": rationale,
        "inputs_read": {"phase11b0": bool(b0), "phase11c": bool(c),
                        "phase11c_decision": c_decision},
        "gate_rows": rows,
        "ready_untested": [r["family_key"] for r in ready_untested],
        "tested_no_alpha": [r["family_key"] for r in tested_no_alpha],
        "paid_gated": [r["family_key"] for r in paid_gated],
        "baseline_remains_champion": (c_decision == "NEW_DATA_NO_ALPHA"),
        "next_phase": ("11-B4 paid shopping cart (analyst estimate revisions)" if decision == DEC_PAID
                       else "11-C alpha test on the ready family"),
        "safety": {
            "paper_only": True, "owned_local_data_only": True, "no_live_api_calls": True,
            "no_orders": True, "no_automation": True, "no_broker": True, "no_deploy": True,
            "no_gcp": True, "no_paper_trader_writes": True,
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
    _write_csv(out_dir / "readiness_gate.csv", report["gate_rows"],
               ["family_key", "classification", "prior_status", "backtestable", "tested_in_11c",
                "gate_status", "unique_tickers", "distinct_months", "median_obs_per_ticker"])


def _print_summary(report):
    print("[%s] decision=%s" % (PHASE, report["decision"]))
    for r in report["gate_rows"]:
        print("  %-32s %-22s tested=%s" % (r["family_key"], r["gate_status"], r["tested_in_11c"]))


def run(out_dir: Path, verbose: bool = True):
    report = gate(_REPO_ROOT)
    write_artifacts(out_dir, report)
    if verbose:
        _print_summary(report)
    return report


def _parse_args(argv):
    ap = argparse.ArgumentParser(description="Phase 11-B3 alpha-readiness gate (offline synthesis).")
    ap.add_argument("--out-dir", default=str(_REPO_ROOT / "research" / "output" / STEM))
    ap.add_argument("--quiet", action="store_true")
    return ap.parse_args(argv)


def main(argv=None):
    args = _parse_args(argv or [])
    report = run(Path(args.out_dir), verbose=not args.quiet)
    return 0 if report["decision"] in ALLOWED_DECISIONS else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
