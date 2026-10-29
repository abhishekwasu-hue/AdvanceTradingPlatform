"""C1 proof tooling (owner, 24 Oct): what the broker really answers for one chart, as counts only.

`GET /api/admin/market-data/probe-candles` (SUPER_ADMIN with a two-factor-checked session, read-only) and the owner's
`deploy/atp.sh probe-candles "<symbol>" <timeframe>` (which runs `python -m app.market_data.probe` in the backend
container as the platform owner's account - the first SUPER_ADMIN_EMAILS user - through his OWN stored broker session)
make the two broker calls a chart makes (completed history + today's intraday) directly, with no shared copy, and report:
the window asked for, the bars each call returned, the first / last bar time, the first / last close and the broker's
empty / error reason. Nothing is written, no token or credential is read out, no other price is printed.
"""
from __future__ import annotations

import argparse
import asyncio
import re
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, cast

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user, require_mfa_session, require_role
from app.core.models import OHLCVBar
from app.db.models import User
from app.db.session import get_session
from app.market_data.calendar import IST, family_holidays, last_session_close, session_family

# MO3: SUPER_ADMIN with a two-factor-verified session, like every admin router.
admin_router = APIRouter(prefix="/api/admin/market-data", tags=["admin"],
                         dependencies=[Depends(require_role()), Depends(require_mfa_session)])

TIMEFRAME_ALIASES = {"1m": "1min", "3m": "3min", "5m": "5min", "15m": "15min", "30m": "30min", "1h": "60min", "60m": "60min",
                     "d": "day", "1d": "day"}
PROBE_TIMEFRAMES = ("1min", "3min", "5min", "15min", "30min", "60min", "day")
_LONG_RUN = re.compile(r"[A-Za-z0-9_\-+/=.]{24,}")       # anything token-like in a broker's message


def normalise_timeframe(raw: str) -> str:
    tf = (raw or "").strip()
    tf = TIMEFRAME_ALIASES.get(tf.lower(), tf.lower())
    if tf not in PROBE_TIMEFRAMES:
        raise ValueError(f"timeframe must be one of {', '.join(PROBE_TIMEFRAMES)} (or 1m 5m 15m 30m 1H D)")
    return tf


def guess_exchange(symbol: str) -> str:
    """An option or future contract trades on NFO; anything else on NSE (the owner may name the exchange)."""
    s = symbol.strip().upper()
    return "NFO" if s.endswith(("CE", "PE", "FUT")) and any(ch.isdigit() for ch in s) else "NSE"


def scrub(text: str, limit: int = 160) -> str:
    """A broker's message, shortened, with anything token-like removed."""
    return _LONG_RUN.sub("[...]", " ".join(str(text).split()))[:limit]


def _iso(ts: datetime) -> str:
    ts = ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(IST).isoformat(timespec="minutes")


def summarise(bars: Optional[List[OHLCVBar]], error: Optional[str]) -> Dict[str, Any]:
    """One call's answer as counts: bars, first / last bar time (IST), first / last close, and why it was empty."""
    if error is not None:
        return {"bars": 0, "first": None, "last": None, "first_close": None, "last_close": None, "reason": error}
    bars = sorted(bars or [], key=lambda b: b.timestamp)
    if not bars:
        return {"bars": 0, "first": None, "last": None, "first_close": None, "last_close": None,
                "reason": "the broker answered with no candles"}
    return {"bars": len(bars), "first": _iso(bars[0].timestamp), "last": _iso(bars[-1].timestamp),
            "first_close": float(bars[0].close), "last_close": float(bars[-1].close), "reason": None}


async def _call(coro: Any) -> tuple:
    try:
        return list(await coro), None
    except Exception as exc:  # noqa: BLE001 - the probe reports the broker's error, it never raises it
        return None, scrub(f"{type(exc).__name__}: {exc}")


async def probe_candles(service: Any, symbol: str, exchange: str, timeframe: str, now: Optional[datetime] = None) -> Dict[str, Any]:
    """The chart's two broker calls for `symbol`, made directly (no shared copy), as counts. `service` is a
    MarketDataService (its `holidays` set by the caller); the base interval is the chart's own (one minute, or day)."""
    from app.market_data.candles_routes import _shape
    now_utc = now or datetime.now(timezone.utc)
    base = "day" if timeframe == "day" else "1min"
    start, end = service.history_window(exchange, base, now_utc)
    history, h_err = await _call(service.broker.get_historical_data(symbol, exchange, base, start, end))
    out: Dict[str, Any] = {
        "symbol": symbol, "exchange": exchange, "timeframe": timeframe, "base_interval": base,
        "window": {"from": _iso(start), "to": _iso(end),
                   "last_session_close": (lambda c: _iso(c) if c else None)(last_session_close(now_utc, service.holidays, exchange)),
                   "asked_at": _iso(now_utc)},
        "history": summarise(history, h_err),
    }
    intraday: Optional[List[OHLCVBar]] = []
    if base == "day":
        out["intraday"] = {"bars": 0, "first": None, "last": None, "first_close": None, "last_close": None,
                           "reason": "not called for daily bars (the chart adds today's bar from one-minute bars)"}
    else:
        intraday, i_err = await _call(service.broker.get_intraday_candles(symbol, exchange, base))
        out["intraday"] = summarise(intraday, i_err)
    merged = sorted({b.timestamp: b for b in (history or []) + (intraday or [])}.values(), key=lambda b: b.timestamp)
    # DH2 / DH6: what the chart adds from the reader's own lake when the broker's history lags (0 when it does not).
    if getattr(service, "lake_fill", None) is not None:
        filled = await service._fill_last_session(symbol, exchange, base, list(merged), now_utc)
        added = [b for b in filled if b.timestamp not in {m.timestamp for m in merged}]
        out["own_lake"] = summarise(added, None if added else "nothing needed or nothing stored")
        merged = filled
    out["chart_bars"] = len(_shape(merged, timeframe)) if merged else 0
    return out


