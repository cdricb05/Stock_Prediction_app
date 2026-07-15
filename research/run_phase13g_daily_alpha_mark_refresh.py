"""Phase 13-G (Part B) - Daily Alpha EOD Mark Refresh (live read-only EODHD).

WHAT THIS IS
    A MANUAL, user-triggered daily mark refresh for the champion composite_sn paper
    books. Every run fetches the latest COMPLETED end-of-day adjusted closes from the
    user's OWNED EODHD entitlement for the union of the CURRENT_CHAMPION Top-50 names
    + SPY (+ the S&P500 shadow Top-50 if the Phase 13-G Part A audit packaged one),
    recomputes paper PnL vs the frozen 13-A book entry prices, compares each book to
    SPY, and persists a dated price-mark artifact OUTSIDE both git repositories.

    IT DOES NOT TRADE. No orders, no broker, no automation, no scheduling, no live
    trading. It is a read-only market-data refresh + PnL computation only.

SAFETY / SECRET DISCIPLINE
    Reuses the existing Phase 8-U/8-R EODHD client (u8._eod_live_get): allow-listed
    host, ``.US`` ticker convention, EODHD_API_KEY read only from the environment and
    passed only as the api_token query param, URLs redacted, errors sanitized to a
    status code. The key is NEVER printed and NEVER persisted. Live read-only EODHD
    calls are explicitly authorized for this phase.

LATEST-PRICE RULE (completed EOD only)
    The latest COMPLETED session is used - a bar dated on the current calendar day is
    treated as potentially incomplete and is NOT used as a completed EOD mark. The
    reference "today" is injectable (--today) for deterministic tests.

OUTPUT (atomic; OUTSIDE git; env PAPER_TRADER_CURRENT_ALPHA_DAILY_MARK_DIR override)
    D:/Stock_Prediction_app_data/phase13g_daily_alpha_marks/
        latest/{daily_alpha_marks.json, daily_alpha_marks.csv, book_summaries.json,
                benchmark_summary.json, refresh_manifest.json, last_run_status.json}
        history/YYYY-MM-DD/{... same five financial-mark files ...}
    A repeated run on the same completed EOD mark date does NOT create another history
    directory or a duplicate financial observation (NO_NEW_MARK_DATE).

TWO DISTINCT CONCEPTS (this is a process-boundary contract, not cosmetic)
    * LATEST VALID FINANCIAL MARK = the five financial-mark files above. They are only
      written/advanced when a genuinely NEW completed EOD mark is acquired. A
      NO_NEW_MARK_DATE run and every BLOCKED_* run PRESERVE them unchanged.
    * CURRENT RUN STATUS = latest/last_run_status.json. It is (re)written on EVERY
      completed runner outcome (success, no-new, or blocked) and records what THIS run
      did. A downstream consumer that launches the runner as a subprocess MUST read
      last_run_status.json (not refresh_manifest.json) to learn the current run result,
      because the runner's Python return value cannot cross the process boundary.

REFRESH RESULT ENUM
    REFRESH_OK_NEW_MARK_DATE | NO_NEW_MARK_DATE | PARTIAL_COVERAGE | INSUFFICIENT_COVERAGE
    | BLOCKED_EODHD_KEY | BLOCKED_EODHD_ENTITLEMENT | BLOCKED_EODHD_RATE_LIMIT
    | BLOCKED_PROVIDER_ERROR | BLOCKED_SCHEMA_ERROR
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

PHASE = "13-G"
PHASE_NAME = "Daily Alpha EOD Mark Refresh"
STEM = "phase13g_daily_alpha_mark_refresh"

ALPHA_NAME = "composite_sn"
BENCHMARK_TICKER = "SPY"
EODHD_KEY_ENV = "EODHD_API_KEY"
DEFAULT_FETCH_START = "2026-01-01"   # compact series covering the 2026-05-22 book entry + latest

# --- inputs (owned / local) ------------------------------------------------- #
_DEFAULT_PACKAGE_DIR = (_REPO_ROOT / "research" / "output"
                        / "phase13a_current_champion_alpha_paper_test_package")
_DEFAULT_AUDIT_DIR = (_REPO_ROOT / "research" / "output"
                      / "phase13g_current_alpha_universe_integrity_audit")

# --- output (OUTSIDE git; dynamic data) ------------------------------------- #
DAILY_MARK_DIR_ENV = "PAPER_TRADER_CURRENT_ALPHA_DAILY_MARK_DIR"
_DEFAULT_DAILY_MARK_DIR = Path("D:/Stock_Prediction_app_data/phase13g_daily_alpha_marks")

# --- coverage thresholds ---------------------------------------------------- #
COV_FULL = "FULL_COVERAGE"
COV_PARTIAL = "PARTIAL_COVERAGE_WARNING"
COV_INSUFFICIENT = "INSUFFICIENT_COVERAGE_REJECT"
_PARTIAL_MIN = 90.0   # >= 90% and < 100% -> warning; < 90% -> reject

# --- refresh result enum ---------------------------------------------------- #
RES_OK = "REFRESH_OK_NEW_MARK_DATE"
RES_NO_NEW = "NO_NEW_MARK_DATE"
RES_PARTIAL = "PARTIAL_COVERAGE"
RES_INSUFFICIENT = "INSUFFICIENT_COVERAGE"
RES_BLOCKED_KEY = "BLOCKED_EODHD_KEY"
RES_BLOCKED_ENTITLEMENT = "BLOCKED_EODHD_ENTITLEMENT"
RES_BLOCKED_RATE = "BLOCKED_EODHD_RATE_LIMIT"
RES_BLOCKED_PROVIDER = "BLOCKED_PROVIDER_ERROR"
RES_BLOCKED_SCHEMA = "BLOCKED_SCHEMA_ERROR"
_BLOCKED = {RES_BLOCKED_KEY, RES_BLOCKED_ENTITLEMENT, RES_BLOCKED_RATE,
            RES_BLOCKED_PROVIDER, RES_BLOCKED_SCHEMA}

# --- current-run status artifact (distinct from the financial-mark artifacts) --- #
RUN_STATUS_FILE = "last_run_status.json"

_MARKS_CSV_HEADER = [
    "ticker", "alpha_name", "signal_date", "source_rank", "in_top25", "in_top50",
    "entry_reference_date", "entry_price", "latest_completed_eod_date",
    "latest_adjusted_close", "paper_return_pct", "price_source", "price_status",
    "acquisition_status",
]

PRICE_SOURCE = "EODHD_LIVE_EOD(adjusted_close)"


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
def _to_float(x) -> Optional[float]:
    if x is None:
        return None
    s = str(x).strip()
    if not s:
        return None
    try:
        v = float(s)
    except (TypeError, ValueError):
        return None
    return None if v != v else v  # drop NaN


def _round(x, nd: int = 4) -> Optional[float]:
    return round(x, nd) if isinstance(x, (int, float)) else None


def _median(vals: List[float]) -> Optional[float]:
    if not vals:
        return None
    s = sorted(vals)
    n = len(s)
    mid = n // 2
    return s[mid] if n % 2 else (s[mid - 1] + s[mid]) / 2.0


def _parse_date(x) -> Optional[date]:
    if not x:
        return None
    try:
        return date.fromisoformat(str(x)[:10])
    except (TypeError, ValueError):
        return None


def _today(today: Optional[str]) -> date:
    d = _parse_date(today)
    return d if d else datetime.now(timezone.utc).date()


def _normalize_bars(payload: Any) -> List[Tuple[str, float]]:
    """Flatten an EODHD EOD payload (list of daily bars) into sorted [(date, adj_close)].
    Adjusted close is preferred, falling back to close - the same rule as u8._normalize_eod."""
    out: List[Tuple[str, float]] = []
    if not isinstance(payload, list):
        return out
    for bar in payload:
        if not isinstance(bar, dict):
            continue
        d = str(bar.get("date") or "").strip()
        if not d:
            continue
        ac = bar.get("adjusted_close")
        if ac is None:
            ac = bar.get("close")
        acf = _to_float(ac)
        if acf is None:
            continue
        out.append((d[:10], acf))
    out.sort(key=lambda t: t[0])
    return out


def _completed_bars(bars: List[Tuple[str, float]], today: date) -> List[Tuple[str, float]]:
    """Only bars strictly before the reference calendar day are treated as completed
    EOD sessions (a same-day bar may be an incomplete intraday snapshot)."""
    return [b for b in bars if _parse_date(b[0]) is not None and _parse_date(b[0]) < today]


def _price_at_or_before(bars: List[Tuple[str, float]], as_of: str) -> Optional[Tuple[str, float]]:
    """Last bar with date <= as_of (mirrors the 13-A entry-price rule)."""
    target = _parse_date(as_of)
    if target is None:
        return None
    chosen = None
    for d, v in bars:  # bars sorted ascending
        pd = _parse_date(d)
        if pd is not None and pd <= target:
            chosen = (d, v)
        else:
            break
    return chosen


def _clean_symbol(ticker: str) -> str:
    """EODHD class-share symbols use a dash, not a dot (e.g. BRK.B -> BRK-B)."""
    return ticker.strip().upper().replace(".", "-")


# --------------------------------------------------------------------------- #
# A. Source universe (13-A package Top-50 + SPY + optional shadow Top-50).
# --------------------------------------------------------------------------- #
def _read_csv_rows(path: Path) -> List[Dict[str, str]]:
    if not path.is_file():
        return []
    with open(path, "r", encoding="utf-8", newline="") as fh:
        return [dict(r) for r in csv.DictReader(fh)]


def load_source_universe(package_dir: Path, audit_dir: Optional[Path]
                         ) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Build the per-ticker book-membership + frozen entry table from the 13-A package
    Top-50 portfolio (the Top-25 is its first 25 rows), plus SPY, plus (if present) the
    Phase 13-G Part A S&P500 shadow Top-50 names. Returns (positions, meta)."""
    pkg = _read_json(package_dir / "phase13a_current_champion_alpha_paper_test_package.json")
    signal_date = str((pkg or {}).get("signal_date") or "")
    top50_rows = _read_csv_rows(package_dir / "current_alpha_paper_portfolio_top50.csv")
    top50_rows = [r for r in top50_rows if str(r.get("ticker", "")).strip()
                  and not str(r.get("ticker", "")).strip().startswith("_")]

    positions: List[Dict[str, Any]] = []
    seen: List[str] = []
    for i, r in enumerate(top50_rows):
        tk = str(r.get("ticker", "")).strip()
        if tk in seen:
            continue
        seen.append(tk)
        positions.append({
            "ticker": tk,
            "alpha_name": ALPHA_NAME,
            "signal_date": signal_date,
            "source_rank": i + 1,
            "in_top25": i < 25,
            "in_top50": i < 50,
            "frozen_entry_price": _to_float(r.get("entry_price")),
            "frozen_entry_reference_date": (str(r.get("entry_reference_date", "")).strip() or None),
            "in_shadow_top50": False,
        })

    shadow_top50: List[str] = []
    if audit_dir is not None:
        audit = _read_json(Path(audit_dir) / "phase13g_current_alpha_universe_integrity_audit.json")
        books = (audit or {}).get("sp500_shadow_books") or {}
        shadow_top50 = [str(t).strip() for t in (books.get("shadow_top50_tickers") or [])]
    shadow_set = set(shadow_top50)
    by_ticker = {p["ticker"]: p for p in positions}
    for p in positions:
        if p["ticker"] in shadow_set:
            p["in_shadow_top50"] = True
    for tk in shadow_top50:
        if tk not in by_ticker:
            positions.append({
                "ticker": tk, "alpha_name": ALPHA_NAME, "signal_date": signal_date,
                "source_rank": None, "in_top25": False, "in_top50": False,
                "frozen_entry_price": None, "frozen_entry_reference_date": None,
                "in_shadow_top50": True,
            })

    meta = {
        "signal_date": signal_date,
        "n_top50": sum(1 for p in positions if p["in_top50"]),
        "n_shadow_top50": len(shadow_top50),
        "shadow_included": bool(shadow_top50),
        "benchmark": BENCHMARK_TICKER,
    }
    return positions, meta


