"""Phase 13-G (Part A) - Current Alpha Universe Integrity Audit + S&P 500 Shadow.

WHY THIS PHASE EXISTS
    The Phase 13-A / 13-CDE / 13-F Paper-Trader cockpit surfaces the champion
    ``composite_sn`` book. Before wiring a daily trading-desk cockpit on top of it,
    we must state HONESTLY what universe actually produced the ranked panel - and
    NOT silently relabel it "S&P 500" if it is not.

    Lineage (traced, not assumed): the Phase 13-A ranked cross-section reads the
    frozen Phase 10-L scored panel, whose tickers come from the Phase 8-V "combined
    EODHD price+fundamentals universe expansion" (base broad-universe price cache +
    S&P-500-seeded EODHD acquisition), materialized as
    ``research/data/eodhd/normalized/eod_prices/expanded_price_panel.csv``. That is
    an S&P-500-SEEDED but BROADER universe (548 tickers -> 545 scoreable, grown from
    an "old" cohort of 299), NOT a strict S&P 500 index universe.

    This audit (a) reports the validated universe identity + per-name S&P 500
    membership of the latest ranked cross-section, and (b) - because owned
    point-in-time (PIT) S&P 500 membership IS available locally (the Norgate
    "S&P 500 Current & Past" monthly membership panel) - builds a separate
    ``S&P500_SHADOW`` using the SAME ``composite_sn`` formula (NO reweight, NO
    retune, NO new factor, NO threshold optimization): it filters the SAME frozen
    scored cross-section to PIT S&P 500 members and re-runs the SAME quarterly
    quintile L/S evaluation, then compares it to the CURRENT_CHAMPION on its
    original validated universe. The champion is preserved and NEVER auto-replaced.

REUSE (single source of truth - nothing re-implemented)
    c10  = run_phase10c_eodhd_quality_oos_validation                     (_eval, AS_OF)
    d10  = run_phase10d_quarterly_quality_composite_validation           (quarterly_backtest)
    lb10 = run_phase10l_quality_composite_reweighting_robustness_backtest (load_panel,
           quarterly_book, signal_battery)  -- the exact 10-D engine on the frozen panel
    panel  = research/output/phase10l_historical_sector_neutral_scored_panel_reconstruction/
             historical_sector_neutral_scored_panel.csv
    membership = D:/Stock_Prediction_app_data/research_panels/phase8a_norgate_sample/
             membership_panel.csv  (Norgate PIT monthly 1.0/0.0 S&P 500 membership)

DECISIONS (allowed - universe identity)
    CURRENT_UNIVERSE_CONFIRMED_SP500 | CURRENT_UNIVERSE_BROADER_KEEP_CHAMPION
And a SEPARATE shadow verdict (sp500_shadow_decision):
    SP500_SHADOW_READY_FOR_PAPER_TEST | SP500_SHADOW_REJECTED_WEAKER |
    SP500_MEMBERSHIP_DATA_INSUFFICIENT

CONSTRAINTS HONORED
    Fully offline (reads only owned/local frozen panel + 13-A package + owned Norgate
    membership panel; NO network, NO key, NO provider probe); paper/research only;
    owned-local-data only; does NOT change the champion ranks or historical evidence;
    NO reweight / retune / new factor / threshold optimization; NO Paper Trader writes;
    NO orders / automation / broker / deploy / GCP; output is metadata / research
    CSV+JSON only. No commit inside the runner. No push.
"""
from __future__ import annotations

import argparse
import bisect
import csv
import json
import math
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from research import run_phase10c_eodhd_quality_oos_validation as c10           # noqa: E402
from research import run_phase10d_quarterly_quality_composite_validation as d10  # noqa: E402
from research import (                                                            # noqa: E402
    run_phase10l_quality_composite_reweighting_robustness_backtest as lb10,
)

PHASE = "13-G"
PHASE_NAME = "Current Alpha Universe Integrity Audit + S&P 500 Shadow"
STEM = "phase13g_current_alpha_universe_integrity_audit"
PERFORMS_NETWORK = False

