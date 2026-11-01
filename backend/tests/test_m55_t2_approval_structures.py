"""M55 T2 (owner order): approval-only mode proposes option / spread deployments instead of skipping them. The proposal
carries the legs resolved by the automatic entry's own resolver; Approve places exactly those legs through the automatic
entry's own order path (PAPER only); Reject or expiry places nothing; an underlying proposal and the exits are unchanged."""
import asyncio
import json
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.ai import monitor, worker_bridge
from app.core.enums import OptionStrategy, SignalDirection
from app.db.models import AiActionRecord, NotificationRecord, StrategyDeploymentRecord, User
from app.execution import approval
from app.execution.multileg import execute_structure
from app.instruments.contracts import ContractRules, resolve_contract
from app.instruments.spreads import resolve_structure
from tests.master_fixture import NIFTY_LOT
from tests.test_auth_api import _session_factory
from tests.test_contract_execution import BIG
from tests.test_contract_rules import TODAY, _load_master
from tests.test_multileg import SPOT, _OptionBroker
from tests.test_trading_worker import OPEN_NOW, _deploy, _force_signal, _get, _signal, _tenant, _trades, _worker
from tests.test_z3_approval_entries import _actions, _flag_for, _orders, quiet_alerts

pytestmark = pytest.mark.usefixtures("no_open_positions_from_earlier_tests")


def _run(coro):
    return asyncio.run(coro)


def _email(tag: str) -> str:
    return f"m55t2-{tag}-{uuid.uuid4().hex[:6]}@example.com"


def _option_dep(t, **fields) -> int:
    dep_id = _deploy(t, symbol="NIFTY 50")

    async def mark():
        async with _session_factory() as session:
            dep = await session.get(StrategyDeploymentRecord, dep_id)
            for key, value in fields.items():
                setattr(dep, key, value)
            await session.commit()
    _run(mark())
    return dep_id


def _spread_dep(t) -> int:
    return _option_dep(t, instrument_kind="OPTION", option_position="WRITE", option_strategy="BULL_PUT_SPREAD", spread_width=2, max_lots=1,
                       target_credit_pct=50.0)


def _setup(monkeypatch, t, broker):
    from app.workers import trading_worker as tw
    _flag_for(t["tenant_id"])
    monkeypatch.setattr(tw, "resolve_structure", lambda session, symbol, rules, strategy, direction, *, spread_width, spot, today, broker="upstox", **kw:
                        resolve_structure(session, symbol, rules, strategy, direction, spread_width=spread_width, spot=SPOT, today=TODAY, broker=broker, **kw))
    monkeypatch.setattr(tw, "resolve_contract", lambda session, symbol, rules, direction, *, spot, today, broker="upstox", **kw:
                        resolve_contract(session, symbol, rules, direction, spot=SPOT, today=TODAY, broker=broker, **kw))

    async def big_risk(tenant_id, session):
        return BIG
    monkeypatch.setattr("app.execution.multileg.get_tenant_risk_config", big_risk)
    monkeypatch.setattr("app.execution.signal_execution.get_tenant_risk_config", big_risk)
    return _worker(monkeypatch, broker)


def _approver_prices(monkeypatch, broker, underlying=100.0):
    """The approver's own price lookup: the underlying's re-quote and each leg's premium (the fake broker's)."""
    async def factory(session, who):
        async def lookup(symbol, exchange="NSE"):
            return underlying if symbol == "RELIANCE" else await broker.get_ltp_for_symbol(symbol, exchange)
        return lookup
    monkeypatch.setattr(worker_bridge, "price_lookup_for_tenant", factory)
    monkeypatch.setattr(worker_bridge, "price_lookup_for_user", factory)


def _decide(action_id, user_id, *, approve: bool, at):
    async def go():
        async with _session_factory() as session:
            row = await session.get(AiActionRecord, action_id)
            user = await session.get(User, user_id)
            row = await monitor.decide(session, row, user, approve=approve, note=None, now=at)
            if not approve:
                return row
            lookup = await worker_bridge.price_lookup_for_user(session, user)
            return await monitor.execute(session, row, user, price_lookup=lookup, now=at)
    return _run(go())


def _expected_spread():
    async def go():
        async with _session_factory() as session:
            rules = ContractRules.from_deployment(StrategyDeploymentRecord(holding="INTRADAY", order_style="MARKET", instrument_kind="OPTION", option_position="WRITE", max_lots=1))
            return await resolve_structure(session, "NIFTY 50", rules, OptionStrategy.BULL_PUT_SPREAD, SignalDirection.LONG,
                                           spread_width=2, spot=SPOT, today=TODAY)
    return _run(go())


