"""SFC P1 (owner APPROVED 19 Nov, docs/plans/strategy-first-chart-PLAN.md): his own strategy on the chart - the plot
API (flag `strategy_chart`).

Checks: only the indicators his conditions read are drawn, each in its pane (SPEC test 1's server side); every signal
bar is a marker, the taken ones are exactly the backtest's entries (SPEC test 8's server side); exits name their rule;
the same bars give the same answer twice (SPEC test 5's server side); the routes are his own (another user: 404), behind
the flag, and read bars the way his bot does (his user and his own broker session). Made-up bars only."""
from __future__ import annotations

import asyncio
import json
import uuid

import pandas as pd

from app.core.models import RiskConfig
from app.db.models import CustomStrategyRecord
from app.market_store.models import BarsResult
from app.quality.lookahead import synthetic_bars
from app.strategy_v2.adapter import StrategyV2Strategy
from app.strategy_v2.plot import plot
from app.strategy_v2.schema import StrategyV2
from tests.test_auth_api import _session_factory, client
from tests.test_n1_e1_execution_core import _flag
from tests.test_trading_worker import _tenant

RISK = RiskConfig(capital=1_000_000, risk_per_trade_pct=0.5)
EMA = lambda n: {"kind": "indicator", "block": "EMA", "params": {"x": "close", "n": n}}   # noqa: E731
RAW = {"schema": "atp.strategy/2", "version": 1, "name": "EMA cross + RSI", "timeframe": "5m",
       "entry": {"sides": "both",
                 "long": {"op": "AND", "items": [{"id": "c1", "left": EMA(9), "cmp": "crosses_above", "right": EMA(21)},
                                                 {"id": "c2", "left": {"kind": "indicator", "block": "RSI", "params": {"n": 14}},
                                                  "cmp": "gt", "right": {"kind": "value", "value": 45}}]},
                 "short": {"op": "AND", "items": [{"id": "s1", "left": EMA(9), "cmp": "crosses_below", "right": EMA(21)}]}},
       "exit": {"stop": {"kind": "atr", "value": 1.5, "atr_period": 14}, "target": {"kind": "atr", "value": 3, "atr_period": 14}}}


def _strategy() -> StrategyV2Strategy:
    return StrategyV2Strategy("custom:1", StrategyV2.model_validate(RAW))


def _bars(n=900, seed=5) -> pd.DataFrame:
    return synthetic_bars(n, tf="5m", seed=seed)


def test_only_its_own_indicators_each_in_its_pane():
    out = plot(_strategy(), _bars(), "S", RISK)
    labels = {s["label"]: s["pane"] for s in out["series"]}
    assert labels == {"EMA(close, 9)": "price", "EMA(close, 21)": "price", "RSI(14)": "own:RSI"}    # nothing else
    assert all(len(s["points"]) > 100 for s in out["series"])


def test_every_signal_bar_is_a_marker_and_the_taken_ones_are_the_backtests_entries():
    strat = _strategy()
    df = _bars()
    out = plot(strat, df, "S", RISK)
    ev, _ = strat.evaluation(df.iloc[-3000:], "S")
    signals = {pd.Timestamp(ev.index[t]).isoformat() for t in (*ev.signal_bars("long"), *ev.signal_bars("short"))}
    signal_marks = [m for m in out["markers"] if m["kind"] in ("entry", "skipped")]
    assert {m["t"] for m in signal_marks} == signals and len(signals) >= 4
    entries = [m for m in out["markers"] if m["kind"] == "entry"]
    assert len(entries) == len(out["trades"]) and {m["t"] for m in entries} == {t["entry_time"] for t in out["trades"]}
    exits = [m for m in out["markers"] if m["kind"] == "exit"]
    assert exits and all(m["rule"] for m in exits)
    first = entries[0]["id"]
    # P3: the conditions of the rule that fired (the long side's for a long entry, the short side's for a short one)
    assert {row["id"] for row in out["why"][first]} == ({"c1", "c2"} if entries[0]["side"] == "long" else {"s1"})
    assert any(row["held"] and row["values"] for row in out["why"][first])


def test_the_same_bars_give_the_same_answer():
    df = _bars()
    assert plot(_strategy(), df, "S", RISK) == plot(_strategy(), df, "S", RISK)


# --- the routes ------------------------------------------------------------------------------------------------------
def _org(tag: str, flag_on: bool = True):
    t = _tenant(f"sfc-{tag}-{uuid.uuid4().hex[:6]}@example.com", with_credentials=False)
    if flag_on:
        _flag("strategy_chart", t["tenant_id"])
    return t


def _save(t, raw: dict) -> int:
    async def go():
        async with _session_factory() as session:
            row = CustomStrategyRecord(tenant_id=t["tenant_id"], user_id=t["user_id"], name=raw.get("name", "x"), config_json=json.dumps(raw))
            session.add(row)
            await session.commit()
            return row.id
    return asyncio.run(go())


def _fake_reads(monkeypatch, frame: pd.DataFrame):
    seen = {}

    async def read_bars(session, symbol, interval, start, end, **kw):
        seen.update(symbol=symbol, interval=interval, user=kw.get("user"), rest=kw.get("rest"))
        return BarsResult(symbol=symbol, exchange="NSE", interval=interval, frame=frame, source="lake:intraday")

    async def own_rest(session, user, symbol, exchange, **kw):
        return "his-own-session"
    import app.market_store as ms
    import app.market_store.sources as src
    monkeypatch.setattr(ms, "read_bars", read_bars)
    monkeypatch.setattr(src, "own_rest", own_rest)
    return seen


def test_the_routes_are_his_own_behind_the_flag_and_read_like_his_bot(monkeypatch):
    seen = _fake_reads(monkeypatch, _bars())
    me, other, off = _org("me"), _org("other"), _org("off", flag_on=False)
    sid = _save(me, RAW)
    v1 = {"name": "old v1", "timeframe": "5min", "long_conditions": [
        {"left": {"type": "indicator", "indicator": "EMA", "period": 9}, "operator": "GT", "right": {"type": "indicator", "indicator": "EMA", "period": 21}}]}
    v1_id = _save(me, v1)
    listed = client.get("/api/strategies/v2/chart-list", headers=me["headers"]).json()["strategies"]
    assert {s["id"]: s["drawable"] for s in listed} == {f"custom:{sid}": True, f"custom:{v1_id}": True}      # v1 drawn through translate
    assert client.get("/api/strategies/v2/chart-list", headers=off["headers"]).status_code in (403, 404, 503)

    body = {"strategy_id": f"custom:{sid}", "symbol": "NIFTY 50", "exchange": "NSE"}
    got = client.post("/api/strategies/v2/plot", headers=me["headers"], json=body)
    assert got.status_code == 200, got.text
    data = got.json()
    assert data["available"] and data["timeframe"] == "5m" and data["markers"] and data["series"]
    assert seen["interval"] == "5m" and seen["user"] is not None and seen["rest"] == "his-own-session"   # his own bars only (M86)
    assert client.post("/api/strategies/v2/plot", headers=other["headers"], json=body).status_code == 404
