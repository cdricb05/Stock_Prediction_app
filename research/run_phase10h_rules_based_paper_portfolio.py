"""Phase 10-H - Rules-Based Paper Portfolio Constructor.

WHY THIS PHASE EXISTS
    Phase 10-G produced a 194-name review template (97 long / 97 short, all NEEDS_REVIEW). The user does
    NOT want to hand-review 194 tickers. The right operating model is: the user approves the CONSTRUCTION
    RULES (and a short list of exceptions), not every ticker. This phase builds a first paper-only
    long/short portfolio from the 10-F-A repaired book using transparent, fixed rules, and surfaces the
    handful of names the rules excluded or flagged so the human reviews RULES + EXCEPTIONS, not 194 names.

    NOT a new alpha search. NOT a provider search. NOT manual review of 194 tickers. NOT a Paper Trader
    integration. NOT order creation. NOT automation. NOT a deploy. Fully offline (no network, no API key,
    no provider probe). Output is metadata-only CSV/JSON in this phase's own research/output directory.

DEFAULT CONSTRUCTION RULES (the thing the user approves)
    1.  Rank within each side by the 10-D SECTOR-NEUTRAL composite (comp_sn).
    2.  Select up to 25 longs and 25 shorts.
    3.  Exclude bottom-quartile liquidity names (below the 25th percentile of liquidity_proxy).
    4.  Exclude names with a missing sector.
    5.  Exclude names with a missing required composite input (fcf_to_assets / operating_accruals).
    6.  Cap each sector at 25% of each side (=> at most floor(0.25 * 25) = 6 names per sector per side).
    7.  Equal-weight the long side.
    8.  Equal-weight the short side.
    9.  Gross exposure 100% long / 100% short in paper terms (dollar-neutral net, 200% gross).
    10. Quarterly rebalance cadence.
    11. No optimised weights.
    12. No discretionary per-ticker approval required.
    13. Flag - but do NOT auto-include - extreme-score candidates (|comp_sn_z| >= 3.0); they are held out
        for an explicit human exception rather than silently included.
    14. If fewer than 25 per side pass the filters, produce the smaller valid book and explain why.

TERMINAL DECISIONS (allowed)
    RULES_BASED_PAPER_PORTFOLIO_READY_FOR_RULE_APPROVAL | RULES_BASED_PAPER_PORTFOLIO_READY_WITH_EXCEPTIONS
    | RULES_BASED_PAPER_PORTFOLIO_BLOCKED_TOO_FEW_CANDIDATES | HARD_BLOCKER_REQUIRES_USER_ACTION |
    ERROR_WITH_REPRO_COMMAND
    FORBIDDEN: LIVE_TRADING_READY, ORDER_READY, AUTOMATION_READY, PAPER_TRADER_READY,
    STRONG_ALPHA_FOUND_READY_FOR_REVIEW, MISSING_KEY, NO_DATA, NEEDS_PROVIDER, EMPTY_PAYLOAD,
    generic ERROR.

CONSTRAINTS HONORED
    Offline (no network/key/provider probe); reads only the owned 10-F-A artifacts; no FMP/AlphaVantage/
    Polygon/Finnhub/Norgate-API; no new purchase; no Paper Trader writes; no Paper Trader signals; no
    trade decisions; NO orders; NO automation; NO broker; NO live trading; no deploy; no GCP; no package
    install; no full regression (targeted tests only); keys never printed or written; output is metadata
    only. No commit. No push.
"""
from __future__ import annotations

import argparse
import math
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from research import run_phase8s_autonomous_eodhd_alpha_factory as s8            # noqa: E402  io helpers
from research import run_phase10e_quarterly_quality_paper_review_harness as e10  # noqa: E402  badges/status
from research import run_phase10b_eodhd_norgate_exhaustive_alpha_factory as b10  # noqa: E402  secret audit

_write_json = s8._write_json
_write_csv = s8._write_csv
_read_csv_file = s8._read_csv_file
_read_json = s8._read_json
_rel = s8._rel
_finite = b10._finite

PHASE = "10-H"
PERFORMS_NETWORK = False

# --------------------------------------------------------------------------- #
# DEFAULT CONSTRUCTION RULES - explicit, transparent, human-approvable.
# --------------------------------------------------------------------------- #
RANK_SCORE = "comp_sn"                 # 10-D sector-neutral composite
TARGET_PER_SIDE = 25                   # rule 2: up to 25 longs / 25 shorts
LIQUIDITY_EXCLUDE_PCTILE = 25.0        # rule 3: bottom-quartile liquidity dropped
SECTOR_CAP_FRAC = 0.25                 # rule 6: <= 25% of each side per sector
EXTREME_Z = 3.0                        # rule 13: |comp_sn_z| >= 3 held out, not auto-included
WEIGHTING = "EQUAL"                    # rules 7-8, 11: equal weight, no optimisation
GROSS_LONG_PCT = 100.0                 # rule 9
GROSS_SHORT_PCT = 100.0               # rule 9
REBALANCE_CADENCE = "QUARTERLY"        # rule 10
MIN_VIABLE_PER_SIDE = 3                # below this a side is too thin to be a paper book at all

