"""Deployments API: what a tenant runs autonomously, on which symbol, in which mode.

A deployment is the instruction the worker (app/workers/trading_worker.py) acts on. Creating one
is deliberately strict - LIVE requires a stored broker whose token is proven VALID right now,
PAPER still needs *some* stored broker for market data, and the base timeframe must be able to
build every timeframe the strategy consumes - because the worker acting on a half-configured
deployment would only discover the problem at 09:15 with real money on the line.
"""
import logging
import re
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.log import write_audit_log
from app.auth.dependencies import get_current_user, require_role
from app.brokers.registry import available_brokers
from app.brokers.token_lifecycle import get_credential_record, token_is_usable
from app.core.enums import DeploymentStatus, ExecutionMode, UserRole
from app.custom_strategies.resolver import resolve_strategy
from app.db.models import BrokerCredentialRecord, StrategyDeploymentRecord, TradeRecord, User
from app.db.session import get_session

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/deployments", tags=["deployments"])

# Roles allowed to change what the platform trades. SUPPORT is read-only by design; SUPER_ADMIN
# always passes require_role.
can_manage = require_role(UserRole.USER, UserRole.STRATEGY_CREATOR)

SUPPORTED_BASE_TIMEFRAMES = ("1min", "3min", "5min", "15min", "30min", "60min")

_TF_RE = re.compile(r"^(\d+)(min|h|d)$")


def _timeframe_minutes(label: str) -> Optional[int]:
    match = _TF_RE.match(label)
    if not match:
        return None
    value, unit = int(match.group(1)), match.group(2)
    return value * {"min": 1, "h": 60, "d": 24 * 60}[unit]


class DeploymentCreateRequest(BaseModel):
    strategy_id: str = Field(min_length=1, max_length=100)
    symbol: str = Field(min_length=1, max_length=50)
    exchange: str = Field(default="NSE", min_length=1, max_length=20)
    timeframe: str = Field(default="1min", description="Base candle interval fetched from the broker")
    mode: ExecutionMode = ExecutionMode.PAPER
    broker_name: Optional[str] = None


class DeploymentActionRequest(BaseModel):
    reason: str = ""


class DeploymentResponse(BaseModel):
    id: int
    strategy_id: str
    symbol: str
    exchange: str
    timeframe: str
    mode: str
    broker_name: Optional[str]
    status: str
    pause_reason: Optional[str]
    last_evaluated_at: Optional[str]
    last_signal_at: Optional[str]
    last_error: Optional[str]
    consecutive_failures: int
    open_positions: int
    created_by: Optional[int]
    created_at: str
    updated_at: str

    @classmethod
    def from_record(cls, record: StrategyDeploymentRecord, open_positions: int = 0) -> "DeploymentResponse":
        iso = lambda value: value.isoformat() if value else None  # noqa: E731
        return cls(
            id=record.id, strategy_id=record.strategy_id, symbol=record.symbol, exchange=record.exchange,
            timeframe=record.timeframe, mode=record.mode, broker_name=record.broker_name, status=record.status,
            pause_reason=record.pause_reason, last_evaluated_at=iso(record.last_evaluated_at),
            last_signal_at=iso(record.last_signal_at), last_error=record.last_error,
            consecutive_failures=record.consecutive_failures or 0, open_positions=open_positions,
            created_by=record.created_by, created_at=iso(record.created_at) or "", updated_at=iso(record.updated_at) or "",
        )


async def _open_position_counts(session: AsyncSession, tenant_id: int) -> dict:
    rows = await session.execute(
        select(TradeRecord.deployment_id, func.count()).where(
            TradeRecord.tenant_id == tenant_id, TradeRecord.exit_time.is_(None), TradeRecord.deployment_id.is_not(None),
        ).group_by(TradeRecord.deployment_id)
    )
    return {dep_id: count for dep_id, count in rows}


async def _get_owned_or_404(deployment_id: int, user: User, session: AsyncSession) -> StrategyDeploymentRecord:
    record = await session.get(StrategyDeploymentRecord, deployment_id)
    if record is None or record.tenant_id != user.tenant_id:
        raise HTTPException(status_code=404, detail="Unknown deployment")
    return record


