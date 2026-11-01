"""M72 CP-1 (owner decision M72Q-7): ONE live run of the Copilot planner on the trader eval set, started by the owner.

The planner is the production loop (`app.ai.agent.run_agent`: the same prompt, tools, limits, refusals and answer
checks). Only two things differ from a chat request:
* the tools answer from the recorded, synthetic results in `evals/sets/copilot_trader_tools.json` (no broker, no
  database row and no user data reaches the model - the questions and the fixtures are public synthetic data), and
* nothing is written: no `agent_runs` / `agent_steps` / `llm_calls` row, no audit row, no proposal row, no spend on an
  organisation's monthly budget. The run's cost is counted here and printed.

The model is the AI key the server already has, picked by `--key` (the key itself is never printed):
* `platform` - the host's platform key (`AI_PLATFORM_PROVIDER` + `AI_PLATFORM_API_KEY[_FILE]`, app/ai/platform_key.py);
* `org`      - an organisation's own saved key (Settings > AI); `--tenant ID` names it, else the one organisation that has
               a key (several: the command lists their ids and stops, nothing is sent);
* `auto`     - (default) `platform` when the host has one, else `org`.
The model is the one the chat Copilot uses with that key (task "copilot"); `--model` overrides it for this run only.

No call cap is set here (M72Q-7: "let it cost, I will watch it"): every one of the 60 questions and the safety block
runs, each inside the planner's own per-question limits (Admin values when the database is reachable, else the
defaults). A refused question never reaches the model.

Output: the trader eval table (the four dimensions by persona and language), the planner score per job (the case's
topic), the safety block, then tokens and cost per answer and the total. `--json` prints the full report instead.
`--dry-run` runs the same loop on an offline fake model (no network, no key read, zero cost) to check the wiring.

On the server (owner, one command): /opt/atp/deploy/atp.sh ai-eval      (runs `python -m evals.planner_live` in the
backend container; any options are passed through, e.g. `ai-eval --offline` - atp.sh keeps `--dry-run` for itself).
CI and the tests never call a model: tests/test_m72_planner_live.py drives this module with a fake provider.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any
from unittest import mock

from evals import trader_eval

# The planner's tool names that read the same data as a recorded chat-job result under another name.
ALIASES = {"get_my_trade_settings": "get_my_settings", "get_positions": "get_my_positions"}
UNTRUSTED_TOOLS = {"get_news"}


class EvalSetupError(RuntimeError):
    """No key could be picked for the run: the message says why; nothing was sent."""


@dataclass
class Usage:
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cost_usd: float = 0.0
    estimated: bool = False
    seconds: float = 0.0

    def add(self, other: Usage) -> None:
        for name in ("calls", "input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens", "cost_usd", "seconds"):
            setattr(self, name, getattr(self, name) + getattr(other, name))
        self.estimated = self.estimated or other.estimated


class CountingProvider:
    """Wraps the real provider: counts every model step's tokens and prices them (app/ai/pricing.py). Passes the turn
    through unchanged; a provider error's usage is counted too (the tokens were spent)."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.name = getattr(inner, "name", "unknown")
        self.model = getattr(inner, "model", "")
        self.supports_tools = getattr(inner, "supports_tools", False)
        self.prompt_version: str | None = None
        self.case = Usage()

    def _count(self, usage: Any) -> None:
        from app.ai import pricing
        if usage is None:
            return
        self.case.calls += 1
        tin, tout = int(getattr(usage, "input_tokens", 0) or 0), int(getattr(usage, "output_tokens", 0) or 0)
        cread, cwrite = int(getattr(usage, "cache_read_tokens", 0) or 0), int(getattr(usage, "cache_write_tokens", 0) or 0)
        self.case.input_tokens += tin
        self.case.output_tokens += tout
        self.case.cache_read_tokens += cread
        self.case.cache_write_tokens += cwrite
        cost = pricing.cost_usd(self.name, getattr(usage, "model", "") or self.model, input_tokens=tin, output_tokens=tout,
                                cache_read_tokens=cread, cache_write_tokens=cwrite)
        override = getattr(usage, "cost_usd_override", None)
        self.case.cost_usd += float(override) if override is not None else cost.amount
        self.case.estimated = self.case.estimated or cost.estimated

    async def complete_tools(self, system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]], *, max_tokens: int = 4000) -> Any:
        from app.ai.providers import ProviderError
        started = time.monotonic()
        try:
            turn = await self.inner.complete_tools(system, messages, tools, max_tokens=max_tokens)
        except ProviderError as exc:
            self._count(getattr(exc, "usage", None) or SimpleNamespace())
            raise
        finally:
            self.case.seconds += time.monotonic() - started
        self._count(getattr(turn, "usage", None) or SimpleNamespace())
        return turn

    async def complete(self, system: str, user: str, *, max_tokens: int = 800) -> Any:
        raise AssertionError("the planner never asks for a wording call")


