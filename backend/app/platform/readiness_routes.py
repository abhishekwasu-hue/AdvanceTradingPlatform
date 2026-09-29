"""Phase AB: `GET /api/readiness?target=PAPER|LIVE` - this organisation's go-live checklist."""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.db.models import Tenant, User
from app.db.session import get_session
from app.platform.readiness import tenant_checklist

router = APIRouter(prefix="/api/readiness", tags=["readiness"])


@router.get("")
async def readiness(target: str = Query(default="PAPER", pattern="^(?i)(paper|live)$"), user: User = Depends(get_current_user),
                    session: AsyncSession = Depends(get_session)) -> dict:
    tenant = await session.get(Tenant, user.tenant_id)
    return (await tenant_checklist(session, user, tenant, target)).as_dict()
