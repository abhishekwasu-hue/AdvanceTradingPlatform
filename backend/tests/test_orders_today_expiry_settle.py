"""Owner bug report (Terminal > Orders, PAPER): an order of an earlier day sat in today's Executed tab, and a PAPER option
that had expired was still "Filled, position open".

A. `GET /api/orders?today=true` (the Terminal's Orders tab) lists only the orders placed since the start of the current IST
   trading day; without it the answer is unchanged (the full ledger).
B. app/paper/expiry_settlement.py settles an open PAPER index option / future past its expiry close at its intrinsic value
   against the stored expiry-day close, reason "Expired - settled", exit time at that close; LIVE is never touched; a
   second pass changes nothing; a bar after the expiry day is never read (no look-ahead). Fake lake data only.
"""
import asyncio
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import delete

from app.db.models import MdCandleRecord, TradeRecord, User
from app.market_data.calendar import IST, trading_day_start
from app.market_lake import ingest
from app.market_lake.bhavcopy_backfill import bar_end
from app.paper import expiry_settlement as X
from app.terminal import status as S
from tests.test_auth_api import _session_factory, client
from tests.test_terminal_t1 import _order, _trade, _who

NIFTY = "NSE_INDEX:NIFTY50"
EXPIRY = date(2031, 11, 4)            # far from every other test's lake rows
SYMBOL_PE = "NIFTY 22550 PE 04 NOV 31"
SYMBOL_CE = "NIFTY 22800 CE 04 NOV 31"
CLOSE = 22400.0                       # the stored expiry-day close: the PE is 150 in the money, the CE is out of it
AFTER = datetime(2031, 11, 12, 5, 0, tzinfo=UTC)     # days later (10:30 IST), when the stale position is found


def _run(coro):
    return asyncio.run(coro)


def _bars(rows: list[tuple[date, float]]) -> None:
    async def go():
        async with _session_factory() as session:
            await session.execute(delete(MdCandleRecord).where(MdCandleRecord.instrument_key == NIFTY,
                                                               MdCandleRecord.ts >= bar_end(EXPIRY - timedelta(days=3)),
                                                               MdCandleRecord.ts <= bar_end(EXPIRY + timedelta(days=3))))
            bars = [ingest.LakeBar(NIFTY, "day", bar_end(d), c - 10, c + 20, c - 30, c, 1000) for d, c in rows]
            if bars:
                await ingest.write_candles_bulk(session, bars, "nse_bhav:index")
            await session.commit()
    _run(go())


def _option(who: dict, symbol: str = SYMBOL_PE, **over) -> int:
    row = dict(symbol=symbol, instrument_kind="OPTION", exchange="NFO", expiry=EXPIRY, lot_size=65, quantity=65.0, entry_price=100.0,
               stop_loss=0.0, underlying_symbol="NIFTY 50", option_position="BUY", holding="SWING",
               entry_time=datetime(2031, 10, 31, 5, 17, tzinfo=UTC))
    row.update(over)
    return _trade(who, **row)


def _get(trade_id: int) -> TradeRecord:
    async def go():
        async with _session_factory() as session:
            return await session.get(TradeRecord, trade_id)
    return _run(go())


def _settle(who: dict, now: datetime = AFTER) -> X.SettleReport:
    async def go():
        async with _session_factory() as session:
            return await X.settle_expired(session, now, tenant_id=who["tenant_id"])
    return _run(go())


def _utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


# --- A: the Orders tab lists today's orders only ---------------------------------------------------------------------------

def test_the_orders_tab_lists_only_todays_orders_and_the_ledger_is_unchanged():
    who = _who("today")
    start = trading_day_start()
    old = _order(who, symbol="NIFTY 22550 PE 03 NOV 26", created_at=start - timedelta(hours=20))   # an earlier IST day
    new = _order(who, symbol="NIFTY 24000 CE 17 NOV 26", created_at=start + timedelta(minutes=1))
    if datetime.now(UTC) < start + timedelta(minutes=1):                                   # the first minute of the day
        return
    today = {o["id"] for o in client.get("/api/orders", headers=who["headers"], params={"mine": "true", "today": "true"}).json()}
    assert new in today and old not in today
    every = {o["id"] for o in client.get("/api/orders", headers=who["headers"], params={"mine": "true"}).json()}
    assert {old, new} <= every                                     # nothing deleted: the full ledger still has both


def test_the_ist_day_boundary_is_ist_midnight_not_utc_midnight():
    who = _who("boundary")
    start = trading_day_start()
    # 23:50 IST of the day before (18:20 UTC): a UTC-midnight boundary would wrongly list it as today's when UTC's date matches.
    late = _order(who, symbol="LATEORDER", created_at=start - timedelta(minutes=10))
    today = {o["id"] for o in client.get("/api/orders", headers=who["headers"], params={"mine": "true", "today": "true"}).json()}
    assert late not in today


