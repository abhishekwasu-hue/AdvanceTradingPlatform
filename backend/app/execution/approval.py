"""Z3 / BT2 (beta spec, Phase 0): approval-only entries, approval expiry and re-quote, and the exits policy.

With the `approval_only_entries` flag on for an organisation (or a deployment's own `entry_mode` = APPROVAL), nothing
enters on its own. Every automatic entry signal - a deployment's signal in the trading worker, a TradingView webhook
alert, a public-API signal - becomes an ENTRY proposal on the ordinary monitor state machine (`ai_actions`, ADR-0006):
PROPOSED -> APPROVED -> EXECUTED, or REJECTED / EXPIRED / FAILED, with Snooze (PW5), Telegram buttons (PW4) and push
(PW3) unchanged. Only an APPROVED entry proposal reaches the order pipeline, and only in PAPER (Phase 0: ATP places no
order through a broker API).

* **Gate (the backstop).** `execute_signal_for_user` and `execute_structure` ask `gate_refusal` on every call. Unless
  the call carries the id of an APPROVED entry proposal of the same organisation (and deployment), an approval-only
  entry is REJECTED on the order trail. There is no bypass: the console's paper-execute raises a proposal too (review
  M1). The default `origin` is "automatic", so a new caller that forgets to say what it is is refused.
  TT-2 (owner, 24 Oct): the one exception is the user's own manual order - origin "manual", set by the server only on
  the F&O ticket and basket routes (and later the Terminal) for an order he confirmed himself; that confirmation is his
  approval. Risk, kill switches, the Phase 0 PAPER lock and idempotency still apply to it in full.
* **Who decides (review L8).** A proposal has an owner (`owner_user_id`): the deployment's creator, the alert's owner,
  the person who sent a manual setup, the holder of the position. Only the owner or the organisation OWNER decides it,
  and an approved entry is placed for the owner (their PAPER account).
* **Expiry.** An entry proposal expires after `APPROVAL_ENTRY_TTL_MINUTES`; the worker expires due rows every cycle.
* **Re-quote.** On Approve the entry is priced again at the current LTP (`requote_check`). A move from the proposal
  price beyond `APPROVAL_REQUOTE_MAX_DRIFT_PCT` % or `APPROVAL_REQUOTE_MAX_DRIFT_TICKS` ticks (the stricter limit wins)
  places nothing: the approved row is closed as EXPIRED and a fresh proposal at the new price is raised (at most
  `APPROVAL_MAX_REQUOTES` times) for the person to decide again. A price already through the stop or target 1 expires
  the proposal without a re-quote. Inside the limit the PAPER order is placed at the re-quoted price.
* **Exits (ADR-0004).** Protective exits - stop loss, premium floor/ceiling, time stop, square-off, kill switch, any
  structure rule - stay automatic and never look at a proposal or a snooze. A target exit (Target 1 / Target 2) on an
  approval-only position asks "Exit now?" instead (an EXIT_POSITION proposal, once per level per position). "Exit now"
  (`exit_now`) is available with the flag on: the API, the Telegram `/exit` command and the proposal's Approve button.
  It books PAPER only (LIVE positions are closed in the broker app - Phase 0 has no broker order path), at the live
  price of the holder's own entitlement or the last price the monitor saw - never a price someone types (review M2).
"""
from __future__ import annotations

import json
import logging
import math
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Dict, Iterable, List, Optional, Tuple

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.log import write_audit_log
from app.core import config as app_config
from app.core.enums import NotificationSeverity, NotificationType
from app.core.json_finite import dumps as finite_dumps
from app.core.models import Signal
from app.db.models import AiActionRecord, StrategyDeploymentRecord, TradeRecord, User
from app.market_data.calendar import IST

logger = logging.getLogger(__name__)

FLAG = "approval_only_entries"
ACTION_ENTRY = "ENTRY"
RULE_ENTRY = "ENTRY_SIGNAL"
ENTRY_MODES = ("AUTO", "APPROVAL")

ORIGIN_AUTOMATIC = "automatic"     # the default: a deployment, a webhook, the public API - needs an approved proposal
ORIGIN_APPROVAL = "approval"       # the execution of an APPROVED entry proposal (must carry its id)
# TT-2 (owner, 24 Oct; answers FNO-1-4): the user's own order, placed and confirmed by his own tap on the F&O ticket, the
# basket or (later) the Terminal. His confirmation IS the approval. Only those routes set it - never a request field - and
# it never carries a deployment: a bot entry with this origin is still refused.
ORIGIN_MANUAL = "manual"
TARGET_RULES = ("TARGET_1_REACHED", "TARGET_2_REACHED")

SOURCE_LABELS = {"deployment": "Deployment signal", "tradingview": "TradingView alert", "public_api": "API signal",
                 "manual": "Manual setup", "ticket": "PAPER ticket", "requote": "Re-quoted entry", "console": "Console signal",
                 "scan": "Scan setup"}

PriceLookup = Callable[..., Awaitable[float]]       # (symbol, exchange="NSE") -> last price


class EntriesPaused(ValueError):
    """Z3-b: the user the proposal is for paused their PAPER entries (/pause), or (Z5 / P1-d, flag risk_by_default) is
    locked out for the day by their daily loss limit or entries-per-day limit: no proposal is raised."""


class InvalidEntryPrice(EntriesPaused):
    """UI2-3 review: the signal's entry price is missing, zero, negative or not a finite number (a NaN from an alert):
    no proposal is raised. A refusal like EntriesPaused - every caller answers it with the reason (no 500, and the
    worker does not count it as a failure)."""


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


