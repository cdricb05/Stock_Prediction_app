"""Phase 29A.2 — resumable campaign semantics, gate audit, reporting.

Covers the Part I contract:
  Campaign limits  (C1-C8):   per-invocation budget, resume, ledger, finalize
  Hypotheses       (H9-H13):  explicit evidence-derived lifecycle
  Evaluation       (E14-E22): primary retention vs strict shadow gates
  Reporting        (R23-R28): reconciled totals, reasons, deltas, next action
  Safety           (S29-S31): pilot untouched, Paper Trader untouched, no
                              orders/broker/automation/promotion

Fully offline: the same deterministic synthetic world as Phase 29A is
injected through the harness seams. The immutable pilot campaign on D: is
only ever os.stat'ed (never read, never written).
"""

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

from research_agent import artifact_store as ast  # noqa: E402
from research_agent import cli  # noqa: E402
from research_agent import controller as ctl  # noqa: E402
from research_agent import evaluator as ev  # noqa: E402
from research_agent import family_backtest as fb  # noqa: E402
from research_agent import memory as mem  # noqa: E402
from research_agent import reporting as rep  # noqa: E402
from research_agent import state_machine as smod  # noqa: E402

PILOT_DIR = (
    r"D:\Stock_Prediction_app_data\research_agent\campaigns"
    r"\phase29a_first_campaign_20260726T152754Z"
)
DESK_DIR = os.path.join(os.path.expanduser("~"), ".paper_trader", "paper_trading_desk")


# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #
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


@pytest.fixture(scope="module")
def guard_snapshots():
    """Taken BEFORE any campaign fixture runs; compared by the safety tests."""
    return {
        "pilot": _snapshot_dir(PILOT_DIR),
        "desk": _snapshot_dir(DESK_DIR),
    }


def _controller_for(root, world, synth_inputs, campaign_id):
    return ctl.CampaignController(
        campaign_id,
        artifact_root=str(root),
        today=TODAY,
        inputs=synth_inputs,
        reference_rows=[],
        close_frame=world["close_frame"],
    )


def _new_campaign(root, world, synth_inputs, config=None):
    created = ctl.create_campaign(
        config or make_config(), artifact_root=str(root), today=TODAY
    )
    assert created["created"], created
    return _controller_for(root, world, synth_inputs, created["campaign_id"])


def _read_bytes(path):
    with open(path, "rb") as fh:
        return fh.read()


@pytest.fixture(scope="module")
def paused_flow(tmp_path_factory, world, synth_inputs, guard_snapshots):
    """run --max-experiments 3 then resume --max-experiments 2 (never finished)."""
    root = tmp_path_factory.mktemp("agent29a2_paused")
    controller = _new_campaign(root, world, synth_inputs)
    cid = controller.campaign_id
    cdir = controller.store.campaign_dir(cid)

    result1 = controller.run(max_experiments=3)
    snap1 = {
        "experiments": controller.memory.experiments(),
        "hyp_rows": list(controller.store.read_hypotheses(cid)),
        "inv_rows": list(controller.store.read_invocations(cid)),
        "inv_bytes": _read_bytes(cdir / "invocations.jsonl"),
        "state": controller.sm.current_state(),
        "status": controller.status(),
        "report": rep.build_report(controller.store, cid),
    }

    resumed = _controller_for(root, world, synth_inputs, cid)
    result2 = resumed.run(max_experiments=2)
    snap2 = {
        "experiments": resumed.memory.experiments(),
        "hyp_rows": list(resumed.store.read_hypotheses(cid)),
        "inv_rows": list(resumed.store.read_invocations(cid)),
        "inv_bytes": _read_bytes(cdir / "invocations.jsonl"),
        "state": resumed.sm.current_state(),
        "status": resumed.status(),
        "report": rep.build_report(resumed.store, cid),
    }
    return {
        "root": root,
        "campaign_id": cid,
        "controller": resumed,
        "result1": result1,
        "result2": result2,
        "snap1": snap1,
        "snap2": snap2,
    }