def _read_json(path: Path) -> Optional[dict]:
    if not Path(path).is_file():
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


# --------------------------------------------------------------------------- #
# B. Live (or injected) EODHD acquisition.
# --------------------------------------------------------------------------- #
# A transport maps (symbol, start_date) -> payload (list of EOD bars). The live
# transport reuses the Phase 8-U client; tests inject a fake one (no network, no key).
Transport = Callable[[str, str], Any]


def live_transport() -> Transport:
    """Reuse the existing Phase 8-U EODHD client (allow-listed host, ``.US`` suffix,
    redacted key). Imported lazily so offline tests never load the network client."""
    from research import run_phase8u_eodhd_price_universe_expansion as u8
    return u8._eod_live_get


class _AcqError(Exception):
    def __init__(self, result_enum: str, message: str):
        super().__init__(message)
        self.result_enum = result_enum


def _classify_provider_error(exc: Exception) -> str:
    etype = getattr(exc, "error_type", "") or ""
    if etype == "invalid_key":
        return RES_BLOCKED_KEY
    if etype == "plan_blocked":
        return RES_BLOCKED_ENTITLEMENT
    if etype == "rate_limited":
        return RES_BLOCKED_RATE
    if etype == "bad_response":
        return RES_BLOCKED_SCHEMA
    return RES_BLOCKED_PROVIDER


