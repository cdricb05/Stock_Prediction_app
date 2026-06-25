"""Tests for Phase 8-F — Autonomous External Signal Research Operating System.

Covers the OS contract: vocab/agents/families, provider-key detection (presence only, never
values), the six external schemas, connector materialization (adapter + schema + MOCK fixture,
no live collection, no secrets), the S8E-011 deep-dive + fixed-filter stress (leak-safe), the
experiment ledger + budget guardrails, promotion/registry/graveyard, recommendation branches,
research memory, the safety block, and a synthetic end-to-end that emits all 38 artifacts.
"""
import importlib.util
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

REPO = Path(__file__).resolve().parents[1]


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, REPO / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


F = _load("phase8f_under_test", "research/run_phase8f_autonomous_external_signal_os.py")
P = F.P8E


# --------------------------------------------------------------------------- #
# Synthetic 8-E panel (planted oil edge) reused by the e2e/panel tests.
# --------------------------------------------------------------------------- #
def _synth_panel(n_sym=70, seed=5):
    rng = np.random.default_rng(seed)
    days = pd.bdate_range("1996-01-01", "2026-06-30")
    sectors = ["Energy", "Information Technology", "Financials", "Health Care", "Industrials"]
    spy_close = pd.Series(100 * np.cumprod(1 + 0.0003 + 0.009 * rng.standard_normal(len(days))), index=days)
    oil_ret = 0.02 * rng.standard_normal(len(days))
    oil_close = pd.Series(50 * np.cumprod(1 + oil_ret), index=days)
    proxy_close, proxy_ret = {}, {}
    for d in P.SENS_DRIVERS:
        proxy = P.DRIVER_BY_KEY[d].proxy
        s = oil_close if d == "oil" else pd.Series(
            100 * np.cumprod(1 + 0.0002 + 0.012 * rng.standard_normal(len(days))), index=days)
        proxy_close[proxy] = s
        proxy_ret[proxy] = s.pct_change()
    for etf in set(P.SECTOR_ETF.values()):
        s = pd.Series(100 * np.cumprod(1 + 0.0002 + 0.011 * rng.standard_normal(len(days))), index=days)
        proxy_close[etf] = s
        proxy_ret[etf] = s.pct_change()
    grid_dates = P._weekly_grid_dates(days)
    shock_grid = P.build_driver_shock_grid(proxy_close, grid_dates)
    feat_cols = [c for c, _a, _d in P.P8D.FEATURE_CATALOG]
    label_cols = [c for c, _d in P.P8D.EVENT_LABELS]
    sens_cols = [f"sens_beta_{d}" for d in P.SENS_DRIVERS] + ["sens_beta_sector"]
    blocks, meta = [], []
    for i in range(n_sym):
        sector = sectors[i % len(sectors)]
        oil_load = 1.4 if i % 3 == 0 else 0.0
        rets = (0.0002 + 0.0006 * rng.standard_normal()) + 0.012 * rng.standard_normal(len(days)) + oil_load * oil_ret
        close = pd.Series(50 * np.cumprod(1 + rets), index=days)
        vol = pd.Series(1e6 * (1 + rng.random(len(days))), index=days)
        feats = P.symbol_features(close, vol, close * vol, spy_close)
        sens, _ = P.symbol_sensitivities(close, sector, proxy_ret)
        labels = P.forward_labels(close, spy_close)
        frame = pd.concat([feats, sens, labels], axis=1).reindex(grid_dates, method="ffill", limit=5)
        frame = frame.dropna(subset=["rv_20", "ret_60", "fwd_excess_20", "fwd_total_5", "sens_beta_market"])
        if frame.empty:
            continue
        frame = frame.reset_index().rename(columns={"index": "date"})
        if "date" not in frame.columns:
            frame = frame.rename(columns={frame.columns[0]: "date"})
        frame["symbol"] = f"S{i:03d}"
        frame["sector"] = sector
        blocks.append(frame)
        meta.append({"ticker": f"S{i:03d}", "gics_sector": sector,
                     "sector_etf": P.SECTOR_ETF.get(sector, ""), "first_quoted_date": "1996-01-01",
                     "last_quoted_date": "", "is_delisted": (i % 5 == 0), "n_grid_obs": len(frame)})
    grid = pd.concat(blocks, ignore_index=True)
    keep = ["date", "symbol", "sector"] + feat_cols + sens_cols + label_cols
    grid = grid[[c for c in keep if c in grid.columns]]
    grid = P._add_cross_sectional(grid, shock_grid)
    mask = (grid["cohort_oil_pos"] >= 1.0) & (grid["drv_oil_shock_z"] >= P.SHOCK_Z)
    for h in (5, 10, 20, 60):
        grid.loc[mask, f"fwd_excess_{h}"] += 0.02
    grid.loc[mask, "fwd_total_5"] += 0.02
    return F.SensPanel(grid, pd.DataFrame(meta).set_index("ticker"), spy_close, grid_dates, True,
                       pd.DataFrame(), [])