ORDER_ACTION = "NO_ORDER"              # explicit on every row: this is paper-only, never an order
REVIEW_STATUS = getattr(e10, "REVIEW_STATUS", "PAPER_REVIEW_ONLY")
SIDE_LONG = getattr(e10, "SIDE_LONG", "LONG")
SIDE_SHORT = getattr(e10, "SIDE_SHORT", "SHORT")
SAFETY_BADGES = list(getattr(e10, "SAFETY_BADGES", [
    "PAPER REVIEW ONLY", "NO ORDERS", "NO AUTOMATION", "HUMAN APPROVAL REQUIRED",
    "NO LIVE TRADING", "NO BROKER", "CREATES NO TRADE DECISIONS", "MANUAL REVIEW",
])) + ["RULES-BASED", "NO PER-TICKER APPROVAL"]

# --------------------------------------------------------------------------- #
# Exclusion reasons (priority order, first match wins for primary_reason).
# --------------------------------------------------------------------------- #
R_MISSING_SECTOR = "missing_sector"
R_MISSING_INPUT = "missing_composite_input"
R_LOW_LIQ = "bottom_quartile_liquidity"
R_EXTREME = "extreme_score_flagged"
R_SECTOR_CAP = "sector_cap"
R_BELOW_CUTOFF = "below_selection_cutoff"
# the subset that counts as an "exception" needing human attention (rule + exceptions model)
_EXCEPTION_REASONS = (R_MISSING_SECTOR, R_MISSING_INPUT, R_LOW_LIQ, R_EXTREME, R_SECTOR_CAP)

# --------------------------------------------------------------------------- #
# Terminal decisions.
# --------------------------------------------------------------------------- #
DEC_READY = "RULES_BASED_PAPER_PORTFOLIO_READY_FOR_RULE_APPROVAL"
DEC_WITH_EXC = "RULES_BASED_PAPER_PORTFOLIO_READY_WITH_EXCEPTIONS"
DEC_TOO_FEW = "RULES_BASED_PAPER_PORTFOLIO_BLOCKED_TOO_FEW_CANDIDATES"
DEC_HARD_BLOCKER = "HARD_BLOCKER_REQUIRES_USER_ACTION"
DEC_ERROR = "ERROR_WITH_REPRO_COMMAND"
ALLOWED_DECISIONS = (DEC_READY, DEC_WITH_EXC, DEC_TOO_FEW, DEC_HARD_BLOCKER, DEC_ERROR)
FORBIDDEN_DECISIONS = (
    "LIVE_TRADING_READY", "ORDER_READY", "AUTOMATION_READY", "PAPER_TRADER_READY",
    "STRONG_ALPHA_FOUND_READY_FOR_REVIEW", "MISSING_KEY", "NO_DATA", "NEEDS_PROVIDER",
    "EMPTY_PAYLOAD", "ERROR",
)

# --------------------------------------------------------------------------- #
# Input / output paths.
# --------------------------------------------------------------------------- #
_F10_DIR = _REPO_ROOT / "research" / "output" / "phase10f_owned_sector_mapping_repair"
_DEF_BOOK = _F10_DIR / "reranked_paper_review_long_short_book.csv"
_DEF_CAND = _F10_DIR / "reranked_paper_review_candidate_list.csv"
_DEF_RISK = _F10_DIR / "repaired_book_risk_flags.csv"
_DEF_F10_JSON = _F10_DIR / "phase10f_owned_sector_mapping_repair.json"
_DEFAULT_OUT = _REPO_ROOT / "research" / "output" / "phase10h_rules_based_paper_portfolio"

_ARTIFACTS = {
    "report": "phase10h_rules_based_paper_portfolio.json",
    "portfolio": "selected_paper_portfolio.csv",
    "long": "selected_long_book.csv",
    "short": "selected_short_book.csv",
    "excluded": "excluded_candidates.csv",
    "rules": "portfolio_construction_rules.csv",
    "sector": "portfolio_sector_exposure.csv",
    "liquidity": "portfolio_liquidity_summary.csv",
    "balance": "portfolio_long_short_balance.csv",
    "exceptions": "portfolio_exceptions_report.csv",
    "checklist": "rule_approval_checklist.csv",
    "next_plan": "phase10i_next_plan.json",
    "secret_audit": "secret_safety_audit.csv",
}


# --------------------------------------------------------------------------- #
# Small numeric helpers (pure python - no numpy dependency, deterministic).
# --------------------------------------------------------------------------- #
def _to_float(x) -> Optional[float]:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(v) or math.isinf(v) else v


def _percentile(values: Sequence[float], pct: float) -> Optional[float]:
    """Linear-interpolation percentile (matches numpy's default 'linear' method)."""
    vals = sorted(v for v in values if _finite(v))
    if not vals:
        return None
    if len(vals) == 1:
        return vals[0]
    k = (len(vals) - 1) * (pct / 100.0)
    lo = math.floor(k)
    hi = math.ceil(k)
    if lo == hi:
        return vals[int(k)]
    return vals[lo] * (hi - k) + vals[hi] * (k - lo)


