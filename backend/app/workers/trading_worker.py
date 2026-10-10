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
from datetime import datetime, time as dtime, timedelta, timezone
from typing import Callable, Dict, List, Optional, Tuple

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.brokers.base import BrokerInterface
from app.alerts.dispatcher import dispatch_pending
from app.brokers.rate_budget import RateBudget, RateLimitedBroker, limits_for
from app.execution.ops_throttle import OpsThrottle
from app.compliance.static_ip import live_entry_problem
from app.brokers.token_lifecycle import build_adapter, get_credential_record, token_is_usable, verify_token
from app.cache.client import cache_release_lock, cache_renew_lock, cache_try_lock
from app.core import config as app_config
from app.core.config import ENVIRONMENT, HARDENED_ENVIRONMENTS, WORKER_CYCLE_SECONDS
from app.core.enums import DeploymentStatus, ExecutionMode, InstrumentKind, NotificationSeverity, NotificationType, OptionStrategy, SignalDirection
from app.core.logging_config import bind_log_context, configure_logging
from app.custom_strategies.resolver import resolve_strategy
from app.db.models import StrategyDeploymentRecord, Tenant, TradeRecord, User, WorkerHeartbeatRecord
from app.plans.limits import live_allowed, tenant_is_active
from app.retention.service import RetentionReport, run_retention
from app.billing.service import sweep as billing_sweep
from app.ai import monitor as ai_monitor
from app.ai import thesis as thesis_module
from app.brokers import login_reminder
from app.workers import eod_summary
from app.news_feed import service as news_feed
from app.platform import controls as platform_controls
from app.ai.regime import classify_regime, parse_filter, regime_blocks
from app.observability.metrics import RETENTION_DELETED, observe_cycle
from app.core.config import INSTRUMENT_SYNC_EXCHANGES, INSTRUMENT_SYNC_HOUR_IST
from app.instruments import master
from app.instruments.master import sync_upstox
from app.backtest import chain_recorder
from app.instruments.contracts import ContractResolutionError, ContractRules, resolve_contract
from app.instruments.spreads import parse_custom_legs, resolve_structure
from app.execution.multileg import execute_structure
from app.execution.signal_execution import execute_signal_for_user
from app.market_data.calendar import IST, all_session_statuses, intraday_cutoffs, session_family
from app.market_data.freshness import candle_staleness
from app.market_data.service import MarketDataService
from app.market_data.stream import StreamManager
from app.market_lake.recorder import LakeRecorder
from app.observability.metrics import MARKET_DATA_STALE
from app.reconciliation.service import broker_uncertain_reason, reconcile_accounts, run_reconciliation
from app.secrets_store.envelope import ensure_tenant_key
from app.accounts.routing import ACCOUNT_REFRESH_SECONDS, RoutingPolicy, choose_account, policy_for
from app.accounts.service import default_account, get_account, list_accounts, routing_for_deployment, sync_account
from app.notifications.service import notify
from app.trading.position_monitor import close_position, exchange_for_trade, monitor_open_positions
from app.trading.stop_guard import verify_protective_stops

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
# Fairness: no tenant may consume more than this share of a cycle evaluating entries. Deployments
# that don't get their turn are picked up first next cycle (round-robin), so a tenant with many
# deployments is slowed, never starved - and never starves the others.
MAX_TENANT_SHARE_OF_CYCLE = 0.5


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
    # P0.1 / S2: Redis was unreachable, so this cycle ran without the replica lock (LIVE entries paused
    # where a second replica is possible).
    lock_degraded: bool = False
    retention: Optional[RetentionReport] = None
    chain_rows_recorded: int = 0   # Phase W
    billing: Optional[Dict[str, int]] = None
    ai_proposals: int = 0
    master_synced: Optional[Dict[str, int]] = None
    stale_skips: int = 0
    reconciled: int = 0
    open_exchanges: List[str] = field(default_factory=list)
    stops_rearmed: int = 0
    streams_connected: int = 0   # Phase S: websocket quote streams currently connected
    memory_snapshots: int = 0    # Phase AR: market-memory rows written this cycle
    lake_bars: int = 0           # B2: 1-minute bars written to the lake this cycle
    login_reminders: int = 0     # D5: pre-open daily-login reminders raised this cycle
    eod_summaries: int = 0       # Phase AX: end-of-day summary notifications raised this cycle
    news_items: int = 0          # Phase BB: new feed items stored this cycle
    news_classified: int = 0     # Phase BB: items classified with organisations' own provider keys


def _just_closed(now: datetime) -> bool:
    """The hour after the NSE close on a weekday (the market memory's final read of the day)."""
    ist = now.astimezone(IST)
    return ist.weekday() < 5 and dtime(15, 30) <= ist.time() < dtime(16, 30)


def _pre_open(now: datetime) -> bool:
    """08:00-09:15 IST on a weekday: the overnight global cues are what a trader reads before the open."""
    ist = now.astimezone(IST)
    return ist.weekday() < 5 and dtime(8, 0) <= ist.time() < dtime(9, 15)


def _adapter_key(broker_name: str, account_label: str) -> str:
    return broker_name if (account_label or "primary") == "primary" else f"{broker_name}@{account_label}"


def _chain_provider(broker: BrokerInterface, cache: Optional[Dict[str, object]] = None):
    """Phase H1: the option chain the strike filters are judged against - the tenant's own
    broker session, fetched only when a deployment actually carries filters. Phase W: the chain
    is kept in `cache` (per cycle) so the recorder reuses it instead of fetching again."""
    async def provider(underlying_symbol: str, expiry):
        chain = await broker.get_option_chain(underlying_symbol, expiry)
        if cache is not None:
            cache[underlying_symbol] = chain
        return chain
    return provider


