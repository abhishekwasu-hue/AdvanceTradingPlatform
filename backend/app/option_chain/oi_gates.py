"""OI Banner O5: the OI gates as opt-in entry conditions for deployments (Strategy Builder / Autopilot).

A deployment lists the gates it wants (`strategy_deployments.oi_gates`, e.g. "OI_DIFF,PCR"); the worker asks
`check_entry` before a NEW entry, never before an exit (ADR-0004). Every gate fails closed: no snapshot, a stale one
(older than the tenant's `stale_after_minutes`), or a gate whose limits the organisation has not set blocks the entry
and says why. Thresholds come from the tenant's OI settings (`OIRegimeSettings`); nothing here is per-symbol.

Gates (ported in O1, `app/option_chain/oi_regime.py`):
* OI_DIFF      - check_oi_diff_entry_gate on the hysteresis-confirmed signal
* OI_CONFIRM   - check_oi_confirmation, strictness from settings (A conflict filter / B strict)
* PCR          - check_pcr_gate with pcr_bullish_min / pcr_bearish_max
* IV_CHANGE    - check_iv_change_gate: today's ATM IV against recent sideways sessions (IV and the day's range from
                 the stored snapshots; a session's open/high/low/close are its first/highest/lowest/last sampled price)
* SWING_OI     - swing_oi_gate: OI-price matrix vs the previous session, PCR bias, max pain; rollover not collected yet
* OI_WALL      - check_oi_wall_confirmation at the psychological level in the trade's direction
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import List, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import OISnapshotRecord
from app.option_chain import oi_regime, snapshots
from app.option_chain.oi_regime import Direction, OIRegimeSettings
from app.option_chain.snapshots import IST

GATES = ("OI_DIFF", "OI_CONFIRM", "PCR", "IV_CHANGE", "SWING_OI", "OI_WALL")


def parse_gates(value: Optional[str]) -> List[str]:
    names = [g.strip().upper() for g in (value or "").split(",") if g.strip()]
    unknown = [g for g in names if g not in GATES]
    if unknown:
        raise ValueError(f"unknown OI gates {unknown}; known: {list(GATES)}")
    return list(dict.fromkeys(names))


@dataclass
class GateResult:
    allowed: bool
    reasons: List[str] = field(default_factory=list)       # why it was blocked (empty when allowed)
    passed: List[str] = field(default_factory=list)


async def _session_ranges(session: AsyncSession, underlying: str, before: date, settings: OIRegimeSettings, days: int) -> List[oi_regime.DayIv]:
    """Past sessions (newest last): their last ATM IV and their range from the sampled underlying prices."""
    out: List[oi_regime.DayIv] = []
    trade_days = list(await session.scalars(select(OISnapshotRecord.trade_date).where(
        OISnapshotRecord.underlying == underlying, OISnapshotRecord.trade_date < before).group_by(OISnapshotRecord.trade_date)
        .order_by(OISnapshotRecord.trade_date.desc()).limit(days * 3)))
    for d in sorted(trade_days):
        slots = await snapshots.day_chains(session, underlying, d)
        prices = [s.chain.underlying_ltp for s in slots if s.chain.underlying_ltp]
        if not prices:
            continue
        out.append(oi_regime.DayIv(d, oi_regime.atm_iv(slots[-1].chain), prices[0], max(prices), min(prices), prices[-1]))
    return out


async def _previous_close(session: AsyncSession, underlying: str, before: date, settings: OIRegimeSettings):
    """The previous session's last slot totals (for the OI-price matrix)."""
    prev_day = await session.scalar(select(func.max(OISnapshotRecord.trade_date)).where(
        OISnapshotRecord.underlying == underlying, OISnapshotRecord.trade_date < before))
    if prev_day is None:
        return None
    slots = await snapshots.day_chains(session, underlying, prev_day)
    if not slots:
        return None
    step = oi_regime.resolve_strike_step(settings, slots[-1].chain)
    return oi_regime.summarise_chain(slots[-1].chain, step, settings.atm_range) if step else None


