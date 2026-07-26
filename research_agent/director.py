"""Phase 29B LLM research-director layer.

The director sits ABOVE the deterministic Phase 29A engine and never below
it: it reads structured campaign evidence, proposes bounded feature-level
hypotheses through the strict DSL, and records decisions — while the
deterministic controller, schemas, evaluators, tools, gates, artifact store
and challenger registry remain the only authorities. The director:

- never performs financial calculations,
- never executes generated code,
- never touches the operational model, and
- can NEVER register a challenger (``register_challenger`` here always
  raises; registration belongs to the campaign controller's strict gates).

Every provider response is treated as untrusted data and re-validated by
the deterministic policy in this module regardless of provider.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import SAFETY_CONTRACT
from .artifact_store import (
    ArtifactStore,
    ImmutableArtifactError,
    _now_iso,
    append_jsonl,
    content_hash,
    find_secret_keys,
    read_json,
    read_jsonl,
    write_json_atomic,
    write_text_atomic,
)
from .director_provider import (
    PROVIDER_NAMES,
    STATUS_OK,
    ResearchDirectorProvider,
)
from .evidence_pack import (
    ALLOWED_DIRECTOR_TOOLS,
    PHASE29B_BUDGET_CEILINGS,
    build_evidence_pack,
)
from .feature_dsl import (
    _is_safe_identifier,
    feature_set_signature,
    validate_feature_set,
)
from .prompt_templates import build_director_request
from .schemas import find_forbidden_execution_keys

DIRECTOR_SCHEMA_VERSION = "29B.1"

EVIDENCE_PACKS_DIR = "evidence_packs"
SESSIONS_DIR = "director_sessions"
GRAVEYARD_LEDGER = "director_graveyard.jsonl"
SESSION_MANIFEST = "session.json"
SESSION_EVENTS = "events.jsonl"
SESSION_PROPOSALS = "proposals.jsonl"
SESSION_DECISIONS = "decisions.jsonl"
SESSION_REQUEST = "request.json"
SESSION_RESPONSE = "response.json"
SESSION_REPORTS_DIR = "reports"

# ---- Part C: hypothesis proposal contract --------------------------------- #
HYPOTHESIS_PROPOSAL_STATUSES = (
    "PROPOSED",
    "VALIDATED",
    "REJECTED_SCHEMA",
    "REJECTED_DUPLICATE",
    "REJECTED_UNSUPPORTED",
    "REJECTED_LEAKAGE_RISK",
    "QUEUED",
    "ACTIVE",
    "PARTIALLY_TESTED",
    "TESTED_NO_SURVIVOR",
    "RETAINED_FOR_ROBUSTNESS",
    "ROBUSTNESS_COMPLETE",
    "EXHAUSTED",
    "BLOCKED",
)

REQUIRED_PROPOSAL_FIELDS = (
    "hypothesis_id",
    "title",
    "diagnosis",
    "supporting_evidence",
    "proposed_feature",
    "expected_mechanism",
    "expected_metric_improvement",
    "falsification_condition",
    "leakage_analysis",
    "required_tools",
    "estimated_compute_cost",
    "priority",
    "parent_hypothesis",
    "duplicate_search_signature",
    "stop_condition",
    "follow_up_policy",
)

COST_CLASSES = ("cheap", "medium", "expensive")
_COST_RANK = {"cheap": 0, "medium": 1, "expensive": 2}

# ---- Part F: decision contract -------------------------------------------- #
DIRECTOR_DECISIONS = (
    "TEST",
    "REVISE",
    "REJECT",
    "STOP_BRANCH",
    "REQUEST_ROBUSTNESS",
    "COMPLETE_RESEARCH_CYCLE",
)
FORBIDDEN_DIRECTOR_DECISIONS = (
    "SHADOW_ACTIVE",
    "PROMOTION_CANDIDATE",
    "OPERATIONAL",
    "CREATE_ORDER",
    "EXECUTE_TRADE",
    "CHANGE_ACTIVE_MODEL",
    "LOWER_GATE",
)
REQUIRED_DECISION_FIELDS = (
    "decision",
    "evidence_refs",
    "reasoning_summary",
    "uncertainties",
    "falsification_condition",
    "next_deterministic_action",
    "budget_impact",
    "safety_confirmation",
)
OPTIONAL_DECISION_FIELDS = ("hypothesis_id", "notes")
REQUIRED_SAFETY_CONFIRMATIONS = (
    "research_only",
    "no_operational_action",
    "no_gate_change",
    "no_budget_increase",
)

# ---- Part G: strict response contract ------------------------------------- #
ALLOWED_RESPONSE_FIELDS = (
    "schema_version", "provider", "status", "proposals", "decisions", "notes",
)
# Fields whose presence in a response is an attempt to alter contract-
# protected state; called out explicitly rather than as a generic unknown.
CONTRACT_PROTECTED_RESPONSE_FIELDS = (
    "safety", "safety_contract", "budgets", "budget_override", "thresholds",
    "gates", "gate_overrides", "data_cutoff", "baseline", "baseline_model",
    "operational_model", "stop_conditions",
)
# Tokens that mark an operational-action request wherever they appear as a
# requested tool or decision.
FORBIDDEN_ACTION_TOKENS = frozenset(
    t.lower() for t in FORBIDDEN_DIRECTOR_DECISIONS
) | {"register_shadow_challenger", "broker", "order", "trade"}

REQUIRED_DIRECTOR_CONFIG_FIELDS = (
    "schema_version",
    "name",
    "objective",
    "primary_target",
    "secondary_constraints",
    "exhausted_dimensions",
    "budgets",
    "provider",
    "strict_mode",
    "safety",
)

GRAVEYARD_REASONS = (
    "duplicate_of_exhausted_branch",
    "unresolvable_leakage_risk",
    "required_feature_unavailable",
    "repeated_failure",
    "branch_exhausted",
)


class DirectorError(RuntimeError):
    pass


class DirectorSafetyError(DirectorError):
    pass


class DirectorStoreError(DirectorError):
    pass


def register_challenger(*_args: Any, **_kwargs: Any) -> None:
    """The director can never register challengers. Always raises.

    Challenger registration belongs exclusively to the deterministic
    campaign controller and requires the strict, unlowered SHADOW_ELIGIBLE
    gates plus explicit human approval beyond that. No provider — fixture
    or live — can reach a registry through the director layer.
    """
    raise DirectorSafetyError(
        "the research director can never register challengers; registration "
        "requires the deterministic controller's strict SHADOW_ELIGIBLE gates "
        "and human approval"
    )


def _violation(field: str, issue: str, value: Any, severity: str = "INVALID"):
    return {"field": field, "issue": issue, "value": value, "severity": severity}


def _is_int(x: Any) -> bool:
    return isinstance(x, int) and not isinstance(x, bool)


def _is_nonempty_str(x: Any) -> bool:
    return isinstance(x, str) and bool(x.strip())


# --------------------------------------------------------------------------- #
# director config validation
# --------------------------------------------------------------------------- #
def validate_director_config(cfg: Any) -> Dict[str, Any]:
    violations: List[dict] = []
    if not isinstance(cfg, dict):
        return {"accepted": False, "config_hash": None,
                "violations": [_violation("$", "config must be a JSON object",
                                          type(cfg).__name__)]}
    for kp in find_forbidden_execution_keys(cfg):
        violations.append(_violation(
            kp, "executable-content key is forbidden (no arbitrary code)", None))
    for f in REQUIRED_DIRECTOR_CONFIG_FIELDS:
        if f not in cfg:
            violations.append(_violation(f, "required field missing", None))

    if cfg.get("strict_mode") is not True:
        violations.append(_violation(
            "strict_mode", "must be true in Phase 29B (unknown response "
            "fields are always rejected)", cfg.get("strict_mode")))

    budgets = cfg.get("budgets")
    if isinstance(budgets, dict):
        for name, ceiling in PHASE29B_BUDGET_CEILINGS.items():
            v = budgets.get(name)
            if not _is_int(v) or v < 0:
                violations.append(_violation(
                    "budgets.%s" % name, "must be a non-negative integer", v))
            elif v > ceiling:
                violations.append(_violation(
                    "budgets.%s" % name,
                    "exceeds the Phase 29B hard ceiling %d (budgets may be "
                    "lowered by the operator, never raised)" % ceiling, v))
        for name in sorted(set(budgets) - set(PHASE29B_BUDGET_CEILINGS)):
            violations.append(_violation(
                "budgets.%s" % name, "unknown budget (strict schema)",
                budgets.get(name)))
    elif budgets is not None:
        violations.append(_violation("budgets", "must be an object", budgets))

    pt = cfg.get("primary_target")
    if isinstance(pt, dict):
        metrics = pt.get("metrics")
        if not isinstance(metrics, list) or not metrics:
            violations.append(_violation(
                "primary_target.metrics", "must be a non-empty list", metrics))
    elif pt is not None:
        violations.append(_violation("primary_target", "must be an object", pt))

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
                "safety.no_operational_promotion",
                "must be true (hard safety rule)",
                safety.get("no_operational_promotion")))
        if safety.get("fixture_provider_may_register_challengers") is not False:
            violations.append(_violation(
                "safety.fixture_provider_may_register_challengers",
                "must be false (the fixture provider can never lead to real "
                "challenger registration)",
                safety.get("fixture_provider_may_register_challengers")))
    elif safety is not None:
        violations.append(_violation("safety", "must be an object", safety))

    exh = cfg.get("exhausted_dimensions")
    if exh is not None and not isinstance(exh, list):
        violations.append(_violation("exhausted_dimensions", "must be a list", exh))

    accepted = not violations
    return {
        "accepted": accepted,
        "violations": violations,
        "config_hash": content_hash(cfg) if accepted else None,
        "schema_version": DIRECTOR_SCHEMA_VERSION,
    }


# --------------------------------------------------------------------------- #
# Part C: proposal validation
# --------------------------------------------------------------------------- #
def validate_proposal(
    proposal: Any,
    *,
    available_fields: Sequence[str],
    max_feature_depth: int,
    max_interactions: int,
    max_primary_experiments: int,
    strict: bool = True,
) -> Dict[str, Any]:
    """Deterministic Part C validation of one LLM-proposed hypothesis.

    Returns {status, reasons, violations, signature, dsl_verdict}. The
    status is None when the proposal is schema-valid (the policy layer then
    applies duplicate/budget checks) or one of the REJECTED_* statuses.
    """
    violations: List[dict] = []
    if not isinstance(proposal, dict):
        return {"status": "REJECTED_SCHEMA",
                "reasons": ["proposal must be a JSON object"],
                "violations": [_violation("$", "must be a JSON object",
                                          type(proposal).__name__)],
                "signature": None, "dsl_verdict": None}

    for kp in find_forbidden_execution_keys(proposal):
        violations.append(_violation(
            kp, "executable-content key is forbidden", None, severity="SAFETY"))

    for f in REQUIRED_PROPOSAL_FIELDS:
        if f not in proposal:
            violations.append(_violation(f, "required field missing", None))
    if strict:
        for f in sorted(set(proposal) - set(REQUIRED_PROPOSAL_FIELDS)):
            violations.append(_violation(
                f, "unknown field (strict schema)", proposal.get(f)))

    if "hypothesis_id" in proposal and not _is_safe_identifier(
            proposal.get("hypothesis_id"), 96):
        violations.append(_violation(
            "hypothesis_id", "must be a plain identifier",
            proposal.get("hypothesis_id")))
    for f in ("title", "diagnosis", "expected_mechanism",
              "falsification_condition", "leakage_analysis",
              "stop_condition", "follow_up_policy"):
        if f in proposal and not _is_nonempty_str(proposal.get(f)):
            violations.append(_violation(f, "must be a non-empty string",
                                         proposal.get(f)))
    se = proposal.get("supporting_evidence")
    if "supporting_evidence" in proposal and (
            not isinstance(se, list) or not se
            or not all(isinstance(x, str) for x in se)):
        violations.append(_violation(
            "supporting_evidence", "must be a non-empty list of evidence "
            "references", se))

    emi = proposal.get("expected_metric_improvement")
    if "expected_metric_improvement" in proposal:
        if not isinstance(emi, dict):
            violations.append(_violation(
                "expected_metric_improvement", "must be an object", emi))
        else:
            for f in sorted(set(emi) - {"metric", "direction", "material_threshold"}):
                violations.append(_violation(
                    "expected_metric_improvement.%s" % f, "unknown field", emi.get(f)))
            if not _is_nonempty_str(emi.get("metric")):
                violations.append(_violation(
                    "expected_metric_improvement.metric",
                    "must name the target metric", emi.get("metric")))
            if emi.get("direction") not in ("increase", "decrease"):
                violations.append(_violation(
                    "expected_metric_improvement.direction",
                    "must be increase or decrease", emi.get("direction")))

    tools = proposal.get("required_tools")
    if "required_tools" in proposal:
        if not isinstance(tools, list) or not tools:
            violations.append(_violation(
                "required_tools", "must be a non-empty list of deterministic "
                "tools", tools))
        else:
            for t in tools:
                tl = str(t).strip().lower()
                if tl in FORBIDDEN_ACTION_TOKENS or any(
                        tok in tl for tok in ("order", "broker", "trade",
                                              "register", "promote",
                                              "activate")):
                    violations.append(_violation(
                        "required_tools", "operational action requested: the "
                        "director may never create orders, touch a broker, "
                        "register challengers, activate shadows or promote "
                        "models", t, severity="SAFETY"))
                elif t not in ALLOWED_DIRECTOR_TOOLS:
                    violations.append(_violation(
                        "required_tools", "unknown deterministic tool", t))

    ecc = proposal.get("estimated_compute_cost")
    if "estimated_compute_cost" in proposal:
        if not isinstance(ecc, dict):
            violations.append(_violation(
                "estimated_compute_cost", "must be an object", ecc))
        else:
            if ecc.get("cost_class") not in COST_CLASSES:
                violations.append(_violation(
                    "estimated_compute_cost.cost_class",
                    "must be one of %s" % (COST_CLASSES,), ecc.get("cost_class")))
            ee = ecc.get("est_experiments")
            if not _is_int(ee) or ee < 1:
                violations.append(_violation(
                    "estimated_compute_cost.est_experiments",
                    "must be a positive integer", ee))
            elif ee > max_primary_experiments:
                violations.append(_violation(
                    "estimated_compute_cost.est_experiments",
                    "exceeds the primary experiment budget %d"
                    % max_primary_experiments, ee))

    if "priority" in proposal and (
            not _is_int(proposal.get("priority")) or proposal["priority"] < 1):
        violations.append(_violation(
            "priority", "must be a positive integer (1 = highest)",
            proposal.get("priority")))
    ph = proposal.get("parent_hypothesis")
    if "parent_hypothesis" in proposal and ph is not None and not _is_safe_identifier(ph, 96):
        violations.append(_violation(
            "parent_hypothesis", "must be null or a plain identifier", ph))
    sig_field = proposal.get("duplicate_search_signature")
    if "duplicate_search_signature" in proposal and not _is_nonempty_str(sig_field):
        violations.append(_violation(
            "duplicate_search_signature", "must be a non-empty string", sig_field))

    dsl_verdict = None
    features: List[Dict[str, Any]] = []
    pf = proposal.get("proposed_feature")
    if "proposed_feature" in proposal:
        if not isinstance(pf, dict) or set(pf) != {"features"}:
            violations.append(_violation(
                "proposed_feature", "must be an object with exactly one key: "
                "features (a list of DSL feature specs)", pf))
        else:
            features = pf.get("features") or []
            dsl_verdict = validate_feature_set(
                features,
                available_fields=available_fields,
                max_depth=max_feature_depth,
                max_interactions=max_interactions,
            )

    # classification precedence: safety/schema > leakage > unsupported
    if any(v["severity"] == "SAFETY" for v in violations):
        return {"status": "REJECTED_SCHEMA",
                "reasons": sorted({"%s: %s" % (v["field"], v["issue"])
                                   for v in violations if v["severity"] == "SAFETY"}),
                "violations": violations, "signature": None,
                "dsl_verdict": dsl_verdict}
    if violations:
        return {"status": "REJECTED_SCHEMA",
                "reasons": sorted({"%s: %s" % (v["field"], v["issue"])
                                   for v in violations})[:8],
                "violations": violations, "signature": None,
                "dsl_verdict": dsl_verdict}
    if dsl_verdict is not None and not dsl_verdict["accepted"]:
        if dsl_verdict["leakage_violations"]:
            status = "REJECTED_LEAKAGE_RISK"
        elif dsl_verdict["unsupported_violations"] and all(
                v["severity"] in ("UNSUPPORTED", "LEAKAGE")
                for v in dsl_verdict["violations"]):
            status = "REJECTED_UNSUPPORTED"
        else:
            status = "REJECTED_SCHEMA"
        return {"status": status,
                "reasons": sorted({"%s: %s" % (v["field"], v["issue"])
                                   for v in dsl_verdict["violations"]})[:8],
                "violations": dsl_verdict["violations"], "signature": None,
                "dsl_verdict": dsl_verdict}

    return {
        "status": None,  # schema-valid; policy applies dedup/budget next
        "reasons": [],
        "violations": [],
        "signature": feature_set_signature(features),
        "dsl_verdict": dsl_verdict,
    }


# --------------------------------------------------------------------------- #
# Part F: decision validation
# --------------------------------------------------------------------------- #
def validate_decision(decision: Any, *, strict: bool = True) -> Dict[str, Any]:
    violations: List[dict] = []
    if not isinstance(decision, dict):
        return {"accepted": False,
                "violations": [_violation("$", "decision must be a JSON object",
                                          type(decision).__name__)]}
    d = decision.get("decision")
    if isinstance(d, str) and d.strip().upper() in FORBIDDEN_DIRECTOR_DECISIONS:
        violations.append(_violation(
            "decision", "forbidden decision: the director may never activate "
            "shadows, promote candidates, create orders, execute trades, "
            "change the active model, or lower a gate", d, severity="SAFETY"))
    elif d not in DIRECTOR_DECISIONS:
        violations.append(_violation(
            "decision", "must be one of %s" % (DIRECTOR_DECISIONS,), d))
    for f in REQUIRED_DECISION_FIELDS:
        if f not in decision:
            violations.append(_violation(f, "required field missing", None))
    if strict:
        allowed = set(REQUIRED_DECISION_FIELDS) | set(OPTIONAL_DECISION_FIELDS)
        for f in sorted(set(decision) - allowed):
            violations.append(_violation(
                f, "unknown field (strict schema)", decision.get(f)))
    bi = decision.get("budget_impact")
    if "budget_impact" in decision and not isinstance(bi, dict):
        violations.append(_violation("budget_impact", "must be an object", bi))
    sc = decision.get("safety_confirmation")
    if "safety_confirmation" in decision:
        if not isinstance(sc, dict):
            violations.append(_violation(
                "safety_confirmation", "must be an object", sc))
        else:
            for f in REQUIRED_SAFETY_CONFIRMATIONS:
                if sc.get(f) is not True:
                    violations.append(_violation(
                        "safety_confirmation.%s" % f,
                        "must be explicitly true", sc.get(f),
                        severity="SAFETY"))
    return {"accepted": not violations, "violations": violations}


# --------------------------------------------------------------------------- #
# Part E: director policy
# --------------------------------------------------------------------------- #
def _normalize_signature(sig: Any) -> Optional[str]:
    if not isinstance(sig, str) or not sig.strip():
        return None
    return sig.strip().lower()


def information_gain_rank_key(proposal_row: Dict[str, Any]) -> Tuple:
    """Deterministic ranking: cheap diagnostics and ablations first.

    Sort key (ascending): interaction-bearing after pure single-signal
    ablations, then declared priority, then cost class, then id.
    """
    p = proposal_row["proposal"]
    interactions = (proposal_row.get("dsl_verdict") or {}).get(
        "total_interactions", 0)
    cost = _COST_RANK.get(
        (p.get("estimated_compute_cost") or {}).get("cost_class"), 1)
    return (
        1 if interactions else 0,
        int(p.get("priority", 99)),
        cost,
        str(p.get("hypothesis_id", "")),
    )


class DirectorPolicy:
    """Deterministic evaluation of one provider response (Parts C/E/F/G)."""

    def __init__(
        self,
        director_config: Dict[str, Any],
        evidence_pack: Dict[str, Any],
        *,
        graveyard: Optional[List[Dict[str, Any]]] = None,
        prior_signatures: Optional[Sequence[str]] = None,
    ):
        self.config = director_config
        self.pack = evidence_pack
        self.budgets = dict(director_config.get("budgets") or {})
        self.strict = director_config.get("strict_mode", True)
        feats = evidence_pack.get("available_features") or {}
        self.available_fields = sorted(
            set(feats.get("momentum_sources") or [])
            | set(feats.get("fundamental_sources") or [])
        )
        self.exhausted_signatures = {
            s for s in (
                _normalize_signature(x)
                for x in evidence_pack.get("exhausted_signatures") or []
            ) if s
        }
        self.graveyard_signatures = {
            s for s in (
                _normalize_signature(g.get("signature"))
                for g in (graveyard or [])
            ) if s
        }
        self.prior_signatures = {
            s for s in (
                _normalize_signature(x) for x in (prior_signatures or [])
            ) if s
        }

    # ---- response-level (Part G) -----------------------------------------
    def validate_response_shape(self, response: Any) -> List[dict]:
        violations: List[dict] = []
        if not isinstance(response, dict):
            return [_violation("$", "response must be a single JSON object",
                               type(response).__name__)]
        secret_hits = find_secret_keys(response)
        if secret_hits:
            violations.append(_violation(
                "$", "secret-looking keys are refused", sorted(secret_hits),
                severity="SAFETY"))
        for k in sorted(set(response) - set(ALLOWED_RESPONSE_FIELDS)):
            if k in CONTRACT_PROTECTED_RESPONSE_FIELDS:
                violations.append(_violation(
                    k, "the director may not alter contract-protected state "
                    "(safety flags, gates, thresholds, budgets, data cutoff, "
                    "baseline identity, operational model)", None,
                    severity="SAFETY"))
            else:
                violations.append(_violation(
                    k, "unknown field (strict schema)", None))
        if not isinstance(response.get("proposals"), list):
            violations.append(_violation(
                "proposals", "must be a list of hypothesis proposals",
                response.get("proposals")))
        if "decisions" in response and not isinstance(
                response.get("decisions"), list):
            violations.append(_violation(
                "decisions", "must be a list", response.get("decisions")))
        return violations

    # ---- full processing --------------------------------------------------
    def process_response(self, response: Any) -> Dict[str, Any]:
        response_violations = self.validate_response_shape(response)
        if response_violations:
            return {
                "response_valid": False,
                "response_violations": response_violations,
                "proposals": [],
                "queued": [],
                "decisions": [],
                "graveyard_candidates": [],
                "budget_usage": {"proposed": 0, "queued": 0,
                                 "est_primary_experiments": 0},
            }

        max_proposed = int(self.budgets.get("max_proposed_hypotheses", 12))
        max_accepted = int(self.budgets.get("max_accepted_hypotheses", 6))
        max_primary = int(self.budgets.get("max_primary_experiments", 24))
        max_depth = int(self.budgets.get("max_feature_depth", 3))
        max_inter = int(self.budgets.get("max_interactions", 4))

        rows: List[Dict[str, Any]] = []
        seen_now: set = set()
        graveyard_candidates: List[Dict[str, Any]] = []

        for idx, proposal in enumerate(response.get("proposals") or []):
            hid = (proposal.get("hypothesis_id")
                   if isinstance(proposal, dict) else None) or "proposal_%d" % idx
            if idx >= max_proposed:
                rows.append({
                    "hypothesis_id": hid, "index": idx,
                    "status": "REJECTED_SCHEMA",
                    "reasons": ["response exceeded max_proposed_hypotheses "
                                "(%d); budgets are contract-protected"
                                % max_proposed],
                    "signature": None, "proposal": proposal,
                    "dsl_verdict": None,
                })
                continue
            verdict = validate_proposal(
                proposal,
                available_fields=self.available_fields,
                max_feature_depth=max_depth,
                max_interactions=max_inter,
                max_primary_experiments=max_primary,
                strict=bool(self.strict),
            )
            row = {
                "hypothesis_id": hid,
                "index": idx,
                "status": verdict["status"],
                "reasons": verdict["reasons"],
                "signature": verdict["signature"],
                "declared_signature": (proposal.get("duplicate_search_signature")
                                       if isinstance(proposal, dict) else None),
                "proposal": proposal,
                "dsl_verdict": verdict["dsl_verdict"],
            }
            if verdict["status"] is None:
                sigs = {s for s in (
                    _normalize_signature(row["signature"]),
                    _normalize_signature(row["declared_signature"]),
                ) if s}
                dup_source = None
                if sigs & self.exhausted_signatures:
                    dup_source = "exhausted_dimension"
                elif sigs & self.graveyard_signatures:
                    dup_source = "graveyard"
                elif sigs & self.prior_signatures:
                    dup_source = "prior_session_proposal"
                elif sigs & seen_now:
                    dup_source = "earlier_proposal_in_response"
                if dup_source:
                    row["status"] = "REJECTED_DUPLICATE"
                    row["reasons"] = [
                        "duplicate-search signature matches %s" % dup_source]
                    if dup_source == "exhausted_dimension":
                        graveyard_candidates.append({
                            "signature": sorted(sigs)[0],
                            "reason": "duplicate_of_exhausted_branch",
                            "hypothesis_id": hid,
                        })
                else:
                    row["status"] = "VALIDATED"
                    seen_now |= sigs
            elif verdict["status"] == "REJECTED_LEAKAGE_RISK":
                graveyard_candidates.append({
                    "signature": _normalize_signature(row["declared_signature"])
                    or ("proposal:" + content_hash(proposal)[:20]),
                    "reason": "unresolvable_leakage_risk",
                    "hypothesis_id": hid,
                })
            elif verdict["status"] == "REJECTED_UNSUPPORTED":
                graveyard_candidates.append({
                    "signature": _normalize_signature(row["declared_signature"])
                    or ("proposal:" + content_hash(proposal)[:20]),
                    "reason": "required_feature_unavailable",
                    "hypothesis_id": hid,
                })
            rows.append(row)

        # deterministic information-gain ranking of the validated proposals
        validated = [r for r in rows if r["status"] == "VALIDATED"]
        validated.sort(key=information_gain_rank_key)
        queued: List[str] = []
        est_total = 0
        for r in validated:
            est = int(((r["proposal"].get("estimated_compute_cost") or {})
                       .get("est_experiments") or 1))
            if len(queued) >= max_accepted:
                r["queue_note"] = ("validated but not queued: "
                                   "max_accepted_hypotheses reached")
                continue
            if est_total + est > max_primary:
                r["queue_note"] = ("validated but not queued: primary "
                                   "experiment budget would be exceeded")
                continue
            r["status"] = "QUEUED"
            est_total += est
            queued.append(r["hypothesis_id"])
        for rank, r in enumerate(validated, start=1):
            r["information_gain_rank"] = rank

        decisions_out: List[Dict[str, Any]] = []
        for i, dec in enumerate(response.get("decisions") or []):
            verdict = validate_decision(dec, strict=bool(self.strict))
            decisions_out.append({
                "index": i,
                "decision": dec.get("decision") if isinstance(dec, dict) else None,
                "hypothesis_id": (dec.get("hypothesis_id")
                                  if isinstance(dec, dict) else None),
                "accepted": verdict["accepted"],
                "violations": verdict["violations"],
                "record": dec,
            })

        return {
            "response_valid": True,
            "response_violations": [],
            "proposals": rows,
            "queued": queued,
            "decisions": decisions_out,
            "graveyard_candidates": graveyard_candidates,
            "budget_usage": {
                "proposed": len(response.get("proposals") or []),
                "validated": len(validated),
                "queued": len(queued),
                "est_primary_experiments": est_total,
                "budgets": dict(self.budgets),
            },
        }


# --------------------------------------------------------------------------- #
# Part H: append-only director store (sessions, packs, graveyard)
# --------------------------------------------------------------------------- #
class DirectorStore:
    """Filesystem store for director sessions under one output root.

    Reuses the ArtifactStore construction guard (the output root may never
    live inside a git checkout) and the same append-only, chain-hashed
    ledger discipline for session events.
    """

    def __init__(self, output_root: str):
        self._guard = ArtifactStore(output_root)  # validates the root
        self.root = Path(output_root)
        self.packs_dir = self.root / EVIDENCE_PACKS_DIR
        self.sessions_root = self.root / SESSIONS_DIR
        self.graveyard_path = self.root / GRAVEYARD_LEDGER

    # ---- evidence packs ---------------------------------------------------
    def evidence_pack_path(self, pack_id: str) -> Path:
        return self.packs_dir / ("%s.json" % pack_id)

    def save_evidence_pack(self, pack: Dict[str, Any]) -> str:
        pack_id = pack.get("evidence_pack_id")
        if not isinstance(pack_id, str) or not pack_id.startswith("ep_"):
            raise DirectorStoreError("evidence pack has no valid id")
        path = self.evidence_pack_path(pack_id)
        if path.exists():
            existing = read_json(path)
            if content_hash(existing) == content_hash(pack):
                return pack_id  # identical rewrite: idempotent no-op
            raise ImmutableArtifactError(
                "evidence pack %s already exists and differs; packs are "
                "immutable" % pack_id)
        write_json_atomic(path, pack)
        return pack_id

    def load_evidence_pack(self, pack_id: str) -> Dict[str, Any]:
        path = self.evidence_pack_path(pack_id)
        if not path.exists():
            raise DirectorStoreError("unknown evidence pack: %s" % pack_id)
        return read_json(path)

    # ---- sessions ---------------------------------------------------------
    def session_dir(self, session_id: str) -> Path:
        if not isinstance(session_id, str) or not session_id.startswith("ds_"):
            raise DirectorStoreError(
                "session_id must be a ds_* identifier (got %r)" % (session_id,))
        return self.sessions_root / session_id

    def list_sessions(self) -> List[str]:
        if not self.sessions_root.exists():
            return []
        return sorted(
            p.name for p in self.sessions_root.iterdir()
            if p.is_dir() and (p / SESSION_MANIFEST).exists()
        )

    def read_session_manifest(self, session_id: str) -> Optional[Dict[str, Any]]:
        path = self.session_dir(session_id) / SESSION_MANIFEST
        if not path.exists():
            return None
        return read_json(path)

    def write_session_manifest(self, session_id: str, doc: Dict[str, Any]) -> None:
        write_json_atomic(self.session_dir(session_id) / SESSION_MANIFEST, doc)

    def append_session_event(
        self, session_id: str, kind: str, payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        path = self.session_dir(session_id) / SESSION_EVENTS
        rows = read_jsonl(path)
        prev_hash = rows[-1]["row_hash"] if rows else "GENESIS"
        core = {"seq": len(rows) + 1, "kind": kind, "payload": payload,
                "prev_hash": prev_hash}
        core["row_hash"] = content_hash(core)
        core["ts"] = _now_iso()
        append_jsonl(path, core)
        return core

    def verify_session_chain(self, session_id: str) -> Dict[str, Any]:
        rows = read_jsonl(self.session_dir(session_id) / SESSION_EVENTS)
        prev = "GENESIS"
        for i, row in enumerate(rows):
            core = {"seq": row.get("seq"), "kind": row.get("kind"),
                    "payload": row.get("payload"),
                    "prev_hash": row.get("prev_hash")}
            if row.get("prev_hash") != prev or content_hash(core) != row.get(
                    "row_hash"):
                return {"intact": False, "first_bad_row": i + 1, "rows": len(rows)}
            prev = row.get("row_hash")
        return {"intact": True, "rows": len(rows)}

    def append_proposal(self, session_id: str, record: Dict[str, Any]) -> None:
        append_jsonl(self.session_dir(session_id) / SESSION_PROPOSALS, record)

    def read_proposals(self, session_id: str) -> List[Dict[str, Any]]:
        return read_jsonl(self.session_dir(session_id) / SESSION_PROPOSALS)

    def append_decision(self, session_id: str, record: Dict[str, Any]) -> None:
        append_jsonl(self.session_dir(session_id) / SESSION_DECISIONS, record)

    def read_decisions(self, session_id: str) -> List[Dict[str, Any]]:
        return read_jsonl(self.session_dir(session_id) / SESSION_DECISIONS)

    def write_session_artifact(
        self, session_id: str, name: str, doc: Dict[str, Any]
    ) -> None:
        path = self.session_dir(session_id) / name
        if path.exists():
            existing = read_json(path)
            if content_hash(existing) == content_hash(doc):
                return
        write_json_atomic(path, doc)

    # ---- graveyard --------------------------------------------------------
    def read_graveyard(self) -> List[Dict[str, Any]]:
        return read_jsonl(self.graveyard_path)

    def append_graveyard(self, record: Dict[str, Any]) -> bool:
        """Append one graveyard row; idempotent per signature."""
        sig = _normalize_signature(record.get("signature"))
        if not sig:
            raise DirectorStoreError("graveyard records require a signature")
        if record.get("reason") not in GRAVEYARD_REASONS:
            raise DirectorStoreError(
                "unknown graveyard reason: %r" % (record.get("reason"),))
        existing = {
            _normalize_signature(g.get("signature"))
            for g in self.read_graveyard()
        }
        if sig in existing:
            return False
        row = dict(record)
        row.setdefault("schema_version", DIRECTOR_SCHEMA_VERSION)
        row.setdefault("record_type", "GRAVEYARD")
        row["signature"] = sig
        row["recorded_at"] = _now_iso()
        append_jsonl(self.graveyard_path, row)
        return True


# --------------------------------------------------------------------------- #
# session orchestration
# --------------------------------------------------------------------------- #
def _session_id_for(pack_id: str, provider_name: str, config_hash: str) -> str:
    return "ds_" + content_hash(
        {"pack": pack_id, "provider": provider_name, "config_hash": config_hash}
    )[:16]


def run_director_session(
    *,
    artifact_root: str,
    campaign_id: str,
    director_config: Dict[str, Any],
    provider: ResearchDirectorProvider,
    output_root: str,
) -> Dict[str, Any]:
    """One bounded director cycle: evidence -> request -> plan -> policy.

    Read-only toward the source campaign; every director artifact goes under
    ``output_root``. Rerunning with identical inputs resumes the same
    session id and never duplicates proposals.
    """
    cfg_verdict = validate_director_config(director_config)
    if not cfg_verdict["accepted"]:
        return {"status": "INVALID_CONFIG",
                "violations": cfg_verdict["violations"]}

    campaign_store = ArtifactStore(artifact_root)
    dstore = DirectorStore(output_root)
    graveyard = dstore.read_graveyard()

    pack = build_evidence_pack(
        campaign_store, campaign_id,
        director_config=director_config,
        graveyard_entries=graveyard,
    )
    dstore.save_evidence_pack(pack)
    request = build_director_request(pack, director_config)
    session_id = _session_id_for(
        pack["evidence_pack_id"], provider.name, cfg_verdict["config_hash"])

    existing = dstore.read_session_manifest(session_id)
    if existing and existing.get("session_state") == "COMPLETE":
        return _session_summary(dstore, session_id, resumed=True)

    sdir = dstore.session_dir(session_id)
    sdir.mkdir(parents=True, exist_ok=True)
    prior_rows = dstore.read_proposals(session_id)
    prior_keys = {(r.get("hypothesis_id"), r.get("signature"))
                  for r in prior_rows}
    prior_signatures = [r.get("signature") for r in prior_rows
                        if r.get("signature")]

    manifest = existing or {
        "schema_version": DIRECTOR_SCHEMA_VERSION,
        "record_type": "DIRECTOR_SESSION",
        "session_id": session_id,
        "source_campaign": campaign_id,
        "evidence_pack_id": pack["evidence_pack_id"],
        "provider": provider.name,
        "config_hash": cfg_verdict["config_hash"],
        "budgets": dict(director_config.get("budgets") or {}),
        "created_at": _now_iso(),
        "session_state": "REQUESTED",
        "safety": dict(SAFETY_CONTRACT),
    }
    dstore.write_session_manifest(session_id, manifest)
    dstore.append_session_event(
        session_id,
        "SESSION_RESUMED" if existing else "SESSION_STARTED",
        {"provider": provider.name, "evidence_pack_id": pack["evidence_pack_id"]},
    )
    dstore.write_session_artifact(session_id, SESSION_REQUEST, request)

    envelope = provider.generate_research_plan(request)
    secret_hits = find_secret_keys(envelope)
    if secret_hits:
        envelope = {
            "schema_version": DIRECTOR_SCHEMA_VERSION,
            "provider": provider.name,
            "status": "INVALID_RESPONSE",
            "response": None,
            "detail": "provider output refused: secret-looking keys %s"
            % sorted(secret_hits),
        }
    dstore.append_session_event(
        session_id, "PROVIDER_RESULT",
        {"provider": provider.name, "status": envelope.get("status")},
    )

    if envelope.get("status") != STATUS_OK:
        persisted = dict(envelope)
        persisted.pop("response", None)
        dstore.write_session_artifact(session_id, SESSION_RESPONSE, persisted)
        manifest["session_state"] = envelope.get("status")
        manifest["provider_detail"] = {
            k: envelope.get(k)
            for k in ("detail", "note", "request_path", "response_path",
                      "availability", "timeout", "returncode")
            if envelope.get(k) is not None
        }
        dstore.write_session_manifest(session_id, manifest)
        return _session_summary(dstore, session_id, resumed=bool(existing))

    policy = DirectorPolicy(
        director_config, pack,
        graveyard=graveyard, prior_signatures=prior_signatures,
    )
    outcome = policy.process_response(envelope.get("response"))
    dstore.write_session_artifact(session_id, SESSION_RESPONSE, envelope)

    new_rows = 0
    for row in outcome["proposals"]:
        key = (row.get("hypothesis_id"), row.get("signature"))
        if key in prior_keys:
            continue
        record = {
            "schema_version": DIRECTOR_SCHEMA_VERSION,
            "record_type": "HYPOTHESIS_PROPOSAL",
            "session_id": session_id,
            "hypothesis_id": row["hypothesis_id"],
            "status": row["status"],
            "reasons": row["reasons"],
            "signature": row.get("signature"),
            "declared_signature": row.get("declared_signature"),
            "information_gain_rank": row.get("information_gain_rank"),
            "queue_note": row.get("queue_note"),
            "proposal": row["proposal"],
            "recorded_at": _now_iso(),
        }
        dstore.append_proposal(session_id, record)
        dstore.append_session_event(
            session_id, "PROPOSAL_RECORDED",
            {"hypothesis_id": row["hypothesis_id"], "status": row["status"]},
        )
        prior_keys.add(key)
        new_rows += 1

    graveyard_added = []
    for cand in outcome["graveyard_candidates"]:
        added = dstore.append_graveyard(dict(cand, source_session=session_id))
        if added:
            graveyard_added.append(cand["signature"])
            dstore.append_session_event(
                session_id, "GRAVEYARD_APPENDED",
                {"signature": cand["signature"], "reason": cand["reason"]},
            )

    existing_decisions = {
        content_hash(r.get("record")) for r in dstore.read_decisions(session_id)
    }
    for dec in outcome["decisions"]:
        if content_hash(dec.get("record")) in existing_decisions:
            continue
        record = {
            "schema_version": DIRECTOR_SCHEMA_VERSION,
            "record_type": "DIRECTOR_DECISION",
            "session_id": session_id,
            "decision": dec.get("decision"),
            "hypothesis_id": dec.get("hypothesis_id"),
            "accepted": dec.get("accepted"),
            "violations": dec.get("violations"),
            "record": dec.get("record"),
            "recorded_at": _now_iso(),
        }
        dstore.append_decision(session_id, record)
        dstore.append_session_event(
            session_id, "DECISION_RECORDED",
            {"decision": dec.get("decision"), "accepted": dec.get("accepted")},
        )

    manifest["session_state"] = "COMPLETE"
    manifest["response_valid"] = outcome["response_valid"]
    manifest["response_violations"] = outcome["response_violations"]
    manifest["queued"] = outcome["queued"]
    manifest["budget_usage"] = outcome["budget_usage"]
    manifest["counts"] = _proposal_counts(dstore.read_proposals(session_id))
    manifest["new_rows_this_invocation"] = new_rows
    dstore.write_session_manifest(session_id, manifest)
    dstore.append_session_event(
        session_id, "SESSION_COMPLETE",
        {"counts": manifest["counts"], "queued": outcome["queued"]},
    )
    summary = _session_summary(dstore, session_id, resumed=bool(existing))
    summary["graveyard_added"] = graveyard_added
    return summary


def _proposal_counts(rows: List[Dict[str, Any]]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for r in rows:
        s = r.get("status", "?")
        counts[s] = counts.get(s, 0) + 1
    return counts


def _session_summary(
    dstore: DirectorStore, session_id: str, *, resumed: bool = False
) -> Dict[str, Any]:
    manifest = dstore.read_session_manifest(session_id) or {}
    rows = dstore.read_proposals(session_id)
    return {
        "status": manifest.get("session_state"),
        "session_id": session_id,
        "source_campaign": manifest.get("source_campaign"),
        "evidence_pack_id": manifest.get("evidence_pack_id"),
        "provider": manifest.get("provider"),
        "resumed": resumed,
        "counts": _proposal_counts(rows),
        "queued": manifest.get("queued") or [],
        "budget_usage": manifest.get("budget_usage"),
        "provider_detail": manifest.get("provider_detail"),
        "event_chain": dstore.verify_session_chain(session_id),
        "session_dir": str(dstore.session_dir(session_id)),
        "safety": dict(SAFETY_CONTRACT),
    }


def build_session_status(dstore: DirectorStore, session_id: str) -> Dict[str, Any]:
    manifest = dstore.read_session_manifest(session_id)
    if manifest is None:
        raise DirectorStoreError("unknown session_id: %s" % session_id)
    rows = dstore.read_proposals(session_id)
    counts = _proposal_counts(rows)
    queued_rows = [r for r in rows if r.get("status") == "QUEUED"]
    budgets = manifest.get("budgets") or {}
    reconciliation = {
        "proposals_recorded": len(rows),
        "counts": counts,
        "queued_total": len(queued_rows),
        "queued_within_budget": len(queued_rows)
        <= int(budgets.get("max_accepted_hypotheses", 6)),
        "recorded_equals_counted": len(rows) == sum(counts.values()),
    }
    return {
        "session_id": session_id,
        "session_state": manifest.get("session_state"),
        "source_campaign": manifest.get("source_campaign"),
        "evidence_pack_id": manifest.get("evidence_pack_id"),
        "provider": manifest.get("provider"),
        "budgets": budgets,
        "budget_usage": manifest.get("budget_usage"),
        "reconciliation": reconciliation,
        "decisions_recorded": len(dstore.read_decisions(session_id)),
        "graveyard_total": len(dstore.read_graveyard()),
        "event_chain": dstore.verify_session_chain(session_id),
        "response_valid": manifest.get("response_valid"),
        "safety": dict(SAFETY_CONTRACT),
        "schema_version": DIRECTOR_SCHEMA_VERSION,
    }


def write_director_report(
    dstore: DirectorStore, session_id: str
) -> Dict[str, Any]:
    status = build_session_status(dstore, session_id)
    rows = dstore.read_proposals(session_id)
    decisions = dstore.read_decisions(session_id)
    report = {
        "record_type": "DIRECTOR_REPORT",
        "generated_at": _now_iso(),
        **status,
        "hypotheses": [
            {
                "hypothesis_id": r.get("hypothesis_id"),
                "status": r.get("status"),
                "reasons": r.get("reasons"),
                "signature": r.get("signature"),
                "information_gain_rank": r.get("information_gain_rank"),
                "title": (r.get("proposal") or {}).get("title"),
                "priority": (r.get("proposal") or {}).get("priority"),
                "falsification_condition": (r.get("proposal") or {}).get(
                    "falsification_condition"),
            }
            for r in rows
        ],
        "decisions": [
            {
                "decision": d.get("decision"),
                "hypothesis_id": d.get("hypothesis_id"),
                "accepted": d.get("accepted"),
            }
            for d in decisions
        ],
        "graveyard": [
            {"signature": g.get("signature"), "reason": g.get("reason")}
            for g in dstore.read_graveyard()
        ],
    }
    rdir = dstore.session_dir(session_id) / SESSION_REPORTS_DIR
    json_path = rdir / "director_report.json"
    md_path = rdir / "director_report.md"
    write_json_atomic(json_path, report)
    write_text_atomic(md_path, _render_report_md(report))
    return {"report": report,
            "artifact_paths": [str(json_path), str(md_path)]}


def _render_report_md(report: Dict[str, Any]) -> str:
    lines = [
        "# Research Director Report — %s" % report["session_id"],
        "",
        "State: **%s** | provider: %s | campaign: %s | evidence pack: %s"
        % (report.get("session_state"), report.get("provider"),
           report.get("source_campaign"), report.get("evidence_pack_id")),
        "",
        "## Safety contract",
    ]
    for k, v in (report.get("safety") or {}).items():
        lines.append("- %s = %s" % (k, str(v).lower()))
    lines += ["", "## Hypotheses"]
    for h in report.get("hypotheses") or []:
        lines.append("- %s [%s] rank=%s priority=%s — %s" % (
            h.get("hypothesis_id"), h.get("status"),
            h.get("information_gain_rank"), h.get("priority"),
            h.get("title")))
        if h.get("reasons"):
            lines.append("  reasons: %s" % "; ".join(h["reasons"][:4]))
    lines += ["", "## Decisions"]
    for d in report.get("decisions") or []:
        lines.append("- %s (%s) accepted=%s" % (
            d.get("decision"), d.get("hypothesis_id"), d.get("accepted")))
    lines += ["", "## Graveyard"]
    for g in report.get("graveyard") or []:
        lines.append("- %s: %s" % (g.get("signature"), g.get("reason")))
    lines += [
        "",
        "_Research-only. The director never performs financial calculations, "
        "never executes generated code, never registers challengers, and "
        "never changes the operational model._",
        "",
    ]
    return "\n".join(lines)


__all__ = [
    "ALLOWED_RESPONSE_FIELDS",
    "CONTRACT_PROTECTED_RESPONSE_FIELDS",
    "DIRECTOR_DECISIONS",
    "DIRECTOR_SCHEMA_VERSION",
    "DirectorError",
    "DirectorPolicy",
    "DirectorSafetyError",
    "DirectorStore",
    "DirectorStoreError",
    "FORBIDDEN_DIRECTOR_DECISIONS",
    "GRAVEYARD_REASONS",
    "HYPOTHESIS_PROPOSAL_STATUSES",
    "REQUIRED_DECISION_FIELDS",
    "REQUIRED_DIRECTOR_CONFIG_FIELDS",
    "REQUIRED_PROPOSAL_FIELDS",
    "build_session_status",
    "information_gain_rank_key",
    "register_challenger",
    "run_director_session",
    "validate_decision",
    "validate_director_config",
    "validate_proposal",
    "write_director_report",
]
