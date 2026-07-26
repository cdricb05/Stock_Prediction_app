"""Phase 29C — deterministic feature campaign executor and director feedback.

Covers the Part N contract (74 checks):
  Feature execution (1-10)
  Point-in-time     (11-20)
  Campaign          (21-30)
  IC screen         (31-36)
  Portfolio         (37-44)
  Robustness        (45-50)
  Feedback          (51-58)
  Safety            (59-65)
  CLI               (66-74)

Fully offline: the deterministic synthetic Phase 29A world runs one real
COMPLETE campaign plus one fixture director session in a tmp artifact
root; the feature campaign then executes the three fixture hypotheses on
real code paths. The Paper Trader desk directory is only ever os.stat'ed;
CLI-driven runs monkeypatch the loaders so no owned D:\\ data is read.
"""

import contextlib
import copy
import io
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_phase29a_research_agent_kernel import (  # noqa: E402
    CUTOFF,
    TODAY,
    _build_world,
    _snapshot_dir,
    make_config,
)

from research_agent import SAFETY_CONTRACT  # noqa: E402
from research_agent import cli  # noqa: E402
from research_agent import controller as ctl  # noqa: E402
from research_agent import director as dr  # noqa: E402
from research_agent import director_feedback as dfk  # noqa: E402
from research_agent import director_provider as dp  # noqa: E402
from research_agent import evaluator as ev  # noqa: E402
from research_agent import family_backtest as fb  # noqa: E402
from research_agent import feature_campaign as fc  # noqa: E402
from research_agent import feature_evaluation as fe  # noqa: E402
from research_agent import feature_execution as fx  # noqa: E402
from research_agent.artifact_store import (  # noqa: E402
    ArtifactStore,
    content_hash,
    read_jsonl,
)
from research_agent.feature_dsl import (  # noqa: E402
    compile_feature_set,
    feature_set_signature,
    validate_feature_set,
    validate_feature_spec,
)

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIRECTOR_CONFIG_PATH = os.path.join(
    REPO, "configs", "research_agent", "phase29b_research_director.json")
FEATURE_CONFIG_PATH = os.path.join(
    REPO, "configs", "research_agent", "phase29c_feature_campaign.json")
DESK_DIR = os.path.join(os.path.expanduser("~"), ".paper_trader",
                        "paper_trading_desk")


def _src(field):
    return {"op": "source", "field": field}


def _feat(fid, expr, family="momentum", desc="test feature"):
    return {"feature_id": fid, "description": desc,
            "source_family": family, "expression": expr}


def _read_bytes(path):
    with open(path, "rb") as fh:
        return fh.read()


def _cli(argv):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = cli.main(argv)
    out = buf.getvalue()
    payload = None
    if out.strip().startswith("{"):
        payload = json.loads(out)
    return code, payload, out


# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def guard_snapshots():
    """Taken BEFORE any campaign runs; compared by the safety tests."""
    return {"desk": _snapshot_dir(DESK_DIR)}


@pytest.fixture(scope="module")
def world(guard_snapshots):
    return _build_world()


@pytest.fixture(scope="module")
def synth_inputs(world):
    return fb.load_family_inputs(
        data_cutoff=CUTOFF,
        mom_monthly=world["mom_monthly"],
        fund_monthly=world["fund_monthly"],
        sector_map={},
        spy_close=world["spy_close"],
    )


@pytest.fixture(scope="module")
def director_cfg():
    with open(DIRECTOR_CONFIG_PATH, "r", encoding="utf-8") as fh:
        return json.load(fh)


@pytest.fixture(scope="module")
def feature_cfg():
    with open(FEATURE_CONFIG_PATH, "r", encoding="utf-8") as fh:
        return json.load(fh)


@pytest.fixture(scope="module")
def pipeline(tmp_path_factory, world, synth_inputs, director_cfg):
    """One COMPLETE 29A campaign + one COMPLETE fixture director session."""
    root = str(tmp_path_factory.mktemp("agent29c_root"))
    created = ctl.create_campaign(make_config(), artifact_root=root,
                                  today=TODAY)
    assert created["created"], created
    cid = created["campaign_id"]
    controller = ctl.CampaignController(
        cid, artifact_root=root, today=TODAY, inputs=synth_inputs,
        reference_rows=[], close_frame=world["close_frame"],
    )
    result = controller.run()
    assert result.get("final_state") == "COMPLETE", result
    provider = dp.get_provider("fixture", director_config=director_cfg)
    session = dr.run_director_session(
        artifact_root=root, campaign_id=cid, director_config=director_cfg,
        provider=provider, output_root=root,
    )
    assert session["status"] == "COMPLETE", session
    assert len(session["queued"]) == 3
    return {"root": root, "campaign_id": cid,
            "session_id": session["session_id"],
            "store": ArtifactStore(root)}


@pytest.fixture(scope="module")
def panel(synth_inputs):
    return fx.FeaturePanel(synth_inputs)


@pytest.fixture(scope="module")
def inventory(synth_inputs):
    return fx.build_source_inventory(synth_inputs)


@pytest.fixture(scope="module")
def numeric_fields(inventory):
    return sorted(inventory["numeric_sources"].keys())


def _compiled(features, numeric_fields):
    doc = compile_feature_set(features, available_fields=numeric_fields)
    assert doc["compiled"], doc["verdict"]["violations"]
    return doc


@pytest.fixture(scope="module")
def mom_series(panel, inventory):
    return panel.source_series("mom_6_1", inventory)


@pytest.fixture(scope="module")
def fcamp(pipeline, feature_cfg, synth_inputs, world):
    """Feature campaign created from the fixture session and run to terminal."""
    made = fc.create_feature_campaign(
        config=feature_cfg, director_root=pipeline["root"],
        session_id=pipeline["session_id"],
        source_campaign_id=pipeline["campaign_id"],
        artifact_root=pipeline["root"], now_label="t29c",
    )
    assert made["created"], made
    fcid = made["feature_campaign_id"]
    fctl = fc.FeatureCampaignController(
        fcid, artifact_root=pipeline["root"], inputs=synth_inputs,
        reference_rows=[], close_frame=world["close_frame"],
    )
    store = fctl.store
    plan_before_run = fctl.plan()
    experiments_before_run = store.experiments(fcid)

    run1 = fctl.run(max_experiments=2)
    events_after_run1 = _read_bytes(store.campaign_dir(fcid) / "events.jsonl")
    invocations_after_run1 = _read_bytes(
        store.campaign_dir(fcid) / "invocations.jsonl")
    experiments_after_run1 = store.experiments(fcid)
    exp_rows_after_run1 = read_jsonl(
        store.campaign_dir(fcid) / "experiment_index.jsonl")

    runs = [run1]
    last = run1
    for _ in range(6):
        if last["status"] != ctl.RUN_PAUSED:
            break
        last = fctl.run(max_experiments=2)
        runs.append(last)
    assert last["status"] == ctl.RUN_OK, last
    report = fc.write_feature_report(store, fcid)["report"]
    return {
        "fcid": fcid, "controller": fctl, "store": store,
        "made": made, "plan_before_run": plan_before_run,
        "experiments_before_run": experiments_before_run,
        "run1": run1, "runs": runs, "final": last,
        "events_after_run1": events_after_run1,
        "invocations_after_run1": invocations_after_run1,
        "experiments_after_run1": experiments_after_run1,
        "exp_rows_after_run1": exp_rows_after_run1,
        "report": report,
    }


