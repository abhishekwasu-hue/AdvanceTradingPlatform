"""H-C2b-3 (ADR-0019 §1): market and research read tools - candles, quote, option chain, regime, backtest.

All of them read server data only: the organisation's own broker session today (`evidence.server_frame`, the H-C1 a
path), the market-data lake once part B is merged. Nothing posted by a browser reaches a tool. When there is no
broker session the tool says so (the fix is on the Broker page) - it never falls back to sample data.
M71 W2-B (flag `market_store_reads`): candles and the regime read the own store first (`evidence.store_frame`): Daily
from the NSE files, intraday from the asker's own stored bars plus only the newest ones; what the store does not hold is
said plainly in `data_gaps`, never filled. Nothing stored -> the broker read exactly as before.

Outputs stay small (the model pays for every token): candles are summarised with the last few bars, the chain is the
strikes around the ATM plus the totals, the backtest returns its statistics and a sample-size warning, never the trades.
"""
from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Literal, Optional, Tuple

import pandas as pd
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.ai.tools import Tool, ToolContext, ToolResult, register

Timeframe = Literal["1min", "5min", "15min", "30min", "60min", "day"]
SYMBOL = Field(min_length=1, max_length=40, pattern=r"^[A-Za-z0-9 &._:-]+$", description="Index or stock symbol, e.g. as on the exchange")
EXCHANGE = Field(default="NSE", pattern=r"^(NSE|BSE|NFO|BFO|MCX|CDS)$")
CHAIN_STRIKES_EACH_SIDE = 5
LAST_BARS = 5


def _iso(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, pd.Timestamp):
        value = value.to_pydatetime()
    if isinstance(value, datetime):
        return (value if value.tzinfo else value.replace(tzinfo=timezone.utc)).isoformat()
    return str(value)


def _r(x: Any, nd: int = 2) -> Optional[float]:
    try:
        return None if x is None or x != x else round(float(x), nd)
    except (TypeError, ValueError):
        return None


async def _frame(ctx: ToolContext, symbol: str, exchange: str, timeframe: str, days: int) -> Tuple[pd.DataFrame, str]:
    """Server candles through the asker's own broker session (the H-C1 a path) -> (frame, source). M72 PR-1a (M71Q-2):
    with `byob_data_tier` off too, only a credential he linked himself - never a teammate's (`own_only`)."""
    from app.ai import evidence
    return await evidence.server_frame(ctx.session, ctx.tenant_id, symbol, exchange, timeframe, lookback_days=days, user=ctx.user,
                                       own_only=True)


async def _candles(ctx: ToolContext, symbol: str, exchange: str, timeframe: str, days: int) -> Tuple[pd.DataFrame, str, List[str]]:
    """M71 W2-B: the candles a read tool summarises -> (frame, source, gap lines): the own store first (flag on), else
    `_frame` exactly as before."""
    from app.ai import evidence
    stored = await evidence.store_frame(ctx.session, ctx.tenant_id, symbol, exchange, timeframe, lookback_days=days, user=ctx.user,
                                        now=ctx.now)
    if stored is not None:
        return stored.frame, stored.source, stored.gaps
    df, source = await _frame(ctx, symbol, exchange, timeframe, days)
    return df, source, []


def _with_gaps(data: Dict[str, Any], gaps: List[str]) -> Dict[str, Any]:
    """The store's plain gap lines (what it does not hold) next to the data - only when there are some."""
    return {**data, "data_gaps": gaps} if gaps else data


async def _backtest_frame(ctx: ToolContext, symbol: str, exchange: str, timeframe: str, days: int) -> Tuple[pd.DataFrame, str, Dict[str, Any]]:
    """Owner order 23 Oct: a backtest's bars - the broker session when usable, else ATP's stored Daily history (flag
    `backtest_stored_history`), else a plain 409 (app/backtest/bar_source.py). With a broker session: `_frame` exactly."""
    from app.backtest import bar_source
    bars = await bar_source.resolve(ctx.session, ctx.tenant_id, symbol, exchange, timeframe, lookback_days=days, user=ctx.user,
                                    broker_fetch=lambda: _frame(ctx, symbol, exchange, timeframe, days))
    return bars.frame, bars.source, bars.info()


