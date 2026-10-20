"""UI2-2 (UI v2 proposal §5 "Data behind each block", §13 UI-3): the dashboard's market strip - `GET /api/market/strip`.

One read-only call for the three indices the dashboard shows (NIFTY 50, NIFTY BANK, INDIA VIX) and the market's state,
instead of three rate-limited `/api/market-data/ltp` calls from the browser:

* **Prices.** The watchlist's batch quotes (`quotes_routes.quotes`): fresh ticks first, then this credential's cached
  quotes, then ONE broker multi-quote call (or a capped per-symbol call) counted against the credential's existing API
  budget (`api_budget`), behind the same broker gate and BT1 entitlement guard. Nothing new is spent or cached.
* **Fallback: market memory.** With no usable broker session (none stored, not logged in today, Tier C under BT1) or no
  live price for an index, the newest market-memory reading of the caller's own organisation is used
  (`market_snapshots`: the Copilot's CUE / SYMBOL reads and the VIX gate's reading, captured by the worker through the
  organisation's broker). It is marked `source: "memory"` with its time. With neither, the index is `missing` with
  `ltp: null` - never a made-up price (owner brief: no sample data).
* **Market state.** Open / pre-open / closed with the next open, from the exchange calendar and `market_holidays` (the
  holiday's name when today is one). No worker heartbeat or internals.

Tenant-safe: prices go through the caller's organisation's broker session (or, under BT1, the caller's own), and the
memory read is filtered by the caller's tenant. Rate limited per user (the dashboard polls every 15 s while the market
is open, every 5 min otherwise). Read-only: nothing is written to the database, no order path is reached.
"""
import json
import logging
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.core.rate_limit import user_rate_limit
from app.db.models import MarketHolidayRecord, MarketSnapshotRecord, User
from app.db.session import get_session
from app.market_data import quotes_routes
from app.market_data.calendar import EXCHANGE_SESSIONS, IST, family_holidays, session_status

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/market", tags=["market-data"])

# (label shown to the trader, the broker's trading symbol, exchange)
INDICES: Tuple[Tuple[str, str, str], ...] = (
    ("NIFTY 50", "NIFTY 50", "NSE"),
    ("BANK NIFTY", "NIFTY BANK", "NSE"),
    ("INDIA VIX", "INDIA VIX", "NSE"),
)
# Market-memory rows that carry an index's level: the Copilot's daily cue and intraday read, the VIX gate's reading.
MEMORY_KINDS = ("CUE", "SYMBOL", "VIX")
MEMORY_MAX_AGE = timedelta(days=5)          # a long weekend plus a holiday; older is not "the last close"
PRE_OPEN = time(9, 0)                       # NSE pre-open call auction 09:00-09:15
REFRESH_OPEN_SECONDS = 15                   # §5 "Refresh": the strip every 15 s during market hours ...
REFRESH_CLOSED_SECONDS = 300                # ... and every 5 min outside them
# The dashboard polls every 15 s while visible (4 a minute); 20 leaves room for a second tab and a reload.
strip_rate_limit = user_rate_limit("market_strip", limit=20, window_seconds=60)


def _hhmm(t: time) -> str:
    return t.strftime("%H:%M")


def market_state(now: datetime, holidays: Dict[date, str]) -> Dict[str, Any]:
    """The strip's market chip, from the NSE calendar: phase `open` / `pre-open` / `closed`, the reason when closed
    (`weekend`, `holiday`, `before-open`, `after-close`), the next open (ISO, IST) and the holiday's name. Pure."""
    spec = EXCHANGE_SESSIONS["NSE"]
    status = session_status(now, set(holidays), "NSE")
    now_ist = now.astimezone(IST)
    today = now_ist.date()
    base: Dict[str, Any] = {"exchange": "NSE", "is_open": status.is_open, "opens_at": _hhmm(spec.open), "closes_at": _hhmm(spec.close),
                            "next_open": status.next_open.isoformat() if status.next_open else None, "holiday": None, "reason": None}
    if status.is_open:
        return {**base, "phase": "open"}
    if today in holidays:
        return {**base, "phase": "closed", "reason": "holiday", "holiday": holidays[today] or None}
    if today.weekday() >= 5:
        return {**base, "phase": "closed", "reason": "weekend"}
    current = now_ist.time()
    if current < spec.open:
        if current >= PRE_OPEN:
            return {**base, "phase": "pre-open", "reason": "before-open"}
        return {**base, "phase": "closed", "reason": "before-open"}
    return {**base, "phase": "closed", "reason": "after-close"}


async def _holidays(session: AsyncSession, now: datetime) -> Dict[date, str]:
    """NSE's closed days (its own calendar and the ones it follows) with today's name, if today is one."""
    days = await family_holidays(session, "NSE")
    names: Dict[date, str] = {d: "" for d in days}
    today = now.astimezone(IST).date()
    if today in names:
        description = await session.scalar(select(MarketHolidayRecord.description).where(
            MarketHolidayRecord.exchange == "NSE", MarketHolidayRecord.holiday_date == today))
        names[today] = (description or "").strip()[:60]
    return names


def _empty(label: str, symbol: str, exchange: str) -> Dict[str, Any]:
    return {"key": symbol, "label": label, "symbol": symbol, "exchange": exchange, "ltp": None, "prev_close": None, "change": None,
            "change_pct": None, "as_of": None, "stale": None, "source": None, "missing": True}


