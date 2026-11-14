"""Runner S2: the runner's loop - one user's deployments on his own machine (docs/design/USER_RUNNER.md, step S2).

One cycle, in the server worker's order (trading_worker.py module docstring), for the deployments in the runner's local
SQLite store:
1. exits first, always: the INTRADAY square-off at his own time and a held structure's expiry settlement
   (`trading/square_off.py`, the worker's own code), then the position monitor's sweep - his stops, targets and exit
   rules (`trading/position_monitor.py`, the one close path). The local kill switch, his LIVE switch, the F&O ban list
   and the ATP link are never read on this path (ADR-0004: exits are never blocked); a LIVE position's exit goes out
   through his own broker session even with his LIVE switch off;
2. entries: for each ACTIVE deployment, his broker's candles -> closed bars only -> the strategy (the DSL / inbuilt
   templates, ADR-0005) -> a Signal -> `execute_signal_for_user`, the ONE entry pipeline the server uses (kill switch,
   platform controls, his own risk settings, router, OPS throttle, algo tag, market protection, PAPER broker for PAPER).
   One position per deployment and one entry per signal bar (the worker's idempotency key).

PAPER is the default. A LIVE entry needs, on top of the deployment's own LIVE choice and ATP's LIVE switches (Phase 0
lock), his own local switch (`settings.live_enabled`) and a real broker feed; the switch is read again right before the
order. Every order intent and result is written to the local audit log (`audit.py`).

S4 (the link, `link.py`; S5 wired in, `broker_session.py`): each cycle first refreshes his broker session (the daily login
when due, the adapter from the keystore), then runs the exits, then ticks the link (heartbeat, his own commands, his
saved rules, the mirror) and only then the entries. While ATP has not answered for longer than his grace setting, new
entries pause (a plain line in link.log); exits never wait for the link.

S4b (`derivatives.py`): options / futures single-leg and multi-leg deployments run as on the server - the contract or the
structure from the deployment's OWN settings (`resolve_contract` / `resolve_structure`, against the contracts ATP sent,
S4), executed by the server's own pipelines (`execute_signal_for_user` with the contract; `multileg.execute_structure`:
hedge legs first, incomplete structures, the F&O ban list for entries only). The exits add the PAPER expiry settlement
(`paper/expiry_settlement.py`, the expiry-day close from his broker's Daily bar) and the incomplete-structure exits
(`execution/incomplete_structures.run_exits`), as the worker runs them.

S4c: ATP's signed state in the heartbeat answer (`link.Link.live_refusal`): a LIVE entry needs ATP's `live_open` for his
organisation (the B1 rule on ATP's side), received within his grace setting, AND his local LIVE file AND the deployment's
own LIVE choice; otherwise it is skipped with a plain reason. His organisation's flags arrive in the same answer and are
written into the local store, so the pipeline reads ATP's answers. Exits never read any of it (ADR-0004).

Not yet (each says so in the deployment's own words): strategy templates (they need the reader port, S1 note).
Approval-only entries are refused by the pipeline's own gate (proposals are answered in ATP: S4).
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import app.execution.exit_settings as exit_settings
import app.execution.incomplete_structures as incomplete_structures
import app.execution.order_defaults as order_defaults
import app.paper.expiry_settlement as expiry_settlement
import app.platform.phase0_lock as phase0_lock
from app.core.enums import DeploymentStatus, ExecutionMode, InstrumentKind, OptionStrategy, SignalDirection
from app.custom_strategies.resolver import TEMPLATE_PREFIX, normalize_strategy_id, resolve_strategy
from app.db.models import StrategyDeploymentRecord, TradeRecord, User
from app.db.session import _session_factory
from app.execution.multileg import execute_structure
from app.execution.signal_execution import execute_signal_for_user
from app.instruments.contracts import ContractResolutionError, ContractRules, resolve_contract
from app.instruments.spreads import parse_custom_legs, resolve_structure
from app.market_data.calendar import IST, intraday_cutoffs, market_session_status, session_family
from app.market_data.freshness import candle_staleness, closed_frames
from app.runner.audit import AuditLog
from app.runner.derivatives import ContractBinder, chain_provider, daily_close, facts_of
from app.runner.feed import BarFeed
from app.runner.lock import RunnerLock
from app.runner.settings import RunnerSettings
from app.trading.position_monitor import monitor_open_positions, trade_venues
from app.trading.square_off import square_off_due

logger = logging.getLogger("app.runner")

LIVE_SWITCH_OFF = "LIVE is off on this runner - your own LIVE switch is not on (nothing was placed)"
LIVE_NEEDS_BROKER = "LIVE needs your own broker session on this runner - it is running on a test feed (nothing was placed)"
# S4c: LIVE_NO_ATP_STATE (S4b) is replaced by ATP's signed state (link.STATE_NEVER / STATE_STALE / STATE_CLOSED); the
# name stays for its importers and is the text of a runner that is not paired (no link: no ATP state).
LIVE_NO_ATP_STATE = ("LIVE needs ATP's answer: this runner is not paired with ATP, so ATP's LIVE state for your organisation "
                     "is unknown - nothing was placed; exits continue")
NOT_YET_TEMPLATES = "Strategy templates run on the runner in a later step - nothing was placed"
NOT_YET_OI_GATES = "Your OI gates need ATP's option-chain data, which the runner does not have yet - nothing was placed"
NO_USER = "No user for this deployment on the runner - nothing was placed"


@dataclass
class CycleSummary:
    started_at: datetime
    skipped: bool = False                 # another cycle of this process was still running
    tenants: int = 0
    exits_closed: int = 0
    deployments_evaluated: int = 0
    entries_placed: int = 0
    entries_paused: str | None = None  # the local kill switch
    errors: list[str] = field(default_factory=list)


def _utc(value: datetime) -> datetime:
    return (value if value.tzinfo else value.replace(tzinfo=UTC)).astimezone(UTC)


class Runner:
    def __init__(self, settings: RunnerSettings, feed: BarFeed, *, audit: AuditLog | None = None,
                 lock: RunnerLock | None = None, sessions: Any = None, link: Any = None, broker_session: Any = None) -> None:
        self.settings = settings
        self.feed = feed
        self.audit = audit or AuditLog(settings.audit_path)
        self.lock = lock or RunnerLock(settings.lock_path)
        self._killed: bool | None = None
        self.sessions = sessions or _session_factory        # the runner's local store (tests: their own file)
        self.link = link                                    # S4: None = not paired (dry runs, tests): no link gate
        self.broker_session = broker_session                # S4: S5's login + keystore adapter (None: the given feed)
        self.binder = ContractBinder()                      # S4b: ATP's contract names on his broker's instruments
        self._last_expiry_sweep: datetime | None = None

    # --- one cycle ------------------------------------------------------------------------------------------------
    async def run_cycle(self, now: datetime | None = None) -> CycleSummary:
        now = _utc(now or datetime.now(UTC))
        summary = CycleSummary(started_at=now)
        async with self.lock.cycle() as mine:
            if not mine:
                summary.skipped = True
                return summary
            if self.broker_session is not None:
                try:
                    feed = await self.broker_session.refresh(now)
                    if feed is not None:
                        self.feed = feed
                except Exception as exc:  # noqa: BLE001 - the last feed stands; exits go on
                    summary.errors.append(f"broker session: {exc}")
            async with self.sessions() as session:
                try:
                    await phase0_lock.is_on(session, None)      # ATP's PAPER-only lock, as the worker refreshes it
                except Exception as exc:  # noqa: BLE001 - unknown state: the last read (or the safe default) stands
                    logger.warning("Runner: PAPER-only lock not read (%s)", exc)
                    await session.rollback()
                await self._settle_expired(session, now, summary)
                for tenant_id in await self._tenants(session):
                    summary.tenants += 1
                    await self._exits(session, tenant_id, now, summary)
                open_positions = len(list(await session.scalars(select(TradeRecord.id).where(TradeRecord.exit_time.is_(None)))))
                await session.commit()                           # no read held open while the link writes the store
            if self.link is not None:
                try:
                    await self.link.tick(now, killed=self.settings.killed(), open_positions=open_positions)
                except Exception as exc:  # noqa: BLE001 - the link never stops the runner
                    summary.errors.append(f"link: {exc}")
                    logger.exception("Runner: link tick failed")
            async with self.sessions() as session:
                for tenant_id in await self._tenants(session):
                    await self._entries(session, tenant_id, now, summary)
        return summary

    async def _tenants(self, session: AsyncSession) -> list[int]:
        deps = set(await session.scalars(select(StrategyDeploymentRecord.tenant_id).where(
            StrategyDeploymentRecord.status.in_((DeploymentStatus.ACTIVE.value, DeploymentStatus.PAUSED.value)))))
        held = set(await session.scalars(select(TradeRecord.tenant_id).where(TradeRecord.exit_time.is_(None))))
        return sorted(deps | held)

    # --- 1. exits (never blocked) -----------------------------------------------------------------------------------
    async def _settle_expired(self, session: AsyncSession, now: datetime, summary: CycleSummary) -> None:
        """S4b: the worker's PAPER expiry settlement (every SWEEP_SECONDS, whatever the session): an open PAPER index
        option / future past its expiry close, settled in cash at the expiry-day close - here his broker's Daily bar of
        that day. An exit: never gated; LIVE rows are never read (the broker settles them)."""
        if self._last_expiry_sweep is not None and (now - self._last_expiry_sweep).total_seconds() < expiry_settlement.SWEEP_SECONDS:
            return
        try:
            report = await expiry_settlement.settle_expired(session, now, close_of=daily_close(self.feed.frames, now))
            self._last_expiry_sweep = now
        except Exception as exc:  # noqa: BLE001 - tried again next pass
            await session.rollback()
            summary.errors.append(f"expiry settlement: {exc}")
            logger.exception("Runner: PAPER expiry settlement failed")
            return
        for trade_id in report.settled:
            trade = await session.get(TradeRecord, trade_id)
            summary.exits_closed += 1
            self.audit.write("exit_result", trade_id=trade_id, deployment_id=getattr(trade, "deployment_id", None),
                             symbol=getattr(trade, "symbol", None), mode="PAPER", reason=expiry_settlement.SETTLED_REASON,
                             exit_price=getattr(trade, "exit_price", None), pnl=getattr(trade, "pnl", None))

    async def _exits(self, session: AsyncSession, tenant_id: int, now: datetime, summary: CycleSummary) -> None:
        try:
            open_trades = list(await session.scalars(select(TradeRecord).where(
                TradeRecord.tenant_id == tenant_id, TradeRecord.exit_time.is_(None))))
            if not open_trades:
                return
            for problem in await self.binder.bind_open(session, self.feed.broker, open_trades):
                self.audit.write("exit_problem", warnings=[problem])     # priced again next cycle; never blocks the others
            venues = await trade_venues(session, open_trades)
            families = {session_family(v) for v in venues.values()}
            open_families = {f for f in families if (await market_session_status(session, now, f)).is_open}
            if not open_families:
                return
            before = [t.id for t in open_trades]
            user_id = open_trades[0].user_id
            now_ist = now.astimezone(IST)
            settling = {f for f in open_families if (cut := intraday_cutoffs(f)[1]) is not None and now_ist.time() >= cut}
            broker = self.feed.broker        # his own session for a LIVE position's exit - never gated by a switch
        except Exception as exc:  # noqa: BLE001 - retried next cycle
            await session.rollback()
            summary.errors.append(f"exits: {exc}")
            logger.exception("Runner: exits could not start for tenant %s", tenant_id)
            return
        try:
            await square_off_due(session, tenant_id, self.feed.ltp, broker, user_id, families=open_families,
                                 today=now_ist.date(), settle_families=settling, now_ist=now_ist)
        except Exception as exc:  # noqa: BLE001 - the monitor below still runs
            await session.rollback()
            summary.errors.append(f"square-off: {exc}")
            logger.exception("Runner: square-off failed for tenant %s", tenant_id)
        try:
            outcomes = await monitor_open_positions(session, tenant_id, self.feed.ltp, broker=broker, user_id=user_id,
                                                    families=open_families, now=now)
            for outcome in outcomes:
                if not outcome.closed and outcome.warnings:
                    self.audit.write("exit_problem", trade_id=outcome.trade_id, warnings=list(outcome.warnings))
        except Exception as exc:  # noqa: BLE001 - retried next cycle
            await session.rollback()
            summary.errors.append(f"position monitor: {exc}")
            logger.exception("Runner: position monitor failed for tenant %s", tenant_id)
        try:
            # SE-7: an incomplete multi-leg order - his "if I do not answer" rule at the deadline, and the filled legs he
            # (or his rule) chose to close. An exit, as in the worker: never gated (ADR-0004).
            await incomplete_structures.run_exits(session, tenant_id, self.feed.ltp, broker=broker, user_id=user_id, now=now)
        except Exception as exc:  # noqa: BLE001 - one bad group never ends the exits; retried next cycle
            await session.rollback()
            summary.errors.append(f"incomplete multi-leg orders: {exc}")
            logger.exception("Runner: incomplete multi-leg exits failed for tenant %s", tenant_id)
        closed = list(await session.scalars(select(TradeRecord).where(
            TradeRecord.id.in_(before), TradeRecord.exit_time.is_not(None))))
        for trade in closed:
            summary.exits_closed += 1
            self.audit.write("exit_result", trade_id=trade.id, deployment_id=trade.deployment_id, symbol=trade.symbol,
                             mode=trade.mode, direction=trade.direction, quantity=trade.quantity, reason=trade.exit_reason,
                             exit_price=trade.exit_price, pnl=trade.pnl)

    # --- 2. entries -------------------------------------------------------------------------------------------------
    async def _entries(self, session: AsyncSession, tenant_id: int, now: datetime, summary: CycleSummary) -> None:
        killed = self.settings.killed()
        if killed != self._killed:
            self.audit.write("kill_switch", on=killed)
            self._killed = killed
        if killed:
            summary.entries_paused = "Local kill switch is on - no new entries; exits continue"
            return
        if self.link is not None and (offline := self.link.entries_paused(now)):
            summary.entries_paused = offline                     # ATP unreachable past his grace: entries only
            return
        if self.link is not None and (no_plan := self.link.plan_refusal()):
            summary.entries_paused = no_plan                     # S4c: his plan's `runner` field says no: entries only
            return
        self._atp_live_refusal(now, tenant_id)                   # S4c: the lock state the pipeline reads, fresh this cycle
        deployments = list(await session.scalars(select(StrategyDeploymentRecord).where(
            StrategyDeploymentRecord.tenant_id == tenant_id,
            StrategyDeploymentRecord.status == DeploymentStatus.ACTIVE.value).order_by(StrategyDeploymentRecord.id)))
        for dep in deployments:
            dep_id = dep.id
            try:
                await self._evaluate(session, dep, now, summary)
            except Exception as exc:  # noqa: BLE001 - recorded on the deployment, never fatal; the others go on
                await session.rollback()
                summary.errors.append(f"deployment {dep_id}: {exc}")
                logger.exception("Runner: deployment %s failed", dep_id)
                again = await session.get(StrategyDeploymentRecord, dep_id)
                if again is not None:
                    again.last_error = f"Runner: {type(exc).__name__}: {exc}"[:500]
                    again.consecutive_failures = (again.consecutive_failures or 0) + 1
                    await session.commit()

    async def _user(self, session: AsyncSession, dep: StrategyDeploymentRecord) -> User | None:
        if dep.created_by is not None:
            user = await session.get(User, dep.created_by)
            if user is not None and user.tenant_id == dep.tenant_id:
                return user
        return await session.scalar(select(User).where(User.tenant_id == dep.tenant_id).order_by(User.id).limit(1))

    def _live_refusal(self) -> str | None:
        """Why this runner places no LIVE order now (his own switch, read fresh), or None."""
        if self.feed.paper_only or self.feed.broker is None:
            return LIVE_NEEDS_BROKER
        if not self.settings.live_enabled():
            return LIVE_SWITCH_OFF
        return None

    def _atp_live_refusal(self, now: datetime, tenant_id: int | None) -> str | None:
        """S4c: ATP's LIVE state for his organisation from the link's last signed answer (fresh within his grace setting),
        or the plain reason it is not open. Not paired: unknown, refused (`LIVE_NO_ATP_STATE`). Sets the lock state the
        pipeline and the broker chokepoint read (`runtime_mode.live_open`)."""
        if self.link is None:
            import app.core.runtime_mode as runtime_mode
            runtime_mode.set_live_open(())
            return LIVE_NO_ATP_STATE
        return self.link.live_refusal(now, tenant_id)

    async def _skip(self, session: AsyncSession, dep: StrategyDeploymentRecord, now: datetime, reason: str | None) -> bool:
        dep.last_evaluated_at = now
        dep.last_error = reason[:500] if reason else None
        await session.commit()
        return False

    async def _evaluate(self, session: AsyncSession, dep: StrategyDeploymentRecord, now: datetime, summary: CycleSummary) -> bool:
        if not (await market_session_status(session, now, dep.exchange)).is_open:
            return False                                          # no session: nothing to read, nothing written
        user = await self._user(session, dep)
        if user is None:
            return await self._skip(session, dep, now, NO_USER)
        live = dep.mode == ExecutionMode.LIVE.value
        if live and (refusal := self._atp_live_refusal(now, dep.tenant_id)):
            return await self._skip(session, dep, now, refusal)                          # S4c: ATP's state, in plain words
        if live and phase0_lock.locked(dep.tenant_id):
            return await self._skip(session, dep, now, phase0_lock.WORKER_NOT_EVALUATED)   # as the worker: not evaluated
        if live and (refusal := self._live_refusal()):
            return await self._skip(session, dep, now, refusal)
        derivative = (dep.instrument_kind or "UNDERLYING") != "UNDERLYING"
        if normalize_strategy_id(dep.strategy_id).startswith(TEMPLATE_PREFIX):
            return await self._skip(session, dep, now, NOT_YET_TEMPLATES)
        if getattr(dep, "oi_gates", None):
            return await self._skip(session, dep, now, NOT_YET_OI_GATES)   # S8: fail-closed, as the worker's gate

        strategy = await resolve_strategy(dep.strategy_id, user, session)
        frames = await self.feed.frames(dep.symbol, dep.exchange, strategy.timeframes, now)
        summary.deployments_evaluated += 1
        if not frames or not strategy.has_enough_history(frames):
            bars = len(next(iter(frames.values()))) if frames else 0
            return await self._skip(session, dep, now, f"Insufficient history ({bars} bars) - waiting for more candles")
        base = frames.get(dep.timeframe)
        if base is None:
            base = next(iter(frames.values()))
        stale = candle_staleness(base.index[-1].to_pydatetime() if len(base) else None, dep.timeframe, now)
        if stale is not None:
            return await self._skip(session, dep, now, f"Skipped: {stale}")
        frames = closed_frames(frames, now)                      # closed bars only (no look-ahead), as the worker
        if not strategy.has_enough_history(frames):
            return await self._skip(session, dep, now, "Insufficient history (closed bars) - waiting for more candles")

        signal = strategy.analyze(frames, dep.symbol)
        if not signal.is_tradeable:
            dep.consecutive_failures = 0
            return await self._skip(session, dep, now, None)
        signal_ts = _utc(signal.timestamp)
        if dep.last_signal_at is not None and _utc(dep.last_signal_at) >= signal_ts:
            dep.last_evaluated_at = now
            await session.commit()
            return False                                          # this bar's signal was already handled
        window = (await exit_settings.load(session, user)).entry_window_reason(session_family(dep.exchange),
                                                                               now.astimezone(IST).time())
        if window:
            return await self._skip(session, dep, now, f"Signal at {signal_ts.isoformat()} skipped: {window}")
        open_trade = await session.scalar(select(TradeRecord.id).where(
            TradeRecord.tenant_id == dep.tenant_id, TradeRecord.exit_time.is_(None), TradeRecord.deployment_id == dep.id).limit(1))
        if open_trade is not None:
            return await self._skip(session, dep, now, None)      # one position per deployment
        if opposite := await order_defaults.opposite_position(session, dep, signal.direction):
            return await self._skip(session, dep, now, opposite)  # S8: his organisation's "one direction per symbol", as the worker
        holding, holding_problem = await order_defaults.bot_holding(session, dep, user)   # the worker's rule (M68 PR-1)
        if holding is None:
            dep.last_signal_at = signal_ts                        # as the worker: nothing placed, the card says which setting
            return await self._skip(session, dep, now, f"Signal at {signal_ts.isoformat()} skipped. {holding_problem}")
        if holding == "SWING" and not derivative and signal.direction == SignalDirection.SHORT:
            dep.last_signal_at = signal_ts                        # Phase AS, as the worker: cash equity is not held short
            return await self._skip(session, dep, now, f"Swing SHORT signal at {signal_ts.isoformat()} skipped: cash equity "
                                                       "cannot be held short overnight")

        key = f"deployment:{dep.id}:{signal_ts.isoformat()}"
        intent: dict[str, Any] = dict(deployment_id=dep.id, strategy_id=dep.strategy_id, symbol=dep.symbol, exchange=dep.exchange,
                                      mode=dep.mode, direction=signal.direction.value, signal_bar=signal_ts.isoformat(),
                                      idempotency_key=key)
        rules = ContractRules.from_deployment(dep)
        structure_kind = OptionStrategy(dep.option_strategy or "SINGLE")
        quotes = self.feed.broker or getattr(self.feed, "quotes", None)   # his session (PAPER fills are priced from it)
        contract = structure = None
        if rules.derived:
            # Phase F3 / H2 as in the worker: the contract (or the structure) from his own settings, off the latest closed
            # underlying close; a rule that cannot be satisfied is recorded, never traded around.
            base = frames.get(dep.timeframe)
            if base is None:
                base = next(iter(frames.values()))
            spot = float(base["close"].iloc[-1]) if len(base) else None
            today = now.astimezone(IST).date()
            multi = rules.kind == InstrumentKind.OPTION and structure_kind != OptionStrategy.SINGLE
            try:
                if multi:
                    structure = await resolve_structure(
                        session, dep.symbol, rules, structure_kind, signal.direction, spread_width=dep.spread_width, spot=spot,
                        today=today, chain_provider=chain_provider(quotes), custom_legs=parse_custom_legs(dep.custom_legs))
                    await self.binder.bind(self.feed.broker, [facts_of(leg.contract) for leg in structure.legs])
                else:
                    contract = await resolve_contract(session, dep.symbol, rules, signal.direction, spot=spot, today=today,
                                                      chain_provider=chain_provider(quotes))
                    await self.binder.bind(self.feed.broker, [facts_of(contract)])
            except (ContractResolutionError, ValueError) as exc:
                if multi and "not entered on" in str(exc):
                    dep.last_signal_at = signal_ts
                why = f"{'Structure not built' if multi else 'Contract not resolved'}: {exc}"
                self.audit.write("entry_intent", **intent)
                self.audit.write("entry_result", **intent, executed=False, reasons=[why])
                return await self._skip(session, dep, now, why)
            intent["contracts"] = ([leg.contract.tradingsymbol for leg in structure.legs] if structure is not None
                                   else [contract.tradingsymbol] if contract is not None else [])
        self.audit.write("entry_intent", **intent)
        if live and (refusal := self._atp_live_refusal(now, dep.tenant_id) or self._live_refusal()):   # again before the order
            self.audit.write("entry_result", **intent, executed=False, reasons=[refusal])
            return await self._skip(session, dep, now, refusal)
        if structure is not None:
            outcome = await execute_structure(
                session, user, mode=dep.mode, strategy_id=dep.strategy_id, signal=signal, structure=structure, rules=rules,
                target_credit_pct=dep.target_credit_pct, stop_credit_pct=dep.stop_credit_pct,
                underlying_exit=bool(dep.underlying_breach_exit), idempotency_key=key,
                broker=self.feed.broker if live else None, quote_broker=quotes, deployment_id=dep.id, holding=holding)
            executed, reasons = bool(outcome.executed), list(outcome.reasons)
            self.audit.write("entry_result", **intent, executed=executed, order_ids=[o.id for o in outcome.orders],
                             statuses=[o.status for o in outcome.orders], order_mode=dep.mode, reasons=reasons[:10])
        else:
            result, order = await execute_signal_for_user(
                session, user, mode=dep.mode, strategy_id=dep.strategy_id, signal=signal, idempotency_key=key,
                broker=self.feed.broker if live else None, deployment_id=dep.id,
                contract=contract, rules=rules if contract is not None else None,
                quote_broker=quotes if contract is not None else self.feed.broker,
                exit_rules=dep.exit_rules, holding=holding, order_style=dep.order_style,
                market_protection_pct=dep.market_protection_pct, exchange=dep.exchange)
            executed, reasons = bool(result.executed), list(result.reasons)
            self.audit.write("entry_result", **intent, executed=executed, order_id=order.id, status=order.status,
                             order_mode=order.mode, reasons=reasons[:10])
        dep.last_signal_at = signal_ts
        dep.last_evaluated_at = now
        dep.last_error = None if executed else "; ".join(reasons)[:500]
        dep.consecutive_failures = 0
        await session.commit()
        if executed:
            summary.entries_placed += 1
        return executed

    # --- the scheduler ----------------------------------------------------------------------------------------------
    def next_wake(self, now: datetime) -> datetime:
        """The next base-bar close (cycle_seconds, aligned to the clock as candles are) plus the settle delay."""
        step = max(1, int(self.settings.cycle_seconds))
        epoch = int(_utc(now).timestamp())
        close = datetime.fromtimestamp((epoch // step + 1) * step, tz=UTC)
        return close + timedelta(seconds=float(self.settings.settle_seconds))

    async def run_forever(self, stop: asyncio.Event) -> None:
        """Cycles at each bar close until `stop` is set; the cycle in progress always finishes (clean shutdown)."""
        self.lock.acquire()
        self.audit.write("runner_started", live_switch=self.settings.live_enabled(), paper_only_feed=self.feed.paper_only)
        try:
            while not stop.is_set():
                summary = await self.run_cycle()
                if summary.errors:
                    logger.warning("Runner cycle: %s", "; ".join(summary.errors)[:500])
                delay = (self.next_wake(datetime.now(UTC)) - datetime.now(UTC)).total_seconds()
                try:
                    await asyncio.wait_for(stop.wait(), timeout=max(0.0, delay))
                except TimeoutError:
                    pass
        finally:
            self.audit.write("runner_stopped")
            self.lock.release()


__all__ = ["Runner", "CycleSummary", "LIVE_SWITCH_OFF", "LIVE_NEEDS_BROKER", "LIVE_NO_ATP_STATE", "NOT_YET_TEMPLATES",
           "NOT_YET_OI_GATES"]
