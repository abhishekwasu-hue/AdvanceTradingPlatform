"""Phase I1: the risk hierarchy - scoped limits, strictest wins, PASS/WARN/BLOCK events, the
hook between sizing and placement (single-leg and multi-leg), automatic strategy/tenant stops,
and the limits/events API."""
import asyncio
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.core.enums import KillSwitchScope, RiskLimitType, RiskScope
from app.core.models import RiskConfig
from app.db.models import RiskEventRecord, RiskLimitRecord, TradeRecord, User
from app.kill_switch.checks import disengage, get_switch
from app.risk_engine.hierarchy import RiskContext, evaluate, strictest
from tests.test_auth_api import _session_factory, client
from tests.test_deployments_api import _auth
from tests.test_live_execution import _LiveBroker, _signal, _upgrade_plan


def _run(coro):
    return asyncio.run(coro)


def _me(headers):
    return client.get("/api/auth/me", headers=headers).json()


def _limit(headers, scope, limit_type, value, scope_id="", **kw):
    resp = client.put("/api/risk/limits", headers=headers, json={"scope": scope, "scope_id": scope_id, "limit_type": limit_type, "limit_value": value, **kw})
    assert resp.status_code == 200, resp.text
    return resp.json()


def _seed_closed_trade(tenant_id, user_id, strategy_id, pnl, symbol="RELIANCE"):
    async def go():
        async with _session_factory() as session:
            now = datetime.now(timezone.utc)
            session.add(TradeRecord(tenant_id=tenant_id, user_id=user_id, symbol=symbol, strategy_id=strategy_id, direction="LONG",
                                    entry_time=now - timedelta(hours=1), entry_price=100.0, quantity=10, stop_loss=98.0, target1=104.0,
                                    exit_time=now, exit_price=100 + pnl / 10, exit_reason="test", pnl=pnl, mode="PAPER"))
            await session.commit()
    _run(go())


def test_strictest_limit_wins_across_scopes():
    rows = [RiskLimitRecord(scope="TENANT", scope_id="", limit_type="MAX_ORDER_VALUE", limit_value=500_000),
            RiskLimitRecord(scope="STRATEGY", scope_id="s", limit_type="MAX_ORDER_VALUE", limit_value=200_000),
            RiskLimitRecord(scope="GLOBAL", scope_id="", limit_type="MAX_ORDER_VALUE", limit_value=900_000),
            RiskLimitRecord(scope="TENANT", scope_id="", limit_type="MAX_TRADES_PER_DAY", limit_value=5)]
    best = strictest(rows)
    assert best["MAX_ORDER_VALUE"].scope == "STRATEGY" and best["MAX_TRADES_PER_DAY"].limit_value == 5


def test_evaluate_records_pass_warn_and_block_events():
    headers = _auth("risk-h-eval@example.com")
    me = _me(headers)
    _limit(headers, "TENANT", "MAX_ORDER_VALUE", 100_000)
    _limit(headers, "STRATEGY", "MAX_LOSS_PER_TRADE", 2_000, scope_id="ema_rsi_scalper_1m")
    _limit(headers, "INSTRUMENT", "MAX_POSITION_QUANTITY", 1_000, scope_id="reliance")

    async def go(quantity, entry=100.0, stop=98.0):
        async with _session_factory() as session:
            ctx = RiskContext(tenant_id=me["tenant_id"], user_id=me["id"], strategy_id="ema_rsi_scalper_1m", symbol="RELIANCE",
                              quantity=quantity, entry=entry, stop_loss=stop, capital=1_000_000)
            return await evaluate(session, ctx)
    ok = _run(go(quantity=100))          # value 10,000; loss 200; qty 100 -> all PASS
    assert ok.allowed and [c.status for c in ok.checks] == ["PASS", "PASS", "PASS"]
    warn = _run(go(quantity=850))        # value 85,000, loss 1,700, qty 850: each >= 80% of its limit -> WARN
    assert warn.allowed and [c.status for c in warn.checks] == ["WARN", "WARN", "WARN"] and len(warn.notes) == 3
    block = _run(go(quantity=1_200))     # value 120,000 BLOCK, loss 2,400 BLOCK, qty 1,200 BLOCK
    assert not block.allowed and len(block.reasons) == 3 and all("exceeds limit" in r for r in block.reasons)

    events = client.get("/api/risk/events", headers=headers, params={"strategy_id": "ema_rsi_scalper_1m"}).json()
    assert len(events) == 9 and events[0]["status"] == "BLOCK" and events[-1]["status"] == "PASS"
    blocked = client.get("/api/risk/events", headers=headers, params={"status": "block"}).json()
    assert len(blocked) == 3 and {e["rule_type"] for e in blocked} == {"MAX_ORDER_VALUE", "MAX_LOSS_PER_TRADE", "MAX_POSITION_QUANTITY"}


