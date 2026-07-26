"""Phase 29C research-only feature campaign.

Converts the QUEUED feature hypotheses of a COMPLETE Phase 29B director
session into real, reproducible, point-in-time research evidence through
the deterministic executor/evaluator — with the same crash-safe artifact
discipline as the Phase 29A campaigns:

- atomic JSON writes, append-only chain-hashed events, append-only
  hypothesis/experiment/invocation ledgers (latest-row-wins snapshots),
- immutable experiment artifacts (identical rewrite = idempotent no-op),
- single-flight campaign lock,
- per-invocation ``--max-experiments`` limits ONE invocation and pauses the
  campaign (resumable); COMPLETE requires all supported work terminal,
- terminal campaigns are never rerun and terminal evidence never rewritten.

The campaign NEVER registers challengers: ``register_challenger`` here
always raises. A robustness-stage SHADOW_ELIGIBLE decision is recorded as
evidence only; any registration would require the deterministic Phase 29A
controller path plus explicit human approval, outside this module.
"""

from __future__ import annotations

import datetime as _dt
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import SAFETY_CONTRACT
from . import evaluator as ev
from . import family_backtest as fb
from . import feature_evaluation as fe
from . import feature_execution as fx
from .artifact_store import (
    ArtifactStore,
    CampaignLock,
    CampaignLockedError,
    ImmutableArtifactError,
    _now_iso,
    _validate_id,
    append_jsonl,
    content_hash,
    read_json,
    read_jsonl,
    write_json_atomic,
    write_text_atomic,
)
from .controller import (
    DEFAULT_ARTIFACT_ROOT,
    PAUSE_INVOCATION_LIMIT,
    PAUSE_REMAINING_WORK,
    RUN_ALREADY_COMPLETE,
    RUN_ALREADY_FAILED,
    RUN_BLOCKED,
    RUN_FAILED,
    RUN_LOCKED,
    RUN_OK,
    RUN_PAUSED,
    read_git_commit,
)
from .director import DirectorSafetyError, DirectorStore
from .director_provider import PROVIDER_NAMES
from .feature_dsl import compile_feature_set, validate_feature_set
from .schemas import (
    APPROVED_COST_BPS_PER_SIDE,
    APPROVED_MIN_ADV_DOLLARS,
    APPROVED_UNIVERSES,
    find_forbidden_execution_keys,
)

FEATURE_CAMPAIGN_SCHEMA_VERSION = "29C.1"

FEATURE_CAMPAIGNS_DIR = "feature_campaigns"

# ---- Part D states -------------------------------------------------------- #
NEW = "NEW"
SOURCE_AUDIT = "SOURCE_AUDIT"
DSL_VALIDATION = "DSL_VALIDATION"
FEATURE_BUILD = "FEATURE_BUILD"
PIT_VALIDATION = "PIT_VALIDATION"
IC_SCREEN = "IC_SCREEN"
PORTFOLIO_SCREEN = "PORTFOLIO_SCREEN"
PRIMARY_EVALUATION = "PRIMARY_EVALUATION"
ROBUSTNESS_TESTING = "ROBUSTNESS_TESTING"
DIRECTOR_FEEDBACK = "DIRECTOR_FEEDBACK"
REPORTING = "REPORTING"
PAUSED = "PAUSED"
COMPLETE = "COMPLETE"
BLOCKED = "BLOCKED"
FAILED = "FAILED"

FEATURE_STATES = (
    NEW, SOURCE_AUDIT, DSL_VALIDATION, FEATURE_BUILD, PIT_VALIDATION,
    IC_SCREEN, PORTFOLIO_SCREEN, PRIMARY_EVALUATION, ROBUSTNESS_TESTING,
    DIRECTOR_FEEDBACK, REPORTING, PAUSED, COMPLETE, BLOCKED, FAILED,
)
TERMINAL_STATES = (COMPLETE, FAILED)

_PIPELINE = {
    NEW: SOURCE_AUDIT,
    SOURCE_AUDIT: DSL_VALIDATION,
    DSL_VALIDATION: FEATURE_BUILD,
    FEATURE_BUILD: PIT_VALIDATION,
    PIT_VALIDATION: IC_SCREEN,
    IC_SCREEN: PORTFOLIO_SCREEN,
    PORTFOLIO_SCREEN: PRIMARY_EVALUATION,
    PRIMARY_EVALUATION: ROBUSTNESS_TESTING,
    ROBUSTNESS_TESTING: DIRECTOR_FEEDBACK,
    DIRECTOR_FEEDBACK: REPORTING,
    REPORTING: COMPLETE,
}

ALLOWED_TRANSITIONS: Dict[str, set] = {
    state: {nxt, PAUSED, BLOCKED, FAILED}
    for state, nxt in _PIPELINE.items()
}
ALLOWED_TRANSITIONS[PAUSED] = {FAILED}
ALLOWED_TRANSITIONS[BLOCKED] = {FAILED}
ALLOWED_TRANSITIONS[COMPLETE] = set()
ALLOWED_TRANSITIONS[FAILED] = set()

FEATURE_EXPERIMENT_ARTIFACTS = (
    "config.json",
    "provenance.json",
    "diagnostics.json",
    "screen_result.json",
    "metrics.json",
    "gate_results.json",
    "baseline_deltas.json",
    "decision.json",
    "robustness_results.json",
    "report.md",
)

FEATURE_CAMPAIGN_ARTIFACTS = (
    "source_inventory.json",
    "baseline_validation.json",
)

EXPERIMENT_KIND_DIAGNOSTIC = "feature_diagnostic"
EXPERIMENT_KIND_INTEGRATION = "baseline_integration"

HYPOTHESIS_STATUSES = (
    "ACCEPTED",
    "REJECTED_AT_DIRECTOR",
    "NOT_ACCEPTED_BUDGET",
    "DSL_VALID",
    "REJECTED_DSL",
    "REJECTED_EXECUTION",
    "REJECTED_PIT",
    "SCREEN_REJECTED",
    "SCREEN_INCONCLUSIVE",
    "ADVANCED_TO_PORTFOLIO",
    "PRIMARY_REJECTED",
    "PRIMARY_INCONCLUSIVE",
    "RETAINED_FOR_ROBUSTNESS",
    "ROBUSTNESS_COMPLETE",
)

EXPERIMENT_STATUSES = (
    "PLANNED",
    "RUNNING",
    "COMPLETE",
    "FAILED",
    "SKIPPED_DSL",
    "SKIPPED_EXECUTION",
    "SKIPPED_PIT",
    "SKIPPED_SCREEN",
    "SKIPPED_BUDGET",
)

# Part L budgets are HARD ceilings: an operator config may lower them, never
# raise them, and no provider response can touch them at all.
PHASE29C_BUDGET_CEILINGS = {
    "max_accepted_hypotheses": 3,
    "max_primary_experiments": 6,
    "max_experiments_per_hypothesis": 2,
    "max_retained_candidates": 2,
    "max_robustness_candidates": 2,
    "max_revised_experiments": 2,
    "max_director_feedback_cycles": 1,
    "max_retry_per_tool": 1,
    "max_feature_depth": 3,
    "max_interactions": 4,
}

# ic_screen override directions: "floor" values may only be raised (stricter),
# "cap" values may only be lowered. Nothing in the config can weaken a screen.
_SCREEN_FLOORS = ("min_months", "min_coverage_fraction", "min_abs_rank_ic_t",
                  "material_ic_t_margin", "min_universe")
_SCREEN_CAPS = ("near_duplicate_abs_corr", "max_complementary_abs_baseline_corr",
                "max_top_rank_sector_share", "leakage_suspicion_abs_ic")

REQUIRED_CONFIG_FIELDS = (
    "schema_version", "name", "objective", "budgets", "integration", "costs",
    "portfolio", "ic_screen", "data", "random_seed", "provider",
    "strict_mode", "safety",
)


class FeatureCampaignError(RuntimeError):
    pass


class InvalidFeatureTransitionError(FeatureCampaignError):
    pass


def register_challenger(*_args: Any, **_kwargs: Any) -> None:
    """The feature campaign can never register challengers. Always raises.

    Robustness-stage outcomes are evidence only. Registration belongs to
    the deterministic Phase 29A controller behind the strict, unlowered
    SHADOW_ELIGIBLE gates plus explicit human approval — and never from
    fixture-provider-generated evidence.
    """
    raise DirectorSafetyError(
        "the Phase 29C feature campaign has no challenger-registration "
        "capability; registration requires the deterministic Phase 29A "
        "controller gates and explicit human approval")


def _violation(field: str, issue: str, value: Any, severity: str = "INVALID"):
    return {"field": field, "issue": issue, "value": value, "severity": severity}


def _is_int(x: Any) -> bool:
    return isinstance(x, int) and not isinstance(x, bool)


def _is_num(x: Any) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool)