@pytest.fixture(scope="module")
def feedback_flow(fcamp):
    store, fcid = fcamp["store"], fcamp["fcid"]
    request_path = store.feedback_dir(fcid) / "feedback_request.json"
    first = dfk.run_feedback_cycle(store, fcid, provider_name="fixture")
    second = dfk.run_feedback_cycle(store, fcid, provider_name="fixture")
    with open(request_path, "r", encoding="utf-8") as fh:
        request = json.load(fh)
    return {"first": first, "second": second, "request": request}


@pytest.fixture(scope="module")
def cli_flow(pipeline, world, synth_inputs, feature_cfg):
    """Full CLI-driven flow with patched loaders (no owned data reads)."""
    mp = pytest.MonkeyPatch()
    orig_pit = fb.validate_point_in_time_integrity
    mp.setattr(fb, "load_family_inputs", lambda **kw: synth_inputs)
    mp.setattr(
        fb, "validate_point_in_time_integrity",
        lambda inputs, **kw: orig_pit(
            inputs, **dict(kw, close_frame=world["close_frame"])),
    )
    root = pipeline["root"]
    steps = {}
    steps["create"] = _cli([
        "feature-create",
        "--director-session-id", pipeline["session_id"],
        "--director-root", root,
        "--source-campaign-id", pipeline["campaign_id"],
        "--artifact-root", root,
        "--config", FEATURE_CONFIG_PATH,
        "--json",
    ])
    fcid = (steps["create"][1] or {}).get("feature_campaign_id")
    steps["fcid"] = fcid
    steps["plan"] = _cli(["feature-plan", "--feature-campaign-id", fcid,
                          "--artifact-root", root, "--json"])
    steps["run_limited"] = _cli(["feature-run", "--feature-campaign-id", fcid,
                                 "--artifact-root", root,
                                 "--max-experiments", "1", "--json"])
    last = None
    for _ in range(8):
        code, payload, _txt = _cli(
            ["feature-resume", "--feature-campaign-id", fcid,
             "--artifact-root", root, "--max-experiments", "6", "--json"])
        last = (code, payload)
        if payload and payload.get("status") == ctl.RUN_OK:
            break
    steps["resume_final"] = last
    store = fc.FeatureCampaignStore(root)
    events_at_complete = _read_bytes(store.campaign_dir(fcid) / "events.jsonl")
    steps["resume_after_complete"] = _cli(
        ["feature-resume", "--feature-campaign-id", fcid,
         "--artifact-root", root, "--json"])
    steps["events_unchanged_after_terminal_resume"] = (
        _read_bytes(store.campaign_dir(fcid) / "events.jsonl")
        == events_at_complete
    )
    snap_before_status = _snapshot_dir(str(store.campaign_dir(fcid)))
    steps["status"] = _cli(["feature-status", "--feature-campaign-id", fcid,
                            "--artifact-root", root, "--json"])
    steps["status_dir_unchanged"] = (
        _snapshot_dir(str(store.campaign_dir(fcid))) == snap_before_status)
    steps["report"] = _cli(["feature-report", "--feature-campaign-id", fcid,
                            "--artifact-root", root, "--json"])
    steps["feedback"] = _cli(
        ["director-feedback", "--feature-campaign-id", fcid,
         "--artifact-root", root, "--provider", "fixture", "--json"])
    mp.undo()
    return steps


