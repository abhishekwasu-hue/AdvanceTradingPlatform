"""Owner order (3 Nov): the day's Daily bar of the indices soon after the close, and NSE's evening files looked for earlier.

No network: the broker is a fake adapter (START-labelled bars, recording every call); the clock is fixed. Dates are in May /
June 2023 (no other test uses them; the test database has no holiday calendar unless a test adds one).
Covered: `due` from the configured time and never before the close; the 1-minute -> Daily rule (whole continuous session
only); a pass writes ONE provisional bar per index into the history owner's PRIVATE store (never the shared lake),
idempotent on a re-run; the broker's Daily candle when it serves today, else its 1-minute bars, else the stored 1-minute
bars; no bar without data, on a holiday or a weekend, or when NSE's file is already in; the reads (chart layer, the store
read the Copilot uses) append it only after the last stored bar, only for the holder, and NSE's row supersedes it; the
"official close" is never claimed for it; the early NSE tries until the day's files are done; the worker steps.
"""
import asyncio
from datetime import UTC, date, datetime, time, timedelta

from sqlalchemy import select

from app.ai import copilot_chat as chat
from app.ai.tools import ToolResult
from app.core import config
from app.core.models import OHLCVBar
from app.db.models import HistoryBackfillFileRecord, MarketHolidayRecord, MdCandleRecord, MdTenantCandleRecord
from app.market_data.calendar import IST
from app.market_lake import bhavcopy_backfill as hb1
from app.market_lake import index_history as dh
from app.market_lake import own_bars
from app.market_lake import provisional_daily as pdaily
from tests.test_auth_api import _session_factory
from tests.test_data_h_lake_backfill import Owner, _count, _teammate, _user, minute_bars

TUE = date(2023, 5, 16)
MON = date(2023, 5, 15)
AT = datetime(2023, 5, 16, 15, 50, tzinfo=IST)
KEY = "NSE_INDEX:NIFTY50"
R = MdTenantCandleRecord


def _run(coro):
    return asyncio.run(coro)


class Broker:
    """`daily_today`: whether the historical endpoint serves today's Daily candle (Kite does; Upstox does not)."""

    def __init__(self, day: date, base: float, *, daily_today: bool = False, minutes: bool = True, fail: bool = False) -> None:
        self.day, self.base, self.daily_today, self.with_minutes, self.fail = day, base, daily_today, minutes, fail
        self.calls: list[tuple] = []

    async def get_historical_data(self, symbol, exchange, interval, from_date, to_date):
        self.calls.append(("historical", symbol, interval))
        if self.fail:
            raise RuntimeError("token abcdefghijklmnopqrstuvwxyz0123456789 refused")
        if interval == "day" and self.daily_today:
            return [OHLCVBar(timestamp=datetime.combine(self.day, time(0, 0), tzinfo=IST), open=self.base, high=self.base + 90,
                             low=self.base - 70, close=self.base + 33, volume=0)]
        return []

    async def get_intraday_candles(self, symbol, exchange, interval):
        self.calls.append(("intraday", symbol, interval))
        return minute_bars(self.day, self.base, after_close=5) if self.with_minutes else []

    async def place_order(self, *a, **k):
        raise AssertionError("the provisional pass placed an order")


def _pass(owner: Owner, broker, now=AT, **kw) -> pdaily.ProvisionalReport:
    kw.setdefault("wrap", lambda inner, record: inner)
    return _run(pdaily.run_pass(_session_factory, now=now, adapter_factory=lambda record: broker, clock=lambda: now, **kw))


def _prov_rows(tenant_id: int, key: str = KEY):
    async def go():
        async with _session_factory() as session:
            return list(await session.scalars(select(R).where(R.tenant_id == tenant_id, R.instrument_key == key, R.timeframe == pdaily.TIMEFRAME)))
    return _run(go())


def _add(*rows):
    async def go():
        async with _session_factory() as session:
            session.add_all(list(rows))
            await session.commit()
    _run(go())


def _nse(key: str, day: date, close: float) -> MdCandleRecord:
    return MdCandleRecord(instrument_key=key, timeframe="day", ts=dh.session_close(day), source="nse_bhav:index", version=1,
                          open=close - 10, high=close + 20, low=close - 30, close=close, volume=0)