# --------------------------------------------------------------------------- #
# Part L: configuration validation
# --------------------------------------------------------------------------- #
def validate_feature_campaign_config(cfg: Any) -> Dict[str, Any]:
    violations: List[dict] = []
    if not isinstance(cfg, dict):
        return {"accepted": False, "config_hash": None,
                "violations": [_violation("$", "config must be a JSON object",
                                          type(cfg).__name__)]}
    for kp in find_forbidden_execution_keys(cfg):
        violations.append(_violation(
            kp, "executable-content key is forbidden (no arbitrary code)", None))
    for f in REQUIRED_CONFIG_FIELDS:
        if f not in cfg:
            violations.append(_violation(f, "required field missing", None))
    for f in sorted(set(cfg) - set(REQUIRED_CONFIG_FIELDS)):
        violations.append(_violation(
            f, "unknown field (strict schema; evaluation gates are NOT "
            "configurable here — the strict evaluator defaults always apply)",
            None))

    if cfg.get("strict_mode") is not True:
        violations.append(_violation("strict_mode", "must be true in Phase 29C",
                                     cfg.get("strict_mode")))

    budgets = cfg.get("budgets")
    if isinstance(budgets, dict):
        for name, ceiling in PHASE29C_BUDGET_CEILINGS.items():
            v = budgets.get(name)
            if not _is_int(v) or v < 0:
                violations.append(_violation(
                    "budgets.%s" % name, "must be a non-negative integer", v))
            elif v > ceiling:
                violations.append(_violation(
                    "budgets.%s" % name,
                    "exceeds the Phase 29C hard ceiling %d (budgets may be "
                    "lowered, never raised)" % ceiling, v))
        for name in sorted(set(budgets) - set(PHASE29C_BUDGET_CEILINGS)):
            violations.append(_violation(
                "budgets.%s" % name, "unknown budget (strict schema)",
                budgets.get(name)))
    elif budgets is not None:
        violations.append(_violation("budgets", "must be an object", budgets))

    integ = cfg.get("integration")
    if isinstance(integ, dict):
        for f in sorted(set(integ) - {"baseline_weight", "feature_weight"}):
            violations.append(_violation(
                "integration.%s" % f, "unknown field", integ.get(f)))
        bw, fw = integ.get("baseline_weight"), integ.get("feature_weight")
        if not (_is_num(bw) and _is_num(fw)):
            violations.append(_violation(
                "integration", "baseline_weight and feature_weight must be "
                "numbers", integ))
        else:
            if abs(float(bw) + float(fw) - 1.0) > 1e-9:
                violations.append(_violation(
                    "integration", "weights must reconcile to exactly one",
                    {"baseline_weight": bw, "feature_weight": fw}))
            if not (0.0 < float(fw) <= fe.FEATURE_WEIGHT_CEILING):
                violations.append(_violation(
                    "integration.feature_weight",
                    "must be in (0, %.2f] (bounded candidate weight; no "
                    "parameter sweep)" % fe.FEATURE_WEIGHT_CEILING, fw))
    elif integ is not None:
        violations.append(_violation("integration", "must be an object", integ))

    costs = cfg.get("costs")
    if isinstance(costs, dict):
        for f in sorted(set(costs) - {"primary_cost_bps_per_side",
                                      "sensitivity_cost_bps_per_side"}):
            violations.append(_violation("costs.%s" % f, "unknown field",
                                         costs.get(f)))
        pc = costs.get("primary_cost_bps_per_side")
        if not _is_num(pc) or abs(float(pc) - 25.0) > 1e-9:
            violations.append(_violation(
                "costs.primary_cost_bps_per_side",
                "must be 25.0 (the Phase 10-C research cost standard)", pc))
        sens = costs.get("sensitivity_cost_bps_per_side")
        ok = (
            isinstance(sens, list) and len(sens) == 2
            and all(_is_num(s) for s in sens)
            and abs(float(sens[0]) - 12.5) <= 1e-9
            and abs(float(sens[1]) - 50.0) <= 1e-9
        )
        if not ok:
            violations.append(_violation(
                "costs.sensitivity_cost_bps_per_side",
                "must be [12.5, 50.0] (the approved ladder %s)"
                % (APPROVED_COST_BPS_PER_SIDE,), sens))
    elif costs is not None:
        violations.append(_violation("costs", "must be an object", costs))

    pf = cfg.get("portfolio")
    if isinstance(pf, dict):
        for f in sorted(set(pf) - {"top_n", "sector_treatment",
                                   "exit_buffer_fraction", "universe",
                                   "min_adv_dollar"}):
            violations.append(_violation("portfolio.%s" % f, "unknown field",
                                         pf.get(f)))
        if pf.get("top_n") != fb.BASELINE_PARAMS["top_n"]:
            violations.append(_violation(
                "portfolio.top_n", "must be the validated baseline book size "
                "%d" % fb.BASELINE_PARAMS["top_n"], pf.get("top_n")))
        if pf.get("sector_treatment") != fb.BASELINE_PARAMS["sector_treatment"]:
            violations.append(_violation(
                "portfolio.sector_treatment",
                "must be the validated baseline convention %r"
                % fb.BASELINE_PARAMS["sector_treatment"],
                pf.get("sector_treatment")))
        eb = pf.get("exit_buffer_fraction")
        if not _is_num(eb) or abs(float(eb)) > 1e-12:
            violations.append(_violation(
                "portfolio.exit_buffer_fraction",
                "must be 0.0: the operational exit buffer is a separate "
                "research dimension and is never silently applied", eb))
        if pf.get("universe") not in APPROVED_UNIVERSES:
            violations.append(_violation(
                "portfolio.universe", "must be one of %s"
                % (APPROVED_UNIVERSES,), pf.get("universe")))
        ma = pf.get("min_adv_dollar")
        if not _is_num(ma) or not any(
                abs(float(ma) - a) <= 1.0 for a in APPROVED_MIN_ADV_DOLLARS):
            violations.append(_violation(
                "portfolio.min_adv_dollar", "must be one of %s"
                % (APPROVED_MIN_ADV_DOLLARS,), ma))
    elif pf is not None:
        violations.append(_violation("portfolio", "must be an object", pf))

    ics = cfg.get("ic_screen")
    if isinstance(ics, dict):
        defaults = fe.DEFAULT_SCREEN_THRESHOLDS
        for f in sorted(set(ics) - set(defaults)):
            violations.append(_violation("ic_screen.%s" % f,
                                         "unknown screen threshold", ics.get(f)))
        for f, v in sorted(ics.items()):
            if f not in defaults:
                continue
            if not _is_num(v):
                violations.append(_violation("ic_screen.%s" % f,
                                             "must be a number", v))
                continue
            default = float(defaults[f]["value"])
            if f in _SCREEN_FLOORS and float(v) < default - 1e-12:
                violations.append(_violation(
                    "ic_screen.%s" % f,
                    "below the validated floor %s (screens may be tightened, "
                    "never weakened)" % default, v))
            if f in _SCREEN_CAPS and float(v) > default + 1e-12:
                violations.append(_violation(
                    "ic_screen.%s" % f,
                    "above the validated cap %s (screens may be tightened, "
                    "never weakened)" % default, v))
    elif ics is not None:
        violations.append(_violation("ic_screen", "must be an object", ics))

    data = cfg.get("data")
    if isinstance(data, dict):
        for f in sorted(set(data) - {"inherit_data_cutoff_from_source_campaign"}):
            violations.append(_violation("data.%s" % f, "unknown field",
                                         data.get(f)))
        if data.get("inherit_data_cutoff_from_source_campaign") is not True:
            violations.append(_violation(
                "data.inherit_data_cutoff_from_source_campaign",
                "must be true: the cutoff is inherited from the source "
                "campaign and immutable",
                data.get("inherit_data_cutoff_from_source_campaign")))
    elif data is not None:
        violations.append(_violation("data", "must be an object", data))

    rs = cfg.get("random_seed")
    if rs is not None and (not _is_int(rs) or rs < 0):
        violations.append(_violation("random_seed",
                                     "must be a non-negative integer", rs))

    prov = cfg.get("provider")
    if isinstance(prov, dict):
        default = prov.get("default")
        if default is not None and default not in PROVIDER_NAMES:
            violations.append(_violation(
                "provider.default", "must be one of %s" % (PROVIDER_NAMES,),
                default))
    elif prov is not None:
        violations.append(_violation("provider", "must be an object", prov))

    safety = cfg.get("safety")
    if isinstance(safety, dict):
        if safety.get("no_operational_promotion") is not True:
            violations.append(_violation(
                "safety.no_operational_promotion", "must be true",
                safety.get("no_operational_promotion")))
        if safety.get("fixture_provider_may_register_challengers") is not False:
            violations.append(_violation(
                "safety.fixture_provider_may_register_challengers",
                "must be false", safety.get(
                    "fixture_provider_may_register_challengers")))
    elif safety is not None:
        violations.append(_violation("safety", "must be an object", safety))

    accepted = not violations
    return {
        "accepted": accepted,
        "violations": violations,
        "config_hash": content_hash(cfg) if accepted else None,
        "schema_version": FEATURE_CAMPAIGN_SCHEMA_VERSION,
    }


