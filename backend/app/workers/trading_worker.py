"""The autonomous trading worker: the process that actually trades while nobody is looking.

Runs as its own container/service (docker-compose `worker`, `python -m app.workers.trading_worker`),
never inside the API process, so an API restart or a closed browser never interrupts trading -
the exact failure the previous single-user bot had, with its engine living inside the Streamlit
UI process. Every `WORKER_CYCLE_SECONDS` (default 60, one base candle) one cycle does, in order:

1. Heartbeat + session check - is the NSE regular session open right now (market calendar +
   holidays)? If not, nothing else runs; the heartbeat still proves the worker is alive.
2. Per tenant with ACTIVE/PAUSED deployments: verify the broker token(s) it needs (cheap profile
   call, at most every few minutes), so LIVE never fires on an expired token and the operator is
   told (TOKEN_EXPIRED) the moment it is.
3. Exits before entries: sweep that tenant's open positions against live LTPs (position monitor),
   and past the intraday square-off time flatten everything still open.
4. Entries: for each ACTIVE deployment fetch candles (cached, resampled), run the strategy, and if
   it signals a trade push it through the *same* execute_signal_for_user pipeline the console and
   webhooks use - kill switches, risk engine, order state machine, notifications, all of it.
   One position per deployment, one entry per signal bar (idempotency key), no entries after the
   cut-off time.
5. Bookkeeping: per-deployment last_evaluated/last_signal/last_error, auto-PAUSE after repeated
   failures (with a SYSTEM_FAILURE alert), heartbeat row for the dashboard/ops.

A Redis lock guards against two replicas trading the same deployments; Redis down means the lock
fails open (single replica assumed) - see app/cache/client.py::cache_acquire_lock.
"""
import asyncio
import logging
import socket
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, time as dtime, timezone
from typing import Callable, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.brokers.base import BrokerInterface
from app.alerts.dispatcher import dispatch_pending
from app.brokers.token_lifecycle import build_adapter, get_credential_record, token_is_usable, verify_token
from app.cache.client import cache_acquire_lock, cache_release_lock
from app.core.config import WORKER_CYCLE_SECONDS
from app.core.enums import DeploymentStatus, ExecutionMode, NotificationSeverity, NotificationType
from app.core.logging_config import bind_log_context, configure_logging
from app.custom_strategies.resolver import resolve_strategy
from app.db.models import BrokerCredentialRecord, StrategyDeploymentRecord, TradeRecord, User, WorkerHeartbeatRecord
from app.execution.signal_execution import execute_signal_for_user
from app.market_data.calendar import IST, market_session_status
from app.market_data.service import MarketDataService
from app.notifications.service import notify
from app.trading.position_monitor import close_position, exchange_for_symbol, monitor_open_positions

logger = logging.getLogger(__name__)

WORKER_NAME = "trading_worker"
LOCK_KEY = "atp:trading_worker:lock"

# Intraday (MIS) discipline: no fresh entries in the last half hour, and flatten everything
# before the brokers' own forced square-off (which typically starts ~15:20 IST and fills badly).
NO_NEW_ENTRIES_AFTER = dtime(15, 0)
SQUARE_OFF_AT = dtime(15, 15)

# A deployment that fails this many cycles in a row is paused rather than retried forever.
MAX_CONSECUTIVE_FAILURES = 5
# How often a VALID token is re-proven against the broker.
TOKEN_RECHECK_SECONDS = 300


@dataclass
class CycleReport:
    started_at: datetime
    market_open: bool
    session_reason: str
    tenants_processed: int = 0
    deployments_evaluated: int = 0
    signals_executed: int = 0
    positions_closed: int = 0
    errors: List[str] = field(default_factory=list)
    skipped_lock: bool = False


