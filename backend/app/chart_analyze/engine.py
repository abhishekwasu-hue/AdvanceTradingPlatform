"""Analyze a chart: the one vision call, on ATP's existing AI path (no new way of holding keys).

Which AI answers, in this order:
1. **The user's own key (BYOK)**, where ATP already allows it: the `vision_byok` flag is on for the organisation, the
   user switched Vision on and has a key for the chosen provider (`app.ai.byok`). The user's own daily / monthly BYOK
   caps apply (hard stop), the call is logged in `llm_calls` as billed to the user's key, and the key is opened only
   for this call and only for its owner (`byok.open_key`), exactly as the Vision audit does.
2. **The organisation's AI provider** (Settings > AI provider, `app.ai.settings.provider_for`): only on a plan with AI
   features, after the owner accepted the data-sharing consent, while the plan's monthly AI budget lasts; metered and
   logged like every other AI call (`MeteredProvider`). On top, this feature's own monthly cost cap per plan tier
   (`limits.check_cost`) applies before the call.
3. Neither: the analysis is not run, and the user reads why ("No AI is set up ...").
The model's reply goes back to the engine (the private package) to be parsed; nothing here reads the method.

Settings redesign A (flag `ai_one_key`, needs `ui_v2`): the order above becomes `app.ai.resolve.provider_for_user` - the
user's ONE connected key (no Vision switch needed), else the organisation's provider, else the refusal
`ai_not_connected`. A key whose free tier may train on what it is sent (Gemini, review R4 / SR-2) gets ATP's private
method instructions only after the user confirmed the key is on a paid plan (`ai_paid_plan_needed` until then).
"""
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import byok, pricing
from app.ai import providers as ai_providers
from app.ai.metering import log_call
from app.ai.providers import Completion, ProviderError
from app.chart_analyze.provider import Instructions
from app.db.models import Tenant, User

FEATURE = "chart_analyze"
NO_AI = ("No AI is set up for chart analysis: the organisation's AI provider is not available on this plan (or its "
         "budget is spent), and you have no own AI key switched on. Add your own key under Settings > Vision with your own AI key.")


class EngineUnavailable(RuntimeError):
    """No AI may answer (the message says why and what to do). `code` is the machine-readable reason."""

    def __init__(self, message: str, code: str = "no_ai") -> None:
        super().__init__(message)
        self.code = code


@dataclass
class Answer:
    text: Optional[str]
    error: Optional[str]
    provider: str
    model: str
    cost_usd: float
    billed_to_user_key: bool


def _estimate(provider: str, model: str, settings: Dict[str, Any]) -> float:
    return pricing.cost_usd(provider, model, input_tokens=int(settings["estimate_input_tokens"]),
                            output_tokens=int(settings["estimate_output_tokens"])).amount


async def _byok_choice(session: AsyncSession, user: User) -> Optional[tuple]:
    """(provider, model) when the user's own key may answer, else None."""
    prefs = await byok.get_prefs(session, user)
    keys = await byok.list_keys(session, user)
    provider = byok.active_provider(prefs, keys)
    if provider is None:
        return None
    allowed, reason = await byok.byok_allowed(session, user.id, provider)
    if not allowed and reason in (byok.REASON_VISION_OFF, byok.REASON_NO_KEY):
        return None
    model = byok.effective_model(next((k for k in keys if k.provider == provider), None), provider)
    return provider, model


async def _call_byok(session: AsyncSession, user: User, provider: str, model: str, instr: Instructions, png: bytes,
                     settings: Dict[str, Any], *, client: Optional[Any], now: datetime) -> Answer:
    from app.secrets_store.envelope import ensure_tenant_key
    blocked = await byok.cap_block(session, user, await byok.get_prefs(session, user), now=now, estimate_usd=_estimate(provider, model, settings))
    if blocked:
        raise EngineUnavailable(f"Your own AI key's {blocked}; it resumes when the {'day' if blocked == byok.REASON_DAILY_CAP else 'month'} "
                                "rolls over (UTC).")
    record = await byok.get_key(session, user, provider)
    if record is None:
        raise EngineUnavailable(byok.VISION_OFF_NO_KEY)
    key = ""
    result: Optional[Completion] = None
    text: Optional[str] = None
    error: Optional[str] = None
    called = False
    try:
        await ensure_tenant_key(session, user.tenant_id)
        key = byok.open_key(record, user=user)
        llm = ai_providers.build_byok_provider(provider, key, model, client=client)
        called = True
        result = await llm.complete_full(instr.system, instr.user, max_tokens=int(settings["max_output_tokens"]), image_png=png)
        text = result.text
    except byok.KeyBindingError as exc:
        error = byok.sanitize_error(str(exc), [key])
    except ProviderError as exc:
        error = byok.sanitize_error(str(exc), [key])
        result = exc.usage
    except Exception as exc:  # noqa: BLE001 - reported by class name only (its text could carry the key)
        error = byok.sanitize_error(f"The AI call failed: {exc.__class__.__name__}", [key])
    finally:
        key = ""
    cost = 0.0
    if result is not None and (result.input_tokens or result.output_tokens or result.cache_read_tokens):
        cost = pricing.cost_usd(provider, result.model or model, input_tokens=result.input_tokens, output_tokens=result.output_tokens,
                                cache_read_tokens=result.cache_read_tokens, cache_write_tokens=result.cache_write_tokens).amount
    if called:
        await log_call(session, tenant_id=user.tenant_id, user_id=user.id, feature=FEATURE, provider=provider,
                       model=(result.model if result is not None and result.model else model), prompt_version=instr.prompt_version,
                       system=instr.system, user=instr.user, response=text if error is None else None,
                       status="ok" if error is None else f"error: {error}", result=result, cost_usd=cost, billed_to_user_key=True)
        record.last_used_at = now
    return Answer(text=text, error=error, provider=provider, model=model, cost_usd=cost, billed_to_user_key=True)