@pytest.fixture(scope="module")
def completed_flow(tmp_path_factory, world, synth_inputs, guard_snapshots):
    root = tmp_path_factory.mktemp("agent29a2_complete")
    controller = _new_campaign(root, world, synth_inputs)
    result = controller.run()
    return {"root": root, "controller": controller, "result": result,
            "report": rep.build_report(controller.store, controller.campaign_id)}


@pytest.fixture(scope="module")
def finalized_flow(tmp_path_factory, world, synth_inputs, guard_snapshots):
    root = tmp_path_factory.mktemp("agent29a2_finalized")
    controller = _new_campaign(root, world, synth_inputs)
    paused = controller.run(max_experiments=2)
    assert paused["status"] == ctl.RUN_PAUSED
    result = controller.finalize(
        reason="operator abandons the remainder: bounded 29A.2 test budget"
    )
    return {"root": root, "controller": controller, "paused": paused,
            "result": result,
            "report": rep.build_report(controller.store, controller.campaign_id)}


# The default synthetic config plans 7 supported cells (8-cell grid minus the
# deduplicated baseline cell) and structurally rejects 2 unsupported probes.
PLANNED_TOTAL = 7


# =========================================================================== #
# Campaign limits (C1-C8)
# =========================================================================== #
class TestInvocationBudget:
    def test_c1_per_invocation_limit_does_not_complete(self, paused_flow):
        r1 = paused_flow["result1"]
        assert r1["status"] == ctl.RUN_PAUSED
        assert r1["final_state"] == smod.PAUSED
        assert r1["pause_reason"] == ctl.PAUSE_INVOCATION_LIMIT
        assert r1["invocation_limit"] == 3
        assert paused_flow["snap1"]["state"] == smod.PAUSED
        assert paused_flow["snap1"]["state"] != smod.COMPLETE

    def test_c2_remaining_counts_correct(self, paused_flow):
        r1 = paused_flow["result1"]
        assert r1["planned_total"] == PLANNED_TOTAL
        assert r1["completed_total"] == 3
        assert r1["remaining_total"] == PLANNED_TOTAL - 3
        exps = paused_flow["snap1"]["experiments"]
        assert sum(1 for r in exps.values() if r["status"] == "COMPLETE") == 3
        assert sum(1 for r in exps.values() if r["status"] == "PLANNED") == PLANNED_TOTAL - 3

    def test_c3_paused_resumable_with_stop_reason(self, paused_flow):
        status = paused_flow["snap1"]["status"]
        assert status["current_state"] == smod.PAUSED
        assert status["resumable"] is True
        assert status["incomplete"] is True
        assert status["last_stop_reason"] == ctl.PAUSE_INVOCATION_LIMIT
        assert status["last_pause"]["pause_reason"] == ctl.PAUSE_INVOCATION_LIMIT

    def test_c4_resume_executes_only_pending(self, paused_flow):
        r2 = paused_flow["result2"]
        assert r2["status"] == ctl.RUN_PAUSED  # 2 cells still remain
        assert r2["completed_total"] == 5
        assert r2["remaining_total"] == PLANNED_TOTAL - 5
        before = {eid: r["completed_at"]
                  for eid, r in paused_flow["snap1"]["experiments"].items()
                  if r["status"] == "COMPLETE"}
        after = paused_flow["snap2"]["experiments"]
        for eid, ts in before.items():
            assert after[eid]["status"] == "COMPLETE"
            assert after[eid]["completed_at"] == ts  # never re-executed

    def test_c5_completed_never_duplicated(self, paused_flow):
        c = paused_flow["controller"]
        rows = c.store.read_experiment_index(c.campaign_id)
        for eid, row in paused_flow["snap2"]["experiments"].items():
            if row["status"] != "COMPLETE":
                continue
            complete_rows = [r for r in rows
                             if r["experiment_id"] == eid and r["status"] == "COMPLETE"]
            assert len(complete_rows) == 1, eid
        hashes = [r["candidate_spec_hash"]
                  for r in paused_flow["snap2"]["experiments"].values()]
        assert len(hashes) == len(set(hashes))

    def test_c6_invocation_ledger_append_only(self, paused_flow):
        rows1 = paused_flow["snap1"]["inv_rows"]
        rows2 = paused_flow["snap2"]["inv_rows"]
        assert len(rows1) == 2  # START + END for invocation 1
        assert len(rows2) == 4
        # append-only: the first invocation's bytes are a strict prefix
        assert paused_flow["snap2"]["inv_bytes"].startswith(
            paused_flow["snap1"]["inv_bytes"]
        )
        end1 = rows1[1]
        assert end1["phase"] == "END"
        assert end1["requested_max_experiments"] == 3
        assert end1["experiments_attempted"] == 3
        assert end1["experiments_completed"] == 3
        assert end1["experiments_failed"] == 0
        assert end1["stop_reason"] == ctl.PAUSE_INVOCATION_LIMIT
        assert end1["state_after"] == smod.PAUSED
        for key in ("invocation_id", "started_at", "completed_at"):
            assert end1.get(key)
        end2 = rows2[3]
        assert end2["invocation_id"] != end1["invocation_id"]
        assert end2["experiments_completed"] == 2

    def test_c7_complete_requires_no_remaining_work(
        self, tmp_path, world, synth_inputs, completed_flow
    ):
        # positive: a full run reaches COMPLETE only with zero remaining work
        assert completed_flow["result"]["final_state"] == smod.COMPLETE
        assert completed_flow["result"]["remaining_total"] == 0
        # negative: REPORTING with pending supported work refuses COMPLETE
        controller = _new_campaign(tmp_path, world, synth_inputs)
        paused = controller.run(max_experiments=1)
        assert paused["status"] == ctl.RUN_PAUSED
        manifest = controller.manifest
        manifest["current_state"] = smod.REPORTING
        manifest["resume_state"] = None
        controller.store.write_manifest(controller.campaign_id, manifest)
        res = controller.run()
        assert res["status"] == ctl.RUN_PAUSED
        assert res["pause_reason"] == ctl.PAUSE_REMAINING_WORK
        assert controller.sm.current_state() != smod.COMPLETE

    def test_c8_finalize_records_reason(self, finalized_flow):
        r = finalized_flow["result"]
        assert r["finalized"] is True
        assert r["final_state"] == smod.COMPLETE
        c = finalized_flow["controller"]
        fin = c.manifest["finalization"]
        assert "bounded 29A.2 test budget" in fin["reason"]
        assert sorted(fin["abandoned_experiment_ids"]) == sorted(
            r["abandoned_experiment_ids"]
        )
        assert len(fin["abandoned_experiment_ids"]) == PLANNED_TOTAL - 2
        exps = c.memory.experiments()
        for eid in fin["abandoned_experiment_ids"]:
            assert exps[eid]["status"] == "ABANDONED"
            assert "bounded 29A.2 test budget" in exps[eid]["abandon_reason"]
        events = c.store.read_events(c.campaign_id)
        fin_events = [e for e in events if e["kind"] == "CAMPAIGN_FINALIZED"]
        assert len(fin_events) == 1
        assert fin_events[0]["payload"]["reason"] == fin["reason"]
        report = finalized_flow["report"]
        assert report["finalization"]["reason"] == fin["reason"]
        assert report["abandoned_total"] == PLANNED_TOTAL - 2
        assert report["incomplete"] is False  # explicit, reasoned completion

    def test_c8b_finalize_requires_reason(self, tmp_path, world, synth_inputs):
        controller = _new_campaign(tmp_path, world, synth_inputs)
        res = controller.finalize(reason="   ")
        assert res["finalized"] is False
        assert "reason" in res["note"]

    def test_c8c_cli_finalize_requires_reason_flag(self, tmp_path, capsys):
        cfg = tmp_path / "cfg.json"
        cfg.write_text(json.dumps(make_config()), encoding="utf-8")
        root = tmp_path / "root"
        cli.main(["create", "--config", str(cfg),
                  "--artifact-root", str(root), "--json"])
        cid = json.loads(capsys.readouterr().out)["campaign_id"]
        with pytest.raises(SystemExit) as exc:
            cli.main(["finalize", "--campaign-id", cid,
                      "--artifact-root", str(root)])
        assert exc.value.code == 2
        assert "--reason" in capsys.readouterr().err


