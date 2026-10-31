"""M62 JN-3: the journal list, its statistics and its export - over the trader's own entries only.

`load` reads the logged-in trader's own ATP trades (tenant and user) and, unless the request leaves them out, his manual
(outside-ATP) entries, filtered by setup, tag, outcome and an IST date range. `summary` / `breakdown` are pure maths over
the decisions (a multi-leg structure counts once, as in the weekly review and the discipline score).

Numbers only: every figure is computed from the trader's own rows; nothing here describes what to do next, and nothing
reads or changes an order, a position or a broker.

Manual entries: included by default and labelled (`source` = OUTSIDE_ATP, and the totals say how many there are);
`include_manual=false` leaves them out.
"""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import JournalEntryRecord, JournalManualTradeRecord, JournalTagRecord, TradeRecord, User
from app.journal import entries, service

IST = timezone(timedelta(hours=5, minutes=30))
WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
OUTCOMES = ("win", "loss", "flat", "open")
MAX_TRADES = 5000            # the newest rows read for one request; `truncated` says when more exist
ATP_SOURCE = "ATP"


@dataclass(frozen=True)
class Filters:
    setup: str | None = None
    tag: str | None = None
    outcome: str | None = None          # win | loss | flat | open
    date_from: date | None = None       # IST calendar days, both inclusive
    date_to: date | None = None
    include_manual: bool = True

    def bounds(self) -> tuple[datetime | None, datetime | None]:
        start = datetime.combine(self.date_from, time.min, tzinfo=IST).astimezone(UTC) if self.date_from else None
        end = datetime.combine(self.date_to + timedelta(days=1), time.min, tzinfo=IST).astimezone(UTC) if self.date_to else None
        return start, end

    def to_dict(self) -> dict[str, Any]:
        return {"setup": self.setup, "tag": self.tag, "outcome": self.outcome,
                "date_from": self.date_from.isoformat() if self.date_from else None,
                "date_to": self.date_to.isoformat() if self.date_to else None, "include_manual": self.include_manual}


@dataclass
class Decision:
    """One decision for the statistics: a single ATP trade, every leg of one structure summed, or one manual entry."""
    unit: str
    setup: str | None
    timeframe: str | None
    entry_time: datetime | None
    pnl: float | None                   # net of charges; None while open
    risk: float                         # rupees from the entry to the initial stop (0 = no valid stop: no R)
    outside_atp: bool = False
    mae_r: float | None = None          # stored excursions (single trades only; a structure has no one price path)
    mfe_r: float | None = None
    mae_amount: float | None = None     # rupees: the excursion in price times the quantity
    mfe_amount: float | None = None
    tags: frozenset[str] = field(default_factory=frozenset)

    @property
    def r(self) -> float | None:
        return self.pnl / self.risk if self.pnl is not None and self.risk > 0 else None

    @property
    def weekday(self) -> str | None:
        return WEEKDAYS[self.entry_time.astimezone(IST).weekday()] if self.entry_time else None


def outcome_of(pnl: float | None) -> str:
    if pnl is None:
        return "open"
    return "win" if pnl > 0 else "loss" if pnl < 0 else "flat"


def _round(value: float | None, places: int = 2) -> float | None:
    return None if value is None else round(value, places)


def _mean(values: Sequence[float]) -> float | None:
    return sum(values) / len(values) if values else None