@pytest.fixture(scope="module")
def panel():
    return _synth_panel()


@pytest.fixture(scope="module")
def driven(panel):
    readiness = F.provider_readiness(F.detect_provider_keys())
    ledger, cycles, filter_rows, _state = F.run_cycles(panel, readiness)
    deep = getattr(F.run_cycles, "_deep_rows", [])
    tail = getattr(F.run_cycles, "_tail_rows", [])
    return dict(readiness=readiness, ledger=ledger, cycles=cycles, filter_rows=filter_rows,
                deep=deep, tail=tail)


# --------------------------------------------------------------------------- #
# Vocab / agents / families.
# --------------------------------------------------------------------------- #
def test_recommendation_vocab_exact_and_ordered():
    assert F.ALLOWED_RECOMMENDATIONS == (
        "CONFIRMED_EXTERNAL_SENSITIVITY_SIGNAL_FOUND", "PROMISING_NEEDS_PROVIDER_HISTORY",
        "PROMISING_BUT_UNCONFIRMED", "NEEDS_NEWS_SENTIMENT_PROVIDER_DATA",
        "NEEDS_ANALYST_REVISION_PROVIDER_DATA", "NEEDS_EXTERNAL_PROVIDER_DATA",
        "EXTERNAL_SIGNAL_RESEARCH_REJECTED", "AUTONOMOUS_SIGNAL_OS_READY_BUT_PROVIDER_BLOCKED",
        "ASSESSMENT_FRAMEWORK_BLOCKED", "ERROR")


def test_status_vocab():
    assert F.ST_CONFIRMED == "CONFIRMED_EXTERNAL_SENSITIVITY_SIGNAL"
    assert set(F.ALLOWED_STATUSES) == {
        "CONFIRMED_EXTERNAL_SENSITIVITY_SIGNAL", "PROMISING_NEEDS_PROVIDER_HISTORY",
        "PROMISING_BUT_UNCONFIRMED", "NEEDS_PROVIDER_DATA", "REJECTED", "BLOCKED"}


def test_agent_roster_15():
    assert len(F.ALL_AGENTS) == 15
    for a in ("quant-research-director", "data-foundation-agent", "external-data-agent",
              "news-sentiment-agent", "analyst-revision-agent", "earnings-event-agent",
              "options-signal-agent", "short-interest-agent", "transcript-tone-agent",
              "sensitivity-signal-agent", "validation-skeptic-agent", "risk-portfolio-agent",
              "model-contribution-agent"):
        assert a in F.ALL_AGENTS


def test_families_and_provider_families():
    assert F.FAM_MACRO == "G_macro_cross_asset_x_sensitivity"
    assert F.FAM_MACRO not in F.PROVIDER_FAMILIES
    for fam in (F.FAM_NEWS, F.FAM_REVISION, F.FAM_EARNINGS, F.FAM_OPTIONS, F.FAM_SHORT, F.FAM_S8E011_EXT):
        assert fam in F.PROVIDER_FAMILIES


def test_artifacts_count_38():
    assert len(F.ARTIFACTS) == 38
    assert "phase8g_next_plan.json" in F.ARTIFACTS
    assert "provider_acquisition_commands.ps1" in F.ARTIFACTS


# --------------------------------------------------------------------------- #
# Provider/key detection — presence only, never values.
# --------------------------------------------------------------------------- #
def test_detect_keys_presence_only_no_values(monkeypatch):
    for k in F.PROVIDER_KEY_ENV:
        monkeypatch.delenv(k, raising=False)
    rows = F.detect_provider_keys()
    assert len(rows) == len(F.PROVIDER_KEY_ENV)
    assert all(r["present"] is False for r in rows)
    # the row schema must never contain a value field
    assert all(set(r.keys()) == {"key_env_var", "present", "feeds_families", "detection"} for r in rows)


