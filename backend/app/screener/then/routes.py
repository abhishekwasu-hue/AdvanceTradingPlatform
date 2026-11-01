"""N1 X1: the scan Then template of an alert rule (flag `scan_then`, off -> 503). The routes only read and write the
template row: they never evaluate the gate, queue an intent or reach an order path (the trading worker does the work)."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user, require_trader
from app.auth.scopes import require_scope
from app.db.models import AlertRuleRecord, ExecutionIntentRecord, ScanThenTemplateRecord, User
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


def _view(row: Any, rule: AlertRuleRecord, user: User, include_status: bool = True, auto_paper: Any = None) -> Dict[str, Any]:
    """The template plus what the caller may do with it: who set it (you / another member) and whether the server would
    take the caller's save or removal (the panel hides what it would refuse)."""
    out = T.as_dict(row, rule.id, include_status=include_status, auto_paper=auto_paper)
    out["set_by_you"] = row is None or row.user_id == user.id
    out["may_change"] = _may_change(user, rule)
    return out


async def _template(session: AsyncSession, rule: AlertRuleRecord) -> Any:
    return await session.scalar(select(ScanThenTemplateRecord).where(ScanThenTemplateRecord.rule_id == rule.id,
                                                                     ScanThenTemplateRecord.tenant_id == rule.tenant_id))


async def _auto_block(session: AsyncSession, rule: AlertRuleRecord, row: Any, known: Any = None,
                      counts: Optional[Dict[int, int]] = None) -> Dict[str, Any]:
    """N1 X2: the template's Auto PAPER state - the three switches and today's count against the limit. `known` /
    `counts`: the organisation's switches and today's counts by rule, when the caller read them once for many rules."""
    from app.execution import automation
    from app.screener.then import auto_paper
    if row is None:
        return {"available": False, "armed": False, "why": "no template", "today": 0, "max_per_day": None}
    av = await auto_paper.availability(session, rule.tenant_id, row, known=known)
    today = (counts.get(rule.id, 0) if counts is not None
             else await auto_paper.entries_today(session, rule.tenant_id, rule.id, datetime.now(timezone.utc)))
    # M68 PR-6 (TS1-063): the template owner's own limit (None = Not set: no Auto PAPER until he sets it).
    own = await automation.load_for(session, row.user_id)
    return {"available": av.available, "armed": av.armed, "why": av.why, "today": today,
            "max_per_day": own.scan_auto_max, "armed_at": (T.arm_of(row) or {}).get("at")}