def _prov(tenant_id: int, holder: int, key: str, day: date, close: float, how: str = pdaily.FROM_BROKER_MINUTES) -> R:
    return R(tenant_id=tenant_id, instrument_key=key, timeframe=pdaily.TIMEFRAME, ts=dh.session_close(day),
             source=pdaily.source_for(how, holder, "upstox"), version=1, open=close - 5, high=close + 15, low=close - 25, close=close,
             volume=0, ingested_at=datetime.combine(day, time(15, 46), tzinfo=IST).astimezone(UTC))


# --- when and how -------------------------------------------------------------------------------------------------------------
def test_due_from_the_configured_time_never_before_the_close_and_once_a_day(monkeypatch):
    monkeypatch.setattr(config, "DAILY_PROVISIONAL_AT_IST", time(15, 45))
    assert pdaily.due(datetime(2023, 5, 16, 15, 40, tzinfo=IST), None) is None
    assert pdaily.due(datetime(2023, 5, 16, 15, 45, tzinfo=IST), None) == TUE
    assert pdaily.due(datetime(2023, 5, 16, 18, 0, tzinfo=IST), TUE) is None
    monkeypatch.setattr(config, "DAILY_PROVISIONAL_AT_IST", time(15, 0))        # set before the close: still waits for it
    assert pdaily.due(datetime(2023, 5, 16, 15, 10, tzinfo=IST), None) is None
    monkeypatch.setattr(config, "DAILY_PROVISIONAL_AT_IST", None)               # "off"
    assert pdaily.due(datetime(2023, 5, 16, 16, 0, tzinfo=IST), None) is None


def test_config_clock_parses_hh_mm_and_off(monkeypatch):
    monkeypatch.setenv("ATP_TEST_CLOCK", "16:05")
    assert config._ist_clock("ATP_TEST_CLOCK", "15:45") == time(16, 5)
    monkeypatch.setenv("ATP_TEST_CLOCK", "off")
    assert config._ist_clock("ATP_TEST_CLOCK", "15:45") is None
    monkeypatch.setenv("ATP_TEST_CLOCK", "25:99")
    assert config._ist_clock("ATP_TEST_CLOCK", "15:45") == time(15, 45)         # a bad value: the default, reported
    assert config.DAILY_PROVISIONAL_AT_IST == time(15, 45) and config.HISTORY_BACKFILL_FIRST_TRY_IST == time(17, 30)
    assert config.HISTORY_BACKFILL_RETRY_MINUTES == 30


def test_a_daily_bar_is_built_only_from_a_whole_continuous_session():
    t = dh.target("NIFTY")
    raw = minute_bars(TUE, 18000, after_close=5)
    minutes, _ = dh.to_lake_bars(t, dh.MINUTE, raw, AT)
    bar = pdaily.bar_from_minutes(KEY, TUE, minutes)
    cont = [b for b in raw if b.timestamp.astimezone(IST).time() < time(15, 30)]
    assert bar is not None and bar.timeframe == pdaily.TIMEFRAME and bar.ts == dh.session_close(TUE)
    assert (bar.open, bar.high, bar.low, bar.close) == (cont[0].open, max(b.high for b in cont), min(b.low for b in cont), cont[-1].close)
    assert pdaily.bar_from_minutes(KEY, TUE, minutes[:200]) is None              # stopped mid-session: no bar
    assert pdaily.bar_from_minutes(KEY, TUE, minutes[30:]) is None               # started late: no bar
    assert pdaily.bar_from_minutes(KEY, TUE, []) is None
    assert pdaily.how_of(pdaily.source_for(pdaily.FROM_BROKER_DAY, 7, "upstox")) == pdaily.FROM_BROKER_DAY
    assert pdaily.how_of(pdaily.source_for(pdaily.FROM_BROKER_MINUTES, 7, "upstox")) == pdaily.FROM_BROKER_MINUTES
    assert pdaily.how_of(pdaily.source_for(pdaily.FROM_STORED_MINUTES, 7)) == pdaily.FROM_STORED_MINUTES
    assert all(len(pdaily.source_for(h, 9999999, "kotak_neo")) <= 30 for h in (pdaily.FROM_BROKER_DAY, pdaily.FROM_BROKER_MINUTES))


