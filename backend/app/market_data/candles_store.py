"""M71 W2-A: the chart candles (`POST /api/market-data/candles`: Charts, Multi-chart, Terminal, the dashboard's index
sparks) read the own store first - flag `market_store_reads` (OFF = `candles_routes.broker_candles`, exactly as before).

Read order per symbol (`app.market_store`, docs/design/M71_READ_PATHS.md):
- Day / week / month on NSE: the NSE daily files (`reader.read_eod`), zero broker calls; week / month aggregated from
  them as before (`periods.aggregate`, IST). Today's bar (`forming`, the v2 chart) from the reader's own stored
  one-minute bars, else from his own session's copy of today's minutes (one intraday call, never a history call).
- Intraday: the reader's OWN stored one-minute bars built into the chart's timeframe (closed bars, START labels), then
  only the newest missing bars by REST through his own session, paced by the credential's API budget and kept until
  the timeframe's next bar closes - a chart re-polled every minute reads no history. The bar still forming is built
  from his own stored minutes after the last closed bar (the live feed's), else the chart's live price forms it.
- M71Q-2: intraday bars only through the reader's own credential (or, for the organisation's OWNER, its own). A user
  without his own broker gets Daily / Weekly and the plain "Connect your broker for intraday bars." line.
- A symbol the store holds nothing of reads through the broker path exactly as before (`broker_candles`, the same
  request for those symbols) when the reader may read it there; else its plain reason.
- An index's volume from its near-month future (`volume_from_future`): the future's stored bars; a bar the future has
  no bar for has NO volume (None, never 0). Future not stored: the index keeps its own volume and a line says so.
Gaps are reported per symbol in plain words (`gaps`), never filled with zeros or copied bars. No look-ahead: only
closed bars from the store; the forming bar is the newest bucket only.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from datetime import time as dtime
from typing import Any

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.brokers.token_lifecycle import build_adapter
from app.core import config
from app.core.models import OHLCVBar
from app.db.models import User
from app.market_data import api_budget, index_volume, periods
from app.market_data.calendar import EXCHANGE_SESSIONS, IST, previous_session_close, session_family
from app.market_store import reader as ms
from app.market_store.models import CONNECT_FOR_INTRADAY, FLAG, NEWEST, NOT_STORED, BarsResult, RestSource, canonical
from app.platform.controls import flag_enabled

logger = logging.getLogger(__name__)

MINUTE = timedelta(minutes=1)
NOTE = "Candles come from ATP's stored market data; only the newest bars are read through your own broker session."


async def store_on(session: AsyncSession, user: User) -> bool:
    """The flag `market_store_reads` for the reader's organisation."""
    return await flag_enabled(session, FLAG, user.tenant_id)


@dataclass
class Own:
    """The reader's broker side for one request: the credential the broker path would read through (None: no usable
    session), whether it is his own (M71Q-2), the REST source of the newest bars and, with bring-your-own-broker on,
    the symbols his session covers (None: all) and an entitlement for the freshness badge."""
    record: Any = None
    mine: bool = False
    rest: RestSource | None = None
    allowed: set[tuple[str, str]] | None = None
    live: Any = None

    def tops_up(self, symbol: str, exchange: str) -> RestSource | None:
        if self.rest is None or (self.allowed is not None and (symbol, exchange) not in self.allowed):
            return None
        return self.rest

    def broker_path(self, symbol: str, exchange: str, intraday: bool) -> bool:
        """Whether a symbol the store holds nothing of may read through the broker path as before: intraday only through
        the reader's own credential (M71Q-2); daily through the session the broker path picks (today's behaviour)."""
        if self.record is None:
            return False
        if self.allowed is not None:
            return (symbol, exchange) in self.allowed
        return self.mine or not intraday