def _round(x, n=6):
    v = _to_float(x)
    return None if v is None else round(v, n)


def _is_unknown_sector(sec: str) -> bool:
    return (sec or "").strip() == "" or (sec or "").strip().lower() == "unknown"


def _sector_cap_count(target: int) -> int:
    return max(1, math.floor(target * SECTOR_CAP_FRAC))


def _next_quarter_end(as_of: date) -> date:
    """End of the calendar quarter AFTER the as-of quarter (quarterly rebalance target)."""
    q = (as_of.month - 1) // 3          # 0..3
    nq = q + 1
    year = as_of.year + nq // 4
    nq = nq % 4
    end_month = nq * 3 + 3              # -> 3, 6, 9, 12
    if end_month == 12:
        return date(year, 12, 31)
    return date(year, end_month + 1, 1) - timedelta(days=1)  # last day of end_month


# --------------------------------------------------------------------------- #
# Load + join the owned 10-F-A artifacts into a per-name candidate record.
# --------------------------------------------------------------------------- #
def _load_inputs(book_csv: Path, cand_csv: Path, risk_csv: Path, f10_json: Path
                 ) -> Tuple[List[Dict], Dict[str, Dict], Dict[str, Dict], Dict]:
    book = _read_csv_file(book_csv)
    cand = {r.get("ticker"): r for r in _read_csv_file(cand_csv)}
    risk = {r.get("ticker"): r for r in _read_csv_file(risk_csv)}
    meta = _read_json(f10_json) if f10_json.exists() else {}
    return book, cand, risk, meta


def _build_records(book: List[Dict], cand: Dict[str, Dict], risk: Dict[str, Dict]) -> List[Dict]:
    """One record per long/short book name, enriched with composite inputs + z-score."""
    recs: List[Dict] = []
    for r in book:
        side = (r.get("side") or "").strip().upper()
        if side not in (SIDE_LONG, SIDE_SHORT):
            continue
        tk = r.get("ticker")
        c = cand.get(tk, {})
        rk = risk.get(tk, {})
        recs.append({
            "ticker": tk,
            "side": side,
            "rank_sn": r.get("rank_sn"),
            "comp_sn": _to_float(r.get("comp_sn")),
            "comp_raw": _to_float(r.get("comp_raw")),
            "comp_sn_z": _to_float(c.get("comp_sn_z")),
            "sector": (r.get("sector") or "").strip(),
            "sector_is_unknown": str(r.get("sector_is_unknown", "")).strip().lower() == "true",
            "sector_repaired": str(r.get("sector_repaired", "")).strip().lower() == "true",
            "cohort": r.get("cohort"),
            "liquidity_proxy": _to_float(r.get("liquidity_proxy")),
            "fcf_to_assets": _to_float(c.get("fcf_to_assets")),
            "operating_accruals": _to_float(c.get("operating_accruals")),
            # 10-F-A's own flags, carried for cross-validation only (we apply our OWN rules below)
            "f10_low_liquidity": str(rk.get("low_liquidity", "")).strip().lower() == "true",
            "f10_extreme_score": str(rk.get("extreme_score", "")).strip().lower() == "true",
            "f10_missing_leg": str(rk.get("missing_leg", "")).strip().lower() == "true",
        })
    return recs


# --------------------------------------------------------------------------- #
# Apply the rules: filter -> rank -> sector-capped greedy fill -> equal weight.
# --------------------------------------------------------------------------- #
def _flag_record(rec: Dict, liq_threshold: Optional[float]) -> Dict:
    sec_missing = rec["sector_is_unknown"] or _is_unknown_sector(rec["sector"])
    input_missing = not _finite(rec["fcf_to_assets"]) or not _finite(rec["operating_accruals"])
    low_liq = (liq_threshold is not None and _finite(rec["liquidity_proxy"])
               and rec["liquidity_proxy"] < liq_threshold)
    extreme = _finite(rec["comp_sn_z"]) and abs(rec["comp_sn_z"]) >= EXTREME_Z
    flags = {"missing_sector": sec_missing, "missing_input": input_missing,
             "low_liquidity": bool(low_liq), "extreme_score": bool(extreme)}
    flags["eligible"] = not any(flags.values())
    return flags


def _primary_ineligible_reason(flags: Dict) -> Optional[str]:
    if flags["missing_sector"]:
        return R_MISSING_SECTOR
    if flags["missing_input"]:
        return R_MISSING_INPUT
    if flags["low_liquidity"]:
        return R_LOW_LIQ
    if flags["extreme_score"]:
        return R_EXTREME
    return None


