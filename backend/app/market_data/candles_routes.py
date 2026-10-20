"""Phase AA: broker candles for the research pages.

The autonomous worker has fetched real candles through each tenant's own broker session since
Phase A2 (`MarketDataService`: history + today's intraday, Redis-cached, resampled from 1-minute
bars anchored to the 09:15 open). The Signals, Scanner, Backtest and Factor Lab pages, however,
still scored candles generated in the browser. This router gives them the same candles:

* `GET  /api/market-data/sources` - the tenant's broker sessions and whether each can serve data
* `POST /api/market-data/candles` - up to 50 symbols at one timeframe through one broker session
* `GET  /api/market-data/ltp` - one symbol's last price for the chart's forming candle (Phase AN):
  the streaming tick when one is fresh, else the broker's quote with the exchange timestamp and a
  `stale` flag, else the bare LTP. Never raises on staleness - a chart shows the price with its age.

The data goes through the tenant's stored credentials (Fernet/envelope-encrypted, never returned),
so a tenant without a logged-in broker gets a 409 that says what to do, not an empty chart. Per-
symbol failures are reported next to the symbols that worked. Fetches are metered per symbol as
`market_data_candles` so plans can price them later; nothing is persisted here.

BT1 (flag `byob_data_tier`, off by default; off = everything above, unchanged): every read here goes through the
entitlement guard (`app/market_data/entitlement.py`). Live data comes only through the user's *own* broker session at a
broker whose data terms are confirmed, for symbols inside that session's budget; the session's own ticks and its
per-request candle cache are kept under that session's namespace only; each response carries a `data_tier` badge (tier,
label, timestamp, staleness).

LD-10 (owner, 20 Oct): candles and prices are public market data. Once the guard has admitted a symbol, its bars come from
the copy shared by every user of the same broker (`shared_candles`: one broker fetch per window, through the requesting
user's own session and budget), and `/ltp` reads the shared live feed's tick for the same broker before it spends a
broker call. Nothing per account is shared, and a session's own ticks never feed the shared feed. Anything else is Tier C (EOD): a 409 with the reason and an `X-Data-Tier: C` header.
`GET /api/market-data/data-tier` is the badge API for a symbol or a market-wide page.

UI2-4a2 (additive): `week` and `month` timeframes, aggregated in IST from daily bars fetched in whole-date chunks per the
broker's request cap (`periods`, `MarketDataService.get_daily_range`); `before` pages them by whole periods. `forming:
true` (the v2 chart) adds today's daily bar from today's one-minute bars to a D/W/M request. An index's volume comes from
its near-month future (`index_volume`, `volume_from_future`), fetched through the same session, budget and entitlement.
"""
import asyncio
import logging
from datetime import date, datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple, cast

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.core.rate_limit import user_rate_limit
from app.billing.service import meter
from app.brokers.base import BrokerInterface
from app.brokers.token_lifecycle import build_adapter, data_is_usable, token_is_usable
from app.core.models import OHLCVBar
from app.db.models import BrokerCredentialRecord, User
from app.db.session import get_session, release_connection
from app.market_data import api_budget, entitlement, index_volume, periods
from app.market_data.calendar import EXCHANGE_SESSIONS, IST, family_holidays, load_holidays, session_family, session_status
from app.market_data.data_terms import terms_for
from app.market_data.entitlement import DataTier, Entitlement
from app.market_data.fanin import FLAG as BYOB_FLAG, Priority
from app.market_data.freshness import quote_is_stale
from app.market_data.service import MarketDataService, build_frames, merge_bars
from app.market_data.stream import TickCache, tick_cache
from app.platform.controls import flag_enabled

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/market-data", tags=["market-data"])

MAX_SYMBOLS = 50
INTRADAY_TIMEFRAMES = ("1min", "3min", "5min", "15min", "30min", "60min")
# UI2-4a2: week and month are aggregated in IST from daily bars (app/market_data/periods.py), never the broker's own.
SUPPORTED_TIMEFRAMES = INTRADAY_TIMEFRAMES + ("day",) + periods.PERIODS
DAILY_TIMEFRAMES = ("day",) + periods.PERIODS
INTRADAY_MAX_LOOKBACK_DAYS = 30      # brokers serve one-minute history about a month per request
DAILY_MAX_LOOKBACK_DAYS = 730
WEEKLY_MAX_LOOKBACK_DAYS = periods.WEEKLY_MAX      # weekly and monthly only: their daily bars come in chunks
MONTHLY_MAX_LOOKBACK_DAYS = periods.MONTHLY_MAX
PERIOD_DEFAULT_DAYS = {"week": periods.WEEKLY_DEFAULT_DAYS, "month": periods.MONTHLY_DEFAULT_DAYS}
PERIOD_MAX_DAYS = {"week": WEEKLY_MAX_LOOKBACK_DAYS, "month": MONTHLY_MAX_LOOKBACK_DAYS}


