import hashlib
import json
import secrets
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.log import write_audit_log
from app.auth.dependencies import get_current_user, require_role
from app.core.enums import UserRole, OrderStatus, SignalDirection, SignalGrade
from app.core.models import Signal
from app.db.models import Tenant, User
from app.db.session import get_session
from app.execution.order_persistence import get_order_by_idempotency_key
from app.billing.service import meter
from app.execution.signal_execution import execute_signal_for_user
from app.secrets_store.envelope import ensure_tenant_key

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


def hash_webhook_token(token: str) -> str:
    """P0.3 / S13: what `tenants.webhook_token_hash` stores (SHA-256 hex of the URL token)."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


async def resolve_tenant_by_webhook_token(session: AsyncSession, token: str, *, accept_hash: bool = False) -> Optional[Tenant]:
    """P0.3 / S13: the URL token is matched by hash; a pre-P0.3 organisation still carrying the plaintext matches
    on it once and gets its hash filled in. With `accept_hash` the stored hash itself is also accepted as the
    path identifier (Telegram inbound: the secret header authenticates, the path only names the organisation)."""
    tenant = await session.scalar(select(Tenant).where(Tenant.webhook_token_hash == hash_webhook_token(token)))
    if tenant is None and accept_hash:
        tenant = await session.scalar(select(Tenant).where(Tenant.webhook_token_hash == token))
    if tenant is None:
        tenant = await session.scalar(select(Tenant).where(Tenant.webhook_token == token))
        if tenant is not None:
            tenant.webhook_token_hash = hash_webhook_token(token)
    return tenant


class WebhookTokenResponse(BaseModel):
    # None once the organisation's token exists only as a hash: rotate to get a new URL (shown once).
    webhook_token: Optional[str] = None
    webhook_url: Optional[str] = None
    configured: bool = True


async def _get_tenant_owner(session: AsyncSession, tenant_id: int) -> User:
    """Webhook alerts have no logged-in caller to attribute an order to - the tenant's OWNER
    (earliest one if several; earliest active user if none) stands in as the acting user for
    audit/notification purposes.
    """
    user = await session.scalar(
        select(User).where(User.tenant_id == tenant_id, User.is_active.is_(True), User.role == UserRole.OWNER.value)
        .order_by(User.id.asc())
    )
    if user is None:
        user = await session.scalar(
            select(User).where(User.tenant_id == tenant_id, User.is_active.is_(True)).order_by(User.id.asc())
        )
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
    tenant = await resolve_tenant_by_webhook_token(session, webhook_token)
    if tenant is None:
        raise HTTPException(status_code=401, detail="Unknown or invalid webhook token")
    await ensure_tenant_key(session, tenant.id)  # Phase N1

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
    await meter(session, user.tenant_id, "webhook_event", 1, source="tradingview", metadata={"order_id": order.id})
    return WebhookExecutionResponse(executed=result.executed, reasons=result.reasons, order_id=order.id)


@router.get("/tradingview/token", response_model=WebhookTokenResponse)
async def get_webhook_token(
    user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session),
) -> WebhookTokenResponse:
    """This tenant's TradingView webhook URL - paste it into TradingView's alert "Webhook URL"
    field, with a JSON message body matching TradingViewAlertPayload. P0.3 / S13: the token is stored hashed,
    so the URL is shown here only while the organisation still carries its pre-P0.3 plaintext; otherwise rotate
    to get a fresh URL, shown once."""
    tenant = await session.get(Tenant, user.tenant_id)
    if tenant.webhook_token:
        return WebhookTokenResponse(webhook_token=tenant.webhook_token, webhook_url=f"/api/webhooks/tradingview/{tenant.webhook_token}")
    return WebhookTokenResponse(webhook_token=None, webhook_url=None, configured=bool(tenant.webhook_token_hash))


@router.post("/tradingview/token/rotate", response_model=WebhookTokenResponse)
async def rotate_webhook_token(
    user: User = Depends(require_role(UserRole.OWNER, UserRole.SUPER_ADMIN)), session: AsyncSession = Depends(get_session),
) -> WebhookTokenResponse:
    """Invalidates the old webhook URL and issues a new one - use if the old URL ever leaks
    (it's the sole credential protecting this endpoint). Owner only; the new URL is shown once."""
    tenant = await session.get(Tenant, user.tenant_id)
    token = secrets.token_urlsafe(24)
    tenant.webhook_token = None
    tenant.webhook_token_hash = hash_webhook_token(token)
    await write_audit_log(session, user.tenant_id, user.id, "webhook_token_rotated")
    await session.commit()
    return WebhookTokenResponse(webhook_token=token, webhook_url=f"/api/webhooks/tradingview/{token}", configured=True)
