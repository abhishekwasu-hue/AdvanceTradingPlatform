"""Phase D1: SEBI algo-order tagging - every broker order carries the tenant's exchange-issued
algo id, shortened to the broker's tag limit, and the tag is stored on the order trail."""
import asyncio

from sqlalchemy import select

from app.core import config
from app.core.enums import OrderSide
from app.db.models import Tenant, TradeRecord, User
from app.execution.tagging import LEG_ENTRY, LEG_EXIT, LEG_STOP, build_order_tag, valid_algo_id
from app.risk_engine.risk_manager import TradingDayState
from tests.test_auth_api import _register, _session_factory, client
from tests.test_live_execution import _LiveBroker, _execute, _router, _signal, _upgrade_plan
from tests.test_orders_api import _fake_long_signal, _sample_candles_payload


def test_tag_without_algo_id_is_strategy_and_leg_within_limit():
    tag = build_order_tag(strategy_id="ema_rsi_scalper_1m", leg=LEG_ENTRY)
    assert tag.endswith("-ENT") and len(tag) <= 20
    assert tag.startswith("ema_rsi_scalper")


def test_algo_id_and_leg_survive_shortening():
    tag = build_order_tag(strategy_id="mtf_trend_pullback_5m_30m", leg=LEG_STOP, algo_id="NSE1234567")
    assert tag.startswith("NSE1234567-") and tag.endswith("-SL") and len(tag) == 20


def test_unsafe_characters_are_stripped_and_longer_limits_used():
    tag = build_order_tag(strategy_id="my strat: v2/beta", leg=LEG_EXIT, algo_id="A-1_b", max_length=40)
    assert tag == "A-1_b-mystratv2beta-EXIT"


def test_algo_id_validation():
    assert valid_algo_id("NSE12345")
    assert valid_algo_id("abc_DEF-9")
    assert not valid_algo_id("")
    assert not valid_algo_id("has space")
    assert not valid_algo_id("x" * 33)


def test_live_router_tags_entry_and_stop_with_algo_id():
    broker = _LiveBroker()
    result = asyncio.run(_router(broker, algo_id="NSE777").execute(_signal(), TradingDayState()))
    assert result.executed
    assert broker.placed[0].tag == "NSE777-ema_rsi_s-ENT"
    assert broker.placed[1].tag == "NSE777-ema_rsi_sc-SL"
    assert result.algo_tag == broker.placed[0].tag
    assert all(len(o.tag) <= broker.max_tag_length for o in broker.placed)


def test_router_respects_broker_tag_limit():
    class Wide(_LiveBroker):
        max_tag_length = 40
    broker = Wide()
    asyncio.run(_router(broker, algo_id="NSE777").execute(_signal(), TradingDayState()))
    assert broker.placed[0].tag == "NSE777-ema_rsi_scalper_1m-ENT"


def _headers(email: str) -> dict:
    _register(email)
    token = client.post("/api/auth/login", json={"email": email, "password": "S3cur3Pass!"}).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_owner_sets_algo_id_via_team_api_and_paper_orders_carry_it(monkeypatch):
    from app.strategy_engine.registry import registry

    headers = _headers("algo-owner@example.com")
    assert client.get("/api/team/tenant", headers=headers).json()["algo_id"] is None

    bad = client.patch("/api/team/tenant", json={"algo_id": "not valid!"}, headers=headers)
    assert bad.status_code == 400

    ok = client.patch("/api/team/tenant", json={"algo_id": " NSE4242 "}, headers=headers)
    assert ok.status_code == 200 and ok.json()["algo_id"] == "NSE4242"

    strategy = registry.get("ema_rsi_scalper_1m")
    monkeypatch.setattr(strategy, "analyze", lambda data, symbol: _fake_long_signal())
    resp = client.post(
        "/api/strategies/ema_rsi_scalper_1m/paper-execute", headers=headers,
        json={"symbol": "TESTSYM", "candles": {"1min": _sample_candles_payload()}},
    )
    assert resp.status_code == 200, resp.text
    orders = client.get("/api/orders", headers=headers).json()
    assert orders and orders[0]["algo_tag"].startswith("NSE4242-") and orders[0]["algo_tag"].endswith("-ENT")

    audit = client.get("/api/audit-logs", headers=headers).json()
    assert any(a["event"] == "tenant_algo_id_changed" and a["detail"] == "NSE4242" for a in audit)

    cleared = client.patch("/api/team/tenant", json={"algo_id": ""}, headers=headers)
    assert cleared.json()["algo_id"] is None


def _user(email: str) -> User:
    _register(email)

    async def load():
        async with _session_factory() as session:
            return await session.scalar(select(User).where(User.email == email))
    user = asyncio.run(load())
    _upgrade_plan(user.tenant_id, "pro")
    return user


def _set_algo_id(tenant_id: int, algo_id):
    async def go():
        async with _session_factory() as session:
            tenant = await session.get(Tenant, tenant_id)
            tenant.algo_id = algo_id
            await session.commit()
    asyncio.run(go())


def test_live_execution_uses_tenant_algo_id_and_records_tag(monkeypatch):
    from app.execution.router import OrderRouter
    monkeypatch.setattr(OrderRouter, "fill_poll_delay_seconds", 0)
    user = _user("algo-live@example.com")
    _set_algo_id(user.tenant_id, "NSE9001")
    broker = _LiveBroker()
    result, order, trade, _ = _execute(user, broker)
    assert result.executed
    assert order.algo_tag == "NSE9001-ema_rsi_-ENT"
    assert broker.placed[0].tag == order.algo_tag
    assert broker.placed[1].tag == "NSE9001-ema_rsi_s-SL"


def test_live_refused_without_algo_id_when_required(monkeypatch):
    from app.execution.router import OrderRouter
    monkeypatch.setattr(OrderRouter, "fill_poll_delay_seconds", 0)
    monkeypatch.setattr(config, "ALGO_ID_REQUIRED_FOR_LIVE", True)
    user = _user("algo-required@example.com")
    broker = _LiveBroker()
    result, order, trade, notes = _execute(user, broker)
    assert not result.executed
    assert order.status == "REJECTED"
    assert any("algo id" in r.lower() for r in result.reasons)
    assert broker.placed == []

    _set_algo_id(user.tenant_id, "NSE1")
    result, order, trade, _ = _execute(user, broker)
    assert result.executed and order.algo_tag.startswith("NSE1-")


def test_exit_order_carries_algo_id():
    from app.trading.position_monitor import close_position
    from tests.test_position_monitor import _Broker as _ExitBroker, _trade, _user as _pm_user

    user = _pm_user("algo-exit@example.com")
    _set_algo_id(user.tenant_id, "NSE55")
    trade_id = _trade(user, mode="LIVE")
    broker = _ExitBroker()

    async def go():
        async with _session_factory() as session:
            trade = await session.get(TradeRecord, trade_id)
            return await close_position(session, trade, 104.0, "Target 1", broker=broker)
    outcome = asyncio.run(go())
    assert outcome.closed
    exits = [o for o in broker.placed if o.order_type == "MARKET"]
    assert exits and exits[-1].tag == "NSE55-ema_rsi_s-EXIT" and len(exits[-1].tag) <= 20
    assert exits[-1].transaction_type == OrderSide.SELL
