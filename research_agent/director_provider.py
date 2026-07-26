"""Phase 29B provider-neutral research-director providers.

Three implementations behind one protocol:

- FixtureDirectorProvider: fully deterministic, offline, used by all tests
  and bounded smokes. May NEVER be used to register a real challenger.
- FileExchangeDirectorProvider: writes a sanitized request file and reads a
  manually supplied response file (development without any external API).
- ClaudeCodeDirectorProvider: uses an ALREADY-INSTALLED, already-
  authenticated Claude CLI. Fixed executable, fixed argument structure,
  shell=False, enforced timeout, JSON-only output. Nothing is installed or
  authenticated here; an absent/broken CLI becomes a structured
  PROVIDER_UNAVAILABLE result, never a crash.

No provider can execute LLM-chosen commands: the executable name and the
argument vector are module constants, the request travels via stdin, and
secrets are never serialized (assert_no_secrets on everything persisted).
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, Optional

from .artifact_store import assert_no_secrets, content_hash, write_json_atomic
from .feature_dsl import expression_signature

PROVIDER_SCHEMA_VERSION = "29B.1"

PROVIDER_FIXTURE = "fixture"
PROVIDER_FILE_EXCHANGE = "file-exchange"
PROVIDER_CLAUDE_CODE = "claude-code"
PROVIDER_NAMES = (PROVIDER_FIXTURE, PROVIDER_FILE_EXCHANGE, PROVIDER_CLAUDE_CODE)

STATUS_OK = "OK"
STATUS_PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
STATUS_PROVIDER_ERROR = "PROVIDER_ERROR"
STATUS_INVALID_RESPONSE = "INVALID_RESPONSE"
STATUS_AWAITING_MANUAL_RESPONSE = "AWAITING_MANUAL_RESPONSE"

# Fixed executable + fixed argument structure for the Claude Code provider.
# Never derived from config, request content, or any LLM output.
CLAUDE_EXECUTABLE = "claude"
CLAUDE_VERSION_ARGS = ("--version",)
CLAUDE_PLAN_ARGS = ("-p", "--output-format", "json")
DEFAULT_TIMEOUT_SECONDS = 180
VERSION_CHECK_TIMEOUT_SECONDS = 30
_MAX_CAPTURED_CHARS = 2000


class ProviderError(RuntimeError):
    pass


def _envelope(provider: str, status: str, **extra: Any) -> Dict[str, Any]:
    doc: Dict[str, Any] = {
        "schema_version": PROVIDER_SCHEMA_VERSION,
        "provider": provider,
        "status": status,
        "response": extra.pop("response", None),
    }
    doc.update(extra)
    return doc


class ResearchDirectorProvider:
    """Protocol: every provider implements these two methods."""

    name = "abstract"

    def check_availability(self) -> Dict[str, Any]:
        raise NotImplementedError

    def generate_research_plan(self, request: Dict[str, Any]) -> Dict[str, Any]:
        raise NotImplementedError


# --------------------------------------------------------------------------- #
# fixture provider
# --------------------------------------------------------------------------- #
def _fixture_feature_momentum_persistence(mom_field: str) -> Dict[str, Any]:
    return {
        "feature_id": "feat_momentum_persistence",
        "description": "trailing 3-month mean of the lagged momentum signal: "
        "persistent momentum ranks should be less noisy month to month",
        "source_family": "momentum",
        "expression": {
            "op": "rolling_mean",
            "params": {"window": 3},
            "inputs": [
                {"op": "lag", "params": {"periods": 1},
                 "inputs": [{"op": "source", "field": mom_field}]}
            ],
        },
    }


def _fixture_feature_fundamental_change(fund_field: str) -> Dict[str, Any]:
    return {
        "feature_id": "feat_fundamental_change",
        "description": "cross-sectional rank of the 3-month change in the "
        "fundamental composite: improvement, not level",
        "source_family": "fundamental",
        "expression": {
            "op": "cross_sectional_rank",
            "params": {},
            "inputs": [
                {"op": "difference", "params": {"periods": 3},
                 "inputs": [{"op": "source", "field": fund_field}]}
            ],
        },
    }


def _fixture_feature_quality_momentum(mom_field: str, fund_field: str) -> Dict[str, Any]:
    return {
        "feature_id": "feat_quality_momentum_interaction",
        "description": "interaction of trailing z-scores of momentum and the "
        "fundamental composite: momentum confirmed by quality",
        "source_family": "mixed",
        "expression": {
            "op": "interaction",
            "params": {},
            "inputs": [
                {"op": "zscore", "params": {"window": 12},
                 "inputs": [{"op": "source", "field": mom_field}]},
                {"op": "zscore", "params": {"window": 12},
                 "inputs": [{"op": "source", "field": fund_field}]},
            ],
        },
    }


def _fixture_proposal(
    hypothesis_id: str,
    title: str,
    diagnosis: str,
    feature: Dict[str, Any],
    mechanism: str,
    priority: int,
    cost_class: str,
) -> Dict[str, Any]:
    return {
        "hypothesis_id": hypothesis_id,
        "title": title,
        "diagnosis": diagnosis,
        "supporting_evidence": [
            "binding_weaknesses.rank_ic",
            "campaign_outcome.robustness_decision_counts",
            "exhausted_research_dimensions",
        ],
        "proposed_feature": {"features": [feature]},
        "expected_mechanism": mechanism,
        "expected_metric_improvement": {
            "metric": "rank_ic_t",
            "direction": "increase",
            "material_threshold": 0.5,
        },
        "falsification_condition": "the feature's monthly rank-IC t-stat does "
        "not exceed the baseline signal's by a material margin on the full "
        "sample, or the improvement is confined to one subperiod",
        "leakage_analysis": "all transforms are trailing (lag/rolling/zscore "
        "over formation-month values only); sources are point-in-time panel "
        "fields; no fwd_* target field, no forward shift, no centered window",
        "required_tools": [
            "run_parameter_experiment",
            "calculate_rank_ic",
            "run_cost_sensitivity",
        ],
        "estimated_compute_cost": {"cost_class": cost_class, "est_experiments": 2},
        "priority": priority,
        "parent_hypothesis": None,
        "duplicate_search_signature": expression_signature(feature),
        "stop_condition": "stop the branch after 2 primary experiments with "
        "no material rank-IC improvement",
        "follow_up_policy": "on success request the robustness battery; on "
        "failure record a graveyard entry and stop the branch",
    }


def build_fixture_response(
    request: Dict[str, Any], *, n_proposals: int = 3
) -> Dict[str, Any]:
    """Deterministic, schema-valid response derived only from the request."""
    pack = request.get("evidence_pack") or {}
    feats = pack.get("available_features") or {}
    mom = list(feats.get("momentum_sources") or [])
    fund = list(feats.get("fundamental_sources") or [])
    mom_field = "mom_6_1" if "mom_6_1" in mom else (sorted(mom)[0] if mom else "mom_6_1")
    fund_field = (
        "composite_sn" if "composite_sn" in fund
        else (sorted(fund)[0] if fund else "composite_sn")
    )

    all_proposals = [
        _fixture_proposal(
            "hyp_29b_momentum_persistence",
            "Momentum persistence stability filter",
            "the binding weakness is monthly rank-IC; a noisy month-to-month "
            "momentum rank should be stabilized by trailing persistence",
            _fixture_feature_momentum_persistence(mom_field),
            "averaging the lagged signal suppresses one-month noise, raising "
            "the cross-sectional rank correlation with forward returns",
            1, "cheap",
        ),
        _fixture_proposal(
            "hyp_29b_fundamental_change",
            "Fundamental improvement (delta) signal",
            "the level of the fundamental composite is exhausted as a blend "
            "input; its CHANGE is an untested, orthogonal signal dimension",
            _fixture_feature_fundamental_change(fund_field),
            "improving fundamentals lead price adjustment; ranking the "
            "3-month delta captures revision-like information from owned data",
            1, "cheap",
        ),
        _fixture_proposal(
            "hyp_29b_quality_momentum_interaction",
            "Quality-confirmed momentum interaction",
            "momentum unconfirmed by fundamentals may mean-revert; an "
            "interaction feature conditions momentum on quality support",
            _fixture_feature_quality_momentum(mom_field, fund_field),
            "the product of trailing z-scores emphasizes names where both "
            "signals agree, which should be where rank-IC is strongest",
            2, "medium",
        ),
    ]
    proposals = all_proposals[: max(0, int(n_proposals))]
    decisions = [
        {
            "decision": "TEST",
            "hypothesis_id": p["hypothesis_id"],
            "evidence_refs": p["supporting_evidence"],
            "reasoning_summary": "feature-level hypothesis targeting the "
            "binding rank-IC weakness; outside every exhausted dimension",
            "uncertainties": [
                "monthly panel history may be short for 12-month windows",
                "the delta signal may be collinear with the level",
            ],
            "falsification_condition": p["falsification_condition"],
            "next_deterministic_action": "validate the DSL specification and "
            "queue at most 2 bounded primary experiments",
            "budget_impact": {"estimated_primary_experiments": 2},
            "safety_confirmation": {
                "research_only": True,
                "no_operational_action": True,
                "no_gate_change": True,
                "no_budget_increase": True,
            },
        }
        for p in proposals
    ]
    return {
        "schema_version": PROVIDER_SCHEMA_VERSION,
        "provider": PROVIDER_FIXTURE,
        "proposals": proposals,
        "decisions": decisions,
    }


class FixtureDirectorProvider(ResearchDirectorProvider):
    """Deterministic offline provider. Tests-and-smoke only.

    A fixture-generated plan can never lead to real challenger registration:
    the director layer has no registration capability at all, and this
    provider additionally marks its envelope fixture=True so any downstream
    consumer can refuse.
    """

    name = PROVIDER_FIXTURE
    may_register_challengers = False

    def __init__(
        self,
        *,
        n_proposals: int = 3,
        fixture_response: Optional[Dict[str, Any]] = None,
    ):
        self.n_proposals = int(n_proposals)
        self.fixture_response = fixture_response

    def check_availability(self) -> Dict[str, Any]:
        return {
            "provider": self.name,
            "available": True,
            "status": STATUS_OK,
            "deterministic": True,
            "may_register_challengers": False,
        }

    def generate_research_plan(self, request: Dict[str, Any]) -> Dict[str, Any]:
        response = self.fixture_response or build_fixture_response(
            request, n_proposals=self.n_proposals
        )
        return _envelope(
            self.name, STATUS_OK, response=response, fixture=True,
            request_id=request.get("request_id"),
        )


# --------------------------------------------------------------------------- #
# file-exchange provider
# --------------------------------------------------------------------------- #
class FileExchangeDirectorProvider(ResearchDirectorProvider):
    """Writes a sanitized request file; reads a manually supplied response.

    Supports development without any external API access: an operator (or a
    separate interactive session) reads director_request_<id>.json and
    writes director_response_<id>.json next to it.
    """

    name = PROVIDER_FILE_EXCHANGE

    def __init__(self, exchange_dir: str):
        self.exchange_dir = Path(exchange_dir)

    def _paths(self, request_id: str) -> Dict[str, Path]:
        return {
            "request": self.exchange_dir / ("director_request_%s.json" % request_id),
            "response": self.exchange_dir / ("director_response_%s.json" % request_id),
        }

    def check_availability(self) -> Dict[str, Any]:
        return {
            "provider": self.name,
            "available": True,
            "status": STATUS_OK,
            "exchange_dir": str(self.exchange_dir),
        }

    def generate_research_plan(self, request: Dict[str, Any]) -> Dict[str, Any]:
        assert_no_secrets(request)
        request_id = request.get("request_id") or (
            "dreq_" + content_hash(request)[:16]
        )
        paths = self._paths(request_id)
        write_json_atomic(paths["request"], request)
        if not paths["response"].exists():
            return _envelope(
                self.name, STATUS_AWAITING_MANUAL_RESPONSE,
                request_id=request_id,
                request_path=str(paths["request"]),
                response_path=str(paths["response"]),
                note="write the JSON response to response_path, then rerun "
                "director-plan with the same arguments",
            )
        try:
            with open(paths["response"], "r", encoding="utf-8") as fh:
                parsed = json.load(fh)
        except (OSError, json.JSONDecodeError) as exc:
            return _envelope(
                self.name, STATUS_INVALID_RESPONSE,
                request_id=request_id,
                detail="response file is not valid JSON: %s" % exc,
            )
        if not isinstance(parsed, dict):
            return _envelope(
                self.name, STATUS_INVALID_RESPONSE,
                request_id=request_id,
                detail="response must be a single JSON object",
            )
        return _envelope(self.name, STATUS_OK, response=parsed,
                         request_id=request_id)


# --------------------------------------------------------------------------- #
# claude-code provider
# --------------------------------------------------------------------------- #
class ClaudeCodeDirectorProvider(ResearchDirectorProvider):
    name = PROVIDER_CLAUDE_CODE

    def __init__(self, *, timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS):
        self.timeout_seconds = int(timeout_seconds)

    def _resolve_executable(self) -> Optional[str]:
        return shutil.which(CLAUDE_EXECUTABLE)

    def check_availability(self) -> Dict[str, Any]:
        exe = self._resolve_executable()
        if not exe:
            return {
                "provider": self.name,
                "available": False,
                "status": STATUS_PROVIDER_UNAVAILABLE,
                "checked_executable": CLAUDE_EXECUTABLE,
                "reason": "claude executable not found on PATH; install/"
                "authentication is deliberately out of scope for this agent",
            }
        try:
            proc = subprocess.run(
                [exe, *CLAUDE_VERSION_ARGS],
                capture_output=True,
                text=True,
                timeout=VERSION_CHECK_TIMEOUT_SECONDS,
                shell=False,
            )
        except (OSError, subprocess.TimeoutExpired, subprocess.SubprocessError) as exc:
            return {
                "provider": self.name,
                "available": False,
                "status": STATUS_PROVIDER_UNAVAILABLE,
                "checked_executable": exe,
                "reason": "provider-check command failed: %s"
                % type(exc).__name__,
            }
        if proc.returncode != 0:
            return {
                "provider": self.name,
                "available": False,
                "status": STATUS_PROVIDER_UNAVAILABLE,
                "checked_executable": exe,
                "returncode": proc.returncode,
                "reason": (proc.stderr or proc.stdout or "")[:_MAX_CAPTURED_CHARS],
            }
        return {
            "provider": self.name,
            "available": True,
            "status": STATUS_OK,
            "executable": exe,
            "version": (proc.stdout or "").strip()[:200],
            "timeout_seconds": self.timeout_seconds,
        }

    def generate_research_plan(self, request: Dict[str, Any]) -> Dict[str, Any]:
        assert_no_secrets(request)
        availability = self.check_availability()
        if not availability.get("available"):
            return _envelope(
                self.name, STATUS_PROVIDER_UNAVAILABLE,
                request_id=request.get("request_id"),
                availability=availability,
            )
        exe = availability["executable"]
        # Fixed argv; the request travels on stdin. Nothing from the request
        # or any LLM output ever reaches the argument vector.
        argv = [exe, *CLAUDE_PLAN_ARGS]
        try:
            proc = subprocess.run(
                argv,
                input=json.dumps(request, sort_keys=True, default=str),
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                shell=False,
            )
        except subprocess.TimeoutExpired:
            return _envelope(
                self.name, STATUS_PROVIDER_ERROR,
                request_id=request.get("request_id"),
                timeout=True,
                detail="provider exceeded the enforced timeout of %ds"
                % self.timeout_seconds,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return _envelope(
                self.name, STATUS_PROVIDER_ERROR,
                request_id=request.get("request_id"),
                detail="provider invocation failed: %s" % type(exc).__name__,
            )
        if proc.returncode != 0:
            return _envelope(
                self.name, STATUS_PROVIDER_ERROR,
                request_id=request.get("request_id"),
                returncode=proc.returncode,
                stderr=(proc.stderr or "")[:_MAX_CAPTURED_CHARS],
            )
        parsed = self._parse_json_only(proc.stdout or "")
        if parsed is None:
            return _envelope(
                self.name, STATUS_INVALID_RESPONSE,
                request_id=request.get("request_id"),
                detail="JSON-only output required; provider returned "
                "non-JSON content",
                stdout_prefix=(proc.stdout or "")[:200],
            )
        return _envelope(self.name, STATUS_OK, response=parsed,
                         request_id=request.get("request_id"))

    @staticmethod
    def _parse_json_only(stdout: str) -> Optional[Dict[str, Any]]:
        try:
            outer = json.loads(stdout)
        except json.JSONDecodeError:
            return None
        if not isinstance(outer, dict):
            return None
        # `claude -p --output-format json` wraps the reply: the actual model
        # reply is the "result" string, which must itself be JSON.
        if "result" in outer and isinstance(outer["result"], str):
            try:
                inner = json.loads(outer["result"])
            except json.JSONDecodeError:
                return None
            return inner if isinstance(inner, dict) else None
        return outer


def get_provider(
    name: str,
    *,
    director_config: Optional[Dict[str, Any]] = None,
    exchange_dir: Optional[str] = None,
    fixture_response: Optional[Dict[str, Any]] = None,
) -> ResearchDirectorProvider:
    cfg = (director_config or {}).get("provider") or {}
    if name == PROVIDER_FIXTURE:
        return FixtureDirectorProvider(
            n_proposals=int((cfg.get("fixture") or {}).get("n_proposals", 3)),
            fixture_response=fixture_response,
        )
    if name == PROVIDER_FILE_EXCHANGE:
        if not exchange_dir:
            raise ProviderError(
                "file-exchange provider requires an exchange directory"
            )
        return FileExchangeDirectorProvider(exchange_dir)
    if name == PROVIDER_CLAUDE_CODE:
        # config key is claude_cli (not *_code): the schema layer refuses any
        # key containing an executable-content token, including "code"
        return ClaudeCodeDirectorProvider(
            timeout_seconds=int(
                (cfg.get("claude_cli") or {}).get(
                    "timeout_seconds", DEFAULT_TIMEOUT_SECONDS
                )
            ),
        )
    raise ProviderError("unknown provider: %s (allowed: %s)"
                        % (name, ", ".join(PROVIDER_NAMES)))


__all__ = [
    "CLAUDE_EXECUTABLE",
    "CLAUDE_PLAN_ARGS",
    "CLAUDE_VERSION_ARGS",
    "ClaudeCodeDirectorProvider",
    "DEFAULT_TIMEOUT_SECONDS",
    "FileExchangeDirectorProvider",
    "FixtureDirectorProvider",
    "PROVIDER_CLAUDE_CODE",
    "PROVIDER_FILE_EXCHANGE",
    "PROVIDER_FIXTURE",
    "PROVIDER_NAMES",
    "ProviderError",
    "ResearchDirectorProvider",
    "STATUS_AWAITING_MANUAL_RESPONSE",
    "STATUS_INVALID_RESPONSE",
    "STATUS_OK",
    "STATUS_PROVIDER_ERROR",
    "STATUS_PROVIDER_UNAVAILABLE",
    "build_fixture_response",
    "get_provider",
]
