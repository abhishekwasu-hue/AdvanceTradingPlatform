"""TM 9.4: the tenant-PRIVATE chain history (`md_tenant_chain_snapshots`) - the owner's own option-chain recording.

Data terms (TM-7, owner 23 Oct; RDY-6): a chain read through one person's broker session is that person's. The recorder
(`chain_recorder`) reads only through the owner's own session and writes here under the owner's tenant; every function
here takes the reader's `tenant_id` and refuses None - there is no "all tenants" read. Within the tenant, rows carry
their holder in `source` (`broker:<name>:u<id>`, HB2's rule) and are shown only to that member (BT1b,
`tenant_store.holder_visible`).

One row = one underlying, one expiry, one slot. The strikes travel as one zlib-compressed JSON document (`CODEC`):
`{"v": 1, "fields": [...], "rows": [[strike, <call fields>, <put fields>], ...]}`, every value as the broker sent it (a
missing or non-finite value is null, never guessed). Greeks are the broker's own; nothing is computed here.
"""
from __future__ import annotations

import json
import math
import zlib
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.brokers.models import OptionChain, OptionChainRow
from app.db.models import MdTenantChainSnapshotRecord
from app.market_data.calendar import EXCHANGE_SESSIONS, IST, session_family
from app.market_lake.tenant_store import HOLDER_PREFIX, holder_visible

CODEC = "zlib-json-v1"
# Per side, in this order, after the strike: the OptionChainRow attribute is "<side>_<attr>".
FIELDS: Tuple[Tuple[str, str], ...] = (
    ("ltp", "ltp"), ("bid", "bid"), ("ask", "ask"), ("volume", "volume"), ("oi", "oi"), ("oi_change", "change_oi"),
    ("iv", "iv"), ("delta", "delta"), ("gamma", "gamma"), ("theta", "theta"), ("vega", "vega"),
)
FIELD_NAMES = tuple(name for name, _ in FIELDS)
MAX_RAW_BYTES = 4 * 1024 * 1024          # one chain's JSON before compression (a few hundred strikes is ~60 KB)
R = MdTenantChainSnapshotRecord


def _require_tenant(tenant_id: Optional[int]) -> int:
    if tenant_id is None or int(tenant_id) <= 0:
        raise PermissionError("The chain history is read per tenant; a tenant id is required")
    return int(tenant_id)


def _utc(ts: datetime) -> datetime:
    return ts.replace(tzinfo=timezone.utc) if ts.tzinfo is None else ts.astimezone(timezone.utc)


def _num(value: Any) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if math.isfinite(value) else None


# ---------------------------------------------------------------- the codec

def encode(rows: Iterable[OptionChainRow]) -> Tuple[bytes, int, int]:
    """(compressed payload, raw JSON bytes, strikes) of a chain's rows, strikes ascending."""
    out: List[List[Optional[float]]] = []
    for row in sorted(rows, key=lambda r: r.strike):
        strike = _num(row.strike)
        if strike is None:
            continue
        values: List[Optional[float]] = [strike]
        for side in ("call", "put"):
            values += [_num(getattr(row, f"{side}_{attr}", None)) for _, attr in FIELDS]
        out.append(values)
    raw = json.dumps({"v": 1, "fields": list(FIELD_NAMES), "rows": out}, separators=(",", ":"), allow_nan=False).encode()
    if len(raw) > MAX_RAW_BYTES:
        raise ValueError(f"chain of {len(out)} strikes is {len(raw)} bytes - above the {MAX_RAW_BYTES} byte cap")
    return zlib.compress(raw, 6), len(raw), len(out)


def decode(payload: bytes, codec: str = CODEC) -> List[OptionChainRow]:
    """The rows `encode` stored (a field this release does not know is ignored; a missing one is None)."""
    if codec != CODEC:
        raise ValueError(f"unknown chain codec {codec!r}")
    doc = json.loads(zlib.decompress(payload))
    names = list(doc.get("fields") or FIELD_NAMES)
    attr_of = dict(FIELDS)
    rows: List[OptionChainRow] = []
    for values in doc.get("rows") or []:
        kwargs: Dict[str, Any] = {"strike": float(values[0])}
        for s, side in enumerate(("call", "put")):
            for i, name in enumerate(names):
                attr = attr_of.get(name)
                pos = 1 + s * len(names) + i
                if attr is not None and pos < len(values):
                    kwargs[f"{side}_{attr}"] = values[pos]
        rows.append(OptionChainRow(**kwargs))
    return rows


