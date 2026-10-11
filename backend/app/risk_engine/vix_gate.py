"""Z5 (Trade port): the India VIX gate - risk by default on a fearful market. Behind the flag `vix_gate`, off by default.

Ported from the Trade repo `check_vix_spike_halt.py` (`run_vix_spike_check`, `upstox_api.fetch_india_vix` and
`fetch_india_vix_prev_close`) and `trading_engine.check_vix_spike_halt`:

* **The reading (Trade parity).** India VIX's live last price and the close of the last completed trading session before
  today (Trade reads the latest daily candle before today), stamped with today's IST date. `capture` takes one through
  the organisation's data path: with `byob_data_tier` on, only the acting user's own entitled session (BT1, Tier A);
  else the organisation's first usable broker session. The trading worker captures one per organisation with the flag
  on, from `VIX_GATE_CHECK_AT` (09:20 IST, five minutes after the open) every `VIX_GATE_CAPTURE_MINUTES` while NSE is
  open. A failed read is stored too, with its reason and no price.
* **Spike pause (Trade parity).** A valid reading today whose change is at or above `VIX_GATE_SPIKE_PCT` (5, ">=")
  pauses new **bullish** entries on `VIX_GATE_UNDERLYINGS` (NIFTY, BANKNIFTY, SENSEX - one India VIX for all three) for
  the rest of the IST day, PAPER and LIVE alike. Bearish entries and every other symbol are not touched.
* **Timing (Trade parity).** Before `VIX_GATE_CHECK_AT` the gate is open (Trade's check has not run). After it plus
  `VIX_GATE_GRACE_MINUTES`, on a weekday, no valid reading today counts as a spike (Trade's fail-safe: a VIX it could
  not read halts the day). In between, with no reading yet, it is open with a note.
* **High level size cut (ATP addition).** The latest fresh valid reading at or above `VIX_GATE_HIGH_LEVEL` (20) cuts the
  risk per trade of new entries on those underlyings by `VIX_GATE_SIZE_CUT_PCT` % (50). A 100% cut refuses the entry.

Differences from Trade, on purpose: a spike in any reading of the day pauses the rest of the day (Trade reads once at
09:20); ATP has no "directional" trade type that Trade exempts; no Telegram message per run. Exits never come here:
stops, targets, time exits and "Exit now" are not gated (ADR-0004). Nothing here places or changes an order.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import config as app_config
from app.db.models import MarketSnapshotRecord
from app.instruments.master import underlying_of
from app.market_data.calendar import IST, to_ist, trading_day_start

FLAG = "vix_gate"
VIX_SYMBOL = "INDIA VIX"
VIX_EXCHANGE = "NSE"
KIND = "VIX"                       # market_snapshots.kind of a live India VIX reading (CUE rows are the Copilot's context)
DAILY_LOOKBACK_DAYS = 10


@dataclass
class VixVerdict:
    reasons: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    size_multiplier: float = 1.0
    state: str = "off"            # off | out_of_scope | before_check | waiting | no_reading | normal | high | spike


def spike_decision(current: Optional[float], prev_close: Optional[float], threshold_pct: float) -> Tuple[bool, Optional[float]]:
    """(halted, % change). Trade `run_vix_spike_check`: a missing price or previous close (or one not above zero) halts
    (fail-safe); otherwise halted when the change is at or above the threshold."""
    if current is None or prev_close is None or prev_close <= 0:
        return True, None
    pct = (float(current) - float(prev_close)) / float(prev_close) * 100.0
    return pct >= threshold_pct, pct


def in_scope(symbol: str, underlyings: Sequence[str]) -> bool:
    return underlying_of(symbol) in {u.upper() for u in underlyings}


def is_bullish(direction: Optional[str]) -> bool:
    """Trade's gate stops plain BULLISH trades only; ATP's long entry on the underlying is that."""
    value = getattr(direction, "value", direction)
    return str(value or "").upper() in ("LONG", "BULLISH")


def _valid(reading: Dict[str, Any]) -> bool:
    last, prev = reading.get("last"), reading.get("prev_close")
    return last is not None and prev is not None and float(prev) > 0 and float(last) > 0


def check_time() -> time:
    try:
        hh, mm = app_config.VIX_GATE_CHECK_AT.split(":")
        return time(int(hh), int(mm))
    except (ValueError, AttributeError):
        return time(9, 20)


def evaluate(readings: Sequence[Dict[str, Any]], *, symbol: str, direction: Optional[str], now: datetime,
             spike_pct: float, high_level: float, size_cut_pct: float, max_age_minutes: int, underlyings: Sequence[str],
             check_at: Optional[time] = None, grace_minutes: int = 10) -> VixVerdict:
    """The verdict for one new entry from the day's India VIX readings (each {last, prev_close, captured_at, error}). Pure."""
    if not in_scope(symbol, underlyings):
        return VixVerdict(state="out_of_scope")
    at = check_at or check_time()
    ist = to_ist(now)
    start = ist.replace(hour=at.hour, minute=at.minute, second=0, microsecond=0)
    if ist < start:
        return VixVerdict(notes=[f"India VIX gate: the day's check runs from {start.strftime('%H:%M')} IST - open until then"],
                          state="before_check")
    valid = [r for r in readings if _valid(r)]
    verdict = VixVerdict(state="normal")
    moved: Optional[str] = None
    if not valid:
        if ist < start + timedelta(minutes=grace_minutes) or ist.weekday() >= 5:
            return VixVerdict(notes=["India VIX gate: no valid India VIX reading yet today - open for now"], state="waiting")
        why = next((str(r.get("error")) for r in reversed(readings) if r.get("error")), "no reading was captured")
        verdict.state = "no_reading"
        moved = f"unknown (India VIX could not be read: {why})"
    else:
        for r in valid:
            halted, pct = spike_decision(r.get("last"), r.get("prev_close"), spike_pct)
            if halted:
                verdict.state = "spike"
                moved = f"{pct:+.1f}%" if pct is not None else "unknown"
                break
    if moved is not None:
        if is_bullish(direction):
            verdict.reasons.append(f"India VIX gate: India VIX moved {moved} against the previous close today (limit {spike_pct:g}%) - "
                                   f"new bullish entries on {underlying_of(symbol)} are paused for the rest of the day; "
                                   "bearish entries and exits are not affected")
        else:
            verdict.notes.append(f"India VIX gate: VIX moved {moved} today; only bullish entries are paused")
    if valid:
        latest = max(valid, key=lambda r: r["captured_at"])
        fresh = now - latest["captured_at"] <= timedelta(minutes=max_age_minutes)
        level = float(latest["last"])
        if high_level > 0 and fresh and level >= high_level:
            if verdict.state != "spike":
                verdict.state = "high"
            cut = min(max(float(size_cut_pct), 0.0), 100.0)
            if cut >= 100.0:
                verdict.reasons.append(f"India VIX gate: India VIX {level:.2f} is at or above {high_level:g} - size cut 100%, no new entries "
                                       f"on {underlying_of(symbol)}")
            elif cut > 0:
                verdict.size_multiplier *= 1.0 - cut / 100.0
                verdict.notes.append(f"India VIX gate: India VIX {level:.2f} is at or above {high_level:g} - risk per trade cut {cut:g}%")
    return verdict


