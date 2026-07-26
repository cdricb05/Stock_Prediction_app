"""Campaign report generation (works for incomplete campaigns too).

Phase 29A.2: reports and status share one work-summary contract. A report is
``incomplete`` whenever supported planned work remains — a bounded invocation
ending never makes a campaign COMPLETE — and always states planned/completed/
remaining totals, the last stop reason, resumability, hypothesis status
counts, per-stage decision counts, exact gate reasons, baseline-relative
deltas and the next recommended agent action.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from . import SAFETY_CONTRACT, SCHEMA_VERSION
from . import evaluator as ev
from .artifact_store import (
    ArtifactStore,
    _now_iso,
    read_jsonl,
    write_json_atomic,
    write_text_atomic,
)
from .memory import CampaignMemory


def _next_recommended_action(
    state: str, campaign_id: str, remaining_total: int
) -> str:
    if state == "COMPLETE":
        return "none — campaign complete; review reports/campaign_report.md"
    if state == "FAILED":
        return ("review the preserved failure evidence; FAILED campaigns are "
                "never rerun")
    if state == "BLOCKED":
        return "fix the blocking input issue, then resume the campaign"
    if state == "NEW_CAMPAIGN":
        return ("run the campaign: python -m research_agent.cli run "
                "--campaign-id %s" % campaign_id)
    if remaining_total > 0:
        return ("resume the campaign (%d supported experiment(s) remaining): "
                "python -m research_agent.cli resume --campaign-id %s"
                % (remaining_total, campaign_id))
    return ("resume to finish evaluation/robustness/reporting: "
            "python -m research_agent.cli resume --campaign-id %s" % campaign_id)


def build_work_summary(store: ArtifactStore, campaign_id: str) -> Dict[str, Any]:
    """Reconciled work/progress totals shared by status and reports."""
    manifest = store.read_manifest(campaign_id)
    memory = CampaignMemory(store, campaign_id)
    experiments = memory.experiments()
    by_status: Dict[str, int] = {}
    for row in experiments.values():
        s = row.get("status", "?")
        by_status[s] = by_status.get(s, 0) + 1

    planned_total = len(experiments)
    completed_total = by_status.get("COMPLETE", 0)
    failed_total = by_status.get("FAILED", 0)
    remaining_total = by_status.get("PLANNED", 0) + by_status.get("RUNNING", 0)
    abandoned_total = by_status.get("ABANDONED", 0)
    skipped_budget_total = by_status.get("SKIPPED_BUDGET", 0)
    runtime_unsupported = by_status.get("REJECTED_UNSUPPORTED", 0)
    plan_totals = manifest.get("plan_totals") or {}
    rejected_unsupported_total = (
        runtime_unsupported + int(plan_totals.get("rejected_unsupported") or 0)
    )
    supported_total = planned_total - runtime_unsupported

    state = manifest.get("current_state", "NEW_CAMPAIGN")
    resumable = state not in ("COMPLETE", "FAILED")
    incomplete = bool(remaining_total > 0 or state != "COMPLETE")

    hyp_counts: Dict[str, int] = {}
    for h in memory.hypotheses().values():
        s = h.get("status", "?")
        hyp_counts[s] = hyp_counts.get(s, 0) + 1

    primary_counts: Dict[str, int] = {}
    for eid, row in experiments.items():
        if row.get("status") != "COMPLETE":
            continue
        doc = store.read_experiment_artifact(campaign_id, eid, "decision.json")
        if doc and doc.get("stage") == "primary":
            d = doc.get("decision", "?")
            primary_counts[d] = primary_counts.get(d, 0) + 1
    robustness_counts: Dict[str, int] = {}
    for doc in (manifest.get("robustness_results") or {}).values():
        d = (doc or {}).get("decision", "?")
        robustness_counts[d] = robustness_counts.get(d, 0) + 1

    last_invocation = manifest.get("last_invocation")
    return {
        "planned_total": planned_total,
        "supported_total": supported_total,
        "completed_total": completed_total,
        "failed_total": failed_total,
        "rejected_unsupported_total": rejected_unsupported_total,
        "remaining_total": remaining_total,
        "abandoned_total": abandoned_total,
        "skipped_budget_total": skipped_budget_total,
        "plan_totals": plan_totals,
        "invocation_limit": (last_invocation or {}).get("requested_max_experiments"),
        "last_stop_reason": manifest.get("last_stop_reason"),
        "last_invocation": last_invocation,
        "resumable": resumable,
        "incomplete": incomplete,
        "hypothesis_status_counts": hyp_counts,
        "primary_decision_counts": primary_counts,
        "robustness_decision_counts": robustness_counts,
        "next_recommended_action": _next_recommended_action(
            state, campaign_id, remaining_total
        ),
    }


def build_report(store: ArtifactStore, campaign_id: str) -> Dict[str, Any]:
    manifest = store.read_manifest(campaign_id)
    memory = CampaignMemory(store, campaign_id)
    hypotheses = memory.hypotheses()
    experiments = memory.experiments()
    challengers = read_jsonl(store.challenger_registry_path(campaign_id))
    status = store.read_status(campaign_id) or {}
    chain = store.verify_event_chain(campaign_id)

    exp_rows: List[Dict[str, Any]] = []
    configurations_tested = 0
    for eid in sorted(experiments):
        row = experiments[eid]
        decision_doc = store.read_experiment_artifact(campaign_id, eid, "decision.json") or {}
        metrics_doc = store.read_experiment_artifact(campaign_id, eid, "metrics.json") or {}
        gates_doc = store.read_experiment_artifact(campaign_id, eid, "gate_results.json") or {}
        deltas_doc = store.read_experiment_artifact(campaign_id, eid, "baseline_deltas.json") or {}
        m = metrics_doc.get("metrics", metrics_doc) if isinstance(metrics_doc, dict) else {}
        if row.get("status") == "COMPLETE":
            configurations_tested += 1
        compact_deltas = {
            name: {
                k: r.get(k)
                for k in ("candidate", "baseline", "delta_abs", "delta_rel",
                          "classification", "material")
            }
            for name, r in (deltas_doc.get("metrics") or {}).items()
        }
        exp_rows.append(
            {
                "experiment_id": eid,
                "hypothesis_id": row.get("hypothesis_id"),
                "status": row.get("status"),
                "attempt": row.get("attempt"),
                "decision": decision_doc.get("decision"),
                "decision_reasons": decision_doc.get("reasons"),
                "diagnostic_flags": decision_doc.get("diagnostic_flags"),
                "hard_gate_failures": gates_doc.get("hard_gate_failures"),
                "baseline_deltas": compact_deltas,
                "final_score": (decision_doc.get("score") or {}).get("final_score"),
                "net_spy_excess_ann": m.get("net_spy_excess_ann"),
                "rank_ic_t": m.get("rank_ic_t"),
                "turnover_monthly_oneside": m.get("turnover_monthly_oneside"),
                "max_drawdown": m.get("max_drawdown"),
                "months": m.get("months"),
                "failure": row.get("failure"),
                "abandon_reason": row.get("abandon_reason"),
            }
        )

    baseline = manifest.get("baseline") or {}
    budgets = manifest.get("budgets") or {}
    thresholds = ev.resolve_thresholds(
        (manifest.get("config") or {}).get("thresholds") or {}
    )
    provisional = sorted(k for k, v in thresholds.items() if v.get("provisional"))

    decisions = [r.get("decision") for r in exp_rows]
    summary = {
        "campaign_id": campaign_id,
        "generated_at": _now_iso(),
        "schema_version": SCHEMA_VERSION,
        "objective": manifest.get("objective"),
        "current_state": manifest.get("current_state"),
        "baseline_model": manifest.get("baseline_model"),
        "baseline_book": manifest.get("baseline_book"),
        "code_commit": manifest.get("code_commit"),
        "data_cutoff": ((manifest.get("config") or {}).get("data") or {}).get("data_cutoff"),
        "event_chain_intact": chain.get("intact"),
        "budgets": budgets,
        "budget_usage": {
            "experiments_recorded": len(experiments),
            "experiments_complete": sum(1 for r in exp_rows if r["status"] == "COMPLETE"),
            "experiments_failed": sum(1 for r in exp_rows if r["status"] == "FAILED"),
            "challengers_registered": len({c.get("candidate_id") for c in challengers}),
        },
        # multiple-testing discipline: every evaluated configuration is counted
        "configurations_tested": configurations_tested,
        "decision_counts": {
            d: decisions.count(d)
            for d in (ev.REJECTED, ev.INCONCLUSIVE, ev.RETAIN_FOR_ROBUSTNESS, ev.SHADOW_ELIGIBLE)
        },
        "baseline_validation": {
            k: baseline.get(k)
            for k in (
                "baseline_reproduced",
                "deterministic",
                "reference_reproduced",
                "reference_months_compared",
                "n_periods",
            )
        },
        "baseline_metrics": baseline.get("metrics"),
        "hypotheses": [
            {
                "hypothesis_id": h,
                "status": hypotheses[h].get("status"),
                "priority": hypotheses[h].get("priority"),
                "diagnosis": hypotheses[h].get("diagnosis"),
            }
            for h in sorted(hypotheses)
        ],
        "experiments": exp_rows,
        "challengers": [
            {
                "candidate_id": c.get("candidate_id"),
                "stage": c.get("stage"),
                "candidate_config": c.get("candidate_config"),
                "known_weaknesses": c.get("known_weaknesses"),
                "human_approval_required": c.get("human_approval_required"),
                "operational_model_changed": c.get("operational_model_changed"),
            }
            for c in challengers
        ],
        "provisional_thresholds": provisional,
        "thresholds": {k: v.get("value") for k, v in thresholds.items()},
        "safety": dict(SAFETY_CONTRACT),
        "finalization": manifest.get("finalization"),
    }
    # Work/progress contract (Phase 29A.2): planned/supported/completed/failed/
    # remaining totals, invocation_limit, last_stop_reason, resumable,
    # incomplete, hypothesis status counts, per-stage decision counts and the
    # next recommended action. `incomplete` is true whenever supported planned
    # work remains — never false merely because one bounded invocation ended.
    summary.update(build_work_summary(store, campaign_id))
    return summary


def _md_table(rows: List[Dict[str, Any]], cols: List[str]) -> str:
    if not rows:
        return "_none_\n"
    out = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for r in rows:
        out.append(
            "| "
            + " | ".join(
                ("%.4f" % r[c]) if isinstance(r.get(c), float) else str(r.get(c))
                for c in cols
            )
            + " |"
        )
    return "\n".join(out) + "\n"


def render_markdown(summary: Dict[str, Any]) -> str:
    s = summary
    lines = [
        "# Research Agent Campaign Report — %s" % s["campaign_id"],
        "",
        "Generated: %s | State: **%s** | Event chain intact: %s"
        % (s["generated_at"], s["current_state"], s["event_chain_intact"]),
        "",
        "## Objective",
        str(s.get("objective")),
        "",
        "## Safety contract",
    ]
    for k, v in s["safety"].items():
        lines.append("- %s = %s" % (k, str(v).lower()))
    lines += [
        "",
        "## Campaign progress",
        "- planned: %s | supported: %s | completed: %s | failed: %s | "
        "remaining: %s | abandoned: %s"
        % (s.get("planned_total"), s.get("supported_total"),
           s.get("completed_total"), s.get("failed_total"),
           s.get("remaining_total"), s.get("abandoned_total")),
        "- incomplete: %s | resumable: %s | last stop reason: %s | "
        "invocation limit: %s"
        % (s.get("incomplete"), s.get("resumable"),
           s.get("last_stop_reason"), s.get("invocation_limit")),
        "- hypothesis statuses: %s" % s.get("hypothesis_status_counts"),
        "- primary decisions: %s | robustness decisions: %s"
        % (s.get("primary_decision_counts"), s.get("robustness_decision_counts")),
        "- next recommended action: %s" % s.get("next_recommended_action"),
        "- finalization: %s" % (s.get("finalization") or "none"),
        "",
        "## Baseline",
        "- model: %s / book: %s" % (s["baseline_model"], s["baseline_book"]),
        "- data cutoff: %s | code commit: %s" % (s["data_cutoff"], s["code_commit"]),
        "- validation: %s" % s["baseline_validation"],
        "",
        "## Multiple-testing discipline",
        "- configurations tested (evaluated cells): %d" % s["configurations_tested"],
        "- decision counts: %s" % s["decision_counts"],
        "- provisional thresholds in force: %s" % (", ".join(s["provisional_thresholds"]) or "none"),
        "",
        "## Budgets",
        "- configured: %s" % s["budgets"],
        "- used: %s" % s["budget_usage"],
        "",
        "## Experiments",
        _md_table(
            s["experiments"],
            [
                "experiment_id",
                "status",
                "decision",
                "final_score",
                "net_spy_excess_ann",
                "rank_ic_t",
                "turnover_monthly_oneside",
                "months",
            ],
        ),
        "## Challengers (shadow-only, human approval required)",
        _md_table(
            s["challengers"],
            ["candidate_id", "stage", "human_approval_required", "operational_model_changed"],
        ),
        "",
        "_This campaign is research-only. No order, broker action, operational "
        "model change, holding change or trading automation was created. Any "
        "promotion beyond SHADOW_ELIGIBLE requires explicit human approval._",
        "",
    ]
    return "\n".join(lines)


def write_report(store: ArtifactStore, campaign_id: str) -> Dict[str, Any]:
    summary = build_report(store, campaign_id)
    rdir = store.campaign_dir(campaign_id) / "reports"
    json_path = rdir / "campaign_report.json"
    md_path = rdir / "campaign_report.md"
    write_json_atomic(json_path, summary)
    write_text_atomic(md_path, render_markdown(summary))
    return {
        "report": summary,
        "artifact_paths": [str(json_path), str(md_path)],
    }


__all__ = ["build_report", "build_work_summary", "render_markdown", "write_report"]