def _select_side(recs: List[Dict], side: str, target: int, cap: int, liq_threshold: Optional[float]
                 ) -> Tuple[List[Dict], List[Dict]]:
    """Return (selected, evaluated) for one side. `evaluated` carries flags + reason for every name."""
    side_recs = [r for r in recs if r["side"] == side]
    # rank: longs want the HIGHEST comp_sn, shorts the LOWEST (most negative).
    reverse = side == SIDE_LONG
    side_recs.sort(key=lambda r: ((r["comp_sn"] if r["comp_sn"] is not None else
                                   (-1e18 if reverse else 1e18)), r["ticker"]),
                   reverse=reverse)

    evaluated: List[Dict] = []
    for r in side_recs:
        evaluated.append({**r, **_flag_record(r, liq_threshold)})

    selected: List[Dict] = []
    sector_count: Dict[str, int] = {}
    for ev in evaluated:
        if not ev["eligible"]:
            ev["selected"] = False
            ev["primary_reason"] = _primary_ineligible_reason(ev)
            continue
        if len(selected) >= target:
            ev["selected"] = False
            ev["primary_reason"] = R_BELOW_CUTOFF        # eligible but ranked out / book full
            continue
        sec = ev["sector"]
        if sector_count.get(sec, 0) >= cap:
            ev["selected"] = False
            ev["primary_reason"] = R_SECTOR_CAP          # eligible but sector already at the 25% cap
            continue
        sector_count[sec] = sector_count.get(sec, 0) + 1
        ev["selected"] = True
        ev["primary_reason"] = None
        ev["rank_in_side"] = len(selected) + 1
        selected.append(ev)
    return selected, evaluated


# --------------------------------------------------------------------------- #
# Artifact builders.
# --------------------------------------------------------------------------- #
def _weight(n: int) -> float:
    return round(1.0 / n, 6) if n else 0.0


def _portfolio_rows(selected: List[Dict], n_by_side: Dict[str, int]) -> List[List]:
    rows = []
    for ev in selected:
        n = n_by_side[ev["side"]]
        w = _weight(n)
        gross = GROSS_LONG_PCT if ev["side"] == SIDE_LONG else GROSS_SHORT_PCT
        rows.append([ev["side"], ev["rank_in_side"], ev["ticker"], ev["sector"],
                     _round(ev["comp_sn"], 5), _round(ev["comp_sn_z"], 4),
                     _round(ev["liquidity_proxy"], 2), ev["cohort"],
                     w, round(w * 100.0, 4), gross, WEIGHTING, REVIEW_STATUS, ORDER_ACTION])
    return rows


_PORTFOLIO_HEADER = ["side", "rank_in_side", "ticker", "sector", "comp_sn", "comp_sn_z",
                     "liquidity_proxy", "cohort", "target_weight", "target_weight_pct",
                     "side_gross_pct", "weighting", "review_status", "order_action"]


def _excluded_rows(evaluated: List[Dict]) -> List[List]:
    rows = []
    for ev in evaluated:
        if ev.get("selected"):
            continue
        rows.append([ev["side"], ev["ticker"], ev["sector"], _round(ev["comp_sn"], 5),
                     _round(ev["comp_sn_z"], 4), _round(ev["liquidity_proxy"], 2),
                     ev.get("primary_reason"), ev["missing_sector"], ev["missing_input"],
                     ev["low_liquidity"], ev["extreme_score"],
                     ev.get("primary_reason") == R_SECTOR_CAP, ev["eligible"]])
    return rows


_EXCLUDED_HEADER = ["side", "ticker", "sector", "comp_sn", "comp_sn_z", "liquidity_proxy",
                    "primary_reason", "flag_missing_sector", "flag_missing_input",
                    "flag_low_liquidity", "flag_extreme_score", "flag_sector_cap", "was_eligible"]


def _exception_rows(evaluated: List[Dict]) -> List[List]:
    rows = []
    for ev in evaluated:
        reason = ev.get("primary_reason")
        if ev.get("selected") or reason not in _EXCEPTION_REASONS:
            continue
        detail = {
            R_LOW_LIQ: "liquidity below the 25th-percentile threshold",
            R_SECTOR_CAP: "sector already at the 25%-of-side cap",
            R_EXTREME: f"|comp_sn_z| >= {EXTREME_Z} (extreme score held out, not auto-included)",
            R_MISSING_SECTOR: "sector missing/Unknown",
            R_MISSING_INPUT: "missing fcf_to_assets or operating_accruals",
        }.get(reason, reason)
        rows.append([ev["side"], ev["ticker"], ev["sector"], _round(ev["comp_sn"], 5),
                     _round(ev["comp_sn_z"], 4), reason, detail, "HELD_OUT_NEEDS_RULE_EXCEPTION"])
    return rows


_EXCEPTION_HEADER = ["side", "ticker", "sector", "comp_sn", "comp_sn_z", "exception_type",
                     "detail", "action"]


def _sector_exposure_rows(selected: List[Dict], n_by_side: Dict[str, int], cap_pct: float
                          ) -> Tuple[List[List], float]:
    rows = []
    largest = 0.0
    for side in (SIDE_LONG, SIDE_SHORT):
        n = n_by_side[side]
        counts: Dict[str, int] = {}
        for ev in selected:
            if ev["side"] == side:
                counts[ev["sector"]] = counts.get(ev["sector"], 0) + 1
        for sec, c in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
            share = round(100.0 * c / n, 4) if n else 0.0
            largest = max(largest, share)
            rows.append([side, sec, c, share, cap_pct, share <= cap_pct + 1e-9])
    return rows, round(largest, 4)