def summary(decisions: Iterable[Decision]) -> dict[str, Any]:
    """Win rate (wins over closed, %), profit factor (gross win over gross loss; none without a loss), expectancy (net
    P&L per closed decision), average win / loss (rupees), average R, average MAE / MFE (R and rupees, over the closed
    decisions that have them stored). Open decisions are counted but never enter a ratio."""
    items = list(decisions)
    closed = [d for d in items if d.pnl is not None]
    pnls = [float(d.pnl) for d in closed if d.pnl is not None]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    rs = [r for r in (d.r for d in closed) if r is not None]
    gross_win, gross_loss = sum(wins), -sum(losses)

    def avg(attr: str) -> tuple[float | None, int]:
        values = [float(v) for v in (getattr(d, attr) for d in closed) if v is not None]
        return _mean(values), len(values)

    mae_r, mae_n = avg("mae_r")
    mfe_r, _ = avg("mfe_r")
    mae_amount, _ = avg("mae_amount")
    mfe_amount, _ = avg("mfe_amount")
    return {
        "trades": len(items), "closed": len(closed), "open": len(items) - len(closed),
        "wins": len(wins), "losses": len(losses), "flat": len(pnls) - len(wins) - len(losses),
        "outside_atp": sum(1 for d in items if d.outside_atp),
        "win_rate": _round(100.0 * len(wins) / len(pnls), 1) if pnls else None,
        "net_pnl": round(sum(pnls), 2), "gross_win": round(gross_win, 2), "gross_loss": round(-gross_loss, 2),
        "profit_factor": _round(gross_win / gross_loss) if gross_loss > 0 else None,
        "expectancy": _round(_mean(pnls)),
        "avg_win": _round(_mean(wins)), "avg_loss": _round(_mean(losses)),
        "avg_r": _round(_mean(rs)), "r_trades": len(rs),
        "avg_mae_r": _round(mae_r), "avg_mfe_r": _round(mfe_r),
        "avg_mae": _round(mae_amount), "avg_mfe": _round(mfe_amount), "excursion_trades": mae_n,
    }


def breakdown(decisions: Iterable[Decision], key: str) -> list[dict[str, Any]]:
    """The summary per setup / timeframe / weekday. Setups and timeframes: most trades first; weekdays: Monday first."""
    groups: dict[str, list[Decision]] = defaultdict(list)
    for d in decisions:
        value = d.weekday if key == "weekday" else getattr(d, key)
        groups[value or "-"].append(d)
    rows = [{key: name, **summary(group)} for name, group in groups.items()]
    if key == "weekday":
        order = {name: i for i, name in enumerate(WEEKDAYS)}
        return sorted(rows, key=lambda g: order.get(g[key], len(order)))
    return sorted(rows, key=lambda g: (-g["trades"], g[key]))


def stats_payload(decisions: Sequence[Decision]) -> dict[str, Any]:
    return {"totals": summary(decisions), "by_setup": breakdown(decisions, "setup"),
            "by_timeframe": breakdown(decisions, "timeframe"), "by_weekday": breakdown(decisions, "weekday")}


# --- loading the trader's own rows ------------------------------------------------------------------------------------

@dataclass
class Loaded:
    rows: list[dict[str, Any]]          # one per trade row / manual entry (the list and the export), newest first
    decisions: list[Decision]           # the statistics' units
    truncated: bool
    setups: list[str]                   # every setup / tag among the rows before the setup / tag / outcome filters (the
    tags: list[str]                     # filter choices)


def _tag_names(tags: Sequence[JournalTagRecord], own: Sequence[str]) -> frozenset[str]:
    return frozenset([t.tag for t in tags if t.status != "DISMISSED"] + [str(x) for x in own])