class CandlesBody(BaseModel):
    symbols: List[str] = Field(min_length=1, max_length=MAX_SYMBOLS)
    exchange: str = Field(default="NSE", min_length=2, max_length=10)
    timeframe: str = Field(default="5min")
    # Above DAILY_MAX_LOOKBACK_DAYS for week and month only (the route refuses it for the others, as before). Omitted
    # for week / month = five / twenty years.
    lookback_days: int = Field(default=5, ge=1, le=MONTHLY_MAX_LOOKBACK_DAYS)
    broker: Optional[str] = Field(default=None, description="a stored broker; omitted = the first usable session")
    account_label: str = Field(default="primary", max_length=50)
    # Older history for a chart scrolled to its left edge: the `lookback_days` of completed bars
    # ending the day before this IST date (no today's bars, no shared 60 s cache - history is final). For week and
    # month: the complete weeks / months before the one holding this date.
    before: Optional[date] = None
    # UI2-4a2 (sent by the v2 chart): append today's daily bar, built from today's one-minute bars, to a day / week /
    # month request without `before` - marked `"forming": true` while the session is open. Off by default, so the pages
    # that score daily bars (Signals, Scanner) keep getting completed bars only.
    forming: bool = False
    # UI2-4a2: an index's volume from its near-month future (`volume_source` in each symbol's answer says which).
    volume_from_future: bool = True


async def _records(session: AsyncSession, tenant_id: int) -> List[BrokerCredentialRecord]:
    return list(await session.scalars(select(BrokerCredentialRecord).where(BrokerCredentialRecord.tenant_id == tenant_id)
                                      .order_by(BrokerCredentialRecord.broker_name, BrokerCredentialRecord.account_label)))


def _source(record: BrokerCredentialRecord, byob: bool = False) -> dict:
    out = {"broker": record.broker_name, "account_label": record.account_label, "token_status": record.token_status,
           "token_expires_at": record.token_expires_at.isoformat() if record.token_expires_at else None, "usable": data_is_usable(record)}
    if byob:
        # BT1: whether this broker's data may be shown in ATP at all (docs/BROKER_DATA_TERMS.md).
        terms = terms_for(record.broker_name)
        # BT1b: "usable" means usable *for data in ATP*: a valid token at a broker whose terms allow display.
        out.update({"data_status": terms.status.value, "tier_a": terms.tier_a_allowed and out["usable"], "token_usable": out["usable"]})
        out["usable"] = out["tier_a"]
    return out


async def _byob(session: AsyncSession, user: User) -> bool:
    """BT1: the bring-your-own-broker data tier is on for this user's organisation (flag off = today's behaviour)."""
    return await flag_enabled(session, BYOB_FLAG, user.tenant_id)


async def _eod_context(session: AsyncSession, ents: Dict[tuple, Entitlement]) -> tuple:
    """(newest EOD file date, holidays) for the per-symbol EOD badges - read only when some symbol is EOD."""
    if all(e.live for e in ents.values()):
        return None, set()
    return await entitlement.eod_as_of(session), await load_holidays(session)


def _tier_c(ents: Dict[tuple, Entitlement]) -> HTTPException:
    """409 for a request none of whose symbols may be shown live; the first symbol's reason says why."""
    ent = next(iter(ents.values()), None)
    reason = ent.reason if ent is not None and ent.reason else "EOD data only for this symbol."
    return HTTPException(status_code=409, detail=reason, headers={"X-Data-Tier": DataTier.EOD.value})


async def _pick_record(session: AsyncSession, tenant_id: int, broker: Optional[str], account_label: str) -> BrokerCredentialRecord:
    """The named broker session, or the first usable one; 404/409 with the fix location otherwise."""
    records = await _records(session, tenant_id)
    if broker:
        wanted = [r for r in records if r.broker_name == broker.lower() and r.account_label == (account_label or "primary")]
        if not wanted:
            raise HTTPException(status_code=404, detail=f"No stored credentials for broker '{broker}' ({account_label})")
        if not data_is_usable(wanted[0]):
            raise HTTPException(status_code=409, detail=f"Broker '{broker}' has no valid session token today. Log in to it under Settings > Brokers.")
        return wanted[0]
    # A logged-in session first; else an Upstox row whose analytics token serves market data without the daily login.
    usable = [r for r in records if token_is_usable(r)] or [r for r in records if data_is_usable(r)]
    if not usable:
        raise HTTPException(status_code=409, detail="No broker session with a valid token. Add the broker's API key under Settings > Brokers "
                                                    "(it is stored encrypted, never in the environment) and log in to it, then try again.")
    return usable[0]


