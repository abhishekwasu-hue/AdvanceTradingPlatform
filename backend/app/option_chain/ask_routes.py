"""OC3 (owner's order, PART C2): "Ask AI" on the Option chain and the user's saved analyses, behind `ui_v2` (404 while
off, as `GET /api/signals/feed`).

* `POST /api/option-chain/ask {symbol, expiry, question}` - the AI turns the question into an analysis spec
  (app/option_chain/ask_ai.py); the server validates it and evaluates it on the caller's own broker chain
  (app/option_chain/ask_spec.py, app/option_chain/ask_adapter.py). Like every AI content route it needs the Copilot
  acknowledgement (428) and the `ai_copilot` kill flag (503 when the operator turned AI off), and it counts against the
  plan's AI rate limit (`ai_rate_limit`: 429 with a plain sentence) and the plan's monthly AI budget (the organisation's
  provider is metered; once the budget is spent the answer is a plain "quota" state, before any chain read or AI call).
  The model is the organisation's own provider and key (Settings > AI) at the operator's fast tier for the
  `option_chain_ask` task; a user's personal Vision key (BYOK) is never used for it (docs/BYOK.md).
* `POST /api/option-chain/eval {symbol, expiry, spec}` - evaluates a spec the caller already has (a saved "My
  analysis") on the current chain. No AI is called and no AI budget is spent.
* Saved analyses are the caller's own preferences namespace `oc_analyses` (`GET /api/me/prefs`,
  `PUT /api/me/prefs/oc_analyses?version=`, app/me/prefs.py): per user, so phone and desktop agree; renamed and deleted
  there. No table and no migration.

Both read the chain through the caller's broker session with the option-chains per-user rate limit. Nothing is stored
but the usage counts, the AI call log and one audit row per question; nothing places, changes or suggests an order
(ADR-0006).
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.compliance_terms import require_ai_acknowledged
from app.ai.rate_limit import ai_rate_limit
from app.auth.dependencies import get_current_user
from app.db.models import Tenant, User
from app.db.session import get_session
from app.market_data.candles_routes import chains_rate_limit
from app.option_chain import ask_adapter, ask_ai, ask_spec, snapshots
from app.option_chain.ask_spec import AnalysisSpec

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/option-chain", tags=["option-chain"])
FLAG = "ui_v2"
AI_FLAG = "ai_copilot"

NO_PROVIDER = "Ask AI needs an AI provider for your organisation (Settings > AI). Saved analyses still update without AI."
NOT_IN_PLAN = "Ask AI is not included in your plan. Saved analyses still update; they use no AI."
QUOTA = "Your plan's AI budget for this month is used up, so Ask AI is paused until the 1st. Saved analyses still update; they use no AI."
AI_ERROR = "The AI did not answer just now. Try again in a moment."


class AskBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbol: str = Field(min_length=1, max_length=30)
    expiry: Optional[date] = None
    question: str = Field(min_length=3, max_length=500)


class EvalBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbol: str = Field(min_length=1, max_length=30)
    expiry: Optional[date] = None
    spec: AnalysisSpec


async def _flag(session: AsyncSession, user: User) -> None:
    from app.platform.controls import flag_enabled
    if not await flag_enabled(session, FLAG, user.tenant_id):
        raise HTTPException(status_code=404, detail="Not found", headers={"X-Feature-Disabled": FLAG})


async def require_ui_v2(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> None:
    """The routes' FIRST dependency: while `ui_v2` is off a request is a 404 before any rate-limit unit is spent and
    before the Copilot acknowledgement is asked for (428)."""
    await _flag(session, user)


def _underlying(symbol: str) -> str:
    try:
        return snapshots.normalise_underlying(symbol)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Enter an index or stock name, for example NIFTY.") from exc


def _data(read: ask_adapter.ChainRead, now: datetime) -> Dict[str, Any]:
    out = {"spot": read.spot, "source": read.source, "data_as_of": now.isoformat(), "snapshots": len(read.history),
           "history_note": read.history_note}
    if read.snapshot:                           # M71 W2-E: an earlier chain while the broker is busy - its own time and age
        out.update(data_as_of=read.snapshot["captured_at"], snapshot=read.snapshot)
    return out


def _evaluated(spec: AnalysisSpec, read: ask_adapter.ChainRead, now: datetime) -> Dict[str, Any]:
    return {"spec": spec.model_dump(mode="json", exclude_none=True), "description": ask_spec.describe(spec),
            "result": ask_spec.evaluate(spec, read.rows, spot=read.spot, now=now, history=read.history)}


def _base(underlying: str, read_expiry: Optional[str], state: str, message: Optional[str]) -> Dict[str, Any]:
    return {"symbol": underlying, "expiry": read_expiry, "state": state, "message": message, "spec": None, "description": [],
            "result": None, "closest": None, "data": None, "disclaimer": ask_ai.disclaimer()}


async def _ai_off_message(session: AsyncSession, tenant: Tenant, provider: Any) -> tuple:
    from app.ai import metering
    from app.plans.limits import feature_allowed
    if not feature_allowed(tenant, "ai_features"):
        return "ai_off", NOT_IN_PLAN
    if await metering.budget_exhausted(session, tenant):
        return "quota", QUOTA
    reason = str(getattr(provider, "reason", "") or "").strip()
    if reason:
        return "ai_off", f"Ask AI is not available: {reason}."
    return "ai_off", NO_PROVIDER


@router.post("/ask", dependencies=[Depends(require_ui_v2), Depends(ai_rate_limit), Depends(chains_rate_limit)])
async def ask(body: AskBody, user: User = Depends(require_ai_acknowledged), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    """The question -> a validated analysis spec -> its table (and chart) on the caller's own chain, with "How this was
    calculated" and the educational disclaimer. An unsupported request gets one plain sentence and the closest analysis."""
    from app.ai import metering
    from app.ai.providers import RuleBasedProvider
    from app.audit.log import write_audit_log
    from app.platform.controls import require_flag
    await _flag(session, user)
    await require_flag(session, AI_FLAG, user.tenant_id)
    underlying = _underlying(body.symbol)
    tenant = await session.get(Tenant, user.tenant_id)
    if tenant is None:
        raise HTTPException(status_code=404, detail="Organisation not found")
    expiry_text = body.expiry.isoformat() if body.expiry else None
    from app.ai import resolve as ai_resolve
    provider = await ai_resolve.provider_for_user(session, user, tenant, ask_ai.TASK)
    if isinstance(provider, RuleBasedProvider):
        if await ai_resolve.one_key_on(session, tenant.id):             # Settings redesign A1: the Connect AI panel opens
            return {**_base(underlying, expiry_text, "ai_off", ai_resolve.NOT_CONNECTED_MESSAGE), "code": ai_resolve.NOT_CONNECTED_CODE}
        state, message = await _ai_off_message(session, tenant, provider)
        return _base(underlying, expiry_text, state, message)           # nothing read, nothing spent
    now = datetime.now(timezone.utc)
    read = await ask_adapter.read(session, user, underlying, body.expiry, now)
    if read.state != "ok":
        return _base(underlying, read.expiry, read.state, read.message)  # no chain: the AI is not asked
    async def budget_left() -> bool:                       # asked before the repair turn: a second call needs budget too
        if ai_resolve.is_user_key(provider):               # the user's own key: its own cap is checked before each call
            return True
        return not await metering.budget_exhausted(session, tenant)

    found = ask_ai.Translation("error")
    try:
        found = await ask_ai.translate(provider, body.question, may_retry=budget_left)
    except Exception as exc:  # noqa: BLE001 - after a paid call the answer is a plain state, never a 500 (class name only)
        logger.warning("OC3 ask: translation failed (%s)", type(exc).__name__)
    finally:
        # the call was paid for: the provider's last use, the audit row and the metered usage are always kept
        await ai_resolve.mark_used(session, user.tenant_id, provider, error="Ask AI: the provider did not answer" if found.state == "error" else None)
        await write_audit_log(session, user.tenant_id, user.id, "option_chain_ask",
                              f"{getattr(provider, 'name', 'ai')}: {found.state} after {found.attempts} call(s); {underlying}; "
                              f"question {len(body.question)} chars")
        await session.commit()
    out = _base(underlying, read.expiry, found.state, None)
    out.update(data=_data(read, now), provider=getattr(provider, "name", None), model=getattr(provider, "model", None),
               prompt_version=ask_ai.PROMPT_VERSION, usage=metering.spent_by(provider))
    if found.state == "spec" and found.spec is not None:
        out.update(state="ok", **_evaluated(found.spec, read, now))
    elif found.state == "quota":
        out.update(message=QUOTA)
    elif found.state in ("unsupported", "cannot"):
        out["message"] = found.reason or ask_ai.CANNOT
        if found.closest is not None:
            out["closest"] = {**_evaluated(found.closest, read, now), "note": ask_ai.CLOSEST_NOTE}
    else:
        out.update(state="ai_error", message=AI_ERROR)
    return out


@router.post("/eval", dependencies=[Depends(require_ui_v2), Depends(chains_rate_limit)])
async def eval_spec(body: EvalBody, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    """A spec the caller already has (a saved analysis), evaluated on the current chain. No AI: nothing is metered
    against the AI budget, so a saved analysis keeps updating when the AI quota is used up."""
    await _flag(session, user)
    underlying = _underlying(body.symbol)
    now = datetime.now(timezone.utc)
    read = await ask_adapter.read(session, user, underlying, body.expiry, now)
    if read.state != "ok":
        return _base(underlying, read.expiry, read.state, read.message)
    out = _base(underlying, read.expiry, "ok", None)
    out.update(data=_data(read, now), **_evaluated(body.spec, read, now))
    return out


__all__ = ["router", "FLAG", "AskBody", "EvalBody", "NO_PROVIDER", "NOT_IN_PLAN", "QUOTA", "AI_ERROR"]