@router.get("/rules/{rule_id}/then")
async def get_then(rule_id: int, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    await require_flag(session, T.FLAG, user.tenant_id)
    rule = await _rule(session, user, rule_id)
    row = await _template(session, rule)
    return _view(row, rule, user, include_status=_sees_status(user, row), auto_paper=await _auto_block(session, rule, row))


class ArmBody(BaseModel):
    confirm: str = Field(description='type "AUTO_PAPER" to confirm: confirmed setups are then placed as PAPER orders without a proposal')


@router.put("/rules/{rule_id}/then/arm", dependencies=[Depends(require_scope("alerts:write"))])
async def arm_then(rule_id: int, body: ArmBody, user: User = Depends(require_trader),
                   session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    """N1 X2: the template's own owner arms Auto PAPER (the entries go to their PAPER book). Needs the platform's
    `scan_auto_paper` switch on and the template in AUTO_PAPER mode; audited. A template PUT or a mode change disarms."""
    from app.audit.log import write_audit_log
    from app.platform.controls import flag_enabled
    from app.screener.then import auto_paper
    await require_flag(session, T.FLAG, user.tenant_id)
    rule = await _rule(session, user, rule_id)
    row = await _template(session, rule)
    if row is None or row.mode != "AUTO_PAPER":
        raise HTTPException(status_code=409, detail="Only an Auto PAPER template can be armed")
    if row.user_id != user.id:
        raise HTTPException(status_code=403, detail="Only the template's owner arms it: the entries go to their own PAPER book")
    if not await flag_enabled(session, auto_paper.FLAG, user.tenant_id):
        raise HTTPException(status_code=409, detail="Auto PAPER is not offered on this platform (the operator's switch is off)")
    if (body.confirm or "").strip() != T.ARM_CONFIRM:
        raise HTTPException(status_code=422, detail=f'Type "{T.ARM_CONFIRM}" to confirm')
    # M68 PR-6 (TS1-063, TS-3): the limits are his own - arming asks for the ones Not set (Settings > Trade settings).
    from app.execution import automation
    missing = (await automation.load(session, user)).scan_missing()
    if missing:
        raise HTTPException(status_code=422, detail=[{"loc": ["values", f"{automation.SECTION}.{name}"], "type": "needs_setting",
                                                      "msg": automation.needs_setting(name)} for name in missing])
    now = datetime.now(timezone.utc)
    T.set_arm(row, user.id, now.isoformat())
    row.updated_at = now
    await write_audit_log(session, user.tenant_id, user.id, "scan_auto_paper_armed", f"rule #{rule.id} template #{row.id}")
    await session.commit()
    return _view(row, rule, user, auto_paper=await _auto_block(session, rule, row))


@router.delete("/rules/{rule_id}/then/arm", dependencies=[Depends(require_scope("alerts:write"))])
async def disarm_then(rule_id: int, user: User = Depends(require_trader), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    """N1 X2: disarms Auto PAPER - the template's owner, or the organisation OWNER for any template (X2-5); audited."""
    from app.audit.log import write_audit_log
    await require_flag(session, T.FLAG, user.tenant_id)
    rule = await _rule(session, user, rule_id)
    row = await _template(session, rule)
    if row is None:
        raise HTTPException(status_code=404, detail="No Then template")
    if row.user_id != user.id and not _is_owner_role(user):
        raise HTTPException(status_code=403, detail="Only the template's owner or the organisation owner disarms it")
    was = T.is_armed(row)
    T.set_arm(row, None, None)
    row.updated_at = datetime.now(timezone.utc)
    await write_audit_log(session, user.tenant_id, user.id, "scan_auto_paper_disarmed", f"rule #{rule.id} template #{row.id} (was armed: {was})")
    await session.commit()
    return _view(row, rule, user, include_status=_sees_status(user, row), auto_paper=await _auto_block(session, rule, row))


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
    was_armed = T.is_armed(row)
    row.user_id, row.mode, row.confirm_tf, row.enabled = user.id, mode, body.confirm_tf, body.enabled
    # N1 X2: a saved template is never armed - the arming key is not kept (arm again after any change).
    row.gate_settings_json, row.updated_at = json.dumps(gate, sort_keys=True), now
    row.last_bar_at, row.last_status_json, row.last_problem = None, None, None
    if was_armed:
        from app.audit.log import write_audit_log
        await write_audit_log(session, user.tenant_id, user.id, "scan_auto_paper_disarmed", f"rule #{rule.id}: the template was changed")
    await session.commit()
    return _view(row, rule, user, auto_paper=await _auto_block(session, rule, row))


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
    return _view(None, rule, user, auto_paper=await _auto_block(session, rule, None))


@router.get("/rules/{rule_id}/then/intents")
async def then_intents(rule_id: int, limit: int = 50, user: User = Depends(get_current_user),
                       session: AsyncSession = Depends(get_session)) -> List[Dict[str, Any]]:
    """The caller's OWN execution-core intents raised by this rule's Then template (newest first): what the core did with
    each confirmed setup - notified, proposed (with the proposal id), refused or expired, and why. Read-only."""
    await require_flag(session, T.FLAG, user.tenant_id)
    rule = await _rule(session, user, rule_id)
    rows = await session.scalars(select(ExecutionIntentRecord).where(
        ExecutionIntentRecord.tenant_id == rule.tenant_id, ExecutionIntentRecord.user_id == user.id, ExecutionIntentRecord.source == "scan",
        ExecutionIntentRecord.source_ref.like(f"rule:{rule.id}:symbol:%")).order_by(ExecutionIntentRecord.id.desc())
        .limit(max(1, min(int(limit), 200))))
    return [T.intent_dict(row) for row in rows]


@router.get("/then")
async def all_then(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    """Review of X1b (LOW): every Then template of the rules the caller sees, in one read (the panel's chips, instead of
    one GET per rule): rule id -> the same shape as GET /rules/{id}/then."""
    from app.alerts.rules_routes import _own_rules_only
    await require_flag(session, T.FLAG, user.tenant_id)
    query = select(AlertRuleRecord).where(AlertRuleRecord.tenant_id == user.tenant_id)
    if await _own_rules_only(session, user):
        query = query.where(AlertRuleRecord.created_by == user.id)                  # BT1b: the same rules the rule list shows
    rules = list(await session.scalars(query))
    rows = {r.rule_id: r for r in await session.scalars(select(ScanThenTemplateRecord).where(
        ScanThenTemplateRecord.tenant_id == user.tenant_id, ScanThenTemplateRecord.rule_id.in_([r.id for r in rules] or [0])))}
    from app.screener.then import auto_paper
    known = await auto_paper.switches(session, user.tenant_id) if rows else None     # re-check of X2: read once, not per rule
    counts = await auto_paper.entries_today_by_rule(session, user.tenant_id, datetime.now(timezone.utc)) if rows else {}
    out: Dict[str, Any] = {}
    for rule in rules:
        row = rows.get(rule.id)
        out[str(rule.id)] = _view(row, rule, user, include_status=_sees_status(user, row),
                                  auto_paper=await _auto_block(session, rule, row, known, counts))
    return {"templates": out}