def test_detect_keys_present_routes_to_family(monkeypatch):
    monkeypatch.setenv("FMP_API_KEY", "super-secret-value")
    rows = F.detect_provider_keys()
    fmp = next(r for r in rows if r["key_env_var"] == "FMP_API_KEY")
    assert fmp["present"] is True
    # the secret value is never echoed anywhere in the row
    assert "super-secret-value" not in str(fmp)
    assert F.FAM_REVISION in fmp["feeds_families"]
    readiness = F.provider_readiness(rows)
    assert readiness[F.FAM_REVISION] is True


def test_config_scan_names_only():
    rows = F.scan_config_for_key_names()
    assert all("note" in r and "values never read" in r["note"] or not r["exists"] for r in rows)


# --------------------------------------------------------------------------- #
# External schemas (Part 5) — exact fields.
# --------------------------------------------------------------------------- #
def test_news_schema_exact():
    assert F.SCHEMA_NEWS == ["event_timestamp", "point_in_time_available_at", "ticker", "provider",
                             "headline", "summary", "article_url_or_provider_id", "sentiment_score",
                             "sentiment_label", "relevance_score", "source", "topic_tags",
                             "raw_payload_path"]


def test_analyst_revision_schema_exact():
    assert F.SCHEMA_REVISION == ["event_timestamp", "point_in_time_available_at", "ticker",
                                 "fiscal_period", "estimate_type", "old_value", "new_value",
                                 "revision_direction", "revision_magnitude", "analyst_count",
                                 "consensus_change", "provider_id", "raw_payload_path"]


def test_all_six_schemas_present():
    assert set(F.SCHEMA_BY_NAME) == {"news_sentiment", "analyst_revisions", "earnings",
                                     "options_iv", "short_interest", "transcript_tone"}
    for fields in F.SCHEMA_BY_NAME.values():
        assert "event_timestamp" in fields and "point_in_time_available_at" in fields
        assert "raw_payload_path" in fields


# --------------------------------------------------------------------------- #
# Connectors — adapter + schema header + MOCK fixture; no live collection; no secrets.
# --------------------------------------------------------------------------- #
def test_build_connectors_materializes_adapter_schema_and_mock():
    readiness = {f: False for f in F.FAMILY_PROVIDERS}
    status, raw, norm, manifests = F.build_connectors(readiness)
    assert len(status) == len(F.EXTERNAL_SOURCES) == 6
    for s in status:
        assert Path(s["adapter_path"]).exists()
        assert Path(s["normalized_schema_path"]).exists()
        assert Path(s["mock_fixture_path"]).exists()
        assert s["live_collection_ran"] is False
        assert s["connector_mode"] == "ADAPTER_AND_MOCK_NO_KEY"
    # mock fixtures are explicitly labelled and never claim real events
    for n in norm:
        assert n["n_real_events"] == 0
        assert n["n_mock_events"] == F._MOCK_N
    df = pd.read_csv(status[0]["mock_fixture_path"])
    assert "is_mock" in df.columns and bool(df["is_mock"].all())


def test_adapter_source_no_real_secret_and_dry_run():
    src = F.EXTERNAL_SOURCES[0]
    code = F._adapter_source(src, F.FAMILY_PROVIDERS[src.family])
    assert "value never returned/printed" in code or "value never" in code
    assert "DRY_RUN_NO_KEY" in code
    # adapter must be syntactically valid python
    compile(code, "<adapter>", "exec")


def test_acquisition_commands_no_secrets(tmp_path):
    readiness = {f: False for f in F.FAMILY_PROVIDERS}
    status, *_ = F.build_connectors(readiness)
    path = F.write_acquisition_commands(tmp_path, status)
    text = path.read_text(encoding="utf-8")
    assert "<YOUR_KEY>" in text
    assert "NO secrets" in text