async def load(session: AsyncSession, user: User, f: Filters) -> Loaded:
    start, end = f.bounds()
    query = select(TradeRecord).where(TradeRecord.user_id == user.id, TradeRecord.tenant_id == user.tenant_id)
    if start is not None:
        query = query.where(TradeRecord.entry_time >= start)
    if end is not None:
        query = query.where(TradeRecord.entry_time < end)
    trades = list(await session.scalars(query.order_by(TradeRecord.entry_time.desc(), TradeRecord.id.desc()).limit(MAX_TRADES + 1)))
    truncated = len(trades) > MAX_TRADES
    trades = trades[:MAX_TRADES]
    ids = [t.id for t in trades] or [-1]
    journal = {e.trade_id: e for e in await session.scalars(select(JournalEntryRecord).where(
        JournalEntryRecord.trade_id.in_(ids), JournalEntryRecord.user_id == user.id, JournalEntryRecord.tenant_id == user.tenant_id))}
    tags: dict[int, list[JournalTagRecord]] = defaultdict(list)
    for tag in await session.scalars(select(JournalTagRecord).where(
            JournalTagRecord.trade_id.in_(ids), JournalTagRecord.user_id == user.id, JournalTagRecord.tenant_id == user.tenant_id)
            .order_by(JournalTagRecord.tag)):
        tags[tag.trade_id].append(tag)
    timeframes = await entries.deployment_timeframes(session, trades)

    manual: list[JournalManualTradeRecord] = []
    if f.include_manual:
        mq = select(JournalManualTradeRecord).where(JournalManualTradeRecord.user_id == user.id,
                                                    JournalManualTradeRecord.tenant_id == user.tenant_id)
        if start is not None:
            mq = mq.where(JournalManualTradeRecord.entry_time >= start)
        if end is not None:
            mq = mq.where(JournalManualTradeRecord.entry_time < end)
        manual = list(await session.scalars(mq.order_by(JournalManualTradeRecord.entry_time.desc()).limit(MAX_TRADES + 1)))
        truncated = truncated or len(manual) > MAX_TRADES
        manual = manual[:MAX_TRADES]
    refs = [entries.Ref(trade=t) for t in trades] + [entries.Ref(manual=m) for m in manual]
    files = await entries.attachments_of(session, user, refs)

    rows: list[dict[str, Any]] = []
    units: dict[str, list[TradeRecord]] = defaultdict(list)
    for t in trades:
        entry = journal.get(t.id)
        own = entries._loads(entry.user_tags_json, []) if entry is not None else []
        plan = entries.plan_view(t, entry, timeframes.get(t.deployment_id or -1))
        exc = service.excursions(t, entry)
        base = service.entry_dict(t, entry, tags.get(t.id, []))
        rows.append(_row(kind="trade", id=t.id, unit=f"g:{t.leg_group_id}" if t.leg_group_id else f"t:{t.id}", source=ATP_SOURCE,
                         mode=t.mode, symbol=t.symbol, direction=t.direction, setup=plan["setup"], timeframe=plan["timeframe"],
                         entry_time=base["entry_time"], exit_time=base["exit_time"], entry_price=t.entry_price, exit_price=t.exit_price,
                         quantity=t.quantity, charges=t.charges, pnl=t.pnl, r=base["r_multiple"], exit_reason=t.exit_reason,
                         tags=[{"id": x.id, "tag": x.tag, "status": x.status, "source": x.source} for x in tags.get(t.id, [])],
                         user_tags=own, notes=t.notes, attachments=len(files.get(("trade", t.id), [])),
                         chart=f"/api/journal/trades/{t.id}/chart?at=entry", mae_r=exc["mae_r"], mfe_r=exc["mfe_r"],
                         leg_group_id=t.leg_group_id, names=_tag_names(tags.get(t.id, []), own)))
        units[rows[-1]["unit"]].append(t)
    for m in manual:
        own = entries._loads(m.user_tags_json, [])
        pnl = entries.manual_pnl(m)
        risk = entries._risk(m.direction, float(m.entry_price), float(m.stop_loss) if m.stop_loss is not None else None, float(m.quantity))
        rows.append(_row(kind="manual", id=m.id, unit=f"m:{m.id}", source=entries.MANUAL_SOURCE, mode=None, symbol=m.symbol,
                         direction=m.direction, setup=m.setup, timeframe=m.timeframe, entry_time=entries._iso(entries._utc(m.entry_time)),
                         exit_time=entries._iso(entries._utc(m.exit_time)), entry_price=m.entry_price, exit_price=m.exit_price,
                         quantity=m.quantity, charges=m.charges, pnl=pnl, r=round(pnl / risk, 2) if pnl is not None and risk > 0 else None,
                         exit_reason=m.exit_reason, tags=[], user_tags=own, notes=m.notes,
                         attachments=len(files.get(("manual", m.id), [])), chart=f"/api/journal/manual/{m.id}/chart?at=entry",
                         mae_r=None, mfe_r=None, leg_group_id=None, names=frozenset(str(x) for x in own), risk=risk))
    rows.sort(key=lambda r: (r["entry_time"] or "", r["kind"], r["id"]), reverse=True)
    setups = sorted({r["setup"] for r in rows if r["setup"]})
    tag_choices = sorted({n for r in rows for n in r["_names"]})

    decisions = _decisions(rows, units, journal)
    kept = {d.unit for d in decisions if _match_decision(d, f)}
    decisions = [d for d in decisions if d.unit in kept]
    rows = [r for r in rows if _match_row(r, f)]
    for r in rows:
        r.pop("_names", None)
        r.pop("_risk", None)
    return Loaded(rows=rows, decisions=decisions, truncated=truncated, setups=setups, tags=tag_choices)