async def check_entry(session: AsyncSession, tenant_id: int, underlying: str, direction: str, gates: List[str], now: datetime) -> GateResult:
    """Every requested gate for a NEW entry in `direction` (BULLISH / BEARISH). Fail-closed throughout."""
    result = GateResult(True)
    if not gates:
        return result

    def block(reason: str) -> None:
        result.allowed = False
        result.reasons.append(reason)

    settings = await snapshots.settings_for(session, tenant_id, underlying)
    day = now.astimezone(IST).date()
    latest_capture = await snapshots.latest_capture(session, underlying)
    if oi_regime.is_stale(latest_capture, now, settings.stale_after_minutes):
        block(f"OI data for {underlying} is missing or older than {settings.stale_after_minutes:g} minutes")
        return result
    slots = await snapshots.day_chains(session, underlying, day)
    timeline = snapshots.replay(underlying, slots, settings, day)
    if not timeline:
        block(f"no OI snapshot for {underlying} today")
        return result
    latest = timeline[-1]
    state, totals = latest.state, latest.totals

    for gate in gates:
        if gate == "OI_DIFF":
            ok = oi_regime.check_oi_diff_entry_gate(direction, state.stable_signal)
            (result.passed.append(gate) if ok else block(f"OI diff gate: signal {state.stable_signal.label} does not support a {direction.lower()} entry"))
        elif gate == "OI_CONFIRM":
            ok, label = oi_regime.check_oi_confirmation(direction, state.stable_signal, settings.oi_confirmation_strictness)
            (result.passed.append(gate) if ok else block(f"OI confirmation ({settings.oi_confirmation_strictness}): {label}"))
        elif gate == "PCR":
            if settings.pcr_bullish_min is None or settings.pcr_bearish_max is None:
                block("PCR gate: limits are not set (OI banner settings)")
                continue
            ok, _, reason = oi_regime.check_pcr_gate(state.pcr, direction, settings.pcr_bullish_min, settings.pcr_bearish_max)
            (result.passed.append(gate) if ok else block(reason))
        elif gate == "IV_CHANGE":
            if settings.iv_change_max_pct is None:
                block("IV change gate: limit is not set (OI banner settings)")
                continue
            today_iv = oi_regime.atm_iv(slots[-1].chain) if not oi_regime.is_stale(latest.captured_at, now, settings.iv_max_age_minutes) else None
            history = await _session_ranges(session, underlying, day, settings, settings.iv_lookback_days)
            change = oi_regime.iv_change_from_average(today_iv, history, day, settings.iv_lookback_days, settings.marubozu_threshold)
            ok, _, reason = oi_regime.check_iv_change_gate(change, settings.iv_change_max_pct)
            (result.passed.append(gate) if ok else block(reason))
        elif gate == "SWING_OI":
            prev = await _previous_close(session, underlying, day, settings)
            matrix = oi_regime.compute_oi_price_matrix(totals.total_call_oi + totals.total_put_oi,
                                                       (prev.total_call_oi + prev.total_put_oi) if prev else None,
                                                       totals.underlying_price, prev.underlying_price if prev else None)
            _, pcr_bias = oi_regime.compute_pcr_signal(totals.total_put_oi, totals.total_call_oi, settings.pcr_bands)
            ok, detail = oi_regime.swing_oi_gate(direction, matrix, pcr_bias, state.max_pain, totals.underlying_price, None, settings.swing_max_opposing)
            (result.passed.append(gate) if ok else block(f"swing OI gate: {len(detail.opposing)} of {detail.total_signals} readings oppose "
                                                         f"({', '.join(n for n, _ in detail.opposing)})"))
        elif gate == "OI_WALL":
            level = oi_regime.find_psychological_level(totals.underlying_price, direction, oi_regime.psychological_round_to(settings, totals.strike_step))
            target = round(level / totals.strike_step) * totals.strike_step
            base = (await snapshots.baselines(session, underlying, day)).get(float(target))
            side = 0 if direction == Direction.BEARISH.value else 1
            ok, detail = oi_regime.check_oi_wall_confirmation(slots[-1].chain.rows, level, direction, totals.strike_step, base[side] if base else None)
            (result.passed.append(gate) if ok else block(f"OI wall gate: no {detail.side} OI added at {detail.target_strike:g} since the open"
                                                         + (f" ({detail.reason})" if detail.reason else "")))
    return result


def direction_of(signal_direction: str) -> Optional[str]:
    """LONG -> BULLISH, SHORT -> BEARISH; anything else has no OI direction."""
    return {"LONG": Direction.BULLISH.value, "SHORT": Direction.BEARISH.value}.get(str(signal_direction).upper().split(".")[-1])


__all__ = ["GATES", "parse_gates", "check_entry", "direction_of", "GateResult"]