# =========================================================================== #
# Hypothesis lifecycle (H9-H13)
# =========================================================================== #
def _cells(*specs):
    out = {}
    for i, (hyp, status) in enumerate(specs):
        eid = "exp_%02d" % i
        out[eid] = {"experiment_id": eid, "hypothesis_id": hyp, "status": status}
    return out


class TestHypothesisLifecycle:
    def test_h9_queued_becomes_active_when_execution_starts(self, paused_flow):
        # unit: one RUNNING cell, nothing terminal
        st = mem.derive_hypothesis_status(
            "h", experiments=_cells(("h", "RUNNING"), ("h", "PLANNED")),
            decisions={},
        )
        assert st == "ACTIVE"
        # integration: the ledger recorded an ACTIVE row when execution began
        statuses = [r["status"] for r in paused_flow["snap1"]["hyp_rows"]]
        assert "ACTIVE" in statuses

    def test_h10_partial_coverage_becomes_partially_tested(self, paused_flow):
        st = mem.derive_hypothesis_status(
            "h",
            experiments=_cells(*([("h", "COMPLETE")] * 2 + [("h", "PLANNED")] * 8)),
            decisions={},
        )
        assert st == "PARTIALLY_TESTED"
        # integration: after 3 of 7 cells, a partially-covered hypothesis
        hyps = {r["hypothesis_id"]: r for r in paused_flow["snap1"]["hyp_rows"]}
        latest = {}
        for r in paused_flow["snap1"]["hyp_rows"]:
            latest[r["hypothesis_id"]] = r["status"]
        assert latest["hyp_blend_balance"] == "PARTIALLY_TESTED"
        assert hyps  # ledger non-empty

    def test_h11_fully_tested_no_survivor(self, completed_flow):
        st = mem.derive_hypothesis_status(
            "h",
            experiments=_cells(("h", "COMPLETE"), ("h", "COMPLETE")),
            decisions={"exp_00": "REJECTED", "exp_01": "INCONCLUSIVE"},
        )
        assert st == "TESTED_NO_SURVIVOR"
        # exhausted: every supported cell terminal without usable evidence
        st2 = mem.derive_hypothesis_status(
            "h",
            experiments=_cells(("h", "FAILED"), ("h", "REJECTED_UNSUPPORTED")),
            decisions={},
        )
        assert st2 == "EXHAUSTED"
        # integration (Part D headline): nothing remains QUEUED after evaluation
        c = completed_flow["controller"]
        for hid, h in c.memory.hypotheses().items():
            assert h["status"] != "QUEUED", hid

    def test_h12_retained_candidate_updates_hypothesis(
        self, tmp_path, world, synth_inputs
    ):
        controller = _new_campaign(tmp_path, world, synth_inputs)
        real_decide = ev.decide_candidate
        mp = pytest.MonkeyPatch()
        try:
            def forced(gates, *, stage, **kw):
                if stage == "primary":
                    return {"decision": ev.RETAIN_FOR_ROBUSTNESS,
                            "reasons": ["forced for test"], "gate_overrides": [],
                            "diagnostic_flags": [], "stage_policy": None}
                return real_decide(gates, stage=stage, **kw)
            mp.setattr(ev, "decide_candidate", forced)
            result = controller.run()
        finally:
            mp.undo()
        assert result["final_state"] == smod.COMPLETE
        # intermediate RETAINED_FOR_ROBUSTNESS rows were appended...
        rows = controller.store.read_hypotheses(controller.campaign_id)
        assert any(r["status"] == "RETAINED_FOR_ROBUSTNESS" for r in rows)
        # ...and the final derived status reflects the completed battery
        latest = controller.memory.hypotheses()
        assert latest["hyp_blend_balance"]["status"] == "ROBUSTNESS_COMPLETE"

    def test_h13_resume_preserves_hypothesis_history(self, paused_flow):
        rows1 = paused_flow["snap1"]["hyp_rows"]
        rows2 = paused_flow["snap2"]["hyp_rows"]
        assert len(rows2) >= len(rows1)
        assert rows2[: len(rows1)] == rows1  # append-only: prefix intact