async def _broker(ctx: ToolContext, symbol: str, exchange: str) -> Tuple[Any, str]:
    """The broker session a quote / chain is read through. BT1b (flag `byob_data_tier`): only the asking user's own
    session at a confirmed broker, for a symbol inside its budget; otherwise 409 (the tool reports the reason).
    M72 PR-1a (M71Q-2): with the flag off, only a credential the asker linked himself - never a teammate's (409)."""
    from app.brokers.token_lifecycle import build_adapter
    from app.market_data import live_access
    from app.market_data.candles_routes import _pick_record
    if await live_access.byob_on(ctx.session, ctx.tenant_id):
        own = await live_access.user_source(ctx.session, ctx.user if getattr(ctx.user, "tenant_id", None) == ctx.tenant_id else None,
                                            [(symbol, exchange)])
        if not own.allows(symbol, exchange):
            raise live_access.LiveDataRefused(own.reason(symbol, exchange))
        return own.adapter(), own.source
    if getattr(ctx.user, "tenant_id", None) != ctx.tenant_id:
        raise live_access.LiveDataRefused(live_access.NO_USER)
    record = await _pick_record(ctx.session, ctx.tenant_id, None, "primary", user_id=int(ctx.user.id))
    return build_adapter(record), f"broker:{record.broker_name}"


async def teammate_refusal(ctx: ToolContext, source: str) -> ToolResult | None:
    """M72 PR-1a (M71Q-2) for the tools that wrap a page's own read (the chain analyses, the screener run): with
    `byob_data_tier` off such a read goes through the organisation's first usable credential. When that credential
    was linked by a teammate, the tool does not run for this member (one person's broker data is never shown to
    another); his own, or no credential at all, and the tool runs exactly as before. Flag on: the page's own path
    already reads only his own session."""
    from app.market_data import live_access
    from app.market_data.candles_routes import TEAMMATE_BROKER, _pick_record
    if await live_access.byob_on(ctx.session, ctx.tenant_id):
        return None
    try:
        record = await _pick_record(ctx.session, ctx.tenant_id, None, "primary")
    except HTTPException:
        return None                                   # no usable session: the tool reports that itself
    owner = getattr(record, "user_id", None)          # NOT NULL on every stored credential
    if (owner is None or owner == getattr(ctx.user, "id", None)) and getattr(ctx.user, "tenant_id", None) == ctx.tenant_id:
        return None
    return ToolResult(False, None, None, source, error=TEAMMATE_BROKER)


async def _hub_connection(ctx: ToolContext) -> Any:
    """TM Part 1 (flag data_hub): the asking user's own connection on the data hub, or None (flag off, no user, no
    broker). The tool then reads the same price / chain snapshot every page of that connection reads."""
    user = ctx.user if getattr(ctx.user, "tenant_id", None) == ctx.tenant_id else None
    if user is None:
        return None
    try:
        from app.market_data import hub as data_hub
        conn = await data_hub.user_connection(ctx.session, user)
    except Exception:  # noqa: BLE001 - the hub is an addition; without it the tool reads as before
        return None
    return conn if conn is not None and not conn.renew else None


def _failed(source: str, exc: HTTPException) -> ToolResult:
    return ToolResult(False, None, None, source, error=str(exc.detail)[:300])


class _CandleArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbol: str = SYMBOL
    exchange: str = EXCHANGE
    timeframe: Timeframe = "5min"
    days: int = Field(default=5, ge=1, le=30, description="Sessions of history (intraday); daily bars always load a year")


class _QuoteArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbol: str = SYMBOL
    exchange: str = EXCHANGE


class _ChainArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    underlying: str = SYMBOL
    expiry: Optional[date] = Field(default=None, description="YYYY-MM-DD; omit for the nearest expiry")


class _BacktestArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    strategy_id: str = Field(min_length=1, max_length=80, description="A strategy id from the platform (built-in or custom:<id>)")
    symbol: str = SYMBOL
    exchange: str = EXCHANGE
    timeframe: Timeframe = "5min"
    days: int = Field(default=20, ge=2, le=60)