async def own_source(session: AsyncSession, user: User, pairs: list[tuple[str, str]], body: Any, priority: Any) -> Own:
    from app.market_data import live_access
    from app.market_data.candles_routes import _pick_record
    from app.market_store import sources
    out = Own()
    try:
        if await live_access.byob_on(session, user.tenant_id):
            src = await live_access.user_source(session, user, pairs, broker=body.broker,
                                                account_label=body.account_label if body.broker else None, priority=priority)
            out.record, out.mine = src.record, True
            out.allowed = {p for p in pairs if src.allows(*p)}
            out.live = next((e for e in src.entitlements.values() if e.live), None)
            out.rest = sources.from_record(user, src.record, src.adapter())
        else:
            record = await _pick_record(session, user.tenant_id, body.broker, body.account_label)
            out.record, out.mine = record, ms.may_top_up(user, record)
            out.rest = sources.from_record(user, record, build_adapter(record)) if out.mine else None
    except (HTTPException, live_access.LiveDataRefused):
        pass                                              # no usable session: the store's bars only
    return out


# --- windows ---------------------------------------------------------------------------------------------------------------

def _ist(day: date, at: dtime) -> datetime:
    return datetime.combine(day, at, tzinfo=IST).astimezone(UTC)


def intraday_window(now: datetime, days: int, before: date | None, holidays: set[date], exchange: str) -> tuple[datetime, datetime]:
    """The broker path's window (`service.history_window` / `get_history`): `days` calendar days back, never starting
    after the last session's day; with `before`, the days ending the day before it (IST)."""
    today = now.astimezone(IST).date()
    if before is not None:
        b = min(before, today)
        return _ist(b - timedelta(days=days), dtime(0, 0)), _ist(b - timedelta(days=1), dtime(23, 59, 59))
    close = previous_session_close(now, holidays, exchange)
    start = now - timedelta(days=days)
    if close is not None:
        start = min(start, _ist(close.astimezone(IST).date(), dtime(0, 0)))
    return start, now


def daily_window(now: datetime, lookback: int, before: date | None, period: str | None, window: tuple[date, date] | None) -> tuple[date, date]:
    """The broker path's daily dates: week / month its whole periods; day `lookback` days (400 at least without `before`,
    `service._lookback`) ending the day before today (today's bar is added on top), or before `before`."""
    from app.market_data.service import DAILY_MIN_LOOKBACK_DAYS
    if period is not None and window is not None:
        return window
    today = now.astimezone(IST).date()
    if before is not None:
        b = min(before, today)
        return b - timedelta(days=lookback), b - timedelta(days=1)
    return today - timedelta(days=max(lookback, DAILY_MIN_LOOKBACK_DAYS)), today - timedelta(days=1)


# --- bars --------------------------------------------------------------------------------------------------------------------

def _nan(v: Any) -> bool:
    return v is None or (isinstance(v, float) and math.isnan(v))


def frame_bars(result: BarsResult) -> list[OHLCVBar]:
    """The result's closed bars, stamped in India time (the broker's own form: 2026-10-30T09:15:00+05:30)."""
    return [b.model_copy(update={"timestamp": b.timestamp.astimezone(IST)}) for b in result.bars()]


