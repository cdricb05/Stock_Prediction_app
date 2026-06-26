"""Phase 8-U - EODHD EOD Price-Universe Expansion + Robustness Re-test.

WHY THIS PHASE EXISTS
    Phase 8-T promoted four earnings-surprise-family signals but its scoreable cross-section was
    capped at 299 tickers because the local OHLCV cache only covers the 301-name phase7i priced
    universe. The daemon found a ~503-name S&P-500 constituent list but no local prices for those
    names. This phase is the bounded PRICE-universe expansion: parse the S&P-500 list, diff it
    against the local price cache, acquire EODHD adjusted EOD prices for the MISSING names (bounded,
    skip-existing), assemble an expanded price panel, then rerun the Phase 8-T scoring core on the
    wider universe and compare the promoted alpha before vs after.

    This is a ROBUSTNESS-EXPANSION phase, not a provider-selection or Paper-Trader-integration
    phase. It is PREVIEW-ONLY research: it widens the cross-section a ranking signal is measured on,
    never orders, never automation, never broker execution. It does not touch Paper Trader or GCP.

    HONEST STRUCTURAL NOTE (verified against the live caches): scoreability is the intersection of
    PRICE coverage with a point-in-time EARNINGS event. The Phase 8-S/8-T earnings cache only covers
    the already-priced 301 names, so the S&P-500 names missing from the price cache are also missing
    from the earnings cache. Acquiring EOD PRICES for them is necessary but NOT sufficient to make
    them scoreable - they also need EODHD fundamentals. This runner therefore acquires the prices
    (the phase deliverable), reruns the core, and reports precisely whether the scoreable set grew;
    when prices land for names that DO already have cached earnings (e.g. a future combined batch),
    the rerun picks them up automatically and the before/after comparison becomes non-trivial.

SECRET DISCIPLINE
    EODHD_API_KEY is read ONLY from the environment, never printed, never written to disk. Every
    persisted URL is redacted. A leak scan over the committed artifacts confirms it is clean. The
    EOD transport reuses the proven Phase 8-R host-allowlist / redaction / error-taxonomy / gitignore
    discipline; raw + normalized EOD price payloads stay under the gitignored
    research/data/eodhd/{raw,normalized}/eod_prices/ trees.

TERMINAL DECISIONS
    EXPANDED_UNIVERSE_ALPHA_CONFIRMED | EXPANDED_UNIVERSE_WEAKENS_ALPHA |
    READY_FOR_NEXT_PRICE_BATCH | HARD_BLOCKER_REQUIRES_USER_ACTION | ERROR

Run (offline; reruns the core on whatever prices are already cached; no network, no key):
    python research/run_phase8u_eodhd_price_universe_expansion.py
Live bounded acquisition + rerun (needs a PAID EODHD_API_KEY in the env):
    $env:EODHD_API_KEY = '<PAID_EODHD_KEY>'
    python research/run_phase8u_eodhd_price_universe_expansion.py --live --max-tickers 250 \
        --max-requests 500 --start-date 2016-01-01
Test (fully offline; injected transport, no key, no network):
    python -m pytest tests/test_phase8u_eodhd_price_universe_expansion.py -q
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Reuse the Phase 8-T daemon (its scoring core + extended features) verbatim, which itself reuses
# the 8-S data layer and the 8-R EODHD transport / secret discipline underneath.
from research import run_phase8t_autonomous_alpha_daemon as t8  # noqa: E402
s8 = t8.s8
r8 = t8.r8

PHASE = "8-U"
PROVIDER_NAME = "EODHD"
API_KEY_ENV = r8.API_KEY_ENV

# Reused IO helpers (single source of truth).
_write_json = s8._write_json
_write_csv = s8._write_csv
_read_csv_file = s8._read_csv_file
_read_json = s8._read_json
_round = s8._round
_rel = s8._rel

# --------------------------------------------------------------------------- #
# Paths / inputs.
# --------------------------------------------------------------------------- #
_OUT_DIR = _REPO_ROOT / "research" / "output" / "phase8u_eodhd_price_universe_expansion"
_PHASE8T_DIR = _REPO_ROOT / "research" / "output" / "phase8t_autonomous_alpha_daemon"
_SP500_LIST_HTML = Path(
    "D:/Stock_Prediction_app_data/phase7k_survivorship_data_foundation/wikipedia/"
    "list_of_sp500_companies.html")

# Bounded EOD price-acquisition defaults (from the brief).
DEFAULT_MAX_TICKERS = 250
DEFAULT_MAX_REQUESTS = 500
DEFAULT_START_DATE = "2016-01-01"

# The two Phase 8-T headline signals the report must explicitly answer for.
FOCUS_SIGNALS = ("surprise_sector_neutral", "surprise_x_quality")

# Reuse the 8-T autonomous-loop depth so the rerun is the SAME campaign as 8-T.
DEFAULT_MAX_CYCLES = t8.DEFAULT_MAX_CYCLES

DEC_CONFIRMED = "EXPANDED_UNIVERSE_ALPHA_CONFIRMED"
DEC_WEAKENS = "EXPANDED_UNIVERSE_WEAKENS_ALPHA"
DEC_NEXT_BATCH = "READY_FOR_NEXT_PRICE_BATCH"
DEC_BLOCKER = "HARD_BLOCKER_REQUIRES_USER_ACTION"
DEC_ERROR = "ERROR"
ALLOWED_DECISIONS = (DEC_CONFIRMED, DEC_WEAKENS, DEC_NEXT_BATCH, DEC_BLOCKER, DEC_ERROR)
# Every allowed outcome is a terminal stopping condition per the brief.
_TERMINAL = ALLOWED_DECISIONS

_ARTIFACTS = {
    "report": "phase8u_eodhd_price_universe_expansion.json",
    "run_log": "phase8u_run_log.csv",
    "sp500_extraction": "sp500_name_list_extraction.csv",
    "existing_coverage": "existing_price_coverage.csv",
    "missing_tickers": "missing_price_tickers.csv",
    "acq_progress": "price_acquisition_progress.csv",
    "raw_manifest": "raw_price_storage_manifest.csv",
    "norm_manifest": "normalized_price_storage_manifest.csv",
    "panel_manifest": "expanded_price_panel_manifest.csv",
    "ba_coverage": "before_after_scoreable_coverage.csv",
    "ba_alpha": "before_after_alpha_comparison.csv",
    "scoreboard": "expanded_universe_scenario_scoreboard.csv",
    "promoted": "expanded_universe_promoted_signals.csv",
    "rejected": "expanded_universe_rejected_signals.csv",
    "robustness_delta": "robustness_delta_report.csv",
    "next_plan": "phase8v_next_plan.json",
    "secret_audit": "secret_safety_audit.csv",
}

# --------------------------------------------------------------------------- #
# EODHD EOD price endpoint (separate from the 8-R fundamentals endpoint; reuses 8-R redaction,
# host allowlist and error taxonomy so the proven secret handling is not duplicated).
# --------------------------------------------------------------------------- #
_EOD_URL = "https://eodhd.com/api/eod/{symbol}.US?fmt=json&order=a&from={start}"
_USER_AGENT = "paper-trader-research-phase8u/1.0"

EodTransport = Callable[[str, str], object]   # (symbol, start_date) -> list-of-bars payload


def _eod_plan_url(symbol: str, start: str) -> str:
    base = _EOD_URL.replace("{symbol}", symbol).replace("{start}", start)
    return r8.redact_url(base + "&api_token=")


def _eod_build_live_url(symbol: str, start: str) -> str:
    import os
    base = _EOD_URL.replace("{symbol}", urllib.parse.quote(symbol)).replace("{start}", start)
    key = os.environ.get(API_KEY_ENV, "") or ""
    return "%s&api_token=%s" % (base, urllib.parse.quote(key))


def _eod_live_get(symbol: str, start: str) -> object:
    """One bounded live GET to the allow-listed EODHD EOD endpoint. The key-bearing URL is used
    transiently and never persisted; errors are sanitized so a key cannot leak through them."""
    url = _eod_build_live_url(symbol, start)
    if r8._host_of(url) not in r8.ALLOWED_HOSTS:
        raise r8.EodhdError("refusing non-allowlisted host", error_type="host_blocked")
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": _USER_AGENT, "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=r8.PROBE_TIMEOUT_SECONDS) as resp:  # nosec
            raw = resp.read()
        try:
            return json.loads(raw.decode("utf-8", "replace"))
        except json.JSONDecodeError as exc:
            raise r8.EodhdError("non-JSON response: %s" % exc, error_type="bad_response")
    except urllib.error.HTTPError as exc:
        code = getattr(exc, "code", None)
        # EOD prices are entitled even on the free tier, so a 401/403 here is a genuinely bad key.
        if code == 402:
            etype = "plan_blocked"
        elif code in (401, 403):
            etype = "invalid_key"
        elif code == 429:
            etype = "rate_limited"
        else:
            etype = "http_error"
        raise r8.EodhdError("provider returned HTTP %s" % code, status_code=code, error_type=etype)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise r8.EodhdError("network error: %s" % type(exc).__name__, error_type="network_error")


# --------------------------------------------------------------------------- #
# A. S&P-500 name-list extraction + price-coverage diff.
# --------------------------------------------------------------------------- #
def parse_sp500_list(html_path: Path) -> List[str]:
    """Parse the S&P-500 constituent SYMBOLS from the local Wikipedia table (a NAME universe, not
    OHLCV). Reads only the `id="constituents"` table so changelog tables don't leak in."""
    try:
        html = Path(html_path).read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return []
    m = re.search(r'id="constituents".*?</table>', html, re.S)
    seg = m.group(0) if m else html
    out: List[str] = []
    for sym in re.findall(r'<td><a [^>]*>([A-Z][A-Z.\-]{0,6})</a>', seg):
        if sym not in out:
            out.append(sym)
    return out


