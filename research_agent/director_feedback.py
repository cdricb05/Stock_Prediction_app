"""Phase 29C director feedback: deterministic packets + ONE bounded cycle.

Part J — every accepted hypothesis gets a deterministic, timestamp-free
feedback packet assembled exclusively from persisted campaign evidence
(DSL verdicts, PIT audits, diagnostics, screen results, metrics, deltas,
gates, decisions, robustness). The packet reports evidence and ONE allowed
deterministic recommendation; it never instructs the LLM to lower gates,
expand budgets, access new files, create code, activate a model, create
orders, or change operational state — no such fields exist.

Part K — a feature campaign supports AT MOST one completed director
feedback cycle (budgeted, persisted, idempotent afterwards). The provider
response is untrusted regardless of provider: strict shape validation,
contract-protected-field refusal, forbidden-decision refusal, explicit
safety confirmations, and full DSL + exhausted-signature + budget
re-validation of any proposed revision. A validated revision is persisted
as VALIDATED_PENDING_NEW_CAMPAIGN: terminal campaigns are never rerun and
terminal evidence is never rewritten, so executing a revision requires a
new bounded campaign created by the operator.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from . import SAFETY_CONTRACT
from .artifact_store import (
    assert_no_secrets,
    content_hash,
    find_secret_keys,
    read_json,
    read_jsonl,
    append_jsonl,
    write_json_atomic,
)
from .director import (
    CONTRACT_PROTECTED_RESPONSE_FIELDS,
    FORBIDDEN_DIRECTOR_DECISIONS,
    REQUIRED_DECISION_FIELDS,
    REQUIRED_SAFETY_CONFIRMATIONS,
)
from .director_provider import (
    DEFAULT_TIMEOUT_SECONDS,
    PROVIDER_CLAUDE_CODE,
    PROVIDER_FILE_EXCHANGE,
    PROVIDER_FIXTURE,
    STATUS_OK,
    ClaudeCodeDirectorProvider,
    FileExchangeDirectorProvider,
    FixtureDirectorProvider,
    ProviderError,
    validate_transport_timeout,
)
from .feature_dsl import feature_set_signature, validate_feature_set
from .schemas import find_forbidden_execution_keys

DIRECTOR_FEEDBACK_SCHEMA_VERSION = "29C.1"

# Part K: the director's allowed follow-up decisions.
FEEDBACK_DECISIONS = (
    "REVISE",
    "STOP_BRANCH",
    "REQUEST_ROBUSTNESS",
    "COMPLETE_RESEARCH_CYCLE",
)

# Part J: the packet's allowed deterministic recommendations.
PACKET_RECOMMENDATIONS = (
    "STOP_BRANCH",
    "REVISE_FEATURE",
    "RUN_ONE_BOUNDED_FOLLOW_UP",
    "REQUEST_ROBUSTNESS",
    "COMPLETE_HYPOTHESIS",
)

ALLOWED_FEEDBACK_RESPONSE_FIELDS = (
    "schema_version", "provider", "feedback_decisions", "notes",
)
OPTIONAL_DECISION_FIELDS = ("hypothesis_id", "notes", "revised_feature")

_RECOMMENDATION_BY_STATUS = {
    "REJECTED_AT_DIRECTOR": "STOP_BRANCH",
    "NOT_ACCEPTED_BUDGET": "STOP_BRANCH",
    "REJECTED_DSL": "STOP_BRANCH",
    "REJECTED_EXECUTION": "STOP_BRANCH",
    "REJECTED_PIT": "STOP_BRANCH",
    "SCREEN_REJECTED": "STOP_BRANCH",
    "PRIMARY_REJECTED": "STOP_BRANCH",
    "SCREEN_INCONCLUSIVE": "REVISE_FEATURE",
    "PRIMARY_INCONCLUSIVE": "REVISE_FEATURE",
    "RETAINED_FOR_ROBUSTNESS": "REQUEST_ROBUSTNESS",
    "ROBUSTNESS_COMPLETE": "COMPLETE_HYPOTHESIS",
}


class FeedbackError(RuntimeError):
    pass


def _violation(field: str, issue: str, value: Any, severity: str = "INVALID"):
    return {"field": field, "issue": issue, "value": value, "severity": severity}


def _slim(doc: Optional[Dict[str, Any]], drop=("monthly_rows",)) -> Optional[Dict[str, Any]]:
    if not isinstance(doc, dict):
        return doc
    return {k: v for k, v in doc.items() if k not in drop}


# --------------------------------------------------------------------------- #
# Part J: feedback packet
# --------------------------------------------------------------------------- #
def build_feedback_packet(store, campaign_id: str,
                          hypothesis_id: str) -> Dict[str, Any]:
    """Deterministic, timestamp-free packet from persisted evidence only."""
    manifest = store.read_manifest(campaign_id)
    hyp = store.hypotheses(campaign_id).get(hypothesis_id)
    if hyp is None:
        raise FeedbackError("unknown hypothesis: %s" % hypothesis_id)
    status = hyp.get("status")
    proposal = hyp.get("proposal") or {}

    compiled_doc = store.read_feature_artifact(
        campaign_id, hypothesis_id, "compiled.json") or {}
    features_doc = store.read_feature_artifact(
        campaign_id, hypothesis_id, "features.json") or {}
    pit_doc = store.read_feature_artifact(
        campaign_id, hypothesis_id, "pit_audit.json") or {}

    diag_eid = "fexp_%s_diag" % hypothesis_id
    integ_eid = "fexp_%s_integ" % hypothesis_id
    diagnostics = _slim(store.read_experiment_artifact(
        campaign_id, diag_eid, "diagnostics.json"))
    screen = store.read_experiment_artifact(
        campaign_id, diag_eid, "screen_result.json")
    metrics_doc = store.read_experiment_artifact(
        campaign_id, integ_eid, "metrics.json")
    deltas = store.read_experiment_artifact(
        campaign_id, integ_eid, "baseline_deltas.json")
    gates = store.read_experiment_artifact(
        campaign_id, integ_eid, "gate_results.json")
    decision = store.read_experiment_artifact(
        campaign_id, integ_eid, "decision.json")
    robustness = store.read_experiment_artifact(
        campaign_id, integ_eid, "robustness_results.json")

    executed = [
        eid for eid, r in store.experiments(campaign_id).items()
        if r.get("hypothesis_id") == hypothesis_id
        and r.get("status") == "COMPLETE"
    ]

    if status in ("SCREEN_REJECTED", "PRIMARY_REJECTED"):
        fals = {"outcome": "FALSIFIED",
                "basis": "deterministic screen/primary evidence met the "
                "stated falsification condition"}
    elif status in ("RETAINED_FOR_ROBUSTNESS", "ROBUSTNESS_COMPLETE"):
        fals = {"outcome": "NOT_FALSIFIED",
                "basis": "candidate retained by the deterministic gates"}
    elif status in ("SCREEN_INCONCLUSIVE", "PRIMARY_INCONCLUSIVE"):
        fals = {"outcome": "NOT_FALSIFIED",
                "basis": "evidence inconclusive: neither materially better "
                "nor falsified"}
    else:
        fals = {"outcome": "UNEVALUATED",
                "basis": "hypothesis was never executed (status %s)" % status}
    fals["condition"] = proposal.get("falsification_condition")

    weaknesses: List[str] = []
    if screen and screen.get("outcome") not in (
            None, "ADVANCE_TO_PORTFOLIO_SCREEN"):
        weaknesses.extend(screen.get("reasons") or [])
    if gates:
        weaknesses.extend(
            "gate not passed: %s" % g["gate"]
            for g in gates.get("gates") or [] if g.get("passed") is False)
    if robustness:
        weaknesses.extend(robustness.get("weaknesses") or [])

    uncertainties = [
        "screen and gate thresholds marked provisional remain provisional",
        "in-sample evidence only; no forward validation has occurred",
    ]
    fill = (metrics_doc or {}).get("feature_fill")
    if fill and fill.get("fill_fraction"):
        uncertainties.append(
            "%.1f%% of universe slots used the neutral feature fill"
            % (100.0 * fill["fill_fraction"]))

    recommendation = _RECOMMENDATION_BY_STATUS.get(
        status, "RUN_ONE_BOUNDED_FOLLOW_UP")

    fdir = store.feedback_dir(campaign_id)
    artifact_refs = sorted(
        str(store.experiment_dir(campaign_id, eid)) for eid in executed
    ) + [str(fdir / ("packet_%s.json" % hypothesis_id))]

    terminal = features_doc.get("terminal_feature_id")
    coverage = ((features_doc.get("features") or {}).get(terminal)
                or {}).get("coverage")

    body = {
        "schema_version": DIRECTOR_FEEDBACK_SCHEMA_VERSION,
        "record_type": "DIRECTOR_FEEDBACK_PACKET",
        "source_director_session": manifest.get("source_director_session"),
        "feature_campaign_id": campaign_id,
        "hypothesis_id": hypothesis_id,
        "hypothesis_status": status,
        "feature_dsl_hash": compiled_doc.get("set_signature")
        or hyp.get("signature"),
        "compiled_transformation_plan": compiled_doc.get("compiled"),
        "pit_result": {
            "pit_ok": pit_doc.get("pit_ok"),
            "violations": pit_doc.get("violations"),
            "max_formation_month": pit_doc.get("max_formation_month"),
            "max_realized_return_date": pit_doc.get("max_realized_return_date"),
        } if pit_doc else None,
        "coverage": coverage,
        "standalone_feature_diagnostics": diagnostics,
        "ic_screen": _slim(screen, drop=("checks",)),
        "portfolio_metrics": (metrics_doc or {}).get("metrics"),
        "baseline_relative_deltas": {
            name: {k: row.get(k) for k in
                   ("candidate", "baseline", "delta_abs", "classification",
                    "material")}
            for name, row in ((deltas or {}).get("metrics") or {}).items()
        } if deltas else None,
        "gate_results": {
            "hard_gate_failures": (gates or {}).get("hard_gate_failures"),
            "unevaluated_gates": (gates or {}).get("unevaluated_gates"),
        } if gates else None,
        "decision": {
            "primary": (decision or {}).get("decision"),
            "primary_reasons": (decision or {}).get("reasons"),
            "robustness": ((robustness or {}).get("decision")
                           or {}).get("decision"),
        },
        "falsification_result": fals,
        "weaknesses": weaknesses,
        "uncertainties": uncertainties,
        "recommended_deterministic_next_action": recommendation,
        "budget_consumed": {
            "experiments_executed": len(executed),
            "experiment_ids": sorted(executed),
        },
        "artifact_references": artifact_refs,
        "safety_contract": dict(SAFETY_CONTRACT),
    }
    body["feedback_id"] = "fb_" + content_hash(
        {"campaign": campaign_id, "hypothesis": hypothesis_id})[:16]
    assert_no_secrets(body)
    return body


# --------------------------------------------------------------------------- #
# Part K: bounded follow-up cycle
# --------------------------------------------------------------------------- #
def build_feedback_request(manifest: Dict[str, Any],
                           packets: List[Dict[str, Any]],
                           *,
                           revisions_used: int) -> Dict[str, Any]:
    budgets = manifest.get("budgets") or {}
    body = {
        "schema_version": DIRECTOR_FEEDBACK_SCHEMA_VERSION,
        "record_type": "DIRECTOR_FEEDBACK_REQUEST",
        "feature_campaign_id": manifest.get("feature_campaign_id"),
        "source_director_session": manifest.get("source_director_session"),
        "data_cutoff": manifest.get("data_cutoff"),
        "allowed_decisions": list(FEEDBACK_DECISIONS),
        "forbidden_decisions": list(FORBIDDEN_DIRECTOR_DECISIONS),
        "budget_state": {
            "max_revised_experiments": budgets.get("max_revised_experiments"),
            "revisions_used": revisions_used,
            "max_director_feedback_cycles": budgets.get(
                "max_director_feedback_cycles"),
        },
        "response_contract": (
            "Return ONE JSON object: {\"feedback_decisions\": [...]} where "
            "each decision carries " + ", ".join(REQUIRED_DECISION_FIELDS)
            + "; decision must be one of the allowed_decisions; a REVISE "
            "decision must add revised_feature = {features: [DSL specs]} "
            "inside the strict Phase 29B feature DSL over the same fields "
            "and the same data cutoff. Budgets, gates, thresholds and the "
            "safety contract are read-only facts, not negotiable state."
        ),
        "packets": sorted(packets, key=lambda p: p.get("hypothesis_id") or ""),
        "safety_contract": dict(SAFETY_CONTRACT),
    }
    body["request_id"] = "fbreq_" + content_hash({
        "campaign": body["feature_campaign_id"],
        "packets": [p.get("feedback_id") for p in body["packets"]],
    })[:16]
    assert_no_secrets(body)
    return body


def build_fixture_feedback_response(request: Dict[str, Any]) -> Dict[str, Any]:
    """Deterministic fixture follow-up derived only from the request."""
    decisions = []
    for packet in request.get("packets") or []:
        rec = packet.get("recommended_deterministic_next_action")
        decision = {
            "STOP_BRANCH": "STOP_BRANCH",
            "COMPLETE_HYPOTHESIS": "COMPLETE_RESEARCH_CYCLE",
            "REQUEST_ROBUSTNESS": "REQUEST_ROBUSTNESS",
        }.get(rec, "STOP_BRANCH")
        decisions.append({
            "decision": decision,
            "hypothesis_id": packet.get("hypothesis_id"),
            "evidence_refs": [packet.get("feedback_id")],
            "reasoning_summary": "deterministic fixture follow-up derived "
            "from the packet's persisted evidence and recommendation",
            "uncertainties": packet.get("uncertainties") or [],
            "falsification_condition": (packet.get("falsification_result")
                                        or {}).get("condition")
            or "no further falsifiable claim on this branch",
            "next_deterministic_action": "record the decision; no further "
            "execution inside this bounded cycle",
            "budget_impact": {"additional_experiments": 0},
            "safety_confirmation": {
                "research_only": True,
                "no_operational_action": True,
                "no_gate_change": True,
                "no_budget_increase": True,
            },
        })
    return {
        "schema_version": DIRECTOR_FEEDBACK_SCHEMA_VERSION,
        "provider": PROVIDER_FIXTURE,
        "feedback_decisions": decisions,
    }


def validate_feedback_response(response: Any) -> List[Dict[str, Any]]:
    violations: List[Dict[str, Any]] = []
    if not isinstance(response, dict):
        return [_violation("$", "response must be a single JSON object",
                           type(response).__name__)]
    hits = find_secret_keys(response)
    if hits:
        violations.append(_violation("$", "secret-looking keys are refused",
                                     sorted(hits), severity="SAFETY"))
    for k in sorted(set(response) - set(ALLOWED_FEEDBACK_RESPONSE_FIELDS)):
        if k in CONTRACT_PROTECTED_RESPONSE_FIELDS:
            violations.append(_violation(
                k, "the director may not alter contract-protected state "
                "(budgets, gates, thresholds, safety, cutoff, baseline, "
                "operational model)", None, severity="SAFETY"))
        else:
            violations.append(_violation(k, "unknown field (strict schema)",
                                         None))
    if not isinstance(response.get("feedback_decisions"), list):
        violations.append(_violation(
            "feedback_decisions", "must be a list of decisions",
            response.get("feedback_decisions")))
    return violations


def validate_feedback_decision(
    decision: Any,
    *,
    numeric_fields: List[str],
    exhausted_signatures: List[str],
    original_signatures: Dict[str, str],
    max_feature_depth: int,
    max_interactions: int,
    revisions_remaining: int,
    hypotheses_already_revised: List[str],
) -> Dict[str, Any]:
    violations: List[Dict[str, Any]] = []
    revision: Optional[Dict[str, Any]] = None
    if not isinstance(decision, dict):
        return {"accepted": False, "revision": None,
                "violations": [_violation("$", "decision must be a JSON "
                                          "object", type(decision).__name__)]}
    for kp in find_forbidden_execution_keys(decision):
        violations.append(_violation(
            kp, "executable-content key is forbidden", None, severity="SAFETY"))

    d = decision.get("decision")
    if isinstance(d, str) and d.strip().upper() in FORBIDDEN_DIRECTOR_DECISIONS:
        violations.append(_violation(
            "decision", "forbidden operational decision: the director may "
            "never activate shadows, promote candidates, create orders, "
            "execute trades, change the active model, or lower a gate", d,
            severity="SAFETY"))
    elif d not in FEEDBACK_DECISIONS:
        violations.append(_violation(
            "decision", "must be one of %s" % (FEEDBACK_DECISIONS,), d))

    for f in REQUIRED_DECISION_FIELDS:
        if f not in decision:
            violations.append(_violation(f, "required field missing", None))
    allowed = set(REQUIRED_DECISION_FIELDS) | set(OPTIONAL_DECISION_FIELDS)
    for f in sorted(set(decision) - allowed):
        violations.append(_violation(f, "unknown field (strict schema)",
                                     decision.get(f)))

    sc = decision.get("safety_confirmation")
    if isinstance(sc, dict):
        for f in REQUIRED_SAFETY_CONFIRMATIONS:
            if sc.get(f) is not True:
                violations.append(_violation(
                    "safety_confirmation.%s" % f, "must be explicitly true",
                    sc.get(f), severity="SAFETY"))
    elif "safety_confirmation" in decision:
        violations.append(_violation("safety_confirmation",
                                     "must be an object", sc))

    if d == "REVISE":
        hid = decision.get("hypothesis_id")
        rf = decision.get("revised_feature")
        if not isinstance(rf, dict) or set(rf) != {"features"}:
            violations.append(_violation(
                "revised_feature", "REVISE requires revised_feature = an "
                "object with exactly one key: features", rf))
        elif revisions_remaining <= 0:
            violations.append(_violation(
                "revised_feature", "revision budget exhausted "
                "(max_revised_experiments)", None))
        elif hid in hypotheses_already_revised:
            violations.append(_violation(
                "revised_feature", "at most one revised experiment per "
                "surviving branch", hid))
        else:
            verdict = validate_feature_set(
                rf.get("features") or [],
                available_fields=numeric_fields,
                max_depth=max_feature_depth,
                max_interactions=max_interactions,
            )
            if not verdict["accepted"]:
                violations.append(_violation(
                    "revised_feature", "revision failed strict DSL "
                    "validation", sorted({
                        "%s: %s" % (v["field"], v["issue"])
                        for v in verdict["violations"]})[:6]))
            else:
                sig = feature_set_signature(rf["features"])
                exhausted = {s.strip().lower() for s in exhausted_signatures}
                if sig.lower() in exhausted:
                    violations.append(_violation(
                        "revised_feature", "revision matches an exhausted "
                        "research signature", sig))
                elif sig == (original_signatures.get(hid) or ""):
                    violations.append(_violation(
                        "revised_feature", "revision is identical to the "
                        "original feature construction", sig))
                else:
                    revision = {
                        "hypothesis_id": hid,
                        "revised_feature": rf,
                        "signature": sig,
                        "dsl_verdict_accepted": True,
                    }
    return {"accepted": not violations, "revision": revision,
            "violations": violations}


def _cycles_path(store, campaign_id: str) -> Path:
    return store.feedback_dir(campaign_id) / "cycles.jsonl"


def run_feedback_cycle(
    store,
    campaign_id: str,
    *,
    provider_name: str,
    exchange_dir: Optional[str] = None,
    timeout_seconds: Optional[int] = None,
) -> Dict[str, Any]:
    """Run AT MOST one bounded director feedback cycle for a campaign."""
    manifest = store.read_manifest(campaign_id)
    budgets = manifest.get("budgets") or {}
    max_cycles = int(budgets.get("max_director_feedback_cycles", 1))
    fdir = store.feedback_dir(campaign_id)
    cycles = read_jsonl(_cycles_path(store, campaign_id))
    completed = [c for c in cycles if c.get("status") == "COMPLETE"]
    if len(completed) >= max_cycles:
        return {
            "status": "FEEDBACK_CYCLE_LIMIT_REACHED",
            "feature_campaign_id": campaign_id,
            "cycles_completed": len(completed),
            "max_director_feedback_cycles": max_cycles,
            "idempotent": True,
            "note": "the bounded feedback cycle already ran; Phase 29C never "
            "runs recursive director cycles",
            "safety": dict(SAFETY_CONTRACT),
        }

    packets = []
    for hid in sorted(manifest.get("accepted_hypotheses") or []):
        path = fdir / ("packet_%s.json" % hid)
        if path.exists():
            packets.append(read_json(path))
    if not packets:
        return {
            "status": "NO_FEEDBACK_PACKETS",
            "feature_campaign_id": campaign_id,
            "note": "run the feature campaign through DIRECTOR_FEEDBACK first",
        }

    revisions = read_jsonl(fdir / "revisions.jsonl")
    request = build_feedback_request(
        manifest, packets, revisions_used=len(revisions))
    write_json_atomic(fdir / "feedback_request.json", request)

    if provider_name == PROVIDER_FIXTURE:
        provider = FixtureDirectorProvider(
            fixture_response=build_fixture_feedback_response(request))
    elif provider_name == PROVIDER_FILE_EXCHANGE:
        provider = FileExchangeDirectorProvider(exchange_dir or str(fdir))
    elif provider_name == PROVIDER_CLAUDE_CODE:
        override = validate_transport_timeout(timeout_seconds)
        provider = ClaudeCodeDirectorProvider(
            timeout_seconds=override if override is not None
            else DEFAULT_TIMEOUT_SECONDS)
    else:
        raise ProviderError("unknown provider: %s" % provider_name)

    envelope = provider.generate_research_plan(request)
    if find_secret_keys(envelope):
        envelope = {"provider": provider_name, "status": "INVALID_RESPONSE",
                    "response": None,
                    "detail": "provider output refused: secret-looking keys"}
    store.append_event(campaign_id, "FEEDBACK_PROVIDER_RESULT", {
        "provider": provider_name, "status": envelope.get("status")})

    if envelope.get("status") != STATUS_OK:
        persisted = {k: v for k, v in envelope.items() if k != "response"}
        write_json_atomic(fdir / "feedback_response.json", persisted)
        append_jsonl(_cycles_path(store, campaign_id), {
            "status": envelope.get("status"),
            "provider": provider_name,
            "request_id": request["request_id"],
        })
        return {
            "status": envelope.get("status"),
            "feature_campaign_id": campaign_id,
            "provider": provider_name,
            "detail": {k: envelope.get(k)
                       for k in ("detail", "note", "request_path",
                                 "response_path", "availability")
                       if envelope.get(k) is not None},
            "cycle_consumed": False,
            "safety": dict(SAFETY_CONTRACT),
        }

    response = envelope.get("response")
    shape_violations = validate_feedback_response(response)
    write_json_atomic(fdir / "feedback_response.json", envelope)
    if shape_violations:
        append_jsonl(_cycles_path(store, campaign_id), {
            "status": "INVALID_RESPONSE",
            "provider": provider_name,
            "request_id": request["request_id"],
            "violations": shape_violations,
        })
        store.append_event(campaign_id, "FEEDBACK_RESPONSE_REJECTED", {
            "n_violations": len(shape_violations)})
        return {
            "status": "INVALID_RESPONSE",
            "feature_campaign_id": campaign_id,
            "violations": shape_violations,
            "cycle_consumed": False,
            "safety": dict(SAFETY_CONTRACT),
        }

    inventory = store.read_campaign_artifact(
        campaign_id, "source_inventory.json") or {}
    numeric_fields = sorted((inventory.get("numeric_sources") or {}).keys())
    hyp_rows = store.hypotheses(campaign_id)
    original_signatures = {
        hid: (row.get("signature") or "")
        for hid, row in hyp_rows.items()
    }
    already_revised = sorted({
        r.get("hypothesis_id") for r in revisions if r.get("hypothesis_id")})
    remaining = max(
        0, int(budgets.get("max_revised_experiments", 0)) - len(revisions))

    decisions_out: List[Dict[str, Any]] = []
    revisions_validated: List[str] = []
    for i, dec in enumerate(response.get("feedback_decisions") or []):
        verdict = validate_feedback_decision(
            dec,
            numeric_fields=numeric_fields,
            exhausted_signatures=list(
                manifest.get("exhausted_signatures") or []),
            original_signatures=original_signatures,
            max_feature_depth=int(budgets.get("max_feature_depth", 3)),
            max_interactions=int(budgets.get("max_interactions", 4)),
            revisions_remaining=remaining,
            hypotheses_already_revised=already_revised,
        )
        record = {
            "schema_version": DIRECTOR_FEEDBACK_SCHEMA_VERSION,
            "record_type": "FEEDBACK_DECISION",
            "index": i,
            "decision": dec.get("decision") if isinstance(dec, dict) else None,
            "hypothesis_id": (dec.get("hypothesis_id")
                              if isinstance(dec, dict) else None),
            "accepted": verdict["accepted"],
            "violations": verdict["violations"],
            "record": dec,
        }
        append_jsonl(fdir / "decisions.jsonl", record)
        store.append_event(campaign_id, "FEEDBACK_DECISION_RECORDED", {
            "decision": record["decision"],
            "hypothesis_id": record["hypothesis_id"],
            "accepted": record["accepted"]})
        decisions_out.append({k: record[k] for k in
                              ("decision", "hypothesis_id", "accepted")})
        if verdict["accepted"] and verdict["revision"]:
            rev = dict(verdict["revision"])
            rev["status"] = "VALIDATED_PENDING_NEW_CAMPAIGN"
            rev["note"] = ("terminal campaigns are never rerun; executing "
                           "this validated revision requires a new bounded "
                           "feature campaign created by the operator")
            append_jsonl(fdir / "revisions.jsonl", rev)
            remaining -= 1
            already_revised.append(rev["hypothesis_id"])
            revisions_validated.append(rev["hypothesis_id"])
            store.append_event(campaign_id, "FEEDBACK_REVISION_VALIDATED", {
                "hypothesis_id": rev["hypothesis_id"],
                "signature": rev["signature"]})

    append_jsonl(_cycles_path(store, campaign_id), {
        "status": "COMPLETE",
        "cycle_number": len(completed) + 1,
        "provider": provider_name,
        "request_id": request["request_id"],
        "decisions_recorded": len(decisions_out),
        "revisions_validated": revisions_validated,
    })
    store.append_event(campaign_id, "FEEDBACK_CYCLE_COMPLETE", {
        "cycle_number": len(completed) + 1,
        "decisions_recorded": len(decisions_out)})
    return {
        "status": "FEEDBACK_CYCLE_COMPLETE",
        "feature_campaign_id": campaign_id,
        "provider": provider_name,
        "cycle_number": len(completed) + 1,
        "decisions": decisions_out,
        "accepted_decisions": sum(1 for d in decisions_out if d["accepted"]),
        "revisions_validated": revisions_validated,
        "cycle_consumed": True,
        "safety": dict(SAFETY_CONTRACT),
    }


__all__ = [
    "ALLOWED_FEEDBACK_RESPONSE_FIELDS",
    "DIRECTOR_FEEDBACK_SCHEMA_VERSION",
    "FEEDBACK_DECISIONS",
    "FeedbackError",
    "PACKET_RECOMMENDATIONS",
    "build_feedback_packet",
    "build_feedback_request",
    "build_fixture_feedback_response",
    "run_feedback_cycle",
    "validate_feedback_decision",
    "validate_feedback_response",
]