def _fetch_one(transport: Transport, ticker: str, start: str) -> Tuple[List[Tuple[str, float]], str]:
    """Fetch + normalize one ticker. Returns (sorted_bars, acquisition_status).
    Raises _AcqError only for FATAL, run-stopping provider states (bad key / plan /
    rate limit); a per-ticker empty/error is returned as a status, not raised."""
    try:
        payload = transport(_clean_symbol(ticker), start)
    except Exception as exc:  # noqa: BLE001 - sanitized taxonomy below
        enum = _classify_provider_error(exc)
        if enum in (RES_BLOCKED_KEY, RES_BLOCKED_ENTITLEMENT, RES_BLOCKED_RATE):
            raise _AcqError(enum, "provider stop: %s" % getattr(exc, "error_type", "error"))
        return [], "ERROR"
    bars = _normalize_bars(payload)
    if not isinstance(payload, list):
        return [], "SCHEMA_ERROR"
    return bars, ("OK" if bars else "EMPTY")


def probe_entitlement(transport: Transport, candidates: Sequence[str], start: str,
                      today: date, log: _Log) -> Optional[str]:
    """Bounded entitlement probe: SPY + up to 3 candidate names. Validate schema +
    a completed-EOD date is present. Returns a BLOCKED_* enum on failure, else None."""
    probe = [BENCHMARK_TICKER] + [c for c in candidates[:3]]
    spy_ok = False
    any_completed = False
    for tk in probe:
        try:
            bars, status = _fetch_one(transport, tk, start)
        except _AcqError as e:
            log.step("probe", "BLOCKED", "%s on %s" % (e.result_enum, tk))
            return e.result_enum
        if status == "SCHEMA_ERROR":
            log.step("probe", "BLOCKED", "schema error on %s" % tk)
            return RES_BLOCKED_SCHEMA
        completed = _completed_bars(bars, today)
        if tk == BENCHMARK_TICKER and completed:
            spy_ok = True
        if completed:
            any_completed = True
    if not spy_ok:
        log.step("probe", "BLOCKED", "no completed SPY EOD bar")
        return RES_BLOCKED_SCHEMA
    if not any_completed:
        log.step("probe", "BLOCKED", "no completed EOD bar in probe set")
        return RES_BLOCKED_SCHEMA
    log.step("probe", "OK", "SPY + candidates schema-valid, completed EOD present")
    return None


