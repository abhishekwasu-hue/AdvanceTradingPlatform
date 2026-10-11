"""HB0: GET /api/admin/history-import/progress - what the R2 import has done (SUPER_ADMIN, MFA session).

Read-only: per dataset the files by status, per symbol done / pending / refused / failed and the last error (the
state HB4's admin page will show). The import itself is run by hand from the CLI (`python -m app.market_lake.r2_import`),
never from a request. Symbols and counts only - no bars are returned here.
"""
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require_mfa_session, require_role
from app.db.session import get_session
from app.market_lake import r2_import
from app.platform.controls import flag_enabled

router = APIRouter(prefix="/api/admin/history-import", tags=["admin"],
                   dependencies=[Depends(require_role()), Depends(require_mfa_session)])


@router.get("/progress")
async def import_progress(dataset: Optional[str] = Query(None, pattern="^(daily|m1)$"), limit: int = Query(500, ge=1, le=5000),
                          session: AsyncSession = Depends(get_session)) -> dict:
    result = await r2_import.progress(session, dataset=dataset, limit=limit)
    result["flag_on"] = await flag_enabled(session, r2_import.FLAG)
    return result
