"""M86 W1 / W4: the chart reader's API. Behind the `chart_reader` flag: while it is off for the caller's organisation every
member route answers the platform's standard 503 with `X-Feature-Disabled: chart_reader`.

- `GET  /api/chart-reader/strategies`      the reader's labels (Abhi's method first), the default, the model and effort,
                                           the estimate per read, whether it is installed and connected, and the
                                           caller's own daily cap and today's spend
- `POST /api/chart-reader/read`            {symbol?, strategy, source: "lake" | "upload", item_id?} -> the reader's result,
                                           unchanged, plus {cached, estimate_usd, cost_usd, label, source, charts,
                                           pair_attached}; 503 with one plain line when the reader is not installed or
                                           not connected
- `GET/PUT /api/admin/chart-reader/settings` SUPER_ADMIN (MFA): model, effort, the API key's environment names, timeframes,
                                           the pair, cache hours, prices and the estimate
Reads only the caller's own rows (tenant AND user). Nothing here places, proposes or changes an order (ADR-0006).
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user, require_mfa_session, require_role
from app.chart_reader import FLAG, loader, service
from app.chart_reader import settings as settings_mod
from app.core.config import CACHE_MAX_TTL_SECONDS
from app.db.models import Tenant, User
from app.db.session import get_session
from app.platform.controls import require_flag

router = APIRouter(prefix="/api/chart-reader", tags=["chart-reader"])
admin_router = APIRouter(prefix="/api/admin/chart-reader", tags=["admin"], dependencies=[Depends(require_role()), Depends(require_mfa_session)])


class ReadBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbol: str | None = Field(default=None, max_length=40)
    strategy: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.\-]{1,64}$")
    source: Literal["lake", "upload"] = "lake"
    item_id: int | None = Field(default=None, ge=1)


class SettingsBody(BaseModel):
    values: dict[str, Any] = Field(default_factory=dict, max_length=len(settings_mod.DEFAULTS))


async def _gate(session: AsyncSession, user: User) -> dict[str, Any]:
    await require_flag(session, FLAG, user.tenant_id)
    return await settings_mod.load(session)


async def _tenant(session: AsyncSession, user: User) -> Tenant:
    tenant = await session.get(Tenant, user.tenant_id)
    if tenant is None:
        raise HTTPException(status_code=404, detail="Unknown organisation")
    return tenant


@router.get("/strategies")
async def strategies(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    settings = await _gate(session, user)
    reader = loader.load()
    connected = service.api_key(settings) is not None
    charts = len(settings["timeframes"])
    now = datetime.now(UTC)
    out: dict[str, Any] = {
        "available": reader is not None and connected,
        "installed": reader is not None,
        "connected": connected,
        "message": None if reader is not None and connected else (loader.NOT_INSTALLED if reader is None else service.NOT_CONNECTED),
        "default": reader.ABHI if reader is not None else None,
        "strategies": service.strategy_labels(reader) if reader is not None else [],
        "model": settings["model"], "effort": settings["effort"],
        "timeframes": list(settings["timeframes"]),
        "estimate_usd": service.estimate_usd(settings, charts),
        "estimate_usd_two_chart": service.estimate_usd(settings, charts + 1),
        "estimate_usd_upload": service.estimate_usd(settings, 1),
        "labels": {"lake": service.LABEL_LAKE, "upload": service.LABEL_UPLOAD},
        "daily_cap_usd": await service.own_cap_usd(session, user),
        "spent_today_usd": round(await service.spent_today_usd(session, user, now), 6),
    }
    return out


@router.post("/read")
async def read(body: ReadBody, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    settings = await _gate(session, user)
    tenant = await _tenant(session, user)
    return await service.read(session, user, tenant, settings, symbol=body.symbol, strategy=body.strategy, source=body.source,
                              item_id=body.item_id)


def _admin_view(values: dict[str, Any]) -> dict[str, Any]:
    reader = loader.load()
    return {"settings": values, "defaults": settings_mod.DEFAULTS, "key_connected": service.api_key(values) is not None,
            "installed": reader is not None, "reader_version": loader.version() or None,
            "reason": None if reader is not None else loader.reason(),
            "cache_seconds_effective": min(int(float(values["cache_hours"]) * 3600), CACHE_MAX_TTL_SECONDS)}


@admin_router.get("/settings")
async def get_settings(session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    return _admin_view(await settings_mod.load(session))


@admin_router.put("/settings")
async def put_settings(body: SettingsBody, user: User = Depends(require_role()), session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    try:
        return _admin_view(await settings_mod.save(session, user, body.values))
    except settings_mod.SettingsError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