def can_trade(user: User) -> bool:
    """The `require_trader` rule for paths that hold a user object (Telegram, the console): a trading role and the
    `trading:write` scope."""
    from app.auth.dependencies import TRADING_ROLES
    from app.auth.scopes import has_scope
    from app.core.enums import UserRole
    if user.role != UserRole.SUPER_ADMIN.value and user.role not in {r.value for r in TRADING_ROLES}:
        return False
    return has_scope(user, "trading:write")


def _is_owner_role(user: User) -> bool:
    from app.core.enums import UserRole
    return user.role in (UserRole.OWNER.value, UserRole.SUPER_ADMIN.value)


def may_decide(user: User, action: AiActionRecord) -> bool:
    """Review L8: a proposal with an owner is decided by that person or the organisation OWNER only."""
    return action.owner_user_id is None or action.owner_user_id == user.id or _is_owner_role(user)


def may_act_on_trade(user: User, trade: TradeRecord) -> bool:
    """Review L8: "Exit now" on a position - its holder or the organisation OWNER."""
    return trade.user_id == user.id or _is_owner_role(user)


async def exit_now_enabled(session: AsyncSession, tenant_id: int) -> bool:
    """Review M4: "Exit now" (API, Telegram /exit, the Positions button) exists with approval-only or the PAPER account on."""
    if await flag_on(session, tenant_id):
        return True
    from app.paper.service import enabled as paper_enabled
    return await paper_enabled(session, tenant_id)


async def gate_refusal(session: AsyncSession, tenant_id: int, *, deployment_id: Optional[int], origin: str,
                       approval_action_id: Optional[int]) -> Optional[str]:
    """Why this entry must be refused under approval-only mode, or None. Called by every order pipeline entry point.
    Only an APPROVED entry proposal of this organisation (and deployment) lets an automatic entry through. TT-2: a manual
    order (origin "manual", set only by the F&O ticket / basket routes for the user's own confirmed tap) is his approval;
    it never belongs to a deployment, so "manual" with a deployment id is treated as automatic and refused."""
    deployment = await session.get(StrategyDeploymentRecord, deployment_id) if deployment_id is not None else None
    if deployment is not None and deployment.tenant_id != tenant_id:
        deployment = None
    if not await required(session, tenant_id, deployment):
        return None
    if origin == ORIGIN_MANUAL and deployment_id is None and approval_action_id is None:
        return None
    if approval_action_id is None:
        return "Approval-only mode: an entry needs an approved proposal - this one came without one"
    # Review V5: read the row from the database, never the session's cache - the orphan sweep may have settled it.
    action = (await session.execute(select(AiActionRecord).where(AiActionRecord.id == approval_action_id)
                                    .execution_options(populate_existing=True))).scalar_one_or_none()
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


# The reason requote_check gives when the live (or proposal) price is not a usable number: callers word it as a quote
# problem, not as "the price moved".
NO_VALID_PRICE = "No valid price to re-quote the entry"


def requote_check(direction: str, proposal_price: float, current_price: float, *, stop_loss: Optional[float],
                  target1: Optional[float], max_drift_pct: float, max_drift_ticks: int, tick_size: float) -> Tuple[str, str]:
    """("ok" | "requote" | "expire", why). Pure: the rule the approval applies to the price it sees at the moment of
    Approve. A price through the stop or target 1 has no trade left in it ("expire"); a move beyond the stricter of the
    percent and tick limits is re-proposed at the new price ("requote"); anything else is placed at the new price."""
    # A NaN compares false with everything, so it would pass every check below as "ok": not finite is no price.
    if (current_price is None or proposal_price is None or not math.isfinite(current_price) or not math.isfinite(proposal_price)
            or current_price <= 0 or proposal_price <= 0):
        return "expire", NO_VALID_PRICE
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
    return "-" if value is None or not math.isfinite(value) else f"{value:,.2f}"


