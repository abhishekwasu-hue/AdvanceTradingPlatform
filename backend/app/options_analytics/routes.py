"""N7 I: options analytics over HTTP (flag `options_analytics`, off by default).

`GET /api/options/analytics?underlying=NIFTY&at=<ISO time>` - ATM IV and the term structure, skew, expected move, PCR,
max pain per expiry and IV rank / percentile, as of `at` (default now), from the chain recorder's captures and the
caller's organisation's own uploads. BT1b: with the bring-your-own-broker data tier on, the recorder's rows (captured
through other organisations' broker sessions) are not used - only the organisation's own uploads. Information only.

No look-ahead: a quote counts at `at` only when it was captured AND stored by then (`chain_recorder.known_by`), so an
upload of older captures made after `at` never changes the answer at `at`.

Bounds: one call reads at most MAX_STAMPS distinct capture times and MAX_ROWS quotes (the newest first; the answer says
when it was cut), in IN lists of at most IN_CHUNK keys, and the route is rate-limited per user.
"""
from __future__ import annotations

from bisect import bisect_left, bisect_right
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.backtest.options import OptionChainSnapshotRow
from app.core.rate_limit import user_rate_limit
from app.db.models import User
from app.db.session import get_session
from app.market_data.calendar import IST
from app.options_analytics import analytics as A

FLAG = "options_analytics"
MAX_LOOKBACK_DAYS = 400
MAX_STAMPS = 50_000         # distinct capture times read for one answer (the recorder writes ~75 a session)
MAX_ROWS = 200_000          # quotes read for one answer
IN_CHUNK = 5_000            # keys per IN list
DISCLAIMER = "Options analytics on recorded quotes - information, not a recommendation; nothing here places an order."

router = APIRouter(tags=["options-analytics"])
analytics_rate_limit = user_rate_limit("options_analytics", limit=30, window_seconds=60)


def _utc(ts: datetime) -> datetime:
    return ts.astimezone(timezone.utc) if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def needed_stamps(stamps: List[datetime], at: datetime, start: datetime) -> List[datetime]:
    """Of the capture times (UTC, any order), the ones an answer at `at` reads: those within MAX_AGE before `at` (the
    snapshot) and those within MAX_AGE before each IST session's last capture at or after `start` (IV history).
    Newest first."""
    asc = sorted(set(stamps))
    last: Dict[date, datetime] = {}
    for ts in asc:
        if ts >= start:
            last[ts.astimezone(IST).date()] = ts
    keep: Set[datetime] = set()
    for end in [at, *last.values()]:
        keep.update(asc[bisect_left(asc, end - A.MAX_AGE): bisect_right(asc, end)])
    return sorted(keep, reverse=True)


async def _rows(session: AsyncSession, underlying: str, at: datetime, lookback_days: int, tenant_id: int,
                own_only: bool) -> Tuple[List[OptionChainSnapshotRow], List[str]]:
    from app.backtest import chain_recorder as R
    from app.db.models import OptionChainSnapshotRecord as Rec
    who = (Rec.source == R.upload_source(tenant_id)) if own_only else R.visible_to(tenant_id)
    base = and_(Rec.underlying == underlying, who, R.known_by(at), Rec.captured_at <= at)
    start = at - timedelta(days=lookback_days)
    limits: List[str] = []
    raw = list(await session.scalars(select(Rec.captured_at).where(base, Rec.captured_at >= start - A.MAX_AGE)
                                      .distinct().order_by(Rec.captured_at.desc()).limit(MAX_STAMPS + 1)))
    if len(raw) > MAX_STAMPS:
        raw = raw[:MAX_STAMPS]
        limits.append(f"Only the newest {MAX_STAMPS} capture times were read; IV history starts later than asked")
    by_utc: Dict[datetime, List[datetime]] = {}
    for value in raw:                                     # the stored value is matched exactly in the IN list
        by_utc.setdefault(_utc(value), []).append(value)
    wanted = [v for ts in needed_stamps(list(by_utc), at, start) for v in by_utc[ts]]
    rows: List[OptionChainSnapshotRow] = []
    for i in range(0, len(wanted), IN_CHUNK):
        q = (select(Rec).where(base, Rec.captured_at.in_(wanted[i:i + IN_CHUNK]))
             .order_by(Rec.captured_at.desc(), Rec.id).limit(MAX_ROWS - len(rows) + 1))
        got = list(await session.scalars(q))
        if len(rows) + len(got) > MAX_ROWS:
            got = got[:MAX_ROWS - len(rows)]
            limits.append(f"Only the newest {MAX_ROWS} quotes were read; IV history starts later than asked")
        rows += [OptionChainSnapshotRow(timestamp=_utc(r.captured_at), expiry=r.expiry, strike=r.strike, right=r.right, ltp=r.ltp, iv=r.iv,
                                        oi=r.oi, underlying_ltp=r.underlying_ltp) for r in got]
        if len(rows) >= MAX_ROWS:
            break
    return rows, limits


@router.get("/api/options/analytics", dependencies=[Depends(analytics_rate_limit)])
async def options_analytics(underlying: str = Query(min_length=1, max_length=30), at: Optional[datetime] = None,
                            lookback_days: int = Query(default=A.LOOKBACK_DAYS, ge=30, le=MAX_LOOKBACK_DAYS),
                            user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    from app.instruments.master import underlying_of
    from app.market_data import live_access
    from app.platform.controls import require_flag
    await require_flag(session, FLAG, user.tenant_id)
    now = datetime.now(timezone.utc)
    when = _utc(at) if at is not None else now
    if when > now:
        raise HTTPException(status_code=422, detail="at: a time in the future has no recorded quotes")
    name = underlying_of(underlying)
    own_only = await live_access.byob_on(session, user.tenant_id)
    rows, limits = await _rows(session, name, when, lookback_days, user.tenant_id, own_only)
    out = A.analytics(rows, when, lookback_days=lookback_days)
    return {"underlying": name, **out, "limits": limits,
            "data": ("this organisation's uploaded quotes only (bring-your-own-broker data tier)" if own_only
                     else "the platform's recorded quotes and this organisation's uploads"),
            "known_by": "quotes captured and stored by this time only (a later upload or correction never changes it)",
            "disclaimer": DISCLAIMER}
