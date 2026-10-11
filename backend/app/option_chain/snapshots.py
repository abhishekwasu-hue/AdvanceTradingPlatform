"""OI Banner O2: the snapshot collector and the readers behind the history API.

Collection (the worker, every slot while the venue is open, for the underlyings a tenant enabled):
* one `oi_snapshots` row per (underlying, slot) - a second capture in the same slot is skipped (idempotent);
* `strike_oi_snapshots`: call/put OI, premium and IV for `OI_BANNER_COLLECT_SPAN` strikes either side of the money -
  wider than any banner window, so a tenant's own ATM range and strike step are applied on read;
* `oi_day_baselines`: the first OI seen per strike per trading day (the OI-wall baseline), written once.

Reading: a day's slots are rebuilt into chains and replayed through `oi_regime.evaluate_snapshot` with the reader's
settings, so every number (diff, signal, classes, PCR, max pain) comes from stored data and the reader's thresholds,
never from a stored verdict. The data is platform-wide reference data (a chain is the same fact for every tenant).
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Dict, List, Optional, Sequence, Tuple

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.brokers.models import OptionChain, OptionChainRow
from app.core import config
from app.db.models import OIBannerSettingRecord, OIDayBaselineRecord, OISnapshotRecord, StrikeOISnapshotRecord
from app.option_chain import oi_regime
from app.option_chain.oi_regime import BannerState, OIRegimeSettings, SnapshotPoint

logger = logging.getLogger(__name__)

try:
    from zoneinfo import ZoneInfo
    IST = ZoneInfo("Asia/Kolkata")
except Exception:  # noqa: BLE001 - tzdata missing: a fixed offset is exact for IST (no DST)
    IST = timezone(timedelta(hours=5, minutes=30))  # type: ignore[assignment]

DEFAULT_KEY = "*"          # a tenant's settings row that applies to every underlying


def normalise_underlying(name: str) -> str:
    value = " ".join(str(name or "").upper().split())
    if not value or len(value) > 30 or not all(c.isalnum() or c in " &-_" for c in value):
        raise ValueError(f"invalid underlying {name!r}")
    return value


# ---------------------------------------------------------------- settings

def platform_layer() -> Dict:
    """The operator's defaults (`OI_BANNER_DEFAULTS`, a JSON object of OIRegimeSettings fields; empty = code defaults)."""
    return dict(config.OI_BANNER_DEFAULTS or {})


async def settings_rows(session: AsyncSession, tenant_id: int, underlying: str) -> Tuple[Optional[OIBannerSettingRecord], Optional[OIBannerSettingRecord]]:
    rows = {r.underlying: r for r in await session.scalars(select(OIBannerSettingRecord).where(
        OIBannerSettingRecord.tenant_id == tenant_id, OIBannerSettingRecord.underlying.in_([DEFAULT_KEY, underlying])))}
    return rows.get(DEFAULT_KEY), rows.get(underlying)


def _overrides(row: Optional[OIBannerSettingRecord]) -> Dict:
    if row is None:
        return {}
    try:
        value = json.loads(row.overrides or "{}")
    except ValueError:
        logger.warning("oi_banner_settings %s: overrides are not JSON - ignored", row.id)
        return {}
    return value if isinstance(value, dict) else {}


async def settings_for(session: AsyncSession, tenant_id: int, underlying: str) -> OIRegimeSettings:
    """Platform defaults, then the tenant's "*" row, then the underlying's row."""
    default_row, own_row = await settings_rows(session, tenant_id, underlying)
    try:
        return oi_regime.resolve_settings(platform_layer(), _overrides(default_row), _overrides(own_row))
    except ValueError as exc:                     # a stored override no longer valid: fall back, never fail the read
        logger.warning("Tenant %s %s: OI settings invalid (%s) - platform defaults used", tenant_id, underlying, exc)
        return oi_regime.resolve_settings(platform_layer())


async def enabled_underlyings(session: AsyncSession) -> List[Tuple[int, str, str]]:
    """(tenant_id, underlying, exchange) for every tenant/underlying the collector should follow."""
    rows = await session.scalars(select(OIBannerSettingRecord).where(OIBannerSettingRecord.enabled.is_(True),
                                                                     OIBannerSettingRecord.underlying != DEFAULT_KEY))
    return [(r.tenant_id, r.underlying, r.exchange) for r in rows]


# ---------------------------------------------------------------- collection

@dataclass
class CollectResult:
    status: str                      # OK / ALREADY_EXISTS / EMPTY
    slot_start: datetime
    strikes: int = 0
    baselines: int = 0


