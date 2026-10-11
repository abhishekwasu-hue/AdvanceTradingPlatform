"""Z6: the discipline engine's database glue - one trader's week of journal, tags and score.

Scope: the journal is personal. Every query here filters on the trader's own `user_id` (and `tenant_id`), so a tenant
owner, a teammate or another tenant never sees it - the risk counters stay tenant-wide elsewhere, the journal does not.

A week is the IST calendar week, Monday 00:00 to the next Monday 00:00, named ISO-style ("2026-W41"); a trade belongs
to the week its entry falls in (an open trade too - its P&L is simply not counted until it closes).
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple, cast

from sqlalchemy import Table, and_, delete, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import JournalEntryRecord, JournalTagRecord, MarketEventRecord, TradeRecord, User
from app.instruments.master import underlying_of
from app.journal import capture
from app.journal import settings as journal_settings
from app.journal.detectors import Detection, JournalTrade, MarketEvent, StopChange, detect
from app.journal.score import TagState, weekly_score
from app.journal.settings import TAGS, DisciplineSettings
from app.market_data.calendar import IST, MARKET_OPEN

_WEEK = re.compile(r"^(\d{4})-W(\d{2})$")
# Context loaded before a week: revenge needs the losing exits just before it, the own-median daily norm needs its
# look-back days; positions still open at the week's start are always loaded (averaging into them).
_MIN_CONTEXT_DAYS = 2
TAG_TABLE = cast(Table, JournalTagRecord.__table__)   # Core statements (upserts, guarded updates) target the table


class WeekError(ValueError):
    pass


def _utc(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def week_bounds(label: Optional[str] = None, *, now: Optional[datetime] = None) -> Tuple[str, datetime, datetime]:
    """("2026-W41", start, end) in UTC for an ISO week label, or the IST week containing `now`."""
    if label:
        m = _WEEK.match(label.strip())
        if not m:
            raise WeekError("week must look like 2026-W41")
        try:
            monday = date.fromisocalendar(int(m.group(1)), int(m.group(2)), 1)
        except ValueError as exc:
            raise WeekError(str(exc)) from exc
    else:
        today = (now or datetime.now(timezone.utc)).astimezone(IST).date()
        monday = today - timedelta(days=today.weekday())
    start = datetime.combine(monday, time(0, 0), tzinfo=IST).astimezone(timezone.utc)
    iso = monday.isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}", start, start + timedelta(days=7)


def previous_week(label: str) -> str:
    _, start, _ = week_bounds(label)
    return week_bounds(None, now=start - timedelta(days=1))[0]


def _stop_changes(entry: Optional[JournalEntryRecord]) -> Tuple[StopChange, ...]:
    if entry is None:
        return ()
    out = []
    for item in capture._loads(entry.stop_history_json, []):
        try:
            at = _utc(datetime.fromisoformat(item["at"])) if item.get("at") else None
            out.append(StopChange(at=at or datetime.min.replace(tzinfo=timezone.utc), old=float(item["from"]), new=float(item["to"]),
                                  source=str(item.get("source") or "rule")))
        except (KeyError, TypeError, ValueError):
            continue
    return tuple(out)


def _has_group_stop(trade: TradeRecord) -> bool:
    if not trade.leg_group_id:
        return False
    meta = capture._loads(trade.group_meta, {})
    return any(meta.get(k) is not None for k in ("stop_value", "pnl_stop")) or bool(meta.get("underlying_exits"))


def to_journal_trade(trade: TradeRecord, entry: Optional[JournalEntryRecord]) -> JournalTrade:
    initial = trade.initial_stop_loss if trade.initial_stop_loss is not None else trade.stop_loss
    plan_entry = float(trade.expected_price) if trade.expected_price else None      # the signal's price; None = no plan price
    entry_time = _utc(trade.entry_time) or datetime.min.replace(tzinfo=timezone.utc)   # the column is NOT NULL
    return JournalTrade(
        id=trade.id, symbol=trade.symbol, underlying=underlying_of(trade.underlying_symbol or trade.symbol), direction=trade.direction,
        entry_time=entry_time, entry_price=float(trade.entry_price), quantity=float(trade.quantity),
        plan_entry=float(plan_entry) if plan_entry is not None else None,
        initial_stop=float(initial) if initial is not None else None,
        final_stop=float(trade.stop_loss) if trade.stop_loss is not None else None,
        target1=float(trade.target1) if trade.target1 is not None else None,
        exit_time=_utc(trade.exit_time), exit_price=float(trade.exit_price) if trade.exit_price is not None else None,
        exit_reason=trade.exit_reason, pnl=float(trade.pnl) if trade.pnl is not None else None,
        stop_changes=_stop_changes(entry), group_id=trade.leg_group_id, has_group_stop=_has_group_stop(trade),
    )


def scope_match(scope: Optional[str], underlying: str) -> bool:
    """The events calendar's scope rule (the same as the Risk Guardian's): none / "*" = every symbol, "INDEX" = every
    index underlying, else one underlying."""
    from app.risk_engine.guardian import INDEX_BUCKET, bucket_for
    text = (scope or "").strip().upper()
    if not text or text == "*":
        return True
    if text == INDEX_BUCKET:
        return bucket_for(underlying) == INDEX_BUCKET
    return underlying_of(text) == underlying_of(underlying)


def event_start(event: MarketEventRecord) -> datetime:
    """When an event starts: its date at its start time (IST), or at the session open when it has none."""
    at = MARKET_OPEN
    if event.start_time:
        try:
            hh, mm = event.start_time.split(":")
            at = time(int(hh), int(mm))
        except ValueError:
            at = MARKET_OPEN
    return datetime.combine(event.event_date, at, tzinfo=IST).astimezone(timezone.utc)


async def load_events(session: AsyncSession, tenant_id: int, start: datetime, end: datetime) -> List[MarketEvent]:
    rows = await session.scalars(select(MarketEventRecord).where(
        MarketEventRecord.event_date >= (start.astimezone(IST).date() - timedelta(days=1)),
        MarketEventRecord.event_date <= end.astimezone(IST).date(),
        or_(MarketEventRecord.tenant_id.is_(None), MarketEventRecord.tenant_id == tenant_id)).order_by(MarketEventRecord.id))
    return [MarketEvent(id=r.id, kind=r.kind, scope=r.underlying, at=event_start(r), description=r.description or "") for r in rows]


async def own_trades(session: AsyncSession, user: User, start: datetime, end: datetime) -> List[TradeRecord]:
    """The trader's own trades entered in [start, end)."""
    return list(await session.scalars(select(TradeRecord).where(
        TradeRecord.tenant_id == user.tenant_id, TradeRecord.user_id == user.id,
        TradeRecord.entry_time >= start, TradeRecord.entry_time < end).order_by(TradeRecord.entry_time, TradeRecord.id)))


