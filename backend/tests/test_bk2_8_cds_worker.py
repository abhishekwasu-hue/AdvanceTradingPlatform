"""BK2-8: currency derivatives (NSE CDS / BSE BCD) on their own worker clock - 09:00-17:00 IST, no new entries after
16:30, square-off at 16:45 (provisional cut-offs), the CDS holiday list plus every NSE holiday.

ADR-0004 throughout: a CDS position is monitored after the NSE close and squared off on its own clock; a row without an
exchange follows its deployment; the family of every open position is always on the worker's clock list."""
import asyncio
from datetime import date, datetime, time, timedelta

import pytest
from sqlalchemy import delete, select

from app.core.enums import SignalDirection, SignalGrade
from app.core.models import Signal
from app.db.models import MarketHolidayRecord, StrategyDeploymentRecord, TradeRecord
from app.market_data import calendar as mcal
from app.trading.position_monitor import open_trade_families, trade_venue, venue_beyond_nse
from tests.test_auth_api import _session_factory
from tests.test_trading_worker import IST, OPEN_NOW, STRATEGY, _deploy, _FakeBroker, _force_signal, _trades, _worker
from tests.test_trading_worker import _tenant as _worker_tenant

FRIDAY = date(2026, 9, 25)
AT_1516 = datetime(2026, 9, 25, 15, 16, tzinfo=IST)     # NSE square-off due; CDS keeps trading
AT_1600 = datetime(2026, 9, 25, 16, 0, tzinfo=IST)      # NSE closed, CDS open
AT_1640 = datetime(2026, 9, 25, 16, 40, tzinfo=IST)     # CDS: no new entries, not yet square-off
AT_1646 = datetime(2026, 9, 25, 16, 46, tzinfo=IST)     # CDS square-off
AT_1705 = datetime(2026, 9, 25, 17, 5, tzinfo=IST)      # CDS closed


def _run(coro):
    return asyncio.run(coro)


_TENANTS: list = []


def _tenant(email: str):
    t = _worker_tenant(email)
    _TENANTS.append(t["tenant_id"])
    return t


@pytest.fixture(autouse=True)
def _close_what_this_module_opened():
    """The worker keeps the clock of every open position platform-wide (BK2-8): a CDS position left open here would keep
    CDS on the clock list of later tests' cycles. Each test's positions are closed afterwards."""
    yield

    async def go():
        async with _session_factory() as session:
            for trade in await session.scalars(select(TradeRecord).where(TradeRecord.tenant_id.in_(_TENANTS), TradeRecord.exit_time.is_(None))):
                trade.exit_time, trade.exit_price, trade.exit_reason = datetime(2026, 9, 25, 17, 0, tzinfo=IST), trade.entry_price, "test cleanup"
            await session.commit()
    _run(go())


def _cds_deploy(t, *, exchange="CDS", symbol="USDINR", status="ACTIVE", mode="PAPER") -> int:
    async def go():
        async with _session_factory() as session:
            dep = StrategyDeploymentRecord(holding="INTRADAY", order_style="MARKET", tenant_id=t["tenant_id"], strategy_id=STRATEGY, symbol=symbol, exchange=exchange, timeframe="1min",
                                           mode=mode, broker_name="upstox", status=status, created_by=t["user_id"])
            session.add(dep)
            await session.commit()
            return dep.id
    return _run(go())


def _open_trade(t, *, symbol="USDINR", exchange="CDS", deployment_id=None, holding="INTRADAY") -> int:
    async def go():
        async with _session_factory() as session:
            trade = TradeRecord(tenant_id=t["tenant_id"], user_id=t["user_id"], mode="PAPER", strategy_id=STRATEGY, symbol=symbol,
                                exchange=exchange, deployment_id=deployment_id, direction="LONG", entry_price=100.0, quantity=1,
                                stop_loss=98.0, target1=104.0, target2=108.0, holding=holding,
                                entry_time=datetime(2026, 9, 25, 10, 0, tzinfo=IST))
            session.add(trade)
            await session.commit()
            return trade.id
    return _run(go())


