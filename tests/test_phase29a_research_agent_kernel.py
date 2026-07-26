"""Phase 29A — autonomous research agent kernel tests.

Fully offline: a deterministic synthetic world (consistent closes ->
momentum -> forward returns) is injected through the harness seams; no test
reads D:\\ data except one skipif-guarded real-data integration test.
"""

import datetime as dt
import json
import os
import random

import pytest

import research_agent as ra
from research_agent import artifact_store as ast
from research_agent import challenger_registry as chal
from research_agent import cli
from research_agent import controller as ctl
from research_agent import evaluator as ev
from research_agent import family_backtest as fb
from research_agent import memory as mem
from research_agent import planner as pln
from research_agent import reporting as rep
from research_agent import schemas as sch
from research_agent import state_machine as smod
from research_agent import tool_adapters as ta
from research_agent import tool_registry as tr

TODAY = dt.date(2026, 7, 24)
CUTOFF = "2021-12-31"

SECTORS = ["Tech", "Health", "Fin", "Energy", "Cons", "Ind"]
N_TICKERS = 60


def _tickers():
    return ["T%02d" % i for i in range(N_TICKERS)]


def _sector_of(i):
    return SECTORS[i % len(SECTORS)] if i < 54 else "Unknown"


def _months(start, end):
    out, cur = [], start
    while cur <= end:
        out.append(cur)
        y, m = int(cur[:4]), int(cur[5:7])
        cur = "%04d-%02d" % (y + (m == 12), 1 if m == 12 else m + 1)
    return out


def _build_world():
    """Closes with a real momentum effect; mom/fwd derived from the SAME closes."""
    rng = random.Random(2029)
    months = _months("2017-01", "2022-01")
    tickers = _tickers()
    closes = {tk: [50.0 + i] for i, tk in enumerate(tickers)}
    for t in range(1, len(months)):
        # momentum signal available from t >= 7
        if t >= 7:
            moms = {}
            for tk in tickers:
                c = closes[tk]
                moms[tk] = c[t - 1] / c[t - 7] - 1.0
            order = sorted(moms, key=lambda k: (-moms[k], k))
            n = len(order)
            pct = {tk: (n - 1 - j) / (n - 1) for j, tk in enumerate(order)}
        else:
            pct = {tk: 0.5 for tk in tickers}
        for tk in tickers:
            drift = 0.002 + 0.05 * (pct[tk] - 0.5)  # momentum genuinely predicts
            shock = rng.gauss(0.0, 0.02)
            closes[tk].append(closes[tk][-1] * max(0.2, 1.0 + drift + shock))

    idx = {m: i for i, m in enumerate(months)}
    mom_monthly = {}
    for m in _months("2017-08", "2021-12"):
        t = idx[m]
        row = {}
        for i, tk in enumerate(tickers):
            c = closes[tk]
            adv = 5.0e7 if i < 40 else (1.5e7 if i < 56 else 0.5e7)
            row[tk] = {
                "ticker": tk,
                "mom_6_1": c[t - 1] / c[t - 7] - 1.0,
                "fwd_1m": c[t + 1] / c[t] - 1.0 if t + 1 < len(months) else None,
                "sector": _sector_of(i),
                "eligible": True,
                "is_member": True,
                "adv_dollar": adv,
            }
        mom_monthly[m] = row

    frng = random.Random(929)
    fund_monthly = {}
    for m in _months("2018-01", "2021-10"):
        if int(m[5:7]) % 3 != 1:  # quarterly: Jan/Apr/Jul/Oct
            continue
        fund_monthly[m] = {
            tk: {"composite_sn": frng.gauss(0.0, 1.0), "sector": _sector_of(i)}
            for i, tk in enumerate(_tickers())
        }

    srng = random.Random(555)
    spy_close = {}
    level = 100.0
    for m in _months("2016-01", "2022-02"):
        drop = -0.12 if m in ("2020-02", "2020-03") else 0.0
        level *= 1.0 + 0.004 + drop + srng.gauss(0.0, 0.01)
        spy_close[m] = level

    import pandas as pd

    frame = pd.DataFrame(
        {"Date": [m + "-28" for m in months],
         **{tk: closes[tk] for tk in tickers}}
    )
    return {
        "mom_monthly": mom_monthly,
        "fund_monthly": fund_monthly,
        "spy_close": spy_close,
        "close_frame": frame,
        "months": months,
    }


@pytest.fixture(scope="module")
def world():
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


def make_config(name="t29a_synth", dims=None, budgets=None):
    return {
        "schema_version": "29A.1",
        "name": name,
        "objective": "synthetic kernel validation campaign",
        "baseline": {
            "model_id": sch.BASELINE_MODEL_ID,
            "book_id": sch.BASELINE_BOOK_ID,
            "cost_bps_per_side": 25.0,
        },
        "research_dimensions": dims
        or {
            "blend_weights": [[0.3, 0.7], [0.5, 0.5]],
            "portfolio_sizes": [25],
            "sector_treatments": ["sector_cap", "raw"],
            "exit_buffer_fractions": [0.0, 0.2],
            "rebalance_treatments": ["monthly", "eligible_sessions_20"],
            "cost_bps_per_side": [12.5, 25.0, 50.0],
            "defensive_overlays": ["off", "current_validated"],
            "min_adv_dollars": [10000000.0, 25000000.0],
        },
        "data": {
            "data_root": "",
            "data_cutoff": CUTOFF,
            "reference_book_returns": r"Z:\nonexistent\ref.csv",
        },
        "budgets": budgets
        or {
            "max_primary_experiments": 60,
            "max_robustness_candidates": 8,
            "max_registered_challengers": 3,
            "max_retry_per_experiment": 1,
            "experiment_timeout_seconds": 900,
        },
        "stop_conditions": {
            "fail_fast_on_pit_failure": True,
            "fail_fast_on_baseline_nonreproducibility": True,
            "allow_pause_resume": True,
            "no_operational_promotion": True,
        },
        "thresholds": {},
        "artifact_root": r"D:\Stock_Prediction_app_data\research_agent",
        "random_seed": 29,
    }


def make_spec(**over):
    spec = {
        "experiment_id": "exp_f30m70_top25_cap_b00",
        "candidate_id": "cand_f30m70_top25_cap_b00",
        "baseline_model": sch.BASELINE_MODEL_ID,
        "candidate_family": sch.CANDIDATE_FAMILY,
        "model_params": {"fundamental_weight": 0.3, "momentum_weight": 0.7},
        "portfolio_params": {
            "top_n": 25,
            "sector_treatment": "sector_cap",
            "rebalance": "monthly",
            "exit_buffer_fraction": 0.0,
            "defensive_overlay": "off",
            "min_adv_dollar": 10000000.0,
        },
        "data_cutoff": CUTOFF,
        "universe": "mhz_reconstruction",
        "evaluation_horizons": ["1m"],
        "cost_bps_per_side": 25.0,
        "robustness_tests": list(sch.APPROVED_ROBUSTNESS_TESTS),
        "compute_estimate": {"est_seconds": 1.0},
        "random_seed": 29,
        "stop_conditions": {"max_runtime_seconds": 900},
    }
    for k, v in over.items():
        if k in ("model_params", "portfolio_params"):
            spec[k] = dict(spec[k], **v)
        else:
            spec[k] = v
    return spec


