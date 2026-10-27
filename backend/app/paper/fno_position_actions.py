"""Terminal T2 (P1-6): "Exit part" and "Reverse" on an open position (TT-9), and Modify of a waiting order (TT-10).

* Exit part - N of the position's M lots close now; the rest stays open with its own stop and target. It is an exit
  (ADR-0004): priced exactly as "Exit now" (`approval.exit_price`: the person's own live price, else the monitor's recent
  last price, never a typed price) and closed through the one exit path (`close_position`) after the row is split
  (`trading/trade_split.split_off`, TM-17's method; all M lots close the row itself). No kill switch, limit, approval-only
  rule or feature flag stops it. LIVE is refused before anything happens, exactly as "Exit now" refuses it (Phase 0:
  a live position is closed in the broker app); the owner's LIVE switches are never read or written here.
* Reverse - one action: the whole position closes (the same exit, never blocked), then the opposite side opens with the
  same lots through the ONE order ticket (`fno_ticket.place`: every entry check, the PAPER chokepoint, the kill switch).
  When the entry is refused the position stays closed and the answer says why in plain words. Only the holder reverses
  (the new position opens on his own account). Any stop the new side needs is the user's own (`stop_loss`); nothing is
  pre-filled.
* Modify - the price / trigger / lots of the caller's own waiting entry, or the level of his own armed stop / target
  (TT-10: one modify path; PAPER at once). Only a waiting row changes (claimed atomically); one already sent is left.

Idempotency: every call carries the client's `client_ref` (made when the action is composed, reused on retry). A second
"Exit part" with the same reference returns the first result; a second "Reverse" never exits twice nor opens twice (the
entry's order key is unique per organisation). Every action writes an audit row.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, Optional, Tuple

from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import InstrumentRecord, OrderRecord, PaperRestingOrderRecord, TradeRecord, User

PART_REF = "exit_part_ref"
LIVE_REFUSED = "LIVE positions are closed in the broker app - ATP places no broker order in Phase 0"


def ref_tag(client_ref: str) -> str:
    """A short, stable tag of the client's reference (fits the exit reason and `group_meta`). Pure."""
    return hashlib.sha256(client_ref.encode()).hexdigest()[:12]


def lots_of(trade: TradeRecord) -> Tuple[int, float]:
    """(open lots, units per lot). A position without a lot size counts one unit per lot. Pure."""
    unit = float(trade.lot_size or 1)
    return int(round(float(trade.quantity) / unit)), unit


def _side_words(trade: TradeRecord) -> str:
    return "Buy" if (trade.direction or "").upper() == "LONG" else "Sell"


async def _own(session: AsyncSession, user: User, trade_id: int, *, holder_only: bool = False) -> TradeRecord:
    from app.execution import approval
    trade = await session.get(TradeRecord, trade_id)
    if trade is None or trade.tenant_id != user.tenant_id:
        raise HTTPException(status_code=404, detail="Unknown position")
    if holder_only and trade.user_id != user.id:
        raise HTTPException(status_code=403, detail="Reverse opens a new position on your own account: only the holder can reverse it")
    if not approval.may_act_on_trade(user, trade):
        raise HTTPException(status_code=403, detail="This position belongs to another member; only they or the organisation owner can close it")
    return trade


def _open_checks(trade: TradeRecord) -> None:
    if trade.exit_time is not None:
        raise HTTPException(status_code=409, detail="Position is already closed")
    if trade.mode != "PAPER":
        raise HTTPException(status_code=409, detail=LIVE_REFUSED)
    if trade.leg_group_id:
        raise HTTPException(status_code=409, detail="This leg belongs to a strategy structure: use Exit now, which closes the whole structure")


async def _hedge_warning(session: AsyncSession, trade: TradeRecord) -> Optional[str]:
    """TT-8: a hedge reduced or closed while its sold leg is open - a warning to confirm, never a refusal."""
    from app.paper import fno_groups
    _, legs = await fno_groups.group_of(session, trade)
    return fno_groups.hedge_warning(trade, legs)


async def _price(session: AsyncSession, user: User, trade: TradeRecord) -> Tuple[float, str]:
    from app.ai.worker_bridge import price_lookup_for_user
    from app.execution.approval import exit_price
    try:
        return await exit_price(session, trade, await price_lookup_for_user(session, user))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


def _closed_row(trade: TradeRecord, lots: int) -> Dict[str, Any]:
    return {"trade_id": trade.id, "symbol": trade.symbol, "lots": lots, "closed": trade.exit_time is not None,
            "exit_price": trade.exit_price, "pnl": trade.pnl}


