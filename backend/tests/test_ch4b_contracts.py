"""N4 CH4b: daily contract and spread charts from the shared lake (GET /api/charts/contracts/series) and contract search
from the instrument master (GET /api/charts/contracts/search).

* keys: the lake's own F&O keys (the bhavcopy's strike text), parsed and rebuilt identically;
* series: one leg's OHLC / volume / OI; a spread's close = sum of qty x close on the days every leg has a bar;
* as of `at`: later bars and a correction ingested after `at` change nothing; a broker stream row is never read;
* computed IV: Black-76 from the leg's close and the same-expiry future's close - equal to the Greeks library's own
  solver; null with no future; labelled computed;
* search: one row per contract across brokers, filters, nearest strike first;
* flag `chart_contracts` off by default (503); refusals (bad leg, a contract twice, naive time).
Synthetic rows only - no network, no broker. Nothing here places an order.
"""
import asyncio
import math
from datetime import date, datetime, time, timedelta, timezone

import pytest
from sqlalchemy import delete

from app.charts import contracts as C
from app.db.models import InstrumentRecord, MdCandleRecord
from app.market_lake.bhavcopy import _strike
from app.platform import controls
from tests.test_auth_api import _session_factory, client  # noqa: F401 - fixtures
from tests.test_phase_l_ai import _owner

SYM = "CH4BIDX"
EXP = date(2026, 10, 27)
CE = f"NFO:{SYM}:CE:{EXP.isoformat()}:25000"
PE = f"NFO:{SYM}:PE:{EXP.isoformat()}:24500"
FUT = f"NFO:{SYM}:FUT:{EXP.isoformat()}"
DAYS = [date(2026, 9, 1) + timedelta(days=i) for i in range(10)]


def _run(coro):
    return asyncio.run(coro)


def _end(d):
    return datetime.combine(d, time(10, 0), tzinfo=timezone.utc)


def _bar(key, d, close, *, source="nse_bhav:fo", version=1, oi=1000, ingested=None):
    return MdCandleRecord(instrument_key=key, timeframe="day", ts=_end(d), source=source, version=version, open=close - 1, high=close + 2,
                          low=close - 2, close=close, volume=500, oi=oi, ingested_at=ingested or _end(d) + timedelta(hours=6))


def _put(rows):
    async def go():
        async with _session_factory() as session:
            session.add_all(rows)
            await session.commit()
    _run(go())


def _flag(on):
    async def go():
        async with _session_factory() as session:
            current = await controls._get(session, controls.KEY_FEATURE_FLAGS)
            stored = dict(current.get("flags") or {})
            stored[C.FLAG] = {"on": on, "tenants": []}
            await controls._set(session, controls.KEY_FEATURE_FLAGS, {"flags": stored}, None)
            await session.commit()
    _run(go())


@pytest.fixture(scope="module", autouse=True)
def _lake():
    rows = []
    for i, d in enumerate(DAYS):
        rows += [_bar(CE, d, 200 + i * 5, oi=1000 + i), _bar(FUT, d, 25100 + i * 10)]
        if i % 2 == 0:
            rows.append(_bar(PE, d, 150 - i, oi=2000 + i))                       # the put trades every other day only
    rows.append(_bar(CE, DAYS[3], 9999, source="stream:upstox", version=7))       # a broker stream row: never read
    rows.append(_bar(CE, DAYS[4], 1.0, version=2, ingested=_end(DAYS[8])))        # a correction known only from day 8
    rows += [InstrumentRecord(broker=b, exchange="NFO", instrument_key=f"{b}|{k}", tradingsymbol=f"{SYM}26OCT{k}", underlying=SYM,
                              instrument_type=t, expiry=EXP, strike=s, lot_size=75)
             for b in ("upstox", "kite") for k, t, s in (("25000CE", "CE", 25000.0), ("24500PE", "PE", 24500.0), ("FUT", "FUT", None),
                                                         ("25500CE", "CE", 25500.0))]
    _put(rows)
    yield

    async def clean():
        async with _session_factory() as session:
            await session.execute(delete(MdCandleRecord).where(MdCandleRecord.instrument_key.like(f"NFO:{SYM}:%")))
            await session.execute(delete(InstrumentRecord).where(InstrumentRecord.underlying == SYM))
            await session.commit()
    _run(clean())


