"""M72 PR-5 (CP-4, finding F9, M72Q-3 default): the signal engine's reading for the Copilot's planner - public words only.

`get_engine_read` is offered only while the organisation's `trade_engine` flag is ON and the private engine is installed
(`engine_provider.loader.available()`); everyone else's chart questions use ATP's own zone layer (`get_chart_structure`
+ `get_market_zones`). It reads ONE symbol of the user's own engine runner settings, as of the last closed bar of his
signal timeframe, exactly as his signals are read (the same request builder, his own stored rows / own session - M71Q-2,
F15; nothing after that bar - no look-ahead). Only the runner's own user reads it (his deployment, his settings).

What reaches the model (and the answer) is a fixed set of generic, public words (`public_reading`, pure):
* each area as a PRICE RANGE with its side ("buyer" / "seller"), its state only when ATP has a public word for it
  (`chart_analyze.contract.AREA_STATES`; any other package token is dropped), its timeframe and where it sits against the
  last close (above / below / inside, the distance in points to its near edge);
* the structure: the higher timeframe's side (up / down / sideways / unclear) and the latest swing labels (HH / HL / LH /
  LL) per timeframe - labels only, never a swing price;
* whether a signal formed on the last closed bar, with its side and the area as a price range.
Never: a model id, an instance id, an area id, the evidence tokens, the internal invalidation reference, the entry zone's
own figures, the engine's version or name, its data-problem tokens or the user's engine settings (atp-private CLAUDE.md).
A read with any data problem is not a reading: the tool says the data is incomplete and the zone layer answers instead.
Kind "read": nothing here places, blocks or changes an order (ADR-0004 / ADR-0006).
"""
from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.ai.tools import Tool, ToolContext, ToolResult, register

FLAG = "trade_engine"
NAME = "get_engine_read"
SOURCE = "signal_engine"
MAX_AREAS = 6
MAX_SWINGS = 4                     # the latest swing labels kept per timeframe
NOT_SET_UP = ("the signal engine reading is not set up for this user (the admin sets whose signals the engine reads and "
              "its symbols); answer from get_chart_structure and get_market_zones instead")
NOT_LISTED = "the signal engine reads only the symbols in this user's engine settings: {symbols}; for {symbol} use get_chart_structure and get_market_zones"
INCOMPLETE = ("the signal engine's data for the last closed bar is not complete, so there is no engine reading now; answer "
              "from get_chart_structure and get_market_zones instead")
FAILED = "the signal engine could not read {symbol} right now; answer from get_chart_structure and get_market_zones instead"
# Fields that must never leave this module (checked by the tests on every prompt and answer).
PRIVATE_FIELDS = ("model_id", "instance_id", "area_id", "evidence", "invalidation", "entry_zone", "engine_settings", "data_problems")


class _EngineArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbol: str = Field(min_length=1, max_length=40, pattern=r"^[A-Za-z0-9 &._:-]+$",
                        description="One of the symbols the user's signal engine reads (an index or an NSE stock)")


def _round(value: float) -> float:
    return round(float(value), 2)


def _where(low: float, high: float, price: float | None) -> tuple[str | None, float | None]:
    if price is None:
        return None, None
    if low <= price <= high:
        return "inside", 0.0
    if low > price:
        return "above", _round(low - price)
    return "below", _round(price - high)


def public_reading(result: Any, *, symbol: str, timeframe: str, last_close: float | None, as_of: str | None) -> dict[str, Any]:
    """The engine's result in generic public words only (see the module text). Pure; the input is an `EngineResult`."""
    from app.chart_analyze.contract import AREA_STATES, TREND_SIDES
    areas: list[dict[str, Any]] = []
    for a in result.areas:
        low, high = _round(min(a.band.low, a.band.high)), _round(max(a.band.low, a.band.high))
        where, distance = _where(low, high, last_close)
        areas.append({"side": a.side, "low": low, "high": high, "state": a.state if a.state in AREA_STATES else None,
                      "timeframe": str(a.timeframe), "where": where, "distance_points": distance})
    areas.sort(key=lambda x: (x["distance_points"] is None, x["distance_points"] or 0.0))
    swings: dict[str, list[str]] = {}
    for s in sorted(result.swings, key=lambda s: s.at):
        swings.setdefault(str(s.timeframe), []).append(s.label)
    higher = result.higher_side
    structure = {"higher_timeframe": str(higher.timeframe) if higher is not None else None,
                 "higher_side": higher.side if higher is not None and higher.side in TREND_SIDES else None,
                 "swings": [{"timeframe": tf, "latest": labels[-MAX_SWINGS:]} for tf, labels in swings.items()]}
    signal = None
    by_area = {a.area_id: a for a in result.areas}
    for s in result.signals:
        band = by_area[s.area_id].band if s.area_id in by_area else None
        card_range = getattr(s.card, "area_range", None)
        if card_range is not None:
            low, high = sorted((float(card_range[0]), float(card_range[1])))
        elif band is not None:
            low, high = min(band.low, band.high), max(band.low, band.high)
        else:
            continue                      # a signal with no area shown as a range is not described (no single price)
        signal = {"side": s.side, "timeframe": str(s.timeframe), "area_low": _round(low), "area_high": _round(high)}
    return {"symbol": symbol, "timeframe": timeframe, "as_of": as_of, "last_close": None if last_close is None else _round(last_close),
            "areas": areas[:MAX_AREAS], "structure": structure, "signal_on_last_bar": signal,
            "words": "Areas are price ranges with their side and state; the structure is the higher timeframe's side and the "
                     "swing labels. A reading of structure, not advice."}


