"""Tests for Phase 8-O - Cheapest Viable Provider Selection and Alt-Data Entitlement Audit.

Proves the brief's acceptance criteria, fully offline (no key, no network):
  * Phase 8-N FMP insufficiency is read correctly (all six critical families blocked);
  * the provider decision matrix includes FMP, Alpha Vantage, Finnhub, EODHD;
  * FMP Ultimate is never recommended by default (and the upgrade is not the default);
  * Alpha Vantage is ranked for earnings/surprises; Finnhub for the analyst families;
  * the output names the exact env vars and produces acquisition commands;
  * no API key is printed or written; no raw paid data lands in committed artifacts;
  * no Paper Trader / GCP / order / deployment logic; committed-safe outputs only;
  * the env-key gate: no alternative key -> BLOCKED_MISSING_ALTERNATIVE_PROVIDER_KEY (cheapest
    next provider still named); keys present -> the mixed-provider strategy.

The bounded probe is exercised via an injected ``transport`` so the suite needs neither a
provider key nor a network. Each run writes to an isolated tmp dir.
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
    path = _REPO_ROOT / "research" / "run_phase8o_cheapest_provider_selection.py"
    spec = importlib.util.spec_from_file_location("phase8o_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


P8O = _load()

_ALL_PROVIDER_ENV = ["FMP_API_KEY", "ALPHAVANTAGE_API_KEY", "FINNHUB_API_KEY", "EODHD_API_KEY"]


def _read_csv(path: Path):
    with open(path, "r", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _read_json(path: Path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _clear_provider_env(monkeypatch):
    for env in _ALL_PROVIDER_ENV:
        monkeypatch.delenv(env, raising=False)


def _write_phase8n_fixture(d: Path, blocked=True):
    """Write a minimal Phase 8-N artifact set mirroring the real INSUFFICIENT live run."""
    d.mkdir(parents=True, exist_ok=True)
    fams = list(P8O.CRITICAL_FAMILIES)
    if blocked:
        coverage = {f: 8 for f in fams}
        entitlement = {f: "PARTIAL" for f in fams}
        block_fraction = {f: 0.6429 for f in fams}
        broadly = {f: True for f in fams}
        missing = list(fams)
        decision = "FMP_PLAN_COVERAGE_INSUFFICIENT"
    else:
        coverage = {f: 22 for f in fams}
        entitlement = {f: "ENTITLED" for f in fams}
        block_fraction = {f: 0.0 for f in fams}
        broadly = {f: False for f in fams}
        missing = []
        decision = "READY_FOR_PROVIDER_EXPANDED_SIGNAL_SCORING"
    report = {
        "phase": "8-N", "decision": decision, "min_tickers_to_score": 20,
        "universe_size": 17, "requests_remaining": 68,
        "coverage_counts": coverage, "family_entitlement": entitlement,
        "family_block_fraction": block_fraction, "missing_blocked_families": missing,
    }
    with open(d / "phase8n_fmp_critical_data_backfill_signal_expansion.json", "w",
              encoding="utf-8") as fh:
        json.dump(report, fh, indent=2)
    with open(d / "fmp_family_entitlement_matrix.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["endpoint_family", "critical", "coverage", "entitlement",
                    "block_fraction", "broadly_blocked"])
        for f in fams:
            w.writerow([f, True, coverage[f], entitlement[f], block_fraction[f], broadly[f]])
    with open(d / "fmp_provider_upgrade_decision.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["endpoint_family", "entitlement", "coverage", "recommended_action", "rationale"])
        w.writerow(["__overall__", "", "",
                    "UPGRADE_NOT_RECOMMENDED_USE_ALTERNATIVE" if blocked else "NO_UPGRADE_NEEDED",
                    "x"])
    with open(d / "fmp_subscription_block_pattern_report.csv", "w", newline="",
              encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["endpoint_family", "pattern"])
        for f in fams:
            w.writerow([f, "SYSTEMATIC" if blocked else "NONE"])


def fake_transport(path: str):
    """A deterministic provider payload (list of rows) for the probe path."""
    return [{"symbol": "AAPL", "date": "2026-01-01", "value": 1.0}]


def _run(monkeypatch, tmp_path, blocked=True, set_keys=None, transport=None, live=False):
    _clear_provider_env(monkeypatch)
    for env in (set_keys or []):
        # Distinctive multi-char value so the leak scan can't false-positive on a substring.
        monkeypatch.setenv(env, "ZZTESTKEYZZ_%s" % env)
    p8n = tmp_path / "p8n"
    _write_phase8n_fixture(p8n, blocked=blocked)
    out = tmp_path / "out"
    data = tmp_path / "data"
    rep = P8O.run(live=live, transport=transport, out_dir=out, data_dir=data,
                  phase8n_dir=p8n, verbose=False)
    return {"report": rep, "out": out, "data": data, "p8n": p8n}


# --------------------------------------------------------------------------- #
# Vocabulary / structure.
# --------------------------------------------------------------------------- #
def test_six_critical_families():
    assert P8O.CRITICAL_FAMILIES == (
        "earnings_surprises", "earnings_calendar", "analyst_estimates",
        "analyst_recommendations", "analyst_price_targets", "ratings_grades_consensus")


def test_eight_allowed_decisions():
    assert len(P8O.ALLOWED_DECISIONS) == 8
    for d in ("ALPHA_VANTAGE_FIRST", "FINNHUB_FIRST", "EODHD_FIRST", "FMP_UPGRADE_REQUIRED",
              "MIXED_PROVIDER_STRATEGY_REQUIRED", "BLOCKED_MISSING_ALTERNATIVE_PROVIDER_KEY",
              "NO_PROVIDER_CAN_SOLVE_CHEAPLY", "ERROR"):
        assert d in P8O.ALLOWED_DECISIONS
    # The "no blocked family" sentinel is NOT one of the eight terminal decisions.
    assert P8O.DEC_FMP_SUFFICIENT not in P8O.ALLOWED_DECISIONS


# --------------------------------------------------------------------------- #
# Phase 8-N insufficiency read correctly.
# --------------------------------------------------------------------------- #
def test_phase8n_insufficiency_read_correctly(monkeypatch, tmp_path):
    r = _run(monkeypatch, tmp_path, blocked=True)
    rep = r["report"]
    assert rep["phase8n_found"] is True
    assert rep["phase8n_fmp_decision"] == "FMP_PLAN_COVERAGE_INSUFFICIENT"
    assert set(rep["blocked_families"]) == set(P8O.CRITICAL_FAMILIES)
    # Every critical family is entitlement-PARTIAL with coverage < 20.
    for f in P8O.CRITICAL_FAMILIES:
        assert rep["critical_family_entitlement"][f] == "PARTIAL"
        assert rep["critical_family_coverage"][f] < rep["min_tickers_to_score"]
    # The coverage summary CSV reflects the blocker.
    cov = _read_csv(r["out"] / "phase8n_fmp_coverage_summary.csv")
    assert len(cov) == 6
    assert all(row["entitlement"] == "PARTIAL" for row in cov)


def test_real_phase8n_dir_reads_as_insufficient_if_present():
    """If the real committed Phase 8-N artifacts exist, they must read as blocked (the live
    run was INSUFFICIENT). Skips cleanly when the offline regen left them entitled/absent."""
    s8n = P8O.read_phase8n(P8O._PHASE8N_DIR)
    if not s8n["found"]:
        pytest.skip("no Phase 8-N artifacts on disk")
    if not s8n["missing_families"]:
        pytest.skip("Phase 8-N artifacts present but not in the blocked state")
    assert set(s8n["missing_families"]).issubset(set(P8O.CRITICAL_FAMILIES))


# --------------------------------------------------------------------------- #
# Provider matrix includes the four providers; AV for earnings, Finnhub for analyst.
# --------------------------------------------------------------------------- #
def test_provider_matrix_includes_four_providers(monkeypatch, tmp_path):
    r = _run(monkeypatch, tmp_path, blocked=True)
    rows = _read_csv(r["out"] / "provider_decision_matrix.csv")
    providers = {row["provider"] for row in rows}
    assert P8O.PROV_FMP in providers
    assert P8O.PROV_AV in providers
    assert P8O.PROV_FINNHUB in providers
    assert P8O.PROV_EODHD in providers
    # Required columns present.
    for col in ("provider", "data_family", "current_key_env_var", "key_present",
                "expected_coverage", "cost_tier_if_known", "supports_historical_data",
                "supports_point_in_time_backtest", "supports_sufficient_history",
                "endpoint_mapping_status", "recommended_for_family"):
        assert col in rows[0]


def test_alpha_vantage_ranked_for_earnings(monkeypatch, tmp_path):
    r = _run(monkeypatch, tmp_path, blocked=True)
    rows = _read_csv(r["out"] / "provider_decision_matrix.csv")
    for fam in ("earnings_surprises", "earnings_calendar"):
        av = [row for row in rows if row["provider"] == P8O.PROV_AV
              and row["data_family"] == fam]
        assert av and av[0]["recommended_for_family"] == "True"
    # The blocker summary routes the earnings families to Alpha Vantage.
    blk = _read_csv(r["out"] / "critical_family_blocker_summary.csv")
    earn = {row["critical_family"]: row for row in blk
            if row["critical_family"] in ("earnings_surprises", "earnings_calendar")}
    assert all(row["recommended_unlock_provider"] == P8O.PROV_AV for row in earn.values())


def test_finnhub_ranked_for_analyst(monkeypatch, tmp_path):
    r = _run(monkeypatch, tmp_path, blocked=True)
    rows = _read_csv(r["out"] / "provider_decision_matrix.csv")
    for fam in ("analyst_recommendations", "analyst_price_targets", "ratings_grades_consensus",
                "analyst_estimates"):
        fh = [row for row in rows if row["provider"] == P8O.PROV_FINNHUB
              and row["data_family"] == fam]
        assert fh and fh[0]["recommended_for_family"] == "True"


# --------------------------------------------------------------------------- #
# FMP Ultimate never recommended; upgrade not the default.
# --------------------------------------------------------------------------- #
def test_fmp_ultimate_not_recommended_by_default(monkeypatch, tmp_path):
    r = _run(monkeypatch, tmp_path, blocked=True)
    rep = r["report"]
    assert rep["fmp_ultimate_rejected"] is True
    assert rep["fmp_upgrade_justified"] is False
    acq = _read_json(r["out"] / "provider_acquisition_decision.json")
    assert acq["fmp_ultimate_rejected"] is True
    assert acq["fmp_upgrade_justified"] is False
    # No provider ROW (ranking, matrix, cost/value) is an "Ultimate" tier recommendation.
    for name in ("cheapest_viable_provider_ranking.csv", "provider_decision_matrix.csv",
                 "provider_cost_value_report.csv"):
        for row in _read_csv(r["out"] / name):
            assert "ultimate" not in row["provider"].lower()


# --------------------------------------------------------------------------- #
# Env-key gate: no alt key -> BLOCKED; keys present -> mixed strategy.
# --------------------------------------------------------------------------- #
def test_decision_blocked_when_no_alt_key(monkeypatch, tmp_path):
    # Only FMP key present (the real shell). No AV/Finnhub/EODHD key.
    r = _run(monkeypatch, tmp_path, blocked=True, set_keys=["FMP_API_KEY"])
    rep = r["report"]
    assert rep["decision"] == "BLOCKED_MISSING_ALTERNATIVE_PROVIDER_KEY"
    # The cheapest next provider is still named (Alpha Vantage) with its env var.
    assert rep["cheapest_provider_to_try_first"] == P8O.PROV_AV
    acq = _read_json(r["out"] / "provider_acquisition_decision.json")
    assert acq["first_provider_env_var"] == "ALPHAVANTAGE_API_KEY"
    assert acq["recommended_strategy"] == "MIXED_PROVIDER_STRATEGY_REQUIRED"


def test_decision_mixed_when_alt_keys_present(monkeypatch, tmp_path):
    r = _run(monkeypatch, tmp_path, blocked=True,
             set_keys=["FMP_API_KEY", "ALPHAVANTAGE_API_KEY", "FINNHUB_API_KEY"])
    rep = r["report"]
    assert rep["decision"] == "MIXED_PROVIDER_STRATEGY_REQUIRED"
    assert rep["cheapest_provider_to_try_first"] == P8O.PROV_AV
    assert rep["second_provider_only_if_needed"] == P8O.PROV_FINNHUB


def test_strategy_fmp_sufficient_when_nothing_blocked(monkeypatch, tmp_path):
    r = _run(monkeypatch, tmp_path, blocked=False)
    rep = r["report"]
    assert rep["blocked_families"] == []
    assert rep["decision"] == P8O.DEC_FMP_SUFFICIENT
    assert rep["decision_is_terminal"] is False  # sentinel, not one of the eight


# --------------------------------------------------------------------------- #
# Exact env vars + acquisition commands.
# --------------------------------------------------------------------------- #
def test_output_includes_exact_env_vars(monkeypatch, tmp_path):
    r = _run(monkeypatch, tmp_path, blocked=True)
    rep = r["report"]
    assert "ALPHAVANTAGE_API_KEY" in rep["env_vars_needed"]
    assert "FINNHUB_API_KEY" in rep["env_vars_needed"]
    setup = (r["out"] / "provider_env_var_setup_commands.ps1").read_text(encoding="utf-8")
    assert "ALPHAVANTAGE_API_KEY" in setup
    assert "FINNHUB_API_KEY" in setup


def test_acquisition_commands_produced(monkeypatch, tmp_path):
    r = _run(monkeypatch, tmp_path, blocked=True, set_keys=["FMP_API_KEY"])
    assert (r["out"] / "provider_env_var_setup_commands.ps1").is_file()
    acq = _read_json(r["out"] / "provider_acquisition_decision.json")
    assert acq["exact_next_command"]
    assert "ALPHAVANTAGE_API_KEY" in acq["exact_next_command"]
    assert "run_phase8o_cheapest_provider_selection.py --live" in acq["exact_next_command"]


# --------------------------------------------------------------------------- #
# Probe: offline no-key -> NOT_PROBED; injected transport -> probed.
# --------------------------------------------------------------------------- #
def test_probe_offline_no_key_not_probed(monkeypatch, tmp_path):
    r = _run(monkeypatch, tmp_path, blocked=True, set_keys=["FMP_API_KEY"])
    rows = _read_csv(r["out"] / "provider_probe_results.csv")
    assert rows
    assert all(row["probed"] == "False" for row in rows)
    assert all(row["status"] == "NOT_PROBED_NO_KEY" for row in rows)
    assert r["report"]["any_probe_executed"] is False


def test_probe_executes_with_transport(monkeypatch, tmp_path):
    r = _run(monkeypatch, tmp_path, blocked=True, transport=fake_transport)
    rows = _read_csv(r["out"] / "provider_probe_results.csv")
    probed = [row for row in rows if row["probed"] == "True"]
    assert probed
    assert any(row["status"] == "PROBED_OK" for row in probed)
    assert r["report"]["any_probe_executed"] is True
    # Verbatim payload persisted ONLY under the gitignored data tree, not the committed out dir.
    raws = list(r["data"].rglob("*.json"))
    assert raws
    for p in r["out"].glob("*"):
        assert p.parent == r["out"]  # nothing nested/raw under the committed dir
    # Each probed provider's data dir is force-gitignored (ignore * except .gitignore).
    for raw in raws:
        gi = raw.parent.parent / ".gitignore"
        assert gi.is_file(), gi
        text = gi.read_text(encoding="utf-8")
        assert "*" in text and "!.gitignore" in text


# --------------------------------------------------------------------------- #
# Secret discipline: no key printed/written; redaction strips secrets.
# --------------------------------------------------------------------------- #
def test_redact_url_strips_secrets():
    for param in ("apikey", "token", "api_token"):
        url = "https://example.com/q?symbol=AAPL&%s=SECRETVALUE" % param
        red = P8O.redact_url(url)
        assert "SECRETVALUE" not in red
        assert "%s=" % param not in red
        assert P8O.REDACTED_KEY_PLACEHOLDER in red


def test_no_api_key_printed_or_written(monkeypatch, tmp_path):
    r = _run(monkeypatch, tmp_path, blocked=True,
             set_keys=["FMP_API_KEY", "ALPHAVANTAGE_API_KEY", "FINNHUB_API_KEY"],
             transport=fake_transport)
    assert r["report"]["secret_safety_leak_scan_clean"] is True
    # Key inventory records presence only; value_read is always False.
    inv = _read_csv(r["out"] / "provider_key_inventory.csv")
    assert all(row["value_read"] == "False" for row in inv)
    # No committed artifact contains a key value or the apikey marker.
    for p in r["out"].glob("*"):
        if p.is_file():
            text = p.read_text(encoding="utf-8")
            assert "ZZTESTKEYZZ" not in text
            assert "apikey=" not in text.lower()


def test_key_inventory_presence_only(monkeypatch, tmp_path):
    r = _run(monkeypatch, tmp_path, blocked=True, set_keys=["ALPHAVANTAGE_API_KEY"])
    inv = {row["env_var"]: row for row in _read_csv(r["out"] / "provider_key_inventory.csv")}
    assert inv["ALPHAVANTAGE_API_KEY"]["key_present"] == "True"
    assert inv["FINNHUB_API_KEY"]["key_present"] == "False"


# --------------------------------------------------------------------------- #
# Committed-safe outputs only; no Paper Trader / GCP / order / deployment logic.
# --------------------------------------------------------------------------- #
def test_all_thirteen_artifacts_written(monkeypatch, tmp_path):
    r = _run(monkeypatch, tmp_path, blocked=True)
    for name in P8O._ARTIFACTS.values():
        assert (r["out"] / name).is_file(), name
    assert len(P8O._ARTIFACTS) == 13


def test_safety_contract_flags(monkeypatch, tmp_path):
    r = _run(monkeypatch, tmp_path, blocked=True, transport=fake_transport)
    rep = r["report"]
    for flag in ("orders_enabled", "automation_enabled", "broker_execution_enabled",
                 "deployed", "paper_trader_touched", "gcp_touched", "committed",
                 "data_fabricated"):
        assert rep[flag] is False
    assert rep["preview_only"] is True


def test_no_forbidden_logic_tokens(monkeypatch, tmp_path):
    r = _run(monkeypatch, tmp_path, blocked=True)
    # Execution/automation LOGIC tokens (the provenance flags paper_trader_touched/gcp_touched
    # are asserted False separately in test_safety_contract_flags).
    forbidden = ("place_order", "submit_order", "broker.execute", "create_order",
                 "gcloud ", "automation_enabled=true")
    for p in r["out"].glob("*"):
        if p.is_file():
            low = p.read_text(encoding="utf-8").lower()
            for tok in forbidden:
                assert tok not in low, (p.name, tok)