async def propose_entry(session: AsyncSession, tenant_id: int, *, signal: Signal, strategy_id: str, source: str,
                        idempotency_key: str, deployment: Optional[StrategyDeploymentRecord] = None, requested_mode: str = "PAPER",
                        exit_rules: Optional[str] = None, holding: str = "INTRADAY", user_id: Optional[int] = None,
                        now: Optional[datetime] = None, requotes: int = 0, requoted_from: Optional[int] = None,
                        exchange: str = "NSE", strike_breach: dict | None = None, ticket: dict | None = None,
                        structure: dict | None = None) -> AiActionRecord | None:
    """Raises one ENTRY proposal (PROPOSED, expiring after APPROVAL_ENTRY_TTL_MINUTES) and announces it through the
    ordinary notification service (in-app, push, Telegram with Approve / Reject / Snooze). None when the same signal (or,
    for a deployment, another open entry proposal) is already there. `user_id` is the proposal's owner; raises
    InvalidEntryPrice (an EntriesPaused) for a missing or non-finite entry price, and EntriesPaused when that user
    paused their PAPER entries (Z3-b) or is locked out by risk by default (Z5 / P1-d).
    `strike_breach` (Z5): the short strikes' breach chances of an option proposal (app/options_builder/strike_breach.py
    ::report), shown on the card. `ticket` (Z5 / P1-d): a PAPER ticket's checked limits ({plan_id, max_quantity,
    risk_cap_amount}) - the approved order keeps them. `structure` (M55 T2): an option / future deployment's resolved
    legs (`structure_payload`) - the card words them, and Approve places exactly those legs. Places nothing."""
    now = _utc(now or datetime.now(timezone.utc)).astimezone(timezone.utc)      # stored as UTC (SQLite drops the zone)
    if signal.entry is None or not math.isfinite(signal.entry) or signal.entry <= 0:
        raise InvalidEntryPrice("The signal has no usable entry price (missing, zero or not a number) - nothing was proposed")
    from app.paper.service import paused_reason
    paused = await paused_reason(session, user_id)
    if paused:
        raise EntriesPaused(paused)
    from app.risk_engine.by_default import proposal_refusal
    locked = await proposal_refusal(session, tenant_id, user_id, now)      # Z5 / P1-d: daily loss lockout, entries per day
    if locked:
        raise EntriesPaused(locked)
    owner_row: Optional[User] = None
    book = None
    if user_id is not None:                                                  # N1 F2: the owner's portfolio caps (entries only)
        from app.platform.controls import flag_enabled
        from app.portfolio import caps, risk_data
        owner_row = await session.get(User, user_id)
        if owner_row is not None and await flag_enabled(session, risk_data.FLAG, tenant_id):
            book = await caps.read_book(session, owner_row, now)              # one read for the caps and the impact line
        refused = await caps.entry_refusals(session, owner_row, caps.candidate_of(
            signal.symbol, signal.entry, signal.stop_loss, quantity=(ticket or {}).get("max_quantity")), now, book=book)
        if refused:
            raise EntriesPaused("; ".join(refused))
    if deployment is not None:
        await _free_deployment_slot(session, tenant_id, deployment.id, now)
    direction = signal.direction.value if hasattr(signal.direction, "value") else str(signal.direction)
    expires = now + timedelta(minutes=app_config.APPROVAL_ENTRY_TTL_MINUTES)
    label = SOURCE_LABELS.get(source, source)
    lines = leg_lines(structure)
    if lines:
        # M55 T2: his own order, so its legs in order words (card and Telegram); the levels stay the underlying's.
        reason = (f"{label}: {structure_title(structure)} on {signal.symbol} - {'; '.join(lines)}. Underlying at {_money(signal.entry)} "
                  f"(stop {_money(signal.stop_loss)}, target {_money(signal.target1)}) - {strategy_id}. Approve to place these legs "
                  f"as a PAPER order at the prices then (re-quoted); it expires at {expires.astimezone(IST).strftime('%H:%M')} IST.")
    else:
        reason = (f"{label}: {direction} {signal.symbol} at {_money(signal.entry)} (stop {_money(signal.stop_loss)}, target {_money(signal.target1)})"
                  f" - {strategy_id}. Approve to place a PAPER order at the price then (re-quoted); it expires at "
                  f"{expires.astimezone(IST).strftime('%H:%M')} IST.")
    # The card shows the first few numbers: the proposal price and its levels lead.
    evidence = {"proposal_price": float(signal.entry), "stop_loss": signal.stop_loss, "target1": signal.target1, "source": source,
                "symbol": signal.symbol, "direction": direction, "target2": signal.target2, "risk_reward": signal.risk_reward,
                "strategy_id": strategy_id, "mode": "PAPER", "requested_mode": requested_mode, "exit_rules": exit_rules,
                "holding": holding, "requotes": requotes, "requoted_from": requoted_from, "data_as_of": _utc(signal.timestamp).isoformat(),
                "exchange": (exchange or "NSE").upper(),
                "signal": signal.model_dump(mode="json"), "strike_breach": strike_breach, "ticket": ticket}
    if structure is not None:
        evidence["structure"] = structure
        evidence["leg_lines"] = lines
    row = AiActionRecord(tenant_id=tenant_id, deployment_id=deployment.id if deployment is not None else None, trade_id=None,
                         action=ACTION_ENTRY, rule=RULE_ENTRY, reason=reason, evidence_json=finite_dumps(evidence),
                         status="PROPOSED", expires_at=expires, idempotency_key=idempotency_key[:200], owner_user_id=user_id)
    try:
        async with session.begin_nested():       # a duplicate is dropped alone; the caller's objects stay intact
            session.add(row)
            await session.flush()
    except IntegrityError:
        return None
    from app.notifications.service import notify
    from app.observability.metrics import AI_PROPOSALS
    AI_PROPOSALS.labels(action=ACTION_ENTRY).inc()
    impact_line = ""
    if book is not None:                                                      # N1 F3: the owner's own book after this entry
        from app.portfolio import risk_text
        impact = await risk_text.proposal_impact(session, owner_row, signal.symbol, signal.entry, signal.stop_loss,
                                                 quantity=(ticket or {}).get("max_quantity"), now=now, book=book)
        impact_line = f" {impact['line']}" if impact else ""
    title = (f"Entry proposal: {structure_title(structure)} on {signal.symbol} (PAPER)" if lines
             else f"Entry proposal: {direction} {signal.symbol} (PAPER)")
    await notify(session, tenant_id, NotificationType.AI_PROPOSAL, title=title,
                 message=f"{reason} Nothing is placed until you approve.{impact_line}", severity=NotificationSeverity.WARNING, user_id=user_id,
                 ai_action_id=row.id)
    from app.ai import vision_audit
    await vision_audit.enqueue_for_proposal(session, row, now=now)
    await session.commit()
    return row


# --- M55 T2: option / future / structure proposals -----------------------------------------------------------------------

STRUCTURE_KIND = "STRUCTURE"      # a multi-leg option structure (execute_structure)
CONTRACT_KIND = "CONTRACT"        # one derived contract: a single option or a future (execute_signal_for_user)


