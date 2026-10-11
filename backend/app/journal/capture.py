"""Z6: journal auto-capture - the hooks the trade pipeline calls, and the rebuild from the trade row.

Where the hooks sit (every PAPER or LIVE fill goes through them, whatever started it):
* `on_entry`       - `app/trading/persistence.persist_trade`, right after the trade row is committed. Every entry path
                     ends there: the console and webhooks (`execute_signal_for_user`), deployments (the worker),
                     multi-leg structures (`execute_structure`), the public API - and Z3's PAPER approvals, which call
                     `execute_signal_for_user` too. A caller that knows where the trade came from passes `source`
                     (Z3: "PROPOSAL"; a scan's "Then" template: "SCAN"); otherwise it is derived (a deployment ->
                     STRATEGY, else MANUAL).
* `on_marks`       - `position_monitor.monitor_open_positions`, after the sweep's marks are committed: the highest and
                     lowest price seen while open (MAE / MFE).
* `on_stop_change` - `position_monitor._apply_trade_exit_rules` after a stop change is committed (and any future
                     stop-edit endpoint): the stop history the "moved stop against position" tag reads.
* `on_exit`        - `position_monitor.close_position`, right after the exit is committed: the exit fill, costs and
                     the exit chart reference.

Every hook runs in its own database session, only while the `discipline_engine` flag is on for the tenant, and
swallows its own errors (logged): the journal can never fail, slow down or roll back a trade or an order. What a hook
missed (flag off at the time, a trade from before this feature) is rebuilt from the trade row by `ensure_entries`.

A chart snapshot is a reference, not an image: the instrument and the instant, which the chart engine redraws from
the market data lake "as of" that time (ADR-0013 keeps every bar's ingest time, so the redraw shows what was known
then). Greeks / IV at entry are solved from the entry premium and the underlying price at the signal when the
caller passes it (`underlying_entry` in the contract meta); without it they stay empty and say why.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, Iterable, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import JournalEntryRecord, TradeRecord
from app.journal import FLAG

log = logging.getLogger(__name__)

SOURCES = ("MANUAL", "STRATEGY", "SCAN", "PROPOSAL", "WEBHOOK")


def _utc(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def derive_source(trade: TradeRecord) -> str:
    return "STRATEGY" if trade.deployment_id is not None else "MANUAL"


def chart_ref(trade: TradeRecord, at: Optional[datetime]) -> Optional[Dict[str, Any]]:
    when = _utc(at)
    if when is None:
        return None
    return {"symbol": trade.symbol, "exchange": trade.exchange, "instrument_key": trade.instrument_key,
            "underlying_symbol": trade.underlying_symbol, "at": when.isoformat(), "redraw": "market_lake_as_of"}


def _strike_and_right(symbol: str) -> tuple:
    """'NIFTY 24500 CE 01 OCT 26' / 'NIFTY26O0124500CE' -> (24500.0, 'CE'); (None, None) when unreadable."""
    m = re.search(r"\b(\d{3,6}(?:\.\d+)?)\s*(CE|PE)\b", symbol)
    if not m:
        m = re.search(r"(\d{3,6}(?:\.\d+)?)(CE|PE)$", symbol.replace(" ", ""))
    return (float(m.group(1)), m.group(2)) if m else (None, None)


def greeks_at_entry(trade: TradeRecord, underlying_price: Optional[float]) -> Dict[str, Any]:
    """IV and Greeks of an option at its entry fill, or {"unavailable": why}."""
    if (trade.instrument_kind or "UNDERLYING") != "OPTION":
        return {"unavailable": "not an option"}
    if not underlying_price or underlying_price <= 0:
        return {"unavailable": "no underlying price at entry"}
    if trade.expiry is None:
        return {"unavailable": "no expiry on the trade"}
    strike, right = _strike_and_right(trade.symbol)
    if strike is None:
        return {"unavailable": f"cannot read strike/right from {trade.symbol}"}
    from app.market_data.calendar import IST
    from app.option_chain.leg_greeks import compute_leg_greeks
    from app.option_chain.models import OptionLegInput, OptionType
    entry_time = _utc(trade.entry_time) or datetime.now(timezone.utc)
    signed = int(trade.quantity) if trade.direction == "LONG" else -int(trade.quantity)
    try:
        leg = compute_leg_greeks(OptionLegInput(strike=strike, option_type=OptionType.CALL if right == "CE" else OptionType.PUT, quantity=signed,
                                                underlying_ltp=float(underlying_price), expiry=trade.expiry.isoformat(),
                                                option_ltp=float(trade.entry_price), as_of=entry_time.astimezone(IST).date().isoformat()))
    except Exception as exc:  # noqa: BLE001 - a premium outside the no-arbitrage bounds cannot be solved
        return {"unavailable": f"IV not solvable: {str(exc)[:120]}"}
    g = leg.greeks
    return {"underlying_price": round(float(underlying_price), 4), "strike": strike, "right": right,
            "iv": round(g.implied_volatility, 4), "delta": round(g.delta, 4), "gamma": round(g.gamma, 6),
            "theta": round(g.theta, 4), "vega": round(g.vega, 4)}


def _loads(raw: Optional[str], default: Any) -> Any:
    try:
        value = json.loads(raw) if raw else default
    except ValueError:
        return default
    return value if isinstance(value, type(default)) else default


def plan_of(trade: TradeRecord) -> Dict[str, Any]:
    """The levels the trade was taken on: the signal's price (or the fill when the signal had none), the initial stop,
    the targets and the planned reward:risk."""
    entry = float(trade.expected_price) if trade.expected_price else float(trade.entry_price)
    stop = float(trade.initial_stop_loss) if trade.initial_stop_loss is not None else float(trade.stop_loss)
    rr = None
    if trade.target1 is not None and stop and entry != stop:
        rr = round(abs(float(trade.target1) - entry) / abs(entry - stop), 2)
    return {"entry": round(entry, 4), "entry_source": "signal" if trade.expected_price else "fill", "stop": round(stop, 4),
            "target1": trade.target1, "target2": trade.target2, "rr": rr, "setup": trade.strategy_id, "holding": trade.holding,
            "underlying_symbol": trade.underlying_symbol, "underlying_stop_loss": trade.underlying_stop_loss,
            "underlying_target1": trade.underlying_target1, "exit_rules": _loads(trade.exit_rules, {}) or None}


def _side(direction: str, leg: str) -> str:
    opening = "BUY" if direction == "LONG" else "SELL"
    return opening if leg == "ENTRY" else ("SELL" if opening == "BUY" else "BUY")


def apply_trade(entry: JournalEntryRecord, trade: TradeRecord, *, source: Optional[str] = None) -> None:
    """Fills what the trade row can tell: plan (once), entry/exit fills, chart references, the price range seen.
    Idempotent: calling it twice changes nothing the second time."""
    if source and source.upper() in SOURCES:
        entry.source = source.upper()
    elif not entry.source:
        entry.source = derive_source(trade)
    entry.mode = trade.mode
    if not _loads(entry.plan_json, {}):
        entry.plan_json = json.dumps(plan_of(trade), sort_keys=True, default=str)
    fills: List[Dict[str, Any]] = _loads(entry.fills_json, [])
    legs = {f.get("leg") for f in fills}
    entry_time = _utc(trade.entry_time)
    if "ENTRY" not in legs:
        fills.append({"leg": "ENTRY", "side": _side(trade.direction, "ENTRY"), "price": float(trade.entry_price), "quantity": float(trade.quantity),
                      "at": entry_time.isoformat() if entry_time else None, "order_id": trade.broker_order_id,
                      "slippage": trade.slippage, "latency_ms": trade.entry_latency_ms})
    exit_time = _utc(trade.exit_time)
    if trade.exit_price is not None and exit_time is not None and "EXIT" not in legs:
        fills.append({"leg": "EXIT", "side": _side(trade.direction, "EXIT"), "price": float(trade.exit_price), "quantity": float(trade.quantity),
                      "at": exit_time.isoformat(), "order_id": trade.exit_order_id, "reason": trade.exit_reason})
    entry.fills_json = json.dumps(fills, sort_keys=True, default=str)
    if entry.chart_entry_json is None:
        ref = chart_ref(trade, trade.entry_time)
        entry.chart_entry_json = json.dumps(ref, sort_keys=True) if ref else None
    if entry.chart_exit_json is None and exit_time is not None:
        ref = chart_ref(trade, exit_time)
        entry.chart_exit_json = json.dumps(ref, sort_keys=True) if ref else None
    _widen(entry, [float(trade.entry_price)] + ([float(trade.exit_price)] if trade.exit_price is not None else [])
           + ([float(trade.best_price)] if trade.best_price is not None else []))


def _widen(entry: JournalEntryRecord, prices: Iterable[float]) -> None:
    for price in prices:
        if price is None or price <= 0:
            continue
        entry.high_price = price if entry.high_price is None else max(float(entry.high_price), price)
        entry.low_price = price if entry.low_price is None else min(float(entry.low_price), price)


def new_entry(trade: TradeRecord) -> JournalEntryRecord:
    return JournalEntryRecord(tenant_id=trade.tenant_id, user_id=trade.user_id, trade_id=trade.id, source="", mode=trade.mode,
                              plan_json="{}", fills_json="[]", stop_history_json="[]")


async def _entry_for(session: AsyncSession, trade: TradeRecord, *, source: Optional[str] = None) -> JournalEntryRecord:
    entry = await session.scalar(select(JournalEntryRecord).where(JournalEntryRecord.trade_id == trade.id))
    if entry is None:
        entry = new_entry(trade)
        session.add(entry)
    apply_trade(entry, trade, source=source)
    return entry


async def ensure_entries(session: AsyncSession, trades: Iterable[TradeRecord]) -> Dict[int, JournalEntryRecord]:
    """The journal entry of every trade, created or completed from the trade row (the caller commits)."""
    trades = list(trades)
    if not trades:
        return {}
    existing = {e.trade_id: e for e in await session.scalars(
        select(JournalEntryRecord).where(JournalEntryRecord.trade_id.in_([t.id for t in trades])))}
    out: Dict[int, JournalEntryRecord] = {}
    for trade in trades:
        entry = existing.get(trade.id)
        if entry is None:
            entry = new_entry(trade)
            session.add(entry)
        apply_trade(entry, trade)
        out[trade.id] = entry
    return out


# --- the hooks (own session, flag-gated, never raise) -----------------------------------------------------------------

async def _run(session: AsyncSession, tenant_id: int, what: str, work: Callable[[AsyncSession], Awaitable[None]]) -> None:
    try:
        from app.platform.controls import flag_enabled
        async with AsyncSession(bind=session.bind, expire_on_commit=False) as own:
            if not await flag_enabled(own, FLAG, tenant_id):
                return
            await work(own)
            await own.commit()
    except Exception as exc:  # noqa: BLE001 - the journal never fails the trade
        log.warning("Journal capture (%s) skipped for tenant %s: %s", what, tenant_id, exc)


async def on_entry(session: AsyncSession, trade: TradeRecord, *, source: Optional[str] = None,
                   underlying_price: Optional[float] = None) -> None:
    trade_id, tenant_id = trade.id, trade.tenant_id

    async def work(own: AsyncSession) -> None:
        row = await own.get(TradeRecord, trade_id)
        if row is None:
            return
        entry = await _entry_for(own, row, source=source)
        if (row.instrument_kind or "UNDERLYING") == "OPTION" and entry.greeks_entry_json is None:
            greeks = greeks_at_entry(row, underlying_price)
            entry.greeks_entry_json = json.dumps(greeks, sort_keys=True)
            entry.iv_entry = greeks.get("iv")
    await _run(session, tenant_id, "entry", work)


async def on_exit(session: AsyncSession, trade: TradeRecord) -> None:
    trade_id, tenant_id = trade.id, trade.tenant_id

    async def work(own: AsyncSession) -> None:
        row = await own.get(TradeRecord, trade_id)
        if row is not None:
            await _entry_for(own, row)
    await _run(session, tenant_id, "exit", work)


async def on_marks(session: AsyncSession, tenant_id: int, prices: Dict[int, float]) -> None:
    if not prices:
        return
    marks = dict(prices)

    async def work(own: AsyncSession) -> None:
        trades = list(await own.scalars(select(TradeRecord).where(TradeRecord.id.in_(list(marks)), TradeRecord.tenant_id == tenant_id)))
        entries = await ensure_entries(own, trades)
        for trade_id, entry in entries.items():
            _widen(entry, [float(marks[trade_id])])
    await _run(session, tenant_id, "marks", work)


async def on_stop_change(session: AsyncSession, trade: TradeRecord, old: float, new: float, *, source: str = "rule",
                         at: Optional[datetime] = None) -> None:
    trade_id, tenant_id = trade.id, trade.tenant_id
    when = _utc(at) or datetime.now(timezone.utc)

    async def work(own: AsyncSession) -> None:
        row = await own.get(TradeRecord, trade_id)
        if row is None:
            return
        entry = await _entry_for(own, row)
        history: List[Dict[str, Any]] = _loads(entry.stop_history_json, [])
        history.append({"at": when.isoformat(), "from": float(old), "to": float(new), "source": source[:40]})
        entry.stop_history_json = json.dumps(history, sort_keys=True)
    await _run(session, tenant_id, "stop change", work)