def _trade(trade_id) -> TradeRecord:
    async def go():
        async with _session_factory() as session:
            return await session.get(TradeRecord, trade_id)
    return _run(go())


def _set_status(dep_id, status) -> None:
    async def go():
        async with _session_factory() as session:
            (await session.get(StrategyDeploymentRecord, dep_id)).status = status
            await session.commit()
    _run(go())


def _usd_signal(ts=OPEN_NOW - timedelta(minutes=1)) -> Signal:
    return Signal(symbol="USDINR", strategy_id=STRATEGY, strategy_name="EMA + RSI", direction=SignalDirection.LONG, timestamp=ts,
                  entry=100.0, stop_loss=98.0, target1=104.0, target2=108.0, risk_reward=2.0, score=85,
                  grade=SignalGrade.HIGH_QUALITY, reasons=["forced"], timeframe_combo="1min")


# --- the calendar --------------------------------------------------------------------------------------------------------

def test_cds_is_its_own_family_with_provisional_cutoffs():
    for name in ("CDS", "cds", "NSE_CD", "BCD", "BSE_CD"):
        assert mcal.session_family(name) == "CDS", name
    assert mcal.session_family("NSE") == mcal.session_family("NFO") == mcal.session_family("BSE") == "NSE"   # unchanged
    spec = mcal.EXCHANGE_SESSIONS["CDS"]
    assert (spec.open, spec.close) == (time(9, 0), time(17, 0))
    assert mcal.intraday_cutoffs("CDS") == (time(16, 30), time(16, 45))                    # equity margins, provisional
    assert mcal.intraday_cutoffs("NSE") == (time(15, 0), time(15, 15))                     # the equity clock is untouched
    assert spec.holiday_calendar == "CDS" and spec.also_closed_on == ("NSE",)
    assert mcal.session_status(AT_1600, exchange="CDS").is_open and not mcal.session_status(AT_1600).is_open
    assert not mcal.session_status(AT_1705, exchange="CDS").is_open
    assert not mcal.session_status(datetime(2026, 9, 25, 8, 59, tzinfo=IST), exchange="BCD").is_open
    assert mcal.session_status(datetime(2026, 9, 25, 9, 0, tzinfo=IST), exchange="BCD").is_open


def test_the_cds_holiday_list_is_its_own_days_plus_every_nse_holiday():
    nse_day, cds_day = date(2027, 2, 17), date(2027, 2, 18)          # Wednesday / Thursday

    async def go():
        async with _session_factory() as session:
            session.add_all([MarketHolidayRecord(exchange="NSE", holiday_date=nse_day, description="test NSE holiday"),
                             MarketHolidayRecord(exchange="CDS", holiday_date=cds_day, description="test CDS-only holiday")])
            await session.commit()
            try:
                at = lambda d: datetime.combine(d, time(11, 0), tzinfo=IST)            # noqa: E731
                return ((await mcal.market_session_status(session, at(nse_day), "CDS")).is_open,
                        (await mcal.market_session_status(session, at(cds_day), "CDS")).is_open,
                        (await mcal.market_session_status(session, at(cds_day), "NSE")).is_open,
                        await mcal.family_holidays(session, "BCD", 2027))
            finally:
                await session.execute(delete(MarketHolidayRecord).where(MarketHolidayRecord.holiday_date.in_([nse_day, cds_day])))
                await session.commit()
    cds_on_nse_holiday, cds_on_own_holiday, nse_on_cds_holiday, days = _run(go())
    assert not cds_on_nse_holiday and not cds_on_own_holiday     # an NSE holiday closes CDS, so does a CDS-only one
    assert nse_on_cds_holiday                                    # a CDS-only holiday never closes the equity market
    assert {nse_day, cds_day} <= days


