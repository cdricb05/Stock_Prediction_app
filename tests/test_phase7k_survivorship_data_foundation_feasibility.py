"""Tests for Phase 7-K — Survivorship-Aware Data Foundation Feasibility Pilot.

Pure / offline unit tests: decision logic, reason classification, Wikipedia table parsing,
the (monkeypatched) delisted price probe, Stooq anti-bot detection, the gate matrix, and a
guarded offline end-to-end (force_skip_live) that verifies all nine committed-safe artifacts.
No network is performed by these tests.
"""

from __future__ import annotations

import json
import os

import pytest

from research import run_phase7k_survivorship_data_foundation_feasibility as K


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _flags(*, tested=True, membership=True, some=False, strong=False, zero=False,
           pit=False, identity=True):
    return {
        "tested": tested,
        "membership_feasible": membership,
        "delisted_some_feasible": some,
        "delisted_strong_feasible": strong,
        "delisted_zero": zero,
        "pit_sector_feasible": pit,
        "identity_feasible": identity,
    }


def _changes_html(rows):
    trs = ["<tr><th>Date</th><th>Added Ticker</th><th>Added Security</th>"
           "<th>Removed Ticker</th><th>Removed Security</th><th>Reason</th></tr>"]
    for (date, at, asec, rt, rsec, reason) in rows:
        trs.append(f"<tr><td>{date}</td><td>{at}</td><td>{asec}</td>"
                   f"<td>{rt}</td><td>{rsec}</td><td>{reason}</td></tr>")
    return ('<table id="constituents"><tr><th>Symbol</th></tr><tr><td>AAA</td></tr></table>'
            '<table id="changes">' + "".join(trs) + "</table>")


# --------------------------------------------------------------------------- #
# Recommendation vocabulary + thresholds
# --------------------------------------------------------------------------- #
def test_recommendation_vocabulary_exact_and_ordered():
    assert K.ALLOWED_RECOMMENDATIONS == (
        "SURVIVORSHIP_DATA_FOUNDATION_FEASIBLE",
        "SURVIVORSHIP_DATA_FOUNDATION_PARTIAL",
        "FREE_DATA_NOT_SUFFICIENT",
        "NEEDS_PAID_OR_EXTERNAL_DATA",
        "ERROR",
    )
    assert K.REC_FEASIBLE == "SURVIVORSHIP_DATA_FOUNDATION_FEASIBLE"
    assert K.REC_PARTIAL == "SURVIVORSHIP_DATA_FOUNDATION_PARTIAL"
    assert K.REC_NOT_SUFFICIENT == "FREE_DATA_NOT_SUFFICIENT"
    assert K.REC_NEEDS_PAID == "NEEDS_PAID_OR_EXTERNAL_DATA"
    assert K.REC_ERROR == "ERROR"


def test_live_gate_constants():
    assert K.LIVE_ENV_VAR == "PHASE7K_LIVE_APPROVED"
    assert K.LIVE_ENV_VALUE == "YES"
    assert K.WEB_CHECK_CAP == 50
    assert K.PRICE_PROBE_CAP == 20


def test_feasibility_thresholds_sane():
    assert K.MIN_MEMBERSHIP_SPAN_YEARS == 2
    assert 0 < K.DELISTED_SOME_FRAC <= K.DELISTED_STRONG_FRAC <= 1.0
    assert K.MIN_TRUE_DELISTING_USABLE >= 1


# --------------------------------------------------------------------------- #
# Decision rule — every recommendation reachable; borderline never rounded up
# --------------------------------------------------------------------------- #
def test_decision_dryrun_is_provisional_not_sufficient():
    assert K.derive_recommendation(_flags(tested=False)) == K.REC_NOT_SUFFICIENT


def test_decision_feasible_requires_membership_strong_and_pit():
    assert K.derive_recommendation(
        _flags(membership=True, some=True, strong=True, pit=True)) == K.REC_FEASIBLE


def test_decision_partial_membership_some_but_no_pit():
    assert K.derive_recommendation(
        _flags(membership=True, some=True, strong=True, pit=False)) == K.REC_PARTIAL


