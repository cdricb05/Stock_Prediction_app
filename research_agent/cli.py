"""PowerShell-friendly operator CLI for the research agent.

Exit codes:
    0  success
    2  invalid configuration / arguments
    3  unknown campaign id
    4  campaign run ended FAILED or BLOCKED
    5  campaign lock conflict (another process is running it)

Every command supports --json for machine-readable output. The CLI performs
research orchestration ONLY: it can never create orders, touch a broker, or
change the operational model.

Phase 29A.2: --max-experiments on run/resume limits ONE invocation; the
campaign pauses (resumable) at the limit instead of completing. `finalize`
is the only way to COMPLETE a campaign that still has supported planned
work, and it requires an explicit --reason that is persisted.

Phase 29B adds the research-director commands (provider-check,
director-evidence, director-validate, director-plan, director-status,
director-report). The director layer is read-only toward campaigns, writes
only under its own output root, and can never register challengers or
touch any operational state.

Phase 29C adds the deterministic feature-campaign commands
(feature-validate, feature-create, feature-plan, feature-run,
feature-resume, feature-status, feature-report, director-feedback).
Feature campaigns execute validated Phase 29B feature hypotheses through
the fixed deterministic executor under <artifact_root>/feature_campaigns;
they inherit the source campaign's data cutoff, never lower a gate, never
register challengers, and never touch Paper Trader.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, Optional

from . import SAFETY_CONTRACT
from .artifact_store import ArtifactStore, ArtifactStoreError
from .controller import (
    DEFAULT_ARTIFACT_ROOT,
    RUN_ALREADY_COMPLETE,
    RUN_ALREADY_FAILED,
    RUN_BLOCKED,
    RUN_FAILED,
    RUN_LOCKED,
    RUN_OK,
    RUN_PAUSED,
    CampaignController,
    create_campaign,
)
from .reporting import write_report
from .schemas import validate_campaign_config

EXIT_OK = 0
EXIT_INVALID = 2
EXIT_UNKNOWN_CAMPAIGN = 3
EXIT_FAILED = 4
EXIT_LOCKED = 5


def _load_config(path: str) -> Optional[Dict[str, Any]]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        print("cannot read config %s: %s" % (path, exc), file=sys.stderr)
        return None


def _emit(payload: Dict[str, Any], as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, indent=1, sort_keys=True, default=str))
    else:
        for k, v in payload.items():
            if isinstance(v, (dict, list)):
                v = json.dumps(v, default=str)[:400]
            print("%s: %s" % (k, v))


def _controller(args) -> Optional[CampaignController]:
    root = args.artifact_root or DEFAULT_ARTIFACT_ROOT
    try:
        store = ArtifactStore(root)
        if args.campaign_id not in store.list_campaigns():
            print("unknown campaign id: %s" % args.campaign_id, file=sys.stderr)
            return None
        return CampaignController(args.campaign_id, artifact_root=root)
    except ArtifactStoreError as exc:
        print(str(exc), file=sys.stderr)
        return None


def cmd_validate_config(args) -> int:
    cfg = _load_config(args.config)
    if cfg is None:
        return EXIT_INVALID
    verdict = validate_campaign_config(cfg)
    _emit(
        {
            "accepted": verdict["accepted"],
            "violations": verdict["violations"],
            "config_hash": verdict["config_hash"],
        },
        args.json,
    )
    return EXIT_OK if verdict["accepted"] else EXIT_INVALID


def cmd_create(args) -> int:
    cfg = _load_config(args.config)
    if cfg is None:
        return EXIT_INVALID
    result = create_campaign(cfg, artifact_root=args.artifact_root)
    _emit(result, args.json)
    return EXIT_OK if result.get("created") else EXIT_INVALID


def cmd_plan(args) -> int:
    ctl = _controller(args)
    if ctl is None:
        return EXIT_UNKNOWN_CAMPAIGN
    result = ctl.plan_preview(max_experiments=args.max_experiments)
    _emit(result, args.json)
    return EXIT_OK


def cmd_run(args) -> int:
    ctl = _controller(args)
    if ctl is None:
        return EXIT_UNKNOWN_CAMPAIGN
    result = ctl.run(max_experiments=args.max_experiments, dry_run=args.dry_run)
    _emit(result, args.json)
    status = result.get("status")
    if args.dry_run or status in (RUN_OK, RUN_ALREADY_COMPLETE, RUN_PAUSED):
        return EXIT_OK
    if status == RUN_LOCKED:
        return EXIT_LOCKED
    if status in (RUN_FAILED, RUN_ALREADY_FAILED, RUN_BLOCKED):
        return EXIT_FAILED
    return EXIT_OK


def cmd_status(args) -> int:
    ctl = _controller(args)
    if ctl is None:
        return EXIT_UNKNOWN_CAMPAIGN
    _emit(ctl.status(), args.json)
    return EXIT_OK


def cmd_pause(args) -> int:
    ctl = _controller(args)
    if ctl is None:
        return EXIT_UNKNOWN_CAMPAIGN
    _emit(ctl.request_pause(), args.json)
    return EXIT_OK


def cmd_resume(args) -> int:
    ctl = _controller(args)
    if ctl is None:
        return EXIT_UNKNOWN_CAMPAIGN
    ctl.clear_operator_request()
    result = ctl.run(max_experiments=args.max_experiments, dry_run=args.dry_run)
    _emit(result, args.json)
    status = result.get("status")
    if args.dry_run or status in (RUN_OK, RUN_ALREADY_COMPLETE, RUN_PAUSED):
        return EXIT_OK
    if status == RUN_LOCKED:
        return EXIT_LOCKED
    return EXIT_FAILED


def cmd_finalize(args) -> int:
    ctl = _controller(args)
    if ctl is None:
        return EXIT_UNKNOWN_CAMPAIGN
    result = ctl.finalize(reason=args.reason)
    _emit(result, args.json)
    if result.get("status") == RUN_LOCKED:
        return EXIT_LOCKED
    if not result.get("finalized"):
        # already-terminal is an idempotent no-op; a missing reason is invalid
        return EXIT_OK if result.get("final_state") else EXIT_INVALID
    if result.get("status") in (RUN_FAILED, RUN_BLOCKED):
        return EXIT_FAILED
    return EXIT_OK


def cmd_report(args) -> int:
    ctl = _controller(args)
    if ctl is None:
        return EXIT_UNKNOWN_CAMPAIGN
    result = write_report(ctl.store, args.campaign_id)
    payload = {
        "artifact_paths": result["artifact_paths"],
        "current_state": result["report"]["current_state"],
        "incomplete": result["report"]["incomplete"],
        "configurations_tested": result["report"]["configurations_tested"],
        "safety": SAFETY_CONTRACT,
    }
    if args.json:
        payload["report"] = result["report"]
    _emit(payload, args.json)
    return EXIT_OK


# --------------------------------------------------------------------------- #
# Phase 29B research-director commands
# --------------------------------------------------------------------------- #
def _director_output_root(args) -> str:
    return args.output_root or args.artifact_root or DEFAULT_ARTIFACT_ROOT


def cmd_provider_check(args) -> int:
    from .director_provider import PROVIDER_NAMES, ProviderError, get_provider

    try:
        provider = get_provider(
            args.provider, exchange_dir=args.exchange_dir or "."
        )
    except ProviderError as exc:
        print(str(exc), file=sys.stderr)
        print("allowed providers: %s" % ", ".join(PROVIDER_NAMES),
              file=sys.stderr)
        return EXIT_INVALID
    _emit(provider.check_availability(), args.json)
    return EXIT_OK


def cmd_director_validate(args) -> int:
    from .director import validate_director_config

    cfg = _load_config(args.config)
    if cfg is None:
        return EXIT_INVALID
    verdict = validate_director_config(cfg)
    _emit(
        {
            "accepted": verdict["accepted"],
            "violations": verdict["violations"],
            "config_hash": verdict["config_hash"],
        },
        args.json,
    )
    return EXIT_OK if verdict["accepted"] else EXIT_INVALID


def cmd_director_evidence(args) -> int:
    from .director import DirectorStore
    from .evidence_pack import EvidencePackError, build_evidence_pack

    root = args.artifact_root or DEFAULT_ARTIFACT_ROOT
    try:
        store = ArtifactStore(root)
        if args.campaign_id not in store.list_campaigns():
            print("unknown campaign id: %s" % args.campaign_id, file=sys.stderr)
            return EXIT_UNKNOWN_CAMPAIGN
        director_config = None
        if args.config:
            director_config = _load_config(args.config)
            if director_config is None:
                return EXIT_INVALID
        dstore = DirectorStore(_director_output_root(args))
        pack = build_evidence_pack(
            store,
            args.campaign_id,
            director_config=director_config,
            graveyard_entries=dstore.read_graveyard(),
        )
        pack_id = dstore.save_evidence_pack(pack)
    except (ArtifactStoreError, EvidencePackError) as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_INVALID
    _emit(
        {
            "evidence_pack_id": pack_id,
            "content_hash": pack["content_hash"],
            "source_campaign": pack["source_campaign"],
            "code_commit": pack["code_commit"],
            "path": str(dstore.evidence_pack_path(pack_id)),
            "safety": SAFETY_CONTRACT,
        },
        args.json,
    )
    return EXIT_OK


def cmd_director_plan(args) -> int:
    from .director import run_director_session, validate_director_config
    from .director_provider import ProviderError, get_provider
    from .evidence_pack import EvidencePackError

    cfg = _load_config(args.config)
    if cfg is None:
        return EXIT_INVALID
    verdict = validate_director_config(cfg)
    if not verdict["accepted"]:
        _emit({"accepted": False, "violations": verdict["violations"]}, args.json)
        return EXIT_INVALID
    root = args.artifact_root or DEFAULT_ARTIFACT_ROOT
    output_root = _director_output_root(args)
    try:
        store = ArtifactStore(root)
        if args.campaign_id not in store.list_campaigns():
            print("unknown campaign id: %s" % args.campaign_id, file=sys.stderr)
            return EXIT_UNKNOWN_CAMPAIGN
        provider = get_provider(
            args.provider,
            director_config=cfg,
            exchange_dir=args.exchange_dir
            or str(Path(output_root) / "director_exchange"),
        )
        result = run_director_session(
            artifact_root=root,
            campaign_id=args.campaign_id,
            director_config=cfg,
            provider=provider,
            output_root=output_root,
        )
    except (ArtifactStoreError, EvidencePackError, ProviderError) as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_INVALID
    _emit(result, args.json)
    if result.get("status") == "INVALID_CONFIG":
        return EXIT_INVALID
    # PROVIDER_UNAVAILABLE / AWAITING_MANUAL_RESPONSE are structured, valid
    # outcomes of a bounded director invocation, not command failures.
    return EXIT_OK


def _director_store_for(args):
    from .director import DirectorStore

    return DirectorStore(_director_output_root(args))


def cmd_director_status(args) -> int:
    from .director import DirectorStoreError, build_session_status

    try:
        dstore = _director_store_for(args)
        status = build_session_status(dstore, args.session_id)
    except (ArtifactStoreError, DirectorStoreError) as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_UNKNOWN_CAMPAIGN
    _emit(status, args.json)
    return EXIT_OK


def cmd_director_report(args) -> int:
    from .director import DirectorStoreError, write_director_report

    try:
        dstore = _director_store_for(args)
        result = write_director_report(dstore, args.session_id)
    except (ArtifactStoreError, DirectorStoreError) as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_UNKNOWN_CAMPAIGN
    payload = {
        "artifact_paths": result["artifact_paths"],
        "session_state": result["report"]["session_state"],
        "reconciliation": result["report"]["reconciliation"],
        "safety": SAFETY_CONTRACT,
    }
    if args.json:
        payload["report"] = result["report"]
    _emit(payload, args.json)
    return EXIT_OK


# --------------------------------------------------------------------------- #
# Phase 29C feature-campaign commands
# --------------------------------------------------------------------------- #
def _feature_store(args):
    from .feature_campaign import FeatureCampaignStore

    return FeatureCampaignStore(args.artifact_root or DEFAULT_ARTIFACT_ROOT)


def _feature_controller(args):
    from .feature_campaign import FeatureCampaignController

    try:
        store = _feature_store(args)
        if args.feature_campaign_id not in store.list_campaigns():
            print("unknown feature campaign id: %s" % args.feature_campaign_id,
                  file=sys.stderr)
            return None
        return FeatureCampaignController(
            args.feature_campaign_id,
            artifact_root=args.artifact_root or DEFAULT_ARTIFACT_ROOT,
        )
    except ArtifactStoreError as exc:
        print(str(exc), file=sys.stderr)
        return None


def cmd_feature_validate(args) -> int:
    from .feature_campaign import validate_feature_campaign_config

    cfg = _load_config(args.config)
    if cfg is None:
        return EXIT_INVALID
    verdict = validate_feature_campaign_config(cfg)
    _emit(
        {
            "accepted": verdict["accepted"],
            "violations": verdict["violations"],
            "config_hash": verdict["config_hash"],
        },
        args.json,
    )
    return EXIT_OK if verdict["accepted"] else EXIT_INVALID


def cmd_feature_create(args) -> int:
    from .feature_campaign import create_feature_campaign

    cfg = _load_config(args.config)
    if cfg is None:
        return EXIT_INVALID
    try:
        result = create_feature_campaign(
            config=cfg,
            director_root=args.director_root,
            session_id=args.director_session_id,
            source_campaign_id=args.source_campaign_id,
            artifact_root=args.artifact_root,
        )
    except ArtifactStoreError as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_INVALID
    _emit(result, args.json)
    if result.get("created"):
        return EXIT_OK
    if result.get("reason") in ("UNKNOWN_DIRECTOR_SESSION",
                                "UNKNOWN_SOURCE_CAMPAIGN"):
        return EXIT_UNKNOWN_CAMPAIGN
    return EXIT_INVALID


def cmd_feature_plan(args) -> int:
    ctl = _feature_controller(args)
    if ctl is None:
        return EXIT_UNKNOWN_CAMPAIGN
    _emit(ctl.plan(), args.json)
    return EXIT_OK


def _feature_run_exit(result: Dict[str, Any]) -> int:
    status = result.get("status")
    if status in (RUN_OK, RUN_ALREADY_COMPLETE, RUN_PAUSED):
        return EXIT_OK
    if status == RUN_LOCKED:
        return EXIT_LOCKED
    if status in (RUN_FAILED, RUN_ALREADY_FAILED, RUN_BLOCKED):
        return EXIT_FAILED
    return EXIT_OK


def cmd_feature_run(args) -> int:
    ctl = _feature_controller(args)
    if ctl is None:
        return EXIT_UNKNOWN_CAMPAIGN
    result = ctl.run(max_experiments=args.max_experiments)
    _emit(result, args.json)
    return _feature_run_exit(result)


def cmd_feature_status(args) -> int:
    ctl = _feature_controller(args)
    if ctl is None:
        return EXIT_UNKNOWN_CAMPAIGN
    _emit(ctl.status(), args.json)
    return EXIT_OK


def cmd_feature_report(args) -> int:
    from .feature_campaign import write_feature_report

    ctl = _feature_controller(args)
    if ctl is None:
        return EXIT_UNKNOWN_CAMPAIGN
    result = write_feature_report(ctl.store, args.feature_campaign_id)
    payload = {
        "artifact_paths": result["artifact_paths"],
        "current_state": result["report"]["current_state"],
        "experiments_by_status": result["report"]["experiments_by_status"],
        "safety": SAFETY_CONTRACT,
    }
    if args.json:
        payload["report"] = result["report"]
    _emit(payload, args.json)
    return EXIT_OK


def cmd_director_feedback(args) -> int:
    from .director_feedback import run_feedback_cycle
    from .director_provider import ProviderError
    from .feature_campaign import FeatureCampaignError

    try:
        store = _feature_store(args)
        if args.feature_campaign_id not in store.list_campaigns():
            print("unknown feature campaign id: %s" % args.feature_campaign_id,
                  file=sys.stderr)
            return EXIT_UNKNOWN_CAMPAIGN
        result = run_feedback_cycle(
            store,
            args.feature_campaign_id,
            provider_name=args.provider,
            exchange_dir=args.exchange_dir,
        )
    except (ArtifactStoreError, FeatureCampaignError, ProviderError) as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_INVALID
    _emit(result, args.json)
    if result.get("status") in (
        "FEEDBACK_CYCLE_COMPLETE",
        "FEEDBACK_CYCLE_LIMIT_REACHED",
        "AWAITING_MANUAL_RESPONSE",
        "PROVIDER_UNAVAILABLE",
    ):
        return EXIT_OK
    return EXIT_INVALID


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m research_agent.cli",
        description="Phase 29A autonomous research agent (research-only; no "
        "orders, no broker, no operational changes)",
    )
    sub = p.add_subparsers(dest="command", required=True)

    def _common(sp, campaign=True):
        sp.add_argument("--artifact-root", default=None,
                        help="artifact root (default %s)" % DEFAULT_ARTIFACT_ROOT)
        sp.add_argument("--json", action="store_true", help="machine-readable output")
        if campaign:
            sp.add_argument("--campaign-id", required=True)

    sp = sub.add_parser("validate-config", help="validate a campaign config")
    sp.add_argument("--config", required=True)
    _common(sp, campaign=False)
    sp.set_defaults(fn=cmd_validate_config)

    sp = sub.add_parser("create", help="create a campaign from a config")
    sp.add_argument("--config", required=True)
    _common(sp, campaign=False)
    sp.set_defaults(fn=cmd_create)

    sp = sub.add_parser("plan", help="plan without executing (read-only)")
    _common(sp)
    sp.add_argument("--max-experiments", type=int, default=None)
    sp.set_defaults(fn=cmd_plan)

    sp = sub.add_parser("run", help="run or resume a campaign")
    _common(sp)
    sp.add_argument("--max-experiments", type=int, default=None)
    sp.add_argument("--dry-run", action="store_true",
                    help="plan only; execute nothing")
    sp.set_defaults(fn=cmd_run)

    sp = sub.add_parser("status", help="campaign status")
    _common(sp)
    sp.set_defaults(fn=cmd_status)

    sp = sub.add_parser("pause", help="request a graceful pause")
    _common(sp)
    sp.set_defaults(fn=cmd_pause)

    sp = sub.add_parser("resume", help="clear pause request and continue")
    _common(sp)
    sp.add_argument("--max-experiments", type=int, default=None)
    sp.add_argument("--dry-run", action="store_true")
    sp.set_defaults(fn=cmd_resume)

    sp = sub.add_parser(
        "finalize",
        help="explicitly end a campaign with remaining work, recording why "
             "that work is abandoned (required --reason)",
    )
    _common(sp)
    sp.add_argument("--reason", required=True,
                    help="why the remaining supported work is being abandoned")
    sp.set_defaults(fn=cmd_finalize)

    sp = sub.add_parser("report", help="generate the campaign report")
    _common(sp)
    sp.set_defaults(fn=cmd_report)

    # ---- Phase 29B research-director commands ----------------------------
    sp = sub.add_parser("provider-check",
                        help="structured director-provider availability check")
    sp.add_argument("--provider", required=True,
                    help="fixture | file-exchange | claude-code")
    sp.add_argument("--exchange-dir", default=None)
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(fn=cmd_provider_check)

    sp = sub.add_parser("director-validate",
                        help="validate a Phase 29B director configuration")
    sp.add_argument("--config", required=True)
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(fn=cmd_director_validate)

    sp = sub.add_parser("director-evidence",
                        help="build the deterministic evidence pack for a "
                             "COMPLETE campaign")
    sp.add_argument("--campaign-id", required=True)
    sp.add_argument("--artifact-root", default=None)
    sp.add_argument("--output-root", default=None,
                    help="director output root (default: artifact root)")
    sp.add_argument("--config", default=None,
                    help="optional director config (budgets/exhausted dims)")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(fn=cmd_director_evidence)

    sp = sub.add_parser("director-plan",
                        help="run one bounded director cycle (evidence -> "
                             "provider plan -> deterministic policy)")
    sp.add_argument("--campaign-id", required=True)
    sp.add_argument("--artifact-root", default=None)
    sp.add_argument("--config", required=True)
    sp.add_argument("--provider", required=True,
                    help="fixture | file-exchange | claude-code")
    sp.add_argument("--output-root", default=None)
    sp.add_argument("--exchange-dir", default=None)
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(fn=cmd_director_plan)

    sp = sub.add_parser("director-status",
                        help="reconciled counts for one director session")
    sp.add_argument("--session-id", required=True)
    sp.add_argument("--artifact-root", default=None)
    sp.add_argument("--output-root", default=None)
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(fn=cmd_director_status)

    sp = sub.add_parser("director-report",
                        help="write the director session report")
    sp.add_argument("--session-id", required=True)
    sp.add_argument("--artifact-root", default=None)
    sp.add_argument("--output-root", default=None)
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(fn=cmd_director_report)

    # ---- Phase 29C feature-campaign commands -----------------------------
    def _feature_common(sp, campaign=True):
        sp.add_argument("--artifact-root", default=None)
        sp.add_argument("--json", action="store_true")
        if campaign:
            sp.add_argument("--feature-campaign-id", required=True)

    sp = sub.add_parser("feature-validate",
                        help="validate a Phase 29C feature-campaign config")
    sp.add_argument("--config", required=True)
    _feature_common(sp, campaign=False)
    sp.set_defaults(fn=cmd_feature_validate)

    sp = sub.add_parser("feature-create",
                        help="create a feature campaign from a COMPLETE "
                             "director session")
    sp.add_argument("--director-session-id", required=True)
    sp.add_argument("--director-root", required=True,
                    help="director output root holding director_sessions")
    sp.add_argument("--source-campaign-id", required=True)
    sp.add_argument("--config", required=True)
    _feature_common(sp, campaign=False)
    sp.set_defaults(fn=cmd_feature_create)

    sp = sub.add_parser("feature-plan",
                        help="read-only reconciled feature-campaign plan")
    _feature_common(sp)
    sp.set_defaults(fn=cmd_feature_plan)

    sp = sub.add_parser("feature-run", help="run a feature campaign")
    _feature_common(sp)
    sp.add_argument("--max-experiments", type=int, default=None,
                    help="per-invocation limit; the campaign PAUSES at the "
                         "limit and stays resumable")
    sp.set_defaults(fn=cmd_feature_run)

    sp = sub.add_parser("feature-resume",
                        help="resume a paused feature campaign (executes "
                             "only pending work; completed experiments are "
                             "never rerun)")
    _feature_common(sp)
    sp.add_argument("--max-experiments", type=int, default=None)
    sp.set_defaults(fn=cmd_feature_run)

    sp = sub.add_parser("feature-status",
                        help="read-only feature-campaign status")
    _feature_common(sp)
    sp.set_defaults(fn=cmd_feature_status)

    sp = sub.add_parser("feature-report",
                        help="write the feature-campaign report")
    _feature_common(sp)
    sp.set_defaults(fn=cmd_feature_report)

    sp = sub.add_parser("director-feedback",
                        help="run the ONE bounded director feedback cycle "
                             "over the persisted feedback packets")
    _feature_common(sp)
    sp.add_argument("--provider", required=True,
                    help="fixture | file-exchange | claude-code")
    sp.add_argument("--exchange-dir", default=None)
    sp.set_defaults(fn=cmd_director_feedback)

    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
