"""Deterministic tool registry.

Every research calculation the agent can request goes through here:

- strict typed input validation (unknown or mistyped inputs are rejected
  with a structured verdict, never a crash)
- structured output envelopes with status, duration, input hash and the
  exact data cutoff in force
- exceptions converted into structured tool failures
- no shell access of any kind: tools are plain Python callables over owned
  local data; this module imports neither subprocess nor os.system

The registry itself is engine-agnostic; the concrete adapters live in
``tool_adapters``.
"""

from __future__ import annotations

import time
from typing import Any, Callable, Dict, List, Optional

from . import SAFETY_CONTRACT, SCHEMA_VERSION
from .artifact_store import _now_iso, content_hash

STATUS_OK = "OK"
STATUS_REJECTED_INVALID = "REJECTED_INVALID"
STATUS_REJECTED_UNSUPPORTED = "REJECTED_UNSUPPORTED"
STATUS_FAILED = "TOOL_FAILED"

COST_CHEAP = "cheap"
COST_MEDIUM = "medium"
COST_EXPENSIVE = "expensive"

_ALLOWED_TYPES = {
    "str": str,
    "int": int,
    "float": (int, float),
    "bool": bool,
    "object": dict,
    "list": list,
}


class ToolDefinitionError(RuntimeError):
    pass


class UnknownToolError(KeyError):
    pass


class Tool:
    def __init__(
        self,
        *,
        name: str,
        description: str,
        fn: Callable[..., Dict[str, Any]],
        input_schema: Dict[str, Dict[str, Any]],
        cost_class: str = COST_MEDIUM,
        deterministic: bool = True,
    ):
        for field, meta in input_schema.items():
            if meta.get("type") not in _ALLOWED_TYPES:
                raise ToolDefinitionError(
                    "tool %s input %s has unknown type %r" % (name, field, meta.get("type"))
                )
        self.name = name
        self.description = description
        self.fn = fn
        self.input_schema = input_schema
        self.cost_class = cost_class
        self.deterministic = deterministic

    def validate_inputs(self, inputs: Dict[str, Any]) -> List[Dict[str, Any]]:
        problems: List[Dict[str, Any]] = []
        if not isinstance(inputs, dict):
            return [{"field": "$", "issue": "inputs must be an object"}]
        for field, meta in self.input_schema.items():
            if meta.get("required") and field not in inputs:
                problems.append({"field": field, "issue": "required input missing"})
        for field, value in inputs.items():
            meta = self.input_schema.get(field)
            if meta is None:
                problems.append({"field": field, "issue": "unknown input (strict schema)"})
                continue
            expected = _ALLOWED_TYPES[meta["type"]]
            if value is not None and not isinstance(value, expected):
                problems.append(
                    {
                        "field": field,
                        "issue": "expected %s" % meta["type"],
                        "value": repr(value)[:80],
                    }
                )
            if isinstance(value, bool) and meta["type"] in ("int", "float"):
                problems.append({"field": field, "issue": "expected %s" % meta["type"]})
        return problems


class ToolRegistry:
    def __init__(self):
        self._tools: Dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ToolDefinitionError("duplicate tool name: %s" % tool.name)
        self._tools[tool.name] = tool

    def names(self) -> List[str]:
        return sorted(self._tools)

    def get(self, name: str) -> Tool:
        if name not in self._tools:
            raise UnknownToolError(name)
        return self._tools[name]

    def describe(self) -> List[Dict[str, Any]]:
        return [
            {
                "name": t.name,
                "description": t.description,
                "cost_class": t.cost_class,
                "inputs": {
                    f: {"type": m["type"], "required": bool(m.get("required"))}
                    for f, m in t.input_schema.items()
                },
            }
            for t in (self._tools[n] for n in self.names())
        ]

    def run(
        self,
        name: str,
        inputs: Optional[Dict[str, Any]] = None,
        *,
        context: Any = None,
        data_cutoff: Optional[str] = None,
        seed: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Execute a tool with a full structured envelope. Never raises for
        tool-level failures; unknown tool names are rejected structurally too."""
        inputs = inputs or {}
        started = _now_iso()
        t0 = time.perf_counter()
        envelope = {
            "schema_version": SCHEMA_VERSION,
            "tool": name,
            "started_at": started,
            "data_cutoff": data_cutoff,
            "seed": seed,
            "inputs_hash": content_hash(inputs),
            "safety": dict(SAFETY_CONTRACT),
        }
        if name not in self._tools:
            envelope.update(
                {
                    "status": STATUS_REJECTED_INVALID,
                    "duration_seconds": round(time.perf_counter() - t0, 6),
                    "failure": {"type": "UnknownTool", "message": "unknown tool: %s" % name,
                                "known_tools": self.names()},
                }
            )
            return envelope
        tool = self._tools[name]
        problems = tool.validate_inputs(inputs)
        if problems:
            envelope.update(
                {
                    "status": STATUS_REJECTED_INVALID,
                    "duration_seconds": round(time.perf_counter() - t0, 6),
                    "failure": {"type": "InvalidInputs", "problems": problems},
                }
            )
            return envelope
        try:
            output = tool.fn(context, **inputs)
        except Exception as exc:  # converted, never propagated
            envelope.update(
                {
                    "status": STATUS_FAILED,
                    "duration_seconds": round(time.perf_counter() - t0, 6),
                    "failure": {"type": type(exc).__name__, "message": str(exc)[:500]},
                }
            )
            return envelope
        status = STATUS_OK
        if isinstance(output, dict) and output.get("_rejected_unsupported"):
            status = STATUS_REJECTED_UNSUPPORTED
        envelope.update(
            {
                "status": status,
                "duration_seconds": round(time.perf_counter() - t0, 6),
                "output": output,
                "artifact_paths": (output or {}).get("artifact_paths", []) if isinstance(output, dict) else [],
            }
        )
        return envelope


__all__ = [
    "COST_CHEAP",
    "COST_EXPENSIVE",
    "COST_MEDIUM",
    "STATUS_FAILED",
    "STATUS_OK",
    "STATUS_REJECTED_INVALID",
    "STATUS_REJECTED_UNSUPPORTED",
    "Tool",
    "ToolDefinitionError",
    "ToolRegistry",
    "UnknownToolError",
]
