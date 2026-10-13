"""Part D7, the rest (rule IN-SEBI.risk.futeq_mwpl; platform flag fo_position_limits, off by default).

- client-level position limit: the higher of `client_limit_mwpl_pct` % of MWPL and `client_limit_oi_pct` % of open
  interest, less the caller's own open F&O quantity on the underlying - a cap on the new quantity in whole lots, a
  refusal when not one lot fits; no lake row -> a note, never silent;
- lot size as of the series: an order sized on an instrument-master lot that differs from the exchange's lot for that
  series (the dated lot table, by expiry) is refused (a stale master);
- expiry-day margin: a short option on its own expiry day carries `expiry_day_margin_multiplier` (rule-set data; 1 =
  none) in the LIVE margin sizing;
- flag off: nothing changes. Every check is on NEW entries; exits are never refused (ADR-0004).
"""
import asyncio
from datetime import datetime, time, timedelta, timezone

from sqlalchemy import delete

from app.compliance import fo_limits, rules
from app.db.models import MdPositionLimitRecord
from app.market_data.calendar import IST
from app.platform.controls import DEFAULT_OFF_FLAGS
from tests.master_fixture import NIFTY_LOT
from tests.test_auth_api import _session_factory
from tests.test_contract_execution import BUY, _execute, _FnoBroker, _load_master, _resolve, _signal, _user
from tests.test_n1_e1_execution_core import _flag


def _run(coro):
    return asyncio.run(coro)


def _limits(underlying, day, *, mwpl, oi, trade_date=None, ingested_at=None):
    """The limits that apply on `day`: the previous session's EOD row, ingested that evening (before any decision on `day`)."""
    trade_date = trade_date or (day - timedelta(days=3 if day.weekday() == 0 else 1))
    ingested_at = ingested_at or datetime.combine(trade_date, time(14, 0), tzinfo=timezone.utc)

    async def go():
        async with _session_factory() as session:
            await session.execute(delete(MdPositionLimitRecord).where(MdPositionLimitRecord.underlying == underlying))
            session.add(MdPositionLimitRecord(underlying=underlying, trade_date=trade_date, source="test", version=1, mwpl=mwpl,
                                              open_interest=oi, ingested_at=ingested_at))
            await session.commit()
    _run(go())


def _fresh_lot(monkeypatch):
    """The fixture master's lot is the current one (the dated table would call it stale)."""
    monkeypatch.setattr(fo_limits, "dated_lot", lambda underlying, expiry: NIFTY_LOT)


def test_the_flag_is_off_by_default_and_off_changes_nothing(monkeypatch):
    assert fo_limits.FLAG in DEFAULT_OFF_FLAGS
    _load_master()
    user = _user("d7r-off@example.com")
    signal = _signal()
    _limits(signal.symbol.upper(), signal.timestamp.astimezone(IST).date(), mwpl=100, oi=10)        # room for nothing
    result, order, trade = _execute(user, mode="PAPER", contract=_resolve(BUY), rules=BUY, quote_broker=_FnoBroker(premium=120.0))
    assert result.executed, result.reasons                                     # off: the stale lot and the limit are not checked


def test_the_client_limit_caps_new_quantity_and_refuses_when_no_lot_fits(monkeypatch):
    _load_master()
    _fresh_lot(monkeypatch)
    user = _user("d7r-cap@example.com")
    _flag(fo_limits.FLAG, user.tenant_id)
    signal = _signal()
    day = signal.timestamp.astimezone(IST).date()
    underlying = signal.symbol.upper()
    ce = _resolve(BUY)
    pct = float(rules.load().param(fo_limits.RULE, "client_limit_mwpl_pct"))
    mwpl = int(NIFTY_LOT * 3.5 * 100 / pct)                                     # the limit is 3.5 lots
    _limits(underlying, day, mwpl=mwpl, oi=0)
    result, order, trade = _execute(user, mode="PAPER", contract=ce, rules=BUY, quote_broker=_FnoBroker(premium=120.0))
    assert result.executed, result.reasons
    assert trade.quantity == 3 * NIFTY_LOT                                       # capped to whole lots under the limit
    nxt = signal.model_copy(update={"timestamp": signal.timestamp + timedelta(minutes=1)})
    result, order, trade = _execute(user, mode="PAPER", contract=ce, rules=BUY, quote_broker=_FnoBroker(premium=120.0), signal=nxt)
    assert not result.executed and order.status == "REJECTED" and trade is None
    assert any("Client position limit" in r and "exits allowed" in r for r in result.reasons)
    # the higher of the two bases counts: a large open interest raises the room again
    oi_pct = float(rules.load().param(fo_limits.RULE, "client_limit_oi_pct"))
    _limits(underlying, day, mwpl=mwpl, oi=int(NIFTY_LOT * 10 * 100 / oi_pct))
    later = signal.model_copy(update={"timestamp": signal.timestamp + timedelta(minutes=2)})
    result, order, trade = _execute(user, mode="PAPER", contract=ce, rules=BUY, quote_broker=_FnoBroker(premium=120.0), signal=later)
    assert result.executed and trade.quantity <= 7 * NIFTY_LOT                   # 10 lots allowed, 3 held