async def _require_usable_broker(session: AsyncSession, tenant_id: int, broker_name: str) -> BrokerCredentialRecord:
    record = await get_credential_record(session, tenant_id, broker_name)
    if record is None:
        raise HTTPException(status_code=409, detail=f"No stored credentials for broker '{broker_name}' - add them in Settings first")
    if not token_is_usable(record):
        raise HTTPException(
            status_code=409,
            detail=f"{broker_name} session is {record.token_status} - log in to {broker_name} from Settings before going LIVE",
        )
    return record


@router.post("", response_model=DeploymentResponse, status_code=status.HTTP_201_CREATED)
async def create_deployment(
    request: DeploymentCreateRequest, user: User = Depends(can_manage), session: AsyncSession = Depends(get_session),
) -> DeploymentResponse:
    try:
        strategy = await resolve_strategy(request.strategy_id, user, session)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    if request.timeframe not in SUPPORTED_BASE_TIMEFRAMES:
        raise HTTPException(status_code=400, detail=f"timeframe must be one of {list(SUPPORTED_BASE_TIMEFRAMES)}")
    base_minutes = _timeframe_minutes(request.timeframe)
    for tf in strategy.timeframes:
        tf_minutes = _timeframe_minutes(tf)
        if tf_minutes is None or base_minutes is None or tf_minutes < base_minutes or tf_minutes % base_minutes != 0:
            raise HTTPException(
                status_code=400,
                detail=f"Base timeframe {request.timeframe} cannot build the strategy's {tf} candles - "
                       f"pick a base that divides every strategy timeframe ({', '.join(strategy.timeframes)})",
            )

    broker_name = request.broker_name
    if broker_name is not None and broker_name not in available_brokers():
        raise HTTPException(status_code=404, detail=f"Unknown broker '{broker_name}'")

    if request.mode == ExecutionMode.LIVE:
        if not broker_name:
            raise HTTPException(status_code=400, detail="LIVE deployments must name the broker to trade through")
        await _require_usable_broker(session, user.tenant_id, broker_name)
    else:
        # PAPER still needs a market-data source. Fall back to the tenant's only stored broker.
        if broker_name is None:
            stored = list(await session.scalars(
                select(BrokerCredentialRecord).where(BrokerCredentialRecord.tenant_id == user.tenant_id)
            ))
            if not stored:
                raise HTTPException(
                    status_code=409,
                    detail="Store broker credentials in Settings first - even paper trading needs a broker session for live candles",
                )
            if len(stored) > 1:
                raise HTTPException(status_code=400, detail="Several brokers stored - name the one to use for market data")
            broker_name = stored[0].broker_name
        elif await get_credential_record(session, user.tenant_id, broker_name) is None:
            raise HTTPException(status_code=409, detail=f"No stored credentials for broker '{broker_name}'")

    record = StrategyDeploymentRecord(
        tenant_id=user.tenant_id, strategy_id=request.strategy_id, symbol=request.symbol.upper().strip(),
        exchange=request.exchange.upper(), timeframe=request.timeframe, mode=request.mode.value,
        broker_name=broker_name, status=DeploymentStatus.ACTIVE.value, created_by=user.id,
    )
    session.add(record)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=409, detail="A deployment of this strategy on this symbol in this mode already exists",
        ) from exc
    await write_audit_log(
        session, user.tenant_id, user.id, "deployment_created",
        f"#{record.id} {record.strategy_id} {record.symbol} {record.mode} via {broker_name}",
    )
    await session.commit()
    await session.refresh(record)
    logger.info("Deployment %s created: %s %s %s", record.id, record.strategy_id, record.symbol, record.mode)
    return DeploymentResponse.from_record(record)


