"""Structured agent memory — never a chat transcript.

Persists typed records (campaign, hypothesis, experiment, result) through the
artifact store. Hypothesis and experiment ledgers are append-only JSONL;
"updates" append a fresh full snapshot row and readers take the latest row per
id, so history is never rewritten.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from . import SAFETY_CONTRACT, SCHEMA_VERSION
from .artifact_store import ArtifactStore, _now_iso, content_hash

HYPOTHESIS_STATUSES = (
    "QUEUED",
    "PLANNED",
    "TESTED",
    "SUPPORTED",
    "FALSIFIED",
    "INCONCLUSIVE",
    "ABANDONED",
)

EXPERIMENT_STATUSES = (
    "PLANNED",
    "RUNNING",
    "COMPLETE",
    "FAILED",
    "REJECTED_UNSUPPORTED",
    "SKIPPED_BUDGET",
)


def build_campaign_record(
    *,
    campaign_id: str,
    objective: str,
    baseline_model: str,
    baseline_book: str,
    data_root: str,
    artifact_root: str,
    code_commit: str,
    budgets: Dict[str, Any],
    stop_conditions: Dict[str, Any],
    config_hash: str,
    current_state: str,
) -> Dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "record_type": "CAMPAIGN",
        "campaign_id": campaign_id,
        "objective": objective,
        "baseline_model": baseline_model,
        "baseline_book": baseline_book,
        "created_at": _now_iso(),
        "current_state": current_state,
        "resume_state": None,
        "data_root": data_root,
        "artifact_root": artifact_root,
        "code_commit": code_commit,
        "budgets": budgets,
        "stop_conditions": stop_conditions,
        "config_hash": config_hash,
        "safety": dict(SAFETY_CONTRACT),
    }


def build_hypothesis_record(
    *,
    hypothesis_id: str,
    diagnosis: str,
    proposed_change: Dict[str, Any],
    expected_benefit: str,
    falsification_condition: str,
    priority: int,
    parent_hypothesis: Optional[str] = None,
    status: str = "QUEUED",
) -> Dict[str, Any]:
    if status not in HYPOTHESIS_STATUSES:
        raise ValueError("unknown hypothesis status: %s" % status)
    return {
        "schema_version": SCHEMA_VERSION,
        "record_type": "HYPOTHESIS",
        "hypothesis_id": hypothesis_id,
        "diagnosis": diagnosis,
        "proposed_change": proposed_change,
        "expected_benefit": expected_benefit,
        "falsification_condition": falsification_condition,
        "priority": int(priority),
        "parent_hypothesis": parent_hypothesis,
        "status": status,
        "recorded_at": _now_iso(),
    }


def build_experiment_record(
    *,
    experiment_id: str,
    hypothesis_id: str,
    baseline_model: str,
    candidate_spec_hash: str,
    requested_tools: List[str],
    status: str,
    attempt: int,
    random_seed: int,
    started_at: Optional[str] = None,
    completed_at: Optional[str] = None,
    input_provenance: Optional[Dict[str, Any]] = None,
    artifact_paths: Optional[Dict[str, str]] = None,
    failure: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    if status not in EXPERIMENT_STATUSES:
        raise ValueError("unknown experiment status: %s" % status)
    return {
        "schema_version": SCHEMA_VERSION,
        "record_type": "EXPERIMENT",
        "experiment_id": experiment_id,
        "hypothesis_id": hypothesis_id,
        "baseline_model": baseline_model,
        "candidate_spec_hash": candidate_spec_hash,
        "requested_tools": list(requested_tools),
        "status": status,
        "attempt": int(attempt),
        "random_seed": int(random_seed),
        "started_at": started_at,
        "completed_at": completed_at,
        "input_provenance": input_provenance or {},
        "artifact_paths": artifact_paths or {},
        "failure": failure,
        "recorded_at": _now_iso(),
    }


def build_result_record(
    *,
    experiment_id: str,
    metrics: Dict[str, Any],
    coverage: Dict[str, Any],
    warnings: List[str],
    failures: List[Dict[str, Any]],
    baseline_comparison: Dict[str, Any],
    gate_results: Dict[str, Any],
    decision: str,
    follow_up: Optional[str] = None,
) -> Dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "record_type": "RESULT",
        "experiment_id": experiment_id,
        "metrics": metrics,
        "coverage": coverage,
        "warnings": list(warnings),
        "failures": list(failures),
        "baseline_comparison": baseline_comparison,
        "gate_results": gate_results,
        "decision": decision,
        "follow_up_recommendation": follow_up,
        "recorded_at": _now_iso(),
    }


class CampaignMemory:
    """Latest-row-wins view over the append-only campaign ledgers."""

    def __init__(self, store: ArtifactStore, campaign_id: str):
        self.store = store
        self.campaign_id = campaign_id

    # ---- hypotheses -------------------------------------------------------
    def record_hypothesis(self, record: Dict[str, Any]) -> None:
        self.store.append_hypothesis(self.campaign_id, record)

    def update_hypothesis_status(self, hypothesis_id: str, status: str) -> None:
        latest = self.hypotheses().get(hypothesis_id)
        if latest is None:
            raise KeyError("unknown hypothesis_id: %s" % hypothesis_id)
        row = dict(latest)
        if status not in HYPOTHESIS_STATUSES:
            raise ValueError("unknown hypothesis status: %s" % status)
        row["status"] = status
        row["recorded_at"] = _now_iso()
        self.store.append_hypothesis(self.campaign_id, row)

    def hypotheses(self) -> Dict[str, Dict[str, Any]]:
        latest: Dict[str, Dict[str, Any]] = {}
        for row in self.store.read_hypotheses(self.campaign_id):
            latest[row["hypothesis_id"]] = row
        return latest

    # ---- experiments ------------------------------------------------------
    def record_experiment(self, record: Dict[str, Any]) -> None:
        self.store.append_experiment_index(self.campaign_id, record)

    def experiments(self) -> Dict[str, Dict[str, Any]]:
        latest: Dict[str, Dict[str, Any]] = {}
        for row in self.store.read_experiment_index(self.campaign_id):
            latest[row["experiment_id"]] = row
        return latest

    def completed_experiment_ids(self) -> List[str]:
        return sorted(
            eid
            for eid, row in self.experiments().items()
            if row.get("status") == "COMPLETE"
        )

    def spec_hash_seen(self, spec_hash: str) -> Optional[str]:
        """Return the experiment_id already carrying this spec hash, if any."""
        for eid, row in sorted(self.experiments().items()):
            if row.get("candidate_spec_hash") == spec_hash:
                return eid
        return None


def spec_hash(spec: Dict[str, Any]) -> str:
    """Deterministic identity of an experiment specification.

    Excludes the experiment_id and planner bookkeeping so two ids wrapping the
    same candidate configuration are recognized as duplicates.
    """
    core = {
        k: v
        for k, v in spec.items()
        if k not in ("experiment_id", "spec_hash", "plan_rank")
    }
    return content_hash(core)


__all__ = [
    "CampaignMemory",
    "EXPERIMENT_STATUSES",
    "HYPOTHESIS_STATUSES",
    "build_campaign_record",
    "build_experiment_record",
    "build_hypothesis_record",
    "build_result_record",
    "spec_hash",
]