def forming_bar(minutes: list[OHLCVBar], interval: str, now: datetime, holidays: set[date], exchange: str,
                after: datetime | None) -> OHLCVBar | None:
    """The bar still forming at `now`, from the reader's own stored one-minute bars (START-stamped) after the newest
    closed bar - the newest bucket of the session grid only, None without such minutes (the live price forms it)."""
    n = ms.INTRADAY_MINUTES[canonical(interval)]
    if n == 1 or not minutes:
        return None
    cut = ms.last_closed_end(now, n, holidays, exchange)
    left = [b for b in minutes if b.timestamp >= cut and b.timestamp + MINUTE <= now]
    if not left:
        return None
    spec = EXCHANGE_SESSIONS[session_family(exchange)]
    last = left[-1].timestamp.astimezone(IST)
    open_ = datetime.combine(last.date(), spec.open, tzinfo=IST)
    if not open_ <= last < datetime.combine(last.date(), spec.close, tzinfo=IST):
        return None
    period = timedelta(minutes=n)
    start = open_ + ((last - open_) // period) * period
    part = [b for b in left if start <= b.timestamp < start + period]
    if not part or (after is not None and start <= after):
        return None
    vols = [float(b.volume) for b in part if not _nan(b.volume)]
    return OHLCVBar(timestamp=start, open=part[0].open, high=max(b.high for b in part), low=min(b.low for b in part),
                    close=part[-1].close, volume=sum(vols) if vols else float("nan"))


def future_volume(bars: list[OHLCVBar], fut_bars: list[OHLCVBar]) -> list[OHLCVBar]:
    """Each index bar with the volume of the future's bar at the same instant; NONE where the future has no bar (never
    0 - a missing bar is missing)."""
    vols = {b.timestamp.astimezone(UTC): b.volume for b in fut_bars}
    return [b.model_copy(update={"volume": vols.get(b.timestamp.astimezone(UTC), float("nan"))}) for b in bars]


def to_json(bars: list[OHLCVBar]) -> list[dict[str, Any]]:
    """The broker path's JSON form of a bar; a missing volume is null, never 0."""
    return [{"timestamp": b.timestamp.isoformat(), "open": float(b.open), "high": float(b.high), "low": float(b.low),
             "close": float(b.close), "volume": None if _nan(b.volume) else float(b.volume)} for b in bars]


def _trim_cut_period(bars: list[OHLCVBar], first: date, period: str) -> list[OHLCVBar]:
    """Week / month whose stored days start inside a period: whole periods only (`candles_routes._trim_periods`)."""
    if periods.period_start(first, period) != first:
        first = periods.next_period_start(first, period)
    return [b for b in bars if periods.ist_date(b.timestamp) >= first]


def _entry(bars: list[OHLCVBar], gaps: list[str], volume_source: dict | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {"bars": to_json(bars), "count": len(bars), "first": bars[0].timestamp.isoformat() if bars else None,
                           "last": bars[-1].timestamp.isoformat() if bars else None, "error": None, "volume_source": volume_source,
                           "gaps": gaps}
    if not bars:
        out["empty_reason"] = gaps[0] if gaps else NOT_STORED
    return out


# --- the route -------------------------------------------------------------------------------------------------------------

async def serve(body: Any, user: User, session: AsyncSession) -> dict:
    """`POST /api/market-data/candles` store-first (module docstring); the response has the broker path's shape, plus
    `gaps` per symbol and `broker_calls` (the broker calls this request made for the stored symbols)."""
    from app.billing.service import meter
    from app.db.session import release_connection
    from app.market_data import candles_routes as cr
    from app.market_data.fanin import Priority
    from app.market_data.service import MarketDataService, merge_bars
    period = cr.check_body(body)
    exchange = body.exchange.upper()
    tf = body.timeframe
    daily = tf in cr.DAILY_TIMEFRAMES
    wanted = list(dict.fromkeys(s.strip().upper() for s in body.symbols if s.strip()))
    warnings: list[str] = []
    if period is not None:
        lookback = body.lookback_days if "lookback_days" in body.model_fields_set else cr.PERIOD_DEFAULT_DAYS[period]
        cap = cr.PERIOD_MAX_DAYS[period]
    else:
        lookback = body.lookback_days
        cap = cr.DAILY_MAX_LOOKBACK_DAYS if daily else cr.INTRADAY_MAX_LOOKBACK_DAYS
    days = min(lookback, cap)
    if lookback > cap:
        if not daily and period is None and tf in cr.LAKE_EXTEND_TIMEFRAMES and cr._index_keys(wanted):
            days = min(lookback, config.LAKE_BACKFILL_INTRADAY_DAYS)          # M55 T4: stored history past the broker's month
        else:
            warnings.append(f"lookback clamped to {cap} days for {tf} bars")
    now = datetime.now(UTC)
    today = now.astimezone(IST).date()
    holidays = await ms.holidays_for(session, exchange)
    today_state = await cr._today_state(session, body, exchange, now)
    futures: dict[str, index_volume.NearFuture] = {}
    if body.volume_from_future:
        for sym in wanted:
            if index_volume.index_underlying(sym, exchange) is not None:
                fut = await index_volume.near_future(session, sym, exchange, today)
                if fut is not None:
                    futures[sym] = fut
    pairs = [(s, exchange) for s in wanted] + [(f.symbol, f.exchange) for f in futures.values()]
    own = await own_source(session, user, pairs, body, Priority.CHART if len(wanted) == 1 else Priority.SCREENER)

    # Phase 1: the store (database reads only).
    eod: dict[str, BarsResult] = {}
    today_minutes: dict[str, list[OHLCVBar]] = {}
    stored: dict[str, Any] = {}
    fut_stored: dict[str, Any] = {}
    window = cr._period_window(period, body.before, days, today) if period is not None else None
    if daily:
        first, last = daily_window(now, days, body.before, period, window)
        for sym in wanted:
            eod[sym] = await ms.read_eod(session, sym, exchange, "1d", _ist(first, dtime(0, 0)), _ist(last, dtime(12, 0)),
                                         now=now, holidays=holidays)
            if today_state is not None and eod[sym].served:
                spec = EXCHANGE_SESSIONS[session_family(exchange)]
                mins = await ms.store_intraday(session, sym, exchange, "1m", _ist(today, spec.open), now, user=user, now=now)
                today_minutes[sym] = mins.minutes
    else:
        start, end = intraday_window(now, days, body.before, holidays, exchange)
        for sym in wanted:
            stored[sym] = await ms.store_intraday(session, sym, exchange, tf, start, end, user=user, now=now)
            fut = futures.get(sym)
            if fut is not None and stored[sym].minutes:
                fut_stored[sym] = await ms.store_intraday(session, fut.symbol, fut.exchange, tf, start, end, user=user, now=now)
    await release_connection(session)

    # Phase 2: only the newest bars by REST (paced, the reader's own session), then the bars.
    budgeted = ms.paced_adapter(own.rest) if own.rest is not None else None
    calls = 0
    out: dict[str, dict[str, Any]] = {}
    legacy: list[str] = []
    served_with_bars = 0
    newest: list[datetime] = []
    for sym in wanted:
        gaps: list[str] = []
        volume_source: dict | None = None
        if daily:
            got = eod[sym]
            if not got.served:
                if own.broker_path(sym, exchange, intraday=False):
                    legacy.append(sym)
                else:
                    out[sym] = _entry([], got.gap_lines())
                continue
            gaps = got.gap_lines()
            bars = frame_bars(got)
            added = False
            if today_state is not None:
                bar = periods.today_daily_bar(today_minutes.get(sym, []), today)
                rest = own.tops_up(sym, exchange)
                if bar is None and rest is not None and budgeted is not None:
                    before_calls = int(getattr(budgeted, "taken", 0))
                    try:
                        bar = await MarketDataService(budgeted, lookback_days=days).get_today_bar(sym, exchange, now)
                    except api_budget.BudgetUsed:
                        gaps.append(ms.NEWEST_BUDGET)
                    except Exception as exc:  # noqa: BLE001 - the completed bars still make a chart
                        logger.info("today's bar not read for %s:%s: %s", exchange, sym, exc)
                        gaps.append("Today's bar could not be read from your broker.")
                    calls += int(getattr(budgeted, "taken", 0)) - before_calls
                if bar is not None and (not bars or periods.ist_date(bars[-1].timestamp) < periods.ist_date(bar.timestamp)):
                    bars = merge_bars(bars, [bar])
                    added = True
            newest_base = bars[-1].timestamp if bars else None
            if period is not None and any(g.kind == ms.OLDER for g in got.gaps) and bars:
                bars = _trim_cut_period(bars, periods.ist_date(bars[0].timestamp), period)
            shaped = cr._shape(bars, tf)
            entry = _entry(shaped, gaps)
            if added and entry["bars"]:
                entry["bars"][-1]["forming"] = bool(today_state)
                entry["bars"][-1]["provisional"] = True
        else:
            st = stored[sym]
            if not st.minutes:
                if own.broker_path(sym, exchange, intraday=True):
                    legacy.append(sym)
                else:
                    out[sym] = _entry([], [CONNECT_FOR_INTRADAY if own.tops_up(sym, exchange) is None else NOT_STORED])
                continue
            rest = own.tops_up(sym, exchange) if body.before is None else None       # an older page is final: no top-up
            got = await ms.top_up(st, rest=rest, budgeted=budgeted if rest is not None else None, now=now, holidays=holidays)
            calls += got.rest_calls
            gaps = [g.reason for g in got.gaps if g.reason and not (body.before is not None and g.kind == NEWEST)]
            gaps = list(dict.fromkeys(gaps))
            bars = frame_bars(got)
            if body.before is None:
                bar = forming_bar(st.minutes, tf, now, holidays, exchange, bars[-1].timestamp if bars else None)
                if bar is not None:
                    bars.append(bar.model_copy(update={"timestamp": bar.timestamp.astimezone(IST)}))
            fut = futures.get(sym)
            fst = fut_stored.get(sym)
            if fut is not None:
                fbars: list[OHLCVBar] = []
                if fst is not None and fst.minutes:
                    frest = own.tops_up(fut.symbol, fut.exchange) if body.before is None else None
                    fgot = await ms.top_up(fst, rest=frest, budgeted=budgeted if frest is not None else None, now=now, holidays=holidays)
                    calls += fgot.rest_calls
                    fbars = frame_bars(fgot)
                    if body.before is None:
                        fbar = forming_bar(fst.minutes, tf, now, holidays, fut.exchange, fbars[-1].timestamp if fbars else None)
                        if fbar is not None:
                            fbars.append(fbar)
                if fbars:
                    bars = future_volume(bars, fbars)
                    volume_source = fut.source(periods.ist_date(fbars[0].timestamp))
                else:
                    warnings.append(f"{sym}: volume from {fut.symbol} is not stored yet")
            newest_base = bars[-1].timestamp if bars else None
            entry = _entry(bars, gaps, volume_source)
        out[sym] = entry
        if entry["count"]:
            served_with_bars += 1
            if newest_base is not None:
                newest.append(newest_base.astimezone(UTC))

    result: dict[str, Any] = {
        "source": {"broker": own.record.broker_name if own.record is not None else "",
                   "account_label": own.record.account_label if own.record is not None else ""},
        "exchange": exchange, "timeframe": tf, "base_interval": "day" if daily else "1min",
        "lookback_days": days,
        "before": body.before.isoformat() if body.before else None, "fetched_at": datetime.now(UTC).isoformat(),
        "symbols": {}, "warnings": warnings, "note": NOTE, "broker_calls": calls}
    if served_with_bars:
        await meter(session, user.tenant_id, "market_data_candles", quantity=served_with_bars, source="api",
                    metadata={"broker": result["source"]["broker"], "timeframe": tf, "exchange": exchange, "read": "store"}, commit=True)
    if own.live is not None and newest:
        badge_tf = "day" if period is not None else tf
        result["data_tier"] = own.live.badge(now=now, timeframe=badge_tf, last_bar_start=min(newest))
    if legacy:
        try:
            more = await cr.broker_candles(body.model_copy(update={"symbols": legacy}), user, session)
        except HTTPException as exc:
            reason = exc.detail if isinstance(exc.detail, str) else NOT_STORED
            more = {"symbols": {s: _entry([], [NOT_STORED if daily else reason]) for s in legacy}, "warnings": []}
        out.update(more.get("symbols", {}))
        result["warnings"].extend(more.get("warnings", []))
        if "data_tier" in more and "data_tier" not in result:
            result["data_tier"] = more["data_tier"]
        if not result["source"]["broker"] and more.get("source"):
            result["source"] = more["source"]
    result["symbols"] = {s: out[s] for s in wanted if s in out}
    if len(wanted) == 1 and served_with_bars:
        from app.growth import activation  # AUD-G2.4: one symbol with bars = a chart
        await activation.record_step(session, user, "first_chart")
    return result


__all__ = ["store_on", "serve", "Own", "own_source", "intraday_window", "daily_window", "forming_bar", "future_volume", "to_json",
           "frame_bars"]
