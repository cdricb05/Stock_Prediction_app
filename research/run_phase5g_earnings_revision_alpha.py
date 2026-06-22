"""Phase 5-G — Earnings / Revision / Post-Earnings Drift Alpha Module (offline, PIT).

This is **Track A (quant brain) research**, not Track B operational packaging. It
ships **no** deployable scorer and starts **no** shadow trading. It tests one
strategic hypothesis with out-of-sample, point-in-time evidence:

    The Phase 5-C / 5-F lineage repeatedly tuned PORTFOLIO FILTERS around the same
    price/volume/regime signal and never beat the Phase 5-C price-only champion.
    The next real improvement must add NEW predictive information. For a 5-20 day
    horizon, earnings surprises, estimate revisions, and post-earnings drift should
    carry signal that delayed fundamentals do not.

        "Does a point-in-time earnings / revision / post-earnings-drift event signal
         add INCREMENTAL out-of-sample ranking IC over the Phase 5-C champion, on the
         names it actually covers, without leakage and surviving a placebo shuffle?"

What it does
------------
  1. **Inventory first (always).** Scans the local event/catalyst artifacts (the
     Phase 3-M earnings panel, the Phase 3-S readiness gate, any analyst-revision
     inventory) and reports, without assuming usability: which files exist, what
     columns exist, whether timestamps are point-in-time safe (availability =
     report/publish date, never fiscal-quarter end), whether earnings dates /
     estimate revisions / surprise fields exist, whether the data covers the Phase
     5-C universe, and whether it can be joined by ticker/date without leakage.
  2. **Decides usability honestly.** If no usable event data exists it STOPS after
     the inventory with a data-blocker report listing the minimum required fields.
     If only partial data exists it builds ONLY the safe features and never fakes a
     missing field (estimate-revision features are built only if a PIT revision
     series exists; here they do not, and that is reported, not patched).
  3. Reuses the Phase 5-C harness (D: read-only price load, SPY-calendar alignment,
     the monthly point-in-time panel, the yearly walk-forward champion predictions,
     rank-IC math, the SPY regime proxy, and the placebo leakage probe).
  4. Builds a PIT earnings-event alpha panel: for each monthly rebalance date and
     covered ticker it joins the most recent earnings event with
     ``availability_date <= rebalance_date`` (strict as-of, no look-ahead) and
     derives surprise direction / magnitude, trailing 4q/8q surprise, surprise
     acceleration, days-since-earnings, a post-earnings-drift window flag, and the
     realized earnings-announcement reaction (price + a recency interaction).
  5. Evaluates five models on the SAME covered cross-section for an apples-to-apples
     incremental test: ``phase5c_reference`` (champion price signal, restricted to
     covered names), ``event_alpha_only``, ``price_plus_event_alpha``,
     ``event_gated_price_signal``, and ``best_event_candidate`` (the best of the
     three event arms by incremental OOS IC, guarded by a placebo floor).
  6. Validates walk-forward (reusing the Phase 5-C folds + >=20-session embargo),
     with rank IC, IC t-stat, IC by year, decile / top-N spread, net-of-25/50bps
     ANNUALIZED-MEAN return, turnover, drawdown, regime breakdown, comparison vs
     Phase 5-C and Phase 5-F0C, and a within-date label-shuffle placebo. It emits an
     honest validation-gate matrix and one of the allowed recommendations. It never
     forces a PASS and never green-lights deployment; the ceiling is a research /
     shadow event-alpha STRATEGY TEST (Phase 5-H), gated on coverage.

Hard rules honored: no live network / API calls, no provider shopping, no FMP, no
SimFin fundamentals expansion (the optional industry map reads Ticker/IndustryId
only), no new paid data, no package installs, D: is read-only price input only, no
writes to D:, no Paper Trader changes, no GCP changes, no deploy, no orders / broker
execution / automation, no binary model artifacts, no commit, no push. Pure offline
numpy + csv. The local universe is full-history survivors, so absolute returns stay
survivorship-biased and no production edge is claimed.
"""
from __future__ import annotations

import argparse
import bisect
import csv
import datetime as dt
import importlib.util
import json
import math
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

PHASE = "5-G"

# --------------------------------------------------------------------------- #
# Paths (all outputs committed-safe; D: is read-only price input only)
# --------------------------------------------------------------------------- #
_OUT_DIR = _REPO_ROOT / "research" / "output" / "phase5g_earnings_revision_alpha"
_REPORT_OUT = _OUT_DIR / "phase5g_earnings_revision_alpha.json"
_INVENTORY_OUT = _OUT_DIR / "event_data_inventory.csv"
_PIT_SAFETY_OUT = _OUT_DIR / "event_data_pit_safety_report.csv"
_FEATURE_CATALOG_OUT = _OUT_DIR / "event_feature_catalog.csv"
_PANEL_SAMPLE_OUT = _OUT_DIR / "event_alpha_panel_sample.csv"
_SCOREBOARD_OUT = _OUT_DIR / "event_alpha_model_scoreboard.csv"
_INCREMENTAL_OUT = _OUT_DIR / "event_alpha_incremental_edge_report.csv"
_GATE_MATRIX_OUT = _OUT_DIR / "event_alpha_validation_gate_matrix.csv"
_NEXT_PLAN_OUT = _OUT_DIR / "phase5h_next_alpha_plan.json"

_PHASE5C_RUNNER = _REPO_ROOT / "research" / "run_phase5c_cross_sectional_alpha_research.py"

# Local event/catalyst artifact candidates (committed research outputs — NOT D:, NOT network).
_EARNINGS_FEATURES_CSV = (_REPO_ROOT / "research" / "output"
                          / "phase3m_earnings_estimates_signal_gate"
                          / "earnings_features_universe.csv")
_EARNINGS_EVENTS_CSV = (_REPO_ROOT / "research" / "output"
                        / "phase3m_earnings_estimates_signal_gate"
                        / "earnings_events_universe.csv")
_EARNINGS_COVERAGE_BY_TICKER_CSV = (_REPO_ROOT / "research" / "output"
                                    / "phase3m_earnings_estimates_signal_gate"
                                    / "earnings_feature_coverage_by_ticker.csv")
_REVISION_INVENTORY_CSV = (_REPO_ROOT / "research" / "output"
                           / "phase3s_event_signal_readiness_gate"
                           / "analyst_revision_data_inventory.csv")
_COVERAGE_STATUS_CSV = (_REPO_ROOT / "research" / "output"
                        / "phase3s_event_signal_readiness_gate"
                        / "earnings_coverage_status.csv")
# Phase 5-F0C report — read (input artifact) to compare against the best 5-F0C strategy.
_PHASE5F0C_REPORT = (_REPO_ROOT / "research" / "output"
                     / "phase5f0c_signal_to_strategy_conversion"
                     / "phase5f0c_signal_to_strategy_conversion.json")

# All committed-safe artifact paths (used for the run report + tests).
_ALL_ARTIFACTS = [
    _REPORT_OUT, _INVENTORY_OUT, _PIT_SAFETY_OUT, _FEATURE_CATALOG_OUT,
    _PANEL_SAMPLE_OUT, _SCOREBOARD_OUT, _INCREMENTAL_OUT, _GATE_MATRIX_OUT,
    _NEXT_PLAN_OUT,
]
# Committed-safe audit artifact (artifact/console/JSON consistency reconciliation).
_AUDIT_OUT = _OUT_DIR / "phase5g_artifact_consistency_audit.csv"


class _OutPaths:
    """Resolve all artifact paths under a single output directory.

    ``run()`` builds one of these from its ``out_dir`` argument so a test can
    redirect EVERY artifact into a temporary directory and never overwrite the
    committed production artifacts. The production default is ``_OUT_DIR``.
    """

    __slots__ = ("out_dir", "report", "inventory", "pit_safety", "feature_catalog",
                 "panel_sample", "scoreboard", "incremental", "gate_matrix",
                 "next_plan", "audit")

    def __init__(self, out_dir) -> None:
        d = Path(out_dir)
        self.out_dir = d
        self.report = d / "phase5g_earnings_revision_alpha.json"
        self.inventory = d / "event_data_inventory.csv"
        self.pit_safety = d / "event_data_pit_safety_report.csv"
        self.feature_catalog = d / "event_feature_catalog.csv"
        self.panel_sample = d / "event_alpha_panel_sample.csv"
        self.scoreboard = d / "event_alpha_model_scoreboard.csv"
        self.incremental = d / "event_alpha_incremental_edge_report.csv"
        self.gate_matrix = d / "event_alpha_validation_gate_matrix.csv"
        self.next_plan = d / "phase5h_next_alpha_plan.json"
        self.audit = d / "phase5g_artifact_consistency_audit.csv"

    @property
    def all_artifacts(self) -> List[Path]:
        return [self.report, self.inventory, self.pit_safety, self.feature_catalog,
                self.panel_sample, self.scoreboard, self.incremental,
                self.gate_matrix, self.next_plan]

    def is_production(self) -> bool:
        try:
            return self.out_dir.resolve() == _OUT_DIR.resolve()
        except OSError:
            return False

# --------------------------------------------------------------------------- #
# Model identifiers (the required model set)
# --------------------------------------------------------------------------- #
M_REF = "phase5c_reference"
M_EVENT = "event_alpha_only"
M_PRICE_PLUS = "price_plus_event_alpha"
M_GATED = "event_gated_price_signal"
M_BEST = "best_event_candidate"
MODEL_ORDER = [M_REF, M_EVENT, M_PRICE_PLUS, M_GATED, M_BEST]
# The three event arms among which the best candidate is chosen.
_EVENT_ARMS = [M_EVENT, M_PRICE_PLUS, M_GATED]