# =========================================================================== #
# Evaluation policy (E14-E22)
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
        "membership_stability": 0.6,
        "gross_return_ann": 0.15,
        "hit_rate": 0.6,
    }
    m.update(over)
    return m


BASE = _good_metrics(
    net_spy_excess_ann=0.03,
    net_excess_ann_by_cost_bps={"12.5": 0.04, "25.0": 0.03, "50.0": 0.01},
    rank_ic_mean=0.02, rank_ic_t=2.5, rank_ic_ir=0.3,
    turnover_monthly_oneside=0.30, max_drawdown=0.25,
)


def _decide_primary(m, base=BASE, thresholds=None):
    gates = ev.evaluate_gates(m, base, thresholds)
    deltas = ev.build_baseline_deltas(m, base)
    return ev.decide_candidate(gates, stage="primary", deltas=deltas), gates, deltas


class TestEvaluationPolicy:
    def test_e14_primary_and_shadow_gates_distinct(self):
        assert set(ev.PRIMARY_RETENTION_GATES) < set(ev.SHADOW_ELIGIBILITY_GATES)
        assert set(ev.PRIMARY_DIAGNOSTIC_GATES).isdisjoint(ev.PRIMARY_RETENTION_GATES)
        pp = ev.stage_policy(ev.STAGE_PRIMARY)
        sp = ev.stage_policy(ev.STAGE_ROBUSTNESS)
        assert pp["blocking_gates"] != sp["blocking_gates"]
        decision, _, _ = _decide_primary(_good_metrics())
        assert decision["stage_policy"]["stage"] == ev.STAGE_PRIMARY
        assert decision["stage_policy"]["blocking_gates"] == list(ev.HARD_GATES)

    def test_e15_provisional_diagnostics_do_not_block_primary(self):
        # absolute rank-IC and turnover thresholds FAIL, but both are within
        # tolerance of the baseline; the material balanced improvement retains
        base = dict(BASE, rank_ic_t=0.85, rank_ic_mean=0.0105,
                    turnover_monthly_oneside=0.40)
        m = _good_metrics(
            rank_ic_t=0.80, rank_ic_mean=0.0100,       # < 2.0 absolute
            turnover_monthly_oneside=0.41,             # > 0.35 absolute
            net_spy_excess_ann=0.09,
            net_excess_ann_by_cost_bps={"12.5": 0.10, "25.0": 0.09, "50.0": 0.07},
            max_drawdown=0.25,
        )
        decision, gates, _ = _decide_primary(m, base)
        gmap = {g["gate"]: g for g in gates["gates"]}
        assert gmap["rank_ic"]["passed"] is False
        assert gmap["turnover"]["passed"] is False
        assert decision["decision"] == ev.RETAIN_FOR_ROBUSTNESS
        flagged = " ".join(decision["diagnostic_flags"])
        assert "rank_ic" in flagged and "turnover" in flagged

    def test_e16_pit_failure_always_blocks(self):
        m = _good_metrics(pit_integrity_ok=False)
        decision, _, _ = _decide_primary(m)
        assert decision["decision"] == ev.REJECTED
        assert decision["gate_overrides"] == ["point_in_time_integrity"]
        gates = ev.evaluate_gates(m, BASE)
        robust = ev.decide_candidate(gates, stage="robustness")
        assert robust["decision"] == ev.REJECTED

    def test_e17_cost_failure_always_blocks(self):
        m = _good_metrics(
            net_excess_ann_by_cost_bps={"12.5": 0.05, "25.0": -0.01, "50.0": -0.05}
        )
        decision, gates, _ = _decide_primary(m)
        assert "cost_robustness_25bps" in gates["hard_gate_failures"]
        assert decision["decision"] == ev.REJECTED

    def test_e18_severe_concentration_blocks(self):
        decision, gates, _ = _decide_primary(_good_metrics(max_sector_weight=0.48))
        assert "sector_concentration" in gates["hard_gate_failures"]
        assert decision["decision"] == ev.REJECTED

    def test_e19_total_return_alone_cannot_retain(self):
        # big return win, materially worse drawdown/vol/turnover/IC: unbalanced
        m = _good_metrics(
            net_spy_excess_ann=0.10,
            net_excess_ann_by_cost_bps={"12.5": 0.11, "25.0": 0.10, "50.0": 0.08},
            max_drawdown=0.28, volatility_ann=0.21,
            turnover_monthly_oneside=0.35, rank_ic_mean=0.015, rank_ic_t=2.0,
        )
        decision, _, deltas = _decide_primary(m)
        assert len(deltas["material_degradations"]) >= 2
        assert decision["decision"] != ev.RETAIN_FOR_ROBUSTNESS
        assert decision["decision"] == ev.INCONCLUSIVE
        # severe single degradation also blocks retention (p38 analogue)
        m2 = _good_metrics(turnover_monthly_oneside=0.9)
        decision2, _, deltas2 = _decide_primary(m2)
        assert "turnover_monthly_oneside" in deltas2["severe_degradations"]
        assert decision2["decision"] == ev.INCONCLUSIVE

    def test_e20_material_balanced_improvement_retained(self):
        decision, _, _ = _decide_primary(_good_metrics(max_drawdown=0.24))
        assert decision["decision"] == ev.RETAIN_FOR_ROBUSTNESS
        # a useful robustness trade-off also retains: return inside tolerance,
        # turnover materially better (the hyp_exit_buffer shape)
        m = dict(BASE)
        m.update(
            net_spy_excess_ann=0.028,
            net_excess_ann_by_cost_bps={"12.5": 0.038, "25.0": 0.028, "50.0": 0.009},
            turnover_monthly_oneside=0.20,
        )
        decision2, _, _ = _decide_primary(m)
        assert decision2["decision"] == ev.RETAIN_FOR_ROBUSTNESS
        assert any("trade-off" in r for r in decision2["reasons"])

    def test_e21_strict_shadow_gates_remain_required(self):
        # failing rank-IC at the robustness stage can never be SHADOW_ELIGIBLE
        m = _good_metrics(rank_ic_t=1.0)
        robust = ev.decide_candidate(ev.evaluate_gates(m, BASE), stage="robustness")
        assert robust["decision"] != ev.SHADOW_ELIGIBLE
        # failing 50 bps survival can never be SHADOW_ELIGIBLE
        m2 = _good_metrics(
            net_excess_ann_by_cost_bps={"12.5": 0.07, "25.0": 0.05, "50.0": -0.01}
        )
        robust2 = ev.decide_candidate(ev.evaluate_gates(m2, BASE), stage="robustness")
        assert robust2["decision"] != ev.SHADOW_ELIGIBLE
        # missing turnover evidence can never be SHADOW_ELIGIBLE
        m3 = _good_metrics()
        m3.pop("turnover_monthly_oneside")
        robust3 = ev.decide_candidate(ev.evaluate_gates(m3, BASE), stage="robustness")
        assert robust3["decision"] != ev.SHADOW_ELIGIBLE
        # the full strict standard still grants shadow eligibility
        ok = ev.decide_candidate(ev.evaluate_gates(_good_metrics(), BASE),
                                 stage="robustness")
        assert ok["decision"] == ev.SHADOW_ELIGIBLE

    def test_e22_primary_never_shadow_eligible(self):
        for m in (
            _good_metrics(),
            _good_metrics(net_spy_excess_ann=0.30),
            _good_metrics(rank_ic_t=9.9, turnover_monthly_oneside=0.05),
        ):
            decision, _, _ = _decide_primary(m)
            assert decision["decision"] != ev.SHADOW_ELIGIBLE
            assert decision["decision"] in (
                ev.REJECTED, ev.INCONCLUSIVE, ev.RETAIN_FOR_ROBUSTNESS
            )