async def run_candles_probe(session: AsyncSession, user: User, symbol: str, exchange: str, timeframe: str) -> Dict[str, Any]:
    """The probe through the caller's own broker session (the chart route's pick) and its API budget."""
    from app.brokers.base import BrokerInterface
    from app.brokers.token_lifecycle import build_adapter
    from app.market_data import api_budget, lake_fill
    from app.market_data.candles_routes import _pick_record
    from app.market_data.service import MarketDataService
    record = await _pick_record(session, user.tenant_id, None, "primary")
    budgeted = api_budget.BudgetedAdapter(build_adapter(record), record.id, record.broker_name)
    service = MarketDataService(cast(BrokerInterface, budgeted), lookback_days=5)
    service.holidays = await family_holidays(session, session_family(exchange))
    service.lake_fill = lake_fill.for_reader(session, user)
    result = await probe_candles(service, symbol, exchange, timeframe)
    result["broker"] = record.broker_name
    return result


@admin_router.get("/probe-candles")
async def probe_candles_route(symbol: str = Query(min_length=1, max_length=60), timeframe: str = Query("30min", max_length=10),
                              exchange: Optional[str] = Query(None, max_length=10),
                              user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    """Read-only: the broker's own answer for one chart, as counts (C1 proof)."""
    try:
        tf = normalise_timeframe(timeframe)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    sym = symbol.strip().upper()
    return await run_candles_probe(session, user, sym, (exchange or guess_exchange(sym)).upper(), tf)


def format_candles(result: Dict[str, Any]) -> List[str]:
    """The probe as a few short lines for the owner's terminal."""
    w = result["window"]
    lines = [f"probe-candles {result['symbol']} ({result['exchange']}) {result['timeframe']} via {result.get('broker', '?')}",
             f"  asked at {w['asked_at']}; last session close {w['last_session_close'] or '-'}",
             f"  history window {w['from']} -> {w['to']} ({result['base_interval']} bars)"]
    for part in ("history", "intraday", "own_lake"):
        if part not in result:
            continue
        p = result[part]
        if p["bars"]:
            lines.append(f"  {part}: {p['bars']} bars, first {p['first']} (close {p['first_close']}), last {p['last']} (close {p['last_close']})")
        else:
            lines.append(f"  {part}: 0 bars - {p['reason']}")
    lines.append(f"  chart: {result['chart_bars']} {result['timeframe']} bars")
    return lines


async def _owner(session: AsyncSession) -> User:
    from sqlalchemy import select

    from app.core.config import SUPER_ADMIN_EMAILS
    from app.core.enums import UserRole
    query = select(User).where(User.role == UserRole.SUPER_ADMIN.value)
    if SUPER_ADMIN_EMAILS:
        query = query.where(User.email.in_(sorted(SUPER_ADMIN_EMAILS)))
    user = (await session.scalars(query.order_by(User.id).limit(1))).first()
    if user is None:
        raise SystemExit("no platform owner account (SUPER_ADMIN) found")
    return user


async def _main_async(args: argparse.Namespace) -> int:
    from app.db.session import _session_factory
    from app.secrets_store.envelope import ensure_tenant_key
    tf = normalise_timeframe(args.timeframe)
    symbol = args.symbol.strip().upper()
    exchange = (args.exchange or guess_exchange(symbol)).upper()
    async with _session_factory() as session:
        user = await _owner(session)
        await ensure_tenant_key(session, user.tenant_id)  # the web route loads it at sign-in; the CLI must load it here
        try:
            result = await run_candles_probe(session, user, symbol, exchange, tf)
        except HTTPException as exc:
            print(f"probe-candles: {exc.detail}")
            return 3
    print("\n".join(format_candles(result)))
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="probe", description="C1: the broker's answer for one chart, as counts")
    sub = parser.add_subparsers(dest="what", required=True)
    c = sub.add_parser("candles")
    c.add_argument("symbol")
    c.add_argument("timeframe")
    c.add_argument("exchange", nargs="?", default="")
    args = parser.parse_args(argv)
    try:
        return asyncio.run(_main_async(args))
    except ValueError as exc:
        print(f"probe-candles: {exc}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
