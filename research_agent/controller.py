"""Campaign controller: drives the state machine end to end.

Crash-safe by construction: every step is persisted before/after execution,
experiment artifacts are immutable, already-complete experiments are skipped
on resume, the campaign lock guarantees single-flight execution, and a
COMPLETE or FAILED campaign is never rerun.
"""

from __future__ import annotations

import datetime as _dt
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import SAFETY_CONTRACT, SCHEMA_VERSION
from . import evaluator as ev
from . import state_machine as smod
from .artifact_store import (
    ArtifactStore,
    CampaignLockedError,
    _now_iso,
    read_json,
    write_json_atomic,
)
from .challenger_registry import (
    ChallengerBudgetError,
    ChallengerRegistry,
    build_challenger_record,
)
from .memory import (
    CampaignMemory,
    build_campaign_record,
    build_experiment_record,
    spec_hash,
)
from .planner import BoundedDeterministicPlanner
from .reporting import write_report
from .schemas import validate_campaign_config
from .tool_adapters import ToolContext, build_registry

OPERATOR_REQUEST_FILE = "operator_request.json"

RUN_OK = "RUN_OK"
RUN_PAUSED = "RUN_PAUSED"
RUN_BLOCKED = "RUN_BLOCKED"
RUN_FAILED = "RUN_FAILED"
RUN_ALREADY_COMPLETE = "RUN_ALREADY_COMPLETE"
RUN_ALREADY_FAILED = "RUN_ALREADY_FAILED"
RUN_LOCKED = "RUN_LOCKED"

DEFAULT_ARTIFACT_ROOT = r"D:\Stock_Prediction_app_data\research_agent"

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read_git_commit(repo_root: str = _REPO_ROOT) -> str:
    """Resolve HEAD without any shell/subprocess (plain file reads)."""
    try:
        head = Path(repo_root, ".git", "HEAD").read_text(encoding="utf-8").strip()
        if head.startswith("ref: "):
            ref = head[5:].strip()
            ref_path = Path(repo_root, ".git", *ref.split("/"))
            if ref_path.exists():
                return ref_path.read_text(encoding="utf-8").strip()
            packed = Path(repo_root, ".git", "packed-refs")
            if packed.exists():
                for line in packed.read_text(encoding="utf-8").splitlines():
                    parts = line.strip().split(" ", 1)
                    if len(parts) == 2 and parts[1] == ref:
                        return parts[0]
            return "unknown"
        return head
    except OSError:
        return "unknown"


def create_campaign(
    config: Dict[str, Any],
    *,
    artifact_root: Optional[str] = None,
    today: Optional[_dt.date] = None,
    now_label: Optional[str] = None,
) -> Dict[str, Any]:
    verdict = validate_campaign_config(config, today=today)
    if not verdict["accepted"]:
        return {"created": False, "reason": "INVALID_CONFIG", "violations": verdict["violations"]}
    root = artifact_root or config.get("artifact_root") or DEFAULT_ARTIFACT_ROOT
    store = ArtifactStore(root)
    stamp = now_label or _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    campaign_id = "%s_%s" % (config["name"], stamp)
    n = 1
    while campaign_id in store.list_campaigns():
        n += 1
        campaign_id = "%s_%s_%d" % (config["name"], stamp, n)
    store.ensure_campaign_layout(campaign_id)
    manifest = build_campaign_record(
        campaign_id=campaign_id,
        objective=config["objective"],
        baseline_model=config["baseline"]["model_id"],
        baseline_book=config["baseline"]["book_id"],
        data_root=config.get("data", {}).get("data_root", ""),
        artifact_root=str(root),
        code_commit=read_git_commit(),
        budgets=config["budgets"],
        stop_conditions=config["stop_conditions"],
        config_hash=verdict["config_hash"],
        current_state=smod.NEW_CAMPAIGN,
    )
    manifest["config"] = config
    store.write_manifest(campaign_id, manifest)
    store.append_event(campaign_id, "CAMPAIGN_CREATED", {"config_hash": verdict["config_hash"]})
    store.write_status(
        campaign_id,
        {"campaign_id": campaign_id, "state": smod.NEW_CAMPAIGN, "safety": dict(SAFETY_CONTRACT)},
    )
    return {"created": True, "campaign_id": campaign_id, "artifact_root": str(root)}