# --- the pass ---------------------------------------------------------------------------------------------------------------------
def test_one_bar_per_index_in_the_owners_private_store_and_an_idempotent_rerun(monkeypatch):
    owner = Owner("pd-own@example.com", monkeypatch, indices=("NIFTY",))
    broker = Broker(TUE, 18000)                                  # Upstox-like: no "today" on the historical endpoint
    report = _pass(owner, broker)
    assert report.status == "ok" and report.written == {KEY: pdaily.FROM_BROKER_MINUTES} and report.complete, report.lines()
    rows = _prov_rows(owner.tenant_id)
    assert len(rows) == 1 and rows[0].source == f"broker:upstox-1m:u{owner.user.id}" and rows[0].ts.replace(tzinfo=UTC) == dh.session_close(TUE)
    cont = [b for b in minute_bars(TUE, 18000) if b.timestamp.astimezone(IST).time() < time(15, 30)]
    assert float(rows[0].close) == cont[-1].close                # the last continuous price, not an auction print
    assert _count(MdCandleRecord, MdCandleRecord.source.like(f"broker:%:u{owner.user.id}")) == 0    # never the shared lake
    calls = len(broker.calls)
    again = _pass(owner, broker)
    assert again.already == [KEY] and not again.written and again.complete and len(broker.calls) == calls
    assert len(_prov_rows(owner.tenant_id)) == 1


def test_the_brokers_daily_candle_is_used_when_it_serves_today(monkeypatch):
    owner = Owner("pd-kite@example.com", monkeypatch, indices=("NIFTY",))
    report = _pass(owner, Broker(TUE, 18100, daily_today=True))
    assert report.written == {KEY: pdaily.FROM_BROKER_DAY}
    row = _prov_rows(owner.tenant_id)[0]
    assert row.source == f"broker:upstox:u{owner.user.id}" and float(row.close) == 18133.0 and float(row.high) == 18190.0


def test_without_a_session_the_stored_minutes_else_no_bar(monkeypatch):
    owner = Owner("pd-nosession@example.com", monkeypatch, indices=("NIFTY",), credential=False)
    empty = _pass(owner, Broker(TUE, 1))
    assert empty.no_data == [KEY] and not empty.written and not empty.complete and _prov_rows(owner.tenant_id) == []
    t = dh.target("NIFTY")
    minutes, _ = dh.to_lake_bars(t, dh.MINUTE, minute_bars(TUE, 18200), AT)
    _add(*[R(tenant_id=owner.tenant_id, instrument_key=KEY, timeframe="1m", ts=b.ts, source=f"broker:upstox:u{owner.user.id}", version=1,
             open=b.open, high=b.high, low=b.low, close=b.close, volume=0) for b in minutes])
    report = _pass(owner, Broker(TUE, 1))
    assert report.written == {KEY: pdaily.FROM_STORED_MINUTES} and report.complete
    row = _prov_rows(owner.tenant_id)[0]
    assert row.source == f"broker:store1m:u{owner.user.id}" and float(row.close) == minutes[-1].close


def test_a_broker_error_falls_back_to_the_stored_minutes_and_is_scrubbed(monkeypatch):
    owner = Owner("pd-fail@example.com", monkeypatch, indices=("NIFTY",))
    report = _pass(owner, Broker(TUE, 1, fail=True))
    assert report.no_data == [KEY] and all("abcdefghijklmnopqrstuvwxyz" not in n for n in report.notes)


def test_no_bar_on_a_holiday_a_weekend_or_once_nse_is_in(monkeypatch):
    owner = Owner("pd-holiday@example.com", monkeypatch, indices=("FINNIFTY", "BANKNIFTY"))
    holiday = date(2023, 6, 28)
    _add(MarketHolidayRecord(exchange="NSE", holiday_date=holiday, description="provisional test holiday"))
    broker = Broker(holiday, 18000)
    report = _pass(owner, broker, now=datetime.combine(holiday, time(16, 0), tzinfo=IST))
    assert report.status == "holiday" and report.complete and broker.calls == []
    sat = date(2023, 5, 20)
    assert _pass(owner, Broker(sat, 1), now=datetime.combine(sat, time(16, 0), tzinfo=IST)).status == "holiday"
    early = _pass(owner, broker, now=datetime(2023, 5, 17, 15, 20, tzinfo=IST))
    assert early.status == "not_due" and _prov_rows(owner.tenant_id) == []
    wed = date(2023, 5, 17)
    _add(_nse("NSE_INDEX:NIFTYFINSERVICE", wed, 18050.0))       # a key no other test reads from the shared lake
    report = _pass(owner, Broker(wed, 18000), now=datetime.combine(wed, time(16, 0), tzinfo=IST))
    assert report.nse_in == ["NSE_INDEX:NIFTYFINSERVICE"] and set(report.written) == {"NSE_INDEX:NIFTYBANK"}


