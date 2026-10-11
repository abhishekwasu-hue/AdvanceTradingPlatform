"""S3a (ADR-0022): alert rules, the organisation's delivery policy and the per-rule event log, behind the
`screener_v2` flag. A rule's condition is validated by ScreenQL before it is saved; a rule never places an order."""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user, require_trader
from app.db.models import AlertChannelRecord, AlertDeliveryRecord, AlertEventRecord, AlertRuleRecord, NotificationPolicyRecord, ScreenRecord, User
from app.db.session import get_session
from app.platform.controls import require_flag

router = APIRouter(prefix="/api/alerts", tags=["alerts"])
FLAG = "screener_v2"
HHMM = r"^([01]\d|2[0-3]):[0-5]\d$"
SYMBOL = re.compile(r"^[A-Z0-9&._:-]{1,40}$")


class RuleBody(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    kind: Literal["screen", "instrument"]
    screen_id: Optional[int] = None
    symbol: Optional[str] = Field(default=None, pattern=r"^[A-Za-z0-9 &._:-]{1,40}$")
    condition: Optional[str] = Field(default=None, max_length=4000, description="ScreenQL condition (instrument rules)")
    base_tf: str = Field(default="1d", pattern=r"^(1m|3m|5m|15m|30m|1h|1d|1w|1M)$")
    priority: Literal["critical", "normal", "low"] = "normal"
    cooldown_minutes: int = Field(default=60, ge=0, le=7 * 24 * 60)
    mode: Literal["instant", "digest"] = "instant"
    digest_every: Literal["hourly", "eod"] = "hourly"
    expires_at: Optional[datetime] = None
    symbols: List[str] = Field(default_factory=list, max_length=50, description="screen rules: the symbols the screen runs on")
    exchange: str = Field(default="NSE", pattern=r"^[A-Z]{2,10}$")


class PolicyBody(BaseModel):
    timezone: str = "Asia/Kolkata"
    quiet_start: Optional[str] = Field(default=None, pattern=HHMM)
    quiet_end: Optional[str] = Field(default=None, pattern=HHMM)
    max_per_hour: int = Field(default=30, ge=1, le=600)
    group_window_seconds: int = Field(default=10, ge=0, le=600)
    eod_digest_time: str = Field(default="15:45", pattern=HHMM)

    @field_validator("timezone")
    @classmethod
    def _tz(cls, v: str) -> str:
        try:
            ZoneInfo(v)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"unknown timezone {v!r}") from exc
        return v


def _rule_dict(r: AlertRuleRecord) -> Dict[str, Any]:
    return {"id": r.id, "name": r.name, "kind": r.kind, "screen_id": r.screen_id, "symbol": r.symbol, "condition": r.condition_text,
            "base_tf": r.base_tf, "priority": r.priority, "cooldown_minutes": r.cooldown_minutes, "mode": r.mode, "digest_every": r.digest_every,
            "status": r.status, "expires_at": r.expires_at.isoformat() if r.expires_at else None,
            "symbols": json.loads(r.universe_json) if r.universe_json else ([r.symbol] if r.symbol else []), "exchange": r.exchange,
            "last_bar_at": r.last_bar_at.isoformat() if r.last_bar_at else None, "last_problem": r.last_problem}


async def _checked(session: AsyncSession, user: User, body: RuleBody) -> Dict[str, Any]:
    if body.kind == "screen":
        if body.screen_id is None:
            raise HTTPException(status_code=422, detail="a screen rule needs screen_id")
        screen = await session.scalar(select(ScreenRecord).where(ScreenRecord.id == body.screen_id, ScreenRecord.tenant_id == user.tenant_id,
                                                                 ScreenRecord.archived.is_(False)))
        if screen is None:
            raise HTTPException(status_code=404, detail="Screen not found")
        symbols = list(dict.fromkeys(s.strip().upper() for s in body.symbols if s.strip()))
        if not symbols or any(not SYMBOL.fullmatch(s) for s in symbols):
            raise HTTPException(status_code=422, detail="a screen rule needs 1-50 valid symbols to run the screen on")
        return {"screen_id": screen.id, "symbol": None, "condition_text": None, "base_tf": screen.base_tf, "universe_json": json.dumps(symbols),
                "exchange": body.exchange}
    if not body.symbol or not body.condition:
        raise HTTPException(status_code=422, detail="an instrument rule needs a symbol and a condition")
    from app.screener import compile_screen, nodes
    ast, validated = compile_screen(body.condition, base_tf=body.base_tf)
    if not validated.ok or ast is None:
        raise HTTPException(status_code=422, detail={"message": "the condition did not pass validation", "problems": [p.as_dict() for p in validated.problems]})
    if validated.cross_sectional:
        raise HTTPException(status_code=422, detail="an instrument rule cannot rank across a universe; use a screen rule")
    return {"screen_id": None, "symbol": body.symbol.strip().upper(), "condition_text": nodes.to_text(ast), "base_tf": body.base_tf,
            "universe_json": None, "exchange": body.exchange}


async def _owned(session: AsyncSession, user: User, rule_id: int) -> AlertRuleRecord:
    row = await session.scalar(select(AlertRuleRecord).where(AlertRuleRecord.id == rule_id, AlertRuleRecord.tenant_id == user.tenant_id))
    if row is None:
        raise HTTPException(status_code=404, detail="Rule not found")
    return row


