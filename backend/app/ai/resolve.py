"""Settings redesign part A (review R2 / R3 / A5): ONE AI key for every user-started AI feature.

`provider_for_user(session, user, tenant, task)` is what the user-started AI calls ask for (the Copilot, the AI routes,
the strategy generator, screener NL, the scanner plan, option-chain Ask, the journal review, the help desk and chart
analysis). With the `ai_one_key` flag OFF (or `ui_v2` off) it returns exactly what `app.ai.settings.provider_for`
returns today. With it ON the order is:

1. **The user's own key** (`user_ai_keys`, the one shown as "Connected" in the Connect AI panel), once the user accepted
   its consent line (`byok_consent`). It runs through `UserKeyProvider`: the user's own daily / monthly caps are checked
   before every call (a plain hard stop - never a silent fallback to the organisation's key), the call is logged in
   `llm_calls` with `billed_to_user_key = true`, no plan usage row is written and the organisation's provider row is not
   touched. The model follows the task's tier (the user never picks one). The plan's AI gate and the organisation's
   data-sharing consent do not apply: the user pays and accepted their own consent (SR-3).
2. **The organisation's own key** (`ai_provider_configs`, `app.ai.settings.provider_for`), unchanged.
3. **The rule-based answer** - and where a feature cannot answer from rules, the one refusal `detail.code =
   "ai_not_connected"` (`not_connected()`), on which the frontend opens the Connect AI panel.

Background jobs (news classification, research jobs) never come here: they stay on the organisation's key. The AI never
places, blocks or changes an order (ADR-0006); a provider gets text (and a chart image) in and text out.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import byok, pricing
from app.ai import providers as ai_providers
from app.ai.metering import log_call
from app.ai.providers import Completion, ProviderError, RuleBasedProvider, ToolTurn
from app.db.models import Tenant, User

logger = logging.getLogger(__name__)

FLAG = "ai_one_key"
PAGE_FLAG = "ui_v2"
NOT_CONNECTED_CODE = "ai_not_connected"
NOT_CONNECTED_MESSAGE = "Connect AI to use this. Paste one key and every AI feature works."
LIMIT_CODE = "ai_limit_reached"
LIMIT_MESSAGES = {"day": "You reached your AI limit for today. It starts again tomorrow, or raise the limit under Connect AI.",
                  "month": "You reached your AI limit for this month. It starts again on the 1st, or raise the limit under Connect AI."}
PAID_PLAN_CODE = "ai_paid_plan_needed"
PAID_PLAN_MESSAGE = ("Chart analysis needs a key on a paid plan, because free keys from this provider may be used for training. "
                     "Confirm your key is on a paid plan under Connect AI.")


async def one_key_on(session: AsyncSession, tenant_id: int) -> bool:
    """The `ai_one_key` flag, which needs `ui_v2` too (SR-1)."""
    from app.platform.controls import flag_enabled
    return await flag_enabled(session, PAGE_FLAG, tenant_id) and await flag_enabled(session, FLAG, tenant_id)


def not_connected_detail() -> Dict[str, str]:
    return {"code": NOT_CONNECTED_CODE, "message": NOT_CONNECTED_MESSAGE}


def not_connected() -> HTTPException:
    """The one machine-readable refusal at a user-started AI call that cannot answer without an AI key (review A1)."""
    return HTTPException(status_code=409, detail=not_connected_detail())


def limit_reached(reason: str) -> HTTPException:
    period = "day" if reason == byok.REASON_DAILY_CAP else "month"
    return HTTPException(status_code=409, detail={"code": LIMIT_CODE, "period": period, "message": LIMIT_MESSAGES[period]})


class UserKeyLimit(ProviderError):
    """The user's own cap was reached between two calls of one request: the call is not made."""

    def __init__(self, reason: str) -> None:
        super().__init__(LIMIT_MESSAGES["day" if reason == byok.REASON_DAILY_CAP else "month"], kind="rate")
        self.reason = reason