def test_limits_api_validation_scoping_and_delete():
    headers = _auth("risk-h-api@example.com")
    other = _auth("risk-h-other@example.com")
    bad = client.put("/api/risk/limits", headers=headers, json={"scope": "STRATEGY", "limit_type": "MAX_DAILY_LOSS", "limit_value": 100})
    assert bad.status_code == 400 and "scope_id" in bad.json()["detail"]
    forbidden = client.put("/api/risk/limits", headers=headers, json={"scope": "GLOBAL", "limit_type": "MAX_DAILY_LOSS", "limit_value": 100})
    assert forbidden.status_code == 403
    created = _limit(headers, "TENANT", "MAX_DAILY_LOSS", 5_000, note="house rule")
    again = _limit(headers, "TENANT", "MAX_DAILY_LOSS", 4_000)
    assert again["id"] == created["id"] and again["limit_value"] == 4_000  # upsert, not duplicate
    mine = client.get("/api/risk/limits", headers=headers).json()
    assert [l["limit_type"] for l in mine] == ["MAX_DAILY_LOSS"]
    assert client.get("/api/risk/limits", headers=other).json() == []
    assert client.delete(f"/api/risk/limits/{created['id']}", headers=other).status_code == 404
    assert client.delete(f"/api/risk/limits/{created['id']}", headers=headers).status_code == 204
    assert client.get("/api/risk/limits", headers=headers).json() == []
    # Viewers can read, not write.
    assert client.put("/api/risk/limits", headers={}, json={"scope": "TENANT", "limit_type": "MAX_DAILY_LOSS", "limit_value": 1}).status_code in (401, 403)


def test_single_leg_order_is_rejected_by_the_hierarchy_with_the_reason_on_the_trail():
    headers = _auth("risk-h-order@example.com")
    me = _me(headers)
    _limit(headers, "TENANT", "MAX_ORDER_VALUE", 20_000)

    async def go():
        from app.execution.signal_execution import execute_signal_for_user
        async with _session_factory() as session:
            user = await session.get(User, me["id"])
            return await execute_signal_for_user(session, user, mode="PAPER", strategy_id="ema_rsi_scalper_1m", signal=_signal(),
                                                 risk_config=RiskConfig(capital=1_000_000, risk_per_trade_pct=1.0))
    result, order = _run(go())
    # risk 10,000 / 2 per unit = 5,000 shares x 100 = 500,000 order value > 20,000
    assert not result.executed and order.status == "REJECTED" and any("MAX_ORDER_VALUE" in r for r in result.reasons)
    events = client.get("/api/risk/events", headers=headers).json()
    assert events[0]["order_id"] == order.id and events[0]["status"] == "BLOCK"

    _limit(headers, "TENANT", "MAX_ORDER_VALUE", 600_000)
    result, order = _run(go())
    assert result.executed and order.status == "POSITION_OPEN"