@router.get("/rules")
async def list_rules(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> List[Dict[str, Any]]:
    await require_flag(session, FLAG, user.tenant_id)
    return [_rule_dict(r) for r in await session.scalars(select(AlertRuleRecord).where(AlertRuleRecord.tenant_id == user.tenant_id)
                                                          .order_by(AlertRuleRecord.id.desc()))]


@router.post("/rules", status_code=201)
async def create_rule(body: RuleBody, user: User = Depends(require_trader), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    await require_flag(session, FLAG, user.tenant_id)
    target = await _checked(session, user, body)
    now = datetime.now(timezone.utc)
    row = AlertRuleRecord(tenant_id=user.tenant_id, created_by=user.id, name=body.name.strip(), kind=body.kind, priority=body.priority,
                          cooldown_minutes=body.cooldown_minutes, mode=body.mode, digest_every=body.digest_every, status="active",
                          expires_at=body.expires_at, created_at=now, updated_at=now, **target)
    session.add(row)
    await session.commit()
    return _rule_dict(row)


@router.post("/rules/{rule_id}/{action}")
async def set_rule_status(rule_id: int, action: Literal["pause", "resume"], user: User = Depends(require_trader),
                          session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    await require_flag(session, FLAG, user.tenant_id)
    row = await _owned(session, user, rule_id)
    row.status, row.updated_at = ("paused" if action == "pause" else "active"), datetime.now(timezone.utc)
    await session.commit()
    return _rule_dict(row)


@router.get("/rules/{rule_id}/events")
async def rule_events(rule_id: int, limit: int = 100, user: User = Depends(get_current_user),
                      session: AsyncSession = Depends(get_session)) -> List[Dict[str, Any]]:
    """The rule's delivery log: every firing with its status and reason code."""
    await require_flag(session, FLAG, user.tenant_id)
    await _owned(session, user, rule_id)
    rows = await session.scalars(select(AlertEventRecord).where(AlertEventRecord.rule_id == rule_id).order_by(AlertEventRecord.id.desc())
                                 .limit(max(1, min(limit, 500))))
    return [{"id": e.id, "symbol": e.symbol, "bar_time": e.bar_time.isoformat(), "status": e.status, "reason": e.reason_code,
             "group_id": e.group_id, "notification_id": e.notification_id, "values": json.loads(e.values_json or "{}"),
             "created_at": e.created_at.isoformat() if e.created_at else None} for e in rows]


@router.get("/policy")
async def get_policy(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    await require_flag(session, FLAG, user.tenant_id)
    from app.alerts.engine import policy_for
    return dict((await policy_for(session, user.tenant_id)).__dict__)


@router.put("/policy")
async def put_policy(body: PolicyBody, user: User = Depends(require_trader), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    await require_flag(session, FLAG, user.tenant_id)
    if (body.quiet_start is None) != (body.quiet_end is None):
        raise HTTPException(status_code=422, detail="give both quiet_start and quiet_end, or neither")
    row = await session.scalar(select(NotificationPolicyRecord).where(NotificationPolicyRecord.tenant_id == user.tenant_id))
    if row is None:
        row = NotificationPolicyRecord(tenant_id=user.tenant_id)
        session.add(row)
    for k, v in body.model_dump().items():
        setattr(row, k, v)
    row.updated_at = datetime.now(timezone.utc)
    await session.commit()
    return body.model_dump()


@router.get("/dead-letters")
async def dead_letters(limit: int = 100, user: User = Depends(require_trader), session: AsyncSession = Depends(get_session)) -> List[Dict[str, Any]]:
    """S3b: deliveries that gave up after every retry (or whose channel was off), for the operator to look at or retry."""
    rows = await session.execute(select(AlertDeliveryRecord, AlertChannelRecord.channel_type).join(
        AlertChannelRecord, AlertChannelRecord.id == AlertDeliveryRecord.channel_id).where(
        AlertDeliveryRecord.tenant_id == user.tenant_id, AlertDeliveryRecord.status == "FAILED").order_by(AlertDeliveryRecord.id.desc())
        .limit(max(1, min(limit, 500))))
    return [{"id": d.id, "notification_id": d.notification_id, "channel": ctype, "attempts": d.attempts, "reason": d.reason_code,
             "last_error": d.last_error, "group_id": d.group_id, "created_at": d.created_at.isoformat() if d.created_at else None}
            for d, ctype in rows.all()]


@router.post("/deliveries/{delivery_id}/retry")
async def retry_delivery(delivery_id: int, user: User = Depends(require_trader), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    """Puts a dead-lettered delivery back in the outbox (fresh attempts); the worker sends it on its next cycle."""
    row = await session.scalar(select(AlertDeliveryRecord).where(AlertDeliveryRecord.id == delivery_id, AlertDeliveryRecord.tenant_id == user.tenant_id))
    if row is None:
        raise HTTPException(status_code=404, detail="Delivery not found")
    if row.status != "FAILED":
        raise HTTPException(status_code=409, detail=f"Only failed deliveries can be retried (this one is {row.status})")
    row.status, row.attempts, row.reason_code, row.next_attempt_at = "PENDING", 0, None, datetime.now(timezone.utc)
    await session.commit()
    return {"id": row.id, "status": row.status}


__all__ = ["router"]
