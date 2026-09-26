"""Phase K3: the public API surface (`/api/public/v1/*`) and key management.

Kept apart from the app's own `/api/v1/*` (section 45 / V3.11): a leaked public key can reach
only what its scopes name, never the console endpoints (credentials, team, kill switches,
deployments). Every endpoint is tenant-scoped from the key, rate-limited per key, metered
against the plan, and answers with plain JSON. `GET /api/public/v1/docs` is the developer
portal's machine-readable page (scopes, endpoints, limits, error codes, idempotency).
"""
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.log import write_audit_log
from app.auth.dependencies import get_current_user, require_owner
from app.core.enums import SignalDirection, SignalGrade
from app.core.models import Signal
from app.db.models import ApiKeyRecord, BacktestRunRecord, OrderRecord, RiskEventRecord, RiskLimitRecord, SignalHistoryRecord, TradeRecord, User
from app.db.session import get_session
from app.instruments import master as instrument_master
from app.plans.limits import feature_allowed, limits, require_feature
from app.plans.registry import get_plan
from app.public_api.keys import SCOPES, ApiPrincipal, api_key_auth, generate_key, parse_scopes
from app.strategy_engine.registry import registry
from app.db.models import Tenant

keys_router = APIRouter(prefix="/api/api-keys", tags=["public-api"])
public_router = APIRouter(prefix="/api/public/v1", tags=["public-api"])

ERROR_CODES = {401: "missing/invalid/expired/revoked key", 402: "plan does not include the public API", 403: "key lacks the scope",
               404: "no such resource in this organisation", 409: "conflict (e.g. broker session not usable)", 422: "validation error",
               429: "per-key per-minute limit or the plan's daily allowance"}


class ApiKeyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    scopes: List[str] = Field(min_length=1)
    rate_limit_per_minute: int = Field(default=60, ge=1, le=600)
    expires_in_days: Optional[int] = Field(default=None, ge=1, le=365)


def _key_dict(k: ApiKeyRecord) -> dict:
    iso = lambda v: v.isoformat() if v else None  # noqa: E731
    return {"id": k.id, "name": k.name, "key_prefix": k.key_prefix, "scopes": parse_scopes(k.scopes), "rate_limit_per_minute": k.rate_limit_per_minute,
            "expires_at": iso(k.expires_at), "revoked_at": iso(k.revoked_at), "last_used_at": iso(k.last_used_at), "created_at": iso(k.created_at)}


