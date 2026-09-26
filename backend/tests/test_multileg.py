"""Phase H2: multi-leg structures - resolution (bull put / bear call / iron condor), economics,
paper and LIVE execution (with unwind on a failed leg), group exits, Greeks per position."""
import asyncio
import json
from datetime import date

import pytest
from sqlalchemy import select

from app.brokers.models import BrokerOrderResponse, MarginInfo
from app.core.enums import ExpiryRule, InstrumentKind, OptionPosition, OptionStrategy, SignalDirection, StrikeRule
from app.core.models import RiskConfig
from app.db.models import NotificationRecord, OrderRecord, StrategyDeploymentRecord, Tenant, TradeRecord, User
from app.execution.multileg import execute_structure
from app.instruments.contracts import ContractResolutionError, ContractRules
from app.instruments.spreads import describe_structure, resolve_structure, structure_metrics
from app.market_data.service import MarketDataService
from app.trading.position_monitor import group_exit, monitor_open_positions
from tests.master_fixture import NIFTY_LOT
from tests.test_auth_api import _session_factory, client
from tests.test_contract_execution import BIG
from tests.test_contract_rules import TODAY, _load_master
from tests.test_deployments_api import _auth, _create, _store_broker
from tests.test_trading_worker import OPEN_NOW, _FakeBroker, _deploy, _force_signal, _get, _signal, _tenant, _trades, _worker

SPOT = 24512.0
# Premiums by strike for the fake broker: PE below spot cheaper further out, CE above likewise.
PREMIUMS = {"24500 PE": 120.0, "24400 PE": 80.0, "24450 PE": 98.0, "24350 PE": 60.0, "24300 PE": 45.0,
            "24500 CE": 110.0, "24600 CE": 70.0, "24550 CE": 88.0, "24650 CE": 55.0, "24700 CE": 42.0}


def _run(coro):
    return asyncio.run(coro)


def _premium(symbol: str) -> float:
    for key, value in PREMIUMS.items():
        if key in symbol:
            return value
    return 30.0


class _OptionBroker(_FakeBroker):
    def __init__(self, *, fail_symbol_containing: str = None, margin_per_lot: float = 60_000.0, available: float = 500_000.0):
        super().__init__()
        self.fail_on = fail_symbol_containing
        self.margin_per_lot = margin_per_lot
        self.available = available
        self.prices = dict(PREMIUMS)

    async def get_ltp(self, symbols):
        return {s: (SPOT if "NIFTY 50" in s else _premium(s)) for s in symbols}

    async def get_ltp_for_symbol(self, symbol, exchange="NSE"):
        if "|" in symbol:
            raise KeyError("instrument keys not quoted by this fake - fall back to the tradingsymbol")
        if "NIFTY 50" in symbol:
            return self.spot if hasattr(self, "spot") else SPOT
        for key, value in self.prices.items():
            if key in symbol:
                return value
        return 30.0

    async def place_order(self, order):
        if self.fail_on and self.fail_on in order.symbol:
            raise TimeoutError(f"broker timeout on {order.symbol}")
        self.placed.append(order)
        return BrokerOrderResponse(order_id=f"ORD-{len(self.placed)}", status="COMPLETE")

    async def get_order_book(self):
        from app.brokers.models import BrokerOrderStatus
        return [BrokerOrderStatus(order_id=f"ORD-{i + 1}", symbol=o.symbol, transaction_type=o.transaction_type, quantity=o.quantity,
                                  filled_quantity=o.quantity, order_type=o.order_type, status="COMPLETE", average_price=_premium(o.symbol))
                for i, o in enumerate(self.placed)]

    async def get_order_margin(self, order):
        return self.margin_per_lot

    async def get_margins(self):
        return MarginInfo(available_cash=self.available, available_margin=self.available)


def _rules(**kw) -> ContractRules:
    return ContractRules(kind=InstrumentKind.OPTION, position=OptionPosition.WRITE, expiry_rule=ExpiryRule.NEAREST, **kw)


