"""UI2-4a1: the caller's own UI preferences, stored on the server per user so phone and desktop agree.

* `GET /api/me/prefs` returns every namespace: the stored value merged over the namespace's defaults, and its version
  (0 = never saved, the defaults).
* `PUT /api/me/prefs/{namespace}` replaces one namespace with the JSON body, validated against that namespace's schema
  (unknown keys and wrong types are refused) and capped at MAX_BODY_BYTES. An optional `?version=` makes it a
  compare-and-set: a stale version gets 409 with the current value; without it the save simply replaces.
* Namespaces are an allow-list (NAMESPACES). `chart` is the first; UI-8 (setup wizard progress, "What is this page?"
  dismissals) adds its own by adding a model here.
* Per user and tenant: every read and write filters on both, and nothing in the URL names another user, so another
  member or organisation can neither read nor change it. Any role (a VIEWER too) may change their own. Not behind a
  flag: these are the user's own display settings, nothing is computed from them, and they place nothing.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, Literal, Optional, Type

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.db.models import User, UserPrefsRecord
from app.db.session import get_session

MAX_BODY_BYTES = 16 * 1024
HEX = r"^#[0-9a-fA-F]{6}$"


class _Prefs(BaseModel):
    # strict: "true" is not a boolean and 1 is not a theme; extra keys are refused, so a typo never saves silently
    model_config = ConfigDict(extra="forbid", strict=True)


class CandleColors(_Prefs):
    """A custom up / down candle colour pair (`#rrggbb`). Absent = the theme's own colours."""
    up: str = Field(pattern=HEX)
    down: str = Field(pattern=HEX)

    @model_validator(mode="after")
    def _distinct(self) -> "CandleColors":
        if self.up.lower() == self.down.lower():
            raise ValueError("up and down candles need different colours")
        return self


class ChartIndicators(_Prefs):
    """The panes a chart opens with (the owner's brief: volume and RSI on)."""
    volume: bool = True
    rsi: bool = True


class ChartPrefs(_Prefs):
    """`chart`: one setting place for every chart (both chart tabs, Signals, Positions, the Option chain)."""
    theme: Literal["app", "light", "dark"] = Field(default="app", description="app = follow the app theme (which follows the device)")
    grid: bool = False
    candle_colors: Optional[CandleColors] = None
    indicators: ChartIndicators = Field(default_factory=ChartIndicators)
    reader_overlay: bool = Field(default=False, description="the 'Reader zones & lines' overlay")


NAMESPACES: Dict[str, Type[_Prefs]] = {"chart": ChartPrefs}

router = APIRouter(prefix="/api/me", tags=["me"])


def defaults(namespace: str) -> Dict[str, Any]:
    return NAMESPACES[namespace]().model_dump(mode="json")


def stored_value(namespace: str, raw: Optional[str]) -> Dict[str, Any]:
    """A stored value as the current schema reads it: keys this release does not know (a newer release saved them) are
    dropped; anything else that no longer validates falls back to the defaults, so a read never fails."""
    model = NAMESPACES[namespace]
    try:
        data = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return defaults(namespace)
    if not isinstance(data, dict):
        return defaults(namespace)
    known = {k: v for k, v in data.items() if k in model.model_fields}
    try:
        return model.model_validate_json(json.dumps(known)).model_dump(mode="json")
    except ValidationError:
        return defaults(namespace)


def _own(user: User):
    return (UserPrefsRecord.tenant_id == user.tenant_id, UserPrefsRecord.user_id == user.id)


async def _body(request: Request) -> bytes:
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > MAX_BODY_BYTES:
        raise HTTPException(status_code=413, detail=f"Preferences larger than {MAX_BODY_BYTES} bytes")
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > MAX_BODY_BYTES:
            raise HTTPException(status_code=413, detail=f"Preferences larger than {MAX_BODY_BYTES} bytes")
    return bytes(data)


def _validate(namespace: str, raw: bytes) -> Dict[str, Any]:
    try:
        return NAMESPACES[namespace].model_validate_json(raw or b"null").model_dump(mode="json")
    except ValidationError as exc:
        # The input is never echoed back (main.py's rule for 422s): where and what only.
        problems = [{"loc": list(e.get("loc", ())), "msg": e.get("msg", "invalid")} for e in exc.errors(include_input=False, include_url=False)]
        raise HTTPException(status_code=422, detail=problems) from None


@router.get("/prefs")
async def get_prefs(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    """Every namespace of the caller's own preferences, defaults filled in; version 0 = never saved."""
    rows = {r.namespace: r for r in await session.scalars(select(UserPrefsRecord).where(*_own(user)))}
    prefs, versions = {}, {}
    for namespace in NAMESPACES:
        row = rows.get(namespace)
        prefs[namespace] = stored_value(namespace, row.value_json if row else None)
        versions[namespace] = row.version if row else 0
    return {"prefs": prefs, "versions": versions}


@router.put("/prefs/{namespace}")
async def put_prefs(namespace: str, request: Request, version: Optional[int] = None, user: User = Depends(get_current_user),
                    session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    """Replaces one namespace of the caller's own preferences with the body (fields left out take their defaults).
    `version` (optional): the version the change was made on; a stale one is refused with 409 and the current value."""
    if namespace not in NAMESPACES:
        raise HTTPException(status_code=404, detail="Unknown preferences namespace")
    value = _validate(namespace, await _body(request))
    row = await session.scalar(select(UserPrefsRecord).where(*_own(user), UserPrefsRecord.namespace == namespace).with_for_update())
    current = row.version if row else 0
    if version is not None and version != current:
        raise HTTPException(status_code=409, detail={"message": "the preferences changed since this edit was made", "version": current,
                                                     "current": stored_value(namespace, row.value_json if row else None)})
    now = datetime.now(timezone.utc)
    if row is None:
        row = UserPrefsRecord(tenant_id=user.tenant_id, user_id=user.id, namespace=namespace, value_json=json.dumps(value),
                              version=1, updated_at=now)
        session.add(row)
    else:
        row.value_json, row.version, row.updated_at = json.dumps(value), row.version + 1, now
    try:
        await session.commit()
    except IntegrityError:          # another device saved this namespace's first value at the same moment
        await session.rollback()
        raise HTTPException(status_code=409, detail={"message": "the preferences changed since this edit was made"}) from None
    return {"namespace": namespace, "value": value, "version": row.version}


__all__ = ["router", "NAMESPACES", "ChartPrefs", "MAX_BODY_BYTES", "defaults", "stored_value"]
