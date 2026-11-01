"""Item 5 (owner's handover), first slice: the chat-first Copilot page's two routes, behind `ui_v2` (404 while off, like
the other v2 pages' routes) and the `ai_copilot` kill switch (503 while off).

* `GET  /api/ai/chat/home` - the page's top: the plain top line, whether an AI key is set ("AI is off — answers are from rules"
  when not), "Today's market in one line" (or the plain reason it is empty), the eight jobs ("What I can do") and the
  starter questions. Rule text only; no model is called.
* `GET / DELETE /api/ai/chat/history` - CP-UX CU6: the person's own conversation, kept across reloads (the newest
  `COPILOT_CHAT_KEEP` turns); DELETE is Settings → My data's "Delete my Copilot conversation".
* `POST /api/ai/chat` - one question, answered by the job it routes to (app/ai/copilot_chat.py): read tools only,
  "How I worked this out", "I don't know" for missing data, the one-line disclaimer. With an AI provider the model may
  re-word the facts (numbers / tickers / direction / advice checked, else the rules stay). AI-acknowledgement gated
  (428 until the user accepted the Copilot terms) and rate limited like every `/api/ai/*` route (`POST chat` weighs 4).

Nothing here writes a trade, an order or a proposal (ADR-0006). The indicator regime and the raw tool errors appear only
in `diagnostics`, for a SUPER_ADMIN (handover 5 C).
"""
from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import ask_context, chat_history, platform_key
from app.ai import copilot_chat as chat
from app.ai import resolve as ai_resolve
from app.ai.ask_context import ChatContext
from app.ai.compliance_terms import require_ai_acknowledged
from app.ai.rate_limit import ai_rate_limit
from app.ai.tools import ToolContext, ToolResult, run_tool
from app.auth.dependencies import get_current_user
from app.core.enums import UserRole
from app.db.models import Tenant, User
from app.db.session import get_session
from app.platform.controls import flag_enabled, require_flag

router = APIRouter(prefix="/api/ai", tags=["ai"], dependencies=[Depends(ai_rate_limit)])
PAGE_FLAG = "ui_v2"


class ChatBody(BaseModel):
    message: str = Field(min_length=2, max_length=1000)
    # M59 CR-2: false = the rule answer only, no AI wording (no metered AI call) - the Copilot's chart attach asks this
    # way after an analysis; the AI words come only when the user taps "Explain in words" (a normal question).
    wording: bool = True
    # M72 PR-3 (CP-3): where the question was asked from - page, symbol, timeframe, one object by kind + id. The server
    # re-reads the object for this user (404 when it is not his); no number about it comes from the client.
    context: ChatContext | None = None


async def _flags(session: AsyncSession, user: User) -> None:
    if not await flag_enabled(session, PAGE_FLAG, user.tenant_id):
        raise HTTPException(status_code=404, detail="Not found", headers={"X-Feature-Disabled": PAGE_FLAG})
    await require_flag(session, "ai_copilot", user.tenant_id)


def _runner(session: AsyncSession, tenant_id: int, user_id: int, question: str = "") -> chat.Runner:
    """Each tool gets a freshly loaded user: a tool may close the session (the chart layers release it before a build),
    commit (the chain read meters its usage) or roll back (a failed context tile), and the next tool must never touch an
    expired or detached user.

    A proposal tool (job 9's alert) runs only after the agent's own injection guard (`proposals.guard`) cleared it on the
    trader's question: his own words must ask for it, at most one proposal per question, and the quoted words must not
    come from third-party text read in this request. A proposal is PROPOSED only - the trader approves it (ADR-0006)."""
    from app.ai.tools import proposals, registry
    untrusted: list = []
    made = [0]

    async def run(name: str, args: Dict[str, Any]) -> ToolResult:
        user = await session.get(User, user_id, populate_existing=True)
        if user is None:
            return ToolResult(False, None, None, name, error="the user could not be read")
        tool = registry().get(name)
        cleared = False
        if tool is not None and tool.kind == "proposal":
            why = proposals.guard(name, args, question, proposals.allowed_for(question), untrusted, made[0], 1)
            if why:
                return ToolResult(False, None, None, name, error=f"the proposal was not made: {why}")
            cleared = True
        result = await run_tool(name, args, ToolContext(session, tenant_id, user), guard_cleared=cleared)
        if cleared and result.ok:
            made[0] += 1
        if result.untrusted:
            untrusted.append(result.text())
        untrusted.extend(result.third_party)
        return result
    return run