def _row(*, names: frozenset[str], risk: float | None = None, r: float | None, **values: Any) -> dict[str, Any]:
    out = dict(values)
    out.update(r_multiple=r, outcome=outcome_of(values["pnl"]), outside_atp=values["source"] == entries.MANUAL_SOURCE,
               source_label=entries.MANUAL_LABEL if values["source"] == entries.MANUAL_SOURCE else None, _names=names, _risk=risk)
    return out


def _decisions(rows: list[dict[str, Any]], units: dict[str, list[TradeRecord]],
               journal: dict[int, JournalEntryRecord]) -> list[Decision]:
    by_unit: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        by_unit[r["unit"]].append(r)
    review = {rt.unit: rt for unit_legs in units.values() for rt in service.review_trades(unit_legs)}
    out: list[Decision] = []
    for unit, legs in by_unit.items():
        legs = sorted(legs, key=lambda r: r["id"])
        first = legs[0]
        entry_time = min((datetime.fromisoformat(r["entry_time"]) for r in legs if r["entry_time"]), default=None)
        names = frozenset(n for r in legs for n in r["_names"])
        if first["kind"] == "manual":
            out.append(Decision(unit=unit, setup=first["setup"], timeframe=first["timeframe"], entry_time=entry_time, pnl=first["pnl"],
                                risk=float(first["_risk"] or 0.0), outside_atp=True, tags=names))
            continue
        rt = review[unit]
        mae_r: float | None = None
        mfe_r: float | None = None
        mae_amount: float | None = None
        mfe_amount: float | None = None
        if len(legs) == 1 and not first["leg_group_id"]:
            trade = units[unit][0]
            exc = service.excursions(trade, journal.get(trade.id))
            mae_r, mfe_r = exc["mae_r"], exc["mfe_r"]
            qty = abs(float(trade.quantity))
            mae_amount = exc["mae"] * qty if exc["mae"] is not None else None
            mfe_amount = exc["mfe"] * qty if exc["mfe"] is not None else None
        out.append(Decision(unit=unit, setup=first["setup"], timeframe=first["timeframe"], entry_time=entry_time, pnl=rt.pnl,
                            risk=rt.risk, mae_r=mae_r, mfe_r=mfe_r, mae_amount=mae_amount, mfe_amount=mfe_amount, tags=names))
    return out


def _match(setup: str | None, names: frozenset[str], outcome: str, f: Filters) -> bool:
    if f.setup and (setup or "") != f.setup:
        return False
    if f.tag and f.tag not in names:
        return False
    return not (f.outcome and outcome != f.outcome)


def _match_row(r: dict[str, Any], f: Filters) -> bool:
    return _match(r["setup"], r["_names"], r["outcome"], f)


def _match_decision(d: Decision, f: Filters) -> bool:
    return _match(d.setup, d.tags, outcome_of(d.pnl), f)