def test_a_bull_put_spread_is_proposed_with_both_resolved_legs_and_approve_places_both(monkeypatch):
    _load_master()
    t = _tenant(_email("spread"))
    dep_id = _spread_dep(t)
    broker = _OptionBroker()
    worker = _setup(monkeypatch, t, broker)
    _force_signal(monkeypatch, _signal)

    report = _run(worker.run_cycle(now=OPEN_NOW))
    assert report.signals_executed == 0
    assert _trades(t["tenant_id"]) == [] and _orders(t["tenant_id"]) == [] and broker.placed == []     # nothing placed
    [row] = _actions(t["tenant_id"], "ENTRY")
    assert row.status == "PROPOSED" and row.deployment_id == dep_id
    assert _get(StrategyDeploymentRecord, dep_id).last_error == f"Waiting for approval: entry proposal #{row.id}"
    ev = json.loads(row.evidence_json)
    payload = ev["structure"]
    assert payload["kind"] == approval.STRUCTURE_KIND and payload["strategy"] == "BULL_PUT_SPREAD" and payload["max_lots"] == 1
    # The same resolver with the same inputs as the automatic entry: exactly its legs.
    expected = _expected_spread()
    assert [(leg["role"], leg["tradingsymbol"], leg["strike"], leg["right"], leg["expiry"], leg["side"], leg["ratio"]) for leg in payload["data"]["legs"]] == \
        [(leg.role, leg.contract.tradingsymbol, leg.contract.strike, leg.contract.right, leg.contract.expiry.isoformat(), leg.side.value, leg.ratio)
         for leg in expected.legs]
    short = next(leg for leg in expected.legs if leg.role == "SHORT")
    wing = next(leg for leg in expected.legs if leg.role == "LONG")
    expiry = approval._expiry_words(short.contract.expiry.isoformat())
    sell = f"Sell 1 lot NIFTY {short.contract.strike:g} PE, expiry {expiry}"
    buy = f"Buy 1 lot NIFTY {wing.contract.strike:g} PE, expiry {expiry}"
    assert ev["leg_lines"][:2] == [buy, sell]                                     # the protective wing first, as it is placed
    assert "at most 1" in ev["leg_lines"][2]
    assert sell in row.reason and buy in row.reason
    # The in-app (and Telegram) message carries the legs in the same words.

    async def note():
        async with _session_factory() as session:
            return await session.scalar(select(NotificationRecord).where(NotificationRecord.tenant_id == t["tenant_id"],
                                                                         NotificationRecord.ai_action_id == row.id))
    message = _run(note())
    assert message is not None and sell in message.message and "Bull put spread on" in message.title
    # The inbox card gets the lines.
    from app.signals_feed.service import _from_action
    assert _from_action(row, OPEN_NOW)["legs"] == ev["leg_lines"]

    _approver_prices(monkeypatch, broker)
    done = _decide(row.id, t["user_id"], approve=True, at=OPEN_NOW + timedelta(minutes=2))
    assert done.status == "EXECUTED", done.result
    trades = _trades(t["tenant_id"])
    assert len(trades) == 2 and {tr.leg_role for tr in trades} == {"SHORT", "LONG"} and len({tr.leg_group_id for tr in trades}) == 1
    assert {tr.symbol for tr in trades} == {short.contract.tradingsymbol, wing.contract.tradingsymbol}
    assert all(tr.mode == "PAPER" and tr.deployment_id == dep_id and tr.quantity == NIFTY_LOT for tr in trades)    # max lots 1
    assert done.trade_id in {tr.id for tr in trades}
    orders = _orders(t["tenant_id"])
    assert sorted(o.idempotency_key for o in orders) == [f"approval:{row.id}:L0", f"approval:{row.id}:L1"]
    assert all(o.mode == "PAPER" for o in orders) and broker.placed == []                         # PAPER: no broker order

    # Exits unchanged: the spread's own target closes both legs automatically - no "Exit now?" question.
    broker.prices = {**broker.prices, "24500 PE": 55.0, "24400 PE": 40.0}
    report = _run(worker.run_cycle(now=OPEN_NOW + timedelta(minutes=3)))
    trades = _trades(t["tenant_id"])
    assert report.positions_closed == 2 and all(tr.exit_time is not None and "Spread target" in tr.exit_reason for tr in trades)
    assert _actions(t["tenant_id"], "EXIT_POSITION") == []
    quiet_alerts(t["tenant_id"])


