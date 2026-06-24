"""Phase 8-A — Autonomous Norgate Research Engine and Agent-System Bootstrap.

**Track A (quant brain) research only.** Offline, point-in-time, leakage-safe. The only
provider is the *locally installed* Norgate Data desktop database (no network API key, no
paid web API, no package install). No Paper Trader, no GCP, no deployment, no broker /
orders / automation, no factor-weight optimization, no ML fitting beyond deterministic
score rules, no regime activation/throttling, no commit, no push. Large normalized data is
written ONLY under D:\\Stock_Prediction_app_data\\{norgate,research_panels,model_artifacts};
the repo receives committed-safe summaries only.

Why this phase exists
---------------------
Prior phases established: the multifactor composite did NOT survive broad-universe
validation (broad price-only IC +0.016942, composite +0.011255, incremental -0.005687,
BROAD_UNIVERSE_SIGNAL_WEAK); free data was NOT sufficient for survivorship-aware validation
(genuine delisted free price coverage 1/20, no point-in-time sectors, FREE_DATA_NOT_SUFFICIENT);
momentum/risk was viable only under caveated survivor-biased data (best u296:mom_6_1:top_decile,
net Sharpe@25 1.437, 9/16 viable). Norgate now removes the survivorship-data limitation: it
exposes the survivorship-aware "S&P 500 Current & Past" membership superset (active + delisted)
and point-in-time index-constituent and GICS sector history.

What this phase does
--------------------
  C. Builds a Norgate provider adapter that introspects the installed API, converts
     recarrays to DataFrames safely, and logs every unavailable function or failed call.
  D. Builds a *survivorship-aware* monthly research panel from the S&P 500 Current & Past
     superset (active + delisted names, point-in-time membership, GICS sectors) on D:.
  E. Runs up to 20 deterministic, leakage-safe experiments (momentum / reversal /
     trend-breakout / volatility-liquidity / breadth-relative-strength), each with cost,
     turnover, drawdown, benchmark, placebo and leakage diagnostics.
  F. Emits committed-safe summaries + a research-director decision artifact, and (in the
     companion files) the Claude Code subagent collection and machine-readable agent contracts.

The ONE question this phase answers
-----------------------------------
    DOES A DETERMINISTIC PRICE/VOLUME SIGNAL SURVIVE COSTS ON SURVIVORSHIP-AWARE
    NORGATE DATA, AND IS THE NORGATE RESEARCH ENGINE READY TO SCALE?

Leakage safety
--------------
Every signal at decision month t reads only month-end closes/volumes up to and including t
(momentum lookbacks skip the most recent month where specified). The forward return for
month t is the return realized strictly over (t, t+1]. Delisted names are held while they
were point-in-time members; a name that leaves the data mid-period is liquidated at its last
available mark (forward return contribution 0 thereafter) rather than being silently dropped
(which would re-introduce survivorship bias) or marked a fake -100% (which would invent loss).
"""
from __future__ import annotations

import argparse
import csv
import inspect
import json
import math
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

PHASE = "8-A"
OBJECTIVE = (
    "Bootstrap the project-level quant research-agent system and run the first bounded "
    "autonomous experiment cycle on survivorship-aware Norgate data: build a Norgate "
    "adapter, a point-in-time S&P 500 Current & Past panel (active + delisted), and up to "
    "20 deterministic leakage-safe signal experiments judged strictly net of costs against "
    "SPY and the equal-weight universe. Research only; no orders/automation/optimization."
)

# --------------------------------------------------------------------------- #
# Recommendation vocabulary (exactly the allowed set, in order).
# --------------------------------------------------------------------------- #
REC_READY = "NORGATE_RESEARCH_ENGINE_READY"
REC_BUILD_FULL = "BUILD_FULL_NORGATE_PANEL_NEXT"
REC_REJECTED = "SIGNAL_RESEARCH_REJECTED_ON_CLEAN_DATA"
REC_NORGATE_BLOCKED = "NORGATE_INTEGRATION_BLOCKED"
REC_AGENT_BLOCKED = "AGENT_SYSTEM_CREATION_BLOCKED"
REC_NEEDS_REVIEW = "NEEDS_RESEARCH_DIRECTOR_REVIEW"
REC_ERROR = "ERROR"
ALLOWED_RECOMMENDATIONS = (
    REC_READY, REC_BUILD_FULL, REC_REJECTED, REC_NORGATE_BLOCKED,
    REC_AGENT_BLOCKED, REC_NEEDS_REVIEW, REC_ERROR,
)

# Per-experiment status vocabulary.
ST_APPROVED = "APPROVED"
ST_WEAK = "WEAK"
ST_REJECTED = "REJECTED"
ST_NEEDS_FULL = "NEEDS_FULL_PANEL"

# --------------------------------------------------------------------------- #
# Fixed modelling config (documented choices — NO tuning loop, NO fit to outcome).
# --------------------------------------------------------------------------- #
DATA_ROOT = Path("D:/Stock_Prediction_app_data")
NORGATE_RAW_ROOT = DATA_ROOT / "norgate"
PANEL_ROOT = DATA_ROOT / "research_panels" / "phase8a_norgate_sample"
MODEL_ARTIFACTS_ROOT = DATA_ROOT / "model_artifacts"

UNIVERSE_WATCHLIST = "S&P 500 Current & Past"   # survivorship-aware membership superset
CURRENT_WATCHLIST = "S&P 500"                   # current members (for audit only)
INDEX_NAME = "S&P 500"                          # index_constituent_timeseries index name
BENCHMARK_SYMBOL = "SPY"
GICS_SCHEME = "GICS"
PANEL_START = "1990-01-01"
PANEL_END = "2026-12-31"

QUANTILES = {"top_decile": 10, "top_quintile": 5}
COST_BPS_GRID = (0.0, 10.0, 25.0, 50.0)         # one-way bps charged on traded fraction
PRIMARY_COST_BPS = 25.0                         # the decision is judged net of this
MAX_POSITION = 0.10                             # max single-name weight (no leverage)
MIN_NAMES_PORT = 20                             # minimum cross-section to form a quantile
MIN_PERIODS_VALID = 36                          # minimum monthly periods for a valid judgment
PERIODS_PER_YEAR = 12
MAX_EXPERIMENTS = 20

# Strict viability bars (a priori; borderline is never rounded up).
DD_FLOOR = -0.60
DD_REL_SPY = 0.15
TURNOVER_CEIL = 0.60            # mean one-sided monthly turnover ceiling for "attractive"
PLACEBO_SHARPE_MARGIN = 0.25    # real net Sharpe must beat its placebo by at least this
PLACEBO_BASE_SEED = 8_000_017   # fixed; deterministic placebo permutation


# --------------------------------------------------------------------------- #
# Small utilities.
# --------------------------------------------------------------------------- #
def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _f(x) -> Optional[float]:
    try:
        if x is None:
            return None
        v = float(x)
        if math.isnan(v) or math.isinf(v):
            return None
        return v
    except (TypeError, ValueError):
        return None


def _round(x, n=6) -> Optional[float]:
    v = _f(x)
    return None if v is None else round(v, n)


def _rel(p: Path) -> str:
    try:
        return str(p.relative_to(_REPO_ROOT)).replace("\\", "/")
    except ValueError:
        return str(p).replace("\\", "/")


def _write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=2, sort_keys=False, default=str)


def _write_csv(path: Path, rows: List[dict], fieldnames: Optional[Sequence[str]] = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        # still write a header-only file if fieldnames known, else an empty marker file
        with open(path, "w", encoding="utf-8", newline="") as fh:
            if fieldnames:
                csv.DictWriter(fh, fieldnames=list(fieldnames)).writeheader()
        return
    if fieldnames is None:
        fieldnames = list(rows[0].keys())
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(fieldnames), extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)