async def _context_trades(session: AsyncSession, user: User, start: datetime, end: datetime, s: DisciplineSettings) -> List[TradeRecord]:
    lookback = max(_MIN_CONTEXT_DAYS, int(s.overtrading_lookback_days) + 1, int(s.revenge_window_minutes // (24 * 60)) + 1)
    since = start - timedelta(days=lookback)
    return list(await session.scalars(select(TradeRecord).where(
        TradeRecord.tenant_id == user.tenant_id, TradeRecord.user_id == user.id, TradeRecord.entry_time < end,
        or_(TradeRecord.entry_time >= since, TradeRecord.exit_time.is_(None), TradeRecord.exit_time >= start),
    ).order_by(TradeRecord.entry_time, TradeRecord.id)))


async def _sync_tags(session: AsyncSession, user: User, trade_ids: Sequence[int], detections: Sequence[Detection]) -> None:
    """AUTO tags follow the detectors; the trader's decisions (CONFIRMED / DISMISSED) and own tags are never touched.

    Concurrency-safe: new tags go in with ON CONFLICT (trade_id, tag) DO NOTHING, and the evidence update and the
    removal of a tag that no longer fires are guarded by `source = AUTO` (and `status = SUGGESTED` for the removal),
    so a second refresh, the trader's decision in another tab, or a row already removed never errors or is undone."""
    if not trade_ids:
        return
    rows = list(await session.scalars(select(JournalTagRecord).where(
        JournalTagRecord.user_id == user.id, JournalTagRecord.trade_id.in_(list(trade_ids)))))
    by_key = {(r.trade_id, r.tag): r for r in rows}
    found = {(d.trade_id, d.tag): d for d in detections}
    tags = TAG_TABLE
    now = datetime.now(timezone.utc)
    new_rows: List[Dict[str, Any]] = []
    for key, d in found.items():
        row = by_key.get(key)
        evidence = json.dumps(d.evidence, sort_keys=True, default=str)
        if row is None:
            new_rows.append({"tenant_id": user.tenant_id, "user_id": user.id, "trade_id": d.trade_id, "tag": d.tag, "source": "AUTO",
                             "status": "SUGGESTED", "evidence_json": evidence, "detected_at": now, "decided_at": None})
        elif row.source == "AUTO" and row.evidence_json != evidence:
            await session.execute(update(tags).where(tags.c.id == row.id, tags.c.source == "AUTO").values(evidence_json=evidence))
    if new_rows:
        await session.execute(capture.dialect_insert(session, tags).on_conflict_do_nothing(index_elements=["trade_id", "tag"]), new_rows)
    stale = [row.id for key, row in by_key.items() if key not in found and row.source == "AUTO" and row.status == "SUGGESTED"]
    if stale:
        await session.execute(delete(tags).where(tags.c.id.in_(stale), tags.c.source == "AUTO", tags.c.status == "SUGGESTED"))


# Namespace of the per-trader advisory lock that serialises two refreshes of the same trader's journal (PostgreSQL).
_REFRESH_LOCK_NAMESPACE = 0x5A36


async def _lock_trader(session: AsyncSession, user: User) -> None:
    """Two refreshes of one trader's week (two tabs, a double click) run one after the other on PostgreSQL. The upserts
    above already make an overlap harmless; the lock just avoids the wasted work. Released at commit."""
    if session.get_bind().dialect.name == "postgresql":
        await session.execute(text("SELECT pg_advisory_xact_lock(:ns, :uid)"), {"ns": _REFRESH_LOCK_NAMESPACE, "uid": int(user.id)})


async def refresh_week(session: AsyncSession, user: User, label: Optional[str] = None, *, now: Optional[datetime] = None) -> Dict[str, Any]:
    """Completes the journal of the week's trades from the trade rows, re-runs the detectors and syncs the AUTO tags.
    Idempotent. Returns the pieces the routes and the weekly review read."""
    now = now or datetime.now(timezone.utc)
    week, start, end = week_bounds(label, now=now)
    await _lock_trader(session, user)
    s = await journal_settings.load(session, user)
    context = await _context_trades(session, user, start, end, s)
    week_trades = [t for t in context if start <= (_utc(t.entry_time) or start) < end]
    entries = await capture.ensure_entries(session, context)
    await session.flush()
    jtrades = [to_journal_trade(t, entries.get(t.id)) for t in context]
    events = await load_events(session, user.tenant_id, start - timedelta(days=7), end)
    detections = detect(jtrades, s, events=events, target_ids=[t.id for t in week_trades], now=now, scope_match=scope_match)
    await _sync_tags(session, user, [t.id for t in week_trades], detections)
    await session.commit()
    tags = list(await session.scalars(select(JournalTagRecord).where(
        JournalTagRecord.user_id == user.id, JournalTagRecord.trade_id.in_([t.id for t in week_trades] or [-1])).order_by(
        JournalTagRecord.trade_id, JournalTagRecord.tag).execution_options(populate_existing=True)))
    units = {t.id: (f"g:{t.leg_group_id}" if t.leg_group_id else f"t:{t.id}") for t in week_trades}
    score = weekly_score(units, [TagState(r.trade_id, r.tag, r.status) for r in tags], s)
    return {"week": week, "start": start, "end": end, "settings": s, "trades": week_trades, "entries": entries, "tags": tags, "score": score}


# --- shaping for the API ----------------------------------------------------------------------------------------------

def excursions(trade: TradeRecord, entry: Optional[JournalEntryRecord]) -> Dict[str, Optional[float]]:
    """MAE / MFE in price and in R (R = the initial stop distance) from the highest / lowest price seen while open."""
    if entry is None or entry.high_price is None or entry.low_price is None:
        return {"mae": None, "mfe": None, "mae_r": None, "mfe_r": None}
    sign = 1 if trade.direction == "LONG" else -1
    high, low, fill = float(entry.high_price), float(entry.low_price), float(trade.entry_price)
    mfe = max(0.0, (high - fill) if sign > 0 else (fill - low))
    mae = max(0.0, (fill - low) if sign > 0 else (high - fill))
    stop = trade.initial_stop_loss if trade.initial_stop_loss is not None else trade.stop_loss
    risk = sign * (fill - float(stop)) if stop else 0.0
    return {"mae": round(mae, 4), "mfe": round(mfe, 4), "mae_r": round(mae / risk, 2) if risk > 0 else None,
            "mfe_r": round(mfe / risk, 2) if risk > 0 else None}


def tag_dict(row: JournalTagRecord) -> Dict[str, Any]:
    return {"id": row.id, "trade_id": row.trade_id, "tag": row.tag, "source": row.source, "status": row.status,
            "evidence": capture._loads(row.evidence_json, {}), "detected_at": row.detected_at.isoformat() if row.detected_at else None,
            "decided_at": row.decided_at.isoformat() if row.decided_at else None}


def entry_dict(trade: TradeRecord, entry: Optional[JournalEntryRecord], tags: Sequence[JournalTagRecord]) -> Dict[str, Any]:
    entry_time, exit_time = _utc(trade.entry_time), _utc(trade.exit_time)
    plan = capture._loads(entry.plan_json, {}) if entry is not None else capture.plan_of(trade)
    stop = trade.initial_stop_loss if trade.initial_stop_loss is not None else trade.stop_loss
    risk_amount = abs(float(trade.entry_price) - float(stop)) * abs(float(trade.quantity)) if stop else 0.0
    r_multiple = round(float(trade.pnl) / risk_amount, 2) if trade.pnl is not None and risk_amount > 0 else None
    greeks = capture._loads(entry.greeks_entry_json, {}) if entry is not None and entry.greeks_entry_json else None
    return {
        "trade_id": trade.id, "symbol": trade.symbol, "mode": trade.mode, "direction": trade.direction, "setup": trade.strategy_id,
        "source": (entry.source if entry is not None else capture.derive_source(trade)) or capture.derive_source(trade),
        "instrument_kind": trade.instrument_kind or "UNDERLYING", "leg_group_id": trade.leg_group_id,
        "entry_time": entry_time.isoformat() if entry_time else None, "exit_time": exit_time.isoformat() if exit_time else None,
        "plan": plan,
        "execution": {"entry_price": trade.entry_price, "quantity": trade.quantity, "slippage": trade.slippage,
                      "entry_latency_ms": trade.entry_latency_ms, "stop_now": trade.stop_loss, "exit_price": trade.exit_price,
                      "exit_reason": trade.exit_reason},
        "fills": capture._loads(entry.fills_json, []) if entry is not None else [],
        "costs": {"charges": trade.charges, "source": trade.charges_source or "ESTIMATED"},
        "pnl": trade.pnl, "r_multiple": r_multiple, "risk_amount": round(risk_amount, 2),
        "holding_minutes": round((exit_time - entry_time).total_seconds() / 60.0, 1) if exit_time and entry_time else None,
        "excursions": excursions(trade, entry),
        "stop_history": capture._loads(entry.stop_history_json, []) if entry is not None else [],
        "chart_entry": capture._loads(entry.chart_entry_json, {}) or None if entry is not None else None,
        "chart_exit": capture._loads(entry.chart_exit_json, {}) or None if entry is not None else None,
        "greeks_entry": greeks, "iv_entry": entry.iv_entry if entry is not None else None, "iv_exit": entry.iv_exit if entry is not None else None,
        "notes": trade.notes, "tags": [tag_dict(t) for t in tags],
    }


async def own_trade(session: AsyncSession, user: User, trade_id: int) -> Optional[TradeRecord]:
    row = await session.get(TradeRecord, trade_id)
    if row is None or row.tenant_id != user.tenant_id or row.user_id != user.id:
        return None
    return row


async def set_tag_status(session: AsyncSession, user: User, tag_id: int, status: str) -> Optional[JournalTagRecord]:
    row = await session.get(JournalTagRecord, tag_id)
    if row is None or row.user_id != user.id or row.tenant_id != user.tenant_id:
        return None
    row.status = status
    row.decided_at = None if status == "SUGGESTED" else datetime.now(timezone.utc)
    await session.commit()
    await session.refresh(row)
    return row


async def add_user_tag(session: AsyncSession, user: User, trade: TradeRecord, tag: str, note: Optional[str]) -> JournalTagRecord:
    """The trader's own tag, or the trader's confirmation of a tag that is already there - one atomic upsert, so a
    detector run inserting the same tag at the same moment never turns it into an error."""
    if tag not in TAGS:
        raise ValueError(f"Unknown tag '{tag}' - known: {list(TAGS)}")
    now = datetime.now(timezone.utc)
    evidence = json.dumps({"note": (note or "").strip()[:300]} if note else {}, sort_keys=True)
    tags = TAG_TABLE
    stmt = capture.dialect_insert(session, tags).values(
        tenant_id=user.tenant_id, user_id=user.id, trade_id=trade.id, tag=tag, source="USER", status="CONFIRMED",
        evidence_json=evidence, detected_at=now, decided_at=now)
    await session.execute(stmt.on_conflict_do_update(index_elements=["trade_id", "tag"], set_={"status": "CONFIRMED", "decided_at": now}))
    await session.commit()
    row = await session.scalar(select(JournalTagRecord).where(and_(JournalTagRecord.trade_id == trade.id, JournalTagRecord.tag == tag))
                               .execution_options(populate_existing=True))
    if row is None:   # just upserted; only a concurrent delete of the trade itself (cascade) gets here
        raise LookupError(f"trade {trade.id} no longer exists")
    return row


# --- Z6-b: the weekly review ------------------------------------------------------------------------------------------

def _group_risk(legs: Sequence[TradeRecord]) -> float:
    """A structure's risk in rupees: its max loss per unit (`risk_per_unit`, else `max_loss`) times the 1x quantity, from
    the group meta the structure was sized on. 0 when the meta does not say (no R for that decision)."""
    for leg in legs:
        meta = capture._loads(leg.group_meta, {})
        per_unit = meta.get("risk_per_unit") or meta.get("max_loss")
        quantity = meta.get("quantity") or leg.quantity
        try:
            risk = abs(float(per_unit)) * abs(float(quantity)) if per_unit else 0.0
        except (TypeError, ValueError):
            continue
        if risk > 0:
            return risk
    return 0.0


def review_trades(trades: Sequence[TradeRecord]) -> List[Any]:
    """The week's decisions for the review: a single trade as it is, the legs of one structure summed into one (net P&L
    and charges added up, closed only when every leg is, the group's risk from its max loss) - the unit the discipline
    score counts, so a 4-leg iron condor is one trade, not four."""
    from app.journal.review import ReviewTrade
    units: Dict[str, List[TradeRecord]] = {}
    for t in trades:
        units.setdefault(f"g:{t.leg_group_id}" if t.leg_group_id else f"t:{t.id}", []).append(t)
    out = []
    for unit, legs in units.items():
        legs = sorted(legs, key=lambda leg: leg.id)
        closed = all(leg.pnl is not None and leg.exit_time is not None for leg in legs)
        entry_times = [x for x in (_utc(leg.entry_time) for leg in legs) if x is not None]
        exit_times = [x for x in (_utc(leg.exit_time) for leg in legs) if x is not None]
        if len(legs) == 1 and not legs[0].leg_group_id:
            t = legs[0]
            stop = t.initial_stop_loss if t.initial_stop_loss is not None else t.stop_loss
            sign = 1 if t.direction == "LONG" else -1
            per_unit = sign * (float(t.entry_price) - float(stop)) if stop else 0.0
            risk = per_unit * abs(float(t.quantity)) if per_unit > 0 else 0.0
        else:
            risk = _group_risk(legs)
        holding = None
        if closed and entry_times and exit_times:
            holding = (max(exit_times) - min(entry_times)).total_seconds() / 60.0
        out.append(ReviewTrade(id=legs[0].id, setup=legs[0].strategy_id, unit=unit,
                               pnl=round(sum(float(leg.pnl) for leg in legs if leg.pnl is not None), 2) if closed else None,
                               charges=float(sum(float(leg.charges or 0.0) for leg in legs)), risk=risk, holding_minutes=holding,
                               trade_ids=tuple(leg.id for leg in legs)))
    return out


async def weekly_review(session: AsyncSession, user: User, label: Optional[str] = None, *, history_weeks: int = 4,
                        now: Optional[datetime] = None) -> Dict[str, Any]:
    """The week's review from the journal, with the trader's own previous `history_weeks` weeks for comparison."""
    from app.journal.review import _stats, build_review
    from app.journal.score import counted
    data = await refresh_week(session, user, label, now=now)
    s: DisciplineSettings = data["settings"]
    live = counted([TagState(r.trade_id, r.tag, r.status) for r in data["tags"]], s)
    history: List[Dict[str, Any]] = []
    week = data["week"]
    for _ in range(max(0, history_weeks)):
        week = previous_week(week)
        past = await refresh_week(session, user, week, now=now)
        stats = _stats(review_trades(past["trades"]))
        history.append({"week": week, "score": past["score"]["score"], "trades": stats["trades"], "net_pnl": stats["net_pnl"],
                        "avg_r": stats["avg_r"], "win_rate": stats["win_rate"]})
    review = build_review(data["week"], review_trades(data["trades"]), [(t.trade_id, t.tag) for t in live], data["score"], history, s)
    review.update(start=data["start"].isoformat(), end=data["end"].isoformat())
    return review
