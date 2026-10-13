"""N1 X1: the scan Then template of an alert rule (flag `scan_then`, off -> 503). The routes only read and write the
template row: they never evaluate the gate, queue an intent or reach an order path (the trading worker does the work)."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user, require_trader
from app.auth.scopes import require_scope
from app.db.models import AlertRuleRecord, ScanThenTemplateRecord, User
from app.db.session import get_session
from app.platform.controls import require_flag
from app.screener.then import templates as T

router = APIRouter(prefix="/api/alerts", tags=["alerts"])


class ThenBody(BaseModel):
    mode: str = Field(default="NOTIFY", description="NOTIFY / ONE_CLICK / AUTO_PAPER (a proposal); AUTO_LIVE is refused")
    confirm_tf: str = Field(default=T.DEFAULT_TF, pattern=r"^(5m|15m)$")
    enabled: bool = True
    gate: Dict[str, Any] = Field(default_factory=dict, description="gate setting overrides (Trade's defaults otherwise)")
    take_over: bool = Field(default=False, description="review of X1b: saving a template another member set makes you its owner - "
                                                         "their setups then go to you; required to confirm that")


async def _rule(session: AsyncSession, user: User, rule_id: int) -> AlertRuleRecord:
    from app.alerts.rules_routes import _owned
    return await _owned(session, user, rule_id)


def _is_owner_role(user: User) -> bool:
    from app.core.enums import UserRole
    return user.role in (UserRole.OWNER.value, UserRole.SUPER_ADMIN.value)


def _may_change(user: User, rule: AlertRuleRecord) -> bool:
    """The rule's creator or the organisation OWNER: intents go to the template's owner, so nobody sets one for another.
    A rule without a creator (the creator left) is the OWNER's to take over only (review of #214)."""
    return (rule.created_by is not None and rule.created_by == user.id) or _is_owner_role(user)


def _sees_status(user: User, row: Any) -> bool:
    """The last check per symbol (live values from the owner's feed) is the template owner's and the OWNER's only."""
    return row is None or row.user_id == user.id or _is_owner_role(user)


def _view(row: Any, rule: AlertRuleRecord, user: User, include_status: bool = True) -> Dict[str, Any]:
    """The template plus what the caller may do with it: who set it (you / another member) and whether the server would
    take the caller's save or removal (the panel hides what it would refuse)."""
    out = T.as_dict(row, rule.id, include_status=include_status)
    out["set_by_you"] = row is None or row.user_id == user.id
    out["may_change"] = _may_change(user, rule)
    return out


async def _template(session: AsyncSession, rule: AlertRuleRecord) -> Any:
    return await session.scalar(select(ScanThenTemplateRecord).where(ScanThenTemplateRecord.rule_id == rule.id,
                                                                     ScanThenTemplateRecord.tenant_id == rule.tenant_id))


@router.get("/rules/{rule_id}/then")
async def get_then(rule_id: int, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    await require_flag(session, T.FLAG, user.tenant_id)
    rule = await _rule(session, user, rule_id)
    row = await _template(session, rule)
    return _view(row, rule, user, include_status=_sees_status(user, row))


@router.put("/rules/{rule_id}/then", dependencies=[Depends(require_scope("alerts:write"))])
async def put_then(rule_id: int, body: ThenBody, user: User = Depends(require_trader),
                   session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    await require_flag(session, T.FLAG, user.tenant_id)
    rule = await _rule(session, user, rule_id)
    if not _may_change(user, rule):
        raise HTTPException(status_code=403, detail="Only the rule's creator or the organisation owner sets its Then template")
    try:
        mode, gate = T.check_mode(body.mode), T.check_gate(body.gate)
    except T.TemplateError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    now = datetime.now(timezone.utc)
    row = await _template(session, rule)
    if row is not None and row.user_id is not None and row.user_id != user.id and not body.take_over:
        # Review of X1b (MED): an OWNER saving a member's template would silently take it over (setups go to the saver).
        raise HTTPException(status_code=409, detail="Another member set this Then. Saving makes you its owner: setups will go to you, not "
                                                    "to them. Save again with take_over to confirm.")
    if row is None:
        row = ScanThenTemplateRecord(tenant_id=rule.tenant_id, rule_id=rule.id, created_at=now)
        session.add(row)
    row.user_id, row.mode, row.confirm_tf, row.enabled = user.id, mode, body.confirm_tf, body.enabled
    row.gate_settings_json, row.updated_at = json.dumps(gate, sort_keys=True), now
    row.last_bar_at, row.last_status_json, row.last_problem = None, None, None
    await session.commit()
    return _view(row, rule, user)


@router.delete("/rules/{rule_id}/then", dependencies=[Depends(require_scope("alerts:write"))])
async def delete_then(rule_id: int, user: User = Depends(require_trader), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    await require_flag(session, T.FLAG, user.tenant_id)
    rule = await _rule(session, user, rule_id)
    if not _may_change(user, rule):
        raise HTTPException(status_code=403, detail="Only the rule's creator or the organisation owner removes its Then template")
    row = await _template(session, rule)
    if row is not None:
        await session.delete(row)
        await session.commit()
    return _view(None, rule, user)