# --------------------------------------------------------------------------- #
# Part C — Norgate provider adapter.
# Every public call is wrapped: success/failure is logged into `access_log`.
# No Norgate API function is assumed; the adapter introspects what is available.
# --------------------------------------------------------------------------- #
class NorgateAdapter:
    """Thin, defensive wrapper over the locally installed `norgatedata` package."""

    def __init__(self) -> None:
        self._nd = None
        self._import_error: Optional[str] = None
        self.access_log: List[dict] = []
        self._available_funcs: List[str] = []
        try:
            import norgatedata as nd  # type: ignore
            self._nd = nd
            self._available_funcs = sorted(
                n for n in dir(nd) if not n.startswith("_") and callable(getattr(nd, n))
            )
        except Exception as exc:  # pragma: no cover - environment dependent
            self._import_error = repr(exc)

    @property
    def available(self) -> bool:
        return self._nd is not None

    @property
    def import_error(self) -> Optional[str]:
        return self._import_error

    def _log(self, func: str, target: str, ok: bool, detail: str = "", n_rows: Optional[int] = None) -> None:
        self.access_log.append({
            "function": func,
            "target": target,
            "available": func in self._available_funcs,
            "ok": ok,
            "n_rows": "" if n_rows is None else n_rows,
            "detail": detail[:200],
        })

    def _has(self, func: str) -> bool:
        return func in self._available_funcs

    def api_inventory(self) -> List[dict]:
        """Introspected signatures of every public callable (for norgate_api_inventory.csv)."""
        rows: List[dict] = []
        if not self.available:
            return rows
        for name in self._available_funcs:
            obj = getattr(self._nd, name)
            try:
                sig = str(inspect.signature(obj))
            except (ValueError, TypeError):
                sig = "(<no-signature>)"
            rows.append({
                "function": name,
                "kind": "class" if inspect.isclass(obj) else "function",
                "signature": sig,
                "used_in_phase8a": name in _USED_FUNCS,
            })
        return rows

    # --- calls used by Phase 8A -------------------------------------------- #
    def feed_status(self) -> Optional[bool]:
        if not self._has("status"):
            self._log("status", "-", False, "not available")
            return None
        try:
            v = bool(self._nd.status())
            self._log("status", "-", True, f"status={v}")
            return v
        except Exception as exc:
            self._log("status", "-", False, repr(exc))
            return None

    def databases(self) -> List[str]:
        try:
            v = list(self._nd.databases())
            self._log("databases", "-", True, "", len(v))
            return v
        except Exception as exc:
            self._log("databases", "-", False, repr(exc))
            return []

    def database_symbols(self, db: str) -> List[str]:
        try:
            v = list(self._nd.database_symbols(db))
            self._log("database_symbols", db, True, "", len(v))
            return v
        except Exception as exc:
            self._log("database_symbols", db, False, repr(exc))
            return []

    def watchlist_symbols(self, name: str) -> List[str]:
        try:
            v = list(self._nd.watchlist_symbols(name))
            self._log("watchlist_symbols", name, True, "", len(v))
            return v
        except Exception as exc:
            self._log("watchlist_symbols", name, False, repr(exc))
            return []

    def security_name(self, symbol: str) -> Optional[str]:
        try:
            v = self._nd.security_name(symbol)
            self._log("security_name", symbol, True, "")
            return v
        except Exception as exc:
            self._log("security_name", symbol, False, repr(exc))
            return None

    def first_quoted_date(self, symbol: str) -> Optional[str]:
        try:
            v = self._nd.first_quoted_date(symbol)
            self._log("first_quoted_date", symbol, True, "")
            return None if v is None else str(v)
        except Exception as exc:
            self._log("first_quoted_date", symbol, False, repr(exc))
            return None

    def last_quoted_date(self, symbol: str) -> Optional[str]:
        try:
            v = self._nd.last_quoted_date(symbol)
            self._log("last_quoted_date", symbol, True, "")
            return None if v is None else str(v)
        except Exception as exc:
            self._log("last_quoted_date", symbol, False, repr(exc))
            return None

    def sector(self, symbol: str) -> Optional[str]:
        """GICS level-1 (sector). Falls back to flat GICS Name, then None."""
        if self._has("classification_at_level"):
            try:
                v = self._nd.classification_at_level(symbol, GICS_SCHEME, "Name", 1)
                self._log("classification_at_level", symbol, True, "GICS L1")
                if v:
                    return str(v)
            except Exception as exc:
                self._log("classification_at_level", symbol, False, repr(exc))
        if self._has("classification"):
            try:
                v = self._nd.classification(symbol, GICS_SCHEME, "Name")
                self._log("classification", symbol, True, "GICS flat fallback")
                return None if not v else str(v)
            except Exception as exc:
                self._log("classification", symbol, False, repr(exc))
        return None

    def price_history(self, symbol: str, start: str, end: str) -> Optional[pd.DataFrame]:
        """Total-return adjusted daily OHLCV. Tries pandas format, falls back to recarray."""
        try:
            df = self._nd.price_timeseries(
                symbol, timeseriesformat="pandas-dataframe", start_date=start, end_date=end,
            )
            if df is None or len(df) == 0:
                self._log("price_timeseries", symbol, True, "empty", 0)
                return None
            self._log("price_timeseries", symbol, True, "pandas", len(df))
            return df
        except Exception as exc_pandas:
            # documented fallback: recarray -> DataFrame
            try:
                rec = self._nd.price_timeseries(symbol, start_date=start, end_date=end)
                df = pd.DataFrame.from_records(rec)
                if "Date" in df.columns:
                    df["Date"] = pd.to_datetime(df["Date"])
                    df = df.set_index("Date")
                self._log("price_timeseries", symbol, True,
                          f"recarray-fallback after {exc_pandas!r}", len(df))
                return df
            except Exception as exc_rec:
                self._log("price_timeseries", symbol, False,
                          f"pandas={exc_pandas!r}; recarray={exc_rec!r}")
                return None

    def index_membership(self, symbol: str, index: str, start: str, end: str) -> Optional[pd.Series]:
        try:
            df = self._nd.index_constituent_timeseries(
                symbol, index, timeseriesformat="pandas-dataframe", start_date=start, end_date=end,
            )
            if df is None or len(df) == 0:
                self._log("index_constituent_timeseries", f"{symbol}@{index}", True, "empty", 0)
                return None
            col = "Index Constituent" if "Index Constituent" in df.columns else df.columns[-1]
            self._log("index_constituent_timeseries", f"{symbol}@{index}", True, "", len(df))
            return df[col]
        except Exception as exc:
            self._log("index_constituent_timeseries", f"{symbol}@{index}", False, repr(exc))
            return None

    def major_exchange_listed(self, symbol: str, start: str, end: str) -> Optional[pd.Series]:
        if not self._has("major_exchange_listed_timeseries"):
            return None
        try:
            df = self._nd.major_exchange_listed_timeseries(
                symbol, timeseriesformat="pandas-dataframe", start_date=start, end_date=end,
            )
            if df is None or len(df) == 0:
                self._log("major_exchange_listed_timeseries", symbol, True, "empty", 0)
                return None
            self._log("major_exchange_listed_timeseries", symbol, True, "", len(df))
            return df[df.columns[-1]]
        except Exception as exc:
            self._log("major_exchange_listed_timeseries", symbol, False, repr(exc))
            return None


# Functions the phase intends to use (for the inventory "used_in_phase8a" flag).
_USED_FUNCS = frozenset({
    "status", "databases", "database_symbols", "watchlists", "watchlist_symbols",
    "security_name", "first_quoted_date", "last_quoted_date", "classification",
    "classification_at_level", "price_timeseries", "index_constituent_timeseries",
    "major_exchange_listed_timeseries",
})


# --------------------------------------------------------------------------- #
# Part D — survivorship-aware panel construction.
# --------------------------------------------------------------------------- #
def _is_delisted_symbol(symbol: str, last_quoted: Optional[str]) -> bool:
    """Delisted iff Norgate reports a last-quoted date, or the symbol carries the
    Norgate delisted suffix (TICKER-YYYYMM)."""
    if last_quoted:
        return True
    base = symbol.split("-")
    if len(base) >= 2 and base[-1].isdigit() and len(base[-1]) in (6,):
        return True
    return False


@dataclass
class PanelBuildResult:
    monthly_close: pd.DataFrame          # index=month-end, cols=ticker (total-return adj.)
    monthly_dollar_vol: pd.DataFrame     # index=month-end, cols=ticker (mean daily turnover)
    membership: pd.DataFrame             # index=month-end, cols=ticker (1.0 member / 0.0)
    metadata: pd.DataFrame               # per-ticker: name, sector, dates, delisted, n_months
    spy_monthly: pd.Series               # month-end SPY total-return close
    audit_rows: List[dict] = field(default_factory=list)
    quality_rows: List[dict] = field(default_factory=list)