# --- readings ------------------------------------------------------------------------------------------------------------

def reading_from(ltp: Optional[float], day_bars: Sequence[Any], now: datetime) -> Dict[str, Any]:
    """Trade's two numbers: the live price and the close of the last completed session before today (IST). Pure."""
    today = to_ist(now).date()
    prev = None
    for bar in day_bars:
        ts = bar.timestamp if bar.timestamp.tzinfo else bar.timestamp.replace(tzinfo=timezone.utc)
        if ts.astimezone(IST).date() < today:
            prev = float(bar.close)
    last = float(ltp) if ltp is not None else None
    change = round((last / prev - 1.0) * 100.0, 2) if last is not None and prev else None
    return {"last": last, "prev_close": prev, "change_pct": change, "date": today.isoformat()}


async def capture(session: AsyncSession, tenant_id: int, service: Any, *, now: Optional[datetime] = None, source: str = "broker",
                  error: Optional[str] = None) -> MarketSnapshotRecord:
    """Reads India VIX through `service` (a MarketDataService on the entitled broker session; None when there is no
    session) and stores the reading. A failure is stored too (no price, the reason), so the gate can say why."""
    now = now or datetime.now(timezone.utc)
    ltp: Optional[float] = None
    bars: List[Any] = []
    if service is None:
        error = error or "no entitled broker session"
    else:
        try:
            ltp = float(await service.get_ltp(VIX_SYMBOL, VIX_EXCHANGE, now=now))
            bars = list(await service.get_candles(VIX_SYMBOL, VIX_EXCHANGE, "day", now=now))
        except Exception as exc:  # noqa: BLE001 - stored as a failed reading (the gate treats it as Trade does)
            error = f"{type(exc).__name__}: {exc}"[:150]
    reading = reading_from(ltp, bars, now)
    if error is None and reading["prev_close"] is None:
        error = "no completed session before today in the daily candles"
    reading["error"] = error
    row = MarketSnapshotRecord(tenant_id=tenant_id, kind=KIND, symbol=VIX_SYMBOL, exchange=VIX_EXCHANGE, timeframe="live", source=source[:40],
                               last_price=reading["last"], change_pct=reading["change_pct"], payload_json=json.dumps(reading), captured_at=now)
    session.add(row)
    await session.commit()
    return row


