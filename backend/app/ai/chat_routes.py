"""Item 5 (owner's handover), first slice: the chat-first Copilot page's two routes, behind `ui_v2` (404 while off, like
the other v2 pages' routes) and the `ai_copilot` kill switch (503 while off).

* `GET  /api/ai/chat/home` - the page's top: the plain top line, whether an AI key is set ("Add a free AI key to start"
  when not), "Today's market in one line" (or the plain reason it is empty), the eight jobs ("What I can do") and the
  starter questions. Rule text only; no model is called.
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

from app.ai import copilot_chat as chat
from app.ai import settings as ai_settings
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


async def _flags(session: AsyncSession, user: User) -> None:
    if not await flag_enabled(session, PAGE_FLAG, user.tenant_id):
        raise HTTPException(status_code=404, detail="Not found", headers={"X-Feature-Disabled": PAGE_FLAG})
    await require_flag(session, "ai_copilot", user.tenant_id)


def _runner(session: AsyncSession, tenant_id: int, user_id: int) -> chat.Runner:
    """Each tool gets a freshly loaded user: a tool may close the session (the chart layers release it before a build),
    commit (the chain read meters its usage) or roll back (a failed context tile), and the next tool must never touch an
    expired or detached user."""
    async def run(name: str, args: Dict[str, Any]) -> ToolResult:
        user = await session.get(User, user_id, populate_existing=True)
        if user is None:
            return ToolResult(False, None, None, name, error="the user could not be read")
        return await run_tool(name, args, ToolContext(session, tenant_id, user))
    return run


async def _provider(session: AsyncSession, tenant_id: int, user_id: int) -> Any:
    tenant = await session.get(Tenant, tenant_id)
    if tenant is None:
        raise HTTPException(status_code=404, detail="Organisation not found")
    return await ai_settings.provider_for(session, tenant, task="copilot", user_id=user_id)


def _ai_state(provider: Any) -> Dict[str, Any]:
    ready = getattr(provider, "name", "rule_based") != "rule_based"
    return {"ready": ready, "title": None if ready else chat.NO_KEY_TITLE, "line": None if ready else chat.NO_KEY_LINE,
            "settings": chat.KEY_SETTINGS, "reason": None if ready else (getattr(provider, "reason", "") or None)}




@router.get("/chat/home")
async def chat_home(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    await _flags(session, user)
    tenant_id, user_id = int(user.tenant_id), int(user.id)         # plain values: the tools may expire the ORM user
    provider = await _provider(session, tenant_id, user_id)
    line = await chat.market_line(_runner(session, tenant_id, user_id))
    return {"top_line": chat.TOP_LINE, "ai": _ai_state(provider), "market_line": line, "disclaimer": chat.DISCLAIMER,
            "jobs": [{"id": j.id, "number": j.number, "title": j.title, "line": j.line, "starter": j.starter, "built": j.built,
                      "page": {"label": j.page[0], "to": j.page[1]} if j.page else None} for j in chat.JOBS],
            "starters": [j.starter for j in chat.JOBS]}


@router.post("/chat")
async def chat_ask(body: ChatBody, user: User = Depends(require_ai_acknowledged), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    await _flags(session, user)
    tenant_id, user_id, admin = int(user.tenant_id), int(user.id), user.role == UserRole.SUPER_ADMIN.value   # read before any tool runs
    run = _runner(session, tenant_id, user_id)
    out = await chat.answer(body.message, run)
    result = out.as_dict()
    provider = await _provider(session, tenant_id, user_id)
    result["ai"] = _ai_state(provider)
    if out.job in chat.BUILT and result["ai"]["ready"]:
        text, why = await chat.narrate(provider, out, body.message)
        await ai_settings.mark_used(session, tenant_id, error=None if text else f"AI answer not used ({why}); answered from the rules")
        await session.commit()                       # P0.8-C: the metered call, used or not
        if text:
            from app.ai import grounding
            facts = "\n".join(out.lines())
            result.update(summary=[text], answer=text, source="ai",
                          numbers=grounding.provenance(text, grounding.allowed_from_text(facts), grounding.allowed_from_text(body.message)))
        else:
            result["note"] = "The AI wording did not pass ATP's checks, so this answer uses ATP's own sentences."
            out.diagnostics["ai_not_used"] = why
        from app.ai import metering
        result["usage"] = metering.spent_by(provider)
    if admin:
        result["diagnostics"] = {**out.diagnostics, **(await _regime_diagnostic(run, out))}
    return result


async def _regime_diagnostic(run: chat.Runner, out: chat.Answer) -> Dict[str, Any]:
    """Handover 5 C: the indicator regime (ADX / EMA / ATR) for the admin only, on the chart reader's symbol."""
    if out.job != "chart":
        return {}
    symbol = next((s.args.get("symbol") for s in out.worked if s.tool == "get_chart_structure"), None) or chat.DEFAULT_SYMBOL
    res = await run("get_market_regime", {"symbol": symbol, "timeframe": "day"})
    return {"indicator_regime": res.data if res.ok else {"error": res.error}}


__all__ = ["router"]
