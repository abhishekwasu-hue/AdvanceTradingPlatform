"""N2-5 (P1 spec 1.5, docs/design/N2_P1FGH.md): the market context panel - the context the method reads before timing an
entry, one tile each. Every tile answers on its own:

- `status` "ok", "stale" (data older than expected) or "na" (not available, with the reason);
- `source` and `as_of`.

One failing tile never hides the others.

| Tile | Data | Whose |
|---|---|---|
| regime | Dow structure (HH/HL, LH/LL from fractal swings) of the NIFTY 50 on daily and weekly bars | shared daily lake (NSE index closes), or the viewer's own Daily history when it holds more (DATA-H) |
| breadth | advances / declines / unchanged across NSE cash shares on the last two lake days | shared daily lake (NSE bhavcopy) |
| vix | India VIX last close, change, and its rank and percentile over the last year; the gate's live reading when the viewer may see it | shared daily lake; the VIX gate's own BT1b rule for the live value |
| fii_dii | FII / DII net flows | the existing seam (`ai.sentiment.fii_dii_provider`), off until a source with clear terms is configured |
| global | overnight moves: US futures and indices, Asia, crude, gold, dollar, USD/INR, US 10-year | the shared free public cache (`ai.global_cues`), delayed |
| events | market events today and the rest of this IST week | global events plus the caller's organisation's own |

Nothing per user is read except what the user's own organisation keeps (events), the VIX gate's viewer rule and the
viewer's OWN private Daily bars (DATA-H, app/market_lake/own_bars.py). No broker session of anyone is used. The panel describes; it never says what to do.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Dict, Optional, Sequence

import pandas as pd
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import MarketEventRecord, MdCandleRecord, User
from app.market_data.calendar import IST
from app.market_lake.asof import candles_as_of

logger = logging.getLogger(__name__)

FLAG = "context_panel"
TIMEFRAME = "day"
MARKET = "NIFTY 50"
VIX = "INDIA VIX"
REGIME_DAYS = 550          # calendar days of daily bars read for the weekly structure
VIX_RANK_SESSIONS = 252
GLOBAL_FAIL_BACKOFF_SECONDS = 60
MAX_MISSING_SESSIONS = 1   # a daily file one session behind is normal (published in the evening); more is stale
TILES = ("regime", "breadth", "vix", "fii_dii", "global", "events")


def _day(ts: datetime) -> date:
    return (ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)).astimezone(IST).date()


def sessions_missing(last: date, today: date, holidays: Sequence[date] = ()) -> int:
    """Weekday sessions strictly between `last` and `today` that are not holidays (today itself is not counted)."""
    n, d = 0, last + timedelta(days=1)
    hol = set(holidays)
    while d < today:
        if d.weekday() < 5 and d not in hol:
            n += 1
        d += timedelta(days=1)
    return n


def freshness(last: date, today: date, holidays: Sequence[date] = ()) -> str:
    return "stale" if sessions_missing(last, today, holidays) > MAX_MISSING_SESSIONS else "ok"


@dataclass(frozen=True)
class Viewer:
    id: int
    tenant_id: int


def na(reason: str, source: str) -> Dict[str, Any]:
    return {"status": "na", "reason": reason, "source": source, "as_of": None}


# --- pure reads ----------------------------------------------------------------------------------------------------------

def dow_trend(bars: pd.DataFrame, window: int = 3) -> Dict[str, Any]:
    """The Dow read of a bar frame (index = time, columns high / low / close): UPTREND with a higher high and a higher
    low as the last swing of each kind, DOWNTREND with a lower high and a lower low, else RANGE; with the last swings."""
    from app.price_action.market_structure import analyze_market_structure
    result = analyze_market_structure(bars, window=window)
    swings = [{"kind": s.kind, "label": s.label, "price": round(float(s.price), 2), "date": _day(pd.Timestamp(s.timestamp).to_pydatetime()).isoformat()}
              for s in result.swings[-4:]]
    return {"trend": result.trend.value if hasattr(result.trend, "value") else str(result.trend), "swings": swings}


def weekly(bars: pd.DataFrame) -> pd.DataFrame:
    """Daily bars -> weeks ending Friday (high = max, low = min, close = last)."""
    return bars.resample("W-FRI").agg({"high": "max", "low": "min", "close": "last"}).dropna()


def breadth(today_closes: Dict[str, float], previous_closes: Dict[str, float]) -> Dict[str, int]:
    up = down = flat = 0
    for key, close in today_closes.items():
        prev = previous_closes.get(key)
        if prev is None or prev <= 0:
            continue
        if close > prev:
            up += 1
        elif close < prev:
            down += 1
        else:
            flat += 1
    return {"advances": up, "declines": down, "unchanged": flat}


def rank_and_percentile(values: Sequence[float]) -> Dict[str, Optional[float]]:
    """For the last value of a series: its rank in the min-max range (0-100) and the share of earlier values below it."""
    if len(values) < 2:
        return {"rank": None, "percentile": None}
    last, lo, hi = values[-1], min(values), max(values)
    rank = None if hi == lo else round(100.0 * (last - lo) / (hi - lo), 1)
    below = sum(1 for v in values[:-1] if v < last)
    return {"rank": rank, "percentile": round(100.0 * below / (len(values) - 1), 1)}


# --- tiles ---------------------------------------------------------------------------------------------------------------

def index_key(name: str) -> str:
    """The lake key of an index by its NSE name ('INDIA VIX' -> NSE_INDEX:INDIAVIX), also for indices that are not F&O
    underlyings."""
    from app.universe.fo_eod import code_for
    return f"NSE_INDEX:{code_for(name)}"


async def _bars(session: AsyncSession, name: str, days: int, today: date) -> pd.DataFrame:
    lo = datetime.combine(today - timedelta(days=days), datetime.min.time(), tzinfo=IST).astimezone(timezone.utc)
    hi = datetime.combine(today + timedelta(days=1), datetime.min.time(), tzinfo=IST).astimezone(timezone.utc)
    rows = await candles_as_of(session, index_key(name), TIMEFRAME, lo, hi)
    if not rows:
        return pd.DataFrame(columns=["high", "low", "close"])
    idx = pd.DatetimeIndex([pd.Timestamp(datetime.combine(_day(r.ts), datetime.min.time())) for r in rows])
    return pd.DataFrame({"high": [float(r.high) for r in rows], "low": [float(r.low) for r in rows], "close": [float(r.close) for r in rows]},
                        index=idx)


REGIME_BARS = 30           # daily bars the regime read needs


SHORT_HISTORY_MARK = "data not ready"


def short_history(have: int, need: int, what: str, key: str) -> str:
    """DATA-H DH5: the plain sentence for a history too short to read - never "no structure" as if the market had none.
    M59 CR-1: never the internal counts either - "Daily: data not ready (history is loading)". `have` / `need` stay
    in the signature for the callers (the counts are data for the admin's health line, not for a user's sentence)."""
    from app.market_lake.index_history import is_target_key
    loading = "history is loading" if is_target_key(key) else "more history is needed"
    label = what.strip().removesuffix(" bars").strip() or "History"
    return f"{label[:1].upper()}{label[1:]}: {SHORT_HISTORY_MARK} ({loading})"


def is_short_history(text: object) -> bool:
    """True when a reason is `short_history`'s sentence (the history is not stored yet, not a failure)."""
    return SHORT_HISTORY_MARK in str(text or "")


