"""Z3 / BT2 (beta spec, Phase 0): approval-only entries, approval expiry and re-quote, and the exits policy.

With the `approval_only_entries` flag on for an organisation (or a deployment's own `entry_mode` = APPROVAL), nothing
enters on its own. Every automatic entry signal - a deployment's signal in the trading worker, a TradingView webhook
alert, a public-API signal - becomes an ENTRY proposal on the ordinary monitor state machine (`ai_actions`, ADR-0006):
PROPOSED -> APPROVED -> EXECUTED, or REJECTED / EXPIRED / FAILED, with Snooze (PW5), Telegram buttons (PW4) and push
(PW3) unchanged. Only an APPROVED entry proposal reaches the order pipeline, and only in PAPER (Phase 0: ATP places no
order through a broker API).

* **Gate (the backstop).** `execute_signal_for_user` and `execute_structure` ask `gate_refusal` on every call. Unless
  the call is a person's own manual entry (the console's paper-execute) or carries the id of an APPROVED entry
  proposal of the same organisation (and deployment), an approval-only entry is REJECTED on the order trail. The
  default `origin` is "automatic", so a new caller that forgets to say what it is is refused, not let through.
* **Expiry.** An entry proposal expires after `APPROVAL_ENTRY_TTL_MINUTES`; the worker expires due rows every cycle.
* **Re-quote.** On Approve the entry is priced again at the current LTP (`requote_check`). A move from the proposal
  price beyond `APPROVAL_REQUOTE_MAX_DRIFT_PCT` % or `APPROVAL_REQUOTE_MAX_DRIFT_TICKS` ticks (the stricter limit wins)
  places nothing: the approved row is closed as EXPIRED and a fresh proposal at the new price is raised (at most
  `APPROVAL_MAX_REQUOTES` times) for the person to decide again. A price already through the stop or target 1 expires
  the proposal without a re-quote. Inside the limit the PAPER order is placed at the re-quoted price.
* **Exits (ADR-0004).** Protective exits - stop loss, premium floor/ceiling, time stop, square-off, kill switch, any
  structure rule - stay automatic and never look at a proposal or a snooze. A target exit (Target 1 / Target 2) on an
  approval-only position asks "Exit now?" instead (an EXIT_POSITION proposal, once per level per position). "Exit now"
  (`exit_now`) is always available: the API, the Telegram `/exit` command and the proposal's Approve button.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable, Dict, Iterable, List, Optional, Tuple

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.log import write_audit_log
from app.core import config as app_config
from app.core.enums import NotificationSeverity, NotificationType
from app.core.models import Signal
from app.db.models import AiActionRecord, StrategyDeploymentRecord, TradeRecord, User
from app.market_data.calendar import IST

logger = logging.getLogger(__name__)

FLAG = "approval_only_entries"
ACTION_ENTRY = "ENTRY"
RULE_ENTRY = "ENTRY_SIGNAL"
ENTRY_MODES = ("AUTO", "APPROVAL")

ORIGIN_AUTOMATIC = "automatic"     # the default: a deployment, a webhook, the public API - needs an approved proposal
ORIGIN_MANUAL = "manual"           # a person's own entry from the console (they are the approval)
ORIGIN_APPROVAL = "approval"       # the execution of an APPROVED entry proposal (must carry its id)

SOURCE_LABELS = {"deployment": "Deployment signal", "tradingview": "TradingView alert", "public_api": "API signal",
                 "manual": "Manual setup", "requote": "Re-quoted entry"}

PriceLookup = Callable[[str], Awaitable[float]]


class EntriesPaused(ValueError):
    """Z3-b: the user the proposal is for paused their PAPER entries (/pause): no proposal is raised."""


def _utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# --- who is approval-only ----------------------------------------------------------------------------------------------

async def flag_on(session: AsyncSession, tenant_id: int) -> bool:
    from app.platform.controls import flag_enabled
    return await flag_enabled(session, FLAG, tenant_id)


async def required(session: AsyncSession, tenant_id: int, deployment: Optional[StrategyDeploymentRecord] = None) -> bool:
    """True when a new entry for this organisation (and deployment) may only come from an approved proposal."""
    if deployment is not None and (deployment.entry_mode or "AUTO") == "APPROVAL":
        return True
    return await flag_on(session, tenant_id)


async def gate_refusal(session: AsyncSession, tenant_id: int, *, deployment_id: Optional[int], origin: str,
                       approval_action_id: Optional[int]) -> Optional[str]:
    """Why this entry must be refused under approval-only mode, or None. Called by every order pipeline entry point."""
    if origin == ORIGIN_MANUAL:
        return None
    deployment = await session.get(StrategyDeploymentRecord, deployment_id) if deployment_id is not None else None
    if deployment is not None and deployment.tenant_id != tenant_id:
        deployment = None
    if not await required(session, tenant_id, deployment):
        return None
    if approval_action_id is None:
        return "Approval-only mode: an entry needs an approved proposal - this one came without one"
    action = await session.get(AiActionRecord, approval_action_id)
    if action is None or action.tenant_id != tenant_id or action.action != ACTION_ENTRY or action.status != "APPROVED":
        return "Approval-only mode: the proposal given is not an approved entry proposal of this organisation"
    if action.deployment_id != deployment_id:
        return "Approval-only mode: the approved proposal belongs to another deployment"
    return None


# --- re-quote -----------------------------------------------------------------------------------------------------------

def tick_size_for(symbol: str) -> float:
    from app.instruments.registry import get_contract_spec
    spec = get_contract_spec(symbol)
    tick = float(getattr(spec, "tick_size", 0) or 0) if spec is not None else 0.0
    return tick if tick > 0 else 0.05


def requote_check(direction: str, proposal_price: float, current_price: float, *, stop_loss: Optional[float],
                  target1: Optional[float], max_drift_pct: float, max_drift_ticks: int, tick_size: float) -> Tuple[str, str]:
    """("ok" | "requote" | "expire", why). Pure: the rule the approval applies to the price it sees at the moment of
    Approve. A price through the stop or target 1 has no trade left in it ("expire"); a move beyond the stricter of the
    percent and tick limits is re-proposed at the new price ("requote"); anything else is placed at the new price."""
    if current_price is None or current_price <= 0 or proposal_price is None or proposal_price <= 0:
        return "expire", "No valid price to re-quote the entry"
    long = (direction or "").upper() == "LONG"
    if stop_loss is not None and ((long and current_price <= stop_loss) or (not long and current_price >= stop_loss)):
        return "expire", f"Price {current_price:g} is already through the stop {stop_loss:g}"
    if target1 is not None and ((long and current_price >= target1) or (not long and current_price <= target1)):
        return "expire", f"Price {current_price:g} is already through target 1 {target1:g}"
    drift = abs(current_price - proposal_price)
    limits: List[Tuple[float, str]] = []
    if max_drift_pct > 0:
        limits.append((proposal_price * max_drift_pct / 100.0, f"{max_drift_pct:g}%"))
    if max_drift_ticks > 0 and tick_size > 0:
        limits.append((max_drift_ticks * tick_size, f"{max_drift_ticks} ticks"))
    if limits:
        limit, label = min(limits, key=lambda item: item[0])
        if drift > limit + 1e-9:
            return "requote", (f"Price moved {drift:g} ({drift / proposal_price * 100:.2f}%) from the proposal price {proposal_price:g} "
                               f"to {current_price:g} - more than the {label} limit")
    return "ok", f"Price {current_price:g} is within the limit of the proposal price {proposal_price:g}"


# --- proposals ----------------------------------------------------------------------------------------------------------

def _money(value: Optional[float]) -> str:
    return "-" if value is None else f"{value:,.2f}"


async def propose_entry(session: AsyncSession, tenant_id: int, *, signal: Signal, strategy_id: str, source: str,
                        idempotency_key: str, deployment: Optional[StrategyDeploymentRecord] = None, requested_mode: str = "PAPER",
                        exit_rules: Optional[str] = None, holding: str = "INTRADAY", user_id: Optional[int] = None,
                        now: Optional[datetime] = None, requotes: int = 0, requoted_from: Optional[int] = None) -> Optional[AiActionRecord]:
    """Raises one ENTRY proposal (PROPOSED, expiring after APPROVAL_ENTRY_TTL_MINUTES) and announces it through the
    ordinary notification service (in-app, push, Telegram with Approve / Reject / Snooze). None when the same signal (or,
    for a deployment, another open entry proposal) is already there; raises EntriesPaused when `user_id` paused their
    PAPER entries (Z3-b). Places nothing."""
    now = _utc(now or datetime.now(timezone.utc)).astimezone(timezone.utc)      # stored as UTC (SQLite drops the zone)
    if signal.entry is None or signal.entry <= 0:
        raise ValueError("An entry proposal needs the signal's entry price")
    from app.paper.service import paused_reason
    paused = await paused_reason(session, user_id)
    if paused:
        raise EntriesPaused(paused)
    direction = signal.direction.value if hasattr(signal.direction, "value") else str(signal.direction)
    expires = now + timedelta(minutes=app_config.APPROVAL_ENTRY_TTL_MINUTES)
    label = SOURCE_LABELS.get(source, source)
    reason = (f"{label}: {direction} {signal.symbol} at {_money(signal.entry)} (stop {_money(signal.stop_loss)}, target {_money(signal.target1)})"
              f" - {strategy_id}. Approve to place a PAPER order at the price then (re-quoted); it expires at "
              f"{expires.astimezone(IST).strftime('%H:%M')} IST.")
    # The card shows the first few numbers: the proposal price and its levels lead.
    evidence = {"proposal_price": float(signal.entry), "stop_loss": signal.stop_loss, "target1": signal.target1, "source": source,
                "symbol": signal.symbol, "direction": direction, "target2": signal.target2, "risk_reward": signal.risk_reward,
                "strategy_id": strategy_id, "mode": "PAPER", "requested_mode": requested_mode, "exit_rules": exit_rules,
                "holding": holding, "requotes": requotes, "requoted_from": requoted_from, "data_as_of": _utc(signal.timestamp).isoformat(),
                "signal": signal.model_dump(mode="json")}
    row = AiActionRecord(tenant_id=tenant_id, deployment_id=deployment.id if deployment is not None else None, trade_id=None,
                         action=ACTION_ENTRY, rule=RULE_ENTRY, reason=reason, evidence_json=json.dumps(evidence, default=str),
                         status="PROPOSED", expires_at=expires, idempotency_key=idempotency_key[:200])
    try:
        async with session.begin_nested():       # a duplicate is dropped alone; the caller's objects stay intact
            session.add(row)
            await session.flush()
    except IntegrityError:
        return None
    from app.observability.metrics import AI_PROPOSALS
    from app.notifications.service import notify
    AI_PROPOSALS.labels(action=ACTION_ENTRY).inc()
    await notify(session, tenant_id, NotificationType.AI_PROPOSAL, title=f"Entry proposal: {direction} {signal.symbol} (PAPER)",
                 message=f"{reason} Nothing is placed until you approve.", severity=NotificationSeverity.WARNING, user_id=user_id,
                 ai_action_id=row.id)
    from app.ai import vision_audit
    await vision_audit.enqueue_for_proposal(session, row, now=now)
    await session.commit()
    return row


async def execute_entry(session: AsyncSession, action: AiActionRecord, user: User, *, price_lookup: Optional[PriceLookup],
                        now: Optional[datetime] = None) -> AiActionRecord:
    """Runs an APPROVED entry proposal: re-quote, then one PAPER order through the ordinary pipeline (kill switches,
    platform controls, risk engine, order state machine). Never a broker order. The row ends EXECUTED (with the trade),
    FAILED (with why), or EXPIRED (the price moved; possibly re-quoted as a new proposal)."""
    from app.execution.signal_execution import execute_signal_for_user
    now = now or datetime.now(timezone.utc)
    if action.status != "APPROVED" or action.action != ACTION_ENTRY:
        raise ValueError(f"Action is {action.status} {action.action}; only APPROVED entry proposals execute")
    evidence = json.loads(action.evidence_json or "{}")
    try:
        deployment = await session.get(StrategyDeploymentRecord, action.deployment_id) if action.deployment_id is not None else None
        if deployment is not None:
            if deployment.status != "ACTIVE":
                raise ValueError(f"Deployment #{deployment.id} is {deployment.status} - the entry was not placed")
            open_trade = await session.scalar(select(TradeRecord.id).where(TradeRecord.deployment_id == deployment.id, TradeRecord.exit_time.is_(None)))
            if open_trade is not None:
                raise ValueError(f"Deployment #{deployment.id} already has an open position (#{open_trade}) - the entry was not placed")
        symbol, direction = str(evidence["symbol"]), str(evidence["direction"])
        if price_lookup is None:
            raise ValueError("No live price to re-quote the entry at - nothing was placed (a broker session supplies quotes)")
        try:
            price = float(await price_lookup(symbol))
        except Exception as exc:  # noqa: BLE001 - no quote, no order
            raise ValueError(f"No live price for {symbol} to re-quote the entry at ({type(exc).__name__}) - nothing was placed") from exc
        verdict, why = requote_check(direction, float(evidence["proposal_price"]), price, stop_loss=evidence.get("stop_loss"),
                                     target1=evidence.get("target1"), max_drift_pct=app_config.APPROVAL_REQUOTE_MAX_DRIFT_PCT,
                                     max_drift_ticks=app_config.APPROVAL_REQUOTE_MAX_DRIFT_TICKS, tick_size=tick_size_for(symbol))
        if verdict != "ok":
            return await _close_unplaced(session, action, user, evidence, deployment, verdict, why, price, now)
        signal = Signal.model_validate(evidence["signal"])
        risk = abs(price - signal.stop_loss) if signal.stop_loss is not None else 0.0
        rr = round(abs(signal.target1 - price) / risk, 2) if risk and signal.target1 is not None else signal.risk_reward
        signal = signal.model_copy(update={"entry": price, "risk_reward": rr, "timestamp": now,
                                           "reasons": list(signal.reasons) + [f"Approved proposal #{action.id}, re-quoted at {price:g}"]})
        result, order = await execute_signal_for_user(
            session, user, mode="PAPER", strategy_id=str(evidence.get("strategy_id") or signal.strategy_id), signal=signal,
            idempotency_key=f"approval:{action.id}", deployment_id=action.deployment_id, exit_rules=evidence.get("exit_rules"),
            holding=str(evidence.get("holding") or "INTRADAY"), origin=ORIGIN_APPROVAL, approval_action_id=action.id,
        )
        if not result.executed:
            raise ValueError("PAPER order not placed: " + ("; ".join(result.reasons) or order.status))
        action.trade_id = order.trade_id
        action.result = f"PAPER order #{order.id} placed at the re-quoted price {price:g}" + (f"; position #{order.trade_id}" if order.trade_id else "")
        action.status, action.executed_at = "EXECUTED", now
    except Exception as exc:  # noqa: BLE001 - recorded on the row, shown to the person
        action.status, action.result, action.executed_at = "FAILED", str(exc)[:300], now
    await session.commit()
    await session.refresh(action)
    return action


async def _close_unplaced(session: AsyncSession, action: AiActionRecord, user: User, evidence: Dict, deployment: Optional[StrategyDeploymentRecord],
                          verdict: str, why: str, price: float, now: datetime) -> AiActionRecord:
    """The price moved: the approval places nothing. The row is closed as EXPIRED (it never filled) and, on a re-quote
    within the allowance, a fresh proposal at the current price is raised for a new decision."""
    requotes = int(evidence.get("requotes") or 0)
    action.status, action.executed_at = "EXPIRED", now
    action.result = f"Not placed: {why}"[:300]
    await session.flush()                     # frees the deployment's open-proposal slot for the re-quote
    if verdict == "requote" and requotes < app_config.APPROVAL_MAX_REQUOTES:
        signal = Signal.model_validate(evidence["signal"])
        risk = abs(price - signal.stop_loss) if signal.stop_loss is not None else 0.0
        rr = round(abs(signal.target1 - price) / risk, 2) if risk and signal.target1 is not None else signal.risk_reward
        try:
            fresh = await propose_entry(session, action.tenant_id, signal=signal.model_copy(update={"entry": price, "risk_reward": rr}),
                                        strategy_id=str(evidence.get("strategy_id") or signal.strategy_id), source="requote",
                                        idempotency_key=f"requote:{action.id}", deployment=deployment,
                                        requested_mode=str(evidence.get("requested_mode") or "PAPER"), exit_rules=evidence.get("exit_rules"),
                                        holding=str(evidence.get("holding") or "INTRADAY"), user_id=user.id, now=now,
                                        requotes=requotes + 1, requoted_from=action.id)
        except EntriesPaused:
            fresh = None
            action.result = f"Not placed: {why}. No re-quote: PAPER entries are paused"[:300]
        if fresh is not None:
            action.result = f"Not placed: {why}. Re-quoted as proposal #{fresh.id}"[:300]
    elif verdict == "requote":
        action.result = f"Not placed: {why}. No re-quote left ({requotes} of {app_config.APPROVAL_MAX_REQUOTES} used)"[:300]
    await write_audit_log(session, action.tenant_id, user.id, "entry_proposal_not_placed", f"#{action.id}: {action.result}")
    await session.commit()
    await session.refresh(action)
    return action


# --- exits policy (ADR-0004) --------------------------------------------------------------------------------------------

def is_protective(reason: str) -> bool:
    """Every exit is protective - automatic, never waiting for anyone - except reaching a target. Unknown reasons are
    protective (fail-safe: they execute)."""
    return not (reason or "").startswith(("Target 1", "Target 2"))


def _target_rule(reason: str) -> str:
    return "TARGET_2_REACHED" if reason.startswith("Target 2") else "TARGET_1_REACHED"


async def exit_approval_predicate(session: AsyncSession, tenant_id: int,
                                  deployments: Iterable[StrategyDeploymentRecord]) -> Optional[Callable[[TradeRecord], bool]]:
    """Which of this organisation's positions ask "Exit now?" on a target: every position with the flag on, else the
    positions of APPROVAL deployments. None when no position does (the monitor behaves exactly as before), and None when
    the flag cannot be read: the exits sweep then runs as before rather than not at all (ADR-0004)."""
    try:
        if await flag_on(session, tenant_id):
            return lambda trade: True
    except Exception:  # noqa: BLE001 - never in the way of the exits sweep
        logger.exception("Approval-only flag unreadable for tenant %s - target exits stay automatic this cycle", tenant_id)
        return None
    ids = {d.id for d in deployments if (d.entry_mode or "AUTO") == "APPROVAL"}
    if not ids:
        return None
    return lambda trade: trade.deployment_id in ids


async def propose_exit(session: AsyncSession, trade: TradeRecord, reason: str, price: float, now: Optional[datetime] = None) -> Optional[AiActionRecord]:
    """A target was reached on an approval-only position: ask "Exit now?" once per level per position. Never raises;
    places nothing. The monitor calls it only after every exit of its sweep is done, so a failure here cannot hold one."""
    now = _utc(now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    rule = _target_rule(reason)
    try:
        seen = await session.scalar(select(AiActionRecord.id).where(AiActionRecord.tenant_id == trade.tenant_id, AiActionRecord.trade_id == trade.id,
                                                                     AiActionRecord.action == "EXIT_POSITION", AiActionRecord.rule == rule))
        if seen is not None:
            return None
        expires = now + timedelta(minutes=app_config.APPROVAL_EXIT_TTL_MINUTES)
        text = (f"Exit now? {trade.direction} {trade.symbol} (position #{trade.id}, {trade.mode}) reached {reason} - last price {price:,.2f}, "
                f"entry {trade.entry_price:,.2f}. Approve to exit at the price then. The stop and the time exits stay automatic.")
        row = AiActionRecord(tenant_id=trade.tenant_id, deployment_id=trade.deployment_id, trade_id=trade.id, action="EXIT_POSITION", rule=rule,
                             reason=text, evidence_json=json.dumps({"reason": reason, "price": price, "entry_price": trade.entry_price,
                                                                    "stop_loss": trade.stop_loss, "target1": trade.target1, "target2": trade.target2,
                                                                    "mode": trade.mode}, default=str),
                             status="PROPOSED", expires_at=expires)
        async with session.begin_nested():       # a concurrent duplicate drops this row alone
            session.add(row)
            await session.flush()
    except IntegrityError:
        return None
    except Exception:  # noqa: BLE001 - a failed question never stops anything (the monitor asks after its exits)
        logger.exception("Exit-now proposal for trade %s failed", trade.id)
        return None
    try:
        from app.notifications.service import notify
        await notify(session, trade.tenant_id, NotificationType.AI_PROPOSAL, title=f"Exit now? {trade.symbol} reached {reason}",
                     message=text, severity=NotificationSeverity.WARNING, related_trade_id=trade.id, ai_action_id=row.id)   # commits
    except Exception:  # noqa: BLE001
        logger.exception("Exit-now proposal for trade %s not announced", trade.id)
        await session.rollback()
        return None
    return row


async def retire_proposals_for_closed(session: AsyncSession, trade: TradeRecord, reason: str) -> int:
    """A position closed (by its stop, a time exit, an "Exit now"...): its open "Exit now?" proposals are void."""
    try:
        async with session.begin_nested():       # a failure rolls back the savepoint only; the close is already committed
            done = await session.execute(update(AiActionRecord).where(AiActionRecord.tenant_id == trade.tenant_id, AiActionRecord.trade_id == trade.id,
                                                                      AiActionRecord.action == "EXIT_POSITION", AiActionRecord.status == "PROPOSED")
                                         .values(status="EXPIRED", result=f"Position closed: {reason}"[:300]).execution_options(synchronize_session=False))
        await session.commit()
        return int(getattr(done, "rowcount", 0) or 0)
    except Exception:  # noqa: BLE001 - bookkeeping only
        logger.warning("Could not retire proposals of closed trade %s", trade.id)
        return 0


async def execution_sources(session: AsyncSession, action: AiActionRecord) -> Tuple[Optional[PriceLookup], Optional[object]]:
    """(price lookup, broker adapter) an approved ENTRY / EXIT_POSITION needs, for the web and Telegram alike."""
    if action.action not in (ACTION_ENTRY, "EXIT_POSITION"):
        return None, None
    from app.ai.worker_bridge import price_lookup_for_tenant
    lookup = await price_lookup_for_tenant(session, action.tenant_id)
    broker = None
    if action.action == "EXIT_POSITION" and action.trade_id is not None:
        from app.trading.position_monitor import broker_for_trade
        trade = await session.get(TradeRecord, action.trade_id)
        broker = await broker_for_trade(session, trade) if trade is not None else None
    return lookup, broker


async def exit_now(session: AsyncSession, trade: TradeRecord, user: User, *, price: Optional[float] = None, via: str = "web"):
    """"Exit now": closes the position at once, whatever is pending or snoozed (ADR-0004). The live price when a broker
    session can quote it; else, for PAPER, the price the person gives, else the last price the monitor saw. A leg of a
    structure closes the whole structure. Returns the close outcomes; raises ValueError when there is no price at all."""
    from app.ai.worker_bridge import price_lookup_for_tenant
    from app.trading.position_monitor import broker_for_trade, close_group, close_position
    lookup = await price_lookup_for_tenant(session, trade.tenant_id)

    async def price_of(leg: TradeRecord) -> Tuple[float, str]:
        if lookup is not None:
            try:
                return float(await lookup(leg.symbol)), "live price"
            except Exception as exc:  # noqa: BLE001 - fall back below
                logger.warning("Exit now: no live price for %s: %s", leg.symbol, type(exc).__name__)
        if leg.mode == "PAPER" and price is not None and len(legs) == 1:
            return float(price), "your price"
        if leg.mode == "PAPER" and leg.mark_price:
            return float(leg.mark_price), "last seen price"
        if leg.mode == "LIVE" and leg.mark_price:
            return float(leg.mark_price), "last seen price (reference; the broker fills at market)"
        raise ValueError(f"No price for {leg.symbol} - give the exit price")

    legs = [trade]
    if trade.leg_group_id:
        legs = list(await session.scalars(select(TradeRecord).where(TradeRecord.tenant_id == trade.tenant_id, TradeRecord.leg_group_id == trade.leg_group_id,
                                                                    TradeRecord.exit_time.is_(None)).order_by(TradeRecord.id)))
    prices: Dict[int, float] = {}
    sources: List[str] = []
    for leg in legs:
        prices[leg.id], source = await price_of(leg)
        sources.append(source)
    reason = f"Exit now ({via})"
    await write_audit_log(session, trade.tenant_id, user.id, "exit_now", f"trade #{trade.id} ({len(legs)} leg(s)) via {via} at {', '.join(sorted(set(sources)))}")
    await session.commit()
    if len(legs) > 1:
        return await close_group(session, legs, prices, reason, broker=None, user_id=user.id)
    broker = await broker_for_trade(session, trade)
    return [await close_position(session, trade, prices[trade.id], reason, broker=broker, user_id=user.id)]