@pytest.fixture
def headers():
    _flag(True)
    yield _owner(f"ch4b-{datetime.now().timestamp()}@example.com")[0]
    _flag(False)


def _series(headers, legs, **params):
    r = client.get("/api/charts/contracts/series", params={"leg": legs, "start": _end(DAYS[0] - timedelta(days=2)).isoformat(),
                                                           "at": _end(DAYS[-1]).isoformat(), **params}, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def test_keys_are_the_lakes_own():
    for s in (25000.0, 112.5, 1050.25):
        assert C.strike_text(s) == _strike(s)
    k = C.parse_key("nfo:ch4bidx:ce:2026-10-27:25000.0")
    assert k["key"] == CE and k["strike"] == 25000.0 and k["kind"] == "CE"
    assert C.parse_key(FUT)["strike"] is None and C.contract_key(SYM, "FUT", EXP) == FUT
    for bad in ("NSE:RELIANCE", f"NFO:{SYM}:FUT:{EXP}:100", f"NFO:{SYM}:CE:{EXP}", "NFO:X:XX:2026-10-27:1"):
        with pytest.raises(ValueError):
            C.parse_key(bad)
    assert C.parse_leg(f"-2*{CE}") == (-2, C.parse_key(CE))
    with pytest.raises(ValueError):
        C.parse_leg(f"0*{CE}")


def test_one_leg_is_its_own_daily_ohlc_and_oi_from_shared_sources_only(headers):
    body = _series(headers, [CE], at=_end(DAYS[6]).isoformat())
    assert body["schema"] == "contract/1" and body["days"] == [d.isoformat() for d in DAYS[:7]]
    assert body["ohlc"]["c"] == [200 + i * 5 for i in range(7)]                     # not the stream row, not the late correction
    assert body["legs"][0]["oi"] == [1000 + i for i in range(7)] and body["legs"][0]["sources"] == ["nse_bhav"]
    assert body["times"][0] == int(datetime.combine(DAYS[0], time(3, 45), tzinfo=timezone.utc).timestamp())
    later = _series(headers, [CE])                                                     # as of day 9: the correction counts now
    assert later["ohlc"]["c"][4] == 1.0 and later["ohlc"]["c"][:4] == body["ohlc"]["c"][:4]


def test_a_spread_is_the_sum_of_its_legs_on_common_days(headers):
    body = _series(headers, [CE, f"-2*{PE}"], at=_end(DAYS[3]).isoformat())
    assert body["days"] == [DAYS[0].isoformat(), DAYS[2].isoformat()]                 # the put has no bar on odd days
    assert body["spread"] == [200 - 2 * 150, 210 - 2 * 148] and "ohlc" not in body
    assert [lg["qty"] for lg in body["legs"]] == [1, -2] and body["legs"][1]["oi"] == [2000, 2002]


def test_computed_iv_is_black76_and_labelled(headers):
    from app.core.config import RISK_FREE_RATE
    from app.option_chain.greeks import implied_volatility, time_to_expiry_years
    from app.option_chain.models import OptionType
    body = _series(headers, [CE], iv="true", at=_end(DAYS[2]).isoformat())
    assert "Computed IV" in body["iv_note"] and "Not a quoted IV" in body["iv_note"]
    t = time_to_expiry_years(EXP, _end(DAYS[0]))
    want = implied_volatility(200, 25100 * math.exp(-RISK_FREE_RATE * t), 25000, t, RISK_FREE_RATE, OptionType.CALL)
    assert body["legs"][0]["iv"][0] == pytest.approx(want, abs=1e-4) and 0.01 < body["legs"][0]["iv"][0] < 1.0
    assert C.computed_iv(200, None, 25000, "CE", EXP, DAYS[0], RISK_FREE_RATE) is None             # no future that day
    assert C.computed_iv(200, 25100, 25000, "CE", EXP, EXP + timedelta(days=1), RISK_FREE_RATE) is None   # expired
    assert C.computed_iv(200, 25100, 25000, "CE", EXP, EXP, RISK_FREE_RATE) is None                       # expiry day itself


def test_black76_solves_a_deep_itm_put_below_k_minus_f():
    """The put floor is e^(-rT)(K-F), not K-S: a deep ITM put priced under K-F used to read as an arbitrage (None)."""
    from app.option_chain.greeks import implied_volatility
    from app.option_chain.models import OptionType
    f, k, t, r = 20000.0, 25000.0, 0.25, 0.07
    price = C.black76_price(f, k, t, r, 0.15, call=False)
    assert price == pytest.approx(4913.9, abs=0.5) and price < k - f
    assert implied_volatility(price, f, k, t, r, OptionType.PUT) is None                 # the old Black-Scholes-bounds path
    assert C.black76_iv(price, f, k, t, r, call=False) == pytest.approx(0.15, abs=1e-4)
    call = C.black76_price(f, 18000.0, t, r, 0.22, call=True)
    assert C.black76_iv(call, f, 18000.0, t, r, call=True) == pytest.approx(0.22, abs=1e-4)
    disc = math.exp(-r * t)
    assert C.black76_iv(disc * (k - f) - 1, f, k, t, r, call=False) is None              # under the Black-76 floor
    assert C.black76_iv(disc * k + 1, f, k, t, r, call=False) is None                    # over e^(-rT)K
    assert C.black76_iv(disc * f + 1, f, 18000.0, t, r, call=True) is None               # over e^(-rT)F
    day = EXP - timedelta(days=91)
    assert C.computed_iv(round(price, 2), f, k, "PE", EXP, day, r) is not None


def test_the_daily_winner_is_per_ist_trade_date_by_source_rank_then_version():
    key = f"NFO:{SYM}:FUT:2026-12-29"
    d = date(2026, 9, 3)
    late_vendor = _bar(key, d, 777.0, source="vendor:x")                    # a lower-ranked source stamped later the same day
    late_vendor.ts = datetime.combine(d, time(18, 30), tzinfo=timezone.utc)  # midnight IST: still the day's bar
    _put([_bar(key, d, 500.0), _bar(key, d, 505.0, version=2, ingested=_end(d)), late_vendor])

    async def go():
        async with _session_factory() as session:
            return await C.daily_bars(session, key, _end(d - timedelta(days=1)), _end(d + timedelta(days=2)))
    got = _run(go())
    assert list(got) == [d] and got[d]["c"] == 505.0 and got[d]["source"] == "nse_bhav"
    assert C.trade_date(_end(d)) == d and C.trade_date(datetime.combine(d, time(18, 30), tzinfo=timezone.utc)) == d


def test_search_lists_each_contract_once_nearest_first(headers):
    r = client.get("/api/charts/contracts/search", params={"underlying": SYM.lower(), "kind": "CE", "near": 25400}, headers=headers)
    body = r.json()
    assert r.status_code == 200 and [c["key"] for c in body["contracts"]] == [f"NFO:{SYM}:CE:{EXP}:25500", CE]
    assert body["contracts"][0]["lot_size"] == 75 and body["total"] == 2
    every = client.get("/api/charts/contracts/search", params={"underlying": SYM, "expiry": EXP.isoformat()}, headers=headers).json()
    assert sorted(c["key"] for c in every["contracts"]) == sorted([CE, PE, FUT, f"NFO:{SYM}:CE:{EXP}:25500"])
    assert every["total"] == 4 and every["exchange"] == "NFO"


def test_search_orders_and_caps_in_the_query_and_never_keys_another_exchange(headers):
    """Many strikes across brokers: the first page is the right one (ordered before the cap), `total` counts distinct
    contracts, and a BFO / MCX contract of the same underlying name never becomes an NFO key."""
    sym, exp2 = "CH4BMANY", date(2026, 11, 24)
    rows = [InstrumentRecord(broker=b, exchange="NFO", instrument_key=f"{b}|{sym}{e}{k}", tradingsymbol=f"{sym}{e:%y%b}{k}CE".upper(),
                             underlying=sym, instrument_type="CE", expiry=e, strike=float(k), lot_size=50)
            for b in ("upstox", "kite") for e in (exp2, EXP) for k in range(20000, 30001, 100)]
    rows += [InstrumentRecord(broker="upstox", exchange=x, instrument_key=f"upstox|{x}{sym}", tradingsymbol=f"{x}{sym}", underlying=sym,
                              instrument_type="CE", expiry=EXP, strike=25050.0, lot_size=20) for x in ("BFO", "MCX")]
    _put(rows)
    try:
        url = "/api/charts/contracts/search"
        body = client.get(url, params={"underlying": sym, "kind": "CE", "near": 25000, "limit": 3}, headers=headers).json()
        assert [c["strike"] for c in body["contracts"]] == [25000.0, 24900.0, 25100.0]
        assert all(c["expiry"] == EXP.isoformat() for c in body["contracts"])           # the nearest expiry first
        assert body["total"] == 2 * 101                                                  # distinct contracts, not broker rows
        windowed = client.get(url, params={"underlying": sym, "kind": "CE", "near": 22000, "limit": 1}, headers=headers).json()
        assert windowed["total"] == 2 * len(range(20000, 27501, 100))                    # strikes within +-25% of `near`
        assert all(c["strike"] != 25050.0 for c in client.get(url, params={"underlying": sym, "near": 25050, "limit": 100},
                                                               headers=headers).json()["contracts"])
        plain = client.get(url, params={"underlying": sym, "limit": 2}, headers=headers).json()
        assert [c["strike"] for c in plain["contracts"]] == [20000.0, 20100.0] and plain["total"] == 2 * 101
    finally:
        async def clean():
            async with _session_factory() as session:
                await session.execute(delete(InstrumentRecord).where(InstrumentRecord.underlying == sym))
                await session.commit()
        _run(clean())


def test_series_is_rate_limited_per_user(headers):
    import time as _time

    from app.core import rate_limit
    uid = client.get("/api/auth/me", headers=headers).json()["id"]
    rate_limit._WINDOWS[("chart_contracts_series", f"u{uid}")].extend([_time.monotonic()] * C.SERIES_RATE_PER_MIN)
    try:
        assert client.get("/api/charts/contracts/series", params={"leg": CE}, headers=headers).status_code == 429
    finally:
        rate_limit.reset("chart_contracts_series")


def test_refusals_and_the_flag(headers):
    url = "/api/charts/contracts/series"
    assert client.get(url, params={"leg": "NSE:RELIANCE"}, headers=headers).status_code == 422
    assert client.get(url, params={"leg": [CE, f"-1*{CE}"]}, headers=headers).status_code == 422
    assert client.get(url, params={"leg": [CE] * (C.MAX_LEGS + 1)}, headers=headers).status_code == 422
    assert client.get(url, params={"leg": CE, "at": "2026-09-05T10:00:00"}, headers=headers).status_code == 422
    empty = _series(headers, [f"NFO:{SYM}:CE:{EXP}:99999"])
    assert empty["days"] == [] and empty["reason"]
    _flag(False)
    assert client.get(url, params={"leg": CE}, headers=headers).status_code == 503
    assert client.get("/api/charts/contracts/search", params={"underlying": SYM}, headers=headers).status_code == 503
    assert C.FLAG in controls.DEFAULT_OFF_FLAGS and "places no orders" in controls.FEATURE_FLAGS[C.FLAG]