async def ask(session: AsyncSession, user: User, tenant: Tenant, instr: Instructions, png: bytes, settings: Dict[str, Any], *,
              now: datetime, client: Optional[Any] = None) -> Answer:
    """One call with the image; raises EngineUnavailable (no AI may answer) or LimitReached (the cost cap)."""
    from app.ai.metering import MeteredProvider
    from app.ai.settings import provider_for
    from app.ai import resolve as ai_resolve
    from app.chart_analyze import limits
    if await ai_resolve.one_key_on(session, tenant.id):
        return await _ask_one_key(session, user, tenant, instr, png, settings, now=now, client=client)
    choice = await _byok_choice(session, user)
    if choice is not None:
        return await _call_byok(session, user, choice[0], choice[1], instr, png, settings, client=client, now=now)
    llm = await provider_for(session, tenant, client=client, task=FEATURE, user_id=user.id)
    if not isinstance(llm, MeteredProvider):
        raise EngineUnavailable(NO_AI)
    await limits.check_cost(session, user, tenant, settings, _estimate(llm.name, llm.model, settings), now)
    llm.prompt_version = instr.prompt_version
    try:
        result = await llm.complete_full(instr.system, instr.user, max_tokens=int(settings["max_output_tokens"]), image_png=png)
    except ProviderError as exc:
        return Answer(text=None, error=byok.sanitize_error(str(exc)), provider=llm.name, model=llm.model,
                      cost_usd=float(llm.spent.get("cost_usd", 0.0)), billed_to_user_key=False)
    return Answer(text=result.text, error=None, provider=llm.name, model=result.model or llm.model,
                  cost_usd=float(llm.spent.get("cost_usd", 0.0)), billed_to_user_key=False)


async def _ask_one_key(session: AsyncSession, user: User, tenant: Tenant, instr: Instructions, png: bytes, settings: Dict[str, Any], *,
                       now: datetime, client: Optional[Any]) -> Answer:
    """Settings redesign A: the one resolver (the user's own key, else the organisation's, else `ai_not_connected`)."""
    from app.ai import resolve as ai_resolve
    from app.ai.metering import MeteredProvider
    from app.chart_analyze import limits
    llm = await ai_resolve.provider_for_user(session, user, tenant, FEATURE, need_vision=True, client=client)
    if isinstance(llm, ai_resolve.UserKeyProvider):
        record = await byok.connected_key(session, user)
        if record is None or not await byok.paid_plan_confirmed(session, user, record):
            # R4 / SR-2: ATP's private method instructions never go to a key that may train on them.
            raise EngineUnavailable(ai_resolve.PAID_PLAN_MESSAGE, code=ai_resolve.PAID_PLAN_CODE)
        llm.prompt_version = instr.prompt_version
        llm.estimate_usd = _estimate(llm.name, llm.model, settings)
        try:
            result = await llm.complete_full(instr.system, instr.user, max_tokens=int(settings["max_output_tokens"]), image_png=png)
        except ai_resolve.UserKeyLimit as exc:
            raise EngineUnavailable(str(exc), code=ai_resolve.LIMIT_CODE) from None
        except ProviderError as exc:
            return Answer(text=None, error=byok.plain_error(exc.kind), provider=llm.name, model=llm.model,
                          cost_usd=float(llm.spent.get("cost_usd", 0.0)), billed_to_user_key=True)
        return Answer(text=result.text, error=None, provider=llm.name, model=result.model or llm.model,
                      cost_usd=float(llm.spent.get("cost_usd", 0.0)), billed_to_user_key=True)
    if not isinstance(llm, MeteredProvider):
        raise EngineUnavailable(ai_resolve.NOT_CONNECTED_MESSAGE, code=ai_resolve.NOT_CONNECTED_CODE)
    await limits.check_cost(session, user, tenant, settings, _estimate(llm.name, llm.model, settings), now)
    llm.prompt_version = instr.prompt_version
    try:
        result = await llm.complete_full(instr.system, instr.user, max_tokens=int(settings["max_output_tokens"]), image_png=png)
    except ProviderError as exc:
        return Answer(text=None, error=byok.sanitize_error(str(exc)), provider=llm.name, model=llm.model,
                      cost_usd=float(llm.spent.get("cost_usd", 0.0)), billed_to_user_key=False)
    return Answer(text=result.text, error=None, provider=llm.name, model=result.model or llm.model,
                  cost_usd=float(llm.spent.get("cost_usd", 0.0)), billed_to_user_key=False)
