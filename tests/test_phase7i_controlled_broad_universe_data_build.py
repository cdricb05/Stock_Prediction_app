"""Tests for Phase 7-I — Controlled Broad Universe Data Foundation Build.

These exercise the pure logic (symbol classification, candidate universe, pilot capping,
dependency detection, gate matrix, recommendation branches, the env approval gate) without
any network call, and a guarded end-to-end dry-run over the real local SEC directory that
asserts the dry-run writes only committed-safe artifacts and nothing to the D: data drive.
"""

import os

import pytest

from research import run_phase7i_controlled_broad_universe_data_build as P

_real = pytest.mark.skipif(
    not P.SEC_COMPANY_TICKERS_JSON.exists(),
    reason="local SEC company_tickers.json not present")


# --------------------------------------------------------------------------- #
# Vocabulary + constants.
# --------------------------------------------------------------------------- #
def test_recommendation_vocabulary_exact():
    assert P.ALLOWED_RECOMMENDATIONS == (
        "READY_FOR_PHASE7J_BROAD_UNIVERSE_SIGNAL_RETEST",
        "LIVE_COLLECTION_APPROVAL_REQUIRED",
        "BROAD_UNIVERSE_DATA_PARTIAL",
        "DATA_COLLECTION_BLOCKED",
        "ERROR",
    )


def test_approval_gate_constants():
    assert P.LIVE_ENV_VAR == "PHASE7I_LIVE_APPROVED"
    assert P.LIVE_ENV_VALUE == "YES"
    assert P.PILOT_MAX_TICKERS == 300
    assert P.MIN_USABLE_PRICE_TICKERS == 250


# --------------------------------------------------------------------------- #
# Symbol classification (exclusion heuristics).
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("ticker,title,expected", [
    ("AAPL", "Apple Inc.", ""),
    ("NVDA", "NVIDIA CORP", ""),
    ("UPS", "United Parcel Service Inc", ""),          # 'United' must NOT match \bUNIT\b
    ("", "Nothing", "invalid_empty_ticker"),
    ("BRK-B", "Berkshire Hathaway", "invalid_characters"),
    ("ABCDEF", "Too Long", "invalid_characters"),
    ("AB1", "Has Digit", "invalid_characters"),
    ("ZZZZW", "Plain Co", "suffix_warrant"),
    ("ZZZZU", "Plain Co", "suffix_unit"),
    ("ZZZZR", "Plain Co", "suffix_rights"),
    ("PDI", "PIMCO Dynamic Income Fund", "title_fund_or_etf"),
    ("SPY", "SPDR S&P 500 ETF Trust", "title_fund_or_etf"),
    ("XPF", "Some Co Preferred Series A", "title_preferred"),
    ("WRNT", "Acme Warrants", "title_warrant"),
])
def test_classify_symbol(ticker, title, expected):
    assert P._classify_symbol(ticker, title) == expected


# --------------------------------------------------------------------------- #
# Candidate universe + pilot.
# --------------------------------------------------------------------------- #
def test_build_candidate_universe_includes_excludes_and_dedups(monkeypatch):
    fake_dir = [
        {"sec_rank": 0, "ticker": "AAPL", "cik": 320193, "title": "Apple Inc."},
        {"sec_rank": 1, "ticker": "ZZZZW", "cik": 1, "title": "Zeta Holdings Co"},  # suffix_warrant
        {"sec_rank": 2, "ticker": "PDI", "cik": 2, "title": "Income Fund"},       # title_fund_or_etf
        {"sec_rank": 3, "ticker": "AAPL", "cik": 320193, "title": "Apple Inc."},  # duplicate
        {"sec_rank": 4, "ticker": "MSFT", "cik": 789019, "title": "Microsoft Corp"},
    ]
    monkeypatch.setattr(P, "_load_sec_directory", lambda: fake_dir)
    monkeypatch.setattr(P, "_load_local_price_tickers", lambda: {"AAPL"})
    rows, summary = P.build_candidate_universe()

    by_ticker = {(r["ticker"], r["sec_rank"]): r for r in rows}
    assert by_ticker[("AAPL", 0)]["included"] == "true"
    assert by_ticker[("ZZZZW", 1)]["exclusion_reason"] == "suffix_warrant"
    assert by_ticker[("PDI", 2)]["exclusion_reason"] == "title_fund_or_etf"
    assert by_ticker[("AAPL", 3)]["exclusion_reason"] == "duplicate_ticker"
    assert by_ticker[("AAPL", 3)]["included"] == "false"
    assert by_ticker[("MSFT", 4)]["included"] == "true"
    # AAPL + MSFT included.
    assert summary["candidate_universe_size"] == 2
    assert summary["directory_size"] == 5
    assert summary["already_collected_local_count"] == 1  # AAPL only


