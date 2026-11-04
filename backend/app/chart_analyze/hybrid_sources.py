"""ADV-2 (owner 4 Nov, docs/design/ADV2_SPEC.md A + B): where the hybrid reading's candles and areas come from.

A - one candle source per timeframe. ATP's stored candles (`price_action_layer.load_frame`, unchanged) when they hold at
least MIN_BARS; else the user's OWN broker candles for the same window, read only, through the chart's own candle read
(`candles_routes.candles`: the entitlement guard / the reader's own credential - never a teammate's, never another
organisation's). Nothing read here is written anywhere: not to the shared lake, not to the user's own bars (licence).
Intraday timeframes are built from the broker's 1-minute bars and Daily / Weekly from its daily bars with the lake's
own conversions (`price_action_layer._to_frame`: closed buckets from the exchange's session open, `bar_end` <= the
as-of minute), so a bar still forming is never read (no look-ahead). Each broker series is read at most once per
reading, at the longest window the broker serves (`candles_routes.INTRADAY_MAX_LOOKBACK_DAYS` /
`DAILY_MAX_LOOKBACK_DAYS`), and cut to each timeframe's own window.

B - areas for the picked symbol on the spot. When the zones job has no bands for the symbol (or zones are off), the
bands of this read are computed from the same frames with the zones engine's own functions (`job.plan_starts`,
`job.frames_from`, `snapshot.build_snapshot`, `chart.bands_from_rows`) and the platform's zone settings
(`store.platform_settings`) - no new rule, no new number. Not stored: a read-time result, cached like the layer
(`price_action_layer.CACHE_SECONDS`, `CACHE_SIZE`).
Reference only: nothing here places, proposes or changes an order.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import OrderedDict
from datetime import datetime
from typing import Any

import pandas as pd
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import User

logger = logging.getLogger(__name__)

INTRADAY = "1min"
DAILY = "day"
NO_SESSION_STATUS = (404, 409)        # the candle read's own answers for "no usable own broker session" (and Tier C)


class NoSession(RuntimeError):
    """The user has no usable broker session of his own for market data."""


def broker_symbol(symbol: str) -> tuple[str, str]:
    """(symbol, exchange) the chart's candle read takes for a picked symbol: an index by its spot name ("NIFTY" ->
    "NIFTY 50" on NSE, "SENSEX" on BSE), "BSE:X" -> X on BSE, a share on NSE."""
    from app.instruments import master
    value = " ".join(symbol.strip().upper().split())
    if ":" in value:
        exchange, _sep, name = value.partition(":")
        return name.strip(), exchange.strip() or "NSE"
    under = master.underlying_of(value)
    if under in master.INDEX_SYMBOLS:
        return master.INDEX_SYMBOLS[under], master.INDEX_EXCHANGE.get(under, "NSE")
    return value, "NSE"


def _when_ist(raw: Any) -> pd.Timestamp:
    from app.market_data.calendar import IST
    t = pd.Timestamp(raw)
    return t.tz_localize(IST) if t.tzinfo is None else t.tz_convert(IST)


def broker_rows(bars: list[dict[str, Any]], interval: str, key: str) -> list[tuple[Any, ...]]:
    """The candle read's bars -> the lake's raw row tuples (END, version, open, high, low, close, volume), END-labelled as
    the lake labels a bar: a 1-minute bar at its start + 1 minute, a daily bar at its session's regular close. A bar the
    read marks as still forming is left out."""
    from app.price_action.zones import job
    sess = job.exchange_session(key)
    out: list[tuple[Any, ...]] = []
    for b in bars:
        if not isinstance(b, dict) or b.get("forming"):
            continue
        try:
            start = _when_ist(b["timestamp"])
            values = [float(b[k]) for k in ("open", "high", "low", "close")]
        except (KeyError, TypeError, ValueError):
            continue
        if any(not v > 0 for v in values):
            continue
        if interval == DAILY:
            end = pd.Timestamp(datetime.combine(start.date(), sess.close), tz=start.tz)
        else:
            end = start + pd.Timedelta(minutes=1)
        out.append((end.tz_convert("UTC").to_pydatetime(), 1, *values, float(b.get("volume") or 0)))
    out.sort(key=lambda r: r[0])
    return out


class OwnBrokerBars:
    """The user's own broker candles for one reading (read only, each series at most once)."""

    def __init__(self, session: AsyncSession, user: User, symbol: str, key: str, when: datetime) -> None:
        self.session, self.user, self.key, self.when = session, user, key, when
        self.symbol, self.exchange = broker_symbol(symbol)
        self.no_session = False
        self.broker: str | None = None
        self._rows: dict[str, list[tuple[Any, ...]]] = {}

    async def _read(self, interval: str) -> list[tuple[Any, ...]]:
        if interval in self._rows:
            return self._rows[interval]
        if self.no_session:
            return []
        from app.market_data import candles_routes as cr
        days = cr.INTRADAY_MAX_LOOKBACK_DAYS if interval == INTRADAY else cr.DAILY_MAX_LOOKBACK_DAYS
        rows: list[tuple[Any, ...]] = []
        try:
            body = await cr.candles(cr.CandlesBody(symbols=[self.symbol], exchange=self.exchange, timeframe=interval, lookback_days=days),
                                    user=self.user, session=self.session)
        except HTTPException as exc:
            if exc.status_code in NO_SESSION_STATUS:
                self.no_session = True
                raise NoSession(str(exc.detail)) from None
            logger.info("Own broker candles not read for %s %s: %s", self.key, interval, exc.status_code)
            body = {}
        except Exception as exc:  # noqa: BLE001 - the stored candles still stand
            logger.info("Own broker candles not read for %s %s: %s", self.key, interval, type(exc).__name__)
            body = {}
        entry = ((body.get("symbols") or {}).get(self.symbol.upper()) or {}) if isinstance(body, dict) else {}
        if isinstance(entry, dict) and not entry.get("error"):
            rows = await asyncio.to_thread(broker_rows, list(entry.get("bars") or []), interval, self.key)
            broker = (body.get("source") or {}).get("broker") if isinstance(body.get("source"), dict) else None
            self.broker = self.broker or (str(broker) if broker else None)
        self._rows[interval] = rows
        return rows

    async def frame(self, tf: str, start: datetime) -> pd.DataFrame:
        """Closed `tf` bars (timestamp = start, bar_end) ending in (start, when], the layer's frame shape. Raises NoSession."""
        from app.charts import price_action_concepts as PC
        from app.charts import price_action_layer as pal
        daily = tf in ("1d", "1w")
        rows = await self._read(DAILY if daily else INTRADAY)
        if not rows:
            return pd.DataFrame(columns=pal.FRAME_COLS)
        if daily:
            frame = await asyncio.to_thread(pal._to_frame, rows, "1d", self.key, self.when, False)
            if tf == "1w" and len(frame):
                frame = await asyncio.to_thread(PC.weekly_from_daily, frame)
        else:
            frame = await asyncio.to_thread(pal._to_frame, rows, tf, self.key, self.when, tf != "1m")
        if not len(frame):
            return frame
        keep = pd.to_datetime(frame["bar_end"], utc=True) > pd.Timestamp(start)
        return frame[keep].reset_index(drop=True)

    @property
    def source(self) -> str:
        return f"broker:{self.broker}" if self.broker else "broker"