@dataclass
class UserKeyProvider:
    """A user's own key, wrapped like `MeteredProvider` (same `complete` / `complete_full` / `complete_tools`, `spent`
    for the Copilot's cost chip, `prompt_version` for the log) but billed to the user's key: the cap is checked before
    every call, each call lands in `llm_calls` with `billed_to_user_key = true`, and no plan usage row is written."""

    inner: Any
    session: AsyncSession
    user: "_Who"
    feature: str
    record_id: int
    prompt_version: Optional[str] = None
    estimate_usd: float = 0.0
    billed_to_user_key: bool = True
    reason: str = ""
    spent: Dict[str, float] = field(default_factory=lambda: {"calls": 0, "tokens_input": 0, "tokens_output": 0, "cost_usd": 0.0})

    @property
    def name(self) -> str:
        return str(self.inner.name)

    @property
    def model(self) -> str:
        return str(self.inner.model)

    @property
    def supports_tools(self) -> bool:
        return hasattr(self.inner, "complete_tools")

    async def _check_cap(self) -> None:
        who: Any = self.user
        blocked = await byok.cap_block(self.session, who, await byok.get_prefs(self.session, who), estimate_usd=self.estimate_usd)
        if blocked:
            raise UserKeyLimit(blocked)

    async def _record(self, result: Optional[Completion], system: str, user_text: str, response: Optional[str], status: str) -> float:
        cost = 0.0
        if result is not None and (result.input_tokens or result.output_tokens or result.cache_read_tokens):
            cost = pricing.cost_usd(self.name, result.model or self.model, input_tokens=result.input_tokens, output_tokens=result.output_tokens,
                                    cache_read_tokens=result.cache_read_tokens, cache_write_tokens=result.cache_write_tokens).amount
            self.spent["calls"] += 1
            self.spent["tokens_input"] += result.input_tokens + result.cache_read_tokens + result.cache_write_tokens
            self.spent["tokens_output"] += result.output_tokens
            self.spent["cost_usd"] += cost
        try:
            await log_call(self.session, tenant_id=self.user.tenant_id, user_id=self.user.id, feature=self.feature, provider=self.name,
                           model=(result.model if result is not None and result.model else self.model),
                           prompt_version=self.prompt_version or f"unversioned:{self.feature}", system=system, user=user_text,
                           response=response, status=status, result=result, cost_usd=cost, billed_to_user_key=True)
            record = await self.session.get(byok.UserAiKeyRecord, self.record_id)
            if record is not None:
                record.last_used_at = datetime.now(timezone.utc)
        except Exception as exc:  # noqa: BLE001 - the log row is never the cause of a failed answer
            logger.warning("Own-key call not logged for user %s (%s): %s", self.user.id, self.feature, exc.__class__.__name__)
        return cost

    async def complete_full(self, system: str, user: str, *, max_tokens: int = 2000, image_png: Optional[bytes] = None) -> Completion:
        await self._check_cap()
        extra: Dict[str, Any] = {"image_png": image_png} if image_png is not None else {}
        try:
            result = await self.inner.complete_full(system, user, max_tokens=max_tokens, **extra)
        except ProviderError as exc:
            await self._record(exc.usage, system, user, None, f"error: {byok.sanitize_error(str(exc))[:280]}")
            raise
        except asyncio.CancelledError:
            try:
                await self._record(None, system, user, None, "cancelled")
            except BaseException:  # noqa: BLE001 - never swallow the cancellation
                pass
            raise
        await self._record(result, system, user, result.text, "ok")
        return result

    async def complete(self, system: str, user: str, *, max_tokens: int = 2000) -> str:
        return (await self.complete_full(system, user, max_tokens=max_tokens)).text

    async def complete_tools(self, system: str, messages: List[Dict[str, Any]], tools: List[Dict[str, Any]], *,
                             max_tokens: int = 4000) -> ToolTurn:
        import json as _json
        step = getattr(self.inner, "complete_tools", None)
        if step is None:
            raise ProviderError(f"{self.name} has no tool use")
        await self._check_cap()
        last = _json.dumps(messages[-1].get("content") if messages else "", default=str)[:20000]
        try:
            turn = await step(system, messages, tools, max_tokens=max_tokens)
        except ProviderError as exc:
            await self._record(exc.usage, system, last, None, f"error: {byok.sanitize_error(str(exc))[:280]}")
            raise
        calls = ", ".join(c.name for c in turn.calls)
        await self._record(turn.usage, system, last, turn.text + (f"\n[tool calls: {calls}]" if calls else ""), "ok")
        return turn