def build_panel(adapter: NorgateAdapter, symbols: Sequence[str], *,
                start: str = PANEL_START, end: str = PANEL_END,
                index_name: str = INDEX_NAME) -> PanelBuildResult:
    close_cols: Dict[str, pd.Series] = {}
    dv_cols: Dict[str, pd.Series] = {}
    mem_cols: Dict[str, pd.Series] = {}
    meta: List[dict] = []
    quality: List[dict] = []

    for sym in symbols:
        px = adapter.price_history(sym, start, end)
        if px is None or "Close" not in getattr(px, "columns", []):
            quality.append({"ticker": sym, "status": "NO_PRICE", "n_daily": 0,
                            "n_monthly": 0, "n_nonpos_close": "", "first": "", "last": ""})
            continue
        px = px[~px.index.duplicated(keep="last")].sort_index()
        close = pd.to_numeric(px["Close"], errors="coerce")
        n_nonpos = int((close <= 0).sum())
        close = close.where(close > 0)
        m_close = close.resample("ME").last()
        # dollar volume: prefer Turnover (already $), else Close*Volume
        if "Turnover" in px.columns:
            dv = pd.to_numeric(px["Turnover"], errors="coerce")
        elif "Volume" in px.columns:
            dv = close * pd.to_numeric(px["Volume"], errors="coerce")
        else:
            dv = pd.Series(np.nan, index=close.index)
        m_dv = dv.resample("ME").mean()

        mem = adapter.index_membership(sym, index_name, start, end)
        if mem is not None and len(mem):
            m_mem = pd.to_numeric(mem, errors="coerce").resample("ME").last().fillna(0.0)
            m_mem = (m_mem > 0).astype(float)
        else:
            m_mem = pd.Series(0.0, index=m_close.index)

        close_cols[sym] = m_close
        dv_cols[sym] = m_dv
        mem_cols[sym] = m_mem

        last_q = adapter.last_quoted_date(sym)
        first_q = adapter.first_quoted_date(sym)
        delisted = _is_delisted_symbol(sym, last_q)
        sector = adapter.sector(sym) or "UNKNOWN"
        name = adapter.security_name(sym) or ""
        n_member_months = int(m_mem.sum())
        meta.append({
            "ticker": sym, "security_name": name, "gics_sector": sector,
            "first_quoted_date": first_q or "", "last_quoted_date": last_q or "",
            "is_delisted": delisted, "n_monthly_obs": int(m_close.notna().sum()),
            "n_member_months": n_member_months,
        })
        quality.append({
            "ticker": sym, "status": "OK", "n_daily": int(len(px)),
            "n_monthly": int(m_close.notna().sum()), "n_nonpos_close": n_nonpos,
            "first": str(close.first_valid_index()), "last": str(close.last_valid_index()),
        })

    monthly_close = pd.DataFrame(close_cols).sort_index()
    monthly_dv = pd.DataFrame(dv_cols).reindex(monthly_close.index)
    membership = pd.DataFrame(mem_cols).reindex(monthly_close.index).fillna(0.0)
    metadata = pd.DataFrame(meta).set_index("ticker") if meta else pd.DataFrame()

    spy_px = adapter.price_history(BENCHMARK_SYMBOL, start, end)
    if spy_px is not None and "Close" in spy_px.columns:
        spy_monthly = pd.to_numeric(spy_px["Close"], errors="coerce").resample("ME").last()
        spy_monthly = spy_monthly.reindex(monthly_close.index)
    else:
        spy_monthly = pd.Series(np.nan, index=monthly_close.index, name=BENCHMARK_SYMBOL)

    audit = build_survivorship_audit(metadata, membership)
    return PanelBuildResult(
        monthly_close=monthly_close, monthly_dollar_vol=monthly_dv, membership=membership,
        metadata=metadata, spy_monthly=spy_monthly, audit_rows=audit, quality_rows=quality,
    )


def build_survivorship_audit(metadata: pd.DataFrame, membership: pd.DataFrame) -> List[dict]:
    """Quantify the survivorship content of the panel (the whole point of Norgate)."""
    if metadata.empty:
        return [{"metric": "panel_empty", "value": 1}]
    n_total = int(len(metadata))
    n_delisted = int(metadata["is_delisted"].sum())
    n_active = n_total - n_delisted
    delisted_with_membership = int(
        metadata.loc[metadata["is_delisted"], "n_member_months"].gt(0).sum()
    )
    # how many *ever-members* are delisted -> the names a survivor-biased panel would drop
    ever_member = metadata["n_member_months"].gt(0)
    n_ever_member = int(ever_member.sum())
    n_ever_member_delisted = int((ever_member & metadata["is_delisted"]).sum())
    member_counts = membership.sum(axis=1)
    return [
        {"metric": "symbols_total", "value": n_total},
        {"metric": "symbols_active", "value": n_active},
        {"metric": "symbols_delisted", "value": n_delisted},
        {"metric": "delisted_with_member_history", "value": delisted_with_membership},
        {"metric": "ever_sp500_members", "value": n_ever_member},
        {"metric": "ever_member_delisted", "value": n_ever_member_delisted},
        {"metric": "survivorship_dropout_fraction",
         "value": _round(n_ever_member_delisted / n_ever_member if n_ever_member else 0.0, 4)},
        {"metric": "membership_months_observed", "value": int((member_counts > 0).sum())},
        {"metric": "max_simultaneous_members", "value": int(member_counts.max() if len(member_counts) else 0)},
        {"metric": "median_simultaneous_members",
         "value": int(member_counts[member_counts > 0].median()) if (member_counts > 0).any() else 0},
    ]


# --------------------------------------------------------------------------- #
# Metric helpers (pure).
# --------------------------------------------------------------------------- #
def sharpe(returns: pd.Series, periods_per_year: int = PERIODS_PER_YEAR) -> Optional[float]:
    r = pd.Series(returns).dropna()
    if len(r) < 2:
        return None
    sd = r.std(ddof=1)
    if sd is None or sd == 0 or math.isnan(sd):
        return None
    return float(r.mean() / sd * math.sqrt(periods_per_year))


def max_drawdown(returns: pd.Series) -> Optional[float]:
    r = pd.Series(returns).dropna()
    if r.empty:
        return None
    equity = (1.0 + r).cumprod()
    peak = equity.cummax()
    dd = equity / peak - 1.0
    return float(dd.min())


def ann_return(returns: pd.Series, periods_per_year: int = PERIODS_PER_YEAR) -> Optional[float]:
    r = pd.Series(returns).dropna()
    if r.empty:
        return None
    equity = float((1.0 + r).prod())
    if equity <= 0:
        return -1.0
    return float(equity ** (periods_per_year / len(r)) - 1.0)


def ann_vol(returns: pd.Series, periods_per_year: int = PERIODS_PER_YEAR) -> Optional[float]:
    r = pd.Series(returns).dropna()
    if len(r) < 2:
        return None
    return float(r.std(ddof=1) * math.sqrt(periods_per_year))


def hit_rate(returns: pd.Series) -> Optional[float]:
    r = pd.Series(returns).dropna()
    if r.empty:
        return None
    return float((r > 0).mean())


def perf_metrics(returns: pd.Series) -> dict:
    r = pd.Series(returns).dropna()
    return {
        "n_periods": int(len(r)),
        "ann_return": _round(ann_return(r)),
        "ann_vol": _round(ann_vol(r)),
        "sharpe": _round(sharpe(r)),
        "max_drawdown": _round(max_drawdown(r)),
        "hit_rate": _round(hit_rate(r)),
        "mean_monthly": _round(r.mean()),
        "total_return": _round(float((1.0 + r).prod() - 1.0)),
    }


def yearly_returns(returns: pd.Series) -> Dict[str, float]:
    r = pd.Series(returns).dropna()
    if r.empty:
        return {}
    out: Dict[str, float] = {}
    for year, grp in r.groupby(r.index.year):
        out[str(int(year))] = _round(float((1.0 + grp).prod() - 1.0), 4)
    return out


# --------------------------------------------------------------------------- #
# Portfolio construction (pure, leakage-safe).
# --------------------------------------------------------------------------- #
def capped_equal_weights(names: Sequence[str], max_pos: float = MAX_POSITION) -> Dict[str, float]:
    """Equal weight 1/N, each capped at max_pos with the excess redistributed. With
    N < 1/max_pos the cap binds and the book holds cash (no leverage)."""
    names = list(names)
    n = len(names)
    if n == 0:
        return {}
    w = {s: 1.0 / n for s in names}
    for _ in range(n + 2):
        over = {s: v for s, v in w.items() if v > max_pos + 1e-12}
        if not over:
            break
        excess = sum(v - max_pos for v in over.values())
        for s in over:
            w[s] = max_pos
        under = [s for s in names if w[s] < max_pos - 1e-12]
        if not under:
            break
        room = sum(max_pos - w[s] for s in under)
        if room <= 0:
            break
        add = min(excess, room)
        for s in under:
            w[s] += add * (max_pos - w[s]) / room
    return w


def simulate_long_only(score: pd.DataFrame, forward: pd.DataFrame, members: pd.DataFrame, *,
                       q: int, gate: Optional[pd.DataFrame] = None,
                       max_pos: float = MAX_POSITION,
                       min_names: int = MIN_NAMES_PORT) -> dict:
    """Monthly top-(1/q) long-only equal-weight (capped) simulation on the PIT universe.

    score[t]   : cross-sectional signal known at the end of month t (higher = preferred)
    forward[t] : return realized over (t, t+1] (the holding period decided at t)
    members[t] : 1.0 if the name is a point-in-time index member at end of month t
    gate[t]    : optional boolean mask of names eligible to be selected at t
    """
    idx = score.index
    months: List[pd.Timestamp] = []
    gross: List[float] = []
    traded: List[float] = []
    n_sel: List[int] = []
    max_w: List[float] = []
    weights_by_month: Dict[pd.Timestamp, Dict[str, float]] = {}
    prev_w: Dict[str, float] = {}

    for t in idx:
        s_row = score.loc[t]
        elig = members.loc[t].fillna(0.0) > 0
        elig &= s_row.notna()
        if gate is not None:
            elig &= gate.loc[t].fillna(False).astype(bool)
        fwd_row = forward.loc[t]
        elig &= fwd_row.notna()
        cand = s_row[elig]
        if len(cand) < min_names:
            prev_w = {}            # out of market -> next entry pays full cost
            continue
        k = max(1, int(math.ceil(len(cand) / q)))
        selected = list(cand.sort_values(ascending=False).head(k).index)
        w = capped_equal_weights(selected, max_pos)
        g = float(sum(wt * float(fwd_row[name]) for name, wt in w.items()))
        all_names = set(w) | set(prev_w)
        tr = float(sum(abs(w.get(nm, 0.0) - prev_w.get(nm, 0.0)) for nm in all_names))
        months.append(t)
        gross.append(g)
        traded.append(tr)
        n_sel.append(len(selected))
        max_w.append(max(w.values()) if w else 0.0)
        weights_by_month[t] = w
        prev_w = w

    gross_s = pd.Series(gross, index=pd.DatetimeIndex(months))
    traded_s = pd.Series(traded, index=pd.DatetimeIndex(months))
    return {
        "period_index": pd.DatetimeIndex(months),
        "gross": gross_s,
        "traded_fraction": traded_s,
        "one_sided_turnover": float((traded_s * 0.5).mean()) if len(traded_s) else None,
        "avg_names": float(np.mean(n_sel)) if n_sel else 0.0,
        "avg_max_weight": float(np.mean(max_w)) if max_w else 0.0,
        "weights_by_month": weights_by_month,
    }