async def best_frame(stored: pd.DataFrame, own: OwnBrokerBars | None, tf: str, start: datetime, min_bars: int) -> tuple[pd.DataFrame, str]:
    """(frame, "stored" | "broker"): the stored frame when it holds at least `min_bars`, else the user's own broker
    frame of the same window when it holds more bars. Raises NoSession only when the stored frame is short."""
    if len(stored) >= min_bars or own is None:
        return stored, "stored"
    broker = await own.frame(tf, start)
    return (broker, "broker") if len(broker) > len(stored) else (stored, "stored")


# --- B: the areas of this read ---------------------------------------------------------------------------------------------

_BANDS: OrderedDict[tuple[Any, ...], tuple[float, list[dict[str, Any]]]] = OrderedDict()


def bands_cache_clear() -> None:
    _BANDS.clear()


def _zone_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """The layer's frame (timestamp, bar_end, OHLC[V]) -> the zones engine's (FRAME_COLUMNS, `close_at`)."""
    from app.price_action.zones import job
    if frame is None or not len(frame):
        return pd.DataFrame(columns=job.FRAME_COLUMNS)
    out = frame.rename(columns={"bar_end": "close_at"})
    if "volume" not in out:
        out = out.assign(volume=0)
    out = out.assign(timestamp=pd.to_datetime(out["timestamp"], utc=True), close_at=pd.to_datetime(out["close_at"], utc=True),
                     volume=out["volume"].fillna(0).astype("int64"))
    return out[job.FRAME_COLUMNS].reset_index(drop=True)