# ---------------------------------------------------------------- writing

@dataclass
class WriteResult:
    status: str                       # OK / ALREADY_EXISTS / EMPTY
    slot_start: datetime
    strikes: int = 0
    raw_bytes: int = 0
    stored_bytes: int = 0


def slot_start(now: datetime, minutes: int) -> datetime:
    """The UTC start of the `minutes` slot holding `now`, counted from IST midnight (the OI collector's slots)."""
    ist = _utc(now).astimezone(IST)
    minute_of_day = ist.hour * 60 + ist.minute
    start = ist.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(minutes=minute_of_day - minute_of_day % max(1, minutes))
    return start.astimezone(timezone.utc)


async def exists(session: AsyncSession, tenant_id: int, underlying: str, expiry: date, slot: datetime) -> bool:
    tenant = _require_tenant(tenant_id)
    return bool(await session.scalar(select(R.id).where(R.tenant_id == tenant, R.underlying == underlying, R.expiry == expiry,
                                                        R.slot_start == _utc(slot)).limit(1)))


async def write(session: AsyncSession, tenant_id: int, underlying: str, expiry: date, chain: OptionChain, now: datetime, *,
                slot_minutes: int, source: str, slot: Optional[datetime] = None) -> WriteResult:
    """Stores one chain, read at `now`, as the tenant's slot (`slot`, else the slot holding `now`). Idempotent per
    (tenant, underlying, expiry, slot); does not commit."""
    tenant = _require_tenant(tenant_id)
    now = _utc(now)
    slot = _utc(slot) if slot is not None else slot_start(now, slot_minutes)
    if await exists(session, tenant, underlying, expiry, slot):
        return WriteResult("ALREADY_EXISTS", slot)
    payload, raw_bytes, strikes = encode(chain.rows)
    if strikes == 0:
        return WriteResult("EMPTY", slot)
    try:
        async with session.begin_nested():
            session.add(R(tenant_id=tenant, underlying=underlying, expiry=expiry, trade_date=slot.astimezone(IST).date(),
                          slot_start=slot, captured_at=now, spot=_num(chain.underlying_ltp), strikes=strikes, source=source[:40],
                          codec=CODEC, raw_bytes=raw_bytes, payload=payload))
            await session.flush()
    except IntegrityError:                        # another replica took the slot between the check and the insert
        return WriteResult("ALREADY_EXISTS", slot)
    return WriteResult("OK", slot, strikes, raw_bytes, len(payload))


# ---------------------------------------------------------------- reading

@dataclass(frozen=True)
class StoredChain:
    underlying: str
    expiry: date
    slot_start: datetime
    captured_at: datetime
    spot: Optional[float]
    rows: List[OptionChainRow]

    def chain(self) -> OptionChain:
        return OptionChain(underlying=self.underlying, expiry=self.expiry.isoformat(), underlying_ltp=self.spot, rows=self.rows)

    def as_json(self) -> Dict[str, Any]:
        return {"underlying": self.underlying, "expiry": self.expiry.isoformat(), "slot": self.slot_start.astimezone(IST).isoformat(),
                "captured_at": self.captured_at.isoformat(), "spot": self.spot, "fields": list(FIELD_NAMES),
                "rows": [[r.strike] + [getattr(r, f"{side}_{attr}") for side in ("call", "put") for _, attr in FIELDS] for r in self.rows]}


def _visible(tenant_id: Optional[int], reader_user_id: Optional[int]):
    tenant = _require_tenant(tenant_id)
    return (R.tenant_id == tenant) & holder_visible(R.source, reader_user_id)


async def count(session: AsyncSession, tenant_id: int, underlying: str, start: date, end: date, *, expiry: Optional[date] = None,
                reader_user_id: Optional[int] = None) -> int:
    query = select(func.count()).select_from(R).where(_visible(tenant_id, reader_user_id), R.underlying == underlying,
                                                      R.trade_date >= start, R.trade_date <= end)
    if expiry is not None:
        query = query.where(R.expiry == expiry)
    return int(await session.scalar(query) or 0)