# --------------------------------------------------------------------------- #
# store (Part D layout)
# --------------------------------------------------------------------------- #
class FeatureCampaignStore:
    """Filesystem store under <artifact_root>/feature_campaigns/<id>/.

    Reuses the ArtifactStore construction guard (never inside a git
    checkout) and the identical atomic-write / append-only / chain-hash /
    immutable-artifact discipline.
    """

    def __init__(self, artifact_root: str):
        self._guard = ArtifactStore(artifact_root)  # validates the root
        self.root = Path(artifact_root)
        self.base = self.root / FEATURE_CAMPAIGNS_DIR

    # ---- layout -----------------------------------------------------------
    def campaign_dir(self, campaign_id: str) -> Path:
        _validate_id(campaign_id, "feature_campaign_id")
        return self.base / campaign_id

    def ensure_layout(self, campaign_id: str) -> Path:
        d = self.campaign_dir(campaign_id)
        for sub in ("features", "experiments", "feedback", "reports", "locks"):
            (d / sub).mkdir(parents=True, exist_ok=True)
        return d

    def list_campaigns(self) -> List[str]:
        if not self.base.exists():
            return []
        return sorted(
            p.name for p in self.base.iterdir()
            if p.is_dir() and (p / "campaign.json").exists()
        )

    # ---- manifest / status ------------------------------------------------
    def read_manifest(self, campaign_id: str) -> Dict[str, Any]:
        path = self.campaign_dir(campaign_id) / "campaign.json"
        if not path.exists():
            raise FeatureCampaignError(
                "unknown feature campaign: %s" % campaign_id)
        return read_json(path)

    def write_manifest(self, campaign_id: str, doc: Dict[str, Any]) -> None:
        write_json_atomic(self.campaign_dir(campaign_id) / "campaign.json", doc)

    def write_status(self, campaign_id: str, doc: Dict[str, Any]) -> None:
        out = dict(doc)
        out["updated_at"] = _now_iso()
        out["schema_version"] = FEATURE_CAMPAIGN_SCHEMA_VERSION
        write_json_atomic(self.campaign_dir(campaign_id) / "status.json", out)

    def read_status(self, campaign_id: str) -> Optional[Dict[str, Any]]:
        path = self.campaign_dir(campaign_id) / "status.json"
        return read_json(path) if path.exists() else None

    # ---- ledgers ----------------------------------------------------------
    def append_event(self, campaign_id: str, kind: str,
                     payload: Dict[str, Any]) -> Dict[str, Any]:
        path = self.campaign_dir(campaign_id) / "events.jsonl"
        rows = read_jsonl(path)
        prev_hash = rows[-1]["row_hash"] if rows else "GENESIS"
        core = {"seq": len(rows) + 1, "kind": kind, "payload": payload,
                "prev_hash": prev_hash}
        core["row_hash"] = content_hash(core)
        core["ts"] = _now_iso()
        append_jsonl(path, core)
        return core

    def read_events(self, campaign_id: str) -> List[Dict[str, Any]]:
        return read_jsonl(self.campaign_dir(campaign_id) / "events.jsonl")

    def verify_event_chain(self, campaign_id: str) -> Dict[str, Any]:
        rows = self.read_events(campaign_id)
        prev = "GENESIS"
        for i, row in enumerate(rows):
            core = {"seq": row.get("seq"), "kind": row.get("kind"),
                    "payload": row.get("payload"),
                    "prev_hash": row.get("prev_hash")}
            if row.get("prev_hash") != prev or content_hash(core) != row.get(
                    "row_hash"):
                return {"intact": False, "first_bad_row": i + 1,
                        "rows": len(rows)}
            prev = row.get("row_hash")
        return {"intact": True, "rows": len(rows)}

    def append_hypothesis(self, campaign_id: str, record: Dict[str, Any]) -> None:
        append_jsonl(self.campaign_dir(campaign_id) / "hypotheses.jsonl", record)

    def hypotheses(self, campaign_id: str) -> Dict[str, Dict[str, Any]]:
        latest: Dict[str, Dict[str, Any]] = {}
        for row in read_jsonl(self.campaign_dir(campaign_id) / "hypotheses.jsonl"):
            latest[row["hypothesis_id"]] = row
        return latest

    def append_experiment(self, campaign_id: str, record: Dict[str, Any]) -> None:
        append_jsonl(
            self.campaign_dir(campaign_id) / "experiment_index.jsonl", record)

    def experiments(self, campaign_id: str) -> Dict[str, Dict[str, Any]]:
        latest: Dict[str, Dict[str, Any]] = {}
        for row in read_jsonl(
                self.campaign_dir(campaign_id) / "experiment_index.jsonl"):
            latest[row["experiment_id"]] = row
        return latest

    def append_invocation(self, campaign_id: str, record: Dict[str, Any]) -> None:
        append_jsonl(self.campaign_dir(campaign_id) / "invocations.jsonl", record)

    def read_invocations(self, campaign_id: str) -> List[Dict[str, Any]]:
        return read_jsonl(self.campaign_dir(campaign_id) / "invocations.jsonl")

    # ---- immutable artifacts ----------------------------------------------
    def _write_immutable(self, path: Path, obj: Any) -> str:
        if str(path.name).endswith(".md"):
            if path.exists():
                if path.read_text(encoding="utf-8") == obj:
                    return content_hash(obj)
                raise ImmutableArtifactError(
                    "artifact already exists and differs: %s" % path)
            write_text_atomic(path, obj)
            return content_hash(obj)
        digest = content_hash(obj)
        if path.exists():
            if content_hash(read_json(path)) == digest:
                return digest
            raise ImmutableArtifactError(
                "artifact already exists and differs: %s" % path)
        return write_json_atomic(path, obj)

    def write_campaign_artifact(self, campaign_id: str, name: str,
                                obj: Any) -> str:
        if name not in FEATURE_CAMPAIGN_ARTIFACTS:
            raise FeatureCampaignError(
                "unknown campaign artifact name: %s" % name)
        return self._write_immutable(self.campaign_dir(campaign_id) / name, obj)

    def read_campaign_artifact(self, campaign_id: str, name: str) -> Any:
        path = self.campaign_dir(campaign_id) / name
        return read_json(path) if path.exists() else None

    def write_feature_artifact(self, campaign_id: str, hypothesis_id: str,
                               name: str, obj: Any) -> str:
        _validate_id(hypothesis_id, "hypothesis_id")
        path = self.campaign_dir(campaign_id) / "features" / (
            "%s.%s" % (hypothesis_id, name))
        return self._write_immutable(path, obj)

    def read_feature_artifact(self, campaign_id: str, hypothesis_id: str,
                              name: str) -> Any:
        path = self.campaign_dir(campaign_id) / "features" / (
            "%s.%s" % (hypothesis_id, name))
        return read_json(path) if path.exists() else None

    def experiment_dir(self, campaign_id: str, experiment_id: str) -> Path:
        _validate_id(experiment_id, "experiment_id")
        return self.campaign_dir(campaign_id) / "experiments" / experiment_id

    def write_experiment_artifact(self, campaign_id: str, experiment_id: str,
                                  name: str, obj: Any) -> str:
        if name not in FEATURE_EXPERIMENT_ARTIFACTS:
            raise FeatureCampaignError(
                "unknown experiment artifact name: %s" % name)
        path = self.experiment_dir(campaign_id, experiment_id) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        return self._write_immutable(path, obj)

    def read_experiment_artifact(self, campaign_id: str, experiment_id: str,
                                 name: str) -> Any:
        path = self.experiment_dir(campaign_id, experiment_id) / name
        if not path.exists():
            return None
        if name.endswith(".md"):
            return path.read_text(encoding="utf-8")
        return read_json(path)

    def feedback_dir(self, campaign_id: str) -> Path:
        return self.campaign_dir(campaign_id) / "feedback"

    def lock(self, campaign_id: str, owner: str) -> CampaignLock:
        return CampaignLock(
            self.campaign_dir(campaign_id) / "locks" / "campaign.lock", owner)