def _window(chain: OptionChain, span: int) -> List[OptionChainRow]:
    rows = sorted(chain.rows, key=lambda r: r.strike)
    ltp = chain.underlying_ltp
    if not rows or ltp is None:
        return []
    centre = min(range(len(rows)), key=lambda i: abs(rows[i].strike - ltp))
    return rows[max(0, centre - span): centre + span + 1]


async def collect(session: AsyncSession, underlying: str, chain: OptionChain, now: datetime, *, source: str = "worker",
                  span: Optional[int] = None, slot_minutes: Optional[int] = None) -> CollectResult:
    """Stores one slot of `chain`. Idempotent per (underlying, slot); does not commit."""
    underlying = normalise_underlying(underlying)
    now = now if now.tzinfo else now.replace(tzinfo=timezone.utc)
    ist = now.astimezone(IST)
    slot = oi_regime.slot_start(ist, slot_minutes or config.OI_BANNER_SLOT_MINUTES).astimezone(timezone.utc)
    if await session.scalar(select(OISnapshotRecord.id).where(OISnapshotRecord.underlying == underlying, OISnapshotRecord.slot_start == slot)):
        return CollectResult("ALREADY_EXISTS", slot)
    rows = _window(chain, span or config.OI_BANNER_COLLECT_SPAN)
    if not rows:
        return CollectResult("EMPTY", slot)
    expiry, _ = oi_regime.compute_dte(chain.expiry, ist.date())
    try:
        async with session.begin_nested():
            snap = OISnapshotRecord(underlying=underlying, trade_date=ist.date(), slot_start=slot, captured_at=now, expiry=expiry,
                                    underlying_price=chain.underlying_ltp, strikes=len(rows), source=source[:30])
            session.add(snap)
            await session.flush()
            for r in rows:
                session.add(StrikeOISnapshotRecord(snapshot_id=snap.id, strike=float(r.strike), call_oi=r.call_oi, put_oi=r.put_oi,
                                                   call_ltp=r.call_ltp, put_ltp=r.put_ltp, call_iv=r.call_iv, put_iv=r.put_iv))
            known = set(await session.scalars(select(OIDayBaselineRecord.strike).where(
                OIDayBaselineRecord.underlying == underlying, OIDayBaselineRecord.trade_date == ist.date())))
            new = [r for r in rows if float(r.strike) not in known]
            for r in new:
                session.add(OIDayBaselineRecord(underlying=underlying, trade_date=ist.date(), strike=float(r.strike),
                                                call_oi=r.call_oi, put_oi=r.put_oi, first_seen_at=now))
            await session.flush()
    except IntegrityError:                        # another writer took the slot between the check and the insert
        return CollectResult("ALREADY_EXISTS", slot)
    return CollectResult("OK", slot, len(rows), len(new))


# ---------------------------------------------------------------- reading

@dataclass(frozen=True)
class SlotChain:
    slot_start: datetime
    captured_at: datetime
    chain: OptionChain


async def day_chains(session: AsyncSession, underlying: str, day: date) -> List[SlotChain]:
    snaps = list(await session.scalars(select(OISnapshotRecord).where(OISnapshotRecord.underlying == underlying, OISnapshotRecord.trade_date == day)
                                       .order_by(OISnapshotRecord.slot_start)))
    if not snaps:
        return []
    by_snap: Dict[int, List[OptionChainRow]] = {s.id: [] for s in snaps}
    for r in await session.scalars(select(StrikeOISnapshotRecord).where(StrikeOISnapshotRecord.snapshot_id.in_(list(by_snap)))
                                   .order_by(StrikeOISnapshotRecord.snapshot_id, StrikeOISnapshotRecord.strike)):
        by_snap[r.snapshot_id].append(OptionChainRow(strike=r.strike, call_oi=r.call_oi, put_oi=r.put_oi, call_ltp=r.call_ltp,
                                                     put_ltp=r.put_ltp, call_iv=r.call_iv, put_iv=r.put_iv))
    return [SlotChain(_utc(s.slot_start), _utc(s.captured_at), OptionChain(underlying=underlying, expiry=s.expiry.isoformat() if s.expiry else "",
                                                                         underlying_ltp=s.underlying_price, rows=by_snap[s.id]))
            for s in snaps]


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


@dataclass(frozen=True)
class TimelineEntry:
    slot_start: datetime
    captured_at: datetime
    state: BannerState
    totals: oi_regime.ChainTotals