class TradingWorker:
    def __init__(
        self, session_factory: async_sessionmaker, *, cycle_seconds: int = WORKER_CYCLE_SECONDS,
        market_data_factory: Callable[[BrokerInterface], MarketDataService] = MarketDataService,
        worker_name: str = WORKER_NAME,
    ) -> None:
        self.session_factory = session_factory
        self.cycle_seconds = cycle_seconds
        self.market_data_factory = market_data_factory
        self.worker_name = worker_name
        self.holder_id = f"{socket.gethostname()}:{uuid.uuid4().hex[:8]}"
        self._stop = asyncio.Event()
        self._last_token_check: Dict[int, float] = {}

    # --- lifecycle --------------------------------------------------------------------------

    def stop(self) -> None:
        self._stop.set()

    async def run_forever(self) -> None:
        logger.info("Trading worker %s starting (cycle %ss)", self.holder_id, self.cycle_seconds)
        while not self._stop.is_set():
            started = time.monotonic()
            try:
                await self.run_cycle()
            except Exception:  # noqa: BLE001 - the loop must survive anything
                logger.exception("Trading worker cycle crashed")
            elapsed = time.monotonic() - started
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=max(1.0, self.cycle_seconds - elapsed))
            except asyncio.TimeoutError:
                pass
        logger.info("Trading worker %s stopped", self.holder_id)

    # --- one cycle ---------------------------------------------------------------------------

    async def run_cycle(self, now: Optional[datetime] = None) -> CycleReport:
        now = now or datetime.now(timezone.utc)
        cycle_started = time.monotonic()
        lock_ttl = max(self.cycle_seconds * 2, 30)
        if not await cache_acquire_lock(LOCK_KEY, self.holder_id, lock_ttl):
            logger.info("Another worker replica holds the lock - skipping this cycle")
            return CycleReport(started_at=now, market_open=False, session_reason="lock held elsewhere", skipped_lock=True)

        try:
            async with self.session_factory() as session:
                status = await market_session_status(session, now)
                report = CycleReport(started_at=now, market_open=status.is_open, session_reason=status.reason)
                if status.is_open:
                    await self._process_tenants(session, now, report)
                else:
                    logger.debug("Market closed: %s", status.reason)
                # Out-of-app alert delivery (Telegram/email) rides on this loop, market open or not:
                # a TOKEN_EXPIRED raised at 03:31 must reach a phone before 09:15.
                try:
                    await dispatch_pending(session)
                except Exception as exc:  # noqa: BLE001 - alerting must never break trading
                    logger.exception("Alert dispatch failed")
                    report.errors.append(f"alert dispatch: {exc}")
                await self._heartbeat(session, report, int((time.monotonic() - cycle_started) * 1000))
                return report
        finally:
            await cache_release_lock(LOCK_KEY, self.holder_id)

    async def _process_tenants(self, session: AsyncSession, now: datetime, report: CycleReport) -> None:
        deployments = list(await session.scalars(
            select(StrategyDeploymentRecord)
            .where(StrategyDeploymentRecord.status.in_([DeploymentStatus.ACTIVE.value, DeploymentStatus.PAUSED.value]))
            .order_by(StrategyDeploymentRecord.tenant_id, StrategyDeploymentRecord.id)
        ))
        by_tenant: Dict[int, List[StrategyDeploymentRecord]] = {}
        for dep in deployments:
            by_tenant.setdefault(dep.tenant_id, []).append(dep)

        for tenant_id, tenant_deployments in by_tenant.items():
            with bind_log_context(tenant_id=tenant_id):
                try:
                    await self._process_tenant(session, tenant_id, tenant_deployments, now, report)
                    report.tenants_processed += 1
                except Exception as exc:  # noqa: BLE001 - one tenant must never block the others
                    logger.exception("Tenant %s cycle failed", tenant_id)
                    report.errors.append(f"tenant {tenant_id}: {exc}")

    async def _process_tenant(
        self, session: AsyncSession, tenant_id: int, deployments: List[StrategyDeploymentRecord],
        now: datetime, report: CycleReport,
    ) -> None:
        user = await self._acting_user(session, tenant_id, deployments)
        if user is None:
            logger.warning("Tenant %s has deployments but no user to act as - skipping", tenant_id)
            return

        # One adapter per broker the tenant trades through, only when its token is proven.
        adapters: Dict[str, BrokerInterface] = {}
        broker_names = {d.broker_name for d in deployments if d.broker_name}
        if not broker_names:
            # PAPER deployments still need a market-data source: any stored broker will do.
            records = list(await session.scalars(
                select(BrokerCredentialRecord).where(BrokerCredentialRecord.tenant_id == tenant_id)
            ))
            broker_names = {r.broker_name for r in records}
        for name in broker_names:
            adapter = await self._usable_adapter(session, tenant_id, name, user.id, now)
            if adapter is not None:
                adapters[name] = adapter

        data_broker = next(iter(adapters.values()), None)
        if data_broker is None:
            message = "No usable broker session (token expired or missing) - log in again from Settings"
            for dep in deployments:
                dep.last_error = message
            await session.commit()
            logger.warning("Tenant %s: %s", tenant_id, message)
            return
        market_data = self.market_data_factory(data_broker)

        # Exits before entries.
        now_ist = now.astimezone(IST)
        live_broker = next((a for n, a in adapters.items() if n in {d.broker_name for d in deployments if d.mode == "LIVE"}), None)
        if now_ist.time() >= SQUARE_OFF_AT:
            report.positions_closed += await self._square_off_all(session, tenant_id, market_data, live_broker, user.id)
        else:
            outcomes = await monitor_open_positions(session, tenant_id, market_data.get_ltp, broker=live_broker, user_id=user.id)
            report.positions_closed += sum(1 for o in outcomes if o.closed)

        entries_allowed = now_ist.time() < NO_NEW_ENTRIES_AFTER
        for dep in deployments:
            if dep.status != DeploymentStatus.ACTIVE.value:
                continue
            with bind_log_context(strategy_id=dep.strategy_id, deployment_id=dep.id):
                try:
                    executed = await self._evaluate_deployment(
                        session, dep, user, market_data, adapters, now, entries_allowed,
                    )
                    report.deployments_evaluated += 1
                    if executed:
                        report.signals_executed += 1
                except Exception as exc:  # noqa: BLE001 - recorded on the deployment, never fatal
                    await self._record_failure(session, dep, exc, user.id)
                    report.errors.append(f"deployment {dep.id}: {exc}")

    async def _evaluate_deployment(
        self, session: AsyncSession, dep: StrategyDeploymentRecord, user: User, market_data: MarketDataService,
        adapters: Dict[str, BrokerInterface], now: datetime, entries_allowed: bool,
    ) -> bool:
        strategy = await resolve_strategy(dep.strategy_id, user, session)
        frames = await market_data.get_frames(dep.symbol, dep.exchange, dep.timeframe, strategy.timeframes)
        # Always persist UTC: a DB that drops tzinfo (SQLite) would otherwise store an IST wall
        # time that reads back as if it were UTC, 5.5h in the future.
        dep.last_evaluated_at = now.astimezone(timezone.utc)
        if not frames or not strategy.has_enough_history(frames):
            bars = len(next(iter(frames.values()))) if frames else 0
            dep.last_error = f"Insufficient history ({bars} bars) - waiting for more candles"
            await session.commit()
            return False

        signal = strategy.analyze(frames, dep.symbol)
        if not signal.is_tradeable:
            dep.last_error = None
            dep.consecutive_failures = 0
            await session.commit()
            return False

        signal_ts = signal.timestamp if signal.timestamp.tzinfo else signal.timestamp.replace(tzinfo=timezone.utc)
        signal_ts = signal_ts.astimezone(timezone.utc)
        if dep.last_signal_at is not None:
            last = dep.last_signal_at if dep.last_signal_at.tzinfo else dep.last_signal_at.replace(tzinfo=timezone.utc)
            if last >= signal_ts:
                # Same (or older) bar already acted on - the strategy is still "in signal", not signalling anew.
                await session.commit()
                return False
        if not entries_allowed:
            dep.last_error = f"Signal at {signal_ts.isoformat()} skipped: no new entries after {NO_NEW_ENTRIES_AFTER.strftime('%H:%M')} IST"
            await session.commit()
            return False
        if await self._has_open_position(session, dep):
            dep.last_error = None
            await session.commit()
            return False

        broker = None
        if dep.mode == ExecutionMode.LIVE.value:
            broker = adapters.get(dep.broker_name or "")
            if broker is None:
                dep.last_error = f"LIVE entry skipped: no usable {dep.broker_name} session - log in again from Settings"
                await session.commit()
                logger.warning("Deployment %s LIVE entry skipped: token not usable", dep.id)
                return False

        idempotency_key = f"deployment:{dep.id}:{signal_ts.isoformat()}"
        result, order = await execute_signal_for_user(
            session, user, mode=dep.mode, strategy_id=dep.strategy_id, signal=signal,
            idempotency_key=idempotency_key, broker=broker, deployment_id=dep.id,
        )
        dep.last_signal_at = signal_ts
        dep.last_error = None if result.executed else "; ".join(result.reasons)[:500]
        dep.consecutive_failures = 0
        await session.commit()
        logger.info("Deployment %s signal %s -> executed=%s order=%s", dep.id, signal.direction.value, result.executed, order.id)
        return result.executed

    # --- helpers -----------------------------------------------------------------------------

    async def _acting_user(
        self, session: AsyncSession, tenant_id: int, deployments: List[StrategyDeploymentRecord],
    ) -> Optional[User]:
        creator_ids = [d.created_by for d in deployments if d.created_by is not None]
        if creator_ids:
            user = await session.get(User, creator_ids[0])
            if user is not None and user.tenant_id == tenant_id:
                return user
        return await session.scalar(select(User).where(User.tenant_id == tenant_id).order_by(User.id).limit(1))

    async def _usable_adapter(
        self, session: AsyncSession, tenant_id: int, broker_name: str, user_id: int, now: datetime,
    ) -> Optional[BrokerInterface]:
        record = await get_credential_record(session, tenant_id, broker_name)
        if record is None:
            return None
        last_check = self._last_token_check.get(record.id, 0.0)
        due = (time.monotonic() - last_check) >= TOKEN_RECHECK_SECONDS
        if due or not token_is_usable(record, now):
            if record.token_status == "EXPIRED" and not due:
                return None
            ok, message = await verify_token(session, record, user_id=user_id)
            await session.commit()
            self._last_token_check[record.id] = time.monotonic()
            if not ok:
                logger.warning("Broker %s for tenant %s not usable: %s", broker_name, tenant_id, message)
                return None
        if not token_is_usable(record, now):
            return None
        return build_adapter(record)

    async def _has_open_position(self, session: AsyncSession, dep: StrategyDeploymentRecord) -> bool:
        open_trade = await session.scalar(
            select(TradeRecord.id).where(
                TradeRecord.tenant_id == dep.tenant_id, TradeRecord.exit_time.is_(None),
                TradeRecord.deployment_id == dep.id,
            ).limit(1)
        )
        return open_trade is not None

    async def _square_off_all(
        self, session: AsyncSession, tenant_id: int, market_data: MarketDataService,
        live_broker: Optional[BrokerInterface], user_id: int,
    ) -> int:
        open_trades = list(await session.scalars(
            select(TradeRecord).where(TradeRecord.tenant_id == tenant_id, TradeRecord.exit_time.is_(None))
        ))
        closed = 0
        for trade in open_trades:
            try:
                price = await market_data.get_ltp(trade.symbol, exchange_for_symbol(trade.symbol))
            except Exception as exc:  # noqa: BLE001
                logger.warning("Square-off: no price for %s (%s) - left open", trade.symbol, exc)
                continue
            broker = live_broker if trade.mode == ExecutionMode.LIVE.value else None
            outcome = await close_position(session, trade, price, "Intraday square-off", broker=broker, user_id=user_id)
            closed += int(outcome.closed)
        if closed:
            logger.info("Square-off closed %d position(s) for tenant %s", closed, tenant_id)
        return closed

    async def _record_failure(self, session: AsyncSession, dep: StrategyDeploymentRecord, exc: Exception, user_id: int) -> None:
        logger.exception("Deployment %s evaluation failed", dep.id)
        dep.consecutive_failures = (dep.consecutive_failures or 0) + 1
        dep.last_error = f"{type(exc).__name__}: {exc}"[:500]
        dep.last_evaluated_at = datetime.now(timezone.utc)
        if dep.consecutive_failures >= MAX_CONSECUTIVE_FAILURES and dep.status == DeploymentStatus.ACTIVE.value:
            dep.status = DeploymentStatus.PAUSED.value
            dep.pause_reason = f"Auto-paused after {dep.consecutive_failures} consecutive failures: {dep.last_error}"
            await session.commit()
            await notify(
                session, dep.tenant_id, NotificationType.SYSTEM_FAILURE,
                title=f"Deployment paused: {dep.strategy_id} on {dep.symbol}", message=dep.pause_reason,
                severity=NotificationSeverity.CRITICAL, user_id=user_id,
            )
            return
        await session.commit()

    async def _heartbeat(self, session: AsyncSession, report: CycleReport, cycle_ms: int) -> None:
        record = await session.scalar(select(WorkerHeartbeatRecord).where(WorkerHeartbeatRecord.worker_name == self.worker_name))
        if record is None:
            record = WorkerHeartbeatRecord(worker_name=self.worker_name)
            session.add(record)
        record.last_seen_at = datetime.now(timezone.utc)
        record.cycle_count = (record.cycle_count or 0) + 1
        record.last_cycle_ms = cycle_ms
        record.last_error = "; ".join(report.errors)[:2000] if report.errors else None
        await session.commit()


async def main() -> None:
    configure_logging()
    from app.db.session import _session_factory  # the app's own engine/pool configuration

    worker = TradingWorker(_session_factory)
    loop = asyncio.get_running_loop()
    try:
        import signal
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, worker.stop)
    except (NotImplementedError, ImportError):  # Windows / restricted environments
        pass
    await worker.run_forever()


if __name__ == "__main__":
    asyncio.run(main())
