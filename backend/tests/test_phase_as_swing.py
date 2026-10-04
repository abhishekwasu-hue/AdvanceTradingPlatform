"""Phase AS: swing trading - daily strategies held overnight, the right broker product, no square-off."""
import asyncio
import math
from datetime import datetime, timedelta, timezone

import pandas as pd

from app.ai import advisor as ad
from app.ai import interview as iv
from app.brokers.models import BrokerOrderResponse
from app.core.enums import SignalDirection
from app.db.models import TradeRecord
from app.deployments.routes import DeploymentCreateRequest
from app.execution.products import product_for, product_for_trade
from app.market_data import service as md_service
from app.market_data.freshness import candle_staleness, timeframe_seconds
from app.strategy_engine.chart_routes import evaluate_on_candles
from app.strategy_engine.swing_strategies import SwingBreakout, SwingEmaPullback
from app.core.models import RiskConfig
from tests.test_deployments_api import _auth, _create, _store_broker
from tests.test_auth_api import _session_factory

UTC = timezone.utc


def _daily(closes, volumes=None, start=datetime(2025, 6, 2, 18, 30, tzinfo=UTC), spread=0.6):
    days, d = [], start
    while len(days) < len(closes):
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    opens = [closes[0]] + list(closes[:-1])
    return pd.DataFrame({"open": opens, "close": closes,
                         "high": [max(o, c) + spread for o, c in zip(opens, closes)],
                         "low": [min(o, c) - spread for o, c in zip(opens, closes)],
                         "volume": volumes or [100_000.0] * len(closes)}, index=pd.DatetimeIndex(days))


def test_products_follow_the_holding():
    assert product_for("INTRADAY", "UNDERLYING", "NSE") == "MIS"
    assert product_for("SWING", "UNDERLYING", "NSE") == "CNC"
    assert product_for("SWING", "OPTION", "NFO") == "NRML" and product_for("SWING", "FUTURE", "NFO") == "NRML"
    assert product_for(None, None, None) == "MIS"
    assert product_for_trade(TradeRecord(holding="SWING", instrument_kind="UNDERLYING", exchange=None)) == "CNC"


def test_swing_ema_pullback_buys_the_dip_in_a_daily_uptrend():
    closes = [100 + 0.6 * i for i in range(80)]
    closes += [closes[-1] - 2.0, closes[-1] - 3.5, closes[-1] - 4.2]       # pull back towards EMA20
    closes += [closes[-1] + 2.5]                                              # bullish close back above it
    df = _daily(closes)
    signal = SwingEmaPullback().analyze({"day": df}, "RELIANCE")
    assert signal.direction == SignalDirection.LONG, signal.reasons
    assert signal.entry - signal.stop_loss >= 0.99 * float(df["close"].diff().abs().tail(14).mean())   # at least ~1 ATR
    assert signal.target1 > signal.entry


def test_swing_breakout_needs_trend_and_volume():
    closes = [100 + 0.5 * i + 1.5 * math.sin(i / 2) for i in range(60)]
    breakout = closes + [max(closes) + 4]
    volumes = [100_000.0] * 60 + [250_000.0]
    long_signal = SwingBreakout().analyze({"day": _daily(breakout, volumes)}, "TCS")
    assert long_signal.direction == SignalDirection.LONG and long_signal.stop_loss < long_signal.entry
    quiet = SwingBreakout().analyze({"day": _daily(breakout, [100_000.0] * 61)}, "TCS")
    assert quiet.direction == SignalDirection.NO_TRADE and "volume" in quiet.reasons[0]
    index_like = SwingBreakout().analyze({"day": _daily(breakout, [0.0] * 61)}, "NIFTY 50")     # no volume: check skipped
    assert index_like.direction == SignalDirection.LONG


def test_daily_strategies_on_the_chart_need_a_day_chart():
    df = _daily([100 + 0.6 * i for i in range(120)])
    run = evaluate_on_candles(SwingBreakout(), df, "day", "TCS", RiskConfig())
    assert run.compatible and run.bars_used == 120
    intraday = evaluate_on_candles(SwingBreakout(), df, "5min", "TCS", RiskConfig())
    assert not intraday.compatible and "switch the chart to day" in intraday.reason


def test_daily_candles_fetch_a_year_and_skip_the_forming_day(monkeypatch):
    calls = {}

    class Broker:
        name = "fake"

        async def get_historical_data(self, symbol, exchange, interval, start, end):
            calls["span"] = (end - start).days
            return []

        async def get_intraday_candles(self, symbol, exchange, interval):
            calls["intraday"] = True
            return []

    async def no_cache(*a, **k):
        return None
    monkeypatch.setattr(md_service, "cache_get", no_cache)
    monkeypatch.setattr(md_service, "cache_set", no_cache)
    asyncio.run(md_service.MarketDataService(Broker()).get_candles("TCS", "NSE", "day"))
    assert calls["span"] >= md_service.DAILY_MIN_LOOKBACK_DAYS - 2 and "intraday" not in calls
    assert timeframe_seconds("day") == 86400
    friday_bar = datetime(2026, 9, 25, 18, 30, tzinfo=UTC)
    assert candle_staleness(friday_bar, "day", datetime(2026, 9, 30, 6, 0, tzinfo=UTC)) is None   # long weekend + holiday