# --------------------------------------------------------------------------- #
# Labels (excess-vs-SPY forward returns; primary = 20d)
# --------------------------------------------------------------------------- #
H5, H10, H20 = 5, 10, 20
LABEL_5D = "forward_excess_return_5d_vs_spy"
LABEL_10D = "forward_excess_return_10d_vs_spy"
LABEL_20D = "forward_excess_return_20d_vs_spy"
PRIMARY_LABEL = LABEL_20D
LABEL_HORIZON = {LABEL_5D: H5, LABEL_10D: H10, LABEL_20D: H20}

# --------------------------------------------------------------------------- #
# Event-feature tuning constants (FIXED a-priori; NOT tuned on this sample)
# --------------------------------------------------------------------------- #
DRIFT_WINDOW_DAYS = 63        # post-earnings-drift window (~one quarter of sessions)
ANN_RETURN_PRE = 1           # announcement window: close[a-1] ...
ANN_RETURN_POST = 1          # ... to close[a+1] (2-day reaction), a = event session
SURPRISE_PCT_WINSOR = 100.0   # clamp |eps_surprise_pct| for the composite (robustness)
MIN_NAMES_PER_DATE = 10       # cross-section must be wide enough to rank (matches 5-C)
TOP_N_PORTFOLIO = (5, 10)     # long-only basket sizes simulated on the covered universe
COST_BPS = (25, 50)
PERIODS_PER_YEAR = 12         # monthly cadence

# Event composite: feature -> sign so that higher == more bullish.
_EVENT_FEATURE_SIGN = {
    "eps_surprise_pct_w": +1,
    "surprise_direction": +1,
    "trailing_4q_avg_surprise_pct": +1,
    "trailing_8q_avg_surprise_pct": +1,
    "trailing_4q_positive_surprise_rate": +1,
    "surprise_acceleration": +1,
    "fresh_positive_surprise": +1,    # surprise_direction gated to the drift window
    "announcement_reaction": +1,      # realized earnings-window price reaction (PIT/past)
}
# Composite block weights (judgement-set; re-standardized before ranking; not fit).
_EVENT_WEIGHTS = {
    "eps_surprise_pct_w": 0.8,
    "surprise_direction": 0.7,
    "trailing_4q_avg_surprise_pct": 0.9,
    "trailing_8q_avg_surprise_pct": 0.6,
    "trailing_4q_positive_surprise_rate": 0.7,
    "surprise_acceleration": 0.8,
    "fresh_positive_surprise": 1.0,   # PEAD: a recent positive surprise is the core bet
    "announcement_reaction": 0.6,
}

# Readiness-gate thresholds.
GATE_INCREMENTAL_IC = 0.005      # min incremental mean rank IC over 5-C to be "useful"
GATE_PLACEBO_ABS = 0.02          # |placebo IC| must collapse below this
GATE_COVERAGE_SUFFICIENT = 75    # project's own min-ticker gate (Phase 3-M/3-S)
GATE_OOS_FOLD_FRACTION = 0.50    # incremental edge must be positive in >= this fold share

# --------------------------------------------------------------------------- #
# Recommendation vocabulary (the allowed values)
# --------------------------------------------------------------------------- #
REC_READY = "READY_FOR_PHASE5H_EVENT_ALPHA_STRATEGY_TEST"
REC_IMPROVES = "EVENT_ALPHA_IMPROVES_SIGNAL"
REC_PARTIAL = "EVENT_DATA_PARTIAL_BUT_USEFUL"
REC_BLOCKER = "EVENT_DATA_BLOCKER"
REC_NO_EDGE = "NO_INCREMENTAL_EVENT_EDGE"
REC_ERROR = "ERROR"
ALLOWED_RECOMMENDATIONS = (REC_READY, REC_IMPROVES, REC_PARTIAL, REC_BLOCKER,
                           REC_NO_EDGE, REC_ERROR)

# Minimum required fields for a usable PIT event/revision alpha source (blocker report).
_MIN_REQUIRED_FIELDS = {
    "earnings_surprise": ["ticker", "reported_date/availability_date (point-in-time)",
                          "reported_eps", "estimated_eps", "surprise or surprise_percentage"],
    "estimate_revision": ["ticker", "revision publication date (point-in-time)",
                          "prior_estimate", "new_estimate (or revision_value)"],
}


# --------------------------------------------------------------------------- #
# Reuse: import the Phase 5-C runner as a module (main() guarded; no side effects).
# --------------------------------------------------------------------------- #
def _load_phase5c(runner_path: Optional[Path] = None):
    path = Path(runner_path) if runner_path else _PHASE5C_RUNNER
    spec = importlib.util.spec_from_file_location("phase5c_runner", str(path))
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load Phase 5-C runner at {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# --------------------------------------------------------------------------- #
# Small IO / numeric helpers
# --------------------------------------------------------------------------- #
def _rel(p: Path) -> str:
    try:
        return str(p.relative_to(_REPO_ROOT))
    except ValueError:
        return str(p)


def _f(x) -> Optional[float]:
    if x is None:
        return None
    try:
        xf = float(x)
    except (TypeError, ValueError):
        return None
    return round(xf, 6) if math.isfinite(xf) else None


def _flt(x) -> Optional[float]:
    """Parse a CSV cell to float or None (blank/non-finite -> None)."""
    if x is None:
        return None
    s = str(x).strip()
    if s == "":
        return None
    try:
        v = float(s)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=2, default=str)
        fh.write("\n")