def test_decision_strong_but_no_pit_is_partial_not_feasible():
    # Strong delisted coverage cannot rescue FEASIBLE when PIT sectors are missing.
    assert K.derive_recommendation(
        _flags(membership=True, some=True, strong=True, pit=False)) != K.REC_FEASIBLE


def test_decision_zero_genuine_delistings_needs_paid():
    assert K.derive_recommendation(
        _flags(membership=True, some=False, strong=False, zero=True)) == K.REC_NEEDS_PAID


def test_decision_sparse_delisted_not_zero_is_free_not_sufficient():
    # The observed live case: membership ok, 1/20 genuine delistings, not zero, no PIT sectors.
    assert K.derive_recommendation(
        _flags(membership=True, some=False, strong=False, zero=False, pit=False)) \
        == K.REC_NOT_SUFFICIENT


def test_decision_membership_infeasible_is_free_not_sufficient():
    assert K.derive_recommendation(
        _flags(membership=False, some=True, strong=True, pit=True, zero=False)) \
        == K.REC_NOT_SUFFICIENT


# --------------------------------------------------------------------------- #
# Reason classification (index removal vs genuine delisting)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("reason,expected", [
    ("Mars Inc. acquired Kellanova.", "true_delisting"),
    ("Sycamore Partners acquired Walgreen Boots Alliance.", "true_delisting"),
    ("Vista Equity Partners acquired Citrix Systems.", "true_delisting"),
    ("Company X went bankrupt.", "true_delisting"),
    ("Taken private by a PE firm.", "true_delisting"),
    ("Market capitalization change.", "index_removal"),
    ("S&P 500 rebalancing.", "index_removal"),
    ("Some unexplained reason.", "other"),
])
def test_classify_removal(reason, expected):
    assert K._classify_removal(reason) == expected


# --------------------------------------------------------------------------- #
# Wikipedia membership parsing + ordering
# --------------------------------------------------------------------------- #
def test_membership_pilot_parses_and_orders_genuine_first():
    html = _changes_html([
        ("June 1, 2024", "NEW", "NewCo", "OLD", "OldCo", "Acme Inc. acquired OldCo."),
        ("March 3, 2022", "XX", "Xco", "YY", "Yco", "Market capitalization change."),
    ])
    timeline, summary, removed = K.run_membership_pilot(html)
    assert summary["events"] == 2
    assert summary["span_years"] == 2  # 2024 - 2022
    assert summary["true_delisting_count"] == 1
    # Genuine delisting ("OLD", acquired) must sort before the index removal ("YY").
    assert removed[0]["ticker"] == "OLD" and removed[0]["classification"] == "true_delisting"
    assert removed[1]["ticker"] == "YY" and removed[1]["classification"] == "index_removal"


def test_membership_pilot_feasible_when_enough_events_and_span():
    rows = []
    for i in range(25):
        yr = 2000 + (i % 5)  # spans 2000..2004 -> 4y >= 2
        tag = chr(65 + i)    # A..Y (letter-only tickers pass the validity regex)
        rows.append((f"July 4, {yr}", f"A{tag}", "Add", f"R{tag}", "Rem", "Acme acquired R."))
    html = _changes_html(rows)
    _, summary, removed = K.run_membership_pilot(html)
    assert summary["events"] == 25
    assert summary["span_years"] >= K.MIN_MEMBERSHIP_SPAN_YEARS
    assert summary["feasible"] is True
    assert len(removed) == 25


def test_membership_pilot_wikitable_fallback_when_no_ids():
    html = ('<table class="wikitable"><tr><th>Symbol</th></tr><tr><td>AAA</td></tr></table>'
            '<table class="wikitable">'
            '<tr><th>Date</th><th>AT</th><th>AS</th><th>RT</th><th>RS</th><th>Reason</th></tr>'
            '<tr><td>May 1, 2023</td><td>N</td><td>N</td><td>OLD</td><td>Oldco</td>'
            '<td>acquired by Acme</td></tr></table>')
    timeline, summary, removed = K.run_membership_pilot(html)
    assert summary["events"] == 1
    assert removed and removed[0]["ticker"] == "OLD"