def structure_payload(*, structure: Any = None, contract: Any = None, max_lots: int | None = None) -> dict:
    """What an option / future deployment's proposal stores: the legs exactly as the automatic entry would place them
    (the same resolver's ResolvedStructure or ResolvedContract), so Approve places these legs and no others. Each leg:
    instrument, strike, right, expiry, side and lots per lot of the trade (`ratio`). The number of lots is sized when
    the order is placed, from the user's own risk settings and at most `max_lots` (his own setting) - as the automatic
    entry does."""
    if structure is not None:
        return {"kind": STRUCTURE_KIND, "strategy": structure.strategy.value, "max_lots": max_lots, "data": structure.as_dict()}
    data = contract.as_dict()
    return {"kind": CONTRACT_KIND, "strategy": "SINGLE", "max_lots": max_lots, "data": data,
            "legs": [{"role": "LONG" if data["entry_side"] == "BUY" else "SHORT", "side": data["entry_side"], "ratio": 1, **data}]}


def _legs_of(structure: dict | None) -> list[dict]:
    if not isinstance(structure, dict):
        return []
    legs = structure.get("legs") if structure.get("kind") == CONTRACT_KIND else (structure.get("data") or {}).get("legs")
    return [leg for leg in (legs or []) if isinstance(leg, dict)]


def structure_title(structure: dict | None) -> str:
    """"Bull put spread", "Single option", "Future" - the trade's name in words."""
    if not isinstance(structure, dict):
        return "Entry"
    if structure.get("kind") == CONTRACT_KIND:
        return "Future" if (structure.get("data") or {}).get("kind") == "FUTURE" else "Single option"
    return str(structure.get("strategy") or "Structure").replace("_", " ").capitalize()


def _expiry_words(raw: Any) -> str:
    try:
        return datetime.strptime(str(raw)[:10], "%Y-%m-%d").strftime("%d %b %Y").lstrip("0")
    except (TypeError, ValueError):
        return str(raw or "-")


def leg_lines(structure: dict | None) -> list[str]:
    """M55 T2: the legs in plain words for the user's own approval card and Telegram ("Sell 1 lot NIFTY 24500 PE,
    expiry 30 Sep 2026"), the bought (protective) legs first as the order path places them, then one sizing sentence.
    Order words only here: it is his own order. [] for an underlying proposal."""
    legs = _legs_of(structure)
    if not legs:
        return []
    out: list[str] = []
    for leg in sorted(legs, key=lambda leg: 0 if leg.get("side") == "BUY" else 1):
        ratio = int(leg.get("ratio") or 1)
        side = "Buy" if leg.get("side") == "BUY" else "Sell"
        name = str(leg.get("underlying") or leg.get("tradingsymbol") or "")
        what = f"{name} future" if leg.get("kind") == "FUTURE" or leg.get("strike") is None else \
            f"{name} {float(leg['strike']):g} {leg.get('right') or ''}".rstrip()
        out.append(f"{side} {ratio} lot{'s' if ratio != 1 else ''} {what}, expiry {_expiry_words(leg.get('expiry'))}")
    cap = structure.get("max_lots") if isinstance(structure, dict) else None
    out.append(("Per lot of the trade. The number of lots" if len(legs) > 1 else "The number of lots")
               + " is sized from your own risk settings when you approve" + (f", at most {int(cap)}" if cap else ""))
    return out


def _contract_from(data: dict, today: Any) -> Any:
    from app.core.enums import InstrumentKind, OptionPosition, OrderSide, SignalDirection
    from app.instruments.contracts import ResolvedContract
    return ResolvedContract(
        kind=InstrumentKind(data["kind"]), underlying=str(data["underlying"]), underlying_symbol=str(data["underlying_symbol"]),
        tradingsymbol=str(data["tradingsymbol"]), exchange=str(data["exchange"]), instrument_key=str(data.get("instrument_key") or ""),
        lot_size=int(data["lot_size"]), tick_size=float(data.get("tick_size") or 0.05),
        expiry=datetime.strptime(str(data["expiry"])[:10], "%Y-%m-%d").date(),
        strike=float(data["strike"]) if data.get("strike") is not None else None, right=data.get("right"),
        entry_side=OrderSide(data["entry_side"]), trade_direction=SignalDirection(data["trade_direction"]),
        position=OptionPosition(data["position"]) if data.get("position") else None,
        selection_notes=tuple(data.get("selection_notes") or ()), selection=data.get("selection"),
        resolved_for=today)                    # the expiry pre-check judges the contract against the day it is placed


def _structure_from(data: dict, today: Any) -> Any:
    from app.core.enums import OptionStrategy
    from app.instruments.spreads import ResolvedLeg, ResolvedStructure
    legs = [ResolvedLeg(role=str(leg["role"]), contract=_contract_from(leg, today), ratio=int(leg.get("ratio") or 1)) for leg in data["legs"]]
    return ResolvedStructure(strategy=OptionStrategy(data["strategy"]), legs=legs, underlying_symbol=str(data["underlying_symbol"]),
                             lot_size=int(data["lot_size"]), expiry=datetime.strptime(str(data["expiry"])[:10], "%Y-%m-%d").date(),
                             width_points=float(data.get("width_points") or 0.0), notes=list(data.get("notes") or []))


class _LookupQuotes:
    """The approver's own price lookup as the quote source the order path asks for a contract's premium
    (`contract_ltp` calls `get_ltp_for_symbol`). Quotes only: it has no order method."""
    name = "approval"

    def __init__(self, lookup: PriceLookup):
        self._lookup = lookup

    async def get_ltp_for_symbol(self, symbol: str, exchange: str = "NSE") -> float:
        return float(await self._lookup(symbol, exchange))


