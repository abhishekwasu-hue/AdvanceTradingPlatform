"""UI2-4a1: the caller's own UI preferences, stored on the server per user so phone and desktop agree.

* `GET /api/me/prefs` returns every namespace: the stored value merged over the namespace's defaults, and its version
  (0 = never saved, the defaults).
* `PUT /api/me/prefs/{namespace}` replaces one namespace with the JSON body, validated against that namespace's schema
  (unknown keys and wrong types are refused) and capped at MAX_BODY_BYTES. An optional `?version=` makes it a
  compare-and-set: a stale version gets 409 with the current value; without it the save simply replaces.
* Namespaces are an allow-list (NAMESPACES). `chart` is the first; `setup` (UI2-7, UI-8) holds the setup wizard's own
  marks - skips, the chip's dismissal, the first automatic opening. Whether a step is done is never stored: app/me/setup.py
  works it out on every read from the rows that prove it.
* Per user and tenant: every read and write filters on both, and nothing in the URL names another user, so another
  member or organisation can neither read nor change it. Any role (a VIEWER too) may change their own. Not behind a
  flag: these are the user's own display settings, nothing is computed from them, and they place nothing.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, Literal, Optional, Tuple, Type

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


class SetupStepMark(_Prefs):
    """One setup step's own mark. Only the skip is stored; "done" is worked out on every read (app/me/setup.py)."""
    skipped_at: Optional[datetime] = Field(default=None, description="when 'Skip for now' was chosen; null = not skipped")


class SetupSteps(_Prefs):
    broker: SetupStepMark = Field(default_factory=SetupStepMark)
    risk: SetupStepMark = Field(default_factory=SetupStepMark)
    strategy: SetupStepMark = Field(default_factory=SetupStepMark)
    alerts: SetupStepMark = Field(default_factory=SetupStepMark)


class SetupPrefs(_Prefs):
    """`setup` (UI v2 §11, UI-8): the setup wizard's marks, so it resumes on any device. localStorage is only a cache."""
    steps: SetupSteps = Field(default_factory=SetupSteps)
    opened_at: Optional[datetime] = Field(default=None, description="the wizard opened by itself (first sign-in); it does not again")
    dismissed_at: Optional[datetime] = Field(default=None, description="the dashboard's Setup chip was closed")


NAMESPACES: Dict[str, Type[_Prefs]] = {"chart": ChartPrefs, "setup": SetupPrefs}

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


def _own(tenant_id: int, user_id: int):
    return (UserPrefsRecord.tenant_id == tenant_id, UserPrefsRecord.user_id == user_id)


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
    rows = {r.namespace: r for r in await session.scalars(select(UserPrefsRecord).where(*_own(user.tenant_id, user.id)))}
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
    value, saved_version = await save(namespace, await _body(request), version, user, session)
    return {"namespace": namespace, "value": value, "version": saved_version}


async def save(namespace: str, raw: bytes, version: Optional[int], user: User, session: AsyncSession) -> Tuple[Dict[str, Any], int]:
    """Validates `raw` against the namespace and stores it for the caller (the compare-and-set described above). Also
    the body of `PUT /api/me/setup` (app/me/setup.py). Returns the stored value and its new version."""
    value = _validate(namespace, raw)
    tenant_id, user_id = user.tenant_id, user.id        # read once: a rollback below expires the `user` object
    # Two attempts: when two devices save a namespace's FIRST value at the same moment, one insert loses on the unique
    # constraint. Without a version that save simply goes again, now as an update of the row the other one made; with a
    # version it is a 409 like any other stale edit.
    saved = 0
    for attempt in (1, 2):
        row = await session.scalar(select(UserPrefsRecord).where(*_own(tenant_id, user_id), UserPrefsRecord.namespace == namespace)
                                   .with_for_update())
        current = row.version if row else 0
        if version is not None and version != current:
            raise HTTPException(status_code=409, detail={"message": "the preferences changed since this edit was made", "version": current,
                                                         "current": stored_value(namespace, row.value_json if row else None)})
        now = datetime.now(timezone.utc)
        if row is None:
            row = UserPrefsRecord(tenant_id=tenant_id, user_id=user_id, namespace=namespace, value_json=json.dumps(value),
                                  version=1, updated_at=now)
            session.add(row)
        else:
            row.value_json, row.version, row.updated_at = json.dumps(value), row.version + 1, now
        saved = row.version
        try:
            await session.commit()
            break
        except IntegrityError:
            await session.rollback()
            if version is not None or attempt == 2:
                raise HTTPException(status_code=409, detail={"message": "the preferences changed since this edit was made"}) from None
    return value, saved


async def read_one(namespace: str, user: User, session: AsyncSession) -> Tuple[Dict[str, Any], int]:
    """One namespace of the caller's own preferences (defaults filled in) and its version (0 = never saved)."""
    row = await session.scalar(select(UserPrefsRecord).where(*_own(user.tenant_id, user.id), UserPrefsRecord.namespace == namespace))
    return stored_value(namespace, row.value_json if row else None), (row.version if row else 0)


__all__ = ["router", "NAMESPACES", "ChartPrefs", "SetupPrefs", "MAX_BODY_BYTES", "defaults", "stored_value", "save", "read_one"]