# --------------------------------------------------------------------------- #
# External experiments + macro mapping.
# --------------------------------------------------------------------------- #
def test_external_experiments_need_provider():
    exps = F._external_experiments(cycle=1)
    assert len(exps) == 6
    assert all(e.needs_provider for e in exps)
    assert all(e.status == F.ST_NEEDS_PROVIDER for e in exps)
    fams = {e.family for e in exps}
    assert F.FAM_NEWS in fams and F.FAM_REVISION in fams and F.FAM_S8E011_EXT in fams


def test_macro_status_mapping():
    class _S:
        def __init__(self, status, ch=False):
            self.status = status
            self.is_challenge = ch
    assert F._map_macro_status(_S(P.ST_CONFIRMED), None) == F.ST_CONFIRMED
    assert F._map_macro_status(_S(P.ST_PROMISING), None) == F.ST_PROMISING_UNCONF
    assert F._map_macro_status(_S(P.ST_REJECTED), None) == F.ST_REJECTED
    assert F._map_macro_status(_S(P.ST_BLOCKED), None) == F.ST_BLOCKED


# --------------------------------------------------------------------------- #
# S8E-011 deep dive + fixed-filter stress (leak-safe, ex-ante buckets only).
# --------------------------------------------------------------------------- #
def test_s8e011_setup_retrievable():
    s = F._s8e011_setup()
    assert s.setup_id == "S8E-011"
    assert s.cohort == "cohort_rates_neg" and s.driver == "rates"


def test_deep_dive_dimensions(panel):
    deep, tail = F.s8e011_deep_dive(panel)
    dims = {r["dimension"] for r in deep}
    for d in ("ALL", "sector", "year", "liquidity_bucket", "volatility_bucket",
              "beta_bucket", "active_vs_delisted"):
        assert d in dims


def test_fixed_filters_are_ex_ante_only():
    # every fixed filter references a structural bucket known before the event, never an outcome
    for name, expr in F._fixed_filters():
        assert "fwd_" not in expr and "worst_decile" not in expr
        assert any(tok in expr for tok in ("liq_bucket", "vol_bucket", "beta_bucket"))


def test_fixed_filter_stress_has_baseline(panel):
    rows = F.s8e011_fixed_filter_stress(panel)
    labels = {r["filter"] for r in rows}
    assert "baseline_no_filter" in labels
    assert len(rows) >= 2


# --------------------------------------------------------------------------- #
# Budget guardrails.
# --------------------------------------------------------------------------- #
def test_budget_guardrails(driven):
    b = F._budget(driven["ledger"])
    assert b["within_total_budget"]
    assert b["per_family_ok"]
    assert b["challenge_ok"]
    assert b["all_pre_registered"]
    assert b["experiments_registered"] <= F.MAX_TOTAL_EXPERIMENTS
    assert all(v <= F.MAX_PER_FAMILY for v in b["per_family_counts"].values())


# --------------------------------------------------------------------------- #
# Recommendation branches.
# --------------------------------------------------------------------------- #
def _exp(status, family=None, needs=False, n_events=None, challenge=False):
    metrics = {"n_events": n_events} if n_events else {}
    return F.Experiment("X", 1, family or F.FAM_MACRO, F.SENS_A, "signal_test", challenge,
                        "rates", "cohort_rates_neg", needs, "h", status=status, metrics=metrics)


def test_rec_framework_blocked():
    rec, _ = F.derive_recommendation(False, True, {}, [])
    assert rec == F.REC_FRAMEWORK_BLOCKED


def test_rec_confirmed_wins():
    rec, _ = F.derive_recommendation(True, True, {}, [_exp(F.ST_CONFIRMED, n_events=2000)])
    assert rec == F.REC_CONFIRMED


def test_rec_promising_unconfirmed():
    rec, _ = F.derive_recommendation(True, True, {}, [_exp(F.ST_PROMISING_UNCONF, n_events=2000)])
    assert rec == F.REC_PROMISING_UNCONF


def test_rec_os_ready_blocked_when_only_provider():
    rdy = {f: False for f in F.FAMILY_PROVIDERS}
    ledger = F._external_experiments(cycle=1)            # all needs_provider, no testable scored
    rec, _ = F.derive_recommendation(True, False, rdy, ledger)
    assert rec == F.REC_OS_READY_BLOCKED


