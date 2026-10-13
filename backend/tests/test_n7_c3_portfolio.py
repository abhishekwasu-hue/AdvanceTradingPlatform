"""N7 C3: the portfolio backtest (flag backtest_portfolio, off by default).

The engine walks every symbol's bars on one book: exits before entries at each time, one shared TradingDayState (open
positions, trades per day, daily loss, cool-down) and, by default, a cash limit. A one-symbol run without the cash limit
makes the single engine's trades exactly; a day's marked equity never moves when later bars are rewritten (no
look-ahead). The route records a ledger trial (deflated for the organisation's portfolio runs) and holds lake windows
to the sealed holdout."""
import asyncio
import json
import uuid
from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import select

from app.backtest import portfolio as P
from app.backtest.engine import run_backtest
from app.backtest.models import ModelSet
from app.core.models import RiskConfig
from app.db.models import MdCandleRecord, ResearchTrialRecord
from app.market_lake.bhavcopy_backfill import SOURCE, bar_end
from app.platform.controls import DEFAULT_OFF_FLAGS
from app.strategy_engine.registry import registry
from tests.test_auth_api import _session_factory, client
from tests.test_deployments_api import _auth
from tests.test_n1_e1_execution_core import _flag
from tests.utils import make_series, noisy_uptrend

STRATEGY = "ema_rsi_scalper_1m"


def _run(coro):
    return asyncio.run(coro)


def _frame(seed, days=4, start=100.0):
    return pd.concat([make_series(noisy_uptrend(n=300, seed=seed + k, start=start + k), start=f"2024-01-{2 + k:02d} 09:15")
                      for k in range(days)])


def _bars(df):
    return [{"timestamp": ts.isoformat(), "open": float(r.open), "high": float(r.high), "low": float(r.low), "close": float(r.close),
             "volume": float(r.volume)} for ts, r in df.iterrows()]


def _key(t):
    return (str(t.symbol), str(t.entry_time), str(t.exit_time), float(t.quantity), round(float(t.pnl), 6), t.exit_reason)


# --- the engine -----------------------------------------------------------------------------------------------------------------

def test_one_symbol_without_the_cash_limit_is_the_single_engine():
    s, df = registry.get(STRATEGY), _frame(7)
    single = run_backtest(s, df, "AAA", "1min", RiskConfig())
    book = P.run_portfolio_backtest(s, {"AAA": df}, "1min", RiskConfig(), settings=P.PortfolioSettings(cash_limit=False))
    assert single.total_trades > 10
    assert [_key(t) for t in book.trades] == [_key(t) for t in single.trades]
    assert book.net_pnl == single.net_pnl and book.final_equity == pytest.approx(RiskConfig().capital + single.net_pnl)


def test_the_book_shares_one_set_of_risk_limits():
    s = registry.get(STRATEGY)
    frames = {"AAA": _frame(7), "BBB": _frame(7), "CCC": _frame(7)}            # the same bars: every signal at the same time
    risk = RiskConfig(max_open_positions=1, max_consecutive_losses=1000)
    book = P.run_portfolio_backtest(s, frames, "1min", risk, settings=P.PortfolioSettings(cash_limit=False))
    alone = run_backtest(s, frames["AAA"], "AAA", "1min", risk)
    assert book.refused.get("risk: Max open positions reached", 0) > 0
    spans = sorted((t.entry_time, t.exit_time) for t in book.trades)
    assert all(b[0] >= a[1] for a, b in zip(spans, spans[1:]))              # never two positions at once
    assert {t.symbol for t in book.trades} == {"AAA"} and book.total_trades == alone.total_trades   # AAA, first in order, takes each


