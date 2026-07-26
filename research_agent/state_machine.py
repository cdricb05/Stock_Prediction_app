"""Campaign state machine: explicit states, validated persisted transitions.

Every transition is validated against ALLOWED_TRANSITIONS, timestamped,
appended to the campaign event ledger (append-only, chain-hashed) and
mirrored into status.json. Replaying an already-applied transition is an
idempotent no-op. Invalid transitions raise InvalidTransitionError and
persist nothing.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .artifact_store import ArtifactStore

NEW_CAMPAIGN = "NEW_CAMPAIGN"
DATA_AUDIT = "DATA_AUDIT"
BASELINE_VALIDATION = "BASELINE_VALIDATION"
HYPOTHESIS_GENERATION = "HYPOTHESIS_GENERATION"
EXPERIMENT_PLANNING = "EXPERIMENT_PLANNING"
EXPERIMENT_RUNNING = "EXPERIMENT_RUNNING"
CANDIDATE_EVALUATION = "CANDIDATE_EVALUATION"
ROBUSTNESS_TESTING = "ROBUSTNESS_TESTING"
CHALLENGER_REGISTRATION = "CHALLENGER_REGISTRATION"
REPORTING = "REPORTING"
COMPLETE = "COMPLETE"
BLOCKED = "BLOCKED"
FAILED = "FAILED"
PAUSED = "PAUSED"

ALL_STATES = (
    NEW_CAMPAIGN,
    DATA_AUDIT,
    BASELINE_VALIDATION,
    HYPOTHESIS_GENERATION,
    EXPERIMENT_PLANNING,
    EXPERIMENT_RUNNING,
    CANDIDATE_EVALUATION,
    ROBUSTNESS_TESTING,
    CHALLENGER_REGISTRATION,
    REPORTING,
    COMPLETE,
    BLOCKED,
    FAILED,
    PAUSED,
)

TERMINAL_STATES = (COMPLETE, FAILED)

# States an unattended run walks through in order; PAUSED/BLOCKED return to
# the state they interrupted (recorded as resume_state in the event payload).
_PIPELINE = {
    NEW_CAMPAIGN: DATA_AUDIT,
    DATA_AUDIT: BASELINE_VALIDATION,
    BASELINE_VALIDATION: HYPOTHESIS_GENERATION,
    HYPOTHESIS_GENERATION: EXPERIMENT_PLANNING,
    EXPERIMENT_PLANNING: EXPERIMENT_RUNNING,
    EXPERIMENT_RUNNING: CANDIDATE_EVALUATION,
    CANDIDATE_EVALUATION: ROBUSTNESS_TESTING,
    ROBUSTNESS_TESTING: CHALLENGER_REGISTRATION,
    CHALLENGER_REGISTRATION: REPORTING,
    REPORTING: COMPLETE,
}

ALLOWED_TRANSITIONS: Dict[str, set] = {
    NEW_CAMPAIGN: {DATA_AUDIT, BLOCKED, FAILED, PAUSED},
    DATA_AUDIT: {BASELINE_VALIDATION, BLOCKED, FAILED, PAUSED},
    BASELINE_VALIDATION: {HYPOTHESIS_GENERATION, BLOCKED, FAILED, PAUSED},
    HYPOTHESIS_GENERATION: {EXPERIMENT_PLANNING, BLOCKED, FAILED, PAUSED},
    # An empty plan may go straight to reporting.
    EXPERIMENT_PLANNING: {EXPERIMENT_RUNNING, REPORTING, BLOCKED, FAILED, PAUSED},
    EXPERIMENT_RUNNING: {CANDIDATE_EVALUATION, BLOCKED, FAILED, PAUSED},
    # With no survivors, evaluation may skip robustness and report directly.
    CANDIDATE_EVALUATION: {ROBUSTNESS_TESTING, REPORTING, BLOCKED, FAILED, PAUSED},
    ROBUSTNESS_TESTING: {CHALLENGER_REGISTRATION, REPORTING, BLOCKED, FAILED, PAUSED},
    CHALLENGER_REGISTRATION: {REPORTING, BLOCKED, FAILED, PAUSED},
    REPORTING: {COMPLETE, BLOCKED, FAILED, PAUSED},
    # PAUSED/BLOCKED may only resume to the exact state they interrupted
    # (validated against the recorded resume_state) or terminate.
    PAUSED: {FAILED},
    BLOCKED: {FAILED},
    COMPLETE: set(),
    FAILED: set(),
}

EVENT_STATE_TRANSITION = "STATE_TRANSITION"


class InvalidTransitionError(RuntimeError):
    pass


def next_pipeline_state(state: str) -> Optional[str]:
    return _PIPELINE.get(state)


class CampaignStateMachine:
    """Persisted state machine for one campaign."""

    def __init__(self, store: ArtifactStore, campaign_id: str):
        self.store = store
        self.campaign_id = campaign_id

    # ---- read -------------------------------------------------------------
    def current_state(self) -> str:
        manifest = self.store.read_manifest(self.campaign_id)
        return manifest.get("current_state", NEW_CAMPAIGN)

    def resume_state(self) -> Optional[str]:
        """The state a PAUSED/BLOCKED campaign should return to."""
        manifest = self.store.read_manifest(self.campaign_id)
        return manifest.get("resume_state")

    def history(self) -> List[Dict[str, Any]]:
        return [
            row
            for row in self.store.read_events(self.campaign_id)
            if row.get("kind") == EVENT_STATE_TRANSITION
        ]

    # ---- write ------------------------------------------------------------
    def transition(
        self, to_state: str, reason: str = "", detail: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        if to_state not in ALL_STATES:
            raise InvalidTransitionError("unknown state: %s" % to_state)
        manifest = self.store.read_manifest(self.campaign_id)
        from_state = manifest.get("current_state", NEW_CAMPAIGN)

        if to_state == from_state:
            # Idempotent replay: already there, persist nothing.
            return {
                "from_state": from_state,
                "to_state": to_state,
                "applied": False,
                "idempotent_noop": True,
            }

        allowed = ALLOWED_TRANSITIONS.get(from_state, set())
        resume_target = manifest.get("resume_state")
        if from_state in (PAUSED, BLOCKED) and to_state == resume_target:
            pass  # resuming to the interrupted state is always legal
        elif to_state not in allowed:
            raise InvalidTransitionError(
                "invalid transition %s -> %s (allowed: %s)"
                % (from_state, to_state, sorted(allowed | ({resume_target} if from_state in (PAUSED, BLOCKED) and resume_target else set())))
            )

        payload = {
            "from_state": from_state,
            "to_state": to_state,
            "reason": reason,
            "detail": detail or {},
        }
        if to_state in (PAUSED, BLOCKED):
            payload["resume_state"] = from_state
        event = self.store.append_event(
            self.campaign_id, EVENT_STATE_TRANSITION, payload
        )

        manifest["current_state"] = to_state
        if to_state in (PAUSED, BLOCKED):
            manifest["resume_state"] = from_state
        elif from_state in (PAUSED, BLOCKED):
            manifest["resume_state"] = None
        manifest["last_transition_at"] = event["ts"]
        self.store.write_manifest(self.campaign_id, manifest)
        return {
            "from_state": from_state,
            "to_state": to_state,
            "applied": True,
            "idempotent_noop": False,
            "event_seq": event["seq"],
            "ts": event["ts"],
        }


__all__ = [
    "ALL_STATES",
    "ALLOWED_TRANSITIONS",
    "TERMINAL_STATES",
    "CampaignStateMachine",
    "InvalidTransitionError",
    "next_pipeline_state",
    "EVENT_STATE_TRANSITION",
    # state name constants
    "NEW_CAMPAIGN",
    "DATA_AUDIT",
    "BASELINE_VALIDATION",
    "HYPOTHESIS_GENERATION",
    "EXPERIMENT_PLANNING",
    "EXPERIMENT_RUNNING",
    "CANDIDATE_EVALUATION",
    "ROBUSTNESS_TESTING",
    "CHALLENGER_REGISTRATION",
    "REPORTING",
    "COMPLETE",
    "BLOCKED",
    "FAILED",
    "PAUSED",
]
