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
import copy
import logging
import socket
import time
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, time as dtime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.brokers.base import BrokerInterface
from app.alerts.dispatcher import dispatch_pending
from app.brokers.rate_budget import RateBudget, RateLimitedBroker, limits_for
from app.platform import phase0_lock
from app.execution.ops_throttle import OpsThrottle
from app.compliance.static_ip import live_entry_problem
from app.brokers.token_lifecycle import build_adapter, get_credential_record, token_is_usable, verify_token
from app.cache.client import cache_lock_holder, cache_release_lock, cache_renew_lock, cache_try_lock
from app.core import config as app_config
from app.core.config import ENVIRONMENT, HARDENED_ENVIRONMENTS, WORKER_CYCLE_SECONDS
from app.core.enums import DeploymentStatus, ExecutionMode, InstrumentKind, NotificationSeverity, NotificationType, OptionStrategy, SignalDirection
from app.core.logging_config import bind_log_context, configure_logging
from app.custom_strategies.resolver import resolve_strategy
from app.db.models import StrategyDeploymentRecord, Tenant, TradeRecord, User, VisionReportRecord, WorkerHeartbeatRecord
from app.db.savepoint import guarded
from app.plans.limits import live_allowed, tenant_is_active
from app.retention.service import RetentionReport, run_retention
from app.billing.service import sweep as billing_sweep
from app.ai import monitor as ai_monitor
from app.ai import thesis as thesis_module
from app.brokers import login_reminder
from app.workers import eod_summary
from app.workers.redis_guard import RedisGuard
from app.news_feed import service as news_feed
from app.platform import controls as platform_controls
from app.ai.regime import classify_regime, parse_filter, regime_note
from app.observability.metrics import RETENTION_DELETED, observe_cycle
from app.core.config import INSTRUMENT_SYNC_EXCHANGES, INSTRUMENT_SYNC_HOUR_IST
from app.instruments import master
from app.instruments.master import sync_upstox
from app.backtest import chain_recorder
from app.instruments.contracts import ContractResolutionError, ContractRules, resolve_contract
from app.instruments.spreads import parse_custom_legs, resolve_structure
from app.execution import approval
from app.execution.multileg import execute_structure
from app.execution.signal_execution import execute_signal_for_user
from app.market_data.calendar import IST, all_session_statuses, intraday_cutoffs, market_session_status, session_family
from app.market_data.freshness import candle_staleness
from app.market_data.service import MarketDataService
from app.market_data.fanin import UserSessionFanIn
from app.market_data.stream import StreamManager
from app.market_lake.recorder import LakeRecorder
from app.observability.metrics import MARKET_DATA_STALE
from app.reconciliation.service import broker_uncertain_reason, reconcile_accounts, run_reconciliation
from app.secrets_store.envelope import ensure_tenant_key
from app.accounts.routing import ACCOUNT_REFRESH_SECONDS, RoutingPolicy, choose_account, policy_for
from app.accounts.service import default_account, get_account, list_accounts, routing_for_deployment, sync_account
from app.notifications.service import notify
from app.trading.position_monitor import (broker_for_trade, close_group, close_position, monitor_open_positions,
                                          open_trade_families_by_tenant, trade_venues)
from app.trading.stop_guard import verify_protective_stops

logger = logging.getLogger(__name__)

# BT1b: what a deployment says while its organisation (flag byob_data_tier on) has no confirmed-data broker session.
WAITING_FOR_CONFIRMED_DATA = "Waiting for Upstox/Dhan data: no new entries; exits continue"

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
RHYTHM_EVERY_MINUTES = 5            # MO4: how often the worker looks for daily-rhythm digests due on Telegram
# Fairness: no tenant may consume more than this share of a cycle evaluating entries. Deployments
# that don't get their turn are picked up first next cycle (round-robin), so a tenant with many
# deployments is slowed, never starved - and never starves the others.
MAX_TENANT_SHARE_OF_CYCLE = 0.5


# MO3-A1: how often the worker ends expired read-only views.
VIEW_SWEEP_SECONDS = 300


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
    # H-1: the lock vanished mid-cycle (Redis eviction) or another replica took it - no new entries for the rest of the
    # cycle; `lock_aborted` when another holder has it and this worker stopped the cycle there.
    lock_lost: bool = False
    lock_aborted: bool = False
    redis_memory_ratio: Optional[float] = None
    retention: Optional[RetentionReport] = None
    chain_rows_recorded: int = 0   # Phase W
    billing: Optional[Dict[str, int]] = None
    ai_proposals: int = 0
    ai_snoozes_woken: int = 0     # PW5: snoozed proposals announced again this cycle
    master_synced: Optional[Dict[str, int]] = None
    universe_synced: Optional[Dict[str, int]] = None      # U1-a
    system_lists: Optional[Dict[str, int]] = None         # WL1: the system watchlists refresh
    history_eod_started: bool = False                     # HB1: the nightly EOD bhavcopy job was started this cycle
    stale_skips: int = 0
    reconciled: int = 0
    open_exchanges: List[str] = field(default_factory=list)
    stops_rearmed: int = 0
    streams_connected: int = 0   # Phase S: websocket quote streams currently connected
    byob_sessions: int = 0       # BT1: per-user broker data sessions open (flag byob_data_tier)
    memory_snapshots: int = 0    # Phase AR: market-memory rows written this cycle
    lake_bars: int = 0           # B2: 1-minute bars written to the lake this cycle
    login_reminders: int = 0     # D5: pre-open daily-login reminders raised this cycle
    oi_snapshots: int = 0        # OI Banner O2: option-chain OI slots stored this cycle
    eod_summaries: int = 0       # Phase AX: end-of-day summary notifications raised this cycle
    news_items: int = 0          # Phase BB: new feed items stored this cycle
    news_classified: int = 0     # Phase BB: items classified with organisations' own provider keys
    market_zones_started: bool = False   # Z7a-2: the nightly market-zones task was started this cycle
    breakout_fitness_started: bool = False   # U6 BK5: the nightly breakout-fitness task was started this cycle
    intraday_history_started: bool = False   # HB2: an owner intraday-history pass was started this cycle


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


# TX-3 / TX-4: why a deployment opens no new entry when the member who set it up cannot act any more (exits continue).
DEPLOYMENT_CREATOR_INACTIVE = ("No new entries: the member who set up this deployment is no longer an active member of this organisation. "
                               "Open positions are still monitored and closed. The owner can re-create the deployment under an active member.")
DEPLOYMENT_CREATOR_CANNOT_TRADE = ("No new entries: the member who set up this deployment no longer has a trading role or the trading:write "
                                   "scope. Open positions are still monitored and closed.")


