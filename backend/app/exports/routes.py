"""Phase D2: compliance export endpoints.

Tenant scope: `GET /api/exports/{dataset}` - OWNER only (the whole organisation's trail, not
just the caller's rows, is what an auditor asks the responsible person for).
Platform scope: `GET /api/admin/exports/{dataset}` - SUPER_ADMIN with a verified MFA session,
optionally narrowed to one tenant with `tenant_id`.

Every export is itself an audit event (`export_generated`: dataset, range, rows, SHA-256), so
"who pulled what" is on the same hash chain as everything else.
"""
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.log import write_audit_log
from app.auth.dependencies import require_mfa_session, require_owner, require_role
from app.db.models import User
from app.db.session import get_session
from app.exports.service import DATASETS, FORMATS, MAX_ROWS, build_export

router = APIRouter(tags=["exports"])


def _response(export) -> Response:
    headers = {
        "Content-Disposition": f'attachment; filename="{export.filename}"',
        "X-Content-SHA256": export.sha256,
        "X-Export-Rows": str(export.row_count),
        "Access-Control-Expose-Headers": "Content-Disposition, X-Content-SHA256, X-Export-Rows",
    }
    if export.chain_intact is not None:
        headers["X-Audit-Chain-Intact"] = "true" if export.chain_intact else "false"
    return Response(content=export.content, media_type=export.media_type, headers=headers)


async def _export(session, user: User, dataset: str, fmt: str, tenant_id: Optional[int], date_from, date_to, limit):
    if dataset not in DATASETS:
        raise HTTPException(status_code=404, detail=f"Unknown dataset; one of {sorted(DATASETS)}")
    if fmt not in FORMATS:
        raise HTTPException(status_code=400, detail=f"format must be one of {list(FORMATS)}")
    try:
        export = await build_export(
            session, dataset, fmt, tenant_id=tenant_id, date_from=date_from, date_to=date_to, limit=limit,
            generated_by=user.email,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    scope = "platform" if tenant_id is None else f"tenant {tenant_id}"
    await write_audit_log(
        session, user.tenant_id, user.id, "export_generated",
        f"{dataset} {fmt} {scope} {date_from or 'start'}..{date_to or 'now'} rows={export.row_count} sha256={export.sha256}",
    )
    await session.commit()
    return _response(export)


@router.get("/api/exports/{dataset}")
async def tenant_export(
    dataset: str, format: str = Query("csv"), date_from: Optional[date] = Query(None, alias="from"),
    date_to: Optional[date] = Query(None, alias="to"), limit: int = Query(MAX_ROWS, ge=1, le=MAX_ROWS),
    user: User = Depends(require_owner), session: AsyncSession = Depends(get_session),
) -> Response:
    """The organisation's own records as a downloadable file (owner only)."""
    return await _export(session, user, dataset, format, user.tenant_id, date_from, date_to, limit)


@router.get("/api/admin/exports/{dataset}", dependencies=[Depends(require_mfa_session)])
async def platform_export(
    dataset: str, format: str = Query("csv"), tenant_id: Optional[int] = None,
    date_from: Optional[date] = Query(None, alias="from"), date_to: Optional[date] = Query(None, alias="to"),
    limit: int = Query(MAX_ROWS, ge=1, le=MAX_ROWS),
    user: User = Depends(require_role()), session: AsyncSession = Depends(get_session),
) -> Response:
    """Platform-wide (or one tenant's) records for regulators and internal audit."""
    return await _export(session, user, dataset, format, tenant_id, date_from, date_to, limit)