async def _own_daily(session: AsyncSession, user: Viewer, name: str, days: int, today: date) -> Optional[pd.DataFrame]:
    """DATA-H: the viewer's own Daily bars of the index (his private store, closing-auction flags applied), or None."""
    from app.market_lake import own_bars
    key = index_key(name)
    scope = await own_bars.scope_for(session, user, key)
    if scope is None:
        return None
    lo = datetime.combine(today - timedelta(days=days), datetime.min.time(), tzinfo=IST).astimezone(timezone.utc)
    hi = datetime.combine(today + timedelta(days=1), datetime.min.time(), tzinfo=IST).astimezone(timezone.utc)
    rows = []
    for label in ("1d", "day"):
        rows = await own_bars.fetch_rows(session, scope, key, label, lo, hi)
        if rows:
            break
    if not rows:
        return None
    idx = pd.DatetimeIndex([pd.Timestamp(datetime.combine(_day(r[0]), datetime.min.time())) for r in rows])
    return pd.DataFrame({"high": [float(r[3]) for r in rows], "low": [float(r[4]) for r in rows], "close": [float(r[5]) for r in rows]},
                        index=idx)


async def regime_tile(session: AsyncSession, user: Viewer, today: date, holidays: Sequence[date]) -> Dict[str, Any]:
    source = "NSE index closes (daily lake)"
    bars = await _bars(session, MARKET, REGIME_DAYS, today)
    own = await _own_daily(session, user, MARKET, REGIME_DAYS, today)
    if own is not None and len(own) > len(bars):
        bars, source = own, "your broker's Daily history (your own store)"
    if len(bars) < REGIME_BARS:
        return na(short_history(len(bars), REGIME_BARS, "daily bars", index_key(MARKET)), source)
    last = bars.index[-1].date()
    close = round(float(bars["close"].iloc[-1]), 2)
    if source.startswith("your broker"):
        # DH3 / DH6: the structure reads the continuous session's range; the close shown is the session's official close
        from app.market_lake import own_bars
        official = await own_bars.official_close(session, index_key(MARKET), await own_bars.scope_for(session, user, index_key(MARKET)))
        if official is not None and official["date"] == last.isoformat():
            close = official["close"]
    return {"status": freshness(last, today, holidays), "source": source, "as_of": last.isoformat(), "market": MARKET,
            "close": close, "daily": dow_trend(bars), "weekly": dow_trend(weekly(bars), window=2),
            "method": "Dow structure: the last swing high and swing low (fractal swings, 3 bars each side daily, 2 weekly)"}