def _write_csv(path: Path, header: Sequence[str], rows: Sequence[Sequence]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        for r in rows:
            w.writerow(r)


def _read_csv_dicts(path: Path) -> Tuple[List[str], List[dict]]:
    """Return (fieldnames, rows). Empty if the file is absent/unreadable."""
    if not path.is_file():
        return [], []
    try:
        with open(path, "r", encoding="utf-8", newline="") as fh:
            reader = csv.DictReader(fh)
            names = list(reader.fieldnames or [])
            rows = [dict(r) for r in reader]
        return names, rows
    except (OSError, ValueError):
        return [], []


# --------------------------------------------------------------------------- #
# Step 1 — Event/catalyst data INVENTORY (always runs; assumes nothing usable).
# --------------------------------------------------------------------------- #
def _detect_family(path: Path, names: Sequence[str]) -> str:
    n = path.name.lower()
    cols = {c.lower() for c in names}
    if "revision" in n or any("revision" in c for c in cols):
        return "analyst_revision"
    if {"surprise", "surprise_percentage", "eps_surprise"} & cols:
        return "earnings_surprise"
    if "coverage" in n:
        return "coverage_status"
    if "earnings" in n:
        return "earnings_event"
    return "unknown"


def build_inventory() -> dict:
    """Inventory local event/catalyst files: existence, columns, PIT safety, dates,
    revisions, surprises, universe coverage, joinability. Pure local read."""
    candidates = [
        ("earnings_events", _EARNINGS_EVENTS_CSV),
        ("earnings_features", _EARNINGS_FEATURES_CSV),
        ("earnings_coverage_by_ticker", _EARNINGS_COVERAGE_BY_TICKER_CSV),
        ("analyst_revision_inventory", _REVISION_INVENTORY_CSV),
        ("coverage_status", _COVERAGE_STATUS_CSV),
    ]
    inv_rows: List[dict] = []
    pit_rows: List[dict] = []

    for role, path in candidates:
        names, rows = _read_csv_dicts(path)
        exists = path.is_file()
        cols_lower = {c.lower() for c in names}
        has_ticker = any(c in cols_lower for c in ("ticker", "symbol"))
        has_avail = "availability_date" in cols_lower
        has_reported = "reported_date" in cols_lower
        has_fiscal = "fiscal_date_ending" in cols_lower
        has_surprise = bool({"surprise", "surprise_percentage", "eps_surprise",
                             "eps_surprise_pct"} & cols_lower)
        has_revision = any("revision" in c for c in cols_lower)
        # PIT-safe iff there is an availability/report date that is the *report* date
        # (not the fiscal-quarter end). Phase 3-M stamps availability_date = reported_date.
        date_min = date_max = ""
        if rows and (has_avail or has_reported):
            dcol = "availability_date" if has_avail else "reported_date"
            ds = sorted(str(r.get(dcol, "")).strip()[:10] for r in rows
                        if str(r.get(dcol, "")).strip())
            if ds:
                date_min, date_max = ds[0], ds[-1]
        pit_safe = bool(exists and (has_avail or has_reported) and has_ticker)
        inv_rows.append({
            "role": role,
            "file_path": _rel(path),
            "exists": exists,
            "detected_family": _detect_family(path, names),
            "row_count": len(rows),
            "column_count": len(names),
            "columns": ";".join(names),
            "has_ticker": has_ticker,
            "has_earnings_date": has_avail or has_reported,
            "has_surprise_field": has_surprise,
            "has_revision_field": has_revision,
            "date_min": date_min,
            "date_max": date_max,
        })
        pit_rows.append({
            "role": role,
            "file_path": _rel(path),
            "exists": exists,
            "has_availability_date": has_avail,
            "has_reported_date": has_reported,
            "has_fiscal_date_ending": has_fiscal,
            "availability_is_report_date": bool(has_avail),
            "uses_fiscal_end_as_timestamp": bool(has_fiscal and not (has_avail or has_reported)),
            "point_in_time_safe": pit_safe,
            "note": ("availability_date present and stamped = report date (PIT-safe join key)"
                     if has_avail else
                     ("reported_date present (usable as PIT key)" if has_reported else
                      "no point-in-time date column")),
        })

    # Headline assessments.
    feat_names, feat_rows = _read_csv_dicts(_EARNINGS_FEATURES_CSV)
    ev_names, ev_rows = _read_csv_dicts(_EARNINGS_EVENTS_CSV)
    rev_names, rev_rows_raw = _read_csv_dicts(_REVISION_INVENTORY_CSV)

    earnings_present = bool(feat_rows or ev_rows)
    # Surprise present iff the events/features carry surprise columns with data.
    surprise_present = bool(
        ({"surprise", "surprise_percentage"} & {c.lower() for c in ev_names}) or
        ({"eps_surprise", "eps_surprise_pct"} & {c.lower() for c in feat_names}))
    # Revision present iff the revision inventory marks a usable PIT revision series.
    revision_present = False
    revision_blocker = "no analyst-revision inventory file found"
    if rev_rows_raw:
        # The Phase 3-S inventory row reports usable/blocker for an analyst-revision series.
        r0 = rev_rows_raw[0]
        usable = str(r0.get("usable", "")).strip().lower() == "true"
        sent_rev = str(r0.get("sentiment_or_revision_column_detected", "")).strip().lower() == "true"
        revision_present = bool(usable and sent_rev)
        revision_blocker = (r0.get("blocker") or "").strip() or (
            "" if revision_present else "analyst-revision series not usable")
    # estimate_revision_proxy in the feature panel is documented-empty when absent.
    rev_proxy_has_data = False
    for r in feat_rows:
        if str(r.get("estimate_revision_proxy", "")).strip() != "":
            rev_proxy_has_data = True
            break

    return {
        "inventory_rows": inv_rows,
        "pit_rows": pit_rows,
        "earnings_data_present": earnings_present,
        "surprise_fields_present": surprise_present,
        "estimate_revisions_present": revision_present,
        "estimate_revision_proxy_has_data": rev_proxy_has_data,
        "revision_blocker": revision_blocker,
        "earnings_feature_rows": len(feat_rows),
        "earnings_event_rows": len(ev_rows),
        "earnings_feature_file": _rel(_EARNINGS_FEATURES_CSV),
        "earnings_event_file": _rel(_EARNINGS_EVENTS_CSV),
    }


# --------------------------------------------------------------------------- #
# Step 2 — Load PIT earnings events keyed for an as-of join.
# --------------------------------------------------------------------------- #
def load_earnings_events(path: Optional[Path] = None) -> Dict[str, List[dict]]:
    """Return ``{ticker: [event, ...]}`` sorted ascending by availability_date.

    Each event carries the PIT-safe availability_date plus surprise fields and the
    trailing-only features Phase 3-M already computed at event time (which use only
    prior earnings). Only rows flagged point_in_time_usable are kept. Pure local read.
    """
    p = Path(path) if path else _EARNINGS_FEATURES_CSV
    names, rows = _read_csv_dicts(p)
    out: Dict[str, List[dict]] = {}
    for r in rows:
        if str(r.get("point_in_time_usable", "True")).strip().lower() == "false":
            continue
        t = (r.get("ticker") or "").strip().upper()
        avail = (r.get("availability_date") or r.get("reported_date") or "").strip()[:10]
        if not t or not avail:
            continue
        ev = {
            "availability_date": avail,
            "eps_surprise": _flt(r.get("eps_surprise")),
            "eps_surprise_pct": _flt(r.get("eps_surprise_pct")),
            "positive_surprise_flag": _flt(r.get("positive_surprise_flag")),
            "negative_surprise_flag": _flt(r.get("negative_surprise_flag")),
            "surprise_magnitude_abs": _flt(r.get("surprise_magnitude_abs")),
            "trailing_4q_avg_surprise_pct": _flt(r.get("trailing_4q_avg_surprise_pct")),
            "trailing_4q_positive_surprise_rate": _flt(r.get("trailing_4q_positive_surprise_rate")),
            "trailing_8q_avg_surprise_pct": _flt(r.get("trailing_8q_avg_surprise_pct")),
            "surprise_acceleration": _flt(r.get("surprise_acceleration")),
            "estimate_revision_proxy": _flt(r.get("estimate_revision_proxy")),
        }
        out.setdefault(t, []).append(ev)
    for t in out:
        out[t].sort(key=lambda e: e["availability_date"])
    return out


def _latest_event_asof(events: List[dict], asof_date: str) -> Optional[dict]:
    """Most recent event with availability_date <= asof_date (strict as-of). No leak."""
    if not events:
        return None
    keys = [e["availability_date"] for e in events]
    j = bisect.bisect_right(keys, asof_date) - 1
    if j < 0:
        return None
    return events[j]


def _days_between(d_from: str, d_to: str) -> Optional[int]:
    try:
        a = dt.date.fromisoformat(d_from[:10])
        b = dt.date.fromisoformat(d_to[:10])
    except ValueError:
        return None
    return (b - a).days


def _announcement_reaction(p5c, al, ticker: str, avail_date: str, rebal_idx: int) -> Optional[float]:
    """Realized earnings-announcement price reaction = close[a+POST]/close[a-PRE]-1,
    where ``a`` is the first master session on/after the availability_date. Returns
    None unless the whole window closes strictly before the rebalance index (so it is
    fully in the past relative to the as-of date — PIT-safe, never look-ahead)."""
    idx_map = al._idx
    a = idx_map.get(avail_date)
    if a is None:
        # nearest master session on/after availability_date
        j = bisect.bisect_left(al.master_dates, avail_date)
        if j >= len(al.master_dates):
            return None
        a = j
    lo, hi = a - ANN_RETURN_PRE, a + ANN_RETURN_POST
    if lo < 0 or hi > rebal_idx:        # window must be entirely past as-of the rebalance
        return None
    c = al.close[ticker]
    base, fut = c[lo], c[hi]
    if not (np.isfinite(base) and np.isfinite(fut)) or base <= 0:
        return None
    return float(fut / base - 1.0)


def build_event_features(p5c, al, events_by_ticker: Dict[str, List[dict]],
                         rebal_indices: List[int]) -> Dict[Tuple[str, str], Dict[str, float]]:
    """``{(date, ticker): {event_feature: value}}`` for covered names, as-of each
    rebalance date. Uses ONLY events whose availability_date <= the rebalance date.
    estimate-revision features are intentionally NOT built (no PIT revision series)."""
    out: Dict[Tuple[str, str], Dict[str, float]] = {}
    for i in rebal_indices:
        d = al.master_dates[i]
        for t, events in events_by_ticker.items():
            if t not in al.close:
                continue
            ev = _latest_event_asof(events, d)
            if ev is None:
                continue
            dse = _days_between(ev["availability_date"], d)
            if dse is None or dse < 0:
                continue
            direction = 0.0
            if ev.get("positive_surprise_flag"):
                direction = 1.0
            elif ev.get("negative_surprise_flag"):
                direction = -1.0
            sp = ev.get("eps_surprise_pct")
            sp_w = (max(-SURPRISE_PCT_WINSOR, min(SURPRISE_PCT_WINSOR, sp))
                    if sp is not None else None)
            within = 1.0 if dse <= DRIFT_WINDOW_DAYS else 0.0
            fresh_pos = direction * within
            ann = _announcement_reaction(p5c, al, t, ev["availability_date"], i)
            feats: Dict[str, Optional[float]] = {
                "eps_surprise_pct_w": sp_w,
                "surprise_direction": direction,
                "trailing_4q_avg_surprise_pct": ev.get("trailing_4q_avg_surprise_pct"),
                "trailing_8q_avg_surprise_pct": ev.get("trailing_8q_avg_surprise_pct"),
                "trailing_4q_positive_surprise_rate": ev.get("trailing_4q_positive_surprise_rate"),
                "surprise_acceleration": ev.get("surprise_acceleration"),
                "fresh_positive_surprise": fresh_pos,
                "announcement_reaction": ann,
                # carried (non-composite) context for the panel sample / gate:
                "days_since_earnings": float(dse),
                "post_earnings_drift_window": within,
            }
            out[(d, t)] = {k: v for k, v in feats.items() if v is not None}
    return out


def _standardize_event_cross_section(recs: List[dict], feat_key: str
                                     ) -> Dict[int, float]:
    """Per-date cross-sectional event-composite score (sign-adjusted z-sum,
    re-standardized). ``recs`` carry ``ev`` feature dicts under ``feat_key``.
    Returns ``{id(rec): composite_score}``. Leakage-free (contemporaneous only)."""
    # Standardize each event feature across the date's covered names.
    z: Dict[int, Dict[str, float]] = {id(r): {} for r in recs}
    for feat, sign in _EVENT_FEATURE_SIGN.items():
        xs = [(r, r[feat_key].get(feat)) for r in recs if r[feat_key].get(feat) is not None]
        if len(xs) < MIN_NAMES_PER_DATE:
            continue
        arr = np.array([v for _, v in xs], dtype=float)
        mu, sd = float(np.mean(arr)), float(np.std(arr))
        if sd <= 0:
            continue
        for r, v in xs:
            z[id(r)][feat] = sign * (v - mu) / sd
    out: Dict[int, float] = {}
    for r in recs:
        zf = z[id(r)]
        num = used = 0.0
        for feat, zz in zf.items():
            w = _EVENT_WEIGHTS.get(feat, 0.0)
            num += w * zz
            used += abs(w)
        out[id(r)] = (num / used) if used > 0 else 0.0
    return out


# --------------------------------------------------------------------------- #
# Multi-horizon excess labels straight from the aligned price calendar.
# --------------------------------------------------------------------------- #
def _excess_forward(p5c, al, ticker: str, i: int, h: int) -> Optional[float]:
    ft = p5c.forward_return(al.close[ticker], i, h)
    fs = p5c.forward_return(al.close[p5c.BENCHMARK], i, h)
    if ft is None or fs is None:
        return None
    return float(ft - fs)


# --------------------------------------------------------------------------- #
# Build OOS scored records for every model on the SAME covered cross-section.
# --------------------------------------------------------------------------- #
def build_model_records(p5c, al, champ_preds: List[dict],
                        event_feats: Dict[Tuple[str, str], Dict[str, float]],
                        date_to_idx: Dict[str, int]
                        ) -> Tuple[Dict[str, List[dict]], dict]:
    """Attach event features to the champion OOS predictions on covered names and
    derive every model's per-(date,ticker) score. Returns (records_by_model, info).

    The champion price score is already out-of-sample (from the Phase 5-C yearly
    walk-forward). The event composite is a FIXED PIT transform (no fit), so every
    derived score is OOS and leakage-free. All models share the identical covered
    record set, so IC differences are a clean incremental comparison."""
    # Keep only covered (date,ticker) with an event-as-of AND a realized primary label.
    covered = [r for r in champ_preds
               if (r["date"], r["ticker"]) in event_feats and r.get("y") is not None]
    for r in covered:
        r["ev"] = event_feats[(r["date"], r["ticker"])]
        i = date_to_idx.get(r["date"])
        r["excess_5d"] = _excess_forward(p5c, al, r["ticker"], i, H5) if i is not None else None
        r["excess_10d"] = _excess_forward(p5c, al, r["ticker"], i, H10) if i is not None else None
        r["excess_20d"] = r.get("y")

    by_date: Dict[str, List[dict]] = {}
    for r in covered:
        by_date.setdefault(r["date"], []).append(r)

    # Per-date event composite + price z-score (for the combined model).
    event_comp: Dict[int, float] = {}
    price_z: Dict[int, float] = {}
    for d, recs in by_date.items():
        event_comp.update(_standardize_event_cross_section(recs, "ev"))
        ps = np.array([r["score"] for r in recs], dtype=float)
        mu, sd = float(np.mean(ps)), float(np.std(ps))
        for r in recs:
            price_z[id(r)] = ((r["score"] - mu) / sd) if sd > 0 else 0.0

    def _mk(records: List[dict], score_fn) -> List[dict]:
        out = []
        for r in records:
            s = score_fn(r)
            if s is None:
                continue
            out.append({
                "date": r["date"], "year": int(r["date"][:4]), "regime": r.get("regime"),
                "ticker": r["ticker"], "score": float(s), "y": r["y"], "raw20": r.get("raw20"),
                "excess_5d": r.get("excess_5d"), "excess_10d": r.get("excess_10d"),
                "excess_20d": r.get("excess_20d"),
            })
        return out

    recs_all = covered
    models: Dict[str, List[dict]] = {}
    # 1. phase5c_reference: champion price score on the covered cross-section.
    models[M_REF] = _mk(recs_all, lambda r: r["score"])
    # 2. event_alpha_only: rank by the event composite alone.
    models[M_EVENT] = _mk(recs_all, lambda r: event_comp.get(id(r)))
    # 3. price_plus_event_alpha: equal-weight z-sum of price + event composite.
    models[M_PRICE_PLUS] = _mk(
        recs_all, lambda r: price_z.get(id(r), 0.0) + event_comp.get(id(r), 0.0))
    # 4. event_gated_price_signal: price score, but only names whose last surprise is
    #    not negative AND inside the drift window are eligible; others pushed to the
    #    bottom (score - large) so the ranking keeps non-eligible names rankable but last.
    def _gated(r):
        ev = r["ev"]
        eligible = (ev.get("surprise_direction", 0.0) >= 0.0
                    and ev.get("post_earnings_drift_window", 0.0) > 0.0)
        return r["score"] if eligible else (r["score"] - 1e6)
    models[M_GATED] = _mk(recs_all, _gated)

    info = {
        "covered_records": len(covered),
        "covered_dates": len(by_date),
        "covered_tickers": len({r["ticker"] for r in covered}),
    }
    return models, info


# --------------------------------------------------------------------------- #
# Multi-horizon IC (event composite vs 5d/10d/20d excess) on covered names.
# --------------------------------------------------------------------------- #
def _mean_rank_ic_for_label(p5c, records: List[dict], label_field: str) -> Optional[float]:
    by_date: Dict[str, List[dict]] = {}
    for r in records:
        if r.get(label_field) is not None:
            by_date.setdefault(r["date"], []).append(r)
    ics = []
    for d, recs in by_date.items():
        if len(recs) < MIN_NAMES_PER_DATE:
            continue
        s = np.array([r["score"] for r in recs], dtype=float)
        y = np.array([r[label_field] for r in recs], dtype=float)
        ic = p5c.rank_ic(s, y)
        if ic is not None and math.isfinite(ic):
            ics.append(ic)
    return float(np.mean(ics)) if ics else None


def _fold_incremental(p5c, ref_recs: List[dict], model_recs: List[dict]) -> dict:
    """Per-year incremental IC = model IC - reference IC on the same dates.
    Returns {n_years, positive_years, fraction_positive, by_year}."""
    def _ic_by_year(records):
        by_year: Dict[int, List[float]] = {}
        by_date: Dict[str, List[dict]] = {}
        for r in records:
            if r.get("y") is not None:
                by_date.setdefault(r["date"], []).append(r)
        for d, recs in by_date.items():
            if len(recs) < MIN_NAMES_PER_DATE:
                continue
            ic = p5c.rank_ic(np.array([r["score"] for r in recs], dtype=float),
                             np.array([r["y"] for r in recs], dtype=float))
            if ic is not None and math.isfinite(ic):
                by_year.setdefault(int(d[:4]), []).append(ic)
        return {y: float(np.mean(v)) for y, v in by_year.items()}

    ref_y = _ic_by_year(ref_recs)
    mod_y = _ic_by_year(model_recs)
    years = sorted(set(ref_y) & set(mod_y))
    by_year = {y: round(mod_y[y] - ref_y[y], 5) for y in years}
    pos = sum(1 for y in years if by_year[y] > 0)
    return {
        "n_years": len(years),
        "positive_years": pos,
        "fraction_positive": round(pos / len(years), 4) if years else None,
        "incremental_ic_by_year": by_year,
    }


# --------------------------------------------------------------------------- #
# Per-model evaluation (reuses Phase 5-C evaluate_model + an annualized-mean net).
# --------------------------------------------------------------------------- #
def _annualized_net(eval_block: dict, n: int, bps: int) -> Optional[float]:
    p = eval_block.get("portfolios", {}).get(n, {})
    net = p.get(f"net_{bps}bps", {})
    mpr = net.get("mean_period_return")
    return float(mpr * PERIODS_PER_YEAR) if mpr is not None else None


def evaluate_all(p5c, models: Dict[str, List[dict]]) -> Dict[str, dict]:
    out: Dict[str, dict] = {}
    for name, recs in models.items():
        block = p5c.evaluate_model(recs)
        if block.get("evaluable"):
            block["multi_horizon_ic"] = {
                LABEL_5D: _f(_mean_rank_ic_for_label(p5c, recs, "excess_5d")),
                LABEL_10D: _f(_mean_rank_ic_for_label(p5c, recs, "excess_10d")),
                LABEL_20D: _f(_mean_rank_ic_for_label(p5c, recs, "excess_20d")),
            }
            block["placebo_mean_ic"] = _f(p5c.placebo_mean_ic(recs))
            for n in TOP_N_PORTFOLIO:
                block.setdefault("ann_net", {})[str(n)] = {
                    "net_25bps_ann_mean": _f(_annualized_net(block, n, 25)),
                    "net_50bps_ann_mean": _f(_annualized_net(block, n, 50)),
                }
        out[name] = block
    return out


# --------------------------------------------------------------------------- #
# Best-candidate selection (incremental edge over 5-C, placebo-guarded).
# --------------------------------------------------------------------------- #
def select_best_event_candidate(p5c, models: Dict[str, List[dict]],
                                evals: Dict[str, dict]) -> Tuple[Optional[str], dict]:
    ref = evals.get(M_REF, {})
    ref_ic = ref.get("mean_rank_ic") if ref.get("evaluable") else None
    detail: Dict[str, dict] = {}
    contenders: List[str] = []
    for arm in _EVENT_ARMS:
        b = evals.get(arm, {})
        if not b.get("evaluable"):
            detail[arm] = {"eligible": False, "reason": "not evaluable"}
            continue
        ic = b.get("mean_rank_ic")
        placebo = b.get("placebo_mean_ic")
        incr = (ic - ref_ic) if (ic is not None and ref_ic is not None) else None
        fold = _fold_incremental(p5c, models[M_REF], models[arm])
        placebo_clean = placebo is None or abs(placebo) <= GATE_PLACEBO_ABS
        oos_ok = (fold.get("fraction_positive") or 0.0) >= GATE_OOS_FOLD_FRACTION
        eligible = bool(incr is not None and incr > 0 and placebo_clean and oos_ok)
        detail[arm] = {
            "eligible": eligible, "mean_rank_ic": _f(ic), "incremental_ic": _f(incr),
            "placebo_mean_ic": _f(placebo), "placebo_clean": placebo_clean,
            "fraction_positive_years": fold.get("fraction_positive"), "oos_ok": oos_ok,
        }
        if eligible:
            contenders.append(arm)

    def _key(arm):
        b = evals[arm]
        return (b.get("mean_rank_ic") or -9.9,
                _annualized_net(b, TOP_N_PORTFOLIO[-1], 50) or -9.9)

    best = max(contenders, key=_key) if contenders else None
    return best, {"reference_mean_rank_ic": _f(ref_ic), "arm_detail": detail,
                  "contenders": contenders}


# --------------------------------------------------------------------------- #
# Gate matrix + recommendation
# --------------------------------------------------------------------------- #
def build_gate_matrix(inv: dict, evals: Dict[str, dict], best_name: Optional[str],
                      best_sel: dict, coverage: dict, leakage_ok: bool) -> List[dict]:
    gates: List[dict] = []

    def add(name, status, metric, value, threshold, note):
        gates.append({"gate_name": name, "status": status, "metric": metric,
                      "value": value, "threshold": threshold, "note": note})

    ref = evals.get(M_REF, {})
    best = evals.get(best_name, {}) if best_name else {}
    best_ic = best.get("mean_rank_ic") if best.get("evaluable") else None
    ref_ic = ref.get("mean_rank_ic") if ref.get("evaluable") else None
    incr = (best_ic - ref_ic) if (best_ic is not None and ref_ic is not None) else None
    placebo = best.get("placebo_mean_ic") if best else None

    # 1. event_data_present
    add("event_data_present_gate", "PASS" if inv["earnings_data_present"] else "FAIL",
        "earnings_data_present", inv["earnings_data_present"], "true",
        f"earnings feature rows={inv['earnings_feature_rows']}, event rows={inv['earnings_event_rows']}")

    # 2. pit_safety
    add("pit_safety_gate", "PASS" if inv.get("pit_safe_overall") else "FAIL",
        "availability_is_report_date", inv.get("pit_safe_overall"), "true",
        "earnings availability_date = report date, never fiscal-quarter end")

    # 3. no_future_event_leakage (as-of join + placebo)
    placebo_clean = placebo is None or abs(placebo) <= GATE_PLACEBO_ABS
    add("no_future_event_leakage_gate", "PASS" if (leakage_ok and placebo_clean) else "FAIL",
        "asof_join_and_placebo_clean", bool(leakage_ok and placebo_clean),
        f"strict as-of join + |placebo IC|<={GATE_PLACEBO_ABS}",
        f"best-model placebo_mean_ic={placebo}")

    # 4. revisions_not_faked
    add("revisions_not_faked_gate", "PASS", "estimate_revision_features_built",
        inv["estimate_revisions_present"], "only if a PIT revision series exists",
        inv.get("revision_blocker") or "no usable analyst-revision series; revision features omitted (not faked)")

    # 5. coverage_sufficient
    cov_ok = coverage["covered_tickers"] >= GATE_COVERAGE_SUFFICIENT
    add("coverage_sufficient_gate", "PASS" if cov_ok else "WARNING",
        "covered_tickers", coverage["covered_tickers"], f">={GATE_COVERAGE_SUFFICIENT}",
        f"covered {coverage['covered_tickers']}/{coverage['universe_size']} "
        f"({coverage['coverage_fraction']}); below the project's own 75-ticker event gate")

    # 6. incremental_ic_over_phase5c
    if incr is None:
        add("incremental_ic_gate", "NOT_EVALUABLE", "incremental_mean_rank_ic", None,
            f">={GATE_INCREMENTAL_IC}", "best event arm or reference not evaluable")
    else:
        status = "PASS" if incr >= GATE_INCREMENTAL_IC else ("WARNING" if incr > 0 else "FAIL")
        add("incremental_ic_gate", status, "incremental_mean_rank_ic", _f(incr),
            f">={GATE_INCREMENTAL_IC}", f"best event IC={_f(best_ic)} vs 5-C ref IC={_f(ref_ic)}")

    # 7. oos_fold_visibility
    arm_detail = best_sel.get("arm_detail", {}).get(best_name, {}) if best_name else {}
    frac = arm_detail.get("fraction_positive_years")
    if frac is None:
        add("oos_fold_visibility_gate", "NOT_EVALUABLE", "fraction_positive_incremental_years",
            None, f">={GATE_OOS_FOLD_FRACTION}", "")
    else:
        add("oos_fold_visibility_gate", "PASS" if frac >= GATE_OOS_FOLD_FRACTION else "WARNING",
            "fraction_positive_incremental_years", frac, f">={GATE_OOS_FOLD_FRACTION}",
            "incremental edge must be visible across folds, not one-year luck")

    # 8. survivorship_bias (always WARNING — full-history survivors)
    add("survivorship_bias_gate", "WARNING", "universe_is_full_history_survivors", True,
        "survivorship-free universe required before trusting absolute returns",
        "rank IC less affected than absolute portfolio returns")

    # 9. no_fundamentals_as_alpha
    add("no_fundamentals_as_alpha_gate", "PASS", "fundamentals_used_as_alpha", False, "false",
        "only earnings surprise / drift events used; no statement line items as alpha")

    # 10-14. always-on safety gates
    add("preview_only_gate", "PASS", "preview_only", True, "true", "research harness only")
    add("no_network_gate", "PASS", "network_used", False, "false", "pure offline; no provider call")
    add("no_orders_gate", "PASS", "orders_enabled", False, "false", "")
    add("no_broker_execution_gate", "PASS", "broker_execution_enabled", False, "false", "")
    add("no_automation_gate", "PASS", "automation_enabled", False, "false", "")
    add("no_binary_model_artifact_gate", "PASS", "binary_model_written", False, "false",
        "models evaluated in-memory; nothing persisted")
    return gates


def derive_recommendation(inv: dict, evals: Dict[str, dict], best_name: Optional[str],
                          best_sel: dict, coverage: dict, leakage_ok: bool) -> str:
    """Honest, evidence-driven recommendation. Never forces READY; READY/IMPROVES
    require sufficient coverage, which the 50/128 panel does not meet."""
    if not inv["earnings_data_present"] or not inv.get("pit_safe_overall"):
        return REC_BLOCKER
    ref = evals.get(M_REF, {})
    if not ref.get("evaluable"):
        return REC_BLOCKER
    best = evals.get(best_name, {}) if best_name else {}
    best_ic = best.get("mean_rank_ic") if best.get("evaluable") else None
    ref_ic = ref.get("mean_rank_ic")
    incr = (best_ic - ref_ic) if (best_ic is not None and ref_ic is not None) else None
    placebo = best.get("placebo_mean_ic") if best else None
    placebo_clean = placebo is None or abs(placebo) <= GATE_PLACEBO_ABS
    arm_detail = best_sel.get("arm_detail", {}).get(best_name, {}) if best_name else {}
    oos_ok = (arm_detail.get("fraction_positive_years") or 0.0) >= GATE_OOS_FOLD_FRACTION
    cov_ok = coverage["covered_tickers"] >= GATE_COVERAGE_SUFFICIENT

    if not leakage_ok or not placebo_clean:
        return REC_NO_EDGE  # an "edge" that fails the placebo/leakage check is no edge
    # No incremental edge at all.
    if best_name is None or incr is None or incr <= 0 or not oos_ok:
        return REC_NO_EDGE
    # A real, leakage-clean, OOS-visible incremental edge exists.
    if incr >= GATE_INCREMENTAL_IC:
        # Coverage decides shadow-readiness vs partial-but-useful.
        if cov_ok:
            return REC_READY if best.get("evaluable") else REC_IMPROVES
        return REC_PARTIAL
    # Positive but small edge.
    return REC_PARTIAL


# --------------------------------------------------------------------------- #
# Artifact writers
# --------------------------------------------------------------------------- #
def _write_inventory(paths: "_OutPaths", inv: dict) -> None:
    inv_hdr = ["role", "file_path", "exists", "detected_family", "row_count",
               "column_count", "has_ticker", "has_earnings_date", "has_surprise_field",
               "has_revision_field", "date_min", "date_max", "columns"]
    _write_csv(paths.inventory, inv_hdr,
               [[r.get(k) for k in inv_hdr] for r in inv["inventory_rows"]])
    pit_hdr = ["role", "file_path", "exists", "has_availability_date", "has_reported_date",
               "has_fiscal_date_ending", "availability_is_report_date",
               "uses_fiscal_end_as_timestamp", "point_in_time_safe", "note"]
    _write_csv(paths.pit_safety, pit_hdr,
               [[r.get(k) for k in pit_hdr] for r in inv["pit_rows"]])


def _write_feature_catalog(paths: "_OutPaths", usable: bool, inv: dict) -> None:
    rows = []
    built = usable
    surprise_built = usable and inv["surprise_fields_present"]
    catalog = [
        ("days_since_earnings", "recency", "calendar days since last earnings availability_date", "context", built),
        ("surprise_direction", "earnings_surprise", "sign of last EPS surprise (+1/0/-1)", "alpha", surprise_built),
        ("eps_surprise_pct_w", "earnings_surprise", "winsorized last EPS surprise percent", "alpha", surprise_built),
        ("surprise_magnitude_abs", "earnings_surprise", "absolute surprise magnitude", "context", surprise_built),
        ("trailing_4q_avg_surprise_pct", "earnings_surprise", "trailing 4q average surprise percent", "alpha", surprise_built),
        ("trailing_8q_avg_surprise_pct", "earnings_surprise", "trailing 8q average surprise percent", "alpha", surprise_built),
        ("trailing_4q_positive_surprise_rate", "earnings_surprise", "trailing 4q positive-surprise rate", "alpha", surprise_built),
        ("surprise_acceleration", "earnings_surprise", "change in surprise vs prior quarter", "alpha", surprise_built),
        ("post_earnings_drift_window", "drift", f"1 if within {DRIFT_WINDOW_DAYS} days of earnings", "alpha", built),
        ("fresh_positive_surprise", "drift", "surprise direction gated to the drift window (PEAD)", "alpha", surprise_built),
        ("announcement_reaction", "drift", "realized 2-day earnings-announcement price reaction (PIT/past)", "alpha", built),
        ("estimate_revision_direction", "estimate_revision", "sign of analyst estimate revision", "alpha", inv["estimate_revisions_present"]),
        ("estimate_revision_magnitude", "estimate_revision", "size of analyst estimate revision", "alpha", inv["estimate_revisions_present"]),
        ("revision_acceleration", "estimate_revision", "change in revision pace", "alpha", inv["estimate_revisions_present"]),
    ]
    for name, fam, desc, role, was_built in catalog:
        rows.append([name, fam, role, desc, was_built,
                     "" if was_built else ("no usable PIT analyst-revision series (not faked)"
                                           if fam == "estimate_revision" else
                                           ("event data not usable" if not built else ""))])
    _write_csv(paths.feature_catalog, ["feature", "family", "role", "description",
                                       "built", "omitted_reason"], rows)


def _write_panel_sample(paths: "_OutPaths", models: Dict[str, List[dict]], event_feats) -> None:
    recs = models.get(M_PRICE_PLUS, [])[:200]
    hdr = ["date", "ticker", "regime", "price_plus_event_score", "y_excess_20d",
           "excess_5d", "excess_10d", "raw_return_20d"]
    rows = [[r["date"], r["ticker"], r.get("regime"), _f(r["score"]), _f(r["y"]),
             _f(r.get("excess_5d")), _f(r.get("excess_10d")), _f(r.get("raw20"))]
            for r in recs]
    _write_csv(paths.panel_sample, hdr, rows)


def _write_scoreboard(paths: "_OutPaths", evals: Dict[str, dict], best_name: Optional[str]) -> None:
    hdr = ["model", "evaluable", "n_scored_dates", "mean_rank_ic", "ic_t_stat",
           "worst_year_ic", "mean_decile_spread", "top_quintile_hit_rate",
           "ic_5d", "ic_10d", "ic_20d", "net_50bps_ann_mean_top10",
           "placebo_mean_ic", "is_best_event_candidate"]
    rows = []
    for m in MODEL_ORDER:
        if m == M_BEST:
            continue
        b = evals.get(m, {})
        ev = b.get("evaluable", False)
        mh = b.get("multi_horizon_ic", {}) if ev else {}
        ann = b.get("ann_net", {}).get(str(TOP_N_PORTFOLIO[-1]), {}) if ev else {}
        rows.append([
            m, ev, b.get("n_scored_dates") if ev else None,
            _f(b.get("mean_rank_ic")) if ev else None,
            _f(b.get("ic_t_stat")) if ev else None,
            _f(b.get("worst_year_ic")) if ev else None,
            _f(b.get("mean_decile_spread")) if ev else None,
            _f(b.get("top_quintile_hit_rate")) if ev else None,
            mh.get(LABEL_5D), mh.get(LABEL_10D), mh.get(LABEL_20D),
            ann.get("net_50bps_ann_mean"), b.get("placebo_mean_ic"),
            (m == best_name),
        ])
    _write_csv(paths.scoreboard, hdr, rows)


def _write_incremental(paths: "_OutPaths", p5c, models, evals, best_sel: dict) -> None:
    hdr = ["model", "mean_rank_ic", "reference_mean_rank_ic", "incremental_ic",
           "placebo_mean_ic", "placebo_clean", "fraction_positive_years", "eligible_as_best"]
    ref_ic = best_sel.get("reference_mean_rank_ic")
    rows = []
    for arm in _EVENT_ARMS:
        d = best_sel.get("arm_detail", {}).get(arm, {})
        rows.append([arm, d.get("mean_rank_ic"), ref_ic, d.get("incremental_ic"),
                     d.get("placebo_mean_ic"), d.get("placebo_clean"),
                     d.get("fraction_positive_years"), d.get("eligible")])
    _write_csv(paths.incremental, hdr, rows)


def _write_gate_matrix(paths: "_OutPaths", gates: List[dict]) -> None:
    _write_csv(paths.gate_matrix, ["gate_name", "status", "metric", "value", "threshold", "note"],
               [[g["gate_name"], g["status"], g["metric"], g["value"], g["threshold"], g["note"]]
                for g in gates])


def _write_next_plan(paths: "_OutPaths", recommendation: str, coverage: dict,
                     best_name: Optional[str], incr: Optional[float]) -> None:
    proceed = recommendation in (REC_READY, REC_IMPROVES)
    plan = {
        "phase": "5-H",
        "title": "Event-Alpha Strategy Test (research / shadow-only)",
        "proceed_to_event_alpha_strategy_test": proceed,
        "blocked_reason": (None if proceed else
                           ("insufficient event coverage "
                            f"({coverage['covered_tickers']}/{coverage['universe_size']} "
                            f"< {GATE_COVERAGE_SUFFICIENT}); expand PIT earnings collection "
                            "and add a PIT analyst-revision series before a strategy test"
                            if recommendation == REC_PARTIAL else
                            "no leakage-clean incremental event edge over Phase 5-C")),
        "best_event_candidate": best_name,
        "incremental_mean_rank_ic_vs_phase5c": _f(incr),
        "required_before_strategy_test": [
            f"expand PIT earnings coverage to >= {GATE_COVERAGE_SUFFICIENT} universe tickers "
            "(resumable cache-first collection in a SEPARATE controlled step; not from this harness)",
            "add a point-in-time analyst-estimate-revision series "
            "(ticker, revision publication date, prior_estimate, new_estimate)",
            "re-confirm survivorship handling on a survivorship-free universe before "
            "trusting absolute portfolio returns",
        ],
        "preview_only": True, "orders_enabled": False, "automation_enabled": False,
        "broker_execution_enabled": False, "deployed": False, "network_used": False,
    }
    _write_json(paths.next_plan, plan)


# --------------------------------------------------------------------------- #
# Audit reconciliation (artifact / console / JSON consistency)
# --------------------------------------------------------------------------- #
def _recommendation_supported_by_metrics(recommendation: str, ref_ic: Optional[float],
                                         best_ic: Optional[float], incr: Optional[float],
                                         placebo_clean: bool, best_name: Optional[str],
                                         oos_ok: bool, cov_ok: bool,
                                         earnings_present: bool, pit_safe: bool,
                                         ref_evaluable: bool) -> bool:
    """Independently re-check that the emitted recommendation is consistent with the
    scoreboard metrics (catches a verdict that disagrees with its own evidence)."""
    if recommendation == REC_ERROR:
        return True  # error reports carry no metrics to support
    if recommendation == REC_BLOCKER:
        return (not earnings_present) or (not pit_safe) or (not ref_evaluable)
    has_edge = bool(best_name and incr is not None and incr > 0
                    and placebo_clean and oos_ok)
    if recommendation == REC_NO_EDGE:
        return not has_edge
    # PARTIAL / IMPROVES / READY all require a real, placebo-clean, OOS-visible edge.
    if recommendation in (REC_PARTIAL, REC_IMPROVES, REC_READY):
        if not has_edge:
            return False
        if recommendation in (REC_IMPROVES, REC_READY):
            # the strong verdicts additionally require sufficient coverage
            return bool(cov_ok and incr is not None and incr >= GATE_INCREMENTAL_IC)
        return True  # PARTIAL: edge present but coverage/threshold not strong enough
    return False


def _write_audit(paths: "_OutPaths", report: dict, evals: Dict[str, dict],
                 best_name: Optional[str], best_sel: dict, coverage: dict,
                 leakage_ok: bool, p5c_meta: dict) -> dict:
    """Write the committed-safe artifact/console/JSON consistency audit row.

    The console summary is rendered from the SAME report dict, so by construction the
    console recommendation cannot disagree with the JSON recommendation; this audit
    records that invariant plus the supporting metrics and the overwrite probe."""
    rec = report.get("recommendation")
    ref = evals.get(M_REF, {})
    ref_evaluable = bool(ref.get("evaluable"))
    ref_ic = ref.get("mean_rank_ic") if ref_evaluable else None
    best = evals.get(best_name, {}) if best_name else {}
    best_ic = best.get("mean_rank_ic") if best.get("evaluable") else None
    incr = report.get("incremental_edge_vs_phase5c", {}).get("incremental_mean_rank_ic")
    placebo = best.get("placebo_mean_ic") if best else None
    placebo_clean = placebo is None or abs(placebo) <= GATE_PLACEBO_ABS
    arm_detail = best_sel.get("arm_detail", {}).get(best_name, {}) if best_name else {}
    oos_ok = (arm_detail.get("fraction_positive_years") or 0.0) >= GATE_OOS_FOLD_FRACTION
    cov_ok = coverage.get("covered_tickers", 0) >= GATE_COVERAGE_SUFFICIENT

    supported = _recommendation_supported_by_metrics(
        rec, ref_ic, best_ic, incr, placebo_clean and leakage_ok, best_name, oos_ok,
        cov_ok, report.get("local_event_data_found", False),
        report.get("pit_safety_summary", {}).get("availability_is_report_date", False),
        ref_evaluable)

    # Overwrite probe: a production artifact whose price source is a pytest/temp path
    # was clobbered by a test. A clean production run reads the D: / repo price CSV.
    price_src = str(p5c_meta.get("price_history_source") or "").lower()
    overwrite = paths.is_production() and (
        "pytest" in price_src or "\\temp\\" in price_src or "/temp/" in price_src
        or "appdata\\local\\temp" in price_src)

    tests_temp_only = _tests_write_to_temp_dir_only()
    final = ("CONSISTENT" if (supported and tests_temp_only and not overwrite)
             else "INCONSISTENT")

    row = {
        "json_recommendation": rec,
        "console_summary_recommendation": rec,  # _print_summary renders from this dict
        "phase5c_reference_model_used": p5c_meta.get("champion_model"),
        "phase5c_reference_ic_covered": _f(ref_ic),
        "best_event_model": best_name,
        "best_event_model_ic": _f(best_ic),
        "incremental_ic": _f(incr),
        "recommendation_supported_by_metrics": supported,
        "tests_write_to_temp_dir_only": tests_temp_only,
        "production_artifact_overwrite_detected": overwrite,
        "final_status": final,
    }
    _write_csv(paths.audit, list(row.keys()), [list(row.values())])
    return row


def _tests_write_to_temp_dir_only() -> bool:
    """True iff the Phase 5-G test module never calls run() without redirecting
    output to a temp dir (i.e. every `.run(` call passes out_dir=). If the test file
    is absent we cannot disprove it, so default True."""
    test_path = _REPO_ROOT / "tests" / "test_phase5g_earnings_revision_alpha.py"
    if not test_path.is_file():
        return True
    try:
        src = test_path.read_text(encoding="utf-8")
    except OSError:
        return True
    # Find every run( invocation and require an out_dir= argument in the same call,
    # balancing parentheses so nested calls like str(p1) do not truncate the scan.
    # Only real harness invocations (those passing price_csv=) are checked, so prose
    # mentions of the API in docstrings/comments are ignored.
    needle = ".run("
    idx = 0
    while True:
        j = src.find(needle, idx)
        if j < 0:
            break
        start = j + len(needle)
        depth = 1
        k = start
        while k < len(src) and depth > 0:
            ch = src[k]
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
            k += 1
        call = src[start:k - 1]
        idx = k
        if "price_csv" not in call:
            continue  # not a harness run invocation (e.g. a docstring reference)
        if "out_dir=" not in call:
            return False
    return True


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
def run(price_csv: Optional[str] = None, max_tickers: Optional[int] = None,
        f0c_report: Optional[Path] = None, out_dir: Optional[Path] = None) -> dict:
    # Every artifact is written under ``out_dir`` (default = the committed production
    # directory). Tests pass a temporary out_dir so a test run can NEVER overwrite the
    # committed production artifacts (the bug this argument exists to prevent).
    paths = _OutPaths(out_dir if out_dir is not None else _OUT_DIR)
    paths.out_dir.mkdir(parents=True, exist_ok=True)
    p5c = _load_phase5c()

    # ---- Step 1: inventory (always) ----
    inv = build_inventory()
    inv["pit_safe_overall"] = any(
        r["point_in_time_safe"] for r in inv["pit_rows"]
        if r["role"] in ("earnings_events", "earnings_features"))
    _write_inventory(paths, inv)

    base_flags = {
        "phase": PHASE,
        "preview_only": True, "orders_enabled": False, "automation_enabled": False,
        "broker_execution_enabled": False, "production_replacement": False,
        "network_used": False, "paid_apis_used": False, "deployed": False,
        "binary_artifacts_created": False,
    }
    zero_cov = {"covered_tickers": 0, "universe_size": 0, "coverage_fraction": 0.0}

    def _finalize(evals, best_name, best_sel, coverage, gates, recommendation,
                  incr, p5c_meta) -> dict:
        report = _assemble_report(paths, inv, evals, best_name, best_sel, coverage,
                                  gates, recommendation, base_flags, incr, p5c_meta,
                                  f0c_report=f0c_report)
        _write_json(paths.report, report)
        _write_audit(paths, report, evals, best_name, best_sel, coverage,
                     leakage_ok=bool(p5c_meta.get("embargo_respected", True)),
                     p5c_meta=p5c_meta)
        return report

    # ---- Data blocker: no usable PIT earnings data -> stop after inventory ----
    if not inv["earnings_data_present"] or not inv["pit_safe_overall"]:
        _write_feature_catalog(paths, False, inv)
        _write_csv(paths.panel_sample, ["note"], [["no usable event data; panel not built"]])
        _write_csv(paths.scoreboard, ["note"], [["no usable event data; models not evaluated"]])
        _write_csv(paths.incremental, ["note"], [["no usable event data"]])
        gates = build_gate_matrix(inv, {}, None, {}, zero_cov, leakage_ok=True)
        _write_gate_matrix(paths, gates)
        _write_next_plan(paths, REC_BLOCKER, zero_cov, None, None)
        return _finalize({}, None, {}, zero_cov, gates, REC_BLOCKER, None, {})

    # ---- Step 2: Phase 5-C price reference (D: read-only) ----
    path = price_csv or p5c._data_path()
    if path is None:
        # Treat absent price history as a data blocker (no fabricated panel).
        _write_feature_catalog(paths, True, inv)
        gates = build_gate_matrix(inv, {}, None, {}, zero_cov, leakage_ok=True)
        _write_gate_matrix(paths, gates)
        return _finalize({}, None, {}, zero_cov, gates, REC_BLOCKER, None,
                         {"price_history_found": False})

    hist = p5c.load_local_history(path)
    al = p5c.Aligned(hist)
    if max_tickers is not None:
        al.tickers = al.tickers[:max_tickers]
    panel = p5c.build_panel(al)
    preds, leakage = p5c.walk_forward(panel)
    # Pick the champion model = highest mean rank IC among the 5-C models (NOT a
    # hardcoded model id); the chosen model is recorded in phase5c_reference_meta.
    champ_name, champ_eval, champ_preds = None, None, None
    for m, recs in preds.items():
        blk = p5c.evaluate_model(recs)
        if blk.get("evaluable") and (champ_eval is None
                                     or blk["mean_rank_ic"] > champ_eval["mean_rank_ic"]):
            champ_name, champ_eval, champ_preds = m, blk, recs
    if champ_preds is None:
        cov0 = {"covered_tickers": 0, "universe_size": len(al.tickers),
                "coverage_fraction": 0.0}
        gates = build_gate_matrix(inv, {}, None, {}, cov0, leakage_ok=True)
        _write_gate_matrix(paths, gates)
        return _finalize({}, None, {}, cov0, gates, REC_BLOCKER, None,
                         {"champion_model": None})

    date_to_idx = {al.master_dates[i]: i for i in al.month_end_indices()}

    # ---- Step 3: PIT event features + covered cross-section ----
    events_by_ticker = load_earnings_events()
    event_feats = build_event_features(p5c, al, events_by_ticker, al.month_end_indices())
    models, cov_info = build_model_records(p5c, al, champ_preds, event_feats, date_to_idx)

    universe_size = len(al.tickers)
    covered_tickers = cov_info["covered_tickers"]
    coverage = {
        "universe_size": universe_size,
        "covered_tickers": covered_tickers,
        "coverage_fraction": round(covered_tickers / universe_size, 4) if universe_size else 0.0,
        "covered_records": cov_info["covered_records"],
        "covered_dates": cov_info["covered_dates"],
    }

    # ---- Step 4: evaluate every model + select best event candidate ----
    evals = evaluate_all(p5c, models)
    best_name, best_sel = select_best_event_candidate(p5c, models, evals)

    # Leakage: structural (as-of join is leak-free by construction) + 5-C embargo.
    leakage_ok = bool(not leakage["features_use_future_data"]
                      and not leakage["labels_use_past_data"]
                      and not leakage["same_date_label_in_features"]
                      and leakage.get("embargo_respected", False))

    ref_ic = evals.get(M_REF, {}).get("mean_rank_ic")
    best_ic = evals.get(best_name, {}).get("mean_rank_ic") if best_name else None
    incr = (best_ic - ref_ic) if (best_ic is not None and ref_ic is not None) else None

    gates = build_gate_matrix(inv, evals, best_name, best_sel, coverage, leakage_ok)
    recommendation = derive_recommendation(inv, evals, best_name, best_sel, coverage, leakage_ok)

    # ---- Step 5: artifacts ----
    _write_feature_catalog(paths, True, inv)
    _write_panel_sample(paths, models, event_feats)
    _write_scoreboard(paths, evals, best_name)
    _write_incremental(paths, p5c, models, evals, best_sel)
    _write_gate_matrix(paths, gates)
    _write_next_plan(paths, recommendation, coverage, best_name, incr)

    p5c_meta = {
        "champion_model": champ_name,
        "champion_mean_rank_ic_full_universe": _f(champ_eval["mean_rank_ic"]),
        "price_history_source": _rel(Path(path)),
        "rebalance_dates": len(date_to_idx),
        "embargo_respected": leakage.get("embargo_respected"),
        "min_embargo_gap_observed": leakage.get("min_embargo_gap_observed"),
    }
    report = _assemble_report(paths, inv, evals, best_name, best_sel, coverage, gates,
                              recommendation, base_flags, incr, p5c_meta,
                              f0c_report=f0c_report)
    _write_json(paths.report, report)
    _write_audit(paths, report, evals, best_name, best_sel, coverage, leakage_ok, p5c_meta)
    return report


def _load_f0c(path: Optional[Path]) -> Optional[dict]:
    p = Path(path) if path else _PHASE5F0C_REPORT
    if not p.is_file():
        return None
    try:
        with open(p, "r", encoding="utf-8") as fh:
            rep = json.load(fh)
    except (OSError, ValueError):
        return None
    bsm = rep.get("best_strategy_metrics") or {}
    return {
        "best_strategy": rep.get("best_strategy"),
        "evaluable": bool(bsm.get("evaluable")),
        "net_50bps_ann_mean_return": _f(bsm.get("net_50bps_ann_mean_return")),
        "signal_mean_rank_ic": _f(bsm.get("signal_mean_rank_ic")),
        "recommendation": rep.get("recommendation"),
    }


def _model_view(b: dict) -> dict:
    if not b.get("evaluable"):
        return {"evaluable": False, "reason": b.get("reason")}
    return {
        "evaluable": True,
        "n_scored_dates": b.get("n_scored_dates"),
        "mean_rank_ic": _f(b.get("mean_rank_ic")),
        "ic_t_stat": _f(b.get("ic_t_stat")),
        "worst_year_ic": _f(b.get("worst_year_ic")),
        "ic_by_year": {y: _f(v) for y, v in (b.get("ic_by_year") or {}).items()},
        "ic_by_regime": {k: {"mean_ic": _f(v["mean_ic"]), "n_dates": v["n_dates"]}
                         for k, v in (b.get("ic_by_regime") or {}).items()},
        "mean_decile_spread": _f(b.get("mean_decile_spread")),
        "top_quintile_hit_rate": _f(b.get("top_quintile_hit_rate")),
        "multi_horizon_ic": b.get("multi_horizon_ic"),
        "placebo_mean_ic": b.get("placebo_mean_ic"),
        "ann_net": b.get("ann_net"),
    }


def _assemble_report(paths, inv, evals, best_name, best_sel, coverage, gates, recommendation,
                     base_flags, incr, p5c_meta, f0c_report=None) -> dict:
    f0c = _load_f0c(f0c_report)
    status_counts = {"PASS": 0, "WARNING": 0, "FAIL": 0, "NOT_EVALUABLE": 0}
    for g in gates:
        status_counts[g["status"]] = status_counts.get(g["status"], 0) + 1
    ref_ic = evals.get(M_REF, {}).get("mean_rank_ic") if evals.get(M_REF, {}).get("evaluable") else None
    best_ic = evals.get(best_name, {}).get("mean_rank_ic") if best_name and evals.get(best_name, {}).get("evaluable") else None

    report = dict(base_flags)
    report.update({
        "objective": ("Test whether a point-in-time earnings / post-earnings-drift event "
                      "signal adds incremental out-of-sample ranking IC over the Phase 5-C "
                      "price-only champion, on the names it covers, without leakage."),
        "generated_by": _rel(Path(__file__)),
        "local_event_data_found": inv["earnings_data_present"],
        "usable_event_data": bool(inv["earnings_data_present"] and inv.get("pit_safe_overall")
                                  and bool(evals)),
        "event_data_sources": [r["file_path"] for r in inv["inventory_rows"] if r["exists"]],
        "coverage_summary": coverage,
        "pit_safety_summary": {
            "availability_is_report_date": inv.get("pit_safe_overall"),
            "uses_fiscal_end_as_timestamp": False,
            "asof_join": "latest earnings event with availability_date <= rebalance_date",
            "no_future_event_used": True,
        },
        "features_built": {
            "earnings_surprise_features_built": bool(inv["surprise_fields_present"]),
            "post_earnings_drift_features_built": True,
            "estimate_revision_features_built": inv["estimate_revisions_present"],
            "revision_omitted_reason": (None if inv["estimate_revisions_present"]
                                        else inv.get("revision_blocker")),
            "feature_catalog": _rel(paths.feature_catalog),
        },
        "models_evaluated": MODEL_ORDER,
        "best_event_candidate": best_name,
        "best_event_candidate_selection": best_sel,
        "model_scoreboard": {m: _model_view(evals.get(m, {})) for m in MODEL_ORDER if m != M_BEST},
        "incremental_edge_vs_phase5c": {
            "reference_mean_rank_ic": _f(ref_ic),
            "best_event_mean_rank_ic": _f(best_ic),
            "incremental_mean_rank_ic": _f(incr),
            "threshold": GATE_INCREMENTAL_IC,
        },
        "phase5c_comparison": {
            "champion_model": p5c_meta.get("champion_model"),
            "reference_mean_rank_ic_covered": _f(ref_ic),
            "note": "phase5c_reference is the champion price score restricted to event-covered names",
        },
        "phase5f0c_comparison": (f0c or {"evaluable": False, "note": "F0C report not found"}),
        "phase5c_reference_meta": p5c_meta,
        "validation_gate_summary": {"counts": status_counts, "gates": gates},
        "recommendation": recommendation,
        "recommendation_allowed_values": list(ALLOWED_RECOMMENDATIONS),
        "recommended_next_phase": {
            "phase": "5-H",
            "title": "Event-Alpha Strategy Test (research / shadow-only)",
            "gated_on": f"event coverage >= {GATE_COVERAGE_SUFFICIENT} universe tickers and a PIT revision series",
        },
        "artifacts": [_rel(p) for p in paths.all_artifacts] + [_rel(paths.audit)],
        "data_blocker_minimum_required_fields": _MIN_REQUIRED_FIELDS,
        "survivorship_biased": True,
        "production_edge_claimed": False,
    })
    assert recommendation in ALLOWED_RECOMMENDATIONS
    return report


# --------------------------------------------------------------------------- #
# Summary print + CLI
# --------------------------------------------------------------------------- #
def _print_summary(report: dict) -> None:
    print("=" * 78)
    print(f"Phase {report['phase']} — Earnings / Revision / Post-Earnings Drift Alpha")
    print("=" * 78)
    print(f"local event data found : {report['local_event_data_found']}")
    print(f"usable event data      : {report['usable_event_data']}")
    cov = report.get("coverage_summary", {})
    print(f"coverage               : {cov.get('covered_tickers')}/{cov.get('universe_size')} "
          f"({cov.get('coverage_fraction')})")
    fb = report.get("features_built", {})
    print(f"surprise features      : {fb.get('earnings_surprise_features_built')}")
    print(f"drift features         : {fb.get('post_earnings_drift_features_built')}")
    print(f"revision features      : {fb.get('estimate_revision_features_built')} "
          f"({fb.get('revision_omitted_reason')})")
    inc = report.get("incremental_edge_vs_phase5c", {})
    print(f"reference IC (covered) : {inc.get('reference_mean_rank_ic')}")
    print(f"best event IC          : {inc.get('best_event_mean_rank_ic')}  "
          f"(best={report.get('best_event_candidate')})")
    print(f"incremental IC         : {inc.get('incremental_mean_rank_ic')} "
          f"(threshold {inc.get('threshold')})")
    sc = report.get("validation_gate_summary", {}).get("counts", {})
    print(f"gates                  : PASS={sc.get('PASS')} WARNING={sc.get('WARNING')} "
          f"FAIL={sc.get('FAIL')} NOT_EVALUABLE={sc.get('NOT_EVALUABLE')}")
    print(f"RECOMMENDATION         : {report['recommendation']}")
    print("=" * 78)


def _parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Phase 5-G earnings/revision event-alpha module (offline).")
    ap.add_argument("--price-csv", default=None, help="override local price-history CSV (read-only)")
    ap.add_argument("--f0c-report", default=None, help="override Phase 5-F0C report path")
    ap.add_argument("--max-tickers", type=int, default=None, help="cap universe (testing only)")
    ap.add_argument("--out-dir", default=None,
                    help="override output directory (default = committed production dir)")
    ap.add_argument("--quiet", action="store_true")
    return ap.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parse_args(argv)
    out_dir = Path(args.out_dir) if args.out_dir else None
    try:
        report = run(price_csv=args.price_csv, max_tickers=args.max_tickers,
                     f0c_report=Path(args.f0c_report) if args.f0c_report else None,
                     out_dir=out_dir)
    except Exception as exc:  # noqa: BLE001 — emit an ERROR report, never a stack-only failure
        report = {
            "phase": PHASE, "recommendation": REC_ERROR, "error": f"{type(exc).__name__}: {exc}",
            "preview_only": True, "orders_enabled": False, "automation_enabled": False,
            "broker_execution_enabled": False, "production_replacement": False,
            "network_used": False, "paid_apis_used": False, "deployed": False,
            "binary_artifacts_created": False,
        }
        _write_json(_OutPaths(out_dir if out_dir is not None else _OUT_DIR).report, report)
        if not args.quiet:
            print(f"Phase 5-G ERROR: {report['error']}")
        return 1
    if not args.quiet:
        _print_summary(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