RET_PRIMARY = "fwd_exc_63"
_PRE2020 = "2020-01-01"
EODHD_KEY_ENV = "EODHD_API_KEY"

# --- inputs (owned / local) ------------------------------------------------- #
_PANEL_REL = ("research/output/phase10l_historical_sector_neutral_scored_panel_reconstruction/"
              "historical_sector_neutral_scored_panel.csv")
_DEFAULT_PANEL = _REPO_ROOT / _PANEL_REL
_DEFAULT_PACKAGE_DIR = (_REPO_ROOT / "research" / "output"
                        / "phase13a_current_champion_alpha_paper_test_package")
_DEFAULT_MEMBERSHIP = Path(
    "D:/Stock_Prediction_app_data/research_panels/phase8a_norgate_sample/membership_panel.csv")

# --- decisions -------------------------------------------------------------- #
DEC_CONFIRMED_SP500 = "CURRENT_UNIVERSE_CONFIRMED_SP500"
DEC_BROADER = "CURRENT_UNIVERSE_BROADER_KEEP_CHAMPION"
ALLOWED_UNIVERSE_DECISIONS = (DEC_CONFIRMED_SP500, DEC_BROADER)

SHADOW_READY = "SP500_SHADOW_READY_FOR_PAPER_TEST"
SHADOW_REJECTED = "SP500_SHADOW_REJECTED_WEAKER"
SHADOW_INSUFFICIENT = "SP500_MEMBERSHIP_DATA_INSUFFICIENT"
ALLOWED_SHADOW_DECISIONS = (SHADOW_READY, SHADOW_REJECTED, SHADOW_INSUFFICIENT)

# A validated universe is "confirmed S&P 500" only if effectively all ranked names
# are PIT members. It is broader if a material fraction are not.
_CONFIRMED_SP500_MIN_FRACTION = 0.98

# Membership classification labels for a single ranked name.
MEMB_CONFIRMED = "CONFIRMED_SP500"          # PIT member at the signal-date month
MEMB_NOT = "NOT_CONFIRMED_SP500"            # in the S&P superset but not a member then
MEMB_NOT_IN_SUPERSET = "NOT_SP500_NOT_IN_SUPERSET"  # never in S&P 500 Current & Past
MEMB_UNKNOWN = "UNKNOWN_MEMBERSHIP"         # membership panel unavailable / unresolvable

_ARTIFACTS = {
    "report": "%s.json" % STEM,
    "membership": "current_alpha_universe_membership.csv",
}

_MEMBERSHIP_CSV_HEADER = [
    "rank", "ticker", "sector", "composite_sn",
    "in_champion_top25", "in_champion_top50",
    "sp500_member_pit", "sp500_membership_status",
    "in_shadow_top25", "in_shadow_top50",
]


class _Log:
    def __init__(self, verbose: bool = True):
        self.verbose = verbose

    def step(self, stage: str = "", status: str = "", msg: str = "", **kwargs) -> None:
        if self.verbose:
            extra = (" " + " ".join("%s=%s" % (k, v) for k, v in kwargs.items())) if kwargs else ""
            print("[%s] %-11s %-9s %s%s" % (PHASE, stage, status, msg, extra))


# --------------------------------------------------------------------------- #
# Small pure helpers.
# --------------------------------------------------------------------------- #
def _finite(x) -> bool:
    return x is not None and isinstance(x, (int, float)) and not (
        isinstance(x, float) and math.isnan(x))


def _r(x, nd: int = 6):
    return round(float(x), nd) if _finite(x) else None


def _write_csv(path: Path, header: Sequence[str], rows) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(list(header))
        for row in rows:
            w.writerow(["" if v is None else v for v in row])


def _write_json(path: Path, obj: Dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=2, default=str)