async def capture_for_tenant(session: AsyncSession, tenant_id: int, user: Any, *, org_adapter: Any, market_data_factory: Any,
                             now: Optional[datetime] = None) -> MarketSnapshotRecord:
    """One reading for an organisation through its entitled data path: with `byob_data_tier` on, the acting user's own
    Tier A session for India VIX (never a teammate's); otherwise the organisation's usable session `org_adapter`."""
    from app.market_data import entitlement
    from app.platform.controls import flag_enabled
    now = now or datetime.now(timezone.utc)
    adapter, source, error = org_adapter, str(getattr(org_adapter, "name", "broker")), None
    if user is not None and await flag_enabled(session, entitlement.FLAG, tenant_id):
        from app.brokers.token_lifecycle import build_adapter
        ent = await entitlement.resolve(session, user, VIX_SYMBOL, VIX_EXCHANGE, register=False)
        if ent.live and ent.record is not None:
            adapter, source = build_adapter(ent.record), ent.record.broker_name
        else:
            adapter, error = None, (ent.reason or "no Tier A session for India VIX")
    elif adapter is None:
        error = "no usable broker session for the organisation"
    service = market_data_factory(adapter, lookback_days=DAILY_LOOKBACK_DAYS) if adapter is not None else None
    return await capture(session, tenant_id, service, now=now, source=source, error=error)


def due(last: Optional[datetime], now: datetime) -> bool:
    """A reading is due on weekdays from the check time on, every VIX_GATE_CAPTURE_MINUTES."""
    ist = to_ist(now)
    if ist.weekday() >= 5 or ist.time() < check_time():
        return False
    return last is None or to_ist(last).date() != ist.date() or now - last >= timedelta(minutes=app_config.VIX_GATE_CAPTURE_MINUTES)


async def enabled(session: AsyncSession, tenant_id: int) -> bool:
    from app.platform.controls import flag_enabled
    return await flag_enabled(session, FLAG, tenant_id)