def _new_campaign(root, world, synth_inputs, config=None):
    created = ctl.create_campaign(config or make_config(), artifact_root=str(root), today=TODAY)
    assert created["created"], created
    return ctl.CampaignController(
        created["campaign_id"],
        artifact_root=str(root),
        today=TODAY,
        inputs=synth_inputs,
        reference_rows=[],
        close_frame=world["close_frame"],
    )


def _snapshot_dir(path):
    if not os.path.isdir(path):
        return None
    out = {}
    for base, _dirs, files in os.walk(path):
        for f in files:
            p = os.path.join(base, f)
            st = os.stat(p)
            out[p] = (st.st_size, st.st_mtime_ns)
    return out


DESK_DIR = os.path.join(os.path.expanduser("~"), ".paper_trader", "paper_trading_desk")
MHZ_INPUTS_DIR = r"D:\Stock_Prediction_app_data\phase25_multi_horizon_alpha"


@pytest.fixture(scope="module")
def completed_campaign(tmp_path_factory, world, synth_inputs):
    root = tmp_path_factory.mktemp("agent29a_complete")
    desk_before = _snapshot_dir(DESK_DIR)
    mhz_before = _snapshot_dir(MHZ_INPUTS_DIR)
    controller = _new_campaign(root, world, synth_inputs)
    result = controller.run()
    return {
        "root": root,
        "controller": controller,
        "result": result,
        "desk_before": desk_before,
        "desk_after": _snapshot_dir(DESK_DIR),
        "mhz_before": mhz_before,
        "mhz_after": _snapshot_dir(MHZ_INPUTS_DIR),
    }


# =========================================================================== #
# Schemas (P1-P6)
# =========================================================================== #
class TestSchemas:
    def test_p1_valid_campaign_config_accepted(self):
        verdict = sch.validate_campaign_config(make_config(), today=TODAY)
        assert verdict["accepted"], verdict["violations"]
        assert verdict["config_hash"]

    def test_p1b_shipped_first_campaign_config_accepted(self):
        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "configs", "research_agent", "phase29a_first_campaign.json",
        )
        with open(path, "r", encoding="utf-8") as fh:
            cfg = json.load(fh)
        verdict = sch.validate_campaign_config(cfg, today=TODAY)
        assert verdict["accepted"], verdict["violations"]

    def test_p1c_baseline_semantics_reconciliation(self):
        # The scientific baseline is the RAW monthly target reconstruction:
        # buffer 0.0 is the only configuration that replays the owned
        # reference historical_book_returns.csv exactly (120/120 months).
        # The live engine's 0.20 EXIT_BUFFER_FRACTION is an operational
        # holdings/action-layer policy, kept as a separate labeled sensitivity.
        assert fb.BASELINE_PARAMS["exit_buffer_fraction"] == 0.0
        assert pln.BASELINE_CELL["exit_buffer_fraction"] == 0.0
        assert pln.BASELINE_CELL["blend"] == (
            fb.BASELINE_PARAMS["fundamental_weight"],
            fb.BASELINE_PARAMS["momentum_weight"],
        )
        assert pln.BASELINE_CELL["top_n"] == fb.BASELINE_PARAMS["top_n"]
        assert pln.BASELINE_CELL["sector_treatment"] == fb.BASELINE_PARAMS["sector_treatment"]
        # the operational 0.20 stays testable, but only as a non-baseline cell
        assert any(abs(b - 0.20) < 1e-12 for b in sch.APPROVED_EXIT_BUFFER_FRACTIONS)
        # cost conventions: reference replay at the file's own 12.5 bps/side
        # (the desk execution rate); campaign evaluation at the conservative
        # 25 bps/side Phase 10-C standard, declared in the shipped config
        assert fb.BASELINE_REFERENCE_COST_BPS_PER_SIDE == 12.5
        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "configs", "research_agent", "phase29a_first_campaign.json",
        )
        with open(path, "r", encoding="utf-8") as fh:
            base = json.load(fh)["baseline"]
        assert base["exit_buffer_fraction"] == 0.0
        assert base["cost_bps_per_side"] == 25.0

    def test_p2_invalid_blend_weight_rejected(self):
        spec = make_spec(model_params={"fundamental_weight": 0.45, "momentum_weight": 0.55})
        verdict = sch.validate_experiment_spec(spec, today=TODAY)
        assert not verdict["accepted"]
        assert any(v["field"] == "model_params" for v in verdict["violations"])

    def test_p3_unsupported_sector_treatment_rejected(self):
        spec = make_spec(portfolio_params={"sector_treatment": "industry_neutral"})
        verdict = sch.validate_experiment_spec(spec, today=TODAY)
        assert not verdict["accepted"]
        spec2 = make_spec(portfolio_params={"rebalance": "eligible_sessions_20"})
        verdict2 = sch.validate_experiment_spec(spec2, today=TODAY)
        assert not verdict2["accepted"]
        assert verdict2["unsupported"], "20-session rebalance must be a structured UNSUPPORTED rejection"

    def test_p4_unknown_tool_rejected(self):
        reg = ta.build_registry()
        env = reg.run("run_arbitrary_python", {})
        assert env["status"] == tr.STATUS_REJECTED_INVALID
        assert env["failure"]["type"] == "UnknownTool"

    def test_p5_arbitrary_command_code_fields_rejected(self):
        spec = make_spec()
        spec["shell_command"] = "format C:"
        verdict = sch.validate_experiment_spec(spec, today=TODAY)
        assert not verdict["accepted"]
        cfg = make_config()
        cfg["data"]["exec"] = "import os"
        assert not sch.validate_campaign_config(cfg, today=TODAY)["accepted"]
        assert sch.find_forbidden_execution_keys({"nested": {"run_script": 1}})

    def test_p5b_benign_words_not_flagged(self):
        assert not sch.find_forbidden_execution_keys({"description": "x", "code_commit": "abc"})

    def test_p6_future_data_cutoff_rejected(self):
        spec = make_spec(data_cutoff="2099-01-01")
        verdict = sch.validate_experiment_spec(spec, today=TODAY)
        assert any("future" in v["issue"] for v in verdict["violations"])
        cfg = make_config()
        cfg["data"]["data_cutoff"] = "2099-01-01"
        assert not sch.validate_campaign_config(cfg, today=TODAY)["accepted"]