# --------------------------------------------------------------------------- #
# A. Owned PIT S&P 500 membership (Norgate monthly 1.0/0.0 panel).
# --------------------------------------------------------------------------- #
class Membership:
    """Point-in-time S&P 500 membership from the owned Norgate monthly panel.

    The panel is a wide matrix: column 0 = ``Date`` (month-end), remaining columns
    are tickers (active names as plain symbols, delisted names carry a ``-YYYYMM``
    suffix). A cell is ``1.0`` (member that month) or ``0.0`` (not). We resolve the
    membership state as-of a rebalance date strictly point-in-time: the latest
    month-end with ``date <= rebalance_date`` (never a future month).
    """

    def __init__(self, dates: List[str], series: Dict[str, List[float]]):
        self._dates = dates                 # sorted ascending ISO month-end strings
        self._series = series               # ticker -> per-date member flag (1.0/0.0/nan)

    @property
    def available(self) -> bool:
        return bool(self._dates) and bool(self._series)

    @property
    def n_dates(self) -> int:
        return len(self._dates)

    @property
    def n_tickers(self) -> int:
        return len(self._series)

    @property
    def date_min(self) -> Optional[str]:
        return self._dates[0] if self._dates else None

    @property
    def date_max(self) -> Optional[str]:
        return self._dates[-1] if self._dates else None

    def in_superset(self, ticker: str) -> bool:
        return ticker in self._series

    def _asof_index(self, rebalance_date: str) -> int:
        """Index of the latest membership month-end with date <= rebalance_date."""
        # bisect_right on the date strings (ISO sorts lexicographically == chronologically)
        return bisect.bisect_right(self._dates, str(rebalance_date)[:10]) - 1

    def member_asof(self, ticker: str, rebalance_date: str) -> Optional[bool]:
        """True/False if resolvable point-in-time; None if ticker not in the superset
        or no membership month is available at/before the rebalance date."""
        col = self._series.get(ticker)
        if col is None:
            return None
        idx = self._asof_index(rebalance_date)
        if idx < 0 or idx >= len(col):
            return None
        val = col[idx]
        if val is None or (isinstance(val, float) and math.isnan(val)):
            return False
        return float(val) >= 0.5


def load_membership(path: Path, universe: Optional[Sequence[str]] = None
                    ) -> Tuple[Membership, Optional[str]]:
    """Load the Norgate PIT S&P 500 membership panel (owned local file).

    Only plain (active-name) columns are kept for lookup - a delist-suffixed column
    (e.g. ``AAL-199702``) refers to a historical listing identity and is not matched
    to a current ticker. If ``universe`` is given, only those columns are retained
    (bounded memory); otherwise all plain columns are kept.
    """
    if not Path(path).is_file():
        return Membership([], {}), (
            "owned Norgate S&P 500 membership panel not found: %s" % path)
    want = set(universe) if universe else None
    with open(path, "r", encoding="utf-8", newline="") as fh:
        reader = csv.reader(fh)
        header = next(reader, None)
        if not header or header[0].strip().lower() != "date":
            return Membership([], {}), "membership panel header is not a Date-indexed matrix"
        # Column indexes to keep: plain ticker names (no delist suffix), optionally
        # restricted to the universe.
        keep_cols: List[Tuple[int, str]] = []
        for i, name in enumerate(header):
            if i == 0:
                continue
            tk = name.strip()
            if not tk or ("-" in tk and tk.rsplit("-", 1)[-1].isdigit()):
                continue  # skip Date and delist-suffixed historical identities
            if want is not None and tk not in want:
                continue
            keep_cols.append((i, tk))
        dates: List[str] = []
        series: Dict[str, List[float]] = {tk: [] for _, tk in keep_cols}
        for row in reader:
            if not row:
                continue
            dates.append(str(row[0]).strip()[:10])
            for i, tk in keep_cols:
                cell = row[i] if i < len(row) else ""
                try:
                    series[tk].append(float(cell) if str(cell).strip() != "" else float("nan"))
                except (TypeError, ValueError):
                    series[tk].append(float("nan"))
    return Membership(dates, series), None