# --------------------------------------------------------------------------- #
# Delisted price probe (monkeypatched sources) — judged on genuine delistings
# --------------------------------------------------------------------------- #
def _td(ticker):
    return {"ticker": ticker, "security": ticker, "reason": "acquired by Acme",
            "classification": "true_delisting"}


def test_delisted_probe_sparse_genuine_delistings(monkeypatch):
    # Only the first ticker has data; the rest return nothing (the observed live pattern).
    def fake_yf(tk):
        return (9000, "1995-01-01", "2026-05-07") if tk == "CTRA" else (0, None, None)
    def fake_stooq(tk, budget):
        return (0, None, None, "blocked")  # anti-bot challenge everywhere
    monkeypatch.setattr(K, "_probe_yfinance", fake_yf)
    monkeypatch.setattr(K, "_probe_stooq", fake_stooq)
    removed = [_td("CTRA")] + [_td(f"X{i}") for i in range(19)]
    rows, summary = K.run_delisted_price_probe(removed, K._Budget(), yf_available=True)
    assert summary["true_delisting_probed"] == 20
    assert summary["true_delisting_usable"] == 1
    assert summary["some_feasible"] is False
    assert summary["strong_feasible"] is False
    assert summary["zero"] is False
    assert summary["stooq_blocked"] == 20
    assert summary["stooq_status"] == "blocked_antibot"


def test_delisted_probe_zero_when_no_source_returns_data(monkeypatch):
    monkeypatch.setattr(K, "_probe_yfinance", lambda tk: (0, None, None))
    monkeypatch.setattr(K, "_probe_stooq", lambda tk, b: (0, None, None, "empty"))
    rows, summary = K.run_delisted_price_probe([_td(f"X{i}") for i in range(6)],
                                               K._Budget(), yf_available=True)
    assert summary["true_delisting_usable"] == 0
    assert summary["zero"] is True
    assert K.derive_recommendation(
        K.build_decision_matrix({"feasible": True}, summary, {"effective_dated": False},
                                {"feasible": True}, tested=True)[1]) == K.REC_NEEDS_PAID


def test_delisted_probe_some_feasible_when_coverage_adequate(monkeypatch):
    # 4 of 5 genuine delistings usable -> frac 0.8 -> some + strong.
    usable = {"A", "B", "C", "D"}
    monkeypatch.setattr(K, "_probe_yfinance",
                        lambda tk: (5000, "2000-01-01", "2024-01-01") if tk in usable else (0, None, None))
    monkeypatch.setattr(K, "_probe_stooq", lambda tk, b: (0, None, None, "empty"))
    removed = [_td(t) for t in ["A", "B", "C", "D", "E"]]
    rows, summary = K.run_delisted_price_probe(removed, K._Budget(), yf_available=True)
    assert summary["true_delisting_usable"] == 4
    assert summary["some_feasible"] is True
    assert summary["strong_feasible"] is True


def test_delisted_probe_respects_price_cap(monkeypatch):
    monkeypatch.setattr(K, "_probe_yfinance", lambda tk: (0, None, None))
    monkeypatch.setattr(K, "_probe_stooq", lambda tk, b: (0, None, None, "empty"))
    rows, summary = K.run_delisted_price_probe([_td(f"X{i}") for i in range(50)],
                                               K._Budget(), yf_available=True)
    assert summary["probed"] == K.PRICE_PROBE_CAP  # capped at 20


# --------------------------------------------------------------------------- #
# Stooq anti-bot detection
# --------------------------------------------------------------------------- #
def test_probe_stooq_detects_antibot_challenge(monkeypatch):
    challenge = ('<!DOCTYPE html><html><head></head><body><noscript>This site requires '
                 'JavaScript to verify your browser.</noscript></body></html>')
    monkeypatch.setattr(K, "_fetch_text", lambda url, budget: challenge)
    rows, first, last, status = K._probe_stooq("AAPL", K._Budget())
    assert status == "blocked" and rows == 0