async def breadth_tile(session: AsyncSession, user: Viewer, today: date, holidays: Sequence[date]) -> Dict[str, Any]:
    source = "NSE bhavcopy, cash market (daily lake)"
    # The last two lake days from the index's own bars (one key: cheap); a cash bar shares the index bar's END time.
    days = list(await session.scalars(select(MdCandleRecord.ts).where(
        MdCandleRecord.instrument_key == index_key(MARKET), MdCandleRecord.timeframe == TIMEFRAME,
        MdCandleRecord.ts <= datetime.combine(today + timedelta(days=1), datetime.min.time(), tzinfo=IST)).group_by(
        MdCandleRecord.ts).order_by(MdCandleRecord.ts.desc()).limit(2)))
    if len(days) < 2:
        return na(f"fewer than two days of {MARKET} closes in the lake to date the breadth", source)

    async def closes(ts: datetime) -> Dict[str, float]:
        rows = await session.execute(select(MdCandleRecord.instrument_key, MdCandleRecord.close, MdCandleRecord.version).where(
            MdCandleRecord.timeframe == TIMEFRAME, MdCandleRecord.instrument_key.like("NSE:%"), MdCandleRecord.ts == ts))
        best: Dict[str, tuple] = {}
        for key, close, version in rows:
            if key not in best or version > best[key][1]:
                best[key] = (float(close), version)
        return {k: v[0] for k, v in best.items()}
    figures = breadth(await closes(days[0]), await closes(days[1]))
    last = _day(days[0])
    counted = figures["advances"] + figures["declines"] + figures["unchanged"]
    if counted == 0:
        return na("no share has a close on both of the last two lake days", source)
    return {"status": freshness(last, today, holidays), "source": source, "as_of": last.isoformat(), "previous": _day(days[1]).isoformat(),
            **figures, "counted": counted, "ratio": round(figures["advances"] / figures["declines"], 2) if figures["declines"] else None}


async def vix_tile(session: AsyncSession, user: Viewer, today: date, holidays: Sequence[date]) -> Dict[str, Any]:
    source = "India VIX daily closes (daily lake)"
    bars = await _bars(session, VIX, 400, today)
    closes = [float(c) for c in bars["close"]][-VIX_RANK_SESSIONS:]
    if len(closes) < 2:
        out: Dict[str, Any] = na("fewer than two India VIX closes in the lake", source)
    else:
        last = bars.index[-1].date()
        out = {"status": freshness(last, today, holidays), "source": source, "as_of": last.isoformat(), "close": round(closes[-1], 2),
               "change_pct": round(100.0 * (closes[-1] / closes[-2] - 1.0), 2) if closes[-2] else None,
               "sessions": len(closes), **rank_and_percentile(closes),
               "method": f"rank = (last - low) / (high - low) and percentile = share of earlier closes below the last, over {len(closes)} sessions"}
    try:
        from app.risk_engine import vix_gate
        gate = await vix_gate.status(session, user.tenant_id, viewer_id=user.id)
        if gate.get("enabled") and gate.get("latest"):
            out["live"] = {"last": gate["latest"]["last"], "change_pct": gate["latest"]["change_pct"], "captured_at": gate["latest"]["captured_at"],
                           "source": "the VIX gate's reading (your own session)"}
    except Exception:  # noqa: BLE001 - the live value is extra; the tile stands without it
        logger.exception("context panel: VIX gate status failed")
    return out


async def fii_dii_tile(session: AsyncSession, user: Viewer, today: date, holidays: Sequence[date]) -> Dict[str, Any]:
    from app.ai.sentiment import fii_dii_provider
    source = "FII / DII provider seam (FII_DII_SOURCE)"
    data = fii_dii_provider()
    if not data:
        return na("No FII / DII source with clear terms of use is configured yet", source)
    return {"status": "ok", "source": source, "as_of": data.get("as_of"), **{k: v for k, v in data.items() if k != "as_of"}}


# None = no failure yet. Not 0.0: time.monotonic() counts from an arbitrary point (on Linux, the host's boot), so in a
# container started less than GLOBAL_FAIL_BACKOFF_SECONDS after boot a 0.0 sentinel would read as "just failed".
_global_failed_at: Dict[str, Optional[float]] = {"at": None}