# =========================================================================== #
# Reporting (R23-R28)
# =========================================================================== #
class TestReporting:
    def test_r23_incomplete_true_with_pending_work(self, paused_flow):
        report = paused_flow["snap2"]["report"]
        assert report["incomplete"] is True
        assert report["resumable"] is True
        assert report["current_state"] == smod.PAUSED

    def test_r24_totals_reconcile(self, paused_flow, completed_flow):
        for report in (paused_flow["snap2"]["report"], completed_flow["report"]):
            accounted = (
                report["completed_total"] + report["failed_total"]
                + report["remaining_total"] + report["abandoned_total"]
                + report["skipped_budget_total"]
                + sum(1 for r in report["experiments"]
                      if r["status"] == "REJECTED_UNSUPPORTED")
            )
            assert report["planned_total"] == accounted
            assert report["planned_total"] == len(report["experiments"])
        assert paused_flow["snap2"]["report"]["completed_total"] == 5
        assert paused_flow["snap2"]["report"]["remaining_total"] == PLANNED_TOTAL - 5
        # status and report agree
        status = paused_flow["snap2"]["status"]
        for key in ("planned_total", "completed_total", "remaining_total",
                    "supported_total", "failed_total"):
            assert status[key] == paused_flow["snap2"]["report"][key]

    def test_r25_exact_gate_reasons_appear(self, completed_flow):
        report = completed_flow["report"]
        evaluated = [r for r in report["experiments"]
                     if r["status"] == "COMPLETE" and r["decision"]]
        assert evaluated
        for row in evaluated:
            assert row["decision_reasons"], row["experiment_id"]
            assert row["hard_gate_failures"] is not None
            if row["decision"] == "REJECTED" and row["hard_gate_failures"]:
                joined = " ".join(row["decision_reasons"])
                assert "hard gate failed" in joined

    def test_r26_baseline_deltas_reconcile(self, completed_flow):
        c = completed_flow["controller"]
        report = completed_flow["report"]
        baseline_excess = report["baseline_metrics"]["net_spy_excess_ann"]
        checked = 0
        for row in report["experiments"]:
            if row["status"] != "COMPLETE" or not row["baseline_deltas"]:
                continue
            d = row["baseline_deltas"]["net_spy_excess_ann"]
            assert d["baseline"] == pytest.approx(baseline_excess)
            assert d["delta_abs"] == pytest.approx(
                row["net_spy_excess_ann"] - baseline_excess
            )
            doc = c.store.read_experiment_artifact(
                c.campaign_id, row["experiment_id"], "baseline_deltas.json"
            )
            assert doc and doc["metrics"]["net_spy_excess_ann"]["classification"] \
                == d["classification"]
            checked += 1
        assert checked > 0

    def test_r27_next_action_present(self, paused_flow, completed_flow):
        assert "resume" in paused_flow["snap2"]["report"]["next_recommended_action"]
        assert paused_flow["campaign_id"] in \
            paused_flow["snap2"]["report"]["next_recommended_action"]
        assert "complete" in completed_flow["report"]["next_recommended_action"]

    def test_r28_completed_campaign_not_resumable(self, completed_flow):
        report = completed_flow["report"]
        assert report["current_state"] == smod.COMPLETE
        assert report["resumable"] is False
        assert report["incomplete"] is False
        assert report["remaining_total"] == 0
        # rerun of a COMPLETE campaign stays an idempotent no-op
        again = completed_flow["controller"].run()
        assert again["status"] == ctl.RUN_ALREADY_COMPLETE