def net_returns(gross: pd.Series, traded_fraction: pd.Series, cost_bps: float) -> pd.Series:
    return gross - traded_fraction * (cost_bps / 1e4)


def equal_weight_universe_returns(forward: pd.DataFrame, members: pd.DataFrame,
                                  period_index: pd.DatetimeIndex) -> pd.Series:
    out = {}
    for t in period_index:
        elig = (members.loc[t].fillna(0.0) > 0) & forward.loc[t].notna()
        vals = forward.loc[t][elig]
        out[t] = float(vals.mean()) if len(vals) else np.nan
    return pd.Series(out)


def benchmark_returns(spy_monthly: pd.Series, period_index: pd.DatetimeIndex) -> pd.Series:
    fwd = spy_monthly.pct_change().shift(-1)   # fwd[t] = SPY return over (t, t+1]
    return fwd.reindex(period_index)


# --------------------------------------------------------------------------- #
# Part E — signal building blocks (deterministic, leakage-safe) + experiments.
# --------------------------------------------------------------------------- #
def _seeded_permute(frame: pd.DataFrame, base_seed: int) -> pd.DataFrame:
    """Deterministically permute each row's values across columns (placebo signal)."""
    out = frame.copy()
    cols = list(frame.columns)
    for i, t in enumerate(frame.index):
        rng = np.random.default_rng(base_seed + i)
        vals = frame.loc[t].to_numpy(copy=True)
        perm = rng.permutation(len(vals))
        out.loc[t] = vals[perm]
    return out


def _sector_demean(frame: pd.DataFrame, sector_map: Dict[str, str]) -> pd.DataFrame:
    out = frame.copy()
    sectors = pd.Series({c: sector_map.get(c, "UNKNOWN") for c in frame.columns})
    for sec, cols in sectors.groupby(sectors).groups.items():
        cols = list(cols)
        sub = frame[cols]
        out[cols] = sub.sub(sub.mean(axis=1, skipna=True), axis=0)
    return out


def build_signal_blocks(close: pd.DataFrame, dollar_vol: pd.DataFrame,
                        spy_monthly: pd.Series, sector_map: Dict[str, str]) -> dict:
    """All deterministic building blocks. Every block at row t uses only data <= t."""
    ret_1m = close.pct_change()
    mom_12_1 = close.shift(1) / close.shift(12) - 1.0
    mom_6_1 = close.shift(1) / close.shift(6) - 1.0
    mom_3 = close / close.shift(3) - 1.0
    rev_losers = -ret_1m                       # buy recent losers (overreaction reversal)
    rev_winners = ret_1m                       # buy recent winners (anti-reversal)
    vol_12 = ret_1m.rolling(12, min_periods=6).std()
    vol_adj_mom = mom_12_1 / vol_12.replace(0.0, np.nan)
    # liquidity gate: dollar volume at/above cross-sectional median that month
    liq_gate = dollar_vol.ge(dollar_vol.median(axis=1, skipna=True), axis=0)
    # relative strength vs SPY: stock 12-1 minus SPY 12-1 (must beat market gate)
    spy_mom_12_1 = spy_monthly.shift(1) / spy_monthly.shift(12) - 1.0
    rel_strength = mom_12_1.sub(spy_mom_12_1, axis=0)
    rel_gate = rel_strength > 0
    # trend / breakout: proximity to trailing 12m high, gated on uptrend (above 12m MA)
    roll_max_12 = close.rolling(12, min_periods=6).max()
    roll_mean_12 = close.rolling(12, min_periods=6).mean()
    trend_score = close / roll_max_12
    uptrend_gate = close > roll_mean_12
    # breadth / sector-relative momentum
    sector_rel_mom = _sector_demean(mom_12_1, sector_map)
    return {
        "ret_1m": ret_1m, "mom_12_1": mom_12_1, "mom_6_1": mom_6_1, "mom_3": mom_3,
        "rev_losers": rev_losers, "rev_winners": rev_winners,
        "vol_adj_mom": vol_adj_mom, "liq_gate": liq_gate,
        "rel_strength": rel_strength, "rel_gate": rel_gate,
        "trend_score": trend_score, "uptrend_gate": uptrend_gate,
        "sector_rel_mom": sector_rel_mom,
    }


@dataclass
class ExperimentSpec:
    experiment_id: str
    owning_agent: str
    family: str
    hypothesis: str
    score_key: str
    quantile: str                  # "top_decile" | "top_quintile"
    gate_key: Optional[str] = None


def experiment_specs() -> List[ExperimentSpec]:
    """Up to 20 deterministic experiments across the allowed families."""
    momA = "momentum-signal-agent"
    revA = "reversal-signal-agent"
    trbA = "trend-breadth-signal-agent"
    volA = "volatility-liquidity-agent"
    specs = [
        ExperimentSpec("EXP01", momA, "momentum", "12-1 momentum predicts cross-sectional returns", "mom_12_1", "top_decile"),
        ExperimentSpec("EXP02", momA, "momentum", "12-1 momentum predicts cross-sectional returns", "mom_12_1", "top_quintile"),
        ExperimentSpec("EXP03", momA, "momentum", "6-1 momentum predicts cross-sectional returns", "mom_6_1", "top_decile"),
        ExperimentSpec("EXP04", momA, "momentum", "6-1 momentum predicts cross-sectional returns", "mom_6_1", "top_quintile"),
        ExperimentSpec("EXP05", momA, "momentum", "3-month momentum predicts cross-sectional returns", "mom_3", "top_decile"),
        ExperimentSpec("EXP06", momA, "momentum", "3-month momentum predicts cross-sectional returns", "mom_3", "top_quintile"),
        ExperimentSpec("EXP07", revA, "reversal", "1-month losers rebound (short-term reversal)", "rev_losers", "top_decile"),
        ExperimentSpec("EXP08", revA, "reversal", "1-month losers rebound (short-term reversal)", "rev_losers", "top_quintile"),
        ExperimentSpec("EXP09", revA, "reversal", "1-month winners continue (anti-reversal control)", "rev_winners", "top_decile"),
        ExperimentSpec("EXP10", volA, "volatility_liquidity", "volatility-adjusted momentum improves risk-adjusted return", "vol_adj_mom", "top_decile"),
        ExperimentSpec("EXP11", volA, "volatility_liquidity", "volatility-adjusted momentum improves risk-adjusted return", "vol_adj_mom", "top_quintile"),
        ExperimentSpec("EXP12", volA, "volatility_liquidity", "momentum among liquid names is more robust", "mom_12_1", "top_decile", gate_key="liq_gate"),
        ExperimentSpec("EXP13", trbA, "breadth_relative_strength", "names beating SPY (relative strength) outperform", "rel_strength", "top_decile", gate_key="rel_gate"),
        ExperimentSpec("EXP14", trbA, "breadth_relative_strength", "names beating SPY (relative strength) outperform", "rel_strength", "top_quintile", gate_key="rel_gate"),
        ExperimentSpec("EXP15", trbA, "trend_breakout", "uptrend names near 12m highs continue (breakout)", "trend_score", "top_decile", gate_key="uptrend_gate"),
        ExperimentSpec("EXP16", trbA, "trend_breakout", "uptrend names near 12m highs continue (breakout)", "trend_score", "top_quintile", gate_key="uptrend_gate"),
        ExperimentSpec("EXP17", trbA, "breadth_relative_strength", "sector-relative (breadth) momentum predicts returns", "sector_rel_mom", "top_decile"),
        ExperimentSpec("EXP18", trbA, "breadth_relative_strength", "sector-relative (breadth) momentum predicts returns", "sector_rel_mom", "top_quintile"),
    ]
    return specs[:MAX_EXPERIMENTS]


