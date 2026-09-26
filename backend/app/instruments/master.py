"""Phase F1: the platform's instrument master.

Every F&O decision needs facts only the exchange/broker master has: which contracts exist for an
underlying, their expiries and strikes, the lot size, the broker's own instrument key. Broker
adapters already download their master for symbol resolution, but only into process memory.
This module persists it (`instruments` table), so the deployments API, the contract resolver and
the console can search it, and syncs it once a day pre-market from the worker.

Sources:
* **Upstox** - public gzip JSON per exchange (no token needed), so the platform always has a
  master even before any tenant logs in. Expiries come as epoch milliseconds.
* **Zerodha** - the tenant's own Kite session (`get_instruments`), when one is usable.

Underlying names: strategies analyse index candles under the index symbol (`NIFTY 50`), while
the F&O master names the underlying `NIFTY`. `underlying_of` maps between the two; equities map
to themselves.
"""
import gzip
import json
import logging
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence

import httpx
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import InstrumentRecord

logger = logging.getLogger(__name__)

UPSTOX_MASTER_URL = "https://assets.upstox.com/market-quote/instruments/exchange/{exchange}.json.gz"
UPSTOX_BROKER = "upstox"
OPTION_TYPES = ("CE", "PE")
DERIVATIVE_TYPES = ("CE", "PE", "FUT")

# Index candle symbol -> F&O underlying name, as the masters spell them.
UNDERLYING_ALIASES: Dict[str, str] = {
    "NIFTY 50": "NIFTY", "NIFTY": "NIFTY",
    "NIFTY BANK": "BANKNIFTY", "BANKNIFTY": "BANKNIFTY",
    "NIFTY FIN SERVICE": "FINNIFTY", "FINNIFTY": "FINNIFTY",
    "NIFTY MID SELECT": "MIDCPNIFTY", "MIDCPNIFTY": "MIDCPNIFTY",
    "NIFTY NEXT 50": "NIFTYNXT50", "NIFTYNXT50": "NIFTYNXT50",
    "SENSEX": "SENSEX", "BANKEX": "BANKEX",
}
# The reverse: F&O underlying -> the symbol whose candles/LTP the strategy uses.
INDEX_SYMBOLS: Dict[str, str] = {
    "NIFTY": "NIFTY 50", "BANKNIFTY": "NIFTY BANK", "FINNIFTY": "NIFTY FIN SERVICE",
    "MIDCPNIFTY": "NIFTY MID SELECT", "NIFTYNXT50": "NIFTY NEXT 50", "SENSEX": "SENSEX", "BANKEX": "BANKEX",
}
INDEX_EXCHANGE: Dict[str, str] = {"SENSEX": "BSE", "BANKEX": "BSE"}


def underlying_of(symbol: str) -> str:
    """`NIFTY 50` -> `NIFTY`; `RELIANCE` -> `RELIANCE`."""
    key = (symbol or "").strip().upper()
    return UNDERLYING_ALIASES.get(key, key)


def derivatives_exchange(underlying: str) -> str:
    """Where this underlying's F&O contracts trade: NFO for NSE names, BFO for SENSEX/BANKEX."""
    return "BFO" if INDEX_EXCHANGE.get(underlying.upper()) == "BSE" else "NFO"