def test_pilot_capped_and_sorted_by_rank():
    rows = [{"sec_rank": i, "ticker": f"T{i}", "included": "true"} for i in range(400)]
    rows += [{"sec_rank": 1000 + i, "ticker": f"X{i}", "included": "false"} for i in range(10)]
    # shuffle order deterministically (reverse) to prove sorting happens.
    pilot = P._pilot_tickers(list(reversed(rows)))
    assert len(pilot) == P.PILOT_MAX_TICKERS
    assert all(r["included"] == "true" for r in pilot)
    assert [r["sec_rank"] for r in pilot] == list(range(P.PILOT_MAX_TICKERS))


# --------------------------------------------------------------------------- #
# Dependency detection.
# --------------------------------------------------------------------------- #
def test_dependency_check_missing_yfinance(monkeypatch):
    def fake_detect(name):
        if name.split(".")[0] == "yfinance":
            return False, None
        return True, "x"
    monkeypatch.setattr(P, "_detect_dependency", fake_detect)
    rows, summary = P.build_dependency_check()
    assert summary["price_collector_available"] is False
    assert "yfinance" in summary["missing_required"]
    assert summary["all_required_available"] is False
    yf_row = [r for r in rows if r["dependency"] == "yfinance"][0]
    assert yf_row["available"] == "false"
    assert "install" in yf_row["note"].lower()


def test_dependency_check_all_present(monkeypatch):
    monkeypatch.setattr(P, "_detect_dependency", lambda name: (True, "1.0"))
    rows, summary = P.build_dependency_check()
    assert summary["all_required_available"] is True
    assert summary["price_collector_available"] is True


# --------------------------------------------------------------------------- #
# Gate matrix + recommendation branches.
# --------------------------------------------------------------------------- #
def _dep(price=True):
    return {"price_collector_available": price, "sec_collector_available": True,
            "pandas_available": True, "missing_required": [] if price else ["yfinance"]}


def _cand():
    return {"candidate_universe_size": 8000, "directory_size": 10415, "excluded_count": 2415}


def _plan():
    return {"pilot_ticker_count": 300}


def test_recommendation_approval_required():
    _, gs = P.build_gate_matrix(_dep(True), _cand(), _plan(),
                                {"ran": False, "usable_price_tickers": 0}, approved=False)
    assert gs["safety_fail"] is False
    assert P.derive_recommendation(gs, approved=False) == P.REC_APPROVAL_REQUIRED


def test_recommendation_blocked_missing_dependency():
    _, gs = P.build_gate_matrix(_dep(False), _cand(), _plan(),
                                {"ran": False, "usable_price_tickers": 0}, approved=False)
    assert gs["price_dependency_missing"] is True
    assert P.derive_recommendation(gs, approved=False) == P.REC_BLOCKED


def test_recommendation_ready_when_coverage_sufficient():
    _, gs = P.build_gate_matrix(_dep(True), _cand(), _plan(),
                                {"ran": True, "usable_price_tickers": 260}, approved=True)
    assert P.derive_recommendation(gs, approved=True) == P.REC_READY


def test_recommendation_partial_when_coverage_insufficient():
    _, gs = P.build_gate_matrix(_dep(True), _cand(), _plan(),
                                {"ran": True, "usable_price_tickers": 10}, approved=True)
    assert P.derive_recommendation(gs, approved=True) == P.REC_PARTIAL


def test_gate_matrix_safety_all_pass():
    gates, gs = P.build_gate_matrix(_dep(True), _cand(), _plan(),
                                    {"ran": False, "usable_price_tickers": 0}, approved=False)
    safety = [g for g in gates if g["kind"] == "safety"]
    assert safety and all(g["status"] == "PASS" for g in safety)
    assert gs["safety_fail"] is False
    # The coverage gate is N/A until a live run establishes coverage.
    cov = [g for g in gates if g["gate"] == "usable_price_coverage_sufficient"][0]
    assert cov["status"] == "N/A"


# --------------------------------------------------------------------------- #
# Env approval gate — live collection runs ONLY when the exact env var is YES.
# --------------------------------------------------------------------------- #
def test_no_live_collection_without_env(tmp_path, monkeypatch):
    monkeypatch.delenv(P.LIVE_ENV_VAR, raising=False)
    called = {"hit": False}

    def boom(pilot):  # must never be called in dry-run
        called["hit"] = True
        raise AssertionError("live collection must not run without approval")
    monkeypatch.setattr(P, "run_pilot_live_collection", boom)
    report = P.run(out_dir=str(tmp_path))
    assert called["hit"] is False
    assert report["live_approval"]["approved_this_run"] is False
    assert report["determinations"]["live_collection_ran"] is False
    assert report["recommendation"] in (P.REC_APPROVAL_REQUIRED, P.REC_BLOCKED)
    assert report["safety"]["wrote_to_data_drive"] is False


