"""Append-only research challenger registry.

Phase 29A may create only BACKTESTED, ROBUSTNESS_VALIDATED and
SHADOW_ELIGIBLE records. SHADOW_ACTIVE requires a later explicit publication
step, PROMOTION_CANDIDATE requires forward evidence, and OPERATIONAL is never
creatable by this agent. Records are immutable: re-registering an identical
candidate is an idempotent no-op; re-registering a changed configuration
under the same candidate_id is an error.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from . import SCHEMA_VERSION
from .artifact_store import ArtifactStore, _now_iso, append_jsonl, content_hash, read_jsonl

STAGES = (
    "EXPERIMENT",
    "BACKTESTED",
    "ROBUSTNESS_VALIDATED",
    "SHADOW_ELIGIBLE",
    "SHADOW_ACTIVE",
    "PROMOTION_CANDIDATE",
    "OPERATIONAL",
    "RETIRED",
)

PHASE29A_CREATABLE_STAGES = (
    "BACKTESTED",
    "ROBUSTNESS_VALIDATED",
    "SHADOW_ELIGIBLE",
)

FORBIDDEN_STAGES_29A = tuple(s for s in STAGES if s not in PHASE29A_CREATABLE_STAGES)


class ChallengerStageError(RuntimeError):
    pass


class ChallengerImmutableError(RuntimeError):
    pass


class ChallengerBudgetError(RuntimeError):
    pass


def build_challenger_record(
    *,
    candidate_id: str,
    stage: str,
    candidate_config: Dict[str, Any],
    dataset_cutoff: str,
    code_commit: str,
    evidence_summary: Dict[str, Any],
    gate_results: Dict[str, Any],
    known_weaknesses: List[str],
    required_forward_validation_horizon_days: int,
    source_experiment_id: Optional[str] = None,
) -> Dict[str, Any]:
    if stage not in STAGES:
        raise ChallengerStageError("unknown challenger stage: %s" % stage)
    if stage not in PHASE29A_CREATABLE_STAGES:
        raise ChallengerStageError(
            "Phase 29A may not create stage %s (allowed: %s)"
            % (stage, ", ".join(PHASE29A_CREATABLE_STAGES))
        )
    config_hash = content_hash(candidate_config)
    return {
        "schema_version": SCHEMA_VERSION,
        "record_type": "CHALLENGER",
        "candidate_id": candidate_id,
        "stage": stage,
        "candidate_config": candidate_config,
        "candidate_config_hash": config_hash,
        "dataset_cutoff": dataset_cutoff,
        "code_commit": code_commit,
        "evidence_summary": evidence_summary,
        "gate_results": gate_results,
        "known_weaknesses": list(known_weaknesses),
        "required_forward_validation_horizon_days": int(
            required_forward_validation_horizon_days
        ),
        "source_experiment_id": source_experiment_id,
        "approved_for_shadow_only": True,
        "human_approval_required": True,
        "operational_model_changed": False,
        "operationally_promoted_by_agent": False,
        "registered_at": _now_iso(),
    }


class ChallengerRegistry:
    def __init__(
        self,
        store: ArtifactStore,
        campaign_id: str,
        max_registered_challengers: int,
    ):
        self.store = store
        self.campaign_id = campaign_id
        self.max_registered = int(max_registered_challengers)

    @property
    def path(self):
        return self.store.challenger_registry_path(self.campaign_id)

    def records(self) -> List[Dict[str, Any]]:
        return read_jsonl(self.path)

    def latest_by_candidate(self) -> Dict[str, Dict[str, Any]]:
        latest: Dict[str, Dict[str, Any]] = {}
        for row in self.records():
            latest[row["candidate_id"]] = row
        return latest

    def register(self, record: Dict[str, Any]) -> Dict[str, Any]:
        stage = record.get("stage")
        if stage not in PHASE29A_CREATABLE_STAGES:
            raise ChallengerStageError(
                "Phase 29A may not create stage %s" % stage
            )
        if record.get("human_approval_required") is not True:
            raise ChallengerStageError(
                "challenger records must keep human_approval_required=true"
            )
        existing = self.latest_by_candidate()
        prior = existing.get(record["candidate_id"])
        if prior is not None:
            if prior.get("candidate_config_hash") == record.get(
                "candidate_config_hash"
            ) and prior.get("stage") == stage:
                return {"registered": False, "idempotent": True, "record": prior}
            if prior.get("candidate_config_hash") != record.get(
                "candidate_config_hash"
            ):
                raise ChallengerImmutableError(
                    "candidate %s already registered with a different "
                    "configuration; challenger configs are immutable"
                    % record["candidate_id"]
                )
            # Same config, different stage: allow only forward movement within
            # the 29A-creatable stages (e.g. BACKTESTED -> SHADOW_ELIGIBLE).
            if STAGES.index(stage) < STAGES.index(prior.get("stage")):
                raise ChallengerStageError(
                    "stage may not move backwards (%s -> %s)"
                    % (prior.get("stage"), stage)
                )
        else:
            distinct = set(existing.keys())
            if len(distinct) >= self.max_registered:
                raise ChallengerBudgetError(
                    "challenger budget exhausted (%d registered, max %d)"
                    % (len(distinct), self.max_registered)
                )
        append_jsonl(self.path, record)
        return {"registered": True, "idempotent": False, "record": record}


__all__ = [
    "STAGES",
    "PHASE29A_CREATABLE_STAGES",
    "FORBIDDEN_STAGES_29A",
    "ChallengerBudgetError",
    "ChallengerImmutableError",
    "ChallengerRegistry",
    "ChallengerStageError",
    "build_challenger_record",
]