class TradingWorker:
    def __init__(
        self, session_factory: async_sessionmaker, *, cycle_seconds: int = WORKER_CYCLE_SECONDS,
        market_data_factory: Callable[[BrokerInterface], MarketDataService] = MarketDataService,
        worker_name: str = WORKER_NAME, max_seconds_per_tenant: Optional[float] = None, require_lock_for_live: Optional[bool] = None,
    ) -> None:
        self.session_factory = session_factory
        self.cycle_seconds = cycle_seconds
        # P0.1 / S2: in production/staging a LIVE entry needs the replica lock to be real (Redis reachable);
        # a single dev/test replica may trade LIVE without Redis as before.
        self.require_lock_for_live = ENVIRONMENT in HARDENED_ENVIRONMENTS if require_lock_for_live is None else require_lock_for_live
        self._lock_degraded = False
        self._lock_ttl = max(cycle_seconds * 2, 30)
        self.market_data_factory = market_data_factory
        self.worker_name = worker_name
        self.holder_id = f"{socket.gethostname()}:{uuid.uuid4().hex[:8]}"
        self._stop = asyncio.Event()
        self._last_token_check: Dict[int, float] = {}
        # IST calendar date of the last retention run (Phase D3) - once a day is plenty.
        self._last_retention_day = None
        # Phase W: last option-chain capture per underlying (UTC), so a chain is fetched once per interval.
        self._last_chain_capture: Dict[str, datetime] = {}
        self._cycle_chains: Dict[str, object] = {}   # chains fetched this tenant cycle, by underlying symbol
        # IST calendar date of the last billing lifecycle sweep (Phase K1): trials, dues, grace.
        self._last_billing_day = None
        # Phase AX: IST date of the last end-of-day summary (one per organisation per day).
        self._last_eod_summary_day = None
        self._last_thesis_report_day = None
        self._last_login_reminder_day = None   # D5: the pre-open daily-login reminder, once per IST day
        # Phase BB: when the news feed was last fetched (cadence 15 min, 5 min around a macro event).
        self._last_news_fetch: Optional[datetime] = None
        # Phase L: last regime per deployment (for the monitoring agent) and which deployments the
        # agent wants classified even without a filter (those with open positions).
        self._regimes: Dict[int, str] = {}
        self._regime_wanted: set = set()
        self._last_ai_expiry_day = None
        # IST date of the last instrument-master sync (Phase F1): once a day, pre-market.
        self._last_master_sync_day = None
        # One API rate budget per (tenant, broker): tenants use their own API keys, so their
        # broker limits are their own too (app/brokers/rate_budget.py).
        self._budgets: Dict[Tuple[int, str], RateBudget] = {}
        self._ops: Dict[Tuple[int, str], OpsThrottle] = {}
        self._tenant_cursor: Dict[int, int] = {}
        self._stale_skips = 0
        # Phase S: one websocket quote stream per broker session, subscribed each cycle to the
        # symbols the tenant's deployments and open positions need (STREAMING_QUOTES_ENABLED).
        self.streams = StreamManager()
        # Part B2: ticks from those streams also become lake bars (LAKE_TICK_WRITER_ENABLED, off by default).
        self.lake = None
        if app_config.LAKE_TICK_WRITER_ENABLED:
            from app.market_data import stream as stream_module
            self.lake = LakeRecorder()
            stream_module.tick_listeners.append(self.lake.on_tick)
        # Phase T: last balance refresh attempt per broker account (failures throttled too).
        self._last_account_refresh: Dict[int, datetime] = {}
        # Phase AR: last market-memory capture per tenant (UTC).
        self._last_memory: Dict[int, datetime] = {}
        self.max_seconds_per_tenant = (
            max_seconds_per_tenant if max_seconds_per_tenant is not None else cycle_seconds * MAX_TENANT_SHARE_OF_CYCLE
        )

    # --- lifecycle --------------------------------------------------------------------------

    def stop(self) -> None:
        self._stop.set()

    async def run_forever(self) -> None:
        logger.info("Trading worker %s starting (cycle %ss)", self.holder_id, self.cycle_seconds)
        # Safety rule 18: before the first cycle, every tenant with an open LIVE position is
        # reconciled against its broker. Whatever happened while this process was down (a fill
        # after a crash, a manual exit at the broker) is known before anything new is traded.
        try:
            await self.reconcile_on_start()
        except Exception:  # noqa: BLE001 - never prevents the worker from starting
            logger.exception("Start-up reconciliation failed")
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
        await self.streams.stop_all()
        logger.info("Trading worker %s stopped", self.holder_id)

    # --- one cycle ---------------------------------------------------------------------------

    async def run_cycle(self, now: Optional[datetime] = None) -> CycleReport:
        now = now or datetime.now(timezone.utc)
        cycle_started = time.monotonic()
        lock_ttl = self._lock_ttl = max(self.cycle_seconds * 2, 30)
        acquired, reachable = await cache_try_lock(LOCK_KEY, self.holder_id, lock_ttl)
        if not acquired:
            logger.info("Another worker replica holds the lock - skipping this cycle")
            return CycleReport(started_at=now, market_open=False, session_reason="lock held elsewhere", skipped_lock=True)
        if self._lock_degraded != (not reachable):
            logger.warning("Redis %s - the replica lock is %s", "unreachable" if not reachable else "back", "off (LIVE entries paused where required)" if not reachable else "on again")
        self._lock_degraded = not reachable

        try:
            async with self.session_factory() as session:
                # Phase O2: one clock per exchange family. NSE is always considered; MCX/CRYPTO only
                # when a deployment actually trades there, so a pure-equity platform behaves as before.
                wanted = {"NSE"} | {session_family(ex) for ex in await session.scalars(
                    select(StrategyDeploymentRecord.exchange).where(
                        StrategyDeploymentRecord.status.in_([DeploymentStatus.ACTIVE.value, DeploymentStatus.PAUSED.value])).distinct())}
                statuses = {name: st for name, st in (await all_session_statuses(session, now)).items() if name in wanted}
                self._open_families = {name for name, st in statuses.items() if st.is_open}
                nse = statuses["NSE"]
                reason = nse.reason if wanted == {"NSE"} else "; ".join(f"{n}: {'open' if st.is_open else 'closed'}" for n, st in sorted(statuses.items()))
                report = CycleReport(started_at=now, market_open=bool(self._open_families), session_reason=reason, open_exchanges=sorted(self._open_families),
                                     lock_degraded=self._lock_degraded)
                if self._open_families:
                    self._stale_skips = 0
                    await self._process_tenants(session, now, report)
                    report.stale_skips = self._stale_skips
                    # P0.1 / S2: a long evaluation phase must not let the lock lapse under the housekeeping below.
                    if not self._lock_degraded and not await cache_renew_lock(LOCK_KEY, self.holder_id, self._lock_ttl):
                        logger.warning("Replica lock could not be renewed - another replica may now hold it")
                else:
                    logger.debug("Market closed: %s", reason)
                if self.lake is not None:
                    try:
                        report.lake_bars = (await self.lake.drain(session, now)).inserted
                    except Exception as exc:  # noqa: BLE001 - the lake must never break trading
                        logger.exception("Lake bar write failed")
                        report.errors.append(f"lake: {exc}")
                        await session.rollback()
                # Out-of-app alert delivery (Telegram/email) rides on this loop, market open or not:
                # a TOKEN_EXPIRED raised at 03:31 must reach a phone before 09:15.
                try:
                    await dispatch_pending(session)
                except Exception as exc:  # noqa: BLE001 - alerting must never break trading
                    logger.exception("Alert dispatch failed")
                    report.errors.append(f"alert dispatch: {exc}")
                # Phase AR: the Copilot's market memory - every 15 minutes while NSE is open and in
                # the hour after the close (the day's final read); before the open only the global
                # cues (no broker read). Never blocks trading.
                if nse.is_open or _just_closed(now) or _pre_open(now):
                    try:
                        report.memory_snapshots = await self._market_memory(session, now, broker_reads=nse.is_open or _just_closed(now))
                    except Exception as exc:  # noqa: BLE001
                        logger.exception("Market memory failed")
                        report.errors.append(f"market memory: {exc}")
                # Phase AX: the end-of-day summary - once per IST weekday from 15:35, one
                # notification per organisation with a deployment or a trade today. Read-only.
                ist_now = now.astimezone(IST)
                if eod_summary.eod_due(now) and self._last_eod_summary_day != ist_now.date():
                    self._last_eod_summary_day = ist_now.date()      # a failure retries tomorrow, not every minute
                    try:
                        report.eod_summaries = await eod_summary.send_all(session, now)
                    except Exception as exc:  # noqa: BLE001 - a report must never break trading
                        logger.exception("EOD summary failed")
                        report.errors.append(f"eod summary: {exc}")
                # D5 (IN-SEBI.login.daily): before the open, remind each organisation whose broker session will not last
                # the day to log in. Read-only; once per IST day (the module also dedupes across restarts).
                if self._last_login_reminder_day != ist_now.date() and login_reminder.in_window(now):
                    self._last_login_reminder_day = ist_now.date()
                    try:
                        report.login_reminders = await login_reminder.send_reminders(session, now)
                    except Exception as exc:  # noqa: BLE001 - a reminder must never break trading
                        logger.exception("Login reminder failed")
                        report.errors.append(f"login reminder: {exc}")
                        await session.rollback()
                # Phase BD-2: the Friday thesis scoreboard (flag market_thesis per tenant, idempotent per ISO week).
                if thesis_module.report_due(now) and self._last_thesis_report_day != ist_now.date():
                    self._last_thesis_report_day = ist_now.date()
                    try:
                        await thesis_module.send_weekly_reports(session, now=now)
                    except Exception as exc:  # noqa: BLE001 - a report must never break trading
                        logger.exception("Thesis weekly report failed")
                        report.errors.append(f"thesis report: {exc}")
                        await session.rollback()
                # Phase BB: the live news feed - one fetch for every organisation (flag `news_feed`,
                # off by default), then each organisation's own AI classification. Never trades.
                try:
                    if await news_feed.enabled(session):
                        cadence = await news_feed.cadence_seconds(session, now)
                        if news_feed.due(self._last_news_fetch, now, cadence):
                            self._last_news_fetch = now
                            result = await news_feed.ingest(session, now)
                            report.news_items = result["new"]
                            report.news_classified = await news_feed.classify_all(session, now)
                except Exception as exc:  # noqa: BLE001 - news must never break trading
                    logger.exception("News feed failed")
                    report.errors.append(f"news feed: {exc}")
                # Instrument master (Phase F1): once per IST day from INSTRUMENT_SYNC_HOUR_IST on,
                # so contracts/expiries/lot sizes are current before the 09:15 open.
                if INSTRUMENT_SYNC_EXCHANGES and self._last_master_sync_day != ist_now.date() and ist_now.hour >= INSTRUMENT_SYNC_HOUR_IST:
                    try:
                        report.master_synced = await sync_upstox(session, INSTRUMENT_SYNC_EXCHANGES)
                        self._last_master_sync_day = ist_now.date()
                        logger.info("Instrument master synced: %s", report.master_synced)
                    except Exception as exc:  # noqa: BLE001 - a failed download must not stop trading
                        logger.exception("Instrument master sync failed")
                        report.errors.append(f"instrument master: {exc}")
                        self._last_master_sync_day = ist_now.date()  # retry tomorrow, not every minute
                # Data retention (Phase D3): once per IST day, outside market hours so it never
                # competes with order flow for the database.
                if not nse.is_open and self._last_retention_day != now.astimezone(IST).date():
                    try:
                        report.retention = await run_retention(session, now)
                        # P0.3 / S7: pin the audit chain's head once a day (cheap; lets verification start from here).
                        try:
                            from app.audit.log import record_anchor
                            if await record_anchor(session, now) is not None:
                                await session.commit()
                        except Exception as exc:  # noqa: BLE001 - an anchor never blocks the cycle
                            await session.rollback()
                            report.errors.append(f"audit anchor: {exc}")
                        self._last_retention_day = now.astimezone(IST).date()
                        if report.retention.total:
                            logger.info("Retention deleted %s", report.retention.deleted)
                            for table, count in report.retention.deleted.items():
                                if count:
                                    RETENTION_DELETED.labels(table=table).inc(count)
                    except Exception as exc:  # noqa: BLE001
                        logger.exception("Retention run failed")
                        report.errors.append(f"retention: {exc}")
                # Billing lifecycle (Phase K1): once per IST day - trial ends, periods past due,
                # grace periods that ran out (tenant suspended), cancellations falling due.
                if self._last_billing_day != now.astimezone(IST).date():
                    try:
                        report.billing = await billing_sweep(session, now)
                        self._last_billing_day = now.astimezone(IST).date()
                        if any(report.billing.values()):
                            logger.info("Billing sweep: %s", report.billing)
                    except Exception as exc:  # noqa: BLE001
                        logger.exception("Billing sweep failed")
                        report.errors.append(f"billing: {exc}")
                        self._last_billing_day = now.astimezone(IST).date()
                await self._heartbeat(session, report, int((time.monotonic() - cycle_started) * 1000))
                return report
        finally:
            await cache_release_lock(LOCK_KEY, self.holder_id)

    async def _market_memory(self, session: AsyncSession, now: datetime, broker_reads: bool = True) -> int:
        """Phase AR: snapshots for every tenant whose traders use the Copilot (a trader profile
        exists), through the tenant's first usable broker session; the global cues need no broker
        (and are all there is before the open, `broker_reads=False`)."""
        from app.ai import market_memory
        from app.db.models import TraderProfileRecord
        written = 0
        for tenant_id in sorted(set(await session.scalars(select(TraderProfileRecord.tenant_id).distinct()))):
            last = self._last_memory.get(tenant_id)
            if last is not None and now - last < timedelta(minutes=market_memory.INTERVAL_MINUTES):
                continue
            self._last_memory[tenant_id] = now
            with bind_log_context(tenant_id=tenant_id):
                try:
                    if not broker_reads:
                        written += (await market_memory.capture_global(session, tenant_id, now=now))["globals"]
                        continue
                    await ensure_tenant_key(session, tenant_id)
                    user = await self._acting_user(session, tenant_id, [])
                    if user is None:
                        written += (await market_memory.capture_global(session, tenant_id, now=now))["globals"]
                        continue
                    broker = None
                    for account in await list_accounts(session, tenant_id):
                        if account.status == "ACTIVE":
                            broker = await self._usable_adapter(session, tenant_id, account.broker_name, user.id, now, account_label=account.account_label)
                            if broker is not None:
                                break
                    if broker is None:
                        written += (await market_memory.capture_global(session, tenant_id, now=now))["globals"]
                        continue
                    result = await market_memory.capture(session, tenant_id, self.market_data_factory, broker, now=now)
                    written += result["symbols"] + result["cues"] + result["globals"]
                    # Phase BC: the deterministic sentiment read rides on the same cadence and broker.
                    news_items: list = []                   # this tenant's own feed items (never a previous tenant's)
                    try:
                        from app.ai import sentiment
                        from app.news_feed import service as news_feed_service
                        memory = await market_memory.latest(session, tenant_id, now=now)
                        news_items = await news_feed_service.items(session, tenant_id, hours=sentiment.NEWS_HOURS, now=now) if await news_feed_service.enabled(session, tenant_id) else []
                        await sentiment.capture(session, tenant_id, broker, memory, now=now, news_items=news_items)
                        written += 1
                    except Exception:  # noqa: BLE001 - sentiment is background, never a blocker
                        logger.exception("Sentiment for tenant %s failed", tenant_id)
                        await session.rollback()        # a failed commit must not poison the next tenant's work
                    # Phase BD-lite: one thesis per watched symbol per day, and yesterday's theses scored (flag per tenant).
                    try:
                        from app.ai import thesis
                        from app.platform.controls import flag_enabled
                        if await flag_enabled(session, thesis.FLAG, tenant_id):
                            memory = await market_memory.latest(session, tenant_id, now=now)
                            written += await thesis.capture_daily(session, tenant_id, memory, now=now, news_items=news_items)
                            await thesis.score_due(session, tenant_id, now=now)
                    except Exception:  # noqa: BLE001 - the thesis is background, never a blocker
                        logger.exception("Thesis for tenant %s failed", tenant_id)
                        await session.rollback()
                except Exception:  # noqa: BLE001 - one tenant's memory must not stop the others
                    logger.exception("Market memory for tenant %s failed", tenant_id)
                    await session.rollback()
        return written

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
            # P0.1 / S2: the evaluation phase is the long one; keep the replica lock alive per tenant.
            if not self._lock_degraded and not await cache_renew_lock(LOCK_KEY, self.holder_id, self._lock_ttl):
                logger.warning("Replica lock could not be renewed before tenant %s - another replica may now hold it", tenant_id)
            with bind_log_context(tenant_id=tenant_id):
                try:
                    await ensure_tenant_key(session, tenant_id)  # Phase N1: credentials decrypt under the tenant key
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

        # One adapter per (broker, account) whose token is proven - every ACTIVE account, not only
        # the ones a deployment names: a routing policy may pick any of them, an open position may
        # sit in any of them, and each one's balance is refreshed through its own session. Keys are
        # "<broker>" for the primary account and "<broker>@<label>" otherwise (Phase I2);
        # `_adapter_key` maps a deployment to its key.
        adapters: Dict[str, BrokerInterface] = {}
        accounts = await list_accounts(session, tenant_id)   # creates rows for pre-I2 credentials
        for account in accounts:
            if account.status != "ACTIVE":
                continue
            adapter = await self._usable_adapter(session, tenant_id, account.broker_name, user.id, now, account_label=account.account_label)
            if adapter is not None:
                adapters[_adapter_key(account.broker_name, account.account_label)] = adapter

        data_broker = next(iter(adapters.values()), None)
        if data_broker is None:
            message = "No usable broker session (token expired or missing) - log in again from Settings"
            for dep in deployments:
                dep.last_error = message
            await session.commit()
            logger.warning("Tenant %s: %s", tenant_id, message)
            return
        market_data = self.market_data_factory(data_broker)
        self._cycle_chains = {}
        # Phase T: balances first, so a capital policy decides on this cycle's numbers.
        await self._refresh_accounts(session, accounts, adapters, now)

        routes: Dict[int, tuple] = {}   # deployment id -> (account, label)
        tenant_for_routing = await session.get(Tenant, tenant_id)
        for dep in deployments:
            if not dep.broker_name:
                continue
            policy = policy_for(dep.routing_policy, getattr(tenant_for_routing, "default_routing_policy", None))
            if dep.mode == ExecutionMode.LIVE.value and policy != RoutingPolicy.EXPLICIT:
                # Phase T: rule-based choice at signal time from the accounts' current state.
                account, label = await self._route_by_policy(session, dep, policy, accounts, now)
            else:
                account, label = await routing_for_deployment(session, tenant_id, dep.broker_name, dep.broker_account_id)
                if dep.mode == ExecutionMode.LIVE.value:
                    dep.last_route = (f"account #{account.id} ({account.broker_name}/{account.account_label}) by EXPLICIT"
                                      if account is not None else "primary credential by EXPLICIT")
            routes[dep.id] = (account, label)
        self._routes = routes
        if app_config.STREAMING_QUOTES_ENABLED:
            await self._stream_quotes(session, tenant_id, adapters, data_broker, deployments, report)

        # Exits before entries.
        now_ist = now.astimezone(IST)
        live_keys = {_adapter_key(d.broker_name, routes.get(d.id, (None, "primary"))[1]) for d in deployments if d.mode == "LIVE" and d.broker_name}
        live_broker = next((a for n, a in adapters.items() if n in live_keys), None)
        live_broker_name = next((n.split("@")[0] for n, a in adapters.items() if a is live_broker), None)
        # Phase T: a tenant with several broker accounts has every exit resolved from the trade's
        # own account (position_monitor.broker_for_trade, cached per cycle) instead of funnelled
        # through the first LIVE session found. One account: that session, as before.
        exit_broker = live_broker if len(accounts) <= 1 else None
        session.info.pop("trade_brokers", None)

        # Phase G1: while the tenant is flagged "broker uncertain", reconcile every cycle so the
        # LIVE block lifts on its own the moment the books agree (and stays while they do not).
        # Phase AL: every account against its own session, settled together - a position in one
        # account is never "missing" because another account was asked.
        tenant_row = await session.get(Tenant, tenant_id)
        if tenant_row is not None and tenant_row.broker_uncertain_since is not None:
            pairs = [(a, adapters[_adapter_key(a.broker_name, a.account_label)]) for a in accounts
                     if a.status == "ACTIVE" and _adapter_key(a.broker_name, a.account_label) in adapters]
            if pairs:
                reports = await reconcile_accounts(session, tenant_row, pairs, user_id=user.id, source="worker")
                report.reconciled += 1 if reports else 0
            elif live_broker is not None:
                try:
                    await run_reconciliation(session, tenant_row, live_broker_name or live_broker.name, live_broker,
                                             user_id=user.id, source="worker")
                    report.reconciled += 1
                except Exception as exc:  # noqa: BLE001 - flagged and audited by the service
                    logger.warning("Tenant %s: reconciliation while uncertain failed: %s", tenant_id, exc)
        open_families = getattr(self, "_open_families", None) or {"NSE"}
        # Phase O2: each venue squares off on its own clock (NSE 15:15, MCX 23:15, crypto never);
        # positions on venues still inside their session are monitored as before.
        due_square_off = {f for f in open_families if (cut := intraday_cutoffs(f)[1]) is not None and now_ist.time() >= cut}
        if due_square_off:
            report.positions_closed += await self._square_off_all(session, tenant_id, market_data, exit_broker, user.id, families=due_square_off)
        monitored = open_families - due_square_off
        if monitored:
            outcomes = await monitor_open_positions(session, tenant_id, market_data.get_ltp, broker=exit_broker, user_id=user.id, families=monitored)
            report.positions_closed += sum(1 for o in outcomes if o.closed)
        # Phase P1: every open LIVE position keeps a standing broker-side stop, whatever happened to it.
        # Phase T: checked per account, against that account's own order book.
        if tenant_row is not None:
            try:
                report.stops_rearmed += await self._guard_stops(session, tenant_row, accounts, adapters, user.id)
            except Exception as exc:  # noqa: BLE001 - advisory safety net; never stops the cycle
                logger.warning("Tenant %s: stop guard failed: %s", tenant_id, exc)
        tenant_started = time.monotonic()
        tenant = await session.get(Tenant, tenant_id)
        if tenant is not None and not tenant_is_active(tenant):
            # Exits above still ran (a suspended org's open risk is still real); no new entries.
            for dep in deployments:
                dep.last_error = f"Organisation is {tenant.status}: no new entries"
            await session.commit()
            return
        active = [d for d in deployments if d.status == DeploymentStatus.ACTIVE.value]
        # Round-robin start so a tenant whose turn ran out of time last cycle resumes where it
        # stopped rather than re-evaluating the same first few deployments forever.
        start = self._tenant_cursor.get(tenant_id, 0) % max(1, len(active))
        ordered = active[start:] + active[:start]
        evaluated = 0
        for dep in ordered:
            if evaluated > 0 and (time.monotonic() - tenant_started) > self.max_seconds_per_tenant:
                logger.warning(
                    "Tenant %s used its cycle time budget after %d/%d deployments - the rest go first next cycle",
                    tenant_id, evaluated, len(ordered),
                )
                break
            evaluated += 1
            family = session_family(dep.exchange)
            if family not in open_families:
                continue  # Phase O2: this venue is closed right now; nothing to evaluate
            if dep.mode == ExecutionMode.LIVE.value and not live_allowed(tenant):
                dep.last_error = "Plan does not include live trading - LIVE entries skipped"
                await session.commit()
                continue
            if dep.mode == ExecutionMode.LIVE.value:
                ip_problem = await live_entry_problem(session, tenant_id, dep.broker_name)   # Part D4 (flag-gated)
                if ip_problem:
                    dep.last_error = ip_problem
                    await session.commit()
                    continue
            if dep.mode == ExecutionMode.LIVE.value and self.require_lock_for_live and self._lock_degraded:
                dep.last_error = "Redis replica lock unavailable - LIVE entries paused this cycle (a second worker could double-trade); exits continue"
                await session.commit()
                continue
            no_new_after = intraday_cutoffs(family)[0]
            entries_allowed = no_new_after is None or now_ist.time() < no_new_after
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
        self._tenant_cursor[tenant_id] = (start + evaluated) % max(1, len(active))

        # Phase W: sample the option chains of this tenant's ACTIVE option deployments for later
        # backtests - one fetch per underlying per interval, platform-wide, never on a closed venue.
        try:
            report.chain_rows_recorded += await self._record_chains(session, active, market_data, now, open_families)
        except Exception as exc:  # noqa: BLE001 - reference data; never stops the cycle
            logger.warning("Tenant %s: option-chain recording failed: %s", tenant_id, exc)

        # Phase L4: the monitoring agent observes this tenant's deployments and raises proposals
        # for a human to decide on. It never acts on its own.
        try:
            open_dep_ids = set(await session.scalars(select(TradeRecord.deployment_id).where(
                TradeRecord.tenant_id == tenant_id, TradeRecord.exit_time.is_(None), TradeRecord.deployment_id.is_not(None))))
            self._regime_wanted = open_dep_ids
            proposals = await ai_monitor.observe(session, tenant_id, deployments, now, regime_lookup=lambda d: self._regimes.get(d.id))
            if proposals:
                created = await ai_monitor.raise_proposals(session, tenant_id, proposals, now)
                report.ai_proposals += len(created)
            if self._last_ai_expiry_day != now.astimezone(IST).date():
                await ai_monitor.expire_stale(session, now)
                self._last_ai_expiry_day = now.astimezone(IST).date()
        except Exception as exc:  # noqa: BLE001 - advisory layer; never stops trading
            logger.exception("Monitoring agent failed for tenant %s", tenant_id)
            report.errors.append(f"ai monitor: {exc}")

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

        # Phase G1 (safety rule 7): no signal on a candle feed that has fallen behind the clock.
        base = frames.get(dep.timeframe)
        if base is None:
            base = next(iter(frames.values()))
        last_bar = base.index[-1].to_pydatetime() if len(base) else None
        stale = candle_staleness(last_bar, dep.timeframe, now)
        if stale is not None:
            dep.last_error = f"Skipped: {stale}"
            await session.commit()
            MARKET_DATA_STALE.labels(kind="candles").inc()
            self._stale_skips += 1
            logger.warning("Deployment %s: %s", dep.id, stale)
            return False

        # Phase L3: the regime filter - a deployment that only trades trends sits out ranges, and
        # says so. Classified on the base frame the strategy is about to read.
        allowed = parse_filter(dep.regime_filter)
        regime = classify_regime(base) if (allowed or dep.id in self._regime_wanted or len(base) >= 60) else None
        if regime is not None:
            self._regimes[dep.id] = regime.kind
        if allowed:
            blocked = regime_blocks(regime, allowed)
            if blocked:
                dep.last_error = blocked
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
        if (getattr(dep, "holding", None) or "INTRADAY") == "SWING" and (dep.instrument_kind or "UNDERLYING") == "UNDERLYING" \
                and signal.direction == SignalDirection.SHORT:
            # Phase AS: delivery (CNC) cannot be sold short and carried overnight.
            dep.last_signal_at = signal_ts
            dep.last_error = (f"Swing SHORT signal at {signal_ts.isoformat()} skipped: cash equity cannot be held short overnight "
                              f"(deploy on futures or options to take swing shorts)")
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

        contract = None
        rules = ContractRules.from_deployment(dep)
        structure_kind = OptionStrategy(dep.option_strategy or "SINGLE")
        if rules.kind == InstrumentKind.OPTION and structure_kind != OptionStrategy.SINGLE:
            # Phase H2: a multi-leg structure - resolved and executed as one position.
            return await self._evaluate_structure(session, dep, user, market_data, adapters, now, signal, signal_ts, rules, structure_kind, frames)
        if rules.derived:
            # Phase F3: pick the option/future from the master at signal time, off the latest
            # underlying close. A rule that cannot be satisfied is recorded, never traded around.
            base = frames.get(dep.timeframe)
            if base is None:
                base = next(iter(frames.values()))
            spot = float(base["close"].iloc[-1]) if len(base) else None
            try:
                contract = await resolve_contract(
                    session, dep.symbol, rules, signal.direction, spot=spot, today=now.astimezone(IST).date(),
                    chain_provider=_chain_provider(market_data.broker, self._cycle_chains),
                )
            except ContractResolutionError as exc:
                dep.last_error = f"Contract not resolved: {exc}"
                await session.commit()
                logger.warning("Deployment %s: %s", dep.id, exc)
                return False

        broker, account_id, skip = await self._live_broker_for(session, dep, adapters)
        if skip:
            dep.last_error = skip
            await session.commit()
            logger.warning("Deployment %s LIVE entry skipped: %s", dep.id, skip)
            return False

        idempotency_key = f"deployment:{dep.id}:{signal_ts.isoformat()}"
        result, order = await execute_signal_for_user(
            session, user, mode=dep.mode, strategy_id=dep.strategy_id, signal=signal,
            idempotency_key=idempotency_key, broker=broker, deployment_id=dep.id,
            contract=contract, rules=rules if contract is not None else None, quote_broker=market_data.broker,
            account_id=account_id, exit_rules=dep.exit_rules, holding=getattr(dep, "holding", None) or "INTRADAY",
            order_style=getattr(dep, "order_style", None) or "MARKET", market_protection_pct=getattr(dep, "market_protection_pct", None),
        )
        dep.last_signal_at = signal_ts
        dep.last_error = None if result.executed else "; ".join(result.reasons)[:500]
        dep.consecutive_failures = 0
        # Phase M / V4.14: stamp the regime read at entry on the trade for the journal.
        if result.executed and order.trade_id is not None and dep.id in self._regimes:
            trade = await session.get(TradeRecord, order.trade_id)
            if trade is not None and trade.regime_at_entry is None:
                trade.regime_at_entry = self._regimes[dep.id]
        await session.commit()
        logger.info("Deployment %s signal %s -> executed=%s order=%s", dep.id, signal.direction.value, result.executed, order.id)
        return result.executed

    async def _evaluate_structure(
        self, session: AsyncSession, dep: StrategyDeploymentRecord, user: User, market_data: MarketDataService,
        adapters: Dict[str, BrokerInterface], now: datetime, signal, signal_ts, rules: ContractRules,
        structure_kind: OptionStrategy, frames,
    ) -> bool:
        base = frames.get(dep.timeframe)
        if base is None:
            base = next(iter(frames.values()))
        spot = float(base["close"].iloc[-1]) if len(base) else None
        try:
            structure = await resolve_structure(
                session, dep.symbol, rules, structure_kind, signal.direction, spread_width=dep.spread_width or 2,
                spot=spot, today=now.astimezone(IST).date(), chain_provider=_chain_provider(market_data.broker, self._cycle_chains),
                custom_legs=parse_custom_legs(getattr(dep, "custom_legs", None)),
            )
        except (ContractResolutionError, ValueError) as exc:
            dep.last_error = f"Structure not built: {exc}"
            dep.last_signal_at = signal_ts if "not entered on" in str(exc) else dep.last_signal_at
            await session.commit()
            logger.warning("Deployment %s: %s", dep.id, exc)
            return False
        broker, account_id, skip = await self._live_broker_for(session, dep, adapters)
        if skip:
            dep.last_error = skip
            await session.commit()
            return False
        result = await execute_structure(
            session, user, mode=dep.mode, strategy_id=dep.strategy_id, signal=signal, structure=structure, rules=rules,
            target_credit_pct=dep.target_credit_pct, stop_credit_pct=dep.stop_credit_pct,
            idempotency_key=f"deployment:{dep.id}:{signal_ts.isoformat()}", broker=broker, quote_broker=market_data.broker,
            deployment_id=dep.id, account_id=account_id,
        )
        dep.last_signal_at = signal_ts
        dep.last_error = None if result.executed else "; ".join(result.reasons)[:500]
        dep.consecutive_failures = 0
        await session.commit()
        logger.info("Deployment %s %s -> executed=%s", dep.id, structure_kind.value, result.executed)
        return result.executed

    async def _live_broker_for(self, session: AsyncSession, dep: StrategyDeploymentRecord, adapters: Dict[str, BrokerInterface]):
        """(broker adapter, account id, skip reason) for a deployment's entry. PAPER: no broker.
        LIVE: the adapter of the routed account, refused when the account is DISABLED, the
        session is not usable, or the tenant is broker-uncertain (safety rule 8)."""
        account, label = getattr(self, "_routes", {}).get(dep.id, (None, "primary"))
        account_id = account.id if account is not None else None
        if dep.mode != ExecutionMode.LIVE.value:
            return None, account_id, None
        if account is not None and account.status != "ACTIVE":
            return None, account_id, f"LIVE entry skipped: broker account #{account.id} ({account.broker_name}/{account.account_label}) is {account.status}"
        # Phase M / V4.13: the operator can switch a broker off platform-wide (exits still run).
        if (dep.broker_name or "").lower() in (await platform_controls.status(session))["disabled_brokers"]:
            return None, account_id, f"LIVE entry skipped: broker {dep.broker_name} is disabled by the platform operator"
        routed_broker = account.broker_name if account is not None else (dep.broker_name or "")
        broker = adapters.get(_adapter_key(routed_broker, label))
        if broker is None:
            return None, account_id, f"LIVE entry skipped: no usable {routed_broker} session - log in again from Settings"
        uncertain = broker_uncertain_reason(await session.get(Tenant, dep.tenant_id))
        if uncertain:
            # execute_signal_for_user would refuse this too (as a REJECTED order per signal
            # bar); the worker stops one step earlier and says why.
            return None, account_id, f"LIVE entry skipped: {uncertain}"
        return broker, account_id, None

    async def reconcile_on_start(self) -> int:
        """Reconcile every tenant holding an open LIVE trade against its broker accounts before
        the first cycle (safety rule 18). Phase AL: each account against its own session, the
        tenant flag settled on the joint result; then each account's protective stops are checked
        in that account's order book. A mismatch flags the tenant (LIVE entries blocked) and raises
        a CRITICAL notification; a clean run clears any stale flag. Returns accounts checked."""
        checked = 0
        now = datetime.now(timezone.utc)
        async with self.session_factory() as session:
            tenant_ids = sorted({row for row in await session.scalars(
                select(TradeRecord.tenant_id).where(TradeRecord.exit_time.is_(None), TradeRecord.mode == ExecutionMode.LIVE.value).distinct()
            )})
            for tenant_id in tenant_ids:
                tenant = await session.get(Tenant, tenant_id)
                if tenant is None:
                    continue
                user = await self._acting_user(session, tenant_id, [])
                accounts = [a for a in await list_accounts(session, tenant_id) if a.status == "ACTIVE"]
                if user is None or not accounts:
                    logger.warning("Start-up reconciliation: tenant %s has open LIVE trades but no broker account/user to check with", tenant_id)
                    continue
                pairs = []
                for account in accounts:
                    adapter = await self._usable_adapter(session, tenant_id, account.broker_name, user.id, now, account_label=account.account_label)
                    if adapter is None:
                        logger.warning("Start-up reconciliation: tenant %s has open LIVE trades but no usable %s/%s session",
                                       tenant_id, account.broker_name, account.account_label)
                        continue
                    pairs.append((account, adapter))
                if not pairs:
                    continue
                reports = await reconcile_accounts(session, tenant, pairs, user_id=user.id, source="startup")
                for r in reports:
                    logger.info("Start-up reconciliation: tenant %s %s/%s -> %d mismatch(es)", tenant_id, r.broker_name, r.account_label, r.mismatched_count)
                for account, adapter in pairs:
                    try:
                        guard = await verify_protective_stops(session, tenant, adapter, user_id=user.id, source="startup", account_id=account.id,
                                                              include_unassigned=bool(account.is_default) or len(accounts) == 1)
                        if guard["rearmed"] or guard["failed"]:
                            logger.warning("Start-up stop guard: tenant %s %s/%s -> %s", tenant_id, account.broker_name, account.account_label, guard)
                    except Exception as exc:  # noqa: BLE001
                        logger.error("Start-up stop guard: tenant %s %s/%s failed: %s", tenant_id, account.broker_name, account.account_label, exc)
                    checked += 1
        return checked

    # --- helpers -----------------------------------------------------------------------------

    async def _acting_user(
        self, session: AsyncSession, tenant_id: int, deployments: List[StrategyDeploymentRecord],
    ) -> Optional[User]:
        creator_ids = [d.created_by for d in deployments if d.created_by is not None]
        if creator_ids:
            user = await session.get(User, creator_ids[0])
            if user is not None and user.tenant_id == tenant_id and user.is_active:
                return user
        owner = await session.scalar(
            select(User).where(User.tenant_id == tenant_id, User.is_active.is_(True), User.role == "OWNER")
            .order_by(User.id).limit(1)
        )
        if owner is not None:
            return owner
        return await session.scalar(
            select(User).where(User.tenant_id == tenant_id, User.is_active.is_(True)).order_by(User.id).limit(1)
        )

    async def _usable_adapter(
        self, session: AsyncSession, tenant_id: int, broker_name: str, user_id: int, now: datetime, account_label: str = "primary",
    ) -> Optional[BrokerInterface]:
        record = await get_credential_record(session, tenant_id, broker_name, account_label)
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
        return self._rate_limited(tenant_id, f"{broker_name}@{account_label}", build_adapter(record))

    def _rate_limited(self, tenant_id: int, budget_key: str, adapter: BrokerInterface) -> BrokerInterface:
        key = (tenant_id, budget_key)
        budget = self._budgets.get(key)
        if budget is None:
            budget = self._budgets[key] = RateBudget(limits_for(budget_key.split("@")[0]))
        ops = None
        if app_config.OPS_THROTTLE_ENABLED:      # Part D2: one OPS throttle per (tenant, broker account), kept across cycles
            ops = self._ops.get(key)
            if ops is None:
                ops = self._ops[key] = OpsThrottle(app_config.OPS_PER_SECOND)
        return RateLimitedBroker(adapter, budget, ops=ops)

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
        live_broker: Optional[BrokerInterface], user_id: int, families: Optional[set] = None,
    ) -> int:
        open_trades = list(await session.scalars(
            select(TradeRecord).where(TradeRecord.tenant_id == tenant_id, TradeRecord.exit_time.is_(None))
        ))
        closed = 0
        for trade in open_trades:
            if families is not None and session_family(exchange_for_trade(trade)) not in families:
                continue
            if (getattr(trade, "holding", None) or "INTRADAY") == "SWING":
                continue   # Phase AS: a swing position is held overnight by design
            try:
                price = await market_data.get_ltp(trade.symbol, exchange_for_trade(trade))
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

    async def _guard_stops(self, session: AsyncSession, tenant: Tenant, accounts: List, adapters: Dict[str, BrokerInterface],
                           user_id: int) -> int:
        """Phase P1 per account (Phase T): one session holds one order book, so each account's open
        LIVE trades are checked against its own; trades recorded before accounts were tracked go
        with the default account of their broker. An account without a usable session this cycle
        is skipped - re-arming needs the session the position lives in, never another."""
        rearmed = 0
        for account in accounts:
            adapter = adapters.get(_adapter_key(account.broker_name, account.account_label))
            if adapter is None:
                continue
            guard = await verify_protective_stops(session, tenant, adapter, user_id=user_id, account_id=account.id,
                                                  include_unassigned=bool(account.is_default) or len(accounts) == 1)
            rearmed += guard["rearmed"]
        return rearmed

    async def _route_by_policy(self, session: AsyncSession, dep: StrategyDeploymentRecord, policy: RoutingPolicy, accounts: List,
                               now: datetime):
        """Phase T: pick the account for one LIVE deployment by its policy. Candidates are the
        tenant's accounts at the deployment's broker, or at every broker when the deployment
        routes across brokers. The decision text is stored on the deployment."""
        candidates = [a for a in accounts if dep.route_across_brokers or a.broker_name == dep.broker_name]
        explicit = await get_account(session, dep.tenant_id, dep.broker_account_id) if dep.broker_account_id else None
        default = await default_account(session, dep.tenant_id, dep.broker_name) if dep.broker_name else None
        open_counts: Dict[int, int] = {}
        if policy == RoutingPolicy.FEWEST_POSITIONS:
            rows = await session.execute(
                select(TradeRecord.broker_account_id, func.count()).where(
                    TradeRecord.tenant_id == dep.tenant_id, TradeRecord.exit_time.is_(None), TradeRecord.mode == ExecutionMode.LIVE.value,
                ).group_by(TradeRecord.broker_account_id))
            open_counts = {aid: int(n) for aid, n in rows if aid is not None}
        choice = choose_account(candidates, policy, now=now, open_positions=open_counts, explicit=explicit, default=default)
        dep.last_route = choice.note[:200]
        if choice.fell_back:
            logger.info("Deployment %s: %s", dep.id, choice.note)
        return choice.account, choice.label

    async def _refresh_accounts(self, session: AsyncSession, accounts: List, adapters: Dict[str, BrokerInterface], now: datetime) -> None:
        """Phase T: keep each account's balance/margin fresh enough for the capital policies -
        one `get_balance` per account per ACCOUNT_REFRESH_SECONDS through the session the worker
        already holds. A failed pull is recorded on the row by `sync_account`, never raised."""
        for account in accounts:
            adapter = adapters.get(_adapter_key(account.broker_name, account.account_label))
            if adapter is None or account.status != "ACTIVE":
                continue
            attempted = self._last_account_refresh.get(account.id)
            if attempted is not None and (now - attempted).total_seconds() < ACCOUNT_REFRESH_SECONDS:
                continue
            last = account.last_sync_at
            if last is not None and (now - (last if last.tzinfo else last.replace(tzinfo=timezone.utc))).total_seconds() < ACCOUNT_REFRESH_SECONDS:
                continue
            self._last_account_refresh[account.id] = now
            await sync_account(session, account, adapter)

    async def _record_chains(self, session: AsyncSession, deployments: List[StrategyDeploymentRecord], market_data: MarketDataService,
                             now: datetime, open_families) -> int:
        """Phase W: record the chains of the underlyings this tenant trades as options."""
        if not chain_recorder.recording_enabled():
            return 0
        interval = timedelta(minutes=chain_recorder.CHAIN_SNAPSHOT_INTERVAL_MINUTES)
        wanted: Dict[str, str] = {}
        for dep in deployments:
            if (dep.instrument_kind or "UNDERLYING") != InstrumentKind.OPTION.value or session_family(dep.exchange) not in open_families:
                continue
            underlying = master.underlying_of(dep.symbol)
            wanted[underlying] = master.INDEX_SYMBOLS.get(underlying, dep.symbol.upper().strip())
        written = 0
        for underlying, chain_symbol in wanted.items():
            last = self._last_chain_capture.get(underlying)
            if last is not None and now - last < interval:
                continue
            self._last_chain_capture[underlying] = now   # once per interval whether or not the fetch works
            chain = self._cycle_chains.get(chain_symbol)   # already fetched for a strike filter this cycle
            if chain is None:
                expiries = await master.expiries(session, underlying, on_or_after=now.astimezone(IST).date())
                chain = await market_data.broker.get_option_chain(chain_symbol, expiries[0] if expiries else None)
            written += await chain_recorder.record_chain(session, underlying, chain, now)
        return written

    async def _stream_quotes(self, session: AsyncSession, tenant_id: int, adapters: Dict[str, BrokerInterface], data_broker: BrokerInterface,
                             deployments: List[StrategyDeploymentRecord], report: CycleReport) -> None:
        """Phase S: keep the tenant's quote stream subscribed to every symbol a decision this
        cycle could need - each deployment's symbol and every open position (contract and its
        underlying). A broker without a stream, or a stream that cannot resolve a symbol, leaves
        that symbol on REST polling; nothing else changes."""
        key = next((k for k, a in adapters.items() if a is data_broker), data_broker.name)
        wanted: set = {(d.symbol, d.exchange or "NSE") for d in deployments}
        open_trades = await session.scalars(select(TradeRecord).where(TradeRecord.tenant_id == tenant_id, TradeRecord.exit_time.is_(None)))
        for trade in open_trades:
            wanted.add((trade.symbol, exchange_for_trade(trade)))
            if trade.underlying_symbol:
                from app.trading.position_monitor import underlying_exchange
                wanted.add((trade.underlying_symbol, underlying_exchange(trade.underlying_symbol)))
        try:
            await self.streams.ensure(f"{tenant_id}:{key}", data_broker, sorted(wanted))
        except Exception as exc:  # noqa: BLE001 - streaming is an optimisation over REST, never a blocker
            logger.warning("Tenant %s: quote stream error: %s", tenant_id, exc)
        report.streams_connected = self.streams.active

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
        observe_cycle(market_open=report.market_open, seconds=cycle_ms / 1000, signals=report.signals_executed,
                      closes=report.positions_closed, errors=len(report.errors))


async def main() -> None:
    configure_logging()
    from app.db.session import _session_factory  # the app's own engine/pool configuration

    worker = TradingWorker(_session_factory)
    from app.core.config import WORKER_METRICS_PORT
    if WORKER_METRICS_PORT > 0:
        from prometheus_client import start_http_server
        start_http_server(WORKER_METRICS_PORT)  # Phase E1: GET /metrics for this process
        logger.info("Worker metrics on :%s/metrics", WORKER_METRICS_PORT)
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
