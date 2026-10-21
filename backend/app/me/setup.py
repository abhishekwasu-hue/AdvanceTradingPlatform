"""UI2-7 (UI v2 proposal §11, §12, §13 UI-8): the caller's setup wizard progress, per user, so it resumes on any device.

* `GET /api/me/setup` - the four steps, each with `done` worked out now from the rows that prove it, next to the marks
  the trader stored (a skip per step, the dashboard chip's dismissal, the wizard's one automatic opening). "Done" is
  never stored, so the chip cannot drift from the real state (a broker removed later makes the step open again).
* `PUT /api/me/setup?version=` - a thin alias of `PUT /api/me/prefs/setup` (same schema, size cap, versions and 409;
  app/me/prefs.py), answering the same view as the GET.

What makes a step done (reads only; nothing here writes to a broker, the risk settings, a deployment or an order):
- broker:   a broker credential is stored for the organisation;
- risk:     the trader saved their own PAPER risk limits (`/paper/risk`), or the organisation's risk settings exist
            (the Risk page);
- strategy: the trader started a PAPER deployment, or ran a backtest (each run is saved);
- alerts:   an alert channel of the organisation is on and its last send (a test or a real alert) went through, or the
            trader linked their own Telegram chat (the bot heard the code they sent).
Like the other preferences this is not behind a flag: the trader's own data, computed from their own rows, places
nothing. Every read filters on the caller's tenant (and user where the row has one).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Request
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.db.models import (
    AlertChannelRecord, BacktestRunRecord, BrokerCredentialRecord, NotificationLinkRecord, PaperAccountRecord, RiskSettingsRecord,
    StrategyDeploymentRecord, User,
)
from app.db.session import get_session
from app.me import prefs

STEPS = ("broker", "risk", "strategy", "alerts")
NAMESPACE = "setup"

router = APIRouter(prefix="/api/me", tags=["me"])


async def _exists(session: AsyncSession, statement) -> bool:
    return await session.scalar(statement.limit(1)) is not None


async def done_steps(session: AsyncSession, tenant_id: int, user_id: int) -> Dict[str, bool]:
    """Which steps the existing rows already prove done (see the module docstring)."""
    broker = await _exists(session, select(BrokerCredentialRecord.id).where(BrokerCredentialRecord.tenant_id == tenant_id))
    risk = await _exists(session, select(PaperAccountRecord.id).where(
        PaperAccountRecord.tenant_id == tenant_id, PaperAccountRecord.user_id == user_id,
        PaperAccountRecord.risk_settings_json.is_not(None), PaperAccountRecord.risk_settings_json.not_in(("", "{}"))))
    risk = risk or await _exists(session, select(RiskSettingsRecord.id).where(RiskSettingsRecord.tenant_id == tenant_id))
    strategy = await _exists(session, select(StrategyDeploymentRecord.id).where(
        StrategyDeploymentRecord.tenant_id == tenant_id, StrategyDeploymentRecord.created_by == user_id,
        StrategyDeploymentRecord.mode == "PAPER"))
    strategy = strategy or await _exists(session, select(BacktestRunRecord.id).where(
        BacktestRunRecord.tenant_id == tenant_id, BacktestRunRecord.user_id == user_id))
    alerts = await _exists(session, select(AlertChannelRecord.id).where(
        AlertChannelRecord.tenant_id == tenant_id, AlertChannelRecord.enabled.is_(True),
        AlertChannelRecord.last_delivered_at.is_not(None), or_(AlertChannelRecord.last_error.is_(None), AlertChannelRecord.last_error == "")))
    alerts = alerts or await _exists(session, select(NotificationLinkRecord.id).where(
        NotificationLinkRecord.tenant_id == tenant_id, NotificationLinkRecord.user_id == user_id, NotificationLinkRecord.status == "linked"))
    return {"broker": broker, "risk": risk, "strategy": strategy, "alerts": alerts}


def view(stored: Dict[str, Any], version: int, done: Dict[str, bool]) -> Dict[str, Any]:
    """The wizard's and the chip's one answer: each step done (worked out) or skipped (stored), the next open step
    (neither done nor skipped; null = setup complete), and the stored marks with their version for the next save."""
    steps: List[Dict[str, Any]] = []
    for key in STEPS:
        skipped_at: Optional[str] = stored["steps"][key]["skipped_at"]
        steps.append({"key": key, "done": bool(done.get(key)), "skipped": skipped_at is not None and not done.get(key), "skipped_at": skipped_at})
    open_steps = [s["key"] for s in steps if not s["done"] and not s["skipped"]]
    return {
        "steps": steps,
        "done_count": sum(1 for s in steps if s["done"]),
        "next": open_steps[0] if open_steps else None,
        "complete": not open_steps,
        "stored": stored,
        "version": version,
    }


@router.get("/setup")
async def get_setup(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    """The caller's own setup progress: the four steps (done worked out now, skips stored) and the stored marks."""
    stored, version = await prefs.read_one(NAMESPACE, user, session)
    return view(stored, version, await done_steps(session, user.tenant_id, user.id))


@router.put("/setup")
async def put_setup(request: Request, version: Optional[int] = None, user: User = Depends(get_current_user),
                    session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    """`PUT /api/me/prefs/setup` under its own name (same rules); answers the GET's view of what was saved."""
    tenant_id, user_id = user.tenant_id, user.id        # read once: a failed save rolls back and expires `user`
    body = await prefs._body(request)
    stored, saved_version = await prefs.save(NAMESPACE, body, version, user, session)
    return view(stored, saved_version, await done_steps(session, tenant_id, user_id))


__all__ = ["router", "STEPS", "done_steps", "view"]