def test_structures_resolve_the_right_legs():
    _load_master()

    async def go(strategy, direction, width=2, **kw):
        async with _session_factory() as session:
            return await resolve_structure(session, "NIFTY 50", _rules(**kw), strategy, direction, spread_width=width, spot=SPOT, today=TODAY)

    bull = _run(go(OptionStrategy.BULL_PUT_SPREAD, SignalDirection.LONG))
    assert [(l.role, l.contract.right, l.contract.strike) for l in bull.legs] == [("SHORT", "PE", 24500.0), ("LONG", "PE", 24400.0)]
    assert bull.width_points == 100.0 and bull.lot_size == NIFTY_LOT and bull.expiry == date(2026, 10, 1)
    bear = _run(go(OptionStrategy.BEAR_CALL_SPREAD, SignalDirection.SHORT, width=1))
    assert [(l.role, l.contract.right, l.contract.strike) for l in bear.legs] == [("SHORT", "CE", 24500.0), ("LONG", "CE", 24550.0)]
    condor = _run(go(OptionStrategy.IRON_CONDOR, SignalDirection.LONG, width=2, strike_rule=StrikeRule.OTM, strike_offset=2))
    assert [(l.role, l.contract.right, l.contract.strike) for l in condor.legs] == [
        ("SHORT", "PE", 24400.0), ("LONG", "PE", 24300.0), ("SHORT", "CE", 24600.0), ("LONG", "CE", 24700.0)]
    with pytest.raises(ContractResolutionError, match="not entered on a SHORT signal"):
        _run(go(OptionStrategy.BULL_PUT_SPREAD, SignalDirection.SHORT))
    with pytest.raises(ContractResolutionError, match="for the wing"):
        _run(go(OptionStrategy.BULL_PUT_SPREAD, SignalDirection.LONG, width=15))
    assert "bull put spread" in describe_structure(OptionStrategy.BULL_PUT_SPREAD, 2, _rules())
    assert "iron condor" in describe_structure(OptionStrategy.IRON_CONDOR, 2, _rules())


def test_structure_metrics_credit_max_loss_breakeven_and_exit_levels():
    _load_master()

    async def go():
        async with _session_factory() as session:
            return await resolve_structure(session, "NIFTY 50", _rules(), OptionStrategy.BULL_PUT_SPREAD, SignalDirection.LONG, spread_width=2, spot=SPOT, today=TODAY)
    s = _run(go())
    premiums = {l.contract.tradingsymbol: _premium(l.contract.tradingsymbol) for l in s.legs}
    m = structure_metrics(s, premiums, target_credit_pct=None, stop_credit_pct=None)
    assert m.net_credit == 40.0 and m.max_profit == 40.0 and m.max_loss == 60.0 and m.breakevens == [24460.0]
    assert m.target_value == 20.0 and m.stop_value == 80.0 and m.short_strikes == {"PE": 24500.0}
    m2 = structure_metrics(s, premiums, target_credit_pct=25, stop_credit_pct=50)
    assert m2.target_value == 30.0 and m2.stop_value == 60.0
    with pytest.raises(ContractResolutionError, match="net debit"):
        structure_metrics(s, {**premiums, s.legs[0].contract.tradingsymbol: 10.0}, target_credit_pct=None, stop_credit_pct=None)


def _user(t):
    async def go():
        async with _session_factory() as session:
            return await session.get(User, t["user_id"])
    return _run(go())