@router.get("", response_model=List[DeploymentResponse])
async def list_deployments(
    user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session),
    include_stopped: bool = False,
) -> List[DeploymentResponse]:
    query = select(StrategyDeploymentRecord).where(StrategyDeploymentRecord.tenant_id == user.tenant_id)
    if not include_stopped:
        query = query.where(StrategyDeploymentRecord.status != DeploymentStatus.STOPPED.value)
    rows = await session.scalars(query.order_by(StrategyDeploymentRecord.created_at.desc()))
    counts = await _open_position_counts(session, user.tenant_id)
    return [DeploymentResponse.from_record(r, counts.get(r.id, 0)) for r in rows]


@router.get("/{deployment_id}", response_model=DeploymentResponse)
async def get_deployment(
    deployment_id: int, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session),
) -> DeploymentResponse:
    record = await _get_owned_or_404(deployment_id, user, session)
    counts = await _open_position_counts(session, user.tenant_id)
    return DeploymentResponse.from_record(record, counts.get(record.id, 0))


@router.post("/{deployment_id}/pause", response_model=DeploymentResponse)
async def pause_deployment(
    deployment_id: int, request: DeploymentActionRequest = DeploymentActionRequest(),
    user: User = Depends(can_manage), session: AsyncSession = Depends(get_session),
) -> DeploymentResponse:
    """No new entries; open positions opened by it stay monitored (the position sweep is
    tenant-wide, not deployment-scoped) and still exit at their stop/target."""
    record = await _get_owned_or_404(deployment_id, user, session)
    if record.status == DeploymentStatus.STOPPED.value:
        raise HTTPException(status_code=409, detail="Deployment is stopped")
    record.status = DeploymentStatus.PAUSED.value
    record.pause_reason = f"Paused by user: {request.reason}".rstrip(": ")
    await write_audit_log(session, user.tenant_id, user.id, "deployment_paused", f"#{record.id} {request.reason}")
    await session.commit()
    await session.refresh(record)
    return DeploymentResponse.from_record(record)


@router.post("/{deployment_id}/resume", response_model=DeploymentResponse)
async def resume_deployment(
    deployment_id: int, user: User = Depends(can_manage), session: AsyncSession = Depends(get_session),
) -> DeploymentResponse:
    record = await _get_owned_or_404(deployment_id, user, session)
    if record.status == DeploymentStatus.STOPPED.value:
        raise HTTPException(status_code=409, detail="A stopped deployment cannot be resumed - create a new one")
    if record.mode == ExecutionMode.LIVE.value and record.broker_name:
        await _require_usable_broker(session, user.tenant_id, record.broker_name)
    record.status = DeploymentStatus.ACTIVE.value
    record.pause_reason = None
    record.consecutive_failures = 0
    record.last_error = None
    await write_audit_log(session, user.tenant_id, user.id, "deployment_resumed", f"#{record.id}")
    await session.commit()
    await session.refresh(record)
    return DeploymentResponse.from_record(record)


@router.post("/{deployment_id}/stop", response_model=DeploymentResponse)
async def stop_deployment(
    deployment_id: int, request: DeploymentActionRequest = DeploymentActionRequest(),
    user: User = Depends(can_manage), session: AsyncSession = Depends(get_session),
) -> DeploymentResponse:
    """Terminal. Positions it opened are left to the monitor (or emergency exit) - stopping a
    deployment is "stop taking new trades", not "dump everything at market"."""
    record = await _get_owned_or_404(deployment_id, user, session)
    record.status = DeploymentStatus.STOPPED.value
    record.pause_reason = f"Stopped by user: {request.reason}".rstrip(": ")
    await write_audit_log(session, user.tenant_id, user.id, "deployment_stopped", f"#{record.id} {request.reason}")
    await session.commit()
    await session.refresh(record)
    return DeploymentResponse.from_record(record)


@router.delete("/{deployment_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_deployment(
    deployment_id: int, user: User = Depends(can_manage), session: AsyncSession = Depends(get_session),
) -> None:
    record = await _get_owned_or_404(deployment_id, user, session)
    if record.status != DeploymentStatus.STOPPED.value:
        raise HTTPException(status_code=409, detail="Stop the deployment before deleting it")
    await session.delete(record)
    await write_audit_log(session, user.tenant_id, user.id, "deployment_deleted", f"#{deployment_id}")
    await session.commit()
