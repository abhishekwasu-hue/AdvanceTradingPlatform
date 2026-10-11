"""OI Banner O4: alerts on banner state changes, through the existing notification service and alert dispatcher.

Rules (spec section 2):
* only on a state change, never on every snapshot: each tenant's banner state is stored per slot (`oi_banner_states`)
  and the new slot is compared with the previous one;
* alert types: direction change, confirmed (hysteresis) flip, Strong <-> Weakening, PCR band change (entering
  Oversold / Overbought is WARNING), max pain moved by N strikes, OI wall formed / broken at the psychological level in
  the stable direction, DTE milestones, collector stale (ops);
* per-type cooldown, a dedupe key (tenant, underlying, type, new state, slot) unique in `oi_alert_log`, quiet hours
  and snooze / mute-today hold an alert back (logged with the reason, not sent);
* the venue being closed suppresses evaluation altogether (the worker only evaluates in session).

Messages state activity and bias, never "buy / sell / target / recommended" (SCREENER_SPEC section 6) - checked by a
test. Severity maps onto the existing routing floors (`severity_reaches`): INFO for ordinary changes, WARNING for an
extreme PCR band and for the collector going stale.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from typing import Dict, List, Optional, Sequence

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import NotificationSeverity, NotificationType
from app.db.models import NotificationRecord, OIAlertLogRecord, OIBannerStateRecord
from app.notifications.service import notify
from app.option_chain import oi_regime, snapshots
from app.option_chain.oi_regime import BannerState, Direction, OIAlertSettings, PcrBand
from app.option_chain.snapshots import IST

logger = logging.getLogger(__name__)

SCHEMA = "oi_banner.v1"
EXTREME_BANDS = (PcrBand.OVERSOLD.value, PcrBand.OVERBOUGHT.value)


@dataclass(frozen=True)
class StateView:
    direction: str
    stable_direction: str
    stable_strength: Optional[str]
    pcr_band: Optional[str]
    max_pain: Optional[float]
    max_pain_ref: Optional[float]
    dte: Optional[int]
    wall: Optional[str]


@dataclass(frozen=True)
class AlertEvent:
    alert_type: str
    old_state: Optional[str]
    new_state: str
    severity: NotificationSeverity


def view_of(row: OIBannerStateRecord) -> StateView:
    return StateView(row.direction, row.stable_direction, row.stable_strength, row.pcr_band, row.max_pain, row.max_pain_ref, row.dte, row.wall)


def detect(prev: Optional[StateView], cur: StateView, settings: OIAlertSettings, strike_step: Optional[float]) -> List[AlertEvent]:
    """The alerts a move from `prev` to `cur` raises (pure). No previous state (the first slot) raises only a DTE
    milestone: there is nothing to compare a direction with yet."""
    out: List[AlertEvent] = []
    want = set(settings.types)

    def add(kind: str, old: Optional[str], new: str, severity: NotificationSeverity = NotificationSeverity.INFO) -> None:
        if kind in want:
            out.append(AlertEvent(kind, old, new, severity))

    if prev is None:
        if cur.dte is not None and cur.dte in settings.dte_milestones:
            add("DTE", None, f"DTE {cur.dte}")
        return out
    if cur.direction != prev.direction:
        add("DIRECTION_CHANGE", prev.direction, cur.direction)
    directional = (Direction.BULLISH.value, Direction.BEARISH.value)
    if cur.stable_direction != prev.stable_direction and cur.stable_direction in directional and prev.stable_direction in directional:
        add("STABLE_FLIP", prev.stable_direction, cur.stable_direction)
    elif cur.stable_direction == prev.stable_direction and cur.stable_strength != prev.stable_strength and cur.stable_strength:
        add("STRENGTH_CHANGE", f"{prev.stable_direction} {prev.stable_strength}", f"{cur.stable_direction} {cur.stable_strength}")
    if cur.pcr_band != prev.pcr_band and cur.pcr_band:
        add("PCR_BAND", prev.pcr_band, cur.pcr_band,
            NotificationSeverity.WARNING if cur.pcr_band in EXTREME_BANDS else NotificationSeverity.INFO)
    ref = prev.max_pain_ref if prev.max_pain_ref is not None else prev.max_pain
    if strike_step and ref is not None and cur.max_pain is not None and abs(cur.max_pain - ref) >= settings.max_pain_strikes * strike_step - 1e-9:
        add("MAX_PAIN_MOVE", f"{ref:g}", f"{cur.max_pain:g}")
    if cur.wall != prev.wall:
        if cur.wall:
            add("OI_WALL", prev.wall, f"formed {cur.wall}")
        elif prev.wall:
            add("OI_WALL", prev.wall, f"broken {prev.wall}")
    if cur.dte != prev.dte and cur.dte is not None and cur.dte in settings.dte_milestones:
        add("DTE", None if prev.dte is None else f"DTE {prev.dte}", f"DTE {cur.dte}")
    return out


def in_quiet_hours(moment_ist: datetime, settings: OIAlertSettings) -> bool:
    if not settings.quiet_start or not settings.quiet_end:
        return False
    start, end = time.fromisoformat(settings.quiet_start), time.fromisoformat(settings.quiet_end)
    now = moment_ist.time()
    return start <= now < end if start <= end else (now >= start or now < end)


def _fmt(value: Optional[float]) -> str:
    return "—" if value is None else f"{value:,.2f}".rstrip("0").rstrip(".")


def render(underlying: str, event: AlertEvent, state: BannerState, slot: datetime, data_as_of: datetime) -> Dict:
    """Title (the short form for SMS / push), message (the spec's template) and the versioned webhook payload."""
    stamp = slot.astimezone(IST).strftime("%d %b %H:%M IST")
    old = event.old_state or "—"
    title = f"{underlying} OI: {event.alert_type.replace('_', ' ').lower()} {old} → {event.new_state}"[:200]
    message = (f"{underlying} OI banner: {old} → {event.new_state}. {state.message}. Put: {state.put_class.value}. "
               f"Call: {state.call_class.value}. PCR {_fmt(state.pcr)} ({state.pcr_label}). Max pain {_fmt(state.max_pain)}. "
               f"DTE {state.dte if state.dte is not None else '—'}. Data as of {stamp}.")
    payload = {
        "schema": SCHEMA, "underlying": underlying, "alert_type": event.alert_type, "old_state": event.old_state, "new_state": event.new_state,
        "snapshot": {"direction": state.direction.value, "message": state.message, "put_class": state.put_class.value,
                     "call_class": state.call_class.value, "signal": state.stable_signal.label, "diff": state.diff, "pcr": state.pcr,
                     "pcr_band": state.pcr_band.value if state.pcr_band else None, "max_pain": state.max_pain, "dte": state.dte,
                     "underlying_price": state.underlying_price, "atm_strike": state.atm_strike},
        "data_timestamps": {"slot": slot.isoformat(), "data_as_of": data_as_of.isoformat()},
    }
    return {"title": title, "message": message, "payload": payload}


def wall_state(rows, baselines: Dict[float, tuple], state: BannerState, strike_step: float, settings: oi_regime.OIRegimeSettings) -> Optional[str]:
    """"CE@level" / "PE@level" when an OI wall stands at the psychological level in the stable direction, else None."""
    direction = state.stable_signal.direction.value
    if direction not in (Direction.BULLISH.value, Direction.BEARISH.value):
        return None
    level = oi_regime.find_psychological_level(state.underlying_price, direction, oi_regime.psychological_round_to(settings, strike_step))
    target = round(level / strike_step) * strike_step
    side_index = 0 if direction == Direction.BEARISH.value else 1
    base = baselines.get(float(target))
    ok, detail = oi_regime.check_oi_wall_confirmation(rows, level, direction, strike_step, base[side_index] if base else None)
    return f"{detail.side}@{detail.target_strike:g}" if ok else None


# ---------------------------------------------------------------- service

async def _last_sent(session: AsyncSession, tenant_id: int, underlying: str, alert_type: str) -> Optional[datetime]:
    value = await session.scalar(select(OIAlertLogRecord.created_at).where(
        OIAlertLogRecord.tenant_id == tenant_id, OIAlertLogRecord.underlying == underlying, OIAlertLogRecord.alert_type == alert_type,
        OIAlertLogRecord.status == "SENT").order_by(OIAlertLogRecord.created_at.desc()).limit(1))
    return value if value is None or value.tzinfo else value.replace(tzinfo=timezone.utc)


async def _log(session: AsyncSession, **values) -> bool:
    """Writes the decision; False when the dedupe key was already taken (the alert was handled before)."""
    try:
        async with session.begin_nested():
            session.add(OIAlertLogRecord(**values))
            await session.flush()
        return True
    except IntegrityError:
        return False


async def _decide(session: AsyncSession, tenant_id: int, underlying: str, event: AlertEvent, slot: datetime, now: datetime,
                  alerts: OIAlertSettings, snoozed_until: Optional[datetime], rendered: Dict) -> str:
    key = f"{tenant_id}:{underlying}:{event.alert_type}:{event.new_state}:{slot.isoformat()}"[:200]
    if await session.scalar(select(OIAlertLogRecord.id).where(OIAlertLogRecord.dedupe_key == key)):
        return "DUPLICATE"
    status = "SENT"
    last = await _last_sent(session, tenant_id, underlying, event.alert_type)
    if snoozed_until is not None and (snoozed_until if snoozed_until.tzinfo else snoozed_until.replace(tzinfo=timezone.utc)) > now:
        status = "SNOOZED"
    elif in_quiet_hours(now.astimezone(IST), alerts):
        status = "QUIET"
    elif last is not None and now - last < timedelta(minutes=alerts.cooldown_minutes):
        status = "COOLDOWN"
    record: Optional[NotificationRecord] = None
    if not await _log(session, tenant_id=tenant_id, underlying=underlying, alert_type=event.alert_type, old_state=event.old_state,
                      new_state=event.new_state, slot_start=slot, dedupe_key=key, status=status, created_at=now):
        return "DUPLICATE"
    if status == "SENT":
        kind = NotificationType.OI_COLLECTOR if event.alert_type == "COLLECTOR_STALE" else NotificationType.OI_BANNER
        record = await notify(session, tenant_id, kind, rendered["title"], rendered["message"], severity=event.severity,
                              metadata_json=json.dumps(rendered["payload"], default=str))
        row = await session.scalar(select(OIAlertLogRecord).where(OIAlertLogRecord.dedupe_key == key))
        if row is not None:
            row.notification_id = record.id
    await session.commit()
    return status


async def evaluate_tenant(session: AsyncSession, tenant_id: int, underlying: str, now: datetime, *, market_open: bool = True) -> List[str]:
    """Stores this tenant's banner state for the latest slot (once) and raises the alerts its changes call for.
    Returns the decisions taken ("SENT", "COOLDOWN", ...). Does nothing for a tenant with alerts off."""
    settings = await snapshots.settings_for(session, tenant_id, underlying)
    if not settings.alerts.enabled or not market_open:
        return []
    _, own = await snapshots.settings_rows(session, tenant_id, underlying)
    snoozed = own.snoozed_until if own is not None else None
    day = now.astimezone(IST).date()
    decisions: List[str] = []
    flags = snapshots.staleness(await snapshots.latest_capture(session, underlying), now, settings, market_open)
    if flags["stale"] and "COLLECTOR_STALE" in settings.alerts.types:
        stale_event = AlertEvent("COLLECTOR_STALE", None, f"stale {day.isoformat()}", NotificationSeverity.WARNING)
        age = flags["age_minutes"]
        rendered = {"title": f"{underlying} OI collector stale", "payload": {"schema": SCHEMA, "underlying": underlying, "alert_type": "COLLECTOR_STALE",
                                                                             "age_minutes": age, "data_timestamps": {"checked_at": now.isoformat()}},
                    "message": f"{underlying} OI snapshots are {('%.0f minutes old' % age) if age is not None else 'missing'} while the market is open; "
                               "the banner and OI gates show stale data until the collector recovers."}
        decisions.append(await _decide(session, tenant_id, underlying, stale_event, datetime.combine(day, time(), IST).astimezone(timezone.utc), now,
                                       settings.alerts, snoozed, rendered))
    slots = await snapshots.day_chains(session, underlying, day)
    timeline = snapshots.replay(underlying, slots, settings, day)
    if not timeline:
        return decisions
    latest = timeline[-1]
    if await session.scalar(select(OIBannerStateRecord.id).where(OIBannerStateRecord.tenant_id == tenant_id, OIBannerStateRecord.underlying == underlying,
                                                                 OIBannerStateRecord.slot_start == latest.slot_start)):
        return decisions                                   # this slot was evaluated already
    prev_row = await session.scalar(select(OIBannerStateRecord).where(
        OIBannerStateRecord.tenant_id == tenant_id, OIBannerStateRecord.underlying == underlying,
        OIBannerStateRecord.slot_start < latest.slot_start, OIBannerStateRecord.slot_start >= datetime.combine(day, time(), IST).astimezone(timezone.utc))
        .order_by(OIBannerStateRecord.slot_start.desc()).limit(1))
    state, step = latest.state, latest.totals.strike_step
    window_rows = slots[-1].chain.rows
    wall = wall_state(window_rows, await snapshots.baselines(session, underlying, day), state, step, settings)
    prev = view_of(prev_row) if prev_row is not None else None
    cur = StateView(state.direction.value, state.stable_signal.direction.value,
                    state.stable_signal.strength.value if state.stable_signal.strength else None,
                    state.pcr_band.value if state.pcr_band else None, state.max_pain, None, state.dte, wall)
    events = detect(prev, cur, settings.alerts, step)
    moved = any(e.alert_type == "MAX_PAIN_MOVE" for e in events)
    ref = state.max_pain if (prev is None or moved) else (prev.max_pain_ref if prev.max_pain_ref is not None else prev.max_pain)
    session.add(OIBannerStateRecord(tenant_id=tenant_id, underlying=underlying, slot_start=latest.slot_start, direction=cur.direction,
                                    stable_direction=cur.stable_direction, stable_strength=cur.stable_strength, pcr_band=cur.pcr_band,
                                    max_pain=cur.max_pain, max_pain_ref=ref, dte=cur.dte, wall=wall, message=state.message[:300], created_at=now))
    try:
        await session.commit()
    except IntegrityError:                                  # another evaluation stored this slot first
        await session.rollback()
        return decisions
    for event in events:
        decisions.append(await _decide(session, tenant_id, underlying, event, latest.slot_start, now, settings.alerts, snoozed,
                                       render(underlying, event, state, latest.slot_start, latest.captured_at)))
    return decisions


async def followers(session: AsyncSession, underlying: str) -> Sequence[int]:
    return sorted({t for t, u, _ in await snapshots.enabled_underlyings(session) if u == underlying})


__all__ = ["detect", "render", "in_quiet_hours", "wall_state", "evaluate_tenant", "followers", "StateView", "AlertEvent", "SCHEMA"]