async def _execute_legs(session: AsyncSession, action: AiActionRecord, owner: User, evidence: dict,
                        deployment: StrategyDeploymentRecord | None, signal: Signal, price: float,
                        price_lookup: PriceLookup, now: datetime) -> dict:
    """M55 T2: an APPROVED option / future / structure proposal placed through the SAME order path as the automatic
    entry - `execute_structure` (wings before shorts, its sizing and risk checks) or `execute_signal_for_user` with the
    contract - with the legs the person approved, PAPER only. Raises ValueError (nothing placed) when it cannot."""
    from app.execution.multileg import execute_structure
    from app.execution.signal_execution import execute_signal_for_user
    from app.instruments.contracts import ContractRules
    payload = evidence["structure"]
    if deployment is None:
        raise ValueError("The trading bot of this proposal is gone - nothing was placed")
    rules = ContractRules.from_deployment(deployment)
    if (deployment.option_strategy or "SINGLE") != payload.get("strategy") or not rules.derived:
        raise ValueError("The trading bot's instrument settings changed since this proposal - nothing was placed")
    today = _utc(now).astimezone(IST).date()
    quotes = _LookupQuotes(price_lookup)
    strategy_id = str(evidence.get("strategy_id") or signal.strategy_id)
    if payload.get("kind") == STRUCTURE_KIND:
        result = await execute_structure(
            session, owner, mode="PAPER", strategy_id=strategy_id, signal=signal, structure=_structure_from(payload["data"], today),
            rules=rules, target_credit_pct=deployment.target_credit_pct, stop_credit_pct=deployment.stop_credit_pct,
            underlying_exit=bool(getattr(deployment, "underlying_breach_exit", None)), idempotency_key=f"approval:{action.id}",
            broker=None, quote_broker=quotes, deployment_id=deployment.id, origin=ORIGIN_APPROVAL, approval_action_id=action.id)
        if not result.executed:
            raise ValueError("PAPER order not placed: " + ("; ".join(result.reasons) or "the structure was refused"))
        trade_id = result.trades[0].id if result.trades else None
        return {"status": "EXECUTED", "executed_at": now, "trade_id": trade_id,
                "result": f"PAPER {structure_title(payload).lower()} placed ({len(result.orders)} legs), underlying re-quoted at {price:g}"
                          + (f"; position #{trade_id}" if trade_id else "")}
    contract = _contract_from(payload["data"], today)
    result, order = await execute_signal_for_user(
        session, owner, mode="PAPER", strategy_id=strategy_id, signal=signal, idempotency_key=f"approval:{action.id}",
        deployment_id=deployment.id, contract=contract, rules=rules, quote_broker=quotes, exit_rules=evidence.get("exit_rules"),
        holding=str(evidence.get("holding") or "INTRADAY"), origin=ORIGIN_APPROVAL, approval_action_id=action.id,
        journal_source="PROPOSAL", exchange=deployment.exchange)
    if not result.executed:
        raise ValueError("PAPER order not placed: " + ("; ".join(result.reasons) or order.status))
    return {"status": "EXECUTED", "executed_at": now, "trade_id": order.trade_id,
            "result": f"PAPER order #{order.id} placed on {contract.tradingsymbol}, underlying re-quoted at {price:g}"
                      + (f"; position #{order.trade_id}" if order.trade_id else "")}


async def _free_deployment_slot(session: AsyncSession, tenant_id: int, deployment_id: int, now: datetime) -> None:
    """Review M6: a deployment holds one open entry proposal; one that is overdue (PROPOSED past its expiry, or APPROVED
    and interrupted for ORPHAN_MINUTES) must not swallow the next signal. Settled here first, conditionally."""
    from app.ai.monitor import ORPHAN_MINUTES, _orphan_outcome
    rows = await session.scalars(select(AiActionRecord).where(AiActionRecord.tenant_id == tenant_id, AiActionRecord.deployment_id == deployment_id,
                                                              AiActionRecord.rule == RULE_ENTRY, AiActionRecord.status.in_(("PROPOSED", "APPROVED"))))
    for row in list(rows):
        if row.status == "PROPOSED" and _utc(row.expires_at) <= now:
            values = {"status": "EXPIRED", "result": "No decision within the time window"}
        elif row.status == "APPROVED" and row.decided_at is not None and (now - _utc(row.decided_at)).total_seconds() >= ORPHAN_MINUTES * 60:
            values = await _orphan_outcome(session, row, now)
        else:
            continue
        await session.execute(update(AiActionRecord).where(AiActionRecord.id == row.id, AiActionRecord.status == row.status)
                              .values(**values).execution_options(synchronize_session=False))
    await session.flush()


def ticket_time_stop_passed(evidence: Dict, now: datetime) -> Optional[str]:
    """Z5 / P1-d: a PAPER ticket's proposal approved after its plan's time stop (or within the minimum lead before it)
    would open a position the time exit closes at once. Why it must not be placed, or None."""
    if not isinstance(evidence, dict) or not evidence.get("ticket") or not evidence.get("exit_rules"):
        return None
    try:
        time_stop = json.loads(evidence["exit_rules"]).get("time_exit_at")
    except (TypeError, ValueError, AttributeError):
        return None
    if not time_stop:
        return None
    earliest = (_utc(now).astimezone(IST) + timedelta(minutes=app_config.TICKET_TIME_STOP_MIN_LEAD_MINUTES)).strftime("%H:%M")
    if str(time_stop) <= earliest:
        return (f"The plan's time stop {time_stop} IST is no longer later than {earliest} IST - the position would close at once; "
                "nothing was placed (make a new plan)")
    return None