class CampaignController:
    def __init__(
        self,
        campaign_id: str,
        *,
        artifact_root: Optional[str] = None,
        today: Optional[_dt.date] = None,
        inputs: Optional[Dict[str, Any]] = None,
        reference_rows: Optional[List[dict]] = None,
        close_frame: Optional[Any] = None,
    ):
        self.store = ArtifactStore(artifact_root or DEFAULT_ARTIFACT_ROOT)
        self.campaign_id = campaign_id
        self.sm = smod.CampaignStateMachine(self.store, campaign_id)
        self.memory = CampaignMemory(self.store, campaign_id)
        self.planner = BoundedDeterministicPlanner()
        self.registry = build_registry()
        self.today = today
        self._seam_inputs = inputs
        self._seam_reference = reference_rows
        self._seam_close_frame = close_frame
        self._ctx: Optional[ToolContext] = None
        self._max_experiments_override: Optional[int] = None

    # ---- helpers ----------------------------------------------------------
    @property
    def manifest(self) -> Dict[str, Any]:
        return self.store.read_manifest(self.campaign_id)

    @property
    def config(self) -> Dict[str, Any]:
        return self.manifest["config"]

    def ctx(self) -> ToolContext:
        if self._ctx is None:
            challengers = ChallengerRegistry(
                self.store,
                self.campaign_id,
                self.config["budgets"]["max_registered_challengers"],
            )
            self._ctx = ToolContext(
                config=self.config,
                today=self.today,
                inputs=self._seam_inputs,
                reference_rows=self._seam_reference,
                close_frame=self._seam_close_frame,
                challenger_registry=challengers,
                report_writer=lambda: write_report(self.store, self.campaign_id),
            )
            baseline = self.manifest.get("baseline") or {}
            if baseline.get("metrics"):
                self._ctx.baseline_metrics = baseline["metrics"]
        return self._ctx

    def _tool(self, name: str, inputs: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        cfg = self.config
        env = self.registry.run(
            name,
            inputs or {},
            context=self.ctx(),
            data_cutoff=cfg["data"]["data_cutoff"],
            seed=cfg.get("random_seed"),
        )
        self.store.append_event(
            self.campaign_id,
            "TOOL_RUN",
            {
                "tool": name,
                "status": env["status"],
                "duration_seconds": env["duration_seconds"],
                "inputs_hash": env["inputs_hash"],
                "failure": env.get("failure"),
            },
        )
        return env

    def _heartbeat(self, **extra) -> None:
        doc = {
            "campaign_id": self.campaign_id,
            "state": self.sm.current_state(),
            "safety": dict(SAFETY_CONTRACT),
            "heartbeat": True,
        }
        doc.update(extra)
        self.store.write_status(self.campaign_id, doc)

    def _operator_request(self) -> Optional[str]:
        path = self.store.campaign_dir(self.campaign_id) / OPERATOR_REQUEST_FILE
        if not path.exists():
            return None
        try:
            return (read_json(path) or {}).get("request")
        except Exception:
            return None

    def request_pause(self) -> Dict[str, Any]:
        path = self.store.campaign_dir(self.campaign_id) / OPERATOR_REQUEST_FILE
        write_json_atomic(path, {"request": "PAUSE", "requested_at": _now_iso()})
        return {"requested": "PAUSE", "note": "campaign pauses gracefully after the current experiment"}

    def clear_operator_request(self) -> None:
        path = self.store.campaign_dir(self.campaign_id) / OPERATOR_REQUEST_FILE
        try:
            path.unlink()
        except FileNotFoundError:
            pass

    def _update_manifest(self, **kv) -> None:
        manifest = self.manifest
        manifest.update(kv)
        self.store.write_manifest(self.campaign_id, manifest)

    # ---- status / plan preview -------------------------------------------
    def status(self) -> Dict[str, Any]:
        manifest = self.manifest
        experiments = self.memory.experiments()
        by_status: Dict[str, int] = {}
        for row in experiments.values():
            by_status[row.get("status", "?")] = by_status.get(row.get("status", "?"), 0) + 1
        challengers = ChallengerRegistry(
            self.store, self.campaign_id, manifest["budgets"]["max_registered_challengers"]
        ).latest_by_candidate()
        return {
            "campaign_id": self.campaign_id,
            "current_state": manifest.get("current_state"),
            "resume_state": manifest.get("resume_state"),
            "objective": manifest.get("objective"),
            "baseline_model": manifest.get("baseline_model"),
            "created_at": manifest.get("created_at"),
            "last_transition_at": manifest.get("last_transition_at"),
            "budgets": manifest.get("budgets"),
            "experiments_by_status": by_status,
            "experiments_total": len(experiments),
            "challengers_registered": sorted(challengers),
            "baseline_reproduced": (manifest.get("baseline") or {}).get("baseline_reproduced"),
            "operator_request": self._operator_request(),
            "heartbeat": self.store.read_status(self.campaign_id),
            "safety": dict(SAFETY_CONTRACT),
            "schema_version": SCHEMA_VERSION,
        }

    def plan_preview(self, max_experiments: Optional[int] = None) -> Dict[str, Any]:
        """Deterministic dry planning: NO ledger writes, NO execution."""
        hypotheses = self.planner.generate_hypotheses(self.config)
        plan = self.planner.plan_experiments(
            self.config,
            seen_spec_hashes=[
                r.get("candidate_spec_hash") for r in self.memory.experiments().values()
            ],
            max_experiments=max_experiments or self._max_experiments_override,
            today=self.today,
        )
        return {
            "dry_run": True,
            "executed": False,
            "hypotheses": [h["hypothesis_id"] for h in hypotheses],
            "planned": [
                {
                    "experiment_id": s["experiment_id"],
                    "plan_rank": s["plan_rank"],
                    "hypothesis_id": s.get("hypothesis_id"),
                    "model_params": s["model_params"],
                    "portfolio_params": s["portfolio_params"],
                    "compute_estimate": s["compute_estimate"],
                }
                for s in plan["planned"]
            ],
            "rejected": plan["rejected"],
            "deduplicated": plan["deduplicated"],
            "budget": plan["budget"],
            "safety": dict(SAFETY_CONTRACT),
        }

    # ---- main loop --------------------------------------------------------
    def run(
        self,
        *,
        max_experiments: Optional[int] = None,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        self._max_experiments_override = max_experiments
        if dry_run:
            return self.plan_preview(max_experiments)

        state = self.sm.current_state()
        if state == smod.COMPLETE:
            return {
                "status": RUN_ALREADY_COMPLETE,
                "campaign_id": self.campaign_id,
                "note": "COMPLETE campaigns are never rerun",
            }
        if state == smod.FAILED:
            return {
                "status": RUN_ALREADY_FAILED,
                "campaign_id": self.campaign_id,
                "note": "FAILED campaigns preserve their evidence and are never rerun",
            }

        lock = self.store.lock(self.campaign_id, owner="research_agent.controller")
        try:
            lock.acquire()
        except CampaignLockedError as exc:
            return {"status": RUN_LOCKED, "campaign_id": self.campaign_id, "detail": str(exc)}

        try:
            result = self._run_locked(lock)
        finally:
            lock.release()
        return result

    def _run_locked(self, lock) -> Dict[str, Any]:
        handlers = {
            smod.NEW_CAMPAIGN: self._do_new,
            smod.DATA_AUDIT: self._do_data_audit,
            smod.BASELINE_VALIDATION: self._do_baseline,
            smod.HYPOTHESIS_GENERATION: self._do_hypotheses,
            smod.EXPERIMENT_PLANNING: self._do_planning,
            smod.EXPERIMENT_RUNNING: self._do_running,
            smod.CANDIDATE_EVALUATION: self._do_evaluation,
            smod.ROBUSTNESS_TESTING: self._do_robustness,
            smod.CHALLENGER_REGISTRATION: self._do_registration,
            smod.REPORTING: self._do_reporting,
        }
        while True:
            state = self.sm.current_state()
            if state == smod.COMPLETE:
                return {"status": RUN_OK, "campaign_id": self.campaign_id, "final_state": state}
            if state == smod.FAILED:
                return {"status": RUN_FAILED, "campaign_id": self.campaign_id, "final_state": state}
            if state in (smod.PAUSED, smod.BLOCKED):
                resume_to = self.sm.resume_state()
                if not resume_to:
                    return {
                        "status": RUN_BLOCKED,
                        "campaign_id": self.campaign_id,
                        "final_state": state,
                        "note": "no recorded resume state",
                    }
                self.sm.transition(resume_to, reason="operator resume")
                continue
            lock.heartbeat()
            self._heartbeat(stage=state)
            outcome = handlers[state]()
            if outcome is not None:  # a handler asked to stop the loop
                return outcome

    # ---- state handlers ---------------------------------------------------
    def _do_new(self) -> Optional[Dict[str, Any]]:
        self.sm.transition(smod.DATA_AUDIT, reason="campaign start")
        return None

    def _do_data_audit(self) -> Optional[Dict[str, Any]]:
        audit = self._tool("audit_data_coverage")
        feats = self._tool("inspect_feature_availability")
        pit = self._tool(
            "validate_point_in_time_integrity",
            {"seed": self.config.get("random_seed", 29)},
        )
        if audit["status"] != "OK" or feats["status"] != "OK":
            self.sm.transition(
                smod.BLOCKED,
                reason="required owned datasets unavailable",
                detail={"audit": audit.get("failure"), "features": feats.get("failure")},
            )
            return {
                "status": RUN_BLOCKED,
                "campaign_id": self.campaign_id,
                "final_state": smod.BLOCKED,
                "note": "data audit could not run; fix inputs then resume",
            }
        self._update_manifest(
            data_audit={
                "coverage": audit["output"],
                "features": feats["output"],
                "pit": (pit.get("output") or {"pit_integrity_ok": False, "failure": pit.get("failure")}),
            }
        )
        pit_ok = bool((pit.get("output") or {}).get("pit_integrity_ok"))
        if not pit_ok:
            self.sm.transition(
                smod.FAILED,
                reason="point-in-time integrity failure (fail-fast)",
                detail={"pit": (pit.get("output") or {}).get("failed_checks", pit.get("failure"))},
            )
            return {
                "status": RUN_FAILED,
                "campaign_id": self.campaign_id,
                "final_state": smod.FAILED,
                "note": "PIT/leakage failure blocks the whole campaign; evidence preserved",
            }
        n_months = (audit["output"] or {}).get("n_fund_era_months") or 0
        if n_months < 36:
            self.sm.transition(
                smod.BLOCKED,
                reason="insufficient usable sample (<36 fund-era months)",
                detail={"n_fund_era_months": n_months},
            )
            return {
                "status": RUN_BLOCKED,
                "campaign_id": self.campaign_id,
                "final_state": smod.BLOCKED,
            }
        self.sm.transition(smod.BASELINE_VALIDATION, reason="data audit passed")
        return None

    def _do_baseline(self) -> Optional[Dict[str, Any]]:
        env = self._tool("run_baseline_validation")
        if env["status"] != "OK":
            self.sm.transition(
                smod.FAILED,
                reason="baseline validation tool failed (fail-fast)",
                detail={"failure": env.get("failure")},
            )
            return {"status": RUN_FAILED, "campaign_id": self.campaign_id, "final_state": smod.FAILED}
        out = env["output"]
        baseline_doc = {
            k: out.get(k)
            for k in (
                "baseline_reproduced",
                "deterministic",
                "invariant_failures",
                "reference_available",
                "reference_reproduced",
                "reference_months_compared",
                "reference_mismatch_count",
                "reference_mismatches",
                "months_only_in_reference",
                "months_only_in_ours",
                "run_hash",
                "n_periods",
                "first_month",
                "last_month",
            )
        }
        baseline_doc["metrics"] = out.get("baseline_metrics")
        baseline_doc["params"] = out.get("baseline_params")
        self._update_manifest(baseline=baseline_doc)
        if not out.get("baseline_reproduced"):
            self.sm.transition(
                smod.FAILED,
                reason="baseline non-reproducibility (fail-fast)",
                detail={
                    "deterministic": out.get("deterministic"),
                    "reference_reproduced": out.get("reference_reproduced"),
                    "reference_mismatch_count": out.get("reference_mismatch_count"),
                    "invariant_failures": out.get("invariant_failures"),
                },
            )
            return {
                "status": RUN_FAILED,
                "campaign_id": self.campaign_id,
                "final_state": smod.FAILED,
                "note": "the baseline could not be reproduced; evidence preserved",
            }
        self.sm.transition(smod.HYPOTHESIS_GENERATION, reason="baseline reproduced")
        return None

    def _do_hypotheses(self) -> Optional[Dict[str, Any]]:
        existing = self.memory.hypotheses()
        for record in self.planner.generate_hypotheses(self.config):
            if record["hypothesis_id"] not in existing:
                self.memory.record_hypothesis(record)
        self.sm.transition(smod.EXPERIMENT_PLANNING, reason="hypothesis queue ready")
        return None

    def _do_planning(self) -> Optional[Dict[str, Any]]:
        existing = self.memory.experiments()
        plan = self.planner.plan_experiments(
            self.config,
            seen_spec_hashes=[r.get("candidate_spec_hash") for r in existing.values()],
            max_experiments=self._max_experiments_override,
            today=self.today,
        )
        for rej in plan["rejected"]:
            self.store.append_event(self.campaign_id, "EXPERIMENT_REJECTED", rej)
        for spec in plan["planned"]:
            if spec["experiment_id"] in existing:
                continue
            record = build_experiment_record(
                experiment_id=spec["experiment_id"],
                hypothesis_id=spec.get("hypothesis_id", ""),
                baseline_model=spec["baseline_model"],
                candidate_spec_hash=spec["spec_hash"],
                requested_tools=["run_parameter_experiment"],
                status="PLANNED",
                attempt=0,
                random_seed=spec["random_seed"],
            )
            record["spec"] = spec
            record["plan_rank"] = spec["plan_rank"]
            self.memory.record_experiment(record)
        self.store.append_event(
            self.campaign_id,
            "PLANNING_COMPLETE",
            {"budget": plan["budget"], "n_rejected": len(plan["rejected"]),
             "n_deduplicated": len(plan["deduplicated"])},
        )
        pending = [
            r for r in self.memory.experiments().values() if r.get("status") == "PLANNED"
        ]
        self.sm.transition(
            smod.EXPERIMENT_RUNNING if pending else smod.REPORTING,
            reason="plan persisted (%d pending)" % len(pending),
        )
        return None

    def _do_running(self) -> Optional[Dict[str, Any]]:
        cfg = self.config
        max_retry = int(cfg["budgets"].get("max_retry_per_experiment", 1))
        timeout_s = float(cfg["budgets"].get("experiment_timeout_seconds", 900))
        budget = int(cfg["budgets"]["max_primary_experiments"])
        if self._max_experiments_override:
            budget = min(budget, int(self._max_experiments_override))

        rows = sorted(
            self.memory.experiments().values(),
            key=lambda r: (r.get("plan_rank") or 10 ** 9, r["experiment_id"]),
        )
        completed = sum(1 for r in rows if r.get("status") == "COMPLETE")
        for row in rows:
            if row.get("status") not in ("PLANNED", "RUNNING"):
                continue
            if completed >= budget:
                skipped = dict(row)
                skipped["status"] = "SKIPPED_BUDGET"
                self.memory.record_experiment(skipped)
                continue
            if self._operator_request() in ("PAUSE", "STOP"):
                self.sm.transition(
                    smod.PAUSED,
                    reason="operator requested %s" % self._operator_request(),
                )
                self.clear_operator_request()
                return {
                    "status": RUN_PAUSED,
                    "campaign_id": self.campaign_id,
                    "final_state": smod.PAUSED,
                    "note": "paused gracefully; resume with the resume command",
                }

            spec = row["spec"]
            attempt = int(row.get("attempt", 0))
            while attempt <= max_retry:
                attempt += 1
                running = dict(row)
                running.update(status="RUNNING", attempt=attempt, started_at=_now_iso())
                self.memory.record_experiment(running)
                self._heartbeat(stage=smod.EXPERIMENT_RUNNING,
                                experiment=row["experiment_id"], attempt=attempt,
                                completed=completed, budget=budget)
                env = self._tool("run_parameter_experiment", {"spec": spec})
                if env["status"] == "OK":
                    out = env["output"]
                    warnings = []
                    if env["duration_seconds"] > timeout_s:
                        warnings.append(
                            "TIMEOUT_SOFT: duration %.1fs exceeded configured %.0fs"
                            % (env["duration_seconds"], timeout_s)
                        )
                    self.store.write_experiment_artifact(
                        self.campaign_id, spec["experiment_id"], "config.json", spec
                    )
                    self.store.write_experiment_artifact(
                        self.campaign_id,
                        spec["experiment_id"],
                        "provenance.json",
                        {
                            "input_provenance": self.ctx().inputs()["provenance"],
                            "data_cutoff": spec["data_cutoff"],
                            "random_seed": spec["random_seed"],
                            "spec_hash": spec["spec_hash"],
                            "code_commit": self.manifest.get("code_commit"),
                            "tool_duration_seconds": env["duration_seconds"],
                        },
                    )
                    self.store.write_experiment_artifact(
                        self.campaign_id,
                        spec["experiment_id"],
                        "metrics.json",
                        {"metrics": out["metrics"], "warnings": warnings,
                         "params": out["params"], "n_periods": out["n_periods"]},
                    )
                    done = dict(running)
                    done.update(
                        status="COMPLETE",
                        completed_at=_now_iso(),
                        input_provenance={"sha256": self.ctx().inputs()["provenance"]["sha256"]},
                        artifact_paths={
                            "dir": str(self.store.experiment_dir(self.campaign_id, spec["experiment_id"]))
                        },
                        failure=None,
                    )
                    self.memory.record_experiment(done)
                    completed += 1
                    break
                if env["status"] in ("REJECTED_INVALID", "REJECTED_UNSUPPORTED"):
                    rej = dict(running)
                    rej.update(status="REJECTED_UNSUPPORTED", failure=env.get("failure")
                               or {"type": env["status"], "detail": (env.get("output") or {}).get("violations")})
                    self.memory.record_experiment(rej)
                    break
                failure = {
                    "classification": "TOOL_ERROR",
                    "attempt": attempt,
                    **(env.get("failure") or {}),
                }
                failed = dict(running)
                failed.update(status="FAILED", failure=failure, completed_at=_now_iso())
                self.memory.record_experiment(failed)
                if attempt > max_retry:
                    break
        self.sm.transition(smod.CANDIDATE_EVALUATION, reason="experiment queue drained")
        return None

    def _do_evaluation(self) -> Optional[Dict[str, Any]]:
        ctx = self.ctx()
        if ctx.baseline_metrics is None:
            self.sm.transition(smod.BLOCKED, reason="baseline metrics unavailable")
            return {"status": RUN_BLOCKED, "campaign_id": self.campaign_id,
                    "final_state": smod.BLOCKED}
        thresholds = ctx.thresholds()
        survivors: List[Dict[str, Any]] = []
        for eid, row in sorted(self.memory.experiments().items()):
            if row.get("status") != "COMPLETE":
                continue
            existing_decision = self.store.read_experiment_artifact(
                self.campaign_id, eid, "decision.json"
            )
            if existing_decision and existing_decision.get("stage") == "primary":
                if existing_decision.get("decision") == ev.RETAIN_FOR_ROBUSTNESS:
                    survivors.append(
                        {"experiment_id": eid,
                         "score": (existing_decision.get("score") or {}).get("final_score", 0.0)}
                    )
                continue
            metrics_doc = self.store.read_experiment_artifact(self.campaign_id, eid, "metrics.json")
            metrics = dict(metrics_doc["metrics"])
            metrics["pit_integrity_ok"] = True  # campaign-level PIT gate passed
            gates = ev.evaluate_gates(metrics, ctx.baseline_metrics, thresholds)
            decision = ev.decide_candidate(gates, stage="primary")
            score = ev.score_candidate(metrics, ctx.baseline_metrics, gates)
            self.store.write_experiment_artifact(
                self.campaign_id, eid, "gate_results.json", gates
            )
            self.store.write_experiment_artifact(
                self.campaign_id,
                eid,
                "decision.json",
                {"stage": "primary", "decision": decision["decision"],
                 "reasons": decision["reasons"], "gate_overrides": decision["gate_overrides"],
                 "score": score},
            )
            self.store.append_event(
                self.campaign_id,
                "CANDIDATE_EVALUATED",
                {"experiment_id": eid, "decision": decision["decision"],
                 "final_score": score["final_score"]},
            )
            if decision["decision"] == ev.RETAIN_FOR_ROBUSTNESS:
                survivors.append({"experiment_id": eid, "score": score["final_score"]})

        max_rb = int(self.config["budgets"]["max_robustness_candidates"])
        survivors.sort(key=lambda s: (-s["score"], s["experiment_id"]))
        queue = [s["experiment_id"] for s in survivors[:max_rb]]
        self._update_manifest(robustness_queue=queue)
        self.store.append_event(
            self.campaign_id,
            "ROBUSTNESS_QUEUE_SELECTED",
            {"n_survivors": len(survivors), "queued": queue, "budget": max_rb},
        )
        self.sm.transition(
            smod.ROBUSTNESS_TESTING if queue else smod.REPORTING,
            reason="%d survivor(s) queued for robustness" % len(queue),
        )
        return None

    def _do_robustness(self) -> Optional[Dict[str, Any]]:
        ctx = self.ctx()
        thresholds = ctx.thresholds()
        shadow: List[str] = []
        for eid in self.manifest.get("robustness_queue", []):
            row = self.memory.experiments().get(eid)
            if not row:
                continue
            spec = row["spec"]
            results: Dict[str, Any] = {}
            for tool_name in (
                "run_cost_sensitivity",
                "run_turnover_analysis",
                "run_sector_analysis",
                "run_regime_analysis",
                "run_subperiod_stability",
                "run_universe_sensitivity",
            ):
                short = tool_name[4:]
                if short not in spec.get("robustness_tests", []):
                    continue
                env = self._tool(tool_name, {"spec": spec})
                results[short] = env.get("output") if env["status"] == "OK" else {
                    "failed": True, "failure": env.get("failure")}
            metrics_doc = self.store.read_experiment_artifact(self.campaign_id, eid, "metrics.json")
            metrics = dict(metrics_doc["metrics"])
            metrics["pit_integrity_ok"] = True
            gates = ev.evaluate_gates(metrics, ctx.baseline_metrics, thresholds)
            decision = ev.decide_candidate(gates, stage="robustness")
            score = ev.score_candidate(metrics, ctx.baseline_metrics, gates)
            weaknesses = [
                "gate not passed: %s" % g["gate"]
                for g in gates["gates"]
                if g["passed"] is not True
            ]
            uni = results.get("universe_sensitivity") or {}
            if uni and uni.get("all_views_positive") is False:
                weaknesses.append("excess not positive under all universe views")
            report_md = _experiment_report_md(eid, spec, metrics, gates, decision, score, results)
            existing_md = self.store.read_experiment_artifact(self.campaign_id, eid, "report.md")
            if existing_md is None:
                self.store.write_experiment_artifact(self.campaign_id, eid, "report.md", report_md)
            self.store.append_event(
                self.campaign_id,
                "ROBUSTNESS_EVALUATED",
                {"experiment_id": eid, "decision": decision["decision"],
                 "weaknesses": weaknesses[:8]},
            )
            robust_doc = {
                "stage": "robustness",
                "decision": decision["decision"],
                "reasons": decision["reasons"],
                "score": score,
                "weaknesses": weaknesses,
                "robustness_results": results,
            }
            self._merge_manifest_robustness(eid, robust_doc)
            if decision["decision"] == ev.SHADOW_ELIGIBLE:
                shadow.append(eid)
        self._update_manifest(shadow_eligible=shadow)
        self.sm.transition(
            smod.CHALLENGER_REGISTRATION if shadow else smod.REPORTING,
            reason="%d shadow-eligible candidate(s)" % len(shadow),
        )
        return None

    def _merge_manifest_robustness(self, eid: str, doc: Dict[str, Any]) -> None:
        manifest = self.manifest
        rb = manifest.get("robustness_results") or {}
        rb[eid] = {"decision": doc["decision"], "weaknesses": doc["weaknesses"],
                   "final_score": doc["score"]["final_score"]}
        manifest["robustness_results"] = rb
        self.store.write_manifest(self.campaign_id, manifest)

    def _do_registration(self) -> Optional[Dict[str, Any]]:
        cfg = self.config
        registered = []
        for eid in self.manifest.get("shadow_eligible", []):
            row = self.memory.experiments().get(eid)
            if not row:
                continue
            spec = row["spec"]
            metrics_doc = self.store.read_experiment_artifact(self.campaign_id, eid, "metrics.json")
            gates = self.store.read_experiment_artifact(self.campaign_id, eid, "gate_results.json")
            rb = (self.manifest.get("robustness_results") or {}).get(eid) or {}
            record = build_challenger_record(
                candidate_id=spec["candidate_id"],
                stage="SHADOW_ELIGIBLE",
                candidate_config={
                    "model_params": spec["model_params"],
                    "portfolio_params": spec["portfolio_params"],
                    "universe": spec["universe"],
                    "cost_bps_per_side": spec["cost_bps_per_side"],
                    "baseline_model": spec["baseline_model"],
                },
                dataset_cutoff=spec["data_cutoff"],
                code_commit=self.manifest.get("code_commit", "unknown"),
                evidence_summary={
                    "net_spy_excess_ann": metrics_doc["metrics"].get("net_spy_excess_ann"),
                    "rank_ic_t": metrics_doc["metrics"].get("rank_ic_t"),
                    "turnover_monthly_oneside": metrics_doc["metrics"].get("turnover_monthly_oneside"),
                    "max_drawdown": metrics_doc["metrics"].get("max_drawdown"),
                    "months": metrics_doc["metrics"].get("months"),
                    "final_score": rb.get("final_score"),
                },
                gate_results={"hard_gate_failures": gates.get("hard_gate_failures", [])},
                known_weaknesses=rb.get("weaknesses", []),
                required_forward_validation_horizon_days=63,
                source_experiment_id=eid,
            )
            env = self._tool("register_shadow_challenger", {"record": record})
            if env["status"] == "OK":
                registered.append(env["output"]["candidate_id"])
            elif (env.get("failure") or {}).get("type") == "ChallengerBudgetError":
                self.store.append_event(
                    self.campaign_id, "CHALLENGER_BUDGET_EXHAUSTED", {"experiment_id": eid}
                )
                break
        self._update_manifest(registered_challengers=registered)
        self.sm.transition(smod.REPORTING, reason="%d challenger(s) registered" % len(registered))
        return None

    def _do_reporting(self) -> Optional[Dict[str, Any]]:
        env = self._tool("generate_campaign_report")
        if env["status"] != "OK":
            self.sm.transition(smod.FAILED, reason="report generation failed",
                               detail={"failure": env.get("failure")})
            return {"status": RUN_FAILED, "campaign_id": self.campaign_id,
                    "final_state": smod.FAILED}
        self.sm.transition(smod.COMPLETE, reason="campaign report written")
        self._heartbeat(stage=smod.COMPLETE)
        return None


def _experiment_report_md(eid, spec, metrics, gates, decision, score, results) -> str:
    lines = [
        "# Experiment %s" % eid,
        "",
        "- decision: **%s** (robustness stage)" % decision["decision"],
        "- final score: %.4f" % score["final_score"],
        "- params: %s / %s" % (spec["model_params"], spec["portfolio_params"]),
        "- net SPY excess (ann): %s" % metrics.get("net_spy_excess_ann"),
        "- rank IC t: %s | turnover: %s | maxDD: %s"
        % (metrics.get("rank_ic_t"), metrics.get("turnover_monthly_oneside"),
           metrics.get("max_drawdown")),
        "",
        "## Gates",
    ]
    for g in gates["gates"]:
        lines.append("- %s: %s (value=%s, threshold=%s%s)" % (
            g["gate"], g["passed"], g["value"], g["threshold"],
            ", provisional" if g["provisional"] else ""))
    lines += ["", "## Robustness results"]
    for name in sorted(results):
        lines.append("- %s: %s" % (name, results[name]))
    lines += ["", "_Research-only. Shadow eligibility never implies operational approval._", ""]
    return "\n".join(lines)


__all__ = [
    "CampaignController",
    "DEFAULT_ARTIFACT_ROOT",
    "OPERATOR_REQUEST_FILE",
    "RUN_ALREADY_COMPLETE",
    "RUN_ALREADY_FAILED",
    "RUN_BLOCKED",
    "RUN_FAILED",
    "RUN_LOCKED",
    "RUN_OK",
    "RUN_PAUSED",
    "create_campaign",
    "read_git_commit",
]
