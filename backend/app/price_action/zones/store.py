"""Market-zone storage (Z7a-2): tenant settings (the OI banner `overrides` pattern) and the as-of zone snapshots.

  * market_zone_settings - a tenant's overrides per lake instrument ("*" = the tenant's default), layered platform
    defaults (MARKET_ZONES_DEFAULTS, then the Trade values in ZoneSettings) -> "*" row -> instrument row. `enabled`
    asks the nightly job to follow the instrument.
  * market_zone_runs / market_zones - one append-only snapshot per (scope, instrument, as_of): the rows of
    snapshot.build_snapshot. scope "platform" is shared by every tenant whose resolved settings equal the platform's
    (settings_hash); a tenant with different settings gets scope "tenant:<id>". Inputs are the shared lake bars only.
  * zones_as_of(T) reads the snapshot with the latest as_of <= T - so a backtest at T never sees a zone computed later.
"""
import hashlib
import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional, Tuple

import pandas as pd
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import config
from app.db.models import MarketZoneRecord, MarketZoneRunRecord, MarketZoneSettingRecord
from app.price_action.zones.settings import ZoneSettings, resolve_settings

logger = logging.getLogger(__name__)

PLATFORM_SCOPE = "platform"
DEFAULT_KEY = "*"
_KEY = re.compile(r"^[A-Z0-9_]{1,12}:[A-Z0-9 &_.\-]{1,50}$")


def tenant_scope(tenant_id: int) -> str:
    return f"tenant:{int(tenant_id)}"


def normalise_key(key: str) -> str:
    """A lake instrument key ("NSE:BANKNIFTY") or "*"; anything else raises ValueError."""
    value = " ".join(str(key or "").upper().split())
    if value == DEFAULT_KEY or _KEY.match(value):
        return value
    raise ValueError(f"invalid instrument key {key!r} (expected EXCHANGE:SYMBOL or '*')")


# ---------------------------------------------------------------- settings

def platform_layer() -> Dict[str, Any]:
    return dict(config.MARKET_ZONES_DEFAULTS or {})


def platform_settings() -> ZoneSettings:
    try:
        return resolve_settings(platform_layer())
    except ValueError as exc:                       # a bad operator default must not stop the job
        logger.warning("MARKET_ZONES_DEFAULTS invalid (%s) - the Trade defaults are used", exc)
        return ZoneSettings()


def settings_hash(settings: ZoneSettings) -> str:
    return hashlib.sha256(json.dumps(settings.model_dump(mode="json"), sort_keys=True).encode()).hexdigest()


def _overrides(row: Optional[MarketZoneSettingRecord]) -> Dict[str, Any]:
    if row is None:
        return {}
    try:
        value = json.loads(row.overrides or "{}")
    except ValueError:
        logger.warning("market_zone_settings %s: overrides are not JSON - ignored", row.id)
        return {}
    return value if isinstance(value, dict) else {}


async def settings_rows(session: AsyncSession, tenant_id: int, key: str) -> Tuple[Optional[MarketZoneSettingRecord], Optional[MarketZoneSettingRecord]]:
    rows = {r.instrument_key: r for r in await session.scalars(select(MarketZoneSettingRecord).where(
        MarketZoneSettingRecord.tenant_id == tenant_id, MarketZoneSettingRecord.instrument_key.in_([DEFAULT_KEY, key])))}
    return rows.get(DEFAULT_KEY), (rows.get(key) if key != DEFAULT_KEY else None)


async def settings_for(session: AsyncSession, tenant_id: int, key: str) -> ZoneSettings:
    """Platform defaults, then the tenant's "*" row, then the instrument's row. A stored override that is no longer
    valid falls back to the platform settings (never fails the job)."""
    key = normalise_key(key)
    default_row, own_row = await settings_rows(session, tenant_id, key)
    try:
        return resolve_settings(platform_layer(), _overrides(default_row), _overrides(own_row))
    except ValueError as exc:
        logger.warning("Tenant %s %s: zone settings invalid (%s) - platform settings used", tenant_id, key, exc)
        return platform_settings()