async def read_range(session: AsyncSession, tenant_id: int, underlying: str, start: date, end: date, *, expiry: Optional[date] = None,
                     reader_user_id: Optional[int] = None, max_chains: Optional[int] = None) -> List[StoredChain]:
    """The tenant's stored chains of `underlying` for the IST trading days start..end, oldest first (then by expiry).
    Counted before anything is read: more than `max_chains` raises ValueError (narrow the range or name an expiry)."""
    if end < start:
        return []
    if max_chains is not None:
        stored = await count(session, tenant_id, underlying, start, end, expiry=expiry, reader_user_id=reader_user_id)
        if stored > max_chains:
            raise ValueError(f"{stored} stored chains in the range; at most {max_chains} are read per request - narrow the range "
                             "or pick one expiry")
    query = select(R).where(_visible(tenant_id, reader_user_id), R.underlying == underlying, R.trade_date >= start, R.trade_date <= end)
    if expiry is not None:
        query = query.where(R.expiry == expiry)
    out: List[StoredChain] = []
    for row in await session.scalars(query.order_by(R.slot_start, R.expiry)):
        out.append(StoredChain(row.underlying, row.expiry, _utc(row.slot_start), _utc(row.captured_at), row.spot,
                               decode(row.payload, row.codec)))
    return out


async def read_day(session: AsyncSession, tenant_id: int, underlying: str, day: date, *, expiry: Optional[date] = None,
                   reader_user_id: Optional[int] = None, max_chains: Optional[int] = None) -> List[StoredChain]:
    return await read_range(session, tenant_id, underlying, day, day, expiry=expiry, reader_user_id=reader_user_id, max_chains=max_chains)


async def coverage(session: AsyncSession, tenant_id: int, *, underlying: Optional[str] = None, reader_user_id: Optional[int] = None,
                   limit_days: int = 366) -> List[Dict[str, Any]]:
    """Per underlying and day (newest first): chains stored, expiries, first / last slot - no market data."""
    query = (select(R.underlying, R.trade_date, func.count(), func.count(func.distinct(R.expiry)), func.min(R.slot_start), func.max(R.slot_start))
             .where(_visible(tenant_id, reader_user_id)).group_by(R.underlying, R.trade_date)
             .order_by(R.trade_date.desc(), R.underlying).limit(limit_days * 10))
    if underlying is not None:
        query = query.where(R.underlying == underlying)
    return [{"underlying": u, "day": d.isoformat(), "chains": int(n), "expiries": int(e),
             "first_slot": _utc(lo).astimezone(IST).isoformat(), "last_slot": _utc(hi).astimezone(IST).isoformat()}
            for u, d, n, e, lo, hi in await session.execute(query)]


# ---------------------------------------------------------------- one member's own reads (B1)

def _holder_only(tenant_id: Optional[int], user_id: int):
    """Only the rows read through `user_id`'s own broker session (source `broker:<name>:u<id>`) - never the tenant's
    other rows, never another member's (B1: with the own-broker data tier the analyses use the user's own reads only)."""
    tenant = _require_tenant(tenant_id)
    return (R.tenant_id == tenant) & R.source.like(f"{HOLDER_PREFIX}%:u{int(user_id)}")


async def holder_day(session: AsyncSession, tenant_id: int, user_id: int, underlying: str, day: date) -> List[StoredChain]:
    """`user_id`'s own stored chains of `underlying` on the IST trading day `day`, oldest first (then by expiry)."""
    out: List[StoredChain] = []
    query = select(R).where(_holder_only(tenant_id, user_id), R.underlying == underlying, R.trade_date == day)
    for row in await session.scalars(query.order_by(R.slot_start, R.expiry)):
        out.append(StoredChain(row.underlying, row.expiry, _utc(row.slot_start), _utc(row.captured_at), row.spot,
                               decode(row.payload, row.codec)))
    return out