class DryRunProvider:
    """`--dry-run`: an offline fake model. Step 1 reads the market snapshot, step 2 answers with no figure. No network."""
    name, model, supports_tools = "dry_run", "offline-fake", True

    def __init__(self) -> None:
        self.prompt_version: str | None = None
        self._asked: set = set()

    async def complete_tools(self, system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]], *, max_tokens: int = 4000) -> Any:
        from app.ai.providers import Completion, ToolCall, ToolTurn
        question = str(messages[0]["content"]) if messages else ""
        if question not in self._asked and len(messages) == 1:
            self._asked.add(question)
            call = ToolCall("dry_1", "get_market_snapshot", {})
            return ToolTurn("", [call], [{"type": "tool_use", "id": call.id, "name": call.name, "input": {}}], Completion(""), "tool_use")
        text = json.dumps({"text": "Dry run: an offline fake model answered; no figure is shown.", "claims": []})
        return ToolTurn(text, [], [{"type": "text", "text": text}], Completion(text), "end_turn")


class _NoWriteSession:
    """The planner's database session for the eval: it accepts the audit rows and writes nothing."""

    def add(self, row: Any) -> None:
        return None

    async def flush(self) -> None:
        return None

    async def commit(self) -> None:
        return None

    async def rollback(self) -> None:
        return None


@dataclass
class CaseRun:
    id: str
    topic: str
    stopped: str
    source: str
    tools: list[str] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)


def _recorded_tool(run: Any, seen: list[str]):
    """`agent.run_tool` over the recorded results: the tool's own argument check, then the fixture (by alias)."""
    from app.ai.tools import ToolResult, registry

    async def run_tool(name: str, arguments: dict[str, Any], ctx: Any, *, guard_cleared: bool = False) -> ToolResult:
        tool = registry().get(name)
        if tool is None:
            return ToolResult(False, None, None, name, error=f"unknown tool {name!r}")
        if tool.kind == "proposal" and not guard_cleared:
            return ToolResult(False, None, None, name, error="a proposal tool runs only after the agent's guard has cleared the call")
        try:
            args = tool.input_model.model_validate(arguments or {})
        except Exception as exc:  # noqa: BLE001 - reported to the model like the real run_tool does
            return ToolResult(False, None, None, name, error=f"invalid arguments: {str(exc)[:200]}")
        if name.startswith("calc_"):
            # Code arithmetic (calc tools, the options payoff / what-if) runs for real: it reads no market data. A tool
            # that needs the trader's saved settings answers with its error here (no database in the eval).
            try:
                res = await tool.fn(ctx, args)
            except Exception as exc:  # noqa: BLE001 - reported to the model like the real run_tool does
                res = ToolResult(False, None, None, name, error=f"{type(exc).__name__}: not available in the eval")
            if res.ok:
                seen.append(name)
            return res
        fixture_name = ALIASES.get(name, name)
        res = await run(fixture_name, args.model_dump())
        if res.ok:
            seen.append(fixture_name)
            res.untrusted = fixture_name in UNTRUSTED_TOOLS
        return res
    return run_tool


async def _offered(ctx: Any, wanted: list[str]) -> list[str]:
    """Every tool the planner may use (the eval does not depend on an organisation's switches)."""
    return list(wanted)


async def _no_audit(*args: Any, **kwargs: Any) -> None:
    return None


def agent_limits_default() -> Any:
    from app.ai import agent
    return agent.AgentLimits()


async def _limits() -> Any:
    """The planner's limits: Admin values from the database when it is reachable, else the defaults."""
    from app.ai import agent
    try:
        from app.db.session import _session_factory
        async with _session_factory() as session:
            return await asyncio.wait_for(agent.admin_limits(session), timeout=10), "Admin values"
    except Exception:  # noqa: BLE001 - the eval still runs on the defaults, and says so
        return agent.AgentLimits(), "defaults (database not read)"


