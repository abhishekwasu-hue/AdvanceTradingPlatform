"""Runner S3 API (docs/design/USER_RUNNER.md section 4). Behind `ui_v2` + `user_runner` (OFF): 404 with X-Feature-Disabled
while either is off for the organisation.

The logged-in user, his own runners only (no admin path, no other member's runner):
* `POST /api/runner/pairing-codes`              - a one-time pairing code (shown once, expires in minutes)
* `GET  /api/runner/devices`                    - his paired runners with what each last reported
* `POST /api/runner/devices/{device_id}/revoke` - removes a runner from his account (its signed requests are refused)
* `GET  /api/runner/devices/{device_id}/mirror` - the read-only copies that runner uploaded

The runner (no login; the pairing code, then its own Ed25519 signature - app/runner_link/signing.py):
* `POST /api/runner/pair`             - code + public key + proof -> its device id
* `POST /api/runner/device/heartbeat` - signed: run state, version, its kill switch state, open positions count
* `POST /api/runner/device/mirror`    - signed, idempotent, size-capped: orders, positions, audit summaries

Nothing here places, changes or forwards an order, evaluates a strategy or sets a runner's kill switch.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession
from typing_extensions import Annotated

from app.audit.log import write_audit_log
from app.auth.dependencies import get_current_user, require_trader
from app.core.rate_limit import rate_limit, user_rate_limit
from app.db.models import User
from app.db.session import get_session
from app.runner_link import service
from app.runner_link.service import RunnerError

router = APIRouter(prefix="/api/runner", tags=["runner"])

# Rate limits, as the other endpoints: per user for his own calls, per client IP for the runner's.
pairing_code_rate_limit = user_rate_limit("runner_pairing_code", limit=10, window_seconds=3600)
pair_rate_limit = rate_limit("runner_pair", limit=10, window_seconds=600,
                             message="Too many pairing attempts. Wait a few minutes and try again.")
device_rate_limit = rate_limit("runner_device", limit=120, window_seconds=60)


async def _require_flags(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> None:
    from app.platform.controls import flag_enabled
    for name in (service.PAGE_FLAG, service.FLAG):
        if not await flag_enabled(session, name, user.tenant_id):
            raise HTTPException(status_code=404, detail="Not found", headers={"X-Feature-Disabled": name})


def _http(exc: RunnerError) -> HTTPException:
    if exc.code == "feature_off":
        return HTTPException(status_code=404, detail="Not found", headers={"X-Feature-Disabled": service.FLAG})
    return HTTPException(status_code=exc.status, detail={"message": exc.message, "code": exc.code})


# --- the logged-in user ---------------------------------------------------------------------------------------------------

@router.post("/pairing-codes", status_code=status.HTTP_201_CREATED, dependencies=[Depends(_require_flags), Depends(pairing_code_rate_limit)])
async def create_pairing_code(user: User = Depends(require_trader), session: AsyncSession = Depends(get_session)) -> dict:
    code, expires = await service.create_code(session, user)
    await write_audit_log(session, user.tenant_id, user.id, "runner_pairing_code", "created")
    await session.commit()
    return {"code": code, "expires_at": expires.isoformat(), "expires_in_minutes": service.PAIRING_CODE_MINUTES}


@router.get("/devices", dependencies=[Depends(_require_flags)])
async def my_devices(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict:
    prefs = await service.user_prefs(session, user.tenant_id, user.id)
    return {"devices": [service.device_dict(d) for d in await service.list_devices(session, user)], "settings": prefs}


@router.post("/devices/{device_id}/revoke", dependencies=[Depends(_require_flags)])
async def revoke_device(device_id: str, user: User = Depends(require_trader), session: AsyncSession = Depends(get_session)) -> dict:
    try:
        device = await service.revoke(session, user, device_id)
    except RunnerError as exc:
        raise _http(exc) from exc
    await write_audit_log(session, user.tenant_id, user.id, "runner_device_revoked", f"runner #{device.id}")
    await session.commit()
    return service.device_dict(device)


@router.get("/devices/{device_id}/mirror", dependencies=[Depends(_require_flags)])
async def device_mirror(device_id: str, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict:
    try:
        device = await service.own_device(session, user, device_id)
    except RunnerError as exc:
        raise _http(exc) from exc
    return {"device": service.device_dict(device), **await service.mirror_view(session, device)}


# --- the runner ---------------------------------------------------------------------------------------------------------------

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=60, pattern=r"^[A-Za-z0-9][A-Za-z0-9 ._()-]{0,59}$")]


class PairBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(min_length=4, max_length=20)
    public_key: str = Field(min_length=64, max_length=64)
    proof: str = Field(min_length=1, max_length=200)
    name: Name
    version: service.Version | None = None


@router.post("/pair", status_code=status.HTTP_201_CREATED, dependencies=[Depends(pair_rate_limit)])
async def pair_device(body: PairBody, session: AsyncSession = Depends(get_session)) -> dict:
    try:
        device = await service.pair(session, code=body.code, public_key=body.public_key, proof=body.proof, name=body.name,
                                    version=body.version, flags_on=lambda tenant_id: service.flags_on(session, tenant_id))
    except RunnerError as exc:
        await session.rollback()
        raise _http(exc) from exc
    await write_audit_log(session, device.tenant_id, device.user_id, "runner_device_paired", f"runner #{device.id}")
    await session.commit()
    return {"device_id": device.device_uid, "name": device.name, "status": device.status}


async def _signed(request: Request, session: AsyncSession, cap: int) -> tuple[Any, bytes]:
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > cap:
        raise HTTPException(status_code=413, detail={"message": "Upload too large: send it in smaller pages", "code": "too_large"})
    body = await request.body()
    if len(body) > cap:
        raise HTTPException(status_code=413, detail={"message": "Upload too large: send it in smaller pages", "code": "too_large"})
    try:
        device = await service.authenticate(session, method=request.method, path=request.url.path, headers=request.headers, body=body)
    except RunnerError as exc:
        await session.rollback()
        raise _http(exc) from exc
    if not await service.flags_on(session, device.tenant_id):
        await session.rollback()
        raise HTTPException(status_code=404, detail="Not found", headers={"X-Feature-Disabled": service.FLAG})
    return device, body


def _parse(model: Any, body: bytes) -> Any:
    try:
        return model.model_validate_json(body or b"{}")
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail={"message": "The runner sent data ATP cannot read", "code": "bad_body",
                                                     "errors": exc.errors(include_url=False, include_input=False)[:10]}) from exc


@router.post("/device/heartbeat", dependencies=[Depends(device_rate_limit)])
async def device_heartbeat(request: Request, session: AsyncSession = Depends(get_session)) -> dict:
    device, body = await _signed(request, session, service.MAX_HEARTBEAT_BYTES)
    beat = _parse(service.Heartbeat, body)
    answer = await service.heartbeat(session, device, beat, request.client.host if request.client else None)
    await session.commit()
    return answer


@router.post("/device/mirror", dependencies=[Depends(device_rate_limit)])
async def device_mirror_upload(request: Request, session: AsyncSession = Depends(get_session)) -> dict:
    device, body = await _signed(request, session, service.MAX_MIRROR_BYTES)
    upload = _parse(service.MirrorUpload, body)
    prefs = await service.user_prefs(session, device.tenant_id, device.user_id)
    if not prefs["mirror_upload"]:
        await session.commit()                                   # the signed request is spent either way (no replay)
        raise HTTPException(status_code=409, detail={"message": "Mirror upload is off in your runner settings", "code": "mirror_off"})
    counts = await service.mirror(session, device, upload)
    await session.commit()
    return {"ok": True, **counts}
