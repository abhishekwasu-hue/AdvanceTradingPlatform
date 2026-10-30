"""M55 T3 (data honesty): backtest, scanner and S&R requests run on the server's own candles.

A request that names a symbol and a timeframe (and, optionally, how far back) gets its bars from the server - the
organisation's broker session when it is usable, else ATP's stored Daily history (flag `backtest_stored_history`), else a
plain 409 that says what is missing (`bar_source.resolve`, the loader `/api/backtest/bars` uses). The run's `data_source`
is the label of what the server loaded; a label the browser sends is never trusted.

Candles the browser posts are accepted only as "paste your own data": `data_mode="paste"`, or (for older callers) any
request that posts candles without naming a mode. Such a run is recorded as `uploaded` whatever the client called it.
Never sample data on the server's side: sample candles a practice page builds are, to the server, uploaded candles.
"""
from __future__ import annotations

from typing import Any, Literal

from fastapi import HTTPException
from pydantic import BaseModel, Field

from app.core.models import OHLCVBar

UPLOADED = "uploaded"
SERVER = "server"
PASTE = "paste"
PASTE_NEEDS_CANDLES = "Paste your own data: no candles were sent."


class DataChoice(BaseModel):
    """Where a request's candles come from. `data_mode` omitted: posted candles are pasted data, no candles = server."""
    data_mode: Literal["server", "paste"] | None = None
    exchange: str = Field(default="NSE", pattern=r"^(NSE|BSE|NFO|BFO|MCX|CDS)$")
    lookback_days: int = Field(default=30, ge=1, le=730, description="calendar days the server loads; intraday at most 30")
    broker: str | None = Field(default=None, max_length=20)


def mode_of(choice: DataChoice, candles: list[Any] | None) -> str:
    if choice.data_mode is not None:
        return choice.data_mode
    return PASTE if candles else SERVER


async def load(session: Any, user: Any, symbol: str, timeframe: str, choice: DataChoice) -> tuple[list[OHLCVBar], str, str]:
    """(bars, data_source, line): the server's own bars for `symbol` / `timeframe`, through the same loader as
    `/api/backtest/bars` (lookback capped like that route)."""
    from app.backtest import bar_source
    from app.market_data.candles_routes import DAILY_MAX_LOOKBACK_DAYS, INTRADAY_MAX_LOOKBACK_DAYS
    if user is None:
        raise HTTPException(status_code=401, detail="Log in first.")
    cap = DAILY_MAX_LOOKBACK_DAYS if bar_source.timeframe_kind(timeframe) else INTRADAY_MAX_LOOKBACK_DAYS
    broker = choice.broker if choice.broker not in (None, "", "auto") else None
    bars = await bar_source.resolve(session, user.tenant_id, symbol, choice.exchange, timeframe,
                                    lookback_days=min(choice.lookback_days, cap), user=user, broker=broker)
    if bars.frame.empty:
        raise HTTPException(status_code=409, detail=bar_source.not_stored(symbol) if bars.kind == "stored"
                            else f"No candles for {symbol.strip().upper()} from {bar_source.broker_words(bars.source)}.")
    candles = [OHLCVBar.model_validate(b) for b in bar_source.frame_bars(bars.frame)]
    return candles, bars.source, bars.info()["line"]


async def fill(session: Any, user: Any, body: Any, *, timeframe: str | None = None) -> str:
    """Sets `body.candles` and `body.data_source` from where the candles really came from; returns the data source.
    `body` carries `symbol`, `candles`, `data_source` and the `DataChoice` fields (`timeframe` defaults to
    `body.base_timeframe`)."""
    if mode_of(body, body.candles) == PASTE:
        if not body.candles:
            raise HTTPException(status_code=422, detail=PASTE_NEEDS_CANDLES)
        body.data_source = UPLOADED
        return UPLOADED
    candles, source, _ = await load(session, user, body.symbol, timeframe or body.base_timeframe, body)
    body.candles = candles
    body.data_source = source
    return source


SCAN_MAX_SYMBOLS = 50


async def fill_scan(session: Any, user: Any, request: Any) -> tuple[str, list[str]]:
    """The scanner's version of `fill`: each symbol's candles from the server (at the symbol's own timeframe), or every
    posted candle as pasted data. Returns (data_source, warnings); a symbol the server has no bars for is left out with
    a warning, and when none has bars the first refusal is raised."""
    posted = any(s.candles for s in request.symbols)
    if mode_of(request, [1] if posted else []) == PASTE:
        if not posted:
            raise HTTPException(status_code=422, detail=PASTE_NEEDS_CANDLES)
        return UPLOADED, []
    if len(request.symbols) > SCAN_MAX_SYMBOLS:
        raise HTTPException(status_code=422, detail=f"At most {SCAN_MAX_SYMBOLS} symbols per scan.")
    sources: list[str] = []
    warnings: list[str] = []
    kept = []
    first_error: HTTPException | None = None
    for item in request.symbols:
        try:
            candles, source, _ = await load(session, user, item.symbol, item.timeframe, request)
        except HTTPException as exc:
            if exc.status_code == 401:
                raise
            first_error = first_error or exc
            warnings.append(f"{item.symbol.strip().upper()}: {exc.detail}")
            continue
        item.candles = candles
        kept.append(item)
        if source not in sources:
            sources.append(source)
    if not kept:
        raise first_error or HTTPException(status_code=422, detail="No symbols to scan.")
    request.symbols = kept
    return ",".join(sources), warnings


__all__ = ["UPLOADED", "SERVER", "PASTE", "PASTE_NEEDS_CANDLES", "SCAN_MAX_SYMBOLS", "DataChoice", "mode_of", "load", "fill",
           "fill_scan"]