@keys_router.get("")
async def list_keys(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> List[dict]:
    rows = await session.scalars(select(ApiKeyRecord).where(ApiKeyRecord.tenant_id == user.tenant_id).order_by(ApiKeyRecord.id.desc()))
    return [_key_dict(k) for k in rows]


@keys_router.get("/scopes")
async def scopes() -> dict:
    return SCOPES


@keys_router.post("", status_code=201)
async def create_key(body: ApiKeyCreate, user: User = Depends(require_owner), session: AsyncSession = Depends(get_session)) -> dict:
    tenant = await session.get(Tenant, user.tenant_id)
    require_feature(tenant, "public_api", "The public API")
    unknown = [s for s in body.scopes if s not in SCOPES]
    if unknown:
        raise HTTPException(status_code=400, detail=f"Unknown scopes {unknown}; valid: {list(SCOPES)}")
    key, prefix, key_hash = generate_key()
    record = ApiKeyRecord(tenant_id=user.tenant_id, user_id=user.id, name=body.name.strip(), key_prefix=prefix, key_hash=key_hash,
                          scopes=",".join(sorted(set(body.scopes))), rate_limit_per_minute=body.rate_limit_per_minute,
                          expires_at=datetime.now(timezone.utc) + timedelta(days=body.expires_in_days) if body.expires_in_days else None)
    session.add(record)
    await write_audit_log(session, user.tenant_id, user.id, "api_key_created", f"{record.name} [{record.scopes}]")
    await session.commit()
    await session.refresh(record)
    return {**_key_dict(record), "key": key, "note": "Store this key now - it is not shown again."}


@keys_router.delete("/{key_id}", status_code=204)
async def revoke_key(key_id: int, user: User = Depends(require_owner), session: AsyncSession = Depends(get_session)) -> None:
    record = await session.get(ApiKeyRecord, key_id)
    if record is None or record.tenant_id != user.tenant_id:
        raise HTTPException(status_code=404, detail="No such key")
    record.revoked_at = datetime.now(timezone.utc)
    await write_audit_log(session, user.tenant_id, user.id, "api_key_revoked", record.name)
    await session.commit()


# --- public surface -----------------------------------------------------------------------

@public_router.get("/docs")
async def docs() -> dict:
    return {
        "version": "v1", "auth": "X-API-Key header; keys are created by an OWNER under Settings > API keys and shown once",
        "base_url": "/api/public/v1", "scopes": SCOPES, "error_codes": ERROR_CODES,
        "rate_limits": "per key per minute (set at creation, max 600) plus the plan's daily allowance",
        "idempotency": "POST /signals takes idempotency_key; the same key replays the original outcome instead of re-executing",
        "endpoints": [
            {"method": "GET", "path": "/account", "scope": "read:account"},
            {"method": "GET", "path": "/instruments/search?q=", "scope": "read:instruments"},
            {"method": "GET", "path": "/strategies", "scope": "read:strategies"},
            {"method": "GET", "path": "/signals?limit=", "scope": "read:signals"},
            {"method": "POST", "path": "/signals", "scope": "write:signals", "body": "strategy_id, symbol, direction, entry, stop_loss, target1, target2?, mode=PAPER, idempotency_key?"},
            {"method": "GET", "path": "/orders?limit=", "scope": "read:orders"},
            {"method": "GET", "path": "/positions", "scope": "read:positions"},
            {"method": "GET", "path": "/trades?limit=", "scope": "read:positions"},
            {"method": "GET", "path": "/backtests?limit=", "scope": "read:backtests"},
            {"method": "GET", "path": "/risk/limits", "scope": "read:risk"},
            {"method": "GET", "path": "/risk/events?limit=", "scope": "read:risk"},
        ],
        "webhooks": "Configure a WEBHOOK alert channel under Settings to receive HMAC-signed notifications (X-ATP-Signature = hex(HMAC-SHA256(secret, body))).",
        "changelog": [{"version": "v1", "date": "2026-09-26", "notes": "Initial public surface (Phase K3)."}],
        "disclaimer": "Signals, scores and backtests are decision support, not investment advice. You remain responsible for every order placed from your account.",
    }


@public_router.get("/account")
async def account(p: ApiPrincipal = Depends(api_key_auth("read:account")), session: AsyncSession = Depends(get_session)) -> dict:
    from app.billing.service import usage_summary
    plan = get_plan(p.tenant.plan)
    return {"tenant_id": p.tenant.id, "organisation": p.tenant.name, "plan": plan.id, "status": p.tenant.status,
            "limits": limits(plan), "metered_30d": await usage_summary(session, p.tenant.id, days=30), "key": p.key.name}


@public_router.get("/instruments/search")
async def instruments(q: str = Query(min_length=1, max_length=50), limit: int = Query(default=20, ge=1, le=100),
                      p: ApiPrincipal = Depends(api_key_auth("read:instruments")), session: AsyncSession = Depends(get_session)) -> List[dict]:
    rows = await instrument_master.search_instruments(session, q, limit=limit)
    return [instrument_master.as_dict(r) for r in rows]


@public_router.get("/strategies")
async def strategies(p: ApiPrincipal = Depends(api_key_auth("read:strategies")), session: AsyncSession = Depends(get_session)) -> List[dict]:
    from app.custom_strategies.resolver import custom_strategy_info
    from app.db.models import CustomStrategyRecord
    inbuilt = [{"id": s.id, "name": s.name, "timeframes": list(s.timeframes), "kind": "inbuilt"} for s in registry.list_all()]
    custom = await session.scalars(select(CustomStrategyRecord).where(CustomStrategyRecord.tenant_id == p.tenant.id))
    for record in custom:
        info = custom_strategy_info(record)
        inbuilt.append({"id": info.id, "name": info.name, "timeframes": list(info.timeframes), "kind": "custom"})
    return inbuilt


@public_router.get("/signals")
async def signals(limit: int = Query(default=50, ge=1, le=500), p: ApiPrincipal = Depends(api_key_auth("read:signals")),
                  session: AsyncSession = Depends(get_session)) -> List[dict]:
    rows = await session.scalars(select(SignalHistoryRecord).where(SignalHistoryRecord.tenant_id == p.tenant.id)
                                 .order_by(SignalHistoryRecord.id.desc()).limit(limit))
    return [{"id": r.id, "strategy_id": r.strategy_id, "symbol": r.symbol, "direction": r.direction, "signal_time": r.signal_time.isoformat(),
             "entry": r.entry, "stop_loss": r.stop_loss, "target1": r.target1, "target2": r.target2, "score": r.score, "grade": r.grade} for r in rows]


class PublicSignal(BaseModel):
    strategy_id: str = Field(min_length=1, max_length=100)
    symbol: str = Field(min_length=1, max_length=50)
    direction: SignalDirection
    entry: float = Field(gt=0)
    stop_loss: float = Field(gt=0)
    target1: float = Field(gt=0)
    target2: Optional[float] = Field(default=None, gt=0)
    mode: str = Field(default="PAPER", pattern=r"^(PAPER|LIVE)$")
    idempotency_key: Optional[str] = Field(default=None, min_length=8, max_length=100)


@public_router.post("/signals", status_code=201)
async def submit_signal(body: PublicSignal, p: ApiPrincipal = Depends(api_key_auth("write:signals")),
                        session: AsyncSession = Depends(get_session)) -> dict:
    from app.execution.signal_execution import execute_signal_for_user
    if body.direction == SignalDirection.NO_TRADE:
        raise HTTPException(status_code=422, detail="direction must be LONG or SHORT")
    if body.mode == "LIVE":
        raise HTTPException(status_code=409, detail="LIVE execution through the public API needs a broker session bound to the key - use PAPER, or deploy from the console")
    risk = abs(body.entry - body.stop_loss)
    rr = round(abs(body.target1 - body.entry) / risk, 2) if risk else None
    signal = Signal(symbol=body.symbol.upper(), strategy_id=body.strategy_id, strategy_name=body.strategy_id, direction=body.direction,
                    timestamp=datetime.now(timezone.utc), entry=body.entry, stop_loss=body.stop_loss, target1=body.target1, target2=body.target2,
                    risk_reward=rr, score=50, grade=SignalGrade.VALID, reasons=[f"public API key {p.key.name}"], timeframe_combo="external")
    result, order = await execute_signal_for_user(session, p.user, mode=body.mode, strategy_id=body.strategy_id, signal=signal,
                                                  idempotency_key=f"api:{p.key.id}:{body.idempotency_key}" if body.idempotency_key else None)
    return {"order_id": order.id, "status": order.status, "executed": result.executed, "reasons": result.reasons, "trade_id": order.trade_id}


@public_router.get("/orders")
async def orders(limit: int = Query(default=50, ge=1, le=500), p: ApiPrincipal = Depends(api_key_auth("read:orders")),
                 session: AsyncSession = Depends(get_session)) -> List[dict]:
    rows = await session.scalars(select(OrderRecord).where(OrderRecord.tenant_id == p.tenant.id).order_by(OrderRecord.id.desc()).limit(limit))
    return [{"id": r.id, "status": r.status, "mode": r.mode, "symbol": r.symbol, "direction": r.direction, "strategy_id": r.strategy_id,
             "quantity": r.quantity, "broker_order_id": r.broker_order_id, "trade_id": r.trade_id, "created_at": r.created_at.isoformat() if r.created_at else None}
            for r in rows]


def _trade(r: TradeRecord) -> dict:
    return {"id": r.id, "mode": r.mode, "symbol": r.symbol, "strategy_id": r.strategy_id, "direction": r.direction, "entry_time": r.entry_time.isoformat(),
            "entry_price": r.entry_price, "quantity": r.quantity, "stop_loss": r.stop_loss, "target1": r.target1, "target2": r.target2,
            "exit_time": r.exit_time.isoformat() if r.exit_time else None, "exit_price": r.exit_price, "exit_reason": r.exit_reason, "pnl": r.pnl,
            "charges": r.charges, "instrument_kind": r.instrument_kind, "leg_group_id": r.leg_group_id}


@public_router.get("/positions")
async def positions(p: ApiPrincipal = Depends(api_key_auth("read:positions")), session: AsyncSession = Depends(get_session)) -> List[dict]:
    rows = await session.scalars(select(TradeRecord).where(TradeRecord.tenant_id == p.tenant.id, TradeRecord.exit_time.is_(None)).order_by(TradeRecord.id))
    return [_trade(r) for r in rows]


@public_router.get("/trades")
async def trades(limit: int = Query(default=100, ge=1, le=1000), p: ApiPrincipal = Depends(api_key_auth("read:positions")),
                 session: AsyncSession = Depends(get_session)) -> List[dict]:
    rows = await session.scalars(select(TradeRecord).where(TradeRecord.tenant_id == p.tenant.id).order_by(TradeRecord.id.desc()).limit(limit))
    return [_trade(r) for r in rows]


@public_router.get("/backtests")
async def backtests(limit: int = Query(default=50, ge=1, le=500), p: ApiPrincipal = Depends(api_key_auth("read:backtests")),
                    session: AsyncSession = Depends(get_session)) -> List[dict]:
    from app.backtest.routes import _run_summary
    rows = await session.scalars(select(BacktestRunRecord).where(BacktestRunRecord.tenant_id == p.tenant.id).order_by(BacktestRunRecord.id.desc()).limit(limit))
    return [_run_summary(r) for r in rows]


@public_router.get("/risk/limits")
async def risk_limits(p: ApiPrincipal = Depends(api_key_auth("read:risk")), session: AsyncSession = Depends(get_session)) -> List[dict]:
    rows = await session.scalars(select(RiskLimitRecord).where((RiskLimitRecord.tenant_id == p.tenant.id) | (RiskLimitRecord.scope == "GLOBAL")))
    return [{"id": r.id, "scope": r.scope, "scope_id": r.scope_id, "limit_type": r.limit_type, "limit_value": r.limit_value, "enabled": r.enabled} for r in rows]


@public_router.get("/risk/events")
async def risk_events(limit: int = Query(default=100, ge=1, le=1000), p: ApiPrincipal = Depends(api_key_auth("read:risk")),
                      session: AsyncSession = Depends(get_session)) -> List[dict]:
    rows = await session.scalars(select(RiskEventRecord).where(RiskEventRecord.tenant_id == p.tenant.id).order_by(RiskEventRecord.id.desc()).limit(limit))
    return [{"id": r.id, "created_at": r.created_at.isoformat() if r.created_at else None, "rule_type": r.rule_type, "scope": r.scope, "status": r.status,
             "action": r.action, "current_value": r.current_value, "limit_value": r.limit_value, "reason": r.reason, "order_id": r.order_id} for r in rows]