def _from_live(base: Dict[str, Any], row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if row.get("missing") or not isinstance(row.get("ltp"), (int, float)) or not row["ltp"] > 0:
        return None
    return {**base, "ltp": float(row["ltp"]), "prev_close": row.get("prev_close"), "change": row.get("change"),
            "change_pct": row.get("change_pct"), "as_of": row.get("timestamp"), "stale": bool(row.get("stale")),
            "source": "live", "missing": False}


def _utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _from_memory(base: Dict[str, Any], record: MarketSnapshotRecord, market_open: bool) -> Optional[Dict[str, Any]]:
    last = record.last_price
    if not isinstance(last, (int, float)) or not last > 0:
        return None
    prev: Optional[float] = None
    try:
        payload = json.loads(record.payload_json or "{}")
    except ValueError:
        payload = {}
    if isinstance(payload, dict) and isinstance(payload.get("prev_close"), (int, float)) and payload["prev_close"] > 0:
        prev = float(payload["prev_close"])
    elif isinstance(record.change_pct, (int, float)) and record.change_pct > -100:
        prev = last / (1 + record.change_pct / 100)
    change = round(last - prev, 2) if prev else None
    change_pct = round((last / prev - 1) * 100, 2) if prev else None
    return {**base, "ltp": round(float(last), 2), "prev_close": round(prev, 2) if prev else None, "change": change, "change_pct": change_pct,
            "as_of": _utc(record.captured_at).isoformat() if record.captured_at else None,
            # While the market trades, a remembered level is behind it; once it has closed it is the last close.
            "stale": market_open, "source": "memory", "missing": False}


async def _memory(session: AsyncSession, tenant_id: int, symbols: List[str], now: datetime) -> Dict[str, MarketSnapshotRecord]:
    """The newest market-memory reading per symbol, of this organisation only, at most MEMORY_MAX_AGE old. One small
    query per symbol (newest first, one row): a symbol whose captures stopped is still found however many newer rows
    the other symbols have."""
    newest: Dict[str, MarketSnapshotRecord] = {}
    for symbol in symbols:
        record = await session.scalar(
            select(MarketSnapshotRecord)
            .where(MarketSnapshotRecord.tenant_id == tenant_id, MarketSnapshotRecord.symbol == symbol,
                   MarketSnapshotRecord.kind.in_(MEMORY_KINDS), MarketSnapshotRecord.captured_at >= now - MEMORY_MAX_AGE,
                   MarketSnapshotRecord.last_price > 0)
            .order_by(MarketSnapshotRecord.captured_at.desc())
            .limit(1))
        if record is not None:
            newest[symbol] = record
    return newest


async def _live(session: AsyncSession, user: User) -> Tuple[Dict[str, Dict[str, Any]], Optional[Dict[str, Any]]]:
    """Live rows by symbol and the broker session they came from - or nothing when no session may be used."""
    body = quotes_routes.QuotesBody(items=[quotes_routes.QuoteItem(symbol=symbol, exchange=exchange) for _, symbol, exchange in INDICES])
    try:
        out = await quotes_routes.quotes(body, user=user, session=session)
    except HTTPException as exc:
        # 409: no usable broker session (or Tier C under BT1); 404 / 429: no record or no budget. The strip falls back.
        logger.debug("Market strip: no live prices for tenant %s (%s)", user.tenant_id, exc.status_code)
        return {}, None
    except Exception as exc:  # noqa: BLE001 - the strip never fails because the broker did; memory answers instead
        logger.info("Market strip: live prices failed for tenant %s: %s", user.tenant_id, type(exc).__name__)
        return {}, None
    return {row["symbol"]: row for row in out.get("items", [])}, out.get("source")


@router.get("/strip", dependencies=[Depends(strip_rate_limit)])
async def strip(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    now = datetime.now(timezone.utc)
    market = market_state(now, await _holidays(session, now))
    live_rows, source_broker = await _live(session, user)
    indices: List[Dict[str, Any]] = []
    for label, symbol, exchange in INDICES:
        base = _empty(label, symbol, exchange)
        row = live_rows.get(symbol)
        indices.append((row is not None and _from_live(base, row)) or base)
    unpriced = [i["symbol"] for i in indices if i["missing"]]
    if unpriced:
        remembered = await _memory(session, user.tenant_id, unpriced, now)
        for n, item in enumerate(indices):
            record = remembered.get(item["symbol"])
            if item["missing"] and record is not None:
                indices[n] = _from_memory(item, record, market["is_open"]) or item
    sources = {i["source"] for i in indices if not i["missing"]}
    source = "none" if not sources else sources.pop() if len(sources) == 1 else "mixed"
    open_or_soon = market["phase"] in ("open", "pre-open")
    return {"indices": indices, "source": source,
            "broker": ({"broker": source_broker.get("broker"), "account_label": source_broker.get("account_label")}
                       if source_broker and any(i["source"] == "live" for i in indices) else None),
            "market": market, "refresh_seconds": REFRESH_OPEN_SECONDS if open_or_soon else REFRESH_CLOSED_SECONDS,
            "fetched_at": now.isoformat()}