def build_coverage_diff(sp500: List[str], priced: List[str], earnings_cached: set
                        ) -> Tuple[List[Dict], List[Dict]]:
    """Return (existing_coverage_rows, missing_rows). A missing S&P-500 name is one with no local
    OHLCV; we also flag whether it already has cached EODHD earnings (the OTHER half of the
    scoreability requirement)."""
    priced_set = set(priced)
    sp_set = set(sp500)
    existing = []
    for tk in priced:
        existing.append({"ticker": tk, "in_price_cache": True, "in_sp500_list": tk in sp_set,
                         "has_earnings_cache": tk in earnings_cached})
    missing = []
    for tk in sp500:
        if tk in priced_set:
            continue
        has_earn = tk in earnings_cached
        missing.append({
            "ticker": tk, "in_price_cache": False, "in_sp500_list": True,
            "has_earnings_cache": has_earn,
            # scoreable-after-price-acquisition only if earnings already exist for this name
            "scoreable_after_price_only": has_earn,
            "needs_earnings_too": not has_earn})
    return existing, missing


# --------------------------------------------------------------------------- #
# B. Bounded EODHD EOD-price acquisition (missing names only; skip-existing).
# --------------------------------------------------------------------------- #
def _eod_raw_dir(P) -> Path:
    return P.r8.raw_dir / "eod_prices"