async def execute_entry(session: AsyncSession, action: AiActionRecord, user: User, *, price_lookup: Optional[PriceLookup],
                        now: Optional[datetime] = None) -> AiActionRecord:
    """Runs an APPROVED entry proposal: re-quote, then one PAPER order through the ordinary pipeline (kill switches,
    platform controls, risk engine, order state machine). Never a broker order. The row ends EXECUTED (with the trade),
    FAILED (with why), or EXPIRED (the price moved; possibly re-quoted as a new proposal). The order is placed for the
    proposal's owner (their PAPER account) whoever approved it (the owner or the organisation OWNER, review L8); the
    re-quote reads the approver's own live price (review M5)."""
    from app.ai.worker_bridge import NoLivePrice
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
        exchange = str(evidence.get("exchange") or (deployment.exchange if deployment is not None else None) or "NSE")
        stale = ticket_time_stop_passed(evidence, now)
        if stale:
            raise ValueError(stale)
        if price_lookup is None:
            raise ValueError("No live price to re-quote the entry at - nothing was placed (a broker session supplies quotes)")
        try:
            price = float(await price_lookup(symbol, exchange))
        except NoLivePrice as exc:
            raise ValueError(f"No live price for {symbol} on your own broker session - nothing was placed ({exc})") from exc
        except Exception as exc:  # noqa: BLE001 - no quote, no order
            raise ValueError(f"No live price for {symbol} to re-quote the entry at ({type(exc).__name__}) - nothing was placed") from exc
        owner: User = user
        if action.owner_user_id is not None and action.owner_user_id != user.id:
            holder = await session.get(User, action.owner_user_id)
            if holder is None or holder.tenant_id != action.tenant_id or not holder.is_active:
                raise ValueError("The person this proposal is for is no longer an active member - nothing was placed")
            owner = holder
        verdict, why = requote_check(direction, float(evidence["proposal_price"]), price, stop_loss=evidence.get("stop_loss"),
                                     target1=evidence.get("target1"), max_drift_pct=app_config.APPROVAL_REQUOTE_MAX_DRIFT_PCT,
                                     max_drift_ticks=app_config.APPROVAL_REQUOTE_MAX_DRIFT_TICKS, tick_size=tick_size_for(symbol))
        if verdict != "ok":
            return await _close_unplaced(session, action, user, evidence, deployment, verdict, why, price, now)
        signal = Signal.model_validate(evidence["signal"])
        # N1 F2: the owner's portfolio caps again at the re-quoted price (the book may have changed since the proposal).
        from app.portfolio import caps
        refused = await caps.entry_refusals(session, owner, caps.candidate_of(
            symbol, price, signal.stop_loss, quantity=(evidence.get("ticket") or {}).get("max_quantity")), now)
        if refused:
            raise ValueError("; ".join(refused) + " - nothing was placed")
        risk = abs(price - signal.stop_loss) if signal.stop_loss is not None else 0.0
        rr = round(abs(signal.target1 - price) / risk, 2) if risk and signal.target1 is not None else signal.risk_reward
        signal = signal.model_copy(update={"entry": price, "risk_reward": rr, "timestamp": now,
                                           "reasons": list(signal.reasons) + [f"Approved proposal #{action.id}, re-quoted at {price:g}"]})
        if evidence.get("structure"):          # M55 T2: the approved legs, through the automatic entry's own order path
            await _settle(session, action, await _execute_legs(session, action, owner, evidence, deployment, signal, price, price_lookup, now))
            return action
        result, order = await execute_signal_for_user(
            session, owner, mode="PAPER", strategy_id=str(evidence.get("strategy_id") or signal.strategy_id), signal=signal,
            idempotency_key=f"approval:{action.id}", deployment_id=action.deployment_id, exit_rules=evidence.get("exit_rules"),
            holding=str(evidence.get("holding") or "INTRADAY"), origin=ORIGIN_APPROVAL, approval_action_id=action.id,
            # Z5 / P1-d: a ticket's proposal keeps the plan's quantity as a ceiling and its checked cap (override included).
            max_quantity=(evidence.get("ticket") or {}).get("max_quantity"), risk_cap_amount=(evidence.get("ticket") or {}).get("risk_cap_amount"),
            journal_source="PROPOSAL",            # Z6 L2: the journal records where the trade came from
            exchange=exchange,                    # BK2-8: the position follows its venue's session (CDS, MCX)
        )
        if not result.executed:
            raise ValueError("PAPER order not placed: " + ("; ".join(result.reasons) or order.status))
        values = {"status": "EXECUTED", "executed_at": now, "trade_id": order.trade_id,
                  "result": f"PAPER order #{order.id} placed at the re-quoted price {price:g}" + (f"; position #{order.trade_id}" if order.trade_id else "")}
    except Exception as exc:  # noqa: BLE001 - recorded on the row, shown to the person
        values = {"status": "FAILED", "executed_at": now, "result": str(exc)[:300]}
        order = None
    await _settle(session, action, values)
    plan_id = (evidence.get("ticket") or {}).get("plan_id") if isinstance(evidence, dict) else None
    if plan_id is not None:
        # Z5 / P1-d: the ticket's plan follows its proposal (PLACED with the order, or FAILED with why), so an override
        # placed through an approval is counted like a direct one.
        from app.paper.ticket import settle_from_proposal
        await settle_from_proposal(session, action.tenant_id, int(plan_id), values, order)
    return action


async def _settle(session: AsyncSession, action: AiActionRecord, values: Dict) -> bool:
    """Review V5: the final write of an approved entry is conditional on the row still being APPROVED - if the orphan
    sweep (or anything else) settled it meanwhile, that outcome stands. Leaves `action` reloaded from the database."""
    claimed = await session.execute(update(AiActionRecord).where(AiActionRecord.id == action.id, AiActionRecord.status == "APPROVED")
                                    .values(**values).execution_options(synchronize_session=False))
    await session.commit()
    await session.refresh(action)
    return getattr(claimed, "rowcount", 0) == 1


