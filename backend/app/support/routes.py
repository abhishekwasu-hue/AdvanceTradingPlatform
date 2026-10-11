"""Z10-f: `POST /api/ai/support/ask` - the support bot.

* Flag `support_bot` (OFF by default): while it is off for the caller's organisation the route answers 404, like a route
  that does not exist (the frontend hides the help button from `/api/system/features`).
* Signed-in users only. Rate-limited twice: the AI endpoints' per-user and per-organisation units (`ai_rate_limit`,
  the router dependency, as on every `/api/ai/*` route) and the bot's own per-user window (`support_rate_limit`,
  `SUPPORT_BOT_RATE_PER_MINUTE`, default 20).
* BYOK rule, the same as the Copilot's: the only model the bot may use is the caller's organisation's own configured
  provider (`ai.settings.provider_for`: the organisation's key, its data-sharing consent, its plan and AI budget), and
  only once the caller accepted the AI Copilot acknowledgement and the `ai_copilot` flag is on. There is no platform
  key, and one user's or organisation's key is never used for another. Without one, the bot answers from the help text.
* No tools: the route reads no positions, orders or account data and changes nothing (no commit unless the provider
  ran, and then only its usage row).
"""
import os

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app import legal
from app.ai import compliance_terms as terms
from app.ai import settings as ai_settings
from app.ai.rate_limit import ai_rate_limit
from app.auth.dependencies import get_current_user
from app.core.rate_limit import user_rate_limit
from app.db.models import Tenant, User
from app.db.session import get_session
from app.platform.controls import flag_enabled
from app.support import FLAG, bot


def _rate() -> int:
    try:
        return max(1, int(os.environ.get("SUPPORT_BOT_RATE_PER_MINUTE", "20")))
    except ValueError:
        return 20


router = APIRouter(prefix="/api/ai/support", tags=["support"], dependencies=[Depends(ai_rate_limit)])
# Module-level so tests can target it through `app.dependency_overrides`.
support_rate_limit = user_rate_limit("support_bot", limit=_rate(), window_seconds=60)


async def support_user(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> User:
    """The caller, when the operator has turned `support_bot` on for their organisation; 404 otherwise."""
    if not await flag_enabled(session, FLAG, user.tenant_id):
        raise HTTPException(status_code=404, detail="Not found", headers={"X-Feature-Disabled": FLAG})
    return user


class AskBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str = Field(min_length=2, max_length=bot.MAX_QUESTION_CHARS)


async def organisation_provider(session: AsyncSession, user: User):
    """The organisation's own external provider for this user, or None (answer from the help text)."""
    if not await flag_enabled(session, "ai_copilot", user.tenant_id):
        return None
    if await terms.latest(session, terms.KIND_COPILOT, tenant_id=user.tenant_id, user_id=user.id) is None:
        return None
    tenant = await session.get(Tenant, user.tenant_id)
    if tenant is None:
        return None
    provider = await ai_settings.provider_for(session, tenant, task="knowledge", user_id=user.id)
    return None if getattr(provider, "name", "rule_based") == "rule_based" else provider


@router.post("/ask", dependencies=[Depends(support_rate_limit)])
async def ask(body: AskBody, user: User = Depends(support_user), session: AsyncSession = Depends(get_session)) -> dict:
    provider = await organisation_provider(session, user)
    out = await bot.reply(body.question, provider=provider, include_legal=await legal.enabled(session, user.tenant_id))
    if provider is not None and (out.get("source") == "ai" or out.get("note")):      # the provider was called
        await ai_settings.mark_used(session, user.tenant_id, error=None if out.get("source") == "ai" else out.get("note"))
        await session.commit()
    return out