def test_a_row_without_an_exchange_follows_its_deployment_only_beyond_the_nse_family():
    class Row:
        def __init__(self, symbol, exchange=None):
            self.symbol, self.exchange = symbol, exchange
    assert trade_venue(Row("USDINR"), "CDS") == "CDS" and trade_venue(Row("USDINR"), "bcd") == "BCD"
    assert trade_venue(Row("RELIANCE"), "BSE") == "NSE" and trade_venue(Row("RELIANCE"), None) == "NSE"     # as before
    assert trade_venue(Row("CRUDEOIL"), "NSE") == "MCX"                   # the registry still wins for its contracts
    assert trade_venue(Row("USDINR", "NFO"), "CDS") == "NFO"              # a stamped exchange is never second-guessed
    assert venue_beyond_nse("cds") == "CDS" and venue_beyond_nse("NSE") is None and venue_beyond_nse(None) is None


# --- the worker ------------------------------------------------------------------------------------------------------------

def test_cds_entry_stamps_the_exchange_and_an_nse_entry_keeps_nse(monkeypatch):
    t = _tenant("bk28-entry@example.com")
    cds_id = _cds_deploy(t)
    nse_id = _deploy(t)
    _force_signal(monkeypatch, _usd_signal)
    report = _run(_worker(monkeypatch, _FakeBroker(ltp=100.5)).run_cycle(now=OPEN_NOW))
    assert report.market_open and set(report.open_exchanges) == {"NSE", "CDS"}
    by_dep = {tr.deployment_id: tr for tr in _trades(t["tenant_id"])}
    assert by_dep[cds_id].exchange == "CDS"                 # the order went to CDS and the position says so
    assert by_dep[nse_id].exchange == "NSE"                 # an equity entry: same venue as before, now written down


def test_no_new_cds_entry_after_1630_but_the_position_is_still_watched(monkeypatch):
    t = _tenant("bk28-cutoff@example.com")
    dep_id = _cds_deploy(t)
    trade_id = _open_trade(t, deployment_id=dep_id)
    _force_signal(monkeypatch, lambda: _usd_signal(ts=AT_1640 - timedelta(minutes=1)))
    broker = _FakeBroker(ltp=100.5, intraday_bars=460)      # candles up to 16:54: fresh at 16:40
    report = _run(_worker(monkeypatch, broker).run_cycle(now=AT_1640))
    assert report.open_exchanges == ["CDS"] and report.signals_executed == 0
    assert len(_trades(t["tenant_id"])) == 1 and _trade(trade_id).exit_time is None
    broker.ltp = 97.5                                        # the stop is crossed: closed at once, before 16:45
    report = _run(_worker(monkeypatch, broker).run_cycle(now=AT_1640 + timedelta(minutes=1)))
    assert report.positions_closed == 1 and _trade(trade_id).exit_time is not None
    assert "square-off" not in (_trade(trade_id).exit_reason or "").lower()


def test_cds_position_is_monitored_after_the_nse_close(monkeypatch):
    t = _tenant("bk28-monitor@example.com")
    dep_id = _cds_deploy(t)
    nse_dep = _deploy(t)
    cds_trade = _open_trade(t, deployment_id=dep_id)
    nse_trade = _open_trade(t, symbol="RELIANCE", exchange="NSE", deployment_id=nse_dep)
    broker = _FakeBroker(ltp=97.5)                          # below both stops
    report = _run(_worker(monkeypatch, broker).run_cycle(now=AT_1600))
    assert report.market_open and report.open_exchanges == ["CDS"] and "NSE: closed" in report.session_reason
    closed = _trade(cds_trade)
    assert closed.exit_time is not None and "stop" in (closed.exit_reason or "").lower() and report.positions_closed == 1
    assert _trade(nse_trade).exit_time is None              # NSE is closed: its stale quote is not acted on tonight


def test_a_legacy_cds_row_without_an_exchange_is_monitored_after_1530_even_with_its_deployment_stopped(monkeypatch):
    t = _tenant("bk28-legacy@example.com")
    cds_dep = _cds_deploy(t)
    _deploy(t)                                              # an ACTIVE equity deployment keeps the tenant in the cycle
    legacy = _open_trade(t, exchange=None, deployment_id=cds_dep)
    _set_status(cds_dep, "STOPPED")                         # no ACTIVE / PAUSED CDS deployment any more

    async def families(tenants):
        async with _session_factory() as session:
            return await open_trade_families(session, tenants)
    assert "CDS" in _run(families({t["tenant_id"]})) and "CDS" in _run(families(None))   # the open position keeps the CDS clock
    assert _run(families(set())) == set()

    report = _run(_worker(monkeypatch, _FakeBroker(ltp=97.5)).run_cycle(now=AT_1600))
    assert report.open_exchanges == ["CDS"]
    row = _trade(legacy)
    assert row.exit_time is not None and "stop" in (row.exit_reason or "").lower()