async def _close_unplaced(session: AsyncSession, action: AiActionRecord, user: User, evidence: Dict, deployment: Optional[StrategyDeploymentRecord],
                          verdict: str, why: str, price: float, now: datetime) -> AiActionRecord:
    """The price moved: the approval places nothing. The row is closed as EXPIRED (it never filled) and, on a re-quote
    within the allowance, a fresh proposal at the current price is raised for a new decision."""
    requotes = int(evidence.get("requotes") or 0)
    # Review V5: conditional - frees the deployment's open-proposal slot for the re-quote unless already settled.
    if not await _settle(session, action, {"status": "EXPIRED", "executed_at": now, "result": f"Not placed: {why}"[:300]}):
        return action
    result = f"Not placed: {why}"
    owner_id = action.owner_user_id if action.owner_user_id is not None else user.id
    if verdict == "requote" and requotes < app_config.APPROVAL_MAX_REQUOTES:
        signal = Signal.model_validate(evidence["signal"])
        risk = abs(price - signal.stop_loss) if signal.stop_loss is not None else 0.0
        rr = round(abs(signal.target1 - price) / risk, 2) if risk and signal.target1 is not None else signal.risk_reward
        try:
            fresh = await propose_entry(session, action.tenant_id, signal=signal.model_copy(update={"entry": price, "risk_reward": rr}),
                                        strategy_id=str(evidence.get("strategy_id") or signal.strategy_id), source="requote",
                                        idempotency_key=f"requote:{action.id}", deployment=deployment,
                                        requested_mode=str(evidence.get("requested_mode") or "PAPER"), exit_rules=evidence.get("exit_rules"),
                                        holding=str(evidence.get("holding") or "INTRADAY"), user_id=owner_id, now=now,
                                        requotes=requotes + 1, requoted_from=action.id, exchange=str(evidence.get("exchange") or "NSE"),
                                        ticket=evidence.get("ticket"), structure=evidence.get("structure"))
        except InvalidEntryPrice:
            # UI2-3 review: a subclass of EntriesPaused, but the reason is the price, not a pause. Defensive only:
            # requote_check already answers a non-finite or non-positive price with "expire", so this is not reached today.
            fresh = None
            result = f"Not placed: {why}. No re-quote: the current price is not usable"
        except EntriesPaused:
            fresh = None
            result = f"Not placed: {why}. No re-quote: PAPER entries are paused"
        if fresh is not None:
            result = f"Not placed: {why}. Re-quoted as proposal #{fresh.id}"
    elif verdict == "requote":
        result = f"Not placed: {why}. No re-quote left ({requotes} of {app_config.APPROVAL_MAX_REQUOTES} used)"
    await session.execute(update(AiActionRecord).where(AiActionRecord.id == action.id).values(result=result[:300])
                          .execution_options(synchronize_session=False))
    await write_audit_log(session, action.tenant_id, user.id, "entry_proposal_not_placed", f"#{action.id}: {result}")
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
    """Which of this organisation's positions ask "Exit now?" on a target: the open PAPER positions that were entered by
    an approved entry proposal (review L2) - any other position keeps its automatic target exit. None when there is none
    (the monitor behaves exactly as before), and None when they cannot be read: the exits sweep then runs as before rather
    than not at all (ADR-0004)."""
    del deployments                      # kept for the caller's signature; the trades themselves say how they entered
    try:
        if not await flag_on(session, tenant_id):          # review V6: flag off - every target exit is automatic again
            return None
        ids = set(await session.scalars(
            select(AiActionRecord.trade_id).join(TradeRecord, TradeRecord.id == AiActionRecord.trade_id)
            .where(AiActionRecord.tenant_id == tenant_id, AiActionRecord.action == ACTION_ENTRY, AiActionRecord.status == "EXECUTED",
                   TradeRecord.tenant_id == tenant_id, TradeRecord.exit_time.is_(None), TradeRecord.mode == "PAPER")))
    except Exception:  # noqa: BLE001 - never in the way of the exits sweep
        logger.exception("Approved positions unreadable for tenant %s - target exits stay automatic this cycle", tenant_id)
        return None
    if not ids:
        return None
    return lambda trade: trade.mode == "PAPER" and trade.id in ids


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
                             reason=text, evidence_json=finite_dumps({"reason": reason, "price": price, "entry_price": trade.entry_price,
                                                                    "stop_loss": trade.stop_loss, "target1": trade.target1, "target2": trade.target2,
                                                                    "mode": trade.mode}),
                             status="PROPOSED", expires_at=expires, owner_user_id=trade.user_id)
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
    """A position closed (by its stop, a time exit, an "Exit now"...): its open "Exit now?" questions are void, and their
    Telegram messages lose the buttons (review L5). Only the questions this module raised (TARGET_* rules), so with
    approval-only off - no such question - it does nothing."""
    try:
        rows = list(await session.scalars(select(AiActionRecord).where(
            AiActionRecord.tenant_id == trade.tenant_id, AiActionRecord.trade_id == trade.id, AiActionRecord.action == "EXIT_POSITION",
            AiActionRecord.rule.in_(TARGET_RULES), AiActionRecord.status == "PROPOSED")))
        if not rows:
            return 0
        retired: List[AiActionRecord] = []
        async with session.begin_nested():       # a failure rolls back the savepoint only; the close is already committed
            for row in rows:
                done = await session.execute(update(AiActionRecord).where(AiActionRecord.id == row.id, AiActionRecord.status == "PROPOSED")
                                             .values(status="EXPIRED", result=f"Position closed: {reason}"[:300]).execution_options(synchronize_session=False))
                if getattr(done, "rowcount", 0) == 1:
                    retired.append(row)
        await session.commit()
        from app.telegram_inbound.service import sync_proposal_message
        for row in retired:
            await session.refresh(row)
            await sync_proposal_message(session, row)
        return len(retired)
    except Exception:  # noqa: BLE001 - bookkeeping only
        logger.warning("Could not retire proposals of closed trade %s", trade.id)
        return 0