def summarise_frame(df: pd.DataFrame) -> Dict[str, Any]:
    close = df["close"].astype(float)
    first, last = float(close.iloc[0]), float(close.iloc[-1])
    tail = df.tail(LAST_BARS)
    return {"bars": len(df), "from": _iso(df.index[0]), "to": _iso(df.index[-1]), "first_close": _r(first), "last_close": _r(last),
            "change_pct": _r((last - first) / first * 100 if first else None), "high": _r(df["high"].max()), "low": _r(df["low"].min()),
            "last_bars": [{"t": _iso(ts), "o": _r(r.open), "h": _r(r.high), "l": _r(r.low), "c": _r(r.close),
                           "v": _r(getattr(r, "volume", 0), 0)} for ts, r in tail.iterrows()]}


async def get_candles(ctx: ToolContext, args: _CandleArgs) -> ToolResult:
    """OHLC candles from the organisation's broker session: range, change and the last few bars."""
    try:
        df, source, gaps = await _candles(ctx, args.symbol, args.exchange, args.timeframe, args.days)
    except HTTPException as exc:
        return _failed("candles", exc)
    summary = summarise_frame(df)
    return ToolResult(True, _with_gaps({"symbol": args.symbol.upper(), "timeframe": args.timeframe, **summary}, gaps), summary["to"], source,
                      {"first_bar": summary["from"], "last_bar": summary["to"]})


async def get_quote(ctx: ToolContext, args: _QuoteArgs) -> ToolResult:
    """The latest quote (LTP, day open/high/low, previous close, volume) from the broker session."""
    try:
        adapter, source = await _broker(ctx, args.symbol.upper(), args.exchange)
    except HTTPException as exc:
        return _failed("quote", exc)
    quote = await adapter.get_quote_for_symbol(args.symbol.upper(), args.exchange)
    if quote is None:
        return ToolResult(False, None, None, source, error=f"no quote for {args.symbol} on {args.exchange}")
    conn = await _hub_connection(ctx)
    if conn is not None:
        # One price everywhere: the connection's fresh hub tick is the last price the chart and watchlist show too.
        tick = await conn.tick_cache.fresh(conn.broker, args.exchange.upper(), args.symbol.upper())
        if tick is not None:
            quote = quote.model_copy(update={"ltp": float(tick.ltp), "timestamp": tick.best_ts})
    stamp = _iso(quote.timestamp) or ctx.now.isoformat()
    data = {"symbol": args.symbol.upper(), "ltp": _r(quote.ltp), "open": _r(quote.open), "high": _r(quote.high), "low": _r(quote.low),
            "prev_close": _r(quote.close), "volume": _r(quote.volume, 0),
            "change_pct": _r((quote.ltp - quote.close) / quote.close * 100 if quote.close else None)}
    return ToolResult(True, data, stamp, source, {"quote_time": _iso(quote.timestamp), "read_at": ctx.now.isoformat()})


def summarise_chain(chain: Any) -> Dict[str, Any]:
    from app.option_chain.analysis import compute_max_pain
    rows = sorted(chain.rows, key=lambda r: r.strike)
    spot = chain.underlying_ltp
    calls = sum(r.call_oi or 0 for r in rows)
    puts = sum(r.put_oi or 0 for r in rows)
    atm = min(rows, key=lambda r: abs(r.strike - spot)).strike if rows and spot else None
    near: List[Any] = rows
    if atm is not None:
        i = [r.strike for r in rows].index(atm)
        near = rows[max(0, i - CHAIN_STRIKES_EACH_SIDE): i + CHAIN_STRIKES_EACH_SIDE + 1]
    top = lambda key: [{"strike": r.strike, "oi": _r(getattr(r, key), 0)} for r in sorted(rows, key=lambda r: -(getattr(r, key) or 0))[:3]]  # noqa: E731
    return {"underlying": chain.underlying, "expiry": str(chain.expiry), "underlying_ltp": _r(spot), "atm_strike": atm,
            "total_call_oi": _r(calls, 0), "total_put_oi": _r(puts, 0), "pcr": _r(puts / calls if calls else None),
            "max_pain": _r(compute_max_pain(rows)), "top_call_oi": top("call_oi"), "top_put_oi": top("put_oi"),
            "near_atm": [{"strike": r.strike, "call_oi": _r(r.call_oi, 0), "call_chg_oi": _r(r.call_change_oi, 0), "call_ltp": _r(r.call_ltp),
                          "call_iv": _r(r.call_iv), "put_oi": _r(r.put_oi, 0), "put_chg_oi": _r(r.put_change_oi, 0), "put_ltp": _r(r.put_ltp),
                          "put_iv": _r(r.put_iv)} for r in near]}