def test_rec_needs_analyst_when_testable_rejected_and_provider_remains():
    ledger = [_exp(F.ST_REJECTED, n_events=2000),
              _exp(F.ST_NEEDS_PROVIDER, family=F.FAM_REVISION, needs=True)]
    rec, _ = F.derive_recommendation(True, True, {}, ledger)
    assert rec == F.REC_NEEDS_ANALYST


def test_rec_rejected_when_all_testable_fail_no_provider():
    rec, _ = F.derive_recommendation(True, True, {}, [_exp(F.ST_REJECTED, n_events=2000)])
    assert rec == F.REC_REJECTED


# --------------------------------------------------------------------------- #
# Promotion / model registry / graveyard / memory / safety.
# --------------------------------------------------------------------------- #
def test_model_candidates_never_deploy(driven):
    rows = F.model_candidate_rows(driven["ledger"], driven["filter_rows"])
    assert rows
    for r in rows:
        assert r["deployed"] is False
        assert r["paper_trader_output"] is False
        assert r["production"] is False


def test_graveyard_carries_forward(driven):
    rows = F.graveyard_rows(driven["ledger"])
    phases = {r["origin_phase"] for r in rows}
    for ph in ("8-B", "8-C", "8-D", "8-E"):
        assert ph in phases
    assert any(r["origin_phase"] == "8-F" for r in rows)


def test_research_memory_required_keys(driven, panel):
    mem = F.build_research_memory("PROMISING_BUT_UNCONFIRMED", {}, driven["ledger"],
                                  driven["readiness"], driven["filter_rows"], panel)
    for k in ("current_best_leads", "rejected_families", "external_data_gaps", "provider_readiness",
              "confirmed_signals", "promising_signals", "rejected_signals",
              "open_research_questions", "next_autonomous_actions", "stop_conditions"):
        assert k in mem
    assert mem["external_data_gaps"]


def test_safety_block_all_forbidden_false():
    sb = F._safety_block({f: False for f in F.FAMILY_PROVIDERS})
    for flag in ("packages_installed", "external_data_faked", "optimized_weights",
                 "factor_signs_modified_after_results", "regime_activation", "ml_fit",
                 "live_trading_signals", "broker_or_orders", "automation", "paper_trader_touched",
                 "gcp_touched", "committed", "pushed", "secrets_printed",
                 "live_external_collection_ran", "provider_key_values_printed"):
        assert sb[flag] is False
    assert sb["mock_fixtures_labelled_and_excluded_from_scoreboard"] is True


# --------------------------------------------------------------------------- #
# End-to-end on the synthetic panel: 38 artifacts, allowed recommendation, no confirmed.
# --------------------------------------------------------------------------- #
def test_e2e_emits_all_artifacts(tmp_path, driven, panel):
    readiness = driven["readiness"]
    ledger, cycles, filter_rows = driven["ledger"], driven["cycles"], driven["filter_rows"]
    status, raw, norm, manifests = F.build_connectors(readiness)
    key_rows = F.detect_provider_keys()
    config_rows = F.scan_config_for_key_names()
    acq = F.write_acquisition_commands(tmp_path, status)
    rec, detail = F.derive_recommendation(True, True, readiness, ledger)
    assert rec in F.ALLOWED_RECOMMENDATIONS
    report = F._assemble_report(F._utc_now_iso(), rec, detail, panel, True, ledger, readiness,
                                key_rows, config_rows, cycles, filter_rows)
    F._emit_all(tmp_path, report, panel, True, ledger, readiness, key_rows, config_rows, status,
                raw, norm, manifests, cycles, filter_rows, driven["deep"], driven["tail"], acq)
    missing = [a for a in F.ARTIFACTS if not (tmp_path / a).exists()]
    assert missing == []


def test_e2e_no_confirmed_under_synthetic(driven):
    # planted oil edge should surface a promising-unconfirmed lead, never a CONFIRMED external signal
    confirmed = [e for e in driven["ledger"] if e.status == F.ST_CONFIRMED]
    assert confirmed == []
    assert any(e.status == F.ST_PROMISING_UNCONF for e in driven["ledger"])


def test_e2e_news_sentiment_blocked_no_key(driven):
    assert driven["readiness"][F.FAM_NEWS] is False
    news = [e for e in driven["ledger"] if e.family == F.FAM_NEWS]
    assert news and all(e.needs_provider for e in news)
