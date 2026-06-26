"""Phase 8-S - Autonomous EODHD Earnings/Fundamentals Alpha Factory.

WHY THIS PHASE EXISTS
    Phase 8-R ACCEPTED EODHD as the core earnings/fundamentals provider (paid access works:
    earnings_history + fundamentals_statements at 32/32 coverage, point-in-time safe). This phase is
    the autonomous acquisition + scoring campaign that turns that data into an honest alpha verdict.
    It (1) resolves the broadest research universe the local price cache can score, (2) continues
    bounded EODHD acquisition until the universe is covered or the request budget is exhausted,
    (3) normalizes EODHD into point-in-time earnings + fundamentals panels, (4) joins them to the
    local price / FRED-macro / sector context with strict no-look-ahead rules, (5) builds every
    feasible earnings + fundamentals + regime feature, (6) tests the relevant cross-sectional
    scenarios with proper validation (rank IC, quantile long-short spread, hit rate, turnover,
    transaction-cost sensitivity, regime split, sector concentration, multiple-testing control),
    and (7) promotes signals only past evidence gates, emitting one terminal research decision.

    This is preview-only research. It produces RANKING SIGNALS / TRADE-IDEA candidates for manual
    review, never orders, never automation, never broker execution. It does not touch Paper Trader
    or GCP. Raw + normalized EODHD payloads stay under the gitignored research/data/eodhd tree.

SECRET DISCIPLINE
    EODHD_API_KEY is read ONLY from the environment, never printed, never written to disk. Every
    persisted URL is redacted. A leak scan over the committed artifacts confirms it is clean. The
    low-level EODHD transport + redaction + persistence are reused verbatim from Phase 8-R.

TERMINAL DECISIONS
    ALPHA_FOUND_READY_FOR_PAPER_TRADER_REVIEW | READY_FOR_MORE_EODHD_ACQUISITION |
    NO_ALPHA_FOUND_AFTER_FULL_EODHD_SWEEP | HARD_BLOCKER_REQUIRES_USER_ACTION | ERROR

Run (offline analysis on whatever EODHD data is already cached locally):
    python research/run_phase8s_autonomous_eodhd_alpha_factory.py
Live acquisition + analysis (needs EODHD_API_KEY in the env; bounded):
    $env:EODHD_API_KEY = '<PAID_EODHD_KEY>'
    python research/run_phase8s_autonomous_eodhd_alpha_factory.py --live --max-tickers 500 --max-requests 5000
Test (fully offline; injected transport, no key, no network):
    python -m pytest tests/test_phase8s_autonomous_eodhd_alpha_factory.py -q
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import sys
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Reuse the Phase 8-R EODHD low-level layer (transport, redaction, classification, persistence,
# secret discipline) verbatim so the proven secret + provider handling is not duplicated.
from research import run_phase8r_broad_bundle_evaluation as r8  # noqa: E402

PHASE = "8-S"
PROVIDER_NAME = "EODHD"
API_KEY_ENV = r8.API_KEY_ENV

# --------------------------------------------------------------------------- #
# Paths.
# --------------------------------------------------------------------------- #
_OUT_DIR = _REPO_ROOT / "research" / "output" / "phase8s_autonomous_eodhd_alpha_factory"
_PHASE8R_DIR = _REPO_ROOT / "research" / "output" / "phase8r_broad_bundle_evaluation"
_PHASE8N_DIR = (_REPO_ROOT / "research" / "output"
                / "phase8n_fmp_critical_data_backfill_signal_expansion")
_INPUT_DIR = _REPO_ROOT / "research" / "input"
_SECTOR_MAP_CSV = _INPUT_DIR / "phase2k_p_sector_map_current.csv"
# Local broad OHLCV cache (read-only D: drive; benchmark_close gives index-relative returns).
_PRICE_CACHE_CSV = Path(
    "D:/Stock_Prediction_app_data/phase7i_broad_universe/prices/phase7i_broad_price_history_free.csv")
_MACRO = {
    "rates": _INPUT_DIR / "macro_treasury_yields.csv",     # observation_date, DGS10, DGS2
    "oil": _INPUT_DIR / "macro_oil_wti.csv",               # observation_date, DCOILWTICO
    "dollar": _INPUT_DIR / "macro_dollar_index.csv",       # observation_date, DTWEXBGS
}

# As-of cutoff: do not score events whose announcement is in the (relative) future, and let the
# price cache's last date bound the horizons. Passed in so tests are deterministic and the module
# never calls Date.now()-style clocks implicitly in scoring math.
DEFAULT_AS_OF = "2026-06-26"

# --------------------------------------------------------------------------- #
# Bounded acquisition defaults (from the brief). NOT a blind full backfill.
# --------------------------------------------------------------------------- #
DEFAULT_MAX_TICKERS = 500
DEFAULT_MAX_REQUESTS = 5000
CHECKPOINT_EVERY = 50

# --------------------------------------------------------------------------- #
# Scoring config + evidence gates.
# --------------------------------------------------------------------------- #
FWD_WINDOWS = (1, 5, 21, 63)          # trading-day forward horizons (1d, 1w, 1mo, 1q)
PRIMARY_HORIZON = 21                  # the headline drift window for IC / spread
N_QUANTILES = 5                       # quintile long-short books (robust on thin months)
MIN_NAMES_PER_MONTH = 8              # a cross-sectional month needs this many events
MIN_MONTHS = 10                       # a scenario needs this many monthly observations
MIN_EVENTS = 150                      # a scenario needs this many usable events overall

# Promotion gates (a signal must clear ALL of these).
GATE_MIN_ABS_IC = 0.03               # mean monthly rank IC magnitude
GATE_MIN_ABS_T = 2.0                 # IC t-stat (~2 sigma)
GATE_BH_Q = 0.10                     # Benjamini-Hochberg false-discovery rate
GATE_MIN_SPREAD_HITRATE = 0.55       # fraction of months the L/S spread has the right sign
GATE_COST_BPS = 10.0                 # round-trip cost per unit turnover for the net-of-cost gate
GATE_MAX_SECTOR_SHARE = 0.60         # top sector may not dominate the long book beyond this
GATE_MIN_REGIMES = 2                 # signal must work (right-sign IC) in >= this many regimes

DEC_ALPHA = "ALPHA_FOUND_READY_FOR_PAPER_TRADER_REVIEW"
DEC_MORE = "READY_FOR_MORE_EODHD_ACQUISITION"
DEC_NO_ALPHA = "NO_ALPHA_FOUND_AFTER_FULL_EODHD_SWEEP"
DEC_BLOCKER = "HARD_BLOCKER_REQUIRES_USER_ACTION"
DEC_ERROR = "ERROR"
ALLOWED_DECISIONS = (DEC_ALPHA, DEC_MORE, DEC_NO_ALPHA, DEC_BLOCKER, DEC_ERROR)

_ARTIFACTS = {
    "report": "phase8s_autonomous_eodhd_alpha_factory.json",
    "run_log": "autonomous_run_log.csv",
    "universe": "acquisition_universe.csv",
    "acq_progress": "eodhd_acquisition_progress.csv",
    "raw_manifest": "eodhd_raw_storage_manifest.csv",
    "norm_manifest": "eodhd_normalized_storage_manifest.csv",
    "family_coverage": "eodhd_family_coverage_summary.csv",
    "ticker_coverage": "eodhd_ticker_coverage_panel.csv",
    "earnings_schema": "earnings_events_schema_report.csv",
    "fundamentals_schema": "fundamentals_schema_report.csv",
    "pit_audit": "point_in_time_audit.csv",
    "join_report": "price_macro_sector_join_report.csv",
    "feature_catalog": "feature_catalog.csv",
    "scenario_plan": "scenario_test_plan.csv",
    "scoreboard": "scenario_scoreboard.csv",
    "rank_ic": "rank_ic_report.csv",
    "decile_spread": "decile_spread_report.csv",
    "hit_rate": "hit_rate_report.csv",
    "tcost": "transaction_cost_sensitivity.csv",
    "turnover": "turnover_report.csv",
    "sector_conc": "sector_concentration_report.csv",
    "regime_split": "regime_split_report.csv",
    "multiple_testing": "multiple_testing_report.csv",
    "promoted": "promoted_alpha_signals.csv",
    "rejected": "rejected_alpha_signals.csv",
    "final_decision": "final_research_decision.json",
    "next_plan": "phase8t_next_plan.json",
    "secret_audit": "secret_safety_audit.csv",
}


class _Paths:
    def __init__(self, out_dir: Optional[Path] = None, data_dir: Optional[Path] = None,
                 phase8n_dir: Optional[Path] = None, price_csv: Optional[Path] = None,
                 sector_csv: Optional[Path] = None, macro: Optional[Dict[str, Path]] = None,
                 phase8r_dir: Optional[Path] = None):
        self.out = Path(out_dir) if out_dir else _OUT_DIR
        # The EODHD data tree (raw/ + normalized/) is managed by the reused 8-R _Paths object.
        self.r8 = r8._Paths(out_dir=self.out, data_dir=data_dir)
        self.phase8r = Path(phase8r_dir) if phase8r_dir else _PHASE8R_DIR
        self.phase8n = Path(phase8n_dir) if phase8n_dir else _PHASE8N_DIR
        self.price_csv = Path(price_csv) if price_csv else _PRICE_CACHE_CSV
        self.sector_csv = Path(sector_csv) if sector_csv else _SECTOR_MAP_CSV
        self.macro = dict(macro) if macro else dict(_MACRO)
        for key, name in _ARTIFACTS.items():
            setattr(self, key, self.out / name)

    @property
    def eodhd_dir(self) -> Path:
        return self.r8.eodhd_dir

    @property
    def raw_fund_dir(self) -> Path:
        return self.r8.raw_dir / "fundamentals"

    @property
    def normalized_csv(self) -> Path:
        return self.r8.normalized_csv


# --------------------------------------------------------------------------- #
# Tiny IO + stats helpers (stdlib).
# --------------------------------------------------------------------------- #
def _rel(path: Path) -> str:
    try:
        return str(Path(path).resolve().relative_to(_REPO_ROOT)).replace("\\", "/")
    except ValueError:
        return str(path).replace("\\", "/")


def _write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=2, allow_nan=False, default=_jsonable)
        fh.write("\n")


def _jsonable(o):
    try:
        import numpy as _np
        if isinstance(o, (_np.integer,)):
            return int(o)
        if isinstance(o, (_np.floating,)):
            return None if math.isnan(float(o)) else float(o)
        if isinstance(o, (_np.bool_,)):
            return bool(o)
    except Exception:
        pass
    return str(o)


def _write_csv(path: Path, header: Sequence[str], rows: Sequence[Sequence]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        for r in rows:
            w.writerow(["" if v is None else v for v in r])


def _read_csv_file(path: Path) -> List[Dict]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return list(csv.DictReader(fh))
    except OSError:
        return []


def _read_json(path: Path) -> Dict:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {}


def _norm_sf(z: float) -> float:
    """Upper-tail of the standard normal via erfc (no scipy dependency)."""
    return 0.5 * math.erfc(z / math.sqrt(2.0))


def _two_sided_p(t: float) -> float:
    if t is None or (isinstance(t, float) and math.isnan(t)):
        return 1.0
    return max(0.0, min(1.0, 2.0 * _norm_sf(abs(t))))


def _round(x, n=4):
    try:
        xf = float(x)
    except (TypeError, ValueError):
        return None
    if math.isnan(xf) or math.isinf(xf):
        return None
    return round(xf, n)


# --------------------------------------------------------------------------- #
# Run-log accumulator.
# --------------------------------------------------------------------------- #
class _Log:
    def __init__(self, verbose: bool = True):
        self.rows: List[List] = []
        self.verbose = verbose

    def step(self, name: str, status: str, detail: str, count=None) -> None:
        self.rows.append([name, status, detail, "" if count is None else count])
        if self.verbose:
            print("  [%-10s] %-26s %s" % (status, name, detail))


# --------------------------------------------------------------------------- #
# 1. Universe resolution (broadest the price cache can actually score).
# --------------------------------------------------------------------------- #
def _price_cache_tickers(price_csv: Path) -> List[str]:
    """Distinct tickers present in the local OHLCV cache (the scoring ceiling)."""
    seen: List[str] = []
    s = set()
    try:
        with open(price_csv, "r", encoding="utf-8") as fh:
            rd = csv.reader(fh)
            header = next(rd, None)
            if not header:
                return []
            try:
                ti = [h.strip().lower() for h in header].index("ticker")
            except ValueError:
                ti = 1
            for row in rd:
                if len(row) <= ti:
                    continue
                tk = row[ti].strip().upper()
                if tk and tk not in s:
                    s.add(tk)
                    seen.append(tk)
    except OSError:
        return []
    return seen


def resolve_universe(P: _Paths, max_tickers: int) -> List[Dict]:
    """Order the universe: FMP-blocked first (Phase 8-N), then the rest of the priced universe,
    so acquisition prioritizes the names FMP could not serve. Only priced tickers are scoreable, so
    the universe is the intersection with the local OHLCV cache."""
    priced = _price_cache_tickers(P.price_csv)
    priced_set = set(priced)
    fmp = r8.read_fmp_earnings_coverage(P.phase8n)
    fmp_blocked = [t for t in fmp["fmp_blocked"] if t in priced_set]
    fmp_covered = [t for t in fmp["fmp_covered"] if t in priced_set]
    cached = set(r8._cached_tickers(P.r8))

    ordered: List[Tuple[str, str]] = []
    seen = set()
    for priority, group in (("1_fmp_blocked", fmp_blocked),
                            ("2_fmp_covered_crosscheck", fmp_covered),
                            ("3_priced_universe", priced)):
        for tk in group:
            if tk not in seen:
                seen.add(tk)
                ordered.append((tk, priority))

    rows: List[Dict] = []
    targeted = 0
    for tk, priority in ordered:
        already = tk in cached
        # A ticker counts against max_tickers only if we would actually request it (not cached).
        will_target = (not already) and (targeted < max_tickers)
        if will_target:
            targeted += 1
        rows.append({"ticker": tk, "priority": priority, "in_price_cache": True,
                     "cached_before": already, "targeted_for_acquisition": will_target})
    return rows


# --------------------------------------------------------------------------- #
# 2. Bounded EODHD acquisition (fundamentals endpoint only; calendar returns 0 rows on this plan).
#    Reuses the Phase 8-R transport, classification, persistence and error taxonomy.
# --------------------------------------------------------------------------- #
def acquire_eodhd(P: _Paths, universe: List[Dict], live: bool,
                  transport: Optional[Callable[[str, str], object]], max_requests: int,
                  log: _Log) -> Dict:
    """Fetch EODHD fundamentals for each targeted (not-yet-cached) ticker, persist the raw payload
    and append normalized earnings rows - all under the gitignored data tree. Honors max_requests
    and stops on an invalid key / free-tier block / consecutive rate-limits. Returns acquisition
    summary; writes the progress + manifest artifacts."""
    can_run = (transport is not None) or (bool(live) and r8.key_present())
    targets = [u["ticker"] for u in universe if u["targeted_for_acquisition"]]
    progress: List[List] = []
    requests_made = 0
    acquired = 0
    consecutive_rl = 0
    stopped = False
    stop_reason = ""
    blocker = ""

    if not can_run or not targets:
        reason = "no live key (offline analysis on cached data)" if not can_run else "nothing to acquire"
        log.step("acquire", "SKIP", reason, count=0)
        _write_csv(P.acq_progress,
                   ["ticker", "status", "earnings_rows", "requests_made_cumulative", "checkpoint"],
                   progress)
        _write_manifests(P)
        return {"executed": False, "requests_made": 0, "acquired": 0, "stopped": False,
                "stop_reason": reason, "blocker": "", "targets": len(targets)}

    r8._ensure_provider_gitignore(P.eodhd_dir)
    normalized: Dict[str, List[Dict]] = {}
    for tk in targets:
        if requests_made >= max_requests:
            stopped = True
            stop_reason = "max_requests (%d) reached" % max_requests
            break
        try:
            payload = (transport(tk, r8.EP_FUNDAMENTALS) if transport is not None
                       else r8._live_get(tk, r8.EP_FUNDAMENTALS))
            requests_made += 1
        except r8.EodhdError as exc:
            requests_made += 1
            etype = getattr(exc, "error_type", "error")
            if etype == "invalid_key":
                stopped = True
                stop_reason = "invalid API key"
                blocker = "EODHD_API_KEY rejected as invalid (HTTP 401)"
                progress.append([tk, "INVALID_KEY", 0, requests_made, ""])
                break
            if etype == "free_tier_blocked":
                stopped = True
                stop_reason = "free-tier entitlement block"
                blocker = ("EODHD key is on the FREE tier (EOD prices only); the fundamentals "
                           "bundle returned HTTP 403 - a paid plan is required")
                progress.append([tk, "FREE_TIER_BLOCKED", 0, requests_made, ""])
                break
            if etype == "rate_limited":
                consecutive_rl += 1
                progress.append([tk, "RATE_LIMITED", 0, requests_made, ""])
                if consecutive_rl >= r8.STOP_AFTER_CONSECUTIVE_RATE_LIMITS:
                    stopped = True
                    stop_reason = "rate-limited %d times consecutively" % consecutive_rl
                    blocker = "EODHD rate limit exhausted; re-run after backoff to continue"
                    break
                if transport is None:
                    time.sleep(r8.PROBE_MIN_SLEEP_SECONDS)
                continue
            progress.append([tk, "ERROR_%s" % etype, 0, requests_made, ""])
            if transport is None:
                time.sleep(r8.PROBE_MIN_SLEEP_SECONDS)
            continue

        consecutive_rl = 0
        fam_results = r8.classify_fundamentals(payload)
        nrows = r8._normalize_earnings(tk, payload)
        earn_ok = any(fr["status"] == "OK" and fr["family"] == r8.FAM_EARNINGS for fr in fam_results)
        if earn_ok:
            r8._persist_raw(P.r8, tk, "fundamentals", payload)
            if nrows:
                normalized[tk] = nrows
            acquired += 1
            status = "OK"
        else:
            status = "EMPTY"
        checkpoint = "checkpoint" if (acquired and acquired % CHECKPOINT_EVERY == 0) else ""
        progress.append([tk, status, len(nrows), requests_made, checkpoint])
        if checkpoint:
            log.step("acquire", "CHECKPOINT", "%d acquired / %d requests" % (acquired, requests_made),
                     count=acquired)
        if transport is None:
            time.sleep(r8.PROBE_MIN_SLEEP_SECONDS)

    if normalized:
        r8._append_normalized(P.r8, normalized)
    _write_csv(P.acq_progress,
               ["ticker", "status", "earnings_rows", "requests_made_cumulative", "checkpoint"],
               progress)
    _write_manifests(P)
    log.step("acquire", "DONE" if not stopped else "STOPPED",
             "acquired %d / requests %d%s" % (acquired, requests_made,
                                              (" - " + stop_reason) if stopped else ""),
             count=acquired)
    return {"executed": True, "requests_made": requests_made, "acquired": acquired,
            "stopped": stopped, "stop_reason": stop_reason, "blocker": blocker,
            "targets": len(targets)}


def _write_manifests(P: _Paths) -> None:
    raw_rows: List[List] = []
    if P.raw_fund_dir.is_dir():
        for fp in sorted(P.raw_fund_dir.glob("*.json")):
            raw_rows.append([fp.stem, _rel(fp), fp.stat().st_size, True])
    _write_csv(P.raw_manifest, ["ticker", "raw_path_redacted", "bytes", "gitignored"], raw_rows)

    norm_rows: List[List] = []
    nc = P.normalized_csv
    if nc.is_file():
        recs = _read_csv_file(nc)
        tickers = sorted({(r.get("symbol") or "").strip().upper() for r in recs if r.get("symbol")})
        norm_rows.append([_rel(nc), len(recs), len(tickers), True])
    _write_csv(P.norm_manifest, ["normalized_file", "rows", "distinct_tickers", "gitignored"],
               norm_rows)


# --------------------------------------------------------------------------- #
# 3. Point-in-time panels (earnings events + fundamentals quarters) from the cached EODHD data.
# --------------------------------------------------------------------------- #
def _to_float(v):
    try:
        if v is None or v == "":
            return float("nan")
        return float(v)
    except (TypeError, ValueError):
        return float("nan")


def load_earnings_events(P: _Paths) -> "object":
    """Build the earnings-events panel from the gitignored normalized earnings CSV. One row per
    (ticker, fiscal quarter): announcement date (reportDate = PIT availability), EPS actual /
    estimate / surprise."""
    import pandas as pd
    recs = _read_csv_file(P.normalized_csv)
    if not recs:
        return pd.DataFrame(columns=["ticker", "fiscal_date", "report_date", "eps_actual",
                                     "eps_estimate", "eps_difference", "surprise_percent"])
    df = pd.DataFrame(recs)
    df = df.rename(columns={"symbol": "ticker"})
    df["ticker"] = df["ticker"].str.strip().str.upper()
    df["report_date"] = pd.to_datetime(df["report_date"], errors="coerce")
    df["fiscal_date"] = pd.to_datetime(df["fiscal_date"], errors="coerce")
    for c in ("eps_actual", "eps_estimate", "eps_difference", "surprise_percent"):
        df[c] = df[c].map(_to_float)
    df = df.dropna(subset=["report_date", "fiscal_date"]).copy()
    df = df.sort_values(["ticker", "fiscal_date"]).reset_index(drop=True)
    return df


def load_fundamentals_panel(P: _Paths) -> "object":
    """Build a point-in-time fundamentals panel from the raw EODHD JSON: one row per (ticker,
    fiscal quarter) keyed by filing_date (the SEC availability date). Best-effort fields - missing
    line items become NaN."""
    import pandas as pd
    rows: List[Dict] = []
    if not P.raw_fund_dir.is_dir():
        return pd.DataFrame()
    for fp in sorted(P.raw_fund_dir.glob("*.json")):
        tk = fp.stem.upper()
        try:
            d = json.loads(fp.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        fin = d.get("Financials") or {}
        inc = ((fin.get("Income_Statement") or {}).get("quarterly") or {})
        bal = ((fin.get("Balance_Sheet") or {}).get("quarterly") or {})
        cfs = ((fin.get("Cash_Flow") or {}).get("quarterly") or {})
        for period, irow in inc.items():
            if not isinstance(irow, dict):
                continue
            brow = bal.get(period, {}) if isinstance(bal.get(period), dict) else {}
            crow = cfs.get(period, {}) if isinstance(cfs.get(period), dict) else {}
            rows.append({
                "ticker": tk,
                "fiscal_date": period,
                "filing_date": irow.get("filing_date") or brow.get("filing_date") or "",
                "total_revenue": _to_float(irow.get("totalRevenue")),
                "net_income": _to_float(irow.get("netIncome")),
                "gross_profit": _to_float(irow.get("grossProfit")),
                "operating_income": _to_float(irow.get("operatingIncome")),
                "total_assets": _to_float(brow.get("totalAssets")),
                "total_liab": _to_float(brow.get("totalLiab")),
                "short_long_debt": _to_float(brow.get("shortLongTermDebtTotal")
                                             if brow.get("shortLongTermDebtTotal") is not None
                                             else brow.get("netDebt")),
                "cash": _to_float(brow.get("cashAndShortTermInvestments")
                                  if brow.get("cashAndShortTermInvestments") is not None
                                  else brow.get("cash")),
                "free_cash_flow": _to_float(crow.get("freeCashFlow")),
            })
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df["filing_date"] = pd.to_datetime(df["filing_date"], errors="coerce")
    df["fiscal_date"] = pd.to_datetime(df["fiscal_date"], errors="coerce")
    df = df.dropna(subset=["fiscal_date"]).sort_values(["ticker", "fiscal_date"]).reset_index(drop=True)
    return df


# --------------------------------------------------------------------------- #
# 4. Price / macro / sector loaders.
# --------------------------------------------------------------------------- #
def load_prices(P: _Paths) -> "object":
    import pandas as pd
    if not P.price_csv.is_file():
        return pd.DataFrame()
    df = pd.read_csv(P.price_csv, usecols=["date", "ticker", "adjusted_close", "benchmark_close"])
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df["ticker"] = df["ticker"].astype(str).str.strip().str.upper()
    df = df.dropna(subset=["date", "adjusted_close"]).sort_values(["ticker", "date"])
    return df.reset_index(drop=True)


def load_sector_map(P: _Paths) -> Dict[str, Tuple[str, str]]:
    out: Dict[str, Tuple[str, str]] = {}
    for r in _read_csv_file(P.sector_csv):
        tk = (r.get("ticker") or "").strip().upper()
        if tk:
            out[tk] = ((r.get("sector") or "Unknown").strip() or "Unknown",
                       (r.get("industry") or "Unknown").strip() or "Unknown")
    return out


def load_macro(P: _Paths) -> "object":
    """Daily macro frame (forward-filled) with as-of columns: 10y, 2s10s slope, oil, dollar, and
    their 1y z-scores / regimes. Indexed by date for as-of joins."""
    import pandas as pd
    import numpy as np
    frames = []
    rates = P.macro.get("rates")
    if rates and Path(rates).is_file():
        r = pd.read_csv(rates)
        r["observation_date"] = pd.to_datetime(r["observation_date"], errors="coerce")
        r = r.rename(columns={"observation_date": "date"})
        for c in ("DGS10", "DGS2"):
            if c in r:
                r[c] = pd.to_numeric(r[c], errors="coerce")
        r["rates_10y"] = r.get("DGS10")
        r["rates_2s10s"] = r.get("DGS10") - r.get("DGS2") if "DGS2" in r else np.nan
        frames.append(r[["date", "rates_10y", "rates_2s10s"]])
    oil = P.macro.get("oil")
    if oil and Path(oil).is_file():
        o = pd.read_csv(oil)
        o["date"] = pd.to_datetime(o["observation_date"], errors="coerce")
        o["oil"] = pd.to_numeric(o.get("DCOILWTICO"), errors="coerce")
        frames.append(o[["date", "oil"]])
    dol = P.macro.get("dollar")
    if dol and Path(dol).is_file():
        d = pd.read_csv(dol)
        d["date"] = pd.to_datetime(d["observation_date"], errors="coerce")
        d["dollar"] = pd.to_numeric(d.get("DTWEXBGS"), errors="coerce")
        frames.append(d[["date", "dollar"]])
    if not frames:
        return pd.DataFrame()
    m = frames[0]
    for f in frames[1:]:
        m = pd.merge(m, f, on="date", how="outer")
    m = m.dropna(subset=["date"]).sort_values("date").set_index("date").ffill()
    # 1y (252-trading-day proxy = 365 cal days) rolling z-scores for oil/dollar regimes.
    for c in ("oil", "dollar"):
        if c in m:
            roll = m[c].rolling(252, min_periods=60)
            m[c + "_z"] = (m[c] - roll.mean()) / roll.std(ddof=0)
    return m.reset_index()


# --------------------------------------------------------------------------- #
# 5. Build the scored event table (no look-ahead).
# --------------------------------------------------------------------------- #
def build_event_table(P: _Paths, as_of: str, log: _Log) -> Tuple["object", Dict, List[Dict]]:
    """Join earnings events to prices/macro/sector and compute features + forward returns under
    strict no-look-ahead rules. Returns (events_df, join_stats, pit_audit_rows)."""
    import pandas as pd
    import numpy as np

    ev = load_earnings_events(P)
    fund = load_fundamentals_panel(P)
    prices = load_prices(P)
    sector = load_sector_map(P)
    macro = load_macro(P)
    as_of_ts = pd.Timestamp(as_of)

    audit: List[Dict] = []

    def chk(name, ok, detail):
        audit.append({"check": name, "result": bool(ok), "detail": detail})

    n_raw = len(ev)
    if ev.empty or prices.empty:
        chk("inputs_present", False, "missing earnings events or price cache")
        return ev.iloc[0:0], {"events_raw": n_raw, "events_usable": 0}, audit

    # --- PIT filters on the earnings event itself --------------------------------------------- #
    chk("report_date_not_before_fiscal_end",
        bool((ev["report_date"] >= ev["fiscal_date"]).all()),
        "every announcement date is on/after its fiscal period end")
    ev = ev[ev["report_date"] >= ev["fiscal_date"]].copy()
    before_future = len(ev)
    ev = ev[ev["report_date"] <= as_of_ts].copy()
    chk("drop_future_announcements", True,
        "dropped %d events announced after as_of=%s" % (before_future - len(ev), as_of))
    have_actual = ev["eps_actual"].notna()
    chk("require_realized_eps_actual", True,
        "dropped %d events with no realized eps_actual (unreported)" % int((~have_actual).sum()))
    ev = ev[have_actual].copy()

    # --- per-ticker earnings-derived features (use only prior quarters: shift) ---------------- #
    ev = ev.sort_values(["ticker", "fiscal_date"]).reset_index(drop=True)
    # Standardized unexpected earnings: surprise / rolling std of the last 8 surprises (lagged).
    ev["surprise"] = ev["eps_actual"] - ev["eps_estimate"]
    surp_std = ev.groupby("ticker")["surprise"].transform(
        lambda s: s.shift(1).rolling(8, min_periods=4).std())
    ev["sue"] = ev["surprise"] / surp_std.replace(0, np.nan)
    ev["surprise_pct"] = ev["surprise_percent"]
    ev["beat"] = (ev["surprise"] > 0).astype(float)
    ev["beat_streak"] = ev.groupby("ticker")["beat"].transform(
        lambda s: s.groupby((s != s.shift()).cumsum()).cumcount().add(1).where(s == 1, 0))
    ev["eps_yoy_growth"] = ev.groupby("ticker")["eps_actual"].transform(
        lambda s: (s - s.shift(4)) / s.shift(4).abs())
    ev["eps_accel"] = ev.groupby("ticker")["eps_yoy_growth"].transform(lambda s: s - s.shift(1))
    ev["magnitude_bucket"] = pd.cut(ev["surprise_pct"].fillna(0.0),
                                    bins=[-1e9, -10, -2, 2, 10, 1e9],
                                    labels=["big_miss", "miss", "inline", "beat", "big_beat"])

    # --- sector ------------------------------------------------------------------------------- #
    ev["sector"] = ev["ticker"].map(lambda t: sector.get(t, ("Unknown", "Unknown"))[0])

    # --- as-of fundamentals join (filing_date <= report_date) --------------------------------- #
    if not fund.empty:
        fund_feat = _fundamentals_features(fund)
        ev = _asof_join_fundamentals(ev, fund_feat)
        chk("fundamentals_asof_no_lookahead", True,
            "fundamentals joined where filing_date <= report_date (as-of merge)")
    else:
        chk("fundamentals_asof_no_lookahead", False, "no fundamentals panel available")

    # --- as-of macro join (macro date <= report_date) ---------------------------------------- #
    if not macro.empty:
        ev = pd.merge_asof(ev.sort_values("report_date"),
                           macro.sort_values("date"),
                           left_on="report_date", right_on="date", direction="backward")
        ev = ev.drop(columns=[c for c in ("date",) if c in ev.columns])
        chk("macro_asof_no_lookahead", True, "macro regime joined as-of report_date (backward)")
    else:
        chk("macro_asof_no_lookahead", False, "no macro frame available")

    # --- forward returns from prices (entry = first close strictly AFTER announcement) -------- #
    ev = _attach_forward_returns(ev, prices, log)
    prim = "fwd_exc_%d" % PRIMARY_HORIZON
    have_fwd = ev[prim].notna() if prim in ev.columns else pd.Series(False, index=ev.index)
    chk("require_forward_prices", True,
        "dropped %d events lacking %d-day forward prices (entry strictly after announcement)"
        % (int((~have_fwd).sum()), PRIMARY_HORIZON))
    ev = ev[have_fwd].copy()

    # price momentum prior to the event (entry/entry-21d), market-relative
    chk("entry_after_announcement", True,
        "entry price = first cache close strictly after report_date; no announcement-day leak")

    stats = {
        "events_raw": int(n_raw),
        "events_with_realized_eps": int(have_actual.sum()),
        "events_usable": int(len(ev)),
        "tickers_usable": int(ev["ticker"].nunique()) if not ev.empty else 0,
        "date_min": str(ev["entry_date"].min().date()) if not ev.empty else "",
        "date_max": str(ev["entry_date"].max().date()) if not ev.empty else "",
        "sectors": int(ev["sector"].nunique()) if not ev.empty else 0,
    }
    chk("usable_events_present", stats["events_usable"] > 0,
        "%d usable point-in-time earnings events across %d tickers"
        % (stats["events_usable"], stats["tickers_usable"]))
    return ev, stats, audit


def _fundamentals_features(fund: "object") -> "object":
    import numpy as np
    f = fund.sort_values(["ticker", "fiscal_date"]).copy()
    f["rev_growth"] = f.groupby("ticker")["total_revenue"].transform(
        lambda s: (s - s.shift(4)) / s.shift(4).abs())
    f["ni_growth"] = f.groupby("ticker")["net_income"].transform(
        lambda s: (s - s.shift(4)) / s.shift(4).abs())
    f["margin"] = f["net_income"] / f["total_revenue"].replace(0, np.nan)
    f["margin_chg"] = f.groupby("ticker")["margin"].transform(lambda s: s - s.shift(4))
    f["debt_assets"] = f["short_long_debt"] / f["total_assets"].replace(0, np.nan)
    f["cash_assets"] = f["cash"] / f["total_assets"].replace(0, np.nan)
    f["fcf_margin"] = f["free_cash_flow"] / f["total_revenue"].replace(0, np.nan)
    f["roa"] = f["net_income"] / f["total_assets"].replace(0, np.nan)
    keep = ["ticker", "filing_date", "rev_growth", "ni_growth", "margin", "margin_chg",
            "debt_assets", "cash_assets", "fcf_margin", "roa"]
    return f.dropna(subset=["filing_date"])[keep].sort_values("filing_date")


def _asof_join_fundamentals(ev: "object", fund_feat: "object") -> "object":
    import pandas as pd
    out = []
    ev = ev.sort_values("report_date")
    for tk, grp in ev.groupby("ticker", group_keys=False):
        ff = fund_feat[fund_feat["ticker"] == tk].sort_values("filing_date")
        if ff.empty:
            out.append(grp)
            continue
        merged = pd.merge_asof(grp.sort_values("report_date"), ff.drop(columns=["ticker"]),
                               left_on="report_date", right_on="filing_date",
                               direction="backward")
        out.append(merged)
    res = pd.concat(out, ignore_index=True) if out else ev
    return res


def _attach_forward_returns(ev: "object", prices: "object", log: _Log) -> "object":
    """For each event, entry = first cache close STRICTLY AFTER the announcement date; forward
    return over N trading days, plus benchmark-excess return. Vectorized per ticker via
    np.searchsorted on the trading-day index."""
    import numpy as np
    import pandas as pd
    # benchmark series (same across tickers per date)
    bench = (prices[["date", "benchmark_close"]].dropna().drop_duplicates("date")
             .sort_values("date").reset_index(drop=True))
    bdates = bench["date"].values.astype("datetime64[ns]")
    bvals = bench["benchmark_close"].to_numpy(dtype=float)

    cols_init = {}
    for w in FWD_WINDOWS:
        cols_init["fwd_ret_%d" % w] = np.nan
        cols_init["fwd_exc_%d" % w] = np.nan
    for c, v in cols_init.items():
        ev[c] = v
    ev["entry_date"] = pd.NaT
    ev["entry_price"] = np.nan
    ev["mom_pre_21"] = np.nan

    pg = {tk: g for tk, g in prices.groupby("ticker")}
    rows_idx = ev.index.to_numpy()
    ev_tk = ev["ticker"].to_numpy()
    ev_rd = ev["report_date"].to_numpy().astype("datetime64[ns]")

    for i, ridx in enumerate(rows_idx):
        tk = ev_tk[i]
        pgrp = pg.get(tk)
        if pgrp is None:
            continue
        pdates = pgrp["date"].to_numpy().astype("datetime64[ns]")
        pclose = pgrp["adjusted_close"].to_numpy(dtype=float)
        # entry = first trading day strictly AFTER the announcement date
        j = int(np.searchsorted(pdates, ev_rd[i], side="right"))
        if j >= len(pdates):
            continue
        entry_px = pclose[j]
        ev.at[ridx, "entry_date"] = pd.Timestamp(pdates[j])
        ev.at[ridx, "entry_price"] = entry_px
        # pre-event momentum (entry vs 21 trading days earlier), market-relative
        if j - 21 >= 0 and pclose[j - 21] > 0:
            stock_pre = entry_px / pclose[j - 21] - 1.0
            bj = int(np.searchsorted(bdates, pdates[j], side="right")) - 1
            bj0 = int(np.searchsorted(bdates, pdates[j - 21], side="right")) - 1
            if bj >= 0 and bj0 >= 0 and bvals[bj0] > 0:
                bench_pre = bvals[bj] / bvals[bj0] - 1.0
                ev.at[ridx, "mom_pre_21"] = stock_pre - bench_pre
        # forward windows
        bj_entry = int(np.searchsorted(bdates, pdates[j], side="right")) - 1
        for w in FWD_WINDOWS:
            k = j + w
            if k >= len(pdates) or entry_px <= 0:
                continue
            stock_ret = pclose[k] / entry_px - 1.0
            ev.at[ridx, "fwd_ret_%d" % w] = stock_ret
            bj_k = int(np.searchsorted(bdates, pdates[k], side="right")) - 1
            if bj_entry >= 0 and bj_k >= 0 and bvals[bj_entry] > 0:
                bench_ret = bvals[bj_k] / bvals[bj_entry] - 1.0
                ev.at[ridx, "fwd_exc_%d" % w] = stock_ret - bench_ret
    return ev


# --------------------------------------------------------------------------- #
# 6. Feature catalog + 7/8. Scenario battery with proper validation.
# --------------------------------------------------------------------------- #
def feature_catalog() -> List[Dict]:
    return [
        {"feature": "surprise", "family": "earnings", "definition": "eps_actual - eps_estimate",
         "pit_basis": "report_date"},
        {"feature": "surprise_pct", "family": "earnings", "definition": "EODHD surprisePercent",
         "pit_basis": "report_date"},
        {"feature": "sue", "family": "earnings",
         "definition": "surprise / rolling std of last 8 lagged surprises", "pit_basis": "report_date"},
        {"feature": "magnitude_bucket", "family": "earnings",
         "definition": "surprise_pct binned (big_miss..big_beat)", "pit_basis": "report_date"},
        {"feature": "beat", "family": "earnings", "definition": "1 if surprise>0", "pit_basis": "report_date"},
        {"feature": "beat_streak", "family": "earnings",
         "definition": "consecutive prior beats incl. current", "pit_basis": "report_date"},
        {"feature": "eps_yoy_growth", "family": "earnings",
         "definition": "(eps_t - eps_t-4)/|eps_t-4|", "pit_basis": "report_date"},
        {"feature": "eps_accel", "family": "earnings",
         "definition": "change in eps_yoy_growth vs prior quarter", "pit_basis": "report_date"},
        {"feature": "mom_pre_21", "family": "price",
         "definition": "market-relative 21d return into the event", "pit_basis": "entry_date"},
        {"feature": "rev_growth", "family": "fundamentals", "definition": "YoY revenue growth",
         "pit_basis": "filing_date<=report_date"},
        {"feature": "ni_growth", "family": "fundamentals", "definition": "YoY net-income growth",
         "pit_basis": "filing_date<=report_date"},
        {"feature": "margin", "family": "fundamentals", "definition": "net_income/total_revenue",
         "pit_basis": "filing_date<=report_date"},
        {"feature": "margin_chg", "family": "fundamentals", "definition": "YoY change in margin",
         "pit_basis": "filing_date<=report_date"},
        {"feature": "debt_assets", "family": "fundamentals", "definition": "debt/total_assets",
         "pit_basis": "filing_date<=report_date"},
        {"feature": "cash_assets", "family": "fundamentals", "definition": "cash/total_assets",
         "pit_basis": "filing_date<=report_date"},
        {"feature": "fcf_margin", "family": "fundamentals", "definition": "free_cash_flow/revenue",
         "pit_basis": "filing_date<=report_date"},
        {"feature": "roa", "family": "fundamentals", "definition": "net_income/total_assets",
         "pit_basis": "filing_date<=report_date"},
        {"feature": "rates_10y", "family": "regime", "definition": "FRED DGS10 as-of report_date",
         "pit_basis": "report_date"},
        {"feature": "rates_2s10s", "family": "regime", "definition": "DGS10-DGS2 slope",
         "pit_basis": "report_date"},
        {"feature": "oil_z / dollar_z", "family": "regime",
         "definition": "1y z-score of WTI / broad dollar", "pit_basis": "report_date"},
    ]


def scenario_plan() -> List[Dict]:
    """Each scenario = a signal column + an optional sector-neutralization + an optional regime
    filter, evaluated against the primary forward-excess-return horizon."""
    return [
        {"scenario": "earnings_surprise", "signal": "surprise_pct", "sector_neutral": False,
         "regime_filter": None, "hypothesis": "positive EPS surprise -> positive drift"},
        {"scenario": "sue_standardized", "signal": "sue", "sector_neutral": False,
         "regime_filter": None, "hypothesis": "standardized unexpected earnings -> drift (PEAD)"},
        {"scenario": "surprise_x_rates_regime", "signal": "surprise_pct", "sector_neutral": False,
         "regime_filter": "high_rates", "hypothesis": "surprise drift conditioned on high-rate regime"},
        {"scenario": "surprise_sector_neutral", "signal": "surprise_pct", "sector_neutral": True,
         "regime_filter": None, "hypothesis": "within-sector surprise ranking -> drift"},
        {"scenario": "surprise_x_quality", "signal": "surprise_pct_x_roa", "sector_neutral": False,
         "regime_filter": None, "hypothesis": "surprise gated by ROA quality"},
        {"scenario": "accel_x_momentum", "signal": "accel_x_mom", "sector_neutral": False,
         "regime_filter": None, "hypothesis": "earnings acceleration confirmed by price momentum"},
        {"scenario": "beat_streak", "signal": "beat_streak", "sector_neutral": False,
         "regime_filter": None, "hypothesis": "repeated beats -> continued drift"},
        {"scenario": "quality_filtered_surprise", "signal": "surprise_pct", "sector_neutral": False,
         "regime_filter": "high_quality", "hypothesis": "surprise only among high-ROA names"},
        {"scenario": "rev_growth", "signal": "rev_growth", "sector_neutral": True,
         "regime_filter": None, "hypothesis": "fundamental revenue growth -> drift"},
        {"scenario": "regime_gated_sue", "signal": "sue", "sector_neutral": False,
         "regime_filter": "easy_regime", "hypothesis": "SUE only in supportive (steep-curve) regime"},
    ]


def _prepare_signals(ev: "object") -> "object":
    import numpy as np
    ev = ev.copy()
    # composite signals
    if "roa" in ev.columns:
        ev["surprise_pct_x_roa"] = ev["surprise_pct"] * _zscore(ev["roa"])
    else:
        ev["surprise_pct_x_roa"] = np.nan
    if "mom_pre_21" in ev.columns:
        ev["accel_x_mom"] = _zscore(ev["eps_accel"]) + _zscore(ev["mom_pre_21"])
    else:
        ev["accel_x_mom"] = np.nan
    # regime flags (as-of, no look-ahead - all from report_date macro)
    if "rates_10y" in ev.columns:
        ev["high_rates"] = ev["rates_10y"] >= ev["rates_10y"].median()
    if "rates_2s10s" in ev.columns:
        ev["easy_regime"] = ev["rates_2s10s"] >= 0  # non-inverted curve
    if "roa" in ev.columns:
        ev["high_quality"] = ev["roa"] >= ev["roa"].median()
    return ev


def _zscore(s: "object") -> "object":
    m, sd = s.mean(), s.std(ddof=0)
    if sd == 0 or (isinstance(sd, float) and math.isnan(sd)):
        return s * 0.0
    return (s - m) / sd


def _spearman(a, b) -> float:
    import numpy as np
    if len(a) < 3:
        return float("nan")
    ra = _rankdata(np.asarray(a, dtype=float))
    rb = _rankdata(np.asarray(b, dtype=float))
    ra = (ra - ra.mean())
    rb = (rb - rb.mean())
    denom = math.sqrt(float((ra * ra).sum()) * float((rb * rb).sum()))
    if denom == 0:
        return float("nan")
    return float((ra * rb).sum() / denom)


def _rankdata(x):
    import numpy as np
    order = np.argsort(x, kind="mergesort")
    ranks = np.empty(len(x), dtype=float)
    ranks[order] = np.arange(1, len(x) + 1, dtype=float)
    # average ties
    _, inv, counts = np.unique(x, return_inverse=True, return_counts=True)
    sums = np.zeros(len(counts))
    np.add.at(sums, inv, ranks)
    avg = sums / counts
    return avg[inv]


def evaluate_signal(ev: "object", signal: str, ret_col: str, sector_neutral: bool,
                    regime_filter: Optional[str]) -> Dict:
    """Full cross-sectional validation battery for one signal: monthly rank IC (+t/p), quintile
    long-short spread (+t, hit rate), event hit rate, turnover, transaction-cost sensitivity,
    regime split, sector concentration."""
    import numpy as np
    import pandas as pd
    df = ev.copy()
    if signal not in df.columns or ret_col not in df.columns:
        return {"n_events": 0, "n_months": 0, "skipped": "missing column %s" % signal}
    if regime_filter and regime_filter in df.columns:
        df = df[df[regime_filter] == True]  # noqa: E712
    df = df[df[signal].notna() & df[ret_col].notna()].copy()
    if df.empty:
        return {"n_events": 0, "n_months": 0, "skipped": "no rows after filter"}
    df["month"] = df["entry_date"].dt.to_period("M")
    if sector_neutral and "sector" in df.columns:
        df["sig"] = df.groupby(["month", "sector"])[signal].transform(lambda s: s - s.mean())
    else:
        df["sig"] = df[signal]
    df = df[df["sig"].notna()].copy()

    ics, spreads, top_names, bottom_names = [], [], [], []
    months = sorted(df["month"].unique())
    prev_top = set()
    turnovers = []
    long_sectors: Dict[str, int] = {}
    for mth in months:
        chunk = df[df["month"] == mth]
        if len(chunk) < MIN_NAMES_PER_MONTH:
            continue
        ics.append(_spearman(chunk["sig"].to_numpy(), chunk[ret_col].to_numpy()))
        # quantile books
        try:
            q = pd.qcut(chunk["sig"].rank(method="first"), N_QUANTILES, labels=False)
        except ValueError:
            continue
        top = chunk[q == N_QUANTILES - 1]
        bot = chunk[q == 0]
        if top.empty or bot.empty:
            continue
        spreads.append(float(top[ret_col].mean() - bot[ret_col].mean()))
        cur_top = set(top["ticker"])
        if prev_top:
            turn = 1.0 - len(cur_top & prev_top) / max(1, len(cur_top))
            turnovers.append(turn)
        prev_top = cur_top
        for sec in top["sector"]:
            long_sectors[sec] = long_sectors.get(sec, 0) + 1

    n_months = len(ics)
    ic_arr = np.asarray([x for x in ics if not math.isnan(x)], dtype=float)
    mean_ic = float(ic_arr.mean()) if len(ic_arr) else float("nan")
    ic_std = float(ic_arr.std(ddof=1)) if len(ic_arr) > 1 else float("nan")
    ic_t = (mean_ic / (ic_std / math.sqrt(len(ic_arr)))
            if len(ic_arr) > 1 and ic_std and not math.isnan(ic_std) and ic_std > 0 else float("nan"))
    ic_p = _two_sided_p(ic_t)

    sp_arr = np.asarray(spreads, dtype=float)
    mean_spread = float(sp_arr.mean()) if len(sp_arr) else float("nan")
    sp_std = float(sp_arr.std(ddof=1)) if len(sp_arr) > 1 else float("nan")
    sp_t = (mean_spread / (sp_std / math.sqrt(len(sp_arr)))
            if len(sp_arr) > 1 and sp_std and sp_std > 0 else float("nan"))
    # spread hit rate: months with same sign as the mean
    if len(sp_arr) and not math.isnan(mean_spread):
        sgn = np.sign(mean_spread)
        spread_hit = float(np.mean(np.sign(sp_arr) == sgn))
    else:
        spread_hit = float("nan")

    # event-level hit rate (positive forward excess return among long-signal half)
    hi = df[df["sig"] >= df["sig"].median()]
    event_hit = float((hi[ret_col] > 0).mean()) if len(hi) else float("nan")

    avg_turnover = float(np.mean(turnovers)) if turnovers else float("nan")
    # net-of-cost spread at GATE_COST_BPS round-trip per unit turnover
    def net_spread(bps):
        t = avg_turnover if not math.isnan(avg_turnover) else 1.0
        return mean_spread - (bps / 1e4) * t * 2.0 if not math.isnan(mean_spread) else float("nan")

    # sector concentration of the long book
    tot_long = sum(long_sectors.values())
    if tot_long:
        shares = {k: v / tot_long for k, v in long_sectors.items()}
        top_sector = max(shares, key=shares.get)
        top_share = shares[top_sector]
        hhi = float(sum(v * v for v in shares.values()))
    else:
        top_sector, top_share, hhi = "", float("nan"), float("nan")

    # regime split of mean IC (high vs low rates)
    regime_ic = {}
    if "high_rates" in df.columns:
        for label, mask in (("high_rates", df["high_rates"] == True),  # noqa: E712
                            ("low_rates", df["high_rates"] == False)):  # noqa: E712
            sub = df[mask]
            sub_ics = []
            for mth in sorted(sub["month"].unique()):
                c = sub[sub["month"] == mth]
                if len(c) >= MIN_NAMES_PER_MONTH:
                    sub_ics.append(_spearman(c["sig"].to_numpy(), c[ret_col].to_numpy()))
            sub_ics = [x for x in sub_ics if not math.isnan(x)]
            if sub_ics:
                regime_ic[label] = float(np.mean(sub_ics))
    regimes_right_sign = (sum(1 for v in regime_ic.values()
                              if not math.isnan(mean_ic) and math.copysign(1, v) == math.copysign(1, mean_ic))
                          if not math.isnan(mean_ic) else 0)

    return {
        "n_events": int(len(df)), "n_months": int(n_months),
        "mean_ic": mean_ic, "ic_std": ic_std, "ic_t": ic_t, "ic_p": ic_p,
        "mean_spread": mean_spread, "spread_t": sp_t, "spread_hit_rate": spread_hit,
        "event_hit_rate": event_hit, "avg_turnover": avg_turnover,
        "net_spread_5bps": net_spread(5), "net_spread_10bps": net_spread(10),
        "net_spread_20bps": net_spread(20),
        "top_sector": top_sector, "top_sector_share": top_share, "hhi": hhi,
        "regime_ic": regime_ic, "regimes_right_sign": int(regimes_right_sign),
    }


def benjamini_hochberg(pvals: List[float], q: float) -> List[bool]:
    m = len(pvals)
    if m == 0:
        return []
    order = sorted(range(m), key=lambda i: (float("inf") if pvals[i] is None
                                            or math.isnan(pvals[i]) else pvals[i]))
    sig = [False] * m
    thresh_rank = -1
    for rank, idx in enumerate(order, start=1):
        p = pvals[idx]
        if p is None or math.isnan(p):
            continue
        if p <= (rank / m) * q:
            thresh_rank = rank
    if thresh_rank >= 1:
        for rank, idx in enumerate(order, start=1):
            if rank <= thresh_rank:
                sig[idx] = True
    return sig


def score_scenarios(ev: "object", log: _Log) -> Tuple[List[Dict], List[Dict], List[Dict]]:
    """Run every scenario, apply multiple-testing control, and split into promoted/rejected by the
    evidence gates. Returns (results, promoted, rejected)."""
    ev = _prepare_signals(ev)
    ret_col = "fwd_exc_%d" % PRIMARY_HORIZON
    plan = scenario_plan()
    results: List[Dict] = []
    for sc in plan:
        metrics = evaluate_signal(ev, sc["signal"], ret_col, sc["sector_neutral"],
                                  sc["regime_filter"])
        row = dict(sc)
        row.update(metrics)
        results.append(row)

    # multiple-testing control across scenarios with a computable IC p-value
    pvals = [r.get("ic_p") if r.get("ic_p") is not None else float("nan") for r in results]
    bh = benjamini_hochberg(pvals, GATE_BH_Q)
    m = len([p for p in pvals if p is not None and not math.isnan(p)])
    for r, sig in zip(results, bh):
        p = r.get("ic_p")
        r["bonferroni_p"] = (min(1.0, p * m) if p is not None and not math.isnan(p) and m else None)
        r["bh_significant"] = bool(sig)

    promoted, rejected = [], []
    for r in results:
        reasons = _gate_reasons(r)
        if not reasons:
            promoted.append(r)
        else:
            rr = dict(r)
            rr["reject_reason"] = "; ".join(reasons)
            rejected.append(rr)
    log.step("score", "DONE", "%d scenarios, %d promoted, %d rejected"
             % (len(results), len(promoted), len(rejected)), count=len(promoted))
    return results, promoted, rejected


def _gate_reasons(r: Dict) -> List[str]:
    """Return the list of failed gates (empty => the signal is promoted)."""
    reasons = []
    if r.get("n_events", 0) < MIN_EVENTS:
        reasons.append("insufficient events (%s<%d)" % (r.get("n_events", 0), MIN_EVENTS))
    if r.get("n_months", 0) < MIN_MONTHS:
        reasons.append("insufficient months (%s<%d)" % (r.get("n_months", 0), MIN_MONTHS))
    ic = r.get("mean_ic")
    if ic is None or math.isnan(ic) or abs(ic) < GATE_MIN_ABS_IC:
        reasons.append("|mean_ic| below %.2f" % GATE_MIN_ABS_IC)
    t = r.get("ic_t")
    if t is None or math.isnan(t) or abs(t) < GATE_MIN_ABS_T:
        reasons.append("|ic_t| below %.1f" % GATE_MIN_ABS_T)
    if not r.get("bh_significant"):
        reasons.append("fails Benjamini-Hochberg q=%.2f" % GATE_BH_Q)
    sh = r.get("spread_hit_rate")
    if sh is None or math.isnan(sh) or sh < GATE_MIN_SPREAD_HITRATE:
        reasons.append("spread hit-rate below %.2f" % GATE_MIN_SPREAD_HITRATE)
    # net-of-cost spread must keep the (non-zero) sign of the gross spread
    net = r.get("net_spread_10bps")
    spread = r.get("mean_spread")
    if (net is None or math.isnan(net) or spread is None or math.isnan(spread)
            or net * spread <= 0):
        reasons.append("does not survive %g bps cost" % GATE_COST_BPS)
    share = r.get("top_sector_share")
    if share is not None and not math.isnan(share) and share > GATE_MAX_SECTOR_SHARE:
        reasons.append("sector-concentrated (%s %.0f%%)" % (r.get("top_sector"), share * 100))
    if r.get("regimes_right_sign", 0) < GATE_MIN_REGIMES:
        reasons.append("not robust across regimes")
    return reasons


# --------------------------------------------------------------------------- #
# Artifact writers for the analysis stage.
# --------------------------------------------------------------------------- #
def _schema_report(df: "object", descriptions: Dict[str, str]) -> List[List]:
    rows = []
    for c in df.columns:
        nn = int(df[c].notna().sum())
        rows.append([c, str(df[c].dtype), nn, descriptions.get(c, "")])
    return rows


def write_analysis_artifacts(P: _Paths, ev: "object", fund: "object", stats: Dict,
                             audit: List[Dict], results: List[Dict], promoted: List[Dict],
                             rejected: List[Dict]) -> None:
    import pandas as pd
    # schema reports
    _write_csv(P.earnings_schema, ["column", "dtype", "non_null", "description"],
               _schema_report(ev, {"sue": "standardized unexpected earnings",
                                   "fwd_exc_21": "21d forward excess return (primary)"}))
    if isinstance(fund, pd.DataFrame) and not fund.empty:
        _write_csv(P.fundamentals_schema, ["column", "dtype", "non_null", "description"],
                   _schema_report(fund, {"filing_date": "PIT availability date"}))
    else:
        _write_csv(P.fundamentals_schema, ["column", "dtype", "non_null", "description"], [])

    _write_csv(P.pit_audit, ["check", "result", "detail"],
               [[a["check"], a["result"], a["detail"]] for a in audit])

    _write_csv(P.join_report, ["metric", "value"],
               [[k, stats.get(k, "")] for k in
                ("events_raw", "events_with_realized_eps", "events_usable", "tickers_usable",
                 "date_min", "date_max", "sectors")])

    _write_csv(P.feature_catalog, ["feature", "family", "definition", "pit_basis"],
               [[f["feature"], f["family"], f["definition"], f["pit_basis"]]
                for f in feature_catalog()])

    _write_csv(P.scenario_plan, ["scenario", "signal", "sector_neutral", "regime_filter",
                                 "hypothesis"],
               [[s["scenario"], s["signal"], s["sector_neutral"], s["regime_filter"] or "",
                 s["hypothesis"]] for s in scenario_plan()])

    def g(r, k, n=4):
        return _round(r.get(k), n)

    _write_csv(P.scoreboard,
               ["scenario", "n_events", "n_months", "mean_ic", "ic_t", "ic_p", "bh_significant",
                "mean_spread", "spread_t", "spread_hit_rate", "net_spread_10bps", "promoted"],
               [[r["scenario"], r.get("n_events", 0), r.get("n_months", 0), g(r, "mean_ic"),
                 g(r, "ic_t", 2), g(r, "ic_p"), r.get("bh_significant", False),
                 g(r, "mean_spread"), g(r, "spread_t", 2), g(r, "spread_hit_rate"),
                 g(r, "net_spread_10bps"), r in promoted] for r in results])

    _write_csv(P.rank_ic, ["scenario", "horizon", "mean_ic", "ic_std", "n_months", "ic_t", "ic_p",
                           "bh_significant"],
               [[r["scenario"], PRIMARY_HORIZON, g(r, "mean_ic"), g(r, "ic_std"),
                 r.get("n_months", 0), g(r, "ic_t", 2), g(r, "ic_p"), r.get("bh_significant", False)]
                for r in results])

    _write_csv(P.decile_spread, ["scenario", "quantiles", "mean_long_short_spread", "spread_t",
                                 "spread_hit_rate", "n_months"],
               [[r["scenario"], N_QUANTILES, g(r, "mean_spread"), g(r, "spread_t", 2),
                 g(r, "spread_hit_rate"), r.get("n_months", 0)] for r in results])

    _write_csv(P.hit_rate, ["scenario", "event_hit_rate", "month_spread_hit_rate"],
               [[r["scenario"], g(r, "event_hit_rate"), g(r, "spread_hit_rate")] for r in results])

    _write_csv(P.tcost, ["scenario", "gross_spread", "avg_turnover", "net_5bps", "net_10bps",
                         "net_20bps"],
               [[r["scenario"], g(r, "mean_spread"), g(r, "avg_turnover"), g(r, "net_spread_5bps"),
                 g(r, "net_spread_10bps"), g(r, "net_spread_20bps")] for r in results])

    _write_csv(P.turnover, ["scenario", "avg_top_quintile_turnover", "interpretation"],
               [[r["scenario"], g(r, "avg_turnover"),
                 "fraction of top-quintile names replaced month-over-month"] for r in results])

    _write_csv(P.sector_conc, ["scenario", "top_sector", "top_sector_share", "hhi"],
               [[r["scenario"], r.get("top_sector", ""), g(r, "top_sector_share"), g(r, "hhi")]
                for r in results])

    reg_rows = []
    for r in results:
        for label, val in (r.get("regime_ic") or {}).items():
            reg_rows.append([r["scenario"], label, _round(val)])
    _write_csv(P.regime_split, ["scenario", "regime", "mean_ic"], reg_rows)

    _write_csv(P.multiple_testing, ["scenario", "ic_p", "bonferroni_p", "bh_q", "bh_significant"],
               [[r["scenario"], g(r, "ic_p"), g(r, "bonferroni_p"), GATE_BH_Q,
                 r.get("bh_significant", False)] for r in results])

    _write_csv(P.promoted, ["scenario", "signal", "mean_ic", "ic_t", "mean_spread",
                            "spread_hit_rate", "net_spread_10bps", "n_events", "n_months"],
               [[r["scenario"], r["signal"], g(r, "mean_ic"), g(r, "ic_t", 2), g(r, "mean_spread"),
                 g(r, "spread_hit_rate"), g(r, "net_spread_10bps"), r.get("n_events", 0),
                 r.get("n_months", 0)] for r in promoted])

    _write_csv(P.rejected, ["scenario", "signal", "mean_ic", "ic_t", "n_events", "reject_reason"],
               [[r["scenario"], r["signal"], g(r, "mean_ic"), g(r, "ic_t", 2), r.get("n_events", 0),
                 r.get("reject_reason", "")] for r in rejected])


def write_coverage_artifacts(P: _Paths, ev: "object", fund: "object") -> Dict:
    import pandas as pd
    recs = _read_csv_file(P.normalized_csv)
    earn_by_tk: Dict[str, int] = {}
    for r in recs:
        tk = (r.get("symbol") or "").strip().upper()
        if tk:
            earn_by_tk[tk] = earn_by_tk.get(tk, 0) + 1
    fund_by_tk = (fund.groupby("ticker").size().to_dict()
                  if isinstance(fund, pd.DataFrame) and not fund.empty else {})
    usable_by_tk = (ev.groupby("ticker").size().to_dict()
                    if isinstance(ev, pd.DataFrame) and not ev.empty else {})
    tickers = sorted(set(earn_by_tk) | set(fund_by_tk))
    rows = []
    for tk in tickers:
        rows.append([tk, earn_by_tk.get(tk, 0), fund_by_tk.get(tk, 0),
                     usable_by_tk.get(tk, 0), usable_by_tk.get(tk, 0) > 0])
    _write_csv(P.ticker_coverage,
               ["ticker", "earnings_rows", "fundamentals_quarters", "usable_scored_events",
                "scoreable"], rows)

    fam_rows = [
        ["earnings_history", len(earn_by_tk), sum(1 for v in earn_by_tk.values() if v > 0),
         len(usable_by_tk), "report_date PIT-safe"],
        ["fundamentals_statements", len(fund_by_tk), sum(1 for v in fund_by_tk.values() if v > 0),
         len(usable_by_tk), "filing_date PIT-safe"],
        ["earnings_calendar", 0, 0, 0, "0 rows on this EODHD plan (reportDate used instead)"],
        ["analyst_estimates", len(earn_by_tk), 0, 0, "NOT PIT-safe (revised) - excluded from scoring"],
        ["analyst_ratings", len(earn_by_tk), 0, 0, "NOT PIT-safe (snapshot) - excluded from scoring"],
    ]
    _write_csv(P.family_coverage,
               ["family", "tickers_present", "tickers_with_data", "tickers_scoreable", "pit_note"],
               fam_rows)
    return {"earnings_tickers": len(earn_by_tk), "fundamentals_tickers": len(fund_by_tk),
            "scoreable_tickers": len(usable_by_tk)}


# --------------------------------------------------------------------------- #
# Decision.
# --------------------------------------------------------------------------- #
def derive_decision(acq: Dict, stats: Dict, results: List[Dict], promoted: List[Dict],
                    universe: List[Dict]) -> Tuple[str, str]:
    if acq.get("blocker"):
        return (DEC_BLOCKER, acq["blocker"])
    if stats.get("events_usable", 0) == 0:
        return (DEC_BLOCKER,
                "No usable point-in-time earnings events could be built (no cached EODHD earnings "
                "or no overlapping price history). Acquire EODHD fundamentals for priced tickers "
                "first (set EODHD_API_KEY and re-run --live).")
    if promoted:
        return (DEC_ALPHA,
                "%d signal(s) cleared every evidence gate (rank IC, t-stat, BH multiple-testing, "
                "quantile spread hit-rate, net-of-cost, sector/regime robustness): %s. Surface as "
                "preview-only ranking signals for manual Paper Trader review - no orders."
                % (len(promoted), ", ".join(p["scenario"] for p in promoted)))
    # nothing promoted: is the universe still expandable?
    n_targeted_remaining = sum(1 for u in universe
                               if u["targeted_for_acquisition"] and not u["cached_before"])
    swept = (not acq.get("stopped")) and n_targeted_remaining == 0 and not acq.get("executed", False)
    # "best" near-miss?
    best = max(results, key=lambda r: (abs(r.get("ic_t") or 0)
                                       if r.get("ic_t") is not None and not math.isnan(r.get("ic_t") or float("nan"))
                                       else 0.0), default=None)
    near_miss = bool(best and best.get("ic_t") is not None and not math.isnan(best.get("ic_t") or float("nan"))
                     and abs(best["ic_t"]) >= 1.5)
    scoreable = stats.get("tickers_usable", 0)
    if scoreable < 150 or near_miss:
        return (DEC_MORE,
                "No signal cleared the full evidence gate on the current %d-ticker scoreable cross-"
                "section%s. The cross-section is below the 150-ticker target, so the honest next "
                "step is to acquire more EODHD coverage (priced universe has more names) and re-"
                "score before concluding."
                % (scoreable, " (best scenario is a near-miss: |IC t|>=1.5)" if near_miss else ""))
    return (DEC_NO_ALPHA,
            "No earnings/fundamentals signal cleared the evidence gates across the full scoreable "
            "EODHD cross-section (%d tickers). After honest cross-sectional, multiple-testing-"
            "controlled, cost-aware testing, no promotable alpha was found." % scoreable)


# --------------------------------------------------------------------------- #
# Orchestration.
# --------------------------------------------------------------------------- #
def run(live: bool = False, transport: Optional[Callable[[str, str], object]] = None,
        out_dir: Optional[Path] = None, data_dir: Optional[Path] = None,
        phase8n_dir: Optional[Path] = None, price_csv: Optional[Path] = None,
        sector_csv: Optional[Path] = None, macro: Optional[Dict[str, Path]] = None,
        phase8r_dir: Optional[Path] = None,
        max_tickers: int = DEFAULT_MAX_TICKERS, max_requests: int = DEFAULT_MAX_REQUESTS,
        as_of: str = DEFAULT_AS_OF, verbose: bool = True) -> Dict:
    P = _Paths(out_dir, data_dir, phase8n_dir, price_csv, sector_csv, macro, phase8r_dir)
    P.out.mkdir(parents=True, exist_ok=True)
    log = _Log(verbose)
    try:
        present = r8.key_present()
        phase8r = _read_json(P.phase8r / "phase8r_broad_bundle_evaluation.json")
        phase8r_decision = phase8r.get("decision", "EODHD_ACCEPT_AS_CORE_PROVIDER")
        log.step("prior", "INFO", "Phase 8-R decision read: %s" % phase8r_decision)
        log.step("start", "INFO", "live=%s key_present=%s max_tickers=%d max_requests=%d"
                 % (live, present, max_tickers, max_requests))

        # 1. universe
        universe = resolve_universe(P, max_tickers)
        _write_csv(P.universe,
                   ["ticker", "priority", "in_price_cache", "cached_before",
                    "targeted_for_acquisition"],
                   [[u["ticker"], u["priority"], u["in_price_cache"], u["cached_before"],
                     u["targeted_for_acquisition"]] for u in universe])
        n_target = sum(1 for u in universe if u["targeted_for_acquisition"])
        log.step("universe", "DONE", "%d priced tickers; %d targeted for acquisition"
                 % (len(universe), n_target), count=len(universe))

        # 2. acquisition (bounded; live-gated; reuses 8-R transport + secret discipline)
        acq = acquire_eodhd(P, universe, live, transport, max_requests, log)

        # 3-5. panels + join + event table
        fund = load_fundamentals_panel(P)
        ev, stats, audit = build_event_table(P, as_of, log)
        log.step("panels", "DONE", "%d usable events / %d tickers"
                 % (stats.get("events_usable", 0), stats.get("tickers_usable", 0)),
                 count=stats.get("events_usable", 0))
        cov = write_coverage_artifacts(P, ev, fund)

        # 6-9. features + scenarios + scoring
        if stats.get("events_usable", 0) > 0:
            results, promoted, rejected = score_scenarios(ev, log)
        else:
            results, promoted, rejected = [], [], []
            log.step("score", "SKIP", "no usable events to score")
        write_analysis_artifacts(P, ev, fund, stats, audit, results, promoted, rejected)

        # 10. decision
        decision, rationale = derive_decision(acq, stats, results, promoted, universe)
        log.step("decision", "DONE", "%s" % decision)

        # final-decision + next-plan + secret audit + main report
        best = None
        if results:
            ranked = [r for r in results if r.get("ic_t") is not None
                      and not math.isnan(r.get("ic_t") or float("nan"))]
            best = max(ranked, key=lambda r: abs(r["ic_t"]), default=None)
        _write_json(P.final_decision, {
            "phase": PHASE, "decision": decision, "decision_is_terminal": decision in ALLOWED_DECISIONS,
            "allowed_decisions": list(ALLOWED_DECISIONS), "rationale": rationale,
            "alpha_found": decision == DEC_ALPHA,
            "promoted_signals": [p["scenario"] for p in promoted],
            "best_scenario": (best or {}).get("scenario") if best else None,
            "best_scenario_ic": _round((best or {}).get("mean_ic")) if best else None,
            "best_scenario_ic_t": _round((best or {}).get("ic_t"), 2) if best else None,
            "scoreable_tickers": stats.get("tickers_usable", 0),
            "usable_events": stats.get("events_usable", 0),
            "preview_only": True, "orders_enabled": False, "automation_enabled": False,
            "broker_execution_enabled": False, "paper_trader_touched": False, "gcp_touched": False,
            "committed": False,
        })

        next_cmd, next_title = _next_step(decision, max_tickers, max_requests)
        _write_json(P.next_plan, {
            "phase": "8-T", "title": next_title, "depends_on_decision": decision,
            "next_command": next_cmd,
            "preview_only": True, "orders_enabled": False, "automation_enabled": False,
            "committed": False,
        })

        # run log
        _write_csv(P.run_log, ["step", "status", "detail", "count"], log.rows)

        # secret leak scan over committed artifacts
        written = [p for p in P.out.glob("*") if p.is_file()]
        leak_clean, scanned, marker_found = r8._scan_for_leaks(written)
        _write_csv(P.secret_audit, ["check", "result", "detail"],
                   [["api_key_read_from_env_only", True, "EODHD_API_KEY via os.environ only (8-R layer)"],
                    ["api_key_printed", False, "key value never printed"],
                    ["api_key_written_to_disk", False, "key value never written to any artifact"],
                    ["committed_artifacts_contain_key_query_param", marker_found,
                     "leak scan over %d committed files" % scanned],
                    ["secret_leak_scan_clean", leak_clean, "no key value or key query param"],
                    ["raw_normalized_gitignored", True,
                     "research/data/eodhd/{raw,normalized}/ force-gitignored before write"]])

        report = {
            "phase": PHASE,
            "objective": ("Autonomously acquire EODHD earnings/fundamentals for the priced research "
                          "universe, build a point-in-time panel, join price/macro/sector, test all "
                          "relevant cross-sectional earnings scenarios with proper validation, and "
                          "produce one terminal alpha decision. Preview-only; no orders."),
            "provider": PROVIDER_NAME, "api_key_env_var": API_KEY_ENV,
            "phase8r_decision": phase8r_decision,
            "eodhd_accepted_as_core_provider": phase8r_decision == "EODHD_ACCEPT_AS_CORE_PROVIDER",
            "eodhd_key_present": present, "api_key_logged": False,
            "mode": ("live_acquire_and_score" if acq.get("executed") and transport is None
                     else ("test_transport" if transport is not None else "offline_score_cached")),
            "as_of": as_of,
            "bounded_limits": {"max_tickers": max_tickers, "max_requests": max_requests,
                               "checkpoint_every": CHECKPOINT_EVERY},
            "universe_priced_tickers": len(universe),
            "universe_targeted_for_acquisition": n_target,
            "acquisition": {"executed": acq.get("executed"), "requests_made": acq.get("requests_made"),
                            "tickers_acquired_this_run": acq.get("acquired"),
                            "stopped_early": acq.get("stopped"), "stop_reason": acq.get("stop_reason")},
            "eodhd_coverage": cov,
            "point_in_time": {"events_raw": stats.get("events_raw", 0),
                              "events_with_realized_eps": stats.get("events_with_realized_eps", 0),
                              "usable_events": stats.get("events_usable", 0),
                              "scoreable_tickers": stats.get("tickers_usable", 0),
                              "date_min": stats.get("date_min", ""), "date_max": stats.get("date_max", ""),
                              "sectors": stats.get("sectors", 0)},
            "scenarios_tested": len(results),
            "scenarios": [{"scenario": r["scenario"], "mean_ic": _round(r.get("mean_ic")),
                           "ic_t": _round(r.get("ic_t"), 2), "ic_p": _round(r.get("ic_p")),
                           "bh_significant": r.get("bh_significant", False),
                           "mean_spread": _round(r.get("mean_spread")),
                           "promoted": r in promoted} for r in results],
            "best_scenario": (best or {}).get("scenario") if best else None,
            "promoted_signals": [p["scenario"] for p in promoted],
            "rejected_signals": [r["scenario"] for r in rejected],
            "alpha_found": decision == DEC_ALPHA,
            "decision": decision, "decision_is_terminal": decision in ALLOWED_DECISIONS,
            "allowed_decisions": list(ALLOWED_DECISIONS), "decision_rationale": rationale,
            "evidence_gates": {"min_abs_ic": GATE_MIN_ABS_IC, "min_abs_t": GATE_MIN_ABS_T,
                               "bh_q": GATE_BH_Q, "min_spread_hit_rate": GATE_MIN_SPREAD_HITRATE,
                               "cost_bps": GATE_COST_BPS, "max_sector_share": GATE_MAX_SECTOR_SHARE,
                               "min_events": MIN_EVENTS, "min_months": MIN_MONTHS},
            "blockers": acq.get("blocker") or "",
            "recommended_next_command": next_cmd,
            "secret_safety_leak_scan_clean": leak_clean, "secret_safety_files_scanned": scanned,
            "raw_dir_gitignored": _rel(P.eodhd_dir),
            "outputs": {k: _rel(getattr(P, k)) for k in _ARTIFACTS},
            # safety / provenance contract
            "preview_only": True, "orders_enabled": False, "automation_enabled": False,
            "broker_execution_enabled": False, "production_replacement": False,
            "network_used": bool(acq.get("executed") and transport is None),
            "paper_trader_touched": False, "gcp_touched": False, "deployed": False,
            "data_fabricated": False, "no_alpha_fabricated": True,
            "raw_paid_data_in_artifacts": False, "wrote_to_d_drive": False, "committed": False,
        }
        _write_json(P.report, report)
        if verbose:
            _print_summary(report)
        return report
    except Exception as exc:  # pragma: no cover - defensive; never leaks a key
        report = {"phase": PHASE, "decision": DEC_ERROR, "decision_is_terminal": True,
                  "allowed_decisions": list(ALLOWED_DECISIONS),
                  "error": "%s: %s" % (type(exc).__name__, exc), "api_key_logged": False,
                  "committed": False, "preview_only": True, "orders_enabled": False,
                  "automation_enabled": False, "paper_trader_touched": False, "gcp_touched": False}
        try:
            _write_json(P.report, report)
            _write_csv(P.run_log, ["step", "status", "detail", "count"], log.rows)
        except Exception:
            pass
        if verbose:
            print("ERROR: %s" % report["error"])
        return report


def _next_step(decision: str, max_tickers: int, max_requests: int) -> Tuple[str, str]:
    if decision == DEC_ALPHA:
        return ("Review research/output/phase8s_autonomous_eodhd_alpha_factory/"
                "promoted_alpha_signals.csv, then surface the promoted signal(s) as PREVIEW-ONLY "
                "ranking ideas in the Paper Trader daily-review cockpit (manual review; no orders).",
                "Promote validated EODHD earnings signal(s) to preview-only Paper Trader review")
    if decision == DEC_MORE:
        return ("$env:EODHD_API_KEY = '<PAID_EODHD_KEY>'; python "
                "research/run_phase8s_autonomous_eodhd_alpha_factory.py --live --max-tickers %d "
                "--max-requests %d   (acquire the rest of the priced universe, then re-score)"
                % (max(max_tickers, 500), max(max_requests, 5000)),
                "Acquire more EODHD coverage and re-score the broader cross-section")
    if decision == DEC_BLOCKER:
        return ("Resolve the blocker (set a valid PAID EODHD_API_KEY / restore price cache), then "
                "re-run: python research/run_phase8s_autonomous_eodhd_alpha_factory.py --live",
                "Clear the hard blocker, then re-run the alpha factory")
    if decision == DEC_NO_ALPHA:
        return ("python research/run_phase8t_alt_signal_families.py   (the EODHD earnings/"
                "fundamentals families showed no promotable alpha; explore a different data family)",
                "No EODHD alpha; pivot to a different signal family")
    return "python research/run_phase8s_autonomous_eodhd_alpha_factory.py", "Re-run the alpha factory"


def _print_summary(report: Dict) -> None:
    print("phase:                 %s" % report.get("phase"))
    print("mode:                  %s" % report.get("mode"))
    print("eodhd key present:     %s" % report.get("eodhd_key_present"))
    acq = report.get("acquisition", {})
    print("requests made:         %s" % acq.get("requests_made"))
    print("tickers acquired:      %s" % acq.get("tickers_acquired_this_run"))
    pit = report.get("point_in_time", {})
    print("usable events:         %s" % pit.get("usable_events"))
    print("scoreable tickers:     %s" % pit.get("scoreable_tickers"))
    print("scenarios tested:      %s" % report.get("scenarios_tested"))
    print("best scenario:         %s" % report.get("best_scenario"))
    print("promoted signals:      %s" % report.get("promoted_signals"))
    print("alpha found:           %s" % report.get("alpha_found"))
    print("decision:              %s" % report.get("decision"))
    print("next command:          %s" % report.get("recommended_next_command"))


def _parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        description=("Phase 8-S autonomous EODHD earnings/fundamentals alpha factory (offline scoring "
                     "on cached data by default; bounded live acquisition with --live + EODHD_API_KEY)."))
    ap.add_argument("--live", action="store_true",
                    help="Run bounded live EODHD acquisition before scoring (requires EODHD_API_KEY).")
    ap.add_argument("--max-tickers", type=int, default=DEFAULT_MAX_TICKERS,
                    help="Max distinct new tickers to acquire this run (default 500).")
    ap.add_argument("--max-requests", type=int, default=DEFAULT_MAX_REQUESTS,
                    help="Hard cap on live EODHD requests this run (default 5000).")
    ap.add_argument("--as-of", default=DEFAULT_AS_OF,
                    help="Score only events announced on/before this date (default %s)." % DEFAULT_AS_OF)
    return ap.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parse_args(argv)
    run(live=args.live, max_tickers=args.max_tickers, max_requests=args.max_requests,
        as_of=args.as_of, verbose=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