# --- the reads ------------------------------------------------------------------------------------------------------------------
def test_reads_append_it_after_the_last_stored_bar_for_the_holder_only_and_nse_supersedes_it(monkeypatch):
    from app.charts import price_action_layer as pal
    owner = Owner("pd-read@example.com", monkeypatch, indices=("NIFTY",))
    mate = _teammate(owner.user, "pd-read-mate@example.com")
    other = _user("pd-read-other@example.com")
    key = "NSE_INDEX:PROVREADTEST"
    days = [date(2023, 5, 1) + timedelta(days=i) for i in range(12) if (date(2023, 5, 1) + timedelta(days=i)).weekday() < 5]
    _add(*[_nse(key, d, 18000 + i) for i, d in enumerate(days)])          # NSE files to Fri 12 May
    _add(_prov(owner.tenant_id, owner.user.id, key, MON, 18500.5),
         _prov(owner.tenant_id, owner.user.id, key, days[3], 1.0))         # an old provisional day NSE already holds
    lo, hi = datetime(2023, 4, 1, tzinfo=UTC), datetime(2023, 5, 15, 16, 0, tzinfo=IST).astimezone(UTC)

    async def reads(scope):
        async with _session_factory() as session:
            with_p, src = await pal.load_frame(session, key, "1d", lo, hi, private=scope, provisional=True)
            without, src0 = await pal.load_frame(session, key, "1d", lo, hi, private=scope)
            weekly, _ = await pal.load_frame(session, key, "1w", lo, hi, private=scope, provisional=True)
            close = await own_bars.official_close(session, key, scope)
            return with_p, src, without, src0, weekly, close
    with_p, src, without, src0, weekly, close = _run(reads(owner.scope))
    assert src == "lake:day+provisional" and src0 == "lake:day"
    assert len(with_p) == len(without) + 1 and float(with_p["close"].iloc[-1]) == 18500.5
    assert list(with_p["close"].iloc[:-1]) == list(without["close"])         # no stored bar is replaced (the old one is not read)
    assert float(weekly["close"].iloc[-1]) == 18000 + len(days) - 1          # the week still trading is not a Weekly bar
    assert close == {"close": 18500.5, "date": MON.isoformat(), "provisional": True,
                     "source": "built from your broker's 1-minute bars; NSE's index close file is not in yet"}

    async def others():
        async with _session_factory() as session:
            out = []
            for u in (mate, other):
                scope = await own_bars.scope_for(session, u, key)
                f, s = await pal.load_frame(session, key, "1d", lo, hi, private=scope, provisional=True)
                out.append((scope, s, float(f["close"].iloc[-1]), await own_bars.official_close(session, key, scope)))
            shared = own_bars.SharedScope(owner.tenant_id, owner.user.id)
            out.append(await own_bars.provisional_rows(session, shared, key, lo, hi))
            return out
    mate_r, other_r, shared_rows = _run(others())
    for scope, s, last, oc in (mate_r, other_r):
        assert scope is None and s == "lake:day" and last == 18000 + len(days) - 1 and not oc.get("provisional")
    assert shared_rows == []                                                   # never through the shared index scope

    _add(_nse(key, MON, 18490.0))                                             # NSE's file of Monday lands
    with_p, src, without, src0, weekly, close = _run(reads(owner.scope))
    assert src == "lake:day" and float(with_p["close"].iloc[-1]) == 18490.0 and len(with_p) == len(without)
    assert close == {"close": 18490.0, "date": MON.isoformat(), "source": "NSE index close file"}


