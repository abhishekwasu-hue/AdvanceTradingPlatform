"""H-C2 (ADR-0019): the Copilot agent's tool registry - typed, validated, tenant-scoped.

* Every tool declares a name, a description, a pydantic input model (its JSON schema is what the model sees), a kind
  (`read`, or `proposal` - H-C2b, which only ever creates PROPOSED actions, ADR-0006), cost units and a timeout.
* A proposal tool must take the trader's own words (`quote`) and a `reason`, and `run_tool` runs it only when the
  agent loop's injection guard has cleared the call (`proposals.guard`).
* The tenant and the user come from the request's session (`ToolContext`), never from the model's arguments.
* Every result is `{data, as_of, source, data_timestamps}`; third-party text (news headlines) is marked untrusted so
  the loop wraps it in `<untrusted_data>` and it can never trigger a tool.
* A tool never sends, modifies or cancels an order and never changes a LIVE setting.
"""
from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, List, Literal, Optional, Type

from pydantic import BaseModel, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import User


@dataclass
class ToolContext:
    session: AsyncSession
    tenant_id: int
    user: User
    now: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class ToolResult:
    ok: bool
    data: Any
    as_of: Optional[str]
    source: str
    data_timestamps: Dict[str, Any] = field(default_factory=dict)
    untrusted: bool = False                 # third-party text inside: wrapped as data, never instructions
    error: Optional[str] = None
    duration_ms: int = 0

    def payload(self) -> Dict[str, Any]:
        if not self.ok:
            return {"error": self.error}
        return {"data": self.data, "as_of": self.as_of, "source": self.source, "data_timestamps": self.data_timestamps}

    def text(self) -> str:
        body = json.dumps(self.payload(), default=str, ensure_ascii=False)
        if self.untrusted:
            # The H-C1 / P0.8-B convention: third-party strings travel as escaped data, never as instructions.
            body = body.replace("<", "&lt;").replace(">", "&gt;")
            return f"<untrusted_data source=\"{self.source}\">{body}</untrusted_data>"
        return body


ToolFn = Callable[[ToolContext, BaseModel], Awaitable[ToolResult]]


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    input_model: Type[BaseModel]
    fn: ToolFn
    kind: Literal["read", "proposal"] = "read"
    cost_units: int = 1
    timeout_seconds: float = 10.0

    def schema(self) -> Dict[str, Any]:
        """The JSON schema the provider sees (additionalProperties false, so strict mode can be used)."""
        schema = self.input_model.model_json_schema()
        schema.setdefault("type", "object")
        schema["additionalProperties"] = False
        schema.pop("title", None)
        return schema


_REGISTRY: Dict[str, Tool] = {}


def register(tool: Tool) -> Tool:
    if tool.name in _REGISTRY:
        raise ValueError(f"tool {tool.name!r} registered twice")
    if tool.kind == "proposal" and not {"quote", "reason"} <= set(tool.input_model.model_fields):
        raise ValueError(f"proposal tool {tool.name!r} must take the trader's own words (quote) and a reason - the guard checks them")
    _REGISTRY[tool.name] = tool
    return tool


def registry() -> Dict[str, Tool]:
    from app.ai.tools import market, proposals, read  # noqa: F401 - registers the tools on first use
    return dict(_REGISTRY)


def specs(names: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """Provider-neutral tool specs: name, description, input_schema."""
    tools = registry()
    return [{"name": t.name, "description": t.description, "input_schema": t.schema()} for n, t in sorted(tools.items()) if names is None or n in names]


def names(kind: str) -> List[str]:
    return sorted(n for n, t in registry().items() if t.kind == kind)


async def run_tool(name: str, arguments: Dict[str, Any], ctx: ToolContext, *, guard_cleared: bool = False) -> ToolResult:
    """Validates the model's arguments against the tool's input model, runs it under its timeout, never raises. A
    proposal tool runs only with `guard_cleared` - set by the agent loop after `proposals.guard` passed the call."""
    started = time.monotonic()
    tool = registry().get(name)
    if tool is None:
        return ToolResult(False, None, None, name, error=f"unknown tool {name!r}")
    if tool.kind == "proposal" and not guard_cleared:
        return ToolResult(False, None, None, name, error="a proposal tool runs only after the agent's guard has cleared the call")
    try:
        args = tool.input_model.model_validate(arguments or {})
    except ValidationError as exc:
        return ToolResult(False, None, None, name, error=f"invalid arguments: {exc.errors()[:3]}")
    try:
        result = await asyncio.wait_for(tool.fn(ctx, args), timeout=tool.timeout_seconds)
    except asyncio.TimeoutError:
        result = ToolResult(False, None, None, name, error=f"timed out after {tool.timeout_seconds:g}s")
    except Exception as exc:  # noqa: BLE001 - a failing tool is reported to the model, never crashes the loop
        result = ToolResult(False, None, None, name, error=f"{type(exc).__name__}: {str(exc)[:200]}")
    result.duration_ms = int((time.monotonic() - started) * 1000)
    return result


__all__ = ["Tool", "ToolContext", "ToolResult", "register", "registry", "specs", "names", "run_tool"]