_SECTOR_HEADER = ["side", "sector", "n_names", "weight_pct", "sector_cap_pct", "within_cap"]


def _liquidity_rows(selected: List[Dict], evaluated_all: List[Dict], n_by_side: Dict[str, int],
                    liq_threshold: Optional[float]) -> List[List]:
    rows = []
    for side in (SIDE_LONG, SIDE_SHORT):
        sel_liq = [ev["liquidity_proxy"] for ev in selected
                   if ev["side"] == side and _finite(ev["liquidity_proxy"])]
        n_excl = sum(1 for ev in evaluated_all
                     if ev["side"] == side and ev["low_liquidity"])
        rows.append([
            side, n_by_side[side],
            _round(min(sel_liq), 2) if sel_liq else None,
            _round(_percentile(sel_liq, 50), 2) if sel_liq else None,
            _round(max(sel_liq), 2) if sel_liq else None,
            _round(liq_threshold, 2), LIQUIDITY_EXCLUDE_PCTILE, n_excl,
        ])
    return rows


_LIQUIDITY_HEADER = ["side", "n_selected", "min_liquidity_proxy", "median_liquidity_proxy",
                     "max_liquidity_proxy", "bottom_quartile_threshold", "exclude_pctile",
                     "n_excluded_by_liquidity"]


def _balance_rows(n_long: int, n_short: int) -> List[List]:
    return [[
        n_long, n_short, GROSS_LONG_PCT, GROSS_SHORT_PCT,
        round(GROSS_LONG_PCT - GROSS_SHORT_PCT, 4),          # net (paper) = 0 when sides balanced
        round(GROSS_LONG_PCT + GROSS_SHORT_PCT, 4),          # gross = 200%
        WEIGHTING, n_long == n_short, "dollar-neutral paper book; net 0%, gross 200%",
    ]]


_BALANCE_HEADER = ["n_long", "n_short", "long_gross_pct", "short_gross_pct", "net_pct", "gross_pct",
                   "weighting", "sides_balanced", "note"]


def _rules_rows(liq_threshold: Optional[float], cap_count: int) -> List[List]:
    R = [
        ("1", "ranking", "score", RANK_SCORE, "rank within side by the 10-D sector-neutral composite"),
        ("2", "book_size", "max_per_side", TARGET_PER_SIDE, "up to 25 longs and 25 shorts"),
        ("3", "liquidity_filter", "exclude_below_pctile", LIQUIDITY_EXCLUDE_PCTILE,
         f"drop bottom-quartile liquidity (threshold liquidity_proxy={_round(liq_threshold, 2)})"),
        ("4", "sector_filter", "drop_missing_sector", True, "exclude names with a missing/Unknown sector"),
        ("5", "input_filter", "require", "fcf_to_assets+operating_accruals",
         "exclude names missing a required composite input"),
        ("6", "sector_cap", "max_pct_per_side", round(SECTOR_CAP_FRAC * 100, 2),
         f"cap each sector at 25% of each side (= {cap_count} names per sector per side)"),
        ("7", "long_weighting", "scheme", WEIGHTING, "equal-weight the long side"),
        ("8", "short_weighting", "scheme", WEIGHTING, "equal-weight the short side"),
        ("9", "gross_exposure", "long/short_pct", f"{GROSS_LONG_PCT}/{GROSS_SHORT_PCT}",
         "100% long / 100% short in paper terms (net 0%, gross 200%)"),
        ("10", "rebalance", "cadence", REBALANCE_CADENCE, "quarterly rebalance"),
        ("11", "weights", "optimised", False, "no optimised weights"),
        ("12", "approval_model", "per_ticker_approval", False,
         "approve the RULES + exceptions, not 194 tickers"),
        ("13", "extreme_handling", "flag_threshold_abs_z", EXTREME_Z,
         "flag extreme-score names and HOLD them out (do not auto-include)"),
        ("14", "underfill", "behaviour", "smaller_valid_book",
         "if <25 per side pass filters, produce the smaller book and explain why"),
    ]
    return [[rid, name, param, val, why] for (rid, name, param, val, why) in R]


_RULES_HEADER = ["rule_id", "rule", "parameter", "value", "rationale"]


def _checklist_rows() -> List[List]:
    items = [
        ("approve_book_size", f"Approve up to {TARGET_PER_SIDE} long / {TARGET_PER_SIDE} short",
         f"{TARGET_PER_SIDE}/{TARGET_PER_SIDE}"),
        ("approve_liquidity_filter", "Approve excluding bottom-quartile liquidity names",
         f"below p{int(LIQUIDITY_EXCLUDE_PCTILE)} liquidity_proxy"),
        ("approve_sector_cap", "Approve capping each sector per side",
         f"{int(SECTOR_CAP_FRAC * 100)}% of side"),
        ("approve_equal_weighting", "Approve equal weighting on both sides (no optimisation)", WEIGHTING),
        ("approve_quarterly_cadence", "Approve quarterly rebalance cadence", REBALANCE_CADENCE),
        ("confirm_paper_only", "Confirm this is paper-only (no broker, no live trading)", "PAPER_ONLY"),
        ("confirm_no_orders_automation", "Confirm no orders and no automation are created", "NONE"),
    ]
    return [[k, desc, setting, "NEEDS_APPROVAL"] for (k, desc, setting) in items]