def _eod_norm_dir(P) -> Path:
    return P.r8.eodhd_dir / "normalized" / "eod_prices"


def _normalize_eod(ticker: str, payload) -> List[Dict]:
    """Flatten an EODHD EOD payload (list of daily bars) into [date, ticker, adjusted_close,
    dollar_volume] rows. Adjusted close is preferred; dollar_volume = raw close * volume."""
    rows: List[Dict] = []
    if not isinstance(payload, list):
        return rows
    for bar in payload:
        if not isinstance(bar, dict):
            continue
        date = str(bar.get("date") or "").strip()
        if not date:
            continue
        ac = bar.get("adjusted_close")
        if ac is None:
            ac = bar.get("close")
        acf = s8._to_float(ac)
        if math.isnan(acf):
            continue
        close = s8._to_float(bar.get("close"))
        vol = s8._to_float(bar.get("volume"))
        dv = close * vol if (not math.isnan(close) and not math.isnan(vol)) else float("nan")
        rows.append({"date": date, "ticker": ticker, "adjusted_close": acf,
                     "dollar_volume": ("" if math.isnan(dv) else dv)})
    return rows


def _eod_cached_tickers(P) -> set:
    d = _eod_raw_dir(P)
    if not d.is_dir():
        return set()
    return {fp.stem.upper() for fp in d.glob("*.json")}


def acquire_eod_prices(P, missing: List[Dict], live: bool, transport: Optional[EodTransport],
                       max_tickers: int, max_requests: int, start_date: str, skip_existing: bool,
                       log) -> Dict:
    """Fetch EODHD adjusted EOD prices for each MISSING ticker (bounded, skip-existing), persist the
    raw payload + a normalized per-ticker price CSV under the gitignored data tree, and write the
    progress + manifest artifacts. Honors max_tickers / max_requests and the proven stop taxonomy."""
    can_run = (transport is not None) or (bool(live) and r8.key_present())
    cached = _eod_cached_tickers(P) if skip_existing else set()
    targets: List[str] = []
    for m in missing:
        tk = m["ticker"]
        if skip_existing and tk in cached:
            continue
        targets.append(tk)
        if len(targets) >= max_tickers:
            break

    progress: List[List] = []
    requests_made = 0
    acquired_tickers: List[str] = []
    consecutive_rl = 0
    stopped = False
    stop_reason = ""
    blocker = ""

    if not can_run or not targets:
        reason = ("no live key (offline rerun on cached prices)" if not can_run
                  else "nothing to acquire (all missing names already cached)")
        log.step("acquire_eod", "SKIP", reason, count=0)
        _write_csv(P.acq_progress,
                   ["ticker", "status", "price_rows", "requests_made_cumulative", "checkpoint"],
                   progress)
        _write_price_manifests(P, acquired_tickers)
        return {"executed": False, "requests_made": 0, "acquired": 0, "acquired_tickers": [],
                "stopped": False, "stop_reason": reason, "blocker": "", "targets": len(targets)}

    r8._ensure_provider_gitignore(P.eodhd_dir)
    _eod_norm_dir(P).mkdir(parents=True, exist_ok=True)
    for tk in targets:
        if requests_made >= max_requests:
            stopped = True
            stop_reason = "max_requests (%d) reached" % max_requests
            break
        try:
            payload = (transport(tk, start_date) if transport is not None
                       else _eod_live_get(tk, start_date))
            requests_made += 1
        except r8.EodhdError as exc:
            requests_made += 1
            etype = getattr(exc, "error_type", "error")
            if etype == "invalid_key":
                stopped = True
                stop_reason = "invalid API key"
                blocker = "EODHD_API_KEY rejected as invalid (HTTP 401/403)"
                progress.append([tk, "INVALID_KEY", 0, requests_made, ""])
                break
            if etype == "plan_blocked":
                stopped = True
                stop_reason = "plan entitlement block"
                blocker = "EODHD plan returned HTTP 402 for EOD prices - check the subscription"
                progress.append([tk, "PLAN_BLOCKED", 0, requests_made, ""])
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
        nrows = _normalize_eod(tk, payload)
        if nrows:
            r8._persist_raw(P.r8, tk, "eod_prices", payload)
            _write_csv(_eod_norm_dir(P) / ("%s.csv" % tk),
                       ["date", "ticker", "adjusted_close", "dollar_volume"],
                       [[r["date"], r["ticker"], r["adjusted_close"], r["dollar_volume"]]
                        for r in nrows])
            acquired_tickers.append(tk)
            status = "OK"
        else:
            status = "EMPTY"
        checkpoint = ("checkpoint" if (acquired_tickers
                      and len(acquired_tickers) % s8.CHECKPOINT_EVERY == 0) else "")
        progress.append([tk, status, len(nrows), requests_made, checkpoint])
        if checkpoint:
            log.step("acquire_eod", "CHECKPOINT",
                     "%d acquired / %d requests" % (len(acquired_tickers), requests_made),
                     count=len(acquired_tickers))
        if transport is None:
            time.sleep(r8.PROBE_MIN_SLEEP_SECONDS)

    _write_csv(P.acq_progress,
               ["ticker", "status", "price_rows", "requests_made_cumulative", "checkpoint"],
               progress)
    _write_price_manifests(P, acquired_tickers)
    log.step("acquire_eod", "DONE" if not stopped else "STOPPED",
             "acquired %d / requests %d%s"
             % (len(acquired_tickers), requests_made, (" - " + stop_reason) if stopped else ""),
             count=len(acquired_tickers))
    return {"executed": True, "requests_made": requests_made, "acquired": len(acquired_tickers),
            "acquired_tickers": acquired_tickers, "stopped": stopped, "stop_reason": stop_reason,
            "blocker": blocker, "targets": len(targets)}