# =========================================================================== #
# Feature execution (1-10)
# =========================================================================== #
class TestFeatureExecution:
    def test_c01_lag_executes_deterministically(self, panel, inventory,
                                                numeric_fields, mom_series):
        compiled = _compiled(
            [_feat("f_lag", {"op": "lag", "params": {"periods": 2},
                             "inputs": [_src("mom_6_1")]})], numeric_fields)
        r1 = fx.execute_feature_set(compiled, panel, inventory)
        r2 = fx.execute_feature_set(compiled, panel, inventory)
        assert r1["executed"] and r2["executed"]
        assert r1["feature_content_hash"] == r2["feature_content_hash"]
        series = r1["features"]["f_lag"]["series"]
        month = sorted(series)[len(series) // 2]
        prev = fx._add_months(month, -2)
        for tk in list(sorted(series[month]))[:5]:
            assert series[month][tk] == mom_series[prev][tk]

    def test_c02_rolling_mean_is_ticker_local_and_trailing(
            self, panel, inventory, numeric_fields, mom_series):
        compiled = _compiled(
            [_feat("f_rm", {"op": "rolling_mean", "params": {"window": 3},
                            "inputs": [_src("mom_6_1")]})], numeric_fields)
        out = fx.execute_feature_set(compiled, panel, inventory)
        series = out["features"]["f_rm"]["series"]
        month = sorted(series)[len(series) // 2]
        for tk in list(sorted(series[month]))[:5]:
            vals = [mom_series[fx._add_months(month, -j)][tk]
                    for j in range(3)]  # trailing window, this ticker only
            assert series[month][tk] == pytest.approx(sum(vals) / 3.0)

    def test_c03_cross_sectional_rank_is_month_local(
            self, panel, inventory, numeric_fields, mom_series):
        compiled = _compiled(
            [_feat("f_rk", {"op": "cross_sectional_rank", "params": {},
                            "inputs": [_src("mom_6_1")]})], numeric_fields)
        out = fx.execute_feature_set(compiled, panel, inventory)
        series = out["features"]["f_rk"]["series"]
        month = sorted(series)[3]
        expected = fb._rank_desc_pct(dict(mom_series[month]))
        assert series[month] == expected

    def test_c04_winsorize_is_month_local(self, panel, inventory,
                                          numeric_fields, mom_series):
        compiled = _compiled(
            [_feat("f_wz", {"op": "winsorize",
                            "params": {"lower_pct": 0.1, "upper_pct": 0.9},
                            "inputs": [_src("mom_6_1")]})], numeric_fields)
        out = fx.execute_feature_set(compiled, panel, inventory)
        series = out["features"]["f_wz"]["series"]
        month = sorted(series)[3]
        s = sorted(mom_series[month].values())
        lo, hi = fx._quantile(s, 0.1), fx._quantile(s, 0.9)
        assert max(series[month].values()) <= hi + 1e-12
        assert min(series[month].values()) >= lo - 1e-12

    def test_c05_sector_neutralize_is_month_and_sector_local(
            self, panel, inventory, numeric_fields):
        compiled = _compiled(
            [_feat("f_sn", {"op": "sector_neutralize", "params": {},
                            "inputs": [_src("mom_6_1")]})], numeric_fields)
        out = fx.execute_feature_set(compiled, panel, inventory)
        series = out["features"]["f_sn"]["series"]
        month = sorted(series)[3]
        groups = {}
        for tk, v in series[month].items():
            groups.setdefault(panel.sector(month, tk), []).append(v)
        for sec, vals in groups.items():
            assert sum(vals) / len(vals) == pytest.approx(0.0, abs=1e-9), sec

    def test_c06_identical_inputs_identical_hashes(
            self, synth_inputs, inventory, numeric_fields):
        compiled = _compiled(
            [_feat("f_h", {"op": "zscore", "params": {"window": 6},
                           "inputs": [_src("mom_6_1")]})], numeric_fields)
        p1 = fx.FeaturePanel(synth_inputs)
        p2 = fx.FeaturePanel(synth_inputs)
        h1 = fx.execute_feature_set(compiled, p1, inventory)
        h2 = fx.execute_feature_set(compiled, p2, inventory)
        assert h1["feature_content_hash"] == h2["feature_content_hash"]

    def test_c07_unknown_compiled_operation_rejected(self, panel, inventory):
        doctored = {"compiled": True, "steps": [{
            "feature_id": "f_bad", "source_family": "momentum",
            "output": "step_1",
            "steps": [{"step": "step_1", "transform": "quantum_warp",
                       "params": {}, "inputs": ["source:mom_6_1"]}],
            "signature": "dsl:doctored",
        }]}
        out = fx.execute_feature_set(doctored, panel, inventory)
        assert not out["executed"]
        assert out["status"] == "REJECTED_INVALID"
        assert any("unknown compiled operation" in v["issue"]
                   for v in out["violations"])

    def test_c08_no_arbitrary_execution_path(self, panel, inventory):
        src = open(fx.__file__, "r", encoding="utf-8").read()
        for token in ("eval(", "exec(", "__import__", "import subprocess",
                      "os.system("):
            assert token not in src, token
        assert set(fx.TRANSFORM_EXECUTORS) | set(
            fx.EXECUTION_UNSUPPORTED_TRANSFORMS) == {
            "lag", "difference", "percentage_change", "rolling_mean",
            "rolling_median", "rolling_std", "rolling_slope", "zscore",
            "cross_sectional_rank", "winsorize", "volatility_scale",
            "sector_neutralize", "sector_cap", "interaction", "ratio",
            "bounded_sum", "bounded_weighted_average"}
        # sector_cap is vocabulary-valid but execution-unsupported (structured)
        doctored = {"compiled": True, "steps": [{
            "feature_id": "f_cap", "source_family": "momentum",
            "output": "step_1",
            "steps": [{"step": "step_1", "transform": "sector_cap",
                       "params": {"cap": 0.25}, "inputs": ["source:mom_6_1"]}],
            "signature": "dsl:cap"}]}
        out = fx.execute_feature_set(doctored, panel, inventory)
        assert out["status"] == "REJECTED_UNSUPPORTED"

    def test_c09_missing_value_behavior_deterministic(
            self, panel, inventory, numeric_fields):
        # ratio guards zero denominators: difference(x,1)/x has months where
        # the value is missing rather than infinite, and the policy is recorded
        compiled = _compiled(
            [_feat("f_ratio", {"op": "ratio", "params": {}, "inputs": [
                {"op": "difference", "params": {"periods": 1},
                 "inputs": [_src("mom_6_1")]},
                _src("mom_6_1"),
            ]})], numeric_fields)
        o1 = fx.execute_feature_set(compiled, panel, inventory)
        o2 = fx.execute_feature_set(compiled, panel, inventory)
        assert o1["feature_content_hash"] == o2["feature_content_hash"]
        steps = o1["features"]["f_ratio"]["steps"]
        assert all("missing_policy" in s and s["missing_policy"] for s in steps)
        assert all("dropped_nonfinite" in s for s in steps)

    def test_c10_infinite_results_rejected_or_normalized(self):
        clean, dropped = fx._drop_nonfinite(
            {"2020-01": {"A": float("inf"), "B": float("nan"), "C": 1.5}})
        assert dropped == 2
        assert clean == {"2020-01": {"C": 1.5}}


# =========================================================================== #
# Point-in-time (11-20)
# =========================================================================== #
class TestPointInTime:
    def test_c11_target_fields_cannot_be_sources(self, panel, inventory,
                                                 numeric_fields):
        verdict = validate_feature_spec(
            _feat("f_t", {"op": "lag", "params": {"periods": 1},
                          "inputs": [_src("fwd_1m")]}),
            available_fields=numeric_fields + ["fwd_1m"])
        assert not verdict["accepted"]
        assert verdict["leakage_violations"]
        # and the executor independently refuses a doctored compiled source
        doctored = {"compiled": True, "steps": [{
            "feature_id": "f_t", "source_family": "momentum",
            "output": "step_1",
            "steps": [{"step": "step_1", "transform": "lag",
                       "params": {"periods": 1}, "inputs": ["source:fwd_1m"]}],
            "signature": "dsl:t"}]}
        out = fx.execute_feature_set(doctored, panel, inventory)
        assert not out["executed"]
        assert "fwd_1m" not in inventory["numeric_sources"]

    def test_c12_negative_lag_rejected(self, numeric_fields):
        verdict = validate_feature_spec(
            _feat("f_n", {"op": "lag", "params": {"periods": -1},
                          "inputs": [_src("mom_6_1")]}),
            available_fields=numeric_fields)
        assert not verdict["accepted"]
        assert verdict["leakage_violations"]

    def test_c13_centered_windows_rejected(self, numeric_fields):
        verdict = validate_feature_spec(
            _feat("f_c", {"op": "rolling_mean",
                          "params": {"window": 3, "centered": True},
                          "inputs": [_src("mom_6_1")]}),
            available_fields=numeric_fields)
        assert not verdict["accepted"]
        assert verdict["leakage_violations"]

    def test_c14_future_joins_rejected(self, numeric_fields):
        verdict = validate_feature_spec(
            _feat("f_f", {"op": "future_join", "params": {},
                          "inputs": [_src("mom_6_1")]}),
            available_fields=numeric_fields)
        assert not verdict["accepted"]
        assert verdict["leakage_violations"]

    def test_c15_target_joined_only_after_construction(
            self, fcamp, synth_inputs):
        store, fcid = fcamp["store"], fcamp["fcid"]
        hid = "hyp_29b_quality_momentum_interaction"
        audit = store.read_feature_artifact(fcid, hid, "pit_audit.json")
        assert "joined ONLY at evaluation" in audit["target_join_timing"]
        assert not (set(audit["source_fields_used"])
                    & {"fwd_1m", "forward_return"})
        diag = store.read_experiment_artifact(
            fcid, "fexp_%s_diag" % hid, "diagnostics.json")
        assert diag["months_evaluated"] > 0  # the join happened at evaluation

    def test_c16_no_backfill_from_future(self, fcamp):
        store, fcid = fcamp["store"], fcamp["fcid"]
        audit = store.read_feature_artifact(
            fcid, "hyp_29b_quality_momentum_interaction", "pit_audit.json")
        trunc = [c for c in audit["checks"]
                 if c["check"] == "truncation_invariance_no_future_dependence"]
        assert trunc and trunc[0]["passed"] is True
        assert trunc[0]["probe_months"]

    def test_c17_pit_audit_records_windows_and_lags(
            self, panel, inventory, numeric_fields, synth_inputs):
        compiled = _compiled(
            [_feat("f_w", {"op": "rolling_mean", "params": {"window": 3},
                           "inputs": [{"op": "lag", "params": {"periods": 1},
                                       "inputs": [_src("mom_6_1")]}]})],
            numeric_fields)
        execution = fx.execute_feature_set(compiled, panel, inventory)
        audit = fx.build_pit_audit(compiled, execution, panel, synth_inputs,
                                   inventory=inventory)
        win = audit["transformation_windows"]["f_w"]
        assert win["effective_lag_months"] == 3  # lag 1 + (window 3 - 1)
        assert len(win["transform_windows"]) == 2
        assert audit["max_formation_month"] == synth_inputs["months"][-1]
        assert audit["max_realized_return_date"] == fb._month_end(
            fb._next_month(synth_inputs["months"][-1]))

    def test_c18_pit_failure_blocks_the_experiment(
            self, pipeline, feature_cfg, synth_inputs, world, monkeypatch):
        made = fc.create_feature_campaign(
            config=feature_cfg, director_root=pipeline["root"],
            session_id=pipeline["session_id"],
            source_campaign_id=pipeline["campaign_id"],
            artifact_root=pipeline["root"], now_label="t29c_pitfail",
        )
        assert made["created"], made
        fcid = made["feature_campaign_id"]
        monkeypatch.setattr(fx, "build_pit_audit", lambda *a, **k: {
            "pit_ok": False, "violations": ["forced_violation"],
            "checks": [], "schema_version": "29C.1"})
        fctl = fc.FeatureCampaignController(
            fcid, artifact_root=pipeline["root"], inputs=synth_inputs,
            reference_rows=[], close_frame=world["close_frame"])
        result = fctl.run()
        assert result["status"] == ctl.RUN_OK  # all-blocked is a valid outcome
        hyps = fctl.store.hypotheses(fcid)
        exps = fctl.store.experiments(fcid)
        for hid in made["accepted_hypotheses"]:
            assert hyps[hid]["status"] == "REJECTED_PIT"
        assert all(r["status"].startswith("SKIPPED")
                   for r in exps.values())
        assert result["completed_total"] == 0

    def test_c19_data_cutoff_inherited_and_immutable(self, fcamp, feature_cfg,
                                                     pipeline):
        manifest = fcamp["store"].read_manifest(fcamp["fcid"])
        assert manifest["data_cutoff"] == CUTOFF
        assert "data_cutoff" not in (feature_cfg.get("data") or {})
        bad = copy.deepcopy(feature_cfg)
        bad["data"]["data_cutoff"] = "2030-01-01"
        verdict = fc.validate_feature_campaign_config(bad)
        assert not verdict["accepted"]
        for eid in ("fexp_hyp_29b_quality_momentum_interaction_diag",):
            cfg_doc = fcamp["store"].read_experiment_artifact(
                fcamp["fcid"], eid, "config.json")
            assert cfg_doc["data_cutoff"] == CUTOFF

    def test_c20_source_hashes_preserved(self, fcamp, synth_inputs):
        store, fcid = fcamp["store"], fcamp["fcid"]
        audit = store.read_feature_artifact(
            fcid, "hyp_29b_quality_momentum_interaction", "pit_audit.json")
        assert audit["source_hashes"] == synth_inputs["provenance"]["sha256"]
        prov = store.read_experiment_artifact(
            fcid, "fexp_hyp_29b_quality_momentum_interaction_diag",
            "provenance.json")
        assert prov["input_provenance"]["sha256"] == \
            synth_inputs["provenance"]["sha256"]


# =========================================================================== #
# Campaign (21-30)
# =========================================================================== #
class TestCampaign:
    def test_c21_director_session_must_be_complete(self, pipeline, feature_cfg,
                                                   tmp_path):
        out = fc.create_feature_campaign(
            config=feature_cfg, director_root=pipeline["root"],
            session_id="ds_0000000000000000",
            source_campaign_id=pipeline["campaign_id"],
            artifact_root=pipeline["root"])
        assert not out["created"]
        assert out["reason"] == "UNKNOWN_DIRECTOR_SESSION"
        other = dr.DirectorStore(str(tmp_path / "droot"))
        sid = "ds_notcomplete000"
        other.session_dir(sid).mkdir(parents=True, exist_ok=True)
        other.write_session_manifest(sid, {
            "session_id": sid, "session_state": "REQUESTED",
            "source_campaign": pipeline["campaign_id"]})
        out = fc.create_feature_campaign(
            config=feature_cfg, director_root=str(tmp_path / "droot"),
            session_id=sid, source_campaign_id=pipeline["campaign_id"],
            artifact_root=pipeline["root"])
        assert not out["created"]
        assert out["reason"] == "DIRECTOR_SESSION_NOT_COMPLETE"

    def test_c22_fixture_session_creates_research_only_campaign(self, fcamp):
        manifest = fcamp["store"].read_manifest(fcamp["fcid"])
        assert manifest["provider"] == "fixture"
        assert manifest["safety"]["research_only"] is True
        assert manifest["safety"]["creates_orders"] is False
        assert sorted(manifest["accepted_hypotheses"]) == sorted(
            fcamp["made"]["accepted_hypotheses"])
        assert manifest["source_campaign"].startswith("t29a_synth")
        assert manifest["evidence_pack_id"].startswith("ep_")

    def test_c23_unsupported_hypotheses_preserved_as_rejected(
            self, pipeline, director_cfg, feature_cfg, tmp_path_factory):
        base = dp.build_fixture_response(
            {"evidence_pack": {"available_features": {
                "momentum_sources": ["mom_6_1"],
                "fundamental_sources": ["composite_sn"]}}},
            n_proposals=2)
        bad = copy.deepcopy(base["proposals"][0])
        bad["hypothesis_id"] = "hyp_bad_transform"
        bad["proposed_feature"] = {"features": [_feat(
            "f_bad", {"op": "hyperbolic_projection", "params": {},
                      "inputs": [_src("mom_6_1")]})]}
        bad["duplicate_search_signature"] = "sig_bad_transform"
        base["proposals"].append(bad)
        base["decisions"] = []
        out_root = str(tmp_path_factory.mktemp("agent29c_dir2"))
        session = dr.run_director_session(
            artifact_root=pipeline["root"],
            campaign_id=pipeline["campaign_id"],
            director_config=director_cfg,
            provider=dp.FixtureDirectorProvider(fixture_response=base),
            output_root=out_root)
        assert session["counts"].get("REJECTED_UNSUPPORTED") == 1
        made = fc.create_feature_campaign(
            config=feature_cfg, director_root=out_root,
            session_id=session["session_id"],
            source_campaign_id=pipeline["campaign_id"],
            artifact_root=pipeline["root"], now_label="t29c_rej")
        assert made["created"]
        store = fc.FeatureCampaignStore(pipeline["root"])
        hyps = store.hypotheses(made["feature_campaign_id"])
        assert hyps["hyp_bad_transform"]["status"] == "REJECTED_AT_DIRECTOR"
        assert hyps["hyp_bad_transform"]["director_status"] == \
            "REJECTED_UNSUPPORTED"
        assert "hyp_bad_transform" in store.read_manifest(
            made["feature_campaign_id"])["rejected_hypotheses"]

    def test_c24_full_plan_persisted_before_execution(self, fcamp):
        before = fcamp["experiments_before_run"]
        assert len(before) == 6
        assert all(r["status"] == "PLANNED" for r in before.values())
        kinds = [r["kind"] for r in before.values()]
        assert kinds.count(fc.EXPERIMENT_KIND_DIAGNOSTIC) == 3
        assert kinds.count(fc.EXPERIMENT_KIND_INTEGRATION) == 3

    def test_c25_invocation_limit_pauses_not_completes(self, fcamp):
        run1 = fcamp["run1"]
        assert run1["status"] == ctl.RUN_PAUSED
        assert run1["pause_reason"] == ctl.PAUSE_INVOCATION_LIMIT
        assert run1["invocation"]["experiments_attempted"] == 2
        assert fcamp["experiments_after_run1"] and any(
            r["status"] == "PLANNED"
            for r in fcamp["experiments_after_run1"].values())

    def test_c26_resume_executes_only_pending(self, fcamp):
        completed_run1 = {
            eid for eid, r in fcamp["experiments_after_run1"].items()
            if r["status"] == "COMPLETE"}
        assert len(completed_run1) == 2
        rows = read_jsonl(fcamp["store"].campaign_dir(fcamp["fcid"])
                          / "experiment_index.jsonl")
        for eid in completed_run1:
            statuses = [r["status"] for r in rows
                        if r["experiment_id"] == eid]
            assert statuses == ["PLANNED", "RUNNING", "COMPLETE"], eid

    def test_c27_completed_experiments_not_duplicated(self, fcamp):
        attempted = sum(r["invocation"]["experiments_attempted"]
                        for r in fcamp["runs"])
        completed = fcamp["final"]["completed_total"]
        assert attempted == completed  # every execution happened exactly once
        again = fcamp["controller"].run()
        assert again["status"] == ctl.RUN_ALREADY_COMPLETE

    def test_c28_campaign_lock_prevents_concurrent_execution(
            self, pipeline, feature_cfg, synth_inputs, world):
        made = fc.create_feature_campaign(
            config=feature_cfg, director_root=pipeline["root"],
            session_id=pipeline["session_id"],
            source_campaign_id=pipeline["campaign_id"],
            artifact_root=pipeline["root"], now_label="t29c_lock")
        fcid = made["feature_campaign_id"]
        fctl = fc.FeatureCampaignController(
            fcid, artifact_root=pipeline["root"], inputs=synth_inputs,
            reference_rows=[], close_frame=world["close_frame"])
        lock = fctl.store.lock(fcid, owner="test-holder")
        lock.acquire()
        try:
            result = fctl.run()
            assert result["status"] == ctl.RUN_LOCKED
        finally:
            lock.release()

    def test_c29_ledgers_append_only(self, fcamp):
        store, fcid = fcamp["store"], fcamp["fcid"]
        events_now = _read_bytes(store.campaign_dir(fcid) / "events.jsonl")
        assert events_now.startswith(fcamp["events_after_run1"])
        invocations_now = _read_bytes(
            store.campaign_dir(fcid) / "invocations.jsonl")
        assert invocations_now.startswith(fcamp["invocations_after_run1"])
        assert store.verify_event_chain(fcid)["intact"]

    def test_c30_complete_requires_all_supported_work_terminal(
            self, fcamp, pipeline, feature_cfg, synth_inputs, world):
        assert fcamp["final"]["remaining_total"] == 0
        assert fcamp["final"].get("final_state") == "COMPLETE"
        # a campaign forced to REPORTING with pending work refuses COMPLETE
        made = fc.create_feature_campaign(
            config=feature_cfg, director_root=pipeline["root"],
            session_id=pipeline["session_id"],
            source_campaign_id=pipeline["campaign_id"],
            artifact_root=pipeline["root"], now_label="t29c_pending")
        fcid = made["feature_campaign_id"]
        store = fc.FeatureCampaignStore(pipeline["root"])
        sm = fc.FeatureStateMachine(store, fcid)
        for state in (fc.SOURCE_AUDIT, fc.DSL_VALIDATION, fc.FEATURE_BUILD,
                      fc.PIT_VALIDATION, fc.IC_SCREEN, fc.PORTFOLIO_SCREEN,
                      fc.PRIMARY_EVALUATION, fc.ROBUSTNESS_TESTING,
                      fc.DIRECTOR_FEEDBACK, fc.REPORTING):
            sm.transition(state, reason="test walk")
        fctl = fc.FeatureCampaignController(
            fcid, artifact_root=pipeline["root"], inputs=synth_inputs,
            reference_rows=[], close_frame=world["close_frame"])
        result = fctl.run()
        assert result["status"] == ctl.RUN_PAUSED
        assert result["pause_reason"] == ctl.PAUSE_REMAINING_WORK
        assert store.read_manifest(fcid)["current_state"] != fc.COMPLETE


# =========================================================================== #
# IC screen (31-36)
# =========================================================================== #
def _screen_diag(**over):
    diag = {
        "feature_id": "f_test",
        "months_evaluated": 45,
        "month_coverage": 0.9,
        "cross_sectional_coverage": 0.95,
        "rank_ic_mean": 0.05,
        "rank_ic_t": 2.5,
        "orientation": 1,
        "rank_ic_mean_ex_best_month": 0.04,
        "subperiod_ic_means": [0.05, 0.04, 0.06],
        "avg_top_rank_sector_share": 0.3,
        "corr_with_sources": {"mom_6_1": 0.2, "composite_sn": 0.1},
        "corr_with_baseline_score": 0.2,
    }
    diag.update(over)
    return diag


BASELINE_M = {"rank_ic_t": 0.8}


class TestICScreen:
    def test_c31_standalone_ic_by_deterministic_tools(
            self, mom_series, synth_inputs):
        d1 = fe.compute_feature_diagnostics(
            mom_series, synth_inputs, feature_id="mom_6_1")
        d2 = fe.compute_feature_diagnostics(
            mom_series, synth_inputs, feature_id="mom_6_1")
        assert content_hash(d1) == content_hash(d2)
        row = d1["monthly_rows"][0]
        m = row["month"]
        mrow = synth_inputs["mom_monthly"][m]
        uni = sorted(tk for tk, r in mrow.items()
                     if r.get("eligible") and r.get("fwd_1m") is not None
                     and tk in mom_series.get(m, {}))
        manual = fe.P._spearman([mom_series[m][tk] for tk in uni],
                                [mrow[tk]["fwd_1m"] for tk in uni])
        assert row["rank_ic"] == pytest.approx(float(manual))

    def test_c32_feature_identical_to_source_rejected(
            self, panel, inventory, numeric_fields, synth_inputs):
        compiled = _compiled(
            [_feat("f_id", {"op": "lag", "params": {"periods": 0},
                            "inputs": [_src("mom_6_1")]})], numeric_fields)
        out = fx.execute_feature_set(compiled, panel, inventory)
        series = out["features"]["f_id"]["series"]
        diag = fe.compute_feature_diagnostics(
            series, synth_inputs, feature_id="f_id")
        assert diag["corr_with_sources"]["mom_6_1"] == pytest.approx(1.0)
        screen = fe.run_ic_screen(
            diag, baseline_metrics=BASELINE_M,
            thresholds={"leakage_suspicion_abs_ic": 0.99})
        assert screen["outcome"] == "REJECTED_NO_INCREMENTAL_SIGNAL"
        assert any("identical" in r for r in screen["reasons"])

    def test_c33_insufficient_coverage_rejects(self):
        screen = fe.run_ic_screen(
            _screen_diag(months_evaluated=12),
            baseline_metrics=BASELINE_M)
        assert screen["outcome"] == "REJECTED_COVERAGE"
        screen = fe.run_ic_screen(
            _screen_diag(month_coverage=0.3),
            baseline_metrics=BASELINE_M)
        assert screen["outcome"] == "REJECTED_COVERAGE"

    def test_c34_single_period_dependency_rejects(self):
        screen = fe.run_ic_screen(
            _screen_diag(subperiod_ic_means=[0.15, -0.01, -0.02]),
            baseline_metrics=BASELINE_M)
        assert screen["outcome"] == "REJECTED_NO_INCREMENTAL_SIGNAL"
        assert any("subperiod" in r for r in screen["reasons"])

    def test_c35_material_ic_improvement_may_advance(self, fcamp):
        screen = fe.run_ic_screen(_screen_diag(),
                                  baseline_metrics=BASELINE_M)
        assert screen["outcome"] == "ADVANCE_TO_PORTFOLIO_SCREEN"
        persisted = fcamp["store"].read_experiment_artifact(
            fcamp["fcid"], "fexp_hyp_29b_quality_momentum_interaction_diag",
            "screen_result.json")
        assert persisted["outcome"] == "ADVANCE_TO_PORTFOLIO_SCREEN"

    def test_c36_positive_alone_does_not_advance(self):
        screen = fe.run_ic_screen(
            _screen_diag(rank_ic_t=0.6, corr_with_baseline_score=0.8),
            baseline_metrics={"rank_ic_t": 2.0})
        assert screen["outcome"] == "INCONCLUSIVE"
        assert any("not enough" in r for r in screen["reasons"])


# =========================================================================== #
# Portfolio (37-44)
# =========================================================================== #
class TestPortfolio:
    def test_c37_baseline_reproduced_before_candidate_evaluation(
            self, synth_inputs, fcamp):
        check = fe.verify_baseline_reproduction_via_integration(synth_inputs)
        assert check["reproduced"] is True
        assert check["baseline_periods_hash"] == \
            check["integrated_periods_hash"]
        manifest = fcamp["store"].read_manifest(fcamp["fcid"])
        assert manifest["baseline"]["baseline_reproduced"] is True
        assert manifest["baseline"]["integration_reproduced"] is True

    def test_c38_integration_weights_reconcile_to_one(
            self, synth_inputs, feature_cfg):
        with pytest.raises(fe.FeatureEvaluationError):
            fe.run_integrated_experiment(synth_inputs, {}, {
                "baseline_weight": 0.8, "feature_weight": 0.1})
        bad = copy.deepcopy(feature_cfg)
        bad["integration"]["feature_weight"] = 0.3  # 0.8 + 0.3 != 1
        verdict = fc.validate_feature_campaign_config(bad)
        assert not verdict["accepted"]

    def test_c39_llm_cannot_alter_integration_weights(
            self, fcamp, feature_cfg, numeric_fields):
        proposal = dp.build_fixture_response(
            {"evidence_pack": {"available_features": {
                "momentum_sources": ["mom_6_1"],
                "fundamental_sources": ["composite_sn"]}}},
            n_proposals=1)["proposals"][0]
        doctored = dict(proposal, integration_weight=0.9)
        verdict = dr.validate_proposal(
            doctored, available_fields=numeric_fields,
            max_feature_depth=3, max_interactions=4,
            max_primary_experiments=6)
        assert verdict["status"] == "REJECTED_SCHEMA"
        # executed params come only from the validated config
        metrics_doc = fcamp["store"].read_experiment_artifact(
            fcamp["fcid"], "fexp_hyp_29b_quality_momentum_interaction_integ",
            "metrics.json")
        assert metrics_doc["params"]["baseline_weight"] == \
            feature_cfg["integration"]["baseline_weight"]
        assert metrics_doc["params"]["feature_weight"] == \
            feature_cfg["integration"]["feature_weight"]

    def test_c40_costs_charged_once(self, synth_inputs, fcamp):
        store, fcid = fcamp["store"], fcamp["fcid"]
        eid = "fexp_hyp_29b_quality_momentum_interaction_integ"
        metrics = store.read_experiment_artifact(
            fcid, eid, "metrics.json")["metrics"]
        hid = "hyp_29b_quality_momentum_interaction"
        execution = store.read_feature_artifact(fcid, hid, "features.json")
        series = execution["features"][
            execution["terminal_feature_id"]]["series"]
        params = dict(store.read_experiment_artifact(
            fcid, eid, "metrics.json")["params"])
        cost = params.pop("primary_cost_bps_per_side")
        sim = fe.run_integrated_experiment(synth_inputs, series, params)
        rt = 2.0 * (cost / 1e4)  # per-side bps -> round trip, applied ONCE
        nets = [p["gross"] - rt * p["turnover"] for p in sim["periods"]]
        expected = (sum(nets) / len(nets)) * 12.0
        assert metrics["net_return_ann"] == pytest.approx(expected)

    def test_c41_baseline_relative_deltas_reconcile(self, fcamp):
        store, fcid = fcamp["store"], fcamp["fcid"]
        eid = "fexp_hyp_29b_quality_momentum_interaction_integ"
        deltas = store.read_experiment_artifact(
            fcid, eid, "baseline_deltas.json")
        metrics = store.read_experiment_artifact(
            fcid, eid, "metrics.json")["metrics"]
        baseline = store.read_manifest(fcid)["baseline"]["metrics"]
        row = deltas["metrics"]["net_spy_excess_ann"]
        assert row["candidate"] == pytest.approx(metrics["net_spy_excess_ann"])
        assert row["baseline"] == pytest.approx(baseline["net_spy_excess_ann"])
        assert row["delta_abs"] == pytest.approx(
            metrics["net_spy_excess_ann"] - baseline["net_spy_excess_ann"])

    def test_c42_sector_cap_convention_matches_baseline(
            self, synth_inputs, fcamp):
        params = fcamp["store"].read_experiment_artifact(
            fcamp["fcid"], "fexp_hyp_29b_quality_momentum_interaction_integ",
            "metrics.json")["params"]
        assert params["sector_treatment"] == \
            fb.BASELINE_PARAMS["sector_treatment"]
        assert params["top_n"] == fb.BASELINE_PARAMS["top_n"]
        hid = "hyp_29b_quality_momentum_interaction"
        execution = fcamp["store"].read_feature_artifact(
            fcamp["fcid"], hid, "features.json")
        series = execution["features"][
            execution["terminal_feature_id"]]["series"]
        p = dict(params)
        p.pop("primary_cost_bps_per_side")
        sim = fe.run_integrated_experiment(synth_inputs, series, p)
        max_per_sector = max(1, int(fb.SECTOR_CAP_FRACTION * p["top_n"]))
        for period in sim["periods"]:
            w = 1.0 / period["n"]
            for sec, sw in period["sector_weights"].items():
                if sec != "Unknown":
                    assert sw <= max_per_sector * w + 1e-9

    def test_c43_no_operational_exit_buffer_silently_applied(
            self, fcamp, feature_cfg):
        params = fcamp["store"].read_experiment_artifact(
            fcamp["fcid"], "fexp_hyp_29b_quality_momentum_interaction_integ",
            "metrics.json")["params"]
        assert params["exit_buffer_fraction"] == 0.0
        bad = copy.deepcopy(feature_cfg)
        bad["portfolio"]["exit_buffer_fraction"] = 0.2
        bad["integration"]["baseline_weight"] = 0.8  # keep rest valid
        verdict = fc.validate_feature_campaign_config(bad)
        assert not verdict["accepted"]
        assert any("exit_buffer" in v["field"] for v in verdict["violations"])

    def test_c44_portfolio_result_deterministic(self, synth_inputs, fcamp):
        hid = "hyp_29b_quality_momentum_interaction"
        execution = fcamp["store"].read_feature_artifact(
            fcamp["fcid"], hid, "features.json")
        series = execution["features"][
            execution["terminal_feature_id"]]["series"]
        params = {"baseline_weight": 0.8, "feature_weight": 0.2,
                  "feature_orientation": 1}
        s1 = fe.run_integrated_experiment(synth_inputs, series, params)
        s2 = fe.run_integrated_experiment(synth_inputs, series, params)
        assert content_hash(s1) == content_hash(s2)


# =========================================================================== #
# Robustness (45-50)
# =========================================================================== #
class TestRobustness:
    def test_c45_only_retained_candidates_enter_robustness(self, fcamp):
        manifest = fcamp["store"].read_manifest(fcamp["fcid"])
        retained = {
            eid for eid, row in fcamp["store"].experiments(
                fcamp["fcid"]).items()
            if (fcamp["store"].read_experiment_artifact(
                fcamp["fcid"], eid, "decision.json") or {}).get("decision")
            == ev.RETAIN_FOR_ROBUSTNESS}
        assert set(manifest.get("robustness_queue") or []) <= retained
        assert set(manifest.get("robustness_results") or {}) <= retained

    def test_c46_strict_shadow_gates_unchanged(self, feature_cfg):
        assert ev.DEFAULT_THRESHOLDS["min_rank_ic_t"]["value"] == 2.0
        assert ev.DEFAULT_THRESHOLDS["max_turnover_monthly_oneside"][
            "value"] == 0.35
        assert ev.DEFAULT_THRESHOLDS["max_sector_weight"]["value"] == 0.25
        assert ev.DEFAULT_THRESHOLDS["min_net_excess_at_50bps"]["value"] == 0.0
        assert "thresholds" not in feature_cfg  # gates not configurable at all

    def test_c47_rank_ic_gate_cannot_be_lowered(self, feature_cfg):
        bad = copy.deepcopy(feature_cfg)
        bad["thresholds"] = {"min_rank_ic_t": 0.5}
        verdict = fc.validate_feature_campaign_config(bad)
        assert not verdict["accepted"]
        assert any(v["field"] == "thresholds" for v in verdict["violations"])
        bad2 = copy.deepcopy(feature_cfg)
        bad2["ic_screen"]["min_abs_rank_ic_t"] = 0.2
        verdict2 = fc.validate_feature_campaign_config(bad2)
        assert not verdict2["accepted"]

    def test_c48_turnover_gate_cannot_be_lowered(self, feature_cfg):
        bad = copy.deepcopy(feature_cfg)
        bad["gates"] = {"max_turnover_monthly_oneside": 0.9}
        verdict = fc.validate_feature_campaign_config(bad)
        assert not verdict["accepted"]
        assert any(v["field"] == "gates" for v in verdict["violations"])

    def test_c49_zero_survivor_outcome_valid(self, fcamp):
        assert fcamp["final"].get("final_state") == "COMPLETE"
        assert fcamp["report"]["shadow_eligible"] == []
        assert fcamp["report"]["robustness_results"] == {}

    def test_c50_no_candidate_becomes_shadow_active_or_operational(
            self, synth_inputs, fcamp):
        hid = "hyp_29b_quality_momentum_interaction"
        execution = fcamp["store"].read_feature_artifact(
            fcamp["fcid"], hid, "features.json")
        series = execution["features"][
            execution["terminal_feature_id"]]["series"]
        baseline = fcamp["store"].read_manifest(
            fcamp["fcid"])["baseline"]["metrics"]
        diag = fcamp["store"].read_experiment_artifact(
            fcamp["fcid"], "fexp_%s_diag" % hid, "diagnostics.json")
        rb = fe.run_feature_robustness(
            synth_inputs, series,
            {"baseline_weight": 0.8, "feature_weight": 0.2,
             "feature_orientation": int(diag["orientation"])},
            primary_cost_bps_per_side=25.0,
            baseline_metrics=baseline, diagnostics=diag)
        assert rb["decision"]["decision"] in ev.DECISIONS
        assert rb["decision"]["decision"] not in (
            "SHADOW_ACTIVE", "OPERATIONAL", "PROMOTION_CANDIDATE")
        with pytest.raises(dr.DirectorSafetyError):
            fc.register_challenger()


# =========================================================================== #
# Feedback (51-58)
# =========================================================================== #
class TestFeedback:
    def test_c51_packet_references_persisted_evidence(self, fcamp):
        packet = dfk.build_feedback_packet(
            fcamp["store"], fcamp["fcid"],
            "hyp_29b_quality_momentum_interaction")
        assert packet["artifact_references"]
        assert packet["budget_consumed"]["experiments_executed"] == 2
        for path in packet["artifact_references"][:2]:
            assert os.path.isdir(path) or os.path.isfile(path) or \
                path.endswith(".json")
        assert packet["standalone_feature_diagnostics"]["rank_ic_t"] is not None
        assert packet["portfolio_metrics"] is not None

    def test_c52_packet_contains_falsification_result(self, fcamp):
        for hid in fcamp["made"]["accepted_hypotheses"]:
            packet = dfk.build_feedback_packet(fcamp["store"], fcamp["fcid"],
                                               hid)
            fr = packet["falsification_result"]
            assert fr["outcome"] in ("FALSIFIED", "NOT_FALSIFIED",
                                     "UNEVALUATED")
            assert fr["condition"]

    def test_c53_fixture_feedback_deterministic(self, feedback_flow):
        request = feedback_flow["request"]
        r1 = dfk.build_fixture_feedback_response(request)
        r2 = dfk.build_fixture_feedback_response(request)
        assert content_hash(r1) == content_hash(r2)
        assert feedback_flow["first"]["status"] == "FEEDBACK_CYCLE_COMPLETE"
        assert all(d["accepted"] for d in feedback_flow["first"]["decisions"])

    def test_c54_one_feedback_cycle_maximum(self, feedback_flow, fcamp):
        assert feedback_flow["second"]["status"] == \
            "FEEDBACK_CYCLE_LIMIT_REACHED"
        assert feedback_flow["second"]["idempotent"] is True
        third = dfk.run_feedback_cycle(fcamp["store"], fcamp["fcid"],
                                       provider_name="fixture")
        assert third["status"] == "FEEDBACK_CYCLE_LIMIT_REACHED"

    def test_c55_revision_within_dsl_and_budget(self, numeric_fields):
        good_features = [_feat("f_rev", {
            "op": "cross_sectional_rank", "params": {},
            "inputs": [{"op": "difference", "params": {"periods": 6},
                        "inputs": [_src("adv_dollar")]}]})]
        base = {
            "decision": "REVISE",
            "hypothesis_id": "hyp_x",
            "evidence_refs": ["fb_1"],
            "reasoning_summary": "r",
            "uncertainties": [],
            "falsification_condition": "f",
            "next_deterministic_action": "n",
            "budget_impact": {"additional_experiments": 1},
            "safety_confirmation": {
                "research_only": True, "no_operational_action": True,
                "no_gate_change": True, "no_budget_increase": True},
            "revised_feature": {"features": good_features},
        }
        ok = dfk.validate_feedback_decision(
            base, numeric_fields=numeric_fields, exhausted_signatures=[],
            original_signatures={}, max_feature_depth=3, max_interactions=4,
            revisions_remaining=2, hypotheses_already_revised=[])
        assert ok["accepted"] and ok["revision"]["signature"]
        no_budget = dfk.validate_feedback_decision(
            base, numeric_fields=numeric_fields, exhausted_signatures=[],
            original_signatures={}, max_feature_depth=3, max_interactions=4,
            revisions_remaining=0, hypotheses_already_revised=[])
        assert not no_budget["accepted"]
        bad_dsl = copy.deepcopy(base)
        bad_dsl["revised_feature"]["features"][0]["expression"] = {
            "op": "future_join", "params": {}, "inputs": [_src("mom_6_1")]}
        rej = dfk.validate_feedback_decision(
            bad_dsl, numeric_fields=numeric_fields, exhausted_signatures=[],
            original_signatures={}, max_feature_depth=3, max_interactions=4,
            revisions_remaining=2, hypotheses_already_revised=[])
        assert not rej["accepted"]

    def test_c56_exhausted_signatures_rejected(self, numeric_fields):
        features = [_feat("f_dup", {
            "op": "cross_sectional_rank", "params": {},
            "inputs": [_src("mom_6_1")]})]
        sig = feature_set_signature(features)
        dec = {
            "decision": "REVISE", "hypothesis_id": "hyp_x",
            "evidence_refs": ["fb_1"], "reasoning_summary": "r",
            "uncertainties": [], "falsification_condition": "f",
            "next_deterministic_action": "n",
            "budget_impact": {}, "safety_confirmation": {
                "research_only": True, "no_operational_action": True,
                "no_gate_change": True, "no_budget_increase": True},
            "revised_feature": {"features": features},
        }
        rej = dfk.validate_feedback_decision(
            dec, numeric_fields=numeric_fields,
            exhausted_signatures=[sig],
            original_signatures={}, max_feature_depth=3, max_interactions=4,
            revisions_remaining=2, hypotheses_already_revised=[])
        assert not rej["accepted"]
        assert any("exhausted" in v["issue"] for v in rej["violations"])

    def test_c57_director_cannot_request_operational_actions(
            self, numeric_fields):
        for forbidden in ("CREATE_ORDER", "SHADOW_ACTIVE", "OPERATIONAL",
                          "CHANGE_ACTIVE_MODEL", "LOWER_GATE"):
            dec = {"decision": forbidden, "hypothesis_id": "hyp_x",
                   "evidence_refs": [], "reasoning_summary": "r",
                   "uncertainties": [], "falsification_condition": "f",
                   "next_deterministic_action": "n", "budget_impact": {},
                   "safety_confirmation": {
                       "research_only": True, "no_operational_action": True,
                       "no_gate_change": True, "no_budget_increase": True}}
            rej = dfk.validate_feedback_decision(
                dec, numeric_fields=numeric_fields, exhausted_signatures=[],
                original_signatures={}, max_feature_depth=3,
                max_interactions=4, revisions_remaining=2,
                hypotheses_already_revised=[])
            assert not rej["accepted"], forbidden
            assert any(v["severity"] == "SAFETY"
                       for v in rej["violations"]), forbidden

    def test_c58_director_cannot_increase_budgets(self):
        violations = dfk.validate_feedback_response({
            "feedback_decisions": [],
            "budgets": {"max_primary_experiments": 999}})
        assert any(v["severity"] == "SAFETY" and v["field"] == "budgets"
                   for v in violations)
        violations = dfk.validate_feedback_response({
            "feedback_decisions": [],
            "gate_overrides": {"rank_ic": "off"}})
        assert any(v["severity"] == "SAFETY" for v in violations)


# =========================================================================== #
# Safety (59-65)
# =========================================================================== #
NEW_MODULES = ("feature_execution.py", "feature_evaluation.py",
               "feature_campaign.py", "director_feedback.py")


class TestSafety:
    def test_c59_no_paper_trader_write(self, guard_snapshots, fcamp,
                                       feedback_flow):
        assert _snapshot_dir(DESK_DIR) == guard_snapshots["desk"]
        import research_agent
        pkg_dir = os.path.dirname(research_agent.__file__)
        for fname in NEW_MODULES:
            src = open(os.path.join(pkg_dir, fname), "r",
                       encoding="utf-8").read()
            assert "paper_trader" not in src, fname

    def test_c60_no_orders_created(self, fcamp):
        report = fcamp["report"]
        assert report["safety"]["creates_orders"] is False
        status = fcamp["store"].read_status(fcamp["fcid"])
        assert status["safety"]["creates_orders"] is False

    def test_c61_no_broker_execution(self, fcamp):
        assert fcamp["report"]["safety"]["broker_execution"] is False
        import research_agent
        pkg_dir = os.path.dirname(research_agent.__file__)
        for fname in NEW_MODULES:
            src = open(os.path.join(pkg_dir, fname), "r",
                       encoding="utf-8").read()
            for token in ("ib_insync", "alpaca", "broker_api"):
                assert token not in src, (fname, token)

    def test_c62_no_trading_automation(self, fcamp):
        assert fcamp["report"]["safety"]["automation_of_trading"] is False
        manifest = fcamp["store"].read_manifest(fcamp["fcid"])
        assert manifest["safety"]["automation_of_trading"] is False

    def test_c63_operational_model_unchanged(self, fcamp, guard_snapshots):
        assert fcamp["report"]["safety"]["operational_model_changed"] is False
        assert _snapshot_dir(DESK_DIR) == guard_snapshots["desk"]

    def test_c64_holdings_and_cash_unchanged(self, guard_snapshots,
                                             feedback_flow):
        # the desk ledgers (holdings, cash, marks) are byte-identical
        assert _snapshot_dir(DESK_DIR) == guard_snapshots["desk"]

    def test_c65_fixture_evidence_cannot_activate_challenger(self, fcamp):
        with pytest.raises(dr.DirectorSafetyError):
            fc.register_challenger()
        with pytest.raises(dr.DirectorSafetyError):
            dr.register_challenger()
        cdir = fcamp["store"].campaign_dir(fcamp["fcid"])
        for base, _dirs, files in os.walk(cdir):
            for f in files:
                assert "challenger" not in f.lower(), os.path.join(base, f)
                assert "registry" not in f.lower(), os.path.join(base, f)
        assert fcamp["report"]["challengers_registered"] == []
        assert fcamp["store"].read_manifest(
            fcamp["fcid"])["provider"] == "fixture"


# =========================================================================== #
# CLI (66-74)
# =========================================================================== #
class TestCLI:
    def test_c66_feature_validate_exit_codes(self, tmp_path):
        code, payload, _ = _cli(["feature-validate", "--config",
                                 FEATURE_CONFIG_PATH, "--json"])
        assert code == 0 and payload["accepted"] is True
        bad = tmp_path / "bad_cfg.json"
        with open(FEATURE_CONFIG_PATH, "r", encoding="utf-8") as fh:
            cfg = json.load(fh)
        cfg["budgets"]["max_primary_experiments"] = 999
        bad.write_text(json.dumps(cfg), encoding="utf-8")
        code, payload, _ = _cli(["feature-validate", "--config", str(bad),
                                 "--json"])
        assert code == 2 and payload["accepted"] is False

    def test_c67_feature_create_returns_campaign_id(self, cli_flow):
        code, payload, _ = cli_flow["create"]
        assert code == 0
        assert payload["created"] is True
        assert payload["feature_campaign_id"].startswith(
            "phase29c_feature_campaign_")

    def test_c68_feature_plan_reconciled_totals(self, cli_flow):
        code, payload, _ = cli_flow["plan"]
        assert code == 0
        assert payload["totals"]["reconciled"] is True
        assert payload["totals"]["planned_total"] == 6

    def test_c69_feature_run_respects_invocation_limits(self, cli_flow):
        code, payload, _ = cli_flow["run_limited"]
        assert code == 0
        assert payload["status"] == ctl.RUN_PAUSED
        assert payload["pause_reason"] == ctl.PAUSE_INVOCATION_LIMIT
        assert payload["invocation"]["experiments_attempted"] == 1

    def test_c70_feature_resume_idempotent(self, cli_flow):
        code, payload = cli_flow["resume_final"]
        assert code == 0 and payload["status"] == ctl.RUN_OK
        code, payload, _ = cli_flow["resume_after_complete"]
        assert code == 0
        assert payload["status"] == ctl.RUN_ALREADY_COMPLETE
        assert cli_flow["events_unchanged_after_terminal_resume"] is True

    def test_c71_feature_status_read_only(self, cli_flow):
        code, payload, _ = cli_flow["status"]
        assert code == 0
        assert payload["current_state"] == "COMPLETE"
        assert cli_flow["status_dir_unchanged"] is True

    def test_c72_feature_report_safety_and_science(self, cli_flow):
        code, payload, _ = cli_flow["report"]
        assert code == 0
        report = payload["report"]
        assert report["safety"]["research_only"] is True
        assert report["baseline"]["metrics"]["rank_ic_t"] is not None
        assert report["experiments"]
        assert "hypothesis_status_counts" in report

    def test_c73_director_feedback_structured_output(self, cli_flow):
        code, payload, _ = cli_flow["feedback"]
        assert code == 0
        assert payload["status"] in ("FEEDBACK_CYCLE_COMPLETE",
                                     "FEEDBACK_CYCLE_LIMIT_REACHED")
        assert payload["safety"]["research_only"] is True

    def test_c74_invalid_ids_fail_clearly(self, pipeline, capsys):
        code = cli.main(["feature-status", "--feature-campaign-id",
                         "no_such_campaign", "--artifact-root",
                         pipeline["root"], "--json"])
        assert code == cli.EXIT_UNKNOWN_CAMPAIGN
        assert "unknown feature campaign" in capsys.readouterr().err
        code = cli.main(["feature-create",
                         "--director-session-id", "ds_missing00000000",
                         "--director-root", pipeline["root"],
                         "--source-campaign-id", pipeline["campaign_id"],
                         "--artifact-root", pipeline["root"],
                         "--config", FEATURE_CONFIG_PATH, "--json"])
        assert code == cli.EXIT_UNKNOWN_CAMPAIGN