def test_paper_bull_put_spread_opens_two_grouped_legs_sized_off_max_loss():
    _load_master()
    t = _tenant("ml-paper@example.com")
    broker = _OptionBroker()

    async def go():
        async with _session_factory() as session:
            user = await session.get(User, t["user_id"])
            structure = await resolve_structure(session, "NIFTY 50", _rules(), OptionStrategy.BULL_PUT_SPREAD, SignalDirection.LONG,
                                                spread_width=2, spot=SPOT, today=TODAY)
            return await execute_structure(session, user, mode="PAPER", strategy_id="ema_rsi_scalper_1m", signal=_signal(), structure=structure,
                                           rules=_rules(), target_credit_pct=None, stop_credit_pct=None, idempotency_key="ml-paper-1",
                                           quote_broker=broker, risk_config=RiskConfig(capital=1_000_000, risk_per_trade_pct=1.0))
    result = _run(go())
    assert result.executed, result.reasons
    trades = _trades(t["tenant_id"])
    assert len(trades) == 2 and {tr.leg_role for tr in trades} == {"SHORT", "LONG"}
    assert len({tr.leg_group_id for tr in trades}) == 1 and all(tr.option_strategy == "BULL_PUT_SPREAD" for tr in trades)
    # risk 10,000 / max loss per lot (60 x 75 = 4,500) -> 2 lots
    assert all(tr.quantity == 2 * NIFTY_LOT for tr in trades)
    short = next(tr for tr in trades if tr.leg_role == "SHORT")
    assert short.direction == "SHORT" and "24500 PE" in short.symbol and short.option_position == "WRITE"
    meta = json.loads(short.group_meta)
    assert meta["net_credit"] == 40.0 and meta["lots"] == 2 and meta["short_strikes"] == {"PE": 24500.0}
    assert all(o.status == "POSITION_OPEN" for o in result.orders) and len(result.orders) == 2

    # Replaying the same key returns the existing outcome without new legs.
    again = _run(go())
    assert again.executed and "already handled" in again.reasons[0] and len(_trades(t["tenant_id"])) == 2


def test_spread_is_refused_when_max_loss_exceeds_risk_or_max_lots_caps():
    _load_master()
    t = _tenant("ml-refuse@example.com")
    broker = _OptionBroker()

    async def go(cfg, max_lots=None, key="k"):
        async with _session_factory() as session:
            user = await session.get(User, t["user_id"])
            structure = await resolve_structure(session, "NIFTY 50", _rules(max_lots=max_lots), OptionStrategy.BULL_PUT_SPREAD, SignalDirection.LONG,
                                                spread_width=2, spot=SPOT, today=TODAY)
            return await execute_structure(session, user, mode="PAPER", strategy_id="s", signal=_signal(), structure=structure,
                                           rules=_rules(max_lots=max_lots), target_credit_pct=None, stop_credit_pct=None,
                                           idempotency_key=key, quote_broker=broker, risk_config=cfg)
    small = _run(go(RiskConfig(capital=100_000, risk_per_trade_pct=1.0), key="small"))
    assert not small.executed and any("lot" in r.lower() for r in small.reasons)  # risk 1,000 < 4,500 max loss per lot
    assert all(o.status == "REJECTED" for o in small.orders) and _trades(t["tenant_id"]) == []
    capped = _run(go(RiskConfig(capital=1_000_000, risk_per_trade_pct=1.0), max_lots=1, key="capped"))
    assert capped.executed and all(tr.quantity == NIFTY_LOT for tr in _trades(t["tenant_id"]))


def test_live_spread_places_wing_first_and_unwinds_on_a_failed_short(monkeypatch):
    monkeypatch.setattr("app.execution.router.OrderRouter.fill_poll_delay_seconds", 0)
    _load_master()
    t = _tenant("ml-live@example.com")

    async def go(broker, key):
        async with _session_factory() as session:
            user = await session.get(User, t["user_id"])
            structure = await resolve_structure(session, "NIFTY 50", _rules(), OptionStrategy.BULL_PUT_SPREAD, SignalDirection.LONG,
                                                spread_width=2, spot=SPOT, today=TODAY)
            return await execute_structure(session, user, mode="LIVE", strategy_id="s", signal=_signal(), structure=structure,
                                           rules=_rules(), target_credit_pct=None, stop_credit_pct=None, idempotency_key=key,
                                           broker=broker, quote_broker=broker, risk_config=RiskConfig(capital=1_000_000, risk_per_trade_pct=1.0))

    ok = _OptionBroker(margin_per_lot=60_000, available=500_000)  # margin allows 6 lots; risk allows 2
    result = _run(go(ok, "live-ok"))
    assert result.executed, result.reasons
    assert [(o.transaction_type.value, "24400 PE" in o.symbol) for o in ok.placed] == [("BUY", True), ("SELL", False)]
    trades = _trades(t["tenant_id"])
    assert len(trades) == 2 and all(tr.mode == "LIVE" and tr.broker_order_id for tr in trades)

    no_margin = _OptionBroker(margin_per_lot=None)
    result = _run(go(no_margin, "live-nomargin"))
    assert not result.executed and any("did not report a margin" in r for r in result.reasons)

    failing = _OptionBroker(fail_symbol_containing="24500 PE")
    result = _run(go(failing, "live-fail"))
    assert not result.executed and result.system_failure and any("unwound 1 filled leg" in r for r in result.reasons)
    # Wing bought, then sold back; the short never filled.
    assert [o.transaction_type.value for o in failing.placed] == ["BUY", "SELL"] and all("24400 PE" in o.symbol for o in failing.placed)
    assert len(_trades(t["tenant_id"])) == 2  # no new legs booked
    assert _get(Tenant, t["tenant_id"]).broker_uncertain_since is not None

    async def failed_orders():
        async with _session_factory() as session:
            return list(await session.scalars(select(OrderRecord).where(OrderRecord.tenant_id == t["tenant_id"], OrderRecord.status == "FAILED")))
    assert len(_run(failed_orders())) == 2