# --- Exit part -----------------------------------------------------------------------------------------------------------

async def _part_done(session: AsyncSession, user: User, trade: TradeRecord, tag: str) -> Optional[TradeRecord]:
    """The row an earlier "Exit part" with this reference closed (or is closing), if any."""
    needle = f'"{PART_REF}": "{tag}"'
    return await session.scalar(select(TradeRecord).where(
        TradeRecord.tenant_id == trade.tenant_id, TradeRecord.user_id == trade.user_id,
        TradeRecord.group_meta.contains(needle)).limit(1))


async def exit_part(session: AsyncSession, user: User, trade_id: int, *, lots: int, client_ref: str,
                    confirm: bool = False) -> Dict[str, Any]:
    """Closes `lots` of this position now; the rest stays open. 1 <= lots <= the open lots, else a plain refusal (422)
    that closes nothing. An exit: never stopped by a kill switch, a limit, approval-only or a flag."""
    from app.audit.log import write_audit_log
    from app.trading.position_monitor import close_position
    from app.trading.trade_split import SplitError, split_off
    trade = await _own(session, user, trade_id)
    tag = ref_tag(client_ref)
    earlier = await _part_done(session, user, trade, tag)
    if earlier is not None:
        if earlier.exit_time is None:
            raise HTTPException(status_code=409, detail="This exit is already on its way - refresh in a moment")
        return {"status": "CLOSED", "repeat": True, "warning": None, "closed": _closed_row(earlier, lots_of(earlier)[0]),
                "remaining_lots": lots_of(trade)[0] if trade.exit_time is None else 0}
    _open_checks(trade)
    held, unit = lots_of(trade)
    if lots < 1 or lots > held:
        raise HTTPException(status_code=422, detail=(f"You hold {held} lot{'s' if held != 1 else ''} of {trade.symbol}. "
                                                     f"Choose between 1 and {held} lot{'s' if held != 1 else ''} to exit - nothing was closed."))
    warning = await _hedge_warning(session, trade)
    if warning and not confirm:
        return {"status": "CONFIRM", "warning": warning, "closed": None, "remaining_lots": held}
    price, source = await _price(session, user, trade)
    marks = {PART_REF: tag}
    if lots == held:
        target = trade
        meta = json.loads(trade.group_meta or "{}") if trade.group_meta else {}
        meta = meta if isinstance(meta, dict) else {}
        meta.update(marks)
        trade.group_meta = json.dumps(meta)
    else:
        try:
            target = await split_off(session, trade, lots * unit, marks=marks)
        except SplitError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
    await write_audit_log(session, trade.tenant_id, user.id, "exit_part",
                          f"trade #{trade.id}: {lots} of {held} lot(s) at {source}{' after the hedge warning' if warning else ''}")
    await session.commit()
    outcome = await close_position(session, target, price, f"Exit part ({source})", broker=None, user_id=user.id)
    if not outcome.closed:
        raise HTTPException(status_code=409, detail="; ".join(outcome.warnings) or "Position could not be closed")
    return {"status": "CLOSED", "repeat": False, "warning": warning, "closed": _closed_row(target, lots),
            "remaining_lots": held - lots}


# --- Reverse -------------------------------------------------------------------------------------------------------------

async def opposite_ask(session: AsyncSession, trade: TradeRecord, *, client_ref: str, stop_loss: Optional[float]) -> Any:
    """The ticket order of the other side with the same lots on the same contract (market, the same product)."""
    from app.paper import fno_ticket
    if (trade.instrument_kind or "") not in ("OPTION", "FUTURE"):
        raise HTTPException(status_code=409, detail="Reverse works on option and future positions")
    row = await session.scalar(select(InstrumentRecord).where(
        InstrumentRecord.tradingsymbol == trade.symbol, InstrumentRecord.expiry == trade.expiry).limit(1))
    if row is None or row.expiry is None or not row.underlying:
        raise HTTPException(status_code=409, detail=f"{trade.symbol} is not in our contract list any more, so it cannot be reversed here")
    held, _ = lots_of(trade)
    right = "FUT" if trade.instrument_kind == "FUTURE" else str(row.instrument_type or "").upper()
    return fno_ticket.OrderAsk(
        underlying=row.underlying, expiry=row.expiry, strike=None if right == "FUT" else float(row.strike or 0.0), right=right,
        side="SELL" if (trade.direction or "").upper() == "LONG" else "BUY", lots=held, order_type="MARKET",
        product="NRML" if (trade.holding or "") == "SWING" else "MIS", stop_loss=stop_loss, client_ref=f"rev-{client_ref}")