# --------------------------------------------------------------------------- #
# B. Latest ranked cross-section (mirrors the 13-A "latest month" selection).
# --------------------------------------------------------------------------- #
def latest_cross_section(df):
    """Return (rows, signal_month, signal_date) for the latest calendar month in the
    panel: one row per ticker (last by entry_date) with a valid composite_sn, ranked
    by composite_sn descending. Ranks/values are the champion's, unchanged."""
    import pandas as pd  # noqa: F401
    sub = df[df["comp_sn"].notna()].copy()
    if sub.empty:
        return [], None, None
    last_month = sub["month"].max()
    cs = sub[sub["month"] == last_month].sort_values("entry_date").groupby(
        "ticker", as_index=False).last()
    cs = cs.sort_values("comp_sn", ascending=False).reset_index(drop=True)
    signal_date = str(cs["entry_date"].max().date()) if len(cs) else None
    rows: List[Dict[str, Any]] = []
    for i, rec in enumerate(cs.itertuples(index=False)):
        d = rec._asdict()
        rows.append({
            "rank": i + 1,
            "ticker": str(d.get("ticker")),
            "sector": (str(d.get("sector")) if d.get("sector") is not None else "Unknown"),
            "composite_sn": _r(d.get("comp_sn")),
        })
    return rows, str(last_month), signal_date


# --------------------------------------------------------------------------- #
# C. Quarterly quintile L/S evaluation (SAME engine as 10-D) + equity metrics.
# --------------------------------------------------------------------------- #
def _quarter_spreads(df, sigcol: str, ret_col: str = RET_PRIMARY,
                     min_names: int = lb10._QTR_MIN_NAMES) -> List[float]:
    """Per-quarter quintile long-short spreads (top quintile mean - bottom quintile
    mean), mirroring ``lb10.quarterly_book`` so cumulative-return and drawdown are
    computed on the SAME book the net-of-cost stats come from."""
    import numpy as np  # noqa: F401
    import pandas as pd
    if df is None or getattr(df, "empty", True):
        return []
    sub = df[df[sigcol].notna() & df[ret_col].notna()].copy()
    if sub.empty:
        return []
    sub["q"] = sub["entry_date"].dt.to_period("Q")
    spreads: List[float] = []
    for q in sorted(sub["q"].unique()):
        chunk = sub[sub["q"] == q].sort_values("entry_date").groupby(
            "ticker", as_index=False).last()
        if len(chunk) < min_names:
            continue
        try:
            qd = pd.qcut(chunk[sigcol].rank(method="first"), 5, labels=False)
        except ValueError:
            continue
        top, bot = chunk[qd == 4], chunk[qd == 0]
        if top.empty or bot.empty:
            continue
        spreads.append(float(top[ret_col].mean() - bot[ret_col].mean()))
    return spreads


def _equity_metrics(spreads: List[float]) -> Dict[str, Any]:
    """Cumulative (compounded) return + max drawdown from a per-quarter spread series."""
    if not spreads:
        return {"cumulative_return": None, "max_drawdown": None, "n_quarters": 0}
    equity = 1.0
    peak = 1.0
    max_dd = 0.0
    for s in spreads:
        equity *= (1.0 + s)
        peak = max(peak, equity)
        dd = equity / peak - 1.0
        max_dd = min(max_dd, dd)
    return {"cumulative_return": _r(equity - 1.0), "max_drawdown": _r(max_dd),
            "n_quarters": len(spreads)}


def evaluate_universe(df, label: str) -> Dict[str, Any]:
    """Full quarterly quintile L/S evaluation of composite_sn on a (possibly filtered)
    panel using the exact 10-D engine, plus IC and equity-curve metrics."""
    book = lb10.quarterly_book(df, "comp_sn", ret_col=RET_PRIMARY, cap_frac=None)
    ic = c10._eval(df, "comp_sn", 63, False)
    spreads = _quarter_spreads(df, "comp_sn")
    equity = _equity_metrics(spreads)
    n_scoreable = int((df["comp_sn"].notna() & df[RET_PRIMARY].notna()).sum())
    return {
        "label": label,
        "coverage_rows_scoreable": n_scoreable,
        "n_tickers": int(df["ticker"].nunique()) if "ticker" in df else None,
        "ic_mean_63d": _r(ic.get("mean_ic")),
        "ic_t_63d": _r(ic.get("ic_t"), 3),
        "top_sector_share": _r(ic.get("top_sector_share"), 4),
        "average_quarterly_return": _r(book.get("mean_spread")),
        "quarterly_spread_t": _r(book.get("spread_t"), 3),
        "hit_rate": _r(book.get("spread_hit_rate"), 4),
        "turnover": _r(book.get("avg_turnover"), 4),
        "net_25bps": _r(book.get("net_25bps")),
        "net_50bps": _r(book.get("net_50bps")),
        "avg_long_count": _r(book.get("avg_long_count"), 2),
        "avg_short_count": _r(book.get("avg_short_count"), 2),
        "n_quarters": int(book.get("n_quarters", 0) or 0),
        "cumulative_return": equity["cumulative_return"],
        "max_drawdown": equity["max_drawdown"],
    }