def test_group_exit_rules():
    legs = [TradeRecord(id=1, leg_role="SHORT", group_meta=json.dumps({"stop_value": 80.0, "target_value": 20.0, "short_strikes": {"PE": 24500.0}})),
            TradeRecord(id=2, leg_role="LONG")]
    assert group_exit(legs, {1: 100.0, 2: 60.0}, 24550.0) is None            # value 40 = credit, nothing hit
    assert "Spread target" in group_exit(legs, {1: 50.0, 2: 32.0}, 24600.0)   # value 18 <= 20
    assert "Spread stop" in group_exit(legs, {1: 150.0, 2: 60.0}, 24550.0)    # value 90 >= 80
    assert "24500 PE breached" in group_exit(legs, {1: 100.0, 2: 60.0}, 24490.0)


def test_worker_opens_and_closes_a_spread_as_one_position(monkeypatch):
    from app.workers import trading_worker as tw
    _load_master()
    t = _tenant("ml-worker@example.com")
    dep_id = _deploy(t, symbol="NIFTY 50")

    async def mark():
        async with _session_factory() as session:
            dep = await session.get(StrategyDeploymentRecord, dep_id)
            dep.instrument_kind, dep.option_position, dep.option_strategy, dep.spread_width, dep.max_lots = "OPTION", "WRITE", "BULL_PUT_SPREAD", 2, 1
            dep.target_credit_pct = 50.0
            await session.commit()
    _run(mark())
    broker = _OptionBroker()
    monkeypatch.setattr(tw, "resolve_structure", lambda session, symbol, rules, strategy, direction, *, spread_width, spot, today, broker="upstox", **kw:
                        resolve_structure(session, symbol, rules, strategy, direction, spread_width=spread_width, spot=SPOT, today=TODAY, broker=broker, **kw))
    worker = _worker(monkeypatch, broker)
    async def big_risk(tenant_id, session):
        return BIG
    monkeypatch.setattr("app.execution.multileg.get_tenant_risk_config", big_risk)

    # A SHORT signal does not open a bull put spread - recorded, not traded.
    _force_signal(monkeypatch, lambda: _signal(direction=SignalDirection.SHORT))
    report = _run(worker.run_cycle(now=OPEN_NOW))
    assert report.signals_executed == 0 and "not entered on a SHORT" in _get(StrategyDeploymentRecord, dep_id).last_error

    from datetime import timedelta
    from tests.test_trading_worker import BAR_TS
    _force_signal(monkeypatch, lambda: _signal(ts=BAR_TS + timedelta(minutes=1)))
    report = _run(worker.run_cycle(now=OPEN_NOW + timedelta(minutes=1)))
    assert report.signals_executed == 1, report.errors
    trades = _trades(t["tenant_id"])
    assert len(trades) == 2 and all(tr.exit_time is None and tr.deployment_id == dep_id for tr in trades)

    # The short PE decays to 55 and the wing to 40: value 15 <= target 20 -> both legs close together.
    broker.prices = {**broker.prices, "24500 PE": 55.0, "24400 PE": 40.0}
    report = _run(worker.run_cycle(now=OPEN_NOW + timedelta(minutes=2)))
    trades = _trades(t["tenant_id"])
    assert report.positions_closed == 2 and all(tr.exit_time is not None and "Spread target" in tr.exit_reason for tr in trades)
    short = next(tr for tr in trades if tr.leg_role == "SHORT")
    long_ = next(tr for tr in trades if tr.leg_role == "LONG")
    assert short.pnl > 0 and long_.pnl < 0 and short.pnl + long_.pnl > 0