def _history_source(market_data: MarketDataService, strategy: object) -> MarketDataService:
    """Strategy templates step 2 (owner order R6): a strategy that names how many calendar days of candles it needs
    (`history_days`, the template adapter: its recipe's warm-up x the safety factor) is fetched with that lookback when it
    is longer than the service's own (5 intraday days); every other strategy gets the service unchanged. A shallow copy:
    the broker session and the caches are shared, only the lookback differs (the candle cache keys carry it)."""
    days = getattr(strategy, "history_days", None)
    if not callable(days):
        return market_data
    need, own = int(days()), getattr(market_data, "lookback_days", None)
    if not isinstance(own, int) or need <= own:
        return market_data
    longer = copy.copy(market_data)
    longer.lookback_days = need
    return longer


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
        self._lock_lost = False
        self._lock_ttl = max(cycle_seconds * 2, 30)
        self.redis_guard = RedisGuard()
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
        self._last_view_sweep: Optional[datetime] = None
        # Phase AX: IST date of the last end-of-day summary (one per organisation per day).
        self._last_eod_summary_day = None
        self._last_rhythm_check: Optional[datetime] = None     # MO4: the daily rhythm's Telegram sends
        self._last_thesis_report_day = None
        self._last_login_reminder_day = None   # D5: the pre-open daily-login reminder, once per IST day
        self._last_analytics_expiry_day = None  # Upstox analytics token: the 7-day / last-day expiry warning, once per IST day
        self._last_analytics_expiry_try: Optional[datetime] = None
        # Phase BB: when the news feed was last fetched (cadence 15 min, 5 min around a macro event).
        self._last_news_fetch: Optional[datetime] = None
        # Phase L: last regime per deployment (for the monitoring agent) and which deployments the
        # agent wants classified even without a filter (those with open positions).
        self._regimes: Dict[int, str] = {}
        self._regime_wanted: set = set()
        # BYOK-V2: the Vision audit drain runs as its own background task (its own session), never inside a cycle.
        self._vision_task: Optional["asyncio.Task[None]"] = None
        # BYOK-V3: when the daily-review queue was last checked (every 15 minutes; the queue itself is once per user per day).
        self._last_vision_daily_check: Optional[datetime] = None
        self._then_warm_task: Optional["asyncio.Task[None]"] = None
        # IST date of the last instrument-master sync (Phase F1): once a day, pre-market.
        self._last_master_sync_day = None
        self._last_universe_sync_day = None   # U1-a
        self._last_system_lists_day = None    # WL1: the system watchlists refresh (the evening run)
        self._system_lists_started = False    # WL1: the one run when the worker starts with NSE closed (a deploy)
        # HB1: the nightly EOD bhavcopy job (IST date of the last start) and its background task.
        self._last_history_eod_day = None
        self._history_eod_task: Optional["asyncio.Task[None]"] = None
        self._last_market_zones_day = None    # Z7a-2: the IST date the nightly market-zones job last COMPLETED
        self._market_zones_task: Optional["asyncio.Task[None]"] = None
        self._last_breakout_fitness_day: Optional[date] = None  # U6 BK5: the IST date the nightly breakout-fitness pass last COMPLETED
        self._breakout_fitness_task: Optional["asyncio.Task[None]"] = None
        # HB2: the IST date the owner's intraday-history pass last COMPLETED, its task, and when an unfinished pass may
        # be started again (monotonic seconds).
        self._last_intraday_day: Optional[date] = None
        self._intraday_task: Optional["asyncio.Task[None]"] = None
        self._intraday_retry_at = 0.0
        self._intraday_adapters: Dict[Any, Any] = {}   # one owner adapter per IST day, reused by that evening's passes
        # One API rate budget per (tenant, broker): tenants use their own API keys, so their
        # broker limits are their own too (app/brokers/rate_budget.py).
        self._budgets: Dict[Tuple[int, str], RateBudget] = {}
        self._ops: Dict[Tuple[int, str], OpsThrottle] = {}
        self._live_paths_locked: Optional[bool] = None   # Phase 0 defaults: last logged state of the global PAPER-only lock
        self._tenant_cursor: Dict[int, int] = {}
        self._stale_skips = 0
        # Phase S: one websocket quote stream per broker session, subscribed each cycle to the
        # symbols the tenant's deployments and open positions need (STREAMING_QUOTES_ENABLED).
        self.streams = StreamManager()
        # BT1: per-user broker sessions for the bring-your-own-broker data tier (flag byob_data_tier, off by default).
        self.byob = UserSessionFanIn()
        self._last_vix: Dict[int, datetime] = {}    # Z5: when each organisation's India VIX reading was last taken
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
        self._last_oi_slot: Dict[str, datetime] = {}      # OI Banner O2: underlying -> last slot attempted
        self.max_seconds_per_tenant = (
            max_seconds_per_tenant if max_seconds_per_tenant is not None else cycle_seconds * MAX_TENANT_SHARE_OF_CYCLE
        )

    # --- lifecycle --------------------------------------------------------------------------

    def stop(self) -> None:
        self._stop.set()
        if self._history_eod_task is not None and not self._history_eod_task.done():
            self._history_eod_task.cancel()          # HB1: the nightly job is resumable - a cancelled day is retried
        if self._market_zones_task is not None and not self._market_zones_task.done():
            self._market_zones_task.cancel()         # Z7a-2: resumable too - stored snapshots are skipped next time
        if self._intraday_task is not None and not self._intraday_task.done():
            self._intraday_task.cancel()             # HB2: resumable - claimed days stay `running` and are asked again
        if self._then_warm_task is not None and not self._then_warm_task.done():
            self._then_warm_task.cancel()            # N1 X1: in memory only - the next worker warms again
        if self._vision_task is not None and not self._vision_task.done():
            self._vision_task.cancel()               # BYOK-V2: a claimed row is closed as "interrupted" by the next drain
        if self._breakout_fitness_task is not None and not self._breakout_fitness_task.done():
            self._breakout_fitness_task.cancel()     # U6 BK5: resumable - stored templates are skipped next time

    async def run_forever(self) -> None:
        logger.info("Trading worker %s starting (cycle %ss)", self.holder_id, self.cycle_seconds)
        await self.startup_check()
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
            try:
                from app.ai import instrument_names
                async with self.session_factory() as session:
                    await instrument_names.refresh_if_stale(session)       # H-C1 c: new listings every six hours
            except Exception:  # noqa: BLE001 - never affects trading
                logger.warning("instrument names not refreshed", exc_info=True)
            elapsed = time.monotonic() - started
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=max(1.0, self.cycle_seconds - elapsed))
            except asyncio.TimeoutError:
                pass
        await self.streams.stop_all()
        await self.byob.close_all()
        logger.info("Trading worker %s stopped", self.holder_id)

    async def startup_check(self) -> Dict[str, object]:
        """Phase 0 deployment defaults (app/platform/deployment_phase.py): seed the two Phase 0 flags where unset
        (ATP_DEPLOYMENT_PHASE=phase0 only) and log why LIVE entry paths are not started while `phase0_paper_only` is on.
        The worker always comes up: exits, stops, square-off and reconciliation of LIVE positions run regardless."""
        from app.ai import instrument_names
        from app.platform import deployment_phase
        try:
            async with self.session_factory() as session:
                await instrument_names.refresh(session)        # H-C1 c: instrument names for the AI output filter (never raises)
        except Exception:  # noqa: BLE001 - never prevents the worker from starting
            logger.warning("instrument names not loaded", exc_info=True)
        try:
            async with self.session_factory() as session:
                state = await deployment_phase.on_startup(session, "worker")
        except Exception:  # noqa: BLE001 - never prevents the worker from starting
            logger.exception("Phase 0 start-up check failed")
            return {"error": "failed"}
        self._live_paths_locked = bool(state.get("phase0_paper_only"))
        return state

    def _note_lock_state(self) -> None:
        """Logs when the global PAPER-only lock changes between cycles (the owner switched it in Admin -> Controls)."""
        locked = phase0_lock.locked(None)
        if locked != self._live_paths_locked:
            from app.platform.deployment_phase import LIVE_PATHS_NOT_STARTED
            if locked:
                logger.warning("Trading worker: %s", LIVE_PATHS_NOT_STARTED)
            else:
                logger.warning("Trading worker: phase0_paper_only is off - LIVE deployments are evaluated again (flags and plans decide)")
            self._live_paths_locked = locked

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
        self._lock_lost = False

        try:
            async with self.session_factory() as session:
                # Z10-c: the broker-order chokepoint reads the PAPER-only lock from this process; refresh it every cycle.
                await phase0_lock.is_on(session, None)
                self._note_lock_state()
                # Phase O2: one clock per exchange family. NSE is always considered; MCX/CDS/CRYPTO only
                # when a deployment actually trades there, so a pure-equity platform behaves as before.
                # BK2-8 / #221: and always the family of every open position of every organisation - with or without an
                # ACTIVE / PAUSED deployment (a PAPER ticket, a manual proposal, a stopped deployment) - so its exits run on
                # its own clock (ADR-0004). The same select names the organisations that get the exits part of the cycle.
                live_deps = (await session.execute(select(StrategyDeploymentRecord.tenant_id, StrategyDeploymentRecord.exchange).where(
                    StrategyDeploymentRecord.status.in_([DeploymentStatus.ACTIVE.value, DeploymentStatus.PAUSED.value])).distinct())).all()
                open_by_tenant = await open_trade_families_by_tenant(session)
                wanted = {"NSE"} | {session_family(ex) for _, ex in live_deps}
                wanted |= set().union(*open_by_tenant.values()) if open_by_tenant else set()
                statuses = {name: st for name, st in (await all_session_statuses(session, now)).items() if name in wanted}
                self._open_families = {name for name, st in statuses.items() if st.is_open}
                nse = statuses["NSE"]
                reason = nse.reason if wanted == {"NSE"} else "; ".join(f"{n}: {'open' if st.is_open else 'closed'}" for n, st in sorted(statuses.items()))
                report = CycleReport(started_at=now, market_open=bool(self._open_families), session_reason=reason, open_exchanges=sorted(self._open_families),
                                     lock_degraded=self._lock_degraded)
                if self._open_families:
                    self._stale_skips = 0
                    await self._process_tenants(session, now, report, open_tenants=set(open_by_tenant))
                    report.stale_skips = self._stale_skips
                    # P0.1 / S2: a long evaluation phase must not let the lock lapse under the housekeeping below.
                    if report.lock_aborted or not await self._keep_lock(session, report, "after the tenants"):
                        return report   # H-1: another replica holds the lock - its cycle runs the housekeeping
                else:
                    logger.debug("Market closed: %s", reason)
                # BT1: open / refresh / close each user's own broker session (flag byob_data_tier; market closed = day end).
                # BT1b: its own DB session, so a failure here never rolls back the cycle's session.
                async with self.session_factory() as byob_session:
                    try:
                        byob = await self.byob.sync(byob_session, now, market_open="NSE" in self._open_families)
                        report.byob_sessions = byob.active
                        report.errors.extend(byob.errors)
                    except Exception as exc:  # noqa: BLE001 - a user's data session must never break trading
                        logger.exception("BYOB session sync failed")
                        report.errors.append(f"byob sessions: {exc}")
                        await byob_session.rollback()
                # Z5 / P1-d: the daily loss lockout is recorded when a user's PAPER day reaches the limit through open marks,
                # not only when something reads it (closes record it themselves). After the exits; never blocks one.
                try:
                    from app.risk_engine import by_default as risk_by_default
                    await risk_by_default.sweep(session, now)
                except Exception as exc:  # noqa: BLE001 - bookkeeping; never breaks trading
                    logger.exception("Daily loss lockout sweep failed")
                    report.errors.append(f"lockout sweep: {exc}")
                    await session.rollback()
                # Z5 (Trade check_vix_spike_halt): the India VIX reading for each organisation with the gate on, from 09:20 IST.
                if nse.is_open:
                    try:
                        await self._vix_readings(session, now)
                    except Exception as exc:  # noqa: BLE001 - a missing reading makes the gate fail safe for bullish entries only
                        logger.exception("India VIX reading failed")
                        report.errors.append(f"vix reading: {exc}")
                        await session.rollback()
                if self.lake is not None:
                    try:
                        report.lake_bars = (await self.lake.drain(session, now)).inserted
                    except Exception as exc:  # noqa: BLE001 - the lake must never break trading
                        logger.exception("Lake bar write failed")
                        report.errors.append(f"lake: {exc}")
                        await session.rollback()
                # H-1: Redis memory against its cap (every REDIS_MEMORY_CHECK_SECONDS), a WARNING at 70 %.
                try:
                    async with guarded(session):
                        report.redis_memory_ratio = await self.redis_guard.check_memory(session)
                except Exception as exc:  # noqa: BLE001 - a monitoring read never breaks trading
                    logger.exception("Redis memory check failed")
                    report.errors.append(f"redis memory check: {exc}")
                # Out-of-app alert delivery (Telegram/email) rides on this loop, market open or not:
                # a TOKEN_EXPIRED raised at 03:31 must reach a phone before 09:15.
                try:
                    # S4a: alert rules on closed bars -> events (flag screener_v2 per organisation), time-boxed so the
                    # trading loop is never held up; a rule's own problem stays on the rule (last_problem).
                    from app.alerts import barclose
                    await barclose.run_due(session, now)
                except Exception as exc:  # noqa: BLE001 - rule evaluation must never stop delivery of what already fired
                    logger.exception("Alert rule evaluation failed")
                    await session.rollback()
                    report.errors.append(f"alert rules: {exc}")
                try:
                    # S3a: alert-rule events -> grouped notifications (quiet hours, caps, digests) before the outbox drain.
                    from app.alerts import engine as alert_engine
                    await alert_engine.flush(session, now)
                except Exception as exc:  # noqa: BLE001 - a rule problem must never stop the outbox drain
                    logger.exception("Alert rule flush failed")
                    await session.rollback()
                    report.errors.append(f"alert flush: {exc}")
                # N1 U4 X1 (flag scan_then): scan Then templates - the level gate on each matched symbol's closed bars; a
                # confirmed setup becomes an execution-core intent (drained below into a notification or a PAPER proposal).
                self._spawn_then_warm(now)
                try:
                    from app.screener.then import runner as scan_then
                    await scan_then.run_due(session, now)
                except Exception as exc:  # noqa: BLE001 - a Then template must never stop the alerts or the outbox
                    logger.exception("Scan Then run failed")
                    await session.rollback()
                    report.errors.append(f"scan then: {exc}")
                # PW5: snoozed proposals whose time is up are announced once more (in-app, push, Telegram) before the
                # outbox drain, market open or not - a snooze "until 09:00" must ring at 09:00.
                try:
                    report.ai_snoozes_woken = await ai_monitor.wake_snoozed(session, now)
                except Exception as exc:  # noqa: BLE001 - a reminder must never break trading or delivery
                    logger.exception("Waking snoozed proposals failed")
                    await session.rollback()
                    report.errors.append(f"ai snooze wake: {exc}")
                # Z3 (review M6): proposal expiry for every organisation, every cycle, market open or not - also for an
                # organisation with no deployment or a lapsed token (entry proposals live minutes; an approval interrupted
                # between decide and execute is failed after ORPHAN_MINUTES so it never holds a deployment's slot).
                try:
                    await ai_monitor.expire_stale(session, now)
                except Exception as exc:  # noqa: BLE001 - housekeeping; never breaks trading or delivery
                    logger.exception("Expiring proposals failed")
                    await session.rollback()
                    report.errors.append(f"ai expiry: {exc}")
                # N1 E1 (ADR-0014, flag execution_core): queued entry intents -> a notification or a PAPER entry proposal
                # (never an order), before the outbox drain so they go out this cycle. Market open or not; an intent
                # nobody dispatched in time expires. One flags read when the flag is off everywhere.
                try:
                    async with guarded(session):
                        from app.execution.core import dispatcher as intent_dispatcher
                        await intent_dispatcher.drain(session, now)
                except Exception as exc:  # noqa: BLE001 - the core must never break trading or delivery
                    logger.exception("Execution intent drain failed")
                    report.errors.append(f"execution intents: {exc}")
                try:
                    async with guarded(session):
                        await dispatch_pending(session)
                except Exception as exc:  # noqa: BLE001 - alerting must never break trading
                    logger.exception("Alert dispatch failed")
                    report.errors.append(f"alert dispatch: {exc}")
                try:
                    # BYOK-V3: after the close, queue each opted-in user's daily review (rows only; idempotent per user per day).
                    if self._last_vision_daily_check is None or now - self._last_vision_daily_check >= timedelta(minutes=15):
                        self._last_vision_daily_check = now
                        from app.ai import vision_audit
                        # Review LOW-4: its own session - it commits, and must neither commit nor roll back the cycle's.
                        async with self.session_factory() as vision_session:
                            try:
                                await vision_audit.enqueue_daily_reviews(vision_session, now)
                            except Exception:
                                await vision_session.rollback()
                                raise
                    # On the cycle's session: in a savepoint, so a failed read never leaves that session's transaction
                    # aborted for the steps after it (ADR-0004).
                    async with guarded(session):
                        if await session.scalar(select(VisionReportRecord.id).where(VisionReportRecord.status == "pending").limit(1)) is not None:
                            self._spawn_vision_drain()
                except Exception as exc:  # noqa: BLE001 - Vision is advisory; never breaks trading
                    logger.warning("Vision audit check failed: %s", exc.__class__.__name__)
                # Phase AR: the Copilot's market memory - every 15 minutes while NSE is open and in
                # the hour after the close (the day's final read); before the open only the global
                # cues (no broker read). Never blocks trading.
                if nse.is_open or _just_closed(now) or _pre_open(now):
                    try:
                        async with guarded(session):
                            report.memory_snapshots = await self._market_memory(session, now, broker_reads=nse.is_open or _just_closed(now))
                    except Exception as exc:  # noqa: BLE001
                        logger.exception("Market memory failed")
                        report.errors.append(f"market memory: {exc}")
                # OI Banner O2: one OI snapshot per enabled underlying per slot while its venue is open.
                try:
                    async with guarded(session):
                        report.oi_snapshots = await self._oi_banner(session, now)
                except Exception as exc:  # noqa: BLE001 - reference data; never blocks trading
                    logger.exception("OI banner collection failed")
                    report.errors.append(f"oi banner: {exc}")
                # OI Banner O4b: the day's banner digest after each tenant's digest time (any time, market open or not).
                try:
                    from app.option_chain import oi_alerts
                    async with self.session_factory() as own:
                        await oi_alerts.send_digests(own, now)
                except Exception as exc:  # noqa: BLE001 - a digest never blocks the cycle
                    logger.warning("OI banner digest failed: %s", exc)
                # MO4 (app/rhythm): the daily rhythm on each opted-in user's own Telegram chat - the pre-market brief, the
                # pulse, the closing wrap on trading days and the weekly review on the weekend; once each (marks per user).
                # Flags ui_v2 + daily_rhythm per organisation. Its own session; read-only towards trading.
                if self._last_rhythm_check is None or now - self._last_rhythm_check >= timedelta(minutes=RHYTHM_EVERY_MINUTES):
                    self._last_rhythm_check = now
                    try:
                        from app.rhythm import deliver as rhythm_deliver
                        async with self.session_factory() as own:
                            await rhythm_deliver.send_due(own, now)
                    except Exception as exc:  # noqa: BLE001 - a digest never blocks the cycle
                        logger.warning("Daily rhythm sends failed: %s", type(exc).__name__)
                # Phase AX: the end-of-day summary - once per IST weekday from 15:35, one
                # notification per organisation with a deployment or a trade today. Read-only.
                ist_now = now.astimezone(IST)
                if eod_summary.eod_due(now) and self._last_eod_summary_day != ist_now.date():
                    self._last_eod_summary_day = ist_now.date()      # a failure retries tomorrow, not every minute
                    try:
                        async with guarded(session):
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
                # Upstox analytics token: warn its holder 7 days before it expires and on the last day (deduped per title).
                # Its own session (it commits); the day counts as done only after a run succeeds, so a failed run is
                # retried (at most every 15 minutes) and a last-day "expires today" is not lost.
                if self._last_analytics_expiry_day != ist_now.date() and (
                        self._last_analytics_expiry_try is None or now - self._last_analytics_expiry_try >= timedelta(minutes=15)):
                    self._last_analytics_expiry_try = now
                    try:
                        from app.brokers import upstox_analytics
                        async with self.session_factory() as own:
                            try:
                                await upstox_analytics.send_expiry_warnings(own, now)
                            except Exception:
                                await own.rollback()
                                raise
                        self._last_analytics_expiry_day = ist_now.date()
                    except Exception as exc:  # noqa: BLE001 - a reminder must never break trading
                        logger.exception("Analytics token expiry warning failed")
                        report.errors.append(f"analytics token expiry: {exc}")
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
                    async with guarded(session):
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
                        async with guarded(session):
                            report.master_synced = await sync_upstox(session, INSTRUMENT_SYNC_EXCHANGES)
                            self._last_master_sync_day = ist_now.date()
                            logger.info("Instrument master synced: %s", report.master_synced)
                    except Exception as exc:  # noqa: BLE001 - a failed download must not stop trading
                        logger.exception("Instrument master sync failed")
                        report.errors.append(f"instrument master: {exc}")
                        self._last_master_sync_day = ist_now.date()  # retry tomorrow, not every minute
                # Screener addendum U1-a: the NSE equity lists and symbol changes, once per IST day after
                # UNIVERSE_SYNC_HOUR_IST, only when the operator has turned it on (files' terms recorded first).
                if app_config.UNIVERSE_SYNC_ENABLED and not nse.is_open and self._last_universe_sync_day != ist_now.date() \
                        and ist_now.hour >= app_config.UNIVERSE_SYNC_HOUR_IST:
                    self._last_universe_sync_day = ist_now.date()      # success or failure: next try is tomorrow
                    try:
                        async with guarded(session):
                            from app.universe.sources import NseArchiveSource
                            from app.universe.sync import sync_equity_lists
                            u = await sync_equity_lists(session, NseArchiveSource(), ist_now.date())
                            report.universe_synced = {"inserted": u.inserted, "updated": u.updated, "seen": u.seen, "refused": len(u.refused)}
                            from app.universe import indices as universe_indices                     # U1-b: constituents after the list
                            idx = await universe_indices.sync_indices(session, NseArchiveSource(universe_indices.source_urls()), ist_now.date())
                            report.universe_synced.update(indices=len(idx), rebalanced=sum(1 for r in idx if r.entered or r.exited),
                                                          indices_refused=sum(1 for r in idx if r.refused))
                            from app.universe import fo_eod                                          # U1-c: closes, lots, ban list
                            fo = await fo_eod.sync_fo_and_index_eod(session, NseArchiveSource(fo_eod.dated_urls(ist_now.date())), ist_now.date())
                            report.universe_synced.update(index_closes=fo.index_rows, fo_changes=len(fo.fo_entered) + len(fo.fo_exited) + len(fo.lot_changes),
                                                          banned=len(fo.banned), fo_refused=len(fo.refused))
                            from app.universe import classification                                  # WL1: the full NSE classification,
                            cls = await classification.sync_classification(session, NseArchiveSource(), ist_now.date())  # when its URL is set
                            if cls is not None:
                                report.universe_synced.update(classified=cls.changed, classification_refused=int(bool(cls.refused)))
                    except Exception as exc:  # noqa: BLE001 - reference data must never stop trading
                        logger.exception("Universe sync failed")
                        report.errors.append(f"universe sync: {exc}")
                # WL1 (handover item 4): the read-only system watchlists (indices, index stocks, sectors, F&O stocks), rebuilt
                # once per IST day after UNIVERSE_SYNC_HOUR_IST from the tables already held - no download, so it runs whether or
                # not the universe sync is on (with it off, the lists are as current as the last sync or upload). NSE closed.
                # Also once when the worker starts with NSE closed (a deploy), so new lists do not wait for the evening.
                # That start-up run does not count as the day's run, so the evening one still follows the universe sync.
                from app.watchlists import system as system_lists
                run_lists, evening = system_lists.refresh_due(nse_open=nse.is_open, hour=ist_now.hour, today=ist_now.date(),
                                                              sync_hour=app_config.UNIVERSE_SYNC_HOUR_IST,
                                                              last_day=self._last_system_lists_day, started=self._system_lists_started)
                if run_lists:
                    self._system_lists_started = True
                    if evening:
                        self._last_system_lists_day = ist_now.date()    # success or failure: next try is tomorrow
                    try:
                        async with guarded(session):
                            sl = await system_lists.refresh(session)
                            report.system_lists = {"lists": sl.lists_total, "changed": sl.lists_changed, "unmapped": sl.unmapped}
                    except Exception as exc:  # noqa: BLE001 - reference data must never stop trading
                        logger.exception("System watchlists refresh failed")
                        report.errors.append(f"system watchlists: {exc}")
                # HB1: the nightly EOD job - once per IST day from HISTORY_BACKFILL_HOUR_IST, only while the
                # `history_backfill` flag is on; it runs in its own task, so a slow archive never holds up a cycle.
                try:
                    async with guarded(session):
                        report.history_eod_started = await self._maybe_start_history_eod(session, now)
                except Exception as exc:  # noqa: BLE001 - market data history must never stop trading
                    logger.exception("History EOD job not started")
                    report.errors.append(f"history eod: {exc}")
                # Z7a-2: market zones - once per IST day from MARKET_ZONES_HOUR_IST, NSE closed (flag `market_zones`, off by
                # default). Started as its own task (own sessions, a time budget), so it never holds up a cycle - exits,
                # stop guards and MCX square-off keep running. Reference data; never trades.
                if not nse.is_open:
                    try:
                        report.market_zones_started = await self._maybe_start_market_zones(session, now)
                    except Exception as exc:  # noqa: BLE001 - reference data must never stop trading
                        logger.exception("Market zones job not started")
                        report.errors.append(f"market zones: {exc}")
                        await session.rollback()
                # U6 BK5: breakout screen fitness - once per IST day from BREAKOUT_FITNESS_HOUR_IST, NSE closed (flag
                # `screener_breakouts_fitness`, off by default). Its own task, sessions and time budget; reference data only.
                if not nse.is_open:
                    try:
                        report.breakout_fitness_started = await self._maybe_start_breakout_fitness(session, now)
                    except Exception as exc:  # noqa: BLE001 - reference data must never stop trading
                        logger.exception("Breakout fitness job not started")
                        report.errors.append(f"breakout fitness: {exc}")
                        await session.rollback()
                # HB2: the owner's intraday history (flag `history_intraday`, off by default) - after the close, through the
                # owner's own broker session, drawing from that session's rate budget only while a reserve stays free for
                # exits. Its own task with its own sessions and a time budget; never during NSE hours.
                if not nse.is_open:
                    try:
                        report.intraday_history_started = await self._maybe_start_intraday_history(session, now)
                    except Exception as exc:  # noqa: BLE001 - market data history must never stop trading
                        logger.exception("Intraday history pass not started")
                        report.errors.append(f"intraday history: {exc}")
                        await session.rollback()
                # MO3-A1: end (and audit) the read-only "View as user" sessions that expired with no later request -
                # every few minutes, cheap (an indexed read of the open views).
                if self._last_view_sweep is None or (now - self._last_view_sweep).total_seconds() >= VIEW_SWEEP_SECONDS:
                    try:
                        async with guarded(session):
                            from app.auth.view_as import end_expired
                            if await end_expired(session, now):
                                await session.commit()
                            self._last_view_sweep = now
                    except Exception as exc:  # noqa: BLE001 - housekeeping never stops trading
                        report.errors.append(f"view sessions: {exc}")
                # Data retention (Phase D3): once per IST day, outside market hours so it never
                # competes with order flow for the database.
                if not nse.is_open and self._last_retention_day != now.astimezone(IST).date():
                    try:
                        async with guarded(session):
                            report.retention = await run_retention(session, now)
                            # P0.3 / S7: pin the audit chain's head once a day (cheap; lets verification start from here).
                            try:
                                async with guarded(session):
                                    from app.audit.log import record_anchor
                                    if await record_anchor(session, now) is not None:
                                        await session.commit()
                            except Exception as exc:  # noqa: BLE001 - an anchor never blocks the cycle
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
                        async with guarded(session):
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

    async def _byob_on(self, session: AsyncSession, tenant_id: int) -> bool:
        """BT1b: the bring-your-own-broker data tier is on for this organisation (flag byob_data_tier, off by default)."""
        from app.platform.controls import flag_enabled
        return await flag_enabled(session, "byob_data_tier", tenant_id)

    async def _first_usable_broker(self, session: AsyncSession, tenant_id: int, now: datetime) -> Optional[BrokerInterface]:
        """The tenant's first ACTIVE account with a usable session (read-only use), or None."""
        await ensure_tenant_key(session, tenant_id)
        user = await self._acting_user(session, tenant_id, [])
        if user is None:
            return None
        for account in await list_accounts(session, tenant_id):
            if account.status == "ACTIVE":
                broker = await self._usable_adapter(session, tenant_id, account.broker_name, user.id, now, account_label=account.account_label)
                if broker is not None:
                    return broker
        return None

    @staticmethod
    async def _oi_future_quote(session: AsyncSession, broker: Any, underlying: str, now: datetime) -> Tuple[Optional[float], Optional[float]]:
        """OC5: the near-month future's (last price, OI) through the same broker session, for the Option chain charts.
        (None, None) when the master has no future, the adapter has no single-symbol quote or the read fails - the
        chain's slot is stored either way."""
        from app.option_chain import chain_view
        try:
            async with guarded(session):              # a failed master read never leaves the slot's transaction aborted
                contract = await chain_view.near_future(session, underlying, now.astimezone(IST).date())
            reader = getattr(broker, "get_quote_for_symbol", None)
            if contract is None or reader is None:
                return None, None
            quote = await reader(contract.symbol, contract.exchange)
        except Exception as exc:  # noqa: BLE001 - the future is extra; the chain's slot never waits on it
            logger.info("OI snapshot of %s: the future's quote could not be read: %s", underlying, type(exc).__name__)
            return None, None
        if quote is None:
            return None, None
        return getattr(quote, "ltp", None), getattr(quote, "oi", None)

    async def _oi_banner(self, session: AsyncSession, now: datetime) -> int:
        """OI Banner O2: for every underlying some tenant enabled, one option-chain read per collector slot while its
        venue is open, through the first of those tenants with a usable broker session. The rows are platform-wide
        reference data; a slot already stored (by another replica or cycle) is skipped. Never places an order."""
        from app.option_chain import oi_regime, snapshots as oi_snapshots
        wanted: Dict[str, List[Tuple[int, str]]] = {}
        for tenant_id, underlying, exchange in await oi_snapshots.enabled_underlyings(session):
            wanted.setdefault(underlying, []).append((tenant_id, exchange))
        if not wanted:
            return 0
        slot = oi_regime.slot_start(now.astimezone(IST), app_config.OI_BANNER_SLOT_MINUTES)
        open_by_family: Dict[str, bool] = {}
        stored = 0
        for underlying, tenants in sorted(wanted.items()):
            family = session_family(tenants[0][1])
            if family not in open_by_family:
                open_by_family[family] = (await market_session_status(session, now, tenants[0][1])).is_open
            if not open_by_family[family] or self._last_oi_slot.get(underlying) == slot:
                continue
            self._last_oi_slot[underlying] = slot            # once per slot whether or not the read works
            for tenant_id, _ in sorted(tenants):
                # Its own session: a failed read is rolled back without touching the cycle's session.
                async with self.session_factory() as own:
                    with bind_log_context(tenant_id=tenant_id):
                        try:
                            if await self._byob_on(own, tenant_id):
                                continue   # BT1b: platform-wide OI rows are never read through a per-user (BYOB) session
                            broker = await self._first_usable_broker(own, tenant_id, now)
                            if broker is None:
                                continue
                            expiries = await master.expiries(own, underlying, on_or_after=now.astimezone(IST).date())
                            chain = await broker.get_option_chain(master.INDEX_SYMBOLS.get(underlying, underlying), expiries[0] if expiries else None)
                            future_price, future_oi = await self._oi_future_quote(own, broker, underlying, now)
                            result = await oi_snapshots.collect(own, underlying, chain, now, source=broker.name,
                                                                future_price=future_price, future_oi=future_oi)
                            await own.commit()
                            stored += 1 if result.status == "OK" else 0
                            break
                        except Exception as exc:  # noqa: BLE001 - try the next tenant's session
                            await own.rollback()
                            logger.warning("OI snapshot of %s through tenant %s failed: %s", underlying, tenant_id, exc)
            # O4: each following tenant's banner state and alerts for this slot (also when the read failed: a stale
            # collector is itself an alert). Its own session; a failure never stops the cycle.
            from app.option_chain import oi_alerts
            for tenant_id in sorted({t for t, _ in tenants}):
                async with self.session_factory() as own:
                    try:
                        await oi_alerts.evaluate_tenant(own, tenant_id, underlying, now, market_open=True)
                    except Exception as exc:  # noqa: BLE001
                        await own.rollback()
                        logger.warning("OI alerts of %s for tenant %s failed: %s", underlying, tenant_id, exc)
        return stored

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
                    if not broker_reads or await self._byob_on(session, tenant_id):
                        # BT1b: organisation-wide memory / sentiment are not fed from a per-user (BYOB) session.
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

    async def _vix_readings(self, session: AsyncSession, now: datetime) -> int:
        """Z5: one India VIX reading per organisation with the `vix_gate` flag on, when due (from 09:20 IST, then every
        VIX_GATE_CAPTURE_MINUTES), through its entitled data path (app/risk_engine/vix_gate.py). A failed read is stored
        with its reason. Returns the readings taken."""
        from app.platform.controls import feature_flags
        from app.risk_engine import vix_gate
        entry = (await feature_flags(session)).get(vix_gate.FLAG) or {}
        if entry.get("on"):
            tenant_ids = sorted(set(await session.scalars(select(User.tenant_id).where(User.is_active.is_(True)).distinct())))
        else:
            tenant_ids = sorted(int(t) for t in entry.get("tenants") or [])
        taken = 0
        for tenant_id in tenant_ids:
            if not vix_gate.due(self._last_vix.get(tenant_id), now):
                continue
            with bind_log_context(tenant_id=tenant_id):
                try:
                    user = await self._acting_user(session, tenant_id, [])
                    members = list(await session.scalars(select(User).where(User.tenant_id == tenant_id, User.is_active.is_(True))
                                                         .order_by(User.id)))
                    broker = None
                    if user is not None:
                        for account in await list_accounts(session, tenant_id):
                            if account.status == "ACTIVE":
                                broker = await self._usable_adapter(session, tenant_id, account.broker_name, user.id, now, account_label=account.account_label)
                                if broker is not None:
                                    break
                    await vix_gate.capture_for_tenant(session, tenant_id, user, org_adapter=broker, market_data_factory=self.market_data_factory,
                                                      now=now, members=members)
                    self._last_vix[tenant_id] = now          # a reading (good or failed) is stored: the next one is due later
                    taken += 1
                except Exception as exc:  # noqa: BLE001 - one organisation's reading must not stop the others
                    logger.exception("India VIX reading for tenant %s failed", tenant_id)
                    await session.rollback()
                    try:                                     # store why, so the gate can say it; else retry next cycle
                        await vix_gate.capture(session, tenant_id, None, now=now, source="none", error=f"reading failed: {type(exc).__name__}: {exc}"[:150])
                        self._last_vix[tenant_id] = now
                    except Exception:  # noqa: BLE001 - no reading stored: due again on the next cycle
                        logger.exception("Storing the failed India VIX reading for tenant %s failed", tenant_id)
                        await session.rollback()
        return taken

    async def _keep_lock(self, session: AsyncSession, report: CycleReport, where: str) -> bool:
        """P0.1 / S2 renewal + H-1 fail-closed. False when the cycle must stop here (another replica holds the lock).

        Renewed: carry on. Redis unreachable: the S2 degraded mode (LIVE entries paused where required). The key gone
        (evicted at the memory cap): take it back, but no new entries for the rest of this cycle - a second replica
        could have run in the gap - and exits continue. Held by someone else: stop, the holder trades. Both raise the
        CRITICAL (app/workers/redis_guard.py)."""
        if self._lock_degraded or await cache_renew_lock(LOCK_KEY, self.holder_id, self._lock_ttl):
            return True
        holder, reachable = await cache_lock_holder(LOCK_KEY)
        if not reachable:
            logger.warning("Redis unreachable %s - the replica lock is off (LIVE entries paused where required)", where)
            self._lock_degraded = report.lock_degraded = True
            return True
        if holder == self.holder_id:
            return True   # the renewal call failed but the lock is still ours
        retaken = False
        if holder is None:
            retaken, _ = await cache_try_lock(LOCK_KEY, self.holder_id, self._lock_ttl)
            holder = None if retaken else (await cache_lock_holder(LOCK_KEY))[0]
        self._lock_lost = report.lock_lost = True
        await self.redis_guard.lock_lost(session, retaken=retaken, holder=holder, where=where)
        if not retaken:
            report.lock_aborted = True
            return False
        return True

    async def _process_tenants(self, session: AsyncSession, now: datetime, report: CycleReport, open_tenants: Optional[set] = None) -> None:
        deployments = list(await session.scalars(
            select(StrategyDeploymentRecord)
            .where(StrategyDeploymentRecord.status.in_([DeploymentStatus.ACTIVE.value, DeploymentStatus.PAUSED.value]))
            .order_by(StrategyDeploymentRecord.tenant_id, StrategyDeploymentRecord.id)
        ))
        by_tenant: Dict[int, List[StrategyDeploymentRecord]] = {}
        for dep in deployments:
            by_tenant.setdefault(dep.tenant_id, []).append(dep)
        # ADR-0004: an open position is watched whether or not a deployment still runs for it - a PAPER ticket, a manual
        # /paper or webhook proposal, or a deployment stopped while its position stayed open. Such an organisation gets
        # the exits part of the cycle (stop, time exit, square-off, stop guard) with no deployment to evaluate.
        if open_tenants is None:
            open_tenants = set(await session.scalars(select(TradeRecord.tenant_id).where(TradeRecord.exit_time.is_(None)).distinct()))
        for tenant_id in open_tenants:
            by_tenant.setdefault(tenant_id, [])
        by_tenant = dict(sorted(by_tenant.items()))

        for tenant_id, tenant_deployments in by_tenant.items():
            # P0.1 / S2: the evaluation phase is the long one; keep the replica lock alive per tenant.
            if not await self._keep_lock(session, report, f"before tenant {tenant_id}"):
                break   # H-1: another replica holds the lock now - it trades (and exits) the remaining tenants
            with bind_log_context(tenant_id=tenant_id):
                try:
                    async with guarded(session):
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
        # #221 LOW-4: an organisation with no active user still gets its exits (ADR-0004); they are recorded with no acting
        # user, and no entry is evaluated (below).
        user_id = user.id if user is not None else None
        if user is None:
            logger.warning("Tenant %s has no active user to act as - exits only (%d deployment(s) not evaluated)", tenant_id, len(deployments))

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
            adapter = await self._usable_adapter(session, tenant_id, account.broker_name, user_id, now, account_label=account.account_label)
            if adapter is not None:
                adapters[_adapter_key(account.broker_name, account.account_label)] = adapter

        # BT1b (flag byob_data_tier): prefer a broker whose data terms allow display in ATP (Upstox, Dhan) as the
        # organisation's data source; its cache is kept to this organisation (below) and it is never streamed into the
        # platform-wide tick keys or the lake (each user's own session streams through the fan-in instead).
        byob = await self._byob_on(session, tenant_id)
        from app.market_data.data_terms import confirmed_brokers
        confirmed = confirmed_brokers()
        if byob:
            adapters = dict(sorted(adapters.items(), key=lambda kv: 0 if kv[0].split("@")[0] in confirmed else 1))
            # Every org-wide stream of this organisation stops (whatever broker it was on, also on the early return
            # below): a user's credential streams only through the per-user fan-in, never into md:tick:* or the lake.
            await self.streams.drop_prefix(f"{tenant_id}:")
        data_broker = next(iter(adapters.values()), None)
        if data_broker is None:
            message = "No usable broker session (token expired or missing) - log in again on the Broker page"
            for dep in deployments:
                dep.last_error = message
            await session.commit()
            # #221 LOW-4: an exit needs a price, and a price comes only through the organisation's own broker session
            # (BT1b: never another organisation's feed) - so its open positions are not watched until a session is back.
            # Said loudly; docs/OPEN_QUESTIONS.md LD-8.
            unwatched = await session.scalar(select(func.count()).select_from(TradeRecord).where(
                TradeRecord.tenant_id == tenant_id, TradeRecord.exit_time.is_(None)))
            if unwatched:
                logger.error("Tenant %s: %s - %d open position(s) get no exits until a broker session is usable", tenant_id, message, unwatched)
            else:
                logger.warning("Tenant %s: %s", tenant_id, message)
            return
        market_data = self.market_data_factory(data_broker)
        if byob and hasattr(market_data, "cache_namespace"):
            # BT1b: this organisation's own cache keys - never the platform-wide md:candles / md:tick keys another
            # organisation reads (get_ltp then polls REST unless a tick arrives under these keys).
            market_data.cache_namespace = f"org.t{tenant_id}"
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
        if app_config.STREAMING_QUOTES_ENABLED and not byob:
            # BT1b: with byob_data_tier on, a user's credential is streamed only by the per-user fan-in (its own keys,
            # never the lake) - not also by this org-wide stream (no second websocket on the same token, no shared ticks).
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
                try:
                    async with guarded(session, keep=(tenant_row,)):
                        reports = await reconcile_accounts(session, tenant_row, pairs, user_id=user_id, source="worker")
                        report.reconciled += 1 if reports else 0
                except Exception as exc:  # noqa: BLE001 - flagged and audited by the service; exits below still run
                    logger.warning("Tenant %s: reconciliation while uncertain failed: %s", tenant_id, exc)
            elif live_broker is not None:
                try:
                    async with guarded(session, keep=(tenant_row,)):
                        await run_reconciliation(session, tenant_row, live_broker_name or live_broker.name, live_broker,
                                                 user_id=user_id, source="worker")
                        report.reconciled += 1
                except Exception as exc:  # noqa: BLE001 - flagged and audited by the service
                    logger.warning("Tenant %s: reconciliation while uncertain failed: %s", tenant_id, exc)
        open_families = getattr(self, "_open_families", None) or {"NSE"}
        # Phase O2: each venue squares off on its own clock (NSE 15:15, MCX 23:15, crypto never);
        # positions on venues still inside their session are monitored as before.
        due_square_off = {f for f in open_families if (cut := intraday_cutoffs(f)[1]) is not None and now_ist.time() >= cut}
        if due_square_off:
            report.positions_closed += await self._square_off_all(session, tenant_id, market_data, exit_broker, user_id, families=due_square_off)
        monitored = open_families - due_square_off
        if monitored:
            # Z3 / BT2: on approval-only positions a target asks "Exit now?"; the stop and time exits stay automatic.
            exit_approval = await approval.exit_approval_predicate(session, tenant_id, deployments)
            outcomes = await monitor_open_positions(session, tenant_id, market_data.get_ltp, broker=exit_broker, user_id=user_id, families=monitored,
                                                    exit_approval=exit_approval, now=now)
            report.positions_closed += sum(1 for o in outcomes if o.closed)
        # Phase P1: every open LIVE position keeps a standing broker-side stop, whatever happened to it.
        # Phase T: checked per account, against that account's own order book.
        if tenant_row is not None:
            try:
                async with guarded(session, keep=(tenant_row,)):
                    report.stops_rearmed += await self._guard_stops(session, tenant_row, accounts, adapters, user_id, families=open_families)
            except Exception as exc:  # noqa: BLE001 - advisory safety net; never stops the cycle
                logger.warning("Tenant %s: stop guard failed: %s", tenant_id, exc)
        if user is None:
            for dep in deployments:
                dep.last_error = "No active user in the organisation: exits only, no new entries"
            await session.commit()
            return
        tenant_started = time.monotonic()
        tenant = await session.get(Tenant, tenant_id)
        if tenant is not None and not tenant_is_active(tenant):
            # Exits above still ran (a suspended org's open risk is still real); no new entries.
            for dep in deployments:
                dep.last_error = f"Organisation is {tenant.status}: no new entries"
            await session.commit()
            return
        data_key = next((k for k, a in adapters.items() if a is data_broker), getattr(data_broker, "name", ""))
        if byob and data_key.split("@")[0] not in confirmed:
            # BT1b / BT1-4: with the bring-your-own-broker data tier on, no new evaluation on a broker whose terms do not
            # allow its data in ATP (execution-only / pending). Exits, square-off and the stop guard above still ran.
            for dep in deployments:
                dep.last_error = WAITING_FOR_CONFIRMED_DATA
            await session.commit()
            return
        active = [d for d in deployments if d.status == DeploymentStatus.ACTIVE.value]
        # Round-robin start so a tenant whose turn ran out of time last cycle resumes where it
        # stopped rather than re-evaluating the same first few deployments forever.
        start = self._tenant_cursor.get(tenant_id, 0) % max(1, len(active))
        ordered = active[start:] + active[:start]
        evaluated = 0
        members: Dict[int, Optional[User]] = {user.id: user}      # per-cycle cache of the deployments' creators
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
            if dep.mode == ExecutionMode.LIVE.value and phase0_lock.locked(tenant_id):
                # Z10-c / Phase 0 defaults: the LIVE entry path is not started at all while the PAPER-only lock is on
                # (layers 1 and 2 would refuse the entry anyway). Exits, stops and square-off above already ran.
                if dep.last_error != phase0_lock.WORKER_NOT_EVALUATED:     # written once, not every cycle
                    dep.last_error = phase0_lock.WORKER_NOT_EVALUATED
                    await session.commit()
                continue
            if dep.mode == ExecutionMode.LIVE.value and not live_allowed(tenant):
                dep.last_error = "Plan does not include live trading - LIVE entries skipped"
                await session.commit()
                continue
            if self._lock_lost:
                dep.last_error = "Worker lock was lost this cycle (Redis eviction?) - no new entries until the next cycle; exits continue"
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
                dep_user = user
                try:
                    # TX-3 / BT1b: a deployment's entries act as the member who set it up (its trades carry their user id),
                    # not as the organisation's acting user; exits above keep the organisation's acting user (ADR-0004).
                    try:
                        resolved, refusal = await self._deployment_user(session, tenant_id, dep, user, members)
                    except Exception:  # noqa: BLE001 - a failed lookup skips this deployment's entries this cycle
                        # Never re-attribute (TX-4): acting as the organisation's user would also skip the inactive /
                        # cannot-trade checks. Nothing is written on a session that just failed; exits already ran.
                        logger.exception("Deployment %s: creator lookup failed - no new entries this cycle", dep.id)
                        continue
                    if resolved is None:
                        # Entries only: the monitor, square-off and stop guard above already ran for its positions.
                        if dep.last_error != refusal:
                            dep.last_error = refusal
                            await session.commit()
                        continue
                    dep_user = resolved
                    async with guarded(session, keep=(dep, tenant, user, dep_user)):
                        executed = await self._evaluate_deployment(
                            session, dep, dep_user, market_data, adapters, now, entries_allowed,
                        )
                        report.deployments_evaluated += 1
                        if executed:
                            report.signals_executed += 1
                except Exception as exc:  # noqa: BLE001 - recorded on the deployment, never fatal
                    await self._record_failure(session, dep, exc, dep_user.id)
                    report.errors.append(f"deployment {dep.id}: {exc}")
        self._tenant_cursor[tenant_id] = (start + evaluated) % max(1, len(active))

        # Phase W: sample the option chains of this tenant's ACTIVE option deployments for later
        # backtests - one fetch per underlying per interval, platform-wide, never on a closed venue.
        try:
            async with guarded(session):
                if not byob:   # BT1b: platform-wide reference rows are not recorded from a per-user (BYOB) broker session
                    report.chain_rows_recorded += await self._record_chains(session, active, market_data, now, open_families)
        except Exception as exc:  # noqa: BLE001 - reference data; never stops the cycle
            logger.warning("Tenant %s: option-chain recording failed: %s", tenant_id, exc)

        # Phase L4: the monitoring agent observes this tenant's deployments and raises proposals
        # for a human to decide on. It never acts on its own.
        try:
            async with guarded(session):
                open_dep_ids = set(await session.scalars(select(TradeRecord.deployment_id).where(
                    TradeRecord.tenant_id == tenant_id, TradeRecord.exit_time.is_(None), TradeRecord.deployment_id.is_not(None))))
                self._regime_wanted = open_dep_ids
                # BK2-8 review LOW-3: only deployments whose own venue is trading now (no CDS "exit?" question at 20:00 because
                # MCX is open). Advisory only - the monitor and square-off above already judged every open position.
                observed = [d for d in deployments if session_family(d.exchange) in open_families]
                proposals = await ai_monitor.observe(session, tenant_id, observed, now, regime_lookup=lambda d: self._regimes.get(d.id))
                if proposals:
                    created = await ai_monitor.raise_proposals(session, tenant_id, proposals, now)
                    report.ai_proposals += len(created)
        except Exception as exc:  # noqa: BLE001 - advisory layer; never stops trading
            logger.exception("Monitoring agent failed for tenant %s", tenant_id)
            report.errors.append(f"ai monitor: {exc}")

    async def _maybe_start_history_eod(self, session: AsyncSession, now: datetime) -> bool:
        """HB1: starts the nightly bhavcopy job (today's files + the catch-up window) when it is due and the flag is on.
        One task per worker; the backfill's advisory lock keeps a second replica (or a manual run) out."""
        from app.market_lake import bhavcopy_backfill
        from app.platform.controls import flag_enabled

        day = bhavcopy_backfill.nightly_due(now, self._last_history_eod_day)
        if day is None or (self._history_eod_task is not None and not self._history_eod_task.done()):
            return False
        if not await flag_enabled(session, bhavcopy_backfill.FLAG):
            return False                                 # asked again next cycle: turning the flag on tonight still runs
        self._last_history_eod_day = day                 # started: the next run is tomorrow

        async def run() -> None:
            from app.market_lake.nse_fetch import PoliteFetcher
            fetcher = PoliteFetcher()
            try:
                summary = await bhavcopy_backfill.run_nightly(self.session_factory, fetcher, now=now)
                logger.info("History EOD job: %s", summary.as_dict())
            except bhavcopy_backfill.BackfillConfigError as exc:
                logger.warning("History EOD job did not run: %s", exc)
            except Exception:  # noqa: BLE001 - market data history; never reaches trading
                logger.exception("History EOD job failed")
            finally:
                await fetcher.aclose()

        self._history_eod_task = asyncio.get_running_loop().create_task(run())
        return True

    async def _maybe_start_intraday_history(self, session: AsyncSession, now: datetime) -> bool:
        """HB2: starts an owner intraday-history pass when one is due (from HISTORY_INTRADAY_HOUR_IST, NSE closed), the
        flag is on for the owner's tenant and no pass is running. An unfinished pass (time budget, no session yet, rate
        limited) is started again at most every HISTORY_INTRADAY_RETRY_MINUTES; the day counts as done only when a pass
        worked through everything. The pass shares this worker's RateBudget for the owner's session."""
        from app.market_lake import intraday_history

        day = intraday_history.nightly_due(now, self._last_intraday_day)
        if day is None or (self._intraday_task is not None and not self._intraday_task.done()):
            return False
        if time.monotonic() < self._intraday_retry_at:
            return False
        if not await intraday_history.enabled(session):
            return False                                 # asked again next cycle: turning the flag on tonight still runs
        self._intraday_retry_at = time.monotonic() + app_config.HISTORY_INTRADAY_RETRY_MINUTES * 60

        async def run() -> None:
            try:
                result = await intraday_history.run_pass(self.session_factory, budget_for=self._budget_for,
                                                         adapter_cache=self._intraday_adapters)
                if result.complete:
                    self._last_intraday_day = day
                elif result.status == "request_cap":     # at most one capped pass per 30 minutes (Upstox: 2,000 / 30 min)
                    self._intraday_retry_at = max(self._intraday_retry_at, time.monotonic() + 30 * 60)
            except intraday_history.IntradayConfigError as exc:
                logger.warning("Intraday history pass did not run: %s", exc)
            except Exception:  # noqa: BLE001 - market data history; never reaches trading
                logger.exception("Intraday history pass failed")

        self._intraday_task = asyncio.get_running_loop().create_task(run())
        return True

    async def _maybe_start_market_zones(self, session: AsyncSession, now: datetime) -> bool:
        """Z7a-2: starts the nightly market-zones pass as a background task when it is due and the flag is on for anyone.
        One task per worker; the job's advisory lock keeps a second replica out. The day counts as done only when a pass
        worked through the whole plan - a pass that ran out of time (or failed) is started again on a later cycle."""
        from app.price_action.zones import job as zones_job

        day = zones_job.nightly_due(now, self._last_market_zones_day)
        if day is None or (self._market_zones_task is not None and not self._market_zones_task.done()):
            return False
        if not await zones_job.any_enabled(session):
            return False                                 # asked again next cycle: turning the flag on tonight still runs
        if not await zones_job.eod_ready(session, day, now):
            return False                                 # HB1's EOD ingest for the day is still running: asked again next cycle

        async def run() -> None:
            try:
                result = await zones_job.run_nightly(self.session_factory, now=now)
                if result.get("complete"):
                    self._last_market_zones_day = day
            except Exception:  # noqa: BLE001 - reference data; never reaches trading
                logger.exception("Market zones job failed")

        self._market_zones_task = asyncio.get_running_loop().create_task(run())
        return True

    def _spawn_then_warm(self, now: datetime) -> None:
        """N1 X1 (review of #214, H1): today's Daily trend contexts for the scan Then symbols, computed in a background task
        with its own session (a cold fold takes seconds) - never inside the trading cycle, one at a time per worker."""
        if self._then_warm_task is not None and not self._then_warm_task.done():
            return

        async def run() -> None:
            from app.screener.then import runner as scan_then
            try:
                await scan_then.warm_due(self.session_factory, now)
            except Exception:  # noqa: BLE001 - a missing context only means "trend pending"; never reaches trading
                logger.exception("Scan Then trend warm-up failed")

        try:
            self._then_warm_task = asyncio.get_running_loop().create_task(run())
        except RuntimeError:
            self._then_warm_task = None
    async def _maybe_start_breakout_fitness(self, session: AsyncSession, now: datetime) -> bool:
        """U6 BK5: starts the nightly breakout-fitness pass as a background task when it is due, the flag is on for anyone
        and HB1's EOD ingest is done (the zones job's rule). One task per worker; the job's advisory lock keeps a second
        replica out; the day counts as done only when a pass worked through every template."""
        from app.price_action.zones import job as zones_job
        from app.screener.breakouts import fitness_job

        day = fitness_job.nightly_due(now, self._last_breakout_fitness_day)
        if day is None or (self._breakout_fitness_task is not None and not self._breakout_fitness_task.done()):
            return False
        if not await fitness_job.any_enabled(session):
            return False
        if not await zones_job.eod_ready(session, day, now):
            return False

        async def run() -> None:
            try:
                result = await fitness_job.run_nightly(self.session_factory, now=now)
                if result.get("complete"):
                    self._last_breakout_fitness_day = day
            except Exception:  # noqa: BLE001 - reference data; never reaches trading
                logger.exception("Breakout fitness job failed")

        self._breakout_fitness_task = asyncio.get_running_loop().create_task(run())
        return True

    def _spawn_vision_drain(self) -> None:
        """BYOK-V2: audits queued Vision reports (the users' own keys, comment only) in a background task with its own
        session - a slow provider never holds up a cycle, and one drain runs at a time per worker. Claims are
        conditional updates, so a second replica never audits the same row."""
        if self._vision_task is not None and not self._vision_task.done():
            return

        async def run() -> None:
            from app.ai import vision_audit
            try:
                async with self.session_factory() as own:
                    await vision_audit.drain(own)
            except Exception:  # noqa: BLE001 - advisory; never reaches trading
                logger.exception("Vision audit drain failed")

        try:
            self._vision_task = asyncio.get_running_loop().create_task(run())
        except RuntimeError:
            self._vision_task = None

    async def _evaluate_deployment(
        self, session: AsyncSession, dep: StrategyDeploymentRecord, user: User, market_data: MarketDataService,
        adapters: Dict[str, BrokerInterface], now: datetime, entries_allowed: bool,
    ) -> bool:
        strategy = await resolve_strategy(dep.strategy_id, user, session)
        frames = await _history_source(market_data, strategy).get_frames(dep.symbol, dep.exchange, dep.timeframe, strategy.timeframes)
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

        # Phase L3 -> handover item 5 C: the indicator regime is an admin diagnostic only. It is classified and logged;
        # it never blocks an entry (and never touched exits). The old filter's verdict is logged for the admin.
        allowed = parse_filter(dep.regime_filter)
        regime = classify_regime(base) if (allowed or dep.id in self._regime_wanted or len(base) >= 60) else None
        if regime is not None:
            self._regimes[dep.id] = regime.kind
        if allowed and regime is not None:
            note = regime_note(regime, allowed)
            if note:
                logger.info("Deployment %s: regime diagnostic (never blocks): %s", dep.id, note)

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
        # OI Banner O5: opt-in OI gates before a NEW entry (this path never runs for exits - ADR-0004). Fail-closed.
        if getattr(dep, "oi_gates", None):
            from app.option_chain import oi_gates
            oi_direction = oi_gates.direction_of(signal.direction.value if hasattr(signal.direction, "value") else signal.direction)
            if oi_direction is not None:
                verdict = await oi_gates.check_entry(session, dep.tenant_id, master.underlying_of(dep.symbol), oi_direction,
                                                     oi_gates.parse_gates(dep.oi_gates), now)
                if not verdict.allowed:
                    dep.last_signal_at = signal_ts                     # this signal is done; the next one is checked afresh
                    dep.last_error = f"Signal at {signal_ts.isoformat()} skipped by OI gates: " + "; ".join(verdict.reasons)[:400]
                    await session.commit()
                    return False

        # Z3 / BT2: approval-only - the signal becomes an ENTRY proposal; nothing is placed until a person approves.
        if await approval.required(session, dep.tenant_id, dep):
            return await self._propose_entry(session, dep, signal, signal_ts, now, user.id)

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
            exchange=dep.exchange,
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
        logger.info("Deployment %s signal %s -> executed=%s order=%s%s", dep.id, signal.direction.value, result.executed, order.id,
                    f" provenance={signal.provenance}" if signal.provenance else "")
        return result.executed

    async def _propose_entry(self, session: AsyncSession, dep: StrategyDeploymentRecord, signal, signal_ts: datetime, now: datetime,
                             user_id: int) -> bool:
        """Z3 / BT2: an approval-only deployment's signal -> one ENTRY proposal (PAPER). Returns False: nothing executed.
        Phase 0 proposes the underlying (cash) only: a LIVE deployment places nothing through ATP, and option / future /
        structure deployments are not proposed yet - each says so on the deployment instead."""
        skip = None
        if dep.mode == ExecutionMode.LIVE.value:
            skip = "Approval-only (Phase 0): ATP places no LIVE entry - deploy in PAPER to get entry proposals"
        elif ContractRules.from_deployment(dep).derived or (dep.option_strategy or "SINGLE") != "SINGLE":
            skip = "Approval-only (Phase 0): option / future deployments are not proposed yet - only the underlying is"
        dep.last_signal_at = signal_ts                      # this signal bar is handled either way
        dep.consecutive_failures = 0
        if skip is not None:
            dep.last_error = f"Signal at {signal_ts.isoformat()} skipped. {skip}"
            await session.commit()
            return False
        try:
            row = await approval.propose_entry(session, dep.tenant_id, signal=signal, strategy_id=dep.strategy_id, source="deployment",
                                               idempotency_key=f"deployment:{dep.id}:{signal_ts.isoformat()}", deployment=dep,
                                               requested_mode=dep.mode, exit_rules=dep.exit_rules, holding=getattr(dep, "holding", None) or "INTRADAY",
                                               user_id=user_id, now=now, exchange=dep.exchange or "NSE")   # the resolved deployment user
        except approval.EntriesPaused as exc:          # Z3-b: the deployment creator paused their PAPER entries
            dep.last_error = f"Signal at {signal_ts.isoformat()} not proposed: {exc}"
            await session.commit()
            return False
        dep.last_error = (f"Waiting for approval: entry proposal #{row.id}" if row is not None
                          else f"Signal at {signal_ts.isoformat()} not proposed: an earlier entry proposal of this deployment is still open")
        await session.commit()
        logger.info("Deployment %s signal %s -> entry proposal %s", dep.id, signal.direction.value, row.id if row is not None else "-")
        return False

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
            return None, account_id, f"LIVE entry skipped: no usable {routed_broker} session - log in again on the Broker page"
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
                        async with guarded(session, keep=(tenant,)):
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

    async def _deployment_user(
        self, session: AsyncSession, tenant_id: int, dep: StrategyDeploymentRecord, fallback: User,
        cache: Dict[int, Optional[User]],
    ) -> Tuple[Optional[User], Optional[str]]:
        """TX-3: (the user a deployment's entries act as, None) or (None, why it opens no new entry).

        * no creator recorded (older rows): the organisation's acting user, as before;
        * the creator is an active member of this organisation with a trading role and `trading:write`: the creator;
        * otherwise (removed, deactivated, another organisation's user, demoted to VIEWER or without the scope): no new
          entries - never re-attributed to someone else (OPEN_QUESTIONS TX-4). Exits are not decided here (ADR-0004)."""
        creator_id = dep.created_by
        if creator_id is None:
            return fallback, None
        if creator_id not in cache:
            cache[creator_id] = await session.get(User, creator_id)
        creator = cache[creator_id]
        if creator is None or creator.tenant_id != tenant_id or not creator.is_active:
            return None, DEPLOYMENT_CREATOR_INACTIVE
        if not approval.can_trade(creator):
            return None, DEPLOYMENT_CREATOR_CANNOT_TRADE
        return creator, None

    async def _usable_adapter(
        self, session: AsyncSession, tenant_id: int, broker_name: str, user_id: Optional[int], now: datetime, account_label: str = "primary",
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

    def _budget_for(self, tenant_id: int, budget_key: str) -> RateBudget:
        """The one RateBudget of a (tenant, "<broker>@<account>") session - shared by the trading calls and HB2's history
        reads (which take tokens only from what is spare, app/brokers/rate_budget.py)."""
        key = (tenant_id, budget_key)
        budget = self._budgets.get(key)
        if budget is None:
            budget = self._budgets[key] = RateBudget(limits_for(budget_key.split("@")[0]))
        return budget

    def _rate_limited(self, tenant_id: int, budget_key: str, adapter: BrokerInterface) -> BrokerInterface:
        key = (tenant_id, budget_key)
        budget = self._budget_for(tenant_id, budget_key)
        ops = None
        if app_config.OPS_THROTTLE_ENABLED:      # Part D2: one OPS throttle per (tenant, broker account), kept across cycles
            ops = self._ops.get(key)
            if ops is None:
                ops = self._ops[key] = OpsThrottle(app_config.OPS_PER_SECOND)
        return RateLimitedBroker(adapter, budget, ops=ops, tenant_id=tenant_id)

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
        live_broker: Optional[BrokerInterface], user_id: Optional[int], families: Optional[set] = None,
    ) -> int:
        open_trades = list(await session.scalars(
            select(TradeRecord).where(TradeRecord.tenant_id == tenant_id, TradeRecord.exit_time.is_(None))
        ))
        venues = await trade_venues(session, open_trades)       # BK2-8: a row without an exchange follows its deployment
        closed = 0
        groups: Dict[str, List[TradeRecord]] = {}
        for trade in open_trades:
            if families is not None and session_family(venues[trade.id]) not in families:
                continue
            if (getattr(trade, "holding", None) or "INTRADAY") == "SWING":
                continue   # Phase AS: a swing position is held overnight by design
            if trade.leg_group_id:
                groups.setdefault(trade.leg_group_id, []).append(trade)
                continue
            try:
                price = await market_data.get_ltp(trade.symbol, venues[trade.id])
            except Exception as exc:  # noqa: BLE001
                logger.warning("Square-off: no price for %s (%s) - left open", trade.symbol, exc)
                continue
            broker = None
            if trade.mode == ExecutionMode.LIVE.value:
                # #221 MED-2: the trade's own account (cached per cycle), as the monitor does, when no LIVE deployment named
                # a session - a LIVE position with no deployment is squared off too (an exit: the Phase 0 chokepoint lets
                # it through).
                broker = live_broker or await broker_for_trade(session, trade)
            trade_id = trade.id
            try:
                # Review LOW-2 (ADR-0004): one savepoint per position - one close that fails never ends the square-off.
                async with guarded(session, keep=(trade,)):
                    outcome = await close_position(session, trade, price, "Intraday square-off", broker=broker, user_id=user_id)
                closed += int(outcome.closed)
            except Exception as exc:  # noqa: BLE001 - left open, squared off again next cycle
                logger.error("Square-off of trade %s failed: %s", trade_id, exc)
        for group_id, legs in groups.items():
            # MED-1 (naked-short guard): a structure's legs close through close_group - shorts first, and its wings are
            # held while a LIVE short has not closed (selling them first would leave a naked short).
            prices: Dict[int, float] = {}
            for leg in legs:
                try:
                    prices[leg.id] = await market_data.get_ltp(leg.symbol, venues[leg.id])
                except Exception as exc:  # noqa: BLE001 - that leg is left open (a short without a quote holds its wings)
                    logger.warning("Square-off: no price for %s (%s) - left open", leg.symbol, exc)
            try:
                async with guarded(session, keep=tuple(legs)):
                    outcomes = await close_group(session, legs, prices, "Intraday square-off", broker=live_broker, user_id=user_id)
                closed += sum(int(o.closed) for o in outcomes)
            except Exception as exc:  # noqa: BLE001 - left open, squared off again next cycle
                logger.error("Square-off of group %s failed: %s", group_id, exc)
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
                           user_id: Optional[int], families: Optional[set] = None) -> int:
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
                                                  include_unassigned=bool(account.is_default) or len(accounts) == 1, families=families)
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
        open_trades = list(await session.scalars(select(TradeRecord).where(TradeRecord.tenant_id == tenant_id, TradeRecord.exit_time.is_(None))))
        venues = await trade_venues(session, open_trades)
        for trade in open_trades:
            wanted.add((trade.symbol, venues[trade.id]))
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
    from app.market_data import shared_candles
    shared_candles.never_wait_on_other_processes()   # LD-10 review: never wait on an API process's candle fill (ADR-0004)

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
