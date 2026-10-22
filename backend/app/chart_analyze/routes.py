"""Analyze a chart: the API (docs/design/CHART_ANALYZE.md §5). Behind the `chart_analyze` flag: while it is off for the
caller's organisation every route below answers the platform's standard 503 with `X-Feature-Disabled: chart_analyze`.

- `GET    /api/chart-analyze/status`              what the page needs: engine installed?, limits used, upload rules
- `POST   /api/chart-analyze/items`               multipart: `file` (PNG / JPEG) OR `link`, and `mode` -> one stored item
- `GET    /api/chart-analyze/items`               the caller's history (newest first)
- `GET    /api/chart-analyze/items/{id}`          one item with its result
- `GET    /api/chart-analyze/items/{id}/image`    the stored (cleaned) image, private, never cached by a proxy
- `POST   /api/chart-analyze/items/{id}/analyze`  runs the analysis (Image only in PR-A)
- `DELETE /api/chart-analyze/items/{id}`          deletes the image and the result at once
- `GET/PUT /api/admin/chart-analyze/settings`     SUPER_ADMIN (MFA): sizes, retention, limits, cost caps
Every item route reads the caller's own rows only (tenant AND user). Upload, link and analyze share a per-user rate
limit from the settings. Nothing here places, proposes or changes an order.
"""
import asyncio
import weakref
from typing import Any, Dict, Optional

from fastapi.concurrency import run_in_threadpool
from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user, require_mfa_session, require_role
from app.chart_analyze import FLAG, contract, imaging, limits, linkfetch, service, storage
from app.chart_analyze import provider as engine_provider
from app.chart_analyze import settings as settings_mod
from app.core.rate_limit import charge_user
from app.db.models import Tenant, User
from app.db.session import get_session
from app.platform.controls import require_flag

router = APIRouter(prefix="/api/chart-analyze", tags=["chart-analyze"])
admin_router = APIRouter(prefix="/api/admin/chart-analyze", tags=["admin"], dependencies=[Depends(require_role()), Depends(require_mfa_session)])

RATE_NAME = "chart_analyze"
READ_CHUNK = 64 * 1024
# Process resource guard (not a plan limit): image decodes running at once in one API process. The gate is taken on the
# event loop BEFORE a thread is used, so waiting decodes never sit in anyio's shared worker threads. One gate per event
# loop (an asyncio.Semaphore belongs to the loop it first waits on).
DECODE_SLOTS = 2
_DECODE_GATES: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Semaphore]" = weakref.WeakKeyDictionary()


def _decode_gate() -> asyncio.Semaphore:
    loop = asyncio.get_running_loop()
    gate = _DECODE_GATES.get(loop)
    if gate is None:
        gate = _DECODE_GATES[loop] = asyncio.Semaphore(DECODE_SLOTS)
    return gate


class SettingsBody(BaseModel):
    values: Dict[str, Any] = Field(default_factory=dict, max_length=len(settings_mod.DEFAULTS))


async def _gate(session: AsyncSession, user: User) -> Dict[str, Any]:
    await require_flag(session, FLAG, user.tenant_id)
    return await settings_mod.load(session)


async def _tenant(session: AsyncSession, user: User) -> Tenant:
    tenant = await session.get(Tenant, user.tenant_id)
    if tenant is None:
        raise HTTPException(status_code=404, detail="Unknown organisation")
    return tenant


async def _rate(user: User, settings: Dict[str, Any]) -> None:
    await charge_user(RATE_NAME, user.id, int(settings["rate_limit_per_minute"]), 60, 1)


def _unreadable(reason: str) -> HTTPException:
    return HTTPException(status_code=422, detail={"message": linkfetch.SCREENSHOT_MESSAGE, "reason": reason, "code": "link_unreadable"})


async def _read_upload(file: UploadFile, settings: Dict[str, Any]) -> bytes:
    """Reads at most the size limit plus one byte, so an oversize upload is refused without holding all of it."""
    limit = int(settings["max_image_bytes"])
    data = bytearray()
    while True:
        chunk = await file.read(READ_CHUNK)
        if not chunk:
            break
        data.extend(chunk)
        if len(data) > limit:
            imaging.check_size(len(data), settings)
    return bytes(data)