def compute_bands(raw: dict[str, pd.DataFrame], when: datetime, price: float) -> list[dict[str, Any]]:
    """Pure: the frames of the settings' timeframes -> the bands a chart draws at `price` (the job's own functions, the
    platform's zone settings). Runs in a build slot."""
    from app.price_action.zones import chart, job, snapshot, store
    s = store.platform_settings()
    frames = job.frames_from({tf: _zone_frame(f) for tf, f in raw.items()}, s, when)
    rows = snapshot.build_snapshot(frames.frames, frames.dyn, s, None)
    return chart.bands_from_rows(rows, price)


async def read_time_bands(session: AsyncSession, user: User, key: str, when: datetime, price: float | None, *,
                          private: Any, own: OwnBrokerBars | None, min_bars: int) -> list[dict[str, Any]]:
    """The areas of interest of this read when the zones job holds none: the settings' frames (stored, else the user's
    own broker candles, as A), computed in a build slot; cached by the bars read. [] when nothing can be computed."""
    from app.charts import price_action_layer as pal
    from app.price_action.zones import job, store
    if price is None or not price > 0:
        return []
    s = store.platform_settings()
    raw: dict[str, pd.DataFrame] = {}
    for tf, start in sorted(job.plan_starts(key, s, when).items()):
        try:
            stored, _src = await pal.load_frame(session, key, tf, start, when, private=private)
        except pal.WindowTooLarge:
            stored = pd.DataFrame(columns=pal.FRAME_COLS)
        try:
            frame, _kind = await best_frame(stored, own, tf, start, min_bars)
        except NoSession:
            frame = stored
        if len(frame):
            raw[tf] = frame
    if not raw:
        return []
    ck = (key, store.settings_hash(s), round(float(price), 6),
          tuple((tf, len(f), str(f["bar_end"].iloc[-1]), float(f["close"].iloc[-1])) for tf, f in sorted(raw.items())))
    hit = _BANDS.get(ck)
    if hit is not None and hit[0] >= time.monotonic():
        _BANDS.move_to_end(ck)
        return [dict(b) for b in hit[1]]
    try:
        bands = await pal._in_slot(session, user.id, compute_bands, raw, when, float(price))
    except HTTPException:
        return []                                   # busy: the reading stands without its areas this time
    except Exception as exc:  # noqa: BLE001 - areas are one section; the structure reading still stands
        logger.warning("Read-time areas failed for %s: %s", key, type(exc).__name__)
        return []
    _BANDS[ck] = (time.monotonic() + pal.CACHE_SECONDS, bands)
    while len(_BANDS) > pal.CACHE_SIZE:
        _BANDS.popitem(last=False)
    return [dict(b) for b in bands]


__all__ = ["NoSession", "OwnBrokerBars", "best_frame", "broker_rows", "broker_symbol", "compute_bands", "read_time_bands", "bands_cache_clear"]
