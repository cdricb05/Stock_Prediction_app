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
"""

from __future__ import annotations

import argparse
import json
import sys
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

    sp = sub.add_parser("report", help="generate the campaign report")
    _common(sp)
    sp.set_defaults(fn=cmd_report)

    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