def reset_global_backoff() -> None:
    _global_failed_at["at"] = None


async def global_tile(session: AsyncSession, user: Viewer, today: date, holidays: Sequence[date]) -> Dict[str, Any]:
    from app.ai import global_cues
    source = global_cues.SOURCE_NOTE_EN
    if not global_cues.enabled():
        return na("Global cues are switched off on this server (GLOBAL_CUES_ENABLED)", source)
    # Review: during an outage a panel load does not fan out to every upstream again for GLOBAL_FAIL_BACKOFF_SECONDS
    # (the shared cache keeps only successful reads).
    failed_at = _global_failed_at["at"]
    if failed_at is not None and time.monotonic() - failed_at < GLOBAL_FAIL_BACKOFF_SECONDS:
        return na("No global market answered a moment ago; asked again shortly", source)
    quotes, errors = await global_cues.fetch_all()
    if not quotes:
        _global_failed_at["at"] = time.monotonic()
        return na("No global market answered" + (f" ({len(errors)} failed)" if errors else ""), source)
    order = {m.key: i for i, m in enumerate(global_cues.MARKETS)}
    rows = sorted(({"key": q["key"], "name": global_cues.BY_KEY[q["key"]].en if q["key"] in global_cues.BY_KEY else q["key"],
                    "group": global_cues.BY_KEY[q["key"]].group if q["key"] in global_cues.BY_KEY else "",
                    "last": q.get("last"), "change_pct": q.get("change_pct"), "as_of": q.get("as_of")} for q in quotes),
                  key=lambda r: order.get(r["key"], 99))
    latest = max((r["as_of"] for r in rows if r["as_of"]), default=None)
    return {"status": "ok", "source": source, "as_of": latest, "markets": rows, "missing": len(errors), "note": global_cues.GIFT_NOTE_EN}


async def events_tile(session: AsyncSession, user: Viewer, today: date, holidays: Sequence[date]) -> Dict[str, Any]:
    source = "Market events calendar (platform and your organisation)"
    week_end = today + timedelta(days=6 - today.weekday())
    rows = list(await session.scalars(select(MarketEventRecord).where(
        MarketEventRecord.event_date >= today, MarketEventRecord.event_date <= week_end,
        or_(MarketEventRecord.tenant_id.is_(None), MarketEventRecord.tenant_id == user.tenant_id)).order_by(
        MarketEventRecord.event_date, MarketEventRecord.start_time, MarketEventRecord.id)))
    items = [{"date": r.event_date.isoformat(), "start_time": r.start_time, "end_time": r.end_time, "kind": r.kind, "scope": r.underlying or "*",
              "action": r.action, "description": r.description, "today": r.event_date == today, "own": r.tenant_id is not None} for r in rows]
    return {"status": "ok", "source": source, "as_of": today.isoformat(), "week_end": week_end.isoformat(),
            "today": [i for i in items if i["today"]], "this_week": [i for i in items if not i["today"]]}


TileFn = Callable[[AsyncSession, Any, date, Sequence[date]], Awaitable[Dict[str, Any]]]
BUILDERS: Dict[str, TileFn] = {"regime": regime_tile, "breadth": breadth_tile, "vix": vix_tile, "fii_dii": fii_dii_tile,
                               "global": global_tile, "events": events_tile}


async def panel(session: AsyncSession, user: User, now: Optional[datetime] = None) -> Dict[str, Any]:
    """Every tile, each on its own: a tile that raises becomes "na" with the error's kind, never a failed panel."""
    from app.market_data.calendar import load_holidays
    now = now or datetime.now(timezone.utc)
    today = now.astimezone(IST).date()
    try:
        holidays = sorted(await load_holidays(session, "NSE"))
    except Exception:  # noqa: BLE001
        holidays = []
    # Plain values, read once: a tile's rollback after a failed statement expires the ORM user, and the next tile must
    # not lazy-load it.
    viewer = Viewer(id=int(user.id), tenant_id=int(user.tenant_id))
    tiles: Dict[str, Any] = {}
    for key in TILES:
        try:
            tiles[key] = await BUILDERS[key](session, viewer, today, holidays)
        except Exception as exc:  # noqa: BLE001 - NA on its own failure (the spec's rule for every tile)
            logger.exception("context panel tile %s failed", key)
            await session.rollback()
            tiles[key] = na(f"Could not be read ({type(exc).__name__})", key)
    return {"tiles": tiles, "order": list(TILES), "as_of": now.isoformat(),
            "note": "Context only: what the data shows now, from the sources named on each tile. It describes the market; it does not "
                    "tell anyone what to trade."}