# =========================================================================== #
# State machine (P7-P12)
# =========================================================================== #
class TestStateMachine:
    def _fresh(self, tmp_path):
        created = ctl.create_campaign(make_config(), artifact_root=str(tmp_path), today=TODAY)
        store = ast.ArtifactStore(str(tmp_path))
        return store, smod.CampaignStateMachine(store, created["campaign_id"])

    def test_p7_valid_transitions_work(self, tmp_path):
        _store, sm = self._fresh(tmp_path)
        out = sm.transition(smod.DATA_AUDIT, reason="t")
        assert out["applied"] and out["from_state"] == smod.NEW_CAMPAIGN
        sm.transition(smod.BASELINE_VALIDATION)
        assert sm.current_state() == smod.BASELINE_VALIDATION

    def test_p8_invalid_transitions_fail(self, tmp_path):
        _store, sm = self._fresh(tmp_path)
        with pytest.raises(smod.InvalidTransitionError):
            sm.transition(smod.REPORTING)
        with pytest.raises(smod.InvalidTransitionError):
            sm.transition("NOT_A_STATE")
        assert sm.current_state() == smod.NEW_CAMPAIGN  # nothing persisted

    def test_p9_transitions_persist(self, tmp_path):
        store, sm = self._fresh(tmp_path)
        sm.transition(smod.DATA_AUDIT, reason="persisted?")
        rows = [r for r in store.read_events(sm.campaign_id)
                if r["kind"] == smod.EVENT_STATE_TRANSITION]
        assert rows and rows[-1]["payload"]["to_state"] == smod.DATA_AUDIT
        assert rows[-1]["ts"]

    def test_p10_resume_restores_state(self, tmp_path):
        store, sm = self._fresh(tmp_path)
        sm.transition(smod.DATA_AUDIT)
        sm.transition(smod.PAUSED, reason="operator")
        sm2 = smod.CampaignStateMachine(store, sm.campaign_id)
        assert sm2.current_state() == smod.PAUSED
        assert sm2.resume_state() == smod.DATA_AUDIT
        sm2.transition(smod.DATA_AUDIT, reason="resume")
        assert sm2.current_state() == smod.DATA_AUDIT

    def test_p10b_idempotent_replay_is_noop(self, tmp_path):
        store, sm = self._fresh(tmp_path)
        sm.transition(smod.DATA_AUDIT)
        before = len(store.read_events(sm.campaign_id))
        out = sm.transition(smod.DATA_AUDIT)
        assert out["idempotent_noop"] and not out["applied"]
        assert len(store.read_events(sm.campaign_id)) == before

    def test_p11_complete_campaign_not_rerun(self, completed_campaign):
        assert completed_campaign["result"]["final_state"] == smod.COMPLETE
        again = completed_campaign["controller"].run()
        assert again["status"] == ctl.RUN_ALREADY_COMPLETE

    def test_p12_failed_campaign_preserves_evidence(self, tmp_path, world, synth_inputs):
        import pandas as pd

        flat = pd.DataFrame(
            {"Date": [m + "-28" for m in world["months"]],
             **{tk: [100.0] * len(world["months"]) for tk in _tickers()}}
        )
        controller = ctl.CampaignController(
            ctl.create_campaign(make_config(), artifact_root=str(tmp_path), today=TODAY)["campaign_id"],
            artifact_root=str(tmp_path), today=TODAY,
            inputs=synth_inputs, reference_rows=[], close_frame=flat,
        )
        result = controller.run()
        assert result["status"] == ctl.RUN_FAILED
        assert controller.sm.current_state() == smod.FAILED
        manifest = controller.manifest
        assert manifest["data_audit"]["pit"]["pit_integrity_ok"] is False
        events = controller.store.read_events(controller.campaign_id)
        assert any(r["kind"] == smod.EVENT_STATE_TRANSITION
                   and r["payload"]["to_state"] == smod.FAILED for r in events)
        again = controller.run()
        assert again["status"] == ctl.RUN_ALREADY_FAILED


# =========================================================================== #
# Artifacts (P13-P19)
# =========================================================================== #
class TestArtifacts:
    def test_p13_manifest_written_atomically(self, tmp_path):
        store = ast.ArtifactStore(str(tmp_path))
        store.ensure_campaign_layout("c1")
        store.write_manifest("c1", {"campaign_id": "c1", "current_state": "NEW_CAMPAIGN"})
        cdir = store.campaign_dir("c1")
        assert (cdir / "campaign.json").exists()
        assert not list(cdir.glob("*.tmp"))
        assert store.read_manifest("c1")["campaign_id"] == "c1"

    def test_p14_event_ledger_append_only_chain(self, tmp_path):
        store = ast.ArtifactStore(str(tmp_path))
        store.ensure_campaign_layout("c1")
        for i in range(3):
            store.append_event("c1", "K", {"i": i})
        assert store.verify_event_chain("c1")["intact"]
        path = store.campaign_dir("c1") / ast.EVENTS_LEDGER
        rows = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines()]
        rows[1]["payload"]["i"] = 999
        path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
        assert not store.verify_event_chain("c1")["intact"]

    def test_p15_experiment_artifacts_immutable(self, tmp_path):
        store = ast.ArtifactStore(str(tmp_path))
        store.ensure_campaign_layout("c1")
        store.write_experiment_artifact("c1", "e1", "metrics.json", {"a": 1})
        store.write_experiment_artifact("c1", "e1", "metrics.json", {"a": 1})  # idempotent
        with pytest.raises(ast.ImmutableArtifactError):
            store.write_experiment_artifact("c1", "e1", "metrics.json", {"a": 2})

    def test_p16_content_hashes_deterministic(self):
        a = {"x": 1, "y": [1, 2, {"z": 3.5}]}
        b = {"y": [1, 2, {"z": 3.5}], "x": 1}
        assert ast.content_hash(a) == ast.content_hash(b)
        assert ast.content_hash(a) != ast.content_hash({"x": 2})

    def test_p17_concurrent_campaign_lock_enforced(self, tmp_path, world, synth_inputs):
        controller = _new_campaign(tmp_path, world, synth_inputs)
        lock = controller.store.lock(controller.campaign_id, owner="other-process")
        lock.acquire()
        try:
            with pytest.raises(ast.CampaignLockedError):
                controller.store.lock(controller.campaign_id, owner="me").acquire()
            result = controller.run()
            assert result["status"] == ctl.RUN_LOCKED
        finally:
            lock.release()

    def test_p18_credentials_are_not_written(self, tmp_path):
        store = ast.ArtifactStore(str(tmp_path))
        store.ensure_campaign_layout("c1")
        with pytest.raises(ast.SecretLeakError):
            ast.write_json_atomic(store.campaign_dir("c1") / "x.json", {"api_key": "sk-123"})
        with pytest.raises(ast.SecretLeakError):
            store.append_event("c1", "K", {"nested": {"password": "hunter2"}})

    def test_p19_artifact_root_refused_inside_git_checkout(self):
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with pytest.raises(ast.ArtifactStoreError):
            ast.ArtifactStore(os.path.join(repo_root, "research_agent_artifacts"))