def test_deployment_api_accepts_structures_and_previews_legs(monkeypatch):
    _load_master()
    headers = _auth("ml-api@example.com")
    _store_broker(headers, token_status="VALID")
    bad = _create(headers, symbol="NIFTY 50", instrument_kind="FUTURE", option_strategy="IRON_CONDOR")
    assert bad.status_code == 400
    bad = _create(headers, symbol="NIFTY 50", instrument_kind="OPTION", option_strategy="BULL_PUT_SPREAD", premium_stop_pct=30)
    assert bad.status_code == 400 and "net credit" in bad.json()["detail"]
    ok = _create(headers, symbol="NIFTY 50", instrument_kind="OPTION", option_strategy="IRON_CONDOR", spread_width=2,
                 strike_rule="OTM", strike_offset=2, target_credit_pct=50, stop_credit_pct=100)
    assert ok.status_code == 201, ok.text
    body = ok.json()
    assert body["option_strategy"] == "IRON_CONDOR" and body["spread_width"] == 2 and body["option_position"] == "WRITE"
    assert "iron condor" in body["contract_rules"]

    monkeypatch.setattr("app.deployments.routes.build_adapter", lambda record, client=None: _OptionBroker())
    preview = client.post("/api/deployments/preview-contract", headers=headers, json={
        "symbol": "NIFTY 50", "instrument_kind": "OPTION", "option_strategy": "BULL_PUT_SPREAD", "spread_width": 2, "spot": SPOT,
    }).json()
    long_side = preview["structures"]["LONG"]
    assert [l["role"] for l in long_side["legs"]] == ["SHORT", "LONG"] and long_side["metrics"]["net_credit"] == 40.0
    assert long_side["metrics"]["max_loss"] == 60.0 and long_side["metrics"]["breakevens"] == [24460.0]
    assert "not entered on a SHORT" in preview["structures"]["SHORT"]["error"]


def test_position_greeks_endpoint(monkeypatch):
    _load_master()
    t = _tenant("ml-greeks@example.com")
    broker = _OptionBroker()

    async def go():
        async with _session_factory() as session:
            user = await session.get(User, t["user_id"])
            structure = await resolve_structure(session, "NIFTY 50", _rules(), OptionStrategy.BULL_PUT_SPREAD, SignalDirection.LONG,
                                                spread_width=2, spot=SPOT, today=TODAY)
            return await execute_structure(session, user, mode="PAPER", strategy_id="s", signal=_signal(), structure=structure,
                                           rules=_rules(max_lots=1), target_credit_pct=None, stop_credit_pct=None, idempotency_key="greeks",
                                           quote_broker=broker, risk_config=RiskConfig(capital=1_000_000, risk_per_trade_pct=1.0))
    assert _run(go()).executed

    monkeypatch.setattr("app.brokers.token_lifecycle.build_adapter", lambda record, client=None: broker)
    monkeypatch.setattr("app.trading.routes.build_adapter", lambda record, client=None: broker, raising=False)
    import app.brokers.token_lifecycle as tl
    monkeypatch.setattr(tl, "build_adapter", lambda record, client=None: broker)
    resp = client.get("/api/positions/greeks", headers=t["headers"])
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["legs"]) == 2 and body["spot"]["NIFTY 50"] == SPOT
    short = next(l for l in body["legs"] if l["leg_role"] == "SHORT")
    assert short["quantity"] == -NIFTY_LOT and short["delta"] < 0 and short["position_delta"] > 0  # short put: positive position delta
    assert len(body["groups"]) == 1 and body["groups"][0]["legs"] == 2 and abs(body["net"]["net_delta"]) < NIFTY_LOT
