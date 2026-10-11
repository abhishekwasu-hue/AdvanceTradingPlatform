"""Z5 (Trade port): the India VIX gate - risk by default on a fearful market. Behind the flag `vix_gate`, off by default.

Ported from the Trade repo `check_vix_spike_halt.py` (`run_vix_spike_check`) and `trading_engine.check_vix_spike_halt`:

* **Spike pause (Trade parity).** India VIX's change against the previous close is measured; at or above
  `VIX_GATE_SPIKE_PCT` (default 5, the Trade threshold, ">=" not ">") new **bullish** entries on the index underlyings
  in `VIX_GATE_UNDERLYINGS` (default NIFTY, BANKNIFTY, SENSEX - one India VIX for all three) are paused for the rest of
  the IST day, PAPER and LIVE alike. Bearish entries and every other symbol are not touched (a VIX spike usually comes
  with a fall: a bearish entry is not a position against the panic). A reading whose price or previous close is missing
  or not positive counts as a spike (Trade's fail-safe when the VIX fetch fails).
* **High level size cut (ATP addition).** When the latest fresh reading today is at or above `VIX_GATE_HIGH_LEVEL`
  (default 20, "high fear" in the knowledge base), the risk per trade of every new entry on those underlyings is cut by
  `VIX_GATE_SIZE_CUT_PCT` % (default 50). A 100% cut refuses the entry.
* **No reading today**: the gate is open, with a note (Trade parity: before the day's check has run, nothing is
  halted).

Differences from Trade, on purpose: the readings come from the organisation's market memory (India VIX captured every
15 minutes through its own broker session, `app/ai/market_memory.py`), not a 09:20 cron; a spike seen in any reading of
the day pauses the rest of the day (Trade checks once, five minutes after the open). ATP has no "directional" trade
type that Trade exempts. Exits never come here: stops, targets, time exits and "Exit now" are not gated (ADR-0004).
Nothing here places or changes an order; the guardian applies the verdict before sizing.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import config as app_config
from app.db.models import MarketSnapshotRecord
from app.instruments.master import underlying_of
from app.market_data.calendar import to_ist, trading_day_start

FLAG = "vix_gate"
CUE_SYMBOL = "INDIA VIX"


@dataclass
class VixVerdict:
    reasons: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    size_multiplier: float = 1.0
    state: str = "off"            # off | out_of_scope | no_reading | normal | high | spike


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


def evaluate(readings: Sequence[Dict[str, Any]], *, symbol: str, direction: Optional[str], now: datetime,
             spike_pct: float, high_level: float, size_cut_pct: float, max_age_minutes: int, underlyings: Sequence[str]) -> VixVerdict:
    """The verdict for one new entry from the day's India VIX readings (each {last, prev_close, captured_at}). Pure."""
    if not in_scope(symbol, underlyings):
        return VixVerdict(state="out_of_scope")
    if not readings:
        return VixVerdict(notes=["India VIX gate: no India VIX reading today - the gate is open"], state="no_reading")
    verdict = VixVerdict(state="normal")
    spikes = [(r, pct) for r in readings for halted, pct in [spike_decision(r.get("last"), r.get("prev_close"), spike_pct)] if halted]
    if spikes:
        verdict.state = "spike"
        _, pct = spikes[0]
        moved = f"{pct:+.1f}%" if pct is not None else "unknown (no valid India VIX price)"
        if is_bullish(direction):
            verdict.reasons.append(f"India VIX gate: India VIX moved {moved} against the previous close today (limit {spike_pct:g}%) - "
                                   f"new bullish entries on {underlying_of(symbol)} are paused for the rest of the day; "
                                   "bearish entries and exits are not affected")
        else:
            verdict.notes.append(f"India VIX gate: VIX moved {moved} today; only bullish entries are paused")
    latest = max(readings, key=lambda r: r["captured_at"])
    fresh = now - latest["captured_at"] <= timedelta(minutes=max_age_minutes)
    level = latest.get("last")
    if high_level > 0 and fresh and level is not None and float(level) >= high_level:
        if verdict.state != "spike":
            verdict.state = "high"
        cut = min(max(float(size_cut_pct), 0.0), 100.0)
        if cut >= 100.0:
            verdict.reasons.append(f"India VIX gate: India VIX {float(level):.2f} is at or above {high_level:g} - size cut 100%, no new entries "
                                   f"on {underlying_of(symbol)}")
        elif cut > 0:
            verdict.size_multiplier *= 1.0 - cut / 100.0
            verdict.notes.append(f"India VIX gate: India VIX {float(level):.2f} is at or above {high_level:g} - risk per trade cut {cut:g}%")
    return verdict