def planner_responder(provider: CountingProvider, limits: Any, runs: dict[str, CaseRun]) -> trader_eval.Responder:
    """Each question through the production planner (`agent.run_agent`) with recorded tools and no writes."""
    from app.ai import agent

    async def respond(case: dict[str, Any], run: Any) -> trader_eval.Candidate:
        from app.ai.tools import ToolContext
        seen: list[str] = []
        provider.case = Usage()
        ctx = ToolContext(_NoWriteSession(), 1, SimpleNamespace(id=1, tenant_id=1))      # type: ignore[arg-type]
        with mock.patch.object(agent, "run_tool", _recorded_tool(run, seen)), mock.patch.object(agent, "available", _offered), \
                mock.patch.object(agent, "write_audit_log", _no_audit):
            ans = await agent.run_agent(provider, ctx, case["question"], lang="en", limits=limits)
        runs[case["id"]] = CaseRun(case["id"], case.get("topic") or case.get("kind") or "safety", ans.stopped, ans.source, seen, provider.case)
        if ans.refused:
            return trader_eval.Candidate(text=ans.text, headline=ans.text.splitlines()[0] if ans.text else "", refused=ans.refused)
        computed = [json.dumps({"tool": c["name"], "arguments": c.get("arguments")}, default=str) for c in ans.tool_calls
                    if c["name"].startswith("calc_") and c.get("ok")]
        if ans.source != "ai":
            # The checks refused the AI's answer (or a limit / outage stopped it): the chat would answer from its rules,
            # so the planner itself scores as no answer here.
            return trader_eval.Candidate(text="", headline="", fallback=True, tools=seen)
        lines = [x for x in ans.text.splitlines() if x.strip()]
        head = ans.headline or (lines[0] if lines else "")
        shown = "\n".join([ans.headline, ans.text]) if ans.headline else ans.text
        chips = [c["label"] for c in ans.chips]
        return trader_eval.Candidate(text=shown, headline=head, followups=chips, tools=seen, computed_text="\n".join(computed))
    return respond


async def _org_provider(tenant: int | None, model: str | None) -> tuple[Any, str]:
    from sqlalchemy import select

    from app.ai.providers import build_provider
    from app.db.models import AiProviderConfigRecord
    from app.db.session import _session_factory
    from app.secrets_store.encryption import decrypt_text
    from app.secrets_store.envelope import PURPOSE_AI_PROVIDER_KEY
    async with _session_factory() as session:
        rows = list(await session.scalars(select(AiProviderConfigRecord).where(AiProviderConfigRecord.enabled.is_(True),
                                                                               AiProviderConfigRecord.provider != "rule_based",
                                                                               AiProviderConfigRecord.encrypted_api_key.is_not(None))))
    if tenant is not None:
        rows = [r for r in rows if int(r.tenant_id) == int(tenant)]
        if not rows:
            raise EvalSetupError(f"organisation {tenant} has no saved AI key (Settings > AI); nothing was sent")
    if not rows:
        raise EvalSetupError("no platform key on the host and no organisation has a saved AI key; nothing was sent")
    if len(rows) > 1:
        ids = ", ".join(str(r.tenant_id) for r in sorted(rows, key=lambda r: r.tenant_id))
        raise EvalSetupError(f"several organisations have a saved AI key (ids {ids}): run again with --tenant ID; nothing was sent")
    row = rows[0]
    key = decrypt_text(row.encrypted_api_key, PURPOSE_AI_PROVIDER_KEY, row.tenant_id)
    try:
        return build_provider(row.provider, key, model or row.model, task="copilot"), f"organisation {row.tenant_id}'s own key ({row.provider})"
    finally:
        key = ""


def _platform_provider(model: str | None) -> tuple[Any, str]:
    from app.ai import platform_key
    from app.ai.providers import build_provider
    name = platform_key.configured_provider()
    if name is None:
        raise EvalSetupError("the host has no platform AI key (AI_PLATFORM_PROVIDER + AI_PLATFORM_API_KEY); nothing was sent")
    key = platform_key._secret()
    try:
        return build_provider(name, key, model, task="copilot"), f"the platform key ({name})"
    finally:
        key = ""


async def pick_provider(key: str, tenant: int | None, model: str | None) -> tuple[Any, str]:
    from app.ai import platform_key
    if key == "platform" or (key == "auto" and platform_key.configured_provider() is not None):
        return _platform_provider(model)
    return await _org_provider(tenant, model)


def run_eval(provider: Any, limits: Any) -> dict[str, Any]:
    """The whole set through the planner: the trader eval's report plus per-case runs and costs."""
    counting = provider if isinstance(provider, CountingProvider) else CountingProvider(provider)
    from app.ai import agent
    from app.ai.prompt_versions import stamp
    stamp(counting, agent.PROMPT_VERSION)
    runs: dict[str, CaseRun] = {}
    report = trader_eval.run(planner_responder(counting, limits, runs))
    total = Usage()
    for r in runs.values():
        total.add(r.usage)
    by_topic: dict[str, Any] = {}
    for topic in sorted({r.topic for r in report["cases"]}):
        by_topic[topic] = trader_eval.group([r for r in report["cases"] if r.topic == topic])
    answered_by_model = [r for r in runs.values() if r.usage.calls]
    return {"report": report, "by_topic": by_topic, "runs": runs, "total": total, "model": f"{counting.name} / {counting.model}",
            "answers_with_a_model_call": len(answered_by_model)}