async def holder_underlyings(session: AsyncSession, tenant_id: int, user_id: int, day: date) -> List[str]:
    """The underlyings `user_id` has own stored chains of on `day` (names only)."""
    query = select(R.underlying).where(_holder_only(tenant_id, user_id), R.trade_date == day).group_by(R.underlying).order_by(R.underlying)
    return [str(u) for u in await session.scalars(query)]


# ---------------------------------------------------------------- the admin status (counts only)

def expected_slots(day: date, now: datetime, minutes: int, exchange: str = "NSE") -> List[datetime]:
    """The slots of `day`'s regular session that have fully passed by `now` (UTC starts) - what a complete recording
    holds. The session's own clock (NSE / BSE share it); holidays are the caller's to exclude."""
    spec = EXCHANGE_SESSIONS[session_family(exchange)]
    open_ = datetime.combine(day, spec.open, tzinfo=IST)
    close = datetime.combine(day, spec.close, tzinfo=IST)
    step = timedelta(minutes=max(1, minutes))
    first = slot_start(open_, minutes).astimezone(IST)
    out: List[datetime] = []
    slot = first
    now_utc = _utc(now)
    while slot < close and slot + step <= now_utc:
        if slot + step > open_:
            out.append(slot.astimezone(timezone.utc))
        slot += step
    return out


def gap_ranges(expected: Sequence[datetime], recorded: Iterable[datetime], minutes: int) -> List[Dict[str, str]]:
    """The expected slots with no recording, merged into IST ranges [{"from", "to"}] (to = the last missing slot's end)."""
    have = {_utc(t) for t in recorded}
    step = timedelta(minutes=max(1, minutes))
    out: List[Dict[str, str]] = []
    start: Optional[datetime] = None
    last: Optional[datetime] = None
    for slot in expected:
        if slot in have:
            if start is not None and last is not None:
                out.append({"from": start.astimezone(IST).isoformat(), "to": (last + step).astimezone(IST).isoformat()})
            start = last = None
            continue
        if start is None:
            start = slot
        last = slot
    if start is not None and last is not None:
        out.append({"from": start.astimezone(IST).isoformat(), "to": (last + step).astimezone(IST).isoformat()})
    return out


async def day_slots(session: AsyncSession, tenant_id: int, day: date) -> Dict[str, Dict[str, Any]]:
    """Per underlying on `day`: chains stored, distinct slots, compressed / raw bytes, last capture (counts only)."""
    tenant = _require_tenant(tenant_id)
    out: Dict[str, Dict[str, Any]] = {}
    rows = await session.execute(select(R.underlying, R.slot_start, R.captured_at, R.raw_bytes, func.length(R.payload))
                                 .where(R.tenant_id == tenant, R.trade_date == day))
    for underlying, slot, captured, raw, stored in rows:
        entry = out.setdefault(underlying, {"chains": 0, "slots": set(), "raw_bytes": 0, "stored_bytes": 0, "last_captured_at": None})
        entry["chains"] += 1
        entry["slots"].add(_utc(slot))
        entry["raw_bytes"] += int(raw or 0)
        entry["stored_bytes"] += int(stored or 0)
        captured = _utc(captured)
        if entry["last_captured_at"] is None or captured > entry["last_captured_at"]:
            entry["last_captured_at"] = captured
    return out


async def totals(session: AsyncSession, tenant_id: int) -> Dict[str, Any]:
    tenant = _require_tenant(tenant_id)
    n, days, first, last, stored = (await session.execute(select(func.count(), func.count(func.distinct(R.trade_date)), func.min(R.trade_date),
                                                                 func.max(R.captured_at), func.sum(func.length(R.payload)))
                                                          .where(R.tenant_id == tenant))).one()
    return {"chains": int(n or 0), "days": int(days or 0), "first_day": first.isoformat() if first else None,
            "last_captured_at": _utc(last).isoformat() if last else None, "stored_bytes": int(stored or 0)}


__all__ = ["CODEC", "FIELD_NAMES", "encode", "decode", "write", "WriteResult", "exists", "slot_start", "StoredChain", "read_range",
           "read_day", "count", "coverage", "holder_day", "holder_underlyings", "expected_slots", "gap_ranges", "day_slots", "totals"]