# =========================================================================== #
# Planner (P20-P27)
# =========================================================================== #
class TestPlanner:
    def test_p20_baseline_validation_scheduled_first(self, completed_campaign):
        events = completed_campaign["controller"].store.read_events(
            completed_campaign["controller"].campaign_id
        )
        tool_rows = [r for r in events if r["kind"] == "TOOL_RUN"]
        first_baseline = next(i for i, r in enumerate(tool_rows)
                              if r["payload"]["tool"] == "run_baseline_validation")
        first_experiment = next(i for i, r in enumerate(tool_rows)
                                if r["payload"]["tool"] == "run_parameter_experiment")
        assert first_baseline < first_experiment

    def test_p21_redundant_experiments_deduplicated(self):
        planner = pln.BoundedDeterministicPlanner()
        plan = planner.plan_experiments(make_config(), today=TODAY)
        assert any("baseline" in d for d in plan["deduplicated"])
        ids = [s["experiment_id"] for s in plan["planned"]]
        assert len(ids) == len(set(ids))
        seen = [s["spec_hash"] for s in plan["planned"]]
        plan2 = planner.plan_experiments(make_config(), seen_spec_hashes=seen, today=TODAY)
        assert plan2["planned"] == []
        assert set(plan2["deduplicated"]) >= set(ids)

    def test_p22_invalid_combinations_rejected(self):
        plan = pln.BoundedDeterministicPlanner().plan_experiments(make_config(), today=TODAY)
        reasons = {r["experiment_id"]: r["reason"] for r in plan["rejected"]}
        assert any(r == "UNSUPPORTED_COMBINATION" for r in reasons.values())
        rejected_text = json.dumps(plan["rejected"])
        assert "eligible_sessions_20" in rejected_text
        assert "current_validated" in rejected_text

    def test_p23_budget_enforced(self):
        plan = pln.BoundedDeterministicPlanner().plan_experiments(
            make_config(), max_experiments=3, today=TODAY
        )
        assert len(plan["planned"]) == 3
        assert any(r["reason"] == "BUDGET_EXHAUSTED" for r in plan["rejected"])
        cfg = make_config(budgets={
            "max_primary_experiments": 2, "max_robustness_candidates": 8,
            "max_registered_challengers": 3, "max_retry_per_experiment": 1,
            "experiment_timeout_seconds": 900})
        plan2 = pln.BoundedDeterministicPlanner().plan_experiments(cfg, today=TODAY)
        assert len(plan2["planned"]) == 2

    def test_p24_cheap_diagnostics_first(self):
        plan = pln.BoundedDeterministicPlanner().plan_experiments(make_config(), today=TODAY)

        def ndev(s):
            mp, pp = s["model_params"], s["portfolio_params"]
            return sum((
                (mp["fundamental_weight"], mp["momentum_weight"]) != (0.5, 0.5),
                pp["top_n"] != 25,
                pp["sector_treatment"] != "sector_cap",
                abs(pp["exit_buffer_fraction"]) > 1e-12,
            ))
        devs = [ndev(s) for s in plan["planned"]]
        assert devs == sorted(devs), "single-axis diagnostics must precede combined cells"
        assert devs[0] == 1

    def test_p25_failed_candidates_do_not_advance(self, completed_campaign):
        c = completed_campaign["controller"]
        queue = c.manifest.get("robustness_queue", [])
        for eid in sorted(c.memory.experiments()):
            decision = c.store.read_experiment_artifact(c.campaign_id, eid, "decision.json")
            if decision and decision.get("decision") != ev.RETAIN_FOR_ROBUSTNESS:
                assert eid not in queue

    def test_p26_survivors_receive_robustness_tests(self, tmp_path, world, synth_inputs):
        cfg = make_config(dims={
            "blend_weights": [[0.3, 0.7], [0.5, 0.5]],
            "portfolio_sizes": [25],
            "sector_treatments": ["sector_cap"],
            "exit_buffer_fractions": [0.0],
            "rebalance_treatments": ["monthly"],
            "cost_bps_per_side": [12.5, 25.0, 50.0],
            "defensive_overlays": ["off"],
            "min_adv_dollars": [10000000.0],
        })
        controller = _new_campaign(tmp_path, world, synth_inputs, config=cfg)
        mp = pytest.MonkeyPatch()
        try:
            real_decide = ev.decide_candidate

            def forced(gates, *, stage):
                if stage == "primary":
                    return {"decision": ev.RETAIN_FOR_ROBUSTNESS,
                            "reasons": ["forced for test"], "gate_overrides": []}
                return real_decide(gates, stage=stage)

            mp.setattr(ev, "decide_candidate", forced)
            result = controller.run()
        finally:
            mp.undo()
        assert result["final_state"] == smod.COMPLETE
        queue = controller.manifest.get("robustness_queue", [])
        assert queue, "forced survivor must be queued"
        events = controller.store.read_events(controller.campaign_id)
        evaluated = {r["payload"]["experiment_id"] for r in events
                     if r["kind"] == "ROBUSTNESS_EVALUATED"}
        assert set(queue) <= evaluated

    def test_p27_max_challenger_count_enforced(self, tmp_path):
        store = ast.ArtifactStore(str(tmp_path))
        store.ensure_campaign_layout("c1")
        registry = chal.ChallengerRegistry(store, "c1", max_registered_challengers=1)
        r1 = chal.build_challenger_record(
            candidate_id="cand_a", stage="SHADOW_ELIGIBLE", candidate_config={"w": 0.3},
            dataset_cutoff=CUTOFF, code_commit="abc", evidence_summary={},
            gate_results={}, known_weaknesses=[], required_forward_validation_horizon_days=63)
        assert registry.register(r1)["registered"]
        r2 = chal.build_challenger_record(
            candidate_id="cand_b", stage="SHADOW_ELIGIBLE", candidate_config={"w": 0.4},
            dataset_cutoff=CUTOFF, code_commit="abc", evidence_summary={},
            gate_results={}, known_weaknesses=[], required_forward_validation_horizon_days=63)
        with pytest.raises(chal.ChallengerBudgetError):
            registry.register(r2)