_CHECKLIST_HEADER = ["item", "description", "current_setting", "status"]


# --------------------------------------------------------------------------- #
# Decision.
# --------------------------------------------------------------------------- #
def _decide(n_long: int, n_short: int, n_exceptions: int, target: int) -> str:
    if n_long < MIN_VIABLE_PER_SIDE or n_short < MIN_VIABLE_PER_SIDE:
        return DEC_TOO_FEW
    if n_exceptions > 0 or n_long < target or n_short < target:
        return DEC_WITH_EXC
    return DEC_READY


def _phase10i_plan(decision: str) -> Dict:
    return {
        "phase": "10-I (planned)",
        "title": "Human rule-approval gate + paper-only position tracker",
        "depends_on": decision,
        "steps": [
            "human signs off rule_approval_checklist.csv (book size, liquidity, sector cap, "
            "equal weighting, quarterly cadence, paper-only, no orders/automation)",
            "human resolves portfolio_exceptions_report.csv (optionally add back held-out extreme/"
            "liquidity/sector-cap names via explicit exception)",
            "on approval, build a PAPER-ONLY position tracker for the selected book "
            "(mark-to-market quarterly; realised vs expected net-25bps) - still NO orders/automation",
        ],
        "still_forbidden": ["orders", "automation", "broker", "live trading", "deploy",
                            "Paper Trader writes", "new data purchase"],
        "exact_next_command": ("review research/output/phase10h_rules_based_paper_portfolio/"
                               "rule_approval_checklist.csv"),
    }


# --------------------------------------------------------------------------- #
# Report assembly + run.
# --------------------------------------------------------------------------- #
def _print_summary(rep: Dict) -> None:
    print(
        f"[{PHASE}] decision={rep['decision']} | long={rep['n_long']} short={rep['n_short']} "
        f"excluded={rep['excluded_count']} | liq_excl={rep['liquidity_filter']['n_excluded_total']} "
        f"(thr={rep['liquidity_filter']['threshold']}) | largest_sector={rep['largest_sector_share']}% | "
        f"exceptions={rep['exceptions_total']} | wrote_pt={rep['wrote_to_paper_trader']} "
        f"orders={rep['creates_orders']} automation={rep['creates_automation']} "
        f"leak_clean={rep['secret_safety_leak_scan_clean']}"
    )


