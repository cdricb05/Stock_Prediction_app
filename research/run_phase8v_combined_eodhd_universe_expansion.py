"""Phase 8-V - Combined EODHD Price + Fundamentals Universe Expansion + Robustness Re-test.

WHY THIS PHASE EXISTS
    Phase 8-T promoted four earnings-surprise-family signals but its scoreable cross-section was
    capped at 299 tickers. Phase 8-U then proved the binding constraint precisely: of the 294
    S&P-500 names missing from the local price cache, ALL 294 ALSO lack cached EODHD earnings, so
    acquiring EOD PRICES alone cannot enlarge the scoreable set - scoreability is the intersection
    of PRICE coverage with a point-in-time EARNINGS event. The honest next step is therefore a
    MATCHED, COMBINED acquisition: for the SAME missing names, fetch BOTH the EODHD EOD prices AND
    the EODHD fundamentals/earnings, mark a name scoreable only when both are present, assemble an
    expanded combined panel, then rerun the Phase 8-T scoring core on the wider scoreable universe
    and compare the promoted alpha before vs after.

    This is a ROBUSTNESS-EXPANSION phase, not provider selection, not price-only, not
    fundamentals-only, not Paper-Trader integration. It is PREVIEW-ONLY research: it widens the
    cross-section a ranking signal is measured on, never orders, never automation, never broker
    execution. It does not touch Paper Trader or GCP.

REUSE
    Builds directly on Phase 8-U (its EOD-price transport + expanded-panel + core-rerun plumbing)
    which itself reuses Phase 8-T's scoring core, Phase 8-S's data layer, and Phase 8-R's EODHD
    transport / secret discipline. The fundamentals leg reuses the proven 8-R/8-S fundamentals
    endpoint, classification, normalization and persistence verbatim.

SECRET DISCIPLINE
    EODHD_API_KEY is read ONLY from the environment, never printed, never written to disk. Every
    persisted URL is redacted. A leak scan over the committed artifacts confirms it is clean. Raw +
    normalized EOD price AND fundamentals payloads stay under the gitignored
    research/data/eodhd/{raw,normalized}/ trees.

TERMINAL DECISIONS
    EXPANDED_UNIVERSE_ALPHA_CONFIRMED | EXPANDED_UNIVERSE_WEAKENS_ALPHA |
    READY_FOR_NEXT_COMBINED_BATCH | HARD_BLOCKER_REQUIRES_USER_ACTION | ERROR

Run (offline; reruns the core on whatever price+earnings are already cached; no network, no key):
    python research/run_phase8v_combined_eodhd_universe_expansion.py
Live bounded combined acquisition + rerun (needs a PAID EODHD_API_KEY in the env):
    $env:EODHD_API_KEY = '<PAID_EODHD_KEY>'
    python research/run_phase8v_combined_eodhd_universe_expansion.py --live --max-tickers 250 \
        --max-requests 1000 --start-date 2016-01-01
Test (fully offline; injected transport, no key, no network):
    python -m pytest tests/test_phase8v_combined_eodhd_universe_expansion.py -q
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Reuse the Phase 8-U price-expansion plumbing verbatim (which reuses 8-T scoring / 8-S data /
# 8-R transport underneath). 8-V adds the matched fundamentals leg on top.
from research import run_phase8u_eodhd_price_universe_expansion as u8  # noqa: E402

t8 = u8.t8
s8 = u8.s8
r8 = u8.r8

PHASE = "8-V"
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
_OUT_DIR = _REPO_ROOT / "research" / "output" / "phase8v_combined_eodhd_universe_expansion"
_PHASE8T_DIR = _REPO_ROOT / "research" / "output" / "phase8t_autonomous_alpha_daemon"
_PHASE8U_DIR = _REPO_ROOT / "research" / "output" / "phase8u_eodhd_price_universe_expansion"
_SP500_LIST_HTML = u8._SP500_LIST_HTML

# Bounded combined-acquisition defaults (from the brief): each ticker needs up to TWO requests
# (one EOD-price + one fundamentals), so the request budget is double the 8-U price-only budget.
DEFAULT_MAX_TICKERS = 250
DEFAULT_MAX_REQUESTS = 1000
DEFAULT_START_DATE = "2016-01-01"

# The two Phase 8-T headline signals the report must explicitly answer for.
FOCUS_SIGNALS = ("surprise_sector_neutral", "surprise_x_quality")

DEFAULT_MAX_CYCLES = t8.DEFAULT_MAX_CYCLES

# EOD-price endpoint sentinel for the combined transport dispatch (distinct from the 8-R
# fundamentals/earnings_calendar endpoints).
EP_EOD_PRICES = "eod_prices"
EP_FUNDAMENTALS = r8.EP_FUNDAMENTALS

DEC_CONFIRMED = "EXPANDED_UNIVERSE_ALPHA_CONFIRMED"
DEC_WEAKENS = "EXPANDED_UNIVERSE_WEAKENS_ALPHA"
DEC_NEXT_BATCH = "READY_FOR_NEXT_COMBINED_BATCH"
DEC_BLOCKER = "HARD_BLOCKER_REQUIRES_USER_ACTION"
DEC_ERROR = "ERROR"
ALLOWED_DECISIONS = (DEC_CONFIRMED, DEC_WEAKENS, DEC_NEXT_BATCH, DEC_BLOCKER, DEC_ERROR)
_TERMINAL = ALLOWED_DECISIONS

_ARTIFACTS = {
    "report": "phase8v_combined_eodhd_universe_expansion.json",
    "run_log": "phase8v_run_log.csv",
    "sp500_extraction": "sp500_name_list_extraction.csv",
    "matched_universe": "matched_acquisition_universe.csv",
    "existing_coverage": "existing_combined_coverage.csv",
    "missing_tickers": "missing_combined_tickers.csv",
    "acq_progress": "combined_acquisition_progress.csv",
    "raw_price_manifest": "raw_price_storage_manifest.csv",
    "raw_fund_manifest": "raw_fundamentals_storage_manifest.csv",
    "norm_price_manifest": "normalized_price_storage_manifest.csv",
    "norm_fund_manifest": "normalized_fundamentals_storage_manifest.csv",
    "expanded_scoreable": "expanded_scoreable_coverage.csv",
    "ba_coverage": "before_after_scoreable_coverage.csv",
    "ba_alpha": "before_after_alpha_comparison.csv",
    "scoreboard": "expanded_universe_scenario_scoreboard.csv",
    "promoted": "expanded_universe_promoted_signals.csv",
    "rejected": "expanded_universe_rejected_signals.csv",
    "robustness_delta": "robustness_delta_report.csv",
    "panel_manifest": "expanded_price_panel_manifest.csv",
    "next_plan": "phase8w_next_plan.json",
    "secret_audit": "secret_safety_audit.csv",
}

# The 18 committed-safe artifacts the brief requires (the rest above are harmless extras).
_REQUIRED_ARTIFACTS = (
    "report", "matched_universe", "existing_coverage", "missing_tickers", "acq_progress",
    "raw_price_manifest", "raw_fund_manifest", "norm_price_manifest", "norm_fund_manifest",
    "expanded_scoreable", "ba_coverage", "ba_alpha", "scoreboard", "promoted", "rejected",
    "robustness_delta", "next_plan", "secret_audit")

CombinedTransport = Callable[[str, str], object]   # (symbol, endpoint) -> payload


# --------------------------------------------------------------------------- #
# Live dispatch for the combined transport (price leg + fundamentals leg).
# --------------------------------------------------------------------------- #
def _live_combined(symbol: str, endpoint: str, start_date: str) -> object:
    """Dispatch one bounded live GET to the right allow-listed EODHD endpoint. Both legs reuse the
    proven 8-U / 8-R transport (host allowlist, URL redaction, error taxonomy)."""
    if endpoint == EP_EOD_PRICES:
        return u8._eod_live_get(symbol, start_date)
    return r8._live_get(symbol, EP_FUNDAMENTALS)


# --------------------------------------------------------------------------- #
# A. Combined coverage diff: a name is scoreable-ready only with BOTH price AND earnings.
# --------------------------------------------------------------------------- #
def _raw_fund_cached_tickers(P) -> set:
    d = P.r8.raw_dir / "fundamentals"
    if not d.is_dir():
        return set()
    return {fp.stem.upper() for fp in d.glob("*.json")}


def build_combined_coverage(sp500: List[str], priced: List[str], earnings_cached: set,
                            fund_cached: set) -> Tuple[List[Dict], List[Dict]]:
    """Return (existing_rows, missing_rows). 'existing' = priced names (with earnings/fundamentals
    flags); 'missing combined' = any S&P-500 name lacking EITHER a local price OR a cached
    point-in-time earnings event (the two halves of scoreability)."""
    priced_set = set(priced)
    existing: List[Dict] = []
    for tk in priced:
        existing.append({
            "ticker": tk, "has_price": True, "has_earnings": tk in earnings_cached,
            "has_fundamentals": tk in fund_cached,
            "scoreable_ready": tk in earnings_cached})
    missing: List[Dict] = []
    for tk in sp500:
        has_price = tk in priced_set
        has_earn = tk in earnings_cached
        has_fund = tk in fund_cached
        if has_price and has_earn:
            continue  # already scoreable-ready
        missing.append({
            "ticker": tk, "has_price": has_price, "has_earnings": has_earn,
            "has_fundamentals": has_fund,
            "need_price": not has_price, "need_fundamentals": not has_earn,
            "scoreable_after_combined": True})  # acquiring both legs makes it scoreable
    return existing, missing


# --------------------------------------------------------------------------- #
# B. Bounded MATCHED combined acquisition (EOD prices + fundamentals for the same names).
# --------------------------------------------------------------------------- #
class _Stop(Exception):
    def __init__(self, reason: str, blocker: str) -> None:
        super().__init__(reason)
        self.reason = reason
        self.blocker = blocker


def acquire_combined(P, missing: List[Dict], live: bool, transport: Optional[CombinedTransport],
                     max_tickers: int, max_requests: int, start_date: str, skip_existing: bool,
                     log) -> Dict:
    """For each MISSING name (bounded by max_tickers; skip-existing per leg), fetch the EODHD EOD
    price AND the EODHD fundamentals, persist both under the gitignored data tree, and mark the name
    scoreable only when BOTH a price panel AND a point-in-time earnings event are present. Honors
    max_requests across both legs and the proven stop taxonomy (invalid key / plan / free-tier /
    consecutive rate-limits)."""
    can_run = (transport is not None) or (bool(live) and r8.key_present())
    price_cached = u8._eod_cached_tickers(P) if skip_existing else set()
    fund_cached = set(r8._cached_tickers(P.r8)) if skip_existing else set()

    targets: List[Dict] = []
    for m in missing:
        tk = m["ticker"]
        do_price = (not skip_existing) or (tk not in price_cached)
        do_fund = (not skip_existing) or (tk not in fund_cached)
        if not (do_price or do_fund):
            continue
        targets.append({"ticker": tk, "do_price": do_price, "do_fund": do_fund})
        if len(targets) >= max_tickers:
            break

    _write_csv(P.matched_universe,
               ["ticker", "need_price", "need_fundamentals", "has_price_cached", "has_earnings_cached"],
               [[t["ticker"], t["do_price"], t["do_fund"], t["ticker"] in price_cached,
                 t["ticker"] in fund_cached] for t in targets])

    progress: List[List] = []
    requests_made = 0
    price_acquired: List[str] = []
    fund_acquired: List[str] = []
    matched_scoreable: List[str] = []
    normalized: Dict[str, List[Dict]] = {}
    consecutive_rl = [0]
    stopped = False
    stop_reason = ""
    blocker = ""

    if not can_run or not targets:
        reason = ("no live key (offline rerun on cached price+earnings)" if not can_run
                  else "nothing to acquire (all missing names already fully cached)")
        log.step("acquire", "SKIP", reason, count=0)
        _write_csv(P.acq_progress,
                   ["ticker", "price_status", "fund_status", "price_rows", "earnings_rows",
                    "requests_cumulative", "scoreable_now", "checkpoint"], progress)
        _write_combined_manifests(P, normalized)
        return {"executed": False, "requests_made": 0, "price_acquired": [], "fund_acquired": [],
                "matched_scoreable": [], "stopped": False, "stop_reason": reason, "blocker": "",
                "targets": len(targets)}

    r8._ensure_provider_gitignore(P.eodhd_dir)
    u8._eod_norm_dir(P).mkdir(parents=True, exist_ok=True)

    def _fetch(endpoint: str, tk: str):
        """One bounded leg fetch. Returns (payload | None). Raises _Stop on a terminal condition;
        records soft errors as None. Updates requests_made / consecutive_rl by closure."""
        nonlocal requests_made
        try:
            payload = (transport(tk, endpoint) if transport is not None
                       else _live_combined(tk, endpoint, start_date))
            requests_made += 1
            consecutive_rl[0] = 0
            return payload
        except r8.EodhdError as exc:
            requests_made += 1
            etype = getattr(exc, "error_type", "error")
            if etype == "invalid_key":
                raise _Stop("invalid API key",
                            "EODHD_API_KEY rejected as invalid (HTTP 401/403)")
            if etype == "plan_blocked":
                raise _Stop("plan entitlement block",
                            "EODHD plan returned HTTP 402 - check the subscription entitlement")
            if etype == "free_tier_blocked":
                raise _Stop("free-tier entitlement block",
                            "EODHD key is on the FREE tier (EOD prices only); fundamentals require "
                            "a paid plan")
            if etype == "rate_limited":
                consecutive_rl[0] += 1
                if consecutive_rl[0] >= r8.STOP_AFTER_CONSECUTIVE_RATE_LIMITS:
                    raise _Stop("rate-limited %d times consecutively" % consecutive_rl[0],
                                "EODHD rate limit exhausted; re-run after backoff to continue")
                if transport is None:
                    time.sleep(r8.PROBE_MIN_SLEEP_SECONDS)
                return None
            if transport is None:
                time.sleep(r8.PROBE_MIN_SLEEP_SECONDS)
            return None

    try:
        for t in targets:
            tk = t["ticker"]
            price_status, fund_status = "SKIP", "SKIP"
            n_price, n_earn = 0, 0

            # --- price leg ---
            if t["do_price"]:
                if requests_made >= max_requests:
                    raise _Stop("max_requests (%d) reached" % max_requests, "")
                payload = _fetch(EP_EOD_PRICES, tk)
                if payload is None:
                    price_status = "ERROR"
                else:
                    nrows = u8._normalize_eod(tk, payload)
                    n_price = len(nrows)
                    if nrows:
                        r8._persist_raw(P.r8, tk, "eod_prices", payload)
                        _write_csv(u8._eod_norm_dir(P) / ("%s.csv" % tk),
                                   ["date", "ticker", "adjusted_close", "dollar_volume"],
                                   [[r["date"], r["ticker"], r["adjusted_close"], r["dollar_volume"]]
                                    for r in nrows])
                        price_acquired.append(tk)
                        price_status = "OK"
                    else:
                        price_status = "EMPTY"

            # --- fundamentals leg ---
            if t["do_fund"]:
                if requests_made >= max_requests:
                    raise _Stop("max_requests (%d) reached" % max_requests, "")
                payload = _fetch(EP_FUNDAMENTALS, tk)
                if payload is None:
                    fund_status = "ERROR"
                else:
                    fam = r8.classify_fundamentals(payload)
                    nrows = r8._normalize_earnings(tk, payload)
                    n_earn = len(nrows)
                    earn_ok = any(fr["status"] == "OK" and fr["family"] == r8.FAM_EARNINGS
                                  for fr in fam)
                    if earn_ok:
                        r8._persist_raw(P.r8, tk, "fundamentals", payload)
                        if nrows:
                            normalized[tk] = nrows
                        fund_acquired.append(tk)
                        fund_status = "OK"
                    else:
                        fund_status = "EMPTY"

            has_price_now = (tk in price_acquired) or (tk in price_cached)
            has_earn_now = (tk in fund_acquired) or (tk in fund_cached)
            is_scoreable = has_price_now and has_earn_now
            if is_scoreable:
                matched_scoreable.append(tk)
            checkpoint = ("checkpoint" if (matched_scoreable
                          and len(matched_scoreable) % s8.CHECKPOINT_EVERY == 0) else "")
            progress.append([tk, price_status, fund_status, n_price, n_earn, requests_made,
                             is_scoreable, checkpoint])
            if checkpoint:
                log.step("acquire", "CHECKPOINT",
                         "%d matched-scoreable / %d requests" % (len(matched_scoreable), requests_made),
                         count=len(matched_scoreable))
    except _Stop as st:
        stopped = True
        stop_reason = st.reason
        blocker = st.blocker

    if normalized:
        r8._append_normalized(P.r8, normalized)
    _write_csv(P.acq_progress,
               ["ticker", "price_status", "fund_status", "price_rows", "earnings_rows",
                "requests_cumulative", "scoreable_now", "checkpoint"], progress)
    _write_combined_manifests(P, normalized)
    log.step("acquire", "DONE" if not stopped else "STOPPED",
             "prices %d / fundamentals %d / matched-scoreable %d / requests %d%s"
             % (len(price_acquired), len(fund_acquired), len(matched_scoreable), requests_made,
                (" - " + stop_reason) if stopped else ""),
             count=len(matched_scoreable))
    return {"executed": True, "requests_made": requests_made, "price_acquired": price_acquired,
            "fund_acquired": fund_acquired, "matched_scoreable": matched_scoreable,
            "stopped": stopped, "stop_reason": stop_reason, "blocker": blocker,
            "targets": len(targets)}


def _write_combined_manifests(P, normalized: Dict[str, List[Dict]]) -> None:
    # raw EOD prices
    raw_price: List[List] = []
    rpd = u8._eod_raw_dir(P)
    if rpd.is_dir():
        for fp in sorted(rpd.glob("*.json")):
            raw_price.append([fp.stem, _rel(fp), fp.stat().st_size, True])
    _write_csv(P.raw_price_manifest, ["ticker", "raw_path", "bytes", "gitignored"], raw_price)

    # raw fundamentals
    raw_fund: List[List] = []
    rfd = P.r8.raw_dir / "fundamentals"
    if rfd.is_dir():
        for fp in sorted(rfd.glob("*.json")):
            raw_fund.append([fp.stem, _rel(fp), fp.stat().st_size, True])
    _write_csv(P.raw_fund_manifest, ["ticker", "raw_path", "bytes", "gitignored"], raw_fund)

    # normalized EOD prices (per-ticker CSVs)
    norm_price: List[List] = []
    npd = u8._eod_norm_dir(P)
    if npd.is_dir():
        for fp in sorted(npd.glob("*.csv")):
            if fp.name == "expanded_price_panel.csv":
                continue
            n = max(0, sum(1 for _ in fp.open(encoding="utf-8")) - 1)
            norm_price.append([fp.stem, _rel(fp), n, True])
    _write_csv(P.norm_price_manifest, ["ticker", "normalized_path", "price_rows", "gitignored"],
               norm_price)

    # normalized fundamentals/earnings (single appended CSV)
    norm_fund: List[List] = []
    nc = P.r8.normalized_csv
    if nc.is_file():
        recs = _read_csv_file(nc)
        tickers = sorted({(r.get("symbol") or "").strip().upper() for r in recs if r.get("symbol")})
        norm_fund.append([_rel(nc), len(recs), len(tickers), True])
    _write_csv(P.norm_fund_manifest, ["normalized_file", "rows", "distinct_tickers", "gitignored"],
               norm_fund)


# --------------------------------------------------------------------------- #
# C. Rerun the Phase 8-T scoring CORE (mirrors 8-U but also captures the usable-ticker LIST so the
#    expanded-scoreable-coverage artifact can name the cross-section that grew).
# --------------------------------------------------------------------------- #
def run_core_campaign(P_score, max_cycles: int, as_of: str, log, label: str) -> Dict:
    fund = s8.load_fundamentals_panel(P_score)
    prices = s8.load_prices(P_score)
    ev, stats, audit = s8.build_event_table(P_score, as_of, log)
    stats = dict(stats)
    stats["usable_tickers"] = (sorted(ev["ticker"].astype(str).str.upper().unique())
                               if hasattr(ev, "empty") and not ev.empty else [])
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
# D. Before/after comparison artifacts (adds 50bps cost robustness on top of 8-U's 25bps).
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
                        g(rb, "net_spread_50bps"), g(ra, "net_spread_50bps"),
                        sc in b_prom, sc in a_prom])
        d_ic = (g(ra, "mean_ic") - g(rb, "mean_ic")
                if g(ra, "mean_ic") is not None and g(rb, "mean_ic") is not None else None)
        d_t = (g(ra, "ic_t", 2) - g(rb, "ic_t", 2)
               if g(ra, "ic_t", 2) is not None and g(rb, "ic_t", 2) is not None else None)
        status = ("unchanged" if (sc in b_prom) == (sc in a_prom)
                  else ("newly_promoted" if sc in a_prom else "dropped"))
        delta_rows.append([sc, g(rb, "mean_ic"), g(ra, "mean_ic"),
                           _round(d_ic) if d_ic is not None else None,
                           g(rb, "ic_t", 2), g(ra, "ic_t", 2), _round(d_t, 2) if d_t is not None else None,
                           g(rb, "net_spread_25bps"), g(ra, "net_spread_25bps"),
                           g(rb, "net_spread_50bps"), g(ra, "net_spread_50bps"),
                           rb.get("subperiod_stable", ""), ra.get("subperiod_stable", ""), status])

    _write_csv(P.ba_alpha,
               ["scenario", "family", "mean_ic_before", "mean_ic_after", "ic_t_before", "ic_t_after",
                "spread_before", "spread_after", "net25_before", "net25_after",
                "net50_before", "net50_after", "promoted_before", "promoted_after"], ba_rows)
    _write_csv(P.robustness_delta,
               ["scenario", "ic_before", "ic_after", "ic_delta", "t_before", "t_after", "t_delta",
                "net25_before", "net25_after", "net50_before", "net50_after",
                "subperiod_stable_before", "subperiod_stable_after", "promotion_status"], delta_rows)

    _write_csv(P.scoreboard,
               ["scenario", "family", "cycle", "n_events", "n_months", "mean_ic", "ic_t",
                "bh_significant", "mean_spread", "net_spread_25bps", "net_spread_50bps",
                "subperiod_stable", "beats_placebo", "promoted"],
               [[r["scenario"], r["family"], r["cycle"], r.get("n_events", 0), r.get("n_months", 0),
                 g(r, "mean_ic"), g(r, "ic_t", 2), r.get("bh_significant", False),
                 g(r, "mean_spread"), g(r, "net_spread_25bps"), g(r, "net_spread_50bps"),
                 r.get("subperiod_stable", False), r.get("beats_placebo", False),
                 r["scenario"] in a_prom] for r in after["results"]])

    _write_csv(P.promoted,
               ["scenario", "family", "signal", "mean_ic", "ic_t", "mean_spread", "net_spread_25bps",
                "net_spread_50bps", "spread_hit_rate", "subperiod_stable", "n_events", "n_months",
                "new_vs_before"],
               [[r["scenario"], r["family"], r["signal"], g(r, "mean_ic"), g(r, "ic_t", 2),
                 g(r, "mean_spread"), g(r, "net_spread_25bps"), g(r, "net_spread_50bps"),
                 g(r, "spread_hit_rate"), r.get("subperiod_stable", False), r.get("n_events", 0),
                 r.get("n_months", 0), r["scenario"] not in b_prom] for r in after["promoted"]])

    _write_csv(P.rejected,
               ["scenario", "family", "signal", "mean_ic", "ic_t", "n_events", "reject_reason"],
               [[r["scenario"], r["family"], r["signal"], g(r, "mean_ic"), g(r, "ic_t", 2),
                 r.get("n_events", 0), r.get("reject_reason", "")] for r in after["rejected"]])


def write_scoreable_coverage(P, before: Dict, after: Dict, acq: Dict) -> List[str]:
    """Emit the expanded-scoreable coverage artifact and return the list of newly-scoreable
    tickers (present in the AFTER event table but not BEFORE)."""
    b_set = set(before["stats"].get("usable_tickers", []) or [])
    a_set = set(after["stats"].get("usable_tickers", []) or [])
    newly = sorted(a_set - b_set)
    rows = []
    for tk in sorted(a_set):
        rows.append([tk, tk in b_set, tk not in b_set,
                     tk in set(acq.get("price_acquired", [])),
                     tk in set(acq.get("fund_acquired", []))])
    # also list matched-but-not-yet-scoreable names acquired this run (price or earnings only)
    for tk in acq.get("matched_scoreable", []):
        if tk not in a_set:
            rows.append([tk, False, True, tk in set(acq.get("price_acquired", [])),
                         tk in set(acq.get("fund_acquired", []))])
    _write_csv(P.expanded_scoreable,
               ["ticker", "scoreable_before", "newly_scoreable", "price_acquired_this_run",
                "fundamentals_acquired_this_run"], rows)
    return newly


# --------------------------------------------------------------------------- #
# D. Decision.
# --------------------------------------------------------------------------- #
def derive_decision(acq: Dict, before: Dict, after: Dict, missing_count: int) -> Tuple[str, str]:
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
                    "The scoreable cross-section widened from %d to %d tickers via a MATCHED "
                    "price+fundamentals acquisition, and every Phase 8-T promoted signal still clears "
                    "the full robustness gate on the expanded universe (focus signals %s survive; "
                    "%d total promoted, none dropped). The alpha is confirmed out-of-universe; "
                    "preview-only, no orders."
                    % (b_score, a_score, "/".join(FOCUS_SIGNALS), len(a_prom)))
        return (DEC_WEAKENS,
                "The scoreable cross-section widened from %d to %d tickers but the promoted alpha "
                "degraded on it (%d signal(s) dropped below the gate: %s). The signal is less robust "
                "than the 8-T sample suggested; treat with caution. Preview-only, no orders."
                % (b_score, a_score, len(dropped),
                   ", ".join(sorted(dropped)) or "focus signal weakened"))

    matched = len(acq.get("matched_scoreable", []))
    if matched > 0:
        why = ("%d name(s) gained BOTH price and earnings this run but the rerun did not yet widen "
               "the scored cross-section (insufficient forward-return overlap or depth); acquire the "
               "next bounded combined batch to push past the event/months gates." % matched)
    elif acq.get("executed"):
        why = ("acquisition ran but no name gained BOTH a price panel and a point-in-time earnings "
               "event together; continue the matched batch for the remaining names.")
    else:
        why = ("no acquisition this run (offline, or all targets already cached); set EODHD_API_KEY "
               "and re-run --live to fetch the bounded next combined batch.")
    return (DEC_NEXT_BATCH,
            "Combined expansion did not enlarge the scoreable cross-section (still %d tickers): %s "
            "%d S&P-500 name(s) remain without a matched price+earnings pair. Preview-only, no orders."
            % (a_score, why, missing_count))


def _next_step(decision: str, missing_count: int) -> Tuple[str, str]:
    if decision in (DEC_CONFIRMED, DEC_WEAKENS):
        return ("Review research/output/phase8v_combined_eodhd_universe_expansion/"
                "before_after_alpha_comparison.csv, then carry the expanded-universe verdict into the "
                "Paper Trader daily-review cockpit as PREVIEW-ONLY ranking ideas (manual review; no "
                "orders).",
                "Carry the expanded-universe alpha verdict to preview-only review")
    if decision == DEC_NEXT_BATCH:
        return ("$env:EODHD_API_KEY = '<PAID_EODHD_KEY>'; python "
                "research/run_phase8v_combined_eodhd_universe_expansion.py --live --max-tickers 250 "
                "--max-requests 1000 --start-date 2016-01-01   (acquire the next bounded MATCHED "
                "price+fundamentals batch for the remaining %d names)" % missing_count,
                "Acquire the next bounded combined price+fundamentals batch, then re-test")
    if decision == DEC_BLOCKER:
        return ("Resolve the blocker (set a valid PAID EODHD_API_KEY with fundamentals entitlement), "
                "then re-run: python research/run_phase8v_combined_eodhd_universe_expansion.py --live",
                "Clear the hard blocker, then re-run the combined expansion")
    return ("python research/run_phase8v_combined_eodhd_universe_expansion.py",
            "Re-run the combined expansion")


# --------------------------------------------------------------------------- #
# Orchestration.
# --------------------------------------------------------------------------- #
class _Paths:
    """8-V committed-safe output paths + the reused 8-S/8-R data-tree handle."""

    def __init__(self, out_dir=None, data_dir=None, phase8n_dir=None, price_csv=None,
                 sector_csv=None, macro=None, phase8r_dir=None, phase8t_dir=None, sp500_html=None):
        self.out = Path(out_dir) if out_dir else _OUT_DIR
        self._score = u8._mk_scoring_paths(self.out, data_dir, phase8n_dir, price_csv, sector_csv,
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


def run(live: bool = False, transport: Optional[CombinedTransport] = None,
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

        # Read Phase 8-T outputs (recorded baseline) + note the 8-U finding this phase acts on.
        t8_report = _read_json(P.phase8t / "phase8t_autonomous_alpha_daemon.json")
        t8_decision = _read_json(P.phase8t / "final_research_decision.json")
        t8_promoted = (t8_decision.get("promoted_signals")
                       or t8_report.get("promoted_signals") or [])
        u8_report = _read_json(_PHASE8U_DIR / "phase8u_eodhd_price_universe_expansion.json")
        log.step("prior", "INFO", "Phase 8-T decision: %s; promoted: %s; 8-U decision: %s"
                 % (t8_decision.get("decision") or t8_report.get("decision") or "(none read)",
                    ", ".join(t8_promoted) if t8_promoted else "(none)",
                    u8_report.get("decision", "(none read)")))
        log.step("start", "INFO",
                 "live=%s key_present=%s max_tickers=%d max_requests=%d start=%s"
                 % (live, present, max_tickers, max_requests, start_date))

        # A. combined coverage diff.
        sp500 = u8.parse_sp500_list(P.sp500_html)
        priced = s8._price_cache_tickers(P.base_price_csv)
        earnings_cached = set(r8._cached_tickers(P.r8))
        fund_cached = _raw_fund_cached_tickers(P)
        existing, missing = build_combined_coverage(sp500, priced, earnings_cached, fund_cached)
        _write_csv(P.sp500_extraction,
                   ["rank", "ticker", "in_price_cache", "has_earnings_cache", "has_fundamentals_cache"],
                   [[i + 1, tk, tk in set(priced), tk in earnings_cached, tk in fund_cached]
                    for i, tk in enumerate(sp500)])
        _write_csv(P.existing_coverage,
                   ["ticker", "has_price", "has_earnings", "has_fundamentals", "scoreable_ready"],
                   [[r["ticker"], r["has_price"], r["has_earnings"], r["has_fundamentals"],
                     r["scoreable_ready"]] for r in existing])
        _write_csv(P.missing_tickers,
                   ["ticker", "has_price", "has_earnings", "has_fundamentals", "need_price",
                    "need_fundamentals", "scoreable_after_combined"],
                   [[r["ticker"], r["has_price"], r["has_earnings"], r["has_fundamentals"],
                     r["need_price"], r["need_fundamentals"], r["scoreable_after_combined"]]
                    for r in missing])
        log.step("diff", "DONE",
                 "sp500=%d priced=%d scoreable_ready=%d missing_combined=%d (need_price=%d need_fund=%d)"
                 % (len(sp500), len(priced), sum(1 for e in existing if e["scoreable_ready"]),
                    len(missing), sum(1 for m in missing if m["need_price"]),
                    sum(1 for m in missing if m["need_fundamentals"])), count=len(missing))

        # B. bounded MATCHED price+fundamentals acquisition.
        acq = acquire_combined(P, missing, live, transport, max_tickers, max_requests, start_date,
                               skip_existing, log)

        # C. expanded price panel (reuses 8-U; new prices merged with the base cache).
        panel_csv, panel_manifest = u8.build_expanded_panel(P, P.base_price_csv,
                                                            acq["price_acquired"], log)

        # D. rerun the 8-T scoring CORE: BEFORE (base panel) and AFTER (expanded panel). The shared
        #    data tree now also carries the newly acquired earnings/fundamentals, so AFTER scores any
        #    name that gained BOTH a price (in the expanded panel) AND an earnings event.
        P_before = u8._mk_scoring_paths(P.out, P.data_dir, P.phase8n_dir, P.base_price_csv,
                                        P.sector_csv, P.macro, P.phase8r_dir)
        before = run_core_campaign(P_before, max_cycles, as_of, log, "before")
        if acq["price_acquired"]:
            P_after = u8._mk_scoring_paths(P.out, P.data_dir, P.phase8n_dir, panel_csv,
                                           P.sector_csv, P.macro, P.phase8r_dir)
            after = run_core_campaign(P_after, max_cycles, as_of, log, "after")
        else:
            after = before  # no new prices -> expanded panel == base -> identical campaign
            log.step("core_after", "REUSE", "no new prices; AFTER reuses BEFORE")

        # E. comparison + scoreable-coverage artifacts.
        write_comparison_artifacts(P, before, after)
        newly_scoreable = write_scoreable_coverage(P, before, after, acq)

        # F. decision.
        decision, rationale = derive_decision(acq, before, after, len(missing))
        log.step("decision", "DONE", decision)

        b_prom = [p["scenario"] for p in before["promoted"]]
        a_prom = [p["scenario"] for p in after["promoted"]]
        a_prom_set = set(a_prom)
        new_alpha = sorted(a_prom_set - set(b_prom))
        ssn_survives = "surprise_sector_neutral" in a_prom_set
        sxq_survives = "surprise_x_quality" in a_prom_set

        next_cmd, next_title = _next_step(decision, len(missing))
        _write_json(P.next_plan, {
            "phase": "8-W", "title": next_title, "depends_on_decision": decision,
            "next_command": next_cmd, "missing_combined_names": len(missing),
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
                    ["raw_normalized_data_gitignored", True,
                     "research/data/eodhd/{raw,normalized}/{eod_prices,fundamentals} force-gitignored"]])

        report = {
            "phase": PHASE,
            "objective": ("Bounded MATCHED EODHD price+fundamentals universe expansion: for the S&P-500 "
                          "names missing a price OR an earnings event, acquire BOTH EOD prices and "
                          "fundamentals, build an expanded combined panel, rerun the Phase 8-T scoring "
                          "core, and compare the promoted alpha before vs after. Preview-only; no orders."),
            "provider": PROVIDER_NAME, "api_key_env_var": API_KEY_ENV,
            "builds_on_phase8t_decision": t8_decision.get("decision") or t8_report.get("decision", ""),
            "builds_on_phase8u_decision": u8_report.get("decision", ""),
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
                "scoreable_ready_before": sum(1 for e in existing if e["scoreable_ready"]),
                "missing_combined_tickers": len(missing),
                "missing_need_price": sum(1 for m in missing if m["need_price"]),
                "missing_need_fundamentals": sum(1 for m in missing if m["need_fundamentals"])},
            "acquisition": {"executed": acq.get("executed"), "requests_made": acq.get("requests_made"),
                            "prices_acquired": len(acq.get("price_acquired", [])),
                            "fundamentals_acquired": len(acq.get("fund_acquired", [])),
                            "matched_scoreable": len(acq.get("matched_scoreable", [])),
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
            "newly_scoreable_tickers": newly_scoreable,
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
            "required_artifacts": [_ARTIFACTS[k] for k in _REQUIRED_ARTIFACTS],
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
    print("missing combined:      %s (need_price=%s need_fund=%s)"
          % (uni.get("missing_combined_tickers"), uni.get("missing_need_price"),
             uni.get("missing_need_fundamentals")))
    acq = report.get("acquisition", {})
    print("prices / fundamentals: %s / %s (requests %s, matched-scoreable %s)"
          % (acq.get("prices_acquired"), acq.get("fundamentals_acquired"),
             acq.get("requests_made"), acq.get("matched_scoreable")))
    print("scoreable before/after:%s / %s" % (report.get("before", {}).get("scoreable_tickers"),
                                              report.get("after", {}).get("scoreable_tickers")))
    print("ssn survives:          %s" % report.get("surprise_sector_neutral_survives"))
    print("sxq survives:          %s" % report.get("surprise_x_quality_survives"))
    print("new alpha:             %s" % report.get("new_alpha_on_expanded_universe"))
    print("decision:              %s" % report.get("decision"))
    print("next command:          %s" % report.get("recommended_next_command"))


def _parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        description=("Phase 8-V combined EODHD price+fundamentals universe expansion (offline rerun "
                     "on cached data by default; bounded live matched acquisition with --live + "
                     "EODHD_API_KEY)."))
    ap.add_argument("--live", action="store_true",
                    help="Bounded live MATCHED EODHD price+fundamentals acquisition (needs EODHD_API_KEY).")
    ap.add_argument("--max-tickers", type=int, default=DEFAULT_MAX_TICKERS)
    ap.add_argument("--max-requests", type=int, default=DEFAULT_MAX_REQUESTS)
    ap.add_argument("--start-date", default=DEFAULT_START_DATE)
    ap.add_argument("--skip-existing", default="true",
                    help="Skip the leg(s) whose data is already cached (default true).")
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