@router.get("/status")
async def status(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    settings = await _gate(session, user)
    tenant = await _tenant(session, user)
    return {"enabled": True, "engine": engine_provider.describe(),
            "modes": {"image_only": True, "hybrid": False}, "hybrid_note": service.HYBRID_SOON,
            "limits": await limits.usage(session, user, tenant, settings),
            "upload": {"types": ["image/png", "image/jpeg"], "max_bytes": int(settings["max_image_bytes"]),
                       "max_width": int(settings["max_image_width"]), "max_height": int(settings["max_image_height"])},
            "retention_days": int(settings["retention_days"]),
            "label": contract.IMAGE_ONLY_LABEL, "disclaimer": contract.DISCLAIMER, "link_message": linkfetch.SCREENSHOT_MESSAGE}


@router.post("/items", status_code=201)
async def add_item(file: Optional[UploadFile] = File(default=None), link: Optional[str] = Form(default=None, max_length=linkfetch.MAX_URL_LENGTH),
                   mode: str = Form(default="image_only"), user: User = Depends(get_current_user),
                   session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    settings = await _gate(session, user)
    if mode not in service.MODES:
        raise HTTPException(status_code=422, detail={"message": "mode must be image_only or hybrid", "code": "bad_mode"})
    if mode == "hybrid":
        raise HTTPException(status_code=409, detail={"message": service.HYBRID_SOON, "code": "hybrid_soon"})
    has_file = file is not None and bool(file.filename or file.size)
    if has_file == bool((link or "").strip()):
        raise HTTPException(status_code=422, detail={"message": "Send one chart: an image file or a link.", "code": "one_source"})
    await _rate(user, settings)
    tenant = await _tenant(session, user)
    # Early, unlocked: a refused user costs nothing. Then the read transaction ends here, so no connection or lock is held
    # while the upload is read, the link fetched or the image decoded; the add re-checks under its lock before inserting.
    await limits.check_can_add(session, user, tenant, settings)
    await session.commit()          # ends the read-only transaction (nothing written); loaded objects stay usable
    source_host: Optional[str] = None
    try:
        if has_file and file is not None:
            raw = await _read_upload(file, settings)
            source = "upload"
        else:
            try:
                fetched = await linkfetch.fetch_image(link or "", settings)
            except linkfetch.LinkUnreadable as exc:
                raise _unreadable(exc.reason) from None
            raw, source, source_host = fetched.data, "link", fetched.target.source_host
        clean = await _decode(raw, settings)
    except imaging.ImageRejected as exc:
        if has_file:
            raise HTTPException(status_code=exc.status, detail={"message": str(exc), "code": "image_rejected"}) from None
        raise _unreadable(str(exc)) from None
    try:
        row = await service.add_item(session, user, tenant, settings, clean, mode=mode, source=source, source_host=source_host)
    except storage.StorageUnavailable:
        raise HTTPException(status_code=503, detail={"message": "Chart storage is not available on this server. Ask the administrator.",
                                                     "code": "storage_unavailable"}) from None
    return service.as_dict(row)


async def _decode(raw: bytes, settings: Dict[str, Any]) -> imaging.CleanImage:
    """The CPU-bound decode in a worker thread, at most DECODE_SLOTS at a time in this process (a large image takes
    about a second and some hundred MB while it is decoded), so the event loop never stalls on it."""
    async with _decode_gate():
        return await run_in_threadpool(imaging.sanitize, raw, settings)


@router.get("/items")
async def list_items(limit: int = 50, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    await _gate(session, user)
    return {"items": await service.history(session, user, limit)}


@router.get("/items/{item_id}")
async def get_item(item_id: int, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    await _gate(session, user)
    return service.as_dict(await service.own(session, user, item_id))


@router.get("/items/{item_id}/image")
async def get_image(item_id: int, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> Response:
    await _gate(session, user)
    row = await service.own(session, user, item_id)
    data = storage.read(row.image_path or "")
    if data is None:                       # the row is the caller's, its file is gone (retention ran, or the store was lost)
        raise HTTPException(status_code=410, detail={"message": "The chart image is no longer stored.", "code": "image_gone"})
    return Response(content=data, media_type="image/png",
                    headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff",
                             "Content-Disposition": f'inline; filename="chart-{row.id}.png"'})


@router.post("/items/{item_id}/analyze")
async def analyze(item_id: int, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    settings = await _gate(session, user)
    await _rate(user, settings)
    tenant = await _tenant(session, user)
    return service.as_dict(await service.analyze(session, user, tenant, settings, item_id))


@router.delete("/items/{item_id}")
async def delete_item(item_id: int, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    settings = await _gate(session, user)
    await service.delete_item(session, user, item_id, settings)
    return {"deleted": item_id}


@admin_router.get("/settings")
async def get_settings(session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    return {"settings": await settings_mod.load(session), "defaults": settings_mod.DEFAULTS}


@admin_router.put("/settings")
async def put_settings(body: SettingsBody, user: User = Depends(require_role()), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    try:
        return {"settings": await settings_mod.save(session, user, body.values), "defaults": settings_mod.DEFAULTS}
    except settings_mod.SettingsError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