def test_rejecting_or_letting_a_spread_proposal_expire_places_nothing(monkeypatch):
    _load_master()
    t = _tenant(_email("reject"))
    dep_id = _spread_dep(t)
    broker = _OptionBroker()
    worker = _setup(monkeypatch, t, broker)
    _force_signal(monkeypatch, _signal)
    _run(worker.run_cycle(now=OPEN_NOW))
    [row] = _actions(t["tenant_id"], "ENTRY")
    assert _decide(row.id, t["user_id"], approve=False, at=OPEN_NOW + timedelta(minutes=1)).status == "REJECTED"
    assert _trades(t["tenant_id"]) == [] and _orders(t["tenant_id"]) == []

    # The next signal: a fresh proposal, left alone past its window - expired exactly as a single proposal is.
    _force_signal(monkeypatch, lambda: _signal(ts=_signal().timestamp + timedelta(minutes=5)))
    _run(worker.run_cycle(now=OPEN_NOW + timedelta(minutes=6)))
    fresh = [a for a in _actions(t["tenant_id"], "ENTRY") if a.id != row.id]
    assert len(fresh) == 1 and json.loads(fresh[0].evidence_json)["structure"]["kind"] == approval.STRUCTURE_KIND
    later = approval._utc(fresh[0].expires_at) + timedelta(minutes=1)

    async def sweep():
        async with _session_factory() as session:
            return await monitor.expire_stale(session, later, tenant_id=t["tenant_id"])
    assert _run(sweep()) >= 1
    assert _get(AiActionRecord, fresh[0].id).status == "EXPIRED"
    with pytest.raises(ValueError):
        _decide(fresh[0].id, t["user_id"], approve=True, at=later)
    assert _trades(t["tenant_id"]) == [] and _orders(t["tenant_id"]) == [] and broker.placed == []
    assert _get(StrategyDeploymentRecord, dep_id).status == "ACTIVE"
    quiet_alerts(t["tenant_id"])


def test_the_structure_path_refuses_without_an_approved_proposal_of_that_deployment(monkeypatch):
    _load_master()
    t = _tenant(_email("gate"))
    dep_id = _spread_dep(t)
    broker = _OptionBroker()
    worker = _setup(monkeypatch, t, broker)
    _force_signal(monkeypatch, _signal)
    _run(worker.run_cycle(now=OPEN_NOW))
    [row] = _actions(t["tenant_id"], "ENTRY")                       # still PROPOSED
    structure = _expected_spread()

    async def place(origin, action_id, key):
        async with _session_factory() as session:
            user = await session.get(User, t["user_id"])
            return await execute_structure(session, user, mode="PAPER", strategy_id="s", signal=_signal(), structure=structure,
                                           rules=ContractRules.from_deployment(await session.get(StrategyDeploymentRecord, dep_id)),
                                           target_credit_pct=None, stop_credit_pct=None, idempotency_key=key, quote_broker=broker,
                                           deployment_id=dep_id, origin=origin, approval_action_id=action_id)
    for origin, action_id in (("automatic", None), ("automatic", row.id), ("manual", None), ("approval", row.id), ("approval", None)):
        result = _run(place(origin, action_id, f"gate:{uuid.uuid4().hex}"))
        assert not result.executed and "Approval-only mode" in result.reasons[0], (origin, action_id)
    assert _trades(t["tenant_id"]) == []
    quiet_alerts(t["tenant_id"])