def test_cds_square_off_runs_at_1645_on_its_own_clock(monkeypatch):
    t = _tenant("bk28-squareoff@example.com")
    dep_id = _cds_deploy(t)
    nse_dep = _deploy(t)
    cds_trade = _open_trade(t, deployment_id=dep_id)
    legacy = _open_trade(t, exchange=None, deployment_id=dep_id)
    swing = _open_trade(t, deployment_id=dep_id, holding="SWING")
    nse_trade = _open_trade(t, symbol="RELIANCE", exchange="NSE", deployment_id=nse_dep)
    broker = _FakeBroker(ltp=100.7)                         # between stop and target: only the clock closes anything

    report = _run(_worker(monkeypatch, broker).run_cycle(now=AT_1516))
    assert _trade(nse_trade).exit_reason == "Intraday square-off"                       # NSE at 15:15
    assert _trade(cds_trade).exit_time is None and _trade(legacy).exit_time is None     # CDS still trading: watched, not flattened
    report = _run(_worker(monkeypatch, broker).run_cycle(now=AT_1640))
    assert report.positions_closed == 0 and _trade(cds_trade).exit_time is None

    report = _run(_worker(monkeypatch, broker).run_cycle(now=AT_1646))
    assert report.positions_closed == 2
    for trade_id in (cds_trade, legacy):
        row = _trade(trade_id)
        assert row.exit_reason == "Intraday square-off" and row.exit_price == 100.7
    assert _trade(swing).exit_time is None                  # Phase AS: a swing position is held overnight by design


def test_a_cds_stop_crossed_after_the_square_off_time_still_closes(monkeypatch):
    t = _tenant("bk28-late-stop@example.com")
    dep_id = _cds_deploy(t)
    trade_id = _open_trade(t, deployment_id=dep_id)
    report = _run(_worker(monkeypatch, _FakeBroker(ltp=97.0)).run_cycle(now=AT_1646 + timedelta(minutes=5)))
    assert report.positions_closed == 1 and _trade(trade_id).exit_time is not None


def test_status_lists_the_cds_session():
    from tests.test_auth_api import client
    body = client.get("/api/system/status").json()
    assert "CDS" in body["sessions"] and {"is_open", "reason", "next_open"} <= set(body["sessions"]["CDS"])


def test_worker_does_not_read_trades_through_a_stale_session_family(monkeypatch):
    """A CDS-only cycle (NSE closed) never squares off or monitors an NSE row, and leaves no NSE order behind."""
    t = _tenant("bk28-nse-untouched@example.com")
    dep_id = _cds_deploy(t)
    nse_trade = _open_trade(t, symbol="RELIANCE", exchange="NSE")
    _open_trade(t, deployment_id=dep_id)
    _run(_worker(monkeypatch, _FakeBroker(ltp=100.7)).run_cycle(now=AT_1646))

    async def open_nse():
        async with _session_factory() as session:
            return await session.scalar(select(TradeRecord.exit_time).where(TradeRecord.id == nse_trade))
    assert _run(open_nse()) is None


# --- review LOW-2 / LOW-3: the exit order, the stop and the advisory layer follow the position's own venue ----------------

def _live_legacy(t, dep_id, *, sl_order_id=None) -> int:
    async def go():
        async with _session_factory() as session:
            trade = TradeRecord(tenant_id=t["tenant_id"], user_id=t["user_id"], mode="LIVE", strategy_id=STRATEGY, symbol="USDINR",
                                exchange=None, deployment_id=dep_id, direction="LONG", entry_price=100.0, quantity=1,
                                stop_loss=98.0, target1=104.0, target2=108.0, sl_order_id=sl_order_id,
                                entry_time=datetime(2026, 9, 25, 10, 0, tzinfo=IST))
            session.add(trade)
            await session.commit()
            return trade.id
    return _run(go())