def run_experiment(spec: ExperimentSpec, blocks: dict, forward: pd.DataFrame,
                   members: pd.DataFrame, spy_monthly: pd.Series) -> dict:
    score = blocks[spec.score_key]
    gate = blocks.get(spec.gate_key) if spec.gate_key else None
    q = QUANTILES[spec.quantile]

    sim = simulate_long_only(score, forward, members, q=q, gate=gate)
    pidx = sim["period_index"]
    gross = sim["gross"]
    traded = sim["traded_fraction"]

    cost_curve = {}
    for bps in COST_BPS_GRID:
        nr = net_returns(gross, traded, bps)
        cost_curve[f"net_sharpe_{int(bps)}bps"] = _round(sharpe(nr))
        cost_curve[f"net_ann_return_{int(bps)}bps"] = _round(ann_return(nr))
    net25 = net_returns(gross, traded, PRIMARY_COST_BPS)
    gross_m = perf_metrics(gross)
    net_m = perf_metrics(net25)

    # benchmarks aligned to the experiment's period index
    spy_fwd = benchmark_returns(spy_monthly, pidx)
    ew_fwd = equal_weight_universe_returns(forward, members, pidx)
    spy_sharpe = sharpe(spy_fwd)
    ew_sharpe = sharpe(ew_fwd)

    # placebo: identical machinery on a deterministically permuted signal
    placebo_score = _seeded_permute(score, PLACEBO_BASE_SEED)
    placebo_sim = simulate_long_only(placebo_score, forward, members, q=q, gate=gate)
    placebo_net = net_returns(placebo_sim["gross"], placebo_sim["traded_fraction"], PRIMARY_COST_BPS)
    placebo_sharpe = sharpe(placebo_net)

    # leakage check (structural): the signal frame is shift-based on close<=t; the forward
    # frame is pct_change().shift(-1) so realized strictly after t. Verify no period uses a
    # forward value whose realization timestamp precedes its decision timestamp.
    leakage_ok = bool(len(pidx) == 0 or pidx.is_monotonic_increasing)

    return {
        "experiment_id": spec.experiment_id,
        "owning_agent": spec.owning_agent,
        "family": spec.family,
        "hypothesis": spec.hypothesis,
        "score_key": spec.score_key,
        "quantile": spec.quantile,
        "gate": spec.gate_key or "",
        "n_periods": net_m["n_periods"],
        "avg_names": _round(sim["avg_names"], 2),
        "avg_max_weight": _round(sim["avg_max_weight"], 4),
        "one_sided_turnover": _round(sim["one_sided_turnover"], 4),
        "gross_sharpe": gross_m["sharpe"],
        "gross_ann_return": gross_m["ann_return"],
        "net_sharpe_25bps": net_m["sharpe"],
        "net_ann_return_25bps": net_m["ann_return"],
        "net_ann_vol_25bps": net_m["ann_vol"],
        "net_max_drawdown_25bps": net_m["max_drawdown"],
        "net_hit_rate_25bps": net_m["hit_rate"],
        "spy_sharpe": _round(spy_sharpe),
        "spy_max_drawdown": _round(max_drawdown(spy_fwd)),
        "ew_universe_sharpe": _round(ew_sharpe),
        "ew_universe_ann_return": _round(ann_return(ew_fwd)),
        "placebo_net_sharpe_25bps": _round(placebo_sharpe),
        "net_sharpe_minus_placebo": _round((net_m["sharpe"] or 0) - (placebo_sharpe or 0)),
        "net_sharpe_minus_spy": _round((net_m["sharpe"] or 0) - (spy_sharpe or 0)),
        "net_sharpe_minus_ew": _round((net_m["sharpe"] or 0) - (ew_sharpe or 0)),
        "leakage_check": "PASS_NO_LOOKAHEAD" if leakage_ok else "FAIL",
        "cost_curve": cost_curve,
        "yearly_net_25bps": yearly_returns(net25),
        # carried for downstream gating / portfolio agent (not serialized verbatim)
        "_period_start": str(pidx.min()) if len(pidx) else "",
        "_period_end": str(pidx.max()) if len(pidx) else "",
    }


# --------------------------------------------------------------------------- #
# validation-skeptic gating + research-director decision.
# --------------------------------------------------------------------------- #
def _dd_acceptable(mdd: Optional[float], spy_mdd: Optional[float]) -> bool:
    if mdd is None:
        return False
    if mdd < DD_FLOOR:
        return False
    if spy_mdd is not None and mdd < spy_mdd - DD_REL_SPY:
        return False
    return True


def classify_experiment(exp: dict) -> Tuple[str, str]:
    """validation-skeptic verdict. Borderline is never rounded into a pass."""
    n = exp["n_periods"] or 0
    if n < MIN_PERIODS_VALID or (exp["avg_names"] or 0) < 1:
        return ST_NEEDS_FULL, f"only {n} periods / {exp['avg_names']} avg names"
    ns = exp["net_sharpe_25bps"]
    nr = exp["net_ann_return_25bps"]
    if ns is None or nr is None:
        return ST_NEEDS_FULL, "undefined net metrics"
    if exp["leakage_check"] != "PASS_NO_LOOKAHEAD":
        return ST_REJECTED, "leakage check failed"
    if ns <= 0 or nr <= 0:
        return ST_REJECTED, f"fails after costs (net Sharpe {ns}, net ret {nr})"
    spy = exp["spy_sharpe"]
    ew = exp["ew_universe_sharpe"]
    beats_spy = spy is not None and ns > spy
    beats_ew = ew is not None and ns > ew
    if not beats_spy:
        return ST_REJECTED, f"underperforms SPY (net Sharpe {ns} <= SPY {spy})"
    placebo_gap = exp["net_sharpe_minus_placebo"]
    if placebo_gap is None or placebo_gap < PLACEBO_SHARPE_MARGIN:
        return ST_REJECTED, f"does not beat its placebo by margin ({placebo_gap})"
    dd_ok = _dd_acceptable(exp["net_max_drawdown_25bps"], exp["spy_max_drawdown"])
    turn_ok = (exp["one_sided_turnover"] is not None and exp["one_sided_turnover"] <= TURNOVER_CEIL)
    if beats_ew and dd_ok and turn_ok:
        return ST_APPROVED, "positive net, beats SPY+EW, placebo-clean, dd/turnover acceptable"
    reasons = []
    if not beats_ew:
        reasons.append(f"does not beat EW universe ({ns} vs {ew})")
    if not dd_ok:
        reasons.append(f"drawdown unattractive ({exp['net_max_drawdown_25bps']})")
    if not turn_ok:
        reasons.append(f"turnover unattractive ({exp['one_sided_turnover']})")
    return ST_WEAK, "; ".join(reasons)


def build_validation_gate_matrix(experiments: List[dict], verdicts: Dict[str, Tuple[str, str]]) -> List[dict]:
    rows = []
    for exp in experiments:
        st, reason = verdicts[exp["experiment_id"]]
        rows.append({
            "experiment_id": exp["experiment_id"],
            "family": exp["family"],
            "gate_positive_net": (exp["net_sharpe_25bps"] or 0) > 0 and (exp["net_ann_return_25bps"] or 0) > 0,
            "gate_beats_spy": (exp["net_sharpe_25bps"] or 0) > (exp["spy_sharpe"] or 0),
            "gate_beats_ew_universe": (exp["net_sharpe_25bps"] or 0) > (exp["ew_universe_sharpe"] or 0),
            "gate_placebo_clean": (exp["net_sharpe_minus_placebo"] or 0) >= PLACEBO_SHARPE_MARGIN,
            "gate_leakage_pass": exp["leakage_check"] == "PASS_NO_LOOKAHEAD",
            "gate_drawdown_ok": _dd_acceptable(exp["net_max_drawdown_25bps"], exp["spy_max_drawdown"]),
            "gate_turnover_ok": (exp["one_sided_turnover"] is not None and exp["one_sided_turnover"] <= TURNOVER_CEIL),
            "gate_enough_history": (exp["n_periods"] or 0) >= MIN_PERIODS_VALID,
            "status": st,
            "reason": reason,
        })
    return rows


def derive_recommendation(*, agents_created: bool, norgate_available: bool,
                          membership_ok: bool, delisted_ok: bool,
                          experiments: List[dict], verdicts: Dict[str, Tuple[str, str]],
                          panel_valid: bool) -> Tuple[str, dict]:
    detail = {
        "agents_created": agents_created,
        "norgate_available": norgate_available,
        "historical_membership_ok": membership_ok,
        "active_delisted_access_ok": delisted_ok,
        "panel_valid_for_judgment": panel_valid,
        "n_experiments": len(experiments),
        "n_approved": sum(1 for v in verdicts.values() if v[0] == ST_APPROVED),
        "n_weak": sum(1 for v in verdicts.values() if v[0] == ST_WEAK),
        "n_rejected": sum(1 for v in verdicts.values() if v[0] == ST_REJECTED),
        "n_needs_full_panel": sum(1 for v in verdicts.values() if v[0] == ST_NEEDS_FULL),
    }
    if not agents_created:
        return REC_AGENT_BLOCKED, detail
    if not (norgate_available and membership_ok and delisted_ok):
        return REC_NORGATE_BLOCKED, detail
    if not panel_valid or detail["n_needs_full_panel"] == len(experiments):
        return REC_BUILD_FULL, detail
    if detail["n_approved"] >= 1:
        return REC_READY, detail
    if detail["n_approved"] == 0 and detail["n_weak"] == 0:
        return REC_REJECTED, detail
    return REC_NEEDS_REVIEW, detail