def test_a_single_option_deployment_is_proposed_with_its_contract_and_placed_on_it(monkeypatch):
    _load_master()
    t = _tenant(_email("single"))
    dep_id = _option_dep(t, instrument_kind="OPTION", option_position="BUY", expiry_rule="NEAREST", strike_rule="ATM", premium_stop_pct=30.0, max_lots=2)
    broker = _OptionBroker()
    worker = _setup(monkeypatch, t, broker)
    _force_signal(monkeypatch, _signal)
    _run(worker.run_cycle(now=OPEN_NOW))
    assert _trades(t["tenant_id"]) == [] and _orders(t["tenant_id"]) == []
    [row] = _actions(t["tenant_id"], "ENTRY")
    ev = json.loads(row.evidence_json)
    assert ev["structure"]["kind"] == approval.CONTRACT_KIND and len(ev["structure"]["legs"]) == 1
    leg = ev["structure"]["legs"][0]
    assert leg["right"] == "CE" and leg["side"] == "BUY" and "24500 CE" in leg["tradingsymbol"]
    assert ev["leg_lines"][0].startswith("Buy 1 lot NIFTY 24500 CE, expiry ") and "at most 2" in ev["leg_lines"][1]

    _approver_prices(monkeypatch, broker)
    done = _decide(row.id, t["user_id"], approve=True, at=OPEN_NOW + timedelta(minutes=2))
    assert done.status == "EXECUTED", done.result
    [trade] = _trades(t["tenant_id"])
    assert trade.symbol == leg["tradingsymbol"] and trade.instrument_kind == "OPTION" and trade.mode == "PAPER" and trade.deployment_id == dep_id
    assert [o.idempotency_key for o in _orders(t["tenant_id"])] == [f"approval:{row.id}"] and broker.placed == []
    quiet_alerts(t["tenant_id"])


def test_an_underlying_proposal_is_unchanged(monkeypatch):
    t = _tenant(_email("cash"))
    _flag_for(t["tenant_id"])
    _deploy(t)
    _force_signal(monkeypatch, _signal)
    worker = _worker(monkeypatch, _OptionBroker())
    _run(worker.run_cycle(now=OPEN_NOW))
    [row] = _actions(t["tenant_id"], "ENTRY")
    ev = json.loads(row.evidence_json)
    assert "structure" not in ev and "leg_lines" not in ev
    assert row.reason.startswith("Deployment signal: LONG RELIANCE at 100.00 (stop 98.00, target 104.00)")
    assert approval.leg_lines(None) == [] and approval.leg_lines({"kind": "STRUCTURE", "data": {}}) == []
    quiet_alerts(t["tenant_id"])


def test_a_requote_keeps_the_approved_legs(monkeypatch):
    _load_master()
    t = _tenant(_email("requote"))
    _spread_dep(t)
    broker = _OptionBroker()
    worker = _setup(monkeypatch, t, broker)
    _force_signal(monkeypatch, _signal)
    _run(worker.run_cycle(now=OPEN_NOW))
    [row] = _actions(t["tenant_id"], "ENTRY")
    _approver_prices(monkeypatch, broker, underlying=101.0)              # 1% away: beyond the drift limit
    done = _decide(row.id, t["user_id"], approve=True, at=OPEN_NOW + timedelta(minutes=2))
    assert done.status == "EXPIRED" and "Re-quoted as proposal" in done.result
    assert _trades(t["tenant_id"]) == [] and _orders(t["tenant_id"]) == []
    fresh = [a for a in _actions(t["tenant_id"], "ENTRY") if a.id != row.id]
    assert len(fresh) == 1 and json.loads(fresh[0].evidence_json)["structure"] == json.loads(row.evidence_json)["structure"]
    quiet_alerts(t["tenant_id"])


def test_leg_words_for_ratio_and_future_legs():
    lines = approval.leg_lines({"kind": "STRUCTURE", "max_lots": None, "data": {"legs": [
        {"side": "SELL", "ratio": 2, "underlying": "NIFTY", "strike": 24600.0, "right": "CE", "expiry": "2026-09-30", "kind": "OPTION"},
        {"side": "BUY", "ratio": 1, "underlying": "NIFTY", "strike": 24500.0, "right": "CE", "expiry": "2026-09-30", "kind": "OPTION"}]}})
    assert lines == ["Buy 1 lot NIFTY 24500 CE, expiry 30 Sep 2026", "Sell 2 lots NIFTY 24600 CE, expiry 30 Sep 2026",
                     "Per lot of the trade. The number of lots is sized from your own risk settings when you approve"]
    future = approval.leg_lines({"kind": "CONTRACT", "max_lots": 3, "data": {"kind": "FUTURE"}, "legs": [
        {"side": "SELL", "ratio": 1, "underlying": "NIFTY", "strike": None, "right": None, "expiry": "2026-09-30", "kind": "FUTURE"}]})
    assert future == ["Sell 1 lot NIFTY future, expiry 30 Sep 2026",
                      "The number of lots is sized from your own risk settings when you approve, at most 3"]
    assert approval.structure_title({"kind": "CONTRACT", "data": {"kind": "FUTURE"}}) == "Future"