def test_swing_deployment_rules():
    headers = _auth("swing-deploy@example.com")
    _store_broker(headers)
    ok = _create(headers, strategy_id="swing_ema_pullback_d", timeframe="day", holding="SWING")
    assert ok.status_code == 201, ok.text
    assert ok.json()["holding"] == "SWING"
    assert _create(headers, strategy_id="swing_breakout_d", timeframe="day").status_code == 400            # daily needs SWING
    assert _create(headers, strategy_id="swing_breakout_d", holding="SWING").status_code == 400            # SWING needs day candles
    assert _create(headers, strategy_id="swing_breakout_d", timeframe="day", holding="SWING", symbol="NIFTY 50",
                   instrument_kind="OPTION", option_position="WRITE").status_code == 400                    # no overnight writes
    assert _create(headers, strategy_id="swing_breakout_d", timeframe="day", holding="SWING", symbol="NIFTY 50",
                   instrument_kind="OPTION", option_strategy="IRON_CONDOR").status_code == 400              # no multi-leg swings
    # Leave nothing ACTIVE behind: later worker tests run every tenant's deployments against fake
    # brokers that serve no daily candles, and their failures would trip the shared circuit breaker.
    from tests.test_auth_api import client
    assert client.post(f"/api/deployments/{ok.json()['id']}/stop", headers=headers, json={"reason": "test done"}).status_code == 200


def test_square_off_skips_swing_positions_and_exits_use_the_entry_product(monkeypatch):
    from app.trading import position_monitor as pm
    from app.workers.trading_worker import TradingWorker
    headers = _auth("swing-squareoff@example.com")
    from tests.test_deployments_api import _me
    me = _me(headers)

    async def seed():
        async with _session_factory() as session:
            rows = [TradeRecord(tenant_id=me["tenant_id"], user_id=me["id"], mode="PAPER", symbol=s, strategy_id="x", direction="LONG",
                                entry_time=datetime.now(UTC), entry_price=100, quantity=1, stop_loss=95, holding=h)
                    for s, h in (("INFY", "INTRADAY"), ("TCS", "SWING"))]
            session.add_all(rows)
            await session.commit()

    class MD:
        async def get_ltp(self, symbol, exchange="NSE", now=None):
            return 101.0

    async def go():
        await seed()
        async with _session_factory() as session:
            worker = TradingWorker(_session_factory, cycle_seconds=60)
            closed = await worker._square_off_all(session, me["tenant_id"], MD(), None, me["id"])
            from sqlalchemy import select
            open_ = list(await session.scalars(select(TradeRecord.symbol).where(TradeRecord.tenant_id == me["tenant_id"], TradeRecord.exit_time.is_(None))))
            return closed, open_
    closed, still_open = asyncio.run(go())
    assert closed == 1 and still_open == ["TCS"]

    placed = []

    class Broker:
        name = "fake"

        async def place_order(self, request):
            placed.append(request)
            return BrokerOrderResponse(order_id="X1", status="COMPLETE")

        async def get_order_book(self):
            return []
    monkeypatch.setattr(pm, "FILL_POLL_ATTEMPTS", 1)
    trade = TradeRecord(id=1, symbol="TCS", strategy_id="x", direction="LONG", quantity=5, stop_loss=95, holding="SWING",
                        instrument_kind="UNDERLYING", exchange=None)
    price = asyncio.run(pm._square_off_live(trade, Broker(), "Target", 110.0, pm.CloseOutcome(trade_id=1, closed=False)))
    assert price == 110.0 and placed[0].product == "CNC"


def test_interview_builds_a_swing_plan():
    closes = [1000 + 1.2 * i + 15 * math.sin(i / 5) for i in range(300)]
    df = _daily(closes)
    a = iv.InterviewAnswers(language="en", style="swing", instrument="fno_stock", symbol="RELIANCE", vehicle="underlying", experience="learning",
                            risk="moderate")
    plan = ad.build_options(a, ad.Preferences(), df, "day")
    dep = plan["deployment"]
    assert dep["holding"] == "SWING" and dep["timeframe"] == "day" and dep["exit_rules"]["time_exit_at"] is None
    assert plan["recommended"]["strategy_id"] in ("swing_ema_pullback_d", "swing_breakout_d")
    assert plan["risk_config"]["risk_per_trade_pct"] == 0.75          # 1% x the 0.75 gap buffer
    DeploymentCreateRequest(**dep).normalised()
    sell = iv.contract_plan(iv.InterviewAnswers(style="swing", experience="experienced", vehicle="option_sell"), "BULLISH")[0]
    assert sell["option_position"] == "BUY" and sell["expiry_rule"] == "MONTHLY"
    assert iv.prefill_from_prompt("मला swing trading strategy हवी")["style"] == "swing"
    answers, _, changes = ad.apply_feedback(iv.InterviewAnswers(style="intraday"), ad.Preferences(), ["want_swing"])
    assert answers.style == "swing" and changes