async def chat_flags(session: AsyncSession, tenant_id: int) -> Dict[str, bool]:
    """The chat's own flags (news and market memory: `ai_agent`; screens: `screener_copilot`; the full view: `ai_multiview`)."""
    return {name: await flag_enabled(session, name, tenant_id) for name in chat.CHAT_FLAGS}


async def _provider(session: AsyncSession, tenant_id: int, user_id: int) -> Any:
    tenant = await session.get(Tenant, tenant_id)
    if tenant is None:
        raise HTTPException(status_code=404, detail="Organisation not found")
    # Settings redesign A: the user's own key first when `ai_one_key` is on (app/ai/resolve.py); off, exactly today's.
    user = await session.get(User, user_id, populate_existing=True)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    try:
        return await ai_resolve.provider_for_user(session, user, tenant, "copilot")
    except HTTPException as exc:
        # The user's own key is at its own limit: a plain hard stop for the AI wording, never the organisation's key
        # (SR-4). The page still answers from ATP's rules and says why.
        if isinstance(exc.detail, dict) and exc.detail.get("code") == ai_resolve.LIMIT_CODE:
            return _AtLimit(str(exc.detail.get("message") or ""))
        raise


class _AtLimit:
    """No provider for this request: the user's own key reached its limit."""
    name = "rule_based"

    def __init__(self, message: str) -> None:
        self.reason = message


def _ai_state(provider: Any, one_key: bool = False) -> Dict[str, Any]:
    if isinstance(provider, _AtLimit):
        return {"ready": False, "title": "AI limit reached", "line": provider.reason, "settings": chat.KEY_SETTINGS, "reason": provider.reason,
                "code": ai_resolve.LIMIT_CODE, "own_key": True}
    platform_reason = getattr(provider, "platform_reason", "")
    if platform_reason in platform_key.LIMIT_REASONS:
        # M72 PR-1b: a platform cap reached is a hard stop for the AI; the rules answer and the page says why.
        return {"ready": False, "title": "AI limit reached", "line": provider.reason, "settings": chat.KEY_SETTINGS, "reason": provider.reason,
                "code": ai_resolve.LIMIT_CODE, "own_key": False, "platform": True}
    ready = getattr(provider, "name", "rule_based") != "rule_based"
    out = {"ready": ready, "title": None if ready else chat.NO_KEY_TITLE, "line": None if ready else chat.NO_KEY_LINE,
           "settings": chat.KEY_SETTINGS, "reason": None if ready else (getattr(provider, "reason", "") or None)}
    if one_key:
        # The Connect AI panel reads its own status; the page only needs to know the key is the user's own.
        out.update(code=None if ready else ai_resolve.NOT_CONNECTED_CODE, own_key=ai_resolve.is_user_key(provider), reason=None)
    return out