async def get_option_chain(ctx: ToolContext, args: _ChainArgs) -> ToolResult:
    """The option chain for an underlying: totals, PCR, max pain, the biggest OI strikes and the strikes around the ATM."""
    try:
        adapter, source = await _broker(ctx, args.underlying.upper(), "NFO")
    except HTTPException as exc:
        return _failed("option_chain", exc)
    conn = await _hub_connection(ctx)
    if conn is not None and conn.record is not None:
        # TM Part 1: the connection's chain snapshot of the last few seconds (the chain page's, say) - no second call.
        from app.market_data import hub as data_hub
        chain = await data_hub.shared_chain(int(conn.record.id), args.underlying, args.expiry,
                                            lambda: adapter.get_option_chain(args.underlying.upper(), args.expiry))
    else:
        chain = await adapter.get_option_chain(args.underlying.upper(), args.expiry)
    if not chain.rows:
        return ToolResult(False, None, None, source, error=f"the broker returned an empty chain for {args.underlying}")
    return ToolResult(True, summarise_chain(chain), ctx.now.isoformat(), source, {"read_at": ctx.now.isoformat(), "expiry": str(chain.expiry)})


async def get_market_regime(ctx: ToolContext, args: _CandleArgs) -> ToolResult:
    """The deterministic regime (trending up/down, ranging, volatile, quiet) on server candles, with its reasons."""
    from app.ai.regime import classify_regime
    try:
        df, source, gaps = await _candles(ctx, args.symbol, args.exchange, args.timeframe, args.days)
    except HTTPException as exc:
        return _failed("regime", exc)
    regime = classify_regime(df)
    data = {"symbol": args.symbol.upper(), "timeframe": args.timeframe, "regime": regime.kind, "confidence": _r(regime.confidence),
            "adx": _r(regime.adx), "ema_slope_pct": _r(regime.ema_slope_pct), "atr_pct": _r(regime.atr_pct), "atr_ratio": _r(regime.atr_ratio),
            "bars": regime.bars, "reasons": regime.reasons}
    return ToolResult(True, _with_gaps(data, gaps), _iso(df.index[-1]), source, {"last_bar": _iso(df.index[-1])})


async def run_backtest(ctx: ToolContext, args: _BacktestArgs) -> ToolResult:
    """Backtest a platform strategy on server candles (never client data). Statistics only, with a sample-size warning."""
    from app.ai.compliance import WEAK_EVIDENCE_MIN_TRADES
    from app.backtest.engine import run_backtest as engine
    from app.core.models import RiskConfig
    from app.custom_strategies.resolver import resolve_strategy
    from app.risk_engine.routes import get_tenant_risk_config
    try:
        strategy = await resolve_strategy(args.strategy_id, ctx.user, ctx.session)
    except (KeyError, PermissionError) as exc:
        return ToolResult(False, None, None, "backtest", error=f"unknown or not allowed strategy {args.strategy_id!r}: {exc}"[:300])
    try:
        df, source, prices = await _backtest_frame(ctx, args.symbol, args.exchange, args.timeframe, args.days)
    except HTTPException as exc:
        return _failed("backtest", exc)
    from app.core import config as app_config
    # DEF-0: the organisation's capital, else the PAPER starting balance (the engine starts there too)
    risk = (await get_tenant_risk_config(ctx.tenant_id, ctx.session) or RiskConfig()).with_capital(app_config.PAPER_STARTING_CAPITAL)
    result = await asyncio.to_thread(engine, strategy, df, args.symbol.upper(), args.timeframe, risk)    # CPU-bound: off the event loop
    data = {"strategy_id": args.strategy_id, "symbol": args.symbol.upper(), "timeframe": args.timeframe, "from": _iso(df.index[0]), "to": _iso(df.index[-1]),
            "trades": result.total_trades, "win_rate": _r(result.win_rate), "net_pnl": _r(result.net_pnl), "profit_factor": _r(result.profit_factor),
            "max_drawdown": _r(result.max_drawdown), "expectancy": _r(result.expectancy), "capital": _r(risk.capital),
            "sample": "insufficient" if result.total_trades < WEAK_EVIDENCE_MIN_TRADES else "ok", "min_trades_for_statistics": WEAK_EVIDENCE_MIN_TRADES,
            "note": "Past simulated results on this window only; costs and slippage are approximations.",
            # CP6-15: the losses-in-a-row pause in this run, in plain words (None when it never engaged)
            "loss_streak_pause": (result.loss_streak or {}).get("note"),
            "prices": prices["words"], "prices_line": prices["line"]}
    return ToolResult(True, data, _iso(df.index[-1]), source, {"first_bar": _iso(df.index[0]), "last_bar": _iso(df.index[-1])})