async def _settings_for(ctx: ToolContext) -> Any:
    """His engine runner settings (`RunnerSettings`), or None when this user is not the runner's user."""
    from app.workers import engine_runner as R
    raw = (await R._stored_settings(ctx.session)).get(str(ctx.tenant_id))
    if raw is None:
        return None
    try:
        cfg = R.parse_settings(ctx.tenant_id, raw)
    except R.SettingsProblem:
        return None
    return cfg if cfg.user_id == ctx.user.id else None


async def build_request(ctx: ToolContext, cfg: Any, watch: Any, engine: Any) -> tuple[Any, bool, float | None]:
    """(the EngineRequest of the last closed bar or None, whether the data is complete, the last close) - the runner's
    own request builder and data path (his stored rows, his own session's newest bars), nothing after that bar."""
    from app.engine_provider import frames as F
    from app.engine_provider import sources as S
    from app.market_lake import own_bars
    from app.workers import engine_runner as R
    tf = cfg.signal_timeframe
    timeframes = list(dict.fromkeys([tf, *engine.timeframes]))
    now = ctx.now
    first = (now.astimezone(F.IST) - timedelta(days=max(cfg.daily_days, cfg.intraday_days) + R.DUE_LOOKBACK_DAYS + 7)).date()
    cal = await S.load_calendar(ctx.session, watch.exchange, first, now.astimezone(F.IST).date() + timedelta(days=1))
    due = R.due_bar(cal, tf, now, cfg.delay_seconds)
    if due is None:
        return None, False, None
    starts = R.window_starts(cal, timeframes, due, cfg)
    scope = await own_bars.scope_for(ctx.session, ctx.user, watch.key)
    extra: dict[str, Any] = {"store": await R.store_read(ctx.session, ctx.user, watch)} if await R.store_on(ctx.session, ctx.tenant_id) else {}
    data, _notes = await S.collect(ctx.session, key=watch.key, exchange=watch.exchange, timeframes=timeframes, starts=starts, now=due,
                                   cal=cal, scope=scope, broker=None, broker_symbol=watch.broker_symbol, **extra)
    built = F.build_request(data, timeframes, tf, due, starts, cal, settings=cfg.engine_settings)
    if built.request is None:
        return None, False, None
    frame = built.request.frames.get(tf)
    last = frame.bars[-1].close if frame is not None and frame.bars else None
    return built.request, not built.data_problems, last


async def get_engine_read(ctx: ToolContext, args: _EngineArgs) -> ToolResult:
    """The signal engine's reading of one of the user's engine symbols as of the last closed bar of his signal timeframe,
    in public words only: the areas as price ranges with side and state, the higher timeframe's side and the latest swing
    labels, and whether a signal formed on that bar (side and area range). No single price to act on; not advice."""
    from app.ai.tools.copilot import lake_key
    from app.engine_provider import loader
    from app.engine_provider.contract import INTERFACE_VERSION, EngineResult
    from app.engine_provider.frames import merge_problems
    engine = loader.load()
    if engine is None:
        return ToolResult(False, None, None, SOURCE, error=NOT_SET_UP)
    cfg = await _settings_for(ctx)
    if cfg is None or not cfg.symbols:
        return ToolResult(False, None, None, SOURCE, error=NOT_SET_UP)
    key = lake_key(args.symbol)
    watch = next((w for w in cfg.symbols if w.key == key or w.shown == args.symbol.strip().upper()), None)
    if watch is None:
        return ToolResult(False, None, None, SOURCE, error=NOT_LISTED.format(symbols=", ".join(w.shown for w in cfg.symbols),
                                                                             symbol=args.symbol.strip().upper()))
    if cfg.signal_timeframe not in engine.timeframes:
        return ToolResult(False, None, None, SOURCE, error=FAILED.format(symbol=watch.shown))
    request, complete, last = await build_request(ctx, cfg, watch, engine)
    if request is None or not complete:
        return ToolResult(False, None, None, SOURCE, error=INCOMPLETE)
    try:
        result = await asyncio.wait_for(asyncio.to_thread(engine.read, request), timeout=cfg.read_timeout_seconds)
    except Exception:  # noqa: BLE001 - a failing or slow read is "no engine reading now"; the zone layer answers
        return ToolResult(False, None, None, SOURCE, error=FAILED.format(symbol=watch.shown))
    if not isinstance(result, EngineResult) or result.interface != INTERFACE_VERSION:
        return ToolResult(False, None, None, SOURCE, error=FAILED.format(symbol=watch.shown))
    if merge_problems(result, ()).data_problems:
        return ToolResult(False, None, None, SOURCE, error=INCOMPLETE)
    as_of = request.as_of.isoformat()
    data = public_reading(result, symbol=watch.shown, timeframe=cfg.signal_timeframe, last_close=last, as_of=as_of)
    return ToolResult(True, data, as_of, SOURCE, {"last_closed_bar": as_of})


def engine_ready() -> bool:
    """Offered only while the private engine is installed (and the flag is on - `Tool.flag`)."""
    from app.engine_provider import loader
    return loader.available()


register(Tool(NAME, get_engine_read.__doc__ or "", _EngineArgs, get_engine_read, cost_units=3, timeout_seconds=40, flag=FLAG,
              ready=engine_ready))

__all__ = ["get_engine_read", "public_reading", "build_request", "engine_ready", "FLAG", "NAME", "PRIVATE_FIELDS"]