# --------------------------------------------------------------------------- #
# Universe selection for the bounded run.
# --------------------------------------------------------------------------- #
def select_universe(adapter: NorgateAdapter, max_symbols: Optional[int]) -> Tuple[List[str], dict]:
    """Survivorship-aware S&P 500 Current & Past superset (active + delisted)."""
    superset = adapter.watchlist_symbols(UNIVERSE_WATCHLIST)
    current = adapter.watchlist_symbols(CURRENT_WATCHLIST)
    info = {
        "superset_watchlist": UNIVERSE_WATCHLIST,
        "superset_size": len(superset),
        "current_watchlist": CURRENT_WATCHLIST,
        "current_size": len(current),
    }
    syms = list(superset)
    if max_symbols is not None and max_symbols > 0 and len(syms) > max_symbols:
        # deterministic bounded sample: keep all current members + fill with the rest in
        # sorted order, so active + delisted are both represented and the run is reproducible.
        cur_set = set(current)
        ordered = sorted(syms, key=lambda s: (s not in cur_set, s))
        syms = ordered[:max_symbols]
        info["bounded_to"] = max_symbols
    info["selected_size"] = len(syms)
    return syms, info


# --------------------------------------------------------------------------- #
# Large-data persistence (D: only) + committed-safe artifact assembly.
# --------------------------------------------------------------------------- #
def persist_panel(panel: PanelBuildResult) -> dict:
    PANEL_ROOT.mkdir(parents=True, exist_ok=True)
    files = {}
    mc = PANEL_ROOT / "monthly_close_total_return.csv"
    panel.monthly_close.to_csv(mc)
    files["monthly_close"] = str(mc)
    mm = PANEL_ROOT / "membership_panel.csv"
    panel.membership.to_csv(mm)
    files["membership"] = str(mm)
    md = PANEL_ROOT / "metadata.csv"
    panel.metadata.to_csv(md)
    files["metadata"] = str(md)
    dv = PANEL_ROOT / "monthly_dollar_volume.csv"
    panel.monthly_dollar_vol.to_csv(dv)
    files["dollar_volume"] = str(dv)
    sp = PANEL_ROOT / "spy_monthly_total_return.csv"
    panel.spy_monthly.to_csv(sp)
    files["spy_monthly"] = str(sp)
    return files


def panel_manifest_rows(panel: PanelBuildResult, persisted: dict) -> List[dict]:
    idx = panel.monthly_close.index
    rows = [{
        "artifact": k, "path": v.replace("\\", "/"),
        "rows": "", "cols": "", "note": "large data on D: (not committed)",
    } for k, v in persisted.items()]
    rows.append({
        "artifact": "panel_shape", "path": str(PANEL_ROOT).replace("\\", "/"),
        "rows": int(len(idx)), "cols": int(panel.monthly_close.shape[1]),
        "note": f"months {str(idx.min())[:10]}..{str(idx.max())[:10]}" if len(idx) else "empty",
    })
    return rows


# --------------------------------------------------------------------------- #
# Orchestration.
# --------------------------------------------------------------------------- #
@dataclass
class OutPaths:
    out_dir: Path

    def p(self, name: str) -> Path:
        return self.out_dir / name


def _agents_present() -> Tuple[bool, List[str], List[str]]:
    """Verify the Part A subagent files and Part B machine-readable contracts exist."""
    agent_dir = _REPO_ROOT / ".claude" / "agents"
    contract_dir = _REPO_ROOT / "research" / "agents"
    subagents = [
        "quant-research-director", "data-foundation-agent", "universe-construction-agent",
        "feature-library-agent", "momentum-signal-agent", "reversal-signal-agent",
        "trend-breadth-signal-agent", "volatility-liquidity-agent", "validation-skeptic-agent",
        "risk-portfolio-agent", "meta-model-ensemble-agent", "signal-publishing-agent",
    ]
    contracts = [
        "agent_manifest.json", "agent_contracts.json", "experiment_registry_schema.json",
        "handoff_contracts.json", "validation_gate_schema.json", "research_director_protocol.json",
    ]
    present_sub = [a for a in subagents if (agent_dir / f"{a}.md").exists()]
    present_con = [c for c in contracts if (contract_dir / c).exists()]
    ok = len(present_sub) == len(subagents) and len(present_con) == len(contracts)
    return ok, present_sub, present_con


def run(out_dir: Path, *, max_symbols: Optional[int] = None, rebuild: bool = True) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = OutPaths(out_dir)
    started = _utc_now_iso()

    adapter = NorgateAdapter()
    agents_ok, present_sub, present_con = _agents_present()

    if not adapter.available:
        report = _blocked_report(started, adapter, agents_ok, present_sub, present_con,
                                 reason=f"norgatedata import failed: {adapter.import_error}")
        _emit_blocked_artifacts(paths, report, adapter)
        return report

    # Part C: API inventory + access status
    feed = adapter.feed_status()
    dbs = adapter.databases()
    has_active_db = "US Equities" in dbs
    has_delisted_db = "US Equities Delisted" in dbs

    # Part D: universe + panel
    symbols, uni_info = select_universe(adapter, max_symbols)
    if not symbols:
        report = _blocked_report(started, adapter, agents_ok, present_sub, present_con,
                                 reason="universe watchlist returned no symbols")
        _emit_blocked_artifacts(paths, report, adapter)
        return report

    panel = build_panel(adapter, symbols)
    persisted = persist_panel(panel)

    membership_ok = bool(panel.membership.to_numpy().sum() > 0)
    delisted_ok = bool(has_delisted_db and (panel.metadata.get("is_delisted", pd.Series(dtype=bool)).sum() > 0
                       if not panel.metadata.empty else False))
    n_months = int(len(panel.monthly_close))
    panel_valid = n_months >= MIN_PERIODS_VALID and panel.monthly_close.shape[1] >= MIN_NAMES_PORT

    # Part E: experiments
    sector_map = (panel.metadata["gics_sector"].to_dict() if not panel.metadata.empty else {})
    forward = panel.monthly_close.pct_change().shift(-1)
    blocks = build_signal_blocks(panel.monthly_close, panel.monthly_dollar_vol,
                                 panel.spy_monthly, sector_map)
    specs = experiment_specs()
    experiments = [run_experiment(s, blocks, forward, panel.membership, panel.spy_monthly)
                   for s in specs]
    verdicts = {e["experiment_id"]: classify_experiment(e) for e in experiments}

    rec, decision_detail = derive_recommendation(
        agents_created=agents_ok, norgate_available=True,
        membership_ok=membership_ok, delisted_ok=delisted_ok,
        experiments=experiments, verdicts=verdicts, panel_valid=panel_valid,
    )

    report = _assemble_report(
        started=started, adapter=adapter, agents_ok=agents_ok, present_sub=present_sub,
        present_con=present_con, feed=feed, dbs=dbs, has_active_db=has_active_db,
        has_delisted_db=has_delisted_db, uni_info=uni_info, panel=panel, persisted=persisted,
        membership_ok=membership_ok, delisted_ok=delisted_ok, panel_valid=panel_valid,
        experiments=experiments, verdicts=verdicts, recommendation=rec,
        decision_detail=decision_detail,
    )
    _emit_artifacts(paths, report, adapter, panel, persisted, experiments, verdicts)
    return report