def test_a_filled_order_whose_position_closed_reads_closed_with_its_exit_reason():
    who = _who("closedrow")
    held = _trade(who, symbol="NIFTY 22550 PE 03 NOV 26")
    closed = _trade(who, symbol="NIFTY 22550 PE 03 NOV 26", exit_time=datetime.now(UTC), exit_price=36.95,
                    exit_reason="Premium floor", pnl=-10892.62)
    o_open = _order(who, trade_id=held)
    o_closed = _order(who, trade_id=closed)
    o_none = _order(who, status="REJECTED")
    rows = {o["id"]: o for o in client.get("/api/orders", headers=who["headers"], params={"mine": "true"}).json()}
    assert (rows[o_closed]["status"], rows[o_closed]["position_closed"], rows[o_closed]["exit_reason"]) == ("POSITION_OPEN", True, "Premium floor")
    assert (rows[o_open]["position_closed"], rows[o_open]["exit_reason"]) == (False, None)
    assert (rows[o_none]["position_closed"], rows[o_none]["exit_reason"]) == (None, None)


# --- B: PAPER expiry settlement ---------------------------------------------------------------------------------------------

def test_an_expired_paper_option_settles_at_intrinsic_value_at_the_expiry_close():
    who = _who("settle")
    _bars([(EXPIRY - timedelta(days=1), 22000.0), (EXPIRY, CLOSE)])
    pe = _option(who)
    ce = _option(who, SYMBOL_CE)
    report = _settle(who)
    assert set(report.settled) == {pe, ce}
    t = _get(pe)
    assert t.exit_reason == "Expired - settled"
    assert t.exit_price == 150.0                                   # 22550 - 22400
    assert _utc(t.exit_time) == datetime(2031, 11, 4, 10, 0, tzinfo=UTC)   # 15:30 IST on the expiry day
    assert t.pnl == round((150.0 - 100.0) * 65 - t.charges, 2)
    c = _get(ce)
    assert c.exit_price == 0.0 and c.exit_reason == "Expired - settled"            # out of the money: worth nothing
    assert c.pnl == round(-100.0 * 65 - c.charges, 2)


def test_settlement_is_idempotent():
    who = _who("idem")
    _bars([(EXPIRY, CLOSE)])
    pe = _option(who)
    assert _settle(who).settled == [pe]
    first = _get(pe)
    assert _settle(who).settled == []
    again = _get(pe)
    assert (again.exit_price, again.pnl, _utc(again.exit_time), again.exit_reason) == (
        first.exit_price, first.pnl, _utc(first.exit_time), first.exit_reason)


def test_a_live_position_is_never_touched():
    who = _who("live")
    _bars([(EXPIRY, CLOSE)])
    live = _option(who, mode="LIVE")
    assert live not in _settle(who).settled
    t = _get(live)
    assert t.exit_time is None and t.exit_price is None and t.exit_reason is None


def test_no_look_ahead_only_the_expiry_days_bar_is_read():
    who = _who("lookahead")
    _bars([(EXPIRY + timedelta(days=1), 99999.0)])                  # only a LATER day's bar is stored
    pe = _option(who)
    report = _settle(who)
    assert pe not in report.settled and (pe, "expiry-day close not stored yet") in report.waiting
    assert _get(pe).exit_time is None
    # Before the expiry day's close the contract is not due at all, even with that day's bar stored.
    _bars([(EXPIRY, CLOSE)])
    before_close = datetime(2031, 11, 4, 9, 55, tzinfo=UTC)    # 15:25 IST
    assert _settle(who, before_close).settled == [] and _get(pe).exit_time is None
    assert _settle(who).settled == [pe] and _get(pe).exit_price == 150.0


def test_a_stock_option_is_left_to_delivery_and_an_unexpired_one_stays_open():
    who = _who("stock")
    _bars([(EXPIRY, CLOSE)])
    stock = _option(who, "RELIANCE 1500 CE 04 NOV 31", underlying_symbol="RELIANCE")
    later = _option(who, "NIFTY 22550 PE 18 NOV 31", expiry=date(2031, 11, 18))
    report = _settle(who)
    assert report.settled == []
    assert (stock, "stock F&O settles by delivery") in report.waiting
    assert _get(stock).exit_time is None and _get(later).exit_time is None


def test_the_today_strip_counts_the_settlement_on_the_expiry_day_not_on_a_later_day():
    who = _who("strip")
    _bars([(EXPIRY, CLOSE)])
    pe = _option(who)
    _settle(who)
    booked = _get(pe).pnl

    async def strip(now: datetime) -> dict:
        async with _session_factory() as session:
            user = await session.get(User, who["user_id"])
            return (await S.terminal_status(session, user, now))["today"]
    on_expiry_evening = datetime(2031, 11, 4, 13, 0, tzinfo=UTC)      # 18:30 IST on the expiry day
    assert _run(strip(on_expiry_evening))["booked"] == booked
    assert _run(strip(AFTER))["booked"] == 0.0
    assert on_expiry_evening.astimezone(IST).date() == EXPIRY


def test_settlement_value_rules():
    assert X.settlement_value("CE", 22000.0, 22150.5) == 150.5
    assert X.settlement_value("CE", 22200.0, 22150.5) == 0.0
    assert X.settlement_value("PE", 22200.0, 22150.5) == 49.5
    assert X.settlement_value("PE", 22000.0, 22150.5) == 0.0
    assert X.settlement_value("FUT", None, 22150.5) == 22150.5
    assert X.settlement_value("CE", None, 22150.5) is None
