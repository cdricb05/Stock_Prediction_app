"""Phase 10-G (review template) - Human approve/reject review template for the repaired L/S book.

WHY THIS EXISTS
    Phase 10-F-A repaired the sector labels of the 10-E quarterly quality book and produced a reranked
    sector-neutral long/short book (research/output/phase10f_owned_sector_mapping_repair/
    reranked_paper_review_long_short_book.csv: 97 long + 97 short) with decision
    SECTOR_MAPPING_REPAIRED_READY_FOR_HUMAN_REVIEW. The one allowed next step is to put that book in
    front of a HUMAN for an explicit approve/reject pass.

    This generator builds that review template. It reads the repaired book and writes one row per
    long/short candidate with EVERY candidate defaulted to review_decision = NEEDS_REVIEW. It performs
    NO auto-approval, writes NOTHING to Paper Trader, creates NO orders, and creates NO automation. It
    is a static, human-fill-in template: changing a decision in this CSV changes nothing downstream
    until a human acts on it in a later, separately-gated step.

    NOT a new alpha search. NOT a provider search. NOT order creation. NOT automation. NOT a deploy.
    NOT a Paper Trader integration. Fully offline (no network, no API key, no provider probe). Output
    is metadata-only CSV/JSON in this phase's own research/output directory.

WHAT EVERY CANDIDATE GETS
    - review_decision  = NEEDS_REVIEW   (the default for EVERY row; never APPROVE - no auto-approval)
    - approved         = FALSE          (generation-time guarantee nothing is pre-approved)
    - order_action     = NO_ORDER       (explicit: this template never implies an order)
    - review_status    = PAPER_REVIEW_ONLY
    - blank reviewer-input columns (reviewer / reviewed_at / conviction / reviewer_notes) for the human

ALLOWED HUMAN DECISIONS (the human may later set review_decision to one of)
    NEEDS_REVIEW | APPROVE | REJECT | HOLD_FOR_MORE_INFO
    The template ships with all rows = NEEDS_REVIEW; the generator NEVER writes APPROVE.

TERMINAL DECISIONS (allowed)
    REVIEW_TEMPLATE_READY_FOR_HUMAN_REVIEW | HARD_BLOCKER_REQUIRES_USER_ACTION | ERROR_WITH_REPRO_COMMAND
    FORBIDDEN: LIVE_TRADING_READY, ORDER_READY, AUTOMATION_READY, AUTO_APPROVED,
    AUTO_APPROVED_READY, STRONG_ALPHA_FOUND_READY_FOR_REVIEW, MISSING_KEY, NO_DATA, NEEDS_PROVIDER,
    EMPTY_PAYLOAD, generic ERROR.

CONSTRAINTS HONORED
    Offline (no network / key / provider probe); reads only the owned 10-F-A repaired book; no FMP /
    AlphaVantage / Polygon / Finnhub / Norgate-API; no new purchase; NO auto-approval; no Paper Trader
    writes; no GCP; NO orders; NO automation; NO live trading; NO broker; no deploy; no package
    install; no full regression (targeted tests only); keys never printed or written; output is
    metadata only. No commit. No push.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Dict, List, Optional

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from research import run_phase8s_autonomous_eodhd_alpha_factory as s8            # noqa: E402  io helpers
from research import run_phase10e_quarterly_quality_paper_review_harness as e10  # noqa: E402  badges/status

_write_json = s8._write_json
_write_csv = s8._write_csv
_read_csv_file = s8._read_csv_file
_rel = s8._rel

PHASE = "10-G (review template)"
PERFORMS_NETWORK = False

# --- the single review state every candidate ships in ---------------------- #
DEFAULT_DECISION = "NEEDS_REVIEW"
ALLOWED_HUMAN_DECISIONS = ("NEEDS_REVIEW", "APPROVE", "REJECT", "HOLD_FOR_MORE_INFO")
ORDER_ACTION = "NO_ORDER"            # explicit, fixed: this template never implies an order
APPROVED_AT_GENERATION = "FALSE"     # nothing is pre-approved
REVIEW_STATUS = getattr(e10, "REVIEW_STATUS", "PAPER_REVIEW_ONLY")
SIDE_LONG = getattr(e10, "SIDE_LONG", "LONG")
SIDE_SHORT = getattr(e10, "SIDE_SHORT", "SHORT")
SAFETY_BADGES = list(getattr(e10, "SAFETY_BADGES", [
    "PAPER REVIEW ONLY", "NO ORDERS", "NO AUTOMATION", "HUMAN APPROVAL REQUIRED",
    "NO LIVE TRADING", "NO BROKER", "CREATES NO TRADE DECISIONS", "MANUAL REVIEW",
])) + ["NO AUTO-APPROVAL"]

# --- terminal decisions ---------------------------------------------------- #
DEC_READY = "REVIEW_TEMPLATE_READY_FOR_HUMAN_REVIEW"
DEC_HARD_BLOCKER = "HARD_BLOCKER_REQUIRES_USER_ACTION"
DEC_ERROR = "ERROR_WITH_REPRO_COMMAND"
ALLOWED_DECISIONS = (DEC_READY, DEC_HARD_BLOCKER, DEC_ERROR)
FORBIDDEN_DECISIONS = (
    "LIVE_TRADING_READY", "ORDER_READY", "AUTOMATION_READY", "AUTO_APPROVED",
    "AUTO_APPROVED_READY", "STRONG_ALPHA_FOUND_READY_FOR_REVIEW", "MISSING_KEY",
    "NO_DATA", "NEEDS_PROVIDER", "EMPTY_PAYLOAD", "ERROR",
)

# --- paths ----------------------------------------------------------------- #
_DEFAULT_BOOK = (_REPO_ROOT / "research" / "output" / "phase10f_owned_sector_mapping_repair"
                 / "reranked_paper_review_long_short_book.csv")
_DEFAULT_OUT = _REPO_ROOT / "research" / "output" / "phase10g_review_template"

_TEMPLATE_CSV = "repaired_book_review_template.csv"
_MANIFEST_JSON = "review_template_manifest.json"

# Context columns carried READ-ONLY from the 10-F-A book (provenance), in this order.
_CTX_COLS = [
    "side", "rank_sn", "ticker", "sector", "sector_repaired", "sector_is_unknown",
    "comp_sn", "comp_raw", "cohort", "liquidity_proxy", "before_review_label",
]
# Reviewer-input columns the human fills in (ship blank except the default decision).
_REVIEW_COLS = ["review_decision", "reviewer", "reviewed_at", "conviction", "reviewer_notes"]
# Fixed safety / non-action columns.
_SAFETY_COLS = ["approved", "order_action", "review_status"]

TEMPLATE_HEADER = _CTX_COLS + _REVIEW_COLS + _SAFETY_COLS

_COLUMN_LEGEND = {
    "side": "LONG or SHORT - the book side this candidate sits on (read-only).",
    "rank_sn": "Sector-neutral rank within the side (read-only).",
    "ticker": "Candidate ticker (read-only).",
    "sector": "Repaired GICS sector used for sector-neutral scoring (read-only).",
    "sector_repaired": "True if Phase 10-F-A repaired this name's sector from owned metadata (read-only).",
    "sector_is_unknown": "True only if the sector is still Unknown after repair (read-only; expected False).",
    "comp_sn": "Sector-neutral composite score = the review score (read-only).",
    "comp_raw": "Raw (sector-independent) composite score, for sanity (read-only).",
    "cohort": "old/new cohort the name belongs to (read-only).",
    "liquidity_proxy": "Dollar-liquidity proxy (read-only).",
    "before_review_label": "Side label from the 10-E book before the 10-F-A rerank (read-only).",
    "review_decision": "HUMAN-EDITABLE. Ships NEEDS_REVIEW for every row. Set to one of "
                       + " / ".join(ALLOWED_HUMAN_DECISIONS) + ".",
    "reviewer": "HUMAN-EDITABLE. Who reviewed (blank on generation).",
    "reviewed_at": "HUMAN-EDITABLE. Review date (blank on generation).",
    "conviction": "HUMAN-EDITABLE. Optional conviction note, e.g. HIGH/MED/LOW (blank on generation).",
    "reviewer_notes": "HUMAN-EDITABLE. Free-text rationale (blank on generation).",
    "approved": "FALSE for every row at generation. The template never pre-approves anything.",
    "order_action": "NO_ORDER for every row. This template never implies or creates an order.",
    "review_status": "PAPER_REVIEW_ONLY for every row.",
}


def _load_book(book_csv: Path) -> List[Dict]:
    """Read the 10-F-A repaired long/short book (owned, local). Never fabricates rows."""
    return _read_csv_file(book_csv)


def _build_template_rows(book_rows: List[Dict]) -> List[List]:
    """One template row per long/short candidate, every one defaulted to NEEDS_REVIEW."""
    out: List[List] = []
    for r in book_rows:
        side = (r.get("side") or "").strip().upper()
        if side not in (SIDE_LONG, SIDE_SHORT):
            continue  # only long/short candidates belong in the review book
        ctx = [r.get(c, "") for c in _CTX_COLS]
        review = [DEFAULT_DECISION, "", "", "", ""]          # decision defaults to NEEDS_REVIEW; rest blank
        safety = [APPROVED_AT_GENERATION, ORDER_ACTION, REVIEW_STATUS]
        out.append(ctx + review + safety)
    return out


def _counts(rows: List[List]) -> Dict[str, int]:
    side_idx = TEMPLATE_HEADER.index("side")
    dec_idx = TEMPLATE_HEADER.index("review_decision")
    n_long = sum(1 for r in rows if str(r[side_idx]).strip().upper() == SIDE_LONG)
    n_short = sum(1 for r in rows if str(r[side_idx]).strip().upper() == SIDE_SHORT)
    n_needs_review = sum(1 for r in rows if r[dec_idx] == DEFAULT_DECISION)
    return {"n_candidates": len(rows), "n_long": n_long, "n_short": n_short,
            "n_needs_review": n_needs_review}


def _manifest(book_csv: Path, out_dir: Path, rows: List[List], counts: Dict[str, int],
              decision: str) -> Dict:
    return {
        "phase": PHASE,
        "decision": decision,
        "allowed_decisions": list(ALLOWED_DECISIONS),
        "forbidden_decisions": list(FORBIDDEN_DECISIONS),
        "purpose": ("human approve/reject review template for the 10-F-A repaired long/short book; "
                    "every candidate defaults to NEEDS_REVIEW; no auto-approval; no Paper Trader "
                    "writes; no orders; no automation"),
        "source_book": _rel(book_csv),
        "template_csv": _rel(out_dir / _TEMPLATE_CSV),
        "n_candidates": counts["n_candidates"],
        "n_long": counts["n_long"],
        "n_short": counts["n_short"],
        # --- the core guarantees the user asked for, made machine-checkable ---
        "default_decision": DEFAULT_DECISION,
        "n_needs_review": counts["n_needs_review"],
        "all_default_needs_review": counts["n_needs_review"] == counts["n_candidates"]
        and counts["n_candidates"] > 0,
        "allowed_human_decisions": list(ALLOWED_HUMAN_DECISIONS),
        "auto_approval": False,
        "n_approved_at_generation": 0,
        "order_action_all": ORDER_ACTION,
        "review_status_all": REVIEW_STATUS,
        # --- standing safety flags ---
        "performs_network": PERFORMS_NETWORK,
        "offline": True,
        "uses_owned_data_only": True,
        "performs_provider_acquisition": False,
        "fabricated_candidates": False,
        "creates_paper_trader_signals": False,
        "creates_trade_decisions": False,
        "creates_orders": False,
        "creates_automation": False,
        "wrote_to_paper_trader": False,
        "live_trading": False,
        "broker_connected": False,
        "deploy": False,
        "api_key_printed": False,
        "api_key_written_to_disk": False,
        "safety_badges": SAFETY_BADGES,
        "template_columns": list(TEMPLATE_HEADER),
        "column_legend": _COLUMN_LEGEND,
        "next_step_for_human": ("open repaired_book_review_template.csv, set review_decision per row "
                                "(APPROVE / REJECT / HOLD_FOR_MORE_INFO); nothing downstream changes "
                                "until a human acts in a separately-gated step"),
        "exact_next_command": ("review research/output/phase10g_review_template/"
                               "repaired_book_review_template.csv"),
        "constraints_honored": [
            "offline (no network/key/provider probe)", "reads only the owned 10-F-A repaired book",
            "every candidate defaults to NEEDS_REVIEW", "NO auto-approval",
            "no Paper Trader writes", "NO orders", "NO automation", "NO live trading", "NO broker",
            "no deploy", "no GCP", "no package install", "no full regression",
            "no fabricated candidates", "no key printed/written", "no commit", "no push",
        ],
    }


def _print_summary(report: Dict) -> None:
    c = report
    print(
        f"[{PHASE}] decision={c['decision']} | candidates={c.get('n_candidates', 0)} "
        f"(long={c.get('n_long', 0)} short={c.get('n_short', 0)}) | "
        f"default={DEFAULT_DECISION} all_needs_review={c.get('all_default_needs_review')} | "
        f"auto_approval={c.get('auto_approval')} orders={c.get('creates_orders')} "
        f"automation={c.get('creates_automation')} wrote_pt={c.get('wrote_to_paper_trader')}"
    )


def _finish_blocker(out_dir: Path, book_csv: Path, decision: str, reason: str,
                    verbose: bool) -> Dict:
    report = {
        "phase": PHASE, "decision": decision, "allowed_decisions": list(ALLOWED_DECISIONS),
        "forbidden_decisions": list(FORBIDDEN_DECISIONS), "reason": reason,
        "source_book": _rel(book_csv), "n_candidates": 0, "n_long": 0, "n_short": 0,
        "all_default_needs_review": False, "auto_approval": False, "creates_orders": False,
        "creates_automation": False, "wrote_to_paper_trader": False,
        "exact_next_command": (f"python research/run_phase10g_review_template.py "
                               f"--book \"{_rel(book_csv)}\""),
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    _write_json(out_dir / _MANIFEST_JSON, report)
    if verbose:
        _print_summary(report)
    return report


def run(book_csv: Optional[Path] = None, out_dir: Optional[Path] = None, *,
        verbose: bool = True) -> Dict:
    book_csv = Path(book_csv) if book_csv else _DEFAULT_BOOK
    out_dir = Path(out_dir) if out_dir else _DEFAULT_OUT

    if not book_csv.exists():
        return _finish_blocker(
            out_dir, book_csv, DEC_HARD_BLOCKER,
            f"repaired long/short book not found at {_rel(book_csv)}; run Phase 10-F-A first",
            verbose)

    book_rows = _load_book(book_csv)
    rows = _build_template_rows(book_rows)
    if not rows:
        return _finish_blocker(
            out_dir, book_csv, DEC_HARD_BLOCKER,
            f"no LONG/SHORT candidates found in {_rel(book_csv)}", verbose)

    counts = _counts(rows)
    out_dir.mkdir(parents=True, exist_ok=True)
    _write_csv(out_dir / _TEMPLATE_CSV, TEMPLATE_HEADER, rows)
    report = _manifest(book_csv, out_dir, rows, counts, DEC_READY)
    _write_json(out_dir / _MANIFEST_JSON, report)
    if verbose:
        _print_summary(report)
    return report


def _parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Build a human review template (all NEEDS_REVIEW) for the "
                                            "10-F-A repaired long/short book.")
    p.add_argument("--book", default=None, help="path to the repaired long/short book CSV "
                                                "(default: the 10-F-A output)")
    p.add_argument("--out", default=None, help="output directory (default: research/output/"
                                               "phase10g_review_template)")
    p.add_argument("--quiet", action="store_true")
    return p.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    ns = _parse_args(argv)
    report = run(book_csv=ns.book, out_dir=ns.out, verbose=not ns.quiet)
    return 0 if report.get("decision") in ALLOWED_DECISIONS else 1


if __name__ == "__main__":
    raise SystemExit(main())