async def enabled(session: AsyncSession, tenant_id: int) -> bool:
    from app.platform.controls import flag_enabled
    return await flag_enabled(session, FLAG, tenant_id)


def _utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


async def readings_today(session: AsyncSession, tenant_id: int, now: Optional[datetime] = None) -> List[Dict[str, Any]]:
    """The organisation's India VIX readings of the IST day whose daily bar is today's (a reading of yesterday's bar
    says nothing about today's move)."""
    now = now or datetime.now(timezone.utc)
    today = to_ist(now).date().isoformat()
    rows = await session.scalars(select(MarketSnapshotRecord).where(
        MarketSnapshotRecord.tenant_id == tenant_id, MarketSnapshotRecord.kind == "CUE", MarketSnapshotRecord.symbol == CUE_SYMBOL,
        MarketSnapshotRecord.captured_at >= trading_day_start(now), MarketSnapshotRecord.captured_at <= now).order_by(MarketSnapshotRecord.captured_at))
    out = []
    for row in rows:
        try:
            payload = json.loads(row.payload_json or "{}")
        except ValueError:
            payload = {}
        if str(payload.get("date") or "")[:10] != today:
            continue
        out.append({"last": payload.get("last", row.last_price), "prev_close": payload.get("prev_close"), "change_pct": row.change_pct,
                    "captured_at": _utc(row.captured_at)})
    return out


async def check(session: AsyncSession, tenant_id: int, *, symbol: str, direction: Optional[str], now: Optional[datetime] = None) -> VixVerdict:
    """The gate's verdict for a new entry, or an empty one while the flag is off."""
    if not await enabled(session, tenant_id):
        return VixVerdict()
    now = now or datetime.now(timezone.utc)
    if not in_scope(symbol, app_config.VIX_GATE_UNDERLYINGS):
        return VixVerdict(state="out_of_scope")
    return evaluate(await readings_today(session, tenant_id, now), symbol=symbol, direction=direction, now=now,
                    spike_pct=app_config.VIX_GATE_SPIKE_PCT, high_level=app_config.VIX_GATE_HIGH_LEVEL, size_cut_pct=app_config.VIX_GATE_SIZE_CUT_PCT,
                    max_age_minutes=app_config.VIX_GATE_MAX_AGE_MINUTES, underlyings=app_config.VIX_GATE_UNDERLYINGS)


async def status(session: AsyncSession, tenant_id: int, now: Optional[datetime] = None) -> Dict[str, Any]:
    """What the gate sees today, for the ticket's status strip."""
    now = now or datetime.now(timezone.utc)
    on = await enabled(session, tenant_id)
    readings = await readings_today(session, tenant_id, now) if on else []
    probe = evaluate(readings, symbol=app_config.VIX_GATE_UNDERLYINGS[0] if app_config.VIX_GATE_UNDERLYINGS else "NIFTY", direction="LONG", now=now,
                     spike_pct=app_config.VIX_GATE_SPIKE_PCT, high_level=app_config.VIX_GATE_HIGH_LEVEL, size_cut_pct=app_config.VIX_GATE_SIZE_CUT_PCT,
                     max_age_minutes=app_config.VIX_GATE_MAX_AGE_MINUTES, underlyings=app_config.VIX_GATE_UNDERLYINGS) if on else VixVerdict()
    latest = readings[-1] if readings else None
    return {"enabled": on, "state": probe.state, "underlyings": list(app_config.VIX_GATE_UNDERLYINGS),
            "latest": {"last": latest["last"], "prev_close": latest["prev_close"], "change_pct": latest["change_pct"],
                       "captured_at": latest["captured_at"].isoformat()} if latest else None,
            "bullish_paused": probe.state == "spike", "size_multiplier": probe.size_multiplier,
            "settings": {"spike_pct": app_config.VIX_GATE_SPIKE_PCT, "high_level": app_config.VIX_GATE_HIGH_LEVEL,
                         "size_cut_pct": app_config.VIX_GATE_SIZE_CUT_PCT, "max_age_minutes": app_config.VIX_GATE_MAX_AGE_MINUTES},
            "messages": probe.reasons + probe.notes}