def test_the_store_read_the_copilot_uses_serves_it_until_nse_lands(monkeypatch):
    from app.market_store import reader
    owner = Owner("pd-store@example.com", monkeypatch, indices=("NIFTY",))
    sym = "PROVSTORETEST"
    key = f"NSE:{sym}"
    days = [date(2023, 5, 8) + timedelta(days=i) for i in range(5)]
    _add(*[MdCandleRecord(instrument_key=key, timeframe="day", ts=dh.session_close(d), source="nse_bhav:cm", version=1, open=100, high=110,
                          low=90, close=100 + i, volume=10) for i, d in enumerate(days)])
    _add(_prov(owner.tenant_id, owner.user.id, key, MON, 222.5, pdaily.FROM_STORED_MINUTES))
    now = datetime(2023, 5, 15, 16, 0, tzinfo=IST).astimezone(UTC)

    async def read(scope):
        async with _session_factory() as session:
            return await reader.read_eod(session, sym, "NSE", "1d", datetime(2023, 5, 1, tzinfo=UTC), now, now=now, holidays=set(),
                                         provisional=scope)
    got, plain = _run(read(owner.scope)), _run(read(None))
    assert got.source == "lake:daily+provisional" and float(got.frame["close"].iloc[-1]) == 222.5 and not any(g.kind == "newest" for g in got.gaps)
    assert plain.source == "lake:daily" and float(plain.frame["close"].iloc[-1]) == 104 and len(plain.frame) == len(got.frame) - 1
    _add(MdCandleRecord(instrument_key=key, timeframe="day", ts=dh.session_close(MON), source="nse_bhav:cm", version=1, open=100, high=110,
                        low=90, close=220.0, volume=10))
    after = _run(read(owner.scope))
    assert after.source == "lake:daily" and float(after.frame["close"].iloc[-1]) == 220.0 and len(after.frame) == len(got.frame)


def test_the_copilot_never_calls_a_provisional_close_official():
    official = {"close": 25123.45, "date": "2026-11-03", "source": "NSE index close file"}
    prov = {"close": 25100.0, "date": "2026-11-03", "provisional": True, "source": "built from your broker's 1-minute bars; NSE's index close file is not in yet"}
    assert "(official close, NSE index close file)" in chat._close_head("NIFTY", {"official_close": official}, "1d")
    head = chat._close_head("NIFTY", {"official_close": prov}, "1d")
    assert "provisional close" in head and "official" not in head
    zones = ToolResult(True, {"bands": []}, "x", "market_zones", {})
    for oc, label in ((official, "Last close"), (prov, "Provisional close")):
        res = ToolResult(True, {"official_close": oc, "last_close": 1.0, "chart_state": {}, "timeframes": []}, "x", "price_action_layer", {})
        assert chat.chart_headline("NIFTY", "1d", res, zones)["price_label"] == label


# --- NSE's files earlier ----------------------------------------------------------------------------------------------------------
def test_the_days_files_are_done_only_with_both_cash_and_index():
    day = date(2023, 6, 13)
    _add(HistoryBackfillFileRecord(segment="cm", trade_date=day, status="done", attempts=1, inserted=0, corrected=0, skipped=0, quality_events=0))
    assert _run(hb1.day_files_done(_session_factory, day)) is False
    _add(HistoryBackfillFileRecord(segment="index", trade_date=day, status="no_file", attempts=1, inserted=0, corrected=0, skipped=0,
                                   quality_events=0))
    assert _run(hb1.day_files_done(_session_factory, day)) is False

    async def done():
        async with _session_factory() as session:
            row = await session.scalar(select(HistoryBackfillFileRecord).where(HistoryBackfillFileRecord.segment == "index",
                                                                               HistoryBackfillFileRecord.trade_date == day))
            row.status = "done"
            await session.commit()
    _run(done())
    assert _run(hb1.day_files_done(_session_factory, day)) is True


def test_early_tries_from_the_first_try_time_every_retry_until_the_cutoff(monkeypatch):
    monkeypatch.setattr(config, "HISTORY_BACKFILL_FIRST_TRY_IST", time(17, 30))
    monkeypatch.setattr(config, "HISTORY_BACKFILL_RETRY_MINUTES", 30)
    monkeypatch.setattr(config, "HISTORY_BACKFILL_HOUR_IST", 19)
    t = lambda h, m: datetime(2023, 5, 16, h, m, tzinfo=IST)               # noqa: E731
    assert hb1.early_due(t(17, 0), None, None) is None
    assert hb1.early_due(t(17, 30), None, None) == TUE
    assert hb1.early_due(t(17, 50), None, t(17, 30)) is None                 # inside the retry interval
    assert hb1.early_due(t(18, 0), None, t(17, 30)) == TUE
    assert hb1.early_due(t(18, 0), TUE, t(17, 30)) is None                   # the day's files are done
    assert hb1.early_due(t(19, 5), None, t(18, 30)) is None                  # from the cut-off: the night run (nightly_due)
    assert hb1.nightly_due(t(19, 5), None) == TUE
    assert hb1.early_due(t(17, 45), None, datetime(2023, 5, 15, 18, 0, tzinfo=IST)) == TUE   # yesterday's try does not count
    monkeypatch.setattr(config, "HISTORY_BACKFILL_FIRST_TRY_IST", None)
    assert hb1.early_due(t(18, 0), None, None) is None