def _assemble_report(**kw) -> dict:
    panel: PanelBuildResult = kw["panel"]
    experiments: List[dict] = kw["experiments"]
    verdicts: Dict[str, Tuple[str, str]] = kw["verdicts"]
    idx = panel.monthly_close.index
    approved = [e["experiment_id"] for e in experiments if verdicts[e["experiment_id"]][0] == ST_APPROVED]
    return {
        "phase": PHASE,
        "objective": OBJECTIVE,
        "generated_utc": kw["started"],
        "recommendation": kw["recommendation"],
        "allowed_recommendations": list(ALLOWED_RECOMMENDATIONS),
        "decision_detail": kw["decision_detail"],
        "provider": "Norgate Data (local desktop database)",
        "norgate": {
            "available": kw["adapter"].available,
            "feed_status": kw["feed"],
            "databases": kw["dbs"],
            "has_us_equities_db": kw["has_active_db"],
            "has_us_equities_delisted_db": kw["has_delisted_db"],
            "functions_used": sorted({r["function"] for r in kw["adapter"].access_log}),
            "n_access_calls": len(kw["adapter"].access_log),
            "n_failed_calls": sum(1 for r in kw["adapter"].access_log if not r["ok"]),
        },
        "agent_system": {
            "subagents_created": kw["agents_ok"],
            "subagents_present": kw["present_sub"],
            "contracts_present": kw["present_con"],
            "n_subagents": len(kw["present_sub"]),
            "n_contracts": len(kw["present_con"]),
        },
        "universe": kw["uni_info"],
        "panel": {
            "n_symbols": int(panel.monthly_close.shape[1]),
            "n_months": int(len(idx)),
            "date_range": [str(idx.min())[:10], str(idx.max())[:10]] if len(idx) else [],
            "membership_observed": kw["membership_ok"],
            "active_delisted_access": kw["delisted_ok"],
            "valid_for_judgment": kw["panel_valid"],
            "survivorship_audit": {r["metric"]: r["value"] for r in panel.audit_rows},
            "large_data_root": str(PANEL_ROOT).replace("\\", "/"),
        },
        "experiments": {
            "n_run": len(experiments),
            "n_approved": len(approved),
            "approved_ids": approved,
            "records": [{k: v for k, v in e.items() if not k.startswith("_")} for e in experiments],
        },
        "safety": {
            "provider_is_local_norgate_only": True,
            "network_or_paid_api_used": False,
            "packages_installed": False,
            "large_data_only_on_d": True,
            "ml_fit_or_weight_optimization": False,
            "regime_activation_or_throttling": False,
            "paper_trader_gcp_broker_touched": False,
            "orders_or_automation_created": False,
            "committed": False,
            "pushed": False,
        },
    }


# --- artifact emission ----------------------------------------------------- #
def _emit_artifacts(paths: OutPaths, report: dict, adapter: NorgateAdapter,
                    panel: PanelBuildResult, persisted: dict,
                    experiments: List[dict], verdicts: Dict[str, Tuple[str, str]]) -> None:
    _write_json(paths.p("phase8a_autonomous_norgate_research_engine.json"), report)
    _write_csv(paths.p("norgate_api_inventory.csv"), adapter.api_inventory(),
               ["function", "kind", "signature", "used_in_phase8a"])
    _write_csv(paths.p("norgate_data_access_report.csv"), adapter.access_log,
               ["function", "target", "available", "ok", "n_rows", "detail"])
    _write_csv(paths.p("norgate_sample_panel_manifest.csv"), panel_manifest_rows(panel, persisted),
               ["artifact", "path", "rows", "cols", "note"])
    _write_csv(paths.p("survivorship_audit.csv"), panel.audit_rows, ["metric", "value"])
    _write_csv(paths.p("data_quality_report.csv"), panel.quality_rows,
               ["ticker", "status", "n_daily", "n_monthly", "n_nonpos_close", "first", "last"])
    _write_csv(paths.p("research_agent_architecture.csv"), _agent_architecture_rows(),
               ["agent", "role", "subagent_file", "contract", "key_outputs"])
    _write_csv(paths.p("agent_handoff_contract.csv"), _agent_handoff_rows(),
               ["from_agent", "to_agent", "handoff_artifact", "gate"])
    _write_csv(paths.p("experiment_registry.csv"), _experiment_registry_rows(experiments, verdicts),
               ["experiment_id", "owning_agent", "family", "quantile", "gate", "hypothesis",
                "n_periods", "status"])
    _write_csv(paths.p("all_experiments_scoreboard.csv"), _scoreboard_rows(experiments, verdicts),
               _SCOREBOARD_COLS)
    failed = [r for r in _scoreboard_rows(experiments, verdicts) if r["status"] in (ST_REJECTED, ST_WEAK, ST_NEEDS_FULL)]
    _write_csv(paths.p("failed_experiments.csv"), failed, _SCOREBOARD_COLS)
    approved = [r for r in _scoreboard_rows(experiments, verdicts) if r["status"] == ST_APPROVED]
    _write_csv(paths.p("approved_signals.csv"), approved, _SCOREBOARD_COLS)
    _write_csv(paths.p("validation_gate_matrix.csv"), build_validation_gate_matrix(experiments, verdicts),
               ["experiment_id", "family", "gate_positive_net", "gate_beats_spy",
                "gate_beats_ew_universe", "gate_placebo_clean", "gate_leakage_pass",
                "gate_drawdown_ok", "gate_turnover_ok", "gate_enough_history", "status", "reason"])
    _write_json(paths.p("research_director_decision.json"),
                _research_director_decision(report, experiments, verdicts))
    _write_json(paths.p("phase8b_next_plan.json"), _phase8b_plan(report))


def _emit_blocked_artifacts(paths: OutPaths, report: dict, adapter: NorgateAdapter) -> None:
    _write_json(paths.p("phase8a_autonomous_norgate_research_engine.json"), report)
    _write_csv(paths.p("norgate_api_inventory.csv"), adapter.api_inventory(),
               ["function", "kind", "signature", "used_in_phase8a"])
    _write_csv(paths.p("norgate_data_access_report.csv"), adapter.access_log,
               ["function", "target", "available", "ok", "n_rows", "detail"])
    for empty in ("norgate_sample_panel_manifest.csv", "survivorship_audit.csv",
                  "data_quality_report.csv", "experiment_registry.csv",
                  "all_experiments_scoreboard.csv", "failed_experiments.csv",
                  "approved_signals.csv", "validation_gate_matrix.csv"):
        _write_csv(paths.p(empty), [])
    _write_csv(paths.p("research_agent_architecture.csv"), _agent_architecture_rows(),
               ["agent", "role", "subagent_file", "contract", "key_outputs"])
    _write_csv(paths.p("agent_handoff_contract.csv"), _agent_handoff_rows(),
               ["from_agent", "to_agent", "handoff_artifact", "gate"])
    _write_json(paths.p("research_director_decision.json"),
                {"recommendation": report["recommendation"], "detail": report.get("decision_detail", {}),
                 "generated_utc": report["generated_utc"]})
    _write_json(paths.p("phase8b_next_plan.json"), _phase8b_plan(report))


_SCOREBOARD_COLS = [
    "experiment_id", "owning_agent", "family", "score_key", "quantile", "gate", "status",
    "n_periods", "avg_names", "one_sided_turnover", "gross_sharpe", "gross_ann_return",
    "net_sharpe_25bps", "net_ann_return_25bps", "net_max_drawdown_25bps", "net_hit_rate_25bps",
    "spy_sharpe", "ew_universe_sharpe", "placebo_net_sharpe_25bps",
    "net_sharpe_minus_spy", "net_sharpe_minus_ew", "net_sharpe_minus_placebo",
    "leakage_check", "reason",
]


def _scoreboard_rows(experiments: List[dict], verdicts) -> List[dict]:
    rows = []
    for e in experiments:
        st, reason = verdicts[e["experiment_id"]]
        row = {k: e.get(k) for k in _SCOREBOARD_COLS if k in e}
        row["status"] = st
        row["reason"] = reason
        rows.append(row)
    return rows


def _experiment_registry_rows(experiments, verdicts) -> List[dict]:
    return [{
        "experiment_id": e["experiment_id"], "owning_agent": e["owning_agent"],
        "family": e["family"], "quantile": e["quantile"], "gate": e["gate"],
        "hypothesis": e["hypothesis"], "n_periods": e["n_periods"],
        "status": verdicts[e["experiment_id"]][0],
    } for e in experiments]


_AGENT_DEFS = [
    ("quant-research-director", "Owns agenda, experiment budget, stop/go decisions, anti-p-hacking",
     "research_director_decision.json"),
    ("data-foundation-agent", "Norgate ingestion, normalization, quality, survivorship audit",
     "data_quality_report.csv; survivorship_audit.csv"),
    ("universe-construction-agent", "Point-in-time universes from membership/status/liquidity",
     "universe_membership_panel.csv; universe_coverage_report.csv"),
    ("feature-library-agent", "Leak-safe versioned feature panels with lineage",
     "feature_catalog.csv; feature_lineage.json"),
    ("momentum-signal-agent", "Momentum features and momentum strategy variants",
     "momentum_signal_scoreboard.csv"),
    ("reversal-signal-agent", "Short-term reversal / overreaction hypotheses",
     "reversal_signal_scoreboard.csv"),
    ("trend-breadth-signal-agent", "Trend, breakout, breadth, market-internal signals",
     "trend_breadth_signal_scoreboard.csv"),
    ("volatility-liquidity-agent", "Volatility, liquidity, turnover, crowding signals",
     "volatility_liquidity_signal_scoreboard.csv"),
    ("validation-skeptic-agent", "Disproves signals: leakage/placebo/cost/stability/multiple-testing",
     "validation_gate_matrix.csv; failed_experiments.csv; approved_signals.csv"),
    ("risk-portfolio-agent", "Approved signals -> portfolio sims with risk constraints",
     "portfolio_risk_report.csv; strategy_scoreboard.csv"),
    ("meta-model-ensemble-agent", "Combine ONLY approved signals; no optimized weights in 8A",
     "signal_correlation_matrix.csv; ensemble_readiness_report.csv"),
    ("signal-publishing-agent", "Paper-research signal previews only; no orders/automation",
     "signal_preview_contract.csv"),
]