def _entry_row(order: Optional[OrderRecord], placed: Dict[str, Any]) -> Dict[str, Any]:
    if order is not None:
        try:
            reasons = [str(r) for r in json.loads(order.reasons_json or "[]")] if not order.trade_id else []
        except ValueError:
            reasons = []
        return {"status": "PLACED" if order.trade_id else "REFUSED", "order_id": order.id, "trade_id": order.trade_id,
                "reasons": reasons, "filled": None}
    return {"status": placed.get("status"), "order_id": placed.get("order_id"), "trade_id": placed.get("trade_id"),
            "reasons": list(placed.get("reasons") or []), "filled": placed.get("filled")}


def reverse_text(symbol: str, entry: Dict[str, Any]) -> str:
    """The plain line after a reverse. Pure."""
    if entry.get("status") == "PLACED":
        return f"{symbol} closed and the opposite position is open."
    why = "; ".join(str(r) for r in entry.get("reasons") or []) or "it was not placed"
    return f"{symbol} closed. The opposite position was not opened: {why}"


async def reverse(session: AsyncSession, user: User, trade_id: int, *, client_ref: str, stop_loss: Optional[float] = None,
                  preview: bool = False, confirm: bool = False) -> Dict[str, Any]:
    """Exit the whole position, then open the opposite side with the same lots - one action. `preview` checks the new
    side (the ticket's confirm figures) and changes nothing. The exit half is never blocked; the entry half runs every
    entry check and, when refused, leaves the position closed and says why."""
    from app.ai.worker_bridge import price_lookup_for_user
    from app.audit.log import write_audit_log
    from app.execution.approval import ORIGIN_MANUAL
    from app.paper import fno_ticket
    from app.trading.position_monitor import close_position
    trade = await _own(session, user, trade_id, holder_only=True)
    tag = ref_tag(client_ref)
    marker = f"ref {tag}"
    ask = await opposite_ask(session, trade, client_ref=client_ref, stop_loss=stop_loss)
    key = f"fno:{user.id}:{ask.client_ref}"
    earlier = await session.scalar(select(OrderRecord).where(OrderRecord.tenant_id == user.tenant_id, OrderRecord.idempotency_key == key))
    if earlier is not None:
        entry = _entry_row(earlier, {})
        return {"status": "REVERSED" if entry["status"] == "PLACED" else "EXITED", "repeat": True, "warning": None,
                "closed": _closed_row(trade, lots_of(trade)[0]), "entry": entry, "text": reverse_text(trade.symbol, entry)}
    resumed = trade.exit_time is not None and (trade.exit_reason or "").endswith(marker)
    if not resumed:
        _open_checks(trade)
    lookup = await price_lookup_for_user(session, user)
    if preview:
        view = await fno_ticket.assess(session, user, ask, price_lookup=lookup, origin=ORIGIN_MANUAL)
        await session.commit()
        return {"status": "PREVIEW", "warning": await _hedge_warning(session, trade), "lots": ask.lots,
                "exit": {"trade_id": trade.id, "symbol": trade.symbol, "side": _side_words(trade)}, "entry": view}
    try:
        contract = await fno_ticket.find_contract(session, ask.underlying, ask.expiry, ask.strike, ask.right)
    except fno_ticket.ContractNotFound as exc:
        raise HTTPException(status_code=409, detail=f"{exc} Nothing was closed.") from exc
    problems = fno_ticket.ask_errors(ask, contract.tick_size)
    if problems:
        # The user's own input for the new side is missing (e.g. a sold option's stop-loss): nothing is closed yet.
        raise HTTPException(status_code=422, detail=f"{' '.join(problems)}. Nothing was closed.")
    warning = None if resumed else await _hedge_warning(session, trade)
    if warning and not confirm:
        return {"status": "CONFIRM", "warning": warning, "closed": None, "entry": None}
    if not resumed:
        price, source = await _price(session, user, trade)
        await write_audit_log(session, trade.tenant_id, user.id, "reverse",
                              f"trade #{trade.id}: exit {lots_of(trade)[0]} lot(s) at {source}, then the opposite side")
        await session.commit()
        outcome = await close_position(session, trade, price, f"Reverse ({source}) {marker}", broker=None, user_id=user.id)
        if not outcome.closed:
            await session.refresh(trade)
            if not (trade.exit_reason or "").endswith(marker):          # closed by something else: no new position
                raise HTTPException(status_code=409, detail="; ".join(outcome.warnings) or "Position could not be closed")
    placed = await fno_ticket.place(session, user, ask, price_lookup=lookup, origin=ORIGIN_MANUAL)
    entry = _entry_row(None, placed)
    return {"status": "REVERSED" if entry["status"] == "PLACED" else "EXITED", "repeat": resumed, "warning": warning,
            "closed": _closed_row(trade, ask.lots), "entry": entry, "text": reverse_text(trade.symbol, entry)}


