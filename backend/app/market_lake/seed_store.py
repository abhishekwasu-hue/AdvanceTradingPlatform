"""M71 PL-7: read helpers for what the seed import put in a tenant's private store (app.market_lake.seed_import).

- `series`: any stored series (one key, one label: 5m futures, 30m commodities, Daily, Weekly ...) as END-labelled bars
  with the data-use tags of its range - a reader for the sets no page shows yet (no UI in this step). Display path:
  sealed / contaminated bars are returned like every other bar, only tagged.
- `option_premiums`: the recorded end-of-day option premiums (NSE F&O bhavcopy closes, keys
  NFO:<SYMBOL>:<CE|PE>:<expiry>:<strike>, label "day") as the option backtest's chain rows - read FIRST, the model
  prices only what has no recorded premium (app.backtest.routes, owner order M71 PL-7).

Every function takes the reader's tenant and refuses None (the private store has no "all tenants" read; M71Q-2).
"""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import Float, cast, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import MdTenantCandleRecord
from app.market_data.calendar import IST
from app.market_lake import data_use

R = MdTenantCandleRecord
MAX_PREMIUM_ROWS = 500_000
STRIKE_BAND = 0.25              # strikes within 25 % of the candles' low / high: what a run's ladder can reach
EXPIRY_HORIZON_DAYS = 120       # expiries up to this long after the span (monthly and far legs)


def _require_tenant(tenant_id: int | None) -> int:
    if tenant_id is None or int(tenant_id) <= 0:
        raise PermissionError("the private store is read per tenant; a tenant id is required")
    return int(tenant_id)


def _utc(ts: datetime) -> datetime:
    return ts.replace(tzinfo=UTC) if ts.tzinfo is None else ts.astimezone(UTC)


async def series(session: AsyncSession, tenant_id: int | None, key: str, label: str, start: datetime, end: datetime, *,
                 limit: int = 200_000) -> dict[str, Any]:
    """{"bars": [{ts (END, UTC ISO), open, high, low, close, volume (None = not in the source), oi}], "tags": [...]}
    for (start, end], the highest version per END. More than `limit` bars raises ValueError (refused, never cut)."""
    tenant = _require_tenant(tenant_id)
    rows = await session.execute(select(R.ts, R.version, cast(R.open, Float), cast(R.high, Float), cast(R.low, Float), cast(R.close, Float),
                                        R.volume, R.oi).where(R.tenant_id == tenant, R.instrument_key == key, R.timeframe == label,
                                                              R.ts > _utc(start), R.ts <= _utc(end)).order_by(R.ts, R.version))
    best: dict[datetime, Any] = {}
    for row in rows:
        best[_utc(row[0])] = row
        if len(best) > limit:
            raise ValueError(f"more than {limit} bars in the window; choose a shorter one")
    bars = [{"ts": t.isoformat(), "open": r[2], "high": r[3], "low": r[4], "close": r[5],
             "volume": None if r[6] is None else int(r[6]), "oi": r[7]} for t, r in sorted(best.items())]
    tags: list[dict[str, Any]] = []
    if best:
        first = (min(best) - timedelta(microseconds=1)).astimezone(IST).date()
        last = (max(best) - timedelta(microseconds=1)).astimezone(IST).date()
        tags = data_use.tags(await data_use.windows_for(session, tenant, key), first, last)
    return {"key": key, "interval": label, "bars": bars, "tags": tags}


def parse_option_key(key: str) -> tuple[str, str, date, float] | None:
    """'NFO:NIFTY:CE:2023-03-30:17000' -> ('NIFTY', 'CE', 2023-03-30, 17000.0); anything else None."""
    parts = key.split(":")
    if len(parts) != 5 or parts[0] not in ("NFO", "BFO") or parts[2] not in ("CE", "PE"):
        return None
    try:
        return parts[1], parts[2], date.fromisoformat(parts[3]), float(parts[4])
    except ValueError:
        return None


async def has_premiums(session: AsyncSession, tenant_id: int | None, underlying: str, start: datetime, end: datetime) -> bool:
    """Whether the tenant holds any recorded end-of-day option premium of `underlying` in the span (one indexed lookup)."""
    tenant = _require_tenant(tenant_id)
    name = " ".join((underlying or "").upper().split())
    found = await session.scalar(select(R.ts).where(R.tenant_id == tenant, R.timeframe == "day", _option_keys(name),
                                                    R.ts >= _utc(start), R.ts <= _utc(end) + timedelta(days=1)).limit(1))
    return found is not None


def _option_keys(name: str) -> Any:
    return or_(*(R.instrument_key.like(f"{venue}:{name}:{right}:%") for venue in ("NFO", "BFO") for right in ("CE", "PE")))


async def option_premiums(session: AsyncSession, tenant_id: int | None, underlying: str, start: datetime, end: datetime, *,
                          strikes: tuple[float, float] | None = None, expiry_until: datetime | None = None,
                          limit: int | None = None) -> list[Any]:
    """The tenant's recorded end-of-day premiums of `underlying`'s options with the session END in [start, end], as
    `OptionChainSnapshotRow`s (timestamp = the session close, ltp = the day's close, oi) - only strikes inside `strikes`
    and expiries up to `expiry_until` when given (the contracts a run can trade). A close of 0 or below is no premium
    and is left out. More than `limit` kept rows raises ValueError (the caller prices by the model, never cuts)."""
    from app.backtest.options import OptionChainSnapshotRow
    tenant = _require_tenant(tenant_id)
    limit = MAX_PREMIUM_ROWS if limit is None else limit
    name = " ".join((underlying or "").upper().split())
    if not name:
        return []
    lo, hi = _utc(start) - timedelta(days=1), _utc(end)
    last_expiry = _utc(expiry_until).date() if expiry_until is not None else None
    query = select(R.instrument_key, R.ts, R.version, cast(R.close, Float), R.oi).where(
        R.tenant_id == tenant, R.timeframe == "day", _option_keys(name), R.ts >= lo, R.ts <= hi).order_by(
        R.instrument_key, R.ts, R.version).execution_options(yield_per=20_000)
    best: dict[tuple[str, datetime], tuple[float, Any]] = {}
    result = await session.stream(query)
    async for key, ts, _v, close, oi in result:
        parsed = parse_option_key(key)
        if parsed is None:
            continue
        _sym, _right, expiry, strike = parsed
        if (strikes is not None and not strikes[0] <= strike <= strikes[1]) or (last_expiry is not None and expiry > last_expiry):
            continue
        best[(key, _utc(ts))] = (float(close), oi)
        if len(best) > limit:
            await result.close()
            raise ValueError(f"more than {limit} recorded option premiums in the span")
    out: list[Any] = []
    for (key, ts), (close, oi) in best.items():
        parsed = parse_option_key(key)
        if parsed is None or close <= 0:
            continue
        _sym, right, expiry, strike = parsed
        out.append(OptionChainSnapshotRow(timestamp=ts, expiry=expiry, strike=strike, right=right, ltp=close,
                                          oi=None if oi is None else float(oi)))
    return out