class _StopBook(_FakeBroker):
    """A stop stays TRIGGER PENDING until cancelled (the base fake fills every order it is given)."""
    def __init__(self, **kw):
        super().__init__(**kw)
        self.cancelled = set()

    async def cancel_order(self, order_id):
        self.cancelled.add(order_id)
        return await super().cancel_order(order_id)

    async def get_order_book(self):
        book = await super().get_order_book()
        for o in book:
            if o.order_type in ("SL-M", "SL"):
                o.status, o.filled_quantity, o.average_price = ("CANCELLED" if o.order_id in self.cancelled else "TRIGGER PENDING"), 0, None
        return book


def test_a_legacy_live_row_on_a_cds_deployment_exits_with_the_cds_venue_at_the_cds_time(monkeypatch):
    monkeypatch.setattr("app.execution.router.OrderRouter.fill_poll_delay_seconds", 0)
    t = _tenant("bk28-live-exit@example.com")
    dep_id = _cds_deploy(t, mode="LIVE")                         # the tenant's LIVE session is the exit's broker
    trade_id = _live_legacy(t, dep_id)
    broker = _StopBook(ltp=100.7)
    _run(_worker(monkeypatch, broker).run_cycle(now=AT_1516))
    def market_exits():
        return [o for o in broker.placed if o.symbol == "USDINR" and o.order_type == "MARKET"]
    assert _trade(trade_id).exit_time is None and market_exits() == []      # 15:15 is NSE's time, not this position's
    assert all(o.exchange == "CDS" for o in broker.placed if o.symbol == "USDINR")   # its missing stop re-armed on CDS
    _run(_worker(monkeypatch, broker).run_cycle(now=AT_1646))
    exits = market_exits()
    assert exits and exits[-1].exchange == "CDS" and exits[-1].transaction_type.value == "SELL"   # the venue whose clock closed it
    row = _trade(trade_id)
    assert row.exit_time is not None and row.exit_reason == "Intraday square-off"


def test_the_stop_guard_rearms_on_the_positions_own_venue_and_clock(monkeypatch):
    from app.db.models import Tenant
    from app.trading import stop_guard
    t = _tenant("bk28-stop-guard@example.com")
    dep_id = _cds_deploy(t)
    trade_id = _live_legacy(t, dep_id, sl_order_id="GONE-1")       # the stop is missing from the book

    async def guard(families):
        broker = _FakeBroker(ltp=100.5)
        async with _session_factory() as session:
            tenant = await session.get(Tenant, t["tenant_id"])
            counts = await stop_guard.verify_protective_stops(session, tenant, broker, families=families)
        return counts, broker.placed
    counts, placed = _run(guard({"NSE", "MCX"}))                  # CDS closed: not this position's clock
    assert counts["checked"] == 0 and placed == []
    counts, placed = _run(guard({"CDS"}))
    assert counts["rearmed"] == 1 and placed and placed[-1].exchange == "CDS" and placed[-1].order_type in ("SL-M", "SL")
    assert _trade(trade_id).sl_order_id != "GONE-1"


def test_the_ai_monitor_observes_only_deployments_whose_venue_is_open(monkeypatch):
    from app.ai import monitor as ai_monitor
    t = _tenant("bk28-ai-observe@example.com")
    cds_id = _cds_deploy(t)
    nse_id = _deploy(t)
    seen = []

    async def observe(session, tenant_id, deployments, now, regime_lookup=None):
        seen.append(sorted(d.id for d in deployments if d.tenant_id == t["tenant_id"]))
        return []
    monkeypatch.setattr(ai_monitor, "observe", observe)
    _run(_worker(monkeypatch, _FakeBroker(ltp=100.5)).run_cycle(now=AT_1600))
    assert seen and seen[-1] == [cds_id]                        # NSE closed: its deployment is not observed at 16:00
    _run(_worker(monkeypatch, _FakeBroker(ltp=100.5)).run_cycle(now=OPEN_NOW))
    assert seen[-1] == sorted([cds_id, nse_id])
