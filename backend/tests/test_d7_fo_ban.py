"""Part D7 (rule IN-SEBI.risk.futeq_mwpl): new F&O entries on an underlying in the exchange's ban period are refused.

- open interest at or above `ban_threshold_pct` of MWPL (rule-set data) -> refusal naming the share; below it -> allowed;
- no lake row for the day -> no refusal, but a note says the check could not run (never silent);
- the newest visible version of the day's row counts (B1 as-of);
- FO_BAN_CHECK_ENABLED off (the default) -> nothing changes; on -> a PAPER option entry on a banned underlying is
  REJECTED before any order, the same entry on a non-banned day fills.
"""
import asyncio
from datetime import timedelta

from sqlalchemy import delete

from app.compliance import fo_limits, rules
from app.core import config
from app.db.models import MdPositionLimitRecord
from app.market_data.calendar import IST
from tests.test_auth_api import _session_factory
from tests.test_contract_execution import BUY, _execute, _FnoBroker, _load_master, _resolve, _signal, _user


def _run(coro):
    return asyncio.run(coro)


def _limits(underlying, day, oi_pct, *, version=1, ingested_at=None):
    mwpl = 1_000_000
    async def go():
        async with _session_factory() as session:
            if version == 1:
                await session.execute(delete(MdPositionLimitRecord).where(MdPositionLimitRecord.underlying == underlying))
            session.add(MdPositionLimitRecord(underlying=underlying, trade_date=day, source="test", version=version, mwpl=mwpl,
                                              open_interest=int(mwpl * oi_pct / 100), **({"ingested_at": ingested_at} if ingested_at else {})))
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
    refusal, note = _status("D7TEST", when + timedelta(days=1))
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
