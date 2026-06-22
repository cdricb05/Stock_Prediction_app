"""Phase 5-E1C - tests for the low-cost fundamentals provider proof test.

Verifies the proof test runs fully OFFLINE (no network, no API key), writes every
required artifact, exposes the documented provider facts (including
annual_upfront_required for each provider), marks FMP as
rejected_due_annual_upfront, always emits a recommendation from the allowed
vocabulary, never writes paid data / keys / binary artifacts, and never touches
Paper Trader / GCP / deploy / order / automation paths or the D: drive.

Pure/offline: no network, no key. Runs under pytest.
"""
from __future__ import annotations

import csv
import importlib
import inspect
import json
import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import pytest  # noqa: E402

_RUNNER_MODULE = "research.run_phase5e1c_provider_proof_test"


def _runner():
    return importlib.import_module(_RUNNER_MODULE)


def _read_csv(path):
    with open(path, "r", newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


@pytest.fixture(scope="module")
def proof(tmp_path_factory):
    """Run the proof test once into a temp dir; return (runner, report, out_dir).

    Uses a temp out_dir so the test never depends on (or clobbers) the committed
    artifacts. No API key is set in the environment - proving none is required.
    """
    runner = _runner()
    out_dir = tmp_path_factory.mktemp("phase5e1c_out")
    report = runner.run(out_dir=out_dir, verbose=False)
    on_disk = json.loads((out_dir / runner._REPORT_OUT.name).read_text(encoding="utf-8"))
    assert on_disk["phase"] == report["phase"]
    return runner, report, out_dir


# --------------------------------------------------------------------------- #
# Offline / no-key / no-network
# --------------------------------------------------------------------------- #
def test_runner_imports_and_compiles():
    runner = _runner()
    assert runner.PHASE == "5-E1C"


def test_no_api_key_required(proof, monkeypatch):
    # Ensure no provider key is present, then run again: it must still succeed.
    for var in ("FMP_API_KEY", "SIMFIN_API_KEY", "EODHD_API_KEY", "TIINGO_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    runner, _, _ = proof
    report = runner.run(verbose=False)  # writes the committed artifacts, no key
    assert report["api_key_required"] is False
    assert report["api_key_used"] is False


def test_source_has_no_network_code():
    """No live network calls by default == there is no network code at all."""
    src = inspect.getsource(_runner())
    for marker in ("urllib", "urlopen", "requests.", "http.client", "socket.",
                   "httpx", "aiohttp"):
        assert marker not in src, "unexpected network marker in source: %s" % marker


def test_report_declares_offline(proof):
    _, report, _ = proof
    assert report["network_used"] is False
    assert report["mode"] == "dry_run_proof_test"
    assert report["installs_packages"] is False
    assert report["builds_collector"] is False


# --------------------------------------------------------------------------- #
# Artifacts present, text-only, no paid data / key / binary
# --------------------------------------------------------------------------- #
def test_writes_all_three_artifacts(proof):
    runner, _, out_dir = proof
    for name in (runner._REPORT_OUT.name, runner._COMPARISON_OUT.name,
                 runner._TICKER_REQ_OUT.name):
        assert (out_dir / name).is_file(), "missing artifact: %s" % name


def test_artifacts_are_text_not_binary(proof):
    _, _, out_dir = proof
    for path in out_dir.iterdir():
        raw = path.read_bytes()
        assert b"\x00" not in raw, "binary NUL byte in artifact: %s" % path.name
        raw.decode("utf-8")  # must be valid UTF-8 text


def test_artifacts_have_no_key_or_paid_data_markers(proof):
    _, _, out_dir = proof
    for path in out_dir.iterdir():
        text = path.read_text(encoding="utf-8")
        assert "apikey=" not in text.lower()
        # No real key value should ever appear (env-only, never embedded).
        assert os.environ.get("FMP_API_KEY", "__never__") not in text
        assert os.environ.get("SIMFIN_API_KEY", "__never__") not in text


# --------------------------------------------------------------------------- #
# Required content: annual_upfront_required, FMP rejection, recommendation vocab
# --------------------------------------------------------------------------- #
def test_every_provider_reports_annual_upfront_required(proof):
    _, report, _ = proof
    for prof in report["providers"]:
        assert "annual_upfront_required" in prof
        assert prof["annual_upfront_required"] in (True, False, "unknown")


def test_comparison_csv_has_annual_upfront_and_required_columns(proof):
    runner, _, out_dir = proof
    rows = _read_csv(out_dir / runner._COMPARISON_OUT.name)
    assert rows
    required_cols = {
        "provider_name", "pricing_model", "annual_upfront_required",
        "monthly_option_available", "data_families_supported",
        "api_or_bulk_download_available", "implementation_friction",
        "recommended_next_action",
    }
    assert required_cols.issubset(set(rows[0].keys()))


def test_fmp_is_marked_rejected_due_annual_upfront(proof):
    runner, report, _ = proof
    assert report["fmp_negative_benchmark"]["status"] == "rejected_due_annual_upfront"
    assert report["fmp_negative_benchmark"]["annual_upfront_required"] is True
    # FMP must never be the selected provider.
    assert report["selected_provider_key"] != "fmp"
    fmp = next(p for p in report["providers"] if p["provider_key"] == "fmp")
    assert fmp["provider_status"] == runner.FMP_STATUS
    assert fmp["is_negative_benchmark"] is True


def test_recommendation_in_allowed_vocab(proof):
    runner, report, _ = proof
    assert report["recommendation"] in runner.ALLOWED_RECOMMENDATIONS


def test_recommends_simfin_with_documented_facts(proof):
    runner, report, _ = proof
    assert report["recommendation"] == runner.REC_TRY_SIMFIN
    assert report["selected_provider_key"] == "simfin"


def test_priority_order_documented(proof):
    _, report, _ = proof
    assert report["provider_priority_order"] == ["simfin", "eodhd", "tiingo", "sec_local"]


def test_free_test_and_monthly_only_possible(proof):
    _, report, _ = proof
    assert report["free_test_possible"] is True
    assert report["monthly_only_payment_possible"] is True


def test_ticker_requirements_cover_all_ten(proof):
    runner, _, out_dir = proof
    rows = _read_csv(out_dir / runner._TICKER_REQ_OUT.name)
    tickers = {r["ticker"] for r in rows}
    assert tickers == {"AAPL", "MSFT", "AMZN", "NVDA", "JPM", "BAC", "C", "APH", "ABT", "ACN"}
    for r in rows:
        assert r["required_quarterly_income_statement"] == "required"
        assert r["required_quarterly_balance_sheet"] == "required"
        assert r["required_quarterly_cash_flow"] == "required"
        assert int(r["required_min_years_history"]) == runner.MIN_HISTORY_YEARS


# --------------------------------------------------------------------------- #
# Safety: no Paper Trader / GCP / deploy / orders / automation / D: writes
# --------------------------------------------------------------------------- #
def test_safety_contract_flags(proof):
    _, report, _ = proof
    assert report["preview_only"] is True
    for flag in ("orders_enabled", "automation_enabled", "broker_execution_enabled",
                 "production_replacement", "writes_to_d_drive", "modifies_paper_trader",
                 "modifies_gcp"):
        assert report[flag] is False


def test_source_has_no_forbidden_write_targets():
    # Scan for concrete EXECUTION/deploy/D:-write code patterns (not the safety prose
    # or boolean flag names, which legitimately mention these surfaces to disable them).
    src = inspect.getsource(_runner()).lower()
    for marker in ("gcloud", "place_order(", "submit_order(", "create_order(",
                   "subprocess", "os.system(", "d:\\\\", "d:/"):
        assert marker not in src, "forbidden marker in source: %s" % marker


def test_out_dir_is_under_research_output():
    runner = _runner()
    assert runner._OUT_DIR.parent.name == "output"
    assert runner._OUT_DIR.name == "phase5e1c_provider_proof_test"


def test_main_returns_zero():
    runner = _runner()
    assert runner.main([]) == 0


# --------------------------------------------------------------------------- #
# Provider-notes override hook (offline) shifts the recommendation deterministically
# --------------------------------------------------------------------------- #
def test_provider_notes_override_shifts_recommendation(tmp_path):
    runner = _runner()
    # If user-confirmed notes reveal SimFin actually requires annual upfront, the
    # recommendation must fall through to the next eligible provider (EODHD).
    notes = {"providers": {"simfin": {"annual_upfront_required": True}}}
    report = runner.run(out_dir=tmp_path, provider_notes=notes, verbose=False)
    assert report["recommendation"] == runner.REC_TRY_EODHD
    assert report["selected_provider_key"] == "eodhd"


def test_provider_notes_all_paid_blocked_falls_back_to_sec(tmp_path):
    runner = _runner()
    notes = {"providers": {
        "simfin": {"annual_upfront_required": True},
        "eodhd": {"annual_upfront_required": True},
    }}
    report = runner.run(out_dir=tmp_path, provider_notes=notes, verbose=False)
    assert report["recommendation"] == runner.REC_USE_SEC
    assert report["selected_provider_key"] == "sec_local"
