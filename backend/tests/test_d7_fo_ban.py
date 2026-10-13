"""Part D7 (rule IN-SEBI.risk.futeq_mwpl): new F&O entries on an underlying in the exchange's ban period are refused.

- open interest at or above `ban_threshold_pct` of MWPL (rule-set data) -> refusal naming the share; below it -> allowed;
- no lake row for the day -> no refusal, but a note says the check could not run (never silent);
- the newest visible version of the row counts (B1 as-of);
- no look-ahead: a decision on day D reads the previous session's EOD row (trade_date < D, the exchange publishes a day's
  OI / MWPL after its close) that was ingested at or before the decision time - never D's own row, never a later file;
- FO_BAN_CHECK_ENABLED off (the default) -> nothing changes; on -> a PAPER option entry on a banned underlying is
  REJECTED before any order, the same entry on a non-banned day fills.
"""
import asyncio
from datetime import datetime, time, timedelta, timezone

from sqlalchemy import delete

from app.compliance import fo_limits, rules
from app.core import config
from app.db.models import MdPositionLimitRecord
from app.market_data.calendar import IST
from tests.test_auth_api import _session_factory
from tests.test_contract_execution import BUY, _execute, _FnoBroker, _load_master, _resolve, _signal, _user


def _run(coro):
    return asyncio.run(coro)


def _prev_session(day):
    d = day - timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def _limits(underlying, day, oi_pct, *, version=1, ingested_at=None, trade_date=None):
    """The limits that apply on `day`: the previous session's EOD row (trade_date), ingested that evening - before any
    decision on `day`."""
    mwpl = 1_000_000
    trade_date = trade_date or _prev_session(day)
    ingested_at = ingested_at or datetime.combine(trade_date, time(14, 0), tzinfo=timezone.utc)
    async def go():
        async with _session_factory() as session:
            if version == 1:
                await session.execute(delete(MdPositionLimitRecord).where(MdPositionLimitRecord.underlying == underlying))
            session.add(MdPositionLimitRecord(underlying=underlying, trade_date=trade_date, source="test", version=version, mwpl=mwpl,
                                              open_interest=int(mwpl * oi_pct / 100), ingested_at=ingested_at))
            await session.commit()
    _run(go())


def _status(underlying, when, **kw):
    async def go():
        async with _session_factory() as session:
            return await fo_limits.ban_status(session, underlying, when, **kw)
    return _run(go())


def test_threshold_from_the_rule_set_missing_data_is_a_note_and_versions_count():
    when = _signal().timestamp
    day = when.astimezone(IST).date()
    threshold = rules.load().param(fo_limits.RULE, "ban_threshold_pct")
    _limits("D7TEST", day, threshold + 1)
    refusal, note = _status("d7test", when)
    assert refusal and "F&O ban" in refusal and f"{threshold:g}%" in refusal and note is None
    _limits("D7TEST", day, threshold - 5, version=2)                  # OI fell below: the newer row counts
    assert _status("D7TEST", when) == (None, None)
    refusal, note = _status("D7TEST", when + timedelta(days=fo_limits.MAX_LIMITS_AGE_DAYS + 2))   # too old to rely on
    assert refusal is None and "not checked" in note


def test_flag_off_changes_nothing(monkeypatch):
    when = _signal().timestamp
    _limits("D7FLAG", when.astimezone(IST).date(), 99)
    monkeypatch.setattr(config, "FO_BAN_CHECK_ENABLED", False)

    async def go():
        async with _session_factory() as session:
            return await fo_limits.entry_refusal(session, "D7FLAG", when)
    assert _run(go()) == (None, None)
    monkeypatch.setattr(config, "FO_BAN_CHECK_ENABLED", True)
    assert _run(go())[0] is not None


def test_paper_option_entry_refused_in_the_ban_period_and_fills_otherwise(monkeypatch):
    monkeypatch.setattr(config, "FO_BAN_CHECK_ENABLED", True)
    _load_master()
    user = _user("d7-ban@example.com")
    ce = _resolve(BUY)
    signal = _signal()
    underlying = signal.symbol.upper()
    day = signal.timestamp.astimezone(IST).date()
    _limits(underlying, day, 97)
    result, order, trade = _execute(user, mode="PAPER", contract=ce, rules=BUY, quote_broker=_FnoBroker(premium=120.0))
    assert not result.executed and order.status == "REJECTED" and trade is None
    assert any("F&O ban" in r for r in result.reasons)
    _limits(underlying, day, 40)
    result, order, trade = _execute(user, mode="PAPER", contract=ce, rules=BUY, quote_broker=_FnoBroker(premium=120.0),
                                    signal=signal.model_copy(update={"timestamp": signal.timestamp + timedelta(minutes=1)}))
    assert result.executed, result.reasons


def test_no_look_ahead_the_previous_session_known_at_the_signal_time():
    when = _signal().timestamp                                         # 2026-09-28 10:00 IST
    day = when.astimezone(IST).date()
    threshold = rules.load().param(fo_limits.RULE, "ban_threshold_pct")
    # the day's OWN row (its EOD file, published after the close) is never used for a decision that day
    _limits("D7LOOK", day, threshold + 4, trade_date=day, ingested_at=when - timedelta(hours=1))
    refusal, note = _status("D7LOOK", when)
    assert refusal is None and "not checked" in note
    # the previous session's row counts only once it was ingested: written after the signal -> not seen at the signal
    _limits("D7LOOK", day, threshold + 4, ingested_at=when + timedelta(minutes=5))
    refusal, note = _status("D7LOOK", when)
    assert refusal is None and "not checked" in note
    assert _status("D7LOOK", when, as_of=when + timedelta(minutes=10))[0] is not None    # known by then
    assert _status("D7LOOK", when + timedelta(minutes=10))[0] is not None                 # as_of defaults to the decision time
