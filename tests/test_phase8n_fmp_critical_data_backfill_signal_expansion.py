"""Tests for Phase 8-N - Controlled FMP Critical-Data Backfill + Signal Expansion.

Proves the brief's acceptance criteria, fully offline (no key, no network):
  * the FMP key is never printed or written; raw/normalized paid data stay gitignored;
  * raw paid payloads never appear in committed artifacts (coverage metadata only);
  * key_metrics/ratios are skipped by default (the 402 entitlement block) and never requested;
  * critical earnings/analyst families are prioritized (critical-first; cap can't drop them);
  * the request plan + live batch are bounded (<=25 tickers, <=150 requests);
  * skip-existing is honored - cached paid data is reused read-only and never overwritten;
  * committed URLs are redacted (no "apikey=" marker anywhere);
  * coverage reports + signal-expansion scoreboards exist;
  * no fabricated alpha: an alpha verdict needs sufficient coverage AND a real score;
  * no Paper Trader / GCP / order / deployment logic; committed-safe outputs only.

The live backfill is exercised via an injected ``transport`` so the suite needs neither
FMP_API_KEY nor a network. Module-scoped fixtures write to isolated tmp dirs.
"""
from __future__ import annotations

import csv
import importlib.util
import json
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def _load():
    path = (_REPO_ROOT / "research"
            / "run_phase8n_fmp_critical_data_backfill_signal_expansion.py")
    spec = importlib.util.spec_from_file_location("phase8n_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


P8N = _load()
import research.providers.fmp_client as fmp  # noqa: E402

_UNIVERSE_22 = ["U%02d" % i for i in range(22)]  # 22 synthetic tickers (>= MIN_TICKERS_TO_SCORE)


def _read_csv(path: Path):
    with open(path, "r", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _seed_cache(raw_dir: Path, families, tickers):
    """Write small realistic raw payloads for (family, ticker) under the gitignored cache."""
    for fam in families:
        for tk in tickers:
            d = raw_dir / fam
            d.mkdir(parents=True, exist_ok=True)
            payload = [{"symbol": tk, "date": "2026-04-30", "epsActual": 2.0,
                        "epsEstimated": 1.9, "revenueActual": 1, "revenueEstimated": 1}]
            with open(d / ("%s.json" % tk), "w", encoding="utf-8") as fh:
                json.dump(payload, fh)


# --------------------------------------------------------------------------- #
# Transports (offline; no key, no network).
# --------------------------------------------------------------------------- #
def sim_transport(path: str):
    """Critical endpoints return data, except price-target (empty); blocked families 402."""
    p = path.lower()
    if "ratios" in p or "key-metrics" in p:
        raise fmp.FmpError("HTTP 402", status_code=402, error_type="http_error")
    if "price-target" in p:
        return []  # 200 empty -> EMPTY_REACHABLE -> 0 coverage
    return [{"symbol": "X", "date": "2026-01-01", "epsActual": 1.0, "epsEstimated": 0.9}]


def blocked_transport(path: str):
    """Every endpoint 402/subscription-blocked (key valid, plan not entitled)."""
    raise fmp.FmpError("HTTP 402", status_code=402, error_type="http_error")


def mixed_transport(path: str):
    """Earnings family 402-blocked; analyst families return data (the MIXED scenario)."""
    p = path.lower()
    if "/stable/earnings?" in p or "earnings?symbol" in p:
        raise fmp.FmpError("HTTP 402", status_code=402, error_type="http_error")
    return [{"symbol": "X", "date": "2026-01-01", "epsActual": 1.0, "epsEstimated": 0.9}]


def one_ticker_blocked_transport(path: str):
    """Only ticker U05 is 402-blocked; every other ticker returns data."""
    if "symbol=u05" in path.lower():
        raise fmp.FmpError("HTTP 402", status_code=402, error_type="http_error")
    return [{"symbol": "X", "date": "2026-01-01", "epsActual": 1.0, "epsEstimated": 0.9}]


def auth_fail_transport(path: str):
    """Every request is 401 invalid-auth (the key itself is bad)."""
    raise fmp.FmpError("HTTP 401", status_code=401, error_type="http_error")


def rate_limit_transport(path: str):
    """Every request is 429 rate-limited (persists through the bounded retry)."""
    raise fmp.FmpError("HTTP 429", status_code=429, error_type="http_error")


_SCORES = {
    "S8N-EARNSURP-RATES": {"confirmed": True, "ev_after_costs": 0.004, "recent_lift": 0.003,
                           "score": 0.7},
    "S8N-ESTREV-RATES": {"confirmed": False, "ev_after_costs": 0.002, "recent_lift": 0.001,
                         "score": 0.4},
    "S8N-RECCHG-SECTOR": {"confirmed": False, "ev_after_costs": -0.001, "recent_lift": -0.002,
                          "score": 0.1},
}


# --------------------------------------------------------------------------- #
# Module-scoped runs (isolated tmp dirs).
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def cached_run(tmp_path_factory):
    """Offline run over a pre-seeded 3-ticker critical cache (no transport, no key)."""
    out = tmp_path_factory.mktemp("p8n_cached_out")
    data = tmp_path_factory.mktemp("p8n_cached_data")
    _seed_cache(data / "raw", P8N.CRITICAL_FAMILIES, ["AAPL", "MSFT", "NVDA"])
    rep = P8N.run(live=False, tickers=["AAPL", "MSFT", "NVDA"], out_dir=Path(out),
                  data_dir=Path(data), verbose=False)
    return {"report": rep, "out": Path(out), "data": Path(data)}


@pytest.fixture(scope="module")
def sim_run(tmp_path_factory):
    """Simulated live backfill across 22 tickers with injected scores."""
    out = tmp_path_factory.mktemp("p8n_sim_out")
    data = tmp_path_factory.mktemp("p8n_sim_data")
    rep = P8N.run(live=False, tickers=_UNIVERSE_22, transport=sim_transport,
                  injected_scores=_SCORES, out_dir=Path(out), data_dir=Path(data),
                  verbose=False)
    return {"report": rep, "out": Path(out), "data": Path(data)}


@pytest.fixture(scope="module")
def blocked_run(tmp_path_factory):
    """Every critical family 402-blocked. With continue-on-subscription-block (default) the
    batch does NOT stop early - all 6 families x 22 tickers are attempted - and the decision
    is FMP_PLAN_COVERAGE_INSUFFICIENT (not a premature rejection)."""
    out = tmp_path_factory.mktemp("p8n_blk_out")
    data = tmp_path_factory.mktemp("p8n_blk_data")
    rep = P8N.run(live=False, tickers=_UNIVERSE_22, transport=blocked_transport,
                  out_dir=Path(out), data_dir=Path(data), verbose=False)
    return {"report": rep, "out": Path(out), "data": Path(data)}


@pytest.fixture(scope="module")
def mixed_run(tmp_path_factory):
    """Earnings 402-blocked, analyst families work -> MIXED_..._ALTERNATIVE_EARNINGS_SOURCE."""
    out = tmp_path_factory.mktemp("p8n_mix_out")
    data = tmp_path_factory.mktemp("p8n_mix_data")
    rep = P8N.run(live=False, tickers=_UNIVERSE_22, transport=mixed_transport,
                  out_dir=Path(out), data_dir=Path(data), verbose=False)
    return {"report": rep, "out": Path(out), "data": Path(data)}


# --------------------------------------------------------------------------- #
# Vocabulary / structure exactness.
# --------------------------------------------------------------------------- #
def test_allowed_decisions_include_original_seven_plus_patch_three():
    # Original 8-M seven contract values...
    for d in ("PROVIDER_EXPANDED_CONFIRMED_ALPHA_FOUND", "PROVIDER_EXPANDED_PROMISING_ALPHA_FOUND",
              "READY_FOR_NEXT_FMP_BATCH", "NEEDS_MORE_FMP_COVERAGE", "BLOCKED_MISSING_FMP_KEY",
              "PROVIDER_EXPANSION_REJECTED", "ERROR"):
        assert d in P8N.ALLOWED_DECISIONS
    # ...plus the three coverage/entitlement outcomes added by the 8-N controller patch.
    for d in ("READY_FOR_PROVIDER_EXPANDED_SIGNAL_SCORING", "FMP_PLAN_COVERAGE_INSUFFICIENT",
              "MIXED_FMP_COVERAGE_NEEDS_ALTERNATIVE_EARNINGS_SOURCE"):
        assert d in P8N.ALLOWED_DECISIONS
    assert len(P8N.ALLOWED_DECISIONS) == 10
    assert len(set(P8N.ALLOWED_DECISIONS)) == 10  # no duplicates


def test_six_critical_families():
    assert P8N.CRITICAL_FAMILIES == (
        "earnings_surprises", "earnings_calendar", "analyst_estimates",
        "analyst_recommendations", "analyst_price_targets", "ratings_grades_consensus")


def test_bounded_limit_constants():
    assert P8N.CEIL_MAX_TICKERS == 25
    assert P8N.CEIL_MAX_REQUESTS == 150
    assert P8N.DEFAULT_MAX_ENDPOINT_FAMILIES == 6


def test_artifact_count():
    # 24 from the 8-M build + 4 added by the 8-N controller patch (entitlement matrix,
    # subscription-block pattern, provider upgrade decision, cheaper-provider alternative).
    assert len(P8N._ARTIFACTS) == 28
    for key in ("entitlement_matrix", "block_pattern", "upgrade_decision", "alt_provider"):
        assert key in P8N._ARTIFACTS


# --------------------------------------------------------------------------- #
# Controller: critical-first; cap can't drop critical; key_metrics/ratios excluded.
# --------------------------------------------------------------------------- #
def test_default_families_are_six_criticals():
    fams, _ = P8N.build_backfill_families()
    names = [e["family"] for e in fams]
    assert names == list(P8N.CRITICAL_FAMILIES)
    assert all(e["critical"] for e in fams)


def test_key_metrics_ratios_skipped_by_default():
    fams, _ = P8N.build_backfill_families()
    names = {e["family"] for e in fams}
    assert "key_metrics_quarterly" not in names
    assert "ratios_quarterly" not in names


def test_cap_cannot_drop_critical():
    for cap in (0, 1, 3, 5):
        fams, notes = P8N.build_backfill_families(max_endpoint_families=cap)
        assert len([e for e in fams if e["critical"]]) == 6, "cap=%d dropped a critical" % cap
        assert any("OVERRIDING" in n for n in notes)


def test_critical_first_ordering():
    fams, _ = P8N.build_backfill_families(max_endpoint_families=8, include_optional=True)
    crit_idx = [i for i, e in enumerate(fams) if e["critical"]]
    noncrit_idx = [i for i, e in enumerate(fams) if not e["critical"]]
    if crit_idx and noncrit_idx:
        assert max(crit_idx) < min(noncrit_idx)


# --------------------------------------------------------------------------- #
# Bounded request plan + live batch.
# --------------------------------------------------------------------------- #
def test_request_plan_bounded(sim_run):
    rows = _read_csv(sim_run["out"] / "fmp_backfill_request_plan.csv")
    assert len(rows) == 6 * 22  # 6 families x 22 tickers
    assert sim_run["report"]["universe_size"] <= P8N.CEIL_MAX_TICKERS


def test_requests_within_budget(sim_run):
    assert sim_run["report"]["requests_made"] <= P8N.CEIL_MAX_REQUESTS


def test_max_tickers_clamped():
    # Asking for 100 tickers is clamped to the 25 ceiling.
    uni, _ = P8N.resolve_universe(100, _REPO_ROOT / "nonexistent", explicit=None)
    assert len(uni) <= P8N.CEIL_MAX_TICKERS


def test_do_not_spend_never_requested(tmp_path):
    """key_metrics/ratios paths are never sent to the transport on a default backfill."""
    seen = []

    def recording_transport(path):
        seen.append(path.lower())
        return [{"symbol": "X", "date": "2026-01-01"}]

    P8N.run(live=False, tickers=["AAA", "BBB"], transport=recording_transport,
            out_dir=tmp_path / "o", data_dir=tmp_path / "d", verbose=False)
    assert seen, "transport was never called"
    assert not any("ratios" in p or "key-metrics" in p for p in seen)


# --------------------------------------------------------------------------- #
# Skip-existing protects already-collected paid data.
# --------------------------------------------------------------------------- #
def test_skip_existing_reuses_and_never_overwrites(tmp_path):
    data = tmp_path / "data"
    raw = data / "raw"
    _seed_cache(raw, ["earnings_surprises"], ["AAA"])
    seeded = raw / "earnings_surprises" / "AAA.json"
    before = seeded.read_text(encoding="utf-8")
    seen = []

    def recording_transport(path):
        seen.append(path)
        return [{"symbol": "X", "date": "2026-01-01"}]

    rep = P8N.run(live=False, tickers=["AAA"], transport=recording_transport,
                  out_dir=tmp_path / "o", data_dir=data, verbose=False)
    # The seeded cell was reused (CACHED), so the transport was never asked for it.
    assert not any("earnings?symbol=AAA" in p.lower() for p in seen)
    assert seeded.read_text(encoding="utf-8") == before  # file untouched
    assert rep["any_cached_reused"] is True
    prog = _read_csv((tmp_path / "o") / "fmp_backfill_progress_report.csv")
    cached = [r for r in prog if r["endpoint_family"] == "earnings_surprises"
              and r["ticker"] == "AAA"]
    assert cached and cached[0]["status"] == P8N.ACQ_CACHED
    assert cached[0]["request_spent"] == "False"


# --------------------------------------------------------------------------- #
# Cached offline run: coverage, manifests, decision.
# --------------------------------------------------------------------------- #
def test_cached_run_all_artifacts(cached_run):
    out = cached_run["out"]
    for name in P8N._ARTIFACTS.values():
        assert (out / name).is_file(), "missing artifact %s" % name


def test_cached_run_coverage_and_decision(cached_run):
    rep = cached_run["report"]
    assert rep["mode"] == "offline_normalize"
    assert rep["requests_made"] == 0
    for fam in P8N.CRITICAL_FAMILIES:
        assert rep["coverage_counts"][fam] == 3
        assert rep["family_status"][fam] == P8N.ACQ_CACHED
    # 3 tickers < MIN_TICKERS_TO_SCORE -> insufficient -> collect the next batch.
    assert rep["decision"] == P8N.DEC_READY_NEXT_BATCH
    assert rep["next_fmp_batch_required"] is True


def test_cached_run_no_fabricated_alpha(cached_run):
    rep = cached_run["report"]
    assert rep["confirmed_signal_count"] == 0
    assert rep["promising_signal_count"] == 0
    for v in rep["signal_verdicts"].values():
        assert v == P8N.VER_INSUFFICIENT


def test_signal_ready_coverage_report(cached_run):
    rows = _read_csv(cached_run["out"] / "fmp_signal_ready_coverage_report.csv")
    assert len(rows) == 6
    for r in rows:
        assert r["ready_to_score"] == "False"
        assert int(r["ticker_gap"]) == P8N.MIN_TICKERS_TO_SCORE - 3


def test_earnings_manifest_is_metadata_only(cached_run):
    rows = _read_csv(cached_run["out"] / "expanded_earnings_event_manifest.csv")
    assert rows
    cols = set(rows[0].keys())
    assert {"ticker", "source_family", "n_events", "first_event_date",
            "last_event_date"}.issubset(cols)
    # Metadata only - no licensed numeric value column leaks the actual EPS.
    assert "epsActual" not in cols and "epsEstimated" not in cols


# --------------------------------------------------------------------------- #
# Simulated live backfill: scoring + decision paths.
# --------------------------------------------------------------------------- #
def test_sim_all_artifacts(sim_run):
    out = sim_run["out"]
    for name in P8N._ARTIFACTS.values():
        assert (out / name).is_file(), "missing artifact %s" % name


def test_sim_confirmed_decision(sim_run):
    rep = sim_run["report"]
    assert rep["mode"] == "live_backfill"
    assert rep["decision"] == P8N.DEC_CONFIRMED
    assert rep["confirmed_signal_count"] == 1
    assert rep["promising_signal_count"] == 1


def test_sim_scoreboard_verdicts(sim_run):
    rows = {r["signal_id"]: r for r in
            _read_csv(sim_run["out"] / "provider_expanded_signal_scoreboard.csv")}
    assert rows["S8N-EARNSURP-RATES"]["verdict"] == P8N.VER_CONFIRMED
    assert rows["S8N-ESTREV-RATES"]["verdict"] == P8N.VER_PROMISING
    assert rows["S8N-RECCHG-SECTOR"]["verdict"] == P8N.VER_REJECTED
    # price-target returned empty -> 0 coverage -> insufficient (no fabricated score)
    assert rows["S8N-PTREV-SENS"]["verdict"] == P8N.VER_INSUFFICIENT


def test_sim_confirmed_promising_split_files(sim_run):
    conf = _read_csv(sim_run["out"] / "confirmed_alpha_signals.csv")
    prom = _read_csv(sim_run["out"] / "promising_alpha_signals.csv")
    rej = _read_csv(sim_run["out"] / "rejected_alpha_signals.csv")
    assert [r["signal_id"] for r in conf] == ["S8N-EARNSURP-RATES"]
    assert [r["signal_id"] for r in prom] == ["S8N-ESTREV-RATES"]
    assert [r["signal_id"] for r in rej] == ["S8N-RECCHG-SECTOR"]


def test_sim_trade_idea_registry_only_tradeable(sim_run):
    rows = _read_csv(sim_run["out"] / "trade_idea_candidate_registry.csv")
    ids = {r["signal_id"] for r in rows}
    assert ids == {"S8N-EARNSURP-RATES", "S8N-ESTREV-RATES"}  # confirmed + promising only


def test_blocked_run_plan_insufficient_not_rejected(blocked_run):
    rep = blocked_run["report"]
    # The CORE FIX: a 402 no longer halts the batch. Every critical family is attempted
    # across all 22 tickers, so the decision reflects the plan's coverage, not a premature stop.
    assert rep["decision"] == P8N.DEC_FMP_PLAN_INSUFFICIENT
    assert rep["stopped_early"] is False
    assert rep["stop_reason"] == ""
    # All six families were reached (6 x 22 = 132 requests, within the 150 budget).
    assert rep["requests_made"] == 6 * len(_UNIVERSE_22)
    assert all(rep["coverage_counts"][fam] == 0 for fam in P8N.CRITICAL_FAMILIES)
    assert all(rep["family_entitlement"][fam] == P8N.ENT_BLOCKED for fam in P8N.CRITICAL_FAMILIES)
    assert rep["missing_blocked_families"] == list(P8N.CRITICAL_FAMILIES)
    assert rep["cheaper_provider_alternatives_recommended"] is True
    assert rep["confirmed_signal_count"] == 0 and rep["promising_signal_count"] == 0


def test_error_report_distinguishes_blocked(blocked_run):
    rows = _read_csv(blocked_run["out"] / "fmp_error_report.csv")
    assert rows
    assert all(r["status"] == P8N.ACQ_SUBSCRIPTION_BLOCKED for r in rows)


# --------------------------------------------------------------------------- #
# CONTROLLER FIX: a 402 must not stop the batch (per ticker AND per endpoint family).
# --------------------------------------------------------------------------- #
def test_402_on_one_ticker_does_not_stop_batch(tmp_path):
    rep = P8N.run(live=False, tickers=_UNIVERSE_22, transport=one_ticker_blocked_transport,
                  out_dir=tmp_path / "o", data_dir=tmp_path / "d", verbose=False)
    # One ticker (U05) is 402-blocked in every family; the rest collect. The batch finishes.
    assert rep["stopped_early"] is False
    assert rep["requests_made"] == 6 * len(_UNIVERSE_22)
    for fam in P8N.CRITICAL_FAMILIES:
        # 22 tickers minus the one blocked ticker => 21 covered (still >= the score minimum).
        assert rep["coverage_counts"][fam] == len(_UNIVERSE_22) - 1
    # Every family cleared the minimum -> ready to score across the board.
    assert rep["decision"] == P8N.DEC_READY_FOR_SCORING


def test_402_on_one_family_does_not_block_next_family(mixed_run):
    rep = mixed_run["report"]
    # Earnings families are fully 402-blocked but the analyst families that come AFTER them
    # in the critical-first order still run and collect data (proves no global halt).
    assert rep["stopped_early"] is False
    for fam in ("earnings_surprises", "earnings_calendar"):
        assert rep["coverage_counts"][fam] == 0
        assert rep["family_entitlement"][fam] == P8N.ENT_BLOCKED
    for fam in ("analyst_estimates", "analyst_recommendations", "analyst_price_targets",
                "ratings_grades_consensus"):
        assert rep["coverage_counts"][fam] == len(_UNIVERSE_22)
        assert rep["family_entitlement"][fam] == P8N.ENT_ENTITLED
    # Earnings blocked but analyst works -> need an alternative earnings source.
    assert rep["decision"] == P8N.DEC_MIXED_NEEDS_ALT_EARNINGS


def test_continue_on_subscription_block_defaults_true():
    # Both the parsed CLI flag and the report field default to True.
    args = P8N._parse_args([])
    assert args.continue_on_block is True
    args_off = P8N._parse_args(["--no-continue-on-subscription-block"])
    assert args_off.continue_on_block is False


def test_continue_on_block_reported_true(blocked_run):
    assert blocked_run["report"]["continue_on_subscription_block"] is True


def test_legacy_no_continue_stops_on_repeated_blocks(tmp_path):
    rep = P8N.run(live=False, tickers=_UNIVERSE_22, transport=blocked_transport,
                  continue_on_subscription_block=False,
                  out_dir=tmp_path / "o", data_dir=tmp_path / "d", verbose=False)
    # Legacy mode: repeated 402s DO halt the batch (opt-in only).
    assert rep["stopped_early"] is True
    assert rep["stop_reason"] == "subscription_block_legacy_stop"
    assert rep["requests_made"] == P8N.STOP_AFTER_CONSEC_SUBSCRIPTION_BLOCKS


def test_max_requests_honored(tmp_path):
    rep = P8N.run(live=False, tickers=_UNIVERSE_22, transport=one_ticker_blocked_transport,
                  max_requests=10, out_dir=tmp_path / "o", data_dir=tmp_path / "d",
                  verbose=False)
    assert rep["requests_made"] <= 10
    assert rep["requests_remaining"] == max(0, 10 - rep["requests_made"])


def test_max_requests_clamped_to_ceiling(tmp_path):
    rep = P8N.run(live=False, tickers=_UNIVERSE_22, transport=one_ticker_blocked_transport,
                  max_requests=10_000, out_dir=tmp_path / "o", data_dir=tmp_path / "d",
                  verbose=False)
    assert rep["requests_made"] <= P8N.CEIL_MAX_REQUESTS


def test_repeated_401_stops_batch_invalid_auth(tmp_path):
    rep = P8N.run(live=False, tickers=_UNIVERSE_22, transport=auth_fail_transport,
                  out_dir=tmp_path / "o", data_dir=tmp_path / "d", verbose=False)
    # A bad key (repeated 401) is a genuine global stop reason.
    assert rep["stopped_early"] is True
    assert rep["stop_reason"] == "repeated_invalid_auth_401"
    assert rep["requests_made"] == P8N.STOP_AFTER_CONSEC_AUTH_ERRORS


def test_rate_limit_exhaustion_stops_batch(tmp_path):
    rep = P8N.run(live=False, tickers=_UNIVERSE_22, transport=rate_limit_transport,
                  out_dir=tmp_path / "o", data_dir=tmp_path / "d", verbose=False)
    # 429 after the bounded retry/backoff is a genuine global stop reason.
    assert rep["stopped_early"] is True
    assert rep["stop_reason"] == "rate_limit_exhausted_429"
    assert rep["requests_made"] == P8N.STOP_AFTER_CONSEC_RATE_LIMITS


def test_classify_error_separates_auth_from_subscription():
    assert P8N.classify_error(401, "http_error") == P8N.ACQ_AUTH_ERROR
    assert P8N.classify_error(402, "http_error") == P8N.ACQ_SUBSCRIPTION_BLOCKED
    assert P8N.classify_error(403, "http_error") == P8N.ACQ_SUBSCRIPTION_BLOCKED
    assert P8N.classify_error(429, "http_error") == P8N.ACQ_RATE_LIMITED
    assert P8N.classify_error(500, "http_error") == P8N.ACQ_ENDPOINT_ERROR


# --------------------------------------------------------------------------- #
# New entitlement / block-pattern / provider-alternative artifacts.
# --------------------------------------------------------------------------- #
def test_entitlement_matrix_produced(blocked_run):
    rows = _read_csv(blocked_run["out"] / "fmp_family_entitlement_matrix.csv")
    assert len(rows) == 6
    cols = set(rows[0].keys())
    assert {"endpoint_family", "attempted", "blocked_402_403", "coverage", "entitlement",
            "ready_to_score", "broadly_blocked"}.issubset(cols)
    assert all(r["entitlement"] == P8N.ENT_BLOCKED for r in rows)


def test_subscription_block_pattern_report_produced(blocked_run):
    rows = _read_csv(blocked_run["out"] / "fmp_subscription_block_pattern_report.csv")
    assert len(rows) == 6
    cols = set(rows[0].keys())
    assert {"endpoint_family", "block_fraction", "pattern", "interpretation"}.issubset(cols)
    # Every attempt blocked -> TOTAL pattern.
    assert all(r["pattern"] == "TOTAL" for r in rows)


def test_provider_upgrade_decision_produced(blocked_run):
    rows = _read_csv(blocked_run["out"] / "fmp_provider_upgrade_decision.csv")
    overall = [r for r in rows if r["endpoint_family"] == "__overall__"]
    assert overall, "missing overall upgrade decision row"
    assert overall[0]["recommended_action"] == "UPGRADE_NOT_RECOMMENDED_USE_ALTERNATIVE"
    assert blocked_run["report"]["provider_upgrade_decision"] == (
        "UPGRADE_NOT_RECOMMENDED_USE_ALTERNATIVE")


def test_cheaper_provider_alternative_report_produced(blocked_run):
    rows = _read_csv(blocked_run["out"] / "fmp_cheaper_provider_alternative_report.csv")
    assert rows
    cols = set(rows[0].keys())
    assert {"missing_family", "candidate_provider", "approx_monthly_cost", "covers_family",
            "recommended"}.issubset(cols)
    fams = {r["missing_family"] for r in rows}
    assert fams == set(P8N.CRITICAL_FAMILIES)
    # Each missing family recommends exactly one cheapest source, and Ultimate is never it.
    for fam in P8N.CRITICAL_FAMILIES:
        recs = [r for r in rows if r["missing_family"] == fam and r["recommended"] == "True"]
        assert len(recs) == 1
    assert not any("ultimate" in r["candidate_provider"].lower() for r in rows)


def test_no_alternative_recommended_when_nothing_blocked(cached_run):
    # No entitlement block -> the cheaper-provider report records "no alternative needed".
    rep = cached_run["report"]
    assert rep["missing_blocked_families"] == []
    assert rep["cheaper_provider_alternatives_recommended"] is False
    assert rep["provider_upgrade_decision"] == "NO_UPGRADE_NEEDED"
    rows = _read_csv(cached_run["out"] / "fmp_cheaper_provider_alternative_report.csv")
    assert any(r["missing_family"] == "(none)" for r in rows)


# --------------------------------------------------------------------------- #
# Coverage + multiple-testing reports.
# --------------------------------------------------------------------------- #
def test_ticker_coverage_by_family_exists(cached_run):
    rows = _read_csv(cached_run["out"] / "fmp_ticker_coverage_by_family.csv")
    assert {"ticker", "endpoint_family", "has_data", "rows"}.issubset(set(rows[0].keys()))


def test_endpoint_status_report_exists(sim_run):
    rows = _read_csv(sim_run["out"] / "fmp_endpoint_status_report.csv")
    assert len(rows) == 6
    assert {"endpoint_family", "cached", "collected", "subscription_blocked",
            "tickers_with_data"}.issubset(set(rows[0].keys()))


def test_multiple_testing_report_no_selection_without_score(cached_run):
    rows = {r["item"]: r for r in _read_csv(cached_run["out"] / "multiple_testing_report.csv")}
    assert rows["hypotheses_scored_for_alpha"]["value"] == "0"
    assert rows["family_wise_selection_taken"]["value"] == "False"


def test_validation_skeptic_report_exists(cached_run):
    rows = _read_csv(cached_run["out"] / "validation_skeptic_report.csv")
    checks = {r["check"] for r in rows}
    assert "no_alpha_without_coverage" in checks
    assert "look_ahead_pit_safety" in checks


# --------------------------------------------------------------------------- #
# Secret safety + storage discipline.
# --------------------------------------------------------------------------- #
def test_no_key_marker_in_any_artifact(sim_run, cached_run, blocked_run):
    marker = "api" + "key="  # assembled so this test file never contains it literally
    for run in (sim_run, cached_run, blocked_run):
        for p in run["out"].glob("*"):
            if p.is_file():
                text = p.read_text(encoding="utf-8", errors="replace").lower()
                assert marker not in text, "key marker leaked into %s" % p.name


def test_secret_audit_passes(cached_run):
    rows = {r["check"]: r for r in
            _read_csv(cached_run["out"] / "fmp_secret_safety_audit.csv")}
    assert rows["no_key_in_output_files"]["passed"] == "True"
    assert rows["api_key_logged"]["passed"] == "True"
    assert rows["api_key_written_to_disk"]["passed"] == "True"
    assert rows["raw_paid_data_gitignored"]["passed"] == "True"
    assert rows["normalized_paid_data_gitignored"]["passed"] == "True"
    assert cached_run["report"]["secret_safety_leak_scan_clean"] is True


def test_storage_manifests_mark_paid_data_gitignored_uncommitted(sim_run):
    for name in ("fmp_raw_storage_manifest.csv", "fmp_normalized_storage_manifest.csv"):
        rows = _read_csv(sim_run["out"] / name)
        assert rows
        for r in rows:
            assert r["gitignored"] == "yes"
            assert r["committed"] == "no"


def test_paid_data_dir_is_gitignored():
    gi = _REPO_ROOT / "research" / "data" / "fmp" / ".gitignore"
    assert gi.is_file()
    body = gi.read_text(encoding="utf-8")
    assert "raw/" in body and "normalized/" in body


def test_normalized_paid_data_not_in_output_dir(sim_run):
    # Normalized CSVs live under the gitignored data tree, NOT under the committed output dir.
    out_files = {p.name for p in sim_run["out"].glob("*")}
    norm_files = {p.name for p in (sim_run["data"] / "normalized").glob("*.csv")}
    assert norm_files  # normalization happened
    assert not (norm_files & out_files)  # and none leaked into the committed output


# --------------------------------------------------------------------------- #
# No Paper Trader / GCP / orders / deployment; committed-safe.
# --------------------------------------------------------------------------- #
def test_no_paper_trader_gcp_or_order_logic(sim_run):
    rep = sim_run["report"]
    assert rep["paper_trader_touched"] is False
    assert rep["gcp_touched"] is False
    assert rep["deployed"] is False
    assert rep["orders_enabled"] is False
    assert rep["automation_enabled"] is False
    assert rep["broker_execution_enabled"] is False


def test_committed_safe_flags(sim_run):
    rep = sim_run["report"]
    assert rep["committed"] is False
    assert rep["data_fabricated"] is False
    assert rep["no_alpha_fabricated"] is True
    assert rep["weights_optimized"] is False
    assert rep["signs_flipped"] is False
    assert rep["wrote_to_d_drive"] is False
    assert rep["preview_only"] is True


def test_script_imports_no_forbidden_modules():
    src = (_REPO_ROOT / "research"
           / "run_phase8n_fmp_critical_data_backfill_signal_expansion.py").read_text(
        encoding="utf-8")
    for line in src.splitlines():
        s = line.strip()
        if s.startswith("import ") or s.startswith("from "):
            low = s.lower()
            assert "api.app" not in low
            assert "google.cloud" not in low
            assert "alpha_vantage" not in low


def test_director_decision_terminal(sim_run, cached_run, blocked_run):
    for run in (sim_run, cached_run, blocked_run):
        dec = run["report"]["decision"]
        assert dec in P8N.ALLOWED_DECISIONS
