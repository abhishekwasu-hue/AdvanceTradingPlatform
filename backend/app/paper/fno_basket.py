"""FNO-2 (owner order ATP_SESSION_FNO_WORKSPACE.md, section 5): the basket of option legs - its numbers, its confirm
step and "Execute basket" - PAPER only (Phase 0). FNO-3: a leg may also be a future (`right` FUT, no strike), added from
the futures chain; the builder's engine values it linearly, its margin is the broker's (never invented) and, as on the
ticket, it needs a stop-loss on either side.

* **evaluate** - the combined payoff (at expiry and today / T+0), max profit and loss, breakevens, PoP, net premium and
  the Greeks of the whole basket, from the Options Builder's engine (`app.options_builder.routes._evaluate`, the same
  numbers the builder shows), with the lot sizes from the instrument master. The margin is ATP's ESTIMATE, labelled so,
  WITH the hedge benefit: the builder's rule on the whole basket (a defined-risk basket needs at least its max loss),
  beside the same rule on the short legs alone (which, uncovered, need the broker's SPAN margin - never invented). No
  broker's margin API is called (open question FNO-2-1).
* **preview** - the confirm step: every leg checked by the ONE order ticket (`fno_ticket.assess`: the contract, the
  live price, the risk checks in plain words), in placement order (hedge legs first), plus the basket's own check
  that the user's open-position and trades-per-day limits leave room for every leg, and the total margin and max loss.
* **execute** - checks everything again at the live price; if anything would refuse, nothing is placed. Then each leg
  is ONE PAPER order through the same ticket (`fno_ticket.place`, which asserts `phase0_lock.assert_paper` at the
  chokepoint): every BUY (hedge) leg first, then the SELL legs, so the protection is in place before the short. A sold
  leg is never placed after its hedges failed (it would be uncovered): it is reported "not placed" instead. A leg that
  fails is reported plainly with what the user can do; nothing is retried by itself and nothing that filled is undone
  (the user exits it with "Exit now" - exits are never blocked, ADR-0004). The AI never calls this (ADR-0006).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

from fastapi import HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import ValidationError
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import InstrumentRecord, StrategyDeploymentRecord, User
from app.instruments import master
from app.paper import fno_ticket

logger = logging.getLogger(__name__)

MAX_LEGS = 12                       # the Options Builder's limit for one strategy
NEUTRAL_WIDTH_STEPS = 2             # a neutral starting width the user edits (no setting of their own)
NEUTRAL_LOTS = 1
# DEF-0 D3 (owner, 28 Oct): no built-in stop for a sold leg - the trader's own premium stop, else none (he sets each leg)


@dataclass
class LegAsk:
    underlying: str
    expiry: date
    strike: Optional[float]          # None for a future (FNO-3)
    right: str                       # CE | PE | FUT
    side: str                        # BUY | SELL
    lots: int
    price: Optional[float] = None    # the price the panel plans with (the chain's, or the user's own); placing uses the live one
    stop_loss: Optional[float] = None    # a sold leg's stop (premium) or a future's stop (either side), required as on the ticket
    iv: Optional[float] = None       # fraction (0.14); solved from the price when absent


def hedge_first(legs: Sequence[LegAsk]) -> List[Tuple[int, LegAsk]]:
    """(index in the request, leg) in placement order: every BUY before every SELL, each group in its own order."""
    indexed = list(enumerate(legs))
    return [p for p in indexed if p[1].side == "BUY"] + [p for p in indexed if p[1].side != "BUY"]


def label(leg: LegAsk) -> str:
    return fno_ticket.contract_label(master.underlying_of(leg.underlying), leg.expiry, leg.strike, leg.right)


# --- the numbers ---------------------------------------------------------------------------------------------------

def _builder_leg(leg: LegAsk, lot_size: int, premium: float) -> Dict[str, Any]:
    # A future has no strike: the builder takes its entry price as the reference (its payoff is linear, no kink there).
    strike = float(premium) if leg.right == fno_ticket.FUTURE or leg.strike is None else float(leg.strike)
    return {"direction": leg.side, "option_type": leg.right, "strike": strike, "premium": float(premium),
            "lots": int(leg.lots), "lot_size": int(lot_size), "expiry": leg.expiry.isoformat(), "iv": leg.iv}


def hedge_margin(builder_legs: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """The margin estimate with the hedge (the whole basket) and without it (the sold legs alone), each labelled; the
    benefit only when both are numbers. Never the broker's number."""
    from app.options_builder import metrics, model
    def estimate(legs: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
        single = len({leg["expiry"] for leg in legs}) == 1
        return metrics.margin_estimate(legs, model.payoff_extremes(legs) if single else None)
    with_hedge = estimate(builder_legs)
    shorts = [leg for leg in builder_legs if leg["direction"] == "SELL"]
    hedged = bool(shorts) and len(shorts) < len(builder_legs)
    without = estimate(shorts) if hedged else None
    benefit = None
    if without is not None and with_hedge["value"] is not None and without["value"] is not None:
        benefit = round(without["value"] - with_hedge["value"], 2)
    note = ("ATP's estimate, not your broker's number: the broker's margin API is not used in practice mode. "
            "With the hedge legs placed first, the broker sees the protection before the short.")
    return {"with_hedge": with_hedge, "without_hedge": without, "benefit": benefit, "hedged": hedged, "source": "estimate",
            "note": note}


async def _contracts(session: AsyncSession, legs: Sequence[LegAsk]) -> Tuple[List[Optional[fno_ticket.Contract]], List[str]]:
    found: List[Optional[fno_ticket.Contract]] = []
    errors: List[str] = []
    for leg in legs:
        try:
            found.append(await fno_ticket.find_contract(session, leg.underlying, leg.expiry, leg.strike, leg.right))
        except fno_ticket.ContractNotFound as exc:
            found.append(None)
            errors.append(str(exc))
    return found, errors


def range_pct(spot: float, builder_legs: Sequence[Dict[str, Any]], low: float = 4.0) -> float:
    """The chart's half-width in % of spot: every strike inside with a margin (as the builder page's `rangePctFor`)."""
    far = max((abs(float(leg["strike"]) - spot) / spot for leg in builder_legs if leg["option_type"] != fno_ticket.FUTURE), default=0.0)
    return float(min(60.0, max(low, -(-(far * 100 * 1.25 + 2) // 1))))


def _compute(builder_legs: List[Dict[str, Any]], spot: float, as_of: Optional[datetime]) -> Dict[str, Any]:
    from app.options_builder.routes import EvaluateBody, _evaluate
    body = EvaluateBody.model_validate({"legs": builder_legs, "spot": spot, "as_of": as_of, "range_pct": range_pct(spot, builder_legs),
                                        "points": 241})
    return _evaluate(body)


async def evaluate(session: AsyncSession, legs: Sequence[LegAsk], spot: float, *, as_of: Optional[datetime] = None) -> Dict[str, Any]:
    """The basket's numbers at the planning prices. Every leg needs a price (the chain's, or the user's own)."""
    contracts, errors = await _contracts(session, legs)
    missing = [label(leg) for leg in legs if leg.price is None or leg.price <= 0]
    if missing:
        errors.append(f"No price for {', '.join(missing)}: enter one to see the numbers.")
    if errors:
        return {"ok": False, "errors": errors}
    builder_legs = [_builder_leg(leg, c.lot_size, float(leg.price or 0)) for leg, c in zip(legs, contracts) if c is not None]
    try:
        out = await run_in_threadpool(_compute, builder_legs, spot, as_of)
    except (HTTPException, ValidationError, ValueError) as exc:     # the engine's own plain reason (an IV it cannot solve)
        detail = exc.detail if isinstance(exc, HTTPException) else str(exc)
        logger.info("FNO basket numbers not computed: %s", detail)
        return {"ok": False, "errors": [str(detail)]}
    out["ok"] = True
    out["errors"] = []
    out["margin"] = {**out["margin"], "hedge": hedge_margin(builder_legs)}
    out["lot_sizes"] = [c.lot_size if c else None for c in contracts]
    out["labels"] = [c.label if c else label(leg) for leg, c in zip(legs, contracts)]
    out["order"] = [i for i, _ in hedge_first(legs)]
    return out


# --- the user's own settings for the ready strategies ----------------------------------------------------------------

async def settings(session: AsyncSession, user: User, underlying: str) -> Dict[str, Any]:
    """Spread width (in strike steps), lots and a sold leg's stop (% above its price) from the user's own options
    deployment - on this underlying first, else their latest one; a neutral default otherwise, said so."""
    name = master.underlying_of(underlying)
    rows = list(await session.scalars(select(StrategyDeploymentRecord).where(
        StrategyDeploymentRecord.tenant_id == user.tenant_id, StrategyDeploymentRecord.instrument_kind == "OPTION",
        or_(StrategyDeploymentRecord.created_by == user.id, StrategyDeploymentRecord.created_by.is_(None)))
        .order_by(StrategyDeploymentRecord.updated_at.desc(), StrategyDeploymentRecord.id.desc()).limit(50)))
    rows.sort(key=lambda d: (master.underlying_of(d.symbol) != name, d.status != "ACTIVE"))    # stable: newest first within
    dep = rows[0] if rows else None
    if dep is None:
        return {"source": None, "width_steps": NEUTRAL_WIDTH_STEPS, "lots": NEUTRAL_LOTS, "stop_pct": None,
                "note": (f"You have no options settings yet, so a neutral start is used: wings {NEUTRAL_WIDTH_STEPS} strikes apart, "
                         f"{NEUTRAL_LOTS} lot, no stop-loss on a sold leg until you set one. Edit any leg.")}
    width = int(dep.spread_width or NEUTRAL_WIDTH_STEPS)
    lots = int(dep.max_lots or NEUTRAL_LOTS)
    stop = float(dep.premium_stop_pct) if dep.premium_stop_pct is not None else None
    where = f"{dep.strategy_id} on {dep.symbol}"
    stop_text = f"a sold leg's stop-loss {stop:g}% above its price" if stop is not None else "no stop-loss on a sold leg until you set one"
    return {"source": where, "width_steps": width, "lots": lots, "stop_pct": stop,
            "note": f"From your settings ({where}): wings {width} strikes apart, {lots} lot{'s' if lots != 1 else ''}, {stop_text}. Edit any leg."}


# --- the confirm step and Execute basket ----------------------------------------------------------------------------

def _ask(leg: LegAsk, product: str, client_ref: Optional[str] = None) -> fno_ticket.OrderAsk:
    return fno_ticket.OrderAsk(underlying=master.underlying_of(leg.underlying), expiry=leg.expiry, strike=leg.strike, right=leg.right,
                               side=leg.side, lots=leg.lots, order_type="MARKET", product=product, stop_loss=leg.stop_loss,
                               chain_ltp=leg.price, client_ref=client_ref)


async def _room(session: AsyncSession, user: User, count: int) -> Optional[str]:
    """The basket opens `count` positions: does the user's open-position and trades-per-day limit leave room for all?"""
    from app.core.models import RiskConfig
    from app.platform.controls import clamp_config, risk_ceilings
    from app.risk_engine import by_default
    from app.risk_engine.routes import get_tenant_risk_config
    from app.trading.persistence import build_trading_day_state
    # The same limits the pipeline counts against (organisation, clamped, the user's own cap); each leg's own checks,
    # the guardian's among them, run per leg in `fno_ticket.assess`.
    cfg = await get_tenant_risk_config(user.tenant_id, session) or RiskConfig()
    cfg, _ = clamp_config(cfg, await risk_ceilings(session))
    if await by_default.enabled(session, user.tenant_id):
        cfg, _ = await by_default.apply_user_cap(session, user, cfg)
    if cfg.max_open_positions is None and cfg.max_trades_per_day is None:
        return None                      # DEF-0: neither limit set - no room to count against
    state = await build_trading_day_state(session, user)
    big = 1 << 30
    open_room = max(0, cfg.max_open_positions - state.open_positions) if cfg.max_open_positions is not None else big
    day_room = max(0, cfg.max_trades_per_day - state.trades_today) if cfg.max_trades_per_day is not None else big
    room = min(open_room, day_room)
    if room >= count:
        return None
    limit = (f"your limit of {cfg.max_open_positions} open positions (you hold {state.open_positions})" if open_room <= day_room
             else f"your limit of {cfg.max_trades_per_day} trades a day ({state.trades_today} today)")
    return (f"This basket opens {count} positions, one per leg; {limit} leaves room for {room}. Remove legs, or exit a position "
            "first. Nothing was placed.")


async def preview(session: AsyncSession, user: User, legs: Sequence[LegAsk], *, product: str, spot: Optional[float],
                  price_lookup: Optional[fno_ticket.PriceLookup], live_only: bool = False, now: Optional[datetime] = None,
                  with_smart: bool = True, origin: str = fno_ticket.ORIGIN_AUTOMATIC) -> Dict[str, Any]:
    """The confirm step: each leg as the ticket sees it (placement order), the basket's own checks, and the totals at
    the prices it would fill at. Reads only. `origin` comes from the route (TT-2: "manual" only on the basket routes)."""
    now = now or datetime.now(timezone.utc)
    rows: List[Dict[str, Any]] = []
    for seq, (i, leg) in enumerate(hedge_first(legs), start=1):
        view = await fno_ticket.assess(session, user, _ask(leg, product), price_lookup=price_lookup, live_only=live_only, now=now,
                                       origin=origin)
        rows.append({"seq": seq, "leg": i, "role": "hedge" if leg.side == "BUY" else "short", **view})
    refusals: List[str] = []
    room = await _room(session, user, len(legs))
    if room:
        refusals.append(room)
    totals: Optional[Dict[str, Any]] = None
    totals_errors: List[str] = []
    priced = all(r["fill_price"] is not None for r in rows)
    if spot and priced:
        fills = {r["leg"]: r["fill_price"] for r in rows}
        at_fill = [replace(leg, price=fills[i]) for i, leg in enumerate(legs)]
        numbers = await evaluate(session, at_fill, spot, as_of=now)
        if numbers.get("ok"):
            totals = {"margin": numbers["margin"], "extremes": numbers["extremes"], "net_premium": numbers["net_premium"],
                      "costs": numbers["costs"]["to_open"]["total"], "breakevens": numbers["breakevens"]}
        else:
            totals_errors = list(numbers.get("errors") or [])
    can = not refusals and all(r["can_place"] for r in rows)
    smart = await smart_view(session, user, legs, rows, product) if with_smart and await fno_ticket.smart_on(session, user.tenant_id) else None
    return {"mode": fno_ticket.MODE, "practice": fno_ticket.PRACTICE, "legs": rows, "refusals": refusals, "totals": totals,
            "totals_errors": totals_errors, "can_execute": can, "product": product, "smart": smart,
            "confirm_label": f"Place {len(rows)} PAPER orders, hedge legs first" if can else None, "as_of": now.isoformat()}


# --- Part 7 smart execution (flag smart_execution): the sequencing preview, the margin, the incomplete state -----------

async def own_broker(session: AsyncSession, user: User) -> Optional[Any]:
    """The user's OWN broker session (never a teammate's), for the broker's basket margin (TM-10); None without one.
    An account read, not market data: any of his own credentials with a usable token."""
    from app.brokers.token_lifecycle import build_adapter, token_is_usable
    from app.market_data import entitlement
    for record in await entitlement.own_records(session, user):
        if token_is_usable(record):
            return build_adapter(record)
    return None


async def practice_balance(session: AsyncSession, user: User) -> float:
    """The PAPER balance: the starting capital plus the net P&L of his own closed PAPER trades (as the account shows)."""
    from app.paper import service
    account = await service.get_account(session, user)
    trades = [t for t in await service._paper_trades(session, user) if service._counted(t) and t.exit_time is not None]
    return round(float(account.starting_capital) + sum(float(t.pnl or 0.0) for t in trades), 2)


async def smart_view(session: AsyncSession, user: User, legs: Sequence[LegAsk], rows: Sequence[Dict[str, Any]], product: str) -> Dict[str, Any]:
    """The confirm step's smart-execution block: the legs in the order they go (hedges first, each with its slices), the
    exit order, and the margin - the broker's with the hedge benefit through his own session where the adapter has a
    basket margin call, else ATP's labelled estimate - with the free balance before and after."""
    from app.brokers.models import BrokerOrderRequest
    from app.core.enums import OrderSide
    from app.execution import smart_execution
    sequence = []
    for r in rows:
        smart = r.get("smart") or {}
        sequence.append({"seq": r["seq"], "leg": r["leg"], "label": r["label"], "side": r["side"], "role": r["role"], "lots": r["lots"],
                         "slices": smart.get("slices") or [r["lots"]], "note": smart.get("note")})
    priced = [(r, legs[r["leg"]]) for r in rows if r.get("fill_price") is not None and r.get("contract")]
    estimate: Dict[str, Any] = {}
    broker_margin: Optional[Dict[str, Any]] = None
    broker_name: Optional[str] = None
    if len(priced) == len(rows) and rows:
        builder = [_builder_leg(leg, int(r["contract"]["lot_size"]), float(r["fill_price"])) for r, leg in priced]
        estimate = hedge_margin(builder)
        broker = await own_broker(session, user)
        if broker is not None:
            broker_name = getattr(broker, "name", None)
            orders = [BrokerOrderRequest(symbol=r["contract"]["tradingsymbol"], exchange=r["contract"]["exchange"],
                                         transaction_type=OrderSide.BUY if r["side"] == "BUY" else OrderSide.SELL,
                                         quantity=float(r["units"]), product=product) for r, _ in priced]
            broker_margin = await smart_execution.broker_basket_margin(broker, orders)
    view = smart_execution.margin_view(broker_margin, estimate, await practice_balance(session, user), free_source="practice",
                                       broker_name=broker_name)
    return {"sequence": sequence, "margin": view.as_dict(),
            "entry_rule": "Hedge legs (Buy) go first, then the sold legs, so the protection is in place before the short.",
            "exit_rule": "On exit the sold legs close first, then the hedges: a short is never left without its hedge.",
            "incomplete_rule": "If a leg does not fill, nothing more is sent: you choose Complete the rest or Close the filled legs."}


def _smart_rows(outcome: Any, legs: Sequence[LegAsk], results: Dict[int, Dict[str, Any]]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for leg_out in outcome.legs:
        leg = legs[leg_out.index]
        result = results.get(leg_out.index) or {}
        status = {"FILLED": "PLACED", "NOT_PLACED": "NOT_PLACED"}.get(leg_out.status) or (
            "FAILED" if result.get("status") == "FAILED" else "REFUSED")
        rows.append({"seq": leg_out.seq, "leg": leg_out.index, "label": result.get("label") or label(leg), "side": leg.side, "lots": leg.lots,
                     "status": status, "reasons": list(leg_out.reasons), "filled": result.get("filled"), "order_id": result.get("order_id"),
                     "trade_id": result.get("trade_id"), "mode": result.get("mode", fno_ticket.MODE)})
    return rows


async def execute_smart(session: AsyncSession, user: User, legs: Sequence[LegAsk], *, product: str, basket_ref: str,
                        price_lookup: Optional[fno_ticket.PriceLookup], now: Optional[datetime] = None,
                        origin: str = fno_ticket.ORIGIN_AUTOMATIC) -> Dict[str, Any]:
    """Execute basket through the smart-execution engine: hedges first, and the first leg that does not fill stops the
    run (TM-11: no SELL before every hedge is filled) with the INCOMPLETE state and its two choices."""
    from app.execution import smart_execution
    results: Dict[int, Dict[str, Any]] = {}
    user_id = user.id
    holder = {"user": user}

    async def place(index: int, seq: int):
        result = await fno_ticket.place(session, holder["user"], _ask(legs[index], product, f"{basket_ref}-{seq}"), price_lookup=price_lookup, now=now,
                                        origin=origin)
        holder["user"] = await session.get(User, user_id) or holder["user"]    # a rollback in the pipeline expires it
        results[index] = result
        return result.get("status") == "PLACED", list(result.get("reasons") or []), result
    outcome = await smart_execution.run([leg.side for leg in legs], place)
    rows = _smart_rows(outcome, legs, results)
    placed = sum(1 for r in rows if r["status"] == "PLACED")
    status = {smart_execution.COMPLETE: "PLACED", smart_execution.INCOMPLETE: "PARTIAL"}.get(outcome.state, "REFUSED")
    return {"mode": fno_ticket.MODE, "status": status, "state": outcome.state, "actions": outcome.actions, "message": outcome.message,
            "reasons": [], "legs": rows, "placed": placed, "next_steps": [outcome.message] if outcome.state != smart_execution.COMPLETE else [],
            "filled_trade_ids": [r["trade_id"] for r in rows if r["status"] == "PLACED" and r["trade_id"]]}


async def close_filled(session: AsyncSession, user: User, trade_ids: Sequence[int]) -> Dict[str, Any]:
    """"Close the filled legs" (TM-11, the user's tap): his own open PAPER legs closed through the ordinary Exit now path,
    the SELL (short) legs first, then the hedges; a short that does not close stops the run before its hedge is closed.
    Exits are never blocked: each leg can always be closed on its own with Exit now (ADR-0004)."""
    from app.db.models import TradeRecord
    from app.execution import approval, smart_execution
    trades = []
    for trade_id in dict.fromkeys(trade_ids):
        trade = await session.get(TradeRecord, trade_id)
        if trade is None or trade.tenant_id != user.tenant_id:
            raise HTTPException(status_code=404, detail="Unknown position")
        if not approval.may_act_on_trade(user, trade):
            raise HTTPException(status_code=403, detail="This position belongs to another member; only they or the organisation owner can close it")
        trades.append(trade)
    sides = ["BUY" if t.direction == "LONG" else "SELL" for t in trades]
    closed: Dict[int, Dict[str, Any]] = {}

    async def close(index: int, seq: int):
        trade = trades[index]
        await session.refresh(trade)
        if trade.exit_time is not None:
            closed[index] = {"trade_id": trade.id, "exit_price": trade.exit_price, "pnl": trade.pnl, "already": True}
            return True, ["Already closed"], closed[index]
        try:
            outcomes = await approval.exit_now(session, trade, user, via="web")
        except ValueError as exc:
            return False, [str(exc)], None
        done = [o for o in outcomes if o.closed]
        if not done:
            return False, [w for o in outcomes for w in o.warnings] or ["The position could not be closed"], None
        closed[index] = {"trade_id": trade.id, "exit_price": done[0].exit_price, "pnl": done[0].pnl}
        return True, [], closed[index]
    outcome = await smart_execution.run(sides, close, exiting=True)
    rows = [{"seq": leg.seq, "trade_id": trades[leg.index].id, "symbol": trades[leg.index].symbol, "side": leg.side,
             "status": "CLOSED" if leg.status == "FILLED" else leg.status, "reasons": leg.reasons,
             "exit_price": (closed.get(leg.index) or {}).get("exit_price"), "pnl": (closed.get(leg.index) or {}).get("pnl")}
            for leg in outcome.legs]
    return {"mode": fno_ticket.MODE, "state": outcome.state, "actions": outcome.actions, "message": outcome.message, "legs": rows}


def _next_steps(status: str, rows: Sequence[Dict[str, Any]]) -> List[str]:
    if status == "PLACED":
        return []
    placed = [r for r in rows if r["status"] == "PLACED"]
    steps = []
    if placed:
        steps.append(f"{len(placed)} leg{'s' if len(placed) != 1 else ''} filled and {'are' if len(placed) != 1 else 'is'} open in "
                     "practice mode. Keep them, or close them with Exit now on Positions or the chain.")
        steps.append("To place the legs that did not fill, fix what each one says and tap \"Place the remaining legs\". "
                     "Nothing is retried by itself.")
    else:
        steps.append("Nothing filled. Fix what each leg says and tap Execute basket again. Nothing is retried by itself.")
    return steps


async def _group(session: AsyncSession, user: User, basket_ref: str, rows: Sequence[Dict[str, Any]]) -> None:
    """TT-8: the filled legs of this basket are stored as one group (app/paper/fno_groups.py)."""
    from app.paper import fno_groups
    await fno_groups.tag_filled(session, user, basket_ref, [r["trade_id"] for r in rows if r.get("status") == "PLACED" and r.get("trade_id")])


async def execute(session: AsyncSession, user: User, legs: Sequence[LegAsk], *, product: str, basket_ref: str,
                  price_lookup: Optional[fno_ticket.PriceLookup], now: Optional[datetime] = None,
                  origin: str = fno_ticket.ORIGIN_AUTOMATIC, exits: Optional[Any] = None) -> Dict[str, Any]:
    """Places every leg as PAPER, hedge legs first, or nothing when the check at the live price refuses anything.
    SX: `exits` (app/execution/strategy_exits.StrategyExitAsk, the user's own "Exits for this strategy") becomes one
    combined exit over the legs that filled; without it none is set. The answer's `exits` says which."""
    result = await _execute(session, user, legs, product=product, basket_ref=basket_ref, price_lookup=price_lookup, now=now,
                            origin=origin)
    if exits is not None and result.get("status") != "REFUSED":
        from app.execution import strategy_exits
        filled = [r["trade_id"] for r in result.get("legs", []) if r.get("status") == "PLACED" and r.get("trade_id")]
        result["exits"] = await strategy_exits.arm(session, user_id_of(user), basket_ref, filled, exits, price_lookup, now)
    return result


def user_id_of(user: User) -> int:
    """The caller's id, read without a database round trip (a rollback inside the pipeline may expire the object)."""
    from sqlalchemy import inspect
    identity = inspect(user).identity
    return int(identity[0]) if identity else int(user.id)


async def _execute(session: AsyncSession, user: User, legs: Sequence[LegAsk], *, product: str, basket_ref: str,
                   price_lookup: Optional[fno_ticket.PriceLookup], now: Optional[datetime] = None,
                   origin: str = fno_ticket.ORIGIN_AUTOMATIC) -> Dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    smart = await fno_ticket.smart_on(session, user.tenant_id)
    check = await preview(session, user, legs, product=product, spot=None, price_lookup=price_lookup, live_only=True, now=now,
                          with_smart=False, origin=origin)
    await session.commit()
    if not check["can_execute"]:
        reasons = list(check["refusals"]) + [f"{r['label']}: {(r['errors'] + r['refusals'] + ['it cannot be placed now'])[0]}"
                                             for r in check["legs"] if not r["can_place"]]
        refused = [{"seq": r["seq"], "leg": r["leg"], "label": r["label"], "side": r["side"], "lots": r["lots"], "status": "NOT_PLACED",
                 "reasons": r["errors"] + r["refusals"], "filled": None, "order_id": None, "trade_id": None} for r in check["legs"]]
        return {"mode": fno_ticket.MODE, "status": "REFUSED", "reasons": reasons, "legs": refused, "placed": 0, "next_steps": [
            "Nothing was placed. Fix what it says and try again."]}
    if smart:
        result = await execute_smart(session, user, legs, product=product, basket_ref=basket_ref, price_lookup=price_lookup, now=now,
                                     origin=origin)
        await _group(session, user, basket_ref, result["legs"])
        return result
    rows: List[Dict[str, Any]] = []
    user_id = user.id
    hedge_failed = False
    for seq, (i, leg) in enumerate(hedge_first(legs), start=1):
        name = label(leg)
        if leg.side == "SELL" and hedge_failed:
            rows.append({"seq": seq, "leg": i, "label": name, "side": leg.side, "lots": leg.lots, "status": "NOT_PLACED", "filled": None,
                         "order_id": None, "trade_id": None,
                         "reasons": ["Not placed because a hedge leg did not fill: on its own this sold leg would be uncovered."]})
            continue
        result = await fno_ticket.place(session, user, _ask(leg, product, f"{basket_ref}-{seq}"), price_lookup=price_lookup, now=now,
                                        origin=origin)
        user = await session.get(User, user_id) or user        # a rollback inside the pipeline (a repeated tap) expires it
        status = result.get("status", "FAILED")
        if status != "PLACED" and leg.side == "BUY":
            hedge_failed = True
        rows.append({"seq": seq, "leg": i, "label": result.get("label") or name, "side": leg.side, "lots": leg.lots,
                     "status": status if status == "PLACED" else ("FAILED" if status == "FAILED" else "REFUSED"),
                     "reasons": list(result.get("reasons") or []), "filled": result.get("filled"), "order_id": result.get("order_id"),
                     "trade_id": result.get("trade_id"), "mode": result.get("mode", fno_ticket.MODE)})
    placed = sum(1 for r in rows if r["status"] == "PLACED")
    status = "PLACED" if placed == len(rows) else ("PARTIAL" if placed else "REFUSED")
    if status != "PLACED":
        logger.info("FNO basket %s for user %s: %s of %s legs placed", basket_ref, user.id, placed, len(rows))
    await _group(session, user, basket_ref, rows)
    return {"mode": fno_ticket.MODE, "status": status, "reasons": [], "legs": rows, "placed": placed, "next_steps": _next_steps(status, rows)}


# --- "Trade in your broker app" ---------------------------------------------------------------------------------------

async def broker_link(session: AsyncSession, user: User, legs: Sequence[LegAsk]) -> Dict[str, Any]:
    """The legs as a pre-filled basket for the user's own broker app, hedge legs first - or why not. ATP sends nothing
    to any broker: the browser opens the broker's page and the user places (or not) there. Only Kite's Publisher basket
    is documented (docs/BROKER_DATA_TERMS.md), and it needs Kite's own contract symbols from the instrument master."""
    from app.ai import proposal_card
    from app.core import config as app_config
    from app.platform.controls import flag_enabled
    if not await flag_enabled(session, proposal_card.FLAG, user.tenant_id):
        return {"available": False, "why": "Trade in your broker app is not turned on for your organisation."}
    broker = (await proposal_card._tenant_broker(session, user.tenant_id) or "").lower()
    key = (app_config.KITE_PUBLISHER_API_KEY or "").strip()
    if broker != "zerodha" or not key:
        return {"available": False, "why": "A pre-filled basket is available for Zerodha Kite only for now. Place the legs in your broker app yourself."}
    orders: List[Dict[str, Any]] = []
    for _, leg in hedge_first(legs):
        name = master.underlying_of(leg.underlying)
        where = [InstrumentRecord.broker == "zerodha", InstrumentRecord.underlying == name, InstrumentRecord.expiry == leg.expiry,
                 InstrumentRecord.instrument_type == leg.right]
        if leg.right != fno_ticket.FUTURE:
            where.append(InstrumentRecord.strike == float(leg.strike or 0.0))
        row = await session.scalar(select(InstrumentRecord).where(*where).limit(1))
        if row is None or not row.lot_size:
            return {"available": False, "why": f"Kite's symbol for {label(leg)} is not in the instrument list yet. Try again after it updates."}
        orders.append({"tradingsymbol": row.tradingsymbol, "exchange": row.exchange or master.derivatives_exchange(name),
                       "transaction_type": leg.side, "quantity": int(leg.lots) * int(row.lot_size)})
    return {"available": True, "broker": "zerodha", "publisher_api_key": key, "orders": orders}
