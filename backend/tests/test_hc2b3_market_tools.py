"""H-C2b-3 (ADR-0019 §1): market and research read tools on server data only - candles, quote, option chain, regime
and backtest. Each has a schema test, a no-broker-session test (fails closed, never sample data) and an as-of test."""
import asyncio
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from app.ai import tools
from app.ai.tools import ToolContext, market, run_tool
from app.brokers.models import OptionChain, OptionChainRow, Quote
from app.db.models import User
from tests.test_auth_api import _session_factory, client  # noqa: F401 - fixtures
from tests.test_hc2a_agent import _Scripted, _agent
from tests.test_phase_l_ai import _owner

NOW = datetime(2026, 3, 10, 5, 0, tzinfo=timezone.utc)
NEW = ("get_candles", "get_quote", "get_option_chain", "get_market_regime", "run_backtest")


def _run(coro):
    return asyncio.run(coro)


def _df(n=300, freq="5min", step=0.2):
    idx = pd.date_range("2026-03-02 03:45", periods=n, freq=freq, tz="UTC")
    base = 100 + np.arange(n) * step + np.sin(np.arange(n) / 3)
    return pd.DataFrame({"open": base - 0.1, "high": base + 0.5, "low": base - 0.5, "close": base, "volume": 1000.0}, index=idx)


def _call(user_id, name, args):
    async def go():
        async with _session_factory() as session:
            user = await session.get(User, user_id)
            return await run_tool(name, args, ToolContext(session, user.tenant_id, user, NOW))
    return _run(go())


def _fake_frame(monkeypatch, df):
    seen = []

    async def frame(ctx, symbol, exchange, timeframe, days):
        seen.append((ctx.tenant_id, symbol, exchange, timeframe, days))
        return df, "broker:fake"
    monkeypatch.setattr(market, "_frame", frame)
    return seen


class _Adapter:
    async def get_quote_for_symbol(self, symbol, exchange="NSE"):
        return Quote(symbol=symbol, ltp=105.0, open=101.0, high=106.0, low=100.0, close=100.0, volume=12345, timestamp=NOW)

    async def get_option_chain(self, underlying, expiry=None):
        rows = [OptionChainRow(strike=float(s), call_oi=1000.0 + (s - 23000) / 10, put_oi=2000.0 - (s - 23000) / 10, call_ltp=50.0, put_ltp=40.0)
                for s in range(23000, 25001, 100)]
        return OptionChain(underlying=underlying, expiry="2026-03-26", underlying_ltp=24030.0, rows=rows)


def test_new_tools_are_registered_strict_and_take_no_client_candles():
    reg = tools.registry()
    assert set(NEW) <= set(reg) and all(reg[n].kind == "read" for n in NEW)
    for spec in tools.specs(list(NEW)):
        assert spec["input_schema"]["additionalProperties"] is False
    _, me = _owner("hc2b3-schema@example.com")
    sneaky = _call(me["id"], "run_backtest", {"strategy_id": "ema_rsi_scalper_1m", "symbol": "NIFTY", "candles": [{"close": 1}]})
    assert not sneaky.ok and "invalid arguments" in sneaky.error                             # evidence is server data only
    assert not _call(me["id"], "get_candles", {"symbol": "NIFTY; DROP", "timeframe": "5min"}).ok
    assert not _call(me["id"], "get_candles", {"symbol": "NIFTY", "timeframe": "2min"}).ok


def test_without_a_broker_session_every_tool_fails_closed():
    _, me = _owner("hc2b3-nobroker@example.com")
    args = {"get_candles": {"symbol": "NIFTY"}, "get_quote": {"symbol": "NIFTY"}, "get_option_chain": {"underlying": "NIFTY"},
            "get_market_regime": {"symbol": "NIFTY"}, "run_backtest": {"strategy_id": "ema_rsi_scalper_1m", "symbol": "NIFTY", "timeframe": "1min"}}
    for name in NEW:
        res = _call(me["id"], name, args[name])
        assert not res.ok and "broker session" in res.error.lower() and res.data is None, name


def test_candles_and_regime_summarise_server_bars_with_as_of(monkeypatch):
    _, me = _owner("hc2b3-candles@example.com")
    df = _df()
    seen = _fake_frame(monkeypatch, df)
    res = _call(me["id"], "get_candles", {"symbol": "nifty", "timeframe": "5min", "days": 3})
    assert res.ok and res.source == "broker:fake" and seen[0][0] == me["tenant_id"] and seen[0][1:] == ("nifty", "NSE", "5min", 3)
    assert res.data["bars"] == 300 and res.data["last_close"] == round(float(df["close"].iloc[-1]), 2) and len(res.data["last_bars"]) == 5
    assert res.as_of == df.index[-1].isoformat() and res.data_timestamps["first_bar"] == df.index[0].isoformat()
    regime = _call(me["id"], "get_market_regime", {"symbol": "NIFTY"})
    assert regime.ok and regime.data["regime"] == "TRENDING_UP" and regime.data["reasons"] and regime.as_of == df.index[-1].isoformat()


def test_quote_and_option_chain(monkeypatch):
    _, me = _owner("hc2b3-chain@example.com")

    async def broker(ctx):
        return _Adapter(), "broker:fake"
    monkeypatch.setattr(market, "_broker", broker)
    q = _call(me["id"], "get_quote", {"symbol": "nifty"})
    assert q.ok and q.data["ltp"] == 105.0 and q.data["change_pct"] == 5.0 and q.as_of == NOW.isoformat()
    c = _call(me["id"], "get_option_chain", {"underlying": "NIFTY"})
    assert c.ok and c.data["atm_strike"] == 24000.0 and len(c.data["near_atm"]) == 11
    assert c.data["pcr"] == round(c.data["total_put_oi"] / c.data["total_call_oi"], 2) and c.data["max_pain"] is not None
    assert c.data["top_call_oi"][0]["strike"] == 25000.0 and c.data["top_put_oi"][0]["strike"] == 23000.0


def test_backtest_runs_on_server_bars_and_flags_a_small_sample(monkeypatch):
    _, me = _owner("hc2b3-bt@example.com")
    _fake_frame(monkeypatch, _df(n=900, freq="1min", step=0.01))
    res = _call(me["id"], "run_backtest", {"strategy_id": "ema_rsi_scalper_1m", "symbol": "NIFTY", "timeframe": "1min", "days": 3})
    assert res.ok and isinstance(res.data["trades"], int) and res.data["min_trades_for_statistics"] == 30
    assert res.data["sample"] == ("insufficient" if res.data["trades"] < 30 else "ok") and "trades_list" not in res.data
    unknown = _call(me["id"], "run_backtest", {"strategy_id": "no_such_strategy", "symbol": "NIFTY"})
    assert not unknown.ok and "unknown or not allowed strategy" in unknown.error


def test_agent_answers_from_candles(monkeypatch):
    _, me = _owner("hc2b3-agent@example.com")
    df = _df()
    _fake_frame(monkeypatch, df)
    last = round(float(df["close"].iloc[-1]), 2)
    fake = _Scripted([("call", [("get_candles", {"symbol": "NIFTY"})]),
                      ("text", f'{{"text": "NIFTY last close {last} on the server bars.", "claims": [{{"statement": "last close {last}", "source": "t1_0"}}]}}')])
    ans = _run(_agent(me["id"], fake, "Where is NIFTY now?"))
    assert ans.stopped == "answered" and ans.claims[0]["source"] == "t1_0" and str(last) in ans.text