def test_probe_stooq_parses_real_csv(monkeypatch):
    csv_text = "Date,Open,High,Low,Close,Volume\n2020-01-02,1,2,0,1,100\n2020-01-03,1,2,0,1,100\n"
    monkeypatch.setattr(K, "_fetch_text", lambda url, budget: csv_text)
    rows, first, last, status = K._probe_stooq("AAPL", K._Budget())
    assert status == "ok" and rows == 2 and first == "2020-01-02" and last == "2020-01-03"


# --------------------------------------------------------------------------- #
# Host allow-list
# --------------------------------------------------------------------------- #
def test_host_allowlist():
    assert K._host_allowed("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies")
    assert K._host_allowed("https://stooq.com/q/d/l/?s=aapl.us&i=d")
    assert not K._host_allowed("https://evil.example.com/x")
    assert not K._host_allowed("http://en.wikipedia.org/x")  # https only


# --------------------------------------------------------------------------- #
# Decision matrix + feasibility matrix
# --------------------------------------------------------------------------- #
def test_decision_matrix_flags_when_tested():
    rows, flags = K.build_decision_matrix(
        {"feasible": True, "events": 399, "span_years": 50},
        {"some_feasible": False, "strong_feasible": False, "zero": False,
         "true_delisting_usable": 1, "true_delisting_probed": 20, "true_delisting_fraction": 0.05,
         "usable_any": 1, "probed": 20},
        {"effective_dated": False}, {"feasible": True}, tested=True)
    assert flags["membership_feasible"] is True
    assert flags["pit_sector_feasible"] is False
    pillars = {r["pillar"]: r for r in rows}
    assert pillars["pit_sectors"]["severity"] == "blocking"
    assert pillars["delisted_prices"]["feasible"] == "false"


def test_feasibility_matrix_marks_stooq_blocked_and_dryrun_not_tested():
    # Dry-run: nothing tested.
    dry = K.build_feasibility_matrix({"bs4": True, "yfinance": True}, {}, {},
                                     {"effective_dated": False}, {}, tested=False)
    assert all(r["result"] == "NOT_TESTED" for r in dry)
    # Tested with a blocked Stooq leg.
    live = K.build_feasibility_matrix(
        {"bs4": True, "yfinance": True},
        {"feasible": True, "events": 399, "span_years": 50},
        {"some_feasible": False, "true_delisting_usable": 1, "true_delisting_probed": 20,
         "true_delisting_fraction": 0.05, "stooq_blocked": 20, "stooq_status": "blocked_antibot",
         "probed": 20, "stooq_usable": 0},
        {"effective_dated": False}, {"feasible": True, "resolved": 6, "probed": 6,
                                     "removed_in_dir": 0, "removed_probed": 9}, tested=True)
    stooq = [r for r in live if "Stooq" in r["source"]][0]
    assert stooq["result"] == "BLOCKED"


# --------------------------------------------------------------------------- #
# Gate matrix
# --------------------------------------------------------------------------- #
def test_gate_matrix_safety_all_pass_and_pit_fails_when_tested():
    flags = _flags(membership=True, some=False, strong=False, zero=False, pit=False, identity=True)
    gates, summary = K.build_gate_matrix({"bs4": True, "yfinance": True}, {}, flags, K._Budget(),
                                         approved=True, tested=True, rec=K.REC_NOT_SUFFICIENT)
    assert summary["safety_fail"] is False
    by = {g["gate"]: g for g in gates}
    assert by["pit_sectors_available"]["status"] == "FAIL"
    assert by["delisted_price_coverage"]["status"] == "FAIL"
    assert by["membership_reconstructable"]["status"] == "PASS"
    # Every safety gate passes.
    for g in gates:
        if g["kind"] == "safety":
            assert g["status"] == "PASS"