def replay(underlying: str, slots: Sequence[SlotChain], settings: OIRegimeSettings, day: date) -> List[TimelineEntry]:
    """The day's banner, slot by slot, with `settings` (oldest first). A slot whose step or window cannot be formed
    is skipped, never guessed."""
    out: List[TimelineEntry] = []
    history: List[SnapshotPoint] = []
    previous: Optional[oi_regime.ChainTotals] = None
    for s in slots:
        step = oi_regime.resolve_strike_step(settings, s.chain)
        totals = oi_regime.summarise_chain(s.chain, step, settings.atm_range) if step else None
        if totals is None:
            continue
        window = [r for r in s.chain.rows if abs(r.strike - totals.atm_strike) <= settings.atm_range * step + 1e-9]
        state = oi_regime.evaluate_snapshot(underlying, totals, previous, history, settings, max_pain_strike=oi_regime.max_pain(window),
                                            expiry=s.chain.expiry or None, today=day)
        out.append(TimelineEntry(s.slot_start, s.captured_at, state, totals))
        history.append(SnapshotPoint(totals.diff, totals.total_put_oi, totals.total_call_oi, state.stable_signal))
        previous = totals
    return out


def entry_json(entry: TimelineEntry) -> Dict:
    st, t = entry.state, entry.totals
    return {
        "slot": entry.slot_start.astimezone(IST).isoformat(), "data_as_of": entry.captured_at.isoformat(),
        "direction": st.direction.value, "message": st.message, "put_class": st.put_class.value, "call_class": st.call_class.value,
        "signal": st.stable_signal.label, "diff": st.diff, "delta_diff": st.delta_diff,
        "total_call_oi": t.total_call_oi, "total_put_oi": t.total_put_oi, "pcr": st.pcr, "pcr_band": st.pcr_band.value if st.pcr_band else None,
        "pcr_label": st.pcr_label, "underlying_price": st.underlying_price, "atm_strike": st.atm_strike, "strike_step": t.strike_step,
        "max_pain": st.max_pain, "expiry": st.expiry.isoformat() if st.expiry else None, "dte": st.dte, "first_of_day": st.first_of_day,
    }


def staleness(latest_capture: Optional[datetime], now: datetime, settings: OIRegimeSettings, market_open: bool) -> Dict:
    """`stale` only while the venue is open (a closed market is shown as closed, not as stale)."""
    age = (now - latest_capture).total_seconds() / 60 if latest_capture else None
    stale = market_open and oi_regime.is_stale(latest_capture, now, settings.stale_after_minutes)
    return {"market_open": market_open, "stale": stale, "age_minutes": round(age, 1) if age is not None else None,
            "stale_after_minutes": settings.stale_after_minutes}


async def latest_capture(session: AsyncSession, underlying: str) -> Optional[datetime]:
    value = await session.scalar(select(OISnapshotRecord.captured_at).where(OISnapshotRecord.underlying == underlying)
                                 .order_by(OISnapshotRecord.slot_start.desc()).limit(1))
    return _utc(value) if value else None


def strike_series(slots: Sequence[SlotChain], settings: OIRegimeSettings) -> List[Dict]:
    """Per-strike OI history for the strikes in the latest slot's window (the per-strike OI chart)."""
    if not slots:
        return []
    last = slots[-1].chain
    step = oi_regime.resolve_strike_step(settings, last)
    totals = oi_regime.summarise_chain(last, step, settings.atm_range) if step else None
    if totals is None:
        return []
    wanted = [s for s, _, _ in totals.strikes]
    series: Dict[float, List[Dict]] = {k: [] for k in wanted}
    for s in slots:
        for r in s.chain.rows:
            if r.strike in series:
                series[r.strike].append({"slot": s.slot_start.astimezone(IST).isoformat(), "call_oi": r.call_oi, "put_oi": r.put_oi})
    return [{"strike": k, "points": v} for k, v in series.items()]


async def baselines(session: AsyncSession, underlying: str, day: date) -> Dict[float, Tuple[Optional[float], Optional[float]]]:
    rows = await session.scalars(select(OIDayBaselineRecord).where(OIDayBaselineRecord.underlying == underlying, OIDayBaselineRecord.trade_date == day))
    return {r.strike: (r.call_oi, r.put_oi) for r in rows}


__all__ = ["collect", "CollectResult", "day_chains", "replay", "entry_json", "staleness", "latest_capture", "strike_series",
           "baselines", "settings_for", "enabled_underlyings", "normalise_underlying", "platform_layer", "IST", "DEFAULT_KEY"]