def _write_price_manifests(P, acquired_tickers: List[str]) -> None:
    raw_rows: List[List] = []
    rd = _eod_raw_dir(P)
    if rd.is_dir():
        for fp in sorted(rd.glob("*.json")):
            raw_rows.append([fp.stem, _rel(fp), fp.stat().st_size, True])
    _write_csv(P.raw_manifest, ["ticker", "raw_path_redacted", "bytes", "gitignored"], raw_rows)

    norm_rows: List[List] = []
    nd = _eod_norm_dir(P)
    if nd.is_dir():
        for fp in sorted(nd.glob("*.csv")):
            if fp.name == "expanded_price_panel.csv":
                continue
            n = max(0, sum(1 for _ in fp.open(encoding="utf-8")) - 1)
            norm_rows.append([fp.stem, _rel(fp), n, True])
    _write_csv(P.norm_manifest, ["ticker", "normalized_path", "price_rows", "gitignored"], norm_rows)


# --------------------------------------------------------------------------- #
# C. Expanded price panel (existing 301-cache + newly acquired EOD prices).
# --------------------------------------------------------------------------- #
def build_expanded_panel(P, base_price_csv: Path, acquired_tickers: List[str], log) -> Tuple[Path, Dict]:
    """Combine the existing local price cache with the newly acquired EOD normalized panels into one
    expanded price CSV (benchmark_close merged from the base cache so index-relative returns stay
    valid). Written under the gitignored eod_prices normalized tree. If nothing was acquired, the
    base cache IS the panel (returned unchanged)."""
    import pandas as pd

    base_tickers = s8._price_cache_tickers(base_price_csv)
    if not acquired_tickers:
        manifest = {"base_price_csv": _rel(base_price_csv), "base_tickers": len(base_tickers),
                    "new_tickers": 0, "total_tickers": len(base_tickers),
                    "expanded_panel": _rel(base_price_csv), "is_expanded": False}
        _write_csv(P.panel_manifest, ["metric", "value"],
                   [[k, v] for k, v in manifest.items()])
        log.step("expand_panel", "SKIP", "no new prices acquired; base cache is the panel",
                 count=len(base_tickers))
        return base_price_csv, manifest

    base = pd.read_csv(base_price_csv,
                       usecols=["date", "ticker", "adjusted_close", "benchmark_close"])
    base["date"] = base["date"].astype(str)
    bench = (base.dropna(subset=["benchmark_close"]).drop_duplicates("date")[["date", "benchmark_close"]])

    frames = [base]
    new_rows_total = 0
    for tk in acquired_tickers:
        fp = _eod_norm_dir(P) / ("%s.csv" % tk)
        if not fp.is_file():
            continue
        nd = pd.read_csv(fp, usecols=["date", "ticker", "adjusted_close"])
        nd["date"] = nd["date"].astype(str)
        nd = nd.merge(bench, on="date", how="left")
        frames.append(nd[["date", "ticker", "adjusted_close", "benchmark_close"]])
        new_rows_total += len(nd)

    panel = pd.concat(frames, ignore_index=True)
    out_path = _eod_norm_dir(P) / "expanded_price_panel.csv"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    panel.to_csv(out_path, index=False)

    total_tickers = int(panel["ticker"].nunique())
    manifest = {"base_price_csv": _rel(base_price_csv), "base_tickers": len(base_tickers),
                "new_tickers": len(acquired_tickers), "total_tickers": total_tickers,
                "new_price_rows": new_rows_total, "panel_rows": int(len(panel)),
                "expanded_panel": _rel(out_path), "is_expanded": True}
    _write_csv(P.panel_manifest, ["metric", "value"], [[k, v] for k, v in manifest.items()])
    log.step("expand_panel", "DONE",
             "%d base + %d new = %d tickers (%d rows)"
             % (len(base_tickers), len(acquired_tickers), total_tickers, len(panel)),
             count=total_tickers)
    return out_path, manifest


# --------------------------------------------------------------------------- #
# D. Rerun the Phase 8-T scoring CORE against a price panel.
# --------------------------------------------------------------------------- #
def _mk_scoring_paths(out_dir, data_dir, phase8n_dir, price_csv, sector_csv, macro, phase8r_dir):
    return s8._Paths(out_dir=out_dir, data_dir=data_dir, phase8n_dir=phase8n_dir,
                     price_csv=price_csv, sector_csv=sector_csv, macro=macro,
                     phase8r_dir=phase8r_dir)


def run_core_campaign(P_score, max_cycles: int, as_of: str, log, label: str) -> Dict:
    """Build the point-in-time event table on `P_score`'s price panel and run the SAME 8-T extended
    feature + multi-cycle scenario campaign. Returns the scored result bundle."""
    fund = s8.load_fundamentals_panel(P_score)
    prices = s8.load_prices(P_score)
    ev, stats, audit = s8.build_event_table(P_score, as_of, log)
    log.step("core_%s" % label, "DONE",
             "%d usable events / %d scoreable tickers"
             % (stats.get("events_usable", 0), stats.get("tickers_usable", 0)),
             count=stats.get("events_usable", 0))
    if stats.get("events_usable", 0) > 0:
        ev = t8.prepare_signals_ext(ev, prices, fund, log)
        results, promoted, rejected, cycle_rows = t8.run_campaign(ev, max_cycles, log)
    else:
        results, promoted, rejected, cycle_rows = [], [], [], []
        log.step("core_%s" % label, "SKIP", "no usable events to score")
    return {"stats": stats, "results": results, "promoted": promoted, "rejected": rejected,
            "cycle_rows": cycle_rows, "audit": audit}


