"""TT-8 (owner, 24 Oct): the legs of one manual basket are one group, with "Exit this leg" and "Exit the whole group".

The group is stored on existing columns, no migration: every filled leg carries `group_meta` {"basket_group": <the
basket's own id>} and `leg_role` SHORT (a sold leg) / LONG (a bought leg). `leg_group_id` is deliberately NOT used: it
makes the position monitor judge the legs as one deployment structure (its group stop / target from `group_meta`), and a
manual leg keeps its own stop and target, judged on its own exactly as before. Baskets placed before TT-8 are found from
their orders' idempotency keys (`fno_contracts.basket_ref_of`), as the positions page already groups them.

Both exits are exits (ADR-0004): no kill switch, limit, approval-only rule or feature flag ever stops them. They price
each leg as "Exit now" does (`approval.exit_price`: the person's own live price, else the monitor's recent last price,
never a typed price) and close through the existing close path (`close_position` / `close_group`). A LIVE leg goes out
through its own broker session (`broker_for_trade`), as every LIVE exit does; Phase 0's runtime lock still applies.

"Exit this leg" on a hedge (a bought option protecting a sold one of the same side, CE / PE) while that sold leg is still
open answers a plain warning and closes nothing until the call carries `confirm` - it is never refused. "Exit the whole
group" closes the sold legs first, then the hedges (`close_group`)."""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import OrderRecord, TradeRecord, User

logger = logging.getLogger(__name__)

GROUP_KEY = "basket_group"


def _meta(trade: TradeRecord) -> Dict[str, Any]:
    try:
        meta = json.loads(trade.group_meta or "{}")
    except ValueError:
        return {}
    return meta if isinstance(meta, dict) else {}


def role_of(trade: TradeRecord) -> str:
    """SHORT for a sold leg, LONG for a bought one (the stored `leg_role` first). Pure."""
    if trade.leg_role in ("SHORT", "LONG"):
        return str(trade.leg_role)
    return "SHORT" if (trade.direction or "").upper() == "SHORT" else "LONG"


def stored_group(trade: TradeRecord) -> Optional[str]:
    ref = _meta(trade).get(GROUP_KEY)
    return str(ref) if ref else None


def tag(trades: Sequence[TradeRecord], basket_ref: str) -> None:
    """Marks the filled legs of one basket as its group (the caller commits). Keeps whatever else `group_meta` holds."""
    for trade in trades:
        meta = _meta(trade)
        meta[GROUP_KEY] = basket_ref
        trade.group_meta = json.dumps(meta)
        trade.leg_role = role_of(trade)


async def tag_filled(session: AsyncSession, user: User, basket_ref: str, trade_ids: Sequence[int]) -> None:
    """After a basket run: the caller's own filled legs become (or join) the basket's group. Never fails the run."""
    ids = [int(i) for i in trade_ids if i]
    if not ids:
        return
    try:
        trades = list(await session.scalars(select(TradeRecord).where(
            TradeRecord.tenant_id == user.tenant_id, TradeRecord.user_id == user.id, TradeRecord.id.in_(ids))))
        tag(trades, basket_ref)
        await session.commit()
    except Exception as exc:  # noqa: BLE001 - the legs are placed; the group falls back to the order keys
        logger.warning("Basket %s: legs not tagged as a group: %s", basket_ref, type(exc).__name__)
        await session.rollback()


async def basket_refs(session: AsyncSession, tenant_id: int, user_id: int, trades: Sequence[TradeRecord]) -> Dict[int, str]:
    """trade id -> the basket it belongs to: the stored group first, else its order's idempotency key (baskets placed
    before TT-8). Only one holder's trades in one tenant."""
    from app.paper.fno_contracts import basket_ref_of
    out: Dict[int, str] = {}
    for t in trades:
        ref = stored_group(t)
        if ref:
            out[t.id] = ref
    rest = [t.id for t in trades if t.id not in out]
    if rest:
        rows = await session.execute(select(OrderRecord.trade_id, OrderRecord.idempotency_key).where(
            OrderRecord.tenant_id == tenant_id, OrderRecord.user_id == user_id, OrderRecord.trade_id.in_(rest)))
        for trade_id, key in rows.all():
            ref = basket_ref_of(key, user_id)
            if ref and trade_id is not None:
                out.setdefault(int(trade_id), ref)
    return out


