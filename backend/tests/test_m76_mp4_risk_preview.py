"""M76 MP-4 (W-3): the risk preview's book read. `/api/positions/greeks` gains two additive, read-only parameters:
`mine=true` (his own open legs) and `underlying=` (`underlying_net`, the sums over that underlying's legs). Without them
the answer is exactly as before. The IV rise for the preview is his own setting, Not set by default (M68)."""
from app.db.models import User
from app.me import trade_settings as ts
from tests import test_multileg as ml


def _open_spread(monkeypatch, email):
    ml._load_master()
    t = ml._tenant(email)
    broker = ml._OptionBroker()

    async def go():
        async with ml._session_factory() as session:
            user = await session.get(User, t["user_id"])
            structure = await ml.resolve_structure(session, "NIFTY 50", ml._rules(), ml.OptionStrategy.BULL_PUT_SPREAD, ml.SignalDirection.LONG,
                                                   spread_width=2, spot=ml.SPOT, today=ml.TODAY)
            return await ml.execute_structure(session, user, mode="PAPER", strategy_id="s", signal=ml._signal(), structure=structure,
                                              rules=ml._rules(max_lots=1), target_credit_pct=None, stop_credit_pct=None, idempotency_key=email,
                                              quote_broker=broker, risk_config=ml.RiskConfig(capital=1_000_000, risk_per_trade_pct=1.0))
    assert ml._run(go()).executed
    import app.brokers.token_lifecycle as tl
    monkeypatch.setattr("app.trading.routes.build_adapter", lambda record, client=None: broker, raising=False)
    monkeypatch.setattr(tl, "build_adapter", lambda record, client=None: broker)
    return t


def test_underlying_net_sums_only_that_underlying(monkeypatch):
    t = _open_spread(monkeypatch, "mp4-greeks@example.com")
    plain = ml.client.get("/api/positions/greeks", headers=t["headers"]).json()
    assert plain["underlying_net"] is None                                       # no parameter: as before
    body = ml.client.get("/api/positions/greeks", headers=t["headers"], params={"mine": "true", "underlying": "NIFTY 50"}).json()
    assert body["net"] == plain["net"] and len(body["legs"]) == len(plain["legs"]) == 2
    u = body["underlying_net"]
    assert u["underlying"] == "NIFTY" and u["legs"] == 2
    for k in ("delta", "theta", "vega"):
        assert abs(u[f"net_{k}"] - round(sum(leg[f"position_{k}"] for leg in body["legs"]), 2)) < 0.011
    alias = ml.client.get("/api/positions/greeks", headers=t["headers"], params={"underlying": "NIFTY"}).json()
    assert alias["underlying_net"]["legs"] == 2                                   # NIFTY 50 and NIFTY are one underlying
    other = ml.client.get("/api/positions/greeks", headers=t["headers"], params={"underlying": "BANKNIFTY"}).json()
    assert other["underlying_net"] == {"underlying": "BANKNIFTY", "legs": 0, "net_delta": 0, "net_gamma": 0, "net_theta": 0, "net_vega": 0}


def test_iv_rise_is_his_own_number_not_set_by_default():
    field = next(f for f in ts.FIELDS if f.key == "warnings.iv_rise_pct")
    assert field.default is None and field.kind == "number" and field.wired
    assert ts.effective({}, "warnings.iv_rise_pct") is None


def test_mine_reads_only_his_own_legs(monkeypatch):
    from sqlalchemy import update

    from app.db.models import TradeRecord
    t = _open_spread(monkeypatch, "mp4-mine@example.com")
    other = ml._tenant("mp4-other@example.com")

    async def hand_over():
        async with ml._session_factory() as session:
            await session.execute(update(TradeRecord).where(TradeRecord.tenant_id == t["tenant_id"]).values(user_id=other["user_id"]))
            await session.commit()
    ml._run(hand_over())
    assert len(ml.client.get("/api/positions/greeks", headers=t["headers"]).json()["legs"]) == 2       # the organisation's, as before
    mine = ml.client.get("/api/positions/greeks", headers=t["headers"], params={"mine": "true", "underlying": "NIFTY"}).json()
    assert mine["legs"] == [] and mine["underlying_net"]["legs"] == 0