async def execution_sources(session: AsyncSession, action: AiActionRecord, user: User) -> Tuple[Optional[PriceLookup], Optional[object]]:
    """(price lookup, broker adapter) an approved ENTRY / EXIT_POSITION needs, for the web and Telegram alike. The price
    is the deciding person's own (with `byob_data_tier` on: their Tier A entitlement only, review M5)."""
    if action.action not in (ACTION_ENTRY, "EXIT_POSITION"):
        return None, None
    from app.ai.worker_bridge import price_lookup_for_user
    lookup = await price_lookup_for_user(session, user)
    broker = None
    if action.action == "EXIT_POSITION" and action.trade_id is not None:
        from app.trading.position_monitor import broker_for_trade
        trade = await session.get(TradeRecord, action.trade_id)
        broker = await broker_for_trade(session, trade) if trade is not None else None
    return lookup, broker


def recent_mark(trade: TradeRecord, now: Optional[datetime] = None) -> bool:
    """Review V2: the monitor's last price is good enough for "Exit now" only while it is fresh - at most
    EXIT_NOW_MAX_MARK_AGE_MINUTES old and from today's session (IST). Pure; protective exits never call it."""
    if trade.mark_price is None or trade.mark_time is None:
        return False
    now = _utc(now or datetime.now(timezone.utc))
    seen = _utc(trade.mark_time)
    if seen.astimezone(IST).date() != now.astimezone(IST).date():
        return False
    return timedelta(0) <= now - seen <= timedelta(minutes=app_config.EXIT_NOW_MAX_MARK_AGE_MINUTES)


async def exit_price(session: AsyncSession, leg: TradeRecord, lookup: Any) -> Tuple[float, str]:
    """The price "Exit now" closes a leg at (and TT-8's leg / group exits): the live price of the person's own entitlement
    (`lookup`, from `price_lookup_for_user`), else the monitor's last price while recent (review V2) - never a typed price
    (review M2). Returns (price, "live price" | "last seen price"); raises ValueError when there is neither."""
    from app.trading.position_monitor import venue_for
    if lookup is not None:
        try:
            return float(await lookup(leg.symbol, await venue_for(session, leg))), "live price"
        except Exception as exc:  # noqa: BLE001 - fall back below
            logger.warning("Exit now: no live price for %s: %s", leg.symbol, type(exc).__name__)
    if leg.mark_price and recent_mark(leg):
        return float(leg.mark_price), "last seen price"
    raise ValueError(f"No recent price for {leg.symbol}: no live quote, and the monitor's last price is missing or older than "
                     f"{app_config.EXIT_NOW_MAX_MARK_AGE_MINUTES} min / not from today's session. Nothing was closed - the stop "
                     "and target keep working; try again when a live price is available")


async def exit_now(session: AsyncSession, trade: TradeRecord, user: User, *, via: str = "web"):
    """"Exit now": closes a PAPER position at once, whatever is pending or snoozed (ADR-0004). Priced at the live price of
    the person's own entitlement, else the last price the monitor saw for it if recent (review V2) - never a typed price
    (review M2). LIVE is
    refused: Phase 0 has no broker order path, LIVE positions are closed in the broker app (review M4). A leg of a
    structure closes the whole structure. The close itself is claimed atomically in `close_position` (review M3).
    Returns the close outcomes; raises ValueError when there is no price at all, or for LIVE."""
    from app.ai.worker_bridge import price_lookup_for_user
    from app.trading.position_monitor import close_group, close_position
    if trade.mode != "PAPER":
        raise ValueError("LIVE positions are closed in the broker app - ATP places no broker order in Phase 0")
    lookup = await price_lookup_for_user(session, user)

    async def price_of(leg: TradeRecord) -> Tuple[float, str]:
        return await exit_price(session, leg, lookup)

    legs = [trade]
    if trade.leg_group_id:
        legs = list(await session.scalars(select(TradeRecord).where(TradeRecord.tenant_id == trade.tenant_id, TradeRecord.leg_group_id == trade.leg_group_id,
                                                                    TradeRecord.exit_time.is_(None)).order_by(TradeRecord.id)))
        if any(leg.mode != "PAPER" for leg in legs):
            raise ValueError("LIVE positions are closed in the broker app - ATP places no broker order in Phase 0")
    prices: Dict[int, float] = {}
    sources: List[str] = []
    for leg in legs:
        prices[leg.id], source = await price_of(leg)
        sources.append(source)
    reason = f"Exit now ({via}, {'live price' if set(sources) == {'live price'} else 'last seen price'})"
    await write_audit_log(session, trade.tenant_id, user.id, "exit_now", f"trade #{trade.id} ({len(legs)} leg(s)) via {via} at {', '.join(sorted(set(sources)))}")
    await session.commit()
    if len(legs) > 1:
        return await close_group(session, legs, prices, reason, broker=None, user_id=user.id)
    return [await close_position(session, trade, prices[trade.id], reason, broker=None, user_id=user.id)]
