"""Campaign report generation (works for incomplete campaigns too)."""

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
        m = metrics_doc.get("metrics", metrics_doc) if isinstance(metrics_doc, dict) else {}
        if row.get("status") == "COMPLETE":
            configurations_tested += 1
        exp_rows.append(
            {
                "experiment_id": eid,
                "hypothesis_id": row.get("hypothesis_id"),
                "status": row.get("status"),
                "attempt": row.get("attempt"),
                "decision": decision_doc.get("decision"),
                "final_score": (decision_doc.get("score") or {}).get("final_score"),
                "net_spy_excess_ann": m.get("net_spy_excess_ann"),
                "rank_ic_t": m.get("rank_ic_t"),
                "turnover_monthly_oneside": m.get("turnover_monthly_oneside"),
                "max_drawdown": m.get("max_drawdown"),
                "months": m.get("months"),
                "failure": row.get("failure"),
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
        "incomplete": manifest.get("current_state") not in ("COMPLETE",),
    }
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


__all__ = ["build_report", "render_markdown", "write_report"]