def test_no_lake_row_is_a_note_and_a_stale_lot_is_refused(monkeypatch):
    _load_master()
    user = _user("d7r-lot@example.com")
    _flag(fo_limits.FLAG, user.tenant_id)
    ce = _resolve(BUY)
    expected = fo_limits.dated_lot(ce.underlying, ce.expiry)
    assert expected is not None and expected != ce.lot_size                      # the fixture master predates the revision
    result, order, trade = _execute(user, mode="PAPER", contract=ce, rules=BUY, quote_broker=_FnoBroker(premium=120.0))
    assert not result.executed and any("refresh the instrument master" in r for r in result.reasons)
    unknown = type("C", (), {"underlying": "NOSUCH", "expiry": ce.expiry, "lot_size": 1, "tradingsymbol": "X"})()
    assert fo_limits.lot_refusal(unknown)[0] is None and "No dated lot size" in fo_limits.lot_refusal(unknown)[1]
    _fresh_lot(monkeypatch)

    async def wipe():
        async with _session_factory() as session:
            await session.execute(delete(MdPositionLimitRecord).where(MdPositionLimitRecord.underlying == _signal().symbol.upper()))
            await session.commit()
    _run(wipe())
    cap, refusal, note = _run(_cap(user))
    assert cap is None and refusal is None and "not checked" in note


async def _cap(user):
    async with _session_factory() as session:
        return await fo_limits.client_limit_cap(session, tenant_id=user.tenant_id, user_id=user.id, underlying=_signal().symbol,
                                                mode="PAPER", lot=NIFTY_LOT, when=_signal().timestamp)


def test_expiry_day_margin_applies_to_short_legs_on_their_own_expiry_day(monkeypatch):
    from app.execution import multileg
    real = rules.load()
    monkeypatch.setattr(fo_limits.rules, "load", lambda: type("R", (), {"param": lambda self, rule, name, default=None:
                                                                         1.5 if name == "expiry_day_margin_multiplier" else real.param(rule, name, default)})())
    now = datetime.now(timezone.utc)
    today = now.astimezone(IST).date()
    assert fo_limits.expiry_day_factor(today, now) == 1.5
    assert fo_limits.expiry_day_factor(today + timedelta(days=1), now) == 1.0
    assert fo_limits.expiry_day_factor(None, now) == 1.0
    assert fo_limits.expiry_day_factor(today, None) == 1.0                     # flag off: callers pass no time
    # the SIGNAL's time decides, not the wall clock: a signal of the day before expiry is not on expiry day
    assert fo_limits.expiry_day_factor(today, now - timedelta(days=1)) == 1.0

    class _Leg:
        def __init__(self, expiry):
            self.contract = type("K", (), {"instrument_key": "NFO|X", "tradingsymbol": "NIFTYX", "exchange": "NFO", "expiry": expiry})()
            self.ratio = 1

    class _S:
        def __init__(self, expiry):
            self.short_legs = [_Leg(expiry)]
            self.lot_size = NIFTY_LOT
    broker = _FnoBroker(margin_per_lot=50_000.0, available=1_000_000.0)
    later, _ = _run(multileg._live_lot_cap(broker, _S(today + timedelta(days=7)), expiry_margin_at=now))
    on_the_day, note = _run(multileg._live_lot_cap(broker, _S(today), expiry_margin_at=now))
    assert on_the_day < later and "75,000" in note                              # 50,000 x 1.5 per lot on expiry day
    flag_off, note_off = _run(multileg._live_lot_cap(broker, _S(today)))       # flag off: no multiplier
    assert flag_off == later and "50,000" in note_off


def test_the_expiry_day_margin_follows_the_flag(monkeypatch):
    user = _user("d7r-expflag@example.com")
    when = _signal().timestamp

    async def at():
        async with _session_factory() as session:
            return await fo_limits.expiry_margin_at(session, user.tenant_id, when)
    assert _run(at()) is None                                                   # off by default: the multiplier never applies
    _flag(fo_limits.FLAG, user.tenant_id)
    assert _run(at()) == when                                                   # on: judged at the signal's time


def test_the_client_limit_does_not_look_ahead(monkeypatch):
    user = _user("d7r-look@example.com")
    signal = _signal()
    day = signal.timestamp.astimezone(IST).date()
    underlying = signal.symbol.upper()
    pct = float(rules.load().param(fo_limits.RULE, "client_limit_mwpl_pct"))
    mwpl = int(NIFTY_LOT * 3.5 * 100 / pct)
    _limits(underlying, day, mwpl=mwpl, oi=0, trade_date=day, ingested_at=signal.timestamp - timedelta(hours=1))
    cap, refusal, note = _run(_cap(user))                                       # the day's own row: never for that day
    assert cap is None and refusal is None and "not checked" in note
    _limits(underlying, day, mwpl=mwpl, oi=0, ingested_at=signal.timestamp + timedelta(minutes=1))
    cap, refusal, note = _run(_cap(user))                                       # ingested after the signal: not seen yet
    assert cap is None and "not checked" in note
    _limits(underlying, day, mwpl=mwpl, oi=0)                                   # the previous session's, known in time
    cap, refusal, note = _run(_cap(user))
    assert cap == 3 * NIFTY_LOT and refusal is None