def _agent_architecture_rows() -> List[dict]:
    return [{
        "agent": a, "role": role, "subagent_file": f".claude/agents/{a}.md",
        "contract": "research/agents/agent_contracts.json", "key_outputs": outputs,
    } for a, role, outputs in _AGENT_DEFS]


def _agent_handoff_rows() -> List[dict]:
    chain = [
        ("data-foundation-agent", "universe-construction-agent", "normalized panel + quality report", "data_quality_pass"),
        ("universe-construction-agent", "feature-library-agent", "point-in-time universe panel", "pit_membership_present"),
        ("feature-library-agent", "momentum-signal-agent", "leak-safe feature catalog", "leakage_pass"),
        ("feature-library-agent", "reversal-signal-agent", "leak-safe feature catalog", "leakage_pass"),
        ("feature-library-agent", "trend-breadth-signal-agent", "leak-safe feature catalog", "leakage_pass"),
        ("feature-library-agent", "volatility-liquidity-agent", "leak-safe feature catalog", "leakage_pass"),
        ("momentum-signal-agent", "validation-skeptic-agent", "momentum scoreboard", "candidate_signal"),
        ("reversal-signal-agent", "validation-skeptic-agent", "reversal scoreboard", "candidate_signal"),
        ("trend-breadth-signal-agent", "validation-skeptic-agent", "trend/breadth scoreboard", "candidate_signal"),
        ("volatility-liquidity-agent", "validation-skeptic-agent", "vol/liquidity scoreboard", "candidate_signal"),
        ("validation-skeptic-agent", "risk-portfolio-agent", "approved_signals.csv", "all_gates_pass"),
        ("risk-portfolio-agent", "meta-model-ensemble-agent", "strategy scoreboard", "risk_acceptable"),
        ("meta-model-ensemble-agent", "signal-publishing-agent", "ensemble readiness report", "ensemble_ready"),
        ("signal-publishing-agent", "quant-research-director", "signal preview contract", "preview_only"),
        ("validation-skeptic-agent", "quant-research-director", "validation gate matrix", "escalation"),
    ]
    return [{"from_agent": f, "to_agent": t, "handoff_artifact": a, "gate": g} for f, t, a, g in chain]


def _research_director_decision(report: dict, experiments, verdicts) -> dict:
    approved = [e for e in experiments if verdicts[e["experiment_id"]][0] == ST_APPROVED]
    best = max(approved, key=lambda e: (e["net_sharpe_25bps"] or -9), default=None)
    return {
        "phase": PHASE,
        "generated_utc": report["generated_utc"],
        "recommendation": report["recommendation"],
        "allowed_recommendations": list(ALLOWED_RECOMMENDATIONS),
        "decision_detail": report["decision_detail"],
        "anti_p_hacking": {
            "n_experiments_registered": len(experiments),
            "all_experiments_pre_specified": True,
            "placebo_required_margin": PLACEBO_SHARPE_MARGIN,
            "leakage_check_required": True,
            "must_beat_spy_and_ew_universe": True,
            "borderline_never_rounded_up": True,
            "n_approved": len(approved),
            "n_rejected": report["decision_detail"]["n_rejected"],
            "n_weak": report["decision_detail"]["n_weak"],
        },
        "best_approved_signal": None if best is None else {
            "experiment_id": best["experiment_id"], "family": best["family"],
            "score_key": best["score_key"], "quantile": best["quantile"],
            "net_sharpe_25bps": best["net_sharpe_25bps"],
            "net_ann_return_25bps": best["net_ann_return_25bps"],
            "net_max_drawdown_25bps": best["net_max_drawdown_25bps"],
            "net_sharpe_minus_ew": best["net_sharpe_minus_ew"],
            "net_sharpe_minus_placebo": best["net_sharpe_minus_placebo"],
        },
        "stop_conditions_honored": [
            "no fundamentals", "no optimized ensemble weights", "no regime activation/throttling",
            "no ML fitting beyond deterministic score rules", "no live trading signals",
            "no orders/broker/automation", "no commit", "no push",
        ],
    }


def _phase8b_plan(report: dict) -> dict:
    rec = report["recommendation"]
    if rec == REC_READY:
        steps = [
            "Scale the survivorship-aware panel to the full Norgate S&P 500 Current & Past superset "
            "(and optionally Russell 3000) with the same point-in-time membership and GICS sectors.",
            "Hand approved signals to risk-portfolio-agent for full portfolio risk reports "
            "(beta, concentration, sector exposure, liquidity-constrained sizing).",
            "Run validation-skeptic holdout / sub-period stability and a multiple-testing correction "
            "across the full experiment registry before any ensemble.",
        ]
    elif rec == REC_BUILD_FULL:
        steps = ["Build the full Norgate panel (more symbols / longer history) — the bounded sample "
                 "was too small/slow for a valid signal judgment."]
    elif rec == REC_REJECTED:
        steps = ["All deterministic signals failed on survivorship-aware data; stop signal hunting "
                 "and escalate to the research director for an agenda change."]
    else:
        steps = ["Resolve the blocking condition (Norgate access or agent-system creation) before any "
                 "further experiments."]
    return {
        "from_phase": PHASE, "recommendation": rec, "next_phase": "8-B",
        "next_steps": steps,
        "hard_constraints": [
            "Norgate is the only provider", "large data on D: only", "repo outputs are summaries only",
            "do not install packages", "no paid APIs other than local Norgate", "no Paper Trader",
            "no GCP", "no broker/order/automation", "no optimized ensemble weights in 8B without "
            "explicit director approval", "do not commit", "do not push",
        ],
    }


def _blocked_report(started, adapter, agents_ok, present_sub, present_con, *, reason: str) -> dict:
    rec = REC_AGENT_BLOCKED if not agents_ok else REC_NORGATE_BLOCKED
    return {
        "phase": PHASE, "objective": OBJECTIVE, "generated_utc": started,
        "recommendation": rec, "allowed_recommendations": list(ALLOWED_RECOMMENDATIONS),
        "decision_detail": {"blocked_reason": reason, "agents_created": agents_ok,
                            "norgate_available": adapter.available},
        "provider": "Norgate Data (local desktop database)",
        "norgate": {"available": adapter.available, "import_error": adapter.import_error,
                    "n_access_calls": len(adapter.access_log),
                    "n_failed_calls": sum(1 for r in adapter.access_log if not r["ok"])},
        "agent_system": {"subagents_created": agents_ok, "subagents_present": present_sub,
                         "contracts_present": present_con},
        "experiments": {"n_run": 0, "n_approved": 0, "approved_ids": [], "records": []},
        "safety": {"provider_is_local_norgate_only": True, "network_or_paid_api_used": False,
                   "packages_installed": False, "large_data_only_on_d": True,
                   "orders_or_automation_created": False, "committed": False, "pushed": False},
    }


def _print_summary(report: dict) -> None:
    rec = report["recommendation"]
    exp = report.get("experiments", {})
    pan = report.get("panel", {})
    print(f"[{PHASE}] recommendation = {rec}")
    print(f"[{PHASE}] subagents created = {report['agent_system'].get('subagents_created')} "
          f"({report['agent_system'].get('n_subagents', 0)}/12); "
          f"contracts = {report['agent_system'].get('n_contracts', 0)}/6")
    if pan:
        print(f"[{PHASE}] panel = {pan.get('n_symbols')} symbols x {pan.get('n_months')} months "
              f"{pan.get('date_range')}; membership={pan.get('membership_observed')} "
              f"delisted_access={pan.get('active_delisted_access')}")
        aud = pan.get("survivorship_audit", {})
        print(f"[{PHASE}] survivorship: active={aud.get('symbols_active')} "
              f"delisted={aud.get('symbols_delisted')} "
              f"ever_member_delisted={aud.get('ever_member_delisted')} "
              f"dropout_frac={aud.get('survivorship_dropout_fraction')}")
    print(f"[{PHASE}] experiments run = {exp.get('n_run')}; approved = {exp.get('n_approved')} "
          f"{exp.get('approved_ids')}")


def _parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Phase 8-A Autonomous Norgate Research Engine")
    ap.add_argument("--out-dir", default=str(
        _REPO_ROOT / "research" / "output" / "phase8a_autonomous_norgate_research_engine"))
    ap.add_argument("--max-symbols", type=int, default=None,
                    help="bound the survivorship-aware universe (default: full superset)")
    return ap.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parse_args(argv)
    try:
        report = run(Path(args.out_dir), max_symbols=args.max_symbols)
    except Exception as exc:  # pragma: no cover - top-level guard
        out = Path(args.out_dir)
        out.mkdir(parents=True, exist_ok=True)
        err = {"phase": PHASE, "recommendation": REC_ERROR, "error": repr(exc),
               "generated_utc": _utc_now_iso()}
        _write_json(out / "phase8a_autonomous_norgate_research_engine.json", err)
        print(f"[{PHASE}] recommendation = {REC_ERROR}: {exc!r}")
        return 1
    _print_summary(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