def test_the_cash_limit_refuses_what_the_book_cannot_pay_for():
    s = registry.get(STRATEGY)
    frames = {"AAA": _frame(7), "BBB": _frame(20, start=200.0)}
    risk = RiskConfig(max_consecutive_losses=1000)
    capped = P.run_portfolio_backtest(s, frames, "1min", risk)
    free = P.run_portfolio_backtest(s, frames, "1min", risk, settings=P.PortfolioSettings(cash_limit=False))
    assert capped.refused.get("cash", 0) > 0 and "cash" not in free.refused and free.total_trades > capped.total_trades
    for t in capped.trades:                                                 # every position fitted the cash at its entry
        assert t.entry_price * t.quantity <= risk.capital * 1.5
    weight = P.run_portfolio_backtest(s, frames, "1min", RiskConfig(capital=10_000_000.0, max_consecutive_losses=1000),
                                      settings=P.PortfolioSettings(max_weight_pct=1.0))
    assert weight.refused.get("weight", 0) > 0


@pytest.mark.parametrize("cut_day", [1, 2, 3])
def test_a_day_s_equity_never_moves_when_later_bars_are_rewritten(cut_day):
    """No look-ahead: the marked daily equity before day `cut_day` is the same when every bar from that day on is replaced
    by noise (all symbols), and the trades closed before it are the same."""
    s = registry.get(STRATEGY)
    frames = {"AAA": _frame(7), "BBB": _frame(20, start=200.0), "CCC": _frame(33, start=50.0)}
    risk = RiskConfig(max_consecutive_losses=1000)
    full = P.run_portfolio_backtest(s, frames, "1min", risk, settings=P.PortfolioSettings(cash_limit=False))
    cut = pd.Timestamp(f"2024-01-{2 + cut_day:02d}")
    rng = np.random.default_rng(cut_day)
    noisy = {}
    for sym, df in frames.items():
        df = df.copy()
        later = df.index >= cut
        scale = rng.uniform(0.5, 1.5, later.sum())
        for col in ("open", "high", "low", "close"):
            df.loc[later, col] = df.loc[later, col].to_numpy() * scale
        df.loc[later, "high"] = df.loc[later, ["open", "high", "low", "close"]].max(axis=1)
        df.loc[later, "low"] = df.loc[later, ["open", "high", "low", "close"]].min(axis=1)
        noisy[sym] = df
    other = P.run_portfolio_backtest(s, noisy, "1min", risk, settings=P.PortfolioSettings(cash_limit=False))
    before = [d for d in full.daily if d["day"] < str(cut.date())]
    assert before and before == [d for d in other.daily if d["day"] < str(cut.date())]
    assert [_key(t) for t in full.trades if t.exit_time < cut] == [_key(t) for t in other.trades if t.exit_time < cut]


def test_models_that_defer_an_entry_or_margin_are_not_offered_yet():
    s, frames = registry.get(STRATEGY), {"AAA": _frame(7, days=2)}
    for models in (ModelSet(fill="next_open"), ModelSet(latency="fixed_ms"), ModelSet(margin="span_like")):
        with pytest.raises(P.PortfolioError):
            P.run_portfolio_backtest(s, frames, "1min", RiskConfig(), models=models)
    slipped = P.run_portfolio_backtest(s, frames, "1min", RiskConfig(), models=ModelSet(slippage="spread_half"))
    assert slipped.execution["models"]["slippage"] == "spread_half"
    with pytest.raises(P.PortfolioError):
        P.run_portfolio_backtest(s, {f"S{i}": frames["AAA"] for i in range(P.MAX_SYMBOLS + 1)}, "1min", RiskConfig())


# --- the route ------------------------------------------------------------------------------------------------------------------

def _org(tag, on=True):
    headers = _auth(f"n7c3-{tag}-{uuid.uuid4().hex[:6]}@example.com")
    tenant = client.get("/api/auth/me", headers=headers).json()["tenant_id"]
    if on:
        _flag(P.FLAG, tenant)
    return headers, tenant


def _trials(tenant_id, kind):
    async def go():
        async with _session_factory() as session:
            return list(await session.scalars(select(ResearchTrialRecord).where(ResearchTrialRecord.tenant_id == tenant_id)))
    return [r for r in _run(go()) if json.loads(r.dsl_json).get("kind") == kind]


BODY = {"strategy_id": STRATEGY, "base_timeframe": "1min", "symbols": ["aaa", "bbb"],
        "risk_config": {"max_consecutive_losses": 1000}, "settings": {"cash_limit": False}}