def _utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def readings_today(session: AsyncSession, tenant_id: int, now: Optional[datetime] = None) -> List[Dict[str, Any]]:
    """The organisation's live India VIX readings of the IST day (failed reads included, with their reason)."""
    now = now or _now()
    today = to_ist(now).date().isoformat()
    rows = await session.scalars(select(MarketSnapshotRecord).where(
        MarketSnapshotRecord.tenant_id == tenant_id, MarketSnapshotRecord.kind == KIND, MarketSnapshotRecord.symbol == VIX_SYMBOL,
        MarketSnapshotRecord.captured_at >= trading_day_start(now), MarketSnapshotRecord.captured_at <= now).order_by(MarketSnapshotRecord.captured_at))
    out = []
    for row in rows:
        try:
            payload = json.loads(row.payload_json or "{}")
        except ValueError:
            payload = {}
        if str(payload.get("date") or "")[:10] != today:
            continue
        out.append({"last": payload.get("last"), "prev_close": payload.get("prev_close"), "change_pct": payload.get("change_pct"),
                    "error": payload.get("error"), "captured_at": _utc(row.captured_at)})
    return out


def _evaluate(readings: Sequence[Dict[str, Any]], symbol: str, direction: Optional[str], now: datetime) -> VixVerdict:
    return evaluate(readings, symbol=symbol, direction=direction, now=now, spike_pct=app_config.VIX_GATE_SPIKE_PCT,
                    high_level=app_config.VIX_GATE_HIGH_LEVEL, size_cut_pct=app_config.VIX_GATE_SIZE_CUT_PCT,
                    max_age_minutes=app_config.VIX_GATE_MAX_AGE_MINUTES, underlyings=app_config.VIX_GATE_UNDERLYINGS,
                    grace_minutes=app_config.VIX_GATE_GRACE_MINUTES)


async def check(session: AsyncSession, tenant_id: int, *, symbol: str, direction: Optional[str], now: Optional[datetime] = None) -> VixVerdict:
    """The gate's verdict for a new entry, or an empty one while the flag is off."""
    if not await enabled(session, tenant_id):
        return VixVerdict()
    now = now or _now()
    if not in_scope(symbol, app_config.VIX_GATE_UNDERLYINGS):
        return VixVerdict(state="out_of_scope")
    return _evaluate(await readings_today(session, tenant_id, now), symbol, direction, now)


async def status(session: AsyncSession, tenant_id: int, now: Optional[datetime] = None) -> Dict[str, Any]:
    """What the gate sees today, for the ticket's status strip."""
    now = now or _now()
    on = await enabled(session, tenant_id)
    readings = await readings_today(session, tenant_id, now) if on else []
    probe = _evaluate(readings, app_config.VIX_GATE_UNDERLYINGS[0] if app_config.VIX_GATE_UNDERLYINGS else "NIFTY", "LONG", now) if on else VixVerdict()
    latest = readings[-1] if readings else None
    return {"enabled": on, "state": probe.state, "underlyings": list(app_config.VIX_GATE_UNDERLYINGS),
            "latest": {"last": latest["last"], "prev_close": latest["prev_close"], "change_pct": latest["change_pct"], "error": latest["error"],
                       "captured_at": latest["captured_at"].isoformat()} if latest else None,
            "bullish_paused": probe.state in ("spike", "no_reading"), "size_multiplier": probe.size_multiplier,
            "settings": {"spike_pct": app_config.VIX_GATE_SPIKE_PCT, "high_level": app_config.VIX_GATE_HIGH_LEVEL,
                         "size_cut_pct": app_config.VIX_GATE_SIZE_CUT_PCT, "max_age_minutes": app_config.VIX_GATE_MAX_AGE_MINUTES,
                         "check_at": app_config.VIX_GATE_CHECK_AT, "grace_minutes": app_config.VIX_GATE_GRACE_MINUTES},
            "messages": probe.reasons + probe.notes}