async def save_settings(session: AsyncSession, tenant_id: int, key: str, overrides: Optional[Mapping[str, Any]] = None, *,
                        enabled: Optional[bool] = None, user_id: Optional[int] = None,
                        now: Optional[datetime] = None) -> MarketZoneSettingRecord:
    """Replace a tenant's overrides for `key` (validated against the layers below it - unknown or out-of-range values
    raise ValueError and nothing is stored) and / or switch following on or off. The caller commits."""
    key = normalise_key(key)
    default_row, own_row = await settings_rows(session, tenant_id, key)
    row = default_row if key == DEFAULT_KEY else own_row
    if overrides is not None:
        clean = dict(overrides)
        below = [platform_layer()] if key == DEFAULT_KEY else [platform_layer(), _overrides(default_row)]
        resolve_settings(*below, clean)
    if row is None:
        row = MarketZoneSettingRecord(tenant_id=tenant_id, instrument_key=key, overrides="{}", enabled=False)
        session.add(row)
    if overrides is not None:
        row.overrides = json.dumps(clean, sort_keys=True)
    if enabled is not None:
        if key == DEFAULT_KEY and enabled:
            raise ValueError("follow an instrument, not '*'")
        row.enabled = bool(enabled)
    row.updated_by, row.updated_at = user_id, now or datetime.now(timezone.utc)
    await session.flush()
    return row


async def followed(session: AsyncSession) -> List[Tuple[int, str]]:
    """(tenant_id, instrument_key) for every instrument a tenant asked the zone job to follow."""
    rows = await session.scalars(select(MarketZoneSettingRecord).where(MarketZoneSettingRecord.enabled.is_(True),
                                                                       MarketZoneSettingRecord.instrument_key != DEFAULT_KEY))
    return sorted((r.tenant_id, r.instrument_key) for r in rows)


# ---------------------------------------------------------------- snapshots

EXCHANGE_TZ = "Asia/Kolkata"


def _dt(x: Any, exchange_tz: str = EXCHANGE_TZ) -> Optional[datetime]:
    """A stored time: tz-aware UTC. A naive stamp is exchange time (the zones package convention; the job's own frames
    are always tz-aware)."""
    if x is None or (isinstance(x, float) and x != x):
        return None
    t = pd.Timestamp(x)
    if t is pd.NaT:
        return None
    t = t.tz_localize(exchange_tz).tz_convert("UTC") if t.tzinfo is None else t.tz_convert("UTC")
    return t.to_pydatetime()


def _utc(x: Optional[datetime]) -> Optional[datetime]:
    """SQLite hands tz-aware columns back naive - read them as UTC."""
    if x is None:
        return None
    return x if x.tzinfo is not None else x.replace(tzinfo=timezone.utc)


async def latest_run(session: AsyncSession, scope: str, key: str, at: Optional[datetime] = None, *,
                     strictly_before: bool = False) -> Optional[MarketZoneRunRecord]:
    """The run with the latest as_of <= at (< at when strictly_before; any when at is None)."""
    query = select(MarketZoneRunRecord).where(MarketZoneRunRecord.scope == scope, MarketZoneRunRecord.instrument_key == key)
    at = _dt(at)
    if at is not None:
        query = query.where(MarketZoneRunRecord.as_of < at if strictly_before else MarketZoneRunRecord.as_of <= at)
    return await session.scalar(query.order_by(MarketZoneRunRecord.as_of.desc()).limit(1))


async def run_exists(session: AsyncSession, scope: str, key: str, as_of: datetime) -> bool:
    return await session.scalar(select(MarketZoneRunRecord.id).where(
        MarketZoneRunRecord.scope == scope, MarketZoneRunRecord.instrument_key == key, MarketZoneRunRecord.as_of == _dt(as_of)).limit(1)) is not None


