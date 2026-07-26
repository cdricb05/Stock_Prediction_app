"""Phase 29B deterministic evidence-pack builder.

The LLM research director never gets repository or filesystem access: it
receives one versioned, content-hashed JSON document built exclusively from
the persisted evidence of a COMPLETE campaign. The pack contains no
timestamps (identical inputs always produce byte-identical packs and the
same evidence_pack_id) and never copies operational Paper Trader data —
only the operational model's identity strings.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from . import SAFETY_CONTRACT
from .artifact_store import ArtifactStore, content_hash, read_jsonl
from .feature_dsl import (
    ALLOWED_TRANSFORMS,
    FORBIDDEN_LEAKAGE_OPS,
    NON_SOURCE_FIELDS,
    TARGET_FIELDS,
    _is_target_field,
)
from .memory import CampaignMemory
from .reporting import build_work_summary
from .tool_adapters import TOOL_NAMES

EVIDENCE_PACK_SCHEMA_VERSION = "29B.1"

# Tools the director may request. Challenger registration and campaign
# reporting stay controller-internal: the director can never reach them.
ALLOWED_DIRECTOR_TOOLS = tuple(sorted(
    set(TOOL_NAMES) - {"register_shadow_challenger", "generate_campaign_report"}
))

# Part E initial budget = hard ceiling. A config may lower these values but
# never raise them; the LLM can never touch them at all.
PHASE29B_BUDGET_CEILINGS = {
    "max_proposed_hypotheses": 12,
    "max_accepted_hypotheses": 6,
    "max_primary_experiments": 24,
    "max_feature_depth": 3,
    "max_interactions": 4,
    "max_robustness_candidates": 4,
    "max_registered_challengers": 2,
    "max_retry_per_tool": 1,
}
PHASE29B_DEFAULT_BUDGETS = dict(PHASE29B_BUDGET_CEILINGS)

# The Phase 29A.3 outcome, preloaded as exhausted context: 59/59 cells of the
# blend/size/sector/buffer grid completed, six retained candidates, all six
# rejected by the strict robustness gates (binding gate: rank_ic), zero
# shadow-eligible, zero challengers. Signatures are the duplicate-search keys
# the director policy matches new proposals against.
EXHAUSTED_PHASE29A_DIMENSIONS = (
    {
        "family": "fundamental_momentum_blend_v1",
        "axis": "blend_weights",
        "values": [[0.3, 0.7], [0.4, 0.6], [0.5, 0.5], [0.6, 0.4], [0.7, 0.3]],
        "signature": "portfolio:blend_weights",
        "evidence": "full approved 30/70..70/30 grid tested in Phase 29A.3; "
        "reweighting adds return but not rank-IC; no shadow survivor",
    },
    {
        "family": "fundamental_momentum_blend_v1",
        "axis": "top_n",
        "values": [25, 50],
        "signature": "portfolio:top_n",
        "evidence": "Top-25 vs Top-50 tested; Top-50 rejected on materially "
        "worse net excess",
    },
    {
        "family": "fundamental_momentum_blend_v1",
        "axis": "sector_treatment",
        "values": ["raw", "sector_cap", "sector_neutral"],
        "signature": "portfolio:sector_treatment",
        "evidence": "raw and sector_neutral cells all failed the "
        "sector_concentration hard gate (40/59 rejections)",
    },
    {
        "family": "fundamental_momentum_blend_v1",
        "axis": "exit_buffer_fraction",
        "values": [0.0, 0.2],
        "signature": "portfolio:exit_buffer_fraction",
        "evidence": "0.20 exit buffer is a real turnover improver but not an "
        "alpha source; no shadow survivor",
    },
    {
        "family": "fundamental_momentum_blend_v1",
        "axis": "combined_grid",
        "values": "all approved blend x size x sector x buffer combinations",
        "signature": "portfolio:blend_family_combined",
        "evidence": "the complete 59-cell interaction grid is exhausted; "
        "portfolio-level knobs cannot move monthly rank-IC",
    },
)

KNOWN_LEAKAGE_RISKS = (
    {
        "risk": "same_period_target_leakage",
        "detail": "fwd_1m is the realized forward return used as the "
        "evaluation target; it (and any fwd_*/forward_*/target_* field) may "
        "never appear as a feature source",
    },
    {
        "risk": "forward_shift",
        "detail": "negative lags, lead operators and future joins move "
        "information backwards in time and are rejected at DSL validation",
    },
    {
        "risk": "centered_windows",
        "detail": "centered rolling windows average future observations into "
        "the formation month; only trailing windows are permitted",
    },
    {
        "risk": "fundamental_restatement",
        "detail": "the fundamental panel is a point-in-time reconstruction "
        "with publication lags already applied; any derived feature must use "
        "only lag/rolling transforms of the panel values as-published",
    },
    {
        "risk": "eligibility_survivorship",
        "detail": "eligible/is_member/adv_dollar are formation-month flags; "
        "filtering on future eligibility would inject survivorship bias",
    },
)


class EvidencePackError(RuntimeError):
    pass


def _decision_doc(store: ArtifactStore, campaign_id: str, eid: str) -> Dict[str, Any]:
    return store.read_experiment_artifact(campaign_id, eid, "decision.json") or {}


def _gate_failures(gates_doc: Any) -> List[str]:
    out: List[str] = []
    for item in (gates_doc or {}).get("hard_gate_failures") or []:
        if isinstance(item, str):
            out.append(item)
        elif isinstance(item, dict):
            out.append(str(item.get("gate", item)))
    return out


def build_evidence_pack(
    store: ArtifactStore,
    campaign_id: str,
    *,
    director_config: Optional[Dict[str, Any]] = None,
    graveyard_entries: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Build the deterministic evidence pack for one COMPLETE campaign."""
    manifest = store.read_manifest(campaign_id)  # raises for unknown campaign
    state = manifest.get("current_state")
    if state != "COMPLETE":
        raise EvidencePackError(
            "evidence packs are built only from COMPLETE campaigns; %s is %s"
            % (campaign_id, state)
        )
    cfg = manifest.get("config") or {}
    director_config = director_config or {}
    memory = CampaignMemory(store, campaign_id)
    experiments = memory.experiments()
    summary = build_work_summary(store, campaign_id)

    features_doc = (manifest.get("data_audit") or {}).get("features") or {}
    mom_fields = list(features_doc.get("momentum_features") or [])
    fund_fields = list(features_doc.get("fundamental_features") or [])
    excluded = sorted(
        {f for f in mom_fields + fund_fields
         if _is_target_field(f) or f in NON_SOURCE_FIELDS}
    )
    momentum_sources = sorted(set(mom_fields) - set(excluded))
    fundamental_sources = sorted(set(fund_fields) - set(excluded))

    gate_distribution: Dict[str, int] = {}
    retained: List[Dict[str, Any]] = []
    rank_ic_t_retained: List[Any] = []
    robustness_results = manifest.get("robustness_results") or {}
    for eid in sorted(experiments):
        row = experiments[eid]
        if row.get("status") != "COMPLETE":
            continue
        dec = _decision_doc(store, campaign_id, eid)
        gates_doc = store.read_experiment_artifact(campaign_id, eid, "gate_results.json")
        for g in _gate_failures(gates_doc):
            gate_distribution[g] = gate_distribution.get(g, 0) + 1
        if dec.get("decision") == "RETAIN_FOR_ROBUSTNESS":
            metrics_doc = store.read_experiment_artifact(
                campaign_id, eid, "metrics.json") or {}
            m = metrics_doc.get("metrics") or {}
            rb = robustness_results.get(eid) or {}
            rank_ic_t_retained.append(m.get("rank_ic_t"))
            retained.append({
                "experiment_id": eid,
                "hypothesis_id": row.get("hypothesis_id"),
                "net_spy_excess_ann": m.get("net_spy_excess_ann"),
                "rank_ic_t": m.get("rank_ic_t"),
                "rank_ic_mean": m.get("rank_ic_mean"),
                "turnover_monthly_oneside": m.get("turnover_monthly_oneside"),
                "max_drawdown": m.get("max_drawdown"),
                "months": m.get("months"),
                "primary_reasons": dec.get("reasons"),
                "robustness_decision": rb.get("decision"),
                "robustness_weaknesses": rb.get("weaknesses"),
            })

    robustness_weakness_counts: Dict[str, int] = {}
    for rb in robustness_results.values():
        for w in (rb or {}).get("weaknesses") or []:
            robustness_weakness_counts[w] = robustness_weakness_counts.get(w, 0) + 1

    baseline = manifest.get("baseline") or {}
    baseline_metrics = baseline.get("metrics") or {}
    binding_weaknesses = {
        "rank_ic": {
            "baseline_rank_ic_t": baseline_metrics.get("rank_ic_t"),
            "baseline_rank_ic_mean": baseline_metrics.get("rank_ic_mean"),
            "retained_rank_ic_t": sorted(
                (v for v in rank_ic_t_retained if v is not None)
            ),
            "robustness_rank_ic_gate_failures": robustness_weakness_counts.get(
                "gate not passed: rank_ic", 0
            ),
            "diagnosis": "monthly cross-sectional rank-IC is a property of the "
            "underlying signals; no portfolio-level knob in the exhausted "
            "family moved it. The strict shadow standard requires rank-IC "
            "evidence the current signal set cannot supply.",
        },
        "robustness_weakness_counts": dict(sorted(
            robustness_weakness_counts.items()
        )),
        "primary_hard_gate_distribution": dict(sorted(gate_distribution.items())),
    }

    challengers = read_jsonl(store.challenger_registry_path(campaign_id))
    exhausted = list(EXHAUSTED_PHASE29A_DIMENSIONS) + [
        e for e in (director_config.get("exhausted_dimensions") or [])
        if isinstance(e, dict) and e.get("signature")
    ]

    body: Dict[str, Any] = {
        "schema_version": EVIDENCE_PACK_SCHEMA_VERSION,
        "record_type": "EVIDENCE_PACK",
        "source_campaign": campaign_id,
        "code_commit": manifest.get("code_commit"),
        "config_hash": manifest.get("config_hash"),
        "data_cutoff": (cfg.get("data") or {}).get("data_cutoff"),
        "dataset_hashes": ((manifest.get("data_audit") or {})
                           .get("coverage") or {}).get("provenance", {}).get("sha256"),
        "current_baseline": {
            "model_id": manifest.get("baseline_model"),
            "book_id": manifest.get("baseline_book"),
            "baseline_reproduced": baseline.get("baseline_reproduced"),
            "deterministic": baseline.get("deterministic"),
            "metrics": {
                k: baseline_metrics.get(k)
                for k in (
                    "net_spy_excess_ann", "net_return_ann", "rank_ic_mean",
                    "rank_ic_t", "rank_ic_ir", "turnover_monthly_oneside",
                    "max_drawdown", "volatility_ann", "hit_rate",
                    "max_sector_weight", "months",
                )
            },
        },
        "active_operational_model": {
            "model_id": manifest.get("baseline_model"),
            "book_id": manifest.get("baseline_book"),
            "identity_only": True,
            "note": "identity strings only; operational Paper Trader state "
            "(no operational records are ever copied into research evidence)",
        },
        "operational_state_excluded": True,
        "campaign_outcome": {
            "current_state": state,
            "planned_total": summary.get("planned_total"),
            "completed_total": summary.get("completed_total"),
            "failed_total": summary.get("failed_total"),
            "rejected_unsupported_total": summary.get("rejected_unsupported_total"),
            "primary_decision_counts": summary.get("primary_decision_counts"),
            "robustness_decision_counts": summary.get("robustness_decision_counts"),
            "hypothesis_status_counts": summary.get("hypothesis_status_counts"),
            "shadow_eligible": list(manifest.get("shadow_eligible") or []),
            "challengers_registered": sorted(
                {c.get("candidate_id") for c in challengers}
            ),
        },
        "exhausted_research_dimensions": exhausted,
        "exhausted_signatures": sorted({e["signature"] for e in exhausted}),
        "binding_weaknesses": binding_weaknesses,
        "retained_candidate_tradeoffs": retained,
        "failed_gate_distribution": dict(sorted(gate_distribution.items())),
        "available_features": {
            "momentum_sources": momentum_sources,
            "fundamental_sources": fundamental_sources,
            "categorical_fields": sorted(
                {f for f in momentum_sources + fundamental_sources if f == "sector"}
            ),
            "excluded_fields": excluded,
            "excluded_reason": "realized-forward targets and bookkeeping "
            "identifiers are never feature sources",
        },
        "allowed_transformations": sorted(ALLOWED_TRANSFORMS),
        "unsupported_transformations": {
            "forbidden_leakage_ops": sorted(FORBIDDEN_LEAKAGE_OPS),
            "note": "any operation not in allowed_transformations is rejected "
            "as UNSUPPORTED at DSL validation",
        },
        "known_leakage_risks": list(KNOWN_LEAKAGE_RISKS),
        "target_fields": sorted(TARGET_FIELDS),
        "available_tools": list(ALLOWED_DIRECTOR_TOOLS),
        "prior_hypothesis_graveyard": [
            {
                "signature": g.get("signature"),
                "reason": g.get("reason"),
                "hypothesis_id": g.get("hypothesis_id"),
            }
            for g in (graveyard_entries or [])
        ],
        "research_budget": dict(
            director_config.get("budgets") or PHASE29B_DEFAULT_BUDGETS
        ),
        "safety_contract": dict(SAFETY_CONTRACT),
    }

    digest = content_hash(body)
    pack = dict(body)
    pack["content_hash"] = digest
    pack["evidence_pack_id"] = "ep_" + digest[:20]
    return pack


__all__ = [
    "ALLOWED_DIRECTOR_TOOLS",
    "EVIDENCE_PACK_SCHEMA_VERSION",
    "EXHAUSTED_PHASE29A_DIMENSIONS",
    "EvidencePackError",
    "KNOWN_LEAKAGE_RISKS",
    "PHASE29B_BUDGET_CEILINGS",
    "PHASE29B_DEFAULT_BUDGETS",
    "build_evidence_pack",
]
