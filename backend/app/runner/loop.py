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

Not in S2 (each says so in the deployment's own words): options / futures and multi-leg deployments (they resolve a
contract from the instrument list, which reaches the runner with the link, S4) and strategy templates (they need the
reader port, S1 note). Approval-only entries are refused by the pipeline's own gate (proposals are answered in ATP: S4).
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
import app.execution.order_defaults as order_defaults
import app.platform.phase0_lock as phase0_lock
from app.core.enums import DeploymentStatus, ExecutionMode, SignalDirection
from app.custom_strategies.resolver import TEMPLATE_PREFIX, normalize_strategy_id, resolve_strategy
from app.db.models import StrategyDeploymentRecord, TradeRecord, User
from app.db.session import _session_factory
from app.execution.signal_execution import execute_signal_for_user
from app.market_data.calendar import IST, intraday_cutoffs, market_session_status, session_family
from app.market_data.freshness import candle_staleness, closed_frames
from app.runner.audit import AuditLog
from app.runner.feed import BarFeed
from app.runner.lock import RunnerLock
from app.runner.settings import RunnerSettings
from app.trading.position_monitor import monitor_open_positions, trade_venues
from app.trading.square_off import square_off_due

logger = logging.getLogger("app.runner")

LIVE_SWITCH_OFF = "LIVE is off on this runner - your own LIVE switch is not on (nothing was placed)"
LIVE_NEEDS_BROKER = "LIVE needs your own broker session on this runner - it is running on a test feed (nothing was placed)"
NOT_YET_DERIVATIVES = ("Options / futures deployments run on the runner once ATP sends it the instrument list "
                       "(a later runner step) - nothing was placed")
NOT_YET_TEMPLATES = "Strategy templates run on the runner in a later step - nothing was placed"
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
    async def _exits(self, session: AsyncSession, tenant_id: int, now: datetime, summary: CycleSummary) -> None:
        try:
            open_trades = list(await session.scalars(select(TradeRecord).where(
                TradeRecord.tenant_id == tenant_id, TradeRecord.exit_time.is_(None))))
            if not open_trades:
                return
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
        if live and phase0_lock.locked(dep.tenant_id):
            return await self._skip(session, dep, now, phase0_lock.WORKER_NOT_EVALUATED)   # as the worker: not evaluated
        if live and (refusal := self._live_refusal()):
            return await self._skip(session, dep, now, refusal)
        if (dep.instrument_kind or "UNDERLYING") != "UNDERLYING" or (dep.option_strategy or "SINGLE") != "SINGLE":
            return await self._skip(session, dep, now, NOT_YET_DERIVATIVES)
        if normalize_strategy_id(dep.strategy_id).startswith(TEMPLATE_PREFIX):
            return await self._skip(session, dep, now, NOT_YET_TEMPLATES)

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
        holding = dep.holding or (await order_defaults.load(session, user)).holding
        if holding == "SWING" and signal.direction == SignalDirection.SHORT:
            dep.last_signal_at = signal_ts                        # Phase AS, as the worker: cash equity is not held short
            return await self._skip(session, dep, now, f"Swing SHORT signal at {signal_ts.isoformat()} skipped: cash equity "
                                                       "cannot be held short overnight")

        key = f"deployment:{dep.id}:{signal_ts.isoformat()}"
        intent: dict[str, Any] = dict(deployment_id=dep.id, strategy_id=dep.strategy_id, symbol=dep.symbol, exchange=dep.exchange,
                                      mode=dep.mode, direction=signal.direction.value, signal_bar=signal_ts.isoformat(),
                                      idempotency_key=key)
        self.audit.write("entry_intent", **intent)
        if live and (refusal := self._live_refusal()):         # read again right before the order (his switch)
            self.audit.write("entry_result", **intent, executed=False, reasons=[refusal])
            return await self._skip(session, dep, now, refusal)
        result, order = await execute_signal_for_user(
            session, user, mode=dep.mode, strategy_id=dep.strategy_id, signal=signal, idempotency_key=key,
            broker=self.feed.broker if live else None, deployment_id=dep.id, quote_broker=self.feed.broker,
            exit_rules=dep.exit_rules, holding=dep.holding, order_style=dep.order_style,
            market_protection_pct=dep.market_protection_pct, exchange=dep.exchange)
        reasons = list(result.reasons)
        self.audit.write("entry_result", **intent, executed=bool(result.executed), order_id=order.id, status=order.status,
                         order_mode=order.mode, reasons=reasons[:10])
        dep.last_signal_at = signal_ts
        dep.last_evaluated_at = now
        dep.last_error = None if result.executed else "; ".join(reasons)[:500]
        dep.consecutive_failures = 0
        await session.commit()
        if result.executed:
            summary.entries_placed += 1
        return bool(result.executed)

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


__all__ = ["Runner", "CycleSummary", "LIVE_SWITCH_OFF", "LIVE_NEEDS_BROKER", "NOT_YET_DERIVATIVES", "NOT_YET_TEMPLATES"]
