"""M86 D2: a user without a broker sees the last end-of-day close (app/market_data/eod_routes.py).

Shared by design and never a broker's row: only the licensed NSE daily files (`nse_bhav:*`) are read - a newer Daily
row a broker session wrote, an intraday row and the tenant-private store are never served. Fake lake data only.
"""
import asyncio
from datetime import date, datetime, timedelta

from sqlalchemy import delete

from app.db.models import MdCandleRecord, MdTenantCandleRecord
from app.market_lake import ingest
from app.market_lake.bhavcopy_backfill import bar_end
from tests.test_auth_api import _session_factory, client
from tests.test_deployments_api import _auth, _me

SYM = "EODTEST"
KEY = f"NSE:{SYM}"


def _run(coro):
    return asyncio.run(coro)


def _weekdays(n: int, last: date) -> list[date]:
    out, d = [], last
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d -= timedelta(days=1)
    return sorted(out)


def _write(key: str, rows: list[tuple[datetime, float]], source: str, timeframe: str = "day", tenant_id: int | None = None) -> None:
    async def go():
        async with _session_factory() as session:
            bars = [ingest.LakeBar(key, timeframe, ts, c - 1, c + 2, c - 3, c, 1000) for ts, c in rows]
            await ingest.write_candles_bulk(session, bars, source, tenant_id=tenant_id)
            await session.commit()
    _run(go())


def _clear(*keys: str) -> None:
    async def go():
        async with _session_factory() as session:
            for model in (MdCandleRecord, MdTenantCandleRecord):
                await session.execute(delete(model).where(model.instrument_key.in_(keys)))
            await session.commit()
    _run(go())


DAYS = _weekdays(30, date(2026, 11, 6))


def _daily(key: str, base: float = 100.0) -> None:
    _write(key, [(bar_end(d), base + i) for i, d in enumerate(DAYS)], "nse_bhav:cm" if key.startswith("NSE:") else "nse_bhav:index")


def test_closes_last_two_stored_closes_with_their_date():
    _clear(KEY)
    _daily(KEY)
    headers = _auth("m86-closes@example.com")
    res = client.get("/api/market-data/eod/closes", params={"symbols": f"{SYM}, NOPE_M86"}, headers=headers)
    assert res.status_code == 200
    body = res.json()
    got, missing = body["items"]
    assert got["symbol"] == SYM and got["missing"] is False
    assert got["close"] == 129.0 and got["prev_close"] == 128.0 and got["change"] == 1.0
    assert got["date"] == "2026-11-06" and body["as_of"] == "2026-11-06" and body["source"] == "eod"
    assert missing == {"symbol": "NOPE_M86", "exchange": "NSE", "close": None, "prev_close": None, "change": None,
                       "change_pct": None, "date": None, "missing": True}
    _clear(KEY)


def test_never_a_broker_row_an_intraday_row_or_a_private_row():
    """A newer Daily row a broker wrote, an intraday bar and the owner's private store are all left out."""
    _clear(KEY)
    _daily(KEY)
    later = bar_end(date(2026, 11, 9))
    _write(KEY, [(later, 999.0)], "broker:upstox")
    _write(KEY, [(later, 888.0)], "broker:upstox", timeframe="1m")
    me = _me(_auth("m86-private@example.com"))
    _write(KEY, [(later, 777.0)], "broker:upstox", tenant_id=me["tenant_id"])
    headers = _auth("m86-reader@example.com")
    got = client.get("/api/market-data/eod/closes", params={"symbols": SYM}, headers=headers).json()["items"][0]
    assert got["close"] == 129.0 and got["date"] == "2026-11-06"
    bars = client.get("/api/market-data/eod/bars", params={"symbol": SYM, "timeframe": "day", "lookback_days": 3650}, headers=headers).json()
    assert all(c["close"] < 200 for c in bars["candles"]) and bars["last_date"] == "2026-11-06"
    _clear(KEY)


def test_bars_daily_and_weekly_never_intraday():
    _clear(KEY)
    _daily(KEY)
    headers = _auth("m86-bars@example.com")
    day = client.get("/api/market-data/eod/bars", params={"symbol": SYM, "timeframe": "day", "lookback_days": 3650}, headers=headers).json()
    assert len(day["candles"]) == 30 and day["candles"][-1]["close"] == 129.0 and day["timeframe"] == "day"
    week = client.get("/api/market-data/eod/bars", params={"symbol": SYM, "timeframe": "week", "lookback_days": 3650}, headers=headers).json()
    assert week["timeframe"] == "week" and 6 <= len(week["candles"]) <= 7 and week["candles"][-1]["close"] == 129.0
    assert client.get("/api/market-data/eod/bars", params={"symbol": SYM, "timeframe": "5m"}, headers=headers).status_code == 422
    _clear(KEY)


def test_closes_limits_and_auth():
    headers = _auth("m86-limits@example.com")
    many = ",".join(f"S{i}" for i in range(61))
    assert client.get("/api/market-data/eod/closes", params={"symbols": many}, headers=headers).status_code == 422
    assert client.get("/api/market-data/eod/closes", params={"symbols": " , "}, headers=headers).status_code == 422
    assert client.get("/api/market-data/eod/closes", params={"symbols": SYM}).status_code == 401