# =========================================================================== #
# Tools (P28-P35)
# =========================================================================== #
class TestTools:
    def _ctx(self, world, synth_inputs):
        return ta.ToolContext(config=make_config(), today=TODAY, inputs=synth_inputs,
                              reference_rows=[], close_frame=world["close_frame"])

    def test_p28_tool_inputs_are_typed(self, world, synth_inputs):
        reg = ta.build_registry()
        ctx = self._ctx(world, synth_inputs)
        env = reg.run("run_parameter_experiment", {"spec": "not-an-object"}, context=ctx)
        assert env["status"] == tr.STATUS_REJECTED_INVALID
        env2 = reg.run("validate_point_in_time_integrity", {"seed": "abc"}, context=ctx)
        assert env2["status"] == tr.STATUS_REJECTED_INVALID
        env3 = reg.run("audit_data_coverage", {"unknown_field": 1}, context=ctx)
        assert env3["status"] == tr.STATUS_REJECTED_INVALID

    def test_p29_tool_outputs_structured(self, world, synth_inputs):
        reg = ta.build_registry()
        ctx = self._ctx(world, synth_inputs)
        env = reg.run("audit_data_coverage", {}, context=ctx, data_cutoff=CUTOFF, seed=29)
        assert env["status"] == tr.STATUS_OK
        for key in ("tool", "started_at", "duration_seconds", "inputs_hash",
                    "data_cutoff", "output", "safety"):
            assert key in env
        assert env["output"]["n_fund_era_months"] >= 36

    def test_p30_tool_failures_are_structured(self, world, synth_inputs):
        reg = ta.build_registry()
        ctx = self._ctx(world, synth_inputs)  # no baseline metrics yet
        env = reg.run("compare_to_baseline", {"candidate_metrics": {}}, context=ctx)
        assert env["status"] == tr.STATUS_FAILED
        assert env["failure"]["type"] == "RuntimeError"
        assert "baseline" in env["failure"]["message"]

    def test_p31_tool_duration_recorded(self, world, synth_inputs):
        reg = ta.build_registry()
        env = reg.run("inspect_feature_availability", {}, context=self._ctx(world, synth_inputs))
        assert isinstance(env["duration_seconds"], float) and env["duration_seconds"] >= 0.0

    def test_p32_exact_data_cutoff_recorded(self, world, synth_inputs):
        reg = ta.build_registry()
        ctx = self._ctx(world, synth_inputs)
        env = reg.run("run_parameter_experiment", {"spec": make_spec()},
                      context=ctx, data_cutoff=CUTOFF, seed=29)
        assert env["data_cutoff"] == CUTOFF
        assert synth_inputs["provenance"]["data_cutoff"] == CUTOFF

    def test_p33_no_future_data_consumed(self, world, synth_inputs):
        assert synth_inputs["provenance"]["max_realized_month_end"] <= CUTOFF
        sim = fb.run_family_experiment(synth_inputs, fb.BASELINE_PARAMS)
        assert sim["last_month"] == "2021-11"  # 2021-12 formation would realize past cutoff
        pit = fb.validate_point_in_time_integrity(
            synth_inputs, seed=29, close_frame=world["close_frame"])
        assert pit["pit_integrity_ok"], pit["failed_checks"]

    def test_p34_no_paper_trader_write_possible(self):
        import research_agent
        pkg_dir = os.path.dirname(research_agent.__file__)
        forbidden = (r"C:\Users\binis\paper_trader", ".paper_trader",
                     "127.0.0.1", "localhost:8001", "localhost:9000")
        for fname in sorted(os.listdir(pkg_dir)):
            if not fname.endswith(".py"):
                continue
            src = open(os.path.join(pkg_dir, fname), "r", encoding="utf-8").read()
            for tok in forbidden:
                assert tok not in src, "%s references %s" % (fname, tok)

    def test_p35_no_shell_and_no_network(self):
        import research_agent
        pkg_dir = os.path.dirname(research_agent.__file__)
        forbidden = ("import subprocess", "from subprocess", "os.system(",
                     "Popen(", "import requests", "import urllib",
                     "urllib.request", "http://", "https://",
                     "ib_insync", "alpaca", "create_order(")
        for fname in sorted(os.listdir(pkg_dir)):
            if not fname.endswith(".py"):
                continue
            src = open(os.path.join(pkg_dir, fname), "r", encoding="utf-8").read()
            for tok in forbidden:
                assert tok not in src, "%s contains %s" % (fname, tok)


# =========================================================================== #
# Evaluation (P36-P42)
# =========================================================================== #
def _good_metrics(**over):
    m = {
        "pit_integrity_ok": True,
        "coverage_fraction": 0.95,
        "rank_ic_mean": 0.03,
        "rank_ic_t": 3.5,
        "rank_ic_ir": 0.4,
        "net_spy_excess_ann": 0.06,
        "net_excess_ann_by_cost_bps": {"12.5": 0.07, "25.0": 0.06, "50.0": 0.04},
        "cost_slope_12p5_to_50": 0.03,
        "turnover_monthly_oneside": 0.25,
        "max_sector_weight": 0.24,
        "n_positive_subperiods": 3,
        "n_subperiods": 3,
        "subperiod_positive_fraction": 1.0,
        "net_excess_ann_ex_best_subperiod": 0.03,
        "regime_positive_fraction": 0.75,
        "max_drawdown": 0.20,
        "volatility_ann": 0.18,
        "hit_rate": 0.6,
    }
    m.update(over)
    return m


BASELINE_M = _good_metrics(net_spy_excess_ann=0.03,
                           net_excess_ann_by_cost_bps={"12.5": 0.04, "25.0": 0.03, "50.0": 0.01},
                           rank_ic_mean=0.02, rank_ic_t=2.5, rank_ic_ir=0.3,
                           turnover_monthly_oneside=0.3, max_drawdown=0.25)