@dataclass(frozen=True)
class _Who:
    """The caller as two plain ids: a Copilot tool may expire the ORM user between two calls of one request."""
    id: int
    tenant_id: int


async def user_key_provider(session: AsyncSession, user: User, task: str, *, need_vision: bool = False,
                            client: Optional[Any] = None) -> Optional[UserKeyProvider]:
    """Tier 1: the user's own connected key (consent accepted), or None. Raises the plain hard stop at the cap."""
    from app.auth import view_as
    from app.secrets_store.envelope import ensure_tenant_key
    if view_as.active():
        return None                                     # MO3-A1: a read-only support view never opens the user's own key
    record = await byok.connected_key(session, user)
    if record is None or not await byok.consent_accepted(session, user, record.provider):
        return None
    if need_vision and record.provider not in ai_providers.VISION_PROVIDERS:
        raise ProviderError(byok.PLAIN_ERRORS["no_vision"], kind="no_vision")
    blocked = await byok.cap_block(session, user, await byok.get_prefs(session, user))
    if blocked:
        raise limit_reached(blocked)
    extra: Dict[str, Any] = {"vision": True} if need_vision else {}
    if ai_providers.paid_model(record.provider) and await byok.paid_plan_confirmed(session, user, record):
        extra["paid"] = True                              # MO2 A: a confirmed paid plan runs on paid models, never free ones
    await ensure_tenant_key(session, user.tenant_id)
    key = byok.open_key(record, user=user)
    try:
        inner = ai_providers.build_byok_provider(record.provider, key, byok.effective_model(record, record.provider), client=client, task=task,
                                                 **extra)
    finally:
        key = ""
    return UserKeyProvider(inner=inner, session=session, user=_Who(int(user.id), int(user.tenant_id)), feature=task, record_id=int(record.id))


async def provider_for_user(session: AsyncSession, user: User, tenant: Tenant, task: str, need_vision: bool = False, *,
                            client: Optional[Any] = None) -> Any:
    """The provider one user-started AI call runs on (see the module docstring). Flag OFF: exactly today's provider."""
    from app.ai import settings as ai_settings
    if await one_key_on(session, tenant.id):
        own = await user_key_provider(session, user, task, need_vision=need_vision, client=client)
        if own is not None:
            return own
    extra: Dict[str, Any] = {"client": client} if client is not None else {}     # the exact call of today (tests swap it)
    return await ai_settings.provider_for(session, tenant, task=task, user_id=user.id, **extra)


async def refuse_if_not_connected(session: AsyncSession, tenant_id: int, provider: Any) -> None:
    """For a feature that cannot answer from rules: with `ai_one_key` on and no AI at all, the one refusal code."""
    if isinstance(provider, RuleBasedProvider) and await one_key_on(session, tenant_id):
        raise not_connected()


async def mark_used(session: AsyncSession, tenant_id: int, provider: Any, error: Optional[str] = None) -> None:
    """The organisation's "last used" stamp - only for a call that ran on the organisation's key (R3)."""
    if isinstance(provider, UserKeyProvider):
        return
    from app.ai import settings as ai_settings
    await ai_settings.mark_used(session, tenant_id, error=error)


def is_user_key(provider: Any) -> bool:
    return isinstance(provider, UserKeyProvider)