async def group_of(session: AsyncSession, trade: TradeRecord) -> Tuple[Optional[str], List[TradeRecord]]:
    """The basket this trade belongs to and every OPEN leg of it (same tenant, same holder), oldest first; (None, [trade])
    for a trade that is not a basket leg."""
    if trade.user_id is None:
        return None, [trade]
    ref = (await basket_refs(session, trade.tenant_id, trade.user_id, [trade])).get(trade.id)
    if ref is None:
        return None, [trade]
    open_trades = list(await session.scalars(select(TradeRecord).where(
        TradeRecord.tenant_id == trade.tenant_id, TradeRecord.user_id == trade.user_id, TradeRecord.exit_time.is_(None),
        TradeRecord.leg_group_id.is_(None)).order_by(TradeRecord.id)))
    refs = await basket_refs(session, trade.tenant_id, trade.user_id, open_trades)
    legs = [t for t in open_trades if refs.get(t.id) == ref]
    return ref, legs or [trade]


_RIGHT = re.compile(r"(?:^|[\s\d])(CE|PE)(?=\s|$)")


def _right(trade: TradeRecord) -> Optional[str]:
    """CE / PE from the contract's symbol, in either form ("NIFTY25OCT25000CE", "NIFTY 24900 CE 27 OCT 26"). Pure."""
    found = _RIGHT.findall((trade.symbol or "").upper())
    return found[-1] if found else None


def shorts_protected(leg: TradeRecord, legs: Sequence[TradeRecord]) -> List[TradeRecord]:
    """The open sold option legs of the group this bought option leg protects (same side, CE / PE; any side when the
    contract's side is unknown). Empty for a sold leg, a future, or no open short. Pure."""
    if role_of(leg) != "LONG" or (leg.instrument_kind or "") != "OPTION":
        return []
    right = _right(leg)
    return [t for t in legs if t.id != leg.id and t.exit_time is None and role_of(t) == "SHORT"
            and (t.instrument_kind or "") == "OPTION" and (right is None or _right(t) in (None, right))]


def hedge_warning(leg: TradeRecord, legs: Sequence[TradeRecord]) -> Optional[str]:
    """The plain warning for closing a hedge before its sold leg, or None. Pure."""
    shorts = shorts_protected(leg, legs)
    if not shorts:
        return None
    names = ", ".join(t.symbol for t in shorts)
    one = len(shorts) == 1
    return (f"{leg.symbol} protects your sold {'leg' if one else 'legs'} {names}, which {'is' if one else 'are'} still open. "
            f"If you exit it first, {'that sold leg has' if one else 'those sold legs have'} no protection and the possible loss "
            f"is no longer capped. You can still exit it - confirm to go ahead, or use \"Exit the whole group\" to close the "
            "sold legs first.")


def _check(user: User, trade: Optional[TradeRecord]) -> TradeRecord:
    from app.execution import approval
    if trade is None or trade.tenant_id != user.tenant_id:
        raise HTTPException(status_code=404, detail="Unknown position")
    if not approval.may_act_on_trade(user, trade):
        raise HTTPException(status_code=403, detail="This position belongs to another member; only they or the organisation owner can close it")
    if trade.exit_time is not None:
        raise HTTPException(status_code=409, detail="Position is already closed")
    if trade.leg_group_id:
        # A strategy structure's legs are judged and closed together (its group stop); "Exit now" closes it whole.
        raise HTTPException(status_code=409, detail="This leg belongs to a strategy structure: use Exit now, which closes the whole structure")
    return trade