def _inr(usd: float) -> float:
    from app.ai import pricing
    return pricing.usd_to_inr(usd)


def render(out: dict[str, Any], source: str, limits_note: str) -> str:
    rep, total = out["report"], out["total"]
    lines = [f"# Copilot planner - live eval ({out['model']}, {source}; planner limits: {limits_note})", "",
             "## Trader eval (the 60 questions)", "", trader_eval.table(rep), "## Planner score per job", "",
             "| Job | n | Answered | Correct | Useful | Format | Score |", "|---|---|---|---|---|---|---|"]
    for topic, g in out["by_topic"].items():
        lines.append(f"| {topic.replace('_', ' ')} | {g['n']} | " + " | ".join(trader_eval._pct(g[d]) for d in trader_eval.DIMENSIONS)
                     + f" | **{trader_eval._pct(g['score'])}** |")
    s = rep["safety"]
    lines += ["", f"## Safety: {s['passed']} / {s['n']} pass ({trader_eval._pct(s['score'])})", ""]
    lines += [f"- {i['id']} ({i['kind']}): " + ("pass" if i["pass"] else "FAIL - " + "; ".join(i["problems"])) for i in s["items"]]
    lines += ["", "## Tokens and cost per answer", "", "| Id | Job | Ended | Model calls | Tokens in | Tokens out | Cost USD | Cost INR |",
              "|---|---|---|---|---|---|---|---|"]
    for r in out["runs"].values():
        u = r.usage
        lines.append(f"| {r.id} | {r.topic.replace('_', ' ')} | {r.stopped} | {u.calls} | {u.input_tokens} | {u.output_tokens} | "
                     f"{u.cost_usd:.4f} | {_inr(u.cost_usd):.2f} |")
    n = max(1, out["answers_with_a_model_call"])
    lines += ["", f"Total: {total.calls} model calls, {total.input_tokens} tokens in, {total.output_tokens} tokens out "
                  f"(cache read {total.cache_read_tokens}, cache write {total.cache_write_tokens}), "
                  f"{total.cost_usd:.4f} USD = {_inr(total.cost_usd):.2f} INR; per answer that reached the model: "
                  f"{total.cost_usd / n:.4f} USD = {_inr(total.cost_usd / n):.2f} INR; {total.seconds:.0f} s of model time."]
    if total.estimated:
        lines.append("The model is not in the price table: the cost is an estimate at the provider's highest price.")
    worst = trader_eval.worst_table(rep)
    return "\n".join(lines) + "\n\n## The 10 weakest answers\n\n" + worst


def as_json(out: dict[str, Any], source: str, limits_note: str) -> dict[str, Any]:
    return {"model": out["model"], "key": source, "limits": limits_note, "eval": trader_eval.as_json(out["report"]), "by_topic": out["by_topic"],
            "runs": [{**r.__dict__, "usage": r.usage.__dict__} for r in out["runs"].values()], "total": out["total"].__dict__,
            "total_inr": _inr(out["total"].cost_usd)}


def main(argv: Sequence[str] | None = None, *, provider: Any = None) -> int:
    """Exit codes: 0 the run finished (the scores are printed, whatever they are); 2 no key could be used (nothing sent)."""
    parser = argparse.ArgumentParser(prog="python -m evals.planner_live", description="One live run of the Copilot planner on the trader eval set.")
    parser.add_argument("--key", choices=("auto", "platform", "org"), default="auto")
    parser.add_argument("--tenant", type=int, default=None, help="the organisation whose saved key answers (with --key org / auto)")
    parser.add_argument("--model", default=None, help="another model of the same provider, for this run only")
    parser.add_argument("--dry-run", "--offline", dest="dry_run", action="store_true", help="an offline fake model: no network, no key, zero cost")
    parser.add_argument("--json", action="store_true", help="the full report as JSON")
    opts = parser.parse_args(list(argv) if argv is not None else None)
    limits, limits_note = asyncio.run(_limits())
    if provider is not None:
        source = "a provider passed in by the caller"
    elif opts.dry_run:
        provider, source = DryRunProvider(), "dry run (offline fake model, no key)"
    else:
        try:
            provider, source = asyncio.run(pick_provider(opts.key, opts.tenant, opts.model))
        except EvalSetupError as exc:
            print(f"Not run: {exc}", file=sys.stderr)
            return 2
    out = run_eval(provider, limits)
    print(json.dumps(as_json(out, source, limits_note), indent=1, ensure_ascii=True, default=str) if opts.json else render(out, source, limits_note))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