class TestEvaluation:
    def test_p36_baseline_relative_comparison_correct(self, world, synth_inputs):
        ctx = ta.ToolContext(config=make_config(), today=TODAY, inputs=synth_inputs,
                             reference_rows=[], close_frame=world["close_frame"])
        ctx.baseline_metrics = dict(BASELINE_M, months=47)
        reg = ta.build_registry()
        env = reg.run("compare_to_baseline", {"candidate_metrics": _good_metrics()}, context=ctx)
        assert env["status"] == tr.STATUS_OK
        d = env["output"]["deltas"]["net_spy_excess_ann"]
        assert d["delta"] == pytest.approx(0.03)
        assert env["output"]["deltas"]["turnover_monthly_oneside"]["delta"] == pytest.approx(-0.05)

    def test_p37_cost_collapse_rejects_candidate(self):
        m = _good_metrics(net_excess_ann_by_cost_bps={"12.5": 0.05, "25.0": -0.01, "50.0": -0.05})
        gates = ev.evaluate_gates(m, BASELINE_M)
        assert "cost_robustness_25bps" in gates["hard_gate_failures"]
        decision = ev.decide_candidate(gates, stage="primary")
        assert decision["decision"] == ev.REJECTED
        assert "cost_robustness_25bps" in decision["gate_overrides"]

    def test_p38_excessive_turnover_penalizes(self):
        m = _good_metrics(turnover_monthly_oneside=0.9)
        gates = ev.evaluate_gates(m, BASELINE_M)
        primary = ev.decide_candidate(gates, stage="primary")
        assert primary["decision"] == ev.INCONCLUSIVE
        robust = ev.decide_candidate(gates, stage="robustness")
        assert robust["decision"] != ev.SHADOW_ELIGIBLE
        score_hi = ev.score_candidate(m, BASELINE_M)
        score_lo = ev.score_candidate(_good_metrics(turnover_monthly_oneside=0.1), BASELINE_M)
        assert score_lo["final_score"] > score_hi["final_score"]

    def test_p39_concentration_violation_blocks(self):
        m = _good_metrics(max_sector_weight=0.48)
        gates = ev.evaluate_gates(m, BASELINE_M)
        assert "sector_concentration" in gates["hard_gate_failures"]
        assert ev.decide_candidate(gates, stage="robustness")["decision"] == ev.REJECTED

    def test_p40_pit_failure_blocks_campaign(self, tmp_path, world, synth_inputs):
        # covered end-to-end in test_p12: a misaligned close frame fails PIT and
        # the campaign transitions to FAILED. Here: the gate itself.
        gates = ev.evaluate_gates(_good_metrics(pit_integrity_ok=False), BASELINE_M)
        decision = ev.decide_candidate(gates, stage="primary")
        assert decision["decision"] == ev.REJECTED
        assert decision["gate_overrides"] == ["point_in_time_integrity"]

    def test_p41_score_exposes_components(self):
        score = ev.score_candidate(_good_metrics(), BASELINE_M,
                                   {"hard_gate_failures": ["coverage"]})
        assert score["components"], "components must be visible"
        for c in score["components"]:
            for key in ("component", "candidate", "baseline", "weight",
                        "normalization_scale", "normalized", "contribution"):
                assert key in c
        assert score["gate_overrides"] == ["coverage"]
        assert score["score_capped_by_gates"] is True
        assert "score" in score["explanation"].lower()

    def test_p42_total_return_alone_cannot_qualify(self):
        m = _good_metrics(net_spy_excess_ann=0.30, rank_ic_mean=-0.01, rank_ic_t=0.2)
        gates = ev.evaluate_gates(m, BASELINE_M)
        robust = ev.decide_candidate(gates, stage="robustness")
        assert robust["decision"] != ev.SHADOW_ELIGIBLE
        m2 = _good_metrics(net_spy_excess_ann=0.30,
                           net_excess_ann_by_cost_bps={"12.5": 0.3, "25.0": 0.2, "50.0": -0.01})
        robust2 = ev.decide_candidate(ev.evaluate_gates(m2, BASELINE_M), stage="robustness")
        assert robust2["decision"] != ev.SHADOW_ELIGIBLE


# =========================================================================== #
# Challenger registry (P43-P47)
# =========================================================================== #
class TestChallengerRegistry:
    def _registry(self, tmp_path, max_ch=3):
        store = ast.ArtifactStore(str(tmp_path))
        store.ensure_campaign_layout("c1")
        return chal.ChallengerRegistry(store, "c1", max_registered_challengers=max_ch)

    def _record(self, cid="cand_x", stage="SHADOW_ELIGIBLE", cfg=None):
        return chal.build_challenger_record(
            candidate_id=cid, stage=stage, candidate_config=cfg or {"w": 0.3},
            dataset_cutoff=CUTOFF, code_commit="abc", evidence_summary={"x": 1},
            gate_results={}, known_weaknesses=["w1"],
            required_forward_validation_horizon_days=63)

    def test_p43_qualified_candidate_can_become_shadow_eligible(self, tmp_path):
        registry = self._registry(tmp_path)
        out = registry.register(self._record())
        assert out["registered"]
        rec = registry.latest_by_candidate()["cand_x"]
        assert rec["stage"] == "SHADOW_ELIGIBLE"
        assert rec["approved_for_shadow_only"] is True

    def test_p44_candidate_cannot_become_operational(self, tmp_path):
        registry = self._registry(tmp_path)
        for stage in ("OPERATIONAL", "SHADOW_ACTIVE", "PROMOTION_CANDIDATE"):
            with pytest.raises(chal.ChallengerStageError):
                self._record(stage=stage)
            forged = self._record()
            forged["stage"] = stage
            with pytest.raises(chal.ChallengerStageError):
                registry.register(forged)

    def test_p45_challenger_record_immutable(self, tmp_path):
        registry = self._registry(tmp_path)
        registry.register(self._record(cfg={"w": 0.3}))
        with pytest.raises(chal.ChallengerImmutableError):
            registry.register(self._record(cfg={"w": 0.7}))

    def test_p46_duplicate_registration_idempotent(self, tmp_path):
        registry = self._registry(tmp_path)
        registry.register(self._record())
        out = registry.register(self._record())
        assert out["idempotent"] and not out["registered"]
        assert len(registry.latest_by_candidate()) == 1

    def test_p47_human_approval_remains_required(self, tmp_path):
        registry = self._registry(tmp_path)
        rec = self._record()
        assert rec["human_approval_required"] is True
        assert rec["operational_model_changed"] is False
        rec["human_approval_required"] = False
        with pytest.raises(chal.ChallengerStageError):
            registry.register(rec)