def record_to_row(rec: MarketZoneRecord) -> Dict[str, Any]:
    try:
        tracker = json.loads(rec.tracker or "{}")
    except ValueError:
        tracker = {}
    return {"zone_key": rec.zone_key, "zone_type": rec.zone_type, "timeframe": rec.timeframe, "zone_low": rec.zone_low,
            "zone_high": rec.zone_high, "strength": rec.strength, "role": rec.role, "origin_role": rec.origin_role, "state": rec.state,
            "formed_at": _utc(rec.formed_at), "confirmed_at": _utc(rec.confirmed_at),
            "first_touch": None if rec.first_touch_event is None else {"event": rec.first_touch_event, "time": _utc(rec.first_touch_at)},
            "touches": rec.touches, "flips": rec.flips,
            "last_event": None if rec.last_event is None else {"event": rec.last_event, "time": _utc(rec.last_event_at)},
            "structure_level": rec.structure_level, "invalidated_at": _utc(rec.invalidated_at), "remembered": rec.remembered,
            "tracker": tracker}


async def rows_of(session: AsyncSession, run: Optional[MarketZoneRunRecord]) -> List[Dict[str, Any]]:
    if run is None:
        return []
    recs = await session.scalars(select(MarketZoneRecord).where(MarketZoneRecord.run_id == run.id).order_by(MarketZoneRecord.id))
    return [record_to_row(r) for r in recs]


def row_to_record(run_id: int, r: Mapping[str, Any]) -> MarketZoneRecord:
    ft, le = r.get("first_touch") or {}, r.get("last_event") or {}
    formed = _dt(r.get("formed_at"))
    if formed is None:
        raise ValueError(f"zone {r.get('zone_key')} has no formation time")
    return MarketZoneRecord(
        run_id=run_id, zone_key=str(r["zone_key"])[:200], zone_type=str(r["zone_type"]), timeframe=str(r["timeframe"]),
        zone_low=float(r["zone_low"]), zone_high=float(r["zone_high"]),
        strength=None if r.get("strength") is None else float(r["strength"]), role=r.get("role"),
        origin_role=str(r.get("origin_role") or ""), state=str(r["state"]), formed_at=formed, confirmed_at=_dt(r.get("confirmed_at")),
        first_touch_event=ft.get("event"), first_touch_at=_dt(ft.get("time")), touches=int(r.get("touches") or 0),
        flips=int(r.get("flips") or 0), last_event=le.get("event"), last_event_at=_dt(le.get("time")),
        structure_level=None if r.get("structure_level") is None else float(r["structure_level"]),
        invalidated_at=_dt(r.get("invalidated_at")), remembered=bool(r.get("remembered")),
        tracker=json.dumps(r.get("tracker") or {}, sort_keys=True, default=str))


async def write_run(session: AsyncSession, *, scope: str, tenant_id: Optional[int], key: str, as_of: datetime, settings: ZoneSettings,
                    rows: List[Mapping[str, Any]], price: Optional[float], bars: Mapping[str, int],
                    previous_run_id: Optional[int] = None, now: Optional[datetime] = None) -> MarketZoneRunRecord:
    """Store one snapshot (the caller commits). Duplicate (scope, key, as_of) raise from the unique constraint."""
    run = MarketZoneRunRecord(scope=scope, tenant_id=tenant_id, instrument_key=key, as_of=_dt(as_of), computed_at=now or datetime.now(timezone.utc),
                              settings_hash=settings_hash(settings), price=price, bars=json.dumps(dict(bars), sort_keys=True),
                              zone_count=len(rows), previous_run_id=previous_run_id)
    session.add(run)
    await session.flush()
    session.add_all([row_to_record(run.id, r) for r in rows])
    await session.flush()
    return run


async def zones_as_of(session: AsyncSession, key: str, at: datetime, tenant_id: Optional[int] = None,
                      ) -> Tuple[Optional[MarketZoneRunRecord], List[Dict[str, Any]]]:
    """The zones known at `at`: the latest snapshot with as_of <= at - the tenant's own scope when it has one at least as
    recent as the platform's, else the platform's. Never a snapshot computed from bars after `at`."""
    key = normalise_key(key)
    candidates = []
    if tenant_id is not None:
        own = await latest_run(session, tenant_scope(tenant_id), key, at)
        if own is not None:
            candidates.append((_utc(own.as_of), 1, own))
    shared = await latest_run(session, PLATFORM_SCOPE, key, at)
    if shared is not None:
        candidates.append((_utc(shared.as_of), 0, shared))
    if not candidates:
        return None, []
    run = max(candidates, key=lambda c: (c[0], c[1]))[2]
    return run, await rows_of(session, run)
