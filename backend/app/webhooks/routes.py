import json
import secrets
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.core.enums import OrderStatus, SignalDirection, SignalGrade
from app.core.models import Signal
from app.db.models import Tenant, User
from app.db.session import get_session
from app.execution.order_persistence import get_order_by_idempotency_key
from app.execution.signal_execution import execute_signal_for_user

router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])


class TradingViewAlertPayload(BaseModel):
    """The shape a TradingView "Webhook URL" alert's message body is expected to carry - a
    pre-formed trade signal, since the strategy logic already ran in Pine Script on TradingView's
    side; there are no OHLCV bars to re-analyze here. `alert_id` is optional but recommended
    (e.g. TradingView's `{{time}}` placeholder, or a UUID) - without it, a retried/duplicated
    delivery re-executes rather than replaying.
    """

    strategy_id: str
    symbol: str
    direction: SignalDirection
    entry: float
    stop_loss: float
    target1: float
    target2: Optional[float] = None
    alert_id: Optional[str] = None

    @field_validator("direction")
    @classmethod
    def _reject_no_trade(cls, value: SignalDirection) -> SignalDirection:
        if value == SignalDirection.NO_TRADE:
            raise ValueError("direction must be LONG or SHORT - an alert firing is itself a trade signal")
        return value


class WebhookExecutionResponse(BaseModel):
    executed: bool
    reasons: List[str]
    order_id: Optional[int] = None
    idempotent_replay: bool = False


class WebhookTokenResponse(BaseModel):
    webhook_token: str
    webhook_url: str


async def _get_tenant_owner(session: AsyncSession, tenant_id: int) -> User:
    """Webhook alerts have no logged-in caller to attribute an order to - the tenant's
    earliest-created user stands in as the acting user for audit/notification purposes. Every V1
    tenant has exactly one user (no invite flow yet - see the Multi-Tenancy Foundation notes on
    this same limitation), so this is unambiguous today.
    """
    user = await session.scalar(select(User).where(User.tenant_id == tenant_id).order_by(User.id.asc()))
    if user is None:
        raise HTTPException(status_code=500, detail="Tenant has no user to attribute this order to")
    return user


@router.post("/tradingview/{webhook_token}", response_model=WebhookExecutionResponse)
async def tradingview_webhook(
    webhook_token: str, payload: TradingViewAlertPayload, session: AsyncSession = Depends(get_session),
) -> WebhookExecutionResponse:
    """Runs an inbound TradingView alert through the exact same kill-switch -> risk engine ->
    paper broker -> notification pipeline a logged-in POST /api/strategies/{id}/paper-execute
    call uses (app/execution/signal_execution.py::execute_signal_for_user). Authenticated by
    `webhook_token` alone: TradingView's webhook alerts can't carry a JWT/OAuth header, so the
    token embedded in the URL (from GET /api/webhooks/tradingview/token) is the credential.
    """
    tenant = await session.scalar(select(Tenant).where(Tenant.webhook_token == webhook_token))
    if tenant is None:
        raise HTTPException(status_code=401, detail="Unknown or invalid webhook token")

    idempotency_key = f"tv:{payload.alert_id}" if payload.alert_id else None
    if idempotency_key:
        existing = await get_order_by_idempotency_key(session, tenant.id, idempotency_key)
        if existing is not None:
            return WebhookExecutionResponse(
                executed=existing.status in (OrderStatus.FILLED.value, OrderStatus.POSITION_OPEN.value),
                reasons=json.loads(existing.reasons_json), order_id=existing.id, idempotent_replay=True,
            )

    user = await _get_tenant_owner(session, tenant.id)

    risk_reward = None
    if payload.entry != payload.stop_loss:
        risk_reward = abs(payload.target1 - payload.entry) / abs(payload.entry - payload.stop_loss)

    signal = Signal(
        symbol=payload.symbol, strategy_id=payload.strategy_id,
        strategy_name=f"TradingView webhook: {payload.strategy_id}", direction=payload.direction,
        timestamp=datetime.now(timezone.utc), entry=payload.entry, stop_loss=payload.stop_loss,
        target1=payload.target1, target2=payload.target2, risk_reward=risk_reward,
        score=100, grade=SignalGrade.A1, reasons=["TradingView webhook alert"], timeframe_combo="webhook",
    )

    result, order = await execute_signal_for_user(
        session, user, mode="PAPER", strategy_id=payload.strategy_id, signal=signal,
        idempotency_key=idempotency_key,
    )
    return WebhookExecutionResponse(executed=result.executed, reasons=result.reasons, order_id=order.id)


@router.get("/tradingview/token", response_model=WebhookTokenResponse)
async def get_webhook_token(
    user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session),
) -> WebhookTokenResponse:
    """This tenant's TradingView webhook URL - paste it into TradingView's alert "Webhook URL"
    field, with a JSON message body matching TradingViewAlertPayload."""
    tenant = await session.get(Tenant, user.tenant_id)
    return WebhookTokenResponse(
        webhook_token=tenant.webhook_token, webhook_url=f"/api/webhooks/tradingview/{tenant.webhook_token}",
    )


@router.post("/tradingview/token/rotate", response_model=WebhookTokenResponse)
async def rotate_webhook_token(
    user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session),
) -> WebhookTokenResponse:
    """Invalidates the old webhook URL and issues a new one - use if the old URL ever leaks
    (it's the sole credential protecting this endpoint)."""
    tenant = await session.get(Tenant, user.tenant_id)
    tenant.webhook_token = secrets.token_urlsafe(24)
    await session.commit()
    return WebhookTokenResponse(
        webhook_token=tenant.webhook_token, webhook_url=f"/api/webhooks/tradingview/{tenant.webhook_token}",
    )