def test_the_route_needs_its_flag_and_records_a_deflated_trial():
    assert P.FLAG in DEFAULT_OFF_FLAGS
    candles = {"AAA": _bars(_frame(7)), "BBB": _bars(_frame(20, start=200.0))}
    off, _ = _org("off", on=False)
    r = client.post("/api/backtest/portfolio", headers=off, json={**BODY, "candles": candles})
    assert r.status_code == 503 and r.headers.get("X-Feature-Disabled") == P.FLAG
    headers, tenant = _org("on")
    first = client.post("/api/backtest/portfolio", headers=headers, json={**BODY, "candles": candles})
    assert first.status_code == 200, first.text
    out = first.json()
    assert out["symbols"] == ["AAA", "BBB"] and out["data_source"] == "uploaded" and out["total_trades"] > 0
    assert out["deflation"]["recorded"] and out["deflation"]["n_trials"] == 1 and out["reproducibility"]["engine_version"] == P.ENGINE_VERSION
    again = client.post("/api/backtest/portfolio", headers=headers, json={**BODY, "candles": candles})
    assert again.json()["deflation"]["n_trials"] == 2
    assert again.json()["reproducibility"]["result_hash"] == out["reproducibility"]["result_hash"]          # reproducible
    rows = _trials(tenant, "portfolio_backtest")
    assert len(rows) == 2 and json.loads(rows[0].dsl_json)["symbols"] == ["AAA", "BBB"] and rows[0].symbol == "PORTFOLIO:2"
    assert client.post("/api/backtest/portfolio", headers=headers, json={**BODY, "candles": {"AAA": candles["AAA"]}}).status_code == 422
    assert client.post("/api/backtest/portfolio", headers=headers,
                       json={**BODY, "candles": candles, "execution_models": {"margin": "span_like"}}).status_code == 403   # backtest_models off
    other, _ = _org("other")                                                   # BT1b: its own search only
    assert client.post("/api/backtest/portfolio", headers=other, json={**BODY, "candles": candles}).json()["deflation"]["n_trials"] == 1


def _sessions(n, start=date(2024, 1, 1)):
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def test_a_lake_portfolio_reads_the_server_s_bars_and_stops_before_the_holdout(monkeypatch):
    headers, _ = _org("lake")
    days = _sessions(90)
    syms = [f"P{uuid.uuid4().hex[:6].upper()}" for _ in range(2)]

    async def seed():
        async with _session_factory() as session:
            for k, sym in enumerate(syms):
                for i, d in enumerate(days):
                    px = 100.0 + k * 10 + i * 0.4 + (4.0 if i % 9 == 0 else 0.0)
                    session.add(MdCandleRecord(instrument_key=f"NSE:{sym}", timeframe="day", ts=bar_end(d), source=SOURCE["cm"], version=1,
                                               open=px - 0.5, high=px + 1.0, low=px - 1.5, close=px, volume=1000 + i))
            await session.commit()
    _run(seed())
    body = {"strategy_id": STRATEGY, "base_timeframe": "day", "symbols": syms, "lake": {"start": str(days[0]), "end": str(days[-1])}}
    r = client.post("/api/backtest/portfolio", headers=headers, json=body)
    assert r.status_code == 200, r.text
    assert r.json()["data_source"] == "lake:nse_bhav:cm:adjusted" and r.json()["per_symbol"][syms[0]]["bars"] == 90
    monkeypatch.setenv("BACKTEST_HOLDOUT_START", str(days[70]))
    sealed = client.post("/api/backtest/portfolio", headers=headers, json=body)
    assert sealed.status_code == 422 and "sealed holdout" in sealed.text
    assert client.post("/api/backtest/portfolio", headers=headers,
                       json={**body, "lake": {"start": str(days[0]), "end": str(days[69])}}).status_code == 200
    both = client.post("/api/backtest/portfolio", headers=headers, json={**body, "candles": {syms[0]: _bars(_frame(7, days=1))}})
    assert both.status_code == 422