# --- Modify a waiting order (TT-10) --------------------------------------------------------------------------------------

async def _tick(session: AsyncSession, symbol: str) -> float:
    row = await session.scalar(select(InstrumentRecord).where(InstrumentRecord.tradingsymbol == symbol).limit(1))
    return float(row.tick_size) if row is not None and row.tick_size else 0.05


async def modify_resting(session: AsyncSession, user: User, row_id: int, *, price: Optional[float] = None,
                         trigger_price: Optional[float] = None, lots: Optional[int] = None, level: Optional[float] = None,
                         spot_level: Optional[float] = None) -> Dict[str, Any]:
    """Changes the caller's own waiting order. An entry: its limit price, trigger and lots (checked like the ticket; every
    entry check runs again when it fires). An armed exit: its level (a stop's trigger, a target's limit, or the
    underlying's level). Nothing is sent now; an order already sent or done is not changed (409)."""
    from app.audit.log import write_audit_log
    from app.execution import resting_book
    from app.paper import fno_ticket
    row = await session.get(PaperRestingOrderRecord, row_id)
    if row is None or row.tenant_id != user.tenant_id or row.user_id != user.id:
        raise HTTPException(status_code=404, detail="Not found")
    if row.status != resting_book.RESTING:
        raise HTTPException(status_code=409, detail=f"This order is {resting_book.STATUS_WORDS.get(row.status, row.status).lower()}: it can no longer be changed")
    changes: Dict[str, Any]
    if row.purpose == resting_book.ENTRY:
        if level is not None or spot_level is not None:
            raise HTTPException(status_code=422, detail="A waiting entry changes by its price, trigger or lots")
        payload = json.loads(row.payload_json or "{}")
        ask = fno_ticket.ask_from_payload(payload["ask"])
        old_lots = max(1, int(ask.lots))
        if price is not None:
            ask.price = price
        if trigger_price is not None:
            ask.trigger_price = trigger_price
        if lots is not None:
            ask.lots = lots
        problems = fno_ticket.ask_errors(ask, await _tick(session, row.symbol))
        if problems:
            raise HTTPException(status_code=422, detail=". ".join(problems))
        new_level = fno_ticket.rest_level(ask)
        payload["ask"] = fno_ticket.ask_payload(ask)
        changes = {"price": ask.price, "trigger_price": ask.trigger_price, "quantity": float(row.quantity) / old_lots * ask.lots,
                   "trigger_level": new_level, "payload_json": json.dumps(payload, default=str, separators=(",", ":"))}
        note = f"Changed by you: {ask.lots} lot(s), waiting for {new_level:,.2f}"
    else:
        if price is not None or trigger_price is not None or lots is not None:
            raise HTTPException(status_code=422, detail="A stop or target closes the whole position: change its level only (use Exit part to close some lots now)")
        if row.trigger_source == resting_book.SPOT:
            if spot_level is None:
                raise HTTPException(status_code=422, detail="Set the underlying's new level")
            changes = {"trigger_level": float(spot_level)}
            note = f"Changed by you: underlying {spot_level:,.2f}"
        else:
            if level is None:
                raise HTTPException(status_code=422, detail="Set the new price of this exit")
            if not fno_ticket.on_tick(float(level), await _tick(session, row.symbol)):
                raise HTTPException(status_code=422, detail=f"The price {level:g} is not a multiple of the contract's tick size")
            changes = {"trigger_level": float(level), ("price" if row.order_type == "LIMIT" else "trigger_price"): float(level)}
            note = f"Changed by you: {row.kind.lower()} at {level:,.2f}"
    done = await session.execute(update(PaperRestingOrderRecord).where(
        PaperRestingOrderRecord.id == row.id, PaperRestingOrderRecord.status == resting_book.RESTING,
    ).values(**changes).execution_options(synchronize_session=False))
    await session.refresh(row)
    if getattr(done, "rowcount", 0) != 1:
        await session.commit()
        raise HTTPException(status_code=409, detail="This order was sent at the same moment: it was not changed")
    resting_book.add_trail(row, resting_book.RESTING, note)
    await write_audit_log(session, user.tenant_id, user.id, "resting_modify", f"waiting order #{row.id}: {note}")
    await session.commit()
    return resting_book.view(row)
