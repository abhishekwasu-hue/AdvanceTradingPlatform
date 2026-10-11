"""Market-zone storage (Z7a-2): tenant settings (the OI banner `overrides` pattern) and the as-of zone snapshots.

  * market_zone_settings - a tenant's overrides per lake instrument ("*" = the tenant's default), layered: the Trade
    values in ZoneSettings -> PLATFORM_DEFAULTS (all-daily) -> MARKET_ZONES_DEFAULTS -> "*" row -> instrument row
    (PRESETS["trade_intraday"] is Trade's intraday choice as a tenant opt-in). `enabled` asks the nightly job to follow
    the instrument.
  * market_zone_runs / market_zones - one append-only snapshot per (scope, instrument, as_of): the rows of
    snapshot.build_snapshot. scope "platform" is shared by every tenant whose resolved settings equal the platform's
    (settings_hash); a tenant with different settings gets scope "tenant:<id>". Inputs are the shared lake bars only.
  * zones_as_of(T) reads the snapshot with the latest as_of <= T built with the reader's own settings (hash) - so a
    backtest at T never sees a zone computed later, nor one computed with other settings unless it asks for the explicit
    platform fallback.
"""
import hashlib
import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import pandas as pd
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import config
from app.db.models import MarketZoneRecord, MarketZoneRunRecord, MarketZoneSettingRecord
from app.price_action.zones.settings import ZoneSettings, resolve_settings
from app.price_action.zones.snapshot import is_line

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

# The platform's own layer (owner, provisional): ALL-DAILY until the shared lake holds intraday bars - after HB0 / HB1 the
# shared lake carries daily history ("day", END 15:30 IST); 1-minute data is tenant-private and never read here. The
# Trade values (1h zones, 15m gaps, 1m..1h dynamic S/R) stay in ZoneSettings and are a tenant opt-in: INTRADAY_PRESET.
# gap_min_pct stays at Trade's 0.30 % - on daily bars a gap needs two whole sessions' ranges apart, so it is already rare.
PLATFORM_DEFAULTS: Dict[str, Any] = {
    "zone_timeframe": "1d", "sr_timeframe": "1d", "gap_timeframe": "1d", "dynamic_sr_timeframes": ["1d"],
    "recent_days_by_tf": {"1d": 450}, "history_days": 730, "memory_retire_days_by_tf": {"1d": 90},
}
PRESETS: Dict[str, Dict[str, Any]] = {
    # Trade's intraday choice (refresh_market_zones.py): needs shared intraday bars (or 1m rebuilt) for the instrument.
    "trade_intraday": {"zone_timeframe": "1h", "sr_timeframe": "1h", "gap_timeframe": "15m",
                       "dynamic_sr_timeframes": ["1m", "5m", "15m", "30m", "1h"], "history_days": 365},
}


def platform_layers() -> List[Dict[str, Any]]:
    """The layers under every tenant: the platform defaults above, then the operator's MARKET_ZONES_DEFAULTS."""
    return [dict(PLATFORM_DEFAULTS), dict(config.MARKET_ZONES_DEFAULTS or {})]


def platform_settings() -> ZoneSettings:
    try:
        return resolve_settings(*platform_layers())
    except ValueError as exc:                       # a bad operator default must not stop the job
        logger.warning("MARKET_ZONES_DEFAULTS invalid (%s) - the platform defaults are used", exc)
        return resolve_settings(PLATFORM_DEFAULTS)


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
        return resolve_settings(*platform_layers(), _overrides(default_row), _overrides(own_row))
    except ValueError as exc:
        logger.warning("Tenant %s %s: zone settings invalid (%s) - platform settings used", tenant_id, key, exc)
        return platform_settings()


async def save_settings(session: AsyncSession, tenant_id: int, key: str, overrides: Optional[Mapping[str, Any]] = None, *,
                        enabled: Optional[bool] = None, user_id: Optional[int] = None, preset: Optional[str] = None,
                        now: Optional[datetime] = None) -> MarketZoneSettingRecord:
    """Replace a tenant's overrides for `key` (validated against the layers below it - unknown or out-of-range values
    raise ValueError and nothing is stored) and / or switch following on or off. `preset` ("trade_intraday") starts the
    overrides from a named set (the given overrides win). The caller commits."""
    key = normalise_key(key)
    default_row, own_row = await settings_rows(session, tenant_id, key)
    row = default_row if key == DEFAULT_KEY else own_row
    if preset is not None:
        if preset not in PRESETS:
            raise ValueError(f"unknown preset {preset!r}; one of {sorted(PRESETS)}")
        overrides = {**PRESETS[preset], **dict(overrides or {})}
    if overrides is not None:
        clean = dict(overrides)
        below = platform_layers() if key == DEFAULT_KEY else [*platform_layers(), _overrides(default_row)]
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


def _aware(at: Optional[datetime], name: str = "at") -> Optional[datetime]:
    """A read time must say its zone: a naive datetime is refused (ambiguous between UTC and exchange time)."""
    if at is not None and at.tzinfo is None:
        raise ValueError(f"{name} must be timezone-aware (got naive {at!r})")
    return at