def test_gate_matrix_dryrun_uses_na_not_fail():
    flags = _flags(tested=False, membership=False)
    gates, summary = K.build_gate_matrix({"bs4": True, "yfinance": True}, {}, flags, K._Budget(),
                                         approved=False, tested=False, rec=K.REC_NOT_SUFFICIENT)
    by = {g["gate"]: g for g in gates}
    assert by["pit_sectors_available"]["status"] == "N/A"
    assert by["live_pilot_ran"]["status"] == "INFO"
    assert summary["safety_fail"] is False


# --------------------------------------------------------------------------- #
# Inventory, sector PIT, identity prune, next plan
# --------------------------------------------------------------------------- #
def test_local_inventory_has_four_pillars_and_no_local_pit_or_membership():
    rows, summary = K.build_local_inventory()
    pillars = {r["pillar"] for r in rows}
    assert {"historical_membership", "delisted_prices", "pit_sectors", "identity_continuity"} <= pillars
    assert summary["local_historical_membership_present"] is False
    assert summary["local_pit_sectors_present"] is False


def test_sector_pit_is_current_only():
    rows, summary = K.build_sector_pit_rows()
    assert summary["effective_dated"] is False
    assert all(r["effective_dated"] == "false" for r in rows)


def test_identity_prune_keeps_former_names():
    full = {"cik": 1, "name": "NewCo", "tickers": ["NEW"], "exchanges": ["Nasdaq"],
            "sicDescription": "x", "formerNames": [{"name": "OldCo"}],
            "filings": {"recent": {"accessionNumber": ["a"] * 50}}}
    pruned = K._prune_submissions_identity(full)
    assert pruned["formerNames"] == [{"name": "OldCo"}]
    assert "filings" not in pruned  # large blocks dropped


@pytest.mark.parametrize("rec,expected_scope", [
    (K.REC_FEASIBLE, "DATA_BUILD_FREE"),
    (K.REC_PARTIAL, "DATA_BUILD_FREE"),
    (K.REC_NEEDS_PAID, "DECISION"),
    (K.REC_NOT_SUFFICIENT, "DECISION"),
])
def test_next_plan_step_scope(rec, expected_scope):
    plan = K.build_next_plan(rec, _flags(), {"events": 0, "span_years": 0}, {}, {})
    assert plan["recommendation"] == rec
    assert plan["next_steps"][0]["scope"] == expected_scope


# --------------------------------------------------------------------------- #
# Guarded offline end-to-end (no network)
# --------------------------------------------------------------------------- #
def test_end_to_end_dry_run_offline(tmp_path):
    report = K.run(out_dir=str(tmp_path), force_skip_live=True)
    assert report["recommendation"] in K.ALLOWED_RECOMMENDATIONS
    assert report["recommendation"] == K.REC_NOT_SUFFICIENT
    assert report["provisional_pending_live_pilot"] is True
    assert report["live_approval"]["live_pilot_ran"] is False
    assert report["live_approval"]["network_requests"] == 0
    # All nine committed-safe artifacts written.
    for name in ("phase7k_survivorship_data_foundation_feasibility.json",
                 "local_survivorship_source_inventory.csv",
                 "free_source_feasibility_matrix.csv",
                 "membership_timeline_pilot.csv",
                 "delisted_price_probe.csv",
                 "sector_pit_feasibility.csv",
                 "identity_mapping_feasibility.csv",
                 "data_foundation_decision_matrix.csv",
                 "phase7l_next_plan.json"):
        assert (tmp_path / name).exists(), name
    # Safety contract.
    assert report["safety"]["committed"] is False
    assert report["safety"]["pushed"] is False
    assert report["safety"]["paid_api_used"] is False
    assert report["safety"]["packages_installed"] is False


def test_end_to_end_dry_run_makes_no_network_and_writes_nothing_to_d(tmp_path):
    report = K.run(out_dir=str(tmp_path), force_skip_live=True)
    assert report["safety"]["wrote_to_data_drive"] is False
    assert report["live_approval"]["web_checks_used"] == 0
    assert report["live_approval"]["price_probes_used"] == 0