def _shape(bars: List[OHLCVBar], timeframe: str) -> List[OHLCVBar]:
    if bars and timeframe in periods.PERIODS:
        return periods.aggregate(bars, timeframe)          # UI2-4a2: daily bars -> weeks / months, in IST
    if bars and timeframe not in ("1min", "day"):
        return _frame_to_bars(build_frames(bars, "1min", [timeframe])[timeframe])
    return bars


def _shaped(bars: List[OHLCVBar], timeframe: str, future_bars: Optional[List[OHLCVBar]] = None) -> tuple:
    """(bars at `timeframe`, their JSON form). Pandas resampling and serialising a few thousand bars: run on a worker
    thread (LD-7), so a chart does not hold the event loop that every other request of the process waits on. With
    `future_bars` (UI2-4a2, an index's near-month future at the same base interval), each bar takes the volume of the
    future's bar at the same timestamp, shaped the same way."""
    bars = _shape(bars, timeframe)
    if future_bars is not None:
        bars = index_volume.replace_volume(bars, _shape(future_bars, timeframe))
    return bars, [b.model_dump(mode="json") for b in bars]


def _frame_to_bars(df) -> List[OHLCVBar]:
    df = df.dropna(subset=["open", "close"])
    # LD-7: plain column iteration instead of iterrows (a Series per row); the same bars.
    volumes = df["volume"] if "volume" in df else [None] * len(df)
    return [OHLCVBar(timestamp=ts.to_pydatetime(), open=float(o), high=float(h), low=float(lo), close=float(c),
                     volume=float(v) if v is not None and v == v else 0.0)
            for ts, o, h, lo, c, v in zip(df.index, df["open"], df["high"], df["low"], df["close"], volumes)]