async def latest_run(session: AsyncSession, scope: str, key: str, at: Optional[datetime] = None, *, strictly_before: bool = False,
                     settings_hash: Optional[str] = None) -> Optional[MarketZoneRunRecord]:
    """The run with the latest as_of <= at (< at when strictly_before; any when at is None), optionally only runs built
    with the given settings hash. `at` must be timezone-aware."""
    query = select(MarketZoneRunRecord).where(MarketZoneRunRecord.scope == scope, MarketZoneRunRecord.instrument_key == key)
    at = _dt(_aware(at))
    if at is not None:
        query = query.where(MarketZoneRunRecord.as_of < at if strictly_before else MarketZoneRunRecord.as_of <= at)
    if settings_hash is not None:
        query = query.where(MarketZoneRunRecord.settings_hash == settings_hash)
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
        # the tracker is what the next run resumes from - only line levels resume (zones are re-detected and re-tracked
        # every night), so only theirs is kept; the zone columns above carry the rest
        tracker=json.dumps(r.get("tracker") or {}, sort_keys=True, default=str) if is_line(str(r["zone_type"])) else "{}")


async def write_run(session: AsyncSession, *, scope: str, tenant_id: Optional[int], key: str, as_of: datetime, settings: ZoneSettings,
                    rows: Sequence[Mapping[str, Any]], price: Optional[float], bars: Mapping[str, int],
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


@dataclass
class ZonesRead:
    """What zones_as_of found: the run (None when there is none), its rows, the scope read, the settings hash the caller's
    settings resolve to, and whether the platform snapshot was served as an explicit fallback for other settings."""
    run: Optional[MarketZoneRunRecord]
    rows: List[Dict[str, Any]]
    scope: Optional[str]
    settings_hash: str
    fallback: bool = False


async def zones_as_of(session: AsyncSession, key: str, at: datetime, tenant_id: Optional[int] = None, *,
                      allow_platform_fallback: bool = False) -> ZonesRead:
    """The zones known at `at` (timezone-aware) for this tenant's settings: the latest snapshot with as_of <= at built with
    exactly those settings (settings_hash) - the platform scope when the tenant's settings resolve to the platform's, else
    the tenant's own scope only. With allow_platform_fallback, a tenant without its own snapshot gets the platform one
    and `fallback` says so. Never a snapshot computed from bars after `at`, never another tenant's."""
    key = normalise_key(key)
    _aware(at)
    plat_hash = settings_hash(platform_settings())
    wanted = settings_hash(await settings_for(session, tenant_id, key)) if tenant_id is not None else plat_hash
    if wanted == plat_hash:
        run = await latest_run(session, PLATFORM_SCOPE, key, at, settings_hash=wanted)
        return ZonesRead(run, await rows_of(session, run), PLATFORM_SCOPE if run is not None else None, wanted)
    if tenant_id is None:                           # unreachable: only a tenant's settings can differ from the platform's
        raise RuntimeError("zones_as_of: settings differ from the platform's without a tenant")
    run = await latest_run(session, tenant_scope(tenant_id), key, at, settings_hash=wanted)
    if run is not None or not allow_platform_fallback:
        return ZonesRead(run, await rows_of(session, run), tenant_scope(tenant_id) if run is not None else None, wanted)
    run = await latest_run(session, PLATFORM_SCOPE, key, at, settings_hash=plat_hash)
    return ZonesRead(run, await rows_of(session, run), PLATFORM_SCOPE if run is not None else None, wanted, fallback=run is not None)


# ---------------------------------------------------------------- retention

async def retention_run_ids(session: AsyncSession, now: datetime, *, daily_days: int, weekly_days: int, tenant_idle_days: int,
                            limit: Optional[int] = None) -> List[int]:
    """Snapshot runs past their keep: older than `daily_days` and not the last run of their ISO week (per scope +
    instrument); older than `weekly_days`; every run of a tenant scope whose newest run is older than `tenant_idle_days`."""
    daily_cut, weekly_cut, idle_cut = now - pd.Timedelta(days=daily_days), now - pd.Timedelta(days=weekly_days), now - pd.Timedelta(days=tenant_idle_days)
    rows = (await session.execute(select(MarketZoneRunRecord.id, MarketZoneRunRecord.scope, MarketZoneRunRecord.instrument_key,
                                         MarketZoneRunRecord.as_of))).all()
    newest: Dict[Tuple[str, str], datetime] = {}
    week_last: Dict[Tuple[str, str, int, int], Tuple[datetime, int]] = {}
    for rid, scope, key, as_of in rows:
        t = _utc(as_of)
        if t is None:
            continue
        newest[(scope, key)] = max(newest.get((scope, key), t), t)
        iso = t.isocalendar()
        wk = (scope, key, iso[0], iso[1])
        if wk not in week_last or (t, rid) > week_last[wk]:
            week_last[wk] = (t, rid)
    keep_weekly = {rid for _, rid in week_last.values()}
    out = []
    for rid, scope, key, as_of in rows:
        t = _utc(as_of)
        if t is None:
            continue
        if scope != PLATFORM_SCOPE and newest[(scope, key)] < idle_cut:
            out.append(rid)
        elif t < weekly_cut or (t < daily_cut and rid not in keep_weekly):
            out.append(rid)
    out.sort()
    return out[:limit] if limit else out


async def delete_runs(session: AsyncSession, run_ids: Sequence[int]) -> int:
    """Delete runs and their zone rows (explicitly - not every database enforces the cascade). The caller commits."""
    from sqlalchemy import delete
    if not run_ids:
        return 0
    await session.execute(delete(MarketZoneRecord).where(MarketZoneRecord.run_id.in_(list(run_ids))))
    await session.execute(delete(MarketZoneRunRecord).where(MarketZoneRunRecord.id.in_(list(run_ids))))
    return len(run_ids)