# --------------------------------------------------------------------------- #
# E. Before/after comparison artifacts.
# --------------------------------------------------------------------------- #
def _by_scenario(results: List[Dict]) -> Dict[str, Dict]:
    return {r["scenario"]: r for r in results}


def write_comparison_artifacts(P, before: Dict, after: Dict) -> None:
    g = lambda r, k, n=4: _round(r.get(k), n)  # noqa: E731
    b_stats, a_stats = before["stats"], after["stats"]
    b_prom = {p["scenario"] for p in before["promoted"]}
    a_prom = {p["scenario"] for p in after["promoted"]}

    _write_csv(P.ba_coverage, ["metric", "before", "after", "delta"],
               [["scoreable_tickers", b_stats.get("tickers_usable", 0),
                 a_stats.get("tickers_usable", 0),
                 a_stats.get("tickers_usable", 0) - b_stats.get("tickers_usable", 0)],
                ["usable_pit_events", b_stats.get("events_usable", 0),
                 a_stats.get("events_usable", 0),
                 a_stats.get("events_usable", 0) - b_stats.get("events_usable", 0)],
                ["sectors", b_stats.get("sectors", 0), a_stats.get("sectors", 0),
                 a_stats.get("sectors", 0) - b_stats.get("sectors", 0)],
                ["promoted_signals", len(b_prom), len(a_prom), len(a_prom) - len(b_prom)]])

    bmap, amap = _by_scenario(before["results"]), _by_scenario(after["results"])
    scenarios = sorted(set(bmap) | set(amap))
    ba_rows, delta_rows = [], []
    for sc in scenarios:
        rb, ra = bmap.get(sc, {}), amap.get(sc, {})
        ba_rows.append([sc, rb.get("family", ra.get("family", "")),
                        g(rb, "mean_ic"), g(ra, "mean_ic"),
                        g(rb, "ic_t", 2), g(ra, "ic_t", 2),
                        g(rb, "mean_spread"), g(ra, "mean_spread"),
                        g(rb, "net_spread_25bps"), g(ra, "net_spread_25bps"),
                        sc in b_prom, sc in a_prom])
        d_ic = (g(ra, "mean_ic") - g(rb, "mean_ic")
                if g(ra, "mean_ic") is not None and g(rb, "mean_ic") is not None else None)
        d_t = (g(ra, "ic_t", 2) - g(rb, "ic_t", 2)
               if g(ra, "ic_t", 2) is not None and g(rb, "ic_t", 2) is not None else None)
        status = ("unchanged" if (sc in b_prom) == (sc in a_prom)
                  else ("newly_promoted" if sc in a_prom else "dropped"))
        delta_rows.append([sc, g(rb, "mean_ic"), g(ra, "mean_ic"), _round(d_ic) if d_ic is not None else None,
                           g(rb, "ic_t", 2), g(ra, "ic_t", 2), _round(d_t, 2) if d_t is not None else None,
                           rb.get("subperiod_stable", ""), ra.get("subperiod_stable", ""), status])

    _write_csv(P.ba_alpha,
               ["scenario", "family", "mean_ic_before", "mean_ic_after", "ic_t_before", "ic_t_after",
                "spread_before", "spread_after", "net25_before", "net25_after",
                "promoted_before", "promoted_after"], ba_rows)
    _write_csv(P.robustness_delta,
               ["scenario", "ic_before", "ic_after", "ic_delta", "t_before", "t_after", "t_delta",
                "subperiod_stable_before", "subperiod_stable_after", "promotion_status"], delta_rows)

    # expanded-universe (AFTER) scoreboard + promoted/rejected
    _write_csv(P.scoreboard,
               ["scenario", "family", "cycle", "n_events", "n_months", "mean_ic", "ic_t",
                "bh_significant", "mean_spread", "net_spread_25bps", "subperiod_stable",
                "beats_placebo", "promoted"],
               [[r["scenario"], r["family"], r["cycle"], r.get("n_events", 0), r.get("n_months", 0),
                 g(r, "mean_ic"), g(r, "ic_t", 2), r.get("bh_significant", False),
                 g(r, "mean_spread"), g(r, "net_spread_25bps"), r.get("subperiod_stable", False),
                 r.get("beats_placebo", False), r["scenario"] in a_prom]
                for r in after["results"]])

    _write_csv(P.promoted,
               ["scenario", "family", "signal", "mean_ic", "ic_t", "mean_spread", "net_spread_25bps",
                "spread_hit_rate", "subperiod_stable", "n_events", "n_months", "new_vs_before"],
               [[r["scenario"], r["family"], r["signal"], g(r, "mean_ic"), g(r, "ic_t", 2),
                 g(r, "mean_spread"), g(r, "net_spread_25bps"), g(r, "spread_hit_rate"),
                 r.get("subperiod_stable", False), r.get("n_events", 0), r.get("n_months", 0),
                 r["scenario"] not in b_prom] for r in after["promoted"]])

    _write_csv(P.rejected,
               ["scenario", "family", "signal", "mean_ic", "ic_t", "n_events", "reject_reason"],
               [[r["scenario"], r["family"], r["signal"], g(r, "mean_ic"), g(r, "ic_t", 2),
                 r.get("n_events", 0), r.get("reject_reason", "")] for r in after["rejected"]])


