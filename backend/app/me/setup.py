"""UI2-7 (UI v2 proposal §11, §12, §13 UI-8): the caller's setup wizard progress, per user, so it resumes on any device.

* `GET /api/me/setup` - the four steps, each with `done` worked out now from the rows that prove it, next to the marks
  the trader stored (a skip per step, the dashboard chip's dismissal, the wizard's one automatic opening). "Done" is
  never stored, so the chip cannot drift from the real state (a broker removed later makes the step open again).
  A skip only changes the order (§11): the next step is the first one not done, preferring one not skipped, and setup
  is complete when every step is done - a skipped broker step counts as done ("I'll trade on paper first").
* `PUT /api/me/setup?version=` - a thin alias of `PUT /api/me/prefs/setup` (same schema, size cap, versions and 409;
  app/me/prefs.py), answering the same view as the GET.

What makes a step done (reads only; nothing here writes to a broker, the risk settings, a deployment or an order):
- broker:   a broker credential of the organisation is stored and verified (`last_verified_at`: a login or a check
            went through; storing new session credentials clears it), or (P3) it holds a working Upstox Analytics Token;
- risk:     the trader saved their own PAPER risk limits (`/paper/risk`), or the organisation's risk settings exist
            (the Risk page);
- strategy: the trader started a PAPER deployment, or ran a backtest (each run is saved);
- alerts:   an alert channel of the organisation is on and its last send (a test or a real alert) went through since its
            settings last changed (a changed destination clears `last_delivered_at`, app/alerts/routes.py).
P3 (the owner's "simple front page"): the same answer carries `get_started`, the Dashboard's three-step card in both
shells (the four-step wizard stays at Settings -> Setup, but nothing on the front page sends the trader there):
- broker:    the broker step above (a verified credential of the organisation, or an Upstox Analytics Token that serves
             candles - see below);
- paper_bot: the trader started a PAPER deployment of their own (any status; a LIVE one never counts);
- telegram:  the organisation's TELEGRAM alert channel is on and its last send went through (the alerts rule above,
             Telegram only).
An Upstox Analytics Token counts as a broker for PAPER trading: it is unexpired and its last probe found the candle
endpoints it serves (`upstox_analytics.analytics_usable`, the market-data gate's own rule), so a paper bot can read
prices without the daily login. Orders always need the daily login, and nothing here makes a LIVE order possible.
The card's own marks (`get_started_hidden_at`, `get_started_shown_at`, app/me/prefs.py SetupPrefs) decide whether it
shows: see get_started_view.

Like the other preferences this is not behind a flag: the trader's own data, computed from their own rows, places
nothing. Every read filters on the caller's tenant (and user where the row has one).

Settings redesign part D: the answer also carries `health` (app/me/setup_health.py) - what in the caller's setup needs an
action now (an expiring data token, a failing AI key, Telegram not connected, delayed market data), cached per (tenant,
user); null while `ui_v2` is off.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Request
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.brokers import upstox_analytics
from app.core.enums import AlertChannelType
from app.db.models import (
    AlertChannelRecord,
    BacktestRunRecord,
    BrokerCredentialRecord,
    PaperAccountRecord,
    RiskSettingsRecord,
    StrategyDeploymentRecord,
    User,
)
from app.db.session import get_session
from app.me import prefs, setup_health

STEPS = ("broker", "risk", "strategy", "alerts")
GET_STARTED = ("broker", "paper_bot", "telegram")
NAMESPACE = "setup"

router = APIRouter(prefix="/api/me", tags=["me"])


async def _exists(session: AsyncSession, statement) -> bool:
    return await session.scalar(statement.limit(1)) is not None


async def _broker_done(session: AsyncSession, tenant_id: int) -> bool:
    """A verified broker credential of the organisation, or an Upstox Analytics Token that serves candles (PAPER)."""
    if await _exists(session, select(BrokerCredentialRecord.id).where(
            BrokerCredentialRecord.tenant_id == tenant_id, BrokerCredentialRecord.last_verified_at.is_not(None))):
        return True
    upstox = await session.scalars(select(BrokerCredentialRecord).where(
        BrokerCredentialRecord.tenant_id == tenant_id, BrokerCredentialRecord.broker_name == upstox_analytics.BROKER))
    # Decrypted in memory only to read the token's expiry and coverage; the value is never returned or logged.
    return any(upstox_analytics.analytics_usable(record) for record in upstox)


async def done_steps(session: AsyncSession, tenant_id: int, user_id: int) -> Dict[str, bool]:
    """Which steps the existing rows already prove done (see the module docstring): the wizard's four (STEPS) and the
    Get started card's two others (`paper_bot`, `telegram`; its broker step is the wizard's)."""
    broker = await _broker_done(session, tenant_id)
    risk = await _exists(session, select(PaperAccountRecord.id).where(
        PaperAccountRecord.tenant_id == tenant_id, PaperAccountRecord.user_id == user_id,
        PaperAccountRecord.risk_settings_json.is_not(None), PaperAccountRecord.risk_settings_json.not_in(("", "{}"))))
    risk = risk or await _exists(session, select(RiskSettingsRecord.id).where(RiskSettingsRecord.tenant_id == tenant_id))
    paper_bot = await _exists(session, select(StrategyDeploymentRecord.id).where(
        StrategyDeploymentRecord.tenant_id == tenant_id, StrategyDeploymentRecord.created_by == user_id,
        StrategyDeploymentRecord.mode == "PAPER"))
    strategy = paper_bot or await _exists(session, select(BacktestRunRecord.id).where(
        BacktestRunRecord.tenant_id == tenant_id, BacktestRunRecord.user_id == user_id))
    delivered = (AlertChannelRecord.tenant_id == tenant_id, AlertChannelRecord.enabled.is_(True),
                 AlertChannelRecord.last_delivered_at.is_not(None),
                 or_(AlertChannelRecord.last_error.is_(None), AlertChannelRecord.last_error == ""))
    alerts = await _exists(session, select(AlertChannelRecord.id).where(*delivered))
    telegram = alerts and await _exists(session, select(AlertChannelRecord.id).where(
        *delivered, AlertChannelRecord.channel_type == AlertChannelType.TELEGRAM.value))
    return {"broker": broker, "risk": risk, "strategy": strategy, "alerts": alerts, "paper_bot": paper_bot, "telegram": telegram}


def get_started_view(stored: Dict[str, Any], done: Dict[str, bool]) -> Dict[str, Any]:
    """The Dashboard's Get started card: three steps, each done (worked out) or not, and whether the card shows.

    It shows while a step is open, until the trader hides it (`get_started_hidden_at`). Once all three are done it is
    gone; the app then stores `get_started_hidden_at` too, so it stays gone ("for good") even if a step opens again later
    (a broker removed). Help -> "Show Get started" clears that mark; when every step is already done it also sets
    `get_started_shown_at`, and the card shows its three ticks until it is hidden again (which clears that mark)."""
    steps = [{"key": key, "done": bool(done.get(key))} for key in GET_STARTED]
    complete = all(s["done"] for s in steps)
    hidden_at: Optional[str] = stored.get("get_started_hidden_at")
    shown_at: Optional[str] = stored.get("get_started_shown_at")
    return {
        "steps": steps,
        "done_count": sum(1 for s in steps if s["done"]),
        "complete": complete,
        "hidden_at": hidden_at,
        "shown_at": shown_at,
        "visible": hidden_at is None and (not complete or shown_at is not None),
    }


def counts_as_done(key: str, done: bool, skipped: bool) -> bool:
    """A step counts towards complete when done; the broker step also when skipped (paper first, §11 step 1)."""
    return done or (key == "broker" and skipped)


def view(stored: Dict[str, Any], version: int, done: Dict[str, bool]) -> Dict[str, Any]:
    """The wizard's and the Get started card's one answer: each step done (worked out) or skipped (stored), the next step
    (the first not done, preferring one not skipped; null = complete), the card (get_started_view), and the stored marks
    with their version for the next save."""
    steps: List[Dict[str, Any]] = []
    for key in STEPS:
        skipped_at: Optional[str] = stored["steps"][key]["skipped_at"]
        steps.append({"key": key, "done": bool(done.get(key)), "skipped": skipped_at is not None and not done.get(key), "skipped_at": skipped_at})
    open_steps = [s for s in steps if not counts_as_done(s["key"], s["done"], s["skipped"])]
    fresh = [s for s in open_steps if not s["skipped"]]
    return {
        "steps": steps,
        "done_count": sum(1 for s in steps if s["done"]),
        "next": (fresh or open_steps)[0]["key"] if open_steps else None,
        "complete": not open_steps,
        "get_started": get_started_view(stored, done),
        "stored": stored,
        "version": version,
    }


@router.get("/setup")
async def get_setup(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    """The caller's own setup progress: the four steps (done worked out now, skips stored) and the stored marks."""
    stored, version = await prefs.read_one(NAMESPACE, user, session)
    return {**view(stored, version, await done_steps(session, user.tenant_id, user.id)), "health": await setup_health.health(session, user)}


@router.put("/setup")
async def put_setup(request: Request, version: Optional[int] = None, user: User = Depends(get_current_user),
                    session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    """`PUT /api/me/prefs/setup` under its own name (same rules); answers the GET's view of what was saved."""
    tenant_id, user_id = user.tenant_id, user.id        # read once: a failed save rolls back and expires `user`
    body = await prefs.read_body(request)
    stored, saved_version = await prefs.save(NAMESPACE, body, version, user, session)
    return {**view(stored, saved_version, await done_steps(session, tenant_id, user_id)),
            "health": await setup_health.health(session, setup_health.Caller(user_id, tenant_id))}


__all__ = ["router", "STEPS", "GET_STARTED", "counts_as_done", "done_steps", "get_started_view", "view"]