# --------------------------------------------------------------------------- #
# D. Membership classification of the latest ranked cross-section.
# --------------------------------------------------------------------------- #
def classify_membership(rows: List[Dict[str, Any]], membership: Membership,
                        signal_date: str) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    counts = {MEMB_CONFIRMED: 0, MEMB_NOT: 0, MEMB_NOT_IN_SUPERSET: 0, MEMB_UNKNOWN: 0}
    out: List[Dict[str, Any]] = []
    for r in rows:
        tk = r["ticker"]
        if not membership.available:
            status, member = MEMB_UNKNOWN, None
        elif not membership.in_superset(tk):
            status, member = MEMB_NOT_IN_SUPERSET, False
        else:
            m = membership.member_asof(tk, signal_date)
            if m is None:
                status, member = MEMB_UNKNOWN, None
            elif m:
                status, member = MEMB_CONFIRMED, True
            else:
                status, member = MEMB_NOT, False
        counts[status] += 1
        out.append({**r, "sp500_member_pit": member, "sp500_membership_status": status})
    return out, counts


# --------------------------------------------------------------------------- #
# E. Decision logic.
# --------------------------------------------------------------------------- #
def decide_universe(n_ranked: int, counts: Dict[str, int]) -> Tuple[str, str]:
    confirmed = counts.get(MEMB_CONFIRMED, 0)
    frac = (confirmed / n_ranked) if n_ranked else 0.0
    if n_ranked and frac >= _CONFIRMED_SP500_MIN_FRACTION:
        return DEC_CONFIRMED_SP500, (
            "%d of %d latest-ranked names (%.1f%%) are confirmed PIT S&P 500 members "
            "-> effectively a strict S&P 500 universe." % (confirmed, n_ranked, 100 * frac))
    return DEC_BROADER, (
        "only %d of %d latest-ranked names (%.1f%%) are confirmed PIT S&P 500 members; "
        "the validated universe is S&P-500-SEEDED but BROADER (Phase 8-V combined EODHD "
        "expansion). Keep composite_sn on its ORIGINAL validated universe as CURRENT_CHAMPION; "
        "do NOT relabel it S&P 500 and do NOT change its ranks or historical evidence."
        % (confirmed, n_ranked, 100 * frac))


def decide_shadow(membership: Membership, champion: Dict[str, Any],
                  shadow: Optional[Dict[str, Any]]) -> Tuple[str, str]:
    if not membership.available or shadow is None:
        return SHADOW_INSUFFICIENT, (
            "owned PIT S&P 500 membership panel is unavailable or the filtered shadow "
            "panel has no scoreable rows; cannot build a methodologically valid shadow.")
    if (shadow.get("n_quarters", 0) or 0) < 8 or shadow.get("net_25bps") is None:
        return SHADOW_INSUFFICIENT, (
            "the PIT S&P 500 shadow has too few backtested quarters / no net-of-cost "
            "estimate to compare against the champion.")
    c_net25 = champion.get("net_25bps")
    s_net25 = shadow.get("net_25bps")
    if _finite(c_net25) and _finite(s_net25) and s_net25 >= c_net25:
        return SHADOW_READY, (
            "the PIT S&P 500 shadow (same composite_sn formula, membership-filtered) has "
            "quarterly net-25bps %s >= the champion's %s on its broader universe; it is a "
            "valid, comparable paper-test candidate. It does NOT replace the champion."
            % (_r(s_net25), _r(c_net25)))
    return SHADOW_REJECTED, (
        "the PIT S&P 500 shadow quarterly net-25bps %s is weaker than the champion's %s on "
        "the broader validated universe; keep the champion, treat the shadow as research "
        "comparison only." % (_r(s_net25), _r(c_net25)))