# --------------------------------------------------------------------------- #
# F. Decision.
# --------------------------------------------------------------------------- #
def derive_decision(acq: Dict, before: Dict, after: Dict, missing_count: int,
                    acquired: int) -> Tuple[str, str]:
    if acq.get("blocker"):
        return (DEC_BLOCKER, acq["blocker"])
    b_score = before["stats"].get("tickers_usable", 0)
    a_score = after["stats"].get("tickers_usable", 0)
    b_prom = {p["scenario"] for p in before["promoted"]}
    a_prom = {p["scenario"] for p in after["promoted"]}
    focus_survive = all(f in a_prom for f in FOCUS_SIGNALS if f in b_prom or f in a_prom)

    if a_score > b_score:
        dropped = b_prom - a_prom
        if focus_survive and not dropped:
            return (DEC_CONFIRMED,
                    "The scoreable cross-section widened from %d to %d tickers and every Phase 8-T "
                    "promoted signal still clears the full robustness gate on the expanded universe "
                    "(focus signals %s survive; %d total promoted, none dropped). The alpha is "
                    "confirmed out-of-universe; preview-only, no orders."
                    % (b_score, a_score, "/".join(FOCUS_SIGNALS), len(a_prom)))
        return (DEC_WEAKENS,
                "The scoreable cross-section widened from %d to %d tickers but the promoted alpha "
                "degraded on it (%d signal(s) dropped below the gate: %s). The signal is less robust "
                "than the 8-T sample suggested; treat with caution. Preview-only, no orders."
                % (b_score, a_score, len(dropped), ", ".join(sorted(dropped)) or "focus signal weakened"))

    # No NEW scoreable names were added (prices missing/0, or priced names lack cached earnings).
    if acquired > 0:
        why = ("the %d newly priced name(s) have no cached EODHD earnings, so they add price history "
               "but no point-in-time events - scoreability needs BOTH price AND fundamentals."
               % acquired)
    else:
        why = ("no EOD prices were acquired this run (offline, or all missing names already cached); "
               "set EODHD_API_KEY and re-run --live to fetch the bounded next price batch.")
    return (DEC_NEXT_BATCH,
            "Price expansion did not enlarge the scoreable cross-section (still %d tickers): %s "
            "%d S&P-500 name(s) remain without local prices. The honest next batch must acquire EOD "
            "prices AND EODHD fundamentals for the same new names before the universe can be scored "
            "wider. Preview-only, no orders." % (a_score, why, missing_count))


def _next_step(decision: str, missing_count: int) -> Tuple[str, str]:
    if decision in (DEC_CONFIRMED, DEC_WEAKENS):
        return ("Review research/output/phase8u_eodhd_price_universe_expansion/"
                "before_after_alpha_comparison.csv, then carry the expanded-universe verdict into the "
                "Paper Trader daily-review cockpit as PREVIEW-ONLY ranking ideas (manual review; no "
                "orders).",
                "Carry the expanded-universe alpha verdict to preview-only review")
    if decision == DEC_NEXT_BATCH:
        return ("$env:EODHD_API_KEY = '<PAID_EODHD_KEY>'; python "
                "research/run_phase8u_eodhd_price_universe_expansion.py --live --max-tickers 250 "
                "--max-requests 500 --start-date 2016-01-01   (acquire the bounded next price batch; "
                "pair with an 8-S fundamentals top-up for the same %d names so they become scoreable)"
                % missing_count,
                "Acquire the next bounded price+fundamentals batch, then re-test")
    if decision == DEC_BLOCKER:
        return ("Resolve the blocker (set a valid PAID EODHD_API_KEY), then re-run: python "
                "research/run_phase8u_eodhd_price_universe_expansion.py --live",
                "Clear the hard blocker, then re-run the price expansion")
    return ("python research/run_phase8u_eodhd_price_universe_expansion.py", "Re-run the expansion")


# --------------------------------------------------------------------------- #
# Orchestration.
# --------------------------------------------------------------------------- #
class _Paths:
    """8-U committed-safe output paths + the reused 8-S/8-R data-tree handle."""

    def __init__(self, out_dir=None, data_dir=None, phase8n_dir=None, price_csv=None,
                 sector_csv=None, macro=None, phase8r_dir=None, phase8t_dir=None, sp500_html=None):
        self.out = Path(out_dir) if out_dir else _OUT_DIR
        # Reuse the 8-S paths object purely as the data-tree + price/sector/macro handle.
        self._score = _mk_scoring_paths(self.out, data_dir, phase8n_dir, price_csv, sector_csv,
                                        macro, phase8r_dir)
        self.r8 = self._score.r8
        self.phase8t = Path(phase8t_dir) if phase8t_dir else _PHASE8T_DIR
        self.sp500_html = Path(sp500_html) if sp500_html else _SP500_LIST_HTML
        self.base_price_csv = self._score.price_csv
        self.data_dir = data_dir
        self.phase8n_dir = phase8n_dir
        self.sector_csv = sector_csv
        self.macro = macro
        self.phase8r_dir = phase8r_dir
        for key, name in _ARTIFACTS.items():
            setattr(self, key, self.out / name)

    @property
    def eodhd_dir(self) -> Path:
        return self.r8.eodhd_dir