@router.get("/chat/home")
async def chat_home(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    await _flags(session, user)
    tenant_id, user_id = int(user.tenant_id), int(user.id)         # plain values: the tools may expire the ORM user
    provider = await _provider(session, tenant_id, user_id)
    one_key = await ai_resolve.one_key_on(session, tenant_id)
    line = await chat.market_line(_runner(session, tenant_id, user_id))
    jobs = chat.visible_jobs(await chat_flags(session, tenant_id))      # CU7: a job behind an OFF switch is not listed
    return {"top_line": chat.TOP_LINE, "ai": _ai_state(provider, one_key), "market_line": line, "disclaimer": chat.DISCLAIMER,
            "jobs": [{"id": j.id, "number": j.number, "title": j.title, "line": j.line, "starter": j.starter, "built": j.built,
                      "page": {"label": j.page[0], "to": j.page[1]} if j.page else None} for j in jobs],
            "starters": [j.starter for j in jobs], "keep": chat_history.keep()}


AGENT_FLAG = "copilot_agent"
AGENT_FALLBACK_NOTE = "The AI could not answer this one, so this answer uses ATP's own sentences."


async def _agent_answer(session: AsyncSession, provider: Any, tenant_id: int, user_id: int, question: str,
                        context: dict[str, Any] | None = None) -> tuple[dict[str, Any] | None, str]:
    """M72 PR-1c (CP-1, F22): the planner first. (the answer in the chat's shape, "") when the AI answered and its answer
    passed the checks; (None, why) otherwise - the caller then answers from ATP's rules. Never an order (ADR-0006)."""
    from app.ai import agent, grounding
    from app.ai.prompt_versions import stamp
    user = await session.get(User, user_id, populate_existing=True)
    if user is None:
        return None, "the user could not be read"
    stamp(provider, agent.PROMPT_VERSION)
    try:
        ans = await agent.run_agent(provider, ToolContext(session, tenant_id, user), question, limits=await agent.admin_limits(session),
                                    context=context)
    except Exception as exc:  # noqa: BLE001 - the planner never breaks the chat: the rules answer instead
        await session.rollback()
        return None, f"planner error ({exc.__class__.__name__})"
    if ans.source != "ai":
        return None, ans.stopped
    worked = [chat.Step(c["name"], chat.TOOL_NAMES.get(c["name"], "Data"), bool(c.get("ok")), c.get("as_of"), None, [],
                        None if c.get("ok") else chat.plain_reason(c.get("error"), c["name"]), dict(c.get("arguments") or {}))
              for c in ans.tool_calls]
    # M72 PR-2 (CP-2, F7): the checked headline first, then the short lines; the cards are tables code drew from the tool
    # results the model named (never a model-written table); the chips are fixed follow-up actions.
    lines = [x.strip() for x in ans.text.splitlines() if x.strip()]
    summary = [ans.headline, *lines] if ans.headline else lines or [ans.text]
    shape = chat.Answer(job="agent", title="Answer", summary=summary, worked=worked, notes=list(ans.disclaimers),
                        blocks=[{k: v for k, v in c.items() if k != "call"} for c in ans.cards]).as_dict()
    shape.update(source="ai", answer=ans.text, claims=ans.claims, disclaimers=ans.disclaimers, proposals=ans.proposals, chips=ans.chips,
                 headline_line=ans.headline or None, headline=ans.chart_headline, structure_card=ans.structure_card,
                 numbers=ans.numbers or grounding.provenance(ans.text, set(), grounding.allowed_from_text(question)),
                 agent={"run_id": ans.run_id, "stopped": ans.stopped, "contract": ans.contract})
    return shape, ""


@router.post("/chat")
async def chat_ask(body: ChatBody, user: User = Depends(require_ai_acknowledged), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    await _flags(session, user)
    tenant_id, user_id, admin = int(user.tenant_id), int(user.id), user.role == UserRole.SUPER_ADMIN.value   # read before any tool runs
    context = await ask_context.resolve(session, tenant_id, user_id, body.context)      # 404: an object that is not his
    token = chat.CONTEXT_SYMBOL.set(ask_context.symbol_of(context))
    try:
        result = await _chat_ask(body, session, tenant_id, user_id, admin, context)
    finally:
        chat.CONTEXT_SYMBOL.reset(token)
    if context:
        result["context"] = {"symbol": context.get("symbol"), "kind": (context.get("object") or {}).get("kind")}
    return result


async def _chat_ask(body: ChatBody, session: AsyncSession, tenant_id: int, user_id: int, admin: bool, context: dict[str, Any] | None) -> dict[str, Any]:
    provider = await _provider(session, tenant_id, user_id)
    ai_state = _ai_state(provider, await ai_resolve.one_key_on(session, tenant_id))
    agent_tried, agent_why = False, ""
    if (body.wording and ai_state["ready"] and getattr(provider, "supports_tools", False) and chat.refusal_kind(body.message) is None
            and await flag_enabled(session, AGENT_FLAG, tenant_id)):
        # M72 PR-1c: the AI planner first; ATP's rules only when the AI is down or its answer fails the checks (F22).
        agent_tried = True
        planned, agent_why = await _agent_answer(session, provider, tenant_id, user_id, body.message, context)
        if planned is not None:
            from app.ai import metering
            run_info = planned.pop("agent")                  # the run's audit id: admin diagnostics only, never the user's view
            await ai_resolve.mark_used(session, tenant_id, provider)
            planned["ai"], planned["usage"] = ai_state, metering.spent_by(provider)
            await chat_history.remember(session, tenant_id, user_id, body.message, planned)
            await session.commit()
            if admin:
                planned["diagnostics"] = {"agent": run_info}
            return planned
        await ai_resolve.mark_used(session, tenant_id, provider, error=f"AI planner not used ({agent_why}); answered from the rules")
        await session.commit()                       # the metered calls and the run's audit rows, used or not
    run = _runner(session, tenant_id, user_id, body.message)
    from app.signals.language import FLAG as SIGNAL_LANGUAGE_FLAG
    out = await chat.answer(body.message, run, signal_v2=await flag_enabled(session, SIGNAL_LANGUAGE_FLAG, tenant_id),
                            flags=await chat_flags(session, tenant_id))
    result = out.as_dict()
    result["ai"] = ai_state
    if agent_tried:
        # The planner was asked and did not answer: no second model call for the wording - the rules answer as they are.
        result["note"] = AGENT_FALLBACK_NOTE
        out.diagnostics["agent_not_used"] = agent_why
        from app.ai import metering
        result["usage"] = metering.spent_by(provider)
    elif out.job in chat.BUILT and result["ai"]["ready"] and body.wording:
        text, why = await chat.narrate(provider, out, body.message)
        await ai_resolve.mark_used(session, tenant_id, provider, error=None if text else f"AI answer not used ({why}); answered from the rules")
        await session.commit()                       # P0.8-C: the metered call, used or not
        if text:
            from app.ai import grounding
            facts = "\n".join(out.lines())
            named = chat.symbol_line(out, text)          # CU1: the AI wording names the symbol it read, or the line is added
            result.update(summary=[text, *([named] if named else [])], answer=text, source="ai",
                          numbers=grounding.provenance(text, grounding.allowed_from_text(facts), grounding.allowed_from_text(body.message)))
        else:
            result["note"] = "The AI wording did not pass ATP's checks, so this answer uses ATP's own sentences."
            out.diagnostics["ai_not_used"] = why
        from app.ai import metering
        result["usage"] = metering.spent_by(provider)
    # CU6: the turn is kept for the person (the newest COPILOT_CHAT_KEEP), so the conversation is there after a reload.
    await chat_history.remember(session, tenant_id, user_id, body.message, result)
    await session.commit()
    if admin:
        result["diagnostics"] = {**out.diagnostics, **(await _regime_diagnostic(run, out))}
    return result


@router.get("/chat/history")
async def chat_history_read(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    """CU6: the person's own conversation, oldest turn first (at most `keep` turns)."""
    await _flags(session, user)
    tenant_id, user_id = int(user.tenant_id), int(user.id)
    return {"turns": await chat_history.recent(session, tenant_id, user_id), "keep": chat_history.keep()}


@router.delete("/chat/history")
async def chat_history_delete(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    """CU6: Settings → My data, "Delete my Copilot conversation". Only the caller's own turns; works whatever the flags
    are (deleting one's own data is never switched off)."""
    deleted = await chat_history.forget(session, int(user.tenant_id), int(user.id))
    await session.commit()
    return {"deleted": deleted}


async def _regime_diagnostic(run: chat.Runner, out: chat.Answer) -> Dict[str, Any]:
    """Handover 5 C: the indicator regime (ADX / EMA / ATR) for the admin only, on the chart reader's symbol."""
    if out.job != "chart":
        return {}
    symbol = next((s.args.get("symbol") for s in out.worked if s.tool == "get_chart_structure"), None) or chat.DEFAULT_SYMBOL
    res = await run("get_market_regime", {"symbol": symbol, "timeframe": "day"})
    return {"indicator_regime": res.data if res.ok else {"error": res.error}}


__all__ = ["router"]