# =========================================================================== #
# Recovery / resume (P48-P52)
# =========================================================================== #
class TestRecovery:
    def test_p48_p49_p51_p52_pause_resume_no_duplicates(self, tmp_path, world, synth_inputs):
        controller = _new_campaign(tmp_path, world, synth_inputs)
        calls = {"n": 0}
        real_request = controller._operator_request

        def pause_after_two():
            done = sum(1 for r in controller.memory.experiments().values()
                       if r.get("status") == "COMPLETE")
            return "PAUSE" if done >= 2 else real_request()

        mp = pytest.MonkeyPatch()
        try:
            mp.setattr(controller, "_operator_request", pause_after_two)
            result = controller.run()
        finally:
            mp.undo()
        assert result["status"] == ctl.RUN_PAUSED  # P51 graceful pause
        assert controller.sm.current_state() == smod.PAUSED
        assert controller.sm.resume_state() == smod.EXPERIMENT_RUNNING

        pre = controller.memory.experiments()
        pre_complete = {eid: r["completed_at"] for eid, r in pre.items()
                        if r.get("status") == "COMPLETE"}
        assert len(pre_complete) >= 2

        # simulate a crash that left one experiment RUNNING mid-flight
        interrupted = next(eid for eid, r in pre.items() if r.get("status") == "PLANNED")
        crash_row = dict(pre[interrupted])
        crash_row.update(status="RUNNING", attempt=1, started_at="2026-07-24T00:00:00Z")
        controller.memory.record_experiment(crash_row)

        resumed = ctl.CampaignController(
            controller.campaign_id, artifact_root=str(tmp_path), today=TODAY,
            inputs=synth_inputs, reference_rows=[], close_frame=world["close_frame"])
        result2 = resumed.run()  # P52 resume continues from persisted state
        assert result2["final_state"] == smod.COMPLETE

        post = resumed.memory.experiments()
        assert post[interrupted]["status"] == "COMPLETE"  # P48 interrupted resumes safely
        for eid, ts in pre_complete.items():  # P49 completed not re-executed
            assert post[eid]["completed_at"] == ts
        rows = resumed.store.read_experiment_index(resumed.campaign_id)
        for eid in pre_complete:
            complete_rows = [r for r in rows
                             if r["experiment_id"] == eid and r.get("status") == "COMPLETE"]
            assert len(complete_rows) == 1

    def test_p50_retry_limit_enforced(self, tmp_path, world, synth_inputs):
        controller = _new_campaign(tmp_path, world, synth_inputs)

        def boom(_ctx, spec):
            raise RuntimeError("engine exploded")

        controller.registry._tools["run_parameter_experiment"].fn = boom
        result = controller.run()
        assert result["final_state"] == smod.COMPLETE  # campaign still reports
        experiments = controller.memory.experiments()
        assert experiments, "experiments must have been planned"
        for eid, row in experiments.items():
            assert row["status"] == "FAILED"
            assert row["attempt"] == 2  # 1 original + exactly 1 retry
            assert row["failure"]["classification"] == "TOOL_ERROR"
        rows = controller.store.read_experiment_index(controller.campaign_id)
        for eid in experiments:
            fails = [r for r in rows if r["experiment_id"] == eid and r.get("status") == "FAILED"]
            assert len(fails) == 2


# =========================================================================== #
# CLI (P53-P58)
# =========================================================================== #
class TestCli:
    def _cfg_file(self, tmp_path, cfg=None):
        path = tmp_path / "cfg.json"
        path.write_text(json.dumps(cfg or make_config()), encoding="utf-8")
        return str(path)

    def test_p53_validate_config_exit_codes(self, tmp_path, capsys):
        good = self._cfg_file(tmp_path)
        assert cli.main(["validate-config", "--config", good, "--json"]) == cli.EXIT_OK
        bad_cfg = make_config()
        bad_cfg["budgets"]["max_primary_experiments"] = -1
        bad = tmp_path / "bad.json"
        bad.write_text(json.dumps(bad_cfg), encoding="utf-8")
        assert cli.main(["validate-config", "--config", str(bad), "--json"]) == cli.EXIT_INVALID
        capsys.readouterr()

    def test_p54_create_produces_campaign_id(self, tmp_path, capsys):
        code = cli.main(["create", "--config", self._cfg_file(tmp_path),
                         "--artifact-root", str(tmp_path / "root"), "--json"])
        assert code == cli.EXIT_OK
        payload = json.loads(capsys.readouterr().out)
        assert payload["created"] is True
        assert payload["campaign_id"].startswith("t29a_synth_")

    def test_p55_dry_run_plans_without_executing(self, tmp_path, capsys):
        root = tmp_path / "root"
        cli.main(["create", "--config", self._cfg_file(tmp_path),
                  "--artifact-root", str(root), "--json"])
        cid = json.loads(capsys.readouterr().out)["campaign_id"]
        code = cli.main(["run", "--campaign-id", cid, "--artifact-root", str(root),
                         "--dry-run", "--json"])
        assert code == cli.EXIT_OK
        payload = json.loads(capsys.readouterr().out)
        assert payload["dry_run"] is True and payload["executed"] is False
        assert payload["planned"], "dry run must show the plan"
        store = ast.ArtifactStore(str(root))
        assert store.read_manifest(cid)["current_state"] == smod.NEW_CAMPAIGN
        assert store.read_experiment_index(cid) == []

    def test_p56_status_returns_structured_json(self, tmp_path, capsys):
        root = tmp_path / "root"
        cli.main(["create", "--config", self._cfg_file(tmp_path),
                  "--artifact-root", str(root), "--json"])
        cid = json.loads(capsys.readouterr().out)["campaign_id"]
        assert cli.main(["status", "--campaign-id", cid, "--artifact-root",
                         str(root), "--json"]) == cli.EXIT_OK
        payload = json.loads(capsys.readouterr().out)
        for key in ("campaign_id", "current_state", "budgets", "safety"):
            assert key in payload
        assert payload["safety"]["research_only"] is True

    def test_p57_report_works_for_incomplete_campaign(self, tmp_path, capsys):
        root = tmp_path / "root"
        cli.main(["create", "--config", self._cfg_file(tmp_path),
                  "--artifact-root", str(root), "--json"])
        cid = json.loads(capsys.readouterr().out)["campaign_id"]
        assert cli.main(["report", "--campaign-id", cid, "--artifact-root",
                         str(root), "--json"]) == cli.EXIT_OK
        payload = json.loads(capsys.readouterr().out)
        assert payload["incomplete"] is True
        for p in payload["artifact_paths"]:
            assert os.path.exists(p)

    def test_p58_invalid_campaign_id_fails_clearly(self, tmp_path, capsys):
        root = tmp_path / "root"
        root.mkdir()
        code = cli.main(["status", "--campaign-id", "does_not_exist",
                         "--artifact-root", str(root), "--json"])
        assert code == cli.EXIT_UNKNOWN_CAMPAIGN
        err = capsys.readouterr().err
        assert "unknown campaign id" in err

    def test_p58b_pause_command(self, tmp_path, capsys):
        root = tmp_path / "root"
        cli.main(["create", "--config", self._cfg_file(tmp_path),
                  "--artifact-root", str(root), "--json"])
        cid = json.loads(capsys.readouterr().out)["campaign_id"]
        assert cli.main(["pause", "--campaign-id", cid, "--artifact-root",
                         str(root), "--json"]) == cli.EXIT_OK
        payload = json.loads(capsys.readouterr().out)
        assert payload["requested"] == "PAUSE"

    def test_p58c_create_rejects_dry_run(self, tmp_path, capsys):
        # --dry-run belongs to run/resume only. A non-executing "create" is
        # validate-config; create's sole job is writing campaign scaffolding.
        root = tmp_path / "root"
        with pytest.raises(SystemExit) as exc:
            cli.main(["create", "--config", self._cfg_file(tmp_path),
                      "--artifact-root", str(root), "--dry-run"])
        assert exc.value.code == 2
        assert "--dry-run" in capsys.readouterr().err