def test_env_yes_triggers_collection_ready(tmp_path, monkeypatch):
    monkeypatch.setenv(P.LIVE_ENV_VAR, "YES")

    def fake_live(pilot):
        status = [{"ticker": "AAPL", "status": "COLLECTED", "rows": 2500,
                   "usable": "true", "source": "yfinance"}]
        summ = {"ran": True, "usable_price_tickers": 260, "collected_tickers": 260,
                "failed_tickers": 0, "empty_tickers": 0, "panel_rows": 1000}
        return status, summ, {P.DATA_COMBINED_PANEL_CSV}
    monkeypatch.setattr(P, "run_pilot_live_collection", fake_live)
    report = P.run(out_dir=str(tmp_path))
    assert report["live_approval"]["approved_this_run"] is True
    assert report["determinations"]["live_collection_ran"] is True
    assert report["recommendation"] == P.REC_READY
    assert report["phase7j_next_plan"]["phase7j_retest_allowed"] is True


def test_env_lowercase_yes_not_approved(tmp_path, monkeypatch):
    # The gate matches "YES" (case-insensitive via .upper()); a non-YES value is not approval.
    monkeypatch.setenv(P.LIVE_ENV_VAR, "MAYBE")
    monkeypatch.setattr(P, "run_pilot_live_collection",
                        lambda pilot: (_ for _ in ()).throw(AssertionError("should not run")))
    report = P.run(out_dir=str(tmp_path))
    assert report["live_approval"]["approved_this_run"] is False


# --------------------------------------------------------------------------- #
# Guarded end-to-end dry-run over the real local SEC directory.
# --------------------------------------------------------------------------- #
@_real
def test_end_to_end_dry_run_real(tmp_path, monkeypatch):
    monkeypatch.delenv(P.LIVE_ENV_VAR, raising=False)
    report = P.run(out_dir=str(tmp_path), force_skip_live=True)

    assert report["recommendation"] in P.ALLOWED_RECOMMENDATIONS
    assert report["recommendation"] != P.REC_ERROR
    assert report["determinations"]["candidate_universe_size"] > 0
    assert report["determinations"]["live_collection_ran"] is False
    assert report["gate_matrix_summary"]["safety_fail"] is False

    # Safety: dry-run wrote nothing to the D: data drive.
    saf = report["safety"]
    assert saf["wrote_to_data_drive"] is False
    assert saf["paid_api_used"] is False
    assert saf["packages_installed"] is False
    assert saf["committed"] is False and saf["pushed"] is False

    # All eight committed-safe artifacts exist.
    for name in ("phase7i_controlled_broad_universe_data_build.json", "candidate_universe.csv",
                 "collection_plan.csv", "dependency_check.csv", "data_storage_manifest.csv",
                 "live_run_command.ps1", "collection_status.csv", "phase7j_next_plan.json"):
        assert (tmp_path / name).exists(), name

    # No large data file was created under tmp (repo) output.
    assert not (tmp_path / "phase7i_broad_price_history_free.csv").exists()

    # collection_status is all PLANNED in a dry-run, capped at the pilot size.
    import pandas as pd
    cs = pd.read_csv(tmp_path / "collection_status.csv")
    assert len(cs) <= P.PILOT_MAX_TICKERS
    assert set(cs["status"].unique()) == {"PLANNED"}

    # The live-run command is ASCII-only (safe to read/run in PowerShell).
    text = (tmp_path / "live_run_command.ps1").read_text(encoding="utf-8")
    assert text.isascii()
    assert "PHASE7I_LIVE_APPROVED" in text


@_real
def test_storage_manifest_targets_data_drive(tmp_path, monkeypatch):
    monkeypatch.delenv(P.LIVE_ENV_VAR, raising=False)
    P.run(out_dir=str(tmp_path), force_skip_live=True)
    import pandas as pd
    man = pd.read_csv(tmp_path / "data_storage_manifest.csv")
    # Every large_data artifact lives on the D: data drive, none written in a dry-run.
    large = man[man["large_data"] == True]
    assert len(large) > 0
    assert (large["drive"] == "D:").all()
    assert (man["written_this_run"] == False).all()
