"""M72 PR-1b: the caller's own use of the platform AI key (Settings › AI). Behind `ai_platform_key` (needs `ui_v2`): 404 while
it is off. Only the caller's own numbers; the key itself, the provider and other users' spend never leave the server. The
user's own lower daily limit is saved through `PUT /api/me/prefs/ai_usage` (app/me/prefs.py)."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import platform_key
from app.auth.dependencies import get_current_user
from app.db.models import Tenant, User
from app.db.session import get_session

router = APIRouter(prefix="/api/ai/platform", tags=["ai"])


@router.get("/usage")
async def my_platform_usage(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    tenant_id, user_id = int(user.tenant_id), int(user.id)
    if not await platform_key.platform_on(session, tenant_id):
        raise HTTPException(status_code=404, detail="Not found")
    tenant = await session.get(Tenant, tenant_id)
    if tenant is None:
        raise HTTPException(status_code=404, detail="Organisation not found")
    from app.ai.resolve import org_has_own_key
    own_org_key = await org_has_own_key(session, tenant_id)
    now = await platform_key.state(session, tenant, user_id)
    ready = platform_key.configured_provider() is not None and not own_org_key
    reason = now["reason"] if ready else None
    return {"in_use": ready, "available": ready and reason is None, "reason": reason,
            "message": platform_key.MESSAGES.get(reason) if reason else None,
            "today_inr": now["today_inr"], "cap_inr": now["cap_inr"], "operator_cap_inr": now["operator_cap_inr"],
            "own_cap_inr": now["own_cap_inr"]}