# --------------------------------------------------------------------------- #
# C. Mark each ticker (entry vs latest completed EOD).
# --------------------------------------------------------------------------- #
def mark_ticker(pos: Dict[str, Any], bars: List[Tuple[str, float]], acq_status: str,
                today: date) -> Dict[str, Any]:
    signal_date = pos.get("signal_date") or ""
    completed = _completed_bars(bars, today)
    latest = completed[-1] if completed else None
    # entry: prefer the frozen 13-A book entry; else compute from the fresh series.
    entry_price = pos.get("frozen_entry_price")
    entry_ref = pos.get("frozen_entry_reference_date")
    if entry_price is None:
        at = _price_at_or_before(bars, signal_date)
        if at is not None:
            entry_ref, entry_price = at[0], at[1]
    latest_date = latest[0] if latest else None
    latest_close = latest[1] if latest else None
    ret = None
    if entry_price is not None and latest_close is not None and entry_price != 0:
        ret = (latest_close / entry_price - 1.0) * 100.0
    if entry_price is None:
        status = "NO_ENTRY_PRICE"
    elif latest_close is None:
        status = "NO_LATEST_PRICE"
    else:
        status = "MARKED"
    return {
        "ticker": pos["ticker"],
        "alpha_name": pos["alpha_name"],
        "signal_date": signal_date,
        "source_rank": pos.get("source_rank"),
        "in_top25": pos.get("in_top25", False),
        "in_top50": pos.get("in_top50", False),
        "in_shadow_top50": pos.get("in_shadow_top50", False),
        "entry_reference_date": entry_ref,
        "entry_price": _round(entry_price, 6),
        "latest_completed_eod_date": latest_date,
        "latest_adjusted_close": _round(latest_close, 6),
        "paper_return_pct": _round(ret, 4),
        "price_source": PRICE_SOURCE,
        "price_status": status,
        "acquisition_status": acq_status,
    }


def _coverage_status(covered: int, total: int) -> str:
    if total <= 0:
        return COV_INSUFFICIENT
    pct = 100.0 * covered / total
    if pct >= 100.0:
        return COV_FULL
    if pct >= _PARTIAL_MIN:
        return COV_PARTIAL
    return COV_INSUFFICIENT