async def _prices(session: AsyncSession, user: User, legs: Sequence[TradeRecord]) -> Tuple[Dict[int, float], str]:
    """Every leg's exit price. M68 PR-3 (TS1-097, ADR-0004): never refused for a missing quote - a leg with no live or
    recent price closes at its older price, else its entry price, and the label says so (approval.exit_now_price)."""
    from app.ai.worker_bridge import price_lookup_for_user
    from app.execution.approval import exit_now_price, source_words
    lookup = await price_lookup_for_user(session, user)
    prices: Dict[int, float] = {}
    sources: List[str] = []
    for leg in legs:
        prices[leg.id], source = await exit_now_price(session, leg, lookup)
        sources.append(source)
    return prices, source_words(sources)


def _row(trade: TradeRecord, outcome: Any) -> Dict[str, Any]:
    return {"trade_id": trade.id, "symbol": trade.symbol, "closed": bool(outcome.closed), "exit_price": outcome.exit_price,
            "pnl": outcome.pnl, "warnings": list(outcome.warnings)}


async def exit_leg(session: AsyncSession, user: User, trade_id: int, *, confirm: bool = False) -> Dict[str, Any]:
    """"Exit this leg": closes this one leg only; the group's other legs stay open. A hedge whose sold leg is still open
    answers {"status": "CONFIRM", "warning"} and closes nothing until `confirm` is true - never a refusal (ADR-0004)."""
    from app.audit.log import write_audit_log
    from app.trading.position_monitor import broker_for_trade, close_position
    trade = _check(user, await session.get(TradeRecord, trade_id))
    ref, legs = await group_of(session, trade)
    warning = hedge_warning(trade, legs)
    if warning and not confirm:
        return {"status": "CONFIRM", "warning": warning, "group": ref, "legs": []}
    try:
        prices, source = await _prices(session, user, [trade])
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await write_audit_log(session, trade.tenant_id, user.id, "exit_leg",
                          f"trade #{trade.id} of basket {ref or '-'} at {source}{' after the hedge warning' if warning else ''}")
    await session.commit()
    broker = await broker_for_trade(session, trade) if trade.mode == "LIVE" else None
    outcome = await close_position(session, trade, prices[trade.id], f"Exit this leg ({source})", broker=broker, user_id=user.id)
    if not outcome.closed:
        raise HTTPException(status_code=409, detail="; ".join(outcome.warnings) or "Position could not be closed")
    return {"status": "CLOSED", "warning": warning, "group": ref, "legs": [_row(trade, outcome)]}


async def exit_group(session: AsyncSession, user: User, trade_id: int) -> Dict[str, Any]:
    """"Exit the whole group": every open leg of this leg's basket, the sold legs first, then the hedges (`close_group`;
    a LIVE short that does not close keeps its hedges open). Priced before anything closes; a leg without a live or recent
    price is priced at its older / entry price (M68 PR-3, TS1-097: never refused for a price)."""
    from app.audit.log import write_audit_log
    from app.trading.position_monitor import close_group
    trade = _check(user, await session.get(TradeRecord, trade_id))
    ref, legs = await group_of(session, trade)
    try:
        prices, source = await _prices(session, user, legs)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    for leg in legs:
        leg.leg_role = role_of(leg)          # close_group orders by role: sold legs first
    await write_audit_log(session, trade.tenant_id, user.id, "exit_group", f"basket {ref or '-'}: {len(legs)} leg(s) at {source}")
    await session.commit()
    by_id = {leg.id: leg for leg in legs}
    outcomes = await close_group(session, legs, prices, f"Exit the whole group ({source})", broker=None, user_id=user.id)
    rows = [_row(by_id[o.trade_id], o) for o in outcomes if o.trade_id in by_id]
    if not any(r["closed"] for r in rows):
        raise HTTPException(status_code=409, detail="; ".join(w for r in rows for w in r["warnings"]) or "The group could not be closed")
    return {"status": "CLOSED" if all(r["closed"] for r in rows) else "PARTIAL", "group": ref, "legs": rows}