# --------------------------------------------------------------------------- #
# F. Orchestration.
# --------------------------------------------------------------------------- #
def run(panel_path: Path, package_dir: Path, membership_path: Path, out_dir: Path,
        log: Optional[_Log] = None) -> Dict[str, Any]:
    log = log or _Log()

    df, panel_meta, perr = lb10.load_panel(Path(panel_path))
    if perr:
        raise SystemExit("[%s] panel load failed: %s" % (PHASE, perr))
    log.step("panel", "DONE", "loaded", rows=panel_meta.get("n_rows"),
             tickers=panel_meta.get("n_tickers"))

    rows, signal_month, signal_date = latest_cross_section(df)
    n_ranked = len(rows)
    log.step("cross_section", "DONE", "latest month %s" % signal_month, n_ranked=n_ranked)

    universe_tickers = [r["ticker"] for r in rows]
    membership, merr = load_membership(Path(membership_path),
                                       universe=None)  # keep all plain columns for coverage
    if merr:
        log.step("membership", "WARN", merr)
    else:
        log.step("membership", "DONE", "loaded", dates=membership.n_dates,
                 tickers=membership.n_tickers)

    classified, counts = classify_membership(rows, membership, signal_date or "")

    # --- champion book membership (top25 / top50 by champion rank) -----------
    champ_top25 = {r["ticker"] for r in rows[:25]}
    champ_top50 = {r["ticker"] for r in rows[:50]}

    # --- champion evaluation on the ORIGINAL validated (broader) universe -----
    champion_eval = evaluate_universe(df, "CURRENT_CHAMPION (validated broader universe)")

    # --- S&P 500 SHADOW: filter the SAME frozen panel to PIT members ----------
    shadow_eval: Optional[Dict[str, Any]] = None
    shadow_top25: set = set()
    shadow_top50: set = set()
    shadow_panel_meta: Dict[str, Any] = {}
    if membership.available:
        import pandas as pd  # noqa: F401
        entry_iso = df["entry_date"].dt.strftime("%Y-%m-%d")
        mask = [
            bool(membership.member_asof(tk, dt))
            for tk, dt in zip(df["ticker"].astype(str), entry_iso)
        ]
        sdf = df[pd.Series(mask, index=df.index)].copy()
        shadow_panel_meta = {
            "n_rows": int(len(sdf)),
            "n_tickers": int(sdf["ticker"].nunique()) if "ticker" in sdf else 0,
            "n_scoreable": int((sdf["comp_sn"].notna() & sdf[RET_PRIMARY].notna()).sum()),
        }
        if shadow_panel_meta["n_scoreable"] > 0:
            shadow_eval = evaluate_universe(sdf, "S&P500_SHADOW (PIT membership-filtered)")
            # latest-month shadow books = highest-composite_sn PIT members, unchanged ranks
            shadow_members = [r for r in classified if r["sp500_membership_status"] == MEMB_CONFIRMED]
            shadow_top25 = {r["ticker"] for r in shadow_members[:25]}
            shadow_top50 = {r["ticker"] for r in shadow_members[:50]}
        log.step("shadow", "DONE", "PIT-filtered panel",
                 rows=shadow_panel_meta.get("n_rows"), scoreable=shadow_panel_meta.get("n_scoreable"))

    universe_decision, universe_rationale = decide_universe(n_ranked, counts)
    shadow_decision, shadow_rationale = decide_shadow(membership, champion_eval, shadow_eval)

    # --- membership CSV ------------------------------------------------------
    csv_rows = []
    for r in classified:
        tk = r["ticker"]
        csv_rows.append([
            r["rank"], tk, r["sector"], r["composite_sn"],
            tk in champ_top25, tk in champ_top50,
            r["sp500_member_pit"], r["sp500_membership_status"],
            tk in shadow_top25, tk in shadow_top50,
        ])
    _write_csv(out_dir / _ARTIFACTS["membership"], _MEMBERSHIP_CSV_HEADER, csv_rows)

    confirmed = counts.get(MEMB_CONFIRMED, 0)
    not_confirmed = counts.get(MEMB_NOT, 0) + counts.get(MEMB_NOT_IN_SUPERSET, 0)
    unknown = counts.get(MEMB_UNKNOWN, 0)

    report = {
        "phase": PHASE,
        "phase_part": "A",
        "phase_name": PHASE_NAME,
        "objective": ("state honestly what universe produced the Phase 13-A composite_sn ranked "
                      "panel; classify each latest-ranked name's PIT S&P 500 membership; and build a "
                      "separate S&P500_SHADOW (same formula, membership-filtered) for comparison "
                      "without ever auto-replacing the champion."),
        "offline": True,
        "performs_network": PERFORMS_NETWORK,
        "eodhd_key_visible": bool(os.environ.get(EODHD_KEY_ENV)),
        "eodhd_key_required": False,
        # --- universe identity (the headline answers) -----------------------
        "validated_alpha_universe_name": "phase8v_combined_eodhd_price_fundamentals_universe",
        "universe_definition": (
            "S&P-500-SEEDED but BROADER combined EODHD universe (Phase 8-V): union of a base "
            "broad-universe price cache (phase7i_broad_universe, 301 priced tickers) and 247 "
            "EODHD-acquired S&P-500 names, materialized as "
            "research/data/eodhd/normalized/eod_prices/expanded_price_panel.csv = 548 tickers -> "
            "545 scoreable (grown from an 'old' cohort of 299). Filters: PIT fundamentals attach "
            "(available_date <= rebalance_date), both-legs composite intersection. Frozen into the "
            "Phase 10-L scored panel that Phase 13-A ranks."),
        "latest_ranked_count": n_ranked,
        "signal_month": signal_month,
        "signal_date": signal_date,
        "is_strict_sp500_universe": (universe_decision == DEC_CONFIRMED_SP500),
        "evidence": [
            "panel column note: 'issuer symbol (Norgate survivorship-free universe)' is an "
            "aspirational label; the actual membership is the Phase 8-V S&P-500-seeded EODHD "
            "acquisition, not a Norgate index-membership query.",
            "phase8v report: total_tickers=548 > sp500_name_list=504; base cache dir is literally "
            "named phase7i_broad_universe; scoreable grew 299 -> 545.",
            "latest ranked cross-section includes non-S&P names (e.g. foreign ADRs) that are absent "
            "from the owned Norgate S&P 500 Current & Past superset.",
        ],
        "latest_cross_section_membership": {
            "n_ranked": n_ranked,
            "confirmed_sp500": confirmed,
            "not_confirmed_sp500": not_confirmed,
            "unknown_membership": unknown,
            "confirmed_fraction": _r((confirmed / n_ranked) if n_ranked else 0.0, 4),
            "breakdown": counts,
        },
        "decision": universe_decision,
        "decision_rationale": universe_rationale,
        "allowed_decisions": list(ALLOWED_UNIVERSE_DECISIONS),
        "champion_preserved": True,
        "champion_note": ("composite_sn is preserved on its ORIGINAL validated universe as "
                          "CURRENT_CHAMPION; ranks and historical evidence are unchanged. The "
                          "champion is NEVER auto-replaced by this audit."),
        # --- owned PIT membership provenance --------------------------------
        "sp500_membership_source": {
            "path": str(membership_path),
            "available": membership.available,
            "provider": "Norgate 'S&P 500 Current & Past' (owned local monthly panel)",
            "resolution": "monthly month-end 1.0/0.0, resolved strictly point-in-time (latest "
                          "month-end <= rebalance_date)",
            "date_min": membership.date_min,
            "date_max": membership.date_max,
            "n_dates": membership.n_dates,
            "n_tickers_in_superset": membership.n_tickers,
            "caveats": [
                "the owned panel is a 1,363-of-1,894 SAMPLE of the full S&P 500 Current & Past "
                "superset; a handful of names may be unresolvable.",
                "monthly (not daily) membership resolution.",
                "delist-suffixed historical identities are not matched to current tickers.",
            ],
        },
        # --- champion vs shadow comparison ----------------------------------
        "current_champion": champion_eval,
        "sp500_shadow": shadow_eval,
        "sp500_shadow_panel": shadow_panel_meta or None,
        "sp500_shadow_books": {
            "shadow_top25_count": len(shadow_top25),
            "shadow_top50_count": len(shadow_top50),
            "shadow_top25_tickers": sorted(shadow_top25),
            "shadow_top50_tickers": sorted(shadow_top50),
            "champion_top25_that_are_sp500": len(champ_top25 & shadow_top25),
            "champion_top50_that_are_sp500": len(champ_top50 & shadow_top50),
        },
        "champion_vs_shadow_delta": (
            {
                "net_25bps_delta_shadow_minus_champion": _r(
                    (shadow_eval.get("net_25bps") or 0) - (champion_eval.get("net_25bps") or 0))
                if shadow_eval else None,
                "net_50bps_delta_shadow_minus_champion": _r(
                    (shadow_eval.get("net_50bps") or 0) - (champion_eval.get("net_50bps") or 0))
                if shadow_eval else None,
                "ic_t_delta_shadow_minus_champion": _r(
                    (shadow_eval.get("ic_t_63d") or 0) - (champion_eval.get("ic_t_63d") or 0), 3)
                if shadow_eval else None,
            } if shadow_eval else None),
        "sp500_shadow_decision": shadow_decision,
        "sp500_shadow_decision_rationale": shadow_rationale,
        "allowed_shadow_decisions": list(ALLOWED_SHADOW_DECISIONS),
        "shadow_method_note": ("the shadow filters the SAME frozen scored cross-section to PIT S&P "
                               "500 members and re-runs the SAME quarterly quintile L/S evaluation; "
                               "NO reweight, NO retune, NO new factor, NO threshold optimization - "
                               "composite_sn values and ranks are unchanged for retained names."),
        "panel_source": {"path": _PANEL_REL, **panel_meta},
        "package_dir": str(package_dir),
        # --- safety ---------------------------------------------------------
        "creates_orders": False, "creates_automation": False, "creates_broker_connection": False,
        "wrote_to_paper_trader": False, "live_trading": False, "deploy": False,
        "uses_paid_data": False, "reweighted_or_retuned_champion": False,
        "changed_champion_ranks": False, "safety_badges": [
            "RESEARCH AUDIT", "PREVIEW ONLY", "NO ORDERS", "NO BROKER", "NO AUTOMATION",
            "NO LIVE TRADING", "CHAMPION UNCHANGED"],
        "artifacts": list(_ARTIFACTS.values()),
    }
    _write_json(out_dir / _ARTIFACTS["report"], report)
    log.step("decision", "DONE", "%s / %s" % (universe_decision, shadow_decision))
    log.step("write", "DONE", "artifacts -> %s" % out_dir)
    return report


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Phase 13-G Part A universe integrity audit")
    ap.add_argument("--panel", default=str(_DEFAULT_PANEL))
    ap.add_argument("--package-dir", default=str(_DEFAULT_PACKAGE_DIR))
    ap.add_argument("--membership", default=str(_DEFAULT_MEMBERSHIP))
    ap.add_argument("--out-dir", default=str(_REPO_ROOT / "research" / "output" / STEM))
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(list(argv) if argv is not None else None)
    log = _Log(verbose=not args.quiet)
    report = run(Path(args.panel), Path(args.package_dir), Path(args.membership),
                 Path(args.out_dir), log)
    print("[%s] DECISION %s | SHADOW %s" % (PHASE, report["decision"],
                                            report["sp500_shadow_decision"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