def _finish_blocker(out_dir: Path, decision: str, reason: str, repro: str, verbose: bool) -> Dict:
    rep = {
        "phase": PHASE, "decision": decision, "allowed_decisions": list(ALLOWED_DECISIONS),
        "forbidden_decisions": list(FORBIDDEN_DECISIONS), "reason": reason,
        "n_long": 0, "n_short": 0, "excluded_count": 0,
        "liquidity_filter": {"threshold": None, "n_excluded_total": 0},
        "largest_sector_share": 0.0, "exceptions_total": 0,
        "wrote_to_paper_trader": False, "creates_orders": False, "creates_automation": False,
        "creates_paper_trader_signals": False, "creates_trade_decisions": False,
        "secret_safety_leak_scan_clean": True, "exact_next_command": repro,
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    _write_json(out_dir / _ARTIFACTS["report"], rep)
    if verbose:
        _print_summary(rep)
    return rep


def run(out_dir: Optional[Path] = None, *, book_csv: Optional[Path] = None,
        cand_csv: Optional[Path] = None, risk_csv: Optional[Path] = None,
        f10_json: Optional[Path] = None, target_per_side: int = TARGET_PER_SIDE,
        verbose: bool = True) -> Dict:
    out_dir = Path(out_dir) if out_dir else _DEFAULT_OUT
    book_csv = Path(book_csv) if book_csv else _DEF_BOOK
    cand_csv = Path(cand_csv) if cand_csv else _DEF_CAND
    risk_csv = Path(risk_csv) if risk_csv else _DEF_RISK
    f10_json = Path(f10_json) if f10_json else _DEF_F10_JSON

    if not book_csv.exists() or not cand_csv.exists():
        return _finish_blocker(
            out_dir, DEC_HARD_BLOCKER,
            f"required 10-F-A inputs missing (book={book_csv.exists()}, cand={cand_csv.exists()})",
            f"python research/run_phase10h_rules_based_paper_portfolio.py --book \"{_rel(book_csv)}\"",
            verbose)

    book, cand, risk, meta = _load_inputs(book_csv, cand_csv, risk_csv, f10_json)
    recs = _build_records(book, cand, risk)
    if not recs:
        return _finish_blocker(
            out_dir, DEC_HARD_BLOCKER, f"no LONG/SHORT candidates in {_rel(book_csv)}",
            f"python research/run_phase10h_rules_based_paper_portfolio.py", verbose)

    cap = _sector_cap_count(target_per_side)
    liq_threshold = _percentile([r["liquidity_proxy"] for r in recs], LIQUIDITY_EXCLUDE_PCTILE)

    sel_long, eval_long = _select_side(recs, SIDE_LONG, target_per_side, cap, liq_threshold)
    sel_short, eval_short = _select_side(recs, SIDE_SHORT, target_per_side, cap, liq_threshold)
    selected = sel_long + sel_short
    evaluated = eval_long + eval_short
    n_by_side = {SIDE_LONG: len(sel_long), SIDE_SHORT: len(sel_short)}

    # --- build artifact row sets ---
    portfolio_rows = _portfolio_rows(selected, n_by_side)
    long_rows = _portfolio_rows(sel_long, n_by_side)
    short_rows = _portfolio_rows(sel_short, n_by_side)
    excluded_rows = _excluded_rows(evaluated)
    exception_rows = _exception_rows(evaluated)
    sector_rows, largest_share = _sector_exposure_rows(selected, n_by_side,
                                                       round(SECTOR_CAP_FRAC * 100, 2))
    liquidity_rows = _liquidity_rows(selected, evaluated, n_by_side, liq_threshold)
    balance_rows = _balance_rows(len(sel_long), len(sel_short))
    rules_rows = _rules_rows(liq_threshold, cap)
    checklist_rows = _checklist_rows()

    # --- exception accounting ---
    exc_counts = {r: 0 for r in _EXCEPTION_REASONS}
    for ev in evaluated:
        if not ev.get("selected") and ev.get("primary_reason") in exc_counts:
            exc_counts[ev["primary_reason"]] += 1
    n_exceptions = sum(exc_counts.values())
    n_liq_excl = exc_counts[R_LOW_LIQ]

    decision = _decide(len(sel_long), len(sel_short), n_exceptions, target_per_side)

    # --- rebalance schedule ---
    as_of_str = (meta.get("as_of") or "2026-06-26")
    try:
        as_of_date = date.fromisoformat(as_of_str)
        reb = _next_quarter_end(as_of_date)
        rebalance_date = reb.isoformat()
        holding_days = (reb - as_of_date).days
    except ValueError:
        rebalance_date, holding_days = None, None

    # --- write artifacts ---
    out_dir.mkdir(parents=True, exist_ok=True)
    _write_csv(out_dir / _ARTIFACTS["portfolio"], _PORTFOLIO_HEADER, portfolio_rows)
    _write_csv(out_dir / _ARTIFACTS["long"], _PORTFOLIO_HEADER, long_rows)
    _write_csv(out_dir / _ARTIFACTS["short"], _PORTFOLIO_HEADER, short_rows)
    _write_csv(out_dir / _ARTIFACTS["excluded"], _EXCLUDED_HEADER, excluded_rows)
    _write_csv(out_dir / _ARTIFACTS["rules"], _RULES_HEADER, rules_rows)
    _write_csv(out_dir / _ARTIFACTS["sector"], _SECTOR_HEADER, sector_rows)
    _write_csv(out_dir / _ARTIFACTS["liquidity"], _LIQUIDITY_HEADER, liquidity_rows)
    _write_csv(out_dir / _ARTIFACTS["balance"], _BALANCE_HEADER, balance_rows)
    _write_csv(out_dir / _ARTIFACTS["exceptions"], _EXCEPTION_HEADER, exception_rows)
    _write_csv(out_dir / _ARTIFACTS["checklist"], _CHECKLIST_HEADER, checklist_rows)
    _write_json(out_dir / _ARTIFACTS["next_plan"], _phase10i_plan(decision))

    top_long_sector = next(((r[1], r[3]) for r in sector_rows if r[0] == SIDE_LONG), (None, None))
    top_short_sector = next(((r[1], r[3]) for r in sector_rows if r[0] == SIDE_SHORT), (None, None))

    report = {
        "phase": PHASE,
        "decision": decision,
        "decision_rationale": _decision_rationale(decision, len(sel_long), len(sel_short),
                                                 n_exceptions, target_per_side),
        "allowed_decisions": list(ALLOWED_DECISIONS),
        "forbidden_decisions": list(FORBIDDEN_DECISIONS),
        "objective": ("build a first paper-only long/short portfolio from the 10-F-A repaired book "
                      "using transparent rules, so the user reviews the rules and exceptions, not "
                      "194 tickers"),
        "source_book": _rel(book_csv),
        "source_candidate_list": _rel(cand_csv),
        "source_risk_flags": _rel(risk_csv),
        "as_of": as_of_str,
        "latest_quarter": meta.get("latest_quarter"),
        # --- selection result ---
        "n_universe_long_short": len(recs),
        "n_long": len(sel_long),
        "n_short": len(sel_short),
        "target_per_side": target_per_side,
        "excluded_count": len(excluded_rows),
        "rank_score": RANK_SCORE,
        "weighting": WEIGHTING,
        "optimised_weights": False,
        "per_ticker_approval_required": False,
        "long_weight_each": _weight(len(sel_long)),
        "short_weight_each": _weight(len(sel_short)),
        # --- filters ---
        "liquidity_filter": {
            "exclude_pctile": LIQUIDITY_EXCLUDE_PCTILE,
            "threshold": _round(liq_threshold, 2),
            "n_excluded_long": sum(1 for ev in eval_long if ev["primary_reason"] == R_LOW_LIQ),
            "n_excluded_short": sum(1 for ev in eval_short if ev["primary_reason"] == R_LOW_LIQ),
            "n_excluded_total": n_liq_excl,
            "applied": True,
        },
        "sector_cap": {"max_pct_per_side": round(SECTOR_CAP_FRAC * 100, 2), "max_names_per_sector": cap,
                       "applied": True},
        "largest_sector_share": largest_share,
        "top_long_sector": top_long_sector[0],
        "top_long_sector_share": top_long_sector[1],
        "top_short_sector": top_short_sector[0],
        "top_short_sector_share": top_short_sector[1],
        "sector_cap_respected": largest_share <= round(SECTOR_CAP_FRAC * 100, 2) + 1e-9,
        # --- exceptions ---
        "exceptions_total": n_exceptions,
        "exceptions_by_type": exc_counts,
        "extreme_score_threshold_abs_z": EXTREME_Z,
        # --- balance / exposure ---
        "long_gross_pct": GROSS_LONG_PCT,
        "short_gross_pct": GROSS_SHORT_PCT,
        "net_pct": round(GROSS_LONG_PCT - GROSS_SHORT_PCT, 4),
        "gross_pct": round(GROSS_LONG_PCT + GROSS_SHORT_PCT, 4),
        "sides_balanced": len(sel_long) == len(sel_short),
        # --- rebalance ---
        "rebalance_cadence": REBALANCE_CADENCE,
        "expected_rebalance_date": rebalance_date,
        "holding_period_days": holding_days,
        # --- safety ---
        "performs_network": PERFORMS_NETWORK,
        "offline": True,
        "uses_owned_data_only": True,
        "performs_provider_acquisition": False,
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
        "order_action_all": ORDER_ACTION,
        "review_status_all": REVIEW_STATUS,
        "safety_badges": SAFETY_BADGES,
        "required_artifacts": [v for v in _ARTIFACTS.values()],
        "exact_next_command": ("review research/output/phase10h_rules_based_paper_portfolio/"
                               "rule_approval_checklist.csv"),
        "constraints_honored": [
            "offline (no network/key/provider probe)", "reads only owned 10-F-A artifacts",
            "no new provider purchase", "rules-based (no per-ticker approval)", "no optimised weights",
            "no Paper Trader writes", "no Paper Trader signals", "no trade decisions", "NO orders",
            "NO automation", "NO broker", "NO live trading", "no deploy", "no GCP", "no package install",
            "no full regression", "no key printed/written", "no commit", "no push",
        ],
    }
    _write_json(out_dir / _ARTIFACTS["report"], report)

    # secret-safety audit runs LAST so it scans every artifact just written.
    sec_rows, leak_clean = b10._secret_safety_audit(out_dir)
    _write_csv(out_dir / _ARTIFACTS["secret_audit"],
               ["file", "clean", "keyed_url_marker", "key_value_present"],
               [[r["file"], r["clean"], r["keyed_url_marker"], r["key_value_present"]] for r in sec_rows])
    report["secret_safety_leak_scan_clean"] = leak_clean
    report["api_key_printed"] = False
    _write_json(out_dir / _ARTIFACTS["report"], report)

    if verbose:
        _print_summary(report)
    return report


def _decision_rationale(decision: str, n_long: int, n_short: int, n_exc: int, target: int) -> str:
    if decision == DEC_TOO_FEW:
        return (f"a side fell below the {MIN_VIABLE_PER_SIDE}-name viability floor "
                f"(long={n_long}, short={n_short})")
    if decision == DEC_WITH_EXC:
        return (f"rules produced a {n_long}/{n_short} paper book with {n_exc} rule-exception(s) "
                f"(extreme/liquidity/sector-cap/missing) for human review")
    if decision == DEC_READY:
        return f"rules produced a clean {n_long}/{n_short} paper book with no exceptions"
    return decision


def _parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Rules-based paper portfolio constructor (Phase 10-H).")
    p.add_argument("--book", default=None)
    p.add_argument("--cand", default=None)
    p.add_argument("--risk", default=None)
    p.add_argument("--f10-json", default=None)
    p.add_argument("--out", default=None)
    p.add_argument("--target-per-side", type=int, default=TARGET_PER_SIDE)
    p.add_argument("--quiet", action="store_true")
    return p.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    ns = _parse_args(argv)
    rep = run(out_dir=ns.out, book_csv=ns.book, cand_csv=ns.cand, risk_csv=ns.risk,
              f10_json=ns.f10_json, target_per_side=ns.target_per_side, verbose=not ns.quiet)
    return 0 if rep.get("decision") in ALLOWED_DECISIONS else 1


if __name__ == "__main__":
    raise SystemExit(main())