def _hb1_flag(on: bool):
    from app.platform import controls

    async def go():
        async with _session_factory() as session:
            current = await controls._get(session, controls.KEY_FEATURE_FLAGS)
            flags = current.get("flags") or {}
            flags[hb1.FLAG] = {"on": on, "tenants": []}
            await controls._set(session, controls.KEY_FEATURE_FLAGS, {"flags": flags}, None)
            await session.commit()
    _run(go())


def test_the_worker_tries_nse_early_until_the_days_files_are_done(monkeypatch):
    from app.workers.trading_worker import TradingWorker
    monkeypatch.setattr(config, "HISTORY_BACKFILL_FIRST_TRY_IST", time(17, 30))
    monkeypatch.setattr(config, "HISTORY_BACKFILL_RETRY_MINUTES", 30)
    monkeypatch.setattr(config, "HISTORY_BACKFILL_HOUR_IST", 19)
    day = date(2023, 6, 6)
    started: list[datetime] = []
    land: dict[str, bool] = {"files": False}

    async def fake_nightly(factory, fetcher, *, now=None):
        started.append(now)
        return hb1.BackfillSummary()

    async def files_done(factory, d):
        return land["files"] and d == day

    class FakeFetcher:
        async def aclose(self):
            pass
    monkeypatch.setattr(hb1, "run_nightly", fake_nightly)
    monkeypatch.setattr(hb1, "day_files_done", files_done)
    monkeypatch.setattr("app.market_lake.nse_fetch.PoliteFetcher", FakeFetcher)
    _hb1_flag(True)
    worker = TradingWorker(_session_factory)

    def turn(h, m, d=day):
        async def go():
            async with _session_factory() as session:
                ok = await worker._maybe_start_history_eod(session, datetime.combine(d, time(h, m), tzinfo=IST))
            if worker._history_eod_task is not None:
                await worker._history_eod_task
            return ok
        return _run(go())
    assert turn(17, 0) is False and started == []
    assert turn(17, 35) is True and worker._last_history_eod_day is None         # not in yet: tried again later
    assert turn(17, 50) is False and len(started) == 1
    land["files"] = True
    assert turn(18, 5) is True and worker._last_history_eod_day == day          # in: no more tries today
    assert turn(18, 40) is False and turn(19, 30) is False and len(started) == 2
    sat = date(2023, 6, 10)
    assert turn(17, 45, sat) is False and len(started) == 2                       # a weekend: only the night run, as before
    assert turn(19, 30, sat) is True and worker._last_history_eod_day == sat


def test_the_worker_writes_the_provisional_bar_from_the_configured_time_and_retries_an_unfinished_day(monkeypatch):
    from app.workers.trading_worker import TradingWorker
    Owner("pd-worker@example.com", monkeypatch, indices=("NIFTY",))
    monkeypatch.setattr(config, "DAILY_PROVISIONAL_AT_IST", time(15, 45))
    monkeypatch.setattr(config, "HISTORY_BACKFILL_RETRY_MINUTES", 30)
    monkeypatch.setattr(config, "HISTORY_BACKFILL_HOUR_IST", 19)
    seen: list[datetime] = []
    result = {"no_data": ["x"]}

    async def fake_pass(factory, *, now=None, **kw):
        seen.append(now)
        return pdaily.ProvisionalReport(day=now.astimezone(IST).date(), no_data=list(result["no_data"]))
    monkeypatch.setattr(pdaily, "run_pass", fake_pass)
    worker = TradingWorker(_session_factory)
    day = date(2023, 6, 7)

    def turn(h, m):
        async def go():
            async with _session_factory() as session:
                ok = await worker._maybe_start_provisional_daily(session, datetime.combine(day, time(h, m), tzinfo=IST))
            if worker._provisional_task is not None:
                await worker._provisional_task
            return ok
        return _run(go())
    assert turn(15, 40) is False and seen == []
    assert turn(15, 46) is True and worker._last_provisional_day is None         # an index had no data yet
    assert turn(16, 0) is False and len(seen) == 1
    result["no_data"] = []
    assert turn(16, 20) is True and worker._last_provisional_day == day
    assert turn(17, 0) is False and len(seen) == 2