class _DraftBacktestArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    config: Dict[str, Any] = Field(description="The draft strategy (CustomStrategyConfig) with every value the trader's own")
    symbol: str = SYMBOL
    exchange: str = EXCHANGE


async def backtest_draft(ctx: ToolContext, args: _DraftBacktestArgs) -> ToolResult:
    """CP6-2: the Copilot's draft paper strategy (never saved) on server candles through ATP's backtest engine - the whole
    window, its earlier and later parts, the walk-forward windows, costs, drawdown and the sample size. Called only by the
    Copilot chat's rules with a complete draft built from the trader's own words (never offered to the agent's model)."""
    from pydantic import ValidationError

    from app.ai import draft_backtest as dbt
    from app.core.models import RiskConfig
    from app.risk_engine.routes import get_tenant_risk_config
    from app.strategy_engine.declarative import CustomStrategyConfig
    try:
        config = CustomStrategyConfig.model_validate(args.config)
    except ValidationError as exc:
        return ToolResult(False, None, None, "backtest", error=f"the draft is not a complete strategy: {exc.errors()[:2]}"[:300])
    fetch_tf = dbt.FETCH_TIMEFRAME.get(config.timeframe, config.timeframe)
    try:
        df, source, prices = await _backtest_frame(ctx, args.symbol, args.exchange, fetch_tf, dbt.TEST_DAYS)
    except HTTPException as exc:
        return _failed("backtest", exc)
    risk = await get_tenant_risk_config(ctx.tenant_id, ctx.session) or RiskConfig()
    from app.backtest.data_policy import HoldoutError
    from app.market_lake import data_use
    try:
        windows = await data_use.windows_for(ctx.session, ctx.tenant_id, args.symbol, refused_only=True)   # M71 PL-7b
        data = await asyncio.to_thread(dbt.honest_test, config, df, args.symbol, fetch_tf, risk, use_windows=windows)  # CPU-bound
    except HoldoutError as exc:
        return ToolResult(False, None, None, "backtest", error=str(exc)[:300])
    data["exchange"] = args.exchange
    data["prices"], data["prices_line"] = prices["words"], prices["line"]
    return ToolResult(True, data, _iso(df.index[-1]), source, {"first_bar": _iso(df.index[0]), "last_bar": _iso(df.index[-1])})


for _tool in (
    Tool("get_candles", get_candles.__doc__ or "", _CandleArgs, get_candles, cost_units=2, timeout_seconds=20),
    Tool("get_quote", get_quote.__doc__ or "", _QuoteArgs, get_quote, cost_units=1, timeout_seconds=10),
    Tool("get_option_chain", get_option_chain.__doc__ or "", _ChainArgs, get_option_chain, cost_units=3, timeout_seconds=20),
    Tool("get_market_regime", get_market_regime.__doc__ or "", _CandleArgs, get_market_regime, cost_units=2, timeout_seconds=20),
    Tool("run_backtest", run_backtest.__doc__ or "", _BacktestArgs, run_backtest, cost_units=5, timeout_seconds=45),
    Tool("backtest_draft", backtest_draft.__doc__ or "", _DraftBacktestArgs, backtest_draft, cost_units=8, timeout_seconds=90, flag="ai_copilot",
         agent=False),
):
    register(_tool)

__all__ = ["teammate_refusal", "get_candles", "get_quote", "get_option_chain", "get_market_regime", "run_backtest", "backtest_draft", "summarise_frame", "summarise_chain"]