# =========================================================================== #
# Safety (S29-S31)
# =========================================================================== #
class TestSafety:
    def test_s29_existing_pilot_not_modified(
        self, guard_snapshots, paused_flow, completed_flow, finalized_flow
    ):
        if guard_snapshots["pilot"] is None:
            pytest.skip("immutable pilot campaign not present on this machine")
        assert _snapshot_dir(PILOT_DIR) == guard_snapshots["pilot"]

    def test_s30_paper_trader_not_written(
        self, guard_snapshots, paused_flow, completed_flow, finalized_flow
    ):
        assert _snapshot_dir(DESK_DIR) == guard_snapshots["desk"]

    def test_s31_no_orders_broker_automation_promotion(
        self, paused_flow, completed_flow, finalized_flow
    ):
        for report in (paused_flow["snap2"]["report"], completed_flow["report"],
                       finalized_flow["report"]):
            safety = report["safety"]
            assert safety["creates_orders"] is False
            assert safety["broker_execution"] is False
            assert safety["automation_of_trading"] is False
            assert safety["operational_model_changed"] is False
            assert safety["operational_holdings_changed"] is False
            assert safety["promotion_requires_human_approval"] is True
            for ch in report["challengers"]:
                assert ch["stage"] == "SHADOW_ELIGIBLE"
                assert ch["human_approval_required"] is True
                assert ch["operational_model_changed"] is False
        status = paused_flow["snap2"]["status"]
        assert status["safety"]["research_only"] is True