def normalise_expiry(value: Any) -> Optional[date]:
    """Masters disagree: Upstox sends epoch milliseconds, Kite an ISO date, some an ISO datetime."""
    if value in (None, "", 0, "0"):
        return None
    if isinstance(value, (int, float)):
        seconds = float(value) / (1000.0 if float(value) > 1e11 else 1.0)
        return datetime.fromtimestamp(seconds, tz=timezone.utc).date()
    text = str(value).strip()
    if text.isdigit():
        return normalise_expiry(int(text))
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        pass
    for fmt in ("%d-%b-%Y", "%d%b%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


@dataclass(frozen=True)
class MasterRow:
    broker: str
    exchange: str
    segment: Optional[str]
    instrument_key: str
    tradingsymbol: str
    name: Optional[str]
    underlying: Optional[str]
    instrument_type: str
    expiry: Optional[date]
    strike: Optional[float]
    lot_size: int
    tick_size: float
    weekly: bool = False


def _instrument_type(raw: Optional[str], segment: Optional[str]) -> str:
    text = (raw or "").upper()
    if text in ("CE", "PE", "FUT", "EQ", "INDEX"):
        return text
    if text in ("FUTIDX", "FUTSTK", "FUTCOM", "FUTCUR"):
        return "FUT"
    if text in ("OPTIDX", "OPTSTK"):
        return "CE"  # refined by the caller when the row carries a right
    if segment and "INDEX" in segment.upper():
        return "INDEX"
    return text or "EQ"


def parse_upstox_master(rows: Iterable[Dict[str, Any]], exchange: str) -> List[MasterRow]:
    """Upstox's per-exchange JSON: `instrument_key` (e.g. `NSE_FO|56789`), `trading_symbol`,
    `segment` (NSE_EQ/NSE_FO/NSE_INDEX/BSE_FO/...), `instrument_type` (EQ/INDEX/FUT/CE/PE),
    `expiry` (epoch ms), `strike_price`, `lot_size`, `tick_size`, `underlying_symbol`/`name`,
    `weekly`. Only the four types F&O routing needs are kept, plus equities and indices."""
    out: List[MasterRow] = []
    for row in rows:
        key = row.get("instrument_key")
        symbol = row.get("trading_symbol") or row.get("tradingsymbol")
        if not key or not symbol:
            continue
        segment = row.get("segment")
        itype = _instrument_type(row.get("instrument_type"), segment)
        if itype not in ("CE", "PE", "FUT", "EQ", "INDEX"):
            continue
        # The exchange a contract trades on, as brokers name it: derivatives of NSE names are NFO.
        seg_upper = (segment or "").upper()
        if seg_upper.endswith("_FO"):
            exch = "BFO" if seg_upper.startswith("BSE") else "NFO"
        elif seg_upper.endswith("_INDEX") or seg_upper.endswith("_EQ"):
            exch = seg_upper.split("_")[0]
        else:
            exch = row.get("exchange") or exchange
        underlying = row.get("underlying_symbol") or row.get("asset_symbol") or row.get("name")
        if itype in ("EQ", "INDEX"):
            underlying = underlying_of(symbol) if itype == "INDEX" else symbol
        strike = row.get("strike_price")
        try:
            strike_value = float(strike) if strike not in (None, "") else None
        except (TypeError, ValueError):
            strike_value = None
        if itype in ("EQ", "INDEX", "FUT") and strike_value == 0:
            strike_value = None
        try:
            lot = int(float(row.get("lot_size") or 1)) or 1
        except (TypeError, ValueError):
            lot = 1
        try:
            tick = float(row.get("tick_size") or 0.05) or 0.05
        except (TypeError, ValueError):
            tick = 0.05
        out.append(MasterRow(
            broker=UPSTOX_BROKER, exchange=exch, segment=segment, instrument_key=str(key), tradingsymbol=str(symbol).upper(),
            name=row.get("name"), underlying=(str(underlying).upper() if underlying else None), instrument_type=itype,
            expiry=normalise_expiry(row.get("expiry")), strike=strike_value, lot_size=lot, tick_size=tick,
            weekly=bool(row.get("weekly", False)),
        ))
    return out


def parse_broker_instruments(broker: str, instruments: Sequence[Any]) -> List[MasterRow]:
    """From an adapter's `Instrument` list (Zerodha/Shoonya), e.g. Kite's NFO dump: `name` is the
    underlying, `instrument_type` CE/PE/FUT/EQ, `expiry` ISO."""
    out: List[MasterRow] = []
    for i in instruments:
        itype = _instrument_type(getattr(i, "instrument_type", None), getattr(i, "segment", None))
        if itype not in ("CE", "PE", "FUT", "EQ", "INDEX"):
            continue
        symbol = (getattr(i, "tradingsymbol", "") or "").upper()
        underlying = getattr(i, "name", None) or symbol
        if itype == "INDEX":
            underlying = underlying_of(symbol)
        elif itype == "EQ":
            underlying = symbol
        out.append(MasterRow(
            broker=broker, exchange=getattr(i, "exchange", "") or "", segment=getattr(i, "segment", None),
            instrument_key=str(getattr(i, "instrument_token")), tradingsymbol=symbol, name=getattr(i, "name", None),
            underlying=str(underlying).upper() if underlying else None, instrument_type=itype,
            expiry=normalise_expiry(getattr(i, "expiry", None)), strike=getattr(i, "strike", None),
            lot_size=int(getattr(i, "lot_size", 1) or 1), tick_size=float(getattr(i, "tick_size", 0.05) or 0.05),
        ))
    return out


async def replace_master(session: AsyncSession, broker: str, exchanges: Sequence[str], rows: Sequence[MasterRow], *,
                         now: Optional[datetime] = None, batch_size: int = 2000) -> int:
    """Swaps the stored master for `broker` on the given exchanges with `rows`. One transaction,
    so readers never see a half-synced table."""
    now = now or datetime.now(timezone.utc)
    await session.execute(delete(InstrumentRecord).where(
        InstrumentRecord.broker == broker, InstrumentRecord.exchange.in_(list(exchanges))))
    payload = [{
        "broker": r.broker, "exchange": r.exchange, "segment": r.segment, "instrument_key": r.instrument_key,
        "tradingsymbol": r.tradingsymbol, "name": r.name, "underlying": r.underlying, "instrument_type": r.instrument_type,
        "expiry": r.expiry, "strike": r.strike, "lot_size": r.lot_size, "tick_size": r.tick_size, "weekly": r.weekly,
        "synced_at": now,
    } for r in rows if r.exchange in exchanges]
    for start in range(0, len(payload), batch_size):
        await session.execute(InstrumentRecord.__table__.insert(), payload[start:start + batch_size])
    await session.commit()
    logger.info("Instrument master replaced for %s %s: %d rows", broker, list(exchanges), len(payload))
    return len(payload)


async def download_upstox_master(exchange: str, client: Optional[httpx.AsyncClient] = None) -> List[Dict[str, Any]]:
    own = client is None
    client = client or httpx.AsyncClient(timeout=60.0)
    try:
        response = await client.get(UPSTOX_MASTER_URL.format(exchange=exchange))
        response.raise_for_status()
        return json.loads(gzip.decompress(response.content))
    finally:
        if own:
            await client.aclose()


async def sync_upstox(session: AsyncSession, exchanges: Sequence[str] = ("NSE",), client: Optional[httpx.AsyncClient] = None) -> Dict[str, int]:
    """Downloads Upstox's public master for each source exchange and stores every derived
    exchange it contains (NSE -> NSE, NFO; BSE -> BSE, BFO)."""
    counts: Dict[str, int] = {}
    for source in exchanges:
        raw = await download_upstox_master(source, client)
        rows = parse_upstox_master(raw, source)
        stored = sorted({r.exchange for r in rows} | {source})
        counts[source] = await replace_master(session, UPSTOX_BROKER, stored, rows)
    return counts


async def sync_from_adapter(session: AsyncSession, broker_name: str, adapter: Any, exchanges: Sequence[str] = ("NSE", "NFO")) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for exchange in exchanges:
        instruments = await adapter.get_instruments(exchange)
        rows = parse_broker_instruments(broker_name, instruments)
        counts[exchange] = await replace_master(session, broker_name, [exchange], rows)
    return counts


# --- queries ------------------------------------------------------------------------------------

async def master_status(session: AsyncSession) -> List[Dict[str, Any]]:
    rows = await session.execute(
        select(InstrumentRecord.broker, InstrumentRecord.exchange, func.count(), func.max(InstrumentRecord.synced_at))
        .group_by(InstrumentRecord.broker, InstrumentRecord.exchange).order_by(InstrumentRecord.broker, InstrumentRecord.exchange)
    )
    return [{"broker": b, "exchange": e, "rows": n, "synced_at": (s.isoformat() if s else None)} for b, e, n, s in rows]


async def search_instruments(session: AsyncSession, q: str, *, broker: str = UPSTOX_BROKER, exchange: Optional[str] = None,
                             instrument_type: Optional[str] = None, limit: int = 50) -> List[InstrumentRecord]:
    text = (q or "").strip().upper()
    query = select(InstrumentRecord).where(InstrumentRecord.broker == broker)
    if text:
        like = f"%{text}%"
        query = query.where((InstrumentRecord.tradingsymbol.like(like)) | (InstrumentRecord.underlying.like(like)) | (InstrumentRecord.name.like(like)))
    if exchange:
        query = query.where(InstrumentRecord.exchange == exchange.upper())
    if instrument_type:
        query = query.where(InstrumentRecord.instrument_type == instrument_type.upper())
    query = query.order_by(InstrumentRecord.instrument_type, InstrumentRecord.expiry, InstrumentRecord.strike, InstrumentRecord.tradingsymbol).limit(max(1, min(limit, 500)))
    return list(await session.scalars(query))


async def expiries(session: AsyncSession, underlying: str, *, broker: str = UPSTOX_BROKER, instrument_type: str = "CE",
                   on_or_after: Optional[date] = None) -> List[date]:
    query = select(InstrumentRecord.expiry).where(
        InstrumentRecord.broker == broker, InstrumentRecord.underlying == underlying_of(underlying),
        InstrumentRecord.instrument_type == instrument_type.upper(), InstrumentRecord.expiry.is_not(None),
    ).distinct().order_by(InstrumentRecord.expiry)
    if on_or_after is not None:
        query = query.where(InstrumentRecord.expiry >= on_or_after)
    return [e for e in await session.scalars(query) if e is not None]


async def strikes(session: AsyncSession, underlying: str, expiry: date, *, broker: str = UPSTOX_BROKER, instrument_type: str = "CE") -> List[float]:
    query = select(InstrumentRecord.strike).where(
        InstrumentRecord.broker == broker, InstrumentRecord.underlying == underlying_of(underlying),
        InstrumentRecord.instrument_type == instrument_type.upper(), InstrumentRecord.expiry == expiry,
        InstrumentRecord.strike.is_not(None),
    ).distinct().order_by(InstrumentRecord.strike)
    return [float(s) for s in await session.scalars(query) if s is not None]


async def find_option(session: AsyncSession, underlying: str, expiry: date, strike: float, right: str, *, broker: str = UPSTOX_BROKER) -> Optional[InstrumentRecord]:
    return await session.scalar(select(InstrumentRecord).where(
        InstrumentRecord.broker == broker, InstrumentRecord.underlying == underlying_of(underlying),
        InstrumentRecord.instrument_type == right.upper(), InstrumentRecord.expiry == expiry,
        func.abs(InstrumentRecord.strike - strike) < 1e-6,
    ).limit(1))


async def find_future(session: AsyncSession, underlying: str, expiry: date, *, broker: str = UPSTOX_BROKER) -> Optional[InstrumentRecord]:
    return await session.scalar(select(InstrumentRecord).where(
        InstrumentRecord.broker == broker, InstrumentRecord.underlying == underlying_of(underlying),
        InstrumentRecord.instrument_type == "FUT", InstrumentRecord.expiry == expiry,
    ).limit(1))


async def find_by_symbol(session: AsyncSession, tradingsymbol: str, *, broker: str = UPSTOX_BROKER, exchange: Optional[str] = None) -> Optional[InstrumentRecord]:
    query = select(InstrumentRecord).where(InstrumentRecord.broker == broker, InstrumentRecord.tradingsymbol == tradingsymbol.upper())
    if exchange:
        query = query.where(InstrumentRecord.exchange == exchange.upper())
    return await session.scalar(query.limit(1))


def as_dict(r: InstrumentRecord) -> Dict[str, Any]:
    return {
        "id": r.id, "broker": r.broker, "exchange": r.exchange, "segment": r.segment, "instrument_key": r.instrument_key,
        "tradingsymbol": r.tradingsymbol, "name": r.name, "underlying": r.underlying, "instrument_type": r.instrument_type,
        "expiry": r.expiry.isoformat() if r.expiry else None, "strike": r.strike, "lot_size": r.lot_size,
        "tick_size": r.tick_size, "weekly": r.weekly,
    }