def run(live: bool = False, transport: Optional[EodTransport] = None,
        out_dir: Optional[Path] = None, data_dir: Optional[Path] = None,
        phase8n_dir: Optional[Path] = None, price_csv: Optional[Path] = None,
        sector_csv: Optional[Path] = None, macro: Optional[Dict[str, Path]] = None,
        phase8r_dir: Optional[Path] = None, phase8t_dir: Optional[Path] = None,
        sp500_html: Optional[Path] = None, max_tickers: int = DEFAULT_MAX_TICKERS,
        max_requests: int = DEFAULT_MAX_REQUESTS, start_date: str = DEFAULT_START_DATE,
        skip_existing: bool = True, max_cycles: int = DEFAULT_MAX_CYCLES,
        as_of: str = s8.DEFAULT_AS_OF, verbose: bool = True) -> Dict:
    P = _Paths(out_dir, data_dir, phase8n_dir, price_csv, sector_csv, macro, phase8r_dir,
               phase8t_dir, sp500_html)
    P.out.mkdir(parents=True, exist_ok=True)
    log = t8._Log(verbose)
    try:
        import pandas as pd  # noqa: F401
        present = r8.key_present()

        # Read Phase 8-T outputs (provenance + recorded baseline).
        t8_report = _read_json(P.phase8t / "phase8t_autonomous_alpha_daemon.json")
        t8_decision = _read_json(P.phase8t / "final_research_decision.json")
        t8_promoted = (t8_decision.get("promoted_signals")
                       or t8_report.get("promoted_signals") or [])
        log.step("prior", "INFO", "Phase 8-T decision: %s; promoted: %s"
                 % (t8_decision.get("decision") or t8_report.get("decision") or "(none read)",
                    ", ".join(t8_promoted) if t8_promoted else "(none)"))
        log.step("start", "INFO",
                 "live=%s key_present=%s max_tickers=%d max_requests=%d start=%s"
                 % (live, present, max_tickers, max_requests, start_date))

        # A. S&P-500 list extraction + price-coverage diff.
        sp500 = parse_sp500_list(P.sp500_html)
        priced = s8._price_cache_tickers(P.base_price_csv)
        earnings_cached = set(r8._cached_tickers(P.r8))
        existing, missing = build_coverage_diff(sp500, priced, earnings_cached)
        _write_csv(P.sp500_extraction, ["rank", "ticker", "in_price_cache", "has_earnings_cache"],
                   [[i + 1, tk, tk in set(priced), tk in earnings_cached]
                    for i, tk in enumerate(sp500)])
        _write_csv(P.existing_coverage,
                   ["ticker", "in_price_cache", "in_sp500_list", "has_earnings_cache"],
                   [[r["ticker"], r["in_price_cache"], r["in_sp500_list"], r["has_earnings_cache"]]
                    for r in existing])
        _write_csv(P.missing_tickers,
                   ["ticker", "in_price_cache", "in_sp500_list", "has_earnings_cache",
                    "scoreable_after_price_only", "needs_earnings_too"],
                   [[r["ticker"], r["in_price_cache"], r["in_sp500_list"], r["has_earnings_cache"],
                     r["scoreable_after_price_only"], r["needs_earnings_too"]] for r in missing])
        log.step("diff", "DONE", "sp500=%d priced=%d missing=%d (of which %d already have earnings)"
                 % (len(sp500), len(priced), len(missing),
                    sum(1 for m in missing if m["has_earnings_cache"])), count=len(missing))

        # B. bounded EOD-price acquisition for missing names.
        acq = acquire_eod_prices(P, missing, live, transport, max_tickers, max_requests,
                                 start_date, skip_existing, log)

        # C. expanded price panel.
        panel_csv, panel_manifest = build_expanded_panel(P, P.base_price_csv,
                                                         acq["acquired_tickers"], log)

        # D. rerun the 8-T scoring CORE: BEFORE (base) and AFTER (expanded).
        P_before = _mk_scoring_paths(P.out, P.data_dir, P.phase8n_dir, P.base_price_csv,
                                     P.sector_csv, P.macro, P.phase8r_dir)
        before = run_core_campaign(P_before, max_cycles, as_of, log, "before")
        if acq["acquired_tickers"]:
            P_after = _mk_scoring_paths(P.out, P.data_dir, P.phase8n_dir, panel_csv,
                                        P.sector_csv, P.macro, P.phase8r_dir)
            after = run_core_campaign(P_after, max_cycles, as_of, log, "after")
        else:
            after = before  # identical panel -> identical campaign (no double work)
            log.step("core_after", "REUSE", "expanded panel == base; AFTER reuses BEFORE")

        # E. comparison artifacts.
        write_comparison_artifacts(P, before, after)

        # F. decision.
        decision, rationale = derive_decision(acq, before, after, len(missing), acq["acquired"])
        log.step("decision", "DONE", decision)

        b_prom = [p["scenario"] for p in before["promoted"]]
        a_prom = [p["scenario"] for p in after["promoted"]]
        a_prom_set = set(a_prom)
        new_alpha = sorted(a_prom_set - set(b_prom))
        ssn_survives = "surprise_sector_neutral" in a_prom_set
        sxq_survives = "surprise_x_quality" in a_prom_set

        next_cmd, next_title = _next_step(decision, len(missing))
        _write_json(P.next_plan, {
            "phase": "8-V", "title": next_title, "depends_on_decision": decision,
            "next_command": next_cmd, "missing_price_names": len(missing),
            "preview_only": True, "orders_enabled": False, "automation_enabled": False,
            "committed": False})

        _write_csv(P.run_log, ["step", "status", "detail", "count"], log.rows)

        # secret leak scan over committed artifacts.
        written = [p for p in P.out.glob("*") if p.is_file()]
        leak_clean, scanned, marker_found = r8._scan_for_leaks(written)
        _write_csv(P.secret_audit, ["check", "result", "detail"],
                   [["api_key_read_from_env_only", True, "EODHD_API_KEY via os.environ only (8-R layer)"],
                    ["api_key_printed", False, "key value never printed"],
                    ["api_key_written_to_disk", False, "key value never written to any artifact"],
                    ["committed_artifacts_contain_key_query_param", marker_found,
                     "leak scan over %d committed files" % scanned],
                    ["secret_leak_scan_clean", leak_clean, "no key value or key query param"],
                    ["raw_normalized_prices_gitignored", True,
                     "research/data/eodhd/{raw,normalized}/eod_prices/ force-gitignored before write"]])

        report = {
            "phase": PHASE,
            "objective": ("Bounded EODHD EOD-price universe expansion: parse the S&P-500 list, diff "
                          "against the local price cache, acquire prices for the missing names, build "
                          "an expanded price panel, rerun the Phase 8-T scoring core, and compare the "
                          "promoted alpha before vs after. Preview-only; no orders."),
            "provider": PROVIDER_NAME, "api_key_env_var": API_KEY_ENV,
            "builds_on_phase8t_decision": t8_decision.get("decision") or t8_report.get("decision", ""),
            "phase8t_promoted": t8_promoted,
            "eodhd_key_present": present, "api_key_logged": False,
            "mode": ("live_acquire_and_rerun" if acq.get("executed") and transport is None
                     else ("test_transport" if transport is not None else "offline_rerun_cached")),
            "as_of": as_of, "start_date": start_date,
            "bounded_limits": {"max_tickers": max_tickers, "max_requests": max_requests,
                               "max_cycles": max_cycles, "skip_existing": skip_existing,
                               "checkpoint_every": s8.CHECKPOINT_EVERY},
            "universe": {
                "sp500_name_list": len(sp500), "priced_tickers": len(priced),
                "missing_price_tickers": len(missing),
                "missing_with_earnings_cache": sum(1 for m in missing if m["has_earnings_cache"]),
                "missing_need_earnings_too": sum(1 for m in missing if m["needs_earnings_too"])},
            "acquisition": {"executed": acq.get("executed"), "requests_made": acq.get("requests_made"),
                            "prices_acquired": acq.get("acquired"),
                            "stopped_early": acq.get("stopped"), "stop_reason": acq.get("stop_reason")},
            "expanded_panel": panel_manifest,
            "before": {"scoreable_tickers": before["stats"].get("tickers_usable", 0),
                       "usable_events": before["stats"].get("events_usable", 0),
                       "promoted_signals": b_prom},
            "after": {"scoreable_tickers": after["stats"].get("tickers_usable", 0),
                      "usable_events": after["stats"].get("events_usable", 0),
                      "promoted_signals": a_prom},
            "scoreable_delta": after["stats"].get("tickers_usable", 0)
            - before["stats"].get("tickers_usable", 0),
            "surprise_sector_neutral_survives": ssn_survives,
            "surprise_x_quality_survives": sxq_survives,
            "new_alpha_on_expanded_universe": new_alpha,
            "new_alpha_found": bool(new_alpha),
            "scenarios_tested": len(after["results"]),
            "decision": decision, "decision_is_terminal": decision in _TERMINAL,
            "allowed_decisions": list(ALLOWED_DECISIONS), "decision_rationale": rationale,
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
            "data_fabricated": False, "alpha_fabricated": False,
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


def _print_summary(report: Dict) -> None:
    print("phase:                 %s" % report.get("phase"))
    print("mode:                  %s" % report.get("mode"))
    print("eodhd key present:     %s" % report.get("eodhd_key_present"))
    uni = report.get("universe", {})
    print("sp500 / priced:        %s / %s" % (uni.get("sp500_name_list"), uni.get("priced_tickers")))
    print("missing price tickers: %s" % uni.get("missing_price_tickers"))
    acq = report.get("acquisition", {})
    print("prices acquired:       %s (requests %s)" % (acq.get("prices_acquired"),
                                                       acq.get("requests_made")))
    print("scoreable before/after:%s / %s" % (report.get("before", {}).get("scoreable_tickers"),
                                              report.get("after", {}).get("scoreable_tickers")))
    print("ssn survives:          %s" % report.get("surprise_sector_neutral_survives"))
    print("sxq survives:          %s" % report.get("surprise_x_quality_survives"))
    print("new alpha:             %s" % report.get("new_alpha_on_expanded_universe"))
    print("decision:              %s" % report.get("decision"))
    print("next command:          %s" % report.get("recommended_next_command"))


def _parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        description=("Phase 8-U EODHD EOD price-universe expansion (offline rerun on cached prices "
                     "by default; bounded live acquisition with --live + EODHD_API_KEY)."))
    ap.add_argument("--live", action="store_true",
                    help="Bounded live EODHD EOD-price acquisition for missing names (needs EODHD_API_KEY).")
    ap.add_argument("--max-tickers", type=int, default=DEFAULT_MAX_TICKERS)
    ap.add_argument("--max-requests", type=int, default=DEFAULT_MAX_REQUESTS)
    ap.add_argument("--start-date", default=DEFAULT_START_DATE)
    ap.add_argument("--skip-existing", default="true",
                    help="Skip tickers whose EOD prices are already cached (default true).")
    ap.add_argument("--max-cycles", type=int, default=DEFAULT_MAX_CYCLES)
    ap.add_argument("--as-of", default=s8.DEFAULT_AS_OF)
    return ap.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parse_args(argv)
    run(live=args.live, max_tickers=args.max_tickers, max_requests=args.max_requests,
        start_date=args.start_date, skip_existing=str(args.skip_existing).lower() != "false",
        max_cycles=args.max_cycles, as_of=args.as_of, verbose=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