def test_strategy_loss_limit_stops_the_strategy_and_daily_loss_stops_the_tenant():
    headers = _auth("risk-h-stop@example.com")
    me = _me(headers)
    _limit(headers, "STRATEGY", "MAX_STRATEGY_LOSS", 1_000, scope_id="ema_rsi_scalper_1m")
    _seed_closed_trade(me["tenant_id"], me["id"], "ema_rsi_scalper_1m", pnl=-1_500)

    async def go(strategy_id="ema_rsi_scalper_1m"):
        from app.execution.signal_execution import execute_signal_for_user
        async with _session_factory() as session:
            user = await session.get(User, me["id"])
            return await execute_signal_for_user(session, user, mode="PAPER", strategy_id=strategy_id, signal=_signal(),
                                                 risk_config=RiskConfig(capital=1_000_000, risk_per_trade_pct=0.1))
    result, order = _run(go())
    assert not result.executed and any("MAX_STRATEGY_LOSS" in r for r in result.reasons)

    async def switch(scope, strategy_id=None):
        async with _session_factory() as session:
            return await get_switch(session, scope, me["tenant_id"], strategy_id)
    strategy_switch = _run(switch(KillSwitchScope.STRATEGY, "ema_rsi_scalper_1m"))
    assert strategy_switch is not None and strategy_switch.engaged and "Risk limit" in strategy_switch.reason
    # The next signal is refused at the door by the kill switch, before any sizing.
    result, order = _run(go())
    assert not result.executed and any("kill switch" in r.lower() for r in result.reasons)
    # Another strategy of the same tenant is untouched by a STRATEGY-scope stop.
    result, order = _run(go(strategy_id="supertrend_adx_scalper_1m"))
    assert result.executed

    _limit(headers, "TENANT", "MAX_DAILY_LOSS", 1_000)
    result, order = _run(go(strategy_id="supertrend_adx_scalper_1m"))
    assert not result.executed and any("MAX_DAILY_LOSS" in r for r in result.reasons)
    tenant_switch = _run(switch(KillSwitchScope.TENANT))
    assert tenant_switch is not None and tenant_switch.engaged
    async def clear():
        async with _session_factory() as session:
            await disengage(session, KillSwitchScope.TENANT, me["tenant_id"])
            await disengage(session, KillSwitchScope.STRATEGY, me["tenant_id"], "ema_rsi_scalper_1m")
    _run(clear())


def test_dry_run_evaluate_endpoint_and_multileg_uses_max_loss():
    headers = _auth("risk-h-dry@example.com")
    _limit(headers, "TENANT", "MAX_LOSS_PER_TRADE", 3_000)
    dry = client.post("/api/risk/evaluate", headers=headers, json={"strategy_id": "s", "symbol": "NIFTY 50", "quantity": 75, "entry": 100, "stop_loss": 50}).json()
    assert not dry["allowed"] and dry["checks"][0]["current"] == 3_750 and dry["checks"][0]["status"] == "BLOCK"
    ok = client.post("/api/risk/evaluate", headers=headers, json={"strategy_id": "s", "symbol": "NIFTY 50", "quantity": 75, "entry": 100, "stop_loss": 70}).json()
    assert ok["allowed"] and ok["checks"][0]["status"] == "PASS"

    # Multi-leg: the structure's max loss per unit (60) x quantity (75 x 2 lots) = 9,000 > 3,000 -> blocked.
    from tests.test_contract_rules import TODAY, _load_master
    from tests.test_multileg import SPOT, _OptionBroker, _rules
    from app.core.enums import OptionStrategy, SignalDirection
    from app.execution.multileg import execute_structure
    from app.instruments.spreads import resolve_structure
    _load_master()
    me = _me(headers)

    async def go():
        async with _session_factory() as session:
            user = await session.get(User, me["id"])
            structure = await resolve_structure(session, "NIFTY 50", _rules(), OptionStrategy.BULL_PUT_SPREAD, SignalDirection.LONG,
                                                spread_width=2, spot=SPOT, today=TODAY)
            return await execute_structure(session, user, mode="PAPER", strategy_id="s", signal=_signal(), structure=structure, rules=_rules(),
                                           target_credit_pct=None, stop_credit_pct=None, idempotency_key="risk-ml", quote_broker=_OptionBroker(),
                                           risk_config=RiskConfig(capital=1_000_000, risk_per_trade_pct=1.0))
    result = _run(go())
    assert not result.executed and any("MAX_LOSS_PER_TRADE" in r and "9,000" in r for r in result.reasons)
    assert all(o.status == "REJECTED" for o in result.orders)