# --------------------------------------------------------------------------- #
# D. Book + benchmark summaries.
# --------------------------------------------------------------------------- #
def _book_summary(marks: List[Dict[str, Any]], book_size: int, mark_date: Optional[str],
                  signal_date: str, spy_return: Optional[float],
                  prev: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    members = [mm for mm in marks if mm.get("in_top25")] if book_size == 25 else \
              [mm for mm in marks if mm.get("in_top50")]
    covered = [mm for mm in members if mm["price_status"] == "MARKED"
               and mm["paper_return_pct"] is not None]
    rets = [mm["paper_return_pct"] for mm in covered]
    total = len(members)
    cov_n = len(covered)
    avg = (sum(rets) / len(rets)) if rets else None
    n_up = sum(1 for x in rets if x > 0)
    ranked = sorted(covered, key=lambda mm: mm["paper_return_pct"], reverse=True)

    def _slim(items):
        return [{"ticker": mm["ticker"], "paper_return_pct": mm["paper_return_pct"]} for mm in items]

    cov_status = _coverage_status(cov_n, total)
    prev_mark = (prev or {}).get("mark_date")
    prev_avg = (prev or {}).get("average_return_pct")
    change = (_round(avg - prev_avg, 4)
              if (avg is not None and isinstance(prev_avg, (int, float))) else None)
    excess = (_round(avg - spy_return, 4)
              if (avg is not None and spy_return is not None) else None)
    return {
        "book_id": "%s__%s__top%d" % (ALPHA_NAME, signal_date, book_size),
        "book_size": book_size,
        "mark_date": mark_date,
        "covered_count": cov_n,
        "missing_count": total - cov_n,
        "total_count": total,
        "coverage_pct": _round(100.0 * cov_n / total, 2) if total else None,
        "coverage_status": cov_status,
        "pnl_claim_valid": cov_status != COV_INSUFFICIENT,
        "average_return_pct": _round(avg, 4),
        "median_return_pct": _round(_median(rets), 4) if rets else None,
        "hit_rate_pct": _round(100.0 * n_up / len(rets), 2) if rets else None,
        "best_5": _slim(ranked[:5]),
        "worst_5": _slim(list(reversed(ranked))[:5]),
        "previous_mark_date": prev_mark,
        "previous_average_return_pct": prev_avg,
        "change_since_previous_mark_pct_points": change,
        "benchmark_return_pct": _round(spy_return, 4) if spy_return is not None else None,
        "excess_return_vs_spy_pct_points": excess,
        "order_action_all": "NO_ORDER",
    }


def _benchmark_summary(spy_mark: Optional[Dict[str, Any]], signal_date: str) -> Dict[str, Any]:
    if not spy_mark:
        return {"ticker": BENCHMARK_TICKER, "available": False,
                "reference_date": signal_date, "reference_price": None,
                "latest_completed_eod_date": None, "latest_adjusted_close": None,
                "return_since_signal_pct": None}
    return {
        "ticker": BENCHMARK_TICKER,
        "available": spy_mark["price_status"] == "MARKED",
        "reference_date": spy_mark.get("entry_reference_date") or signal_date,
        "reference_price": spy_mark.get("entry_price"),
        "latest_completed_eod_date": spy_mark.get("latest_completed_eod_date"),
        "latest_adjusted_close": spy_mark.get("latest_adjusted_close"),
        "return_since_signal_pct": spy_mark.get("paper_return_pct"),
    }


# --------------------------------------------------------------------------- #
# E. Atomic artifact IO + dedup by mark date.
# --------------------------------------------------------------------------- #
def _atomic_write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(obj, fh, indent=2, default=str)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def _atomic_write_csv(path: Path, header: Sequence[str], rows) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(list(header))
            for row in rows:
                w.writerow(["" if v is None else v for v in row])
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def _read_prev_manifest(mark_dir: Path) -> Optional[dict]:
    return _read_json(mark_dir / "latest" / "refresh_manifest.json")


def _write_artifact_set(dst: Path, marks_payload: dict, marks_rows: list,
                        book_summaries: dict, benchmark: dict, manifest: dict) -> None:
    _atomic_write_json(dst / "daily_alpha_marks.json", marks_payload)
    _atomic_write_csv(dst / "daily_alpha_marks.csv", _MARKS_CSV_HEADER, marks_rows)
    _atomic_write_json(dst / "book_summaries.json", book_summaries)
    _atomic_write_json(dst / "benchmark_summary.json", benchmark)
    _atomic_write_json(dst / "refresh_manifest.json", manifest)


def _latest_valid_mark(mark_dir: Path) -> Tuple[bool, Optional[str]]:
    """Inspect the persisted latest/refresh_manifest.json and return (available, date)
    for the latest VALID financial mark. A blocked/never-marked manifest (no mark_date)
    is not a valid mark. This is distinct from the current run's status."""
    prev = _read_prev_manifest(mark_dir)
    if not isinstance(prev, dict) or prev.get("blocked"):
        return (False, None)
    md = prev.get("mark_date")
    return (True, md) if md else (False, None)


def _build_run_status(*, refresh_result: str, run_at: str, ref_today: date,
                      mark_date_observed: Optional[str],
                      previous_valid_mark_date: Optional[str], new_mark_date: bool,
                      blocked: bool, blocked_message: Optional[str],
                      latest_valid_mark_available: bool,
                      latest_valid_mark_date: Optional[str]) -> Dict[str, Any]:
    """Build the CURRENT RUN STATUS record (latest/last_run_status.json).

    This describes what THIS run did; it is NOT the latest valid financial mark. It is
    the authoritative cross-process signal a subprocess consumer reads to decide whether
    a fresh paper snapshot is warranted."""
    return {
        "phase": PHASE, "phase_part": "B", "artifact": "current_run_status",
        "refresh_result": refresh_result,
        "last_run_at": run_at,
        "reference_today": ref_today.isoformat(),
        "mark_date_observed": mark_date_observed,
        "previous_valid_mark_date": previous_valid_mark_date,
        "new_mark_date": bool(new_mark_date),
        "blocked": bool(blocked),
        "blocked_message": blocked_message,
        "latest_valid_mark_available": bool(latest_valid_mark_available),
        "latest_valid_mark_date": latest_valid_mark_date,
        "price_source": PRICE_SOURCE,
        # --- safety (this run traded nothing) ---
        "creates_orders": False,
        "creates_automation": False,
        "creates_broker_connection": False,
        "wrote_to_paper_trader": False,
        "live_trading": False,
        "order_action_all": "NO_ORDER",
    }


def _write_run_status(mark_dir: Path, status: Dict[str, Any]) -> None:
    """Atomically persist the CURRENT RUN STATUS. Written on EVERY completed outcome so
    the result of this run survives the subprocess boundary; it never touches the
    financial-mark artifacts."""
    _atomic_write_json(mark_dir / "latest" / RUN_STATUS_FILE, status)


# --------------------------------------------------------------------------- #
# F. Orchestration.
# --------------------------------------------------------------------------- #
def refresh(package_dir: Path, audit_dir: Optional[Path], mark_dir: Path, *,
            transport: Optional[Transport] = None, today: Optional[str] = None,
            start: str = DEFAULT_FETCH_START, log: Optional[_Log] = None) -> Dict[str, Any]:
    log = log or _Log()
    ref_today = _today(today)
    run_at = datetime.now(timezone.utc).isoformat()

    positions, uni_meta = load_source_universe(package_dir, audit_dir)
    signal_date = uni_meta["signal_date"]
    universe = [BENCHMARK_TICKER] + [p["ticker"] for p in positions]
    # de-dup preserving order
    seen: List[str] = []
    for t in universe:
        if t not in seen:
            seen.append(t)
    universe = seen
    log.step("universe", "DONE", "union built", tickers=len(universe),
             shadow=uni_meta["shadow_included"])

    tr = transport if transport is not None else None
    key_present = bool(os.environ.get(EODHD_KEY_ENV))
    if tr is None:
        if not key_present:
            return _blocked_manifest(RES_BLOCKED_KEY, "EODHD_API_KEY is not set", mark_dir,
                                     run_at, uni_meta, ref_today, log)
        tr = live_transport()

    # --- bounded entitlement probe (SPY + 3) --------------------------------
    candidates = [p["ticker"] for p in positions[:3]]
    block = probe_entitlement(tr, candidates, start, ref_today, log)
    if block is not None:
        return _blocked_manifest(block, "entitlement probe failed", mark_dir, run_at,
                                 uni_meta, ref_today, log)

    # --- full acquisition ---------------------------------------------------
    series: Dict[str, List[Tuple[str, float]]] = {}
    acq: Dict[str, str] = {}
    for tk in universe:
        try:
            bars, status = _fetch_one(tr, tk, start)
        except _AcqError as e:
            log.step("acquire", "BLOCKED", "%s on %s" % (e.result_enum, tk))
            return _blocked_manifest(e.result_enum, "provider stop during acquisition",
                                     mark_dir, run_at, uni_meta, ref_today, log)
        series[tk] = bars
        acq[tk] = status
    log.step("acquire", "DONE", "fetched", tickers=len(universe),
             ok=sum(1 for v in acq.values() if v == "OK"))

    # --- mark each position + SPY ------------------------------------------
    marks = [mark_ticker(p, series.get(p["ticker"], []), acq.get(p["ticker"], "EMPTY"), ref_today)
             for p in positions]
    spy_pos = {"ticker": BENCHMARK_TICKER, "alpha_name": ALPHA_NAME, "signal_date": signal_date,
               "source_rank": None, "in_top25": False, "in_top50": False, "in_shadow_top50": False,
               "frozen_entry_price": None, "frozen_entry_reference_date": None}
    spy_mark = mark_ticker(spy_pos, series.get(BENCHMARK_TICKER, []),
                           acq.get(BENCHMARK_TICKER, "EMPTY"), ref_today)

    # --- mark date = the SPY session (fallback: max completed across names) ---
    mark_date = spy_mark.get("latest_completed_eod_date")
    if not mark_date:
        dates = [mm["latest_completed_eod_date"] for mm in marks
                 if mm.get("latest_completed_eod_date")]
        mark_date = max(dates) if dates else None

    spy_return = spy_mark.get("paper_return_pct") if spy_mark.get("price_status") == "MARKED" else None

    # --- dedup: same completed mark date -> NO_NEW_MARK_DATE ----------------
    prev_manifest = _read_prev_manifest(mark_dir)
    prev_mark_date = (prev_manifest or {}).get("mark_date")
    prev_books = {b.get("book_size"): b for b in (prev_manifest or {}).get("book_summaries_preview", [])}
    if mark_date is not None and prev_mark_date == mark_date:
        log.step("dedup", "SKIP", "mark date %s unchanged -> NO_NEW_MARK_DATE" % mark_date)
        # Persist the CURRENT RUN STATUS (NO_NEW); do NOT touch the financial-mark files.
        status = _build_run_status(
            refresh_result=RES_NO_NEW, run_at=run_at, ref_today=ref_today,
            mark_date_observed=mark_date, previous_valid_mark_date=prev_mark_date,
            new_mark_date=False, blocked=False, blocked_message=None,
            latest_valid_mark_available=True, latest_valid_mark_date=prev_mark_date)
        _write_run_status(mark_dir, status)
        # The returned value is informational only (a NO_NEW echo of the preserved
        # financial mark); the durable current-run signal is last_run_status.json.
        manifest = _read_json(mark_dir / "latest" / "refresh_manifest.json") or {}
        manifest = dict(manifest)
        manifest["refresh_result"] = RES_NO_NEW
        manifest["new_mark_date"] = False
        manifest["last_probe_run_at"] = run_at
        return manifest

    # --- book summaries -----------------------------------------------------
    prev25 = {"mark_date": prev_mark_date, "average_return_pct":
              (prev_books.get(25) or {}).get("average_return_pct")}
    prev50 = {"mark_date": prev_mark_date, "average_return_pct":
              (prev_books.get(50) or {}).get("average_return_pct")}
    book25 = _book_summary(marks, 25, mark_date, signal_date, spy_return, prev25)
    book50 = _book_summary(marks, 50, mark_date, signal_date, spy_return, prev50)
    benchmark = _benchmark_summary(spy_mark, signal_date)

    worst_cov = min(book25["coverage_status"], book50["coverage_status"],
                    key=lambda s: {COV_FULL: 2, COV_PARTIAL: 1, COV_INSUFFICIENT: 0}[s])
    if worst_cov == COV_FULL:
        refresh_result = RES_OK
    elif worst_cov == COV_PARTIAL:
        refresh_result = RES_PARTIAL
    else:
        refresh_result = RES_INSUFFICIENT

    marks_rows = [[mm[c] for c in _MARKS_CSV_HEADER] for mm in marks]
    marks_payload = {
        "phase": PHASE, "alpha_name": ALPHA_NAME, "signal_date": signal_date,
        "mark_date": mark_date, "reference_today": ref_today.isoformat(),
        "price_source": PRICE_SOURCE, "n_marks": len(marks),
        "marks": marks, "benchmark": benchmark, "run_at": run_at,
    }
    book_summaries = {"top25": book25, "top50": book50, "mark_date": mark_date,
                      "benchmark": benchmark}
    manifest = _build_manifest(refresh_result, mark_date, prev_mark_date, signal_date,
                               uni_meta, marks, book25, book50, benchmark, run_at, ref_today)

    # --- atomic writes: latest/ + history/<mark_date>/ ----------------------
    _write_artifact_set(mark_dir / "latest", marks_payload, marks_rows, book_summaries,
                        benchmark, manifest)
    if mark_date:
        hist = mark_dir / "history" / mark_date
        if not (hist / "refresh_manifest.json").is_file():
            _write_artifact_set(hist, marks_payload, marks_rows, book_summaries,
                                benchmark, manifest)
    # CURRENT RUN STATUS: written AFTER the financial mark so the new mark is the latest
    # valid one. A new completed mark was acquired -> new_mark_date = True.
    status = _build_run_status(
        refresh_result=refresh_result, run_at=run_at, ref_today=ref_today,
        mark_date_observed=mark_date, previous_valid_mark_date=prev_mark_date,
        new_mark_date=bool(mark_date is not None and mark_date != prev_mark_date),
        blocked=False, blocked_message=None,
        latest_valid_mark_available=bool(mark_date is not None),
        latest_valid_mark_date=mark_date)
    _write_run_status(mark_dir, status)
    log.step("write", "DONE", "%s mark=%s" % (refresh_result, mark_date))
    return manifest


def _build_manifest(refresh_result, mark_date, prev_mark_date, signal_date, uni_meta,
                    marks, book25, book50, benchmark, run_at, ref_today) -> Dict[str, Any]:
    def _bp(b):
        return {k: b[k] for k in ("book_id", "book_size", "coverage_status", "coverage_pct",
                                  "covered_count", "total_count", "average_return_pct",
                                  "change_since_previous_mark_pct_points",
                                  "benchmark_return_pct", "excess_return_vs_spy_pct_points",
                                  "pnl_claim_valid")}
    return {
        "phase": PHASE, "phase_part": "B", "phase_name": PHASE_NAME,
        "refresh_result": refresh_result,
        "allowed_results": [RES_OK, RES_NO_NEW, RES_PARTIAL, RES_INSUFFICIENT, RES_BLOCKED_KEY,
                            RES_BLOCKED_ENTITLEMENT, RES_BLOCKED_RATE, RES_BLOCKED_PROVIDER,
                            RES_BLOCKED_SCHEMA],
        "new_mark_date": bool(mark_date is not None and mark_date != prev_mark_date),
        "mark_date": mark_date,
        "previous_mark_date": prev_mark_date,
        "reference_today": ref_today.isoformat(),
        "alpha_name": ALPHA_NAME,
        "signal_date": signal_date,
        "universe": uni_meta,
        "n_marks": len(marks),
        "price_source": PRICE_SOURCE,
        "benchmark_summary_preview": benchmark,
        "book_summaries_preview": [_bp(book25), _bp(book50)],
        "last_refresh_run_at": run_at,
        # --- safety ---
        "creates_orders": False, "creates_broker_instructions": False,
        "creates_automation": False, "live_trading": False, "wrote_to_paper_trader": False,
        "api_key_printed": False, "api_key_persisted": False,
        "order_action_all": "NO_ORDER",
        "safety_badges": ["MANUAL DAILY REFRESH", "READ ONLY MARKET DATA", "PAPER TEST ONLY",
                          "NO ORDERS", "NO BROKER", "NO AUTOMATION", "NO LIVE TRADING"],
    }


def _blocked_manifest(result_enum: str, message: str, mark_dir: Path, run_at: str,
                      uni_meta: Dict[str, Any], ref_today: date, log: _Log) -> Dict[str, Any]:
    log.step("blocked", "STOP", "%s: %s" % (result_enum, message))
    # A blocked run PRESERVES any prior valid financial mark unchanged; report it so the
    # consumer knows the last good mark is still available.
    lv_available, lv_date = _latest_valid_mark(mark_dir)
    manifest = {
        "phase": PHASE, "phase_part": "B", "phase_name": PHASE_NAME,
        "refresh_result": result_enum, "new_mark_date": False, "mark_date": None,
        "blocked": True, "blocked_message": message,
        "reference_today": ref_today.isoformat(),
        "alpha_name": ALPHA_NAME, "universe": uni_meta, "last_refresh_run_at": run_at,
        "latest_valid_mark_available": lv_available, "latest_valid_mark_date": lv_date,
        "creates_orders": False, "creates_automation": False, "live_trading": False,
        "wrote_to_paper_trader": False, "api_key_printed": False, "api_key_persisted": False,
        "order_action_all": "NO_ORDER",
        "safety_badges": ["MANUAL DAILY REFRESH", "READ ONLY MARKET DATA", "PAPER TEST ONLY",
                          "NO ORDERS", "NO BROKER", "NO AUTOMATION", "NO LIVE TRADING"],
    }
    # Blocked runs do NOT overwrite the financial-mark files; they persist ONLY the
    # CURRENT RUN STATUS with the exact blocked enum.
    status = _build_run_status(
        refresh_result=result_enum, run_at=run_at, ref_today=ref_today,
        mark_date_observed=None, previous_valid_mark_date=lv_date,
        new_mark_date=False, blocked=True, blocked_message=message,
        latest_valid_mark_available=lv_available, latest_valid_mark_date=lv_date)
    _write_run_status(mark_dir, status)
    return manifest


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Phase 13-G Part B daily alpha EOD mark refresh")
    ap.add_argument("--package-dir", default=str(_DEFAULT_PACKAGE_DIR))
    ap.add_argument("--audit-dir", default=str(_DEFAULT_AUDIT_DIR))
    ap.add_argument("--mark-dir", default=os.environ.get(DAILY_MARK_DIR_ENV,
                                                          str(_DEFAULT_DAILY_MARK_DIR)))
    ap.add_argument("--start", default=DEFAULT_FETCH_START)
    ap.add_argument("--today", default=None, help="reference calendar day (YYYY-MM-DD) for the "
                                                  "completed-EOD rule; defaults to UTC today")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(list(argv) if argv is not None else None)
    log = _Log(verbose=not args.quiet)
    audit_dir = Path(args.audit_dir) if Path(args.audit_dir).exists() else None
    manifest = refresh(Path(args.package_dir), audit_dir, Path(args.mark_dir),
                       today=args.today, start=args.start, log=log)
    print("[%s] RESULT %s mark=%s" % (PHASE, manifest.get("refresh_result"),
                                      manifest.get("mark_date")))
    return 0 if manifest.get("refresh_result") not in _BLOCKED else 2


if __name__ == "__main__":
    raise SystemExit(main())