# =========================================================================== #
# Safety (P59-P64)
# =========================================================================== #
class TestSafety:
    def test_p59_p60_operational_stores_untouched(self, completed_campaign):
        # the full synthetic campaign ran to COMPLETE; the operational desk
        # store and the multi-horizon store must be byte-for-byte untouched
        assert completed_campaign["result"]["final_state"] == smod.COMPLETE
        assert completed_campaign["desk_before"] == completed_campaign["desk_after"]
        assert completed_campaign["mhz_before"] == completed_campaign["mhz_after"]

    def test_p61_p62_p63_safety_contract_everywhere(self, completed_campaign):
        c = completed_campaign["controller"]
        status = c.status()
        for field, value in ra.SAFETY_CONTRACT.items():
            assert status["safety"][field] == value
        report = rep.build_report(c.store, c.campaign_id)
        assert report["safety"]["creates_orders"] is False
        assert report["safety"]["broker_execution"] is False
        assert report["safety"]["automation_of_trading"] is False
        assert report["safety"]["promotion_requires_human_approval"] is True
        assert report["safety"]["arbitrary_code_execution"] is False
        heartbeat = c.store.read_status(c.campaign_id)
        assert heartbeat["safety"]["research_only"] is True

    def test_p64_no_deployment_or_scheduler_hooks(self):
        import research_agent
        pkg_dir = os.path.dirname(research_agent.__file__)
        forbidden = ("gcloud", "systemctl", "stock-api.service", "ssh ",
                     "import sched", "crontab", "CronCreate")
        for fname in sorted(os.listdir(pkg_dir)):
            if not fname.endswith(".py"):
                continue
            src = open(os.path.join(pkg_dir, fname), "r", encoding="utf-8").read()
            for tok in forbidden:
                assert tok not in src, "%s contains %s" % (fname, tok)

    def test_p64b_campaign_writes_confined_to_artifact_root(self, completed_campaign):
        c = completed_campaign["controller"]
        cdir = c.store.campaign_dir(c.campaign_id)
        expected = {"campaign.json", "events.jsonl", "hypotheses.jsonl",
                    "experiment_index.jsonl", "experiments", "challengers",
                    "reports", "locks", "status.json", "operator_request.json"}
        actual = {p.name for p in cdir.iterdir()}
        assert actual <= expected, "unexpected artifacts: %s" % (actual - expected)


# =========================================================================== #
# Harness correctness (baseline, reference replay, buffer, universe)
# =========================================================================== #
class TestHarness:
    def test_baseline_validation_deterministic(self, synth_inputs):
        bv = fb.run_baseline_validation(synth_inputs, reference_rows=[])
        assert bv["deterministic"] is True
        assert bv["invariant_failures"] == []
        assert bv["reference_available"] is False
        assert bv["baseline_reproduced"] is True
        assert bv["n_periods"] >= 36

    def test_reference_replay_round_trip_and_tamper(self, synth_inputs):
        sim = fb.run_family_experiment(synth_inputs, fb.BASELINE_PARAMS)
        rt = 2.0 * (fb.BASELINE_REFERENCE_COST_BPS_PER_SIDE / 1e4)
        ref = [{"month": p["month"], "gross": round(p["gross"], 6),
                "net": round(p["gross"] - rt * p["turnover"], 6),
                "turnover": round(p["turnover"], 4), "n": p["n"]}
               for p in sim["periods"]]
        bv = fb.run_baseline_validation(synth_inputs, reference_rows=ref)
        assert bv["reference_reproduced"] is True and bv["reference_mismatch_count"] == 0
        tampered = [dict(r) for r in ref]
        tampered[3]["gross"] += 0.01
        bv2 = fb.run_baseline_validation(synth_inputs, reference_rows=tampered)
        assert bv2["reference_reproduced"] is False
        assert bv2["baseline_reproduced"] is False  # fail-fast trigger

    def test_sector_cap_respected(self, synth_inputs):
        sim = fb.run_family_experiment(synth_inputs, fb.BASELINE_PARAMS)
        cap = max(1, int(fb.SECTOR_CAP_FRACTION * 25))
        for p in sim["periods"]:
            for sec, w in p["sector_weights"].items():
                if sec != "Unknown":
                    assert round(w * p["n"]) <= cap

    def test_exit_buffer_reduces_turnover(self, synth_inputs):
        base = fb.run_family_experiment(synth_inputs, fb.BASELINE_PARAMS)
        buffered = fb.run_family_experiment(
            synth_inputs, dict(fb.BASELINE_PARAMS, exit_buffer_fraction=0.20))
        t_base = [p["turnover"] for p in base["periods"] if not p["established"]]
        t_buf = [p["turnover"] for p in buffered["periods"] if not p["established"]]
        assert sum(t_buf) / len(t_buf) <= sum(t_base) / len(t_base)

    def test_universe_filter_shrinks_universe(self, synth_inputs):
        base = fb.run_family_experiment(synth_inputs, fb.BASELINE_PARAMS)
        live = fb.run_family_experiment(
            synth_inputs,
            dict(fb.BASELINE_PARAMS, universe="mhz_live_eligibility",
                 min_adv_dollar=2.5e7))
        assert live["periods"][0]["n_common"] < base["periods"][0]["n_common"]

    def test_metrics_battery_complete(self, synth_inputs):
        sim = fb.run_family_experiment(synth_inputs, fb.BASELINE_PARAMS)
        m = fb.compute_experiment_metrics(sim, synth_inputs, primary_cost_bps_per_side=25.0)
        for key in ("net_spy_excess_ann", "net_excess_ann_by_cost_bps", "rank_ic_mean",
                    "rank_ic_t", "turnover_monthly_oneside", "max_drawdown",
                    "volatility_ann", "hit_rate", "n_positive_subperiods",
                    "regime_positive_fraction", "max_sector_weight", "coverage_fraction"):
            assert key in m
        assert m["months"] == sim["n_periods"]
        assert set(m["net_excess_ann_by_cost_bps"]) == {"12.5", "25.0", "50.0"}
        # higher costs can never raise the net excess
        bc = m["net_excess_ann_by_cost_bps"]
        assert bc["12.5"] >= bc["25.0"] >= bc["50.0"]


@pytest.mark.skipif(
    not (os.path.exists(fb.DEFAULT_MOMENTUM_PANEL)
         and os.path.exists(fb.DEFAULT_FUND_PANEL)
         and os.path.exists(fb.DEFAULT_REFERENCE_BOOK_RETURNS)),
    reason="owned real datasets not present",
)
class TestRealDataIntegration:
    def test_real_baseline_reproduces_owned_reference_exactly(self):
        inputs = fb.load_family_inputs(data_cutoff="2026-06-30")
        bv = fb.run_baseline_validation(inputs)
        assert bv["deterministic"] is True
        assert bv["reference_available"] is True
        assert bv["reference_reproduced"] is True, {
            "mismatches": bv["reference_mismatches"],
            "only_ref": bv["months_only_in_reference"],
            "only_ours": bv["months_only_in_ours"],
        }
        assert bv["baseline_reproduced"] is True
        assert bv["reference_months_compared"] >= 100