# --------------------------------------------------------------------------- #
# state machine (Part D; validated persisted transitions)
# --------------------------------------------------------------------------- #
class FeatureStateMachine:
    def __init__(self, store: FeatureCampaignStore, campaign_id: str):
        self.store = store
        self.campaign_id = campaign_id

    def current_state(self) -> str:
        return self.store.read_manifest(self.campaign_id).get(
            "current_state", NEW)

    def resume_state(self) -> Optional[str]:
        return self.store.read_manifest(self.campaign_id).get("resume_state")

    def transition(self, to_state: str, reason: str = "",
                   detail: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        if to_state not in FEATURE_STATES:
            raise InvalidFeatureTransitionError("unknown state: %s" % to_state)
        manifest = self.store.read_manifest(self.campaign_id)
        from_state = manifest.get("current_state", NEW)
        if to_state == from_state:
            return {"from_state": from_state, "to_state": to_state,
                    "applied": False, "idempotent_noop": True}
        allowed = ALLOWED_TRANSITIONS.get(from_state, set())
        resume_target = manifest.get("resume_state")
        if from_state in (PAUSED, BLOCKED) and to_state == resume_target:
            pass
        elif to_state not in allowed:
            raise InvalidFeatureTransitionError(
                "invalid transition %s -> %s" % (from_state, to_state))
        payload = {"from_state": from_state, "to_state": to_state,
                   "reason": reason, "detail": detail or {}}
        if to_state in (PAUSED, BLOCKED):
            payload["resume_state"] = from_state
        event = self.store.append_event(
            self.campaign_id, "STATE_TRANSITION", payload)
        manifest["current_state"] = to_state
        if to_state in (PAUSED, BLOCKED):
            manifest["resume_state"] = from_state
        elif from_state in (PAUSED, BLOCKED):
            manifest["resume_state"] = None
        manifest["last_transition_at"] = event["ts"]
        self.store.write_manifest(self.campaign_id, manifest)
        return {"from_state": from_state, "to_state": to_state,
                "applied": True, "idempotent_noop": False,
                "event_seq": event["seq"], "ts": event["ts"]}


# --------------------------------------------------------------------------- #
# campaign creation (Part D)
# --------------------------------------------------------------------------- #
def _hyp_snapshot(hypothesis_id: str, status: str, **extra: Any) -> Dict[str, Any]:
    if status not in HYPOTHESIS_STATUSES:
        raise FeatureCampaignError("unknown hypothesis status: %s" % status)
    row = {
        "schema_version": FEATURE_CAMPAIGN_SCHEMA_VERSION,
        "record_type": "FEATURE_HYPOTHESIS",
        "hypothesis_id": hypothesis_id,
        "status": status,
        "recorded_at": _now_iso(),
    }
    row.update(extra)
    return row


def _experiment_row(record: Dict[str, Any]) -> Dict[str, Any]:
    if record.get("status") not in EXPERIMENT_STATUSES:
        raise FeatureCampaignError(
            "unknown experiment status: %s" % record.get("status"))
    row = dict(record)
    row.setdefault("schema_version", FEATURE_CAMPAIGN_SCHEMA_VERSION)
    row.setdefault("record_type", "FEATURE_EXPERIMENT")
    row["recorded_at"] = _now_iso()
    return row


def create_feature_campaign(
    *,
    config: Dict[str, Any],
    director_root: str,
    session_id: str,
    source_campaign_id: str,
    artifact_root: Optional[str] = None,
    now_label: Optional[str] = None,
) -> Dict[str, Any]:
    """Create a research-only feature campaign from a COMPLETE director session.

    The FULL experiment plan (two bounded experiments per accepted
    hypothesis, integration conditional on the IC screen) is persisted
    before any execution; rejected/unqueued director hypotheses are
    preserved with their reasons; the data cutoff and dataset hashes are
    inherited from the source campaign and immutable thereafter.
    """
    verdict = validate_feature_campaign_config(config)
    if not verdict["accepted"]:
        return {"created": False, "reason": "INVALID_CONFIG",
                "violations": verdict["violations"]}

    dstore = DirectorStore(director_root)
    session = dstore.read_session_manifest(session_id)
    if session is None:
        return {"created": False, "reason": "UNKNOWN_DIRECTOR_SESSION",
                "session_id": session_id}
    if session.get("session_state") != "COMPLETE":
        return {"created": False, "reason": "DIRECTOR_SESSION_NOT_COMPLETE",
                "session_state": session.get("session_state")}
    if session.get("source_campaign") != source_campaign_id:
        return {"created": False, "reason": "SESSION_CAMPAIGN_MISMATCH",
                "session_source_campaign": session.get("source_campaign")}

    root = artifact_root or DEFAULT_ARTIFACT_ROOT
    campaign_store = ArtifactStore(root)
    try:
        source_manifest = campaign_store.read_manifest(source_campaign_id)
    except Exception:
        return {"created": False, "reason": "UNKNOWN_SOURCE_CAMPAIGN",
                "source_campaign_id": source_campaign_id}

    src_cfg = source_manifest.get("config") or {}
    data_cutoff = (src_cfg.get("data") or {}).get("data_cutoff")
    if not data_cutoff:
        return {"created": False, "reason": "SOURCE_CAMPAIGN_HAS_NO_CUTOFF"}
    dataset_hashes = (((source_manifest.get("data_audit") or {})
                       .get("coverage") or {}).get("provenance") or {}).get(
        "sha256")

    pack = None
    try:
        pack = dstore.load_evidence_pack(session.get("evidence_pack_id"))
    except Exception:
        pack = None
    exhausted_signatures = sorted(
        (pack or {}).get("exhausted_signatures") or [])

    rows = dstore.read_proposals(session_id)
    queued = sorted(
        (r for r in rows if r.get("status") == "QUEUED"),
        key=lambda r: (r.get("information_gain_rank") or 10 ** 9,
                       r.get("hypothesis_id") or ""),
    )
    budgets = dict(config["budgets"])
    max_hyps = int(budgets["max_accepted_hypotheses"])
    accepted = queued[:max_hyps]
    over_budget = queued[max_hyps:]
    rejected = [r for r in rows if r.get("status") != "QUEUED"]

    fstore = FeatureCampaignStore(root)
    stamp = now_label or _dt.datetime.now(_dt.timezone.utc).strftime(
        "%Y%m%dT%H%M%SZ")
    campaign_id = "%s_%s" % (config["name"], stamp)
    n = 1
    while campaign_id in fstore.list_campaigns():
        n += 1
        campaign_id = "%s_%s_%d" % (config["name"], stamp, n)
    fstore.ensure_layout(campaign_id)

    integration = dict(config["integration"])
    portfolio = dict(config["portfolio"])
    costs = dict(config["costs"])
    plan_rows: List[Dict[str, Any]] = []
    rank = 0
    per_hyp = min(2, int(budgets["max_experiments_per_hypothesis"]))
    max_primary = int(budgets["max_primary_experiments"])
    for r in accepted:
        hid = r["hypothesis_id"]
        rank += 1
        diag = {
            "experiment_id": "fexp_%s_diag" % hid,
            "hypothesis_id": hid,
            "kind": EXPERIMENT_KIND_DIAGNOSTIC,
            "plan_rank": rank,
            "status": "PLANNED",
            "execution_condition": None,
            "params": {"min_universe": None},  # resolved from ic_screen config
            "data_cutoff": data_cutoff,
            "random_seed": int(config["random_seed"]),
        }
        diag["spec_hash"] = content_hash(
            {k: v for k, v in diag.items() if k not in ("plan_rank", "status")})
        plan_rows.append(diag)
    for r in accepted:
        if per_hyp < 2:
            break
        hid = r["hypothesis_id"]
        rank += 1
        integ = {
            "experiment_id": "fexp_%s_integ" % hid,
            "hypothesis_id": hid,
            "kind": EXPERIMENT_KIND_INTEGRATION,
            "plan_rank": rank,
            "status": "PLANNED",
            "execution_condition": "ic_screen == ADVANCE_TO_PORTFOLIO_SCREEN",
            "params": {
                "baseline_weight": integration["baseline_weight"],
                "feature_weight": integration["feature_weight"],
                "top_n": portfolio["top_n"],
                "sector_treatment": portfolio["sector_treatment"],
                "exit_buffer_fraction": portfolio["exit_buffer_fraction"],
                "universe": portfolio["universe"],
                "min_adv_dollar": portfolio["min_adv_dollar"],
                "primary_cost_bps_per_side": costs["primary_cost_bps_per_side"],
            },
            "data_cutoff": data_cutoff,
            "random_seed": int(config["random_seed"]),
        }
        integ["spec_hash"] = content_hash(
            {k: v for k, v in integ.items() if k not in ("plan_rank", "status")})
        plan_rows.append(integ)
    plan_rows = plan_rows[:max_primary]

    manifest = {
        "schema_version": FEATURE_CAMPAIGN_SCHEMA_VERSION,
        "record_type": "FEATURE_CAMPAIGN",
        "feature_campaign_id": campaign_id,
        "objective": config["objective"],
        "source_director_session": session_id,
        "source_campaign": source_campaign_id,
        "evidence_pack_id": session.get("evidence_pack_id"),
        "provider": session.get("provider"),
        "code_commit": read_git_commit(),
        "data_cutoff": data_cutoff,
        "dataset_hashes": dataset_hashes,
        "data_paths": dict(src_cfg.get("data") or {}),
        "accepted_hypotheses": [r["hypothesis_id"] for r in accepted],
        "rejected_hypotheses": sorted(
            {r.get("hypothesis_id") for r in rejected + over_budget
             if r.get("hypothesis_id")}),
        "experiment_plan": {
            "planned_total": len(plan_rows),
            "diagnostics": sum(
                1 for p in plan_rows
                if p["kind"] == EXPERIMENT_KIND_DIAGNOSTIC),
            "integrations": sum(
                1 for p in plan_rows
                if p["kind"] == EXPERIMENT_KIND_INTEGRATION),
        },
        "budgets": budgets,
        "config": config,
        "config_hash": verdict["config_hash"],
        "exhausted_signatures": exhausted_signatures,
        "current_state": NEW,
        "resume_state": None,
        "created_at": _now_iso(),
        "safety": dict(SAFETY_CONTRACT),
    }
    fstore.write_manifest(campaign_id, manifest)
    fstore.append_event(campaign_id, "FEATURE_CAMPAIGN_CREATED", {
        "source_director_session": session_id,
        "source_campaign": source_campaign_id,
        "config_hash": verdict["config_hash"],
    })

    for r in accepted:
        fstore.append_hypothesis(campaign_id, _hyp_snapshot(
            r["hypothesis_id"], "ACCEPTED",
            proposal=r.get("proposal"),
            signature=r.get("signature"),
            information_gain_rank=r.get("information_gain_rank"),
        ))
    for r in over_budget:
        fstore.append_hypothesis(campaign_id, _hyp_snapshot(
            r["hypothesis_id"], "NOT_ACCEPTED_BUDGET",
            director_status=r.get("status"),
            reasons=["max_accepted_hypotheses budget reached"],
            signature=r.get("signature"),
        ))
    for r in rejected:
        fstore.append_hypothesis(campaign_id, _hyp_snapshot(
            r["hypothesis_id"], "REJECTED_AT_DIRECTOR",
            director_status=r.get("status"),
            reasons=r.get("reasons"),
            signature=r.get("signature"),
        ))
    for p in plan_rows:
        fstore.append_experiment(campaign_id, _experiment_row(p))
    fstore.append_event(campaign_id, "PLAN_PERSISTED", {
        "planned_total": len(plan_rows),
        "accepted_hypotheses": [r["hypothesis_id"] for r in accepted],
    })
    fstore.write_status(campaign_id, {
        "feature_campaign_id": campaign_id,
        "state": NEW,
        "safety": dict(SAFETY_CONTRACT),
    })
    return {"created": True, "feature_campaign_id": campaign_id,
            "artifact_root": str(root),
            "planned_total": len(plan_rows),
            "accepted_hypotheses": [r["hypothesis_id"] for r in accepted]}


# --------------------------------------------------------------------------- #
# controller
# --------------------------------------------------------------------------- #
class FeatureCampaignController:
    def __init__(
        self,
        campaign_id: str,
        *,
        artifact_root: Optional[str] = None,
        inputs: Optional[Dict[str, Any]] = None,
        reference_rows: Optional[List[dict]] = None,
        close_frame: Optional[Any] = None,
    ):
        self.store = FeatureCampaignStore(artifact_root or DEFAULT_ARTIFACT_ROOT)
        self.campaign_id = campaign_id
        self.sm = FeatureStateMachine(self.store, campaign_id)
        self._seam_inputs = inputs
        self._seam_reference = reference_rows
        self._seam_close_frame = close_frame
        self._panel: Optional[fx.FeaturePanel] = None
        self._max_experiments_override: Optional[int] = None
        self._counters = {"attempted": 0, "completed": 0, "failed": 0}

    # ---- shared context ---------------------------------------------------
    @property
    def manifest(self) -> Dict[str, Any]:
        return self.store.read_manifest(self.campaign_id)

    @property
    def config(self) -> Dict[str, Any]:
        return self.manifest["config"]

    def inputs(self) -> Dict[str, Any]:
        if self._seam_inputs is None:
            manifest = self.manifest
            data = manifest.get("data_paths") or {}
            self._seam_inputs = fb.load_family_inputs(
                data_cutoff=manifest["data_cutoff"],
                momentum_panel_path=data.get("momentum_panel"),
                fundamental_panel_path=data.get("fundamental_panel"),
                sector_map_path=data.get("sector_map"),
                spy_monthly_path=data.get("spy_monthly"),
            )
        return self._seam_inputs

    def panel(self) -> fx.FeaturePanel:
        if self._panel is None:
            self._panel = fx.FeaturePanel(self.inputs())
        return self._panel

    def _inventory(self) -> Dict[str, Any]:
        inv = self.store.read_campaign_artifact(
            self.campaign_id, "source_inventory.json")
        if inv is None:
            raise FeatureCampaignError("source inventory not built yet")
        return inv

    def _screen_thresholds(self) -> Dict[str, Any]:
        return {
            k: v for k, v in (self.config.get("ic_screen") or {}).items()
        }

    def _update_manifest(self, **kv: Any) -> None:
        manifest = self.manifest
        manifest.update(kv)
        self.store.write_manifest(self.campaign_id, manifest)

    def _heartbeat(self, **extra: Any) -> None:
        doc = {
            "feature_campaign_id": self.campaign_id,
            "state": self.sm.current_state(),
            "safety": dict(SAFETY_CONTRACT),
            "heartbeat": True,
        }
        doc.update(extra)
        self.store.write_status(self.campaign_id, doc)

    def _set_hypothesis_status(self, hypothesis_id: str, status: str,
                               **extra: Any) -> None:
        latest = self.store.hypotheses(self.campaign_id).get(hypothesis_id)
        if latest is None:
            raise FeatureCampaignError(
                "unknown hypothesis: %s" % hypothesis_id)
        if latest.get("status") == status:
            return
        row = dict(latest)
        row.update(_hyp_snapshot(hypothesis_id, status, **extra))
        self.store.append_hypothesis(self.campaign_id, row)
        self.store.append_event(self.campaign_id, "HYPOTHESIS_LIFECYCLE", {
            "hypothesis_id": hypothesis_id,
            "from_status": latest.get("status"),
            "to_status": status,
        })

    def _set_experiment(self, experiment_id: str, status: str,
                        **extra: Any) -> None:
        latest = self.store.experiments(self.campaign_id).get(experiment_id)
        if latest is None:
            raise FeatureCampaignError(
                "unknown experiment: %s" % experiment_id)
        row = dict(latest)
        row["status"] = status
        row.update(extra)
        self.store.append_experiment(
            self.campaign_id, _experiment_row(row))

    def _skip_hypothesis_experiments(self, hypothesis_id: str,
                                     status: str, reason: str) -> None:
        for eid, row in sorted(self.store.experiments(self.campaign_id).items()):
            if row.get("hypothesis_id") == hypothesis_id and \
                    row.get("status") == "PLANNED":
                self._set_experiment(eid, status, skip_reason=reason)

    # ---- work summary / status -------------------------------------------
    def work_summary(self) -> Dict[str, Any]:
        experiments = self.store.experiments(self.campaign_id)
        by_status: Dict[str, int] = {}
        for row in experiments.values():
            s = row.get("status", "?")
            by_status[s] = by_status.get(s, 0) + 1
        hyp_counts: Dict[str, int] = {}
        for h in self.store.hypotheses(self.campaign_id).values():
            s = h.get("status", "?")
            hyp_counts[s] = hyp_counts.get(s, 0) + 1
        remaining = by_status.get("PLANNED", 0) + by_status.get("RUNNING", 0)
        state = self.manifest.get("current_state", NEW)
        return {
            "planned_total": len(experiments),
            "completed_total": by_status.get("COMPLETE", 0),
            "failed_total": by_status.get("FAILED", 0),
            "skipped_total": sum(
                v for k, v in by_status.items() if k.startswith("SKIPPED")),
            "remaining_total": remaining,
            "experiments_by_status": dict(sorted(by_status.items())),
            "hypothesis_status_counts": dict(sorted(hyp_counts.items())),
            "resumable": state not in TERMINAL_STATES,
            "incomplete": bool(remaining > 0 or state != COMPLETE),
        }

    def status(self) -> Dict[str, Any]:
        manifest = self.manifest
        doc = {
            "feature_campaign_id": self.campaign_id,
            "current_state": manifest.get("current_state"),
            "resume_state": manifest.get("resume_state"),
            "objective": manifest.get("objective"),
            "source_director_session": manifest.get("source_director_session"),
            "source_campaign": manifest.get("source_campaign"),
            "provider": manifest.get("provider"),
            "data_cutoff": manifest.get("data_cutoff"),
            "budgets": manifest.get("budgets"),
            "baseline": {
                k: (manifest.get("baseline") or {}).get(k)
                for k in ("baseline_reproduced", "integration_reproduced",
                          "n_periods")
            },
            "robustness_queue": manifest.get("robustness_queue"),
            "robustness_results": manifest.get("robustness_results"),
            "last_pause": manifest.get("last_pause"),
            "event_chain": self.store.verify_event_chain(self.campaign_id),
            "heartbeat": self.store.read_status(self.campaign_id),
            "safety": dict(SAFETY_CONTRACT),
            "schema_version": FEATURE_CAMPAIGN_SCHEMA_VERSION,
        }
        doc.update(self.work_summary())
        return doc

    def plan(self) -> Dict[str, Any]:
        """Read-only reconciled plan view (no writes, no execution)."""
        experiments = self.store.experiments(self.campaign_id)
        rows = sorted(
            experiments.values(),
            key=lambda r: (r.get("plan_rank") or 10 ** 9, r["experiment_id"]),
        )
        planned = [
            {
                "experiment_id": r["experiment_id"],
                "hypothesis_id": r.get("hypothesis_id"),
                "kind": r.get("kind"),
                "plan_rank": r.get("plan_rank"),
                "status": r.get("status"),
                "execution_condition": r.get("execution_condition"),
            }
            for r in rows
        ]
        totals = {
            "planned_total": len(planned),
            "by_kind": {
                EXPERIMENT_KIND_DIAGNOSTIC: sum(
                    1 for p in planned
                    if p["kind"] == EXPERIMENT_KIND_DIAGNOSTIC),
                EXPERIMENT_KIND_INTEGRATION: sum(
                    1 for p in planned
                    if p["kind"] == EXPERIMENT_KIND_INTEGRATION),
            },
            "budget_max_primary_experiments": (self.manifest.get("budgets")
                                               or {}).get(
                "max_primary_experiments"),
        }
        totals["reconciled"] = (
            totals["by_kind"][EXPERIMENT_KIND_DIAGNOSTIC]
            + totals["by_kind"][EXPERIMENT_KIND_INTEGRATION]
            == totals["planned_total"]
            and totals["planned_total"]
            <= (totals["budget_max_primary_experiments"] or 0)
        )
        return {
            "dry_run": True,
            "executed": False,
            "feature_campaign_id": self.campaign_id,
            "planned": planned,
            "totals": totals,
            "safety": dict(SAFETY_CONTRACT),
        }

    # ---- invocation ledger ------------------------------------------------
    def _begin_invocation(self, max_experiments: Optional[int]) -> Dict[str, Any]:
        rows = self.store.read_invocations(self.campaign_id)
        n = sum(1 for r in rows if r.get("phase") == "START") + 1
        record = {
            "schema_version": FEATURE_CAMPAIGN_SCHEMA_VERSION,
            "record_type": "INVOCATION",
            "invocation_id": "inv_%04d" % n,
            "phase": "START",
            "started_at": _now_iso(),
            "requested_max_experiments": max_experiments,
            "state_before": self.sm.current_state(),
        }
        self.store.append_invocation(self.campaign_id, record)
        self._counters = {"attempted": 0, "completed": 0, "failed": 0}
        return record

    def _end_invocation(self, start: Dict[str, Any],
                        result: Dict[str, Any]) -> Dict[str, Any]:
        record = {
            "schema_version": FEATURE_CAMPAIGN_SCHEMA_VERSION,
            "record_type": "INVOCATION",
            "invocation_id": start["invocation_id"],
            "phase": "END",
            "started_at": start["started_at"],
            "completed_at": _now_iso(),
            "requested_max_experiments": start.get("requested_max_experiments"),
            "experiments_attempted": self._counters["attempted"],
            "experiments_completed": self._counters["completed"],
            "experiments_failed": self._counters["failed"],
            "stop_reason": result.get("pause_reason") or result.get("status"),
            "state_after": self.sm.current_state(),
        }
        self.store.append_invocation(self.campaign_id, record)
        self._update_manifest(last_invocation=record)
        return record

    # ---- main loop --------------------------------------------------------
    def run(self, *, max_experiments: Optional[int] = None) -> Dict[str, Any]:
        self._max_experiments_override = max_experiments
        state = self.sm.current_state()
        if state == COMPLETE:
            return {"status": RUN_ALREADY_COMPLETE,
                    "feature_campaign_id": self.campaign_id,
                    "note": "COMPLETE feature campaigns are never rerun",
                    **self.work_summary()}
        if state == FAILED:
            return {"status": RUN_ALREADY_FAILED,
                    "feature_campaign_id": self.campaign_id,
                    "note": "FAILED campaigns preserve their evidence",
                    **self.work_summary()}
        lock = self.store.lock(self.campaign_id,
                               owner="research_agent.feature_campaign")
        try:
            lock.acquire()
        except CampaignLockedError as exc:
            return {"status": RUN_LOCKED,
                    "feature_campaign_id": self.campaign_id,
                    "detail": str(exc)}
        invocation = self._begin_invocation(max_experiments)
        try:
            result = self._run_locked(lock)
            end = self._end_invocation(invocation, result)
        finally:
            lock.release()
        merged = dict(self.work_summary())
        merged.update(result)
        merged["invocation"] = end
        return merged

    def _run_locked(self, lock: CampaignLock) -> Dict[str, Any]:
        handlers = {
            NEW: self._do_new,
            SOURCE_AUDIT: self._do_source_audit,
            DSL_VALIDATION: self._do_dsl_validation,
            FEATURE_BUILD: self._do_feature_build,
            PIT_VALIDATION: self._do_pit_validation,
            IC_SCREEN: self._do_ic_screen,
            PORTFOLIO_SCREEN: self._do_portfolio_screen,
            PRIMARY_EVALUATION: self._do_primary_evaluation,
            ROBUSTNESS_TESTING: self._do_robustness,
            DIRECTOR_FEEDBACK: self._do_director_feedback,
            REPORTING: self._do_reporting,
        }
        while True:
            state = self.sm.current_state()
            if state == COMPLETE:
                return {"status": RUN_OK,
                        "feature_campaign_id": self.campaign_id,
                        "final_state": state}
            if state == FAILED:
                return {"status": RUN_FAILED,
                        "feature_campaign_id": self.campaign_id,
                        "final_state": state}
            if state in (PAUSED, BLOCKED):
                resume_to = self.sm.resume_state()
                if not resume_to:
                    return {"status": RUN_BLOCKED,
                            "feature_campaign_id": self.campaign_id,
                            "final_state": state,
                            "note": "no recorded resume state"}
                self.sm.transition(resume_to, reason="operator resume")
                continue
            lock.heartbeat()
            self._heartbeat(stage=state)
            outcome = handlers[state]()
            if outcome is not None:
                return outcome

    def _pause(self, pause_reason: str, note: str,
               detail: Dict[str, Any]) -> Dict[str, Any]:
        self.sm.transition(PAUSED, reason=note,
                           detail=dict(detail, pause_reason=pause_reason))
        self._update_manifest(last_pause=dict(
            detail, pause_reason=pause_reason, at=_now_iso()))
        self._heartbeat(stage=PAUSED, pause_reason=pause_reason)
        outcome = dict(self.work_summary())
        outcome.update(detail)
        outcome.update(status=RUN_PAUSED,
                       feature_campaign_id=self.campaign_id,
                       final_state=PAUSED,
                       pause_reason=pause_reason, note=note)
        return outcome

    def _fail(self, reason: str, detail: Dict[str, Any]) -> Dict[str, Any]:
        self.sm.transition(FAILED, reason=reason, detail=detail)
        return {"status": RUN_FAILED,
                "feature_campaign_id": self.campaign_id,
                "final_state": FAILED, "note": reason, **detail}

    def _limit_reached(self) -> bool:
        limit = self._max_experiments_override
        return (limit is not None
                and self._counters["attempted"] >= int(limit))

    # ---- stage handlers ---------------------------------------------------
    def _do_new(self) -> Optional[Dict[str, Any]]:
        self.sm.transition(SOURCE_AUDIT, reason="feature campaign start")
        return None

    def _do_source_audit(self) -> Optional[Dict[str, Any]]:
        inputs = self.inputs()
        inventory = fx.build_source_inventory(inputs)
        self.store.write_campaign_artifact(
            self.campaign_id, "source_inventory.json", inventory)
        pit = fb.validate_point_in_time_integrity(
            inputs,
            seed=int(self.config.get("random_seed", 29)),
            close_frame=self._seam_close_frame,
        )
        if not pit.get("pit_integrity_ok"):
            return self._fail(
                "panel point-in-time integrity failure (fail-fast)",
                {"failed_checks": pit.get("failed_checks")})
        baseline = fb.run_baseline_validation(
            inputs,
            reference_path=(self.manifest.get("data_paths") or {}).get(
                "reference_book_returns"),
            reference_rows=self._seam_reference,
        )
        sim = baseline.pop("sim")
        if not baseline.get("baseline_reproduced"):
            return self._fail(
                "committed baseline could not be reproduced (fail-fast)",
                {"deterministic": baseline.get("deterministic"),
                 "reference_mismatch_count": baseline.get(
                     "reference_mismatch_count")})
        integration_check = fe.verify_baseline_reproduction_via_integration(
            inputs)
        if not integration_check.get("reproduced"):
            return self._fail(
                "integrated engine failed to reproduce the committed baseline "
                "at feature weight 0 (fail-fast)", integration_check)
        metrics = fb.compute_experiment_metrics(
            sim, inputs,
            primary_cost_bps_per_side=float(
                self.config["costs"]["primary_cost_bps_per_side"]))
        metrics["pit_integrity_ok"] = True
        baseline_doc = {
            k: baseline.get(k)
            for k in ("baseline_reproduced", "deterministic",
                      "reference_available", "reference_reproduced",
                      "reference_months_compared", "run_hash", "n_periods")
        }
        baseline_doc["integration_reproduced"] = True
        baseline_doc["metrics"] = metrics
        baseline_doc["pit"] = {
            "pit_integrity_ok": True,
            "checks": [c.get("check") for c in pit.get("checks") or []],
        }
        self.store.write_campaign_artifact(
            self.campaign_id, "baseline_validation.json", baseline_doc)
        self._update_manifest(baseline=baseline_doc)
        self.sm.transition(DSL_VALIDATION,
                           reason="sources audited; baseline reproduced")
        return None

    def _do_dsl_validation(self) -> Optional[Dict[str, Any]]:
        inventory = self._inventory()
        numeric_fields = sorted((inventory.get("numeric_sources") or {}).keys())
        budgets = self.manifest["budgets"]
        for hid in self.manifest.get("accepted_hypotheses") or []:
            hyp = self.store.hypotheses(self.campaign_id).get(hid) or {}
            if hyp.get("status") not in ("ACCEPTED",):
                continue
            features = ((hyp.get("proposal") or {}).get("proposed_feature")
                        or {}).get("features") or []
            verdict = validate_feature_set(
                features,
                available_fields=numeric_fields,
                max_depth=int(budgets["max_feature_depth"]),
                max_interactions=int(budgets["max_interactions"]),
            )
            if not verdict["accepted"]:
                self._set_hypothesis_status(
                    hid, "REJECTED_DSL",
                    reasons=sorted({
                        "%s: %s" % (v["field"], v["issue"])
                        for v in verdict["violations"]})[:8])
                self._skip_hypothesis_experiments(
                    hid, "SKIPPED_DSL", "feature DSL validation failed")
                continue
            compiled = compile_feature_set(
                features,
                available_fields=numeric_fields,
                max_depth=int(budgets["max_feature_depth"]),
                max_interactions=int(budgets["max_interactions"]),
            )
            self.store.write_feature_artifact(
                self.campaign_id, hid, "compiled.json", {
                    "hypothesis_id": hid,
                    "compiled": compiled["steps"],
                    "set_signature": verdict.get("set_signature"),
                })
            self._set_hypothesis_status(hid, "DSL_VALID")
        self.sm.transition(FEATURE_BUILD, reason="feature DSL validated")
        return None

    def _do_feature_build(self) -> Optional[Dict[str, Any]]:
        inventory = self._inventory()
        panel = self.panel()
        for hid, hyp in sorted(self.store.hypotheses(self.campaign_id).items()):
            if hyp.get("status") != "DSL_VALID":
                continue
            if self.store.read_feature_artifact(
                    self.campaign_id, hid, "features.json") is not None:
                continue
            compiled_doc = self.store.read_feature_artifact(
                self.campaign_id, hid, "compiled.json") or {}
            compiled = {"compiled": True, "steps": compiled_doc.get("compiled")}
            execution = fx.execute_feature_set(compiled, panel, inventory)
            if not execution.get("executed"):
                self._set_hypothesis_status(
                    hid, "REJECTED_EXECUTION",
                    reasons=sorted({
                        "%s: %s" % (v["field"], v["issue"])
                        for v in execution.get("violations") or []})[:8])
                self._skip_hypothesis_experiments(
                    hid, "SKIPPED_EXECUTION", "feature execution rejected")
                continue
            self.store.write_feature_artifact(
                self.campaign_id, hid, "features.json", execution)
            self.store.append_event(self.campaign_id, "FEATURE_BUILT", {
                "hypothesis_id": hid,
                "feature_content_hash": execution.get("feature_content_hash"),
                "terminal_feature_id": execution.get("terminal_feature_id"),
            })
        self.sm.transition(PIT_VALIDATION, reason="features built")
        return None

    def _do_pit_validation(self) -> Optional[Dict[str, Any]]:
        inventory = self._inventory()
        panel = self.panel()
        inputs = self.inputs()
        for hid, hyp in sorted(self.store.hypotheses(self.campaign_id).items()):
            if hyp.get("status") != "DSL_VALID":
                continue
            if self.store.read_feature_artifact(
                    self.campaign_id, hid, "pit_audit.json") is not None:
                continue
            execution = self.store.read_feature_artifact(
                self.campaign_id, hid, "features.json")
            if execution is None:
                continue
            compiled_doc = self.store.read_feature_artifact(
                self.campaign_id, hid, "compiled.json") or {}
            compiled = {"compiled": True, "steps": compiled_doc.get("compiled")}
            audit = fx.build_pit_audit(
                compiled, execution, panel, inputs, inventory=inventory)
            self.store.write_feature_artifact(
                self.campaign_id, hid, "pit_audit.json", audit)
            if not audit.get("pit_ok"):
                self._set_hypothesis_status(
                    hid, "REJECTED_PIT", reasons=audit.get("violations"))
                self._skip_hypothesis_experiments(
                    hid, "SKIPPED_PIT",
                    "feature-level point-in-time audit failed")
                self.store.append_event(self.campaign_id, "PIT_BLOCKED", {
                    "hypothesis_id": hid,
                    "violations": audit.get("violations")})
        self.sm.transition(IC_SCREEN, reason="feature PIT audits complete")
        return None

    def _screen_outcome_of(self, hid: str) -> Optional[str]:
        doc = self.store.read_experiment_artifact(
            self.campaign_id, "fexp_%s_diag" % hid, "screen_result.json")
        return (doc or {}).get("outcome")

    def _do_ic_screen(self) -> Optional[Dict[str, Any]]:
        inputs = self.inputs()
        baseline_metrics = (self.manifest.get("baseline") or {}).get("metrics")
        thresholds = self._screen_thresholds()
        rows = sorted(
            self.store.experiments(self.campaign_id).values(),
            key=lambda r: (r.get("plan_rank") or 10 ** 9, r["experiment_id"]))
        for row in rows:
            # RUNNING = interrupted mid-execution; deterministic re-execution
            # is safe because experiment artifacts are immutable-identical.
            if row.get("kind") != EXPERIMENT_KIND_DIAGNOSTIC or \
                    row.get("status") not in ("PLANNED", "RUNNING"):
                continue
            hid = row["hypothesis_id"]
            hyp = self.store.hypotheses(self.campaign_id).get(hid) or {}
            if hyp.get("status") != "DSL_VALID":
                continue
            if self._limit_reached():
                return self._pause(
                    PAUSE_INVOCATION_LIMIT,
                    "per-invocation experiment limit reached; resume to "
                    "continue the remaining supported work",
                    {"invocation_limit": self._max_experiments_override})
            eid = row["experiment_id"]
            self._counters["attempted"] += 1
            self._set_experiment(eid, "RUNNING", started_at=_now_iso())
            execution = self.store.read_feature_artifact(
                self.campaign_id, hid, "features.json")
            terminal = execution.get("terminal_feature_id")
            series = ((execution.get("features") or {}).get(terminal)
                      or {}).get("series") or {}
            resolved = fe.resolve_screen_thresholds(thresholds)
            diag = fe.compute_feature_diagnostics(
                series, inputs, feature_id=terminal,
                min_universe=int(resolved["min_universe"]["value"]))
            screen = fe.run_ic_screen(
                diag, baseline_metrics=baseline_metrics,
                thresholds=thresholds, dsl_ok=True, pit_ok=True)
            self.store.write_experiment_artifact(
                self.campaign_id, eid, "config.json",
                {k: row.get(k) for k in
                 ("experiment_id", "hypothesis_id", "kind", "params",
                  "data_cutoff", "random_seed", "spec_hash")})
            self.store.write_experiment_artifact(
                self.campaign_id, eid, "provenance.json", {
                    "input_provenance": inputs["provenance"],
                    "feature_content_hash": execution.get(
                        "feature_content_hash"),
                    "code_commit": self.manifest.get("code_commit"),
                })
            slim = dict(diag)
            slim["monthly_rows"] = slim.get("monthly_rows") or []
            self.store.write_experiment_artifact(
                self.campaign_id, eid, "diagnostics.json", slim)
            self.store.write_experiment_artifact(
                self.campaign_id, eid, "screen_result.json", screen)
            self._set_experiment(eid, "COMPLETE",
                                 completed_at=_now_iso(),
                                 screen_outcome=screen["outcome"])
            self._counters["completed"] += 1
            self.store.append_event(self.campaign_id, "IC_SCREEN_COMPLETE", {
                "experiment_id": eid, "hypothesis_id": hid,
                "outcome": screen["outcome"]})
            outcome = screen["outcome"]
            if outcome == "ADVANCE_TO_PORTFOLIO_SCREEN":
                self._set_hypothesis_status(hid, "ADVANCED_TO_PORTFOLIO")
            elif outcome == "INCONCLUSIVE":
                self._set_hypothesis_status(hid, "SCREEN_INCONCLUSIVE")
            else:
                self._set_hypothesis_status(
                    hid, "SCREEN_REJECTED", reasons=screen.get("reasons"))
        self.sm.transition(PORTFOLIO_SCREEN, reason="IC screen drained")
        return None

    def _do_portfolio_screen(self) -> Optional[Dict[str, Any]]:
        inputs = self.inputs()
        rows = sorted(
            self.store.experiments(self.campaign_id).values(),
            key=lambda r: (r.get("plan_rank") or 10 ** 9, r["experiment_id"]))
        for row in rows:
            if row.get("kind") != EXPERIMENT_KIND_INTEGRATION or \
                    row.get("status") not in ("PLANNED", "RUNNING"):
                continue
            hid = row["hypothesis_id"]
            outcome = self._screen_outcome_of(hid)
            if outcome != "ADVANCE_TO_PORTFOLIO_SCREEN":
                self._set_experiment(
                    row["experiment_id"], "SKIPPED_SCREEN",
                    skip_reason="ic_screen outcome %s" % outcome)
                continue
            if self._limit_reached():
                return self._pause(
                    PAUSE_INVOCATION_LIMIT,
                    "per-invocation experiment limit reached; resume to "
                    "continue the remaining supported work",
                    {"invocation_limit": self._max_experiments_override})
            eid = row["experiment_id"]
            self._counters["attempted"] += 1
            self._set_experiment(eid, "RUNNING", started_at=_now_iso())
            execution = self.store.read_feature_artifact(
                self.campaign_id, hid, "features.json")
            terminal = execution.get("terminal_feature_id")
            series = ((execution.get("features") or {}).get(terminal)
                      or {}).get("series") or {}
            diag = self.store.read_experiment_artifact(
                self.campaign_id, "fexp_%s_diag" % hid, "diagnostics.json")
            params = dict(row.get("params") or {})
            primary_cost = float(params.pop("primary_cost_bps_per_side"))
            params["feature_orientation"] = int(
                (diag or {}).get("orientation") or 1)
            sim = fe.run_integrated_experiment(inputs, series, params)
            metrics = fb.compute_experiment_metrics(
                sim, inputs, primary_cost_bps_per_side=primary_cost)
            metrics["pit_integrity_ok"] = True
            self.store.write_experiment_artifact(
                self.campaign_id, eid, "config.json",
                {k: row.get(k) for k in
                 ("experiment_id", "hypothesis_id", "kind", "params",
                  "data_cutoff", "random_seed", "spec_hash",
                  "execution_condition")})
            self.store.write_experiment_artifact(
                self.campaign_id, eid, "provenance.json", {
                    "input_provenance": inputs["provenance"],
                    "feature_content_hash": execution.get(
                        "feature_content_hash"),
                    "code_commit": self.manifest.get("code_commit"),
                })
            self.store.write_experiment_artifact(
                self.campaign_id, eid, "metrics.json", {
                    "metrics": metrics,
                    "params": dict(params,
                                   primary_cost_bps_per_side=primary_cost),
                    "n_periods": sim["n_periods"],
                    "feature_fill": sim.get("feature_fill"),
                })
            self._set_experiment(eid, "COMPLETE", completed_at=_now_iso())
            self._counters["completed"] += 1
            self.store.append_event(
                self.campaign_id, "PORTFOLIO_SCREEN_COMPLETE",
                {"experiment_id": eid, "hypothesis_id": hid})
        self.sm.transition(PRIMARY_EVALUATION,
                           reason="portfolio screen drained")
        return None

    def _do_primary_evaluation(self) -> Optional[Dict[str, Any]]:
        baseline_metrics = (self.manifest.get("baseline") or {}).get("metrics")
        if baseline_metrics is None:
            return self._fail("baseline metrics unavailable", {})
        survivors: List[Dict[str, Any]] = []
        for eid, row in sorted(self.store.experiments(self.campaign_id).items()):
            if row.get("kind") != EXPERIMENT_KIND_INTEGRATION or \
                    row.get("status") != "COMPLETE":
                continue
            existing = self.store.read_experiment_artifact(
                self.campaign_id, eid, "decision.json")
            if existing and existing.get("stage") == "primary":
                if existing.get("decision") == ev.RETAIN_FOR_ROBUSTNESS:
                    survivors.append({
                        "experiment_id": eid,
                        "score": (existing.get("score") or {}).get(
                            "final_score", 0.0)})
                continue
            metrics_doc = self.store.read_experiment_artifact(
                self.campaign_id, eid, "metrics.json")
            metrics = dict(metrics_doc["metrics"])
            gates = ev.evaluate_gates(metrics, baseline_metrics,
                                      thresholds=None)
            deltas = ev.build_baseline_deltas(metrics, baseline_metrics)
            decision = ev.decide_candidate(gates, stage=ev.STAGE_PRIMARY,
                                           deltas=deltas)
            score = ev.score_candidate(metrics, baseline_metrics, gates)
            self.store.write_experiment_artifact(
                self.campaign_id, eid, "gate_results.json", gates)
            self.store.write_experiment_artifact(
                self.campaign_id, eid, "baseline_deltas.json", deltas)
            self.store.write_experiment_artifact(
                self.campaign_id, eid, "decision.json", {
                    "stage": "primary",
                    "decision": decision["decision"],
                    "reasons": decision["reasons"],
                    "diagnostic_flags": decision.get("diagnostic_flags"),
                    "stage_policy": decision.get("stage_policy"),
                    "score": score,
                })
            self.store.append_event(self.campaign_id, "CANDIDATE_EVALUATED", {
                "experiment_id": eid, "decision": decision["decision"]})
            hid = row["hypothesis_id"]
            if decision["decision"] == ev.RETAIN_FOR_ROBUSTNESS:
                survivors.append({"experiment_id": eid,
                                  "score": score["final_score"]})
                self._set_hypothesis_status(hid, "RETAINED_FOR_ROBUSTNESS")
            elif decision["decision"] == ev.REJECTED:
                self._set_hypothesis_status(hid, "PRIMARY_REJECTED")
            else:
                self._set_hypothesis_status(hid, "PRIMARY_INCONCLUSIVE")
        budgets = self.manifest["budgets"]
        cap = min(int(budgets["max_retained_candidates"]),
                  int(budgets["max_robustness_candidates"]))
        survivors.sort(key=lambda s: (-s["score"], s["experiment_id"]))
        queue = [s["experiment_id"] for s in survivors[:cap]]
        self._update_manifest(robustness_queue=queue)
        self.store.append_event(self.campaign_id, "ROBUSTNESS_QUEUE_SELECTED", {
            "n_survivors": len(survivors), "queued": queue, "budget": cap})
        self.sm.transition(ROBUSTNESS_TESTING,
                           reason="%d survivor(s) queued" % len(queue))
        return None

    def _do_robustness(self) -> Optional[Dict[str, Any]]:
        inputs = self.inputs()
        baseline_metrics = (self.manifest.get("baseline") or {}).get("metrics")
        results = dict(self.manifest.get("robustness_results") or {})
        for eid in self.manifest.get("robustness_queue") or []:
            if self.store.read_experiment_artifact(
                    self.campaign_id, eid, "robustness_results.json"):
                continue
            row = self.store.experiments(self.campaign_id).get(eid)
            if not row:
                continue
            hid = row["hypothesis_id"]
            execution = self.store.read_feature_artifact(
                self.campaign_id, hid, "features.json")
            terminal = execution.get("terminal_feature_id")
            series = ((execution.get("features") or {}).get(terminal)
                      or {}).get("series") or {}
            metrics_doc = self.store.read_experiment_artifact(
                self.campaign_id, eid, "metrics.json") or {}
            params = dict(metrics_doc.get("params") or {})
            primary_cost = float(params.pop("primary_cost_bps_per_side"))
            diag = self.store.read_experiment_artifact(
                self.campaign_id, "fexp_%s_diag" % hid, "diagnostics.json")
            rb = fe.run_feature_robustness(
                inputs, series, params,
                primary_cost_bps_per_side=primary_cost,
                baseline_metrics=baseline_metrics,
                diagnostics=diag,
            )
            self.store.write_experiment_artifact(
                self.campaign_id, eid, "robustness_results.json", rb)
            results[eid] = {
                "decision": rb["decision"]["decision"],
                "weaknesses": rb["weaknesses"],
                "final_score": rb["score"]["final_score"],
            }
            self.store.append_event(self.campaign_id, "ROBUSTNESS_EVALUATED", {
                "experiment_id": eid,
                "decision": rb["decision"]["decision"]})
            self._set_hypothesis_status(hid, "ROBUSTNESS_COMPLETE")
        self._update_manifest(robustness_results=results)
        # Deliberately NO challenger registration path, whatever the decision.
        self.sm.transition(DIRECTOR_FEEDBACK, reason="robustness drained")
        return None

    def _do_director_feedback(self) -> Optional[Dict[str, Any]]:
        from .director_feedback import build_feedback_packet

        fdir = self.store.feedback_dir(self.campaign_id)
        for hid in self.manifest.get("accepted_hypotheses") or []:
            path = fdir / ("packet_%s.json" % hid)
            if path.exists():
                continue
            packet = build_feedback_packet(self.store, self.campaign_id, hid)
            write_json_atomic(path, packet)
            self.store.append_event(self.campaign_id, "FEEDBACK_PACKET_BUILT", {
                "hypothesis_id": hid,
                "feedback_id": packet.get("feedback_id"),
                "recommendation": packet.get(
                    "recommended_deterministic_next_action")})
        self.sm.transition(REPORTING, reason="feedback packets built")
        return None

    def _do_reporting(self) -> Optional[Dict[str, Any]]:
        pending = [
            r for r in self.store.experiments(self.campaign_id).values()
            if r.get("status") in ("PLANNED", "RUNNING")
        ]
        if pending:
            return self._pause(
                PAUSE_REMAINING_WORK,
                "%d supported planned experiment(s) remain; a bounded "
                "invocation ending is not campaign completion" % len(pending),
                {"remaining_total": len(pending)})
        write_feature_report(self.store, self.campaign_id)
        self.sm.transition(COMPLETE, reason="feature campaign report written")
        self._heartbeat(stage=COMPLETE)
        return None


# --------------------------------------------------------------------------- #
# reporting
# --------------------------------------------------------------------------- #
def build_feature_report(store: FeatureCampaignStore,
                         campaign_id: str) -> Dict[str, Any]:
    manifest = store.read_manifest(campaign_id)
    experiments = store.experiments(campaign_id)
    hyps = store.hypotheses(campaign_id)
    chain = store.verify_event_chain(campaign_id)

    exp_rows = []
    for eid in sorted(experiments):
        row = experiments[eid]
        screen = store.read_experiment_artifact(campaign_id, eid,
                                                "screen_result.json") or {}
        decision = store.read_experiment_artifact(campaign_id, eid,
                                                  "decision.json") or {}
        metrics_doc = store.read_experiment_artifact(campaign_id, eid,
                                                     "metrics.json") or {}
        rb = store.read_experiment_artifact(campaign_id, eid,
                                            "robustness_results.json") or {}
        m = metrics_doc.get("metrics") or {}
        exp_rows.append({
            "experiment_id": eid,
            "hypothesis_id": row.get("hypothesis_id"),
            "kind": row.get("kind"),
            "status": row.get("status"),
            "screen_outcome": screen.get("outcome"),
            "primary_decision": decision.get("decision"),
            "robustness_decision": (rb.get("decision") or {}).get("decision"),
            "net_spy_excess_ann": m.get("net_spy_excess_ann"),
            "rank_ic_t": m.get("rank_ic_t"),
            "turnover_monthly_oneside": m.get("turnover_monthly_oneside"),
            "months": m.get("months"),
            "skip_reason": row.get("skip_reason"),
        })

    counts_by_status: Dict[str, int] = {}
    for r in exp_rows:
        counts_by_status[r["status"]] = counts_by_status.get(r["status"], 0) + 1
    hyp_counts: Dict[str, int] = {}
    for h in hyps.values():
        hyp_counts[h.get("status", "?")] = hyp_counts.get(
            h.get("status", "?"), 0) + 1

    baseline = manifest.get("baseline") or {}
    report = {
        "schema_version": FEATURE_CAMPAIGN_SCHEMA_VERSION,
        "record_type": "FEATURE_CAMPAIGN_REPORT",
        "feature_campaign_id": campaign_id,
        "generated_at": _now_iso(),
        "objective": manifest.get("objective"),
        "current_state": manifest.get("current_state"),
        "source_director_session": manifest.get("source_director_session"),
        "source_campaign": manifest.get("source_campaign"),
        "provider": manifest.get("provider"),
        "code_commit": manifest.get("code_commit"),
        "data_cutoff": manifest.get("data_cutoff"),
        "dataset_hashes": manifest.get("dataset_hashes"),
        "event_chain_intact": chain.get("intact"),
        "budgets": manifest.get("budgets"),
        "baseline": {
            "baseline_reproduced": baseline.get("baseline_reproduced"),
            "integration_reproduced": baseline.get("integration_reproduced"),
            "metrics": {
                k: (baseline.get("metrics") or {}).get(k)
                for k in ("net_spy_excess_ann", "rank_ic_mean", "rank_ic_t",
                          "turnover_monthly_oneside", "max_drawdown", "months")
            },
        },
        "experiments": exp_rows,
        "experiments_by_status": dict(sorted(counts_by_status.items())),
        "hypothesis_status_counts": dict(sorted(hyp_counts.items())),
        "robustness_results": manifest.get("robustness_results") or {},
        "shadow_eligible": sorted(
            eid for eid, r in (manifest.get("robustness_results")
                               or {}).items()
            if r.get("decision") == ev.SHADOW_ELIGIBLE),
        "challengers_registered": [],
        "challenger_registration_note": "the feature campaign has no "
        "registration capability; SHADOW_ELIGIBLE evidence requires the "
        "Phase 29A controller path plus human approval",
        "safety": dict(SAFETY_CONTRACT),
    }
    return report


def _render_feature_report_md(report: Dict[str, Any]) -> str:
    lines = [
        "# Feature Campaign Report — %s" % report["feature_campaign_id"],
        "",
        "State: **%s** | provider: %s | source session: %s | cutoff: %s"
        % (report.get("current_state"), report.get("provider"),
           report.get("source_director_session"), report.get("data_cutoff")),
        "",
        "## Safety contract",
    ]
    for k, v in (report.get("safety") or {}).items():
        lines.append("- %s = %s" % (k, str(v).lower()))
    lines += [
        "",
        "## Baseline",
        "- reproduced: %s | integrated-engine reproduction: %s"
        % ((report.get("baseline") or {}).get("baseline_reproduced"),
           (report.get("baseline") or {}).get("integration_reproduced")),
        "- metrics: %s" % (report.get("baseline") or {}).get("metrics"),
        "",
        "## Experiments",
    ]
    for r in report.get("experiments") or []:
        lines.append(
            "- %s [%s/%s] screen=%s primary=%s robustness=%s ic_t=%s"
            % (r["experiment_id"], r["kind"], r["status"],
               r.get("screen_outcome"), r.get("primary_decision"),
               r.get("robustness_decision"), r.get("rank_ic_t")))
    lines += [
        "",
        "## Hypotheses",
        "- %s" % report.get("hypothesis_status_counts"),
        "",
        "_Research-only. No order, broker action, trading automation, shadow "
        "activation, challenger registration or operational-model change was "
        "created by this campaign._",
        "",
    ]
    return "\n".join(lines)


def write_feature_report(store: FeatureCampaignStore,
                         campaign_id: str) -> Dict[str, Any]:
    report = build_feature_report(store, campaign_id)
    rdir = store.campaign_dir(campaign_id) / "reports"
    json_path = rdir / "feature_campaign_report.json"
    md_path = rdir / "feature_campaign_report.md"
    write_json_atomic(json_path, report)
    write_text_atomic(md_path, _render_feature_report_md(report))
    return {"report": report,
            "artifact_paths": [str(json_path), str(md_path)]}


__all__ = [
    "ALLOWED_TRANSITIONS",
    "EXPERIMENT_KIND_DIAGNOSTIC",
    "EXPERIMENT_KIND_INTEGRATION",
    "EXPERIMENT_STATUSES",
    "FEATURE_CAMPAIGN_SCHEMA_VERSION",
    "FEATURE_EXPERIMENT_ARTIFACTS",
    "FEATURE_STATES",
    "FeatureCampaignController",
    "FeatureCampaignError",
    "FeatureCampaignStore",
    "FeatureStateMachine",
    "HYPOTHESIS_STATUSES",
    "InvalidFeatureTransitionError",
    "PHASE29C_BUDGET_CEILINGS",
    "TERMINAL_STATES",
    "build_feature_report",
    "create_feature_campaign",
    "register_challenger",
    "validate_feature_campaign_config",
    "write_feature_report",
]