@router.get("/sources")
async def sources(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict:
    records = await _records(session, user.tenant_id)
    byob = await _byob(session, user)
    if byob:
        records = [r for r in records if r.user_id == user.id]   # BT1: a teammate's broker session is not this user's data source
    items = [_source(r, byob) for r in records]
    return {"sources": items, "usable": any(i["usable"] for i in items), "timeframes": list(SUPPORTED_TIMEFRAMES),
            "max_symbols": MAX_SYMBOLS, "intraday_max_lookback_days": INTRADAY_MAX_LOOKBACK_DAYS, "daily_max_lookback_days": DAILY_MAX_LOOKBACK_DAYS,
            "weekly_max_lookback_days": WEEKLY_MAX_LOOKBACK_DAYS, "monthly_max_lookback_days": MONTHLY_MAX_LOOKBACK_DAYS}


# Z10-c: every call reads through a broker session and spends that session's rate budget (shared by the organisation
# unless the bring-your-own-broker tier is on), so a member - a VIEWER included - is limited per user.
candles_rate_limit = user_rate_limit("market_data_candles", limit=120, window_seconds=60)
chains_rate_limit = user_rate_limit("market_data_chains", limit=30, window_seconds=60)
# CH5-a review (ADR-0004): a chart polls its last price (every 5 s while it is the active chart, every 20 s otherwise in
# the multi-chart workspace, one poll per symbol shared by the charts showing it); without a fresh tick each call reads
# the broker's quote API. Per user, and every broker call also draws from the session's API budget (api_budget).
ltp_rate_limit = user_rate_limit("market_data_ltp", limit=60, window_seconds=60)


def _period_window(period: str, before: Optional[date], lookback: int, today: date) -> Tuple[date, date]:
    """UI2-4a2: the daily dates a week / month request reads - whole periods only. Without `before`: through yesterday
    (today's bar is added on top when asked for). With it: up to the end of the period before the one holding it."""
    if before is not None:
        end = periods.period_start(min(before, today), period) - timedelta(days=1)
    else:
        end = today - timedelta(days=1)
    return periods.period_start(end - timedelta(days=max(1, lookback) - 1), period), end


async def _today_state(session: AsyncSession, body: CandlesBody, exchange: str, now: datetime) -> Optional[bool]:
    """UI2-4a2: whether to add today's daily bar - None: no (not asked, older history, not a trading day yet, a 24x7
    venue); True: the session is open, the bar is forming; False: the session closed for the day, the bar is complete
    (the broker's daily history has it only from tomorrow)."""
    if not body.forming or body.before is not None or body.timeframe not in DAILY_TIMEFRAMES:
        return None
    family = session_family(exchange)
    spec = EXCHANGE_SESSIONS[family]
    if spec.always_open:
        return None
    holidays = await family_holidays(session, family)
    if session_status(now, holidays, family).is_open:
        return True
    now_ist = now.astimezone(IST)
    if now_ist.weekday() < 5 and now_ist.date() not in holidays and now_ist.time() >= spec.close:
        return False
    return None


class _Got:
    """One symbol's base bars (daily for day / week / month, else one-minute) and what to say about them."""

    def __init__(self, bars: List[OHLCVBar], today_added: bool = False, notes: Optional[List[str]] = None) -> None:
        self.bars = bars
        self.today_added = today_added
        self.notes = notes or []


async def _base_bars(service: MarketDataService, symbol: str, exchange: str, base_interval: str, period: Optional[str],
                     window: Optional[Tuple[date, date]], before: Optional[date], today_state: Optional[bool], now: datetime) -> _Got:
    notes: List[str] = []
    if period is not None and window is not None:
        start, end = window
        got = await service.get_daily_range(symbol, exchange, start, end, now)
        bars = got.bars
        if got.stopped is not None and got.covered_from > start:
            # The older chunks did not load this time: whole periods only, so the oldest bar shown is never a cut one.
            first = got.covered_from
            if periods.period_start(first, period) != first:
                first = periods.next_period_start(first, period)
            bars = [b for b in bars if periods.ist_date(b.timestamp) >= first]
            notes.append(f"older {period}ly bars not loaded this time ({got.stopped}); they load on the next request")
    elif before is not None:
        bars = await service.get_history(symbol, exchange, base_interval, before)
    else:
        bars = await service.get_candles(symbol, exchange, base_interval)
    added = False
    if today_state is not None and base_interval == "day":
        try:
            today_bar = await service.get_today_bar(symbol, exchange, now)
        except api_budget.BudgetUsed:
            today_bar = None
            notes.append("today's bar not loaded this time (budget used)")
        except Exception as exc:  # noqa: BLE001 - the completed bars still make a chart
            logger.info("Today's bar failed for %s:%s: %s", exchange, symbol, exc)
            today_bar = None
            notes.append(f"today's bar not loaded: {type(exc).__name__}: {exc}"[:200])
        if today_bar is not None and (not bars or periods.ist_date(bars[-1].timestamp) < periods.ist_date(today_bar.timestamp)):
            bars = merge_bars(bars, [today_bar])
            added = True
    return _Got(list(bars), added, notes)


async def _future_bars(service: MarketDataService, fut: index_volume.NearFuture, base_interval: str, period: Optional[str],
                       window: Optional[Tuple[date, date]], before: Optional[date], today_state: Optional[bool], now: datetime) -> List[OHLCVBar]:
    """UI2-4a2: the near-month future's bars for the same window, through the same service (session, budget). Nothing
    is fetched for a window that ends before the contract can have been listed."""
    listed = fut.expiry - timedelta(days=index_volume.LISTED_DAYS_BEFORE_EXPIRY)
    if period is not None and window is not None:
        window = (max(window[0], listed), window[1])         # a window ending before the listing: no call (get_daily_range)
    elif before is not None and before <= listed:
        return []
    return (await _base_bars(service, fut.symbol, fut.exchange, base_interval, period, window, before, today_state, now)).bars


@router.post("/candles", dependencies=[Depends(candles_rate_limit)])
async def candles(body: CandlesBody, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict:
    if body.timeframe not in SUPPORTED_TIMEFRAMES:
        raise HTTPException(status_code=400, detail=f"timeframe must be one of {', '.join(SUPPORTED_TIMEFRAMES)}")
    period = body.timeframe if body.timeframe in periods.PERIODS else None
    if period is None and body.lookback_days > DAILY_MAX_LOOKBACK_DAYS:
        raise HTTPException(status_code=422, detail=f"lookback_days must be at most {DAILY_MAX_LOOKBACK_DAYS} for {body.timeframe} bars "
                                                    f"(longer only for week and month)")
    exchange = body.exchange.upper()
    byob = await _byob(session, user)
    ents: Dict[tuple, Entitlement] = {}
    namespace: Optional[str] = None
    live: Optional[Entitlement] = None
    wanted = list(dict.fromkeys(s.strip().upper() for s in body.symbols if s.strip()))
    priority = Priority.CHART if len(wanted) == 1 else Priority.SCREENER
    if byob:
        ents = await entitlement.resolve_many(session, user, [(s, exchange) for s in wanted], broker=body.broker,
                                              account_label=body.account_label if body.broker else None, priority=priority)
        live = next((e for e in ents.values() if e.live), None)
        if live is None or live.record is None or live.session is None:
            raise _tier_c(ents)
        record, namespace = live.record, live.session.namespace
    else:
        record = await _pick_record(session, user.tenant_id, body.broker, body.account_label)

    warnings: List[str] = []
    daily = body.timeframe in DAILY_TIMEFRAMES
    if period is not None:
        lookback = body.lookback_days if "lookback_days" in body.model_fields_set else PERIOD_DEFAULT_DAYS[period]
        cap = PERIOD_MAX_DAYS[period]
    else:
        lookback = body.lookback_days
        cap = DAILY_MAX_LOOKBACK_DAYS if daily else INTRADAY_MAX_LOOKBACK_DAYS
    if lookback > cap:
        warnings.append(f"lookback clamped to {cap} days for {body.timeframe} bars")
        lookback = cap
    base_interval = "day" if daily else "1min"
    # Every broker call of this request takes a token from the credential's API budget (api_budget, shared with /ltp by
    # every API worker); a cached answer costs none. A symbol that gets no token says so; the others still load.
    budgeted = api_budget.BudgetedAdapter(build_adapter(record), record.id, record.broker_name)
    service = MarketDataService(cast(BrokerInterface, budgeted), lookback_days=lookback, cache_namespace=namespace)
    now = datetime.now(timezone.utc)
    today = now.astimezone(IST).date()
    window = _period_window(period, body.before, lookback, today) if period is not None else None
    today_state = await _today_state(session, body, exchange, now)
    # UI2-4a2: an index's volume from its near-month future - found in the platform's instrument master, and (BT1) only
    # when the future is inside the same session's entitlement as the index itself; else the index keeps its own volume.
    futures: Dict[str, index_volume.NearFuture] = {}
    if body.volume_from_future:
        for raw in wanted:
            main = ents.get((raw, exchange))
            if index_volume.index_underlying(raw, exchange) is None or (byob and (main is None or not main.live)):
                continue
            fut = await index_volume.near_future(session, raw, exchange, today)
            if fut is None:
                continue
            if byob:
                fent = await entitlement.resolve(session, user, fut.symbol, fut.exchange, broker=record.broker_name,
                                                 account_label=record.account_label, priority=priority)
                if not fent.live or fent.record is None or fent.record.id != record.id:
                    warnings.append(f"{raw}: volume from {fut.symbol} not shown ({fent.reason or 'outside your session'})")
                    continue
            futures[raw] = fut
    eod_date, holidays = await _eod_context(session, ents)
    # LD-7: the broker calls below can take seconds; no pool connection is held meanwhile (reads only so far - the meter
    # write after the loop opens a new transaction).
    await release_connection(session)

    out: Dict[str, dict] = {}
    fetched = 0
    newest: List[datetime] = []      # BT1b: each symbol's newest bar - the badge is timed by the data, not the fetch
    for raw in body.symbols:
        symbol = raw.strip().upper()
        if not symbol or symbol in out:
            continue
        ent = ents.get((symbol, exchange))
        if byob and (ent is None or not ent.live):
            # BT1: outside the user's session budget - EOD only for this one, the others still load.
            out[symbol] = {"bars": [], "count": 0, "first": None, "last": None, "error": ent.reason if ent else "EOD data only",
                           "data_tier": ent.badge(eod_date=eod_date, now=now, holidays=holidays) if ent else None, "volume_source": None}
            continue
        try:
            got = await _base_bars(service, symbol, exchange, base_interval, period, window, body.before, today_state, now)
            warnings.extend(f"{symbol}: {note}" for note in got.notes)
            fut = futures.get(symbol)
            fut_bars: Optional[List[OHLCVBar]] = None
            volume_source: Optional[dict] = None
            if fut is not None:
                try:
                    fut_bars = await _future_bars(service, fut, base_interval, period, window, body.before,
                                                  today_state if got.today_added else None, now)
                    volume_source = fut.source(periods.ist_date(fut_bars[0].timestamp) if fut_bars else None)
                except api_budget.BudgetUsed:
                    fut_bars = None
                    warnings.append(f"{symbol}: volume from {fut.symbol} not loaded this time (budget used)")
                except Exception as exc:  # noqa: BLE001 - the index's own bars still make a chart
                    logger.info("Future volume fetch failed for %s via %s: %s", fut.symbol, record.broker_name, exc)
                    fut_bars = None
                    warnings.append(f"{symbol}: volume from {fut.symbol} not loaded ({type(exc).__name__})")
            bars, shaped = await asyncio.to_thread(_shaped, got.bars, body.timeframe, fut_bars)
            if got.today_added and today_state and shaped:
                shaped[-1]["forming"] = True
            out[symbol] = {"bars": shaped, "count": len(bars),
                           "first": bars[0].timestamp.isoformat() if bars else None, "last": bars[-1].timestamp.isoformat() if bars else None, "error": None,
                           "volume_source": volume_source}
            if bars:
                last_ts = bars[-1].timestamp
                newest.append(last_ts if last_ts.tzinfo else last_ts.replace(tzinfo=timezone.utc))
            fetched += 1
            if not bars:
                warnings.append(f"{symbol}: the broker returned no candles")
        except api_budget.BudgetUsed:
            out[symbol] = {"bars": [], "count": 0, "first": None, "last": None, "error": "budget used", "budget_used": True, "volume_source": None}
            continue
        except Exception as exc:  # noqa: BLE001 - one bad symbol must not lose the others
            logger.info("Candle fetch failed for %s:%s via %s: %s", exchange, symbol, record.broker_name, exc)
            out[symbol] = {"bars": [], "count": 0, "first": None, "last": None, "error": f"{type(exc).__name__}: {exc}"[:300], "volume_source": None}
    if fetched:
        await meter(session, user.tenant_id, "market_data_candles", quantity=fetched, source="api",
                    metadata={"broker": record.broker_name, "timeframe": body.timeframe, "exchange": exchange}, commit=True)
    budget_used = sorted(s for s, v in out.items() if v.get("budget_used"))
    if budget_used:
        warnings.append(f"budget used: {len(budget_used)} symbol(s) not loaded this time to keep the broker's rate limit for the "
                        f"trading worker ({', '.join(budget_used[:10])}{' ...' if len(budget_used) > 10 else ''}) - they load on the next refresh")
    if not fetched and not budget_used:
        warnings.append("every symbol failed - check the symbols are the broker's trading symbols and the session token is valid")
    result = {"source": {"broker": record.broker_name, "account_label": record.account_label}, "exchange": exchange, "timeframe": body.timeframe,
              "base_interval": base_interval, "lookback_days": lookback, "before": body.before.isoformat() if body.before else None, "fetched_at": datetime.now(timezone.utc).isoformat(),
              "symbols": out, "warnings": warnings,
              "note": "Candles come from your broker's historical API through your own session and are cached for 60 seconds platform-wide."}
    if live is not None:
        # The stalest symbol decides (a batch is only as fresh as its oldest feed); no bars at all = age unknown = stale.
        oldest = min(newest) if newest else None
        result["data_tier"] = live.badge(now=now, timeframe=body.timeframe if oldest is not None else None, last_bar_start=oldest)
        result["note"] = ("Candles come through your own broker session; a copy another user of the same broker fetched in the last "
                          "minutes is used instead of a second broker call.")
    return result


@router.get("/ltp", dependencies=[Depends(ltp_rate_limit)])
async def ltp(symbol: str, exchange: str = "NSE", broker: Optional[str] = None, account_label: str = "primary",
              user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict:
    """Phase AN: the last price of one symbol for a live chart. Tick cache first (the exchange's own
    last trade when streaming is on), then the broker's quote (with the exchange timestamp, so the
    chart can say how old it is), then the plain LTP. A stale quote is returned flagged, not refused:
    unlike an exit decision, a chart is allowed to show yesterday's close with its age."""
    symbol = symbol.strip().upper()
    exchange = exchange.strip().upper()
    if not symbol:
        raise HTTPException(status_code=400, detail="symbol is required")
    now = datetime.now(timezone.utc)
    if await _byob(session, user):
        # BT1: the user's own confirmed broker session only, its own tick cache only, inside its symbol budget.
        ent = await entitlement.resolve(session, user, symbol, exchange, broker=broker, account_label=account_label if broker else None, now=now)
        if not ent.live or ent.record is None or ent.session is None:
            raise _tier_c({(symbol, exchange): ent})
        # LD-10: the user's own session's tick first, then the shared live feed's for the same broker, then the broker.
        body = await _ltp_body(ent.record, TickCache(namespace=ent.session.namespace), symbol, exchange, now, shared_feed=tick_cache)
        # The tick's / quote's own time; a bare LTP has none, so its age is unknown and the badge says stale.
        as_of = datetime.fromisoformat(body["timestamp"]) if body["timestamp"] else None
        body["data_tier"] = ent.badge(as_of=as_of, now=now)
        if as_of is None:
            # BT1b: no broker timestamp = age unknown = stale, in the body as on the badge.
            body["stale"], body["stale_reason"] = True, body["data_tier"]["stale_reason"]
        return body
    record = await _pick_record(session, user.tenant_id, broker, account_label)
    return await _ltp_body(record, tick_cache, symbol, exchange, now)


async def _ltp_body(record: BrokerCredentialRecord, ticks: TickCache, symbol: str, exchange: str, now: datetime,
                    shared_feed: Optional[TickCache] = None) -> dict:
    base = {"symbol": symbol, "exchange": exchange, "source_broker": record.broker_name, "fetched_at": now.isoformat()}
    tick = await ticks.fresh(record.broker_name, exchange, symbol, now)
    if tick is None and shared_feed is not None and shared_feed is not ticks:
        # LD-10 (owner, 20 Oct): the price is public market data - a fresh tick of the shared live feed (the platform's
        # stream, same broker and exchange) instead of a broker call through this user's budget; else as before.
        tick = await shared_feed.fresh(record.broker_name, exchange, symbol, now)
    if tick is not None:
        return {**base, "ltp": float(tick.ltp), "timestamp": tick.best_ts.isoformat(), "age_seconds": round(tick.age_seconds(now), 1),
                "stale": False, "stale_reason": None, "source": "tick", "bid": None, "ask": None, "volume": None}
    # CH5-a review (ADR-0004): one broker call per credential and symbol every few seconds, whoever asks - the result is
    # shared through the cache, and one request at a time makes the call while the others wait for it.
    shared = await api_budget.cached_quote(record.id, exchange, symbol)
    if shared is not None:
        return _refreshed(shared, now)
    holder = await api_budget.claim_fetch(record.id, exchange, symbol)
    if holder is None:
        shared = await api_budget.wait_for_quote(record.id, exchange, symbol)
        if shared is not None:
            return _refreshed(shared, now)
    try:
        body = await _broker_ltp(record, base, symbol, exchange, now)
        await api_budget.store_quote(record.id, exchange, symbol, body)
        return body
    finally:
        if holder is not None:
            await api_budget.release_fetch(record.id, exchange, symbol, holder)


def _refreshed(body: dict, now: datetime) -> dict:
    """A shared quote, its age and staleness measured again now (the price itself is the broker's, a few seconds old)."""
    out = {**body, "shared": True}
    if body.get("timestamp"):
        ts = datetime.fromisoformat(body["timestamp"])
        reason = quote_is_stale(ts, now)
        out.update(age_seconds=round((now - ts).total_seconds(), 1), stale=reason is not None, stale_reason=reason)
    return out


async def _broker_ltp(record: BrokerCredentialRecord, base: dict, symbol: str, exchange: str, now: datetime) -> dict:
    adapter = build_adapter(record)

    async def token() -> None:
        # Every broker call draws from this credential's API budget, shared by every API process (api_budget); none
        # within a moment -> 429, never a queue that grows while the worker's exits need the same API key.
        if not await api_budget.take(record.id, record.broker_name):
            raise HTTPException(status_code=429, detail="The broker's rate budget for this session is in use - the price refreshes shortly",
                                headers={"Retry-After": "5"})
    try:
        await token()
        quote = await adapter.get_quote_for_symbol(symbol, exchange)
        if quote is not None:
            reason = quote_is_stale(quote.timestamp, now)
            age = (now - quote.timestamp).total_seconds() if quote.timestamp is not None else None
            return {**base, "ltp": float(quote.ltp), "timestamp": quote.timestamp.isoformat() if quote.timestamp else None,
                    "age_seconds": round(age, 1) if age is not None else None, "stale": reason is not None, "stale_reason": reason,
                    "source": "quote", "bid": quote.bid, "ask": quote.ask, "volume": quote.volume or None}
        await token()
        price = await adapter.get_ltp_for_symbol(symbol, exchange)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - the broker's words, as a 502 the chart can show
        raise HTTPException(status_code=502, detail=f"{record.broker_name}: {type(exc).__name__}: {exc}"[:300]) from exc
    return {**base, "ltp": float(price), "timestamp": None, "age_seconds": None, "stale": False, "stale_reason": None,
            "source": "ltp", "bid": None, "ask": None, "volume": None}


class ChainsBody(BaseModel):
    underlyings: List[str] = Field(min_length=1, max_length=20)
    expiry: Optional[date] = Field(default=None, description="omitted = the nearest expiry the broker returns")
    broker: Optional[str] = None
    account_label: str = Field(default="primary", max_length=50)


@router.post("/option-chains", dependencies=[Depends(chains_rate_limit)])
async def option_chains(body: ChainsBody, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict:
    """Phase AD: the broker's live option chain for up to 20 underlyings through the tenant's own
    session, in the same `OptionChain` shape the scanner, the chain analyser and the strike
    selector consume. Each underlying succeeds or fails on its own."""
    byob = await _byob(session, user)
    ents: Dict[tuple, Entitlement] = {}
    live: Optional[Entitlement] = None
    if byob:
        # BT1: an option chain counts as one symbol (the underlying, F&O segment) in the user's own session budget.
        wanted = list(dict.fromkeys(u.strip().upper() for u in body.underlyings if u.strip()))
        ents = await entitlement.resolve_many(session, user, [(u, "NFO") for u in wanted], broker=body.broker,
                                              account_label=body.account_label if body.broker else None, priority=Priority.CHART)
        live = next((e for e in ents.values() if e.live), None)
        if live is None or live.record is None:
            raise _tier_c(ents)
        record = live.record
    else:
        record = await _pick_record(session, user.tenant_id, body.broker, body.account_label)
    adapter = build_adapter(record)
    now = datetime.now(timezone.utc)
    eod_date, holidays = await _eod_context(session, ents)
    out: Dict[str, dict] = {}
    warnings: List[str] = []
    fetched = 0
    for raw in body.underlyings:
        underlying = raw.strip().upper()
        if not underlying or underlying in out:
            continue
        ent = ents.get((underlying, "NFO"))
        if byob and (ent is None or not ent.live):
            out[underlying] = {"chain": None, "rows": 0, "error": ent.reason if ent else "EOD data only",
                               "data_tier": ent.badge(eod_date=eod_date, now=now, holidays=holidays) if ent else None}
            continue
        try:
            chain = await adapter.get_option_chain(underlying, body.expiry)
            out[underlying] = {"chain": chain.model_dump(mode="json"), "rows": len(chain.rows), "error": None}
            fetched += 1
            if not chain.rows:
                warnings.append(f"{underlying}: the broker returned an empty chain")
        except NotImplementedError:
            out[underlying] = {"chain": None, "rows": 0, "error": f"{record.broker_name} has no option-chain endpoint in this adapter"}
        except Exception as exc:  # noqa: BLE001 - one underlying must not lose the others
            logger.info("Option chain fetch failed for %s via %s: %s", underlying, record.broker_name, exc)
            out[underlying] = {"chain": None, "rows": 0, "error": f"{type(exc).__name__}: {exc}"[:300]}
    if fetched:
        await meter(session, user.tenant_id, "market_data_chains", quantity=fetched, source="api",
                    metadata={"broker": record.broker_name, "expiry": body.expiry.isoformat() if body.expiry else None}, commit=True)
    else:
        warnings.append("every underlying failed - check the symbols are index/stock names the broker's option master knows")
    result = {"source": {"broker": record.broker_name, "account_label": record.account_label}, "expiry": body.expiry.isoformat() if body.expiry else None,
              "fetched_at": datetime.now(timezone.utc).isoformat(), "symbols": out, "warnings": warnings,
              "note": "Live chain through your own broker session; option-chain filters and the analyser read it exactly as they read a sample chain."}
    if live is not None:
        # The chain carries no exchange timestamp: its age is unknown, so the badge says stale (alerts suppressed).
        result["data_tier"] = live.badge(now=now)
    return result


@router.get("/data-tier")
async def data_tier(symbol: Optional[str] = None, exchange: str = "NSE", user: User = Depends(get_current_user),
                    session: AsyncSession = Depends(get_session)) -> dict:
    """BT1: the data-tier badge. With `symbol`: the tier this user would see for it (Live through their own confirmed
    broker session, or EOD with the reason) - a lookup that takes no budget slot. Without: the market-wide badge (EOD, as
    of the newest exchange file). `enabled: false` when the `byob_data_tier` flag is off (pages show no badge)."""
    if not await _byob(session, user):
        return {"enabled": False}
    now = datetime.now(timezone.utc)
    eod_date = await entitlement.eod_as_of(session)
    holidays = await load_holidays(session)
    market_wide = entitlement.eod_badge(eod_date, now, holidays)
    badge = market_wide
    if symbol and symbol.strip():
        sym, exch = symbol.strip().upper(), exchange.strip().upper()
        ent = await entitlement.resolve(session, user, sym, exch, now=now, register=False)
        as_of = None
        if ent.live and ent.session is not None:
            tick = await TickCache(namespace=ent.session.namespace).get(ent.session.broker, exch, sym)
            as_of = tick.best_ts if tick is not None else None
        badge = ent.badge(as_of=as_of, eod_date=eod_date, now=now, holidays=holidays)
    return {"enabled": True, "badge": badge, "market_wide": market_wide, "eod_date": eod_date.isoformat() if eod_date else None,
            "sessions": await entitlement.session_summaries(session, user, now)}
